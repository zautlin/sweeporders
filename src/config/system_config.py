"""
System Configuration Module

Auto-detects system resources and calculates optimal configuration
for parallel processing and memory management.
"""

import multiprocessing
import os
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
        """Format configuration as human-readable string."""
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
    """Detect system config and calculate optimal settings. Supports manual overrides for workers and chunk size."""
    try:
        # Try to import psutil for accurate memory detection
        import psutil
        has_psutil = True
    except ImportError:
        has_psutil = False
        print("Warning: psutil not installed. Using default memory estimation.")
        print("Install with: pip install psutil")
    
    # Detect CPU cores
    cpu_count = multiprocessing.cpu_count()
    
    # Calculate optimal workers
    if override_workers is not None:
        num_workers = max(1, override_workers)
    else:
        num_workers = _calculate_optimal_workers(cpu_count)
    
    # Detect memory
    if has_psutil:
        memory_info = psutil.virtual_memory()
        available_memory_bytes = memory_info.available
        available_memory_gb = available_memory_bytes / (1024 ** 3)
    else:
        # Fallback: assume conservative 8GB available
        available_memory_bytes = 8 * 1024 ** 3
        available_memory_gb = 8.0
    
    # Calculate optimal chunk size
    if override_chunk_size is not None:
        chunk_size = max(1000, override_chunk_size)
    else:
        chunk_size = _calculate_optimal_chunk_size(available_memory_bytes)
    
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
        return 1  # Single core or dual core: no parallelization
    elif cpu_count <= 4:
        return cpu_count - 1  # Leave 1 core free
    elif cpu_count <= 8:
        return cpu_count - 2  # Leave 2 cores free
    else:
        return min(cpu_count - 2, 16)  # Cap at 16 workers


def _calculate_optimal_chunk_size(available_memory: int) -> int:
    """Calculate optimal chunk size based on available memory."""
    # Target 5% of available memory per chunk
    target_memory_per_chunk = available_memory * 0.05
    
    # Estimate ~1KB per row (conservative estimate)
    # Actual row size varies by schema, but 1KB is reasonable average
    estimated_chunk_size = int(target_memory_per_chunk / 1024)
    
    # Apply constraints: min 10K, max 500K
    chunk_size = max(10_000, min(estimated_chunk_size, 500_000))
    
    return chunk_size


def get_config_with_overrides(
    workers: Optional[int] = None,
    chunk_size: Optional[int] = None
) -> SystemConfig:
    """Get system config with env var overrides. Priority: function args > env vars > auto-detect."""
    # Check environment variables
    env_workers = os.getenv('SWEEP_WORKERS')
    env_chunk_size = os.getenv('SWEEP_CHUNK_SIZE')
    
    # Priority: function args > env vars > auto-detect
    final_workers = workers
    if final_workers is None and env_workers is not None:
        try:
            final_workers = int(env_workers)
        except ValueError:
            print(f"Warning: Invalid SWEEP_WORKERS value '{env_workers}', ignoring")
    
    final_chunk_size = chunk_size
    if final_chunk_size is None and env_chunk_size is not None:
        try:
            final_chunk_size = int(env_chunk_size)
        except ValueError:
            print(f"Warning: Invalid SWEEP_CHUNK_SIZE value '{env_chunk_size}', ignoring")
    
    return detect_system_config(
        override_workers=final_workers,
        override_chunk_size=final_chunk_size
    )


def print_system_info():
    """Print detailed system information for debugging."""
    try:
        import psutil
        
        print("\nDetailed System Information:")
        print("="*60)
        
        # CPU info
        cpu_count = multiprocessing.cpu_count()
        print(f"CPU Cores (Logical): {cpu_count}")
        
        try:
            cpu_freq = psutil.cpu_freq()
            if cpu_freq:
                print(f"CPU Frequency: {cpu_freq.current:.2f} MHz")
        except Exception:
            pass
        
        # Memory info
        mem = psutil.virtual_memory()
        print(f"\nMemory:")
        print(f"  Total: {mem.total / (1024**3):.2f} GB")
        print(f"  Available: {mem.available / (1024**3):.2f} GB")
        print(f"  Used: {mem.used / (1024**3):.2f} GB ({mem.percent}%)")
        
        # Disk info
        try:
            disk = psutil.disk_usage('.')
            print(f"\nDisk (current directory):")
            print(f"  Total: {disk.total / (1024**3):.2f} GB")
            print(f"  Free: {disk.free / (1024**3):.2f} GB ({100 - disk.percent}%)")
        except Exception:
            pass
        
        print("="*60)
        
    except ImportError:
        print("\nInstall psutil for detailed system information:")
        print("  pip install psutil")


if __name__ == '__main__':
    # Test the configuration detection
    print("Testing System Configuration Detection")
    print("="*60)
    
    # Auto-detect
    config = detect_system_config()
    print("\nAuto-detected Configuration:")
    print(config)
    
    # Test with overrides
    config_override = detect_system_config(override_workers=4, override_chunk_size=50000)
    print("\nWith Overrides (workers=4, chunk_size=50000):")
    print(config_override)
    
    # Test environment variable support
    os.environ['SWEEP_WORKERS'] = '8'
    os.environ['SWEEP_CHUNK_SIZE'] = '200000'
    config_env = get_config_with_overrides()
    print("\nWith Environment Variables (SWEEP_WORKERS=8, SWEEP_CHUNK_SIZE=200000):")
    print(config_env)
    
    # Clean up
    del os.environ['SWEEP_WORKERS']
    del os.environ['SWEEP_CHUNK_SIZE']
    
    # Print detailed system info
    print_system_info()
