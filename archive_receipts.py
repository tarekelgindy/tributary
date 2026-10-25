"""
Receipt archiving + verification crawl (2026-10-25, Batch C)
============================================================
Two jobs, one resumable rate-limited pass over the corpus's cited URLs:

  1. VERIFY the never-checked (the 28-event June lesson): plain HTTP
     existence for entries whose verification_status is "unchecked" —
     200 -> url-ok/verified, else url-error. Same semantics the trace
     verifier uses for its URL stage.
  2. ARCHIVE receipts against link rot: ask Wayback's availability API for
     a snapshot; store archive_url when one exists; when none does, request
     a save (SavePageNow, politely rate-limited, capped per run).

Priority order: trace anchors & crowns -> trace logs -> event carriers.
Resumable by construction (entries with an archive_url / a checked status
are skipped), so run it as often as patience allows:

    python archive_receipts.py --max-saves 80 --max-checks 400 --apply
"""

import argparse
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "TributaryFingerprintVerifier/0.1 (+https://github.com/tarekelgindy/tributary)"}
SKIP_ARCHIVE = ("web.archive.org", "archive.today", "archive.ph", "t.me/", "x.com/", "twitter.com/")


def _write_retry(path: Path, text: str, attempts: int = 4):
    """Windows AV/indexer locks surface as transient EINVAL/EACCES on write;
    retry briefly rather than losing a crawl slice (seen live 2026-10-25)."""
    for i in range(attempts):
        try:
            path.write_text(text, encoding="utf-8")
            return
        except OSError:
            if i == attempts - 1:
                raise
            time.sleep(0.6 * (i + 1))


def wayback_lookup(client, url: str) -> str:
    try:
        r = client.get("https://archive.org/wayback/available",
                       params={"url": url}, timeout=15)
        snap = (r.json().get("archived_snapshots") or {}).get("closest") or {}
        return snap.get("url") or "" if snap.get("available") else ""
    except Exception:  # noqa: BLE001
        return ""


def wayback_save(client, url: str) -> bool:
    try:
        r = client.get(f"https://web.archive.org/save/{url}", timeout=40)
        return r.status_code in (200, 302)
    except Exception:  # noqa: BLE001
        return False


def check_url(client, url: str) -> tuple:
    try:
        r = client.get(url, timeout=15, follow_redirects=True)
        ok = r.status_code == 200
        return ok, ("url-ok" if ok else f"url-error"), f"HTTP {r.status_code} (archiving crawl)"
    except Exception as e:  # noqa: BLE001
        return False, "url-error", f"{type(e).__name__} (archiving crawl)"


def iter_entries():
    """Yield (path, container_dict, entry_dict, priority). Lower = first."""
    for p in sorted((ROOT / "gallery" / "traces").glob("*.json")):
        fp = json.loads(p.read_text(encoding="utf-8"))
        for layer in ("lexical", "conceptual"):
            g = (fp.get("genealogy") or {}).get(layer) or {}
            anchor_id = g.get("primary_origin_id")
            for a in g.get("attestation_log") or []:
                pr = 0 if (a.get("instance_id") == anchor_id
                           or a.get("amplifier_role") == "originator") else 1
                yield p, fp, a, pr
    for p in sorted((ROOT / "gallery" / "events").glob("*.json")):
        ev = json.loads(p.read_text(encoding="utf-8"))
        for f in ev.get("framings") or []:
            for c in f.get("carriers") or []:
                yield p, ev, c, 2


def main():
    ap = argparse.ArgumentParser(description="Verify unchecked receipts and archive against rot.")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-saves", type=int, default=50)
    ap.add_argument("--max-checks", type=int, default=300)
    ap.add_argument("--max-lookups", type=int, default=200,
                    help="availability lookups are the hidden cost at corpus "
                         "scale — cap them so a slice finishes (learned when "
                         "the first run timed out on ~4k uncapped lookups)")
    ap.add_argument("--save-delay", type=float, default=12.0)
    args = ap.parse_args()

    rows = sorted(iter_entries(), key=lambda r: r[3])
    touched_files = {}
    checks = saves = lookups = 0
    client = httpx.Client(headers=UA)
    for p, container, e, pr in rows:
        url = str(e.get("source_url") or e.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        dirty = False
        # 1) verification for the never-checked
        status = str(e.get("verification_status") or "unchecked")
        if status == "unchecked" and checks < args.max_checks and "x.com" not in url:
            ok, st, note = check_url(client, url)
            checks += 1
            if args.apply:
                e["verified"] = ok
                e["verification_status"] = st
                e["verification_notes"] = note
                dirty = True
        # 2) archiving
        if (lookups < args.max_lookups and not str(e.get("archive_url") or "")
                and not any(s in url for s in SKIP_ARCHIVE)):
            snap = wayback_lookup(client, url)
            lookups += 1
            if snap:
                if args.apply:
                    e["archive_url"] = snap
                    dirty = True
            elif saves < args.max_saves and pr <= 1:
                if wayback_save(client, url):
                    saves += 1
                    time.sleep(args.save_delay)
        if dirty:
            touched_files[p] = container
            # flush incrementally: a killed long crawl must keep its progress
            if args.apply and len(touched_files) % 10 == 0:
                for fp_, c_ in touched_files.items():
                    _write_retry(fp_, json.dumps(c_, indent=2, ensure_ascii=False,
                                                 default=str))
        if (checks >= args.max_checks and saves >= args.max_saves
                and lookups >= args.max_lookups):
            break

    if args.apply:
        for p, container in touched_files.items():
            _write_retry(p, json.dumps(container, indent=2, ensure_ascii=False,
                                       default=str))
    print(f"checks={checks} lookups={lookups} saves-requested={saves} "
          f"files-updated={len(touched_files) if args.apply else 0}"
          f"{'' if args.apply else ' (DRY RUN)'}")
    if args.apply and touched_files:
        print("now: python publish.py (floor may re-evaluate newly-checked events)")


if __name__ == "__main__":
    main()
