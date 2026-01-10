# Main.py Split Plan - Option 1: By Responsibility

## Goal
Split main.py (627 lines) into 4 focused modules:
1. `main.py` - Just main() orchestrator (~50 lines)
2. `pipeline/pipeline_config.py` - All CLI/config logic (~200 lines)
3. `pipeline/pipeline_stages.py` - All stage execution (~250 lines)
4. `pipeline/pipeline_output.py` - All printing/summary (~100 lines)

## File 1: main.py (~50 lines)

**Purpose:** Entry point with minimal orchestration logic

**Contents:**
```python
"""Centre Point Sweep Order Matching Pipeline - Main Entry Point"""

import time
from pipeline.pipeline_config import parse_arguments, build_runtime_config, setup_directories
from pipeline.pipeline_stages import execute_pipeline_stages
from pipeline.pipeline_output import print_pipeline_header, print_execution_summary

def main():
    """Main pipeline orchestrator."""
    start_time = time.time()
    
    args = parse_arguments()
    runtime_config = build_runtime_config(args)
    setup_directories()
    
    print_pipeline_header(runtime_config)
    
    data, all_partition_keys = execute_pipeline_stages(runtime_config)
    
    execution_time = time.time() - start_time
    print_execution_summary(data, runtime_config, execution_time)

if __name__ == '__main__':
    main()
```

**Functions:**
- `main()` - Just orchestration

**Lines:** ~50 lines

---

## File 2: pipeline/pipeline_config.py (~200 lines)

**Purpose:** CLI argument parsing, configuration building, discovery logic

**Contents:**
```python
"""Pipeline configuration and CLI argument handling."""

import argparse
from pathlib import Path
import config.config as config
from discovery.security_discovery import SecurityDiscovery
from utils.statistics_layer import StatisticsEngine

# === CLI PARSING ===
def parse_arguments():
    """Parse CLI arguments with config.py fallback."""
    # Current implementation (lines 17-63)

# === CONFIGURATION HELPERS ===
def _handle_list_operations(args, discovery):
    """Handle --list-dates and --list-securities operations."""
    # New function - extracted from get_runtime_config

def _resolve_stages_to_run(args):
    """Determine which stages to run from CLI args."""
    # New function - extracted from get_runtime_config

def _determine_parallel_mode(args):
    """Determine parallel processing mode from CLI args and config."""
    # New function - extracted from get_runtime_config

def _create_stats_engine(args):
    """Create statistics engine based on CLI args and config."""
    # New function - extracted from get_runtime_config

def _select_securities_from_args(args, discovery, date):
    """Select securities to process based on CLI arguments."""
    # New function - extracted from get_runtime_config
    # Handles: --auto-discover, --orderbookid, --ticker, default config

def _build_security_file_mappings(securities, date, stages):
    """Build input file mappings for each security."""
    # New function - extracted from get_runtime_config

# === MAIN CONFIG BUILDER ===
def build_runtime_config(args):
    """Build runtime config from CLI args and config (orchestrator)."""
    # Simplified version of get_runtime_config
    # Calls all the helper functions above
    # Returns runtime_config dict

# === DIRECTORY SETUP ===
def setup_directories():
    """Create output directories if they don't exist."""
    # Current implementation (lines 218-222)
```

**Functions:**
- `parse_arguments()` - Keep as-is from main.py
- `_handle_list_operations()` - NEW
- `_resolve_stages_to_run()` - NEW
- `_determine_parallel_mode()` - NEW
- `_create_stats_engine()` - NEW
- `_select_securities_from_args()` - NEW
- `_build_security_file_mappings()` - NEW
- `build_runtime_config()` - Simplified from get_runtime_config
- `setup_directories()` - Keep as-is from main.py

**Lines:** ~200 lines

---

## File 3: pipeline/pipeline_stages.py (~250 lines)

**Purpose:** All stage execution logic and orchestration

**Contents:**
```python
"""Pipeline stage execution functions."""

from pathlib import Path
import config.config as config
import pipeline.data_processor as dp
import pipeline.partition_processor as pp
import pipeline.metrics_generator as mg
import analysis.sweep_execution_analyzer as sea
import analysis.unmatched_analyzer as uma
import analysis.volume_analyzer as va
import aggregation.aggregate_sweep_results as agg
import aggregation.analyze_aggregated_results as analyze
import aggregation.aggregate_volume_analysis as vol_agg

# === STAGE 1 ===
def extract_and_prepare_data(input_files):
    """Extract orders, trades, reference data, order states, and execution times."""
    # Current implementation (lines 224-280)

# === STAGE 2 ===
def run_simulations_and_lob(data, enable_parallel):
    """Run simulations and create LOB states."""
    # Current implementation (lines 282-346)

# === STAGE 3 ===
def run_per_security_analysis(processed_dir, outputs_dir, partition_keys, stats_engine):
    """Run sweep execution and unmatched order analysis plus volume analysis."""
    # Current implementation (lines 401-436)

# === STAGE 4 ===
def run_cross_security_aggregation(runtime_config):
    """Aggregate sweep and volume results across all securities."""
    # Current implementation (lines 438-481)

# === PARTITION LOADING ===
def _load_partition_keys_from_disk(security, processed_dir):
    """Load partition keys from processed directory for a security."""
    # New function - extracted from main()
    # Lines 584-595 from main.py

# === STAGE EXECUTION ORCHESTRATION ===
def _execute_stage_1(sec_info, stages):
    """Execute Stage 1 for a security if needed."""
    # New wrapper

def _execute_stage_2(data, runtime_config, stages):
    """Execute Stage 2 for a security if needed."""
    # New wrapper

def _execute_stage_3(security, data, runtime_config, stages):
    """Execute Stage 3 for a security if needed."""
    # New wrapper

def _process_single_security(sec_info, runtime_config):
    """Process all stages for a single security."""
    # New function - extracted from main() loop
    # Lines 548-605 from main.py

def execute_pipeline_stages(runtime_config):
    """Execute all pipeline stages based on runtime config."""
    # New orchestrator - extracted from main()
    # Handles:
    # - Loop over securities
    # - Call _process_single_security for each
    # - Execute Stage 4 if needed
    # - Return data and partition keys
```

**Functions:**
- `extract_and_prepare_data()` - From main.py
- `run_simulations_and_lob()` - From main.py
- `run_per_security_analysis()` - From main.py
- `run_cross_security_aggregation()` - From main.py
- `_load_partition_keys_from_disk()` - NEW
- `_execute_stage_1()` - NEW
- `_execute_stage_2()` - NEW
- `_execute_stage_3()` - NEW
- `_process_single_security()` - NEW
- `execute_pipeline_stages()` - NEW (main orchestrator)

**Lines:** ~250 lines

---

## File 4: pipeline/pipeline_output.py (~100 lines)

**Purpose:** All output printing and formatting

**Contents:**
```python
"""Pipeline output printing and formatting."""

import config.config as config

# === HEADER PRINTING ===
def _print_statistics_tier(stats_engine):
    """Print statistics tier information."""
    # New function - extracted from main()

def _print_security_info(securities):
    """Print security configuration details."""
    # New function - extracted from main()

def _print_system_config():
    """Print system configuration."""
    # New function - extracted from main()

def _print_stage_plan(stages):
    """Print which stages will be executed."""
    # New function - extracted from main()

def print_pipeline_header(runtime_config):
    """Print pipeline header with configuration details."""
    # New function - combines lines 495-536 from main()
    # Calls helper functions above

# === SUMMARY PRINTING ===
def _format_partition_breakdown(data):
    """Format partition breakdown for summary."""
    # New helper - extracted from print_summary

def _format_output_directories():
    """Format output directory paths."""
    # New helper - extracted from print_summary

def print_execution_summary(data, runtime_config, execution_time):
    """Print pipeline execution summary."""
    # Simplified version of print_summary (lines 348-399)
    # Calls helper functions above
```

**Functions:**
- `_print_statistics_tier()` - NEW
- `_print_security_info()` - NEW
- `_print_system_config()` - NEW
- `_print_stage_plan()` - NEW
- `print_pipeline_header()` - NEW (orchestrator)
- `_format_partition_breakdown()` - NEW
- `_format_output_directories()` - NEW
- `print_execution_summary()` - Simplified from print_summary

**Lines:** ~100 lines

---

## Migration Map

| Current (main.py) | New Location | New Name |
|------------------|--------------|----------|
| `parse_arguments()` | pipeline_config.py | `parse_arguments()` |
| `get_runtime_config()` | pipeline_config.py | `build_runtime_config()` + helpers |
| `setup_directories()` | pipeline_config.py | `setup_directories()` |
| `extract_and_prepare_data()` | pipeline_stages.py | `extract_and_prepare_data()` |
| `run_simulations_and_lob()` | pipeline_stages.py | `run_simulations_and_lob()` |
| `run_per_security_analysis()` | pipeline_stages.py | `run_per_security_analysis()` |
| `run_cross_security_aggregation()` | pipeline_stages.py | `run_cross_security_aggregation()` |
| `print_summary()` | pipeline_output.py | `print_execution_summary()` |
| `main()` (lines 548-605) | pipeline_stages.py | `_process_single_security()` + `execute_pipeline_stages()` |
| `main()` (lines 495-536) | pipeline_output.py | `print_pipeline_header()` + helpers |
| `main()` remaining | main.py | `main()` (simplified) |

---

## Implementation Steps

### Step 1: Create pipeline_config.py
1. Create new file
2. Copy `parse_arguments()`
3. Extract helpers from `get_runtime_config()`
4. Create `build_runtime_config()` orchestrator
5. Copy `setup_directories()`

### Step 2: Create pipeline_output.py
1. Create new file
2. Extract print helpers from main()
3. Create `print_pipeline_header()`
4. Refactor `print_summary()` → `print_execution_summary()`

### Step 3: Create pipeline_stages.py
1. Create new file
2. Copy all 4 stage functions
3. Extract `_load_partition_keys_from_disk()`
4. Create stage wrappers
5. Create `_process_single_security()`
6. Create `execute_pipeline_stages()` orchestrator

### Step 4: Simplify main.py
1. Add imports from new modules
2. Simplify `main()` to ~50 lines
3. Remove all moved functions
4. Keep only orchestration logic

### Step 5: Simplify all docstrings to 1 line

### Step 6: Test
1. Test with DRR (orderbookid 110621)
2. Test with BHP (orderbookid 70616)
3. Test --list-dates
4. Test --list-securities

### Step 7: Commit

---

## Benefits

### 1. Separation of Concerns
- **Configuration** isolated in pipeline_config.py
- **Execution** isolated in pipeline_stages.py
- **Output** isolated in pipeline_output.py
- **Orchestration** clear in main.py

### 2. Testability
- Can test config building without executing pipeline
- Can test stage execution without CLI parsing
- Can test output formatting independently
- Can mock any module easily

### 3. Maintainability
- Adding new CLI args → modify pipeline_config.py
- Adding new stages → modify pipeline_stages.py
- Changing output format → modify pipeline_output.py
- Main orchestration logic separate

### 4. Readability
- main.py shows high-level flow at a glance
- Each module has single responsibility
- No 627-line monolith

### 5. Reusability
- Can import stage functions from other scripts
- Can reuse config building in tests
- Can use output formatters elsewhere

---

## File Size Comparison

| File | Lines | Purpose |
|------|-------|---------|
| **Before** | | |
| main.py | 627 | Everything |
| **After** | | |
| main.py | ~50 | Entry point |
| pipeline/pipeline_config.py | ~200 | Config/CLI |
| pipeline/pipeline_stages.py | ~250 | Execution |
| pipeline/pipeline_output.py | ~100 | Output |
| **Total** | ~600 | (-27 lines) |

---

## All Requirements Met

✅ **main.py has only main() function**
✅ **All other code moved to appropriately named files**
✅ **Organized by responsibility (config, stages, output)**
✅ **All docstrings max 1 line**
✅ **No functional changes**
✅ **All existing behavior preserved**
✅ **Better testability and maintainability**

This makes perfect sense! 🎯
