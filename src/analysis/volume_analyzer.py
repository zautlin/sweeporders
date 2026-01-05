"""
Volume-Based Order Analysis

Analyzes sweep order execution performance segmented by order volume.
Compares real vs simulated execution across different volume buckets (quartiles).

Focuses on two key metrics:
1. Execution Cost difference (real - sim) in bps
2. Execution Time difference (real - sim) in seconds
"""

import pandas as pd
from column_schema import col
import numpy as np
from utils.statistics_layer import StatisticsEngine
from pathlib import Path
import logging
import os

# Try to import scipy for backward compatibility
try:
    from scipy import stats as scipy_stats
    SCIPY_AVAILABLE = True
except ImportError:
    scipy_stats = None
    SCIPY_AVAILABLE = False

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_volume_buckets(df, method='quartile'):
    """
    Create volume buckets for orders based on order_quantity.
    
    Args:
        df: DataFrame with order_quantity column
        method: 'quartile' (4 buckets), 'quintile' (5 buckets), or 'custom'
        
    Returns:
        DataFrame with added columns: volume_bucket, volume_bucket_label
    """
    if method == 'quartile':
        # Split into 4 buckets (Q1, Q2, Q3, Q4)
        df[col.volume.volume_bucket] = pd.qcut(df['order_quantity'], q=4, labels=False, duplicates='drop')
        
        # Create labels
        bucket_labels = {0: 'Q1', 1: 'Q2', 2: 'Q3', 3: 'Q4'}
        df['volume_bucket_label'] = df[col.volume.volume_bucket].map(bucket_labels)
        
    elif method == 'quintile':
        # Split into 5 buckets
        df[col.volume.volume_bucket] = pd.qcut(df['order_quantity'], q=5, labels=False, duplicates='drop')
        
        bucket_labels = {0: 'Quintile 1', 1: 'Quintile 2', 2: 'Quintile 3',
                        3: 'Quintile 4', 4: 'Quintile 5'}
        df['volume_bucket_label'] = df[col.volume.volume_bucket].map(bucket_labels)
    
    # Log bucket ranges
    for bucket in sorted(df[col.volume.volume_bucket].unique()):
        bucket_data = df[df[col.volume.volume_bucket] == bucket]
        min_qty = bucket_data['order_quantity'].min()
        max_qty = bucket_data['order_quantity'].max()
        mean_qty = bucket_data['order_quantity'].mean()
        count = len(bucket_data)
        label = bucket_data['volume_bucket_label'].iloc[0]
        
        logger.info(f"  {label}: {min_qty:.0f}-{max_qty:.0f} units (mean={mean_qty:.0f}, n={count})")
    
    return df


def calculate_volume_bucket_stats(df):
    """
    Calculate statistics for each volume bucket.
    
    Focuses on exec_cost_arrival_diff_bps and exec_time_diff_sec.
    
    Returns:
        DataFrame with statistics by bucket
    """
    results = []
    
    for bucket in sorted(df[col.volume.volume_bucket].unique()):
        bucket_data = df[df[col.volume.volume_bucket] == bucket]
        bucket_label = bucket_data['volume_bucket_label'].iloc[0]
        
        # Basic statistics
        n_orders = len(bucket_data)
        min_qty = bucket_data['order_quantity'].min()
        max_qty = bucket_data['order_quantity'].max()
        mean_qty = bucket_data['order_quantity'].mean()
        median_qty = bucket_data['order_quantity'].median()
        
        # Execution cost difference statistics
        exec_cost_diff = bucket_data['exec_cost_arrival_diff_bps']
        mean_exec_cost_diff = exec_cost_diff.mean()
        median_exec_cost_diff = exec_cost_diff.median()
        std_exec_cost_diff = exec_cost_diff.std()
        
        # Execution time difference statistics
        exec_time_diff = bucket_data['exec_time_diff_sec']
        mean_exec_time_diff = exec_time_diff.mean()
        median_exec_time_diff = exec_time_diff.median()
        std_exec_time_diff = exec_time_diff.std()
        
        # Percentage where dark pool is better
        dark_better_pct = (bucket_data['dark_pool_better'] == True).sum() / n_orders * 100
        
        results.append({
            'volume_bucket': bucket,
            'volume_bucket_label': bucket_label,
            'n_orders': n_orders,
            'min_quantity': min_qty,
            'max_quantity': max_qty,
            'mean_quantity': mean_qty,
            'median_quantity': median_qty,
            'mean_exec_cost_diff_bps': mean_exec_cost_diff,
            'median_exec_cost_diff_bps': median_exec_cost_diff,
            'std_exec_cost_diff_bps': std_exec_cost_diff,
            'mean_exec_time_diff_sec': mean_exec_time_diff,
            'median_exec_time_diff_sec': median_exec_time_diff,
            'std_exec_time_diff_sec': std_exec_time_diff,
            'dark_pool_better_pct': dark_better_pct
        })
    
    return pd.DataFrame(results)


def volume_bucket_statistical_tests(df, stats_engine=None):
    """
    Run paired t-tests for each volume bucket.
    
    Tests if the mean difference is significantly different from 0 within each bucket.
    
    Args:
        df: DataFrame with volume bucket analysis
        stats_engine: StatisticsEngine instance (optional, defaults to enabled)
    
    Returns:
        DataFrame with t-test results by bucket
    """
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    results = []
    
    for bucket in sorted(df[col.volume.volume_bucket].unique()):
        bucket_data = df[df[col.volume.volume_bucket] == bucket]
        bucket_label = bucket_data['volume_bucket_label'].iloc[0]
        n_orders = len(bucket_data)
        
        # Warn if sample size is small
        if n_orders < 30:
            logger.warning(f"  {bucket_label}: Small sample size (n={n_orders}), results may not be reliable")
        
        # Test execution cost difference
        exec_cost_diff = bucket_data['exec_cost_arrival_diff_bps'].dropna()
        if len(exec_cost_diff) > 0:
            # Calculate effect size (always - it's descriptive)
            cohens_d_cost = exec_cost_diff.mean() / exec_cost_diff.std() if exec_cost_diff.std() > 0 else 0
            
            if stats_engine.is_enabled():
                ttest_result = stats_engine.ttest_1samp(exec_cost_diff, 0)
                ci_result = stats_engine.confidence_interval(exec_cost_diff, confidence=0.95)
                
                t_stat_cost = ttest_result.statistic if ttest_result else np.nan
                p_value_cost = ttest_result.pvalue if ttest_result else np.nan
                ci_95_cost = ci_result if ci_result else (np.nan, np.nan)
            else:
                t_stat_cost = p_value_cost = np.nan
                ci_95_cost = (np.nan, np.nan)
        else:
            t_stat_cost = p_value_cost = cohens_d_cost = np.nan
            ci_95_cost = (np.nan, np.nan)
        
        # Test execution time difference
        exec_time_diff = bucket_data['exec_time_diff_sec'].dropna()
        if len(exec_time_diff) > 0:
            # Calculate effect size (always - it's descriptive)
            cohens_d_time = exec_time_diff.mean() / exec_time_diff.std() if exec_time_diff.std() > 0 else 0
            
            if stats_engine.is_enabled():
                ttest_result = stats_engine.ttest_1samp(exec_time_diff, 0)
                ci_result = stats_engine.confidence_interval(exec_time_diff, confidence=0.95)
                
                t_stat_time = ttest_result.statistic if ttest_result else np.nan
                p_value_time = ttest_result.pvalue if ttest_result else np.nan
                ci_95_time = ci_result if ci_result else (np.nan, np.nan)
            else:
                t_stat_time = p_value_time = np.nan
                ci_95_time = (np.nan, np.nan)
        else:
            t_stat_time = p_value_time = cohens_d_time = np.nan
            ci_95_time = (np.nan, np.nan)
        
        results.append({
            'volume_bucket': bucket,
            'volume_bucket_label': bucket_label,
            'n_orders': n_orders,
            # Execution cost test results
            'exec_cost_t_statistic': t_stat_cost,
            'exec_cost_p_value': p_value_cost,
            'exec_cost_cohens_d': cohens_d_cost,
            'exec_cost_ci_95_lower': ci_95_cost[0],
            'exec_cost_ci_95_upper': ci_95_cost[1],
            'exec_cost_significant': p_value_cost < 0.05 if not pd.isna(p_value_cost) else False,
            # Execution time test results
            'exec_time_t_statistic': t_stat_time,
            'exec_time_p_value': p_value_time,
            'exec_time_cohens_d': cohens_d_time,
            'exec_time_ci_95_lower': ci_95_time[0],
            'exec_time_ci_95_upper': ci_95_time[1],
            'exec_time_significant': p_value_time < 0.05 if not pd.isna(p_value_time) else False,
        })
    
    return pd.DataFrame(results)


def compare_across_volume_buckets(df, stats_engine=None):
    """
    Compare differences across volume buckets using ANOVA and pairwise tests.
    
    Tests whether execution performance varies by order size.
    
    Args:
        df: DataFrame with volume bucket analysis
        stats_engine: StatisticsEngine instance (optional, defaults to enabled)
    
    Returns:
        Tuple of (anova_results_df, pairwise_results_df)
    """
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    anova_results = []
    pairwise_results = []
    
    buckets = sorted(df[col.volume.volume_bucket].unique())
    
    # Only run ANOVA if stats enabled
    if stats_engine.is_enabled():
        # ANOVA for execution cost
        exec_cost_groups = [df[df[col.volume.volume_bucket] == b]['exec_cost_arrival_diff_bps'].dropna() 
                            for b in buckets]
        exec_cost_groups = [g for g in exec_cost_groups if len(g) >= 2]
        
        if len(exec_cost_groups) >= 2:
            anova_result = stats_engine.f_oneway(*exec_cost_groups)
            
            if anova_result:
                f_stat_cost = anova_result.statistic
                p_value_cost = anova_result.pvalue
                
                anova_results.append({
                    'metric': 'exec_cost_arrival_diff_bps',
                    'f_statistic': f_stat_cost,
                    'p_value': p_value_cost,
                    'significant': p_value_cost < 0.05
                })
            else:
                logger.warning("ANOVA not available (requires scipy)")
        
        # ANOVA for execution time
        exec_time_groups = [df[df[col.volume.volume_bucket] == b]['exec_time_diff_sec'].dropna() 
                            for b in buckets]
        exec_time_groups = [g for g in exec_time_groups if len(g) >= 2]
        
        if len(exec_time_groups) >= 2:
            anova_result = stats_engine.f_oneway(*exec_time_groups)
            
            if anova_result:
                f_stat_time = anova_result.statistic
                p_value_time = anova_result.pvalue
                
                anova_results.append({
                    'metric': 'exec_time_diff_sec',
                    'f_statistic': f_stat_time,
                    'p_value': p_value_time,
                    'significant': p_value_time < 0.05
                })
            else:
                logger.warning("ANOVA not available (requires scipy)")
        
        # Pairwise comparisons (if ANOVA is significant)
        for anova_result_dict in anova_results:
            if anova_result_dict['significant']:
                metric = anova_result_dict['metric']
                
                for i in range(len(buckets)):
                    for j in range(i+1, len(buckets)):
                        bucket1, bucket2 = buckets[i], buckets[j]
                        
                        group1 = df[df[col.volume.volume_bucket] == bucket1][metric].dropna()
                        group2 = df[df[col.volume.volume_bucket] == bucket2][metric].dropna()
                        
                        if len(group1) >= 2 and len(group2) >= 2:
                            ttest_result = stats_engine.ttest_ind(group1, group2)
                            
                            if ttest_result:
                                label1 = df[df[col.volume.volume_bucket] == bucket1]['volume_bucket_label'].iloc[0]
                                label2 = df[df[col.volume.volume_bucket] == bucket2]['volume_bucket_label'].iloc[0]
                                
                                pairwise_results.append({
                                    'metric': metric,
                                    'bucket1': bucket1,
                                    'bucket1_label': label1,
                                    'bucket2': bucket2,
                                    'bucket2_label': label2,
                                    'mean_diff_bucket1': group1.mean(),
                                    'mean_diff_bucket2': group2.mean(),
                                    't_statistic': ttest_result.statistic,
                                    'p_value': ttest_result.pvalue,
                                    'significant': ttest_result.pvalue < 0.05
                                })
    else:
        logger.info("Cross-bucket ANOVA skipped (stats disabled)")
    
    anova_df = pd.DataFrame(anova_results) if anova_results else pd.DataFrame()
    pairwise_df = pd.DataFrame(pairwise_results) if pairwise_results else pd.DataFrame()
    
    return anova_df, pairwise_df


def create_output_directory(outputs_dir, partition_key):
    """Create volume_analysis output directory."""
    date_str, orderbookid = partition_key.split('/')
    volume_dir = Path(outputs_dir) / date_str / orderbookid / 'volume_analysis'
    volume_dir.mkdir(parents=True, exist_ok=True)
    return str(volume_dir)


def save_volume_analysis(volume_dir, stats_df, tests_df, anova_df, pairwise_df):
    """Save volume analysis results to CSV files."""
    # Save summary statistics
    summary_path = Path(volume_dir) / 'volume_bucket_summary.csv'
    stats_df.to_csv(summary_path, index=False)
    logger.info(f"  Saved: {summary_path}")
    
    # Save statistical tests
    tests_path = Path(volume_dir) / 'volume_bucket_statistical_tests.csv'
    tests_df.to_csv(tests_path, index=False)
    logger.info(f"  Saved: {tests_path}")
    
    # Save ANOVA results if available
    if not anova_df.empty:
        anova_path = Path(volume_dir) / 'volume_bucket_anova.csv'
        anova_df.to_csv(anova_path, index=False)
        logger.info(f"  Saved: {anova_path}")
    
    # Save pairwise comparisons if available
    if not pairwise_df.empty:
        pairwise_path = Path(volume_dir) / 'volume_bucket_pairwise.csv'
        pairwise_df.to_csv(pairwise_path, index=False)
        logger.info(f"  Saved: {pairwise_path}")


def generate_volume_analysis_report(stats_df, tests_df, anova_df, pairwise_df, volume_dir):
    """Generate human-readable text report for volume analysis."""
    report_lines = []
    
    def add_line(line=""):
        report_lines.append(line)
    
    add_line("="*80)
    add_line("VOLUME-BASED ANALYSIS REPORT")
    add_line("="*80)
    add_line()
    
    add_line("SUMMARY BY VOLUME BUCKET")
    add_line("-"*80)
    add_line()
    
    for _, row in stats_df.iterrows():
        add_line(f"{row['volume_bucket_label']}:")
        add_line(f"  Orders: {row[col.volume.n_orders]:,}")
        add_line(f"  Quantity Range: {row['min_quantity']:.0f} - {row['max_quantity']:.0f} units")
        add_line(f"  Mean Quantity: {row['mean_quantity']:.0f} units")
        add_line(f"")
        add_line(f"  Execution Cost Difference:")
        add_line(f"    Mean: {row['mean_exec_cost_diff_bps']:+.2f} bps")
        add_line(f"    Median: {row['median_exec_cost_diff_bps']:+.2f} bps")
        add_line(f"")
        add_line(f"  Execution Time Difference:")
        add_line(f"    Mean: {row['mean_exec_time_diff_sec']:+.2f} sec")
        add_line(f"    Median: {row['median_exec_time_diff_sec']:+.2f} sec")
        add_line(f"")
        add_line(f"  Dark Pool Better: {row['dark_pool_better_pct']:.2f}% of orders")
        add_line()
    
    add_line("="*80)
    add_line("STATISTICAL TESTS BY VOLUME BUCKET")
    add_line("="*80)
    add_line()
    
    for _, row in tests_df.iterrows():
        add_line(f"{row['volume_bucket_label']} (n={row[col.volume.n_orders]}):")
        add_line(f"")
        add_line(f"  Execution Cost Difference:")
        
        if pd.notna(row['exec_cost_t_statistic']) and pd.notna(row['exec_cost_p_value']):
            add_line(f"    t-statistic: {row['exec_cost_t_statistic']:.4f}")
            add_line(f"    p-value: {row['exec_cost_p_value']:.2e}")
            add_line(f"    Significant: {row['exec_cost_significant']}")
        
        if pd.notna(row['exec_cost_ci_95_lower']) and pd.notna(row['exec_cost_ci_95_upper']):
            add_line(f"    95% CI: [{row['exec_cost_ci_95_lower']:+.2f}, {row['exec_cost_ci_95_upper']:+.2f}]")
        
        add_line(f"")
        add_line(f"  Execution Time Difference:")
        
        if pd.notna(row['exec_time_t_statistic']) and pd.notna(row['exec_time_p_value']):
            add_line(f"    t-statistic: {row['exec_time_t_statistic']:.4f}")
            add_line(f"    p-value: {row['exec_time_p_value']:.2e}")
            add_line(f"    Significant: {row['exec_time_significant']}")
        
        if pd.notna(row['exec_time_ci_95_lower']) and pd.notna(row['exec_time_ci_95_upper']):
            add_line(f"    95% CI: [{row['exec_time_ci_95_lower']:+.2f}, {row['exec_time_ci_95_upper']:+.2f}]")
        
        add_line()
    
    if not anova_df.empty:
        add_line("="*80)
        add_line("CROSS-BUCKET COMPARISON (ANOVA)")
        add_line("="*80)
        add_line()
        
        for _, row in anova_df.iterrows():
            metric_name = "Execution Cost" if "cost" in row['metric'] else "Execution Time"
            add_line(f"{metric_name}:")
            add_line(f"  F-statistic: {row['f_statistic']:.4f}")
            add_line(f"  p-value: {row[col.stats.p_value]:.2e}")
            add_line(f"  Significant difference across buckets: {row['significant']}")
            add_line()
    
    if not pairwise_df.empty:
        add_line("="*80)
        add_line("PAIRWISE COMPARISONS")
        add_line("="*80)
        add_line()
        
        for _, row in pairwise_df.iterrows():
            if row['significant']:
                metric_name = "Execution Cost" if "cost" in row['metric'] else "Execution Time"
                add_line(f"{metric_name}: {row['bucket1_label']} vs {row['bucket2_label']}")
                add_line(f"  {row['bucket1_label']}: {row['mean_diff_bucket1']:+.2f}")
                add_line(f"  {row['bucket2_label']}: {row['mean_diff_bucket2']:+.2f}")
                add_line(f"  p-value: {row[col.stats.p_value]:.2e}")
                add_line()
    
    add_line("="*80)
    add_line("END OF REPORT")
    add_line("="*80)
    
    # Save report
    report_path = Path(volume_dir) / 'volume_analysis_report.txt'
    with open(report_path, 'w') as f:
        f.write("\n".join(report_lines))
    
    logger.info(f"  Saved: {report_path}")


def analyze_by_volume(outputs_dir, partition_keys, method='quartile', stats_engine=None):
    """
    Main function: Analyze sweep order execution by volume buckets.
    
    Args:
        outputs_dir: Path to outputs directory
        partition_keys: List of partition keys (format: 'date/orderbookid')
        method: Volume bucketing method ('quartile', 'quintile', or 'custom')
        stats_engine: StatisticsEngine instance (optional, defaults to enabled)
        
    Returns:
        Dict with summary of analysis results
    """
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    logger.info("\n" + "="*80)
    logger.info("VOLUME-BASED ANALYSIS")
    logger.info("="*80)
    logger.info(f"Statistics: {'ENABLED' if stats_engine.is_enabled() else 'DISABLED'}")
    if stats_engine.is_enabled():
        logger.info(f"Statistics Tier: {stats_engine.get_tier_name()}")
    logger.info("="*80)
    
    summary = {}
    
    for partition_key in partition_keys:
        logger.info(f"\nProcessing partition: {partition_key}")
        
        # Load sweep order comparison data
        date_str, orderbookid = partition_key.split('/')
        comparison_file = Path(outputs_dir) / date_str / orderbookid / 'matched' / 'sweep_order_comparison_detailed.csv'
        
        if not comparison_file.exists():
            logger.warning(f"  Comparison file not found: {comparison_file}")
            continue
        
        df = pd.read_csv(comparison_file)
        logger.info(f"  Loaded {len(df)} matched orders")
        
        # Create volume buckets
        logger.info(f"  Creating volume buckets (method={method})...")
        df = create_volume_buckets(df, method=method)
        
        # Calculate statistics
        logger.info(f"  Calculating statistics by bucket...")
        stats_df = calculate_volume_bucket_stats(df)
        
        # Run statistical tests
        logger.info(f"  Running statistical tests...")
        tests_df = volume_bucket_statistical_tests(df, stats_engine)
        
        # Compare across buckets
        logger.info(f"  Comparing across buckets...")
        anova_df, pairwise_df = compare_across_volume_buckets(df, stats_engine)
        
        # Create output directory
        volume_dir = create_output_directory(outputs_dir, partition_key)
        
        # Save results
        logger.info(f"  Saving results...")
        save_volume_analysis(volume_dir, stats_df, tests_df, anova_df, pairwise_df)
        
        # Generate report
        generate_volume_analysis_report(stats_df, tests_df, anova_df, pairwise_df, volume_dir)
        
        # Store summary
        summary[partition_key] = {
            'n_orders': len(df),
            'n_buckets': len(stats_df),
            'output_dir': volume_dir
        }
        
        logger.info(f"  ✓ Volume analysis complete for {partition_key}")
    
    return summary


if __name__ == '__main__':
    # Example usage
    import config
    
    # Get partition keys from processed directory
    from pathlib import Path
    processed_path = Path(config.PROCESSED_DIR)
    partition_keys = [f"{d.parent.name}/{d.name}" for d in processed_path.rglob('*') 
                     if d.is_dir() and d.parent.name != 'processed']
    
    if partition_keys:
        analyze_by_volume(config.OUTPUTS_DIR, partition_keys, method='quartile')
    else:
        print("No partition keys found")
