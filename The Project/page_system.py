"""Data & Settings page: data explorer, calculation audit, data quality, database & AI connection."""
import pandas as pd
import streamlit as st

from config import *
from database import table_row_counts
from ui_common import show_df, current_session_scenario


def _tab_explorer(ctx):
    data = ctx.data
    sources = {
        f"{FLAT_POSITIONS_VIEW} (SQL View)": ctx.positions,
        f"{FLAT_CASHFLOWS_VIEW} (SQL View)": ctx.cashflows,
        "products": data.get("products_raw"),
        "positions": data.get("positions_raw"),
        "cash_flows": data.get("cashflows_raw"),
        "market_data": data.get("market_data_raw"),
        "scenarios": data.get("scenarios_raw"),
        "simulation_runs": data.get("simulation_runs_raw"),
        "simulation_results": data.get("simulation_results_raw"),
    }
    name = st.selectbox("Table / view", list(sources.keys()))
    df = sources[name] if sources[name] is not None else pd.DataFrame()
    st.caption(f"Rows loaded: {len(df)}")
    show_df(df, height=520)


def _tab_audit(ctx):
    res = current_session_scenario(ctx)
    q = res.detail
    st.markdown("##### Independent Calculation Validation")
    show_df(res.validation)
    failed = int((res.validation["Status"] == "FAIL").sum()) if not res.validation.empty else 0
    (st.error if failed else st.success)(
        f"{failed} calculation validation check(s) failed." if failed
        else "All calculation checks passed within the configured tolerance."
    )


def _tab_quality(ctx):
    if ctx.engine is not None:
        show_df(table_row_counts(ctx.engine))
    show_df(current_session_scenario(ctx).quality)


def _tab_connection(db_connected, connection_error):
    host, port, database = st.session_state["db_host"], st.session_state["db_port"], st.session_state["db_name"]
    c1, c2 = st.columns(2)
    new_host = c1.text_input("Host", value=st.session_state["db_host"], key="advanced_db_host")
    new_port = c1.number_input("Port", 1, 65535, int(st.session_state["db_port"]), 1, key="advanced_db_port")
    new_db = c2.text_input("Database", value=st.session_state["db_name"], key="advanced_db_name")
    new_user = c2.text_input("User", value=st.session_state["db_user"], key="advanced_db_user")
    new_pw = st.text_input("Password", value=st.session_state["db_password"], type="password", key="advanced_db_password")
    if st.button("Save and reconnect", type="primary", use_container_width=True):
        st.session_state.update({
            "db_host": new_host, "db_port": int(new_port), "db_name": new_db,
            "db_user": new_user, "db_password": new_pw,
        })
        st.cache_resource.clear()
        st.cache_data.clear()
        st.rerun()
    if db_connected:
        st.success(f"Connected to {database} on {host}:{port}.")
    elif connection_error:
        st.code(connection_error, language="text")


def _tab_ai():
    ai_host = st.text_input("Ollama URL", value=st.session_state["ollama_host"], key="advanced_ollama_host")
    ai_model = st.text_input("Ollama model", value=st.session_state["ollama_model"], key="advanced_ollama_model")
    if st.button("Save AI configuration", use_container_width=True):
        st.session_state["ollama_host"], st.session_state["ollama_model"] = ai_host, ai_model
        st.success("AI configuration saved.")


def page_system(ctx, db_connected, connection_error):
    st.markdown('<div class="section-header">⚙️ Data & Settings</div>', unsafe_allow_html=True)
    t = st.tabs(["Data explorer", "Calculation audit", "Database connection", "AI (Ollama)"])
    with t[0]:
        _tab_explorer(ctx) if db_connected else st.info("Connect to the database first.")
    with t[1]:
        _tab_audit(ctx)
    # with t[2]:
    #     _tab_quality(ctx)
    with t[2]:
        _tab_connection(db_connected, connection_error)
    with t[3]:
        _tab_ai()