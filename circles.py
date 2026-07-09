"""
Tributary circle layer (Phase 1a)
=================================
Adds CIRCLES — epistemic communities — to the amplification graph as
first-class nodes, and rolls the existing multi-frame pipeline output up
into a per-event, per-circle framing frequency table.

Graph shape (extends the actors.py actor↔narrative substrate):

    (:Source news Actor)-[:MEMBER_OF {confidence, basis}]->(:Circle)

  - Circle nodes: v0 ships TWO (left-leaning / right-leaning), but nothing
    below is hard-coded to two — membership derivation iterates the CIRCLES
    table, so independent/international circles are an added definition,
    not a refactor.
  - MEMBER_OF edges are SEEDED from the local AllSides snapshot via bias_db
    (basis: "allsides_snapshot"), so the provenance of the labels themselves
    is kept: every edge records the verbatim AllSides label, AllSides' own
    confidence, how the outlet matched, and the snapshot date. Crude is fine
    for v0; `basis` is what lets a better membership source replace this one
    later without a schema change.
  - The news-media gate from coverage_lean applies: only news-type actors
    matched to News-Media entities get membership. Political/social/
    institutional carriers are actors IN the story, not press coverage of
    it. Center-rated outlets are members of NO circle (reported, not
    dropped); unrated outlets are an honest coverage gap.

The rollup (framing_rollup) is aggregation over existing analysis output,
not new extraction: per (framing × circle) it reports how many of the
circle's member outlets carried the framing (n_pieces of n_total, where a
"piece" is a distinct member outlet carrying the framing as a PRIMARY
source — relays are texture, counted separately), stance counts, and quote
receipts — every representative quote carries its URL + archive link +
date. Rows where n_pieces = 0 are kept: what a circle never touched is
signal, not noise.

Recognition rule (METHODOLOGY.md, v0 proxy): a circle's rows quote ONLY
that circle's own sources — enforced structurally here, because
representative quotes are drawn exclusively from the circle's member
outlets' carrier excerpts.

This adjudicates nothing: it counts who carried what, with receipts.

CLI:
    python circles.py events/<id>.json          # rollup for one event
    python circles.py --members events/         # membership across a corpus
    python circles.py --sweep events/           # calibration table over a corpus
    python circles.py --backfill events/        # store circle_rollup on each JSON
    python circles.py --archive events/         # wayback links for receipt URLs
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from actors import ActorRegistry
from bias_db import BiasDB, _domain_main_label, _norm_name


@dataclass(frozen=True)
class Circle:
    """A first-class epistemic-community node in the amplification graph."""
    circle_id: str                   # stable slug ('left-leaning')
    name: str
    description: str
    buckets: tuple                   # AllSides buckets that seed membership (v0 rule)
    basis: str = "allsides_snapshot"

    @property
    def key(self) -> str:
        return f"circle:{self.circle_id}"

    def to_dict(self) -> dict:
        return {"key": self.key, "circle_id": self.circle_id, "name": self.name,
                "description": self.description, "basis": self.basis,
                "buckets": list(self.buckets)}


# The circle table. v0: two circles from the AllSides left/right axis; a new
# circle (independent / international / topic-endogenous) is a new row here —
# derivation code iterates this table and never assumes two.
CIRCLES = [
    Circle("left-leaning", "Left-leaning circle",
           "News outlets AllSides rates left or lean-left.",
           ("left", "lean-left")),
    Circle("right-leaning", "Right-leaning circle",
           "News outlets AllSides rates right or lean-right.",
           ("right", "lean-right")),
]
_BUCKET_TO_CIRCLE = {b: c for c in CIRCLES for b in c.buckets}

# Membership confidence is a documented v0 heuristic, kept auditable by
# storing both inputs verbatim on the edge: how the outlet name/domain
# matched the ratings DB × how confident the rater itself is in the rating.
_MATCH_CONF = {"alias": 0.95, "domain": 0.9, "name": 0.85, "contains": 0.6}
_RATING_CONF = {"High": 1.0, "Medium": 0.8, "Low or Initial Rating": 0.55, "NA": 0.5}


@dataclass
class MembershipEdge:
    """(:Source)-[:MEMBER_OF {confidence, basis}]->(:Circle)"""
    actor_key: str                   # graph node key (e.g. 'site:nytimes.com')
    actor_display: str
    domain: str                      # the actor's domain, for audit
    circle_id: str
    confidence: float                # v0: match confidence × rating confidence
    basis: str                       # 'allsides_snapshot' (or 'mbfc_snapshot')
    outlet: str                      # the RATED ENTITY matched, verbatim — the
                                     # audit anchor: 'this actor was taken to be
                                     # this AllSides entity'
    bucket: str                      # normalized lean bucket that placed it
    label: str                       # rater's label, verbatim
    rating_source: str               # 'allsides' | 'mbfc'
    rating_confidence: str           # rater's own confidence, verbatim
    matched_by: str                  # alias | domain | name | contains
    as_of: str                       # snapshot date of the ratings DB

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _identity_ok(actor, r) -> bool:
    """Membership is a graph fact, so the bar for 'this actor IS that rated
    entity' sits higher than coverage_lean's descriptive skew. Two failure
    classes found by corpus audit are rejected:

      - foreign-ccTLD brand collisions (nation.com.pk, The Nation of Pakistan,
        matching AllSides' US 'The Nation' by brand label) — a 2-letter-TLD
        domain must match via the curated alias file, nothing fuzzier;
      - wire-copy identity transfer (wfmz.com displaying 'Associated Press'
        because it carries AP copy, then matching AP by name) — a name/contains
        match for a domain-keyed actor must be CONSISTENT with the domain's own
        brand label, else the rating belongs to the wire, not the site.

    Better to miss a membership than to invent one."""
    if r.matched_by == "alias":                       # curated — trusted
        return True
    if actor.domain:
        tld = actor.domain.rsplit(".", 1)[-1]
        if len(tld) == 2:                             # foreign ccTLD: alias-only
            return False
        if r.matched_by in ("name", "contains"):
            lbl = "".join(ch for ch in _domain_main_label(actor.domain) if ch.isalnum())
            on = _norm_name(r.outlet)
            return bool(lbl and on and (lbl in on or on in lbl))
        return True                                   # matched_by == 'domain'
    # domain-less actor (bare name in source data): exact-name matches only
    return r.matched_by == "name"


def derive_membership(reg: ActorRegistry, db: BiasDB = None) -> dict:
    """Seed MEMBER_OF edges for a registry's news actors from the bias DB.
    Returns {"members": {actor_key: MembershipEdge}, "center": [...],
    "unrated": [...]} — center and unrated are kept for transparency: an
    outlet in neither circle is a fact about the seeding, not a data error."""
    db = db or BiasDB()
    members, center, unrated = {}, [], []
    for a in reg.actors.values():
        if a.actor_type != "news":       # news-media gate (see module doc)
            continue
        r = db.lookup(domain=a.domain, name=a.display_name, prefer_news=True)
        if r is None or not r.is_news_media or not _identity_ok(a, r):
            unrated.append({"actor_key": a.key, "display": a.display_name,
                            "domain": a.domain})
            continue
        circle = _BUCKET_TO_CIRCLE.get(r.bucket)
        if circle is None:               # center-rated: a member of NO circle
            center.append({"actor_key": a.key, "display": a.display_name,
                           "outlet": r.outlet, "bucket": r.bucket})
            continue
        conf = round(_MATCH_CONF.get(r.matched_by, 0.6)
                     * _RATING_CONF.get(r.confidence, 0.5), 2)
        members[a.key] = MembershipEdge(
            actor_key=a.key, actor_display=a.display_name, domain=a.domain,
            circle_id=circle.circle_id, confidence=conf,
            basis=f"{r.source}_snapshot", outlet=r.outlet, bucket=r.bucket,
            label=r.label, rating_source=r.source,
            rating_confidence=r.confidence, matched_by=r.matched_by,
            as_of=r.as_of)
    return {"members": members, "center": center, "unrated": unrated}


# stance ranking: an outlet's strongest stance on a framing wins (as in
# coalitions.py) — champion > oppose > mention
_RANK = {"champion": 3, "oppose": 2, "mention": 1}
_QUOTE_ORDER = {"champion": 0, "mention": 1, "oppose": 2}
MAX_QUOTES = 3


def framing_rollup(event: dict, db: BiasDB = None,
                   registry: ActorRegistry = None) -> dict:
    """The Phase 1a aggregation: per (framing × circle) frequency table with
    receipts, over the event's existing carriers. No new extraction."""
    db = db or BiasDB()
    reg = registry or ActorRegistry()
    if registry is None:
        reg.ingest_event(event)
        reg.finalize()
    m = derive_membership(reg, db)
    members = m["members"]

    # Counting unit: the RATED OUTLET (membership edge's matched entity), not
    # the actor key — two domains of one outlet (abcnews.com + go.com) must
    # not count as two pieces. Distinct-outlet discipline, as in coverage_lean.
    cells = {}       # (fid, cid) -> {"stance": {outlet: stance}, "edges": [edge]}
    relays = {}      # (fid, cid) -> set(outlet) relaying (secondary tier)
    covering = {}    # cid -> set(outlet) with >=1 PRIMARY framing edge
    cov_keys = {}    # cid -> set(actor_key) behind those outlets (for listing)
    fr_names = {f.get("framing_id", ""): f.get("name", "") for f in event.get("framings", [])}
    for e in reg.edges:
        if e.narrative_kind != "framing":
            continue
        edge = members.get(e.actor_key)
        if edge is None:
            continue
        key = (e.narrative_id, edge.circle_id)
        if e.tier == "secondary":
            relays.setdefault(key, set()).add(edge.outlet)
            continue
        covering.setdefault(edge.circle_id, set()).add(edge.outlet)
        cov_keys.setdefault(edge.circle_id, set()).add(e.actor_key)
        cell = cells.setdefault(key, {"stance": {}, "edges": []})
        cur = cell["stance"].get(edge.outlet)
        if cur is None or _RANK[e.stance] > _RANK[cur]:
            cell["stance"][edge.outlet] = e.stance
        cell["edges"].append(e)

    def _quotes(cell) -> list:
        """Receipts: representative quotes, ONLY from this circle's member
        outlets (the structural recognition rule). Champions first, then
        prefer quotes that carry an archive link and a date."""
        cand = [e for e in cell["edges"] if (e.evidence or "").strip()]
        cand.sort(key=lambda e: (_QUOTE_ORDER.get(e.stance, 1),
                                 not e.archive_url, not e.date))
        out, seen = [], set()
        for e in cand:
            m_edge = members[e.actor_key]
            if m_edge.outlet in seen:    # one quote per outlet per row
                continue
            seen.add(m_edge.outlet)
            # display from the membership edge: it was resolved AFTER
            # finalize(), so it carries the canonical name (edge.actor_display
            # was captured before name resolution and can be a raw domain)
            out.append({"outlet": m_edge.actor_display, "stance": e.stance,
                        "quote": e.evidence.strip(), "url": e.source_url,
                        "archive_url": e.archive_url, "date": e.date})
            if len(out) >= MAX_QUOTES:
                break
        return out

    rows = []
    for fid, fname in fr_names.items():
        for c in CIRCLES:
            n_total = len(covering.get(c.circle_id, ()))
            cell = cells.get((fid, c.circle_id), {"stance": {}, "edges": []})
            stances = {"champion": 0, "mention": 0, "oppose": 0}
            for st in cell["stance"].values():
                stances[st] += 1
            n_pieces = len(cell["stance"])
            rows.append({
                "framing_id": fid, "framing": fname, "circle_id": c.circle_id,
                # a "piece" = a distinct member outlet carrying the framing as
                # a PRIMARY source; n_total = the circle's distinct member
                # outlets carrying ANY framing of this event (same basis, so
                # the a/b rate is internally consistent)
                "n_pieces": n_pieces, "n_total": n_total,
                "rate": round(n_pieces / n_total, 3) if n_total else None,
                "stances": stances,
                "n_relays": len(relays.get((fid, c.circle_id), set()) - set(cell["stance"])),
                "representative_quotes": _quotes(cell),
            })

    circles_out = []
    for c in CIRCLES:
        circles_out.append({
            **c.to_dict(),
            # counted in distinct rated outlets; listed per actor key so the
            # MEMBER_OF edges (and their match provenance) stay auditable
            "n_covering": len(covering.get(c.circle_id, ())),
            "members_covering": sorted(
                (members[k].to_dict() for k in cov_keys.get(c.circle_id, ())),
                key=lambda d: (-d["confidence"], d["outlet"].lower())),
        })

    thin = [c["circle_id"] for c in circles_out if c["n_covering"] < 3]
    return {
        "analysis_id": event.get("analysis_id", ""),
        "event": event.get("event", "")[:160],
        "n_framings": len(fr_names),
        "circles": circles_out,
        # transparency: rated-center and unrated news outlets belong to no
        # circle; they are the seeding's honest remainder, not noise
        "unassigned": {"center": m["center"], "unrated": m["unrated"]},
        "rows": rows,
        "note": ("" if not thin else
                 f"too few rated member outlets in {', '.join(thin)} "
                 f"(<3 covering this event) — rates for that circle are not "
                 f"meaningful, read the raw counts and quotes only"),
        "membership_basis": {
            "basis": "allsides_snapshot",
            "as_of": db.meta.get("as_of", ""),
            "source_note": "Ratings are AllSides' — Tributary only aggregates them.",
            "attribution_url": db.meta.get("attribution_url", ""),
        },
    }


# ---- receipt archiving ---------------------------------------------------
def _wayback_lookup(url: str, timeout: float = 15.0) -> str:
    """Closest EXISTING Wayback snapshot for `url`, or "". Lookup-only — this
    never creates a snapshot, so a miss is an honest archive gap, not an error
    (Save Page Now for the misses is a manual step at publish time)."""
    if not url:
        return ""
    try:
        q = urllib.parse.urlencode({"url": url})
        req = urllib.request.Request(
            f"https://archive.org/wayback/available?{q}",
            headers={"User-Agent": "tributary-tracer/1.0 (receipt archiving)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        closest = (data.get("archived_snapshots") or {}).get("closest") or {}
        if closest.get("available"):
            return str(closest.get("url", ""))
    except Exception:  # noqa: BLE001 — network best-effort; gap stays honest
        pass
    return ""


def archive_receipts(paths: list, db: BiasDB, delay: float = 0.4) -> None:
    """For each event, look up Wayback snapshots for every representative-
    quote URL in its circle rollup, write them back onto the MATCHING CARRIERS
    (so the viewer, coverage_lean and future rollups all benefit), and refresh
    the stored circle_rollup. One in-run cache across events; polite delay
    between archive.org calls."""
    cache = {}
    n_url = n_hit = 0
    for path in paths:
        try:
            ev = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not ev.get("framings"):
            continue
        rep = framing_rollup(ev, db=db)
        urls = {q["url"] for r in rep["rows"] for q in r["representative_quotes"]
                if q["url"] and not q["archive_url"]}
        changed = False
        for u in sorted(urls):
            if u not in cache:
                cache[u] = _wayback_lookup(u)
                n_url += 1
                time.sleep(delay)
            if not cache[u]:
                continue
            for fr in ev.get("framings", []):
                for c in fr.get("carriers", []):
                    if c.get("url") == u and not c.get("archive_url"):
                        c["archive_url"] = cache[u]
                        changed = True
                        n_hit += 1
        # refresh the stored rollup either way (carriers may have changed)
        ev["circle_rollup"] = framing_rollup(ev, db=db)
        path.write_text(json.dumps(ev, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8")
        if changed:
            print(f"  [archive] {path.name}: "
                  f"{sum(1 for fr in ev['framings'] for c in fr['carriers'] if c.get('archive_url'))}"
                  f" carriers now archived", file=sys.stderr)
    print(f"[archive] looked up {n_url} URLs; wrote {n_hit} archive links "
          f"(misses are an honest gap — no snapshot exists yet)", file=sys.stderr)


# ---- CLI ----------------------------------------------------------------
def _load_dir(d: str):
    for p in sorted(Path(d).glob("*.json")):
        if p.name in ("index.json", "corpus_index.json"):
            continue
        try:
            yield p, json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue


def _print_rollup(rep: dict):
    print(f"\nCircle rollup — {rep['event']}")
    mb = rep["membership_basis"]
    cov = {c["circle_id"]: c["n_covering"] for c in rep["circles"]}
    un = rep["unassigned"]
    print(f"  member outlets covering event: "
          + "  ".join(f"{cid} {n}" for cid, n in cov.items())
          + f"  |  center {len(un['center'])}  unrated {len(un['unrated'])}"
          f"  (news-media gate; AllSides {mb['as_of']})")
    if rep["note"]:
        print(f"  NOTE: {rep['note']}")
    ids = [c["circle_id"] for c in rep["circles"]]
    print(f"\n  {'framing':<44}" + "".join(f"{cid:>16}" for cid in ids))
    by_fr = {}
    for r in rep["rows"]:
        by_fr.setdefault((r["framing_id"], r["framing"]), {})[r["circle_id"]] = r
    for (fid, fname), per in by_fr.items():
        line = f"  {fname[:42]:<44}"
        for cid in ids:
            r = per.get(cid)
            line += f"{r['n_pieces']}/{r['n_total']}".rjust(16) if r else " " * 16
        print(line)
    print("\n  receipts (per circle, that circle's own quotes only):")
    for (fid, fname), per in by_fr.items():
        qs = [(cid, q) for cid in ids for q in per.get(cid, {}).get("representative_quotes", [])]
        if not qs:
            continue
        print(f"  · {fname[:60]}")
        for cid, q in qs[:4]:
            arch = " [archived]" if q["archive_url"] else ""
            print(f"      {cid:<14} {q['outlet'][:24]:<24} ({q['stance']}) "
                  f"\"{q['quote'][:70]}\"{arch}")
    if un["unrated"]:
        ex = ", ".join(u["display"] for u in un["unrated"][:6])
        print(f"\n  unrated news outlets (no circle, honest gap): {ex}")


def main():
    p = argparse.ArgumentParser(description="Circle membership + per-event framing rollup.")
    p.add_argument("event_json", nargs="?")
    p.add_argument("--members", default="", help="Show circle membership across a corpus dir.")
    p.add_argument("--sweep", default="", help="Calibration table over a corpus dir.")
    p.add_argument("--backfill", default="", help="Store circle_rollup on each event JSON.")
    p.add_argument("--archive", default="",
                   help="Event JSON or directory: add Wayback links to receipt "
                        "URLs (lookup-only) and refresh stored rollups.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    db = BiasDB()

    if args.archive:
        target = Path(args.archive)
        paths = ([p2 for p2, _ in _load_dir(args.archive)] if target.is_dir()
                 else [target])
        archive_receipts(paths, db)
        return

    if args.members:
        reg = ActorRegistry()
        for _, ev in _load_dir(args.members):
            if ev.get("framings"):
                reg.ingest_event(ev)
        reg.finalize()
        m = derive_membership(reg, db)
        by_circle = {}
        for e in m["members"].values():
            by_circle.setdefault(e.circle_id, []).append(e)
        print(f"Circle membership over corpus ({len(reg.actors)} actors; "
              f"AllSides {db.meta.get('as_of','')})")
        for c in CIRCLES:
            es = sorted(by_circle.get(c.circle_id, []),
                        key=lambda e: (-e.confidence, e.actor_display.lower()))
            print(f"\n  {c.name} — {len(es)} member actors "
                  f"({len({e.outlet for e in es})} distinct rated outlets)")
            for e in es:
                print(f"    {e.confidence:.2f}  {(e.domain or e.actor_display)[:26]:<26} "
                      f"= {e.outlet[:30]:<30} {e.bucket:<10} "
                      f"({e.rating_source}/{e.matched_by}, rater: {e.rating_confidence})")
        print(f"\n  center (no circle): {len(m['center'])} — "
              + ", ".join(x["display"] for x in m["center"][:10]))
        print(f"  unrated news outlets (gap): {len(m['unrated'])} — "
              + ", ".join(x["display"] for x in m["unrated"][:10]))
        return

    if args.sweep:
        rows = []
        for _, ev in _load_dir(args.sweep):
            if not ev.get("framings"):
                continue
            rep = framing_rollup(ev, db=db)
            cov = {c["circle_id"]: c["n_covering"] for c in rep["circles"]}
            per_fr = {}
            for r in rep["rows"]:
                if r["n_pieces"] > 0:
                    per_fr.setdefault(r["framing_id"], set()).add(r["circle_id"])
            both = sum(1 for cids in per_fr.values() if len(cids) == len(CIRCLES))
            rows.append((cov.get("left-leaning", 0), cov.get("right-leaning", 0),
                         both, rep["n_framings"], ev.get("event", "")[:52]))
        rows.sort(key=lambda x: -x[2])
        print(f"{'left':>5} {'right':>5} {'both':>5} {'frms':>5}  event")
        for l, r, b, nf, name in rows:
            print(f"{l:>5} {r:>5} {b:>5} {nf:>5}  {name}")
        print("\n(both = framings carried by member outlets of every circle — "
              "raw candidate count, NOT yet the Phase 1b intersection: no "
              "substantiveness filter, stance not yet accounted for)")
        return

    if args.backfill:
        done = 0
        for path, ev in _load_dir(args.backfill):
            if not ev.get("framings"):
                continue
            ev["circle_rollup"] = framing_rollup(ev, db=db)
            path.write_text(json.dumps(ev, indent=2, ensure_ascii=False, default=str),
                            encoding="utf-8")
            done += 1
        print(f"[backfill] stored circle_rollup on {done} event JSONs. "
              f"Basis: AllSides {db.meta.get('as_of','')}.", file=sys.stderr)
        return

    if not args.event_json:
        p.error("give an event JSON, or --members/--sweep/--backfill <dir>")
    ev = json.loads(Path(args.event_json).read_text(encoding="utf-8"))
    rep = framing_rollup(ev, db=db)
    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        _print_rollup(rep)


if __name__ == "__main__":
    main()
