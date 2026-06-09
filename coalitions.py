"""
Per-event coalition layer (Phase A2)
====================================
Given an EventAnalysis, build the bipartite slice — which ACTORS carry which
FRAMINGS — from the shared actor graph (actors.py), and derive the axis-free
coalition structure:

  - framings, each with its carrier set (primary sources + secondary relays)
  - BRIDGES: actors that carry 2+ framings (cross-cutting connectors — the
    "strange bedfellows" when those framings are otherwise separate)
  - DISJOINTNESS between framing coalitions (do actors silo into one framing,
    or span several?) — the narrative-centric polarization proxy, NO left/right

This adjudicates nothing: it describes who is amplifying which lens of the
event. It's a sample (the carriers we surfaced), so metrics are guarded on
having enough structure to mean anything.

CLI:
    python coalitions.py events/<id>.json        # one event
    python coalitions.py --sweep events/         # calibration table over a corpus
    python coalitions.py --backfill events/      # store `coalition` on each JSON
"""

import argparse
import itertools
import json
import sys
from pathlib import Path

from actors import ActorRegistry


def coalition(event: dict, registry: ActorRegistry = None) -> dict:
    reg = registry or ActorRegistry()
    if registry is None:
        reg.ingest_event(event)
        reg.finalize()

    # framing_id -> {name, actors:set, primary:set, secondary:set, stance:{actor:stance}}
    _RANK = {"champion": 3, "oppose": 2, "mention": 1}
    fr = {}
    for e in reg.edges:
        if e.narrative_kind != "framing":
            continue
        m = fr.setdefault(e.narrative_id, {"name": e.narrative_label,
                                           "actors": set(), "primary": set(),
                                           "secondary": set(), "stance": {}})
        m["actors"].add(e.actor_key)
        m[e.tier].add(e.actor_key)
        cur = m["stance"].get(e.actor_key)
        if cur is None or _RANK[e.stance] > _RANK[cur]:
            m["stance"][e.actor_key] = e.stance      # strongest stance this actor takes

    # include framings that had zero resolvable carriers, for honest counts
    for f in event.get("framings", []):
        fr.setdefault(f.get("framing_id", ""), {"name": f.get("name", ""),
                                                "actors": set(), "primary": set(),
                                                "secondary": set()})

    fids = [k for k in fr if fr[k]["actors"]]          # framings with carriers
    actor_framings = {}                                # actor_key -> set(framing_id) carried
    actor_champ = {}                                   # actor_key -> set(framing_id) CHAMPIONED
    actor_oppose = {}                                  # actor_key -> set(framing_id) OPPOSED
    for fid in fids:
        for a in fr[fid]["actors"]:
            actor_framings.setdefault(a, set()).add(fid)
        for a, st in fr[fid]["stance"].items():
            if st == "champion":
                actor_champ.setdefault(a, set()).add(fid)
            elif st == "oppose":
                actor_oppose.setdefault(a, set()).add(fid)
    n_actors = len(actor_framings)

    # pairwise overlap between framing coalitions
    pairs = []
    for a, b in itertools.combinations(fids, 2):
        sa, sb = fr[a]["actors"], fr[b]["actors"]
        inter, union = sa & sb, sa | sb
        j = len(inter) / len(union) if union else 0.0
        pairs.append({"a": fr[a]["name"], "b": fr[b]["name"],
                      "shared": len(inter), "jaccard": round(j, 3)})
    mean_j = round(sum(p["jaccard"] for p in pairs) / len(pairs), 3) if pairs else None

    # BRIDGES now require CHAMPIONING 2+ framings — an actor that actively
    # pushes multiple lenses, not one that merely mentions several (which is
    # just broad coverage). Reference/aggregator hosts stay out.
    bridges = []
    for a, fs in actor_champ.items():
        if len(fs) >= 2 and reg.actors[a].actor_type != "reference":
            act = reg.actors[a]
            bridges.append({"actor_id": act.actor_id, "display": act.display_name,
                            "actor_type": act.actor_type, "n_framings": len(fs),
                            "framings": sorted(fr[f]["name"] for f in fs)})
    bridges.sort(key=lambda b: -b["n_framings"])

    # CONTESTED actors — the genuine strange bedfellows: champion one framing
    # while OPPOSING (critic of) another. This is the real cross-pressure signal
    # stance unlocks, distinct from breadth.
    contested = []
    for a in set(actor_champ) & set(actor_oppose):
        if reg.actors[a].actor_type == "reference":
            continue
        act = reg.actors[a]
        contested.append({"actor_id": act.actor_id, "display": act.display_name,
                          "actor_type": act.actor_type,
                          "champions": sorted(fr[f]["name"] for f in actor_champ[a]),
                          "opposes": sorted(fr[f]["name"] for f in actor_oppose[a])})

    bridge_ratio = round(len(bridges) / n_actors, 3) if n_actors else 0.0
    siloed = sum(1 for fs in actor_framings.values() if len(fs) == 1)
    siloed_share = round(siloed / n_actors, 3) if n_actors else 0.0

    by_type = {}
    for a in actor_framings:
        t = reg.actors[a].actor_type
        by_type[t] = by_type.get(t, 0) + 1

    def _carrier_list(fid):
        out = []
        for a in fr[fid]["actors"]:
            act = reg.actors[a]
            out.append({"actor_id": act.actor_id, "display": act.display_name,
                        "actor_type": act.actor_type,
                        "stance": fr[fid]["stance"].get(a, "mention"),
                        "tier": "primary" if a in fr[fid]["primary"] else "secondary",
                        "bridges": len(actor_champ.get(a, ())) >= 2})
        # champions first, opposers flagged, then by tier/bridge/name — viewer-ready
        order = {"champion": 0, "mention": 1, "oppose": 2}
        return sorted(out, key=lambda c: (order.get(c["stance"], 1),
                                          c["tier"] != "primary", not c["bridges"],
                                          c["display"].lower()))

    return {
        "n_framings": len(fids),
        "n_actors": n_actors,
        "framings": [{"framing_id": fid, "name": fr[fid]["name"],
                      "n_carriers": len(fr[fid]["actors"]),
                      "primary": len(fr[fid]["primary"]),
                      "secondary": len(fr[fid]["secondary"]),
                      "carriers": _carrier_list(fid)}
                     for fid in fids],
        "bridges": bridges,
        "contested_actors": contested,
        "framing_pairs": sorted(pairs, key=lambda p: p["jaccard"]),
        "by_actor_type": by_type,
        # Raw connectivity numbers — stored, but NOT dressed up as a polarization
        # score: bridge_ratio falls as one-off sources accumulate, so it partly
        # tracks event scale. Read the BRIDGES and the structure, not this number.
        "connectivity": {
            "bridge_ratio": bridge_ratio,        # actors carrying 2+ framings / all actors
            "siloed_share": siloed_share,        # actors carrying exactly 1 framing
            "mean_jaccard": mean_j,              # avg framing-pair carrier overlap
        },
        "structure_note": _structure_note(len(fids), n_actors, len(bridges)),
    }


def fingerprint_actors(fp: dict, registry: ActorRegistry = None) -> dict:
    """Upstream counterpart of coalition(): the canonical actors who amplified a
    NarrativeFingerprint over time, resolved from its genealogy (attestation_log
    + social_spread) via the SAME actor graph. Adds what downstream lacks —
    TIME (dates) and the individuals/social accounts (incl. Bluesky from
    --social). This is the A4 tie-off: both directions, one substrate."""
    reg = registry or ActorRegistry()
    if registry is None:
        reg.ingest_fingerprint(fp)
        reg.finalize()
    _RANK = {"champion": 3, "oppose": 2, "mention": 1}
    agg = {}
    for e in reg.edges:
        if e.narrative_kind != "fingerprint":
            continue
        a = agg.setdefault(e.actor_key, {"roles": set(), "stance": "mention",
                                         "lineages": set(), "social": False,
                                         "dates": [], "n": 0})
        a["roles"].add(e.role)
        if _RANK[e.stance] > _RANK[a["stance"]]:
            a["stance"] = e.stance
        if e.lineage_type:
            a["lineages"].add(e.lineage_type)
        if e.direction == "upstream-social":
            a["social"] = True
        if e.date:
            a["dates"].append(e.date)
        a["n"] += 1

    actors = []
    for k, a in agg.items():
        act = reg.actors[k]
        dates = sorted(d for d in a["dates"] if d)
        actors.append({"actor_id": act.actor_id, "display": act.display_name,
                       "actor_type": act.actor_type, "roles": sorted(a["roles"]),
                       "stance": a["stance"], "lineages": sorted(a["lineages"]),
                       "is_social": a["social"], "n_edges": a["n"],
                       "first_date": dates[0] if dates else "",
                       "last_date": dates[-1] if dates else ""})
    actors.sort(key=lambda x: (x["first_date"] or "9999", x["display"].lower()))
    all_dates = sorted(d for x in actors for d in (x["first_date"], x["last_date"]) if d)
    by_type = {}
    for x in actors:
        by_type[x["actor_type"]] = by_type.get(x["actor_type"], 0) + 1
    return {
        "n_actors": len(actors),
        "actors": actors,
        "originators": [x for x in actors if "originator" in x["roles"]],
        "critics": [x for x in actors if x["stance"] == "oppose"],
        "social_accounts": [x for x in actors if x["is_social"]],
        "by_actor_type": by_type,
        "date_span": [all_dates[0], all_dates[-1]] if all_dates else None,
    }


def _structure_note(n_framings, n_actors, n_bridges) -> str:
    """A factual, guarded one-liner — describes connectivity, claims no verdict."""
    if n_framings < 2 or n_actors < 6:
        return "too few framings/actors to characterize the coalition structure"
    return (f"{n_bridges} of {n_actors} actors actively champion more than one framing "
            f"(real cross-cutting, not just broad coverage); see the bridges and any "
            f"contested actors for who connects — or splits — which lenses.")


def _print(rep: dict):
    c = rep["connectivity"]
    print(f"\n  framings: {rep['n_framings']}  actors: {rep['n_actors']}  "
          f"bridge-ratio: {c['bridge_ratio']}")
    print(f"  {rep['structure_note']}")
    print(f"  actor types: {rep['by_actor_type']}")
    if rep["bridges"]:
        print("  bridging actors (CHAMPION multiple framings — the connectors):")
        for b in rep["bridges"][:10]:
            print(f"    [{b['actor_type']:<12}] {b['display'][:34]:<34} "
                  f"{b['n_framings']} framings: {', '.join(b['framings'][:3])}"
                  f"{'…' if b['n_framings'] > 3 else ''}")
    if rep.get("contested_actors"):
        print("  contested actors (champion one framing, OPPOSE another — strange bedfellows):")
        for c in rep["contested_actors"][:8]:
            print(f"    [{c['actor_type']:<12}] {c['display'][:30]:<30} "
                  f"champions[{', '.join(c['champions'][:2])}]  opposes[{', '.join(c['opposes'][:2])}]")


def _load_dir(d: str):
    for p in sorted(Path(d).glob("*.json")):
        if p.name in ("index.json", "corpus_index.json"):
            continue
        try:
            yield p, json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue


def main():
    p = argparse.ArgumentParser(description="Per-event coalition structure.")
    p.add_argument("event_json", nargs="?")
    p.add_argument("--sweep", default="", help="Calibration table over a corpus dir.")
    p.add_argument("--backfill", default="", help="Store `coalition` on each event JSON.")
    p.add_argument("--backfill-fp", default="",
                   help="Store `amplifiers` on each NarrativeFingerprint JSON in this dir.")
    p.add_argument("--fp", default="", help="Show one fingerprint's upstream amplifiers.")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.backfill_fp:
        done = 0
        for path, fp in _load_dir(args.backfill_fp):
            if not fp.get("genealogy"):
                continue
            fp["amplifiers"] = fingerprint_actors(fp)
            path.write_text(json.dumps(fp, indent=2, ensure_ascii=False, default=str),
                            encoding="utf-8")
            done += 1
        print(f"[backfill] stored amplifiers on {done} fingerprint JSONs.", file=sys.stderr)
        return
    if args.fp:
        fp = json.loads(Path(args.fp).read_text(encoding="utf-8"))
        rep = fingerprint_actors(fp)
        print(f"Upstream amplifiers — {(fp.get('lexical',{}) or {}).get('canonical_phrase','')[:60]}")
        print(f"  {rep['n_actors']} actors; span {rep['date_span']}; types {rep['by_actor_type']}")
        print(f"  originators: {', '.join(a['display'] for a in rep['originators'][:5]) or '(none)'}")
        print(f"  critics: {', '.join(a['display'] for a in rep['critics'][:5]) or '(none)'}")
        print(f"  social accounts: {len(rep['social_accounts'])}")
        for a in rep["actors"][:12]:
            print(f"    {a['first_date'][:10] or '          '}  [{a['actor_type']:<10}] "
                  f"{a['display'][:32]:<32} {'/'.join(a['lineages'])} {a['stance']}")
        return

    if args.sweep:
        rows = []
        for _, ev in _load_dir(args.sweep):
            if not ev.get("framings"):
                continue
            r = coalition(ev)
            rows.append((r["n_actors"], r["connectivity"]["bridge_ratio"],
                         len(r["bridges"]), ev.get("event", "")[:50]))
        rows.sort(key=lambda x: -x[1])
        print(f"{'actors':>6} {'bridgeR':>7} {'bridges':>7}  event")
        for n, br, nb, ev in rows:
            print(f"{n:>6} {br:>7} {nb:>7}  {ev}")
        return
    if args.backfill:
        done = 0
        for path, ev in _load_dir(args.backfill):
            if not ev.get("framings"):
                continue
            ev["coalition"] = coalition(ev)
            path.write_text(json.dumps(ev, indent=2, ensure_ascii=False, default=str),
                            encoding="utf-8")
            done += 1
        print(f"[backfill] stored coalition on {done} event JSONs.", file=sys.stderr)
        return
    if not args.event_json:
        p.error("give an event JSON, or --sweep/--backfill <dir>")
    ev = json.loads(Path(args.event_json).read_text(encoding="utf-8"))
    rep = coalition(ev)
    print(json.dumps(rep, indent=2, ensure_ascii=False) if args.json else "", end="")
    if not args.json:
        print(f"Coalition — {ev.get('event','')[:70]}")
        _print(rep)


if __name__ == "__main__":
    main()
