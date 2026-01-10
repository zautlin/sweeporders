# Reference Data Normalization Plan

## Goal
Normalize column names for all reference data files (session, reference, nbbo, participants) so downstream code doesn't need to hardcode raw column names.

---

## Current State Analysis

### Files Requiring Normalization

| Data Type    | Current State | Raw Columns Need Normalizing | Target Columns |
|--------------|---------------|-------------------------------|----------------|
| **session**  | ❌ NOT normalized | `OrderBookId`, `TradeDate` | `orderbookid`, `timestamp` |
| **reference**| ❌ NOT normalized | `Id`, `TradeDate` | `orderbookid`, `timestamp` |
| **nbbo**     | ✅ Already normalized | `bidprice`, `offerprice`, `orderbookid`, etc. | Various (already working) |
| **participants** | ❌ NOT normalized | `TradeDate` | `timestamp` |

### Raw Column Names Found

**Session** (`20240505_session.csv`):
```
OrderBookId, TradeDate, Timestamp, Level, Name, Type, etc.
```

**Reference** (`20250505_orderbook.csv`):
```
Id, TradeDate, Timestamp, Name, InstrumentGroupId, etc.
```

**NBBO** (`cba_20240505_nbbo.csv`):
```
orderbookid, timestamp, bidprice, offerprice, tradedate, etc.
```

**Participants** (`20240505_par.csv`):
```
TradeDate, Timestamp, ParticipantName, Id, etc.
```

---

## Existing Normalization Map

Already defined in `src/utils/normalization.py`:

```python
COLUMN_NORMALIZATION_MAP = {
    'session': {
        'OrderBookId': 'orderbookid',
        'TradeDate': 'timestamp',
    },
    'reference': {
        'Id': 'orderbookid',
        'TradeDate': 'timestamp',
    },
    'nbbo': {
        'security_code': 'orderbookid',
        'securitycode': 'orderbookid',
        'bidprice': 'bid',
        'offerprice': 'offer',
        'bidquantity': 'bid_quantity',
        'offerquantity': 'offer_quantity',
    },
    'participants': {
        'TradeDate': 'timestamp',
    }
}
```

✅ **Good news:** The normalization mappings already exist!

---

## Current Code Flow

### Where Normalization Happens (✅) and Doesn't Happen (❌)

```python
# data_processor.py

# ✅ NBBO - normalized at line 85
def _process_nbbo_data(...):
    ...
    partition_data_normalized = normalize_column_names(partition_data, 'nbbo')
    
# ✅ Orders - normalized at line 266
def extract_orders(...):
    ...
    group_df_normalized = normalize_column_names(group_df, 'orders')
    
# ✅ Trades - normalized at line 328
def extract_trades(...):
    ...
    partition_trades_normalized = normalize_column_names(partition_trades, 'trades')

# ❌ Session/Reference - NOT normalized
def _process_single_reference_type(...):
    df = _read_csv_files_concat(file_list)
    df = add_date_column(df, timestamp_col)
    return _partition_by_date_and_save(df, ...)  # <-- No normalization!

# ❌ Participants - NOT normalized  
def _process_participants_with_fallback(...):
    df = _read_csv_files_concat(file_list)
    df = add_date_column(df, timestamp_col)
    # Process and save...  # <-- No normalization!
```

---

## Implementation Plan

### Step 1: Update `_process_single_reference_type()` ✅
**File:** `src/pipeline/data_processor.py` (lines 102-112)

**Change:**
- Add `data_type` parameter to function signature
- Call `normalize_column_names()` after reading files and before saving
- Update callers to pass 'session' or 'reference' as data_type

**Before:**
```python
def _process_single_reference_type(file_list, timestamp_col, unique_dates, processed_dir, filename):
    """Process single reference data type: read files, add date, partition, save."""
    if not file_list:
        return {}
    
    df = _read_csv_files_concat(file_list)
    if df is None or len(df) == 0:
        return {}
    
    df = add_date_column(df, timestamp_col)
    return _partition_by_date_and_save(df, unique_dates, processed_dir, filename, col.common.date)
```

**After:**
```python
def _process_single_reference_type(file_list, timestamp_col, unique_dates, processed_dir, filename, data_type):
    """Process single reference data type: read files, normalize, add date, partition, save."""
    if not file_list:
        return {}
    
    df = _read_csv_files_concat(file_list)
    if df is None or len(df) == 0:
        return {}
    
    # Normalize column names BEFORE processing
    df = normalize_column_names(df, data_type)
    
    df = add_date_column(df, timestamp_col)
    return _partition_by_date_and_save(df, unique_dates, processed_dir, filename, col.common.date)
```

### Step 2: Update Callers of `_process_single_reference_type()` ✅
**File:** `src/pipeline/data_processor.py` (lines 435, 444)

**Session caller (line 435):**
```python
results['session'] = _process_single_reference_type(
    session_files, col.session.timestamp, unique_dates, processed_dir, 'session.csv.gz', 'session'
)
```

**Reference caller (line 444):**
```python
results['reference'] = _process_single_reference_type(
    reference_files, col.reference.timestamp, unique_dates, processed_dir, 'reference.csv.gz', 'reference'
)
```

### Step 3: Update `_process_participants_with_fallback()` ✅
**File:** `src/pipeline/data_processor.py` (lines 115-158)

**Change:**
- Add `normalize_column_names()` call after reading files

**Add after line 124:**
```python
df = add_date_column(df, timestamp_col)
df = normalize_column_names(df, 'participants')  # <-- ADD THIS LINE
```

### Step 4: Verify NBBO Normalization ✅
**File:** `src/pipeline/data_processor.py` (line 85)

**Current code (already correct):**
```python
partition_data_normalized = normalize_column_names(partition_data, 'nbbo')
```

✅ NBBO is already normalized - no changes needed!

### Step 5: Update Normalization Map (if needed) ✅
**File:** `src/utils/normalization.py`

Check if any additional column mappings are needed based on raw data analysis.

**Potential additions:**
```python
'nbbo': {
    'security_code': 'orderbookid',
    'securitycode': 'orderbookid',
    'bidprice': 'bid',          # Already exists
    'offerprice': 'offer',      # Already exists
    'bidquantity': 'bid_quantity',   # Already exists
    'offerquantity': 'offer_quantity', # Already exists
    'tradedate': 'tradedate',   # May need to check if this needs normalization
},
```

---

## Testing Plan

### Test 1: DRR (OrderbookID 110621, Date 20240905)
```bash
python src/main.py --orderbookid 110621 --date 20240905 --stage 1
```

**Verify:**
- Session files saved with normalized columns
- Reference files saved with normalized columns
- NBBO files continue to work
- No downstream errors

### Test 2: BHP (OrderbookID 70616, Date 20240905)
```bash
python src/main.py --orderbookid 70616 --date 20240905 --stage 1
```

**Verify:**
- All reference data normalized correctly
- Processed files have standardized column names

### Test 3: Inspect Processed Files
```bash
# Check processed session file
python -c "import pandas as pd; df = pd.read_csv('data/processed/2024-09-05/session.csv.gz'); print(df.columns.tolist())"

# Check processed reference file  
python -c "import pandas as pd; df = pd.read_csv('data/processed/2024-09-04/reference.csv.gz'); print(df.columns.tolist())"
```

**Expected:** Columns should show `orderbookid`, `timestamp` instead of `OrderBookId`, `TradeDate`, `Id`

---

## Success Criteria

✅ All reference data files (session, reference, nbbo, participants) have normalized column names  
✅ Downstream code doesn't need to handle raw column name variations  
✅ DRR and BHP tests pass successfully  
✅ No errors in processing pipeline  
✅ Processed files contain standardized column names

---

## Files to Modify

1. ✅ `src/pipeline/data_processor.py` - Add normalization calls (3 changes)
2. ✅ `src/utils/normalization.py` - Review/update normalization map (if needed)

---

## Summary

**Problem:** Session, reference, and participants data aren't normalized, forcing downstream code to handle raw column names like `OrderBookId`, `Id`, `TradeDate`.

**Solution:** Add `normalize_column_names()` calls to all reference data processing functions, ensuring consistent column naming throughout the pipeline.

**Impact:** Cleaner code, no hardcoded column names downstream, easier maintenance.
