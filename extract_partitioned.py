"""
Simple End-to-End Partitioned Pipeline
Extracts, simulates, and compares Centre Point orders with sweep matching.
Outputs organized into processed/ (intermediate) and outputs/ (final results).
"""

import pandas as pd
from pathlib import Path
from typing import Dict
import time

# Configuration
INPUT_FILES = {
    'orders': 'data/raw/orders/drr_orders.csv',
    'trades': 'data/raw/trades/drr_trades_segment_1.csv',
    'nbbo': 'data/raw/nbbo/nbbo.csv',
    'session': 'data/raw/session/session.csv',
    'reference': 'data/raw/reference/ob.csv',
    'participants': 'data/raw/participants/par.csv',
}

# Directory structure
PROCESSED_DIR = 'data/processed'  # Intermediate files: raw data, LOB states
OUTPUTS_DIR = 'data/outputs'      # Final outputs: simulation results, comparisons

# Order types
CENTRE_POINT_ORDER_TYPES = [64, 256, 2048, 4096, 4098]
SWEEP_ORDER_TYPE = 2048
INCOMING_ORDER_TYPES = [64, 256, 4096, 4098]
CHUNK_SIZE = 100000

# Column name mapping for schema independence
COLUMN_MAPPING = {
    # Orders file columns
    'orders': {
        'order_id': 'order_id',
        'timestamp': 'timestamp',
        'sequence': 'sequence',
        'order_type': 'exchangeordertype',
        'security_code': 'securitycode',
        'side': 'side',
        'quantity': 'quantity',
        'price': 'price',
        'bid': 'bid',
        'offer': 'offer',
        'leaves_quantity': 'leavesquantity',
        'matched_quantity': 'totalmatchedquantity',
    },
    # Trades file columns
    'trades': {
        'order_id': 'orderid',
        'trade_time': 'tradetime',
        'trade_price': 'tradeprice',
        'quantity': 'quantity',
    },
    # NBBO file columns
    'nbbo': {
        'timestamp': 'tradedate',
        'security_code': 'orderbookid',
        'bid': 'bidprice',
        'offer': 'offerprice',
    },
    # Session file columns
    'session': {
        'timestamp': 'TradeDate',
    },
    # Reference file columns
    'reference': {
        'timestamp': 'TradeDate',
    },
    # Participants file columns
    'participants': {
        'timestamp': 'TradeDate',
    },
}


def add_date_column(df, timestamp_col):
    """Add date column from timestamp."""
    df['date'] = pd.to_datetime(df[timestamp_col], unit='ns').dt.strftime('%Y-%m-%d')
    return df


def col(file_type, logical_name):
    """Get actual column name from logical name using mapping."""
    mapped = COLUMN_MAPPING.get(file_type, {}).get(logical_name)
    return mapped if mapped is not None else logical_name


def extract_orders(input_file, processed_dir):
    """Extract Centre Point orders and partition by date/security."""
    print(f"\n[1/11] Extracting Centre Point orders from {input_file}...")
    
    # Get column names
    order_type_col = col('orders', 'order_type')
    timestamp_col = col('orders', 'timestamp')
    security_col = col('orders', 'security_code')
    
    orders_list = []
    total_rows = 0
    
    # Read in chunks
    for chunk in pd.read_csv(input_file, chunksize=CHUNK_SIZE, low_memory=False):
        total_rows += len(chunk)
        
        # Filter for Centre Point orders
        cp_chunk = chunk[chunk[order_type_col].isin(CENTRE_POINT_ORDER_TYPES)].copy()
        
        if len(cp_chunk) > 0:
            cp_chunk = add_date_column(cp_chunk, timestamp_col)
            # Standardize column name
            if security_col != 'security_code':
                cp_chunk = cp_chunk.rename(columns={security_col: 'security_code'})
            orders_list.append(cp_chunk)
    
    if not orders_list:
        print("  No Centre Point orders found!")
        return {}
    
    orders = pd.concat(orders_list, ignore_index=True)
    print(f"  Found {len(orders):,} Centre Point orders from {total_rows:,} total rows")
    
    # Partition by date/security
    partitions = {}
    for (date, security_code), group_df in orders.groupby(['date', 'security_code']):
        partition_key = f"{date}/{security_code}"
        partitions[partition_key] = group_df
        
        # Save to processed directory
        partition_dir = Path(processed_dir) / date / str(security_code)
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        partition_file = partition_dir / "centrepoint_orders_raw.csv.gz"
        group_df.to_csv(partition_file, index=False, compression='gzip')
        
        size_mb = partition_file.stat().st_size / (1024 * 1024)
        print(f"  {partition_key}: {len(group_df):,} orders ({size_mb:.2f} MB)")
    
    return partitions


def extract_trades(input_file, orders_by_partition, processed_dir):
    """Extract trades matching order_ids from partitions."""
    print(f"\n[2/11] Extracting matching trades from {input_file}...")
    
    # Get column names
    order_id_col_orders = col('orders', 'order_id')
    order_id_col_trades = col('trades', 'order_id')
    trade_time_col = col('trades', 'trade_time')
    
    # Collect all order IDs
    all_order_ids = set()
    partition_order_ids = {}
    
    for partition_key, orders_df in orders_by_partition.items():
        order_ids = set(orders_df[order_id_col_orders].unique())
        partition_order_ids[partition_key] = order_ids
        all_order_ids.update(order_ids)
    
    print(f"  Looking for {len(all_order_ids):,} order IDs across {len(orders_by_partition)} partitions")
    
    # Read and filter trades
    trades_list = []
    total_rows = 0
    
    for chunk in pd.read_csv(input_file, chunksize=CHUNK_SIZE, low_memory=False):
        total_rows += len(chunk)
        matched_chunk = chunk[chunk[order_id_col_trades].isin(all_order_ids)].copy()
        
        if len(matched_chunk) > 0:
            matched_chunk = add_date_column(matched_chunk, trade_time_col)
            trades_list.append(matched_chunk)
    
    if not trades_list:
        print("  No matching trades found!")
        return {}
    
    all_trades = pd.concat(trades_list, ignore_index=True)
    print(f"  Found {len(all_trades):,} trades from {total_rows:,} total rows")
    
    # Partition trades to match order partitions
    trades_by_partition = {}
    
    for partition_key, order_ids in partition_order_ids.items():
        partition_trades = all_trades[all_trades[order_id_col_trades].isin(order_ids)].copy()
        
        if len(partition_trades) > 0:
            trades_by_partition[partition_key] = partition_trades
            
            # Save to processed directory
            date, security_code = partition_key.split('/')
            partition_dir = Path(processed_dir) / date / security_code
            partition_dir.mkdir(parents=True, exist_ok=True)
            
            partition_file = partition_dir / "centrepoint_trades_raw.csv.gz"
            partition_trades.to_csv(partition_file, index=False, compression='gzip')
            
            size_mb = partition_file.stat().st_size / (1024 * 1024)
            unique_orders = partition_trades[order_id_col_trades].nunique()
            print(f"  {partition_key}: {len(partition_trades):,} trades, {unique_orders:,} orders ({size_mb:.2f} MB)")
    
    return trades_by_partition


def aggregate_trades(trades_by_partition, processed_dir):
    """Aggregate trades by order_id per partition."""
    print(f"\n[3/11] Aggregating trades by order...")
    
    order_id_col = col('trades', 'order_id')
    trade_time_col = col('trades', 'trade_time')
    trade_price_col = col('trades', 'trade_price')
    quantity_col = col('trades', 'quantity')
    
    trades_agg_by_partition = {}
    
    for partition_key, trades_df in trades_by_partition.items():
        if len(trades_df) == 0:
            continue
        
        # Aggregate by order ID
        agg_dict = {
            quantity_col: 'sum',
            trade_price_col: 'mean',
            trade_time_col: ['min', 'max', 'count']
        }
        
        trades_agg = trades_df.groupby(order_id_col).agg(agg_dict).reset_index()
        
        # Flatten column names
        trades_agg.columns = [
            'orderid',
            'total_quantity_filled',
            'avg_execution_price',
            'first_trade_time',
            'last_trade_time',
            'num_trades'
        ]
        
        # Calculate execution duration
        trades_agg['execution_duration_sec'] = (
            (trades_agg['last_trade_time'] - trades_agg['first_trade_time']) / 1e9
        )
        
        trades_agg_by_partition[partition_key] = trades_agg
        
        # Save to processed directory
        date, security_code = partition_key.split('/')
        partition_dir = Path(processed_dir) / date / security_code
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        partition_file = partition_dir / "centrepoint_trades_agg.csv.gz"
        trades_agg.to_csv(partition_file, index=False, compression='gzip')
        
        size_mb = partition_file.stat().st_size / (1024 * 1024)
        print(f"  {partition_key}: {len(trades_agg):,} orders with trades ({size_mb:.2f} MB)")
    
    total_orders = sum(len(agg) for agg in trades_agg_by_partition.values())
    print(f"  Total: {total_orders:,} orders with trades")
    
    return trades_agg_by_partition


def extract_nbbo(input_file, partitions, processed_dir):
    """Extract and partition NBBO data by date/security."""
    print(f"\n[4/11] Extracting NBBO data from {input_file}...")
    
    if not Path(input_file).exists():
        print(f"  File not found, skipping")
        return {}
    
    # Get column names
    timestamp_col = col('nbbo', 'timestamp')
    security_col = col('nbbo', 'security_code')
    
    # Read NBBO file
    nbbo_df = pd.read_csv(input_file)
    print(f"  Total NBBO records: {len(nbbo_df):,}")
    
    # Add date column
    if 'date' not in nbbo_df.columns:
        nbbo_df = add_date_column(nbbo_df, timestamp_col)
    
    # Standardize security_code column
    if security_col != 'security_code':
        nbbo_df = nbbo_df.rename(columns={security_col: 'security_code'})
    
    nbbo_by_partition = {}
    
    for partition_key in partitions:
        date, security_code = partition_key.split('/')
        security_code_int = int(security_code)
        
        # Filter NBBO for this partition
        partition_nbbo = nbbo_df[
            (nbbo_df['date'] == date) & 
            (nbbo_df['security_code'] == security_code_int)
        ].copy()
        
        if len(partition_nbbo) > 0:
            nbbo_by_partition[partition_key] = partition_nbbo
            
            # Save to processed directory
            partition_dir = Path(processed_dir) / date / security_code
            partition_dir.mkdir(parents=True, exist_ok=True)
            
            partition_file = partition_dir / "nbbo.csv.gz"
            partition_nbbo.to_csv(partition_file, index=False, compression='gzip')
            
            size_mb = partition_file.stat().st_size / (1024 * 1024)
            print(f"  {partition_key}: {len(partition_nbbo):,} records ({size_mb:.2f} MB)")
    
    print(f"  Total: {len(nbbo_by_partition)} partitions with NBBO data")
    return nbbo_by_partition


def extract_reference_data(dates, processed_dir):
    """Extract and partition session, reference, and participants data by date."""
    print(f"\n[5/11] Extracting reference data for {len(dates)} dates...")
    
    # Extract Session data
    session_file = INPUT_FILES['session']
    if Path(session_file).exists():
        timestamp_col = col('session', 'timestamp')
        session_df = pd.read_csv(session_file)
        if 'date' not in session_df.columns:
            session_df = add_date_column(session_df, timestamp_col)
        
        for date in dates:
            date_session = session_df[session_df['date'] == date].copy()
            if len(date_session) > 0:
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                
                date_file = date_dir / "session.csv.gz"
                date_session.to_csv(date_file, index=False, compression='gzip')
                print(f"  {date}/session.csv.gz: {len(date_session):,} records")
    
    # Extract Reference data
    reference_file = INPUT_FILES['reference']
    if Path(reference_file).exists():
        timestamp_col = col('reference', 'timestamp')
        ref_df = pd.read_csv(reference_file)
        if 'date' not in ref_df.columns:
            ref_df = add_date_column(ref_df, timestamp_col)
        
        for date in dates:
            date_ref = ref_df[ref_df['date'] == date].copy()
            if len(date_ref) > 0:
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                
                date_file = date_dir / "reference.csv.gz"
                date_ref.to_csv(date_file, index=False, compression='gzip')
                print(f"  {date}/reference.csv.gz: {len(date_ref):,} records")
    
    # Extract Participants data
    participants_file = INPUT_FILES['participants']
    if Path(participants_file).exists():
        timestamp_col = col('participants', 'timestamp')
        par_df = pd.read_csv(participants_file)
        if 'date' not in par_df.columns:
            par_df = add_date_column(par_df, timestamp_col)
        
        for date in dates:
            date_par = par_df[par_df['date'] == date].copy()
            if len(date_par) > 0:
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                
                date_file = date_dir / "participants.csv.gz"
                date_par.to_csv(date_file, index=False, compression='gzip')
                print(f"  {date}/participants.csv.gz: {len(date_par):,} records")


def extract_lob_states(orders_by_partition, trades_by_partition, processed_dir):
    """
    Extract before/after LOB states per partition.
    
    - orders_before_lob: Initial state (min timestamp+sequence per order)
    - orders_after_lob: Final state (max timestamp+sequence per order)
    """
    print(f"\n[6/11] Extracting LOB states...")
    
    order_id_col = col('orders', 'order_id')
    timestamp_col = col('orders', 'timestamp')
    sequence_col = col('orders', 'sequence')
    
    lob_states_by_partition = {}
    
    for partition_key, orders_df in orders_by_partition.items():
        if len(orders_df) == 0:
            continue
        
        # Sort by timestamp, then sequence
        orders_sorted = orders_df.sort_values([timestamp_col, sequence_col])
        
        # Get first state per order (orders_before_lob)
        orders_before = orders_sorted.groupby(order_id_col).first().reset_index()
        
        # Get last state per order (orders_after_lob)
        orders_after = orders_sorted.groupby(order_id_col).last().reset_index()
        
        lob_states_by_partition[partition_key] = {
            'before': orders_before,
            'after': orders_after
        }
        
        # Save to processed directory
        date, security_code = partition_key.split('/')
        partition_dir = Path(processed_dir) / date / security_code
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        before_file = partition_dir / "orders_before_lob.csv"
        after_file = partition_dir / "orders_after_lob.csv"
        
        orders_before.to_csv(before_file, index=False)
        orders_after.to_csv(after_file, index=False)
        
        print(f"  {partition_key}: {len(orders_before):,} before, {len(orders_after):,} after")
    
    return lob_states_by_partition


def extract_last_execution_times(trades_by_partition, processed_dir):
    """
    Extract first and last execution time per order from trades.
    """
    print(f"\n[7/11] Extracting last execution times...")
    
    order_id_col = col('trades', 'order_id')
    trade_time_col = col('trades', 'trade_time')
    
    execution_times_by_partition = {}
    
    for partition_key, trades_df in trades_by_partition.items():
        if len(trades_df) == 0:
            continue
        
        # Group by order and get first/last trade times
        execution_times = trades_df.groupby(order_id_col).agg({
            trade_time_col: ['min', 'max']
        }).reset_index()
        
        # Flatten column names
        execution_times.columns = ['orderid', 'first_execution_time', 'last_execution_time']
        
        execution_times_by_partition[partition_key] = execution_times
        
        # Save to processed directory
        date, security_code = partition_key.split('/')
        partition_dir = Path(processed_dir) / date / security_code
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        exec_file = partition_dir / "last_execution_time.csv"
        execution_times.to_csv(exec_file, index=False)
        
        print(f"  {partition_key}: {len(execution_times):,} orders with execution times")
    
    return execution_times_by_partition


def get_nbbo_midpoint(timestamp, orderbookid, nbbo_df, fallback_bid, fallback_offer):
    """
    Get NBBO midpoint price at given timestamp.
    Falls back to order bid/offer if NBBO not available.
    """
    if nbbo_df is None or len(nbbo_df) == 0:
        # Fallback to order prices
        if pd.notna(fallback_bid) and pd.notna(fallback_offer):
            return (fallback_bid + fallback_offer) / 2
        return None
    
    # Find NBBO at or before timestamp
    # Handle both 'security_code' and 'orderbookid' column names
    orderbook_col = 'security_code' if 'security_code' in nbbo_df.columns else col('nbbo', 'security_code')
    timestamp_col_nbbo = col('nbbo', 'timestamp') if col('nbbo', 'timestamp') in nbbo_df.columns else 'timestamp'
    
    nbbo_at_time = nbbo_df[
        (nbbo_df[orderbook_col] == orderbookid) &
        (nbbo_df[timestamp_col_nbbo] <= timestamp)
    ].sort_values(timestamp_col_nbbo, ascending=False)
    
    if len(nbbo_at_time) > 0:
        row = nbbo_at_time.iloc[0]
        # Handle both standardized and original column names
        bid_col = col('nbbo', 'bid')
        offer_col = col('nbbo', 'offer')
        bid = row.get('bid', row.get(bid_col))
        offer = row.get('offer', row.get(offer_col))
        if pd.notna(bid) and pd.notna(offer):
            return (bid + offer) / 2
    
    # Fallback
    if pd.notna(fallback_bid) and pd.notna(fallback_offer):
        return (fallback_bid + fallback_offer) / 2
    
    return None


def simulate_sweep_matching(lob_states_by_partition, execution_times_by_partition, 
                           nbbo_by_partition, outputs_dir):
    """
    Simulate sweep matching for each partition.
    Save results to outputs/ directory.
    """
    print(f"\n[8/11] Simulating sweep matching...")
    
    order_id_col = col('orders', 'order_id')
    order_type_col = col('orders', 'order_type')
    timestamp_col = col('orders', 'timestamp')
    side_col = col('orders', 'side')
    quantity_col = col('orders', 'quantity')
    leaves_qty_col = col('orders', 'leaves_quantity')
    bid_col = col('orders', 'bid')
    offer_col = col('orders', 'offer')
    
    simulation_results_by_partition = {}
    
    for partition_key, lob_states in lob_states_by_partition.items():
        orders_before = lob_states['before']
        orders_after = lob_states['after']
        
        # Get execution times
        execution_times = execution_times_by_partition.get(partition_key, pd.DataFrame())
        
        # Get NBBO data
        nbbo_df = nbbo_by_partition.get(partition_key, None)
        
        # Prepare sweep orders (type 2048, from orders_after)
        sweep_orders = orders_after[orders_after[order_type_col] == SWEEP_ORDER_TYPE].copy()
        
        if len(sweep_orders) == 0:
            print(f"  {partition_key}: No sweep orders, skipping")
            continue
        
        # Merge with execution times
        sweep_orders = sweep_orders.merge(
            execution_times.rename(columns={'orderid': order_id_col}),
            on=order_id_col,
            how='left'
        )
        
        # Fill missing execution times
        sweep_orders['first_execution_time'] = sweep_orders['first_execution_time'].fillna(0)
        sweep_orders['last_execution_time'] = sweep_orders['last_execution_time'].fillna(float('inf'))
        
        # Sort by time priority
        sweep_orders = sweep_orders.sort_values('first_execution_time').reset_index(drop=True)
        
        # Prepare incoming orders (types 64, 256, 4096, 4098, from orders_before)
        incoming_orders = orders_before[
            orders_before[order_type_col].isin(INCOMING_ORDER_TYPES)
        ].copy()
        
        incoming_orders = incoming_orders.sort_values(timestamp_col).reset_index(drop=True)
        
        # Track remaining quantities
        sweep_remaining = {int(row[order_id_col]): row[leaves_qty_col] 
                          for _, row in sweep_orders.iterrows()}
        
        # Run simulation
        all_matches = []
        order_summaries = []
        
        for idx, incoming in incoming_orders.iterrows():
            incoming_id = incoming[order_id_col]
            incoming_ts = incoming[timestamp_col]
            incoming_side = incoming[side_col]
            incoming_qty = incoming[quantity_col]
            orderbookid = incoming.get('security_code', incoming.get(col('orders', 'security_code')))
            
            # Find active sweep orders
            active_sweeps = sweep_orders[
                (sweep_orders['first_execution_time'] <= incoming_ts) &
                (sweep_orders['last_execution_time'] >= incoming_ts)
            ]
            
            # Filter opposite side
            opposite_side = 2 if incoming_side == 1 else 1
            matching_sweeps = active_sweeps[active_sweeps[side_col] == opposite_side]
            matching_sweeps = matching_sweeps.sort_values('first_execution_time')
            
            # Match sequentially
            remaining_qty = incoming_qty
            matches_for_order = []
            
            for _, sweep in matching_sweeps.iterrows():
                if remaining_qty <= 0:
                    break
                
                sweep_id = int(sweep[order_id_col])
                available_qty = sweep_remaining.get(sweep_id, 0)
                
                if available_qty <= 0:
                    continue
                
                # Calculate match quantity
                match_qty = min(remaining_qty, available_qty)
                
                # Get midpoint price
                midpoint = get_nbbo_midpoint(
                    incoming_ts, orderbookid, nbbo_df,
                    incoming.get(bid_col), incoming.get(offer_col)
                )
                
                if midpoint is None:
                    continue
                
                # Record match
                match_record = {
                    'incoming_orderid': incoming_id,
                    'sweep_orderid': sweep_id,
                    'matched_quantity': match_qty,
                    'match_price': midpoint,
                    'match_timestamp': incoming_ts
                }
                
                all_matches.append(match_record)
                matches_for_order.append(match_record)
                
                # Update remaining quantities
                remaining_qty -= match_qty
                sweep_remaining[sweep_id] -= match_qty
            
            # Summary for this incoming order
            total_matched = sum(m['matched_quantity'] for m in matches_for_order)
            avg_price = (sum(m['matched_quantity'] * m['match_price'] for m in matches_for_order) / total_matched) if total_matched > 0 else None
            
            order_summaries.append({
                'orderid': incoming_id,
                'original_quantity': incoming_qty,
                'matched_quantity': total_matched,
                'num_matches': len(matches_for_order),
                'avg_match_price': avg_price,
                'fill_ratio': total_matched / incoming_qty if incoming_qty > 0 else 0
            })
        
        # Convert to DataFrames
        match_details = pd.DataFrame(all_matches) if all_matches else pd.DataFrame()
        match_summary = pd.DataFrame(order_summaries)
        
        # Calculate sweep utilization
        sweep_utilization = []
        for sweep_id in sweep_remaining.keys():
            original_qty = sweep_orders[sweep_orders[order_id_col] == sweep_id][leaves_qty_col].values
            if len(original_qty) > 0:
                original_qty = original_qty[0]
                used_qty = original_qty - sweep_remaining[sweep_id]
                num_matches = sum(1 for m in all_matches if m['sweep_orderid'] == sweep_id)
                
                sweep_utilization.append({
                    'sweep_orderid': sweep_id,
                    'original_quantity': original_qty,
                    'matched_quantity': used_qty,
                    'remaining_quantity': sweep_remaining[sweep_id],
                    'num_matches': num_matches,
                    'utilization_ratio': used_qty / original_qty if original_qty > 0 else 0
                })
        
        sweep_util_df = pd.DataFrame(sweep_utilization)
        
        simulation_results_by_partition[partition_key] = {
            'match_details': match_details,
            'match_summary': match_summary,
            'sweep_utilization': sweep_util_df
        }
        
        # Save to outputs directory
        date, security_code = partition_key.split('/')
        partition_dir = Path(outputs_dir) / date / security_code
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        if len(match_details) > 0:
            match_details.to_csv(partition_dir / "sweep_match_details.csv", index=False)
        match_summary.to_csv(partition_dir / "sweep_match_summary.csv", index=False)
        sweep_util_df.to_csv(partition_dir / "sweep_utilization.csv", index=False)
        
        print(f"  {partition_key}: {len(all_matches):,} matches, {len(incoming_orders):,} incoming orders")
    
    return simulation_results_by_partition


def calculate_simulated_metrics(lob_states_by_partition, simulation_results_by_partition, outputs_dir):
    """
    Calculate simulated metrics and merge with original orders.
    Save to outputs/ directory.
    """
    print(f"\n[9/11] Calculating simulated metrics...")
    
    order_id_col = col('orders', 'order_id')
    
    orders_with_sim_metrics_by_partition = {}
    
    for partition_key, lob_states in lob_states_by_partition.items():
        orders_after = lob_states['after']
        
        sim_results = simulation_results_by_partition.get(partition_key)
        if sim_results is None:
            print(f"  {partition_key}: No simulation results, skipping")
            continue
        
        match_summary = sim_results['match_summary']
        
        # Merge orders with simulation results
        orders_with_sim = orders_after.merge(
            match_summary[['orderid', 'matched_quantity', 'num_matches', 'avg_match_price', 'fill_ratio']].rename(columns={
                'orderid': order_id_col,
                'matched_quantity': 'simulated_matched_quantity',
                'num_matches': 'simulated_num_matches',
                'avg_match_price': 'simulated_avg_price',
                'fill_ratio': 'simulated_fill_ratio'
            }),
            on=order_id_col,
            how='left'
        )
        
        # Fill NaN for orders without simulated matches
        orders_with_sim['simulated_matched_quantity'] = orders_with_sim['simulated_matched_quantity'].fillna(0)
        orders_with_sim['simulated_num_matches'] = orders_with_sim['simulated_num_matches'].fillna(0)
        orders_with_sim['simulated_fill_ratio'] = orders_with_sim['simulated_fill_ratio'].fillna(0)
        
        # Classify simulated fill status
        orders_with_sim['simulated_fill_status'] = 'Unfilled'
        orders_with_sim.loc[orders_with_sim['simulated_fill_ratio'] >= 0.99, 'simulated_fill_status'] = 'Fully Filled'
        orders_with_sim.loc[
            (orders_with_sim['simulated_fill_ratio'] > 0) & (orders_with_sim['simulated_fill_ratio'] < 0.99),
            'simulated_fill_status'
        ] = 'Partially Filled'
        
        orders_with_sim_metrics_by_partition[partition_key] = orders_with_sim
        
        # Save to outputs directory
        date, security_code = partition_key.split('/')
        partition_dir = Path(outputs_dir) / date / security_code
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        output_file = partition_dir / "orders_with_simulated_metrics.csv"
        orders_with_sim.to_csv(output_file, index=False)
        
        print(f"  {partition_key}: {len(orders_with_sim):,} orders with simulated metrics")
    
    return orders_with_sim_metrics_by_partition


def classify_order_groups(orders_with_sim_metrics_by_partition):
    """
    Classify orders into groups based on REAL execution (from final state).
    
    Group 1: Fully Filled (leavesquantity == 0)
    Group 2: Partially Filled (leavesquantity > 0 AND totalmatchedquantity > 0)
    Group 3: Unfilled (leavesquantity > 0 AND totalmatchedquantity == 0)
    """
    print(f"\n[10/11] Classifying order groups...")
    
    leaves_qty_col = col('orders', 'leaves_quantity')
    matched_qty_col = col('orders', 'matched_quantity')
    
    groups_by_partition = {}
    
    for partition_key, orders_df in orders_with_sim_metrics_by_partition.items():
        # Classify based on final state
        group1 = orders_df[orders_df[leaves_qty_col] == 0].copy()
        group2 = orders_df[
            (orders_df[leaves_qty_col] > 0) & 
            (orders_df[matched_qty_col] > 0)
        ].copy()
        group3 = orders_df[
            (orders_df[leaves_qty_col] > 0) & 
            (orders_df[matched_qty_col] == 0)
        ].copy()
        
        groups_by_partition[partition_key] = {
            'Group 1 (Fully Filled)': group1,
            'Group 2 (Partially Filled)': group2,
            'Group 3 (Unfilled)': group3
        }
        
        print(f"  {partition_key}: G1={len(group1):,}, G2={len(group2):,}, G3={len(group3):,}")
    
    return groups_by_partition


def compare_real_vs_simulated(orders_with_sim_metrics_by_partition, groups_by_partition, outputs_dir):
    """
    Compare real vs simulated metrics by group for each partition.
    Save comparison reports to outputs/ directory.
    """
    print(f"\n[11/11] Comparing real vs simulated metrics...")
    
    order_id_col = col('orders', 'order_id')
    quantity_col = col('orders', 'quantity')
    matched_qty_col = col('orders', 'matched_quantity')
    leaves_qty_col = col('orders', 'leaves_quantity')
    
    for partition_key, groups in groups_by_partition.items():
        orders_df = orders_with_sim_metrics_by_partition[partition_key]
        
        group_summaries = []
        order_details = []
        group_analyses = []
        
        for group_name, group_df in groups.items():
            if len(group_df) == 0:
                continue
            
            # Group summary
            real_matched = group_df[matched_qty_col].sum()
            sim_matched = group_df['simulated_matched_quantity'].sum()
            total_qty = group_df[quantity_col].sum()
            
            summary = {
                'group': group_name,
                'num_orders': len(group_df),
                'total_quantity': total_qty,
                'real_matched_quantity': real_matched,
                'real_fill_ratio': real_matched / total_qty if total_qty > 0 else 0,
                'simulated_matched_quantity': sim_matched,
                'simulated_fill_ratio': sim_matched / total_qty if total_qty > 0 else 0,
                'quantity_difference': sim_matched - real_matched,
                'fill_ratio_difference': (sim_matched - real_matched) / total_qty if total_qty > 0 else 0
            }
            group_summaries.append(summary)
            
            # Order-level details
            for _, order in group_df.iterrows():
                detail = {
                    'group': group_name,
                    'orderid': order[order_id_col],
                    'quantity': order[quantity_col],
                    'real_matched': order[matched_qty_col],
                    'simulated_matched': order['simulated_matched_quantity'],
                    'difference': order['simulated_matched_quantity'] - order[matched_qty_col],
                    'real_fill_status': 'Fully Filled' if order[leaves_qty_col] == 0 else ('Partially Filled' if order[matched_qty_col] > 0 else 'Unfilled'),
                    'simulated_fill_status': order['simulated_fill_status']
                }
                order_details.append(detail)
            
            # Group analysis
            better_count = (group_df['simulated_matched_quantity'] > group_df[matched_qty_col]).sum()
            same_count = (group_df['simulated_matched_quantity'] == group_df[matched_qty_col]).sum()
            worse_count = (group_df['simulated_matched_quantity'] < group_df[matched_qty_col]).sum()
            
            analysis = {
                'group': group_name,
                'num_orders': len(group_df),
                'better_in_simulation': better_count,
                'same_in_simulation': same_count,
                'worse_in_simulation': worse_count,
                'avg_real_fill_ratio': group_df[matched_qty_col].sum() / group_df[quantity_col].sum() if group_df[quantity_col].sum() > 0 else 0,
                'avg_simulated_fill_ratio': group_df['simulated_matched_quantity'].sum() / group_df[quantity_col].sum() if group_df[quantity_col].sum() > 0 else 0
            }
            group_analyses.append(analysis)
        
        # Save comparison reports to outputs directory
        date, security_code = partition_key.split('/')
        partition_dir = Path(outputs_dir) / date / security_code
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        pd.DataFrame(group_summaries).to_csv(partition_dir / "group_comparison_summary.csv", index=False)
        pd.DataFrame(order_details).to_csv(partition_dir / "order_level_comparison.csv", index=False)
        pd.DataFrame(group_analyses).to_csv(partition_dir / "group_analysis_detail.csv", index=False)
        
        # Statistical summary
        total_orders = sum(len(g) for g in groups.values())
        total_real = sum(g[matched_qty_col].sum() for g in groups.values())
        total_sim = sum(g['simulated_matched_quantity'].sum() for g in groups.values())
        
        stats = {
            'partition': partition_key,
            'total_orders': total_orders,
            'total_real_matched': total_real,
            'total_simulated_matched': total_sim,
            'improvement': total_sim - total_real,
            'improvement_pct': ((total_sim - total_real) / total_real * 100) if total_real > 0 else 0
        }
        
        pd.DataFrame([stats]).to_csv(partition_dir / "statistical_summary.csv", index=False)
        
        print(f"  {partition_key}: Real={total_real:,.0f}, Sim={total_sim:,.0f}, Diff={total_sim-total_real:,.0f}")


def print_summary(orders_by_partition, trades_by_partition, execution_time):
    """Print final summary."""
    print("\n" + "="*80)
    print("END-TO-END PIPELINE COMPLETE")
    print("="*80)
    
    total_orders = sum(len(df) for df in orders_by_partition.values())
    total_trades = sum(len(df) for df in trades_by_partition.values())
    
    print(f"✅ Centre Point orders: {total_orders:,}")
    print(f"✅ Matching trades: {total_trades:,}")
    print(f"✅ Partitions created: {len(orders_by_partition)}")
    print(f"✅ LOB states extracted → {PROCESSED_DIR}/")
    print(f"✅ Sweep matching simulated → {OUTPUTS_DIR}/")
    print(f"✅ Metrics compared (real vs simulated) → {OUTPUTS_DIR}/")
    print(f"\n⏱️  Execution time: {execution_time:.2f}s")
    print("="*80)


def main():
    """Main execution function."""
    start_time = time.time()
    
    print("="*80)
    print("END-TO-END CENTRE POINT PIPELINE (PARTITIONED)")
    print("="*80)
    print(f"Processed directory: {PROCESSED_DIR}/")
    print(f"Outputs directory: {OUTPUTS_DIR}/")
    print(f"Chunk size: {CHUNK_SIZE:,}")
    
    # Create output directories
    Path(PROCESSED_DIR).mkdir(parents=True, exist_ok=True)
    Path(OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
    
    # Step 1: Extract orders
    orders_by_partition = extract_orders(INPUT_FILES['orders'], PROCESSED_DIR)
    
    if not orders_by_partition:
        print("\nNo Centre Point orders found. Exiting.")
        return
    
    # Get unique dates
    partition_keys = list(orders_by_partition.keys())
    unique_dates = sorted(set(pk.split('/')[0] for pk in partition_keys))
    
    # Step 2: Extract trades
    trades_by_partition = extract_trades(INPUT_FILES['trades'], orders_by_partition, PROCESSED_DIR)
    
    # Step 3: Aggregate trades
    if trades_by_partition:
        aggregate_trades(trades_by_partition, PROCESSED_DIR)
    else:
        print("\n[3/11] No trades to aggregate")
    
    # Step 4: Extract NBBO
    nbbo_by_partition = extract_nbbo(INPUT_FILES['nbbo'], partition_keys, PROCESSED_DIR)
    
    # Step 5: Extract reference data
    extract_reference_data(unique_dates, PROCESSED_DIR)
    
    # Step 6: Extract LOB states (before/after)
    lob_states_by_partition = extract_lob_states(orders_by_partition, trades_by_partition, PROCESSED_DIR)
    
    # Step 7: Extract last execution times
    execution_times_by_partition = extract_last_execution_times(trades_by_partition, PROCESSED_DIR)
    
    # Step 8: Simulate sweep matching (outputs to OUTPUTS_DIR)
    simulation_results_by_partition = simulate_sweep_matching(
        lob_states_by_partition, 
        execution_times_by_partition,
        nbbo_by_partition,
        OUTPUTS_DIR
    )
    
    # Step 9: Calculate simulated metrics (outputs to OUTPUTS_DIR)
    orders_with_sim_metrics_by_partition = calculate_simulated_metrics(
        lob_states_by_partition,
        simulation_results_by_partition,
        OUTPUTS_DIR
    )
    
    # Step 10: Classify order groups
    groups_by_partition = classify_order_groups(orders_with_sim_metrics_by_partition)
    
    # Step 11: Compare real vs simulated (outputs to OUTPUTS_DIR)
    compare_real_vs_simulated(
        orders_with_sim_metrics_by_partition,
        groups_by_partition,
        OUTPUTS_DIR
    )
    
    # Print summary
    execution_time = time.time() - start_time
    print_summary(orders_by_partition, trades_by_partition, execution_time)


if __name__ == '__main__':
    main()
