# Hardcoded Column Name Removal Plan

## Executive Summary

**Goal:** Complete removal of hardcoded column name strings in the pipeline codebase.

**Current Status:** ~80% complete (normalization phase done), ~20% remaining

**Remaining Work:** 
- Add 10 missing columns to COLUMN_MAPPING
- Update 3 critical files to use col.* accessors
- Fix 16+ hardcoded column references

---

## What's Already Done ✅

### Phase 1: Normalization System (COMPLETED)
- ✅ Created `src/utils/normalization.py` module
- ✅ Implemented `normalize_column_names()` function
- ✅ Added normalization for orders, trades, session, reference, participants, NBBO
- ✅ Applied normalization in `data_processor.py` at data ingestion points

### Phase 2: Column Schema System (COMPLETED)
- ✅ Created `src/config/column_schema.py` with accessor classes
- ✅ Implemented `col.orders.*`, `col.trades.*`, `col.common.*` accessors
- ✅ Used throughout most of the codebase (~200+ usages)

---

## What's Remaining (Phase 3)

### Problem Areas Identified

**MUST FIX (High Priority):**

1. **src/pipeline/sweep_simulator.py** - 7+ hardcoded columns
2. **src/analysis/sweep_execution_analyzer.py** - 6+ hardcoded columns  
3. **src/pipeline/data_processor.py** - 1 hardcoded column

**Total:** ~16+ instances across 3 critical files

---

## Missing Columns in COLUMN_MAPPING

The following columns exist in the data but are missing from `config.COLUMN_MAPPING`:

### 1. Trades Columns (Missing 7 columns)

```python
'trades': {
    # ... existing columns ...
    
    # ADD THESE:
    'dealsource': 'dealsource',
    'dealsource_decoded': 'dealsourcedecoded',
    'passive_aggressive': 'passiveaggressive',
    'match_group_id': 'matchgroupid',
    'national_bid_snapshot': 'nationalbidpricesnapshot',
    'national_offer_snapshot': 'nationalofferpricesnapshot',
    'participant_id': 'participantid',
}
```

### 2. Orders Columns (Missing 2 columns)

```python
'orders': {
    # ... existing columns ...
    
    # ADD THESE:
    'national_bid': 'national_bid',           # Check if this exists in raw data
    'national_offer': 'national_offer',       # Check if this exists in raw data
}
```

### 3. NBBO Columns (Missing 2 columns)

```python
'nbbo': {
    # ... existing columns ...
    
    # ADD THESE (if not already present):
    'national_bid': 'nationalbidprice',       # Verify actual column name
    'national_offer': 'nationalofferprice',   # Verify actual column name
}
```

---

## Implementation Plan

### Step 1: Add Missing Columns to COLUMN_MAPPING

**File:** `src/config/config.py`

**Action:** Add 10 missing columns to appropriate sections

**Estimated time:** 10 minutes

**Testing:** Run column schema tests to verify accessors work

---

### Step 2: Fix sweep_simulator.py (7+ instances)

**File:** `src/pipeline/sweep_simulator.py`  
**Function:** `generate_simulated_trades()` (lines 324-433)

**Current code:**
```python
# Line 324-326
sim_trades['dealsource'] = 2
sim_trades['dealsourcedecoded'] = 'Sweep'
sim_trades['exchangeinfo'] = 0

# Line 386-397
sim_trades['matchgroupid'] = sim_trades.groupby([...]).ngroup() + 1
sim_trades['nationalbidpricesnapshot'] = sim_trades['bid_price']
sim_trades['nationalofferpricesnapshot'] = sim_trades['offer_price']

# Line 410-421
sim_trades['passiveaggressive'] = sim_trades['side_match'].map({...})
sim_trades['sidedecoded'] = sim_trades[col.common.side].map({...})
sim_trades['participantid'] = 1

# Line 431-433
int_columns = ['EXCHANGE', 'sequence', 'tradetime', 'securitycode', 'orderid', ...]
```

**Proposed fix:**
```python
# Use col.trades.* accessors
sim_trades[col.trades.dealsource] = 2
sim_trades[col.trades.dealsource_decoded] = 'Sweep'
sim_trades[col.trades.exchange_info] = 0

sim_trades[col.trades.match_group_id] = sim_trades.groupby([...]).ngroup() + 1
sim_trades[col.trades.national_bid_snapshot] = sim_trades['bid_price']
sim_trades[col.trades.national_offer_snapshot] = sim_trades['offer_price']

sim_trades[col.trades.passive_aggressive] = sim_trades['side_match'].map({...})
sim_trades[col.trades.side_decoded] = sim_trades[col.common.side].map({...})
sim_trades[col.trades.participant_id] = 1

# Use col.* for int_columns list
int_columns = [
    col.trades.exchange,
    col.common.sequence,
    col.trades.trade_time,
    col.common.security_code,
    col.common.orderid,
    # ... rest of columns
]
```

**Lines to change:** ~15 lines  
**Estimated time:** 20 minutes  
**Testing:** Run sweep simulation test to verify output matches

---

### Step 3: Fix sweep_execution_analyzer.py (6+ instances)

**File:** `src/analysis/sweep_execution_analyzer.py`  
**Functions:** `_load_nbbo_data()`, `_load_trade_data()` (lines 62-127)

**Current code:**
```python
# Line 62-63
df['arrival_midpoint'] = (df['national_bid'] + df['national_offer']) / 2
df['arrival_spread'] = df['national_offer'] - df['national_bid']

# Line 90, 94-95
df['trade_midpoint'] = (df['nationalbidpricesnapshot'] + df['nationalofferpricesnapshot']) / 2

# Line 119, 122, 126-127
df = df[(df[col.common.orderid].isin(sweep_orderids)) & (df['passiveaggressive'] == 1)].copy()
```

**Proposed fix:**
```python
# Use col.nbbo.* or col.orders.* accessors
df['arrival_midpoint'] = (df[col.nbbo.national_bid] + df[col.nbbo.national_offer]) / 2
df['arrival_spread'] = df[col.nbbo.national_offer] - df[col.nbbo.national_bid]

# Use col.trades.* accessors
df['trade_midpoint'] = (df[col.trades.national_bid_snapshot] + df[col.trades.national_offer_snapshot]) / 2

# Use col.trades.passive_aggressive accessor
df = df[(df[col.common.orderid].isin(sweep_orderids)) & (df[col.trades.passive_aggressive] == 1)].copy()
```

**Lines to change:** ~6 lines  
**Estimated time:** 15 minutes  
**Testing:** Run sweep execution analysis test

---

### Step 4: Fix data_processor.py (1 instance)

**File:** `src/pipeline/data_processor.py`  
**Function:** `_filter_orders_with_valid_trades()` (line 214)

**Current code:**
```python
dealsources = order_trades['dealsource'].unique()
```

**Proposed fix:**
```python
dealsources = order_trades[col.trades.dealsource].unique()
```

**Lines to change:** 1 line  
**Estimated time:** 2 minutes  
**Testing:** Run data processor tests

---

## Testing Strategy

### Test 1: Column Mapping Validation
```bash
# Verify all new columns are accessible
python -c "
from config.column_schema import col
print('Testing new columns...')
print(f'dealsource: {col.trades.dealsource}')
print(f'passive_aggressive: {col.trades.passive_aggressive}')
print(f'national_bid_snapshot: {col.trades.national_bid_snapshot}')
print('✅ All columns accessible!')
"
```

### Test 2: Sweep Simulation
```bash
# Run sweep simulation with DRR
python src/main.py --orderbookid 110621 --date 20240905 --stage 2
```

**Expected:** 
- Simulation completes without errors
- Generated trades have correct column names
- Output files match expected format

### Test 3: Sweep Execution Analysis
```bash
# Run full pipeline to test analysis
python src/main.py --orderbookid 110621 --date 20240905
```

**Expected:**
- Analysis completes without KeyError
- Metrics calculated correctly
- Output reports generated

### Test 4: Regression Test
```bash
# Run with BHP to ensure no regression
python src/main.py --orderbookid 70616 --date 20240905 --stage 1
```

---

## Calculated Columns (Optional - Document Only)

These are intermediate/calculated columns created during processing. They don't need col.* accessors since they're not in the raw data, but should be documented:

**Intermediate Calculations:**
- `price_qty_product` - Used for VWAP calculation
- `arrival_midpoint` - Midpoint at order arrival
- `arrival_spread` - Spread at order arrival
- `trade_midpoint` - Midpoint at trade execution
- `vwap` - Volume-weighted average price
- `execution_duration_sec` - Time from first to last fill
- `cumulative_fill` - Running total of filled quantity
- `is_first_fill`, `is_last_fill` - Boolean flags for fill position
- `price_vs_order_price` - Price difference metric
- `match_value` - Trade matching value

**Recommendation:** Create a `CALCULATED_COLUMNS` constant in config.py for documentation:

```python
# Add to config.py
CALCULATED_COLUMNS = {
    'price_qty_product': 'Intermediate for VWAP calculation',
    'arrival_midpoint': 'NBBO midpoint at order arrival',
    'arrival_spread': 'NBBO spread at order arrival',
    'trade_midpoint': 'NBBO midpoint at trade execution',
    'vwap': 'Volume-weighted average price',
    'execution_duration_sec': 'Time from first to last fill (seconds)',
    'cumulative_fill': 'Running total of filled quantity',
    'is_first_fill': 'Boolean: Is this the first fill?',
    'is_last_fill': 'Boolean: Is this the last fill?',
    'price_vs_order_price': 'Difference between trade price and order price',
    'match_value': 'Value used for trade matching',
}
```

---

## Implementation Order

### Phase 3A: Add Missing Columns (30 minutes)
1. ✅ Add 7 trades columns to COLUMN_MAPPING
2. ✅ Add 2 orders columns (verify they exist first)
3. ✅ Add/verify 2 NBBO columns
4. ✅ Test column accessors work
5. ✅ Commit: "feat: Add missing columns to COLUMN_MAPPING"

### Phase 3B: Fix sweep_simulator.py (30 minutes)
1. ✅ Replace hardcoded 'dealsource', 'dealsourcedecoded', etc.
2. ✅ Replace hardcoded 'matchgroupid', 'nationalbidpricesnapshot', etc.
3. ✅ Replace hardcoded 'passiveaggressive', 'participantid', etc.
4. ✅ Replace int_columns list with col.* accessors
5. ✅ Test sweep simulation
6. ✅ Commit: "refactor: Replace hardcoded columns in sweep_simulator.py"

### Phase 3C: Fix sweep_execution_analyzer.py (20 minutes)
1. ✅ Replace 'national_bid', 'national_offer' with col.nbbo.*
2. ✅ Replace 'nationalbidpricesnapshot', 'nationalofferpricesnapshot' with col.trades.*
3. ✅ Replace 'passiveaggressive' with col.trades.passive_aggressive
4. ✅ Test sweep execution analysis
5. ✅ Commit: "refactor: Replace hardcoded columns in sweep_execution_analyzer.py"

### Phase 3D: Fix data_processor.py (5 minutes)
1. ✅ Replace 'dealsource' with col.trades.dealsource
2. ✅ Test data processor
3. ✅ Commit: "refactor: Replace hardcoded column in data_processor.py"

### Phase 3E: Documentation (15 minutes)
1. ✅ Add CALCULATED_COLUMNS constant to config.py
2. ✅ Update README or docs with column schema usage
3. ✅ Commit: "docs: Document calculated columns and finalize column schema"

---

## Total Estimated Time

| Phase | Time |
|-------|------|
| 3A: Add missing columns | 30 min |
| 3B: Fix sweep_simulator.py | 30 min |
| 3C: Fix sweep_execution_analyzer.py | 20 min |
| 3D: Fix data_processor.py | 5 min |
| 3E: Documentation | 15 min |
| **Total** | **100 minutes (~1.5 hours)** |

---

## Expected Outcomes

After completing Phase 3:

✅ **No hardcoded column names** in pipeline/analysis/aggregation code  
✅ **All column references** use col.* accessors  
✅ **10 new columns** added to COLUMN_MAPPING  
✅ **Schema-independent pipeline** - can adapt to any dataset schema by changing config  
✅ **Better maintainability** - single source of truth for column names  
✅ **IDE autocomplete** for column names  
✅ **No breaking changes** - all existing tests pass  

---

## Risk Assessment

**Low Risk:**
- Changes are localized to 3 files
- Column mapping additions are non-breaking
- All changes preserve existing functionality
- Extensive testing at each step

**Mitigation:**
- Commit after each phase for easy rollback
- Test after each file change
- Run full regression test at the end

---

## Questions for Review

1. **Do you want to include calculated columns in COLUMN_MAPPING?**
   - Pro: Consistency (everything uses col.*)
   - Con: They're not from raw data, just internal calculations
   - Recommendation: Document separately, don't add to COLUMN_MAPPING

2. **Should we verify 'national_bid'/'national_offer' exist in orders data?**
   - Need to check if these are in raw orders.csv or only in NBBO
   - May need to load from NBBO and merge into orders

3. **Implement in phases or all at once?**
   - Recommendation: Implement in phases (3A → 3B → 3C → 3D → 3E)
   - Each phase is independently testable

4. **Any other files with hardcoded columns I missed?**
   - Current analysis covered pipeline/, analysis/, aggregation/
   - Should we also check discovery/, utils/?

---

## Success Criteria

- ✅ All 10 missing columns added to COLUMN_MAPPING
- ✅ Zero hardcoded column strings in critical files
- ✅ All tests pass (DRR, BHP pipelines)
- ✅ Output files unchanged (byte-for-byte comparison)
- ✅ Documentation updated

**When this plan is complete, the branch goal "Remove hardcoded column names" will be 100% achieved!**

---

## Ready for Implementation?

This plan is ready for your review. Please confirm:
1. ✅ Approve the approach?
2. ✅ Approve the phases (3A → 3B → 3C → 3D → 3E)?
3. ✅ Any changes or additions needed?
4. ✅ Ready to start implementation?
