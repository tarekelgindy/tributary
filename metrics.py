"""
Usage metrics digest (2026-09-18)
=================================
Pulls the Worker's privacy-first usage log (GET /metrics, maintainer-gated by
the callback secret) and prints an aggregate digest: activity by day, by
country, top search queries, top viewed pages, and every trace/contribution
request. The log carries CDN-derived country/region/city and NEVER IP
addresses — see METHODOLOGY "Usage measurement".

    python metrics.py            # digest of everything logged (180-day TTL)
    python metrics.py --days 7   # restrict to the last N days
    python metrics.py --raw      # dump raw records as JSON lines

Secret resolution: keys/cloudfare_callback_secret.txt, else CALLBACK_SECRET
in the environment (also in .env).
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
WORKER_URL = "https://tributary-requests.tarek-elgindy.workers.dev"


def load_secret():
    f = ROOT / "keys" / "cloudfare_callback_secret.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    env = os.environ.get("CALLBACK_SECRET", "").strip()
    if env:
        return env
    sys.exit("[metrics] no secret: expected keys/cloudfare_callback_secret.txt "
             "or CALLBACK_SECRET in the environment")


def fetch_all(secret):
    records, cursor = [], None
    with httpx.Client(headers={"X-Callback-Secret": secret}, timeout=30.0) as http:
        while True:
            params = {"cursor": cursor} if cursor else {}
            r = http.get(WORKER_URL + "/metrics", params=params)
            if r.status_code == 403:
                sys.exit("[metrics] 403 — the secret doesn't match the Worker's "
                         "CALLBACK_SECRET (was the Worker redeployed with the "
                         "metrics routes?)")
            r.raise_for_status()
            data = r.json()
            records += data.get("records") or []
            cursor = data.get("cursor")
            if not cursor:
                return records


def geo(r):
    bits = [r.get("city") or "", r.get("region") or "", r.get("country") or ""]
    return ", ".join(b for b in bits if b) or "(unknown)"


def main():
    ap = argparse.ArgumentParser(description="Aggregate the site's usage log.")
    ap.add_argument("--days", type=int, default=0, help="only the last N days")
    ap.add_argument("--raw", action="store_true", help="dump raw JSON lines")
    args = ap.parse_args()

    records = fetch_all(load_secret())
    if args.days:
        floor = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
        records = [r for r in records if (r.get("t") or "") >= floor]
    records.sort(key=lambda r: r.get("t") or "")

    if args.raw:
        for r in records:
            print(json.dumps(r, ensure_ascii=False))
        return

    if not records:
        print("No usage records yet (logging starts when the updated Worker "
              "is deployed).")
        return

    types = Counter(r["type"] for r in records)
    span = f'{records[0]["t"][:10]} → {records[-1]["t"][:10]}'
    print(f"=== Tributary usage · {len(records)} events · {span} ===\n")
    print("By type:     " + " · ".join(f"{k}: {n}" for k, n in types.most_common()))

    by_day = Counter((r.get("t") or "")[:10] for r in records)
    print("\nBy day:")
    for day in sorted(by_day)[-14:]:
        print(f"  {day}  {'#' * min(by_day[day], 60)} {by_day[day]}")

    by_country = Counter(r.get("country") or "(unknown)" for r in records)
    print("\nBy country:  " + " · ".join(f"{c}: {n}" for c, n in by_country.most_common(12)))

    def region_label(r):
        c = r.get("country") or "(unknown)"
        return f'{r["region"]}, {c}' if r.get("region") else f"{c} (no region)"
    by_region = Counter(region_label(r) for r in records)
    print("By region:   " + " · ".join(f"{lbl}: {n}" for lbl, n in by_region.most_common(12)))

    searches = [r for r in records if r["type"] == "search"]
    if searches:
        print(f"\nSearches ({len(searches)}):")
        for (q, kind), n in Counter((r.get("q") or "", r.get("kind") or "")
                                    for r in searches).most_common(20):
            print(f"  {n:>3}× [{kind:<5}] {q[:90]}")

    views = [r for r in records if r["type"] == "view"]
    if views:
        print(f"\nViews ({len(views)}):")
        for path, n in Counter(r.get("path") or "/" for r in views).most_common(15):
            print(f"  {n:>3}× {path[:100]}")

    reqs = [r for r in records if r["type"] == "request"]
    if reqs:
        print(f"\nGeneration requests ({len(reqs)}, every one listed):")
        for r in reqs:
            flag = "" if r.get("ok") else f"  [BLOCKED: {r.get('why', '?')}]"
            print(f"  {r['t'][:16]}  [{r.get('kind', '?'):<5}] {geo(r):<28} "
                  f"{(r.get('q') or '')[:70]}{flag}")

    contribs = [r for r in records if r["type"] == "contribution"]
    if contribs:
        print(f"\nContributions ({len(contribs)}):")
        for r in contribs:
            print(f"  {r['t'][:16]}  [{r.get('kind', '?'):<7}] {geo(r):<28} "
                  f"trace {r.get('fp', '?')}")


if __name__ == "__main__":
    main()
