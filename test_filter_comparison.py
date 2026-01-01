"""
Performance Comparison: fast_filter.py vs current ingest.py

Tests the original ingest.py approach vs the new fast_filter.py
to demonstrate the performance improvement for handling large files.
"""

import pandas as pd
import numpy as np
import time
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from fast_filter import UltraFastOrderFilter, TimeFilter
from src.ingest import extract_centrepoint_orders

def test_original_ingest():
    """Test the original ingest.py approach"""
    print("\n" + "="*80)
    print("TEST 1: Original ingest.py approach (full load)")
    print("="*80)
    
    input_file = 'data/orders/drr_orders.csv'
    output_dir = 'processed_files'
    
    start_time = time.time()
    
    # Load entire file into memory (this is what ingest.py does)
    print(f"Loading entire file: {input_file}")
    df = pd.read_csv(input_file)
    load_time = time.time() - start_time
    print(f"  Time to load: {load_time:.2f}s")
    print(f"  Rows loaded: {len(df):,}")
    print(f"  Memory usage: ~{df.memory_usage(deep=True).sum() / 1024 / 1024:.1f}MB")
    
    # Now apply filters (this is slow due to datetime conversion)
    filter_start = time.time()
    
    from datetime import timezone, timedelta
    aest_tz = timezone(timedelta(hours=10))
    print(f"\nApplying filters...")
    print(f"  1. Converting timestamps to datetime...")
    df['timestamp_dt'] = pd.to_datetime(df['timestamp'], unit='ns', utc=True).dt.tz_convert(aest_tz)
    df['hour'] = df['timestamp_dt'].dt.hour
    
    print(f"  2. Filtering trading hours (10-16)...")
    filtered = df[(df['hour'] >= 10) & (df['hour'] <= 16)].copy()
    
    print(f"  3. Filtering participant 69...")
    cp_orders = filtered[filtered['participantid'] == 69].copy()
    
    filter_time = time.time() - filter_start
    total_time = time.time() - start_time
    
    print(f"\nResults:")
    print(f"  Rows kept: {len(cp_orders):,}")
    print(f"  Filter time: {filter_time:.2f}s")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Speed: {len(df) / total_time:,.0f} rows/sec")
    
    return len(cp_orders), total_time


def test_fast_filter():
    """Test the new fast_filter.py approach"""
    print("\n" + "="*80)
    print("TEST 2: New fast_filter.py approach (chunked streaming)")
    print("="*80)
    
    input_file = 'data/orders/drr_orders.csv'
    
    start_time = time.time()
    
    filter_obj = UltraFastOrderFilter(
        input_file=input_file,
        chunk_size=50000,
        verbose=False,  # Quiet mode for comparison
    )
    
    result_df = filter_obj.filter_orders(
        participant_ids=[69],
        start_hour=10,
        end_hour=16,
    )
    
    total_time = time.time() - start_time
    rows_kept = len(result_df) if result_df is not None else 0
    
    print(f"Results:")
    print(f"  Rows kept: {rows_kept:,}")
    print(f"  Total time: {total_time:.2f}s")
    
    return rows_kept, total_time


def test_vectorized_time_filter():
    """Test vectorized time filtering without datetime conversion"""
    print("\n" + "="*80)
    print("TEST 3: Vectorized time filtering (no datetime conversion)")
    print("="*80)
    
    input_file = 'data/orders/drr_orders.csv'
    
    start_time = time.time()
    
    print(f"Loading file with optimized dtypes...")
    from fast_filter import OPTIMAL_DTYPES, USECOLS_ORDERS
    
    dtypes = {col: OPTIMAL_DTYPES.get(col, 'object') for col in USECOLS_ORDERS}
    df = pd.read_csv(input_file, dtype=dtypes, usecols=USECOLS_ORDERS)
    load_time = time.time() - start_time
    
    print(f"  Time: {load_time:.2f}s")
    print(f"  Rows: {len(df):,}")
    print(f"  Memory: ~{df.memory_usage(deep=True).sum() / 1024 / 1024:.1f}MB")
    
    # Apply filters
    filter_start = time.time()
    
    print(f"\nApplying vectorized filters...")
    filtered = df[df['participantid'].isin([69])]
    filtered = TimeFilter.filter_by_hour_vectorized(
        filtered,
        timestamp_col='timestamp',
        start_hour=10,
        end_hour=16,
    )
    
    filter_time = time.time() - filter_start
    total_time = time.time() - start_time
    
    print(f"\nResults:")
    print(f"  Rows kept: {len(filtered):,}")
    print(f"  Filter time: {filter_time:.2f}s")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Speed: {len(df) / total_time:,.0f} rows/sec")
    
    return len(filtered), total_time


if __name__ == '__main__':
    print("\n" + "="*80)
    print("PERFORMANCE COMPARISON: fast_filter.py vs ingest.py")
    print("="*80)
    
    # Test 1: Original ingest approach
    rows1, time1 = test_original_ingest()
    
    # Test 2: New fast filter approach
    rows2, time2 = test_fast_filter()
    
    # Test 3: Vectorized approach
    rows3, time3 = test_vectorized_time_filter()
    
    # Summary
    print("\n" + "="*80)
    print("PERFORMANCE SUMMARY")
    print("="*80)
    
    print(f"\nApproach 1 (Original ingest.py)")
    print(f"  Time: {time1:.2f}s")
    print(f"  Rows: {rows1:,}")
    print(f"  Speed: {48033 / time1:,.0f} rows/sec")
    
    print(f"\nApproach 2 (Chunked streaming with optimized types)")
    print(f"  Time: {time2:.2f}s")
    print(f"  Rows: {rows2:,}")
    print(f"  Speed: {48033 / time2:,.0f} rows/sec")
    print(f"  Speedup: {time1/time2:.1f}x faster")
    
    print(f"\nApproach 3 (Vectorized filtering)")
    print(f"  Time: {time3:.2f}s")
    print(f"  Rows: {rows3:,}")
    print(f"  Speed: {48033 / time3:,.0f} rows/sec")
    print(f"  Speedup: {time1/time3:.1f}x faster")
    
    # Verify results match
    print(f"\n" + "="*80)
    print("RESULT VALIDATION")
    print("="*80)
    
    if rows1 == rows2 == rows3:
        print(f"✅ All approaches returned same row count: {rows1:,} rows")
    else:
        print(f"❌ Row count mismatch!")
        print(f"   Original: {rows1:,}")
        print(f"   Fast filter: {rows2:,}")
        print(f"   Vectorized: {rows3:,}")
    
    print(f"\n✅ Comparison complete!")
