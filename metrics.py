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
    python metrics.py --regions  # day-by-day breakdown by region; (NEW) marks
                                 # a region's first-ever appearance in the log
                                 # (combine with --days to window the table)

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


def region_label(r):
    c = r.get("country") or "(unknown)"
    return f'{r["region"]}, {c}' if r.get("region") else f"{c} (no region)"


# Paths a scanner fleet hits; deeper paths (corpus, viewer loads) and any
# search/request/contribution are treated as human signal.
SHALLOW_PATHS = {"", "/", "/index.html"}


def crawler_buckets(records):
    """Region-day buckets that look like scanner fleets: view events only,
    shallow paths only. Deliberately coarse — the log stores no UA, IP, or
    session (privacy posture), so bot classification can only be aggregate.
    A lone human who views just the homepage from a new region is the
    accepted cost; --all shows everything. (The new-domain burst of
    2026-10-06 — certificate-transparency scanners — is the motivating case.)"""
    buckets = {}
    for r in records:
        key = ((r.get("t") or "")[:10], region_label(r))
        b = buckets.setdefault(key, {"types": set(), "paths": set()})
        b["types"].add(r.get("type"))
        if r.get("type") == "view":
            b["paths"].add(r.get("path") or "/")
    return {k for k, b in buckets.items()
            if b["types"] == {"view"} and b["paths"] <= SHALLOW_PATHS}


def regions_table(all_records, windowed):
    """Day × region: how usage spreads. first-seen is computed over the FULL
    log (not the window) so (NEW) marks a region's first-ever appearance."""
    first_seen = {}
    for r in all_records:                       # chronologically sorted
        first_seen.setdefault(region_label(r), (r.get("t") or "")[:10])

    by_day = {}
    for r in windowed:
        by_day.setdefault((r.get("t") or "")[:10], []).append(r)

    print(f"=== Daily usage by region · {len(windowed)} events · "
          f"{len(by_day)} active days ===")
    print("(NEW) = that region's first-ever appearance in the log\n")
    for day in sorted(by_day):
        rows = by_day[day]
        tmix = " · ".join(f"{k}: {n}" for k, n in
                          Counter(r["type"] for r in rows).most_common())
        # Depth = the aggregate human-vs-bot signal we can honestly compute
        # without sessions: distinct paths viewed, plus any deeper activity.
        paths = {r.get("path") or "/" for r in rows if r["type"] == "view"}
        depth = f"paths: {len(paths)}"
        if any(r["type"] == "search" for r in rows):
            depth += " · searched"
        if any(r["type"] in ("request", "contribution") for r in rows):
            depth += " · requested"
        print(f"{day}  {len(rows):>4} events   ({tmix})   [{depth}]")
        regions = Counter(region_label(r) for r in rows)
        parts = [f"{lbl}: {n}" + (" (NEW)" if first_seen.get(lbl) == day else "")
                 for lbl, n in regions.most_common()]
        shown, extra = parts[:8], len(parts) - 8
        print(f"            {' · '.join(shown)}"
              f"{f' · +{extra} more regions' if extra > 0 else ''}")


def main():
    ap = argparse.ArgumentParser(description="Aggregate the site's usage log.")
    ap.add_argument("--days", type=int, default=0, help="only the last N days")
    ap.add_argument("--raw", action="store_true", help="dump raw JSON lines")
    ap.add_argument("--regions", action="store_true",
                    help="day-by-day usage per region, (NEW) on first appearance")
    ap.add_argument("--all", action="store_true",
                    help="include likely-crawler region-days (filtered by default)")
    args = ap.parse_args()

    all_records = fetch_all(load_secret())
    all_records.sort(key=lambda r: r.get("t") or "")
    records = all_records
    if args.days:
        floor = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
        records = [r for r in records if (r.get("t") or "") >= floor]

    if args.raw:
        for r in records:                      # raw stays raw — never filtered
            print(json.dumps(r, ensure_ascii=False))
        return

    if not args.all:
        bots = crawler_buckets(records)
        if bots:
            before = len(records)
            records = [r for r in records
                       if ((r.get("t") or "")[:10], region_label(r)) not in bots]
            print(f"[filtered {before - len(records)} likely-crawler events "
                  f"across {len(bots)} region-days (views-only, homepage-only); "
                  f"--all to include]\n", file=sys.stderr)

    if not records:
        print("No usage records yet (logging starts when the updated Worker "
              "is deployed).")
        return

    if args.regions:
        regions_table(all_records, records)
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
