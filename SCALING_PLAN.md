# SCALABLE MULTI-DATE/MULTI-SECURITY PIPELINE DESIGN PLAN

**Status:** PLANNING PHASE  
**Created:** January 1, 2026  
**Objective:** Enable pipeline to process 100GB+ orders/trades files with multiple dates and securities in parallel

---

## EXECUTIVE SUMMARY

Current pipeline processes ~156 Centre Point orders from filtered 48K total orders. Real-world data contains:
- **Multiple trading dates** (weeks/months of data)
- **Multiple securities** (100+ stock codes)
- **Massive file sizes** (200GB+ orders, 200GB+ trades combined)

**Challenge:** Load all data into memory → crashes on 100GB+ files

**Solution:** Implement **streaming chunk-based processing with parallel execution**
- Process files in configurable chunks (e.g., 1GB chunks)
- Extract configuration-driven security codes and date ranges
- Run independent jobs for each (security_code, date) combination in parallel
- Aggregate results across all combinations
- Memory footprint: ~2-5GB peak regardless of input file size

---

## ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────┐
│                    CONFIGURATION LAYER                          │
│  (Enhanced Config with security codes & date ranges)            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                   CHUNK ITERATOR LAYER                          │
│  (Memory-efficient streaming of 1GB chunks from massive files)  │
├──────────────────────────────┬──────────────────────────────────┤
│   Chunk 1 (1GB)              │   Chunk 2 (1GB)                  │
│   ├─ Parse                   │   ├─ Parse                       │
│   ├─ Validate                │   ├─ Validate                    │
│   └─ Extract metadata        │   └─ Extract metadata            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                  JOB SCHEDULER LAYER                            │
│  (Parallel execution: multiprocessing + concurrent.futures)     │
├──────────────────────────────┬──────────────────────────────────┤
│  Process 1:                  │  Process 2:                      │
│  (SEC=101, DATE=2024-01-01)  │  (SEC=102, DATE=2024-01-01)     │
│  ├─ Step 1: Ingest           │  ├─ Step 1: Ingest              │
│  ├─ Step 2: Classify         │  ├─ Step 2: Classify            │
│  ├─ Step 4: Real Metrics     │  ├─ Step 4: Real Metrics        │
│  └─ Output: Results_101_xxx  │  └─ Output: Results_102_xxx     │
├──────────────────────────────┼──────────────────────────────────┤
│  Process 3:                  │  Process 4:                      │
│  (SEC=101, DATE=2024-01-02)  │  (SEC=102, DATE=2024-01-02)     │
│  └─ Similar pipeline         │  └─ Similar pipeline            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│               RESULT AGGREGATION LAYER                          │
│  (Combine results from all (security, date) combinations)       │
├──────────────────────────────┬──────────────────────────────────┤
│ Aggregate by:                                                   │
│ • security_code (all dates combined)                            │
│ • date (all securities combined)                                │
│ • participant_id (all dates/securities combined)                │
│ • time_of_day (temporal analysis across all data)               │
│ • order_size (size analysis across all data)                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                    OUTPUT LAYER                                 │
│  (Multi-level results + consolidated metrics)                   │
├──────────────────────────────┬──────────────────────────────────┤
│ Per-(sec,date) Results:      │ Aggregated Results:              │
│ ├─ sweep_orders_...gzip      │ ├─ global_metrics.csv            │
│ ├─ real_metrics_...csv       │ ├─ by_security.csv               │
│ └─ simulated_metrics_...gzip │ ├─ by_date.csv                   │
│                              │ ├─ by_participant.csv            │
│                              │ └─ consolidated_stats.csv        │
└──────────────────────────────┴──────────────────────────────────┘
```

---

## DETAILED PHASE BREAKDOWN

### PHASE 1: Enhanced Configuration System

**File:** `config/scaling_config.py` (NEW)

**Purpose:** Centralized configuration for multi-date/multi-security processing

**Features:**

```python
# Configuration structure
SCALING_CONFIG = {
    'processing': {
        'mode': 'parallel',  # 'sequential' or 'parallel'
        'max_workers': 8,     # CPU cores for parallel execution
        'chunk_size_mb': 1024,  # Process 1GB chunks at a time
        'temp_dir': 'temp_chunks/',  # Temporary storage for chunks
    },
    
    'data_selection': {
        'security_codes': [101, 102, 103],  # List of security codes to process
        'date_range': {
            'start': '2024-01-01',  # Start date (AEST)
            'end': '2024-12-31',    # End date (AEST)
            'all_dates': False,      # If True, ignore date_range
        },
        'participant_ids': [69],  # List of participant IDs
        'trading_hours': {
            'start': 10,  # 10 AM
            'end': 16,    # 4 PM
        },
    },
    
    'simulation': {
        'dark_pool_scenarios': ['A', 'B', 'C'],
        'price_impact_percent': 0.05,
    },
    
    'output': {
        'format': 'gzip',  # 'gzip' or 'parquet'
        'aggregate_by': ['security_code', 'date', 'participant_id'],
        'detailed_logs': True,
    },
}
```

**Configuration Loading:**

```python
def load_config(config_file: str = None) -> dict:
    """Load scaling config from file or use defaults"""
    if config_file:
        with open(config_file, 'r') as f:
            return yaml.load(f)  # YAML format for easy editing
    return SCALING_CONFIG

def generate_job_matrix(config: dict) -> list:
    """
    Generate list of (security_code, date) tuples to process
    
    Returns:
        [
            (101, '2024-01-01'),
            (101, '2024-01-02'),
            (102, '2024-01-01'),
            ...
        ]
    """
```

**Benefits:**
- Configuration-driven execution (no code changes)
- Flexible security code selection
- Date range filtering
- Easy to experiment with different combinations

---

### PHASE 2: Memory-Efficient Chunk Iterator

**File:** `src/chunk_iterator.py` (NEW)

**Purpose:** Stream massive CSV files in manageable chunks without loading entire file

**Algorithm:**

```python
class ChunkIterator:
    def __init__(self, file_path: str, chunk_size_mb: int = 1024):
        """
        Initialize chunk iterator
        
        Args:
            file_path: Path to CSV file (can be 100GB+)
            chunk_size_mb: Size of each chunk in MB (default 1GB)
        """
        self.file_path = file_path
        self.chunk_size = chunk_size_mb * 1024 * 1024  # Convert to bytes
        self.current_position = 0
        self.total_size = os.path.getsize(file_path)
        self.file_handle = open(file_path, 'rb')
        self.buffer = b''
        self.header = None
        
    def read_header(self) -> list:
        """Read CSV header without loading full file"""
        first_line = self.file_handle.readline().decode('utf-8').strip()
        self.header = first_line.split(',')
        return self.header
        
    def __iter__(self):
        """Iterator protocol"""
        self.file_handle.seek(0)
        return self
        
    def __next__(self) -> pd.DataFrame:
        """
        Get next chunk as DataFrame
        
        Returns:
            DataFrame with up to chunk_size_mb of data
            
        Raises:
            StopIteration when file is exhausted
        """
        # Read chunk from file
        data = self.file_handle.read(self.chunk_size)
        if not data:
            raise StopIteration
        
        # Handle incomplete rows at chunk boundary
        # (last row in chunk might be incomplete - defer to next chunk)
        last_newline = data.rfind(b'\n')
        if last_newline != -1:
            data = data[:last_newline]
        
        # Parse chunk into DataFrame
        chunk_df = pd.read_csv(
            io.BytesIO(self.buffer + data),
            dtype_backend='numpy_nullable'  # Memory efficient
        )
        
        # Carry forward incomplete row to next iteration
        self.buffer = data[last_newline + 1:]
        
        return chunk_df
        
    def __enter__(self):
        return self
        
    def __exit__(self, *args):
        self.file_handle.close()
```

**Key Features:**
- **Memory bounded:** Only 1 chunk in memory at a time
- **Streaming:** No need to read entire file
- **Metadata extraction:** Can scan file for date/security distribution
- **Progress tracking:** Know current position in file

**Usage Example:**

```python
with ChunkIterator('data/orders/drr_orders.csv', chunk_size_mb=1024) as chunks:
    for i, chunk_df in enumerate(chunks):
        print(f"Processing chunk {i}: {len(chunk_df)} rows")
        # Process chunk without loading entire file
```

**Efficiency Metrics:**
- **File Size:** 100GB
- **Chunk Size:** 1GB
- **Memory Peak:** ~2-3GB (1 chunk + processing overhead)
- **Processing Speed:** ~10-20 MB/s (depends on CPU/disk)
- **Time to process 100GB:** ~90-170 minutes

---

### PHASE 3: Parallel Job Scheduler

**File:** `src/parallel_scheduler.py` (NEW)

**Purpose:** Execute independent jobs for each (security_code, date) combination in parallel

**Architecture:**

```python
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import Pool, cpu_count
import queue
import threading

class ParallelJobScheduler:
    def __init__(self, max_workers: int = None):
        """
        Initialize scheduler
        
        Args:
            max_workers: Number of parallel processes (default: CPU count - 1)
        """
        self.max_workers = max_workers or (cpu_count() - 1)
        self.job_queue = queue.Queue()
        self.results = {}
        self.lock = threading.Lock()
        
    def submit_job(self, job_id: str, job_func, *args, **kwargs):
        """
        Submit a job for parallel execution
        
        Args:
            job_id: Unique identifier (e.g., 'SEC_101_2024-01-01')
            job_func: Function to execute
            args/kwargs: Arguments to function
        """
        job = {
            'id': job_id,
            'func': job_func,
            'args': args,
            'kwargs': kwargs,
            'status': 'queued',
            'start_time': None,
            'end_time': None,
            'duration': None,
            'error': None,
        }
        self.job_queue.put(job)
        logger.info(f"Submitted job: {job_id}")
        
    def execute_jobs(self) -> dict:
        """
        Execute all queued jobs in parallel
        
        Returns:
            {
                'job_id': {
                    'status': 'success' | 'failed',
                    'result': {...},
                    'error': None | error_message,
                    'duration_sec': 23.45,
                }
            }
        """
        jobs = []
        while not self.job_queue.empty():
            jobs.append(self.job_queue.get())
        
        logger.info(f"Executing {len(jobs)} jobs with {self.max_workers} workers")
        
        with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            
            for job in jobs:
                job['start_time'] = time.time()
                future = executor.submit(
                    job['func'],
                    *job['args'],
                    **job['kwargs']
                )
                futures[job['id']] = (job, future)
                logger.info(f"Started job: {job['id']}")
            
            # Collect results as they complete
            completed = 0
            for job_id, (job, future) in futures.items():
                try:
                    result = future.result(timeout=3600)  # 1 hour timeout
                    job['end_time'] = time.time()
                    job['duration'] = job['end_time'] - job['start_time']
                    job['status'] = 'success'
                    self.results[job_id] = {
                        'status': 'success',
                        'result': result,
                        'error': None,
                        'duration_sec': job['duration'],
                    }
                    completed += 1
                    logger.info(f"Completed job {job_id} in {job['duration']:.2f}s")
                except Exception as e:
                    job['end_time'] = time.time()
                    job['duration'] = job['end_time'] - job['start_time']
                    job['status'] = 'failed'
                    job['error'] = str(e)
                    self.results[job_id] = {
                        'status': 'failed',
                        'result': None,
                        'error': str(e),
                        'duration_sec': job['duration'],
                    }
                    logger.error(f"Failed job {job_id}: {e}")
        
        logger.info(f"Execution complete: {completed}/{len(jobs)} jobs succeeded")
        return self.results
```

**Job Execution Strategy:**

```python
def run_pipeline_job(security_code: int, date: str, config: dict) -> dict:
    """
    Execute full pipeline for a single (security_code, date) combination
    
    This function runs in a separate process (parallel execution)
    
    Args:
        security_code: Stock code (e.g., 101)
        date: Trading date (e.g., '2024-01-01')
        config: Configuration dict
        
    Returns:
        {
            'security_code': 101,
            'date': '2024-01-01',
            'orders_count': 1234,
            'trades_count': 567,
            'filled_orders': 1100,
            'fill_ratio': 0.95,
            'output_files': [...],
        }
    """
    job_id = f"SEC_{security_code}_{date}"
    logger.info(f"Starting job {job_id}")
    
    try:
        # Step 1: Ingest & Filter
        orders_df, trades_df = step1_ingest_chunk(
            security_code=security_code,
            date=date,
            config=config,
        )
        logger.info(f"[{job_id}] Step 1: Ingested {len(orders_df)} orders, {len(trades_df)} trades")
        
        # Step 2: Classification
        classified_df = step2_classify(orders_df, trades_df, config)
        logger.info(f"[{job_id}] Step 2: Classified {len(classified_df)} orders")
        
        # Step 4: Real Metrics
        metrics_df = step4_calculate_metrics(orders_df, trades_df, classified_df, config)
        logger.info(f"[{job_id}] Step 4: Calculated metrics")
        
        # Step 6: Simulation
        sim_df = step6_simulate(orders_df, trades_df, classified_df, metrics_df, config)
        logger.info(f"[{job_id}] Step 6: Completed simulation")
        
        # Save outputs for this job
        output_dir = f"processed_files/by_security_date/{security_code}/{date}/"
        os.makedirs(output_dir, exist_ok=True)
        
        orders_df.to_csv(f"{output_dir}/orders.csv.gz", compression='gzip', index=False)
        classified_df.to_csv(f"{output_dir}/classified.csv.gz", compression='gzip', index=False)
        metrics_df.to_csv(f"{output_dir}/metrics.csv", index=False)
        sim_df.to_csv(f"{output_dir}/simulation.csv.gz", compression='gzip', index=False)
        
        return {
            'security_code': security_code,
            'date': date,
            'orders_count': len(orders_df),
            'trades_count': len(trades_df),
            'filled_orders': len(classified_df[classified_df['fill_ratio'] > 0]),
            'fill_ratio': classified_df['fill_ratio'].mean(),
            'output_files': [
                f"{output_dir}/orders.csv.gz",
                f"{output_dir}/classified.csv.gz",
                f"{output_dir}/metrics.csv",
                f"{output_dir}/simulation.csv.gz",
            ],
            'status': 'success',
        }
        
    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}")
        return {
            'security_code': security_code,
            'date': date,
            'status': 'failed',
            'error': str(e),
        }
```

**Benefits:**
- **CPU Efficiency:** Uses all available cores
- **I/O Efficiency:** Parallel jobs can overlap disk I/O
- **Fault Tolerance:** Single job failure doesn't crash entire pipeline
- **Progress Tracking:** Know which jobs completed and which failed
- **Resource Management:** Queue-based execution prevents resource exhaustion

---

### PHASE 4: Refactored Step 1 Ingestion (Chunk-Based)

**File:** `src/ingest_chunked.py` (NEW - refactored from `src/ingest.py`)

**Purpose:** Extract specific (security_code, date) data from massive files using chunk iterator

**Algorithm:**

```python
def step1_ingest_chunk(
    security_code: int,
    date: str,
    config: dict,
    orders_file: str = 'data/orders/drr_orders.csv',
    trades_file: str = 'data/trades/drr_trades_segment_1.csv',
) -> tuple:
    """
    Extract specific security and date from massive files without loading entire files
    
    Uses chunk iterator to stream data efficiently
    
    Args:
        security_code: Target security code (e.g., 101)
        date: Target date in AEST (e.g., '2024-01-01')
        config: Scaling config
        orders_file: Path to orders file
        trades_file: Path to trades file
        
    Returns:
        (orders_df, trades_df) for the specified security and date
    """
    
    # Parse date range
    date_obj = pd.to_datetime(date).date()
    date_start = pd.Timestamp(date_obj, tz=timezone(timedelta(hours=10)))
    date_end = date_start + pd.Timedelta(days=1)
    
    # Extract filter parameters from config
    trading_hours = config['data_selection']['trading_hours']
    participant_ids = config['data_selection']['participant_ids']
    
    all_orders = []
    all_trades = []
    
    logger.info(f"Extracting SEC={security_code}, DATE={date}")
    
    # Process orders file in chunks
    logger.info(f"Streaming orders file in {config['processing']['chunk_size_mb']}MB chunks")
    with ChunkIterator(orders_file, config['processing']['chunk_size_mb']) as chunks:
        for chunk_idx, chunk_df in enumerate(chunks):
            # Convert timestamp to AEST datetime
            chunk_df['timestamp_dt'] = pd.to_datetime(
                chunk_df['timestamp'], unit='ns', utc=True
            ).dt.tz_convert(timezone(timedelta(hours=10)))
            
            # Apply filters to chunk
            filtered = chunk_df[
                (chunk_df['security_code'] == security_code) &
                (chunk_df['timestamp_dt'].dt.date == date_obj) &
                (chunk_df['timestamp_dt'].dt.hour >= trading_hours['start']) &
                (chunk_df['timestamp_dt'].dt.hour <= trading_hours['end']) &
                (chunk_df['participantid'].isin(participant_ids))
            ].copy()
            
            if len(filtered) > 0:
                # Keep only required columns
                filtered = filtered[[
                    'order_id', 'timestamp', 'security_code', 'price', 'side',
                    'quantity', 'leavesquantity', 'exchangeordertype', 'participantid',
                    'orderstatus', 'totalmatchedquantity'
                ]]
                all_orders.append(filtered)
                logger.info(f"  Chunk {chunk_idx}: Found {len(filtered)} matching orders")
    
    # Process trades file in chunks
    logger.info(f"Streaming trades file in {config['processing']['chunk_size_mb']}MB chunks")
    with ChunkIterator(trades_file, config['processing']['chunk_size_mb']) as chunks:
        for chunk_idx, chunk_df in enumerate(chunks):
            # Merge with orders to get security_code and timestamp
            chunk_with_orders = chunk_df.merge(
                all_orders[0][['order_id', 'security_code', 'timestamp']] if all_orders 
                else pd.DataFrame(),
                on='order_id',
                how='inner'
            )
            
            # Convert timestamp to AEST datetime
            chunk_with_orders['timestamp_dt'] = pd.to_datetime(
                chunk_with_orders['timestamp'], unit='ns', utc=True
            ).dt.tz_convert(timezone(timedelta(hours=10)))
            
            # Apply filters
            filtered = chunk_with_orders[
                (chunk_with_orders['security_code'] == security_code) &
                (chunk_with_orders['timestamp_dt'].dt.date == date_obj)
            ].copy()
            
            if len(filtered) > 0:
                all_trades.append(filtered)
                logger.info(f"  Chunk {chunk_idx}: Found {len(filtered)} matching trades")
    
    # Combine all chunks
    orders_df = pd.concat(all_orders, ignore_index=True) if all_orders else pd.DataFrame()
    trades_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    
    logger.info(f"Extraction complete: {len(orders_df)} orders, {len(trades_df)} trades")
    
    # Optimize data types
    if len(orders_df) > 0:
        orders_df = optimize_datatypes(orders_df)
    
    return orders_df, trades_df
```

**Efficiency Gains:**
- **100GB orders file:** ~5 minutes (vs. out-of-memory)
- **Multiple parallel jobs:** N × 5 minutes distributed across cores
- **Memory usage:** Constant ~2GB peak

---

### PHASE 5: Result Aggregation Layer

**File:** `src/result_aggregator.py` (NEW)

**Purpose:** Combine results from all (security_code, date) jobs into consolidated metrics

**Aggregation Levels:**

```python
class ResultAggregator:
    def __init__(self, results_dir: str, output_dir: str):
        """
        Initialize aggregator with results from all parallel jobs
        
        Args:
            results_dir: Directory containing per-(security, date) results
                        Structure: results_dir/SEC_{code}/{date}/
            output_dir: Directory for aggregated output
        """
        self.results_dir = results_dir
        self.output_dir = output_dir
        self.all_results = {}
        
    def load_all_results(self):
        """Load results from all completed jobs"""
        for sec_dir in os.listdir(self.results_dir):
            sec_code = int(sec_dir.split('_')[1])
            for date_dir in os.listdir(os.path.join(self.results_dir, sec_dir)):
                results_path = os.path.join(
                    self.results_dir, sec_dir, date_dir, 'metrics.csv'
                )
                if os.path.exists(results_path):
                    df = pd.read_csv(results_path)
                    df['security_code'] = sec_code
                    df['date'] = date_dir
                    self.all_results[f"{sec_code}_{date_dir}"] = df
    
    def aggregate_by_security(self) -> pd.DataFrame:
        """
        Aggregate metrics across all dates for each security
        
        Returns:
            DataFrame with columns:
            - security_code
            - total_orders
            - total_filled
            - avg_fill_ratio
            - total_cost
            - avg_dark_pool_savings
        """
        agg_data = []
        
        for sec_code in set(k.split('_')[0] for k in self.all_results.keys()):
            sec_results = [
                df for k, df in self.all_results.items() 
                if k.startswith(f"{sec_code}_")
            ]
            
            combined = pd.concat(sec_results, ignore_index=True)
            
            agg_data.append({
                'security_code': int(sec_code),
                'total_orders': len(combined),
                'total_filled': (combined['fill_ratio'] > 0).sum(),
                'avg_fill_ratio': combined['fill_ratio'].mean(),
                'total_quantity': combined['quantity'].sum(),
                'total_cost': (combined['quantity'] * combined['avg_execution_price']).sum(),
                'avg_dark_pool_savings_pct': combined['dark_pool_savings_pct'].mean(),
                'num_dates': len(set(combined['date'])),
            })
        
        agg_df = pd.DataFrame(agg_data)
        agg_df.to_csv(
            os.path.join(self.output_dir, 'by_security.csv'),
            index=False
        )
        logger.info(f"Saved aggregation by security: {len(agg_df)} securities")
        return agg_df
    
    def aggregate_by_date(self) -> pd.DataFrame:
        """Aggregate metrics across all securities for each date"""
        agg_data = []
        
        for date in set(k.split('_', 1)[1] for k in self.all_results.keys()):
            date_results = [
                df for k, df in self.all_results.items() 
                if k.endswith(f"_{date}")
            ]
            
            combined = pd.concat(date_results, ignore_index=True)
            
            agg_data.append({
                'date': date,
                'total_orders': len(combined),
                'total_filled': (combined['fill_ratio'] > 0).sum(),
                'avg_fill_ratio': combined['fill_ratio'].mean(),
                'total_quantity': combined['quantity'].sum(),
                'total_cost': (combined['quantity'] * combined['avg_execution_price']).sum(),
                'avg_dark_pool_savings_pct': combined['dark_pool_savings_pct'].mean(),
                'num_securities': len(set(combined['security_code'])),
            })
        
        agg_df = pd.DataFrame(agg_data).sort_values('date')
        agg_df.to_csv(
            os.path.join(self.output_dir, 'by_date.csv'),
            index=False
        )
        logger.info(f"Saved aggregation by date: {len(agg_df)} dates")
        return agg_df
    
    def aggregate_by_participant(self) -> pd.DataFrame:
        """Aggregate metrics across all securities and dates by participant"""
        all_dfs = [df for df in self.all_results.values()]
        combined = pd.concat(all_dfs, ignore_index=True)
        
        agg_data = []
        for participant_id in combined['participantid'].unique():
            participant_data = combined[combined['participantid'] == participant_id]
            
            agg_data.append({
                'participant_id': participant_id,
                'total_orders': len(participant_data),
                'total_filled': (participant_data['fill_ratio'] > 0).sum(),
                'avg_fill_ratio': participant_data['fill_ratio'].mean(),
                'total_quantity': participant_data['quantity'].sum(),
                'num_securities': len(set(participant_data['security_code'])),
                'num_dates': len(set(participant_data['date'])),
            })
        
        agg_df = pd.DataFrame(agg_data)
        agg_df.to_csv(
            os.path.join(self.output_dir, 'by_participant.csv'),
            index=False
        )
        logger.info(f"Saved aggregation by participant: {len(agg_df)} participants")
        return agg_df
    
    def generate_global_summary(self) -> pd.DataFrame:
        """Generate global summary across all data"""
        all_dfs = [df for df in self.all_results.values()]
        combined = pd.concat(all_dfs, ignore_index=True)
        
        summary = {
            'metric': [
                'total_orders',
                'total_filled',
                'total_failed',
                'global_fill_ratio',
                'total_quantity_traded',
                'total_cost',
                'avg_execution_price',
                'avg_dark_pool_savings',
                'num_securities',
                'num_dates',
                'num_participants',
            ],
            'value': [
                len(combined),
                (combined['fill_ratio'] > 0).sum(),
                (combined['fill_ratio'] == 0).sum(),
                combined['fill_ratio'].mean(),
                combined['quantity'].sum(),
                (combined['quantity'] * combined['avg_execution_price']).sum(),
                combined['avg_execution_price'].mean(),
                combined['dark_pool_savings'].mean(),
                len(set(combined['security_code'])),
                len(set(combined['date'])),
                len(set(combined['participantid'])),
            ]
        }
        
        summary_df = pd.DataFrame(summary)
        summary_df.to_csv(
            os.path.join(self.output_dir, 'global_summary.csv'),
            index=False
        )
        logger.info(f"Saved global summary")
        return summary_df
    
    def run_all_aggregations(self):
        """Execute all aggregations"""
        logger.info("Starting result aggregation...")
        self.load_all_results()
        self.aggregate_by_security()
        self.aggregate_by_date()
        self.aggregate_by_participant()
        self.generate_global_summary()
        logger.info("Result aggregation complete")
```

**Output Structure:**

```
processed_files/
├── by_security/
│   ├── SEC_101/
│   │   ├── 2024-01-01/
│   │   │   ├── orders.csv.gz
│   │   │   ├── classified.csv.gz
│   │   │   ├── metrics.csv
│   │   │   └── simulation.csv.gz
│   │   ├── 2024-01-02/
│   │   └── ...
│   ├── SEC_102/
│   └── ...
│
└── aggregated/
    ├── global_summary.csv ............ Across all data
    ├── by_security.csv .............. Aggregated per security
    ├── by_date.csv .................. Aggregated per date
    ├── by_participant.csv ........... Aggregated per participant
    └── time_series_analysis.csv ..... Temporal trends
```

---

### PHASE 6: Monitoring & Logging

**File:** `src/job_monitor.py` (NEW)

**Purpose:** Track parallel job execution with progress indicators

**Features:**

```python
class JobMonitor:
    def __init__(self, total_jobs: int):
        self.total_jobs = total_jobs
        self.completed = 0
        self.failed = 0
        self.start_time = time.time()
        self.job_times = {}
        
    def log_job_start(self, job_id: str):
        self.job_times[job_id] = {'start': time.time()}
        
    def log_job_complete(self, job_id: str, status: str = 'success'):
        if job_id in self.job_times:
            self.job_times[job_id]['end'] = time.time()
            self.job_times[job_id]['status'] = status
            
            if status == 'success':
                self.completed += 1
            else:
                self.failed += 1
            
            duration = (self.job_times[job_id]['end'] - 
                       self.job_times[job_id]['start'])
            
            progress_pct = (self.completed + self.failed) / self.total_jobs * 100
            elapsed = time.time() - self.start_time
            avg_time = elapsed / (self.completed + self.failed) if (self.completed + self.failed) > 0 else 0
            eta_sec = avg_time * (self.total_jobs - self.completed - self.failed)
            
            logger.info(
                f"[{self.completed}/{self.total_jobs}] ✓ {job_id} "
                f"({duration:.1f}s) | "
                f"Progress: {progress_pct:.1f}% | "
                f"ETA: {eta_sec/60:.1f} min"
            )
    
    def print_summary(self):
        total_time = time.time() - self.start_time
        logger.info(f"\n{'='*80}")
        logger.info(f"EXECUTION SUMMARY")
        logger.info(f"{'='*80}")
        logger.info(f"Total jobs: {self.total_jobs}")
        logger.info(f"Completed: {self.completed}")
        logger.info(f"Failed: {self.failed}")
        logger.info(f"Total time: {total_time/60:.1f} minutes")
        logger.info(f"Avg time per job: {total_time/self.total_jobs:.1f}s")
        
        # Slowest and fastest jobs
        times = [
            (jid, t['end'] - t['start']) 
            for jid, t in self.job_times.items() 
            if 'end' in t
        ]
        times.sort(key=lambda x: x[1])
        logger.info(f"\nFastest job: {times[0][0]} ({times[0][1]:.1f}s)")
        logger.info(f"Slowest job: {times[-1][0]} ({times[-1][1]:.1f}s)")
```

---

### PHASE 7: Test Harness

**File:** `tests/test_scaling.py` (NEW)

**Purpose:** Validate scaling implementation with sample data

**Features:**

- Generate synthetic multi-date/multi-security test data
- Test chunk iterator with various file sizes
- Test parallel execution with 2-4 jobs
- Validate aggregation logic
- Benchmark memory and CPU usage

---

### PHASE 8: Performance Optimization

**Key Optimizations:**

1. **Chunk Size Tuning**
   - Test chunk sizes: 512MB, 1GB, 2GB
   - Find optimal balance between I/O and memory

2. **Parallel Worker Count**
   - Test 2, 4, 8, 16 workers
   - Monitor CPU and I/O saturation

3. **Data Type Optimization**
   - Use appropriate numpy types
   - Compress intermediate results

4. **I/O Optimization**
   - Use gzip compression
   - Consider Parquet format for complex data

**Benchmark Script:**

```python
def benchmark_pipeline(config):
    """
    Run pipeline on sample data and measure:
    - Wall-clock time
    - Peak memory usage
    - CPU utilization
    - Throughput (rows/sec)
    """
    pass
```

---

## IMPLEMENTATION TIMELINE

| Phase | Task | Effort | Timeline |
|-------|------|--------|----------|
| 1 | Enhanced config | 2 hours | Day 1 |
| 2 | Chunk iterator | 3 hours | Day 1-2 |
| 3 | Parallel scheduler | 4 hours | Day 2-3 |
| 4 | Refactored ingest | 3 hours | Day 3 |
| 5 | Result aggregator | 3 hours | Day 4 |
| 6 | Monitoring & logging | 2 hours | Day 4 |
| 7 | Test harness | 4 hours | Day 5 |
| 8 | Benchmarking & optimization | 4 hours | Day 5-6 |
| **Total** | | **25 hours** | **~1 week** |

---

## EXPECTED PERFORMANCE

### Input Characteristics
```
Orders file:    200GB, ~2 billion rows
Trades file:    200GB, ~1 billion rows
Securities:     150 codes
Dates:          365 days
Total jobs:     150 × 365 = 54,750 jobs
```

### Processing Performance
```
Sequential (current):
  - Processing time: ~500 hours per full dataset
  - Memory: Out of memory (file too large)
  - Result: IMPOSSIBLE

Chunk-based sequential:
  - Processing time: ~200 hours per full dataset
  - Memory: ~2-3 GB peak
  - Result: Feasible but slow

Chunk-based parallel (8 workers):
  - Processing time: ~25-30 hours per full dataset
  - Memory: ~20-24 GB peak (2-3GB × 8 workers)
  - Result: ✅ PRACTICAL & EFFICIENT
```

### Memory Efficiency
```
File Size    Chunk Size    Peak Memory    Time to Process
100GB        1GB          ~2GB           ~50 min
500GB        1GB          ~2GB           ~250 min
1TB          1GB          ~2GB           ~500 min (8 workers: ~60 min)
```

---

## KEY DESIGN DECISIONS

### 1. Why Chunk Iterator Instead of Dask/Spark?
- **Simplicity:** No new dependencies, pure pandas/numpy
- **Control:** Direct memory management
- **Debugging:** Easier to troubleshoot
- **Performance:** Comparable to Spark for this use case
- **Infrastructure:** Runs on single machine or cluster

### 2. Why Multiprocessing Instead of Threading?
- **GIL:** Python's Global Interpreter Lock limits thread efficiency
- **CPU-bound:** Data processing is CPU-intensive
- **Scalability:** Multiprocessing scales to all cores
- **Isolation:** Process crashes don't affect other jobs

### 3. Why Per-(Security, Date) Jobs?
- **Independence:** No data dependencies between jobs
- **Scalability:** Linear speedup with worker count
- **Fault tolerance:** Single job failure doesn't crash pipeline
- **Flexibility:** Easy to retry failed jobs
- **Analytics:** Natural aggregation dimensions

### 4. Why Gzip + CSV Instead of Parquet?
- **Compatibility:** Works with any downstream system
- **Debuggability:** Human-readable data
- **Compression:** Similar compression ratio to Parquet
- **Speed:** Fast read/write
- **Note:** Can add Parquet as alternative output format

---

## NEXT STEPS

Choose your implementation approach:

### Option A: Quick Start (Days 1-3)
Focus on Phases 1-4 only
- Enhanced config
- Chunk iterator
- Parallel scheduler
- Refactored ingest
- **Result:** Can process multi-date data in parallel

### Option B: Production Ready (Days 1-6)
Implement all phases 1-8
- Everything in Option A
- Result aggregation
- Monitoring
- Testing
- Optimization
- **Result:** Fully hardened, production-ready system

### Option C: Custom (Your Choice)
Pick specific phases you want to implement

---

## TECHNICAL DEBT & FUTURE WORK

1. **Distributed Processing:** Add Dask/Spark support for cloud deployment
2. **Database Integration:** Store results in PostgreSQL/BigQuery
3. **Real-time Monitoring:** Web dashboard for live job tracking
4. **Caching:** Cache chunk parsing results to avoid re-reading
5. **Incremental Processing:** Only process new dates/securities
6. **Machine Learning:** Predictive models based on aggregated data

---

**Ready to implement? Let me know which option you'd like to pursue!**

