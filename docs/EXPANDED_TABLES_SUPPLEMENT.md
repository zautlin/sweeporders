# Expanded Cross-Security Comparison Tables
## Real (Lit Market) | Simulated (Dark Pool) | Difference

**Document Purpose:** This supplement provides expanded tables showing Real, Simulated, and Difference values for all metrics across the four securities (BHP, CBA, DRR, WTC).

**Reading Guide:**
- **Real (Lit Market):** Actual lit market execution performance
- **Simulated (Dark Pool):** Midpoint dark pool execution (simulated)
- **Difference:** Real - Simulated (negative = lit market worse, positive = lit market better)
- **Cohen's d:** Effect size (|d| < 0.2 = negligible, 0.2-0.5 = small, 0.5-0.8 = medium, >0.8 = large)

---

## Group 1: Cost Metrics (Primary Performance Indicators)

### Execution Cost (Arrival) - basis points

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d | Effect Size |
|----------|-------------------|-----------------------|------------|-----------|-------------|
| **Portfolio** | -1.14 | +0.22 | **-1.36** | -0.26 | Small |
| **BHP** | -0.95 | +0.31 | **-1.26** | -0.37 | Small |
| **CBA** | -0.48 | +0.12 | **-0.61** | -0.17 | Negligible |
| **DRR** | **-13.98** | +0.32 | **-14.30** | **-0.78** | **Medium** |
| **WTC** | -0.78 | +0.23 | **-1.01** | -0.27 | Small |

**Interpretation:**
- Negative values = costs MORE (worse performance)
- All lit market values are negative (lit market consistently costs more)
- All dark pool values are positive (dark pool consistently saves money)
- DRR shows catastrophic lit market performance (-13.98 bps)
- Difference ranges from -0.61 bps (CBA, acceptable) to -14.30 bps (DRR, critical)

### Execution Cost (Volume-Weighted) - basis points

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d | Effect Size |
|----------|-------------------|-----------------------|------------|-----------|-------------|
| **Portfolio** | -1.24 | +0.22 | **-1.45** | -0.45 | Small |
| **BHP** | -1.04 | +0.31 | **-1.35** | **-0.99** | **Large** |
| **CBA** | -0.58 | +0.12 | **-0.70** | -0.35 | Small |
| **DRR** | **-12.03** | +0.32 | **-12.34** | **-1.14** | **Very Large** |
| **WTC** | -1.13 | +0.23 | **-1.36** | **-0.93** | **Large** |

**Interpretation:**
- Volume-weighting makes lit market performance WORSE for all securities except DRR
- Effect sizes jump to "large" and "very large" for BHP, DRR, WTC
- Indicates large orders suffer disproportionate adverse selection in lit market
- BHP: 7% VW penalty (-1.35 vs -1.26 bps)
- WTC: 35% VW penalty (-1.36 vs -1.01 bps) - WORST size dependency
- DRR: 14% VW improvement (-12.34 vs -14.30 bps) - unusual, still catastrophic

### Slippage (Arrival) - basis points

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d |
|----------|-------------------|-----------------------|------------|-----------|
| **Portfolio** | -1.14 | +0.22 | **-1.36** | -0.26 |
| **BHP** | -0.95 | +0.31 | **-1.26** | -0.37 |
| **CBA** | -0.48 | +0.12 | **-0.61** | -0.17 |
| **DRR** | **-13.98** | +0.32 | **-14.30** | **-0.78** |
| **WTC** | -0.78 | +0.23 | **-1.01** | -0.27 |

**Interpretation:**
- Slippage mirrors execution cost (arrival) exactly
- Both measure cost relative to arrival midpoint
- Confirms lit market consistently "slips" away from favorable prices

### Implementation Shortfall - basis points

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d |
|----------|-------------------|-----------------------|------------|-----------|
| **Portfolio** | -0.36 | +0.11 | **-0.47** | -0.09 |
| **BHP** | -0.60 | +0.20 | **-0.80** | -0.23 |
| **CBA** | **+0.11** | +0.04 | **+0.06** | +0.02 |
| **DRR** | **-4.97** | +0.12 | **-5.09** | -0.23 |
| **WTC** | -0.19 | +0.07 | **-0.27** | -0.07 |

**Interpretation:**
- Implementation shortfall = total cost including market drift
- CBA shows POSITIVE lit market value (+0.11) - only security where lit is competitive
- But dark pool still better (+0.06 bps advantage)
- DRR's -4.97 bps lit market cost shows problem isn't just drift - it's adverse selection
- Lower magnitude than execution cost suggests some favorable drift offset adverse selection

### Price Improvement - basis points

| Security | Real (Lit Market) | Simulated (Dark Pool) | Dark Pool Improvement | Cohen's d |
|----------|-------------------|-----------------------|-----------------------|-----------|
| **Portfolio** | -1.61 | -2.97 | **+1.36** | +0.26 |
| **BHP** | -1.38 | -2.64 | **+1.26** | +0.37 |
| **CBA** | -1.12 | -1.72 | **+0.60** | +0.17 |
| **DRR** | -8.99 | -23.30 | **+14.31** | **+0.78** |
| **WTC** | -1.72 | -2.73 | **+1.01** | +0.27 |

**Interpretation:**
- Negative values = cost relative to benchmark
- Dark pool ALWAYS shows more negative value (further from benchmark)
- BUT difference is POSITIVE (dark pool improvement)
- This is because dark pool reference is more aggressive benchmark (midpoint execution)
- Price improvement = how much dark pool SAVES vs lit market
- DRR: +14.31 bps improvement = dark pool saves $40.36 per order on average

**Annual Value:**
- Portfolio: 1.36 bps × $2.5B notional = $3.4M/year
- DRR: 14.31 bps × $80M notional = $1.14M/year

---

## Group 2: Time Metrics (Execution Speed & Market Exposure)

### Execution Time - seconds

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d | Effect Size |
|----------|-------------------|-----------------------|------------|-----------|-------------|
| **Portfolio** | 2.89 sec | 1.45 sec | **+1.44 sec** | 0.02 | Negligible |
| **BHP** | 2.32 sec | 1.16 sec | **+1.16 sec** | 0.04 | Negligible |
| **CBA** | 1.34 sec | 1.25 sec | **+0.08 sec** | 0.00 | Negligible |
| **DRR** | **21.41 sec** | 8.32 sec | **+13.09 sec** | 0.03 | Negligible |
| **WTC** | 3.58 sec | 1.27 sec | **+2.31 sec** | 0.06 | Negligible |

**Interpretation:**
- Positive difference = lit market is SLOWER
- ALL securities show lit market is slower (range: 0.08 to 13.09 seconds)
- DRR: 21.41 sec lit market execution (2.6x slower than dark pool's 8.32 sec)
- Despite large absolute differences, ALL effect sizes negligible (high variance)
- Paradox: Lit market consistently slower but doesn't matter statistically

**Why negligible?**
- High variance in execution times (some fast, some slow)
- Standard deviation >> mean difference
- Result: Cohen's d < 0.1 for all securities

### Time to First Fill - seconds

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d | Effect Size |
|----------|-------------------|-----------------------|------------|-----------|-------------|
| **Portfolio** | 75.37 sec | 4.21 sec | **+71.16 sec** | 0.23 | Small |
| **BHP** | 75.25 sec | 4.92 sec | **+70.33 sec** | 0.26 | Small |
| **CBA** | 44.55 sec | 2.52 sec | **+42.03 sec** | 0.14 | Negligible |
| **DRR** | **578.64 sec (9.6 min)** | 27.03 sec | **+551.61 sec (9.2 min)** | 0.58 | Medium |
| **WTC** | 59.38 sec | 3.04 sec | **+56.34 sec** | 0.40 | Small |

**Interpretation:**
- Time to first fill = latency until order starts executing
- Lit market has MUCH longer latency (42 to 552 seconds longer)
- DRR: Lit market takes 578.64 sec (9.6 minutes) to start vs 27.03 sec in dark pool
- Effect sizes slightly larger than execution time (small to medium)
- BUT: Still don't drive cost differences (market drift near zero)

**The DRR Paradox:**
- 9-minute slower start time
- Effect size: medium (d=0.58)
- Market drift during extra time: only -0.12 bps (negligible)
- Cost difference: -14.30 bps (mostly adverse selection, not drift)
- **Conclusion:** Waiting 9 minutes for dark pool fill is worth it (saves $40/order)

---

## Group 3: Quantity Metrics (Fill Quality & Completion)

### Quantity Filled - units

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d | Effect Size |
|----------|-------------------|-----------------------|------------|-----------|-------------|
| **Portfolio** | 96.25 | 96.36 | **-0.11** | -0.00 | Negligible |
| **BHP** | 194.63 | 187.82 | **+6.80** | 0.03 | Negligible |
| **CBA** | 24.31 | 23.47 | **+0.83** | 0.03 | Negligible |
| **DRR** | 639.47 | 728.82 | **-89.35** | -0.12 | Negligible |
| **WTC** | 13.07 | 12.39 | **+0.68** | 0.07 | Negligible |

**Interpretation:**
- Average order size ranges from 13 units (WTC) to 639 units (DRR)
- Quantity differences are tiny relative to order size (-0.11 to +6.80 units)
- DRR exception: Lit fills 89 FEWER units on average (639 vs 729)
- BUT: Effect size still negligible (std = 721.79 units, massive variance)
- **Conclusion:** Venue doesn't significantly affect fill quantity

### Fill Ratio

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d |
|----------|-------------------|-----------------------|------------|-----------|
| **Portfolio** | 1.14 | 0.99 | **+0.15** | 0.02 |
| **BHP** | 1.19 | 0.99 | **+0.20** | 0.02 |
| **CBA** | 1.07 | 0.99 | **+0.08** | 0.10 |
| **DRR** | **2.26** | 1.00 | **+1.27** | 0.04 |
| **WTC** | 1.05 | 0.99 | **+0.06** | 0.13 |

**Interpretation:**
- Fill ratio > 1.0 suggests orders filling more than target quantity (partial fill + amendment?)
- Dark pool consistently shows 0.99-1.00 (fills target quantity exactly)
- Lit market shows 1.05-2.26 (fills more than target)
- DRR: 2.26 fill ratio (fills 126% more?) - DATA QUALITY ISSUE
- Effect sizes negligible despite large DRR difference
- **Recommendation:** Investigate fill ratio calculation methodology

### Number of Fills

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d | Interpretation |
|----------|-------------------|-----------------------|------------|-----------|----------------|
| **Portfolio** | 1.29 | 1.69 | **-0.40** | -0.23 | Dark has MORE fills |
| **BHP** | 1.31 | 1.66 | **-0.35** | -0.18 | Dark has MORE fills |
| **CBA** | 1.23 | 1.63 | **-0.40** | -0.25 | Dark has MORE fills |
| **DRR** | 1.26 | 1.84 | **-0.58** | -0.22 | Dark has MORE fills |
| **WTC** | 1.35 | 1.80 | **-0.46** | -0.31 | Dark has MORE fills |

**Interpretation:**
- Negative difference = lit market uses FEWER fills (less fragmented)
- Dark pool: 1.63-1.84 fills per order (30-46% more fragmented)
- Lit market: 1.23-1.35 fills per order (more consolidated)
- Traditional view: More fills = worse (fragmentation penalty)
- **Reality:** Dark pool has more fills BUT better prices (-1.36 bps savings)
- **Conclusion:** Fragmentation at CONSISTENT PRICES (midpoint) > consolidation at ESCALATING PRICES (adverse selection)

### Average Fill Size - units

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d | Interpretation |
|----------|-------------------|-----------------------|------------|-----------|----------------|
| **Portfolio** | 76.02 | 55.53 | **+20.49** | 0.10 | Lit has LARGER fills |
| **BHP** | 152.49 | 114.71 | **+37.78** | 0.21 | Lit has LARGER fills |
| **CBA** | 18.41 | 13.30 | **+5.11** | 0.17 | Lit has LARGER fills |
| **DRR** | **527.96** | 356.72 | **+171.24** | 0.17 | Lit has LARGER fills |
| **WTC** | 10.22 | 7.03 | **+3.19** | 0.30 | Lit has LARGER fills |

**Interpretation:**
- Lit market: Larger fills (76-528 units per fill)
- Dark pool: Smaller fills (56-357 units per fill)
- Confirms: Lit market less fragmented (fewer, larger fills)
- DRR: Lit market fills average 528 units vs 357 units in dark pool
- Effect sizes negligible to small (0.10-0.30)
- **Key insight:** Larger fills ≠ better execution (lit market still loses on cost)

---

## Group 4: Price Metrics (Execution Pricing & Benchmarks)

### VWAP - absolute price units

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Cohen's d | Winner |
|----------|-------------------|-----------------------|------------|-----------|--------|
| **Portfolio** | 98,566.27 | 98,567.89 | **-1.62** | -0.04 | Dark (better VWAP) |
| **BHP** | 38,948.58 | 38,951.71 | **-3.13** | -0.23 | Dark (better VWAP) |
| **CBA** | 141,065.25 | 141,064.36 | **+0.90** | 0.02 | **Lit (better VWAP)** |
| **DRR** | 3,391.31 | 3,393.05 | **-1.74** | -0.23 | Dark (better VWAP) |
| **WTC** | 122,850.04 | 122,853.34 | **-3.30** | -0.07 | Dark (better VWAP) |

**Interpretation:**
- VWAP in absolute price units (not normalized)
- Negative difference = lit market achieves WORSE VWAP (higher for buys, lower for sells)
- CBA is ONLY security where lit market achieves better VWAP (+0.90 price units)
- But CBA still loses on execution cost (-0.61 bps) due to spread crossing
- DRR: Lit market VWAP is 1.74 price units worse (on $3,391 avg = 5 bps)
- Effect sizes negligible (VWAP measured in absolute price, high variance)

**CBA Paradox:**
- Lit market: Better VWAP (lower execution prices)
- BUT: Still higher execution cost (-0.61 bps)
- **Why?** VWAP doesn't account for spread crossing cost
- Lit market pays spread + gets good fills
- Dark pool pays no spread (midpoint) + gets fair fills
- **Net:** Dark pool still wins

### Arrival Midpoint - absolute price units

| Security | Real (Lit Market) | Simulated (Dark Pool) | Difference | Interpretation |
|----------|-------------------|-----------------------|------------|----------------|
| **Portfolio** | 98,567.15 | 98,567.15 | **0.00** | Identical arrival conditions |
| **BHP** | 38,950.92 | 38,950.92 | **0.00** | Identical arrival conditions |
| **CBA** | 141,063.73 | 141,063.73 | **0.00** | Identical arrival conditions |
| **DRR** | 3,393.01 | 3,393.01 | **0.00** | Identical arrival conditions |
| **WTC** | 122,852.42 | 122,852.42 | **0.00** | Identical arrival conditions |

**Interpretation:**
- PERFECT equality = both strategies use identical arrival snapshot
- No look-ahead bias
- No timing arbitrage
- Fair comparison guaranteed
- **Validates methodology:** Cost differences are venue-specific, not timing-specific

### Price Improvement - basis points (from Group 1)

| Security | Real (Lit Market) | Simulated (Dark Pool) | Dark Pool Improvement |
|----------|-------------------|-----------------------|-----------------------|
| **Portfolio** | -1.61 | -2.97 | **+1.36 bps** |
| **BHP** | -1.38 | -2.64 | **+1.26 bps** |
| **CBA** | -1.12 | -1.72 | **+0.60 bps** |
| **DRR** | -8.99 | -23.30 | **+14.31 bps** |
| **WTC** | -1.72 | -2.73 | **+1.01 bps** |

**Interpretation:**
- Price improvement = cost saved by dark pool vs lit market
- Directly quantifies dark pool value
- Range: 0.60 bps (CBA) to 14.31 bps (DRR)
- **Annual value:** 1.36 bps × $2.5B = $3.4M portfolio-wide

---

## Summary: Key Insights from Expanded Tables

### 1. Lit Market Consistently Costs More (Cost Metrics)

**Evidence:**
- ALL securities show negative lit market execution costs
- ALL securities show positive dark pool savings
- Difference ranges from -0.61 bps (acceptable) to -14.30 bps (catastrophic)

**Magnitude by Security:**
| Security | Lit Cost | Dark Savings | Difference | Annual Impact |
|----------|----------|--------------|------------|---------------|
| DRR | -13.98 bps | +0.32 bps | -14.30 bps | $1.14M |
| BHP | -1.04 bps | +0.31 bps | -1.35 bps (VW) | $1.10M |
| WTC | -1.13 bps | +0.23 bps | -1.36 bps (VW) | $908K |
| CBA | -0.58 bps | +0.12 bps | -0.70 bps (VW) | $658K |

### 2. Volume-Weighting Amplifies Size Problems

**Evidence:**
- VW costs WORSE than arrival costs for all securities except DRR
- Effect sizes jump from small to large/very large
- Large orders experience disproportionate adverse selection

**VW Amplification:**
| Security | Arrival Cost | VW Cost | Amplification | Size Dependency |
|----------|--------------|---------|---------------|-----------------|
| WTC | -1.01 bps | -1.36 bps | **+35%** | Worst |
| BHP | -1.26 bps | -1.35 bps | +7% | Moderate |
| CBA | -0.61 bps | -0.70 bps | +15% | Low |
| DRR | -14.30 bps | -12.34 bps | -14% (improvement) | Still catastrophic |

### 3. Time Differences Don't Drive Costs

**Evidence:**
- Execution time: Lit market 1-13 sec slower
- Market drift: Near zero (-0.01 bps)
- Time-to-fill: Lit market 42-552 sec slower to start
- Cost differences: -0.61 to -14.30 bps (driven by adverse selection, not drift)

**Conclusion:** Extra 1-9 minutes in lit market doesn't cause market movement. Cost differences are from adverse selection (being picked off), not timing.

### 4. Fill Fragmentation Doesn't Hurt Performance

**Evidence:**
- Dark pool: 1.63-1.84 fills per order (MORE fragmented)
- Lit market: 1.23-1.35 fills per order (LESS fragmented)
- Dark pool: Better prices (-1.36 bps savings)

**Insight:** Multiple fills at CONSISTENT PRICES (dark pool midpoint) > fewer fills at ESCALATING PRICES (lit market adverse selection).

### 5. Arrival Conditions Identical = Fair Comparison

**Evidence:**
- Arrival midpoint: 0.00 difference for ALL securities
- Arrival spread: 0.00 bps difference for ALL securities

**Validation:** Both strategies face identical market conditions at order arrival. Cost differences reflect venue characteristics, not timing arbitrage.

### 6. Only Cost Metrics Matter

**Statistical Significance by Metric Group:**
| Metric Group | Effect Size Range | Economically Significant | Routing Relevance |
|--------------|-------------------|--------------------------|-------------------|
| Cost | d = -0.17 to -1.14 (negligible to very large) | ✓ YES ($3.4M/year) | **CRITICAL** |
| Time | d = 0.00 to 0.58 (negligible to medium) | ✗ NO (drift near zero) | Ignore |
| Quantity | d = -0.00 to 0.30 (negligible to small) | ✗ NO (no impact on cost) | Ignore |
| Price | d = -0.04 to -0.23 (negligible to small) | ✓ YES (mirrors cost) | Use cost metric instead |
| Spread | d = 0.00 (no variance) | ✓ YES (validates methodology) | Context only |
| Drift | d ≈ 0.00 (negligible) | ✗ NO (<1% of cost) | Ignore |

**Recommendation:** Route based EXCLUSIVELY on cost metrics. All other factors are noise or irrelevant.

---

## Routing Decision Matrix (Based on Expanded Tables)

### Security-Specific Thresholds

| Security | Lit Cost (VW) | Dark Savings | Recommended Routing | Rationale |
|----------|---------------|--------------|---------------------|-----------|
| **DRR** | -12.03 bps | +0.32 bps | **100% dark, all sizes** | Catastrophic lit performance |
| **BHP** | -1.04 bps | +0.31 bps | **Dark for Q3-Q4 (>600 units)** | Large effect size (d=-0.99) |
| **WTC** | -1.13 bps | +0.23 bps | **Dark for Q3-Q4 (>100 units)** | Worst VW amplification (35%) |
| **CBA** | -0.58 bps | +0.12 bps | **Dark preferred, lit acceptable** | Small effect size (d=-0.35) |

### Size-Based Thresholds (All Securities)

| Order Size Quartile | Lit Cost | Dark Savings | Recommended Routing |
|---------------------|----------|--------------|---------------------|
| Q1 (Small) | -0.75 bps | Minimal | Lit acceptable |
| Q2 (Medium-Small) | -1.00 bps | Small | Dark preferred |
| Q3 (Medium-Large) | -1.45 bps | Moderate | **Dark mandatory** (except CBA) |
| Q4 (Large) | -2.10 bps | Large | **Dark mandatory** (all securities) |

### Implementation Priority

**Phase 1 (Month 1):** High-impact, low-effort
1. DRR: Route 100% dark → Save $1.14M/year
2. BHP/WTC Q4: Route large orders dark → Save $700K/year
3. **Total Phase 1 savings:** $1.84M/year (54% of excess cost)

**Phase 2 (Months 2-3):** Medium-impact, medium-effort
4. Q3 orders: Route to dark for all except small CBA → Save $300K/year
5. Dynamic spread routing: If spread > 5 bps, route dark → Save $200K/year
6. **Total Phase 1+2 savings:** $2.34M/year (69% of excess cost)

**Phase 3 (Months 4-6):** Lower-impact, higher-effort
7. Order slicing: Break very large orders into Q2-Q3 pieces → Save $150K/year
8. Optimize CBA routing: Balance lit speed with dark cost savings → Save $150K/year
9. **Total savings potential:** $2.64M/year (78% of excess cost)

---

**Document End**

*This expanded table supplement provides the detailed Real/Simulated/Difference breakdowns for all metrics in the cross-security analysis.*

*For full interpretation, context, and recommendations, see: CROSS_SECURITY_METRIC_ANALYSIS.md*
