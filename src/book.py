"""
Phase 1.3: Build Dark Order Book
Creates a searchable data structure of all orders that could be in the dark book
"""

import pandas as pd
import pickle
from pathlib import Path
import logging
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def build_dark_book(centrepoint_orders: pd.DataFrame, output_dir: str) -> tuple:
    """
    Build dark order book structure organized by symbol and side.
    
    Structure:
    {
        'symbol_code': {
            1: {  # BUY side
                price: [(order_id, quantity, timestamp, participant_id), ...],
                ...
            },
            2: {  # SELL side
                price: [(order_id, quantity, timestamp, participant_id), ...],
                ...
            }
        },
        ...
    }
    
    Args:
        centrepoint_orders: DataFrame with all Centre Point orders
        output_dir: Directory to save dark book
        
    Returns:
        Dictionary with dark book structure
    """
    logger.info("Building dark order book...")
    
    dark_book = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    order_index = {}
    
    # Group by symbol
    for symbol in centrepoint_orders['security_code'].unique():
        symbol_orders = centrepoint_orders[centrepoint_orders['security_code'] == symbol]
        logger.info(f"Processing symbol {symbol}: {len(symbol_orders):,} orders")
        
        for side in [1, 2]:  # 1=BUY, 2=SELL
            side_orders = symbol_orders[symbol_orders['side'] == side].sort_values('timestamp')
            
            for _, order in side_orders.iterrows():
                order_id = order['order_id']
                price = order['price']
                quantity = order['quantity']
                timestamp = order['timestamp']
                participant_id = order['participantid']
                
                # Add to dark book at this price level
                dark_book[symbol][side][price].append({
                    'order_id': order_id,
                    'quantity': quantity,
                    'timestamp': timestamp,
                    'participant_id': participant_id
                })
                
                # Add to order index for fast lookup
                order_index[order_id] = {
                    'symbol': symbol,
                    'side': side,
                    'price': price,
                    'quantity': quantity,
                    'timestamp': timestamp,
                    'participant_id': participant_id
                }
    
    # Convert defaultdicts to regular dicts for serialization
    dark_book_dict = {}
    for symbol, sides in dark_book.items():
        dark_book_dict[symbol] = {}
        for side, prices in sides.items():
            dark_book_dict[symbol][side] = dict(prices)
    
    # Save dark book
    output_path = Path(output_dir) / 'dark_book_state.pkl'
    with open(output_path, 'wb') as f:
        pickle.dump(dark_book_dict, f)
    logger.info(f"Saved dark book to {output_path}")
    
    # Save order index
    output_path = Path(output_dir) / 'order_index.pkl'
    with open(output_path, 'wb') as f:
        pickle.dump(order_index, f)
    logger.info(f"Saved order index to {output_path}")
    
    # Save summary stats
    stats = {
        'total_symbols': len(dark_book_dict),
        'total_orders': len(order_index),
        'total_quantity': sum(o['quantity'] for o in order_index.values()),
        'symbols': list(dark_book_dict.keys())
    }
    
    logger.info(f"Dark book stats: {stats}")
    
    return dark_book_dict, order_index


if __name__ == '__main__':
    output_dir = 'processed_files'
    
    # Load Centre Point orders
    orders_path = Path(output_dir) / 'centrepoint_orders_raw.parquet'
    cp_orders = pd.read_parquet(orders_path)
    
    # Build dark book
    dark_book, order_index = build_dark_book(cp_orders, output_dir)
    
    print(f"\nBuilt dark book with {len(order_index):,} orders")
