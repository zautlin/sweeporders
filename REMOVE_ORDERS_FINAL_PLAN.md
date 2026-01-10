# Plan: Remove Unused `orders_final_state.csv`

## Executive Summary

`orders_final_state.csv` is created but never used in the simulation pipeline. It represents the final state of orders at end-of-day, but the simulation only needs `orders_before` (placement) and `orders_after` (after immediate matching).

**Recommendation:** Remove creation and loading of `orders_final_state.csv` to simplify the codebase.

---

## Impact Analysis

### Files That Will Be Modified

1. **`src/pipeline/data_processor.py`**
   - `get_orders_state()` - Remove orders_final creation/saving
   - `load_partition_data()` - Remove orders_final loading

### Current Usage

**Created:**
- Line 509: `orders_final_list = []` - Initialize list
- Line 534: `orders_final_list.append(records_at_max_ts.iloc[-1])` - Populate
- Line 538: `orders_final = pd.DataFrame(orders_final_list)` - Create DataFrame
- Line 543: `'final': orders_final` - Add to dict (USED in return value)
- Line 552: `final_file = partition_dir / "orders_final_state.csv"` - Define path
- Line 556: `orders_final.to_csv(final_file, index=False)` - Save to processed/
- Line 559: `debug_file = debug_dir / f"orders_final_{date}_{orderbookid}.csv"` - Debug path
- Line 560: `orders_final.to_csv(debug_file, index=False)` - Save to debug/
- Line 562: `print(f"... {len(orders_final):,} final")` - Print count

**Loaded:**
- Line 620-623: Loads `orders_final_state.csv` into `partition_data['orders_final']`

**Used:**
- ❌ NOWHERE - Never accessed downstream

---

## Detailed Plan

### Phase 1: Remove from `get_orders_state()`

**File:** `src/pipeline/data_processor.py` (lines 484-562)

**Changes:**

1. **Remove orders_final creation (lines 509, 534, 538)**
   ```python
   # REMOVE these lines:
   orders_final_list = []
   orders_final_list.append(records_at_max_ts.iloc[-1])
   orders_final = pd.DataFrame(orders_final_list).reset_index(drop=True)
   ```

2. **Remove from return dict (line 543)**
   ```python
   # BEFORE:
   order_states_by_partition[partition_key] = {
       'before': orders_before,
       'after': orders_after,
       'final': orders_final  # REMOVE THIS LINE
   }
   
   # AFTER:
   order_states_by_partition[partition_key] = {
       'before': orders_before,
       'after': orders_after
   }
   ```

3. **Remove file saving (lines 552, 556, 559-560)**
   ```python
   # REMOVE these lines:
   final_file = partition_dir / "orders_final_state.csv"
   orders_final.to_csv(final_file, index=False)
   debug_file = debug_dir / f"orders_final_{date}_{security_code}.csv"
   orders_final.to_csv(debug_file, index=False)
   ```

4. **Update print statement (line 562)**
   ```python
   # BEFORE:
   print(f"  {partition_key}: {len(orders_before):,} before, {len(orders_after):,} after, {len(orders_final):,} final")
   
   # AFTER:
   print(f"  {partition_key}: {len(orders_before):,} before, {len(orders_after):,} after")
   ```

5. **Remove max_timestamps calculation (lines 505, 513, 527-531, 534)**
   ```python
   # REMOVE:
   max_timestamps = orders_sorted.groupby(col.common.orderid)[col.common.timestamp].max()
   
   # And remove all code that uses max_timestamps:
   max_ts = max_timestamps[order_id]
   records_at_max_ts = orders_sorted[...]
   orders_final_list.append(records_at_max_ts.iloc[-1])
   ```

**Simplified function after removal:**

```python
def get_orders_state(orders_by_partition, processed_dir):
    """Extract before/after order states per partition."""
    print(f"\n[5/11] Extracting order states...")
    
    order_states_by_partition = {}
    
    for partition_key, orders_df in orders_by_partition.items():
        if len(orders_df) == 0:
            continue
        
        date, security_code = partition_key.split('/')
        
        # Sort by timestamp, then sequence (ascending)
        orders_sorted = orders_df.sort_values([col.common.timestamp, col.common.sequence])
        
        # Get minimum timestamps per order
        min_timestamps = orders_sorted.groupby(col.common.orderid)[col.common.timestamp].min()
        
        orders_before_list = []
        orders_after_list = []
        
        for order_id in min_timestamps.index:
            min_ts = min_timestamps[order_id]
            
            # Filter to records at minimum timestamp for this order
            records_at_min_ts = orders_sorted[
                (orders_sorted[col.common.orderid] == order_id) & 
                (orders_sorted[col.common.timestamp] == min_ts)
            ]
            
            # BEFORE: First record at min timestamp (min sequence)
            orders_before_list.append(records_at_min_ts.iloc[0])
            
            # AFTER: Last record at min timestamp (max sequence)
            orders_after_list.append(records_at_min_ts.iloc[-1])
        
        orders_before = pd.DataFrame(orders_before_list).reset_index(drop=True)
        orders_after = pd.DataFrame(orders_after_list).reset_index(drop=True)
        
        order_states_by_partition[partition_key] = {
            'before': orders_before,
            'after': orders_after
        }
        
        # Save to processed directory
        partition_dir = Path(processed_dir) / date / security_code
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        before_file = partition_dir / "orders_before_matching.csv"
        after_file = partition_dir / "orders_after_matching.csv"
        
        orders_before.to_csv(before_file, index=False)
        orders_after.to_csv(after_file, index=False)
        
        print(f"  {partition_key}: {len(orders_before):,} before, {len(orders_after):,} after")
    
    return order_states_by_partition
```

**Lines removed:** ~25 lines
**Performance improvement:** No longer calculates max_timestamps (saves groupby operation)

---

### Phase 2: Remove from `load_partition_data()`

**File:** `src/pipeline/data_processor.py` (lines 620-623)

**Changes:**

```python
# REMOVE these lines:
# Load orders_final_state
final_file = partition_dir / "orders_final_state.csv"
if final_file.exists():
    partition_data['orders_final'] = pd.read_csv(final_file)
```

**Simplified function after removal:**

```python
def load_partition_data(partition_key, processed_dir):
    """Load all necessary data for a partition for simulation."""
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    
    partition_data = {}
    
    # Load orders_before_matching
    before_file = partition_dir / "orders_before_matching.csv"
    if before_file.exists():
        partition_data['orders_before'] = pd.read_csv(before_file)
    
    # Load orders_after_matching
    after_file = partition_dir / "orders_after_matching.csv"
    if after_file.exists():
        partition_data['orders_after'] = pd.read_csv(after_file)
    
    # Load last_execution_time
    exec_file = partition_dir / "last_execution_time.csv"
    if exec_file.exists():
        partition_data['last_execution'] = pd.read_csv(exec_file)
    else:
        # Create empty DataFrame if no execution times
        partition_data['last_execution'] = pd.DataFrame(columns=['orderid', 'first_execution_time', 'last_execution_time'])
    
    return partition_data
```

**Lines removed:** 4 lines

---

### Phase 3: Clean Up Existing Files (Optional)

**Should we delete existing `orders_final_state.csv` files?**

**Options:**

**Option A: Leave existing files (Recommended)**
- ✅ No data loss
- ✅ Can be used for manual debugging
- ✅ No need to regenerate if we want them back
- ❌ Takes up disk space

**Option B: Delete existing files**
- ✅ Clean up disk space
- ✅ Consistent with code changes
- ❌ Permanent data loss
- ❌ Would need to re-run Stage 1 to regenerate

**Recommendation:** Leave existing files (Option A), but stop creating new ones.

---

## Testing Plan

### Test 1: Stage 1 Only (DRR)
```bash
# Clean processed directory
rm -rf data/processed/2024-09-04/110621
rm -rf data/processed/2024-09-05/110621

# Run Stage 1
python src/main.py --orderbookid 110621 --date 20240905 --stage 1
```

**Verify:**
- ✅ `orders_before_matching.csv` created
- ✅ `orders_after_matching.csv` created
- ✅ `orders_final_state.csv` NOT created (should not exist)
- ✅ Print statement shows "before, after" (no "final")
- ✅ No errors

### Test 2: Stage 1 + Stage 2 (DRR)
```bash
# Run Stages 1-2
python src/main.py --orderbookid 110621 --date 20240905 --stage 1 --stage 2
```

**Verify:**
- ✅ Stage 1 completes without errors
- ✅ Stage 2 loads partition_data successfully
- ✅ Simulation runs without errors
- ✅ Output files created correctly

### Test 3: Full Pipeline (BHP)
```bash
# Clean processed directory
rm -rf data/processed/2024-09-05/70616

# Run full pipeline
python src/main.py --orderbookid 70616 --date 20240905
```

**Verify:**
- ✅ All stages complete successfully
- ✅ No references to orders_final
- ✅ Simulation results unchanged

### Test 4: Check Debug Directory
```bash
# Verify debug files NOT created
ls data/debug/orders_final_*
```

**Expected:** Files should not exist (or old files remain but no new ones)

---

## Impact Assessment

### Positive Impacts ✅

1. **Simpler Code**
   - Removes ~30 lines of unused code
   - Clearer purpose (only before/after states)
   - Less cognitive load

2. **Performance**
   - Saves one groupby operation (`max_timestamps`)
   - Saves file I/O (2 writes, 1 read per partition)
   - Faster Stage 1 execution

3. **Disk Space**
   - No longer creates `orders_final_state.csv` (saves ~0.5MB per partition)
   - No longer creates debug files in `data/debug/`

4. **Maintenance**
   - Less code to maintain
   - Clearer data flow

### Potential Risks ⚠️

1. **Loss of Debug Information**
   - Can no longer see end-of-day order states
   - **Mitigation:** Keep existing files, document how to regenerate if needed

2. **Breaking Change for Manual Scripts**
   - If anyone manually reads `orders_final_state.csv` in notebooks/scripts
   - **Mitigation:** Check for any references in scripts/ or notebooks/

3. **Future Use Cases**
   - Might want end-of-day states for reconciliation later
   - **Mitigation:** Easy to add back if needed (code is in git history)

---

## Rollback Plan

If we discover `orders_final` is actually needed:

1. **Revert commit** containing these changes
2. **Or manually restore code** from this plan document
3. **Re-run Stage 1** for affected dates/securities

The code is straightforward to restore since we're documenting the exact changes.

---

## Pre-Flight Checklist

Before implementing:

- [ ] Search entire codebase for "orders_final" references
- [ ] Search notebooks/ for "orders_final_state.csv"
- [ ] Search scripts/ for "orders_final"
- [ ] Check if any analysis code depends on debug files
- [ ] Verify no other modules import from data_processor and use orders_final
- [ ] Review with team (if applicable)

---

## Implementation Steps

### Step 1: Code Search
```bash
# Search for any references to orders_final
grep -r "orders_final" src/ --include="*.py"
grep -r "orders_final_state" src/ --include="*.py"
grep -r "orders_final" notebooks/ --include="*.ipynb" 2>/dev/null || echo "No notebooks dir"
grep -r "orders_final" scripts/ --include="*.py" 2>/dev/null || echo "No scripts dir"
```

### Step 2: Make Changes
1. Edit `src/pipeline/data_processor.py`
2. Remove orders_final logic from `get_orders_state()`
3. Remove orders_final loading from `load_partition_data()`

### Step 3: Test
1. Run Test 1 (Stage 1 only)
2. Run Test 2 (Stages 1-2)
3. Run Test 3 (Full pipeline)
4. Verify no errors, output unchanged

### Step 4: Commit
```bash
git add src/pipeline/data_processor.py
git commit -m "refactor: Remove unused orders_final_state.csv

- Remove orders_final creation from get_orders_state()
- Remove orders_final loading from load_partition_data()
- orders_final was never used in simulation pipeline
- Only before/after states are needed for matching
- Saves ~30 lines of code and improves performance
- Reduces disk I/O (2 writes + 1 read per partition)

Tested with DRR and BHP, all stages pass."
```

---

## Questions for Review

1. **Should we keep or delete existing `orders_final_state.csv` files?**
   - Recommendation: Keep (Option A)

2. **Should we add a comment explaining why we only have before/after?**
   - Recommendation: Yes, add docstring update

3. **Should we update any documentation?**
   - Check if README mentions orders_final_state.csv

4. **Should we search notebooks/scripts for usage first?**
   - Recommendation: Yes, run pre-flight checklist

---

## Summary

**Total Lines Removed:** ~30 lines
**Files Modified:** 1 file (`data_processor.py`)
**Risk Level:** Low (unused code removal)
**Testing Required:** Stage 1 + Stage 2 tests
**Estimated Time:** 15 minutes coding + 20 minutes testing

**Recommendation:** Proceed with removal after completing pre-flight checklist.
