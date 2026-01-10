# Main.py Refactoring Plan

## Current Analysis (627 lines)

### Functions Overview:
1. **parse_arguments()** (lines 17-63, 47 lines) - CLI parsing
2. **get_runtime_config()** (lines 66-216, 151 lines) - Config building with discovery logic
3. **setup_directories()** (lines 218-222, 5 lines) - Directory creation
4. **extract_and_prepare_data()** (lines 224-280, 57 lines) - Stage 1
5. **run_simulations_and_lob()** (lines 282-346, 65 lines) - Stage 2
6. **print_summary()** (lines 348-399, 52 lines) - Summary printing
7. **run_per_security_analysis()** (lines 401-436, 36 lines) - Stage 3
8. **run_cross_security_aggregation()** (lines 438-481, 44 lines) - Stage 4
9. **main()** (lines 483-623, 141 lines) - Main orchestrator

### Issues Identified:

#### 1. get_runtime_config() - TOO COMPLEX (151 lines)
- Handles CLI arg resolution
- Handles discovery logic
- Handles file validation
- Handles statistics engine creation
- Has deeply nested if/else logic for security selection
- Contains exit() calls (should be at top level)

#### 2. main() - TOO COMPLEX (141 lines)
- Does argument parsing
- Does config building
- Does directory setup
- Does header printing
- Has complex stage execution logic
- Has data loading logic mixed with execution
- Handles multiple securities in a loop
- Handles partition key discovery
- Has summary printing at the end

#### 3. Multiple Single-Purpose Functions Need Comments
- parse_arguments() has 2-line docstring (GOOD)
- setup_directories() has 1-line docstring (GOOD)
- All stage functions have long docstrings (NEED REDUCTION)

#### 4. Inconsistent Docstring Styles
- Some have multi-line detailed descriptions
- Some reference step numbers
- Should be max 1 line per requirement

## Refactoring Plan

### Phase 1: Extract Security Selection Logic (from get_runtime_config)

**New Functions:**
1. `_select_securities_from_args(args, discovery, date)` - Pure security selection logic
2. `_handle_list_operations(args, discovery)` - List dates/securities operations
3. `_resolve_stages_to_run(args)` - Stage resolution
4. `_build_security_file_mappings(securities, date, stages)` - File mapping
5. `_determine_parallel_mode(args)` - Parallel config resolution
6. `_create_stats_engine(args)` - Stats engine creation

**Result:** get_runtime_config() becomes ~40 lines (orchestrator only)

### Phase 2: Extract Main() Orchestration Logic

**New Functions:**
1. `_print_pipeline_header(runtime_config)` - Print initial header
2. `_print_stage_plan(stages)` - Print which stages will run
3. `_process_single_security(sec_info, runtime_config, stages)` - Process one security
4. `_load_partition_keys_from_disk(security, processed_dir)` - Partition discovery
5. `_run_stage_1(input_files)` - Stage 1 wrapper
6. `_run_stage_2(data, enable_parallel)` - Stage 2 wrapper
7. `_run_stage_3(partition_keys, processed_dir, outputs_dir, stats_engine)` - Stage 3 wrapper
8. `_run_stage_4(runtime_config)` - Stage 4 wrapper

**Result:** main() becomes ~60 lines (orchestrator only)

### Phase 3: Simplify Docstrings

**Rule:** Max 1 line per function
- Remove "Uses parallel..." explanations → code is self-explanatory
- Remove "Steps 1-6" → implementation detail
- Remove "(NEW)" markers → not needed in refactored code
- Keep only what function DOES, not how

### Phase 4: Consolidate Print Functions

**New Functions:**
1. `_print_statistics_tier(stats_engine)` - Stats tier info
2. `_print_security_info(securities)` - Security details
3. `_print_system_config()` - System configuration

**Result:** Reduce duplication in print logic

### Phase 5: Stage Function Simplification

**Current stage functions are good but:**
- Reduce docstrings to 1 line
- Keep implementation as-is (working correctly)

## Detailed Breakdown

### File Structure After Refactoring:

```
# Imports (lines 1-15)

# === CLI ARGUMENT PARSING ===
def parse_arguments()  # Keep as-is, good

# === CONFIGURATION BUILDING ===
def _handle_list_operations(args, discovery)  # NEW
def _resolve_stages_to_run(args)  # NEW
def _determine_parallel_mode(args)  # NEW
def _create_stats_engine(args)  # NEW
def _select_securities_from_args(args, discovery, date)  # NEW
def _build_security_file_mappings(securities, date, stages)  # NEW
def get_runtime_config(args)  # SIMPLIFIED ORCHESTRATOR

# === DIRECTORY SETUP ===
def setup_directories()  # Keep as-is, good

# === STAGE EXECUTION ===
def extract_and_prepare_data(input_files)  # SIMPLIFIED DOCSTRING
def run_simulations_and_lob(data, enable_parallel)  # SIMPLIFIED DOCSTRING
def run_per_security_analysis(processed_dir, outputs_dir, partition_keys, stats_engine)  # SIMPLIFIED DOCSTRING
def run_cross_security_aggregation(runtime_config)  # SIMPLIFIED DOCSTRING

# === STAGE WRAPPERS (for main loop) ===
def _run_stage_1(input_files)  # NEW
def _run_stage_2(data, enable_parallel)  # NEW  
def _run_stage_3(partition_keys, processed_dir, outputs_dir, stats_engine)  # NEW
def _run_stage_4(runtime_config)  # NEW

# === PARTITION LOADING ===
def _load_partition_keys_from_disk(security, processed_dir)  # NEW

# === SECURITY PROCESSING ===
def _process_single_security(sec_info, runtime_config, stages)  # NEW

# === OUTPUT PRINTING ===
def _print_statistics_tier(stats_engine)  # NEW
def _print_security_info(securities)  # NEW
def _print_system_config()  # NEW
def _print_pipeline_header(runtime_config)  # NEW
def _print_stage_plan(stages)  # NEW
def print_summary(data, runtime_config, execution_time)  # SIMPLIFIED

# === MAIN ORCHESTRATOR ===
def main()  # SIMPLIFIED TO ~60 LINES

if __name__ == '__main__':
    main()
```

## Expected Line Counts After Refactoring:

| Function | Before | After | Change |
|----------|--------|-------|--------|
| parse_arguments | 47 | 47 | 0 (keep) |
| get_runtime_config | 151 | 40 | -111 |
| setup_directories | 5 | 5 | 0 (keep) |
| extract_and_prepare_data | 57 | 50 | -7 |
| run_simulations_and_lob | 65 | 58 | -7 |
| print_summary | 52 | 45 | -7 |
| run_per_security_analysis | 36 | 30 | -6 |
| run_cross_security_aggregation | 44 | 37 | -7 |
| main | 141 | 60 | -81 |
| **NEW: Helper functions** | 0 | ~200 | +200 |
| **Total** | 627 | ~570 | -57 |

## Benefits:

1. **Separation of Concerns**
   - Configuration building split into focused functions
   - Stage execution separated from orchestration
   - Output printing consolidated

2. **Testability**
   - Each helper function can be unit tested
   - Security selection logic isolated
   - Stage wrappers can be mocked

3. **Readability**
   - main() is a clear orchestrator
   - get_runtime_config() no longer has 151 lines of nested logic
   - Each function does ONE thing

4. **Maintainability**
   - Adding new CLI args: modify helper functions
   - Adding new stages: add wrapper functions
   - Changing print format: modify print helpers

## Implementation Order:

1. **Phase 1** - Extract from get_runtime_config (biggest win)
2. **Phase 3** - Simplify all docstrings to 1 line
3. **Phase 2** - Extract from main()
4. **Phase 4** - Consolidate print functions
5. **Test** - Run both DRR and BHP tests
6. **Commit**

## Constraints:

- Keep ALL existing function signatures for public functions
- Keep ALL existing CLI arguments working
- Keep ALL existing behavior identical
- Max 1 comment per function (docstring)
- Private functions prefixed with `_`
- No functional changes, only refactoring

## Coverage:

This plan covers:
- ✅ All 9 existing functions
- ✅ All CLI argument handling
- ✅ All configuration logic
- ✅ All stage execution
- ✅ All output printing
- ✅ All security discovery logic
- ✅ All partition loading logic
- ✅ All parallel/sequential mode handling
- ✅ All statistics engine setup
- ✅ All directory setup
- ✅ All summary printing

**Nothing is left out.**
