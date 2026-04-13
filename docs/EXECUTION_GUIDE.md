# SweepOrders — Execution Guide

This guide covers how to organise your input data, run the pipeline, and interpret the reports it produces.

---

## End-to-End Flow

```
data/raw/                  ←  You place raw CSV files here (Step 1)
    orders/
    trades/
    session/
    reference/
    participants/
    nbbo/  (optional)
        ↓
    Stage 1  (python main.py --stage 1)
        ↓  Reads flat CSVs, filters Centre Point orders,
        ↓  partitions by date and security, saves compressed files
        ↓
data/processed/{date}/{security}/     ←  Structured partitions (Step 2)
    cp_orders_filtered.csv.gz
    cp_trades_matched.csv.gz
    orders_before_matching.csv
    orders_after_matching.csv
    last_execution_time.csv
    nbbo.csv.gz  /  session.csv.gz  /  reference.csv.gz
        ↓
    Stage 2  — Simulation
    Stage 3  — Per-security analysis
    Stage 4  — Cross-security reports
        ↓
data/outputs/{date}/{security}/       ←  Per-security reports (Step 3)
data/aggregated/                      ←  Portfolio-level reports (Step 3)
```

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Folder Structure](#2-folder-structure)
3. [Input File Reference](#3-input-file-reference)
4. [Environment Setup](#4-environment-setup)
5. [Step 1 — Place Raw Input Files](#5-step-1--place-raw-input-files)
6. [Step 2 — Partition Raw Data (Stage 1)](#6-step-2--partition-raw-data-stage-1)
7. [Step 3 — Run Full Pipeline](#7-step-3--run-full-pipeline)
8. [Understanding the Output](#8-understanding-the-output)
9. [Running Reports Standalone](#9-running-reports-standalone)
10. [Common Scenarios](#10-common-scenarios)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Prerequisites

- Python 3.10 or later
- pip

Install dependencies once:

```bash
pip install -r requirements.txt
```

Required packages: `pandas>=2.0`, `numpy>=1.24`, `scipy>=1.10`, `psutil>=5.9`

**macOS/Linux — activate the bundled virtual environment:**

```bash
source activate.sh
```

**Windows — activate manually:**

```powershell
swp_env\Scripts\activate
```

---

## 2. Folder Structure

### 2.1 Project layout

```
sweeporders/
├── src/                        # All source code
│   ├── main.py                 # Entry point
│   ├── config/
│   ├── pipeline/
│   ├── analysis/
│   ├── aggregation/
│   ├── discovery/
│   └── utils/
├── data/
│   ├── raw/                    # ← Place your input files here
│   │   ├── orders/
│   │   ├── trades/
│   │   ├── session/
│   │   ├── reference/
│   │   ├── participants/
│   │   └── nbbo/               # Optional
│   ├── processed/              # Auto-created by pipeline (Stage 1)
│   ├── outputs/                # Auto-created by pipeline (Stage 3)
│   └── aggregated/             # Auto-created by pipeline (Stage 4)
├── tests/
├── docs/
├── requirements.txt
└── activate.sh
```

### 2.2 Data flow summary

| Folder | Created by | Contents |
|---|---|---|
| `data/raw/` | You | Original flat CSV files from the exchange |
| `data/processed/` | Stage 1 | Partitioned, compressed files per date/security |
| `data/outputs/` | Stages 2–3 | Per-security simulation results and reports |
| `data/aggregated/` | Stage 4 | Cross-security merged reports |

See **Section 5** for how to name and place raw files, and **Section 6** for how Stage 1 builds the processed structure.

---

## 3. Input File Reference

### 3.1 Orders file (`{ticker}_{date}_orders.csv`)

Each row is an order lifecycle event (submission, amendment, cancellation, fill).

| Column | Type | Description |
|---|---|---|
| `order_id` | int64 | Unique order identifier |
| `exchange` | int | Exchange code (3 = ASX) |
| `timestamp` | int64 | Nanosecond epoch timestamp |
| `security_code` | int | Orderbookid (e.g. 110621 for DRR) |
| `price` | int | Order limit price (in price units) |
| `side` | int | 1 = Buy, 2 = Sell |
| `orderstatus` | int | 1 = Active, 2 = Incoming, 3 = Cancelled, etc. |
| `exchangeordertype` | int | 2048 = Centre Point Sweep; 64/256/4096/4098 = other CP types |
| `quantity` | int | Original order quantity |
| `leavesquantity` | int | Remaining unfilled quantity |
| `totalmatchedquantity` | int | Cumulative filled quantity |
| `national_bid` / `national_offer` | int64 | NBBO snapshot (−9223372036854775808 = sentinel / unavailable) |
| `bid` / `offer` | int64 | Order-level best bid/offer |
| `participantid` | int | Broker/participant identifier |
| `preferenceonly` | int | 1 = same-participant preference matching only |
| `midtick` | int | Midtick improvement flag (5/6 = Any Price Block) |
| `sequence` | int | Exchange sequence number (used for time priority) |
| `crossingkey` | int | Crossing key for preference matching |

### 3.2 Trades file (`{ticker}_{date}_trades.csv`)

Each match produces two rows — one for each side of the trade.

| Column | Type | Description |
|---|---|---|
| `orderid` | int64 | Order that generated this trade leg |
| `tradetime` | int64 | Nanosecond epoch timestamp of execution |
| `tradeprice` | int | Execution price |
| `quantity` | int | Quantity traded |
| `side` | int | 1 = Buy, 2 = Sell |
| `dealsource` | int | 1 = Continuous lit, 47 = Centre Point, 46 = Preference, 50/51 = APB |
| `matchgroupid` | int64 | Links the two legs of the same match |
| `passiveaggressive` | int | 1 = Aggressor (sweep), 0 = Passive (resting) |
| `securitycode` | int | Orderbookid |

### 3.3 Session file (`{date}_session.csv`)

Trading session state transitions for each security (PRE_OPEN, OPEN, CONTINUOUS, AUCTION, etc.). Used to filter orders to eligible matching sessions only.

### 3.4 Reference file (`{date}_ob.csv`)

Order book reference data — primarily used to look up tick size for each security.

### 3.5 Participants file (`{date}_par.csv`)

Maps participant IDs to names and participant types (Broker, Market Maker, etc.).

### 3.6 NBBO file (`{ticker}_{date}_nbbo.csv`) — optional

National Best Bid/Offer snapshots used for midpoint price calculation. When absent, the pipeline derives midpoint from `bid`/`offer` columns in the orders file.

---

## 4. Environment Setup


### macOS / Linux

```bash
# From the project root
source activate.sh

# Then run from src/
cd src
python main.py --help
```

### Windows

```powershell
# From the project root
swp_env\Scripts\activate

# Then run from src/
cd src
python main.py --help
```

### Running from any directory (without cd)

All paths are resolved relative to the project root, so you can also run:

```bash
# macOS/Linux
PYTHONPATH=/path/to/sweeporders/src python /path/to/sweeporders/src/main.py --ticker drr --date 20240905

# Windows PowerShell
$env:PYTHONPATH="C:\path\to\sweeporders\src"
python C:\path\to\sweeporders\src\main.py --ticker drr --date 20240905
```

---

## 5. Step 1 — Place Raw Input Files

Before running anything, drop your raw CSV files into the correct subfolders under `data/raw/`. The pipeline discovers them automatically by filename — no configuration changes needed.

### Required files

| Folder | File naming pattern | Example |
|---|---|---|
| `data/raw/orders/` | `{ticker}_{YYYYMMDD}_orders.csv` | `drr_20240905_orders.csv` |
| `data/raw/trades/` | `{ticker}_{YYYYMMDD}_trades.csv` | `drr_20240905_trades.csv` |
| `data/raw/session/` | `{YYYYMMDD}_session.csv` | `20240905_session.csv` |
| `data/raw/reference/` | `{YYYYMMDD}_ob.csv` | `20240905_ob.csv` |
| `data/raw/participants/` | `{YYYYMMDD}_par.csv` | `20240905_par.csv` |

### Optional

| Folder | File naming pattern | Notes |
|---|---|---|
| `data/raw/nbbo/` | `{ticker}_{YYYYMMDD}_nbbo.csv` | When absent, midpoint falls back to bid/offer from orders file |

> **Session, reference, and participants are date-keyed — one file covers all securities for that date.** If an exact-date match is missing, the pipeline falls back to the nearest available date and logs a warning.

### Verify discovery before running

```bash
cd src

# macOS/Linux
python main.py --list-dates
python main.py --list-securities --date 20240905

# Windows
python main.py --list-dates
python main.py --list-securities --date 20240905
```

---

## 6. Step 2 — Partition Raw Data (Stage 1)

Stage 1 reads the flat raw CSVs, filters to Centre Point orders, and writes structured compressed partitions into `data/processed/{date}/{security}/`. **This must be run before simulation or reporting.**

```bash
cd src

# Partition a single security
python main.py --ticker drr --date 20240905 --stage 1

# Partition all securities for a date at once
python main.py --auto-discover --date 20240905 --stage 1
```

### What Stage 1 does

| Step | Action | Output location |
|---|---|---|
| 1 | Filter Centre Point orders (all types) from raw orders CSV | `data/processed/{date}/{security}/cp_orders_filtered.csv.gz` |
| 2 | Match trades to extracted order IDs | `data/processed/{date}/{security}/cp_trades_matched.csv.gz` |
| 3 | Aggregate trades per order | `data/processed/{date}/{security}/cp_trades_aggregated.csv.gz` |
| 4 | Load and partition session, reference, participants, NBBO | `data/processed/{date}/session.csv.gz` etc. |
| 5 | Extract order states before/after each sweep's execution window | `data/processed/{date}/{security}/orders_before_matching.csv` |
| 6 | Calculate execution time windows for qualifying sweep orders | `data/processed/{date}/{security}/last_execution_time.csv` |

### Resulting folder structure after Stage 1

```
data/processed/
└── 2024-09-05/
    ├── session.csv.gz              ← shared across all securities for this date
    ├── reference.csv.gz
    ├── participants.csv.gz
    └── 110621/                     ← one folder per security (orderbookid)
        ├── cp_orders_filtered.csv.gz
        ├── cp_trades_matched.csv.gz
        ├── cp_trades_aggregated.csv.gz
        ├── orders_before_matching.csv
        ├── orders_after_matching.csv
        ├── last_execution_time.csv
        └── nbbo.csv.gz
```

---

## 7. Step 3 — Run Full Pipeline

Once Stage 1 has partitioned the data, run the remaining stages to simulate, analyse, and report.

```bash
cd src

# Run all remaining stages (2, 3, 4) on already-partitioned data
python main.py --ticker drr --date 20240905 --stage 2 --stage 3 --stage 4

# Or run everything end-to-end in one command (Stages 1–4)
python main.py --ticker drr --date 20240905

# All securities for a date, with parallel processing
python main.py --auto-discover --date 20240905 --parallel

# With statistical tests (t-tests, p-values, confidence intervals)
python main.py --ticker drr --date 20240905 --enable-stats
```

### Pipeline stages

| Stage | What it does |
|---|---|
| 1 | Extract and partition raw data → `data/processed/` |
| 2 | Simulate dark pool matching, calculate real and simulated metrics |
| 3 | Per-security analysis: sweep execution, unmatched orders, volume quartiles |
| 4 | Cross-security aggregation and portfolio-level reports |

### Configuration defaults (`src/config/config.py`)

Override these by editing the file — no CLI flag needed:

| Setting | Default | Description |
|---|---|---|
| `TICKER` | `'drr'` | Default ticker when none specified |
| `DATE` | `'20240905'` | Default date |
| `NBBO_SOURCE` | `'INTERNAL'` | `'INTERNAL'` (orders file) or `'EXTERNAL'` (nbbo.csv) |
| `MIN_ORDERS_THRESHOLD` | `100` | Minimum orders for auto-discovery |
| `MIN_TRADES_THRESHOLD` | `10` | Minimum trades for auto-discovery |
| `ENABLE_PARALLEL_PROCESSING` | `False` | Multi-core processing |
| `VOLUME_BUCKET_METHOD` | `'quartile'` | `'quartile'`, `'quintile'`, or `'custom'` |

---

## 8. Understanding the Output

### 6.1 Output folder structure

After a full run for `drr` on `20240905`, the output tree looks like:

```
data/
├── processed/
│   └── 2024-09-05/
│       └── 110621/
│           ├── orders_before_matching.csv   # Resting orders at time of each sweep
│           ├── orders_after_matching.csv    # Sweep orders with fill data
│           ├── last_execution_time.csv      # Execution windows per sweep order
│           ├── nbbo.csv.gz
│           ├── session.csv.gz
│           ├── reference.csv.gz
│           └── participants.csv.gz
│
├── outputs/
│   └── 2024-09-05/
│       └── 110621/
│           ├── real_trade_metrics.csv           # Actual execution metrics
│           ├── simulated_trade_metrics.csv      # Simulated dark pool metrics
│           ├── simulation_order_summary.csv     # Per-sweep fill summary
│           ├── trade_level_comparison.csv       # Real vs simulated per order
│           ├── matched/                         # Orders matched in both real & sim
│           │   ├── sweep_order_comparison_detailed.csv
│           │   ├── sweep_order_comparison_summary.csv
│           │   ├── sweep_order_statistical_tests.csv
│           │   └── sweep_order_quantile_comparison.csv
│           ├── unmatched/                       # Orders only matched in real
│           │   └── sweep_order_unexecuted_in_dark.csv
│           ├── unmatched_analysis/
│           │   ├── unmatched_liquidity_analysis.csv
│           │   └── unmatched_root_causes.csv
│           └── volume_analysis/
│               ├── volume_bucket_summary.csv
│               └── volume_bucket_statistical_tests.csv
│
└── aggregated/                                  # Cross-security (Stage 4)
    ├── aggregated_sweep_comparison.csv
    ├── aggregated_statistical_summary.csv
    ├── aggregated_per_security_tests.csv
    ├── aggregated_cross_security_anova.csv
    ├── aggregated_cross_security_pairwise.csv
    ├── aggregated_cross_orderbookid_tests.csv
    ├── aggregated_analysis_report.txt
    ├── aggregated_volume_summary.csv
    └── aggregated_volume_report.txt
```

### 6.2 Key report files explained

#### `matched/sweep_order_comparison_detailed.csv`
One row per sweep order that was matched in **both** the real lit market and the simulated dark pool. Core comparison table.

| Column group | Columns | Description |
|---|---|---|
| Identity | `orderid`, `date`, `orderbookid`, `side` | Order identifiers |
| Real execution | `real_fill_rate`, `real_exec_cost_bps`, `real_vwap`, `real_num_fills` | Actual ASX execution quality |
| Simulated | `sim_fill_rate`, `sim_exec_cost_bps`, `sim_vwap`, `sim_num_fills` | Dark pool simulation results |
| Difference | `exec_cost_diff_bps`, `fill_rate_diff`, `vwap_diff` | Real minus simulated |

#### `matched/sweep_order_comparison_summary.csv`
Aggregated mean/median/std for each metric across all matched orders. The primary single-page summary.

#### `unmatched/sweep_order_unexecuted_in_dark.csv`
Orders that filled in the real market but would **not** have matched in the simulated dark pool. Includes reason codes and liquidity context.

#### `unmatched_analysis/unmatched_root_causes.csv`
Root cause classification for each unmatched order:

| Root cause | Meaning |
|---|---|
| `INSTANT_LIT_EXECUTION` | Order was filled immediately on lit market before dark pool could match |
| `SIMULATION_MISS` | Dark pool simulation found no eligible contra order |

#### `volume_analysis/volume_bucket_summary.csv`
Execution metrics broken down by order size quartile (Q1=smallest to Q4=largest). Shows whether execution quality differs across order sizes.

#### `aggregated_sweep_comparison.csv`
All securities and dates merged into one file. The primary input for cross-security analysis.

#### `aggregated_analysis_report.txt`
Human-readable text report covering portfolio-level findings: overall real vs simulated execution cost, statistical significance, cross-security ANOVA results.

### 6.3 Interpreting the key metric — Execution Cost (bps)

The central question the pipeline answers:

> **Would routing these orders through a Centre Point dark pool have produced better execution than the actual lit market?**

- **Negative execution cost** = favourable (buy below midpoint, or sell above midpoint)
- **`exec_cost_diff_bps` > 0** → dark pool simulation achieves lower cost than real execution
- **`exec_cost_diff_bps` < 0** → real execution was better

Example output from the pipeline summary:

```
Real execution cost:       -13.98 bps
Simulated (dark pool):     -81.62 bps
Difference:                +67.64 bps  → dark pool provides better execution
```

---

## 9. Running Reports Standalone

Stage 4 aggregation scripts can also be run independently — useful if you want to re-generate reports without re-running the full simulation.

```bash
# From project root (macOS/Linux)
PYTHONPATH=src python src/aggregation/aggregate_sweep_results.py
PYTHONPATH=src python src/aggregation/analyze_aggregated_results.py
PYTHONPATH=src python src/aggregation/aggregate_volume_analysis.py

# Windows PowerShell
$env:PYTHONPATH="src"
python src\aggregation\aggregate_sweep_results.py
python src\aggregation\analyze_aggregated_results.py
python src\aggregation\aggregate_volume_analysis.py
```

Each script reads from `data/outputs/` and writes to `data/aggregated/`. No arguments needed — paths are resolved automatically from the project root.

---

## 10. Common Scenarios

### Process a single security

```bash
python main.py --ticker bhp --date 20240905
```

### Process all available securities for a date

```bash
python main.py --auto-discover --date 20240905 --parallel
```

### Re-run analysis only (skip simulation)

Useful when you want to tweak reporting without re-running the simulation:

```bash
python main.py --ticker drr --date 20240905 --stage 3 --stage 4
```

### Run with statistical tests enabled

Adds t-tests, p-values, and confidence intervals to all reports:

```bash
python main.py --ticker drr --date 20240905 --enable-stats
```

### Add a new date's data

1. Drop the new files into `data/raw/orders/` and `data/raw/trades/` using the naming convention
2. Add the session/reference/participants files for the new date if available
3. Run:

```bash
python main.py --auto-discover --date 20241015
```

---

## 11. Troubleshooting

### `Ticker X not found for date YYYYMMDD`

Check that:
- The orders file exists: `data/raw/orders/{ticker}_{date}_orders.csv`
- The trades file exists: `data/raw/trades/{ticker}_{date}_trades.csv`
- Both filenames follow the exact pattern `{ticker}_{YYYYMMDD}_{orders|trades}.csv`

Use `--list-securities --date YYYYMMDD` to see what the pipeline actually discovers.

### `No valid securities found for date YYYYMMDD`

The pipeline discovered files but the security didn't meet the minimum thresholds (default: 100 orders, 10 trades). Either:
- Lower the thresholds: `python main.py --auto-discover --date YYYYMMDD --min-orders 10 --min-trades 1`
- Use `--ticker` or `--orderbookid` directly — these bypass the threshold check

### `No sweep orders, skipping` for a partition

The orders file contains no Centre Point Sweep orders (type `2048`) that qualified for simulation on that date/security. This is expected for some partitions.

### Session/reference data missing for a date

The pipeline will attempt a fallback to the nearest available date's session/reference file and log a warning. Results may be slightly less accurate but the pipeline will complete.

### Statistical tests showing `p = nan`

Statistical tests are disabled by default. Run with `--enable-stats` to get p-values. With `--enable-stats`, `nan` means the sample was too small for the test (fewer than 2 observations).
