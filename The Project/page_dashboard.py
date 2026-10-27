"""Simplified Executive Dashboard: KPI cards, 3 focused tabs, AI explanation, and PDF/PPTX Report Builder."""
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st


from config import *
from calculations import valuation_rates, latest_market_date
from analytics import (
    liquidity_ladder, exposure_tables, position_sensitivity,
    pnl_components, management_insights,build_full_row_by_row_scenario_table,calculation_validation_table,export_row_by_row_with_formulas_xlsx,
)
from ui_common import (
    show_df, style_nbe_figure, format_large_number, kpi_card, tone_for,
    risk_badge, active_driver_chips, current_session_scenario,
    evaluate_saved_scenario, attribution, render_ai_scenario_panel,
)
from reporting import (
    REPORTING_AVAILABLE, REPORTING_MISSING_ERROR,
    generate_pdf_report, generate_pptx_report, generate_zip_bundle,
)


def _fmt_delta(value, unit=""):
    if value is None or pd.isna(value) or abs(value) < 1e-12:
        return None
    sign = "+" if value > 0 else "-"
    return f"{sign}{format_large_number(abs(value))}{unit}" if unit == "" else f"{value:+,.2f}{unit}"


def _kpi_grid(res):
    b, a = res.before, res.after
    nii_pct = ((a["NII"] - b["NII"]) / abs(b["NII"]) * 100) if b["NII"] else np.nan
    cards = [
        ("NII", format_large_number(a["NII"]), _fmt_delta(a["NII"] - b["NII"]),
         tone_for(a["NII"] - b["NII"], True), f"{nii_pct:+.1f}% vs base" if pd.notna(nii_pct) else ""),
        ("NIM", f"{a['NIM %']:.2f}%", _fmt_delta((a["NIM %"] - b["NIM %"]) * 100, " bps"),
         tone_for(a["NIM %"] - b["NIM %"], True), "annualised"),
        ("Cost of funds", f"{a['Cost of Funds %']:.2f}%", _fmt_delta((a["Cost of Funds %"] - b["Cost of Funds %"]) * 100, " bps"),
         tone_for(a["Cost of Funds %"] - b["Cost of Funds %"], False), "annualised"),
        ("Total P&L & value impact", format_large_number(a["PnL Total Impact"]), None,
         tone_for(a["PnL Total Impact"], True), "NII + credit cost + FX/gold + MTM"),
        ("LCR", "N/A" if pd.isna(a["CF LCR %"]) else f"{a['CF LCR %']:,.1f}%",
         None if pd.isna(a["CF LCR %"]) or pd.isna(b["CF LCR %"]) else _fmt_delta(a["CF LCR %"] - b["CF LCR %"], " pp"),
         tone_for(None if pd.isna(a["CF LCR %"]) or pd.isna(b["CF LCR %"]) else a["CF LCR %"] - b["CF LCR %"], True),
         f"HQLA / Deposits"),
        ("Net cash flow", format_large_number(a["CF Net Cashflow"]), _fmt_delta(a["CF Net Cashflow"] - b["CF Net Cashflow"]),
         tone_for(a["CF Net Cashflow"] - b["CF Net Cashflow"], True), f"next {res.horizon} days"),
        ("Loans", format_large_number(a["Loans"]), _fmt_delta(a["Loans"] - b["Loans"]), "neutral", "EGP-equivalent"),
        ("Deposits", format_large_number(a["Deposits"]), _fmt_delta(a["Deposits"] - b["Deposits"]), "neutral", "EGP-equivalent"),
        ("Wholesale funding", format_large_number(a["Funding"]), _fmt_delta(a["Funding"] - b["Funding"]), "neutral", "EGP-equivalent"),
        ("Investments", format_large_number(a["Investments"]), _fmt_delta(a["Investments"] - b["Investments"]), "neutral", "EGP-equivalent"),
        ("Loan / deposit", f"{a['Loan Deposit %']:.1f}%", _fmt_delta(a["Loan Deposit %"] - b["Loan Deposit %"], " pp"), "neutral", ""),
        ("Repricing gap", format_large_number(a["Repricing Gap"]), _fmt_delta(a["Repricing Gap"] - b["Repricing Gap"]), "neutral",
         "assets - liabilities repricing in horizon"),
    ]
    for offset in range(0, len(cards), 4):
        for col, card in zip(st.columns(4), cards[offset:offset + 4]):
            with col:
                kpi_card(*card)


def _before_after_bar(df, x, before_col, after_col, title, height=360):
    long = df.melt(id_vars=x, value_vars=[before_col, after_col], var_name="Stage", value_name="Value")
    long["Stage"] = long["Stage"].str.replace(r" ?(Balance|Interest)", "", regex=True).replace({"": "Value"})
    long["Label"] = long["Value"].apply(format_large_number)
    fig = px.bar(long, x=x, y="Value", color="Stage", barmode="group", text="Label", title=title,
                 color_discrete_sequence=[NBE_GREEN, NBE_ORANGE])
    fig.update_traces(textposition="outside", cliponaxis=False)
    return style_nbe_figure(fig, height)


def _waterfall(labels, values, title, total_label):
    measure = ["relative"] * len(values) + ["total"]
    fig = go.Figure(go.Waterfall(
        x=labels + [total_label], measure=measure, y=list(values) + [sum(values)],
        text=[format_large_number(v) for v in list(values) + [sum(values)]],
        increasing=dict(marker=dict(color=NBE_GREEN)), decreasing=dict(marker=dict(color="#C0392B")),
        totals=dict(marker=dict(color=NBE_DARK_GREEN)),
    ))
    fig.update_layout(title=title)
    return style_nbe_figure(fig)


def page_dashboard(ctx, history):
    res = current_session_scenario(ctx)
    if not history.empty and not ctx.scenarios.empty:
        options = {"Current session scenario": 0}
        for _, r in history.sort_values("scenario_id").iterrows():
            options[f"{r.get('scenario_name', 'Scenario')} (ID {int(r['scenario_id'])})"] = int(r["scenario_id"])
        choice = st.selectbox("Scenario shown on the dashboard", list(options.keys()), key="dashboard_scenario_selector")
        if options[choice] != 0:
            row = ctx.scenarios[pd.to_numeric(ctx.scenarios["scenario_id"]) == options[choice]].iloc[0]
            res = evaluate_saved_scenario(ctx, row)

    if ctx.positions.empty:
        st.warning("No positions loaded. Connect to the database in Data & Settings.")
        return

    # حساب الأسعار بعد الصدمة
    fx_shock = res.params.get("fx_shock_pct", 0.0)
    gold_shock = res.params.get("gold_shock_pct", 0.0)
    base_fx_rate = 48.613
    base_gold_rate = 4220.42
    affected_fx_rate = base_fx_rate * (1.0 + fx_shock / 100.0)
    affected_gold_rate = base_gold_rate * (1.0 + gold_shock / 100.0)

    # تحديد الألوان تلقائياً بناءً على نسبة الزيادة أو النقصان
    fx_color = "#28a745" if fx_shock > 0 else ("#dc3545" if fx_shock < 0 else "#666")
    gold_color = "#28a745" if gold_shock > 0 else ("#dc3545" if gold_shock < 0 else "#666")

    # تصميم البانر العلوي مع تلوين نسب الزيادة والنقصان
    st.markdown(
        f'''<div class="scn-banner">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap: 15px;">
                <div>
                    <div class="scn-title">{res.name}</div>
                    <div class="scn-meta">Simulation date {res.sim_date:%Y-%m-%d} &nbsp;|&nbsp; Horizon {res.horizon} days
                    (to {(res.sim_date + pd.Timedelta(days=res.horizon)):%Y-%m-%d}) &nbsp;|&nbsp; EGP-equivalent</div>
                </div>
                <div style="display:flex; align-items:center; gap: 12px; flex-wrap:wrap;">
                    <!-- مؤشر سعر الدولار -->
                    <div style="background: rgba(255,255,255,0.8); padding: 6px 12px; border-radius: 8px; border: 1px solid #e0e0e0; font-size: 13px; text-align: center;">
                        <span style="color: #555; font-weight: 600;">💱 USD:</span> 
                        <span style="color: #111; font-weight: bold;">{affected_fx_rate:,.2f}</span>
                        <span style="color: {fx_color}; font-size: 11px; font-weight: bold;">({fx_shock:+.1f}%)</span>
                    </div>
                    <!-- مؤشر سعر الذهب -->
                    <div style="background: rgba(255,255,255,0.8); padding: 6px 12px; border-radius: 8px; border: 1px solid #e0e0e0; font-size: 13px; text-align: center;">
                        <span style="color: #555; font-weight: 600;">🪙 Gold (24K):</span> 
                        <span style="color: #111; font-weight: bold;">{affected_gold_rate:,.2f}</span>
                        <span style="color: {gold_color}; font-size: 11px; font-weight: bold;">({gold_shock:+.1f}%)</span>
                    </div>
                    <!-- شارة المخاطر -->
                    <div>{risk_badge(res.risk_level)}</div>
                </div>
            </div>
            {active_driver_chips(res.params)}
        </div>''',
        unsafe_allow_html=True,
    )

    _kpi_grid(res)
    

    attr = attribution(ctx, res)
    insights = management_insights(res.before, res.after, attr)
    st.markdown('<div class="insight-box"><b>💡 Management insight</b></div>', unsafe_allow_html=True)
    for line in insights:
        st.markdown(f"- {line}")

    # Build reusable chart figures for both Dashboard display and PDF/PPTX Export
    b, a = res.before, res.after
    fig_nii_bridge = _waterfall(
        ["Base NII", "Rate effect", "Volume / FX effect"],
        [b["NII"], a["NII Rate Effect"], a["NII Volume Effect"]],
        "NII bridge: base -> scenario (EGP)", "Scenario NII",
    )
    comp = pnl_components(b, a)
    fig_pnl_comp = _waterfall(comp["Component"].tolist(), comp["Impact"].tolist(), "P&L and value impact (EGP)", "Total impact")

    port = pd.DataFrame({
        "Portfolio": ["Loans", "Deposits", "Funding", "Investments"],
        "Before": [b["Loans"], b["Deposits"], b["Funding"], b["Investments"]],
        "After": [a["Loans"], a["Deposits"], a["Funding"], a["Investments"]],
    })
    fig_portfolio = _before_after_bar(port, "Portfolio", "Before", "After", "Portfolio before vs after (EGP)")

    expo = exposure_tables(res.detail)
    fig_currency = _before_after_bar(expo["currency"], "Model Currency", "Before Balance", "After Balance", "Balance by currency (EGP)") if "currency" in expo else None

    # 3 Focused Tabs
    tabs = st.tabs([
        "1. Overview",
        "2. Balances",
        "3. Liquidity",
    ])

    with tabs[0]:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(fig_nii_bridge, use_container_width=True)
        with c2:
            st.plotly_chart(fig_pnl_comp, use_container_width=True)
        if not attr.empty:
            st.markdown("#### Driver Attribution (Standalone vs Combined)")
            show_df(attr)
        st.markdown("#### KPI Comparison Table")
        show_df(res.comparison.drop(columns=["Code","Source"]))
        render_ai_scenario_panel(ctx, res, res.name, res.type, panel_key="dashboard_current", insights=insights)

    with tabs[1]:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(fig_portfolio, use_container_width=True)
        with c2:
            if fig_currency is not None:
                st.plotly_chart(fig_currency, use_container_width=True)


    with tabs[2]:
        # st.markdown("#### Risk Summary")
        # show_df(res.risk.drop(columns=["Source"]))
        lad = liquidity_ladder(ctx.cashflows, res.params, res.sim_date, res.horizon, stressed=True)
        if not lad.empty:
            fig_lad = go.Figure()
            fig_lad.add_bar(x=lad["Bucket"], y=lad["Inflow"], name="Inflow", marker_color=NBE_GREEN)
            fig_lad.add_bar(x=lad["Bucket"], y=-lad["Outflow"], name="Outflow", marker_color=NBE_ORANGE)
            fig_lad.add_trace(go.Scatter(x=lad["Bucket"], y=lad["Cumulative Net"], name="Cumulative net", mode="lines+markers", line=dict(color=NBE_DARK_GREEN, width=3)))
            fig_lad.update_layout(title="Maturity Ladder by Bucket (EGP)", barmode="relative")
            st.plotly_chart(style_nbe_figure(fig_lad, 380), use_container_width=True)


# -------------------------------------------------------------------------
    # عرض الجداول التفصيلية والتحليلية (تضمين جميع الجداول الـ 5 في تبويبات منفصلة)
    # -------------------------------------------------------------------------
    st.markdown("---")
    st.markdown("### Advanced ALM Analytics Tables")
    
    # إنشاء 5 تبويبات (Tabs) لكل الجداول المطلوبة
    tab_row, tab_sens, tab_liq, tab_exp = st.tabs([
        "Row-by-Row Details",
        "Top Position Sensitivity", 
        "Liquidity Ladder", 
        "Exposure Breakdown", 
        # "Calculation Validation"
    ])

    # سحب المتغيرات والتواريخ بشكل آمن لتجنب أي أخطاء AttributeError
    horizon = res.after.get("Horizon Days", 365) if hasattr(res, "after") and isinstance(res.after, dict) else 365
    sim_date = res.after.get("Simulation Date", None) if hasattr(res, "after") and isinstance(res.after, dict) else None

    # 1. تاب الجدول التفصيلي صف بصف (Row-by-Row Full Calculation Table)
    with tab_row:
        st.markdown("#### Detailed Row-by-Row Scenario Impact Analysis")
        st.caption("Detailed breakdown combining view data, scenario parameters, and isolated indicator impacts per position.")
        
        full_row_table = build_full_row_by_row_scenario_table(
            res.detail, res.params, horizon, sim_date=res.sim_date
        )
        st.dataframe(full_row_table, use_container_width=True)

        excel_bytes = export_row_by_row_with_formulas_xlsx(full_row_table)
        st.download_button(
            label="Download Row-by-Row Full Calculation Table (Excel)",
            data=excel_bytes,
            file_name="alm_row_by_row_details_formulas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_row_tab_xlsx_formulas",
        )
        

    # 2. تاب حساسية المراكز العليا (Top Positions Sensitivity)
    with tab_sens:
        st.markdown("#### Top 10 Positions by Total Financial Impact")
        top_pos_df = position_sensitivity(res.detail, top=10)
        st.dataframe(top_pos_df, use_container_width=True)
        
        csv_sens = top_pos_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Download Top Position Sensitivity (CSV)", 
            csv_sens, 
            "top_position_sensitivity.csv", 
            "text/csv", 
            key="dl_sens_tab"
        )

    # 3. تاب سلم السيولة والتدفقات النقدية (Liquidity Ladder)
    with tab_liq:
        st.markdown("#### Maturity Bucket Liquidity Ladder (Stressed Cash Flows)")
        liq_df = liquidity_ladder(ctx.cashflows, res.params, sim_date, horizon, stressed=True)
        st.dataframe(liq_df, use_container_width=True)
        
        csv_liq = liq_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Download Liquidity Ladder (CSV)", 
            csv_liq, 
            "liquidity_ladder.csv", 
            "text/csv", 
            key="dl_liq_tab"
        )

    # 4. تاب التعرضات والتجميعات حسب التصنيف (Exposure Breakdown)
    with tab_exp:
        st.markdown("#### Portfolio Exposure Breakdown by Category")
        exp_dict = exposure_tables(res.detail)
        if exp_dict and "category" in exp_dict and not exp_dict["category"].empty:
            st.dataframe(exp_dict["category"], use_container_width=True)
            csv_exp = exp_dict["category"].to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "Download Exposure Table (CSV)", 
                csv_exp, 
                "exposure_by_category.csv", 
                "text/csv", 
                key="dl_exp_tab"
            )
        else:
            st.info("No exposure data available.")



    # =========================================================================
    # 📄 Export Custom Executive Report (PDF / PPTX / Both)
    # =========================================================================
    # =========================================================================
    # Report Builder Section (PDF / PPTX Export)
    # =========================================================================
    st.markdown("---")
    with st.expander("📄 Export Custom Executive Report (PDF / PPTX / Both)", expanded=False):
        if not REPORTING_AVAILABLE:
            st.warning(f"To enable PDF and PPTX export, install the required packages:\n`pip install reportlab python-pptx kaleido`")
        else:
            st.caption("")
            
            # 1. تعريف البيانات أولاً قبل استخدامهما في الفورم
            kpi_items = [
                {"label": "NII", "before": format_large_number(b["NII"]), "after": format_large_number(a["NII"]), "delta": _fmt_delta(a["NII"] - b["NII"])},
                {"label": "Net Interest Margin (NIM %)", "before": f"{b['NIM %']:.2f}%", "after": f"{a['NIM %']:.2f}%", "delta": _fmt_delta((a['NIM %'] - b['NIM %']) * 100, " bps")},
                {"label": "Cost of Funds %", "before": f"{b['Cost of Funds %']:.2f}%", "after": f"{a['Cost of Funds %']:.2f}%", "delta": _fmt_delta((a['Cost of Funds %'] - b['Cost of Funds %']) * 100, " bps")},
                {"label": "Total P&L & Value Impact", "before": "0.00", "after": format_large_number(a["PnL Total Impact"]), "delta": format_large_number(a["PnL Total Impact"])},
                {"label": "LCR %", "before": f"{b['CF LCR %']:,.1f}%", "after": f"{a['CF LCR %']:,.1f}%", "delta": _fmt_delta(a['CF LCR %'] - b['CF LCR %'], " pp")},
                # {"label": "HQLA to Deposits %", "before": f"{b.get('HQLA to Deposits %', 0):,.1f}%", "after": f"{a.get('HQLA to Deposits %', 0):,.1f}%", "delta": _fmt_delta(a.get("HQLA to Deposits %", 0) - b.get("HQLA to Deposits %", 0), " pp")},
                {"label": "Net Cash Flow", "before": format_large_number(b["CF Net Cashflow"]), "after": format_large_number(a["CF Net Cashflow"]), "delta": _fmt_delta(a["CF Net Cashflow"] - b["CF Net Cashflow"])},
                {"label": "Total Loans", "before": format_large_number(b["Loans"]), "after": format_large_number(a["Loans"]), "delta": _fmt_delta(a["Loans"] - b["Loans"])},
                {"label": "Total Deposits", "before": format_large_number(b["Deposits"]), "after": format_large_number(a["Deposits"]), "delta": _fmt_delta(a["Deposits"] - b["Deposits"])},
            ]
            all_kpi_names = [item["label"] for item in kpi_items]
            available_charts = {
                "NII Bridge (Base vs Scenario)": fig_nii_bridge,
                "P&L and Value Impact Components": fig_pnl_comp,
                "Portfolio Before vs After": fig_portfolio,
            }
            if fig_currency is not None:
                available_charts["Balance by Currency (EGP)"] = fig_currency

            # 2. بناء الفورم بعد توفر المتغيرات
            with st.form(key="report_builder_form"):
                col1, col2 = st.columns(2)
                with col1:
                    chosen_kpis = st.multiselect("1. Choose KPIs to include", options=all_kpi_names, default=all_kpi_names[:6], key="rep_kpis")
                with col2:
                    chosen_charts = st.multiselect("2. Choose Charts to include", options=list(available_charts.keys()), default=list(available_charts.keys()), key="rep_charts")

                o1, o2, o3 = st.columns(3)
                inc_comp = o1.checkbox("Include Comparison Table", value=True, key="rep_inc_comp")
                inc_ai = o2.checkbox("Include Management / AI Insight", value=True, key="rep_inc_ai")
                rep_format = o3.radio("3. Output Format", options=["PDF", "PPTX", "Both (PDF & PPTX)"], horizontal=True, key="rep_fmt")

                submitted = st.form_submit_button("⚙️ Generate Report File(s)", use_container_width=True)

            if submitted:
                with st.spinner("Generating report file(s)..."):
                    selected_kpis_payload = [item for item in kpi_items if item["label"] in chosen_kpis]
                    selected_charts_payload = {name: available_charts[name] for name in chosen_charts}
                    
                    # البحث فقط عن التفسير المحفوظ مسبقاً في قاعدة البيانات أو الذاكرة
                    ai_text = st.session_state.get("ai_insight_dashboard_current")
                    run_id = st.session_state.get("last_run_id")
                    
                    if not ai_text and ctx.engine is not None:
                        try:
                            if run_id:
                                query = f"SELECT ai_explanation FROM simulation_runs WHERE run_id = {run_id}"
                            else:
                                safe_name = str(res.name).replace("'", "''")
                                query = f"""
                                    SELECT sr.ai_explanation 
                                    FROM simulation_runs sr 
                                    JOIN scenarios s ON sr.scenario_id = s.scenario_id 
                                    WHERE s.scenario_name = '{safe_name}' 
                                    ORDER BY sr.run_id DESC LIMIT 1
                                """
                            df_run = pd.read_sql(query, ctx.engine)
                            if not df_run.empty and pd.notna(df_run.iloc[0]["ai_explanation"]):
                                ai_text = df_run.iloc[0]["ai_explanation"]
                        except Exception:
                            pass

                    # إذا لم يكن هناك تفسير محفوظ، نعتمد على الملخص العادي insights ولن يتم تشغيل الـ AI
                    if not ai_text:
                        ai_text = "\n".join(insights)

                    ai_payload = ai_text if inc_ai else None
                    comp_payload = res.comparison.drop(columns=["Code"]) if inc_comp else None

                    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(res.name))
                    base_filename = f"NBE_ALM_Report_{safe_name}_{res.sim_date:%Y%m%d}"

                    pdf_b, pptx_b = None, None
                    if rep_format in ("PDF", "Both (PDF & PPTX)"):
                        pdf_b = generate_pdf_report(
                            res.name, res.type, res.sim_date.strftime("%Y-%m-%d"), res.horizon, 
                            selected_kpis_payload, selected_charts_payload, comp_payload, res.risk, ai_payload, params=res.params
                        )
                    if rep_format in ("PPTX", "Both (PDF & PPTX)"):
                        pptx_b = generate_pptx_report(
                            res.name, res.type, res.sim_date.strftime("%Y-%m-%d"), res.horizon, 
                            selected_kpis_payload, selected_charts_payload, comp_payload, ai_payload, params=res.params
                        )
                    
                    st.session_state["rep_pdf_bytes"] = pdf_b
                    st.session_state["rep_pptx_bytes"] = pptx_b
                    st.session_state["rep_ready_mode"] = rep_format
                    st.session_state["rep_ready_name"] = base_filename
                st.success("Report ready for download!")

            r_mode = st.session_state.get("rep_ready_mode")
            r_name = st.session_state.get("rep_ready_name", "NBE_ALM_Report")
            r_pdf = st.session_state.get("rep_pdf_bytes")
            r_pptx = st.session_state.get("rep_pptx_bytes")

            if r_mode == "PDF" and r_pdf:
                st.download_button("⬇️ Download PDF Report (.pdf)", data=r_pdf, file_name=f"{r_name}.pdf", mime="application/pdf", use_container_width=True)
            elif r_mode == "PPTX" and r_pptx:
                st.download_button("⬇️ Download PowerPoint (.pptx)", data=r_pptx, file_name=f"{r_name}.pptx", mime="application/vnd.openxmlformats-officedocument.presentationml.presentation", use_container_width=True)
            elif r_mode == "Both (PDF & PPTX)" and r_pdf and r_pptx:
                zip_b = generate_zip_bundle(r_pdf, r_pptx, r_name)
                d1, d2, d3 = st.columns(3)
                d1.download_button("📦 Download Both (ZIP)", data=zip_b, file_name=f"{r_name}_Bundle.zip", mime="application/zip", use_container_width=True)
                d2.download_button("⬇️ Download PDF Only", data=r_pdf, file_name=f"{r_name}.pdf", mime="application/pdf", use_container_width=True)
                d3.download_button("⬇️ Download PPTX Only", data=r_pptx, file_name=f"{r_name}.pptx", mime="application/vnd.openxmlformats-officedocument.presentationml.presentation", use_container_width=True)


@st.fragment
def render_report_builder_section(res, b, a, fig_nii_bridge, fig_pnl_comp, fig_portfolio, fig_currency, insights):
    """قسم بناء التقارير مع منع إعادة تحميل الصفحة بالكامل عند الضغط على Generate."""
    st.markdown("---")
    with st.expander("📄 Export Custom Executive Report (PDF / PPTX / Both)", expanded=False):
        if not REPORTING_AVAILABLE:
            st.warning("To enable PDF and PPTX export, install: `pip install reportlab python-pptx kaleido`")
        else:
            st.caption("1️⃣ حدد الـ KPIs والرسوم، 2️⃣ اختر الصيغة، ثم اضغط Generate لتوليد الملف فوراً.")
            
            kpi_items = [
                {"label": "NII", "before": format_large_number(b["NII"]), "after": format_large_number(a["NII"]), "delta": _fmt_delta(a["NII"] - b["NII"])},
                {"label": "Net Interest Margin (NIM %)", "before": f"{b['NIM %']:.2f}%", "after": f"{a['NIM %']:.2f}%", "delta": _fmt_delta((a['NIM %'] - b['NIM %']) * 100, " bps")},
                {"label": "Cost of Funds %", "before": f"{b['Cost of Funds %']:.2f}%", "after": f"{a['Cost of Funds %']:.2f}%", "delta": _fmt_delta((a['Cost of Funds %'] - b['Cost of Funds %']) * 100, " bps")},
                {"label": "Total P&L & Value Impact", "before": "0.00", "after": format_large_number(a["PnL Total Impact"]), "delta": format_large_number(a["PnL Total Impact"])},
                {"label": "LCR % ", "before": f"{b['CF LCR %']:,.1f}%", "after": f"{a['CF LCR %']:,.1f}%", "delta": _fmt_delta(a['CF LCR %'] - b['CF LCR %'], " pp")},
                {"label": "Net Cash Flow", "before": format_large_number(b["CF Net Cashflow"]), "after": format_large_number(a["CF Net Cashflow"]), "delta": _fmt_delta(a["CF Net Cashflow"] - b["CF Net Cashflow"])},
                {"label": "Total Loans", "before": format_large_number(b["Loans"]), "after": format_large_number(a["Loans"]), "delta": _fmt_delta(a["Loans"] - b["Loans"])},
                {"label": "Total Deposits", "before": format_large_number(b["Deposits"]), "after": format_large_number(a["Deposits"]), "delta": _fmt_delta(a["Deposits"] - b["Deposits"])},
            ]
            all_kpi_names = [item["label"] for item in kpi_items]
            available_charts = {
                "NII Bridge (Base vs Scenario)": fig_nii_bridge,
                "P&L and Value Impact Components": fig_pnl_comp,
                "Portfolio Before vs After": fig_portfolio,
            }
            if fig_currency is not None:
                available_charts["Balance by Currency (EGP)"] = fig_currency

            # استخدام Form داخل الـ Fragment يمنع أي تحديث عشوائي
            with st.form(key="report_fragment_form"):
                col1, col2 = st.columns(2)
                with col1:
                    chosen_kpis = st.multiselect("1. Choose KPIs to include", options=all_kpi_names, default=all_kpi_names[:6], key="frag_rep_kpis")
                with col2:
                    chosen_charts = st.multiselect("2. Choose Charts to include", options=list(available_charts.keys()), default=list(available_charts.keys()), key="frag_rep_charts")

                o1, o2, o3 = st.columns(3)
                inc_comp = o1.checkbox("Include Comparison Table", value=True, key="frag_rep_inc_comp")
                inc_ai = o2.checkbox("Include Management / AI Insight", value=True, key="frag_rep_inc_ai")
                rep_format = o3.radio("3. Output Format", options=["PDF", "PPTX", "Both (PDF & PPTX)"], horizontal=True, key="frag_rep_fmt")

                submitted = st.form_submit_button("⚙️ Generate Report File(s)", use_container_width=True)

            if submitted:
                with st.spinner("Generating report file(s)..."):
                    selected_kpis_payload = [item for item in kpi_items if item["label"] in chosen_kpis]
                    selected_charts_payload = {name: available_charts[name] for name in chosen_charts}
                    
                    # البحث فقط عن التفسير المحفوظ مسبقاً في قاعدة البيانات أو الذاكرة
                    ai_text = st.session_state.get("ai_insight_dashboard_current")
                    run_id = st.session_state.get("last_run_id")
                    
                    if not ai_text and ctx.engine is not None:
                        try:
                            if run_id:
                                query = f"SELECT ai_explanation FROM simulation_runs WHERE run_id = {run_id}"
                            else:
                                safe_name = str(res.name).replace("'", "''")
                                query = f"""
                                    SELECT sr.ai_explanation 
                                    FROM simulation_runs sr 
                                    JOIN scenarios s ON sr.scenario_id = s.scenario_id 
                                    WHERE s.scenario_name = '{safe_name}' 
                                    ORDER BY sr.run_id DESC LIMIT 1
                                """
                            df_run = pd.read_sql(query, ctx.engine)
                            if not df_run.empty and pd.notna(df_run.iloc[0]["ai_explanation"]):
                                ai_text = df_run.iloc[0]["ai_explanation"]
                        except Exception:
                            pass

                    # إذا لم يكن هناك تفسير محفوظ، نعتمد على الملخص العادي insights ولن يتم تشغيل الـ AI
                    if not ai_text:
                        ai_text = "\n".join(insights)

                    ai_payload = ai_text if inc_ai else None
                    comp_payload = res.comparison.drop(columns=["Code"]) if inc_comp else None

                    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(res.name))
                    base_filename = f"NBE_ALM_Report_{safe_name}_{res.sim_date:%Y%m%d}"

                    pdf_b, pptx_b = None, None
                    if rep_format in ("PDF", "Both (PDF & PPTX)"):
                        pdf_b = generate_pdf_report(
                            res.name, res.type, res.sim_date.strftime("%Y-%m-%d"), res.horizon, 
                            selected_kpis_payload, selected_charts_payload, comp_payload, res.risk, ai_payload, params=res.params
                        )
                    if rep_format in ("PPTX", "Both (PDF & PPTX)"):
                        pptx_b = generate_pptx_report(
                            res.name, res.type, res.sim_date.strftime("%Y-%m-%d"), res.horizon, 
                            selected_kpis_payload, selected_charts_payload, comp_payload, ai_payload, params=res.params
                        )
                    
                    st.session_state["rep_pdf_bytes"] = pdf_b
                    st.session_state["rep_pptx_bytes"] = pptx_b
                    st.session_state["rep_ready_mode"] = rep_format
                    st.session_state["rep_ready_name"] = base_filename
                st.success("Report ready for download!")

            r_mode = st.session_state.get("frag_ready_mode")
            r_name = st.session_state.get("frag_ready_name", "NBE_ALM_Report")
            r_pdf = st.session_state.get("frag_frag_pdf_bytes" if "frag_frag_pdf_bytes" in st.session_state else "frag_pdf_bytes")
            r_pptx = st.session_state.get("frag_pptx_bytes")

            if r_mode == "PDF" and r_pdf:
                st.download_button("⬇️ Download PDF Report (.pdf)", data=r_pdf, file_name=f"{r_name}.pdf", mime="application/pdf", use_container_width=True, key="dl_pdf_btn")
            elif r_mode == "PPTX" and r_pptx:
                st.download_button("⬇️ Download PowerPoint (.pptx)", data=r_pptx, file_name=f"{r_name}.pptx", mime="application/vnd.openxmlformats-officedocument.presentationml.presentation", use_container_width=True, key="dl_pptx_btn")
            elif r_mode == "Both (PDF & PPTX)" and r_pdf and r_pptx:
                zip_b = generate_zip_bundle(r_pdf, r_pptx, r_name)
                d1, d2, d3 = st.columns(3)
                d1.download_button("📦 Download Both (ZIP)", data=zip_b, file_name=f"{r_name}_Bundle.zip", mime="application/zip", use_container_width=True, key="dl_zip_btn")
                d2.download_button("⬇️ Download PDF Only", data=r_pdf, file_name=f"{r_name}.pdf", mime="application/pdf", use_container_width=True, key="dl_pdf_only_btn")
                d3.download_button("⬇️ Download PPTX Only", data=r_pptx, file_name=f"{r_name}.pptx", mime="application/vnd.openxmlformats-officedocument.presentationml.presentation", use_container_width=True, key="dl_pptx_only_btn")                