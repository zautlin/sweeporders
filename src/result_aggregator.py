"""
Phase 5: Result Aggregation Module

Combines results from parallel jobs and generates consolidated metrics.

Key Features:
- Aggregate (security_code, date) results
- Group by security code, date, participant ID, time of day, order size
- Generate summary statistics
- Export to CSV/Parquet
- Memory-efficient batch processing
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import logging
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class AggregationMetrics:
    """Metrics from aggregation operation"""
    total_rows_input: int
    total_rows_output: int
    groups_by_security: int
    groups_by_date: int
    groups_by_participant: int
    aggregation_time_sec: float
    output_files: Optional[List[str]] = None
    
    def __post_init__(self):
        if self.output_files is None:
            self.output_files = []


class AggregationEngine:
    """
    Core aggregation engine for combining job results
    
    Aggregates results by:
    - Security code (all dates combined)
    - Date (all securities combined)
    - Participant ID (all securities and dates combined)
    - Time of day (intra-day patterns)
    - Order size (execution patterns by size)
    """
    
    def __init__(self, verbose: bool = True):
        """Initialize AggregationEngine"""
        self.verbose = verbose
        self.all_results = []
        self.metrics = None
    
    def add_results(self, result_dataframe: pd.DataFrame) -> None:
        """Add a result dataframe to aggregation pool"""
        if result_dataframe.empty:
            logger.warning("Received empty dataframe, skipping")
            return
        
        self.all_results.append(result_dataframe)
        if self.verbose:
            logger.info(f"Added result: {len(result_dataframe)} rows, total pool: {sum(len(r) for r in self.all_results):,}")
    
    def combine_results(self) -> pd.DataFrame:
        """Combine all results into single dataframe"""
        if not self.all_results:
            logger.warning("No results to combine")
            return pd.DataFrame()
        
        combined = pd.concat(self.all_results, ignore_index=True)
        
        if self.verbose:
            logger.info(f"Combined results: {len(combined):,} rows from {len(self.all_results)} batches")
        
        return combined
    
    def aggregate_by_security(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate all results by security code"""
        if df.empty or 'security_code' not in df.columns:
            return pd.DataFrame()
        
        # Group by security code
        grouped = df.groupby('security_code').agg({
            'order_id': 'count',
            'quantity': ['sum', 'mean', 'std'],
            'price': ['mean', 'min', 'max'],
            'participantid': 'nunique',
            'timestamp': ['min', 'max'],
        }).reset_index()
        
        # Flatten column names
        grouped.columns = ['_'.join(col).strip('_') if col[1] else col[0] for col in grouped.columns.values]
        grouped.rename(columns={'order_id_count': 'total_orders'}, inplace=True)
        
        if self.verbose:
            logger.info(f"Security aggregation: {len(grouped)} unique securities")
        
        return grouped
    
    def aggregate_by_date(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate all results by date"""
        if df.empty or 'timestamp' not in df.columns:
            return pd.DataFrame()
        
        # Convert timestamp to date if needed
        if df['timestamp'].dtype == 'int64':
            df_copy = df.copy()
            df_copy['date'] = pd.to_datetime(df_copy['timestamp'], unit='ns', utc=True).dt.date
        else:
            df_copy = df.copy()
            df_copy['date'] = pd.to_datetime(df_copy['timestamp']).dt.date
        
        # Group by date
        grouped = df_copy.groupby('date').agg({
            'order_id': 'count',
            'quantity': ['sum', 'mean'],
            'price': ['mean', 'min', 'max'],
            'participantid': 'nunique',
            'security_code': 'nunique',
        }).reset_index()
        
        # Flatten column names
        grouped.columns = ['_'.join(col).strip('_') if col[1] else col[0] for col in grouped.columns.values]
        grouped.rename(columns={'order_id_count': 'total_orders'}, inplace=True)
        
        if self.verbose:
            logger.info(f"Date aggregation: {len(grouped)} unique dates")
        
        return grouped
    
    def aggregate_by_participant(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate all results by participant ID"""
        if df.empty or 'participantid' not in df.columns:
            return pd.DataFrame()
        
        # Group by participant
        grouped = df.groupby('participantid').agg({
            'order_id': 'count',
            'quantity': ['sum', 'mean'],
            'price': ['mean', 'min', 'max'],
            'security_code': 'nunique',
        }).reset_index()
        
        # Flatten column names
        grouped.columns = ['_'.join(col).strip('_') if col[1] else col[0] for col in grouped.columns.values]
        grouped.rename(columns={'order_id_count': 'total_orders'}, inplace=True)
        
        if self.verbose:
            logger.info(f"Participant aggregation: {len(grouped)} unique participants")
        
        return grouped
    
    def aggregate_by_time_of_day(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate results by hour of day"""
        if df.empty or 'timestamp' not in df.columns:
            return pd.DataFrame()
        
        df_copy = df.copy()
        
        # Extract hour
        if df_copy['timestamp'].dtype == 'int64':
            df_copy['timestamp_dt'] = pd.to_datetime(df_copy['timestamp'], unit='ns', utc=True)
        else:
            df_copy['timestamp_dt'] = pd.to_datetime(df_copy['timestamp'])
        
        df_copy['hour'] = df_copy['timestamp_dt'].dt.hour
        
        # Group by hour
        grouped = df_copy.groupby('hour').agg({
            'order_id': 'count',
            'quantity': ['sum', 'mean'],
            'price': ['mean', 'min', 'max'],
            'participantid': 'nunique',
        }).reset_index()
        
        # Flatten column names
        grouped.columns = ['_'.join(col).strip('_') if col[1] else col[0] for col in grouped.columns.values]
        grouped.rename(columns={'order_id_count': 'total_orders'}, inplace=True)
        
        if self.verbose:
            logger.info(f"Time of day aggregation: {len(grouped)} hours")
        
        return grouped
    
    def aggregate_by_order_size(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate results by order size buckets"""
        if df.empty or 'quantity' not in df.columns:
            return pd.DataFrame()
        
        df_copy = df.copy()
        
        # Create size buckets
        df_copy['size_bucket'] = pd.cut(df_copy['quantity'],
                                        bins=[0, 1000, 5000, 10000, 50000, np.inf],
                                        labels=['<1K', '1K-5K', '5K-10K', '10K-50K', '>50K'])
        
        # Group by bucket
        grouped = df_copy.groupby('size_bucket', observed=True).agg({
            'order_id': 'count',
            'quantity': ['sum', 'mean'],
            'price': ['mean', 'min', 'max'],
            'participantid': 'nunique',
        }).reset_index()
        
        # Flatten column names
        grouped.columns = ['_'.join(col).strip('_') if col[1] else col[0] for col in grouped.columns.values]
        grouped.rename(columns={'order_id_count': 'total_orders'}, inplace=True)
        
        if self.verbose:
            logger.info(f"Order size aggregation: {len(grouped)} size buckets")
        
        return grouped


class TimeSeriesAggregator:
    """
    Generate time series aggregations for temporal analysis
    """
    
    @staticmethod
    def hourly_aggregation(df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate to hourly buckets"""
        if df.empty or 'timestamp' not in df.columns:
            return pd.DataFrame()
        
        df_copy = df.copy()
        
        # Convert to datetime if needed
        if df_copy['timestamp'].dtype == 'int64':
            df_copy['timestamp_dt'] = pd.to_datetime(df_copy['timestamp'], unit='ns', utc=True)
        else:
            df_copy['timestamp_dt'] = pd.to_datetime(df_copy['timestamp'])
        
        # Round to hour
        df_copy['hour_bucket'] = df_copy['timestamp_dt'].dt.floor('H')
        
        # Aggregate
        grouped = df_copy.groupby('hour_bucket').agg({
            'order_id': 'count',
            'quantity': ['sum', 'mean'],
            'price': 'mean',
        }).reset_index()
        
        grouped.columns = ['timestamp', 'order_count', 'quantity_sum', 'quantity_avg', 'price_avg']
        
        return grouped
    
    @staticmethod
    def daily_aggregation(df: pd.DataFrame) -> pd.DataFrame:
        """Aggregate to daily buckets"""
        if df.empty or 'timestamp' not in df.columns:
            return pd.DataFrame()
        
        df_copy = df.copy()
        
        # Convert to datetime if needed
        if df_copy['timestamp'].dtype == 'int64':
            df_copy['timestamp_dt'] = pd.to_datetime(df_copy['timestamp'], unit='ns', utc=True)
        else:
            df_copy['timestamp_dt'] = pd.to_datetime(df_copy['timestamp'])
        
        # Extract date
        df_copy['date'] = df_copy['timestamp_dt'].dt.date
        
        # Aggregate
        grouped = df_copy.groupby('date').agg({
            'order_id': 'count',
            'quantity': ['sum', 'mean'],
            'price': 'mean',
            'participantid': 'nunique',
            'security_code': 'nunique',
        }).reset_index()
        
        grouped.columns = ['date', 'order_count', 'quantity_sum', 'quantity_avg', 'price_avg', 'participant_count', 'security_count']
        
        return grouped


class ResultWriter:
    """
    Write aggregated results to CSV or Parquet
    """
    
    def __init__(self, output_dir: str = 'processed_files/', format: str = 'csv'):
        """
        Initialize ResultWriter
        
        Args:
            output_dir: Directory to save files
            format: 'csv' or 'parquet'
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.format = format
        self.written_files = []
    
    def write_results(self, dataframe: pd.DataFrame, filename: str) -> str:
        """Write dataframe to file"""
        filepath = self.output_dir / filename
        
        try:
            if self.format == 'csv':
                dataframe.to_csv(filepath, index=False)
                self.written_files.append(str(filepath))
                logger.info(f"Wrote {len(dataframe):,} rows to {filepath}")
            elif self.format == 'parquet':
                dataframe.to_parquet(filepath, index=False, compression='gzip')
                self.written_files.append(str(filepath))
                logger.info(f"Wrote {len(dataframe):,} rows to {filepath}")
            else:
                raise ValueError(f"Unknown format: {self.format}")
            
            return str(filepath)
        except Exception as e:
            logger.error(f"Failed to write {filepath}: {e}")
            raise
    
    def write_summary(self, summary_dict: Dict[str, Any], filename: str = 'aggregation_summary.json') -> str:
        """Write summary as JSON"""
        filepath = self.output_dir / filename
        
        try:
            with open(filepath, 'w') as f:
                json.dump(summary_dict, f, indent=2, default=str)
            logger.info(f"Wrote summary to {filepath}")
            return str(filepath)
        except Exception as e:
            logger.error(f"Failed to write summary: {e}")
            raise


class ResultAggregator:
    """
    Complete result aggregation pipeline
    
    Usage:
        aggregator = ResultAggregator(output_dir='processed_files/')
        
        # Add results from jobs
        for result_df in job_results:
            aggregator.add_result(result_df)
        
        # Generate aggregations
        aggregator.aggregate_all()
        
        # Print summary
        aggregator.print_summary()
    """
    
    def __init__(self, output_dir: str = 'processed_files/', format: str = 'csv', verbose: bool = True):
        """Initialize ResultAggregator"""
        self.output_dir = output_dir
        self.format = format
        self.verbose = verbose
        
        self.engine = AggregationEngine(verbose=verbose)
        self.writer = ResultWriter(output_dir=output_dir, format=format)
        self.ts_agg = TimeSeriesAggregator()
        
        self.aggregations = {}
        self.metrics = None
    
    def add_result(self, result_df: pd.DataFrame) -> None:
        """Add a job result to aggregator"""
        self.engine.add_results(result_df)
    
    def aggregate_all(self) -> Dict[str, pd.DataFrame]:
        """Run all aggregations"""
        import time
        start_time = time.time()
        
        # Combine all results
        combined = self.engine.combine_results()
        
        if combined.empty:
            logger.warning("No results to aggregate")
            return {}
        
        # Run aggregations
        self.aggregations = {
            'by_security': self.engine.aggregate_by_security(combined),
            'by_date': self.engine.aggregate_by_date(combined),
            'by_participant': self.engine.aggregate_by_participant(combined),
            'by_time_of_day': self.engine.aggregate_by_time_of_day(combined),
            'by_order_size': self.engine.aggregate_by_order_size(combined),
            'hourly_ts': self.ts_agg.hourly_aggregation(combined),
            'daily_ts': self.ts_agg.daily_aggregation(combined),
        }
        
        # Metrics
        agg_time = time.time() - start_time
        self.metrics = AggregationMetrics(
            total_rows_input=len(combined),
            total_rows_output=sum(len(v) for v in self.aggregations.values()),
            groups_by_security=len(self.aggregations['by_security']),
            groups_by_date=len(self.aggregations['by_date']),
            groups_by_participant=len(self.aggregations['by_participant']),
            aggregation_time_sec=agg_time,
        )
        
        if self.verbose:
            logger.info(f"Aggregation complete in {agg_time:.2f}s")
        
        return self.aggregations
    
    def write_all(self) -> List[str]:
        """Write all aggregations to files"""
        written = []
        
        filenames = {
            'by_security': 'aggregation_by_security.csv',
            'by_date': 'aggregation_by_date.csv',
            'by_participant': 'aggregation_by_participant.csv',
            'by_time_of_day': 'aggregation_by_time_of_day.csv',
            'by_order_size': 'aggregation_by_order_size.csv',
            'hourly_ts': 'timeseries_hourly.csv',
            'daily_ts': 'timeseries_daily.csv',
        }
        
        for agg_name, df in self.aggregations.items():
            if not df.empty and agg_name in filenames:
                filepath = self.writer.write_results(df, filenames[agg_name])
                written.append(filepath)
        
        # Write summary
        if self.metrics:
            summary = {
                'aggregation_time_sec': self.metrics.aggregation_time_sec,
                'total_rows_input': self.metrics.total_rows_input,
                'total_rows_output': self.metrics.total_rows_output,
                'groups_by_security': self.metrics.groups_by_security,
                'groups_by_date': self.metrics.groups_by_date,
                'groups_by_participant': self.metrics.groups_by_participant,
                'generated_at': datetime.now().isoformat(),
            }
            self.writer.write_summary(summary)
        
        return written
    
    def print_summary(self) -> None:
        """Print summary of aggregation"""
        if not self.metrics:
            logger.warning("No metrics available")
            return
        
        print("\n" + "="*70)
        print("RESULT AGGREGATOR SUMMARY")
        print("="*70)
        print(f"\nInput:")
        print(f"  Total rows: {self.metrics.total_rows_input:,}")
        
        print(f"\nAggregations:")
        print(f"  By security: {self.metrics.groups_by_security} unique codes")
        print(f"  By date: {self.metrics.groups_by_date} unique dates")
        print(f"  By participant: {self.metrics.groups_by_participant} unique participants")
        
        print(f"\nOutput:")
        print(f"  Total aggregated rows: {self.metrics.total_rows_output:,}")
        print(f"  Files written: {len(self.writer.written_files)}")
        
        print(f"\nPerformance:")
        print(f"  Aggregation time: {self.metrics.aggregation_time_sec:.2f}s")
        
        print("="*70 + "\n")


# ============================================================================
# MAIN (For Testing)
# ============================================================================

if __name__ == '__main__':
    print("Testing ResultAggregator\n")
    
    # Create sample data
    print("Test 1: Generate sample results")
    np.random.seed(42)
    
    sample_data = {
        'order_id': np.arange(1000, 2000),
        'security_code': np.random.choice([110621, 110622, 110623], 1000),
        'participantid': np.random.choice([69, 70, 71], 1000),
        'quantity': np.random.randint(100, 10000, 1000),
        'price': np.random.uniform(3000, 4000, 1000),
        'timestamp': np.random.randint(1725494536000000000, 1725581136000000000, 1000),
    }
    
    sample_df = pd.DataFrame(sample_data)
    print(f"  Sample data shape: {sample_df.shape}")
    print(f"  Columns: {list(sample_df.columns)}")
    
    print(f"\nTest 2: Initialize aggregator")
    aggregator = ResultAggregator(verbose=True)
    aggregator.add_result(sample_df)
    print(f"  ✓ Aggregator ready")
    
    print(f"\nTest 3: Run aggregations")
    aggregations = aggregator.aggregate_all()
    print(f"  ✓ Aggregations complete: {len(aggregations)} types")
    
    for agg_name, agg_df in aggregations.items():
        print(f"    {agg_name}: {len(agg_df)} rows")
    
    print(f"\nTest 4: Write results")
    written = aggregator.write_all()
    print(f"  ✓ Files written: {len(written)}")
    
    print(f"\nTest 5: Print summary")
    aggregator.print_summary()
    
    print("ResultAggregator tests passed! ✓")
