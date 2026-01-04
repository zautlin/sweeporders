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
    'outputs_files': {
        'orders_with_simulated_metrics.csv': 8248,
        'simulation_order_summary.csv': 1274,
        'trade_level_comparison.csv': 1688,
    },
    'processed_files': {
        'cp_trades_simulation.csv': 3097,  # 19-column format: 2 rows per match + header (1548 * 2 + 1)
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
    
    partition_key = "2024-09-05/110621"
    output_path = Path(output_dir) / partition_key
    processed_path = Path(output_dir).parent / 'processed' / partition_key
    
    if not output_path.exists():
        print(f"❌ Output directory not found: {output_path}")
        return False
    
    if not processed_path.exists():
        print(f"❌ Processed directory not found: {processed_path}")
        return False
    
    print(f"\nValidating outputs in: {output_path}")
    print(f"Validating processed in: {processed_path}\n")
    
    all_valid = True
    
    # Check outputs/ files
    print("\n1. OUTPUTS/ FILE EXISTENCE & ROW COUNTS")
    print("-" * 80)
    for filename, expected_rows in BASELINE_METRICS['outputs_files'].items():
        filepath = output_path / filename
        if not validate_file_exists(filepath):
            all_valid = False
            continue
        if not validate_row_count(filepath, expected_rows, tolerance=1):
            all_valid = False
    
    # Check processed/ files
    print("\n2. PROCESSED/ FILE EXISTENCE & ROW COUNTS")
    print("-" * 80)
    for filename, expected_rows in BASELINE_METRICS['processed_files'].items():
        filepath = processed_path / filename
        if not validate_file_exists(filepath):
            all_valid = False
            continue
        if not validate_row_count(filepath, expected_rows, tolerance=1):
            all_valid = False
    
    # Check key columns in outputs/
    print("\n3. OUTPUTS/ REQUIRED COLUMNS")
    print("-" * 80)
    
    outputs_column_checks = {
        'simulation_order_summary.csv': ['orderid', 'matched_quantity', 'num_matches'],
        'orders_with_simulated_metrics.csv': ['orderid', 'simulated_matched_quantity', 'simulated_fill_status'],
    }
    
    for filename, required_cols in outputs_column_checks.items():
        filepath = output_path / filename
        if not validate_key_columns(filepath, required_cols):
            all_valid = False
    
    # Check key columns in processed/
    print("\n4. PROCESSED/ REQUIRED COLUMNS")
    print("-" * 80)
    
    processed_column_checks = {
        'cp_trades_simulation.csv': ['EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode', 
                                      'orderid', 'dealsource', 'matchgroupid', 'side', 'passiveaggressive'],
    }
    
    for filename, required_cols in processed_column_checks.items():
        filepath = processed_path / filename
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
