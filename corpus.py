"""
Tributary corpus builder
=========================
Batch-process a list of events (or claims) into the corpus, one per line.
Designed for the budget-constrained reality:

  - Incremental: each result is saved as it completes, so running out of
    credit (or a crash) never loses finished work.
  - Resumable: a corpus index dedups by input line — re-running skips
    what's already done (pass --force to redo).
  - Resilient: a failure on one item (429/529, empty response, etc.) is
    logged and the batch continues to the next.
  - Cheap by default: events use --framings-only + a low --max-searches so
    a ~100-event corpus lands around $20-25. Drop --framings-only for the
    full pipeline when you want depth.

This runs the analyses SEQUENTIALLY at the normal (already-discounted-by-
flags) price. The Anthropic Batch API path (another ~50% off, async) slots
in once batch_probe.py confirms web_search works in batch — see 2c.

Usage:
    # one event per line (blank lines and #comments ignored)
    python corpus.py events.txt
    python corpus.py events.txt --full              # full event pipeline (pricier)
    python corpus.py events.txt --max-searches 4    # cheaper
    python corpus.py claims.txt --claims            # fingerprint claims instead of events
    python corpus.py events.txt --limit 10          # only the first 10 new items
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from fingerprint import (EventAnalyzer, FingerprintGenerator, FingerprintStore,
                         score_contestedness, _log_progress)
from models import EventAnalysis, Scope


def _read_topics(path: str) -> list:
    lines = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            s = raw.strip()
            if s and not s.startswith("#"):
                lines.append(s)
    return lines


def _load_index(index_path: Path) -> dict:
    if index_path.exists():
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


async def run_corpus(args):
    topics = _read_topics(args.topics_file)
    if not topics:
        print(f"[no topics found in {args.topics_file}]", file=sys.stderr)
        return

    mode = "claims" if args.claims else "events"
    out_dir = Path(args.out_dir or ("fingerprints" if args.claims else "events"))
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "corpus_index.json"
    index = _load_index(index_path)

    scope = Scope(language="en", region=args.region)
    models = ({k: args.event_model for k in EventAnalyzer.DEFAULT_MODELS}
              if args.event_model else None)
    analyzer = EventAnalyzer(max_searches=args.max_searches, models=models,
                             lean_framings=args.lean_framings)
    fp_gen = FingerprintGenerator(max_searches=args.max_searches)
    fp_store = FingerprintStore(str(out_dir)) if args.claims else None

    # Recovery path: a previous --batch run crashed locally after submission.
    # The paid batch lives server-side (results retrievable for 29 days) and
    # the custom_id -> input mapping was persisted at submit time; reattach,
    # retrieve, assemble, save — no re-spend.
    if args.resume_batch:
        state_path = out_dir / "batch_state.json"
        if not state_path.exists():
            print(f"[corpus] no {state_path} found — nothing to resume.", file=sys.stderr)
            return
        state = json.loads(state_path.read_text(encoding="utf-8"))
        bid, items = state["batch_id"], state["items"]
        print(f"[corpus] resuming batch {bid} ({len(items)} items)...", file=sys.stderr)
        import anthropic as _anthropic
        client = _anthropic.Anthropic()
        batch = client.messages.batches.retrieve(bid)
        if batch.processing_status != "ended":
            print(f"[corpus] batch status is '{batch.processing_status}' — try again "
                  "later (it completes server-side within 24h).", file=sys.stderr)
            return
        results = {r.custom_id: r for r in client.messages.batches.results(bid)}
        ok = fail = done = 0
        for cid, item in items.items():
            if item["raw"] in index and not args.force:
                done += 1
                continue
            r = results.get(cid)
            text = ""
            if r is not None and getattr(r.result, "type", None) == "succeeded":
                from fingerprint import _response_text
                text = _response_text(r.result.message)
            framings, _ = analyzer.parse_framings_text(text) if text else ([], "")
            if not framings:
                fail += 1
                continue
            analysis = await analyzer.build_event_from_framings(
                item["event"], item["date"], framings,
                framings_only=args.framings_only, verify=not args.no_verify)
            (out_dir / f"{analysis.analysis_id}.json").write_text(
                analysis.to_json(), encoding="utf-8")
            index[item["raw"]] = analysis.analysis_id
            index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
            ok += 1
        print(f"[corpus] resume done — {ok} recovered, {done} already saved, "
              f"{fail} empty/failed. Corpus now has {len(index)} events.", file=sys.stderr)
        return

    # Which items still need doing?
    todo = [t for t in topics if args.force or t not in index]
    skipped = len(topics) - len(todo)

    # Contestedness pre-filter (events): skip neutral events that won't have
    # divergent framings — cheap chunked Haiku calls, gating the expensive analysis.
    if args.min_contestedness > 0 and mode == "events" and todo:
        _log_progress(f"Scoring contestedness of {len(todo)} events "
                      f"({(len(todo) + 39) // 40} chunked Haiku calls)...")
        try:
            scored = await score_contestedness(analyzer.client, todo)
        except RuntimeError as e:
            print(f"[corpus] ABORTED before any spend — {e}", file=sys.stderr)
            return
        kept, dropped = [], []
        for s in scored:
            (kept if s["score"] >= args.min_contestedness else dropped).append(s)
        for s in dropped:
            print(f"  [skip {s['score']:.0f}/10] {s['topic'][:60]} — {s['reason']}",
                  file=sys.stderr)
        print(f"[contestedness >= {args.min_contestedness}: kept {len(kept)} of "
              f"{len(todo)}, dropped {len(dropped)} neutral events]", file=sys.stderr)
        todo = [s["topic"] for s in kept]

    if args.limit:
        todo = todo[:args.limit]

    base = (0.20 if args.framings_only else 0.22) if mode == "events" else (
        0.90 if args.full else 0.20)
    per = base * 0.55 if (args.batch and mode == "events") else base
    print(f"[corpus] {len(topics)} topics ({skipped} already done) → "
          f"processing {len(todo)} {mode} at ~${per:.2f} each "
          f"≈ ${per * len(todo):.2f} total"
          f"{' (Batch API ~50% off)' if (args.batch and mode == 'events') else ''}. "
          f"Ctrl-C to abort.", file=sys.stderr)

    t0 = time.monotonic()
    ok = fail = 0

    # ---- Batch path (events): one Batch-API job for all framing searches ----
    if args.batch and mode == "events" and todo:
        print("[corpus] --batch: submitting all framing searches as one async "
              "Batch-API job (~50% off). This can take minutes to hours; "
              "finished events are saved as they assemble.", file=sys.stderr)
        try:
            analyses = await analyzer.analyze_events_batch(
                todo, scope=scope, framings_only=args.framings_only,
                verify=not args.no_verify,
                state_file=str(out_dir / "batch_state.json"))
        except Exception as e:  # noqa: BLE001
            print(f"[corpus] batch failed: {type(e).__name__}: {e}\n"
                  "  (If the batch was already submitted, recover it without "
                  "re-spending: python corpus.py <topics> --resume-batch. "
                  "Otherwise re-run without --batch.)", file=sys.stderr)
            return
        for topic, analysis in zip(todo, analyses):
            if not analysis.framings:
                fail += 1
                continue
            (out_dir / f"{analysis.analysis_id}.json").write_text(
                analysis.to_json(), encoding="utf-8")
            index[topic] = analysis.analysis_id
            ok += 1
        index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
        dt = time.monotonic() - t0
        print(f"\n[corpus] batch done in {dt/60:.1f} min — {ok} succeeded, "
              f"{fail} failed/empty. Corpus now has {len(index)} events. "
              f"Output in {out_dir}/", file=sys.stderr)
        return

    for i, topic in enumerate(todo, 1):
        _log_progress(f"[{i}/{len(todo)}] {topic[:70]}")
        try:
            if args.claims:
                fp = await fp_gen.generate_fingerprint(
                    topic, scope=scope,
                    skip_conceptual=not args.full,
                    skip_mutations=not args.full,
                    skip_evidence=not args.full,
                )
                fp_store.save(fp)
                index[topic] = fp.fingerprint_id
                ok += 1
            else:
                analysis = await analyzer.analyze_event(
                    topic, scope=scope, framings_only=args.framings_only,
                    verify=not args.no_verify,
                )
                if not analysis.framings:
                    _log_progress(f"[{i}/{len(todo)}] no framings — skipped "
                                  "(transient? re-runnable)")
                    fail += 1
                    continue
                path = out_dir / f"{analysis.analysis_id}.json"
                path.write_text(analysis.to_json(), encoding="utf-8")
                index[topic] = analysis.analysis_id
                ok += 1
        except KeyboardInterrupt:
            print("\n[corpus] aborted by user — finished items are saved.", file=sys.stderr)
            break
        except Exception as e:  # noqa: BLE001 — keep the batch going on any single failure
            _log_progress(f"[{i}/{len(todo)}] FAILED: {type(e).__name__}: {e}")
            fail += 1
        finally:
            # Persist the index after every item so a crash never loses progress.
            index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")

    dt = time.monotonic() - t0
    print(f"\n[corpus] done in {dt/60:.1f} min — {ok} succeeded, {fail} failed/skipped. "
          f"Corpus now has {len(index)} {mode}. Output in {out_dir}/", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description="Batch-build a Tributary corpus from a topic list.")
    p.add_argument("topics_file", help="Text file with one event (or claim) per line; "
                                       "blank lines and #comments ignored.")
    p.add_argument("--claims", action="store_true",
                   help="Fingerprint each line as a CLAIM (upstream) instead of analyzing "
                        "it as an EVENT (downstream, the default).")
    p.add_argument("--framings-only", action="store_true",
                   help="Events: stop after the framing search (~$0.20 vs ~$0.50 each). "
                        "Recommended for cheap corpus-building.")
    p.add_argument("--full", action="store_true",
                   help="Claims: run the full deep pipeline (conceptual+evidence+mutations). "
                        "Events: ignored (use without --framings-only for the full event pipeline).")
    p.add_argument("--batch", action="store_true",
                   help="Events: run all framing searches as one async Batch-API job "
                        "(~50%% off tokens). Takes minutes to hours but ~halves the cost. "
                        "Cheap Haiku steps still run live.")
    p.add_argument("--max-searches", type=int, default=6,
                   help="Cap web searches per call (default 6 for corpus economy).")
    p.add_argument("--no-verify", action="store_true",
                   help="Skip carrier-URL verification (events). Verification is free "
                        "(HTTP only) and flags dead/hallucinated carrier links.")
    p.add_argument("--min-contestedness", type=float, default=0,
                   help="Events: skip events scoring below this (0-10) on a cheap "
                        "contestedness pre-filter — so you only spend on events likely "
                        "to have divergent framings. Try 6. Default 0 = off.")
    p.add_argument("--lean-framings", action="store_true",
                   help="Events: fewer framings/carriers per event (smaller output, "
                        "cheaper).")
    p.add_argument("--event-model", default="",
                   help="Blanket model override for the event pipeline steps.")
    p.add_argument("--region", default="US", help='Scope region (default "US").')
    p.add_argument("--out-dir", default="",
                   help="Output dir (default: events/ for events, fingerprints/ for claims).")
    p.add_argument("--limit", type=int, default=0,
                   help="Process at most N new items this run (0 = all).")
    p.add_argument("--force", action="store_true",
                   help="Re-process items already in the corpus index.")
    p.add_argument("--resume-batch", action="store_true",
                   help="Recover a --batch run that crashed locally after "
                        "submission: reattach to the server-side batch recorded "
                        "in <out-dir>/batch_state.json, retrieve results, and "
                        "assemble/save events — no re-spend.")
    args = p.parse_args()
    asyncio.run(run_corpus(args))


if __name__ == "__main__":
    main()
