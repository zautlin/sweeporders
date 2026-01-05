# Main.py Refactoring Plan

## Overview
Refactor main.py to support command-line arguments while maintaining backward compatibility with config.py, and ensure all functions have concise 2-line max comments.

## Current State Analysis

### Issues Identified
1. ❌ No CLI argument support (--ticker, --date)
2. ❌ Hardcoded config file dependency
3. ❌ File naming pattern doesn't match requirement (needs `{ticker}_{date}_orders.csv`)
4. ❌ Verbose/multi-line docstrings throughout
5. ❌ Unmatched analyzer not clearly shown as final step
6. ✅ Pipeline structure is good (steps 1-14)
7. ✅ Parallel processing already supported

### Current File Pattern
```
data/raw/orders/drr_orders.csv
data/raw/trades/drr_trades.csv
```

### Required File Pattern
```
data/raw/orders/cba_20240905_orders.csv
data/raw/trades/cba_20240905_trades.csv
data/raw/nbbo/cba_20240905_nbbo.csv
data/raw/session/cba_20240905_session.csv
```

## Refactoring Plan

### Phase 1: Add CLI Argument Support ⭐

**Goal**: Support `ipython3 main.py --ticker cba --date 20240905`

**Changes**:
1. Add `argparse` for CLI argument parsing
2. Priority order: CLI args > config.py fallback
3. Auto-construct file paths from ticker/date pattern
4. Maintain backward compatibility (run without args uses config.py)

**New Function**:
```python
def parse_arguments():
    # Parse CLI arguments (--ticker, --date) with config.py fallback
    parser = argparse.ArgumentParser()
    parser.add_argument('--ticker', help='Ticker symbol (e.g., cba, drr)')
    parser.add_argument('--date', help='Date in YYYYMMDD format')
    args = parser.parse_args()
    return args
```

**File Path Resolution Logic**:
```python
def resolve_file_paths(ticker, date):
    # Build file paths using {ticker}_{date}_{filetype}.csv pattern or fallback to config
    if ticker and date:
        # CLI mode: construct paths
        base_path = PROJECT_ROOT / 'data/raw'
        files = {
            'orders': f"{base_path}/orders/{ticker}_{date}_orders.csv",
            'trades': f"{base_path}/trades/{ticker}_{date}_trades.csv",
            'nbbo': f"{base_path}/nbbo/{ticker}_{date}_nbbo.csv",
            'session': f"{base_path}/session/{ticker}_{date}_session.csv",
            # reference and participants might not have date suffix
        }
        return files
    else:
        # Config mode: use config.INPUT_FILES
        return config.INPUT_FILES
```

### Phase 2: Refactor Main Pipeline Functions ⭐

**Goal**: Clean up main() to be more modular and readable

**Changes**:
1. Extract file path resolution into separate function
2. Extract directory creation into separate function
3. Extract data extraction phase (steps 1-6) into function
4. Keep processing phase (steps 7-12) as-is
5. Make step 13-14 (analysis) more explicit

**New Structure**:
```python
def main():
    # Main pipeline orchestrator: CLI args -> data extraction -> processing -> analysis
    args = parse_arguments()
    input_files = resolve_file_paths(args.ticker, args.date)
    setup_directories()
    
    # Phase 1: Extract data (steps 1-6)
    data = extract_and_prepare_data(input_files)
    
    # Phase 2: Process partitions (steps 7-12)
    process_partitions_pipeline(data)
    
    # Phase 3: Final analysis (steps 13-14) - LAST STEPS
    run_final_analysis(data['partition_keys'])
    
    print_summary(...)
```

**New Functions**:
```python
def setup_directories():
    # Create output directories if they don't exist
    ...

def extract_and_prepare_data(input_files):
    # Run steps 1-6: extract orders, trades, reference data, states, execution times
    ...

def process_partitions_pipeline(data):
    # Run steps 7-12: simulation, metrics, comparison (parallel or sequential)
    ...

def run_final_analysis(partition_keys):
    # Run steps 13-14: sweep execution analysis + unmatched analyzer (FINAL STEP)
    ...
```

### Phase 3: Compress All Docstrings to 2 Lines Max ⭐

**Goal**: All function comments max 2 lines

**Current Examples** (verbose):
```python
def print_summary(orders_by_partition, trades_by_partition, execution_time):
    """Print pipeline execution summary."""
```

**New Format** (concise):
```python
def print_summary(orders_by_partition, trades_by_partition, execution_time):
    # Print pipeline execution summary with order/trade counts and timing
```

**Apply to ALL functions**:
- One-line purpose statement
- Optional second line for key details if needed
- Remove multi-paragraph docstrings

### Phase 4: Update Config.py for CLI Support

**Goal**: Make config.py work with dynamic file paths

**Changes**:
1. Add function to set input files dynamically
2. Keep defaults but allow override
3. Add validation for file existence

**New Function in config.py**:
```python
def set_input_files_from_cli(ticker, date, project_root=PROJECT_ROOT):
    # Override INPUT_FILES with CLI-derived paths for ticker and date
    ...
    
def validate_input_files(files):
    # Check if all required input files exist, raise FileNotFoundError if missing
    ...
```

### Phase 5: Ensure Unmatched Analyzer is Clear Final Step

**Goal**: Make it explicit that unmatched_analyzer is the last step

**Changes**:
```python
# Phase 3: Final analysis (steps 13-14) - LAST STEPS
print("\n" + "="*80)
print("FINAL ANALYSIS - STEPS 13-14")
print("="*80)

# Step 13: Sweep order execution analysis
sea.analyze_sweep_execution(config.PROCESSED_DIR, partition_keys)

# Step 14: Unmatched orders root cause analysis (FINAL STEP)
print("\n[Step 14/14] Running unmatched orders analysis (FINAL STEP)...")
uma.analyze_unmatched_orders(config.PROCESSED_DIR, partition_keys)

print("\n✓ Pipeline complete - All analysis finished")
```

## File Changes Summary

### Files to Modify
1. **src/main.py** - Major refactoring
   - Add argparse CLI support
   - Add file path resolution logic
   - Refactor into modular functions
   - Compress all docstrings to 2 lines
   - Make final step explicit

2. **src/config.py** - Minor updates
   - Add `set_input_files_from_cli()` function
   - Add `validate_input_files()` function
   - Keep existing defaults

3. **All src/*.py modules** (if needed)
   - Audit and compress docstrings to 2 lines max

## Backward Compatibility

### Existing Usage (No Changes Required)
```bash
# Still works - uses config.py
python src/main.py
```

### New CLI Usage
```bash
# New way - uses CLI args
ipython3 src/main.py --ticker cba --date 20240905

# Mix of both (CLI overrides config)
ipython3 src/main.py --ticker drr --date 20240904
```

## Testing Plan

### Test Cases
1. ✅ Run with config.py only (no args) - should work as before
2. ✅ Run with `--ticker cba --date 20240905` - should find cba_20240905_*.csv files
3. ✅ Run with `--ticker drr --date 20240904` - should find drr_20240904_*.csv files
4. ✅ Run with missing files - should show clear error message
5. ✅ Verify unmatched_analyzer runs as final step
6. ✅ Verify all output files are generated

## Implementation Order

1. **Phase 1** - CLI arguments (30 min)
2. **Phase 2** - Refactor main() structure (45 min)
3. **Phase 3** - Compress docstrings (30 min)
4. **Phase 4** - Update config.py (15 min)
5. **Phase 5** - Make final step explicit (10 min)
6. **Testing** - Run all test cases (20 min)

**Total Estimated Time**: ~2.5 hours

## Expected Results

### Before
```bash
python src/main.py  # Only way to run, uses hardcoded config
```

### After
```bash
# Method 1: Config file (backward compatible)
python src/main.py

# Method 2: CLI args (new)
ipython3 src/main.py --ticker cba --date 20240905

# Method 3: Interactive Python
ipython3
>>> %run src/main.py --ticker cba --date 20240905
```

### Output
```
CENTRE POINT SWEEP ORDER MATCHING PIPELINE
================================================================================
Mode: CLI (ticker=cba, date=20240905)
Input files:
  orders: data/raw/orders/cba_20240905_orders.csv ✓
  trades: data/raw/trades/cba_20240905_trades.csv ✓
...
[Steps 1-12 run]
================================================================================
FINAL ANALYSIS - STEPS 13-14
================================================================================
[13/14] Sweep order execution analysis...
[14/14] Unmatched orders analysis (FINAL STEP)...

✓ Pipeline complete - All analysis finished
```

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Break existing config.py usage | HIGH | Maintain backward compatibility, test both modes |
| File pattern mismatch | MEDIUM | Validate files exist before processing |
| Missing required files | MEDIUM | Add clear error messages with file paths |
| Docstring compression loses info | LOW | Keep essential info, remove redundancy |

## Approval Checklist

Before proceeding, please confirm:

- [ ] ✅ CLI argument pattern `--ticker cba --date 20240905` is correct
- [ ] ✅ File naming pattern `{ticker}_{date}_orders.csv` is correct
- [ ] ✅ All reference files (nbbo, session, etc.) follow same pattern
- [ ] ✅ Backward compatibility with config.py is acceptable
- [ ] ✅ 2-line max docstrings are acceptable
- [ ] ✅ Unmatched analyzer should remain as final step (step 14)
- [ ] ✅ OK to proceed with implementation

## Questions for You

1. **File patterns**: Do ALL files follow `{ticker}_{date}_{type}.csv` pattern, or only orders/trades?
   - If reference files (ob.csv, par.csv) don't have ticker/date, how should we handle them?

2. **Date format**: Confirm format is YYYYMMDD (e.g., 20240905) not YYYY-MM-DD?

3. **Required files**: Which files are mandatory vs optional?
   - orders.csv ✓ required
   - trades.csv ✓ required
   - nbbo.csv - required?
   - session.csv - required?
   - reference/ob.csv - required?
   - participants/par.csv - required?

4. **Error handling**: If files are missing, should we:
   - Stop immediately with error?
   - Continue with available files?
   - Prompt user for alternative path?

Please review and approve or suggest changes! 🚀
