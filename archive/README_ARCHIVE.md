# Archive Directory

This directory contains files that were part of the development process but are not actively used in the production pipeline.

## Contents

### Documentation (Moved from root and docs/)
- `README.md` - Original project README
- `MODIFICATION_SUMMARY.md` - Summary of code modifications
- `MULTIDATASET_PIPELINE.md` - Multi-dataset pipeline documentation
- `DYNAMIC_CONFIG_PLAN.md` - Dynamic configuration planning document
- `DYNAMIC_CONFIG_SUMMARY.md` - Configuration summary
- `TECHNICAL_SPECIFICATION.md` - Technical specification document
- `session-SIMULATION.md` - Simulation session notes

### Utility Scripts
- `analyze_continuous_lit_orders.py` - Analysis script for continuous lit orders
- `generate_analysis_report.py` - Markdown report generation utility
- `validate_outputs.py` - Output validation script

### Debug Data
- `debug_data/` - Debug CSV files from testing
  - `orders_final_2024-09-04_110621.csv`
  - `orders_final_2024-09-04_85603.csv`
  - `orders_final_2024-09-05_110621.csv`
  - `orders_final_2024-09-05_85603.csv`

## Why These Files Are Archived

### Utility Scripts
These scripts were useful during development and debugging but are not part of the core pipeline:
- **analyze_continuous_lit_orders.py**: Specialized analysis that can be run separately if needed
- **generate_analysis_report.py**: Report generation utility, not part of automated pipeline
- **validate_outputs.py**: Validation script used during development phase

### Documentation
Multiple documentation files were created during development iterations. The active documentation now lives in:
- `docs/` directory (for technical docs that should remain accessible)
- Core pipeline README (to be created in root)

### Debug Data
Debug CSV files from test runs - kept for reference but not needed for production runs.

## Restoring Files

If you need any of these files, they can be accessed from this archive directory:

```bash
# Copy a utility script back to root
cp archive/generate_analysis_report.py .

# View archived documentation
cat archive/README.md
```

## Active Pipeline Files

The production pipeline uses:
- `src/` - All core modules
- `run_multidataset_pipeline.py` - Main orchestrator
- `data/` - Input/output data directories
- `docs/` - Technical documentation (bi.txt, dd.txt)
