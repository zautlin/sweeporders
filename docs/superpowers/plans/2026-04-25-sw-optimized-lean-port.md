# sw_optimized Lean Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `sweeporders` from a deep `src/`-tree (~14k LOC across 8 packages) into a flat email-portable layout of 4 top-level Python files (`process.py`, `aggregate.py`, `report.py`, `config.py`) co-located with `data/`, suitable for emailing to a server and running standalone. Production target: multi-day × multi-ticker batch runs, so CLI and report layouts are multi-partition first-class.

**Architecture:** Physically consolidate Stage 1+2 into `process.py`, Stage 3+4 into `aggregate.py`, and Stage 5+6 (with multi-cut aggregation) into `report.py`. Keep the existing `_legacy.py` simulator as the matching engine — the rewrite track is paused until the new structure is settled. Each script is a standalone CLI entry point that accepts `--dates` (list) and `--tickers` (list or `--auto-tickers`) and reads from `data/raw/` → writes to `data/processed/` → `data/outputs/` → `data/reports/`. The pandas backend is removed; Polars + DuckDB are the only data paths. Per-stage parity gates against a baseline captured at the very start of the work guarantee no behavioural drift during consolidation. Local parity uses CBA/20240505 (the only complete bundle on laptop); real multi-day/multi-ticker parity runs deferred to the server.

**Tech Stack:** Python 3.11+, Polars, DuckDB, NumPy, multiprocessing (stdlib), pytest. Branch `sw_optimized` (already created from `final` HEAD).

---

## ⚠️ Local Parity Gate — Known Coverage Gap

The CBA/20240505 dataset on the laptop produces ZERO simulator matches: the contra pool contains only `ordertype=1` (regular orders), nothing in `ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}`. Consequently:

- ✅ **Local parity validates Stage 1** (sweep eligibility filtering, partitioning, contra-pool construction, reference-data joins).
- ❌ **Local parity does NOT validate Stage 2 matching logic** (midpoint calc, MAQ/SFMQ, iceberg, crossing keys, APB, Phase 2 resting). Both legacy and ported code produce empty `simulated_trades.csv`, so any matching-logic regression in `process.py` will silently pass `tests/test_parity.py`.

**Mitigation:** Task 9 (deferred, server-side) is the load-bearing gate for the simulator. Before declaring "port complete" beyond `sw_optimized` branch, the server multi-day × multi-ticker run MUST execute, and any byte-diff there is a release blocker.

**Implementer note (Task 1):** Harness uses content-hash (decompressed) rather than raw byte-equality for `.csv.gz` files because gzip embeds an mtime header. Plain `.csv` files remain true byte-equality. See `tests/test_parity.py`.

---

## Pre-flight context

**Cuts (definitively dropped, do not port):**

| Source | LOC | Reason |
|---|---|---|
| `src/console.py` | 319 | duplicate entry point |
| `src/analysis/data_explorer.py` | 774 | exploratory tool, off critical path |
| `src/analysis/validation.py` | 457 | dev-only sanity checker |
| `src/aggregation/aggregate_volume_analysis.py` | 483 | research artifact |
| `src/aggregation/analyze_aggregated_results.py` | 858 | research artifact, replaced by report.py multi-cuts |
| `src/discovery/security_discovery.py` | 338 | replaced by inline auto-discovery (~30 LOC) inside process.py |
| `src/utils/statistics_layer.py` | 492 | scipy 3-tier engine, off by default |
| pandas branches inside `file_utils.py` and transforms | scattered | Polars+DuckDB only |
| `PROCESSING_MODE='stream'` references | scattered | unimplemented |

**Total LOC dropped: ~3.7k.**

**Kept (consolidated into the 4 new files):**

| Target | Sources | Source LOC |
|---|---|---|
| `config.py` | `src/config/config.py` + `src/config/column_schema.py` + `src/config/system_config.py` | 1,091 |
| `process.py` | `src/main.py` + `src/pipeline/{data_processor, reference_data, partition_processor, pipeline_stages, pipeline_config, pipeline_output}.py` + `src/pipeline/sweep_simulator/_legacy.py` + `src/utils/{file_utils, io_backend, data_utils, normalization}.py` + ~30 LOC inline auto-discover (replaces `security_discovery.py`) | ~5,800 |
| `aggregate.py` | `src/pipeline/{trade_metrics_calculator, execution_comparison}.py` | 1,773 |
| `report.py` | `src/analysis/{sweep_execution_analyzer, volume_analyzer, unmatched_analyzer}.py` + `src/aggregation/aggregate_sweep_results.py` + new multi-cut rollup helpers | ~2,300 (after slimming) |

**Aggregation cuts in `report.py`:** day, ticker, participant, volume-bucket, session-phase. (Sector deferred per user instruction.)

**Phase 2 resting:** kept (master switch `SIMULATE_RESTING_PHASE` and all sub-flags survive the move into `config.py`).

**Sprint 1 rewrite WIP:** `src/pipeline/sweep_simulator/_rewrite.py` and `tests/test_sweep_rewrite.py` are preserved on a separate archive branch (Task 0). They are NOT in the email bundle. The rewrite resumes after the new structure is stable.

**Email bundle = 4 .py + requirements.txt + (optional) tests/.** No `src/` tree.

---

## File Structure (end state)

```
sweeporders/
├── process.py                 # Stage 1+2: ingest + simulate
├── aggregate.py               # Stage 3+4: metrics + comparison
├── report.py                  # Stage 5+6: per-security + multi-cut rollups
├── config.py                  # constants, column schema, knobs
├── requirements.txt
├── tests/
│   ├── test_parity.py         # baseline byte-diff harness (CBA/20240505)
│   ├── test_process_smoke.py
│   ├── test_aggregate_smoke.py
│   └── test_report_smoke.py
└── data/
    ├── raw/                   # input CSVs (server-side)
    ├── processed/{date}/{orderbookid}/    # process.py output (per-partition)
    ├── outputs/{date}/{orderbookid}/      # aggregate.py output (per-partition)
    └── reports/               # report.py output (FLAT — cross-date rollups)
        ├── by_day.csv
        ├── by_ticker.csv
        ├── by_participant.csv
        ├── by_volume_bucket.csv
        ├── by_session_phase.csv
        └── per_security/
            └── {date}_{orderbookid}.csv
```

---

## Task 0: Preserve Sprint 1 Rewrite WIP

**Files:**
- Create branch: `rewrite_wip_archive` from current `sw_optimized` HEAD
- Modify: working tree on `sw_optimized` (committing current WIP as a checkpoint)

- [ ] **Step 1: Create archive branch carrying the rewrite WIP**

```bash
git stash push -u -m "rewrite-wip-snapshot"
git checkout -b rewrite_wip_archive
git stash pop
git add src/pipeline/sweep_simulator/__init__.py \
        src/pipeline/sweep_simulator/_rewrite.py \
        src/pipeline/sweep_simulator/_legacy.py \
        tests/test_sweep_rewrite.py \
        tests/test_sweep_parity.py \
        scripts/verify_sprint1_task1.sh \
        docs/superpowers/specs/2026-04-23-stage2-simulator-efficiency-design.md \
        CLAUDE.md \
        docs/superpowers/plans/
git commit -m "checkpoint: Sprint 1 rewrite WIP (Tasks 1-3, parity harness stub)"
```

- [ ] **Step 2: Verify archive exists and return to `sw_optimized`**

```bash
git log --oneline rewrite_wip_archive | head -3
git checkout sw_optimized
git status
```

Expected: `rewrite_wip_archive` shows the new commit on top; `sw_optimized` shows the same uncommitted WIP it had before (untouched).

- [ ] **Step 3: Commit the same WIP on `sw_optimized` as a starting checkpoint**

```bash
git add src/pipeline/sweep_simulator/__init__.py \
        src/pipeline/sweep_simulator/_rewrite.py \
        src/pipeline/sweep_simulator/_legacy.py \
        tests/test_sweep_rewrite.py \
        tests/test_sweep_parity.py \
        scripts/verify_sprint1_task1.sh \
        docs/superpowers/specs/2026-04-23-stage2-simulator-efficiency-design.md \
        CLAUDE.md \
        docs/superpowers/plans/
git commit -m "checkpoint: pre-port state on sw_optimized (rewrite WIP preserved on rewrite_wip_archive)"
```

Expected: clean `git status`; `git log -1 --oneline` shows the checkpoint.

---

## Task 1: Capture Parity Baseline (CBA/20240505)

**Note:** Laptop only has one complete raw-data bundle: CBA/20240505 (orders + trades + nbbo + session + participants all present for that date). Parity baseline uses that. Multi-day/multi-ticker parity is deferred to Task 9 (server-side).

**Files:**
- Create: `tests/parity_baseline/20240505_cba/` (output snapshot dir)
- Create: `tests/test_parity.py` (parity harness)

- [ ] **Step 1: Run the existing pipeline end-to-end on CBA/20240505**

```bash
cd src
python main.py --ticker cba --date 20240505
cd ..
```

Expected: exits 0; populated `data/processed/20240505/{orderbookid}/`, `data/outputs/20240505/{orderbookid}/`, `data/aggregated/20240505/` (if Stage 6 ran).

- [ ] **Step 2: Snapshot baseline outputs**

```bash
mkdir -p tests/parity_baseline/20240505_cba/processed
mkdir -p tests/parity_baseline/20240505_cba/outputs
cp -R data/processed/20240505/. tests/parity_baseline/20240505_cba/processed/
cp -R data/outputs/20240505/.   tests/parity_baseline/20240505_cba/outputs/
```

- [ ] **Step 3: Write the parity harness**

Create `tests/test_parity.py`:

```python
"""Byte-diff harness: compare current outputs against tests/parity_baseline/.
Local parity uses CBA/20240505. Multi-day/multi-ticker parity runs on server (Task 9)."""
from pathlib import Path
import hashlib

BASELINE = Path(__file__).parent / "parity_baseline" / "20240505_cba"
LIVE_PROCESSED = Path(__file__).parent.parent / "data" / "processed" / "20240505"
LIVE_OUTPUTS   = Path(__file__).parent.parent / "data" / "outputs"   / "20240505"

def _hash(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

def _diff_tree(baseline_root: Path, live_root: Path) -> list[str]:
    diffs = []
    for b in baseline_root.rglob("*.csv"):
        rel = b.relative_to(baseline_root)
        l = live_root / rel
        if not l.exists():
            diffs.append(f"MISSING in live: {rel}")
        elif _hash(b) != _hash(l):
            diffs.append(f"BYTE-DIFF: {rel}")
    return diffs

def test_processed_parity():
    diffs = _diff_tree(BASELINE / "processed", LIVE_PROCESSED)
    assert not diffs, "\n".join(diffs)

def test_outputs_parity():
    diffs = _diff_tree(BASELINE / "outputs", LIVE_OUTPUTS)
    assert not diffs, "\n".join(diffs)
```

- [ ] **Step 4: Verify the harness passes against itself (current pipeline)**

```bash
rm -rf data/processed/20240505 data/outputs/20240505
cd src && python main.py --ticker cba --date 20240505 && cd ..
python -m pytest tests/test_parity.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit baseline + harness**

```bash
git add tests/parity_baseline/ tests/test_parity.py
git commit -m "test: capture CBA/20240505 parity baseline + byte-diff harness"
```

---

## Task 2: Build Flat `config.py`

**Files:**
- Create: `config.py` (top-level)
- Source: merge `src/config/config.py` (643) + `src/config/column_schema.py` (233) + `src/config/system_config.py` (215)

- [ ] **Step 1: Read all three source files end-to-end to identify shared symbols**

```bash
wc -l src/config/config.py src/config/column_schema.py src/config/system_config.py
```

Note exported names (constants, `ColumnAccessor`, `col`, `COLUMN_MAPPING`, worker-count helpers).

- [ ] **Step 2: Create `config.py` with concatenated content, ordered by dependency**

Order inside the file:
1. Imports (stdlib + numpy + psutil)
2. Path constants (`RAW_DIR`, `PROCESSED_DIR`, `OUTPUTS_DIR`, `REPORTS_DIR` — note: replace `AGGREGATED_DIR` with `REPORTS_DIR`)
3. `COLUMN_MAPPING` dict + `ColumnAccessor` class + `col` instance (from `column_schema.py`)
4. Order-type, dealsource, NBBO sentinel constants (from `config.py`)
5. Tunable knobs (`TICKER`, `DATE`, `MIN_ORDERS_THRESHOLD`, `MIN_TRADES_THRESHOLD`, `ENABLE_PARALLEL_PROCESSING=True` (default flipped on for the lean port), `ENABLE_STATISTICAL_TESTS=False`)
6. Phase 2 resting flags (`SIMULATE_RESTING_PHASE` + sub-flags) — copy verbatim from `config.py`
7. Volume bucket method (`VOLUME_BUCKET_METHOD = 'quartile'`)
8. System helpers (worker-count auto-detect from `system_config.py`)

**Drop while merging:**
- `USE_DUCKDB_IO` flag (always `True` semantics — used everywhere unconditionally)
- `USE_POLARS_TRANSFORMS` flag (always `True`)
- `PROCESSING_MODE='stream'` references
- `NBBO_SOURCE='EXTERNAL'` branches (keep `'INTERNAL'` only — confirm with user if EXTERNAL is needed)

- [ ] **Step 3: Smoke-test the new config**

Create `tests/test_config_smoke.py`:

```python
def test_config_imports_and_exposes_expected_names():
    import config
    assert hasattr(config, "col")
    assert hasattr(config, "COLUMN_MAPPING")
    assert config.SWEEP_ORDER_TYPE == 2048
    assert 64 in config.ELIGIBLE_MATCHING_ORDER_TYPES
    assert config.MIN_ORDERS_THRESHOLD == 100
    assert config.SIMULATE_RESTING_PHASE in (True, False)
    assert config.RAW_DIR.name == "raw"
```

```bash
python -m pytest tests/test_config_smoke.py -v
```

Expected: 1 passed.

- [ ] **Step 4: Commit**

```bash
git add config.py tests/test_config_smoke.py
git commit -m "feat: flat top-level config.py merging config/column_schema/system_config"
```

---

## Task 3: Build Flat `process.py` (Stages 1–2)

**Files:**
- Create: `process.py` (top-level)
- Source: merge `src/main.py` + `src/pipeline/pipeline_config.py` + `src/pipeline/pipeline_stages.py` (Stages 1–2 portions only) + `src/pipeline/pipeline_output.py` + `src/pipeline/data_processor.py` + `src/pipeline/reference_data.py` + `src/pipeline/partition_processor.py` + `src/pipeline/sweep_simulator/_legacy.py` + `src/utils/file_utils.py` + `src/utils/io_backend.py` + `src/utils/data_utils.py` + `src/utils/normalization.py` + ~30 LOC inline auto-discovery (replaces `src/discovery/security_discovery.py`)

This task is the largest. Break into sub-steps strictly.

- [ ] **Step 1: Lay out `process.py` skeleton**

Create `process.py` with this top-level section structure (no logic yet, just headers):

```python
"""sweeporders process.py — Stages 1+2 (ingest + simulate).

Run: python process.py --dates 20240505,20240905 [--tickers cba,drr | --auto-tickers] [--workers N]
     python process.py --dates 20240505 --tickers cba
Reads:  data/raw/
Writes: data/processed/{date}/{orderbookid}/

Consolidates: data_processor, reference_data, partition_processor, sweep_simulator/_legacy,
              file_utils, io_backend, data_utils, normalization, pipeline_stages (Stages 1-2),
              auto-discover (inline, multi-date).
"""
from __future__ import annotations
import argparse, sys, os, logging, multiprocessing as mp
from pathlib import Path
import numpy as np
import polars as pl
import duckdb
import config

# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Logging + helpers (from utils/normalization, utils/data_utils)
# ─────────────────────────────────────────────────────────────────────────────
# Section 2: I/O backend (from utils/io_backend, utils/file_utils — Polars+DuckDB only)
# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Reference data loaders (from pipeline/reference_data)
# ─────────────────────────────────────────────────────────────────────────────
# Section 4: Auto-discovery (replaces discovery/security_discovery.py)
# ─────────────────────────────────────────────────────────────────────────────
# Section 5: Stage 1 — order/trade extraction + partitioning (from data_processor)
# ─────────────────────────────────────────────────────────────────────────────
# Section 6: Stage 2 — sweep simulator (from sweep_simulator/_legacy)
# ─────────────────────────────────────────────────────────────────────────────
# Section 7: Per-partition orchestration (from pipeline_stages, partition_processor)
# ─────────────────────────────────────────────────────────────────────────────
# Section 8: CLI (from main.py + pipeline_config)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    raise NotImplementedError("scaffold only — fill in sections 1–8")
```

- [ ] **Step 2: Fill Section 1 (helpers) + Section 2 (I/O)**

Copy bodies from `src/utils/normalization.py`, `src/utils/data_utils.py`, `src/utils/file_utils.py`, `src/utils/io_backend.py`. **Drop pandas branches** in `file_utils.py` — keep only Polars/DuckDB code paths.

After this step, run:
```bash
python -c "import process"
```
Expected: imports clean, no errors.

- [ ] **Step 3: Fill Section 3 (reference data)**

Copy `src/pipeline/reference_data.py` body into Section 3. Replace any `from config.column_schema import col` → already covered (config.py lives at root).

- [ ] **Step 4: Fill Section 4 (inline auto-discovery, multi-date)**

Replace 338 LOC `security_discovery.py` with a ~40 LOC multi-date version: for each date, glob `data/raw/orders/*_{date}_orders.csv`, count rows via DuckDB, filter by `MIN_ORDERS_THRESHOLD`, return `(date, ticker)` tuples.

```python
def auto_discover_partitions(dates: list[str]) -> list[tuple[str, str]]:
    """For each date, find tickers whose orders file has >= MIN_ORDERS_THRESHOLD rows.
    Returns list of (date, ticker) partition keys."""
    con = duckdb.connect()
    out = []
    for date in dates:
        pattern = str(config.RAW_DIR / "orders" / f"*_{date}_orders.csv")
        rows = con.execute(
            f"SELECT regexp_extract(filename, '([a-z]+)_{date}_orders\\.csv', 1) AS ticker, "
            f"COUNT(*) AS n FROM read_csv_auto('{pattern}', filename=True) GROUP BY ticker"
        ).fetchall()
        out.extend((date, t) for t, n in rows if n >= config.MIN_ORDERS_THRESHOLD)
    return out

def resolve_partitions(dates: list[str], tickers: list[str] | None) -> list[tuple[str, str]]:
    """If tickers provided, return cartesian product filtered to existing raw files.
    Otherwise auto-discover."""
    if tickers is None:
        return auto_discover_partitions(dates)
    return [(d, t) for d in dates for t in tickers
            if (config.RAW_DIR / "orders" / f"{t}_{d}_orders.csv").exists()]
```

- [ ] **Step 5: Fill Section 5 (Stage 1)**

Copy `src/pipeline/data_processor.py` body. Adjust internal imports (replace `from config.column_schema` → already at root; replace `from utils.X` with internal calls — all utils now in Section 1/2).

- [ ] **Step 6: Fill Section 6 (Stage 2 simulator)**

Copy `src/pipeline/sweep_simulator/_legacy.py` body verbatim. Adjust imports.

- [ ] **Step 7: Fill Section 7 (orchestration) + Section 8 (CLI)**

Copy the Stage 1+2 portions of `src/pipeline/pipeline_stages.py` and the worker pool from `src/pipeline/partition_processor.py`. Strip Stage 3+ code (those move to aggregate.py). Copy CLI from `src/main.py` + `src/pipeline/pipeline_config.py`. Adjust `__main__` block.

- [ ] **Step 8: Smoke-test that `process.py` runs on CBA/20240505**

```bash
rm -rf data/processed/20240505
python process.py --dates 20240505 --tickers cba
ls data/processed/20240505/
```

Expected: same partition contents as the legacy run (orders.csv, sweep_orders.csv, simulated_trades.csv, etc.).

- [ ] **Step 9: Run parity harness (Stage 1+2 outputs only)**

```bash
python -m pytest tests/test_parity.py::test_processed_parity -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add process.py
git commit -m "feat: flat process.py — Stage 1+2 consolidated, drop pandas/discovery/console"
```

---

## Task 4: Build Flat `aggregate.py` (Stages 3–4)

**Files:**
- Create: `aggregate.py`
- Source: `src/pipeline/trade_metrics_calculator.py` (655) + `src/pipeline/execution_comparison.py` (1,118)

- [ ] **Step 1: Lay out `aggregate.py` skeleton**

```python
"""sweeporders aggregate.py — Stages 3+4 (metrics + real-vs-sim comparison).

Run: python aggregate.py --dates 20240505,20240905 [--tickers cba,drr | --auto-tickers]
     python aggregate.py --dates 20240505 --tickers cba
Reads:  data/processed/{date}/{orderbookid}/
Writes: data/outputs/{date}/{orderbookid}/comparison.csv (+ intermediate metrics CSVs)
"""
from __future__ import annotations
import argparse, logging
from pathlib import Path
import polars as pl
import numpy as np
import config

# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Trade metrics calculator (from trade_metrics_calculator.py)
# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Execution comparison (from execution_comparison.py)
# ─────────────────────────────────────────────────────────────────────────────
# Section 3: CLI orchestration (per-partition loop)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    raise NotImplementedError("scaffold only")
```

- [ ] **Step 2: Fill Section 1 — copy `trade_metrics_calculator.py` body**

All 36 metrics (5 groups: fill, price, exec cost, timing, market) preserved verbatim. Adjust imports to reference `config` (top-level).

- [ ] **Step 3: Fill Section 2 — copy `execution_comparison.py` body**

Real-vs-sim join on `orderid`, comparison report generation.

- [ ] **Step 4: Fill Section 3 — CLI**

Iterate `data/processed/{date}/*/` partitions, call metric calculators on each, emit `data/outputs/{date}/{orderbookid}/comparison.csv`.

- [ ] **Step 5: Smoke-test on CBA/20240505**

```bash
rm -rf data/outputs/20240505
python aggregate.py --dates 20240505 --tickers cba
ls data/outputs/20240505/
```

Expected: comparison.csv + per-metric CSVs under each orderbookid subdir.

- [ ] **Step 6: Run parity harness (Stage 3+4 outputs)**

```bash
python -m pytest tests/test_parity.py::test_outputs_parity -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add aggregate.py
git commit -m "feat: flat aggregate.py — Stage 3+4 metrics + comparison consolidated"
```

---

## Task 5: Build Flat `report.py` (Stages 5–6 + Multi-Cut)

**Files:**
- Create: `report.py`
- Source: slim `src/analysis/sweep_execution_analyzer.py` (1,002) + `src/analysis/volume_analyzer.py` (517) + `src/analysis/unmatched_analyzer.py` (437) + `src/aggregation/aggregate_sweep_results.py` (254) + new multi-cut helpers

- [ ] **Step 1: Lay out `report.py` skeleton with 5 cuts**

```python
"""sweeporders report.py — Stages 5+6 (per-security analysis + multi-cut rollups).

Run: python report.py                           # reports over EVERYTHING in data/outputs/
     python report.py --dates 20240505,20240905 # restrict to listed dates

Reads:  data/outputs/{date}/{orderbookid}/
Writes: data/reports/                           # FLAT — rollups span dates
          ├── per_security/{date}_{orderbookid}.csv
          ├── by_day.csv
          ├── by_ticker.csv
          ├── by_participant.csv
          ├── by_volume_bucket.csv
          └── by_session_phase.csv

Aggregation cuts: day, ticker, participant, volume-bucket, session-phase.
(Sector deferred per user instruction 2026-04-25.)
"""
from __future__ import annotations
import argparse, logging
from pathlib import Path
import polars as pl
import duckdb
import config

# ─────────────────────────────────────────────────────────────────────────────
# Section 1: Per-security analyzers (from analysis/* — slimmed)
# ─────────────────────────────────────────────────────────────────────────────
# Section 2: Cross-security aggregation (from aggregate_sweep_results.py)
# ─────────────────────────────────────────────────────────────────────────────
# Section 3: Multi-cut rollups (NEW — by day/ticker/participant/volume/session)
# ─────────────────────────────────────────────────────────────────────────────
# Section 4: CLI orchestration
# ─────────────────────────────────────────────────────────────────────────────
```

- [ ] **Step 2: Fill Section 1 — slim per-security analyzers**

Copy bodies from `analysis/sweep_execution_analyzer.py`, `volume_analyzer.py`, `unmatched_analyzer.py`. **Drop:** any scipy/statistics_layer references; any plotting code; verbose pretty-print helpers. **Keep:** the metric extraction logic that produces summary rows.

- [ ] **Step 3: Fill Section 2 — cross-security aggregation**

Copy `aggregation/aggregate_sweep_results.py` body. This collects per-security summary rows into one DataFrame.

- [ ] **Step 4: Fill Section 3 — multi-cut rollup helpers**

```python
METRIC_COLUMNS = [
    # All 36 metrics from aggregate.py — explicit list to avoid silent drift
    # Fill group:
    "fill_rate", "weighted_fill_rate", "n_orders_filled", "n_orders_unfilled",
    # Price group:
    "vwap_real", "vwap_sim", "price_improvement_bps", "midpoint_capture_rate",
    # ... full enumeration of 36 metrics ...
]

def rollup_by(df: pl.DataFrame, group_cols: list[str]) -> pl.DataFrame:
    """Aggregate METRIC_COLUMNS by group_cols using volume-weighted means."""
    return (
        df.group_by(group_cols)
          .agg([pl.col(m).mean().alias(m) for m in METRIC_COLUMNS] +
               [pl.col("matched_quantity").sum().alias("total_qty"),
                pl.col("orderid").n_unique().alias("n_orders")])
          .sort(group_cols)
    )

def cut_by_day(df):              return rollup_by(df, ["date"])
def cut_by_ticker(df):           return rollup_by(df, ["date", "ticker"])
def cut_by_participant(df):      return rollup_by(df, ["date", "participantid"])
def cut_by_volume_bucket(df):    return rollup_by(df, ["date", "volume_bucket"])
def cut_by_session_phase(df):    return rollup_by(df, ["date", "session_phase"])
```

- [ ] **Step 5: Fill Section 4 — CLI**

Glob `data/outputs/{date}/*/comparison.csv` for every date under `data/outputs/` (or only the `--dates` listed), concat into one DataFrame, write per-security summaries to `data/reports/per_security/{date}_{orderbookid}.csv`, then write each multi-cut rollup to `data/reports/`.

- [ ] **Step 6: Smoke-test**

```bash
rm -rf data/reports
python report.py
ls data/reports/
```

Expected: `by_day.csv`, `by_ticker.csv`, `by_participant.csv`, `by_volume_bucket.csv`, `by_session_phase.csv`, `per_security/`.

- [ ] **Step 7: Spot-check one rollup**

```bash
python -c "import polars as pl; print(pl.read_csv('data/reports/by_ticker.csv'))"
```

Expected: one row per (date, ticker), all 36 metric columns populated.

- [ ] **Step 8: Commit**

```bash
git add report.py
git commit -m "feat: flat report.py — Stage 5+6 + multi-cut rollups (day/ticker/participant/volume/session)"
```

---

## Task 6: Verify Multiprocessing Actually Scales

**Files:**
- Create: `tests/test_multiprocessing_speedup.py`
- Modify: `process.py` (add `--workers N` CLI flag if not already wired)

- [ ] **Step 1: Add timing instrumentation to `process.py`**

In Section 7, around the worker pool dispatch:

```python
import time
t0 = time.perf_counter()
# ... existing partition pool code ...
elapsed = time.perf_counter() - t0
logging.info(f"process.py finished {n_partitions} partitions in {elapsed:.2f}s with {workers} workers")
```

- [ ] **Step 2: Run over ALL available partitions with 1 worker**

Use every raw file that exists as a multi-partition workload.

```bash
rm -rf data/processed
ALL_DATES=$(ls data/raw/orders/ | grep -oE '[0-9]{8}' | sort -u | paste -sd, -)
time python process.py --dates "$ALL_DATES" --auto-tickers --workers 1
```

Record wall-clock time `T1`.

- [ ] **Step 3: Run with 4 workers**

```bash
rm -rf data/processed
time python process.py --dates "$ALL_DATES" --auto-tickers --workers 4
```

Record `T4`.

- [ ] **Step 4: Run with 8 workers**

```bash
rm -rf data/processed
time python process.py --dates "$ALL_DATES" --auto-tickers --workers 8
```

Record `T8`.

- [ ] **Step 5: Write the speedup test**

Create `tests/test_multiprocessing_speedup.py`:

```python
"""Verify multiprocessing actually delivers speedup, not just thread spawn overhead.
Runs over ALL available raw partitions (multi-date × multi-ticker)."""
import subprocess, time, shutil, re
from pathlib import Path

def _all_dates() -> str:
    raw = Path("data/raw/orders")
    dates = sorted({m.group() for f in raw.glob("*_orders.csv")
                    for m in [re.search(r"\d{8}", f.name)] if m})
    return ",".join(dates)

def _run(workers: int) -> float:
    shutil.rmtree("data/processed", ignore_errors=True)
    t0 = time.perf_counter()
    subprocess.run(
        ["python", "process.py", "--dates", _all_dates(),
         "--auto-tickers", "--workers", str(workers)],
        check=True, capture_output=True
    )
    return time.perf_counter() - t0

def test_4_workers_at_least_2x_faster_than_1():
    t1 = _run(1)
    t4 = _run(4)
    speedup = t1 / t4
    assert speedup >= 2.0, f"only {speedup:.2f}× speedup with 4 workers (T1={t1:.1f}s, T4={t4:.1f}s)"
```

- [ ] **Step 6: Run the speedup test**

```bash
python -m pytest tests/test_multiprocessing_speedup.py -v -s
```

Expected: PASS with logged speedup ≥ 2.0×. **If it fails:** investigate — likely a serial bottleneck (e.g., DuckDB connection contention, partition I/O serialisation). Report findings before proceeding to Task 7.

- [ ] **Step 7: Commit**

```bash
git add tests/test_multiprocessing_speedup.py process.py
git commit -m "test: verify multiprocessing scales (≥2× on 4 workers)"
```

---

## Task 7: End-to-End Bundle Rehearsal (the "email" test)

**Files:**
- Create: `requirements.txt` (top-level, polars + duckdb + numpy + psutil + pytest)
- Create: `bundle/` (temporary, gitignored)

- [ ] **Step 1: Write `requirements.txt`**

```
polars>=1.0
duckdb>=1.0
numpy>=1.24
psutil>=5.9
pytest>=7.0
```

- [ ] **Step 2: Stage the email bundle**

```bash
mkdir -p /tmp/sw_bundle
cp process.py aggregate.py report.py config.py requirements.txt /tmp/sw_bundle/
mkdir -p /tmp/sw_bundle/data
cp -R data/raw /tmp/sw_bundle/data/
ls /tmp/sw_bundle/
```

Expected: 5 files + `data/raw/` only.

- [ ] **Step 3: Create a fresh venv in the bundle dir + install deps**

```bash
cd /tmp/sw_bundle
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

- [ ] **Step 4: Run the full pipeline from the bundle dir**

```bash
cd /tmp/sw_bundle
.venv/bin/python process.py   --dates 20240505 --tickers cba
.venv/bin/python aggregate.py --dates 20240505 --tickers cba
.venv/bin/python report.py
ls data/processed/20240505/
ls data/outputs/20240505/
ls data/reports/
```

Expected: all three scripts exit 0; outputs populated; no `ImportError`.

- [ ] **Step 5: Diff bundle outputs against the original repo's outputs**

```bash
cd /Users/agautam/workspace/python/sweeporders
diff -r /tmp/sw_bundle/data/processed/20240505 data/processed/20240505 || echo "DIFFS FOUND"
diff -r /tmp/sw_bundle/data/outputs/20240505   data/outputs/20240505   || echo "DIFFS FOUND"
```

Expected: no diffs.

- [ ] **Step 6: Clean up**

```bash
rm -rf /tmp/sw_bundle
```

- [ ] **Step 7: Commit `requirements.txt`**

```bash
git add requirements.txt
git commit -m "feat: add requirements.txt for email bundle"
```

---

## Task 8: Delete `src/` Tree

**Files:**
- Delete: entire `src/` directory
- Delete: `tests/test_sweep_rewrite.py`, `tests/test_sweep_parity.py` (rewrite-track tests; preserved on `rewrite_wip_archive`)
- Delete: `scripts/verify_sprint1_task1.sh` (Sprint 1 verify; preserved on archive branch)
- Delete: any tests under `tests/` that import from `src.*`

- [ ] **Step 1: Audit which tests still import from `src/` or refer to legacy paths**

```bash
grep -rln 'from src' tests/ || echo "none"
grep -rln 'src\.pipeline\|src\.config\|src\.utils\|src\.analysis\|src\.aggregation\|src\.discovery' tests/ || echo "none"
```

For each match: either rewrite to import from `process`/`aggregate`/`report`/`config`, or delete the test if it's exercising removed code.

- [ ] **Step 2: Re-run all parity tests against the flat layout (sanity check before deletion)**

```bash
rm -rf data/processed/20240505 data/outputs/20240505 data/reports
python process.py   --dates 20240505 --tickers cba
python aggregate.py --dates 20240505 --tickers cba
python report.py
python -m pytest tests/test_parity.py -v
```

Expected: 2 passed.

- [ ] **Step 3: Delete `src/` and rewrite-WIP files**

```bash
git rm -r src/
git rm tests/test_sweep_rewrite.py tests/test_sweep_parity.py 2>/dev/null || true
git rm scripts/verify_sprint1_task1.sh 2>/dev/null || true
git rm -r src/data 2>/dev/null || true
```

- [ ] **Step 4: Update `CLAUDE.md`**

Edit `CLAUDE.md`:
- Replace the "Running the Pipeline" section: `cd src && python main.py …` → `python process.py …` then `aggregate.py` then `report.py`.
- Replace the "Module Responsibilities" table with the new 4-file structure.
- Drop references to `console.py`, `discovery/`, `analysis/data_explorer.py`, `validation.py`, `statistics_layer.py`, `analyze_aggregated_results.py`, `aggregate_volume_analysis.py`.
- Add note: rewrite-WIP preserved on branch `rewrite_wip_archive`.

- [ ] **Step 5: Re-run parity one more time (post-delete)**

```bash
rm -rf data/processed/20240505 data/outputs/20240505 data/reports
python process.py   --dates 20240505 --tickers cba
python aggregate.py --dates 20240505 --tickers cba
python report.py
python -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Final commit**

```bash
git add -A
git commit -m "chore: delete src/ — flat 4-file lean port complete"
git log --oneline -10
```

Expected: clean working tree; clean linear history of the port; `sw_optimized` branch ready to merge or email.

---

## Task 9: Deferred Server Parity Gate (Multi-Day × Multi-Ticker)

**Note:** This task is **not executed on the laptop**. It runs on the 32-core server after the email bundle arrives, because the laptop only has one complete raw-data bundle (CBA/20240505). This is the real "port is correct" gate.

**Files:**
- Create: `tests/test_parity_server.py` (runs on server)
- Create: `tests/parity_baseline_server/` (captured on server from legacy code, before deleting src/)

- [ ] **Step 1: On server — capture multi-day × multi-ticker baseline from legacy `src/` tree**

Before the server-side `src/` gets replaced with the flat layout, run the legacy pipeline over 2+ dates × 3+ tickers and snapshot the outputs into `tests/parity_baseline_server/`.

```bash
# Example on server (pick real dates/tickers available there):
cd src
for date in 20240904 20240905; do
  for ticker in bhp drr wtc cba; do
    python main.py --ticker $ticker --date $date
  done
done
cd ..
mkdir -p tests/parity_baseline_server
cp -R data/processed tests/parity_baseline_server/
cp -R data/outputs   tests/parity_baseline_server/
```

- [ ] **Step 2: On server — run the flat 4-file pipeline over the same (date, ticker) set**

```bash
rm -rf data/processed data/outputs data/reports
python process.py   --dates 20240904,20240905 --tickers bhp,drr,wtc,cba --workers 8
python aggregate.py --dates 20240904,20240905 --tickers bhp,drr,wtc,cba --workers 8
python report.py
```

- [ ] **Step 3: Byte-diff against the server baseline**

```bash
diff -r tests/parity_baseline_server/processed data/processed || echo "DIFFS"
diff -r tests/parity_baseline_server/outputs   data/outputs   || echo "DIFFS"
```

Expected: no diffs.

- [ ] **Step 4: Sanity-check report outputs for all-metric coverage**

```bash
python -c "
import polars as pl
for f in ['by_day','by_ticker','by_participant','by_volume_bucket','by_session_phase']:
    df = pl.read_csv(f'data/reports/{f}.csv')
    print(f, df.shape, df.columns)
"
```

Expected: each rollup file has all 36 metric columns.

- [ ] **Step 5: Record findings back in this plan doc**

Add a line to the plan under "Server Parity" status (✅ passed on `<date>` against `<dataset>`, or ❌ with diffs listed) so the artifact is traceable.

---

## Self-Review

- **Spec coverage:**
  - "1 file for processing" → `process.py` (Task 3) ✓
  - "1 file to aggregate and match" → `aggregate.py` (Task 4) ✓
  - "1 file for reporting" → `report.py` (Task 5) ✓
  - "directly under where the file is processed" → top-level co-located with `data/` ✓
  - Polars + DuckDB only → pandas branches dropped in Tasks 2, 3 ✓
  - Multiprocessing kept and verified → Tasks 3 + 6 ✓
  - Phase 2 resting kept → Phase 2 flags preserved verbatim in Task 2 ✓
  - Port `_legacy.py` (rewrite later) → Task 0 archives rewrite WIP; Task 3 step 6 copies legacy verbatim ✓
  - All 36 metrics × multi-cut → Task 5 step 4 (`METRIC_COLUMNS` enumerated explicitly) ✓
  - Aggregation by day, ticker, participant, volume-bucket, session-phase (no sector) → Task 5 step 4 ✓
  - Multi-day × multi-ticker CLI first-class → `--dates` + `--tickers`/`--auto-tickers` on all scripts (Tasks 3, 4, 5) ✓
  - Flat reports layout (cross-date rollups) → `data/reports/` directly (Task 5) ✓
  - Local parity on CBA/20240505 → Task 1; server parity multi-day × multi-ticker → Task 9 ✓
  - Email bundle rehearsal → Task 7 ✓
  - Delete `src/` → Task 8 ✓

- **Placeholder scan:** `METRIC_COLUMNS` list in Task 5 step 4 currently truncates with `# ... full enumeration of 36 metrics ...`. Resolve at execution time by reading the actual metric names from `trade_metrics_calculator.py` and pasting them in. This is a known gap, called out here so the implementer knows to expand it.

- **Type consistency:** `cut_by_*` helpers all return `pl.DataFrame`; `rollup_by` returns `pl.DataFrame`; CLI in Section 4 of `report.py` consumes them uniformly. ✓

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-25-sw-optimized-lean-port.md`.**

Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
