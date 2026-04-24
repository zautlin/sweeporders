# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**SweepOrders** is a quantitative finance research pipeline that analyzes Centre Point sweep order execution quality on the ASX. It compares actual lit market execution against simulated dark pool (midpoint matching) execution to determine optimal order routing strategies.

## Environment Setup

```bash
source activate.sh          # Activates swp_env/ virtual environment
pip install -r requirements.txt  # pandas>=2.0, numpy>=1.24, scipy>=1.10, psutil>=5.9
```

## Running the Pipeline

`main.py` **must be run from `src/`** — imports are module-relative (`from config.column_schema import col`).

```bash
cd src
python main.py                                        # Default: DRR, 2024-09-05
python main.py --ticker bhp --date 20240905           # Specific security/date
python main.py --auto-discover --date 20240905        # All valid securities
python main.py --stage 1 --stage 2                    # Run specific stages only
python main.py --enable-stats                         # Enable scipy statistical tests
python main.py --parallel                             # Enable parallel processing
python main.py --list-dates                           # Show available dates
python main.py --list-securities --date 20240905      # Show securities for a date
python console.py                                     # Interactive console variant
```

### Spec sources

`docs/bi.txt` and `docs/dd.txt` are the ASX Centre Point behaviour specs that drive simulator semantics — consult them before changing matching logic. Design notes live in `docs/SIMULATION_DESIGN.md` and `docs/CROSS_SECURITY_METRIC_ANALYSIS.md`. User-facing guide (if present): `docs/EXECUTION_GUIDE.md`. There is no `README.md` at the repo root.

## Tests

```bash
python -m pytest tests/                              # All tests
python -m pytest tests/test_metrics_simple.py        # Single test file
python tests/test_integration_drr.py                 # Integration test (requires DRR data)
```

## Architecture

### 6-Stage Pipeline

| Stage | Steps | Purpose |
|-------|-------|---------|
| 1 | 1-6 | Extract Centre Point orders, match trades, load reference data, partition by date/security |
| 2 | 7 | Simulate dark pool midpoint matching |
| 3 | 8-9 | Calculate real and simulated trade metrics (36 metrics across 5 groups) |
| 4 | 10 | Compare real vs. simulated execution quality |
| 5 | 11-12+ | Per-security analysis (sweep execution, unmatched orders, volume quartiles) |
| 6 | — | Cross-security aggregation and portfolio-level insights |

### Key Design Patterns

**Partition Key:** `{date}/{orderbookid}` (e.g., `20240905/100`). All processing is partitioned this way. Data flows: `data/raw/` → `data/processed/{date}/{orderbookid}/` → `data/outputs/{date}/{orderbookid}/` → `data/aggregated/`.

**Centre Point Sweep Orders:** Order type `2048`. Only sessions `OPEN`/`CONTINUOUS` allow matching; `PRE_OPEN`, `AUCTION`, etc. do not.

**Schema-Independent Columns:** All column name mappings are centralized in `config/config.py` → `COLUMN_MAPPING`. The `ColumnAccessor` in `config/column_schema.py` provides a single point of change for schema variations — use `col.common.orderid`, `col.common.timestamp`, etc. instead of raw string column names.

**3-Tier Statistics:** `utils/statistics_layer.py` implements graceful degradation — Tier 1 (always: descriptive via pandas/numpy), Tier 2 (approximate: normal approximation, no scipy), Tier 3 (exact: scipy t-tests, p-values, CI). Enable with `--enable-stats`.

**Parallel Processing:** Off by default (`ENABLE_PARALLEL_PROCESSING = False`). Uses `ProcessPoolExecutor` in `pipeline/partition_processor.py`. Worker count auto-detected from CPU/memory via `config/system_config.py`.

**Optional IO/Transform Backends:** Feature flags in `config/config.py` — `USE_DUCKDB_IO = False` (glob-based partition scanning via DuckDB) and `USE_POLARS_TRANSFORMS = False` (vectorized in-memory transforms). Both fall back to pandas when disabled with no behavioral change. Per-worker DuckDB connections live in `utils/io_backend.py` (thread-local, in-memory).

**Processing Mode:** `PROCESSING_MODE` in `config/config.py` controls inter-stage persistence:
- `'file'` (default) — writes each stage's partitions to `data/processed/`, allowing resume from any stage.
- `'memory'` — skips intermediate writes; DataFrames passed directly between stages. Faster, but must run end-to-end.
- `'stream'` — reserved for future event-driven mode (not implemented).

### Stage 2 semantics (the simulator)

These are the non-obvious rules that drive `pipeline/sweep_simulator.py` — easy to regress if you don't know them.

**Sweep selection funnel (Stage 1 pre-filter).** Only sweeps that pass *all three* filters in `data_processor.py` reach the simulator:
1. `exchangeordertype == 2048` (sweep).
2. Real-world completion: final `changereason == 3` (TRADED) AND final `leavesQuantity == 0` AND a `changereason == 6` (NEW_ORDER) event exists in the order's history.
3. All of the order's real trades have `dealsource == 1` (lit/continuous).

Sweeps that did not fully fill on the lit market — and sweeps that actually matched via Centre Point in reality — are **not simulated**.

**Per-sweep simulation window.** Each sweep is scanned over `[first_execution_time, last_execution_time]`, where:
- `first_execution_time` = minimum `timestamp` for that `orderid` in the orders file (arrival).
- `last_execution_time` = max `tradetime` across the order's real lit trades (real fill-out).

A sweep that filled in 20 ms gets a 20 ms window — not a full-day scan. `MATCHING_SESSION_STATES = {'OPEN', 'CONTINUOUS'}` further filters; all other states (`PRE_OPEN`, `AUCTION`, `POST_CLOSE`, `CLOSED`, `PRE_CSPA`, `CSPA`, `ADJUST`, `ADJUST_ON`, `PURGE_ORDERS`, `SYSTEM_MAINTENANCE`) are rejected both at contra arrival and again at match time.

**Effective timestamp vs timestamp.** `effective_timestamp = timechanged` when the order has lost priority (`orderbookposition > 0` OR `changereason ∈ {7, 8, 39}`); otherwise `effective_timestamp = timestamp`. Plain `changereason == 5` (user update) does **not** cost priority.

**Shared contra inventory.** `order_remaining` and `iceberg_slice_consumed` are single dicts that persist across every sweep in the partition. Sweeps are processed in `(effective_timestamp, sequence)` order and compete for inventory; do not assume per-sweep independence.

**MAQ early-break vs skip.** `minimumquantity` / `singlefillminimumquantity` failures either `continue` (skip this contra) or `break` (stop the sweep's scan) — when `sweep_remaining_qty < sweep_maq` *and* the sweep has already partially filled, the simulator `break`s. That asymmetry is spec-mandated.

**Phase 2 (resting leg).** Gated by `cfg.SIMULATE_RESTING_PHASE` (master switch). When enabled, sweeps with leftover qty rest in the book:
- `SIMULATE_LIT_RESTING` — include lit TradeMatch leg.
- `RESTING_LIT_BOOK_MODE` = `'full'` (build a proper book, Option A) or `'scan'` (just scan eligible contras, Option B).
- `RESTING_USE_MIDTICK`, `RESTING_LIT_USE_LIMIT` — resting-price rules.
- `RESTING_MODEL_CANCELLATION` — expire at session end.
- `RESTING_APPLY_*` — toggle crossing-keys, session filter, MAQ, preferencing, iceberg.
Dark leg uses a contra-centric loop per bi.txt §24.10: same-participant preferencing first, then FIFO. Phase 2 lit leg does **not** reposition on iceberg refresh — that's intentional.

**Simulator invariants** (regression hazards):
- `SWEEP_ORDER_TYPE = 2048`; `ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}` — do not filter passive types out of the contra pool.
- NBBO sentinel `INT64_SENTINEL = -9223372036854775808` on `national_bid`/`national_offer` means "unavailable"; fall back to order-level `bid`/`offer`.
- APB (`midtick ∈ {5, 6}` on a type-4096 contra): matches at contra limit price, `MIN_BLOCK_SIZE` enforced, no NBBO constraint, dealsources 50/51.
- Dealsources: 1 = lit continuous, 46 = preference, 47 = Centre Point, 50/51 = APB.

### Stage 3 ground truth

The "real" metric set in Stage 3 comes from the lit `data/raw/trades/*.csv` (dealsource 1), aggregated per `orderid`. The "simulated" set comes from `simulated_trades` emitted by Stage 2. `execution_comparison.py` joins the two on `orderid` in Stage 4.

### Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `main.py` | Entry point; orchestrates all stages |
| `console.py` | Interactive console variant of the entry point |
| `pipeline/pipeline_config.py` | CLI argument parsing; runtime config building |
| `pipeline/pipeline_stages.py` | Stage execution logic |
| `pipeline/data_processor.py` | Order/trade extraction, session filtering, partitioning |
| `pipeline/sweep_simulator.py` | Core simulation engine (midpoint matching, amendments, iceberg, sweep-to-sweep) |
| `pipeline/trade_metrics_calculator.py` | 36-metric calculator (fill, price, exec cost, timing, market) |
| `pipeline/execution_comparison.py` | Real vs. simulated metrics comparison and report generation |
| `pipeline/reference_data.py` | Tick size and participant info loading |
| `discovery/security_discovery.py` | Auto-discovery of securities from raw data |
| `analysis/sweep_execution_analyzer.py` | Per-security execution analysis with statistics |
| `analysis/unmatched_analyzer.py` | Root cause analysis for unmatched orders |
| `analysis/volume_analyzer.py` | Order volume quartile/bucket analysis |
| `aggregation/aggregate_sweep_results.py` | Cross-security result merging |
| `utils/file_utils.py` | Safe CSV I/O, partition path management, DuckDB glob queries |
| `utils/io_backend.py` | Per-worker DuckDB connection + DuckDB/Polars/pandas bridges |
| `utils/normalization.py` | Column-name normalisation helpers |
| `utils/data_utils.py` | Shared DataFrame utilities |
| `utils/statistics_layer.py` | 3-tier statistical engine |
| `config/config.py` | Central configuration (paths, thresholds, order types, NBBO source) |
| `config/column_schema.py` | `ColumnAccessor` — schema-independent column access |

### Configuration Defaults (`config/config.py`)

```python
TICKER = 'drr'
DATE = '20240905'
SWEEP_ORDER_TYPE = 2048
ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}
NBBO_SOURCE = 'INTERNAL'          # 'INTERNAL' (orders file) or 'EXTERNAL' (nbbo.csv.gz)
MIN_ORDERS_THRESHOLD = 100
MIN_TRADES_THRESHOLD = 10
ENABLE_PARALLEL_PROCESSING = False
ENABLE_STATISTICAL_TESTS = False
USE_DUCKDB_IO = False             # DuckDB glob-based aggregation (Phase 2-3 optimization)
USE_POLARS_TRANSFORMS = False     # Polars for in-memory transforms (Phase 4 optimization)
PROCESSING_MODE = 'file'          # 'file' | 'memory' | 'stream' (stream = future)
SIMULATE_RESTING_PHASE = …        # Master switch for Phase 2 (resting leg)
VOLUME_BUCKET_METHOD = 'quartile' # 'quartile', 'quintile', or 'custom'
```

### Input Data Layout

```
data/raw/
  orders/       {ticker}_{date}_orders.csv
  trades/       {ticker}_{date}_trades.csv
  nbbo/         {ticker}_{date}_nbbo.csv  (optional, used if NBBO_SOURCE='EXTERNAL')
  session/      {date}_session.csv
  reference/    {date}_ob.csv
  participants/ {date}_par.csv
```
