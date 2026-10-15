"""
Apply the referent-bound invariant to the PUBLISHED corpus (2026-10-14)
=======================================================================
New traces get this in-pipeline; existing traces predate the referent field.
For each published trace: extract the claim's referent (one Haiku call,
~$0.001, skipped if already stored), then enforce the invariant on the
stored JSON — any asserting entry dated before the referent's existence is
filed as related-context, and the lineage re-anchors.

    python apply_referent_bound.py            # DRY RUN — table only
    python apply_referent_bound.py --apply    # write + re-anchor + notes

Hand-corrected traces should report 0 demotions (a consistency check);
form-general claims (no referent) are no-ops. Run publish.py and re-render
cards for changed traces after --apply.
"""

import argparse
import json
from datetime import date
from pathlib import Path

import anthropic

from fingerprint import HAIKU, REFERENT_BOUND_NOTE, _parse_json_safe

ROOT = Path(__file__).resolve().parent

REFERENT_SYSTEM = """\
You extract the REFERENT of a claim, if it has one. A referent is the ONE
proper-named thing or specific event the claim is about — a named film,
book, incident, or a named person's specific act. A descriptive designation
("the poorest billionaire in California") is a category slot, NOT a
referent; statistics, generalizations, and folk wisdom have no referent.
Output ONLY JSON:
{"description": "<the referent, or empty string>",
 "date": "<the referent's own date (release/event/act) as the EARLIEST
plausible ISO date at known precision, e.g. '1986-01-01' for a 1986 film;
empty if no referent or unknowable>",
 "basis": "<= 10 words: how you know the date>"}
Leave fields empty rather than guessing."""


def extract_referent(client, claim: str) -> dict:
    resp = client.messages.create(
        model=HAIKU, max_tokens=200,
        system=[{"type": "text", "text": REFERENT_SYSTEM}],
        messages=[{"role": "user", "content": json.dumps({"claim": claim})}])
    out = _parse_json_safe("".join(b.text for b in resp.content
                                   if getattr(b, "type", "") == "text")) or {}
    ref = {k: str(out.get(k, "")).strip() for k in ("description", "date", "basis")}
    return ref if ref["description"] else {}


def bound_pass_dict(fp: dict, ref: dict) -> tuple:
    """The same invariant as fingerprint._referent_bound_pass, on the stored
    dict form. Returns (demoted_count, per_layer_summary)."""
    try:
        bound = date.fromisoformat(str(ref.get("date") or "")[:10])
    except ValueError:
        return 0, ""
    demoted, bits = 0, []
    for layer in ("lexical", "conceptual"):
        gl = (fp.get("genealogy") or {}).get(layer) or {}
        log = gl.get("attestation_log") or []
        hit = 0
        for a in log:
            if (a.get("claim_relation") or "") == "related-context":
                continue
            try:
                d = date.fromisoformat(str(a.get("date") or "")[:10])
            except ValueError:
                continue
            if d < bound:
                a["claim_relation"] = "related-context"
                a["evidence"] = (str(a.get("evidence") or "")).rstrip() + \
                    REFERENT_BOUND_NOTE.format(
                        ref_desc=ref.get("description", ""),
                        ref_date=str(ref.get("date"))[:10])
                hit += 1
        asserting = sorted(
            (a for a in log
             if (a.get("claim_relation") or "") != "related-context" and a.get("date")),
            key=lambda a: a["date"])
        if hit and asserting:
            first = asserting[0]
            if str(gl.get("first_attested_date") or "") < first["date"]:
                gl["first_attested_date"] = first["date"]
                gl["first_attested_source"] = first.get("source_url", "")
                gl["primary_origin_id"] = first.get("instance_id", "")
                gl["attestation_confidence"] = float(first.get("confidence") or 0)
                if gl.get("status") != "unknown":
                    gl["status"] = ("single-origin"
                                    if first.get("amplifier_role") == "originator"
                                    else "earliest-found")
        demoted += hit
        if hit:
            bits.append(f"{layer}:{hit}")
    return demoted, " ".join(bits)


def main():
    ap = argparse.ArgumentParser(description="Referent-bound invariant over the corpus.")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()
    client = anthropic.Anthropic()

    changed = 0
    for p in sorted((ROOT / "gallery" / "traces").glob("*.json")):
        fp = json.loads(p.read_text(encoding="utf-8"))
        claim = (fp.get("lexical") or {}).get("canonical_phrase") or ""
        con = fp.setdefault("conceptual", {})
        ref = con.get("referent") or {}
        if not ref:
            ref = extract_referent(client, claim)
            con["referent"] = ref
        tag = f'{ref.get("description", "")[:44]} @ {ref.get("date", "")}' if ref else "(form-general)"
        demoted, bits = bound_pass_dict(fp, ref) if ref else (0, "")
        print(f"{p.stem}  {claim[:42]:42}  ref={tag:52}  demote={demoted} {bits}")
        if args.apply and (demoted or con.get("referent") != {}):
            p.write_text(json.dumps(fp, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
            changed += bool(demoted)
    if args.apply:
        print(f"\napplied; {changed} trace(s) had demotions — run publish.py and "
              f"re-render cards for those ids")


if __name__ == "__main__":
    main()
