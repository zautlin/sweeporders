# Dynamic System Configuration Plan
## Auto-tuning Workers and Chunk Size Based on System Resources

---

## 1. Overview

### Objective
Make the pipeline dynamically adapt to system resources by:
1. **Auto-detecting CPU cores** for parallel processing
2. **Auto-calculating optimal chunk size** based on available memory
3. **Configuring worker pool** for partition processing
4. **Monitoring resource usage** during execution

### Benefits
- Better performance on high-end machines
- Safer execution on low-resource systems
- No manual configuration needed
- Automatic scaling

---

## 2. System Configuration Strategy

### 2.1 CPU Detection

**Approach**: Use `multiprocessing.cpu_count()` or `os.cpu_count()`

**Worker Calculation**:
```python
import multiprocessing
import os

def get_optimal_workers():
    """
    Calculate optimal number of workers based on CPU cores.
    
    Strategy:
    - Leave 1-2 cores free for system operations
    - Cap at reasonable maximum (e.g., 16) to avoid overhead
    - Minimum of 1 worker
    """
    cpu_count = multiprocessing.cpu_count()
    
    if cpu_count <= 2:
        return 1  # Single core or dual core: no parallelization
    elif cpu_count <= 4:
        return cpu_count - 1  # Leave 1 core free
    elif cpu_count <= 8:
        return cpu_count - 2  # Leave 2 cores free
    else:
        return min(cpu_count - 2, 16)  # Cap at 16 workers
```

### 2.2 Memory Detection

**Approach**: Use `psutil` library for accurate memory detection

**Chunk Size Calculation**:
```python
import psutil

def get_optimal_chunk_size():
    """
    Calculate optimal chunk size based on available memory.
    
    Strategy:
    - Target 5-10% of available memory per chunk
    - Estimate ~1KB per row average
    - Min: 10,000 rows
    - Max: 500,000 rows
    - Default: 100,000 rows
    """
    try:
        # Get available memory in bytes
        available_memory = psutil.virtual_memory().available
        
        # Estimate chunk size (assuming ~1KB per row)
        # Target 5% of available memory per chunk
        target_memory_per_chunk = available_memory * 0.05
        estimated_chunk_size = int(target_memory_per_chunk / 1024)  # 1KB per row
        
        # Apply constraints
        chunk_size = max(10_000, min(estimated_chunk_size, 500_000))
        
        return chunk_size
    except Exception:
        # Fallback to default
        return 100_000
```

### 2.3 Configuration Class

Create a new `SystemConfig` class in `src/system_config.py`:

```python
"""
System Configuration Module

Auto-detects system resources and calculates optimal configuration
for parallel processing and memory management.
"""

import multiprocessing
import os
import psutil
from dataclasses import dataclass
from typing import Optional


@dataclass
class SystemConfig:
    """System configuration based on detected resources."""
    cpu_count: int
    num_workers: int
    available_memory_gb: float
    chunk_size: int
    enable_parallel: bool
    
    def __str__(self):
        return (
            f"SystemConfig(\n"
            f"  CPU Cores: {self.cpu_count}\n"
            f"  Workers: {self.num_workers}\n"
            f"  Available Memory: {self.available_memory_gb:.2f} GB\n"
            f"  Chunk Size: {self.chunk_size:,}\n"
            f"  Parallel Processing: {'Enabled' if self.enable_parallel else 'Disabled'}\n"
            f")"
        )


def detect_system_config(
    override_workers: Optional[int] = None,
    override_chunk_size: Optional[int] = None
) -> SystemConfig:
    """
    Detect system configuration and calculate optimal settings.
    
    Args:
        override_workers: Manual override for number of workers (optional)
        override_chunk_size: Manual override for chunk size (optional)
    
    Returns:
        SystemConfig object with optimal settings
    """
    # Detect CPU cores
    cpu_count = multiprocessing.cpu_count()
    
    # Calculate optimal workers
    if override_workers is not None:
        num_workers = max(1, override_workers)
    else:
        num_workers = _calculate_optimal_workers(cpu_count)
    
    # Detect memory
    memory_info = psutil.virtual_memory()
    available_memory_gb = memory_info.available / (1024 ** 3)
    
    # Calculate optimal chunk size
    if override_chunk_size is not None:
        chunk_size = max(1000, override_chunk_size)
    else:
        chunk_size = _calculate_optimal_chunk_size(memory_info.available)
    
    # Enable parallel only if we have multiple workers
    enable_parallel = num_workers > 1
    
    config = SystemConfig(
        cpu_count=cpu_count,
        num_workers=num_workers,
        available_memory_gb=available_memory_gb,
        chunk_size=chunk_size,
        enable_parallel=enable_parallel
    )
    
    return config


def _calculate_optimal_workers(cpu_count: int) -> int:
    """Calculate optimal number of workers based on CPU count."""
    if cpu_count <= 2:
        return 1
    elif cpu_count <= 4:
        return cpu_count - 1
    elif cpu_count <= 8:
        return cpu_count - 2
    else:
        return min(cpu_count - 2, 16)


def _calculate_optimal_chunk_size(available_memory: int) -> int:
    """
    Calculate optimal chunk size based on available memory.
    
    Args:
        available_memory: Available memory in bytes
    
    Returns:
        Optimal chunk size (number of rows)
    """
    # Target 5% of available memory per chunk
    target_memory_per_chunk = available_memory * 0.05
    
    # Estimate ~1KB per row (conservative estimate)
    estimated_chunk_size = int(target_memory_per_chunk / 1024)
    
    # Apply constraints: min 10K, max 500K
    chunk_size = max(10_000, min(estimated_chunk_size, 500_000))
    
    return chunk_size


def get_config_with_overrides(
    workers: Optional[int] = None,
    chunk_size: Optional[int] = None
) -> SystemConfig:
    """
    Get system configuration with optional overrides from environment variables.
    
    Environment Variables:
        SWEEP_WORKERS: Number of worker processes
        SWEEP_CHUNK_SIZE: Chunk size for reading CSV files
    
    Args:
        workers: Direct override for workers
        chunk_size: Direct override for chunk size
    
    Returns:
        SystemConfig object
    """
    # Check environment variables
    env_workers = os.getenv('SWEEP_WORKERS')
    env_chunk_size = os.getenv('SWEEP_CHUNK_SIZE')
    
    # Priority: function args > env vars > auto-detect
    final_workers = workers
    if final_workers is None and env_workers is not None:
        try:
            final_workers = int(env_workers)
        except ValueError:
            pass
    
    final_chunk_size = chunk_size
    if final_chunk_size is None and env_chunk_size is not None:
        try:
            final_chunk_size = int(env_chunk_size)
        except ValueError:
            pass
    
    return detect_system_config(
        override_workers=final_workers,
        override_chunk_size=final_chunk_size
    )
```

---

## 3. Code Changes Required

### 3.1 Create New File: `src/system_config.py`

Status: **NEW FILE**

Content: See Section 2.3 above

### 3.2 Update `src/main.py`

**Changes**:

1. **Import system_config module**:
```python
import system_config as sc
```

2. **Replace static configuration**:
```python
# OLD:
CHUNK_SIZE = 100000

# NEW:
# System configuration (auto-detected)
SYSTEM_CONFIG = sc.get_config_with_overrides()
CHUNK_SIZE = SYSTEM_CONFIG.chunk_size
NUM_WORKERS = SYSTEM_CONFIG.num_workers
```

3. **Update main() function to print config**:
```python
def main():
    """Main execution function."""
    start_time = time.time()
    
    print("="*80)
    print("CENTRE POINT SWEEP ORDER MATCHING PIPELINE")
    print("="*80)
    
    # Print system configuration
    print("\nSystem Configuration:")
    print(SYSTEM_CONFIG)
    
    print(f"\nProcessed directory: {PROCESSED_DIR}/")
    print(f"Outputs directory: {OUTPUTS_DIR}/")
    
    # ... rest of main()
```

4. **Add parallel processing for partitions** (optional enhancement):
```python
def simulate_sweep_matching_parallel(orders_by_partition, nbbo_by_partition, output_dir):
    """Step 8: Simulate sweep matching with parallel processing."""
    print("\n[8/11] Simulating sweep matching...")
    
    if not SYSTEM_CONFIG.enable_parallel:
        # Fall back to sequential processing
        return simulate_sweep_matching(orders_by_partition, nbbo_by_partition, output_dir)
    
    print(f"   Using {NUM_WORKERS} parallel workers")
    
    from multiprocessing import Pool
    
    simulation_results_by_partition = {}
    
    # Prepare tasks
    tasks = []
    for partition_key in orders_by_partition.keys():
        tasks.append((partition_key, PROCESSED_DIR, nbbo_by_partition.get(partition_key)))
    
    # Process in parallel
    with Pool(processes=NUM_WORKERS) as pool:
        results = pool.starmap(_simulate_partition_task, tasks)
    
    # Collect results
    for partition_key, result in zip(orders_by_partition.keys(), results):
        if result:
            simulation_results_by_partition[partition_key] = result
            
            # Save results
            partition_output_dir = Path(output_dir) / partition_key
            partition_output_dir.mkdir(parents=True, exist_ok=True)
            
            result['order_summary'].to_csv(
                partition_output_dir / 'simulation_order_summary.csv', 
                index=False
            )
            result['match_details'].to_csv(
                partition_output_dir / 'simulation_match_details.csv', 
                index=False
            )
    
    print(f"   Completed sweep simulation for {len(simulation_results_by_partition)} partitions")
    return simulation_results_by_partition


def _simulate_partition_task(partition_key, processed_dir, nbbo_data):
    """Worker task for parallel simulation."""
    # Load partition data
    partition_data = dp.load_partition_data(partition_key, processed_dir)
    
    if not partition_data or 'orders_before' not in partition_data:
        return None
    
    # Add NBBO data
    partition_data['nbbo'] = nbbo_data
    
    # Run simulation
    return ss.simulate_partition(partition_key, partition_data)
```

### 3.3 Update `src/data_processor.py`

**Changes**:

1. **Make chunk_size a parameter everywhere**:
```python
# Already has chunk_size parameter in extract_orders()
# Update extract_trades() to accept chunk_size:

def extract_trades(input_file, orders_by_partition, processed_dir, column_mapping, chunk_size=100000):
    """
    Extract trades matching order_ids from partitions.
    
    Args:
        ...
        chunk_size: Chunk size for reading CSV (default: 100000)
    """
    # Replace hardcoded chunk_size = 100000 with parameter
```

2. **Add parallel processing helper** (optional):
```python
def process_partitions_parallel(partition_keys, process_func, num_workers):
    """
    Process partitions in parallel using multiprocessing.
    
    Args:
        partition_keys: List of partition keys to process
        process_func: Function to process each partition
        num_workers: Number of worker processes
    
    Returns:
        Dictionary mapping partition_key to results
    """
    from multiprocessing import Pool
    
    with Pool(processes=num_workers) as pool:
        results = pool.map(process_func, partition_keys)
    
    return dict(zip(partition_keys, results))
```

### 3.4 Update `requirements.txt`

**Add**:
```
psutil>=5.9.0
```

---

## 4. Implementation Phases

### Phase 1: Basic Auto-Detection (No Parallel Processing)
**Priority**: HIGH
**Effort**: 2-3 hours

**Tasks**:
1. Create `src/system_config.py` with basic detection
2. Update `src/main.py` to use auto-detected config
3. Update `src/data_processor.py` to accept chunk_size parameter
4. Test on different machine configurations
5. Update documentation

**Files Changed**:
- `src/system_config.py` (NEW)
- `src/main.py` (MODIFIED)
- `src/data_processor.py` (MODIFIED)
- `requirements.txt` (MODIFIED)
- `docs/TECHNICAL_SPECIFICATION.md` (MODIFIED)

### Phase 2: Parallel Partition Processing
**Priority**: MEDIUM
**Effort**: 4-6 hours

**Tasks**:
1. Add parallel processing for simulation stage
2. Add parallel processing for data extraction stages
3. Handle shared state and file I/O carefully
4. Add progress tracking for parallel jobs
5. Test with various partition counts

**Files Changed**:
- `src/main.py` (MODIFIED - add parallel functions)
- `src/data_processor.py` (MODIFIED - add parallel helpers)
- `src/sweep_simulator.py` (VERIFY - ensure thread-safe)

### Phase 3: Advanced Optimizations
**Priority**: LOW
**Effort**: 3-4 hours

**Tasks**:
1. Add memory monitoring during execution
2. Add adaptive chunk size (adjust if memory pressure detected)
3. Add progress bars with `tqdm`
4. Add performance profiling
5. Add resource usage logging

**Files Changed**:
- `src/system_config.py` (MODIFIED)
- `src/main.py` (MODIFIED)
- Add new `src/performance_monitor.py`

---

## 5. Configuration Options

### 5.1 Command Line Arguments

Add argparse support in `main.py`:

```python
import argparse

def parse_args():
    parser = argparse.ArgumentParser(
        description='Centre Point Sweep Order Matching Pipeline'
    )
    parser.add_argument(
        '--workers', 
        type=int, 
        default=None,
        help='Number of worker processes (default: auto-detect)'
    )
    parser.add_argument(
        '--chunk-size', 
        type=int, 
        default=None,
        help='Chunk size for CSV reading (default: auto-detect)'
    )
    parser.add_argument(
        '--no-parallel',
        action='store_true',
        help='Disable parallel processing'
    )
    parser.add_argument(
        '--profile',
        action='store_true',
        help='Enable performance profiling'
    )
    return parser.parse_args()
```

### 5.2 Environment Variables

Support environment variable configuration:

```bash
# Set number of workers
export SWEEP_WORKERS=8

# Set chunk size
export SWEEP_CHUNK_SIZE=200000

# Run pipeline
python main.py
```

### 5.3 Configuration File

Optional: Add `config.yaml` support:

```yaml
system:
  workers: auto  # or specific number
  chunk_size: auto  # or specific number
  enable_parallel: true
  
performance:
  memory_limit_gb: null  # null = use 90% of available
  monitoring: true
  progress_bars: true
```

---

## 6. Testing Strategy

### 6.1 Unit Tests

Create `tests/test_system_config.py`:

```python
import pytest
from src import system_config as sc

def test_detect_system_config():
    config = sc.detect_system_config()
    assert config.cpu_count > 0
    assert config.num_workers > 0
    assert config.chunk_size >= 10_000
    assert config.chunk_size <= 500_000

def test_worker_calculation():
    assert sc._calculate_optimal_workers(1) == 1
    assert sc._calculate_optimal_workers(2) == 1
    assert sc._calculate_optimal_workers(4) == 3
    assert sc._calculate_optimal_workers(8) == 6
    assert sc._calculate_optimal_workers(32) == 16  # capped

def test_chunk_size_calculation():
    # 8GB available memory
    memory_8gb = 8 * 1024 ** 3
    chunk = sc._calculate_optimal_chunk_size(memory_8gb)
    assert 10_000 <= chunk <= 500_000

def test_override_workers():
    config = sc.detect_system_config(override_workers=4)
    assert config.num_workers == 4

def test_override_chunk_size():
    config = sc.detect_system_config(override_chunk_size=50000)
    assert config.chunk_size == 50000
```

### 6.2 Integration Tests

Test on different machine configurations:

1. **Low-end**: 2 cores, 4GB RAM
   - Expected: 1 worker, ~20K chunk size
   
2. **Mid-range**: 4 cores, 16GB RAM
   - Expected: 3 workers, ~80K chunk size
   
3. **High-end**: 16 cores, 64GB RAM
   - Expected: 14 workers, ~300K chunk size

### 6.3 Performance Benchmarks

Compare execution times:
- Sequential vs Parallel
- Various chunk sizes
- Various worker counts

---

## 7. Risk Analysis

### 7.1 Potential Issues

| Risk | Impact | Mitigation |
|------|--------|------------|
| Memory exhaustion with large chunks | HIGH | Cap chunk size at 500K rows |
| Too many workers causing overhead | MEDIUM | Cap at 16 workers |
| File I/O contention in parallel | MEDIUM | Use sequential I/O with parallel compute |
| Platform-specific issues | LOW | Test on multiple OSes |
| psutil not installed | LOW | Graceful fallback to defaults |

### 7.2 Fallback Strategy

If any error occurs:
1. Log warning
2. Fall back to default values (1 worker, 100K chunk)
3. Continue execution
4. Report issue in summary

---

## 8. Documentation Updates

### 8.1 README.md Updates

Add section on configuration:

```markdown
## Configuration

The pipeline automatically detects system resources and optimizes:
- Number of parallel workers (based on CPU cores)
- Chunk size for CSV reading (based on available memory)

### Manual Override

Via command line:
\`\`\`bash
python main.py --workers 8 --chunk-size 200000
\`\`\`

Via environment variables:
\`\`\`bash
export SWEEP_WORKERS=8
export SWEEP_CHUNK_SIZE=200000
python main.py
\`\`\`

### Disable Parallel Processing

\`\`\`bash
python main.py --no-parallel
\`\`\`
```

### 8.2 TECHNICAL_SPECIFICATION.md Updates

Add new section:

```markdown
## 9. System Configuration & Performance

### Auto-Detection
- CPU cores: `multiprocessing.cpu_count()`
- Memory: `psutil.virtual_memory().available`
- Workers: CPU cores - 2 (capped at 16)
- Chunk size: 5% of available memory

### Parallel Processing
- Partition-level parallelization
- Simulation runs in parallel per partition
- Data extraction remains sequential (I/O bound)
```

---

## 9. Rollout Plan

### Step 1: Create system_config.py
- Implement basic detection
- Add unit tests
- Verify on local machine

### Step 2: Update main.py (non-parallel)
- Integrate system_config
- Replace hardcoded values
- Test with auto-detection

### Step 3: Update data_processor.py
- Add chunk_size parameter
- Pass through from main
- Test with various chunk sizes

### Step 4: Add parallel processing (optional)
- Implement parallel simulation
- Add worker pool management
- Test with multiple partitions

### Step 5: Documentation
- Update all docs
- Add configuration guide
- Create troubleshooting section

### Step 6: Testing & Validation
- Run benchmarks
- Test on different systems
- Collect performance metrics

---

## 10. Success Criteria

✅ Pipeline automatically detects system resources
✅ No manual configuration required by default
✅ Performance improves on multi-core systems
✅ Graceful degradation on low-resource systems
✅ All existing functionality preserved
✅ Comprehensive documentation updated
✅ Tests pass on various configurations

---

## 11. Estimated Timeline

| Phase | Duration | Dependencies |
|-------|----------|--------------|
| Phase 1: Basic Auto-Detection | 2-3 hours | None |
| Phase 2: Parallel Processing | 4-6 hours | Phase 1 |
| Phase 3: Advanced Optimizations | 3-4 hours | Phase 1, 2 |
| Testing & Validation | 2-3 hours | All phases |
| Documentation | 1-2 hours | All phases |
| **Total** | **12-18 hours** | |

---

## 12. Next Steps

1. ✅ Review this plan
2. ⬜ Approve Phase 1 implementation
3. ⬜ Create feature branch
4. ⬜ Implement system_config.py
5. ⬜ Update main.py and data_processor.py
6. ⬜ Add tests
7. ⬜ Test on various systems
8. ⬜ Update documentation
9. ⬜ Merge to main
10. ⬜ Decide on Phase 2 implementation

**Recommended**: Start with Phase 1 only for safety and testing.
