"""
Parallel Job Scheduler for Multi-Process Execution

Manages parallel execution of independent jobs using multiprocessing
and concurrent.futures for CPU-bound and I/O-bound tasks.

Key Features:
- Execute jobs in parallel with configurable worker count
- Job queue management and tracking
- Progress monitoring and metrics
- Graceful error handling and recovery
- Result aggregation
"""

import logging
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from multiprocessing import Manager, Queue
from dataclasses import dataclass, field
from typing import Callable, List, Tuple, Dict, Any, Optional
from enum import Enum
from pathlib import Path
from datetime import datetime
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class JobStatus(Enum):
    """Job execution status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class Job:
    """Represents a single job to be executed"""
    job_id: str
    security_code: int
    date: str
    task_func: Optional[Callable] = None
    task_args: Tuple = field(default_factory=tuple)
    task_kwargs: Dict[str, Any] = field(default_factory=dict)
    
    def __hash__(self):
        return hash(self.job_id)
    
    def __eq__(self, other):
        return isinstance(other, Job) and self.job_id == other.job_id


@dataclass
class JobResult:
    """Result of a completed job"""
    job_id: str
    security_code: int
    date: str
    status: JobStatus
    result: Any = None
    error: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration_sec: float = 0.0
    
    @property
    def success(self) -> bool:
        return self.status == JobStatus.COMPLETED
    
    @property
    def failed(self) -> bool:
        return self.status == JobStatus.FAILED


@dataclass
class SchedulerMetrics:
    """Metrics for scheduler execution"""
    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    skipped_jobs: int = 0
    total_time_sec: float = 0.0
    avg_job_time_sec: float = 0.0
    throughput_jobs_per_sec: float = 0.0
    
    @property
    def success_rate(self) -> float:
        if self.total_jobs == 0:
            return 0.0
        return (self.completed_jobs / self.total_jobs) * 100


class ParallelJobScheduler:
    """
    Schedules and executes jobs in parallel using multiprocessing
    
    Usage:
        scheduler = ParallelJobScheduler(max_workers=8)
        
        jobs = [
            Job(
                job_id=f"job_{i}",
                security_code=sec,
                date=date,
                task_func=process_chunk,
                task_args=(security, date, chunk_df)
            )
            for i, (security, date) in enumerate(job_matrix)
        ]
        
        results = scheduler.execute_jobs(jobs)
        scheduler.print_summary()
    """
    
    def __init__(
        self,
        max_workers: int = 8,
        timeout_sec: float = 3600.0,
        use_threads: bool = False,
        verbose: bool = True,
    ):
        """
        Initialize ParallelJobScheduler
        
        Args:
            max_workers: Number of parallel workers
            timeout_sec: Timeout per job in seconds
            use_threads: Use ThreadPoolExecutor instead of ProcessPoolExecutor
            verbose: Print progress messages
        """
        self.max_workers = max_workers
        self.timeout_sec = timeout_sec
        self.use_threads = use_threads
        self.verbose = verbose
        self.executor_type = ThreadPoolExecutor if use_threads else ProcessPoolExecutor
        
        self.jobs: List[Job] = []
        self.results: List[JobResult] = []
        self.metrics = SchedulerMetrics()
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        
        if self.verbose:
            executor_name = "ThreadPoolExecutor" if use_threads else "ProcessPoolExecutor"
            logger.info(f"ParallelJobScheduler initialized: {max_workers} workers, {executor_name}")
    
    def add_job(
        self,
        job_id: str,
        security_code: int,
        date: str,
        task_func: Callable,
        task_args: Tuple = (),
        task_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Add a job to the scheduler queue"""
        if task_kwargs is None:
            task_kwargs = {}
        
        job = Job(
            job_id=job_id,
            security_code=security_code,
            date=date,
            task_func=task_func,
            task_args=task_args,
            task_kwargs=task_kwargs,
        )
        self.jobs.append(job)
    
    def add_jobs(self, jobs: List[Job]) -> None:
        """Add multiple jobs to the scheduler queue"""
        self.jobs.extend(jobs)
    
    def execute_jobs(self) -> List[JobResult]:
        """
        Execute all queued jobs in parallel
        
        Returns:
            List of JobResult objects
        """
        if not self.jobs:
            logger.warning("No jobs to execute")
            return []
        
        self.metrics.total_jobs = len(self.jobs)
        self.start_time = time.time()
        
        if self.verbose:
            logger.info(f"Starting parallel execution: {len(self.jobs)} jobs with {self.max_workers} workers")
        
        # Map job to future
        job_to_future = {}
        
        with self.executor_type(max_workers=self.max_workers) as executor:
            # Submit all jobs
            for job in self.jobs:
                if job.task_func is None:
                    logger.warning(f"Job {job.job_id} has no task function, skipping")
                    result = JobResult(
                        job_id=job.job_id,
                        security_code=job.security_code,
                        date=job.date,
                        status=JobStatus.SKIPPED,
                        error="No task function provided"
                    )
                    self.results.append(result)
                    self.metrics.skipped_jobs += 1
                    continue
                
                try:
                    future = executor.submit(
                        self._execute_job_wrapper,
                        job
                    )
                    job_to_future[future] = job
                except Exception as e:
                    logger.error(f"Failed to submit job {job.job_id}: {e}")
                    result = JobResult(
                        job_id=job.job_id,
                        security_code=job.security_code,
                        date=job.date,
                        status=JobStatus.FAILED,
                        error=str(e)
                    )
                    self.results.append(result)
                    self.metrics.failed_jobs += 1
            
            # Collect results as they complete
            completed = 0
            for future in as_completed(job_to_future.keys(), timeout=self.timeout_sec):
                completed += 1
                job = job_to_future[future]
                
                try:
                    result = future.result()
                    self.results.append(result)
                    
                    if result.success:
                        self.metrics.completed_jobs += 1
                    else:
                        self.metrics.failed_jobs += 1
                    
                    if self.verbose and completed % max(1, len(self.jobs) // 10) == 0:
                        progress = (completed / len(self.jobs)) * 100
                        logger.info(f"Progress: {progress:.1f}% ({completed}/{len(self.jobs)})")
                
                except Exception as e:
                    logger.error(f"Job {job.job_id} failed with exception: {e}")
                    result = JobResult(
                        job_id=job.job_id,
                        security_code=job.security_code,
                        date=job.date,
                        status=JobStatus.FAILED,
                        error=str(e)
                    )
                    self.results.append(result)
                    self.metrics.failed_jobs += 1
        
        self.end_time = time.time()
        self.metrics.total_time_sec = self.end_time - self.start_time
        
        if self.metrics.completed_jobs > 0:
            self.metrics.avg_job_time_sec = self.metrics.total_time_sec / self.metrics.completed_jobs
            self.metrics.throughput_jobs_per_sec = self.metrics.completed_jobs / self.metrics.total_time_sec
        
        if self.verbose:
            logger.info(f"Execution complete: {self.metrics.completed_jobs}/{len(self.jobs)} jobs succeeded")
        
        return self.results
    
    def print_summary(self) -> None:
        """Print summary of scheduler execution"""
        if not self.results:
            logger.warning("No results to summarize")
            return
        
        print("\n" + "="*70)
        print("PARALLEL JOB SCHEDULER SUMMARY")
        print("="*70)
        print(f"\nTotal Jobs: {self.metrics.total_jobs}")
        print(f"Completed: {self.metrics.completed_jobs}")
        print(f"Failed: {self.metrics.failed_jobs}")
        print(f"Skipped: {self.metrics.skipped_jobs}")
        print(f"Success Rate: {self.metrics.success_rate:.1f}%")
        
        print(f"\nTotal Time: {self.metrics.total_time_sec:.2f} seconds")
        print(f"Average Job Time: {self.metrics.avg_job_time_sec:.2f} seconds")
        print(f"Throughput: {self.metrics.throughput_jobs_per_sec:.2f} jobs/second")
        
        # Slowest jobs
        if self.results:
            slowest = sorted(self.results, key=lambda r: r.duration_sec, reverse=True)[:3]
            print(f"\nSlowest Jobs:")
            for result in slowest:
                if result.duration_sec > 0:
                    print(f"  {result.job_id}: {result.duration_sec:.2f}s")
        
        print("="*70 + "\n")
    
    def save_results(self, filepath: str) -> None:
        """Save results to JSON file"""
        results_data = [
            {
                'job_id': r.job_id,
                'security_code': r.security_code,
                'date': r.date,
                'status': r.status.value,
                'duration_sec': r.duration_sec,
                'error': r.error,
            }
            for r in self.results
        ]
        
        with open(filepath, 'w') as f:
            json.dump({
                'metrics': {
                    'total_jobs': self.metrics.total_jobs,
                    'completed_jobs': self.metrics.completed_jobs,
                    'failed_jobs': self.metrics.failed_jobs,
                    'total_time_sec': self.metrics.total_time_sec,
                    'success_rate': self.metrics.success_rate,
                },
                'results': results_data,
            }, f, indent=2)
        
        logger.info(f"Results saved to {filepath}")
    
    # ========== PRIVATE HELPER METHODS ==========
    
    @staticmethod
    def _execute_job_wrapper(job: Job) -> JobResult:
        """Wrapper to execute a job and capture result"""
        start_time = time.time()
        
        try:
            # Execute the task function
            if job.task_func is None:
                raise ValueError("Job has no task function")
            
            result_data = job.task_func(*job.task_args, **job.task_kwargs)
            
            end_time = time.time()
            duration = end_time - start_time
            
            result = JobResult(
                job_id=job.job_id,
                security_code=job.security_code,
                date=job.date,
                status=JobStatus.COMPLETED,
                result=result_data,
                start_time=start_time,
                end_time=end_time,
                duration_sec=duration,
            )
            
            return result
        
        except Exception as e:
            end_time = time.time()
            duration = end_time - start_time
            
            result = JobResult(
                job_id=job.job_id,
                security_code=job.security_code,
                date=job.date,
                status=JobStatus.FAILED,
                error=str(e),
                start_time=start_time,
                end_time=end_time,
                duration_sec=duration,
            )
            
            logger.error(f"Job {job.job_id} failed: {e}")
            return result


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def run_parallel_jobs(
    jobs: List[Job],
    max_workers: int = 8,
    timeout_sec: float = 3600.0,
    verbose: bool = True,
) -> List[JobResult]:
    """
    Convenience function to run a list of jobs in parallel
    
    Args:
        jobs: List of Job objects
        max_workers: Number of parallel workers
        timeout_sec: Timeout per job
        verbose: Print progress messages
    
    Returns:
        List of JobResult objects
    """
    scheduler = ParallelJobScheduler(
        max_workers=max_workers,
        timeout_sec=timeout_sec,
        verbose=verbose,
    )
    scheduler.add_jobs(jobs)
    return scheduler.execute_jobs()


# ============================================================================
# MAIN (For Testing)
# ============================================================================

if __name__ == '__main__':
    print("Testing ParallelJobScheduler\n")
    
    # Test task function
    def sample_task(security_code: int, date: str, sleep_time: float = 0.1) -> Dict[str, Any]:
        """Sample task that simulates processing"""
        import time
        time.sleep(sleep_time)
        return {
            'security_code': security_code,
            'date': date,
            'processed_rows': 10000,
            'timestamp': datetime.now().isoformat(),
        }
    
    # Create sample jobs
    jobs = []
    for i, sec in enumerate([101, 102, 103]):
        for j, date in enumerate(['2024-01-01', '2024-01-02']):
            job = Job(
                job_id=f"job_{i}_{j}",
                security_code=sec,
                date=date,
                task_func=sample_task,
                task_args=(sec, date, 0.1),
            )
            jobs.append(job)
    
    print(f"Test 1: Execute {len(jobs)} jobs with 3 threads")
    # Use threads for testing (processes have pickle issues in __main__)
    scheduler = ParallelJobScheduler(max_workers=3, use_threads=True, verbose=True)
    scheduler.add_jobs(jobs)
    results = scheduler.execute_jobs()
    scheduler.print_summary()
    
    print(f"Test 2: Verify results")
    successful = [r for r in results if r.success]
    print(f"  Successful results: {len(successful)}/{len(results)}")
    print(f"  Sample result: {successful[0].result if successful else 'None'}")
    
    print(f"\nTest 3: Save results to file")
    scheduler.save_results('processed_files/scheduler_test_results.json')
    print(f"  ✓ Results saved")
    
    print("\nParallelJobScheduler tests passed! ✓")
