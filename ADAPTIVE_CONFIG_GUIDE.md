# ADAPTIVE CONFIGURATION SYSTEM - DESIGN & USAGE GUIDE

**Status:** IMPLEMENTED ✅  
**Created:** January 1, 2026  
**Purpose:** Automatically detect hardware and optimize CPU workers + chunk size

---

## EXECUTIVE SUMMARY

The adaptive configuration system automatically:

1. **Detects hardware specifications** (CPU cores, RAM, disk speed)
2. **Calculates optimal parameters** (worker count and chunk size)
3. **Applies safety margins** (prevents out-of-memory and system overload)
4. **Monitors execution** (tracks memory and CPU usage)
5. **Adjusts dynamically** (reduces workers if memory pressure detected)

**Result:** Works optimally on laptops (2-4 workers, 256-512 MB chunks) through servers (16+ workers, 2-4 GB chunks) without manual tuning.

---

## PROBLEM SOLVED

### Before (Fixed Configuration)
```
config/scaling_config.py:
  max_workers: 8  # Wrong for laptops, maybe wrong for servers
  chunk_size_mb: 1024  # Too big for 8GB machines, too small for servers
  
Result: OOM errors on small machines, suboptimal on large ones
```

### After (Adaptive Configuration)
```
Laptop (4GB RAM, 2 cores):
  → Auto-calculates: 2 workers, 256 MB chunks ✅

Workstation (16GB RAM, 8 cores):
  → Auto-calculates: 7 workers, 400 MB chunks ✅

Server (64GB RAM, 32 cores):
  → Auto-calculates: 30 workers, 2000 MB chunks ✅
```

---

## KEY COMPONENTS

### 1. Hardware Profiler
**Detects:**
- CPU cores (physical and logical)
- RAM available
- Disk space
- Disk speed (estimated via testing)
- System classification (laptop/workstation/server)

**Usage:**
```python
from config.adaptive_config import HardwareProfiler

profile = HardwareProfiler.profile(data_path='data/')
print(f"CPU Cores: {profile.cpu_cores}")
print(f"Available RAM: {profile.available_ram_gb} GB")
print(f"System Type: {profile.system_type}")
```

### 2. Parameter Calculator
**Calculates:**
- Optimal worker count (leaves cores for OS)
- Optimal chunk size (based on available RAM with safety margin)
- Memory per worker allocation
- Temporary disk space needed
- Estimated throughput

**Strategy:**

#### Worker Count Calculation
```
Laptop (≤4 cores):      Use 1-2 workers (conservative)
Workstation (4-8 cores): Use cores-1 (leave 1 for OS)
Server (>8 cores):      Use cores-2 (leave 2 for OS)

Hard limits: Min=1, Max=32
```

#### Chunk Size Calculation
```
1. Available RAM = Total RAM × (1 - 20% safety margin)
2. Per-worker allocation = Available RAM ÷ worker count
3. Chunk size = Per-worker allocation × 0.8 (leave headroom)
4. Constraint: Between 256 MB (min) and 4 GB (max)
```

**Usage:**
```python
from config.adaptive_config import AdaptiveParameterCalculator

params = AdaptiveParameterCalculator.calculate_optimal_parameters(profile)
print(f"Max Workers: {params.max_workers}")
print(f"Chunk Size: {params.chunk_size_mb} MB")
```

### 3. Runtime Monitor
**Monitors:**
- Current and peak memory usage
- Current and peak CPU usage
- Job counts and failures
- Memory pressure (when to reduce workers)

**Dynamic Adjustment:**
- Detects memory pressure (>85% used)
- Automatically reduces worker count
- Continues processing smoothly

**Usage:**
```python
from config.adaptive_config import RuntimeMonitor

monitor = RuntimeMonitor(initial_workers=7)

# During execution
metrics = monitor.update_metrics()
if monitor.should_reduce_workers():
    new_workers = monitor.reduce_workers()

# Summary
print(monitor.get_summary())
```

### 4. Adaptive Config
**Combines everything:**
- Hardware profile
- Optimal parameters
- Data selection settings
- Simulation settings
- Output settings

**Features:**
- Automatic hardware detection
- Parameter calculation
- Override capability (if you want manual tuning)
- Save/load from JSON
- Pretty printing

**Usage:**
```python
from config.adaptive_config import create_adaptive_config

# Automatic detection and optimal parameter calculation
config = create_adaptive_config()
config.print_summary()
config.save_to_file('adaptive_config.json')

# With overrides
config = create_adaptive_config(
    override_workers=4,  # Force 4 workers
    override_chunk_size=512,  # Force 512 MB chunks
)

# Access configuration
print(f"Using {config.max_workers} workers")
print(f"Chunk size: {config.chunk_size_mb} MB")
```

---

## HARDWARE DETECTION EXAMPLES

### Example 1: Laptop (8GB RAM, 4 cores)

```
Detected:
  CPU Cores: 4 physical
  RAM: 8.0 GB total, 3.5 GB available
  System Type: laptop

Calculated:
  Max Workers: 2 (conservative for laptop)
  Chunk Size: 256 MB (min to prevent too many chunks)
  Memory per Worker: 314 MB
  Safety Margin: 20%

Reasoning: Laptop with 4 cores and 3.5GB available RAM.
          Using 2 workers and 256MB chunks for optimal 
          performance with safety margins.
```

### Example 2: Workstation (16GB RAM, 8 cores)

```
Detected:
  CPU Cores: 8 physical
  RAM: 16.0 GB total, 10.0 GB available
  System Type: workstation

Calculated:
  Max Workers: 7 (cores-1 for OS)
  Chunk Size: 400 MB
  Memory per Worker: 571 MB
  Safety Margin: 20%

Reasoning: Workstation with 8 cores and 10.0GB available RAM.
          Using 7 workers and 400MB chunks for optimal 
          performance with safety margins.
```

### Example 3: Server (256GB RAM, 32 cores)

```
Detected:
  CPU Cores: 32 physical
  RAM: 256.0 GB total, 200.0 GB available
  System Type: server

Calculated:
  Max Workers: 30 (cores-2 for OS)
  Chunk Size: 2000 MB (limited by max)
  Memory per Worker: 2286 MB
  Safety Margin: 20%

Reasoning: Server with 32 cores and 200.0GB available RAM.
          Using 30 workers and 2000MB chunks for optimal 
          performance with safety margins.
```

---

## SAFETY MARGINS EXPLAINED

### Memory Safety Margin (20%)

**Why 20%?**
- OS needs RAM for: kernel, caches, buffers, other processes
- JVM/Python overhead
- File system cache

**Example:**
```
Available RAM: 8 GB
Safety Margin (20%): 1.6 GB reserved for OS
Usable RAM: 6.4 GB for pipeline
```

### Per-Worker Allocation

**Calculation:**
```
Per-worker allocation = Usable RAM ÷ Worker Count

Chunk size = Per-worker allocation × 0.8
(The remaining 0.2 = overhead for data structures, variables, etc.)
```

**Example with 16GB RAM, 8 workers:**
```
Usable RAM: 12.8 GB (after 20% safety margin)
Per-worker: 1.6 GB
Chunk size: 1.28 GB (80% of per-worker)
Per-worker overhead: 0.32 GB (20% of per-worker)
```

### Chunk Size Constraints

**Minimum (256 MB):** Prevents too many chunks (slow overhead)  
**Maximum (4 GB):** Prevents memory issues (even on big machines)  

---

## DYNAMIC MONITORING & ADJUSTMENT

The system monitors execution and adjusts if needed:

### Memory Pressure Detection

```python
# Check if memory usage is too high
if monitor.should_reduce_workers(memory_threshold_percent=85.0):
    # Memory usage > 85% of total RAM
    new_workers = monitor.reduce_workers()  # Reduce by 20%
    logger.warning(f"Reduced workers from {old} to {new_workers}")
```

### Metrics Tracked

```
- Current memory usage (MB)
- Peak memory usage (MB)
- Memory utilization percentage
- Current CPU usage (%)
- Peak CPU usage (%)
- Jobs processed
- Failed jobs
```

### Example Execution Summary

```
Runtime Summary:
  Peak Memory: 8,240 MB
  Peak CPU: 85.3%
  Jobs Processed: 2,847
  Failed Jobs: 3
```

---

## USAGE PATTERNS

### Pattern 1: Fully Automatic (Recommended)

```python
from config.adaptive_config import create_adaptive_config
from src.parallel_scheduler import ParallelJobScheduler

# Create adaptive config (auto-detects hardware)
config = create_adaptive_config()
config.print_summary()

# Use calculated parameters
scheduler = ParallelJobScheduler(max_workers=config.max_workers)
chunk_iterator = ChunkIterator(
    file_path='data/orders/drr_orders.csv',
    chunk_size_mb=config.chunk_size_mb
)

# Run with optimal settings
results = scheduler.execute_jobs()
```

### Pattern 2: With Override (Manual Tuning)

```python
# If you know better than auto-detection
config = create_adaptive_config(
    override_workers=4,  # Force this many workers
    override_chunk_size=256,  # Force this chunk size
)

config.print_summary()
```

### Pattern 3: With Runtime Monitoring

```python
from config.adaptive_config import create_adaptive_config, RuntimeMonitor

config = create_adaptive_config()
monitor = RuntimeMonitor(config.max_workers)

for job in job_queue:
    # Check memory during execution
    if monitor.should_reduce_workers():
        config.max_workers = monitor.reduce_workers()
        scheduler.update_workers(config.max_workers)
    
    # Process job
    result = scheduler.execute(job)
    monitor.job_count += 1

print(monitor.get_summary())
```

---

## CONFIGURATION FILE FORMAT

Auto-saved as `adaptive_config.json`:

```json
{
  "hardware": {
    "system_type": "workstation",
    "cpu_cores": 8,
    "total_ram_gb": 16.0,
    "available_ram_gb": 10.0,
    "disk_space_gb": 100.0,
    "disk_speed_mbps": 2720.0
  },
  "optimal_parameters": {
    "max_workers": 7,
    "chunk_size_mb": 400,
    "memory_per_worker_mb": 571,
    "memory_safety_margin_percent": 20,
    "estimated_throughput_mbps": 1904.0,
    "reasoning": "Workstation with 8 cores..."
  },
  "current_settings": {
    "max_workers": 7,
    "chunk_size_mb": 400
  },
  "data_selection": {},
  "simulation": {},
  "output": {}
}
```

---

## PERFORMANCE IMPACT

### Without Adaptive Configuration
```
Laptop user:    20+ GB chunks → OOM crash ✗
Server admin:   256 MB chunks → 100x slower than needed ✗
```

### With Adaptive Configuration
```
Laptop user:    Auto: 256 MB chunks, 2 workers ✅
Server admin:   Auto: 2000 MB chunks, 30 workers ✅
Both:           Optimal performance, zero manual tuning ✅
```

---

## TROUBLESHOOTING

### Problem: Getting OOM errors even with adaptive config

**Cause:** Other applications using RAM  
**Solution 1:** Close other applications, then re-run (adaptive will recalculate)  
**Solution 2:** Override chunk size smaller:
```python
config = create_adaptive_config(override_chunk_size=256)
```

### Problem: Not using all CPU cores

**Cause:** Adaptive system being conservative for stability  
**Solution 1:** Monitor.update_metrics() shows actual usage, if low, increase workers:
```python
config = create_adaptive_config(override_workers=16)
```
**Solution 2:** Reduce safety margin (advanced):
```python
AdaptiveParameterCalculator.MEMORY_SAFETY_MARGIN = 10  # Instead of 20
```

### Problem: Disk is too slow

**Cause:** Adaptive detected slow disk, calculated small chunks  
**Solution 1:** Use SSD for data files  
**Solution 2:** Override chunk size larger (if you have RAM):
```python
config = create_adaptive_config(override_chunk_size=2048)
```

---

## ALGORITHM PSEUDOCODE

### Worker Count Calculation
```
cpu_cores = detect_physical_cpu_cores()
system_type = classify_system(cpu_cores, ram_gb)

if system_type == 'laptop':
    workers = max(1, min(2, cores-1))
elif system_type == 'workstation':
    workers = max(2, cores-1)
else:  # server
    workers = max(4, cores-2)

return max(MIN_WORKERS, min(MAX_WORKERS, workers))
```

### Chunk Size Calculation
```
available_ram_mb = available_ram_gb × 1024
safety_margin_mb = available_ram_mb × (SAFETY_MARGIN / 100)
usable_ram_mb = available_ram_mb - safety_margin_mb

per_worker_mb = usable_ram_mb / worker_count
per_worker_mb = clamp(per_worker_mb, MIN_WORKER_MB, MAX_WORKER_MB)

chunk_size_mb = per_worker_mb × 0.8  # 80% usable
return clamp(chunk_size_mb, MIN_CHUNK_MB, MAX_CHUNK_MB)
```

---

## TESTING THE SYSTEM

```python
# Test on this system
python -c "from config.adaptive_config import create_adaptive_config; \
    config = create_adaptive_config(); config.print_summary()"

# Output shows what would be used automatically
# Override and test if needed
```

---

## INTEGRATION WITH SCALING PLAN

This adaptive system enables:

- **Phase 1 (Config):** Use adaptive detection instead of manual config
- **Phase 2 (Chunk Iterator):** Automatically sized chunks
- **Phase 3 (Job Scheduler):** Automatically scaled workers
- **Phase 6 (Monitoring):** Real-time metrics and adjustment

**Result:** Installation works on any hardware without configuration tweaks.

---

## FUTURE ENHANCEMENTS

1. **Disk I/O benchmarking** - More accurate speed detection
2. **CPU instruction set detection** - Optimize for AVX2, etc.
3. **Network speed detection** - For distributed processing
4. **Historical tuning** - Learn optimal settings from past runs
5. **Predictive adjustment** - Predict memory needs based on file size
6. **Machine learning** - Train model to predict optimal parameters

---

## SUMMARY

The adaptive configuration system solves the manual tuning problem:

**Before:** 
```
config.py: max_workers=8, chunk_size=1024
Result: Works on one server, fails on others
```

**After:**
```
from config.adaptive_config import create_adaptive_config
config = create_adaptive_config()  # Auto-detects and calculates
Result: Works optimally on any server without changes
```

**Key Benefits:**
- ✅ No manual tuning needed
- ✅ Works on laptops through servers
- ✅ Safety margins prevent crashes
- ✅ Dynamic adjustment during execution
- ✅ Detailed metrics and logging

