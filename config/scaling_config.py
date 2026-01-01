"""
Enhanced Scaling Configuration System

Manages multi-date, multi-security, and parallel processing configuration.
Integrates with adaptive_config.py for automatic hardware optimization.

Key Features:
- YAML-like configuration for easy editing
- Job matrix generation (security_code, date combinations)
- Validation with meaningful error messages
- Integration with adaptive hardware detection
- Support for laptop, workstation, and server setups
"""

import os
import json
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Tuple, Optional
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================================
# DEFAULT CONFIGURATION
# ============================================================================

DEFAULT_SCALING_CONFIG = {
    'processing': {
        'mode': 'parallel',  # 'sequential' or 'parallel'
        'max_workers': 'auto',  # 'auto' or integer (e.g., 8)
        'chunk_size_mb': 'auto',  # 'auto' or integer (e.g., 400)
        'temp_dir': 'temp_chunks/',
        'cleanup_temp': True,
        'verbose': True,
    },
    
    'data_selection': {
        'security_codes': [],  # Empty = all codes in file
        'date_range': {
            'start': None,  # ISO format: '2024-01-01'
            'end': None,    # ISO format: '2024-12-31'
            'all_dates': False,  # If True, ignore date_range
        },
        'participant_ids': [],  # Empty = all participants
        'trading_hours': {
            'start': 10,  # 10 AM AEST
            'end': 16,    # 4 PM AEST
            'enabled': False,
        },
    },
    
    'pipeline_steps': {
        'step_1_ingest': True,
        'step_2_classify': True,
        'step_4_real_metrics': True,
        'step_5_simulation': True,
        'step_6_dark_pool': True,
        'step_7_extended_analysis': False,
        'step_8_statistics': False,
    },
    
    'simulation': {
        'dark_pool_scenarios': ['A', 'B', 'C'],
        'price_impact_percent': 0.05,
        'execution_delay_ms': 100,
    },
    
    'output': {
        'format': 'gzip',  # 'gzip' or 'parquet'
        'output_dir': 'processed_files/',
        'aggregate_by': ['security_code', 'date', 'participant_id'],
        'detailed_logs': True,
        'save_intermediate': False,  # Save results for each (sec, date) job
    },
}

# ============================================================================
# PRESET CONFIGURATIONS FOR DIFFERENT HARDWARE
# ============================================================================

LAPTOP_CONFIG = {
    'processing': {
        'mode': 'parallel',
        'max_workers': 2,
        'chunk_size_mb': 256,
        'temp_dir': 'temp_chunks/',
    },
    'data_selection': {
        'security_codes': [],
        'date_range': {'start': None, 'end': None, 'all_dates': False},
        'participant_ids': [],
    },
}

WORKSTATION_CONFIG = {
    'processing': {
        'mode': 'parallel',
        'max_workers': 7,
        'chunk_size_mb': 400,
        'temp_dir': 'temp_chunks/',
    },
    'data_selection': {
        'security_codes': [],
        'date_range': {'start': None, 'end': None, 'all_dates': False},
        'participant_ids': [],
    },
}

SERVER_CONFIG = {
    'processing': {
        'mode': 'parallel',
        'max_workers': 30,
        'chunk_size_mb': 2000,
        'temp_dir': 'temp_chunks/',
    },
    'data_selection': {
        'security_codes': [],
        'date_range': {'start': None, 'end': None, 'all_dates': False},
        'participant_ids': [],
    },
}

# ============================================================================
# DATACLASSES FOR TYPE SAFETY
# ============================================================================

@dataclass
class ProcessingConfig:
    """Processing parameters"""
    mode: str
    max_workers: int
    chunk_size_mb: int
    temp_dir: str
    cleanup_temp: bool = True
    verbose: bool = True

@dataclass
class DateRange:
    """Date range specification"""
    start: Optional[str] = None
    end: Optional[str] = None
    all_dates: bool = False
    
    def is_valid(self) -> bool:
        """Validate date range"""
        if self.all_dates:
            return True
        if self.start and self.end:
            try:
                start_dt = datetime.fromisoformat(self.start)
                end_dt = datetime.fromisoformat(self.end)
                return start_dt <= end_dt
            except ValueError:
                return False
        return self.start is None and self.end is None

@dataclass
class DataSelectionConfig:
    """Data selection parameters"""
    security_codes: List[int] = field(default_factory=list)
    date_range: DateRange = field(default_factory=DateRange)
    participant_ids: List[int] = field(default_factory=list)
    trading_hours: Dict = field(default_factory=lambda: {'start': 10, 'end': 16, 'enabled': False})

@dataclass
class SimulationConfig:
    """Simulation parameters"""
    dark_pool_scenarios: List[str] = field(default_factory=lambda: ['A', 'B', 'C'])
    price_impact_percent: float = 0.05
    execution_delay_ms: int = 100

@dataclass
class OutputConfig:
    """Output parameters"""
    format: str = 'gzip'
    output_dir: str = 'processed_files/'
    aggregate_by: List[str] = field(default_factory=lambda: ['security_code', 'date'])
    detailed_logs: bool = True
    save_intermediate: bool = False

@dataclass
class ScalingConfig:
    """Complete scaling configuration"""
    processing: ProcessingConfig
    data_selection: DataSelectionConfig
    pipeline_steps: Dict
    simulation: SimulationConfig
    output: OutputConfig


# ============================================================================
# CONFIGURATION MANAGEMENT
# ============================================================================

class ConfigManager:
    """Manages scaling configuration with validation and merging"""
    
    def __init__(self):
        self.config = None
        self.hardware_optimized = False
    
    def load_config(self, config_source: Optional[str] = None) -> ScalingConfig:
        """
        Load configuration from file, dict, or use defaults
        
        Args:
            config_source: 
                - None: use DEFAULT_SCALING_CONFIG
                - str path: load from JSON/YAML file
                - 'laptop'/'workstation'/'server': use preset
        
        Returns:
            ScalingConfig object
        """
        if config_source is None:
            raw_config = DEFAULT_SCALING_CONFIG.copy()
        elif config_source == 'laptop':
            raw_config = self._merge_configs(DEFAULT_SCALING_CONFIG, LAPTOP_CONFIG)
        elif config_source == 'workstation':
            raw_config = self._merge_configs(DEFAULT_SCALING_CONFIG, WORKSTATION_CONFIG)
        elif config_source == 'server':
            raw_config = self._merge_configs(DEFAULT_SCALING_CONFIG, SERVER_CONFIG)
        elif isinstance(config_source, str) and os.path.exists(config_source):
            raw_config = self._load_from_file(config_source)
        else:
            raise ValueError(f"Unknown config source: {config_source}")
        
        self.config = self._dict_to_dataclass(raw_config)
        return self.config
    
    def optimize_with_hardware(self, hardware_profile=None) -> 'ScalingConfig':
        """
        Optimize parameters based on hardware detection
        
        Args:
            hardware_profile: Optional HardwareProfile from adaptive_config
                If None, imports and uses adaptive_config.create_adaptive_config()
        
        Returns:
            Updated ScalingConfig
        """
        if hardware_profile is None:
            try:
                # Try absolute import first
                try:
                    from adaptive_config import create_adaptive_config  # noqa: F401
                except ImportError:
                    # Try relative import
                    import sys
                    from pathlib import Path
                    config_path = str(Path(__file__).parent)
                    if config_path not in sys.path:
                        sys.path.insert(0, config_path)
                    from adaptive_config import create_adaptive_config  # noqa: F401
                
                adaptive_config = create_adaptive_config()
                hardware_profile = adaptive_config
            except (ImportError, Exception) as e:
                logger.warning(f"adaptive_config not available ({e}), using manual config")
                if self.config is not None:
                    return self.config
                else:
                    raise ValueError("No configuration loaded")
        
        # Apply hardware-optimized values
        if self.config is not None:
            if self.config.processing.max_workers == 'auto':
                self.config.processing.max_workers = hardware_profile.max_workers
            
            if self.config.processing.chunk_size_mb == 'auto':
                self.config.processing.chunk_size_mb = hardware_profile.chunk_size_mb
            
            self.hardware_optimized = True
            logger.info(f"Hardware optimization applied: {hardware_profile.max_workers} workers, {hardware_profile.chunk_size_mb}MB chunks")
        
        return self.config if self.config is not None else ScalingConfig(
            processing=ProcessingConfig('parallel', 0, 0, 'temp_chunks/'),
            data_selection=DataSelectionConfig(),
            pipeline_steps={},
            simulation=SimulationConfig(),
            output=OutputConfig()
        )
    
    def save_config(self, filepath: str) -> None:
        """Save current configuration to JSON file"""
        if self.config is None:
            raise ValueError("No configuration loaded")
        config_dict = self._dataclass_to_dict(self.config)
        with open(filepath, 'w') as f:
            json.dump(config_dict, f, indent=2)
        logger.info(f"Configuration saved to {filepath}")
    
    def print_summary(self) -> None:
        """Print human-readable configuration summary"""
        if not self.config:
            logger.error("No configuration loaded")
            return
        
        print("\n" + "="*70)
        print("SCALING CONFIGURATION SUMMARY")
        print("="*70)
        
        print("\n[PROCESSING]")
        print(f"  Mode: {self.config.processing.mode}")
        print(f"  Workers: {self.config.processing.max_workers}")
        print(f"  Chunk Size: {self.config.processing.chunk_size_mb} MB")
        print(f"  Hardware Optimized: {self.hardware_optimized}")
        
        print("\n[DATA SELECTION]")
        sec_str = f"{len(self.config.data_selection.security_codes)} codes" if self.config.data_selection.security_codes else "All"
        print(f"  Securities: {sec_str}")
        
        if self.config.data_selection.date_range.all_dates:
            print(f"  Dates: All")
        elif self.config.data_selection.date_range.start and self.config.data_selection.date_range.end:
            print(f"  Dates: {self.config.data_selection.date_range.start} to {self.config.data_selection.date_range.end}")
        else:
            print(f"  Dates: None (full file)")
        
        part_str = f"{len(self.config.data_selection.participant_ids)} IDs" if self.config.data_selection.participant_ids else "All"
        print(f"  Participants: {part_str}")
        
        print("\n[PIPELINE STEPS]")
        for step, enabled in self.config.pipeline_steps.items():
            status = "✓" if enabled else "✗"
            print(f"  {status} {step}")
        
        print("\n[OUTPUT]")
        print(f"  Format: {self.config.output.format}")
        print(f"  Directory: {self.config.output.output_dir}")
        print(f"  Aggregate by: {', '.join(self.config.output.aggregate_by)}")
        
        print("\n" + "="*70 + "\n")
    
    # ========== PRIVATE HELPER METHODS ==========
    
    @staticmethod
    def _merge_configs(base: dict, override: dict) -> dict:
        """Deep merge override config into base config"""
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict):
                result[key] = ConfigManager._merge_configs(result[key], value)
            else:
                result[key] = value
        return result
    
    @staticmethod
    def _load_from_file(filepath: str) -> dict:
        """Load configuration from JSON or YAML file"""
        if filepath.endswith('.json'):
            with open(filepath, 'r') as f:
                return json.load(f)
        elif filepath.endswith(('.yaml', '.yml')):
            try:
                import yaml
                with open(filepath, 'r') as f:
                    return yaml.safe_load(f)
            except ImportError:
                raise ImportError("PyYAML required for YAML config files")
        else:
            raise ValueError("Config file must be .json or .yaml/.yml")
    
    @staticmethod
    def _dict_to_dataclass(config_dict: dict) -> ScalingConfig:
        """Convert dictionary to ScalingConfig dataclass"""
        return ScalingConfig(
            processing=ProcessingConfig(**config_dict['processing']),
            data_selection=DataSelectionConfig(
                security_codes=config_dict['data_selection']['security_codes'],
                date_range=DateRange(**config_dict['data_selection']['date_range']),
                participant_ids=config_dict['data_selection']['participant_ids'],
                trading_hours=config_dict['data_selection']['trading_hours'],
            ),
            pipeline_steps=config_dict['pipeline_steps'],
            simulation=SimulationConfig(**config_dict['simulation']),
            output=OutputConfig(**config_dict['output']),
        )
    
    @staticmethod
    def _dataclass_to_dict(config: ScalingConfig) -> dict:
        """Convert ScalingConfig dataclass back to dictionary"""
        return {
            'processing': asdict(config.processing),
            'data_selection': {
                'security_codes': config.data_selection.security_codes,
                'date_range': asdict(config.data_selection.date_range),
                'participant_ids': config.data_selection.participant_ids,
                'trading_hours': config.data_selection.trading_hours,
            },
            'pipeline_steps': config.pipeline_steps,
            'simulation': asdict(config.simulation),
            'output': asdict(config.output),
        }


# ============================================================================
# JOB MATRIX GENERATION
# ============================================================================

class JobMatrixGenerator:
    """Generates (security_code, date) job tuples from configuration"""
    
    @staticmethod
    def generate_job_matrix(
        config: ScalingConfig,
        available_securities: Optional[List[int]] = None,
        available_dates: Optional[List[str]] = None,
    ) -> List[Tuple[int, str]]:
        """
        Generate list of (security_code, date) tuples to process
        
        Args:
            config: ScalingConfig object
            available_securities: List of security codes in input file (optional, for validation)
            available_dates: List of dates in input file (optional, for validation)
        
        Returns:
            List of (security_code, date) tuples
        """
        # Determine which securities to process
        if config.data_selection.security_codes:
            securities = config.data_selection.security_codes
        elif available_securities:
            securities = available_securities
        else:
            securities = []  # Placeholder - will need to scan file
        
        # Determine which dates to process
        if config.data_selection.date_range.all_dates and available_dates:
            dates = available_dates
        elif config.data_selection.date_range.start and config.data_selection.date_range.end:
            dates = JobMatrixGenerator._generate_date_range(
                config.data_selection.date_range.start,
                config.data_selection.date_range.end
            )
        elif available_dates:
            dates = available_dates
        else:
            dates = []  # Placeholder - will need to scan file
        
        # Create job matrix
        job_matrix = [(sec, date) for sec in securities for date in dates]
        
        logger.info(f"Generated job matrix: {len(job_matrix)} jobs ({len(securities)} securities × {len(dates)} dates)")
        
        return job_matrix
    
    @staticmethod
    def _generate_date_range(start_str: str, end_str: str) -> List[str]:
        """
        Generate list of dates between start and end (inclusive)
        
        Args:
            start_str: ISO format date string (e.g., '2024-01-01')
            end_str: ISO format date string (e.g., '2024-01-31')
        
        Returns:
            List of date strings in ISO format
        """
        start_date = datetime.fromisoformat(start_str).date()
        end_date = datetime.fromisoformat(end_str).date()
        
        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current.isoformat())
            current += timedelta(days=1)
        
        return dates


# ============================================================================
# VALIDATION
# ============================================================================

class ConfigValidator:
    """Validates scaling configuration for errors"""
    
    @staticmethod
    def validate(config: ScalingConfig) -> Tuple[bool, List[str]]:
        """
        Validate configuration
        
        Returns:
            (is_valid, list_of_errors)
        """
        errors = []
        
        # Validate processing
        if config.processing.mode not in ['sequential', 'parallel']:
            errors.append(f"Invalid processing mode: {config.processing.mode}")
        
        # Allow 'auto' or integer for max_workers
        if not (isinstance(config.processing.max_workers, int) and config.processing.max_workers >= 1):
            if config.processing.max_workers != 'auto' and not isinstance(config.processing.max_workers, int):
                errors.append(f"max_workers must be 'auto' or integer >= 1, got {config.processing.max_workers}")
        
        # Allow 'auto' or integer for chunk_size_mb
        if not (isinstance(config.processing.chunk_size_mb, int) and config.processing.chunk_size_mb >= 32):
            if config.processing.chunk_size_mb != 'auto' and not isinstance(config.processing.chunk_size_mb, int):
                errors.append(f"chunk_size_mb must be 'auto' or integer >= 32, got {config.processing.chunk_size_mb}")
        
        # Validate data selection
        if not config.data_selection.date_range.is_valid():
            errors.append("Invalid date range")
        
        # Validate output
        if not os.path.exists(config.output.output_dir):
            try:
                os.makedirs(config.output.output_dir, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create output directory: {e}")
        
        return len(errors) == 0, errors


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def load_scaling_config(config_source: Optional[str] = None, optimize: bool = True) -> ScalingConfig:
    """
    Convenience function to load and optimize configuration
    
    Usage:
        # Use defaults, auto-optimize hardware
        config = load_scaling_config()
        
        # Use preset
        config = load_scaling_config('laptop')
        
        # Load from file
        config = load_scaling_config('config/my_scaling.json')
    """
    manager = ConfigManager()
    config = manager.load_config(config_source)
    
    if optimize:
        config = manager.optimize_with_hardware()
    
    # Validate
    is_valid, errors = ConfigValidator.validate(config)
    if not is_valid:
        logger.error("Configuration validation failed:")
        for error in errors:
            logger.error(f"  - {error}")
        raise ValueError("Invalid configuration")
    
    return config


def print_config_summary(config: ScalingConfig) -> None:
    """Convenience function to print configuration summary"""
    manager = ConfigManager()
    manager.config = config
    manager.print_summary()


# ============================================================================
# MAIN (For Testing)
# ============================================================================

if __name__ == '__main__':
    print("Testing Scaling Configuration System\n")
    
    # Test 1: Load default config
    print("Test 1: Load default configuration")
    config = load_scaling_config(optimize=True)
    print(f"  ✓ Config loaded: {config.processing.max_workers} workers, {config.processing.chunk_size_mb}MB chunks\n")
    
    # Test 2: Load preset
    print("Test 2: Load laptop preset")
    laptop_config = load_scaling_config('laptop', optimize=False)
    print(f"  ✓ Laptop config: {laptop_config.processing.max_workers} workers, {laptop_config.processing.chunk_size_mb}MB chunks\n")
    
    # Test 3: Generate job matrix
    print("Test 3: Generate job matrix")
    securities = [101, 102, 103]
    dates = ['2024-01-01', '2024-01-02', '2024-01-03']
    job_matrix = JobMatrixGenerator.generate_job_matrix(
        config,
        available_securities=securities,
        available_dates=dates
    )
    print(f"  ✓ Job matrix created: {len(job_matrix)} jobs")
    print(f"    Sample: {job_matrix[:3]}\n")
    
    # Test 4: Print summary
    print("Test 4: Configuration summary")
    print_config_summary(config)
    
    # Test 5: Save and load from file
    print("Test 5: Save and load from file")
    manager = ConfigManager()
    manager.config = config
    manager.save_config('config/test_scaling_config.json')
    loaded_config = load_scaling_config('config/test_scaling_config.json')
    print(f"  ✓ Config saved and reloaded\n")
    
    print("All tests passed! ✓")
