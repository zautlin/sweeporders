"""
Phase 4: Reporting and Metrics Aggregation
Generates comprehensive comparison reports
"""

import pandas as pd
import numpy as np
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def read_csv_safe(filepath):
    """Read CSV file with empty file handling."""
    try:
        return pd.read_csv(filepath)
    except (pd.errors.EmptyDataError, FileNotFoundError):
        return pd.DataFrame()


def generate_reports(output_dir: str):
    """Generate all comparison reports."""
    logger.info("Phase 4: Generating reports...")
    
    # Load simulation results with empty file handling
    sim_a = read_csv_safe(Path(output_dir) / 'scenario_a_simulation_results.csv.gz')
    sim_b = read_csv_safe(Path(output_dir) / 'scenario_b_simulation_results.csv.gz')
    sim_c = read_csv_safe(Path(output_dir) / 'scenario_c_simulation_results.csv.gz')
    
    # Load original scenario data
    scenario_a_orders = read_csv_safe(Path(output_dir) / 'scenario_a_immediate_full.csv.gz')
    scenario_b_c_orders = read_csv_safe(Path(output_dir) / 'scenario_c_partial_none.csv.gz')
    
    logger.info(f"Loaded {len(sim_a)} Scenario A results")
    logger.info(f"Loaded {len(sim_b)} Scenario B results")
    logger.info(f"Loaded {len(sim_c)} Scenario C results")
    
    # Report 1: Scenario Comparison Summary
    logger.info("Generating scenario comparison summary...")
    
    b_count = len(scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0]) if len(scenario_b_c_orders) > 0 else 0
    c_count = len(scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0]) if len(scenario_b_c_orders) > 0 else 0
    
    summary = pd.DataFrame({
        'Scenario': ['Real Execution (A)', 'Real Execution (B)', 'Real Execution (C)',
                     'Simulated Dark (A)', 'Simulated Dark (B)', 'Simulated Dark (C)'],
        'OrderCount': [
            len(scenario_a_orders), 
            b_count,
            c_count,
            len(sim_a), 
            len(sim_b), 
            len(sim_c)
        ],
        'TotalQuantity': [
            scenario_a_orders['quantity'].sum() if len(scenario_a_orders) > 0 else 0,
            scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0]['quantity'].sum() if b_count > 0 else 0,
            scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0]['quantity'].sum() if c_count > 0 else 0,
            sim_a['simulated_num_matches'].sum() if len(sim_a) > 0 else 0,
            sim_b['residual_fill_qty'].sum() if len(sim_b) > 0 else 0,
            sim_c['simulated_fill_ratio'].sum() if len(sim_c) > 0 else 0
        ],
        'AvgFillRatio': [
            scenario_a_orders['fill_ratio'].mean() if len(scenario_a_orders) > 0 else 0,
            scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0]['fill_ratio'].mean() if b_count > 0 else 0,
            scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0]['fill_ratio'].mean() if c_count > 0 else 0,
            sim_a['simulated_fill_ratio'].mean() if len(sim_a) > 0 else 0,
            sim_b['simulated_fill_ratio'].mean() if len(sim_b) > 0 else 0,
            sim_c['simulated_fill_ratio'].mean() if len(sim_c) > 0 else 0
        ]
    })
    summary.to_csv(Path(output_dir) / 'scenario_comparison_summary.csv.gz', compression='gzip', index=False)
    logger.info(f"Saved scenario_comparison_summary.csv.gz")
    
    # Report 2: Detailed Comparison
    logger.info("Generating detailed comparison...")
    
    all_real_orders = pd.concat([scenario_a_orders, scenario_b_c_orders], ignore_index=True) if len(scenario_a_orders) > 0 or len(scenario_b_c_orders) > 0 else pd.DataFrame()
    
    detailed = pd.DataFrame({
        'Metric': ['Total Orders', 'Avg Fill Ratio', 'Median Fill Ratio', 'Std Dev Fill Ratio',
                   'Orders with Full Fill (>=99%)', 'Avg Execution Price'],
        'Real_Execution': [
            len(all_real_orders),
            all_real_orders['fill_ratio'].mean() if len(all_real_orders) > 0 else 0,
            all_real_orders['fill_ratio'].median() if len(all_real_orders) > 0 else 0,
            all_real_orders['fill_ratio'].std() if len(all_real_orders) > 0 else 0,
            len(scenario_a_orders),
            scenario_a_orders['avg_execution_price'].mean() if len(scenario_a_orders) > 0 else 0
        ],
        'Simulated_A': [
            len(sim_a),
            sim_a['simulated_fill_ratio'].mean() if len(sim_a) > 0 else 0,
            sim_a['simulated_fill_ratio'].median() if len(sim_a) > 0 else 0,
            sim_a['simulated_fill_ratio'].std() if len(sim_a) > 0 else 0,
            (sim_a['simulated_fill_ratio'] >= 0.99).sum() if len(sim_a) > 0 else 0,
            sim_a['simulated_execution_price'].mean() if len(sim_a) > 0 else 0
        ],
        'Simulated_B': [
            len(sim_b),
            sim_b['simulated_fill_ratio'].mean() if len(sim_b) > 0 else 0,
            sim_b['simulated_fill_ratio'].median() if len(sim_b) > 0 else 0,
            sim_b['simulated_fill_ratio'].std() if len(sim_b) > 0 else 0,
            (sim_b['simulated_fill_ratio'] >= 0.99).sum() if len(sim_b) > 0 else 0,
            0
        ],
        'Simulated_C': [
            len(sim_c),
            sim_c['simulated_fill_ratio'].mean() if len(sim_c) > 0 else 0,
            sim_c['simulated_fill_ratio'].median() if len(sim_c) > 0 else 0,
            sim_c['simulated_fill_ratio'].std() if len(sim_c) > 0 else 0,
            (sim_c['simulated_fill_ratio'] >= 0.99).sum() if len(sim_c) > 0 else 0,
            sim_c['simulated_execution_price'].mean() if len(sim_c) > 0 else 0
        ]
    })
    detailed.to_csv(Path(output_dir) / 'scenario_detailed_comparison.csv.gz', compression='gzip', index=False)
    logger.info(f"Saved scenario_detailed_comparison.csv.gz")
    
    # Report 3: Order-level detail
    logger.info("Generating order-level detail...")
    
    order_details = []
    
    if len(scenario_a_orders) > 0 and len(sim_a) > 0:
        order_detail_a = scenario_a_orders[['order_id', 'security_code', 'side', 'price', 'quantity', 'fill_ratio', 'avg_execution_price']].copy()
        order_detail_a = order_detail_a.merge(sim_a[['order_id', 'simulated_fill_ratio', 'simulated_execution_price']], on='order_id', how='left')
        order_detail_a['Scenario'] = 'A'
        order_details.append(order_detail_a)
    
    if len(scenario_b_c_orders) > 0 and len(sim_b) > 0:
        partial_orders = scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0].copy()
        if len(partial_orders) > 0:
            order_detail_b = partial_orders[['order_id', 'security_code', 'side', 'price', 'quantity', 'fill_ratio']].copy()
            order_detail_b = order_detail_b.merge(sim_b[['order_id', 'simulated_fill_ratio']], on='order_id', how='left')
            order_detail_b['Scenario'] = 'B'
            order_details.append(order_detail_b)
    
    if len(scenario_b_c_orders) > 0 and len(sim_c) > 0:
        unfilled_orders = scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0].copy()
        if len(unfilled_orders) > 0:
            order_detail_c = unfilled_orders[['order_id', 'security_code', 'side', 'price', 'quantity', 'fill_ratio']].copy()
            order_detail_c = order_detail_c.merge(sim_c[['order_id', 'simulated_fill_ratio']], on='order_id', how='left')
            order_detail_c['Scenario'] = 'C'
            order_details.append(order_detail_c)
    
    all_order_detail = pd.concat(order_details, ignore_index=True) if order_details else pd.DataFrame()
    all_order_detail.to_csv(Path(output_dir) / 'order_level_detail.csv.gz', compression='gzip', index=False)
    logger.info(f"Saved order_level_detail.csv.gz with {len(all_order_detail):,} orders")
    
    # Report 4: Execution cost comparison
    logger.info("Generating execution cost comparison...")
    
    real_cost_a = (scenario_a_orders['fill_ratio'] * scenario_a_orders['avg_execution_price']).sum() if len(scenario_a_orders) > 0 else 0
    real_cost_bc = (scenario_b_c_orders['fill_ratio'] * scenario_b_c_orders['avg_execution_price']).sum() if len(scenario_b_c_orders) > 0 else 0
    
    sim_cost_a = (sim_a['simulated_fill_ratio'] * sim_a['simulated_execution_price']).sum() if len(sim_a) > 0 else 0
    sim_cost_b = (sim_b['simulated_fill_ratio'] * sim_b['simulated_fill_ratio']).sum() if len(sim_b) > 0 else 0
    sim_cost_c = (sim_c['simulated_fill_ratio'] * sim_c['simulated_execution_price']).sum() if len(sim_c) > 0 else 0
    
    cost_comparison = pd.DataFrame({
        'Scenario': ['Real Execution', 'Simulated A', 'Simulated B', 'Simulated C'],
        'TotalExecutionCost': [real_cost_a + real_cost_bc, sim_cost_a, sim_cost_b, sim_cost_c],
        'AvgCostPerShare': [
            (real_cost_a + real_cost_bc) / (scenario_a_orders['quantity'].sum() + scenario_b_c_orders['quantity'].sum()) if len(all_real_orders) > 0 else 0,
            sim_cost_a / scenario_a_orders['quantity'].sum() if len(sim_a) > 0 and len(scenario_a_orders) > 0 else 0,
            sim_cost_b / scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0]['quantity'].sum() if len(sim_b) > 0 and b_count > 0 else 0,
            sim_cost_c / scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0]['quantity'].sum() if len(sim_c) > 0 and c_count > 0 else 0
        ]
    })
    cost_comparison.to_csv(Path(output_dir) / 'execution_cost_comparison.csv.gz', compression='gzip', index=False)
    logger.info(f"Saved execution_cost_comparison.csv.gz")
    
    # Report 5: By participant
    logger.info("Generating by-participant summary...")
    all_orders = read_csv_safe(Path(output_dir) / 'centrepoint_orders_raw.csv.gz')
    
    by_participant = []
    if len(all_orders) > 0:
        for participant_id in all_orders['participantid'].unique():
            participant_orders = all_orders[all_orders['participantid'] == participant_id]
            by_participant.append({
                'ParticipantID': participant_id,
                'OrderCount': len(participant_orders),
                'TotalQuantity': participant_orders['quantity'].sum()
            })
    
    by_participant_df = pd.DataFrame(by_participant)
    by_participant_df.to_csv(Path(output_dir) / 'by_participant.csv.gz', compression='gzip', index=False)
    logger.info(f"Saved by_participant.csv.gz with {len(by_participant_df)} participants")
    
    logger.info("All reports generated successfully!")
    
    return summary, detailed, all_order_detail, cost_comparison


if __name__ == '__main__':
    output_dir = 'processed_files'
    Path(output_dir).mkdir(exist_ok=True)
    
    summary, detailed, order_detail, cost = generate_reports(output_dir)
    
    print("\nPhase 4 Complete - Reports Generated:")
    print(f"  - scenario_comparison_summary.csv.gz")
    print(f"  - scenario_detailed_comparison.csv.gz")
    print(f"  - order_level_detail.csv.gz ({len(order_detail):,} orders)")
    print(f"  - execution_cost_comparison.csv.gz")
    print(f"  - by_participant.csv.gz")
