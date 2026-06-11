"""
Tributary source-bias lookup + event coverage-lean
==================================================

Two layers, kept deliberately separate (see the README "coverage lean" note):

  1. THE RATING — outlet -> Left/Lean-Left/Center/Lean-Right/Right. This is
     AllSides' (or MBFC's) work; we only look it up. Loaded from a local
     snapshot (data/allsides_ratings.json, built by gen_bias_data.py), so
     there's no API on the hot path — just a dict.
  2. THE COVERAGE — which outlets actually carried a story. Tributary already
     discovers this: the carriers in an EventAnalysis. coverage_lean() tags
     each carrier's outlet with its bias and aggregates.

The product of the two is a Ground-News-style coverage distribution
("coverage here skews left, little right-of-center") derived from Tributary's
own output. It is a SKEW SIGNAL over the carriers we found — a sample, not an
exhaustive census — and it adjudicates nothing; it describes who covered what.

Neutrality: every lean is attributable to AllSides/MBFC. We never invent a
rating. Outlets we can't resolve are reported as coverage gaps, not guessed.

CLI:
    python bias_db.py events/<id>.json        # coverage-lean for one event
    python bias_db.py --lookup nytimes.com    # what's one domain rated?
"""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

_DATA = Path(__file__).resolve().parent / "data"

# AllSides' five buckets, ordered left -> right, with our symmetric numeric.
_BUCKETS = ["left", "lean-left", "center", "lean-right", "right"]
_LABEL_TO_BUCKET = {
    "left": "left", "left-center": "lean-left", "center": "center",
    "right-center": "lean-right", "right": "right",
}
# host-prefix noise to strip when reducing a URL to a registrable-ish domain
_HOST_PREFIXES = ("www.", "m.", "mobile.", "amp.", "edition.", "us.", "uk.")
# desk/section suffixes AllSides appends; stripped when normalizing a name
_DESK_BITS = (" - news", " - opinion", " - editorial", " online news",
              " (web news)", " (online news)", " editorial", " opinion",
              " (news)", " news")


def normalize_domain(url_or_host: str) -> str:
    """Reduce a URL or host to a lowercased registrable-ish domain.
    https://www.nytimes.com/2026/... -> nytimes.com ; x.com -> x.com"""
    if not url_or_host:
        return ""
    s = url_or_host.strip().lower()
    host = urlsplit(s if "//" in s else "//" + s).netloc or s
    host = host.split("@")[-1].split(":")[0]            # drop creds/port
    for p in _HOST_PREFIXES:
        if host.startswith(p):
            host = host[len(p):]
    return host.strip("/").strip()


def _domain_main_label(domain: str) -> str:
    """nytimes.com -> nytimes ; bbc.co.uk -> bbc — the brandable label."""
    if not domain:
        return ""
    parts = domain.split(".")
    # drop a trailing ccTLD+sld like co.uk / com.au
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "gov", "net", "ac"):
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


def _norm_name(name: str) -> str:
    """Collapse an outlet/carrier name to a match key: lowercase, drop a
    parenthetical author, drop desk suffixes, drop a leading 'the', keep
    alphanumerics only. 'The New York Times (Katie Glueck)' -> 'newyorktimes'."""
    if not name:
        return ""
    s = name.lower().strip()
    if "(" in s:                                   # 'reason magazine (robby soave)'
        s = s[:s.index("(")].strip()
    if " / " in s:                                 # 'wbur / associated press' -> first
        s = s.split(" / ")[0].strip()
    for bit in _DESK_BITS:
        if s.endswith(bit):
            s = s[: -len(bit)].strip()
    if s.startswith("the "):
        s = s[4:]
    return "".join(ch for ch in s if ch.isalnum())


@dataclass
class BiasRating:
    outlet: str                 # the rated entity's name (as in the source DB)
    label: str                  # source label, verbatim (e.g. 'left-center')
    bucket: str                 # normalized: left/lean-left/center/lean-right/right
    lean_num: int               # -2..+2
    source: str                 # 'allsides' | 'mbfc'
    confidence: str = ""
    as_of: str = ""
    attribution_url: str = ""
    matched_by: str = ""        # 'alias' | 'domain' | 'name' | 'contains' — for transparency
    outlet_type: str = ""       # AllSides entity type: 'News Media' / 'Author' / 'Think Tank / Policy Group'

    @property
    def is_news_media(self) -> bool:
        return self.outlet_type.lower().startswith("news")

    def to_dict(self) -> dict:
        return {"outlet": self.outlet, "label": self.label, "bucket": self.bucket,
                "lean_num": self.lean_num, "source": self.source,
                "confidence": self.confidence, "matched_by": self.matched_by,
                "outlet_type": self.outlet_type}


class BiasDB:
    """Loads the local AllSides snapshot (+ optional MBFC) and resolves an
    outlet (by domain or name) to a BiasRating. AllSides is the lean-of-record;
    MBFC, if present, fills gaps AllSides doesn't cover."""

    def __init__(self, data_dir: Path = _DATA):
        self.data_dir = Path(data_dir)
        self._by_outlet = {}     # exact outlet name -> entry dict (+source)
        self._by_norm = {}       # normalized name -> entry dict (lean-bearing preferred)
        self._aliases = {}       # domain -> exact outlet name
        self._meta = {}
        self._load()

    # ---- loading -------------------------------------------------------
    def _ingest(self, outlets: list, source: str, meta: dict):
        as_of = meta.get("as_of", "")
        attr = meta.get("attribution_url", "")
        for o in outlets:
            name = o.get("outlet", "")
            label = o.get("rating", "")
            bucket = _LABEL_TO_BUCKET.get(label)
            if not name or bucket is None:        # skip non-lean buckets (allsides/mixed/NA)
                continue
            entry = {
                "outlet": name, "label": label, "bucket": bucket,
                "lean_num": o.get("lean_num", 0), "source": source,
                "confidence": o.get("confidence", ""), "as_of": as_of,
                "attribution_url": attr, "type": o.get("type", ""),
            }
            # exact-name index (first source wins; AllSides loaded first)
            self._by_outlet.setdefault(name, entry)
            # normalized index: prefer a News-desk / News-Media entry over Opinion
            nk = _norm_name(name)
            if nk:
                cur = self._by_norm.get(nk)
                if cur is None or _prefer(entry, cur):
                    self._by_norm[nk] = entry

    def _load(self):
        allsides = self.data_dir / "allsides_ratings.json"
        if not allsides.exists():
            print(f"[bias_db] missing {allsides} — run: python gen_bias_data.py",
                  file=sys.stderr)
        else:
            d = json.loads(allsides.read_text(encoding="utf-8"))
            self._meta = d.get("_meta", {})
            self._ingest(d.get("outlets", []), "allsides", self._meta)
        # optional MBFC fallback (same shape; gaps only)
        mbfc = self.data_dir / "mbfc_ratings.json"
        if mbfc.exists():
            d = json.loads(mbfc.read_text(encoding="utf-8"))
            self._ingest(d.get("outlets", []), "mbfc", d.get("_meta", {}))
        aliases = self.data_dir / "domain_aliases.json"
        if aliases.exists():
            self._aliases = json.loads(aliases.read_text(encoding="utf-8")).get("aliases", {})

    # ---- lookup --------------------------------------------------------
    def _rating(self, entry: dict, matched_by: str) -> BiasRating:
        return BiasRating(
            outlet=entry["outlet"], label=entry["label"], bucket=entry["bucket"],
            lean_num=int(entry["lean_num"]), source=entry["source"],
            confidence=entry.get("confidence", ""), as_of=entry.get("as_of", ""),
            attribution_url=entry.get("attribution_url", ""), matched_by=matched_by,
            outlet_type=entry.get("type", ""))

    def lookup(self, domain: str = "", name: str = "", prefer_news: bool = False):
        """Resolve to a BiasRating, or None if we can't (an honest gap).
        Tries, in order: domain alias -> domain brand-label -> exact name ->
        normalized name -> name-contains. With prefer_news, the fuzzy 'contains'
        pass only matches News-Media entities (so a news outlet isn't shadowed
        by a like-named author/think-tank)."""
        dom = normalize_domain(domain)
        if dom:
            if dom in self._aliases:
                e = self._by_outlet.get(self._aliases[dom])
                if e:
                    return self._rating(e, "alias")
            label = _domain_main_label(dom)
            if label:
                # 'the' already stripped from norm keys; try both forms
                for key in (label, label.lstrip("the") if label.startswith("the") else label):
                    if key in self._by_norm:
                        return self._rating(self._by_norm[key], "domain")
        if name:
            if name in self._by_outlet:
                return self._rating(self._by_outlet[name], "name")
            nk = _norm_name(name)
            if nk and nk in self._by_norm:
                return self._rating(self._by_norm[nk], "name")
            if nk and len(nk) >= 5:                # cautious contains, avoid short collisions
                for k, e in self._by_norm.items():
                    if prefer_news and not e.get("type", "").lower().startswith("news"):
                        continue
                    if len(k) >= 5 and (k in nk or nk in k) and _contains_ok(k, nk):
                        return self._rating(e, "contains")
        return None

    @property
    def meta(self) -> dict:
        return self._meta


# Generic desk/edition bits: when one normalized name extends another AT THE
# END, the extension must be one of these for the match to count as the SAME
# outlet ('foxnews' -> 'foxnewsdigital').
_CONTAINS_EXTRAS = {"news", "online", "onlinenews", "digital", "com", "media",
                    "magazine", "editorial", "opinion", "politics", "staff",
                    "us", "uk"}


def _contains_ok(k: str, nk: str) -> bool:
    """Guard for the fuzzy contains pass, after a corpus audit found 46 of 51
    published contains-matches were WRONG (Iran International -> The Nation,
    Kyiv Independent -> The Independent, Oman Observer -> The Observer (NY),
    National Post -> The Nation...). Containment counts as identity only when
    the shorter name is a PREFIX of the longer and the leftover is a generic
    desk/edition suffix or a plural 's'. A leading extension is a different
    brand (NewsNation is not The Nation), and 'nation'+'al...' is a different
    word entirely — both rejected."""
    short, long_ = (k, nk) if len(k) <= len(nk) else (nk, k)
    if short == long_:
        return True
    if long_.startswith(short):
        rest = long_[len(short):]
        return rest in _CONTAINS_EXTRAS or rest == "s"
    return False


def _prefer(a: dict, b: dict) -> bool:
    """When two AllSides entries share a normalized name (e.g. News vs
    Opinion desks), prefer the one that represents straight news coverage —
    that's what a 'carrier' of an event most often is."""
    def score(e):
        n = e["outlet"].lower()
        s = 0
        if e.get("type", "").lower().startswith("news"):   # News Media >> Author / Think Tank
            s += 4
        if "news" in n and "opinion" not in n and "editorial" not in n:
            s += 2
        if "opinion" in n or "editorial" in n:
            s -= 2
        if e.get("source") == "allsides":          # AllSides over MBFC on ties
            s += 1
        return s
    return score(a) > score(b)


# ---- event coverage-lean ----------------------------------------------
def coverage_lean(event: dict, db: BiasDB = None, news_only: bool = True) -> dict:
    """Aggregate the bias of an EventAnalysis's carriers into a coverage-lean
    distribution. Counts DISTINCT OUTLETS (not raw carrier rows) so an outlet
    cited under several framings isn't multi-counted.

    Returns a dict with the per-bucket distribution, a lean index, a plain
    skew descriptor, and — for transparency — the matched outlets and the
    unresolved ones (the coverage gaps)."""
    db = db or BiasDB()
    seen = {}            # domain-or-name key -> (BiasRating | None, display, is_news)
    eco = {t: 0 for t in ("news", "political", "social", "institutional", "academic", "other")}
    framings = event.get("framings", [])
    framings_with_vested = 0
    for fr in framings:
        fr_has_vested = False
        for c in fr.get("carriers", []):
            ct = (c.get("carrier_type") or "other").lower()
            eco[ct if ct in eco else "other"] += 1
            if ct in ("political", "institutional"):
                fr_has_vested = True
        if fr_has_vested:
            framings_with_vested += 1
    for fr in framings:
        for c in fr.get("carriers", []):
            ctype = (c.get("carrier_type") or "").lower()
            url = c.get("url", "")
            name = c.get("name", "")
            dom = normalize_domain(url)
            key = dom or _norm_name(name) or name
            if not key or key in seen:
                continue
            # A media coverage-lean counts NEWS OUTLETS only. Political/social/
            # institutional/academic carriers (officials, NGOs, think tanks,
            # courts) are actors in the story, not press coverage of it — and
            # AllSides rates some of them, so without this gate they'd leak in
            # and fabricate a skew. Require Tributary's 'news' tag AND a match
            # to a News-Media entity.
            is_news = (ctype == "news")
            rating = db.lookup(domain=url, name=name, prefer_news=True) if is_news else None
            seen[key] = (rating, name or dom, is_news, dom)

    counts = {b: 0 for b in _BUCKETS}
    matched, unmatched, nonnews = [], [], []
    for key, (rating, display, is_news, dom) in seen.items():
        if news_only and not is_news:
            nonnews.append(display)
            continue
        if rating is None or not rating.is_news_media:
            unmatched.append({"carrier": display, "domain": dom})
            continue
        counts[rating.bucket] += 1
        matched.append({"outlet": rating.outlet, "carrier": display,
                        "bucket": rating.bucket, "lean_num": rating.lean_num,
                        "source": rating.source, "matched_by": rating.matched_by})

    n = sum(counts.values())
    lean_index = round(sum(c["lean_num"] for c in matched) / n, 2) if n else None
    left_n = counts["left"] + counts["lean-left"]
    right_n = counts["right"] + counts["lean-right"]
    pct = {b: round(100 * counts[b] / n) for b in _BUCKETS} if n else {b: 0 for b in _BUCKETS}
    # How much of the news coverage we could actually rate. Low resolve (e.g.
    # international stories, whose outlets a US-centric DB doesn't carry) makes
    # any skew claim unreliable — so we gate the descriptor on it rather than
    # mistaking "couldn't resolve" for "one-sided".
    news_total = n + len(unmatched)
    resolve_rate = round(n / news_total, 2) if news_total else 0.0

    return {
        "event": event.get("event", "")[:160],
        "rated_outlets": n,
        "distribution": counts,
        "distribution_pct": pct,
        "left_of_center": left_n,
        "center": counts["center"],
        "right_of_center": right_n,
        "lean_index": lean_index,                  # -2 (all left) .. +2 (all right)
        "resolve_rate": resolve_rate,              # rated / (rated + unrated) news carriers
        "skew": _skew_descriptor(left_n, counts["center"], right_n, lean_index,
                                 n, resolve_rate),
        "coverage_gap": {                          # transparency: what we couldn't rate
            "unrated_news_carriers": len(unmatched),
            "examples": unmatched[:8],
        },
        "non_news_carriers": len(nonnews),
        "matched": sorted(matched, key=lambda m: m["lean_num"]),
        # Carrier ecosystem — texture on WHO is contesting the event, not a
        # polarization score. Many vested-interest (political/institutional)
        # carriers tracks event SCALE as much as contestedness (a big
        # geopolitical story pulls in IGOs/militaries/NGOs regardless), so read
        # it as a signal, not a verdict — strongest as a NEGATIVE one (few
        # vested interests -> likely uncontested). A real adversarial-spread
        # measure (vested actors on OPPOSING framings) is a later enhancement.
        "ecosystem": {
            "by_type": eco,
            "vested_interest": eco["political"] + eco["institutional"],
            "framings_with_vested": framings_with_vested,
            "n_framings": len(framings),
        },
        "source_note": db.meta.get("source_detail") or db.meta.get("source", ""),
        "as_of": db.meta.get("as_of", ""),
    }


def _skew_descriptor(left_n, center_n, right_n, lean_index,
                     rated_n=0, resolve_rate=0.0) -> str:
    """Neutral, descriptive label for the COVERAGE SAMPLE — not a truth claim.
    Gated on having enough rated coverage to say anything: too few rated
    outlets, or too low a resolve rate (common for international stories a
    US-centric bias DB doesn't cover), yields 'insufficient rated coverage'
    rather than a misleading one-sided/blindspot call."""
    total = left_n + center_n + right_n
    if total == 0:
        return "no rated coverage"
    if rated_n < 4 or resolve_rate < 0.4:
        return "insufficient rated coverage to judge skew (DB is US-centric; many carriers unrated)"
    if right_n == 0 and left_n > 0:
        return "one-sided: no right-of-center coverage found (possible blindspot)"
    if left_n == 0 and right_n > 0:
        return "one-sided: no left-of-center coverage found (possible blindspot)"
    if lean_index is None:
        return "mixed"
    if lean_index <= -0.8:
        return "skews left"
    if lean_index >= 0.8:
        return "skews right"
    return "balanced across the spectrum"


def _print_report(rep: dict):
    print(f"\nCoverage lean — {rep['event']}")
    print(f"  rated outlets: {rep['rated_outlets']} ({int(rep['resolve_rate']*100)}% of news "
          f"carriers)  |  lean index: {rep['lean_index']} (-2 all-left .. +2 all-right)")
    print(f"  skew: {rep['skew']}")
    bar = rep["distribution"]
    print(f"  L {bar['left']}  LL {bar['lean-left']}  C {bar['center']}  "
          f"LR {bar['lean-right']}  R {bar['right']}    "
          f"[left-of-center {rep['left_of_center']} / center {rep['center']} / "
          f"right-of-center {rep['right_of_center']}]")
    if rep["matched"]:
        print("  matched outlets:")
        for m in rep["matched"]:
            print(f"    {m['bucket']:>10}  {m['carrier'][:46]:<46}  "
                  f"({m['source']}/{m['matched_by']})")
    gap = rep["coverage_gap"]
    if gap["unrated_news_carriers"]:
        ex = ", ".join(g["domain"] or g["carrier"] for g in gap["examples"])
        print(f"  coverage gaps ({gap['unrated_news_carriers']} unrated news carriers): {ex}")
    if rep["non_news_carriers"]:
        print(f"  ({rep['non_news_carriers']} non-news carriers — political/social — excluded)")
    eco = rep.get("ecosystem", {})
    if eco:
        bt = eco["by_type"]
        print(f"  carrier ecosystem: news {bt['news']} / political {bt['political']} / "
              f"institutional {bt['institutional']} / social {bt['social']}  "
              f"(vested interest: {eco['vested_interest']}, across "
              f"{eco['framings_with_vested']}/{eco['n_framings']} framings — texture, not a score)")
    print(f"  source: AllSides ({rep['as_of']}) — ratings are AllSides', aggregated by Tributary")


def backfill(directory: str, db: BiasDB) -> tuple:
    """Inject a `coverage_lean` block into every event JSON in a directory that
    has framings. Lets an existing corpus gain the lean without re-analysis."""
    done = skipped = 0
    for path in sorted(Path(directory).glob("*.json")):
        if path.name == "corpus_index.json":
            continue
        try:
            ev = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            skipped += 1
            continue
        if not ev.get("framings"):
            skipped += 1
            continue
        ev["coverage_lean"] = coverage_lean(ev, db=db)
        path.write_text(json.dumps(ev, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8")
        done += 1
    return done, skipped


def main():
    p = argparse.ArgumentParser(description="Source-bias lookup + event coverage-lean.")
    p.add_argument("event_json", nargs="?", help="Path to an EventAnalysis JSON.")
    p.add_argument("--lookup", default="", help="Resolve a single domain or outlet name.")
    p.add_argument("--backfill", default="",
                   help="Inject coverage_lean into every event JSON in this directory.")
    p.add_argument("--include-nonnews", action="store_true",
                   help="Count political/social carriers too (default: news outlets only).")
    p.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = p.parse_args()

    db = BiasDB()
    if args.lookup:
        r = db.lookup(domain=args.lookup, name=args.lookup)
        print(json.dumps(r.to_dict() if r else {"result": "unrated (coverage gap)"}, indent=2))
        return
    if args.backfill:
        done, skipped = backfill(args.backfill, db)
        print(f"[backfill] added coverage_lean to {done} event JSONs "
              f"({skipped} skipped — no framings). Source: AllSides {db.meta.get('as_of','')}.",
              file=sys.stderr)
        return
    if not args.event_json:
        p.error("give an event JSON path, or --lookup <domain/name>")
    event = json.loads(Path(args.event_json).read_text(encoding="utf-8"))
    rep = coverage_lean(event, db=db, news_only=not args.include_nonnews)
    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        _print_report(rep)


if __name__ == "__main__":
    main()
