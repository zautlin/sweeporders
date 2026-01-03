"""
Metrics Generator Module

Handles calculation of simulated execution metrics and comparison with real execution:
- Calculate simulated metrics for orders (matched quantity, fill ratio, fill status, prices)
- Compare real vs simulated execution by order groups
- Generate detailed comparison reports
- Calculate statistical summaries and differences
"""

import pandas as pd
import numpy as np
from pathlib import Path


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


# ============================================================================
# NEW: SWEEP ORDER COMPARISON FUNCTIONS
# ============================================================================

def compare_sweep_execution(sweep_order_summary, orders_after_matching, trades_agg, groups):
    """
    Compare simulated vs real execution for SWEEP ORDERS ONLY.
    
    Args:
        sweep_order_summary: DataFrame from simulation (per sweep order)
        orders_after_matching: DataFrame with real final state of orders
        trades_agg: DataFrame with aggregated trade data
        groups: Dictionary mapping group names to sweep order DataFrames
    
    Returns:
        Dictionary containing:
            - 'sweep_comparison': Per-order comparison (sweep orders only)
            - 'group_summary': Summary statistics by group
            - 'statistical_tests': T-test results
            - 'size_analysis': Analysis segmented by order size
    """
    
    # Standardize column names in groups dictionary
    standardized_groups = {}
    for group_name, group_df in groups.items():
        group_df_copy = group_df.copy()
        if 'order_id' in group_df_copy.columns and 'orderid' not in group_df_copy.columns:
            group_df_copy = group_df_copy.rename(columns={'order_id': 'orderid'})
        # Ensure orderid is int64
        if 'orderid' in group_df_copy.columns:
            group_df_copy['orderid'] = group_df_copy['orderid'].astype('int64')
        standardized_groups[group_name] = group_df_copy
    groups = standardized_groups
    
    # Filter orders_after_matching for sweep orders only (type 2048)
    sweep_orders_real = orders_after_matching[
        orders_after_matching['exchangeordertype'] == 2048
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
    comparison['real_matched_quantity'] = comparison['totalmatchedquantity'].fillna(0)
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
    comparison['group'] = comparison['orderid'].apply(lambda x: _find_order_group(x, groups))
    
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
        # Handle both 'orderid' and 'order_id' column names
        if 'orderid' in group_df.columns:
            orderid_col = 'orderid'
        elif 'order_id' in group_df.columns:
            orderid_col = 'order_id'
        else:
            continue
        
        # Ensure orderid is int64 for comparison
        try:
            if int(orderid) in group_df[orderid_col].astype('int64').values:
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


def _calculate_statistical_tests(comparison_df):
    """
    Calculate paired t-tests comparing simulated vs real execution.
    
    Tests are performed on:
    - Matched quantity
    - Fill ratio
    - Number of matches
    
    Segmented by:
    - Overall
    - By group
    - By size category
    """
    from scipy import stats
    
    results = []
    
    # Overall tests
    results.extend(_run_ttests(comparison_df, 'Overall', 'All'))
    
    # Tests by group
    for group_name in comparison_df['group'].unique():
        if group_name == 'Unknown':
            continue
        group_data = comparison_df[comparison_df['group'] == group_name]
        if len(group_data) >= 2:  # Need at least 2 samples for t-test
            results.extend(_run_ttests(group_data, 'Group', group_name))
    
    # Tests by size category
    for size_cat in comparison_df['size_category'].unique():
        size_data = comparison_df[comparison_df['size_category'] == size_cat]
        if len(size_data) >= 2:
            results.extend(_run_ttests(size_data, 'Size', size_cat))
    
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
                results.extend(_run_ttests(segment_data, f'Group-Size', f'{group_name}_{size_cat}'))
    
    return pd.DataFrame(results)


def _run_ttests(data, segment_type, segment_name):
    """Run paired t-tests on a data segment."""
    from scipy import stats
    
    results = []
    
    # Only run tests if we have enough data
    if len(data) < 2:
        return results
    
    # Test 1: Matched Quantity
    real_matched = data['real_matched_quantity'].values
    sim_matched = data['simulated_matched_quantity'].values
    
    if len(real_matched) >= 2:
        t_stat, p_value = stats.ttest_rel(sim_matched, real_matched)
        mean_diff = (sim_matched - real_matched).mean()
        std_diff = (sim_matched - real_matched).std()
        ci_lower, ci_upper = stats.t.interval(
            0.95, 
            len(real_matched) - 1,
            loc=mean_diff,
            scale=stats.sem(sim_matched - real_matched)
        )
        
        results.append({
            'segment_type': segment_type,
            'segment_name': segment_name,
            'metric': 'Matched Quantity',
            'n_samples': len(data),
            'mean_real': real_matched.mean(),
            'mean_simulated': sim_matched.mean(),
            'mean_difference': mean_diff,
            'std_difference': std_diff,
            't_statistic': t_stat,
            'p_value': p_value,
            'significant_5pct': p_value < 0.05,
            'significant_1pct': p_value < 0.01,
            'ci_95_lower': ci_lower,
            'ci_95_upper': ci_upper
        })
    
    # Test 2: Fill Ratio
    real_fill = data['real_fill_ratio'].values
    sim_fill = data['simulated_fill_ratio'].values
    
    if len(real_fill) >= 2:
        t_stat, p_value = stats.ttest_rel(sim_fill, real_fill)
        mean_diff = (sim_fill - real_fill).mean()
        std_diff = (sim_fill - real_fill).std()
        ci_lower, ci_upper = stats.t.interval(
            0.95, 
            len(real_fill) - 1,
            loc=mean_diff,
            scale=stats.sem(sim_fill - real_fill)
        )
        
        results.append({
            'segment_type': segment_type,
            'segment_name': segment_name,
            'metric': 'Fill Ratio',
            'n_samples': len(data),
            'mean_real': real_fill.mean(),
            'mean_simulated': sim_fill.mean(),
            'mean_difference': mean_diff,
            'std_difference': std_diff,
            't_statistic': t_stat,
            'p_value': p_value,
            'significant_5pct': p_value < 0.05,
            'significant_1pct': p_value < 0.01,
            'ci_95_lower': ci_lower,
            'ci_95_upper': ci_upper
        })
    
    # Test 3: Number of Matches
    real_num = data['real_num_matches'].values
    sim_num = data['simulated_num_matches'].values
    
    if len(real_num) >= 2:
        t_stat, p_value = stats.ttest_rel(sim_num, real_num)
        mean_diff = (sim_num - real_num).mean()
        std_diff = (sim_num - real_num).std()
        ci_lower, ci_upper = stats.t.interval(
            0.95, 
            len(real_num) - 1,
            loc=mean_diff,
            scale=stats.sem(sim_num - real_num)
        )
        
        results.append({
            'segment_type': segment_type,
            'segment_name': segment_name,
            'metric': 'Number of Matches',
            'n_samples': len(data),
            'mean_real': real_num.mean(),
            'mean_simulated': sim_num.mean(),
            'mean_difference': mean_diff,
            'std_difference': std_diff,
            't_statistic': t_stat,
            'p_value': p_value,
            'significant_5pct': p_value < 0.05,
            'significant_1pct': p_value < 0.01,
            'ci_95_lower': ci_lower,
            'ci_95_upper': ci_upper
        })
    
    return results


def _calculate_size_analysis(comparison_df):
    """Calculate detailed analysis by order size category."""
    
    analyses = []
    
    for size_cat in ['Small', 'Medium', 'Large']:
        size_data = comparison_df[comparison_df['size_category'] == size_cat]
        
        if len(size_data) == 0:
            continue
        
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
    """
    Generate comprehensive comparison reports for sweep orders.
    
    Args:
        partition_key: Partition identifier
        comparison_results: Dictionary from compare_sweep_execution()
        output_dir: Directory to save reports
    
    Returns:
        Dictionary mapping report type to file path
    """
    
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
