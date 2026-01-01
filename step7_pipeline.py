"""
Phase 7: Extended Analysis - Group 3 Simulation, Time of Day, Order Size, and Participant

Extends the dark pool simulation analysis to:
1. Simulate Group 3 (unexecuted orders) for execution improvement potential
2. Analyze execution metrics by time of day
3. Analyze execution metrics by order size
4. Analyze execution metrics by participant

This provides deeper insights into execution patterns and optimization opportunities.
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


def load_data(output_dir='processed_files'):
    """Load all necessary data."""
    logger.info("Loading data for extended analysis...")
    
    classified = pd.read_csv(f'{output_dir}/sweep_orders_classified.csv.gz')
    trades = pd.read_csv(f'{output_dir}/centrepoint_trades_raw.csv.gz')
    nbbo = pd.read_csv('data/nbbo/nbbo.csv')
    raw_orders = pd.read_csv(INPUT_FILES['orders'])
    real_metrics = pd.read_csv(f'{output_dir}/real_execution_metrics.csv', index_col=0)
    
    logger.info(f"✓ Loaded {len(classified)} classified orders, {len(trades)} trades")
    
    return classified, trades, nbbo, raw_orders, real_metrics


def add_time_features(df):
    """Convert timestamps and add time-based features."""
    # Convert nanoseconds to datetime and add 10 hours for AEST (UTC+10)
    df['timestamp_dt'] = pd.to_datetime(df['timestamp'], unit='ns') + pd.Timedelta(hours=10)
    df['hour_aest'] = df['timestamp_dt'].dt.hour
    df['date'] = df['timestamp_dt'].dt.date
    
    # Create time of day buckets
    def categorize_time(hour):
        if 10 <= hour < 12:
            return 'Morning (10-12)'
        elif 12 <= hour < 15:
            return 'Midday (12-15)'
        elif 15 <= hour < 18:
            return 'Afternoon (15-18)'
        else:
            return 'Outside Hours'
    
    df['time_of_day'] = df['hour_aest'].apply(categorize_time)
    
    return df


def add_size_features(df):
    """Add order size categorization."""
    def categorize_size(qty):
        if qty <= 100:
            return 'Tiny (1-100)'
        elif qty <= 1000:
            return 'Small (101-1K)'
        elif qty <= 10000:
            return 'Medium (1K-10K)'
        else:
            return 'Large (10K+)'
    
    df['size_category'] = df['quantity'].apply(categorize_size)
    return df


def analyze_group3(classified, trades, nbbo, raw_orders):
    """
    Analyze Group 3 (unexecuted orders) and estimate execution potential.
    
    Key questions:
    1. Why weren't these orders executed?
    2. Could dark pools have improved execution?
    3. What was the market opportunity cost?
    """
    logger.info("\n" + "=" * 80)
    logger.info("ANALYSIS 1: GROUP 3 - UNEXECUTED ORDERS")
    logger.info("=" * 80)
    
    group3 = classified[classified['sweep_group'] == 'GROUP_3_NOT_EXECUTED'].copy()
    logger.info(f"\nGroup 3 Orders: {len(group3)}")
    
    if len(group3) > 0:
        # Add time features
        group3 = add_time_features(group3)
        
        logger.info("\nGroup 3 Orders Details:")
        print(group3[['order_id', 'quantity', 'price', 'side', 'time_of_day']].to_string())
        
        # Summary statistics
        logger.info(f"\nTotal Quantity Unexecuted: {group3['quantity'].sum():,.0f} units")
        logger.info(f"Average Order Size: {group3['quantity'].mean():,.0f} units")
        logger.info(f"Average Order Price: ${group3['price'].mean():.2f}")
        
        # Opportunity cost at mid-price
        mid_price = (nbbo['bidprice'].mean() + nbbo['offerprice'].mean()) / 2
        opportunity_qty = group3['quantity'].sum()
        opportunity_cost_actual = (group3['quantity'] * group3['price']).sum()
        opportunity_cost_dark = opportunity_qty * mid_price
        potential_savings = opportunity_cost_actual - opportunity_cost_dark
        
        logger.info(f"\nOpportunity Analysis:")
        logger.info(f"  Potential Cost (at stated price): ${opportunity_cost_actual:,.0f}")
        logger.info(f"  Dark Pool Cost (at mid-price ${mid_price:.2f}): ${opportunity_cost_dark:,.0f}")
        logger.info(f"  Potential Savings if executed in dark: ${potential_savings:,.0f}")
        logger.info(f"  Percentage: {(potential_savings / opportunity_cost_actual * 100) if opportunity_cost_actual > 0 else 0:.2f}%")
        
        # Reasons for non-execution
        logger.info(f"\nGroup 3 Distribution by Time of Day:")
        print(group3['time_of_day'].value_counts())
        
        logger.info(f"\nGroup 3 Distribution by Size:")
        group3 = add_size_features(group3)
        print(group3['size_category'].value_counts())
    
    return group3


def analyze_by_time_of_day(classified, real_metrics):
    """Analyze execution metrics by time of day."""
    logger.info("\n" + "=" * 80)
    logger.info("ANALYSIS 2: TIME OF DAY PATTERNS")
    logger.info("=" * 80)
    
    classified = add_time_features(classified)
    
    logger.info(f"\nOrders by Time of Day:")
    time_distribution = classified['time_of_day'].value_counts()
    print(time_distribution)
    
    # Execution metrics by time of day
    logger.info(f"\nExecution Metrics by Time of Day:")
    time_analysis = classified.groupby('time_of_day', observed=True).agg({
        'order_id': 'count',
        'quantity': ['sum', 'mean'],
        'price': 'mean',
        'totalmatchedquantity': 'sum',
        'leavesquantity': 'mean'
    }).round(2)
    
    time_analysis.columns = ['Orders', 'Total Qty', 'Avg Qty', 'Avg Price', 
                             'Total Matched', 'Avg Leaves']
    print(time_analysis)
    
    # Fill ratio by time of day
    classified['fill_ratio'] = classified['totalmatchedquantity'] / classified['quantity']
    logger.info(f"\nFill Ratio by Time of Day:")
    fill_by_time = classified.groupby('time_of_day', observed=True).agg({
        'fill_ratio': ['mean', 'min', 'max', 'std'],
        'order_id': 'count'
    }).round(4)
    fill_by_time.columns = ['Avg Fill %', 'Min Fill %', 'Max Fill %', 'Std Dev', 'Count']
    print(fill_by_time * 100)  # Convert to percentage
    
    # Classification distribution by time of day
    logger.info(f"\nOrder Classification by Time of Day:")
    class_by_time = pd.crosstab(classified['time_of_day'], classified['sweep_group'])
    print(class_by_time)
    
    return classified


def analyze_by_order_size(classified, real_metrics):
    """Analyze execution metrics by order size."""
    logger.info("\n" + "=" * 80)
    logger.info("ANALYSIS 3: ORDER SIZE PATTERNS")
    logger.info("=" * 80)
    
    classified = add_size_features(classified)
    
    logger.info(f"\nOrders by Size Category:")
    size_distribution = classified['size_category'].value_counts().sort_index()
    print(size_distribution)
    
    # Execution metrics by size
    size_order = ['Tiny (1-100)', 'Small (101-1K)', 'Medium (1K-10K)', 'Large (10K+)']
    classified['size_category'] = pd.Categorical(classified['size_category'], categories=size_order, ordered=True)
    
    logger.info(f"\nExecution Metrics by Order Size:")
    size_analysis = classified.groupby('size_category', observed=True).agg({
        'order_id': 'count',
        'quantity': ['sum', 'mean'],
        'price': 'mean',
        'totalmatchedquantity': 'sum'
    }).round(2)
    
    size_analysis.columns = ['Orders', 'Total Qty', 'Avg Qty', 'Avg Price', 'Total Matched']
    print(size_analysis)
    
    # Fill ratio by size
    classified['fill_ratio'] = classified['totalmatchedquantity'] / classified['quantity']
    logger.info(f"\nFill Ratio by Order Size:")
    fill_by_size = classified.groupby('size_category', observed=True).agg({
        'fill_ratio': ['mean', 'min', 'max', 'std'],
        'order_id': 'count'
    }).round(4)
    fill_by_size.columns = ['Avg Fill %', 'Min Fill %', 'Max Fill %', 'Std Dev', 'Count']
    print(fill_by_size * 100)  # Convert to percentage
    
    # Classification distribution by size
    logger.info(f"\nOrder Classification by Size:")
    class_by_size = pd.crosstab(classified['size_category'], classified['sweep_group'])
    print(class_by_size)
    
    return classified


def analyze_by_participant(classified, raw_orders):
    """Analyze execution metrics by participant."""
    logger.info("\n" + "=" * 80)
    logger.info("ANALYSIS 4: PARTICIPANT ANALYSIS")
    logger.info("=" * 80)
    
    logger.info(f"\nParticipants in Dataset:")
    
    # Merge participant info
    raw_summary = raw_orders.groupby('participantid').agg({
        'order_id': 'nunique',
        'quantity': 'sum'
    }).rename(columns={'order_id': 'raw_orders', 'quantity': 'raw_qty'})
    
    logger.info(f"Raw orders by participant:")
    print(raw_summary.sort_values('raw_orders', ascending=False).head(10))
    
    # Classified orders by participant
    logger.info(f"\nSweep Orders (Classified) by Participant:")
    classified_summary = classified.groupby('participantid').agg({
        'order_id': 'count',
        'quantity': 'sum',
        'totalmatchedquantity': 'sum',
        'price': 'mean'
    }).rename(columns={
        'order_id': 'sweep_orders',
        'quantity': 'total_qty',
        'totalmatchedquantity': 'matched_qty',
        'price': 'avg_price'
    }).round(2)
    
    classified_summary['fill_ratio'] = (classified_summary['matched_qty'] / 
                                       classified_summary['total_qty']).round(4)
    
    print(classified_summary)
    
    # Classification by participant
    logger.info(f"\nClassification Distribution by Participant:")
    class_by_participant = pd.crosstab(classified['participantid'], 
                                       classified['sweep_group'], 
                                       margins=True)
    print(class_by_participant)
    
    return classified


def save_extended_analysis_results(classified_time, classified_size, classified_participant, 
                                   group3, output_dir='processed_files'):
    """Save all analysis results to files."""
    logger.info("\n" + "=" * 80)
    logger.info("SAVING EXTENDED ANALYSIS RESULTS")
    logger.info("=" * 80)
    
    # Create summary dataframes for export
    
    # 1. Time of day summary
    time_summary = classified_time.groupby('time_of_day', observed=True).agg({
        'order_id': 'count',
        'quantity': 'sum',
        'totalmatchedquantity': 'sum',
        'price': 'mean',
        'leavesquantity': 'mean'
    }).round(2)
    time_summary['fill_ratio'] = (time_summary['totalmatchedquantity'] / 
                                  time_summary['quantity']).round(4)
    time_summary = time_summary.reset_index()
    time_summary.to_csv(f'{output_dir}/analysis_by_time_of_day.csv', index=False)
    logger.info(f"✓ Saved analysis_by_time_of_day.csv")
    
    # 2. Order size summary
    size_order = ['Tiny (1-100)', 'Small (101-1K)', 'Medium (1K-10K)', 'Large (10K+)']
    classified_size['size_category'] = pd.Categorical(classified_size['size_category'], 
                                                       categories=size_order, ordered=True)
    size_summary = classified_size.groupby('size_category', observed=True).agg({
        'order_id': 'count',
        'quantity': 'sum',
        'totalmatchedquantity': 'sum',
        'price': 'mean',
        'leavesquantity': 'mean'
    }).round(2)
    size_summary['fill_ratio'] = (size_summary['totalmatchedquantity'] / 
                                  size_summary['quantity']).round(4)
    size_summary = size_summary.reset_index()
    size_summary.to_csv(f'{output_dir}/analysis_by_order_size.csv', index=False)
    logger.info(f"✓ Saved analysis_by_order_size.csv")
    
    # 3. Participant summary
    participant_summary = classified_participant.groupby('participantid').agg({
        'order_id': 'count',
        'quantity': 'sum',
        'totalmatchedquantity': 'sum',
        'price': 'mean'
    }).round(2)
    participant_summary['fill_ratio'] = (participant_summary['totalmatchedquantity'] / 
                                         participant_summary['quantity']).round(4)
    participant_summary = participant_summary.reset_index()
    participant_summary.to_csv(f'{output_dir}/analysis_by_participant.csv', index=False)
    logger.info(f"✓ Saved analysis_by_participant.csv")
    
    # 4. Group 3 analysis
    if len(group3) > 0:
        group3_export = group3[['order_id', 'quantity', 'price', 'side', 
                               'totalmatchedquantity', 'time_of_day', 'size_category']].copy()
        group3_export.to_csv(f'{output_dir}/group3_unexecuted_analysis.csv', index=False)
        logger.info(f"✓ Saved group3_unexecuted_analysis.csv")
    
    logger.info("\n✓ All extended analysis results saved")


def main():
    """Run all extended analyses."""
    logger.info("Starting Step 7: Extended Analysis")
    
    # Load data
    classified, trades, nbbo, raw_orders, real_metrics = load_data()
    
    # Analysis 1: Group 3 unexecuted orders
    group3 = analyze_group3(classified, trades, nbbo, raw_orders)
    
    # Analysis 2: Time of day patterns
    classified_time = analyze_by_time_of_day(classified.copy(), real_metrics)
    
    # Analysis 3: Order size patterns
    classified_size = analyze_by_order_size(classified.copy(), real_metrics)
    
    # Analysis 4: Participant analysis
    classified_participant = analyze_by_participant(classified.copy(), raw_orders)
    
    # Save results
    save_extended_analysis_results(classified_time, classified_size, 
                                   classified_participant, group3)
    
    logger.info("\n" + "=" * 80)
    logger.info("✓ STEP 7: EXTENDED ANALYSIS COMPLETE")
    logger.info("=" * 80)


if __name__ == '__main__':
    main()
