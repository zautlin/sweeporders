"""
NBBO Midprice Extraction and Management
Extracts NBBO midprices from trade execution data for use in dark book simulations
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.columns import INPUT_FILES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def extract_nbbo_midprices_from_trades(trades_df: pd.DataFrame) -> pd.DataFrame:
    """
    Extract NBBO midprices from trade data.
    
    Uses the nationalbidpricesnapshot and nationalofferpricesnapshot columns
    that are recorded at time of execution.
    
    Args:
        trades_df: DataFrame with trade records containing:
            - tradetime: timestamp of execution
            - nationalbidpricesnapshot: bid price at execution
            - nationalofferpricesnapshot: ask price at execution
            - securitycode: security identifier
            
    Returns:
        DataFrame with columns:
            - tradetime: timestamp
            - security_code: security identifier
            - bid_price: snapshot bid
            - ask_price: snapshot ask
            - midprice: (bid + ask) / 2
    """
    logger.info("Extracting NBBO midprices from trade data...")
    
    nbbo_data = trades_df[[
        'tradetime',
        'securitycode',
        'nationalbidpricesnapshot',
        'nationalofferpricesnapshot'
    ]].copy()
    
    # Rename to standard names
    nbbo_data = nbbo_data.rename(columns={
        'tradetime': 'timestamp',
        'securitycode': 'security_code',
        'nationalbidpricesnapshot': 'bid_price',
        'nationalofferpricesnapshot': 'ask_price'
    })
    
    # Calculate midprice
    nbbo_data['midprice'] = (nbbo_data['bid_price'] + nbbo_data['ask_price']) / 2.0
    
    # Remove duplicates and keep latest for each symbol
    nbbo_data = nbbo_data.sort_values('timestamp').drop_duplicates(
        subset=['security_code'],
        keep='last'
    )
    
    logger.info(f"Extracted {len(nbbo_data):,} NBBO snapshots")
    logger.info(f"Symbols covered: {nbbo_data['security_code'].nunique()}")
    
    return nbbo_data


def load_nbbo_midprices(trades_file: str = None, trades_df: pd.DataFrame = None) -> dict:
    """
    Load NBBO midprices as a lookup dictionary.
    
    Can accept either a trades file path or a trades DataFrame.
    
    Args:
        trades_file: Path to trades CSV file (optional)
        trades_df: Pre-loaded trades DataFrame (optional)
        
    Returns:
        Dictionary mapping security_code -> midprice
    """
    if trades_df is None:
        if trades_file is None:
            trades_file = INPUT_FILES.get('trades', 'data/trades/drr_trades_segment_1.csv')
        logger.info(f"Loading trades from: {trades_file}")
        trades_df = pd.read_csv(trades_file)
    
    nbbo_data = extract_nbbo_midprices_from_trades(trades_df)
    
    # Create lookup dictionary
    nbbo_lookup = {}
    for _, row in nbbo_data.iterrows():
        nbbo_lookup[row['security_code']] = {
            'bid_price': row['bid_price'],
            'ask_price': row['ask_price'],
            'midprice': row['midprice'],
            'timestamp': row['timestamp']
        }
    
    logger.info(f"Created NBBO lookup for {len(nbbo_lookup)} securities")
    
    return nbbo_lookup


def get_midprice_for_security(security_code: int, nbbo_lookup: dict) -> float:
    """
    Get midprice for a specific security.
    
    Args:
        security_code: Security identifier
        nbbo_lookup: NBBO lookup dictionary
        
    Returns:
        Midprice, or None if not found
    """
    if security_code in nbbo_lookup:
        return nbbo_lookup[security_code]['midprice']
    return None


def classify_trade_location(trade_price: float, bid: float, ask: float) -> str:
    """
    Classify where a trade executed relative to the spread.
    
    Args:
        trade_price: Actual execution price
        bid: Bid price
        ask: Ask price
        
    Returns:
        Classification: 'at_bid', 'at_ask', 'inside_spread', 'outside_spread'
    """
    if trade_price <= bid:
        return 'at_bid'
    elif trade_price >= ask:
        return 'at_ask'
    elif bid < trade_price < ask:
        return 'inside_spread'
    else:
        return 'outside_spread'


def add_nbbo_metrics_to_orders(orders_df: pd.DataFrame, trades_df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach NBBO metrics to order records.
    
    For each order, find the NBBO snapshot at time of execution and add:
    - bid_price, ask_price, midprice
    - execution_vs_bid, execution_vs_ask, execution_vs_mid
    - trade_location classification
    
    Args:
        orders_df: Orders DataFrame
        trades_df: Trades DataFrame with NBBO snapshots
        
    Returns:
        Orders DataFrame with added NBBO columns
    """
    logger.info("Adding NBBO metrics to orders...")
    
    # Group trades by order_id and security to get execution details
    trades_summary = trades_df.groupby('orderid').agg({
        'tradetime': 'first',
        'tradeprice': 'mean',
        'nationalbidpricesnapshot': 'mean',
        'nationalofferpricesnapshot': 'mean'
    }).reset_index()
    
    trades_summary = trades_summary.rename(columns={
        'orderid': 'order_id',
        'tradetime': 'execution_time',
        'tradeprice': 'actual_execution_price',
        'nationalbidpricesnapshot': 'bid_at_execution',
        'nationalofferpricesnapshot': 'ask_at_execution'
    })
    
    # Calculate midprice
    trades_summary['midprice_at_execution'] = (
        (trades_summary['bid_at_execution'] + trades_summary['ask_at_execution']) / 2.0
    )
    
    # Calculate execution quality metrics
    trades_summary['vs_bid'] = (
        trades_summary['actual_execution_price'] - trades_summary['bid_at_execution']
    )
    trades_summary['vs_ask'] = (
        trades_summary['actual_execution_price'] - trades_summary['ask_at_execution']
    )
    trades_summary['vs_midprice'] = (
        trades_summary['actual_execution_price'] - trades_summary['midprice_at_execution']
    )
    
    # Merge with orders
    orders_with_nbbo = orders_df.merge(
        trades_summary,
        on='order_id',
        how='left'
    )
    
    logger.info(f"Added NBBO metrics to {orders_with_nbbo['midprice_at_execution'].notna().sum():,} orders")
    
    return orders_with_nbbo


# ============================================================================
# SIMULATION MATCHING LOGIC
# ============================================================================

def match_dark_book_at_midprice(
    order_price: float,
    order_quantity: int,
    order_side: int,
    midprice: float,
    dark_book_prices: dict,
    dark_book_orders: dict
) -> dict:
    """
    Match incoming order against dark book using midprice as reference.
    
    Matching algorithm:
    1. For BUY orders: Match against SELL orders at or better than midprice
    2. For SELL orders: Match against BUY orders at or better than midprice
    3. Within acceptable prices, match by price priority (best first) then FIFO
    
    Args:
        order_price: Order's limit price
        order_quantity: Quantity to match
        order_side: 1=BUY, 2=SELL
        midprice: Reference midprice for matching (typical execution price)
        dark_book_prices: Dict of available prices in dark book (opposite side)
        dark_book_orders: Dict mapping prices to order lists
        
    Returns:
        Dictionary with matching results:
            - matched_quantity: Total quantity matched
            - matched_orders: List of matched order IDs
            - execution_prices: List of matched prices
            - average_price: Volume-weighted average execution price
    """
    matched_quantity = 0
    matched_orders = []
    execution_prices = []
    remaining_qty = order_quantity
    
    if order_side == 1:  # BUY order - match against SELL orders
        # Want best (lowest) SELL prices at or below order price
        # Prefer prices at or below midprice (better than market)
        acceptable_prices = sorted([
            p for p in dark_book_prices.keys()
            if p <= min(order_price, midprice * 1.05)  # Allow slight premium
        ])
    else:  # SELL order - match against BUY orders
        # Want best (highest) BUY prices at or above order price
        # Prefer prices at or above midprice (better than market)
        acceptable_prices = sorted([
            p for p in dark_book_prices.keys()
            if p >= max(order_price, midprice * 0.95)  # Allow slight discount
        ], reverse=True)
    
    # Match orders by price priority then FIFO
    for price in acceptable_prices:
        for order_data in dark_book_orders[price]:
            if remaining_qty <= 0:
                break
            
            match_qty = min(remaining_qty, order_data['quantity'])
            matched_quantity += match_qty
            matched_orders.append(order_data['order_id'])
            execution_prices.extend([price] * match_qty)
            remaining_qty -= match_qty
        
        if remaining_qty <= 0:
            break
    
    # Calculate average execution price
    avg_price = (
        sum(execution_prices) / len(execution_prices)
        if execution_prices else 0
    )
    
    return {
        'matched_quantity': matched_quantity,
        'fill_ratio': matched_quantity / order_quantity if order_quantity > 0 else 0,
        'matched_orders': matched_orders,
        'execution_prices': execution_prices,
        'average_price': avg_price,
        'vs_midprice': avg_price - midprice if execution_prices else 0
    }


if __name__ == '__main__':
    import sys
    
    # Example usage
    trades_path = INPUT_FILES.get('trades', 'data/trades/drr_trades_segment_1.csv')
    trades = pd.read_csv(trades_path)
    
    # Extract NBBO midprices
    nbbo_lookup = load_nbbo_midprices(trades_df=trades)
    
    # Show results
    print("\n=== NBBO Midprice Extraction ===")
    for symbol, data in list(nbbo_lookup.items())[:5]:
        print(f"Symbol {symbol}: bid={data['bid_price']}, ask={data['ask_price']}, mid={data['midprice']:.2f}")
