"""
Tributary topic discovery — pull notable events from Wikipedia's Current Events
==============================================================================
Wikipedia's "Portal:Current events" is a free, no-auth, date-addressable,
editorially-curated daily list of notable events, organized by category and
written as neutral "what happened" descriptions. That's an ideal seed source
for the corpus: notable events reliably have multi-source coverage and
divergent framings (which is what the downstream pipeline maps).

This fetches a given day's events, extracts them into clean one-per-line
event seeds, and writes a topics file you can review/trim and then feed to
corpus.py. Free to run (Wikipedia HTTP only — no API key, no spend).

Usage:
    python discover.py                       # today
    python discover.py --date 2026-06-07     # a specific day
    python discover.py --date 2026-06-07 --out topics.txt --limit 40
    python discover.py --days-back 3         # 3 days ago

Then:
    python corpus.py topics_2026-06-07.txt --framings-only --max-searches 4
"""

import argparse
import re
import sys
from datetime import date, datetime, timedelta, timezone
from html import unescape
from html.parser import HTMLParser

import httpx

WIKI_API = "https://en.wikipedia.org/w/api.php"


class _CurrentEventsParser(HTMLParser):
    """Collects the DIRECT text of every <li> (a stack gives each list item
    only its own text, not its nested children's)."""
    def __init__(self):
        super().__init__()
        self._stack = []
        self.items = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self._stack.append([])

    def handle_endtag(self, tag):
        if tag == "li" and self._stack:
            self.items.append("".join(self._stack.pop()).strip())

    def handle_data(self, data):
        if self._stack:
            self._stack[-1].append(data)


def _portal_page_title(d: date) -> str:
    # Wikipedia format: "Portal:Current events/2026 June 7" (full month, no zero-pad)
    return f"Portal:Current events/{d.year} {d.strftime('%B')} {d.day}"


def _clean_event(text: str) -> str:
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # Strip trailing source citations like " (BBC) (Reuters)".
    for _ in range(4):
        new = re.sub(r"\s*\([^()]*\)\s*$", "", text).strip()
        if new == text:
            break
        text = new
    # Drop a trailing bare reference marker / stray punctuation.
    text = text.rstrip(" .;,–-").strip()
    return text


def fetch_events(d: date, timeout: float = 20.0) -> list:
    title = _portal_page_title(d)
    params = {
        "action": "parse", "page": title, "prop": "text",
        "format": "json", "formatversion": "2",
        "redirects": "1",
    }
    # Wikipedia enforces a descriptive User-Agent with contact info; a generic
    # one gets a 403. https://meta.wikimedia.org/wiki/User-Agent_policy
    headers = {
        "User-Agent": "Tributary/0.1 (https://github.com/tarekelgindy/tributary) httpx",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=timeout, headers=headers) as client:
        resp = client.get(WIKI_API, params=params)
        resp.raise_for_status()
        data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Wikipedia API error for '{title}': {data['error'].get('info')}")
    html = (data.get("parse") or {}).get("text", "")
    if not html:
        return []
    parser = _CurrentEventsParser()
    parser.feed(html)
    return parser.items


def discover(d: date, limit: int = 0, min_len: int = 30, max_len: int = 280) -> list:
    raw = fetch_events(d)
    seen, events = set(), []
    for item in raw:
        ev = _clean_event(item)
        if not (min_len <= len(ev) <= max_len):
            continue
        # Skip list-of-links fragments (mostly capitalized tokens / few words)
        if len(ev.split()) < 5:
            continue
        key = ev.lower()
        if key in seen:
            continue
        seen.add(key)
        events.append(ev)
    if limit:
        events = events[:limit]
    return events


def main():
    p = argparse.ArgumentParser(
        description="Pull notable events from Wikipedia Current Events into a topics file.")
    p.add_argument("--date", default="", help="YYYY-MM-DD (default: today, UTC).")
    p.add_argument("--days-back", type=int, default=0,
                   help="N days before today (ignored if --date is given).")
    p.add_argument("--out", default="",
                   help="Output file (default: topics_<date>.txt). Use '-' for stdout.")
    p.add_argument("--limit", type=int, default=0, help="Max events (0 = all).")
    args = p.parse_args()

    if args.date:
        try:
            d = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"[bad --date '{args.date}', expected YYYY-MM-DD]", file=sys.stderr)
            sys.exit(1)
    else:
        d = datetime.now(timezone.utc).date() - timedelta(days=args.days_back)

    print(f"Fetching Wikipedia Current Events for {d.isoformat()}...", file=sys.stderr)
    try:
        events = discover(d, limit=args.limit)
    except Exception as e:  # noqa: BLE001
        print(f"[failed: {type(e).__name__}: {e}]", file=sys.stderr)
        print("(If the page doesn't exist yet, try an earlier --date or --days-back 1.)",
              file=sys.stderr)
        sys.exit(1)

    if not events:
        print(f"[no events found for {d.isoformat()} — the day's page may be empty or "
              "not yet published; try --days-back 1]", file=sys.stderr)
        sys.exit(0)

    header = (f"# Notable events from Wikipedia Current Events, {d.isoformat()}\n"
              f"# Source: {_portal_page_title(d)}\n"
              f"# Review/trim, then: python corpus.py <this file> --framings-only --max-searches 4\n")
    body = "\n".join(events) + "\n"

    if args.out == "-":
        sys.stdout.write(header + body)
    else:
        out = args.out or f"topics_{d.isoformat()}.txt"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(header + body)
        print(f"Wrote {len(events)} events to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
