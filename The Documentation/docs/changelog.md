# Changelog: v1 to v2.1

Version 2.1.0, calculation engine 2.

## Summary

| Area | v1 | v2.1 |
|---|---|---|
| Structure | Seven modules | Fourteen files, with one module per page and a shared UI layer |
| Scenario modes | Four older modes | Base Case, Combined Stress, Individual Parameter, Free Type |
| Drivers | Rate shocks with pass-through parameters, yield-curve shifts, product-specific loan and deposit rate shocks | Nine drivers: policy rate, FX, gold, deposit run-off, loan growth, deposit growth, inflation, unemployment, GDP |
| Repricing | Yield-curve driven | Event-based on each position's repricing date, parallel shock |
| Balance effects | Rates mainly | Loan and deposit growth and run-off change interest |
| FX and gold | Not modelled as shocks | Revaluation effect and balance effect |
| P&L | NII focused | Total P&L and value impact: ΔNII, credit cost, FX/gold revaluation, investment MTM |
| Data access | Table reads | Two flat SQL views in EGP, with a LEFT JOIN fallback |
| Persistence | Several statements | One transaction; 15 KPI rows and 5 risk rows per run |
| Reports | None | PDF, PowerPoint, ZIP, Excel with formulas, CSV |
| Validation | Basic | Independent recomputation table, PASS or FAIL |
| AI | Explanation | Explanation saved to the run, free-text parsing with regex fallback |
| Launcher | Manual | `Run_App.bat` |

## Improvements

- Table names are matched case-insensitively (`LOWER(table_name)`), so behaviour is the same on Windows and Linux MySQL.
- The connection is built with `URL.create`, so passwords with special characters work.
- The SQLAlchemy engine is cached with `st.cache_resource`.
- Persistence runs in a single transaction.
- Loan and deposit growth now change interest, not just balances.
- Driver attribution with an interaction term.
- Maturity ladder, exposure tables and top-position sensitivity.
- Risk rating with explicit reasons.
- Reload button and a 120-second data cache.

## Removed

- Pass-through parameters and yield-curve repricing.
- Product-specific shocks such as the old loan and deposit rate shock fields.
- The old four-mode scenario naming.

## Known issues carried into v2.1

See [Limitations & Roadmap](limitations.md) for the full list, including the Excel mismatch, the AI panel writing during render, the wrong `DOCS_BASE_URL`, and the unwired documentation page.
