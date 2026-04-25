"""Row-equivalence parity harness against tests/parity_baseline/.

Local parity uses CBA/20240505. Multi-day/multi-ticker parity runs on server.

Format-aware: reads .parquet and .csv/.csv.gz transparently and compares
DataFrame contents row-wise (column-name & value equality after sorting).

To regenerate the baseline after a deliberate behavioural change, run::

    rm -rf data/processed data/outputs
    python process.py --dates 20240505 --tickers cba
    python aggregate.py --dates 20240505 --tickers cba
    rm -rf tests/parity_baseline/20240505_cba
    mkdir -p tests/parity_baseline/20240505_cba
    cp -R data/processed tests/parity_baseline/20240505_cba/processed
    cp -R data/outputs   tests/parity_baseline/20240505_cba/outputs
"""
from pathlib import Path

import pandas as pd
import pytest

BASELINE = Path(__file__).parent / "parity_baseline" / "20240505_cba"
LIVE_PROCESSED = Path(__file__).parent.parent / "data" / "processed"
LIVE_OUTPUTS = Path(__file__).parent.parent / "data" / "outputs"

TABLE_SUFFIXES = {".parquet", ".csv", ".gz"}


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix == ".gz":
        return pd.read_csv(path, compression="gzip")
    return pd.read_csv(path)


def _sort_key(df: pd.DataFrame) -> pd.DataFrame:
    """Stable row-order: sort by all columns to make compare order-insensitive."""
    if df.empty:
        return df
    return df.sort_values(by=list(df.columns), kind="stable").reset_index(drop=True)


def _diff_tree(baseline_root: Path, live_root: Path) -> list[str]:
    diffs: list[str] = []
    for b in sorted(p for p in baseline_root.rglob("*") if p.suffix in TABLE_SUFFIXES):
        rel = b.relative_to(baseline_root)
        l = live_root / rel
        if not l.exists():
            diffs.append(f"MISSING in live: {rel}")
            continue
        try:
            b_df = _sort_key(_read_table(b))
            l_df = _sort_key(_read_table(l))
        except Exception as e:
            diffs.append(f"READ-ERROR: {rel} → {e}")
            continue
        if list(b_df.columns) != list(l_df.columns):
            diffs.append(
                f"COLUMN-DIFF: {rel} baseline={list(b_df.columns)} live={list(l_df.columns)}"
            )
            continue
        if len(b_df) != len(l_df):
            diffs.append(f"ROW-COUNT-DIFF: {rel} baseline={len(b_df)} live={len(l_df)}")
            continue
        try:
            pd.testing.assert_frame_equal(
                b_df, l_df, check_dtype=False, check_exact=False, atol=1e-9
            )
        except AssertionError as e:
            diffs.append(f"VALUE-DIFF: {rel} — {str(e).splitlines()[0]}")
    return diffs


@pytest.mark.skipif(not (BASELINE / "processed").exists(),
                    reason="parity baseline not present (regenerate per docstring)")
def test_processed_parity():
    diffs = _diff_tree(BASELINE / "processed", LIVE_PROCESSED)
    assert not diffs, "\n".join(diffs)


@pytest.mark.skipif(not (BASELINE / "outputs").exists(),
                    reason="parity baseline not present (regenerate per docstring)")
def test_outputs_parity():
    diffs = _diff_tree(BASELINE / "outputs", LIVE_OUTPUTS)
    assert not diffs, "\n".join(diffs)
