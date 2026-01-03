"""
Sweep Simulator Module

Handles all sweep order matching simulation logic:
- Load and prepare sweep orders and incoming orders
- Simulate time-priority sweep matching algorithm
- Calculate midpoint prices from NBBO or fallback to bid/offer
- Track sweep order utilization
- Generate match details and order summaries
"""

import pandas as pd
import numpy as np

# Constants
SWEEP_ORDER_TYPE = 2048
ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}  # ALL CP types, including sweep-to-sweep
ORDER_TYPE_COLUMN = 'exchangeordertype'


def get_midpoint(nbbo_data, timestamp, orderbookid, fallback_bid, fallback_offer):
    """
    Get midpoint price at given timestamp.
    
    Priority:
    1. NBBO file data (if available)
    2. Fallback to bid/offer from orders
    
    Args:
        nbbo_data: DataFrame from nbbo.csv (or None if not available), 
                   should be sorted by timestamp
        timestamp: Order timestamp (nanoseconds)
        orderbookid: Security code
        fallback_bid: Bid price from order (fallback)
        fallback_offer: Offer price from order (fallback)
    
    Returns:
        Midpoint price or None
    """
    
    # Try NBBO data first
    if nbbo_data is not None and len(nbbo_data) > 0:
        midpoint = _get_midpoint_from_nbbo(nbbo_data, timestamp, orderbookid)
        if midpoint is not None:
            return midpoint
    
    # Fallback to order bid/offer
    if fallback_bid is not None and fallback_offer is not None:
        if pd.notna(fallback_bid) and pd.notna(fallback_offer):
            return (fallback_bid + fallback_offer) / 2.0
    
    return None


def _get_midpoint_from_nbbo(nbbo_data, timestamp, orderbookid):
    """Get midpoint from NBBO data (most recent quote before timestamp)."""
    
    if nbbo_data is None:
        return None
    
    # Handle both 'security_code' and 'orderbookid' column names
    orderbook_col = 'security_code' if 'security_code' in nbbo_data.columns else 'orderbookid'
    timestamp_col = 'timestamp' if 'timestamp' in nbbo_data.columns else 'tradedate'
    
    # Filter for this orderbookid and timestamp <= target
    valid_quotes = nbbo_data[
        (nbbo_data[orderbook_col] == orderbookid) &
        (nbbo_data[timestamp_col] <= timestamp)
    ]
    
    if len(valid_quotes) == 0:
        return None
    
    # Get most recent quote
    latest = valid_quotes.iloc[-1]
    
    # Handle both standardized and original column names
    bid_col = 'bidprice' if 'bidprice' in latest.index else 'bid'
    offer_col = 'offerprice' if 'offerprice' in latest.index else 'offer'
    
    # Calculate midpoint
    midpoint = (latest[bid_col] + latest[offer_col]) / 2.0
    
    return midpoint


def load_and_prepare_orders(partition_data):
    """
    Load and prepare sweep orders and all matching-eligible orders for simulation.
    
    Args:
        partition_data: Dictionary containing:
            - 'orders_before': DataFrame from orders_before_matching.csv
            - 'orders_after': DataFrame from orders_after_matching.csv
            - 'last_execution': DataFrame from last_execution_time.csv
    
    Returns:
        Tuple of (sweep_orders, all_orders)
    """
    
    sweep_orders = _prepare_sweep_orders(partition_data)
    all_orders = _prepare_all_orders_for_matching(partition_data)
    
    return sweep_orders, all_orders


def _prepare_sweep_orders(partition_data):
    """
    Prepare sweep orders (type 2048) from orders_before_matching.csv.
    
    Uses orders_before to get FIRST timestamp and sequence for correct sorting.
    Merges with orders_after to get final leavesquantity.
    
    NOTE: orders_before may have multiple rows per order (different states).
    We take the FIRST occurrence (earliest timestamp/sequence) per order.
    
    Returns:
        DataFrame with columns:
            - orderid
            - timestamp (original placement time - FIRST occurrence)
            - sequence (original placement sequence - FIRST occurrence)
            - side (1=BUY, 2=SELL)
            - leavesquantity (available quantity from orders_after)
            - first_execution_time
            - last_execution_time
            - orderbookid
    """
    orders_before = partition_data['orders_before']
    orders_after = partition_data['orders_after']
    last_execution = partition_data['last_execution']
    
    # Standardize column names first
    if 'order_id' in orders_before.columns and 'orderid' not in orders_before.columns:
        orders_before = orders_before.rename(columns={'order_id': 'orderid'})
    if 'order_id' in orders_after.columns and 'orderid' not in orders_after.columns:
        orders_after = orders_after.rename(columns={'order_id': 'orderid'})
    
    # Get sweep orders from orders_before (for timestamp/sequence)
    # IMPORTANT: Take only FIRST occurrence per order (earliest timestamp/sequence)
    sweep_orders_before = orders_before[orders_before[ORDER_TYPE_COLUMN] == SWEEP_ORDER_TYPE].copy()
    sweep_orders_before = sweep_orders_before.sort_values(['orderid', 'timestamp', 'sequence'])
    sweep_orders_before = sweep_orders_before.groupby('orderid', as_index=False).first()
    
    # Get leavesquantity from orders_after (final state)
    sweep_orders_after = orders_after[orders_after[ORDER_TYPE_COLUMN] == SWEEP_ORDER_TYPE][['orderid', 'leavesquantity']].copy()
    
    # Merge to get both timestamp/sequence AND leavesquantity
    sweep_orders = sweep_orders_before.merge(
        sweep_orders_after[['orderid', 'leavesquantity']],
        on='orderid',
        how='left',
        suffixes=('', '_after')
    )
    
    # Use leavesquantity from orders_after if available
    if 'leavesquantity_after' in sweep_orders.columns:
        sweep_orders['leavesquantity'] = sweep_orders['leavesquantity_after'].fillna(sweep_orders['leavesquantity'])
        sweep_orders = sweep_orders.drop(columns=['leavesquantity_after'])
    
    # Rename security_code to orderbookid for consistency
    if 'security_code' in sweep_orders.columns:
        sweep_orders = sweep_orders.rename(columns={'security_code': 'orderbookid'})
    elif 'securitycode' in sweep_orders.columns:
        sweep_orders = sweep_orders.rename(columns={'securitycode': 'orderbookid'})
    
    # Merge with last_execution_time
    sweep_orders = sweep_orders.merge(
        last_execution[['orderid', 'first_execution_time', 'last_execution_time']],
        on='orderid',
        how='left'
    )
    
    # For orders without execution times, use a very wide time window
    sweep_orders['first_execution_time'] = sweep_orders['first_execution_time'].fillna(0)
    sweep_orders['last_execution_time'] = sweep_orders['last_execution_time'].fillna(float('inf'))
    
    # Select required columns
    sweep_orders = sweep_orders[[
        'orderid', 'timestamp', 'sequence', 'side', 'leavesquantity', 
        'first_execution_time', 'last_execution_time', 'orderbookid'
    ]].copy()
    
    # Ensure orderid is int64
    sweep_orders['orderid'] = sweep_orders['orderid'].astype('int64')
    
    # Sort by timestamp, then sequence (time priority based on PLACEMENT, not execution)
    sweep_orders = sweep_orders.sort_values(['timestamp', 'sequence']).reset_index(drop=True)
    
    return sweep_orders


def _prepare_all_orders_for_matching(partition_data):
    """
    Prepare ALL Centre Point orders for matching (including sweeps).
    
    This represents the pool of orders that can match with sweeps.
    Uses orders_before_matching.csv to get original timestamp/sequence.
    
    Returns:
        DataFrame with columns:
            - orderid
            - timestamp
            - sequence
            - side (1=BUY, 2=SELL)
            - quantity
            - orderbookid
            - bid (for fallback midpoint)
            - offer (for fallback midpoint)
    """
    orders_before = partition_data['orders_before']
    
    # Standardize column names first
    if 'order_id' in orders_before.columns and 'orderid' not in orders_before.columns:
        orders_before = orders_before.rename(columns={'order_id': 'orderid'})
    
    # Get ALL Centre Point orders (including type 2048)
    all_orders = orders_before[
        orders_before[ORDER_TYPE_COLUMN].isin(ELIGIBLE_MATCHING_ORDER_TYPES)
    ].copy()
    
    # Rename security_code to orderbookid for consistency
    if 'security_code' in all_orders.columns:
        all_orders = all_orders.rename(columns={'security_code': 'orderbookid'})
    elif 'securitycode' in all_orders.columns:
        all_orders = all_orders.rename(columns={'securitycode': 'orderbookid'})
    
    # Select required columns
    all_orders = all_orders[[
        'orderid', 'timestamp', 'sequence', 'side', 'quantity', 'orderbookid', 'bid', 'offer'
    ]].copy()
    
    # Ensure orderid is int64
    all_orders['orderid'] = all_orders['orderid'].astype('int64')
    
    # Sort by timestamp, then sequence for chronological processing
    all_orders = all_orders.sort_values(['timestamp', 'sequence']).reset_index(drop=True)
    
    return all_orders


def simulate_partition(partition_key, partition_data):
    """
    Simulate sweep matching for a partition.
    
    Args:
        partition_key: Partition identifier (e.g., "2024-09-05/110621")
        partition_data: Dictionary containing:
            - 'orders_before': DataFrame
            - 'orders_after': DataFrame
            - 'last_execution': DataFrame
            - 'nbbo': DataFrame or None
    
    Returns:
        Dictionary containing:
            - 'match_details': All individual matches
            - 'order_summary': Per incoming order summary
            - 'sweep_utilization': Per sweep order utilization
    """
    
    # Load and prepare orders
    sweep_orders, all_orders = load_and_prepare_orders(partition_data)
    
    if len(sweep_orders) == 0:
        print(f"  {partition_key}: No sweep orders, skipping")
        return None
    
    if len(all_orders) == 0:
        print(f"  {partition_key}: No matching orders, skipping")
        return None
    
    # Get NBBO data
    nbbo_data = partition_data.get('nbbo')
    
    # Prepare NBBO data if available
    if nbbo_data is not None and len(nbbo_data) > 0:
        nbbo_data = nbbo_data.sort_values('timestamp').reset_index(drop=True)
    
    # Run simulation
    results = simulate_sweep_matching(sweep_orders, all_orders, nbbo_data)
    
    print(f"  {partition_key}: {len(results['match_details']):,} matches, {len(sweep_orders):,} sweep orders")
    
    return results


def simulate_sweep_matching(sweep_orders, all_orders, nbbo_data):
    """
    Simulate sweep matching with CORRECT sweep-centric algorithm.
    
    CORRECT Algorithm:
        1. Process SWEEP orders chronologically by timestamp+sequence (placement time)
        2. For each sweep order:
            a. Get execution time window [first_execution_time, last_execution_time]
            b. Find ALL orders that arrived in that time window
            c. Exclude the sweep itself from matching candidates
            d. Filter for opposite side and same orderbookid
            e. Sort eligible orders by timestamp+sequence (time priority)
            f. Match sequentially until sweep is filled or no more eligible orders
            g. Record matches at midpoint price
    
    Args:
        sweep_orders: DataFrame of sweep orders (sorted by timestamp, sequence)
        all_orders: DataFrame of ALL Centre Point orders (including sweeps)
        nbbo_data: DataFrame with NBBO data (or None)
    
    Returns:
        Dictionary containing:
            - 'match_details': All individual matches
            - 'order_summary': Per sweep order summary
            - 'sweep_utilization': Per sweep order utilization
    """
    
    # Initialize tracking structures
    all_matches = []
    sweep_summaries = []
    
    # Track sweep usage
    sweep_usage = {int(orderid): {'matched_quantity': 0, 'num_matches': 0} 
                   for orderid in sweep_orders['orderid'].values}
    
    # Track remaining quantities for ALL orders (for matching)
    order_remaining = {int(orderid): qty for orderid, qty in 
                      zip(all_orders['orderid'].values, all_orders['quantity'].values)}
    
    # Pre-index all_orders by orderid for fast lookup
    all_orders_indexed = all_orders.set_index('orderid')
    
    # Process each SWEEP order chronologically (by placement time)
    for idx in range(len(sweep_orders)):
        sweep = sweep_orders.iloc[idx]
        # CRITICAL: Use .loc with column name and explicit astype to preserve int64
        # When using .iterrows(), pandas converts to float64 causing precision loss
        sweep_id = int(sweep_orders['orderid'].iloc[idx])
        sweep_side = int(sweep['side'])
        sweep_qty_available = sweep['leavesquantity']
        sweep_orderbookid = sweep['orderbookid']
        first_exec_time = sweep['first_execution_time']
        last_exec_time = sweep['last_execution_time']
        
        if sweep_qty_available <= 0:
            # No quantity to match
            sweep_summaries.append({
                'orderid': sweep_id,
                'timestamp': sweep['timestamp'],
                'side': sweep_side,
                'quantity': sweep_qty_available,
                'matched_quantity': 0,
                'remaining_quantity': 0,
                'fill_ratio': 0,
                'num_matches': 0,
                'orderbookid': sweep_orderbookid
            })
            continue
        
        # Find eligible orders in execution time window
        eligible_orders = all_orders[
            (all_orders['timestamp'] >= first_exec_time) &
            (all_orders['timestamp'] <= last_exec_time) &
            (all_orders['orderid'] != sweep_id) &  # CRITICAL: Exclude self
            (all_orders['orderbookid'] == sweep_orderbookid) &
            (all_orders['side'] != sweep_side)  # Opposite side
        ].copy()
        
        # Sort by time priority (timestamp, then sequence)
        eligible_orders = eligible_orders.sort_values(['timestamp', 'sequence'])
        
        # Match sequentially
        sweep_remaining_qty = sweep_qty_available
        sweep_matched_qty = 0
        sweep_num_matches = 0
        
        for _, order in eligible_orders.iterrows():
            if sweep_remaining_qty <= 0:
                break
            
            order_id = int(order['orderid'])  # Convert to int
            order_available = order_remaining.get(order_id, 0)
            
            if order_available <= 0:
                continue
            
            # Calculate match quantity
            match_qty = min(sweep_remaining_qty, order_available)
            
            # Get midpoint price
            midpoint = get_midpoint(
                nbbo_data=nbbo_data,
                timestamp=order['timestamp'],
                orderbookid=sweep_orderbookid,
                fallback_bid=order['bid'],
                fallback_offer=order['offer']
            )
            
            if midpoint is None:
                continue
            
            # Record match
            all_matches.append({
                'incoming_orderid': order_id,  # Keep this name for backward compat with metrics generator
                'sweep_orderid': sweep_id,
                'timestamp': order['timestamp'],
                'matched_quantity': match_qty,
                'price': midpoint,
                'orderbookid': sweep_orderbookid
            })
            
            # Update quantities
            sweep_remaining_qty -= match_qty
            order_remaining[order_id] -= match_qty
            sweep_matched_qty += match_qty
            sweep_num_matches += 1
        
        # Update sweep usage (ensure key exists)
        if sweep_id not in sweep_usage:
            sweep_usage[sweep_id] = {'matched_quantity': 0, 'num_matches': 0}
        
        sweep_usage[sweep_id]['matched_quantity'] = sweep_matched_qty
        sweep_usage[sweep_id]['num_matches'] = sweep_num_matches
        
        # Generate summary for this sweep
        sweep_summaries.append({
            'orderid': sweep_id,
            'timestamp': sweep['timestamp'],
            'side': sweep_side,
            'quantity': sweep_qty_available,
            'matched_quantity': sweep_matched_qty,
            'remaining_quantity': sweep_remaining_qty,
            'fill_ratio': sweep_matched_qty / sweep_qty_available if sweep_qty_available > 0 else 0,
            'num_matches': sweep_num_matches,
            'orderbookid': sweep_orderbookid
        })
    
    # Convert to DataFrames
    match_details_df = pd.DataFrame(all_matches) if all_matches else pd.DataFrame()
    order_summary_df = pd.DataFrame(sweep_summaries)
    sweep_utilization_df = _generate_sweep_utilization(sweep_orders, sweep_usage)
    
    return {
        'match_details': match_details_df,
        'order_summary': order_summary_df,
        'sweep_utilization': sweep_utilization_df
    }


def _generate_sweep_utilization(sweep_orders, sweep_usage):
    """
    Generate utilization report for sweep orders.
    
    Args:
        sweep_orders: DataFrame of sweep orders
        sweep_usage: Dict tracking usage per sweep order
    
    Returns:
        DataFrame with columns:
            - orderid
            - leavesquantity (available)
            - matched_quantity
            - remaining_quantity
            - utilization_ratio
            - num_matches
    """
    utilization = []
    
    for _, sweep in sweep_orders.iterrows():
        sweep_id = int(sweep['orderid'])
        available_qty = sweep['leavesquantity']
        
        if sweep_id not in sweep_usage:
            continue
            
        usage = sweep_usage[sweep_id]
        matched_qty = usage['matched_quantity']
        
        utilization.append({
            'orderid': sweep_id,
            'leavesquantity': available_qty,
            'matched_quantity': matched_qty,
            'remaining_quantity': available_qty - matched_qty,
            'utilization_ratio': matched_qty / available_qty if available_qty > 0 else 0,
            'num_matches': usage['num_matches']
        })
    
    return pd.DataFrame(utilization)
