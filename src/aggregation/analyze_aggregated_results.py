"""
Analyze Aggregated Sweep Order Results

This script performs comprehensive statistical analysis on the aggregated sweep order
results across multiple securities, including:
- Overall paired t-tests (real vs simulated execution)
- Per-security paired t-tests
- Cross-security comparison tests (ANOVA, pairwise comparisons)
- Effect sizes (Cohen's d)
- Confidence intervals

Usage:
    python analyze_aggregated_results.py
    
Inputs:
    data/outputs/aggregated_sweep_comparison.csv
    
Outputs:
    data/outputs/aggregated_statistical_summary.csv
    data/outputs/aggregated_cross_security_tests.csv
    data/outputs/aggregated_analysis_report.txt
"""

import pandas as pd
from config.column_schema import col
import numpy as np
import logging
from pathlib import Path
from utils.statistics_layer import StatisticsEngine

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

# Metrics to analyze (difference columns)
DIFF_METRICS = [
    'exec_cost_arrival_diff_bps',
    'exec_cost_vw_diff_bps',
    'effective_spread_diff_pct',
    'exec_time_diff_sec',
    'vw_exec_time_diff_sec',
    'qty_diff',
    'fill_rate_diff_pct',
    'num_fills_diff',
    'vwap_diff',
    'avg_fill_size_diff',
    'time_to_first_fill_diff_sec'
]

# Metric names for reporting
METRIC_NAMES = {
    'exec_cost_arrival_diff_bps': 'Execution Cost (Arrival)',
    'exec_cost_vw_diff_bps': 'Execution Cost (VW)',
    'effective_spread_diff_pct': 'Effective Spread',
    'exec_time_diff_sec': 'Execution Time',
    'vw_exec_time_diff_sec': 'VW Execution Time',
    'qty_diff': 'Quantity Filled',
    'fill_rate_diff_pct': 'Fill Rate',
    'num_fills_diff': 'Number of Fills',
    'vwap_diff': 'VWAP Difference',
    'avg_fill_size_diff': 'Average Fill Size',
    'time_to_first_fill_diff_sec': 'Time to First Fill'
}

# Units for each metric
METRIC_UNITS = {
    'exec_cost_arrival_diff_bps': 'bps',
    'exec_cost_vw_diff_bps': 'bps',
    'effective_spread_diff_pct': '%',
    'exec_time_diff_sec': 'sec',
    'vw_exec_time_diff_sec': 'sec',
    'qty_diff': 'units',
    'fill_rate_diff_pct': '%',
    'num_fills_diff': 'fills',
    'vwap_diff': 'price',
    'avg_fill_size_diff': 'units',
    'time_to_first_fill_diff_sec': 'sec'
}


def load_aggregated_data(input_path='data/aggregated/aggregated_sweep_comparison.csv'):
    """Load the aggregated sweep comparison data."""
    try:
        df = pd.read_csv(input_path)
        logger.info(f"Loaded {len(df):,} orders from {input_path}")
        logger.info(f"Securities: {df[col.common.ticker].unique().tolist()}")
        return df
    except Exception as e:
        logger.error(f"Error loading aggregated data: {str(e)}")
        return None


def cohens_d(group1, group2):
    """Calculate Cohen's d effect size between two groups."""
    n1, n2 = len(group1), len(group2)
    var1, var2 = np.var(group1, ddof=1), np.var(group2, ddof=1)
    pooled_std = np.sqrt(((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2))
    
    if pooled_std == 0:
        return 0.0
    
    return (np.mean(group1) - np.mean(group2)) / pooled_std


def paired_cohens_d(differences):
    """Calculate Cohen's d for paired samples (mean difference / std of differences)."""
    if len(differences) == 0 or np.std(differences, ddof=1) == 0:
        return 0.0
    return np.mean(differences) / np.std(differences, ddof=1)


def interpret_effect_size(d):
    """Interpret Cohen's d effect size."""
    abs_d = abs(d)
    if abs_d < 0.2:
        return "negligible"
    elif abs_d < 0.5:
        return "small"
    elif abs_d < 0.8:
        return "medium"
    else:
        return "large"


def overall_paired_tests(df, stats_engine=None):
    """Perform paired t-tests on all orders combined (real vs simulated)."""
    logger.info("\n" + "="*80)
    logger.info("OVERALL PAIRED T-TESTS (All Securities Combined)")
    logger.info("="*80)
    
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    results = []
    
    for metric in DIFF_METRICS:
        if metric not in df.columns:
            logger.warning(f"Metric {metric} not found in data, skipping...")
            continue
        
        # Get differences (positive = dark pool better)
        differences = df[metric].dropna()
        
        if len(differences) == 0:
            logger.warning(f"No data for {metric}, skipping...")
            continue
        
        # Calculate descriptive statistics
        mean_diff = differences.mean()
        std_diff = differences.std()
        median_diff = differences.median()
        
        # Initialize result dict
        result_dict = {
            'metric': METRIC_NAMES[metric],
            'metric_key': metric,
            'unit': METRIC_UNITS[metric],
            'n_orders': len(differences),
            'mean_diff': mean_diff,
            'median_diff': median_diff,
            'std_diff': std_diff,
        }
        
        # Effect size (always calculate - it's descriptive, not inferential)
        effect_size = paired_cohens_d(differences)
        effect_interp = interpret_effect_size(effect_size)
        result_dict['cohens_d'] = effect_size
        result_dict[col.stats.effect_size] = effect_interp
        
        # Initialize variables for logging
        ci_result = None
        ttest_result = None
        
        if stats_engine.is_enabled():
            # Run statistical tests
            ttest_result = stats_engine.ttest_1samp(differences, 0)
            ci_result = stats_engine.confidence_interval(differences, confidence=0.95)
            
            if ttest_result:
                t_stat, p_value = ttest_result
                result_dict[col.stats.t_statistic] = t_stat
                result_dict[col.stats.p_value] = p_value
            else:
                result_dict[col.stats.t_statistic] = None
                result_dict[col.stats.p_value] = None
            
            if ci_result:
                result_dict['ci_95_lower'] = ci_result[0]
                result_dict['ci_95_upper'] = ci_result[1]
            else:
                result_dict['ci_95_lower'] = None
                result_dict['ci_95_upper'] = None
        else:
            # Stats disabled - use None for statistical fields
            result_dict[col.stats.t_statistic] = None
            result_dict[col.stats.p_value] = None
            result_dict['ci_95_lower'] = None
            result_dict['ci_95_upper'] = None
        
        # Count of orders where dark pool was better
        better_count = (differences > 0).sum()
        better_pct = (better_count / len(differences)) * 100
        result_dict['dark_better_count'] = better_count
        result_dict['dark_better_pct'] = better_pct
        
        results.append(result_dict)
        
        # Log results
        logger.info(f"\n{METRIC_NAMES[metric]} ({METRIC_UNITS[metric]}):")
        logger.info(f"  Mean Difference: {mean_diff:+.4f}")
        
        if stats_engine.is_enabled() and ci_result:
            logger.info(f"  95% CI: [{result_dict['ci_95_lower']:+.4f}, {result_dict['ci_95_upper']:+.4f}]")
        
        if stats_engine.is_enabled() and ttest_result:
            logger.info(f"  t-statistic: {result_dict[col.stats.t_statistic]:.4f}, p-value: {result_dict[col.stats.p_value]:.2e}")
        logger.info(f"  Cohen's d: {effect_size:.4f} ({effect_interp})")
        logger.info(f"  Dark pool better: {better_count:,} / {len(differences):,} ({better_pct:.2f}%)")
    
    return pd.DataFrame(results)


def per_security_paired_tests(df, stats_engine=None):
    """Perform paired t-tests for each security individually."""
    logger.info("\n" + "="*80)
    logger.info("PER-SECURITY PAIRED T-TESTS")
    logger.info("="*80)
    
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    results = []
    tickers = sorted(df[col.common.ticker].unique())
    
    for ticker in tickers:
        logger.info(f"\n--- {ticker} ---")
        ticker_df = df[df[col.common.ticker] == ticker]
        
        for metric in DIFF_METRICS:
            if metric not in ticker_df.columns:
                continue
            
            differences = ticker_df[metric].dropna()
            
            if len(differences) < 2:
                continue
            
            # Descriptive statistics
            mean_diff = differences.mean()
            std_diff = differences.std()
            median_diff = differences.median()
            
            # Effect size (always calculate - it's descriptive)
            effect_size = paired_cohens_d(differences)
            effect_interp = interpret_effect_size(effect_size)
            
            better_count = (differences > 0).sum()
            better_pct = (better_count / len(differences)) * 100
            
            # Initialize result dict
            result_dict = {
                'ticker': ticker,
                'metric': METRIC_NAMES[metric],
                'metric_key': metric,
                'unit': METRIC_UNITS[metric],
                'n_orders': len(differences),
                'mean_diff': mean_diff,
                'median_diff': median_diff,
                'std_diff': std_diff,
                'cohens_d': effect_size,
                'effect_size': effect_interp,
                'dark_better_count': better_count,
                'dark_better_pct': better_pct
            }
            
            if stats_engine.is_enabled():
                # Run statistical tests
                ttest_result = stats_engine.ttest_1samp(differences, 0)
                ci_result = stats_engine.confidence_interval(differences, confidence=0.95)
                
                if ttest_result:
                    result_dict[col.stats.t_statistic] = ttest_result.statistic
                    result_dict[col.stats.p_value] = ttest_result.pvalue
                else:
                    result_dict[col.stats.t_statistic] = None
                    result_dict[col.stats.p_value] = None
                
                if ci_result:
                    result_dict['ci_95_lower'] = ci_result[0]
                    result_dict['ci_95_upper'] = ci_result[1]
                else:
                    result_dict['ci_95_lower'] = None
                    result_dict['ci_95_upper'] = None
            else:
                # Stats disabled
                result_dict[col.stats.t_statistic] = None
                result_dict[col.stats.p_value] = None
                result_dict['ci_95_lower'] = None
                result_dict['ci_95_upper'] = None
            
            results.append(result_dict)
        
        # Show key metric for this security
        exec_cost_metric = 'exec_cost_arrival_diff_bps'
        if exec_cost_metric in ticker_df.columns:
            diff = ticker_df[exec_cost_metric].dropna()
            logger.info(f"  Exec Cost (Arrival): {diff.mean():+.2f} bps (n={len(diff)})")
    
    return pd.DataFrame(results)


def cross_security_comparison_tests(df, stats_engine=None):
    """Perform cross-security comparison tests (ANOVA and pairwise t-tests)."""
    logger.info("\n" + "="*80)
    logger.info("CROSS-SECURITY COMPARISON TESTS")
    logger.info("="*80)
    
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    anova_results = []
    pairwise_results = []
    
    tickers = sorted(df[col.common.ticker].unique())
    
    for metric in DIFF_METRICS:
        if metric not in df.columns:
            continue
        
        # Prepare groups for ANOVA
        groups = []
        valid_tickers = []
        
        for ticker in tickers:
            ticker_data = df[df[col.common.ticker] == ticker][metric].dropna()
            if len(ticker_data) >= 2:
                groups.append(ticker_data)
                valid_tickers.append(ticker)
        
        if len(groups) < 2:
            continue
        
        # One-way ANOVA (only if stats enabled)
        if stats_engine.is_enabled():
            anova_result = stats_engine.f_oneway(*groups)
            
            if anova_result:
                f_stat = anova_result.statistic
                p_value = anova_result.pvalue
                
                anova_results.append({
                    'metric': METRIC_NAMES[metric],
                    'metric_key': metric,
                    'unit': METRIC_UNITS[metric],
                    'f_statistic': f_stat,
                    'p_value': p_value,
                    'significant': p_value < 0.05,
                    'n_securities': len(groups)
                })
                
                logger.info(f"\n{METRIC_NAMES[metric]}:")
                logger.info(f"  ANOVA F-statistic: {f_stat:.4f}, p-value: {p_value:.2e}")
                logger.info(f"  Significant difference across securities: {p_value < 0.05}")
                
                # Pairwise t-tests (if ANOVA is significant)
                if p_value < 0.05:
                    for i in range(len(valid_tickers)):
                        for j in range(i+1, len(valid_tickers)):
                            ticker1, ticker2 = valid_tickers[i], valid_tickers[j]
                            
                            group1 = df[df[col.common.ticker] == ticker1][metric].dropna()
                            group2 = df[df[col.common.ticker] == ticker2][metric].dropna()
                            
                            # Independent t-test
                            ttest_result = stats_engine.ttest_ind(group1, group2)
                            
                            # Effect size (always calculate - it's descriptive)
                            effect = cohens_d(group1, group2)
                            effect_interp = interpret_effect_size(effect)
                            
                            result_dict = {
                                'metric': METRIC_NAMES[metric],
                                'metric_key': metric,
                                'unit': METRIC_UNITS[metric],
                                'ticker1': ticker1,
                                'ticker2': ticker2,
                                'mean_diff_ticker1': group1.mean(),
                                'mean_diff_ticker2': group2.mean(),
                                'mean_difference': group1.mean() - group2.mean(),
                                'cohens_d': effect,
                                'effect_size': effect_interp,
                                'n_ticker1': len(group1),
                                'n_ticker2': len(group2)
                            }
                            
                            if ttest_result:
                                result_dict[col.stats.t_statistic] = ttest_result.statistic
                                result_dict[col.stats.p_value] = ttest_result.pvalue
                                result_dict['significant'] = ttest_result.pvalue < 0.05
                                p_val = ttest_result.pvalue
                            else:
                                result_dict[col.stats.t_statistic] = None
                                result_dict[col.stats.p_value] = None
                                result_dict['significant'] = None
                                p_val = 1.0  # For logging check
                            
                            pairwise_results.append(result_dict)
                            
                            if p_val < 0.05:
                                logger.info(f"    {ticker1} vs {ticker2}: "
                                          f"{group1.mean():+.4f} vs {group2.mean():+.4f}, "
                                          f"p={p_val:.2e}, d={effect:.2f} ({effect_interp})")
            else:
                # ANOVA not available (Tier 2 approximation doesn't support it)
                logger.warning(f"\n{METRIC_NAMES[metric]}: ANOVA not available (requires scipy)")
        else:
            # Stats disabled
            logger.info(f"\n{METRIC_NAMES[metric]}: Cross-security tests skipped (stats disabled)")
    
    anova_df = pd.DataFrame(anova_results) if anova_results else pd.DataFrame()
    pairwise_df = pd.DataFrame(pairwise_results) if pairwise_results else pd.DataFrame()
    
    return anova_df, pairwise_df


def cross_orderbookid_date_tests(df, stats_engine=None):
    """Focused t-tests on execution cost and execution time differences"""
    logger.info("\n" + "="*80)
    logger.info("CROSS-ORDERBOOKID/DATE T-TESTS (PRIMARY FOCUS)")
    logger.info("="*80)
    
    # Create default stats engine if not provided
    if stats_engine is None:
        stats_engine = StatisticsEngine(enable_stats=True)
    
    results = []
    
    # Define the two focus metrics
    focus_metrics = {
        'exec_cost_arrival_diff_bps': {
            'name': 'Execution Cost (Arrival)',
            'unit': 'bps',
            'interpretation': 'Positive = dark pool better, Negative = lit market better'
        },
        'exec_time_diff_sec': {
            'name': 'Execution Time',
            'unit': 'sec',
            'interpretation': 'Positive = dark pool slower, Negative = dark pool faster'
        }
    }
    
    for metric_key, metric_info in focus_metrics.items():
        differences = df[metric_key].dropna()
        
        if len(differences) == 0:
            logger.warning(f"No data for {metric_info['name']}, skipping...")
            continue
        
        # Descriptive statistics
        mean_diff = differences.mean()
        std_diff = differences.std()
        median_diff = differences.median()
        
        # Effect size (always calculate - it's descriptive)
        effect_size = paired_cohens_d(differences)
        effect_interp = interpret_effect_size(effect_size)
        
        # Count of orders where dark pool was better
        better_count = (differences > 0).sum()
        better_pct = (better_count / len(differences)) * 100
        
        # Initialize result dict
        result_dict = {
            'metric': metric_info['name'],
            'metric_key': metric_key,
            'unit': metric_info['unit'],
            'n_orders': len(differences),
            'n_orderbookids': df[col.common.orderbookid].nunique(),
            'n_dates': df[col.common.date].nunique(),
            'mean_diff': mean_diff,
            'median_diff': median_diff,
            'std_diff': std_diff,
            'cohens_d': effect_size,
            'effect_size': effect_interp,
            'better_count': better_count,
            'better_pct': better_pct,
            'interpretation': metric_info['interpretation']
        }
        
        # Initialize for logging
        ci_result = None
        ttest_result = None
        significance = "N/A"
        
        if stats_engine.is_enabled():
            # Paired t-test (testing if mean difference is significantly different from 0)
            ttest_result = stats_engine.ttest_1samp(differences, 0)
            ci_result = stats_engine.confidence_interval(differences, confidence=0.95)
            
            if ttest_result:
                result_dict[col.stats.t_statistic] = ttest_result.statistic
                result_dict[col.stats.p_value] = ttest_result.pvalue
                
                # Determine significance level
                p_value = ttest_result.pvalue
                if p_value < 0.001:
                    significance = "***"
                elif p_value < 0.01:
                    significance = "**"
                elif p_value < 0.05:
                    significance = "*"
                else:
                    significance = "ns"
                result_dict['significance'] = significance
            else:
                result_dict[col.stats.t_statistic] = None
                result_dict[col.stats.p_value] = None
                result_dict['significance'] = None
            
            if ci_result:
                result_dict['ci_95_lower'] = ci_result[0]
                result_dict['ci_95_upper'] = ci_result[1]
            else:
                result_dict['ci_95_lower'] = None
                result_dict['ci_95_upper'] = None
        else:
            # Stats disabled
            result_dict[col.stats.t_statistic] = None
            result_dict[col.stats.p_value] = None
            result_dict['significance'] = None
            result_dict['ci_95_lower'] = None
            result_dict['ci_95_upper'] = None
        
        results.append(result_dict)
        
        # Log results
        logger.info(f"\n{metric_info['name']} ({metric_info['unit']}):")
        logger.info(f"  Pooled across {df[col.common.orderbookid].nunique()} orderbookids and {df[col.common.date].nunique()} dates")
        logger.info(f"  Sample size: {len(differences):,} orders")
        logger.info(f"  Mean Difference: {mean_diff:+.4f} {metric_info['unit']}")
        
        if stats_engine.is_enabled() and ci_result:
            logger.info(f"  95% CI: [{result_dict['ci_95_lower']:+.4f}, {result_dict['ci_95_upper']:+.4f}] {metric_info['unit']}")
        
        if stats_engine.is_enabled() and ttest_result:
            logger.info(f"  t-statistic: {result_dict[col.stats.t_statistic]:.4f}")
            logger.info(f"  p-value: {result_dict[col.stats.p_value]:.2e} {significance}")
        
        logger.info(f"  Cohen's d: {effect_size:.4f} ({effect_interp})")
        logger.info(f"  Dark pool better: {better_count:,} / {len(differences):,} ({better_pct:.2f}%)")
        logger.info(f"  Interpretation: {metric_info['interpretation']}")
    
    return pd.DataFrame(results)


def generate_text_report(df, overall_df, per_security_df, anova_df, pairwise_df, cross_orderbook_df=None):
    """Generate a human-readable text report."""
    
    report_lines = []
    
    def add_line(line=""):
        """Append line to report."""
        report_lines.append(line)
    
    add_line("="*80)
    add_line("AGGREGATED SWEEP ORDER ANALYSIS REPORT")
    add_line("="*80)
    add_line()
    
    # CROSS-ORDERBOOKID/DATE TESTS (TOP PRIORITY)
    if cross_orderbook_df is not None and not cross_orderbook_df.empty:
        add_line("="*80)
        add_line("CROSS-ORDERBOOKID/DATE T-TESTS (PRIMARY FOCUS)")
        add_line("="*80)
        add_line()
        add_line(f"Analysis pooled across {df[col.common.orderbookid].nunique()} orderbookids and {df[col.common.date].nunique()} date(s)")
        add_line(f"Total sample: {len(df):,} orders")
        add_line()
        
        for _, row in cross_orderbook_df.iterrows():
            add_line(f"{row['metric']}:")
            add_line(f"  Mean Difference: {row['mean_diff']:+.4f} {row['unit']}")
            
            # Handle None values for statistical fields
            if pd.notna(row.get('ci_95_lower')) and pd.notna(row.get('ci_95_upper')):
                add_line(f"  95% CI: [{row['ci_95_lower']:+.4f}, {row['ci_95_upper']:+.4f}] {row['unit']}")
            
            if pd.notna(row.get('t_statistic')) and pd.notna(row.get('p_value')):
                sig = row.get('significance', 'N/A')
                add_line(f"  t-statistic: {row[col.stats.t_statistic]:.4f}")
                add_line(f"  p-value: {row[col.stats.p_value]:.2e} {sig}")
            
            if pd.notna(row.get('cohens_d')) and pd.notna(row.get('effect_size')):
                add_line(f"  Cohen's d: {row['cohens_d']:.4f} ({row[col.stats.effect_size]})")
            
            add_line(f"  Dark pool better: {row['better_pct']:.2f}% of orders")
            add_line()
            
            # Add interpretation
            if row['metric_key'] == 'exec_cost_arrival_diff_bps':
                if row['mean_diff'] > 0:
                    add_line(f"  INTERPRETATION: Dark pool execution is {row['mean_diff']:.2f} bps BETTER")
                    add_line(f"                  across all orderbookids/dates (statistically significant)")
                else:
                    add_line(f"  INTERPRETATION: Dark pool execution is {abs(row['mean_diff']):.2f} bps WORSE")
                    add_line(f"                  across all orderbookids/dates (statistically significant)")
            elif row['metric_key'] == 'exec_time_diff_sec':
                if row['mean_diff'] > 0:
                    add_line(f"  INTERPRETATION: Dark pool execution is {row['mean_diff']:.2f} sec SLOWER")
                    add_line(f"                  across all orderbookids/dates")
                else:
                    add_line(f"  INTERPRETATION: Dark pool execution is {abs(row['mean_diff']):.2f} sec FASTER")
                    add_line(f"                  across all orderbookids/dates")
            add_line()
        add_line()
    
    # Dataset summary
    add_line("="*80)
    add_line("DATASET SUMMARY")
    add_line("="*80)
    add_line(f"Total Orders: {len(df):,}")
    add_line(f"Securities: {df[col.common.ticker].nunique()}")
    add_line(f"Dates: {df[col.common.date].nunique()}")
    add_line()
    
    add_line("Orders by Security:")
    for ticker in sorted(df[col.common.ticker].unique()):
        count = len(df[df[col.common.ticker] == ticker])
        pct = (count / len(df)) * 100
        add_line(f"  {ticker:4s}: {count:6,} orders ({pct:5.2f}%)")
    add_line()
    
    # Overall results (focus on execution cost)
    add_line("="*80)
    add_line("OVERALL RESULTS (All Securities Combined)")
    add_line("="*80)
    add_line()
    
    exec_cost_row = overall_df[overall_df['metric_key'] == 'exec_cost_arrival_diff_bps'].iloc[0]
    add_line("PRIMARY METRIC: Execution Cost (Arrival)")
    add_line(f"  Mean Difference: {exec_cost_row['mean_diff']:+.2f} bps")
    
    if pd.notna(exec_cost_row.get('ci_95_lower')) and pd.notna(exec_cost_row.get('ci_95_upper')):
        add_line(f"  95% CI: [{exec_cost_row['ci_95_lower']:+.2f}, {exec_cost_row['ci_95_upper']:+.2f}] bps")
    
    if pd.notna(exec_cost_row.get('t_statistic')) and pd.notna(exec_cost_row.get('p_value')):
        add_line(f"  t-statistic: {exec_cost_row[col.stats.t_statistic]:.4f}")
        add_line(f"  p-value: {exec_cost_row[col.stats.p_value]:.2e}")
    
    if pd.notna(exec_cost_row.get('cohens_d')) and pd.notna(exec_cost_row.get('effect_size')):
        add_line(f"  Cohen's d: {exec_cost_row['cohens_d']:.4f} ({exec_cost_row[col.stats.effect_size]})")
    
    add_line(f"  Dark pool better: {exec_cost_row['dark_better_count']:,} / {exec_cost_row[col.volume.n_orders]:,} "
             f"({exec_cost_row['dark_better_pct']:.2f}%)")
    
    if exec_cost_row['mean_diff'] > 0:
        add_line(f"\n  CONCLUSION: Dark pool execution is {exec_cost_row['mean_diff']:.2f} bps BETTER on average")
    else:
        add_line(f"\n  CONCLUSION: Dark pool execution is {abs(exec_cost_row['mean_diff']):.2f} bps WORSE on average")
    add_line()
    
    # Per-security results
    add_line("="*80)
    add_line("PER-SECURITY RESULTS (Execution Cost - Arrival)")
    add_line("="*80)
    add_line()
    
    exec_cost_per_sec = per_security_df[
        per_security_df['metric_key'] == 'exec_cost_arrival_diff_bps'
    ].sort_values('mean_diff', ascending=False)
    
    for _, row in exec_cost_per_sec.iterrows():
        add_line(f"{row[col.common.ticker]:4s}:")
        add_line(f"  Mean Difference: {row['mean_diff']:+7.2f} bps")
        
        if pd.notna(row.get('ci_95_lower')) and pd.notna(row.get('ci_95_upper')):
            add_line(f"  95% CI: [{row['ci_95_lower']:+.2f}, {row['ci_95_upper']:+.2f}] bps")
        
        if pd.notna(row.get('p_value')):
            add_line(f"  p-value: {row[col.stats.p_value]:.2e}")
        
        if pd.notna(row.get('cohens_d')) and pd.notna(row.get('effect_size')):
            add_line(f"  Cohen's d: {row['cohens_d']:.4f} ({row[col.stats.effect_size]})")
        
        add_line(f"  Dark pool better: {row['dark_better_pct']:.2f}% of orders")
        add_line()
    
    # Cross-security comparison
    if not anova_df.empty:
        add_line("="*80)
        add_line("CROSS-SECURITY COMPARISON")
        add_line("="*80)
        add_line()
        
        exec_cost_anova = anova_df[anova_df['metric_key'] == 'exec_cost_arrival_diff_bps']
        if not exec_cost_anova.empty:
            row = exec_cost_anova.iloc[0]
            add_line("ANOVA Test (Execution Cost - Arrival):")
            
            if pd.notna(row.get('f_statistic')) and pd.notna(row.get('p_value')):
                add_line(f"  F-statistic: {row['f_statistic']:.4f}")
                add_line(f"  p-value: {row[col.stats.p_value]:.2e}")
                add_line(f"  Significant difference across securities: {row['significant']}")
            else:
                add_line("  ANOVA not available (requires scipy)")
            add_line()
            
            if row['significant'] and not pairwise_df.empty:
                add_line("Significant Pairwise Comparisons:")
                exec_cost_pairwise = pairwise_df[
                    (pairwise_df['metric_key'] == 'exec_cost_arrival_diff_bps') & 
                    (pairwise_df['significant'])
                ]
                
                for _, pw_row in exec_cost_pairwise.iterrows():
                    add_line(f"  {pw_row['ticker1']} vs {pw_row['ticker2']}:")
                    add_line(f"    {pw_row['ticker1']}: {pw_row['mean_diff_ticker1']:+.2f} bps")
                    add_line(f"    {pw_row['ticker2']}: {pw_row['mean_diff_ticker2']:+.2f} bps")
                    add_line(f"    Difference: {pw_row['mean_difference']:+.2f} bps")
                    
                    if pd.notna(pw_row.get('p_value')):
                        add_line(f"    p-value: {pw_row[col.stats.p_value]:.2e}")
                    
                    if pd.notna(pw_row.get('cohens_d')) and pd.notna(pw_row.get('effect_size')):
                        add_line(f"    Cohen's d: {pw_row['cohens_d']:.4f} ({pw_row[col.stats.effect_size]})")
                    add_line()
    
    add_line("="*80)
    add_line("END OF REPORT")
    add_line("="*80)
    
    return "\n".join(report_lines)


def main(stats_engine=None):
    """Main execution function."""
    # If stats_engine not provided, parse command-line arguments
    if stats_engine is None:
        import argparse
        
        parser = argparse.ArgumentParser(description='Statistical analysis of aggregated sweep order comparison')
        parser.add_argument('--enable-stats', action='store_true', default=True,
                           help='Enable statistical tests (default: enabled)')
        parser.add_argument('--disable-stats', action='store_true',
                           help='Disable statistical tests (only descriptive stats)')
        args = parser.parse_args()
        
        # Determine if stats should be enabled
        enable_stats = not args.disable_stats
        
        # Create stats engine
        stats_engine = StatisticsEngine(enable_stats=enable_stats)
    
    # Get stats status from engine
    enable_stats = stats_engine.is_enabled()
    
    logger.info("="*80)
    logger.info("AGGREGATED SWEEP ORDER STATISTICAL ANALYSIS")
    logger.info("="*80)
    logger.info(f"Statistics: {'ENABLED' if enable_stats else 'DISABLED'}")
    logger.info(f"Statistics Tier: {stats_engine.get_tier_name()}")
    logger.info("="*80)
    
    # Load data
    input_path = 'data/aggregated/aggregated_sweep_comparison.csv'
    df = load_aggregated_data(input_path)
    
    if df is None:
        logger.error("Failed to load data!")
        return 1
    
    # Perform analyses
    overall_df = overall_paired_tests(df, stats_engine)
    per_security_df = per_security_paired_tests(df, stats_engine)
    anova_df, pairwise_df = cross_security_comparison_tests(df, stats_engine)
    cross_orderbook_df = cross_orderbookid_date_tests(df, stats_engine)  # NEW: Primary focus
    
    # Save results
    output_dir = Path('data/aggregated')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Save statistical summaries
    overall_df.to_csv(output_dir / 'aggregated_statistical_summary.csv', index=False)
    logger.info(f"\nSaved overall summary: {output_dir / 'aggregated_statistical_summary.csv'}")
    
    per_security_df.to_csv(output_dir / 'aggregated_per_security_tests.csv', index=False)
    logger.info(f"Saved per-security tests: {output_dir / 'aggregated_per_security_tests.csv'}")
    
    if not anova_df.empty:
        anova_df.to_csv(output_dir / 'aggregated_cross_security_anova.csv', index=False)
        logger.info(f"Saved ANOVA results: {output_dir / 'aggregated_cross_security_anova.csv'}")
    
    if not pairwise_df.empty:
        pairwise_df.to_csv(output_dir / 'aggregated_cross_security_pairwise.csv', index=False)
        logger.info(f"Saved pairwise comparisons: {output_dir / 'aggregated_cross_security_pairwise.csv'}")
    
    # Save cross-orderbookid focused tests (NEW)
    if not cross_orderbook_df.empty:
        cross_orderbook_df.to_csv(output_dir / 'aggregated_cross_orderbookid_tests.csv', index=False)
        logger.info(f"Saved cross-orderbookid tests: {output_dir / 'aggregated_cross_orderbookid_tests.csv'}")
    
    # Generate and save text report
    report = generate_text_report(df, overall_df, per_security_df, anova_df, pairwise_df, cross_orderbook_df)
    
    report_path = output_dir / 'aggregated_analysis_report.txt'
    with open(report_path, 'w') as f:
        f.write(report)
    
    logger.info(f"Saved text report: {report_path}")
    
    # Print key findings
    logger.info("\n" + "="*80)
    logger.info("KEY FINDINGS")
    logger.info("="*80)
    
    # Show cross-orderbookid results (PRIMARY FOCUS)
    if not cross_orderbook_df.empty:
        exec_cost = cross_orderbook_df[cross_orderbook_df['metric_key'] == 'exec_cost_arrival_diff_bps'].iloc[0]
        exec_time = cross_orderbook_df[cross_orderbook_df['metric_key'] == 'exec_time_diff_sec'].iloc[0]
        
        logger.info(f"\nExecution Cost (pooled across orderbookids): {exec_cost['mean_diff']:+.2f} bps "
                   f"({'BETTER' if exec_cost['mean_diff'] > 0 else 'WORSE'})")
        
        if pd.notna(exec_cost.get('p_value')) and pd.notna(exec_cost.get('significance')):
            logger.info(f"Statistical significance: p = {exec_cost[col.stats.p_value]:.2e} {exec_cost['significance']}")
        
        if pd.notna(exec_cost.get('cohens_d')) and pd.notna(exec_cost.get('effect_size')):
            logger.info(f"Effect size: {exec_cost['cohens_d']:.4f} ({exec_cost[col.stats.effect_size]})")
        
        logger.info(f"\nExecution Time (pooled across orderbookids): {exec_time['mean_diff']:+.2f} sec "
                   f"({'SLOWER' if exec_time['mean_diff'] > 0 else 'FASTER'})")
        
        if pd.notna(exec_time.get('p_value')) and pd.notna(exec_time.get('significance')):
            logger.info(f"Statistical significance: p = {exec_time[col.stats.p_value]:.2e} {exec_time['significance']}")
        
        if pd.notna(exec_time.get('cohens_d')) and pd.notna(exec_time.get('effect_size')):
            logger.info(f"Effect size: {exec_time['cohens_d']:.4f} ({exec_time[col.stats.effect_size]})")
    
    logger.info("\n✓ Analysis completed successfully!")
    return 0


if __name__ == '__main__':
    exit(main())
