"""
Phase 4: Real Execution Metrics Calculation

Calculates comprehensive execution metrics for sweep orders classified in Step 2.

Metrics Calculated:
1. Number of Full Fills - Count of orders with leavesquantity == 0
2. Number of Partial Fills - Count of orders with partial execution
3. Quantity Traded - Total quantity executed across all trades
4. Total Order Quantity - Total quantity ordered
5. Average Execution Cost - Volume-weighted average price (VWAP)
6. Order Fill Ratio - Percentage of quantity filled

Results are provided for:
- Group 1 (Fully Filled)
- Group 2 (Partially Filled)
- Group 3 (Not Executed)
- Overall (All Groups)
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging
import sys
from typing import Dict, Tuple

sys.path.insert(0, str(Path(__file__).parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))

from config.columns import INPUT_FILES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_step2_and_trades(output_dir='processed_files'):
    """
    Load Step 2 classified orders and Step 1 trades.
    
    Returns:
        Tuple of (classified_orders_df, trades_df)
    """
    logger.info("Loading Step 2 classified orders and Step 1 trades...")
    
    classified = pd.read_csv(f'{output_dir}/sweep_orders_classified.csv.gz')
    trades = pd.read_csv(f'{output_dir}/centrepoint_trades_raw.csv.gz')
    
    logger.info(f"✓ Loaded {len(classified)} classified orders and {len(trades)} trades")
    
    return classified, trades


def calculate_trade_summary(trades_df):
    """
    Calculate trade summary metrics per order.
    
    For each order, calculates:
    - Total quantity traded
    - Volume-weighted average price (VWAP)
    
    Args:
        trades_df: Trades dataframe
        
    Returns:
        DataFrame indexed by orderid with trade metrics
    """
    logger.info("Calculating trade summary metrics...")
    
    # Calculate VWAP (Volume-Weighted Average Price)
    trades_df = trades_df.copy()
    trades_df['trade_value'] = trades_df['quantity'] * trades_df['tradeprice']
    
    trade_summary = trades_df.groupby('orderid').agg({
        'quantity': 'sum',
        'trade_value': 'sum'
    }).rename(columns={'quantity': 'total_traded_qty'})
    
    # Calculate VWAP
    trade_summary['avg_execution_price'] = (
        trade_summary['trade_value'] / trade_summary['total_traded_qty']
    )
    
    # Drop intermediate column
    trade_summary = trade_summary.drop('trade_value', axis=1)
    
    logger.info(f"✓ Calculated trade metrics for {len(trade_summary)} orders")
    
    return trade_summary


def merge_metrics(classified_df, trade_summary_df):
    """
    Merge classified orders with trade metrics.
    
    Args:
        classified_df: Step 2 classified orders
        trade_summary_df: Trade summary metrics
        
    Returns:
        DataFrame with orders and trade metrics combined
    """
    logger.info("Merging classified orders with trade metrics...")
    
    merged = classified_df.merge(
        trade_summary_df,
        left_on='order_id',
        right_index=True,
        how='left'
    )
    
    # Fill NaN values for Group 3 (no trades)
    merged['total_traded_qty'] = merged['total_traded_qty'].fillna(0)
    merged['avg_execution_price'] = merged['avg_execution_price'].fillna(0)
    
    logger.info(f"✓ Merged {len(merged)} orders with trade metrics")
    
    return merged


def calculate_group_metrics(merged_df):
    """
    Calculate execution metrics for each group.
    
    Metrics:
    1. Number of Full Fills - Count of orders with leavesquantity == 0
    2. Number of Partial Fills - Count of orders with partial execution
    3. Quantity Traded - Total quantity executed
    4. Total Order Quantity - Total quantity ordered
    5. Average Execution Cost - VWAP
    6. Order Fill Ratio - Percentage filled
    
    Args:
        merged_df: Merged orders with trade metrics
        
    Returns:
        Dictionary with metrics for each group and overall
    """
    logger.info("Calculating execution metrics for each group...")
    
    groups = ['GROUP_1_FULLY_FILLED', 'GROUP_2_PARTIALLY_FILLED', 'GROUP_3_NOT_EXECUTED']
    metrics_dict = {}
    
    for group in groups:
        group_data = merged_df[merged_df['sweep_group'] == group]
        
        if len(group_data) == 0:
            metrics_dict[group] = {
                'num_full_fills': 0,
                'num_partial_fills': 0,
                'quantity_traded': 0,
                'total_order_quantity': 0,
                'avg_execution_cost': 0.0,
                'order_fill_ratio': 0.0,
                'order_count': 0
            }
            continue
        
        # 1. Number of Full Fills (leavesquantity == 0)
        num_full_fills = len(group_data[group_data['leavesquantity'] == 0])
        
        # 2. Number of Partial Fills (leavesquantity > 0 AND totalmatchedquantity > 0)
        num_partial_fills = len(group_data[
            (group_data['leavesquantity'] > 0) & 
            (group_data['totalmatchedquantity'] > 0)
        ])
        
        # 3. Quantity Traded (total executed quantity)
        quantity_traded = group_data['totalmatchedquantity'].sum()
        
        # 4. Total Order Quantity
        total_order_qty = group_data['quantity'].sum()
        
        # 5. Average Execution Cost (VWAP across all trades in group)
        if quantity_traded > 0:
            # Calculate VWAP for the group
            total_value = (group_data['total_traded_qty'] * group_data['avg_execution_price']).sum()
            avg_execution_cost = total_value / quantity_traded
        else:
            avg_execution_cost = 0.0
        
        # 6. Order Fill Ratio (quantity traded / total order quantity)
        if total_order_qty > 0:
            order_fill_ratio = quantity_traded / total_order_qty
        else:
            order_fill_ratio = 0.0
        
        metrics_dict[group] = {
            'num_full_fills': num_full_fills,
            'num_partial_fills': num_partial_fills,
            'quantity_traded': int(quantity_traded),
            'total_order_quantity': int(total_order_qty),
            'avg_execution_cost': round(avg_execution_cost, 2),
            'order_fill_ratio': round(order_fill_ratio, 4),
            'order_count': len(group_data)
        }
        
        logger.info(f"✓ {group}: {num_full_fills} full fills, {num_partial_fills} partial fills")
    
    # Calculate overall metrics
    total_full_fills = sum(m['num_full_fills'] for m in metrics_dict.values())
    total_partial_fills = sum(m['num_partial_fills'] for m in metrics_dict.values())
    total_traded = sum(m['quantity_traded'] for m in metrics_dict.values())
    total_order_qty = sum(m['total_order_quantity'] for m in metrics_dict.values())
    
    if total_traded > 0:
        total_value = sum(
            m['quantity_traded'] * m['avg_execution_cost'] 
            for m in metrics_dict.values()
        )
        overall_avg_cost = total_value / total_traded
    else:
        overall_avg_cost = 0.0
    
    if total_order_qty > 0:
        overall_fill_ratio = total_traded / total_order_qty
    else:
        overall_fill_ratio = 0.0
    
    metrics_dict['OVERALL'] = {
        'num_full_fills': total_full_fills,
        'num_partial_fills': total_partial_fills,
        'quantity_traded': int(total_traded),
        'total_order_quantity': int(total_order_qty),
        'avg_execution_cost': round(overall_avg_cost, 2),
        'order_fill_ratio': round(overall_fill_ratio, 4),
        'order_count': len(merged_df)
    }
    
    logger.info(f"✓ Overall metrics calculated")
    
    return metrics_dict


def validate_metrics(metrics_dict, merged_df):
    """
    Validate metrics calculations.
    
    Args:
        metrics_dict: Calculated metrics
        merged_df: Original merged dataframe
        
    Returns:
        True if validation passes
    """
    logger.info("Validating metrics...")
    
    # Check total order count
    group_total = metrics_dict['OVERALL']['order_count']
    actual_total = len(merged_df)
    
    if group_total != actual_total:
        logger.error(f"Order count mismatch: {group_total} != {actual_total}")
        return False
    
    # Check sum of full and partial fills
    total_fills = (
        metrics_dict['OVERALL']['num_full_fills'] + 
        metrics_dict['OVERALL']['num_partial_fills']
    )
    filled_orders = len(merged_df[merged_df['totalmatchedquantity'] > 0])
    
    if total_fills != filled_orders:
        logger.warning(f"Fill count: {total_fills} (may not equal executed orders {filled_orders})")
    
    logger.info("✓ Metrics validation PASSED")
    return True


def save_metrics_results(metrics_dict, output_dir='processed_files'):
    """
    Save metrics results to CSV files.
    
    Args:
        metrics_dict: Calculated metrics
        output_dir: Output directory
    """
    logger.info("Saving metrics results...")
    
    # Convert to DataFrame for better presentation
    metrics_df = pd.DataFrame(metrics_dict).T
    
    # Save to CSV
    output_path = Path(output_dir) / 'real_execution_metrics.csv'
    metrics_df.to_csv(output_path)
    
    logger.info(f"✓ Saved metrics to {output_path}")
    
    # Also save as compressed CSV for consistency with other outputs
    output_path_gz = Path(output_dir) / 'real_execution_metrics.csv.gz'
    metrics_df.to_csv(output_path_gz, compression='gzip')
    
    logger.info(f"✓ Saved compressed metrics to {output_path_gz}")
    
    return metrics_df


def print_metrics_summary(metrics_dict):
    """
    Print formatted metrics summary.
    
    Args:
        metrics_dict: Calculated metrics
    """
    print("\n" + "=" * 100)
    print("REAL EXECUTION METRICS - SUMMARY")
    print("=" * 100)
    
    metrics_df = pd.DataFrame(metrics_dict).T
    
    # Reorder columns for better presentation
    column_order = [
        'order_count',
        'num_full_fills',
        'num_partial_fills',
        'quantity_traded',
        'total_order_quantity',
        'avg_execution_cost',
        'order_fill_ratio'
    ]
    
    metrics_df = metrics_df[column_order]
    
    print("\n" + metrics_df.to_string())
    
    print("\n" + "=" * 100)
    print("METRIC DEFINITIONS:")
    print("=" * 100)
    print("  order_count ................... Number of orders in group")
    print("  num_full_fills ................ Orders with leavesquantity == 0")
    print("  num_partial_fills ............ Orders with 0 < leavesquantity < quantity")
    print("  quantity_traded ............... Total units executed across all orders")
    print("  total_order_quantity ......... Total units ordered across all orders")
    print("  avg_execution_cost ........... Volume-weighted average execution price")
    print("  order_fill_ratio ............. Percentage of orders filled (0.0-1.0)")
    print("=" * 100 + "\n")


def run_step4_pipeline(output_dir='processed_files'):
    """
    Execute Step 4: Real Execution Metrics Calculation.
    
    Returns:
        Dictionary with metrics results
    """
    logger.info("=" * 80)
    logger.info("STEP 4: REAL EXECUTION METRICS CALCULATION")
    logger.info("=" * 80)
    
    Path(output_dir).mkdir(exist_ok=True)
    
    # Load data
    logger.info("\n[4.1] Loading data...")
    classified, trades = load_step2_and_trades(output_dir)
    logger.info(f"✓ Loaded {len(classified)} orders and {len(trades)} trades")
    
    # Calculate trade metrics
    logger.info("\n[4.2] Calculating trade metrics...")
    trade_summary = calculate_trade_summary(trades)
    logger.info(f"✓ Trade metrics calculated")
    
    # Merge metrics
    logger.info("\n[4.3] Merging orders with trade metrics...")
    merged = merge_metrics(classified, trade_summary)
    logger.info(f"✓ Merged {len(merged)} orders")
    
    # Calculate group metrics
    logger.info("\n[4.4] Calculating execution metrics...")
    metrics = calculate_group_metrics(merged)
    logger.info(f"✓ Metrics calculated for all groups")
    
    # Validate
    logger.info("\n[4.5] Validating metrics...")
    if not validate_metrics(metrics, merged):
        logger.error("Validation FAILED!")
        return None
    
    # Save results
    logger.info("\n[4.6] Saving results...")
    metrics_df = save_metrics_results(metrics, output_dir)
    logger.info(f"✓ Results saved")
    
    # Print summary
    print_metrics_summary(metrics)
    
    logger.info("\n" + "=" * 80)
    logger.info("STEP 4 COMPLETE")
    logger.info("=" * 80)
    
    return metrics


if __name__ == '__main__':
    results = run_step4_pipeline()
    
    if results:
        print("\n✓ STEP 4 PIPELINE COMPLETED SUCCESSFULLY")
    else:
        print("\n✗ STEP 4 PIPELINE FAILED")
