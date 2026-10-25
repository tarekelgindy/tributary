"""
Trace update pass (2026-10-25, Batch C) — append-only freshness
===============================================================
A trace is a snapshot; breaking-claim traces age fastest. This maintainer
tool re-runs ONLY the phrasing search for a published trace and appends
instances the record doesn't have yet — strictly append-only:

  - existing entries are never edited, re-dated, re-roled, or removed
    (hand corrections are settled history);
  - only instances with a NEW source_url and a date on/after the current
    anchor are appended (anything claiming to predate the anchor is a
    re-anchoring question for a human, printed but not applied);
  - appended entries pass the deterministic guards (embed-recovery,
    snowflake, examiner regex) and can never take an originator crown
    (a later instance cannot coin what exists);
  - stats re-derive; last_updated bumps; verification left to the
    archiving crawl.

    python update_trace.py <fingerprint_id>            # DRY RUN
    python update_trace.py <fingerprint_id> --apply    # append + republish

Cost: one earliest-uses search (~$0.10-0.25). Run publish.py + cards.py
--fp <id> after --apply (the tool reminds you).
"""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent


async def fetch_new_instances(fp: dict) -> list:
    from fingerprint import (FingerprintGenerator, _embed_recovery_pass,
                             _role_sanity_pass, _snowflake_sanity_pass)
    from models import AmplifierRole, LexicalLayer, Scope

    lexd = fp["lexical"]
    lexical = LexicalLayer(
        canonical_phrase=lexd.get("canonical_phrase") or "",
        phrase_variants=lexd.get("phrase_variants") or [],
        diagnostic_ngrams=lexd.get("diagnostic_ngrams") or [],
        stopword_stripped_signature=lexd.get("stopword_stripped_signature") or "")
    scope = Scope(language="en", region=(fp.get("scope") or {}).get("region") or "US")
    gen = FingerprintGenerator(max_searches=4)
    found = await gen.search_earliest_uses(lexical, scope)
    _embed_recovery_pass(found)
    _snowflake_sanity_pass(found)
    _role_sanity_pass(found)
    for i in found:   # a later instance cannot coin what exists
        if i.amplifier_role == AmplifierRole.ORIGINATOR:
            i.amplifier_role = AmplifierRole.EARLY_AMPLIFIER
            i.role_evidence = ((i.role_evidence or "") +
                " [update pass: appended instances never take a crown — the "
                "published lineage's origin stands.]").strip()
    return found


def main():
    ap = argparse.ArgumentParser(description="Append-only freshness update for a published trace.")
    ap.add_argument("fingerprint_id")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    p = ROOT / "gallery" / "traces" / f"{args.fingerprint_id}.json"
    fp = json.loads(p.read_text(encoding="utf-8"))
    lex = fp["genealogy"]["lexical"]
    log = lex.get("attestation_log") or []
    known_urls = {str(a.get("source_url") or "").strip().rstrip("/")
                  for a in log if a.get("source_url")}
    anchor = str(lex.get("first_attested_date") or "")

    found = asyncio.run(fetch_new_instances(fp))
    appended, earlier = [], []
    for i in found:
        u = str(i.source_url or "").strip().rstrip("/")
        if not u or u in known_urls:
            continue
        d = str(i.date or "")[:10]
        if anchor and d and d < anchor:
            earlier.append(i)
            continue
        appended.append(i)

    print(f"{args.fingerprint_id}: {len(found)} found, {len(appended)} new "
          f"(post-anchor, novel URL), {len(earlier)} claim to predate the anchor")
    for i in appended:
        print(f"  + {i.date}  {str(i.amplifier_role.value):18} {(i.source_title or '')[:64]}")
    for i in earlier:
        print(f"  ! PRE-ANCHOR (not applied — human call): {i.date}  "
              f"{(i.source_title or '')[:58]}  {(i.source_url or '')[:70]}")

    if args.apply and appended:
        from apply_referent_bound import _stats_dict
        for i in appended:
            d = i.__dict__.copy()
            d["amplifier_role"] = i.amplifier_role.value
            d.setdefault("claim_relation", "")
            d.setdefault("verified", False)
            d.setdefault("verification_status", "unchecked")
            d.setdefault("verification_notes", "appended by the update pass; verification via the archiving crawl")
            d.setdefault("provenance", {"origin": "ai", "status": "ai_generated",
                                        "reasoning": "update pass (append-only), 2026-10-25"})
            log.append(d)
        lex["timeline_stats"] = _stats_dict(lex)
        from datetime import datetime, timezone
        fp["last_updated"] = datetime.now(timezone.utc).isoformat()
        p.write_text(json.dumps(fp, indent=2, ensure_ascii=False, default=str),
                     encoding="utf-8")
        print(f"\nappended {len(appended)} — now: python publish.py && "
              f"python cards.py --fp {args.fingerprint_id}")


if __name__ == "__main__":
    main()
