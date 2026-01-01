"""
Debug the row count mismatch between filtering approaches
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import timezone, timedelta
from src.fast_filter import TimeFilter, OPTIMAL_DTYPES, USECOLS_ORDERS

input_file = 'data/orders/drr_orders.csv'

print("=" * 80)
print("DEBUGGING ROW COUNT MISMATCH")
print("=" * 80)

# Load with original approach
print("\n1. Original approach (full load):")
df_orig = pd.read_csv(input_file)
print(f"   Total rows: {len(df_orig):,}")

# Apply hour filter using datetime conversion
aest_tz = timezone(timedelta(hours=10))
df_orig['timestamp_dt'] = pd.to_datetime(df_orig['timestamp'], unit='ns', utc=True).dt.tz_convert(aest_tz)
df_orig['hour'] = df_orig['timestamp_dt'].dt.hour

print(f"   Hours 10-16: {len(df_orig[(df_orig['hour'] >= 10) & (df_orig['hour'] <= 16)]):,}")
cp_orders_orig = df_orig[(df_orig['hour'] >= 10) & (df_orig['hour'] <= 16) & (df_orig['participantid'] == 69)]
print(f"   Participant 69 in hours 10-16: {len(cp_orders_orig):,}")

# Load with optimized approach
print("\n2. Vectorized approach (optimized types):")
dtypes = {col: OPTIMAL_DTYPES.get(col, 'object') for col in USECOLS_ORDERS}
df_opt = pd.read_csv(input_file, dtype=dtypes, usecols=USECOLS_ORDERS)
print(f"   Total rows: {len(df_opt):,}")

# Apply filters
df_opt_filtered = df_opt[df_opt['participantid'].isin([69])]
print(f"   Participant 69: {len(df_opt_filtered):,}")

df_opt_filtered = TimeFilter.filter_by_hour_vectorized(
    df_opt_filtered,
    timestamp_col='timestamp',
    start_hour=10,
    end_hour=16,
)
print(f"   After hour filter (10-16): {len(df_opt_filtered):,}")

# Check the hour calculation
print("\n3. Comparing hour calculations:")

# Original method - convert timestamp to datetime
timestamps_for_orig = df_orig['timestamp'].iloc[:10].values
print(f"   Sample timestamps: {timestamps_for_orig[:3]}")

# Convert to datetime
datetimes = pd.to_datetime(timestamps_for_orig, unit='ns', utc=True).tz_convert(aest_tz)
hours_orig = datetimes.hour
print(f"   Hours (datetime method): {list(hours_orig[:3])}")

# Vectorized method
timestamps_for_vec = df_opt['timestamp'].iloc[:10].values
timestamps_sec = timestamps_for_vec / 1_000_000_000
utc_hours = (timestamps_sec / 3600) % 24
local_hours = (utc_hours + 10) % 24
print(f"   Hours (vectorized method): {list(local_hours[:3])}")

print("\n4. Checking for boundary cases:")

# Find rows in hours 10-16 using original method
mask_orig = (df_orig['hour'] >= 10) & (df_orig['hour'] <= 16)
rows_orig = df_orig[mask_orig]

# Find rows in hours 10-16 using vectorized method
timestamps_sec = df_opt['timestamp'].values / 1_000_000_000
utc_hours = (timestamps_sec / 3600) % 24
local_hours = (utc_hours + 10) % 24
mask_vec = (local_hours >= 10) & (local_hours <= 16)
rows_vec = df_opt[mask_vec]

print(f"   Original method finds: {len(rows_orig):,} rows in hours 10-16")
print(f"   Vectorized method finds: {len(rows_vec):,} rows in hours 10-16")

# Check for differences
if len(rows_orig) != len(rows_vec):
    print("\n5. Investigating differences:")
    # Show some of the hours that differ
    all_hours_orig = df_orig['hour'].unique()
    print(f"   Unique hours (original): {sorted(all_hours_orig)}")
    
    # Show which timestamps are being filtered
    hours_in_range_orig = sorted(df_orig[mask_orig]['hour'].unique())
    hours_in_range_vec = sorted(df_opt[mask_vec]['timestamp'].apply(
        lambda t: int(((t / 1_000_000_000 / 3600) % 24 + 10) % 24)
    ).unique())
    
    print(f"   Hours found (original): {hours_in_range_orig}")
    print(f"   Hours found (vectorized): {hours_in_range_vec}")

# Filter for participant 69
cp_orig = rows_orig[rows_orig['participantid'] == 69]
cp_vec = rows_vec[rows_vec['participantid'] == 69]

print(f"\n6. Final participant 69 counts:")
print(f"   Original: {len(cp_orig):,}")
print(f"   Vectorized: {len(cp_vec):,}")

if len(cp_orig) != len(cp_vec):
    print(f"\n   Difference: {len(cp_orig) - len(cp_vec):,} rows")
    
    # Show a few sample rows from original
    if len(cp_orig) > 0:
        print(f"\n   Sample from original (first 3):")
        for idx, row in cp_orig.iloc[:3].iterrows():
            hour_dt = pd.to_datetime(row['timestamp'], unit='ns', utc=True).tz_convert(aest_tz).hour
            hour_vec = int(((row['timestamp'] / 1_000_000_000 / 3600) % 24 + 10) % 24)
            print(f"     timestamp={row['timestamp']}, hour_dt={hour_dt}, hour_vec={hour_vec}")

