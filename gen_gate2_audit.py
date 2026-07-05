"""Gate 2 audit pack: sample omission claims from an agenda report for manual audit.

ROADMAP Gate 2 (rerun, sitemap-based): for 10 claimed "never mentioned" items,
manually search the outlet's own site. Require >=9/10 to hold before any
omission claim is published. A false "they never covered it" is Tributary's
single most damaging possible error.

This script does the mechanical half: it samples 10 sitemap-based omission
claims from a report JSON (deterministically, seeded by report_id, capped at
2 per outlet so one bad sitemap can't dominate the audit) and writes a
fill-in audit file. The human half — searching each outlet's own site — is
deliberately manual: the gate exists to check the instrument against the
world, not against another instrument.

Usage:
    python gen_gate2_audit.py                      # newest report in agenda/reports/
    python gen_gate2_audit.py --report <path> --n 10
    python gen_gate2_audit.py --tally <audit.json> # score a filled-in audit

Verdicts (fill into the JSON, or tick the .md and transcribe):
    held       — searched the outlet's site + a site: query; no article on this
                 story in the report window. The omission claim stands.
    failed     — found an article covering the story in-window. Paste the URL
                 into evidence_url. The claim is false.
    unclear    — adjacent/ambiguous coverage; explain in notes. Counts against
                 the pass bar (the gate errs toward not claiming).
"""

import argparse
import json
import random
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
REPORT_DIR = _ROOT / "agenda" / "reports"
AUDIT_DIR = _ROOT / "agenda" / "audits"

PER_OUTLET_CAP = 2


def newest_report() -> Path:
    reports = sorted(REPORT_DIR.glob("*.json"))
    if not reports:
        raise SystemExit(f"no reports in {REPORT_DIR} — run agenda.py --report first")
    return reports[-1]


def candidate_claims(report: dict) -> list:
    """Every publishable omission claim in the report: sitemap-evidenced
    missed stories from outlets with a sufficient sample."""
    claims = []
    for outlet in report.get("omissions", {}).get("by_outlet", []):
        if outlet.get("insufficient_sample"):
            continue
        for story in outlet.get("missed_stories", []):
            if story.get("evidence_source") != "news-sitemap":
                continue  # RSS-only evidence is not publishable (Gate 2 pre-log)
            claims.append({
                "outlet": outlet.get("outlet", ""),
                "outlet_name": outlet.get("name", outlet.get("outlet", "")),
                "lean": outlet.get("lean") or "unrated",
                "sitemap_articles_in_window": outlet.get("sitemap_articles", 0),
                "story_id": story.get("story_id", ""),
                "story_label": story.get("label", ""),
                "carried_by_n_outlets": story.get("n_outlets", 0),
                "nearest_article": story.get("nearest_article", ""),
                "nearest_sim": story.get("nearest_sim", 0.0),
                "nearest_url": story.get("nearest_url", ""),
            })
    return claims


def sample_claims(claims: list, n: int, seed: str) -> list:
    """Deterministic shuffle, then greedy pick with a per-outlet cap; refill
    past the cap only if the pool is too small to reach n otherwise."""
    rng = random.Random(seed)
    pool = claims[:]
    rng.shuffle(pool)
    picked, counts = [], {}
    for claim in pool:
        if len(picked) >= n:
            break
        if counts.get(claim["outlet"], 0) >= PER_OUTLET_CAP:
            continue
        picked.append(claim)
        counts[claim["outlet"]] = counts.get(claim["outlet"], 0) + 1
    if len(picked) < n:
        for claim in pool:
            if len(picked) >= n:
                break
            if claim not in picked:
                picked.append(claim)
    return picked


def write_audit(report_path: Path, n: int) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    claims = candidate_claims(report)
    if not claims:
        raise SystemExit("report contains no publishable omission claims to audit")
    seed = report.get("report_id", report_path.stem)
    picked = sample_claims(claims, n, seed)

    period = report.get("period", {})
    audit = {
        "is_gate2_audit": True,
        "report": report_path.name,
        "report_id": report.get("report_id", ""),
        "period": period,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "seed": seed,
        "pool_size": len(claims),
        "sampled": len(picked),
        "pass_bar": "held on >= 9 of 10 (ROADMAP Gate 2)",
        "claims": [dict(c, verdict="", evidence_url="", notes="") for c in picked],
    }

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    json_path = AUDIT_DIR / f"gate2_{stamp}.json"
    json_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        f"# Gate 2 audit — {stamp}",
        "",
        f"Report: `{report_path.name}` · window {period.get('start', '?')} → {period.get('end', '?')}",
        f"Pool: {len(claims)} publishable omission claims · sampled {len(picked)} (seed `{seed}`, ≤{PER_OUTLET_CAP}/outlet)",
        "",
        "For each claim: search the outlet's own site search AND a "
        "`site:<domain> <story keywords>` web query, restricted to the report window. "
        "An in-window article covering the story = **failed** (paste the URL). "
        "Nothing found = **held**. Ambiguous = **unclear** (explain).",
        "",
        f"**Pass bar: ≥9/10 held.** Verdicts go in `{json_path.name}`.",
        "",
    ]
    for i, c in enumerate(picked, 1):
        md_lines += [
            f"## {i}. {c['outlet_name']} ({c['lean']}) — \"{c['story_label']}\"",
            f"- Carried by {c['carried_by_n_outlets']} other roster outlets; "
            f"{c['outlet_name']} had {c['sitemap_articles_in_window']} sitemap articles in the window.",
            f"- Instrument's nearest article (sim {c['nearest_sim']}): "
            f"\"{c['nearest_article']}\" — {c['nearest_url']}",
            "- [ ] held  [ ] failed  [ ] unclear — evidence: ",
            "",
        ]
    md_path = AUDIT_DIR / f"gate2_{stamp}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    outlets = sorted({c['outlet_name'] for c in picked})
    print(f"{len(picked)} claims across {len(outlets)} outlets: {', '.join(outlets)}")


def tally(audit_path: Path) -> None:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    claims = audit.get("claims", [])
    counts = {"held": 0, "failed": 0, "unclear": 0, "": 0}
    for c in claims:
        counts[c.get("verdict", "")] = counts.get(c.get("verdict", ""), 0) + 1
    unfilled = counts.pop("", 0)
    print(f"held {counts['held']} · failed {counts['failed']} · unclear {counts['unclear']}"
          + (f" · unfilled {unfilled}" if unfilled else ""))
    if unfilled:
        print("audit incomplete — fill every verdict before scoring")
        return
    n = len(claims)
    passed = counts["held"] >= 9 and n >= 10
    print(f"Gate 2: {'PASSED' if passed else 'FAILED'} ({counts['held']}/{n} held; bar is >=9/10)")
    for c in claims:
        if c.get("verdict") != "held":
            print(f"  {c['verdict']:>8}: {c['outlet_name']} — {c['story_label']}"
                  + (f" ({c['evidence_url']})" if c.get("evidence_url") else ""))


def main():
    ap = argparse.ArgumentParser(description="Gate 2 omission-claim audit pack")
    ap.add_argument("--report", type=Path, default=None, help="report JSON (default: newest)")
    ap.add_argument("--n", type=int, default=10, help="claims to sample (default 10)")
    ap.add_argument("--tally", type=Path, default=None, help="score a filled-in audit JSON")
    args = ap.parse_args()
    if args.tally:
        tally(args.tally)
    else:
        write_audit(args.report or newest_report(), args.n)


if __name__ == "__main__":
    main()
