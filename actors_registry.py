"""
Actor canonicalization registry (2026-10-25, Batch D / Phase 4 prerequisite)
============================================================================
One outlet, many spellings: the same actor appears across the corpus as
'thegatewaypundit.com', 'Gateway Pundit', and 'The Gateway Pundit'. The
graph is confetti until those are one node. This tool builds
registry/actors.json mechanically from the published corpus:

  - an http(s) URL canonicalizes to its registrable domain (outlet actors),
    EXCEPT platform hosts where the subdomain or path IS the actor:
    *.substack.com keeps its subdomain; x.com/twitter.com, t.me,
    bsky.app/profile, youtube.com/@ keep their handle (account actors);
  - display names and aliases are collected from carrier names, source
    titles, and authors seen alongside each key;
  - what cannot be resolved mechanically (name-only carriers, near-duplicate
    display names under different keys) is emitted as review_candidates —
    the maintainer's ten minutes, durable forever (plan 2e checkpoint).

    python actors_registry.py            # build + print summary
    python actors_registry.py --merges   # print the review-candidates list

The registry is public (it only groups names already published in the
corpus); the GRAPH built on top of it stays maintainer-side until the
Phase 4 cluster audit (see graph.py).
"""

import argparse
import difflib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
REG_DIR = ROOT / "registry"

SUBDOMAIN_IS_ACTOR = ("substack.com", "wordpress.com", "medium.com", "blogspot.com")
HANDLE_HOSTS = {
    "x.com": re.compile(r"^/(\w{1,15})(?:/|$)"),
    "twitter.com": re.compile(r"^/(\w{1,15})(?:/|$)"),
    "t.me": re.compile(r"^/(?:s/)?([\w@]+)(?:/|$)"),
    "bsky.app": re.compile(r"^/profile/([^/]+)"),
    "youtube.com": re.compile(r"^/(@[\w.-]+)"),
    "rumble.com": None,   # path ids are videos, not actors -> domain actor
}
NOT_HANDLES = {"i", "home", "search", "hashtag", "intent", "share", "status"}
# two-label public suffixes we actually meet; a full PSL is overkill here
CC_SECOND = {"co", "com", "org", "net", "ac", "gov", "edu"}


def registrable_domain(host: str) -> str:
    host = host.lower().lstrip(".").removeprefix("www.")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    for plat in SUBDOMAIN_IS_ACTOR:
        if host.endswith(plat) and host != plat:
            return host                      # the subdomain IS the actor
    if parts[-2] in CC_SECOND and len(parts[-1]) == 2:
        return ".".join(parts[-3:])          # e.g. theguardian.co.uk style
    return ".".join(parts[-2:])


def actor_key(url: str):
    """(key, kind) or (None, None). key examples:
    'gatewaypundit.com' (outlet) / '@realamvoice@x' (account) /
    'luthmann.substack.com' (outlet, subdomain actor)."""
    try:
        u = urlparse(url.strip())
    except ValueError:
        return None, None
    if u.scheme not in ("http", "https") or not u.netloc:
        return None, None
    host = u.netloc.lower().removeprefix("www.")
    base = registrable_domain(host)
    pat = HANDLE_HOSTS.get(base)
    if pat:
        m = pat.match(u.path or "")
        if m:
            h = m.group(1).lower().lstrip("@")
            if h and h not in NOT_HANDLES:
                plat = "x" if base in ("x.com", "twitter.com") else base.split(".")[0]
                return f"@{h}@{plat}", "account"
        if base in ("x.com", "twitter.com", "t.me", "bsky.app"):
            return None, None                # platform page, not an actor
    return base, "outlet"


def _clean_name(s: str) -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    s = re.sub(r"\s*[—–|-]\s*(on X|post on X|X post).*$", "", s, flags=re.I)
    return s[:80]


def harvest():
    actors = defaultdict(lambda: {"kind": "", "names": Counter(), "n_edges": 0,
                                  "sources": Counter()})
    nameless = Counter()      # name-only carriers (no URL) -> review
    for p in sorted((ROOT / "gallery" / "traces").glob("*.json")):
        fp = json.loads(p.read_text(encoding="utf-8"))
        for layer in ("lexical", "conceptual"):
            for a in ((fp.get("genealogy") or {}).get(layer) or {}).get("attestation_log") or []:
                key, kind = actor_key(a.get("source_url") or "")
                if not key:
                    continue
                rec = actors[key]
                rec["kind"] = rec["kind"] or kind
                rec["n_edges"] += 1
                rec["sources"]["traces"] += 1
                for field in ("author", "source_title"):
                    n = _clean_name(a.get(field))
                    if n and len(n) > 2 and kind == "account":
                        rec["names"][n] += 1
                if kind == "outlet":
                    rec["names"][key] += 1
    for p in sorted((ROOT / "gallery" / "events").glob("*.json")):
        ev = json.loads(p.read_text(encoding="utf-8"))
        for f in ev.get("framings") or []:
            for c in f.get("carriers") or []:
                key, kind = actor_key(c.get("url") or "")
                nm = _clean_name(c.get("name"))
                if not key:
                    if nm:
                        nameless[nm] += 1
                    continue
                rec = actors[key]
                rec["kind"] = rec["kind"] or kind
                rec["n_edges"] += 1
                rec["sources"]["events"] += 1
                if nm:
                    rec["names"][nm] += 1
    return actors, nameless


def build():
    actors, nameless = harvest()
    out = {}
    for key, rec in actors.items():
        names = [n for n, _ in rec["names"].most_common(6)]
        out[key] = {"kind": rec["kind"],
                    "display": names[0] if names else key,
                    "aliases": names[1:],
                    "n_edges": rec["n_edges"],
                    "sources": dict(rec["sources"])}
    # review candidates: (a) name-only carriers that fuzzy-match an actor's
    # display (likely the same actor, needs a human yes); (b) distinct keys
    # with near-identical displays (possible rebrand/mirror)
    review = []
    display_by_key = {k: v["display"].lower() for k, v in out.items()}
    for nm, cnt in nameless.most_common():
        best = max(display_by_key,
                   key=lambda k: difflib.SequenceMatcher(None, nm.lower(), display_by_key[k]).ratio(),
                   default=None)
        ratio = difflib.SequenceMatcher(None, nm.lower(), display_by_key.get(best, "")).ratio() if best else 0
        review.append({"type": "name-only-carrier", "name": nm, "times_seen": cnt,
                       "suggested_actor": best if ratio >= 0.6 else "",
                       "similarity": round(ratio, 2)})
    keys = sorted(out)
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            # comparing two bare domains is string noise (acs.org/ans.org);
            # a pair is only worth a human look when at least one side has a
            # real display name or the registrable domains share a stem
            if display_by_key[k1] == k1 and display_by_key[k2] == k2 \
                    and k1.split(".")[0][:6] != k2.split(".")[0][:6]:
                continue
            r = difflib.SequenceMatcher(None, display_by_key[k1], display_by_key[k2]).ratio()
            if r >= 0.85:
                review.append({"type": "possible-duplicate-actors",
                               "a": k1, "b": k2, "similarity": round(r, 2)})
    REG_DIR.mkdir(exist_ok=True)
    reg = {"built": "2026-10-25", "note": "mechanical pass; merges pending maintainer review",
           "actors": out, "review_candidates": review, "merges": {}}
    (REG_DIR / "actors.json").write_text(
        json.dumps(reg, indent=1, ensure_ascii=False), encoding="utf-8")
    return reg


def main():
    ap = argparse.ArgumentParser(description="Build the actor canonicalization registry.")
    ap.add_argument("--merges", action="store_true", help="print review candidates only")
    args = ap.parse_args()
    reg = build()
    actors, review = reg["actors"], reg["review_candidates"]
    kinds = Counter(a["kind"] for a in actors.values())
    print(f"actors: {len(actors)} ({dict(kinds)}); review candidates: {len(review)}")
    top = sorted(actors.items(), key=lambda kv: -kv[1]["n_edges"])[:12]
    for k, a in top:
        print(f"  {a['n_edges']:>3} edges  {k:34} {a['display'][:40]}")
    if args.merges or review:
        print("\n--- review candidates (maintainer checkpoint) ---")
        for r in review[:40]:
            print(" ", json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
