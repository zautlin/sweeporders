# Centre Point Sweep Order Analysis Pipeline

A comprehensive pipeline for analyzing Centre Point sweep order execution quality by comparing actual market executions against simulated dark pool matching.

## Overview

This pipeline processes large-scale order and trade data to evaluate:
- **Execution Quality**: Price improvement and cost metrics (VWAP, execution cost, effective spread)
- **Fill Performance**: Fill rates, quantities, and execution times
- **Market Impact**: Comparing real market executions vs. simulated dark pool matches

## Quick Start

### Basic Usage (Single Partition)

```bash
# Process a single date/security combination
python src/main.py
```

Edit `src/config.py` to specify date and security code before running.

### Multi-Dataset Pipeline (NEW)

For processing 100GB+ files with multiple dates and securities:

```bash
# Process entire dataset in parallel
python run_multidataset_pipeline.py \
    --orders data/raw/orders/large_orders.csv \
    --trades data/raw/trades/large_trades.csv \
    --output data/processed \
    --workers 8
```

**Performance**: Processes 750 partitions in ~38 minutes (vs 3+ hours sequential)

See `archive/MULTIDATASET_PIPELINE.md` for detailed documentation.

## Features

### Core Analysis
- ✅ Matched order comparison (real vs simulated execution)
- ✅ Unmatched order analysis (orders not executed in dark pool)
- ✅ Statistical testing (paired t-tests, Spearman correlation, Cohen's d)
- ✅ Quantile analysis across execution metrics
- ✅ Volume-weighted execution time (VWTE) metric

### Multi-Dataset Capabilities (NEW)
- ✅ Streaming extraction for 100GB+ files (constant memory usage)
- ✅ Parallel processing (6-12x faster)
- ✅ Automatic partition tracking and registry
- ✅ Resumable processing with error recovery
- ✅ Auto-detection of system resources (CPU/RAM)

## Project Structure

```
sweeporders/
├── src/                          # Core pipeline modules
│   ├── main.py                   # Single-partition orchestrator
│   ├── config.py                 # Configuration
│   ├── data_processor.py         # Data loading and partitioning
│   ├── sweep_simulator.py        # Dark pool matching simulation
│   ├── sweep_execution_analyzer.py  # Matched order analysis
│   ├── unmatched_analyzer.py     # Unmatched order analysis
│   ├── partition_registry.py     # Multi-dataset partition tracking (NEW)
│   ├── streaming_extractor.py    # Memory-efficient extraction (NEW)
│   ├── parallel_engine.py        # Parallel processing engine (NEW)
│   └── system_config.py          # System resource detection (NEW)
│
├── run_multidataset_pipeline.py  # Multi-dataset orchestrator (NEW)
├── data/                         # Data directories
│   ├── raw/                      # Input data
│   │   ├── orders/
│   │   ├── trades/
│   │   ├── nbbo/
│   │   └── session/
│   └── processed/                # Output data (partitioned by date/security)
│
├── docs/                         # Technical documentation
├── archive/                      # Archived files and utilities
└── requirements.txt              # Python dependencies
```

## Installation

```bash
# Create virtual environment
python -m venv swp_env
source swp_env/bin/activate  # On Windows: swp_env\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Usage

### Option 1: Single Partition (Traditional)

Edit `src/config.py`:
```python
DATASET = 'DRR'              # or 'CBA'
SECURITY_CODE = '110621'     # ASX security code
DATE = '2024-09-05'          # Trading date
```

Run:
```bash
python src/main.py
```

### Option 2: Multi-Dataset (Recommended for Large Files)

```bash
# Check system configuration
python run_multidataset_pipeline.py --show-system-info

# Run pipeline with auto-detected settings
python run_multidataset_pipeline.py \
    --orders data/raw/orders/your_file.csv \
    --trades data/raw/trades/your_file.csv \
    --output data/processed

# Custom configuration
python run_multidataset_pipeline.py \
    --orders data/raw/orders/your_file.csv \
    --trades data/raw/trades/your_file.csv \
    --output data/processed \
    --workers 12 \
    --chunk-size 200000

# Resume interrupted run
python run_multidataset_pipeline.py \
    --orders data/raw/orders/your_file.csv \
    --trades data/raw/trades/your_file.csv \
    --output data/processed \
    --resume
```

## Output Structure

```
data/processed/
├── partition_registry.json       # Processing status (multi-dataset only)
└── {date}/{security}/
    ├── orders.csv
    ├── trades.csv
    └── stats/
        ├── matched/
        │   ├── sweep_order_comparison_detailed.csv
        │   ├── sweep_order_comparison_summary.csv
        │   ├── sweep_order_statistical_tests.csv
        │   ├── sweep_order_quantile_comparison.csv
        │   └── sweep_order_validation.json
        └── unmatched/
            ├── sweep_order_unexecuted_in_dark.csv
            └── unexecuted_summary.json
```

## Key Metrics

### Execution Quality
- **Execution Cost (Arrival)**: Cost relative to arrival midpoint (bps)
- **Execution Cost (VW)**: Volume-weighted cost using trade-time NBBO (bps)
- **Effective Spread**: Captured spread percentage
- **VWAP**: Volume-weighted average price

### Fill Performance
- **Fill Rate**: Percentage of order quantity filled
- **Quantity Filled**: Total quantity executed
- **Number of Fills**: Trade count per order
- **VWTE**: Volume-weighted time of execution (when bulk volume executed)

### Timing Metrics
- **Execution Time**: Time from first to last fill
- **Time to First Fill**: Time from order arrival to first execution
- **VWTE**: Volume-weighted execution time

## Performance

### Single Partition
- Typical partition (1 day, 1 security): 10-30 seconds
- Includes: data loading, simulation, analysis, statistical tests

### Multi-Dataset (750 partitions)
- **Sequential (1 worker)**: ~3.1 hours
- **Parallel (8 workers)**: ~38 minutes (5.2x faster)
- **Memory usage**: ~500MB constant during extraction + 2-4GB per worker

## Documentation

- `docs/` - Technical specifications and data dictionaries
- `archive/MULTIDATASET_PIPELINE.md` - Multi-dataset pipeline documentation
- `archive/README_ARCHIVE.md` - Information about archived files

## Archived Files

Utility scripts and development documentation are archived in `archive/`:
- Analysis utilities
- Report generators
- Development documentation
- Debug data

See `archive/README_ARCHIVE.md` for details.

## Dependencies

- Python 3.8+
- pandas >= 2.0.0
- numpy >= 1.24.0
- scipy >= 1.10.0
- psutil >= 5.9.0

## System Requirements

### Minimum
- 8GB RAM
- 4 CPU cores
- 10GB free disk space

### Recommended (Multi-Dataset)
- 16GB+ RAM
- 8+ CPU cores
- SSD storage for large files

## Troubleshooting

### Out of Memory
- Reduce chunk size: `--chunk-size 50000`
- Reduce workers: `--workers 4`

### Slow Performance
- Increase workers: `--workers 12`
- Increase chunk size: `--chunk-size 250000`
- Use SSD for data files

### Failed Partitions
Check registry for errors:
```python
from src.partition_registry import PartitionRegistry
registry = PartitionRegistry('data/processed/partition_registry.json')
registry.load()
for p in registry.get_failed():
    print(f"{p.partition_key}: {p.error_message}")
```

## Contributing

This is a research pipeline. For modifications:
1. Test with small datasets first (2-3 days)
2. Run validation scripts on outputs
3. Document changes in commit messages

## License

Internal research project.
