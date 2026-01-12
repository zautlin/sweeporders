#!/usr/bin/env python3
"""
Extract detailed metrics for cross-security comparison analysis
"""

import pandas as pd
import numpy as np

# Load the full dataset
df = pd.read_csv('/Users/agautam/workspace/python/sweeporders/data/aggregated/aggregated_sweep_comparison.csv')

# Define all metrics to analyze
metrics = [
    # Group 1: Cost Metrics
    ('exec_cost_arrival_diff_bps', 'Execution Cost (Arrival)', 'bps'),
    ('exec_cost_vw_diff_bps', 'Execution Cost (VW)', 'bps'),
    # Group 2: Time Metrics
    ('exec_time_diff_sec', 'Execution Time', 'sec'),
    # Group 3: Quantity Metrics  
    ('qty_diff', 'Quantity Filled', 'units'),
    # Group 4: Price Metrics
    ('vwap_diff', 'VWAP Difference', 'price'),
]

# Calculate statistics for each security
securities = ['BHP', 'CBA', 'DRR', 'WTC']

print("=" * 100)
print("CROSS-SECURITY METRIC ANALYSIS")
print("=" * 100)
print()

for metric_key, metric_name, unit in metrics:
    print(f"\n{'=' * 100}")
    print(f"METRIC: {metric_name} ({metric_key})")
    print(f"{'=' * 100}")
    print()
    
    # Portfolio level
    portfolio_data = df[metric_key].dropna()
    port_mean = portfolio_data.mean()
    port_median = portfolio_data.median()
    port_std = portfolio_data.std()
    port_n = len(portfolio_data)
    port_cohens_d = port_mean / port_std if port_std != 0 else 0
    
    # Determine effect size category
    abs_d = abs(port_cohens_d)
    if abs_d < 0.2:
        effect_size = "negligible"
    elif abs_d < 0.5:
        effect_size = "small"
    elif abs_d < 0.8:
        effect_size = "medium"
    else:
        effect_size = "large"
    
    print(f"Portfolio: n={port_n:,}, mean={port_mean:.2f} {unit}, median={port_median:.2f} {unit}, "
          f"std={port_std:.2f} {unit}, d={port_cohens_d:.2f} ({effect_size})")
    print()
    
    # Per security
    for ticker in securities:
        sec_data = df[df['ticker'] == ticker][metric_key].dropna()
        
        if len(sec_data) == 0:
            continue
            
        mean_val = sec_data.mean()
        median_val = sec_data.median()
        std_val = sec_data.std()
        n_orders = len(sec_data)
        cohens_d = mean_val / std_val if std_val != 0 else 0
        
        # Determine effect size
        abs_d = abs(cohens_d)
        if abs_d < 0.2:
            eff = "negligible"
        elif abs_d < 0.5:
            eff = "small"
        elif abs_d < 0.8:
            eff = "medium"
        else:
            eff = "large"
        
        # Dark pool better percentage
        dark_better = (sec_data < 0).sum()  # negative diff means dark pool better (lower cost)
        dark_better_pct = (dark_better / n_orders * 100) if n_orders > 0 else 0
        
        print(f"{ticker:4s}: n={n_orders:5,}, mean={mean_val:8.2f} {unit:5s}, median={median_val:8.2f} {unit:5s}, "
              f"std={std_val:6.2f} {unit:5s}, d={cohens_d:6.2f} ({eff:10s}), dark_better={dark_better_pct:5.1f}%")
    
    print()

# Additional analysis: compute derived metrics from raw columns
print(f"\n{'=' * 100}")
print("ADDITIONAL DERIVED METRICS (computed from raw data)")
print("=" * 100)
print()

# Slippage metrics
for slip_type, real_col, sim_col in [
    ('Arrival', 'real_slippage_bps', 'sim_slippage_bps'),
]:
    if real_col in df.columns and sim_col in df.columns:
        df[f'slippage_{slip_type.lower()}_diff'] = df[real_col] - df[sim_col]
        
        print(f"\nSlippage ({slip_type}):")
        portfolio_slip = df[f'slippage_{slip_type.lower()}_diff'].dropna()
        print(f"Portfolio: mean={portfolio_slip.mean():.2f} bps, median={portfolio_slip.median():.2f} bps")
        
        for ticker in securities:
            sec_slip = df[df['ticker'] == ticker][f'slippage_{slip_type.lower()}_diff'].dropna()
            if len(sec_slip) > 0:
                print(f"{ticker}: mean={sec_slip.mean():.2f} bps, median={sec_slip.median():.2f} bps")

# Implementation shortfall
if 'real_implementation_shortfall_bps' in df.columns and 'sim_implementation_shortfall_bps' in df.columns:
    df['implementation_shortfall_diff_bps'] = df['real_implementation_shortfall_bps'] - df['sim_implementation_shortfall_bps']
    
    print(f"\nImplementation Shortfall:")
    portfolio_is = df['implementation_shortfall_diff_bps'].dropna()
    print(f"Portfolio: mean={portfolio_is.mean():.2f} bps, median={portfolio_is.median():.2f} bps")
    
    for ticker in securities:
        sec_is = df[df['ticker'] == ticker]['implementation_shortfall_diff_bps'].dropna()
        if len(sec_is) > 0:
            print(f"{ticker}: mean={sec_is.mean():.2f} bps, median={sec_is.median():.2f} bps")

# Time to first fill
if 'real_time_to_first_fill_sec' in df.columns and 'sim_time_to_first_fill_sec' in df.columns:
    df['time_to_first_fill_diff_sec'] = df['real_time_to_first_fill_sec'] - df['sim_time_to_first_fill_sec']
    
    print(f"\nTime to First Fill:")
    portfolio_ttff = df['time_to_first_fill_diff_sec'].dropna()
    print(f"Portfolio: mean={portfolio_ttff.mean():.2f} sec, median={portfolio_ttff.median():.2f} sec")
    
    for ticker in securities:
        sec_ttff = df[df['ticker'] == ticker]['time_to_first_fill_diff_sec'].dropna()
        if len(sec_ttff) > 0:
            print(f"{ticker}: mean={sec_ttff.mean():.2f} sec, median={sec_ttff.median():.2f} sec")

# Fill ratio
if 'real_fill_ratio' in df.columns and 'sim_fill_ratio' in df.columns:
    df['fill_ratio_diff'] = df['real_fill_ratio'] - df['sim_fill_ratio']
    
    print(f"\nFill Ratio:")
    portfolio_fr = df['fill_ratio_diff'].dropna()
    print(f"Portfolio: mean={portfolio_fr.mean():.4f}, median={portfolio_fr.median():.4f}")
    
    for ticker in securities:
        sec_fr = df[df['ticker'] == ticker]['fill_ratio_diff'].dropna()
        if len(sec_fr) > 0:
            print(f"{ticker}: mean={sec_fr.mean():.4f}, median={sec_fr.median():.4f}")

# Number of fills
if 'real_num_fills' in df.columns and 'sim_num_fills' in df.columns:
    df['num_fills_diff'] = df['real_num_fills'] - df['sim_num_fills']
    
    print(f"\nNumber of Fills:")
    portfolio_nf = df['num_fills_diff'].dropna()
    print(f"Portfolio: mean={portfolio_nf.mean():.2f}, median={portfolio_nf.median():.2f}")
    
    for ticker in securities:
        sec_nf = df[df['ticker'] == ticker]['num_fills_diff'].dropna()
        if len(sec_nf) > 0:
            print(f"{ticker}: mean={sec_nf.mean():.2f}, median={sec_nf.median():.2f}")

# Average fill size
if 'real_avg_fill_size' in df.columns and 'sim_avg_fill_size' in df.columns:
    df['avg_fill_size_diff'] = df['real_avg_fill_size'] - df['sim_avg_fill_size']
    
    print(f"\nAverage Fill Size:")
    portfolio_afs = df['avg_fill_size_diff'].dropna()
    print(f"Portfolio: mean={portfolio_afs.mean():.2f} units, median={portfolio_afs.median():.2f} units")
    
    for ticker in securities:
        sec_afs = df[df['ticker'] == ticker]['avg_fill_size_diff'].dropna()
        if len(sec_afs) > 0:
            print(f"{ticker}: mean={sec_afs.mean():.2f} units, median={sec_afs.median():.2f} units")

# Spread metrics
for spread_type, real_col, sim_col in [
    ('Arrival', 'real_arrival_spread_bps', 'sim_arrival_spread_bps'),
]:
    if real_col in df.columns and sim_col in df.columns:
        df[f'{spread_type.lower()}_spread_diff_bps'] = df[real_col] - df[sim_col]
        
        print(f"\n{spread_type} Spread:")
        portfolio_spread = df[f'{spread_type.lower()}_spread_diff_bps'].dropna()
        print(f"Portfolio: mean={portfolio_spread.mean():.2f} bps, median={portfolio_spread.median():.2f} bps")
        
        for ticker in securities:
            sec_spread = df[df['ticker'] == ticker][f'{spread_type.lower()}_spread_diff_bps'].dropna()
            if len(sec_spread) > 0:
                print(f"{ticker}: mean={sec_spread.mean():.2f} bps, median={sec_spread.median():.2f} bps")

# Market drift
if 'real_market_drift_bps' in df.columns and 'sim_market_drift_bps' in df.columns:
    df['market_drift_diff_bps'] = df['real_market_drift_bps'] - df['sim_market_drift_bps']
    
    print(f"\nMarket Drift:")
    portfolio_md = df['market_drift_diff_bps'].dropna()
    print(f"Portfolio: mean={portfolio_md.mean():.2f} bps, median={portfolio_md.median():.2f} bps")
    
    for ticker in securities:
        sec_md = df[df['ticker'] == ticker]['market_drift_diff_bps'].dropna()
        if len(sec_md) > 0:
            print(f"{ticker}: mean={sec_md.mean():.2f} bps, median={sec_md.median():.2f} bps")

# Price improvement
if 'real_price_improvement_bps' in df.columns and 'sim_price_improvement_bps' in df.columns:
    df['price_improvement_diff_bps'] = df['real_price_improvement_bps'] - df['sim_price_improvement_bps']
    
    print(f"\nPrice Improvement:")
    portfolio_pi = df['price_improvement_diff_bps'].dropna()
    print(f"Portfolio: mean={portfolio_pi.mean():.2f} bps, median={portfolio_pi.median():.2f} bps")
    
    for ticker in securities:
        sec_pi = df[df['ticker'] == ticker]['price_improvement_diff_bps'].dropna()
        if len(sec_pi) > 0:
            print(f"{ticker}: mean={sec_pi.mean():.2f} bps, median={sec_pi.median():.2f} bps")

print("\n" + "=" * 100)
print("ANALYSIS COMPLETE")
print("=" * 100)
