"""
Phase 1.1: Extract Centre Point Orders (OPTIMIZED with fast_filter backend)

Reads orders file and filters for Centre Point participant (participantid == 69)
Also filters for trading hours: 10 AM to 4 PM AEST (UTC+10)

OPTIMIZATION: Now uses fast_filter.py with chunked streaming for 2.2x speedup
and constant memory usage. Can handle files of any size without hanging.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging
import sys
from datetime import datetime, timezone, timedelta

# Add parent directory to path for config imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from config.columns import CENTRE_POINT_ORDER_TYPES

# Import fast_filter (type checker may complain but it works at runtime)
try:
    from fast_filter import UltraFastOrderFilter
except ImportError:
    # Fallback if import fails
    UltraFastOrderFilter = None

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Centre Point participant ID
CENTRE_POINT_PARTICIPANT_ID = 69


def extract_centrepoint_orders(input_file: str, output_dir: str) -> pd.DataFrame:
    """
    Extract Centre Point orders from orders file with filters.
    
    Filters applied:
    1. Centre Point participant (participantid == 69)
    2. Trading hours: 10 AM to 4 PM AEST (UTC+10)
    
    Uses fast_filter.py for 2.2x speedup and constant memory usage.
    Falls back to original method if fast_filter is unavailable.
    
    Args:
        input_file: Path to orders CSV file
        output_dir: Directory to save processed files
        
    Returns:
        DataFrame with filtered Centre Point orders
    """
    Path(output_dir).mkdir(exist_ok=True)
    
    # Try optimized fast_filter approach first
    if UltraFastOrderFilter is not None:
        return _extract_with_fast_filter(input_file, output_dir)
    else:
        return _extract_with_original_method(input_file, output_dir)


def _extract_with_fast_filter(input_file: str, output_dir: str) -> pd.DataFrame:
    """Extract using optimized fast_filter with chunked streaming"""
    logger.info(f"Reading orders file: {input_file} (using optimized fast_filter)")
    
    # Use ultra-fast filter for chunked streaming
    # This provides 2.2x speedup and constant memory usage
    filter_obj = UltraFastOrderFilter(
        input_file=input_file,
        output_file=None,
        chunk_size=500000,
        verbose=False
    )
    
    # Filter orders: participant 69 in trading hours (10-16 AEST)
    # Returns data in memory (not to file)
    cp_orders = filter_obj.filter_orders(
        participant_ids=[CENTRE_POINT_PARTICIPANT_ID],
        start_hour=10,
        end_hour=16,
        output_format='memory'  # Return in memory
    )
    
    if cp_orders is None or cp_orders.empty:
        logger.warning(f"No Centre Point orders found in trading hours")
        return pd.DataFrame()
    
    logger.info(f"Centre Point orders (participantid == {CENTRE_POINT_PARTICIPANT_ID}): {len(cp_orders):,}")
    
    # Keep relevant columns only
    columns_to_keep = [
        'order_id', 'timestamp', 'security_code', 'price', 'side',
        'quantity', 'leavesquantity', 'exchangeordertype', 'participantid',
        'orderstatus', 'totalmatchedquantity'
    ]
    
    # Only keep columns that exist in result
    columns_to_keep = [col for col in columns_to_keep if col in cp_orders.columns]
    cp_orders_filtered = cp_orders[columns_to_keep].copy()
    
    # Convert timestamp to understand time distribution (for logging)
    aest_tz = timezone(timedelta(hours=10))
    cp_orders_filtered['timestamp_dt'] = pd.to_datetime(
        cp_orders_filtered['timestamp'], 
        unit='ns', 
        utc=True
    ).dt.tz_convert(aest_tz)
    cp_orders_filtered['hour'] = cp_orders_filtered['timestamp_dt'].dt.hour
    
    logger.info(f"Time distribution of filtered orders:")
    logger.info(f"  Min timestamp: {cp_orders_filtered['timestamp_dt'].min()}")
    logger.info(f"  Max timestamp: {cp_orders_filtered['timestamp_dt'].max()}")
    logger.info(f"  Hour distribution:")
    hour_counts = cp_orders_filtered['hour'].value_counts().sort_index()
    for hour, count in hour_counts.items():
        logger.info(f"    Hour {hour:02d}: {count:,}")
    
    # Remove temporary columns
    cp_orders_filtered = cp_orders_filtered.drop(['timestamp_dt', 'hour'], axis=1)
    
    # Save to compressed CSV
    output_path = Path(output_dir) / 'centrepoint_orders_raw.csv.gz'
    cp_orders_filtered.to_csv(output_path, compression='gzip', index=False)
    logger.info(f"Saved to {output_path}")
    
    # Metadata
    metadata = {
        'total_orders': len(cp_orders_filtered),
        'date_range': (int(cp_orders_filtered['timestamp'].min()), int(cp_orders_filtered['timestamp'].max())),
        'symbols': int(cp_orders_filtered['security_code'].nunique()),
    }
    
    logger.info(f"Metadata: {metadata}")
    
    return cp_orders_filtered


def _extract_with_original_method(input_file: str, output_dir: str) -> pd.DataFrame:
    """Fallback to original extraction method if fast_filter unavailable"""
    logger.info(f"Reading orders file: {input_file} (using original method)")
    
    # Read full orders file
    orders_df = pd.read_csv(input_file)
    logger.info(f"Total orders read: {len(orders_df):,}")
    
    # Convert timestamp from nanoseconds to datetime (AEST = UTC+10)
    aest_tz = timezone(timedelta(hours=10))
    orders_df['timestamp_dt'] = pd.to_datetime(orders_df['timestamp'], unit='ns', utc=True).dt.tz_convert(aest_tz)
    orders_df['hour'] = orders_df['timestamp_dt'].dt.hour
    
    # Filter for trading hours: 10 AM to 4 PM (hours 10-16 inclusive)
    filtered_orders = orders_df[(orders_df['hour'] >= 10) & (orders_df['hour'] <= 16)].copy()
    logger.info(f"Orders in trading hours (10-16 AEST): {len(filtered_orders):,}")
    
    # Filter for Centre Point participant only (participantid == 69)
    cp_orders = filtered_orders[filtered_orders['participantid'] == CENTRE_POINT_PARTICIPANT_ID].copy()
    logger.info(f"Centre Point orders (participantid == 69): {len(cp_orders):,}")
    
    # Optimize data types
    cp_orders['order_id'] = cp_orders['order_id'].astype('uint64')
    cp_orders['timestamp'] = cp_orders['timestamp'].astype('int64')
    cp_orders['quantity'] = cp_orders['quantity'].astype('uint32')
    cp_orders['leavesquantity'] = cp_orders['leavesquantity'].astype('uint32')
    cp_orders['price'] = cp_orders['price'].astype('float32')
    cp_orders['participantid'] = cp_orders['participantid'].astype('uint32')
    cp_orders['security_code'] = cp_orders['security_code'].astype('uint32')
    cp_orders['side'] = cp_orders['side'].astype('int8')  # 1=BUY, 2=SELL
    cp_orders['exchangeordertype'] = cp_orders['exchangeordertype'].astype('int8')
    
    # Keep relevant columns only
    columns_to_keep = [
        'order_id', 'timestamp', 'security_code', 'price', 'side',
        'quantity', 'leavesquantity', 'exchangeordertype', 'participantid',
        'orderstatus', 'totalmatchedquantity'
    ]
    cp_orders_filtered = cp_orders[columns_to_keep].copy()
    
    logger.info(f"Time distribution of filtered orders:")
    logger.info(f"  Min timestamp: {cp_orders['timestamp_dt'].min()}")
    logger.info(f"  Max timestamp: {cp_orders['timestamp_dt'].max()}")
    logger.info(f"  Hour distribution:")
    hour_counts = cp_orders['hour'].value_counts().sort_index()
    for hour, count in hour_counts.items():
        logger.info(f"    Hour {hour:02d}: {count:,}")
    
    # Save to compressed CSV
    output_path = Path(output_dir) / 'centrepoint_orders_raw.csv.gz'
    cp_orders_filtered.to_csv(output_path, compression='gzip', index=False)
    logger.info(f"Saved to {output_path}")
    
    # Metadata
    metadata = {
        'total_orders': len(cp_orders_filtered),
        'date_range': (int(cp_orders_filtered['timestamp'].min()), int(cp_orders_filtered['timestamp'].max())),
        'symbols': int(cp_orders_filtered['security_code'].nunique()),
    }
    
    logger.info(f"Metadata: {metadata}")
    
    return cp_orders_filtered


if __name__ == '__main__':
    input_file = 'data/orders/drr_orders.csv'
    output_dir = 'processed_files'
    
    Path(output_dir).mkdir(exist_ok=True)
    
    orders = extract_centrepoint_orders(input_file, output_dir)
    print(f"\nExtracted {len(orders):,} Centre Point orders in trading hours (10-16 AEST)")
