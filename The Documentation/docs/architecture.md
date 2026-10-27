# Architecture

## Layered view

```mermaid
graph TD
    U[Analyst / Browser] --> UI[ui.py<br/>sidebar, loading, routing]
    UI --> PD[page_dashboard.py]
    UI --> PS[page_scenarios.py]
    UI --> PY[page_system.py]
    PD --> UC[ui_common.py<br/>theme, cards, run_scenario, AI panel]
    PS --> UC
    PY --> UC
    PD --> RP[reporting.py<br/>PDF / PPTX / ZIP]
    UC --> AN[analytics.py]
    UC --> SC[scenarios.py]
    UC --> DB[database.py]
    AN --> CA[calculations.py]
    DB --> SC
    RP --> SC
    UI --> DB
    UI --> CA
    SC -.explanation / parsing only.-> OLL[(Ollama / Phi-3)]
    DB --> MY[(MySQL)]
    CA --> CFG[config.py]
    AN --> CFG
    SC --> CFG
    DB --> CFG
```

| Layer | Modules | Responsibility |
|---|---|---|
| Presentation | `ui.py`, `page_*.py`, `ui_common.py` | Screens, controls, charts, state |
| Domain logic | `calculations.py`, `analytics.py`, `scenarios.py` | Engine, KPIs, risk, validation, parameters |
| Reporting | `reporting.py`, Excel export in `analytics.py` | Documents and spreadsheets |
| Data access | `database.py` | Connection, reads, persistence |
| Configuration | `config.py` | Constants and thresholds |

`config` depends on nothing. `calculations` and `scenarios` depend only on `config`. There are no circular imports.

## Request lifecycle

Streamlit re-runs the script on every interaction. Each run does the following.

```mermaid
sequenceDiagram
    participant U as User
    participant UI as ui.py
    participant DB as database.py
    participant C as calculations.py
    participant P as Page module
    participant A as analytics.py
    U->>UI: interaction
    UI->>DB: connect_existing_database()
    DB-->>UI: engine (tables validated)
    UI->>DB: cached_alm_data() (TTL 120 s)
    DB-->>UI: flat positions, flat cash flows, market data, history
    UI->>C: prepare_inputs(positions, cashflows, market)
    C-->>UI: prep (normalized positions, cash flows, macro snapshot)
    UI->>P: page(ctx, history)
    P->>C: apply_prepared(prep, date, horizon, params)
    C-->>P: before KPIs, after KPIs, position detail
    P->>A: comparison, risk, validation, attribution
    A-->>P: tables
    P->>DB: persist_simulation() on "Run & save"
```

The shared context `ctx` (a `SimpleNamespace`) carries the engine, raw tables, the flat views, the prepared inputs, a data stamp used as a cache key, and the saved scenario history.

## Execution flow of a scenario

`run_scenario()` in `ui_common.py` is the single entry point used by every page:

1. `build_scenario_parameters` fills missing drivers with neutral defaults.
2. `apply_prepared` runs the position engine and the liquidity engine and returns `before`, `after` and the position-level `detail`.
3. `comparison_table`, `build_alm_risk_summary` and `overall_risk_rating` build the result tables.
4. `calculation_validation_table` recomputes the headline KPIs independently.

## State and caching

| Mechanism | What it holds |
|---|---|
| `st.session_state` | Connection settings, active template, scenario parameters, simulation date, horizon, AI text, report bytes |
| `st.cache_resource` | The SQLAlchemy engine, keyed by connection values |
| `st.cache_data` (120 s) | All loaded tables and views, plus driver attribution and sensitivity results |
| **Reload data** button | Clears the data cache |

## Local AI boundary

```mermaid
graph LR
    T[Free text] --> Q{Ollama running<br/>and model installed?}
    Q -- yes --> J[Phi-3 returns JSON<br/>temperature 0]
    Q -- no --> R[Regex parser]
    J --> M[Regex fills any blank keys]
    R --> M
    M --> N[Numeric parameters]
    N --> D[Deterministic engine]
    D --> X[All numbers]
    X -. read only .-> E[Phi-3 explanation<br/>saved to simulation_runs]
```

The model does two things only: it extracts explicit numbers from text, and it narrates results that Python already computed. See [AI Assistant](ai.md).

## Deployment shape

A single Streamlit process, one MySQL database and an optional local Ollama service. There is no application server, message queue or external API. This keeps data on the bank's own machines.
