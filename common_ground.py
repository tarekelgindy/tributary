"""
Tributary common ground — the Phase 1b computations
===================================================
Tests the mission's central empirical claim against data we already have:
engagement-driven curation systematically buries agreement between circles.
Three computations plus one overlay (MISSION_PLAN 1b), all aggregation over
existing pipeline output — no new extraction, no new API spend by default.

  1. FRAMING INTERSECTION (per event) — framings carried substantively in
     EVERY circle, with per-circle rates ("framing F appears in a/b
     left-leaning pieces and c/d right-leaning pieces") and quotes from each
     circle's own sources. Substantive means ASSERTED (champion or mention)
     by at least one member outlet in each circle: a framing one circle only
     OPPOSES is contestation wearing the same clothes as agreement, and it
     is reported separately as `contested`, never as intersection — the
     honesty rule cuts both ways, and so does this filter.
  2. CONVERGENT CLAIMS (per event) — the same claim asserted by member
     outlets in more than one circle. Claim units are the carriers' own
     verbatim excerpts (receipts built in); the narrative-matcher's local
     embeddings score cross-circle pairs for free, and the SAME two-stage
     discipline as serving applies: an embedding-similar pair is only a
     CANDIDATE until the stage-2 judge (matcher's validated Haiku prompt,
     ~$0.001 for a whole event batch) confirms same-subject / same-blame /
     same-consequence. Without --confirm nothing is labeled convergent —
     Gate 1 showed embeddings alone cannot make the same-claim call.
  3. SHARED-BUT-BURIED (per capture week) — stories carried by rated US
     outlets in BOTH circles but feed-featured by NEITHER: the mirror image
     of agenda.py's one_side_only query, inverted. Same evidence bar (>= 2
     rated US outlets per side, the bar one_side_only uses for presence),
     same featuring definition (best feed position <= FEATURED_POSITION,
     persistence tracked via captures_seen). Transparency guard: outlets
     OUTSIDE the circles (center / unrated / international) that did
     feature the story are listed on the row, so "buried" can never be
     ambushed by "but Reuters had it at #2".
  4. CLAIM-AGE OVERLAY — trace-engine first-attestation dates attached to
     intersecting framings, so a long-lived framing is distinguishable from
     a recently seeded one. Attachment is honest about its basis: a framing
     whose fingerprint_id is set gets a LINKED trace; otherwise the matcher
     provides an EMBEDDING-STAGE LEAD (never presented as confirmed). The
     dates themselves come from fingerprint genealogy, confidence and all.

This adjudicates nothing: it counts, pairs, and dates what circles' own
member outlets said, with receipts. Low overlap is a reportable finding.

Recognition rule (METHODOLOGY.md v0): everything shown per circle is that
circle's own quoted language — excerpts from its member outlets' carriers —
enforced structurally, as in circles.py.

CLI:
    python common_ground.py events/<id>.json     # one event: all three + overlay
    python common_ground.py <event> --confirm    # + stage-2 judge on claim pairs
    python common_ground.py --sweep events/      # corpus table: where is overlap
    python common_ground.py --buried             # capture-week shared-but-buried
    python common_ground.py --backfill events/   # store common_ground on each JSON
"""

import argparse
import json
import re
import sys
from datetime import date
from itertools import combinations
from pathlib import Path

from actors import ActorRegistry
from bias_db import BiasDB
from circles import CIRCLES, derive_membership, framing_rollup, _load_dir
from matcher import HI_THRESHOLD, LO_THRESHOLD, Matcher, embed_texts

_ROOT = Path(__file__).resolve().parent
_FINGERPRINT_DIR = _ROOT / "fingerprints"

# A claim unit is a carrier's verbatim excerpt. Shorter fragments ("totally
# obliterated") embed as near-noise and pair promiscuously; this floor keeps
# units sentence-shaped. Raising it drops units, never invents them.
MIN_CLAIM_CHARS = 40
# Pairs reported per event, ranked by similarity. A cap is a coverage bound,
# so the output records how many candidates it dropped (no silent caps).
MAX_PAIRS = 12
# Embedding-stage lead bar for the claim-age overlay — same value
# agenda.attach_traces uses for its digest leads (uncalibrated, and therefore
# never presented as more than a lead).
LEAD_MIN_SIM = 0.60


# ---------------------------------------------------------------------------
# 1. Framing intersection
# ---------------------------------------------------------------------------

def _asserting(row: dict) -> int:
    """Member outlets ASSERTING the framing (champion + mention). Opposing
    carriage is engagement with the framing, not agreement with it."""
    st = row.get("stances", {})
    return st.get("champion", 0) + st.get("mention", 0)


def framing_intersection(event: dict, rollup: dict = None,
                         db: BiasDB = None) -> dict:
    """Framings appearing substantively in EVERY circle, from the Phase 1a
    rollup. Returns {"intersections": [...], "contested": [...]} — contested
    rows are framings every circle touches but at least one circle only
    opposes. Rows keep the rollup's receipts (each circle's own quotes)."""
    rep = rollup or framing_rollup(event, db=db)
    by_fr = {}
    for r in rep["rows"]:
        by_fr.setdefault((r["framing_id"], r["framing"]), {})[r["circle_id"]] = r

    intersections, contested = [], []
    for (fid, fname), per in by_fr.items():
        if any(per.get(c.circle_id, {}).get("n_pieces", 0) < 1 for c in CIRCLES):
            continue                      # absent from some circle: no overlap claim
        per_circle = {}
        statement_bits = []
        thin = []
        for c in CIRCLES:
            r = per[c.circle_id]
            per_circle[c.circle_id] = {
                "n_pieces": r["n_pieces"], "n_total": r["n_total"],
                "rate": r["rate"], "stances": r["stances"],
                "asserting": _asserting(r),
                "quotes": r["representative_quotes"],
            }
            statement_bits.append(
                f"{r['n_pieces']}/{r['n_total']} {c.circle_id} pieces")
            if r["n_total"] < 3:
                thin.append(c.circle_id)
        row = {
            "framing_id": fid, "framing": fname,
            "statement": f"'{fname}' appears in " + " and ".join(statement_bits),
            "per_circle": per_circle,
            # strength = the weakest circle's asserting count: an intersection
            # is only as substantive as its thinnest side
            "strength": min(pc["asserting"] for pc in per_circle.values()),
            "thin_circles": thin,
        }
        if row["strength"] >= 1:
            intersections.append(row)
        else:
            opp = [cid for cid, pc in per_circle.items() if pc["asserting"] == 0]
            row["oppose_only_circles"] = opp
            contested.append(row)
    intersections.sort(key=lambda r: (
        -r["strength"],
        -min(pc["rate"] or 0 for pc in r["per_circle"].values()),
        r["framing"]))
    return {"intersections": intersections, "contested": contested,
            "n_framings": rep["n_framings"], "note": rep.get("note", "")}


# ---------------------------------------------------------------------------
# 2. Convergent claims
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def _norm_claim(s: str) -> str:
    return _WS.sub(" ", (s or "").strip().lower())


def _claim_units(event: dict, db: BiasDB) -> dict:
    """circle_id -> [{text, receipts[]}]: each circle's member outlets'
    verbatim ASSERTED excerpts (primary tier, stance != oppose), deduped by
    normalized text within the circle (wire copy on two member sites is one
    claim with two receipts). Receipts carry outlet + URL + archive + date +
    framing, so every pair ships checkable provenance."""
    reg = ActorRegistry()
    reg.ingest_event(event)
    reg.finalize()
    members = derive_membership(reg, db)["members"]
    fr_names = {f.get("framing_id", ""): f.get("name", "")
                for f in event.get("framings", [])}
    units = {c.circle_id: {} for c in CIRCLES}
    for e in reg.edges:
        if e.narrative_kind != "framing" or e.tier != "primary":
            continue
        if e.stance == "oppose":          # quoted to rebut is not asserted
            continue
        edge = members.get(e.actor_key)
        if edge is None:
            continue
        text = (e.evidence or "").strip()
        if len(text) < MIN_CLAIM_CHARS:
            continue
        slot = units[edge.circle_id].setdefault(
            _norm_claim(text), {"text": text, "receipts": []})
        slot["receipts"].append({
            "outlet": edge.actor_display, "stance": e.stance,
            "url": e.source_url, "archive_url": e.archive_url,
            "date": e.date, "framing_id": e.narrative_id,
            "framing": fr_names.get(e.narrative_id, e.narrative_label),
        })
    return {cid: list(d.values()) for cid, d in units.items()}


def _confirm_pairs(pairs: list, model: str = "claude-haiku-4-5-20251001") -> list:
    """One batched stage-2 call over the retained pairs, reusing matcher.py's
    validated judge prompt (its input format is already a JSON list). Returns
    [{same, why}] aligned with `pairs`; raises on any failure — callers treat
    failure as not-confirmed, never as confirmed."""
    import anthropic
    from fingerprint import _parse_json_safe
    from matcher import CONFIRM_SYSTEM
    client = anthropic.Anthropic()
    payload = [{"id": i, "a": p["a"]["text"], "b": p["b"]["text"]}
               for i, p in enumerate(pairs)]
    resp = client.messages.create(
        model=model, max_tokens=128 + 64 * len(pairs),
        system=[{"type": "text", "text": CONFIRM_SYSTEM}],
        messages=[{"role": "user", "content": json.dumps(payload)}])
    text = "".join(b.text for b in resp.content
                   if getattr(b, "type", "") == "text")
    j = {int(row.get("id", -1)): row
         for row in (_parse_json_safe(text) or {}).get("judgments", [])}
    return [{"same": bool(j.get(i, {}).get("same", False)),
             "why": str(j.get(i, {}).get("why", ""))}
            for i in range(len(pairs))]


def convergent_claims(event: dict, db: BiasDB = None,
                      confirm: bool = False, max_pairs: int = MAX_PAIRS) -> dict:
    """Cross-circle claim pairs. Embedding stage (local, free) proposes;
    the stage-2 judge disposes. Without confirmation every pair stays a
    CANDIDATE — the two stages fail differently (embeddings miss blame and
    consequence; Gate 1 measured it), so 'convergent' is only ever the
    composite verdict."""
    db = db or BiasDB()
    units = _claim_units(event, db)
    n_units = {cid: len(us) for cid, us in units.items()}
    out = {"n_claim_units": n_units, "pairs": [], "dropped_candidates": 0,
           "confirm_run": False,
           "note": ("pairs are embedding-stage candidates only; nothing is "
                    "labeled convergent without the stage-2 judge (--confirm)")}
    if sum(1 for n in n_units.values() if n) < 2:
        return out

    vecs = {}
    for cid, us in units.items():
        if us:
            import numpy as np
            vecs[cid] = np.asarray(embed_texts([u["text"] for u in us]),
                                   dtype="float32")
    pairs = []
    for ca, cb in combinations([c.circle_id for c in CIRCLES], 2):
        if ca not in vecs or cb not in vecs:
            continue
        sims = vecs[ca] @ vecs[cb].T
        ia, ib = (sims >= LO_THRESHOLD).nonzero()
        for i, k in zip(ia.tolist(), ib.tolist()):
            ua, ub = units[ca][i], units[cb][k]
            sim = float(sims[i][k])
            pairs.append({
                "similarity": round(sim, 3),
                "band": "candidate_high" if sim >= HI_THRESHOLD else "review_band",
                "identical_wording": _norm_claim(ua["text"]) == _norm_claim(ub["text"]),
                "status": "candidate",
                "a": {"circle_id": ca, **ua},
                "b": {"circle_id": cb, **ub},
            })
    pairs.sort(key=lambda p: -p["similarity"])
    out["dropped_candidates"] = max(0, len(pairs) - max_pairs)
    pairs = pairs[:max_pairs]

    if confirm and pairs:
        try:
            verdicts = _confirm_pairs(pairs)
            out["confirm_run"] = True
            out["note"] = ("stage-2 judge run on retained pairs; only "
                           "judge-confirmed pairs are convergent")
            for p, v in zip(pairs, verdicts):
                p["confirm"] = v
                p["status"] = "convergent" if v["same"] else "rejected_by_judge"
        except Exception as e:  # noqa: BLE001 — no confirmation, no convergence
            out["note"] = (f"stage-2 judge unavailable "
                           f"({type(e).__name__}) — all pairs remain candidates")
    out["pairs"] = pairs
    return out


# ---------------------------------------------------------------------------
# 4. Claim-age overlay (on intersecting framings)
# ---------------------------------------------------------------------------

def _age_days(first: str, event_date: str):
    try:
        return (date.fromisoformat(event_date[:10])
                - date.fromisoformat(first[:10])).days
    except (ValueError, TypeError):
        return None


def _lineage_block(fp: dict, ltype: str, event_date: str) -> dict:
    lin = (fp.get("genealogy") or {}).get(ltype) or {}
    first = lin.get("first_attested_date", "")
    if not first:
        return {}
    return {
        "first_attested_date": first,
        "first_attested_source": lin.get("first_attested_source", ""),
        "status": lin.get("status", ""),
        "attestation_confidence": lin.get("attestation_confidence", ""),
        "age_days_at_event": _age_days(first, event_date),
    }


def claim_age_overlay(intersections: list, event: dict,
                      fp_dir: Path = _FINGERPRINT_DIR) -> int:
    """Attach first-attestation dates to intersecting framings, in place.
    Basis is explicit on every attachment: 'linked' (the framing's own
    fingerprint_id) or 'embedding_lead' (matcher nearest neighbor >=
    LEAD_MIN_SIM — a lead, never a confirmed identity). Age is structural:
    the reader distinguishes organic from seeded; we only report dates."""
    if not intersections:
        return 0
    fps = {}
    for p in fp_dir.glob("*.json"):
        if p.name in ("index.json", "vectors.json", "review_queue.json",
                      "generation_queue.json"):
            continue
        try:
            fp = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if fp.get("fingerprint_id"):
            fps[fp["fingerprint_id"]] = fp

    linked_ids = {f.get("framing_id", ""): f.get("fingerprint_id", "")
                  for f in event.get("framings", [])}
    key_claims = {f.get("framing_id", ""): f.get("key_claim", "")
                  for f in event.get("framings", [])}
    event_date = event.get("event_date", "") or event.get("created_at", "")
    matcher = Matcher(str(fp_dir))
    have_vectors = bool(matcher.sidecar.get("vectors"))

    attached = 0
    for row in intersections:
        fid = row["framing_id"]
        fp, basis, lead = None, "", None
        if linked_ids.get(fid) and linked_ids[fid] in fps:
            fp, basis = fps[linked_ids[fid]], "linked"
        elif have_vectors and key_claims.get(fid):
            r = matcher.match(key_claims[fid])
            b = r.best
            if b and max(b.l1_sim, b.l2_sim) >= LEAD_MIN_SIM and b.fingerprint_id in fps:
                fp, basis = fps[b.fingerprint_id], "embedding_lead"
                lead = {"l1_sim": round(b.l1_sim, 3), "l2_sim": round(b.l2_sim, 3),
                        "decision": r.decision,
                        "note": "embedding-stage lead only; not a confirmed "
                                "identity (serving discipline unchanged)"}
        if fp is None:
            continue
        block = {
            "basis": basis,
            "fingerprint_id": fp["fingerprint_id"],
            "canonical_phrase": (fp.get("lexical") or {}).get("canonical_phrase", ""),
            "lexical": _lineage_block(fp, "lexical", event_date),
            "conceptual": _lineage_block(fp, "conceptual", event_date),
        }
        if lead:
            block["lead"] = lead
        if block["lexical"] or block["conceptual"]:
            row["claim_age"] = block
            attached += 1
    return attached


# ---------------------------------------------------------------------------
# Per-event bundle (what --backfill stores)
# ---------------------------------------------------------------------------

def common_ground(event: dict, db: BiasDB = None, confirm: bool = False) -> dict:
    db = db or BiasDB()
    inter = framing_intersection(event, db=db)
    conv = convergent_claims(event, db=db, confirm=confirm)
    n_aged = claim_age_overlay(inter["intersections"], event)
    return {
        "analysis_id": event.get("analysis_id", ""),
        "event": event.get("event", "")[:160],
        "framing_intersection": inter,
        "convergent_claims": conv,
        "claim_age_attached": n_aged,
        "membership_basis": {
            "basis": "allsides_snapshot",
            "as_of": db.meta.get("as_of", ""),
            "source_note": "Ratings are AllSides' — Tributary only aggregates them.",
        },
    }


# ---------------------------------------------------------------------------
# 3. Shared-but-buried (per capture week, agenda layer)
# ---------------------------------------------------------------------------

BURIED_MIN_PER_SIDE = 2   # same presence bar one_side_only uses per side

BURIED_CAVEAT = (
    "The story universe is what at least one sampled RSS feed carried — a "
    "story no feed touched at all is invisible to this query. COVERAGE by a "
    "circle means a rated US member outlet either feed-carried the story or "
    "has a news-sitemap article matching it (the omission report's evidence "
    "standard; threshold uncalibrated until the Gate 2 audit). Note the risk "
    "direction: here a borderline sitemap match can be ADJACENT coverage "
    "rather than the story itself, which would overstate 'both circles "
    "covered it' — every sitemap receipt therefore ships the matched "
    "article's title, URL and similarity so the claim is checkable. "
    "FEATURING is feed-only: observed at position <= {pos} in at least one "
    "capture. "
    "'Buried' therefore means both circles demonstrably covered the story "
    "and no member outlet of either circle ever surfaced it top-of-feed — "
    "it says nothing about placement on outlets' websites. Circle scope is "
    "RATED US outlets only (the AllSides spectrum is a US construct, the "
    "same scope one_side_only uses). Outlets outside the circles that DID "
    "feature the story are listed on the row."
)


def shared_but_buried(days: int = 7, min_per_side: int = BURIED_MIN_PER_SIDE,
                      threshold: float = None, save: bool = True) -> dict:
    """The inverted one-side-only query: stories COVERED by >= min_per_side
    rated US outlets in EVERY circle, feed-featured by NONE of them. Reuses
    agenda.py's capture/cluster/describe machinery unchanged, so the story
    universe is identical to the agenda report's. Coverage evidence is feed
    carriage OR a sitemap-title match (feed carriage alone misses the point:
    a story that only ever sat deep in feeds — or never entered them — but
    was written about by both sides is exactly the buried agreement this
    query exists to surface)."""
    import numpy as np

    import agenda
    threshold = threshold if threshold is not None else agenda.STORY_THRESHOLD
    roster = agenda.load_roster()
    leans = agenda.resolve_leans(roster)
    snaps = agenda.load_snapshots(days)
    if not snaps:
        return {"error": "no agenda snapshots in window — run agenda.py --capture"}
    items_by_outlet, _stale = agenda.aggregate_items(snaps, days)
    raw, _ = agenda.cluster_stories(items_by_outlet, threshold=threshold)
    stories = agenda.describe_stories(raw, leans, roster)

    bucket_to_circle = {b: c.circle_id for c in CIRCLES for b in c.buckets}
    by_key = roster["by_key"]
    circle_outlets = {c.circle_id: [] for c in CIRCLES}   # rated US members
    for o in roster["outlets"]:
        if o["stream"] == "news" and o["country"] == "US":
            cid = bucket_to_circle.get(leans.get(o["key"], ""))
            if cid:
                circle_outlets[cid].append(o["key"])

    # Sitemap coverage evidence, computed vectorized per outlet (article
    # titles x story centroids), exactly as the agenda report does. Absent
    # sitemap data degrades honestly to feed-only evidence.
    try:
        from sitemaps import load_sitemap_titles
        sitemap_titles = load_sitemap_titles(days)
    except Exception:  # noqa: BLE001 — no captures = feed-only evidence
        sitemap_titles = {}
    member_keys = {k for ks in circle_outlets.values() for k in ks}
    sitemap_vecs = agenda.embed_sitemap_titles(
        {k: v for k, v in sitemap_titles.items() if k in member_keys})
    cents = np.vstack([s["_centroid"] for s in stories]) if stories else None
    sm_hits = {}   # outlet_key -> {story_idx: (sim, article)}
    for okey, (vecs, arts) in sitemap_vecs.items():
        sims = vecs @ cents.T                     # articles x stories
        best_article = sims.argmax(axis=0)        # per story: closest article
        best_sim = sims.max(axis=0)
        sm_hits[okey] = {
            int(j): (float(best_sim[j]), arts[int(best_article[j])])
            for j in (best_sim >= agenda.COVERED_THRESHOLD).nonzero()[0]}

    rows, n_covered_both, n_featured_some = [], 0, 0
    for idx, s in enumerate(stories):
        per_circle = {c.circle_id: [] for c in CIRCLES}
        outside_featured = []
        feed_outlets = set()
        for carr in s["carriers"]:
            o = by_key.get(carr["outlet"])
            if o is None or o["stream"] != "news":
                continue
            feed_outlets.add(carr["outlet"])
            crec = {
                "outlet": carr["outlet"], "name": carr["name"],
                "evidence": "feed",
                "best_position": carr["best_position"],
                "captures_seen": carr.get("captures_seen", 1),
                "featured": carr["best_position"] <= agenda.FEATURED_POSITION,
                "sample_title": carr["sample_title"],
                "sample_link": carr["sample_link"],
            }
            cid = (bucket_to_circle.get(leans.get(carr["outlet"], ""))
                   if o["country"] == "US" else None)
            if cid:
                per_circle[cid].append(crec)
            elif crec["featured"]:
                crec["lean"] = leans.get(carr["outlet"], "") or (
                    "international" if o["country"] != "US" else "unrated")
                outside_featured.append(crec)
        for cid, keys in circle_outlets.items():   # sitemap-only coverage
            for okey in keys:
                if okey in feed_outlets or idx not in sm_hits.get(okey, {}):
                    continue
                sim, art = sm_hits[okey][idx]
                per_circle[cid].append({
                    "outlet": okey, "name": by_key[okey]["name"],
                    "evidence": "sitemap", "featured": False,
                    "nearest_sim": round(sim, 3),
                    "sample_title": art["title"], "sample_link": art["url"],
                })
        if any(len(cs) < min_per_side for cs in per_circle.values()):
            continue
        n_covered_both += 1
        featured_circles = [cid for cid, cs in per_circle.items()
                            if any(c["featured"] for c in cs)]
        if featured_circles:
            n_featured_some += 1
            continue
        for cs in per_circle.values():
            cs.sort(key=lambda c: (c["evidence"] != "feed",
                                   c.get("best_position", 10**6)))
        rows.append({
            "story_id": s["story_id"], "label": s["label"],
            "n_outlets": s["n_outlets"], "n_items": s["n_items"],
            "per_circle": per_circle,
            "best_position_any_circle": min(
                (c["best_position"] for cs in per_circle.values()
                 for c in cs if c["evidence"] == "feed"), default=None),
            "featured_outside_circles": outside_featured,
        })
    rows.sort(key=lambda r: (-sum(len(cs) for cs in r["per_circle"].values()),
                             r["best_position_any_circle"] or 10**6))

    from agenda import FEATURED_POSITION, REPORT_DIR, _now_utc
    end = _now_utc()
    report = {
        "is_shared_buried_report": True,
        "generated_at": end.isoformat(),
        "period_days": days,
        "captures": len(snaps),
        "method_caveat": BURIED_CAVEAT.format(pos=FEATURED_POSITION),
        "params": {"min_rated_us_outlets_per_side": min_per_side,
                   "featured_position": FEATURED_POSITION,
                   "story_threshold": threshold,
                   "sitemap_covered_threshold": agenda.COVERED_THRESHOLD,
                   "member_outlets_with_sitemap": sorted(sitemap_vecs)},
        "totals": {
            "stories": len(stories),
            "covered_by_every_circle": n_covered_both,
            "of_those_feed_featured_by_a_circle": n_featured_some,
            "shared_but_buried": len(rows),
        },
        "stories": rows,
    }
    if save:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / f"{end.date().isoformat()}_shared_buried_{days}d.json"
        path.write_text(json.dumps(report, indent=1, ensure_ascii=False),
                        encoding="utf-8")
        print(f"[buried] {len(rows)} shared-but-buried of {n_covered_both} "
              f"both-circle stories ({len(stories)} total) -> {path}",
              file=sys.stderr)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_event(rep: dict):
    inter = rep["framing_intersection"]
    conv = rep["convergent_claims"]
    print(f"\nCommon ground — {rep['event']}")
    if inter["note"]:
        print(f"  NOTE: {inter['note']}")

    print(f"\n  framing intersections: {len(inter['intersections'])} of "
          f"{inter['n_framings']} framings "
          f"(contested: {len(inter['contested'])})")
    for row in inter["intersections"]:
        thin = f"  [thin: {', '.join(row['thin_circles'])}]" if row["thin_circles"] else ""
        print(f"\n  · {row['statement']}{thin}")
        for cid, pc in row["per_circle"].items():
            for q in pc["quotes"][:1]:
                arch = " [archived]" if q["archive_url"] else ""
                print(f"      {cid:<14} {q['outlet'][:24]:<24} ({q['stance']}) "
                      f"\"{q['quote'][:76]}\"{arch}")
        age = row.get("claim_age")
        if age:
            lex = age.get("lexical") or {}
            d = lex.get("first_attested_date", "")
            days_old = lex.get("age_days_at_event")
            print(f"      claim age    first attested {d or '?'}"
                  + (f" ({days_old} days before event)" if days_old is not None else "")
                  + f"  [{age['basis']}; lineage {lex.get('status','')}, "
                    f"conf {lex.get('attestation_confidence','')}]")
    for row in inter["contested"]:
        print(f"  x contested (not common ground): '{row['framing']}' — "
              f"oppose-only in {', '.join(row['oppose_only_circles'])}")

    units = ", ".join(f"{cid} {n}" for cid, n in conv["n_claim_units"].items())
    print(f"\n  convergent claims — claim units: {units}"
          + (f"; {conv['dropped_candidates']} candidates beyond the top "
             f"{MAX_PAIRS} dropped" if conv["dropped_candidates"] else ""))
    print(f"  ({conv['note']})")
    for p in conv["pairs"]:
        tag = {"convergent": "CONVERGENT", "rejected_by_judge": "rejected"}.get(
            p["status"], p["band"])
        same = " — identical wording" if p["identical_wording"] else ""
        print(f"\n  · sim {p['similarity']:.2f} [{tag}]{same}"
              + (f"  judge: {p['confirm']['why']}" if p.get("confirm") else ""))
        for side in ("a", "b"):
            u = p[side]
            r0 = u["receipts"][0]
            more = f" (+{len(u['receipts'])-1} more)" if len(u["receipts"]) > 1 else ""
            print(f"      {u['circle_id']:<14} {r0['outlet'][:24]:<24}"
                  f" \"{u['text'][:72]}\"{more}")


def _print_buried(rep: dict):
    if rep.get("error"):
        print(rep["error"], file=sys.stderr)
        return
    t = rep["totals"]
    print(f"\nShared-but-buried — last {rep['period_days']} days "
          f"({rep['captures']} captures)")
    print(f"  {t['stories']} stories; {t['covered_by_every_circle']} carried by "
          f">= {rep['params']['min_rated_us_outlets_per_side']} rated US outlets "
          f"on each side; {t['of_those_feed_featured_by_a_circle']} of those "
          f"featured by a circle; {t['shared_but_buried']} shared-but-buried")
    for s in rep["stories"]:
        print(f"\n  · {s['label'][:96]}")
        for cid, cs in s["per_circle"].items():
            ol = ", ".join(
                (f"{c['name']} (pos {c['best_position']}, x{c['captures_seen']})"
                 if c["evidence"] == "feed"
                 else f"{c['name']} (sitemap, sim {c['nearest_sim']})")
                for c in cs[:3])
            print(f"      {cid:<14} {len(cs)} outlets: {ol}")
        if s["featured_outside_circles"]:
            ol = ", ".join(f"{c['name']} (pos {c['best_position']}, {c['lean']})"
                           for c in s["featured_outside_circles"][:3])
            print(f"      featured outside circles: {ol}")
    print(f"\n  caveat: {rep['method_caveat']}")


def main():
    if hasattr(sys.stdout, "reconfigure"):   # quoted excerpts vs cp1252 consoles
        sys.stdout.reconfigure(errors="replace")
    p = argparse.ArgumentParser(
        description="Phase 1b: framing intersection, convergent claims, "
                    "shared-but-buried, claim-age overlay.")
    p.add_argument("event_json", nargs="?")
    p.add_argument("--sweep", default="", help="Overlap table over a corpus dir.")
    p.add_argument("--backfill", default="",
                   help="Store a common_ground block on each event JSON.")
    p.add_argument("--buried", action="store_true",
                   help="Capture-week shared-but-buried report (agenda layer).")
    p.add_argument("--days", type=int, default=7,
                   help="capture window for --buried (default 7)")
    p.add_argument("--min-per-side", type=int, default=BURIED_MIN_PER_SIDE,
                   help="rated US outlets per circle for 'covered by both'")
    p.add_argument("--confirm", action="store_true",
                   help="Run the stage-2 judge on claim pairs "
                        "(needs ANTHROPIC_API_KEY; ~$0.001/event).")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.buried:
        rep = shared_but_buried(days=args.days, min_per_side=args.min_per_side)
        if args.json:
            print(json.dumps(rep, indent=2, ensure_ascii=False))
        else:
            _print_buried(rep)
        return

    db = BiasDB()
    if args.sweep:
        print(f"{'inter':>5} {'cont':>5} {'convHI':>6} {'convRV':>6} "
              f"{'maxsim':>6}  event")
        rows = []
        for _, ev in _load_dir(args.sweep):
            if not ev.get("framings"):
                continue
            inter = framing_intersection(ev, db=db)
            conv = convergent_claims(ev, db=db)
            hi = sum(1 for q in conv["pairs"] if q["band"] == "candidate_high")
            rv = sum(1 for q in conv["pairs"] if q["band"] == "review_band")
            mx = conv["pairs"][0]["similarity"] if conv["pairs"] else 0.0
            rows.append((len(inter["intersections"]), len(inter["contested"]),
                         hi, rv, mx, ev.get("event", "")[:56]))
        rows.sort(key=lambda r: (-r[0], -r[2], -r[4]))
        for it, ct, hi, rv, mx, name in rows:
            print(f"{it:>5} {ct:>5} {hi:>6} {rv:>6} {mx:>6.2f}  {name}")
        print(f"\n({len(rows)} events; inter = framings asserted by member "
              f"outlets of every circle; cont = every circle touches, some "
              f"circle only opposes; conv = cross-circle claim pairs at "
              f">= {HI_THRESHOLD} / {LO_THRESHOLD}..{HI_THRESHOLD} — "
              f"embedding-stage candidates, not yet judge-confirmed)")
        return

    if args.backfill:
        done = 0
        for path, ev in _load_dir(args.backfill):
            if not ev.get("framings"):
                continue
            ev["common_ground"] = common_ground(ev, db=db, confirm=args.confirm)
            path.write_text(json.dumps(ev, indent=2, ensure_ascii=False,
                                       default=str), encoding="utf-8")
            done += 1
        print(f"[backfill] stored common_ground on {done} event JSONs "
              f"(confirm={'on' if args.confirm else 'off'}).", file=sys.stderr)
        return

    if not args.event_json:
        p.error("give an event JSON, or --sweep/--backfill <dir>, or --buried")
    ev = json.loads(Path(args.event_json).read_text(encoding="utf-8"))
    rep = common_ground(ev, db=db, confirm=args.confirm)
    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        _print_event(rep)


if __name__ == "__main__":
    main()
