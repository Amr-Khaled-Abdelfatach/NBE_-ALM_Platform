"""Documentation page: how the engine works, what every driver / KPI means, horizon, assumptions, brief coverage."""
import pandas as pd
import streamlit as st

from config import *
from calculations import apply_prepared, effective_drivers
from ui_common import show_df, format_large_number, doc_button

DRIVER_DOCS = {
    "rate_change_pp": ("Market", "Parallel shift of the policy / market rate, in percentage points (+3 = +300 bps). Applied at each floating position's next repricing date, and at maturity for fixed-rate positions that roll over inside the horizon. Scaled by the pass-through of the position's category. A customer rate never goes below 0."),
    "funding_cost_shock_bps": ("Market", "Extra cost on deposits and wholesale funding, in bps, on top of the policy-rate pass-through (e.g. wider funding spreads). Applied at the same repricing dates."),
    "fx_shock_pct": ("Market", "Change of the EGP value of foreign currency (USD, EUR...). Revalues every FX position's balance, FX cash flows and the net open FX position (a P&L revaluation: FX assets - FX liabilities)."),
    "gold_shock_pct": ("Market", "Change of the gold price. Revalues gold positions (grams x price), gold cash flows and the net gold position (P&L revaluation)."),
    "loan_pass_through_pct": ("Pass-through", "Share of the market-rate move that reaches loan rates (100 = full)."),
    "deposit_pass_through_pct": ("Pass-through", "Share of the market-rate move that reaches deposit rates."),
    "funding_pass_through_pct": ("Pass-through", "Share of the market-rate move that reaches wholesale-funding cost (Treasury-segment deposits are treated as funding)."),
    "investment_pass_through_pct": ("Pass-through", "Share of the market-rate move that reaches investment yields; also scales the yield shock used for the bond-price (MTM) estimate."),
    "loan_growth_pct": ("Balance sheet", "Change of the loan balances over the horizon (volume effect on interest income and credit cost)."),
    "deposit_growth_pct": ("Balance sheet", "Change of the deposit balances (volume effect on interest expense)."),
    "deposit_runoff_pct": ("Liquidity", "Share of deposits that leaves. Reduces deposit balances (and their interest cost) and adds the same amount to cash outflows, which lowers the LCR proxy."),
    "liquidity_shift_pct": ("Liquidity", "Generic stress on cash flows: inflows x (1+x), outflows x (1-x) and HQLA x (1+x). Use a negative value to stress."),
    "hqla_haircut_pct": ("Liquidity", "Reduced market liquidity: the HQLA stock is cut by this percentage in the LCR proxy."),
    "inflation_shock_pct": ("Macro", "Change of inflation (pp). Enters the calculation by moving the policy rate: effective rate shock = direct shock + coefficient x inflation shock. Also lowers / raises the displayed real policy rate."),
    "unemployment_shock_pct": ("Macro", "Increase of unemployment (pp). Adds deposit run-off (households use savings) and credit cost on the loan book. Only increases are adverse."),
    "gdp_shock_pct": ("Macro", "Change of GDP growth (pp). Moves loan growth and, when negative, adds credit cost."),
    "infl_to_rate": ("Macro transmission", "Policy-rate response to inflation, pp of rate per pp of inflation (illustrative default 0.5)."),
    "unemp_to_runoff": ("Macro transmission", "Extra deposit run-off (pp) per pp of unemployment increase."),
    "unemp_to_credit_bps": ("Macro transmission", "Credit cost, bps of the loan book per year, per pp of unemployment increase."),
    "gdp_to_credit_bps": ("Macro transmission", "Credit cost, bps of the loan book per year, per pp of GDP contraction."),
    "gdp_to_loan_growth": ("Macro transmission", "Loan growth (pp) per pp of GDP growth change."),
}

KPI_DOCS = [
    ["Position NII", "Sum of asset interest (loans + investments) minus liability interest (deposits + funding) over the horizon, EGP.", "positions (via the flat table), market_data rates"],
    ["Position NIM %", "NII x 365 / horizon / (loans + investments) x 100  (annualised).", "positions"],
    ["Cost of funds %", "Interest expense x 365 / horizon / (deposits + funding) x 100  (annualised).", "positions"],
    ["Repricing gap", "Assets that reprice inside the horizon minus liabilities that reprice inside the horizon, EGP. Negative = liability-sensitive.", "positions, repricing dates / frequency"],
    ["Net cash flow (Liquidity)", "Cash inflows minus outflows dated inside the horizon, in EGP (FX / gold flows revalued by the scenario).", "cash_flows"],
    ["LCR proxy %", f"HQLA stock / (outflow - min(inflow, {LCR_INFLOW_CAP:.0%} x outflow)) x 100 over the first {LCR_WINDOW_DAYS} days. A proxy, not the regulatory LCR.", "positions (HQLA flag), cash_flows"],
    ["FX / gold revaluation (P&L)", "Net open position x shock: (FX or gold assets - FX or gold liabilities) x shock.", "positions, market_data"],
    ["Investment MTM", "- duration x yield shock x balance for investments (duration approximated by the time to the next repricing / maturity). Gold excluded.", "positions"],
    ["Credit cost (macro)", "After-scenario loans x credit-cost rate x horizon / 365, where the rate comes from unemployment and GDP.", "positions, macro coefficients"],
    ["Total P&L & value impact", "Change in NII - credit cost + FX / gold revaluation + investment MTM. A pre-tax proxy: no fees, operating costs or capital.", "all of the above"],
    ["Overall risk", "LOW / MEDIUM / HIGH from thresholds on NII, total impact, cost of funds and LCR (editable in config.py).", "all KPIs"],
]

COVERAGE = [
    ["Interest rate", "Policy / market rate increase or decrease", "✅", "Parallel shift with pass-through and repricing dates."],
    ["Interest rate", "Yield-curve shifts", "❌", "market_data has no yield curve (only the policy rate). Add tenor points to support steepening / flattening."],
    ["Interest rate", "Deposit and lending rate changes, repricing assumptions", "✅", "Pass-through per category + each position's repricing schedule."],
    ["Interest rate", "Funding-cost changes", "✅", "Funding-cost shock (bps) on deposits and wholesale funding."],
    ["FX", "USD/EGP and other FX movements", "✅", "Revalues balances, cash flows and the net open position (P&L)."],
    ["Gold & commodities", "Gold price movements", "✅", "Gold positions, cash flows and net gold position. Other commodities are not in the data."],
    ["Gold & commodities", "Collateral values", "🟡", "Gold-backed loan balances are revalued; there is no separate collateral table."],
    ["Liquidity & funding", "Deposit outflows", "✅", "Deposit run-off (also driven by unemployment)."],
    ["Liquidity & funding", "Corporate deposit withdrawals", "🟡", "Run-off applies to all deposits; segment-specific run-off is a possible extension."],
    ["Liquidity & funding", "Reduced market liquidity", "✅", "HQLA haircut."],
    ["Liquidity & funding", "Wholesale funding stress, FX funding stress", "🟡", "Through funding-cost shock, FX shock and run-off (no separate funding-availability limit)."],
    ["Liquidity & funding", "Maturity concentration", "✅", "Maturity ladder by bucket."],
    ["Market & investment", "Bond prices", "🟡", "Duration-based MTM from the rate shock."],
    ["Market & investment", "Equity prices, credit spreads, volatility", "❌", "No equity positions, spread curves or volatility surfaces in the database."],
    ["Macro", "Inflation, unemployment, GDP, policy rate, exchange rate", "✅", "All five enter the calculation (macro through documented transmission coefficients)."],
    ["Scenarios", "Single and combined scenarios, interactions", "✅", "Driver attribution shows each driver alone and the interaction term."],
    ["Outputs", "NII, NIM, cost of funds, liquidity, LCR, P&L", "✅", "P&L is a pre-tax proxy."],
    ["Outputs", "Capital", "❌", "No capital / RWA data in the database."],
    ["Outputs", "Overall risk position", "✅", "LOW / MEDIUM / HIGH rating with reasons."],
    ["Dashboard", "Scenario → impact → key indicators → management insight", "✅", "One Dashboard page."],
    ["AI", "Natural-language explanation, scenario comparison", "✅", "Phi-3 explains the deterministic results; comparison tab for saved scenarios."],
    ["AI", "Detect unusual movements, rank high-impact scenarios", "🟡", "Driver ranking exists; automatic anomaly detection does not."],
]


def page_documentation(ctx):
    st.markdown('<div class="section-header">📘 Documentation</div>', unsafe_allow_html=True)
    doc_button("")
    tabs = st.tabs(["Overview", "Drivers", "KPIs & formulas", "Horizon days", "Macro transmission",
                    "Data & assumptions", "Brief coverage"])

    with tabs[0]:
        st.markdown(f"""
**{APP_NAME}** answers *"what could happen, and what should we consider doing about it?"* for the bank's ALM position.
It turns the bank's own positions, cash flows and market data into a what-if engine.

**How to use it in three steps**
1. **Scenarios** page → choose a template (or the brief's example), set the *simulation date* and *horizon*, set the drivers, press **Run & save**.
2. **Dashboard** page → read the KPI cards, the management insight and the risk rating, then explore the charts (earnings, balance sheet, liquidity, drivers, market & macro).
3. Ask the AI to **explain** the scenario in management language, or compare saved scenarios.

**How it works**
```
MySQL tables (products, positions, cash_flows, market_data)
        └─ flat tables (vw_alm_flat_table / vw_alm_cashflows_flat, or the Python twin)  → EGP-valued positions and cash flows
                └─ deterministic Python engine (event-based repricing, FX/gold revaluation, macro transmission)
                        └─ KPIs, P&L components, risk rating, sensitivities  → Dashboard
                                └─ saved to scenarios / simulation_runs / simulation_results
```
Python produces every number. The local AI model (Phi-3 via Ollama) only **explains** them.
""")

    with tabs[1]:
        st.markdown("Every driver, its unit, and what it does inside the calculation.")
        rows = [[PARAM_SPECS[k][0], DRIVER_DOCS[k][0], PARAM_SPECS[k][1], f"{PARAM_SPECS[k][2]:g} to {PARAM_SPECS[k][3]:g}", f"{PARAM_SPECS[k][4]:g}", DRIVER_DOCS[k][1]]
                for k in PARAM_SPECS]
        show_df(pd.DataFrame(rows, columns=["Driver", "Group", "Unit", "Range", "Default", "What it does"]), height=640)
        st.caption("pp = percentage points (3 pp = 300 bps).  Individual drivers or any combination can be used; the Dashboard shows each driver's own impact and the interaction between them.")

    with tabs[2]:
        show_df(pd.DataFrame(KPI_DOCS, columns=["KPI", "Definition", "Data used"]))
        st.markdown("""
**Interest per position** = balance (EGP) × rate × accrual days / 365.
The rate stays at today's level until the position's **first repricing date inside the horizon**, then becomes
`max(0, rate + shock × pass-through)` for the rest of the window.
**Balance after** = balance × growth / run-off factor × (1 + FX or gold shock).
**NII change** is split into a *rate effect* (repricing) and a *volume / FX effect* (balances), and the two always add up to the total.
""")

    with tabs[3]:
        st.markdown("### What are *horizon days*?")
        st.markdown(f"""
The **horizon** is the length of the **time window you measure**, starting at the **simulation date**.
If the simulation date is 18 Oct and the horizon is 365 days, the engine measures everything between 18 Oct and 18 Oct next year.

**It is used for:**
- **Interest / NII:** interest earned and paid *inside the window* (a 90-day horizon gives roughly a quarter of the interest of a 365-day horizon).
- **When a shock bites:** a rate shock only changes a position's interest *after its repricing date*. A deposit that reprices in 30 days feels the shock for 335 of 365 days, but for only 60 of 90 days.
- **Cash flows / liquidity:** inflows and outflows dated inside the window are added up (net cash flow).
- **Ratios:** NIM and cost of funds are annualised with 365 / horizon, so they stay comparable across horizons.
- **Credit cost:** annual credit-cost rate × horizon / 365.
- **Repricing gap:** only positions that reprice inside the window count.

**It is *not* used for:** the LCR proxy, which always looks at the first **{LCR_WINDOW_DAYS} days**.

**Which value to choose:** 365 days is the standard one-year *earnings-at-risk* view. Use 30–90 days for a near-term / liquidity view and 730 days to see slower repricing.
It is saved with the scenario (`scenarios.horizon_days`).
""")
        st.markdown("#### Worked example with your data (base case, then a +3 pp rate shock)")
        rows = []
        for h in [30, 90, 180, 365, 730]:
            b, a, _ = apply_prepared(ctx.prep, st.session_state.get("current_simulation_date"), h, {"rate_change_pp": 3.0})
            rows.append([h, b["Position NII"], a["Position NII"], a["Position NII"] - b["Position NII"], b["Position NIM %"], b["CF Net Cashflow"]])
        ex = pd.DataFrame(rows, columns=["Horizon (days)", "Base NII (EGP)", "NII after +3 pp", "Change", "Base NIM %", "Base net cash flow"])
        if ctx.positions.empty:
            st.info("Connect to the database to see the example.")
        else:
            for c in ["Base NII (EGP)", "NII after +3 pp", "Change", "Base net cash flow"]:
                ex[c] = ex[c].apply(format_large_number)
            show_df(ex)
            st.caption("NII grows with the horizon (more days of interest) while NIM stays similar (it is annualised). The shock's effect depends on how many positions have repriced inside the window.")

    with tabs[4]:
        p = dict(SCENARIO_DEFAULTS)
        st.markdown(f"""
Macro variables are not only displayed: they change the calculation through **transmission coefficients** (editable on the Scenarios page, under *Macro transmission assumptions*).

| Macro shock | Channel | Formula (defaults) |
|---|---|---|
| **Inflation** | policy-rate response | effective rate shock = direct shock + {p['infl_to_rate']:g} × inflation shock |
| **Unemployment ↑** | extra deposit run-off | effective run-off = direct run-off + {p['unemp_to_runoff']:g} × unemployment increase |
| **Unemployment ↑** | credit cost | {p['unemp_to_credit_bps']:g} bps of the loan book per year per pp |
| **GDP ↓** | credit cost | {p['gdp_to_credit_bps']:g} bps of the loan book per year per pp of contraction |
| **GDP** | loan growth | effective loan growth = direct growth + {p['gdp_to_loan_growth']:g} × GDP shock |
| **FX / gold** | revaluation | balances, cash flows and net open positions revalued by the shock |

**Example:** inflation +4 pp with the default 0.5 coefficient adds a +2 pp rate shock; unemployment +3 pp adds 1.5 pp deposit run-off and a credit cost of 75 bps a year on loans.

> ⚠️ The coefficients are **illustrative defaults**, not statistical estimates. Calibrate them with the bank's own history (for example regress policy rate on inflation, or credit losses on unemployment) before using the results for decisions.
""")
        eff = effective_drivers(p)
        st.caption(f"With all shocks at 0 the effective drivers are neutral: {eff}")

    with tabs[5]:
        st.markdown(f"""
**Data sources** — tables `products`, `positions`, `cash_flows`, `market_data`, `scenarios`, `simulation_runs`, `simulation_results`
and views `{FLAT_POSITIONS_VIEW}`, `{FLAT_CASHFLOWS_VIEW}`. If a view does not return the same number of rows as its raw table (for example because a `product_id` is missing in `products`),
the app rebuilds the same flat table in Python and flags it under *Data & Settings → Data quality*.

**Valuation** — FX and gold positions are converted to EGP with the **latest** `market_data` rate (`FX_USD_EGP`, `GOLD_24K_EGP`, ...). Every amount in the app is EGP-equivalent.
Rates in the database are decimal fractions (0.1225 = 12.25 %).

**Key modelling assumptions**
- **Static balance sheet:** positions maturing inside the horizon are renewed at the same balance and reprice at maturity (switch: `ROLLOVER_AT_MATURITY`).
- **Parallel rate shift:** there is no yield curve in the data, so the shock is the same for all tenors.
- Positions already past their `maturity_date` are excluded and flagged.
- Deposits in the Treasury segment are treated as **wholesale funding**.
- A customer rate cannot fall below 0.
- The LCR proxy and the P&L total are analytical proxies (no fees, operating cost, tax or capital).
- Saved scenarios keep the pass-through, funding-cost, HQLA-haircut and macro-coefficient settings in the *description* column because the `scenarios` table has no columns for them.
- The AI never calculates; it only explains the numbers produced by Python.
""")

    with tabs[6]:
        st.markdown("How the application covers the project brief (✅ supported · 🟡 partly · ❌ not possible with the current data).")
        show_df(pd.DataFrame(COVERAGE, columns=["Area", "Brief item", "Status", "How / why"]), height=640)
