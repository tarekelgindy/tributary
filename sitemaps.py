"""
Tributary sitemap capture — the EXISTENCE layer for omission claims
===================================================================
Gate 2 pre-log (2026-06-11): RSS-based omission claims failed the informal
audit — outlets' homepages visibly carried stories their sampled feeds never
showed. The structural cause: an RSS feed is one thin, oddly-scoped editorial
surface, not the outlet. A *news-sitemap* is different in kind: outlets
publish them for Google News, they enumerate (close to) EVERY article from
the last ~48 hours, and they carry per-article titles and timestamps. That is
a near-census — which is the evidentiary standard an absence claim requires.

Division of labor after this module:
    sitemaps  -> whether an outlet covered a story at all   (existence)
    RSS feeds -> how hard the outlet pushed it              (prominence)
An outlet with no usable news-sitemap gets NO omission claims, ever — an
honest gap, never a guess (same posture as bias_db's unrated outlets).

CLI:
    python sitemaps.py --discover            # probe robots.txt + common paths
    python sitemaps.py --discover --key fox  # one outlet, verbose
    python sitemaps.py --capture             # daily snapshot -> agenda/sitemaps/
    python sitemaps.py --capture --force     # re-capture even if today's exists

Free to run: HTTP only, no API key, no model. Capture is daily-guarded —
sitemaps cover ~48h, so once a day loses nothing and twice is harmless.
"""

import argparse
import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

_ROOT = Path(__file__).resolve().parent
ROSTER_PATH = _ROOT / "data" / "agenda_outlets.json"
SITEMAP_DIR = _ROOT / "agenda" / "sitemaps"

_HEADERS = {
    "User-Agent": "Tributary/0.1 (https://github.com/tarekelgindy/tributary) httpx",
    "Accept": "application/xml, text/xml, */*",
}

# Common news-sitemap locations, probed after robots.txt. Ordered roughly by
# how often each convention appears in the wild (WordPress/Yoast, Arc, custom).
COMMON_PATHS = [
    "/news-sitemap.xml", "/sitemap-news.xml", "/sitemap_news.xml",
    "/sitemaps/news.xml", "/sitemap/news.xml", "/google-news-sitemap.xml",
    "/sitemaps/sitemap-google-news.xml", "/arc/outboundfeeds/news-sitemap/?outputType=xml",
    "/feeds/sitemap_news.xml", "/sitemap.xml",
]

# Keep only articles this recent in a capture. Sitemaps advertise ~48h; the
# margin tolerates timezone-sloppy publishers.
KEEP_HOURS = 72
MAX_ENTRIES_PER_OUTLET = 1500

_SLUG_DROP = re.compile(r"\.(html?|php|cms|ece|amp)$", re.I)
_NON_WORD = re.compile(r"[-_]+")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fetch(url: str, timeout: float = 30.0) -> tuple:
    """-> (status, bytes, err). Transparent gzip (by suffix or magic bytes)."""
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS,
                          follow_redirects=True) as client:
            r = client.get(url)
            content = r.content
            if content[:2] == b"\x1f\x8b":      # gzipped payload (e.g. .xml.gz)
                try:
                    content = gzip.decompress(content)
                except OSError:
                    pass
            return r.status_code, content, ""
    except Exception as e:  # noqa: BLE001 — a dead sitemap is data
        return 0, b"", f"{type(e).__name__}: {e}"


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _slug_title(url: str) -> str:
    """Headline proxy from the URL slug — most CMSes derive the slug from the
    headline. 'us-launches-new-strikes-against-iran' -> readable text.
    Returns '' when the slug is non-verbal (ids, dates)."""
    path = url.split("?")[0].rstrip("/")
    seg = path.rsplit("/", 1)[-1]
    seg = _SLUG_DROP.sub("", seg)
    seg = re.sub(r"-?\d{5,}$", "", seg)          # trailing article ids
    words = [w for w in _NON_WORD.split(seg) if w]
    verbal = [w for w in words if not w.isdigit()]
    if len(verbal) < 3:                          # not headline-shaped
        return ""
    return " ".join(verbal).strip()


def parse_sitemap(content: bytes) -> tuple:
    """Parse a sitemap or sitemap-index document.
    -> (kind, items) where kind is 'index' (items = child sitemap URLs) or
    'urlset' (items = article dicts {url, title, published, title_source})."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        # tolerate stray leading bytes / BOM junk before the XML declaration
        text = content.decode("utf-8", "replace")
        start = text.find("<")
        if start < 0:
            return "error", []
        try:
            root = ET.fromstring(text[start:])
        except ET.ParseError:
            return "error", []

    kind = _strip_ns(root.tag)
    if kind == "sitemapindex":
        urls = []
        for sm in root:
            for child in sm:
                if _strip_ns(child.tag) == "loc" and child.text:
                    urls.append(child.text.strip())
        return "index", urls

    if kind != "urlset":
        return "error", []

    items = []
    for url_el in root:
        if _strip_ns(url_el.tag) != "url":
            continue
        loc = title = pub = lastmod = ""
        for child in url_el.iter():
            t = _strip_ns(child.tag)
            txt = (child.text or "").strip()
            if not txt:
                continue
            if t == "loc" and not loc:
                loc = txt
            elif t == "title":                   # news:title
                title = txt
            elif t == "publication_date":        # news:publication_date
                pub = txt
            elif t == "lastmod" and not lastmod:
                lastmod = txt
        if not loc:
            continue
        title_source = "news" if title else "slug"
        if not title:
            title = _slug_title(loc)
        if not title:
            continue                             # nothing headline-shaped
        items.append({"url": loc, "title": title,
                      "published": pub or lastmod,
                      "title_source": title_source})
    return "urlset", items


def _parse_when(s: str):
    """ISO-ish timestamp -> aware datetime, or None."""
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        d = datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fetch_articles(sitemap_url: str, keep_hours: int = KEEP_HOURS,
                   max_children: int = 6) -> tuple:
    """Fetch a news sitemap (following one level of index, preferring child
    sitemaps that look news-ish/recent) -> (articles, err). Articles are
    deduped by URL and filtered to the keep window; undated articles from a
    news-titled sitemap are kept (absence of a date is not staleness — the
    sitemap itself only advertises recent content)."""
    status, content, err = _fetch(sitemap_url)
    if status != 200 or not content:
        return [], err or f"HTTP {status}"
    kind, items = parse_sitemap(content)
    if kind == "error":
        return [], "unparseable XML"
    if kind == "index":
        # prefer children whose URL mentions news/latest/recent, else newest few
        ranked = sorted(items, key=lambda u: (("news" not in u.lower())
                                              and ("latest" not in u.lower()), u))
        articles, child_err = [], ""
        for child in ranked[:max_children]:
            st, ct, e = _fetch(child)
            if st != 200 or not ct:
                child_err = e or f"HTTP {st}"
                continue
            k2, it2 = parse_sitemap(ct)
            if k2 == "urlset":
                articles.extend(it2)
        items = articles
        if not items:
            return [], f"index had no readable children ({child_err})"

    cutoff = _now_utc() - timedelta(hours=keep_hours)
    seen, out = set(), []
    for it in items:
        if it["url"] in seen:
            continue
        seen.add(it["url"])
        when = _parse_when(it["published"])
        if when is not None and when < cutoff:
            continue
        out.append(it)
        if len(out) >= MAX_ENTRIES_PER_OUTLET:
            break
    return out, ""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _robots_sitemaps(domain: str) -> list:
    status, content, _ = _fetch(f"https://{domain}/robots.txt", timeout=15.0)
    if status != 200:
        return []
    urls = []
    for line in content.decode("utf-8", "replace").splitlines():
        if line.lower().startswith("sitemap:"):
            urls.append(line.split(":", 1)[1].strip())
    # news-ish first
    return sorted(urls, key=lambda u: ("news" not in u.lower(), len(u)))


def discover_outlet(domain: str, verbose: bool = False) -> dict:
    """Find the best news-sitemap candidate for a domain. 'Best' = yields the
    most articles inside the keep window, preferring real news:title entries
    over slug-derived ones. Returns {url, articles, titled, note} or {}."""
    candidates = _robots_sitemaps(domain)
    candidates += [f"https://{domain}{p}" for p in COMMON_PATHS]
    best = {}
    tried = set()
    for url in candidates:
        if url in tried:
            continue
        tried.add(url)
        articles, err = fetch_articles(url)
        titled = sum(1 for a in articles if a["title_source"] == "news")
        dated = sum(1 for a in articles if a["published"])
        if verbose:
            print(f"    {len(articles):>4} articles ({titled} news-titled, "
                  f"{dated} dated)  {err or ''}  {url}", file=sys.stderr)
        if not articles:
            continue
        score = (titled, len(articles))
        if not best or score > best["_score"]:
            best = {"url": url, "articles": len(articles), "titled": titled,
                    "dated": dated, "_score": score}
        if titled >= 20:        # a real news-sitemap; stop probing politely
            break
    if best:
        best.pop("_score", None)
    return best


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def capture(force: bool = False) -> dict:
    """One daily snapshot of every roster outlet's news sitemap into
    agenda/sitemaps/<date>.json. Idempotent per UTC day unless --force."""
    roster = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    today = _now_utc().date().isoformat()
    SITEMAP_DIR.mkdir(parents=True, exist_ok=True)
    path = SITEMAP_DIR / f"{today}.json"
    if path.exists() and not force:
        print(f"[sitemaps] {path.name} already captured today (use --force "
              f"to redo)", file=sys.stderr)
        return json.loads(path.read_text(encoding="utf-8"))

    snap = {"is_sitemap_snapshot": True, "captured_at": _now_utc().isoformat(),
            "keep_hours": KEEP_HOURS, "outlets": {}}
    ok = absent = failed = 0
    for o in roster.get("outlets", []):
        if o.get("stream") != "news":
            continue
        sm_url = o.get("news_sitemap", "")
        if not sm_url:
            snap["outlets"][o["key"]] = {"name": o["name"], "sitemap": "",
                                         "articles": [], "error": "no news_sitemap in roster"}
            absent += 1
            continue
        articles, err = fetch_articles(sm_url)
        # Multilingual outlets (found live: DW's sitemap mixes en/de/ar/…)
        # would generate false misses — non-English titles can't match
        # English story centroids. The roster filter keeps one edition.
        url_filter = o.get("sitemap_url_filter", "")
        if url_filter:
            articles = [a for a in articles if url_filter in a["url"]]
        snap["outlets"][o["key"]] = {
            "name": o["name"], "sitemap": sm_url,
            "n_articles": len(articles),
            "n_news_titled": sum(1 for a in articles if a["title_source"] == "news"),
            "error": err, "articles": articles,
        }
        if articles:
            ok += 1
        else:
            failed += 1
        print(f"  {'ok ' if articles else 'FAIL'}  {o['key']:<20} "
              f"{len(articles):>5} articles  {err}", file=sys.stderr)
    path.write_text(json.dumps(snap, indent=1, ensure_ascii=False),
                    encoding="utf-8")
    print(f"[sitemaps] {ok} ok / {failed} failed / {absent} without a "
          f"sitemap -> {path}", file=sys.stderr)
    return snap


def load_sitemap_titles(days: int) -> dict:
    """For agenda.py: outlet_key -> list[{title, url, title_source}] across
    the window's sitemap snapshots, deduped by URL. Outlets absent from the
    result have NO sitemap evidence and must receive no omission claims."""
    if not SITEMAP_DIR.exists():
        return {}
    cutoff = (_now_utc() - timedelta(days=days + 1)).date().isoformat()
    out = {}
    for p in sorted(SITEMAP_DIR.glob("*.json")):
        if p.stem < cutoff:
            continue
        try:
            snap = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for okey, o in snap.get("outlets", {}).items():
            arts = o.get("articles", [])
            if not arts:
                continue
            slot = out.setdefault(okey, {})
            for a in arts:
                slot[a["url"]] = a
    return {k: list(v.values()) for k, v in out.items()}


def main():
    p = argparse.ArgumentParser(
        description="News-sitemap discovery + daily capture (the existence "
                    "layer for omission claims).")
    p.add_argument("--discover", action="store_true",
                   help="probe robots.txt + common paths for every roster outlet")
    p.add_argument("--key", default="", help="with --discover: one outlet, verbose")
    p.add_argument("--capture", action="store_true",
                   help="daily snapshot of all roster news sitemaps")
    p.add_argument("--force", action="store_true",
                   help="with --capture: redo even if today's snapshot exists")
    args = p.parse_args()

    if args.discover:
        roster = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
        outlets = [o for o in roster.get("outlets", []) if o.get("stream") == "news"]
        if args.key:
            outlets = [o for o in outlets if o["key"] == args.key]
        for o in outlets:
            print(f"{o['key']} ({o['domain']}):", file=sys.stderr)
            best = discover_outlet(o["domain"], verbose=bool(args.key))
            if best:
                print(f"  -> {best['articles']} articles "
                      f"({best['titled']} news-titled, {best['dated']} dated)  "
                      f"{best['url']}")
            else:
                print("  -> NONE FOUND (this outlet gets no omission claims)")
    elif args.capture:
        capture(force=args.force)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
