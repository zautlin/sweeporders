"""
Simulated Metrics Calculator

This module calculates simulated execution metrics for incoming orders based on
sweep matching simulation results, mirroring the real execution metrics for comparison.
"""

import pandas as pd
import numpy as np


def calculate_simulated_metrics(all_orders, order_summary, match_details):
    """
    Calculate simulated execution metrics for orders.
    
    Args:
        all_orders: DataFrame with all orders (from orders_before_lob.csv)
        order_summary: DataFrame with per-order simulation summary
        match_details: DataFrame with all individual matches
    
    Returns:
        DataFrame with original orders plus simulated metric columns:
            - simulated_matched_quantity: Total quantity matched in simulation
            - simulated_fill_ratio: Fraction of quantity filled (0.0 to 1.0)
            - simulated_fill_status: 'Fully Filled', 'Partially Filled', 'Unfilled'
            - simulated_num_matches: Number of matches with sweep orders
            - simulated_avg_price: Average execution price (weighted by quantity)
            - simulated_total_value: Total value of matched quantity
    """
    
    # Start with all orders
    result = all_orders.copy()
    
    # Rename order_id to orderid if needed
    if 'order_id' in result.columns and 'orderid' not in result.columns:
        result = result.rename(columns={'order_id': 'orderid'})
    
    # Check if order_summary is empty
    if order_summary.empty:
        result['simulated_matched_quantity'] = 0
        result['simulated_num_matches'] = 0
        result['simulated_fill_ratio'] = 0
        result['simulated_fill_status'] = 'Unfilled'
        result['simulated_avg_price'] = 0.0
        result['simulated_total_value'] = 0.0
        return result
    
    # Merge with order summary
    result = result.merge(
        order_summary[['orderid', 'matched_quantity', 'num_matches']],
        on='orderid',
        how='left',
        suffixes=('', '_sim')
    )
    
    # Rename columns with simulated_ prefix
    result = result.rename(columns={
        'matched_quantity': 'simulated_matched_quantity',
        'num_matches': 'simulated_num_matches'
    })
    
    # Fill NaN for orders that didn't participate in simulation
    result['simulated_matched_quantity'] = result['simulated_matched_quantity'].fillna(0)
    result['simulated_num_matches'] = result['simulated_num_matches'].fillna(0).astype(int)
    
    # Calculate fill ratio
    result['simulated_fill_ratio'] = np.where(
        result['quantity'] > 0,
        result['simulated_matched_quantity'] / result['quantity'],
        0
    )
    
    # Determine fill status
    result['simulated_fill_status'] = result.apply(
        lambda row: _determine_fill_status(
            row['simulated_matched_quantity'],
            row['quantity']
        ),
        axis=1
    )
    
    # Calculate average price and total value
    if not match_details.empty:
        price_stats = _calculate_price_metrics(match_details)
        result = result.merge(
            price_stats,
            on='orderid',
            how='left'
        )
    else:
        result['simulated_avg_price'] = 0.0
        result['simulated_total_value'] = 0.0
    
    # Fill NaN for orders without matches
    result['simulated_avg_price'] = result['simulated_avg_price'].fillna(0)
    result['simulated_total_value'] = result['simulated_total_value'].fillna(0)
    
    return result


def _determine_fill_status(matched_qty, total_qty):
    """
    Determine fill status based on matched vs total quantity.
    
    Args:
        matched_qty: Quantity matched in simulation
        total_qty: Total order quantity
    
    Returns:
        Fill status: 'Fully Filled', 'Partially Filled', or 'Unfilled'
    """
    if matched_qty == 0:
        return 'Unfilled'
    elif matched_qty >= total_qty:
        return 'Fully Filled'
    else:
        return 'Partially Filled'


def _calculate_price_metrics(match_details):
    """
    Calculate average price and total value per order from match details.
    
    Args:
        match_details: DataFrame with columns:
            - incoming_orderid
            - matched_quantity
            - price
    
    Returns:
        DataFrame with columns:
            - orderid
            - simulated_avg_price
            - simulated_total_value
    """
    # Calculate total value per match
    match_details = match_details.copy()
    match_details['match_value'] = match_details['matched_quantity'] * match_details['price']
    
    # Aggregate by incoming order
    price_stats = match_details.groupby('incoming_orderid').agg({
        'matched_quantity': 'sum',
        'match_value': 'sum'
    }).reset_index()
    
    # Calculate weighted average price
    price_stats['simulated_avg_price'] = np.where(
        price_stats['matched_quantity'] > 0,
        price_stats['match_value'] / price_stats['matched_quantity'],
        0
    )
    
    # Rename columns
    price_stats = price_stats.rename(columns={
        'incoming_orderid': 'orderid',
        'match_value': 'simulated_total_value'
    })
    
    # Keep only needed columns
    price_stats = price_stats[['orderid', 'simulated_avg_price', 'simulated_total_value']]
    
    return price_stats
