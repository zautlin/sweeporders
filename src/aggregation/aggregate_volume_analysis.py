"""
Aggregate Volume Analysis Results Across Securities

This script scans the data/outputs directory for all volume_bucket_summary.csv
files from different securities and dates, merges them into unified datasets,
and performs cross-security volume bucket comparisons.

Usage:
    python src/aggregate_volume_analysis.py
    
Outputs:
    data/outputs/aggregated/aggregated_volume_summary.csv - All volume bucket summaries
    data/outputs/aggregated/aggregated_volume_cross_security_tests.csv - Statistical tests
    data/outputs/aggregated/aggregated_volume_report.txt - Human-readable report
"""

import pandas as pd
from config.column_schema import col
import numpy as np
from pathlib import Path
import logging
from utils.statistics_layer import StatisticsEngine
from typing import List, Tuple, Dict, Any

# Try to import scipy for backward compatibility
try:
    from scipy import stats as scipy_stats
    SCIPY_AVAILABLE = True
except ImportError:
    scipy_stats = None
    SCIPY_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Security mapping from orderbookid to ticker
SECURITY_MAPPING = {
    '110621': 'DRR',
    '85603': 'WTC',
    '70616': 'BHP',
    '124055': 'CBA'
}


def find_volume_analysis_files(outputs_dir: str = 'data/outputs') -> List[Tuple[str, str, str, str]]:
    """Scan the outputs directory for all volume_bucket_summary.csv files."""
    outputs_path = Path(outputs_dir)
    found_files = []
    
    if not outputs_path.exists():
        logger.error(f"Outputs directory not found: {outputs_dir}")
        return found_files
    
    # Pattern: data/outputs/{date}/{orderbookid}/volume_analysis/volume_bucket_summary.csv
    for date_dir in outputs_path.iterdir():
        if not date_dir.is_dir() or date_dir.name == 'aggregated':
            continue
            
        date_str = date_dir.name
        
        for orderbook_dir in date_dir.iterdir():
            if not orderbook_dir.is_dir():
                continue
                
            orderbookid = orderbook_dir.name
            ticker = SECURITY_MAPPING.get(orderbookid, f'UNKNOWN_{orderbookid}')
            
            # Look for the volume bucket summary file
            volume_file = orderbook_dir / 'volume_analysis' / 'volume_bucket_summary.csv'
            
            if volume_file.exists():
                found_files.append((
                    str(volume_file),
                    date_str,
                    orderbookid,
                    ticker
                ))
                logger.info(f"Found volume analysis: {ticker} ({date_str}) - {orderbookid}")
            else:
                logger.debug(f"No volume analysis for {ticker} ({date_str}) - {orderbookid}")
    
    return found_files


def load_and_tag_volume_file(file_path: str, date_str: str, orderbookid: str, ticker: str) -> pd.DataFrame:
    """Load a volume bucket summary CSV and add identifying columns."""
    try:
        df = pd.read_csv(file_path)
        
        # Add identifying columns at the beginning
        df.insert(0, 'ticker', ticker)
        df.insert(1, 'orderbookid', orderbookid)
        df.insert(2, 'date', date_str)
        
        logger.info(f"Loaded {len(df)} volume buckets from {ticker}")
        return df
        
    except Exception as e:
        logger.error(f"Error loading {file_path}: {str(e)}")
        return pd.DataFrame()


def aggregate_volume_summaries(outputs_dir: str = 'data/outputs') -> pd.DataFrame:
    """Aggregate all volume bucket summaries into a single DataFrame."""
    logger.info("=" * 80)
    logger.info("AGGREGATING VOLUME BUCKET SUMMARIES")
    logger.info("=" * 80)
    
    # Find all volume analysis files
    files = find_volume_analysis_files(outputs_dir)
    
    if not files:
        logger.warning("No volume analysis files found!")
        return pd.DataFrame()
    
    logger.info(f"Found {len(files)} volume analysis files")
    
    # Load and combine all files
    all_dfs = []
    for file_path, date_str, orderbookid, ticker in files:
        df = load_and_tag_volume_file(file_path, date_str, orderbookid, ticker)
        if not df.empty:
            all_dfs.append(df)
    
    if not all_dfs:
        logger.warning("No data loaded from volume analysis files!")
        return pd.DataFrame()
    
    # Concatenate all DataFrames
    aggregated_df = pd.concat(all_dfs, ignore_index=True)
    
    logger.info(f"Aggregated {len(aggregated_df)} volume bucket records")
    logger.info(f"Securities: {', '.join(sorted(aggregated_df[col.common.ticker].unique()))}")
    logger.info(f"Dates: {', '.join(sorted(aggregated_df[col.common.date].unique()))}")
    
    return aggregated_df


def cross_security_volume_tests(df: pd.DataFrame, stats_engine=None) -> pd.DataFrame:
    """Perform statistical tests comparing volume buckets across securities."""
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    logger.info("Performing cross-security volume bucket tests...")
    
    results = []
    
    # Get unique volume buckets
    buckets = sorted(df['volume_bucket_label'].unique())
    
    if not stats_engine.is_enabled():
        logger.info("Cross-security volume tests skipped (stats disabled)")
        return pd.DataFrame(results)
    
    for bucket in buckets:
        bucket_data = df[df['volume_bucket_label'] == bucket]
        
        # Test 1: Compare execution cost difference across securities
        if 'mean_exec_cost_diff_bps' in bucket_data.columns:
            # ANOVA test across securities
            security_groups = [
                group['mean_exec_cost_diff_bps'].values 
                for ticker, group in bucket_data.groupby('ticker')
                if len(group) > 0
            ]
            
            if len(security_groups) > 1:
                anova_result = stats_engine.f_oneway(*security_groups)
                
                if anova_result:
                    f_stat = anova_result.statistic
                    p_value = anova_result.pvalue
                    
                    results.append({
                        'volume_bucket': bucket,
                        'metric': 'exec_cost_diff_bps',
                        'test_type': 'ANOVA',
                        'n_securities': len(security_groups),
                        'total_observations': sum(len(g) for g in security_groups),
                        'f_statistic': f_stat,
                        'p_value': p_value,
                        'significant': p_value < 0.05,
                        'overall_mean': bucket_data['mean_exec_cost_diff_bps'].mean(),
                        'overall_std': bucket_data['mean_exec_cost_diff_bps'].std()
                    })
                else:
                    logger.warning(f"ANOVA not available for {bucket} execution cost (requires scipy)")
        
        # Test 2: Compare execution time difference across securities
        if 'mean_exec_time_diff_sec' in bucket_data.columns:
            # ANOVA test across securities
            security_groups = [
                group['mean_exec_time_diff_sec'].values 
                for ticker, group in bucket_data.groupby('ticker')
                if len(group) > 0
            ]
            
            if len(security_groups) > 1:
                anova_result = stats_engine.f_oneway(*security_groups)
                
                if anova_result:
                    f_stat = anova_result.statistic
                    p_value = anova_result.pvalue
                    
                    results.append({
                        'volume_bucket': bucket,
                        'metric': 'exec_time_diff_sec',
                        'test_type': 'ANOVA',
                        'n_securities': len(security_groups),
                        'total_observations': sum(len(g) for g in security_groups),
                        'f_statistic': f_stat,
                        'p_value': p_value,
                        'significant': p_value < 0.05,
                        'overall_mean': bucket_data['mean_exec_time_diff_sec'].mean(),
                        'overall_std': bucket_data['mean_exec_time_diff_sec'].std()
                    })
                else:
                    logger.warning(f"ANOVA not available for {bucket} execution time (requires scipy)")
    
    results_df = pd.DataFrame(results)
    logger.info(f"Completed {len(results_df)} cross-security volume tests")
    
    return results_df


def per_security_volume_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Create per-security summary statistics across all volume buckets."""
    logger.info("Creating per-security volume summaries...")
    
    summaries = []
    
    for ticker in sorted(df[col.common.ticker].unique()):
        ticker_data = df[df[col.common.ticker] == ticker]
        
        summary = {
            'ticker': ticker,
            'orderbookid': ticker_data[col.common.orderbookid].iloc[0],
            'n_buckets': len(ticker_data),
            'total_orders': ticker_data[col.volume.n_orders].sum(),
            'min_order_size': ticker_data['min_quantity'].min(),
            'max_order_size': ticker_data['max_quantity'].max(),
            'mean_exec_cost_diff_bps': ticker_data['mean_exec_cost_diff_bps'].mean(),
            'weighted_exec_cost_diff_bps': np.average(
                ticker_data['mean_exec_cost_diff_bps'],
                weights=ticker_data[col.volume.n_orders]
            ),
            'mean_exec_time_diff_sec': ticker_data['mean_exec_time_diff_sec'].mean(),
            'weighted_exec_time_diff_sec': np.average(
                ticker_data['mean_exec_time_diff_sec'],
                weights=ticker_data[col.volume.n_orders]
            ),
            'overall_dark_pool_better_pct': np.average(
                ticker_data['dark_pool_better_pct'],
                weights=ticker_data[col.volume.n_orders]
            )
        }
        
        summaries.append(summary)
    
    summary_df = pd.DataFrame(summaries)
    logger.info(f"Created summaries for {len(summary_df)} securities")
    
    return summary_df


def generate_volume_report(
    aggregated_df: pd.DataFrame,
    cross_security_tests: pd.DataFrame,
    per_security_summary: pd.DataFrame,
    output_file: str
) -> None:
    """Generate human-readable text report of volume analysis findings."""
    logger.info(f"Generating volume analysis report: {output_file}")
    
    with open(output_file, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("AGGREGATED VOLUME ANALYSIS REPORT\n")
        f.write("=" * 80 + "\n\n")
        
        # Overview
        f.write("OVERVIEW\n")
        f.write("-" * 80 + "\n")
        f.write(f"Total Securities: {aggregated_df[col.common.ticker].nunique()}\n")
        f.write(f"Securities: {', '.join(sorted(aggregated_df[col.common.ticker].unique()))}\n")
        f.write(f"Total Volume Buckets: {len(aggregated_df)}\n")
        f.write(f"Total Orders Analyzed: {aggregated_df[col.volume.n_orders].sum():,}\n")
        f.write(f"Date Range: {aggregated_df[col.common.date].min()} to {aggregated_df[col.common.date].max()}\n")
        f.write("\n")
        
        # Per-Security Summary
        f.write("PER-SECURITY SUMMARY\n")
        f.write("-" * 80 + "\n")
        f.write(f"{'Ticker':<8} {'Orders':>8} {'Size Range':>20} {'Exec Cost (bps)':>16} {'Exec Time (s)':>14} {'% Better':>10}\n")
        f.write("-" * 80 + "\n")
        
        for _, row in per_security_summary.iterrows():
            size_range = f"{row['min_order_size']:.0f}-{row['max_order_size']:.0f}"
            f.write(f"{row[col.common.ticker]:<8} {row['total_orders']:>8,} {size_range:>20} "
                   f"{row['weighted_exec_cost_diff_bps']:>16.2f} "
                   f"{row['weighted_exec_time_diff_sec']:>14.2f} "
                   f"{row['overall_dark_pool_better_pct']:>10.1f}%\n")
        
        f.write("\n")
        
        # Volume Bucket Comparison
        f.write("VOLUME BUCKET COMPARISON (ALL SECURITIES)\n")
        f.write("-" * 80 + "\n")
        
        for bucket in sorted(aggregated_df['volume_bucket_label'].unique()):
            bucket_data = aggregated_df[aggregated_df['volume_bucket_label'] == bucket]
            
            f.write(f"\n{bucket}:\n")
            f.write(f"  Total Orders: {bucket_data[col.volume.n_orders].sum():,}\n")
            f.write(f"  Size Range: {bucket_data['min_quantity'].min():.0f} - {bucket_data['max_quantity'].max():.0f}\n")
            f.write(f"  Mean Exec Cost Diff: {bucket_data['mean_exec_cost_diff_bps'].mean():.2f} bps\n")
            f.write(f"  Mean Exec Time Diff: {bucket_data['mean_exec_time_diff_sec'].mean():.2f} sec\n")
            f.write(f"  Dark Pool Better: {bucket_data['dark_pool_better_pct'].mean():.1f}%\n")
            
            # Show per-security breakdown
            f.write(f"  By Security:\n")
            for ticker in sorted(bucket_data[col.common.ticker].unique()):
                ticker_bucket = bucket_data[bucket_data[col.common.ticker] == ticker]
                cost_diff = ticker_bucket['mean_exec_cost_diff_bps'].iloc[0]
                time_diff = ticker_bucket['mean_exec_time_diff_sec'].iloc[0]
                pct_better = ticker_bucket['dark_pool_better_pct'].iloc[0]
                n_orders = ticker_bucket[col.volume.n_orders].iloc[0]
                f.write(f"    {ticker}: {cost_diff:>7.2f} bps, {time_diff:>6.2f}s, {pct_better:>5.1f}% better ({n_orders:,} orders)\n")
        
        f.write("\n")
        
        # Cross-Security Statistical Tests
        f.write("CROSS-SECURITY STATISTICAL TESTS\n")
        f.write("-" * 80 + "\n")
        
        if cross_security_tests.empty:
            f.write("Statistical tests disabled or no tests performed\n")
        else:
            f.write("Testing whether volume buckets show significantly different performance across securities\n\n")
            
            for metric in ['exec_cost_diff_bps', 'exec_time_diff_sec']:
                metric_tests = cross_security_tests[cross_security_tests['metric'] == metric]
                
                if len(metric_tests) == 0:
                    continue
                
                metric_name = "Execution Cost Difference" if metric == 'exec_cost_diff_bps' else "Execution Time Difference"
                f.write(f"\n{metric_name}:\n")
                
                for _, test in metric_tests.iterrows():
                    sig_marker = "***" if test[col.stats.p_value] < 0.001 else "**" if test[col.stats.p_value] < 0.01 else "*" if test[col.stats.p_value] < 0.05 else "ns"
                    f.write(f"  {test[col.volume.volume_bucket]}: F={test['f_statistic']:.2f}, p={test[col.stats.p_value]:.4e} {sig_marker}\n")
                    f.write(f"    Overall Mean: {test['overall_mean']:.2f}, Std: {test['overall_std']:.2f}\n")
                    f.write(f"    Securities: {test['n_securities']}, Total Observations: {test['total_observations']}\n")
                    
                    if test['significant']:
                        f.write(f"    → SIGNIFICANT difference across securities\n")
                    else:
                        f.write(f"    → No significant difference across securities\n")
        
        f.write("\n")
        
        # Key Findings
        f.write("KEY FINDINGS\n")
        f.write("-" * 80 + "\n")
        
        # Find the best and worst performing security
        best_security = per_security_summary.loc[per_security_summary['weighted_exec_cost_diff_bps'].idxmax()]
        worst_security = per_security_summary.loc[per_security_summary['weighted_exec_cost_diff_bps'].idxmin()]
        
        f.write(f"1. Best Dark Pool Performance: {best_security[col.common.ticker]} "
               f"({best_security['weighted_exec_cost_diff_bps']:+.2f} bps)\n")
        f.write(f"2. Worst Dark Pool Performance: {worst_security[col.common.ticker]} "
               f"({worst_security['weighted_exec_cost_diff_bps']:+.2f} bps)\n")
        
        # Execution time analysis
        overall_time_diff = per_security_summary['weighted_exec_time_diff_sec'].mean()
        f.write(f"3. Overall Execution Time: {overall_time_diff:+.2f} seconds (positive = dark pool slower)\n")
        
        # Find if there's a volume pattern
        volume_pattern = []
        for bucket in ['Q1', 'Q2', 'Q3', 'Q4']:
            bucket_data = aggregated_df[aggregated_df['volume_bucket_label'] == bucket]
            if len(bucket_data) > 0:
                avg_cost = bucket_data['mean_exec_cost_diff_bps'].mean()
                volume_pattern.append((bucket, avg_cost))
        
        if len(volume_pattern) == 4:
            if volume_pattern[0][1] > volume_pattern[3][1]:
                f.write(f"4. Volume Pattern: Small orders perform better than large orders\n")
                f.write(f"   Q1: {volume_pattern[0][1]:+.2f} bps vs Q4: {volume_pattern[3][1]:+.2f} bps\n")
            else:
                f.write(f"4. Volume Pattern: Large orders perform better than small orders\n")
                f.write(f"   Q4: {volume_pattern[3][1]:+.2f} bps vs Q1: {volume_pattern[0][1]:+.2f} bps\n")
        
        f.write("\n")
        f.write("=" * 80 + "\n")
        f.write("END OF REPORT\n")
        f.write("=" * 80 + "\n")
    
    logger.info(f"Report written to {output_file}")


def main(stats_engine=None):
    """Main execution function for aggregating volume analysis."""
    # If stats_engine not provided, parse command-line arguments
    if stats_engine is None:
        import argparse
        from config import ENABLE_STATISTICAL_TESTS
        
        # Parse command-line arguments
        parser = argparse.ArgumentParser(description='Aggregate Volume Analysis Results')
        parser.add_argument('--enable-stats', action='store_true', 
                            help='Enable statistical tests')
        parser.add_argument('--disable-stats', action='store_true',
                            help='Disable statistical tests')
        args = parser.parse_args()
        
        # Determine whether to enable statistics
        if args.enable_stats:
            enable_stats = True
        elif args.disable_stats:
            enable_stats = False
        else:
            enable_stats = ENABLE_STATISTICAL_TESTS
        
        # Create statistics engine
        stats_engine = StatisticsEngine(enable_stats=enable_stats)
    
    logger.info("Starting volume analysis aggregation...")
    logger.info(f"Statistics: {stats_engine.get_tier_name()}")
    
    # Step 1: Aggregate volume summaries
    aggregated_df = aggregate_volume_summaries()
    
    if aggregated_df.empty:
        logger.error("No volume analysis data to aggregate. Exiting.")
        return
    
    # Step 2: Create output directory
    output_dir = Path('data/aggregated')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Step 3: Save aggregated volume summaries
    output_file = output_dir / 'aggregated_volume_summary.csv'
    aggregated_df.to_csv(output_file, index=False)
    logger.info(f"Saved aggregated volume summary to {output_file}")
    
    # Step 4: Perform cross-security tests
    cross_security_tests = cross_security_volume_tests(aggregated_df, stats_engine)
    
    if not cross_security_tests.empty:
        test_output = output_dir / 'aggregated_volume_cross_security_tests.csv'
        cross_security_tests.to_csv(test_output, index=False)
        logger.info(f"Saved cross-security tests to {test_output}")
    
    # Step 5: Create per-security summary
    per_security_summary = per_security_volume_summary(aggregated_df)
    
    if not per_security_summary.empty:
        summary_output = output_dir / 'aggregated_volume_per_security_summary.csv'
        per_security_summary.to_csv(summary_output, index=False)
        logger.info(f"Saved per-security summary to {summary_output}")
    
    # Step 6: Generate text report
    report_file = output_dir / 'aggregated_volume_report.txt'
    generate_volume_report(aggregated_df, cross_security_tests, per_security_summary, str(report_file))
    
    logger.info("=" * 80)
    logger.info("VOLUME ANALYSIS AGGREGATION COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Total securities: {aggregated_df[col.common.ticker].nunique()}")
    logger.info(f"Total volume buckets: {len(aggregated_df)}")
    logger.info(f"Total orders: {aggregated_df[col.volume.n_orders].sum():,}")
    logger.info(f"Output directory: {output_dir}")


if __name__ == '__main__':
    main()
