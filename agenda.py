"""
Tributary agenda layer — RSS prominence capture + agenda fingerprints
=====================================================================
Phase 2 (ROADMAP): selection and prominence are where outlet bias lives even
when individual stories are fair — and they are countable, structural, robust
signals (P5). Nothing in this module asks an LLM what a story means; the only
model on the hot path is the local embedding model (matcher.py), used to
group headlines that say the same thing.

Four subcommands, four artifacts:

  --capture   Snapshot every feed in data/agenda_outlets.json: items WITH
              their feed position (the prominence signal) into one raw,
              dated file under agenda/snapshots/. Free, no API key. Designed
              to run on a schedule (cron / Task Scheduler), several times a
              day — position churn across captures is the signal.
  --universe  Snapshot the day's event universe via discover.py (Wikipedia
              Current Events + most-viewed) into agenda/universe/.
  --stories   Cluster the period's headlines into cross-outlet stories using
              the Phase 1 matcher's embeddings; annotate each story with
              carried-by-N-outlets and its lean spread (one-side-only flag).
              This doubles as DISCOVERY: observed coverage divergence
              replaces predicted contestedness. Writes a topics file that
              corpus.py can consume directly.
  --report    The period's agenda report: per-outlet attention distributions
              (the outlet's agenda fingerprint), ecosystem attention, the
              omission report, and the universe cross-check — one JSON under
              agenda/reports/, renderable by fingerprint_viewer.html.

Honesty constraints (these are load-bearing — Gate 2):
  - RSS is a SAMPLE of an outlet's output, not a census. Every omission is
    phrased "zero observed items in the outlet's sampled feeds over the
    period", and the report embeds the sampled-feed list, capture timestamps,
    and the caveat in the JSON itself. No "never covered" claim is published
    before the Gate 2 audit (manual site search for 10 claimed omissions).
  - Leans come from bias_db (AllSides snapshot) at runtime — attributable,
    never invented. Unrated outlets are reported as unrated, and one-side-only
    flags are computed over RATED US news outlets only.
  - The fact-checker stream contributes SELECTION only (what they chose to
    check); verdicts are never ingested (P1).

Cost: capture and clustering are entirely local/free. No Anthropic calls.
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import httpx

_ROOT = Path(__file__).resolve().parent
ROSTER_PATH = _ROOT / "data" / "agenda_outlets.json"
AGENDA_DIR = _ROOT / "agenda"
SNAP_DIR = AGENDA_DIR / "snapshots"
UNIVERSE_DIR = AGENDA_DIR / "universe"
REPORT_DIR = AGENDA_DIR / "reports"

# Same UA policy as discover.py: descriptive, with contact info.
_HEADERS = {
    "User-Agent": "Tributary/0.1 (https://github.com/tarekelgindy/tributary) httpx",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}

# Headlines of the same story across outlets share entities but not phrasing;
# this threshold was tuned by eyeballing real clusters (see --stories --show).
# It gates joining a story's centroid, so raising it splits stories, lowering
# it merges them. Calibrate against real captures before trusting downstream.
STORY_THRESHOLD = 0.62
# A universe event counts as "covered" if any headline reaches this similarity.
# Uncalibrated until Gate 2 — the report labels this mapping experimental.
UNIVERSE_THRESHOLD = 0.60
# Omission guards (uncalibrated until Gate 2; all err toward NOT claiming an
# omission, because a false "they never covered it" is the project's most
# damaging possible error):
#   - EXISTENCE is tested against news-sitemap titles (near-census of ~48h),
#     never against RSS feeds — the Gate 2 pre-log showed one feed is a thin,
#     oddly-scoped slice of an outlet, and homepage spot checks falsified
#     RSS-based "missed" rows immediately. No sitemap evidence -> no claim.
#   - a sitemap with fewer than OMISSION_MIN_SITEMAP in-window articles is
#     not a census either -> "insufficient sample", no claim;
#   - any sitemap title within ADJACENT_THRESHOLD of the story centroid is
#     "adjacent coverage", never "omitted"; at or above COVERED_THRESHOLD it
#     is covered outright — just not feed-featured (a prominence finding,
#     not an omission);
#   - OMISSION_MIN_SAMPLE still gates the RSS-side attention/prominence rows.
OMISSION_MIN_SAMPLE = 8
OMISSION_MIN_SITEMAP = 30
ADJACENT_THRESHOLD = 0.42
COVERED_THRESHOLD = STORY_THRESHOLD

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

OMISSION_CAVEAT = (
    "Existence is tested against the outlet's NEWS-SITEMAP — the near-census "
    "of recent articles outlets publish for search crawlers — never against "
    "RSS feeds (the Gate 2 pre-log showed feeds are thin, oddly-scoped "
    "slices: homepage spot checks falsified RSS-based 'missed' rows "
    "immediately). Outlets without a usable news-sitemap receive NO omission "
    "claims at all. A sitemap miss still is not proof of silence — sitemaps "
    "can lag or omit, syndicated coverage may live off-domain, and the "
    "similarity thresholds are uncalibrated — so every claim ships its "
    "nearest-article evidence, and per ROADMAP Gate 2 no omission claim is "
    "published before a manual audit against the outlet's own site. A story "
    "found in the sitemap but absent from the outlet's front feeds is "
    "reported separately as 'covered, not feed-featured' — a prominence "
    "observation, not an omission."
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(s: str, limit: int = 280) -> str:
    s = _TAG_RE.sub(" ", unescape(s or ""))
    s = _WS_RE.sub(" ", s).strip()
    return s[:limit].strip()


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def load_roster() -> dict:
    d = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    outlets = d.get("outlets", [])
    keys = [o["key"] for o in outlets]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate outlet keys in agenda_outlets.json")
    return {"meta": d.get("_meta", {}), "outlets": outlets,
            "by_key": {o["key"]: o for o in outlets}}


def resolve_leans(roster: dict) -> dict:
    """outlet key -> AllSides bucket ('left'..'right') or '' for unrated.
    bias_db is the only authority; we never invent a rating (its ethos)."""
    from bias_db import BiasDB
    db = BiasDB()
    leans = {}
    for o in roster["outlets"]:
        r = db.lookup(domain=o["domain"], name=o["name"], prefer_news=True)
        leans[o["key"]] = r.bucket if (r and r.is_news_media) else ""
    return leans


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

def fetch_feed(url: str, timeout: float = 25.0) -> tuple:
    """-> (status_code, content_bytes, error_str). Never raises."""
    try:
        with httpx.Client(timeout=timeout, headers=_HEADERS,
                          follow_redirects=True) as client:
            resp = client.get(url)
            return resp.status_code, resp.content, ""
    except Exception as e:  # noqa: BLE001 — a dead feed is data, not a crash
        return 0, b"", f"{type(e).__name__}: {e}"


def parse_feed(content: bytes) -> tuple:
    """-> (items, bozo_note). Position is 1-based feed order — the
    prominence signal. feedparser tolerates the malformed XML real feeds
    emit (unescaped ampersands etc.) where a strict parser would drop the
    whole outlet from the sample and silently bias it."""
    import calendar
    import feedparser
    parsed = feedparser.parse(content)
    items = []
    for i, e in enumerate(parsed.entries[:200], start=1):
        title = _clean_text(getattr(e, "title", ""), 300)
        if not title:
            continue
        # Normalized timestamp for staleness checks: feedparser parses the
        # zoo of RSS date formats into a UTC struct_time. Found live: CNN's
        # top-stories feed was a zombie serving April-2023 items — without a
        # parseable date the omission report would claim CNN "never
        # mentioned" everything in the period.
        st = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        published_iso = ""
        if st:
            published_iso = datetime.fromtimestamp(
                calendar.timegm(st), tz=timezone.utc).isoformat()
        items.append({
            "position": i,
            "title": title,
            "link": getattr(e, "link", "") or "",
            "guid": getattr(e, "id", "") or getattr(e, "link", "") or title,
            "published": getattr(e, "published", "") or getattr(e, "updated", "") or "",
            "published_iso": published_iso,
            "summary": _clean_text(getattr(e, "summary", ""), 280),
        })
    bozo = ""
    if getattr(parsed, "bozo", 0) and items:
        bozo = f"recovered-from-malformed-xml: {type(getattr(parsed, 'bozo_exception', '')).__name__}"
    return items, bozo


def capture(save: bool = True) -> dict:
    """One capture run over the whole roster -> one snapshot dict (saved to
    agenda/snapshots/<UTC-stamp>.json). Per-outlet failures are recorded in
    the snapshot — an unreachable feed is an honest gap in the sample, and
    pretending otherwise would bias every downstream count."""
    roster = load_roster()
    captured_at = _now_utc()
    snap = {
        "is_agenda_snapshot": True,
        "captured_at": captured_at.isoformat(),
        "roster_curated": roster["meta"].get("curated", ""),
        "outlets": {},
    }
    ok = failed = 0
    for o in roster["outlets"]:
        status, content, err = fetch_feed(o["rss"])
        items, bozo = ([], "")
        if status == 200 and content:
            items, bozo = parse_feed(content)
        if items:
            ok += 1
        else:
            failed += 1
            if not err:
                err = f"HTTP {status}" if status != 200 else "no items parsed"
        # Zombie-feed tripwire: a "top stories" feed whose newest parseable
        # date is days old is almost certainly dead, not quiet (found live:
        # CNN's feed served April 2023). Flagged at capture so it's noticed
        # immediately, and excluded from omission claims downstream.
        newest = max((it["published_iso"] for it in items if it["published_iso"]),
                     default="")
        stale = bool(newest) and newest < (captured_at - timedelta(days=3)).isoformat()
        snap["outlets"][o["key"]] = {
            "name": o["name"], "domain": o["domain"], "stream": o["stream"],
            "country": o["country"], "scope": o["scope"], "feed_url": o["rss"],
            "http_status": status, "error": err if not items else "",
            "note": bozo, "newest_published": newest, "stale": stale,
            "n_items": len(items), "items": items,
        }
        print(f"  {'ok ' if items else 'FAIL'}  {o['key']:<20} "
              f"{len(items):>3} items  "
              f"{('STALE feed — newest item ' + newest[:10]) if stale else ''}"
              f"{err if not items else ''}", file=sys.stderr)
    snap["outlets_ok"] = ok
    snap["outlets_failed"] = failed
    if save:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        path = SNAP_DIR / f"{captured_at.strftime('%Y-%m-%dT%H%M')}Z.json"
        path.write_text(json.dumps(snap, indent=1, ensure_ascii=False),
                        encoding="utf-8")
        print(f"[capture] {ok} ok / {failed} failed -> {path}", file=sys.stderr)
    else:
        print(f"[capture] {ok} ok / {failed} failed (dry run, not saved)",
              file=sys.stderr)
    return snap


# ---------------------------------------------------------------------------
# Universe (event-universe snapshot via discover.py)
# ---------------------------------------------------------------------------

def capture_universe(d: date = None) -> dict:
    """The day's event universe: Wikipedia Current Events (editorial 'what
    happened') + most-viewed articles (attention signal). Both free.
    Defaults to YESTERDAY (UTC): the Current Events page fills in over its
    day, and pageview data only publishes after the day ends — capturing the
    completed day is the honest snapshot."""
    import discover
    d = d or (_now_utc().date() - timedelta(days=1))
    events, topview, errors = [], [], []
    try:
        events = [discover._clean_event(t) for t in discover.fetch_events(d)]
        events = [e for e in events if len(e) >= 30]
    except Exception as e:  # noqa: BLE001
        errors.append(f"current-events: {type(e).__name__}: {e}")
    try:
        topview = discover.fetch_topview(d)
    except Exception as e:  # noqa: BLE001
        errors.append(f"topview: {type(e).__name__}: {e}")
    uni = {
        "is_agenda_universe": True,
        "date": d.isoformat(),
        "captured_at": _now_utc().isoformat(),
        "current_events": events,
        "topview": topview,
        "errors": errors,
    }
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    path = UNIVERSE_DIR / f"{d.isoformat()}.json"
    path.write_text(json.dumps(uni, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[universe] {d}: {len(events)} current events, {len(topview)} "
          f"topview -> {path}" + (f"  errors: {errors}" if errors else ""),
          file=sys.stderr)
    return uni


# ---------------------------------------------------------------------------
# Aggregation: snapshots -> deduped per-outlet items
# ---------------------------------------------------------------------------

def load_snapshots(days: int) -> list:
    """All snapshots whose captured_at falls within the last <days> days."""
    if not SNAP_DIR.exists():
        return []
    cutoff = _now_utc() - timedelta(days=days)
    snaps = []
    for p in sorted(SNAP_DIR.glob("*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        try:
            ts = datetime.fromisoformat(s.get("captured_at", ""))
        except ValueError:
            continue
        if ts >= cutoff:
            s["_file"] = p.name
            snaps.append(s)
    return snaps


def aggregate_items(snaps: list, days: int = 0) -> tuple:
    """Dedupe items across captures (an item appearing in 6 captures is one
    item). Key: (outlet, guid). Keeps best (minimum) observed position and
    the count of captures it appeared in (persistence — front-page staying
    power).

    With days > 0, items whose parseable published date falls BEFORE the
    window are dropped and counted per outlet: a zombie feed (CNN served
    April-2023 items) must not put its outlet into omission claims or seed
    stale story clusters. Undated items are kept — absence of a date is not
    evidence of staleness. Returns (items_by_outlet, stale_dropped)."""
    cutoff = ((_now_utc() - timedelta(days=days)).isoformat()
              if days > 0 else "")
    # The roster evolves (dead feeds get replaced); older snapshots may carry
    # outlets that no longer exist. Those items have no roster entry to
    # report under, so they're excluded from the analysis (the raw snapshot
    # remains the evidence of record).
    roster_keys = set(load_roster()["by_key"])
    agg = {}     # outlet_key -> {guid: item}
    stale = {}   # outlet_key -> dropped count
    for snap in snaps:
        for okey, o in snap.get("outlets", {}).items():
            if okey not in roster_keys:
                continue
            slot = agg.setdefault(okey, {})
            for it in o.get("items", []):
                pub = it.get("published_iso", "")
                if cutoff and pub and pub < cutoff:
                    stale[okey] = stale.get(okey, 0) + 1
                    continue
                cur = slot.get(it["guid"])
                if cur is None:
                    slot[it["guid"]] = {
                        "title": it["title"], "link": it["link"],
                        "published": it.get("published", ""),
                        "summary": it.get("summary", ""),
                        "best_position": it["position"],
                        "captures_seen": 1,
                        "first_seen": snap.get("captured_at", ""),
                    }
                else:
                    cur["captures_seen"] += 1
                    cur["best_position"] = min(cur["best_position"], it["position"])
    return {k: list(v.values()) for k, v in agg.items()}, stale


# ---------------------------------------------------------------------------
# Stories: cluster headlines across outlets
# ---------------------------------------------------------------------------

def cluster_stories(items_by_outlet: dict, threshold: float = STORY_THRESHOLD) -> tuple:
    """Greedy centroid clustering of headline embeddings (matcher.py's local
    model — free). Items arrive sorted by first_seen so clusters grow
    chronologically; each item joins the best-matching story centroid at or
    above the threshold, else founds a new story. Centroid (not single-link)
    assignment limits chaining drift.

    Returns (stories, vecs_by_outlet):
      stories: [{members: [(outlet_key, item)], centroid, member_vecs}]
      vecs_by_outlet: {outlet_key: (np.ndarray, [titles])} — every embedded
      item per outlet, for the omission report's adjacency check."""
    import numpy as np
    from matcher import embed_texts

    flat = []
    for okey, items in items_by_outlet.items():
        for it in items:
            flat.append((okey, it))
    if not flat:
        return [], {}
    flat.sort(key=lambda x: x[1].get("first_seen", ""))
    vecs = np.asarray(embed_texts([it["title"] for _, it in flat]),
                      dtype=np.float32)
    by_outlet_rows = {}
    for i, (okey, it) in enumerate(flat):
        by_outlet_rows.setdefault(okey, []).append(i)
    vecs_by_outlet = {okey: (vecs[rows], [flat[i][1]["title"] for i in rows])
                      for okey, rows in by_outlet_rows.items()}

    # Preallocate the centroid matrix so assignment is one matvec per item.
    centroids = np.zeros((len(flat), vecs.shape[1]), dtype=np.float32)
    sums = []      # raw vector sums per story
    stories = []   # member lists per story
    member_vecs = []
    for i, v in enumerate(vecs):
        if stories:
            sims = centroids[:len(stories)] @ v
            j = int(sims.argmax())
            if sims[j] >= threshold:
                stories[j].append(flat[i])
                member_vecs[j].append(v)
                sums[j] = sums[j] + v
                centroids[j] = sums[j] / (np.linalg.norm(sums[j]) or 1.0)
                continue
        stories.append([flat[i]])
        member_vecs.append([v])
        sums.append(v.copy())
        centroids[len(stories) - 1] = v
    return ([{"members": m, "centroid": centroids[k], "member_vecs": member_vecs[k]}
             for k, m in enumerate(stories)], vecs_by_outlet)


def _medoid_title(story: dict) -> str:
    """The member headline closest to the story centroid — a real, observed
    headline (never a synthesized label)."""
    titles = [it["title"] for _, it in story["members"]]
    if len(titles) == 1:
        return titles[0]
    sims = [float(v @ story["centroid"]) for v in story["member_vecs"]]
    return titles[sims.index(max(sims))]


def describe_stories(stories: list, leans: dict, roster: dict) -> list:
    """Stories -> serializable records with carrier/lean structure.
    lean_spread counts DISTINCT rated news outlets per bucket; unrated
    outlets are counted as 'international' or 'unrated' (a US-centric ratings
    DB can't place them — that's a gap, not 'center'). one_side_only is
    computed over RATED US OUTLETS ONLY (the AllSides spectrum is a US
    construct; a culture story carried by Guardian+Al Jazeera+ABC-AU would
    otherwise flag 'left-only' on internationals alone) and requires >=3
    rated US outlets with one side at >=2 and the other at zero. P1: it is a
    structural observation about this sample, not a verdict."""
    by_key = roster["by_key"]
    out = []
    for st in stories:
        per_outlet = {}
        for okey, it in st["members"]:
            rec = per_outlet.setdefault(okey, {
                "outlet": okey, "name": by_key[okey]["name"],
                "stream": by_key[okey]["stream"], "items": 0,
                "best_position": it["best_position"],
                "sample_title": it["title"], "sample_link": it["link"],
            })
            rec["items"] += 1
            if it["best_position"] < rec["best_position"]:
                rec["best_position"] = it["best_position"]
                rec["sample_title"] = it["title"]
                rec["sample_link"] = it["link"]
        news = [r for r in per_outlet.values() if r["stream"] == "news"]
        factcheck = [r for r in per_outlet.values() if r["stream"] == "factcheck"]
        spread = {"left": 0, "lean-left": 0, "center": 0, "lean-right": 0,
                  "right": 0, "unrated": 0, "international": 0}
        locc_us = rocc_us = rated_us = 0
        for r in news:
            o = by_key[r["outlet"]]
            b = leans.get(r["outlet"], "")
            if b:
                spread[b] += 1
                if o["country"] == "US":
                    rated_us += 1
                    if b in ("left", "lean-left"):
                        locc_us += 1
                    elif b in ("right", "lean-right"):
                        rocc_us += 1
            elif o["country"] != "US":
                spread["international"] += 1
            else:
                spread["unrated"] += 1
        one_side = ""
        if rated_us >= 3 and locc_us >= 2 and rocc_us == 0:
            one_side = "no right-of-center US outlet observed"
        elif rated_us >= 3 and rocc_us >= 2 and locc_us == 0:
            one_side = "no left-of-center US outlet observed"
        members_key = "|".join(sorted(f"{k}:{it['link'] or it['title']}"
                                      for k, it in st["members"]))
        out.append({
            "story_id": hashlib.sha256(members_key.encode()).hexdigest()[:12],
            "label": _medoid_title(st),
            "n_outlets": len(news),
            "n_items": sum(r["items"] for r in news),
            "lean_spread": spread,
            "one_side_only": one_side,
            "factcheck_attention": len(factcheck),
            "carriers": sorted(per_outlet.values(),
                               key=lambda r: (-r["items"], r["best_position"])),
            "_centroid": st["centroid"],   # for adjacency checks; stripped before save
        })
    out.sort(key=lambda s: (-s["n_outlets"], -s["n_items"]))
    return out


def attach_traces(stories: list, max_stories: int = 60,
                  min_sim: float = 0.60) -> int:
    """Bridge to Phase 1: match each cross-outlet story label against the
    FINGERPRINT corpus (headline embeddings vs. traced narratives). Embedding
    stage only — free, and explicitly a LEAD for the digest, not a serving
    decision (serving still requires Matcher.match_confirmed, per Gate 1).
    Attaches 'nearest_trace' {fingerprint_id, canonical_phrase, l1/l2 sims,
    decision} where the best neighbor clears min_sim on either axis."""
    try:
        from matcher import Matcher
        m = Matcher()
        if not m.sidecar.get("vectors"):
            return 0
    except Exception:  # noqa: BLE001 — no corpus / no library: silently no-op
        return 0
    n = attached = 0
    for s in stories:
        if s["n_outlets"] < 2 or n >= max_stories:
            continue
        n += 1
        r = m.match(s["label"])
        b = r.best
        if b and max(b.l1_sim, b.l2_sim) >= min_sim:
            s["nearest_trace"] = {
                "fingerprint_id": b.fingerprint_id,
                "canonical_phrase": b.canonical_phrase,
                "l1_sim": round(b.l1_sim, 3), "l2_sim": round(b.l2_sim, 3),
                "decision": r.decision,
                "note": "embedding-stage lead only; not confirmed (Gate 1: "
                        "serving requires the two-stage judge)",
            }
            attached += 1
    return attached


def write_topics(stories: list, days: int, min_outlets: int = 3) -> Path:
    """The discovery output: contested-by-observation topics for corpus.py.
    Selection = carried broadly OR one-side-only OR fact-checker-selected —
    all structural signals. Lines are the medoid headlines (real, observed);
    annotations ride on comment lines corpus.py skips."""
    today = _now_utc().date().isoformat()
    path = AGENDA_DIR / f"topics_{today}.txt"
    lines = [
        f"# Tributary agenda discovery, {today} (last {days} days of captures)",
        "# Selection: carried by >= {0} news outlets, one-side-only, or fact-checker-selected.".format(min_outlets),
        "# Run: python corpus.py <this file> --max-searches 6",
        "",
    ]
    n = 0
    for s in stories:
        flags = []
        if s["one_side_only"]:
            flags.append(s["one_side_only"])
        if s["factcheck_attention"]:
            flags.append(f"fact-checked x{s['factcheck_attention']}")
        broad = s["n_outlets"] >= min_outlets
        if not (broad or flags):
            continue
        sp = s["lean_spread"]
        lines.append(f"# carried by {s['n_outlets']} outlets "
                     f"(L{sp['left'] + sp['lean-left']}/C{sp['center']}/"
                     f"R{sp['right'] + sp['lean-right']}/intl {sp['international']}/"
                     f"unrated {sp['unrated']})"
                     + (f" — {'; '.join(flags)}" if flags else ""))
        lines.append(s["label"])
        n += 1
    AGENDA_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[stories] {n} topics -> {path}", file=sys.stderr)
    return path


# ---------------------------------------------------------------------------
# Report: attention distributions + omissions + universe cross-check
# ---------------------------------------------------------------------------

def attention_distributions(stories: list, items_by_outlet: dict,
                            roster: dict, top: int = 10) -> dict:
    """Per-outlet attention over the story universe — the outlet's agenda
    fingerprint. share = the outlet's items on this story / all its items in
    the period. Purely countable (P5): no weights beyond presence + the best
    feed position, reported separately."""
    per = {}
    story_by_id = {s["story_id"]: s for s in stories}
    carried = {}   # outlet -> [(story_id, items, best_pos)]
    for s in stories:
        for c in s["carriers"]:
            carried.setdefault(c["outlet"], []).append(
                (s["story_id"], c["items"], c["best_position"]))
    for o in roster["outlets"]:
        items_total = len(items_by_outlet.get(o["key"], []))
        rows = carried.get(o["key"], [])
        rows.sort(key=lambda r: (-r[1], r[2]))
        per[o["key"]] = {
            "name": o["name"], "stream": o["stream"],
            "items": items_total, "stories_carried": len(rows),
            "top": [{
                "story_id": sid,
                "label": story_by_id[sid]["label"][:160],
                "items": n, "best_position": pos,
                "share": round(n / items_total, 4) if items_total else 0.0,
            } for sid, n, pos in rows[:top]],
        }
    return per


def omission_report(stories: list, items_by_outlet: dict, roster: dict,
                    leans: dict, sitemap_titles: dict,
                    min_outlets: int = 8) -> dict:
    """For each BROAD story (>= min_outlets distinct news outlets observed
    carrying it), which news outlets show no observed coverage? EXISTENCE is
    tested against news-sitemap titles — RSS feeds only ever supply the
    carrier/prominence side. Guards, each erring toward NOT claiming (a
    false "they never covered it" is the most damaging error this project
    can make — Gate 2):
      1. no news-sitemap (or none fetched in window) -> NO omission claims;
      2. a sitemap with < OMISSION_MIN_SITEMAP in-window articles is not a
         census either -> 'insufficient sample', no claims;
      3. a sitemap title at/above COVERED_THRESHOLD -> 'covered, not
         feed-featured' (a prominence finding, not an omission);
      4. at/above ADJACENT_THRESHOLD -> 'adjacent coverage', not an omission;
      5. every remaining claim ships its nearest-article evidence so the
         Gate 2 audit can check it."""
    from matcher import embed_texts
    news_keys = [o["key"] for o in roster["outlets"] if o["stream"] == "news"]
    sampled = {k: len(items_by_outlet.get(k, [])) for k in news_keys}
    broad = [s for s in stories if s["n_outlets"] >= min_outlets]

    vec_cache = {}     # okey -> (matrix, [articles]) embedded on first need
    def outlet_vecs(k):
        if k not in vec_cache:
            arts = sitemap_titles.get(k, [])
            if arts:
                import numpy as np
                m = np.asarray(embed_texts([a["title"] for a in arts]),
                               dtype=np.float32)
                vec_cache[k] = (m, arts)
            else:
                vec_cache[k] = (None, [])
        return vec_cache[k]

    rows = []
    no_sitemap = []
    for k in news_keys:
        n_sitemap = len(sitemap_titles.get(k, []))
        if n_sitemap == 0:
            no_sitemap.append(k)
            continue   # no census -> no omission claim possible
        row = {
            "outlet": k, "name": roster["by_key"][k]["name"],
            "lean": leans.get(k, ""),
            "sitemap_articles": n_sitemap,
            "rss_items_sampled": sampled[k],
            "broad_stories": len(broad),
            "insufficient_sample": n_sitemap < OMISSION_MIN_SITEMAP,
            "missed": 0, "missed_stories": [],
            "adjacent_stories": [], "covered_not_featured": [],
        }
        if not row["insufficient_sample"]:
            for s in broad:
                if any(c["outlet"] == k for c in s["carriers"]):
                    continue
                vecs, arts = outlet_vecs(k)
                sims = vecs @ s["_centroid"]
                i = int(sims.argmax())
                near_sim = round(float(sims[i]), 3)
                entry = {"story_id": s["story_id"], "label": s["label"][:160],
                         "n_outlets": s["n_outlets"],
                         "nearest_sim": near_sim,
                         "nearest_article": arts[i]["title"][:160],
                         "nearest_url": arts[i]["url"],
                         "evidence_source": "news-sitemap"}
                if near_sim >= COVERED_THRESHOLD:
                    row["covered_not_featured"].append(entry)
                elif near_sim >= ADJACENT_THRESHOLD:
                    row["adjacent_stories"].append(entry)
                else:
                    row["missed_stories"].append(entry)
            row["missed"] = len(row["missed_stories"])
        rows.append(row)
    rows.sort(key=lambda r: -r["missed"])
    return {
        "method_caveat": OMISSION_CAVEAT,
        "min_outlets_for_broad": min_outlets,
        "guards": {"min_sitemap_articles": OMISSION_MIN_SITEMAP,
                   "covered_threshold": COVERED_THRESHOLD,
                   "adjacent_threshold": ADJACENT_THRESHOLD,
                   "note": "thresholds uncalibrated until the Gate 2 audit"},
        "broad_stories": len(broad),
        "no_sitemap_no_claims": no_sitemap,
        "by_outlet": rows,
        "one_side_only": [{
            "story_id": s["story_id"], "label": s["label"][:160],
            "side_present": s["one_side_only"], "lean_spread": s["lean_spread"],
            "n_outlets": s["n_outlets"],
        } for s in stories if s["one_side_only"]],
    }


def universe_crosscheck(stories: list, days: int,
                        threshold: float = UNIVERSE_THRESHOLD) -> dict:
    """Wikipedia Current Events vs. observed story centroids: which events in
    the editorial universe show no matching story in ANY sampled feed?
    EXPERIMENTAL until calibrated (Gate 2): similarity between an event
    description and a headline is a different register pair than
    headline<->headline, so the threshold is conservative and every claim
    carries its nearest-story evidence for manual checking."""
    import numpy as np
    from matcher import embed_texts
    if not UNIVERSE_DIR.exists():
        return {"events": 0, "note": "no universe snapshots captured yet"}
    cutoff = (_now_utc() - timedelta(days=days)).date().isoformat()
    events = []
    days_used = []
    for p in sorted(UNIVERSE_DIR.glob("*.json")):
        try:
            u = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if u.get("date", "") >= cutoff:
            events.extend(u.get("current_events", []))
            days_used.append(u.get("date", ""))
    events = sorted(set(events))
    if not events or not stories:
        return {"events": len(events), "days": days_used,
                "note": "nothing to cross-check yet"}
    evecs = np.asarray(embed_texts(events), dtype=np.float32)
    cents = np.vstack([s_raw["centroid"] for s_raw in stories])
    sims = evecs @ cents.T          # events x stories
    out_uncovered = []
    covered = 0
    for i, ev in enumerate(events):
        j = int(sims[i].argmax())
        best = float(sims[i][j])
        if best >= threshold:
            covered += 1
        else:
            out_uncovered.append({"event": ev, "best_sim": round(best, 3)})
    return {
        "method_caveat": "Experimental mapping (event-description register vs "
                         "headline register); threshold uncalibrated until the "
                         "Gate 2 audit. Treat as leads, not claims.",
        "days": days_used, "events": len(events),
        "covered_by_some_story": covered,
        "threshold": threshold,
        "uncovered_by_roster": out_uncovered,
    }


def build_report(days: int, threshold: float, min_outlets: int,
                 save: bool = True) -> dict:
    roster = load_roster()
    leans = resolve_leans(roster)
    snaps = load_snapshots(days)
    if not snaps:
        print("[report] no snapshots in window — run --capture first",
              file=sys.stderr)
        return {}
    items_by_outlet, stale_dropped = aggregate_items(snaps, days)
    raw_stories, vecs_by_outlet = cluster_stories(items_by_outlet, threshold=threshold)
    stories = describe_stories(raw_stories, leans, roster)
    try:
        from sitemaps import load_sitemap_titles
        sitemap_titles = load_sitemap_titles(days)
    except Exception:  # noqa: BLE001 — absent module/data = no claims, not a crash
        sitemap_titles = {}
    if not sitemap_titles:
        print("[report] no sitemap captures in window — omission claims "
              "fully suppressed (run sitemaps.py --capture)", file=sys.stderr)
    n_traced = attach_traces(stories)
    if n_traced:
        print(f"[report] {n_traced} stories matched an already-traced "
              f"narrative (embedding-stage leads)", file=sys.stderr)
    end = _now_utc()
    report = {
        "is_agenda_report": True,
        "report_id": hashlib.sha256(
            f"{end.date().isoformat()}|{days}|{len(snaps)}".encode()
        ).hexdigest()[:12],
        "generated_at": end.isoformat(),
        "period": {"days": days,
                   "start": (end - timedelta(days=days)).isoformat(),
                   "end": end.isoformat()},
        "captures": [{"file": s["_file"], "captured_at": s["captured_at"],
                      "outlets_ok": s.get("outlets_ok", 0),
                      "outlets_failed": s.get("outlets_failed", 0)}
                     for s in snaps],
        "roster": {
            "news": sum(1 for o in roster["outlets"] if o["stream"] == "news"),
            "factcheck": sum(1 for o in roster["outlets"] if o["stream"] == "factcheck"),
            "leans": leans,
            "lean_source": "AllSides (via bias_db snapshot); unrated = no "
                           "attributable rating, never guessed",
        },
        "totals": {
            "captures": len(snaps),
            "unique_items": sum(len(v) for v in items_by_outlet.values()),
            "stories": len(stories),
            "story_threshold": threshold,
            # items predating the window, dropped per outlet (zombie-feed
            # guard); an outlet with nothing left is 'unsampled', never
            # 'omitted everything'
            "stale_items_dropped": stale_dropped,
        },
        "sitemap_coverage": {k: len(v) for k, v in sorted(sitemap_titles.items())},
        "stories": [{k: v for k, v in s.items() if k != "_centroid"}
                    for s in stories[:200]],
        "attention": attention_distributions(stories, items_by_outlet, roster),
        "omissions": omission_report(stories, items_by_outlet, roster, leans,
                                     sitemap_titles, min_outlets=min_outlets),
        "universe": universe_crosscheck(raw_stories, days),
    }
    if save:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / f"{end.date().isoformat()}_{days}d.json"
        path.write_text(json.dumps(report, indent=1, ensure_ascii=False),
                        encoding="utf-8")
        print(f"[report] {len(stories)} stories from "
              f"{report['totals']['unique_items']} items across {len(snaps)} "
              f"captures -> {path}", file=sys.stderr)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_roster():
    roster = load_roster()
    leans = resolve_leans(roster)
    buckets = {}
    print(f"{'key':<20} {'stream':<10} {'country':<8} lean")
    for o in roster["outlets"]:
        b = leans[o["key"]] or ("international" if o["country"] != "US" else "UNRATED")
        if o["stream"] == "news":
            buckets[b] = buckets.get(b, 0) + 1
        print(f"{o['key']:<20} {o['stream']:<10} {o['country']:<8} {b}")
    print("\nnews-outlet spectrum balance (AllSides via bias_db):")
    for b in ("left", "lean-left", "center", "lean-right", "right",
              "international", "UNRATED"):
        if buckets.get(b):
            print(f"  {b:<14} {buckets[b]}")


def _cmd_stories(days: int, threshold: float, show: int, min_outlets: int):
    roster = load_roster()
    leans = resolve_leans(roster)
    snaps = load_snapshots(days)
    if not snaps:
        print("[stories] no snapshots in window — run --capture first",
              file=sys.stderr)
        return
    items_by_outlet, stale_dropped = aggregate_items(snaps, days)
    if stale_dropped:
        print(f"[stories] stale items dropped (pre-window): {stale_dropped}",
              file=sys.stderr)
    raw, _ = cluster_stories(items_by_outlet, threshold=threshold)
    stories = describe_stories(raw, leans, roster)
    write_topics(stories, days, min_outlets=min_outlets)
    for s in stories[:show]:
        sp = s["lean_spread"]
        print(f"\n[{s['n_outlets']:>2} outlets / {s['n_items']:>3} items] "
              f"L{sp['left'] + sp['lean-left']} C{sp['center']} "
              f"R{sp['right'] + sp['lean-right']} intl{sp['international']}"
              + (f"  ** {s['one_side_only']}" if s["one_side_only"] else "")
              + (f"  [fact-checked x{s['factcheck_attention']}]"
                 if s["factcheck_attention"] else ""))
        print(f"  {s['label']}")
        for c in s["carriers"][:6]:
            print(f"    - {c['name']:<28} x{c['items']} (best pos {c['best_position']})")


def main():
    p = argparse.ArgumentParser(
        description="Tributary agenda layer: RSS prominence capture, story "
                    "clustering, attention distributions, omission report.")
    p.add_argument("--capture", action="store_true",
                   help="snapshot all roster feeds now")
    p.add_argument("--dry-run", action="store_true",
                   help="with --capture: fetch + report health, don't save")
    p.add_argument("--universe", action="store_true",
                   help="snapshot today's event universe via discover.py")
    p.add_argument("--date", default="",
                   help="with --universe: YYYY-MM-DD (default: yesterday UTC, "
                        "the last completed day)")
    p.add_argument("--roster", action="store_true",
                   help="roster health: resolved leans + spectrum balance")
    p.add_argument("--stories", action="store_true",
                   help="cluster the period's headlines into stories; write topics file")
    p.add_argument("--report", action="store_true",
                   help="full agenda report JSON (stories + attention + omissions)")
    p.add_argument("--days", type=int, default=7,
                   help="capture window in days for --stories/--report (default 7)")
    p.add_argument("--threshold", type=float, default=STORY_THRESHOLD,
                   help=f"story-join similarity threshold (default {STORY_THRESHOLD})")
    p.add_argument("--min-outlets", type=int, default=8,
                   help="distinct outlets for a story to count as broad "
                        "in the omission report (default 8)")
    p.add_argument("--show", type=int, default=25,
                   help="with --stories: print the top N stories")
    args = p.parse_args()

    if args.capture:
        capture(save=not args.dry_run)
    elif args.universe:
        d = date.fromisoformat(args.date) if args.date else None
        capture_universe(d)
    elif args.roster:
        _cmd_roster()
    elif args.stories:
        _cmd_stories(args.days, args.threshold, args.show,
                     min_outlets=3)
    elif args.report:
        build_report(args.days, args.threshold, args.min_outlets)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
