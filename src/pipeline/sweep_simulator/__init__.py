"""Sweep simulator package.

During the Stage 2 efficiency rewrite this package re-exports the legacy
monolithic simulator from `_legacy` while new layered implementations
(prep / kernel / emit) are built alongside it. Public API is unchanged;
every existing caller keeps working.

Spec: docs/superpowers/specs/2026-04-23-stage2-simulator-efficiency-design.md
"""

from ._legacy import (
    # Public entry points
    simulate_partition,
    simulate_sweep_matching,
    simulate_resting_phase,
    load_and_prepare_orders,
    build_remainder_df,
    # Helpers imported directly by tests/test_resting_phase.py
    _calc_resting_price,
    _calc_lit_resting_price,
    _get_session_end_time,
    _build_lit_order_book,
    # Constants used externally
    SWEEP_ORDER_TYPE,
    ELIGIBLE_MATCHING_ORDER_TYPES,
    INT64_SENTINEL,
)

__all__ = [
    'simulate_partition',
    'simulate_sweep_matching',
    'simulate_resting_phase',
    'load_and_prepare_orders',
    'build_remainder_df',
    'SWEEP_ORDER_TYPE',
    'ELIGIBLE_MATCHING_ORDER_TYPES',
    'INT64_SENTINEL',
]
