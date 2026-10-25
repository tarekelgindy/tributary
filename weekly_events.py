"""
Weekly downstream loop — selection stage (2026-10-25, Batch C)
==============================================================
Picks the week's top broadly-carried stories from the agenda layer and
prints the ones not already covered by a published event, one per line
(tab-separated rank metadata after the label). The weekly-events workflow
feeds each line to the SAME gated event lane visitor requests use
(fingerprint.py --event: shape gate, policy gate, quality floor, cards) —
this script only chooses; it never generates.

    python weekly_events.py --sync --select --n 3

--sync pulls the agenda-data branch's snapshots into agenda/ first (the
capture job writes them there on CI; a local checkout may be stale).
Selection: stories clustered over the last 7 days, ranked by distinct
outlets carrying them; a story is skipped when a published event's title
already resembles its label (difflib >= 0.60) — the loop adds novel
events, never near-duplicates.
"""

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sync_snapshots():
    subprocess.run(["git", "fetch", "origin", "agenda-data"], cwd=ROOT, check=True)
    subprocess.run(["git", "checkout", "origin/agenda-data", "--",
                    "snapshots", "sitemaps"], cwd=ROOT, check=True)
    # the branch keeps them at the repo root; agenda.py reads agenda/
    import shutil
    for sub in ("snapshots", "sitemaps"):
        src, dst = ROOT / sub, ROOT / "agenda" / sub
        if src.exists():
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.glob("*.json"):
                shutil.copyfile(f, dst / f.name)
            shutil.rmtree(src)
    subprocess.run(["git", "reset", "-q", "HEAD", "snapshots", "sitemaps"],
                   cwd=ROOT, check=False)


def existing_event_titles() -> list:
    idx = ROOT / "gallery" / "index.json"
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("events") if isinstance(data, dict) else data
    return [str(r.get("event") or "") for r in (rows or []) if isinstance(r, dict)]


def already_covered(label: str, titles: list, cutoff: float = 0.60) -> bool:
    lab = label.lower()
    return any(difflib.SequenceMatcher(None, lab, t.lower()).ratio() >= cutoff
               for t in titles if t)


def select(days: int, n: int, min_outlets: int) -> list:
    from agenda import (aggregate_items, cluster_stories, describe_stories,
                        load_roster, load_snapshots, resolve_leans)
    snaps = load_snapshots(days)
    if not snaps:
        print("no agenda snapshots in window (run with --sync?)", file=sys.stderr)
        return []
    items, _ = aggregate_items(snaps, days)
    raw, _ = cluster_stories(items)
    stories = describe_stories(raw, resolve_leans(load_roster()), load_roster())
    titles = existing_event_titles()
    picked = []
    for s in stories:
        if s.get("n_outlets", 0) < min_outlets:
            continue
        label = (s.get("label") or "").strip()
        # the event gate needs a describable happening, not a topic word
        if len(label.split()) < 5:
            continue
        if already_covered(label, titles):
            continue
        picked.append((label, s["n_outlets"], s.get("n_items", 0)))
        if len(picked) >= n:
            break
    return picked


def main():
    ap = argparse.ArgumentParser(description="Select the week's top novel events.")
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--sync", action="store_true")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-outlets", type=int, default=6)
    args = ap.parse_args()
    if args.sync:
        sync_snapshots()
    if args.select:
        for label, n_out, n_items in select(args.days, args.n, args.min_outlets):
            print(f"{label}\t{n_out} outlets\t{n_items} items")


if __name__ == "__main__":
    main()
