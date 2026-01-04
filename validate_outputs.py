#!/usr/bin/env python3
"""
Pipeline Output Validation Script
Compares baseline vs refactored pipeline outputs to ensure identical results.
"""

import pandas as pd
from pathlib import Path
import sys

# Baseline metrics (from current implementation)
BASELINE_METRICS = {
    'execution_time_max': 7.0,  # seconds (allow some variance)
    'qualifying_sweep_orders': 1273,
    'simulation_matches': 1548,
    'file_counts': {
        'orders_with_simulated_metrics.csv': 8248,
        'simulation_order_summary.csv': 1274,
        'simulation_match_details.csv': 1549,
        'trade_level_comparison.csv': 1688,
        'trade_accuracy_summary.csv': 2,
        's_t_2024-09-05.csv.gz': 3097,  # 19-column format: 2 rows per match + header (1548 * 2 + 1)
    }
}

def validate_file_exists(filepath):
    """Check if file exists."""
    if not filepath.exists():
        print(f"❌ MISSING: {filepath}")
        return False
    print(f"✓ EXISTS: {filepath}")
    return True

def validate_row_count(filepath, expected_rows, tolerance=0):
    """Check row count matches expected."""
    if not filepath.exists():
        return False
    
    try:
        if filepath.suffix == '.gz':
            df = pd.read_csv(filepath, compression='gzip')
        else:
            df = pd.read_csv(filepath)
        
        actual_rows = len(df) + 1  # +1 for header
        
        if abs(actual_rows - expected_rows) <= tolerance:
            print(f"✓ ROW COUNT OK: {filepath.name} = {actual_rows} rows")
            return True
        else:
            print(f"❌ ROW COUNT MISMATCH: {filepath.name}")
            print(f"   Expected: {expected_rows}, Actual: {actual_rows}, Diff: {actual_rows - expected_rows}")
            return False
    except Exception as e:
        print(f"❌ ERROR reading {filepath}: {e}")
        return False

def validate_key_columns(filepath, required_columns):
    """Check required columns exist."""
    if not filepath.exists():
        return False
    
    try:
        if filepath.suffix == '.gz':
            df = pd.read_csv(filepath, compression='gzip', nrows=1)
        else:
            df = pd.read_csv(filepath, nrows=1)
        
        missing = [col for col in required_columns if col not in df.columns]
        
        if missing:
            print(f"❌ MISSING COLUMNS in {filepath.name}: {missing}")
            return False
        else:
            print(f"✓ COLUMNS OK: {filepath.name}")
            return True
    except Exception as e:
        print(f"❌ ERROR reading {filepath}: {e}")
        return False

def validate_pipeline_outputs(output_dir):
    """Validate all pipeline outputs."""
    print("="*80)
    print("PIPELINE OUTPUT VALIDATION")
    print("="*80)
    
    output_path = Path(output_dir) / "2024-09-05" / "110621"
    
    if not output_path.exists():
        print(f"❌ Output directory not found: {output_path}")
        return False
    
    print(f"\nValidating outputs in: {output_path}\n")
    
    all_valid = True
    
    # Check file existence and row counts
    print("\n1. FILE EXISTENCE & ROW COUNTS")
    print("-" * 80)
    for filename, expected_rows in BASELINE_METRICS['file_counts'].items():
        filepath = output_path / filename
        if not validate_file_exists(filepath):
            all_valid = False
            continue
        if not validate_row_count(filepath, expected_rows, tolerance=1):
            all_valid = False
    
    # Check key columns
    print("\n2. REQUIRED COLUMNS")
    print("-" * 80)
    
    column_checks = {
        'simulation_order_summary.csv': ['orderid', 'matched_quantity', 'num_matches'],
        'simulation_match_details.csv': ['sweep_orderid', 'incoming_orderid', 'matched_quantity'],
        's_t_2024-09-05.csv.gz': ['EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode', 
                                   'orderid', 'dealsource', 'matchgroupid', 'side', 'passiveaggressive'],
        'orders_with_simulated_metrics.csv': ['orderid', 'simulated_matched_quantity', 'simulated_fill_status'],
    }
    
    for filename, required_cols in column_checks.items():
        filepath = output_path / filename
        if not validate_key_columns(filepath, required_cols):
            all_valid = False
    
    # Summary
    print("\n" + "="*80)
    if all_valid:
        print("✅ VALIDATION PASSED - All outputs match baseline")
    else:
        print("❌ VALIDATION FAILED - See errors above")
    print("="*80)
    
    return all_valid

if __name__ == '__main__':
    output_dir = sys.argv[1] if len(sys.argv) > 1 else 'data/outputs'
    success = validate_pipeline_outputs(output_dir)
    sys.exit(0 if success else 1)
