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
