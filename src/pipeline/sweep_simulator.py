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
    """Load and prepare sweep orders and all matching-eligible orders for simulation."""
    
    sweep_orders = _prepare_sweep_orders(partition_data)
    all_orders = _prepare_all_orders_for_matching(partition_data)
    
    return sweep_orders, all_orders


def _prepare_sweep_orders(partition_data):
    """Prepare sweep orders (type 2048) from ONLY qualifying orders in last_execution."""
    orders_after = partition_data['orders_after']
    last_execution = partition_data['last_execution']
    
    # Standardize column names
    if 'order_id' in orders_after.columns and 'orderid' not in orders_after.columns:
        orders_after = orders_after.rename(columns={'order_id': 'orderid'})
    
    # Start with last_execution (ONLY qualifying sweep orders)
    # Already has: orderid, first_execution_time, last_execution_time
    sweep_orders = last_execution.copy()
    
    # INNER JOIN with orders_after to get ALL order data
    sweep_orders_after = orders_after[orders_after[ORDER_TYPE_COLUMN] == SWEEP_ORDER_TYPE].copy()
    
    # Handle orderbookid naming variations
    if 'security_code' in sweep_orders_after.columns and 'orderbookid' not in sweep_orders_after.columns:
        sweep_orders_after = sweep_orders_after.rename(columns={'security_code': 'orderbookid'})
    elif 'securitycode' in sweep_orders_after.columns and 'orderbookid' not in sweep_orders_after.columns:
        sweep_orders_after = sweep_orders_after.rename(columns={'securitycode': 'orderbookid'})
    
    sweep_orders = sweep_orders.merge(
        sweep_orders_after,
        on='orderid',
        how='inner'  # INNER JOIN - only keep qualifying orders
    )
    
    # Select required columns (include all available)
    base_columns = [
        'orderid', 'timestamp', 'sequence', 'side', 'leavesquantity', 
        'first_execution_time', 'last_execution_time', 'orderbookid'
    ]
    
    # Add optional columns if they exist
    optional_columns = []
    if 'totalmatchedquantity' in sweep_orders.columns:
        optional_columns.append('totalmatchedquantity')
    if 'price' in sweep_orders.columns:
        optional_columns.append('price')
    
    # Insert optional columns after leavesquantity
    final_columns = base_columns[:5] + optional_columns + base_columns[5:]
    
    sweep_orders = sweep_orders[final_columns].copy()
    
    # Ensure orderid is int64
    sweep_orders[col.common.orderid] = sweep_orders[col.common.orderid].astype('int64')
    
    # Sort by timestamp, then sequence (time priority based on PLACEMENT)
    sweep_orders = sweep_orders.sort_values(['timestamp', 'sequence']).reset_index(drop=True)
    
    return sweep_orders


def _prepare_all_orders_for_matching(partition_data):
    """Prepare ALL Centre Point orders for matching (including sweeps)."""
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
    
    print(f"  {partition_key}: {len(results['match_details']):,} matches, {len(sweep_orders):,} sweep orders")
    
    return results


def simulate_sweep_matching(sweep_orders, all_orders, nbbo_data):
    """Simulate sweep matching with CORRECT sweep-centric algorithm."""
    
    # Initialize tracking structures
    all_matches = []
    sweep_summaries = []
    
    # Track sweep usage
    sweep_usage = {int(orderid): {'matched_quantity': 0, 'num_matches': 0} 
                   for orderid in sweep_orders[col.common.orderid].values}
    
    # Track remaining quantities for ALL orders (for matching)
    order_remaining = {int(orderid): qty for orderid, qty in 
                      zip(all_orders[col.common.orderid].values, all_orders[col.common.quantity].values)}
    
    # Pre-index all_orders by orderid for fast lookup
    all_orders_indexed = all_orders.set_index('orderid')
    
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
            
            # Get midpoint price
            midpoint = get_midpoint(
                nbbo_data=nbbo_data,
                timestamp=order[col.common.timestamp],
                orderbookid=sweep_orderbookid,
                fallback_bid=order[col.orders.bid],
                fallback_offer=order[col.orders.offer]
            )
            
            if midpoint is None:
                continue
            
            # Record match
            all_matches.append({
                'incoming_orderid': np.int64(order_id),  # Explicit numpy int64
                'sweep_orderid': np.int64(sweep_id),     # Explicit numpy int64
                'timestamp': order[col.common.timestamp],
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
            'timestamp': sweep[col.common.timestamp],
            'side': sweep_side,
            'quantity': sweep_qty_available,
            'matched_quantity': sweep_matched_qty,
            'remaining_quantity': sweep_remaining_qty,
            'fill_ratio': sweep_matched_qty / sweep_qty_available if sweep_qty_available > 0 else 0,
            'num_matches': sweep_num_matches,
            'orderbookid': sweep_orderbookid
        })
    
    # Convert to DataFrames with explicit dtypes to prevent float conversion
    if all_matches:
        match_details_df = pd.DataFrame(all_matches)
        # Force int64 IMMEDIATELY to prevent any float conversion
        match_details_df['sweep_orderid'] = pd.to_numeric(match_details_df['sweep_orderid'], downcast='signed', errors='coerce').astype('int64')
        match_details_df['incoming_orderid'] = pd.to_numeric(match_details_df['incoming_orderid'], downcast='signed', errors='coerce').astype('int64')
    else:
        match_details_df = pd.DataFrame()
    
    # DEBUG: Check after DataFrame creation
    if len(match_details_df) > 0:
        print(f"  [DEBUG] After DataFrame creation:")
        print(f"    First sweep_orderid: {match_details_df['sweep_orderid'].iloc[0]}")
        print(f"    Expected: 7904794000124134556")
        print(f"    Match? {match_details_df['sweep_orderid'].iloc[0] == 7904794000124134556}")
    
    order_summary_df = pd.DataFrame(sweep_summaries)
    sweep_utilization_df = _generate_sweep_utilization(sweep_orders, sweep_usage)
    
    # Generate simulated trades
    simulated_trades_df = generate_simulated_trades(match_details_df, sweep_orders, nbbo_data)
    
    return {
        'match_details': match_details_df,
        'order_summary': order_summary_df,
        'sweep_utilization': sweep_utilization_df,
        'simulated_trades': simulated_trades_df
    }


def generate_simulated_trades(match_details, sweep_orders, nbbo_data=None):
    """Generate simulated trades in 19-column format matching real trade files (2 rows per match)."""
    if len(match_details) == 0:
        return pd.DataFrame(columns=[
            'EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode', 'orderid',
            'dealsource', 'dealsourcedecoded', 'exchangeinfo', 'matchgroupid',
            'nationalbidpricesnapshot', 'nationalofferpricesnapshot', 'tradeprice', 'quantity',
            'side', 'sidedecoded', 'participantid', 'passiveaggressive', 'row_num'
        ])
    
    # DEBUG: Check what we received
    print(f"  [DEBUG] generate_simulated_trades received:")
    print(f"    First sweep_orderid: {match_details['sweep_orderid'].iloc[0]}")
    print(f"    Type: {match_details['sweep_orderid'].dtype}")
    print(f"    Expected: 7904794000124134556")
    print(f"    Match? {match_details['sweep_orderid'].iloc[0] == 7904794000124134556}")
    
    # Extract date from first match timestamp
    first_timestamp = match_details[col.common.timestamp].iloc[0]
    tradedate = pd.to_datetime(first_timestamp, unit='ns').strftime('%Y-%m-%d')
    
    # Get sweep order sides
    sweep_side_map = sweep_orders.set_index('orderid')[col.common.side].to_dict()
    
    # Sort matches by timestamp for consistent ordering
    matches_sorted = match_details.sort_values('timestamp').reset_index(drop=True)
    
    # DEBUG: Check what's in match_details
    print(f"  [DEBUG] match_details has {len(matches_sorted)} rows")
    print(f"  [DEBUG] match_details columns: {matches_sorted.columns.tolist()}")
    if len(matches_sorted) > 0:
        print(f"  [DEBUG] First 3 sweep_orderids: {matches_sorted['sweep_orderid'].head(3).tolist()}")
        print(f"  [DEBUG] First 3 incoming_orderids: {matches_sorted['incoming_orderid'].head(3).tolist()}")
    
    # Prepare NBBO data for lookup (sort by timestamp)
    if nbbo_data is not None and len(nbbo_data) > 0:
        nbbo_sorted = nbbo_data.sort_values('timestamp').reset_index(drop=True)
    else:
        nbbo_sorted = None
    
    # Generate rows (2 per match)
    rows = []
    row_counter = 1
    
    # CRITICAL: Use itertuples() instead of iterrows() to preserve int64 precision
    # iterrows() converts int64 to float64, causing precision loss for large integers
    for match in matches_sorted.itertuples(index=True, name='Match'):
        sweep_orderid = match.sweep_orderid
        incoming_orderid = match.incoming_orderid
        timestamp = match.timestamp
        quantity = match.matched_quantity
        price = match.price
        security_code = match.orderbookid
        idx = match.Index
        
        # DEBUG: Print first match details
        if idx == 0:
            print(f"  [DEBUG] First match:")
            print(f"    sweep_orderid: {sweep_orderid} (type: {type(sweep_orderid)})")
            print(f"    incoming_orderid: {incoming_orderid} (type: {type(incoming_orderid)})")
        
        # Generate unique matchgroupid (use base + match index)
        matchgroupid = 7904794000999000001 + idx
        
        # Get aggressor side
        aggressor_side = sweep_side_map.get(sweep_orderid, 2)
        passive_side = 1 if aggressor_side == 2 else 2
        
        # DEBUG first row
        if idx == 0:
            print(f"  [DEBUG] Before appending to rows:")
            print(f"    sweep_orderid: {sweep_orderid} (type: {type(sweep_orderid)})")
            print(f"    incoming_orderid: {incoming_orderid} (type: {type(incoming_orderid)})")
        
        # Get NBBO snapshots (most recent before this timestamp)
        bid_snapshot = 0
        offer_snapshot = 0
        if nbbo_sorted is not None:
            recent_nbbo = nbbo_sorted[nbbo_sorted[col.common.timestamp] <= timestamp]
            if len(recent_nbbo) > 0:
                latest_nbbo = recent_nbbo.iloc[-1]
                bid_snapshot = latest_nbbo.get('bidprice', 0)
                offer_snapshot = latest_nbbo.get('offerprice', 0)
        
        # Row 1: Aggressor (sweep order)
        rows.append({
            'EXCHANGE': 3,
            'sequence': row_counter,
            'tradedate': tradedate,
            'tradetime': timestamp,
            'securitycode': security_code,
            'orderid': sweep_orderid,
            'dealsource': 99,
            'dealsourcedecoded': 'Simulated',
            'exchangeinfo': '',
            'matchgroupid': matchgroupid,
            'nationalbidpricesnapshot': int(bid_snapshot),
            'nationalofferpricesnapshot': int(offer_snapshot),
            'tradeprice': int(price),
            'quantity': int(quantity),
            'side': int(aggressor_side),
            'sidedecoded': 'Buy' if aggressor_side == 1 else 'Sell',
            'participantid': 0,
            'passiveaggressive': 1,
            'row_num': row_counter
        })
        row_counter += 1
        
        # Row 2: Passive (incoming order)
        rows.append({
            'EXCHANGE': 3,
            'sequence': row_counter,
            'tradedate': tradedate,
            'tradetime': timestamp,
            'securitycode': security_code,
            'orderid': incoming_orderid,
            'dealsource': 99,
            'dealsourcedecoded': 'Simulated',
            'exchangeinfo': '',
            'matchgroupid': matchgroupid,
            'nationalbidpricesnapshot': int(bid_snapshot),
            'nationalofferpricesnapshot': int(offer_snapshot),
            'tradeprice': int(price),
            'quantity': int(quantity),
            'side': int(passive_side),
            'sidedecoded': 'Buy' if passive_side == 1 else 'Sell',
            'participantid': 0,
            'passiveaggressive': 0,
            'row_num': row_counter
        })
        row_counter += 1
    
    # Create DataFrame
    trades_df = pd.DataFrame(rows)
    
    # Ensure correct data types (prevent scientific notation)
    int_columns = ['EXCHANGE', 'sequence', 'tradetime', 'securitycode', 'orderid', 
                   'dealsource', 'matchgroupid', 'nationalbidpricesnapshot', 
                   'nationalofferpricesnapshot', 'tradeprice', 'quantity', 
                   'side', 'participantid', 'passiveaggressive', 'row_num']
    
    for col in int_columns:
        if col in trades_df.columns:
            trades_df[col] = trades_df[col].astype('int64')
    
    return trades_df


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
