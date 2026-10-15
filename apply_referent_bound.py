"""
Apply the referent-bound invariant to the PUBLISHED corpus (2026-10-14)
=======================================================================
New traces get this in-pipeline; existing traces predate the referent field.
For each published trace: extract the claim's referent (one Haiku call,
~$0.001, skipped if already stored), then enforce the invariant on the
stored JSON — any asserting entry dated before the referent's existence is
filed as related-context, and the lineage re-anchors.

Two-stage extraction (2026-10-14, same day): claims typed DESCRIPTIVELY
("a sushi chain owner...", "plague outbreak in Russia") name nothing, so
text-only extraction calls them form-general — yet every found instance may
concern one specific incident, which makes them referent-specific in
practice (the bioweapons and Kimura failure mode). When stage 1 yields no
dated referent, stage 2 shows the model the trace's own earliest asserting
instances and asks whether they resolve to one referent. Dry-run prints
every entry a demotion would touch — review them before --apply.

    python apply_referent_bound.py            # DRY RUN — table only
    python apply_referent_bound.py --apply    # write + re-anchor + notes

Hand-corrected traces should report 0 demotions (a consistency check);
form-general claims (no referent) are no-ops. Run publish.py and re-render
cards for changed traces after --apply.
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import anthropic

from fingerprint import (HAIKU, REFERENT_BOUND_NOTE, _parse_json_safe,
                         compute_timeline_stats)

ROOT = Path(__file__).resolve().parent


def load_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip().removeprefix("export ").lstrip()
            if line.startswith("ANTHROPIC_API_KEY"):
                os.environ["ANTHROPIC_API_KEY"] = \
                    line.split("=", 1)[1].strip().strip('"').strip("'")
                return
    sys.exit("no ANTHROPIC_API_KEY in environment or .env")

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


def _norm_date(d: str) -> str:
    """Year or year-month outputs are honest precision but invalid for
    date.fromisoformat — pad to the earliest day they denote."""
    d = d.strip()
    if re.fullmatch(r"\d{4}", d):
        return d + "-01-01"
    if re.fullmatch(r"\d{4}-\d{2}", d):
        return d + "-01"
    return d


def extract_referent(client, claim: str) -> dict:
    resp = client.messages.create(
        model=HAIKU, max_tokens=200,
        system=[{"type": "text", "text": REFERENT_SYSTEM}],
        messages=[{"role": "user", "content": json.dumps({"claim": claim})}])
    out = _parse_json_safe("".join(b.text for b in resp.content
                                   if getattr(b, "type", "") == "text")) or {}
    ref = {k: str(out.get(k, "")).strip() for k in ("description", "date", "basis")}
    ref["date"] = _norm_date(ref["date"])
    return ref if ref["description"] else {}


REFERENT_IMPLICIT_SYSTEM = """\
You resolve an IMPLICIT referent. A claim was typed descriptively — its text
names no specific thing — but the attested instances found for it may all
concern ONE specific incident, work, or named person's specific act. If so,
that thing IS the claim's referent, and nothing predating it can assert the
claim. You get the claim and its earliest found instances (date, title,
phrasing). Decide: do the instances concern one specific identifiable
referent, or do they genuinely span different subjects, incidents, or eras
(a form-general claim — stereotypes, folk wisdom, perennial assertions)?
Output ONLY JSON:
{"description": "<the one specific referent the instances concern, or empty
string if they span subjects or you are unsure>",
 "date": "<the referent's OWN date (the incident/act/release itself, not the
earliest report about it) as the EARLIEST plausible ISO date at known
precision; empty if unknowable>",
 "basis": "<= 12 words: how the instances identify and date it>"}
The referent's date must not be LATER than the earliest instance that
concerns it. Leave fields empty rather than guessing."""


def resolve_implicit_referent(client, claim: str, fp: dict) -> dict:
    """Stage 2: when the claim text names nothing, ask whether the trace's
    own asserting instances resolve to one specific referent."""
    digest = []
    for layer in ("lexical", "conceptual"):
        log = ((fp.get("genealogy") or {}).get(layer) or {}).get("attestation_log") or []
        for a in log:
            if (a.get("claim_relation") or "") == "related-context":
                continue
            d = str(a.get("date") or "")[:10]
            if not d:
                continue
            digest.append({"date": d,
                           "title": str(a.get("source_title") or "")[:80],
                           "phrasing": str(a.get("lexical_form_seen")
                                           or a.get("exact_quote") or "")[:120]})
    digest = sorted(digest, key=lambda x: x["date"])[:12]
    if not digest:
        return {}
    resp = client.messages.create(
        model=HAIKU, max_tokens=250,
        system=[{"type": "text", "text": REFERENT_IMPLICIT_SYSTEM}],
        messages=[{"role": "user", "content": json.dumps(
            {"claim": claim, "instances": digest})}])
    out = _parse_json_safe("".join(b.text for b in resp.content
                                   if getattr(b, "type", "") == "text")) or {}
    ref = {k: str(out.get(k, "")).strip() for k in ("description", "date", "basis")}
    ref["date"] = _norm_date(ref["date"])
    if not (ref["description"] and ref["date"]):
        return {}
    ref["basis"] = ("resolved from the trace's own instances: " + ref["basis"]).strip()
    return ref


def _stats_dict(gl: dict) -> dict:
    """compute_timeline_stats is attribute-based; adapt the stored dict form."""
    rec = SimpleNamespace(attestation_log=[SimpleNamespace(
        claim_relation=a.get("claim_relation") or "",
        date=a.get("date") or "",
        amplifier_role=a.get("amplifier_role") or "",
    ) for a in (gl.get("attestation_log") or [])])
    return compute_timeline_stats(rec)


def bound_pass_dict(fp: dict, ref: dict) -> tuple:
    """The same invariant as fingerprint._referent_bound_pass, on the stored
    dict form. Returns (demoted_count, per_layer_summary, hit_details)."""
    try:
        bound = date.fromisoformat(str(ref.get("date") or "")[:10])
    except ValueError:
        return 0, "", []
    demoted, bits, details = 0, [], []
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
                details.append(f"{layer}  {a.get('date')}  "
                               f"{str(a.get('source_title') or '')[:60]}")
                hit += 1
        asserting = sorted(
            (a for a in log
             if (a.get("claim_relation") or "") != "related-context" and a.get("date")),
            key=lambda a: a["date"])
        layer_changed = bool(hit)
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
                bits.append(f"{layer}:anchor→{first['date']}")
        elif not asserting and log and (
                gl.get("first_attested_date")
                or (gl.get("status") or "") not in ("", "unknown")):
            # nothing asserts: the record cannot claim an anchor (the Milo
            # shape, abd11e95d1c5) — also repairs a stale anchor left on a
            # layer whose entries are already all related-context
            gl["first_attested_date"] = ""
            gl["first_attested_source"] = ""
            gl["primary_origin_id"] = ""
            gl["attestation_confidence"] = 0.0
            gl["status"] = "unknown"
            bits.append(f"{layer}:anchor-cleared")
            layer_changed = True
        if layer_changed:
            gl["timeline_stats"] = _stats_dict(gl)
        demoted += hit
        if hit:
            bits.append(f"{layer}:{hit}")
    return demoted, " ".join(bits), details


def main():
    ap = argparse.ArgumentParser(description="Referent-bound invariant over the corpus.")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()
    load_key()
    client = anthropic.Anthropic()

    changed = 0
    for p in sorted((ROOT / "gallery" / "traces").glob("*.json")):
        fp = json.loads(p.read_text(encoding="utf-8"))
        claim = (fp.get("lexical") or {}).get("canonical_phrase") or ""
        con = fp.setdefault("conceptual", {})
        ref = con.get("referent") or {}
        src = "stored"
        if not ref:
            # a stored referent (pipeline L2 or hand-seeded) is authoritative,
            # including a deliberate no-date one — only fresh extractions cascade
            ref = extract_referent(client, claim)
            src = "text"
            if not ref.get("date"):
                implicit = resolve_implicit_referent(client, claim, fp)
                if implicit:
                    ref, src = implicit, "implicit"
        con["referent"] = ref
        tag = (f'{ref.get("description", "")[:44]} @ {ref.get("date", "")} [{src}]'
               if ref else "(form-general)")
        demoted, bits, details = (bound_pass_dict(fp, ref)
                                  if ref.get("date") else (0, "", []))
        print(f"{p.stem}  {claim[:42]:42}  ref={tag:58}  demote={demoted} {bits}")
        verb = "demoted      " if args.apply else "would demote "
        for line in details:
            print(f"    {verb} {line}")
        if args.apply and (bits or ref):
            p.write_text(json.dumps(fp, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
            changed += bool(bits)
    if args.apply:
        print(f"\napplied; {changed} trace(s) changed (demotions or anchor repair) "
              f"— run publish.py and re-render cards for those ids")


if __name__ == "__main__":
    main()
