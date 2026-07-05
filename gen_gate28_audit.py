"""Gate 2.8 blind audit pack: sample attestations from the fixed traces for
human labeling.

ROADMAP Gate 2.8: ~30 blind-sampled attestations from repaired + regenerated
traces; the human labels role and date-type WITHOUT seeing the AI's labels.
Pass bar: >=90% role agreement, ZERO adoption/critic inversions, ZERO
backdated retrospectives the pipeline didn't flag.

Workflow:
    python gen_gate28_audit.py                       # writes the blind pack
    (label the .md checklist — do NOT open the .json first; it holds the
     AI labels for scoring)
    python gen_gate28_audit.py --transcribe agenda/audits/gate28_<date>.md
    python gen_gate28_audit.py --tally agenda/audits/gate28_<date>.json
"""

import argparse
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
AUDIT_DIR = _ROOT / "agenda" / "audits"

SOURCES = [
    ("gum", _ROOT / "examples" / "fingerprint_chewing_gum.json"),
    ("carrots", _ROOT / "examples" / "fingerprint_carrots_eyesight.json"),
    ("rigged-economy", _ROOT / "examples" / "fingerprint_rigged_economy.json"),
    ("birthright-event", _ROOT / "gallery" / "events" / "55fe57fd9c41.json"),
]

ROLES = ["originator", "early-amplifier", "mass-amplifier",
         "institutional-adoption", "critic", "mention"]
PER_TRACE_CAP = 6


def collect_pool() -> list:
    pool = []
    for label, path in SOURCES:
        doc = json.loads(path.read_text(encoding="utf-8"))
        fps = [doc] if doc.get("fingerprint_id") else (doc.get("fingerprints") or [])
        for fp in fps:
            phrase = ((fp.get("lexical") or {}).get("canonical_phrase") or "")
            trace = f"{label}/{fp.get('fingerprint_id', '')[:6]}"
            for lin in ("lexical", "conceptual"):
                g = (fp.get("genealogy") or {}).get(lin) or {}
                for i in g.get("attestation_log") or []:
                    if i.get("claim_relation") == "related-context":
                        continue  # not part of the claimed lineage
                    if not (i.get("source_title") or i.get("author")):
                        continue
                    pool.append({
                        "trace": trace, "phrase": phrase,
                        "lineage": "phrasing" if lin == "lexical" else "idea",
                        "date": i.get("date", ""),
                        "date_precision": i.get("date_precision", ""),
                        "title": i.get("source_title", ""),
                        "author": i.get("author", ""),
                        "quote": (i.get("exact_quote") or "")[:240],
                        "url": i.get("source_url", ""),
                        "cited_via": i.get("cited_via", ""),
                        # hidden ground truth — scored at tally time
                        "ai_role": i.get("amplifier_role", ""),
                        "ai_describes_period": i.get("describes_period", ""),
                        "human_role": "", "human_date": "",
                    })
    return pool


def sample(pool: list, n: int) -> list:
    rng = random.Random("gate-2.8:" + str(len(pool)))
    shuffled = pool[:]
    rng.shuffle(shuffled)
    picked, counts = [], {}
    for item in shuffled:
        if len(picked) >= n:
            break
        if counts.get(item["trace"], 0) >= PER_TRACE_CAP:
            continue
        picked.append(item)
        counts[item["trace"]] = counts.get(item["trace"], 0) + 1
    if len(picked) < n:
        for item in shuffled:
            if len(picked) >= n:
                break
            if item not in picked:
                picked.append(item)
    rng.shuffle(picked)   # blind order: no grouping by trace or role
    return picked


def write_pack(n: int) -> None:
    pool = collect_pool()
    picked = sample(pool, n)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    json_path = AUDIT_DIR / f"gate28_{stamp}.json"
    md_path = AUDIT_DIR / f"gate28_{stamp}.md"

    audit = {
        "is_gate28_audit": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_size": len(pool), "sampled": len(picked),
        "pass_bar": ">=90% role agreement; zero adoption/critic inversions; "
                    "zero unflagged backdated retrospectives",
        "items": picked,
    }
    json_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False),
                         encoding="utf-8")

    lines = [
        f"# Gate 2.8 blind audit — {stamp}",
        "",
        f"{len(picked)} attestations sampled from {len(pool)} across the fixed traces.",
        "**Do not open the .json before labeling — it contains the AI's labels.**",
        "",
        "For each item, judge from the source itself (click the URL when unsure):",
        "- **Role** — what did this source DO with the narrative? A source whose",
        "  function is examining/checking/debunking is a critic even if neutral in",
        "  tone. `originator` only if this looks like the actual coining.",
        "- **Date** — does the date shown look like THIS DOCUMENT's own publication",
        "  date? Mark `backdated` if it looks like the era the content *describes*.",
        "",
        f"When done: `python gen_gate28_audit.py --transcribe agenda/audits/gate28_{stamp}.md`",
        f"then `python gen_gate28_audit.py --tally agenda/audits/gate28_{stamp}.json`",
        "",
    ]
    for k, it in enumerate(picked, 1):
        via = f" — via {it['cited_via']}" if it["cited_via"] else ""
        lines += [
            f"## {k}. [{it['lineage']}] “{it['phrase'][:60]}”",
            f"- **{it['date']}** ({it['date_precision'] or 'unspecified precision'}) · "
            f"{it['author'] or '(no author)'} — {it['title']}",
            f"- quote: “{it['quote']}”" if it["quote"] else "- (no quote recorded)",
            f"- {it['url']}{via}",
            "- Role: [ ] originator · [ ] early-amplifier · [ ] mass-amplifier · "
            "[ ] institutional-adoption · [ ] critic · [ ] mention",
            "- Date: [ ] document · [ ] backdated · [ ] unsure",
            "",
        ]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {md_path}")
    print(f"wrote {json_path}  (holds AI labels — label the md first)")
    by_trace = {}
    for it in picked:
        by_trace[it["trace"]] = by_trace.get(it["trace"], 0) + 1
    print("sample spread:", ", ".join(f"{t}={c}" for t, c in sorted(by_trace.items())))


def transcribe(md_path: Path) -> None:
    text = md_path.read_text(encoding="utf-8")
    json_path = md_path.with_suffix(".json")
    audit = json.loads(json_path.read_text(encoding="utf-8"))
    blocks = re.split(r"^## \d+\. ", text, flags=re.M)[1:]
    if len(blocks) != len(audit["items"]):
        raise SystemExit(f"{len(blocks)} blocks vs {len(audit['items'])} items — aborting")
    n = 0
    for block, item in zip(blocks, audit["items"]):
        rm = re.search(r"\[\s*[xX]\s*\]\s*(originator|early-amplifier|mass-amplifier|"
                       r"institutional-adoption|critic|mention)", block)
        dm = re.search(r"\[\s*[xX]\s*\]\s*(document|backdated|unsure)", block)
        if rm:
            item["human_role"] = rm.group(1)
            n += 1
        if dm:
            item["human_date"] = dm.group(1)
    json_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"transcribed {n} role verdict(s) -> {json_path.name}")


def tally(json_path: Path) -> None:
    audit = json.loads(json_path.read_text(encoding="utf-8"))
    items = audit["items"]
    labeled = [i for i in items if i.get("human_role")]
    if len(labeled) < len(items):
        print(f"{len(items) - len(labeled)} item(s) unlabeled — finish before scoring")
        return
    judged = [i for i in items if i["human_role"] != "unsure"]
    agree = [i for i in judged if i["human_role"] == i["ai_role"]]
    inversions = [i for i in items if (
        (i["human_role"] == "critic") != (i["ai_role"] == "critic")
        and "critic" in (i["human_role"], i["ai_role"])
        and (i["human_role"] in ("institutional-adoption", "mass-amplifier", "early-amplifier")
             or i["ai_role"] in ("institutional-adoption", "mass-amplifier", "early-amplifier")))]
    backdated = [i for i in items
                 if i.get("human_date") == "backdated" and not i.get("ai_describes_period")]
    pct = 100 * len(agree) / max(len(judged), 1)
    print(f"role agreement: {len(agree)}/{len(judged)} = {pct:.0f}%")
    print(f"adoption/critic inversions: {len(inversions)}")
    print(f"unflagged backdated dates: {len(backdated)}")
    for i in inversions:
        print(f"  INVERSION: human={i['human_role']} ai={i['ai_role']} — {i['title'][:60]}")
    for i in backdated:
        print(f"  BACKDATED: {i['date']} — {i['title'][:60]}")
    for i in items:
        if i["human_role"] not in (i["ai_role"], "unsure", ""):
            print(f"  diff: human={i['human_role']:<22} ai={i['ai_role']:<22} {i['title'][:52]}")
    passed = pct >= 90 and not inversions and not backdated
    print(f"\nGate 2.8: {'PASSED' if passed else 'FAILED'} "
          f"(bar: >=90%, zero inversions, zero unflagged backdating)")


def main():
    ap = argparse.ArgumentParser(description="Gate 2.8 blind label audit")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--transcribe", type=Path)
    ap.add_argument("--tally", type=Path)
    args = ap.parse_args()
    if args.transcribe:
        transcribe(args.transcribe)
    elif args.tally:
        tally(args.tally)
    else:
        write_pack(args.n)


if __name__ == "__main__":
    main()
