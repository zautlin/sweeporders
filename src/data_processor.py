"""
Data Processor Module

Handles all data extraction, partitioning, and preprocessing operations:
- Extract and partition Centre Point orders by date/security
- Extract and match trades to orders
- Aggregate trade metrics
- Extract NBBO data
- Extract reference data (session, reference, participants)
- Extract LOB states (before/after)
- Extract execution times from trades
- Load partition data for simulation
- Classify orders into groups based on real execution
"""

import pandas as pd
from pathlib import Path


def add_date_column(df, timestamp_col):
    """Add date column from timestamp (convert UTC to AEST)."""
    df['date'] = (pd.to_datetime(df[timestamp_col], unit='ns')
                    .dt.tz_localize('UTC')
                    .dt.tz_convert('Australia/Sydney')
                    .dt.strftime('%Y-%m-%d'))
    return df


def col(file_type, logical_name, column_mapping):
    """Get actual column name from logical name using mapping."""
    mapped = column_mapping.get(file_type, {}).get(logical_name)
    return mapped if mapped is not None else logical_name


def extract_orders(input_file, processed_dir, order_types, chunk_size, column_mapping):
    """
    Extract Centre Point orders and partition by date/security.
    
    Args:
        input_file: Path to orders CSV file
        processed_dir: Directory to save processed partitions
        order_types: List of order types to extract (e.g., [64, 256, 2048, 4096, 4098])
        chunk_size: Chunk size for reading large files
        column_mapping: Column name mapping dictionary
    
    Returns:
        Dictionary mapping partition_key (date/security) to DataFrame
    """
    print(f"\n[1/11] Extracting Centre Point orders from {input_file}...")
    
    order_type_col = col('orders', 'order_type', column_mapping)
    timestamp_col = col('orders', 'timestamp', column_mapping)
    security_col = col('orders', 'security_code', column_mapping)
    
    orders_list = []
    total_rows = 0
    
    for chunk in pd.read_csv(input_file, chunksize=chunk_size, low_memory=False):
        total_rows += len(chunk)
        cp_chunk = chunk[chunk[order_type_col].isin(order_types)].copy()
        
        if len(cp_chunk) > 0:
            cp_chunk = add_date_column(cp_chunk, timestamp_col)
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
        
        partition_file = partition_dir / "cp_orders_filtered.csv.gz"
        group_df.to_csv(partition_file, index=False, compression='gzip')
        
        size_mb = partition_file.stat().st_size / (1024 * 1024)
        print(f"  {partition_key}: {len(group_df):,} orders ({size_mb:.2f} MB)")
    
    return partitions


def extract_trades(input_file, orders_by_partition, processed_dir, column_mapping, chunk_size=100000):
    """
    Extract trades matching order_ids from partitions.
    
    Args:
        input_file: Path to trades CSV file
        orders_by_partition: Dictionary of orders DataFrames by partition
        processed_dir: Directory to save processed trades
        column_mapping: Column name mapping dictionary
        chunk_size: Chunk size for reading CSV (default: 100000)
    
    Returns:
        Dictionary mapping partition_key to trades DataFrame
    """
    print(f"\n[2/11] Extracting matching trades from {input_file}...")
    
    order_id_col_orders = col('orders', 'order_id', column_mapping)
    order_id_col_trades = col('trades', 'order_id', column_mapping)
    trade_time_col = col('trades', 'trade_time', column_mapping)
    
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
    
    for chunk in pd.read_csv(input_file, chunksize=chunk_size, low_memory=False):
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
            
            partition_file = partition_dir / "cp_trades_matched.csv.gz"
            partition_trades.to_csv(partition_file, index=False, compression='gzip')
            
            size_mb = partition_file.stat().st_size / (1024 * 1024)
            unique_orders = partition_trades[order_id_col_trades].nunique()
            print(f"  {partition_key}: {len(partition_trades):,} trades, {unique_orders:,} orders ({size_mb:.2f} MB)")
    
    return trades_by_partition


def aggregate_trades(orders_by_partition, trades_by_partition, processed_dir, column_mapping):
    """
    Aggregate trades by order_id per partition.
    
    Args:
        orders_by_partition: Dictionary of orders DataFrames
        trades_by_partition: Dictionary of trades DataFrames
        processed_dir: Directory to save aggregated trades
        column_mapping: Column name mapping dictionary
    
    Returns:
        Dictionary mapping partition_key to aggregated trades DataFrame
    """
    print(f"\n[3/11] Aggregating trades by order...")
    
    order_id_col = col('trades', 'order_id', column_mapping)
    trade_time_col = col('trades', 'trade_time', column_mapping)
    trade_price_col = col('trades', 'trade_price', column_mapping)
    quantity_col = col('trades', 'quantity', column_mapping)
    
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
        
        partition_file = partition_dir / "cp_trades_aggregated.csv.gz"
        trades_agg.to_csv(partition_file, index=False, compression='gzip')
        
        size_mb = partition_file.stat().st_size / (1024 * 1024)
        print(f"  {partition_key}: {len(trades_agg):,} orders with trades ({size_mb:.2f} MB)")
    
    total_orders = sum(len(agg) for agg in trades_agg_by_partition.values())
    print(f"  Total: {total_orders:,} orders with trades")
    
    return trades_agg_by_partition


def extract_nbbo(input_file, orders_by_partition, processed_dir, column_mapping):
    """
    Extract and partition NBBO data by date/security.
    
    Args:
        input_file: Path to NBBO CSV file
        orders_by_partition: Dictionary of orders (to get partition keys)
        processed_dir: Directory to save NBBO partitions
        column_mapping: Column name mapping dictionary
    
    Returns:
        Dictionary mapping partition_key to NBBO DataFrame
    """
    print(f"\n[4/11] Extracting NBBO data from {input_file}...")
    
    if not Path(input_file).exists():
        print(f"  File not found, skipping")
        return {}
    
    timestamp_col = col('nbbo', 'timestamp', column_mapping)
    security_col = col('nbbo', 'security_code', column_mapping)
    
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
    
    for partition_key in orders_by_partition.keys():
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


def extract_reference_data(input_files, unique_dates, orders_by_partition, processed_dir):
    """
    Extract and partition session, reference, and participants data by date.
    
    Args:
        input_files: Dictionary with keys 'session', 'reference', 'participants'
        unique_dates: List of unique dates to extract
        orders_by_partition: Dictionary of orders (unused, for consistency)
        processed_dir: Directory to save reference data
    """
    print(f"\n[5/11] Extracting reference data for {len(unique_dates)} dates...")
    
    # Extract Session data
    session_file = input_files['session']
    if Path(session_file).exists():
        session_df = pd.read_csv(session_file)
        if 'date' not in session_df.columns:
            session_df = add_date_column(session_df, 'TradeDate')
        
        for date in unique_dates:
            date_session = session_df[session_df['date'] == date].copy()
            if len(date_session) > 0:
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                
                date_file = date_dir / "session.csv.gz"
                date_session.to_csv(date_file, index=False, compression='gzip')
                print(f"  {date}/session.csv.gz: {len(date_session):,} records")
    
    # Extract Reference data
    reference_file = input_files['reference']
    if Path(reference_file).exists():
        ref_df = pd.read_csv(reference_file)
        if 'date' not in ref_df.columns:
            ref_df = add_date_column(ref_df, 'TradeDate')
        
        for date in unique_dates:
            date_ref = ref_df[ref_df['date'] == date].copy()
            if len(date_ref) > 0:
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                
                date_file = date_dir / "reference.csv.gz"
                date_ref.to_csv(date_file, index=False, compression='gzip')
                print(f"  {date}/reference.csv.gz: {len(date_ref):,} records")
    
    # Extract Participants data
    participants_file = input_files['participants']
    if Path(participants_file).exists():
        par_df = pd.read_csv(participants_file)
        if 'date' not in par_df.columns:
            par_df = add_date_column(par_df, 'TradeDate')
        
        for date in unique_dates:
            date_par = par_df[par_df['date'] == date].copy()
            if len(date_par) > 0:
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                
                date_file = date_dir / "participants.csv.gz"
                date_par.to_csv(date_file, index=False, compression='gzip')
                print(f"  {date}/participants.csv.gz: {len(date_par):,} records")


def get_orders_state(orders_by_partition, processed_dir, column_mapping):
    """
    Extract before/after order states per partition.
    
    - orders_before_matching: Initial state (min timestamp+sequence per order)
    - orders_after_matching: Final state (max timestamp+sequence per order)
    
    Args:
        orders_by_partition: Dictionary of orders DataFrames
        processed_dir: Directory to save order states
        column_mapping: Column name mapping dictionary
    
    Returns:
        Dictionary mapping partition_key to {'before': DataFrame, 'after': DataFrame}
    """
    print(f"\n[6/11] Extracting order states...")
    
    order_id_col = col('orders', 'order_id', column_mapping)
    timestamp_col = col('orders', 'timestamp', column_mapping)
    sequence_col = col('orders', 'sequence', column_mapping)
    
    order_states_by_partition = {}
    
    for partition_key, orders_df in orders_by_partition.items():
        if len(orders_df) == 0:
            continue
        
        # Sort by timestamp, then sequence
        orders_sorted = orders_df.sort_values([timestamp_col, sequence_col])
        
        # Get first state per order (orders_before_matching)
        orders_before = orders_sorted.groupby(order_id_col).first().reset_index()
        
        # Get last state per order (orders_after_matching)
        orders_after = orders_sorted.groupby(order_id_col).last().reset_index()
        
        order_states_by_partition[partition_key] = {
            'before': orders_before,
            'after': orders_after
        }
        
        # Save to processed directory
        date, security_code = partition_key.split('/')
        partition_dir = Path(processed_dir) / date / security_code
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        before_file = partition_dir / "orders_before_matching.csv"
        after_file = partition_dir / "orders_after_matching.csv"
        
        orders_before.to_csv(before_file, index=False)
        orders_after.to_csv(after_file, index=False)
        
        print(f"  {partition_key}: {len(orders_before):,} before, {len(orders_after):,} after")
    
    return order_states_by_partition


def extract_last_execution_times(orders_by_partition, trades_by_partition, processed_dir, column_mapping):
    """
    Extract first and last execution time per order from trades.
    
    Args:
        orders_by_partition: Dictionary of orders (unused, for consistency)
        trades_by_partition: Dictionary of trades DataFrames
        processed_dir: Directory to save execution times
        column_mapping: Column name mapping dictionary
    
    Returns:
        Dictionary mapping partition_key to execution times DataFrame
    """
    print(f"\n[7/11] Extracting last execution times...")
    
    order_id_col = col('trades', 'order_id', column_mapping)
    trade_time_col = col('trades', 'trade_time', column_mapping)
    
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


def load_partition_data(partition_key, processed_dir):
    """
    Load all necessary data for a partition for simulation.
    
    Args:
        partition_key: Partition identifier (e.g., "2024-09-05/110621")
        processed_dir: Directory containing processed data
    
    Returns:
        Dictionary containing:
            - 'orders_before': DataFrame from orders_before_matching.csv
            - 'orders_after': DataFrame from orders_after_matching.csv
            - 'last_execution': DataFrame from last_execution_time.csv
    """
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    
    partition_data = {}
    
    # Load orders_before_matching
    before_file = partition_dir / "orders_before_matching.csv"
    if before_file.exists():
        partition_data['orders_before'] = pd.read_csv(before_file)
    
    # Load orders_after_matching
    after_file = partition_dir / "orders_after_matching.csv"
    if after_file.exists():
        partition_data['orders_after'] = pd.read_csv(after_file)
    
    # Load last_execution_time
    exec_file = partition_dir / "last_execution_time.csv"
    if exec_file.exists():
        partition_data['last_execution'] = pd.read_csv(exec_file)
    else:
        # Create empty DataFrame if no execution times
        partition_data['last_execution'] = pd.DataFrame(columns=['orderid', 'first_execution_time', 'last_execution_time'])
    
    return partition_data


def classify_order_groups(orders_by_partition, processed_dir, column_mapping):
    """
    Classify SWEEP ORDERS ONLY (type 2048) into groups based on REAL execution (from final state).
    
    Group 1: Fully Filled (leavesquantity == 0)
    Group 2: Partially Filled (leavesquantity > 0 AND totalmatchedquantity > 0)
    Group 3: Unfilled (leavesquantity > 0 AND totalmatchedquantity == 0)
    
    Args:
        orders_by_partition: Dictionary of orders (partition keys only, NOT USED for data)
        processed_dir: Directory containing orders_after_matching.csv
        column_mapping: Column name mapping dictionary
    
    Returns:
        Dictionary mapping partition_key to groups dictionary
    """
    print(f"\n[10/11] Classifying sweep order groups (type 2048 only)...")
    
    order_type_col = col('orders', 'order_type', column_mapping)
    leaves_qty_col = col('orders', 'leaves_quantity', column_mapping)
    matched_qty_col = col('orders', 'matched_quantity', column_mapping)
    
    groups_by_partition = {}
    
    for partition_key in orders_by_partition.keys():
        # Load orders_after_matching.csv to get REAL execution results
        date, security_code = partition_key.split('/')
        partition_dir = Path(processed_dir) / date / security_code
        after_file = partition_dir / "orders_after_matching.csv"
        
        if not after_file.exists():
            continue
        
        orders_after = pd.read_csv(after_file)
        
        # Filter for sweep orders ONLY (type 2048)
        sweep_orders = orders_after[orders_after[order_type_col] == 2048].copy()
        
        if len(sweep_orders) == 0:
            print(f"  {partition_key}: No sweep orders found")
            continue
        
        # Classify based on real execution
        group1 = sweep_orders[sweep_orders[leaves_qty_col] == 0].copy()
        group2 = sweep_orders[
            (sweep_orders[leaves_qty_col] > 0) & 
            (sweep_orders[matched_qty_col] > 0)
        ].copy()
        group3 = sweep_orders[
            (sweep_orders[leaves_qty_col] > 0) & 
            (sweep_orders[matched_qty_col] == 0)
        ].copy()
        
        groups_by_partition[partition_key] = {
            'Group 1 (Fully Filled)': group1,
            'Group 2 (Partially Filled)': group2,
            'Group 3 (Unfilled)': group3
        }
        
        print(f"  {partition_key}: G1={len(group1):,}, G2={len(group2):,}, G3={len(group3):,} (sweep orders only)")
    
    return groups_by_partition
