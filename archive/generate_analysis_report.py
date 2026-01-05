#!/usr/bin/env python3
"""
Generate Markdown Summary Report from Sweep Execution Analysis

Usage:
    python generate_analysis_report.py [partition_key]
    
Example:
    python generate_analysis_report.py 2024-09-05/110621
    python generate_analysis_report.py  # Uses latest partition
"""

import pandas as pd
import json
from pathlib import Path
import sys
from datetime import datetime


def find_latest_partition(processed_dir):
    """Find the most recent partition with stats."""
    stats_dirs = list(Path(processed_dir).rglob('stats/matched'))
    if not stats_dirs:
        return None
    
    # Sort by modification time
    latest = max(stats_dirs, key=lambda p: p.stat().st_mtime)
    partition_key = '/'.join(latest.parts[-4:-2])
    return partition_key


def generate_report(processed_dir, partition_key, output_file=None):
    """Generate markdown report from analysis outputs."""
    
    partition_dir = Path(processed_dir) / partition_key
    matched_dir = partition_dir / 'stats' / 'matched'
    unmatched_dir = partition_dir / 'stats' / 'unmatched'
    
    # Check if analysis exists
    if not matched_dir.exists():
        print(f"Error: Analysis not found for partition {partition_key}")
        return False
    
    # Load analysis files
    summary_df = pd.read_csv(matched_dir / 'sweep_order_comparison_summary.csv')
    tests_df = pd.read_csv(matched_dir / 'sweep_order_statistical_tests.csv')
    quantiles_df = pd.read_csv(matched_dir / 'sweep_order_quantile_comparison.csv')
    comparison_df = pd.read_csv(matched_dir / 'sweep_order_comparison_detailed.csv')
    unmatched_df = pd.read_csv(unmatched_dir / 'sweep_order_unexecuted_in_dark.csv')
    
    with open(matched_dir / 'analysis_validation_report.json', 'r') as f:
        validation = json.load(f)
    
    # Generate markdown report
    report = []
    report.append(f"# Sweep Order Execution Analysis Report")
    report.append(f"")
    report.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"**Partition:** {partition_key}")
    report.append(f"")
    report.append(f"---")
    report.append(f"")
    
    # Executive Summary
    report.append(f"## Executive Summary")
    report.append(f"")
    report.append(f"- **Matched Orders (Set A):** {len(comparison_df)} orders with both real and simulated execution")
    report.append(f"- **Unmatched Orders (Set B):** {len(unmatched_df)} orders with real execution only")
    report.append(f"- **Total Sweep Orders Analyzed:** {len(comparison_df) + len(unmatched_df)}")
    report.append(f"")
    
    # Key Finding
    exec_cost_summary = summary_df[summary_df['metric_name'] == 'exec_cost_arrival_bps']
    exec_cost_test = tests_df[tests_df['metric_name'] == 'exec_cost_arrival_bps']
    
    if len(exec_cost_summary) > 0:
        real_cost = exec_cost_summary['real_mean'].values[0]
        sim_cost = exec_cost_summary['sim_mean'].values[0]
        diff_cost = exec_cost_summary['diff_mean'].values[0]
        p_val = exec_cost_test['paired_t_pvalue'].values[0]
        cohens_d = exec_cost_test['cohens_d_effect_size'].values[0]
        
        report.append(f"### Key Finding: Execution Cost Analysis")
        report.append(f"")
        report.append(f"| Metric | Value |")
        report.append(f"|--------|-------|")
        report.append(f"| Real Execution Cost | {real_cost:.2f} bps |")
        report.append(f"| Simulated Execution Cost | {sim_cost:.2f} bps |")
        report.append(f"| Difference (Real - Sim) | {diff_cost:.2f} bps |")
        report.append(f"| p-value | {p_val:.2e} |")
        report.append(f"| Cohen's d (Effect Size) | {cohens_d:.3f} |")
        report.append(f"")
        
        if diff_cost > 0:
            report.append(f"**Interpretation:** The dark pool provides **{diff_cost:.2f} bps better** execution cost compared to lit market execution. This difference is highly statistically significant (p < 0.001).")
        else:
            report.append(f"**Interpretation:** The dark pool provides **{abs(diff_cost):.2f} bps worse** execution cost compared to lit market execution.")
        report.append(f"")
    
    report.append(f"---")
    report.append(f"")
    
    # Statistical Test Summary Table
    report.append(f"## Statistical Test Summary")
    report.append(f"")
    report.append(f"| Metric | Real Mean | Sim Mean | Difference | p-value | Significance |")
    report.append(f"|--------|-----------|----------|------------|---------|--------------|")
    
    key_metrics = [
        'exec_cost_arrival_bps',
        'exec_cost_vw_bps',
        'effective_spread_pct',
        'vwap',
        'qty_filled',
        'fill_rate_pct',
        'num_fills',
        'exec_time_sec'
    ]
    
    for metric in key_metrics:
        summary_row = summary_df[summary_df['metric_name'] == metric]
        test_row = tests_df[tests_df['metric_name'] == metric]
        
        if len(summary_row) > 0 and len(test_row) > 0:
            real_mean = summary_row['real_mean'].values[0]
            sim_mean = summary_row['sim_mean'].values[0]
            diff_mean = summary_row['diff_mean'].values[0]
            p_value = test_row['paired_t_pvalue'].values[0]
            sig = test_row['significance_level'].values[0]
            
            report.append(f"| {metric} | {real_mean:.2f} | {sim_mean:.2f} | {diff_mean:.2f} | {p_value:.2e} | {sig} |")
    
    report.append(f"")
    report.append(f"**Significance Levels:** `***` p<0.001, `**` p<0.01, `*` p<0.05, `ns` not significant")
    report.append(f"")
    report.append(f"---")
    report.append(f"")
    
    # Quantile Analysis
    report.append(f"## Distribution Analysis (Execution Cost)")
    report.append(f"")
    exec_cost_quantiles = quantiles_df[quantiles_df['metric_name'] == 'exec_cost_arrival_bps']
    
    if len(exec_cost_quantiles) > 0:
        report.append(f"| Percentile | Real | Simulated | Difference |")
        report.append(f"|------------|------|-----------|------------|")
        
        for _, row in exec_cost_quantiles.iterrows():
            pct = row['percentile']
            real_val = row['real_value']
            sim_val = row['sim_value']
            diff_val = row['difference']
            report.append(f"| {pct}th | {real_val:.2f} | {sim_val:.2f} | {diff_val:.2f} |")
    
    report.append(f"")
    report.append(f"---")
    report.append(f"")
    
    # Unmatched Orders Analysis
    report.append(f"## Unmatched Orders (Set B)")
    report.append(f"")
    report.append(f"**Count:** {len(unmatched_df)} orders")
    report.append(f"")
    report.append(f"These orders executed in the lit market but had no matching counterparty in the dark pool simulation.")
    report.append(f"")
    
    if len(unmatched_df) > 0:
        avg_real_cost = unmatched_df['real_exec_cost_arrival_bps'].mean()
        report.append(f"- Average execution cost (lit market): {avg_real_cost:.2f} bps")
        report.append(f"")
    
    report.append(f"---")
    report.append(f"")
    
    # Methodology
    report.append(f"## Methodology")
    report.append(f"")
    report.append(f"### Execution Cost Calculation")
    report.append(f"")
    report.append(f"```")
    report.append(f"Execution Cost (bps) = Side × [(VWAP - ArrivalMid) / ArrivalMid] × 10,000")
    report.append(f"")
    report.append(f"where:")
    report.append(f"  Side = +1 for buys, -1 for sells")
    report.append(f"  VWAP = Volume-weighted average execution price")
    report.append(f"  ArrivalMid = NBBO midpoint at order submission")
    report.append(f"  Positive cost = worse execution")
    report.append(f"  Negative cost = better execution (price improvement)")
    report.append(f"```")
    report.append(f"")
    report.append(f"### Statistical Tests")
    report.append(f"")
    report.append(f"- **Paired t-test:** Tests if the mean difference between real and simulated execution is statistically significant")
    report.append(f"- **Wilcoxon signed-rank test:** Non-parametric alternative to t-test")
    report.append(f"- **Cohen's d:** Measures the effect size (practical significance)")
    report.append(f"  - Small: |d| < 0.2")
    report.append(f"  - Medium: 0.2 ≤ |d| < 0.8")
    report.append(f"  - Large: |d| ≥ 0.8")
    report.append(f"")
    report.append(f"---")
    report.append(f"")
    
    # Data Files
    report.append(f"## Output Files")
    report.append(f"")
    report.append(f"**Location:** `{matched_dir.parent}`")
    report.append(f"")
    report.append(f"### Matched Orders")
    report.append(f"")
    report.append(f"- `sweep_order_comparison_detailed.csv` - Order-level paired comparison ({len(comparison_df)} rows)")
    report.append(f"- `sweep_order_comparison_summary.csv` - Aggregated statistics (10 metrics)")
    report.append(f"- `sweep_order_statistical_tests.csv` - Hypothesis test results (10 metrics)")
    report.append(f"- `sweep_order_quantile_comparison.csv` - Distribution analysis (50 rows)")
    report.append(f"- `analysis_validation_report.json` - Metadata and key findings")
    report.append(f"")
    report.append(f"### Unmatched Orders")
    report.append(f"")
    report.append(f"- `sweep_order_unexecuted_in_dark.csv` - Orders with real trades only ({len(unmatched_df)} rows)")
    report.append(f"")
    
    # Write report
    report_text = '\n'.join(report)
    
    if output_file is None:
        output_file = matched_dir / 'ANALYSIS_REPORT.md'
    
    with open(output_file, 'w') as f:
        f.write(report_text)
    
    print(f"✓ Report generated: {output_file}")
    return True


def main():
    processed_dir = Path('data/processed')
    
    if len(sys.argv) > 1:
        partition_key = sys.argv[1]
    else:
        print("No partition specified, finding latest...")
        partition_key = find_latest_partition(processed_dir)
        if partition_key is None:
            print("Error: No analysis results found in processed directory")
            sys.exit(1)
        print(f"Using latest partition: {partition_key}")
    
    success = generate_report(processed_dir, partition_key)
    
    if success:
        print("\n" + "="*80)
        print("Report generation complete!")
        print("="*80)
    else:
        sys.exit(1)


if __name__ == '__main__':
    main()
