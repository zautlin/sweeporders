# Dynamic Configuration Implementation - Quick Summary

## Overview
Make chunk size and number of workers dynamic based on system configuration.

## Quick Plan

### Phase 1: Auto-Detection (RECOMMENDED START)
**Time**: 2-3 hours  
**Risk**: Low

**What to implement**:
1. Create `src/system_config.py` - auto-detects CPU and memory
2. Update `src/main.py` - use detected config instead of hardcoded values
3. Update `src/data_processor.py` - make chunk_size a parameter
4. Add `psutil` to `requirements.txt`

**Key Features**:
- Auto-detect CPU cores → calculate optimal workers
- Auto-detect RAM → calculate optimal chunk size
- Support manual overrides via environment variables
- Graceful fallback to defaults if detection fails

**Example Output**:
```
System Configuration:
  CPU Cores: 8
  Workers: 6
  Available Memory: 15.23 GB
  Chunk Size: 80,000
  Parallel Processing: Enabled
```

---

## Implementation Steps

### Step 1: Create `src/system_config.py`
```python
import multiprocessing
import psutil
from dataclasses import dataclass

@dataclass
class SystemConfig:
    cpu_count: int
    num_workers: int
    available_memory_gb: float
    chunk_size: int
    enable_parallel: bool

def detect_system_config():
    cpu_count = multiprocessing.cpu_count()
    num_workers = _calculate_workers(cpu_count)
    memory = psutil.virtual_memory().available
    chunk_size = _calculate_chunk_size(memory)
    
    return SystemConfig(...)

def _calculate_workers(cpu_count):
    # Logic: leave 1-2 cores free, cap at 16
    if cpu_count <= 2: return 1
    elif cpu_count <= 4: return cpu_count - 1
    else: return min(cpu_count - 2, 16)

def _calculate_chunk_size(available_memory):
    # Target 5% of available memory per chunk
    # Estimate ~1KB per row
    target = available_memory * 0.05
    chunk = int(target / 1024)
    return max(10_000, min(chunk, 500_000))
```

### Step 2: Update `src/main.py`
```python
import system_config as sc

# OLD:
CHUNK_SIZE = 100000

# NEW:
SYSTEM_CONFIG = sc.detect_system_config()
CHUNK_SIZE = SYSTEM_CONFIG.chunk_size
NUM_WORKERS = SYSTEM_CONFIG.num_workers

def main():
    print("\nSystem Configuration:")
    print(SYSTEM_CONFIG)
    # ... rest of main
```

### Step 3: Update `src/data_processor.py`
```python
# Update function signature:
def extract_trades(input_file, orders_by_partition, processed_dir, 
                   column_mapping, chunk_size=100000):
    # Use chunk_size parameter instead of hardcoded value
    for chunk in pd.read_csv(input_file, chunksize=chunk_size, ...):
        ...
```

### Step 4: Update `requirements.txt`
```
pandas
numpy
scipy
psutil>=5.9.0
```

---

## Configuration Options

### Environment Variables
```bash
export SWEEP_WORKERS=8           # Override workers
export SWEEP_CHUNK_SIZE=200000   # Override chunk size
python src/main.py
```

### Command Line (future)
```bash
python src/main.py --workers 8 --chunk-size 200000
```

---

## Testing Scenarios

| System | Expected Config |
|--------|----------------|
| 2 cores, 4GB RAM | 1 worker, ~20K chunk |
| 4 cores, 16GB RAM | 3 workers, ~80K chunk |
| 16 cores, 64GB RAM | 14 workers, ~300K chunk |

---

## Rollout

1. ✅ Review plan
2. Create `system_config.py`
3. Update `main.py` and `data_processor.py`
4. Test on your machine
5. Update documentation
6. Commit changes

---

## Phase 2 (Optional - Later)

Add parallel processing for partitions:
- Use `multiprocessing.Pool` for partition-level parallelization
- Simulate multiple partitions concurrently
- Time: 4-6 hours
- Risk: Medium

**Not needed immediately** - Phase 1 gives most benefits with low risk.

---

## Files to Create/Modify

**NEW**:
- `src/system_config.py`

**MODIFIED**:
- `src/main.py`
- `src/data_processor.py`
- `requirements.txt`
- `docs/TECHNICAL_SPECIFICATION.md`
- `README.md`

---

## Recommendation

**START WITH PHASE 1 ONLY**

Benefits:
- Auto-detection works immediately
- No parallel complexity
- Easy to test and debug
- Low risk of breaking existing code
- Gets 80% of benefits

Once Phase 1 is stable and tested, then consider Phase 2 for parallel processing.
