"""
Hydrate the builder/CI fingerprint store from the published gallery.
====================================================================
gallery/traces/ is the canonical published record; fingerprints/ is a
disposable working view. The CI runner starts with an EMPTY store (the
directory is gitignored), which left match-and-serve switched off: every
duplicate request paid full generation price because the dedup layers had
nothing to match against (built 2026-06, guarded 2026-09-25, enabled here).

This script flows data in the ONE safe direction — gallery -> store — so the
request workflow can serve existing traces instead of regenerating them:

    python hydrate_store.py                 # copy missing + rebuild index
    python hydrate_store.py --force         # store copies refresh from gallery
    (then: python matcher.py --backfill     # embed for the semantic stage)

Rules (the stale-source lesson, CORRECTIONS.md):
  - NEVER writes into gallery/ — it only reads there.
  - Trace files are copied byte-for-byte (so a later store->gallery copy of a
    served ID is a no-op diff, never a drift).
  - Locally, existing store files are kept unless --force: the builder's
    working copies may hold in-progress work. Index entries for ids that are
    not in gallery (local drafts) are preserved.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def index_entry(fp: dict) -> dict:
    """Build the FingerprintStore index entry from a published trace payload —
    the same fields FingerprintStore.save() writes, so find_matching() and the
    polarity judge see hydrated and freshly saved traces identically."""
    lex = fp.get("lexical") or {}
    gen = fp.get("genealogy") or {}
    glex = gen.get("lexical") or {}
    gcon = gen.get("conceptual") or {}
    return {
        "canonical_phrase": lex.get("canonical_phrase", ""),
        "diagnostic_ngrams": lex.get("diagnostic_ngrams", []),
        "stopword_stripped_signature": lex.get("stopword_stripped_signature", ""),
        "lexical_first_attested_date": glex.get("first_attested_date", ""),
        "lexical_first_attested_source": glex.get("first_attested_source", ""),
        "lexical_status": glex.get("status", ""),
        "conceptual_first_attested_date": gcon.get("first_attested_date", ""),
        "conceptual_first_attested_source": gcon.get("first_attested_source", ""),
        "conceptual_status": gcon.get("status", ""),
        "created_at": fp.get("created_at", ""),
        "last_updated": fp.get("last_updated", ""),
        "scope": fp.get("scope", {}),
    }


def hydrate(gallery_dir: Path, store_dir: Path, force: bool = False) -> dict:
    if not gallery_dir.is_dir():
        raise SystemExit(f"[hydrate] no gallery at {gallery_dir} — nothing to do.")
    store_dir.mkdir(parents=True, exist_ok=True)

    index_path = store_dir / "index.json"
    index = {}
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            index = {}

    copied = kept = indexed = skipped = 0
    for src in sorted(gallery_dir.glob("*.json")):
        try:
            fp = json.loads(src.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[hydrate] skipping unreadable {src.name}: {e}", file=sys.stderr)
            skipped += 1
            continue
        fid = fp.get("fingerprint_id")
        if not fid or f"{fid}.json" != src.name:
            print(f"[hydrate] skipping {src.name}: fingerprint_id mismatch "
                  f"({fid!r})", file=sys.stderr)
            skipped += 1
            continue
        dst = store_dir / src.name
        if dst.exists() and not force:
            kept += 1
        else:
            shutil.copyfile(src, dst)   # byte-for-byte: no reserialization drift
            copied += 1
        index[fid] = index_entry(fp)    # index always reflects the published copy
        indexed += 1

    index_path.write_text(json.dumps(index, indent=2, default=str),
                          encoding="utf-8")
    return {"copied": copied, "kept": kept, "indexed": indexed,
            "skipped": skipped, "index_total": len(index)}


def main():
    ap = argparse.ArgumentParser(
        description="Hydrate the fingerprint store from gallery/traces "
                    "(one direction only: gallery -> store).")
    ap.add_argument("--gallery-dir", default=str(ROOT / "gallery" / "traces"))
    ap.add_argument("--store-dir", default=str(ROOT / "fingerprints"))
    ap.add_argument("--force", action="store_true",
                    help="Refresh existing store copies from gallery "
                         "(CI-safe; locally this replaces working copies).")
    args = ap.parse_args()

    stats = hydrate(Path(args.gallery_dir), Path(args.store_dir), force=args.force)
    print(f"[hydrate] {stats['indexed']} published traces -> store "
          f"({stats['copied']} copied, {stats['kept']} kept, "
          f"{stats['skipped']} skipped); index now {stats['index_total']} entries")


if __name__ == "__main__":
    main()
