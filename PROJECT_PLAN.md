# Sweep Orders Analysis - Project Plan

## Overview
Build an end-to-end pipeline to analyze Centre Point sweep orders, simulate alternative execution scenarios, and generate comparative performance metrics.

---

## Phase 1: Data Ingestion & Filtering

### 1.1 Read & Extract Centre Point Orders
**Goal:** Load large orders file, filter for Centre Point order types, enable parallel processing

**Input:**
- Large orders file (exact name/location needed)

**Processing:**
- Read file in chunks to manage memory
- Filter for order types: 64, 256, 2048, 4096, 4098
- Extract and store Centre Point order IDs

**Output:**
- `processed_files/centrepoint_orders.pkl` or `.parquet` (in-memory object)
- Metadata: total orders, order type breakdown

**Dependencies:**
- pandas, numpy
- Optional: polars (for larger datasets)

**Estimated Size:** TBD (depends on input size)

---

### 1.2 Read & Match Trades
**Goal:** Filter 50GB trades file for Centre Point orders

**Input:**
- `trades.csv` (≈50GB)
- Centre Point order IDs from Step 1.1

**Processing:**
- Stream trades.csv in chunks
- Match trade IDs to Centre Point order IDs
- Retain only matching trades

**Output:**
- `processed_files/centrepoint_trades.processed` (≈1–3GB)

**Estimated Duration:** 10-30 minutes (depending on hardware)

---

### 1.3 Build NBBO / Midpoint Input
**Goal:** Prepare bid/ask data for cost calculations

**Input:**
- `externalprice.csv` or price data from orders.csv
- Timestamp alignment requirements

**Processing:**
- Load bid/ask at each timestamp
- Remove duplicates
- Calculate midpoints
- Deduplicate across time

**Output:**
- `processed_files/nbbo.processed` (≈500MB–2GB)

**Note:** Need to clarify exact source (externalprice.csv vs orders.csv)

---

## Phase 2: Order Classification & Outcome Determination

### 2.1 Filter Sweep Orders Only
**Goal:** Isolate sweep orders (exchangeordertype == 2048)

**Input:**
- Centre Point orders from Step 1.1

**Processing:**
- Filter where `exchangeordertype == 2048`
- Retain all order metadata (arrival time, quantity, participant, etc.)

**Output:**
- `processed_files/sweep_orders.processed` (≈200MB–1GB)
- Metadata: total sweep orders, quantity by participant/venue

---

### 2.2 Classify Sweep Order Outcomes
**Goal:** Categorize orders by fill status and timing

**Input:**
- Sweep orders from Step 2.1
- Matched trades from Step 1.2

**Processing:**
- Match orders to their executed trades
- Calculate fill time and fill percentage
- Classify into 3 categories:
  - **Immediate Full:** 100% filled in < 1 second
  - **Eventual Full:** 100% filled in ≥ 1 second
  - **Partial:** Never reached 100%

**Output:**
- `processed_files/immediate_full_orders.pkl`
- `processed_files/eventual_full_orders.pkl`
- `processed_files/partial_orders.pkl`
- Summary stats for each category

---

## Phase 3: Market Reconstruction

### 3.1 Rebuild Lit Order Book
**Goal:** Reconstruct market microstructure over time

**Input:**
- All trades from Step 1.2

**Processing:**
- Build in-memory order book snapshots
- Track bid/ask price and quantity at each timestamp
- Update book with each trade execution
- Store snapshots at configurable intervals (e.g., every 100ms or per-trade)

**Output:**
- In-memory data structure (dict of timestamps → book state)
- Optional: Save to `processed_files/order_book_snapshots.pkl` for checkpoint

**Complexity Note:** Requires careful timestamp handling and memory management for ≈50GB trades

---

## Phase 4: Simulation Engine

### 4.1 Per-Partition Simulations
**Goal:** Test three execution scenarios for each order

**Input:**
- Orders (all categories)
- Trades (matched real execution)
- NBBO data
- Reconstructed order book

**Simulation Scenarios:**

#### Scenario A: Fully Executed Sweep Orders
- Identify execution partition (where order was actually filled)
- Identify completion time
- **Simulation:** Rest order in dark book at midpoint from arrival to actual completion time
- Match against other orders arriving in that window
- Measure: Fill time, fill ratio, execution cost vs actual

#### Scenario B: Partially Executed Orders
- Identify actual fill amount
- Identify residual quantity
- **Simulation:** Keep actual executed quantity + residual in dark book at midpoint until EOD
- Test whether residual would match at midpoint during trading hours
- Measure: Potential full fill vs actual partial fill

#### Scenario C: Unexecuted Sweep Orders
- Identify orders with no execution in sweep phase
- **Simulation:** Rest in dark book at midpoint until EOD
- Test for potential midpoint matches during trading hours
- Measure: Potential fill vs actual no-fill

**Output:**
- Simulation results (dict or DataFrame):
  - Order ID
  - Scenario (A/B/C)
  - Simulated fill time
  - Simulated fill ratio
  - Simulated execution cost
  - Comparison to actual

---

## Phase 5: Reporting & Analysis

### 5.1 Generate Reports
**Goal:** Create comprehensive comparison metrics

**Input:**
- Simulation results from Phase 4
- Real trade data
- Aggregated metrics by scenario

**Processing:**
- Calculate per-scenario metrics:
  - Number of full fills
  - Number of partial fills
  - Total quantity traded
  - Average execution cost
  - Fill ratio statistics
  - Cost comparison vs actual

- Aggregate by:
  - Order type
  - Participant
  - Venue
  - Time period

**Output Files (5+ CSV reports):**

1. **scenario_comparison.csv**
   - Columns: Scenario, Full_Fills, Partial_Fills, Avg_Cost, Fill_Ratio, etc.
   - Rows: One per scenario (A, B, C) + actual

2. **order_level_detail.csv**
   - Columns: OrderID, OrderType, ArrivalTime, Quantity, RealFillRatio, ScenarioA_FillRatio, ScenarioB_FillRatio, ScenarioC_FillRatio, CostDiff_A, CostDiff_B, CostDiff_C
   - Rows: One per order

3. **partition_detail_scenario_a.csv**
   - Partition-specific metrics for Scenario A

4. **partition_detail_scenario_b.csv**
   - Partition-specific metrics for Scenario B

5. **partition_detail_scenario_c.csv**
   - Partition-specific metrics for Scenario C

6. **aggregated_metrics.csv** (optional)
   - By participant, venue, time period, etc.

---

## Technology Stack

**Core Libraries:**
- `pandas` / `polars` - Data manipulation
- `numpy` - Numerical computing
- `pyarrow` / `parquet` - Efficient storage
- `dask` (optional) - Parallel processing for large files

**Optional:**
- `multiprocessing` or `concurrent.futures` - Parallel order processing
- `pickle` - State serialization for checkpoints

---

## File Structure

```
sweeporders/
├── src/
│   ├── __init__.py
│   ├── ingest.py          # Phase 1: Data ingestion
│   ├── classify.py        # Phase 2: Order classification
│   ├── book.py            # Phase 3: Order book reconstruction
│   ├── simulate.py        # Phase 4: Simulation engine
│   ├── report.py          # Phase 5: Report generation
│   └── utils.py           # Shared utilities
├── config/
│   ├── settings.yaml      # Configuration (file paths, parameters)
│   └── logging.yaml       # Logging configuration
├── data/
│   ├── (input files)
│   └── processed_files/   # Output directory
├── notebooks/             # Jupyter notebooks for exploration
├── tests/                 # Unit tests
├── requirements.txt       # Dependencies
└── main.py               # Orchestration script
```

---

## Execution Flow

```
main.py
  │
  ├─→ ingest.py
  │     ├─→ extract_centrepoint_orders()        [1.1]
  │     ├─→ match_trades()                       [1.2]
  │     └─→ build_nbbo()                         [1.3]
  │
  ├─→ classify.py
  │     ├─→ filter_sweep_orders()               [2.1]
  │     └─→ classify_outcomes()                  [2.2]
  │
  ├─→ book.py
  │     └─→ rebuild_order_book()                [3.1]
  │
  ├─→ simulate.py
  │     └─→ run_simulations()                   [4.1]
  │           ├─→ scenario_a()
  │           ├─→ scenario_b()
  │           └─→ scenario_c()
  │
  └─→ report.py
        └─→ generate_reports()                  [5.1]
```

---

## Open Questions / Clarifications Needed

1. **Data Source Clarifications:**
   - Exact file path for "large orders file" in 1.1?
   - Is `externalprice.csv` available, or extract prices from orders?
   - Date range to process? (full year, specific period?)

2. **Performance Constraints:**
   - Available RAM? (Will influence chunking strategy)
   - Target runtime? (This will guide parallelization strategy)
   - GPU available? (Could accelerate matching operations)

3. **Order Book Granularity:**
   - Update frequency: per-trade or fixed intervals?
   - Store full snapshots or deltas?
   - How far back to keep in memory?

4. **Simulation Details:**
   - Midpoint calculation: simple (bid+ask)/2 or volume-weighted?
   - Dark pool matching rules: strict timestamp order or quantity priority?
   - Partial fill priority: FIFO or other?

5. **Reporting:**
   - Any specific audience (trading desk, compliance, quant)?
   - Charts/visualizations needed beyond CSV?
   - Time breakdowns (hourly, by symbol, by participant)?

---

## Estimated Effort

| Phase | Task | Effort | Notes |
|-------|------|--------|-------|
| 1 | Data Ingestion | 3-4 days | Depends on data complexity & format |
| 2 | Classification | 2-3 days | Straightforward filtering/matching |
| 3 | Order Book | 3-5 days | Memory management critical for 50GB |
| 4 | Simulation | 5-7 days | Core logic; most complex phase |
| 5 | Reporting | 2-3 days | Report generation & validation |
| — | Testing & QA | 2-3 days | Throughout all phases |
| **Total** | | **17-25 days** | With 1-2 engineers |

---

## Risk Factors

- **Memory constraints:** 50GB trades file requires careful streaming
- **Data quality:** Need to handle missing/malformed data in large files
- **Timestamp alignment:** Ensure consistent time zones & precision across files
- **Order matching:** Need robust logic to handle partial fills, order updates, cancellations
- **Simulation validation:** Results must be validated against real data

---

## Success Criteria

- ✅ All 5 input phases complete without data loss
- ✅ 3 order outcome categories correctly classified
- ✅ Order book reconstruction validates against known market events
- ✅ 3 scenarios run successfully for all orders
- ✅ Reports generated and validated
- ✅ Total output ≤ 10GB (compressed as needed)
- ✅ End-to-end runtime < 2 hours (target)
