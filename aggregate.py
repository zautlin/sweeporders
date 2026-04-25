"""sweeporders aggregate.py - Stages 3+4 (metrics + real-vs-sim comparison).

Flat consolidation; reads data/processed/ and writes data/outputs/.

Run: python aggregate.py --dates 20240505 --tickers cba
     python aggregate.py --dates 20240505,20240905 --auto-tickers --workers 4
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
# (lean port) legacy sys.path hack removed; flat layout finds config.py natively.
# (consolidated) import config.config as config


def get_partition_dir(base_dir, partition_key):
    """Build partition directory path from partition key (date/security)."""
    date, security = partition_key.split('/')
    return Path(base_dir) / date / security


def safe_read_csv(filepath, required=True, compression='infer', **kwargs):
    """Read CSV with existence check and error handling."""
    filepath = Path(filepath)

    if not filepath.exists():
        if required:
            raise FileNotFoundError(f"Required file not found: {filepath}")
        return None

    if config.USE_DUCKDB_IO:
        try:
            pass  # (consolidated) inline calls to get_conn/duck_to_polars
            rel = get_conn().execute(f"SELECT * FROM read_csv_auto('{filepath}')")
            return duck_to_polars(rel).to_pandas()
        except Exception as e:
            raise IOError(f"Error reading {filepath}: {e}")

    try:
        return pd.read_csv(filepath, compression=compression, **kwargs)
    except pd.errors.EmptyDataError:
        return None
    except Exception as e:
        raise IOError(f"Error reading {filepath}: {e}")


def safe_write_csv(df, filepath, compression=None, create_dirs=True, **kwargs):
    """Write CSV with directory creation and error handling.

    Accepts both pandas DataFrames and Polars DataFrames.
    """
    filepath = Path(filepath)

    if create_dirs:
        filepath.parent.mkdir(parents=True, exist_ok=True)

    try:
        if isinstance(df, pl.DataFrame):
            df.write_csv(filepath)
        else:
            df.to_csv(filepath, compression=compression, index=False, **kwargs)
    except Exception as e:
        raise IOError(f"Error writing {filepath}: {e}")


def query_partitions(base_dir, filename, where_sql="") -> pl.DataFrame:
    """Query one file across every date/orderbookid partition in a single DuckDB pass.

    Usage::

        # All sweep orders across all partitions
        df = query_partitions(PROCESSED_DIR, 'orders_before_matching.csv',
                              "WHERE exchangeordertype = 2048")

        # All real trade metrics for cross-security aggregation
        df = query_partitions(OUTPUTS_DIR, 'real_trade_metrics.csv')

        # Works on gzip files too (DuckDB auto-detects)
        df = query_partitions(PROCESSED_DIR, 'cp_trades_matched.csv.gz')

    Returns a Polars DataFrame with all rows union'd; column order normalised by name.
    """
    pass  # (consolidated) inline calls to get_conn/duck_to_polars
    glob_pattern = str(Path(base_dir) / '*' / '*' / filename)
    conn = get_conn()
    sql = f"SELECT * FROM read_csv_auto('{glob_pattern}', union_by_name=True) {where_sql}"
    return duck_to_polars(conn.execute(sql))


def load_orders_before(partition_dir):
    """Load orders_before_matching.csv from partition directory."""
    filepath = Path(partition_dir) / "orders_before_matching.csv"
    return safe_read_csv(filepath, required=False)


def load_orders_after(partition_dir):
    """Load orders_after_matching.csv from partition directory."""
    filepath = Path(partition_dir) / "orders_after_matching.csv"
    return safe_read_csv(filepath, required=False)


def load_trades_matched(partition_dir):
    """Load cp_trades_matched.csv.gz from partition directory."""
    filepath = Path(partition_dir) / "cp_trades_matched.csv.gz"
    return safe_read_csv(filepath, required=False, compression='gzip')


def load_trades_aggregated(partition_dir):
    """Load cp_trades_aggregated.csv.gz from partition directory."""
    filepath = Path(partition_dir) / "cp_trades_aggregated.csv.gz"
    return safe_read_csv(filepath, required=False, compression='gzip')


def load_last_execution(partition_dir):
    """Load last_execution_time.csv from partition directory."""
    filepath = Path(partition_dir) / "last_execution_time.csv"
    return safe_read_csv(filepath, required=False)


def load_nbbo(partition_dir):
    """Load nbbo.csv.gz from partition directory."""
    filepath = Path(partition_dir) / "nbbo.csv.gz"
    return safe_read_csv(filepath, required=False, compression='gzip')


def save_simulation_results(sim_results, output_dir, partition_key):
    """Save simulation outputs (order_summary and simulated_trades)."""
    partition_output_dir = Path(output_dir) / partition_key
    partition_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save order summary
    if 'order_summary' in sim_results and sim_results['order_summary'] is not None:
        safe_write_csv(
            sim_results['order_summary'],
            partition_output_dir / 'simulation_order_summary.csv',
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
            
            trades_filename = 'cp_trades_simulation.csv'
            safe_write_csv(
                simulated_trades,
                partition_processed_dir / trades_filename,
                compression=None,  # Uncompressed for easier access
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
            partition_processed_dir / 'cp_trades_simulation_resting.csv',
            compression=None,
            create_dirs=False,
        )

    resting_summary = sim_results.get('resting_summary')
    if resting_summary is not None and len(resting_summary) > 0:
        partition_output_dir = Path(output_dir) / partition_key
        partition_output_dir.mkdir(parents=True, exist_ok=True)
        safe_write_csv(
            resting_summary,
            partition_output_dir / 'resting_order_summary.csv',
            create_dirs=False,
        )


def save_orders_with_metrics(orders_with_metrics, output_dir, partition_key):
    """Save orders with simulated metrics."""
    partition_output_dir = Path(output_dir) / partition_key
    safe_write_csv(
        orders_with_metrics,
        partition_output_dir / 'orders_with_simulated_metrics.csv'
    )


def save_trade_comparison(comparison_df, accuracy_df, output_dir, partition_key):
    """Save trade-level comparison results."""
    partition_output_dir = Path(output_dir) / partition_key
    
    if comparison_df is not None and len(comparison_df) > 0:
        safe_write_csv(
            comparison_df,
            partition_output_dir / 'trade_level_comparison.csv'
        )
    
    if accuracy_df is not None and len(accuracy_df) > 0:
        safe_write_csv(
            accuracy_df,
            partition_output_dir / 'trade_accuracy_summary.csv'
        )


def save_trade_metrics(real_metrics_df, sim_metrics_df, output_dir, partition_key):
    """Save real and simulated trade metrics calculated in Stage 2 for Stage 3 reuse."""
    partition_output_dir = Path(output_dir) / partition_key
    
    if real_metrics_df is not None and len(real_metrics_df) > 0:
        safe_write_csv(
            real_metrics_df,
            partition_output_dir / 'real_trade_metrics.csv'
        )
    
    if sim_metrics_df is not None and len(sim_metrics_df) > 0:
        safe_write_csv(
            sim_metrics_df,
            partition_output_dir / 'simulated_trade_metrics.csv'
        )


def load_trade_metrics(output_dir, partition_key):
    """Load pre-calculated trade metrics from Stage 2 output directory."""
    partition_output_dir = Path(output_dir) / partition_key
    
    real_metrics_path = partition_output_dir / 'real_trade_metrics.csv'
    sim_metrics_path = partition_output_dir / 'simulated_trade_metrics.csv'
    
    real_metrics_df = safe_read_csv(real_metrics_path, required=False)
    sim_metrics_df = safe_read_csv(sim_metrics_path, required=False)
    
    return real_metrics_df, sim_metrics_df


def load_simulation_trades(partition_dir):
    """Load cp_trades_simulation.csv from partition processed directory."""
    filepath = Path(partition_dir) / "cp_trades_simulation.csv"
    return safe_read_csv(filepath, required=False)


def load_simulation_order_summary(partition_dir):
    """Load simulation_order_summary.csv from partition output directory."""
    filepath = Path(partition_dir) / "simulation_order_summary.csv"
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
        participants_file = self.processed_dir / 'participants.csv.gz'
        if not participants_file.exists():
            return None
        participants = pd.read_csv(participants_file)
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
        
        reference_file = self.processed_dir / 'reference.csv.gz'
        if not reference_file.exists():
            self.tick_size_tables[orderbookid] = None
            return None
        
        reference = pd.read_csv(reference_file)
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
        nbbo_file = Path(partition_dir) / 'nbbo.csv.gz'
        if not nbbo_file.exists():
            return 10
        nbbo = pd.read_csv(nbbo_file)
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
        orders_file = Path(partition_dir) / 'orders_before_matching.csv'
        if not orders_file.exists():
            return 10
        orders = pd.read_csv(orders_file, nrows=1000)
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
        
        reference_file = self.processed_dir / 'reference.csv.gz'
        if not reference_file.exists():
            self.price_limits[orderbookid] = None
            return None
        
        reference = pd.read_csv(reference_file)
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
        pass  # (consolidated) inline calls to get_conn/duck_to_polars
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
        pass  # (consolidated) inline calls to get_conn/duck_to_polars
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
            pass  # (consolidated) inline calls to get_conn/duck_to_polars
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

# Try to import scipy for backward compatibility
try:
    from scipy import stats as scipy_stats
    SCIPY_AVAILABLE = True
except ImportError:
    scipy_stats = None
    SCIPY_AVAILABLE = False


def calculate_simulated_metrics(all_orders, order_summary, simulated_trades):
    """Calculate simulated execution metrics for orders using unified calculator."""
    
    # Start with all orders
    result = all_orders.copy()
    
    # Check if order_summary is empty
    if order_summary.empty or simulated_trades.empty:
        # Return with zero metrics
        result['qty_filled'] = 0
        result['num_fills'] = 0
        result['fill_ratio'] = 0.0
        result['fill_status'] = 'Unfilled'
        result['vwap'] = 0.0
        result['total_execution_value'] = 0.0
        return result
    
    # Create order context for calculator (include arrival NBBO for metrics)
    # Note: All columns are normalized by Stage 1 (orderid, timestamp, etc.)
    order_context = result[['orderid', 'timestamp', 'side', 'quantity', 'price']].copy()
    
    # Arrival NBBO (from orders_after_matching.csv - needed for arrival-based metrics)
    if 'national_bid' in result.columns:
        order_context['national_bid'] = result['national_bid']
    if 'national_offer' in result.columns:
        order_context['national_offer'] = result['national_offer']
    
    # Calculate comprehensive metrics using unified calculator
    # Note: Simulated trades execute at midpoint by design (see sweep_simulator.py line 444)
    metrics_result = calculate_trade_metrics(
        trades_df=simulated_trades,
        orders_df=order_context,
        nbbo_df=None,
        filter_orderids=order_summary['orderid'].tolist() if 'orderid' in order_summary.columns else None,
        role_filter='aggressor',  # Only aggressor rows for sweep orders
        prefix='',
        is_simulated=True  # Flag for simulated-specific logic
    )
    
    per_order_metrics = metrics_result['per_order_metrics']
    
    if per_order_metrics.empty:
        # Return with zero metrics
        result['qty_filled'] = 0
        result['num_fills'] = 0
        result['fill_ratio'] = 0.0
        result['fill_status'] = 'Unfilled'
        result['vwap'] = 0.0
        result['total_execution_value'] = 0.0
        return result
    
    # Merge with all_orders
    result = result.merge(
        per_order_metrics,
        on='orderid',
        how='left'
    )
    
    # Fill NaN for orders without simulation matches
    metric_cols = ['qty_filled', 'num_fills', 'fill_ratio', 'vwap', 'total_execution_value']
    for col_name in metric_cols:
        if col_name in result.columns:
            result[col_name] = result[col_name].fillna(0)
    
    # Fill fill_status with 'Unfilled' for NaN
    if 'fill_status' in result.columns:
        result['fill_status'] = result['fill_status'].fillna('Unfilled')
    
    return result


def _determine_fill_status(matched_qty, total_qty):
    """Determine fill status based on matched vs total quantity (Unfilled/Partially Filled/Fully Filled)."""
    if matched_qty == 0:
        return 'Unfilled'
    elif matched_qty >= total_qty:
        return 'Fully Filled'
    else:
        return 'Partially Filled'


def compare_by_group(orders_with_metrics, groups):
    """Compare real vs simulated metrics across all groups."""
    
    group_summaries = []
    all_order_details = []
    group_analyses = []
    
    for group_name, group_orders in groups.items():
        # Get orders with metrics for this group
        if 'order_id' in group_orders.columns:
            group_orderids = group_orders[col.common.order_id].values
        else:
            group_orderids = group_orders[col.common.orderid].values
        
        group_with_metrics = orders_with_metrics[
            orders_with_metrics[col.common.orderid].isin(group_orderids)
        ].copy()
        
        if len(group_with_metrics) == 0:
            continue
        
        # Calculate group-level statistics
        summary = _calculate_group_summary(group_name, group_with_metrics)
        group_summaries.append(summary)
        
        # Calculate order-level details
        order_details = _calculate_order_details(group_name, group_with_metrics)
        all_order_details.extend(order_details)
        
        # Detailed group analysis
        analysis = _analyze_group_differences(group_name, group_with_metrics)
        group_analyses.append(analysis)
    
    return {
        'group_summary': pd.DataFrame(group_summaries),
        'order_details': pd.DataFrame(all_order_details),
        'group_analysis': pd.DataFrame(group_analyses)
    }


def _calculate_group_summary(group_name, group_df):
    """Calculate summary statistics comparing real vs simulated for a group."""
    
    summary = {
        'group': group_name,
        'num_orders': len(group_df),
        
        # Real execution stats
        'real_total_quantity': group_df[col.common.quantity].sum(),
        'real_matched_quantity': group_df.get('totalmatchedquantity', pd.Series([0] * len(group_df))).sum(),
        'real_avg_fill_ratio': group_df.get('totalmatchedquantity', pd.Series([0] * len(group_df))).sum() / group_df[col.common.quantity].sum() if group_df[col.common.quantity].sum() > 0 else 0,
        
        # Simulated execution stats
        'simulated_total_quantity': group_df[col.common.quantity].sum(),
        'simulated_matched_quantity': group_df['simulated_matched_quantity'].sum(),
        'simulated_avg_fill_ratio': group_df['simulated_matched_quantity'].sum() / group_df[col.common.quantity].sum() if group_df[col.common.quantity].sum() > 0 else 0,
        
        # Comparison
        'quantity_difference': group_df['simulated_matched_quantity'].sum() - group_df.get('totalmatchedquantity', pd.Series([0] * len(group_df))).sum(),
        'fill_ratio_difference': (group_df['simulated_matched_quantity'].sum() - group_df.get('totalmatchedquantity', pd.Series([0] * len(group_df))).sum()) / group_df[col.common.quantity].sum() if group_df[col.common.quantity].sum() > 0 else 0,
    }
    
    # Count fill status changes
    if 'simulated_fill_status' in group_df.columns:
        summary['num_fully_filled_simulated'] = (group_df['simulated_fill_status'] == 'Fully Filled').sum()
        summary['num_partially_filled_simulated'] = (group_df['simulated_fill_status'] == 'Partially Filled').sum()
        summary['num_unfilled_simulated'] = (group_df['simulated_fill_status'] == 'Unfilled').sum()
    
    return summary


def _calculate_order_details(group_name, group_df):
    """Calculate order-level comparison details."""
    
    real_matched_col = 'totalmatchedquantity' if 'totalmatchedquantity' in group_df.columns else None

    real_matched_series = group_df[real_matched_col] if real_matched_col else pd.Series(
        0, index=group_df.index)
    qty_safe = group_df[col.common.quantity].clip(lower=1)

    details_df = group_df.assign(
        group=group_name,
        real_matched=real_matched_series,
        simulated_matched=group_df['simulated_matched_quantity'],
        difference=group_df['simulated_matched_quantity'] - real_matched_series,
        real_fill_ratio=real_matched_series / qty_safe,
        simulated_fill_ratio=group_df['simulated_matched_quantity'] / qty_safe,
        fill_ratio_change=(group_df['simulated_matched_quantity'] - real_matched_series) / qty_safe,
    ).rename(columns={col.common.orderid: 'orderid', col.common.quantity: 'quantity'})

    output_cols = ['group', 'orderid', 'quantity', 'real_matched', 'simulated_matched',
                   'difference', 'real_fill_ratio', 'simulated_fill_ratio', 'fill_ratio_change']
    if 'simulated_fill_status' in group_df.columns:
        output_cols.append('simulated_fill_status')

    return details_df[output_cols].to_dict('records')


def _analyze_group_differences(group_name, group_df):
    """Analyze differences between real and simulated execution for a group."""
    
    real_matched_col = 'totalmatchedquantity' if 'totalmatchedquantity' in group_df.columns else None
    
    if real_matched_col:
        real_matched = group_df[real_matched_col]
    else:
        real_matched = pd.Series([0] * len(group_df))
    
    simulated_matched = group_df['simulated_matched_quantity']
    differences = simulated_matched - real_matched
    
    analysis = {
        'group': group_name,
        'num_orders': len(group_df),
        
        # Difference statistics
        'mean_difference': differences.mean(),
        'median_difference': differences.median(),
        'std_difference': differences.std(),
        'min_difference': differences.min(),
        'max_difference': differences.max(),
        
        # Categorize differences
        'num_simulated_better': (differences > 0).sum(),
        'num_simulated_worse': (differences < 0).sum(),
        'num_simulated_same': (differences == 0).sum(),
        
        'pct_simulated_better': (differences > 0).sum() / len(group_df) * 100 if len(group_df) > 0 else 0,
        'pct_simulated_worse': (differences < 0).sum() / len(group_df) * 100 if len(group_df) > 0 else 0,
        'pct_simulated_same': (differences == 0).sum() / len(group_df) * 100 if len(group_df) > 0 else 0,
    }
    
    # Calculate total quantity impact
    total_quantity = group_df[col.common.quantity].sum()
    if total_quantity > 0:
        analysis['total_quantity_impact_pct'] = (differences.sum() / total_quantity) * 100
    else:
        analysis['total_quantity_impact_pct'] = 0
    
    return analysis


def generate_comparison_reports(partition_key, comparison_data, output_dir):
    """Generate comparison reports for a partition."""
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    report_files = {}
    
    # Report 1: Group Comparison Summary
    if 'group_summary' in comparison_data:
        summary_file = output_dir / 'group_comparison_summary.csv'
        comparison_data['group_summary'].to_csv(summary_file, index=False)
        report_files['group_summary'] = summary_file
    
    # Report 2: Order-Level Comparison
    if 'order_details' in comparison_data:
        details_file = output_dir / 'order_level_comparison.csv'
        comparison_data['order_details'].to_csv(details_file, index=False)
        report_files['order_details'] = details_file
    
    # Report 3: Group Analysis Detail
    if 'group_analysis' in comparison_data:
        analysis_file = output_dir / 'group_analysis_detail.csv'
        comparison_data['group_analysis'].to_csv(analysis_file, index=False)
        report_files['group_analysis'] = analysis_file
    
    # Report 4: Statistical Summary
    stats_file = _generate_statistical_summary(comparison_data, output_dir)
    if stats_file:
        report_files['statistical_summary'] = stats_file
    
    return report_files


def _generate_statistical_summary(comparison_data, output_dir):
    """Generate statistical summary report."""
    
    stats = []
    
    # Overall statistics from group analysis
    if 'group_analysis' in comparison_data:
        analysis_df = comparison_data['group_analysis']

        metric_specs = [
            ('mean_difference',        'Mean Difference',          'Average difference between simulated and real matched quantity'),
            ('std_difference',         'Std Difference',           'Standard deviation of differences'),
            ('pct_simulated_better',   '% Better in Simulation',   'Percentage of orders with better fill in simulation'),
            ('total_quantity_impact_pct', 'Total Quantity Impact %', 'Total impact on quantity as percentage'),
        ]
        for col_name, label, description in metric_specs:
            if col_name in analysis_df.columns:
                rows = analysis_df[['group', col_name]].copy()
                rows['metric'] = rows['group'] + f' - {label}'
                rows['description'] = description
                rows = rows.rename(columns={col_name: 'value'})[['metric', 'value', 'description']]
                stats.extend(rows.to_dict('records'))
    
    # Aggregate statistics from group summary
    if 'group_summary' in comparison_data:
        summary_df = comparison_data['group_summary']
        
        total_real = summary_df['real_matched_quantity'].sum()
        total_simulated = summary_df['simulated_matched_quantity'].sum()
        
        stats.append({
            'metric': 'Overall - Total Real Matched',
            'value': total_real,
            'description': 'Total matched quantity in real execution'
        })
        
        stats.append({
            'metric': 'Overall - Total Simulated Matched',
            'value': total_simulated,
            'description': 'Total matched quantity in simulation'
        })
        
        stats.append({
            'metric': 'Overall - Total Difference',
            'value': total_simulated - total_real,
            'description': 'Difference between simulated and real total matched'
        })
        
        if total_real > 0:
            stats.append({
                'metric': 'Overall - % Change',
                'value': ((total_simulated - total_real) / total_real) * 100,
                'description': 'Percentage change from real to simulated'
            })
    
    # Write statistical summary
    stats_df = pd.DataFrame(stats)
    stats_file = output_dir / 'statistical_summary.csv'
    stats_df.to_csv(stats_file, index=False)
    
    return stats_file


# ============================================================================
# NEW: SWEEP ORDER COMPARISON FUNCTIONS
# ============================================================================

def compare_sweep_execution(sweep_order_summary, orders_after_matching, trades_agg, groups):
    """Compare simulated vs real execution for SWEEP ORDERS ONLY."""
    
    # Standardize column names in groups dictionary
    standardized_groups = {}
    for group_name, group_df in groups.items():
        group_df_copy = group_df.copy()
        if 'order_id' in group_df_copy.columns and 'orderid' not in group_df_copy.columns:
            group_df_copy = group_df_copy.rename(columns={'order_id': 'orderid'})
        # Ensure orderid is int64
        if 'orderid' in group_df_copy.columns:
            group_df_copy[col.common.orderid] = group_df_copy[col.common.orderid].astype('int64')
        standardized_groups[group_name] = group_df_copy
    groups = standardized_groups
    
    # Filter orders_after_matching for sweep orders only (type 2048)
    sweep_orders_real = orders_after_matching[
        orders_after_matching[col.orders.order_type] == 2048
    ].copy()
    
    # Standardize column names
    if 'order_id' in sweep_orders_real.columns and 'orderid' not in sweep_orders_real.columns:
        sweep_orders_real = sweep_orders_real.rename(columns={'order_id': 'orderid'})
    
    # Merge simulation with real execution data
    comparison = sweep_order_summary.merge(
        sweep_orders_real[['orderid', 'quantity', 'leavesquantity', 'totalmatchedquantity']],
        on='orderid',
        how='left',
        suffixes=('_sim', '_real')
    )
    
    # Merge with trade aggregates for price information
    if trades_agg is not None and len(trades_agg) > 0:
        comparison = comparison.merge(
            trades_agg[['orderid', 'avg_execution_price', 'num_trades', 'first_trade_time', 'last_trade_time']],
            on='orderid',
            how='left'
        )
        comparison['real_num_matches'] = comparison['num_trades'].fillna(0)
        comparison['real_avg_price'] = comparison['avg_execution_price'].fillna(0)
    else:
        comparison['real_num_matches'] = 0
        comparison['real_avg_price'] = 0.0
    
    # Calculate real execution metrics
    comparison['real_matched_quantity'] = comparison[col.orders.matched_quantity].fillna(0)
    comparison['real_fill_ratio'] = comparison['real_matched_quantity'] / comparison['quantity_real']
    comparison['real_fill_status'] = comparison.apply(
        lambda row: _determine_fill_status(row['real_matched_quantity'], row['quantity_real']),
        axis=1
    )
    
    # Calculate simulated execution metrics (rename for clarity)
    comparison = comparison.rename(columns={
        'matched_quantity': 'simulated_matched_quantity',
        'fill_ratio': 'simulated_fill_ratio',
        'num_matches': 'simulated_num_matches',
        'quantity_sim': 'available_quantity'
    })
    
    comparison['simulated_fill_status'] = comparison.apply(
        lambda row: _determine_fill_status(row['simulated_matched_quantity'], row['available_quantity']),
        axis=1
    )
    
    # Calculate differences
    comparison['matched_quantity_diff'] = comparison['simulated_matched_quantity'] - comparison['real_matched_quantity']
    comparison['fill_ratio_diff'] = comparison['simulated_fill_ratio'] - comparison['real_fill_ratio']
    comparison['num_matches_diff'] = comparison['simulated_num_matches'] - comparison['real_num_matches']
    comparison['price_diff'] = 0.0  # Placeholder - would need simulated prices
    
    # Add order size category
    comparison['size_category'] = comparison['available_quantity'].apply(_categorize_order_size)
    
    # Add group membership
    comparison['group'] = comparison[col.common.orderid].apply(lambda x: _find_order_group(x, groups))
    
    # Generate comprehensive analyses
    sweep_comparison = comparison[[
        'orderid', 'available_quantity', 'size_category', 'group',
        'real_matched_quantity', 'simulated_matched_quantity', 'matched_quantity_diff',
        'real_fill_ratio', 'simulated_fill_ratio', 'fill_ratio_diff',
        'real_num_matches', 'simulated_num_matches', 'num_matches_diff',
        'real_fill_status', 'simulated_fill_status'
    ]]
    
    # Calculate group-level summaries
    group_summary = _calculate_sweep_group_summary(comparison)
    
    # Calculate statistical tests
    statistical_tests = _calculate_statistical_tests(comparison)
    
    # Analysis by order size
    size_analysis = _calculate_size_analysis(comparison)
    
    return {
        'sweep_comparison': sweep_comparison,
        'group_summary': group_summary,
        'statistical_tests': statistical_tests,
        'size_analysis': size_analysis
    }


def _categorize_order_size(quantity):
    """Categorize order by size."""
    if quantity <= 500:
        return 'Small'
    elif quantity <= 2000:
        return 'Medium'
    else:
        return 'Large'


def _find_order_group(orderid, groups):
    """Find which group an order belongs to."""
    for group_name, group_df in groups.items():
        # Post-normalization always uses 'orderid' column name
        if 'orderid' in group_df.columns:
            orderid_col_name = col.common.orderid
        elif 'order_id' in group_df.columns:
            orderid_col_name = col.common.order_id
        else:
            continue
        
        # Ensure orderid is int64 for comparison
        try:
            if int(orderid) in group_df[orderid_col_name].astype('int64').values:
                return group_name
        except (ValueError, TypeError, KeyError):
            continue
    return 'Unknown'


def _calculate_sweep_group_summary(comparison_df):
    """Calculate summary statistics by group for sweep orders."""
    
    summaries = []
    
    for group_name in comparison_df['group'].unique():
        if group_name == 'Unknown':
            continue
            
        group_data = comparison_df[comparison_df['group'] == group_name]
        
        summary = {
            'group': group_name,
            'num_orders': len(group_data),
            
            # Real execution
            'real_total_matched': group_data['real_matched_quantity'].sum(),
            'real_mean_matched': group_data['real_matched_quantity'].mean(),
            'real_mean_fill_ratio': group_data['real_fill_ratio'].mean(),
            'real_mean_num_matches': group_data['real_num_matches'].mean(),
            
            # Simulated execution
            'simulated_total_matched': group_data['simulated_matched_quantity'].sum(),
            'simulated_mean_matched': group_data['simulated_matched_quantity'].mean(),
            'simulated_mean_fill_ratio': group_data['simulated_fill_ratio'].mean(),
            'simulated_mean_num_matches': group_data['simulated_num_matches'].mean(),
            
            # Differences
            'total_quantity_diff': group_data['matched_quantity_diff'].sum(),
            'mean_quantity_diff': group_data['matched_quantity_diff'].mean(),
            'mean_fill_ratio_diff': group_data['fill_ratio_diff'].mean(),
            'mean_num_matches_diff': group_data['num_matches_diff'].mean(),
            
            # Percentages
            'pct_sim_better': ((group_data['matched_quantity_diff'] > 0).sum() / len(group_data) * 100) if len(group_data) > 0 else 0,
            'pct_sim_worse': ((group_data['matched_quantity_diff'] < 0).sum() / len(group_data) * 100) if len(group_data) > 0 else 0,
            'pct_sim_same': ((group_data['matched_quantity_diff'] == 0).sum() / len(group_data) * 100) if len(group_data) > 0 else 0,
        }
        
        summaries.append(summary)
    
    return pd.DataFrame(summaries)


def _calculate_statistical_tests(comparison_df, stats_engine=None):
    """Calculate paired t-tests comparing simulated vs real execution."""
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    results = []
    
    # Overall tests
    results.extend(_run_ttests(comparison_df, 'Overall', 'All', stats_engine))
    
    # Tests by group
    for group_name in comparison_df['group'].unique():
        if group_name == 'Unknown':
            continue
        group_data = comparison_df[comparison_df['group'] == group_name]
        if len(group_data) >= 2:  # Need at least 2 samples for t-test
            results.extend(_run_ttests(group_data, 'Group', group_name, stats_engine))
    
    # Tests by size category
    for size_cat in comparison_df['size_category'].unique():
        size_data = comparison_df[comparison_df['size_category'] == size_cat]
        if len(size_data) >= 2:
            results.extend(_run_ttests(size_data, 'Size', size_cat, stats_engine))
    
    # Tests by group AND size
    for group_name in comparison_df['group'].unique():
        if group_name == 'Unknown':
            continue
        for size_cat in comparison_df['size_category'].unique():
            segment_data = comparison_df[
                (comparison_df['group'] == group_name) & 
                (comparison_df['size_category'] == size_cat)
            ]
            if len(segment_data) >= 2:
                results.extend(_run_ttests(segment_data, f'Group-Size', f'{group_name}_{size_cat}', stats_engine))
    
    return pd.DataFrame(results)


def _run_ttests(data, segment_type, segment_name, stats_engine=None):
    """Run paired t-tests on a data segment with proper NaN/Inf handling."""
    import numpy as np
    import warnings
    
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    results = []
    
    # Only run tests if we have enough data
    if len(data) < 2:
        return results
    
    # Define metrics to test
    metrics_to_test = [
        ('Matched Quantity', 'real_matched_quantity', 'simulated_matched_quantity'),
        ('Fill Ratio', 'real_fill_ratio', 'simulated_fill_ratio'),
        ('Number of Matches', 'real_num_matches', 'simulated_num_matches')
    ]
    
    for metric_name, real_col, sim_col in metrics_to_test:
        # Extract and validate data
        real_values = data[real_col].values
        sim_values = data[sim_col].values
        
        # Filter out NaN/Inf values
        valid_mask = np.isfinite(real_values) & np.isfinite(sim_values)
        real_valid = real_values[valid_mask]
        sim_valid = sim_values[valid_mask]
        
        # Check for sufficient valid samples and non-zero variance
        if len(real_valid) < 2 or real_valid.std() == 0 or sim_valid.std() == 0:
            continue
        
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', category=RuntimeWarning)
                
                # Calculate mean difference
                differences = sim_valid - real_valid
                mean_diff = differences.mean()
                std_diff = differences.std()
                
                # Initialize statistical values
                t_stat = p_value = np.nan
                ci_lower = ci_upper = np.nan
                
                if stats_engine.is_enabled():
                    # Run paired t-test
                    ttest_result = stats_engine.ttest_rel(sim_valid, real_valid)
                    if ttest_result:
                        t_stat = ttest_result.statistic
                        p_value = ttest_result.pvalue
                    
                    # Calculate confidence interval for differences
                    ci_result = stats_engine.confidence_interval(differences, confidence=0.95)
                    if ci_result:
                        ci_lower, ci_upper = ci_result
                
                results.append({
                    'segment_type': segment_type,
                    'segment_name': segment_name,
                    'metric': metric_name,
                    'n_samples': len(real_valid),
                    'mean_real': real_valid.mean(),
                    'mean_simulated': sim_valid.mean(),
                    'mean_difference': mean_diff,
                    'std_difference': std_diff,
                    't_statistic': t_stat,
                    'p_value': p_value,
                    'significant_5pct': (p_value < 0.05) if not np.isnan(p_value) else False,
                    'significant_1pct': (p_value < 0.01) if not np.isnan(p_value) else False,
                    'ci_95_lower': ci_lower,
                    'ci_95_upper': ci_upper
                })
        except Exception:
            # Skip test if calculation fails
            pass
    
    return results


def _calculate_size_analysis(comparison_df):
    """Calculate detailed analysis by order size category."""
    import warnings
    import numpy as np
    
    analyses = []
    
    for size_cat in ['Small', 'Medium', 'Large']:
        size_data = comparison_df[comparison_df['size_category'] == size_cat]
        
        if len(size_data) == 0:
            continue
        
        # Suppress pandas RuntimeWarnings for NaN operations
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', category=RuntimeWarning)
            
            analysis = {
                'size_category': size_cat,
                'num_orders': len(size_data),
                'quantity_range': f"{size_data['available_quantity'].min():.0f} - {size_data['available_quantity'].max():.0f}",
                
                # Real execution
                'real_total_matched': size_data['real_matched_quantity'].sum(),
                'real_mean_matched': size_data['real_matched_quantity'].mean(),
                'real_std_matched': size_data['real_matched_quantity'].std(),
                'real_mean_fill_ratio': size_data['real_fill_ratio'].mean(),
                'real_std_fill_ratio': size_data['real_fill_ratio'].std(),
                
                # Simulated execution
                'simulated_total_matched': size_data['simulated_matched_quantity'].sum(),
                'simulated_mean_matched': size_data['simulated_matched_quantity'].mean(),
                'simulated_std_matched': size_data['simulated_matched_quantity'].std(),
                'simulated_mean_fill_ratio': size_data['simulated_fill_ratio'].mean(),
                'simulated_std_fill_ratio': size_data['simulated_fill_ratio'].std(),
                
                # Differences
                'mean_quantity_diff': size_data['matched_quantity_diff'].mean(),
                'median_quantity_diff': size_data['matched_quantity_diff'].median(),
                'std_quantity_diff': size_data['matched_quantity_diff'].std(),
                'mean_fill_ratio_diff': size_data['fill_ratio_diff'].mean(),
                'median_fill_ratio_diff': size_data['fill_ratio_diff'].median(),
                
                # Distribution
                'pct_sim_better': ((size_data['matched_quantity_diff'] > 0).sum() / len(size_data) * 100),
                'pct_sim_worse': ((size_data['matched_quantity_diff'] < 0).sum() / len(size_data) * 100),
                'pct_sim_same': ((size_data['matched_quantity_diff'] == 0).sum() / len(size_data) * 100),
            }
        
        analyses.append(analysis)
    
    return pd.DataFrame(analyses)


def generate_sweep_comparison_reports(partition_key, comparison_results, output_dir):
    """Generate comprehensive comparison reports for sweep orders."""
    
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    report_files = {}
    
    # Report 1: Per-order sweep comparison
    if 'sweep_comparison' in comparison_results:
        file_path = output_dir / 'sweep_order_comparison.csv'
        comparison_results['sweep_comparison'].to_csv(file_path, index=False)
        report_files['sweep_comparison'] = file_path
        print(f"    Generated: sweep_order_comparison.csv ({len(comparison_results['sweep_comparison']):,} orders)")
    
    # Report 2: Group summary
    if 'group_summary' in comparison_results:
        file_path = output_dir / 'sweep_group_summary.csv'
        comparison_results['group_summary'].to_csv(file_path, index=False)
        report_files['group_summary'] = file_path
        print(f"    Generated: sweep_group_summary.csv")
    
    # Report 3: Statistical tests
    if 'statistical_tests' in comparison_results:
        file_path = output_dir / 'sweep_statistical_tests.csv'
        comparison_results['statistical_tests'].to_csv(file_path, index=False)
        report_files['statistical_tests'] = file_path
        print(f"    Generated: sweep_statistical_tests.csv ({len(comparison_results['statistical_tests'])} tests)")
    
    # Report 4: Size analysis
    if 'size_analysis' in comparison_results:
        file_path = output_dir / 'sweep_size_analysis.csv'
        comparison_results['size_analysis'].to_csv(file_path, index=False)
        report_files['size_analysis'] = file_path
        print(f"    Generated: sweep_size_analysis.csv")
    
    return report_files


# ============================================================================
# TRADE-LEVEL COMPARISON (Real vs Simulated Trades)
# ============================================================================

def calculate_real_trade_metrics(trades_by_partition, orders_by_partition, processed_dir):
    """Calculate comprehensive metrics from real trades for sweep orders using unified calculator."""
    print(f"\n[11/11] Calculating real trade metrics for sweep orders...")
    
    SWEEP_ORDER_TYPE = 2048
    real_metrics_by_partition = {}
    
    for partition_key, trades_df in trades_by_partition.items():
        if len(trades_df) == 0:
            continue
        
        # Load order data to identify sweep orders
        date, security_code = partition_key.split('/')
        partition_dir = Path(processed_dir) / date / security_code
        
        orders_before_file = partition_dir / "orders_before_matching.csv"
        if not orders_before_file.exists():
            continue
        
        orders_before = pd.read_csv(orders_before_file)
        
        # Standardize column names
        if 'order_id' in orders_before.columns:
            orders_before = orders_before.rename(columns={'order_id': 'orderid'})
        if 'order_id' in trades_df.columns:
            trades_df = trades_df.rename(columns={'order_id': 'orderid'})
        
        # Get sweep order IDs
        sweep_orderids = orders_before[
            orders_before[col.orders.order_type] == SWEEP_ORDER_TYPE
        ][col.common.orderid].unique()
        
        if len(sweep_orderids) == 0:
            print(f"  {partition_key}: No sweep orders found")
            continue
        
        # Use unified calculator for comprehensive metrics
        metrics = calculate_trade_metrics(
            trades_df=trades_df,
            orders_df=orders_before,
            nbbo_df=None,
            filter_orderids=list(sweep_orderids),
            role_filter=None,  # All trades for sweep orders
            prefix='',  # No prefix for real trades
            is_simulated=False  # Real trades
        )
        
        if len(metrics['per_order_metrics']) == 0:
            print(f"  {partition_key}: No metrics calculated")
            continue
        
        real_metrics_by_partition[partition_key] = {
            'trade_metrics': metrics['per_trade_metrics'],
            'order_metrics': metrics['per_order_metrics']
        }
        
        num_orders = len(metrics['per_order_metrics'])
        num_trades = len(metrics['per_trade_metrics'])
        print(f"  {partition_key}: {num_trades:,} trades for {num_orders:,} sweep orders (36 metrics per order)")
    
    return real_metrics_by_partition


def load_real_metrics(output_dir, partition_keys):
    """Load pre-calculated real trade metrics from disk."""
    real_metrics_by_partition = {}
    
    for partition_key in partition_keys:
        partition_output_dir = Path(output_dir) / partition_key
        real_metrics_path = partition_output_dir / 'real_trade_metrics.csv'
        
        if not real_metrics_path.exists():
            continue
        
        real_order_metrics = pd.read_csv(real_metrics_path)
        
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
        
        # Load orders to get arrival NBBO for simulated metrics
        date, security_code = partition_key.split('/')
        partition_dir = Path(output_dir).parent / "processed" / date / security_code
        orders_before = fu.load_orders_before(partition_dir)
        
        # Aggregate simulated trades per order (for sweep orders)
        sim_aggregated = _aggregate_simulated_trades_per_order(sim_trades, sim_order_summary, orders_before)
        
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
            file_path = partition_output_dir / 'trade_level_comparison.csv'
            comparison_data['trade_level_comparison'].to_csv(file_path, index=False)
            report_files['trade_level_comparison'] = file_path
            print(f"    {partition_key}/trade_level_comparison.csv: {len(comparison_data['trade_level_comparison']):,} orders")
        
        # Report 2: Trade accuracy summary (optional)
        if include_accuracy_summary and 'trade_accuracy_summary' in comparison_data:
            file_path = partition_output_dir / 'trade_accuracy_summary.csv'
            comparison_data['trade_accuracy_summary'].to_csv(file_path, index=False)
            report_files['trade_accuracy_summary'] = file_path
            print(f"    {partition_key}/trade_accuracy_summary.csv: Overall metrics")
        
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
# Section 10 - pipeline/pipeline_output.py
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
# Section 11 - pipeline/pipeline_stages.py
# ============================================================================
"""Pipeline stage execution functions."""

from pathlib import Path
# (consolidated) import config.config as config
# (consolidated) import pipeline.data_processor as dp
# (consolidated) import pipeline.partition_processor as pp
# (consolidated) import pipeline.execution_comparison as ec
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
        import sys as _sfu_sys; fu = _sfu_sys.modules[__name__]  # was utils.file_utils
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
            sim_metrics_path = output_partition_dir / 'simulated_trade_metrics.csv'
            sim_aggregated.to_csv(sim_metrics_path, index=False)
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
    import sys as _sfu_sys; fu = _sfu_sys.modules[__name__]  # was utils.file_utils
    
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
# Section 12 - pipeline/pipeline_config.py + main.py
# ============================================================================
"""Pipeline configuration and CLI argument handling."""

import argparse
from pathlib import Path
# (consolidated) import config.config as config
# (replaced by inline shim) from discovery.security_discovery import SecurityDiscovery
# (cut) from utils.statistics_layer import StatisticsEngine


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


# (removed: original `if __name__ == '__main__': main()` — replaced by cli_multi_agg below)


# ============================================================================
# Section 13 - New flat-port multi-date/multi-ticker CLI (Stages 3+4)
# ============================================================================

import duckdb as _duckdb_a
from pathlib import Path as _Path_a
from types import SimpleNamespace as _NS_a
import sys as _sys_a
import argparse as _argparse_a
import csv as _csv_a
import config  # top-level config.py at repo root

# Self-aliases so legacy code's module references resolve to this module.
dp = _sys_a.modules[__name__]
pp = _sys_a.modules[__name__]
ec = _sys_a.modules[__name__]
fu = _sys_a.modules[__name__]
du = _sys_a.modules[__name__]
ss = _sys_a.modules[__name__]
tm = _sys_a.modules[__name__]


class SecurityDiscovery:  # inline shim (identical to process.py's shim)
    def __init__(self, raw_data_dir, min_orders=100, min_trades=10):
        self.raw_data_dir = _Path_a(raw_data_dir)
        self.min_orders = min_orders
        self.min_trades = min_trades

    def get_orderbookid_from_ticker(self, ticker, date):
        f = self.raw_data_dir / "orders" / f"{ticker}_{date}_orders.csv"
        with open(f) as fh:
            row = next(_csv_a.DictReader(fh), None)
        if row is None:
            raise ValueError(f"No rows in {f}")
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
                out.append(_NS_a(ticker=ticker, orderbookid=obid,
                                 order_count=0, trade_count=0))
            except Exception:
                continue
        return out

    def discover_securities_for_date(self, date):
        return self.get_valid_securities(date)


def _auto_discover_partitions_agg(dates):
    con = _duckdb_a.connect()
    out = []
    for date in dates:
        pattern = str(_Path_a(config.RAW_DIR) / "orders" / f"*_{date}_orders.csv")
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


def _resolve_partitions_agg(dates, tickers):
    if tickers is None:
        return _auto_discover_partitions_agg(dates)
    return [(d, t) for d in dates for t in tickers
            if (_Path_a(config.RAW_DIR) / "orders" / f"{t}_{d}_orders.csv").exists()]


def _make_legacy_args_agg(date, ticker, workers):
    ns = _argparse_a.Namespace()
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
    # Legacy stages 3/4 need Stage 1's in-memory `data`; run the whole pipeline
    # end-to-end. Wasteful but the only way to get correct outputs without
    # refactoring Stage 3/4 to load from disk.
    ns.stage = [1, 2, 3, 4]
    ns.processing_mode = None
    ns.disable_resting = False
    ns.disable_lit_resting = False
    return ns


def cli_multi_agg():
    parser = _argparse_a.ArgumentParser(
        prog="aggregate.py",
        description="Stage 3+4: metrics + real-vs-sim comparison (multi-date/multi-ticker)."
    )
    parser.add_argument("--dates", required=True)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--tickers")
    g.add_argument("--auto-tickers", action="store_true")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()

    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    tickers = None if args.auto_tickers else [t.strip() for t in args.tickers.split(",") if t.strip()]

    partitions = _resolve_partitions_agg(dates, tickers)
    if not partitions:
        print("No partitions resolved.", file=_sys_a.stderr)
        _sys_a.exit(2)

    print(f"[aggregate] Resolved {len(partitions)} partition(s): {partitions}")
    setup_directories()

    failures = []
    for (date, ticker) in partitions:
        try:
            print(f"\n[aggregate] === {ticker.upper()} / {date} ===")
            ns = _make_legacy_args_agg(date, ticker, workers=args.workers)
            runtime_config = build_runtime_config(ns)
            execute_pipeline_stages(runtime_config)
        except SystemExit:
            raise
        except BaseException as exc:
            failures.append((date, ticker, repr(exc)))
            print(f"[aggregate] FAILED {ticker}/{date}: {exc!r}", file=_sys_a.stderr)

    if failures:
        _sys_a.exit(1)


if __name__ == '__main__':
    cli_multi_agg()
