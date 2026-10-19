"""
Redaction absence check (2026-10-19)
====================================
Asserts that none of a set of redacted terms appears anywhere in the given
files. The term list is the payload, so it must NEVER live in this repo:
pass it as a file path OUTSIDE the repository (the maintainer keeps one per
redacted trace). Run before publishing or re-rendering anything derived
from a redacted trace:

    python tools/redaction_check.py --terms <path-outside-repo> \
        gallery/traces/<id>.json gallery/cards/<id>.html

Exit 0 = clean; exit 1 = a term is present (named by index, never echoed).
Case-insensitive, whole-string containment: stricter than the word-bounded
scrub that produced the artifact, so anything the scrub missed trips here.
"""

import argparse
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description="Assert redacted terms are absent from files.")
    ap.add_argument("--terms", required=True,
                    help="path to the term file (one term per line; keep OUTSIDE the repo)")
    ap.add_argument("paths", nargs="+", help="files to check")
    args = ap.parse_args()

    terms_path = Path(args.terms).resolve()
    repo = Path(__file__).resolve().parent.parent
    if repo in terms_path.parents:
        sys.exit("refusing: the term file must live OUTSIDE the repository")

    terms = [t.strip().lower() for t in
             terms_path.read_text(encoding="utf-8").splitlines() if t.strip()]
    if not terms:
        sys.exit("refusing: empty term list")

    failed = 0
    for p in args.paths:
        blob = Path(p).read_bytes().decode("utf-8", errors="replace").lower()
        hits = [i for i, t in enumerate(terms) if t in blob]
        status = f"TERM(S) PRESENT: index {hits}" if hits else "clean"
        print(f"  [{'FAIL' if hits else 'OK'}] {p}: {status}")
        failed += bool(hits)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
