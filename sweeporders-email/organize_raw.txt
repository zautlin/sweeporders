"""Distribute loose raw CSV/Parquet files into data/raw/{subdir}/ by filename.

If your raw files are sitting flat in `data/` or `data/raw/` rather than in
their proper subdirectories, this script moves them into place. It looks at
the filename suffix (right before the extension) to decide where each file
belongs:

    *_orders.{csv,parquet}        → data/raw/orders/
    *_trades.{csv,parquet}        → data/raw/trades/
    *_nbbo.{csv,parquet}          → data/raw/nbbo/
    *_session.{csv,parquet}       → data/raw/session/
    *_orderbook.{csv,parquet}     → data/raw/reference/
    *_reference.{csv,parquet}     → data/raw/reference/
    *_par.{csv,parquet}           → data/raw/participants/
    *_participants.{csv,parquet}  → data/raw/participants/

Subdirectories are created if they don't exist. Existing destination files
are NOT overwritten — the script reports a conflict and leaves the source
in place.

Usage:
    python organize_raw.py            # move (default)
    python organize_raw.py --copy     # copy instead of move
    python organize_raw.py --dry-run  # preview without touching anything
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import config


# Map suffix-before-extension → target subdir under data/raw/.
SUFFIX_TO_SUBDIR = {
    'orders':       'orders',
    'trades':       'trades',
    'nbbo':         'nbbo',
    'session':      'session',
    'orderbook':    'reference',
    'reference':    'reference',
    'par':          'participants',
    'participants': 'participants',
}

VALID_EXTS = {'.csv', '.parquet'}

SUFFIX_RE = re.compile(r'_([a-z]+)$', re.IGNORECASE)


def classify(p: Path) -> str | None:
    """Return the target subdir name for `p`, or None if unrecognised."""
    if p.suffix not in VALID_EXTS:
        return None
    m = SUFFIX_RE.search(p.stem)
    if not m:
        return None
    return SUFFIX_TO_SUBDIR.get(m.group(1).lower())


def collect_loose_files(roots: list[Path]) -> list[tuple[Path, str]]:
    """Find every classifiable file in `roots` (non-recursive into subdirs).

    A file is "loose" if it sits directly under `data/` or `data/raw/` —
    not already nested in one of the canonical subdirs.
    """
    out: list[tuple[Path, str]] = []
    canonical_subdirs = set(SUFFIX_TO_SUBDIR.values())
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if not p.is_file():
                continue
            if p.parent.name in canonical_subdirs:
                continue                                # already in place
            kind = classify(p)
            if kind is None:
                continue
            out.append((p, kind))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog='organize_raw.py',
        description='Distribute loose raw files into data/raw/{subdir}/.')
    ap.add_argument('--copy', action='store_true',
                    help='Copy instead of move (default: move).')
    ap.add_argument('--dry-run', action='store_true',
                    help='Preview without touching the filesystem.')
    args = ap.parse_args()

    raw_dir = Path(config.RAW_DIR)
    data_dir = Path(config.DATA_DIR)

    # Always make sure the canonical subdirs (and processed/outputs) exist.
    canonical_dirs = [
        raw_dir / sub for sub in
        ('orders', 'trades', 'nbbo', 'session', 'reference', 'participants')
    ] + [Path(config.PROCESSED_DIR), Path(config.OUTPUTS_DIR), Path(config.REPORTS_DIR)]
    for d in canonical_dirs:
        if not d.is_dir():
            if args.dry_run:
                print(f"[dry-run] mkdir -p {d}")
            else:
                d.mkdir(parents=True, exist_ok=True)
                print(f"  created {d}")

    # Find loose files in data/ and data/raw/.
    loose = collect_loose_files([data_dir, raw_dir])

    if not loose:
        print("No loose files to organise — every file is already in its subdir.")
        return 0

    print(f"\nFound {len(loose)} loose file(s) to {'copy' if args.copy else 'move'}:")
    moved = skipped = conflict = 0
    for src, kind in loose:
        dst_dir = raw_dir / kind
        dst = dst_dir / src.name

        if dst.exists():
            print(f"  ⊘ CONFLICT  {src.name:40} → {kind}/  (target exists)")
            conflict += 1
            continue

        if args.dry_run:
            print(f"  [dry-run]  {src.name:40} → {kind}/")
        else:
            dst_dir.mkdir(parents=True, exist_ok=True)
            if args.copy:
                shutil.copy2(src, dst)
                print(f"  ✓ COPIED   {src.name:40} → {kind}/")
            else:
                shutil.move(str(src), str(dst))
                print(f"  ✓ MOVED    {src.name:40} → {kind}/")
            moved += 1

    print(f"\nDone. {moved} {'copied' if args.copy else 'moved'}, {conflict} conflict(s).")
    return 0 if conflict == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
