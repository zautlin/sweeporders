# Load Reference Data in load_partition_data() Plan - REVISED (All 4 Files)

## Current Situation

### Problem
`load_partition_data()` currently only loads:
- `orders_before_matching.csv`
- `orders_after_matching.csv`
- `last_execution_time.csv`

But it does NOT load:
- `session.csv.gz` (date-level) - **REQUIRED**
- `reference.csv.gz` (date-level) - **REQUIRED**
- `participants.csv.gz` (date-level) - **REQUIRED**
- `nbbo.csv.gz` (partition-level) - **REQUIRED**

This means when Stage 2/3 runs independently (without Stage 1), the reference data is not available.

### Current File Structure

```
data/processed/
├── 2024-09-04/
│   ├── session.csv.gz          # Date-level (all securities) ← LOAD THIS
│   ├── reference.csv.gz        # Date-level (all securities) ← LOAD THIS
│   ├── participants.csv.gz     # Date-level (all securities) ← LOAD THIS
│   └── 110621/                 # Partition (date + security)
│       ├── orders_before_matching.csv      ← Already loaded
│       ├── orders_after_matching.csv       ← Already loaded
│       ├── last_execution_time.csv         ← Already loaded
│       ├── nbbo.csv.gz         # Partition-level ← LOAD THIS
│       └── cp_orders_filtered.csv.gz
└── 2024-09-05/
    ├── participants.csv.gz     # Date-level ← LOAD THIS
    └── 110621/
        ├── orders_before_matching.csv
        ├── orders_after_matching.csv
        ├── last_execution_time.csv
        ├── nbbo.csv.gz         ← LOAD THIS
        └── cp_orders_filtered.csv.gz
```

**Files to Load (All 4):**
1. ✅ `nbbo.csv.gz` - Partition-level (date + security specific)
2. ✅ `session.csv.gz` - Date-level (trading hours, session info)
3. ✅ `reference.csv.gz` - Date-level (security metadata)
4. ✅ `participants.csv.gz` - Date-level (market participant info)

### Where load_partition_data() is Called

```python
# src/pipeline/partition_processor.py
def process_single_partition(partition_key, processed_dir, outputs_dir):
    """Process single partition: load data, simulate, calculate metrics."""
    
    # Load partition data
    partition_data = dp.load_partition_data(partition_key, processed_dir)
    
    # Currently partition_data contains:
    # - orders_before
    # - orders_after
    # - last_execution
    # 
    # Missing:
    # - session (date-level)
    # - reference (date-level)
    # - participants (date-level)
    # - nbbo (partition-level)
```

---

## Proposed Solution - Option 1 (Load All 4 Reference Files)

### Changes to `load_partition_data()`

**Add loading for 4 reference files:**

```python
def load_partition_data(partition_key, processed_dir):
    """Load all data for a partition including reference data."""
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    date_dir = Path(processed_dir) / date
    
    partition_data = {}
    
    # ===== PARTITION-LEVEL DATA (existing) =====
    
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
        partition_data['last_execution'] = pd.DataFrame(
            columns=['orderid', 'first_execution_time', 'last_execution_time']
        )
    
    # ===== REFERENCE DATA (NEW - 4 files) =====
    
    # Load NBBO (partition-specific)
    nbbo_file = partition_dir / "nbbo.csv.gz"
    if nbbo_file.exists():
        partition_data['nbbo'] = pd.read_csv(nbbo_file)
    else:
        partition_data['nbbo'] = pd.DataFrame()  # Empty DataFrame
    
    # Load session data (date-level)
    session_file = date_dir / "session.csv.gz"
    if session_file.exists():
        partition_data['session'] = pd.read_csv(session_file)
    else:
        partition_data['session'] = pd.DataFrame()  # Empty DataFrame
    
    # Load reference data (date-level)
    reference_file = date_dir / "reference.csv.gz"
    if reference_file.exists():
        partition_data['reference'] = pd.read_csv(reference_file)
    else:
        partition_data['reference'] = pd.DataFrame()  # Empty DataFrame
    
    # Load participants data (date-level)
    participants_file = date_dir / "participants.csv.gz"
    if participants_file.exists():
        partition_data['participants'] = pd.read_csv(participants_file)
    else:
        partition_data['participants'] = pd.DataFrame()  # Empty DataFrame
    
    return partition_data
```

**What Changed:**
- ✅ Added loading for `nbbo.csv.gz` (partition-level)
- ✅ Added loading for `session.csv.gz` (date-level)
- ✅ Added loading for `reference.csv.gz` (date-level)
- ✅ Added loading for `participants.csv.gz` (date-level)

**Benefits:**
- ✅ Complete reference data available for simulation/analysis
- ✅ Stage 2/3 can run independently without Stage 1
- ✅ No breaking changes (only additions)
- ✅ Matches Stage 1 data structure exactly

**Memory Impact:**
- NBBO: ~200 bytes (very small, 2 records typical)
- Session: ~300 bytes (9 records typical)
- Reference: ~600 bytes (1 record typical)
- Participants: ~300 bytes (5 records typical)
- **Total: ~1.4KB per partition** (negligible)

---

## Data Structure After Implementation

After changes, `partition_data` will contain:

```python
partition_data = {
    # Partition-level (existing)
    'orders_before': DataFrame,      # Orders at placement
    'orders_after': DataFrame,       # Orders after immediate matching
    'last_execution': DataFrame,     # Execution time tracking
    
    # Reference data (NEW - 4 files)
    'nbbo': DataFrame,               # NBBO quotes for this security (partition-level)
    'session': DataFrame,            # Session/trading hours info (date-level)
    'reference': DataFrame,          # Security reference data (date-level)
    'participants': DataFrame,       # Market participant info (date-level)
}
```

This matches the data structure needed by Stage 2 simulation and Stage 3 analysis.

---

## Implementation Steps

### Step 1: Update `load_partition_data()` in data_processor.py

Add loading logic for 4 files:
1. `nbbo.csv.gz` (partition-level: `processed_dir/date/security/nbbo.csv.gz`)
2. `session.csv.gz` (date-level: `processed_dir/date/session.csv.gz`)
3. `reference.csv.gz` (date-level: `processed_dir/date/reference.csv.gz`)
4. `participants.csv.gz` (date-level: `processed_dir/date/participants.csv.gz`)

**Location:** Lines 585-608 in `src/pipeline/data_processor.py`

### Step 2: Test with DRR

Test that reference data loads correctly when running stages independently:

```bash
# First ensure Stage 1 data exists on disk
python src/main.py --orderbookid 110621 --date 20240905 --stage 1

# Then test Stage 2 loads reference data correctly
python src/main.py --orderbookid 110621 --date 20240905 --stage 2
```

### Step 3: Verify Data Availability

Check that loaded data is correct:
- NBBO has correct number of records
- Session has trading hours
- Reference has security metadata
- Participants has market participant info

---

## Testing Checklist

- [ ] Run Stage 1 to generate processed files
- [ ] Run Stage 2 independently (verify no errors about missing data)
- [ ] Check that `partition_data['nbbo']` is populated
- [ ] Check that `partition_data['session']` is populated
- [ ] Check that `partition_data['reference']` is populated
- [ ] Check that `partition_data['participants']` is populated
- [ ] Run full pipeline (Stages 1-4) to ensure no regression
- [ ] Test with missing files (should create empty DataFrames)

---

## Expected Code Changes

**File:** `src/pipeline/data_processor.py`
**Function:** `load_partition_data()` (lines 585-608)
**Lines Added:** ~29 lines (4 reference files × ~7 lines each)
**Lines Removed:** 0 lines
**Breaking Changes:** None

---

## Performance Impact

**Memory per partition:**
- NBBO: ~200 bytes
- Session: ~300 bytes
- Reference: ~600 bytes
- Participants: ~300 bytes
- **Total: ~1.4KB** (negligible, < 0.002 MB)

**I/O per partition:**
- +4 file reads (all compressed, very small)
- Time: < 0.01 seconds per partition

**For 10 partitions:**
- Memory: ~14KB total
- Time: < 0.1 seconds total

**Conclusion:** Performance impact is negligible.

---

## Summary

**Problem:** `load_partition_data()` doesn't load session, reference, participants, or NBBO data.

**Solution:** Add loading logic for 4 reference files (nbbo, session, reference, participants).

**Implementation:** ~29 lines added to `load_partition_data()`.

**Impact:** 
- Memory: +1.4KB per partition (negligible)
- I/O: +4 tiny file reads
- Breaking changes: None

**Ready to implement immediately!** 🚀
