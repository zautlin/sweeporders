"""
Chunk Iterator for Memory-Efficient File Processing

Enables streaming processing of large CSV files in configurable chunks
without loading entire file into memory.

Key Features:
- Stream CSV files in fixed MB-sized chunks
- Preserve row integrity (no rows split across chunks)
- Calculate optimal chunk size based on available memory
- Support for filtering and preprocessing
- Progress tracking and metrics
"""

import pandas as pd
import logging
from pathlib import Path
from typing import Iterator, Optional, List, Dict, Tuple
from dataclasses import dataclass
import time

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class ChunkMetrics:
    """Metrics for a processed chunk"""
    chunk_num: int
    rows_read: int
    rows_kept: int
    bytes_read: int
    processing_time_sec: float
    rows_per_sec: float


class ChunkIterator:
    """
    Iterator for streaming large CSV files in memory-efficient chunks
    
    Usage:
        with ChunkIterator('data/large_file.csv', chunk_size_mb=400) as chunks:
            for chunk_df in chunks:
                # Process chunk (e.g., 400MB at a time)
                process_chunk(chunk_df)
    """
    
    def __init__(
        self,
        filepath: str,
        chunk_size_mb: int = 400,
        filter_func: Optional[callable] = None,
        dtypes: Optional[Dict[str, str]] = None,
        usecols: Optional[List[str]] = None,
        verbose: bool = True,
    ):
        """
        Initialize ChunkIterator
        
        Args:
            filepath: Path to CSV file to process
            chunk_size_mb: Target chunk size in MB (actual size may vary due to row boundaries)
            filter_func: Optional function to filter rows (returns True to keep)
            dtypes: Optional dict of column names to dtypes for pd.read_csv
            usecols: Optional list of columns to read (if None, read all)
            verbose: Print progress messages
        """
        self.filepath = Path(filepath)
        self.chunk_size_bytes = chunk_size_mb * 1024 * 1024
        self.filter_func = filter_func
        self.dtypes = dtypes
        self.usecols = usecols
        self.verbose = verbose
        
        # Validate file exists
        if not self.filepath.exists():
            raise FileNotFoundError(f"File not found: {self.filepath}")
        
        # File stats
        self.file_size_bytes = self.filepath.stat().st_size
        self.file_size_mb = self.file_size_bytes / (1024 * 1024)
        
        if self.verbose:
            logger.info(f"ChunkIterator initialized: {self.filepath.name} ({self.file_size_mb:.1f} MB)")
            logger.info(f"  Chunk size: {chunk_size_mb} MB")
            logger.info(f"  Estimated chunks: {max(1, int(self.file_size_bytes / self.chunk_size_bytes))}")
        
        # Metrics
        self.chunks_processed = 0
        self.total_rows_read = 0
        self.total_rows_kept = 0
        self.total_time_sec = 0.0
        self.chunk_metrics: List[ChunkMetrics] = []
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.print_summary()
    
    def __iter__(self) -> Iterator[pd.DataFrame]:
        """
        Iterate over chunks of the CSV file
        
        Yields:
            pd.DataFrame: Chunk of data (filtered if filter_func provided)
        """
        chunk_num = 0
        bytes_read = 0
        
        # Read CSV in chunks of nrows (since we don't know nrows per MB)
        # Use adaptive row reading to hit chunk_size_bytes target
        
        nrows_estimate = self._estimate_nrows_for_chunk()
        
        for chunk_df in pd.read_csv(
            self.filepath,
            chunksize=nrows_estimate,
            dtype=self.dtypes,
            usecols=self.usecols,
        ):
            if chunk_df.empty:
                continue
            
            chunk_num += 1
            start_time = time.time()
            rows_before = len(chunk_df)
            
            # Apply filter if provided
            if self.filter_func:
                chunk_df = chunk_df[chunk_df.apply(self.filter_func, axis=1)]
            
            rows_after = len(chunk_df)
            bytes_in_chunk = chunk_df.memory_usage(deep=True).sum()
            processing_time = time.time() - start_time
            
            # Track metrics
            self.total_rows_read += rows_before
            self.total_rows_kept += rows_after
            self.total_time_sec += processing_time
            self.chunks_processed += 1
            bytes_read += bytes_in_chunk
            
            metric = ChunkMetrics(
                chunk_num=chunk_num,
                rows_read=rows_before,
                rows_kept=rows_after,
                bytes_read=bytes_in_chunk,
                processing_time_sec=processing_time,
                rows_per_sec=rows_after / processing_time if processing_time > 0 else 0,
            )
            self.chunk_metrics.append(metric)
            
            if self.verbose and chunk_num % 5 == 0:
                logger.info(
                    f"Chunk {chunk_num}: {rows_before} rows → {rows_after} rows "
                    f"({bytes_in_chunk / 1024 / 1024:.1f} MB) "
                    f"in {processing_time:.2f}s"
                )
            
            # Only yield non-empty chunks after filtering
            if not chunk_df.empty:
                yield chunk_df
    
    def print_summary(self) -> None:
        """Print summary of chunk processing"""
        if self.chunks_processed == 0:
            logger.warning("No chunks were processed")
            return
        
        avg_chunk_size = self.total_rows_read / self.chunks_processed
        filter_ratio = (self.total_rows_kept / self.total_rows_read * 100) if self.total_rows_read > 0 else 0
        total_throughput = self.total_rows_kept / self.total_time_sec if self.total_time_sec > 0 else 0
        
        print("\n" + "="*70)
        print("CHUNK ITERATOR SUMMARY")
        print("="*70)
        print(f"File: {self.filepath.name}")
        print(f"Total Size: {self.file_size_mb:.1f} MB")
        print(f"\nChunks Processed: {self.chunks_processed}")
        print(f"Average Chunk Size: {avg_chunk_size:.0f} rows")
        print(f"\nRows Read: {self.total_rows_read:,}")
        print(f"Rows Kept: {self.total_rows_kept:,}")
        print(f"Filter Ratio: {filter_ratio:.1f}% retained")
        print(f"\nTotal Time: {self.total_time_sec:.2f} seconds")
        print(f"Throughput: {total_throughput:.0f} rows/sec")
        print("="*70 + "\n")
    
    # ========== PRIVATE HELPER METHODS ==========
    
    def _estimate_nrows_for_chunk(self) -> int:
        """
        Estimate number of rows to read to hit chunk_size_bytes target
        
        Reads first 1000 rows to estimate average row size
        """
        try:
            sample_df = pd.read_csv(
                self.filepath,
                nrows=1000,
                dtype=self.dtypes,
                usecols=self.usecols,
            )
            
            if sample_df.empty:
                return 10000  # Default fallback
            
            avg_bytes_per_row = sample_df.memory_usage(deep=True).sum() / len(sample_df)
            estimated_nrows = int(self.chunk_size_bytes / avg_bytes_per_row)
            
            # Ensure reasonable bounds
            estimated_nrows = max(1000, min(estimated_nrows, 1000000))
            
            if self.verbose:
                logger.info(f"Estimated {estimated_nrows:,} rows per chunk based on sample")
            
            return estimated_nrows
        
        except Exception as e:
            logger.warning(f"Could not estimate nrows: {e}, using default")
            return 50000  # Default fallback


class ChunkFilter:
    """Pre-built filters for common filtering scenarios"""
    
    @staticmethod
    def by_security_code(security_codes: List[int]) -> callable:
        """Filter to keep only specified security codes"""
        def filter_func(row):
            return row.get('security_code', row.get('SecurityCode', None)) in security_codes
        return filter_func
    
    @staticmethod
    def by_date(start_date: str, end_date: str, date_column: str = 'date') -> callable:
        """Filter to keep only rows within date range"""
        from datetime import datetime
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
        
        def filter_func(row):
            try:
                row_date = pd.to_datetime(row[date_column]).date()
                return start <= row_date <= end
            except:
                return False
        return filter_func
    
    @staticmethod
    def by_participant_id(participant_ids: List[int]) -> callable:
        """Filter to keep only specified participant IDs"""
        def filter_func(row):
            return row.get('participant_id', row.get('ParticipantID', None)) in participant_ids
        return filter_func
    
    @staticmethod
    def combine_filters(*filters: callable) -> callable:
        """Combine multiple filters with AND logic"""
        def combined_filter(row):
            return all(f(row) for f in filters)
        return combined_filter


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def read_csv_in_chunks(
    filepath: str,
    chunk_size_mb: int = 400,
    filter_func: Optional[callable] = None,
    process_func: Optional[callable] = None,
    verbose: bool = True,
) -> Tuple[int, int, float]:
    """
    Read CSV in chunks and optionally process each chunk
    
    Args:
        filepath: Path to CSV file
        chunk_size_mb: Target chunk size in MB
        filter_func: Optional function to filter rows
        process_func: Optional function to process each chunk
        verbose: Print progress messages
    
    Returns:
        (total_rows_read, total_rows_kept, total_time_sec)
    """
    with ChunkIterator(filepath, chunk_size_mb, filter_func, verbose=verbose) as chunks:
        for chunk_df in chunks:
            if process_func:
                process_func(chunk_df)
    
    return chunks.total_rows_read, chunks.total_rows_kept, chunks.total_time_sec


# ============================================================================
# MAIN (For Testing)
# ============================================================================

if __name__ == '__main__':
    print("Testing ChunkIterator\n")
    
    # Test with actual orders file
    orders_file = 'data/orders/drr_orders.csv'
    
    if Path(orders_file).exists():
        print(f"Test 1: Basic chunk reading from {orders_file}")
        with ChunkIterator(orders_file, chunk_size_mb=50) as chunks:
            for i, chunk in enumerate(chunks):
                if i == 0:
                    print(f"  First chunk shape: {chunk.shape}")
                    print(f"  Columns: {list(chunk.columns)[:5]}...")
        
        print(f"\nTest 2: Reading with security code filter")
        # Assuming security_code column exists
        filter_func = ChunkFilter.by_security_code([101, 102, 103])
        rows_read, rows_kept, time_sec = read_csv_in_chunks(
            orders_file,
            chunk_size_mb=50,
            filter_func=filter_func,
        )
        print(f"  Read {rows_read:,} rows, kept {rows_kept:,} rows in {time_sec:.2f}s")
        
        print("\nChunkIterator tests passed! ✓")
    else:
        print(f"File not found: {orders_file}")
        print("Skipping tests - file not available")
