"""
Human contributions — intake and write-back (Phase 2c-B)
========================================================
The flow that turns a reader's submission into a recorded, credited
contribution. Two modes, called by .github/workflows/contribution.yml:

  --intake   Validate a submission (payload arrives in CONTRIB_* env vars via
             the Cloudflare Worker -> repository_dispatch), run the mechanical
             checks the AI's own output gets (URL reachable, quote actually on
             the page, Wayback snapshot), and write a review file the workflow
             files as a GitHub issue labeled `contribution-pending`.
             NOTHING PUBLISHES AT INTAKE.

  --apply    On the maintainer's approval (label `approve-contribution`),
             parse the review issue's JSON block and write the contribution
             into the published fingerprint: a ledger entry
             (models.Contribution), a contributor record, and the effect —
             a new attestation with human_added provenance, a structured
             correction (edit: role/date/attribution/receipt, applied with
             a dated note), or a confirm/dispute transition on an existing
             element's Provenance.
             Updates every published copy (gallery/traces/ standalone +
             gallery/events/ embedded) so they never drift.

Principles (METHODOLOGY "Human contributions"): a contribution is a claim
with receipts — it gets the same verification statuses as AI output, a
provenance label, and named credit (or "Anonymous", by choice). Contact
details never reach this script or the public repo: the Worker keeps them
in private KV storage only.

Local dry-run (no network beyond the URL checks):
  CONTRIB_KIND=add CONTRIB_FP=<id> CONTRIB_URL=... CONTRIB_DATE=2003-01-05 \
    python contribute.py --intake --out review.md
  ISSUE_BODY="$(cat review.md)" python contribute.py --apply --gallery-dir tmp/gallery
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from models import (AttestedInstance, Contribution, ContributionKind,
                    Contributor, Provenance)

ROOT = Path(__file__).resolve().parent
UTC_NOW = lambda: datetime.now(timezone.utc).isoformat()
VERIFY_TIMEOUT = 20.0
HEX12 = re.compile(r"^[a-f0-9]{12}$")
DATE_RE = re.compile(r"^\d{4}(-\d{2})?(-\d{2})?$")
JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)

# v2 (2026-09-24, friends-round feedback: "hard to contribute origins/
# amplifiers that differ from what the AI found"): add is any-date, and
# edit = a structured correction (role/date/attribution/receipt) applied
# with a dated note, exactly the convention maintainer corrections use.
KINDS = {"add", "confirm", "dispute", "edit"}   # flag/add_context wait
ROLES = {"originator", "early-amplifier", "mass-amplifier",
         "institutional-adoption", "critic", "mention"}


# ---------------------------------------------------------------------------
# mechanical verification (sync mirror of fingerprint.py's checks — a human
# submission gets exactly the treatment the AI's citations get)
# ---------------------------------------------------------------------------

def check_url(http, url):
    """(status_code | None, error_note)"""
    try:
        r = http.head(url, follow_redirects=True, timeout=VERIFY_TIMEOUT)
        if r.status_code in (403, 405) or r.status_code >= 500:
            r = http.get(url, follow_redirects=True, timeout=VERIFY_TIMEOUT)
        return r.status_code, ""
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception as e:
        return None, type(e).__name__


def check_quote(http, url, quote):
    """(matched: bool, note) — fuzzy chunk windows, like the pipeline's."""
    quote = (quote or "").strip()
    if len(quote) < 12:
        return False, "quote too short to check"
    try:
        r = http.get(url, follow_redirects=True, timeout=VERIFY_TIMEOUT)
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code} fetching content"
    except Exception as e:
        return False, f"{type(e).__name__} fetching content"
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", r.text).lower())
    words = re.findall(r"\w+", quote.lower())
    if len(words) < 4:
        return False, "too few words to check"
    win = min(6, max(3, len(words) // 3))
    for i in range(0, len(words) - win + 1, max(1, win // 2)):
        if " ".join(words[i:i + win]) in text:
            return True, "matched fuzzy quote chunk"
    return False, "no fuzzy chunk of quote found in page"


def wayback(http, url):
    try:
        r = http.get("https://archive.org/wayback/available",
                     params={"url": url}, timeout=VERIFY_TIMEOUT)
        closest = (r.json().get("archived_snapshots") or {}).get("closest") or {}
        return str(closest.get("url", "")) if closest.get("available") else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# fingerprint location: every published copy of a fingerprint_id
# ---------------------------------------------------------------------------

def find_copies(gallery, fp_id):
    """[(path, 'standalone'|'embedded')] for every published copy."""
    hits = []
    p = gallery / "traces" / f"{fp_id}.json"
    if p.exists():
        hits.append((p, "standalone"))
    for ev_path in sorted((gallery / "events").glob("*.json")):
        try:
            ev = json.loads(ev_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if any(f.get("fingerprint_id") == fp_id for f in ev.get("fingerprints") or []):
            hits.append((ev_path, "embedded"))
    return hits


def get_fp(doc, path_kind, fp_id):
    if path_kind == "standalone":
        return doc
    return next(f for f in doc["fingerprints"] if f.get("fingerprint_id") == fp_id)


def find_element(fp, element_id):
    """Locate an attestation-log entry by instance_id. Returns
    (lineage, entry) or (None, None)."""
    for lin in ("lexical", "conceptual"):
        for entry in ((fp.get("genealogy") or {}).get(lin) or {}).get("attestation_log") or []:
            if entry.get("instance_id") == element_id:
                return lin, entry
    return None, None


# ---------------------------------------------------------------------------
# intake
# ---------------------------------------------------------------------------

def read_payload():
    g = lambda k: (os.environ.get(k) or "").strip()
    return {
        "kind": g("CONTRIB_KIND").lower(),
        "fingerprint_id": g("CONTRIB_FP").lower(),
        "url": g("CONTRIB_URL")[:500],
        "date": g("CONTRIB_DATE")[:10],
        "quote": g("CONTRIB_QUOTE")[:600],
        "source_author": g("CONTRIB_SOURCE_AUTHOR")[:120],
        "reason": g("CONTRIB_REASON")[:600],
        "element_id": g("CONTRIB_ELEMENT").lower(),
        "role": g("CONTRIB_ROLE").lower()[:30],
        "lineage": g("CONTRIB_LINEAGE").lower() or "lexical",
        "name": g("CONTRIB_NAME")[:80],
        "anonymous": g("CONTRIB_ANON").lower() in ("1", "true", "yes", "on"),
        "ref": g("CONTRIB_REF")[:32],
        "submitted_at": UTC_NOW(),
    }


def intake(gallery, out_path):
    p = read_payload()
    problems = []
    if p["kind"] not in KINDS:
        problems.append(f"unsupported kind {p['kind']!r} "
                        "(supported: add / edit / confirm / dispute)")
    if not HEX12.match(p["fingerprint_id"]):
        problems.append("fingerprint id must be 12 hex chars")
    copies = find_copies(gallery, p["fingerprint_id"]) if not problems else []
    if not problems and not copies:
        problems.append(f"no published fingerprint {p['fingerprint_id']} in the gallery")

    fp = None
    if copies:
        doc = json.loads(copies[0][0].read_text(encoding="utf-8"))
        fp = get_fp(doc, copies[0][1], p["fingerprint_id"])

    if p["kind"] == "add":
        if not re.match(r"^https?://", p["url"] or ""):
            problems.append("an added use needs an http(s) receipt URL")
        if not DATE_RE.match(p["date"] or ""):
            problems.append("date must be YYYY, YYYY-MM, or YYYY-MM-DD")
        if p["lineage"] not in ("lexical", "conceptual"):
            problems.append("lineage must be lexical or conceptual")
    elif p["kind"] == "edit":
        if not HEX12.match(p["element_id"]):
            problems.append("a correction needs the target entry's id")
        elif fp is not None and find_element(fp, p["element_id"])[0] is None:
            problems.append(f"no attestation-log entry {p['element_id']} on this trace")
        if not any(p[k] for k in ("role", "date", "source_author", "url")):
            problems.append("a correction needs at least one proposed change "
                            "(role, date, attribution, or receipt link)")
        if p["role"] and p["role"] not in ROLES:
            problems.append("proposed role must be one of: " + ", ".join(sorted(ROLES)))
        if p["date"] and not DATE_RE.match(p["date"]):
            problems.append("date must be YYYY, YYYY-MM, or YYYY-MM-DD")
        if p["url"] and not re.match(r"^https?://", p["url"]):
            problems.append("the proposed receipt must be an http(s) URL")
        if len(p["reason"]) < 12:
            problems.append("a correction needs a reason (what is wrong and how you know)")
    else:
        if not HEX12.match(p["element_id"]):
            problems.append("confirm/dispute needs the target entry's id")
        elif fp is not None and find_element(fp, p["element_id"])[0] is None:
            problems.append(f"no attestation-log entry {p['element_id']} on this trace")
        if p["kind"] == "dispute" and len(p["reason"]) < 12:
            problems.append("a dispute needs a reason (what is wrong, with a receipt if possible)")

    display = "Anonymous" if p["anonymous"] or not p["name"] else p["name"]

    # mechanical checks — same treatment the AI's own citations get
    checks = {"verification_status": "unchecked", "verification_notes": "", "archive_url": ""}
    if p["url"] and not problems:
        with httpx.Client(headers={"User-Agent": "tributary-verify/1.0"}) as http:
            code, err = check_url(http, p["url"])
            if code is None:
                checks["verification_status"] = "fetch-error"
                checks["verification_notes"] = f"could not fetch ({err}) — paywalled or bot-blocked pages often do this while opening fine in a browser"
            elif code >= 400:
                checks["verification_status"] = "url-error"
                checks["verification_notes"] = f"HTTP {code}"
            elif p["quote"]:
                ok, note = check_quote(http, p["url"], p["quote"])
                checks["verification_status"] = "verified" if ok else "quote-not-found"
                checks["verification_notes"] = note
            else:
                checks["verification_status"] = "url-ok"
                checks["verification_notes"] = "URL reachable (HTTP %d); no quote supplied to check" % code
            checks["archive_url"] = wayback(http, p["url"])

    record = {**p, "display_name": display, "checks": checks, "valid": not problems,
              "problems": problems}

    edit_bits = ", ".join(filter(None, [
        p["role"] and f"role -> {p['role']}",
        p["date"] and f"date -> {p['date']}",
        p["source_author"] and f"attribution -> {p['source_author']}",
        p["url"] and f"receipt -> {p['url'][:60]}"]))
    kind_line = {"add": f"a use we missed ({p['date']}, {p['url'][:80]})",
                 "edit": f"correction to entry {p['element_id']}: {edit_bits}",
                 "confirm": f"confirm entry {p['element_id']}",
                 "dispute": f"dispute entry {p['element_id']}"}.get(p["kind"], p["kind"])
    phrase = ((fp or {}).get("lexical") or {}).get("canonical_phrase", "")[:100]

    lines = [
        f"**Contribution ({p['kind']})** to `{p['fingerprint_id']}` — “{phrase}”",
        "",
        f"- **What:** {kind_line}",
        f"- **By:** {display}" + (" (chose anonymity)" if display == "Anonymous" and p["name"] else ""),
        f"- **Reason:** {p['reason'] or '(none given)'}",
        f"- **Quote:** {p['quote'] or '(none)'}",
        f"- **Mechanical checks:** {checks['verification_status']}"
        + (f" — {checks['verification_notes']}" if checks["verification_notes"] else "")
        + (f" · [archived]({checks['archive_url']})" if checks["archive_url"] else ""),
        "",
    ]
    if problems:
        lines += ["**REJECTED at intake:**"] + [f"- {x}" for x in problems] + [""]
    else:
        lines += ["To **publish**: add the `approve-contribution` label. To **decline**: close the issue.",
                  "A quote-not-found or fetch-error check is not automatically disqualifying "
                  "(paywalls, offline originals) — the receipt is above; judge it.", ""]
    lines += ["```json", json.dumps(record, indent=1, ensure_ascii=False), "```"]
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")

    print(f"[contribute] intake {'REJECTED' if problems else 'ok'}: "
          f"{p['kind']} on {p['fingerprint_id']} by {display}", file=sys.stderr)
    # the workflow reads these to build the issue title
    print(f"TITLE=Contribution ({p['kind']}) to {p['fingerprint_id']}: {phrase[:60]}")
    print(f"VALID={'yes' if not problems else 'no'}")
    return 0


# ---------------------------------------------------------------------------
# apply (runs only on the maintainer's approval label)
# ---------------------------------------------------------------------------

def _date_key(s):
    """Sortable key; missing precision rounds LATE so an earlier-than claim
    must be strictly earlier (matches the age math's conservatism)."""
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$", s or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2) or 12), int(m.group(3) or 31))


def _refresh_stats(gl):
    """Recompute timeline_stats after the log changed. Best-effort: the
    stats function lives in fingerprint.py (which imports anthropic); if
    that import is unavailable the stale stats stay — the viewer's own
    rendering derives most figures live."""
    try:
        from types import SimpleNamespace
        from fingerprint import compute_timeline_stats
        rec = SimpleNamespace(attestation_log=[
            SimpleNamespace(**i) for i in gl.get("attestation_log") or []])
        gl["timeline_stats"] = compute_timeline_stats(rec)
    except Exception as e:
        print(f"[contribute] stats refresh skipped ({type(e).__name__})",
              file=sys.stderr)


def _refresh_lineage(gl):
    """After an edit changed dates/roles: re-derive the lineage's earliest
    fields from the asserting entries, and downgrade a single-origin claim
    that no longer has an originator-labeled entry backing it."""
    log = gl.get("attestation_log") or []
    asserting = [i for i in log
                 if (i.get("claim_relation") or "") != "related-context"
                 and _date_key(i.get("date"))]
    if asserting:
        earliest = min(asserting, key=lambda i: _date_key(i.get("date")))
        if gl.get("first_attested_date") != earliest.get("date"):
            gl["first_attested_date"] = earliest.get("date")
            gl["first_attested_source"] = earliest.get("source_url", "")
            gl["primary_origin_id"] = earliest.get("instance_id", "")
    if gl.get("status") == "single-origin" and not any(
            (i.get("amplifier_role") or "") == "originator" for i in asserting):
        gl["status"] = "earliest-found"
    _refresh_stats(gl)


def contributor_id_for(record):
    if record["anonymous"] or not record["name"]:
        return "anon-" + hashlib.sha256(
            (record["submitted_at"] + record["fingerprint_id"]).encode()).hexdigest()[:10]
    return hashlib.sha256(
        ("name:" + record["name"].strip().lower()).encode()).hexdigest()[:12]


def apply_to_fp(fp, record):
    """Mutate one fingerprint dict in place. Returns a summary string."""
    display = record["display_name"]
    cid = contributor_id_for(record)
    checks = record.get("checks") or {}

    contribution = Contribution(
        kind=ContributionKind(record["kind"]),
        target_element_id=record.get("element_id") or record["fingerprint_id"],
        target_element_type="attested_instance",
        contributor_id=cid, contributor_name=display,
        reason=record.get("reason", ""),
        payload={k: record.get(k, "") for k in
                 ("url", "date", "quote", "source_author", "role", "lineage")},
        created_at=record["submitted_at"],
    )

    summary = ""
    if record["kind"] == "add":
        prov = Provenance.human(contributor_id=cid, contributor_name=display,
                                reasoning=record.get("reason", ""))
        inst = AttestedInstance(
            date=record["date"], source_url=record["url"],
            author=record.get("source_author", ""),
            exact_quote=record.get("quote", ""),
            evidence=f"Contributed by {display}"
                     + (f": {record['reason']}" if record.get("reason") else ""),
            verification_status=checks.get("verification_status", "unchecked"),
            verification_notes=checks.get("verification_notes", ""),
            verified=checks.get("verification_status") == "verified",
            archive_url=checks.get("archive_url", ""),
            provenance=prov,
        )
        gl = fp.setdefault("genealogy", {}).setdefault(record["lineage"], {})
        gl.setdefault("attestation_log", []).append(inst.to_dict())
        contribution.target_element_id = inst.instance_id
        summary = f"added {record['date']} attestation ({inst.instance_id})"
        # earlier than the recorded earliest? then the human moved the origin
        new_k, old_k = _date_key(record["date"]), _date_key(gl.get("first_attested_date"))
        if new_k and (old_k is None or new_k < old_k):
            gl["first_attested_date"] = record["date"]
            gl["first_attested_source"] = record["url"]
            if gl.get("status") == "single-origin":
                gl["status"] = "earliest-found"   # the single-origin claim just broke
            summary += f"; new earliest for the {record['lineage']} lineage"
        _refresh_stats(gl)
    elif record["kind"] == "edit":
        lin, entry = find_element(fp, record["element_id"])
        if entry is None:
            raise SystemExit(f"[contribute] apply: element {record['element_id']} not found")
        day = (record["submitted_at"] or "")[:10]
        reason = record.get("reason", "")
        changes = []
        if record.get("role"):
            old = entry.get("amplifier_role") or "unknown"
            entry["amplifier_role"] = record["role"]
            entry["role_evidence"] = ((entry.get("role_evidence") or "")
                + f" [{day} correction by {display}: role {old} -> "
                  f"{record['role']} — {reason}]").strip()
            changes.append(f"role {old} -> {record['role']}")
        if record.get("date"):
            changes.append(f"date {entry.get('date') or '?'} -> {record['date']}")
            entry["date"] = record["date"]
        if record.get("source_author"):
            changes.append(f"attribution {entry.get('author') or '?'} -> "
                           f"{record['source_author']}")
            entry["author"] = record["source_author"]
        if record.get("url"):
            entry["source_url"] = record["url"]
            # the proposed receipt was mechanically checked at intake
            entry["verification_status"] = checks.get("verification_status", "unchecked")
            entry["verification_notes"] = checks.get("verification_notes", "")
            entry["verified"] = checks.get("verification_status") == "verified"
            if checks.get("archive_url"):
                entry["archive_url"] = checks["archive_url"]
            changes.append("receipt URL replaced (re-checked at intake)")
        if [c for c in changes if not c.startswith("role ")]:
            entry["evidence"] = ((entry.get("evidence") or "")
                + f" [{day} correction by {display}: "
                  f"{'; '.join(c for c in changes if not c.startswith('role '))}"
                  f" — {reason}]").strip()
        prov = entry.get("provenance") or Provenance().to_dict()
        prov.setdefault("edits", []).append(contribution.contribution_id)
        # a maintainer-approved correction IS a human review of the entry;
        # it also resolves a standing dispute (the record of it remains)
        if prov.get("status") in ("ai_generated", "disputed", ""):
            prov["status"] = "human_confirmed"
        entry["provenance"] = prov
        _refresh_lineage(fp["genealogy"][lin])
        summary = (f"correction on {lin} entry {record['element_id']}: "
                   + "; ".join(changes))
    else:
        lin, entry = find_element(fp, record["element_id"])
        if entry is None:
            raise SystemExit(f"[contribute] apply: element {record['element_id']} not found")
        prov = entry.get("provenance") or Provenance().to_dict()
        key = "confirmations" if record["kind"] == "confirm" else "disputes"
        prov.setdefault("confirmations", [])
        prov.setdefault("disputes", [])
        prov[key].append(contribution.contribution_id)
        n_c, n_d = len(prov["confirmations"]), len(prov["disputes"])
        prov["confirmation_count"], prov["dispute_count"] = n_c, n_d
        # settlement mirror of models.Provenance.is_settled
        prov["is_settled"] = (n_d == 0 and n_c >= 3) or (n_c >= 5 and n_c > n_d * 2)
        if record["kind"] == "dispute":
            prov["status"] = "disputed"           # disputes are sticky until resolved
        elif prov.get("status") in ("ai_generated", "human_confirmed", ""):
            prov["status"] = "consensus" if prov["is_settled"] else "human_confirmed"
        entry["provenance"] = prov
        summary = f"{record['kind']} on {lin} entry {record['element_id']} ({prov['status']})"

    fp.setdefault("contributions", []).append(contribution.to_dict())
    contributors = fp.setdefault("contributors", [])
    for c in contributors:
        if c.get("contributor_id") == cid:
            c["contributions_count"] = int(c.get("contributions_count", 0)) + 1
            break
    else:
        contributors.append(Contributor(contributor_id=cid, display_name=display,
                                        contributions_count=1).__dict__ | {
            "joined_at": record["submitted_at"]})
    fp["last_updated"] = UTC_NOW()
    return summary


def apply_mode(gallery, body):
    m = JSON_BLOCK.search(body or "")
    if not m:
        raise SystemExit("[contribute] apply: no JSON block found in the issue body")
    record = json.loads(m.group(1))
    if not record.get("valid"):
        raise SystemExit("[contribute] apply: this submission was rejected at intake "
                         f"({'; '.join(record.get('problems') or ['unknown'])})")
    if record.get("kind") not in KINDS:
        raise SystemExit(f"[contribute] apply: unsupported kind {record.get('kind')!r}")

    copies = find_copies(gallery, record["fingerprint_id"])
    if not copies:
        raise SystemExit(f"[contribute] apply: fingerprint {record['fingerprint_id']} "
                         "not found in the gallery")
    summary, touched = "", []
    for path, path_kind in copies:
        doc = json.loads(path.read_text(encoding="utf-8"))
        fp = get_fp(doc, path_kind, record["fingerprint_id"])
        summary = apply_to_fp(fp, record)
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        touched.append(str(path.relative_to(gallery.parent)).replace("\\", "/"))

    print(f"[contribute] applied: {summary}", file=sys.stderr)
    print(f"SUMMARY={summary}")
    print(f"FPID={record['fingerprint_id']}")
    print(f"BY={record['display_name']}")
    print(f"FILES={' '.join(touched)}")
    print(f"URL=https://tarekelgindy.github.io/tributary/fingerprint_viewer.html"
          f"?load=gallery/traces/{record['fingerprint_id']}.json")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Human-contribution intake and write-back.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--intake", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--out", default="review.md", help="--intake: review file to write")
    ap.add_argument("--body-file", default="", help="--apply: file with the issue body "
                    "(default: ISSUE_BODY env var)")
    ap.add_argument("--gallery-dir", default=str(ROOT / "gallery"))
    args = ap.parse_args()
    gallery = Path(args.gallery_dir)

    if args.intake:
        return intake(gallery, args.out)
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file \
        else os.environ.get("ISSUE_BODY", "")
    return apply_mode(gallery, body)


if __name__ == "__main__":
    sys.exit(main())
