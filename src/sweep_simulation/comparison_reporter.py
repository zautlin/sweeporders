"""
Comparison Reporter

This module compares real execution with simulated execution results, generating
detailed reports analyzing the differences by order group and statistical significance.
"""

from pathlib import Path
import pandas as pd
import numpy as np


def compare_by_group(orders_with_metrics, groups):
    """
    Compare real vs simulated metrics across all groups.
    
    Groups are defined based on REAL execution:
        - Group 1: Fully Filled (leavesquantity == 0)
        - Group 2: Partially Filled (leavesquantity > 0 AND totalmatchedquantity > 0)
        - Group 3: Unfilled (leavesquantity > 0 AND totalmatchedquantity == 0)
    
    Args:
        orders_with_metrics: DataFrame with both real and simulated metrics
        groups: Dictionary mapping group names to order DataFrames
    
    Returns:
        Dictionary containing:
            - 'group_summary': Group-level comparison statistics
            - 'order_details': Order-level comparison details
            - 'group_analysis': Detailed group analysis
    """
    
    group_summaries = []
    all_order_details = []
    group_analyses = []
    
    for group_name, group_orders in groups.items():
        # Get orders with metrics for this group
        if 'order_id' in group_orders.columns:
            group_orderids = group_orders['order_id'].values
            orderid_col = 'orderid' if 'orderid' in orders_with_metrics.columns else 'order_id'
        else:
            group_orderids = group_orders['orderid'].values
            orderid_col = 'orderid'
        
        group_with_metrics = orders_with_metrics[
            orders_with_metrics[orderid_col].isin(group_orderids)
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
        'real_total_quantity': group_df['quantity'].sum(),
        'real_matched_quantity': group_df.get('totalmatchedquantity', pd.Series([0] * len(group_df))).sum(),
        'real_avg_fill_ratio': group_df.get('totalmatchedquantity', pd.Series([0] * len(group_df))).sum() / group_df['quantity'].sum() if group_df['quantity'].sum() > 0 else 0,
        
        # Simulated execution stats
        'simulated_total_quantity': group_df['quantity'].sum(),
        'simulated_matched_quantity': group_df['simulated_matched_quantity'].sum(),
        'simulated_avg_fill_ratio': group_df['simulated_matched_quantity'].sum() / group_df['quantity'].sum() if group_df['quantity'].sum() > 0 else 0,
        
        # Comparison
        'quantity_difference': group_df['simulated_matched_quantity'].sum() - group_df.get('totalmatchedquantity', pd.Series([0] * len(group_df))).sum(),
        'fill_ratio_difference': (group_df['simulated_matched_quantity'].sum() - group_df.get('totalmatchedquantity', pd.Series([0] * len(group_df))).sum()) / group_df['quantity'].sum() if group_df['quantity'].sum() > 0 else 0,
    }
    
    # Count fill status changes
    if 'simulated_fill_status' in group_df.columns:
        summary['num_fully_filled_simulated'] = (group_df['simulated_fill_status'] == 'Fully Filled').sum()
        summary['num_partially_filled_simulated'] = (group_df['simulated_fill_status'] == 'Partially Filled').sum()
        summary['num_unfilled_simulated'] = (group_df['simulated_fill_status'] == 'Unfilled').sum()
    
    return summary


def _calculate_order_details(group_name, group_df):
    """Calculate order-level comparison details."""
    
    details = []
    
    real_matched_col = 'totalmatchedquantity' if 'totalmatchedquantity' in group_df.columns else None
    
    for _, order in group_df.iterrows():
        real_matched = order[real_matched_col] if real_matched_col else 0
        simulated_matched = order['simulated_matched_quantity']
        quantity = order['quantity']
        
        detail = {
            'group': group_name,
            'orderid': order['orderid'],
            'quantity': quantity,
            'real_matched': real_matched,
            'simulated_matched': simulated_matched,
            'difference': simulated_matched - real_matched,
            'real_fill_ratio': real_matched / quantity if quantity > 0 else 0,
            'simulated_fill_ratio': simulated_matched / quantity if quantity > 0 else 0,
            'fill_ratio_change': (simulated_matched - real_matched) / quantity if quantity > 0 else 0,
        }
        
        # Add status if available
        if 'simulated_fill_status' in order:
            detail['simulated_fill_status'] = order['simulated_fill_status']
        
        details.append(detail)
    
    return details


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
    total_quantity = group_df['quantity'].sum()
    if total_quantity > 0:
        analysis['total_quantity_impact_pct'] = (differences.sum() / total_quantity) * 100
    else:
        analysis['total_quantity_impact_pct'] = 0
    
    return analysis


def generate_comparison_reports(partition_key, comparison_data, output_dir):
    """
    Generate comparison reports for a partition.
    
    Args:
        partition_key: Partition identifier (e.g., "2024-09-05/110621")
        comparison_data: Dictionary containing:
            - 'group_summary': Group-level comparison
            - 'order_details': Order-level details
            - 'group_analysis': Detailed group analysis
        output_dir: Directory to write report files
    
    Returns:
        Dictionary mapping report type to file path
    """
    
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
        
        for _, row in analysis_df.iterrows():
            stats.append({
                'metric': f"{row['group']} - Mean Difference",
                'value': row['mean_difference'],
                'description': 'Average difference between simulated and real matched quantity'
            })
            
            stats.append({
                'metric': f"{row['group']} - Std Difference",
                'value': row['std_difference'],
                'description': 'Standard deviation of differences'
            })
            
            stats.append({
                'metric': f"{row['group']} - % Better in Simulation",
                'value': row['pct_simulated_better'],
                'description': 'Percentage of orders with better fill in simulation'
            })
            
            stats.append({
                'metric': f"{row['group']} - Total Quantity Impact %",
                'value': row['total_quantity_impact_pct'],
                'description': 'Total impact on quantity as percentage'
            })
    
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
