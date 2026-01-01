"""
Adaptive Configuration System
Automatically detects hardware specifications and optimizes parameters.

This system:
1. Profiles the server hardware (CPU cores, RAM, disk speed)
2. Calculates optimal worker count and chunk size
3. Adjusts parameters based on server capabilities
4. Monitors execution and adapts in real-time
5. Provides safety margins to prevent out-of-memory errors
"""

import os
import psutil
import multiprocessing
import shutil
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from pathlib import Path
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# HARDWARE PROFILES (Reference Configurations)
# ============================================================================

@dataclass
class HardwareProfile:
    """Detected hardware specifications"""
    cpu_cores: int
    cpu_logical_cores: int
    total_ram_gb: float
    available_ram_gb: float
    disk_space_gb: float
    disk_speed_mbps: float  # Estimated MB/s
    system_type: str  # 'laptop', 'workstation', 'server'


@dataclass
class OptimalParameters:
    """Calculated optimal parameters"""
    max_workers: int
    chunk_size_mb: int
    memory_per_worker_mb: int
    memory_safety_margin_percent: int
    temp_disk_space_mb: int
    estimated_throughput_mbps: float
    reasoning: str = ""


# ============================================================================
# HARDWARE DETECTION
# ============================================================================

class HardwareProfiler:
    """Detects and analyzes server hardware"""
    
    @staticmethod
    def get_cpu_info() -> Tuple[int, int]:
        """
        Get CPU information
        
        Returns:
            (physical_cores, logical_cores)
        """
        physical_cores = psutil.cpu_count(logical=False) or 1
        logical_cores = psutil.cpu_count(logical=True) or 1
        return physical_cores, logical_cores
    
    @staticmethod
    def get_memory_info() -> Tuple[float, float]:
        """
        Get memory information in GB
        
        Returns:
            (total_ram_gb, available_ram_gb)
        """
        memory = psutil.virtual_memory()
        total_gb = memory.total / (1024 ** 3)
        available_gb = memory.available / (1024 ** 3)
        return total_gb, available_gb
    
    @staticmethod
    def get_disk_info(path: str = '/') -> Tuple[float, float]:
        """
        Get disk information for a given path
        
        Args:
            path: Path to check disk space for
            
        Returns:
            (disk_space_gb, estimated_speed_mbps)
        """
        disk_usage = shutil.disk_usage(path)
        disk_space_gb = disk_usage.free / (1024 ** 3)
        
        # Estimate disk speed (simple heuristic)
        # Real SSDs: 500-3500 MB/s
        # Real HDDs: 50-200 MB/s
        # Network drives: 10-50 MB/s
        disk_speed_mbps = HardwareProfiler._estimate_disk_speed(path)
        
        return disk_space_gb, disk_speed_mbps
    
    @staticmethod
    def _estimate_disk_speed(path: str) -> float:
        """
        Estimate disk speed by testing write speed
        
        Args:
            path: Path to test
            
        Returns:
            Estimated speed in MB/s
        """
        try:
            import tempfile
            import time
            
            # Create temporary test file (10 MB)
            test_size = 10 * 1024 * 1024  # 10 MB
            test_data = b'x' * (1024 * 1024)  # 1 MB chunks
            
            # Test write speed
            with tempfile.NamedTemporaryFile(dir=path, delete=True) as tmp:
                start = time.time()
                for _ in range(10):  # Write 10 MB
                    tmp.write(test_data)
                    tmp.flush()
                elapsed = time.time() - start
            
            speed_mbps = (test_size / (1024 * 1024)) / elapsed if elapsed > 0 else 100
            return min(speed_mbps, 3500)  # Cap at typical SSD speed
        except Exception as e:
            logger.warning(f"Could not estimate disk speed: {e}. Using default: 100 MB/s")
            return 100.0
    
    @staticmethod
    def classify_system(cpu_cores: int, ram_gb: float) -> str:
        """
        Classify system type based on hardware
        
        Args:
            cpu_cores: Physical CPU cores
            ram_gb: Total RAM in GB
            
        Returns:
            'laptop', 'workstation', or 'server'
        """
        if cpu_cores <= 4 and ram_gb <= 16:
            return 'laptop'
        elif cpu_cores <= 8 and ram_gb <= 32:
            return 'workstation'
        else:
            return 'server'
    
    @staticmethod
    def profile(data_path: str = 'data/') -> HardwareProfile:
        """
        Complete hardware profile detection
        
        Args:
            data_path: Path where data files are located
            
        Returns:
            HardwareProfile with all detected specs
        """
        phys_cores, log_cores = HardwareProfiler.get_cpu_info()
        total_ram, avail_ram = HardwareProfiler.get_memory_info()
        disk_space, disk_speed = HardwareProfiler.get_disk_info(data_path)
        system_type = HardwareProfiler.classify_system(phys_cores, total_ram)
        
        profile = HardwareProfile(
            cpu_cores=phys_cores,
            cpu_logical_cores=log_cores,
            total_ram_gb=total_ram,
            available_ram_gb=avail_ram,
            disk_space_gb=disk_space,
            disk_speed_mbps=disk_speed,
            system_type=system_type
        )
        
        logger.info(f"Hardware Profile Detected:")
        logger.info(f"  System Type: {system_type}")
        logger.info(f"  CPU Cores: {phys_cores} physical, {log_cores} logical")
        logger.info(f"  RAM: {total_ram:.1f} GB total, {avail_ram:.1f} GB available")
        logger.info(f"  Disk Space: {disk_space:.1f} GB free")
        logger.info(f"  Disk Speed: ~{disk_speed:.0f} MB/s")
        
        return profile


# ============================================================================
# ADAPTIVE PARAMETER CALCULATION
# ============================================================================

class AdaptiveParameterCalculator:
    """Calculates optimal parameters based on hardware"""
    
    # Safety margins (prevent OOM and system overload)
    MEMORY_SAFETY_MARGIN = 20  # Reserve 20% of RAM for OS and other processes
    MIN_MEMORY_PER_WORKER_MB = 500  # Minimum 500 MB per worker
    MAX_MEMORY_PER_WORKER_MB = 4096  # Maximum 4 GB per worker
    
    # Chunk size constraints
    MIN_CHUNK_SIZE_MB = 256  # Don't go below 256 MB (too many chunks)
    MAX_CHUNK_SIZE_MB = 4096  # Don't go above 4 GB (memory issues)
    
    # Worker constraints
    MIN_WORKERS = 1
    MAX_WORKERS = 32  # Practical limit for multiprocessing
    
    @staticmethod
    def calculate_max_workers(profile: HardwareProfile) -> int:
        """
        Calculate maximum workers based on CPU cores
        
        Strategy:
        - Laptop (≤4 cores): Use 1-2 workers (leave cores for OS)
        - Workstation (4-8 cores): Use cores-1 (leave 1 for OS)
        - Server (>8 cores): Use cores-2 (leave 2 for OS)
        
        Args:
            profile: Hardware profile
            
        Returns:
            Recommended number of workers
        """
        cores = profile.cpu_cores
        system_type = profile.system_type
        
        if system_type == 'laptop':
            # Laptop: conservative, use 1-2 workers
            workers = max(1, min(2, cores - 1))
        elif system_type == 'workstation':
            # Workstation: use most cores but leave 1 for OS
            workers = max(2, cores - 1)
        else:  # server
            # Server: use most cores but leave 2 for OS
            workers = max(4, cores - 2)
        
        # Hard limits
        workers = max(AdaptiveParameterCalculator.MIN_WORKERS, workers)
        workers = min(AdaptiveParameterCalculator.MAX_WORKERS, workers)
        
        logger.info(f"Calculated max workers: {workers} (from {cores} CPU cores)")
        return workers
    
    @staticmethod
    def calculate_chunk_size(profile: HardwareProfile, max_workers: int) -> int:
        """
        Calculate optimal chunk size based on available RAM
        
        Strategy:
        - Calculate available RAM after safety margin
        - Divide by max workers to get per-worker allocation
        - Chunk size = per-worker allocation * 0.8 (leave headroom)
        - Constraint: between MIN and MAX chunk size
        
        Args:
            profile: Hardware profile
            max_workers: Number of parallel workers
            
        Returns:
            Optimal chunk size in MB
        """
        # Available RAM after safety margin
        safety_margin = (profile.available_ram_gb * 1024) * \
                       (AdaptiveParameterCalculator.MEMORY_SAFETY_MARGIN / 100)
        usable_ram_mb = (profile.available_ram_gb * 1024) - safety_margin
        
        # Per-worker memory allocation
        per_worker_mb = usable_ram_mb / max_workers
        
        # Clamp to reasonable range
        per_worker_mb = max(AdaptiveParameterCalculator.MIN_MEMORY_PER_WORKER_MB, per_worker_mb)
        per_worker_mb = min(AdaptiveParameterCalculator.MAX_MEMORY_PER_WORKER_MB, per_worker_mb)
        
        # Chunk size = 80% of per-worker allocation (leave 20% for overhead)
        chunk_size_mb = int(per_worker_mb * 0.8)
        
        # Clamp to chunk size constraints
        chunk_size_mb = max(AdaptiveParameterCalculator.MIN_CHUNK_SIZE_MB, chunk_size_mb)
        chunk_size_mb = min(AdaptiveParameterCalculator.MAX_CHUNK_SIZE_MB, chunk_size_mb)
        
        logger.info(f"Calculated chunk size: {chunk_size_mb} MB")
        logger.info(f"  Available RAM: {usable_ram_mb:.0f} MB")
        logger.info(f"  Per-worker allocation: {per_worker_mb:.0f} MB")
        
        return chunk_size_mb
    
    @staticmethod
    def calculate_optimal_parameters(profile: HardwareProfile) -> OptimalParameters:
        """
        Calculate all optimal parameters
        
        Args:
            profile: Hardware profile
            
        Returns:
            OptimalParameters with all calculated values
        """
        max_workers = AdaptiveParameterCalculator.calculate_max_workers(profile)
        chunk_size_mb = AdaptiveParameterCalculator.calculate_chunk_size(profile, max_workers)
        
        # Calculate other metrics
        memory_per_worker_mb = (profile.available_ram_gb * 1024) / max_workers
        memory_per_worker_mb = int(memory_per_worker_mb * 0.8)  # 80% usable
        
        # Temporary disk space needed (a few chunks)
        temp_disk_mb = chunk_size_mb * max_workers * 2
        
        # Estimated throughput
        estimated_throughput = profile.disk_speed_mbps * 0.7  # Assume 70% of disk speed
        
        # Create reasoning string
        reasoning = (
            f"{profile.system_type.capitalize()} with {profile.cpu_cores} cores "
            f"and {profile.available_ram_gb:.1f}GB available RAM. "
            f"Using {max_workers} workers and {chunk_size_mb}MB chunks for "
            f"optimal performance with safety margins."
        )
        
        params = OptimalParameters(
            max_workers=max_workers,
            chunk_size_mb=chunk_size_mb,
            memory_per_worker_mb=memory_per_worker_mb,
            memory_safety_margin_percent=AdaptiveParameterCalculator.MEMORY_SAFETY_MARGIN,
            temp_disk_space_mb=temp_disk_mb,
            estimated_throughput_mbps=estimated_throughput,
            reasoning=reasoning
        )
        
        return params


# ============================================================================
# DYNAMIC MONITORING & ADJUSTMENT
# ============================================================================

class RuntimeMonitor:
    """Monitors execution and adjusts parameters dynamically"""
    
    def __init__(self, initial_workers: int):
        """
        Initialize runtime monitor
        
        Args:
            initial_workers: Initial worker count
        """
        self.initial_workers = initial_workers
        self.current_workers = initial_workers
        self.peak_memory_mb = 0
        self.peak_cpu_percent = 0
        self.job_count = 0
        self.failed_jobs = 0
        
    def update_metrics(self) -> Dict:
        """
        Update runtime metrics
        
        Returns:
            Current system metrics
        """
        process = psutil.Process()
        memory_info = psutil.virtual_memory()
        
        # Use memory.percent directly (cross-platform compatible)
        used_memory_mb = memory_info.used / (1024 * 1024)
        total_memory_mb = memory_info.total / (1024 * 1024)
        
        cpu_percent = process.cpu_percent(interval=0.1)
        
        self.peak_memory_mb = max(self.peak_memory_mb, used_memory_mb)
        self.peak_cpu_percent = max(self.peak_cpu_percent, cpu_percent)
        
        metrics = {
            'current_memory_mb': used_memory_mb,
            'peak_memory_mb': self.peak_memory_mb,
            'memory_percent': (used_memory_mb / total_memory_mb) * 100,
            'cpu_percent': cpu_percent,
            'peak_cpu_percent': self.peak_cpu_percent,
            'job_count': self.job_count,
            'failed_jobs': self.failed_jobs,
        }
        
        return metrics
    
    def should_reduce_workers(self, memory_threshold_percent: float = 85.0) -> bool:
        """
        Check if we should reduce worker count due to memory pressure
        
        Args:
            memory_threshold_percent: Threshold for reduction (default 85%)
            
        Returns:
            True if should reduce workers
        """
        metrics = self.update_metrics()
        return metrics['memory_percent'] > memory_threshold_percent
    
    def reduce_workers(self) -> int:
        """
        Reduce worker count and return new count
        
        Returns:
            New worker count
        """
        if self.current_workers > 1:
            self.current_workers = max(1, int(self.current_workers * 0.8))
            logger.warning(f"Reduced workers to {self.current_workers} due to memory pressure")
        return self.current_workers
    
    def get_summary(self) -> str:
        """Get execution summary"""
        metrics = self.update_metrics()
        return (
            f"\nRuntime Summary:\n"
            f"  Peak Memory: {self.peak_memory_mb:.0f} MB\n"
            f"  Peak CPU: {self.peak_cpu_percent:.1f}%\n"
            f"  Jobs Processed: {self.job_count}\n"
            f"  Failed Jobs: {self.failed_jobs}\n"
        )


# ============================================================================
# ADAPTIVE CONFIGURATION CLASS
# ============================================================================

@dataclass
class AdaptiveConfig:
    """Complete adaptive configuration"""
    
    # Hardware info
    hardware_profile: HardwareProfile
    optimal_parameters: OptimalParameters
    
    # Current settings (can be overridden)
    max_workers: int = field(init=False)
    chunk_size_mb: int = field(init=False)
    
    # Other settings
    data_selection: Dict = field(default_factory=dict)
    simulation: Dict = field(default_factory=dict)
    output: Dict = field(default_factory=dict)
    
    def __post_init__(self):
        """Initialize adaptive parameters"""
        self.max_workers = self.optimal_parameters.max_workers
        self.chunk_size_mb = self.optimal_parameters.chunk_size_mb
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization"""
        return {
            'hardware': {
                'system_type': self.hardware_profile.system_type,
                'cpu_cores': self.hardware_profile.cpu_cores,
                'total_ram_gb': self.hardware_profile.total_ram_gb,
                'available_ram_gb': self.hardware_profile.available_ram_gb,
                'disk_space_gb': self.hardware_profile.disk_space_gb,
                'disk_speed_mbps': self.hardware_profile.disk_speed_mbps,
            },
            'optimal_parameters': {
                'max_workers': self.optimal_parameters.max_workers,
                'chunk_size_mb': self.optimal_parameters.chunk_size_mb,
                'memory_per_worker_mb': self.optimal_parameters.memory_per_worker_mb,
                'memory_safety_margin_percent': self.optimal_parameters.memory_safety_margin_percent,
                'estimated_throughput_mbps': self.optimal_parameters.estimated_throughput_mbps,
                'reasoning': self.optimal_parameters.reasoning,
            },
            'current_settings': {
                'max_workers': self.max_workers,
                'chunk_size_mb': self.chunk_size_mb,
            },
            'data_selection': self.data_selection,
            'simulation': self.simulation,
            'output': self.output,
        }
    
    def save_to_file(self, filepath: str) -> None:
        """Save configuration to JSON file"""
        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        logger.info(f"Configuration saved to {filepath}")
    
    def print_summary(self) -> None:
        """Print configuration summary"""
        print(f"\n{'='*80}")
        print(f"ADAPTIVE CONFIGURATION SUMMARY")
        print(f"{'='*80}")
        print(f"\nHardware Profile:")
        print(f"  System Type: {self.hardware_profile.system_type}")
        print(f"  CPU Cores: {self.hardware_profile.cpu_cores} physical, "
              f"{self.hardware_profile.cpu_logical_cores} logical")
        print(f"  RAM: {self.hardware_profile.total_ram_gb:.1f} GB total, "
              f"{self.hardware_profile.available_ram_gb:.1f} GB available")
        print(f"  Disk: {self.hardware_profile.disk_space_gb:.1f} GB free, "
              f"~{self.hardware_profile.disk_speed_mbps:.0f} MB/s")
        
        print(f"\nOptimal Parameters:")
        print(f"  Max Workers: {self.optimal_parameters.max_workers}")
        print(f"  Chunk Size: {self.optimal_parameters.chunk_size_mb} MB")
        print(f"  Memory per Worker: {self.optimal_parameters.memory_per_worker_mb:.0f} MB")
        print(f"  Safety Margin: {self.optimal_parameters.memory_safety_margin_percent}%")
        print(f"  Est. Throughput: {self.optimal_parameters.estimated_throughput_mbps:.0f} MB/s")
        
        print(f"\nReasoning:")
        print(f"  {self.optimal_parameters.reasoning}")
        print(f"\n{'='*80}\n")


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def create_adaptive_config(
    data_path: str = 'data/',
    override_workers: Optional[int] = None,
    override_chunk_size: Optional[int] = None,
) -> AdaptiveConfig:
    """
    Create adaptive configuration with automatic hardware detection
    
    Args:
        data_path: Path where data files are located
        override_workers: Override calculated worker count (optional)
        override_chunk_size: Override calculated chunk size (optional)
        
    Returns:
        AdaptiveConfig instance
    """
    # Profile hardware
    profile = HardwareProfiler.profile(data_path)
    
    # Calculate optimal parameters
    params = AdaptiveParameterCalculator.calculate_optimal_parameters(profile)
    
    # Create config
    config = AdaptiveConfig(
        hardware_profile=profile,
        optimal_parameters=params,
        data_selection={},
        simulation={},
        output={},
    )
    
    # Apply overrides if provided
    if override_workers is not None:
        config.max_workers = override_workers
        logger.info(f"Overriding max_workers to {override_workers}")
    
    if override_chunk_size is not None:
        config.chunk_size_mb = override_chunk_size
        logger.info(f"Overriding chunk_size_mb to {override_chunk_size} MB")
    
    return config


if __name__ == '__main__':
    # Example usage
    config = create_adaptive_config()
    config.print_summary()
    config.save_to_file('adaptive_config.json')
