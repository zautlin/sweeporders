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
import numpy as np
from pathlib import Path
from config.config import SWEEP_ORDER_TYPE, PROJECT_ROOT
import config.config as _cfg
from config.column_schema import col
from utils.normalization import normalize_column_names


def _should_save():
    """Return True if intermediate partition files should be written to disk."""
    return _cfg.PROCESSING_MODE == 'file'


# Session state constants (per dd.txt spec)
MATCHING_SESSION_STATES = {'OPEN'}  # Only OPEN state allows continuous matching


def add_date_column(df, timestamp_col):
    """Add date column from timestamp (convert UTC to AEST)."""
    df[col.common.date] = (pd.to_datetime(df[timestamp_col], unit='ns')
                    .dt.tz_localize('UTC')
                    .dt.tz_convert('Australia/Sydney')
                    .dt.strftime('%Y-%m-%d'))
    return df


def load_session_data(date, orderbookid):
    """
    Load session state data for given date and orderbook.
    
    Session data defines trading phases (PRE_OPEN, OPEN, AUCTION, etc.)
    Per dd.txt spec, matching only allowed during OPEN state.
    
    Args:
        date: Date string (YYYY-MM-DD or YYYYMMDD)
        orderbookid: Order book ID (integer)
    
    Returns:
        DataFrame: Session states with timestamps
    """
    # Normalize date format
    date_str = date.replace('-', '')
    session_file = PROJECT_ROOT / 'data' / 'raw' / 'session' / f'{date_str}_session.csv'
    
    if not session_file.exists():
        print(f"  Warning: Session file not found: {session_file}")
        return None
    
    session_df = pd.read_csv(session_file)
    
    # Clean column names (remove leading spaces)
    session_df.columns = session_df.columns.str.strip()
    
    # Filter for this orderbook
    orderbook_sessions = session_df[
        session_df['OrderBookId'] == orderbookid
    ].copy()
    
    if len(orderbook_sessions) == 0:
        print(f"  Warning: No session data for orderbook {orderbookid}")
        return None
    
    # Convert Timestamp to nanoseconds (already in nanoseconds in raw data)
    orderbook_sessions['timestamp'] = orderbook_sessions['Timestamp'].astype('int64')
    
    # Sort by timestamp
    orderbook_sessions = orderbook_sessions.sort_values('timestamp').reset_index(drop=True)
    
    # Select relevant columns
    result = orderbook_sessions[['timestamp', 'Name', 'OrderBookId', 'isEndOfTrading']].copy()
    result.columns = ['timestamp', 'session_state', 'orderbookid', 'is_end_of_trading']
    
    return result


def get_session_times(session_df):
    """
    Get session start and end times from session data.
    
    Args:
        session_df: Session DataFrame from load_session_data()
    
    Returns:
        tuple: (session_start_ns, session_end_ns) or (None, None) if no data
    """
    if session_df is None or len(session_df) == 0:
        return None, None
    
    # Find OPEN session start
    open_sessions = session_df[session_df['session_state'] == 'OPEN']
    
    if len(open_sessions) > 0:
        session_start = open_sessions['timestamp'].min()
    else:
        # Fallback: use first timestamp
        session_start = session_df['timestamp'].min()
    
    # Session end is last timestamp
    session_end = session_df['timestamp'].max()
    
    return session_start, session_end


def is_matching_allowed(session_state):
    """
    Check if matching is allowed in current session state.
    
    Per ASX bi.txt Section 8:
    - OPEN: Continuous matching allowed
    - PRE_OPEN, AUCTION, CLOSE: No continuous matching
    
    Args:
        session_state: Session state name (string)
    
    Returns:
        bool: True if matching allowed
    """
    return session_state in MATCHING_SESSION_STATES


def filter_orders_by_session(orders_df, session_df):
    """
    Filter orders to only those in matching-allowed sessions.
    
    Args:
        orders_df: Orders DataFrame
        session_df: Session DataFrame from load_session_data()
    
    Returns:
        DataFrame: Filtered orders
    """
    if session_df is None or len(session_df) == 0:
        # No session data, return all orders
        return orders_df
    
    # Merge orders with session states
    orders_with_state = pd.merge_asof(
        orders_df.sort_values('timestamp'),
        session_df.sort_values('timestamp'),
        on='timestamp',
        direction='backward',
        suffixes=('', '_session')
    )
    
    # Filter to matching-allowed states
    matching_orders = orders_with_state[
        orders_with_state['session_state'].apply(is_matching_allowed)
    ].copy()
    
    # Drop session columns
    cols_to_drop = [c for c in matching_orders.columns if c.endswith('_session') or c == 'session_state']
    matching_orders = matching_orders.drop(columns=cols_to_drop, errors='ignore')
    
    return matching_orders


def calculate_order_lifetime(order_row, session_start_ns, session_end_ns):
    """
    Calculate order lifetime based on timevalidity field.
    
    Per dd.txt spec p.870:
    - timevalidity field controls how long order stays in book
    - MSB (most significant byte) defines the unit
    - LSB (least significant byte) defines the value
    
    Time validity types:
    - 0 (Bouncing/IOC): Order expires immediately
    - 1 (Rest of Day): Order valid until end of session
    - 2 (GTC): Order valid for 30 days
    - 5 (Days): Order valid for N days (LSB = days)
    - 6 (Current Max): Order valid until end of current session
    
    Args:
        order_row: Order DataFrame row
        session_start_ns: Session start timestamp (nanoseconds)
        session_end_ns: Session end timestamp (nanoseconds)
    
    Returns:
        tuple: (start_timestamp_ns, end_timestamp_ns)
    """
    timestamp = int(order_row.get('timestamp', 0))
    timevalidity = int(order_row.get('timevalidity', 1))  # Default: Rest of Day
    
    # Handle NaN timevalidity
    if pd.isna(timevalidity):
        timevalidity = 1
    
    # Extract MSB and LSB
    msb = (timevalidity >> 8) & 0xFF  # Most significant byte
    lsb = timevalidity & 0xFF  # Least significant byte
    
    # Calculate end time based on timevalidity type
    if msb == 0:  # Bouncing / IOC
        # Order expires immediately (within 1 second)
        return timestamp, timestamp + 1_000_000_000
    
    elif msb == 1:  # Rest of Day
        # Order valid until end of session
        return timestamp, session_end_ns
    
    elif msb == 2:  # Good Till Canceled
        # Order valid for 30 days by default
        return timestamp, timestamp + 30 * 24 * 3600 * 1_000_000_000
    
    elif msb == 5:  # Days
        # Order valid for N days (LSB = number of days)
        days = lsb if lsb > 0 else 30  # Default to 30 if LSB is 0
        return timestamp, timestamp + days * 24 * 3600 * 1_000_000_000
    
    elif msb == 6:  # Current Max
        # Order valid until end of current session type
        return timestamp, session_end_ns
    
    else:
        # Unknown timevalidity, default to Rest of Day
        return timestamp, session_end_ns


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
            results[date] = date_data
            if _should_save():
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                output_file = date_dir / filename
                date_data.to_csv(output_file, index=False, compression='gzip')
                size_kb = output_file.stat().st_size / 1024
                print(f"    {date}/{filename}: {len(date_data):,} records ({size_kb:.1f} KB)")
            else:
                print(f"    {date}/{filename}: {len(date_data):,} records (in-memory)")
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
            partition_data_normalized = normalize_column_names(partition_data, 'nbbo')
            results[partition_key] = partition_data_normalized
            if _should_save():
                partition_dir = Path(processed_dir) / date / orderbookid
                partition_dir.mkdir(parents=True, exist_ok=True)
                output_file = partition_dir / filename
                partition_data_normalized.to_csv(output_file, index=False, compression='gzip')
                size_kb = output_file.stat().st_size / 1024
                print(f"    {partition_key}/{filename}: {len(partition_data):,} records ({size_kb:.1f} KB)")
            else:
                print(f"    {partition_key}/{filename}: {len(partition_data):,} records (in-memory)")
        else:
            print(f"    {partition_key}/{filename}: NO DATA (missing in raw files)")

    return results

def _process_single_reference_type(file_list, timestamp_col, unique_dates, processed_dir, filename, data_type):
    """Process single reference data type: read files, add date, normalize, partition, save."""
    if not file_list:
        return {}
    
    df = _read_csv_files_concat(file_list)
    if df is None or len(df) == 0:
        return {}
    
    df = add_date_column(df, timestamp_col)
    
    # Normalize column names AFTER adding date column
    df = normalize_column_names(df, data_type)
    
    return _partition_by_date_and_save(df, unique_dates, processed_dir, filename, col.common.date)


def _process_participants_with_fallback(file_list, timestamp_col, unique_dates, processed_dir):
    """Process participants data with latest-date fallback for missing dates."""
    if not file_list:
        return {}
    
    df = _read_csv_files_concat(file_list)
    if df is None or len(df) == 0:
        return {}
    
    df = add_date_column(df, timestamp_col)
    
    # Normalize column names AFTER adding date column
    df = normalize_column_names(df, 'participants')
    
    all_participant_dates = df[col.common.date].unique()
    print(f"    Available dates in participants: {sorted(all_participant_dates)}")
    
    results = {}
    
    for date in unique_dates:
        date_data = df[df[col.common.date] == date].copy()
        
        if len(date_data) > 0:
            results[date] = date_data
            if _should_save():
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                output_file = date_dir / "participants.csv.gz"
                date_data.to_csv(output_file, index=False, compression='gzip')
                size_kb = output_file.stat().st_size / 1024
                print(f"    {date}/participants.csv.gz: {len(date_data):,} records ({size_kb:.1f} KB)")
            else:
                print(f"    {date}/participants.csv.gz: {len(date_data):,} records (in-memory)")
        else:
            latest_date = max(all_participant_dates)
            fallback_data = df[df[col.common.date] == latest_date].copy()
            results[date] = fallback_data
            if _should_save():
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                output_file = date_dir / "participants.csv.gz"
                fallback_data.to_csv(output_file, index=False, compression='gzip')
                size_kb = output_file.stat().st_size / 1024
                print(f"    {date}/participants.csv.gz: {len(fallback_data):,} records ({size_kb:.1f} KB) [FALLBACK from {latest_date}]")
            else:
                print(f"    {date}/participants.csv.gz: {len(fallback_data):,} records (in-memory) [FALLBACK from {latest_date}]")
    
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
    sweep_orders = orders_df[orders_df[col.common.exchangeordertype] == SWEEP_ORDER_TYPE].copy()
    
    if len(sweep_orders) == 0:
        return []
    
    sweep_orders_sorted = sweep_orders.sort_values([col.common.orderid, col.common.timestamp])
    
    qualifying_order_ids = []
    
    for order_id, group in sweep_orders_sorted.groupby(col.common.orderid):
        final_state = group.iloc[-1]
        
        if final_state[col.common.changereason] == 3 and final_state[col.common.leavesquantity] == 0:
            if (group[col.common.changereason] == 6).any():
                qualifying_order_ids.append(order_id)
    
    return qualifying_order_ids


def _filter_orders_with_valid_trades(order_ids, trades_df):
    """Filter orders that have trades with dealsource=1."""
    qualifying_trades = trades_df[trades_df[col.common.orderid].isin(order_ids)].copy()
    
    orders_with_valid_trades = {}
    
    for order_id in order_ids:
        order_trades = qualifying_trades[qualifying_trades[col.common.orderid] == order_id]
        
        if len(order_trades) == 0:
            continue
        
        dealsources = order_trades[col.trades.dealsource].unique()
        if not (len(dealsources) == 1 and dealsources[0] == 1):
            continue
        
        orders_with_valid_trades[order_id] = order_trades
    
    return orders_with_valid_trades


def _extract_execution_time_dict(order_id, order_df, trades_df):
    """Extract first execution time from orders and last execution time from trades."""
    first_time = order_df[col.common.timestamp].min()
    last_time = trades_df[col.common.tradetime].max()
    
    return {
        'orderid': order_id,
        'first_execution_time': first_time,
        'last_execution_time': last_time
    }


def _save_execution_times(partition_key, execution_times_df, processed_dir):
    """Save execution times DataFrame to partition directory."""
    if not _should_save():
        return
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    partition_dir.mkdir(parents=True, exist_ok=True)
    execution_times_df.to_csv(partition_dir / "last_execution_time.csv", index=False)


def extract_orders(input_file, processed_dir, order_types, chunk_size):
    """Extract Centre Point orders and partition by date/security."""
    print(f"\n[1/11] Extracting Centre Point orders from {input_file}...")

    if _cfg.USE_DUCKDB_IO:
        import polars as pl
        from utils.io_backend import get_conn, duck_to_polars
        conn = get_conn()
        types_sql = ','.join(str(t) for t in order_types)
        orders_pl = duck_to_polars(conn.execute(f"""
            SELECT * FROM read_csv_auto('{input_file}')
            WHERE {col.orders.order_type} IN ({types_sql})
        """))
        total_rows = conn.execute(f"SELECT COUNT(*) FROM read_csv_auto('{input_file}')").fetchone()[0]
        # Replicate add_date_column: UTC ns epoch → AEST → '%Y-%m-%d'
        orders_pl = orders_pl.with_columns(
            pl.from_epoch(pl.col(col.orders.timestamp), time_unit='ns')
              .dt.replace_time_zone('UTC')
              .dt.convert_time_zone('Australia/Sydney')
              .dt.strftime('%Y-%m-%d')
              .alias(col.common.date)
        )
        orders = orders_pl.to_pandas()
        if len(orders) == 0:
            print("  No Centre Point orders found!")
            return {}
        print(f"  Found {len(orders):,} Centre Point orders from {total_rows:,} total rows")
    else:
        orders_list = []
        total_rows = 0

        for chunk in pd.read_csv(input_file, chunksize=chunk_size, low_memory=False):
            total_rows += len(chunk)
            cp_chunk = chunk[chunk[col.orders.order_type].isin(order_types)].copy()

            if len(cp_chunk) > 0:
                cp_chunk = add_date_column(cp_chunk, col.orders.timestamp)
                orders_list.append(cp_chunk)

        if not orders_list:
            print("  No Centre Point orders found!")
            return {}

        orders = pd.concat(orders_list, ignore_index=True)
        print(f"  Found {len(orders):,} Centre Point orders from {total_rows:,} total rows")
    
    # Partition by date/security
    partitions = {}
    for (date, security_code_val), group_df in orders.groupby(['date', col.orders.security_code]):
        partition_key = f"{date}/{security_code_val}"
        
        # Normalize column names to standard before saving
        group_df_normalized = normalize_column_names(group_df, 'orders')
        
        # Store normalized version for downstream use
        partitions[partition_key] = group_df_normalized

        if _should_save():
            partition_dir = Path(processed_dir) / date / str(security_code_val)
            partition_dir.mkdir(parents=True, exist_ok=True)
            partition_file = partition_dir / "cp_orders_filtered.csv.gz"
            group_df_normalized.to_csv(partition_file, index=False, compression='gzip')
            size_mb = partition_file.stat().st_size / (1024 * 1024)
            print(f"  {partition_key}: {len(group_df):,} orders ({size_mb:.2f} MB)")
        else:
            print(f"  {partition_key}: {len(group_df):,} orders (in-memory)")
    
    return partitions


def extract_trades(input_file, orders_by_partition, processed_dir, chunk_size):
    """Extract trades matching order_ids from partitions."""
    print(f"\n[2/11] Extracting matching trades from {input_file}...")
    
    order_id_col_orders = 'orderid'
    
    # Collect all order IDs
    all_order_ids = set()
    partition_order_ids = {}
    
    for partition_key, orders_df in orders_by_partition.items():
        order_ids = set(orders_df[order_id_col_orders].unique())
        partition_order_ids[partition_key] = order_ids
        all_order_ids.update(order_ids)
    
    print(f"  Looking for {len(all_order_ids):,} order IDs across {len(orders_by_partition)} partitions")

    if _cfg.USE_DUCKDB_IO:
        import polars as pl
        from utils.io_backend import get_conn, duck_to_polars
        conn = get_conn()
        # Use a temp table join to avoid huge IN-list string interpolation
        conn.execute("CREATE OR REPLACE TEMP TABLE _target_ids (orderid BIGINT)")
        conn.executemany("INSERT INTO _target_ids VALUES (?)", [(int(i),) for i in all_order_ids])
        trades_pl = duck_to_polars(conn.execute(f"""
            SELECT t.* FROM read_csv_auto('{input_file}') t
            JOIN _target_ids i ON t.{col.trades.order_id} = i.orderid
        """))
        total_rows = conn.execute(f"SELECT COUNT(*) FROM read_csv_auto('{input_file}')").fetchone()[0]
        trades_pl = trades_pl.with_columns(
            pl.from_epoch(pl.col(col.trades.trade_time), time_unit='ns')
              .dt.replace_time_zone('UTC')
              .dt.convert_time_zone('Australia/Sydney')
              .dt.strftime('%Y-%m-%d')
              .alias(col.common.date)
        )
        all_trades = trades_pl.to_pandas()
        if len(all_trades) == 0:
            print("  No matching trades found!")
            return {}
        print(f"  Found {len(all_trades):,} trades from {total_rows:,} total rows")
    else:
        # Read and filter trades
        trades_list = []
        total_rows = 0

        for chunk in pd.read_csv(input_file, chunksize=chunk_size, low_memory=False):
            total_rows += len(chunk)
            matched_chunk = chunk[chunk[col.trades.order_id].isin(all_order_ids)].copy()

            if len(matched_chunk) > 0:
                matched_chunk = add_date_column(matched_chunk, col.trades.trade_time)
                trades_list.append(matched_chunk)

        if not trades_list:
            print("  No matching trades found!")
            return {}

        all_trades = pd.concat(trades_list, ignore_index=True)
        print(f"  Found {len(all_trades):,} trades from {total_rows:,} total rows")
    
    # Partition trades to match order partitions
    trades_by_partition = {}
    
    for partition_key, order_ids in partition_order_ids.items():
        partition_trades = all_trades[all_trades[col.trades.order_id].isin(order_ids)].copy()
        
        if len(partition_trades) > 0:
            # Normalize column names to standard before saving
            partition_trades_normalized = normalize_column_names(partition_trades, 'trades')
            
            # Store normalized version for downstream use
            trades_by_partition[partition_key] = partition_trades_normalized

            unique_orders = partition_trades[col.trades.order_id].nunique()
            if _should_save():
                date, security_code = partition_key.split('/')
                partition_dir = Path(processed_dir) / date / security_code
                partition_dir.mkdir(parents=True, exist_ok=True)
                partition_file = partition_dir / "cp_trades_matched.csv.gz"
                partition_trades_normalized.to_csv(partition_file, index=False, compression='gzip')
                size_mb = partition_file.stat().st_size / (1024 * 1024)
                print(f"  {partition_key}: {len(partition_trades):,} trades, {unique_orders:,} orders ({size_mb:.2f} MB)")
            else:
                print(f"  {partition_key}: {len(partition_trades):,} trades, {unique_orders:,} orders (in-memory)")
    
    return trades_by_partition


def aggregate_trades(orders_by_partition, trades_by_partition, processed_dir):
    """Aggregate trades by order_id per partition."""
    print(f"\n[3/11] Aggregating trades by order...")
    
    trades_agg_by_partition = {}
    
    for partition_key, trades_df in trades_by_partition.items():
        if len(trades_df) == 0:
            continue
        
        if _cfg.USE_DUCKDB_IO:
            import polars as pl
            from utils.io_backend import get_conn, duck_to_polars
            conn = get_conn()
            conn.register('_trades', trades_df.to_arrow() if hasattr(trades_df, 'to_arrow') else __import__('pyarrow').Table.from_pandas(trades_df))
            agg_pl = duck_to_polars(conn.execute(f"""
                SELECT
                    {col.common.orderid}                                              AS orderid,
                    SUM({col.common.quantity})                                        AS total_quantity_filled,
                    AVG({col.common.tradeprice})                                      AS avg_execution_price,
                    SUM({col.common.tradeprice} * {col.common.quantity})
                      / SUM({col.common.quantity})                                    AS vwap,
                    MIN({col.common.tradetime})                                       AS first_trade_time,
                    MAX({col.common.tradetime})                                       AS last_trade_time,
                    COUNT(*)                                                          AS num_trades,
                    (MAX({col.common.tradetime}) - MIN({col.common.tradetime})) / 1e9 AS execution_duration_sec
                FROM _trades
                GROUP BY {col.common.orderid}
            """))
            trades_agg = agg_pl.to_pandas()
        else:
            # Calculate price*quantity for VWAP
            trades_df = trades_df.copy()
            trades_df['price_qty_product'] = trades_df[col.common.tradeprice] * trades_df[col.common.quantity]

            # Aggregate by order ID
            agg_dict = {
                col.common.quantity: 'sum',
                col.common.tradeprice: 'mean',
                'price_qty_product': 'sum',  # For VWAP calculation
                col.common.tradetime: ['min', 'max', 'count']
            }

            trades_agg = trades_df.groupby(col.common.orderid).agg(agg_dict).reset_index()

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




def process_reference_data(raw_folders, processed_dir, orders_by_partition):
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
            session_files, col.session.timestamp, unique_dates, processed_dir, 'session.csv.gz', 'session'
        )
    else:
        print(f"\n  Processing Session data from 0 file(s)...")
    
    reference_files = list(Path(raw_folders['reference']).glob('*.csv'))
    if reference_files:
        print(f"\n  Processing Reference data from {len(reference_files)} file(s)...")
        results['reference'] = _process_single_reference_type(
            reference_files, col.reference.timestamp, unique_dates, processed_dir, 'reference.csv.gz', 'reference'
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


def get_orders_state(orders_by_partition, processed_dir):
    """Extract before/after/final order states per partition."""
    print(f"\n[5/11] Extracting order states...")
    
    order_states_by_partition = {}
    
    # Create debug directory
    debug_dir = Path(processed_dir).parent / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    
    for partition_key, orders_df in orders_by_partition.items():
        if len(orders_df) == 0:
            continue
        
        date, security_code = partition_key.split('/')
        
        # Sort by timestamp, then sequence (ascending)
        orders_sorted = orders_df.sort_values([col.common.timestamp, col.common.sequence])

        # BEFORE state: the NEW ORDER submission event (changereason=1).
        # Using min-timestamp is wrong because the data file includes session-cleanup
        # cancellations (changereason=6) that appear earlier than the actual submission.
        # Orders that have no changereason=1 event (pure cleanup artefacts) are excluded.
        CHANGEREASON_NEW = 1
        if 'changereason' in orders_sorted.columns:
            new_order_rows = orders_sorted[orders_sorted['changereason'] == CHANGEREASON_NEW]
            orders_before = new_order_rows.drop_duplicates(
                subset=[col.common.orderid], keep='first'
            ).reset_index(drop=True)
        else:
            # Fallback when changereason is unavailable
            orders_before = orders_sorted.drop_duplicates(
                subset=[col.common.orderid], keep='first'
            ).reset_index(drop=True)

        # AFTER state: the last event per order (captures final cancellation/fill)
        orders_after = orders_sorted.drop_duplicates(
            subset=[col.common.orderid], keep='last'
        ).reset_index(drop=True)
        
        order_states_by_partition[partition_key] = {
            'before': orders_before,
            'after': orders_after
        }

        if _should_save():
            partition_dir = Path(processed_dir) / date / security_code
            partition_dir.mkdir(parents=True, exist_ok=True)
            orders_before.to_csv(partition_dir / "orders_before_matching.csv", index=False)
            orders_after.to_csv(partition_dir / "orders_after_matching.csv", index=False)

        print(f"  {partition_key}: {len(orders_before):,} before, {len(orders_after):,} after")
    
    return order_states_by_partition


def extract_last_execution_times(orders_by_partition, trades_by_partition, processed_dir):
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
    """Load all necessary data for a partition including reference data."""
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    date_dir = Path(processed_dir) / date
    
    partition_data = {}
    
    # ===== PARTITION-LEVEL DATA =====
    
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
    
    # ===== REFERENCE DATA =====
    
    # Load NBBO (partition-specific)
    nbbo_file = partition_dir / "nbbo.csv.gz"
    if nbbo_file.exists():
        partition_data['nbbo'] = pd.read_csv(nbbo_file)
    else:
        partition_data['nbbo'] = pd.DataFrame()
    
    # Load session data (date-level)
    session_file = date_dir / "session.csv.gz"
    if session_file.exists():
        partition_data['session'] = pd.read_csv(session_file)
    else:
        partition_data['session'] = pd.DataFrame()
    
    # Load reference data (date-level)
    reference_file = date_dir / "reference.csv.gz"
    if reference_file.exists():
        partition_data['reference'] = pd.read_csv(reference_file)
    else:
        partition_data['reference'] = pd.DataFrame()
    
    # Load participants data (date-level)
    participants_file = date_dir / "participants.csv.gz"
    if participants_file.exists():
        partition_data['participants'] = pd.read_csv(participants_file)
    else:
        partition_data['participants'] = pd.DataFrame()
    
    return partition_data


def classify_order_groups(orders_by_partition, processed_dir):
    """Classify sweep orders into groups based on real execution results."""
    print(f"\n[9/11] Classifying sweep order groups (type 2048 only)...")
    
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
        sweep_orders = orders_after[orders_after[col.common.exchangeordertype] == SWEEP_ORDER_TYPE].copy()
        
        if len(sweep_orders) == 0:
            print(f"  {partition_key}: No sweep orders found")
            continue
        
        # Classify based on real execution
        group1 = sweep_orders[sweep_orders[col.common.leavesquantity] == 0].copy()
        group2 = sweep_orders[
            (sweep_orders[col.common.leavesquantity] > 0) & 
            (sweep_orders[col.common.matched_quantity] > 0)
        ].copy()
        group3 = sweep_orders[
            (sweep_orders[col.common.leavesquantity] > 0) & 
            (sweep_orders[col.common.matched_quantity] == 0)
        ].copy()
        
        groups_by_partition[partition_key] = {
            'Group 1 (Fully Filled)': group1,
            'Group 2 (Partially Filled)': group2,
            'Group 3 (Unfilled)': group3
        }
        
        print(f"  {partition_key}: G1={len(group1):,}, G2={len(group2):,}, G3={len(group3):,} (sweep orders only)")

    return groups_by_partition


# ============================================================================
# STREAMING GENERATORS (PROCESSING_MODE = 'stream')
# ============================================================================

def stream_orders_for_partition(partition_data):
    """
    Generator: yield contra-pool order dicts in (effective_timestamp, sequence) order.

    Wraps _prepare_all_orders_for_matching so that the matching engine
    receives one dict at a time instead of a full DataFrame.  The DataFrame
    is still built internally for normalisation; the generator just avoids
    handing a large object to the caller.
    """
    # Import locally to avoid circular import (sweep_simulator imports data_processor)
    from pipeline.sweep_simulator import _prepare_all_orders_for_matching
    all_orders = _prepare_all_orders_for_matching(partition_data)
    for _, row in all_orders.iterrows():
        yield row.to_dict()


def stream_sweep_orders_for_partition(partition_data):
    """
    Generator: yield sweep order dicts in (effective_timestamp, sequence) order.

    Wraps _prepare_sweep_orders so the matching engine receives one sweep
    dict at a time.
    """
    from pipeline.sweep_simulator import _prepare_sweep_orders
    sweep_orders = _prepare_sweep_orders(partition_data)
    for _, row in sweep_orders.iterrows():
        yield row.to_dict()


def stream_partition_data(partition_data):
    """
    Return a dict containing lazy streaming iterators for both sweep orders and
    contra-pool orders drawn from partition_data.

    All other partition_data keys (nbbo, session_states, reference, etc.) are
    passed through unchanged so callers can build reference_loader etc. as normal.
    """
    return {
        **partition_data,
        'sweep_orders_iter': stream_sweep_orders_for_partition(partition_data),
        'all_orders_iter':   stream_orders_for_partition(partition_data),
    }


# ============================================================================
# FILE-LEVEL STREAMING (read CSV on-disk row-by-row, never full DataFrame)
# ============================================================================

# Sentinel and default constants (mirrored from sweep_simulator to avoid
# a circular import at module level).
_INT64_SENTINEL   = -9223372036854775808
_MIDTICK_NO       = 2
_CR_NEW_ORDER     = 6
_ORDERTYPE_LIMIT  = 1
_PRIORITY_LOSS_CR = {7, 8, 39}


def _safe_int(val, default=0):
    """Cast val to int with a fallback for None / NaN / empty string."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if f != f else int(f)  # f != f is the NaN test
    except (ValueError, TypeError):
        return default


def _eff_ts(ts, tc, obp, cr):
    """Compute effective_timestamp for a single row (mirrors _get_effective_timestamp)."""
    if obp > 0 or cr in _PRIORITY_LOSS_CR:
        return tc if tc else ts
    return ts


def _normalise_contra_row(row):
    """
    Normalise one raw CSV row dict into a matching-ready contra order dict.
    Mirrors the per-column casts in _prepare_all_orders_for_matching.
    """
    ts  = _safe_int(row.get('timestamp', 0))
    tc  = _safe_int(row.get('timechanged'), ts) or ts
    obp = _safe_int(row.get('orderbookposition', 0))
    cr  = _safe_int(row.get('changereason'), _CR_NEW_ORDER)

    dq_raw = row.get('display_quantity')
    dq = (None if dq_raw is None or (isinstance(dq_raw, float) and dq_raw != dq_raw)
          else _safe_int(dq_raw))

    return {
        'orderid':                   _safe_int(row.get('orderid', 0)),
        'timestamp':                 ts,
        'sequence':                  _safe_int(row.get('sequence', 0)),
        'side':                      _safe_int(row.get('side', 0)),
        'quantity':                  _safe_int(row.get('quantity', 0)),
        'orderbookid':               _safe_int(row.get('orderbookid', 0)),
        'bid':                       _safe_int(row.get('bid', 0)),
        'offer':                     _safe_int(row.get('offer', 0)),
        'national_bid':              _safe_int(row.get('national_bid'), _INT64_SENTINEL),
        'national_offer':            _safe_int(row.get('national_offer'), _INT64_SENTINEL),
        'minimumquantity':           _safe_int(row.get('minimumquantity', 0)),
        'singlefillminimumquantity': _safe_int(row.get('singlefillminimumquantity', 0)),
        'crossingkey':               _safe_int(row.get('crossingkey', 0)),
        'participantid':             _safe_int(row.get('participantid', 0)),
        'midtick':                   _safe_int(row.get('midtick'), _MIDTICK_NO),
        'timevalidity':              _safe_int(row.get('timevalidity'), 1536),
        'price':                     _safe_int(row.get('price', 0)),
        'changereason':              cr,
        'orderbookposition':         obp,
        'timechanged':               tc,
        'ordertype':                 _safe_int(row.get('ordertype'), _ORDERTYPE_LIMIT),
        'display_quantity':          dq,
        'exchangeordertype':         _safe_int(row.get('exchangeordertype', 0)),
        'effective_timestamp':       _eff_ts(ts, tc, obp, cr),
    }


def _normalise_sweep_row(row, le):
    """
    Normalise one raw orders_after row dict merged with a last_execution entry.
    Mirrors the per-column casts in _prepare_sweep_orders.
    """
    ts  = _safe_int(row.get('timestamp', 0))
    tc  = _safe_int(row.get('timechanged'), ts) or ts
    obp = _safe_int(row.get('orderbookposition', 0))
    cr  = _safe_int(row.get('changereason'), _CR_NEW_ORDER)

    dq_raw = row.get('display_quantity')
    dq = (None if dq_raw is None or (isinstance(dq_raw, float) and dq_raw != dq_raw)
          else _safe_int(dq_raw))

    eff = _eff_ts(ts, tc, obp, cr)

    return {
        'orderid':                   _safe_int(row.get('orderid', 0)),
        'timestamp':                 ts,
        'sequence':                  _safe_int(row.get('sequence', 0)),
        'side':                      _safe_int(row.get('side', 0)),
        'leavesquantity':            _safe_int(row.get('leavesquantity', 0)),
        'matched_quantity':          _safe_int(row.get('matched_quantity',
                                               row.get('totalmatchedquantity', 0))),
        'price':                     _safe_int(row.get('price', 0)),
        'first_execution_time':      _safe_int(le.get('first_execution_time'), ts),
        'last_execution_time':       _safe_int(le.get('last_execution_time'), ts),
        'orderbookid':               _safe_int(row.get('orderbookid', 0)),
        'minimumquantity':           _safe_int(row.get('minimumquantity', 0)),
        'singlefillminimumquantity': _safe_int(row.get('singlefillminimumquantity', 0)),
        'crossingkey':               _safe_int(row.get('crossingkey', 0)),
        'participantid':             _safe_int(row.get('participantid', 0)),
        'midtick':                   _safe_int(row.get('midtick'), _MIDTICK_NO),
        'changereason':              cr,
        'orderbookposition':         obp,
        'timechanged':               tc,
        'display_quantity':          dq,
        'preferenceonly':            _safe_int(row.get('preferenceonly', 0)),
        'effective_timestamp':       eff,
        'lost_priority':             (obp > 0) or (cr in _PRIORITY_LOSS_CR),
    }


def stream_orders_from_file(orders_path, eligible_order_types=None, chunk_size=None):
    """
    Generator: stream contra-pool orders from a CSV file, yielding normalised
    order dicts in (effective_timestamp, sequence) order.

    Reads chunk_size rows at a time — only that many rows are in memory at once.
    Each chunk is sorted internally; heapq.merge produces a globally-sorted
    stream (correct when the file is roughly time-ordered, as order logs are).
    """
    import heapq
    if eligible_order_types is None:
        from pipeline.sweep_simulator import ELIGIBLE_MATCHING_ORDER_TYPES
        eligible_order_types = ELIGIBLE_MATCHING_ORDER_TYPES
    if chunk_size is None:
        chunk_size = _cfg.STREAM_CHUNK_SIZE

    orders_path = Path(orders_path)
    if not orders_path.exists():
        return

    et_col = 'exchangeordertype'

    def _chunk_to_sorted_iter(chunk):
        if et_col in chunk.columns:
            chunk = chunk[chunk[et_col].isin(eligible_order_types)]
        if len(chunk) == 0:
            return iter([])
        rows = [_normalise_contra_row(r) for r in chunk.to_dict('records')]
        rows.sort(key=lambda r: (r['effective_timestamp'], r['sequence']))
        return iter(rows)

    chunk_iters = [_chunk_to_sorted_iter(c)
                   for c in pd.read_csv(orders_path, chunksize=chunk_size)]
    yield from heapq.merge(*chunk_iters,
                           key=lambda r: (r['effective_timestamp'], r['sequence']))


def stream_sweep_orders_from_file(orders_after_path, last_execution_df, chunk_size=None):
    """
    Generator: stream sweep orders from a CSV file, yielding normalised sweep
    order dicts in (effective_timestamp, sequence) order.

    last_execution_df stays in memory (small — one row per sweep order).
    Only rows whose orderid appears in last_execution_df are yielded.
    """
    import heapq
    if chunk_size is None:
        chunk_size = _cfg.STREAM_CHUNK_SIZE

    orders_after_path = Path(orders_after_path)
    if not orders_after_path.exists():
        return
    if last_execution_df is None or len(last_execution_df) == 0:
        return

    # Build lookup: orderid → last_execution row dict (small, stays in memory)
    le_lookup = {int(r['orderid']): r
                 for r in last_execution_df.to_dict('records')}

    et_col = 'exchangeordertype'

    def _chunk_to_sorted_iter(chunk):
        if et_col in chunk.columns:
            chunk = chunk[chunk[et_col] == SWEEP_ORDER_TYPE]
        if len(chunk) == 0:
            return iter([])
        rows = []
        for r in chunk.to_dict('records'):
            le = le_lookup.get(_safe_int(r.get('orderid', 0)))
            if le is None:
                continue
            rows.append(_normalise_sweep_row(r, le))
        rows.sort(key=lambda r: (r['effective_timestamp'], r['sequence']))
        return iter(rows)

    chunk_iters = [_chunk_to_sorted_iter(c)
                   for c in pd.read_csv(orders_after_path, chunksize=chunk_size)]
    yield from heapq.merge(*chunk_iters,
                           key=lambda r: (r['effective_timestamp'], r['sequence']))


def stream_partition_data_from_file(partition_key, processed_dir, last_execution_df=None):
    """
    Return streaming iterators that read directly from processed partition CSV files.

    Unlike stream_partition_data (which wraps DataFrames already in memory),
    this function never loads a full DataFrame — only chunk_size rows at a time.
    """
    date, sec = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / sec

    return {
        'sweep_orders_iter': stream_sweep_orders_from_file(
            partition_dir / 'orders_after_matching.csv', last_execution_df),
        'all_orders_iter': stream_orders_from_file(
            partition_dir / 'orders_before_matching.csv'),
    }
