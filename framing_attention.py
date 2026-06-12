"""
Tributary framing-attention bridge — which framings does each outlet express?
=============================================================================
Phase 2.6. The agenda layer counts WHICH stories outlets surface; the event
analyses map HOW a story can be framed. This module connects them: every
captured headline from the window is aligned to the framing whose
perspective it expresses — or "none". The output is the outlet × framing
attention matrix: which framings each outlet's front feed expressed, with
what prominence, and which framings it never touched.

Divergence here is measured against the event's OWN framings. No left/right
axis appears anywhere in the claim, and internationally unrated outlets are
first-class (Al Jazeera is unplaceable on AllSides but perfectly placeable
against an event's framings).

Two stages:
  1. RELEVANCE (free, local embeddings): a headline is a candidate when it
     reaches RELEVANCE_THRESHOLD against the event text or any framing
     key-claim.
  2. ALIGNMENT (interpretive — full P5 protocol): a batched Haiku judge
     assigns each candidate to the framing whose perspective it expresses,
     or "none" (purely factual / different angle). ~$0.001 per ~25
     headlines. Every label carries provenance and the judge's reason.

P1 holds throughout: the judge classifies PERSPECTIVE EXPRESSED, never the
truth, fairness, or quality of a framing or headline.

Gate 2.6b (before any matrix ships in a digest): --audit-sample writes a
blind file (model labels withheld, in a separate answer key) for human
labeling; require >=80% agreement and zero confidently-wrong-framing
assignments.

CLI:
    python framing_attention.py events/<id>.json --days 2        # print + save
    python framing_attention.py events/<id>.json --audit-sample 30
"""

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
OUT_DIR = _ROOT / "agenda" / "framing"

RELEVANCE_THRESHOLD = 0.45   # generous on purpose: stage 2 filters
BATCH_SIZE = 25
JUDGE_MODEL = "claude-haiku-4-5-20251001"
# fa-2: the Gate 2.6b first run FAILED at 57% — not because the judge was
# loose but because the construct was underspecified: the human labeled
# factual strike-report headlines into "Escalation Spiral" (the narrative
# the facts belong to) while fa-1's prompt sent them to "none" (no
# perspective taken). Both readings were right; the label was wrong. fa-2
# splits the relation: EXPRESSES (takes the framing's perspective) vs
# REPORTS (neutrally covers that framing's subject matter) vs none.
PROMPT_VERSION = "fa-2"

JUDGE_SYSTEM = """You classify how a news headline relates to an event's competing framings.
For each headline, pick the single closest framing (or "none") and a relation:
- "expresses": the headline's emphasis, word choice, or asserted significance takes that framing's perspective.
- "reports": the headline neutrally reports facts that are the subject matter of that framing's narrative, without adopting its perspective. Choose the framing whose narrative those facts most belong to.
- "none": the headline is unrelated to every framing's narrative (mere topic overlap with the event is not enough). For "none", set framing to "none".
When torn between two framings, pick the closer one and say confidence "low".
Never judge whether any framing or headline is true, fair, or good journalism. You describe perspective and subject matter, not quality.
Input JSON: {"event": "...", "framings": [{"id", "name", "key_claim"}], "headlines": [{"id", "text"}]}
Output ONLY JSON: {"assignments": [{"id": <headline id>, "framing": "<framing id or none>", "relation": "expresses|reports|none", "confidence": "high|medium|low", "why": "<= 10 words"}]}"""

MATRIX_CAVEAT = (
    "Counts are over headlines captured from each outlet's sampled front "
    "feeds — a prominence sample, not a census. A framing absent from an "
    "outlet's row means 'not expressed in its sampled front-feed headlines "
    "this window', never 'never published'. Framing labels are "
    "AI-interpretive (model + reason attached to every row) and ship in a "
    "digest only after the Gate 2.6b human audit. Labels describe the "
    "perspective a headline expresses, never its truth or quality."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_event(path: str) -> dict:
    ev = json.loads(Path(path).read_text(encoding="utf-8"))
    framings = [{"id": fr.get("framing_id", f"f{i}"),
                 "name": fr.get("name", ""),
                 "key_claim": fr.get("key_claim", "")}
                for i, fr in enumerate(ev.get("framings", []))]
    if not framings:
        raise SystemExit(f"{path} has no framings — run corpus.py on it first")
    return {"analysis_id": ev.get("analysis_id", Path(path).stem),
            "event": ev.get("event", ""), "framings": framings}


def candidate_headlines(days: int, event: dict,
                        relevance: float = RELEVANCE_THRESHOLD) -> list:
    """Stage 1 (free): window items whose title embedding reaches the
    relevance threshold against the event text or any framing key-claim.
    -> [(outlet_key, item, rel_sim)]"""
    import numpy as np
    from agenda import load_snapshots, aggregate_items
    from matcher import embed_texts

    snaps = load_snapshots(days)
    items_by_outlet, _ = aggregate_items(snaps, days)
    flat = [(k, it) for k, items in items_by_outlet.items() for it in items]
    if not flat:
        return []
    anchors = [event["event"]] + [f["key_claim"] for f in event["framings"]]
    avecs = np.asarray(embed_texts(anchors), dtype=np.float32)
    tvecs = np.asarray(embed_texts([it["title"] for _, it in flat]),
                       dtype=np.float32)
    sims = tvecs @ avecs.T
    best = sims.max(axis=1)
    return [(k, it, round(float(s), 3))
            for (k, it), s in zip(flat, best) if s >= relevance]


def align(candidates: list, event: dict,
          model: str = JUDGE_MODEL) -> list:
    """Stage 2 (interpretive): batched judge calls. -> [{id, framing,
    confidence, why}] aligned by candidate index. Requires
    ANTHROPIC_API_KEY; a failed batch leaves its headlines unlabeled
    (reported, never guessed)."""
    import anthropic
    from fingerprint import _parse_json_safe
    client = anthropic.Anthropic()
    out = [None] * len(candidates)
    n_calls = 0
    for start in range(0, len(candidates), BATCH_SIZE):
        chunk = candidates[start:start + BATCH_SIZE]
        payload = {
            "event": event["event"],
            "framings": event["framings"],
            "headlines": [{"id": start + j, "text": it["title"]}
                          for j, (_, it, _) in enumerate(chunk)],
        }
        try:
            resp = client.messages.create(
                model=model, max_tokens=2048,
                system=[{"type": "text", "text": JUDGE_SYSTEM}],
                messages=[{"role": "user", "content": json.dumps(payload)}])
            n_calls += 1
            text = "".join(b.text for b in resp.content
                           if getattr(b, "type", "") == "text")
            for a in (_parse_json_safe(text) or {}).get("assignments", []):
                i = a.get("id")
                if isinstance(i, int) and 0 <= i < len(out):
                    rel = str(a.get("relation", "")).lower()
                    if rel not in ("expresses", "reports", "none"):
                        rel = "expresses"   # fa-1 compatibility
                    out[i] = {"framing": str(a.get("framing", "none")),
                              "relation": rel,
                              "confidence": str(a.get("confidence", "")),
                              "why": str(a.get("why", ""))}
        except Exception as e:  # noqa: BLE001 — unlabeled is honest
            print(f"[framing] batch at {start} failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
    return out, n_calls


def build_matrix(event: dict, candidates: list, labels: list,
                 days: int, n_calls: int) -> dict:
    from agenda import load_roster, resolve_leans
    roster = load_roster()
    leans = resolve_leans(roster)
    fids = {f["id"] for f in event["framings"]}

    matrix = {}
    totals = {f["id"]: {"expressed": 0, "reported": 0, "outlets": set()}
              for f in event["framings"]}
    unlabeled = 0
    for (okey, it, rel), lab in zip(candidates, labels):
        o = roster["by_key"].get(okey, {})
        row = matrix.setdefault(okey, {
            "name": o.get("name", okey), "stream": o.get("stream", ""),
            "lean": leans.get(okey, ""), "country": o.get("country", ""),
            "relevant_items": 0, "none": 0, "unlabeled": 0, "framings": {},
            "none_headlines": [],
        })
        row["relevant_items"] += 1
        if lab is None:
            row["unlabeled"] += 1
            unlabeled += 1
            continue
        fid = lab["framing"] if lab["framing"] in fids else "none"
        relation = lab.get("relation", "expresses")
        if fid == "none" or relation == "none":
            row["none"] += 1
            # kept (capped) so the Gate 2.6b audit can test "none" labels —
            # a judge that over-assigns framings to neutral headlines is a
            # failure mode the audit must be able to see
            if len(row["none_headlines"]) < 20:
                row["none_headlines"].append({
                    "title": it["title"], "confidence": lab["confidence"],
                    "why": lab["why"]})
            continue
        cell = row["framings"].setdefault(fid, {
            "expressed": 0, "reported": 0,
            "best_position": it["best_position"], "headlines": []})
        cell[relation == "reports" and "reported" or "expressed"] += 1
        cell["best_position"] = min(cell["best_position"], it["best_position"])
        cell["headlines"].append({
            "title": it["title"], "link": it.get("link", ""),
            "position": it["best_position"], "relevance": rel,
            "relation": relation,
            "confidence": lab["confidence"], "why": lab["why"]})
        totals[fid]["expressed" if relation == "expresses" else "reported"] += 1
        totals[fid]["outlets"].add(okey)

    return {
        "is_framing_attention": True,
        "event_id": event["analysis_id"],
        "event": event["event"],
        "generated_at": _now_iso(),
        "window_days": days,
        "relevance_threshold": RELEVANCE_THRESHOLD,
        "n_candidate_headlines": len(candidates),
        "n_unlabeled": unlabeled,
        "framings": event["framings"],
        "framing_totals": {fid: {"expressed": t["expressed"],
                                 "reported": t["reported"],
                                 "n_outlets": len(t["outlets"])}
                           for fid, t in totals.items()},
        "matrix": dict(sorted(matrix.items(),
                              key=lambda kv: -kv[1]["relevant_items"])),
        "judge": {"model": JUDGE_MODEL, "prompt_version": PROMPT_VERSION,
                  "batches": n_calls,
                  "audit_status": "PENDING Gate 2.6b — not publishable"},
        "method_caveat": MATRIX_CAVEAT,
    }


def write_audit_sample(result: dict, n: int) -> tuple:
    """Gate 2.6b: a blind sample for human labeling. The judging file lists
    headline + framings with NO model label; the answer key lives separately
    so the comparison happens after labeling, not during."""
    rows = []
    for okey, row in result["matrix"].items():
        for fid, cell in row["framings"].items():
            for h in cell["headlines"]:
                rows.append({"headline": h["title"], "model_framing": fid,
                             "model_relation": h.get("relation", "expresses"),
                             "model_confidence": h["confidence"]})
        for h in row.get("none_headlines", []):
            rows.append({"headline": h["title"], "model_framing": "none",
                         "model_relation": "none",
                         "model_confidence": h["confidence"]})
    random.seed(26)                      # reproducible sample
    random.shuffle(rows)
    rows = rows[:n]
    blind = {
        "instructions": "For each headline: your_framing = the closest "
                        "framing (id, name, or F<n>), or 'none' if unrelated "
                        "to every framing's narrative. your_relation = "
                        "'expresses' (the headline takes that framing's "
                        "perspective via emphasis/word choice), 'reports' "
                        "(neutrally covers facts belonging to that framing's "
                        "narrative), or 'none'. Don't consult the answer key "
                        "until done.",
        "event": result["event"],
        "framings": result["framings"],
        "items": [{"i": i, "headline": r["headline"], "your_framing": "",
                   "your_relation": ""}
                  for i, r in enumerate(rows)],
    }
    key = {"items": [{"i": i, "model_framing": r["model_framing"],
                      "model_relation": r.get("model_relation", "expresses"),
                      "model_confidence": r["model_confidence"]}
                     for i, r in enumerate(rows)]}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Versioned by judge prompt so a re-audit never overwrites the labeled
    # files of a previous round (the fa-1 audit is part of the gate record).
    bpath = OUT_DIR / f"audit_{result['event_id']}_{PROMPT_VERSION}_blind.json"
    kpath = OUT_DIR / f"audit_{result['event_id']}_{PROMPT_VERSION}_key.json"
    bpath.write_text(json.dumps(blind, indent=1, ensure_ascii=False), encoding="utf-8")
    kpath.write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding="utf-8")
    return bpath, kpath


def score_audit(event_id: str) -> dict:
    """Gate 2.6b scoring: human blind labels vs the model's answer key.
    Tolerant label matching (framing id, framing name, F<n> index, or
    'none') so the labeler isn't penalized for not copying hex ids.
    Gate: >=80% agreement AND zero confidently-wrong-framing assignments
    (model said framing X with high confidence; human says a different
    framing or none)."""
    bpath = OUT_DIR / f"audit_{event_id}_{PROMPT_VERSION}_blind.json"
    kpath = OUT_DIR / f"audit_{event_id}_{PROMPT_VERSION}_key.json"
    if not bpath.exists():     # fa-1 round used unversioned names
        bpath = OUT_DIR / f"audit_{event_id}_blind.json"
        kpath = OUT_DIR / f"audit_{event_id}_key.json"
    print(f"[audit] scoring {bpath.name}", file=sys.stderr)
    blind = json.loads(bpath.read_text(encoding="utf-8"))
    key = {i["i"]: i for i in json.loads(
        kpath.read_text(encoding="utf-8"))["items"]}
    framings = blind.get("framings", [])
    by_id = {f["id"].lower(): f["id"] for f in framings}
    by_name = {f["name"].lower(): f["id"] for f in framings}
    by_index = {f"f{n+1}": f["id"] for n, f in enumerate(framings)}

    def norm(label):
        s = str(label or "").strip().lower()
        if not s:
            return None
        if s in ("none", "no", "neutral", "n/a", "-"):
            return "none"
        for table in (by_id, by_index, by_name):
            if s in table:
                return table[s]
        # prefix/substring tolerance on names ("escalation" -> full name)
        hits = [fid for nm, fid in by_name.items() if s in nm or nm in s]
        return hits[0] if len(hits) == 1 else f"UNRECOGNIZED:{s}"

    rows = []
    unrecognized = []
    for item in blind["items"]:
        h = norm(item.get("your_framing"))
        if h is None:
            continue
        if isinstance(h, str) and h.startswith("UNRECOGNIZED:"):
            unrecognized.append(item)
            continue
        k = key[item["i"]]
        rows.append({"headline": item["headline"], "human": h,
                     "model": k["model_framing"],
                     "human_rel": str(item.get("your_relation", "")).strip().lower(),
                     "model_rel": str(k.get("model_relation", "")).strip().lower(),
                     "confidence": k["model_confidence"]})
    n = len(rows)
    agree = [r for r in rows if r["human"] == r["model"]]
    over = [r for r in rows if r["model"] != "none" and r["human"] == "none"]
    under = [r for r in rows if r["model"] == "none" and r["human"] != "none"]
    cross = [r for r in rows if "none" not in (r["model"], r["human"])
             and r["human"] != r["model"]]
    conf_wrong = [r for r in rows if r["confidence"] == "high"
                  and r["human"] != r["model"]]
    names = {f["id"]: f["name"] for f in framings}

    print(f"Gate 2.6b — {n} labeled ({len(unrecognized)} unrecognized labels skipped)")
    rate = len(agree) / n if n else 0.0
    print(f"  framing agreement: {len(agree)}/{n} = {rate:.0%}   (gate: >=80%)")
    rel_rows = [r for r in rows if r["human_rel"] and r["model_rel"]]
    if rel_rows:
        rel_agree = sum(1 for r in rel_rows if r["human_rel"] == r["model_rel"])
        print(f"  relation agreement (expresses/reports/none): "
              f"{rel_agree}/{len(rel_rows)} = {rel_agree / len(rel_rows):.0%} (reported, not gated)")
    print(f"  disagreement anatomy: over-assigned (model framing, human none): "
          f"{len(over)} | under-assigned (model none, human framing): {len(under)} "
          f"| cross-framing: {len(cross)}")
    print(f"  confidently wrong (model 'high', human disagrees): {len(conf_wrong)}"
          f"   (gate: zero)")
    for r in conf_wrong:
        print(f"    model={names.get(r['model'], r['model'])[:34]} "
              f"human={names.get(r['human'], r['human'])[:34]}  {r['headline'][:60]}")
    if over:
        print("  over-assignments (the 'related but not aligned' cases):")
        for r in over[:8]:
            print(f"    model={names.get(r['model'], r['model'])[:40]:<40} {r['headline'][:65]}")
    verdict = "PASS" if (rate >= 0.8 and not conf_wrong) else "FAIL"
    print(f"  GATE 2.6b: {verdict}")
    return {"n": n, "agreement": rate, "over": len(over), "under": len(under),
            "cross": len(cross), "confidently_wrong": len(conf_wrong),
            "verdict": verdict}


def _print_matrix(r: dict):
    names = {f["id"]: f["name"] for f in r["framings"]}
    print(f"\nFraming attention — {r['event'][:100]}")
    print(f"  {r['n_candidate_headlines']} relevant headlines, "
          f"{r['n_unlabeled']} unlabeled; judge {r['judge']['model']} "
          f"({r['judge']['batches']} calls)")
    print("\n  framing totals (expressed + reported):")
    for fid, t in sorted(r["framing_totals"].items(),
                         key=lambda kv: -(kv[1]["expressed"] + kv[1]["reported"])):
        if t["expressed"] or t["reported"]:
            print(f"    {t['expressed']:>3} expressed / {t['reported']:>3} reported "
                  f"/ {t['n_outlets']:>2} outlets   {names.get(fid, fid)[:60]}")
    print("\n  per outlet:")
    for okey, row in r["matrix"].items():
        parts = [f"{names.get(fid, fid)[:30]} e{c['expressed']}/r{c['reported']} (pos {c['best_position']})"
                 for fid, c in sorted(row["framings"].items(),
                                      key=lambda kv: -(kv[1]["expressed"] + kv[1]["reported"]))]
        lean = row["lean"] or ("intl" if row["country"] != "US" else "unrated")
        print(f"    {row['name'][:26]:<26} [{lean:>9}] {row['relevant_items']:>2} items, "
              f"none={row['none']}: " + ("; ".join(parts) if parts else "—"))


def main():
    p = argparse.ArgumentParser(description="Outlet × framing attention matrix "
                                            "(agenda captures × event framings).")
    p.add_argument("event_json", help="Path to an EventAnalysis JSON with framings.")
    p.add_argument("--days", type=int, default=2,
                   help="capture window (default 2 — match the story's freshness)")
    p.add_argument("--no-save", action="store_true")
    p.add_argument("--audit-sample", type=int, default=0,
                   help="also write a blind Gate 2.6b sample of N labeled headlines")
    p.add_argument("--score-audit", action="store_true",
                   help="score the human-labeled blind file against the model key")
    args = p.parse_args()

    if args.score_audit:
        event_id = Path(args.event_json).stem.split("_")[0]
        score_audit(event_id)
        return

    event = load_event(args.event_json)
    cands = candidate_headlines(args.days, event)
    print(f"[framing] {len(cands)} candidate headlines above "
          f"{RELEVANCE_THRESHOLD} relevance", file=sys.stderr)
    if not cands:
        return
    labels, n_calls = align(cands, event)
    result = build_matrix(event, cands, labels, args.days, n_calls)
    _print_matrix(result)
    if not args.no_save:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        path = OUT_DIR / f"{event['analysis_id']}_{datetime.now(timezone.utc).date().isoformat()}.json"
        path.write_text(json.dumps(result, indent=1, ensure_ascii=False),
                        encoding="utf-8")
        print(f"\n[framing] saved -> {path}", file=sys.stderr)
    if args.audit_sample:
        b, k = write_audit_sample(result, args.audit_sample)
        print(f"[framing] Gate 2.6b blind sample -> {b}\n"
              f"[framing] answer key (open AFTER labeling) -> {k}", file=sys.stderr)


if __name__ == "__main__":
    main()
