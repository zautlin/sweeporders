"""
Phase 1.2: Read and Match Trades
Filters trades to only those linked to Centre Point orders
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def match_trades(trades_file: str, centrepoint_order_ids: list, output_dir: str) -> tuple:
    """
    Match trades to Centre Point orders.
    
    Args:
        trades_file: Path to trades CSV file
        centrepoint_order_ids: List of order IDs to match against
        output_dir: Directory to save processed files
        
    Returns:
        Tuple of (trades_df, trades_agg_df)
    """
    logger.info(f"Reading trades file: {trades_file}")
    
    # Convert order IDs to list for matching
    cp_order_ids_list = list(centrepoint_order_ids)
    logger.info(f"Matching against {len(cp_order_ids_list):,} Centre Point orders")
    
    # Read trades file
    trades_df = pd.read_csv(trades_file)
    logger.info(f"Total trades read: {len(trades_df):,}")
    
    # Match trades to Centre Point orders
    trades_df['orderid'] = pd.to_numeric(trades_df['orderid'], errors='coerce')
    matched_trades = trades_df[trades_df['orderid'].isin(cp_order_ids_list)].copy()
    logger.info(f"Matched trades: {len(matched_trades):,} ({100*len(matched_trades)/len(trades_df):.1f}%)")
    
    # Optimize data types
    matched_trades['orderid'] = matched_trades['orderid'].astype('int64')
    matched_trades['tradetime'] = pd.to_numeric(matched_trades['tradetime'], errors='coerce').astype('int64')
    matched_trades['quantity'] = pd.to_numeric(matched_trades['quantity'], errors='coerce').astype('uint32')
    matched_trades['tradeprice'] = pd.to_numeric(matched_trades['tradeprice'], errors='coerce').astype('float32')
    matched_trades['securitycode'] = pd.to_numeric(matched_trades['securitycode'], errors='coerce').astype('int64')
    matched_trades['participantid'] = pd.to_numeric(matched_trades['participantid'], errors='coerce').astype('uint32')
    matched_trades['side'] = pd.to_numeric(matched_trades['side'], errors='coerce').astype('int8')
    
    # Keep relevant columns
    columns_to_keep = [
        'orderid', 'tradetime', 'securitycode', 'tradeprice', 'quantity',
        'side', 'participantid'
    ]
    matched_trades = matched_trades[columns_to_keep].dropna()
    
    logger.info(f"Cleaned trades: {len(matched_trades):,}")
    
    # Save raw trades
    output_path = Path(output_dir) / 'centrepoint_trades_raw.csv.gz'
    matched_trades.to_csv(output_path, compression='gzip', index=False)
    logger.info(f"Saved raw trades to {output_path}")
    
    # Aggregate trades by order
    agg_data = []
    for order_id, group in matched_trades.groupby('orderid'):
        # Calculate metrics
        total_qty = group['quantity'].sum()
        avg_price = (group['tradeprice'] * group['quantity']).sum() / total_qty if total_qty > 0 else 0
        first_trade_ts = group['tradetime'].min()
        last_trade_ts = group['tradetime'].max()
        execution_duration_ns = last_trade_ts - first_trade_ts
        execution_duration_sec = execution_duration_ns / 1e9
        
        agg_data.append({
            'order_id': order_id,
            'total_quantity_filled': total_qty,
            'avg_execution_price': avg_price,
            'first_trade_ts': first_trade_ts,
            'last_trade_ts': last_trade_ts,
            'execution_duration_ns': execution_duration_ns,
            'execution_duration_sec': execution_duration_sec,
            'num_trades': len(group),
            'security_code': group['securitycode'].iloc[0]
        })
    
    trades_agg = pd.DataFrame(agg_data)
    
    logger.info(f"Aggregated trades: {len(trades_agg):,} unique orders")
    logger.info(f"Total quantity filled: {trades_agg['total_quantity_filled'].sum():,}")
    
    # Save aggregated trades
    output_path = Path(output_dir) / 'centrepoint_trades_agg.csv.gz'
    trades_agg.to_csv(output_path, compression='gzip', index=False)
    logger.info(f"Saved aggregated trades to {output_path}")
    
    return matched_trades, trades_agg


if __name__ == '__main__':
    from ingest import extract_centrepoint_orders
    
    output_dir = 'processed_files'
    Path(output_dir).mkdir(exist_ok=True)
    
    # Get Centre Point orders first
    cp_orders = extract_centrepoint_orders('data/orders/drr_orders.csv', output_dir)
    
    # Match trades
    trades, trades_agg = match_trades(
        'data/trades/drr_trades_segment_1.csv',
        cp_orders['order_id'].tolist(),
        output_dir
    )
    
    print(f"\nMatched {len(trades):,} trades from {len(trades_agg):,} orders")
