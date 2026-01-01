# Column Names Configuration Guide

## Overview

The `config/columns.py` file provides centralized management of all column names and constants used throughout the sweep orders analysis pipeline. This ensures consistent naming conventions across all modules and makes it easy to maintain and update field mappings.

## Structure

### 1. Input Columns (`INPUT_COLUMNS`)

Maps source column names from input CSV files to standard column names used internally.

```python
INPUT_COLUMNS = {
    'orders': {
        'order_id': 'orderid',
        'security_code': 'securitycode',
        'exchange_order_type': 'exchangeordertype',
        'side': 'side',  # 1=BUY, 2=SELL
        'price': 'price',
        'quantity': 'quantity',
        'timestamp': 'timestamp',
        'participant_id': 'participantid',
        'leaves_quantity': 'leavesquantity',
    },
    'trades': {
        'order_id': 'orderid',
        'trade_price': 'tradeprice',
        'trade_time': 'tradetime',
        'trade_quantity': 'quantity',
    }
}
```

**Usage**: When reading raw input files, use the values (actual column names from CSV) and then convert to standard names using `rename_columns_to_standard()`.

### 2. Standard Column Names (`STANDARD_COLUMNS`)

Normalized column names used consistently throughout the codebase.

- Order identifiers: `order_id`, `security_code`, `participant_id`, `timestamp`
- Price/Quantity: `price`, `quantity`, `leaves_quantity`
- Classifications: `exchange_order_type`, `scenario_type`
- Execution data: `trade_price`, `trade_time`
- Fill metrics: `total_quantity_filled`, `fill_ratio`, `avg_execution_price`
- Simulation metrics: `simulated_fill_ratio`, `simulated_execution_price`

**Usage**: All code should use these standard names after data transformation.

### 3. Order Types

**Centre Point Order Types** (sweep order identifiers):
```python
CENTRE_POINT_ORDER_TYPES = [64, 256, 2048, 4096, 4098]
```

**Order Side Values**:
- `1` = BUY
- `2` = SELL

### 4. Scenario Types

Classification results for sweep orders:

```python
SCENARIO_TYPES = {
    'A': 'A_Immediate_Full',      # 100% fill in < 1 second
    'B': 'B_Eventual_Full',       # 100% fill in >= 1 second  
    'C': 'C_Partial_None',        # < 100% fill or no fill
}
```

### 5. Scenario Classification Thresholds

```python
SCENARIO_THRESHOLDS = {
    'immediate_fill_threshold_ratio': 0.99,        # >= 99% = full
    'immediate_fill_threshold_seconds': 1.0,       # < 1 sec = immediate
    'eventual_fill_threshold_seconds': 1.0,        # >= 1 sec = eventual
    'eventual_fill_threshold_ratio': 0.99,         # >= 99% = full
}
```

### 6. Output Files (`OUTPUT_FILES`)

Maps logical file identifiers to actual output filenames.

```python
OUTPUT_FILES = {
    # Phase 1
    'centrepoint_orders_raw': 'centrepoint_orders_raw.csv.gz',
    'centrepoint_trades_raw': 'centrepoint_trades_raw.csv.gz',
    'centrepoint_trades_agg': 'centrepoint_trades_agg.csv.gz',
    
    # Phase 2
    'scenario_a_immediate_full': 'scenario_a_immediate_full.csv.gz',
    'scenario_c_partial_none': 'scenario_c_partial_none.csv.gz',
    
    # Phase 3
    'scenario_a_simulation_results': 'scenario_a_simulation_results.csv.gz',
    'scenario_c_simulation_results': 'scenario_c_simulation_results.csv.gz',
    
    # Phase 4
    'scenario_comparison_summary': 'scenario_comparison_summary.csv.gz',
    'order_level_detail': 'order_level_detail.csv.gz',
    # ... and more
}
```

**Usage**: Instead of hardcoding filenames, use:
```python
from config.columns import OUTPUT_FILES
from pathlib import Path

output_path = Path(output_dir) / OUTPUT_FILES['centrepoint_orders_raw']
```

### 7. Input Files (`INPUT_FILES`)

Maps logical data source identifiers to file paths.

```python
INPUT_FILES = {
    'orders': 'data/orders/drr_orders.csv',
    'trades': 'data/trades/drr_trades_segment_1.csv',
    'nbbo': 'data/nbbo/nbbo.csv',
    'participants': 'data/participants/par.csv',
    'reference': 'data/reference/ob.csv',
    'session': 'data/session/session.csv',
}
```

## Helper Functions

### `get_input_column_name(data_type, standard_name)`

Get the actual input column name for a given standard column name.

```python
from config.columns import get_input_column_name

actual_name = get_input_column_name('orders', 'order_id')  # Returns 'orderid'
```

### `rename_columns_to_standard(df, data_type)`

Rename input columns to standard names.

```python
from config.columns import rename_columns_to_standard

orders_df = pd.read_csv('data/orders/drr_orders.csv')
orders_df = rename_columns_to_standard(orders_df, 'orders')
# Now has: order_id, security_code, side, etc. instead of orderid, securitycode, side
```

### `validate_columns(df, required_columns, context="")`

Validate that required columns exist in DataFrame.

```python
from config.columns import validate_columns

required = ['order_id', 'security_code', 'price']
validate_columns(df, required, context='Orders DataFrame')
# Raises ValueError if any column is missing
```

## Usage Examples

### Example 1: Reading Orders File

```python
import pandas as pd
from config.columns import rename_columns_to_standard, validate_columns

# Read raw file
orders_df = pd.read_csv('data/orders/drr_orders.csv')

# Rename to standard names
orders_df = rename_columns_to_standard(orders_df, 'orders')

# Validate required columns
validate_columns(
    orders_df,
    ['order_id', 'security_code', 'side', 'price', 'quantity'],
    context='Orders'
)
```

### Example 2: Filtering Sweep Orders

```python
from config.columns import CENTRE_POINT_ORDER_TYPES

sweep_orders = orders_df[
    orders_df['exchange_order_type'].isin(CENTRE_POINT_ORDER_TYPES)
]
```

### Example 3: Classifying Scenarios

```python
from config.columns import SCENARIO_TYPES, SCENARIO_THRESHOLDS

threshold = SCENARIO_THRESHOLDS['immediate_fill_threshold_ratio']

scenario_a = orders[
    (orders['fill_ratio'] >= threshold) &
    (orders['execution_duration_sec'] < 1.0)
].copy()

scenario_a['scenario_type'] = SCENARIO_TYPES['A']
```

### Example 4: Saving Output Files

```python
from pathlib import Path
from config.columns import OUTPUT_FILES

output_dir = 'processed_files'

# Save with standard filename
output_path = Path(output_dir) / OUTPUT_FILES['scenario_comparison_summary']
summary_df.to_csv(output_path, compression='gzip', index=False)
```

## Benefits

1. **Single Source of Truth**: All column definitions in one place
2. **Easy Maintenance**: Update once, applies everywhere
3. **Consistency**: Standard names used throughout code
4. **Readability**: Descriptive standard names vs. raw CSV column names
5. **Validation**: Built-in helper functions for data quality checks
6. **Flexibility**: Easy to support multiple input format versions

## Migration Path

When integrating with existing code:

1. Import the config at the top of each module
2. Replace hardcoded column names with standard names
3. Use helper functions for column operations
4. Replace hardcoded filenames with `OUTPUT_FILES` and `INPUT_FILES`

## Current Integration

The following modules currently use the config:

- ✅ `src/ingest.py` - Uses `CENTRE_POINT_ORDER_TYPES`
- ⏳ `src/match_trades.py` - Ready for integration
- ⏳ `src/book.py` - Ready for integration
- ⏳ `src/classify.py` - Ready for integration
- ⏳ `src/simulate.py` - Ready for integration
- ⏳ `src/report.py` - Ready for integration

## Adding New Columns

To add a new column to the configuration:

1. Add to `INPUT_COLUMNS` if it comes from raw data
2. Add to `STANDARD_COLUMNS` with a descriptive name
3. Update relevant thresholds or constants if needed
4. Update modules that use the column
5. Update this documentation

Example:

```python
# Add to INPUT_COLUMNS
INPUT_COLUMNS = {
    'orders': {
        # ... existing ...
        'order_status': 'orderstatus',  # NEW
    }
}

# Add to STANDARD_COLUMNS
STANDARD_COLUMNS = {
    # ... existing ...
    'order_status': 'orderstatus',  # NEW
}
```

## References

- Main pipeline: `main.py`
- Config location: `config/columns.py`
- Phase 1 (Ingest): `src/ingest.py`
- Phase 2 (Classify): `src/classify.py`
- Phase 3 (Simulate): `src/simulate.py`
- Phase 4 (Report): `src/report.py`
