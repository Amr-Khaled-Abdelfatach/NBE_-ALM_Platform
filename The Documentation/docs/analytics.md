# Analytics & Validation

`analytics.py` turns engine output into tables a person can read, a risk rating, and an independent check. It never changes a number; it only recombines.

## KPI comparison

`comparison_table` produces the 15 KPIs compared on the dashboard.

| Source | KPIs | Unit |
|---|---|---|
| Positions | NII, Loans, Deposits, Funding, Investments, Repricing Gap | EGP |
| Positions | NIM %, Cost of Funds %, Loan Deposit % | % |
| Cash flows | Liquidity (net cash flow) | EGP |
| Cash flows | CF LCR % | % |
| P&L | Credit Cost, FX Gold Revaluation, Investment MTM, Total Impact | EGP |

Columns: Before, After, Absolute Change, Change % (change over the absolute base value).

## Risk summary

`build_alm_risk_summary` rates five indicators.

| Indicator | Rule |
|---|---|
| NII, NIM | ADVERSE if lower, POSITIVE if higher |
| Cost of funds | ADVERSE if higher, POSITIVE if lower |
| Repricing gap | Informational |
| LCR proxy | HIGH if below the limit (`LCR_WARNING_THRESHOLD`, default 100), otherwise OK |

## Overall risk rating

`overall_risk_rating` starts at LOW and raises the level when any rule fires. Each rule adds a reason string shown on the dashboard.

| Rule | Threshold (`config.py`) | Result |
|---|---|---|
| LCR proxy below limit | 100 | HIGH |
| NII change ≤ | −10 % | HIGH |
| NII change ≤ | −3 % | MEDIUM |
| Total P&L impact as % of base NII ≤ | −10 % | HIGH |
| Total P&L impact as % of base NII ≤ | −3 % | MEDIUM |
| Cost of funds up by ≥ | 50 bps | MEDIUM |
| LCR relative drop ≤ | −25 % | MEDIUM |

The final rating is the highest level reached. Thresholds are constants and `LCR_WARNING_THRESHOLD` can be set with an environment variable.

!!! warning "LCR rule and the base case"
    Because the LCR proxy is HQLA divided by deposits, it can sit below 100 % even in the base case, which makes the rating HIGH regardless of the scenario. Verify on real data, and consider recalibrating the limit or the definition.

## Management insights

`management_insights` writes four sentences from the numbers (NII with rate and volume effects, NIM and cost of funds in bps, liquidity, total impact). It is template text, not AI, and is the fallback explanation in reports.

## Driver attribution

`driver_attribution` answers *which driver caused how much of the effect*.

1. Run the combined scenario.
2. For each non-zero driver, run it **alone** with every other driver neutral.
3. Report each driver's NII impact and total P&L impact.
4. Report an **Interaction** row.

$$\text{Interaction} = (\text{Combined} - \text{Base}) - \sum_{drivers} (\text{Standalone} - \text{Base})$$

A non-zero interaction means the drivers amplify or offset each other, for example a balance-growth effect multiplied by a rate effect. The last row repeats the combined result so the table adds up. Results are cached for 120 seconds per scenario.

## Other analytics

| Function | Output |
|---|---|
| `shock_sensitivity` | Re-runs the engine over a list of shock values for one driver |
| `position_sensitivity` | Top 10 positions by absolute total impact (NII impact + valuation + MTM − credit cost) |
| `pnl_components` | The four P&L components for the waterfall chart |
| `liquidity_ladder` | Inflow, outflow, net and cumulative net by maturity bucket (0-7, 8-30, 31-90, 91-180, 181-365 days, over 1 year) |
| `exposure_tables` | Balances and interest before and after, grouped by category, currency and product family |
| `build_data_quality_checks` | Row counts and source notes (its tab is hidden in v2.1) |

## Independent validation

`calculation_validation_table` recomputes headline numbers directly from the position-level detail and compares them with the reported KPIs.

| Check | Formula |
|---|---|
| NII (before) | Σ asset interest − Σ liability interest, before |
| NII (after) | The same, after |
| NII change | Equals rate effect + volume effect |
| NIM (after) | NII × 365/H ÷ (loans + investments) × 100 |
| Cost of funds (after) | Interest expense × 365/H ÷ (deposits + funding) × 100 |
| Total impact | ΔNII − credit cost + revaluation + MTM |
| LCR | CF HQLA ÷ deposits × 100 |

Each row shows Expected, Actual, Difference and **PASS, FAIL or SKIP** with tolerances of 0.01 EGP for amounts and 0.0001 for percentages.

!!! note "What validation proves"
    The check confirms that the summary layer agrees with the position layer. It is a consistency test, not an external reconciliation to the general ledger or to a regulatory report. It also cannot catch a conceptual error that is present in both places.

## Row-by-Row table and Excel

`build_full_row_by_row_scenario_table` builds one wide row per position (inputs, scenario parameters, isolated effects). `export_row_by_row_with_formulas_xlsx` writes it with Excel formulas so every effect can be traced. See the caveat in [Reports & Exports](reports.md).
