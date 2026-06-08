"""
Generate Tributary's local source-bias snapshot from AllSides ratings.
================================================================

The bias *ratings* are AllSides' work, not ours — Tributary only looks them
up and aggregates. This keeps the provenance honest: every lean we display is
attributable to AllSides (or, later, MBFC), never invented by us or a model.

AllSides Media Bias Ratings are licensed CC BY-NC 4.0 (free for research /
noncommercial use WITH attribution; commercial use needs an AllSides license).
See https://www.allsides.com/media-bias/ratings and the license at
https://www.allsides.com/tools-services/bias-ratings-license-api .

Source of this snapshot:
  --from-url  a CSV mirror of AllSides ratings (default: the favstats/AllSideR
              community mirror — REAL AllSides data but a 2019 snapshot; major
              outlets' leans are stable, but refresh from a current AllSides
              export for production).
  --from-file a local CSV/JSON you exported from AllSides (preferred for
              current data).

Emits data/allsides_ratings.json — a list of {outlet, rating, rating_num,
type, confidence, perc_agree, allsides_url} plus a header with source + license
+ as_of, consumed by bias_db.py.

Usage:
    python gen_bias_data.py                       # pull the community mirror
    python gen_bias_data.py --from-file my.csv    # use a fresh AllSides export
"""

import argparse
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

_DEFAULT_URL = ("https://raw.githubusercontent.com/favstats/AllSideR/"
                "master/data/allsides_data.csv")
_UA = "Tributary/0.1 (https://github.com/tarekelgindy/tributary) research"

# AllSides' five-point label -> our symmetric numeric lean (for skew math).
# left = most-left, right = most-right; center = 0. "allsides"/"mixed"/blank
# are AllSides' non-lean buckets and are treated as unrated for lean purposes.
_LABEL_NUM = {
    "left": -2, "left-center": -1, "center": 0,
    "right-center": 1, "right": 2,
}


def _fetch_csv(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def _rows_from_csv(text: str) -> list:
    return list(csv.DictReader(io.StringIO(text)))


def _norm_rating(raw: str) -> str:
    return (raw or "").strip().lower()


def build(rows: list, source_label: str, as_of: str) -> dict:
    outlets = []
    for r in rows:
        name = (r.get("news_source") or r.get("name") or "").strip()
        if not name:
            continue
        rating = _norm_rating(r.get("rating"))
        # keep every entity, but only lean-bucketed ones get a numeric lean
        lean_num = _LABEL_NUM.get(rating)
        try:
            perc = float(r.get("perc_agree")) if r.get("perc_agree") else None
        except (TypeError, ValueError):
            perc = None
        outlets.append({
            "outlet": name,
            "rating": rating,                       # AllSides label, verbatim
            "lean_num": lean_num,                    # -2..+2, or null if non-lean
            "type": (r.get("type") or "").strip(),
            "confidence": (r.get("confidence_level") or "").strip(),
            "perc_agree": perc,
            "allsides_url": (r.get("url") or "").strip(),
        })
    return {
        "_meta": {
            "source": "AllSides Media Bias Ratings",
            "source_detail": source_label,
            "license": "CC BY-NC 4.0 (attribution required; commercial use needs an AllSides license)",
            "attribution_url": "https://www.allsides.com/media-bias/ratings",
            "as_of": as_of,
            "note": ("Ratings are AllSides' — Tributary only aggregates them. "
                     "Refresh via gen_bias_data.py --from-file <current AllSides export>."),
            "count": len(outlets),
        },
        "outlets": outlets,
    }


def main():
    p = argparse.ArgumentParser(description="Build data/allsides_ratings.json from AllSides ratings.")
    p.add_argument("--from-url", default=_DEFAULT_URL,
                   help="CSV URL to pull (default: favstats/AllSideR community mirror, 2019).")
    p.add_argument("--from-file", default="",
                   help="Local CSV (or JSON) export from AllSides; overrides --from-url.")
    p.add_argument("--as-of", default="2019-10-18",
                   help="Date the ratings snapshot reflects (default: the community mirror's date).")
    p.add_argument("--out", default="data/allsides_ratings.json")
    args = p.parse_args()

    if args.from_file:
        text = Path(args.from_file).read_text(encoding="utf-8")
        if args.from_file.lower().endswith(".json"):
            print("[gen] JSON input: pass it through bias_db's loader instead.", file=sys.stderr)
            sys.exit(2)
        rows = _rows_from_csv(text)
        src = f"local file {args.from_file}"
    else:
        print(f"[gen] fetching AllSides ratings mirror: {args.from_url}", file=sys.stderr)
        rows = _rows_from_csv(_fetch_csv(args.from_url))
        src = args.from_url

    data = build(rows, source_label=src, as_of=args.as_of)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    rated = sum(1 for o in data["outlets"] if o["lean_num"] is not None)
    print(f"[gen] wrote {out} — {data['_meta']['count']} entities "
          f"({rated} with a left/center/right lean). Source: {src}", file=sys.stderr)


if __name__ == "__main__":
    main()
