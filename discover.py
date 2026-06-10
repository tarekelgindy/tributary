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


_TOPVIEW_API = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/top/"
                "en.wikipedia.org/all-access/{year}/{month:02d}/{day:02d}")
# Non-article pages to drop from the most-viewed list.
_TOPVIEW_SKIP = re.compile(r"^(Main_Page|Special:|Portal:|Wikipedia:|Help:|"
                           r"Category:|Template:|File:|Talk:|User:)")


def fetch_topview(d: date, limit: int = 50, timeout: float = 20.0) -> list:
    """The most-viewed Wikipedia articles for a date (a 'what people are
    actually looking up' trending signal), as readable topic phrases."""
    url = _TOPVIEW_API.format(year=d.year, month=d.month, day=d.day)
    headers = {
        "User-Agent": "Tributary/0.1 (https://github.com/tarekelgindy/tributary) httpx",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=timeout, headers=headers) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    items = (data.get("items") or [{}])[0].get("articles") or []
    topics = []
    for a in items:
        art = a.get("article", "")
        if not art or _TOPVIEW_SKIP.match(art):
            continue
        topic = unescape(art.replace("_", " ")).strip()
        if topic and topic not in topics:
            topics.append(topic)
        if len(topics) >= limit:
            break
    return topics


def discover(d: date, source: str = "wikipedia", limit: int = 0,
             min_len: int = 30, max_len: int = 280) -> list:
    if source == "topview":
        return fetch_topview(d, limit=limit or 50)
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


TRIAGE_SYSTEM = """You triage candidate topics for narrative analysis. Each input line was scraped from Wikipedia (Current Events portal sentences, or bare trending page titles from TopView).

KEEP a line only if it describes a SPECIFIC EVENT, decision, dispute, or development — something that happened, concrete enough that news coverage of it can be searched and competing framings of it can be mapped.

DROP a line if it is:
- a bare name of a person, place, organization, or product with no event stated (e.g. "Charlie Kirk", "Greenland") — it may be trending *because* of an event, but the line itself names no event, and inventing one would be guessing;
- a title of a film, TV show, game, song, or other media work;
- a reference or list page ("Deaths in 2026", date pages, "List of ...");
- a generic perennial topic or navigational page;
- a sports fixture/standings page with no specific occurrence stated (a specific result or election IS an event).

Do NOT rewrite or complete any line. Judge only what is written.

Input: a JSON list of {"idx": int, "text": str}.
Output: ONLY a JSON object, no prose, no code fences:
{"triage": [{"idx": 0, "keep": true, "reason": "<= 6 words"}, ...]}
Every input idx must appear exactly once."""


def clean_topics(topics: list, model: str = "claude-haiku-4-5-20251001") -> tuple:
    """One batched Haiku call that classifies each topic keep/drop per
    TRIAGE_SYSTEM. Returns (kept, dropped) where dropped is [{topic, reason}].
    Requires ANTHROPIC_API_KEY; costs ~a fifth of a cent per ~200 topics.
    Fails open per item: anything the response doesn't cover is KEPT (the
    downstream contestedness pre-filter is the next net)."""
    import json as _json
    import anthropic
    from fingerprint import _parse_json_safe  # battle-tested salvage parser

    if not topics:
        return [], []
    client = anthropic.Anthropic()
    payload = [{"idx": i, "text": t} for i, t in enumerate(topics)]
    resp = client.messages.create(
        model=model, max_tokens=8192,
        system=[{"type": "text", "text": TRIAGE_SYSTEM}],
        messages=[{"role": "user", "content": _json.dumps(payload, indent=1)}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = _parse_json_safe(text) or {}
    verdicts = {}
    for v in (data.get("triage") or []):
        if isinstance(v, dict) and "idx" in v:
            try:
                verdicts[int(v["idx"])] = (bool(v.get("keep", True)),
                                           str(v.get("reason", "")))
            except (TypeError, ValueError):
                continue
    kept, dropped = [], []
    for i, t in enumerate(topics):
        keep, reason = verdicts.get(i, (True, "(unjudged - kept)"))
        (kept if keep else dropped).append({"topic": t, "reason": reason} if not keep else t)
    return kept, dropped


def _apply_clean(events: list) -> list:
    """Run clean_topics over events with progress + drop log on stderr."""
    print(f"[clean] triaging {len(events)} topics (one Haiku call)...", file=sys.stderr)
    kept, dropped = clean_topics(events)
    for d in dropped:
        print(f"  [drop] {d['topic'][:70]} — {d['reason']}", file=sys.stderr)
    print(f"[clean] kept {len(kept)} of {len(events)} "
          f"({len(dropped)} dropped as non-events)", file=sys.stderr)
    return kept


def main():
    p = argparse.ArgumentParser(
        description="Pull notable events from Wikipedia Current Events into a topics file.")
    p.add_argument("--date", default="", help="YYYY-MM-DD (default: today, UTC).")
    p.add_argument("--days-back", type=int, default=0,
                   help="N days before today (ignored if --date is given).")
    p.add_argument("--source", choices=["wikipedia", "topview"], default="wikipedia",
                   help="wikipedia = Current Events portal (curated notable events, "
                        "default); topview = the day's most-viewed Wikipedia pages "
                        "(a 'what people are looking up' trending signal — pages, not "
                        "events; pair with corpus.py --min-contestedness to filter).")
    p.add_argument("--out", default="",
                   help="Output file (default: topics_<date>.txt). Use '-' for stdout.")
    p.add_argument("--limit", type=int, default=0, help="Max items (0 = all).")
    p.add_argument("--clean", action="store_true",
                   help="Triage topics with one cheap Haiku call: keep concrete "
                        "events, drop bare names / media titles / list pages. "
                        "Needs ANTHROPIC_API_KEY; ~$0.002 per 200 topics. "
                        "Especially useful with --source topview.")
    p.add_argument("--clean-file", default="",
                   help="Skip discovery; triage an EXISTING topics file in place "
                        "(comments preserved, drops logged).")
    args = p.parse_args()

    if args.clean_file:
        path = args.clean_file
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        comments = [ln for ln in lines if ln.strip().startswith("#")]
        topics = [ln.strip() for ln in lines
                  if ln.strip() and not ln.strip().startswith("#")]
        kept = _apply_clean(topics)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(comments + kept) + "\n")
        print(f"[clean] rewrote {path}: {len(kept)} topics", file=sys.stderr)
        return

    if args.date:
        try:
            d = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print(f"[bad --date '{args.date}', expected YYYY-MM-DD]", file=sys.stderr)
            sys.exit(1)
    else:
        d = datetime.now(timezone.utc).date() - timedelta(days=args.days_back)

    label = ("most-viewed pages (TopView)" if args.source == "topview"
             else "Current Events")
    print(f"Fetching Wikipedia {label} for {d.isoformat()}...", file=sys.stderr)
    try:
        events = discover(d, source=args.source, limit=args.limit)
    except Exception as e:  # noqa: BLE001
        print(f"[failed: {type(e).__name__}: {e}]", file=sys.stderr)
        print("(If the page/data doesn't exist yet, try an earlier --date or --days-back 1.)",
              file=sys.stderr)
        sys.exit(1)

    if not events:
        print(f"[nothing found for {d.isoformat()} — the day's data may not be "
              "published yet; try --days-back 1]", file=sys.stderr)
        sys.exit(0)

    if args.clean:
        events = _apply_clean(events)

    if args.source == "topview":
        src_note = ("Wikipedia most-viewed pages (TopView) — topics/pages, not events")
        src_line = "Wikipedia Pageviews top API"
        run_hint = "python corpus.py <this file> --min-contestedness 6 --max-searches 6"
    else:
        src_note = "Wikipedia Current Events"
        src_line = _portal_page_title(d)
        run_hint = "python corpus.py <this file> --max-searches 6"
    header = (f"# {src_note}, {d.isoformat()}\n"
              f"# Source: {src_line}\n"
              f"# Review/trim, then: {run_hint}\n")
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
