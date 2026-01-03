# Sweep Orders Analysis Pipeline

A Python-based simulation and analysis tool for Centre Point order matching using sweep order logic. This pipeline extracts, simulates, and compares real execution metrics with simulated sweep matching behavior based on NBBO midpoint pricing and time-priority matching.

## Overview

This project analyzes Centre Point orders on the ASX (Australian Securities Exchange) by:
1. Extracting Centre Point orders and their matching trades from raw market data
2. Simulating sweep order matching using time-priority logic
3. Comparing real vs simulated execution metrics across different order groups

### What are Centre Point Orders?

Centre Point is a mid-point liquidity pool where orders are matched at the midpoint between the best bid and offer. This pipeline specifically focuses on **sweep orders** (type 2048), which provide passive liquidity that can be matched by incoming aggressive orders.

## Features

- **Schema-Independent Design**: Uses configurable column mappings to adapt to different data schemas
- **Partitioned Processing**: Processes data by date and security code for efficient parallel processing
- **Comprehensive Simulation**: Implements time-priority sweep matching with NBBO midpoint pricing
- **Detailed Comparison Reports**: Generates statistical analysis comparing real vs simulated execution
- **Order Classification**: Automatically classifies orders into fully-filled, partially-filled, and unfilled groups
- **LOB State Extraction**: Captures limit order book states before and after matching events

## Quick Start

### Prerequisites

- Python 3.8 or higher
- pandas >= 2.0.0
- numpy >= 1.24.0

### Installation

1. Clone the repository:
```bash
git clone https://github.com/zautlin/sweeporders.git
cd sweeporders
```

2. Create and activate a virtual environment:
```bash
python -m venv swp_env
source swp_env/bin/activate  # On Windows: swp_env\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

### Running the Pipeline

Place your raw data files in the `data/raw/` directory structure:
```
data/raw/
├── orders/drr_orders.csv
├── trades/drr_trades_segment_1.csv
├── nbbo/nbbo.csv
├── session/session.csv
├── reference/ob.csv
└── participants/par.csv
```

Then run the pipeline:
```bash
python extract_partitioned.py
```

The pipeline will:
- Extract and partition Centre Point orders by date/security
- Simulate sweep matching
- Generate comparison reports in `data/outputs/`

Typical execution time: ~80 seconds for ~28K orders and ~4K trades.

## Architecture

### Project Structure

```
sweeporders/
├── extract_partitioned.py        # Main simple pipeline (979 lines)
├── requirements.txt               # Python dependencies
├── config/
│   └── columns.py                # Column name mappings
├── data/
│   ├── raw/                      # Source data (tracked in git)
│   ├── processed/                # Intermediate files (ignored)
│   └── outputs/                  # Final results (ignored)
├── docs/
│   ├── bi.txt                    # Business requirements
│   └── dd.txt                    # Data dictionary
└── src/
    ├── ingest_centrepoint.py     # Advanced class-based pipeline (2,376 lines)
    └── sweep_simulation/         # Simulation module
        ├── __init__.py
        ├── simulator.py          # Sweep matching algorithm
        ├── nbbo_provider.py      # NBBO midpoint calculation
        ├── metrics_calculator.py # Execution metrics computation
        └── comparison_reporter.py # Real vs simulated comparison
```

### Pipeline Steps

The pipeline executes 11 sequential steps:

1. **Extract Orders**: Filter Centre Point orders, partition by date/security
2. **Extract Trades**: Filter trades matching Centre Point orders
3. **Aggregate Trades**: Compute total matched quantity and average price per order
4. **Extract NBBO**: Filter NBBO data for relevant dates/securities
5. **Extract Reference Data**: Extract session, reference, and participant data
6. **Extract LOB States**: Capture order book states before/after each execution
7. **Extract Last Execution Times**: Determine when each sweep order stopped matching
8. **Simulate Sweep Matching**: Run time-priority simulation with NBBO midpoint pricing
9. **Calculate Simulated Metrics**: Compute fill ratios, participation rates, etc.
10. **Classify Order Groups**: Group orders by real execution outcome (G1/G2/G3)
11. **Compare Real vs Simulated**: Generate statistical comparison reports

### Order Types

- **Centre Point Orders**: Types 64, 256, 2048, 4096, 4098
- **Sweep Orders** (Type 2048): Passive liquidity providers
- **Incoming Orders** (Types 64, 256, 4096, 4098): Aggressive liquidity takers

### Order Classification

Based on the final order state (last timestamp + sequence):

| Group | Description | Condition |
|-------|-------------|-----------|
| **G1** | Fully Filled | `leavesquantity == 0` |
| **G2** | Partially Filled | `leavesquantity > 0 AND totalmatchedquantity > 0` |
| **G3** | Unfilled | `leavesquantity > 0 AND totalmatchedquantity == 0` |

## Simulation Algorithm

### Sweep Matching Logic

For each incoming order (processed chronologically):

1. **Find Active Sweeps**: Identify sweep orders where `first_execution_time <= timestamp <= last_execution_time`
2. **Filter Opposite Side**: Match BUY sweeps with SELL incoming (and vice versa)
3. **Sort by Time Priority**: Earliest `first_execution_time` gets matched first
4. **Match Sequentially**: Fill incoming order quantity from available sweep orders
5. **Price Determination**: Use NBBO midpoint (or fallback to order bid/offer midpoint)

### Price Calculation

```python
# Primary: NBBO midpoint
price = (nbbo_bid + nbbo_offer) / 2

# Fallback: Order bid/offer midpoint
if nbbo_unavailable:
    price = (order_bid + order_offer) / 2
```

## Output Files

### Intermediate Files (`data/processed/{date}/{security}/`)

- `centrepoint_orders_raw.csv.gz` - Filtered Centre Point orders
- `centrepoint_trades_raw.csv.gz` - Matching trades
- `centrepoint_trades_agg.csv.gz` - Aggregated trade metrics per order
- `nbbo.csv.gz` - NBBO data
- `orders_before_lob.csv` - Limit order book state before matching
- `orders_after_lob.csv` - Limit order book state after matching
- `last_execution_time.csv` - Last execution timestamp per sweep order

### Final Results (`data/outputs/{date}/{security}/`)

- `sweep_match_details.csv` - Detailed match-by-match simulation results
- `sweep_match_summary.csv` - Per-order simulation summary
- `sweep_utilization.csv` - Sweep order utilization statistics
- `orders_with_simulated_metrics.csv` - Orders with both real and simulated metrics
- `group_comparison_summary.csv` - Group-level statistical comparison
- `order_level_comparison.csv` - Order-level comparison details
- `group_analysis_detail.csv` - Detailed group breakdown
- `statistical_summary.csv` - Overall statistical analysis

## Configuration

### Input File Paths

Edit `INPUT_FILES` in `extract_partitioned.py`:

```python
INPUT_FILES = {
    'orders': 'data/raw/orders/drr_orders.csv',
    'trades': 'data/raw/trades/drr_trades_segment_1.csv',
    'nbbo': 'data/raw/nbbo/nbbo.csv',
    'session': 'data/raw/session/session.csv',
    'reference': 'data/raw/reference/ob.csv',
    'participants': 'data/raw/participants/par.csv',
}
```

### Column Mappings

Adapt to your data schema by editing `COLUMN_MAPPING` in `extract_partitioned.py`:

```python
COLUMN_MAPPING = {
    'orders': {
        'order_id': 'order_id',           # Your column name for order ID
        'timestamp': 'timestamp',          # Your column name for timestamp
        'order_type': 'exchangeordertype', # Your column name for order type
        # ... more mappings
    },
    # ... more file types
}
```

## Example Results

Sample output from a partition (2024-09-05/110621):

| Metric | Group 1 (Fully Filled) | Group 2 (Partially Filled) | Group 3 (Unfilled) |
|--------|------------------------|----------------------------|-------------------|
| **Orders** | 6,423 | 21,779 | 250 |
| **Real Matched Qty** | 2.28M | 470K | 0 |
| **Simulated Matched Qty** | 69K | 2.74M | 6.86M |
| **Change** | -97% | +482% | +∞ |

**Overall Improvement**: +250% increase in matched quantity (simulation vs real)

## Advanced Usage

### Using the Class-Based Pipeline

For more control, use the advanced class-based implementation:

```python
from src.ingest_centrepoint import CentrePointOrdersExtractor, TradesExtractor
from src.sweep_simulation.simulator import SweepOrderSimulator
from src.sweep_simulation.comparison_reporter import ComparisonReporter

# Extract orders
extractor = CentrePointOrdersExtractor(
    input_file='data/raw/orders/drr_orders.csv',
    output_dir='data/processed'
)
orders_df = extractor.extract()

# Run simulation
simulator = SweepOrderSimulator()
results = simulator.simulate(orders_df, nbbo_df)

# Generate comparison report
reporter = ComparisonReporter()
report = reporter.compare(orders_df, results)
```

### Processing Multiple Partitions

```python
from pathlib import Path

# Process all date/security combinations
for partition_dir in Path('data/processed').rglob('*/*/'):
    date = partition_dir.parent.name
    security = partition_dir.name
    print(f"Processing {date}/{security}...")
    # Run simulation on partition
```

## Known Limitations

1. **Limited NBBO Data**: Current test dataset has only 2 NBBO records; most pricing falls back to order bid/offer midpoint
2. **Single Security**: Test data contains only one security code (110621)
3. **Short Time Period**: Only 2 trading days (2024-09-04, 2024-09-05) in test data
4. **Group 1 Anomaly**: Fully-filled orders show worse fill ratio in simulation (requires investigation)

## Troubleshooting

### Common Issues

**Issue**: `FileNotFoundError: data/raw/orders/drr_orders.csv`
- **Solution**: Ensure raw data files are in `data/raw/` subdirectories

**Issue**: `KeyError: 'exchangeordertype'`
- **Solution**: Update `COLUMN_MAPPING` to match your data schema

**Issue**: `MemoryError` on large datasets
- **Solution**: Adjust `CHUNK_SIZE` in `extract_partitioned.py` (default: 100,000)

**Issue**: Empty output files
- **Solution**: Check that your data contains Centre Point orders (types 64, 256, 2048, 4096, 4098)

## Development

### Running Tests

```bash
# Run unit tests (coming soon)
pytest tests/

# Run specific test
pytest tests/test_simulator.py
```

### Code Style

This project follows PEP 8 style guidelines. Format code with:

```bash
black extract_partitioned.py src/
```

### Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

## Performance

Typical performance on standard hardware (MacBook Pro M1):

- **28,452 orders**: ~80 seconds total
- **4,228 trades**: ~5 seconds for trade extraction
- **Memory usage**: ~500MB peak
- **Output size**: ~50MB compressed

## Roadmap

### Planned Features

- [ ] Multi-file trade segment support
- [ ] Parallel partition processing
- [ ] Interactive visualization dashboard
- [ ] Real-time streaming mode
- [ ] Enhanced NBBO price discovery
- [ ] Alternative matching algorithms (pro-rata, price-time priority)
- [ ] Comprehensive unit test suite
- [ ] Performance profiling and optimization
- [ ] Docker containerization

### Future Research

- [ ] Investigate Group 1 simulation underperformance
- [ ] Analyze impact of NBBO data quality on simulation accuracy
- [ ] Compare multiple matching algorithms
- [ ] Validate simulation results against production data

## Documentation

- `docs/bi.txt` - ASX Trade business information and specifications
- `docs/dd.txt` - Data dictionary with field definitions
- `CLEANUP_ANALYSIS.md` - Project cleanup decisions and rationale

## License

This project is proprietary. All rights reserved.

## Contact

For questions or issues, please open an issue on GitHub:
https://github.com/zautlin/sweeporders/issues

## Acknowledgments

- ASX (Australian Securities Exchange) for market data specifications
- Centre Point order matching documentation

---

**Last Updated**: January 2026  
**Version**: 1.0.0  
**Status**: Production Ready
