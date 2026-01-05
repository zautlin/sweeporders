"""
Aggregate Sweep Order Results Across Securities

This script scans the data/outputs directory for all sweep_order_comparison_detailed.csv
files from different securities and dates, merges them into a single dataset with 
identifying columns (date, orderbookid, ticker), and saves the aggregated results.

Usage:
    python aggregate_sweep_results.py
    
Outputs:
    data/outputs/aggregated_sweep_comparison.csv - Merged dataset with all securities
"""

import pandas as pd
from column_schema import col
import os
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Security mapping from orderbookid to ticker
SECURITY_MAPPING = {
    '110621': 'DRR',
    '85603': 'CBA',
    '70616': 'BHP',
    '124055': 'WTC'
}


def find_detailed_comparison_files(outputs_dir='data/outputs'):
    """
    Scan the outputs directory for all sweep_order_comparison_detailed.csv files.
    
    Args:
        outputs_dir: Path to outputs directory
        
    Returns:
        List of tuples (file_path, date, orderbookid, ticker)
    """
    outputs_path = Path(outputs_dir)
    found_files = []
    
    if not outputs_path.exists():
        logger.error(f"Outputs directory not found: {outputs_dir}")
        return found_files
    
    # Pattern: data/outputs/{date}/{orderbookid}/matched/sweep_order_comparison_detailed.csv
    for date_dir in outputs_path.iterdir():
        if not date_dir.is_dir():
            continue
            
        date_str = date_dir.name
        
        for orderbook_dir in date_dir.iterdir():
            if not orderbook_dir.is_dir():
                continue
                
            orderbookid = orderbook_dir.name
            ticker = SECURITY_MAPPING.get(orderbookid, f'UNKNOWN_{orderbookid}')
            
            # Look for the detailed comparison file
            comparison_file = orderbook_dir / 'matched' / 'sweep_order_comparison_detailed.csv'
            
            if comparison_file.exists():
                found_files.append((
                    str(comparison_file),
                    date_str,
                    orderbookid,
                    ticker
                ))
                logger.info(f"Found: {ticker} ({date_str}) - {orderbookid}")
            else:
                logger.warning(f"Missing comparison file for {ticker} ({date_str}) - {orderbookid}")
    
    return found_files


def load_and_tag_file(file_path, date_str, orderbookid, ticker):
    """
    Load a detailed comparison CSV and add identifying columns.
    
    Args:
        file_path: Path to CSV file
        date_str: Date string (YYYY-MM-DD)
        orderbookid: Order book ID
        ticker: Security ticker symbol
        
    Returns:
        DataFrame with added columns: date, orderbookid, ticker
    """
    try:
        df = pd.read_csv(file_path)
        
        # Add identifying columns at the beginning
        df.insert(0, 'ticker', ticker)
        df.insert(1, 'orderbookid', orderbookid)
        df.insert(2, 'date', date_str)
        
        logger.info(f"Loaded {len(df)} orders from {ticker} ({date_str})")
        return df
        
    except Exception as e:
        logger.error(f"Error loading {file_path}: {str(e)}")
        return None


def aggregate_results(outputs_dir='data/outputs'):
    """
    Aggregate all sweep order comparison results into a single dataset.
    
    Args:
        outputs_dir: Path to outputs directory
        
    Returns:
        Aggregated DataFrame with all securities
    """
    logger.info("Starting aggregation of sweep order results...")
    
    # Find all comparison files
    files = find_detailed_comparison_files(outputs_dir)
    
    if not files:
        logger.error("No comparison files found!")
        return None
    
    logger.info(f"Found {len(files)} files to aggregate")
    
    # Load and concatenate all files
    dfs = []
    total_orders = 0
    
    for file_path, date_str, orderbookid, ticker in files:
        df = load_and_tag_file(file_path, date_str, orderbookid, ticker)
        
        if df is not None:
            dfs.append(df)
            total_orders += len(df)
    
    if not dfs:
        logger.error("No data loaded from any files!")
        return None
    
    # Concatenate all DataFrames
    logger.info(f"Concatenating {len(dfs)} DataFrames with {total_orders} total orders...")
    aggregated_df = pd.concat(dfs, ignore_index=True)
    
    # Sort by ticker, date, orderid for easier analysis
    aggregated_df = aggregated_df.sort_values(['ticker', 'date', 'orderid']).reset_index(drop=True)
    
    logger.info(f"Aggregation complete: {len(aggregated_df)} orders from {len(dfs)} securities")
    
    return aggregated_df


def print_summary_stats(df):
    """Print summary statistics of the aggregated dataset."""
    logger.info("\n" + "="*80)
    logger.info("AGGREGATED DATASET SUMMARY")
    logger.info("="*80)
    
    # Overall stats
    logger.info(f"\nTotal Orders: {len(df):,}")
    logger.info(f"Securities: {df[col.common.ticker].nunique()}")
    logger.info(f"Dates: {df[col.common.date].nunique()}")
    
    # Per-security breakdown
    logger.info("\nOrders by Security:")
    for ticker in sorted(df[col.common.ticker].unique()):
        count = len(df[df[col.common.ticker] == ticker])
        pct = (count / len(df)) * 100
        logger.info(f"  {ticker:4s}: {count:6,} orders ({pct:5.2f}%)")
    
    # Dark pool performance overview
    logger.info("\nDark Pool Performance (Exec Cost Arrival, bps):")
    for ticker in sorted(df[col.common.ticker].unique()):
        ticker_df = df[df[col.common.ticker] == ticker]
        mean_diff = ticker_df['exec_cost_arrival_diff_bps'].mean()
        better_count = (ticker_df['dark_pool_better'] == True).sum()
        better_pct = (better_count / len(ticker_df)) * 100
        
        status = "BETTER" if mean_diff > 0 else "WORSE"
        logger.info(f"  {ticker:4s}: {mean_diff:+7.2f} bps ({status}) - {better_pct:5.2f}% orders better in dark")
    
    # Overall dark pool performance
    overall_mean = df['exec_cost_arrival_diff_bps'].mean()
    overall_better = (df['dark_pool_better'] == True).sum()
    overall_better_pct = (overall_better / len(df)) * 100
    
    logger.info(f"\n  ALL : {overall_mean:+7.2f} bps (OVERALL) - {overall_better_pct:5.2f}% orders better in dark")
    logger.info("="*80 + "\n")


def save_aggregated_results(df, output_path='data/aggregated/aggregated_sweep_comparison.csv'):
    """Save the aggregated DataFrame to CSV."""
    try:
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        df.to_csv(output_path, index=False)
        logger.info(f"Aggregated results saved to: {output_path}")
        logger.info(f"File size: {os.path.getsize(output_path) / (1024*1024):.2f} MB")
        
        return True
        
    except Exception as e:
        logger.error(f"Error saving aggregated results: {str(e)}")
        return False


def main():
    """Main execution function."""
    logger.info("="*80)
    logger.info("SWEEP ORDER RESULTS AGGREGATION")
    logger.info("="*80 + "\n")
    
    # Aggregate results
    df = aggregate_results()
    
    if df is None:
        logger.error("Aggregation failed!")
        return 1
    
    # Print summary statistics
    print_summary_stats(df)
    
    # Save results
    output_path = 'data/aggregated/aggregated_sweep_comparison.csv'
    success = save_aggregated_results(df, output_path)
    
    if success:
        logger.info("\n✓ Aggregation completed successfully!")
        logger.info(f"✓ Output file: {output_path}")
        return 0
    else:
        logger.error("\n✗ Failed to save aggregated results")
        return 1


if __name__ == '__main__':
    exit(main())
