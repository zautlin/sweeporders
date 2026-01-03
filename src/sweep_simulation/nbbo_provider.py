"""
NBBO Provider

Provides midpoint prices from NBBO data with fallback to order bid/offer.
"""

import pandas as pd
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class NBBOProvider:
    """
    Provides midpoint prices from NBBO data.
    
    Priority:
    1. NBBO file (data/nbbo/nbbo.csv)
    2. Fallback to bid/offer from orders
    """
    
    def __init__(self, nbbo_data, orders_before):
        """
        Initialize NBBO provider.
        
        Args:
            nbbo_data: DataFrame from nbbo.csv (or None if not available)
            orders_before: DataFrame with orders (for fallback bid/offer)
        """
        self.orders_before = orders_before
        
        # Prepare NBBO lookup if available
        if nbbo_data is not None and len(nbbo_data) > 0:
            self.nbbo_data = nbbo_data.sort_values('timestamp').reset_index(drop=True)
            self.has_nbbo = True
            logger.info("  Using NBBO data from nbbo.csv")
        else:
            self.nbbo_data = None
            self.has_nbbo = False
            logger.info("  NBBO file not found, will use bid/offer from orders")
    
    def get_midpoint(self, timestamp, orderbookid, fallback_bid, fallback_offer):
        """
        Get midpoint price at given timestamp.
        
        Args:
            timestamp: Order timestamp (nanoseconds)
            orderbookid: Security code
            fallback_bid: Bid price from order (fallback)
            fallback_offer: Offer price from order (fallback)
        
        Returns:
            Midpoint price or None
        """
        
        # Try NBBO data first
        if self.has_nbbo:
            midpoint = self._get_midpoint_from_nbbo(timestamp, orderbookid)
            if midpoint is not None:
                return midpoint
        
        # Fallback to order bid/offer
        if fallback_bid is not None and fallback_offer is not None:
            if pd.notna(fallback_bid) and pd.notna(fallback_offer):
                return (fallback_bid + fallback_offer) / 2.0
        
        return None
    
    def _get_midpoint_from_nbbo(self, timestamp, orderbookid):
        """Get midpoint from NBBO data (most recent quote before timestamp)."""
        
        if self.nbbo_data is None:
            return None
        
        # Filter for this orderbookid and timestamp <= target
        valid_quotes = self.nbbo_data[
            (self.nbbo_data['orderbookid'] == orderbookid) &
            (self.nbbo_data['timestamp'] <= timestamp)
        ]
        
        if len(valid_quotes) == 0:
            return None
        
        # Get most recent quote
        latest = valid_quotes.iloc[-1]
        
        # Calculate midpoint
        midpoint = (latest['bidprice'] + latest['offerprice']) / 2.0
        
        return midpoint
