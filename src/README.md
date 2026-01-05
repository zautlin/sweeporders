# Source Code Directory

This directory contains the Centre Point Sweep Order Analysis Pipeline source code.

## Directory Structure

```
src/
├── main.py                    # Pipeline entry point
├── config.py                  # Configuration and settings
├── column_schema.py           # Centralized column schema system
├── system_config.py           # System resource configuration
│
├── pipeline/                  # Core data processing pipeline
│   ├── data_processor.py      # Data extraction & partitioning
│   ├── partition_processor.py # Partition processing orchestration
│   ├── sweep_simulator.py     # Sweep order matching simulation
│   └── metrics_generator.py   # Performance metrics calculation
│
├── analysis/                  # Analysis modules
│   ├── sweep_execution_analyzer.py  # Sweep execution analysis
│   ├── unmatched_analyzer.py        # Unmatched order root cause analysis
│   ├── volume_analyzer.py           # Volume-based performance analysis
│   └── data_explorer.py             # Data exploration & profiling
│
├── aggregation/               # Cross-security aggregation
│   ├── aggregate_sweep_results.py     # Aggregate sweep comparisons
│   ├── aggregate_volume_analysis.py   # Aggregate volume analysis
│   └── analyze_aggregated_results.py  # Statistical tests on aggregated data
│
├── utils/                     # Utility modules
│   ├── data_utils.py          # Data manipulation utilities
│   ├── file_utils.py          # File I/O operations
│   └── statistics_layer.py    # Statistical testing framework
│
└── discovery/                 # Auto-discovery
    └── security_discovery.py  # Automatic security detection
```

## Module Descriptions

### Core Modules

**`main.py`** - Pipeline entry point
- CLI argument parsing
- Stage orchestration
- Main execution flow

**`config.py`** - Configuration
- File paths and directories
- Pipeline parameters
- Column mapping definitions
- Centre Point order types

**`column_schema.py`** - Column Schema System
- Centralized column name mappings
- Schema-independent column accessors
- Type-safe column references

**`system_config.py`** - System Configuration
- CPU and memory detection
- Chunk size calculation
- Worker pool sizing

---

### Pipeline Package (`pipeline/`)

Core data processing pipeline for sweep order analysis.

**`data_processor.py`**
- Extract Centre Point orders from raw data
- Partition by date/security
- Extract and match trades
- Process reference data (NBBO, session, participants)
- Extract order states (before/after matching)

**`partition_processor.py`**
- Process partitions sequentially or in parallel
- Orchestrate simulation and metrics calculation
- Trade-level comparison

**`sweep_simulator.py`**
- Simulate dark pool sweep order matching
- Time-priority matching algorithm
- NBBO-based midpoint pricing
- Match detail tracking

**`metrics_generator.py`**
- Calculate execution metrics (VWAP, fill rates, execution time)
- Compare real vs simulated performance
- Group-based analysis
- Statistical significance testing

---

### Analysis Package (`analysis/`)

Specialized analysis modules for different aspects of sweep execution.

**`sweep_execution_analyzer.py`**
- Analyze sweep execution performance
- Real vs simulated comparison
- Group-based breakdowns (fully filled, partially filled, unfilled)
- Statistical tests (t-tests, effect sizes)

**`unmatched_analyzer.py`**
- Root cause analysis for unmatched orders
- Liquidity analysis
- Price overlap detection
- Order book depth assessment

**`volume_analyzer.py`**
- Volume-based performance analysis
- Quartile bucketing
- Volume-weighted metrics
- Size-stratified comparisons

**`data_explorer.py`**
- Data exploration and profiling
- Order/trade summaries
- Hourly pattern detection
- Data quality checks

---

### Aggregation Package (`aggregation/`)

Cross-security aggregation and statistical analysis.

**`aggregate_sweep_results.py`**
- Aggregate results across multiple securities
- Per-security summary statistics
- Cross-security data preparation

**`aggregate_volume_analysis.py`**
- Aggregate volume analysis across securities
- Volume bucket comparisons
- Weighted metrics

**`analyze_aggregated_results.py`**
- Statistical tests on aggregated data
- Cross-security ANOVA
- Pairwise comparisons
- Effect size calculations

---

### Utils Package (`utils/`)

Shared utility modules used across the pipeline.

**`data_utils.py`**
- Column name normalization
- Sweep order identification
- Data type conversions

**`file_utils.py`**
- Partition directory management
- Load/save operations for processed data
- File path construction

**`statistics_layer.py`**
- Optional statistical testing framework
- 3-tier system (descriptive / approximate / full scipy)
- T-tests, ANOVA, correlations, confidence intervals
- Graceful degradation without scipy

---

### Discovery Package (`discovery/`)

Automatic detection of available securities in raw data.

**`security_discovery.py`**
- Scan raw data directories
- Detect available securities by date
- Count orders and trades per security
- Generate security metadata

---

## Import Conventions

When importing modules from within `src/`, use relative package imports:

```python
# From main.py
import pipeline.data_processor as dp
from analysis.sweep_execution_analyzer import analyze_sweep_execution
from utils.statistics_layer import StatisticsEngine

# From within pipeline/ modules
from .data_processor import load_partition_data
from ..utils.file_utils import save_simulation_results

# From within analysis/ modules
from ..pipeline.metrics_generator import calculate_simulated_metrics
from ..utils.statistics_layer import StatisticsEngine
```

## Adding New Modules

When adding new modules:

1. **Choose the appropriate package** based on functionality
2. **Add docstring** describing module purpose
3. **Follow naming conventions** (lowercase with underscores)
4. **Import from appropriate packages** using relative imports
5. **Update package `__init__.py`** if needed

## Package Guidelines

- **`pipeline/`** - Core data processing only
- **`analysis/`** - Analytical calculations and reports
- **`aggregation/`** - Cross-security aggregation logic
- **`utils/`** - Reusable utilities with no business logic
- **`discovery/`** - Data scanning and metadata extraction

## Testing

Run pipeline from project root:

```bash
# Test with single security
python src/main.py --orderbookid 110621 --date 20240905 --stage 1

# Test full pipeline
python src/main.py --orderbookid 110621 --date 20240905 --all-stages

# Test with statistics enabled
python src/main.py --orderbookid 110621 --date 20240905 --enable-stats
```

## Dependencies

See `requirements.txt` in project root for full dependency list.

Core dependencies:
- `pandas` - Data manipulation
- `numpy` - Numerical computing
- `scipy` (optional) - Statistical tests
