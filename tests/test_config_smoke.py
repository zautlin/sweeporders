"""Smoke tests for the flat top-level config.py (Task 2 of lean port)."""
import sys
import os

# Ensure the repo root is on sys.path so `import config` resolves to the new
# top-level config.py, not any src/config package.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_config_imports_and_exposes_expected_names():
    import config
    # Schema
    assert hasattr(config, "col")
    assert hasattr(config, "COLUMN_MAPPING")
    # Order types
    assert config.SWEEP_ORDER_TYPE == 2048
    assert 64   in config.ELIGIBLE_MATCHING_ORDER_TYPES
    assert 256  in config.ELIGIBLE_MATCHING_ORDER_TYPES
    assert 2048 in config.ELIGIBLE_MATCHING_ORDER_TYPES
    assert 4096 in config.ELIGIBLE_MATCHING_ORDER_TYPES
    assert 4098 in config.ELIGIBLE_MATCHING_ORDER_TYPES
    # NBBO sentinel
    assert config.INT64_SENTINEL == -9223372036854775808
    # Thresholds
    assert config.MIN_ORDERS_THRESHOLD == 100
    assert config.MIN_TRADES_THRESHOLD == 10
    # Volume bucket
    assert config.VOLUME_BUCKET_METHOD == "quartile"
    # Paths
    assert config.RAW_DIR.name == "raw"
    assert config.PROCESSED_DIR.name == "processed"
    assert config.OUTPUTS_DIR.name == "outputs"
    assert config.REPORTS_DIR.name == "reports"
    # Phase 2 resting (flag exists, doesn't matter what default is)
    assert hasattr(config, "SIMULATE_RESTING_PHASE")
    # Parallel default flipped to True for the lean port
    assert config.ENABLE_PARALLEL_PROCESSING is True
    # Stream mode removed (PROCESSING_MODE may still exist as 'file' or 'memory')
    assert config.PROCESSING_MODE in ("file", "memory")
    assert config.NBBO_SOURCE == "INTERNAL"
    # No-longer-existing flags should be gone
    assert not hasattr(config, "USE_DUCKDB_IO")
    assert not hasattr(config, "USE_POLARS_TRANSFORMS")


def test_column_accessor_returns_real_column_names():
    import config
    # Just check `col.common.timestamp` resolves to a string (whichever specific
    # column name is mapped — implementation detail of COLUMN_MAPPING).
    assert isinstance(config.col.common.timestamp, str)
    assert isinstance(config.col.common.orderid, str)


def test_worker_count_helper():
    import config
    n = config.auto_worker_count() if hasattr(config, "auto_worker_count") else None
    if n is not None:
        assert isinstance(n, int) and n >= 1
