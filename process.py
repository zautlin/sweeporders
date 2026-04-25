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
"""
Column normalization utilities for standardizing DataFrame column names.

This module handles the transformation of raw CSV column names to standardized
names used throughout the pipeline after Stage 1.
"""

import pandas as pd

# Maps raw/alternative column names to standardized names for processed files
COLUMN_NORMALIZATION_MAP = {
    'orders': {
        'order_id': 'orderid',
        'security_code': 'orderbookid',
        'securitycode': 'orderbookid',
        'SecurityCode': 'orderbookid',
        'totalmatchedquantity': 'matched_quantity',
    },
    'trades': {
        'order_id': 'orderid',
        'security_code': 'orderbookid',
        'securitycode': 'orderbookid',
    },
    'nbbo': {
        'security_code': 'orderbookid',
        'securitycode': 'orderbookid',
        'bidprice': 'bid',
        'offerprice': 'offer',
        'bidquantity': 'bid_quantity',
        'offerquantity': 'offer_quantity',
    },
    'session': {
        'OrderBookId': 'orderbookid',
        'TradeDate': 'timestamp',
    },
    'reference': {
        'Id': 'orderbookid',
        'TradeDate': 'timestamp',
    },
    'participants': {
        'TradeDate': 'timestamp',
    }
}


def normalize_column_names(df: pd.DataFrame, data_type: str) -> pd.DataFrame:
    """Normalize DataFrame columns to standard names used in processed files."""
    if data_type not in COLUMN_NORMALIZATION_MAP:
        return df
    
    norm_map = COLUMN_NORMALIZATION_MAP[data_type]
    rename_dict = {col: norm_map[col] for col in df.columns if col in norm_map}
    
    return df.rename(columns=rename_dict) if rename_dict else df


def validate_columns(df: pd.DataFrame, required_columns: list, context: str = "") -> bool:
    """Validate that required columns exist in DataFrame."""
    missing = set(required_columns) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {context}: {missing}")
    return True

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


def standardize_sides(df, side_col='side'):
    """Ensure side column uses standard values (1=Buy, 2=Sell)."""
    if side_col not in df.columns:
        return df
    
    df = df.copy()
    
    # Handle string values
    df[side_col] = df[side_col].replace({
        'Buy': 1,
        'Sell': 2,
        'BUY': 1,
        'SELL': 2,
        'buy': 1,
        'sell': 2
    })
    
    # Ensure numeric
    df[side_col] = pd.to_numeric(df[side_col], errors='coerce')
    
    return df


def get_side_decoded(side):
    """Convert numeric side to string (1 → 'Buy', 2 → 'Sell')."""
    return 'Buy' if side == 1 else 'Sell' if side == 2 else 'Unknown'


def ensure_orderid_int64(df):
    """Ensure orderid column is int64 type."""
    if 'orderid' in df.columns:
        df = df.copy()
        df['orderid'] = df['orderid'].astype('int64')
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


def polars_to_pandas(df: pl.DataFrame):
    """Convert Polars → pandas at StatisticsEngine / normalize_column_names boundaries."""
    return df.to_pandas()

# ============================================================================
# Section 4 - utils/file_utils.py
# ============================================================================
"""File I/O utilities for pipeline operations."""

import pandas as pd
import polars as pl
from pathlib import Path

import sys
import os
# (lean port) original line `sys.path.insert(0, dirname(dirname(__file__)))` removed —
# legacy hack from src/-layout days. The flat layout has config.py next to this
# script, so Python's normal resolution finds it without help.
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


def safe_read_csv(filepath, required=True, compression='infer', **kwargs):
    """Read tabular data with format auto-detection (Parquet or CSV).

    Dispatches on file extension: .parquet → pd.read_parquet, else CSV.
    Name kept for backward compatibility with legacy call sites.
    """
    filepath = Path(filepath)

    if not filepath.exists():
        if required:
            raise FileNotFoundError(f"Required file not found: {filepath}")
        return None

    try:
        if filepath.suffix == '.parquet':
            kwargs.pop('compression', None)
            return pd.read_parquet(filepath, **kwargs)

        if config.USE_DUCKDB_IO:
            rel = get_conn().execute(f"SELECT * FROM read_csv_auto('{filepath}')")
            return duck_to_polars(rel).to_pandas()

        return pd.read_csv(filepath, compression=compression, **kwargs)
    except pd.errors.EmptyDataError:
        return None
    except Exception as e:
        raise IOError(f"Error reading {filepath}: {e}")


def safe_write_csv(df, filepath, compression=None, create_dirs=True, **kwargs):
    """Write tabular data with format auto-detection (Parquet or CSV).

    Dispatches on file extension: .parquet → zstd parquet, else CSV.
    Accepts both pandas and Polars DataFrames.
    """
    filepath = Path(filepath)

    if create_dirs:
        filepath.parent.mkdir(parents=True, exist_ok=True)

    try:
        if filepath.suffix == '.parquet':
            if isinstance(df, pl.DataFrame):
                df.write_parquet(filepath, compression='zstd')
            else:
                df.to_parquet(filepath, compression='zstd', index=False)
            return

        if isinstance(df, pl.DataFrame):
            df.write_csv(filepath)
        else:
            df.to_csv(filepath, compression=compression, index=False, **kwargs)
    except Exception as e:
        raise IOError(f"Error writing {filepath}: {e}")


def query_partitions(base_dir, filename, where_sql="") -> pl.DataFrame:
    """Query one file across every date/orderbookid partition in a single DuckDB pass.

    Dispatches the reader on filename extension (.parquet → read_parquet, else read_csv_auto).

    Usage::

        df = query_partitions(PROCESSED_DIR, 'orders_before_matching.parquet',
                              "WHERE exchangeordertype = 2048")
        df = query_partitions(OUTPUTS_DIR, 'real_trade_metrics.parquet')
        df = query_partitions(PROCESSED_DIR, 'cp_trades_matched.parquet')
    """
    glob_pattern = str(Path(base_dir) / '*' / '*' / filename)
    reader = "read_parquet" if filename.endswith(".parquet") else "read_csv_auto"
    conn = get_conn()
    sql = f"SELECT * FROM {reader}('{glob_pattern}', union_by_name=True) {where_sql}"
    return duck_to_polars(conn.execute(sql))


def load_orders_before(partition_dir):
    """Load orders_before_matching parquet from partition directory."""
    filepath = Path(partition_dir) / "orders_before_matching.parquet"
    return safe_read_csv(filepath, required=False)


def load_orders_after(partition_dir):
    """Load orders_after_matching parquet from partition directory."""
    filepath = Path(partition_dir) / "orders_after_matching.parquet"
    return safe_read_csv(filepath, required=False)


def load_trades_matched(partition_dir):
    """Load cp_trades_matched parquet from partition directory."""
    filepath = Path(partition_dir) / "cp_trades_matched.parquet"
    return safe_read_csv(filepath, required=False)


def load_trades_aggregated(partition_dir):
    """Load cp_trades_aggregated parquet from partition directory."""
    filepath = Path(partition_dir) / "cp_trades_aggregated.parquet"
    return safe_read_csv(filepath, required=False)


def load_last_execution(partition_dir):
    """Load last_execution_time parquet from partition directory."""
    filepath = Path(partition_dir) / "last_execution_time.parquet"
    return safe_read_csv(filepath, required=False)


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


def save_resting_simulation_results(sim_results, output_dir, partition_key):
    """Save Phase 2 resting simulation outputs alongside Phase 1 outputs."""
    processed_dir = Path(output_dir).parent / 'processed'
    partition_processed_dir = processed_dir / partition_key
    partition_processed_dir.mkdir(parents=True, exist_ok=True)

    resting_trades = sim_results.get('resting_trades')
    if resting_trades is not None and len(resting_trades) > 0:
        safe_write_csv(
            resting_trades,
            partition_processed_dir / 'cp_trades_simulation_resting.parquet',
            create_dirs=False,
        )

    resting_summary = sim_results.get('resting_summary')
    if resting_summary is not None and len(resting_summary) > 0:
        partition_output_dir = Path(output_dir) / partition_key
        partition_output_dir.mkdir(parents=True, exist_ok=True)
        safe_write_csv(
            resting_summary,
            partition_output_dir / 'resting_order_summary.parquet',
            create_dirs=False,
        )


def save_orders_with_metrics(orders_with_metrics, output_dir, partition_key):
    """Save orders with simulated metrics."""
    partition_output_dir = Path(output_dir) / partition_key
    safe_write_csv(
        orders_with_metrics,
        partition_output_dir / 'orders_with_simulated_metrics.parquet'
    )


def save_trade_comparison(comparison_df, accuracy_df, output_dir, partition_key):
    """Save trade-level comparison results."""
    partition_output_dir = Path(output_dir) / partition_key
    
    if comparison_df is not None and len(comparison_df) > 0:
        safe_write_csv(
            comparison_df,
            partition_output_dir / 'trade_level_comparison.parquet'
        )
    
    if accuracy_df is not None and len(accuracy_df) > 0:
        safe_write_csv(
            accuracy_df,
            partition_output_dir / 'trade_accuracy_summary.parquet'
        )


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


def load_trade_metrics(output_dir, partition_key):
    """Load pre-calculated trade metrics from Stage 2 output directory."""
    partition_output_dir = Path(output_dir) / partition_key
    
    real_metrics_path = partition_output_dir / 'real_trade_metrics.parquet'
    sim_metrics_path = partition_output_dir / 'simulated_trade_metrics.parquet'
    
    real_metrics_df = safe_read_csv(real_metrics_path, required=False)
    sim_metrics_df = safe_read_csv(sim_metrics_path, required=False)
    
    return real_metrics_df, sim_metrics_df


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
import config as cfg


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
        participants = pd.read_parquet(participants_file)
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

        reference = pd.read_parquet(reference_file)
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
        nbbo = pd.read_parquet(nbbo_file)
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
        orders = pd.read_parquet(orders_file).head(1000)
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

        reference = pd.read_parquet(reference_file)
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


def calculate_tick_size_from_spread(spread):
    """Calculate tick size from observed spread."""
    if spread <= 0:
        return 10
    tick_size = max(1, spread // 2)
    if tick_size <= 1:
        return 1
    elif tick_size <= 5:
        return 5
    else:
        return 10


_reference_loader = None


def get_reference_loader(processed_dir=None):
    """Get or create global reference data loader."""
    global _reference_loader
    if _reference_loader is None and processed_dir:
        _reference_loader = ReferenceDataLoader(processed_dir)
    return _reference_loader

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
    session_dir = PROJECT_ROOT / 'data' / 'raw' / 'session'
    session_pq = session_dir / f'{date_str}_session.parquet'
    session_csv = session_dir / f'{date_str}_session.csv'
    session_file = session_pq if session_pq.exists() else session_csv

    if not session_file.exists():
        print(f"  Warning: Session file not found: {session_file}")
        return None

    session_df = pd.read_parquet(session_file) if session_file.suffix == '.parquet' else pd.read_csv(session_file)
    
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
        f = Path(file)
        df = pd.read_parquet(f) if f.suffix == '.parquet' else pd.read_csv(f)
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
    
    df = add_date_column(df, timestamp_col)
    return _partition_by_date_security_and_save(df, orders_by_partition, processed_dir, "nbbo.parquet", col.common.date, security_col)


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
        if fp.suffix == '.parquet':
            import pyarrow.parquet as _pq
            total_rows += _pq.ParquetFile(fp).metadata.num_rows
            filters = [(col.orders.order_type, 'in', list(order_types))]
            if orderbookids_filter:
                filters.append((col.orders.security_code, 'in', list(orderbookids_filter)))
            chunk = pd.read_parquet(fp, filters=filters)
            if len(chunk) > 0:
                frames.append(chunk)
        elif _cfg.USE_DUCKDB_IO:
            import polars as pl
            conn = get_conn()
            types_sql = ','.join(str(t) for t in order_types)
            where = f"{col.orders.order_type} IN ({types_sql})"
            if orderbookids_filter:
                obid_sql = ','.join(str(x) for x in orderbookids_filter)
                where += f" AND {col.orders.security_code} IN ({obid_sql})"
            orders_pl = duck_to_polars(conn.execute(f"""
                SELECT * FROM read_csv_auto('{fp}')
                WHERE {where}
            """))
            total_rows += conn.execute(f"SELECT COUNT(*) FROM read_csv_auto('{fp}')").fetchone()[0]
            chunk = orders_pl.to_pandas()
            if len(chunk) > 0:
                frames.append(chunk)
        else:
            for sub in pd.read_csv(fp, chunksize=chunk_size, low_memory=False):
                total_rows += len(sub)
                f = sub[sub[col.orders.order_type].isin(order_types)]
                if orderbookids_filter:
                    f = f[f[col.orders.security_code].isin(orderbookids_filter)]
                if len(f) > 0:
                    frames.append(f.copy())

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
    for (date, security_code_val), group_df in orders.groupby(['date', col.orders.security_code]):
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
        if fp.suffix == '.parquet':
            import pyarrow.parquet as _pq
            total_rows += _pq.ParquetFile(fp).metadata.num_rows
            trades = pd.read_parquet(fp)
            matched = trades[trades[col.trades.order_id].isin(all_order_ids)].copy()
            if len(matched) > 0:
                frames.append(matched)
        elif _cfg.USE_DUCKDB_IO:
            import polars as pl
            conn = get_conn()
            conn.execute("CREATE OR REPLACE TEMP TABLE _target_ids (orderid BIGINT)")
            conn.executemany("INSERT INTO _target_ids VALUES (?)", [(int(i),) for i in all_order_ids])
            trades_pl = duck_to_polars(conn.execute(f"""
                SELECT t.* FROM read_csv_auto('{fp}') t
                JOIN _target_ids i ON t.{col.trades.order_id} = i.orderid
            """))
            total_rows += conn.execute(f"SELECT COUNT(*) FROM read_csv_auto('{fp}')").fetchone()[0]
            chunk = trades_pl.to_pandas()
            if len(chunk) > 0:
                frames.append(chunk)
        else:
            for sub in pd.read_csv(fp, chunksize=chunk_size, low_memory=False):
                total_rows += len(sub)
                matched_chunk = sub[sub[col.trades.order_id].isin(all_order_ids)].copy()
                if len(matched_chunk) > 0:
                    frames.append(matched_chunk)

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


def aggregate_trades(orders_by_partition, trades_by_partition, processed_dir):
    """Aggregate trades by order_id per partition."""
    print(f"\n[3/11] Aggregating trades by order...")
    
    trades_agg_by_partition = {}
    
    for partition_key, trades_df in trades_by_partition.items():
        if len(trades_df) == 0:
            continue
        
        if _cfg.USE_DUCKDB_IO:
            import polars as pl
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
        
        partition_file = partition_dir / "cp_trades_aggregated.parquet"
        safe_write_csv(trades_agg, partition_file, create_dirs=False)
        
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
        partition_data['orders_before'] = pd.read_parquet(before_file)

    # Load orders_after_matching
    after_file = partition_dir / "orders_after_matching.parquet"
    if after_file.exists():
        partition_data['orders_after'] = pd.read_parquet(after_file)

    # Load last_execution_time
    exec_file = partition_dir / "last_execution_time.parquet"
    if exec_file.exists():
        partition_data['last_execution'] = pd.read_parquet(exec_file)
    else:
        partition_data['last_execution'] = pd.DataFrame(columns=['orderid', 'first_execution_time', 'last_execution_time'])

    # ===== REFERENCE DATA =====

    # Load NBBO (partition-specific)
    nbbo_file = partition_dir / "nbbo.parquet"
    if nbbo_file.exists():
        partition_data['nbbo'] = pd.read_parquet(nbbo_file)
    else:
        partition_data['nbbo'] = pd.DataFrame()

    # Load session data (date-level)
    session_file = date_dir / "session.parquet"
    if session_file.exists():
        partition_data['session'] = pd.read_parquet(session_file)
    else:
        partition_data['session'] = pd.DataFrame()

    # Load reference data (date-level)
    reference_file = date_dir / "reference.parquet"
    if reference_file.exists():
        partition_data['reference'] = pd.read_parquet(reference_file)
    else:
        partition_data['reference'] = pd.DataFrame()

    # Load participants data (date-level)
    participants_file = date_dir / "participants.parquet"
    if participants_file.exists():
        partition_data['participants'] = pd.read_parquet(participants_file)
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
        after_file = partition_dir / "orders_after_matching.parquet"

        if not after_file.exists():
            continue

        orders_after = pd.read_parquet(after_file)
        
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

import heapq
import pandas as pd
import numpy as np
from pathlib import Path
from config import col
import config as cfg
# (consolidated) from pipeline.reference_data import ReferenceDataLoader

# Constants
SWEEP_ORDER_TYPE = 2048
ELIGIBLE_MATCHING_ORDER_TYPES = {64, 256, 2048, 4096, 4098}  # ALL CP types, including sweep-to-sweep
ORDER_TYPE_COLUMN = 'exchangeordertype'

# Order type constants (dd.txt p.924)
ORDERTYPE_LIMIT = 1
ORDERTYPE_MARKET = 2
ORDERTYPE_MTL = 3  # Market-to-Limit
ORDERTYPE_PASSIVE = 4

# Midtick field values (per dd.txt spec p.1045)
MIDTICK_UNDEFINED = 0
MIDTICK_YES = 1
MIDTICK_NO = 2
MIDTICK_DARK_EXEC = 3
MIDTICK_DARK_WITH_MIDTICK = 4
MIDTICK_ANY_PRICE_BLOCK = 5
MIDTICK_ANY_PRICE_BLOCK_WITH_MIDTICK = 6

# ASX deal source codes (per dd.txt §1144-1187)
DEALSOURCE_CONTINUOUS        = 1   # Two orders matched in continuous matching (lit)
DEALSOURCE_CENTREPOINT       = 47  # Centre Point dark pool match
DEALSOURCE_PREFERENCE        = 46  # Centre Point preference-matched
DEALSOURCE_BLOCK             = 50  # Centre Point Any Price Block trade
DEALSOURCE_PREFERENCE_BLOCK  = 51  # Preference Any Price Block trade

ORDERTYPE_BLOCK_LIMIT = 4096  # Centre Point Block Limit order type

# Sentinel value used by ASX for unavailable int64 fields
INT64_SENTINEL = -9223372036854775808

# Change reason codes (per dd.txt spec p.941-954)
CHANGEREASON_UNDEFINED = 0
CHANGEREASON_CANCELED_BY_TRADER = 1
CHANGEREASON_TRADED = 3
CHANGEREASON_INACTIVATED_CONNECTION = 4
CHANGEREASON_UPDATED_BY_USER = 5
CHANGEREASON_NEW_ORDER = 6
CHANGEREASON_MARKET_CONVERTED_AUCTION = 7
CHANGEREASON_MARKET_TO_LIMIT = 8
CHANGEREASON_CANCELED_BY_SYSTEM = 9
CHANGEREASON_UNDISCLOSED_TO_REGULAR = 39

# Session states (bi.txt Section 8)
MATCHING_SESSION_STATES = {'OPEN', 'CONTINUOUS'}
NON_MATCHING_SESSION_STATES = {'PRE_OPEN', 'AUCTION', 'POST_CLOSE', 'CLOSED', 'PRE_CSPA', 'CSPA', 'ADJUST', 'ADJUST_ON', 'PURGE_ORDERS', 'SYSTEM_MAINTENANCE'}


def get_midpoint(nbbo_data, timestamp, orderbookid, fallback_bid, fallback_offer):
    """Get midpoint price at given timestamp."""
    if nbbo_data is not None and len(nbbo_data) > 0:
        midpoint = _get_midpoint_from_nbbo(nbbo_data, timestamp, orderbookid)
        if midpoint is not None:
            return midpoint
    if fallback_bid is not None and fallback_offer is not None:
        if pd.notna(fallback_bid) and pd.notna(fallback_offer):
            return (fallback_bid + fallback_offer) / 2.0
    return None


def _get_midpoint_from_nbbo(nbbo_data, timestamp, orderbookid):
    """Get midpoint from NBBO data (most recent quote before timestamp)."""
    if nbbo_data is None:
        return None
    valid_quotes = nbbo_data[
        (nbbo_data[col.common.orderbookid] == orderbookid) &
        (nbbo_data[col.common.timestamp] <= timestamp)
    ]
    if len(valid_quotes) == 0:
        return None
    latest = valid_quotes.iloc[-1]
    midpoint = (latest[col.common.bid] + latest[col.common.offer]) / 2.0
    return midpoint


def _get_nbbo_at_timestamp(nbbo_data, timestamp, orderbookid):
    """Get NBBO bid/offer/timestamp at or before given timestamp."""
    if nbbo_data is None or len(nbbo_data) == 0:
        return 0, 0, 0
    relevant_nbbo = nbbo_data[
        (nbbo_data[col.common.orderbookid] == orderbookid) &
        (nbbo_data[col.common.timestamp] <= timestamp)
    ]
    if len(relevant_nbbo) == 0:
        return 0, 0, 0
    latest = relevant_nbbo.iloc[-1]
    bid = latest.get('bid', 0)
    offer = latest.get('offer', 0)
    nbbo_ts = latest[col.common.timestamp]
    return int(bid), int(offer), int(nbbo_ts)


def _calculate_tick_size(nbbo_bid, nbbo_offer, tick_size_override=None, tick_size_table=None, price=None):
    """
    Calculate tick size from NBBO spread or reference data.
    
    Priority:
    1. Use override if provided
    2. Use tick size table from reference data (price-dependent)
    3. Estimate from spread
    """
    # Use override if provided
    if tick_size_override is not None and tick_size_override > 0:
        return tick_size_override
    
    # Use tick size table if available (price-dependent)
    if tick_size_table is not None and price is not None:
        for ts in tick_size_table:
            if ts['lower_limit'] <= price <= ts['upper_limit']:
                return ts['tick_size']
    
    # Fallback: estimate from spread
    spread = nbbo_offer - nbbo_bid
    if spread <= 0:
        return 10
    if spread <= 20:
        return max(1, spread // 2)
    elif spread <= 100:
        return max(5, spread // 10)
    else:
        return max(10, spread // 20)


def _apply_midtick_improvement(midpoint, nbbo_bid, nbbo_offer, side, midtick_value, tick_size_override=None, tick_size_table=None, price=None):
    """Apply mid-tick price improvement if configured."""
    if midtick_value not in [MIDTICK_YES, MIDTICK_DARK_WITH_MIDTICK]:
        return midpoint
    
    tick_size = _calculate_tick_size(nbbo_bid, nbbo_offer, tick_size_override, tick_size_table, price)
    half_tick = tick_size / 2.0
    
    if side == 1:  # BUY - better price is LOWER
        improved_price = midpoint - half_tick
        improved_price = max(improved_price, nbbo_bid)
    else:  # SELL - better price is HIGHER
        improved_price = midpoint + half_tick
        improved_price = min(improved_price, nbbo_offer)
    
    return improved_price


def _add_execution_delay(timestamp_ns, delay_mean_ms=5.0, delay_std_ms=2.0):
    """
    Add realistic execution delay to timestamp.
    
    Models latency as log-normal distribution:
    - Mean: ~5ms (order entry + matching + broadcast)
    - Std: ~2ms
    """
    delay_ms = np.random.lognormal(mean=np.log(delay_mean_ms), sigma=0.5)
    delay_ns = int(delay_ms * 1_000_000)
    return timestamp_ns + delay_ns


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


def _is_price_within_limits(price, price_limits):
    """Check if price is within acceptable limits."""
    if price_limits is None:
        return True
    lower = price_limits.get('lower_limit', 0)
    upper = price_limits.get('upper_limit', float('inf'))
    return lower <= price <= upper


def _validate_order_price_limit(order, execution_price, sweep_side):
    """
    Validate execution price against order's price limit.
    
    Returns True if execution is allowed, False if price violates limit.
    """
    order_type = int(order.get('ordertype', ORDERTYPE_LIMIT))
    order_price = order.get('price', 0)
    
    if order_type == ORDERTYPE_LIMIT:
        # Limit order: must execute at limit or better
        if sweep_side == 1:  # BUY sweep against SELL limit
            # Contra is selling, their limit is minimum acceptable
            if execution_price < order_price:
                return False
        else:  # SELL sweep against BUY limit
            # Contra is buying, their limit is maximum acceptable
            if execution_price > order_price:
                return False
    elif order_type == ORDERTYPE_MARKET:
        # Market order: no price limit
        pass
    elif order_type == ORDERTYPE_MTL:
        # Market-to-Limit: if already filled, use first fill price as limit
        if order.get('matched_quantity', 0) > 0:
            first_fill_price = order.get('first_fill_price', order_price)
            if sweep_side == 1:  # BUY sweep
                if execution_price < first_fill_price:
                    return False
            else:  # SELL sweep
                if execution_price > first_fill_price:
                    return False
    
    return True


def _is_valid_trading_session(timestamp_ns, session_states_df):
    """
    IMPROVEMENT 1: Session State Filtering
    
    Check if timestamp falls within a valid trading session.
    
    Per bi.txt Section 8:
    - OPEN/CONTINUOUS: Matching allowed
    - PRE_OPEN, AUCTION, POST_CLOSE, CLOSED: No continuous matching
    
    Args:
        timestamp_ns: Timestamp in nanoseconds
        session_states_df: DataFrame with session state changes
    
    Returns:
        bool: True if in valid trading session
    """
    if session_states_df is None or len(session_states_df) == 0:
        return True  # No session data, assume valid
    
    # Find the session state at this timestamp
    session_before = session_states_df[
        session_states_df['timestamp'] <= timestamp_ns
    ]
    
    if len(session_before) == 0:
        return True  # Before any session data, assume valid
    
    current_session = session_before.iloc[-1]
    session_state = current_session.get('session_state', 'OPEN')
    
    # Check if session allows continuous matching
    return session_state in MATCHING_SESSION_STATES


def _get_iceberg_available_qty(order, slice_consumed=0):
    """
    IMPROVEMENT 4: Iceberg Order Support

    Get available quantity from the current display slice of an iceberg order.

    Per bi.txt §27: undisclosed-quantity orders show only `display_quantity` in
    the book.  Each time the displayed slice is fully consumed the order is
    repositioned to the back of the queue and a fresh slice of `display_quantity`
    (or remaining total, whichever is smaller) becomes visible.

    Args:
        order:          Order DataFrame row.
        slice_consumed: How many units of the current display slice have already
                        been matched (tracked externally per order_id).

    Returns:
        int: Units available from the current display slice.
             Returns the full remaining quantity for non-iceberg orders.
    """
    display_qty = order.get('display_quantity', None)
    if display_qty is None or pd.isna(display_qty):
        # Not an iceberg — no slice cap.
        return int(order.get('quantity', order.get('leaves_quantity', 0)))
    return max(0, int(display_qty) - int(slice_consumed))


def _get_participant_type(participant_id, participants_dict):
    """
    IMPROVEMENT 6: Participant Type Analysis
    
    Get participant type (Broker, Market Maker, etc.)
    
    Args:
        participant_id: Participant ID
        participants_dict: Dictionary of participant info
    
    Returns:
        str: Participant type
    """
    if participants_dict is None:
        return 'Unknown'
    
    participant_info = participants_dict.get(participant_id, {})
    return participant_info.get('ParticipantType', 'Unknown')


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
        col.common.price, 'first_execution_time', 'last_execution_time',
        col.common.orderbookid, 'minimumquantity', 'singlefillminimumquantity',
        'crossingkey', col.orders.participant_id, 'midtick',
    ]
    
    # Optional columns with defaults
    optional_columns = {
        'changereason': CHANGEREASON_NEW_ORDER,
        'orderbookposition': 0,
        'timechanged': None,  # Will be filled with timestamp
        'display_quantity': None,  # Iceberg display quantity
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
        'display_quantity': None,  # Iceberg display quantity
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
        
        results = simulate_sweep_matching(
            sweep_orders, all_orders, nbbo_data,
            tick_size_override=tick_size,
            tick_size_table=tick_size_table,
            price_limits=price_limits,
            participants_dict=participants_dict,
            session_states_df=session_states_df
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

        # ── Phase 2: Resting simulation (controlled by config flags) ─────────
        if cfg.SIMULATE_RESTING_PHASE:
            sweep_orders_prepared, all_orders_prepared = load_and_prepare_orders(partition_data)
            remainder_df = build_remainder_df(
                sweep_orders_prepared, results['sweep_usage']
            )
            if len(remainder_df) > 0:
                print(f"  {partition_key}: Phase 2 — {len(remainder_df)} orders with remaining qty")
                lit_orders_raw = partition_data.get('lit_orders_raw')
                resting_results = simulate_resting_phase(
                    sweep_orders=sweep_orders_prepared,
                    remainder_df=remainder_df,
                    all_cp_orders=all_orders_prepared,
                    lit_orders_raw=lit_orders_raw,
                    nbbo_data=nbbo_data,
                    session_states_df=session_states_df,
                    tick_size_override=tick_size,
                    tick_size_table=tick_size_table,
                    participants_dict=participants_dict,
                )
                results['resting_trades']  = resting_results['resting_trades']
                results['resting_summary'] = resting_results['resting_summary']
                n_resting = len(resting_results['resting_trades']) // 2
                print(f"  {partition_key}: Phase 2 — {n_resting} resting matches")
            else:
                print(f"  {partition_key}: Phase 2 — no remaining qty after Phase 1, skipping")
                results['resting_trades']  = pd.DataFrame()
                results['resting_summary'] = pd.DataFrame()

        return results
    except ValueError as e:
        print(f"\n{'='*80}\nERROR: NBBO Configuration Issue for {partition_key}\n{'='*80}")
        print(f"{str(e)}\n{'='*80}\n")
        raise


def simulate_sweep_matching(sweep_orders, all_orders, nbbo_data, nbbo_source=None, 
                           tick_size_override=None, tick_size_table=None, price_limits=None,
                           participants_dict=None, session_states_df=None):
    """
    Simulate sweep matching with all improvements:
    - Order type filtering
    - Tick size from reference data
    - Price limit validation
    - NBBO timing at match time
    - Execution delay modeling
    - Sweep-to-sweep tracking
    - Session state filtering (IMPROVEMENT 1)
    - Order book position tracking (IMPROVEMENT 3)
    - Iceberg order support (IMPROVEMENT 4)
    - Participant type analysis (IMPROVEMENT 6)
    """
    if nbbo_source is None:
        nbbo_source = cfg.NBBO_SOURCE
    if nbbo_source not in ['INTERNAL', 'EXTERNAL']:
        raise ValueError(f"Invalid NBBO_SOURCE: '{nbbo_source}'")
    
    simulated_trades = []
    sweep_summaries = []
    sweep_usage = {int(orderid): {'matched_quantity': 0, 'num_matches': 0} 
                   for orderid in sweep_orders[col.common.orderid].values}
    order_remaining = {int(orderid): qty for orderid, qty in
                      zip(all_orders[col.common.orderid].values, all_orders[col.common.quantity].values)}
    # Per-order count of units consumed from the current display slice (iceberg tracking).
    iceberg_slice_consumed: dict = {}
    all_orders_indexed = all_orders.set_index('orderid')
    
    row_counter = 1
    match_counter = 0
    base_matchgroupid = 7904794000999000001
    
    if len(sweep_orders) > 0:
        first_timestamp = sweep_orders[col.common.timestamp].iloc[0]
        tradedate = pd.to_datetime(first_timestamp, unit='ns').strftime('%Y-%m-%d')
    else:
        tradedate = None
    
    if nbbo_source == 'EXTERNAL':
        if nbbo_data is None or len(nbbo_data) == 0:
            raise ValueError("NBBO_SOURCE is 'EXTERNAL' but no NBBO data available")
        nbbo_sorted = nbbo_data.sort_values('timestamp').reset_index(drop=True)
        print(f"    Using NBBO source: EXTERNAL ({len(nbbo_data):,} snapshots)")
        # Pre-extract numpy arrays for fast NBBO lookup inside inner loop
        _nbbo_ts  = nbbo_sorted['timestamp'].to_numpy()
        _nbbo_bid = nbbo_sorted[col.orders.bid].to_numpy()
        _nbbo_ask = nbbo_sorted[col.orders.offer].to_numpy()
    else:
        nbbo_sorted = None
        _nbbo_ts = _nbbo_bid = _nbbo_ask = None
        print(f"    Using NBBO source: INTERNAL")

    # Pre-sort session states once for Polars join_asof (used when USE_POLARS_TRANSFORMS=True)
    if cfg.USE_POLARS_TRANSFORMS and session_states_df is not None and len(session_states_df) > 0:
        import polars as pl
        _session_pl = pl.from_pandas(session_states_df).sort('timestamp')
    else:
        _session_pl = None

    for idx in range(len(sweep_orders)):
        sweep = sweep_orders.iloc[idx]
        sweep_id = int(sweep_orders[col.common.orderid].iloc[idx])
        sweep_side = int(sweep[col.common.side])
        sweep_qty_available = sweep[col.orders.leaves_quantity]
        sweep_orderbookid = sweep[col.common.orderbookid]
        
        first_exec_time = int(sweep['effective_timestamp'])
        last_exec_time = int(sweep['last_execution_time'])
        sweep_lost_priority = bool(sweep.get('lost_priority', False))
        sweep_changereason = int(sweep.get('changereason', CHANGEREASON_NEW_ORDER))
        
        if sweep_qty_available <= 0:
            sweep_summaries.append({
                'orderid': sweep_id, 'timestamp': sweep[col.common.timestamp],
                'side': sweep_side, 'quantity': sweep_qty_available,
                'matched_quantity': 0, 'remaining_quantity': 0,
                'fill_ratio': 0, 'num_matches': 0,
                'orderbookid': sweep_orderbookid,
                'lost_priority': sweep_lost_priority,
                'changereason': sweep_changereason,
            })
            continue
        
        eligible_orders = all_orders[
            (all_orders['effective_timestamp'] >= first_exec_time) &
            (all_orders['effective_timestamp'] <= last_exec_time) &
            (all_orders[col.common.orderid] != sweep_id) &
            (all_orders[col.common.orderbookid] == sweep_orderbookid) &
            (all_orders[col.common.side] != sweep_side)
        ].copy()
        
        # IMPROVEMENT 1: Session State Filtering
        if session_states_df is not None and len(session_states_df) > 0:
            if cfg.USE_POLARS_TRANSFORMS and _session_pl is not None:
                import polars as pl
                eo_pl = pl.from_pandas(eligible_orders).sort('effective_timestamp')
                eo_pl = eo_pl.join_asof(
                    _session_pl.select(['timestamp', 'session_state']),
                    left_on='effective_timestamp',
                    right_on='timestamp',
                    strategy='backward'
                ).filter(pl.col('session_state').is_in(['OPEN', 'CONTINUOUS'])).drop('session_state')
                eligible_orders = eo_pl.to_pandas()
            else:
                eligible_orders = eligible_orders[
                    eligible_orders['effective_timestamp'].apply(
                        lambda ts: _is_valid_trading_session(ts, session_states_df)
                    )
                ]
        
        eligible_orders = eligible_orders.sort_values(['effective_timestamp', 'sequence'])

        # Build a min-heap so iceberg contras can be repositioned to the back of
        # the queue when their display slice is exhausted (bi.txt §27).
        # Elements: (effective_timestamp, sequence, counter, order_dict)
        _p1_heap: list = []
        _p1_counter = 0
        for _, _o_row in eligible_orders.iterrows():
            heapq.heappush(_p1_heap, (
                int(_o_row['effective_timestamp']),
                int(_o_row['sequence']),
                _p1_counter,
                _o_row.to_dict(),
            ))
            _p1_counter += 1

        sweep_remaining_qty = sweep_qty_available
        sweep_matched_qty = 0
        sweep_num_matches = 0

        while _p1_heap and sweep_remaining_qty > 0:
            _, _, _, order = heapq.heappop(_p1_heap)

            order_id = int(order[col.common.orderid])
            order_available = order_remaining.get(order_id, 0)
            if order_available <= 0:
                continue
            
            # IMPROVEMENT 4: Iceberg Order Support — cap to current display slice
            order_available = min(
                order_available,
                _get_iceberg_available_qty(order, iceberg_slice_consumed.get(order_id, 0)),
            )
            
            # MAQ Validation
            sweep_maq = int(sweep.get('minimumquantity', 0)) if 'minimumquantity' in sweep else 0
            sweep_single_fill = int(sweep.get('singlefillminimumquantity', 0)) if 'singlefillminimumquantity' in sweep else 0
            contra_maq = int(order.get('minimumquantity', 0))
            contra_single_fill = int(order.get('singlefillminimumquantity', 0))
            potential_match_qty = min(sweep_remaining_qty, order_available)
            
            if sweep_maq > 0:
                if sweep_single_fill == 1:
                    if potential_match_qty < sweep_maq:
                        continue
                else:
                    if sweep_remaining_qty < sweep_maq and sweep_matched_qty > 0:
                        break
                    elif potential_match_qty < sweep_maq and sweep_matched_qty == 0:
                        continue
            
            if contra_maq > 0:
                if contra_single_fill == 1:
                    if potential_match_qty < contra_maq:
                        continue
                else:
                    if order_available < contra_maq:
                        continue
            
            # Crossing Prevention
            sweep_participant = int(sweep.get('participantid', 0))
            contra_participant = int(order.get('participantid', 0))
            sweep_crossing_key = int(sweep.get('crossingkey', 0))
            contra_crossing_key = int(order.get('crossingkey', 0))
            
            if sweep_participant > 0 and sweep_participant == contra_participant:
                if sweep_crossing_key == 0 or contra_crossing_key == 0:
                    continue
                elif sweep_crossing_key != contra_crossing_key:
                    continue
            
            match_qty = min(sweep_remaining_qty, order_available)
            match_timestamp = int(order['effective_timestamp'])
            
            # IMPROVEMENT 1: Check session state at match time
            if not _is_valid_trading_session(match_timestamp, session_states_df):
                continue
            
            # Detect Any Price Block contra before NBBO check (APB doesn't need NBBO)
            contra_midtick = int(order.get('midtick', MIDTICK_NO))
            is_apb = (
                int(order.get('exchangeordertype', 0)) == ORDERTYPE_BLOCK_LIMIT
                and contra_midtick in (MIDTICK_ANY_PRICE_BLOCK, MIDTICK_ANY_PRICE_BLOCK_WITH_MIDTICK)
            )

            if is_apb:
                # §24.1.3 Any Price Block — execution at contra's limit price, no NBBO constraint.
                # Both sides must have crossing prices; minimum block size must be met.
                contra_limit = int(order.get('price', 0))
                sweep_limit  = int(sweep.get('price', 0))
                if sweep_side == 1:      # buy sweep: must be willing to pay at least contra ask
                    if sweep_limit < contra_limit:
                        continue
                else:                    # sell sweep: must be willing to sell at most contra bid
                    if sweep_limit > contra_limit:
                        continue
                execution_price = float(contra_limit)
                # Minimum block size: traded value (qty * price in price units) must meet threshold.
                if cfg.MIN_BLOCK_SIZE > 0 and match_qty * execution_price < cfg.MIN_BLOCK_SIZE:
                    continue
                is_pref = (
                    cfg.RESTING_APPLY_PREFERENCING
                    and sweep_participant > 0
                    and contra_participant > 0
                    and sweep_participant == contra_participant
                )
                match_type = 'BLOCK_PREF' if is_pref else 'BLOCK'
                nbbo_bid = nbbo_offer = 0  # no NBBO constraint for APB
            else:
                # GAP 4: Get NBBO at match time (not order entry time)
                if nbbo_source == 'EXTERNAL':
                    if _nbbo_ts is not None:
                        # Fast numpy searchsorted — eliminates one DataFrame slice per match
                        _idx = int(np.searchsorted(_nbbo_ts, match_timestamp, side='right')) - 1
                        nbbo_bid  = int(_nbbo_bid[_idx])  if _idx >= 0 else 0
                        nbbo_offer = int(_nbbo_ask[_idx]) if _idx >= 0 else 0
                    else:
                        nbbo_bid, nbbo_offer, _ = _get_nbbo_at_timestamp(
                            nbbo_sorted, match_timestamp, sweep_orderbookid
                        )
                    if nbbo_bid == 0 or nbbo_offer == 0:
                        continue
                else:
                    nbbo_bid = int(order[col.orders.national_bid])
                    nbbo_offer = int(order[col.orders.national_offer])
                    # Sentinel value (-9223372036854775808) means field is unavailable;
                    # fall back to order book bid/offer snapshot columns.
                    if nbbo_bid == INT64_SENTINEL or nbbo_offer == INT64_SENTINEL:
                        nbbo_bid = int(order[col.orders.bid])
                        nbbo_offer = int(order[col.orders.offer])
                    # If still invalid (both 0 or sentinel), skip this match.
                    if nbbo_bid <= 0 or nbbo_offer <= 0:
                        continue

                # GAP 3: Check price limits
                midpoint = (nbbo_bid + nbbo_offer) / 2
                if not _is_price_within_limits(midpoint, price_limits):
                    continue

                # GAP 1: Validate order price limits
                if not _validate_order_price_limit(order, midpoint, sweep_side):
                    continue

                # GAP 2 & Mid-tick improvement
                sweep_midtick = int(sweep.get('midtick', MIDTICK_NO))
                effective_midtick = max(sweep_midtick, contra_midtick)
                execution_price = _apply_midtick_improvement(
                    midpoint, nbbo_bid, nbbo_offer, sweep_side, effective_midtick,
                    tick_size_override=tick_size_override,
                    tick_size_table=tick_size_table,
                    price=midpoint
                )

                # GAP 6: Track match type (sweep-to-sweep vs sweep-to-regular)
                match_type = 'SWEEP_TO_SWEEP' if order.get('exchangeordertype') == 2048 else 'SWEEP_TO_REGULAR'

            # GAP 5: Add execution delay
            match_timestamp = _add_execution_delay(match_timestamp)

            matchgroupid = base_matchgroupid + match_counter
            match_counter += 1

            sweep_side_val = int(sweep[col.common.side])
            order_side = int(order[col.common.side])
            
            # IMPROVEMENT 6: Get participant type
            contra_participant_type = _get_participant_type(contra_participant, participants_dict)
            
            simulated_trades.append({
                'EXCHANGE': 3, 'sequence': row_counter, 'tradedate': tradedate,
                'tradetime': match_timestamp, 'securitycode': sweep_orderbookid,
                'orderid': sweep_id, 'dealsource': _get_deal_source(match_type), 'exchangeinfo': '',
                'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid,
                'nationalofferpricesnapshot': nbbo_offer,
                'tradeprice': int(execution_price), 'quantity': int(match_qty),
                'side': sweep_side_val, 'participantid': 0,
                'passiveaggressive': 1, 'row_num': row_counter,
                'match_type': match_type,
                'contra_participant_type': contra_participant_type,
            })
            row_counter += 1

            simulated_trades.append({
                'EXCHANGE': 3, 'sequence': row_counter, 'tradedate': tradedate,
                'tradetime': match_timestamp, 'securitycode': sweep_orderbookid,
                'orderid': order_id, 'dealsource': _get_deal_source(match_type), 'exchangeinfo': '',
                'matchgroupid': matchgroupid,
                'nationalbidpricesnapshot': nbbo_bid,
                'nationalofferpricesnapshot': nbbo_offer,
                'tradeprice': int(execution_price), 'quantity': int(match_qty),
                'side': order_side, 'participantid': 0,
                'passiveaggressive': 0, 'row_num': row_counter,
                'match_type': match_type,
                'contra_participant_type': contra_participant_type,
            })
            row_counter += 1
            
            sweep_remaining_qty -= match_qty
            order_remaining[order_id] -= match_qty
            sweep_matched_qty += match_qty
            sweep_num_matches += 1
            # Update iceberg slice consumed; on exhaustion, reposition to back of queue.
            _iceberg_dq = order.get('display_quantity', None)
            if _iceberg_dq is not None and not pd.isna(_iceberg_dq):
                _iceberg_dq = int(_iceberg_dq)
                _new_consumed = iceberg_slice_consumed.get(order_id, 0) + match_qty
                if _new_consumed >= _iceberg_dq:
                    # Slice exhausted — reset consumed counter and reposition the order to
                    # the back of the time-priority queue at its current timestamp.
                    iceberg_slice_consumed[order_id] = _new_consumed - _iceberg_dq
                    if order_remaining.get(order_id, 0) > 0:
                        heapq.heappush(_p1_heap, (
                            int(order['effective_timestamp']),
                            _p1_counter,
                            _p1_counter,
                            order,
                        ))
                        _p1_counter += 1
                else:
                    iceberg_slice_consumed[order_id] = _new_consumed
        
        if sweep_id not in sweep_usage:
            sweep_usage[sweep_id] = {'matched_quantity': 0, 'num_matches': 0}
        sweep_usage[sweep_id]['matched_quantity'] = sweep_matched_qty
        sweep_usage[sweep_id]['num_matches'] = sweep_num_matches
        
        sweep_summaries.append({
            'orderid': sweep_id, 'timestamp': sweep[col.common.timestamp],
            'side': sweep_side, 'quantity': sweep_qty_available,
            'matched_quantity': sweep_matched_qty,
            'remaining_quantity': sweep_remaining_qty,
            'fill_ratio': sweep_matched_qty / sweep_qty_available if sweep_qty_available > 0 else 0,
            'num_matches': sweep_num_matches, 'orderbookid': sweep_orderbookid,
            'lost_priority': sweep_lost_priority, 'changereason': sweep_changereason,
        })
    
    if simulated_trades:
        simulated_trades_df = pd.DataFrame(simulated_trades)
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
        simulated_trades_df = pd.DataFrame(columns=[
            'EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode',
            'orderid', 'dealsource', 'exchangeinfo', 'matchgroupid',
            'nationalbidpricesnapshot', 'nationalofferpricesnapshot',
            'tradeprice', 'quantity', 'side', 'participantid',
            'passiveaggressive', 'row_num', 'match_type', 'contra_participant_type'
        ])
    
    order_summary_df = pd.DataFrame(sweep_summaries)
    sweep_utilization_df = _generate_sweep_utilization(sweep_orders, sweep_usage)

    return {
        'order_summary': order_summary_df,
        'sweep_utilization': sweep_utilization_df,
        'simulated_trades': simulated_trades_df,
        'sweep_usage': sweep_usage,          # exposed for build_remainder_df
    }


def _calc_resting_price(limit_price, tick_size, side):
    """
    Calculate effective passive resting price for Centre Point.

    Per bi.txt §25.2 pt 6: passive CP execution is at a half-tick INSIDE
    the order's limit price (i.e. the midpoint must be at least this good).

    If RESTING_USE_MIDTICK is False, the raw limit price is returned unchanged
    (experimental — not per ASX spec).

    Args:
        limit_price: Order limit price (integer ticks)
        tick_size:   Tick size for the instrument
        side:        1 = BUY, 2 = SELL

    Returns:
        int: Effective resting price threshold
    """
    if not cfg.RESTING_USE_MIDTICK:
        return int(limit_price)
    half_tick = tick_size / 2.0
    if side == 1:   # BUY  — half-tick ABOVE limit makes it easier to match
        return int(limit_price + half_tick)
    else:           # SELL — half-tick BELOW limit
        return int(limit_price - half_tick)


def _calc_lit_resting_price(limit_price, tick_size, side):
    """
    Effective resting price in ASX TradeMatch.

    Per bi.txt §25.2 pt 7: mid-tick flag is IGNORED in TradeMatch.
    When RESTING_LIT_USE_LIMIT=True (default / spec-compliant) the raw
    limit price is used.  Set False for experimental half-tick variant.
    """
    if cfg.RESTING_LIT_USE_LIMIT:
        return int(limit_price)
    half_tick = tick_size / 2.0
    if side == 1:
        return int(limit_price + half_tick)
    else:
        return int(limit_price - half_tick)


def _get_session_end_time(session_states_df, after_timestamp):
    """
    Return the timestamp of the first non-OPEN/CONTINUOUS session change
    after `after_timestamp`.  Falls back to int max if no session data.
    """
    if session_states_df is None or len(session_states_df) == 0:
        return int(2**62)
    future = session_states_df[
        (session_states_df['timestamp'] > after_timestamp) &
        (~session_states_df['session_state'].isin(MATCHING_SESSION_STATES))
    ]
    if len(future) == 0:
        return int(2**62)
    return int(future['timestamp'].iloc[0])


def _build_lit_order_book(lit_orders_raw, orderbookid):
    """
    Build a price-time priority lit order book from raw orders for a security.

    Only includes orders with exchangeordertype in [0, 2] (regular lit + market).
    The book is returned as a sorted structure suitable for walking in price
    priority when an aggressive order arrives.

    Returns:
        dict with keys 'buy' and 'sell', each a list of dicts sorted by
        (price desc for buy / price asc for sell, then effective_timestamp asc).
        Each entry: {orderid, price, effective_timestamp, sequence, quantity,
                     participantid, crossingkey, ordertype, remaining_qty}
    """
    LIT_ORDER_TYPES = {0, 2}
    if lit_orders_raw is None or len(lit_orders_raw) == 0:
        return {'buy': [], 'sell': []}

    book_df = lit_orders_raw[
        (lit_orders_raw[ORDER_TYPE_COLUMN].isin(LIT_ORDER_TYPES)) &
        (lit_orders_raw['orderbookid'] == orderbookid) &
        (lit_orders_raw['orderstatus'] == 1)
    ].copy()

    if len(book_df) == 0:
        return {'buy': [], 'sell': []}

    # Compute effective timestamp
    book_df['effective_timestamp'] = book_df.apply(
        _get_effective_timestamp, axis=1
    ).astype('int64')
    book_df['quantity'] = book_df['quantity'].fillna(0).astype('int64')
    book_df['price'] = book_df['price'].fillna(0).astype('int64')
    book_df['participantid'] = book_df['participantid'].fillna(0).astype('int64')
    book_df['crossingkey'] = book_df['crossingkey'].fillna(0).astype('int64')
    book_df['ordertype'] = book_df.get('ordertype', 1).fillna(1).astype('int64')

    buy_side = book_df[book_df['side'] == 1].sort_values(
        ['price', 'effective_timestamp', 'sequence'],
        ascending=[False, True, True]
    )
    sell_side = book_df[book_df['side'] == 2].sort_values(
        ['price', 'effective_timestamp', 'sequence'],
        ascending=[True, True, True]
    )

    def _to_records(df):
        return [
            {
                'orderid': int(r['orderid']),
                'price': int(r['price']),
                'effective_timestamp': int(r['effective_timestamp']),
                'sequence': int(r.get('sequence', 0)),
                'quantity': int(r['quantity']),
                'participantid': int(r['participantid']),
                'crossingkey': int(r['crossingkey']),
                'ordertype': int(r['ordertype']),
                'remaining_qty': int(r['quantity']),
            }
            for _, r in df.iterrows()
        ]

    return {'buy': _to_records(buy_side), 'sell': _to_records(sell_side)}


def simulate_resting_phase(
    sweep_orders,
    remainder_df,
    all_cp_orders,
    lit_orders_raw,
    nbbo_data,
    session_states_df,
    tick_size_override=None,
    tick_size_table=None,
    participants_dict=None,
):
    """
    Phase 2: Simulate passive resting of sweep orders in Centre Point (dark)
    and/or ASX TradeMatch (lit) after the aggressive Phase 1.

    Controlled entirely by config flags:
        cfg.SIMULATE_RESTING_PHASE      — master switch (caller should check)
        cfg.SIMULATE_LIT_RESTING        — include lit venue
        cfg.RESTING_LIT_BOOK_MODE       — 'full' (Option A) or 'scan' (Option B)
        cfg.RESTING_USE_MIDTICK         — half-tick dark resting price
        cfg.RESTING_LIT_USE_LIMIT       — lit resting price = limit (spec)
        cfg.RESTING_MODEL_CANCELLATION  — expire at session end
        cfg.RESTING_APPLY_CROSSING_KEYS — crossing key validation
        cfg.RESTING_APPLY_SESSION_FILTER— session state filter
        cfg.RESTING_APPLY_MAQ           — MAQ validation
        cfg.RESTING_APPLY_PREFERENCING  — same-participant preferencing
        cfg.RESTING_APPLY_ICEBERG       — iceberg shown qty

    Args:
        sweep_orders:    Prepared sweep orders DataFrame (from _prepare_sweep_orders)
        remainder_df:    DataFrame with columns [orderid, rest_entry_time,
                         remaining_qty, limit_price, side, orderbookid, midtick,
                         timevaliditydecoded] — one row per sweep order with
                         remaining quantity after Phase 1
        all_cp_orders:   All CP orders (same as Phase 1 all_orders) — contra pool
                         for dark leg
        lit_orders_raw:  Raw orders DataFrame including types [0, 2] — contra pool
                         for lit leg (may be None if SIMULATE_LIT_RESTING=False)
        nbbo_data:       NBBO DataFrame or None
        session_states_df: Session state DataFrame or None
        tick_size_override: Tick size override (int) or None
        tick_size_table: Tick size table from reference data or None
        participants_dict: Participant info dict or None

    Returns:
        dict with keys:
            'resting_trades'  — DataFrame of all Phase 2 simulated trades
            'resting_summary' — DataFrame with per-order fill summary
    """
    nbbo_source = cfg.NBBO_SOURCE

    resting_trades = []
    resting_summaries = []
    row_counter = 1
    match_counter = 0
    base_matchgroupid = 7904794001999000001

    # Pre-build NBBO arrays for fast lookup
    if nbbo_source == 'EXTERNAL' and nbbo_data is not None and len(nbbo_data) > 0:
        _nbbo_sorted = nbbo_data.sort_values('timestamp').reset_index(drop=True)
        _nbbo_ts  = _nbbo_sorted['timestamp'].to_numpy()
        _nbbo_bid = _nbbo_sorted[col.orders.bid].to_numpy()
        _nbbo_ask = _nbbo_sorted[col.orders.offer].to_numpy()
    else:
        _nbbo_ts = _nbbo_bid = _nbbo_ask = None

    # Pre-index CP contra orders by (orderbookid, side) for fast lookup
    cp_index = {}
    for side_val in [1, 2]:
        for ob_id in all_cp_orders[col.common.orderbookid].unique():
            mask = (
                (all_cp_orders[col.common.orderbookid] == ob_id) &
                (all_cp_orders[col.common.side] == side_val)
            )
            cp_index[(int(ob_id), int(side_val))] = all_cp_orders[mask].copy()

    # Build lit order book per security (Option A) or index (Option B)
    lit_books = {}   # orderbookid → {'buy': [...], 'sell': [...]}  (Option A)
    lit_index = {}   # (orderbookid, side) → DataFrame              (Option B)
    if cfg.SIMULATE_LIT_RESTING and lit_orders_raw is not None:
        ob_ids = remainder_df['orderbookid'].unique()
        for ob_id in ob_ids:
            if cfg.RESTING_LIT_BOOK_MODE == 'full':
                lit_books[int(ob_id)] = _build_lit_order_book(lit_orders_raw, ob_id)
            else:
                LIT_TYPES = {0, 2}
                for side_val in [1, 2]:
                    mask = (
                        (lit_orders_raw[ORDER_TYPE_COLUMN].isin(LIT_TYPES)) &
                        (lit_orders_raw['orderbookid'] == ob_id) &
                        (lit_orders_raw['side'] == side_val) &
                        (lit_orders_raw['orderstatus'] == 1)
                    )
                    sub = lit_orders_raw[mask].copy()
                    if 'effective_timestamp' not in sub.columns:
                        _r = sub.apply(_get_effective_timestamp, axis=1)
                        if isinstance(_r, pd.DataFrame):
                            _r = _r.squeeze(axis=1) if _r.shape[1] == 1 else pd.Series(dtype='int64')
                        sub['effective_timestamp'] = _r.astype('int64')
                    lit_index[(int(ob_id), int(side_val))] = sub.sort_values(
                        ['effective_timestamp', 'sequence']
                    )

    # Track remaining qty per CP contra order across all resting sweeps
    cp_order_remaining = {
        int(oid): int(qty)
        for oid, qty in zip(
            all_cp_orders[col.common.orderid].values,
            all_cp_orders[col.common.quantity].values,
        )
    }
    # Iceberg slice tracking for Phase 2 (CP dark and lit-scan legs, keyed by order_id)
    p2_iceberg_slice_consumed: dict = {}
    # Track remaining qty for lit book entries (Option A)
    lit_remaining = {}   # orderid → remaining qty

    if len(sweep_orders) > 0:
        first_ts = sweep_orders[col.common.timestamp].iloc[0]
        tradedate = pd.to_datetime(first_ts, unit='ns').strftime('%Y-%m-%d')
    else:
        tradedate = None

    # ── DARK LEG — inverted loop: contra-centric with preferencing ───────────
    # Per §24.10: an incoming order routes to same-participant resting orders
    # first, then FIFO.  We must iterate over incoming contras in time order
    # and, for each, select the best eligible resting sweep.
    #
    # Sweep state is tracked in a dict so the lit leg (below) can read final
    # dark fill counts.

    # Build per-sweep state from remainder_df
    sweep_state = {}
    for _, rem in remainder_df.iterrows():
        sid = int(rem['orderid'])
        ts_rem = int(rem['rest_entry_time'])
        if cfg.RESTING_MODEL_CANCELLATION:
            exp = _get_session_end_time(session_states_df, ts_rem)
        else:
            exp = int(2**62)
        lp  = int(rem['limit_price'])
        sv  = int(rem['side'])
        tck = _calculate_tick_size(0, 0, tick_size_override, tick_size_table, lp)
        sweep_state[sid] = {
            'rest_entry_time':   ts_rem,
            'expiry_time':       exp,
            'remaining_qty':     int(rem['remaining_qty']),
            'limit_price':       lp,
            'side':              sv,
            'orderbookid':       int(rem['orderbookid']),
            'participantid':     int(rem.get('participantid', 0)),
            'crossingkey':       int(rem.get('crossingkey', 0)),
            'minimumquantity':   int(rem.get('minimumquantity', 0)),
            'singlefillminimumquantity': int(rem.get('singlefillminimumquantity', 0)),
            'preferenceonly':    int(rem.get('preferenceonly', 0)),
            'dark_resting_price': _calc_resting_price(lp, tck, sv),
            'lit_resting_price':  _calc_lit_resting_price(lp, tck, sv),
            'dark_filled':   0,
            'dark_matches':  0,
            'lit_filled':    0,
            'lit_matches':   0,
        }

    # Collect all CP contra orders across all relevant (orderbookid, side) pairs,
    # sort globally by time.
    all_ob_sides = {(st['orderbookid'], 2 if st['side'] == 1 else 1)
                    for st in sweep_state.values()}
    all_contra_frames = [
        cp_index.get(key, pd.DataFrame()) for key in all_ob_sides
        if len(cp_index.get(key, pd.DataFrame())) > 0
    ]
    if all_contra_frames:
        all_cp_contras = pd.concat(all_contra_frames).sort_values(
            ['effective_timestamp', 'sequence']
        ).reset_index(drop=True)
    else:
        all_cp_contras = pd.DataFrame()

    # Build a min-heap for Phase 2 dark-leg contra iteration so that iceberg
    # contras can be repositioned after slice exhaustion (bi.txt §27).
    # Elements: (effective_timestamp, sequence, counter, contra_dict)
    _p2_heap: list = []
    _p2_counter = 0
    for _, _c_row in all_cp_contras.iterrows():
        heapq.heappush(_p2_heap, (
            int(_c_row['effective_timestamp']),
            int(_c_row['sequence']),
            _p2_counter,
            _c_row.to_dict(),
        ))
        _p2_counter += 1

    while _p2_heap:
        _, _, _, contra = heapq.heappop(_p2_heap)
        contra_ts          = int(contra['effective_timestamp'])
        contra_id          = int(contra[col.common.orderid])
        contra_ob          = int(contra[col.common.orderbookid])
        contra_side_val    = int(contra[col.common.side])
        contra_participant = int(contra.get('participantid', 0))
        contra_crossing_key = int(contra.get('crossingkey', 0))

        contra_avail = cp_order_remaining.get(contra_id, 0)
        if contra_avail <= 0:
            continue

        if cfg.RESTING_APPLY_ICEBERG:
            contra_avail = min(
                contra_avail,
                _get_iceberg_available_qty(contra, p2_iceberg_slice_consumed.get(contra_id, 0)),
            )
        if contra_avail <= 0:
            continue

        if cfg.RESTING_APPLY_SESSION_FILTER:
            if not _is_valid_trading_session(contra_ts, session_states_df):
                continue

        # NBBO at match time (same as Phase 1)
        if nbbo_source == 'EXTERNAL' and _nbbo_ts is not None:
            _idx = int(np.searchsorted(_nbbo_ts, contra_ts, side='right')) - 1
            nbbo_bid   = int(_nbbo_bid[_idx]) if _idx >= 0 else 0
            nbbo_offer = int(_nbbo_ask[_idx]) if _idx >= 0 else 0
            # If external NBBO unavailable, fall back to order snapshot fields.
            if nbbo_bid <= 0 or nbbo_offer <= 0:
                nb = int(contra.get(col.orders.national_bid, INT64_SENTINEL))
                no = int(contra.get(col.orders.national_offer, INT64_SENTINEL))
                if nb != INT64_SENTINEL and no != INT64_SENTINEL and nb > 0 and no > 0:
                    nbbo_bid, nbbo_offer = nb, no
                else:
                    nbbo_bid = int(contra.get(col.orders.bid, 0))
                    nbbo_offer = int(contra.get(col.orders.offer, 0))
        else:
            nbbo_bid   = int(contra.get(col.orders.national_bid, INT64_SENTINEL))
            nbbo_offer = int(contra.get(col.orders.national_offer, INT64_SENTINEL))
            # Sentinel means unavailable — fall back to order book bid/offer snapshot.
            if nbbo_bid == INT64_SENTINEL or nbbo_offer == INT64_SENTINEL:
                nbbo_bid   = int(contra.get(col.orders.bid, 0))
                nbbo_offer = int(contra.get(col.orders.offer, 0))

        if nbbo_bid <= 0 or nbbo_offer <= 0:
            continue

        midpoint = (nbbo_bid + nbbo_offer) / 2.0

        # Find eligible resting sweeps for this contra
        eligible = [
            (sid, st) for sid, st in sweep_state.items()
            if st['orderbookid'] == contra_ob
            and st['side'] != contra_side_val        # opposite sides
            and st['rest_entry_time'] < contra_ts
            and contra_ts < st['expiry_time']
            and st['remaining_qty'] > 0
        ]
        if not eligible:
            continue

        # §24.10 Preferencing: same-participant resting sweeps first (only if that
        # sweep has preferenceonly=1, i.e. the participant opted in), then FIFO.
        if cfg.RESTING_APPLY_PREFERENCING and contra_participant > 0:
            preferred = sorted(
                [
                    (sid, st) for sid, st in eligible
                    if st['participantid'] == contra_participant
                    and st['preferenceonly'] == 1
                ],
                key=lambda x: x[1]['rest_entry_time']
            )
            others = sorted(
                [
                    (sid, st) for sid, st in eligible
                    if not (st['participantid'] == contra_participant
                            and st['preferenceonly'] == 1)
                ],
                key=lambda x: x[1]['rest_entry_time']
            )
            sorted_sweeps = preferred + others
        else:
            sorted_sweeps = sorted(eligible, key=lambda x: x[1]['rest_entry_time'])

        contra_midtick = int(contra.get('midtick', MIDTICK_NO))

        for sweep_id, st in sorted_sweeps:
            if contra_avail <= 0:
                break

            sweep_side    = st['side']
            dark_rp       = st['dark_resting_price']
            sweep_maq     = st['minimumquantity']
            sweep_sfmaq   = st['singlefillminimumquantity']
            dark_filled_so_far = st['dark_filled']

            # Midpoint check against this sweep's resting price
            if sweep_side == 1:
                if midpoint > dark_rp:
                    continue
            else:
                if midpoint < dark_rp:
                    continue

            if cfg.RESTING_APPLY_CROSSING_KEYS:
                sweep_participant  = st['participantid']
                sweep_crossing_key = st['crossingkey']
                if sweep_participant > 0 and sweep_participant == contra_participant:
                    if sweep_crossing_key == 0 or contra_crossing_key == 0:
                        continue
                    if sweep_crossing_key != contra_crossing_key:
                        continue

            potential_qty = min(st['remaining_qty'], contra_avail)

            if cfg.RESTING_APPLY_MAQ and sweep_maq > 0:
                if sweep_sfmaq == 1:
                    if potential_qty < sweep_maq:
                        continue
                else:
                    if st['remaining_qty'] < sweep_maq and dark_filled_so_far > 0:
                        continue
                    if potential_qty < sweep_maq and dark_filled_so_far == 0:
                        continue

            match_qty = potential_qty
            execution_price = _apply_midtick_improvement(
                midpoint, nbbo_bid, nbbo_offer, sweep_side,
                max(MIDTICK_YES if cfg.RESTING_USE_MIDTICK else MIDTICK_NO, contra_midtick),
                tick_size_override=tick_size_override,
                tick_size_table=tick_size_table,
                price=midpoint,
            )

            match_timestamp = _add_execution_delay(contra_ts)
            matchgroupid = base_matchgroupid + match_counter
            match_counter += 1

            contra_participant_type = _get_participant_type(contra_participant, participants_dict)

            # Use preference match type when the sweep opted into preferencing
            # and the contra order is from the same participant (bi.txt §24.10).
            dark_match_type = (
                'RESTING_DARK_PREF'
                if st['preferenceonly'] == 1
                   and contra_participant > 0
                   and st['participantid'] == contra_participant
                else 'RESTING_DARK'
            )

            _append_resting_trade(
                resting_trades, row_counter, tradedate, match_timestamp,
                contra_ob, sweep_id, matchgroupid, nbbo_bid, nbbo_offer,
                execution_price, match_qty, sweep_side, 0,
                dark_match_type, contra_participant_type, st['rest_entry_time'],
            )
            row_counter += 1
            _append_resting_trade(
                resting_trades, row_counter, tradedate, match_timestamp,
                contra_ob, contra_id, matchgroupid, nbbo_bid, nbbo_offer,
                execution_price, match_qty, contra_side_val, 1,
                dark_match_type, contra_participant_type, st['rest_entry_time'],
            )
            row_counter += 1

            st['remaining_qty'] -= match_qty
            st['dark_filled']   += match_qty
            st['dark_matches']  += 1
            contra_avail        -= match_qty
            cp_order_remaining[contra_id] = cp_order_remaining.get(contra_id, 0) - match_qty
            # Update iceberg slice tracking; on exhaustion, reposition contra to back of queue.
            _p2_dq = contra.get('display_quantity', None)
            if _p2_dq is not None and not pd.isna(_p2_dq):
                _p2_dq = int(_p2_dq)
                _p2_new = p2_iceberg_slice_consumed.get(contra_id, 0) + match_qty
                if _p2_new >= _p2_dq:
                    # Slice exhausted — reset and reposition contra to back of time-priority queue.
                    p2_iceberg_slice_consumed[contra_id] = _p2_new - _p2_dq
                    if cp_order_remaining.get(contra_id, 0) > 0:
                        heapq.heappush(_p2_heap, (
                            contra_ts,
                            _p2_counter,
                            _p2_counter,
                            contra,
                        ))
                        _p2_counter += 1
                    break  # exit inner sorted_sweeps loop; contra re-enters heap at updated priority
                else:
                    p2_iceberg_slice_consumed[contra_id] = _p2_new

    # ── LIT LEG — per-sweep loop (lit contras are not shared across sweeps) ──
    for _, rem in remainder_df.sort_values('rest_entry_time').iterrows():
        sweep_id = int(rem['orderid'])
        st = sweep_state[sweep_id]

        remaining_qty = st['remaining_qty']
        sweep_side    = st['side']
        ob_id         = st['orderbookid']
        rest_entry_time = st['rest_entry_time']
        expiry_time     = st['expiry_time']
        lit_resting_price = st['lit_resting_price']
        sweep_participant  = st['participantid']
        sweep_crossing_key = st['crossingkey']
        contra_side = 2 if sweep_side == 1 else 1

        # ── LIT LEG (ASX TradeMatch passive) ─────────────────────────────────
        if cfg.SIMULATE_LIT_RESTING and remaining_qty > 0:

            if cfg.RESTING_LIT_BOOK_MODE == 'full':
                # Option A: price-time priority lit book.
                # Our sweep is RESTING — contra orders are on the opposite side.
                # BUY sweep resting → contra SELL orders must have price <= our limit.
                # SELL sweep resting → contra BUY orders must have price >= our limit.
                # Sell side sorted price ASC (cheapest first); buy side price DESC (highest first).
                # NOTE: this models contra-order price crossings correctly but does not
                # yet deduct competing same-side orders at better prices — a future enhancement.
                book = lit_books.get(ob_id, {})
                contra_book_key = 'sell' if sweep_side == 1 else 'buy'
                contra_side_book = book.get(contra_book_key, [])

                for entry in contra_side_book:
                    if remaining_qty <= 0:
                        break

                    entry_ts = entry['effective_timestamp']
                    if entry_ts <= rest_entry_time:
                        continue
                    if entry_ts >= expiry_time:
                        break

                    entry_qty = lit_remaining.get(entry['orderid'], entry['remaining_qty'])
                    if entry_qty <= 0:
                        continue

                    if cfg.RESTING_APPLY_SESSION_FILTER:
                        if not _is_valid_trading_session(entry_ts, session_states_df):
                            continue

                    if cfg.RESTING_APPLY_CROSSING_KEYS:
                        contra_participant = entry['participantid']
                        contra_crossing_key = entry['crossingkey']
                        if sweep_participant > 0 and sweep_participant == contra_participant:
                            if sweep_crossing_key == 0 or contra_crossing_key == 0:
                                continue
                            if sweep_crossing_key != contra_crossing_key:
                                continue

                    # Price check (lit): contra price must cross our resting limit
                    entry_price = entry['price']
                    if sweep_side == 1:   # BUY resting — contra sell price <= our limit
                        if entry_price > lit_resting_price:
                            continue
                    else:                  # SELL resting — contra buy price >= our limit
                        if entry_price < lit_resting_price:
                            continue

                    match_qty = min(remaining_qty, entry_qty)
                    execution_price = lit_resting_price
                    match_timestamp = _add_execution_delay(entry_ts)
                    matchgroupid = base_matchgroupid + match_counter
                    match_counter += 1

                    contra_participant_type = _get_participant_type(
                        entry['participantid'], participants_dict
                    )

                    _append_resting_trade(
                        resting_trades, row_counter, tradedate, match_timestamp,
                        ob_id, sweep_id, matchgroupid, 0, 0,
                        execution_price, match_qty, sweep_side, 0,
                        'RESTING_LIT', contra_participant_type, rest_entry_time,
                    )
                    row_counter += 1
                    _append_resting_trade(
                        resting_trades, row_counter, tradedate, match_timestamp,
                        ob_id, entry['orderid'], matchgroupid, 0, 0,
                        execution_price, match_qty, contra_side, 1,
                        'RESTING_LIT', contra_participant_type, rest_entry_time,
                    )
                    row_counter += 1

                    remaining_qty -= match_qty
                    lit_remaining[entry['orderid']] = entry_qty - match_qty
                    st['lit_filled']  += match_qty
                    st['lit_matches'] += 1

            else:
                # Option B: flat scan — same structure as dark leg but with lit orders
                contra_lit = lit_index.get((ob_id, contra_side), pd.DataFrame())

                for _, contra in contra_lit.iterrows():
                    if remaining_qty <= 0:
                        break

                    contra_ts = int(contra.get('effective_timestamp',
                                               contra.get('timestamp', 0)))
                    if contra_ts <= rest_entry_time:
                        continue
                    if contra_ts >= expiry_time:
                        break

                    if cfg.RESTING_APPLY_SESSION_FILTER:
                        if not _is_valid_trading_session(contra_ts, session_states_df):
                            continue

                    if cfg.RESTING_APPLY_CROSSING_KEYS:
                        contra_participant = int(contra.get('participantid', 0))
                        contra_crossing_key = int(contra.get('crossingkey', 0))
                        if sweep_participant > 0 and sweep_participant == contra_participant:
                            if sweep_crossing_key == 0 or contra_crossing_key == 0:
                                continue
                            if sweep_crossing_key != contra_crossing_key:
                                continue

                    contra_id_lit = int(contra.get('orderid', 0))
                    contra_avail = int(contra.get('quantity', 0))
                    if cfg.RESTING_APPLY_ICEBERG:
                        contra_avail = min(
                            contra_avail,
                            _get_iceberg_available_qty(
                                contra, p2_iceberg_slice_consumed.get(contra_id_lit, 0)
                            ),
                        )
                    if contra_avail <= 0:
                        continue

                    contra_price = int(contra.get('price', 0))
                    if sweep_side == 1:
                        if contra_price > lit_resting_price:
                            continue
                    else:
                        if contra_price < lit_resting_price:
                            continue

                    match_qty = min(remaining_qty, contra_avail)
                    execution_price = lit_resting_price
                    match_timestamp = _add_execution_delay(contra_ts)
                    matchgroupid = base_matchgroupid + match_counter
                    match_counter += 1

                    contra_participant_type = _get_participant_type(
                        int(contra.get('participantid', 0)), participants_dict
                    )

                    _append_resting_trade(
                        resting_trades, row_counter, tradedate, match_timestamp,
                        ob_id, sweep_id, matchgroupid, 0, 0,
                        execution_price, match_qty, sweep_side, 0,
                        'RESTING_LIT_SCAN', contra_participant_type, rest_entry_time,
                    )
                    row_counter += 1
                    _append_resting_trade(
                        resting_trades, row_counter, tradedate, match_timestamp,
                        ob_id, contra_id_lit, matchgroupid, 0, 0,
                        execution_price, match_qty, contra_side, 1,
                        'RESTING_LIT_SCAN', contra_participant_type, rest_entry_time,
                    )
                    row_counter += 1

                    remaining_qty -= match_qty
                    st['lit_filled']  += match_qty
                    st['lit_matches'] += 1
                    # Update iceberg slice tracking for this lit contra.
                    _lit_dq = contra.get('display_quantity', None)
                    if _lit_dq is not None and not pd.isna(_lit_dq):
                        _lit_dq = int(_lit_dq)
                        _lit_new = p2_iceberg_slice_consumed.get(contra_id_lit, 0) + match_qty
                        p2_iceberg_slice_consumed[contra_id_lit] = (
                            _lit_new - _lit_dq if _lit_new >= _lit_dq else _lit_new
                        )

        resting_summaries.append(_make_resting_summary(
            sweep_id, rest_entry_time, ob_id, sweep_side,
            st['dark_filled'], st['dark_matches'],
            st['lit_filled'],  st['lit_matches'],
        ))

    # Build output DataFrames
    if resting_trades:
        resting_trades_df = pd.DataFrame(resting_trades)
        int_cols = [
            'EXCHANGE', 'sequence', 'tradetime', 'securitycode', 'orderid',
            'dealsource', 'matchgroupid', 'nationalbidpricesnapshot',
            'nationalofferpricesnapshot', 'tradeprice', 'quantity', 'side',
            'participantid', 'passiveaggressive', 'row_num', 'rest_entry_time',
        ]
        for c in int_cols:
            if c in resting_trades_df.columns:
                resting_trades_df[c] = resting_trades_df[c].astype('int64')
    else:
        resting_trades_df = pd.DataFrame(columns=[
            'EXCHANGE', 'sequence', 'tradedate', 'tradetime', 'securitycode',
            'orderid', 'dealsource', 'exchangeinfo', 'matchgroupid',
            'nationalbidpricesnapshot', 'nationalofferpricesnapshot',
            'tradeprice', 'quantity', 'side', 'participantid',
            'passiveaggressive', 'row_num', 'match_type',
            'contra_participant_type', 'rest_entry_time',
            'resting_duration_sec', 'phase', 'venue',
        ])

    resting_summary_df = pd.DataFrame(resting_summaries)
    return {
        'resting_trades': resting_trades_df,
        'resting_summary': resting_summary_df,
    }


def _get_deal_source(match_type):
    """Map internal match_type to ASX deal source code (dd.txt §1144-1187)."""
    if match_type in ('RESTING_LIT', 'RESTING_LIT_SCAN'):
        return DEALSOURCE_CONTINUOUS        # 1 — lit continuous matching
    if match_type == 'RESTING_DARK_PREF':
        return DEALSOURCE_PREFERENCE        # 46 — Centre Point preference matched
    if match_type == 'BLOCK_PREF':
        return DEALSOURCE_PREFERENCE_BLOCK  # 51 — preference Any Price Block
    if match_type == 'BLOCK':
        return DEALSOURCE_BLOCK             # 50 — Any Price Block
    return DEALSOURCE_CENTREPOINT           # 47 — Centre Point dark pool


def _append_resting_trade(
    trades_list, row_num, tradedate, tradetime, securitycode, orderid,
    matchgroupid, nbbo_bid, nbbo_offer, execution_price, quantity,
    side, passiveaggressive, match_type, contra_participant_type,
    rest_entry_time,
):
    """Append a single resting-phase trade row."""
    phase = 2
    venue = 'dark' if 'DARK' in match_type else 'lit'
    resting_duration_sec = (tradetime - rest_entry_time) / 1e9 if rest_entry_time > 0 else 0.0
    trades_list.append({
        'EXCHANGE': 3,
        'sequence': row_num,
        'tradedate': tradedate,
        'tradetime': tradetime,
        'securitycode': securitycode,
        'orderid': orderid,
        'dealsource': _get_deal_source(match_type),
        'exchangeinfo': '',
        'matchgroupid': matchgroupid,
        'nationalbidpricesnapshot': nbbo_bid,
        'nationalofferpricesnapshot': nbbo_offer,
        'tradeprice': int(execution_price),
        'quantity': int(quantity),
        'side': side,
        'participantid': 0,
        'passiveaggressive': passiveaggressive,
        'row_num': row_num,
        'match_type': match_type,
        'contra_participant_type': contra_participant_type,
        'rest_entry_time': rest_entry_time,
        'resting_duration_sec': resting_duration_sec,
        'phase': phase,
        'venue': venue,
    })


def _make_resting_summary(
    orderid, rest_entry_time, orderbookid, side,
    dark_filled, dark_matches, lit_filled, lit_matches,
):
    """Build a resting phase summary row for one sweep order."""
    total_filled = dark_filled + lit_filled
    return {
        'orderid': orderid,
        'rest_entry_time': rest_entry_time,
        'orderbookid': orderbookid,
        'side': side,
        'dark_filled_qty': dark_filled,
        'dark_num_matches': dark_matches,
        'lit_filled_qty': lit_filled,
        'lit_num_matches': lit_matches,
        'total_resting_filled_qty': total_filled,
    }


def build_remainder_df(sweep_orders, sweep_usage):
    """
    Build remainder_df from Phase 1 results — one row per sweep order with
    unfilled quantity and the timestamp at which it enters the resting queue.

    rest_entry_time = last_execution_time of the sweep order (i.e. when the
    aggressive phase ended and the order began resting).

    Args:
        sweep_orders: Prepared sweep orders DataFrame
        sweep_usage:  Dict {orderid: {'matched_quantity': ..., 'num_matches': ...}}

    Returns:
        DataFrame with columns needed by simulate_resting_phase
    """
    rows = []
    for _, sweep in sweep_orders.iterrows():
        sweep_id = int(sweep[col.common.orderid])
        available_qty = int(sweep[col.orders.leaves_quantity])
        usage = sweep_usage.get(sweep_id, {'matched_quantity': 0})
        phase1_filled = int(usage['matched_quantity'])
        remaining = available_qty - phase1_filled
        if remaining <= 0:
            continue
        rows.append({
            'orderid': sweep_id,
            'rest_entry_time': int(sweep.get('last_execution_time', sweep[col.common.timestamp])),
            'remaining_qty': remaining,
            'limit_price': int(sweep[col.common.price]),
            'side': int(sweep[col.common.side]),
            'orderbookid': int(sweep[col.common.orderbookid]),
            'midtick': int(sweep.get('midtick', MIDTICK_NO)),
            'timevaliditydecoded': sweep.get('timevaliditydecoded', 'Rest of Day'),
            'participantid': int(sweep.get('participantid', 0)),
            'crossingkey': int(sweep.get('crossingkey', 0)),
            'minimumquantity': int(sweep.get('minimumquantity', 0)),
            'singlefillminimumquantity': int(sweep.get('singlefillminimumquantity', 0)),
            'preferenceonly': int(sweep.get('preferenceonly', 0)),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        'orderid', 'rest_entry_time', 'remaining_qty', 'limit_price',
        'side', 'orderbookid', 'midtick', 'timevaliditydecoded',
        'participantid', 'crossingkey', 'minimumquantity',
        'singlefillminimumquantity',
    ])


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

# ============================================================================
# Section 8 - pipeline/partition_processor.py
# ============================================================================
"""Partition processing logic for pipeline steps 7-12."""

import pandas as pd
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

# (consolidated) import pipeline.data_processor as dp
# (consolidated) import pipeline.sweep_simulator as ss
# (deferred to aggregate.py) import pipeline.execution_comparison as ec
# (consolidated) import utils.file_utils as fu
# (consolidated) import utils.data_utils as du
from config import col
# (deferred to aggregate.py) from .trade_metrics_calculator import calculate_trade_metrics
import config as cfg


def process_single_partition(partition_key, processed_dir, outputs_dir, enable_trade_comparison=True):
    """Process single partition through steps 7-12 (simulation, metrics, comparison)."""
    try:
        date, security_code = partition_key.split('/')
        partition_dir = fu.get_partition_dir(processed_dir, partition_key)
        
        # Load partition data
        partition_data = dp.load_partition_data(partition_key, processed_dir)
        
        if not partition_data or 'orders_before' not in partition_data:
            return {
                'partition_key': partition_key,
                'status': 'skipped',
                'reason': 'No partition data found'
            }
        
        # Load NBBO if available
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
        
        # Step 8: Calculate simulated metrics
        orders_after = partition_data.get('orders_after')
        if orders_after is not None:
            orders_with_metrics = ec.calculate_simulated_metrics(
                orders_after,
                sim_results['order_summary'],
                sim_results['simulated_trades']
            )
            fu.save_orders_with_metrics(orders_with_metrics, outputs_dir, partition_key)
        
        # Steps 11-12: Trade-level comparison
        if enable_trade_comparison:
            compare_trades_for_partition(partition_key, sim_results, processed_dir, outputs_dir)
        
        return {
            'partition_key': partition_key,
            'status': 'success',
            'num_sweep_orders': len(sim_results['order_summary']),
            'num_matches': len(sim_results['simulated_trades']) // 2  # 2 rows per match
        }
        
    except Exception as e:
        return {
            'partition_key': partition_key,
            'status': 'error',
            'error': str(e)
        }


def compare_trades_for_partition(partition_key, sim_results, processed_dir, output_dir):
    """Compare trades for single partition (Steps 11-12)."""
    partition_dir = fu.get_partition_dir(processed_dir, partition_key)
    
    # Load trades data
    trades_df = fu.load_trades_matched(partition_dir)
    if trades_df is None or len(trades_df) == 0:
        return
    
    # Load orders to identify sweep orders
    orders_before = fu.load_orders_before(partition_dir)
    if orders_before is None:
        return
    
    # No normalization needed - Stage 1 already normalized column names
    
    # Get sweep order IDs
    sweep_orderids = du.get_sweep_orderids(orders_before)
    
    if len(sweep_orderids) == 0:
        return
    
    # Filter trades to only those involving sweep orders
    sweep_trades = trades_df[trades_df[col.common.orderid].isin(sweep_orderids)].copy()
    
    if len(sweep_trades) == 0:
        return
    
    # Aggregate simulated trades (pass orders_before for arrival NBBO)
    sim_aggregated = ec._aggregate_simulated_trades_per_order(
        sim_results['simulated_trades'],
        sim_results['order_summary'],
        orders_before  # Include arrival NBBO for simulated metrics
    )
    
    # Calculate real trade metrics
    from .trade_metrics_calculator import calculate_trade_metrics
    real_metrics_result = calculate_trade_metrics(
        trades_df=sweep_trades,
        orders_df=orders_before,
        filter_orderids=list(sweep_orderids),
        role_filter=None,
        prefix=''
    )
    real_order_metrics = real_metrics_result['per_order_metrics']
    
    # Compare
    comparison = ec._compare_order_level_trades(real_order_metrics, sim_aggregated)
    accuracy_summary = ec._calculate_trade_accuracy_summary(comparison)
    
    # Save comparison reports
    fu.save_trade_comparison(comparison, accuracy_summary, output_dir, partition_key)
    
    # Save full metrics (36 metrics per order) for Stage 3 to load
    # This avoids recalculating the same metrics in Stage 3
    fu.save_trade_metrics(real_order_metrics, sim_aggregated, output_dir, partition_key)


def process_partitions_parallel(partition_keys, processed_dir, outputs_dir, max_workers):
    """Process multiple partitions in parallel."""
    print(f"\n{'='*80}")
    print(f"PARALLEL PARTITION PROCESSING")
    print(f"{'='*80}")
    print(f"Processing {len(partition_keys)} partitions with {max_workers} workers...")
    
    partition_results = {}
    
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # Submit all partition jobs
        futures = {
            executor.submit(
                process_single_partition,
                partition_key,
                processed_dir,
                outputs_dir,
                True  # enable_trade_comparison
            ): partition_key
            for partition_key in partition_keys
        }
        
        # Collect results as they complete
        completed = 0
        for future in as_completed(futures):
            partition_key = futures[future]
            completed += 1
            
            try:
                result = future.result()
                partition_results[partition_key] = result
                
                status = result.get('status')
                if status == 'success':
                    print(f"  [{completed}/{len(partition_keys)}] ✓ {partition_key}: "
                          f"{result.get('num_sweep_orders', 0):,} sweep orders, "
                          f"{result.get('num_matches', 0):,} matches")
                elif status == 'skipped':
                    print(f"  [{completed}/{len(partition_keys)}] ⊘ {partition_key}: "
                          f"Skipped - {result.get('reason', 'Unknown')}")
                elif status == 'error':
                    print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: "
                          f"ERROR - {result.get('error', 'Unknown')}")
                    
            except Exception as e:
                print(f"  [{completed}/{len(partition_keys)}] ✗ {partition_key}: EXCEPTION - {str(e)}")
                partition_results[partition_key] = {
                    'partition_key': partition_key,
                    'status': 'exception',
                    'error': str(e)
                }
    
    # Print summary
    successful = sum(1 for r in partition_results.values() if r.get('status') == 'success')
    failed = sum(1 for r in partition_results.values() if r.get('status') in ('error', 'exception'))
    skipped = sum(1 for r in partition_results.values() if r.get('status') == 'skipped')
    
    print(f"\n{'='*80}")
    print(f"PARALLEL PROCESSING COMPLETE")
    print(f"  ✓ Successful: {successful}/{len(partition_keys)}")
    print(f"  ✗ Failed: {failed}/{len(partition_keys)}")
    print(f"  ⊘ Skipped: {skipped}/{len(partition_keys)}")
    print(f"{'='*80}")
    
    return partition_results


def _inject_lit_orders(partition_data, partition_key):
    """
    If resting phase + lit resting are both enabled, load the raw orders file
    for the security (all order types including 0 and 2) and attach it to
    partition_data['lit_orders_raw'].

    The orderbookid for this partition is derived from partition_key
    (format: 'date/orderbookid').  Raw files are discovered via cfg.RAW_FOLDERS.
    Does nothing if SIMULATE_RESTING_PHASE or SIMULATE_LIT_RESTING are False.
    """
    if not (cfg.SIMULATE_RESTING_PHASE and cfg.SIMULATE_LIT_RESTING):
        return

    if partition_data.get('lit_orders_raw') is not None:
        return  # already loaded

    try:
        _, orderbookid_str = partition_key.split('/')
        orderbookid = int(orderbookid_str)

        # Find the raw orders file that contains this orderbookid
        raw_orders_dir = Path(cfg.RAW_FOLDERS['orders'])
        raw_files = list(raw_orders_dir.glob('*_orders.csv'))
        if not raw_files:
            return

        # Prefer the most recently modified file (usually the active dataset)
        raw_file = sorted(raw_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        lit_df = pd.read_csv(raw_file, usecols=[
            'order_id', 'timestamp', 'security_code', 'exchangeordertype',
            'side', 'price', 'quantity', 'leavesquantity', 'orderstatus',
            'participantid', 'crossingkey', 'changereason', 'ordertype',
            'sequence', 'national_bid', 'national_offer',
        ], dtype={'security_code': int})

        # Normalise column names to match processed schema
        lit_df = lit_df.rename(columns={
            'order_id': 'orderid',
            'security_code': 'orderbookid',
            'leavesquantity': 'leavesquantity',
        })

        # Filter to this security
        lit_df = lit_df[lit_df['orderbookid'] == orderbookid].copy()

        # Keep only lit order types [0, 2]
        lit_df = lit_df[lit_df['exchangeordertype'].isin([0, 2])].copy()

        if len(lit_df) > 0:
            partition_data['lit_orders_raw'] = lit_df
            print(f"    Loaded {len(lit_df)} lit orders for resting phase (orderbookid={orderbookid})")
    except Exception as e:
        print(f"    Warning: could not load lit orders for resting phase: {e}")


def simulate_partition_step(partition_key, partition_data, nbbo_data, output_dir):
    """Step 7: Simulate sweep matching for single partition."""
    partition_data['nbbo'] = nbbo_data
    _inject_lit_orders(partition_data, partition_key)

    sim_results = ss.simulate_partition(partition_key, partition_data)

    if not sim_results:
        return None

    fu.save_simulation_results(sim_results, output_dir, partition_key)

    if cfg.SIMULATE_RESTING_PHASE and 'resting_trades' in sim_results:
        fu.save_resting_simulation_results(sim_results, output_dir, partition_key)

    return sim_results


def calculate_metrics_step(partition_key, sim_results, orders_after, output_dir):
    """Step 8: Calculate simulated metrics for single partition."""
    if orders_after is None or sim_results is None:
        return None
    
    orders_with_metrics = ec.calculate_simulated_metrics(
        orders_after,
        sim_results['order_summary'],
        sim_results['simulated_trades']
    )
    
    fu.save_orders_with_metrics(orders_with_metrics, output_dir, partition_key)
    
    return orders_with_metrics


def simulate_sweep_matching_sequential(orders_by_partition, order_states_by_partition,
                                       last_execution_by_partition, nbbo_by_partition, output_dir,
                                       reference_results=None):
    """Step 7: Simulate sweep matching for all partitions (sequential processing)."""
    print("\n[7/11] Simulating sweep matching...")

    simulation_results_by_partition = {}

    for partition_key in orders_by_partition.keys():
        if cfg.PROCESSING_MODE == 'memory':
            # Build partition_data entirely from in-memory dicts — no disk reads.
            states = order_states_by_partition.get(partition_key, {})
            if not states or 'before' not in states:
                continue
            date = partition_key.split('/')[0]
            ref = reference_results or {}
            partition_data = {
                'orders_before':  states['before'],
                'orders_after':   states['after'],
                'last_execution': last_execution_by_partition.get(partition_key, pd.DataFrame(
                    columns=['orderid', 'first_execution_time', 'last_execution_time'])),
                'nbbo':           nbbo_by_partition.get(partition_key),
                'session':        ref.get('session', {}).get(date, pd.DataFrame()),
                'reference':      ref.get('reference', {}).get(date, pd.DataFrame()),
                'participants':   ref.get('participants', {}).get(date, pd.DataFrame()),
            }
        else:
            # Original file-based path: load from data/processed/
            partition_data = dp.load_partition_data(
                partition_key,
                Path(output_dir).parent / 'processed'
            )

            if not partition_data or 'orders_before' not in partition_data:
                continue

            # Override with in-memory datasets (they are fresher than what's on disk)
            if partition_key in order_states_by_partition:
                partition_data['orders_before'] = order_states_by_partition[partition_key]['before']
                partition_data['orders_after'] = order_states_by_partition[partition_key]['after']

            if partition_key in last_execution_by_partition:
                partition_data['last_execution'] = last_execution_by_partition[partition_key]

            partition_data['nbbo'] = nbbo_by_partition.get(partition_key)

        # Load raw lit orders for resting phase if needed
        _inject_lit_orders(partition_data, partition_key)

        # Run simulation
        sim_results = ss.simulate_partition(partition_key, partition_data)

        if not sim_results:
            continue

        # Save simulation results
        fu.save_simulation_results(sim_results, output_dir, partition_key)

        if cfg.SIMULATE_RESTING_PHASE and 'resting_trades' in sim_results:
            fu.save_resting_simulation_results(sim_results, output_dir, partition_key)

        simulation_results_by_partition[partition_key] = {
            'order_summary': sim_results['order_summary'],
            'simulated_trades': sim_results['simulated_trades'],
        }
    
    print(f"   Completed sweep simulation for {len(simulation_results_by_partition)} partitions")
    return simulation_results_by_partition


def calculate_simulated_metrics_sequential(orders_by_partition, simulation_results_by_partition,
                                           processed_dir, output_dir, order_states=None):
    """Step 8: Calculate simulated metrics for all partitions (sequential processing)."""
    print("\n[8/11] Calculating simulated metrics...")

    orders_with_sim_metrics_by_partition = {}

    for partition_key, sim_results in simulation_results_by_partition.items():
        if cfg.PROCESSING_MODE == 'memory' and order_states and partition_key in order_states:
            orders_after = order_states[partition_key]['after']
        else:
            partition_dir = fu.get_partition_dir(processed_dir, partition_key)
            orders_after = fu.load_orders_after(partition_dir)

        if orders_after is None:
            continue
        
        # Calculate simulated metrics
        orders_with_metrics = ec.calculate_simulated_metrics(
            orders_after,
            sim_results['order_summary'],
            sim_results['simulated_trades']
        )
        
        # Save orders with simulated metrics
        fu.save_orders_with_metrics(orders_with_metrics, output_dir, partition_key)
        
        orders_with_sim_metrics_by_partition[partition_key] = orders_with_metrics
    
    print(f"   Calculated metrics for {len(orders_with_sim_metrics_by_partition)} partitions")
    return orders_with_sim_metrics_by_partition


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
            sim_aggregated = ec._aggregate_simulated_trades_per_order(
                simulated_trades,
                order_summary,
                orders_before
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
# Section 9 - pipeline/pipeline_output.py
# ============================================================================
"""Pipeline output printing and formatting."""

# (consolidated) import config.config as config


def _print_statistics_tier(stats_engine):
    """Print statistics tier information."""
    print(f"\nStatistics Tier: {stats_engine.get_tier_name()}")
    if stats_engine.tier == 2:
        print(f"  ⚠ Using approximate statistics (scipy not available)")
    elif stats_engine.tier == 1:
        print(f"  ℹ Statistical tests disabled (use --enable-stats to enable)")


def _print_security_info(securities):
    """Print security configuration details."""
    if len(securities) == 1:
        sec = securities[0]['security']
        ticker_str = sec.ticker.upper() if sec.ticker else "Unknown"
        print(f"  Security:   OrderbookID {sec.orderbookid} ({ticker_str})")
        print(f"              {sec.order_count:,} orders, {sec.trade_count:,} trades")
    else:
        print(f"  Securities: {len(securities)} securities to process")
        for sec_info in securities:
            sec = sec_info['security']
            ticker_str = sec.ticker.upper() if sec.ticker else "Unknown"
            print(f"              - OrderbookID {sec.orderbookid} ({ticker_str}): {sec.order_count:,} orders, {sec.trade_count:,} trades")


def _print_system_config():
    """Print system configuration."""
    print(f"\nSystem Configuration:")
    print(config.SYSTEM_CONFIG)


def _print_stage_plan(stages):
    """Print which stages will be executed."""
    if stages:
        print(f"\nStages to run: {', '.join(map(str, stages))}")
    else:
        print(f"\nRunning all stages (1-4)")


def print_pipeline_header(runtime_config):
    """Print pipeline header with configuration details."""
    print("="*80)
    print("CENTRE POINT SWEEP ORDER MATCHING PIPELINE")
    print("="*80)
    
    stats_engine = runtime_config['stats_engine']
    _print_statistics_tier(stats_engine)
    
    stages = runtime_config['stages']
    if stages is None or any(s in [1, 2, 3] for s in stages):
        print("\nRuntime Configuration:")
        print(f"  Date:       {runtime_config['date']}")
        
        securities = runtime_config['securities']
        _print_security_info(securities)
        
        print(f"  Mode:       {'Parallel' if runtime_config['enable_parallel'] else 'Sequential'}")
        _print_system_config()
        
        print(f"\nDirectories:")
        print(f"  Processed:  {config.PROCESSED_DIR}/")
        print(f"  Outputs:    {config.OUTPUTS_DIR}/")
    
    _print_stage_plan(stages)


def _format_partition_breakdown(data):
    """Format partition breakdown for summary."""
    lines = []
    for partition_key in sorted(data['orders'].keys()):
        num_orders = len(data['orders'][partition_key])
        num_trades = len(data['trades'].get(partition_key, [])) if data['trades'] else 0
        lines.append(f"  {partition_key}: {num_orders:,} orders, {num_trades:,} trades")
    return '\n'.join(lines)


def _format_output_directories():
    """Format output directory paths."""
    return (
        f"  Processed data: {config.PROCESSED_DIR}/\n"
        f"  Final outputs:  {config.OUTPUTS_DIR}/\n"
        f"  Aggregated:     {config.AGGREGATED_DIR}/"
    )


def print_execution_summary(data, runtime_config, execution_time):
    """Print pipeline execution summary."""
    print("\n" + "="*80)
    print("PIPELINE EXECUTION SUMMARY")
    print("="*80)
    print(f"Pipeline completed successfully")
    print(f"")
    
    print("Configuration:")
    print(f"  Date:    {runtime_config['date']}")
    
    securities = runtime_config.get('securities', [])
    if securities:
        if len(securities) == 1:
            sec = securities[0]['security']
            ticker_str = sec.ticker.upper() if sec.ticker else "Unknown"
            print(f"  Security: OrderbookID {sec.orderbookid} ({ticker_str})")
        else:
            print(f"  Securities: {len(securities)} processed")
    
    print(f"  Mode:    {'Parallel' if runtime_config['enable_parallel'] else 'Sequential'}")
    
    stats_engine = runtime_config.get('stats_engine')
    if stats_engine:
        print(f"  Statistics: {stats_engine.get_tier_name()}")
    
    print(f"")
    
    if data:
        total_orders = sum(len(df) for df in data['orders'].values())
        total_trades = sum(len(df) for df in data['trades'].values()) if data['trades'] else 0
        num_partitions = len(data['orders'])
        
        print(f"Total Centre Point Orders: {total_orders:,}")
        print(f"Total Trades: {total_trades:,}")
        print(f"Number of Partitions: {num_partitions}")
    
    print(f"Execution Time: {execution_time:.2f} seconds")
    
    if data:
        print("\nPartition Breakdown:")
        print(_format_partition_breakdown(data))
    
    print("\nOutput files:")
    print(_format_output_directories())
    print("="*80)

# ============================================================================
# Section 10 - pipeline/pipeline_stages.py (Stage 1+2 portions)
# ============================================================================
"""Pipeline stage execution functions."""

from pathlib import Path
# (consolidated) import config.config as config
# (consolidated) import pipeline.data_processor as dp
# (consolidated) import pipeline.partition_processor as pp
# (deferred to aggregate.py) import pipeline.execution_comparison as ec
# (deferred to report.py) import analysis.sweep_execution_analyzer as sea
# (deferred to report.py) import analysis.unmatched_analyzer as uma
# (deferred to report.py) import analysis.volume_analyzer as va
# (deferred to report.py) import aggregation.aggregate_sweep_results as agg
# (deferred to report.py) import aggregation.analyze_aggregated_results as analyze
# (deferred to report.py) import aggregation.aggregate_volume_analysis as vol_agg


def extract_and_prepare_data(input_files):
    """Extract orders, trades, reference data, order states, and execution times."""
    print("\n" + "="*80)
    print("STAGE 1: DATA EXTRACTION & PREPARATION (Steps 1-6)")
    print("="*80)
    
    orders_by_partition = dp.extract_orders(
        input_files['orders'], 
        config.PROCESSED_DIR, 
        config.CENTRE_POINT_ORDER_TYPES, 
        config.CHUNK_SIZE
    )
    
    if not orders_by_partition:
        print("\nNo Centre Point orders found. Exiting.")
        return None
    
    trades_by_partition = dp.extract_trades(
        input_files['trades'], 
        orders_by_partition, 
        config.PROCESSED_DIR, 
        config.CHUNK_SIZE
    )
    
    reference_results = dp.process_reference_data(
        config.RAW_FOLDERS,
        config.PROCESSED_DIR,
        orders_by_partition
    )
    
    nbbo_by_partition = reference_results.get('nbbo', {})
    
    order_states_by_partition = dp.get_orders_state(
        orders_by_partition, 
        config.PROCESSED_DIR
    )
    
    last_execution_by_partition = dp.extract_last_execution_times(
        orders_by_partition, 
        trades_by_partition, 
        config.PROCESSED_DIR
    )
    
    return {
        'orders': orders_by_partition,
        'trades': trades_by_partition,
        'order_states': order_states_by_partition,
        'last_execution': last_execution_by_partition,
        'nbbo': nbbo_by_partition,
        'reference': reference_results,
        'partition_keys': list(orders_by_partition.keys()),
    }


def run_simulations_and_lob(data, enable_parallel):
    """Run simulations and create LOB states."""
    _print_stage_2_header(data, enable_parallel)
    
    if enable_parallel and len(data['partition_keys']) > 1:
        return _run_parallel_processing(data)
    else:
        return _run_sequential_processing(data)


def _print_stage_2_header(data, enable_parallel):
    """Print Stage 2 header with mode information."""
    print(f"\n{'='*80}")
    print(f"STAGE 2: SIMULATION & LOB STATES (Steps 7-12)")
    print(f"{'='*80}")
    
    if enable_parallel and len(data['partition_keys']) > 1:
        print(f"PARALLEL PROCESSING MODE")
        print(f"Using {config.MAX_PARALLEL_WORKERS} workers for {len(data['partition_keys'])} partitions")
    else:
        print(f"SEQUENTIAL PROCESSING MODE")
        print(f"Processing {len(data['partition_keys'])} partition(s) sequentially")


def _run_parallel_processing(data):
    """Execute Stage 2 in parallel mode."""
    return pp.process_partitions_parallel(
        data['partition_keys'],
        config.PROCESSED_DIR,
        config.OUTPUTS_DIR,
        config.MAX_PARALLEL_WORKERS
    )


def _run_sequential_processing(data):
    """Execute Stage 2 in sequential mode."""
    simulation_results = _run_simulation_and_metrics(data)
    _run_trade_comparison(data, simulation_results)
    return simulation_results


def _run_simulation_and_metrics(data):
    """Run simulation and calculate metrics (Steps 7-8)."""
    simulation_results_by_partition = pp.simulate_sweep_matching_sequential(
        data['orders'],
        data['order_states'],
        data['last_execution'],
        data['nbbo'],
        config.OUTPUTS_DIR,
        reference_results=data.get('reference'),
    )

    pp.calculate_simulated_metrics_sequential(
        data['orders'],
        simulation_results_by_partition,
        config.PROCESSED_DIR,
        config.OUTPUTS_DIR,
        order_states=data['order_states'],
    )

    return simulation_results_by_partition


def _run_trade_comparison(data, simulation_results):
    """Compare real vs simulated trades and generate reports (Steps 11-12)."""
    real_trade_metrics = mg.calculate_real_trade_metrics(
        data['trades'],
        data['orders'],
        config.PROCESSED_DIR
    )
    
    if real_trade_metrics:
        trade_comparison = mg.compare_real_vs_simulated_trades(
            real_trade_metrics,
            simulation_results,
            config.OUTPUTS_DIR
        )
        
        mg.generate_trade_comparison_reports(
            trade_comparison,
            config.OUTPUTS_DIR,
            include_accuracy_summary=False
        )


def run_stage_2_simulation(data, enable_parallel):
    """STAGE 2: Run simulation only (Step 7)."""
    _print_stage_2_simulation_header(data, enable_parallel)
    
    if enable_parallel and len(data['partition_keys']) > 1:
        return pp.process_partitions_parallel_stage_2(
            data['partition_keys'],
            config.PROCESSED_DIR,
            config.OUTPUTS_DIR,
            config.MAX_PARALLEL_WORKERS
        )
    else:
        return pp.simulate_sweep_matching_sequential(
            data['orders'],
            data['order_states'],
            data['last_execution'],
            data['nbbo'],
            config.OUTPUTS_DIR
        )


def run_stage_3_calculate_metrics(data, enable_parallel):
    """STAGE 3: Calculate metrics for both real and simulated trades (Steps 8-9)."""
    _print_stage_3_metrics_header(data, enable_parallel)
    
    if enable_parallel and len(data['partition_keys']) > 1:
        return pp.process_partitions_parallel_stage_3(
            data['partition_keys'],
            config.PROCESSED_DIR,
            config.OUTPUTS_DIR,
            config.MAX_PARALLEL_WORKERS
        )
    else:
        # Step 8: Calculate real trade metrics FIRST (ground truth)
        print("\n[8/11] Calculating real trade metrics...")
        real_trade_metrics = ec.calculate_real_trade_metrics(
            data['trades'],
            data['orders'],
            config.PROCESSED_DIR
        )
        
        # Save real metrics to disk for Stage 4
        import utils.file_utils as fu
        for partition_key, metrics_data in real_trade_metrics.items():
            real_order_metrics = metrics_data['order_metrics']
            fu.save_trade_metrics(real_order_metrics, None, config.OUTPUTS_DIR, partition_key)
        
        # Step 9: Calculate simulated trade metrics SECOND
        print("\n[9/11] Calculating simulated trade metrics...")
        simulation_results = _load_simulation_results(data['partition_keys'])
        
        # For each partition, aggregate simulated trades and save metrics
        for partition_key in simulation_results.keys():
            # Load simulated metrics that were just calculated
            simulated_trades = simulation_results[partition_key]['simulated_trades']
            order_summary = simulation_results[partition_key]['order_summary']
            
            # Load orders for arrival NBBO
            partition_dir = fu.get_partition_dir(config.PROCESSED_DIR, partition_key)
            orders_before = fu.load_orders_before(partition_dir)
            
            # Aggregate simulated trades to get per-order metrics
            sim_aggregated = ec._aggregate_simulated_trades_per_order(
                simulated_trades,
                order_summary,
                orders_before
            )
            
            # Save simulated metrics to match real metrics format
            # Use fu.save_trade_metrics to save to simulated_trade_metrics.csv
            from pathlib import Path
            output_partition_dir = Path(config.OUTPUTS_DIR) / partition_key
            output_partition_dir.mkdir(parents=True, exist_ok=True)
            sim_metrics_path = output_partition_dir / 'simulated_trade_metrics.parquet'
            safe_write_csv(sim_aggregated, sim_metrics_path, create_dirs=False)
            print(f"  Saved simulated metrics for {partition_key}: {len(sim_aggregated)} orders")
        
        return {
            'real_metrics': real_trade_metrics,
            'simulation_results': simulation_results
        }


def run_stage_4_comparison(data, enable_parallel):
    """STAGE 4: Compare real vs simulated metrics (Step 10)."""
    _print_stage_4_comparison_header(data, enable_parallel)
    
    # Load pre-calculated metrics from disk
    print("\n[10/11] Loading and comparing metrics...")
    
    partition_keys = data['partition_keys']
    real_metrics = ec.load_real_metrics(config.OUTPUTS_DIR, partition_keys)
    simulation_results = _load_simulation_results(partition_keys)
    
    if not real_metrics or not simulation_results:
        print("  ✗ Cannot compare: missing metrics files. Run stages 2-3 first.")
        return None
    
    # Compare and generate reports
    trade_comparison = ec.compare_real_vs_simulated_trades(
        real_metrics,
        simulation_results,
        config.OUTPUTS_DIR
    )
    
    ec.generate_trade_comparison_reports(
        trade_comparison,
        config.OUTPUTS_DIR,
        include_accuracy_summary=False
    )
    
    return trade_comparison


def _load_simulation_results(partition_keys):
    """Load simulation results from disk for given partitions."""
    import utils.file_utils as fu
    
    simulation_results = {}
    for partition_key in partition_keys:
        # Load simulated trades
        partition_dir = fu.get_partition_dir(config.PROCESSED_DIR, partition_key)
        simulated_trades = fu.load_simulation_trades(partition_dir)
        
        # Load order summary
        output_partition_dir = fu.get_partition_dir(config.OUTPUTS_DIR, partition_key)
        order_summary = fu.load_simulation_order_summary(output_partition_dir)
        
        if simulated_trades is not None and order_summary is not None:
            simulation_results[partition_key] = {
                'simulated_trades': simulated_trades,
                'order_summary': order_summary
            }
    
    return simulation_results


def _print_stage_2_simulation_header(data, enable_parallel):
    """Print Stage 2 header for simulation."""
    print(f"\n{'='*80}")
    print(f"STAGE 2: SIMULATION ONLY (Step 7)")
    print(f"{'='*80}")
    
    if enable_parallel and len(data['partition_keys']) > 1:
        print(f"PARALLEL PROCESSING MODE")
        print(f"Using {config.MAX_PARALLEL_WORKERS} workers for {len(data['partition_keys'])} partitions")
    else:
        print(f"SEQUENTIAL PROCESSING MODE")
        print(f"Processing {len(data['partition_keys'])} partition(s) sequentially")


def _print_stage_3_metrics_header(data, enable_parallel):
    """Print Stage 3 header for metrics calculation."""
    print(f"\n{'='*80}")
    print(f"STAGE 3: CALCULATE METRICS (Steps 8-9)")
    print(f"{'='*80}")
    print(f"  Step 8: Real trade metrics (ground truth)")
    print(f"  Step 9: Simulated trade metrics")
    
    if enable_parallel and len(data['partition_keys']) > 1:
        print(f"\nPARALLEL PROCESSING MODE")
        print(f"Using {config.MAX_PARALLEL_WORKERS} workers for {len(data['partition_keys'])} partitions")
    else:
        print(f"\nSEQUENTIAL PROCESSING MODE")
        print(f"Processing {len(data['partition_keys'])} partition(s) sequentially")


def _print_stage_4_comparison_header(data, enable_parallel):
    """Print Stage 4 header for metrics comparison."""
    print(f"\n{'='*80}")
    print(f"STAGE 4: METRICS COMPARISON (Step 10)")
    print(f"{'='*80}")


def run_stage_5_per_security_analysis(processed_dir, outputs_dir, partition_keys, stats_engine):
    """STAGE 5: Run sweep execution and unmatched order analysis plus volume analysis."""
    _print_stage_5_header()
    
    _run_sweep_execution_analysis(processed_dir, outputs_dir, partition_keys, stats_engine)
    _run_unmatched_orders_analysis(processed_dir, outputs_dir, partition_keys)
    volume_summary = _run_volume_analysis(outputs_dir, partition_keys, stats_engine)
    
    print(f"\n✓ Stage 5 complete (per-security analysis + volume analysis)")
    return volume_summary


# Backward compatibility alias
run_per_security_analysis = run_stage_5_per_security_analysis


def _print_stage_5_header():
    """Print Stage 5 header."""
    print(f"\n{'='*80}")
    print(f"STAGE 5: PER-SECURITY ANALYSIS (Steps 11-12 + Volume Analysis)")
    print(f"{'='*80}")


def _run_sweep_execution_analysis(processed_dir, outputs_dir, partition_keys, stats_engine):
    """Run sweep order execution analysis (Step 13)."""
    print("\n[Step 13] Analyzing sweep order execution...")
    sea.analyze_sweep_execution(
        processed_dir,
        outputs_dir,
        partition_keys,
        stats_engine=stats_engine
    )


def _run_unmatched_orders_analysis(processed_dir, outputs_dir, partition_keys):
    """Run unmatched orders analysis (Step 14)."""
    print("\n[Step 14] Analyzing unmatched orders...")
    uma.analyze_unmatched_orders(
        processed_dir,
        outputs_dir,
        partition_keys
    )


def _run_volume_analysis(outputs_dir, partition_keys, stats_engine):
    """Run volume-based analysis."""
    print("\n[Volume Analysis] Analyzing execution by order volume...")
    return va.analyze_by_volume(
        outputs_dir,
        partition_keys,
        method='quartile',
        stats_engine=stats_engine
    )


def run_stage_6_aggregation(runtime_config):
    """STAGE 6: Aggregate sweep and volume results across all securities."""
    _print_stage_6_header()
    
    stats_engine = runtime_config.get('stats_engine')
    
    # Step 1-2: Aggregate and save sweep results
    aggregated_df = _aggregate_sweep_results()
    if aggregated_df is None:
        return False
    
    _save_sweep_results(aggregated_df)
    
    # Step 3: Statistical analysis
    _run_statistical_analysis(stats_engine)
    
    # Step 4: Volume aggregation
    _aggregate_volume_results(stats_engine)
    
    print("\n✓ Stage 6 complete (cross-security aggregation + volume analysis)")
    return True


# Backward compatibility alias
run_cross_security_aggregation = run_stage_6_aggregation


def _print_stage_6_header():
    """Print Stage 6 header."""
    print(f"\n{'='*80}")
    print(f"STAGE 6: CROSS-SECURITY AGGREGATION")
    print(f"{'='*80}")
    print("\n[Stage 6] Aggregating results across all securities...")


def _aggregate_sweep_results():
    """Merge all sweep_order_comparison_detailed.csv files."""
    print("  Step 1: Merging sweep_order_comparison_detailed.csv files...")
    aggregated_df = agg.aggregate_results(config.OUTPUTS_DIR)
    
    if aggregated_df is None:
        print("  ✗ No results found to aggregate")
    
    return aggregated_df


def _save_sweep_results(aggregated_df):
    """Save aggregated sweep results."""
    print("  Step 2: Saving aggregated dataset...")
    output_path = config.AGGREGATED_DIR + '/aggregated_sweep_comparison.csv'
    agg.save_aggregated_results(aggregated_df, output_path)


def _run_statistical_analysis(stats_engine):
    """Run statistical analysis on aggregated data."""
    print("  Step 3: Running statistical analysis...")
    analyze.main(stats_engine)


def _aggregate_volume_results(stats_engine):
    """Aggregate volume analysis across securities."""
    print("  Step 4: Aggregating volume analysis across securities...")
    try:
        vol_agg.main(stats_engine)
        print("  ✓ Volume analysis aggregation complete")
    except Exception as e:
        print(f"  ⚠ Volume analysis aggregation skipped: {str(e)}")


def _load_partition_keys_from_disk(security, processed_dir):
    """Load partition keys from processed directory for a security."""
    processed_path = Path(processed_dir)
    partition_keys = []
    
    for date_dir in processed_path.iterdir():
        if date_dir.is_dir() and not date_dir.name.startswith('.'):
            for orderbook_dir in date_dir.iterdir():
                if orderbook_dir.is_dir() and not orderbook_dir.name.startswith('.'):
                    if int(orderbook_dir.name) == security.orderbookid:
                        partition_key = f"{date_dir.name}/{orderbook_dir.name}"
                        partition_keys.append(partition_key)
    
    return partition_keys


def _execute_stage_1(sec_info, stages):
    """Execute Stage 1 for a security if needed."""
    if stages is None or 1 in stages:
        data = extract_and_prepare_data(sec_info['input_files'])
        if data is None:
            return None, []
        return data, data['partition_keys']
    return None, []


def _execute_stage_2(data, runtime_config, stages):
    """Execute Stage 2 (Simulation) for a security if needed."""
    if stages is None or 2 in stages:
        if data is None:
            print(f"\n✗ Stage 2 requires Stage 1 data. Please run Stage 1 first or run both together.")
            return
        
        run_stage_2_simulation(data, runtime_config['enable_parallel'])


def _execute_stage_3(data, runtime_config, stages):
    """Execute Stage 3 (Metrics Calculation) for a security if needed."""
    if stages is None or 3 in stages:
        if data is None:
            print(f"\n✗ Stage 3 requires Stage 1 data. Please run Stage 1 first or run both together.")
            return
        
        run_stage_3_calculate_metrics(data, runtime_config['enable_parallel'])


def _execute_stage_4(data, runtime_config, stages):
    """Execute Stage 4 (Metrics Comparison) for a security if needed."""
    if stages is None or 4 in stages:
        if data is None:
            print(f"\n✗ Stage 4 requires Stage 1 data. Please run Stage 1 first or run both together.")
            return
        
        run_stage_4_comparison(data, runtime_config['enable_parallel'])


def _execute_stage_5(security, data, runtime_config, stages):
    """Execute Stage 5 (Per-Security Analysis) for a security if needed."""
    if stages is None or 5 in stages:
        if data is None:
            partition_keys = _load_partition_keys_from_disk(security, config.PROCESSED_DIR)
            if not partition_keys:
                print(f"✗ Stage 5 requires processed data from Stages 1-4 for OrderbookID {security.orderbookid}")
                return []
        else:
            partition_keys = data['partition_keys']
        
        run_stage_5_per_security_analysis(config.PROCESSED_DIR, config.OUTPUTS_DIR, partition_keys, runtime_config['stats_engine'])
        return partition_keys
    
    return []


def _process_single_security(sec_info, runtime_config):
    """Process all stages for a single security."""
    security = sec_info['security']
    stages = runtime_config['stages']
    
    _print_security_header(security)
    
    # Execute Stage 1: Data extraction
    data, partition_keys_s1 = _execute_stage_1(sec_info, stages)
    if data is None and _should_run_stage(stages, 1):
        _print_stage_failure(security.orderbookid, 1, "no Centre Point orders found")
        return None, []
    _print_stage_success_if_ran(stages, 1, security.orderbookid)
    
    # Execute Stage 2: Simulation
    _execute_stage_2(data, runtime_config, stages)
    _print_stage_success_if_ran(stages, 2, security.orderbookid)
    
    # Execute Stage 3: Calculate metrics
    _execute_stage_3(data, runtime_config, stages)
    _print_stage_success_if_ran(stages, 3, security.orderbookid)
    
    # Execute Stage 4: Compare metrics
    _execute_stage_4(data, runtime_config, stages)
    _print_stage_success_if_ran(stages, 4, security.orderbookid)
    
    # Execute Stage 5: Per-security analysis
    partition_keys_s5 = _execute_stage_5(security, data, runtime_config, stages)
    _print_stage_success_if_ran(stages, 5, security.orderbookid)
    
    # Combine partition keys
    all_partition_keys = _combine_partition_keys(partition_keys_s1, partition_keys_s5)
    
    return data, all_partition_keys


def _print_security_header(security):
    """Print processing header for a security."""
    ticker_str = security.ticker.upper() if security.ticker else "Unknown"
    print(f"\n{'='*80}")
    print(f"Processing OrderbookID {security.orderbookid} ({ticker_str})")
    print(f"{'='*80}")


def _should_run_stage(stages, stage_num):
    """Check if a stage should be run."""
    return stages is None or stage_num in stages


def _print_stage_success_if_ran(stages, stage_num, orderbookid):
    """Print stage success message if stage was executed."""
    if _should_run_stage(stages, stage_num):
        print(f"\n✓ Stage {stage_num} complete for OrderbookID {orderbookid}")


def _print_stage_failure(orderbookid, stage_num, reason):
    """Print stage failure message."""
    print(f"\n✗ Stage {stage_num} failed for OrderbookID {orderbookid} - {reason}")


def _combine_partition_keys(*key_lists):
    """Combine multiple partition key lists."""
    combined = []
    for keys in key_lists:
        if keys:
            combined.extend(keys)
    return combined


def execute_pipeline_stages(runtime_config):
    """Execute all pipeline stages based on runtime config."""
    stages = runtime_config['stages']
    securities = runtime_config.get('securities', [])
    
    data = None
    all_partition_keys = []
    
    for sec_info in securities:
        sec_data, sec_partition_keys = _process_single_security(sec_info, runtime_config)
        if sec_data:
            data = sec_data
        all_partition_keys.extend(sec_partition_keys)
    
    if stages is None or 6 in stages:
        run_stage_6_aggregation(runtime_config)
        print(f"\n✓ Stage 6 complete")
    
    return data, all_partition_keys

# ============================================================================
# Section 11 - pipeline/pipeline_config.py + main.py
# ============================================================================
"""Pipeline configuration and CLI argument handling."""

import argparse
from pathlib import Path
# (consolidated) import config.config as config
# (replaced by inline auto_discover) from discovery.security_discovery import SecurityDiscovery
# (cut: stats_layer dropped) from utils.statistics_layer import StatisticsEngine


def parse_arguments():
    """Parse CLI arguments with config.py fallback."""
    parser = argparse.ArgumentParser(
        description='Centre Point Sweep Order Matching Pipeline - 6 Stage Pipeline',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # Security selection (mutually exclusive)
    security_group = parser.add_mutually_exclusive_group()
    security_group.add_argument('--ticker', type=str, default=None,
                        help=f'Ticker symbol (legacy, default: {config.TICKER})')
    security_group.add_argument('--orderbookid', type=int, default=None,
                        help='OrderbookID to process')
    security_group.add_argument('--auto-discover', action='store_true',
                        help='Auto-discover and process all valid securities for the date')
    
    parser.add_argument('--date', type=str, default=None,
                        help=f'Date in YYYYMMDD format (default: {config.DATE})')
    
    # Discovery options
    parser.add_argument('--list-securities', action='store_true',
                        help='List all available securities for the date and exit')
    parser.add_argument('--list-dates', action='store_true',
                        help='List all available dates in raw data and exit')
    parser.add_argument('--min-orders', type=int, default=config.MIN_ORDERS_THRESHOLD,
                        help=f'Minimum orders threshold for valid security (default: {config.MIN_ORDERS_THRESHOLD})')
    parser.add_argument('--min-trades', type=int, default=config.MIN_TRADES_THRESHOLD,
                        help=f'Minimum trades threshold for valid security (default: {config.MIN_TRADES_THRESHOLD})')
    
    # Pipeline stages
    parser.add_argument('--stage', type=int, choices=[1, 2, 3, 4, 5, 6], action='append',
                        help='Pipeline stage(s) to run (can specify multiple): '
                             '1=extraction, 2=simulation, 3=metrics, 4=comparison, 5=analysis, 6=aggregation')
    
    # Processing options
    parser.add_argument('--parallel', action='store_true',
                        help='Enable parallel processing for Stage 2 (overrides config)')
    parser.add_argument('--sequential', action='store_true',
                        help='Force sequential processing for Stage 2 (overrides config)')
    parser.add_argument('--processing-mode', type=str, choices=['file', 'memory'],
                        default=None,
                        help='Processing mode: "file" writes intermediate partitions to disk '
                             '(resumable), "memory" keeps everything in RAM (faster, no disk I/O). '
                             f'Default from config: {config.PROCESSING_MODE}')
    
    # Statistical testing options
    stats_group = parser.add_mutually_exclusive_group()
    stats_group.add_argument('--enable-stats', action='store_true',
                        help='Enable statistical tests (t-tests, p-values, confidence intervals)')
    stats_group.add_argument('--disable-stats', action='store_true',
                        help='Disable statistical tests (only descriptive statistics)')
    
    return parser.parse_args()


def _handle_list_operations(args, discovery):
    """Handle --list-dates and --list-securities operations."""
    if args.list_dates:
        dates = discovery.get_available_dates()
        print("\nAvailable dates in raw data:")
        if dates:
            for date in dates:
                print(f"  - {date}")
        else:
            print("  No data files found")
        print()
        exit(0)
    
    date = args.date or config.DATE
    
    if args.list_securities:
        if not date:
            print("Error: --list-securities requires --date argument")
            exit(1)
        discovery.print_summary(date)
        exit(0)
    
    return date


def _resolve_stages_to_run(args):
    """Determine which stages to run from CLI args."""
    return args.stage if args.stage else None


def _determine_parallel_mode(args):
    """Determine parallel processing mode from CLI args and config."""
    if args.parallel:
        return True
    elif args.sequential:
        return False
    else:
        return config.ENABLE_PARALLEL_PROCESSING


def _create_stats_engine(args):
    """Create statistics engine based on CLI args and config."""
    if args.enable_stats:
        enable_stats = True
    elif args.disable_stats:
        enable_stats = False
    else:
        enable_stats = config.ENABLE_STATISTICAL_TESTS
    
    # StatisticsEngine dropped from lean port; downstream gracefully handles None.
    return None


def _select_securities_from_args(args, discovery, date):
    """Select securities to process based on CLI arguments."""
    securities_to_process = []
    
    if args.auto_discover:
        if not date:
            raise ValueError("--auto-discover requires --date argument")
        
        valid_securities = discovery.get_valid_securities(date)
        if not valid_securities:
            raise ValueError(f"No valid securities found for date {date}")
        
        securities_to_process = valid_securities
        print(f"\nAuto-discovered {len(securities_to_process)} valid securities:")
        for sec in securities_to_process:
            print(f"  - {sec}")
    
    elif args.orderbookid:
        if not date:
            raise ValueError("--orderbookid requires --date argument")
        
        all_securities = discovery.discover_securities_for_date(date)
        security = next((s for s in all_securities if s.orderbookid == args.orderbookid), None)
        
        if not security:
            raise ValueError(f"OrderbookID {args.orderbookid} not found for date {date}")
        
        if not (security.in_orders and security.in_trades):
            raise ValueError(f"OrderbookID {args.orderbookid} missing order or trade data")
        
        securities_to_process = [security]
    
    elif args.ticker:
        if not date:
            raise ValueError("--ticker requires --date argument")
        
        orderbookid = discovery.get_orderbookid_from_ticker(args.ticker, date)
        if not orderbookid:
            raise ValueError(f"Ticker {args.ticker} not found for date {date}")
        
        all_securities = discovery.discover_securities_for_date(date)
        security = next((s for s in all_securities if s.orderbookid == orderbookid), None)
        
        if not security:
            raise ValueError(f"Security not found for ticker {args.ticker}")
        
        securities_to_process = [security]
    
    else:
        ticker = config.TICKER
        if not date:
            raise ValueError("Date is required for processing")
        
        orderbookid = discovery.get_orderbookid_from_ticker(ticker, date)
        if orderbookid:
            all_securities = discovery.discover_securities_for_date(date)
            security = next((s for s in all_securities if s.orderbookid == orderbookid), None)
            if security:
                securities_to_process = [security]
            else:
                raise ValueError(f"Security not found for config ticker {ticker}")
        else:
            raise ValueError(f"Config ticker {ticker} not found for date {date}. Use --orderbookid or --ticker")
    
    return securities_to_process


def _build_security_file_mappings(securities, date, stages):
    """Build input file mappings for each security."""
    securities_with_files = []
    
    if stages is None or any(s in [1, 2, 3] for s in stages):
        for security in securities:
            input_files = config.get_input_files(ticker=security.ticker, date=date)
            
            if stages is None or 1 in stages:
                config.validate_input_files(input_files)
            
            securities_with_files.append({
                'security': security,
                'input_files': input_files
            })
    
    return securities_with_files


def build_runtime_config(args):
    """Build runtime config from CLI args and config."""
    discovery = SecurityDiscovery(
        raw_data_dir=config.PROJECT_ROOT / 'data/raw',
        min_orders=args.min_orders,
        min_trades=args.min_trades,
    )
    
    date = _handle_list_operations(args, discovery)
    stages = _resolve_stages_to_run(args)
    enable_parallel = _determine_parallel_mode(args)
    stats_engine = _create_stats_engine(args)

    if args.processing_mode:
        config.PROCESSING_MODE = args.processing_mode
    
    securities_to_process = []
    if stages is None or any(s in [1, 2, 3] for s in stages):
        securities_to_process = _select_securities_from_args(args, discovery, date)
    
    if stages and any(s in [1, 2, 3] for s in stages):
        if not securities_to_process:
            raise ValueError("Stages 1-3 require security specification (--ticker, --orderbookid, or --auto-discover)")
    
    securities_with_files = _build_security_file_mappings(securities_to_process, date, stages)
    
    return {
        'date': date,
        'securities': securities_with_files,
        'enable_parallel': enable_parallel,
        'stages': stages,
        'stats_engine': stats_engine,
    }


def setup_directories():
    """Create output directories if they don't exist."""
    Path(config.PROCESSED_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.OUTPUTS_DIR).mkdir(parents=True, exist_ok=True)

"""Centre Point Sweep Order Matching Pipeline - Main Orchestrator"""

import time
# (consolidated) from pipeline.pipeline_config import parse_arguments, build_runtime_config, setup_directories
# (consolidated) from pipeline.pipeline_output import print_pipeline_header, print_execution_summary
# (consolidated) from pipeline.pipeline_stages import execute_pipeline_stages


def main():
    """Main pipeline: 4-stage architecture with --stage argument support."""
    start_time = time.time()
    
    # Parse CLI arguments
    args = parse_arguments()
    
    # Build runtime configuration (CLI overrides config)
    runtime_config = build_runtime_config(args)
    
    # Print header and configuration
    print_pipeline_header(runtime_config)
    
    # Setup directories
    setup_directories()
    
    # Execute pipeline stages
    data, all_partition_keys = execute_pipeline_stages(runtime_config)
    
    # Print summary
    execution_time = time.time() - start_time
    print_execution_summary(data, runtime_config, execution_time)


# ============================================================================
# Section 12 - New flat-port multi-date/multi-ticker CLI
# ============================================================================

import duckdb as _duckdb
from pathlib import Path as _Path
from types import SimpleNamespace as _NS
import sys as _sys
import argparse as _argparse
import csv as _csv
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


class SecurityDiscovery:  # minimal shim replacing src/discovery/security_discovery.py (338 LOC)
    """Inline shim — only the methods used by build_runtime_config in our CLI flow."""

    def __init__(self, raw_data_dir, min_orders=100, min_trades=10):
        self.raw_data_dir = _Path(raw_data_dir)
        self.min_orders = min_orders
        self.min_trades = min_trades

    def get_orderbookid_from_ticker(self, ticker, date):
        f = self.raw_data_dir / "orders" / f"{ticker}_{date}_orders.csv"
        with open(f) as fh:
            row = next(_csv.DictReader(fh), None)
        if row is None:
            raise ValueError(f"No rows in {f}")
        # Raw CSV uses 'security_code' (canonical name 'orderbookid' after
        # normalisation). Try both to be schema-tolerant.
        for k in ("orderbookid", "security_code", "OrderBookId", "Id"):
            if k in row and row[k]:
                return int(row[k])
        raise KeyError(f"No orderbookid-like column in {f}: cols={list(row)}")

    def get_available_dates(self):
        dates = set()
        for p in (self.raw_data_dir / "orders").glob("*_orders.csv"):
            parts = p.stem.split("_")
            if len(parts) >= 3 and parts[-2].isdigit() and len(parts[-2]) == 8:
                dates.add(parts[-2])
        return sorted(dates)

    def print_summary(self, date):
        return None

    def get_valid_securities(self, date):
        out = []
        for p in (self.raw_data_dir / "orders").glob(f"*_{date}_orders.csv"):
            ticker = p.stem.replace(f"_{date}_orders", "")
            try:
                obid = self.get_orderbookid_from_ticker(ticker, date)
                # Caller code does sec.orderbookid / sec.ticker — return objects.
                out.append(_NS(ticker=ticker, orderbookid=obid,
                               order_count=0, trade_count=0))
            except Exception:
                continue
        return out

    def discover_securities_for_date(self, date):
        return self.get_valid_securities(date)


def auto_discover_partitions(dates):
    """For each date, find tickers with >= MIN_ORDERS_THRESHOLD orders.
    Returns list of (date, ticker) partition keys."""
    con = _duckdb.connect()
    out = []
    for date in dates:
        pattern = str(_Path(config.RAW_DIR) / "orders" / f"*_{date}_orders.csv")
        try:
            rows = con.execute(
                "SELECT regexp_extract(filename, '([a-z]+)_" + date + "_orders\\.csv', 1) AS ticker, "
                "COUNT(*) AS n FROM read_csv_auto(?, filename=True) GROUP BY ticker",
                [pattern]
            ).fetchall()
        except Exception:
            rows = []
        out.extend((date, t) for t, n in rows if n >= config.MIN_ORDERS_THRESHOLD)
    return out


def resolve_partitions(dates, tickers):
    """If tickers provided, return cartesian product filtered to existing raw files.
    Otherwise auto-discover."""
    if tickers is None:
        return auto_discover_partitions(dates)
    return [(d, t) for d in dates for t in tickers
            if (_Path(config.RAW_DIR) / "orders" / f"{t}_{d}_orders.csv").exists()]


def _make_legacy_args(date, ticker, stages, workers):
    """Build a Namespace that build_runtime_config() will accept."""
    ns = _argparse.Namespace()
    ns.ticker = ticker
    ns.orderbookid = None
    ns.date = date
    ns.auto_discover = False
    ns.list_dates = False
    ns.list_securities = False
    ns.min_orders = config.MIN_ORDERS_THRESHOLD
    ns.min_trades = config.MIN_TRADES_THRESHOLD
    ns.parallel = (workers is not None and workers > 1) or config.ENABLE_PARALLEL_PROCESSING
    ns.sequential = not ns.parallel
    ns.workers = workers
    ns.enable_stats = False
    ns.disable_stats = True
    ns.simple_stats = True
    ns.stage = list(stages)
    ns.processing_mode = None
    ns.disable_resting = False
    ns.disable_lit_resting = False
    return ns


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
