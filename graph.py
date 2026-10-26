"""
Graph v0 — the actor <-> narrative graph, maintainer-only (2026-10-25, Batch D)
===============================================================================
The bipartite substrate of the bubbles roadmap (MISSION_PLAN Spine +
Phase 4), built purely from the published corpus:

  edge = (actor, narrative, role, date, receipt)
    - actors via registry/actors.json's canonicalization (actors_registry);
    - narratives = published traces; attestation-log entries supply edges;
    - event carriers join through their framing's judge-confirmed
      fingerprint_id (the Batch B joins) — the cross-half edges;
    - related-context never edges (context is not carriage);
    - CRITIC edges are kept in the graph but EXCLUDED from co-carriage
      clustering: a bubble is who PUSHES a narrative; debunkers of it are
      doing the opposite work, and folding them in would merge every
      fact-checker into the bubbles they cover.

Co-carriage projection: actor pairs weighted by how many narratives they
both carry. Clusters: connected components over pairs sharing >= 2
narratives (deliberately dumb for v0 — no tuned community detection until
the Phase 4 audit defines what "right" means). ENTRANCE EXAM: the
clustering must recover the documented 2026-10 Talarico burst carriers as
one cluster; "not dense enough yet" is a legitimate, reportable result.

Output goes to the PRIVATE maintainer dir, never the repo: cluster
membership is exactly the claim class the Phase 4 audit must gate.

    python graph.py --report "C:/Users/Tarek/Documents/tributary_private/graph_v0_report.md"
"""

import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from actors_registry import actor_key

ROOT = Path(__file__).resolve().parent
CARRY_ROLES = {"originator", "early-amplifier", "mass-amplifier",
               "institutional-adoption", "mention", "unknown", ""}
TALARICO_EXPECTED = {"thegatewaypundit.com", "wltreport.com", "pjmedia.com",
                     "redstate.com", "townhall.com", "100percentfedup.com",
                     "@spencerpratt@x", "@realamvoice@x"}


def load_registry():
    reg = json.loads((ROOT / "registry" / "actors.json").read_text(encoding="utf-8"))
    merges = reg.get("merges") or {}
    def canon(key):
        return merges.get(key, key)
    return reg, canon


def build_edges(canon):
    """[(actor, narrative_id, role, date, via)]"""
    edges = []
    claims = {}
    for p in sorted((ROOT / "gallery" / "traces").glob("*.json")):
        fp = json.loads(p.read_text(encoding="utf-8"))
        nid = fp.get("fingerprint_id") or p.stem
        claims[nid] = (fp.get("lexical") or {}).get("canonical_phrase", "")[:80]
        for layer in ("lexical", "conceptual"):
            for a in ((fp.get("genealogy") or {}).get(layer) or {}).get("attestation_log") or []:
                if (a.get("claim_relation") or "") == "related-context":
                    continue
                key, _ = actor_key(a.get("source_url") or "")
                if not key:
                    continue
                edges.append((canon(key), nid, str(a.get("amplifier_role") or ""),
                              str(a.get("date") or "")[:10], "trace"))
    for p in sorted((ROOT / "gallery" / "events").glob("*.json")):
        ev = json.loads(p.read_text(encoding="utf-8"))
        for f in ev.get("framings") or []:
            nid = (f.get("fingerprint_id") or "").strip()
            if not nid or nid not in claims:
                continue
            for c in f.get("carriers") or []:
                key, _ = actor_key(c.get("url") or "")
                if not key:
                    continue
                edges.append((canon(key), nid, str(c.get("amplifier_role") or ""),
                              str(c.get("date") or "")[:10], "event-join"))
    return edges, claims


def co_carriage(edges):
    carried = defaultdict(set)      # actor -> narratives CARRIED (not critic)
    for actor, nid, role, _d, _v in edges:
        if role in CARRY_ROLES:
            carried[actor].add(nid)
    pair_w = Counter()
    for a1, a2 in combinations(sorted(carried), 2):
        shared = carried[a1] & carried[a2]
        if shared:
            pair_w[(a1, a2)] = len(shared)
    return carried, pair_w


def clusters_from_pairs(pair_w, min_shared=2):
    parent = {}
    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    for (a1, a2), w in pair_w.items():
        if w >= min_shared:
            union(a1, a2)
    groups = defaultdict(set)
    for node in parent:
        groups[find(node)].add(node)
    return [sorted(g) for g in groups.values() if len(g) >= 3]


def entrance_exam(carried, pair_w):
    """Does the documented Talarico burst bind at ANY shared-narrative
    threshold? Report the strongest honest statement the data supports.
    A cluster holding more than a third of all carrying actors is the
    everything-blob (mega-carriers glue the corpus) — finding the burst
    inside it recovers nothing."""
    present = {a for a in TALARICO_EXPECTED if a in carried}
    out = {"expected": sorted(TALARICO_EXPECTED), "present_in_graph": sorted(present)}
    for min_shared in (3, 2, 1):
        cls = clusters_from_pairs(pair_w, min_shared=min_shared)
        best, overlap = None, 0
        for c in cls:
            o = len(present & set(c))
            if o > overlap:
                best, overlap = c, o
        size = len(best) if best else 0
        blob = size > max(20, len(carried) // 3)
        verdict = ("blob — not meaningful" if blob and overlap
                   else "RECOVERED" if overlap >= max(4, len(present) - 2)
                   else "partial" if overlap >= 2 else "not recovered")
        out[f"min_shared={min_shared}"] = {
            "clusters": len(cls), "burst_members_in_one_cluster": overlap,
            "of_present": len(present), "cluster_size": size, "verdict": verdict,
        }
    return out


def main():
    ap = argparse.ArgumentParser(description="Build graph v0 and run the entrance exam.")
    ap.add_argument("--report", default="", help="write the maintainer report HERE "
                    "(keep it OUTSIDE the repo — cluster claims are unaudited)")
    ap.add_argument("--min-shared", type=int, default=2)
    args = ap.parse_args()

    if args.report:
        rp = Path(args.report).resolve()
        if ROOT in rp.parents:
            raise SystemExit("refusing: the graph report must live OUTSIDE the repo")

    reg, canon = load_registry()
    edges, claims = build_edges(canon)
    carried, pair_w = co_carriage(edges)
    cls = clusters_from_pairs(pair_w, min_shared=args.min_shared)
    exam = entrance_exam(carried, pair_w)

    n_join = sum(1 for e in edges if e[4] == "event-join")
    print(f"edges: {len(edges)} ({n_join} via event joins) | actors carrying: "
          f"{len(carried)} | narratives: {len(claims)} | "
          f"pairs sharing>=1: {len(pair_w)} | clusters(min_shared={args.min_shared}): {len(cls)}")
    print("exam:", json.dumps({k: v for k, v in exam.items() if k.startswith('min_')}, indent=1))

    if args.report:
        lines = [f"# Graph v0 — maintainer report (2026-10-25)\n",
                 f"- edges: {len(edges)} ({n_join} via event joins); actors carrying: {len(carried)}; "
                 f"narratives: {len(claims)}\n",
                 f"\n## Entrance exam (Talarico burst)\n```json\n{json.dumps(exam, indent=1)}\n```\n",
                 f"\n## Clusters (min shared narratives = {args.min_shared}; UNAUDITED — internal only)\n"]
        for i, c in enumerate(sorted(cls, key=len, reverse=True)):
            binding = Counter()
            cset = set(c)
            for a in c:
                for nid in carried[a]:
                    binding[nid] += 1
            top = [f"{claims.get(n, n)[:60]} ({k} members)"
                   for n, k in binding.most_common(5) if k >= 2]
            lines.append(f"\n### cluster {i + 1} ({len(c)} actors)\n- members: {', '.join(c)}\n"
                         f"- binding narratives: " + ("; ".join(top) if top else "(none shared by 2+)") + "\n")
        rp.write_text("".join(lines), encoding="utf-8")
        print(f"report -> {rp}")


if __name__ == "__main__":
    main()
