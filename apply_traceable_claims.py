"""
Backfill traceable_claim onto published framings (2026-10-26)
=============================================================
New events emit it at generation; the 892 existing framings predate the
field. For each framing without one: a Haiku call restates the framing's
QUESTION as one self-contained declarative sentence that names the event's
specific referent — Tarek's spec, verbatim example in the prompt. Resumable
(framings that already have the field are skipped); ~$0.001 per framing.

    python apply_traceable_claims.py --sample 8     # dry-run a review sample
    python apply_traceable_claims.py --apply        # backfill the corpus
"""

import argparse
import json
from pathlib import Path

import anthropic

from apply_referent_bound import load_key
from fingerprint import _parse_json_safe

ROOT = Path(__file__).resolve().parent

SYSTEM = """\
You restate a news-framing's QUESTION as its asserted claim, for tracing.
You get an event description, the framing's question, and its key claim.
Output ONE self-contained declarative sentence that:
  - keeps the QUESTION's own wording as closely as possible, converted to a
    statement (the framing answers its question with YES — state that);
  - names the event's specific referent (actors, incident, place) so the
    sentence makes sense with no surrounding context;
  - stays neutral in register (no added adjectives or editorializing beyond
    what the question itself carries);
  - is never a question, has no pronouns needing the event to resolve
    ("this attack" -> the named attack), and stays under ~45 words.
Example: event about a gunman attack in central Israel; question "Is the
Israeli far-right using this attack to advance a pre-existing political
agenda, including the death penalty law?" ->
"The Israeli far-right is using the gunman attack in central Israel to
advance a pre-existing political agenda, including the death penalty law."
Output ONLY JSON: {"traceable_claim": "<the sentence>"}"""


def restate(client, event: str, question: str, key_claim: str) -> str:
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=200,
        system=[{"type": "text", "text": SYSTEM}],
        messages=[{"role": "user", "content": json.dumps(
            {"event": event[:400], "question": question, "key_claim": key_claim},
            ensure_ascii=False)}])
    out = _parse_json_safe("".join(b.text for b in resp.content
                                   if getattr(b, "type", "") == "text")) or {}
    tc = str(out.get("traceable_claim") or "").strip()
    return "" if (not tc or tc.endswith("?")) else tc[:300]


def main():
    ap = argparse.ArgumentParser(description="Backfill traceable_claim on published framings.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sample", type=int, default=0, help="dry-run: restate N framings and print")
    args = ap.parse_args()
    load_key()
    client = anthropic.Anthropic()

    done = shown = 0
    for p in sorted((ROOT / "gallery" / "events").glob("*.json")):
        ev = json.loads(p.read_text(encoding="utf-8"))
        dirty = False
        for fr in ev.get("framings") or []:
            if (fr.get("traceable_claim") or "").strip():
                continue
            q = (fr.get("question") or "").strip()
            if not q:
                continue
            if args.sample and shown >= args.sample:
                break
            tc = restate(client, ev.get("event") or "", q, fr.get("key_claim") or "")
            if not tc:
                continue
            if args.sample:
                shown += 1
                print(f"Q : {q[:90]}\nTC: {tc}\n")
                continue
            if args.apply:
                fr["traceable_claim"] = tc
                dirty = True
                done += 1
        if args.apply and dirty:
            p.write_text(json.dumps(ev, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
        if args.sample and shown >= args.sample:
            break
    if args.apply:
        print(f"backfilled {done} framings — run publish.py")


if __name__ == "__main__":
    main()
