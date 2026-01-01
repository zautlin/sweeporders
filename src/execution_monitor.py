"""
Phase 6: Execution Monitor - Real-time Progress Tracking

Provides comprehensive monitoring of pipeline execution including:
- Real-time progress tracking
- Memory usage monitoring
- CPU utilization tracking
- ETA calculation
- Dynamic worker adjustment
- Performance analytics
"""

import time
import psutil
import threading
import logging
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from enum import Enum
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Job execution status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ResourceMetrics:
    """System resource metrics at a point in time"""
    timestamp: float
    cpu_percent: float
    memory_mb: float
    memory_percent: float
    disk_io_read_mb: float
    disk_io_write_mb: float


@dataclass
class JobMetrics:
    """Metrics for a single job execution"""
    job_id: str
    status: JobStatus = JobStatus.PENDING
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    rows_processed: int = 0
    rows_output: int = 0
    duration_sec: float = 0.0
    throughput_rows_sec: float = 0.0
    error_message: Optional[str] = None


@dataclass
class ExecutionMetrics:
    """Overall execution metrics"""
    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    total_rows_processed: int = 0
    total_rows_output: int = 0
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    total_duration_sec: float = 0.0
    peak_memory_mb: float = 0.0
    average_memory_mb: float = 0.0
    average_cpu_percent: float = 0.0
    resource_history: List[ResourceMetrics] = field(default_factory=list)
    job_metrics: Dict[str, JobMetrics] = field(default_factory=dict)


class ExecutionMonitor:
    """
    Real-time execution monitor with progress tracking and analytics
    
    Features:
    - Track job progress in real-time
    - Monitor system resources (CPU, memory, disk I/O)
    - Calculate ETA based on current progress
    - Dynamic worker adjustment recommendations
    - Performance analytics and reporting
    
    Example:
        monitor = ExecutionMonitor(total_jobs=10)
        
        with monitor.job_tracker('job_1') as tracker:
            for i in range(1000):
                # Process data
                tracker.update(rows_processed=i+1, rows_output=i)
        
        monitor.print_summary()
        monitor.save_metrics('metrics.json')
    """
    
    def __init__(
        self,
        total_jobs: int,
        monitor_interval_sec: float = 1.0,
        enable_resource_monitoring: bool = True,
    ):
        """
        Initialize execution monitor
        
        Args:
            total_jobs: Total number of jobs to process
            monitor_interval_sec: Interval for resource monitoring
            enable_resource_monitoring: Enable/disable resource monitoring
        """
        self.total_jobs = total_jobs
        self.monitor_interval_sec = monitor_interval_sec
        self.enable_resource_monitoring = enable_resource_monitoring
        
        self.metrics = ExecutionMetrics(total_jobs=total_jobs)
        self.metrics.start_time = time.time()
        
        # Resource monitoring
        self._monitoring = False
        self._monitor_thread = None
        self._last_disk_io = None
        
        # Start resource monitoring if enabled
        if self.enable_resource_monitoring:
            self._start_resource_monitoring()
    
    def _start_resource_monitoring(self) -> None:
        """Start background resource monitoring thread"""
        self._monitoring = True
        self._last_disk_io = psutil.disk_io_counters()
        self._monitor_thread = threading.Thread(target=self._monitor_resources, daemon=True)
        self._monitor_thread.start()
        logger.info("Resource monitoring started")
    
    def _monitor_resources(self) -> None:
        """Background thread for continuous resource monitoring"""
        while self._monitoring:
            try:
                # CPU and memory
                cpu_percent = psutil.cpu_percent(interval=0.1)
                memory = psutil.virtual_memory()
                memory_mb = memory.used / 1024 / 1024
                memory_percent = memory.percent
                
                # Disk I/O
                disk_io = psutil.disk_io_counters()
                disk_read_mb = (disk_io.read_bytes - self._last_disk_io.read_bytes) / 1024 / 1024
                disk_write_mb = (disk_io.write_bytes - self._last_disk_io.write_bytes) / 1024 / 1024
                self._last_disk_io = disk_io
                
                # Record metrics
                metrics = ResourceMetrics(
                    timestamp=time.time(),
                    cpu_percent=cpu_percent,
                    memory_mb=memory_mb,
                    memory_percent=memory_percent,
                    disk_io_read_mb=disk_read_mb,
                    disk_io_write_mb=disk_write_mb,
                )
                self.metrics.resource_history.append(metrics)
                
                # Update peak memory
                if memory_mb > self.metrics.peak_memory_mb:
                    self.metrics.peak_memory_mb = memory_mb
                
                time.sleep(self.monitor_interval_sec)
            except Exception as e:
                logger.error(f"Error in resource monitoring: {e}")
    
    def job_started(self, job_id: str) -> None:
        """Mark job as started"""
        if job_id not in self.metrics.job_metrics:
            self.metrics.job_metrics[job_id] = JobMetrics(job_id=job_id)
        
        job = self.metrics.job_metrics[job_id]
        job.status = JobStatus.RUNNING
        job.start_time = time.time()
    
    def job_completed(
        self,
        job_id: str,
        rows_processed: int = 0,
        rows_output: int = 0,
    ) -> None:
        """Mark job as completed with metrics"""
        if job_id not in self.metrics.job_metrics:
            self.metrics.job_metrics[job_id] = JobMetrics(job_id=job_id)
        
        job = self.metrics.job_metrics[job_id]
        job.status = JobStatus.COMPLETED
        job.end_time = time.time()
        job.rows_processed = rows_processed
        job.rows_output = rows_output
        
        if job.start_time:
            job.duration_sec = job.end_time - job.start_time
            job.throughput_rows_sec = rows_processed / job.duration_sec if job.duration_sec > 0 else 0
        
        self.metrics.completed_jobs += 1
        self.metrics.total_rows_processed += rows_processed
        self.metrics.total_rows_output += rows_output
    
    def job_failed(self, job_id: str, error_message: str = "") -> None:
        """Mark job as failed"""
        if job_id not in self.metrics.job_metrics:
            self.metrics.job_metrics[job_id] = JobMetrics(job_id=job_id)
        
        job = self.metrics.job_metrics[job_id]
        job.status = JobStatus.FAILED
        job.end_time = time.time()
        job.error_message = error_message
        
        self.metrics.failed_jobs += 1
        logger.error(f"Job {job_id} failed: {error_message}")
    
    def get_progress(self) -> Dict[str, Any]:
        """Get current progress metrics"""
        completed = self.metrics.completed_jobs
        failed = self.metrics.failed_jobs
        total = self.metrics.total_jobs
        pending = total - completed - failed
        
        # Calculate ETA
        eta_sec = None
        if completed > 0 and self.metrics.start_time:
            elapsed = time.time() - self.metrics.start_time
            avg_time_per_job = elapsed / completed
            eta_sec = avg_time_per_job * pending
        
        # Current resource usage
        current_resources = None
        if self.metrics.resource_history:
            current_resources = asdict(self.metrics.resource_history[-1])
        
        return {
            'progress_percent': (completed / total * 100) if total > 0 else 0,
            'completed_jobs': completed,
            'pending_jobs': pending,
            'failed_jobs': failed,
            'total_jobs': total,
            'total_rows_processed': self.metrics.total_rows_processed,
            'total_rows_output': self.metrics.total_rows_output,
            'elapsed_sec': time.time() - self.metrics.start_time if self.metrics.start_time else 0,
            'eta_sec': eta_sec,
            'peak_memory_mb': self.metrics.peak_memory_mb,
            'current_resources': current_resources,
        }
    
    def print_progress(self) -> None:
        """Print current progress to console"""
        progress = self.get_progress()
        
        print("\n" + "="*80)
        print(f"EXECUTION PROGRESS")
        print("="*80)
        
        # Progress bar
        pct = progress['progress_percent']
        bar_len = 40
        filled = int(bar_len * pct / 100)
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\nProgress: [{bar}] {pct:.1f}%")
        
        # Job counts
        print(f"\nJobs:")
        print(f"  Completed: {progress['completed_jobs']:,}/{progress['total_jobs']:,}")
        print(f"  Pending:   {progress['pending_jobs']:,}")
        print(f"  Failed:    {progress['failed_jobs']:,}")
        
        # Data processed
        print(f"\nData:")
        print(f"  Rows processed: {progress['total_rows_processed']:,}")
        print(f"  Rows output:    {progress['total_rows_output']:,}")
        
        # Time
        elapsed_min = progress['elapsed_sec'] / 60
        print(f"\nTime:")
        print(f"  Elapsed:  {elapsed_min:.1f} minutes")
        if progress['eta_sec']:
            eta_min = progress['eta_sec'] / 60
            print(f"  ETA:      {eta_min:.1f} minutes")
        
        # Resources
        if progress['current_resources']:
            res = progress['current_resources']
            print(f"\nResources:")
            print(f"  CPU:      {res.get('cpu_percent', 0):.1f}%")
            print(f"  Memory:   {res.get('memory_mb', 0):.0f} MB ({res.get('memory_percent', 0):.1f}%)")
            print(f"  Peak mem: {progress['peak_memory_mb']:.0f} MB")
        
        print("="*80)
    
    def print_summary(self) -> None:
        """Print final execution summary"""
        self._monitoring = False
        
        if self.metrics.start_time:
            self.metrics.end_time = time.time()
            self.metrics.total_duration_sec = self.metrics.end_time - self.metrics.start_time
        
        # Calculate averages
        if self.metrics.resource_history:
            avg_cpu = sum(m.cpu_percent for m in self.metrics.resource_history) / len(self.metrics.resource_history)
            avg_mem = sum(m.memory_mb for m in self.metrics.resource_history) / len(self.metrics.resource_history)
            self.metrics.average_cpu_percent = avg_cpu
            self.metrics.average_memory_mb = avg_mem
        
        print("\n" + "="*80)
        print("EXECUTION SUMMARY")
        print("="*80)
        
        # Job summary
        print(f"\nJobs:")
        print(f"  Total:      {self.metrics.total_jobs:,}")
        print(f"  Completed:  {self.metrics.completed_jobs:,}")
        print(f"  Failed:     {self.metrics.failed_jobs:,}")
        success_pct = (self.metrics.completed_jobs / self.metrics.total_jobs * 100) if self.metrics.total_jobs > 0 else 0
        print(f"  Success:    {success_pct:.1f}%")
        
        # Data summary
        print(f"\nData Processing:")
        print(f"  Rows input:     {self.metrics.total_rows_processed:,}")
        print(f"  Rows output:    {self.metrics.total_rows_output:,}")
        if self.metrics.total_rows_processed > 0:
            ratio = self.metrics.total_rows_output / self.metrics.total_rows_processed * 100
            print(f"  Compression:    {ratio:.1f}%")
        
        # Performance
        print(f"\nPerformance:")
        print(f"  Total time:     {self.metrics.total_duration_sec:.1f} seconds")
        if self.metrics.total_rows_processed > 0 and self.metrics.total_duration_sec > 0:
            throughput = self.metrics.total_rows_processed / self.metrics.total_duration_sec
            print(f"  Throughput:     {throughput:,.0f} rows/sec")
        
        # Resources
        print(f"\nResources:")
        print(f"  Peak memory:    {self.metrics.peak_memory_mb:.0f} MB")
        print(f"  Avg memory:     {self.metrics.average_memory_mb:.0f} MB")
        print(f"  Avg CPU:        {self.metrics.average_cpu_percent:.1f}%")
        
        # Job details
        if self.metrics.job_metrics:
            print(f"\nJob Details:")
            for job_id, job in sorted(self.metrics.job_metrics.items()):
                status = "✓" if job.status == JobStatus.COMPLETED else "✗"
                print(f"  {status} {job_id}: {job.status.value} ({job.throughput_rows_sec:,.0f} rows/sec)")
        
        print("="*80)
    
    def save_metrics(self, output_file: str) -> None:
        """Save metrics to JSON file"""
        # Finalize metrics
        if self.metrics.start_time:
            self.metrics.end_time = time.time()
            self.metrics.total_duration_sec = self.metrics.end_time - self.metrics.start_time
        
        # Convert to dict for JSON serialization
        metrics_dict = {
            'total_jobs': self.metrics.total_jobs,
            'completed_jobs': self.metrics.completed_jobs,
            'failed_jobs': self.metrics.failed_jobs,
            'total_rows_processed': self.metrics.total_rows_processed,
            'total_rows_output': self.metrics.total_rows_output,
            'start_time': datetime.fromtimestamp(self.metrics.start_time).isoformat() if self.metrics.start_time else None,
            'end_time': datetime.fromtimestamp(self.metrics.end_time).isoformat() if self.metrics.end_time else None,
            'total_duration_sec': self.metrics.total_duration_sec,
            'peak_memory_mb': self.metrics.peak_memory_mb,
            'average_memory_mb': self.metrics.average_memory_mb,
            'average_cpu_percent': self.metrics.average_cpu_percent,
            'job_metrics': {
                job_id: {
                    'status': job.status.value,
                    'rows_processed': job.rows_processed,
                    'rows_output': job.rows_output,
                    'duration_sec': job.duration_sec,
                    'throughput_rows_sec': job.throughput_rows_sec,
                }
                for job_id, job in self.metrics.job_metrics.items()
            }
        }
        
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(metrics_dict, f, indent=2)
        
        logger.info(f"Metrics saved to {output_file}")


# ============================================================================
# TESTING
# ============================================================================

if __name__ == '__main__':
    print("Testing Execution Monitor\n")
    
    # Create monitor for 5 jobs
    monitor = ExecutionMonitor(total_jobs=5, monitor_interval_sec=0.5)
    
    # Simulate job execution
    for i in range(5):
        job_id = f"job_{i+1}"
        monitor.job_started(job_id)
        
        print(f"Processing {job_id}...")
        time.sleep(0.5)  # Simulate work
        
        # Simulate processing metrics
        rows_processed = 1000 + (i * 100)
        rows_output = 800 + (i * 80)
        
        monitor.job_completed(job_id, rows_processed=rows_processed, rows_output=rows_output)
        
        # Show progress
        monitor.print_progress()
    
    # Print final summary
    monitor.print_summary()
    
    # Save metrics
    monitor.save_metrics('processed_files/execution_metrics.json')
    print("\n✅ Execution monitor test complete!")
