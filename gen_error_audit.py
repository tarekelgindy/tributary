"""
Attestation error-rate audit — draw and tally (2026-09-25)
==========================================================
The corpus-wide precision audit METHODOLOGY has promised since the plan
existed. Draws a seeded random sample of attestations from the published
traces and writes a blind grading sheet with PRE-REGISTERED criteria (the
criteria are written into the sheet before any grading — the Gate-2.6b
gold-instability lesson applied to ourselves).

  python gen_error_audit.py --draw            # audits/error_audit_<date>.md
  python gen_error_audit.py --tally <sheet>   # per-field precision + 95% CI

Unit: one attestation = (URL, quote, date, attributed source) from a
published trace's lexical or conceptual log. Population: asserting entries
(claim_relation != related-context) with a source URL; social spread
excluded (different unit). Five graded fields per item — URL, quote, date,
attribution, relevance — plus role agreement graded SEPARATELY (roles are
the known-weak interpretive layer and must not hide inside, or drag down,
the attestation number). Machines already check URL+quote; the human
audit's real value is attribution, date semantics, and relevance.

Publishing (METHODOLOGY): per-field precision with confidence intervals,
the seed, the date, a failure-anatomy table, and a link to the raw sheet.
The number carries a version; re-draw after major pipeline changes.
"""

import argparse
import json
import math
import random
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED = 20260925
N_DEFAULT = 100

CRITERIA = """## Pre-registered grading criteria — read before grading, do not adjust after

**A. URL resolves** — the source URL loads the cited document, directly or via
the archive link. A paywall or cookie wall counts as RESOLVING if the page is
identifiably the cited document. A domain-root, 404, or wrong-document page fails.

**B. Quote appears** — the recorded quote (or an inconsequentially trimmed
version) appears on the page/archive. Paraphrase fails. Items with no recorded
quote are pre-marked n/a and excluded from this field's denominator.

**C. Date correct at claimed granularity** — the document's own publication
date matches at the precision recorded: a YYYY-01-01 entry claims only the
YEAR; a full date claims the day. Grade against the page/archive, not memory.
A retrospective dated to the period it describes fails.

**D. Attribution correct** — the recorded author/outlet is who the page says
produced it. Wire copy credited to the reprinting outlet fails; an outlet
attribution for an authored piece passes only if the outlet is right.

**E. Attests this narrative** — the page actually uses/asserts/discusses THIS
claim (shown with each item), not merely its topic. Grade the relation the
entry claims: an asserting entry must assert it.

**Role (separate)** — does the role label fairly describe this source's
position in the spread? Grade agree/disagree/unsure; unsure is honest and
excluded from the role denominator.

Mark [x] when the criterion HOLDS. Leave [ ] when it fails. Add a note on any
failure. Do not consult the pipeline's evidence/confidence fields while
grading; grade only the fields shown against the live page or archive.
"""


def draw(n, seed, out_dir):
    rng = random.Random(seed)
    pop = []
    for p in sorted((ROOT / "gallery" / "traces").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        fid = d.get("fingerprint_id") or p.stem
        claim = (d.get("lexical") or {}).get("canonical_phrase", "")
        for lin in ("lexical", "conceptual"):
            for i, e in enumerate(((d.get("genealogy") or {}).get(lin) or {})
                                  .get("attestation_log") or []):
                if (e.get("claim_relation") or "") == "related-context":
                    continue
                if not (e.get("source_url") or "").startswith("http"):
                    continue
                pop.append({
                    "fingerprint_id": fid, "claim": claim, "lineage": lin,
                    "instance_id": e.get("instance_id", f"{fid}:{lin}:{i}"),
                    "date": e.get("date", ""), "author": e.get("author", ""),
                    "source_title": e.get("source_title", ""),
                    "source_url": e.get("source_url", ""),
                    "archive_url": e.get("archive_url", ""),
                    "exact_quote": e.get("exact_quote", ""),
                    "amplifier_role": e.get("amplifier_role", "unknown"),
                })
    sample = pop if len(pop) <= n else rng.sample(pop, n)
    rng.shuffle(sample)

    out_dir.mkdir(parents=True, exist_ok=True)
    day = date.today().isoformat()
    sheet = out_dir / f"error_audit_{day}.md"
    manifest = out_dir / f"error_audit_{day}.manifest.json"

    lines = [
        f"# Attestation error-rate audit — drawn {day}",
        "",
        f"Seed {seed} · sample {len(sample)} of {len(pop)} eligible attestations "
        f"(asserting entries with a source URL, lexical + conceptual, across "
        f"{len(set(x['fingerprint_id'] for x in pop))} published traces; "
        f"social spread excluded).",
        "",
        CRITERIA,
        "---",
        "",
    ]
    for k, x in enumerate(sample, 1):
        quote = (x["exact_quote"] or "").strip()
        lines += [
            f"### {k}. {x['date'] or '(undated)'} · {x['lineage']} · trace {x['fingerprint_id']}",
            f"Claim under trace: “{x['claim']}”",
            f"Recorded source: {x['author'] or '(no author)'} — {x['source_title'] or '(no title)'}",
            f"URL: {x['source_url']}",
        ]
        if x["archive_url"]:
            lines.append(f"Archive: {x['archive_url']}")
        lines.append(f"Quote: “{quote}”" if quote
                      else "Quote: (none recorded)")
        lines += [
            "- [ ] A. URL resolves",
            ("- [ ] B. Quote appears" if quote
             else "- B: n/a (no quote recorded)"),
            "- [ ] C. Date correct at claimed granularity",
            "- [ ] D. Attribution correct",
            "- [ ] E. Attests this narrative",
            f"- Role shown: `{x['amplifier_role']}` — [ ] agree · [ ] disagree · [ ] unsure",
            "- Notes:",
            "",
        ]
    sheet.write_text("\n".join(lines), encoding="utf-8")
    manifest.write_text(json.dumps(
        {"drawn": day, "seed": seed, "n": len(sample), "population": len(pop),
         "items": sample}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[audit] {len(sample)} items -> {sheet}")
    print(f"[audit] manifest -> {manifest}")
    print("[audit] grade blind against the live pages, then: "
          f"python gen_error_audit.py --tally {sheet}")


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


def tally(sheet_path):
    text = Path(sheet_path).read_text(encoding="utf-8")
    items = re.split(r"^### \d+\. ", text, flags=re.M)[1:]
    fields = {"A": [0, 0], "B": [0, 0], "C": [0, 0], "D": [0, 0], "E": [0, 0]}
    role = {"agree": 0, "disagree": 0, "unsure": 0}
    fails = {f: [] for f in fields}
    for idx, it in enumerate(items, 1):
        for f in fields:
            m = re.search(r"- \[( |x|X)\] %s\." % f, it)
            if m:
                fields[f][1] += 1
                if m.group(1).lower() == "x":
                    fields[f][0] += 1
                else:
                    fails[f].append(idx)
        rm = re.findall(r"\[( |x|X)\] (agree|disagree|unsure)", it)
        for mark, lab in rm:
            if mark.lower() == "x":
                role[lab] += 1
    print(f"Attestation precision (n = {len(items)} items) — 95% Wilson CIs\n")
    names = {"A": "URL resolves", "B": "Quote appears", "C": "Date correct",
             "D": "Attribution correct", "E": "Attests narrative"}
    for f, (k, n) in fields.items():
        p, lo, hi = wilson(k, n)
        print(f"  {f}. {names[f]:<22} {k:>3}/{n:<3}  {p:6.1%}  [{lo:.1%} – {hi:.1%}]"
              + (f"   failed items: {fails[f]}" if fails[f] else ""))
    rn = role["agree"] + role["disagree"]
    if rn:
        p, lo, hi = wilson(role["agree"], rn)
        print(f"\n  Role agreement (separate, unsure excluded): "
              f"{role['agree']}/{rn}  {p:6.1%}  [{lo:.1%} – {hi:.1%}] "
              f"(unsure: {role['unsure']})")
    print("\nPublish per METHODOLOGY: table + seed + date + failure anatomy "
          "+ link to this sheet. The number carries a version.")


def main():
    ap = argparse.ArgumentParser(description="Attestation error-rate audit.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--draw", action="store_true")
    mode.add_argument("--tally", metavar="SHEET")
    ap.add_argument("--n", type=int, default=N_DEFAULT)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out-dir", default=str(ROOT / "audits"))
    a = ap.parse_args()
    if a.draw:
        draw(a.n, a.seed, Path(a.out_dir))
    else:
        tally(a.tally)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
