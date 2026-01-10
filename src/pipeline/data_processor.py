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
from config.config import SWEEP_ORDER_TYPE, normalize_to_standard_names
from config.column_schema import col


def add_date_column(df, timestamp_col):
    """Add date column from timestamp (convert UTC to AEST)."""
    df[col.common.date] = (pd.to_datetime(df[timestamp_col], unit='ns')
                    .dt.tz_localize('UTC')
                    .dt.tz_convert('Australia/Sydney')
                    .dt.strftime('%Y-%m-%d'))
    return df


def _read_csv_files_concat(file_list):
    """Read and concatenate multiple CSV files into single DataFrame."""
    if not file_list:
        return None
    
    dfs = []
    for file in file_list:
        df = pd.read_csv(file)
        dfs.append(df)
    
    if not dfs:
        return None
    
    return pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]


def _partition_by_date_and_save(df, unique_dates, processed_dir, filename, date_col):
    """Partition DataFrame by date and save each partition as compressed CSV."""
    results = {}
    
    for date in unique_dates:
        date_data = df[df[date_col] == date].copy()
        
        if len(date_data) > 0:
            date_dir = Path(processed_dir) / date
            date_dir.mkdir(parents=True, exist_ok=True)
            
            output_file = date_dir / filename
            date_data.to_csv(output_file, index=False, compression='gzip')
            
            results[date] = date_data
            size_kb = output_file.stat().st_size / 1024
            print(f"    {date}/{filename}: {len(date_data):,} records ({size_kb:.1f} KB)")
        else:
            print(f"    {date}/{filename}: NO DATA (missing in raw files)")
    
    return results


def _partition_by_date_security_and_save(df, orders_by_partition, processed_dir, filename, date_col, security_col):
    """Partition DataFrame by date/security and save each partition as compressed CSV."""
    results = {}
    
    for partition_key in orders_by_partition.keys():
        date, orderbookid = partition_key.split('/')
        orderbookid_int = int(orderbookid)
        
        partition_data = df[
            (df[date_col] == date) & 
            (df[security_col] == orderbookid_int)
        ].copy()
        
        if len(partition_data) > 0:
            partition_data_normalized = normalize_to_standard_names(partition_data, 'nbbo')
            
            partition_dir = Path(processed_dir) / date / orderbookid
            partition_dir.mkdir(parents=True, exist_ok=True)
            
            output_file = partition_dir / filename
            partition_data_normalized.to_csv(output_file, index=False, compression='gzip')
            
            results[partition_key] = partition_data_normalized
            size_kb = output_file.stat().st_size / 1024
            print(f"    {partition_key}/{filename}: {len(partition_data):,} records ({size_kb:.1f} KB)")
        else:
            print(f"    {partition_key}/{filename}: NO DATA (missing in raw files)")
    
    return results


def _process_single_reference_type(file_list, timestamp_col, unique_dates, processed_dir, filename):
    """Process single reference data type: read files, add date, partition, save."""
    if not file_list:
        return {}
    
    df = _read_csv_files_concat(file_list)
    if df is None or len(df) == 0:
        return {}
    
    df = add_date_column(df, timestamp_col)
    return _partition_by_date_and_save(df, unique_dates, processed_dir, filename, col.common.date)


def _process_participants_with_fallback(file_list, timestamp_col, unique_dates, processed_dir):
    """Process participants data with latest-date fallback for missing dates."""
    if not file_list:
        return {}
    
    df = _read_csv_files_concat(file_list)
    if df is None or len(df) == 0:
        return {}
    
    df = add_date_column(df, timestamp_col)
    
    all_participant_dates = df[col.common.date].unique()
    print(f"    Available dates in participants: {sorted(all_participant_dates)}")
    
    results = {}
    
    for date in unique_dates:
        date_data = df[df[col.common.date] == date].copy()
        
        if len(date_data) > 0:
            date_dir = Path(processed_dir) / date
            date_dir.mkdir(parents=True, exist_ok=True)
            
            output_file = date_dir / "participants.csv.gz"
            date_data.to_csv(output_file, index=False, compression='gzip')
            
            results[date] = date_data
            size_kb = output_file.stat().st_size / 1024
            print(f"    {date}/participants.csv.gz: {len(date_data):,} records ({size_kb:.1f} KB)")
        else:
            latest_date = max(all_participant_dates)
            fallback_data = df[df[col.common.date] == latest_date].copy()
            
            date_dir = Path(processed_dir) / date
            date_dir.mkdir(parents=True, exist_ok=True)
            
            output_file = date_dir / "participants.csv.gz"
            fallback_data.to_csv(output_file, index=False, compression='gzip')
            
            results[date] = fallback_data
            size_kb = output_file.stat().st_size / 1024
            print(f"    {date}/participants.csv.gz: {len(fallback_data):,} records ({size_kb:.1f} KB) [FALLBACK from {latest_date}]")
    
    return results


def _process_nbbo_data(file_list, timestamp_col, orders_by_partition, processed_dir, security_col):
    """Process NBBO data partitioned by date and security."""
    if not file_list:
        return {}
    
    df = _read_csv_files_concat(file_list)
    if df is None or len(df) == 0:
        return {}
    
    df = add_date_column(df, timestamp_col)
    return _partition_by_date_security_and_save(df, orders_by_partition, processed_dir, "nbbo.csv.gz", col.common.date, security_col)


def _filter_sweep_orders_by_execution(orders_df):
    """Filter sweep orders that completed execution."""
    order_id_col = col.common.orderid
    timestamp_col = col.common.timestamp
    order_type_col = col.common.exchangeordertype
    changereason_col = col.common.changereason
    leavesqty_col = col.common.leavesquantity
    
    sweep_orders = orders_df[orders_df[order_type_col] == SWEEP_ORDER_TYPE].copy()
    
    if len(sweep_orders) == 0:
        return []
    
    sweep_orders_sorted = sweep_orders.sort_values([order_id_col, timestamp_col])
    
    qualifying_order_ids = []
    
    for order_id, group in sweep_orders_sorted.groupby(order_id_col):
        final_state = group.iloc[-1]
        
        if final_state[changereason_col] == 3 and final_state[leavesqty_col] == 0:
            if (group[changereason_col] == 6).any():
                qualifying_order_ids.append(order_id)
    
    return qualifying_order_ids


def _filter_orders_with_valid_trades(order_ids, trades_df):
    """Filter orders that have trades with dealsource=1."""
    trade_orderid_col = col.common.orderid
    
    qualifying_trades = trades_df[trades_df[trade_orderid_col].isin(order_ids)].copy()
    
    orders_with_valid_trades = {}
    
    for order_id in order_ids:
        order_trades = qualifying_trades[qualifying_trades[trade_orderid_col] == order_id]
        
        if len(order_trades) == 0:
            continue
        
        dealsources = order_trades['dealsource'].unique()
        if not (len(dealsources) == 1 and dealsources[0] == 1):
            continue
        
        orders_with_valid_trades[order_id] = order_trades
    
    return orders_with_valid_trades


def _extract_execution_time_dict(order_id, order_df, trades_df):
    """Extract first execution time from orders and last execution time from trades."""
    timestamp_col = col.common.timestamp
    trade_time_col = col.common.tradetime
    
    first_time = order_df[timestamp_col].min()
    last_time = trades_df[trade_time_col].max()
    
    return {
        'orderid': order_id,
        'first_execution_time': first_time,
        'last_execution_time': last_time
    }


def _save_execution_times(partition_key, execution_times_df, processed_dir):
    """Save execution times DataFrame to partition directory."""
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    partition_dir.mkdir(parents=True, exist_ok=True)
    
    exec_file = partition_dir / "last_execution_time.csv"
    execution_times_df.to_csv(exec_file, index=False)


def extract_orders(input_file, processed_dir, order_types, chunk_size, column_mapping):
    """Extract Centre Point orders and partition by date/security."""
    print(f"\n[1/11] Extracting Centre Point orders from {input_file}...")
    
    order_type_col = col.orders.order_type
    timestamp_col = col.orders.timestamp
    security_col = col.orders.security_code
    
    orders_list = []
    total_rows = 0
    
    for chunk in pd.read_csv(input_file, chunksize=chunk_size, low_memory=False):
        total_rows += len(chunk)
        cp_chunk = chunk[chunk[order_type_col].isin(order_types)].copy()
        
        if len(cp_chunk) > 0:
            cp_chunk = add_date_column(cp_chunk, timestamp_col)
            orders_list.append(cp_chunk)
    
    if not orders_list:
        print("  No Centre Point orders found!")
        return {}
    
    orders = pd.concat(orders_list, ignore_index=True)
    print(f"  Found {len(orders):,} Centre Point orders from {total_rows:,} total rows")
    
    # Partition by date/security
    partitions = {}
    for (date, security_code_val), group_df in orders.groupby(['date', security_col]):
        partition_key = f"{date}/{security_code_val}"
        
        # Normalize column names to standard before saving
        group_df_normalized = normalize_to_standard_names(group_df, 'orders')
        
        # Store normalized version for downstream use
        partitions[partition_key] = group_df_normalized
        
        # Save to processed directory
        partition_dir = Path(processed_dir) / date / str(security_code_val)
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        partition_file = partition_dir / "cp_orders_filtered.csv.gz"
        group_df_normalized.to_csv(partition_file, index=False, compression='gzip')
        
        size_mb = partition_file.stat().st_size / (1024 * 1024)
        print(f"  {partition_key}: {len(group_df):,} orders ({size_mb:.2f} MB)")
    
    return partitions


def extract_trades(input_file, orders_by_partition, processed_dir, column_mapping, chunk_size):
    """Extract trades matching order_ids from partitions."""
    print(f"\n[2/11] Extracting matching trades from {input_file}...")
    
    order_id_col_orders = 'orderid'
    order_id_col_trades = col.trades.order_id
    trade_time_col = col.trades.trade_time
    
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
            # Normalize column names to standard before saving
            partition_trades_normalized = normalize_to_standard_names(partition_trades, 'trades')
            
            # Store normalized version for downstream use
            trades_by_partition[partition_key] = partition_trades_normalized
            
            # Save to processed directory
            date, security_code = partition_key.split('/')
            partition_dir = Path(processed_dir) / date / security_code
            partition_dir.mkdir(parents=True, exist_ok=True)
            
            partition_file = partition_dir / "cp_trades_matched.csv.gz"
            partition_trades_normalized.to_csv(partition_file, index=False, compression='gzip')
            
            size_mb = partition_file.stat().st_size / (1024 * 1024)
            unique_orders = partition_trades[order_id_col_trades].nunique()
            print(f"  {partition_key}: {len(partition_trades):,} trades, {unique_orders:,} orders ({size_mb:.2f} MB)")
    
    return trades_by_partition


def aggregate_trades(orders_by_partition, trades_by_partition, processed_dir, column_mapping):
    """Aggregate trades by order_id per partition."""
    print(f"\n[3/11] Aggregating trades by order...")
    
    order_id_col = col.common.orderid
    trade_time_col = col.common.tradetime
    trade_price_col = col.common.tradeprice
    quantity_col = col.common.quantity
    
    trades_agg_by_partition = {}
    
    for partition_key, trades_df in trades_by_partition.items():
        if len(trades_df) == 0:
            continue
        
        # Calculate price*quantity for VWAP
        trades_df = trades_df.copy()
        trades_df['price_qty_product'] = trades_df[trade_price_col] * trades_df[quantity_col]
        
        # Aggregate by order ID
        agg_dict = {
            quantity_col: 'sum',
            trade_price_col: 'mean',
            'price_qty_product': 'sum',  # For VWAP calculation
            trade_time_col: ['min', 'max', 'count']
        }
        
        trades_agg = trades_df.groupby(order_id_col).agg(agg_dict).reset_index()
        
        # Flatten column names
        trades_agg.columns = [
            'orderid',
            'total_quantity_filled',
            'avg_execution_price',
            'price_qty_product_sum',
            'first_trade_time',
            'last_trade_time',
            'num_trades'
        ]
        
        # Calculate VWAP: sum(price * quantity) / sum(quantity)
        trades_agg['vwap'] = trades_agg['price_qty_product_sum'] / trades_agg['total_quantity_filled']
        
        # Drop intermediate calculation column
        trades_agg = trades_agg.drop('price_qty_product_sum', axis=1)
        
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




def process_reference_data(raw_folders, processed_dir, orders_by_partition, column_mapping):
    """Process and partition all reference data files."""
    print(f"\n[4/11] Processing reference data files...")
    
    unique_dates = sorted(set(pk.split('/')[0] for pk in orders_by_partition.keys()))
    unique_securities = {}
    for pk in orders_by_partition.keys():
        date, security = pk.split('/')
        if date not in unique_securities:
            unique_securities[date] = []
        unique_securities[date].append(security)
    
    print(f"  Target dates: {unique_dates}")
    print(f"  Target securities by date: {unique_securities}")
    
    results = {'session': {}, 'reference': {}, 'participants': {}, 'nbbo': {}}
    
    session_files = list(Path(raw_folders['session']).glob('*.csv'))
    if session_files:
        print(f"\n  Processing Session data from {len(session_files)} file(s)...")
        results['session'] = _process_single_reference_type(
            session_files, col.session.timestamp, unique_dates, processed_dir, 'session.csv.gz'
        )
    else:
        print(f"\n  Processing Session data from 0 file(s)...")
    
    reference_files = list(Path(raw_folders['reference']).glob('*.csv'))
    if reference_files:
        print(f"\n  Processing Reference data from {len(reference_files)} file(s)...")
        results['reference'] = _process_single_reference_type(
            reference_files, col.reference.timestamp, unique_dates, processed_dir, 'reference.csv.gz'
        )
    else:
        print(f"\n  Processing Reference data from 0 file(s)...")
    
    participants_files = list(Path(raw_folders['participants']).glob('*.csv'))
    if participants_files:
        print(f"\n  Processing Participants data from {len(participants_files)} file(s)...")
        results['participants'] = _process_participants_with_fallback(
            participants_files, col.participants.timestamp, unique_dates, processed_dir
        )
    else:
        print(f"\n  Processing Participants data from 0 file(s)...")
    
    nbbo_files = list(Path(raw_folders['nbbo']).glob('*.csv'))
    if nbbo_files:
        print(f"\n  Processing NBBO data from {len(nbbo_files)} file(s)...")
        results['nbbo'] = _process_nbbo_data(
            nbbo_files, col.nbbo.timestamp, orders_by_partition, processed_dir, col.nbbo.security_code
        )
    else:
        print(f"\n  Processing NBBO data from 0 file(s)...")
    
    print(f"\n  Summary:")
    print(f"    Session: {len(results['session'])} dates processed")
    print(f"    Reference: {len(results['reference'])} dates processed")
    print(f"    Participants: {len(results['participants'])} dates processed")
    print(f"    NBBO: {len(results['nbbo'])} partitions processed")
    
    return results


def get_orders_state(orders_by_partition, processed_dir, column_mapping):
    """Extract before/after/final order states per partition."""
    print(f"\n[5/11] Extracting order states...")
    
    order_id_col = col.common.orderid
    timestamp_col = col.common.timestamp
    sequence_col = col.common.sequence
    
    order_states_by_partition = {}
    
    # Create debug directory
    debug_dir = Path(processed_dir).parent / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    
    for partition_key, orders_df in orders_by_partition.items():
        if len(orders_df) == 0:
            continue
        
        date, security_code = partition_key.split('/')
        
        # Sort by timestamp, then sequence (ascending)
        orders_sorted = orders_df.sort_values([timestamp_col, sequence_col])
        
        # Get minimum and maximum timestamps per order
        min_timestamps = orders_sorted.groupby(order_id_col)[timestamp_col].min()
        max_timestamps = orders_sorted.groupby(order_id_col)[timestamp_col].max()
        
        orders_before_list = []
        orders_after_list = []
        orders_final_list = []
        
        for order_id in min_timestamps.index:
            min_ts = min_timestamps[order_id]
            max_ts = max_timestamps[order_id]
            
            # Filter to records at minimum timestamp for this order
            records_at_min_ts = orders_sorted[
                (orders_sorted[order_id_col] == order_id) & 
                (orders_sorted[timestamp_col] == min_ts)
            ]
            
            # BEFORE: First record at min timestamp (min sequence)
            orders_before_list.append(records_at_min_ts.iloc[0])
            
            # AFTER: Last record at min timestamp (max sequence)
            orders_after_list.append(records_at_min_ts.iloc[-1])
            
            # Filter to records at maximum timestamp for this order
            records_at_max_ts = orders_sorted[
                (orders_sorted[order_id_col] == order_id) & 
                (orders_sorted[timestamp_col] == max_ts)
            ]
            
            # FINAL: Last record at max timestamp (max sequence) - for debugging
            orders_final_list.append(records_at_max_ts.iloc[-1])
        
        orders_before = pd.DataFrame(orders_before_list).reset_index(drop=True)
        orders_after = pd.DataFrame(orders_after_list).reset_index(drop=True)
        orders_final = pd.DataFrame(orders_final_list).reset_index(drop=True)
        
        order_states_by_partition[partition_key] = {
            'before': orders_before,
            'after': orders_after,
            'final': orders_final
        }
        
        # Save to processed directory
        partition_dir = Path(processed_dir) / date / security_code
        partition_dir.mkdir(parents=True, exist_ok=True)
        
        before_file = partition_dir / "orders_before_matching.csv"
        after_file = partition_dir / "orders_after_matching.csv"
        final_file = partition_dir / "orders_final_state.csv"
        
        orders_before.to_csv(before_file, index=False)
        orders_after.to_csv(after_file, index=False)
        orders_final.to_csv(final_file, index=False)
        
        # Save to DEBUG directory with naming convention: orders_final_{date}_{orderbookid}.csv
        debug_file = debug_dir / f"orders_final_{date}_{security_code}.csv"
        orders_final.to_csv(debug_file, index=False)
        
        print(f"  {partition_key}: {len(orders_before):,} before, {len(orders_after):,} after, {len(orders_final):,} final")
    
    return order_states_by_partition


def extract_last_execution_times(orders_by_partition, trades_by_partition, processed_dir, column_mapping):
    """Extract first and last execution times for SWEEP ORDERS ONLY."""
    print(f"\n[6/11] Extracting execution times for qualifying sweep orders (type {SWEEP_ORDER_TYPE}) with three-level filtering...")
    
    execution_times_by_partition = {}
    
    for partition_key, orders_df in orders_by_partition.items():
        if len(orders_df) == 0:
            continue
        
        qualifying_order_ids = _filter_sweep_orders_by_execution(orders_df)
        
        if not qualifying_order_ids or partition_key not in trades_by_partition:
            execution_times_df = pd.DataFrame(columns=['orderid', 'first_execution_time', 'last_execution_time'])
            execution_times_by_partition[partition_key] = execution_times_df
            _save_execution_times(partition_key, execution_times_df, processed_dir)
            print(f"  {partition_key}: 0 qualifying sweep orders")
            continue
        
        trades_df = trades_by_partition[partition_key]
        orders_with_valid_trades = _filter_orders_with_valid_trades(qualifying_order_ids, trades_df)
        
        execution_times = []
        for order_id, order_trades in orders_with_valid_trades.items():
            order_data = orders_df[orders_df[col.common.orderid] == order_id]
            exec_time = _extract_execution_time_dict(order_id, order_data, order_trades)
            execution_times.append(exec_time)
        
        execution_times_df = pd.DataFrame(execution_times) if execution_times else pd.DataFrame(columns=['orderid', 'first_execution_time', 'last_execution_time'])
        execution_times_by_partition[partition_key] = execution_times_df
        _save_execution_times(partition_key, execution_times_df, processed_dir)
        print(f"  {partition_key}: {len(execution_times_df):,} qualifying sweep orders")
    
    return execution_times_by_partition


def load_partition_data(partition_key, processed_dir):
    """Load all necessary data for a partition for simulation."""
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
    
    # Load orders_final_state
    final_file = partition_dir / "orders_final_state.csv"
    if final_file.exists():
        partition_data['orders_final'] = pd.read_csv(final_file)
    
    # Load last_execution_time
    exec_file = partition_dir / "last_execution_time.csv"
    if exec_file.exists():
        partition_data['last_execution'] = pd.read_csv(exec_file)
    else:
        # Create empty DataFrame if no execution times
        partition_data['last_execution'] = pd.DataFrame(columns=['orderid', 'first_execution_time', 'last_execution_time'])
    
    return partition_data


def classify_order_groups(orders_by_partition, processed_dir, column_mapping):
    """Classify sweep orders into groups based on real execution results."""
    print(f"\n[9/11] Classifying sweep order groups (type 2048 only)...")
    
    order_type_col = col.common.exchangeordertype
    leaves_qty_col = col.common.leavesquantity
    matched_qty_col = col.common.matched_quantity
    
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
        sweep_orders = orders_after[orders_after[order_type_col] == SWEEP_ORDER_TYPE].copy()
        
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
