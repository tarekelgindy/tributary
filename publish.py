"""
Publish analyzed events to the public gallery
=============================================
events/ is gitignored (generated artifacts); the public gallery is served from
the tracked gallery/ directory on GitHub Pages. This script is the one step
between "corpus built" and "corpus browsable by anyone":

    python corpus.py topics.txt --batch        # build (API)
    python publish.py                          # export to gallery/  (free)
    git add gallery/ && git commit && git push # live

It copies every event analysis that has framings into gallery/events/ and
writes gallery/index.json — {id, event, event_date, n_framings, n_actors,
skew} per entry — which index.html fetches to render the corpus list.
Re-runs are idempotent; entries are sorted newest-first by event_date.

P1 note: the per-event "skew" string passed through here is the coverage-lean
descriptor, which is guarded language by construction ("insufficient rated
coverage…", "possible blindspot") — no verdicts are added at publish time.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def publish(events_dir: Path, gallery_dir: Path, min_framings: int = 2) -> dict:
    out_events = gallery_dir / "events"
    out_events.mkdir(parents=True, exist_ok=True)
    entries, skipped = [], 0
    for p in sorted(events_dir.glob("*.json")):
        if p.name == "corpus_index.json":
            continue
        try:
            ev = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            skipped += 1
            continue
        framings = ev.get("framings") or []
        if not ev.get("is_event_analysis") or len(framings) < min_framings:
            skipped += 1
            continue
        aid = ev.get("analysis_id") or p.stem
        shutil.copyfile(p, out_events / f"{aid}.json")
        lean = ev.get("coverage_lean") or {}
        coal = ev.get("coalition") or {}
        entries.append({
            "id": aid,
            "event": (ev.get("event") or "")[:200],
            "event_date": ev.get("event_date") or "",
            "created_at": ev.get("created_at") or "",
            "n_framings": len(framings),
            "n_actors": coal.get("n_actors", 0),
            "skew": lean.get("skew", ""),
        })
    entries.sort(key=lambda e: (e["event_date"] or e["created_at"]), reverse=True)
    index = {"count": len(entries), "entries": entries}
    (gallery_dir / "index.json").write_text(
        json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    build_search_index(gallery_dir)
    return {"published": len(entries), "skipped": skipped}


def build_search_index(gallery_dir: Path) -> int:
    """gallery/search_index.json — the corpus the homepage question box
    matches against: every published event (title + framing names) and every
    example trace (canonical phrase + variants + published request-fulfilled
    traces in gallery/traces/). Lexical keys only; the client does simple
    token scoring, and misses fall through to the request-a-trace path."""
    items = []

    def keys_for_fp(fp):
        lex = fp.get("lexical") or {}
        return [lex.get("canonical_phrase") or ""] + (lex.get("phrase_variants") or [])

    for p in sorted((gallery_dir / "events").glob("*.json")):
        try:
            ev = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        framings = ev.get("framings") or []
        items.append({
            "kind": "event",
            "title": (ev.get("event") or "")[:200],
            "url": f"fingerprint_viewer.html?load=gallery/events/{p.name}",
            "keys": [f.get("name") or "" for f in framings]
                    + [f.get("key_claim") or "" for f in framings],
        })

    for folder, prefix in ((ROOT / "examples", "examples"),
                           (gallery_dir / "traces", "gallery/traces")):
        if not folder.exists():
            continue
        for p in sorted(folder.glob("*.json")):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if doc.get("fingerprint_id") and doc.get("lexical"):
                items.append({
                    "kind": "trace",
                    "title": (doc["lexical"].get("canonical_phrase") or p.stem)[:200],
                    "url": f"fingerprint_viewer.html?load={prefix}/{p.name}",
                    "keys": keys_for_fp(doc),
                })
            elif doc.get("is_event_analysis"):
                items.append({
                    "kind": "event",
                    "title": (doc.get("event") or "")[:200],
                    "url": f"fingerprint_viewer.html?load={prefix}/{p.name}",
                    "keys": [f.get("name") or "" for f in (doc.get("framings") or [])],
                })

    (gallery_dir / "search_index.json").write_text(
        json.dumps({"count": len(items), "items": items}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    return len(items)


def main():
    p = argparse.ArgumentParser(description="Export analyzed events to the public gallery/.")
    p.add_argument("--events-dir", default=str(ROOT / "events"))
    p.add_argument("--gallery-dir", default=str(ROOT / "gallery"))
    p.add_argument("--min-framings", type=int, default=2,
                   help="Publish only events with at least this many framings (default 2).")
    args = p.parse_args()
    rep = publish(Path(args.events_dir), Path(args.gallery_dir), args.min_framings)
    print(f"[publish] {rep['published']} events -> {args.gallery_dir} "
          f"({rep['skipped']} skipped: no framings / not event JSON). "
          f"Now: git add gallery/ && git commit && git push", file=sys.stderr)


if __name__ == "__main__":
    main()
