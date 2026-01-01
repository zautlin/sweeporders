"""
Column Names Configuration
Centralized mapping for column names used throughout the pipeline.
This ensures consistent column naming across all modules.
"""

import pandas as pd

# ============================================================================
# INPUT DATA COLUMNS (Raw data from source files)
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

# ============================================================================
# STANDARD COLUMN NAMES (Normalized names used in code)
# ============================================================================

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
# ORDER TYPES
# ============================================================================

CENTRE_POINT_ORDER_TYPES = [64, 256, 2048, 4096, 4098]

# Order side values
ORDER_SIDE = {
    'BUY': 1,
    'SELL': 2,
}

# Scenario types
SCENARIO_TYPES = {
    'A': 'A_Immediate_Full',
    'B': 'B_Eventual_Full',
    'C': 'C_Partial_None',
}

# ============================================================================
# SCENARIO CLASSIFICATION THRESHOLDS
# ============================================================================

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
# INPUT DATA FILE PATHS
# ============================================================================

INPUT_FILES = {
    'orders': 'data/orders/drr_orders.csv',
    'trades': 'data/trades/drr_trades_segment_1.csv',
    'nbbo': 'data/nbbo/nbbo.csv',
    'participants': 'data/participants/par.csv',
    'reference': 'data/reference/ob.csv',
    'session': 'data/session/session.csv',
}

# ============================================================================
# COLUMN SELECTION HELPERS
# ============================================================================

def get_input_column_name(data_type: str, standard_name: str) -> str:
    """
    Get the actual input column name for a given standard column name.
    
    Args:
        data_type: Type of input data ('orders', 'trades')
        standard_name: Standard column name
        
    Returns:
        Actual column name from input file
    """
    if data_type not in INPUT_COLUMNS:
        raise ValueError(f"Unknown data type: {data_type}")
    
    if standard_name not in INPUT_COLUMNS[data_type]:
        raise ValueError(f"Unknown column for {data_type}: {standard_name}")
    
    return INPUT_COLUMNS[data_type][standard_name]


def get_standard_column_name(column: str) -> str:
    """
    Get the standard column name, or return as-is if already standard.
    
    Args:
        column: Column name
        
    Returns:
        Standard column name
    """
    for standard, actual in STANDARD_COLUMNS.items():
        if actual == column:
            return standard
    return column


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def rename_columns_to_standard(df: pd.DataFrame, data_type: str) -> pd.DataFrame:
    """
    Rename input columns to standard column names.
    
    Args:
        df: Input DataFrame
        data_type: Type of input data ('orders', 'trades')
        
    Returns:
        DataFrame with standard column names
    """
    import pandas as pd
    
    if data_type not in INPUT_COLUMNS:
        raise ValueError(f"Unknown data type: {data_type}")
    
    rename_map = {v: k for k, v in INPUT_COLUMNS[data_type].items()}
    
    # Only rename columns that exist in the dataframe
    rename_map = {k: v for k, v in rename_map.items() if k in df.columns}
    
    return df.rename(columns=rename_map)


# ============================================================================
# VALIDATION HELPERS
# ============================================================================

def validate_columns(df: pd.DataFrame, required_columns: list, context: str = "") -> bool:
    """
    Validate that required columns exist in DataFrame.
    
    Args:
        df: DataFrame to validate
        required_columns: List of required column names
        context: Context string for error messages
        
    Returns:
        True if all columns exist
        
    Raises:
        ValueError if any required column is missing
    """
    missing = set(required_columns) - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in {context}: {missing}")
    return True
