"""Parity harness for the Stage 2 simulator rewrite.

Implementation and tests share one file (5-scripts-per-sprint rule).
Spec: docs/superpowers/specs/2026-04-23-stage2-simulator-efficiency-design.md §7
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDiffReport:
    def test_empty_is_equivalent(self):
        from test_sweep_parity import DiffReport
        r = DiffReport(semantically_equivalent=True)
        assert r.semantically_equivalent is True
        assert r.trade_mismatches == []
        assert r.order_mismatches == []
