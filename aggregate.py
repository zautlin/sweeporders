"""sweeporders aggregate.py - Stages 3+4 (metrics + real-vs-sim comparison).

Flat consolidation; reads data/processed/ and writes data/outputs/.

Run: python aggregate.py --dates 20240505 --tickers cba
     python aggregate.py --dates 20240505,20240905 --auto-tickers --workers 4
"""

# ============================================================================
# Section 1 - utils/normalization.py
# ============================================================================
"""Column normalization — re-exported from config for back-compat.

The canonical map and helper now live in config.COLUMN_NORMALIZATION_MAP /
config.normalize_column_names. Add new server-side aliases there.
"""

import pandas as pd

from config import COLUMN_NORMALIZATION_MAP, normalize_column_names

# ============================================================================
# Section 2 - utils/data_utils.py
# ============================================================================
"""Data normalization and transformation utilities."""

import pandas as pd


# NOTE: Column normalization now happens in Stage 1 (data_processor.py)
# using config.normalize_to_standard_names(). The normalize_column_names()
# function has been removed as it's no longer needed.


def add_date_column(df, timestamp_col):
    """Add date column from timestamp (convert UTC to AEST)."""
    df = df.copy()
    df['date'] = (pd.to_datetime(df[timestamp_col], unit='ns')
                    .dt.tz_localize('UTC')
                    .dt.tz_convert('Australia/Sydney')
                    .dt.strftime('%Y-%m-%d'))
    return df


def filter_sweep_orders(df, order_type_col='exchangeordertype', sweep_type=2048):
    """Filter for sweep orders (type 2048 by default)."""
    return df[df[order_type_col] == sweep_type].copy()


def get_sweep_orderids(orders_df, order_type_col='exchangeordertype', sweep_type=2048):
    """Extract unique sweep order IDs."""
    sweep_orders = filter_sweep_orders(orders_df, order_type_col, sweep_type)
    return sweep_orders['orderid'].unique() if len(sweep_orders) > 0 else []


# ============================================================================
# Section 3 - utils/io_backend.py
# ============================================================================
"""DuckDB + Polars I/O backend helpers.

Each ProcessPoolExecutor worker gets its own thread-local DuckDB connection
(in-memory) so there is no contention between parallel partition jobs.
"""

import threading
import duckdb
import polars as pl

_local = threading.local()


def get_conn() -> duckdb.DuckDBPyConnection:
    """Return a per-thread in-memory DuckDB connection."""
    if not hasattr(_local, 'conn'):
        _local.conn = duckdb.connect()
    return _local.conn


def duck_to_polars(rel) -> pl.DataFrame:
    """Zero-copy: DuckDB relation → Polars via Arrow."""
    return pl.from_arrow(rel.arrow())


# ============================================================================
# Section 4 - utils/file_utils.py
# ============================================================================
"""File I/O utilities for pipeline operations."""

import pandas as pd
import polars as pl
from pathlib import Path

import sys
import os
# (lean port) legacy sys.path hack removed; flat layout finds config.py natively.
# (consolidated) import config.config as config


def get_partition_dir(base_dir, partition_key):
    """Build partition directory path from partition key (date/security)."""
    date, security = partition_key.split('/')
    return Path(base_dir) / date / security


def _glob_raw_inputs(folder):
    """List raw input files under folder, preferring .parquet over .csv per stem.

    Run convert_raw.py once to materialise parquet copies; this helper then routes
    everything to the parquet versions automatically.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return []
    seen = set()
    out = []
    for f in sorted(folder.glob('*.parquet')):
        out.append(f)
        seen.add(f.stem)
    for f in sorted(folder.glob('*.csv')):
        if f.stem not in seen:
            out.append(f)
    return out


def _filters_to_sql_where(filters):
    """Translate a pyarrow-style filters list into a SQL WHERE clause.

    filters: list[(col_name, op, values)] — supported ops: 'in', '=='.
    """
    if not filters:
        return ''
    parts = []
    for col_name, op, values in filters:
        if op == 'in':
            vals = ', '.join(repr(v) if isinstance(v, str) else str(int(v)) for v in values)
            parts.append(f"{col_name} IN ({vals})")
        elif op == '==':
            v = repr(values) if isinstance(values, str) else int(values)
            parts.append(f"{col_name} = {v}")
        else:
            raise NotImplementedError(f"Filter op {op!r} not supported")
    return ' WHERE ' + ' AND '.join(parts)


def safe_read_csv(filepath, required=True, compression='infer',
                  filters=None, return_total=False, **kwargs):
    """Read CSV or Parquet via DuckDB. Both formats use DuckDB's parallel parser
    so CSV ingest is fast even on multi-GB files. Name kept for backward
    compatibility with legacy call sites.

    Optional kwargs:
      filters: list[(col, op, values)] predicate pushdown ('in'|'==').
      return_total: when True, also return total_rows_in_file as second tuple element.
    """
    filepath = Path(filepath)

    if not filepath.exists():
        if required:
            raise FileNotFoundError(f"Required file not found: {filepath}")
        return (None, 0) if return_total else None

    if filepath.suffix == '.parquet':
        source = f"'{filepath}'"
    elif filepath.suffix == '.csv':
        source = f"read_csv_auto('{filepath}')"
    else:
        raise IOError(f"Unsupported file extension: {filepath.suffix} ({filepath})")

    try:
        kwargs.pop('compression', None)
        conn = duckdb.connect()
        where = _filters_to_sql_where(filters)
        df = conn.execute(f"SELECT * FROM {source}{where}").df()
        if return_total:
            total = conn.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]
            return df, total
        return df
    except Exception as e:
        raise IOError(f"Error reading {filepath}: {e}")


def safe_write_csv(df, filepath, compression=None, create_dirs=True, **kwargs):
    """Write tabular data with format auto-detection (Parquet or CSV).

    Dispatches on file extension: .parquet → zstd parquet, else CSV.
    Accepts both pandas and Polars DataFrames. Pandas-DF parquet writes
    go through DuckDB (no pyarrow runtime dep).
    """
    filepath = Path(filepath)

    if create_dirs:
        filepath.parent.mkdir(parents=True, exist_ok=True)

    try:
        if filepath.suffix == '.parquet':
            if isinstance(df, pl.DataFrame):
                df.write_parquet(filepath, compression='zstd')
            else:
                conn = duckdb.connect()
                conn.register('_df_to_write', df)
                conn.execute(
                    f"COPY _df_to_write TO '{filepath}' "
                    f"(FORMAT PARQUET, COMPRESSION ZSTD)"
                )
            return

        if isinstance(df, pl.DataFrame):
            df.write_csv(filepath)
        else:
            df.to_csv(filepath, compression=compression, index=False, **kwargs)
    except Exception as e:
        raise IOError(f"Error writing {filepath}: {e}")


def load_orders_before(partition_dir):
    """Load orders_before_matching parquet from partition directory."""
    filepath = Path(partition_dir) / "orders_before_matching.parquet"
    return safe_read_csv(filepath, required=False)


def load_trades_matched(partition_dir):
    """Load cp_trades_matched parquet from partition directory."""
    filepath = Path(partition_dir) / "cp_trades_matched.parquet"
    return safe_read_csv(filepath, required=False)


def save_trade_metrics(real_metrics_df, sim_metrics_df, output_dir, partition_key):
    """Save real and simulated trade metrics calculated in Stage 2 for Stage 3 reuse."""
    partition_output_dir = Path(output_dir) / partition_key
    
    if real_metrics_df is not None and len(real_metrics_df) > 0:
        safe_write_csv(
            real_metrics_df,
            partition_output_dir / 'real_trade_metrics.parquet'
        )
    
    if sim_metrics_df is not None and len(sim_metrics_df) > 0:
        safe_write_csv(
            sim_metrics_df,
            partition_output_dir / 'simulated_trade_metrics.parquet'
        )


def load_simulation_trades(partition_dir):
    """Load cp_trades_simulation parquet from partition processed directory."""
    filepath = Path(partition_dir) / "cp_trades_simulation.parquet"
    return safe_read_csv(filepath, required=False)


def load_simulation_order_summary(partition_dir):
    """Load simulation_order_summary parquet from partition output directory."""
    filepath = Path(partition_dir) / "simulation_order_summary.parquet"
    return safe_read_csv(filepath, required=False)

# ============================================================================
# Section 5 - pipeline/reference_data.py
# ============================================================================
"""
Reference Data Module

Handles loading and using reference data for simulation:
- Tick size tables from order book reference data
- Participant information
- Session state information
- Price limits
"""

import pandas as pd
from pathlib import Path
from config import col


_reference_loader = None


# ============================================================================
# Section 6 - pipeline/data_processor.py
# ============================================================================
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
from config import SWEEP_ORDER_TYPE, PROJECT_ROOT
import config as _cfg
from config import col
# (consolidated) from utils.normalization import normalize_column_names


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


def _read_csv_files_concat(file_list):
    """Read and concatenate multiple CSV files into single DataFrame."""
    if not file_list:
        return None

    dfs = []
    for file in file_list:
        f = Path(file)
        df = safe_read_csv(f)
        dfs.append(df)

    if not dfs:
        return None

    return pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]


def _partition_by_date_and_save(df, unique_dates, processed_dir, filename, date_col):
    """Partition DataFrame by date and save each partition. Format dispatched on filename extension."""
    results = {}

    for date in unique_dates:
        date_data = df[df[date_col] == date].copy()

        if len(date_data) > 0:
            results[date] = date_data
            if _should_save():
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                output_file = date_dir / filename
                safe_write_csv(date_data, output_file, create_dirs=False, compression='gzip')
                size_kb = output_file.stat().st_size / 1024
                print(f"    {date}/{filename}: {len(date_data):,} records ({size_kb:.1f} KB)")
            else:
                print(f"    {date}/{filename}: {len(date_data):,} records (in-memory)")
        else:
            print(f"    {date}/{filename}: NO DATA (missing in raw files)")

    return results


def _partition_by_date_security_and_save(df, orders_by_partition, processed_dir, filename, date_col, security_col):
    """Partition DataFrame by date/security and save each partition. Format dispatched on filename extension."""
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
                safe_write_csv(partition_data_normalized, output_file, create_dirs=False, compression='gzip')
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

    # Normalize raw → canonical first so PascalCase server inputs work the same
    # as lowercase local inputs.
    df = normalize_column_names(df, data_type)
    df = add_date_column(df, col.common.timestamp)

    return _partition_by_date_and_save(df, unique_dates, processed_dir, filename, col.common.date)


def _process_participants_with_fallback(file_list, timestamp_col, unique_dates, processed_dir):
    """Process participants data with latest-date fallback for missing dates."""
    if not file_list:
        return {}

    df = _read_csv_files_concat(file_list)
    if df is None or len(df) == 0:
        return {}

    df = normalize_column_names(df, 'participants')
    df = add_date_column(df, col.common.timestamp)
    
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
                output_file = date_dir / "participants.parquet"
                safe_write_csv(date_data, output_file, create_dirs=False)
                size_kb = output_file.stat().st_size / 1024
                print(f"    {date}/participants.parquet: {len(date_data):,} records ({size_kb:.1f} KB)")
            else:
                print(f"    {date}/participants.parquet: {len(date_data):,} records (in-memory)")
        else:
            latest_date = max(all_participant_dates)
            fallback_data = df[df[col.common.date] == latest_date].copy()
            results[date] = fallback_data
            if _should_save():
                date_dir = Path(processed_dir) / date
                date_dir.mkdir(parents=True, exist_ok=True)
                output_file = date_dir / "participants.parquet"
                safe_write_csv(fallback_data, output_file, create_dirs=False)
                size_kb = output_file.stat().st_size / 1024
                print(f"    {date}/participants.parquet: {len(fallback_data):,} records ({size_kb:.1f} KB) [FALLBACK from {latest_date}]")
            else:
                print(f"    {date}/participants.parquet: {len(fallback_data):,} records (in-memory) [FALLBACK from {latest_date}]")

    return results


def _process_nbbo_data(file_list, timestamp_col, orders_by_partition, processed_dir, security_col):
    """Process NBBO data partitioned by date and security."""
    if not file_list:
        return {}

    df = _read_csv_files_concat(file_list)
    if df is None or len(df) == 0:
        return {}

    df = normalize_column_names(df, 'nbbo')
    df = add_date_column(df, col.common.timestamp)
    return _partition_by_date_security_and_save(
        df, orders_by_partition, processed_dir, "nbbo.parquet",
        col.common.date, col.common.orderbookid,
    )


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
    safe_write_csv(execution_times_df, partition_dir / "last_execution_time.parquet", create_dirs=False)


def extract_orders(input_file, processed_dir, order_types, chunk_size,
                   orderbookids_filter=None, dates_filter=None):
    """Extract Centre Point orders from one-or-more raw files; partition by (date, orderbookid).

    `input_file` may be a single path (str/Path) or a list — each file is read,
    filtered by `order_types` (and optionally `orderbookids_filter` pushed down at the
    parquet level), then concatenated. The natural groupby downstream produces
    data/processed/{date}/{orderbookid}/ regardless of how the inputs were sliced.
    `dates_filter` is applied post-read (after add_date_column) and accepts
    'YYYY-MM-DD' strings.
    """
    if isinstance(input_file, (str, Path)):
        input_files = [Path(input_file)]
    else:
        input_files = [Path(f) for f in input_file]

    print(f"\n[1/11] Extracting Centre Point orders from {len(input_files)} file(s)...")
    for f in input_files:
        print(f"        {f}")

    frames = []
    total_rows = 0
    for fp in input_files:
        filters = [(col.orders.order_type, 'in', list(order_types))]
        if orderbookids_filter:
            filters.append((col.orders.security_code, 'in', list(orderbookids_filter)))
        chunk, n_total = safe_read_csv(fp, filters=filters, return_total=True)
        total_rows += n_total
        if chunk is not None and len(chunk) > 0:
            chunk = normalize_column_names(chunk, 'orders')
            frames.append(chunk)

    if not frames:
        print("  No Centre Point orders found!")
        return {}

    orders = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    orders = add_date_column(orders, col.orders.timestamp)

    if dates_filter:
        orders = orders[orders[col.common.date].isin(dates_filter)]
        if len(orders) == 0:
            print(f"  No Centre Point orders match dates_filter={dates_filter}")
            return {}

    print(f"  Found {len(orders):,} Centre Point orders from {total_rows:,} total rows")

    # Partition by date/security
    partitions = {}
    for (date, security_code_val), group_df in orders.groupby(['date', col.common.orderbookid]):
        partition_key = f"{date}/{security_code_val}"
        
        # Normalize column names to standard before saving
        group_df_normalized = normalize_column_names(group_df, 'orders')
        
        # Store normalized version for downstream use
        partitions[partition_key] = group_df_normalized

        if _should_save():
            partition_dir = Path(processed_dir) / date / str(security_code_val)
            partition_dir.mkdir(parents=True, exist_ok=True)
            partition_file = partition_dir / "cp_orders_filtered.parquet"
            safe_write_csv(group_df_normalized, partition_file, create_dirs=False)
            size_mb = partition_file.stat().st_size / (1024 * 1024)
            print(f"  {partition_key}: {len(group_df):,} orders ({size_mb:.2f} MB)")
        else:
            print(f"  {partition_key}: {len(group_df):,} orders (in-memory)")
    
    return partitions


def extract_trades(input_file, orders_by_partition, processed_dir, chunk_size):
    """Extract trades matching order_ids from partitions; supports list-of-files."""
    if isinstance(input_file, (str, Path)):
        input_files = [Path(input_file)]
    else:
        input_files = [Path(f) for f in input_file]

    print(f"\n[2/11] Extracting matching trades from {len(input_files)} file(s)...")

    order_id_col_orders = 'orderid'

    # Collect all order IDs across all order partitions
    all_order_ids = set()
    partition_order_ids = {}

    for partition_key, orders_df in orders_by_partition.items():
        order_ids = set(orders_df[order_id_col_orders].unique())
        partition_order_ids[partition_key] = order_ids
        all_order_ids.update(order_ids)
    
    print(f"  Looking for {len(all_order_ids):,} order IDs across {len(orders_by_partition)} partitions")

    frames = []
    total_rows = 0
    for fp in input_files:
        trades, n_total = safe_read_csv(fp, return_total=True)
        total_rows += n_total
        if trades is not None and len(trades) > 0:
            trades = normalize_column_names(trades, 'trades')
            matched = trades[trades[col.trades.order_id].isin(all_order_ids)].copy()
            if len(matched) > 0:
                frames.append(matched)

    if not frames:
        print("  No matching trades found!")
        return {}

    all_trades = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    all_trades = add_date_column(all_trades, col.trades.trade_time)
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
                partition_file = partition_dir / "cp_trades_matched.parquet"
                safe_write_csv(partition_trades_normalized, partition_file, create_dirs=False)
                size_mb = partition_file.stat().st_size / (1024 * 1024)
                print(f"  {partition_key}: {len(partition_trades):,} trades, {unique_orders:,} orders ({size_mb:.2f} MB)")
            else:
                print(f"  {partition_key}: {len(partition_trades):,} trades, {unique_orders:,} orders (in-memory)")
    
    return trades_by_partition


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
    
    session_files = _glob_raw_inputs(raw_folders['session'])
    if session_files:
        print(f"\n  Processing Session data from {len(session_files)} file(s)...")
        results['session'] = _process_single_reference_type(
            session_files, col.session.timestamp, unique_dates, processed_dir, 'session.parquet', 'session'
        )
    else:
        print(f"\n  Processing Session data from 0 file(s)...")
    
    reference_files = _glob_raw_inputs(raw_folders['reference'])
    if reference_files:
        print(f"\n  Processing Reference data from {len(reference_files)} file(s)...")
        results['reference'] = _process_single_reference_type(
            reference_files, col.reference.timestamp, unique_dates, processed_dir, 'reference.parquet', 'reference'
        )
    else:
        print(f"\n  Processing Reference data from 0 file(s)...")
    
    participants_files = _glob_raw_inputs(raw_folders['participants'])
    if participants_files:
        print(f"\n  Processing Participants data from {len(participants_files)} file(s)...")
        results['participants'] = _process_participants_with_fallback(
            participants_files, col.participants.timestamp, unique_dates, processed_dir
        )
    else:
        print(f"\n  Processing Participants data from 0 file(s)...")
    
    nbbo_files = _glob_raw_inputs(raw_folders['nbbo'])
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
            safe_write_csv(orders_before, partition_dir / "orders_before_matching.parquet", create_dirs=False)
            safe_write_csv(orders_after, partition_dir / "orders_after_matching.parquet", create_dirs=False)

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


# ============================================================================
# Section 7 - pipeline/trade_metrics_calculator.py (Stage 3)
# ============================================================================
"""
Comprehensive Trade Metrics Calculator for Sweep Orders

This module calculates 36 comprehensive metrics for sweep orders covering:
- Fill metrics (quantity, ratios, fill counts)
- Price metrics (VWAP, arrival prices, price improvement)
- Execution cost metrics (arrival-based, volume-weighted)
- Timing metrics (durations, time to first fill)
- Market context metrics (market drift, spread volatility)

Works with both real and simulated trades using unified logic.
All functions are pure functions - no class wrapper needed.
"""

import pandas as pd
import numpy as np
from typing import Optional, Dict, List

try:
    import config as _cfg
except ModuleNotFoundError:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    import config as _cfg


def calculate_trade_metrics(
    trades_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    nbbo_df: Optional[pd.DataFrame] = None,
    filter_orderids: Optional[List[int]] = None,
    role_filter: Optional[str] = None,
    prefix: str = '',
    is_simulated: bool = False
) -> Dict[str, pd.DataFrame]:
    """Calculate 36 comprehensive metrics for sweep orders across 5 groups (fill, price, cost, timing, market)."""
    # 1. Normalize schemas
    # Note: Only needed for simulated trades (securitycode → orderbookid)
    # Real trades are already normalized by Stage 1
    trades = _normalize_trade_schema(trades_df.copy())
    orders = orders_df.copy()  # No normalization needed - Stage 1 guarantees normalized schema
    
    # 2. Filter trades
    # Note: Necessary because real trades may contain non-sweep orders
    # and simulated trades have both aggressor + passive rows per match
    trades = _filter_trades(trades, filter_orderids, role_filter)
    
    if len(trades) == 0:
        return {
            'per_trade_metrics': pd.DataFrame(),
            'per_order_metrics': pd.DataFrame()
        }
    
    # 3. Enrich trades with order context
    trades_enriched = _enrich_trades_with_context(trades, orders, nbbo_df)
    
    # 4. Calculate per-trade metrics
    per_trade = _calculate_per_trade_metrics(trades_enriched)
    
    # 5. Aggregate to order level
    per_order = _aggregate_to_order_level(per_trade, orders, is_simulated)
    
    # 6. Apply prefix
    per_order = _apply_prefix(per_order, prefix)
    
    return {
        'per_trade_metrics': per_trade,
        'per_order_metrics': per_order
    }


def _normalize_trade_schema(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize simulated trade schema to match real trades (securitycode → orderbookid)."""
    if 'securitycode' in trades_df.columns and 'orderbookid' not in trades_df.columns:
        trades_df['orderbookid'] = trades_df['securitycode']
    
    return trades_df


def _filter_trades(
    trades_df: pd.DataFrame,
    filter_orderids: Optional[List[int]],
    role_filter: Optional[str]
) -> pd.DataFrame:
    """Filter trades by orderids and role (aggressor if role_filter='aggressor')."""
    filtered = trades_df
    
    # Filter by orderids
    if filter_orderids is not None:
        filtered = filtered[filtered['orderid'].isin(filter_orderids)]
    
    # Filter by role
    if role_filter == 'aggressor' and 'passiveaggressive' in filtered.columns:
        filtered = filtered[filtered['passiveaggressive'] == 1]
    
    return filtered


def _enrich_trades_with_context(
    trades_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    nbbo_df: Optional[pd.DataFrame]
) -> pd.DataFrame:
    """Enrich trades with order context (arrival time, side, quantity, price) and arrival NBBO."""
    # Handle indexed DataFrame (reset if orderid is index)
    if orders_df.index.name == 'orderid':
        orders_df = orders_df.reset_index()
    
    # Prepare order context
    order_context = orders_df[['orderid', 'timestamp', 'side', 'quantity', 'price']].copy()
    order_context = order_context.rename(columns={
        'timestamp': 'order_timestamp',
        'side': 'order_side',
        'quantity': 'order_quantity',
        'price': 'order_price'
    })
    
    # Get arrival NBBO from orders
    # Both real (orders_before) and simulated (orders_after) have national_bid/national_offer
    if 'national_bid' in orders_df.columns and 'national_offer' in orders_df.columns:
        order_context['arrival_bid'] = orders_df['national_bid']
        order_context['arrival_offer'] = orders_df['national_offer']
    else:
        # Fallback: No arrival NBBO available (shouldn't happen with current pipeline)
        order_context['arrival_bid'] = np.nan
        order_context['arrival_offer'] = np.nan
    
    # Calculate arrival midpoint and spread (use existing if available)
    if 'arrival_midpoint' in orders_df.columns:
        order_context['arrival_midpoint'] = orders_df['arrival_midpoint']
    else:
        order_context['arrival_midpoint'] = (
            order_context['arrival_bid'] + order_context['arrival_offer']
        ) / 2.0
    
    if 'arrival_spread' in orders_df.columns:
        order_context['arrival_spread'] = orders_df['arrival_spread']
    else:
        order_context['arrival_spread'] = (
            order_context['arrival_offer'] - order_context['arrival_bid']
        )
    
    # Merge with trades
    enriched = trades_df.merge(order_context, on='orderid', how='left')
    
    # Calculate trade midpoint from NBBO snapshots (if available)
    if 'nationalbidpricesnapshot' in enriched.columns and 'nationalofferpricesnapshot' in enriched.columns:
        enriched['trade_midpoint'] = (
            enriched['nationalbidpricesnapshot'] + enriched['nationalofferpricesnapshot']
        ) / 2.0
        enriched['trade_spread'] = (
            enriched['nationalofferpricesnapshot'] - enriched['nationalbidpricesnapshot']
        )
    else:
        enriched['trade_midpoint'] = np.nan
        enriched['trade_spread'] = np.nan
    
    return enriched


def _calculate_per_trade_metrics(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Calculate per-trade enrichment metrics (cumulative fill, first/last fill flags, price vs limit)."""
    enriched = trades_df.sort_values(['orderid', 'tradetime']).copy()

    # Calculate cumulative fill
    if _cfg.USE_POLARS_TRANSFORMS:
        import polars as pl
        enriched_pl = pl.from_pandas(enriched)
        enriched_pl = enriched_pl.with_columns(
            pl.col('quantity').cum_sum().over('orderid').alias('cumulative_fill')
        )
        enriched = enriched_pl.to_pandas()
    else:
        enriched['cumulative_fill'] = enriched.groupby('orderid')['quantity'].cumsum()
    
    # Identify first and last fills
    enriched['is_first_fill'] = ~enriched.duplicated(subset=['orderid'], keep='first')
    enriched['is_last_fill'] = ~enriched.duplicated(subset=['orderid'], keep='last')
    
    # Price vs order price
    enriched['price_vs_order_price'] = enriched['tradeprice'] - enriched['order_price']
    
    return enriched


def _aggregate_to_order_level(
    trades_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    is_simulated: bool = False
) -> pd.DataFrame:
    """Aggregate per-trade metrics to order level with all 36 metrics across 5 groups."""
    if _cfg.USE_POLARS_TRANSFORMS:
        return _aggregate_to_order_level_polars(trades_df, orders_df, is_simulated)

    metrics_list = []

    for orderid, order_trades in trades_df.groupby('orderid'):
        # Get order context (take first row since all trades have same order context)
        order_context = order_trades.iloc[0]

        # Calculate all metric groups
        fill_metrics = _calculate_fill_metrics(order_trades, order_context)
        price_metrics = _calculate_price_metrics(order_trades, order_context)
        exec_cost_metrics = _calculate_execution_cost_metrics(order_trades, order_context, is_simulated)
        timing_metrics = _calculate_timing_metrics(order_trades, order_context)
        market_context_metrics = _calculate_market_context_metrics(order_trades, order_context)

        # Combine all metrics
        order_metrics = {
            'orderid': orderid,
            'orderbookid': order_context.get('orderbookid', np.nan),
            **fill_metrics,
            **price_metrics,
            **exec_cost_metrics,
            **timing_metrics,
            **market_context_metrics
        }

        metrics_list.append(order_metrics)

    return pd.DataFrame(metrics_list)


def _aggregate_to_order_level_polars(
    trades_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    is_simulated: bool = False
) -> pd.DataFrame:
    """Polars-vectorised replacement for _aggregate_to_order_level (called when USE_POLARS_TRANSFORMS=True)."""
    import polars as pl

    df = pl.from_pandas(trades_df)  # already sorted by [orderid, tradetime]

    # ── Group aggregation ──────────────────────────────────────────────────────
    agg = df.group_by('orderid', maintain_order=True).agg([
        # Group A: Fill
        pl.col('quantity').sum().alias('qty_filled'),
        pl.col('order_quantity').first(),
        pl.len().alias('num_fills'),

        # Group B: Price
        (pl.col('tradeprice') * pl.col('quantity')).sum()
          .truediv(pl.col('quantity').sum()).alias('vwap'),
        pl.col('arrival_midpoint').first(),
        pl.col('arrival_bid').first(),
        pl.col('arrival_offer').first(),
        pl.col('order_price').first().cast(pl.Float64).alias('limit_price'),
        pl.col('order_side').first(),
        pl.col('order_timestamp').first(),

        # Group C: Exec cost helpers
        (
            (pl.col('trade_midpoint') - pl.col('arrival_midpoint').first())
            / pl.col('arrival_midpoint').first() * 10000 * pl.col('quantity')
        ).sum().truediv(pl.col('quantity').sum()).alias('_vw_cost_sim'),
        (
            (pl.col('tradeprice') - pl.col('trade_midpoint'))
            / pl.col('trade_midpoint') * 10000 * pl.col('quantity')
        ).sum().truediv(pl.col('quantity').sum()).alias('_vw_cost_real'),
        (pl.col('tradeprice') * pl.col('quantity')).sum().alias('total_execution_value'),

        # Group D: Timing
        pl.col('tradetime').min().alias('first_fill_time'),
        pl.col('tradetime').max().alias('last_fill_time'),
        (pl.col('quantity') * (pl.col('tradetime') - pl.col('order_timestamp').first()))
          .sum().truediv(pl.col('quantity').sum()).alias('_vw_exec_time_ns'),

        # Group E: Market context
        pl.col('trade_midpoint').first().alias('first_fill_midpoint'),
        pl.col('trade_midpoint').last().alias('last_fill_midpoint'),
        (pl.col('trade_spread') / pl.col('trade_midpoint') * 10000).mean().alias('avg_execution_spread_bps'),
        (pl.col('trade_spread') / pl.col('trade_midpoint') * 10000).std().alias('spread_volatility_bps'),
        pl.col('tradeprice').std().alias('_price_std'),

        pl.col('orderbookid').first(),
    ])

    # ── Post-agg derived columns ───────────────────────────────────────────────
    agg = agg.with_columns([
        # Fill derived
        (pl.col('qty_filled') / pl.col('order_quantity')).alias('fill_ratio'),
        (pl.col('qty_filled') / pl.col('order_quantity') * 100).alias('fill_rate_pct'),
        (pl.col('qty_filled') / pl.col('num_fills')).alias('avg_fill_size'),
        pl.when(pl.col('qty_filled') == 0).then(pl.lit('Unfilled'))
          .when(pl.col('qty_filled') >= pl.col('order_quantity')).then(pl.lit('Fully Filled'))
          .otherwise(pl.lit('Partially Filled')).alias('fill_status'),

        # Side multiplier
        pl.when(pl.col('order_side') == 1).then(pl.lit(1.0))
          .otherwise(pl.lit(-1.0)).alias('_side_mult'),

        # Arrival spread
        (pl.col('arrival_offer') - pl.col('arrival_bid')).alias('arrival_spread'),

        # Timing (seconds)
        ((pl.col('first_fill_time') - pl.col('order_timestamp')) / 1e9)
          .clip(lower_bound=0.0).alias('time_to_first_fill_sec'),
        ((pl.col('last_fill_time') - pl.col('first_fill_time')) / 1e9)
          .clip(lower_bound=0.0).alias('execution_duration_sec'),
        ((pl.col('last_fill_time') - pl.col('order_timestamp')) / 1e9)
          .clip(lower_bound=0.0).alias('total_duration_sec'),
        (pl.col('_vw_exec_time_ns') / 1e9).clip(lower_bound=0.0).alias('vw_exec_time_sec'),
    ])
    agg = agg.with_columns([
        # Arrival spread bps
        pl.when(pl.col('arrival_midpoint') > 0)
          .then(pl.col('arrival_spread') / pl.col('arrival_midpoint') * 10000)
          .otherwise(pl.lit(None)).alias('arrival_spread_bps'),

        # Exec cost arrival
        pl.when(pl.col('arrival_midpoint') > 0)
          .then(pl.col('_side_mult') * (pl.col('vwap') - pl.col('arrival_midpoint'))
                / pl.col('arrival_midpoint') * 10000)
          .otherwise(pl.lit(None)).alias('exec_cost_arrival_bps'),

        # Exec cost volume-weighted
        pl.col('_side_mult') * (
            pl.col('_vw_cost_sim') if is_simulated else pl.col('_vw_cost_real')
        ).alias('exec_cost_vw_bps'),

        # Price improvement
        pl.when(pl.col('order_side') == 1)
          .then(pl.col('limit_price') - pl.col('vwap'))
          .otherwise(pl.col('vwap') - pl.col('limit_price')).alias('price_improvement'),

        # Market drift
        pl.when(pl.col('first_fill_midpoint') > 0)
          .then((pl.col('last_fill_midpoint') - pl.col('first_fill_midpoint'))
                / pl.col('first_fill_midpoint') * 10000)
          .otherwise(pl.lit(None)).alias('market_drift_bps'),

        # Price volatility
        pl.when(pl.col('vwap') > 0)
          .then(pl.col('_price_std') / pl.col('vwap') * 10000)
          .otherwise(pl.lit(None)).alias('price_volatility_bps'),

        # avg_time_between_fills
        pl.when(pl.col('num_fills') > 1)
          .then(pl.col('execution_duration_sec') / (pl.col('num_fills') - 1))
          .otherwise(pl.lit(0.0)).alias('avg_time_between_fills'),
    ])
    agg = agg.with_columns([
        # price_improvement_bps
        pl.when(pl.col('limit_price') > 0)
          .then(pl.col('price_improvement') / pl.col('limit_price') * 10000)
          .otherwise(pl.lit(0.0)).alias('price_improvement_bps'),

        # order_timestamp rename for output consistency
        pl.col('order_timestamp').alias('order_timestamp'),
        pl.col('first_fill_time').alias('first_fill_time'),
        pl.col('last_fill_time').alias('last_fill_time'),
    ])

    # Drop internal columns and convert to pandas for StatisticsEngine boundary
    internal_cols = [c for c in agg.columns if c.startswith('_')]
    return agg.drop(internal_cols).to_pandas()


def _calculate_fill_metrics(trades: pd.DataFrame, order_context: pd.Series) -> Dict:
    """Calculate Group A fill metrics (qty filled, fill ratio, num fills, avg fill size, fill status)."""
    qty_filled = int(trades['quantity'].sum())
    order_quantity = int(order_context['order_quantity'])
    num_fills = len(trades)
    
    # Calculate ratios
    fill_ratio = qty_filled / order_quantity if order_quantity > 0 else 0.0
    fill_rate_pct = fill_ratio * 100.0
    avg_fill_size = qty_filled / num_fills if num_fills > 0 else 0.0
    
    # Determine fill status
    if qty_filled == 0:
        fill_status = 'Unfilled'
    elif qty_filled >= order_quantity:
        fill_status = 'Fully Filled'
    else:
        fill_status = 'Partially Filled'
    
    return {
        'qty_filled': qty_filled,
        'order_quantity': order_quantity,
        'fill_ratio': fill_ratio,
        'fill_rate_pct': fill_rate_pct,
        'num_fills': num_fills,
        'avg_fill_size': avg_fill_size,
        'fill_status': fill_status
    }


def _calculate_price_metrics(trades: pd.DataFrame, order_context: pd.Series) -> Dict:
    """Calculate Group B price metrics (VWAP, arrival prices, spread, limit price, price improvement)."""
    qty_filled = trades['quantity'].sum()
    
    # Calculate VWAP
    if qty_filled > 0:
        vwap = (trades['tradeprice'] * trades['quantity']).sum() / qty_filled
    else:
        vwap = 0.0
    
    # Arrival prices
    arrival_bid = float(order_context['arrival_bid']) if pd.notna(order_context['arrival_bid']) else np.nan
    arrival_offer = float(order_context['arrival_offer']) if pd.notna(order_context['arrival_offer']) else np.nan
    arrival_midpoint = float(order_context['arrival_midpoint']) if pd.notna(order_context['arrival_midpoint']) else np.nan
    
    # Arrival spread
    if pd.notna(arrival_bid) and pd.notna(arrival_offer):
        arrival_spread = arrival_offer - arrival_bid
        arrival_spread_bps = (arrival_spread / arrival_midpoint) * 10000 if arrival_midpoint > 0 else 0.0
    else:
        arrival_spread = np.nan
        arrival_spread_bps = np.nan
    
    # Limit price
    limit_price = int(order_context['order_price'])
    
    # Price improvement (positive = better)
    side = int(order_context['order_side'])
    if side == 1:  # Buy: saved money if vwap < limit
        price_improvement = limit_price - vwap
    else:  # Sell: made more if vwap > limit
        price_improvement = vwap - limit_price
    
    price_improvement_bps = (price_improvement / limit_price) * 10000 if limit_price > 0 else 0.0
    
    return {
        'vwap': vwap,
        'arrival_midpoint': arrival_midpoint,
        'arrival_bid': arrival_bid,
        'arrival_offer': arrival_offer,
        'arrival_spread': arrival_spread,
        'arrival_spread_bps': arrival_spread_bps,
        'limit_price': limit_price,
        'price_improvement': price_improvement,
        'price_improvement_bps': price_improvement_bps
    }


def _calculate_execution_cost_metrics(trades: pd.DataFrame, order_context: pd.Series, is_simulated: bool = False) -> Dict:
    """Calculate Group C execution cost metrics (arrival-based, volume-weighted, effective spread, slippage, shortfall)."""
    qty_filled = trades['quantity'].sum()
    side = int(order_context['order_side'])
    side_multiplier = 1 if side == 1 else -1  # Buy=+1, Sell=-1
    
    # Calculate VWAP
    if qty_filled > 0:
        vwap = (trades['tradeprice'] * trades['quantity']).sum() / qty_filled
    else:
        vwap = 0.0
    
    arrival_midpoint = float(order_context['arrival_midpoint']) if pd.notna(order_context['arrival_midpoint']) else np.nan
    arrival_spread = float(order_context['arrival_spread']) if pd.notna(order_context['arrival_spread']) else np.nan
    
    # Execution cost - arrival based
    # Negative = better (bought below / sold above midpoint)
    if pd.notna(arrival_midpoint) and arrival_midpoint > 0:
        exec_cost_arrival_bps = side_multiplier * ((vwap - arrival_midpoint) / arrival_midpoint) * 10000
    else:
        exec_cost_arrival_bps = np.nan
    
    # Execution cost - volume weighted (using trade-by-trade NBBO)
    # Note: For simulated trades, tradeprice = midpoint by design (see sweep_simulator.py line 444)
    # For real trades, we compare actual execution price vs trade-time NBBO midpoint
    # For simulated trades, we compare midpoint execution vs arrival midpoint (market drift)
    if is_simulated:
        # Simulated trades execute at midpoint, so compare vs arrival midpoint to show market drift
        # This shows how much the market moved from arrival to execution time
        if pd.notna(arrival_midpoint) and arrival_midpoint > 0:
            weighted_costs = []
            for _, trade in trades.iterrows():
                # trade_midpoint is the NBBO midpoint at execution time (matches tradeprice for simulated)
                trade_mid = trade.get('trade_midpoint', np.nan)
                if pd.notna(trade_mid) and trade_mid > 0:
                    # Cost = how much market moved from arrival to execution
                    trade_cost = side_multiplier * ((trade_mid - arrival_midpoint) / arrival_midpoint) * 10000
                    weighted_cost = trade_cost * trade['quantity']
                    weighted_costs.append(weighted_cost)
            
            if len(weighted_costs) > 0 and qty_filled > 0:
                exec_cost_vw_bps = sum(weighted_costs) / qty_filled
            else:
                exec_cost_vw_bps = 0.0  # No market movement
        else:
            exec_cost_vw_bps = np.nan
    else:
        # Real trades: compare actual execution price vs trade-time NBBO midpoint
        weighted_costs = []
        for _, trade in trades.iterrows():
            trade_mid = trade.get('trade_midpoint', np.nan)
            if pd.notna(trade_mid) and trade_mid > 0:
                trade_cost = side_multiplier * ((trade['tradeprice'] - trade_mid) / trade_mid) * 10000
                weighted_cost = trade_cost * trade['quantity']
                weighted_costs.append(weighted_cost)
        
        if len(weighted_costs) > 0 and qty_filled > 0:
            exec_cost_vw_bps = sum(weighted_costs) / qty_filled
        else:
            exec_cost_vw_bps = np.nan
    
    # Effective spread
    if pd.notna(arrival_midpoint) and pd.notna(arrival_spread) and arrival_spread > 0:
        effective_spread_cents = 2 * abs(vwap - arrival_midpoint)
        effective_spread_pct = (effective_spread_cents / arrival_spread) * 100
    else:
        effective_spread_pct = np.nan
    
    # Slippage (same as exec_cost_arrival_bps)
    slippage_bps = exec_cost_arrival_bps
    
    # Implementation shortfall
    if pd.notna(arrival_midpoint) and qty_filled > 0:
        actual_cost = qty_filled * vwap
        ideal_cost = qty_filled * arrival_midpoint
        implementation_shortfall_bps = ((actual_cost - ideal_cost) / ideal_cost) * 10000 if ideal_cost > 0 else 0.0
    else:
        implementation_shortfall_bps = np.nan
    
    # Total execution value
    total_execution_value = (trades['tradeprice'] * trades['quantity']).sum()
    
    return {
        'exec_cost_arrival_bps': exec_cost_arrival_bps,
        'exec_cost_vw_bps': exec_cost_vw_bps,
        'effective_spread_pct': effective_spread_pct,
        'slippage_bps': slippage_bps,
        'implementation_shortfall_bps': implementation_shortfall_bps,
        'total_execution_value': total_execution_value
    }


def _calculate_timing_metrics(trades: pd.DataFrame, order_context: pd.Series) -> Dict:
    """Calculate Group D timing metrics (order timestamp, fill times, durations, time to first fill, avg interval)."""
    order_timestamp = int(order_context['order_timestamp']) if pd.notna(order_context['order_timestamp']) else np.nan
    first_fill_time = int(trades['tradetime'].min())
    last_fill_time = int(trades['tradetime'].max())
    num_fills = len(trades)
    qty_filled = trades['quantity'].sum()
    
    # Time to first fill (from sweep order arrival)
    if pd.notna(order_timestamp):
        time_to_first_fill_sec = (first_fill_time - order_timestamp) / 1e9
        # Handle negative times (timestamp ordering issues)
        time_to_first_fill_sec = max(0.0, time_to_first_fill_sec)
    else:
        time_to_first_fill_sec = np.nan
    
    # Execution duration (first fill to last fill)
    execution_duration_sec = (last_fill_time - first_fill_time) / 1e9
    execution_duration_sec = max(0.0, execution_duration_sec)
    
    # Total duration (sweep order to last fill)
    if pd.notna(order_timestamp):
        total_duration_sec = (last_fill_time - order_timestamp) / 1e9
        total_duration_sec = max(0.0, total_duration_sec)
    else:
        total_duration_sec = np.nan
    
    # Average time between fills
    if num_fills > 1:
        avg_time_between_fills = execution_duration_sec / (num_fills - 1)
    else:
        avg_time_between_fills = 0.0
    
    # Volume-weighted execution time
    if pd.notna(order_timestamp) and qty_filled > 0:
        weighted_time = 0.0
        for _, trade in trades.iterrows():
            time_from_arrival = (trade['tradetime'] - order_timestamp) / 1e9
            time_from_arrival = max(0.0, time_from_arrival)  # Handle negative times
            weighted_time += trade['quantity'] * time_from_arrival
        vw_exec_time_sec = weighted_time / qty_filled
    else:
        vw_exec_time_sec = np.nan
    
    return {
        'order_timestamp': order_timestamp,
        'first_fill_time': first_fill_time,
        'last_fill_time': last_fill_time,
        'time_to_first_fill_sec': time_to_first_fill_sec,
        'execution_duration_sec': execution_duration_sec,
        'total_duration_sec': total_duration_sec,
        'avg_time_between_fills': avg_time_between_fills,
        'vw_exec_time_sec': vw_exec_time_sec
    }


def _calculate_market_context_metrics(trades: pd.DataFrame, order_context: pd.Series) -> Dict:
    """Calculate Group E market context metrics (fill midpoints, market drift, spread volatility, price volatility)."""
    # Midpoints at first and last fill
    if 'trade_midpoint' in trades.columns and trades['trade_midpoint'].notna().any():
        first_fill_midpoint = float(trades.iloc[0]['trade_midpoint']) if pd.notna(trades.iloc[0]['trade_midpoint']) else np.nan
        last_fill_midpoint = float(trades.iloc[-1]['trade_midpoint']) if pd.notna(trades.iloc[-1]['trade_midpoint']) else np.nan
        
        # Market drift
        if pd.notna(first_fill_midpoint) and pd.notna(last_fill_midpoint) and first_fill_midpoint > 0:
            market_drift_bps = ((last_fill_midpoint - first_fill_midpoint) / first_fill_midpoint) * 10000
        else:
            market_drift_bps = np.nan
    else:
        first_fill_midpoint = np.nan
        last_fill_midpoint = np.nan
        market_drift_bps = np.nan
    
    # Spread metrics
    if 'trade_spread' in trades.columns and 'trade_midpoint' in trades.columns:
        # Calculate spread in bps for each trade
        spread_bps_list = []
        for _, trade in trades.iterrows():
            if pd.notna(trade['trade_spread']) and pd.notna(trade['trade_midpoint']) and trade['trade_midpoint'] > 0:
                spread_bps = (trade['trade_spread'] / trade['trade_midpoint']) * 10000
                spread_bps_list.append(spread_bps)
        
        if len(spread_bps_list) > 0:
            avg_execution_spread_bps = float(np.mean(spread_bps_list))
            spread_volatility_bps = float(np.std(spread_bps_list)) if len(spread_bps_list) > 1 else 0.0
        else:
            avg_execution_spread_bps = np.nan
            spread_volatility_bps = np.nan
    else:
        avg_execution_spread_bps = np.nan
        spread_volatility_bps = np.nan
    
    # Price volatility
    if len(trades) > 1:
        qty_filled = trades['quantity'].sum()
        vwap = (trades['tradeprice'] * trades['quantity']).sum() / qty_filled if qty_filled > 0 else 0.0
        price_std = float(trades['tradeprice'].std())
        price_volatility_bps = (price_std / vwap) * 10000 if vwap > 0 else 0.0
    else:
        price_volatility_bps = 0.0
    
    return {
        'first_fill_midpoint': first_fill_midpoint,
        'last_fill_midpoint': last_fill_midpoint,
        'market_drift_bps': market_drift_bps,
        'avg_execution_spread_bps': avg_execution_spread_bps,
        'spread_volatility_bps': spread_volatility_bps,
        'price_volatility_bps': price_volatility_bps
    }


def _apply_prefix(metrics_df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """Apply prefix to all metric columns except identifiers (orderid, orderbookid)."""
    if not prefix or len(metrics_df) == 0:
        return metrics_df
    
    # Columns that should NOT be prefixed
    identifier_columns = ['orderid', 'orderbookid']
    
    # Rename all columns except identifiers
    rename_map = {}
    for col in metrics_df.columns:
        if col not in identifier_columns:
            rename_map[col] = f"{prefix}{col}"
    
    return metrics_df.rename(columns=rename_map)

# ============================================================================
# Section 8 - pipeline/execution_comparison.py (Stage 4)
# ============================================================================
"""
Metrics Generator Module

Handles calculation of simulated execution metrics and comparison with real execution:
- Calculate simulated metrics for orders (matched quantity, fill ratio, fill status, prices)
- Compare real vs simulated execution by order groups
- Generate detailed comparison reports
- Calculate statistical summaries and differences
"""

import pandas as pd
from config import col
import numpy as np
from pathlib import Path
# (cut) from utils.statistics_layer import StatisticsEngine
# (consolidated) from .trade_metrics_calculator import calculate_trade_metrics
# (consolidated) from utils import file_utils as fu

# ============================================================================
# NEW: SWEEP ORDER COMPARISON FUNCTIONS
# ============================================================================


# ============================================================================
# TRADE-LEVEL COMPARISON (Real vs Simulated Trades)
# ============================================================================


def load_real_metrics(output_dir, partition_keys):
    """Load pre-calculated real trade metrics from disk."""
    real_metrics_by_partition = {}
    
    for partition_key in partition_keys:
        partition_output_dir = Path(output_dir) / partition_key
        real_metrics_path = partition_output_dir / 'real_trade_metrics.parquet'
        
        if not real_metrics_path.exists():
            continue
        
        real_order_metrics = safe_read_csv(real_metrics_path)
        
        if len(real_order_metrics) > 0:
            real_metrics_by_partition[partition_key] = {
                'order_metrics': real_order_metrics
            }
    
    return real_metrics_by_partition


def compare_real_vs_simulated_trades(real_metrics_by_partition, simulation_results_by_partition, output_dir):
    """Compare real trades with simulated trades at trade level."""
    print(f"\n[12/11] Comparing real vs simulated trades...")
    
    trade_comparison_by_partition = {}
    
    for partition_key, real_metrics in real_metrics_by_partition.items():
        sim_results = simulation_results_by_partition.get(partition_key)
        
        if not sim_results:
            print(f"  {partition_key}: No simulation results found")
            continue
        
        # Extract data
        real_order_metrics = real_metrics['order_metrics']
        sim_trades = sim_results['simulated_trades']
        sim_order_summary = sim_results['order_summary']
        
        if len(sim_trades) == 0:
            print(f"  {partition_key}: No simulated trades")
            continue
        
        # Load orders to get arrival NBBO for simulated metrics. Use orders_after
        # because sweeps appear there in their final state with their original
        # `quantity`; orders_before contains contras (resting orders), not sweeps.
        date, security_code = partition_key.split('/')
        partition_dir = Path(output_dir).parent / "processed" / date / security_code
        orders_after_file = partition_dir / "orders_after_matching.parquet"
        if orders_after_file.exists():
            orders_for_sim = fu.safe_read_csv(orders_after_file, required=False)
        else:
            orders_for_sim = fu.load_orders_before(partition_dir)

        # Aggregate simulated trades per order (for sweep orders)
        sim_aggregated = _aggregate_simulated_trades_per_order(sim_trades, sim_order_summary, orders_for_sim)
        
        # Compare real vs simulated at order level
        comparison = _compare_order_level_trades(real_order_metrics, sim_aggregated)
        
        # Calculate accuracy summary
        accuracy_summary = _calculate_trade_accuracy_summary(comparison)
        
        trade_comparison_by_partition[partition_key] = {
            'trade_level_comparison': comparison,
            'trade_accuracy_summary': accuracy_summary,
            'real_metrics': real_order_metrics,  # Add full metrics for saving
            'sim_metrics': sim_aggregated  # Add full metrics for saving
        }
        
        print(f"  {partition_key}: Compared {len(comparison):,} sweep orders")
    
    return trade_comparison_by_partition


def _aggregate_simulated_trades_per_order(simulated_trades, order_summary, orders_df=None):
    """Aggregate simulated trades per sweep order using unified calculator with optional order context."""
    # Get unique orderids from order_summary (these are sweep orders)
    if order_summary is not None and not order_summary.empty:
        sweep_orderids = order_summary['orderid'].unique().tolist()
    else:
        # If no order_summary, derive sweep orderids from all simulated trade rows
        # (do not filter by passiveaggressive — sweeps appear as PA=1 in Phase 1 and PA=0 in Phase 2)
        sweep_orderids = simulated_trades['orderid'].unique().tolist()
    
    # Create order context for metrics calculation
    if orders_df is not None and not orders_df.empty:
        # Use provided orders (has arrival NBBO from orders_before/orders_after)
        order_context = orders_df[orders_df['orderid'].isin(sweep_orderids)].copy()
        
        # Ensure required columns exist
        if 'timestamp' not in order_context.columns and 'arrival_time' in order_context.columns:
            order_context['timestamp'] = order_context['arrival_time']
    else:
        # Fallback: create minimal order data from simulated trades (no arrival NBBO).
        # Use all rows for each sweep orderid — both PA=1 (Phase 1 aggressor) and
        # PA=0 (Phase 2 resting) rows carry consistent side/price metadata.
        order_context = simulated_trades[simulated_trades['orderid'].isin(sweep_orderids)].groupby('orderid').agg({
            'tradetime': 'min',  # Use first trade time as proxy for order time
            'side': 'first',
            'quantity': 'sum',  # Total matched quantity
            'tradeprice': 'first'  # Use as proxy for limit price
        }).reset_index()
        order_context = order_context.rename(columns={
            'tradetime': 'timestamp',
            'tradeprice': 'price'
        })
    
    # Calculate metrics using unified calculator
    # Note: Simulated trades execute at midpoint (see sweep_simulator.py line 444)
    metrics = calculate_trade_metrics(
        trades_df=simulated_trades,
        orders_df=order_context,
        nbbo_df=None,
        filter_orderids=sweep_orderids,
        role_filter=None,  # Include both Phase 1 (PA=1) and Phase 2 resting (PA=0) fills
        prefix='sim_',
        is_simulated=True,
    )
    
    return metrics['per_order_metrics']


def _compare_order_level_trades(real_metrics, sim_metrics):
    """Compare real vs simulated trades at order level using comprehensive metrics."""
    # Map comprehensive metric names to expected comparison names
    # Real metrics use new names directly
    real_mapped = real_metrics.copy()
    real_mapped = real_mapped.rename(columns={
        'qty_filled': 'total_quantity_filled',
        'num_fills': 'total_trades',
        'vwap': 'weighted_avg_price'
    })
    
    # Simulated metrics already have sim_ prefix, just map the base names
    sim_mapped = sim_metrics.copy()
    sim_mapped = sim_mapped.rename(columns={
        'sim_qty_filled': 'sim_total_quantity',
        'sim_num_fills': 'sim_total_matches',
        'sim_vwap': 'sim_avg_price'
    })
    
    # Merge real and simulated metrics
    comparison = real_mapped.merge(
        sim_mapped,
        on='orderid',
        how='outer',
        suffixes=('_real', '_sim')
    )
    
    # Fill NaN values
    comparison = comparison.fillna(0)
    
    # Calculate differences
    comparison['quantity_diff'] = comparison['sim_total_quantity'] - comparison['total_quantity_filled']
    comparison['quantity_accuracy_pct'] = np.where(
        comparison['total_quantity_filled'] > 0,
        (comparison['sim_total_quantity'] / comparison['total_quantity_filled']) * 100,
        0
    )
    
    comparison['num_trades_diff'] = comparison['sim_total_matches'] - comparison['total_trades']
    
    comparison['price_diff'] = comparison['sim_avg_price'] - comparison['weighted_avg_price']
    comparison['price_error_pct'] = np.where(
        comparison['weighted_avg_price'] > 0,
        abs(comparison['price_diff'] / comparison['weighted_avg_price']) * 100,
        0
    )
    
    comparison['execution_time_diff_sec'] = (
        comparison['sim_execution_duration_sec'] - comparison['execution_duration_sec']
    )
    
    # Calculate match status
    comparison['match_status'] = comparison.apply(_determine_match_status, axis=1)
    
    # Calculate accuracy score (0-100)
    comparison['accuracy_score'] = comparison.apply(_calculate_accuracy_score, axis=1)
    
    return comparison


def _determine_match_status(row):
    """Determine the match status between real and simulated."""
    
    qty_diff_pct = abs(row['quantity_diff']) / max(row['total_quantity_filled'], 1) * 100
    price_diff_pct = row['price_error_pct']
    time_diff_sec = abs(row['execution_time_diff_sec'])
    
    if qty_diff_pct < 5 and price_diff_pct < 1 and time_diff_sec < 1:
        return 'EXACT_MATCH'
    elif qty_diff_pct < 10 and price_diff_pct < 5:
        return 'CLOSE_MATCH'
    elif qty_diff_pct < 25:
        return 'PARTIAL_MATCH'
    else:
        return 'POOR_MATCH'


def _calculate_accuracy_score(row):
    """Calculate accuracy score (0-100) for trade comparison."""
    
    # Quantity accuracy (40 points max)
    qty_accuracy = 40 * min(
        row['sim_total_quantity'] / max(row['total_quantity_filled'], 1),
        1.0
    )
    
    # Price accuracy (30 points max)
    price_accuracy = 30 * max(0, 1 - row['price_error_pct'] / 100)
    
    # Trade count accuracy (20 points max)
    trade_count_accuracy = 20 * min(
        row['sim_total_matches'] / max(row['total_trades'], 1),
        1.0
    )
    
    # Time accuracy (10 points max)
    time_accuracy = 10 * max(0, 1 - abs(row['execution_time_diff_sec']) / 10)
    
    return qty_accuracy + price_accuracy + trade_count_accuracy + time_accuracy


def _calculate_trade_accuracy_summary(comparison):
    """Calculate summary statistics for trade accuracy."""
    
    summary = {
        'total_orders': len(comparison),
        'orders_with_real_trades': (comparison['total_quantity_filled'] > 0).sum(),
        'orders_with_sim_matches': (comparison['sim_total_quantity'] > 0).sum(),
        
        # Quantity metrics
        'total_real_quantity': comparison['total_quantity_filled'].sum(),
        'total_sim_quantity': comparison['sim_total_quantity'].sum(),
        'quantity_match_rate_pct': (comparison['sim_total_quantity'].sum() / 
                                     max(comparison['total_quantity_filled'].sum(), 1)) * 100,
        
        # Price metrics
        'avg_price_error_pct': comparison['price_error_pct'].mean(),
        'median_price_error_pct': comparison['price_error_pct'].median(),
        'price_rmse': np.sqrt((comparison['price_diff'] ** 2).mean()),
        
        # Trade count metrics
        'total_real_trades': comparison['total_trades'].sum(),
        'total_sim_matches': comparison['sim_total_matches'].sum(),
        'trade_count_match_rate_pct': (comparison['sim_total_matches'].sum() / 
                                        max(comparison['total_trades'].sum(), 1)) * 100,
        
        # Time metrics
        'avg_time_diff_sec': comparison['execution_time_diff_sec'].mean(),
        'median_time_diff_sec': comparison['execution_time_diff_sec'].median(),
        
        # Match status distribution
        'exact_matches': (comparison['match_status'] == 'EXACT_MATCH').sum(),
        'close_matches': (comparison['match_status'] == 'CLOSE_MATCH').sum(),
        'partial_matches': (comparison['match_status'] == 'PARTIAL_MATCH').sum(),
        'poor_matches': (comparison['match_status'] == 'POOR_MATCH').sum(),
        
        # Overall accuracy
        'avg_accuracy_score': comparison['accuracy_score'].mean(),
        'median_accuracy_score': comparison['accuracy_score'].median(),
    }
    
    return pd.DataFrame([summary])


def generate_trade_comparison_reports(trade_comparison_by_partition, output_dir, include_accuracy_summary=True):
    """Generate trade-level comparison reports."""
    print(f"\n  Generating trade-level comparison reports...")
    
    import sys as _sfu_sys; fu = _sfu_sys.modules[__name__]  # was utils.file_utils
    
    report_files_by_partition = {}
    
    for partition_key, comparison_data in trade_comparison_by_partition.items():
        partition_output_dir = Path(output_dir) / partition_key
        partition_output_dir.mkdir(parents=True, exist_ok=True)
        
        report_files = {}
        
        # Report 1: Trade-level comparison
        if 'trade_level_comparison' in comparison_data:
            file_path = partition_output_dir / 'trade_level_comparison.parquet'
            safe_write_csv(comparison_data['trade_level_comparison'], file_path, create_dirs=False)
            report_files['trade_level_comparison'] = file_path
            print(f"    {partition_key}/trade_level_comparison.parquet: {len(comparison_data['trade_level_comparison']):,} orders")

        # Report 2: Trade accuracy summary (optional)
        if include_accuracy_summary and 'trade_accuracy_summary' in comparison_data:
            file_path = partition_output_dir / 'trade_accuracy_summary.parquet'
            safe_write_csv(comparison_data['trade_accuracy_summary'], file_path, create_dirs=False)
            report_files['trade_accuracy_summary'] = file_path
            print(f"    {partition_key}/trade_accuracy_summary.parquet: Overall metrics")
        
        # NEW: Save full metrics (36 metrics per order) for Stage 3 to load
        # This avoids recalculating the same metrics in Stage 3
        if 'real_metrics' in comparison_data and 'sim_metrics' in comparison_data:
            fu.save_trade_metrics(
                comparison_data['real_metrics'],
                comparison_data['sim_metrics'],
                output_dir,
                partition_key
            )
            print(f"    {partition_key}/real_trade_metrics.csv: {len(comparison_data['real_metrics']):,} orders (36 metrics)")
            print(f"    {partition_key}/simulated_trade_metrics.csv: {len(comparison_data['sim_metrics']):,} orders (36 metrics)")
        
        report_files_by_partition[partition_key] = report_files
    
    return report_files_by_partition


# ============================================================================
# Section 9 - pipeline/partition_processor.py
# ============================================================================
"""Partition processing logic for pipeline steps 7-12."""

import pandas as pd
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

# (consolidated) import pipeline.data_processor as dp
# (deferred to process.py) import pipeline.sweep_simulator as ss
# (consolidated) import pipeline.execution_comparison as ec
# (consolidated) import utils.file_utils as fu
# (consolidated) import utils.data_utils as du
from config import col
# (consolidated) from .trade_metrics_calculator import calculate_trade_metrics


def process_partitions_parallel_stage_3(partition_keys, processed_dir, outputs_dir, max_workers):
    """Process partitions in parallel for Stage 3 (calculate both real and simulated metrics)."""
    print(f"\nProcessing {len(partition_keys)} partitions with {max_workers} workers...")
    
    partition_results = {}
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit metrics calculation jobs
        futures = {
            executor.submit(
                _process_partition_calculate_metrics,
                partition_key,
                processed_dir,
                outputs_dir
            ): partition_key
            for partition_key in partition_keys
        }
        
        # Collect results
        completed = 0
        for future in as_completed(futures):
            partition_key = futures[future]
            completed += 1
            
            try:
                result = future.result()
                partition_results[partition_key] = result
                
                if result.get('status') == 'success':
                    print(f"  [{completed}/{len(partition_keys)}] ✓ {partition_key}: "
                          f"Calculated metrics for {result.get('num_orders', 0):,} orders")
                elif result.get('status') == 'skipped':
                    print(f"  [{completed}/{len(partition_keys)}] ⊘ {partition_key}: "
                          f"Skipped - {result.get('reason', 'Unknown')}")
                else:
                    print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: "
                          f"ERROR - {result.get('error', 'Unknown')}")
            except Exception as e:
                print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: EXCEPTION - {str(e)}")
                partition_results[partition_key] = {
                    'partition_key': partition_key,
                    'status': 'exception',
                    'error': str(e)
                }
    
    _print_processing_summary(partition_results, partition_keys)
    return partition_results


def _process_partition_calculate_metrics(partition_key, processed_dir, outputs_dir):
    """Process single partition for Stage 3: calculate real and simulated metrics."""
    try:
        partition_dir = fu.get_partition_dir(processed_dir, partition_key)
        
        # Load partition data
        orders_before = fu.load_orders_before(partition_dir)
        trades_df = fu.load_trades_matched(partition_dir)
        
        if orders_before is None or trades_df is None:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'Missing orders or trades data'
            }
        
        # Get sweep order IDs
        sweep_orderids = du.get_sweep_orderids(orders_before)
        
        if len(sweep_orderids) == 0:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No sweep orders'
            }
        
        # Filter trades to sweep orders only
        sweep_trades = trades_df[trades_df[col.common.orderid].isin(sweep_orderids)].copy()
        
        # Step 8: Calculate REAL trade metrics
        real_metrics_result = calculate_trade_metrics(
            trades_df=sweep_trades,
            orders_df=orders_before,
            filter_orderids=list(sweep_orderids),
            role_filter=None,
            prefix='',
            is_simulated=False
        )
        real_order_metrics = real_metrics_result['per_order_metrics']
        
        # Step 9: Calculate SIMULATED trade metrics
        # Load simulation results
        simulated_trades = fu.load_simulation_trades(partition_dir)
        output_partition_dir = fu.get_partition_dir(outputs_dir, partition_key)
        order_summary = fu.load_simulation_order_summary(output_partition_dir)

        if simulated_trades is not None and order_summary is not None:
            # Use orders_after as the order context for simulated metrics — sweeps
            # appear there (in their final state) but typically NOT in orders_before
            # (they aggressed rather than rested). orders_after's `quantity` column
            # is the original order quantity, which is what the metrics calc needs.
            orders_after_file = partition_dir / "orders_after_matching.parquet"
            if orders_after_file.exists():
                from process import safe_read_csv as _safe_read
                orders_after = _safe_read(orders_after_file, required=False)
            else:
                orders_after = None
            sim_aggregated = ec._aggregate_simulated_trades_per_order(
                simulated_trades,
                order_summary,
                orders_after if orders_after is not None else orders_before,
            )
        else:
            sim_aggregated = None
        
        # Save metrics
        fu.save_trade_metrics(real_order_metrics, sim_aggregated, outputs_dir, partition_key)
        
        return {
            'partition_key': partition_key,
            'status': 'success',
            'num_orders': len(real_order_metrics)
        }
        
    except Exception as e:
        return {
            'partition_key': partition_key,
            'status': 'error',
            'error': str(e)
        }


def _print_processing_summary(partition_results, partition_keys):
    """Print summary of parallel processing results."""
    successful = sum(1 for r in partition_results.values() if r.get('status') == 'success')
    failed = sum(1 for r in partition_results.values() if r.get('status') in ('error', 'exception'))
    skipped = sum(1 for r in partition_results.values() if r.get('status') == 'skipped')
    
    print(f"\nPARALLEL PROCESSING COMPLETE")
    print(f"  ✓ Successful: {successful}/{len(partition_keys)}")
    print(f"  ✗ Failed: {failed}/{len(partition_keys)}")
    print(f"  ⊘ Skipped: {skipped}/{len(partition_keys)}")

# ============================================================================
# ============================================================================
# Section 10 - CLI plumbing (bulk-mode entry only)
# ============================================================================
# Legacy per-(ticker,date) plumbing (parse_arguments, build_runtime_config,
# execute_pipeline_stages, _process_single_security, SecurityDiscovery, etc.)
# was deleted on 2026-04-28 — superseded by the bulk-mode `cli_multi_agg()` below.

def setup_directories():
    """Create output directories if they don't exist."""
    Path(config.PROCESSED_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)


import sys as _sys_a
import argparse as _argparse_a
import config  # top-level config.py at repo root

# Self-aliases so legacy code's module references resolve to this module.
dp = _sys_a.modules[__name__]
pp = _sys_a.modules[__name__]
ec = _sys_a.modules[__name__]
fu = _sys_a.modules[__name__]
du = _sys_a.modules[__name__]
ss = _sys_a.modules[__name__]
tm = _sys_a.modules[__name__]

def cli_multi_agg():
    """Bulk-ingest aggregate CLI: stages 3+4 over partitions in data/processed/.

    Re-runs Stage 1 (extract from raw) to rebuild the in-memory `data` dict that
    Stage 4 needs, then runs Stage 3 (metrics) + Stage 4 (comparison).
    Stage 2 simulation is skipped here — process.py is responsible for that.
    --dates / --tickers / --orderbookids are all optional post-read filters.
    """
    parser = _argparse_a.ArgumentParser(
        prog="aggregate.py",
        description="Stage 3+4: metrics + real-vs-sim comparison."
    )
    parser.add_argument("--dates", default=None,
                        help="Optional comma-separated YYYYMMDD trade-date filter (post-read).")
    parser.add_argument("--tickers", default=None,
                        help="Optional comma-separated ticker filter (matches filename substring).")
    parser.add_argument("--orderbookids", default=None,
                        help="Optional comma-separated orderbookid filter (parquet predicate-pushdown).")
    parser.add_argument("--workers", type=int, default=None,
                        help="Worker pool size (default: auto).")
    args = parser.parse_args()

    dates_filter = None
    if args.dates:
        dates_filter = []
        for d in (x.strip() for x in args.dates.split(",") if x.strip()):
            dates_filter.append(f"{d[0:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else d)

    orderbookids_filter = None
    if args.orderbookids:
        orderbookids_filter = {int(x) for x in args.orderbookids.split(",") if x.strip()}

    tickers_hint = None
    if args.tickers:
        tickers_hint = [t.strip().lower() for t in args.tickers.split(",") if t.strip()]

    orders_files = _glob_raw_inputs(config.RAW_FOLDERS['orders'])
    trades_files = _glob_raw_inputs(config.RAW_FOLDERS['trades'])

    if tickers_hint:
        def _match(p): return any(t in p.name.lower() for t in tickers_hint)
        orders_files = [f for f in orders_files if _match(f)]
        trades_files = [f for f in trades_files if _match(f)]

    if not orders_files:
        print(f"[aggregate] No orders files found in {config.RAW_FOLDERS['orders']}.", file=_sys_a.stderr)
        _sys_a.exit(2)

    print(f"[aggregate] Discovered {len(orders_files)} orders + {len(trades_files)} trades file(s).")
    if dates_filter:        print(f"[aggregate] dates filter (post-read): {dates_filter}")
    if orderbookids_filter: print(f"[aggregate] orderbookids filter (push-down): {sorted(orderbookids_filter)}")
    if tickers_hint:        print(f"[aggregate] tickers hint (filename substring): {tickers_hint}")

    setup_directories()

    print("\n" + "=" * 80)
    print("STAGE 1: DATA EXTRACTION & PREPARATION (rebuild in-memory data for Stage 4)")
    print("=" * 80)

    orders_by_partition = extract_orders(
        orders_files, config.PROCESSED_DIR,
        config.CENTRE_POINT_ORDER_TYPES, config.CHUNK_SIZE,
        orderbookids_filter=orderbookids_filter,
        dates_filter=dates_filter,
    )
    if not orders_by_partition:
        print("[aggregate] No Centre Point orders found. Exiting.")
        return

    trades_by_partition = extract_trades(
        trades_files, orders_by_partition, config.PROCESSED_DIR, config.CHUNK_SIZE,
    )
    reference_results = process_reference_data(config.RAW_FOLDERS, config.PROCESSED_DIR, orders_by_partition)
    nbbo_by_partition = reference_results.get('nbbo', {})
    order_states_by_partition = get_orders_state(orders_by_partition, config.PROCESSED_DIR)
    last_execution_by_partition = extract_last_execution_times(
        orders_by_partition, trades_by_partition, config.PROCESSED_DIR
    )

    data = {
        'orders': orders_by_partition,
        'trades': trades_by_partition,
        'order_states': order_states_by_partition,
        'last_execution': last_execution_by_partition,
        'nbbo': nbbo_by_partition,
        'reference': reference_results,
        'partition_keys': list(orders_by_partition.keys()),
    }

    workers = args.workers or config.MAX_PARALLEL_WORKERS

    print("\n" + "=" * 80)
    print(f"STAGE 3: CALCULATE METRICS — {len(data['partition_keys'])} partition(s), {workers} worker(s)")
    print("=" * 80)
    process_partitions_parallel_stage_3(
        data['partition_keys'], config.PROCESSED_DIR, config.OUTPUTS_DIR, workers
    )

    print("\n" + "=" * 80)
    print("STAGE 4: METRICS COMPARISON (Step 10)")
    print("=" * 80)
    print("\n[10/11] Loading and comparing metrics...")
    partition_keys = data['partition_keys']
    real_metrics = load_real_metrics(config.OUTPUTS_DIR, partition_keys)
    simulation_results = {}
    for pk in partition_keys:
        partition_dir = get_partition_dir(config.PROCESSED_DIR, pk)
        out_partition_dir = get_partition_dir(config.OUTPUTS_DIR, pk)
        sim_trades = load_simulation_trades(partition_dir)
        order_summary = load_simulation_order_summary(out_partition_dir)
        if sim_trades is not None and order_summary is not None:
            simulation_results[pk] = {'simulated_trades': sim_trades, 'order_summary': order_summary}
    if real_metrics and simulation_results:
        trade_comparison = compare_real_vs_simulated_trades(real_metrics, simulation_results, config.OUTPUTS_DIR)
        generate_trade_comparison_reports(trade_comparison, config.OUTPUTS_DIR, include_accuracy_summary=False)
    else:
        print("  ✗ Cannot compare: missing metrics files. Run stages 2-3 first.")

    print(f"\n✓ Stage 3+4 complete for {len(data['partition_keys'])} partition(s)")


if __name__ == '__main__':
    cli_multi_agg()
