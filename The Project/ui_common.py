"""Shared UI helpers: theme, cards, scenario execution, cached analytics, AI panel."""
from datetime import date
from types import SimpleNamespace
import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import text
from config import *
from calculations import apply_prepared
from scenarios import build_scenario_parameters, scenario_params_from_row, ask_ollama
from database import update_run_ai_explanation
from analytics import (
    comparison_table, build_alm_risk_summary, build_data_quality_checks,
    calculation_validation_table, shock_sensitivity, driver_attribution,
    fmt_amount, overall_risk_rating,
)

format_large_number = fmt_amount

CUSTOM_CSS = r"""
<style>
.block-container { padding-top: 1.5rem !important; max-width: 1500px; }

.section-header { font-size: 1.25rem; font-weight: 700; margin: 1rem 0 .5rem 0; color: var(--text-color); border-bottom: 2px solid #EAF5EF; padding-bottom: .4rem; }
[data-testid="stMetric"] { background-color: var(--secondary-background-color); border: 1px solid var(--border-color); border-radius: 14px; padding: 14px; }
input, textarea, select, [data-baseweb="input"], [data-baseweb="select"], [data-baseweb="textarea"] { border-radius: 10px !important; }
.stButton > button { border-radius: 12px !important; min-height: 42px; font-weight: 600; transition: all .2s; }
.stButton > button:hover { border-color: #007A3D !important; color: #007A3D !important; }
.stButton > button[kind="primary"] { background:#007A3D !important; color:#FFFFFF !important; border-color:#007A3D !important; }
.stButton > button[kind="primary"]:hover { background:#005B2D !important; border-color:#005B2D !important; color:#FFFFFF !important; }
[data-testid="stSidebar"] { border-right: 1px solid var(--border-color); }

/* البانر العلوي */
.app-header { 
    display: flex; 
    justify-content: space-between; 
    align-items: center; 
    padding: 18px 24px; 
    margin-bottom: 16px; 
    background: linear-gradient(135deg, #005B2D, #007A3D 70%, #2E8B57); 
    border-radius: 16px; 
    box-shadow: 0 8px 24px rgba(0,91,45,.16); 
}

/* العنوان في اليسار */
.app-title-container { 
    display: flex; 
    flex-direction: column;
    text-align: left;
}

.app-logo { 
    height: 48px; 
    width: auto; 
    border-radius: 8px; 
    background: transparent; 
    padding: 2px; 
}

.app-title { color: #fff; font-size: 1.55rem; font-weight: 750; }
.app-subtitle { color: #EAF5EF; font-size: .86rem; margin-top: 3px; }

/* الجزء الأيسر الذي يحوي الحالة */
.app-left-section {
    display: flex;
    align-items: center;
    gap: 15px;
}

.app-status { color: #fff; font-size: .8rem; border: 1px solid rgba(255,255,255,.25); padding: 6px 11px; border-radius: 999px; white-space: nowrap; }
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #F7941D; margin-right: 7px; }

.scn-banner { padding: 14px 18px; border-radius: 14px; border: 1px solid var(--border-color); background: var(--secondary-background-color); margin-bottom: 12px; }
.scn-title { font-size: 1.2rem; font-weight: 750; }
.scn-meta { font-size: .82rem; opacity: .75; margin: 2px 0 8px 0; }
.chip { display: inline-block; padding: 3px 10px; margin: 2px 6px 2px 0; border-radius: 999px; background: #EAF5EF; color: #005B2D; font-size: .78rem; font-weight: 650; border: 1px solid #BFE0CC; }
.risk-badge { display: inline-block; padding: 5px 14px; border-radius: 999px; font-weight: 800; font-size: .85rem; letter-spacing: .5px; color: #fff; }
.risk-LOW { background: #007A3D; } .risk-MEDIUM { background: #F7941D; } .risk-HIGH { background: #C0392B; }
.kpi-card { border: 1px solid var(--border-color); border-left: 6px solid #66786D; border-radius: 14px; padding: 12px 14px; background: var(--secondary-background-color); margin-bottom: 10px; min-height: 104px; }
.kpi-card.good { border-left-color: #007A3D; } .kpi-card.bad { border-left-color: #C0392B; } .kpi-card.neutral { border-left-color: #66786D; }
.kpi-label { font-size: .76rem; text-transform: uppercase; letter-spacing: .6px; opacity: .72; font-weight: 650; }
.kpi-value { font-size: 1.55rem; font-weight: 780; line-height: 1.25; }
.kpi-delta { font-size: .82rem; font-weight: 650; } .kpi-delta.good { color: #007A3D; } .kpi-delta.bad { color: #C0392B; } .kpi-delta.neutral { opacity: .7; }
.kpi-hint { font-size: .72rem; opacity: .6; }
.insight-box { padding: 14px 18px; border-radius: 14px; border: 1px solid #BFE0CC; background: linear-gradient(135deg,rgba(0,122,61,.07),rgba(247,148,29,.05)); margin: 8px 0 14px 0; }
.ai-card { padding: 16px; border: 1px solid var(--border-color); border-radius: 14px; background: var(--secondary-background-color); }


/* -------------------------------------------------------------------------- */
/* Modern NBE Sidebar Executive Styling (Approved Clean Style)                */
/* -------------------------------------------------------------------------- */
[data-testid="stSidebar"] {
    background-color: #FFFFFF !important;
    border-right: 1px solid #D2E3D9 !important;
    padding-top: 1.5rem;
}

[data-testid="stSidebar"] > div:first-child {
    background-color: #FFFFFF !important;
}

[data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] h1 {
    color: #173B2A !important;
}

[data-testid="stSidebar"] .stButton > button {
    background-color: #FFFFFF !important;
    color: #173B2A !important;
    border: 1px solid #C8DCD1 !important;
    border-radius: 12px !important;
    font-weight: 600 !important;
    box-shadow: 0 2px 6px rgba(0, 91, 45, 0.03);
    transition: all 0.2s ease;
}

[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #EAF5EF !important;
    border-color: #007A3D !important;
    color: #005B2D !important;
}

[data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #005B2D, #007A3D) !important;
    color: #FFFFFF !important;
    border: none !important;
    font-weight: 700 !important;
    box-shadow: 0 4px 15px rgba(0, 91, 45, 0.25) !important;
}

[data-testid="stSidebar"] .stButton > button[kind="primary"] p,
[data-testid="stSidebar"] .stButton > button[kind="primary"] span {
    color: #FFFFFF !important;
}
</style>
"""





def show_df(df, height=None):
    kwargs = {"use_container_width": True, "hide_index": True}
    if height:
        kwargs["height"] = height
    st.dataframe(df, **kwargs)


def style_nbe_figure(fig, height=None):
    dark = str(st.get_option("theme.base") or "light").lower() == "dark"
    bg = "rgba(0,0,0,0)"
    text_color = "#EAF5EF" if dark else NBE_TEXT
    grid = "#35684D" if dark else "#E1ECE5"
    fig.update_layout(
        template="plotly_dark" if dark else "plotly_white", paper_bgcolor=bg, plot_bgcolor=bg,
        colorway=NBE_PALETTE, font=dict(color=text_color, family=CHART_FONT),
        title_font=dict(color=NBE_ORANGE if dark else NBE_DARK_GREEN, size=16),
        legend=dict(font=dict(color=text_color), orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis=dict(gridcolor=grid, zerolinecolor=grid, linecolor=NBE_GREEN),
        yaxis=dict(gridcolor=grid, zerolinecolor=grid, linecolor=NBE_GREEN),
        margin=CHART_MARGIN, hoverlabel=dict(bgcolor=NBE_DARK_GREEN, font=dict(color="#FFFFFF")),
        height=height or 380,
    )
    return fig


def pct_change(before, after):
    before = float(before or 0)
    after = float(after) if pd.notna(after) else np.nan
    return ((after - before) / abs(before) * 100.0) if before and pd.notna(after) else np.nan



def render_page_header(title, subtitle=None, status="Connected"):
    sub = f'<div class="app-subtitle">{subtitle}</div>' if subtitle else ""
    
    logo_html = ""
    try:
        import base64, os
        if os.path.exists("nbe_logo_w.png"):
            with open("nbe_logo_w.png", "rb") as f:
                encoded_logo = base64.b64encode(f.read()).decode()
            logo_html = f'<img src="data:image/png;base64,{encoded_logo}" class="app-logo" alt="NBE Logo">'
    except Exception:
        pass

    st.markdown(
        f'''<div class="app-header">
            <!-- العنوان والوصف في جهة اليسار -->
            <div class="app-title-container">
                <div class="app-title">{title}</div>
                {sub}
            </div>
            <!-- الشعار وحالة الاتصال في جهة اليمين -->
            <div class="app-left-section">
                <div class="app-status"><span class="status-dot"></span>{status}</div>
                {logo_html}
            </div>
        </div>''',
        unsafe_allow_html=True,
    )


def kpi_card(label, value, delta=None, tone="neutral", hint=""):
    arrow = {"good": "▲ ", "bad": "▼ ", "neutral": ""}[tone] if delta else ""
    delta_html = f'<div class="kpi-delta {tone}">{arrow}{delta}</div>' if delta else '<div class="kpi-delta neutral">&nbsp;</div>'
    hint_html = f'<div class="kpi-hint">{hint}</div>' if hint else ""
    st.markdown(
        f'<div class="kpi-card {tone}"><div class="kpi-label">{label}</div><div class="kpi-value">{value}</div>'
        f'{delta_html}{hint_html}</div>',
        unsafe_allow_html=True,
    )


def tone_for(delta, higher_is_better):
    if higher_is_better is None or delta is None or pd.isna(delta) or abs(delta) < 1e-12:
        return "neutral"
    return "good" if (delta > 0) == bool(higher_is_better) else "bad"


def risk_badge(level):
    return f'<span class="risk-badge risk-{level}">OVERALL RISK: {level}</span>'


def active_driver_chips(params):
    chips = []
    for k, (label, unit, *_rest, _group) in PARAM_SPECS.items():
        if abs(params[k] - SCENARIO_DEFAULTS[k]) > 1e-12:
            chips.append(f'<span class="chip">{label.split(" (")[0]}: {params[k]:+g}{unit}</span>')
    return "".join(chips) or '<span class="chip">Base case - no stress assumptions</span>'


def run_scenario(ctx, params, sim_date, horizon_days):
    params = build_scenario_parameters(params)
    before, after, detail = apply_prepared(ctx.prep, sim_date, int(horizon_days), params)
    risk = build_alm_risk_summary(before, after)
    level, reasons = overall_risk_rating(before, after, risk)
    return SimpleNamespace(
        params=params, sim_date=pd.Timestamp(sim_date), horizon=int(horizon_days),
        before=before, after=after, detail=detail,
        comparison=comparison_table(before, after), risk=risk, risk_level=level, risk_reasons=reasons,
        quality=build_data_quality_checks(before, ctx.positions, ctx.cashflows, ctx.market_data, after, raw=ctx.raw, source_notes=ctx.source_notes),
        validation=calculation_validation_table(before, after, detail, params),
        name="", type="",
    )


def current_session_scenario(ctx):
    res = run_scenario(
        ctx, st.session_state.get("current_params") or {},
        st.session_state.get("current_simulation_date", date.today()),
        st.session_state.get("current_horizon_days", DEFAULT_HORIZON_DAYS),
    )
    res.name = st.session_state.get("current_scenario_name", "Current Session")
    res.type = "Current Scenario"
    return res


def evaluate_saved_scenario(ctx, row):
    sim = pd.to_datetime(row.get("simulation_date"), errors="coerce")
    sim = sim if pd.notna(sim) else pd.Timestamp(date.today())
    horizon = int(row.get("horizon_days") or DEFAULT_HORIZON_DAYS)
    res = run_scenario(ctx, scenario_params_from_row(row), sim, horizon)
    res.name = str(row.get("scenario_name", "Saved scenario"))
    res.type = str(row.get("scenario_type", "SAVED"))
    return res


def _items(params):
    return tuple(sorted((k, round(float(v), 10)) for k, v in params.items()))


@st.cache_data(ttl=DATA_CACHE_TTL_SECONDS, show_spinner=False, max_entries=64)
def cached_sensitivity(_prep, stamp, sim, horizon, params_items, key, values):
    return shock_sensitivity(_prep, sim, horizon, dict(params_items), key, list(values))


@st.cache_data(ttl=DATA_CACHE_TTL_SECONDS, show_spinner=False, max_entries=64)
def cached_attribution(_prep, stamp, sim, horizon, params_items):
    return driver_attribution(_prep, sim, horizon, dict(params_items))


def sensitivity(ctx, res, key, values):
    return cached_sensitivity(ctx.prep, ctx.stamp, str(res.sim_date.date()), res.horizon, _items(res.params), key, tuple(values))


def attribution(ctx, res):
    return cached_attribution(ctx.prep, ctx.stamp, str(res.sim_date.date()), res.horizon, _items(res.params))


def render_ai_scenario_panel(ctx, res, scenario_name, scenario_type, panel_key="scenario", insights=None):
    st.markdown('<div class="ai-card">', unsafe_allow_html=True)
    
    # --- 1. جلب أو إنشاء الـ run_id الحقيقي تلقائياً بناءً على السيناريو الحالي ---
    run_id = None
    if ctx.engine is not None:
        try:
            safe_name = str(scenario_name).replace("'", "''")
            query = f"""
                SELECT sr.run_id 
                FROM simulation_runs sr 
                JOIN scenarios s ON sr.scenario_id = s.scenario_id 
                WHERE s.scenario_name = '{safe_name}' 
                ORDER BY sr.run_id DESC LIMIT 1
            """
            df_id = pd.read_sql(query, ctx.engine)
            if not df_id.empty:
                run_id = int(df_id.iloc[0]["run_id"])
            else:
                res_scen = pd.read_sql(f"SELECT scenario_id FROM scenarios WHERE scenario_name = '{safe_name}'", ctx.engine)
                if not res_scen.empty:
                    scen_id = int(res_scen.iloc[0]["scenario_id"])
                else:
                    with ctx.engine.begin() as conn:
                        res_ins = conn.execute(
                            text("INSERT INTO scenarios (scenario_name, scenario_type, simulation_date, horizon_days) VALUES (:name, :stype, :sdate, :hz)"),
                            {"name": scenario_name, "stype": scenario_type, "sdate": res.sim_date.date(), "hz": res.horizon}
                        )
                        scen_id = int(res_ins.lastrowid)
                
                with ctx.engine.begin() as conn:
                    res_run_ins = conn.execute(
                        text("INSERT INTO simulation_runs (scenario_id, simulation_date, horizon_days, run_status) VALUES (:sid, :sdate, :hz, 'COMPLETED')"),
                        {"sid": scen_id, "sdate": res.sim_date.date(), "hz": res.horizon}
                    )
                    run_id = int(res_run_ins.lastrowid)
        except Exception:
            pass

    st.write(f"Current Run ID: {run_id}")
    st.markdown(f"### 🤖 AI scenario explanation — {scenario_name}")
    st.caption(f"Scenario: {scenario_name} | Type: {scenario_type} | Date: {res.sim_date:%Y-%m-%d} | Horizon: {res.horizon} days")
    st.info("Phi-3 only explains the deterministic calculation. Python remains the source of every number.")
    
    payload = {
        "scenario_name": scenario_name, "scenario_type": scenario_type,
        "simulation_date": str(res.sim_date.date()), "horizon_days": res.horizon, "inputs": res.params,
        "before": {k: v for k, v in res.before.items() if not str(k).startswith("Macro")},
        "after": {k: v for k, v in res.after.items() if not str(k).startswith("Macro")},
        "risk": res.risk.to_dict(orient="records"), "overall_risk": res.risk_level,
        "deterministic_insights": insights or [],
    }

    # --- 2. تخصيص مفتاح فريد لكل سيناريو و run_id لمنع تداخل التفسيرات نهائياً ---
    unique_key = f"{panel_key}_{run_id if run_id is not None else scenario_name}"
    insight_state_key = f"ai_insight_{unique_key}"
    meta_state_key = f"ai_insight_meta_{unique_key}"

    existing_insight = None
    
    # --- 3. جلب التفسير المحفوظ من قاعدة البيانات لهذا الـ run_id تحديداً ---
    if ctx.engine is not None and run_id:
        try:
            query = f"SELECT ai_explanation FROM simulation_runs WHERE run_id = {run_id}"
            df_run = pd.read_sql(query, ctx.engine)
            if not df_run.empty and pd.notna(df_run.iloc[0]["ai_explanation"]):
                val = str(df_run.iloc[0]["ai_explanation"]).strip()
                if val:
                    existing_insight = val
        except Exception:
            pass

    # الأولوية دائماً لما هو محفوظ في قاعدة البيانات لهذا الـ run_id، ثم الذاكرة الخاصة بنفس الـ run_id فقط
    insight = existing_insight or st.session_state.get(insight_state_key)

    # مزامنة المفتاح العام لكي يستخدمه مصدر التقارير PDF/PPTX للسيناريو المعروض حالياً فقط
    st.session_state[f"ai_insight_{panel_key}"] = insight

    if not insight:
        if st.button("Explain this scenario", type="primary", key=f"ai_explain_{unique_key}"):
            with st.spinner("Generating AI explanation via Ollama..."):
                host = st.session_state.get("ollama_host", OLLAMA_HOST)
                model = st.session_state.get("ollama_model", OLLAMA_MODEL)
                insight = ask_ollama(host, model, payload)
                st.session_state[insight_state_key] = insight
                st.session_state[f"ai_insight_{panel_key}"] = insight
                st.session_state[meta_state_key] = {"scenario_name": scenario_name, "model": model}
                
                # --- الحفظ الفوري والتحديث التلقائي في قاعدة البيانات ---
                if ctx.engine is not None and run_id and not str(insight).startswith("Phi-3"):
                    try:
                        with ctx.engine.begin() as conn:
                            conn.execute(
                                text("UPDATE simulation_runs SET ai_explanation = :exp WHERE run_id = :rid"),
                                {"exp": str(insight), "rid": run_id}
                            )
                        st.rerun()
                    except Exception as e:
                        st.error(f"Save error: {e}")

    insight = existing_insight or st.session_state.get(insight_state_key)
    meta = st.session_state.get(meta_state_key, {})
    
    if insight:
        if existing_insight:
            st.success("⚡ Loaded saved AI explanation instantly from database.")
        st.write(insight)
        st.caption(f"Explained scenario: {meta.get('scenario_name', scenario_name)} | Model: {meta.get('model', OLLAMA_MODEL)}")
        
        # زر حفظ يدوي احتياطي يظهر فقط إذا لم يكن محفوظاً في الداتابيز بعد
        if ctx.engine is not None and run_id and not str(insight).startswith("Phi-3") and not existing_insight:
            if st.button("💾 Save explanation to database", key=f"ai_save_{unique_key}"):
                try:
                    with ctx.engine.begin() as conn:
                        conn.execute(
                            text("UPDATE simulation_runs SET ai_explanation = :exp WHERE run_id = :rid"),
                            {"exp": str(insight), "rid": run_id}
                        )
                    st.success("Explanation saved successfully to database!")
                    st.rerun()
                except Exception as err:
                    st.warning(f"Save failed: {err}")
                
    st.markdown('</div>', unsafe_allow_html=True)