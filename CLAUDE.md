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
```

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

**Optional IO/Transform Backends:** Feature flags in `config/config.py` — `USE_DUCKDB_IO = False` (glob-based partition scanning via DuckDB) and `USE_POLARS_TRANSFORMS = False` (vectorized in-memory transforms). Both fall back to pandas when disabled with no behavioral change.

### Module Responsibilities

| Module | Responsibility |
|--------|---------------|
| `main.py` | Entry point; orchestrates all stages |
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
