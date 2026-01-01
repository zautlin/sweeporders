"""
MASTER PIPELINE: Steps 1-8 Complete Sweep Orders Analysis

Runs all analysis steps in sequence:
1. Data Ingestion & Filtering with NBBO
2. Sweep Order Classification
3. (Skipped - would be Step 3)
4. Real Execution Metrics Calculation
5. Dark Pool Simulation Plan (informational)
6. Dark Pool Simulation with 3 Scenarios
7. Extended Analysis (Group 3, Time of Day, Size, Participant)
8. Statistical Comparison (T-tests, ANOVA, Effect Sizes)

Output: Complete sweep orders analysis with statistical validation
"""

import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def run_step(step_number, script_name, step_description):
    """
    Run a single step of the pipeline.
    
    Args:
        step_number: Step number (1-8)
        script_name: Python script to run
        step_description: Human readable description
    
    Returns:
        bool: True if successful, False if failed
    """
    logger.info("\n" + "=" * 100)
    logger.info(f"STEP {step_number}: {step_description}")
    logger.info("=" * 100)
    
    try:
        result = subprocess.run(
            [sys.executable, script_name],
            check=True,
            cwd=Path(__file__).parent,
            capture_output=False
        )
        logger.info(f"✅ STEP {step_number} COMPLETED SUCCESSFULLY")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ STEP {step_number} FAILED")
        logger.error(f"Error: {e}")
        return False
    except FileNotFoundError:
        logger.error(f"❌ Script not found: {script_name}")
        return False


def main():
    """Run the complete pipeline."""
    
    logger.info("\n" + "=" * 100)
    logger.info("SWEEP ORDERS ANALYSIS - COMPLETE PIPELINE (STEPS 1-8)")
    logger.info("=" * 100)
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    steps = [
        (1, 'step1_pipeline.py', 'Data Ingestion & Filtering with NBBO'),
        (2, 'step2_pipeline.py', 'Sweep Order Classification into 3 Groups'),
        (4, 'step4_pipeline.py', 'Real Execution Metrics Calculation'),
        (6, 'step6_pipeline.py', 'Dark Pool Simulation with 3 Scenarios'),
        (7, 'step7_pipeline.py', 'Extended Analysis (Group 3, Time/Size/Participant)'),
        (8, 'step8_pipeline.py', 'Statistical Analysis (T-Tests, ANOVA, Effect Sizes)'),
    ]
    
    completed = []
    failed = []
    
    for step_num, script, description in steps:
        success = run_step(step_num, script, description)
        if success:
            completed.append(step_num)
        else:
            failed.append(step_num)
            # Continue with next step even if current fails
            logger.warning(f"Continuing with next step...")
    
    # Final summary
    logger.info("\n" + "=" * 100)
    logger.info("PIPELINE EXECUTION SUMMARY")
    logger.info("=" * 100)
    logger.info(f"Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"\nSteps Completed: {completed}")
    logger.info(f"Steps Failed: {failed}")
    
    if failed:
        logger.error(f"\n❌ Pipeline completed with errors in steps: {failed}")
        return False
    else:
        logger.info("\n✅ COMPLETE PIPELINE EXECUTED SUCCESSFULLY")
        logger.info("\nAll outputs generated:")
        logger.info("  Data Files:")
        logger.info("    - processed_files/centrepoint_orders_raw.csv.gz")
        logger.info("    - processed_files/centrepoint_trades_raw.csv.gz")
        logger.info("    - processed_files/sweep_orders_classified.csv.gz")
        logger.info("  Analysis Results:")
        logger.info("    - processed_files/real_execution_metrics.csv")
        logger.info("    - processed_files/simulated_metrics_summary.csv.gz")
        logger.info("    - processed_files/simulated_metrics_detailed.csv.gz")
        logger.info("  Extended Analysis:")
        logger.info("    - processed_files/analysis_by_time_of_day.csv")
        logger.info("    - processed_files/analysis_by_order_size.csv")
        logger.info("    - processed_files/analysis_by_participant.csv")
        logger.info("    - processed_files/group3_unexecuted_analysis.csv")
        logger.info("  Statistical Results:")
        logger.info("    - processed_files/stats_paired_ttest_results.csv")
        logger.info("    - processed_files/stats_savings_ttest_results.csv")
        logger.info("    - processed_files/stats_anova_results.csv")
        logger.info("    - processed_files/stats_summary_table.csv")
        logger.info("\nDocumentation:")
        logger.info("    - STEP1_DETAILED_SUMMARY.md")
        logger.info("    - STEP2_DETAILED_SUMMARY.md")
        logger.info("    - STEP4_DETAILED_SUMMARY.md")
        logger.info("    - STEP5_SIMULATION_PLAN.md")
        logger.info("    - STEP6_DETAILED_SUMMARY.md")
        logger.info("    - STEP7_DETAILED_SUMMARY.md")
        logger.info("    - STEP8_DETAILED_SUMMARY.md")
        logger.info("\n" + "=" * 100)
        logger.info("Ready for executive presentation and implementation planning!")
        logger.info("=" * 100)
        return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
