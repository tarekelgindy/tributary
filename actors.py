"""
Tributary actor registry & resolution
======================================
The NODE layer of the bipartite amplification graph: actors ↔ narratives.

Both directions write into one graph, because the schema already aligns —
every carrier of a narrative, upstream or down, is an actor↔narrative edge
with an `amplifier_role`:

  - downstream  FramingCarrier            (who carries a framing of an event)
  - upstream    AttestedInstance          (who attested a narrative; dated, lexical/conceptual)
  - upstream    SocialAttestedInstance    (who posted it; platform + engagement)

This module canonicalizes the messy "who" — outlet names, author bylines,
social handles, org names — into stable Actor nodes, so the same actor across
framings, events, and lineages collapses to ONE node. That's what lets the two
directions progress in tandem instead of diverging.

Identity is deliberately PRAGMATIC for Phase A (within-corpus):
  outlets by registrable domain · social by platform:handle · blogs by host ·
  officials by gov domain · people by normalized name.
People are NOT aggressively merged (homonyms/variants/affiliation are a Phase B
concern). The goal here is good-enough nodes to see coalitions, not a perfect
entity-resolution system.

CLI (validation):
    python actors.py --events events/ --fingerprints fingerprints/
"""

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from bias_db import normalize_domain, _norm_name

# Social platforms → canonical handle-bearing actors (keyed by platform:handle)
_SOCIAL_HOSTS = {
    "x.com": "x", "twitter.com": "x", "nitter.net": "x",
    "bsky.app": "bluesky", "bsky.social": "bluesky",
    "facebook.com": "facebook", "fb.com": "facebook",
    "instagram.com": "instagram", "threads.net": "threads",
    "youtube.com": "youtube", "youtu.be": "youtube",
    "tiktok.com": "tiktok", "t.me": "telegram", "reddit.com": "reddit",
    "truthsocial.com": "truthsocial", "rumble.com": "rumble",
    "gab.com": "gab", "mastodon.social": "mastodon", "post.news": "post",
}
# Canonical display names for common citation hosts — overrides per-carrier
# names (which occasionally disagree with their own URL in the source data) and
# gives aggregators/wire/fact-checkers a clean label. Keyed by registrable domain.
_DOMAIN_NAMES = {
    "wikipedia.org": "Wikipedia", "britannica.com": "Britannica",
    "youtube.com": "YouTube", "snopes.com": "Snopes", "politifact.com": "PolitiFact",
    "factcheck.org": "FactCheck.org", "aljazeera.com": "Al Jazeera",
    "nih.gov": "NIH", "reuters.com": "Reuters", "apnews.com": "Associated Press",
    "bbc.com": "BBC", "bbc.co.uk": "BBC", "npr.org": "NPR", "pbs.org": "PBS NewsHour",
    "nytimes.com": "The New York Times", "washingtonpost.com": "The Washington Post",
    "wsj.com": "The Wall Street Journal", "cnn.com": "CNN", "foxnews.com": "Fox News",
    "nbcnews.com": "NBC News", "cbsnews.com": "CBS News", "abcnews.com": "ABC News",
    "abcnews.go.com": "ABC News", "time.com": "TIME", "axios.com": "Axios",
    "politico.com": "Politico", "thehill.com": "The Hill", "theguardian.com": "The Guardian",
    "timesofisrael.com": "The Times of Israel", "jpost.com": "The Jerusalem Post",
    "cnbc.com": "CNBC", "yahoo.com": "Yahoo", "nypost.com": "New York Post",
    "usatoday.com": "USA Today", "bloomberg.com": "Bloomberg", "forbes.com": "Forbes",
}
# Blog/newsletter platforms → the specific publication IS the host (subdomain)
_BLOG_SUFFIXES = (".substack.com", ".medium.com", ".wordpress.com",
                  ".blogspot.com", ".ghost.io", ".tumblr.com", ".wixsite.com")
_GOV_SUFFIXES = (".gov", ".mil", ".senate.gov", ".house.gov")

# downstream carrier_type / source hints → actor_type
_TYPE_MAP = {
    "news": "news", "social": "social", "political": "official",
    "institutional": "organization", "academic": "academic", "other": "other",
}


def _registrable(host: str) -> str:
    """nytimes.com / www.nytimes.com / mobile.nytimes.com -> nytimes.com.
    Keeps a ccTLD pair (bbc.co.uk). Subdomain dropped EXCEPT for blog hosts,
    where the subdomain is the identity (handled before this is called)."""
    if not host:
        return ""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if parts[-2] in ("co", "com", "org", "gov", "net", "ac") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])          # foo.co.uk
    return ".".join(parts[-2:])


def _handle_from_url(url: str, host: str) -> str:
    """x.com/lyndseyfififield -> lyndseyfififield ; youtube.com/@chan -> chan"""
    try:
        path = urlsplit(url if "//" in url else "//" + url).path
    except ValueError:
        return ""
    seg = next((p for p in path.split("/") if p), "")
    seg = seg.lstrip("@").strip()
    # skip non-handle path roots
    if seg.lower() in ("watch", "channel", "user", "p", "status", "i", "intent", "hashtag"):
        parts = [p for p in path.split("/") if p]
        seg = parts[1].lstrip("@") if len(parts) > 1 else ""
    return seg.lower()


def _looks_label(s: str) -> bool:
    """Is this string a usable actor name (an outlet/handle), vs an article
    title or sentence we should NOT adopt as identity?"""
    s = (s or "").strip()
    if not s or not any(c.isalpha() for c in s):
        return False
    if len(s) > 45 or len(s.split()) > 7:
        return False
    if s.endswith((".", ":", "”", '"', "?", "!")):
        return False
    return True


def _label_score(s: str) -> int:
    """Higher = more like a clean outlet/handle label (used to pick the best
    display when several resolutions collide on one actor key)."""
    s = (s or "").strip()
    if not s:
        return -100
    sc = 0
    wc = len(s.split())
    sc += 2 if len(s) <= 28 else (1 if len(s) <= 45 else -3)
    sc += 2 if wc <= 4 else (0 if wc <= 7 else -4)
    if s.endswith((".", ":", "”", '"', "?", "!")):
        sc -= 2
    if any(c.isupper() for c in s):
        sc += 1
    if " " in s:
        sc += 1
    return sc


def _site_type(host: str, carrier_type: str) -> str:
    """Type a domain-keyed actor from the DOMAIN, not the speaker's role. A
    politician quoted via cnn.com (carrier_type 'political') must not make CNN
    'official'. Institutional/academic DO describe the host (hrw.org, harvard)."""
    if host.endswith(_GOV_SUFFIXES):
        return "official"
    ct = (carrier_type or "").lower()
    if ct == "institutional":
        return "organization"
    if ct == "academic":
        return "academic"
    return "news"


def _strip_author_paren(name: str) -> tuple:
    """'The New York Times (Katie Glueck)' -> ('The New York Times', 'Katie Glueck').
    'WBUR / Associated Press' -> ('WBUR', 'Associated Press').
    'Rep. Massie via CNN' -> ('Rep. Massie', '')  (drop the venue suffix)."""
    name = (name or "").strip()
    author = ""
    if "(" in name and ")" in name:
        author = name[name.index("(") + 1: name.rindex(")")].strip()
        name = name[:name.index("(")].strip()
    for sep in (" via ", " / ", " — ", " - "):
        if sep in name:
            head, _, tail = name.partition(sep)
            name, author = head.strip(), author or tail.strip()
            break
    return name, author


# Tokens that mark a name as an ORGANISATION rather than a person.
_ORG_WORDS = {"office", "committee", "department", "forces", "administration",
              "party", "news", "watch", "institute", "coalition", "union",
              "association", "group", "council", "court", "bank", "ministry",
              "agency", "bureau", "commission", "campaign", "foundation",
              "center", "centre", "network", "service", "company", "corp",
              "house", "senate", "congress", "parliament", "court", "fund"}
_PERSON_TITLES = ("rep.", "rep ", "sen.", "sen ", "dr.", "mr.", "ms.", "mrs.",
                  "gov.", "pres.", "president ", "sec.", "secretary ", "justice ",
                  "judge ", "gen.", "brig.", "col.", "lt.", "minister ", "prof.",
                  "professor ", "chancellor ", "mayor ", "ambassador ")


def _looks_personal(name: str) -> bool:
    n = (name or "").strip()
    low = n.lower()
    if low.startswith(_PERSON_TITLES):
        return True
    toks = n.split()
    if 2 <= len(toks) <= 4 and all(t[:1].isupper() for t in toks if t):
        return not any(t.lower().strip(".,") in _ORG_WORDS for t in toks)
    return False


@dataclass
class Actor:
    """A canonical node in the amplification graph."""
    key: str                         # stable identity key (e.g. 'site:nytimes.com')
    display_name: str
    actor_type: str                  # news / social / blog / official / organization / academic / person / other
    domain: str = ""
    handle: str = ""
    platform: str = ""
    is_person: bool = False
    aliases: set = field(default_factory=set)

    @property
    def actor_id(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {"actor_id": self.actor_id, "key": self.key,
                "display_name": self.display_name, "actor_type": self.actor_type,
                "domain": self.domain, "handle": self.handle,
                "platform": self.platform, "is_person": self.is_person,
                "aliases": sorted(self.aliases)}


@dataclass
class AmplificationEdge:
    """An actor amplifying a narrative — the unit both directions emit."""
    actor_key: str
    actor_display: str
    narrative_kind: str              # 'framing' | 'fingerprint'
    narrative_id: str
    narrative_label: str
    role: str                        # amplifier_role value
    direction: str                   # downstream | upstream-formal | upstream-social
    context_id: str = ""             # event analysis_id or fingerprint_id
    date: str = ""
    lineage_type: str = ""           # lexical | conceptual (upstream)
    platform: str = ""
    source_url: str = ""
    evidence: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def resolve(name: str = "", url: str = "", handle: str = "", platform: str = "",
            carrier_type: str = "", author: str = "", source_title: str = "") -> Actor:
    """Resolve a raw 'who' into a canonical Actor. Order: explicit social ->
    social host -> blog host -> gov -> generic outlet domain -> bare person."""
    name, paren_author = _strip_author_paren(name)
    author = author or paren_author
    host = normalize_domain(url) if url else ""
    # Only adopt `name` as identity if it reads like a label; an article title
    # (source_title) is NEVER identity — it's evidence, kept on the edge.
    clean = name if _looks_label(name) else ""

    # 1. explicit social attestation (upstream social: platform+handle given)
    if platform and handle:
        h = handle.lstrip("@").lower()
        return Actor(key=f"social:{platform.lower()}:{h}", display_name=clean or ("@" + h),
                     actor_type="social", handle=h, platform=platform.lower(),
                     is_person=True, aliases={a for a in (author,) if a})

    # 2. social host in the URL — the account is the actor
    if host in _SOCIAL_HOSTS:
        plat = _SOCIAL_HOSTS[host]
        h = handle.lstrip("@").lower() or _handle_from_url(url, host)
        if h:
            return Actor(key=f"social:{plat}:{h}", display_name=clean or ("@" + h),
                         actor_type="social", handle=h, platform=plat, is_person=True)
        return Actor(key=f"site:{host}", display_name=clean or plat,
                     actor_type="social", domain=host)

    # 3. NAMED actor (politician / org / scholar): the speaker is the amplifier,
    # any news URL is just the citation venue (kept on the edge, not the identity).
    ct = (carrier_type or "").lower()
    if ct in ("political", "institutional", "academic") and clean:
        if _looks_personal(clean):
            atype = "official" if ct == "political" else ("academic" if ct == "academic" else "person")
            return Actor(key=f"person:{_norm_name(clean)}", display_name=clean,
                         actor_type=atype, is_person=True)
        atype = {"political": "official", "institutional": "organization",
                 "academic": "academic"}[ct]
        return Actor(key=f"org:{_norm_name(clean)}", display_name=clean, actor_type=atype)

    # 4. blog/newsletter platform — the subdomain publication is the identity
    if host and host.endswith(_BLOG_SUFFIXES):
        return Actor(key=f"blog:{host}", display_name=clean or host,
                     actor_type="blog", domain=host, aliases={a for a in (author,) if a})

    # 4/5. outlet/site by registrable domain — TYPE FROM THE DOMAIN, not the
    # speaker's carrier_type (a politician quoted via cnn.com ≠ CNN is official)
    if host:
        reg = _registrable(host)
        return Actor(key=f"site:{reg}", display_name=clean or reg,
                     actor_type=_site_type(host, carrier_type), domain=reg,
                     aliases={a for a in (author,) if a and a != name})

    # 6. no URL — a bare person or named org (here carrier_type DOES describe it)
    base_type = _TYPE_MAP.get((carrier_type or "").lower(), "")
    person = author or name
    nk = _norm_name(person)
    if nk:
        is_person = base_type not in ("organization", "official") and bool(author or " " in person.strip())
        return Actor(key=f"{'person' if is_person else 'org'}:{nk}", display_name=person,
                     actor_type="person" if is_person else (base_type or "organization"),
                     is_person=is_person)
    return Actor(key=f"name:{(name or 'unknown').lower()}", display_name=name or "(unknown)",
                 actor_type=base_type or "other")


class ActorRegistry:
    """Interns actors (dedupe by key, merge aliases) and collects edges. One
    registry per event for Phase A; a persistent one is the Phase B corpus graph."""

    def __init__(self):
        self.actors: dict = {}
        self.edges: list = []
        self.name_votes: dict = {}       # key -> {candidate display: count}
        self._final = False

    def intern(self, a: Actor) -> Actor:
        cur = self.actors.get(a.key)
        if cur is None:
            self.actors[a.key] = a
            cur = a
        else:
            if cur.actor_type in ("", "other") and a.actor_type not in ("", "other"):
                cur.actor_type = a.actor_type
            cur.aliases |= a.aliases
        # Vote for the display: don't let one mislabeled carrier win — take the
        # most-common label-like name across all of this actor's appearances.
        if _looks_label(a.display_name):
            votes = self.name_votes.setdefault(a.key, {})
            votes[a.display_name] = votes.get(a.display_name, 0) + 1
        return cur

    def finalize(self):
        """Resolve each actor's display name: canonical map > majority vote >
        whatever it had. Call before reading displays. Idempotent."""
        if self._final:
            return
        for key, actor in self.actors.items():
            canon = _DOMAIN_NAMES.get(actor.domain)
            votes = self.name_votes.get(key, {})
            if canon:
                chosen = canon
            elif votes:
                chosen = max(votes.items(), key=lambda kv: (kv[1], _label_score(kv[0])))[0]
            else:
                chosen = actor.display_name
            actor.aliases |= {n for n in votes if n != chosen}
            actor.aliases.discard(chosen)
            actor.display_name = chosen
        self._final = True

    def _edge(self, actor: Actor, **kw):
        self.edges.append(AmplificationEdge(
            actor_key=actor.key, actor_display=actor.display_name, **kw))

    def ingest_event(self, event: dict):
        ctx = event.get("analysis_id", "")
        for fr in event.get("framings", []):
            nref = (fr.get("framing_id", ""), fr.get("name", ""))
            for c in fr.get("carriers", []):
                a = self.intern(resolve(name=c.get("name", ""), url=c.get("url", ""),
                                        carrier_type=c.get("carrier_type", "")))
                self._edge(a, narrative_kind="framing", narrative_id=nref[0],
                           narrative_label=nref[1], role=c.get("amplifier_role", "unknown"),
                           direction="downstream", context_id=ctx,
                           source_url=c.get("url", ""), evidence=c.get("excerpt", ""))

    def ingest_fingerprint(self, fp: dict):
        fid = fp.get("fingerprint_id", "")
        label = ((fp.get("lexical") or {}).get("canonical_phrase")
                 or fp.get("claim") or fp.get("input_text", ""))[:80]
        gen = fp.get("genealogy", {}) or {}
        for ltype in ("lexical", "conceptual"):
            rec = gen.get(ltype, {}) or {}
            for a_inst in rec.get("attestation_log", []):
                a = self.intern(resolve(url=a_inst.get("source_url", ""),
                                        source_title=a_inst.get("source_title", ""),
                                        author=a_inst.get("author", "")))
                self._edge(a, narrative_kind="fingerprint", narrative_id=fid,
                           narrative_label=label, role=a_inst.get("amplifier_role", "unknown"),
                           direction="upstream-formal", context_id=fid,
                           date=a_inst.get("date", ""), lineage_type=ltype,
                           source_url=a_inst.get("source_url", ""),
                           evidence=a_inst.get("exact_quote", ""))
            for s in rec.get("social_spread", []):
                a = self.intern(resolve(handle=s.get("author_handle", ""),
                                        platform=s.get("platform", "")))
                self._edge(a, narrative_kind="fingerprint", narrative_id=fid,
                           narrative_label=label, role=s.get("amplifier_role", "unknown"),
                           direction="upstream-social", context_id=fid,
                           date=s.get("post_date", ""), lineage_type=ltype,
                           platform=s.get("platform", ""), source_url=s.get("post_url", ""))


def _load_dir(d: str) -> list:
    out = []
    for p in sorted(Path(d).glob("*.json")):
        if p.name in ("index.json", "corpus_index.json"):
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def main():
    p = argparse.ArgumentParser(description="Build/validate the actor graph over a corpus.")
    p.add_argument("--events", default="", help="Directory of EventAnalysis JSONs.")
    p.add_argument("--fingerprints", default="", help="Directory of NarrativeFingerprint JSONs.")
    p.add_argument("--top", type=int, default=15)
    args = p.parse_args()
    if not args.events and not args.fingerprints:
        p.error("give --events and/or --fingerprints")

    reg = ActorRegistry()
    seen_in = {}     # actor_key -> set(directions)  (for cross-direction detection)
    for ev in _load_dir(args.events) if args.events else []:
        reg.ingest_event(ev)
    for fp in _load_dir(args.fingerprints) if args.fingerprints else []:
        reg.ingest_fingerprint(fp)
    reg.finalize()

    by_dir = {}
    deg = {}
    for e in reg.edges:
        by_dir[e.direction] = by_dir.get(e.direction, 0) + 1
        deg[e.actor_key] = deg.get(e.actor_key, 0) + 1
        seen_in.setdefault(e.actor_key, set()).add(
            "down" if e.direction == "downstream" else "up")

    print(f"actors: {len(reg.actors)}   edges: {len(reg.edges)}   by direction: {by_dir}")
    types = {}
    for a in reg.actors.values():
        types[a.actor_type] = types.get(a.actor_type, 0) + 1
    print(f"actor types: {types}")
    print(f"\ntop {args.top} actors by amplification edges:")
    for key, n in sorted(deg.items(), key=lambda x: -x[1])[:args.top]:
        a = reg.actors[key]
        print(f"  {n:>4}  [{a.actor_type:<12}] {a.display_name[:42]:<42}  {a.key}")
    cross = [k for k, dirs in seen_in.items() if len(dirs) > 1]
    print(f"\nactors appearing in BOTH directions (same node, up+down): {len(cross)}")
    for k in cross[:10]:
        print(f"  [{reg.actors[k].actor_type}] {reg.actors[k].display_name} ({k})")


if __name__ == "__main__":
    main()
