"""
NBBO Provider

Provides midpoint prices from NBBO data with fallback to order bid/offer.
"""

import pandas as pd


def get_midpoint(nbbo_data, timestamp, orderbookid, fallback_bid, fallback_offer):
    """
    Get midpoint price at given timestamp.
    
    Priority:
    1. NBBO file data (if available)
    2. Fallback to bid/offer from orders
    
    Args:
        nbbo_data: DataFrame from nbbo.csv (or None if not available), 
                   should be sorted by timestamp
        timestamp: Order timestamp (nanoseconds)
        orderbookid: Security code
        fallback_bid: Bid price from order (fallback)
        fallback_offer: Offer price from order (fallback)
    
    Returns:
        Midpoint price or None
    """
    
    # Try NBBO data first
    if nbbo_data is not None and len(nbbo_data) > 0:
        midpoint = _get_midpoint_from_nbbo(nbbo_data, timestamp, orderbookid)
        if midpoint is not None:
            return midpoint
    
    # Fallback to order bid/offer
    if fallback_bid is not None and fallback_offer is not None:
        if pd.notna(fallback_bid) and pd.notna(fallback_offer):
            return (fallback_bid + fallback_offer) / 2.0
    
    return None


def _get_midpoint_from_nbbo(nbbo_data, timestamp, orderbookid):
    """Get midpoint from NBBO data (most recent quote before timestamp)."""
    
    if nbbo_data is None:
        return None
    
    # Filter for this orderbookid and timestamp <= target
    valid_quotes = nbbo_data[
        (nbbo_data['orderbookid'] == orderbookid) &
        (nbbo_data['timestamp'] <= timestamp)
    ]
    
    if len(valid_quotes) == 0:
        return None
    
    # Get most recent quote
    latest = valid_quotes.iloc[-1]
    
    # Calculate midpoint
    midpoint = (latest['bidprice'] + latest['offerprice']) / 2.0
    
    return midpoint
