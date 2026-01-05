"""Data normalization and transformation utilities."""

import pandas as pd


def normalize_column_names(df):
    """Standardize column names (order_id → orderid, security_code → orderbookid)."""
    df = df.copy()
    
    # Standardize orderid
    if 'order_id' in df.columns and 'orderid' not in df.columns:
        df = df.rename(columns={'order_id': 'orderid'})
    
    # Standardize orderbookid  
    if 'security_code' in df.columns and 'orderbookid' not in df.columns:
        df = df.rename(columns={'security_code': 'orderbookid'})
    elif 'securitycode' in df.columns and 'orderbookid' not in df.columns:
        df = df.rename(columns={'securitycode': 'orderbookid'})
    
    return df


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
