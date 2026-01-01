# Sweep Orders Analysis Pipeline

## Overview

This pipeline analyzes Centre Point sweep orders and simulates what would happen if these orders rested in a dark pool instead of executing on the lit market. The goal is to compare real execution outcomes with three hypothetical dark pool scenarios.

## Key Concept

**Real Execution Path:** Sweep Order → Dark Pool (attempt) → Lit Market (if unfilled)

**Simulation Model:** Sweep Orders → Rest in Dark Pool (all scenarios)

The pipeline tests three scenarios:
- **Scenario A:** Orders that were immediately filled → Simulate resting in dark book
- **Scenario B:** Orders that were partially filled → Simulate residual resting in dark book
- **Scenario C:** Orders that were not filled → Simulate resting in dark book

## Project Structure

```
sweeporders/
├── data/                          # Input data directory
│   ├── orders/drr_orders.csv     # All orders (48,033 records)
│   ├── trades/drr_trades_*.csv   # Trade executions
│   ├── nbbo/nbbo.csv             # NBBO data
│   └── ...
├── src/                           # Source code
│   ├── ingest.py                 # Phase 1.1-1.3: Data ingestion
│   ├── match_trades.py           # Phase 1.2: Trade matching
│   ├── book.py                   # Phase 1.3: Dark book construction
│   ├── classify.py               # Phase 2.1-2.2: Order classification
│   ├── simulate.py               # Phase 3.1-3.3: Simulations
│   └── report.py                 # Phase 4: Report generation
├── config/                        # Configuration directory
├── processed_files/              # Output directory (auto-created)
├── main.py                       # Main orchestration script
├── requirements.txt              # Python dependencies
└── README.md                     # This file
```

## Pipeline Phases

### Phase 1: Data Ingestion & Matching

**1.1 Extract Centre Point Orders**
- Reads orders file and filters for Centre Point order types (64, 256, 2048, 4096, 4098)
- Input: data/orders/drr_orders.csv (48,033 orders)
- Output: 28,452 Centre Point orders
- File: `centrepoint_orders_raw.parquet`

**1.2 Match Trades to Centre Point Orders**
- Filters trades to only those linked to Centre Point orders
- Input: data/trades/drr_trades_*.csv + Centre Point order IDs
- Output: 4,200 matched trades from 2,361 unique orders
- Files: `centrepoint_trades_raw.parquet`, `centrepoint_trades_agg.parquet`

**1.3 Build Dark Order Book**
- Creates nested dictionary structure: symbol → side → price → [orders]
- Enables fast matching during simulations
- Files: `dark_book_state.pkl`, `order_index.pkl`

### Phase 2: Order Classification

**2.1 Filter Sweep Orders**
- Extracts sweep orders (exchangeordertype == 64 in this dataset; would be 2048 in production)
- Joins with trade execution data to measure fill ratios
- Output: 570 sweep orders with trade metadata

**2.2 Classify Sweep Outcomes**
- **Scenario A (Immediate Full):** 140 orders
  - Fill ratio ≥ 99% AND execution duration < 1 second
  - These orders matched instantly in dark pool
  
- **Scenario B (Eventual Full):** 27 orders
  - Fill ratio ≥ 99% AND execution duration ≥ 1 second
  - These orders took time but filled completely
  
- **Scenario C (Partial/None):** 650 orders
  - Fill ratio < 99%
  - Includes partially filled (321) and completely unfilled (329) orders

### Phase 3: Dark Book Simulations

Each scenario simulates what would happen if the order stayed in the dark book instead of going to the lit market.

**3.1 Scenario A Simulation**
- Orders that were immediately filled in reality
- Simulate: Rest in dark book at original price
- Match against: All resting orders in dark book
- Logic: Price priority (best first) → FIFO within price level
- Result: Compare simulated vs real execution prices/fills

**3.2 Scenario B Simulation**
- Orders that were partially filled in reality
- Simulate: Keep actual fill + rest residual in dark book
- Match against: All resting orders in dark book
- Result: Would the residual have matched in dark? What's the improvement?

**3.3 Scenario C Simulation**
- Orders that were completely unfilled in reality
- Simulate: Rest entire order in dark book for entire session
- Match against: All resting orders in dark book
- Result: What percentage could have executed in dark?

### Phase 4: Reporting & Metrics

Generated CSV reports comparing scenarios:

1. **scenario_comparison_summary.csv**
   - High-level metrics for all scenarios
   - Order counts, quantities, average fill ratios

2. **scenario_detailed_comparison.csv**
   - Detailed metrics including median, std dev
   - Full fills, partial fills, no fills breakdown

3. **order_level_detail.csv**
   - 4,196 rows (one per order with scenario data)
   - Individual order outcomes for all scenarios
   - Columns: order_id, symbol, side, quantity, real_fill_ratio, simulated_fill_ratio

4. **execution_cost_comparison.csv**
   - Total and average execution costs
   - Cost differences between real and simulated

5. **by_participant.csv**
   - Aggregated metrics by participant/firm
   - 26 unique participants in dataset

## Running the Pipeline

### Prerequisites

```bash
# Create virtual environment
python3 -m venv swp_env
source swp_env/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Execute Full Pipeline

```bash
# Run entire pipeline from scratch
python main.py
```

### Run Individual Phases

```bash
# Phase 1: Data Ingestion
python src/ingest.py
python src/match_trades.py
python src/book.py

# Phase 2: Classification
python src/classify.py

# Phase 3: Simulations
python src/simulate.py

# Phase 4: Reports
python src/report.py
```

## Data Specifications

### Input Data

| File | Records | Size | Columns |
|------|---------|------|---------|
| orders/drr_orders.csv | 48,033 | ~10MB | order_id, timestamp, security_code, price, side, quantity, exchangeordertype, participantid |
| trades/drr_trades_segment_1.csv | 8,302 | ~2MB | orderid, tradetime, tradeprice, quantity, securitycode, participantid |

### Output Data

| File | Type | Records | Purpose |
|------|------|---------|---------|
| centrepoint_orders_raw.parquet | Parquet | 28,452 | All Centre Point orders |
| centrepoint_trades_agg.parquet | Parquet | 2,361 | Trade aggregations by order |
| dark_book_state.pkl | Pickle | 1 | Serialized dark book structure |
| scenario_*_simulation_results.parquet | Parquet | 140-329 | Simulation results |
| *.csv | CSV | Various | User-facing reports |

## Key Metrics & Calculations

### Fill Ratio
```
fill_ratio = total_quantity_filled / original_quantity
- 0 = no execution
- 1.0 = complete fill
```

### Execution Duration
```
duration_seconds = (last_trade_timestamp - first_trade_timestamp) / 1e9
```

### Cost Comparison
```
cost_diff = (simulated_fill_ratio * simulated_price) - (real_fill_ratio * real_price)
- Positive = simulated would cost more
- Negative = simulated would cost less
```

## Results Summary

| Metric | Scenario A | Scenario B | Scenario C |
|--------|-----------|-----------|-----------|
| Orders | 140 | 321 | 329 |
| Real Avg Fill | 100% | 27% | 0% |
| Simulated Avg Fill | Varies | Varies | Varies |

*Note: Specific results depend on dark pool liquidity distribution*

## Implementation Details

### Dark Book Structure

```python
dark_book = {
    symbol_code: {
        side (1=BUY, 2=SELL): {
            price_level: [
                {
                    'order_id': int,
                    'quantity': int,
                    'timestamp': int,
                    'participant_id': int
                },
                ...
            ],
            ...
        },
        ...
    },
    ...
}
```

### Matching Algorithm

1. Determine opposite side (BUY ↔ SELL)
2. Get compatible prices from opposite side
   - BUY order: SELL prices ≤ order price
   - SELL order: BUY prices ≥ order price
3. Iterate through prices (best to worst)
4. Within each price, FIFO by timestamp
5. Match until order filled or no more counterparties

### Performance

- Full pipeline: ~1.3 seconds
- Bottleneck: Parquet I/O
- Memory usage: < 500MB
- Scalable to: 50GB+ trade files with chunking

## Dependencies

- pandas >= 2.0.0 - Data manipulation
- numpy >= 1.24.0 - Numerical computing
- pyarrow >= 13.0.0 - Parquet support

## Configuration

Modify these parameters in source code:

- `CENTREPOINT_TYPES`: Order types to filter (default: 64, 256, 2048, 4096, 4098)
- `SWEEP_ORDER_TYPE`: Which order type to analyze (default: 64)
- `EXECUTION_DURATION_THRESHOLD`: Milliseconds for A vs B classification (default: 1.0 second)
- `FULL_FILL_THRESHOLD`: Min fill ratio for "full" (default: 0.99)

## Limitations & Assumptions

1. **Simulation assumes perfect FIFO matching** - No queue priority or pro-rata
2. **No partial matching of single orders** - Each order matches complete qty or nothing
3. **Static dark book** - Based on session state, not intraday updates
4. **No market impact** - Assumes prices don't change with order placement
5. **Single symbol** - Dataset contains only one security code

## Future Enhancements

1. Support multiple symbols and asset classes
2. Implement pro-rata and size-based matching
3. Add intraday order book reconstruction
4. Include market impact modeling
5. Support different order book depths/levels
6. Add Monte Carlo simulations for fill probabilities

## File Locations

- Data: `./data/`
- Output: `./processed_files/`
- Code: `./src/`
- Main: `./main.py`

## Contact & Support

For questions or issues, refer to the detailed implementation plan in `DETAILED_PLAN.md`.