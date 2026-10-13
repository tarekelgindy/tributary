"""
Opus vs Sonnet A/B on the judgment steps (2026-10-12, Decision Log)
===================================================================
Question (Tarek's): would Opus on the judgment-heavy steps measurably
improve origins and roles, now that the role-prompt batch fixed the rules?
Both arms run the SAME current prompts; only --judgment-model differs
(earliest-use, both adversarial passes, conceptual ancestors). Five
known-answer claims from the hand-corrected corpus are the ground truth.

    python ab_opus.py --run sonnet     # ~5 traces x ~$0.70
    python ab_opus.py --run opus       # ~5 traces x ~$1.05
    python ab_opus.py --score          # compare both arms vs known answers

Outputs land in _ab/<arm>/ (gitignored). Each claim runs with --force
(no gate, no dedup) and production flags (--evidence --conceptual
--max-searches 4).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AB = ROOT / "_ab"
OPUS = "claude-opus-5"

# Known answers, from the hand-audited published corpus. kw matching is
# case-insensitive substring over author+source_title+url of the entry.
CASES = [
    {"slug": "carrots",
     "claim": "carrots improve your eyesight and night vision",
     "lex_year": "1940", "lex_origin_kw": ["ministry"],
     "fiction_kw": []},
    {"slug": "moon",
     "claim": "the moon landing was faked",
     "con_year": "1969", "lex_origin_kw": ["kaysing"],
     "fiction_kw": ["gunn", "cave of night", "capricorn", "twilight zone"]},
    {"slug": "thumbs",
     "claim": "Roman thumbs up meant live and thumbs down meant death for gladiators",
     "con_year": "1872", "con_origin_kw": ["gérôme", "gerome", "pollice"],
     "fiction_kw": []},
    {"slug": "gum",
     "claim": "chewing gum stays in your stomach for seven years if you swallow it",
     "lex_year": "1993",
     "fiction_kw": []},
    {"slug": "vaxrebuttal",
     "claim": "vaccines do not cause autism",
     "lex_year": "1998", "lex_origin_kw": ["medical research council", "mrc", "lancet"],
     # polarity check: debunk-flavored sources must not be labeled critic
     "debunk_kw": ["mayo", "cdc", "fact", "myth", "debunk"],
     "fiction_kw": []},
]


def run_arm(arm: str):
    model = OPUS if arm == "opus" else ""
    outdir = AB / arm
    outdir.mkdir(parents=True, exist_ok=True)
    for c in CASES:
        out = outdir / f"{c['slug']}.out"
        if (outdir / "index.json").exists() and _find_fp(outdir, c):
            print(f"[{arm}] {c['slug']}: already present, skipping")
            continue
        print(f"[{arm}] {c['slug']}: running…", flush=True)
        cmd = [sys.executable, "-X", "utf8", str(ROOT / "fingerprint.py"), c["claim"],
               "--force", "--evidence", "--conceptual", "--max-searches", "4",
               "--save", "--store-dir", str(outdir)]
        if model:
            cmd += ["--judgment-model", model]
        with open(out, "w", encoding="utf-8") as fh:
            r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT)
        print(f"[{arm}] {c['slug']}: exit {r.returncode}", flush=True)


def _find_fp(outdir: Path, case) -> dict | None:
    """The store names files by fingerprint id; find the one whose canonical
    phrase matches the case's claim tokens."""
    want = set(case["claim"].lower().split()[:4])
    for p in outdir.glob("*.json"):
        if p.name in ("index.json", "vectors.json"):
            continue
        try:
            fp = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        canon = ((fp.get("lexical") or {}).get("canonical_phrase") or "").lower()
        if len(want & set(canon.split())) >= 2:
            return fp
    return None


def _entry_blob(a) -> str:
    return " ".join(str(a.get(k) or "") for k in
                    ("author", "source_title", "source_url", "exact_quote")).lower()


def score_case(fp: dict, case) -> dict:
    g = fp.get("genealogy") or {}
    lex, con = g.get("lexical") or {}, g.get("conceptual") or {}
    lex_log = [a for a in lex.get("attestation_log") or []
               if (a.get("claim_relation") or "") != "related-context"]
    con_log = [a for a in con.get("attestation_log") or []
               if (a.get("claim_relation") or "") != "related-context"]
    res = {"lex_first": lex.get("first_attested_date", ""),
           "con_first": con.get("first_attested_date", ""),
           "lex_status": lex.get("status"), "con_status": con.get("status")}

    checks = {}
    if "lex_year" in case:
        checks["lex_year"] = str(res["lex_first"]).startswith(case["lex_year"])
    if "con_year" in case:
        checks["con_year"] = str(res["con_first"]).startswith(case["con_year"])
    if "lex_origin_kw" in case:
        origins = [a for a in lex_log if a.get("amplifier_role") == "originator"] or lex_log[:1]
        checks["lex_origin"] = any(kw in _entry_blob(a)
                                   for a in origins for kw in case["lex_origin_kw"])
    if "con_origin_kw" in case:
        origins = [a for a in con_log if a.get("amplifier_role") == "originator"] or con_log[:1]
        checks["con_origin"] = any(kw in _entry_blob(a)
                                   for a in origins for kw in case["con_origin_kw"])
    if case.get("fiction_kw"):
        leak = [a for a in lex_log + con_log
                if any(kw in _entry_blob(a) for kw in case["fiction_kw"])]
        checks["no_fiction_leak"] = not leak
    if case.get("debunk_kw"):
        bad = [a for a in lex_log if a.get("amplifier_role") == "critic"
               and any(kw in _entry_blob(a) for kw in case["debunk_kw"])]
        checks["polarity_roles"] = not bad
    res["checks"] = checks
    res["score"] = f"{sum(checks.values())}/{len(checks)}"
    return res


def score():
    rows = []
    for c in CASES:
        row = {"case": c["slug"]}
        for arm in ("sonnet", "opus"):
            fp = _find_fp(AB / arm, c) if (AB / arm).exists() else None
            row[arm] = score_case(fp, c) if fp else None
        rows.append(row)
    for row in rows:
        print(f"\n=== {row['case']}")
        for arm in ("sonnet", "opus"):
            r = row[arm]
            if not r:
                print(f"  {arm:7}: (missing)")
                continue
            marks = " ".join(f"{k}:{'Y' if v else 'N'}" for k, v in r["checks"].items())
            print(f"  {arm:7}: {r['score']}  lex={r['lex_first']}({r['lex_status']}) "
                  f"con={r['con_first']}({r['con_status']})  {marks}")
    totals = {arm: sum(int(row[arm]["score"].split("/")[0]) for row in rows if row[arm])
              for arm in ("sonnet", "opus")}
    maxes = {arm: sum(int(row[arm]["score"].split("/")[1]) for row in rows if row[arm])
             for arm in ("sonnet", "opus")}
    print(f"\nTOTAL  sonnet {totals['sonnet']}/{maxes['sonnet']} · "
          f"opus {totals['opus']}/{maxes['opus']}")


def main():
    ap = argparse.ArgumentParser(description="Opus vs Sonnet A/B on judgment steps.")
    ap.add_argument("--run", choices=["sonnet", "opus"])
    ap.add_argument("--score", action="store_true")
    args = ap.parse_args()
    if args.run:
        run_arm(args.run)
    if args.score:
        score()
    if not args.run and not args.score:
        ap.error("--run sonnet|opus or --score")


if __name__ == "__main__":
    main()
