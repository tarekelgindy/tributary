"""
Apply the deterministic sanity passes to the PUBLISHED corpus (2026-10-19)
==========================================================================
New traces get these in-pipeline (since 2026-10-18/19); the corpus predates
them. Per trace, per layer, in pipeline order: embed-recovery (placeholder
X URLs recovered from the embedded posts in their citing articles), then
snowflake dates, the examiner-title regex, and the one-crown rule — plus a
conservative date-precision backfill (entries dated Jan-1 with no precision
and no month evidence are marked year-precision; display softens, nothing
moves). Timeline stats re-derive and anchors re-source when a layer changes.

    python apply_sanity_passes.py            # DRY RUN — table only
    python apply_sanity_passes.py --apply    # write changes

Run publish.py and re-render cards for changed ids after --apply; add
CORRECTIONS rows for any published-output role changes.
"""

import argparse
import json
import re
from pathlib import Path
from types import SimpleNamespace as NS

from apply_referent_bound import _stats_dict
from fingerprint import (_NEG_TOKENS, _embed_recovery_pass, _role_sanity_pass,
                         _single_originator_pass, _snowflake_sanity_pass,
                         _url_date_sanity_pass)
from models import AmplifierRole

ROOT = Path(__file__).resolve().parent

_MONTH_RE = re.compile(
    r"january|february|march|april|may|june|july|august|september|october|"
    r"november|december|\d{4}-\d{2}-(?:0[2-9]|[12]\d|3[01])", re.IGNORECASE)


def _precision_backfill(log: list) -> int:
    """Mark year-precision where the evidence can't support a day: date is
    Jan 1, no precision recorded, and neither evidence nor title mentions a
    month or a non-Jan-1 full date. Conservative: presentation-only field."""
    n = 0
    for a in log:
        if (not a.get("date_precision")
                and re.fullmatch(r"\d{4}-01-01", str(a.get("date") or ""))
                and not _MONTH_RE.search(
                    f"{a.get('evidence', '')} {a.get('source_title', '')}")):
            a["date_precision"] = "year"
            n += 1
    return n


_DEFAULTS = {"date": "", "date_precision": "", "evidence": "", "confidence": 0.0,
             "role_evidence": "", "source_url": "", "cited_via": "",
             "source_title": "", "claim_relation": "", "exact_quote": "",
             "lexical_form_seen": "", "instance_id": ""}


def sweep_trace(fp: dict, details: list) -> dict:
    # REBUTTAL-POLARITY GUARD: the examiner-title regex is polarity-blind by
    # accepted design, but on a rebuttal trace a deny/debunk title ASSERTS
    # the claim (the Mayo inversion) — and this corpus's rebuttal traces
    # carry hand corrections the regex would undo. Skip the regex there.
    is_rebuttal = any(t in f" {fp['lexical']['canonical_phrase'].lower()} "
                      for t in _NEG_TOKENS)
    out = {}
    for layer in ("lexical", "conceptual"):
        g = (fp.get("genealogy") or {}).get(layer) or {}
        log = g.get("attestation_log") or []
        if not log:
            continue
        objs = [NS(**{**_DEFAULTS, **a,
                      "amplifier_role":
                      AmplifierRole(a.get("amplifier_role") or "unknown")})
                for a in log]
        rec = _embed_recovery_pass(objs)
        udate = _url_date_sanity_pass(objs)
        snow = _snowflake_sanity_pass(objs)
        pre = [o.amplifier_role for o in objs]
        if not is_rebuttal:
            _role_sanity_pass(objs)
        crowns = _single_originator_pass(objs)
        for a, o, before in zip(log, objs, pre):
            if o.amplifier_role != before:
                details.append(f"    {layer}  {o.date}  {before.value} -> "
                               f"{o.amplifier_role.value}  {(o.source_title or '')[:58]}")
            a.update(date=o.date, date_precision=o.date_precision,
                     evidence=o.evidence, confidence=o.confidence,
                     role_evidence=o.role_evidence, source_url=o.source_url,
                     amplifier_role=o.amplifier_role.value)
        regex_crit = sum(1 for before, o in zip(pre, objs)
                         if o.amplifier_role == AmplifierRole.CRITIC != before)
        prec = _precision_backfill(log)
        changed = rec or udate or snow or crowns or regex_crit
        if changed:
            # anchors follow their entries (url/date may have moved)
            asserting = sorted(
                (a for a in log
                 if (a.get("claim_relation") or "") != "related-context"
                 and a.get("date")), key=lambda a: a["date"])
            if asserting:
                first = asserting[0]
                g["first_attested_date"] = first["date"]
                g["first_attested_source"] = first.get("source_url", "")
                g["primary_origin_id"] = first.get("instance_id", "")
                g["attestation_confidence"] = float(first.get("confidence") or 0)
            g["timeline_stats"] = _stats_dict(g)
        if changed or prec:
            out[layer] = {"recovered": rec, "url_date": udate, "snowflake": snow,
                          "crowns_demoted": crowns, "regex_critic": regex_crit,
                          "precision_marked": prec}
    return out


def main():
    ap = argparse.ArgumentParser(description="Deterministic sanity passes over the corpus.")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()
    changed_ids = []
    for p in sorted((ROOT / "gallery" / "traces").glob("*.json")):
        fp = json.loads(p.read_text(encoding="utf-8"))
        details = []
        rep = sweep_trace(fp, details)
        if rep:
            print(f"{p.stem}  {fp['lexical']['canonical_phrase'][:44]:44}  {rep}")
            for line in details:
                print(line)
            structural = any(v["recovered"] or v["snowflake"] or v["crowns_demoted"]
                             or v["regex_critic"] for v in rep.values())
            if args.apply:
                p.write_text(json.dumps(fp, indent=2, ensure_ascii=False,
                                        default=str), encoding="utf-8")
                if structural:
                    changed_ids.append(p.stem)
    if args.apply:
        print(f"\napplied; structural changes in: {changed_ids or 'none'} — "
              f"run publish.py and re-render cards for those ids")


if __name__ == "__main__":
    main()
