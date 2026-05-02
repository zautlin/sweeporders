# CLAUDE.md

Guidance for Claude Code working on this repository.

## Project Overview

**SweepOrders** is a quantitative finance research pipeline that analyses Centre Point sweep order execution quality on the ASX. It compares actual lit market execution against simulated dark pool (midpoint matching) execution to determine optimal order routing strategies.

## Layout (post-lean-port, 2026-04-25)

The codebase was consolidated from a deep `src/`-tree (~14k LOC across 8 packages) into 4 flat top-level Python files. The legacy structure is preserved on branches `final` and `main`; the current branch (`sw_optimized`) carries the lean version.

```
sweeporders/
├── config.py        796 LOC   constants, column schema, knobs
├── process.py     5,323 LOC   Stages 1+2 (ingest + simulate)
├── aggregate.py   5,219 LOC   Stages 3+4 (metrics + real-vs-sim comparison)
├── report.py        279 LOC   Stages 5+6 (per-security + multi-cut rollups)
├── requirements.txt
├── activate.sh                venv activation
├── data/
│   ├── raw/                   input CSVs (orders, trades, nbbo, session, reference, participants)
│   ├── processed/{date}/{orderbookid}/   process.py output
│   ├── outputs/{date}/{orderbookid}/     aggregate.py output
│   └── reports/                          report.py output (FLAT — cross-date rollups)
├── tests/
│   ├── test_parity.py         byte-diff harness against captured baseline
│   ├── test_config_smoke.py   config import + key-knob assertions
│   └── parity_baseline/       captured legacy outputs for CBA/20240505
├── bundle/                    167 MB email-portable artifact (gitignored)
└── docs/
    ├── bi.txt                 ASX Centre Point behaviour spec
    └── dd.txt                 ASX Centre Point data dictionary spec
```

## Environment Setup

```bash
source activate.sh                   # activates swp_env/
pip install -r requirements.txt      # polars, duckdb, pandas, numpy, psutil
```

## Running the Pipeline

Each stage is a standalone CLI. All scripts run from the repo root.

```bash
# Stage 1+2: ingest + simulate
python process.py --dates 20240505 --tickers cba
python process.py --dates 20240505,20240905 --auto-tickers --workers 4

# Stage 3+4: metrics + real-vs-sim comparison
python aggregate.py --dates 20240505 --tickers cba

# Stage 5+6: per-security + multi-cut rollups
python report.py                              # report on everything in data/outputs/
python report.py --dates 20240505,20240905    # restrict
```

Outputs flow `data/raw/` → `data/processed/` → `data/outputs/` → `data/reports/`. The reports include 5 cuts: `by_day.csv`, `by_ticker.csv`, `by_volume_bucket.csv` (active) plus `by_participant.csv` and `by_session_phase.csv` (stubs — need joins against raw orders / session data).

## Tests

```bash
python -m pytest tests/                             # all (5 tests, ~0.2s)
python -m pytest tests/test_parity.py -v            # byte-diff against CBA/20240505 baseline
python -m pytest tests/test_config_smoke.py -v
```

**Local parity gate caveat:** the CBA/20240505 dataset on the laptop produces zero simulator matches (contra pool has only `ordertype=1`). Local parity validates Stage 1 (extraction, partitioning, contra-pool construction) but **does not exercise the simulator's matching logic**. Real multi-day × multi-ticker parity must run on the 32-core server before declaring "port complete" beyond the laptop.

## Architecture

### 6-Stage Pipeline (re-mapped onto 4 flat files)

| Stage | Steps | File | Purpose |
|-------|-------|------|---------|
| 1 | 1–6 | `process.py` | Extract Centre Point orders, match trades, load reference, partition by `{date}/{orderbookid}` |
| 2 | 7 | `process.py` | Simulate dark pool midpoint matching |
| 3 | 8–9 | `aggregate.py` | Calculate 36 real + simulated metrics (5 groups: fill, price, exec cost, timing, market) |
| 4 | 10 | `aggregate.py` | Compare real vs. simulated metrics, generate per-partition comparison CSV |
| 5 | 11–12 | `report.py` | Per-security summaries written to `data/reports/per_security/` |
| 6 | — | `report.py` | Cross-cut rollups: by_day, by_ticker, by_volume_bucket (active); by_participant, by_session_phase (stubs) |

### Spec sources

`docs/bi.txt` and `docs/dd.txt` are the ASX Centre Point behaviour specs that drive simulator semantics — consult them before changing matching logic.

### Key Design Patterns

**Partition Key:** `{trade_date}/{orderbookid}` (e.g., `2024-09-05/85603`). Note the trade date may differ from the raw filename's date label — the pipeline partitions by actual trade timestamps from inside the file. Data flows: `data/raw/` → `data/processed/{date}/{orderbookid}/` → `data/outputs/{date}/{orderbookid}/` → `data/reports/`.

**Centre Point Sweep Orders:** Order type `2048`. Only sessions `OPEN`/`CONTINUOUS` allow matching; `PRE_OPEN`, `AUCTION`, etc. do not.

**Schema-Independent Columns:** All column-name mappings are centralised in `config.COLUMN_MAPPING`. The `ColumnAccessor` exposes them as `config.col.common.orderid`, `config.col.common.timestamp`, etc. Use these instead of raw string column names — schema variations only need to change `COLUMN_MAPPING`.

**Multi-Backend (legacy paths preserved):** `config.USE_DUCKDB_IO` and `config.USE_POLARS_TRANSFORMS` are both `False` by default — the lean port preserves the pandas codepath that produced the parity baseline. Backend migration to polars/duckdb is a follow-up sprint (decision logged in commit `1639336`).

**Module Self-Aliases inside Consolidated Files:** `process.py` and `aggregate.py` set `dp = pp = ec = fu = du = ss = sys.modules[__name__]` near their CLI sections so legacy intra-module references like `dp.load_partition_data` and `pp.process_partitions_parallel` continue to resolve to the same-file functions after consolidation.

**Parallel Processing:** `ENABLE_PARALLEL_PROCESSING = True` by default. Worker count auto-detected via helpers in `config.py`. Override with `--workers N`.

**Inline `SecurityDiscovery` Shim:** `process.py` and `aggregate.py` each contain a ~30-LOC `SecurityDiscovery` class replacing the original 338-LOC discovery package. It reads `data/raw/orders/{ticker}_{date}_orders.csv` to map ticker → orderbookid using `csv.DictReader`.

### Stage 2 semantics (the simulator) — non-obvious rules

These drive `process.py`'s simulator section (originally `src/pipeline/sweep_simulator/_legacy.py`) — easy to regress if you don't know them.

**Sweep selection funnel (Stage 1 pre-filter).** Only sweeps that pass *all three* filters reach the simulator:
1. `exchangeordertype == 2048` (sweep).
2. Real-world completion: final `changereason == 3` (TRADED) AND final `leavesQuantity == 0` AND a `changereason == 6` (NEW_ORDER) event exists.
3. All of the order's real trades have `dealsource == 1` (lit/continuous).

Sweeps that did not fully fill on the lit market — and sweeps that actually matched via Centre Point in reality — are **not simulated**.

**Per-sweep simulation window.** Each sweep is scanned over `[first_execution_time, last_execution_time]`. Session filter `MATCHING_SESSION_STATES = {'OPEN', 'CONTINUOUS'}` rejects all other states (`PRE_OPEN`, `AUCTION`, `POST_CLOSE`, `CLOSED`, `PRE_CSPA`, `CSPA`, `ADJUST`, `ADJUST_ON`, `PURGE_ORDERS`, `SYSTEM_MAINTENANCE`).

**Effective timestamp vs timestamp.** `effective_timestamp = timechanged` when the order has lost priority (`orderbookposition > 0` OR `changereason ∈ {7, 8, 39}`); otherwise `effective_timestamp = timestamp`. Plain `changereason == 5` (user update) does **not** cost priority.

**Shared contra inventory.** `order_remaining` persists across every sweep in the partition. Sweeps are processed in `(effective_timestamp, sequence)` order and compete for inventory. Iceberg display-slice modelling was removed on `swp_cleaned_phase_2` — full contra quantity is treated as visible.

**MAQ early-break vs skip.** `minimumquantity` / `singlefillminimumquantity` failures either `continue` (skip this contra) or `break` (stop the sweep's scan). When `sweep_remaining_qty < sweep_maq` AND the sweep has already partially filled, the simulator `break`s. Spec-mandated asymmetry.

**Phase 2 (resting leg) — REMOVED.** The simulator is now active-window-only: each sweep matches against contras alive in `[first_execution_time, last_execution_time]` and that's it. The unfilled remainder is *not* modelled as resting on either dark or lit. Removed on branch `swp_cleaned_phase_2` after the inventory-tracking inconsistency between phases was identified.

**Simulator invariants:**
- `SWEEP_ORDER_TYPE = 2048`; `ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}`.
- NBBO sentinel `INT64_SENTINEL = -9223372036854775808` means "unavailable"; fall back to order-level `bid`/`offer`.
- APB (`midtick ∈ {5, 6}` on a type-4096 contra): matches at contra limit price, `MIN_BLOCK_SIZE` enforced, no NBBO constraint, dealsources 50/51.
- Dealsources: 1 = lit continuous, 46 = preference, 47 = Centre Point, 50/51 = APB.

### Configuration Defaults (`config.py`)

```python
TICKER = 'drr'
DATE = '20240905'
SWEEP_ORDER_TYPE = 2048
ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}
NBBO_SOURCE = 'INTERNAL'              # only INTERNAL supported (EXTERNAL branch dropped)
MIN_ORDERS_THRESHOLD = 100
MIN_TRADES_THRESHOLD = 10
ENABLE_PARALLEL_PROCESSING = True     # flipped True for the lean port
USE_DUCKDB_IO = False                 # preserved at False to match parity baseline
USE_POLARS_TRANSFORMS = False         # preserved at False to match parity baseline
PROCESSING_MODE = 'file'              # 'file' | 'memory'  (stream removed)
VOLUME_BUCKET_METHOD = 'quartile'     # 'quartile' | 'quintile' | 'custom'
```

### Input Data Layout

```
data/raw/
  orders/       {ticker}_{date}_orders.csv
  trades/       {ticker}_{date}_trades.csv
  nbbo/         {ticker}_{date}_nbbo.csv
  session/      {date}_session.csv
  reference/    {date}_orderbook.csv
  participants/ {date}_par.csv
```

## Email-portable bundle

`bundle/` (gitignored, 167 MB) is a self-contained release artefact: 4 .py files + requirements.txt + `data/raw/`. `cd bundle && python process.py … && python aggregate.py … && python report.py` runs end-to-end with all outputs landing inside `bundle/data/`. Ship via `tar czf sweeporders-lean.tar.gz -C /Users/agautam/workspace/python/sweeporders bundle/`.

## Branches

- `sw_optimized` (current): lean port — 4 flat files, no `src/`.
- `rewrite_wip_archive`: Sprint 1 simulator-rewrite WIP (Tasks 1–3 + parity-harness stub) preserved for the post-port rewrite effort.
- `final`, `main`, etc.: legacy `src/`-tree layout, preserved for reference.
