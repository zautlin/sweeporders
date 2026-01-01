"""
Phase 1.3: NBBO Data Loading and Trade Enrichment
Reads NBBO data from nbbo.csv and matches it with trades by security code and timestamp.
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config.columns import INPUT_FILES

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_nbbo_data(nbbo_file: str) -> pd.DataFrame:
    """
    Load NBBO data from CSV file.
    
    NBBO file columns:
    - orderbookid: Security identifier (matches security_code from orders/trades)
    - bidprice: Bid price at snapshot time
    - offerprice: Ask/offer price at snapshot time
    - timestamp: Time of NBBO snapshot
    
    Args:
        nbbo_file: Path to NBBO CSV file
        
    Returns:
        DataFrame with NBBO data
    """
    logger.info(f"Loading NBBO data from: {nbbo_file}")
    
    nbbo_df = pd.read_csv(nbbo_file)
    logger.info(f"Total NBBO records read: {len(nbbo_df):,}")
    
    # Convert types
    nbbo_df['orderbookid'] = nbbo_df['orderbookid'].astype('int64')
    nbbo_df['timestamp'] = nbbo_df['timestamp'].astype('int64')
    nbbo_df['bidprice'] = pd.to_numeric(nbbo_df['bidprice'], errors='coerce').astype('float32')
    nbbo_df['offerprice'] = pd.to_numeric(nbbo_df['offerprice'], errors='coerce').astype('float32')
    
    # Remove invalid records (where bid or offer is negative or null)
    nbbo_df = nbbo_df[(nbbo_df['bidprice'] > 0) & (nbbo_df['offerprice'] > 0)].copy()
    logger.info(f"NBBO records after validation: {len(nbbo_df):,}")
    
    # Sort by orderbookid and timestamp for efficient lookup
    nbbo_df = nbbo_df.sort_values(['orderbookid', 'timestamp']).reset_index(drop=True)
    
    logger.info(f"Unique order books (securities): {nbbo_df['orderbookid'].nunique()}")
    
    return nbbo_df


def match_trades_with_nbbo(trades_df: pd.DataFrame, nbbo_df: pd.DataFrame) -> pd.DataFrame:
    """
    Enrich trades with NBBO data by matching on security code and finding closest NBBO before trade.
    
    For each trade, find the NBBO record where:
    1. orderbookid (from NBBO) == securitycode (from trades)
    2. NBBO timestamp is the largest timestamp <= trade timestamp
    
    Note: If no NBBO data matches the security code, the trade will still be included
    but with NaN values for NBBO columns.
    
    Args:
        trades_df: Trades DataFrame with columns:
            - orderid, tradetime, securitycode, tradeprice, quantity, side, participantid
        nbbo_df: NBBO DataFrame with columns:
            - orderbookid, bidprice, offerprice, timestamp
            
    Returns:
        Trades DataFrame enriched with NBBO columns: nbbo_bidprice, nbbo_offerprice, nbbo_timestamp, nbbo_found
    """
    logger.info("Matching trades with NBBO data...")
    
    # Make copies to avoid modifying originals
    trades = trades_df.copy()
    nbbo = nbbo_df.copy()
    
    # Ensure security codes match field names
    trades['orderbookid'] = trades['securitycode']
    
    # For each trade, find the closest NBBO before the trade
    enriched_trades = []
    nbbo_matched_count = 0
    
    for idx, trade_row in trades.iterrows():
        trade_orderid = trade_row['orderid']
        trade_securitycode = trade_row['securitycode']
        trade_time = trade_row['tradetime']
        
        # Filter NBBO for this security code
        nbbo_for_security = nbbo[nbbo['orderbookid'] == trade_securitycode]
        
        if len(nbbo_for_security) == 0:
            # No NBBO data for this security code - add empty NBBO columns
            enriched_trades.append({
                **trade_row.to_dict(),
                'nbbo_bidprice': np.nan,
                'nbbo_offerprice': np.nan,
                'nbbo_timestamp': np.nan,
                'nbbo_found': False
            })
            continue
        
        # Find NBBO records with timestamp <= trade_time
        nbbo_before_trade = nbbo_for_security[nbbo_for_security['timestamp'] <= trade_time]
        
        if len(nbbo_before_trade) == 0:
            # No NBBO data before trade time - add empty NBBO columns
            enriched_trades.append({
                **trade_row.to_dict(),
                'nbbo_bidprice': np.nan,
                'nbbo_offerprice': np.nan,
                'nbbo_timestamp': np.nan,
                'nbbo_found': False
            })
            continue
        
        # Get the closest NBBO before trade (largest timestamp <= trade_time)
        closest_nbbo = nbbo_before_trade.iloc[-1]  # Last one is the closest
        
        enriched_trades.append({
            **trade_row.to_dict(),
            'nbbo_bidprice': closest_nbbo['bidprice'],
            'nbbo_offerprice': closest_nbbo['offerprice'],
            'nbbo_timestamp': closest_nbbo['timestamp'],
            'nbbo_found': True
        })
        nbbo_matched_count += 1
    
    enriched_df = pd.DataFrame(enriched_trades)
    
    logger.info(f"Matched {nbbo_matched_count:,} / {len(enriched_df):,} trades with NBBO data ({100*nbbo_matched_count/len(enriched_df):.1f}%)")
    logger.info(f"Note: {len(enriched_df) - nbbo_matched_count:,} trades have no matching NBBO (different securities)")
    
    return enriched_df


def enrich_trades_with_nbbo(trades_file: str, nbbo_file: str, output_dir: str) -> pd.DataFrame:
    """
    Load trades, load NBBO, and enrich trades with NBBO data.
    
    Args:
        trades_file: Path to trades CSV file
        nbbo_file: Path to NBBO CSV file
        output_dir: Directory to save enriched trades
        
    Returns:
        DataFrame with enriched trades
    """
    logger.info("Starting trade enrichment with NBBO data...")
    
    # Load data
    trades_df = pd.read_csv(trades_file)
    logger.info(f"Loaded {len(trades_df):,} trades")
    
    nbbo_df = load_nbbo_data(nbbo_file)
    
    # Match and enrich
    enriched_trades = match_trades_with_nbbo(trades_df, nbbo_df)
    
    # Save enriched trades
    output_path = Path(output_dir) / 'centrepoint_trades_with_nbbo.csv.gz'
    enriched_trades.to_csv(output_path, compression='gzip', index=False)
    logger.info(f"Saved enriched trades to {output_path}")
    
    return enriched_trades


if __name__ == '__main__':
    output_dir = 'processed_files'
    Path(output_dir).mkdir(exist_ok=True)
    
    # Load NBBO
    nbbo_file = INPUT_FILES.get('nbbo', 'data/nbbo/nbbo.csv')
    nbbo_data = load_nbbo_data(nbbo_file)
    
    print(f"\nLoaded {len(nbbo_data):,} NBBO records")
    print(f"Covering {nbbo_data['orderbookid'].nunique()} unique securities")
