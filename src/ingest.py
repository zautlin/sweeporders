"""
Phase 1.1: Extract Centre Point Orders
Reads orders file and filters for Centre Point order types
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def extract_centrepoint_orders(input_file: str, output_dir: str) -> pd.DataFrame:
    """
    Extract Centre Point orders from orders file.
    
    Centre Point order types: 64, 256, 2048, 4096, 4098
    
    Args:
        input_file: Path to orders CSV file
        output_dir: Directory to save processed files
        
    Returns:
        DataFrame with Centre Point orders
    """
    CENTREPOINT_TYPES = [64, 256, 2048, 4096, 4098]
    
    logger.info(f"Reading orders file: {input_file}")
    
    # Read full orders file
    orders_df = pd.read_csv(input_file)
    logger.info(f"Total orders read: {len(orders_df):,}")
    
    # Filter for Centre Point order types
    cp_orders = orders_df[orders_df['exchangeordertype'].isin(CENTREPOINT_TYPES)].copy()
    logger.info(f"Centre Point orders found: {len(cp_orders):,}")
    
    # Optimize data types
    cp_orders['order_id'] = cp_orders['order_id'].astype('uint64')
    cp_orders['timestamp'] = pd.to_datetime(cp_orders['timestamp'], unit='ns').astype('int64')
    cp_orders['quantity'] = cp_orders['quantity'].astype('uint32')
    cp_orders['leavesquantity'] = cp_orders['leavesquantity'].astype('uint32')
    cp_orders['price'] = cp_orders['price'].astype('float32')
    cp_orders['participantid'] = cp_orders['participantid'].astype('uint32')
    cp_orders['security_code'] = cp_orders['security_code'].astype('uint32')
    cp_orders['side'] = cp_orders['side'].astype('category')  # 1=BUY, 2=SELL
    cp_orders['exchangeordertype'] = cp_orders['exchangeordertype'].astype('int8')
    
    # Keep relevant columns only
    columns_to_keep = [
        'order_id', 'timestamp', 'security_code', 'price', 'side',
        'quantity', 'leavesquantity', 'exchangeordertype', 'participantid',
        'orderstatus', 'totalmatchedquantity'
    ]
    cp_orders_filtered = cp_orders[columns_to_keep].copy()
    
    logger.info(f"Centre Point order types distribution:")
    for ot in sorted(CENTREPOINT_TYPES):
        count = (cp_orders_filtered['exchangeordertype'] == ot).sum()
        logger.info(f"  Type {ot}: {count:,}")
    
    # Save to parquet
    output_path = Path(output_dir) / 'centrepoint_orders_raw.parquet'
    cp_orders_filtered.to_parquet(output_path, compression='snappy')
    logger.info(f"Saved to {output_path}")
    
    # Metadata
    metadata = {
        'total_orders': len(cp_orders_filtered),
        'date_range': (int(cp_orders_filtered['timestamp'].min()), int(cp_orders_filtered['timestamp'].max())),
        'symbols': int(cp_orders_filtered['security_code'].nunique()),
        'participants': int(cp_orders_filtered['participantid'].nunique()),
    }
    
    logger.info(f"Metadata: {metadata}")
    
    return cp_orders_filtered


if __name__ == '__main__':
    import sys
    
    input_file = 'data/orders/drr_orders.csv'
    output_dir = 'processed_files'
    
    Path(output_dir).mkdir(exist_ok=True)
    
    orders = extract_centrepoint_orders(input_file, output_dir)
    print(f"\nExtracted {len(orders):,} Centre Point orders")
