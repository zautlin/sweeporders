# Project Cleanup Analysis

## Current State
- **Active Pipeline:** `extract_partitioned.py` (simple, self-contained, 979 lines)
- **Advanced Pipeline:** `src/ingest_centrepoint.py` (class-based, 2,376 lines)
- **Simulation Module:** `src/sweep_simulation/` (4 files, used by both pipelines)
- **Configuration:** `config/columns.py` (used by ingest_centrepoint.py)

## Files Cleaned Up ✅
- `extract_partitioned_old.py` - Removed (backup)
- `extract_partitioned.py.backup` - Removed (backup)
- `latest` - Removed (580KB session file)
- `processed_files/` - Removed (empty directory)
- `debug_filter_mismatch.py` - Removed (debug script)
- `test_filter_comparison.py` - Removed (test script)
- `e2e_integration_test.py` - Removed (test script)

## Files to Review

### Old Pipeline Entry Points (can be removed if not needed):
- `main.py` - Old orchestration script using individual src modules
- `main_pipeline.py` - Old multi-step pipeline runner

### Old Pipeline src/ Modules (used only by main.py/main_pipeline.py):
These are NOT used by the current `extract_partitioned.py` or `src/ingest_centrepoint.py`:

1. `src/ingest.py` - Old data ingestion (replaced by extract_partitioned.py functions)
2. `src/match_trades.py` - Old trade matching (replaced)
3. `src/book.py` - Old dark book builder (replaced)
4. `src/classify.py` - Old classification logic (replaced)
5. `src/simulate.py` - Old simulation (replaced by src/sweep_simulation/)
6. `src/report.py` - Old reporting (replaced by comparison reports)
7. `src/nbbo.py` - Old NBBO handling (replaced by nbbo_provider.py)

### Scalable Processing Infrastructure (decision needed):
8. `src/chunk_iterator.py` - Chunked file reading utilities
9. `src/parallel_scheduler.py` - Parallel job scheduling
10. `src/ingest_scalable.py` - Scalable multi-partition processing
11. `src/result_aggregator.py` - Result aggregation across partitions
12. `src/execution_monitor.py` - Real-time progress monitoring
13. `src/fast_filter.py` - Fast filtering utilities

### Configuration Files:
14. `config/adaptive_config.py` - Hardware detection & optimization
15. `config/scaling_config.py` - Scaling parameters
16. `config/test_scaling_config.json` - Test configuration

## Currently Used Files ✅

### Core Pipeline:
- `extract_partitioned.py` - Main simple pipeline
- `src/ingest_centrepoint.py` - Advanced class-based pipeline

### Simulation Module:
- `src/sweep_simulation/__init__.py`
- `src/sweep_simulation/simulator.py`
- `src/sweep_simulation/nbbo_provider.py`
- `src/sweep_simulation/metrics_calculator.py`
- `src/sweep_simulation/comparison_reporter.py`

### Configuration:
- `config/columns.py` - Column name mappings

### Dependencies:
- `requirements.txt`

## Recommendation

**Option 1: Minimal (Keep only active code)**
Remove all old pipeline files (items 1-7) and scalable infrastructure (items 8-15).
Keep only: extract_partitioned.py, ingest_centrepoint.py, sweep_simulation/, config/columns.py

**Option 2: Keep Infrastructure**
Remove old pipeline files (items 1-7) but keep scalable infrastructure (items 8-15) for future use.

**Option 3: Archive Old Code**
Move old pipeline code to an `archive/` directory instead of deleting.

Which option would you prefer?
