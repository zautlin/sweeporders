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
from config.column_schema import col

# Constants
SWEEP_ORDER_TYPE = 2048
ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}  # ALL CP types, including sweep-to-sweep
ORDER_TYPE_COLUMN = 'exchangeordertype'


def get_midpoint(nbbo_data, timestamp, orderbookid, fallback_bid, fallback_offer):
    """Get midpoint price at given timestamp."""
    
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
    
    # Filter for this orderbookid and timestamp <= target
    valid_quotes = nbbo_data[
        (nbbo_data[col.common.orderbookid] == orderbookid) &
        (nbbo_data[col.common.timestamp] <= timestamp)
    ]
    
    if len(valid_quotes) == 0:
        return None
    
    # Get most recent quote
    latest = valid_quotes.iloc[-1]
    
    # Calculate midpoint
    midpoint = (latest[col.common.bid] + latest[col.common.offer]) / 2.0
    
    return midpoint


def _get_nbbo_at_timestamp(nbbo_data, timestamp, orderbookid):
    """Get NBBO bid/offer/timestamp at or before given timestamp.
    
    Args:
        nbbo_data: Sorted NBBO DataFrame (must be pre-sorted by timestamp)
        timestamp: Match timestamp (nanoseconds)
        orderbookid: Security identifier
    
    Returns:
        tuple: (bid, offer, nbbo_timestamp) or (0, 0, 0) if not found
    """
    if nbbo_data is None or len(nbbo_data) == 0:
        return 0, 0, 0
    
    # Filter for this security at or before timestamp
    relevant_nbbo = nbbo_data[
        (nbbo_data[col.common.orderbookid] == orderbookid) &
        (nbbo_data[col.common.timestamp] <= timestamp)
    ]
    
    if len(relevant_nbbo) == 0:
        return 0, 0, 0
    
    # Get most recent NBBO
    latest = relevant_nbbo.iloc[-1]
    bid = latest.get('bid', 0)
    offer = latest.get('offer', 0)
    nbbo_ts = latest[col.common.timestamp]
    
    return int(bid), int(offer), int(nbbo_ts)


def _calculate_midpoint(bid, offer, fallback_bid=0, fallback_offer=0):
    """Calculate midpoint from NBBO with fallback.
    
    Args:
        bid: NBBO bid price
        offer: NBBO offer price
        fallback_bid: Fallback bid if NBBO unavailable
        fallback_offer: Fallback offer if NBBO unavailable
    
    Returns:
        float: Midpoint price or None if cannot calculate
    """
    # Try NBBO first
    if bid > 0 and offer > 0:
        return (bid + offer) / 2
    
    # Fallback to order bid/offer
    if fallback_bid > 0 and fallback_offer > 0:
        return (fallback_bid + fallback_offer) / 2
    
    return None


def load_and_prepare_orders(partition_data):
    """Load and prepare sweep orders and all matching-eligible orders for simulation."""
    
    sweep_orders = _prepare_sweep_orders(partition_data)
    all_orders = _prepare_all_orders_for_matching(partition_data)
    
    return sweep_orders, all_orders


def _prepare_sweep_orders(partition_data):
    """Prepare sweep orders (type 2048) from ONLY qualifying orders in last_execution."""
    orders_after = partition_data['orders_after']
    last_execution = partition_data['last_execution']
    
    # No normalization needed - Stage 1 already normalized column names
    
    # Start with last_execution (ONLY qualifying sweep orders)
    # Already has: orderid, first_execution_time, last_execution_time
    sweep_orders = last_execution.copy()
    
    # INNER JOIN with orders_after to get ALL order data
    sweep_orders_after = orders_after[orders_after[ORDER_TYPE_COLUMN] == SWEEP_ORDER_TYPE].copy()
    
    sweep_orders = sweep_orders.merge(
        sweep_orders_after,
        on='orderid',
        how='inner'  # INNER JOIN - only keep qualifying orders
    )
    
    # Define required columns using col.* accessors for schema independence
    # Note: After normalization, 'totalmatchedquantity' becomes 'matched_quantity'
    required_columns = [
        col.common.orderid,
        col.common.timestamp,
        col.common.sequence,
        col.common.side,
        col.orders.leaves_quantity,
        'matched_quantity',  # Required for metrics comparison (normalized from 'totalmatchedquantity')
        col.common.price,  # Required for trade generation
        'first_execution_time',  # Calculated in data_processor
        'last_execution_time',   # Calculated in data_processor
        col.common.orderbookid,
    ]
    
    # Validate all required columns exist
    missing = set(required_columns) - set(sweep_orders.columns)
    if missing:
        raise ValueError(
            f"Missing required columns for sweep order simulation: {missing}\n"
            f"Available columns: {sorted(sweep_orders.columns)}"
        )
    
    sweep_orders = sweep_orders[required_columns].copy()
    
    # Ensure orderid is int64
    sweep_orders[col.common.orderid] = sweep_orders[col.common.orderid].astype('int64')
    
    # Sort by timestamp, then sequence (time priority based on PLACEMENT)
    sweep_orders = sweep_orders.sort_values(['timestamp', 'sequence']).reset_index(drop=True)
    
    return sweep_orders


def _prepare_all_orders_for_matching(partition_data):
    """Prepare ALL Centre Point orders for matching (including sweeps)."""
    orders_before = partition_data['orders_before']
    
    # No normalization needed - Stage 1 already normalized column names
    
    # Get ALL Centre Point orders (including type 2048)
    all_orders = orders_before[
        orders_before[ORDER_TYPE_COLUMN].isin(ELIGIBLE_MATCHING_ORDER_TYPES)
    ].copy()
    
    # Define required columns using col.* accessors for schema independence
    required_columns = [
        col.common.orderid,
        col.common.timestamp,
        col.common.sequence,
        col.common.side,
        col.common.quantity,
        col.common.orderbookid,
        col.orders.bid,
        col.orders.offer,
    ]
    
    # Validate all required columns exist
    missing = set(required_columns) - set(all_orders.columns)
    if missing:
        raise ValueError(
            f"Missing required columns for matching orders: {missing}\n"
            f"Available columns: {sorted(all_orders.columns)}"
        )
    
    all_orders = all_orders[required_columns].copy()
    
    # Ensure orderid is int64
    all_orders[col.common.orderid] = all_orders[col.common.orderid].astype('int64')
    
    # Sort by timestamp, then sequence for chronological processing
    all_orders = all_orders.sort_values(['timestamp', 'sequence']).reset_index(drop=True)
    
    return all_orders


def simulate_partition(partition_key, partition_data):
    """Simulate sweep matching for a partition."""
    
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
    
    # Calculate matches from simulated trades (2 rows per match)
    num_matches = len(results['simulated_trades']) // 2 if len(results['simulated_trades']) > 0 else 0
    print(f"  {partition_key}: {num_matches:,} matches, {len(sweep_orders):,} sweep orders")
    
    return results


def simulate_sweep_matching(sweep_orders, all_orders, nbbo_data):
    """Simulate sweep matching and generate full trade rows directly (2 rows per match)."""
    
    # Initialize tracking structures
    simulated_trades = []  # Will store FULL TRADE ROWS (2 per match)
    sweep_summaries = []
    
    # Track sweep usage
    sweep_usage = {int(orderid): {'matched_quantity': 0, 'num_matches': 0} 
                   for orderid in sweep_orders[col.common.orderid].values}
    
    # Track remaining quantities for ALL orders (for matching)
    order_remaining = {int(orderid): qty for orderid, qty in 
                      zip(all_orders[col.common.orderid].values, all_orders[col.common.quantity].values)}
    
    # Pre-index all_orders by orderid for fast lookup
    all_orders_indexed = all_orders.set_index('orderid')
    
    # Counters for trade generation
    row_counter = 1
    match_counter = 0
    base_matchgroupid = 7904794000999000001
    
    # Extract tradedate once from first sweep order
    if len(sweep_orders) > 0:
        first_timestamp = sweep_orders[col.common.timestamp].iloc[0]
        tradedate = pd.to_datetime(first_timestamp, unit='ns').strftime('%Y-%m-%d')
    else:
        tradedate = None
    
    # Pre-sort NBBO for efficient lookups
    if nbbo_data is not None and len(nbbo_data) > 0:
        nbbo_sorted = nbbo_data.sort_values('timestamp').reset_index(drop=True)
    else:
        nbbo_sorted = None
    
    # Process each SWEEP order chronologically (by placement time)
    for idx in range(len(sweep_orders)):
        sweep = sweep_orders.iloc[idx]
        # CRITICAL: Use .loc with column name and explicit astype to preserve int64
        # When using .iterrows(), pandas converts to float64 causing precision loss
        sweep_id = int(sweep_orders[col.common.orderid].iloc[idx])
        sweep_side = int(sweep[col.common.side])
        sweep_qty_available = sweep[col.orders.leaves_quantity]
        sweep_orderbookid = sweep[col.common.orderbookid]
        first_exec_time = sweep['first_execution_time']
        last_exec_time = sweep['last_execution_time']
        
        if sweep_qty_available <= 0:
            # No quantity to match
            sweep_summaries.append({
                'orderid': sweep_id,
                'timestamp': sweep[col.common.timestamp],
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
            (all_orders[col.common.timestamp] >= first_exec_time) &
            (all_orders[col.common.timestamp] <= last_exec_time) &
            (all_orders[col.common.orderid] != sweep_id) &  # CRITICAL: Exclude self
            (all_orders[col.common.orderbookid] == sweep_orderbookid) &
            (all_orders[col.common.side] != sweep_side)  # Opposite side
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
            
            order_id = int(order[col.common.orderid])  # Convert to int
            order_available = order_remaining.get(order_id, 0)
            
            if order_available <= 0:
                continue
            
            # Calculate match quantity
            match_qty = min(sweep_remaining_qty, order_available)
            
            # Get match timestamp (when incoming order arrives)
            match_timestamp = order[col.common.timestamp]
            
            # Get NBBO at match time
            nbbo_bid, nbbo_offer, _ = _get_nbbo_at_timestamp(
                nbbo_sorted,
                match_timestamp,
                sweep_orderbookid
            )
            
            # Calculate midpoint for trade price
            midpoint = _calculate_midpoint(
                nbbo_bid,
                nbbo_offer,
                fallback_bid=order[col.orders.bid],
                fallback_offer=order[col.orders.offer]
            )
            
            if midpoint is None:
                continue  # Cannot price this match
            
            # Generate unique matchgroupid
            matchgroupid = base_matchgroupid + match_counter
            match_counter += 1
            
            # Get sides
            sweep_side_val = int(sweep[col.common.side])
            order_side = int(order[col.common.side])
            
            # === ROW 1: AGGRESSOR (Sweep Order) ===
            simulated_trades.append({
                'EXCHANGE': 3,
                'sequence': row_counter,
                'tradedate': tradedate,
                'tradetime': match_timestamp,
                'securitycode': sweep_orderbookid,
                'orderid': sweep_id,
                'dealsource': 99,
                'exchangeinfo': '',
                'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid,
                'nationalofferpricesnapshot': nbbo_offer,
                'tradeprice': int(midpoint),
                'quantity': int(match_qty),
                'side': sweep_side_val,
                'participantid': 0,
                'passiveaggressive': 1,  # Aggressor
                'row_num': row_counter,
            })
            row_counter += 1
            
            # === ROW 2: PASSIVE (Incoming Order) ===
            simulated_trades.append({
                'EXCHANGE': 3,
                'sequence': row_counter,
                'tradedate': tradedate,
                'tradetime': match_timestamp,
                'securitycode': sweep_orderbookid,
                'orderid': order_id,
                'dealsource': 99,
                'exchangeinfo': '',
                'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid,
                'nationalofferpricesnapshot': nbbo_offer,
                'tradeprice': int(midpoint),
                'quantity': int(match_qty),
                'side': order_side,
                'participantid': 0,
                'passiveaggressive': 0,  # Passive
                'row_num': row_counter,
            })
            row_counter += 1
            
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
            'timestamp': sweep[col.common.timestamp],
            'side': sweep_side,
            'quantity': sweep_qty_available,
            'matched_quantity': sweep_matched_qty,
            'remaining_quantity': sweep_remaining_qty,
            'fill_ratio': sweep_matched_qty / sweep_qty_available if sweep_qty_available > 0 else 0,
            'num_matches': sweep_num_matches,
            'orderbookid': sweep_orderbookid
        })
    
    # Convert to DataFrame
    if simulated_trades:
        simulated_trades_df = pd.DataFrame(simulated_trades)
        
        # Ensure correct data types
        int_columns = [
            'EXCHANGE', 'sequence', 'tradetime', 'securitycode', 'orderid',
            'dealsource', 'matchgroupid', 'nationalbidpricesnapshot',
            'nationalofferpricesnapshot', 'tradeprice', 'quantity', 'side',
            'participantid', 'passiveaggressive', 'row_num'
        ]
        
        for col_name in int_columns:
            if col_name in simulated_trades_df.columns:
                simulated_trades_df[col_name] = simulated_trades_df[col_name].astype('int64')
    else:
        # Empty case - no matches
        simulated_trades_df = pd.DataFrame(columns=[
            'EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode',
            'orderid', 'dealsource', 'exchangeinfo', 'matchgroupid',
            'nationalbidpricesnapshot', 'nationalofferpricesnapshot',
            'tradeprice', 'quantity', 'side', 'participantid',
            'passiveaggressive', 'row_num'
        ])
    
    order_summary_df = pd.DataFrame(sweep_summaries)
    sweep_utilization_df = _generate_sweep_utilization(sweep_orders, sweep_usage)
    
    return {
        'order_summary': order_summary_df,
        'sweep_utilization': sweep_utilization_df,
        'simulated_trades': simulated_trades_df
    }


def _generate_sweep_utilization(sweep_orders, sweep_usage):
    """Generate utilization report for sweep orders."""
    utilization = []
    
    for _, sweep in sweep_orders.iterrows():
        sweep_id = int(sweep[col.common.orderid])
        available_qty = sweep[col.orders.leaves_quantity]
        
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
