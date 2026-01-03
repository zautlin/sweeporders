"""
Sweep Order Matching Simulation Package

This package provides functionality to simulate orderbook matching between
sweep orders (type 2048) and incoming Centre Point orders.
"""

from .simulator import SweepMatchingSimulator, OrderLoader
from .nbbo_provider import NBBOProvider
from .metrics_calculator import SimulatedMetricsCalculator
from .comparison_reporter import GroupComparator, ComparisonReporter

__all__ = [
    'SweepMatchingSimulator',
    'OrderLoader',
    'NBBOProvider',
    'SimulatedMetricsCalculator',
    'GroupComparator',
    'ComparisonReporter',
]
