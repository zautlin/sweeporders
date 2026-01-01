"""
Phase 8: Statistical Comparison of Real vs Simulated Metrics

Performs comprehensive statistical analysis comparing real execution metrics
with simulated metrics across three dark pool scenarios using paired t-tests,
effect sizes, and significance testing.

Tests key hypotheses:
1. Are simulated prices significantly different from actual prices?
2. What is the magnitude of the difference (effect size)?
3. Which scenario shows the most significant improvement?
4. What is the statistical confidence in these findings?
"""

import pandas as pd
import numpy as np
from scipy import stats
from pathlib import Path
import logging
import sys

sys.path.insert(0, str(Path(__file__).parent / 'src'))
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_data(output_dir='processed_files'):
    """Load all necessary data."""
    logger.info("Loading data for statistical analysis...")
    
    real_metrics = pd.read_csv(f'{output_dir}/real_execution_metrics.csv', index_col=0)
    simulated_detailed = pd.read_csv(f'{output_dir}/simulated_metrics_detailed.csv.gz')
    simulated_summary = pd.read_csv(f'{output_dir}/simulated_metrics_summary.csv.gz')
    classified = pd.read_csv(f'{output_dir}/sweep_orders_classified.csv.gz')
    
    logger.info(f"✓ Loaded real metrics, {len(simulated_detailed)} simulated results, "
                f"{len(simulated_summary)} scenarios")
    
    return real_metrics, simulated_detailed, simulated_summary, classified


def calculate_effect_size(actual, simulated):
    """
    Calculate Cohen's d effect size (standardized mean difference).
    
    Cohen's d = (mean_1 - mean_2) / pooled_std
    
    Interpretation:
    - 0.2: Small effect
    - 0.5: Medium effect
    - 0.8: Large effect
    """
    mean_diff = np.mean(actual) - np.mean(simulated)
    pooled_std = np.sqrt((np.std(actual)**2 + np.std(simulated)**2) / 2)
    
    if pooled_std == 0:
        return 0
    
    cohens_d = mean_diff / pooled_std
    return cohens_d


def interpret_effect_size(d):
    """Interpret Cohen's d value."""
    d = abs(d)
    if d < 0.2:
        return "Negligible"
    elif d < 0.5:
        return "Small"
    elif d < 0.8:
        return "Medium"
    else:
        return "Large"


def paired_ttest_analysis(real_prices, simulated_prices, scenario_name):
    """
    Perform paired t-test comparing real vs simulated prices.
    
    Null hypothesis: Mean real price = Mean simulated price
    Alternative: Mean real price ≠ Mean simulated price
    """
    logger.info(f"\n{'=' * 100}")
    logger.info(f"T-TEST ANALYSIS: {scenario_name.upper()}")
    logger.info(f"{'=' * 100}")
    
    # Ensure same length
    assert len(real_prices) == len(simulated_prices), "Length mismatch"
    
    # Calculate differences
    differences = real_prices - simulated_prices
    
    # Paired t-test
    t_stat, p_value = stats.ttest_rel(real_prices, simulated_prices)
    
    # Effect size (Cohen's d)
    cohens_d = calculate_effect_size(real_prices, simulated_prices)
    effect_interpretation = interpret_effect_size(cohens_d)
    
    # Descriptive statistics
    mean_real = np.mean(real_prices)
    mean_sim = np.mean(simulated_prices)
    mean_diff = mean_real - mean_sim
    std_diff = np.std(differences)
    se_diff = std_diff / np.sqrt(len(differences))
    
    # Confidence interval (95%)
    ci_lower = mean_diff - 1.96 * se_diff
    ci_upper = mean_diff + 1.96 * se_diff
    
    # Significance level
    alpha = 0.05
    significant = p_value < alpha
    
    # Results dictionary
    results = {
        'scenario': scenario_name,
        'n': len(real_prices),
        'mean_real': mean_real,
        'mean_simulated': mean_sim,
        'mean_difference': mean_diff,
        'std_difference': std_diff,
        'se_difference': se_diff,
        'ci_lower': ci_lower,
        'ci_upper': ci_upper,
        't_statistic': t_stat,
        'p_value': p_value,
        'significant': significant,
        'cohens_d': cohens_d,
        'effect_size': effect_interpretation
    }
    
    # Display results
    logger.info(f"\nDescriptive Statistics:")
    logger.info(f"  Sample Size: {results['n']} orders")
    logger.info(f"  Mean Real Price: ${results['mean_real']:.2f}")
    logger.info(f"  Mean Simulated Price: ${results['mean_simulated']:.2f}")
    logger.info(f"  Mean Difference: ${results['mean_difference']:.2f}")
    logger.info(f"  Std Dev of Differences: ${results['std_difference']:.2f}")
    logger.info(f"  Std Error of Mean Diff: ${results['se_difference']:.2f}")
    
    logger.info(f"\nPaired T-Test Results:")
    logger.info(f"  Null Hypothesis: Mean Real Price = Mean Simulated Price")
    logger.info(f"  Alternative: Mean Real Price ≠ Mean Simulated Price")
    logger.info(f"  T-Statistic: {results['t_statistic']:.4f}")
    logger.info(f"  P-Value: {results['p_value']:.6f}")
    logger.info(f"  Significance Level (α): 0.05")
    logger.info(f"  Result: {'✅ STATISTICALLY SIGNIFICANT' if significant else '❌ NOT SIGNIFICANT'}")
    
    logger.info(f"\nEffect Size Analysis:")
    logger.info(f"  Cohen's d: {results['cohens_d']:.4f}")
    logger.info(f"  Effect Size: {results['effect_size']}")
    logger.info(f"  Interpretation: {effect_interpretation} effect")
    
    logger.info(f"\n95% Confidence Interval for Mean Difference:")
    logger.info(f"  Lower Bound: ${results['ci_lower']:.2f}")
    logger.info(f"  Upper Bound: ${results['ci_upper']:.2f}")
    logger.info(f"  Range: ${results['ci_lower']:.2f} to ${results['ci_upper']:.2f}")
    
    return results


def perform_all_ttest_analyses(simulated_detailed):
    """Perform t-tests for all three scenarios."""
    logger.info("\n" + "=" * 100)
    logger.info("PAIRED T-TEST COMPARISON: REAL vs SIMULATED PRICES")
    logger.info("=" * 100)
    
    all_results = []
    
    # Extract actual prices (same for all scenarios)
    actual_prices = simulated_detailed[simulated_detailed['scenario'] == 'unlimited']['actual_price'].values
    
    # Test each scenario
    for scenario in ['unlimited', 'limited_50', 'price_impact']:
        scenario_data = simulated_detailed[simulated_detailed['scenario'] == scenario]
        simulated_prices = scenario_data['simulated_price'].values
        
        results = paired_ttest_analysis(actual_prices, simulated_prices, scenario)
        all_results.append(results)
    
    return pd.DataFrame(all_results)


def cost_savings_ttest_analysis(simulated_detailed):
    """
    Perform t-test on cost savings across orders.
    
    Tests whether cost improvements are statistically significantly different from zero.
    """
    logger.info("\n" + "=" * 100)
    logger.info("COST SAVINGS T-TEST: ARE IMPROVEMENTS SIGNIFICANTLY DIFFERENT FROM ZERO?")
    logger.info("=" * 100)
    
    results = []
    
    for scenario in ['unlimited', 'limited_50', 'price_impact']:
        logger.info(f"\n{scenario.upper()}:")
        logger.info("-" * 100)
        
        scenario_data = simulated_detailed[simulated_detailed['scenario'] == scenario]
        savings = scenario_data['cost_difference'].values
        improvement_pct = scenario_data['cost_improvement_pct'].values
        
        # One-sample t-test (null: mean savings = 0)
        t_stat, p_value = stats.ttest_1samp(savings, 0)
        
        mean_savings = np.mean(savings)
        std_savings = np.std(savings)
        se_savings = std_savings / np.sqrt(len(savings))
        ci_lower = mean_savings - 1.96 * se_savings
        ci_upper = mean_savings + 1.96 * se_savings
        
        significant = p_value < 0.05
        
        logger.info(f"  Mean Cost Savings: ${mean_savings:,.2f}")
        logger.info(f"  Std Dev: ${std_savings:,.2f}")
        logger.info(f"  95% CI: [${ci_lower:,.2f}, ${ci_upper:,.2f}]")
        logger.info(f"  T-Statistic: {t_stat:.4f}")
        logger.info(f"  P-Value: {p_value:.6f}")
        logger.info(f"  Result: {'✅ SIGNIFICANT' if significant else '❌ NOT SIGNIFICANT'}")
        logger.info(f"  Interpretation: Savings are {'significantly' if significant else 'NOT significantly'} "
                   f"different from zero (α=0.05)")
        
        results.append({
            'scenario': scenario,
            'mean_savings': mean_savings,
            'std_savings': std_savings,
            'se_savings': se_savings,
            'ci_lower': ci_lower,
            'ci_upper': ci_upper,
            't_statistic': t_stat,
            'p_value': p_value,
            'significant': significant,
            'mean_improvement_pct': np.mean(improvement_pct)
        })
    
    return pd.DataFrame(results)


def anova_scenario_comparison(simulated_detailed):
    """
    Perform one-way ANOVA to test if scenarios are significantly different.
    
    Tests: H0: All scenarios have same mean cost improvement
           H1: At least one scenario differs
    """
    logger.info("\n" + "=" * 100)
    logger.info("ONE-WAY ANOVA: ARE SCENARIOS SIGNIFICANTLY DIFFERENT?")
    logger.info("=" * 100)
    
    # Extract improvement percentages by scenario
    unlimited = simulated_detailed[simulated_detailed['scenario'] == 'unlimited']['cost_improvement_pct'].values
    limited_50 = simulated_detailed[simulated_detailed['scenario'] == 'limited_50']['cost_improvement_pct'].values
    price_impact = simulated_detailed[simulated_detailed['scenario'] == 'price_impact']['cost_improvement_pct'].values
    
    # One-way ANOVA
    f_stat, p_value = stats.f_oneway(unlimited, limited_50, price_impact)
    
    logger.info(f"\nNull Hypothesis: All scenarios have equal mean cost improvement")
    logger.info(f"Alternative: At least one scenario differs")
    logger.info(f"\nResults:")
    logger.info(f"  F-Statistic: {f_stat:.4f}")
    logger.info(f"  P-Value: {p_value:.6f}")
    logger.info(f"  Significance Level (α): 0.05")
    logger.info(f"  Result: {'✅ SIGNIFICANT DIFFERENCE' if p_value < 0.05 else '❌ NO SIGNIFICANT DIFFERENCE'}")
    
    if p_value < 0.05:
        logger.info(f"\n  Interpretation: The three scenarios produce significantly different cost improvements.")
        logger.info(f"  This means: Choosing the right scenario matters - they are NOT equivalent.")
    else:
        logger.info(f"\n  Interpretation: No significant difference between scenarios.")
        logger.info(f"  This means: Scenarios are equivalent in terms of improvement.")
    
    # Post-hoc analysis (if significant, which scenarios differ?)
    if p_value < 0.05:
        logger.info(f"\nPost-Hoc Pairwise Comparisons (Independent t-tests):")
        
        # Unlimited vs Limited 50%
        t1, p1 = stats.ttest_ind(unlimited, limited_50)
        logger.info(f"  Unlimited vs Limited 50%:")
        logger.info(f"    T-Statistic: {t1:.4f}, P-Value: {p1:.6f} "
                   f"({'✅ Different' if p1 < 0.05 else '❌ Same'})")
        
        # Unlimited vs Price Impact
        t2, p2 = stats.ttest_ind(unlimited, price_impact)
        logger.info(f"  Unlimited vs Price Impact:")
        logger.info(f"    T-Statistic: {t2:.4f}, P-Value: {p2:.6f} "
                   f"({'✅ Different' if p2 < 0.05 else '❌ Same'})")
        
        # Limited 50% vs Price Impact
        t3, p3 = stats.ttest_ind(limited_50, price_impact)
        logger.info(f"  Limited 50% vs Price Impact:")
        logger.info(f"    T-Statistic: {t3:.4f}, P-Value: {p3:.6f} "
                   f"({'✅ Different' if p3 < 0.05 else '❌ Same'})")
    
    return {'f_statistic': f_stat, 'p_value': p_value, 'significant': p_value < 0.05}


def create_summary_table(ttest_results, savings_results):
    """Create comprehensive summary table."""
    logger.info("\n" + "=" * 100)
    logger.info("COMPREHENSIVE STATISTICAL SUMMARY TABLE")
    logger.info("=" * 100)
    
    summary = pd.DataFrame({
        'Scenario': ttest_results['scenario'].values,
        'Sample Size': ttest_results['n'].values.astype(int),
        'Mean Real Price': ttest_results['mean_real'].values,
        'Mean Sim Price': ttest_results['mean_simulated'].values,
        'Price Difference': ttest_results['mean_difference'].values,
        'Cohen\'s d': ttest_results['cohens_d'].values,
        'Effect Size': ttest_results['effect_size'].values,
        'T-Statistic': ttest_results['t_statistic'].values,
        'P-Value': ttest_results['p_value'].values,
        'Significant': ttest_results['significant'].values,
        'Mean Savings ($)': savings_results['mean_savings'].values,
        'Mean Improvement (%)': savings_results['mean_improvement_pct'].values
    })
    
    logger.info("\n" + summary.to_string())
    
    return summary


def create_detailed_output(ttest_results, savings_results, anova_results, summary_table, 
                          output_dir='processed_files'):
    """Save all statistical results to CSV files."""
    logger.info("\n" + "=" * 100)
    logger.info("SAVING STATISTICAL ANALYSIS RESULTS")
    logger.info("=" * 100)
    
    # 1. T-test results
    ttest_export = ttest_results[['scenario', 'n', 'mean_real', 'mean_simulated', 
                                   'mean_difference', 'std_difference', 'se_difference',
                                   'ci_lower', 'ci_upper', 't_statistic', 'p_value', 
                                   'significant', 'cohens_d', 'effect_size']]
    ttest_export.to_csv(f'{output_dir}/stats_paired_ttest_results.csv', index=False)
    logger.info(f"✓ Saved stats_paired_ttest_results.csv")
    
    # 2. Savings t-test results
    savings_export = savings_results[['scenario', 'mean_savings', 'std_savings', 
                                      'se_savings', 'ci_lower', 'ci_upper',
                                      't_statistic', 'p_value', 'significant',
                                      'mean_improvement_pct']]
    savings_export.to_csv(f'{output_dir}/stats_savings_ttest_results.csv', index=False)
    logger.info(f"✓ Saved stats_savings_ttest_results.csv")
    
    # 3. ANOVA results
    anova_export = pd.DataFrame([anova_results])
    anova_export.to_csv(f'{output_dir}/stats_anova_results.csv', index=False)
    logger.info(f"✓ Saved stats_anova_results.csv")
    
    # 4. Summary table
    summary_table.to_csv(f'{output_dir}/stats_summary_table.csv', index=False)
    logger.info(f"✓ Saved stats_summary_table.csv")
    
    logger.info("\n✓ All statistical analysis results saved")


def main():
    """Run all statistical analyses."""
    logger.info("Starting Step 8: Statistical Comparison Analysis")
    
    # Load data
    real_metrics, simulated_detailed, simulated_summary, classified = load_data()
    
    # Analysis 1: Paired t-tests for all scenarios
    ttest_results = perform_all_ttest_analyses(simulated_detailed)
    
    # Analysis 2: Cost savings significance tests
    savings_results = cost_savings_ttest_analysis(simulated_detailed)
    
    # Analysis 3: ANOVA comparing scenarios
    anova_results = anova_scenario_comparison(simulated_detailed)
    
    # Summary table
    summary_table = create_summary_table(ttest_results, savings_results)
    
    # Save results
    create_detailed_output(ttest_results, savings_results, anova_results, summary_table)
    
    logger.info("\n" + "=" * 100)
    logger.info("✓ STEP 8: STATISTICAL ANALYSIS COMPLETE")
    logger.info("=" * 100)
    
    return ttest_results, savings_results, anova_results, summary_table


if __name__ == '__main__':
    ttest_results, savings_results, anova_results, summary_table = main()
