# Centre Point Sweep Order Matching Pipeline

Python-based system for analyzing and simulating sweep order matching behavior in Centre Point order books.

## Overview

This pipeline extracts historical order and trade data, simulates sweep order matching using a time-priority algorithm, and compares simulated execution against real market execution with comprehensive statistical analysis.

## Project Structure

```
sweeporders/
├── src/                          # Source code
│   ├── main.py                   # Main pipeline controller
│   ├── data_processor.py         # Data extraction and preparation
│   ├── sweep_simulator.py        # Sweep matching simulation
│   ├── metrics_generator.py      # Metrics calculation and comparison
│   └── columns.py                # Configuration and column mappings
├── data/                         # Data directory
│   ├── raw/                      # Raw input data
│   │   ├── orders/
│   │   ├── trades/
│   │   ├── nbbo/
│   │   ├── session/
│   │   ├── reference/
│   │   └── participants/
│   ├── processed/                # Intermediate processed data
│   └── outputs/                  # Final simulation results
├── docs/                         # Documentation
│   ├── TECHNICAL_SPECIFICATION.md
│   ├── bi.txt
│   └── dd.txt
├── requirements.txt              # Python dependencies
└── README.md
```

## Features

- **Data Extraction**: Processes large CSV files in chunks, filters Centre Point orders
- **Partitioning**: Organizes data by date and security for efficient processing
- **Sweep Simulation**: Time-priority matching algorithm with execution windows
- **Comparison Analysis**: Comprehensive statistical comparison (real vs simulated)
- **Group Classification**: Categorizes orders by fill status (Fully, Partially, Unfilled)
- **Statistical Testing**: Paired t-tests across multiple segments

## Requirements

- Python 3.x
- pandas
- numpy
- scipy
- psutil (for auto-detecting system resources)

Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Basic Execution

Run the pipeline from the `src/` directory:

```bash
cd src
python main.py
```

Or run from the project root:

```bash
python -m src.main
```

The pipeline will automatically detect your system's CPU cores and available memory to optimize processing.

**Example output**:
```
System Configuration:
  CPU Cores: 8
  Workers: 6
  Available Memory: 15.23 GB
  Chunk Size: 80,000
  Parallel Processing: Enabled
```

### Input Files

Place the following CSV files in `data/raw/`:
- `orders/drr_orders.csv` - Order data
- `trades/drr_trades_segment_1.csv` - Trade executions
- `nbbo/nbbo.csv` - NBBO quotes
- `session/session.csv` - Session data
- `reference/ob.csv` - Reference data
- `participants/par.csv` - Participant data

### Output Files

The pipeline generates two sets of outputs:

**Intermediate (`data/processed/{date}/{security}/`)**:
- `cp_orders_filtered.csv.gz` - Filtered Centre Point orders
- `cp_trades_matched.csv.gz` - Matched trades
- `cp_trades_aggregated.csv.gz` - Aggregated trade metrics
- `nbbo.csv.gz` - NBBO data
- `orders_before_matching.csv` - Initial order states
- `orders_after_matching.csv` - Final order states
- `last_execution_time.csv` - Execution time windows

**Final Results (`data/outputs/{date}/{security}/`)**:
- `simulation_order_summary.csv` - Per-order simulation results
- `simulation_match_details.csv` - Individual match details
- `sweep_order_comparison.csv` - Real vs simulated comparison
- `sweep_group_summary.csv` - Statistics by group
- `sweep_statistical_tests.csv` - T-test results
- `sweep_size_analysis.csv` - Analysis by order size

## Pipeline Stages

The pipeline executes 11 stages:

1. **Extract Orders** - Filter Centre Point orders by type
2. **Extract Trades** - Match trades to orders
3. **Aggregate Trades** - Calculate per-order trade metrics
4. **Extract NBBO** - Load price reference data
5. **Extract Reference Data** - Load session, reference, participants
6. **Get Orders State** - Extract before/after states
7. **Extract Execution Times** - Calculate execution windows
8. **Simulate Sweep Matching** - Run time-priority simulation
9. **Calculate Simulated Metrics** - Compute simulation metrics
10. **Classify Order Groups** - Group by fill status
11. **Compare Real vs Simulated** - Generate comparison reports

## Configuration

### Automatic Configuration

The pipeline **automatically detects** system resources and optimizes settings:

- **CPU Cores**: Detects available cores and calculates optimal worker count
- **Available Memory**: Analyzes free RAM and calculates optimal chunk size
- **Workers**: CPU cores - 2 (capped at 16 workers)
- **Chunk Size**: 5% of available memory, constrained to 10K-500K rows

The system prints detected configuration on startup.

### Manual Override - Environment Variables

Override auto-detected values using environment variables:

```bash
# Set number of workers
export SWEEP_WORKERS=8

# Set chunk size
export SWEEP_CHUNK_SIZE=200000

# Run pipeline
cd src
python main.py
```

### Manual Override - Code

Edit `src/main.py` to override:

```python
# Force specific configuration
SYSTEM_CONFIG = sc.detect_system_config(
    override_workers=4,
    override_chunk_size=50000
)
```

### Configuration Reference

```python
# Centre Point order types
CENTRE_POINT_ORDER_TYPES = [64, 256, 2048, 4096, 4098]

# Sweep order type (subject of simulation)
SWEEP_ORDER_TYPE = 2048

# Directory paths
PROCESSED_DIR = 'data/processed'
OUTPUTS_DIR = 'data/outputs'
```

Column mappings defined in `src/columns.py`.

## System Requirements

### Minimum Requirements
- **CPU**: 2 cores (single-threaded processing)
- **RAM**: 4 GB
- **Disk**: 10 GB free space

### Recommended Requirements
- **CPU**: 4+ cores (parallel processing enabled)
- **RAM**: 16 GB
- **Disk**: 50 GB free space

### Resource Allocation Examples

| System | Workers | Chunk Size | Processing Mode |
|--------|---------|------------|-----------------|
| 2 cores, 4GB RAM | 1 | ~20K rows | Sequential |
| 4 cores, 8GB RAM | 3 | ~40K rows | Parallel |
| 8 cores, 16GB RAM | 6 | ~80K rows | Parallel |
| 16 cores, 64GB RAM | 14 | ~300K rows | Parallel |

The pipeline automatically adjusts to your system capabilities.

## Algorithm

The core sweep matching algorithm:

1. Process sweep orders chronologically by **placement time**
2. For each sweep order:
   - Get execution time window `[first_execution_time, last_execution_time]`
   - Find all eligible orders that arrived in the window
   - Filter for opposite side, same security, exclude self
   - Sort by time priority (timestamp → sequence)
   - Match sequentially until sweep is filled or no more orders
   - Record matches at midpoint price
   - Update remaining quantities

## Order Groups

Orders classified into 3 groups based on **real execution**:

- **Group 1 (Fully Filled)**: `leavesquantity == 0`
- **Group 2 (Partially Filled)**: `leavesquantity > 0 AND totalmatchedquantity > 0`
- **Group 3 (Unfilled)**: `leavesquantity > 0 AND totalmatchedquantity == 0`

## Size Categories

Orders categorized by quantity:

- **Small**: quantity ≤ 500
- **Medium**: 500 < quantity ≤ 2000
- **Large**: quantity > 2000

## Timezone

All timestamps are converted from UTC to **AEST (Australia/Sydney)** timezone.

## Documentation

Comprehensive technical documentation available in `docs/TECHNICAL_SPECIFICATION.md`:
- Architecture diagrams
- Class diagrams
- Algorithm pseudocode
- Data models
- API specifications

## Performance

- **Chunked Processing**: Handles large files efficiently (100K rows/chunk)
- **Partitioning**: Independent processing per (date, security)
- **Compression**: gzip compression reduces storage by ~70-80%
- **Memory Efficient**: Processes data incrementally

## License

[Add license information]

## Authors

[Add author information]

## Version History

- v1.0 - Initial release with full pipeline functionality
