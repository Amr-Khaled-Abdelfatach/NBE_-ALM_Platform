# Reports & Exports

## Overview

| Output | Where | Format | Needs |
|---|---|---|---|
| Executive report | Dashboard → *Export Custom Executive Report* | PDF, PPTX, or ZIP of both | `reportlab`, `python-pptx`, `kaleido` |
| Row-by-Row table | Dashboard → *Row-by-Row Details* | Excel (`.xlsx`) with live formulas | `openpyxl` |
| Top position sensitivity | Dashboard → *Top Position Sensitivity* | CSV | none |
| Liquidity ladder | Dashboard → *Liquidity Ladder* | CSV | none |
| Exposure breakdown | Dashboard → *Exposure Breakdown* | CSV | none |

CSV files are written as UTF-8 with BOM so Excel opens Arabic text correctly.

## Executive report builder

The form lets the analyst choose:

1. **KPIs.** Up to nine cards: NII, NIM, cost of funds, total P&L and value impact, LCR, net cash flow, total loans, total deposits. The first six are pre-selected.
2. **Charts.** NII bridge, P&L components, portfolio before vs after, and balance by currency when available.
3. **Include comparison table** and **Include management / AI insight**.
4. **Output format.** PDF, PPTX, or both.

Charts are rendered to PNG by Kaleido (`plotly_fig_to_png_bytes`) and embedded.

### What goes into each file

=== "PDF (landscape A4)"

    1. Title, scenario type, date and horizon
    2. Active scenario parameters, shown as chips
    3. Scenario explanation and management insight
    4. Selected KPI table (before, after, change)
    5. Selected charts
    6. Full comparison table
    7. Header and footer on every page

=== "PowerPoint"

    1. Title slide
    2. Parameters slide
    3. KPI slide
    4. AI or management insight slide
    5. One slide per selected chart
    6. NBE-style header and footer on every slide

=== "ZIP bundle"

    Choosing *Both* produces a ZIP containing the PDF and the PPTX with the same base name, plus buttons to download either one alone.

File names follow `NBE_ALM_Report_<scenario>_<yyyymmdd>`.

### Where the explanation text comes from

The report never starts the AI. It looks, in order, for:

1. the explanation already shown on the dashboard in this session,
2. the explanation saved on the run in `simulation_runs.ai_explanation`,
3. otherwise the deterministic management insight sentences.

So a report is always reproducible and never blocks waiting for the model. Generate the AI explanation first if you want it in the report.

!!! note "Header logo"
    The report header uses the logo files named in [Getting Started](getting-started.md) when they are present next to the app.

## Row-by-Row Excel

`build_full_row_by_row_scenario_table` joins each position's inputs, the scenario parameters and the isolated effects. `export_row_by_row_with_formulas_xlsx` then writes the table to Excel with **live formulas** for interest, effects and totals, so a reviewer can change a cell and see the result recalculate.

!!! warning "Excel can differ from the dashboard in edge cases"
    The workbook recomputes interest from the repricing date and frequency columns with its own formulas. While verifying this documentation against a synthetic portfolio, the Excel and the engine disagreed for a fixed-rate loan that rolls over at maturity: the engine gave a repricing fraction of 66.85 %, the workbook 100 %, a difference of about 5.2 million EGP of interest on that one position. Positions with a blank or already-past repricing date are the sensitive case. Treat the dashboard (engine) numbers as the reference and use the workbook to inspect structure. See [Limitations](limitations.md).

## Checking a report

Before circulating a report, compare its KPI table with the **Calculation audit** tab (Data & Settings). All rows should read PASS.
