"""
Step 1 Orchestrator: Data Ingestion with Filtering and NBBO Enrichment

This script implements Step 1 of the pipeline:
1. Read and filter Centre Point orders (10-16 AEST, participantid==69)
2. Match trades to filtered orders
3. Enrich trades with NBBO data by matching security code and timestamp
"""

import pandas as pd
from pathlib import Path
import logging
import sys

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))

from config.columns import INPUT_FILES
from ingest import extract_centrepoint_orders
from match_trades import match_trades
from nbbo import load_nbbo_data, match_trades_with_nbbo

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def run_step1_pipeline(output_dir: str = 'processed_files') -> dict:
    """
    Execute Step 1 of the pipeline: Ingest, filter, and enrich data.
    
    Returns:
        Dictionary containing:
        - orders: Filtered orders DataFrame
        - trades: Matched trades DataFrame
        - trades_agg: Aggregated trades DataFrame
        - trades_with_nbbo: Trades enriched with NBBO data
        - nbbo: NBBO DataFrame
    """
    logger.info("=" * 80)
    logger.info("STEP 1: Data Ingestion with Filtering and NBBO Enrichment")
    logger.info("=" * 80)
    
    Path(output_dir).mkdir(exist_ok=True)
    
    # Step 1.1: Extract Centre Point orders (10-16 AEST)
    logger.info("\n[1.1] Extracting Centre Point orders (10-16 AEST)...")
    orders_df = extract_centrepoint_orders(
        INPUT_FILES['orders'],
        output_dir
    )
    logger.info(f"✓ Extracted {len(orders_df):,} Centre Point orders")
    
    # Step 1.2: Match trades to filtered orders
    logger.info("\n[1.2] Matching trades to filtered orders...")
    trades_df, trades_agg_df = match_trades(
        INPUT_FILES['trades'],
        orders_df['order_id'].tolist(),
        output_dir
    )
    logger.info(f"✓ Matched {len(trades_df):,} trades from {len(trades_agg_df):,} orders")
    
    # Step 1.3: Load NBBO data
    logger.info("\n[1.3] Loading NBBO data...")
    nbbo_df = load_nbbo_data(INPUT_FILES['nbbo'])
    logger.info(f"✓ Loaded {len(nbbo_df):,} NBBO records")
    
    # Step 1.4: Enrich trades with NBBO data
    logger.info("\n[1.4] Enriching trades with NBBO data...")
    trades_with_nbbo_df = match_trades_with_nbbo(trades_df, nbbo_df)
    
    # Save enriched trades
    output_path = Path(output_dir) / 'centrepoint_trades_with_nbbo.csv.gz'
    trades_with_nbbo_df.to_csv(output_path, compression='gzip', index=False)
    logger.info(f"✓ Saved enriched trades to {output_path}")
    
    # Validation
    logger.info("\n" + "=" * 80)
    logger.info("STEP 1 VALIDATION")
    logger.info("=" * 80)
    
    # Check time filtering
    min_hour = orders_df.groupby('order_id').apply(
        lambda x: pd.to_datetime(x['timestamp'].iloc[0], unit='ns', utc=True)
        .tz_convert('UTC+10:00').hour
    ).min()
    max_hour = orders_df.groupby('order_id').apply(
        lambda x: pd.to_datetime(x['timestamp'].iloc[0], unit='ns', utc=True)
        .tz_convert('UTC+10:00').hour
    ).max()
    
    logger.info(f"✓ Time filter check:")
    logger.info(f"  - All orders between {min_hour:02d}:00 and {max_hour:02d}:00 AEST")
    logger.info(f"  - Expected range: 10:00 to 16:00")
    
    # Check Centre Point filter
    all_cp = (orders_df['participantid'] == 69).all()
    logger.info(f"✓ Centre Point filter: {'PASS' if all_cp else 'FAIL'} (all participantid == 69)")
    
    # Check trade matching
    trades_matched_to_orders = trades_df['orderid'].isin(orders_df['order_id']).sum()
    logger.info(f"✓ Trade matching: {trades_matched_to_orders:,}/{len(trades_df):,} trades match filtered orders")
    
    # Check NBBO matching
    nbbo_found = trades_with_nbbo_df['nbbo_found'].sum()
    logger.info(f"✓ NBBO matching: {nbbo_found:,}/{len(trades_with_nbbo_df):,} trades matched with NBBO")
    
    # Check NBBO validity (bid < offer)
    valid_nbbo = (
        (trades_with_nbbo_df['nbbo_bidprice'] < trades_with_nbbo_df['nbbo_offerprice']) | 
        trades_with_nbbo_df['nbbo_bidprice'].isna()
    ).sum()
    logger.info(f"✓ NBBO validity: {valid_nbbo:,}/{len(trades_with_nbbo_df):,} have bid < offer")
    
    logger.info("\n" + "=" * 80)
    logger.info("STEP 1 COMPLETE")
    logger.info("=" * 80)
    
    return {
        'orders': orders_df,
        'trades': trades_df,
        'trades_agg': trades_agg_df,
        'trades_with_nbbo': trades_with_nbbo_df,
        'nbbo': nbbo_df
    }


if __name__ == '__main__':
    results = run_step1_pipeline()
    
    print("\n" + "=" * 80)
    print("STEP 1 OUTPUT SUMMARY")
    print("=" * 80)
    print(f"Orders: {len(results['orders']):,} Centre Point orders (10-16 AEST)")
    print(f"Trades: {len(results['trades']):,} matched trades from {len(results['trades_agg']):,} orders")
    print(f"NBBO: {len(results['nbbo']):,} NBBO records covering {results['nbbo']['orderbookid'].nunique()} securities")
    print(f"Enriched Trades: {results['trades_with_nbbo']['nbbo_found'].sum():,} trades with NBBO data")
    print("=" * 80)
