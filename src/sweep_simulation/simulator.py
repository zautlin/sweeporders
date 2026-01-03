"""
Sweep Order Matching Simulator

This module simulates the matching of sweep orders (type 2048) with incoming orders
(types 64, 256, 4096, 4098) to compare simulated execution with actual execution.
"""

import pandas as pd
import numpy as np
from . import nbbo_provider

# Constants
SWEEP_ORDER_TYPE = 2048
INCOMING_ORDER_TYPES = {64, 256, 4096, 4098}
ORDER_TYPE_COLUMN = 'exchangeordertype'


def load_and_prepare_orders(partition_key, partition_data):
    """
    Load and prepare sweep and incoming orders for simulation.
    
    Args:
        partition_key: Partition identifier (e.g., "2024-09-05/110621")
        partition_data: Dictionary containing:
            - 'orders_before': DataFrame from orders_before_lob.csv
            - 'orders_after': DataFrame from orders_after_lob.csv
            - 'last_execution': DataFrame from last_execution_time.csv
    
    Returns:
        Tuple of (sweep_orders, incoming_orders)
    """
    
    sweep_orders = _prepare_sweep_orders(partition_data)
    incoming_orders = _prepare_incoming_orders(partition_data)
    
    return sweep_orders, incoming_orders


def _prepare_sweep_orders(partition_data):
    """
    Prepare sweep orders (type 2048) from orders_after_lob.csv.
    
    Returns:
        DataFrame with columns:
            - orderid
            - side (1=BUY, 2=SELL)
            - leavesquantity (available quantity)
            - first_execution_time
            - last_execution_time
            - orderbookid
    """
    orders_after = partition_data['orders_after']
    last_execution = partition_data['last_execution']
    
    # Filter for sweep orders
    sweep_orders = orders_after[orders_after[ORDER_TYPE_COLUMN] == SWEEP_ORDER_TYPE].copy()
    
    # Rename order_id to orderid for consistency
    if 'order_id' in sweep_orders.columns:
        sweep_orders = sweep_orders.rename(columns={'order_id': 'orderid'})
    
    # Rename security_code to orderbookid for consistency
    if 'security_code' in sweep_orders.columns:
        sweep_orders = sweep_orders.rename(columns={'security_code': 'orderbookid'})
    
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
        'orderid', 'side', 'leavesquantity', 
        'first_execution_time', 'last_execution_time', 'orderbookid'
    ]].copy()
    
    # Sort by first_execution_time for time priority
    sweep_orders = sweep_orders.sort_values('first_execution_time').reset_index(drop=True)
    
    return sweep_orders


def _prepare_incoming_orders(partition_data):
    """
    Prepare incoming orders (types 64, 256, 4096, 4098) from orders_before_lob.csv.
    
    Returns:
        DataFrame with columns:
            - orderid
            - timestamp
            - side (1=BUY, 2=SELL)
            - quantity
            - orderbookid
            - bid (for fallback midpoint)
            - offer (for fallback midpoint)
    """
    orders_before = partition_data['orders_before']
    
    # Filter for incoming order types
    incoming_orders = orders_before[
        orders_before[ORDER_TYPE_COLUMN].isin(INCOMING_ORDER_TYPES)
    ].copy()
    
    # Rename order_id to orderid for consistency
    if 'order_id' in incoming_orders.columns:
        incoming_orders = incoming_orders.rename(columns={'order_id': 'orderid'})
    
    # Rename security_code to orderbookid for consistency
    if 'security_code' in incoming_orders.columns:
        incoming_orders = incoming_orders.rename(columns={'security_code': 'orderbookid'})
    
    # Select required columns
    incoming_orders = incoming_orders[[
        'orderid', 'timestamp', 'side', 'quantity', 'orderbookid', 'bid', 'offer'
    ]].copy()
    
    # Sort by timestamp for chronological processing
    incoming_orders = incoming_orders.sort_values('timestamp').reset_index(drop=True)
    
    return incoming_orders


def simulate_sweep_matching(partition_key, sweep_orders, incoming_orders, nbbo_data):
    """
    Simulate sweep matching for a partition.
    
    Algorithm:
        1. Process incoming orders chronologically by timestamp
        2. For each incoming order:
            a. Find sweep orders active at that timestamp
            b. Filter for opposite side
            c. Sort by time priority (first_execution_time)
            d. Match sequentially until filled or no more sweeps
            e. Record matches at midpoint price
    
    Args:
        partition_key: Partition identifier
        sweep_orders: DataFrame of sweep orders
        incoming_orders: DataFrame of incoming orders
        nbbo_data: DataFrame with NBBO data (or None)
    
    Returns:
        Dictionary containing:
            - 'match_details': All individual matches
            - 'order_summary': Per incoming order summary
            - 'sweep_utilization': Per sweep order utilization
    """
    
    # Prepare NBBO data if available
    if nbbo_data is not None and len(nbbo_data) > 0:
        nbbo_data = nbbo_data.sort_values('timestamp').reset_index(drop=True)
    
    # Initialize tracking structures
    all_matches = []
    order_summaries = []
    sweep_usage = {int(orderid): {'matched_quantity': 0, 'num_matches': 0} 
                   for orderid in sweep_orders['orderid'].values}
    
    # Track remaining quantities for sweep orders
    sweep_remaining = {int(orderid): qty for orderid, qty in 
                      zip(sweep_orders['orderid'].values, sweep_orders['leavesquantity'].values)}
    
    # Process each incoming order chronologically
    for idx, incoming in incoming_orders.iterrows():
        matches, summary = _match_incoming_order(
            incoming, 
            sweep_orders, 
            sweep_remaining,
            nbbo_data
        )
        
        # Record matches
        all_matches.extend(matches)
        order_summaries.append(summary)
        
        # Update sweep usage tracking
        for match in matches:
            sweep_id = match['sweep_orderid']
            sweep_usage[sweep_id]['matched_quantity'] += match['matched_quantity']
            sweep_usage[sweep_id]['num_matches'] += 1
    
    # Convert to DataFrames
    match_details_df = pd.DataFrame(all_matches) if all_matches else pd.DataFrame()
    order_summary_df = pd.DataFrame(order_summaries)
    sweep_utilization_df = _generate_sweep_utilization(sweep_orders, sweep_usage)
    
    return {
        'match_details': match_details_df,
        'order_summary': order_summary_df,
        'sweep_utilization': sweep_utilization_df
    }


def _match_incoming_order(incoming, sweep_orders, sweep_remaining, nbbo_data):
    """
    Match a single incoming order with active sweep orders.
    
    Args:
        incoming: Series representing incoming order
        sweep_orders: DataFrame of all sweep orders
        sweep_remaining: Dict tracking remaining quantity per sweep order
        nbbo_data: DataFrame with NBBO data (or None)
    
    Returns:
        Tuple of (matches, summary)
            matches: List of match dictionaries
            summary: Summary dict for this incoming order
    """
    incoming_id = incoming['orderid']
    incoming_ts = incoming['timestamp']
    incoming_side = incoming['side']
    incoming_qty = incoming['quantity']
    orderbookid = incoming['orderbookid']
    
    # Find active sweep orders at this timestamp
    active_sweeps = sweep_orders[
        (sweep_orders['first_execution_time'] <= incoming_ts) &
        (sweep_orders['last_execution_time'] >= incoming_ts)
    ]
    
    # Filter for opposite side (BUY incoming matches SELL sweeps, vice versa)
    opposite_side = 2 if incoming_side == 1 else 1
    matching_sweeps = active_sweeps[active_sweeps['side'] == opposite_side]
    
    # Sort by time priority (earliest first_execution_time)
    matching_sweeps = matching_sweeps.sort_values('first_execution_time')
    
    # Match sequentially
    matches = []
    remaining_qty = incoming_qty
    total_matched_qty = 0
    num_matches = 0
    
    for _, sweep in matching_sweeps.iterrows():
        if remaining_qty <= 0:
            break
        
        sweep_id = int(sweep['orderid'])
        sweep_available = sweep_remaining.get(sweep_id, 0)
        
        if sweep_available <= 0:
            continue
        
        # Calculate match quantity
        match_qty = min(remaining_qty, sweep_available)
        
        # Get midpoint price
        midpoint = nbbo_provider.get_midpoint(
            nbbo_data=nbbo_data,
            timestamp=incoming_ts,
            orderbookid=orderbookid,
            fallback_bid=incoming['bid'],
            fallback_offer=incoming['offer']
        )
        
        # Record match
        matches.append({
            'incoming_orderid': incoming_id,
            'sweep_orderid': sweep_id,
            'timestamp': incoming_ts,
            'matched_quantity': match_qty,
            'price': midpoint,
            'orderbookid': orderbookid
        })
        
        # Update quantities
        remaining_qty -= match_qty
        sweep_remaining[sweep_id] -= match_qty
        total_matched_qty += match_qty
        num_matches += 1
    
    # Generate summary for this incoming order
    summary = {
        'orderid': incoming_id,
        'timestamp': incoming_ts,
        'side': incoming_side,
        'quantity': incoming_qty,
        'matched_quantity': total_matched_qty,
        'remaining_quantity': remaining_qty,
        'fill_ratio': total_matched_qty / incoming_qty if incoming_qty > 0 else 0,
        'num_matches': num_matches,
        'orderbookid': orderbookid
    }
    
    return matches, summary


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
