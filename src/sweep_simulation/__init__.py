"""
Sweep Order Matching Simulation Package

This package provides functionality to simulate orderbook matching between
sweep orders (type 2048) and incoming Centre Point orders.

All modules now use simple functions instead of classes.
"""

# Import main functions from each module
from . import nbbo_provider
from . import metrics_calculator
from . import simulator
from . import comparison_reporter

__all__ = [
    'nbbo_provider',
    'metrics_calculator',
    'simulator',
    'comparison_reporter',
]
