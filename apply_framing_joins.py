"""
Framing <-> trace auto-join (2026-10-25, Batch B)
=================================================
Today 4 of 112 events link any framing to a trace; this sweep builds the
graph's cross-half edges. For every framing without a fingerprint_id:
embed its key_claim against the trace store (local model, free) and
judge-confirm only the top candidate above threshold (~$0.01 each, via the
matcher's retrieve-then-confirm). A confirmed match sets
framing.fingerprint_id — the viewer links it to the published trace; the
heavy legacy embedded-fingerprints shape is not used.

Prereqs: python hydrate_store.py (store mirrors gallery), then the matcher
backfills embeddings on first run.

    python apply_framing_joins.py            # DRY RUN
    python apply_framing_joins.py --apply    # write + rerun publish.py
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(description="Join event framings to published traces.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--hi", type=float, default=None, help="override matcher HI threshold")
    args = ap.parse_args()

    from matcher import Matcher
    m = Matcher()
    m.backfill()

    confirmed = judged = 0
    for p in sorted((ROOT / "gallery" / "events").glob("*.json")):
        ev = json.loads(p.read_text(encoding="utf-8"))
        touched = False
        for fr in ev.get("framings") or []:
            if fr.get("fingerprint_id"):
                continue
            claim = (fr.get("key_claim") or "").strip()
            if not claim:
                continue
            kw = {"hi": args.hi} if args.hi is not None else {}
            res = m.match_confirmed(claim, **kw)
            if res.confirm is not None:
                judged += 1
            # join only judge-confirmed SAME-claim matches; a negation match
            # is a real genealogical relation but must not silently equate a
            # framing with its denial (the polarity guard's whole point)
            if res.decision in ("serve_cached", "lexical_variant") and res.best:
                fid = res.best.fingerprint_id
                confirmed += 1
                touched = True
                print(f"{p.stem}  framing '{(fr.get('name') or '')[:34]}' -> trace {fid}"
                      f"  ({claim[:58]})")
                if args.apply:
                    fr["fingerprint_id"] = fid
                    fr.setdefault("provenance", {})["join"] = (
                        "matcher-confirmed join (judge relation=same), 2026-10-25 backfill")
        if args.apply and touched:
            p.write_text(json.dumps(ev, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
    print(f"\n{confirmed} framing(s) joined ({judged} judge calls)."
          + ("" if args.apply else " DRY RUN — nothing written."))


if __name__ == "__main__":
    main()
