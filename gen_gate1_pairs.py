"""
Generate the Gate 1 calibration pairs for human labeling.
=========================================================
Gate 1 (ROADMAP Phase 1): 30 claim pairs spanning same-narrative / paraphrase
/ different; human judgment vs. matcher band; zero confident-match false
positives required before serve-from-cache becomes default.

This samples claim texts from the live corpus (framing key_claims across the
gallery events + fingerprint canonical phrases), embeds them locally, and
selects pairs STRATIFIED by similarity — high band (likely same/paraphrase),
middle band (the hard cases), low band (clearly different) — so the labels
exercise every decision boundary. Similarities are deliberately NOT written
into the output file, so the human labels stay blind to the matcher's opinion.

    python gen_gate1_pairs.py            # -> gate1_pairs.json (label each entry)
    python matcher.py --calibrate gate1_pairs.json   # after labeling
"""

import json
import random
from pathlib import Path

from matcher import embed_texts, _dot

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gate1_pairs.json"

N_HIGH, N_MID, N_LOW = 10, 12, 8          # 30 total; extra weight on the hard band
HIGH, LOW = 0.80, 0.45                     # band edges for SAMPLING (not the grid)


def collect_claims() -> list:
    texts, seen = [], set()

    def add(t, src):
        t = (t or "").strip()
        if 25 <= len(t) <= 220 and t.lower() not in seen:
            seen.add(t.lower())
            texts.append({"text": t, "src": src})

    for p in sorted((ROOT / "gallery" / "events").glob("*.json")):
        ev = json.loads(p.read_text(encoding="utf-8"))
        for fr in ev.get("framings", []):
            add(fr.get("key_claim"), p.stem)
    for p in sorted((ROOT / "fingerprints").glob("*.json")):
        if p.name in ("index.json", "vectors.json", "review_queue.json",
                      "generation_queue.json"):
            continue
        try:
            fp = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        add((fp.get("lexical") or {}).get("canonical_phrase"), p.stem)
    return texts


def main():
    rng = random.Random(42)               # reproducible sampling
    claims = collect_claims()
    print(f"collected {len(claims)} distinct claims; embedding...")
    vecs = embed_texts([c["text"] for c in claims])

    # All cross-source pairs, banded by similarity. Same-source (same event)
    # pairs are excluded for the high band — two framings of one event are
    # trivially related; the interesting "same narrative" cases are CROSS-event.
    high, mid, low = [], [], []
    n = len(claims)
    for i in range(n):
        for j in range(i + 1, n):
            s = _dot(vecs[i], vecs[j])
            pair = (s, i, j)
            if s >= HIGH and claims[i]["src"] != claims[j]["src"]:
                high.append(pair)
            elif LOW <= s < HIGH:
                mid.append(pair)
            elif 0.15 <= s < LOW:
                low.append(pair)

    def sample(pool, k):
        rng.shuffle(pool)
        out, used = [], set()
        for s, i, j in sorted(pool, key=lambda x: -x[0]):
            if i in used or j in used:    # each claim appears at most once
                continue
            used.update((i, j))
            out.append((s, i, j))
            if len(out) == k:
                break
        return out

    picked_high = sample(high, N_HIGH)
    picked_mid = sample(mid, N_MID)
    picked_low = sample(low, N_LOW)
    chosen = picked_high + picked_mid + picked_low
    rng.shuffle(chosen)                   # no band ordering hints for the labeler

    pairs = [{"id": k, "a": claims[i]["text"], "b": claims[j]["text"], "label": ""}
             for k, (s, i, j) in enumerate(chosen)]
    OUT.write_text(json.dumps({
        "_instructions": (
            "Label each pair: 'same' = the same narrative/claim (one fingerprint "
            "should serve both); 'paraphrase' = the same idea in clearly different "
            "wording; 'different' = distinct claims, even if about the same topic. "
            "Trust your judgment; don't overthink edge cases — note them in 'note' "
            "if you want. Then run: python matcher.py --calibrate gate1_pairs.json"),
        "pairs": pairs,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(pairs)} pairs -> {OUT.name} "
          f"({len(picked_high)} high / {len(picked_mid)} mid / {len(picked_low)} low, shuffled)")


if __name__ == "__main__":
    main()
