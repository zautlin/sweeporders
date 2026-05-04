"""sweeporders process.py - Stages 1+2 (ingest + simulate).

Flat consolidation of:
  utils/normalization.py, utils/data_utils.py, utils/file_utils.py, utils/io_backend.py,
  pipeline/reference_data.py, pipeline/data_processor.py,
  pipeline/sweep_simulator/_legacy.py,
  pipeline/partition_processor.py, pipeline/pipeline_stages.py,
  pipeline/pipeline_output.py, pipeline/pipeline_config.py, main.py

Run: python process.py --dates 20240505 --tickers cba
     python process.py --dates 20240505,20240905 --auto-tickers --workers 4
"""

# ============================================================================
# Section 1 - utils/normalization.py
# ============================================================================
"""Column normalization — re-exported from config for back-compat.

The canonical map and helper now live in config.COLUMN_NORMALIZATION_MAP /
config.normalize_column_names. Add new server-side aliases there.
"""

import pandas as pd

from config import normalize_column_names

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
# (lean port) original line `sys.path.insert(0, dirname(dirname(__file__)))` removed —
# legacy hack from src/-layout days. The flat layout has config.py next to this
# script, so Python's normal resolution finds it without help.
# (consolidated) import config.config as config


def get_partition_dir(base_dir, partition_key):
    """Build partition directory path from partition key (date/security)."""
    date, security = partition_key.split('/')
    return Path(base_dir) / date / security


def _glob_raw_inputs(folder):
    """List raw inputs under folder. Both .parquet and .csv are read via DuckDB
    (parallel CSV parser, much faster than pandas chunked-read). When both
    formats exist for the same stem, .parquet is preferred.
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


def load_nbbo(partition_dir):
    """Load nbbo parquet from partition directory."""
    filepath = Path(partition_dir) / "nbbo.parquet"
    return safe_read_csv(filepath, required=False)


def save_simulation_results(sim_results, output_dir, partition_key):
    """Save simulation outputs (order_summary and simulated_trades)."""
    partition_output_dir = Path(output_dir) / partition_key
    partition_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save order summary
    if 'order_summary' in sim_results and sim_results['order_summary'] is not None:
        safe_write_csv(
            sim_results['order_summary'],
            partition_output_dir / 'simulation_order_summary.parquet',
            create_dirs=False
        )
    
    # Save simulated trades to processed directory
    if 'simulated_trades' in sim_results and sim_results['simulated_trades'] is not None:
        simulated_trades = sim_results['simulated_trades']
        if len(simulated_trades) > 0:
            # Save to processed directory instead of outputs
            processed_dir = Path(output_dir).parent / 'processed'
            partition_processed_dir = processed_dir / partition_key
            partition_processed_dir.mkdir(parents=True, exist_ok=True)
            
            trades_filename = 'cp_trades_simulation.parquet'
            safe_write_csv(
                simulated_trades,
                partition_processed_dir / trades_filename,
                create_dirs=False
            )


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
import config as cfg


_reference_loader = None


# ============================================================================
# Section 6 - pipeline/data_processor.py (Stage 1)
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
from config import SWEEP_ORDER_TYPE
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
        df = safe_read_csv(Path(file))
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
    """Keep orders that have at least one real trade.

    Previously filtered out orders that had any non-lit (dealsource != 1)
    trades. That filter has been removed — we now keep BOTH lit-only and
    mixed (dark + lit) fully-filled sweeps. The simulator's question for
    mixed sweeps is "what if the order had stayed entirely in dark?"
    against the original contra book.
    """
    qualifying_trades = trades_df[trades_df[col.common.orderid].isin(order_ids)].copy()

    orders_with_valid_trades = {}
    for order_id in order_ids:
        order_trades = qualifying_trades[qualifying_trades[col.common.orderid] == order_id]
        if len(order_trades) == 0:
            continue                              # no real fills → nothing to compare against
        orders_with_valid_trades[order_id] = order_trades
    return orders_with_valid_trades


def _compute_rest_on_lit_qty(order_df):
    """Quantity of a sweep order that ended up resting on the lit book.

    A sweep order's lifecycle on ASX: arrive → match in dark → match aggressively
    in lit at the same instant → rest on lit with whatever's left → get hit by
    later contras. The "rest-on-lit quantity" is the leavesquantity at the end
    of that initial matching pass — i.e. at the last event sharing the
    NEW_ORDER (changereason=6) timestamp.

    This is the quantity we want the simulator to counterfactually re-route to
    dark resting: "would dark resting have filled the part that real-life
    parked on the lit book?"

    Returns 0 if the order has no NEW_ORDER event (shouldn't happen for
    qualifying sweeps — the gate already requires changereason=6) or if the
    initial pass cleaned the order out (no resting portion → drop from sim).
    """
    new_order_events = order_df[order_df[col.common.changereason] == 6]
    if len(new_order_events) == 0:
        return 0
    init_ts = int(new_order_events[col.common.timestamp].iloc[0])
    same_ts = order_df[order_df[col.common.timestamp] == init_ts]
    if len(same_ts) == 0:
        return 0
    same_ts_sorted = same_ts.sort_values(col.common.sequence)
    return int(same_ts_sorted[col.common.leavesquantity].iloc[-1])


def _extract_execution_time_dict(order_id, order_df, trades_df):
    """Extract first/last execution times and rest-on-lit qty for one order."""
    first_time = order_df[col.common.timestamp].min()
    last_time = trades_df[col.common.tradetime].max()
    rest_on_lit = _compute_rest_on_lit_qty(order_df)

    return {
        'orderid': order_id,
        'first_execution_time': first_time,
        'last_execution_time': last_time,
        'rest_on_lit_quantity': rest_on_lit,
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
        # Predicate filter — pushdown for parquet, scan-time WHERE for CSV.
        filters = [(col.orders.order_type, 'in', list(order_types))]
        if orderbookids_filter:
            filters.append((col.orders.security_code, 'in', list(orderbookids_filter)))
        chunk, n_total = safe_read_csv(fp, filters=filters, return_total=True)
        total_rows += n_total
        if chunk is not None and len(chunk) > 0:
            # Normalize raw → canonical on-disk schema before downstream filters / groupby
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
    grouped = list(orders.groupby(['date', col.common.orderbookid]))
    n_partitions = len(grouped)
    for i, ((date, security_code_val), group_df) in enumerate(grouped, 1):
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
            print(f"  [{i}/{n_partitions}] {partition_key}: {len(group_df):,} orders "
                  f"({size_mb:.2f} MB)", flush=True)
        else:
            print(f"  [{i}/{n_partitions}] {partition_key}: {len(group_df):,} orders "
                  f"(in-memory)", flush=True)
    
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


class ReferenceDataLoader:
    """Load and manage reference data for simulation."""
    
    def __init__(self, processed_dir):
        """Initialize reference data loader."""
        self.processed_dir = Path(processed_dir)
        self.tick_sizes = {}
        self.tick_size_tables = {}
        self.participants = {}
        self.sessions = {}
        self.price_limits = {}
        
    def load_participants(self):
        """Load participant reference data."""
        participants_file = self.processed_dir / 'participants.parquet'
        if not participants_file.exists():
            return None
        participants = safe_read_csv(participants_file)
        if 'Id' in participants.columns:
            self.participants = participants.set_index('Id').to_dict('index')
        return participants
    
    def get_participant_info(self, participant_id):
        """Get participant information."""
        return self.participants.get(participant_id)
    
    def get_participant_type(self, participant_id):
        """Get participant type (Broker, Market Maker, etc.)."""
        info = self.get_participant_info(participant_id)
        if info:
            return info.get('ParticipantType', 'Unknown')
        return 'Unknown'
    
    def load_tick_size_table(self, orderbookid):
        """Load tick size table from reference data."""
        if orderbookid in self.tick_size_tables:
            return self.tick_size_tables[orderbookid]
        
        reference_file = self.processed_dir / 'reference.parquet'
        if not reference_file.exists():
            self.tick_size_tables[orderbookid] = None
            return None

        reference = safe_read_csv(reference_file)
        orderbook_ref = reference[reference.get('OrderBookId', reference.get('orderbookid')) == orderbookid]

        if len(orderbook_ref) == 0:
            self.tick_size_tables[orderbookid] = None
            return None
        
        if 'TickSize' in orderbook_ref.columns:
            tick_size = orderbook_ref['TickSize'].iloc[0]
            if pd.notna(tick_size):
                tick_size_table = [{'lower_limit': 0, 'upper_limit': float('inf'), 'tick_size': int(tick_size)}]
                self.tick_size_tables[orderbookid] = tick_size_table
                return tick_size_table
        
        self.tick_size_tables[orderbookid] = None
        return None
    
    def get_tick_size_table(self, orderbookid):
        """Get tick size table for orderbook."""
        return self.load_tick_size_table(orderbookid)
    
    def get_tick_size_for_price(self, price, tick_size_table):
        """Get tick size for given price level."""
        if tick_size_table is None:
            return 10
        for ts in tick_size_table:
            if ts['lower_limit'] <= price <= ts['upper_limit']:
                return ts['tick_size']
        return tick_size_table[0]['tick_size'] if tick_size_table else 10
    
    def infer_tick_size_from_prices(self, prices):
        """Infer tick size from a list of prices."""
        if len(prices) < 2:
            return 10
        sorted_prices = sorted(set(prices))
        if len(sorted_prices) < 2:
            return 10
        diffs = [sorted_prices[i+1] - sorted_prices[i] for i in range(len(sorted_prices)-1)]
        min_diff = min(diffs)
        if all(d % min_diff == 0 for d in diffs):
            return min_diff
        return min_diff
    
    def load_tick_sizes_from_nbbo(self, partition_dir):
        """Load/estimate tick sizes from NBBO data."""
        nbbo_file = Path(partition_dir) / 'nbbo.parquet'
        if not nbbo_file.exists():
            return 10
        nbbo = safe_read_csv(nbbo_file)
        if len(nbbo) == 0:
            return 10
        if 'bid' in nbbo.columns and 'offer' in nbbo.columns:
            spreads = nbbo['offer'] - nbbo['bid']
            spreads = spreads[spreads > 0]
            if len(spreads) > 0:
                median_spread = spreads.median()
                tick_size = max(1, int(median_spread) // 2)
                return tick_size
        return 10
    
    def load_tick_sizes_from_orders(self, partition_dir):
        """Estimate tick size from order prices."""
        orders_file = Path(partition_dir) / 'orders_before_matching.parquet'
        if not orders_file.exists():
            return 10
        orders = safe_read_csv(orders_file).head(1000)
        if 'price' not in orders.columns:
            return 10
        prices = orders['price'].dropna().unique()
        if len(prices) < 2:
            return 10
        return self.infer_tick_size_from_prices(prices)
    
    def get_tick_size(self, partition_dir, orderbookid=None):
        """Get tick size for a partition/orderbook."""
        if orderbookid and orderbookid in self.tick_sizes:
            return self.tick_sizes[orderbookid]
        
        tick_size = self.load_tick_sizes_from_nbbo(partition_dir)
        if tick_size == 10:
            tick_size = self.load_tick_sizes_from_orders(partition_dir)
        
        if orderbookid:
            self.tick_sizes[orderbookid] = tick_size
        return tick_size
    
    def load_price_limits(self, orderbookid):
        """Load price limits for orderbook."""
        if orderbookid in self.price_limits:
            return self.price_limits[orderbookid]
        
        reference_file = self.processed_dir / 'reference.parquet'
        if not reference_file.exists():
            self.price_limits[orderbookid] = None
            return None

        reference = safe_read_csv(reference_file)
        orderbook_ref = reference[reference.get('OrderBookId', reference.get('orderbookid')) == orderbookid]
        
        if len(orderbook_ref) == 0:
            self.price_limits[orderbookid] = None
            return None
        
        limits = {}
        if 'PriceLimitLower' in orderbook_ref.columns:
            limits['lower_limit'] = orderbook_ref['PriceLimitLower'].iloc[0]
        if 'PriceLimitUpper' in orderbook_ref.columns:
            limits['upper_limit'] = orderbook_ref['PriceLimitUpper'].iloc[0]
        if 'ReferencePrice' in orderbook_ref.columns:
            ref_price = orderbook_ref['ReferencePrice'].iloc[0]
            if pd.notna(ref_price):
                if 'lower_limit' not in limits:
                    limits['lower_limit'] = ref_price * 0.9
                if 'upper_limit' not in limits:
                    limits['upper_limit'] = ref_price * 1.1
        
        if limits:
            self.price_limits[orderbookid] = limits
            return limits
        
        self.price_limits[orderbookid] = None
        return None
    
    def get_price_limits(self, orderbookid):
        """Get price limits for orderbook (alias for load_price_limits)."""
        return self.load_price_limits(orderbookid)
    
    def load_all_reference_data(self, partition_dirs):
        """Load all reference data for multiple partitions."""
        reference_data = {}
        self.load_participants()
        
        for partition_dir in partition_dirs:
            partition_key = Path(partition_dir).name
            orderbookid = int(partition_key) if partition_key.isdigit() else None
            
            tick_size = self.get_tick_size(str(partition_dir), orderbookid)
            tick_size_table = self.load_tick_size_table(orderbookid)
            price_limits = self.load_price_limits(orderbookid)
            
            reference_data[partition_key] = {
                'tick_size': tick_size,
                'tick_size_table': tick_size_table,
                'price_limits': price_limits,
                'orderbookid': orderbookid,
            }
        
        return reference_data


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
    """Extract first/last execution times + rest-on-lit qty for SWEEP ORDERS.

    Only sweeps with rest_on_lit_quantity > 0 reach the simulator —
    a sweep that was fully filled at submission has no resting portion to
    counterfactually re-route to dark.
    """
    print(f"\n[6/11] Extracting execution times for qualifying sweep orders (type {SWEEP_ORDER_TYPE}) with three-level filtering...")

    empty_cols = ['orderid', 'first_execution_time', 'last_execution_time',
                  'rest_on_lit_quantity']
    execution_times_by_partition = {}

    for partition_key, orders_df in orders_by_partition.items():
        if len(orders_df) == 0:
            continue

        qualifying_order_ids = _filter_sweep_orders_by_execution(orders_df)

        if not qualifying_order_ids or partition_key not in trades_by_partition:
            execution_times_df = pd.DataFrame(columns=empty_cols)
            execution_times_by_partition[partition_key] = execution_times_df
            _save_execution_times(partition_key, execution_times_df, processed_dir)
            print(f"  {partition_key}: 0 qualifying sweep orders")
            continue

        trades_df = trades_by_partition[partition_key]
        orders_with_valid_trades = _filter_orders_with_valid_trades(qualifying_order_ids, trades_df)

        execution_times = []
        n_skipped_no_rest = 0
        for order_id, order_trades in orders_with_valid_trades.items():
            order_data = orders_df[orders_df[col.common.orderid] == order_id]
            exec_time = _extract_execution_time_dict(order_id, order_data, order_trades)
            if exec_time['rest_on_lit_quantity'] <= 0:
                n_skipped_no_rest += 1
                continue                                       # nothing rested → nothing to re-route
            execution_times.append(exec_time)

        execution_times_df = pd.DataFrame(execution_times) if execution_times else pd.DataFrame(columns=empty_cols)
        execution_times_by_partition[partition_key] = execution_times_df
        _save_execution_times(partition_key, execution_times_df, processed_dir)
        msg = f"  {partition_key}: {len(execution_times_df):,} qualifying sweep orders"
        if n_skipped_no_rest:
            msg += f" ({n_skipped_no_rest:,} skipped — no resting portion)"
        print(msg)

    return execution_times_by_partition


def load_partition_data(partition_key, processed_dir):
    """Load all necessary data for a partition including reference data."""
    date, security_code = partition_key.split('/')
    partition_dir = Path(processed_dir) / date / security_code
    date_dir = Path(processed_dir) / date
    
    partition_data = {}
    
    # ===== PARTITION-LEVEL DATA =====
    
    # Load orders_before_matching
    before_file = partition_dir / "orders_before_matching.parquet"
    if before_file.exists():
        partition_data['orders_before'] = safe_read_csv(before_file)

    # Load orders_after_matching
    after_file = partition_dir / "orders_after_matching.parquet"
    if after_file.exists():
        partition_data['orders_after'] = safe_read_csv(after_file)

    # Load last_execution_time
    exec_file = partition_dir / "last_execution_time.parquet"
    if exec_file.exists():
        partition_data['last_execution'] = safe_read_csv(exec_file)
    else:
        partition_data['last_execution'] = pd.DataFrame(columns=[
            'orderid', 'first_execution_time', 'last_execution_time',
            'rest_on_lit_quantity',
        ])

    # ===== REFERENCE DATA =====

    # Load NBBO (partition-specific)
    nbbo_file = partition_dir / "nbbo.parquet"
    if nbbo_file.exists():
        partition_data['nbbo'] = safe_read_csv(nbbo_file)
    else:
        partition_data['nbbo'] = pd.DataFrame()

    # Load session data (date-level)
    session_file = date_dir / "session.parquet"
    if session_file.exists():
        partition_data['session'] = safe_read_csv(session_file)
    else:
        partition_data['session'] = pd.DataFrame()

    # Load reference data (date-level)
    reference_file = date_dir / "reference.parquet"
    if reference_file.exists():
        partition_data['reference'] = safe_read_csv(reference_file)
    else:
        partition_data['reference'] = pd.DataFrame()

    # Load participants data (date-level)
    participants_file = date_dir / "participants.parquet"
    if participants_file.exists():
        partition_data['participants'] = safe_read_csv(participants_file)
    else:
        partition_data['participants'] = pd.DataFrame()
    
    return partition_data


# ============================================================================
# Section 7 - pipeline/sweep_simulator/_legacy.py (Stage 2)
# ============================================================================
"""
Sweep Simulator Module - Complete Implementation

Handles all sweep order matching simulation logic:
- Load and prepare sweep orders and incoming orders
- Simulate time-priority sweep matching algorithm
- Calculate midpoint prices from NBBO or fallback to bid/offer
- Apply mid-tick price improvement when configured
- Track sweep order utilization
- Generate match details and order summaries
- Handle order amendments and priority loss
- Use reference data for tick sizes and participant info
- Filter by order type (Limit/Market/Passive)
- Validate price limits
- Model execution delays
- Track sweep-to-sweep matches
- Session state filtering (IMPROVEMENT 1)
- Order book position tracking (IMPROVEMENT 3)
- Iceberg order support (IMPROVEMENT 4)
- Participant type analysis (IMPROVEMENT 6)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from config import col
import config as cfg
# (consolidated) from pipeline.reference_data import ReferenceDataLoader

# Constants
SWEEP_ORDER_TYPE = 2048
ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}  # ALL CP types, including sweep-to-sweep
ORDER_TYPE_COLUMN = 'exchangeordertype'

# Order type — only LIMIT is referenced from prep code as a default.
ORDERTYPE_LIMIT = 1

# Midtick — only NO is referenced from prep code as a default.
MIDTICK_NO = 2

# Change reason codes used by sweep-completion / priority-loss prep helpers.
CHANGEREASON_NEW_ORDER = 6
CHANGEREASON_MARKET_CONVERTED_AUCTION = 7
CHANGEREASON_MARKET_TO_LIMIT = 8
CHANGEREASON_UNDISCLOSED_TO_REGULAR = 39

# Session states gating Stage 1 prep (matching gates live in simulator.py).
MATCHING_SESSION_STATES = {'OPEN', 'CONTINUOUS'}


def _has_lost_priority(order_row):
    """
    Check if an order has lost priority due to amendment.
    
    IMPROVEMENT 3: Order Book Position Tracking
    
    Per dd.txt p.868:
    - orderBookPosition > 0 indicates priority loss
    - changereason=5 (updated by user) with certain changes loses priority
    - changereason=7,8,39 also indicate priority loss
    """
    order_book_position = order_row.get('orderbookposition', 0)
    if pd.isna(order_book_position):
        order_book_position = 0
    if order_book_position > 0:
        return True
    changereason = order_row.get('changereason', 0)
    if pd.isna(changereason):
        changereason = 0
    priority_loss_reasons = {
        CHANGEREASON_MARKET_CONVERTED_AUCTION,
        CHANGEREASON_MARKET_TO_LIMIT,
        CHANGEREASON_UNDISCLOSED_TO_REGULAR,
    }
    if changereason in priority_loss_reasons:
        return True
    return False


def _get_effective_timestamp(order_row):
    """Get effective timestamp for order matching."""
    if _has_lost_priority(order_row):
        time_changed = order_row.get('timechanged', order_row.get('timestamp', 0))
        if pd.isna(time_changed):
            time_changed = order_row.get('timestamp', 0)
        return int(time_changed)
    else:
        return int(order_row.get('timestamp', 0))


def load_and_prepare_orders(partition_data):
    """Load and prepare sweep orders and all matching-eligible orders for simulation."""
    sweep_orders = _prepare_sweep_orders(partition_data)
    all_orders = _prepare_all_orders_for_matching(partition_data)
    return sweep_orders, all_orders


def _prepare_sweep_orders(partition_data):
    """Prepare sweep orders (type 2048) from ONLY qualifying orders in last_execution."""
    orders_after = partition_data['orders_after']
    last_execution = partition_data['last_execution']
    sweep_orders = last_execution.copy()
    sweep_orders_after = orders_after[orders_after[ORDER_TYPE_COLUMN] == SWEEP_ORDER_TYPE].copy()
    sweep_orders = sweep_orders.merge(sweep_orders_after, on='orderid', how='inner')
    
    required_columns = [
        col.common.orderid, col.common.timestamp, col.common.sequence,
        col.common.side, col.orders.leaves_quantity, 'matched_quantity',
        'rest_on_lit_quantity',
        col.common.price, 'first_execution_time', 'last_execution_time',
        col.common.orderbookid, 'minimumquantity', 'singlefillminimumquantity',
        'crossingkey', col.orders.participant_id, 'midtick',
    ]
    
    # Optional columns with defaults
    optional_columns = {
        'changereason': CHANGEREASON_NEW_ORDER,
        'orderbookposition': 0,
        'timechanged': None,  # Will be filled with timestamp
        'preferenceonly': 0,
    }
    
    missing = set(required_columns) - set(sweep_orders.columns)
    if missing:
        raise ValueError(f"Missing required columns for sweep order simulation: {missing}")
    
    sweep_orders = sweep_orders[required_columns].copy()
    
    # Add optional columns with defaults
    for col_name, default_val in optional_columns.items():
        if col_name not in sweep_orders.columns:
            sweep_orders[col_name] = default_val
    
    sweep_orders[col.common.orderid] = sweep_orders[col.common.orderid].astype('int64')
    sweep_orders['minimumquantity'] = sweep_orders['minimumquantity'].fillna(0).astype('int64')
    sweep_orders['singlefillminimumquantity'] = sweep_orders['singlefillminimumquantity'].fillna(0).astype('int64')
    sweep_orders['crossingkey'] = sweep_orders['crossingkey'].fillna(0).astype('int64')
    sweep_orders[col.orders.participant_id] = sweep_orders[col.orders.participant_id].fillna(0).astype('int64')
    sweep_orders['midtick'] = sweep_orders['midtick'].fillna(MIDTICK_NO).astype('int64')
    sweep_orders['changereason'] = sweep_orders['changereason'].fillna(CHANGEREASON_NEW_ORDER).astype('int64')
    sweep_orders['orderbookposition'] = sweep_orders['orderbookposition'].fillna(0).astype('int64')
    sweep_orders['preferenceonly'] = sweep_orders['preferenceonly'].fillna(0).astype('int64')
    sweep_orders['timechanged'] = sweep_orders['timechanged'].fillna(sweep_orders['timestamp']).astype('int64')
    if cfg.USE_POLARS_TRANSFORMS:
        import polars as pl
        so_pl = pl.from_pandas(sweep_orders)
        so_pl = so_pl.with_columns(
            pl.when(
                (pl.col('orderbookposition') > 0) |
                pl.col('changereason').is_in([7, 8, 39])
            )
            .then(pl.col('timechanged').fill_null(pl.col('timestamp')))
            .otherwise(pl.col('timestamp'))
            .cast(pl.Int64)
            .alias('effective_timestamp')
        )
        sweep_orders = so_pl.to_pandas()
    else:
        result = sweep_orders.apply(_get_effective_timestamp, axis=1)
        if isinstance(result, pd.DataFrame):
            result = result.squeeze(axis=1) if result.shape[1] == 1 else pd.Series(dtype='int64')
        sweep_orders['effective_timestamp'] = result.astype('int64')
    sweep_orders['lost_priority'] = sweep_orders.apply(_has_lost_priority, axis=1)
    sweep_orders = sweep_orders.sort_values(['effective_timestamp', 'sequence']).reset_index(drop=True)

    return sweep_orders


def _prepare_all_orders_for_matching(partition_data):
    """Prepare ALL Centre Point orders for matching (including sweeps)."""
    orders_before = partition_data['orders_before']
    all_orders = orders_before[orders_before[ORDER_TYPE_COLUMN].isin(ELIGIBLE_MATCHING_ORDER_TYPES)].copy()
    
    # orderstatus is NOT used to filter the contra pool here.
    # Per the ASX DD spec, orderstatus=1 means "on book" and orderstatus=2 means
    # "not yet on book" — but new order submissions always arrive as orderstatus=2
    # (incoming, not yet handled). The orders_before_matching.csv now captures each
    # order at its changereason=1 (submission) event, so orderstatus is always 2
    # at that snapshot. Chronological filtering is done by the simulator via
    # timestamp comparison (contra must arrive before the sweep).

    # Passive orders (ordertype=4) cannot be the aggressive side, but sweeps are
    # always the aggressive side in this simulation — passive orders remain valid
    # as the resting/contra side, so they are kept in the matching pool.

    required_columns = [
        col.common.orderid, col.common.timestamp, col.common.sequence,
        col.common.side, col.common.quantity, col.common.orderbookid,
        col.orders.bid, col.orders.offer,
        col.orders.national_bid, col.orders.national_offer,
        'minimumquantity', 'singlefillminimumquantity', 'crossingkey',
        'participantid', 'midtick', 'timevalidity', 'price',
    ]
    
    # Optional columns with defaults
    optional_columns = {
        'changereason': CHANGEREASON_NEW_ORDER,
        'orderbookposition': 0,
        'timechanged': None,
        'ordertype': ORDERTYPE_LIMIT,
    }
    
    missing = set(required_columns) - set(all_orders.columns)
    if missing:
        raise ValueError(f"Missing required columns for matching orders: {missing}")
    
    all_orders = all_orders[required_columns].copy()
    
    # Add optional columns with defaults
    for col_name, default_val in optional_columns.items():
        if col_name not in all_orders.columns:
            all_orders[col_name] = default_val
    
    all_orders[col.common.orderid] = all_orders[col.common.orderid].astype('int64')
    all_orders['minimumquantity'] = all_orders['minimumquantity'].fillna(0).astype('int64')
    all_orders['singlefillminimumquantity'] = all_orders['singlefillminimumquantity'].fillna(0).astype('int64')
    all_orders['crossingkey'] = all_orders['crossingkey'].fillna(0).astype('int64')
    all_orders['participantid'] = all_orders['participantid'].fillna(0).astype('int64')
    all_orders['midtick'] = all_orders['midtick'].fillna(MIDTICK_NO).astype('int64')
    all_orders['changereason'] = all_orders['changereason'].fillna(CHANGEREASON_NEW_ORDER).astype('int64')
    all_orders['orderbookposition'] = all_orders['orderbookposition'].fillna(0).astype('int64')
    all_orders['timechanged'] = all_orders['timechanged'].fillna(all_orders['timestamp']).astype('int64')
    all_orders['timevalidity'] = all_orders['timevalidity'].fillna(1536).astype('int64')
    all_orders['ordertype'] = all_orders['ordertype'].fillna(ORDERTYPE_LIMIT).astype('int64')
    all_orders['price'] = all_orders['price'].fillna(0).astype('int64')
    if cfg.USE_POLARS_TRANSFORMS:
        import polars as pl
        ao_pl = pl.from_pandas(all_orders)
        ao_pl = ao_pl.with_columns(
            pl.when(
                (pl.col('orderbookposition') > 0) |
                pl.col('changereason').is_in([7, 8, 39])
            )
            .then(pl.col('timechanged').fill_null(pl.col('timestamp')))
            .otherwise(pl.col('timestamp'))
            .cast(pl.Int64)
            .alias('effective_timestamp')
        )
        all_orders = ao_pl.to_pandas()
    else:
        result = all_orders.apply(_get_effective_timestamp, axis=1)
        if isinstance(result, pd.DataFrame):
            result = result.squeeze(axis=1) if result.shape[1] == 1 else pd.Series(dtype='int64')
        all_orders['effective_timestamp'] = result.astype('int64')
    all_orders = all_orders.sort_values(['effective_timestamp', 'sequence']).reset_index(drop=True)

    return all_orders


def simulate_partition(partition_key, partition_data, reference_loader=None):
    """Simulate sweep matching for a partition."""
    try:
        sweep_orders, all_orders = load_and_prepare_orders(partition_data)
        
        if len(sweep_orders) == 0:
            print(f"  {partition_key}: No sweep orders, skipping")
            return None
        if len(all_orders) == 0:
            print(f"  {partition_key}: No matching orders, skipping")
            return None
        
        nbbo_data = partition_data.get('nbbo')
        if cfg.NBBO_SOURCE == 'EXTERNAL':
            if nbbo_data is None or len(nbbo_data) == 0:
                raise ValueError(f"NBBO_SOURCE is 'EXTERNAL' but no NBBO data found for partition {partition_key}")
        
        # Get reference data (tick sizes, price limits, participants)
        if reference_loader is None:
            processed_dir = Path(partition_data.get('processed_dir', 'data/processed'))
            reference_loader = ReferenceDataLoader(processed_dir)
        
        partition_dir = Path(partition_data.get('partition_dir', f'data/processed/{partition_key}'))
        orderbookid = sweep_orders[col.common.orderbookid].iloc[0] if len(sweep_orders) > 0 else None
        
        # IMPROVEMENT 2: Get tick size table from reference data
        tick_size = reference_loader.get_tick_size(str(partition_dir), orderbookid)
        tick_size_table = reference_loader.get_tick_size_table(orderbookid)
        
        # IMPROVEMENT 3: Get price limits
        price_limits = reference_loader.get_price_limits(orderbookid)
        
        # IMPROVEMENT 6: Get participant info
        reference_loader.load_participants()
        participants_dict = reference_loader.participants
        
        # IMPROVEMENT 1: Get session states
        session_states_df = partition_data.get('session_states')
        
        print(f"    Using tick size: {tick_size} (from reference data)")

        from simulator import simulate_sweep_matching_numpy
        results = simulate_sweep_matching_numpy(
            sweep_orders, all_orders, nbbo_data,
            nbbo_source=cfg.NBBO_SOURCE,
            tick_size_override=tick_size,
            tick_size_table=tick_size_table,
            price_limits=price_limits,
            participants_dict=participants_dict,
            session_states_df=session_states_df,
            partition_key=partition_key,
            progress_every=500,
        )
        
        num_matches = len(results['simulated_trades']) // 2 if len(results['simulated_trades']) > 0 else 0
        print(f"  {partition_key}: {num_matches:,} matches, {len(sweep_orders):,} sweep orders")
        
        amended_count = (sweep_orders['lost_priority'] == True).sum()
        print(f"  {partition_key}: {amended_count} sweep orders lost priority due to amendment")
        
        # Print order type distribution
        if 'ordertype' in all_orders.columns:
            ot_counts = all_orders['ordertype'].value_counts()
            print(f"    Order types: {dict(ot_counts)}")
        
        # Print match type breakdown
        if 'match_type' in results['simulated_trades'].columns:
            match_types = results['simulated_trades']['match_type'].value_counts()
            print(f"    Match types: {dict(match_types)}")

        # IMPROVEMENT 6: Print participant type breakdown
        if 'contra_participant_type' in results['simulated_trades'].columns:
            participant_types = results['simulated_trades']['contra_participant_type'].value_counts()
            print(f"    Participant types: {dict(participant_types)}")

        return results
    except ValueError as e:
        print(f"\n{'='*80}\nERROR: NBBO Configuration Issue for {partition_key}\n{'='*80}")
        print(f"{str(e)}\n{'='*80}\n")
        raise


def process_partitions_parallel_stage_2(partition_keys, processed_dir, outputs_dir, max_workers):
    """Process partitions in parallel for Stage 2 (simulation only)."""
    print(f"\nProcessing {len(partition_keys)} partitions with {max_workers} workers...")
    
    partition_results = {}
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit simulation jobs
        futures = {
            executor.submit(
                _process_partition_simulation_only,
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
                          f"{result.get('num_sweep_orders', 0):,} sweep orders, "
                          f"{result.get('num_matches', 0):,} matches")
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


def _process_partition_simulation_only(partition_key, processed_dir, outputs_dir):
    """Process single partition for Stage 2: simulation only."""
    try:
        partition_data = dp.load_partition_data(partition_key, processed_dir)
        
        if not partition_data or 'orders_before' not in partition_data:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No partition data found'
            }
        
        # Load NBBO
        partition_dir = fu.get_partition_dir(processed_dir, partition_key)
        nbbo_data = fu.load_nbbo(partition_dir)
        partition_data['nbbo'] = nbbo_data
        
        # Step 7: Simulate sweep matching
        sim_results = ss.simulate_partition(partition_key, partition_data)
        
        if not sim_results:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No simulation results'
            }
        
        # Save simulation results
        fu.save_simulation_results(sim_results, outputs_dir, partition_key)
        
        return {
            'partition_key': partition_key,
            'status': 'success',
            'num_sweep_orders': len(sim_results['order_summary']),
            'num_matches': len(sim_results['simulated_trades']) // 2
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
# Section 9 - CLI plumbing (bulk-mode entry only)
# ============================================================================
# Legacy per-(ticker,date) plumbing (parse_arguments, build_runtime_config,
# execute_pipeline_stages, _process_single_security, SecurityDiscovery, etc.)
# was deleted on 2026-04-28 — superseded by the bulk-mode `cli_multi()` below.
def setup_directories():
    """Create raw / processed / outputs / reports directories if they don't exist.

    Raw subdirs are auto-created so a fresh checkout doesn't error before the
    user has had a chance to drop input files into place. Run organize_raw.py
    if your CSVs are sitting flat in data/ rather than the proper subdirs.
    """
    for sub in ('orders', 'trades', 'nbbo', 'session', 'reference', 'participants'):
        Path(config.RAW_DIR / sub).mkdir(parents=True, exist_ok=True)
    Path(config.PROCESSED_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.REPORTS_DIR).mkdir(parents=True, exist_ok=True)


import sys as _sys
import argparse as _argparse
import config  # top-level config.py at repo root

# Self-aliases so legacy code's `dp.X` / `pp.X` / `ec.X` still resolve to the
# consolidated functions in this same module. (ec.* is dead code on the
# Stage 1+2 path; left in place for any Stage 3+ caller that's not used here.)
dp = _sys.modules[__name__]
pp = _sys.modules[__name__]
ec = _sys.modules[__name__]
fu = _sys.modules[__name__]   # was utils.file_utils
du = _sys.modules[__name__]   # was utils.data_utils
ss = _sys.modules[__name__]   # was pipeline.sweep_simulator


def cli_multi():
    """Bulk-ingest CLI: process every file under data/raw/{orders,trades}/ in one pass.

    --dates / --tickers / --orderbookids are all optional post-read filters.
    Absent → process everything found in raw. The natural groupby(date, orderbookid)
    downstream still partitions output into data/processed/{date}/{orderbookid}/.
    """
    parser = _argparse.ArgumentParser(
        prog="process.py",
        description="Stage 1+2: bulk ingest from data/raw/, partition by (date, orderbookid)."
    )
    parser.add_argument("--dates", default=None,
                        help="Optional comma-separated YYYYMMDD trade-date filter (post-read).")
    parser.add_argument("--tickers", default=None,
                        help="Optional comma-separated ticker filter (matches filename substring).")
    parser.add_argument("--orderbookids", default=None,
                        help="Optional comma-separated orderbookid filter (parquet predicate-pushdown).")
    parser.add_argument("--workers", type=int, default=None,
                        help="Worker pool size for Stage 2 (default: auto).")
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
        print(f"[process] No orders files found in {config.RAW_FOLDERS['orders']}.", file=_sys.stderr)
        _sys.exit(2)

    print(f"[process] Discovered {len(orders_files)} orders + {len(trades_files)} trades file(s).")
    if dates_filter:        print(f"[process] dates filter (post-read): {dates_filter}")
    if orderbookids_filter: print(f"[process] orderbookids filter (push-down): {sorted(orderbookids_filter)}")
    if tickers_hint:        print(f"[process] tickers hint (filename substring): {tickers_hint}")

    setup_directories()

    print("\n" + "=" * 80)
    print("STAGE 1: DATA EXTRACTION & PREPARATION (Steps 1-6)")
    print("=" * 80)

    orders_by_partition = extract_orders(
        orders_files, config.PROCESSED_DIR,
        config.CENTRE_POINT_ORDER_TYPES, config.CHUNK_SIZE,
        orderbookids_filter=orderbookids_filter,
        dates_filter=dates_filter,
    )
    if not orders_by_partition:
        print("\nNo Centre Point orders found. Exiting.")
        return

    trades_by_partition = extract_trades(
        trades_files, orders_by_partition, config.PROCESSED_DIR, config.CHUNK_SIZE,
    )

    process_reference_data(config.RAW_FOLDERS, config.PROCESSED_DIR, orders_by_partition)
    get_orders_state(orders_by_partition, config.PROCESSED_DIR)
    extract_last_execution_times(orders_by_partition, trades_by_partition, config.PROCESSED_DIR)

    partition_keys = list(orders_by_partition.keys())
    workers = args.workers or config.MAX_PARALLEL_WORKERS

    print("\n" + "=" * 80)
    print(f"STAGE 2: SIMULATION (Step 7) — {len(partition_keys)} partition(s), {workers} worker(s)")
    print("=" * 80)
    process_partitions_parallel_stage_2(partition_keys, config.PROCESSED_DIR, config.OUTPUTS_DIR, workers)

    print(f"\n✓ Stage 1+2 complete for {len(partition_keys)} partition(s)")


if __name__ == '__main__':
    cli_multi()
