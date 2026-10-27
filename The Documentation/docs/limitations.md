# Limitations & Roadmap

This page lists what the model does not do and the defects found while documenting v2.1. They come from reading the code and from running the real engine on a synthetic portfolio, so each item can be reproduced.

## Modelling scope

| # | Limitation | Consequence |
|---|---|---|
| 1 | **Parallel rate shock only.** One shift applied to every position after its first repricing event. No yield curve, no product-specific pass-through, no beta | Cannot show that deposit rates lag market rates or that a steepening curve affects products differently |
| 2 | **`market_data` rates do not reprice anything.** It is used for FX and gold valuation and the macro snapshot | Rate scenarios are independent of the observed curve |
| 3 | **Balance changes are not funded.** Loan growth adds interest income with no matching funding cost; deposit growth is not invested | Growth scenarios look favourable to NII |
| 4 | **Run-off improves NII and the LCR proxy.** Deposits leave, so interest expense falls, and a smaller deposit base raises HQLA ÷ deposits | The sign is the opposite of a real run-off stress. In the verification portfolio, adding run-off raised NII and the proxy |
| 5 | **LCR is a proxy.** Headline value is HQLA ÷ deposits, compared with a 100 % limit. The 30-day stressed outflow denominator is computed but not used | Rates every scenario HIGH. **Confirmed on the sample database:** the base case shows LCR 79.5 % and overall risk HIGH (see the screenshot in [User Interface](ui.md)) |
| 6 | **Inflation has no KPI effect** | Setting it changes nothing except the stored value, the macro banner and the AI payload |
| 7 | **Macro shocks act only through credit cost**, with fixed 25 / 20 bps constants, one-sided (only adverse moves cost) | Illustrative, not calibrated |
| 8 | **Credit cost is only on loans** and is a flat rate on the stressed balance | No segment, stage or collateral detail |
| 9 | **Rollover assumption.** Positions maturing inside the window are assumed to roll at the same balance and reprice at maturity | No new-business volume or margin assumption |
| 10 | **Single horizon, no dynamic balance sheet** | No multi-period projection |
| 11 | **Pre-tax, no fees or non-interest income**, no capital or leverage | Not a full earnings forecast |
| 12 | **Investment MTM uses a simple duration** from the first repricing event | Convexity and credit spreads ignored |

## Defects and risks found in v2.1

| # | Finding | Where | Impact |
|---|---|---|---|
| A | **Excel export can disagree with the engine.** A fixed-rate loan rolling at maturity showed a repricing fraction of 100 % in Excel and 66.85 % in the engine, about 5.2 M EGP of interest on one position. Blank or past repricing dates are the sensitive case | `export_row_by_row_with_formulas_xlsx` | A reviewer recalculating in Excel may not match the dashboard |
| B | **AI panel writes to the database while rendering.** If no run is found for a scenario name, it inserts a scenario and a run | `render_ai_scenario_panel` | Opening a dashboard can create rows; runs are matched by **name**, so duplicate names collide |
| C | **String-built SQL** with only single quotes doubled | AI panel, report builder | Backslash sequences can break the query; use bound parameters |
| D | **`DOCS_BASE_URL` points to another account**, `amr-khaled-abdelfatach.github.io/NBE_ALM/` | `config.py`, `ui.py` | The sidebar *Documentation* button opens a different site |
| E | **`page_documentation.py` is broken and unused.** It imports `effective_drivers` and `doc_button`, which no longer exist | `page_documentation.py` | Importing it fails |
| F | **Hard-coded market values.** USD/EGP 48.613 and gold 4220.42 are typed into the dashboard banner. They equal the latest rows in the current dump (17 Oct 2026) | `page_dashboard.py` | Correct today, but the banner will go stale as soon as `market_data` is updated |
| G | **Debug line** `Current Run ID: …` is shown in the AI panel. In the dark theme the sidebar *Documentation* link button is nearly unreadable (dark text on a dark button) | `ui_common.py`, `ui.py` | Cosmetic |
| H | **Duplicate report-builder code.** An inline copy runs; a `@st.fragment` copy (`render_report_builder_section`) is never called | `page_dashboard.py` | Maintenance risk |
| I | **Silent column dropping.** Inserts keep only columns that exist | `persist_simulation` | A schema typo loses data without an error |
| J | **No authentication.** Every run is stored with `executed_by = "Streamlit"` | `database.py` | No audit trail of who ran what |
| K | **No automated tests** and few docstrings; `from config import *` hides origins of names | all modules | Harder review and safer refactoring is needed |
| L | **Commented-out code and unused settings**, for example the data-quality tab and several unused config values | various | Noise |

## Interpretation notes

- The column **Eligible** means *has a repricing event inside the horizon*.
- NII is for the horizon; NIM and cost of funds are annualised.
- The validation table checks internal consistency, not reconciliation to the ledger.
- The SQL dump supplied with the project is the older schema. The v2.1 structure documented here is derived from the code.

## Suggested roadmap

=== "Quick fixes"

    1. Point `DOCS_BASE_URL` to this site.
    2. Replace string-built SQL with bound parameters.
    3. Stop the AI panel from inserting rows; save runs only on **Run & save**.
    4. Remove the debug line, dead code and the broken page, or fix it.
    5. Read FX and gold for the banner from `market_data`.
    6. Align the Excel formulas with the engine's rollover rule, and add a test that compares them.

=== "Modelling"

    1. Replace the HQLA ÷ deposits proxy with a Basel-style LCR (HQLA over 30-day net stressed outflows) and calibrate the limit.
    2. Make run-off an outflow with a funding replacement cost.
    3. Add a yield curve and product pass-through betas.
    4. Fund balance growth, and add new-business margins.
    5. Calibrate the credit-cost constants and make them two-sided.
    6. Give inflation a defined channel or remove the driver.

=== "Platform"

    1. User login and `executed_by` from the session.
    2. Unit tests for the engine with golden cases, plus CI.
    3. Role-based access to saved scenarios and reports.
    4. Scheduled runs and approval workflow for ALCO packs.
