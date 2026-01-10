# Pipeline Stages Refactoring Plan

## Current State Analysis

### File: `src/pipeline/pipeline_stages.py` (307 lines)

**Current functions:**
1. `extract_and_prepare_data()` - Stage 1 (lines 16-66) - 51 lines
2. `run_simulations_and_lob()` - Stage 2 (lines 69-125) - 57 lines
3. `run_per_security_analysis()` - Stage 3 (lines 128-158) - 31 lines
4. `run_cross_security_aggregation()` - Stage 4 (lines 161-193) - 33 lines
5. `_load_partition_keys_from_disk()` - Helper (lines 196-209) - 14 lines
6. `_execute_stage_1()` - Stage executor (lines 212-219) - 8 lines
7. `_execute_stage_2()` - Stage executor (lines 222-229) - 8 lines
8. `_execute_stage_3()` - Stage executor (lines 232-246) - 15 lines
9. `_process_single_security()` - Orchestrator (lines 249-285) - 37 lines
10. `execute_pipeline_stages()` - Main entry (lines 288-306) - 19 lines

---

## Issues Identified

### 1. **Long Functions with Multiple Responsibilities**
- `run_simulations_and_lob()` (57 lines) does too much:
  - Determines parallel vs sequential mode
  - Runs parallel processing
  - Runs 4 sequential steps (simulation, metrics, real trades, comparison)
  - Generates reports

### 2. **Hardcoded Dependencies**
- All functions directly reference `config.PROCESSED_DIR`, `config.OUTPUTS_DIR`, etc.
- Makes testing harder

### 3. **Mixed Abstraction Levels**
- `_execute_stage_1/2/3()` are thin wrappers
- `_process_single_security()` mixes orchestration with printing

### 4. **Inconsistent Error Handling**
- Stage 2 prints error but returns None
- Stage 3 returns empty list on error
- No consistent error handling pattern

### 5. **Duplicate Code**
- Success message printing repeated 3 times
- Partition key handling duplicated

---

## Refactoring Goals

1. ✅ **Keep existing function signatures unchanged** (external API preserved)
2. ✅ Break down large functions into smaller, focused helpers
3. ✅ Reduce duplication
4. ✅ Improve readability and maintainability
5. ✅ Keep all functionality intact

---

## Refactoring Plan

### Phase 1: Extract Sequential Processing Logic (Stage 2)

**Current:** `run_simulations_and_lob()` - 57 lines mixing parallel/sequential logic

**Refactor to:**
```python
def run_simulations_and_lob(data, enable_parallel):
    """Run simulations and create LOB states."""
    _print_stage_2_header(data, enable_parallel)
    
    if enable_parallel and len(data['partition_keys']) > 1:
        return _run_parallel_processing(data)
    else:
        return _run_sequential_processing(data)

def _print_stage_2_header(data, enable_parallel):
    """Print Stage 2 header with mode information."""
    print(f"\n{'='*80}")
    print(f"STAGE 2: SIMULATION & LOB STATES (Steps 7-12)")
    print(f"{'='*80}")
    
    if enable_parallel and len(data['partition_keys']) > 1:
        print(f"PARALLEL PROCESSING MODE")
        print(f"Using {config.MAX_PARALLEL_WORKERS} workers for {len(data['partition_keys'])} partitions")
    else:
        print(f"SEQUENTIAL PROCESSING MODE")
        print(f"Processing {len(data['partition_keys'])} partition(s) sequentially")

def _run_parallel_processing(data):
    """Execute Stage 2 in parallel mode."""
    return pp.process_partitions_parallel(
        data['partition_keys'],
        config.PROCESSED_DIR,
        config.OUTPUTS_DIR,
        config.MAX_PARALLEL_WORKERS
    )

def _run_sequential_processing(data):
    """Execute Stage 2 in sequential mode."""
    simulation_results = _run_simulation_and_metrics(data)
    _run_trade_comparison(data, simulation_results)
    return simulation_results

def _run_simulation_and_metrics(data):
    """Run simulation and calculate metrics (Steps 7-8)."""
    simulation_results_by_partition = pp.simulate_sweep_matching_sequential(
        data['orders'],
        data['order_states'],
        data['last_execution'],
        data['nbbo'],
        config.OUTPUTS_DIR
    )
    
    pp.calculate_simulated_metrics_sequential(
        data['orders'],
        simulation_results_by_partition,
        config.PROCESSED_DIR,
        config.OUTPUTS_DIR
    )
    
    return simulation_results_by_partition

def _run_trade_comparison(data, simulation_results):
    """Compare real vs simulated trades and generate reports (Steps 11-12)."""
    real_trade_metrics = mg.calculate_real_trade_metrics(
        data['trades'],
        data['orders'],
        config.PROCESSED_DIR
    )
    
    if real_trade_metrics:
        trade_comparison = mg.compare_real_vs_simulated_trades(
            real_trade_metrics,
            simulation_results,
            config.OUTPUTS_DIR
        )
        
        mg.generate_trade_comparison_reports(
            trade_comparison,
            config.OUTPUTS_DIR,
            include_accuracy_summary=False
        )
```

**Benefits:**
- Main function reduces from 57 → 7 lines
- Each helper has single responsibility
- Sequential processing logic separated from parallel
- Easier to test individual steps

---

### Phase 2: Simplify Stage Execution Logic

**Current:** `_process_single_security()` - 37 lines with mixed concerns

**Refactor to:**
```python
def _process_single_security(sec_info, runtime_config):
    """Process all stages for a single security."""
    security = sec_info['security']
    stages = runtime_config['stages']
    
    _print_security_header(security)
    
    # Execute Stage 1
    data, partition_keys_s1 = _execute_stage_1(sec_info, stages)
    if data is None and _should_run_stage(stages, 1):
        _print_stage_failure(security.orderbookid, 1, "no Centre Point orders found")
        return None, []
    _print_stage_success_if_ran(stages, 1, security.orderbookid)
    
    # Execute Stage 2
    _execute_stage_2(data, runtime_config, stages)
    _print_stage_success_if_ran(stages, 2, security.orderbookid)
    
    # Execute Stage 3
    partition_keys_s3 = _execute_stage_3(security, data, runtime_config, stages)
    _print_stage_success_if_ran(stages, 3, security.orderbookid)
    
    # Combine partition keys
    all_partition_keys = _combine_partition_keys(partition_keys_s1, partition_keys_s3)
    
    return data, all_partition_keys

def _print_security_header(security):
    """Print processing header for a security."""
    ticker_str = security.ticker.upper() if security.ticker else "Unknown"
    print(f"\n{'='*80}")
    print(f"Processing OrderbookID {security.orderbookid} ({ticker_str})")
    print(f"{'='*80}")

def _should_run_stage(stages, stage_num):
    """Check if a stage should be run."""
    return stages is None or stage_num in stages

def _print_stage_success_if_ran(stages, stage_num, orderbookid):
    """Print stage success message if stage was executed."""
    if _should_run_stage(stages, stage_num):
        print(f"\n✓ Stage {stage_num} complete for OrderbookID {orderbookid}")

def _print_stage_failure(orderbookid, stage_num, reason):
    """Print stage failure message."""
    print(f"\n✗ Stage {stage_num} failed for OrderbookID {orderbookid} - {reason}")

def _combine_partition_keys(*key_lists):
    """Combine multiple partition key lists."""
    combined = []
    for keys in key_lists:
        if keys:
            combined.extend(keys)
    return combined
```

**Benefits:**
- Eliminates duplicate stage success printing
- Extracts reusable utilities
- Main function becomes more readable
- Consistent patterns for stage execution

---

### Phase 3: Extract Stage 3 Analysis Steps

**Current:** `run_per_security_analysis()` - 31 lines with 3 analysis steps

**Refactor to:**
```python
def run_per_security_analysis(processed_dir, outputs_dir, partition_keys, stats_engine):
    """Run sweep execution and unmatched order analysis plus volume analysis."""
    _print_stage_3_header()
    
    _run_sweep_execution_analysis(processed_dir, outputs_dir, partition_keys, stats_engine)
    _run_unmatched_orders_analysis(processed_dir, outputs_dir, partition_keys)
    volume_summary = _run_volume_analysis(outputs_dir, partition_keys, stats_engine)
    
    print(f"\n✓ Stage 3 complete (per-security analysis + volume analysis)")
    return volume_summary

def _print_stage_3_header():
    """Print Stage 3 header."""
    print(f"\n{'='*80}")
    print(f"STAGE 3: PER-SECURITY ANALYSIS (Steps 13-14 + Volume Analysis)")
    print(f"{'='*80}")

def _run_sweep_execution_analysis(processed_dir, outputs_dir, partition_keys, stats_engine):
    """Run sweep order execution analysis (Step 13)."""
    print("\n[Step 13] Analyzing sweep order execution...")
    sea.analyze_sweep_execution(
        processed_dir,
        outputs_dir,
        partition_keys,
        stats_engine=stats_engine
    )

def _run_unmatched_orders_analysis(processed_dir, outputs_dir, partition_keys):
    """Run unmatched orders analysis (Step 14)."""
    print("\n[Step 14] Analyzing unmatched orders...")
    uma.analyze_unmatched_orders(
        processed_dir,
        outputs_dir,
        partition_keys
    )

def _run_volume_analysis(outputs_dir, partition_keys, stats_engine):
    """Run volume-based analysis."""
    print("\n[Volume Analysis] Analyzing execution by order volume...")
    return va.analyze_by_volume(
        outputs_dir,
        partition_keys,
        method='quartile',
        stats_engine=stats_engine
    )
```

**Benefits:**
- Each analysis step is isolated
- Easier to add/remove analysis steps
- Clear step boundaries

---

### Phase 4: Extract Stage 4 Aggregation Steps

**Current:** `run_cross_security_aggregation()` - 33 lines with 4 steps

**Refactor to:**
```python
def run_cross_security_aggregation(runtime_config):
    """Aggregate sweep and volume results across all securities."""
    _print_stage_4_header()
    
    stats_engine = runtime_config.get('stats_engine')
    
    # Step 1-2: Aggregate and save sweep results
    aggregated_df = _aggregate_sweep_results()
    if aggregated_df is None:
        return False
    
    _save_sweep_results(aggregated_df)
    
    # Step 3: Statistical analysis
    _run_statistical_analysis(stats_engine)
    
    # Step 4: Volume aggregation
    _aggregate_volume_results(stats_engine)
    
    print("\n✓ Stage 4 complete (cross-security aggregation + volume analysis)")
    return True

def _print_stage_4_header():
    """Print Stage 4 header."""
    print(f"\n{'='*80}")
    print(f"STAGE 4: CROSS-SECURITY AGGREGATION")
    print(f"{'='*80}")
    print("\n[Stage 4] Aggregating results across all securities...")

def _aggregate_sweep_results():
    """Merge all sweep_order_comparison_detailed.csv files."""
    print("  Step 1: Merging sweep_order_comparison_detailed.csv files...")
    aggregated_df = agg.aggregate_results(config.OUTPUTS_DIR)
    
    if aggregated_df is None:
        print("  ✗ No results found to aggregate")
    
    return aggregated_df

def _save_sweep_results(aggregated_df):
    """Save aggregated sweep results."""
    print("  Step 2: Saving aggregated dataset...")
    output_path = config.AGGREGATED_DIR + '/aggregated_sweep_comparison.csv'
    agg.save_aggregated_results(aggregated_df, output_path)

def _run_statistical_analysis(stats_engine):
    """Run statistical analysis on aggregated data."""
    print("  Step 3: Running statistical analysis...")
    analyze.main(stats_engine)

def _aggregate_volume_results(stats_engine):
    """Aggregate volume analysis across securities."""
    print("  Step 4: Aggregating volume analysis across securities...")
    try:
        vol_agg.main(stats_engine)
        print("  ✓ Volume analysis aggregation complete")
    except Exception as e:
        print(f"  ⚠ Volume analysis aggregation skipped: {str(e)}")
```

**Benefits:**
- Clear step boundaries
- Each step is independently testable
- Error handling isolated to volume aggregation

---

## Summary of Changes

### Functions to Keep (External API - NO CHANGES):
1. ✅ `extract_and_prepare_data(input_files)` - Keep signature
2. ✅ `run_simulations_and_lob(data, enable_parallel)` - Keep signature
3. ✅ `run_per_security_analysis(processed_dir, outputs_dir, partition_keys, stats_engine)` - Keep signature
4. ✅ `run_cross_security_aggregation(runtime_config)` - Keep signature
5. ✅ `execute_pipeline_stages(runtime_config)` - Keep signature
6. ✅ `_load_partition_keys_from_disk(security, processed_dir)` - Keep as-is
7. ✅ `_execute_stage_1(sec_info, stages)` - Keep as-is
8. ✅ `_execute_stage_2(data, runtime_config, stages)` - Keep as-is
9. ✅ `_execute_stage_3(security, data, runtime_config, stages)` - Keep as-is
10. ✅ `_process_single_security(sec_info, runtime_config)` - Keep signature

### New Helper Functions to Add:
**Stage 2 (5 new functions):**
- `_print_stage_2_header(data, enable_parallel)`
- `_run_parallel_processing(data)`
- `_run_sequential_processing(data)`
- `_run_simulation_and_metrics(data)`
- `_run_trade_comparison(data, simulation_results)`

**Stage 3 (4 new functions):**
- `_print_stage_3_header()`
- `_run_sweep_execution_analysis(processed_dir, outputs_dir, partition_keys, stats_engine)`
- `_run_unmatched_orders_analysis(processed_dir, outputs_dir, partition_keys)`
- `_run_volume_analysis(outputs_dir, partition_keys, stats_engine)`

**Stage 4 (5 new functions):**
- `_print_stage_4_header()`
- `_aggregate_sweep_results()`
- `_save_sweep_results(aggregated_df)`
- `_run_statistical_analysis(stats_engine)`
- `_aggregate_volume_results(stats_engine)`

**Orchestration helpers (5 new functions):**
- `_print_security_header(security)`
- `_should_run_stage(stages, stage_num)`
- `_print_stage_success_if_ran(stages, stage_num, orderbookid)`
- `_print_stage_failure(orderbookid, stage_num, reason)`
- `_combine_partition_keys(*key_lists)`

### Total: 19 new helper functions

---

## Expected Results

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Total lines | 307 | ~450 | +143 (more granular) |
| Largest function | 57 lines | ~20 lines | -65% |
| Functions | 10 | 29 | +19 helpers |
| Avg function size | 30 lines | 15 lines | -50% |

---

## Benefits

1. ✅ **Single Responsibility** - Each function does one thing
2. ✅ **Testability** - Smaller functions easier to test
3. ✅ **Readability** - Clear function names describe intent
4. ✅ **Maintainability** - Changes isolated to specific helpers
5. ✅ **No Breaking Changes** - External API preserved
6. ✅ **DRY** - Eliminates duplicate code (stage success printing, etc.)

---

## Testing Strategy

After refactoring:
1. Run DRR test (stage 1)
2. Run BHP test (stage 1)
3. Run full pipeline test (all stages)
4. Verify output files unchanged
5. Verify all print statements appear correctly

---

## Questions for Approval

1. **Do you approve this refactoring approach?**
2. **Should we add even more granularity (smaller functions)?**
3. **Any specific naming conventions you prefer?**
4. **Should we also extract config references into parameters?**

---

## Implementation Order

If approved, implement in this order:
1. Phase 1: Stage 2 helpers (test after)
2. Phase 2: Orchestration helpers (test after)
3. Phase 3: Stage 3 helpers (test after)
4. Phase 4: Stage 4 helpers (test after)
5. Final integration test

Each phase can be committed separately for easier review.
