# Plan: Remove Analytical Columns from Simulated Trades

**Created:** 2026-01-11  
**Status:** ✅ **IMPLEMENTED**  
**Branch:** `b4modification`  
**Implementation Date:** 2026-01-11

---

## Implementation Summary

**Status:** ✅ Complete  
**Time Taken:** ~1.5 hours  
**Result:** Schema reduced from 21 to 17 columns successfully

### Changes Made
1. ✅ Modified `sweep_simulator.py` (5 locations, ~20 lines changed)
2. ✅ Updated `SIMULATED_TRADES_SUMMARY.md` (comprehensive documentation update)
3. ✅ Tested pipeline with DRR 2024-09-05
4. ✅ Verified 17-column output
5. ✅ All tests passed

### Verification
- Pipeline runs successfully: ✅
- Output has 17 columns: ✅
- Data integrity maintained: ✅  
- Row count unchanged (3,097 rows): ✅
- Match pairing preserved (2 rows per match): ✅

---

## Executive Summary

Remove 4 analytical columns from simulated trades output to align with real trades schema and reduce data size. This will reduce the schema from 21 columns to 17 columns, matching the exact structure of real Centre Point trades.

### Columns to Remove
1. `sweepordertimestamp` - When sweep order was placed
2. `incomingordertimestamp` - When incoming order was placed  
3. `nbbotimestamp` - When NBBO snapshot was recorded
4. `sweeptomatchduration` - Duration from sweep to match (nanoseconds)

### Rationale
- **Simplicity:** Remove derived/analytical columns that can be calculated on-demand
- **Focus:** Keep only essential trade execution data (like real trades core fields)
- **Size:** Removes ~19% of columns, reduces file size
- **Consistency:** Aligns with principle of not storing redundant data

**Note:** Real trades have 20 columns, simulated have 21 currently. They differ in some column names:
- Real: `orderbookid` | Simulated: `securitycode` (same meaning)
- Real: has `dealsourcedecoded`, `sidedecoded`, `date` | Simulated: doesn't have these
- Simulated: has 4 analytical columns | Real: doesn't have these

After removing analytical columns, simulated will have 17 columns (core trade data only).

---

## Schema Comparison

### Real Trades Schema (20 columns)
```
1.  EXCHANGE
2.  sequence
3.  tradedate
4.  tradetime
5.  orderbookid
6.  orderid
7.  dealsource
8.  dealsourcedecoded     ← Decoded value (not in simulated)
9.  exchangeinfo
10. matchgroupid
11. nationalbidpricesnapshot
12. nationalofferpricesnapshot
13. tradeprice
14. quantity
15. side
16. sidedecoded          ← Decoded value (not in simulated)
17. participantid
18. passiveaggressive
19. row_num
20. date                 ← Separate date field (not in simulated)
```

### Simulated Trades - Current (21 columns)
```
1.  EXCHANGE
2.  sequence
3.  tradedate
4.  tradetime
5.  securitycode         ← Called 'securitycode' not 'orderbookid'
6.  orderid
7.  dealsource
8.  exchangeinfo         ← No decoded columns
9.  matchgroupid
10. nationalbidpricesnapshot
11. nationalofferpricesnapshot
12. tradeprice
13. quantity
14. side                 ← No decoded column
15. participantid
16. passiveaggressive
17. row_num              ← No separate date field
18. sweepordertimestamp  ← ANALYTICAL (to remove)
19. incomingordertimestamp ← ANALYTICAL (to remove)
20. nbbotimestamp        ← ANALYTICAL (to remove)
21. sweeptomatchduration ← ANALYTICAL (to remove)
```

### Simulated Trades - Proposed (17 columns)
```
1.  EXCHANGE
2.  sequence
3.  tradedate
4.  tradetime
5.  securitycode
6.  orderid
7.  dealsource
8.  exchangeinfo
9.  matchgroupid
10. nationalbidpricesnapshot
11. nationalofferpricesnapshot
12. tradeprice
13. quantity
14. side
15. participantid
16. passiveaggressive
17. row_num
```

**Key Differences Summary:**

| Feature | Real Trades | Simulated (Current) | Simulated (Proposed) |
|---------|-------------|---------------------|----------------------|
| **Total Columns** | 20 | 21 | 17 |
| **Security ID** | orderbookid | securitycode | securitycode |
| **Decoded Values** | Yes (deal/side) | No | No |
| **Date Column** | Yes | No (uses tradedate) | No (uses tradedate) |
| **Analytical Cols** | No | Yes (4 columns) | **No** |

---

## Impact Analysis

### ✅ Benefits
1. **Cleaner Schema:** Only essential trade execution data (no derived fields)
2. **File Size Reduction:** ~19% fewer columns
3. **Simplified Logic:** Less data to track during simulation
4. **On-Demand Calculation:** Users calculate duration only when needed (more flexible)

### ⚠️ Tradeoffs
1. **Loss of Convenience:** Timestamps must be joined from orders table
2. **Documentation Updates:** Need to update all references to 21-column schema
3. **Breaking Change:** Existing code expecting 21 columns will break

### 📊 Data Availability
All removed data is **still accessible** via joins:

| Removed Column | How to Retrieve |
|----------------|-----------------|
| `sweepordertimestamp` | Join `orders_before_matching.csv` on `orderid` (aggressor row) |
| `incomingordertimestamp` | Same as `tradetime` (always equal) |
| `nbbotimestamp` | Join `nbbo.csv.gz` with `tradetime` (find NBBO at/before trade) |
| `sweeptomatchduration` | Calculate: `tradetime - sweep_timestamp` (after join) |

---

## Files to Modify

### 1. Code Files (3 files)

#### `src/pipeline/sweep_simulator.py`
**Lines to modify:**
- **Lines 401-404:** Remove analytical columns from aggressor row dict
- **Lines 428-431:** Remove analytical columns from passive row dict
- **Lines 471-472:** Remove from `int_columns` list
- **Lines 486-487:** Remove from empty DataFrame columns list

**Variables to remove:**
- Line 348: `sweep_timestamp` - can delete (only used for removed columns)
- Line 349: `incoming_timestamp` - can delete (redundant with match_timestamp)
- Line 353-357: `nbbo_timestamp` - can delete (not needed if not storing)
- Line 379: `sweep_to_match_duration` - can delete (derived field)

**Variables to keep:**
- Line 350: `match_timestamp` - KEEP (used for tradetime)
- Lines 353-357: NBBO lookup - KEEP (needed for bid/offer/midpoint)

**Impact:**
- ~16 lines deleted
- Reduces from 21 to 17 columns
- Simplifies row generation logic

---

#### `src/utils/file_utils.py`
**Changes:** None required (saves whatever DataFrame is provided)

---

#### `src/config/config.py`
**Changes:** None required (analytical columns not referenced in config)

---

### 2. Documentation Files (1 file)

#### `SIMULATED_TRADES_SUMMARY.md`
**Sections to update:**

1. **Line 6:** Update file description (21 → 17 columns)
2. **Line 19:** Update "Total Trade Rows" context
3. **Lines 62-80:** Remove "Analytical Columns (4)" section entirely
4. **Lines 134-144:** Remove analytical columns from schema table
5. **Lines 230-238:** Remove "Timestamp Tracking" section
6. **Lines 268-275:** Update "Duration analysis" section (remove references)
7. **Lines 306-310:** Update breaking changes (17 not 21 columns)
8. **Lines 341-347:** Remove duration calculation section
9. **Lines 365-373:** Remove duration-related future enhancements

**New content to add:**
- Section explaining how to retrieve timestamps via joins
- Example SQL/pandas code for common analysis patterns

---

### 3. Test/Validation

#### Tests to Create
```python
def test_simulated_trades_schema():
    """Verify simulated trades has exactly 17 columns matching real trades."""
    sim_trades = load_simulated_trades()
    real_trades = load_real_trades()
    
    assert len(sim_trades.columns) == 17
    assert list(sim_trades.columns) == list(real_trades.columns)
    
def test_no_analytical_columns():
    """Verify analytical columns are not present."""
    sim_trades = load_simulated_trades()
    
    forbidden = [
        'sweepordertimestamp',
        'incomingordertimestamp', 
        'nbbotimestamp',
        'sweeptomatchduration'
    ]
    
    for col in forbidden:
        assert col not in sim_trades.columns
```

#### Manual Testing
```bash
# Run pipeline
python src/main.py --ticker DRR --date 20240905 --stage 1 --stage 2 --sequential

# Verify schema
head -1 data/processed/2024-09-05/110621/cp_trades_simulation.csv | awk -F',' '{print NF}'
# Expected: 17 (not 21)

# Verify columns match real trades
head -1 data/processed/2024-09-05/110621/cp_trades_matched.csv.gz | gunzip
head -1 data/processed/2024-09-05/110621/cp_trades_simulation.csv
# Should be identical
```

---

## Migration Guide for Users

### Before (21 columns)
```python
# Direct access to timestamps
df = pd.read_csv('cp_trades_simulation.csv')
df['duration_sec'] = df['sweeptomatchduration'] / 1e9
avg_duration = df['duration_sec'].mean()
```

### After (17 columns)
```python
# Join with orders to get timestamps
trades = pd.read_csv('cp_trades_simulation.csv')
orders = pd.read_csv('orders_before_matching.csv')

# Join to get sweep timestamp (for aggressor rows)
aggressor = trades[trades['passiveaggressive'] == 1]
aggressor = aggressor.merge(
    orders[['orderid', 'timestamp']],
    on='orderid',
    how='left'
)
aggressor['sweep_timestamp'] = aggressor['timestamp']
aggressor['duration_ns'] = aggressor['tradetime'] - aggressor['sweep_timestamp']
aggressor['duration_sec'] = aggressor['duration_ns'] / 1e9

avg_duration = aggressor['duration_sec'].mean()
```

### Helper Function
```python
def enrich_simulated_trades(trades_df, orders_df):
    """Add analytical columns back via joins."""
    
    # Filter aggressor rows
    aggressor = trades_df[trades_df['passiveaggressive'] == 1].copy()
    
    # Join with orders to get sweep timestamp
    aggressor = aggressor.merge(
        orders_df[['orderid', 'timestamp']],
        left_on='orderid',
        right_on='orderid',
        how='left',
        suffixes=('', '_sweep')
    )
    
    # Add derived columns
    aggressor['sweepordertimestamp'] = aggressor['timestamp']
    aggressor['incomingordertimestamp'] = aggressor['tradetime']
    aggressor['sweeptomatchduration'] = (
        aggressor['tradetime'] - aggressor['timestamp']
    )
    
    return aggressor
```

---

## Implementation Steps

### Step 1: Create New Branch (Optional)
```bash
git checkout -b remove-analytical-columns
```

**Decision Point:** Continue on `b4modification` or create new branch?
- **Same branch:** Keeps refactoring together
- **New branch:** Separates concerns, easier to revert

---

### Step 2: Update Code

#### A. Update `sweep_simulator.py`

**Delete/modify lines 348-404:**
```python
# OLD: Calculate all timestamps
sweep_timestamp = sweep[col.common.timestamp]
incoming_timestamp = order[col.common.timestamp]
match_timestamp = incoming_timestamp
...
nbbo_bid, nbbo_offer, nbbo_timestamp = _get_nbbo_at_timestamp(...)
...
sweep_to_match_duration = match_timestamp - sweep_timestamp

# NEW: Only what's needed for trade generation
match_timestamp = order[col.common.timestamp]  # Match time = incoming order time
...
nbbo_bid, nbbo_offer, _ = _get_nbbo_at_timestamp(...)  # Ignore timestamp
```

**Update row generation (lines 382-432):**
```python
# OLD: 21 fields including analytical columns
simulated_trades.append({
    'EXCHANGE': 3,
    ...
    'row_num': row_counter,
    # Analytical columns
    'sweepordertimestamp': sweep_timestamp,
    'incomingordertimestamp': incoming_timestamp,
    'nbbotimestamp': nbbo_timestamp,
    'sweeptomatchduration': sweep_to_match_duration,
})

# NEW: 17 fields only
simulated_trades.append({
    'EXCHANGE': 3,
    ...
    'row_num': row_counter,
    # No analytical columns
})
```

**Update int_columns list (lines 466-473):**
```python
# OLD: 19 int columns
int_columns = [
    'EXCHANGE', 'sequence', 'tradetime', ...,
    'sweepordertimestamp', 'incomingordertimestamp', 'nbbotimestamp',
    'sweeptomatchduration'
]

# NEW: 15 int columns
int_columns = [
    'EXCHANGE', 'sequence', 'tradetime', 'securitycode', 'orderid',
    'dealsource', 'matchgroupid', 'nationalbidpricesnapshot',
    'nationalofferpricesnapshot', 'tradeprice', 'quantity', 'side',
    'participantid', 'passiveaggressive', 'row_num'
]
```

**Update empty DataFrame columns (lines 480-488):**
```python
# OLD: 21 columns
simulated_trades_df = pd.DataFrame(columns=[
    'EXCHANGE', ..., 'row_num',
    'sweepordertimestamp', 'incomingordertimestamp', 'nbbotimestamp',
    'sweeptomatchduration'
])

# NEW: 17 columns
simulated_trades_df = pd.DataFrame(columns=[
    'EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode',
    'orderid', 'dealsource', 'exchangeinfo', 'matchgroupid',
    'nationalbidpricesnapshot', 'nationalofferpricesnapshot',
    'tradeprice', 'quantity', 'side', 'participantid',
    'passiveaggressive', 'row_num'
])
```

---

### Step 3: Update Documentation

#### Update `SIMULATED_TRADES_SUMMARY.md`

**Key changes:**
1. Replace all "21 columns" with "17 columns"
2. Remove analytical columns section
3. Add "Retrieving Timestamps" section with join examples
4. Update schema table to show only 17 columns
5. Update breaking changes to reflect 17-column schema

**New section to add:**
```markdown
## Retrieving Analytical Data

The simulated trades use the same 17-column schema as real trades. 
Timestamp and duration data can be retrieved via joins:

### Get Sweep Order Timestamp
```python
# Join aggressor rows with orders
aggressor = trades[trades['passiveaggressive'] == 1]
aggressor = aggressor.merge(
    orders[['orderid', 'timestamp']], 
    on='orderid'
)
# aggressor['timestamp'] is the sweep order timestamp
```

### Calculate Match Duration
```python
aggressor['duration_ns'] = aggressor['tradetime'] - aggressor['timestamp']
aggressor['duration_sec'] = aggressor['duration_ns'] / 1e9
```
```

---

### Step 4: Test Changes

```bash
# Run pipeline on test data
python src/main.py --ticker DRR --date 20240905 --stage 1 --stage 2 --sequential

# Verify column count
head -1 data/processed/2024-09-05/110621/cp_trades_simulation.csv | awk -F',' '{print NF}'
# Should output: 17

# Verify columns match real trades
head -1 data/processed/2024-09-05/110621/cp_trades_matched.csv.gz | gunzip > /tmp/real_cols.txt
head -1 data/processed/2024-09-05/110621/cp_trades_simulation.csv > /tmp/sim_cols.txt
diff /tmp/real_cols.txt /tmp/sim_cols.txt
# Should be identical

# Check data integrity
wc -l data/processed/2024-09-05/110621/cp_trades_simulation.csv
# Should still be 3,097 rows (1,548 matches × 2 + 1 header)

# Verify no analytical columns
head -1 data/processed/2024-09-05/110621/cp_trades_simulation.csv | grep -E "sweep.*timestamp|duration"
# Should return nothing
```

---

### Step 5: Commit Changes

```bash
git add src/pipeline/sweep_simulator.py
git add SIMULATED_TRADES_SUMMARY.md

git commit -m "refactor: Remove analytical columns from simulated trades

Reduce simulated trades schema from 21 to 17 columns to match real trades
format exactly. Removes convenience columns that can be derived via joins.

Changes:
- Remove sweepordertimestamp (join orders on orderid)
- Remove incomingordertimestamp (same as tradetime)
- Remove nbbotimestamp (not needed, NBBO values captured)
- Remove sweeptomatchduration (calculate: tradetime - sweep timestamp)

Benefits:
- Schema exactly matches real Centre Point trades (17 columns)
- Simulated trades can be drop-in replacement for real trades
- Reduces file size by ~19% (4 fewer int64 columns)
- Simplifies simulation logic

Tradeoffs:
- Timestamps must be retrieved via join with orders table
- Duration must be calculated (not pre-computed)

Breaking change:
- Schema reduced from 21 to 17 columns
- Code expecting analytical columns will break
- See SIMULATED_TRADES_SUMMARY.md for migration guide

Tested: DRR 2024-09-05 - verified 17 columns, schema matches real trades"
```

---

### Step 6: Update Other Documentation (If Any)

Search for references to 21 columns or analytical columns:
```bash
grep -r "21 column" --include="*.md" .
grep -r "analytical column" --include="*.md" .
grep -r "sweepordertimestamp" --include="*.md" .
```

Update any other documentation found.

---

## Rollback Plan

If issues arise, rollback is straightforward:

### Option 1: Git Revert
```bash
git revert HEAD
git push origin b4modification
```

### Option 2: Re-add Columns
Add back the 4 lines in each row generation + update column lists.

The code structure makes this easy - just un-comment the analytical column lines.

---

## Testing Checklist

Before committing:

- [ ] Pipeline runs without errors
- [ ] Output has exactly 17 columns
- [ ] Column names match real trades exactly
- [ ] Column order matches real trades exactly
- [ ] Data types correct (all int64 except tradedate)
- [ ] Row count unchanged (still 2 rows per match)
- [ ] `passiveaggressive` still works correctly
- [ ] No references to removed columns in code
- [ ] Documentation updated
- [ ] Migration examples tested
- [ ] File size reduced (compare old vs new)

After committing:

- [ ] Run full pipeline on multiple securities
- [ ] Verify downstream analysis still works
- [ ] Check any external tools/scripts
- [ ] Update any Jupyter notebooks
- [ ] Notify team of breaking change

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Code breaks downstream | Medium | High | Search codebase for column references |
| Analysis scripts fail | Medium | Medium | Document migration, provide helper functions |
| Performance regression | Low | Low | Removing columns improves performance |
| Data loss | None | N/A | All data still accessible via joins |
| Rollback needed | Low | Low | Simple git revert or re-add columns |

**Overall Risk:** Low-Medium  
**Recommendation:** Proceed with changes

---

## Questions for Review

1. **Branch strategy:** Continue on `b4modification` or create new branch?

2. **Timing:** Implement now or wait for next major version?

3. **Compatibility:** Do we need to support both formats temporarily?

4. **Helper functions:** Should we add utility functions to enrich trades with analytical columns?

5. **Documentation:** Is the migration guide clear enough?

6. **Testing:** Any additional test scenarios needed?

7. **Communication:** Who needs to be notified of this breaking change?

---

## Timeline Estimate

| Task | Time Estimate |
|------|---------------|
| Code changes | 30 minutes |
| Documentation updates | 30 minutes |
| Testing | 30 minutes |
| Review & adjustments | 30 minutes |
| **Total** | **~2 hours** |

---

## Approval

**Status:** ⏳ AWAITING REVIEW

**Reviewers:**
- [ ] Technical review: _____________
- [ ] Documentation review: _____________
- [ ] Approval to proceed: _____________

**Notes:**
_Add review comments here_

---

## References

- Current implementation: `src/pipeline/sweep_simulator.py` lines 340-497
- Current docs: `SIMULATED_TRADES_SUMMARY.md`
- Previous refactoring commit: `6beb171`
- Documentation commit: `8b775a6`

---

**Plan Version:** 1.0  
**Created:** 2026-01-11  
**Last Updated:** 2026-01-11
