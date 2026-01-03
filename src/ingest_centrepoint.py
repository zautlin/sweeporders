"""
Integrated Centre Point Data Extraction Pipeline
Handles large files (100GB+) efficiently with chunked processing

This module provides a modular pipeline for:
1. Extracting Centre Point orders from orders.csv
2. Extracting matching trades from trades.csv
3. Aggregating trades by order
4. Calculating execution metrics
5. Extracting before/after matching states (partitioned by date/orderbookid)
6. Categorizing orders into groups

Design principles:
- Memory-efficient chunked processing
- Separation of concerns  
- Handles multiple dates and orderbookids
- Hierarchical output structure: ./data/{date}/{orderbookid}/
- Reusable components
- Clear data flow
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging
import time
from typing import Set, Dict, Tuple, Optional, List, Any
from dataclasses import dataclass, field
from datetime import datetime
from collections import defaultdict
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.columns import (
    INPUT_FILES, 
    OUTPUT_FILES, 
    CENTRE_POINT_ORDER_TYPES
)

# Import sweep simulation modules
from sweep_simulation import (
    OrderLoader, 
    SweepMatchingSimulator, 
    NBBOProvider,
    SimulatedMetricsCalculator,
    GroupComparator,
    ComparisonReporter
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# DATA TYPE DEFINITIONS
# ============================================================================

OPTIMAL_DTYPES_ORDERS = {
    'order_id': 'uint64',
    'timestamp': 'int64',
    'quantity': 'uint32',
    'leavesquantity': 'uint32',
    'price': 'float32',
    'participantid': 'uint32',
    'security_code': 'uint32',
    'side': 'int8',
    'exchangeordertype': 'int16',
    'orderstatus': 'int8',
    'totalmatchedquantity': 'uint32',
    'changereason': 'int8',
    'midtick': 'int8',
    'singlefillminimumquantity': 'uint32',
    'minimumquantity': 'uint32',
    'sequence': 'uint32',
}

OPTIMAL_DTYPES_TRADES = {
    'orderid': 'uint64',
    'tradetime': 'int64',
    'tradeprice': 'float32',
    'quantity': 'uint32',
    'securitycode': 'uint32',
    'participantid': 'uint32',
    'side': 'int8',
    'dealsource': 'int16',
}


# ============================================================================
# PIPELINE CONFIGURATION
# ============================================================================

@dataclass
class PipelineConfig:
    """Configuration for the Centre Point extraction pipeline."""
    output_dir: str = 'data'  # Base directory for hierarchical output
    chunk_size: int = 100000
    calculate_metrics: bool = True
    extract_before_after: bool = True
    categorize_groups: bool = True
    partition_by_date: bool = True  # Partition outputs by date
    partition_by_orderbookid: bool = True  # Partition outputs by orderbookid
    verbose: bool = True
    
    # Sweep simulation options
    run_sweep_simulation: bool = True
    calculate_simulated_metrics: bool = True
    compare_groups: bool = True
    generate_comparison_reports: bool = True


@dataclass
class PartitionInfo:
    """Information about a data partition (date + orderbookid)."""
    date_str: str  # YYYY-MM-DD format
    orderbookid: int
    order_count: int = 0
    trade_count: int = 0
    
    @property
    def partition_key(self) -> str:
        """Get unique partition key."""
        return f"{self.date_str}/{self.orderbookid}"
    
    def get_output_dir(self, base_dir: str) -> Path:
        """Get output directory for this partition."""
        return Path(base_dir) / self.date_str / str(self.orderbookid)


@dataclass
class PipelineResults:
    """Results from pipeline execution."""
    cp_orders: pd.DataFrame
    matched_trades: pd.DataFrame
    trades_agg: pd.DataFrame
    partitions: Dict[str, PartitionInfo] = field(default_factory=dict)
    orders_with_metrics: Optional[pd.DataFrame] = None
    before_after_by_partition: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]] = field(default_factory=dict)
    last_execution_by_partition: Dict[str, pd.DataFrame] = field(default_factory=dict)
    groups: Optional[Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]] = None
    execution_time: float = 0.0
    
    # Sweep simulation results
    sweep_simulations_by_partition: Dict[str, Any] = field(default_factory=dict)
    orders_with_simulated_metrics: Dict[str, pd.DataFrame] = field(default_factory=dict)
    group_comparisons: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

class DateExtractor:
    """Utility to extract dates from timestamps."""
    
    @staticmethod
    def timestamp_to_date(timestamp_ns: int) -> str:
        """
        Convert nanosecond timestamp to YYYY-MM-DD date string.
        
        Args:
            timestamp_ns: Timestamp in nanoseconds
            
        Returns:
            Date string in YYYY-MM-DD format
        """
        timestamp_sec = timestamp_ns / 1e9
        dt = datetime.fromtimestamp(timestamp_sec)
        return dt.strftime('%Y-%m-%d')
    
    @staticmethod
    def add_date_column(df: pd.DataFrame, timestamp_col: str = 'timestamp') -> pd.DataFrame:
        """
        Add date column to dataframe based on timestamp.
        
        Args:
            df: DataFrame with timestamp column
            timestamp_col: Name of timestamp column
            
        Returns:
            DataFrame with added 'date' column
        """
        if len(df) == 0:
            df['date'] = pd.Series(dtype='object')
            return df
        
        # Convert timestamps to dates (vectorized)
        df['date'] = pd.to_datetime(df[timestamp_col], unit='ns').dt.strftime('%Y-%m-%d')
        return df


class ProgressLogger:
    """Utility class for consistent progress logging."""
    
    @staticmethod
    def section(title: str):
        """Log a section header."""
        logger.info("\n" + "="*80)
        logger.info(title)
        logger.info("="*80)
    
    @staticmethod
    def subsection(title: str):
        """Log a subsection header."""
        logger.info("\n" + title)
    
    @staticmethod
    def metric(label: str, value, unit: str = ""):
        """Log a metric with consistent formatting."""
        if isinstance(value, int):
            logger.info(f"  {label}: {value:,}{unit}")
        elif isinstance(value, float):
            logger.info(f"  {label}: {value:,.2f}{unit}")
        else:
            logger.info(f"  {label}: {value}{unit}")
    
    @staticmethod
    def save_file(file_path: Path, row_count: int):
        """Log file save operation."""
        size_mb = file_path.stat().st_size / (1024 * 1024)
        logger.info(f"  Saved {row_count:,} rows ({size_mb:.2f} MB)")
        logger.info(f"  File: {file_path}")


class ChunkedProcessor:
    """Base class for chunked data processing."""
    
    def __init__(self, chunk_size: int = 100000):
        self.chunk_size = chunk_size
        self.total_rows_processed = 0
        self.start_time = None
    
    def start_processing(self):
        """Start timing the processing."""
        self.start_time = time.time()
        self.total_rows_processed = 0
    
    def get_stats(self) -> Dict:
        """Get processing statistics."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        return {
            'total_rows': self.total_rows_processed,
            'elapsed_time': elapsed,
            'rows_per_sec': self.total_rows_processed / elapsed if elapsed > 0 else 0
        }


class PartitionManager:
    """Manages data partitioning by date and orderbookid."""
    
    def __init__(self):
        self.partitions: Dict[str, PartitionInfo] = {}
    
    def register_partition(self, date_str: str, orderbookid: int) -> PartitionInfo:
        """Register or get existing partition."""
        key = f"{date_str}/{orderbookid}"
        if key not in self.partitions:
            self.partitions[key] = PartitionInfo(date_str=date_str, orderbookid=orderbookid)
        return self.partitions[key]
    
    def get_partitions_from_df(self, df: pd.DataFrame) -> List[PartitionInfo]:
        """Extract all unique partitions from dataframe."""
        if 'date' not in df.columns:
            df = DateExtractor.add_date_column(df)
        
        # Get unique date/orderbookid combinations
        unique_partitions = df[['date', 'security_code']].drop_duplicates()
        
        partitions = []
        for _, row in unique_partitions.iterrows():
            partition = self.register_partition(row['date'], row['security_code'])
            partitions.append(partition)
        
        return partitions
    
    def partition_dataframe(self, df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """
        Split dataframe into partitions by date and orderbookid.
        
        Args:
            df: DataFrame with 'date' and 'security_code' columns
            
        Returns:
            Dictionary mapping partition_key to DataFrame subset
        """
        if 'date' not in df.columns:
            df = DateExtractor.add_date_column(df)
        
        partitioned = {}
        for (date, orderbookid), group in df.groupby(['date', 'security_code']):
            key = f"{date}/{orderbookid}"
            partitioned[key] = group
            
            # Update partition info
            partition = self.register_partition(date, orderbookid)
            partition.order_count = len(group)
        
        return partitioned
    
    def log_summary(self):
        """Log summary of all partitions."""
        ProgressLogger.section("PARTITION SUMMARY")
        ProgressLogger.metric("Total partitions", len(self.partitions))
        
        # Group by date
        by_date = defaultdict(list)
        for partition in self.partitions.values():
            by_date[partition.date_str].append(partition)
        
        for date_str in sorted(by_date.keys()):
            partitions = by_date[date_str]
            total_orders = sum(p.order_count for p in partitions)
            total_trades = sum(p.trade_count for p in partitions)
            
            logger.info(f"\n  Date: {date_str}")
            logger.info(f"    Orderbookids: {len(partitions)}")
            logger.info(f"    Total orders: {total_orders:,}")
            logger.info(f"    Total trades: {total_trades:,}")
            
            for partition in sorted(partitions, key=lambda p: p.orderbookid):
                logger.info(f"      Orderbookid {partition.orderbookid}: "
                           f"{partition.order_count:,} orders, {partition.trade_count:,} trades")


# ============================================================================
# STEP 1: EXTRACT CENTRE POINT ORDERS
# ============================================================================

class CentrePointOrderExtractor(ChunkedProcessor):
    """Extract Centre Point orders from large order files."""
    
    def __init__(self, order_types: List[int], chunk_size: int = 100000):
        super().__init__(chunk_size)
        self.order_types = order_types
    
    def extract(self, input_file: str, output_file: str) -> pd.DataFrame:
        """
        Extract Centre Point orders using chunked processing.
        
        Args:
            input_file: Path to orders CSV file
            output_file: Path to save filtered orders
            
        Returns:
            DataFrame with Centre Point orders
        """
        ProgressLogger.section("STEP 1: EXTRACTING CENTRE POINT ORDERS")
        ProgressLogger.metric("Input file", Path(input_file).name)
        ProgressLogger.metric("Output file", Path(output_file).name)
        ProgressLogger.metric("Order types", self.order_types)
        ProgressLogger.metric("Chunk size", self.chunk_size)
        
        self.start_processing()
        cp_orders_list = []
        type_counts = {ot: 0 for ot in self.order_types}
        
        # Process file in chunks
        for chunk in pd.read_csv(
            input_file,
            chunksize=self.chunk_size,
            dtype=OPTIMAL_DTYPES_ORDERS,
            low_memory=False
        ):
            self.total_rows_processed += len(chunk)
            
            # Filter for Centre Point orders
            cp_chunk = chunk[chunk['exchangeordertype'].isin(self.order_types)].copy()
            
            if len(cp_chunk) > 0:
                # Add date column
                cp_chunk = DateExtractor.add_date_column(cp_chunk)
                cp_orders_list.append(cp_chunk)
                
                # Track counts by type
                for order_type in self.order_types:
                    type_counts[order_type] += (cp_chunk['exchangeordertype'] == order_type).sum()
        
        # Combine all chunks
        if not cp_orders_list:
            logger.warning("No Centre Point orders found!")
            return pd.DataFrame()
        
        cp_orders = pd.concat(cp_orders_list, ignore_index=True)
        
        # Log statistics
        stats = self.get_stats()
        ProgressLogger.subsection("Extraction Complete")
        ProgressLogger.metric("Total rows processed", stats['total_rows'])
        ProgressLogger.metric("Centre Point orders found", len(cp_orders))
        ProgressLogger.metric("Match rate", f"{len(cp_orders)/stats['total_rows']*100:.2f}%")
        ProgressLogger.metric("Processing rate", int(stats['rows_per_sec']), " rows/sec")
        
        # Log date/orderbookid distribution
        if 'date' in cp_orders.columns and 'security_code' in cp_orders.columns:
            unique_dates = cp_orders['date'].nunique()
            unique_orderbookids = cp_orders['security_code'].nunique()
            ProgressLogger.metric("Unique dates", unique_dates)
            ProgressLogger.metric("Unique orderbookids", unique_orderbookids)
            ProgressLogger.metric("Unique date/orderbookid combinations", 
                                 len(cp_orders.groupby(['date', 'security_code'])))
        
        ProgressLogger.subsection("Breakdown by Order Type")
        for order_type, count in type_counts.items():
            if count > 0:
                ProgressLogger.metric(f"Type {order_type}", count)
        
        # Save to disk
        ProgressLogger.subsection("Saving to disk")
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cp_orders.to_csv(output_path, index=False, compression='gzip')
        ProgressLogger.save_file(output_path, len(cp_orders))
        
        return cp_orders
    
    def extract_partitioned(self, input_file: str, base_output_dir: str) -> Dict[str, pd.DataFrame]:
        """
        Extract Centre Point orders and partition by date/security_code.
        
        Creates partitioned structure:
            {base_output_dir}/processed/{date}/{security_code}/centrepoint_orders_raw.csv.gz
        
        Args:
            input_file: Path to orders CSV file
            base_output_dir: Base directory for output files
            
        Returns:
            Dict mapping 'date/security_code' -> DataFrame
        """
        ProgressLogger.section("STEP 1: EXTRACTING CENTRE POINT ORDERS (PARTITIONED)")
        ProgressLogger.metric("Input file", Path(input_file).name)
        ProgressLogger.metric("Base output dir", base_output_dir)
        ProgressLogger.metric("Order types", self.order_types)
        ProgressLogger.metric("Chunk size", self.chunk_size)
        
        self.start_processing()
        cp_orders_list = []
        type_counts = {ot: 0 for ot in self.order_types}
        
        # Process file in chunks
        for chunk in pd.read_csv(
            input_file,
            chunksize=self.chunk_size,
            dtype=OPTIMAL_DTYPES_ORDERS,
            low_memory=False
        ):
            self.total_rows_processed += len(chunk)
            
            # Filter for Centre Point orders
            cp_chunk = chunk[chunk['exchangeordertype'].isin(self.order_types)].copy()
            
            if len(cp_chunk) > 0:
                # Add date column
                cp_chunk = DateExtractor.add_date_column(cp_chunk)
                cp_orders_list.append(cp_chunk)
                
                # Track counts by type
                for order_type in self.order_types:
                    type_counts[order_type] += (cp_chunk['exchangeordertype'] == order_type).sum()
        
        # Combine all chunks
        if not cp_orders_list:
            logger.warning("No Centre Point orders found!")
            return {}
        
        cp_orders = pd.concat(cp_orders_list, ignore_index=True)
        
        # Log statistics
        stats = self.get_stats()
        ProgressLogger.subsection("Extraction Complete")
        ProgressLogger.metric("Total rows processed", stats['total_rows'])
        ProgressLogger.metric("Centre Point orders found", len(cp_orders))
        ProgressLogger.metric("Match rate", f"{len(cp_orders)/stats['total_rows']*100:.2f}%")
        ProgressLogger.metric("Processing rate", int(stats['rows_per_sec']), " rows/sec")
        
        ProgressLogger.subsection("Breakdown by Order Type")
        for order_type, count in type_counts.items():
            if count > 0:
                ProgressLogger.metric(f"Type {order_type}", count)
        
        # Partition by date/security_code
        ProgressLogger.subsection("Partitioning by date/security_code")
        
        if 'date' not in cp_orders.columns or 'security_code' not in cp_orders.columns:
            raise ValueError("Orders must have 'date' and 'security_code' columns for partitioning")
        
        partitions = {}
        partition_groups = cp_orders.groupby(['date', 'security_code'])
        
        ProgressLogger.metric("Unique dates", cp_orders['date'].nunique())
        ProgressLogger.metric("Unique securities", cp_orders['security_code'].nunique())
        ProgressLogger.metric("Total partitions", len(partition_groups))
        
        # Save each partition
        for (date, security_code), group_df in partition_groups:
            partition_key = f"{date}/{security_code}"
            partitions[partition_key] = group_df
            
            # Save to partitioned location
            partition_dir = Path(base_output_dir) / "processed" / date / str(security_code)
            partition_dir.mkdir(parents=True, exist_ok=True)
            
            partition_file = partition_dir / "centrepoint_orders_raw.csv.gz"
            group_df.to_csv(partition_file, index=False, compression='gzip')
            
            size_mb = partition_file.stat().st_size / (1024 * 1024)
            logger.info(f"  {partition_key}: {len(group_df):,} orders ({size_mb:.2f} MB)")
        
        return partitions


# ============================================================================
# STEP 2: EXTRACT MATCHING TRADES
# ============================================================================

class MatchingTradesExtractor(ChunkedProcessor):
    """Extract trades that match specific order IDs."""
    
    def __init__(self, chunk_size: int = 100000):
        super().__init__(chunk_size)
    
    def extract(self, input_file: str, order_ids: Set[int], output_file: str) -> pd.DataFrame:
        """
        Extract trades matching given order IDs.
        
        Args:
            input_file: Path to trades CSV file
            order_ids: Set of order IDs to match
            output_file: Path to save matching trades
            
        Returns:
            DataFrame with matching trades
        """
        ProgressLogger.section("STEP 2: EXTRACTING MATCHING TRADES")
        ProgressLogger.metric("Input file", Path(input_file).name)
        ProgressLogger.metric("Output file", Path(output_file).name)
        ProgressLogger.metric("Order IDs to match", len(order_ids))
        ProgressLogger.metric("Chunk size", self.chunk_size)
        
        self.start_processing()
        matched_trades_list = []
        
        # Process file in chunks
        for chunk in pd.read_csv(
            input_file,
            chunksize=self.chunk_size,
            dtype=OPTIMAL_DTYPES_TRADES,
            low_memory=False
        ):
            self.total_rows_processed += len(chunk)
            
            # Filter for matching order IDs
            matched_chunk = chunk[chunk['orderid'].isin(order_ids)].copy()
            
            if len(matched_chunk) > 0:
                # Add date column based on tradetime
                matched_chunk = DateExtractor.add_date_column(matched_chunk, 'tradetime')
                matched_trades_list.append(matched_chunk)
        
        # Combine all chunks
        if not matched_trades_list:
            logger.warning("No matching trades found!")
            return pd.DataFrame()
        
        matched_trades = pd.concat(matched_trades_list, ignore_index=True)
        
        # Log statistics
        stats = self.get_stats()
        ProgressLogger.subsection("Extraction Complete")
        ProgressLogger.metric("Total rows processed", stats['total_rows'])
        ProgressLogger.metric("Matching trades found", len(matched_trades))
        ProgressLogger.metric("Match rate", f"{len(matched_trades)/stats['total_rows']*100:.2f}%")
        ProgressLogger.metric("Processing rate", int(stats['rows_per_sec']), " rows/sec")
        
        ProgressLogger.subsection("Trade Statistics")
        unique_orders = matched_trades['orderid'].nunique()
        ProgressLogger.metric("Unique orders with trades", unique_orders)
        ProgressLogger.metric("Avg trades per order", f"{len(matched_trades)/unique_orders:.2f}")
        ProgressLogger.metric("Total quantity traded", matched_trades['quantity'].sum())
        
        # Save to disk
        ProgressLogger.subsection("Saving to disk")
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        matched_trades.to_csv(output_path, index=False, compression='gzip')
        ProgressLogger.save_file(output_path, len(matched_trades))
        
        return matched_trades
    
    def extract_partitioned(
        self, 
        input_file: str, 
        orders_by_partition: Dict[str, pd.DataFrame], 
        base_output_dir: str
    ) -> Dict[str, pd.DataFrame]:
        """
        Extract trades and partition to match order partitions.
        
        For each partition (date/security_code), extracts only trades matching
        the order_ids in that partition.
        
        Creates partitioned structure:
            {base_output_dir}/processed/{date}/{security_code}/centrepoint_trades_raw.csv.gz
        
        Args:
            input_file: Path to trades CSV file
            orders_by_partition: Dict mapping 'date/security_code' -> orders DataFrame
            base_output_dir: Base directory for output files
            
        Returns:
            Dict mapping 'date/security_code' -> trades DataFrame
        """
        ProgressLogger.section("STEP 2: EXTRACTING MATCHING TRADES (PARTITIONED)")
        ProgressLogger.metric("Input file", Path(input_file).name)
        ProgressLogger.metric("Base output dir", base_output_dir)
        ProgressLogger.metric("Partitions to process", len(orders_by_partition))
        ProgressLogger.metric("Chunk size", self.chunk_size)
        
        # Collect all order IDs from all partitions
        all_order_ids = set()
        partition_order_ids = {}
        
        for partition_key, orders_df in orders_by_partition.items():
            order_ids = set(orders_df['order_id'].unique())
            partition_order_ids[partition_key] = order_ids
            all_order_ids.update(order_ids)
        
        ProgressLogger.metric("Total unique order IDs", len(all_order_ids))
        
        self.start_processing()
        all_trades_list = []
        
        # Process file in chunks and collect all matching trades
        for chunk in pd.read_csv(
            input_file,
            chunksize=self.chunk_size,
            dtype=OPTIMAL_DTYPES_TRADES,
            low_memory=False
        ):
            self.total_rows_processed += len(chunk)
            
            # Filter for matching order IDs (any partition)
            matched_chunk = chunk[chunk['orderid'].isin(all_order_ids)].copy()
            
            if len(matched_chunk) > 0:
                # Add date column based on tradetime
                matched_chunk = DateExtractor.add_date_column(matched_chunk, 'tradetime')
                all_trades_list.append(matched_chunk)
        
        # Combine all chunks
        if not all_trades_list:
            logger.warning("No matching trades found!")
            return {}
        
        all_trades = pd.concat(all_trades_list, ignore_index=True)
        
        # Log statistics
        stats = self.get_stats()
        ProgressLogger.subsection("Extraction Complete")
        ProgressLogger.metric("Total rows processed", stats['total_rows'])
        ProgressLogger.metric("Matching trades found", len(all_trades))
        ProgressLogger.metric("Match rate", f"{len(all_trades)/stats['total_rows']*100:.2f}%")
        ProgressLogger.metric("Processing rate", int(stats['rows_per_sec']), " rows/sec")
        
        # Partition trades to match order partitions
        ProgressLogger.subsection("Partitioning trades by date/security_code")
        
        trades_by_partition = {}
        
        for partition_key, order_ids in partition_order_ids.items():
            # Extract trades for this partition's orders
            partition_trades = all_trades[all_trades['orderid'].isin(order_ids)].copy()
            
            if len(partition_trades) > 0:
                trades_by_partition[partition_key] = partition_trades
                
                # Save to partitioned location
                date, security_code = partition_key.split('/')
                partition_dir = Path(base_output_dir) / "processed" / date / security_code
                partition_dir.mkdir(parents=True, exist_ok=True)
                
                partition_file = partition_dir / "centrepoint_trades_raw.csv.gz"
                partition_trades.to_csv(partition_file, index=False, compression='gzip')
                
                size_mb = partition_file.stat().st_size / (1024 * 1024)
                unique_orders = partition_trades['orderid'].nunique()
                logger.info(f"  {partition_key}: {len(partition_trades):,} trades, "
                          f"{unique_orders:,} orders ({size_mb:.2f} MB)")
            else:
                logger.warning(f"  {partition_key}: No trades found")
        
        return trades_by_partition


# ============================================================================
# STEP 3: AGGREGATE TRADES BY ORDER
# ============================================================================

class TradeAggregator:
    """Aggregate trade data by order ID."""
    
    @staticmethod
    def aggregate(trades: pd.DataFrame, output_file: str) -> pd.DataFrame:
        """
        Aggregate trades by order ID.
        
        Args:
            trades: DataFrame with trade data
            output_file: Path to save aggregated data
            
        Returns:
            DataFrame with aggregated trade statistics per order
        """
        ProgressLogger.section("STEP 3: AGGREGATING TRADES BY ORDER")
        
        if len(trades) == 0:
            logger.warning("No trades to aggregate!")
            return pd.DataFrame()
        
        # Aggregate by order ID
        agg_dict = {
            'quantity': 'sum',                    # Total filled quantity
            'tradeprice': 'mean',                 # Average execution price
            'tradetime': ['min', 'max', 'count']  # First/last trade time, trade count
        }
        
        trades_agg = trades.groupby('orderid').agg(agg_dict).reset_index()
        
        # Flatten column names
        trades_agg.columns = [
            'orderid', 
            'total_quantity_filled',
            'avg_execution_price',
            'first_trade_time',
            'last_trade_time',
            'num_trades'
        ]
        
        # Calculate execution duration (in seconds)
        trades_agg['execution_duration_sec'] = (
            (trades_agg['last_trade_time'] - trades_agg['first_trade_time']) / 1e9
        )
        
        # Log statistics
        ProgressLogger.subsection("Aggregation Complete")
        ProgressLogger.metric("Orders with trades", len(trades_agg))
        ProgressLogger.metric("Total quantity filled", trades_agg['total_quantity_filled'].sum())
        ProgressLogger.metric("Avg execution price", f"${trades_agg['avg_execution_price'].mean():.4f}")
        ProgressLogger.metric("Avg trades per order", f"{trades_agg['num_trades'].mean():.2f}")
        
        # Save to disk
        ProgressLogger.subsection("Saving to disk")
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        trades_agg.to_csv(output_path, index=False, compression='gzip')
        ProgressLogger.save_file(output_path, len(trades_agg))
        
        return trades_agg
    
    @staticmethod
    def aggregate_partitioned(
        trades_by_partition: Dict[str, pd.DataFrame],
        base_output_dir: str
    ) -> Dict[str, pd.DataFrame]:
        """
        Aggregate trades per partition.
        
        For each partition (date/security_code), aggregates trades by order_id.
        
        Creates partitioned structure:
            {base_output_dir}/processed/{date}/{security_code}/centrepoint_trades_agg.csv.gz
        
        Args:
            trades_by_partition: Dict mapping 'date/security_code' -> trades DataFrame
            base_output_dir: Base directory for output files
            
        Returns:
            Dict mapping 'date/security_code' -> aggregated trades DataFrame
        """
        ProgressLogger.section("STEP 3: AGGREGATING TRADES BY ORDER (PARTITIONED)")
        ProgressLogger.metric("Partitions to process", len(trades_by_partition))
        
        trades_agg_by_partition = {}
        all_agg_list = []
        
        for partition_key, trades_df in trades_by_partition.items():
            if len(trades_df) == 0:
                logger.warning(f"  {partition_key}: No trades to aggregate")
                continue
            
            # Aggregate by order ID
            agg_dict = {
                'quantity': 'sum',                    # Total filled quantity
                'tradeprice': 'mean',                 # Average execution price
                'tradetime': ['min', 'max', 'count']  # First/last trade time, trade count
            }
            
            trades_agg = trades_df.groupby('orderid').agg(agg_dict).reset_index()
            
            # Flatten column names
            trades_agg.columns = [
                'orderid', 
                'total_quantity_filled',
                'avg_execution_price',
                'first_trade_time',
                'last_trade_time',
                'num_trades'
            ]
            
            # Calculate execution duration (in seconds)
            trades_agg['execution_duration_sec'] = (
                (trades_agg['last_trade_time'] - trades_agg['first_trade_time']) / 1e9
            )
            
            trades_agg_by_partition[partition_key] = trades_agg
            all_agg_list.append(trades_agg)
            
            # Save to partitioned location
            date, security_code = partition_key.split('/')
            partition_dir = Path(base_output_dir) / "processed" / date / security_code
            partition_dir.mkdir(parents=True, exist_ok=True)
            
            partition_file = partition_dir / "centrepoint_trades_agg.csv.gz"
            trades_agg.to_csv(partition_file, index=False, compression='gzip')
            
            size_mb = partition_file.stat().st_size / (1024 * 1024)
            logger.info(f"  {partition_key}: {len(trades_agg):,} orders with trades ({size_mb:.2f} MB)")
        
        # Log overall statistics
        ProgressLogger.subsection("Aggregation Complete")
        total_orders = sum(len(agg) for agg in trades_agg_by_partition.values())
        ProgressLogger.metric("Total orders with trades", total_orders)
        
        if all_agg_list:
            all_agg = pd.concat(all_agg_list, ignore_index=True)
            ProgressLogger.metric("Total quantity filled", all_agg['total_quantity_filled'].sum())
            ProgressLogger.metric("Avg execution price", f"${all_agg['avg_execution_price'].mean():.4f}")
            ProgressLogger.metric("Avg trades per order", f"{all_agg['num_trades'].mean():.2f}")
        
        return trades_agg_by_partition


# ============================================================================
# REFERENCE DATA EXTRACTORS (PARTITIONED)
# ============================================================================

class NBBOExtractor:
    """Extract and partition NBBO data by date/security."""
    
    @staticmethod
    def extract_partitioned(
        input_file: str,
        base_output_dir: str,
        partitions: List[str]
    ) -> Dict[str, pd.DataFrame]:
        """
        Extract NBBO data and partition by date/security_code.
        
        Creates partitioned structure:
            {base_output_dir}/processed/{date}/{security_code}/nbbo.csv.gz
        
        Args:
            input_file: Path to NBBO CSV file
            base_output_dir: Base directory for output files
            partitions: List of partition keys ('date/security_code')
            
        Returns:
            Dict mapping 'date/security_code' -> NBBO DataFrame
        """
        ProgressLogger.section("STEP 0A: EXTRACTING NBBO DATA (PARTITIONED)")
        ProgressLogger.metric("Input file", Path(input_file).name)
        ProgressLogger.metric("Target partitions", len(partitions))
        
        # Read NBBO file
        nbbo_df = pd.read_csv(input_file)
        ProgressLogger.metric("Total NBBO records", len(nbbo_df))
        
        # Add date column if not present
        if 'date' not in nbbo_df.columns and 'tradedate' in nbbo_df.columns:
            nbbo_df = DateExtractor.add_date_column(nbbo_df, 'tradedate')
        
        # Rename orderbookid to security_code if needed
        if 'orderbookid' in nbbo_df.columns and 'security_code' not in nbbo_df.columns:
            nbbo_df = nbbo_df.rename(columns={'orderbookid': 'security_code'})
        
        nbbo_by_partition = {}
        
        for partition_key in partitions:
            date, security_code = partition_key.split('/')
            security_code_int = int(security_code)
            
            # Filter NBBO for this partition
            partition_nbbo = nbbo_df[
                (nbbo_df['date'] == date) & 
                (nbbo_df['security_code'] == security_code_int)
            ].copy()
            
            if len(partition_nbbo) > 0:
                nbbo_by_partition[partition_key] = partition_nbbo
                
                # Save to partitioned location
                partition_dir = Path(base_output_dir) / "processed" / date / security_code
                partition_dir.mkdir(parents=True, exist_ok=True)
                
                partition_file = partition_dir / "nbbo.csv.gz"
                partition_nbbo.to_csv(partition_file, index=False, compression='gzip')
                
                size_mb = partition_file.stat().st_size / (1024 * 1024)
                logger.info(f"  {partition_key}: {len(partition_nbbo):,} records ({size_mb:.2f} MB)")
            else:
                logger.warning(f"  {partition_key}: No NBBO data found")
        
        ProgressLogger.subsection("NBBO Extraction Complete")
        ProgressLogger.metric("Partitions with NBBO data", len(nbbo_by_partition))
        
        return nbbo_by_partition


class SessionExtractor:
    """Extract and partition session data by date."""
    
    @staticmethod
    def extract_partitioned(
        input_file: str,
        base_output_dir: str,
        dates: List[str]
    ) -> Dict[str, pd.DataFrame]:
        """
        Extract session data and partition by date.
        
        Creates date-level structure:
            {base_output_dir}/processed/{date}/session.csv.gz
        
        Args:
            input_file: Path to session CSV file
            base_output_dir: Base directory for output files
            dates: List of dates to extract
            
        Returns:
            Dict mapping date -> session DataFrame
        """
        ProgressLogger.section("STEP 0B: EXTRACTING SESSION DATA (PARTITIONED)")
        ProgressLogger.metric("Input file", Path(input_file).name)
        ProgressLogger.metric("Target dates", len(dates))
        
        # Read session file
        session_df = pd.read_csv(input_file)
        ProgressLogger.metric("Total session records", len(session_df))
        
        # Add date column if not present
        if 'date' not in session_df.columns and 'TradeDate' in session_df.columns:
            session_df = DateExtractor.add_date_column(session_df, 'TradeDate')
        
        session_by_date = {}
        
        for date in dates:
            # Filter session for this date
            date_session = session_df[session_df['date'] == date].copy()
            
            if len(date_session) > 0:
                session_by_date[date] = date_session
                
                # Save to date-level location
                date_dir = Path(base_output_dir) / "processed" / date
                date_dir.mkdir(parents=True, exist_ok=True)
                
                date_file = date_dir / "session.csv.gz"
                date_session.to_csv(date_file, index=False, compression='gzip')
                
                size_mb = date_file.stat().st_size / (1024 * 1024)
                logger.info(f"  {date}: {len(date_session):,} session records ({size_mb:.2f} MB)")
            else:
                logger.warning(f"  {date}: No session data found")
        
        ProgressLogger.subsection("Session Extraction Complete")
        ProgressLogger.metric("Dates with session data", len(session_by_date))
        
        return session_by_date


class ReferenceExtractor:
    """Extract and partition reference data by date."""
    
    @staticmethod
    def extract_partitioned(
        input_file: str,
        base_output_dir: str,
        dates: List[str]
    ) -> Dict[str, pd.DataFrame]:
        """
        Extract reference data and partition by date.
        
        Creates date-level structure:
            {base_output_dir}/processed/{date}/reference.csv.gz
        
        Args:
            input_file: Path to reference CSV file
            base_output_dir: Base directory for output files
            dates: List of dates to extract
            
        Returns:
            Dict mapping date -> reference DataFrame
        """
        ProgressLogger.section("STEP 0C: EXTRACTING REFERENCE DATA (PARTITIONED)")
        ProgressLogger.metric("Input file", Path(input_file).name)
        ProgressLogger.metric("Target dates", len(dates))
        
        # Read reference file
        ref_df = pd.read_csv(input_file)
        ProgressLogger.metric("Total reference records", len(ref_df))
        
        # Add date column if not present
        if 'date' not in ref_df.columns and 'TradeDate' in ref_df.columns:
            ref_df = DateExtractor.add_date_column(ref_df, 'TradeDate')
        
        ref_by_date = {}
        
        for date in dates:
            # Filter reference for this date
            date_ref = ref_df[ref_df['date'] == date].copy()
            
            if len(date_ref) > 0:
                ref_by_date[date] = date_ref
                
                # Save to date-level location
                date_dir = Path(base_output_dir) / "processed" / date
                date_dir.mkdir(parents=True, exist_ok=True)
                
                date_file = date_dir / "reference.csv.gz"
                date_ref.to_csv(date_file, index=False, compression='gzip')
                
                size_mb = date_file.stat().st_size / (1024 * 1024)
                logger.info(f"  {date}: {len(date_ref):,} reference records ({size_mb:.2f} MB)")
            else:
                logger.warning(f"  {date}: No reference data found")
        
        ProgressLogger.subsection("Reference Extraction Complete")
        ProgressLogger.metric("Dates with reference data", len(ref_by_date))
        
        return ref_by_date


class ParticipantsExtractor:
    """Extract and partition participants data by date."""
    
    @staticmethod
    def extract_partitioned(
        input_file: str,
        base_output_dir: str,
        dates: List[str]
    ) -> Dict[str, pd.DataFrame]:
        """
        Extract participants data and partition by date.
        
        Creates date-level structure:
            {base_output_dir}/processed/{date}/participants.csv.gz
        
        Args:
            input_file: Path to participants CSV file
            base_output_dir: Base directory for output files
            dates: List of dates to extract
            
        Returns:
            Dict mapping date -> participants DataFrame
        """
        ProgressLogger.section("STEP 0D: EXTRACTING PARTICIPANTS DATA (PARTITIONED)")
        ProgressLogger.metric("Input file", Path(input_file).name)
        ProgressLogger.metric("Target dates", len(dates))
        
        # Read participants file
        par_df = pd.read_csv(input_file)
        ProgressLogger.metric("Total participant records", len(par_df))
        
        # Add date column if not present
        if 'date' not in par_df.columns and 'TradeDate' in par_df.columns:
            par_df = DateExtractor.add_date_column(par_df, 'TradeDate')
        
        par_by_date = {}
        
        for date in dates:
            # Filter participants for this date
            date_par = par_df[par_df['date'] == date].copy()
            
            if len(date_par) > 0:
                par_by_date[date] = date_par
                
                # Save to date-level location
                date_dir = Path(base_output_dir) / "processed" / date
                date_dir.mkdir(parents=True, exist_ok=True)
                
                date_file = date_dir / "participants.csv.gz"
                date_par.to_csv(date_file, index=False, compression='gzip')
                
                size_mb = date_file.stat().st_size / (1024 * 1024)
                logger.info(f"  {date}: {len(date_par):,} participant records ({size_mb:.2f} MB)")
            else:
                logger.warning(f"  {date}: No participant data found")
        
        ProgressLogger.subsection("Participants Extraction Complete")
        ProgressLogger.metric("Dates with participant data", len(par_by_date))
        
        return par_by_date


# ============================================================================
# STEP 3B: EXTRACT LAST EXECUTION TIME (PARTITIONED)
# ============================================================================

class LastExecutionTimeExtractor:
    """Extract first and last execution time for each order, partitioned by security_code."""
    
    @staticmethod
    def extract_partitioned(
        trades: pd.DataFrame,
        base_output_dir: str,
        partition_manager: PartitionManager
    ) -> Dict[str, pd.DataFrame]:
        """
        Extract first (min) and last (max) execution timestamps for each order, partitioned by security_code.
        
        Saves to: base_output_dir/{date}/{security_code}/last_execution_time.csv
        
        Args:
            trades: DataFrame with trade data (must have 'orderid', 'tradetime', 'securitycode')
            base_output_dir: Base directory for output
            partition_manager: PartitionManager instance
            
        Returns:
            Dictionary mapping partition_key to DataFrame with execution times
        """
        ProgressLogger.section("STEP 3B: EXTRACTING EXECUTION TIMES (PARTITIONED)")
        ProgressLogger.metric("Total trades", len(trades))
        
        if len(trades) == 0:
            logger.warning("No trades to process!")
            return {}
        
        # Ensure date column exists
        if 'date' not in trades.columns:
            trades = DateExtractor.add_date_column(trades, 'tradetime')
        
        # Get min and max timestamp per order
        ProgressLogger.subsection("Calculating first and last execution timestamps per order")
        execution_times = trades.groupby('orderid').agg({
            'tradetime': ['min', 'max'],
            'securitycode': 'first',  # Security code should be same for all trades of an order
            'date': 'first'  # Use first date (could also be last, should be same)
        }).reset_index()
        
        # Flatten column names
        execution_times.columns = ['orderid', 'first_execution_time', 'last_execution_time', 'security_code', 'date']
        
        ProgressLogger.metric("Orders with executions", len(execution_times))
        
        # Partition by date and security_code
        ProgressLogger.subsection("Partitioning by date and security_code")
        partitioned_results = {}
        
        for (date, security_code), group in execution_times.groupby(['date', 'security_code']):
            partition_key = f"{date}/{security_code}"
            
            # Register partition if not exists
            partition_info = partition_manager.register_partition(date, security_code)
            partition_info.trade_count = len(group)
            
            # Get output directory
            output_dir = partition_info.get_output_dir(base_output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Save to CSV with all three columns
            output_path = output_dir / 'last_execution_time.csv'
            group[['orderid', 'first_execution_time', 'last_execution_time']].to_csv(output_path, index=False)
            
            partitioned_results[partition_key] = group
            
            logger.info(f"  Partition: {partition_key}")
            logger.info(f"    Orders: {len(group):,}")
            logger.info(f"    Saved to: {output_path}")
        
        # Log summary
        ProgressLogger.subsection("Execution Time Summary")
        ProgressLogger.metric("Total partitions", len(partitioned_results))
        ProgressLogger.metric("Total orders with executions", len(execution_times))
        
        # Show time range statistics
        earliest_start = execution_times['first_execution_time'].min()
        latest_end = execution_times['last_execution_time'].max()
        
        earliest_dt = datetime.fromtimestamp(earliest_start / 1e9)
        latest_dt = datetime.fromtimestamp(latest_end / 1e9)
        
        logger.info(f"  Earliest first execution: {earliest_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"  Latest last execution:    {latest_dt.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Calculate execution duration statistics
        execution_times['execution_duration_sec'] = (
            execution_times['last_execution_time'] - execution_times['first_execution_time']
        ) / 1e9
        
        avg_duration = execution_times['execution_duration_sec'].mean()
        max_duration = execution_times['execution_duration_sec'].max()
        single_trade_orders = (execution_times['execution_duration_sec'] == 0).sum()
        
        logger.info(f"  Avg execution duration:   {avg_duration:.2f}s")
        logger.info(f"  Max execution duration:   {max_duration:.2f}s")
        logger.info(f"  Orders with single trade: {single_trade_orders:,} ({single_trade_orders/len(execution_times)*100:.1f}%)")
        
        return partitioned_results


# ============================================================================
# STEP 4: CALCULATE ORDER METRICS
# ============================================================================

class OrderMetricsCalculator:
    """Calculate execution metrics for orders."""
    
    @staticmethod
    def calculate(orders: pd.DataFrame, trades_agg: pd.DataFrame, output_file: str) -> pd.DataFrame:
        """
        Calculate execution metrics for orders.
        
        Args:
            orders: DataFrame with order data
            trades_agg: DataFrame with aggregated trade data
            output_file: Path to save orders with metrics
            
        Returns:
            DataFrame with orders and calculated metrics
        """
        ProgressLogger.section("STEP 4: CALCULATING ORDER METRICS")
        
        # Merge orders with trade aggregations
        orders_with_metrics = orders.merge(
            trades_agg,
            left_on='order_id',
            right_on='orderid',
            how='left'
        )
        
        # Fill NaN values for orders without trades
        orders_with_metrics['total_quantity_filled'] = orders_with_metrics['total_quantity_filled'].fillna(0)
        orders_with_metrics['num_trades'] = orders_with_metrics['num_trades'].fillna(0)
        
        # Calculate fill ratio
        orders_with_metrics['fill_ratio'] = (
            orders_with_metrics['total_quantity_filled'] / orders_with_metrics['quantity']
        ).clip(0, 1)
        
        # Categorize fill status
        orders_with_metrics['fill_status'] = 'unfilled'
        orders_with_metrics.loc[orders_with_metrics['fill_ratio'] >= 0.99, 'fill_status'] = 'full'
        orders_with_metrics.loc[
            (orders_with_metrics['fill_ratio'] > 0) & (orders_with_metrics['fill_ratio'] < 0.99),
            'fill_status'
        ] = 'partial'
        
        # Calculate slippage (if price data available)
        has_price = orders_with_metrics['avg_execution_price'].notna()
        if has_price.any():
            orders_with_metrics['slippage_bps'] = (
                (orders_with_metrics['avg_execution_price'] - orders_with_metrics['price']) / 
                orders_with_metrics['price'] * 10000
            ).abs()
        
        # Categorize execution speed
        orders_with_metrics['execution_speed'] = 'incomplete'
        orders_with_metrics.loc[
            orders_with_metrics['execution_duration_sec'] < 1,
            'execution_speed'
        ] = 'immediate'
        orders_with_metrics.loc[
            orders_with_metrics['execution_duration_sec'] >= 1,
            'execution_speed'
        ] = 'eventual'
        
        # Calculate remaining quantity
        orders_with_metrics['remaining_quantity'] = (
            orders_with_metrics['quantity'] - orders_with_metrics['total_quantity_filled']
        )
        
        # Log statistics
        OrderMetricsCalculator._log_metrics(orders_with_metrics)
        
        # Save to disk
        ProgressLogger.subsection("Saving to disk")
        output_path = Path(output_file)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        orders_with_metrics.to_csv(output_path, index=False, compression='gzip')
        ProgressLogger.save_file(output_path, len(orders_with_metrics))
        
        return orders_with_metrics
    
    @staticmethod
    def _log_metrics(df: pd.DataFrame):
        """Log detailed metrics statistics."""
        ProgressLogger.subsection("METRICS SUMMARY")
        
        # Fill status distribution
        ProgressLogger.subsection("Fill Status Distribution")
        for status in ['full', 'partial', 'unfilled']:
            count = (df['fill_status'] == status).sum()
            if count > 0:
                label = f"{status.capitalize()} fills"
                if status == 'full':
                    label += " (≥99%)"
                ProgressLogger.metric(label, count)
        
        # Execution speed
        ProgressLogger.subsection("Execution Speed (for filled orders)")
        for speed in ['immediate', 'eventual', 'incomplete']:
            count = (df['execution_speed'] == speed).sum()
            if count > 0:
                label = speed.capitalize()
                if speed == 'immediate':
                    label += " (<1s)"
                elif speed == 'eventual':
                    label += " (≥1s)"
                ProgressLogger.metric(label, count)
        
        # Quantity metrics
        ProgressLogger.subsection("Quantity Metrics")
        ProgressLogger.metric("Total order quantity", df['quantity'].sum())
        ProgressLogger.metric("Total filled quantity", df['total_quantity_filled'].sum())
        total_qty = df['quantity'].sum()
        if total_qty > 0:
            ProgressLogger.metric("Overall fill ratio", f"{df['total_quantity_filled'].sum()/total_qty*100:.2f}%")
        
        # Execution quality (filled orders only)
        filled = df[df['fill_status'] != 'unfilled']
        if len(filled) > 0:
            ProgressLogger.subsection("Execution Quality (filled orders only)")
            ProgressLogger.metric("Avg fill ratio", f"{filled['fill_ratio'].mean()*100:.2f}%")
            if 'execution_duration_sec' in filled.columns:
                ProgressLogger.metric("Avg execution time", f"{filled['execution_duration_sec'].mean():.2f}s")
            if 'slippage_bps' in filled.columns:
                ProgressLogger.metric("Avg slippage", f"{filled['slippage_bps'].mean():.2f} bps")
            ProgressLogger.metric("Avg trades per order", f"{filled['num_trades'].mean():.2f}")
        
        # By order type
        if 'exchangeordertype' in df.columns:
            ProgressLogger.subsection("By Order Type")
            for order_type in df['exchangeordertype'].unique():
                type_df = df[df['exchangeordertype'] == order_type]
                full = (type_df['fill_status'] == 'full').sum()
                partial = (type_df['fill_status'] == 'partial').sum()
                unfilled = (type_df['fill_status'] == 'unfilled').sum()
                avg_fill = type_df['fill_ratio'].mean() * 100
                
                logger.info(f"  Type {order_type}: {len(type_df):,} orders | "
                           f"Full: {full:,} | Partial: {partial:,} | Unfilled: {unfilled:,} | "
                           f"Avg Fill: {avg_fill:.2f}%")


# ============================================================================
# STEP 5: EXTRACT BEFORE/AFTER LOB STATES (PARTITIONED)
# ============================================================================

class BeforeAfterLOBExtractor:
    """Extract initial and final states of orders, partitioned by date/orderbookid."""
    
    @staticmethod
    def extract_partitioned(
        orders: pd.DataFrame, 
        base_output_dir: str,
        partition_manager: PartitionManager
    ) -> Dict[str, Tuple[pd.DataFrame, pd.DataFrame]]:
        """
        Extract before (initial) and after (final) states of each order,
        partitioned by date and orderbookid.
        
        Args:
            orders: DataFrame with all order events
            base_output_dir: Base directory for output (e.g., './data')
            partition_manager: PartitionManager instance
            
        Returns:
            Dictionary mapping partition_key to (orders_before, orders_after) tuples
        """
        ProgressLogger.section("STEP 5: EXTRACTING BEFORE/AFTER LOB STATES (PARTITIONED)")
        ProgressLogger.metric("Total order events", len(orders))
        
        # Ensure date column exists
        if 'date' not in orders.columns:
            orders = DateExtractor.add_date_column(orders)
        
        # Partition the data
        ProgressLogger.subsection("Partitioning by date and orderbookid")
        partitioned_orders = partition_manager.partition_dataframe(orders)
        ProgressLogger.metric("Total partitions", len(partitioned_orders))
        
        # Process each partition
        results = {}
        for partition_key, partition_df in partitioned_orders.items():
            partition_info = partition_manager.partitions[partition_key]
            
            logger.info(f"\n  Processing partition: {partition_key}")
            logger.info(f"    Orders: {len(partition_df):,}")
            
            # Extract before/after for this partition
            orders_before, orders_after = BeforeAfterLOBExtractor._extract_for_partition(
                partition_df, 
                partition_info,
                base_output_dir
            )
            
            results[partition_key] = (orders_before, orders_after)
        
        # Log summary
        BeforeAfterLOBExtractor._log_summary(results, partition_manager)
        
        return results
    
    @staticmethod
    def _extract_for_partition(
        orders: pd.DataFrame,
        partition_info: PartitionInfo,
        base_output_dir: str
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """Extract before/after states for a single partition."""
        
        # Sort chronologically
        orders_sorted = orders.sort_values(['order_id', 'timestamp', 'sequence'])
        
        # Extract initial state (first event per order)
        orders_before = orders_sorted.groupby('order_id').head(1).copy()
        orders_before['lob_state'] = 'BEFORE'
        
        # Extract final state (last event per order)
        orders_after = orders_sorted.groupby('order_id').tail(1).copy()
        orders_after['lob_state'] = 'AFTER'
        
        # Save to hierarchical directory structure: ./data/{date}/{orderbookid}/
        output_dir = partition_info.get_output_dir(base_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        before_path = output_dir / 'orders_before_lob.csv'
        orders_before.to_csv(before_path, index=False)
        logger.info(f"      Saved BEFORE: {before_path} ({len(orders_before):,} orders)")
        
        after_path = output_dir / 'orders_after_lob.csv'
        orders_after.to_csv(after_path, index=False)
        logger.info(f"      Saved AFTER:  {after_path} ({len(orders_after):,} orders)")
        
        return orders_before, orders_after
    
    @staticmethod
    def _log_summary(
        results: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]],
        partition_manager: PartitionManager
    ):
        """Log summary of before/after extraction."""
        ProgressLogger.section("BEFORE/AFTER LOB EXTRACTION SUMMARY")
        
        total_unique_orders = 0
        total_changed = 0
        total_unchanged = 0
        
        for partition_key, (before_df, after_df) in results.items():
            # Merge for comparison
            comparison = before_df[['order_id', 'totalmatchedquantity']].merge(
                after_df[['order_id', 'totalmatchedquantity']],
                on='order_id',
                suffixes=('_before', '_after')
            )
            
            changed = (comparison['totalmatchedquantity_after'] > comparison['totalmatchedquantity_before']).sum()
            unchanged = len(comparison) - changed
            
            total_unique_orders += len(comparison)
            total_changed += changed
            total_unchanged += unchanged
        
        ProgressLogger.metric("Total unique orders processed", total_unique_orders)
        ProgressLogger.metric("Orders that changed state", f"{total_changed:,} ({total_changed/total_unique_orders*100:.2f}%)")
        ProgressLogger.metric("Orders unchanged", f"{total_unchanged:,} ({total_unchanged/total_unique_orders*100:.2f}%)")
        
        # Log partition details
        partition_manager.log_summary()


# ============================================================================
# STEP 6: CATEGORIZE ORDERS INTO GROUPS
# ============================================================================

class OrderCategorizer:
    """Categorize orders into execution groups."""
    
    @staticmethod
    def categorize(orders: pd.DataFrame, output_dir: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Categorize orders into 3 groups based on final state.
        
        Groups:
        - Group 1 (Fully Filled): leavesquantity == 0
        - Group 2 (Partially Filled): leavesquantity > 0 AND totalmatchedquantity > 0
        - Group 3 (Not Executed): leavesquantity > 0 AND totalmatchedquantity == 0
        
        Args:
            orders: DataFrame with order data
            output_dir: Directory to save group files
            
        Returns:
            Tuple of (group1, group2, group3)
        """
        ProgressLogger.section("STEP 6: CATEGORIZING ORDERS INTO GROUPS")
        
        ProgressLogger.subsection("Grouping logic")
        logger.info("  Group 1 (Fully Filled): leavesquantity == 0")
        logger.info("  Group 2 (Partially Filled): leavesquantity > 0 AND totalmatchedquantity > 0")
        logger.info("  Group 3 (Not Executed): leavesquantity > 0 AND totalmatchedquantity == 0")
        
        # Get final state of each order
        ProgressLogger.subsection("Finding final state of each order (max timestamp + max sequence)")
        orders_sorted = orders.sort_values(['order_id', 'timestamp', 'sequence'])
        final_states = orders_sorted.groupby('order_id').tail(1).copy()
        
        ProgressLogger.metric("Final states found", len(final_states))
        
        # Categorize
        group1 = final_states[final_states['leavesquantity'] == 0].copy()
        group1['sweep_group'] = 'GROUP_1_FULLY_FILLED'
        
        group2 = final_states[
            (final_states['leavesquantity'] > 0) & 
            (final_states['totalmatchedquantity'] > 0)
        ].copy()
        group2['sweep_group'] = 'GROUP_2_PARTIALLY_FILLED'
        
        group3 = final_states[
            (final_states['leavesquantity'] > 0) & 
            (final_states['totalmatchedquantity'] == 0)
        ].copy()
        group3['sweep_group'] = 'GROUP_3_NOT_EXECUTED'
        
        # Verify completeness
        total_categorized = len(group1) + len(group2) + len(group3)
        if total_categorized != len(final_states):
            logger.warning(f"Warning: {len(final_states) - total_categorized} orders not categorized!")
        
        # Save files
        ProgressLogger.subsection("Saving groups")
        
        files = [
            ('sweep_orders_group1_fully_filled.csv.gz', group1, 'Fully Filled'),
            ('sweep_orders_group2_partially_filled.csv.gz', group2, 'Partially Filled'),
            ('sweep_orders_group3_not_executed.csv.gz', group3, 'Not Executed')
        ]
        
        for filename, group_df, label in files:
            output_path = Path(output_dir) / filename
            output_path.parent.mkdir(parents=True, exist_ok=True)
            group_df.to_csv(output_path, index=False, compression='gzip')
            size_mb = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"  {label}: {len(group_df):,} orders ({size_mb:.2f} MB)")
            logger.info(f"    Saved to: {filename}")
        
        # Log statistics
        OrderCategorizer._log_group_statistics(group1, group2, group3, final_states, orders)
        
        return group1, group2, group3
    
    @staticmethod
    def _log_group_statistics(group1, group2, group3, final_states, all_orders):
        """Log detailed group statistics."""
        ProgressLogger.section("GROUP STATISTICS")
        
        # Group 1 statistics
        if len(group1) > 0:
            ProgressLogger.subsection(f"Group 1 (Fully Filled): {len(group1):,} orders")
            ProgressLogger.metric("Total quantity", group1['quantity'].sum())
            ProgressLogger.metric("Total matched", group1['totalmatchedquantity'].sum())
            ProgressLogger.metric("Avg quantity per order", int(group1['quantity'].mean()))
            ProgressLogger.metric("Fill rate", "100.00%")
        
        # Group 2 statistics
        if len(group2) > 0:
            ProgressLogger.subsection(f"Group 2 (Partially Filled): {len(group2):,} orders")
            ProgressLogger.metric("Total quantity", group2['quantity'].sum())
            ProgressLogger.metric("Total matched", group2['totalmatchedquantity'].sum())
            ProgressLogger.metric("Total leaves", group2['leavesquantity'].sum())
            avg_fill = (group2['totalmatchedquantity'] / group2['quantity']).mean() * 100
            ProgressLogger.metric("Avg fill rate", f"{avg_fill:.2f}%")
        
        # Group 3 statistics
        if len(group3) > 0:
            ProgressLogger.subsection(f"Group 3 (Not Executed): {len(group3):,} orders")
            ProgressLogger.metric("Total quantity", group3['quantity'].sum())
            ProgressLogger.metric("Total matched", group3['totalmatchedquantity'].sum())
            ProgressLogger.metric("Total leaves", group3['leavesquantity'].sum())
            ProgressLogger.metric("Fill rate", "0.00%")
        
        # Overall distribution
        ProgressLogger.subsection("Overall Distribution")
        total = len(final_states)
        if total > 0:
            logger.info(f"  Fully Filled:     {len(group1):>10,} ({len(group1)/total*100:>5.2f}%)")
            logger.info(f"  Partially Filled: {len(group2):>10,} ({len(group2)/total*100:>5.2f}%)")
            logger.info(f"  Not Executed:     {len(group3):>10,} ({len(group3)/total*100:>5.2f}%)")
            logger.info(f"  Total:            {total:>10,}")
        
        # By order type
        if 'exchangeordertype' in all_orders.columns:
            ProgressLogger.subsection("By Order Type")
            for order_type in CENTRE_POINT_ORDER_TYPES:
                g1_count = len(group1[group1['exchangeordertype'] == order_type])
                g2_count = len(group2[group2['exchangeordertype'] == order_type])
                g3_count = len(group3[group3['exchangeordertype'] == order_type])
                total_type = g1_count + g2_count + g3_count
                
                if total_type > 0:
                    logger.info(f"  Type {order_type}: {total_type:>6,} orders | "
                               f"Full: {g1_count:>5,} | Partial: {g2_count:>5,} | None: {g3_count:>5,}")


# ============================================================================
# MAIN PIPELINE
# ============================================================================

class CentrePointPipeline:
    """Main pipeline orchestrator for Centre Point data extraction."""
    
    def __init__(self, config: PipelineConfig):
        self.config = config
        self.start_time = None
        self.partition_manager = PartitionManager()
    
    def run(self) -> PipelineResults:
        """
        Execute the complete pipeline.
        
        Returns:
            PipelineResults with all extracted data
        """
        self.start_time = time.time()
        
        # Create output directory
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        
        ProgressLogger.section("CENTRE POINT DATA EXTRACTION PIPELINE")
        ProgressLogger.metric("Output directory", self.config.output_dir)
        ProgressLogger.metric("Chunk size", self.config.chunk_size)
        ProgressLogger.metric("Partition by date", self.config.partition_by_date)
        ProgressLogger.metric("Partition by orderbookid", self.config.partition_by_orderbookid)
        
        # Step 1: Extract Centre Point orders
        extractor = CentrePointOrderExtractor(
            order_types=CENTRE_POINT_ORDER_TYPES,
            chunk_size=self.config.chunk_size
        )
        cp_orders = extractor.extract(
            input_file=INPUT_FILES['orders'],
            output_file=str(Path(self.config.output_dir) / OUTPUT_FILES['centrepoint_orders_raw'])
        )
        
        if len(cp_orders) == 0:
            logger.error("No Centre Point orders found. Exiting.")
            return PipelineResults(
                cp_orders=pd.DataFrame(),
                matched_trades=pd.DataFrame(),
                trades_agg=pd.DataFrame(),
                execution_time=time.time() - self.start_time
            )
        
        # Step 2: Extract matching trades
        trades_extractor = MatchingTradesExtractor(chunk_size=self.config.chunk_size)
        cp_order_ids = set(cp_orders['order_id'].unique())
        matched_trades = trades_extractor.extract(
            input_file=INPUT_FILES['trades'],
            order_ids=cp_order_ids,
            output_file=str(Path(self.config.output_dir) / OUTPUT_FILES['centrepoint_trades_raw'])
        )
        
        # Step 3: Aggregate trades
        if len(matched_trades) == 0:
            logger.warning("No matching trades found.")
            trades_agg = pd.DataFrame()
            last_execution_by_partition = {}
            orders_with_metrics = None
        else:
            trades_agg = TradeAggregator.aggregate(
                trades=matched_trades,
                output_file=str(Path(self.config.output_dir) / OUTPUT_FILES['centrepoint_trades_agg'])
            )
            
            # Step 3B: Extract last execution time (partitioned)
            last_execution_by_partition = LastExecutionTimeExtractor.extract_partitioned(
                trades=matched_trades,
                base_output_dir=self.config.output_dir,
                partition_manager=self.partition_manager
            )
            
            # Step 4: Calculate metrics (optional)
            if self.config.calculate_metrics:
                orders_with_metrics = OrderMetricsCalculator.calculate(
                    orders=cp_orders,
                    trades_agg=trades_agg,
                    output_file=str(Path(self.config.output_dir) / 'centrepoint_orders_with_metrics.csv.gz')
                )
            else:
                orders_with_metrics = None
        
        # Step 5: Extract before/after LOB states (optional, partitioned)
        if self.config.extract_before_after:
            before_after_by_partition = BeforeAfterLOBExtractor.extract_partitioned(
                orders=cp_orders,
                base_output_dir=self.config.output_dir,
                partition_manager=self.partition_manager
            )
        else:
            before_after_by_partition = {}
        
        # Step 6: Categorize into groups (optional)
        if self.config.categorize_groups:
            group1, group2, group3 = OrderCategorizer.categorize(
                orders=cp_orders,
                output_dir=self.config.output_dir
            )
            groups = (group1, group2, group3)
        else:
            groups = None
        
        # Step 7: Run Sweep Matching Simulation (optional, partitioned)
        sweep_simulations_by_partition = {}
        if self.config.run_sweep_simulation and before_after_by_partition and last_execution_by_partition:
            sweep_simulations_by_partition = self._run_sweep_simulation(
                before_after_by_partition,
                last_execution_by_partition
            )
        
        # Step 8: Calculate Simulated Metrics (optional, partitioned)
        orders_with_simulated_metrics = {}
        if self.config.calculate_simulated_metrics and sweep_simulations_by_partition:
            orders_with_simulated_metrics = self._calculate_simulated_metrics(
                sweep_simulations_by_partition,
                cp_orders
            )
        
        # Step 9: Compare Groups (optional, partitioned)
        group_comparisons = {}
        if self.config.compare_groups and orders_with_simulated_metrics and groups:
            group_comparisons = self._compare_groups(
                orders_with_simulated_metrics,
                groups
            )
        
        # Step 10: Generate Comparison Reports (optional, partitioned)
        if self.config.generate_comparison_reports and group_comparisons:
            self._generate_comparison_reports(group_comparisons)
        
        # Log final summary
        execution_time = time.time() - self.start_time
        self._log_summary(
            cp_orders, matched_trades, trades_agg, orders_with_metrics,
            before_after_by_partition, last_execution_by_partition, groups, 
            sweep_simulations_by_partition, orders_with_simulated_metrics, 
            group_comparisons, execution_time
        )
        
        return PipelineResults(
            cp_orders=cp_orders,
            matched_trades=matched_trades,
            trades_agg=trades_agg,
            partitions=self.partition_manager.partitions,
            orders_with_metrics=orders_with_metrics,
            before_after_by_partition=before_after_by_partition,
            last_execution_by_partition=last_execution_by_partition,
            groups=groups,
            sweep_simulations_by_partition=sweep_simulations_by_partition,
            orders_with_simulated_metrics=orders_with_simulated_metrics,
            group_comparisons=group_comparisons,
            execution_time=execution_time
        )
    
    def run_partitioned(self) -> PipelineResults:
        """
        Execute the complete pipeline with full partitioning from the start.
        
        This method partitions raw data (orders, trades, NBBO, etc.) immediately
        and maintains the partitioned structure throughout the pipeline.
        
        Returns:
            PipelineResults with all extracted data
        """
        self.start_time = time.time()
        
        # Create output directory
        Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        
        ProgressLogger.section("CENTRE POINT DATA EXTRACTION PIPELINE (FULLY PARTITIONED)")
        ProgressLogger.metric("Output directory", self.config.output_dir)
        ProgressLogger.metric("Chunk size", self.config.chunk_size)
        
        # Step 1: Extract Centre Point orders (partitioned)
        extractor = CentrePointOrderExtractor(
            order_types=CENTRE_POINT_ORDER_TYPES,
            chunk_size=self.config.chunk_size
        )
        cp_orders_by_partition = extractor.extract_partitioned(
            input_file=INPUT_FILES['orders'],
            base_output_dir=self.config.output_dir
        )
        
        if not cp_orders_by_partition:
            logger.error("No Centre Point orders found. Exiting.")
            return PipelineResults(
                cp_orders=pd.DataFrame(),
                matched_trades=pd.DataFrame(),
                trades_agg=pd.DataFrame(),
                execution_time=time.time() - self.start_time
            )
        
        # Combine all partitions for global operations
        cp_orders = pd.concat(cp_orders_by_partition.values(), ignore_index=True)
        
        # Get partition keys and unique dates
        partition_keys = list(cp_orders_by_partition.keys())
        unique_dates = sorted(set(pk.split('/')[0] for pk in partition_keys))
        
        ProgressLogger.metric("Total partitions", len(partition_keys))
        ProgressLogger.metric("Unique dates", len(unique_dates))
        
        # Step 0A: Extract NBBO data (partitioned by date/security)
        if Path(INPUT_FILES['nbbo']).exists():
            nbbo_by_partition = NBBOExtractor.extract_partitioned(
                input_file=INPUT_FILES['nbbo'],
                base_output_dir=self.config.output_dir,
                partitions=partition_keys
            )
        else:
            logger.warning("NBBO file not found, skipping NBBO extraction")
            nbbo_by_partition = {}
        
        # Step 0B: Extract Session data (partitioned by date)
        if Path(INPUT_FILES['session']).exists():
            session_by_date = SessionExtractor.extract_partitioned(
                input_file=INPUT_FILES['session'],
                base_output_dir=self.config.output_dir,
                dates=unique_dates
            )
        else:
            logger.warning("Session file not found, skipping session extraction")
            session_by_date = {}
        
        # Step 0C: Extract Reference data (partitioned by date)
        if Path(INPUT_FILES['reference']).exists():
            reference_by_date = ReferenceExtractor.extract_partitioned(
                input_file=INPUT_FILES['reference'],
                base_output_dir=self.config.output_dir,
                dates=unique_dates
            )
        else:
            logger.warning("Reference file not found, skipping reference extraction")
            reference_by_date = {}
        
        # Step 0D: Extract Participants data (partitioned by date)
        if Path(INPUT_FILES['participants']).exists():
            participants_by_date = ParticipantsExtractor.extract_partitioned(
                input_file=INPUT_FILES['participants'],
                base_output_dir=self.config.output_dir,
                dates=unique_dates
            )
        else:
            logger.warning("Participants file not found, skipping participants extraction")
            participants_by_date = {}
        
        # Step 2: Extract matching trades (partitioned)
        trades_extractor = MatchingTradesExtractor(chunk_size=self.config.chunk_size)
        trades_by_partition = trades_extractor.extract_partitioned(
            input_file=INPUT_FILES['trades'],
            orders_by_partition=cp_orders_by_partition,
            base_output_dir=self.config.output_dir
        )
        
        matched_trades = pd.concat(trades_by_partition.values(), ignore_index=True) if trades_by_partition else pd.DataFrame()
        
        # Step 3: Aggregate trades (partitioned)
        if trades_by_partition:
            trades_agg_by_partition = TradeAggregator.aggregate_partitioned(
                trades_by_partition=trades_by_partition,
                base_output_dir=self.config.output_dir
            )
            trades_agg = pd.concat(trades_agg_by_partition.values(), ignore_index=True)
            
            # Step 3B: Extract last execution time (partitioned)
            last_execution_by_partition = LastExecutionTimeExtractor.extract_partitioned(
                trades=matched_trades,
                base_output_dir=self.config.output_dir,
                partition_manager=self.partition_manager
            )
            
            # Step 4: Skip metrics calculation (not needed in partitioned pipeline)
            # Metrics were combined for all orders, but partitioned data doesn't require
            # a separate global metrics file
            orders_with_metrics = None
        else:
            logger.warning("No matching trades found.")
            trades_agg_by_partition = {}
            trades_agg = pd.DataFrame()
            last_execution_by_partition = {}
            orders_with_metrics = None
        
        # Step 5: Extract before/after LOB states (optional, partitioned)
        if self.config.extract_before_after:
            before_after_by_partition = BeforeAfterLOBExtractor.extract_partitioned(
                orders=cp_orders,
                base_output_dir=self.config.output_dir,
                partition_manager=self.partition_manager
            )
        else:
            before_after_by_partition = {}
        
        # Step 6: Skip group categorization (not needed in partitioned pipeline)
        # Groups were only used for global classification, but partitioned data
        # doesn't require separate group files at root level
        groups = None
        
        # Step 7: Run Sweep Matching Simulation (optional, partitioned)
        sweep_simulations_by_partition = {}
        if self.config.run_sweep_simulation and before_after_by_partition and last_execution_by_partition:
            sweep_simulations_by_partition = self._run_sweep_simulation(
                before_after_by_partition,
                last_execution_by_partition
            )
        
        # Step 8: Calculate Simulated Metrics (optional, partitioned)
        orders_with_simulated_metrics = {}
        if self.config.calculate_simulated_metrics and sweep_simulations_by_partition:
            orders_with_simulated_metrics = self._calculate_simulated_metrics(
                sweep_simulations_by_partition,
                cp_orders
            )
        
        # Step 9: Compare Groups (optional, partitioned)
        group_comparisons = {}
        if self.config.compare_groups and orders_with_simulated_metrics and groups:
            group_comparisons = self._compare_groups(
                orders_with_simulated_metrics,
                groups
            )
        
        # Step 10: Generate Comparison Reports (optional, partitioned)
        if self.config.generate_comparison_reports and group_comparisons:
            self._generate_comparison_reports(group_comparisons)
        
        # Log final summary
        execution_time = time.time() - self.start_time
        self._log_summary(
            cp_orders, matched_trades, trades_agg, orders_with_metrics,
            before_after_by_partition, last_execution_by_partition, groups, 
            sweep_simulations_by_partition, orders_with_simulated_metrics, 
            group_comparisons, execution_time
        )
        
        return PipelineResults(
            cp_orders=cp_orders,
            matched_trades=matched_trades,
            trades_agg=trades_agg,
            partitions=self.partition_manager.partitions,
            orders_with_metrics=orders_with_metrics,
            before_after_by_partition=before_after_by_partition,
            last_execution_by_partition=last_execution_by_partition,
            groups=groups,
            sweep_simulations_by_partition=sweep_simulations_by_partition,
            orders_with_simulated_metrics=orders_with_simulated_metrics,
            group_comparisons=group_comparisons,
            execution_time=execution_time
        )
    
    def _run_sweep_simulation(
        self,
        before_after_by_partition: Dict[str, Tuple[pd.DataFrame, pd.DataFrame]],
        last_execution_by_partition: Dict[str, pd.DataFrame]
    ) -> Dict[str, Dict[str, pd.DataFrame]]:
        """Step 7: Run sweep matching simulation for each partition."""
        ProgressLogger.section("STEP 7: SWEEP MATCHING SIMULATION")
        
        sweep_simulations = {}
        
        for partition_key in before_after_by_partition.keys():
            if partition_key not in last_execution_by_partition:
                logger.warning(f"No last execution data for partition {partition_key}, skipping simulation")
                continue
            
            logger.info(f"Running sweep simulation for partition {partition_key}")
            
            # Get partition data
            orders_before, orders_after = before_after_by_partition[partition_key]
            last_execution = last_execution_by_partition[partition_key]
            
            # Prepare partition data
            partition_data = {
                'orders_before': orders_before,
                'orders_after': orders_after,
                'last_execution': last_execution
            }
            
            # Load orders
            loader = OrderLoader(partition_key, partition_data)
            sweep_orders, incoming_orders = loader.load_orders()
            
            # Get output directory for this partition
            partition_parts = partition_key.split('/')
            partition_dir = Path(self.config.output_dir) / partition_parts[0] / partition_parts[1]
            
            # Load NBBO data if available
            nbbo_file = Path(self.config.output_dir) / 'nbbo' / 'nbbo.csv'
            nbbo_data = None
            if nbbo_file.exists():
                try:
                    nbbo_data = pd.read_csv(nbbo_file)
                    logger.debug(f"Loaded NBBO data: {len(nbbo_data)} rows")
                except Exception as e:
                    logger.warning(f"Failed to load NBBO file: {e}")
            
            # Create NBBO provider
            nbbo_provider = NBBOProvider(nbbo_data, orders_before)
            
            # Run simulation
            simulator = SweepMatchingSimulator(nbbo_provider)
            simulation_results = simulator.simulate_partition(
                partition_key,
                sweep_orders,
                incoming_orders
            )
            
            # Save simulation results to partition directory
            if not simulation_results['match_details'].empty:
                simulation_results['match_details'].to_csv(
                    partition_dir / 'sweep_match_details.csv',
                    index=False
                )
            
            simulation_results['order_summary'].to_csv(
                partition_dir / 'sweep_match_summary.csv',
                index=False
            )
            
            simulation_results['sweep_utilization'].to_csv(
                partition_dir / 'sweep_utilization.csv',
                index=False
            )
            
            sweep_simulations[partition_key] = simulation_results
            
            logger.info(f"Simulation complete for {partition_key}: "
                       f"{len(simulation_results['match_details'])} matches generated")
        
        ProgressLogger.metric("Partitions simulated", len(sweep_simulations))
        return sweep_simulations
    
    def _calculate_simulated_metrics(
        self,
        sweep_simulations: Dict[str, Dict[str, pd.DataFrame]],
        cp_orders: pd.DataFrame
    ) -> Dict[str, pd.DataFrame]:
        """Step 8: Calculate simulated execution metrics for each partition."""
        ProgressLogger.section("STEP 8: CALCULATE SIMULATED METRICS")
        
        orders_with_simulated_metrics = {}
        calculator = SimulatedMetricsCalculator()
        
        for partition_key, simulation_results in sweep_simulations.items():
            logger.info(f"Calculating simulated metrics for partition {partition_key}")
            
            # Get orders for this partition
            partition_parts = partition_key.split('/')
            date_str = partition_parts[0]
            orderbookid = int(partition_parts[1])
            
            # Filter cp_orders for this partition (use security_code column)
            partition_orders = cp_orders[
                (cp_orders['date'] == date_str) &
                (cp_orders['security_code'] == orderbookid)
            ].copy()
            
            # Calculate simulated metrics
            orders_with_metrics = calculator.calculate(
                partition_orders,
                simulation_results['order_summary'],
                simulation_results['match_details']
            )
            
            # Save to partition directory
            partition_dir = Path(self.config.output_dir) / date_str / str(orderbookid)
            orders_with_metrics.to_csv(
                partition_dir / 'orders_with_simulated_metrics.csv',
                index=False
            )
            
            orders_with_simulated_metrics[partition_key] = orders_with_metrics
            
            logger.info(f"Simulated metrics calculated for {partition_key}: "
                       f"{len(orders_with_metrics)} orders")
        
        ProgressLogger.metric("Partitions with metrics", len(orders_with_simulated_metrics))
        return orders_with_simulated_metrics
    
    def _compare_groups(
        self,
        orders_with_simulated_metrics: Dict[str, pd.DataFrame],
        groups: Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
    ) -> Dict[str, Dict[str, Any]]:
        """Step 9: Compare real vs simulated execution by group for each partition."""
        ProgressLogger.section("STEP 9: COMPARE REAL VS SIMULATED BY GROUP")
        
        group_comparisons = {}
        comparator = GroupComparator()
        
        # Create groups dict for comparator
        groups_dict = {
            'Group 1 (Fully Filled)': groups[0],
            'Group 2 (Partially Filled)': groups[1],
            'Group 3 (Not Executed)': groups[2]
        }
        
        for partition_key, orders_with_metrics in orders_with_simulated_metrics.items():
            logger.info(f"Comparing groups for partition {partition_key}")
            
            # Run comparison
            comparison_data = comparator.compare_by_group(
                orders_with_metrics,
                groups_dict
            )
            
            group_comparisons[partition_key] = comparison_data
            
            logger.info(f"Group comparison complete for {partition_key}")
        
        ProgressLogger.metric("Partitions compared", len(group_comparisons))
        return group_comparisons
    
    def _generate_comparison_reports(
        self,
        group_comparisons: Dict[str, Dict[str, Any]]
    ):
        """Step 10: Generate comparison reports for each partition."""
        ProgressLogger.section("STEP 10: GENERATE COMPARISON REPORTS")
        
        for partition_key, comparison_data in group_comparisons.items():
            logger.info(f"Generating reports for partition {partition_key}")
            
            # Get output directory for this partition
            partition_parts = partition_key.split('/')
            partition_dir = Path(self.config.output_dir) / partition_parts[0] / partition_parts[1]
            
            # Create reporter
            reporter = ComparisonReporter(partition_dir)
            
            # Generate reports
            report_files = reporter.generate_reports(partition_key, comparison_data)
            
            logger.info(f"Generated {len(report_files)} reports for {partition_key}")
        
        ProgressLogger.metric("Report sets generated", len(group_comparisons))
    
    def _log_summary(self, cp_orders, matched_trades, trades_agg, orders_with_metrics,
                     before_after_by_partition, last_execution_by_partition, groups,
                     sweep_simulations, orders_with_simulated_metrics, group_comparisons,
                     execution_time):
        """Log pipeline execution summary."""
        ProgressLogger.section("PIPELINE COMPLETE")
        ProgressLogger.metric("Total execution time", f"{execution_time:.2f}s ({execution_time/60:.2f} min)")
        
        ProgressLogger.subsection("Results")
        ProgressLogger.metric("Centre Point orders", len(cp_orders))
        ProgressLogger.metric("Matching trades", len(matched_trades))
        ProgressLogger.metric("Orders with trades", len(trades_agg))
        
        if orders_with_metrics is not None:
            ProgressLogger.metric("Orders with metrics", len(orders_with_metrics))
        
        if before_after_by_partition:
            total_before = sum(len(ba[0]) for ba in before_after_by_partition.values())
            total_after = sum(len(ba[1]) for ba in before_after_by_partition.values())
            ProgressLogger.metric("Partitions created", len(before_after_by_partition))
            ProgressLogger.metric("Total orders before LOB", total_before)
            ProgressLogger.metric("Total orders after LOB", total_after)
        
        if last_execution_by_partition:
            total_orders = sum(len(df) for df in last_execution_by_partition.values())
            ProgressLogger.metric("Last execution time partitions", len(last_execution_by_partition))
            ProgressLogger.metric("Total orders with last execution", total_orders)
        
        if groups is not None:
            ProgressLogger.metric("Group 1 (Fully Filled)", len(groups[0]))
            ProgressLogger.metric("Group 2 (Partially Filled)", len(groups[1]))
            ProgressLogger.metric("Group 3 (Not Executed)", len(groups[2]))
        
        if sweep_simulations:
            total_matches = sum(len(sim['match_details']) for sim in sweep_simulations.values())
            ProgressLogger.metric("Sweep simulations run", len(sweep_simulations))
            ProgressLogger.metric("Total simulated matches", total_matches)
        
        if orders_with_simulated_metrics:
            ProgressLogger.metric("Partitions with simulated metrics", len(orders_with_simulated_metrics))
        
        if group_comparisons:
            ProgressLogger.metric("Group comparisons generated", len(group_comparisons))
        
        logger.info(f"\nOutput files saved to: {self.config.output_dir}/")


# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================

def main():
    """Command-line interface for the pipeline."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Extract Centre Point orders and matching trades from large CSV files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full pipeline with partitioning
  python ingest_centrepoint.py
  
  # Skip optional steps
  python ingest_centrepoint.py --skip-metrics --skip-groups
  
  # Use custom chunk size
  python ingest_centrepoint.py --chunk-size 50000
  
  # Use custom output directory
  python ingest_centrepoint.py --output-dir ./my_data
  
  # Disable partitioning
  python ingest_centrepoint.py --no-partition

Output Structure:
  With partitioning enabled (default):
    ./data/{date}/{orderbookid}/orders_before_lob.csv
    ./data/{date}/{orderbookid}/orders_after_lob.csv
  
  Example:
    ./data/2024-09-04/110621/orders_before_lob.csv
    ./data/2024-09-04/110621/orders_after_lob.csv
        """
    )
    
    parser.add_argument(
        '--output-dir',
        type=str,
        default='data',
        help='Base output directory for processed files (default: data)'
    )
    parser.add_argument(
        '--chunk-size',
        type=int,
        default=100000,
        help='Number of rows per chunk (default: 100000)'
    )
    parser.add_argument(
        '--skip-metrics',
        action='store_true',
        help='Skip metrics calculation step'
    )
    parser.add_argument(
        '--skip-before-after',
        action='store_true',
        help='Skip before/after LOB state extraction'
    )
    parser.add_argument(
        '--skip-groups',
        action='store_true',
        help='Skip group categorization step'
    )
    parser.add_argument(
        '--no-partition',
        action='store_true',
        help='Disable partitioning by date/orderbookid'
    )
    parser.add_argument(
        '--skip-simulation',
        action='store_true',
        help='Skip sweep order matching simulation'
    )
    parser.add_argument(
        '--use-partitioned',
        action='store_true',
        help='Use fully partitioned pipeline (partitions raw data immediately)'
    )
    
    args = parser.parse_args()
    
    # Create configuration
    config = PipelineConfig(
        output_dir=args.output_dir,
        chunk_size=args.chunk_size,
        calculate_metrics=not args.skip_metrics,
        extract_before_after=not args.skip_before_after,
        categorize_groups=not args.skip_groups,
        partition_by_date=not args.no_partition,
        partition_by_orderbookid=not args.no_partition,
        run_sweep_simulation=not args.skip_simulation,
        calculate_simulated_metrics=not args.skip_simulation,
        compare_groups=not args.skip_simulation,
        generate_comparison_reports=not args.skip_simulation
    )
    
    # Run pipeline
    pipeline = CentrePointPipeline(config)
    if args.use_partitioned:
        logger.info("Using fully partitioned pipeline (run_partitioned)")
        results = pipeline.run_partitioned()
    else:
        logger.info("Using standard pipeline (run)")
        results = pipeline.run()
    
    # Print final summary
    print("\n" + "="*80)
    print("EXTRACTION COMPLETE")
    print("="*80)
    print(f"✅ Centre Point orders: {len(results.cp_orders):,}")
    print(f"✅ Matching trades: {len(results.matched_trades):,}")
    print(f"✅ Aggregated orders: {len(results.trades_agg):,}")
    
    if results.orders_with_metrics is not None:
        print(f"✅ Orders with metrics: {len(results.orders_with_metrics):,}")
    
    if results.before_after_by_partition:
        print(f"✅ Partitions created: {len(results.before_after_by_partition)}")
        print(f"✅ Output structure: {config.output_dir}/{{date}}/{{orderbookid}}/orders_{{before|after}}_lob.csv")
        
        # Show sample partition paths
        sample_partitions = list(results.before_after_by_partition.keys())[:3]
        if sample_partitions:
            print(f"\n   Sample partitions:")
            for partition_key in sample_partitions:
                partition = results.partitions[partition_key]
                output_dir = partition.get_output_dir(config.output_dir)
                print(f"     - {output_dir}/")
    
    if results.groups is not None:
        print(f"✅ Group 1 (Fully Filled): {len(results.groups[0]):,}")
        print(f"✅ Group 2 (Partially Filled): {len(results.groups[1]):,}")
        print(f"✅ Group 3 (Not Executed): {len(results.groups[2]):,}")
    
    print(f"\n⏱️  Execution time: {results.execution_time:.2f}s")


if __name__ == '__main__':
    main()
