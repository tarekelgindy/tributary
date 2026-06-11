"""
Calibration pairs for the agenda layer's similarity thresholds (Phase 2.6)
==========================================================================
The story-join (0.62) and adjacency (0.42) thresholds were set by eyeballing
one evening of clusters. This generates the labeled set that calibrates them
properly — the same blind protocol as Gate 1 (gen_gate1_pairs.py): you label
pairs WITHOUT seeing the similarities; the key file holds them for scoring.

Sampling is similarity-stratified across real captured headlines so every
decision band is represented:
    >= 0.75        near-duplicate candidates (should be same story)
    0.55 - 0.75    the story-join battleground (0.62 lives here)
    0.35 - 0.55    the adjacency battleground (0.42 lives here)
    <  0.35        presumed different (catches false negatives)

Label vocabulary (one per pair, in the blind file):
    same      — same story (one event, both headlines report it)
    related   — same broader matter, different story/angle (adjacent)
    different — unrelated

Usage:
    python gen_agenda_pairs.py --days 2 --n 40        # write blind + key
    python gen_agenda_pairs.py --score                # after labeling
Scoring reports, per threshold, how the bands agree with your labels and the
confident-error count (same-pairs below adjacency, different-pairs above
story-join — the errors that corrupt clustering and omission claims).
"""

import argparse
import json
import random
import sys
from itertools import combinations
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
BLIND = _ROOT / "agenda" / "calibration_pairs_blind.json"
KEY = _ROOT / "agenda" / "calibration_pairs_key.json"

BANDS = [(0.75, 1.01, "near-dup"), (0.55, 0.75, "join-band"),
         (0.35, 0.55, "adjacent-band"), (0.0, 0.35, "far")]
PER_BAND = {"near-dup": 8, "join-band": 14, "adjacent-band": 12, "far": 6}


def generate(days: int, n: int):
    import numpy as np
    from agenda import load_snapshots, aggregate_items
    from matcher import embed_texts

    snaps = load_snapshots(days)
    items_by_outlet, _ = aggregate_items(snaps, days)
    # cross-outlet pairs only — within-outlet pairs are dedup questions, not
    # story-identity questions
    flat = [(k, it["title"]) for k, items in items_by_outlet.items()
            for it in items]
    if len(flat) < 50:
        raise SystemExit("not enough captured headlines — run --capture first")
    random.seed(26)
    random.shuffle(flat)
    flat = flat[:600]                       # bound the pairwise work
    vecs = np.asarray(embed_texts([t for _, t in flat]), dtype=np.float32)

    by_band = {name: [] for *_, name in BANDS}
    for i, j in combinations(range(len(flat)), 2):
        if flat[i][0] == flat[j][0]:
            continue
        s = float(vecs[i] @ vecs[j])
        for lo, hi, name in BANDS:
            if lo <= s < hi:
                by_band[name].append((s, i, j))
                break

    picked = []
    for name, want in PER_BAND.items():
        pool = by_band[name]
        random.shuffle(pool)
        picked.extend(pool[:want])
    random.shuffle(picked)
    picked = picked[:n]

    blind = {"instructions": "Label each pair: same (one story, both report "
                             "it) / related (same broader matter, different "
                             "story or angle) / different. Do NOT open the "
                             "key file until done.",
             "pairs": [{"i": k, "a": flat[i][1], "b": flat[j][1], "label": ""}
                       for k, (s, i, j) in enumerate(picked)]}
    key = {"pairs": [{"i": k, "sim": round(s, 4),
                      "outlets": [flat[i][0], flat[j][0]]}
                     for k, (s, i, j) in enumerate(picked)]}
    BLIND.parent.mkdir(parents=True, exist_ok=True)
    BLIND.write_text(json.dumps(blind, indent=1, ensure_ascii=False), encoding="utf-8")
    KEY.write_text(json.dumps(key, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[pairs] {len(picked)} pairs -> {BLIND}\n[pairs] key (sims) -> {KEY}\n"
          f"[pairs] band counts: " +
          ", ".join(f"{nm}={min(len(by_band[nm]), PER_BAND[nm])}" for nm in PER_BAND),
          file=sys.stderr)


def score(story_threshold: float = 0.62, adjacent_threshold: float = 0.42):
    blind = json.loads(BLIND.read_text(encoding="utf-8"))
    key = {p["i"]: p["sim"] for p in
           json.loads(KEY.read_text(encoding="utf-8"))["pairs"]}
    labeled = [(p["label"].strip().lower(), key[p["i"]], p)
               for p in blind["pairs"] if p.get("label", "").strip()]
    if not labeled:
        raise SystemExit("no labels in the blind file yet")
    n = len(labeled)
    join_err = [(lab, s, p) for lab, s, p in labeled
                if s >= story_threshold and lab == "different"]
    adj_err = [(lab, s, p) for lab, s, p in labeled
               if s < adjacent_threshold and lab == "same"]
    same_below_join = sum(1 for lab, s, _ in labeled
                          if lab == "same" and s < story_threshold)
    print(f"{n} labeled pairs")
    print(f"story-join {story_threshold}: {len(join_err)} CONFIDENT ERRORS "
          f"(labeled different, sim above join) — must be 0 to trust clustering")
    for lab, s, p in join_err:
        print(f"    {s:.3f}  {p['a'][:60]} || {p['b'][:60]}")
    print(f"adjacency {adjacent_threshold}: {len(adj_err)} CONFIDENT ERRORS "
          f"(labeled same, sim below adjacency) — these would become false "
          f"omission claims")
    for lab, s, p in adj_err:
        print(f"    {s:.3f}  {p['a'][:60]} || {p['b'][:60]}")
    print(f"(context: {same_below_join} same-pairs sit below the join "
          f"threshold — under-merging, conservative rather than dangerous)")
    same_sims = sorted(s for lab, s, _ in labeled if lab == "same")
    diff_sims = sorted(s for lab, s, _ in labeled if lab == "different")
    if same_sims and diff_sims:
        print(f"same sims:      min {same_sims[0]:.3f} / median "
              f"{same_sims[len(same_sims)//2]:.3f} / max {same_sims[-1]:.3f}")
        print(f"different sims: min {diff_sims[0]:.3f} / median "
              f"{diff_sims[len(diff_sims)//2]:.3f} / max {diff_sims[-1]:.3f}")


def main():
    p = argparse.ArgumentParser(description="Blind calibration pairs for "
                                            "agenda thresholds (Gate-1 protocol).")
    p.add_argument("--days", type=int, default=2)
    p.add_argument("--n", type=int, default=40)
    p.add_argument("--score", action="store_true",
                   help="score the labeled blind file against the thresholds")
    p.add_argument("--story-threshold", type=float, default=0.62)
    p.add_argument("--adjacent-threshold", type=float, default=0.42)
    args = p.parse_args()
    if args.score:
        score(args.story_threshold, args.adjacent_threshold)
    else:
        generate(args.days, args.n)


if __name__ == "__main__":
    main()
