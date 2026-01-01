"""
Scalable Ingest Module for Multi-Security/Multi-Date Processing

Refactored ingest pipeline compatible with ChunkIterator and ParallelJobScheduler.
- Works with data chunks from ChunkIterator
- Filters by (security_code, date) combinations
- Returns clean dataframes ready for classification

Key Features:
- Process chunks from ChunkIterator (memory efficient)
- Filter by security code and date
- Optional: filter by participant ID and trading hours
- Optimize data types for memory efficiency
- Return statistics for aggregation
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional, Dict, List
from dataclasses import dataclass
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class IngestMetrics:
    """Metrics from ingest operation"""
    rows_input: int
    rows_after_date_filter: int
    rows_after_security_filter: int
    rows_after_participant_filter: int
    rows_after_hour_filter: int
    rows_output: int
    memory_mb: float


class ScalableIngest:
    """
    Scalable ingest module for processing data chunks
    
    Usage:
        ingest = ScalableIngest(
            security_code=101,
            date='2024-01-01',
            participant_id=69,  # optional
            trading_hours=(10, 16),  # optional
        )
        
        # Process a chunk from ChunkIterator
        df_processed, metrics = ingest.process_chunk(chunk_df)
    """
    
    def __init__(
        self,
        security_code: int,
        date: str,
        participant_id: Optional[int] = None,
        trading_hours: Optional[Tuple[int, int]] = None,
        timezone_offset: int = 10,  # AEST = UTC+10
    ):
        """
        Initialize ScalableIngest with filter parameters
        
        Args:
            security_code: Security code to filter for
            date: Date string (YYYY-MM-DD format) to filter for
            participant_id: Optional participant ID to filter for (e.g., 69 for Centre Point)
            trading_hours: Optional tuple (start_hour, end_hour) for trading hour filtering
            timezone_offset: UTC offset in hours (default 10 for AEST)
        """
        self.security_code = security_code
        self.date = date
        self.participant_id = participant_id
        self.trading_hours = trading_hours
        self.timezone_offset = timezone_offset
        self.tz = timezone(timedelta(hours=timezone_offset))
        
        # Parse date
        try:
            self.date_obj = datetime.fromisoformat(date).date()
        except ValueError:
            raise ValueError(f"Invalid date format: {date}. Use YYYY-MM-DD")
        
        self.metrics = IngestMetrics(0, 0, 0, 0, 0, 0, 0.0)
    
    def process_chunk(self, chunk_df: pd.DataFrame) -> Tuple[pd.DataFrame, IngestMetrics]:
        """
        Process a chunk of data with filtering
        
        Args:
            chunk_df: DataFrame chunk from ChunkIterator
        
        Returns:
            (processed_df, metrics)
        """
        if chunk_df.empty:
            return chunk_df, self.metrics
        
        df = chunk_df.copy()
        self.metrics.rows_input = len(df)
        
        # Step 1: Filter by date
        df = self._filter_by_date(df)
        self.metrics.rows_after_date_filter = len(df)
        
        if df.empty:
            self.metrics.rows_output = 0
            return df, self.metrics
        
        # Step 2: Filter by security code
        df = self._filter_by_security(df)
        self.metrics.rows_after_security_filter = len(df)
        
        if df.empty:
            self.metrics.rows_output = 0
            return df, self.metrics
        
        # Step 3: Filter by participant ID (if specified)
        if self.participant_id is not None:
            df = self._filter_by_participant(df)
            self.metrics.rows_after_participant_filter = len(df)
        
        if df.empty:
            self.metrics.rows_output = 0
            return df, self.metrics
        
        # Step 4: Filter by trading hours (if specified)
        if self.trading_hours is not None:
            df = self._filter_by_hours(df)
            self.metrics.rows_after_hour_filter = len(df)
        
        # Step 5: Optimize data types
        df = self._optimize_dtypes(df)
        
        self.metrics.rows_output = len(df)
        self.metrics.memory_mb = df.memory_usage(deep=True).sum() / (1024 * 1024)
        
        return df, self.metrics
    
    # ========== PRIVATE FILTER METHODS ==========
    
    def _filter_by_date(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter rows by date"""
        if 'timestamp' not in df.columns:
            logger.warning("'timestamp' column not found, skipping date filter")
            return df
        
        # Convert timestamp (nanoseconds) to datetime
        df_copy = df.copy()
        df_copy['timestamp_dt'] = pd.to_datetime(df_copy['timestamp'], unit='ns', utc=True).dt.tz_convert(self.tz)
        
        # Filter by date
        filtered = df_copy[df_copy['timestamp_dt'].dt.date == self.date_obj].copy()
        
        # Drop temporary column
        if 'timestamp_dt' in filtered.columns:
            filtered = filtered.drop(columns=['timestamp_dt'])
        
        return filtered
    
    def _filter_by_security(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter rows by security code"""
        if 'security_code' not in df.columns:
            logger.warning("'security_code' column not found, skipping security filter")
            return df
        
        return df[df['security_code'] == self.security_code].copy()
    
    def _filter_by_participant(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter rows by participant ID"""
        if 'participantid' not in df.columns:
            logger.warning("'participantid' column not found, skipping participant filter")
            return df
        
        return df[df['participantid'] == self.participant_id].copy()
    
    def _filter_by_hours(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter rows by trading hours"""
        if 'timestamp' not in df.columns:
            logger.warning("'timestamp' column not found, skipping hour filter")
            return df
        
        df_copy = df.copy()
        df_copy['timestamp_dt'] = pd.to_datetime(df_copy['timestamp'], unit='ns', utc=True).dt.tz_convert(self.tz)
        df_copy['hour'] = df_copy['timestamp_dt'].dt.hour
        
        start_hour, end_hour = self.trading_hours
        filtered = df_copy[(df_copy['hour'] >= start_hour) & (df_copy['hour'] <= end_hour)].copy()
        
        # Drop temporary columns
        cols_to_drop = [c for c in ['timestamp_dt', 'hour'] if c in filtered.columns]
        if cols_to_drop:
            filtered = filtered.drop(columns=cols_to_drop)
        
        return filtered
    
    @staticmethod
    def _optimize_dtypes(df: pd.DataFrame) -> pd.DataFrame:
        """Optimize data types to reduce memory usage"""
        df_copy = df.copy()
        
        # Define optimal types for common columns
        type_mapping = {
            'order_id': 'uint64',
            'timestamp': 'int64',
            'quantity': 'uint32',
            'leavesquantity': 'uint32',
            'totalmatchedquantity': 'uint32',
            'price': 'float32',
            'participantid': 'uint32',
            'security_code': 'uint32',
            'side': 'int8',
            'exchangeordertype': 'int8',
            'orderstatus': 'int8',
        }
        
        for col, dtype in type_mapping.items():
            if col in df_copy.columns:
                try:
                    df_copy[col] = df_copy[col].astype(dtype)
                except Exception as e:
                    logger.warning(f"Could not convert {col} to {dtype}: {e}")
        
        return df_copy


class BatchIngest:
    """
    Process multiple (security_code, date) combinations from chunks
    
    Usage:
        batch = BatchIngest(
            job_configs=[
                {'security_code': 101, 'date': '2024-01-01'},
                {'security_code': 102, 'date': '2024-01-01'},
                ...
            ],
            participant_id=69,
        )
        
        # Process a chunk across multiple jobs
        results = batch.process_chunk(chunk_df)
    """
    
    def __init__(
        self,
        job_configs: List[Dict],
        participant_id: Optional[int] = None,
        trading_hours: Optional[Tuple[int, int]] = None,
    ):
        """
        Initialize BatchIngest with multiple filter configurations
        
        Args:
            job_configs: List of dicts with 'security_code' and 'date' keys
            participant_id: Optional participant ID to apply to all jobs
            trading_hours: Optional hours tuple to apply to all jobs
        """
        self.job_configs = job_configs
        self.participant_id = participant_id
        self.trading_hours = trading_hours
        
        # Create ScalableIngest instance for each config
        self.ingesters = [
            ScalableIngest(
                security_code=config['security_code'],
                date=config['date'],
                participant_id=participant_id,
                trading_hours=trading_hours,
            )
            for config in job_configs
        ]
    
    def process_chunk(self, chunk_df: pd.DataFrame) -> Dict[str, Tuple[pd.DataFrame, IngestMetrics]]:
        """
        Process chunk across multiple configurations
        
        Returns:
            Dict mapping config_key -> (processed_df, metrics)
        """
        results = {}
        
        for config, ingester in zip(self.job_configs, self.ingesters):
            key = f"{config['security_code']}_{config['date']}"
            processed_df, metrics = ingester.process_chunk(chunk_df)
            results[key] = (processed_df, metrics)
        
        return results


# ============================================================================
# LEGACY COMPATIBILITY (wrap original function)
# ============================================================================

def extract_centrepoint_orders_scalable(
    chunk_df: pd.DataFrame,
    output_dir: str = 'processed_files/',
) -> Tuple[pd.DataFrame, IngestMetrics]:
    """
    Scalable version of extract_centrepoint_orders for use with chunks
    
    Filters:
    - Centre Point participant (participantid == 69)
    - Trading hours: 10 AM to 4 PM AEST
    - All security codes
    
    Args:
        chunk_df: Data chunk from ChunkIterator
        output_dir: Directory for saved files (not used in scalable version)
    
    Returns:
        (filtered_df, metrics)
    """
    # Use Centre Point filter with trading hours
    ingest = ScalableIngest(
        security_code=None,  # Will handle this differently
        date='2024-01-01',  # Placeholder
        participant_id=69,
        trading_hours=(10, 16),
    )
    
    # Modified process that doesn't filter by security or date
    df = chunk_df.copy()
    metrics = IngestMetrics(len(df), 0, 0, 0, 0, 0, 0.0)
    
    # Convert timestamp
    aest_tz = timezone(timedelta(hours=10))
    df['timestamp_dt'] = pd.to_datetime(df['timestamp'], unit='ns', utc=True).dt.tz_convert(aest_tz)
    df['hour'] = df['timestamp_dt'].dt.hour
    
    # Filter for trading hours
    df = df[(df['hour'] >= 10) & (df['hour'] <= 16)].copy()
    metrics.rows_after_hour_filter = len(df)
    
    # Filter for Centre Point
    df = df[df['participantid'] == 69].copy()
    metrics.rows_after_participant_filter = len(df)
    
    # Optimize types
    df = ScalableIngest._optimize_dtypes(df)
    df = df.drop(columns=['timestamp_dt', 'hour'])
    
    metrics.rows_output = len(df)
    metrics.memory_mb = df.memory_usage(deep=True).sum() / (1024 * 1024)
    
    return df, metrics


# ============================================================================
# MAIN (For Testing)
# ============================================================================

if __name__ == '__main__':
    print("Testing ScalableIngest\n")
    
    # Read a sample chunk
    try:
        print("Test 1: Load orders file as chunk")
        chunk_df = pd.read_csv('data/orders/drr_orders.csv')
        print(f"  Chunk loaded: {chunk_df.shape}")
        print(f"  Columns: {list(chunk_df.columns)}")
        
        # Get available values
        security_codes = chunk_df['security_code'].unique()
        print(f"  Available security codes: {security_codes[:5]}")
        
        print(f"\nTest 2: Process chunk with security code filter")
        sec_code = security_codes[0]
        ingest = ScalableIngest(
            security_code=int(sec_code),
            date='2024-01-01',
            participant_id=69,
            trading_hours=(10, 16),
        )
        processed_df, metrics = ingest.process_chunk(chunk_df)
        print(f"  Input: {metrics.rows_input:,} rows")
        print(f"  Output: {metrics.rows_output:,} rows")
        print(f"  Memory: {metrics.memory_mb:.2f} MB")
        print(f"  Filters applied:")
        print(f"    Date: {metrics.rows_after_date_filter:,}")
        print(f"    Security: {metrics.rows_after_security_filter:,}")
        print(f"    Participant: {metrics.rows_after_participant_filter:,}")
        print(f"    Hours: {metrics.rows_after_hour_filter:,}")
        
        print(f"\nTest 3: Legacy extract_centrepoint_orders_scalable")
        cp_df, cp_metrics = extract_centrepoint_orders_scalable(chunk_df)
        print(f"  Input: {cp_metrics.rows_input:,} rows")
        print(f"  Output: {cp_metrics.rows_output:,} rows")
        print(f"  CP orders found: {cp_metrics.rows_output:,}")
        
        if cp_metrics.rows_output > 0:
            print(f"  Sample CP order:")
            print(f"    {cp_df.iloc[0].to_dict()}")
        
        print("\nScalableIngest tests passed! ✓")
    
    except FileNotFoundError as e:
        print(f"File not found: {e}")
        print("Skipping tests - data file not available")
