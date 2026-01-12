"""
Metrics Generator Module

Handles calculation of simulated execution metrics and comparison with real execution:
- Calculate simulated metrics for orders (matched quantity, fill ratio, fill status, prices)
- Compare real vs simulated execution by order groups
- Generate detailed comparison reports
- Calculate statistical summaries and differences
"""

import pandas as pd
from config.column_schema import col
import numpy as np
from pathlib import Path
from utils.statistics_layer import StatisticsEngine
from .trade_metrics_calculator import calculate_trade_metrics
from utils import file_utils as fu

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
    
    details = []
    
    real_matched_col = 'totalmatchedquantity' if 'totalmatchedquantity' in group_df.columns else None
    
    for _, order in group_df.iterrows():
        real_matched = order[real_matched_col] if real_matched_col else 0
        simulated_matched = order['simulated_matched_quantity']
        quantity = order[col.common.quantity]
        
        detail = {
            'group': group_name,
            'orderid': order[col.common.orderid],
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
        # If no order_summary, get from simulated trades aggressor rows
        sweep_orderids = simulated_trades[simulated_trades['passiveaggressive'] == 1]['orderid'].unique().tolist()
    
    # Create order context for metrics calculation
    if orders_df is not None and not orders_df.empty:
        # Use provided orders (has arrival NBBO from orders_before/orders_after)
        order_context = orders_df[orders_df['orderid'].isin(sweep_orderids)].copy()
        
        # Ensure required columns exist
        if 'timestamp' not in order_context.columns and 'arrival_time' in order_context.columns:
            order_context['timestamp'] = order_context['arrival_time']
    else:
        # Fallback: create minimal order data from simulated trades (no arrival NBBO)
        aggressor_trades = simulated_trades[simulated_trades['passiveaggressive'] == 1]
        order_context = aggressor_trades.groupby('orderid').agg({
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
        role_filter='aggressor',  # Only aggressor rows
        prefix='sim_',  # Add sim_ prefix
        is_simulated=True  # Simulated trades
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
    
    import utils.file_utils as fu
    
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

