#!/usr/bin/env python3
"""Interactive console for the Centre Point Sweep Order Pipeline.

Run from the src/ directory:
    python console.py
"""

import sys
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config.config as config
from pipeline.pipeline_config import build_runtime_config, setup_directories
from pipeline.pipeline_output import print_pipeline_header, print_execution_summary
from pipeline.pipeline_stages import execute_pipeline_stages
from discovery.security_discovery import SecurityDiscovery

# ── Constants ─────────────────────────────────────────────────────────────────

WIDTH = 72
DIVIDER = "─" * WIDTH
HEADER  = "═" * WIDTH

STAGE_DESCRIPTIONS = {
    1: "Data Extraction & Preparation     (steps 1-6)",
    2: "Simulation — dark pool matching   (step 7)",
    3: "Calculate Metrics — real + sim    (steps 8-9)",
    4: "Metrics Comparison                (step 10)",
    5: "Per-Security Analysis             (steps 11-12 + volume)",
    6: "Cross-Security Aggregation",
}


# ── Configuration state ───────────────────────────────────────────────────────

class ConsoleConfig:
    """Holds mutable session configuration chosen by the user."""

    def __init__(self):
        self.date          = config.DATE
        self.ticker        = config.TICKER
        self.orderbookid   = None   # when set, takes priority over ticker
        self.auto_discover = False
        self.parallel      = config.ENABLE_PARALLEL_PROCESSING
        self.enable_stats  = config.ENABLE_STATISTICAL_TESTS
        self.min_orders    = config.MIN_ORDERS_THRESHOLD
        self.min_trades    = config.MIN_TRADES_THRESHOLD

    # ── Security description ───────────────────────────────────────────────

    def security_label(self) -> str:
        if self.auto_discover:
            return "Auto-discover all valid securities"
        if self.orderbookid:
            return f"OrderbookID {self.orderbookid}"
        return f"{self.ticker.upper()} (ticker)"

    def describe(self) -> list[str]:
        return [
            f"  Date         {self.date}",
            f"  Security     {self.security_label()}",
            f"  Mode         {'Parallel' if self.parallel else 'Sequential'}",
            f"  Statistics   {'Enabled' if self.enable_stats else 'Disabled'}",
            f"  Min orders   {self.min_orders}   Min trades   {self.min_trades}",
        ]

    # ── Build fake argparse namespace ─────────────────────────────────────

    def to_args(self, stages: list[int] | None = None) -> types.SimpleNamespace:
        """Return a SimpleNamespace that satisfies build_runtime_config's interface."""
        use_ticker = self.ticker if (not self.orderbookid and not self.auto_discover) else None
        return types.SimpleNamespace(
            date           = self.date,
            ticker         = use_ticker,
            orderbookid    = self.orderbookid,
            auto_discover  = self.auto_discover,
            parallel       = self.parallel,
            sequential     = not self.parallel,
            enable_stats   = self.enable_stats,
            disable_stats  = not self.enable_stats,
            stage          = stages,
            list_dates     = False,
            list_securities= False,
            min_orders     = self.min_orders,
            min_trades     = self.min_trades,
        )


# ── Console UI ────────────────────────────────────────────────────────────────

class PipelineConsole:
    """Menu-driven interactive console."""

    def __init__(self):
        self.cfg = ConsoleConfig()

    # ── Entry point ───────────────────────────────────────────────────────

    def run(self):
        self._banner()
        while True:
            self._main_menu()
            choice = self._prompt("Select").strip().lower()

            if   choice == 'q':  self._quit()
            elif choice == 'c':  self._configure_menu()
            elif choice == 'l':  self._list_securities()
            elif choice == 'a':  self._run_stages(list(range(1, 7)))
            elif choice == 'r':  self._run_custom_stages_menu()
            elif choice in '123456':
                self._run_stages([int(choice)])
            else:
                _err("Unknown option.")

    # ── Menus ─────────────────────────────────────────────────────────────

    def _banner(self):
        print(f"\n{HEADER}")
        print("  CENTRE POINT SWEEP PIPELINE  —  Interactive Console")
        print(HEADER)

    def _main_menu(self):
        print(f"\n{DIVIDER}")
        print("  Configuration:")
        for line in self.cfg.describe():
            print(line)
        print()
        print("  [c]  Configure settings")
        print("  [l]  List available securities for selected date")
        print()
        for num, desc in STAGE_DESCRIPTIONS.items():
            print(f"  [{num}]  Stage {num}  —  {desc}")
        print()
        print("  [r]  Run a custom set of stages")
        print("  [a]  Run ALL stages  (1 → 6)")
        print("  [q]  Quit")

    def _configure_menu(self):
        while True:
            print(f"\n{DIVIDER}")
            print("  CONFIGURE")
            print(DIVIDER)
            for line in self.cfg.describe():
                print(line)
            print()
            print("  [d]  Date              (YYYYMMDD)")
            print("  [t]  Ticker")
            print("  [o]  OrderbookID")
            print("  [a]  Toggle auto-discover")
            print("  [p]  Toggle parallel / sequential mode")
            print("  [s]  Toggle statistical tests")
            print("  [m]  Set min-orders / min-trades thresholds")
            print("  [b]  Back")

            choice = self._prompt("Select").strip().lower()

            if   choice == 'b': break
            elif choice == 'd': self._set_date()
            elif choice == 't': self._set_ticker()
            elif choice == 'o': self._set_orderbookid()
            elif choice == 'a': self._toggle_auto_discover()
            elif choice == 'p': self._toggle_parallel()
            elif choice == 's': self._toggle_stats()
            elif choice == 'm': self._set_thresholds()
            else: _err("Unknown option.")

    # ── Configuration actions ─────────────────────────────────────────────

    def _set_date(self):
        val = self._prompt(f"Date (YYYYMMDD) [{self.cfg.date}]").strip()
        if val:
            if len(val) == 8 and val.isdigit():
                self.cfg.date = val
                print(f"  Date set to {val}")
            else:
                _err("Invalid date format — use YYYYMMDD (e.g. 20240905).")

    def _set_ticker(self):
        val = self._prompt(f"Ticker [{self.cfg.ticker}]").strip()
        if val:
            self.cfg.ticker        = val.lower()
            self.cfg.orderbookid   = None
            self.cfg.auto_discover = False
            print(f"  Ticker set to {val.upper()}")

    def _set_orderbookid(self):
        val = self._prompt(f"OrderbookID [{self.cfg.orderbookid or 'none'}]").strip()
        if val:
            try:
                self.cfg.orderbookid   = int(val)
                self.cfg.auto_discover = False
                print(f"  OrderbookID set to {val}")
            except ValueError:
                _err("OrderbookID must be an integer.")

    def _toggle_auto_discover(self):
        self.cfg.auto_discover = not self.cfg.auto_discover
        if self.cfg.auto_discover:
            self.cfg.orderbookid = None
        state = "ON" if self.cfg.auto_discover else "OFF"
        print(f"  Auto-discover: {state}")

    def _toggle_parallel(self):
        self.cfg.parallel = not self.cfg.parallel
        state = "Parallel" if self.cfg.parallel else "Sequential"
        print(f"  Processing mode: {state}")

    def _toggle_stats(self):
        self.cfg.enable_stats = not self.cfg.enable_stats
        state = "Enabled" if self.cfg.enable_stats else "Disabled"
        print(f"  Statistics: {state}")

    def _set_thresholds(self):
        val = self._prompt(f"Min orders [{self.cfg.min_orders}]").strip()
        if val:
            try:
                self.cfg.min_orders = int(val)
            except ValueError:
                _err("Must be an integer.")
                return
        val = self._prompt(f"Min trades [{self.cfg.min_trades}]").strip()
        if val:
            try:
                self.cfg.min_trades = int(val)
            except ValueError:
                _err("Must be an integer.")
                return
        print(f"  Thresholds: min_orders={self.cfg.min_orders}  min_trades={self.cfg.min_trades}")

    # ── Listing ───────────────────────────────────────────────────────────

    def _list_securities(self):
        if not self.cfg.date:
            _err("No date configured. Use [c] → [d] to set a date first.")
            return
        print(f"\n  Discovering securities for {self.cfg.date} …")
        try:
            discovery = SecurityDiscovery(
                min_orders=self.cfg.min_orders,
                min_trades=self.cfg.min_trades,
            )
            discovery.print_summary(self.cfg.date)
        except Exception as e:
            _err(f"Discovery failed: {e}")

    # ── Stage execution ───────────────────────────────────────────────────

    def _run_custom_stages_menu(self):
        print()
        for num, desc in STAGE_DESCRIPTIONS.items():
            print(f"    {num}  {desc}")
        val = self._prompt("Stages to run (e.g. 1 2 3)").strip()
        if not val:
            return
        try:
            stages = [int(s) for s in val.split()]
            if not stages or not all(1 <= s <= 6 for s in stages):
                raise ValueError
        except ValueError:
            _err("Enter space-separated stage numbers between 1 and 6.")
            return
        self._run_stages(stages)

    def _run_stages(self, stages: list[int]):
        label = ", ".join(map(str, stages))
        print(f"\n{HEADER}")
        print(f"  Running stage(s): {label}")
        print(HEADER)

        args = self.cfg.to_args(stages=stages)
        try:
            start          = time.time()
            runtime_config = build_runtime_config(args)
            print_pipeline_header(runtime_config)
            setup_directories()
            data, _        = execute_pipeline_stages(runtime_config)
            elapsed        = time.time() - start
            print_execution_summary(data, runtime_config, elapsed)
        except ValueError as e:
            _err(str(e))
        except KeyboardInterrupt:
            print("\n\n  Interrupted.")
        except Exception as e:
            _err(f"Unexpected error: {e}")
            raise

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _prompt(msg: str) -> str:
        try:
            return input(f"\n  > {msg}: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

    @staticmethod
    def _quit():
        print("\nGoodbye.\n")
        sys.exit(0)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _err(msg: str):
    print(f"\n  ERROR: {msg}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    PipelineConsole().run()


if __name__ == "__main__":
    main()
