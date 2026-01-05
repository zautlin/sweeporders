"""
Security Auto-Discovery Module

Automatically discovers all available securities (orderbookids) from raw order and trade files.
No hardcoding required - works with any securities present in the raw data.

Author: Centre Point Analysis Pipeline
Date: 2026-01-05
"""

import pandas as pd
from config.column_schema import col
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import logging
from dataclasses import dataclass
import re

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class SecurityInfo:
    """Information about a discovered security"""
    orderbookid: int
    ticker: Optional[str]
    date: str
    in_orders: bool
    in_trades: bool
    order_count: int
    trade_count: int
    order_file: Optional[str]
    trade_file: Optional[str]
    
    def __str__(self):
        status = []
        if self.in_orders:
            status.append(f"Orders: {self.order_count:,}")
        if self.in_trades:
            status.append(f"Trades: {self.trade_count:,}")
        
        ticker_str = self.ticker.upper() if self.ticker else "Unknown"
        return f"OrderbookID {self.orderbookid} ({ticker_str}) - {', '.join(status)}"


class SecurityDiscovery:
    """
    Discovers all available securities from raw data files
    """
    
    def __init__(self, raw_data_dir: str = 'data/raw', 
                 min_orders: int = 100, 
                 min_trades: int = 10):
        """
        Initialize security discovery
        
        Args:
            raw_data_dir: Path to raw data directory
            min_orders: Minimum number of orders to consider a security valid
            min_trades: Minimum number of trades to consider a security valid
        """
        self.raw_data_dir = Path(raw_data_dir)
        self.min_orders = min_orders
        self.min_trades = min_trades
        
    def get_available_dates(self) -> List[str]:
        """
        Scan raw data directory to find all available dates
        
        Returns:
            List of date strings (YYYYMMDD format)
        """
        dates = set()
        
        # Scan order files
        orders_dir = self.raw_data_dir / 'orders'
        if orders_dir.exists():
            for file_path in orders_dir.glob('*_orders.csv'):
                date = self._extract_date_from_filename(file_path.name)
                if date:
                    dates.add(date)
        
        # Scan trade files
        trades_dir = self.raw_data_dir / 'trades'
        if trades_dir.exists():
            for file_path in trades_dir.glob('*_trades.csv'):
                date = self._extract_date_from_filename(file_path.name)
                if date:
                    dates.add(date)
        
        return sorted(list(dates))
    
    def _extract_date_from_filename(self, filename: str) -> Optional[str]:
        """
        Extract date from filename like 'drr_20240905_orders.csv'
        
        Args:
            filename: Filename to parse
            
        Returns:
            Date string in YYYYMMDD format, or None if not found
        """
        # Pattern: {ticker}_{YYYYMMDD}_{orders|trades}.csv
        pattern = r'_(\d{8})_(?:orders|trades)\.csv$'
        match = re.search(pattern, filename)
        if match:
            return match.group(1)
        return None
    
    def _extract_ticker_from_filename(self, filename: str) -> Optional[str]:
        """
        Extract ticker from filename like 'drr_20240905_orders.csv'
        
        Args:
            filename: Filename to parse
            
        Returns:
            Ticker string, or None if not found
        """
        # Pattern: {ticker}_{YYYYMMDD}_{orders|trades}.csv
        pattern = r'^([a-zA-Z]+)_\d{8}_(?:orders|trades)\.csv$'
        match = re.match(pattern, filename)
        if match:
            return match.group(1)
        return None
    
    def _get_orderbookids_from_file(self, file_path: Path, 
                                    file_type: str) -> Dict[int, int]:
        """
        Extract unique orderbookids and their counts from a CSV file
        
        Args:
            file_path: Path to CSV file
            file_type: 'orders' or 'trades'
            
        Returns:
            Dict mapping orderbookid to count
        """
        try:
            # Read only first chunk to find orderbookids (for performance)
            # For orders, column is 'security_code'
            # For trades, column might be 'orderbookid' or 'security_code'
            
            # Try reading first 10000 rows to identify orderbookids
            df_sample = pd.read_csv(file_path, nrows=10000)
            
            # Find the orderbookid column
            orderbookid_col = None
            for col in ['security_code', 'securitycode', 'orderbookid', 'SecurityCode', 'OrderbookID']:
                if col in df_sample.columns:
                    orderbookid_col = col
                    break
            
            if orderbookid_col is None:
                logger.warning(f"Could not find orderbookid column in {file_path}")
                return {}
            
            # Read full file to get accurate counts
            logger.info(f"Reading {file_path.name} to count {file_type}...")
            df = pd.read_csv(file_path, usecols=[orderbookid_col])
            
            # Count occurrences of each orderbookid
            counts = df[orderbookid_col].value_counts().to_dict()
            
            return counts
            
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")
            return {}
    
    def discover_securities_for_date(self, date: str) -> List[SecurityInfo]:
        """
        Discover all securities available for a specific date
        
        Args:
            date: Date string in YYYYMMDD format
            
        Returns:
            List of SecurityInfo objects
        """
        securities = {}  # orderbookid -> SecurityInfo
        
        # Scan order files
        orders_dir = self.raw_data_dir / 'orders'
        if orders_dir.exists():
            for file_path in orders_dir.glob(f'*_{date}_orders.csv'):
                ticker = self._extract_ticker_from_filename(file_path.name)
                orderbookids = self._get_orderbookids_from_file(file_path, 'orders')
                
                for orderbookid, count in orderbookids.items():
                    if orderbookid not in securities:
                        securities[orderbookid] = SecurityInfo(
                            orderbookid=orderbookid,
                            ticker=ticker,
                            date=date,
                            in_orders=True,
                            in_trades=False,
                            order_count=count,
                            trade_count=0,
                            order_file=str(file_path),
                            trade_file=None
                        )
                    else:
                        securities[orderbookid].in_orders = True
                        securities[orderbookid].order_count = count
                        securities[orderbookid].order_file = str(file_path)
                        if not securities[orderbookid].ticker:
                            securities[orderbookid].ticker = ticker
        
        # Scan trade files
        trades_dir = self.raw_data_dir / 'trades'
        if trades_dir.exists():
            for file_path in trades_dir.glob(f'*_{date}_trades.csv'):
                ticker = self._extract_ticker_from_filename(file_path.name)
                orderbookids = self._get_orderbookids_from_file(file_path, 'trades')
                
                for orderbookid, count in orderbookids.items():
                    if orderbookid not in securities:
                        securities[orderbookid] = SecurityInfo(
                            orderbookid=orderbookid,
                            ticker=ticker,
                            date=date,
                            in_orders=False,
                            in_trades=True,
                            order_count=0,
                            trade_count=count,
                            order_file=None,
                            trade_file=str(file_path)
                        )
                    else:
                        securities[orderbookid].in_trades = True
                        securities[orderbookid].trade_count = count
                        securities[orderbookid].trade_file = str(file_path)
                        if not securities[orderbookid].ticker:
                            securities[orderbookid].ticker = ticker
        
        return list(securities.values())
    
    def get_valid_securities(self, date: str) -> List[SecurityInfo]:
        """
        Get all valid securities (meeting minimum thresholds) for a date
        
        Args:
            date: Date string in YYYYMMDD format
            
        Returns:
            List of SecurityInfo objects that meet thresholds
        """
        all_securities = self.discover_securities_for_date(date)
        
        valid_securities = []
        for sec in all_securities:
            # Must have both orders and trades
            if not (sec.in_orders and sec.in_trades):
                logger.info(f"Skipping {sec.orderbookid}: Not in both orders and trades")
                continue
            
            # Must meet minimum thresholds
            if sec.order_count < self.min_orders:
                logger.info(f"Skipping {sec.orderbookid}: Only {sec.order_count} orders (min: {self.min_orders})")
                continue
            
            if sec.trade_count < self.min_trades:
                logger.info(f"Skipping {sec.orderbookid}: Only {sec.trade_count} trades (min: {self.min_trades})")
                continue
            
            valid_securities.append(sec)
        
        return sorted(valid_securities, key=lambda x: x.orderbookid)
    
    def get_orderbookid_from_ticker(self, ticker: str, date: str) -> Optional[int]:
        """
        Get orderbookid for a given ticker and date
        
        Args:
            ticker: Ticker symbol (e.g., 'drr', 'bhp')
            date: Date string in YYYYMMDD format
            
        Returns:
            Orderbookid, or None if not found
        """
        securities = self.discover_securities_for_date(date)
        
        ticker_lower = ticker.lower()
        for sec in securities:
            if sec.ticker and sec.ticker.lower() == ticker_lower:
                return sec.orderbookid
        
        return None
    
    def print_summary(self, date: str):
        """
        Print a summary of discovered securities for a date
        
        Args:
            date: Date string in YYYYMMDD format
        """
        print(f"\n{'='*80}")
        print(f"Security Discovery Summary for {date}")
        print(f"{'='*80}")
        
        all_securities = self.discover_securities_for_date(date)
        valid_securities = self.get_valid_securities(date)
        
        print(f"\nTotal securities found: {len(all_securities)}")
        print(f"Valid securities (meeting thresholds): {len(valid_securities)}")
        print(f"  - Minimum orders: {self.min_orders}")
        print(f"  - Minimum trades: {self.min_trades}")
        
        if valid_securities:
            print(f"\n{'OrderbookID':<12} {'Ticker':<8} {'Orders':<12} {'Trades':<12} {'Status'}")
            print(f"{'-'*80}")
            for sec in valid_securities:
                ticker = sec.ticker.upper() if sec.ticker else "Unknown"
                status = "✓ Valid"
                print(f"{sec.orderbookid:<12} {ticker:<8} {sec.order_count:<12,} {sec.trade_count:<12,} {status}")
        
        # Show invalid securities
        invalid_securities = [s for s in all_securities if s not in valid_securities]
        if invalid_securities:
            print(f"\nInvalid securities (not meeting thresholds):")
            print(f"{'OrderbookID':<12} {'Ticker':<8} {'Orders':<12} {'Trades':<12} {'Reason'}")
            print(f"{'-'*80}")
            for sec in invalid_securities:
                ticker = sec.ticker.upper() if sec.ticker else "Unknown"
                reasons = []
                if not sec.in_orders:
                    reasons.append("No orders file")
                if not sec.in_trades:
                    reasons.append("No trades file")
                if sec.in_orders and sec.order_count < self.min_orders:
                    reasons.append(f"Orders < {self.min_orders}")
                if sec.in_trades and sec.trade_count < self.min_trades:
                    reasons.append(f"Trades < {self.min_trades}")
                
                reason = ", ".join(reasons)
                print(f"{sec.orderbookid:<12} {ticker:<8} {sec.order_count:<12,} {sec.trade_count:<12,} {reason}")
        
        print(f"\n{'='*80}\n")


def main():
    """Command-line interface for security discovery"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Discover available securities in raw data files'
    )
    parser.add_argument(
        '--date',
        type=str,
        help='Date in YYYYMMDD format (e.g., 20240905)'
    )
    parser.add_argument(
        '--list-dates',
        action='store_true',
        help='List all available dates'
    )
    parser.add_argument(
        '--min-orders',
        type=int,
        default=100,
        help='Minimum number of orders (default: 100)'
    )
    parser.add_argument(
        '--min-trades',
        type=int,
        default=10,
        help='Minimum number of trades (default: 10)'
    )
    
    args = parser.parse_args()
    
    discovery = SecurityDiscovery(
        min_orders=args.min_orders,
        min_trades=args.min_trades
    )
    
    if args.list_dates:
        dates = discovery.get_available_dates()
        print(f"\nAvailable dates in raw data:")
        for date in dates:
            print(f"  - {date}")
        print()
        return
    
    if args.date:
        discovery.print_summary(args.date)
    else:
        # Show all available dates
        dates = discovery.get_available_dates()
        if dates:
            print("\nAvailable dates:")
            for date in dates:
                print(f"  - {date}")
            print("\nUse --date YYYYMMDD to see securities for a specific date")
        else:
            print("No data files found in data/raw/")


if __name__ == '__main__':
    main()
