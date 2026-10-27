"""ui.py — NBE ALM application shell: navigation, data loading, page routing."""
import os
from types import SimpleNamespace

import pandas as pd
import streamlit as st

import base64

from config import *
from database import connect_existing_database, load_alm_data
from calculations import prepare_inputs, latest_market_date
from scenarios import scenario_summary, template_values
from ui_common import CUSTOM_CSS, render_page_header
from page_dashboard import page_dashboard
from page_scenarios import page_scenarios, apply_template
from page_system import page_system

DOCS_URL = globals().get("DOCS_BASE_URL", "https://amr-khaled-abdelfatach.github.io/NBE_ALM/")



def nav_button(label):
    active = st.session_state.get("main_page", "Dashboard") == label
    safe_key = "".join(c if c.isalnum() else "_" for c in label)
    if st.sidebar.button(
        label,
        key="nav_" + safe_key,
        use_container_width=True,
        type="primary" if active else "secondary",
    ):
        st.session_state["main_page"]  = label
        st.rerun()




@st.cache_data(ttl=DATA_CACHE_TTL_SECONDS, show_spinner="Loading ALM data…")
def cached_alm_data(_engine, host, port, database, user):
    return load_alm_data(_engine)


def _data_stamp(data) -> str:
    pos, cf, md = data.get("positions_flat"), data.get("cashflows_flat"), data.get("market_data_raw")
    parts = [len(pos) if pos is not None else 0, len(cf) if cf is not None else 0, len(md) if md is not None else 0]
    if pos is not None and not pos.empty and "Evaluated_Balance_EGP" in pos.columns:
        parts.append(round(float(pd.to_numeric(pos["Evaluated_Balance_EGP"], errors="coerce").sum()), 2))
    parts.append(str(latest_market_date(md)) if md is not None else "")
    return "|".join(str(p) for p in parts)


def run_app():
    os.environ.setdefault("STREAMLIT_THEME_BASE", "light")
    os.environ.setdefault("STREAMLIT_THEME_PRIMARY_COLOR", NBE_GREEN)
    try:
        st.set_option("theme.base", "light")
    except Exception:
        pass
    st.set_page_config(
        page_title=PAGE_TITLE,
        page_icon=PAGE_ICON,
        layout=PAGE_LAYOUT,
        initial_sidebar_state=INITIAL_SIDEBAR_STATE,
    )
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    for key, value in [
        ("db_host", DB_HOST), ("db_port", DB_PORT), ("db_name", DB_NAME),
        ("db_user", DB_USER), ("db_password", DB_PASSWORD),
        ("ollama_host", OLLAMA_HOST), ("ollama_model", OLLAMA_MODEL),
    ]:
        st.session_state.setdefault(key, value)

    # ---------------- القائمة الجانبية (بدون أي تكرار) ----------------

    if os.path.exists("nbe_logo_only.png"):
        with open("nbe_logo_only.png", "rb") as f:
            encoded_sidebar_logo = base64.b64encode(f.read()).decode()
        st.sidebar.markdown(
            f'<div style="text-align: center; margin-bottom: 15px;">'
            f'<img src="data:image/png;base64,{encoded_sidebar_logo}" style="max-width: 130px; height: auto; border-radius: 8px;" alt="NBE Logo">'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.sidebar.markdown("# 🏦 NBE ALM")

    st.sidebar.caption("")
    st.sidebar.markdown("**MAIN**")
    for label in ["Dashboard", "Scenarios"]:  # <--- أضف "Data Analytics" هنا
        nav_button(label)

    # زر التوثيق الخارجي
    st.sidebar.link_button("Documentation", DOCS_URL, use_container_width=True)

    st.sidebar.markdown("**SYSTEM**")
    nav_button("Data & Settings")
    if st.sidebar.button("Reload data", key="nav_reload_data", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    selected = st.session_state.get("main_page", "Dashboard")
    if selected not in PAGES:
        selected = "Dashboard"
    page = PAGES[selected]

    # ---------------- تحميل البيانات ----------------
    host, port = str(st.session_state["db_host"]), int(st.session_state["db_port"])
    database, user, password = (
        str(st.session_state["db_name"]),
        str(st.session_state["db_user"]),
        str(st.session_state["db_password"]),
    )
    engine, connection_error, db_connected = None, None, False
    try:
        engine = connect_existing_database(host, port, database, user, password)
        db_connected = True
    except Exception as exc:
        connection_error = str(exc)

    if not db_connected:
        st.error(f"Database connection failed: {connection_error}")
        if page != "system":
            st.warning("Open **Data & Settings → Database connection** to verify Host, Port, Database, User and Password.")

    data = cached_alm_data(engine, host, port, database, user) if db_connected else {}
    empty = pd.DataFrame()
    positions, cashflows, market = (
        data.get("positions_flat", empty),
        data.get("cashflows_flat", empty),
        data.get("market_data_raw", empty),
    )
    ctx = SimpleNamespace(
        engine=engine, data=data, positions=positions, cashflows=cashflows, market_data=market,
        prep=prepare_inputs(positions, cashflows, market), stamp=_data_stamp(data),
        scenarios=data.get("scenarios_raw", empty), runs=data.get("simulation_runs_raw", empty),
        results=data.get("simulation_results_raw", empty),
        raw={k: data.get(k, empty) for k in ["products_raw", "positions_raw", "cashflows_raw"]},
        source_notes=data.get("source_notes", {}),
    )
    history = scenario_summary(ctx.scenarios)

    render_page_header(
        "NBE ALM Simulation Center",
        "Scenario → Impact → Key indicators → Management insight | Deterministic calculations | Local Phi-3 explanations",
        "Connected" if db_connected else "Connection required",
    )
    # doc_button()

    if "active_template" not in st.session_state:
        apply_template("Combined Stress Scenario")
    st.session_state.setdefault("builder_values", template_values(st.session_state["active_template"]))

    if page == "dashboard":
        page_dashboard(ctx, history)
    elif page == "scenarios":
        page_scenarios(ctx, history)

    
    else:
        page_system(ctx, db_connected, connection_error)

    st.divider()
    st.caption("ALM calculation engine: deterministic Python calculations. Ollama is explanation-only and does not alter the numbers.")