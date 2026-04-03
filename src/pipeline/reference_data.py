"""
Reference Data Module

Handles loading and using reference data for simulation:
- Tick size tables from order book reference data
- Participant information
- Session state information
- Price limits
"""

import pandas as pd
from pathlib import Path
from config.column_schema import col
import config.config as cfg


class ReferenceDataLoader:
    """Load and manage reference data for simulation."""
    
    def __init__(self, processed_dir):
        """Initialize reference data loader."""
        self.processed_dir = Path(processed_dir)
        self.tick_sizes = {}
        self.tick_size_tables = {}
        self.participants = {}
        self.sessions = {}
        self.price_limits = {}
        
    def load_participants(self):
        """Load participant reference data."""
        participants_file = self.processed_dir / 'participants.csv.gz'
        if not participants_file.exists():
            return None
        participants = pd.read_csv(participants_file)
        if 'Id' in participants.columns:
            self.participants = participants.set_index('Id').to_dict('index')
        return participants
    
    def get_participant_info(self, participant_id):
        """Get participant information."""
        return self.participants.get(participant_id)
    
    def get_participant_type(self, participant_id):
        """Get participant type (Broker, Market Maker, etc.)."""
        info = self.get_participant_info(participant_id)
        if info:
            return info.get('ParticipantType', 'Unknown')
        return 'Unknown'
    
    def load_tick_size_table(self, orderbookid):
        """Load tick size table from reference data."""
        if orderbookid in self.tick_size_tables:
            return self.tick_size_tables[orderbookid]
        
        reference_file = self.processed_dir / 'reference.csv.gz'
        if not reference_file.exists():
            self.tick_size_tables[orderbookid] = None
            return None
        
        reference = pd.read_csv(reference_file)
        orderbook_ref = reference[reference.get('OrderBookId', reference.get('orderbookid')) == orderbookid]
        
        if len(orderbook_ref) == 0:
            self.tick_size_tables[orderbookid] = None
            return None
        
        if 'TickSize' in orderbook_ref.columns:
            tick_size = orderbook_ref['TickSize'].iloc[0]
            if pd.notna(tick_size):
                tick_size_table = [{'lower_limit': 0, 'upper_limit': float('inf'), 'tick_size': int(tick_size)}]
                self.tick_size_tables[orderbookid] = tick_size_table
                return tick_size_table
        
        self.tick_size_tables[orderbookid] = None
        return None
    
    def get_tick_size_table(self, orderbookid):
        """Get tick size table for orderbook."""
        return self.load_tick_size_table(orderbookid)
    
    def get_tick_size_for_price(self, price, tick_size_table):
        """Get tick size for given price level."""
        if tick_size_table is None:
            return 10
        for ts in tick_size_table:
            if ts['lower_limit'] <= price <= ts['upper_limit']:
                return ts['tick_size']
        return tick_size_table[0]['tick_size'] if tick_size_table else 10
    
    def infer_tick_size_from_prices(self, prices):
        """Infer tick size from a list of prices."""
        if len(prices) < 2:
            return 10
        sorted_prices = sorted(set(prices))
        if len(sorted_prices) < 2:
            return 10
        diffs = [sorted_prices[i+1] - sorted_prices[i] for i in range(len(sorted_prices)-1)]
        min_diff = min(diffs)
        if all(d % min_diff == 0 for d in diffs):
            return min_diff
        return min_diff
    
    def load_tick_sizes_from_nbbo(self, partition_dir):
        """Load/estimate tick sizes from NBBO data."""
        nbbo_file = Path(partition_dir) / 'nbbo.csv.gz'
        if not nbbo_file.exists():
            return 10
        nbbo = pd.read_csv(nbbo_file)
        if len(nbbo) == 0:
            return 10
        if 'bid' in nbbo.columns and 'offer' in nbbo.columns:
            spreads = nbbo['offer'] - nbbo['bid']
            spreads = spreads[spreads > 0]
            if len(spreads) > 0:
                median_spread = spreads.median()
                tick_size = max(1, int(median_spread) // 2)
                return tick_size
        return 10
    
    def load_tick_sizes_from_orders(self, partition_dir):
        """Estimate tick size from order prices."""
        orders_file = Path(partition_dir) / 'orders_before_matching.csv'
        if not orders_file.exists():
            return 10
        orders = pd.read_csv(orders_file, nrows=1000)
        if 'price' not in orders.columns:
            return 10
        prices = orders['price'].dropna().unique()
        if len(prices) < 2:
            return 10
        return self.infer_tick_size_from_prices(prices)
    
    def get_tick_size(self, partition_dir, orderbookid=None):
        """Get tick size for a partition/orderbook."""
        if orderbookid and orderbookid in self.tick_sizes:
            return self.tick_sizes[orderbookid]
        
        tick_size = self.load_tick_sizes_from_nbbo(partition_dir)
        if tick_size == 10:
            tick_size = self.load_tick_sizes_from_orders(partition_dir)
        
        if orderbookid:
            self.tick_sizes[orderbookid] = tick_size
        return tick_size
    
    def load_price_limits(self, orderbookid):
        """Load price limits for orderbook."""
        if orderbookid in self.price_limits:
            return self.price_limits[orderbookid]
        
        reference_file = self.processed_dir / 'reference.csv.gz'
        if not reference_file.exists():
            self.price_limits[orderbookid] = None
            return None
        
        reference = pd.read_csv(reference_file)
        orderbook_ref = reference[reference.get('OrderBookId', reference.get('orderbookid')) == orderbookid]
        
        if len(orderbook_ref) == 0:
            self.price_limits[orderbookid] = None
            return None
        
        limits = {}
        if 'PriceLimitLower' in orderbook_ref.columns:
            limits['lower_limit'] = orderbook_ref['PriceLimitLower'].iloc[0]
        if 'PriceLimitUpper' in orderbook_ref.columns:
            limits['upper_limit'] = orderbook_ref['PriceLimitUpper'].iloc[0]
        if 'ReferencePrice' in orderbook_ref.columns:
            ref_price = orderbook_ref['ReferencePrice'].iloc[0]
            if pd.notna(ref_price):
                if 'lower_limit' not in limits:
                    limits['lower_limit'] = ref_price * 0.9
                if 'upper_limit' not in limits:
                    limits['upper_limit'] = ref_price * 1.1
        
        if limits:
            self.price_limits[orderbookid] = limits
            return limits
        
        self.price_limits[orderbookid] = None
        return None
    
    def get_price_limits(self, orderbookid):
        """Get price limits for orderbook (alias for load_price_limits)."""
        return self.load_price_limits(orderbookid)
    
    def load_all_reference_data(self, partition_dirs):
        """Load all reference data for multiple partitions."""
        reference_data = {}
        self.load_participants()
        
        for partition_dir in partition_dirs:
            partition_key = Path(partition_dir).name
            orderbookid = int(partition_key) if partition_key.isdigit() else None
            
            tick_size = self.get_tick_size(str(partition_dir), orderbookid)
            tick_size_table = self.load_tick_size_table(orderbookid)
            price_limits = self.load_price_limits(orderbookid)
            
            reference_data[partition_key] = {
                'tick_size': tick_size,
                'tick_size_table': tick_size_table,
                'price_limits': price_limits,
                'orderbookid': orderbookid,
            }
        
        return reference_data


def calculate_tick_size_from_spread(spread):
    """Calculate tick size from observed spread."""
    if spread <= 0:
        return 10
    tick_size = max(1, spread // 2)
    if tick_size <= 1:
        return 1
    elif tick_size <= 5:
        return 5
    else:
        return 10


_reference_loader = None


def get_reference_loader(processed_dir=None):
    """Get or create global reference data loader."""
    global _reference_loader
    if _reference_loader is None and processed_dir:
        _reference_loader = ReferenceDataLoader(processed_dir)
    return _reference_loader
