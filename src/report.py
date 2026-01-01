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


def generate_reports(output_dir: str):
    """Generate all comparison reports."""
    logger.info("Phase 4: Generating reports...")
    
    # Load simulation results
    sim_a = pd.read_parquet(Path(output_dir) / 'scenario_a_simulation_results.parquet')
    sim_b = pd.read_parquet(Path(output_dir) / 'scenario_b_simulation_results.parquet')
    sim_c = pd.read_parquet(Path(output_dir) / 'scenario_c_simulation_results.parquet')
    
    # Load original scenario data
    scenario_a_orders = pd.read_parquet(Path(output_dir) / 'scenario_a_immediate_full.parquet')
    scenario_b_c_orders = pd.read_parquet(Path(output_dir) / 'scenario_c_partial_none.parquet')
    
    logger.info(f"Loaded {len(sim_a)} Scenario A results")
    logger.info(f"Loaded {len(sim_b)} Scenario B results")
    logger.info(f"Loaded {len(sim_c)} Scenario C results")
    
    # Report 1: Scenario Comparison Summary
    logger.info("Generating scenario comparison summary...")
    summary = pd.DataFrame({
        'Scenario': ['Real Execution (A)', 'Real Execution (B)', 'Real Execution (C)',
                     'Simulated Dark (A)', 'Simulated Dark (B)', 'Simulated Dark (C)'],
        'OrderCount': [
            len(scenario_a_orders), len(scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0]),
            len(scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0]),
            len(sim_a), len(sim_b), len(sim_c)
        ],
        'TotalQuantity': [
            scenario_a_orders['quantity'].sum(),
            scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0]['quantity'].sum(),
            scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0]['quantity'].sum(),
            sim_a['simulated_num_matches'].sum() if len(sim_a) > 0 else 0,
            sim_b['residual_fill_qty'].sum() if len(sim_b) > 0 else 0,
            sim_c['simulated_fill_ratio'].sum() if len(sim_c) > 0 else 0
        ],
        'AvgFillRatio': [
            scenario_a_orders['fill_ratio'].mean(),
            scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0]['fill_ratio'].mean(),
            scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0]['fill_ratio'].mean(),
            sim_a['simulated_fill_ratio'].mean() if len(sim_a) > 0 else 0,
            sim_b['simulated_fill_ratio'].mean() if len(sim_b) > 0 else 0,
            sim_c['simulated_fill_ratio'].mean() if len(sim_c) > 0 else 0
        ]
    })
    summary.to_csv(Path(output_dir) / 'scenario_comparison_summary.csv', index=False)
    logger.info(f"Saved scenario_comparison_summary.csv")
    
    # Report 2: Detailed Comparison
    logger.info("Generating detailed comparison...")
    detailed = pd.DataFrame({
        'Metric': ['Total Orders', 'Avg Fill Ratio', 'Median Fill Ratio', 'Std Dev Fill Ratio',
                   'Orders with Full Fill (>=99%)', 'Avg Execution Price'],
        'Real_Execution': [
            len(scenario_a_orders) + len(scenario_b_c_orders),
            (scenario_a_orders['fill_ratio'].sum() + scenario_b_c_orders['fill_ratio'].sum()) / 
            (len(scenario_a_orders) + len(scenario_b_c_orders)),
            pd.concat([scenario_a_orders, scenario_b_c_orders])['fill_ratio'].median(),
            pd.concat([scenario_a_orders, scenario_b_c_orders])['fill_ratio'].std(),
            len(scenario_a_orders),
            scenario_a_orders['avg_execution_price'].mean()
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
            0  # Not applicable for B
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
    detailed.to_csv(Path(output_dir) / 'scenario_detailed_comparison.csv', index=False)
    logger.info(f"Saved scenario_detailed_comparison.csv")
    
    # Report 3: Order-level detail
    logger.info("Generating order-level detail...")
    
    # Merge simulation results with original orders
    order_detail_a = scenario_a_orders[['order_id', 'security_code', 'side', 'price', 'quantity', 'fill_ratio', 'avg_execution_price']].copy()
    order_detail_a = order_detail_a.merge(sim_a[['order_id', 'simulated_fill_ratio', 'simulated_execution_price']], on='order_id', how='left')
    order_detail_a['Scenario'] = 'A'
    
    partial_orders = scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0].copy()
    order_detail_b = partial_orders[['order_id', 'security_code', 'side', 'price', 'quantity', 'fill_ratio']].copy()
    order_detail_b = order_detail_b.merge(sim_b[['order_id', 'simulated_fill_ratio']], on='order_id', how='left')
    order_detail_b['Scenario'] = 'B'
    
    unfilled_orders = scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0].copy()
    order_detail_c = unfilled_orders[['order_id', 'security_code', 'side', 'price', 'quantity', 'fill_ratio']].copy()
    order_detail_c = order_detail_c.merge(sim_c[['order_id', 'simulated_fill_ratio']], on='order_id', how='left')
    order_detail_c['Scenario'] = 'C'
    
    all_order_detail = pd.concat([order_detail_a, order_detail_b, order_detail_c], ignore_index=True)
    all_order_detail.to_csv(Path(output_dir) / 'order_level_detail.csv', index=False)
    logger.info(f"Saved order_level_detail.csv with {len(all_order_detail):,} orders")
    
    # Report 4: Execution cost comparison
    logger.info("Generating execution cost comparison...")
    
    # Calculate total cost for each scenario
    real_cost_a = (scenario_a_orders['fill_ratio'] * scenario_a_orders['avg_execution_price']).sum()
    real_cost_bc = (scenario_b_c_orders['fill_ratio'] * scenario_b_c_orders['avg_execution_price']).sum()
    
    sim_cost_a = (sim_a['simulated_fill_ratio'] * sim_a['simulated_execution_price']).sum() if len(sim_a) > 0 else 0
    sim_cost_b = (sim_b['simulated_fill_ratio'] * sim_b['simulated_fill_ratio']).sum() if len(sim_b) > 0 else 0
    sim_cost_c = (sim_c['simulated_fill_ratio'] * sim_c['simulated_execution_price']).sum() if len(sim_c) > 0 else 0
    
    cost_comparison = pd.DataFrame({
        'Scenario': ['Real Execution', 'Simulated A', 'Simulated B', 'Simulated C'],
        'TotalExecutionCost': [real_cost_a + real_cost_bc, sim_cost_a, sim_cost_b, sim_cost_c],
        'AvgCostPerShare': [
            (real_cost_a + real_cost_bc) / (scenario_a_orders['quantity'].sum() + scenario_b_c_orders['quantity'].sum()),
            sim_cost_a / scenario_a_orders['quantity'].sum() if len(sim_a) > 0 else 0,
            sim_cost_b / scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] > 0]['quantity'].sum() if len(sim_b) > 0 else 0,
            sim_cost_c / scenario_b_c_orders[scenario_b_c_orders['total_quantity_filled'] == 0]['quantity'].sum() if len(sim_c) > 0 else 0
        ]
    })
    cost_comparison.to_csv(Path(output_dir) / 'execution_cost_comparison.csv', index=False)
    logger.info(f"Saved execution_cost_comparison.csv")
    
    # Report 5: By participant (if available)
    logger.info("Generating by-participant summary...")
    all_orders = pd.read_parquet(Path(output_dir) / 'centrepoint_orders_raw.parquet')
    
    by_participant = []
    for participant_id in all_orders['participantid'].unique():
        participant_orders = all_orders[all_orders['participantid'] == participant_id]
        real_avg_fill = participant_orders['quantity'].sum()
        by_participant.append({
            'ParticipantID': participant_id,
            'OrderCount': len(participant_orders),
            'TotalQuantity': participant_orders['quantity'].sum()
        })
    
    by_participant_df = pd.DataFrame(by_participant)
    by_participant_df.to_csv(Path(output_dir) / 'by_participant.csv', index=False)
    logger.info(f"Saved by_participant.csv with {len(by_participant_df)} participants")
    
    logger.info("All reports generated successfully!")
    
    return summary, detailed, all_order_detail, cost_comparison


if __name__ == '__main__':
    output_dir = 'processed_files'
    Path(output_dir).mkdir(exist_ok=True)
    
    summary, detailed, order_detail, cost = generate_reports(output_dir)
    
    print("\nPhase 4 Complete - Reports Generated:")
    print(f"  - scenario_comparison_summary.csv")
    print(f"  - scenario_detailed_comparison.csv")
    print(f"  - order_level_detail.csv ({len(order_detail):,} orders)")
    print(f"  - execution_cost_comparison.csv")
    print(f"  - by_participant.csv")
