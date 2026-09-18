"""
Add the conceptual (idea) lineage to already-published traces, in place
=======================================================================
The public request path generates lean traces (phrasing lineage + evidence,
no idea lineage). This upgrades a published trace by running ONLY the
conceptual pass — search + adversarial check + role sanity (all inside
generate_lineage_conceptual) + URL verification + timeline stats — and
grafting the result onto every published copy (standalone and
event-embedded). The lexical log, evidence landscape, and any human
corrections/contributions are untouched; a full --force regeneration would
re-search those layers and could reintroduce corrected errors.

    python upgrade_conceptual.py <fp_id> [<fp_id> ...] [--max-searches N]

Cost: one Sonnet+web_search pass + one adversarial pass, ~$0.30/trace at
the default search cap (4, matching the request path's lean recipe).
Refuses to overwrite a non-empty conceptual log unless --force.
API key: ANTHROPIC_API_KEY from the environment, else read from .env.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GALLERY = ROOT / "gallery"


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
    sys.exit("[upgrade] no ANTHROPIC_API_KEY in environment or .env")


async def upgrade_one(fp_id, max_searches):
    import httpx
    from contribute import find_copies, get_fp
    from fingerprint import (FingerprintGenerator, compute_timeline_stats,
                             _verify_attested, _VERIFY_USER_AGENT,
                             _VERIFY_TIMEOUT)
    from models import ConceptualLayer, Scope

    copies = find_copies(GALLERY, fp_id)
    if not copies:
        print(f"[upgrade] {fp_id}: no published copy found — skipped")
        return False
    base = json.loads(copies[0][0].read_text(encoding="utf-8"))
    fp = get_fp(base, copies[0][1], fp_id)

    existing = (fp.get("genealogy", {}).get("conceptual") or {}) \
        .get("attestation_log") or []
    if existing and not ARGS.force:
        print(f"[upgrade] {fp_id}: conceptual log already has "
              f"{len(existing)} entries — skipped (use --force)")
        return False

    l2 = fp.get("conceptual") or {}
    conceptual = ConceptualLayer(
        claim_predicate=l2.get("claim_predicate", ""),
        entities=l2.get("entities") or {},
        causal_structure=l2.get("causal_structure", ""),
    )
    if not conceptual.claim_predicate:
        print(f"[upgrade] {fp_id}: no stored claim_predicate (L2) — "
              f"cannot trace the idea without regenerating; skipped")
        return False
    sc = fp.get("scope") or {}
    scope = Scope(language=sc.get("language", "en"),
                  region=sc.get("region", "US"),
                  time_window_start=sc.get("time_window_start", ""),
                  time_window_end=sc.get("time_window_end", ""))

    print(f"[upgrade] {fp_id}: tracing idea — "
          f"\"{conceptual.claim_predicate[:90]}\"")
    gen = FingerprintGenerator(max_searches=max_searches)
    record = await gen.generate_lineage_conceptual(conceptual, scope)

    if record.attestation_log:
        headers = {"User-Agent": _VERIFY_USER_AGENT}
        async with httpx.AsyncClient(headers=headers,
                                     timeout=_VERIFY_TIMEOUT) as http:
            await asyncio.gather(
                *[_verify_attested(http, i, check_quote=False)
                  for i in record.attestation_log],
                return_exceptions=True)
    record.timeline_stats = compute_timeline_stats(record)

    new_con = record.to_dict()
    now = datetime.now(timezone.utc).isoformat()
    for path, kind in copies:
        doc = json.loads(path.read_text(encoding="utf-8"))
        target = get_fp(doc, kind, fp_id)
        target.setdefault("genealogy", {})["conceptual"] = new_con
        target["last_updated"] = now
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False),
                        encoding="utf-8")
        print(f"[upgrade] {fp_id}: wrote {kind} copy {path}")

    n = len(record.attestation_log)
    ok = sum(1 for i in record.attestation_log
             if (getattr(i, "verification_status", "") or "") in
             ("verified", "url-ok"))
    status = getattr(record.status, "value", record.status)
    print(f"[upgrade] {fp_id}: {status} · first attested "
          f"{record.first_attested_date or '(none)'} · {n} entries "
          f"({ok} URL-checked ok)")
    for i in record.attestation_log:
        rel = getattr(i, "claim_relation", "") or ""
        print(f"    {i.date or '????':>10}  {(i.author or i.source_title)[:48]:<48} "
              f"role={getattr(i.amplifier_role, 'value', i.amplifier_role)}"
              f"{'  [' + rel + ']' if rel else ''}"
              f"  conf={i.confidence}  verify={i.verification_status or '-'}")
    return True


async def main():
    load_key()
    done = 0
    for fp_id in ARGS.fp_ids:            # sequential: search-heavy calls
        try:
            done += bool(await upgrade_one(fp_id, ARGS.max_searches))
        except Exception as e:           # one failure must not kill the batch
            print(f"[upgrade] {fp_id}: FAILED — {type(e).__name__}: {e}")
    print(f"[upgrade] {done}/{len(ARGS.fp_ids)} upgraded. Next: audit the "
          f"entries above, then python publish.py && python cards.py --fp <id>")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Graft a conceptual lineage onto published traces.")
    ap.add_argument("fp_ids", nargs="+", help="12-hex fingerprint ids")
    ap.add_argument("--max-searches", type=int, default=4)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing non-empty conceptual log")
    ARGS = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
