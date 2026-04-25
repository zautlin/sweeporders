"""Stage 0: convert data/raw/*.csv → data/raw/*.parquet (zstd).

One-time prep step. Once converted, process.py / aggregate.py
prefer the parquet versions automatically (see config.get_input_files).

Why: at 100 GB+ scale, single-threaded CSV parsing dominates Stage 1.
DuckDB's COPY does the parse in parallel, and parquet is 5-10× smaller
on disk + supports predicate pushdown so subsequent reads materialise
only the rows the pipeline actually needs.

Usage:
    python convert_raw.py                  # convert all data/raw/*/*.csv
    python convert_raw.py --force          # rewrite even if parquet is newer
    python convert_raw.py --subdirs orders,trades   # only these subdirs
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

import config

SUBDIRS = ('orders', 'trades', 'nbbo', 'session', 'reference', 'participants')


def _needs_convert(csv_path: Path, pq_path: Path, force: bool) -> bool:
    if force or not pq_path.exists():
        return True
    return pq_path.stat().st_mtime < csv_path.stat().st_mtime


def convert(csv_path: Path, force: bool = False, conn=None) -> tuple[Path, str]:
    pq_path = csv_path.with_suffix('.parquet')
    if not _needs_convert(csv_path, pq_path, force):
        return pq_path, 'skip'

    conn = conn or duckdb.connect()
    csv_str = str(csv_path).replace("'", "''")
    pq_str = str(pq_path).replace("'", "''")
    conn.execute(
        f"COPY (SELECT * FROM read_csv_auto('{csv_str}')) "
        f"TO '{pq_str}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    return pq_path, 'wrote'


def main():
    parser = argparse.ArgumentParser(prog='convert_raw.py',
                                     description='Convert data/raw/*.csv → data/raw/*.parquet (zstd).')
    parser.add_argument('--force', action='store_true',
                        help='Rewrite parquet even if it is newer than the CSV.')
    parser.add_argument('--subdirs', default=','.join(SUBDIRS),
                        help='Comma-separated subdirs to convert (default: all).')
    args = parser.parse_args()

    subdirs = [s.strip() for s in args.subdirs.split(',') if s.strip()]
    raw = Path(config.RAW_DIR)
    if not raw.is_dir():
        print(f"[convert_raw] no raw dir: {raw}", file=sys.stderr)
        sys.exit(2)

    conn = duckdb.connect()
    total_csv = total_pq = 0
    n_wrote = n_skip = 0
    for sub in subdirs:
        d = raw / sub
        if not d.is_dir():
            continue
        csvs = sorted(d.glob('*.csv'))
        if not csvs:
            continue
        print(f"[convert_raw] {sub}/  ({len(csvs)} csv file(s))")
        for csv in csvs:
            pq, action = convert(csv, force=args.force, conn=conn)
            csv_mb = csv.stat().st_size / (1024 * 1024)
            pq_mb = pq.stat().st_size / (1024 * 1024) if pq.exists() else 0.0
            ratio = csv_mb / pq_mb if pq_mb > 0 else 0.0
            tag = '✓' if action == 'wrote' else '⊘'
            print(f"  {tag} {csv.name:40s}  {csv_mb:8.1f} MB → {pq_mb:6.1f} MB  ({ratio:4.1f}×)")
            total_csv += csv_mb
            total_pq += pq_mb
            n_wrote += action == 'wrote'
            n_skip += action == 'skip'

    if total_pq > 0:
        print(f"\n[convert_raw] done. wrote={n_wrote} skip={n_skip}  "
              f"total: {total_csv:.1f} MB CSV → {total_pq:.1f} MB parquet "
              f"({total_csv/total_pq:.1f}× smaller)")


if __name__ == '__main__':
    main()
