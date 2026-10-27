# Getting Started

## Prerequisites

| Item | Notes |
|---|---|
| Python 3.10+ | The code uses `tuple[bool, str]`-style typing and f-strings with nested quotes. |
| MySQL 8 | With the ALM database loaded (default name `nbe_alm_simulation`). |
| Ollama + Phi-3 | Optional. Needed only for AI explanations and AI parsing of free-text scenarios. |

Python packages, inferred from the imports:

```bash
pip install streamlit pandas numpy plotly sqlalchemy pymysql requests openpyxl
pip install reportlab python-pptx kaleido      # only for PDF / PPTX export
```

The report libraries are optional: without them the dashboard shows a hint instead of the export form.

## 1. Prepare the database

The application connects to an **existing** database and never creates tables. Besides the seven tables it expects two SQL views that flatten positions and cash flows into EGP-valued rows:

| Object | Type | Purpose |
|---|---|---|
| `products`, `positions`, `cash_flows`, `market_data` | tables | Source data |
| `scenarios`, `simulation_runs`, `simulation_results` | tables | Saved scenarios, runs and KPI results |
| `vw_alm_flat_table` | view | One row per position, valued in EGP |
| `vw_alm_cashflows_flat` | view | One row per cash flow, valued in EGP |

!!! tip "If a view is missing or incomplete"
    The loader compares the view's row count with the raw table. If they differ, it falls back to an equivalent `LEFT JOIN` query embedded in `database.py`, so no position is silently dropped. See [Database](database.md).

## 2. Configure

Settings come from environment variables, with defaults in `config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `DB_HOST` | `localhost` | MySQL host |
| `DB_PORT` | `3306` | MySQL port |
| `DB_NAME` | `nbe_alm_simulation` | Database |
| `DB_USER` | `root` | User |
| `DB_PASSWORD` | *(empty)* | Password |
| `DB_DRIVER` | `mysql+pymysql` | SQLAlchemy driver |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `phi3` | Model name |
| `OLLAMA_TIMEOUT` | `800` | Seconds per request |
| `OLLAMA_TEMPERATURE` | `0.0` | Sampling temperature for explanations |
| `DATA_CACHE_TTL_SECONDS` | `120` (constant) | How long loaded data is cached |

!!! warning "Development defaults"
    `root` with an empty password is for local development only. Use a dedicated least-privilege account anywhere else. See [Security](security.md).

The connection values can also be edited at run time in **Data & Settings → Database connection**.

## 3. Logo files (optional)

The interface and reports look for these images in the folder where the app is started. If a file is missing, the logo is skipped silently.

| File | Used for |
|---|---|
| `nbe_logo_only.png` | Sidebar icon |
| `nbe_logo_w.png` | Header banner and PowerPoint header (white version) |
| `nbe_logo.png` | PDF report header |

## 4. Run

=== "Windows shortcut"

    Double-click `Run_App.bat`. It starts Streamlit without a console window:

    ```bat
    @echo off
    cd /d "%~dp0"
    start "" pythonw -m streamlit run app.py
    exit
    ```

=== "Command line"

    ```bash
    streamlit run app.py
    ```

If the connection fails the app does not stop. Open **Data & Settings → Database connection**, where the real MySQL error is shown, fix the values and press **Save and reconnect**.

## 5. Optional: local AI

```bash
ollama pull phi3
ollama serve
```

Without Ollama, free-text scenarios fall back to regex parsing, and the explanation panel shows a message that the model is unavailable.

## 6. First run in three steps

1. **Scenarios → Create & run.** Pick *Combined Stress Scenario*, set values, press **Run & save simulation**.
2. **Dashboard.** Read the KPI cards, the management insight and the risk badge, then explore the tabs.
3. **Explain this scenario.** Ask the local AI for an executive explanation, then export a report if needed.

## Project layout

```text
NBE_ALM/
├── app.py              entry point
├── ui.py               shell: sidebar, data loading, routing
├── ui_common.py        theme, cards, scenario runner, AI panel
├── page_dashboard.py   dashboard and report builder
├── page_scenarios.py   create / saved / compare scenarios
├── page_system.py      data explorer, audit, connection, AI settings
├── page_documentation.py   (not wired in v2.1, see Limitations)
├── config.py           constants, parameters, thresholds
├── database.py         connection, loading, persistence
├── calculations.py     deterministic engine
├── scenarios.py        parameters, templates, free-text parsing, Ollama
├── analytics.py        KPIs, risk, attribution, validation, Excel export
├── reporting.py        PDF and PPTX generation
└── Run_App.bat         Windows launcher
```
