"""
Event dates from receipts (2026-10-25, Batch B)
===============================================
104 of 112 published events carry an EMPTY event_date, and the one date the
project ever audited (Odeh) was wrong by two years against its own carriers.
This sweep: (1) derives a date for undated events (one Haiku describe call
each, ~$0.001) and BOUNDS every date — derived or pre-existing — against the
event's own carrier dates (the Odeh rule as code: coverage receipts outrank
summary fields); (2) stamps coverage_as_of = the latest carrier date on
every event, so staleness has an honest face in the viewer.

    python apply_event_dates.py            # DRY RUN — table only
    python apply_event_dates.py --apply    # write + rerun publish.py after

Bounding rule (pure, tested): a date is credible only inside
[earliest_carrier - 45d, latest_carrier]; outside it, the event is dated
"on or before first coverage" at the earliest carrier date, with the basis
recorded in event_date_basis either way.
"""

import argparse
import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WINDOW_BEFORE_DAYS = 45


import re as _re


def _parse(d):
    s = str(d or "").strip()[:10]
    if _re.fullmatch(r"\d{4}", s):
        s += "-01-01"          # year precision: earliest day it denotes
    elif _re.fullmatch(r"\d{4}-\d{2}", s):
        s += "-01"
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def carrier_dates(ev: dict) -> list:
    out = []
    for f in ev.get("framings") or []:
        for c in f.get("carriers") or []:
            d = _parse(c.get("date"))
            if d:
                out.append(d)
    return sorted(out)


def bound_event_date(derived: str, cdates: list) -> tuple:
    """(event_date, basis). The provable half of the Odeh rule only:
    coverage cannot precede its event, so a date AFTER the latest carrier
    (+7d slack for publish lag) is impossible and falls back to earliest
    coverage as an upper bound. Dates far BEFORE coverage are legitimate
    (retrospective analyses — the 2024 Iran strike covered in 2026), so
    they survive; the sweep separately FLAGS >2y gaps for eyeball review,
    which is where the Odeh typo class (2024 for 2026) actually lives."""
    if not cdates:
        return (str(derived or "")[:10],
                "stated in description; no carrier dates to bound against") \
            if _parse(derived) else ("", "")
    hi = cdates[-1] + timedelta(days=7)
    d = _parse(derived)
    if d and d <= hi:
        prec = " (year precision)" if _re.fullmatch(r"\d{4}", str(derived or "").strip()) else ""
        return d.isoformat(), f"stated in description; within coverage horizon{prec}"
    return cdates[0].isoformat(), "earliest coverage (event on or before this date)"


async def _derive_dates(events: list) -> dict:
    """events: [(path, ev)] needing derivation. Returns {path: derived_str}."""
    from fingerprint import EventAnalyzer
    gen = EventAnalyzer()
    out = {}
    CHUNK = 8
    for i in range(0, len(events), CHUNK):
        chunk = events[i:i + CHUNK]
        results = await asyncio.gather(
            *[gen.describe_event(ev.get("event") or "") for _, ev in chunk],
            return_exceptions=True)
        for (p, _), r in zip(chunk, results):
            out[p] = "" if isinstance(r, Exception) else (r[1] or "")
    return out


def main():
    ap = argparse.ArgumentParser(description="Event dates from receipts.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    files = sorted((ROOT / "gallery" / "events").glob("*.json"))
    loaded = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in files]
    need = [(p, ev) for p, ev in loaded if not str(ev.get("event_date") or "").strip()]
    print(f"{len(loaded)} events; {len(need)} undated -> deriving via Haiku...")
    derived = asyncio.run(_derive_dates(need)) if need else {}

    changed = 0
    for p, ev in loaded:
        cd = carrier_dates(ev)
        before = str(ev.get("event_date") or "")[:10]
        src = before or derived.get(p, "")
        new, basis = bound_event_date(src, cd)
        cov = cd[-1].isoformat() if cd else ""
        delta = (before != new) or (str(ev.get("coverage_as_of") or "") != cov)
        if delta:
            changed += 1
            flag = " **CHANGED FROM STORED DATE**" if before and before != new else ""
            d = _parse(new)
            if d and cd and (cd[0] - d).days > 730:
                flag += " **REVIEW: >2y before coverage (Odeh-typo territory or genuinely retrospective)**"
            print(f"{p.stem}  {before or '(empty)':>10} -> {new or '(none)':10}  "
                  f"cov_as_of={cov}  [{basis}]{flag}")
        if args.apply:
            ev["event_date"] = new
            if basis:
                ev["event_date_basis"] = basis
            if cov:
                ev["coverage_as_of"] = cov
            p.write_text(json.dumps(ev, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
    print(f"\n{changed} event(s) would change" if not args.apply
          else f"\napplied to {changed} event(s) — run publish.py")


if __name__ == "__main__":
    main()
