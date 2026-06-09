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

    # framing_id -> {name, actors:set, primary:set, secondary:set}
    fr = {}
    for e in reg.edges:
        if e.narrative_kind != "framing":
            continue
        m = fr.setdefault(e.narrative_id, {"name": e.narrative_label,
                                           "actors": set(), "primary": set(),
                                           "secondary": set()})
        m["actors"].add(e.actor_key)
        m[e.tier].add(e.actor_key)

    # include framings that had zero resolvable carriers, for honest counts
    for f in event.get("framings", []):
        fr.setdefault(f.get("framing_id", ""), {"name": f.get("name", ""),
                                                "actors": set(), "primary": set(),
                                                "secondary": set()})

    fids = [k for k in fr if fr[k]["actors"]]          # framings with carriers
    actor_framings = {}                                # actor_key -> set(framing_id)
    for fid in fids:
        for a in fr[fid]["actors"]:
            actor_framings.setdefault(a, set()).add(fid)
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

    bridges = []
    for a, fs in actor_framings.items():
        # Reference/aggregator hosts (Wikipedia, Britannica…) bridge everything
        # by being cited everywhere — that's not cross-cutting, it's infra. Keep
        # them out of the connector signal (they remain carriers on the framing).
        if len(fs) >= 2 and reg.actors[a].actor_type != "reference":
            act = reg.actors[a]
            bridges.append({"actor_id": act.actor_id, "display": act.display_name,
                            "actor_type": act.actor_type, "n_framings": len(fs),
                            "framings": sorted(fr[f]["name"] for f in fs)})
    bridges.sort(key=lambda b: -b["n_framings"])
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
                        "tier": "primary" if a in fr[fid]["primary"] else "secondary",
                        "bridges": len(actor_framings.get(a, ())) >= 2})
        # primary first, then bridges, then alpha — viewer-ready order
        return sorted(out, key=lambda c: (c["tier"] != "primary", not c["bridges"],
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


def _structure_note(n_framings, n_actors, n_bridges) -> str:
    """A factual, guarded one-liner — describes connectivity, claims no verdict."""
    if n_framings < 2 or n_actors < 6:
        return "too few framings/actors to characterize the coalition structure"
    return (f"{n_bridges} of {n_actors} actors bridge multiple framings; the rest "
            f"appear under a single framing. See the bridges for who connects which lenses.")


def _print(rep: dict):
    c = rep["connectivity"]
    print(f"\n  framings: {rep['n_framings']}  actors: {rep['n_actors']}  "
          f"bridge-ratio: {c['bridge_ratio']}")
    print(f"  {rep['structure_note']}")
    print(f"  actor types: {rep['by_actor_type']}")
    if rep["bridges"]:
        print("  bridging actors (carry multiple framings — the connectors):")
        for b in rep["bridges"][:10]:
            print(f"    [{b['actor_type']:<12}] {b['display'][:34]:<34} "
                  f"{b['n_framings']} framings: {', '.join(b['framings'][:3])}"
                  f"{'…' if b['n_framings'] > 3 else ''}")


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
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

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
