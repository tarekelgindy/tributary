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

from fingerprint import EventAnalyzer, FingerprintGenerator, FingerprintStore, _log_progress
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
    analyzer = EventAnalyzer(max_searches=args.max_searches, models=models)
    fp_gen = FingerprintGenerator(max_searches=args.max_searches)
    fp_store = FingerprintStore(str(out_dir)) if args.claims else None

    # Which items still need doing?
    todo = [t for t in topics if args.force or t not in index]
    skipped = len(topics) - len(todo)
    if args.limit:
        todo = todo[:args.limit]

    per = (0.20 if args.framings_only else 0.50) if mode == "events" else (
        0.90 if args.full else 0.20)
    print(f"[corpus] {len(topics)} topics ({skipped} already done) → "
          f"processing {len(todo)} {mode} at ~${per:.2f} each "
          f"≈ ${per * len(todo):.2f} total. Ctrl-C to abort.", file=sys.stderr)

    t0 = time.monotonic()
    ok = fail = 0
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
    p.add_argument("--max-searches", type=int, default=6,
                   help="Cap web searches per call (default 6 for corpus economy).")
    p.add_argument("--event-model", default="",
                   help="Blanket model override for the event pipeline steps.")
    p.add_argument("--region", default="US", help='Scope region (default "US").')
    p.add_argument("--out-dir", default="",
                   help="Output dir (default: events/ for events, fingerprints/ for claims).")
    p.add_argument("--limit", type=int, default=0,
                   help="Process at most N new items this run (0 = all).")
    p.add_argument("--force", action="store_true",
                   help="Re-process items already in the corpus index.")
    args = p.parse_args()
    asyncio.run(run_corpus(args))


if __name__ == "__main__":
    main()
