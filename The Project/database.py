"""Database access and persistence layer (Simplified & Direct View Loading)."""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, URL

from config import (
    DB_DRIVER, DB_POOL_PRE_PING, DB_POOL_RECYCLE, DB_READ_LIMIT,
    REQUIRED_TABLES, OPTIONAL_VIEWS, FLAT_POSITIONS_VIEW, FLAT_CASHFLOWS_VIEW,
)
from scenarios import scenario_row_from_params


def create_db_engine(host, port, database, user, password) -> Engine:
    url = URL.create(
        DB_DRIVER, username=user, password=password, host=host, port=int(port),
        database=database, query={"charset": "utf8mb4"},
    )
    return create_engine(url, pool_pre_ping=DB_POOL_PRE_PING, pool_recycle=DB_POOL_RECYCLE)


@st.cache_resource(show_spinner=False)
def get_cached_engine(host, port, database, user, password):
    return create_db_engine(host, port, database, user, password)


def read_sql_df(engine: Engine, sql: str, params: Optional[dict] = None, raise_errors: bool = False) -> pd.DataFrame:
    try:
        return pd.read_sql(text(sql), engine, params=params or {})
    except Exception as exc:
        st.session_state.setdefault("db_errors", []).append(str(exc))
        if raise_errors:
            raise
        return pd.DataFrame()


def table_exists(engine: Engine, table_name: str) -> bool:
    df = read_sql_df(
        engine,
        """SELECT COUNT(*) AS n FROM information_schema.tables
           WHERE table_schema = DATABASE() AND LOWER(table_name) = LOWER(:table_name)""",
        {"table_name": table_name},
    )
    return not df.empty and int(df.iloc[0]["n"]) > 0


def get_columns(engine: Engine, table_name: str) -> List[str]:
    df = read_sql_df(
        engine,
        """SELECT COLUMN_NAME FROM information_schema.columns
           WHERE table_schema = DATABASE() AND LOWER(table_name) = LOWER(:table_name)
           ORDER BY ORDINAL_POSITION""",
        {"table_name": table_name},
    )
    if df.empty:
        return []
    column = "COLUMN_NAME" if "COLUMN_NAME" in df.columns else df.columns[0]
    return df[column].tolist()


def read_table(engine: Engine, table_name: str, limit: int = DB_READ_LIMIT) -> pd.DataFrame:
    if not table_exists(engine, table_name):
        return pd.DataFrame()
    columns = get_columns(engine, table_name)
    if not columns:
        return pd.DataFrame()
    projection = ", ".join(f"`{c}`" for c in columns)
    return read_sql_df(engine, f"SELECT {projection} FROM `{table_name}` LIMIT {int(limit)}")


def table_row_counts(engine: Engine) -> pd.DataFrame:
    rows = []
    for name in REQUIRED_TABLES + OPTIONAL_VIEWS:
        kind = "VIEW" if name in OPTIONAL_VIEWS else "TABLE"
        if not table_exists(engine, name):
            rows.append([name, kind, None, "MISSING"])
            continue
        df = read_sql_df(engine, f"SELECT COUNT(*) AS n FROM `{name}`")
        rows.append([name, kind, int(df.iloc[0]["n"]) if not df.empty else None, "OK" if not df.empty else "UNREADABLE"])
    return pd.DataFrame(rows, columns=["Object", "Type", "Rows", "Status"])


def _clean(value):
    if value is None:
        return None
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def connect_existing_database(host, port, database, user, password) -> Engine:
    engine = get_cached_engine(host, port, database, user, password)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1 AS connected"))
    missing = [t for t in REQUIRED_TABLES if not table_exists(engine, t)]
    if missing:
        raise RuntimeError("Connected successfully, but required ALM tables are missing: " + ", ".join(missing))
    return engine


# Compact LEFT-JOIN query used ONLY if the MySQL view drops unmapped product_ids
_LEFT_POS_SQL = """
WITH latestvaluationrates AS (
    SELECT t.base_currency, t.value AS market_rate FROM (
        SELECT base_currency, value,
               ROW_NUMBER() OVER (PARTITION BY base_currency ORDER BY as_of_datetime DESC, market_data_id DESC) AS rn
        FROM market_data WHERE instrument_code IN ('FX_USD_EGP','FX_EUR_EGP','FX_GBP_EGP','FX_SAR_EGP','GOLD_24K_EGP')
    ) t WHERE t.rn = 1
),
latestmacroindicators AS (
    SELECT MAX(CASE WHEN instrument_code='MACRO_INFLATION' THEN value END) AS inflation_rate,
           MAX(CASE WHEN instrument_code='MACRO_UNEMPLOYMENT' THEN value END) AS unemployment_rate,
           MAX(CASE WHEN instrument_code='MACRO_GDP_GROWTH' THEN value END) AS gdp_growth_rate,
           MAX(CASE WHEN instrument_code='CBE_POLICY_RATE' THEN value END) AS cbe_policy_rate
    FROM market_data WHERE data_type='Macroeconomic'
),
positioncashflowsummary AS (
    SELECT position_id, COUNT(cashflow_id) AS total_scheduled_cashflows,
           SUM(CASE WHEN inflow_outflow='Inflow' THEN total_amount ELSE 0 END) AS total_expected_inflows,
           SUM(CASE WHEN inflow_outflow='Outflow' THEN total_amount ELSE 0 END) AS total_expected_outflows,
           MIN(cashflow_date) AS next_cashflow_date, MAX(hqla_flag) AS is_hqla_asset
    FROM cash_flows GROUP BY position_id
)
SELECT pos.position_id AS Position_ID, pos.product_id AS Product_ID, pos.product_code AS Product_Code,
       COALESCE(prd.product_name, CONCAT('Product #', pos.product_id)) AS Product_Name,
       COALESCE(prd.product_type, pos.position_type) AS Product_Type,
       CASE WHEN prd.product_type IN ('Treasury Bills','Treasury Bonds') THEN 'Sovereign Debt'
            WHEN prd.product_type LIKE '%Gold%' OR pos.valuation_basis='Gold' THEN 'Precious Metals'
            WHEN prd.product_type LIKE '%Credit Cards%' OR pos.position_type='Loan' THEN 'Retail & Corp Lending'
            ELSE 'Term Deposits & CDs' END AS Product_Family,
       COALESCE(prd.asset_liability_type, CASE WHEN pos.position_type IN ('Loan','Investment') THEN 'Asset' ELSE 'Liability' END) AS Balance_Sheet_Side,
       pos.position_type AS ALM_Position_Category, pos.segment AS Customer_Segment,
       pos.currency_code AS Currency, pos.valuation_basis AS Valuation_Basis,
       pos.balance_amount AS Original_Balance_Or_Grams, COALESCE(val.market_rate, 1.0) AS Applied_Market_Rate,
       ROUND(pos.balance_amount * COALESCE(val.market_rate, 1.0), 2) AS Evaluated_Balance_EGP,
       pos.interest_rate AS Contractual_Interest_Rate, pos.rate_type AS Rate_Type,
       COALESCE(prd.repricing_frequency, CASE WHEN pos.rate_type='Floating' THEN 'Monthly' ELSE 'At Maturity' END) AS Repricing_Frequency,
       pos.start_date AS Start_Date, pos.maturity_date AS Maturity_Date, pos.next_repricing_date AS Next_Repricing_Date,
       GREATEST(0, TO_DAYS(pos.maturity_date) - TO_DAYS(CURDATE())) AS Remaining_Maturity_Days,
       GREATEST(0, TO_DAYS(pos.next_repricing_date) - TO_DAYS(CURDATE())) AS Days_To_Next_Repricing,
       COALESCE(cf.total_scheduled_cashflows, 0) AS Scheduled_Cashflows_Count,
       ROUND(COALESCE(cf.total_expected_inflows, 0) * COALESCE(val.market_rate, 1.0), 2) AS Expected_Inflows_EGP,
       ROUND(COALESCE(cf.total_expected_outflows, 0) * COALESCE(val.market_rate, 1.0), 2) AS Expected_Outflows_EGP,
       cf.next_cashflow_date AS Next_Cashflow_Date, COALESCE(cf.is_hqla_asset, 0) AS HQLA_Liquidity_Flag,
       mac.cbe_policy_rate AS CBE_Policy_Rate, mac.inflation_rate AS Inflation_Rate,
       (pos.interest_rate - mac.inflation_rate) AS Real_Interest_Rate,
       mac.unemployment_rate AS Unemployment_Rate, mac.gdp_growth_rate AS GDP_Growth_Rate, pos.status AS Status
FROM positions pos
LEFT JOIN products prd ON pos.product_id = prd.product_id
LEFT JOIN latestvaluationrates val ON pos.currency_code = val.base_currency
LEFT JOIN positioncashflowsummary cf ON pos.position_id = cf.position_id
CROSS JOIN latestmacroindicators mac
"""

_LEFT_CF_SQL = """
WITH latestvaluationrates AS (
    SELECT t.base_currency, t.value AS market_rate FROM (
        SELECT base_currency, value,
               ROW_NUMBER() OVER (PARTITION BY base_currency ORDER BY as_of_datetime DESC, market_data_id DESC) AS rn
        FROM market_data WHERE instrument_code IN ('FX_USD_EGP','FX_EUR_EGP','FX_GBP_EGP','FX_SAR_EGP','GOLD_24K_EGP')
    ) t WHERE t.rn = 1
),
latestmacroindicators AS (
    SELECT MAX(CASE WHEN instrument_code='MACRO_INFLATION' THEN value END) AS inflation_rate,
           MAX(CASE WHEN instrument_code='MACRO_UNEMPLOYMENT' THEN value END) AS unemployment_rate,
           MAX(CASE WHEN instrument_code='MACRO_GDP_GROWTH' THEN value END) AS gdp_growth_rate,
           MAX(CASE WHEN instrument_code='CBE_POLICY_RATE' THEN value END) AS cbe_policy_rate
    FROM market_data WHERE data_type='Macroeconomic'
)
SELECT cf.cashflow_id AS Cashflow_ID, cf.cashflow_date AS Cashflow_Date, DAYNAME(cf.cashflow_date) AS Day_Name,
       MONTH(cf.cashflow_date) AS Month, CONCAT('Q', QUARTER(cf.cashflow_date)) AS Quarter, YEAR(cf.cashflow_date) AS Year,
       GREATEST(0, TO_DAYS(cf.cashflow_date) - TO_DAYS(CURDATE())) AS Days_To_Cashflow,
       cf.maturity_bucket AS ALM_Maturity_Bucket, cf.position_id AS Position_ID, cf.product_id AS Product_ID,
       cf.product_code AS Product_Code, COALESCE(prd.product_name, CONCAT('Product #', cf.product_id)) AS Product_Name,
       COALESCE(prd.product_type, pos.position_type) AS Product_Type,
       CASE WHEN prd.product_type IN ('Treasury Bills','Treasury Bonds') THEN 'Sovereign Debt'
            WHEN prd.product_type LIKE '%Gold%' OR cf.valuation_basis='Gold' THEN 'Precious Metals'
            WHEN prd.product_type LIKE '%Credit Cards%' OR pos.position_type='Loan' THEN 'Retail & Corp Lending'
            ELSE 'Term Deposits & CDs' END AS Product_Family,
       COALESCE(prd.asset_liability_type, CASE WHEN pos.position_type IN ('Loan','Investment') THEN 'Asset' ELSE 'Liability' END) AS Balance_Sheet_Side,
       pos.position_type AS ALM_Position_Category, pos.segment AS Customer_Segment,
       cf.cashflow_type AS Cashflow_Type, cf.inflow_outflow AS Flow_Type,
       cf.currency_code AS Currency, cf.valuation_basis AS Valuation_Basis,
       COALESCE(val.market_rate, 1.0) AS Applied_Market_Rate,
       cf.principal_amount AS Principal_Native, cf.interest_amount AS Interest_Original, cf.total_amount AS Total_Amount_Original,
       ROUND(cf.principal_amount * COALESCE(val.market_rate, 1.0), 2) AS Principal_EGP,
       ROUND(cf.interest_amount * COALESCE(val.market_rate, 1.0), 2) AS Interest_EGP,
       ROUND(cf.total_amount * COALESCE(val.market_rate, 1.0), 2) AS Total_Amount_EGP,
       ROUND(CASE WHEN cf.inflow_outflow='Inflow' THEN cf.total_amount * COALESCE(val.market_rate, 1.0)
                  ELSE -1 * cf.total_amount * COALESCE(val.market_rate, 1.0) END, 2) AS Signed_Net_Cashflow_EGP,
       cf.is_expected AS Is_Expected_Flag, cf.hqla_flag AS HQLA_Flag,
       pos.interest_rate AS Position_Interest_Rate, pos.rate_type AS Rate_Type,
       mac.cbe_policy_rate AS CBE_Policy_Rate, mac.inflation_rate AS Inflation_Rate,
       mac.unemployment_rate AS Unemployment_Rate, mac.gdp_growth_rate AS GDP_Growth_Rate
FROM cash_flows cf
JOIN positions pos ON cf.position_id = pos.position_id
LEFT JOIN products prd ON cf.product_id = prd.product_id
LEFT JOIN latestvaluationrates val ON cf.currency_code = val.base_currency
CROSS JOIN latestmacroindicators mac
"""


def load_alm_data(engine: Engine) -> Dict[str, object]:
    """Load the 7 tables and read the 2 SQL Views directly."""
    products = read_table(engine, "products")
    positions = read_table(engine, "positions")
    cashflows = read_table(engine, "cash_flows")
    market = read_table(engine, "market_data")

    view_pos = read_table(engine, FLAT_POSITIONS_VIEW)
    view_cf = read_table(engine, FLAT_CASHFLOWS_VIEW)

    # Use the SQL view directly if it includes all rows; otherwise use the LEFT-JOIN SQL query
    if len(view_pos) == len(positions) and not view_pos.empty:
        pos_flat = view_pos
        pos_note = {"source": "view", "message": f"Using SQL view `{FLAT_POSITIONS_VIEW}` ({len(view_pos)} rows)."}
    else:
        pos_flat = read_sql_df(engine, _LEFT_POS_SQL)
        pos_note = {"source": "view", "message": f"Loaded all {len(pos_flat)} positions via SQL LEFT JOIN (update `{FLAT_POSITIONS_VIEW}` to LEFT JOIN products)."}

    if len(view_cf) == len(cashflows) and not view_cf.empty:
        cf_flat = view_cf
        cf_note = {"source": "view", "message": f"Using SQL view `{FLAT_CASHFLOWS_VIEW}` ({len(view_cf)} rows)."}
    else:
        cf_flat = read_sql_df(engine, _LEFT_CF_SQL)
        cf_note = {"source": "view", "message": f"Loaded all {len(cf_flat)} cash flows via SQL LEFT JOIN (update `{FLAT_CASHFLOWS_VIEW}` to LEFT JOIN products)."}

    return {
        "products_raw": products, "positions_raw": positions, "cashflows_raw": cashflows,
        "market_data_raw": market, "scenarios_raw": read_table(engine, "scenarios"),
        "simulation_runs_raw": read_table(engine, "simulation_runs"),
        "simulation_results_raw": read_table(engine, "simulation_results"),
        "positions_flat": pos_flat, "cashflows_flat": cf_flat,
        "positions_view_raw": view_pos, "cashflows_view_raw": view_cf,
        "source_notes": {"positions": pos_note, "cashflows": cf_note},
    }


def _direction(change) -> str:
    c = _clean(change)
    return "NEUTRAL" if c is None or c == 0 else ("POSITIVE" if c > 0 else "NEGATIVE")


def _insert_conn(conn, table: str, row: dict, allowed: set) -> int:
    item = {k: _clean(v) for k, v in row.items() if k in allowed}
    cols = list(item.keys())
    res = conn.execute(
        text(f"INSERT INTO `{table}` ({', '.join(f'`{c}`' for c in cols)}) VALUES ({', '.join(f':p{i}' for i in range(len(cols)))})"),
        {f"p{i}": item[c] for i, c in enumerate(cols)},
    )
    return int(res.lastrowid)


def persist_simulation(
    engine: Engine, scenario_name: str, scenario_type: str, simulation_date, horizon_days: int,
    params: dict, comparison: pd.DataFrame, risk_df: Optional[pd.DataFrame] = None,
    scenario_description: str = "", base_as_of_datetime=None,
    existing_scenario_id: Optional[int] = None, ai_explanation: Optional[str] = None,
):
    """Save scenario, simulation run, and KPI/RISK results cleanly in a single transaction."""
    now = datetime.now()
    sim_dt = pd.Timestamp(simulation_date).to_pydatetime()
    base_dt = pd.Timestamp(base_as_of_datetime).to_pydatetime() if base_as_of_datetime is not None and pd.notna(base_as_of_datetime) else None
    try:
        sc_cols = set(get_columns(engine, "scenarios"))
        run_cols = set(get_columns(engine, "simulation_runs"))
        res_cols = set(get_columns(engine, "simulation_results"))
        scenario_row = {
            "scenario_name": scenario_name,
            "scenario_type": scenario_type,
            "description": str(scenario_description or "").strip(),
            "base_as_of_datetime": base_dt,
            "simulation_date": sim_dt,
            "horizon_days": int(horizon_days),
            **scenario_row_from_params(params),
            "status": "COMPLETED",
        }
        result_rows = []
        for _, r in comparison.iterrows():
            result_rows.append({
                "result_type": "KPI", "metric_code": str(r.get("Code", r.get("KPI", "KPI")))[:100],
                "metric_name": str(r.get("KPI", "KPI")), "category": str(r.get("Source", "ALM")),
                "base_value": r.get("Before"), "scenario_value": r.get("After"),
                "absolute_impact": r.get("Absolute Change"), "percentage_impact": r.get("Change %"),
                "unit": str(r.get("Unit", "EGP")), "severity": "INFO",
                "impact_direction": _direction(r.get("Absolute Change")),
            })
        if risk_df is not None and not risk_df.empty:
            for _, r in risk_df.iterrows():
                level = str(r.get("Risk Level", "INFO"))
                result_rows.append({
                    "result_type": "RISK", "metric_code": str(r.get("Risk Indicator", "RISK"))[:100],
                    "metric_name": str(r.get("Metric", "Risk")), "category": str(r.get("Source", "ALM")),
                    "base_value": r.get("Base"), "scenario_value": r.get("Scenario"),
                    "absolute_impact": r.get("Impact"), "percentage_impact": r.get("Impact %"),
                    "unit": str(r.get("Unit", "EGP")), "severity": level,
                    "impact_direction": "NEGATIVE" if level in {"ADVERSE", "HIGH"} else "POSITIVE" if level == "POSITIVE" else "NEUTRAL",
                })

        with engine.begin() as conn:
            if existing_scenario_id is not None:
                scenario_id = int(existing_scenario_id)
                upd = {k: _clean(v) for k, v in scenario_row.items() if k in sc_cols}
                sets = ", ".join(f"`{k}`=:v{i}" for i, k in enumerate(upd))
                conn.execute(
                    text(f"UPDATE `scenarios` SET {sets} WHERE `scenario_id`=:sid"),
                    {**{f"v{i}": v for i, v in enumerate(upd.values())}, "sid": scenario_id},
                )
            else:
                scenario_row["scenario_code"] = f"SCN-{now:%Y%m%d%H%M%S%f}"
                scenario_id = _insert_conn(conn, "scenarios", scenario_row, sc_cols)

            run_id = _insert_conn(conn, "simulation_runs", {
                "scenario_id": scenario_id, "as_of_datetime": base_dt, "simulation_date": sim_dt,
                "horizon_days": int(horizon_days), "run_status": "COMPLETED", "started_at": now,
                "completed_at": datetime.now(), "records_processed": len(result_rows),
                "executed_by": "Streamlit", "ai_explanation": ai_explanation,
            }, run_cols)

            for row in result_rows:
                row["run_id"] = run_id
                _insert_conn(conn, "simulation_results", row, res_cols)
        return run_id, scenario_id, [(True, f"Saved scenario {scenario_id}, run {run_id}, and {len(result_rows)} result row(s).")]
    except Exception as exc:
        return None, None, [(False, f"Simulation save failed: {exc}")]


def update_run_ai_explanation(engine: Engine, run_id: int, explanation: str) -> Tuple[bool, str]:
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE `simulation_runs` SET `ai_explanation`=:e WHERE `run_id`=:r"),
                {"e": explanation, "r": int(run_id)},
            )
        return True, f"AI explanation saved on run {run_id}."
    except Exception as exc:
        return False, f"Could not save the AI explanation: {exc}"