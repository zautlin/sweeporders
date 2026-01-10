"""
Configuration Module

Centralized configuration for the Centre Point Sweep Order Matching Pipeline.
Includes column mappings, order types, file paths, and system configuration.
"""

import pandas as pd
from pathlib import Path
from . import system_config as sc


# ============================================================================
# PROJECT ROOT DIRECTORY
# ============================================================================

# Get the project root directory (parent of src/, grandparent of config/)
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()


# ============================================================================
# SYSTEM CONFIGURATION (Auto-detected based on CPU/memory)
# ============================================================================

SYSTEM_CONFIG = sc.get_config_with_overrides()
CHUNK_SIZE = SYSTEM_CONFIG.chunk_size
NUM_WORKERS = SYSTEM_CONFIG.num_workers

# Parallel processing configuration
ENABLE_PARALLEL_PROCESSING = False  # Set to False for debugging/sequential mode
MAX_PARALLEL_WORKERS = NUM_WORKERS  # Number of parallel workers for partition processing


# ============================================================================
# DATASET CONFIGURATION
# ============================================================================

# Default ticker and date - used when CLI args not provided
TICKER = 'drr'           # Default ticker symbol
DATE = '20240905'        # Default date in YYYYMMDD format

# Legacy hardcoded tickers (for backward compatibility)
# DEPRECATED: Use security auto-discovery instead
LEGACY_TICKERS = ['drr', 'bhp', 'cba', 'wtc']

# ============================================================================
# SECURITY AUTO-DISCOVERY CONFIGURATION
# ============================================================================

# Enable automatic discovery of securities from raw files
AUTO_DISCOVERY_ENABLED = True

# Minimum thresholds for valid securities
MIN_ORDERS_THRESHOLD = 100  # Ignore securities with < 100 orders
MIN_TRADES_THRESHOLD = 10   # Ignore securities with < 10 trades

# ============================================================================
# STATISTICAL TESTING CONFIGURATION
# ============================================================================

# Enable statistical tests (t-tests, p-values, confidence intervals)
# Default: False (only descriptive statistics)
ENABLE_STATISTICAL_TESTS = False

# Force use of simple statistics even if scipy is available
# Useful for testing approximate methods
FORCE_SIMPLE_STATS = False

# Show warnings when using approximate statistics
WARN_APPROXIMATE_STATS = True


# ============================================================================
# INPUT FILES
# ============================================================================

def get_input_files(ticker=None, date=None):
    """Build input file paths from ticker and date. Uses config defaults if not provided."""
    ticker = ticker or TICKER
    date = date or DATE
    
    base = PROJECT_ROOT / 'data/raw'
    
    return {
        'orders': str(base / f'orders/{ticker}_{date}_orders.csv'),
        'trades': str(base / f'trades/{ticker}_{date}_trades.csv'),
        'nbbo': str(base / f'nbbo/{ticker}_{date}_nbbo.csv'),
        'session': str(base / f'session/{date}_session.csv'),
        'reference': str(base / f'reference/{date}_ob.csv'),
        'participants': str(base / f'participants/{date}_par.csv'),
    }


def validate_input_files(files):
    """Check if required input files exist, raise error if missing."""
    required = ['orders', 'trades']  # Minimum required files
    missing = []
    
    for key in required:
        if key in files and not Path(files[key]).exists():
            missing.append(f"{key}: {files[key]}")
    
    if missing:
        raise FileNotFoundError(
            f"Required input files not found:\n  " + "\n  ".join(missing) +
            f"\n\nExpected pattern: {{ticker}}_{{date}}_{{type}}.csv"
        )
    
    return True


# Default INPUT_FILES using config values
INPUT_FILES = get_input_files()

# Raw data directories
RAW_FOLDERS = {
    'orders': str(PROJECT_ROOT / 'data/raw/orders'),
    'trades': str(PROJECT_ROOT / 'data/raw/trades'),
    'nbbo': str(PROJECT_ROOT / 'data/raw/nbbo'),
    'session': str(PROJECT_ROOT / 'data/raw/session'),
    'reference': str(PROJECT_ROOT / 'data/raw/reference'),
    'participants': str(PROJECT_ROOT / 'data/raw/participants'),
}


# ============================================================================
# DIRECTORY STRUCTURE
# ============================================================================

PROCESSED_DIR = str(PROJECT_ROOT / 'data/processed')  # Intermediate files: raw data, LOB states
OUTPUTS_DIR = str(PROJECT_ROOT / 'data/outputs')      # Final outputs: simulation results, comparisons
AGGREGATED_DIR = str(PROJECT_ROOT / 'data/aggregated')  # Aggregated cross-security results


# ============================================================================
# ORDER TYPES
# ============================================================================

CENTRE_POINT_ORDER_TYPES = [64, 256, 2048, 4096, 4098]
SWEEP_ORDER_TYPE = 2048
ELIGIBLE_MATCHING_ORDER_TYPES = [64, 256, 2048, 4096, 4098]  # ALL CP types, including sweep-to-sweep

# Order side values
ORDER_SIDE = {
    'BUY': 1,
    'SELL': 2,
}


# ============================================================================
# COLUMN NAME MAPPING (for schema independence)
# ============================================================================

COLUMN_MAPPING = {
    # ========================================================================
    # INPUT FILE COLUMNS (from raw CSV files)
    # ========================================================================
    
    # Orders file columns
    'orders': {
        'order_id': 'order_id',
        'timestamp': 'timestamp',
        'sequence': 'sequence',
        'order_type': 'exchangeordertype',
        'security_code': 'security_code',
        'securitycode': 'securitycode',  # Alternative name
        'side': 'side',
        'quantity': 'quantity',
        'price': 'price',
        'bid': 'bid',
        'offer': 'offer',
        'leaves_quantity': 'leavesquantity',
        'matched_quantity': 'totalmatchedquantity',
        'order_status': 'orderstatus',
        'change_reason': 'changereason',
        'participant_id': 'participantid',
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
        'timestamp': 'timestamp',
        'security_code': 'orderbookid',
        'bid': 'bidprice',
        'offer': 'offerprice',
        'bid_quantity': 'bidquantity',
        'offer_quantity': 'offerquantity',
    },
    
    # Session file columns
    'session': {
        'timestamp': 'TradeDate',
        'security_code': 'OrderBookId',
    },
    
    # Reference file columns
    'reference': {
        'timestamp': 'TradeDate',
        'security_code': 'Id',
    },
    
    # Participants file columns
    'participants': {
        'timestamp': 'TradeDate',
    },
    
    # ========================================================================
    # PROCESSED/CALCULATED COLUMNS (created during pipeline execution)
    # ========================================================================
    
    # Sweep order analysis columns
    'sweep': {
        'sweep_orderid': 'sweep_orderid',
        'orderid': 'orderid',
        'order_quantity': 'order_quantity',
        'orderbookid': 'orderbookid',
        'ticker': 'ticker',
        'date': 'date',
        'timestamp': 'timestamp',
        'side': 'side',
        'price': 'price',
        'arrival_bid': 'arrival_bid',
        'arrival_offer': 'arrival_offer',
    },
    
    # Metrics columns (calculated execution metrics)
    'metrics': {
        'qty_filled': 'qty_filled',
        'fill_rate_pct': 'fill_rate_pct',
        'fill_rate': 'fill_rate',
        'num_fills': 'num_fills',
        'avg_fill_size': 'avg_fill_size',
        'vwap': 'vwap',
        'exec_cost_arrival_bps': 'exec_cost_arrival_bps',
        'exec_cost_vw_bps': 'exec_cost_vw_bps',
        'effective_spread_pct': 'effective_spread_pct',
        'exec_time_sec': 'exec_time_sec',
        'time_to_first_fill_sec': 'time_to_first_fill_sec',
        'vw_exec_time_sec': 'vw_exec_time_sec',
        'first_execution': 'first_execution',
        'last_execution': 'last_execution',
    },
    
    # Simulation columns (dark pool simulation results)
    'simulation': {
        'simulated_qty_filled': 'simulated_qty_filled',
        'simulated_fill_ratio': 'simulated_fill_ratio',
        'simulated_fill_status': 'simulated_fill_status',
        'simulated_matched_quantity': 'simulated_matched_quantity',
        'simulated_num_fills': 'simulated_num_fills',
        'simulated_vwap': 'simulated_vwap',
        'simulated_exec_cost': 'simulated_exec_cost',
        'match_status': 'match_status',
        'match_details': 'match_details',
    },
    
    # Comparison columns (real vs simulated)
    'comparison': {
        'real_matched_quantity': 'real_matched_quantity',
        'simulated_matched_quantity': 'simulated_matched_quantity',
        'matched_quantity_diff': 'matched_quantity_diff',
        'qty_filled_diff': 'qty_filled_diff',
        'fill_rate_diff': 'fill_rate_diff',
        'vwap_diff': 'vwap_diff',
        'exec_cost_diff': 'exec_cost_diff',
        'exec_time_diff': 'exec_time_diff',
        'exec_cost_arrival_diff_bps': 'exec_cost_arrival_diff_bps',
        'exec_cost_vw_diff_bps': 'exec_cost_vw_diff_bps',
        'exec_time_diff_sec': 'exec_time_diff_sec',
        'better_execution': 'better_execution',
        'price_error_pct': 'price_error_pct',
    },
    
    # Statistical analysis columns
    'stats': {
        't_statistic': 't_statistic',
        'p_value': 'p_value',
        'mean_diff': 'mean_diff',
        'effect_size': 'effect_size',
        'cohens_d': 'cohens_d',
        'confidence_interval_lower': 'ci_95_lower',
        'confidence_interval_upper': 'ci_95_upper',
        'significant': 'significant',
        'significance': 'significance',
        'f_statistic': 'f_statistic',
        'pearson_correlation': 'pearson_correlation',
        'spearman_correlation': 'spearman_correlation',
    },
    
    # Volume analysis columns
    'volume': {
        'volume_bucket': 'volume_bucket',
        'volume_bucket_label': 'volume_bucket_label',
        'n_orders': 'n_orders',
        'min_quantity': 'min_quantity',
        'max_quantity': 'max_quantity',
        'mean_quantity': 'mean_quantity',
        'mean_exec_cost_diff_bps': 'mean_exec_cost_diff_bps',
        'mean_exec_time_diff_sec': 'mean_exec_time_diff_sec',
        'dark_pool_better_pct': 'dark_pool_better_pct',
        'weighted_exec_cost_diff_bps': 'weighted_exec_cost_diff_bps',
        'weighted_exec_time_diff_sec': 'weighted_exec_time_diff_sec',
    },
    
    # Aggregated analysis columns (cross-security)
    'aggregated': {
        'ticker': 'ticker',
        'date': 'date',
        'orderbookid': 'orderbookid',
        'security_code': 'security_code',
        'metric': 'metric',
        'metric_key': 'metric_key',
        'unit': 'unit',
        'n_orders': 'n_orders',
        'mean': 'mean',
        'median': 'median',
        'std': 'std',
        'count': 'count',
        'better_count': 'better_count',
        'better_pct': 'better_pct',
    },
    
    # Unmatched analysis columns
    'unmatched': {
        'root_cause': 'root_cause',
        'contra_depth': 'contra_depth',
        'contra_orders_count': 'contra_orders_count',
        'potential_fill_qty': 'potential_fill_qty',
        'price_overlap': 'price_overlap',
    },
    
    # Common identifiers (used across multiple contexts)
    'common': {
        'orderid': 'orderid',
        'order_id': 'order_id',
        'ticker': 'ticker',
        'date': 'date',
        'orderbookid': 'orderbookid',
        'timestamp': 'timestamp',
        'side': 'side',
        'quantity': 'quantity',
        'price': 'price',
    },
}


# ============================================================================
# COLUMN NORMALIZATION MAPPING
# Maps raw/alternative column names to standardized names for processed files
# ============================================================================

COLUMN_NORMALIZATION_MAP = {
    'orders': {
        # Raw name → Standard name
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


# ============================================================================
# LEGACY COLUMN DEFINITIONS (for backward compatibility)
# ============================================================================

INPUT_COLUMNS = {
    # Orders file columns
    'orders': {
        'order_id': 'orderid',
        'security_code': 'securitycode',
        'exchange_order_type': 'exchangeordertype',
        'side': 'side',  # 1=BUY, 2=SELL
        'price': 'price',
        'quantity': 'quantity',
        'timestamp': 'timestamp',
        'participant_id': 'participantid',
        'leaves_quantity': 'leavesquantity',
    },
    
    # Trades file columns
    'trades': {
        'order_id': 'orderid',
        'trade_price': 'tradeprice',
        'trade_time': 'tradetime',
        'trade_quantity': 'quantity',
    }
}

STANDARD_COLUMNS = {
    # Order identifiers and metadata
    'order_id': 'order_id',
    'security_code': 'security_code',
    'side': 'side',
    'participant_id': 'participantid',
    'timestamp': 'timestamp',
    
    # Order price and quantity
    'price': 'price',
    'quantity': 'quantity',
    'leaves_quantity': 'leavesquantity',
    
    # Order classification
    'exchange_order_type': 'exchangeordertype',
    'scenario_type': 'scenario_type',
    
    # Trade execution data
    'trade_price': 'tradeprice',
    'trade_time': 'tradetime',
    'trade_quantity': 'quantity',  # When referring to trade quantity
    
    # Fill metrics (calculated)
    'total_quantity_filled': 'total_quantity_filled',
    'fill_ratio': 'fill_ratio',
    'avg_execution_price': 'avg_execution_price',
    'execution_duration_sec': 'execution_duration_sec',
    'num_trades': 'num_trades',
    
    # Simulation metrics (calculated)
    'simulated_fill_ratio': 'simulated_fill_ratio',
    'simulated_execution_price': 'simulated_execution_price',
    'simulated_num_matches': 'simulated_num_matches',
    'residual_fill_qty': 'residual_fill_qty',
}


# ============================================================================
# SCENARIO TYPES AND THRESHOLDS
# ============================================================================

SCENARIO_TYPES = {
    'A': 'A_Immediate_Full',
    'B': 'B_Eventual_Full',
    'C': 'C_Partial_None',
}

SCENARIO_THRESHOLDS = {
    'immediate_fill_threshold_ratio': 0.99,  # >= 99% fill = "immediate full"
    'immediate_fill_threshold_seconds': 1.0,  # < 1 second = "immediate"
    'eventual_fill_threshold_seconds': 1.0,  # >= 1 second = "eventual"
    'eventual_fill_threshold_ratio': 0.99,  # >= 99% fill = "full"
}


# ============================================================================
# OUTPUT FILE NAMES
# ============================================================================

OUTPUT_FILES = {
    # Phase 1 - Ingestion outputs
    'centrepoint_orders_raw': 'centrepoint_orders_raw.csv.gz',
    
    # Phase 1.2 - Trade matching outputs
    'centrepoint_trades_raw': 'centrepoint_trades_raw.csv.gz',
    'centrepoint_trades_agg': 'centrepoint_trades_agg.csv.gz',
    
    # Phase 1.3 - Dark book outputs
    'dark_book_state': 'dark_book_state.pkl',
    'order_index': 'order_index.pkl',
    
    # Phase 2.1 - Sweep order filtering
    'sweep_orders_with_trades': 'sweep_orders_with_trades.csv.gz',
    
    # Phase 2.2 - Scenario classification
    'scenario_a_immediate_full': 'scenario_a_immediate_full.csv.gz',
    'scenario_b_eventual_full': 'scenario_b_eventual_full.csv.gz',
    'scenario_c_partial_none': 'scenario_c_partial_none.csv.gz',
    'scenario_summary': 'scenario_summary.csv',
    
    # Phase 3 - Simulation outputs
    'scenario_a_simulation_results': 'scenario_a_simulation_results.csv.gz',
    'scenario_b_simulation_results': 'scenario_b_simulation_results.csv.gz',
    'scenario_c_simulation_results': 'scenario_c_simulation_results.csv.gz',
    
    # Phase 4 - Reports
    'scenario_comparison_summary': 'scenario_comparison_summary.csv.gz',
    'scenario_detailed_comparison': 'scenario_detailed_comparison.csv.gz',
    'order_level_detail': 'order_level_detail.csv.gz',
    'execution_cost_comparison': 'execution_cost_comparison.csv.gz',
    'by_participant': 'by_participant.csv.gz',
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_input_column_name(data_type: str, standard_name: str) -> str:
    """Get the actual input column name for a given standard column name."""
    if data_type not in INPUT_COLUMNS:
        raise ValueError(f"Unknown data type: {data_type}")
    
    if standard_name not in INPUT_COLUMNS[data_type]:
        raise ValueError(f"Unknown column for {data_type}: {standard_name}")
    
    return INPUT_COLUMNS[data_type][standard_name]


def get_standard_column_name(column: str) -> str:
    """Get the standard column name, or return as-is if already standard."""
    for standard, actual in STANDARD_COLUMNS.items():
        if actual == column:
            return standard
    return column


def rename_columns_to_standard(df: pd.DataFrame, data_type: str) -> pd.DataFrame:
    """Rename input columns to standard column names."""
    if data_type not in INPUT_COLUMNS:
        raise ValueError(f"Unknown data type: {data_type}")
    
    rename_map = {v: k for k, v in INPUT_COLUMNS[data_type].items()}
    
    # Only rename columns that exist in the dataframe
    rename_map = {k: v for k, v in rename_map.items() if k in df.columns}
    
    return df.rename(columns=rename_map)


def normalize_to_standard_names(df: pd.DataFrame, data_type: str) -> pd.DataFrame:
    """Normalize DataFrame columns to standard names. Used by Stage 1 before saving processed files."""
    if data_type not in COLUMN_NORMALIZATION_MAP:
        # No normalization defined for this data type, return as-is
        return df
    
    norm_map = COLUMN_NORMALIZATION_MAP[data_type]
    rename_dict = {}
    
    # Build rename dictionary for columns that exist in DataFrame
    for col in df.columns:
        if col in norm_map:
            rename_dict[col] = norm_map[col]
    
    # Apply renaming if any mappings found
    if rename_dict:
        df = df.rename(columns=rename_dict)
    
    return df


def validate_columns(df: pd.DataFrame, required_columns: list, context: str = "") -> bool:
    """Validate that required columns exist in DataFrame."""
    missing = set(required_columns) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {context}: {missing}")
    return True


# ============================================================================
# PIPELINE STAGES
# ============================================================================

# Stage names and descriptions
STAGE_1_NAME = "Data Extraction & Preparation"
STAGE_2_NAME = "Simulation & LOB States"
STAGE_3_NAME = "Per-Security Analysis + Volume Analysis"
STAGE_4_NAME = "Cross-Security Aggregation"

# Volume analysis configuration
VOLUME_BUCKET_METHOD = 'quartile'  # Options: 'quartile', 'quintile', 'custom'
VOLUME_CUSTOM_THRESHOLDS = [100, 500, 1000, 5000]  # Used if method='custom'

# DEPRECATED: Old aggregation directory (incorrect path)
# Use AGGREGATED_DIR at top of file instead


# ============================================================================
# CONFIGURATION SUMMARY
# ============================================================================

def print_config():
    """Print current configuration summary."""
    print("="*80)
    print("PIPELINE CONFIGURATION")
    print("="*80)
    print("\nDataset Configuration:")
    print(f"  Ticker:  {TICKER}")
    print(f"  Date:    {DATE}")
    print("\nSecurity Discovery:")
    print(f"  Auto-discovery enabled:  {AUTO_DISCOVERY_ENABLED}")
    print(f"  Min orders threshold:    {MIN_ORDERS_THRESHOLD}")
    print(f"  Min trades threshold:    {MIN_TRADES_THRESHOLD}")
    print("\nStatistical Testing:")
    print(f"  Tests enabled:           {ENABLE_STATISTICAL_TESTS}")
    print(f"  Force simple stats:      {FORCE_SIMPLE_STATS}")
    print("\nSystem Configuration:")
    print(SYSTEM_CONFIG)
    print(f"\nInput Files:")
    for key, path in INPUT_FILES.items():
        print(f"  {key:15} -> {path}")
    print(f"\nDirectories:")
    print(f"  Processed:   {PROCESSED_DIR}")
    print(f"  Outputs:     {OUTPUTS_DIR}")
    print(f"  Aggregated:  {AGGREGATED_DIR}")
    print(f"\nOrder Types:")
    print(f"  Centre Point: {CENTRE_POINT_ORDER_TYPES}")
    print(f"  Sweep:        {SWEEP_ORDER_TYPE}")
    print("="*80)


if __name__ == '__main__':
    # Test configuration
    print_config()
