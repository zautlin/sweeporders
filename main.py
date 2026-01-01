"""
Main Orchestration Script
Runs the complete sweep orders analysis pipeline
"""

import sys
from pathlib import Path
import logging
import time

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from ingest import extract_centrepoint_orders
from match_trades import match_trades
from book import build_dark_book
from classify import filter_sweep_orders, classify_sweep_outcomes
from simulate import simulate_scenario_a, simulate_scenario_b, simulate_scenario_c, load_dark_book
from report import generate_reports

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Run complete pipeline."""
    start_time = time.time()
    output_dir = 'processed_files'
    Path(output_dir).mkdir(exist_ok=True)
    
    logger.info("=" * 80)
    logger.info("SWEEP ORDERS ANALYSIS PIPELINE")
    logger.info("=" * 80)
    
    # Phase 1: Data Ingestion
    logger.info("\n[Phase 1] DATA INGESTION")
    logger.info("-" * 80)
    
    logger.info("Phase 1.1: Extracting Centre Point Orders...")
    cp_orders = extract_centrepoint_orders('data/orders/drr_orders.csv', output_dir)
    logger.info(f"✓ Extracted {len(cp_orders):,} Centre Point orders")
    
    logger.info("\nPhase 1.2: Matching trades to Centre Point orders...")
    trades, trades_agg = match_trades(
        'data/trades/drr_trades_segment_1.csv',
        cp_orders['order_id'].tolist(),
        output_dir
    )
    logger.info(f"✓ Matched {len(trades):,} trades ({len(trades_agg):,} unique orders)")
    
    logger.info("\nPhase 1.3: Building dark order book...")
    dark_book, order_index = build_dark_book(cp_orders, output_dir)
    logger.info(f"✓ Built dark book with {len(order_index):,} orders")
    
    # Phase 2: Classification
    logger.info("\n[Phase 2] ORDER CLASSIFICATION")
    logger.info("-" * 80)
    
    logger.info("Phase 2.1: Filtering sweep orders...")
    sweep_orders = filter_sweep_orders(cp_orders, trades_agg, output_dir)
    logger.info(f"✓ Filtered {len(sweep_orders):,} sweep orders")
    
    logger.info("\nPhase 2.2: Classifying sweep order outcomes...")
    scenario_a, scenario_b, scenario_c, summary = classify_sweep_outcomes(sweep_orders, output_dir)
    logger.info(f"✓ Scenario A: {len(scenario_a):,} (immediate full)")
    logger.info(f"✓ Scenario B: {len(scenario_b):,} (eventual full)")
    logger.info(f"✓ Scenario C: {len(scenario_c):,} (partial/none)")
    
    # Phase 3: Simulations
    logger.info("\n[Phase 3] DARK BOOK SIMULATIONS")
    logger.info("-" * 80)
    
    dark_book = load_dark_book(output_dir)
    
    logger.info("Phase 3.1: Simulating Scenario A (immediate full in dark)...")
    sim_a = simulate_scenario_a(scenario_a, dark_book, cp_orders, output_dir)
    logger.info(f"✓ Simulated {len(sim_a):,} orders")
    
    logger.info("\nPhase 3.2: Simulating Scenario B (residual in dark)...")
    sim_b = simulate_scenario_b(scenario_c, dark_book, cp_orders, output_dir)
    logger.info(f"✓ Simulated {len(sim_b):,} orders")
    
    logger.info("\nPhase 3.3: Simulating Scenario C (unfilled in dark)...")
    sim_c = simulate_scenario_c(scenario_c, dark_book, cp_orders, output_dir)
    logger.info(f"✓ Simulated {len(sim_c):,} orders")
    
    # Phase 4: Reporting
    logger.info("\n[Phase 4] REPORTING & METRICS")
    logger.info("-" * 80)
    
    logger.info("Generating comprehensive reports...")
    summary, detailed, order_detail, cost = generate_reports(output_dir)
    logger.info("✓ Generated 5 report files")
    
    # Summary
    end_time = time.time()
    duration = end_time - start_time
    
    logger.info("\n" + "=" * 80)
    logger.info("PIPELINE COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Total duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
    logger.info(f"\nOutput directory: {output_dir}/")
    logger.info("\nGenerated files:")
    logger.info("  Data files:")
    logger.info("    - centrepoint_orders_raw.csv.gz")
    logger.info("    - centrepoint_trades_raw.csv.gz")
    logger.info("    - centrepoint_trades_agg.csv.gz")
    logger.info("    - sweep_orders_with_trades.csv.gz")
    logger.info("    - dark_book_state.pkl")
    logger.info("    - order_index.pkl")
    logger.info("  Scenario classification:")
    logger.info("    - scenario_a_immediate_full.csv.gz")
    logger.info("    - scenario_b_eventual_full.csv.gz")
    logger.info("    - scenario_c_partial_none.csv.gz")
    logger.info("  Simulation results:")
    logger.info("    - scenario_a_simulation_results.csv.gz")
    logger.info("    - scenario_b_simulation_results.csv.gz")
    logger.info("    - scenario_c_simulation_results.csv.gz")
    logger.info("  Reports (CSV.GZ):")
    logger.info("    - scenario_comparison_summary.csv.gz")
    logger.info("    - scenario_detailed_comparison.csv.gz")
    logger.info("    - order_level_detail.csv.gz")
    logger.info("    - execution_cost_comparison.csv.gz")
    logger.info("    - by_participant.csv.gz")
    logger.info("    - scenario_summary.csv")
    logger.info("=" * 80)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)
