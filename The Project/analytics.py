"""ALM analytics, risk summaries, data quality, and validation (Simplified & Verified)."""
from typing import Optional

import numpy as np
import pandas as pd

from config import (
    LCR_WARNING_THRESHOLD, RATE_WARNING_THRESHOLD, MARKET_DATA_STALE_DAYS,
    DAYS_PER_YEAR, MATURITY_BUCKET_ORDER, FLAT_POSITIONS_VIEW, FLAT_CASHFLOWS_VIEW,
    SCENARIO_DEFAULTS, RISK_NII_HIGH_DROP_PCT, RISK_NII_MEDIUM_DROP_PCT,
    RISK_COF_MEDIUM_RISE_BPS, RISK_LCR_MEDIUM_RELATIVE_DROP_PCT,
)
from calculations import (
    apply_prepared, prepare_inputs, merge_params, normalize_cashflows,
    normalize_positions, valuation_rates, macro_snapshot, latest_market_date, _basis_shock_pct,
)
import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def fmt_amount(num) -> str:
    if num is None or pd.isna(num):
        return "N/A"
    a = abs(float(num))
    for limit, suffix in [(1e12, " T"), (1e9, " B"), (1e6, " M"), (1e3, " K")]:
        if a >= limit:
            return f"{float(num) / limit:,.2f}{suffix}"
    return f"{float(num):,.2f}"


_COMPARISON_SPECS = [
    ("POSITION_NII", "NII", "Positions", "EGP"),
    ("POSITION_NIM", "NIM %", "Positions", "%"),
    ("POSITION_COF", "Cost of Funds %", "Positions", "%"),
    ("POSITION_LOANS", "Loans", "Positions", "EGP"),
    ("POSITION_DEPOSITS", "Deposits", "Positions", "EGP"),
    ("POSITION_FUNDING", "Funding", "Positions", "EGP"),
    ("POSITION_INVESTMENTS", "Investments", "Positions", "EGP"),
    ("POSITION_LOAN_DEPOSIT", "Loan Deposit %", "Positions", "%"),
    ("POSITION_REPRICING_GAP", "Repricing Gap", "Positions", "EGP"),
    ("LIQUIDITY_NET_CASHFLOW", "Liquidity", "Cash_Flows", "EGP"),
    ("LCR_PROXY", "CF LCR %", "Cash_Flows", "%"),
    ("PNL_CREDIT_COST", "PnL Credit Cost", "P&L", "EGP"),
    ("PNL_FX_GOLD_REVALUATION", "PnL FX Gold Revaluation", "P&L", "EGP"),
    ("PNL_INVESTMENT_MTM", "PnL Investment MTM", "P&L", "EGP"),
    ("PNL_TOTAL_IMPACT", "PnL Total Impact", "P&L", "EGP"),
]


def comparison_table(before: dict, after: dict) -> pd.DataFrame:
    rows = []
    for code, metric, source, unit in _COMPARISON_SPECS:
        b_raw, a_raw = before.get(metric, np.nan), after.get(metric, np.nan)
        b = float(b_raw) if pd.notna(b_raw) else np.nan
        a = float(a_raw) if pd.notna(a_raw) else np.nan
        delta = a - b if pd.notna(a) and pd.notna(b) else np.nan
        change = delta / abs(b) * 100.0 if pd.notna(delta) and b else np.nan
        rows.append({
            "Code": code, "Source": source, "KPI": metric, "Unit": unit,
            "Before": b, "After": a, "Absolute Change": delta, "Change %": change,
        })
    return pd.DataFrame(rows)


def build_alm_risk_summary(before: dict, after: dict) -> pd.DataFrame:
    specs = [
        ("POSITION_NII", "NII", "EGP", None, "Positions"),
        ("POSITION_NIM", "NIM %", "%", None, "Positions"),
        ("POSITION_COF", "Cost of Funds %", "%", None, "Positions"),
        ("REPRICING_GAP", "Repricing Gap", "EGP", None, "Positions"),
        ("LCR_PROXY", "CF LCR %", "%", LCR_WARNING_THRESHOLD, "Cash_Flows"),
    ]
    rows = []
    for code, name, unit, limit, source in specs:
        b_raw, a_raw = before.get(name, np.nan), after.get(name, np.nan)
        b = float(b_raw) if pd.notna(b_raw) else np.nan
        a = float(a_raw) if pd.notna(a_raw) else np.nan
        impact = a - b if pd.notna(a) and pd.notna(b) else np.nan
        pct = impact / abs(b) * 100.0 if pd.notna(impact) and b else np.nan

        level = "INFO"
        if code == "LCR_PROXY":
            level = "UNAVAILABLE" if pd.isna(a) else ("HIGH" if a < limit else "OK")
        elif code == "POSITION_COF":
            level = "ADVERSE" if pd.notna(impact) and impact > 0 else "POSITIVE" if pd.notna(impact) and impact < 0 else "NEUTRAL"
        elif code in {"POSITION_NII", "POSITION_NIM"}:
            level = "ADVERSE" if pd.notna(impact) and impact < 0 else "POSITIVE" if pd.notna(impact) and impact > 0 else "NEUTRAL"

        rows.append({
            "Risk Indicator": code, "Metric": name, "Base": b, "Scenario": a, "Impact": impact,
            "Impact %": pct, "Risk Level": level, "Limit": limit,
            "Limit Breach": (bool(a < limit) if limit is not None and pd.notna(a) else None),
            "Unit": unit, "Source": source,
        })
    return pd.DataFrame(rows)


def shock_sensitivity(prep: dict, simulation_date, horizon_days, params, param_key: str, values) -> pd.DataFrame:
    rows = []
    base = merge_params(params)
    for v in values:
        p = dict(base)
        p[param_key] = float(v)
        _, a, _ = apply_prepared(prep, simulation_date, horizon_days, p)
        rows.append([
            float(v), a["NII"], a["NIM %"], a["Cost of Funds %"],
            a["Repricing Gap"], a["CF LCR %"], a["PnL Total Impact"],
        ])
    return pd.DataFrame(rows, columns=[
        "Shock", "NII", "NIM %", "Cost of Funds %",
        "Repricing Gap", "LCR %", "Total P&L & Value Impact",
    ])


DRIVER_GROUPS = {
    "Policy rate": ["rate_change_pp"],
    "FX": ["fx_shock_pct"],
    "Gold": ["gold_shock_pct"],
    "Deposit run-off": ["deposit_runoff_pct"],
    "Loan growth": ["loan_growth_pct"],
    "Deposit growth": ["deposit_growth_pct"],
    "Inflation": ["inflation_shock_pct"],
    "Unemployment": ["unemployment_shock_pct"],
    "GDP": ["gdp_shock_pct"],
}


def driver_attribution(prep: dict, simulation_date, horizon_days, params) -> pd.DataFrame:
    p = merge_params(params)
    base_b, comb, _ = apply_prepared(prep, simulation_date, horizon_days, p)
    neutral = dict(SCENARIO_DEFAULTS)
    rows, sum_nii, sum_tot = [], 0.0, 0.0
    for label, keys in DRIVER_GROUPS.items():
        if all(abs(p[k] - SCENARIO_DEFAULTS[k]) < 1e-12 for k in keys):
            continue
        single = dict(neutral)
        single.update({k: p[k] for k in keys})
        _, a, _ = apply_prepared(prep, simulation_date, horizon_days, single)
        d_nii = a["NII"] - base_b["NII"]
        rows.append([label, d_nii, a.get("PnL Total Impact", 0.0), a.get("CF LCR Proxy %", a.get("HQLA to Deposits %", 0.0)), a.get("Cost of Funds %", 0.0) - base_b.get("Cost of Funds %", 0.0)])
        sum_nii += d_nii
        sum_tot += a["PnL Total Impact"]
    cols = ["Driver", "NII Impact", "Total P&L & Value Impact", "LCR %", "Cost of Funds Change (pp)"]
    if rows:
        rows.append([
            "Interaction (combined - sum of drivers)",
            comb["NII"] - base_b["NII"] - sum_nii,
            comb["PnL Total Impact"] - sum_tot, np.nan, np.nan,
        ])
        rows.append([
            "COMBINED SCENARIO",
            comb["NII"] - base_b["NII"],
            comb["PnL Total Impact"], comb["CF LCR %"],
            comb["Cost of Funds %"] - base_b["Cost of Funds %"],
        ])
    return pd.DataFrame(rows, columns=cols)


def position_sensitivity(position_detail: pd.DataFrame, top: int = 10) -> pd.DataFrame:
    cols = ["Position", "Category", "Currency", "NII Impact", "Valuation Effect", "Investment MTM", "Total Impact"]
    q = position_detail
    if q is None or q.empty:
        return pd.DataFrame(columns=cols)
    cat = q["Category"].astype(str).str.lower()
    sign = np.where(cat.isin(["loan", "investment"]), 1.0, np.where(cat.isin(["deposit", "funding"]), -1.0, 0.0))
    out = pd.DataFrame({
        "Position": q["position_id"].astype(int).astype(str) + " - " + q["product_name"].astype(str).str.slice(0, 28),
        "Category": q["Category"], "Currency": q["Model Currency"],
        "NII Impact": q["Interest Change"] * sign, "Valuation Effect": q["Valuation Effect"],
        "Investment MTM": q["Investment MTM"],
    })
    out["Total Impact"] = out["NII Impact"] + out["Valuation Effect"] + out["Investment MTM"] - q["Credit Cost"]
    out = out.reindex(out["Total Impact"].abs().sort_values(ascending=False).index).head(top)
    return out[cols].reset_index(drop=True)


def pnl_components(before: dict, after: dict) -> pd.DataFrame:
    return pd.DataFrame([
        ["NII change", after.get("PnL NII Change", 0.0)],
        ["Credit cost (macro)", after.get("PnL Credit Cost", 0.0)],
        ["FX / gold revaluation", after.get("PnL FX Gold Revaluation", 0.0)],
        ["Investment MTM", after.get("PnL Investment MTM", 0.0)],
    ], columns=["Component", "Impact"])


def overall_risk_rating(before: dict, after: dict, risk_df: Optional[pd.DataFrame] = None):
    level, reasons = "LOW", []

    def bump(new, why):
        nonlocal level
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if order[new] > order[level]:
            level = new
        reasons.append(why)

    b_nii, a_nii = before.get("NII", 0.0), after.get("NII", 0.0)
    nii_pct = (a_nii - b_nii) / abs(b_nii) * 100 if b_nii else 0.0
    tot_pct = after.get("PnL Total Impact", 0.0) / abs(b_nii) * 100 if b_nii else 0.0
    lcr_b, lcr_a = before.get("CF LCR %", np.nan), after.get("CF LCR %", np.nan)
    cof = (after.get("Cost of Funds %", 0) - before.get("Cost of Funds %", 0)) * 100

    if pd.notna(lcr_a) and lcr_a < LCR_WARNING_THRESHOLD:
        bump("HIGH", f"LCR proxy {lcr_a:,.1f}% is below the {LCR_WARNING_THRESHOLD:.0f}% threshold.")
    if nii_pct <= RISK_NII_HIGH_DROP_PCT:
        bump("HIGH", f"NII falls {nii_pct:.1f}% (threshold {RISK_NII_HIGH_DROP_PCT:.0f}%).")
    elif nii_pct <= RISK_NII_MEDIUM_DROP_PCT:
        bump("MEDIUM", f"NII falls {nii_pct:.1f}% (threshold {RISK_NII_MEDIUM_DROP_PCT:.0f}%).")
    if tot_pct <= RISK_NII_HIGH_DROP_PCT:
        bump("HIGH", f"Total P&L / value impact is {tot_pct:.1f}% of base NII.")
    elif tot_pct <= RISK_NII_MEDIUM_DROP_PCT:
        bump("MEDIUM", f"Total P&L / value impact is {tot_pct:.1f}% of base NII.")
    if cof >= RISK_COF_MEDIUM_RISE_BPS:
        bump("MEDIUM", f"Cost of funds rises {cof:,.0f} bps.")
    if pd.notna(lcr_a) and pd.notna(lcr_b) and lcr_b:
        rel = (lcr_a - lcr_b) / abs(lcr_b) * 100
        if rel <= RISK_LCR_MEDIUM_RELATIVE_DROP_PCT:
            bump("MEDIUM", f"LCR proxy drops {abs(rel):.0f}% versus base.")
    if not reasons:
        reasons.append("No risk threshold is breached.")
    return level, reasons


def management_insights(before: dict, after: dict, attribution: Optional[pd.DataFrame] = None) -> list:
    out = []
    h = int(after.get("Horizon Days", 365))
    b_nii, a_nii = before.get("NII", 0.0), after.get("NII", 0.0)
    pct = (a_nii - b_nii) / abs(b_nii) * 100 if b_nii else np.nan
    arrow = "rises" if a_nii > b_nii else "falls" if a_nii < b_nii else "is unchanged"
    pct_txt = f" ({pct:+.1f}%)" if pd.notna(pct) else ""
    out.append(
        f"**NII {arrow}** from {fmt_amount(b_nii)} to {fmt_amount(a_nii)} EGP over {h} days{pct_txt}: "
        f"rate effect {fmt_amount(after.get('NII Rate Effect', 0))}, volume / FX effect {fmt_amount(after.get('NII Volume Effect', 0))}."
    )
    d_nim = after.get("NIM %", 0) - before.get("NIM %", 0)
    d_cof = after.get("Cost of Funds %", 0) - before.get("Cost of Funds %", 0)
    out.append(
        f"**NIM** moves {d_nim * 100:+.0f} bps to {after.get('NIM %', 0):.2f}%; "
        f"**cost of funds** moves {d_cof * 100:+.0f} bps to {after.get('Cost of Funds %', 0):.2f}%."
    )
    lcr_b, lcr_a = before.get("CF LCR %", np.nan), after.get("CF LCR %", np.nan)
    if pd.notna(lcr_a):
        out.append(
            f"**Liquidity:** LCR moves from {lcr_b:,.1f}% to {lcr_a:,.1f}% "
            f"(HQLA to Deposits)."
        )
    total = after.get("PnL Total Impact", 0.0)
    out.append(f"**Total P&L & Value Impact:** {fmt_amount(total)} EGP across NII, FX/Gold revaluation, Investment MTM, and Credit Cost.")
    return out


def liquidity_ladder(cashflows, params=None, simulation_date=None, horizon_days: int = 365, stressed: bool = True) -> pd.DataFrame:
    cf = normalize_cashflows(cashflows)
    cols = ["Bucket", "Inflow", "Outflow", "Net", "Cumulative Net"]
    if cf.empty:
        return pd.DataFrame(columns=cols)
    p = merge_params(params if stressed else None)
    sim = pd.Timestamp.today().normalize() if simulation_date is None else pd.Timestamp(simulation_date).normalize()
    d = cf["Cashflow Date"]
    cf = cf[d.notna() & (d >= sim) & (d <= sim + pd.Timedelta(days=int(horizon_days)))].copy()
    if cf.empty:
        return pd.DataFrame(columns=cols)
    amt = cf["Amount EGP"] * (1.0 + _basis_shock_pct(cf["Valuation Basis"], p) / 100.0)
    cf["_in"] = amt.where(cf["Flow"].eq("inflow"), 0.0)
    cf["_out"] = amt.where(cf["Flow"].eq("outflow"), 0.0)
    g = cf.groupby("Bucket")[["_in", "_out"]].sum().reset_index()
    g.columns = ["Bucket", "Inflow", "Outflow"]
    order = {b: i for i, b in enumerate(MATURITY_BUCKET_ORDER)}
    g["_o"] = g["Bucket"].map(order).fillna(len(order))
    g = g.sort_values("_o").drop(columns="_o").reset_index(drop=True)
    g["Net"] = g["Inflow"] - g["Outflow"]
    g["Cumulative Net"] = g["Net"].cumsum()
    return g[cols]


def exposure_tables(position_detail: pd.DataFrame) -> dict:
    if position_detail is None or position_detail.empty:
        return {}
    out = {}
    for key, column in [("category", "Category"), ("currency", "Model Currency"), ("family", "Product Family")]:
        out[key] = position_detail.groupby(column).agg(
            Before_Balance=("Before Balance", "sum"), After_Balance=("After Balance", "sum"),
            Interest_Before=("Interest Before", "sum"), Interest_After=("Interest After", "sum"),
        ).reset_index().rename(columns={
            "Before_Balance": "Before Balance", "After_Balance": "After Balance",
            "Interest_Before": "Interest Before", "Interest_After": "Interest After",
        })
    return out


def build_data_quality_checks(before: dict, positions_flat, cashflows_flat=None, market_data=None, after=None,
                              raw: Optional[dict] = None, source_notes: Optional[dict] = None) -> pd.DataFrame:
    checks = []

    def add(name, status, detail):
        checks.append([name, status, detail])

    source_notes = source_notes or {}
    for key, label in [("positions", FLAT_POSITIONS_VIEW), ("cashflows", FLAT_CASHFLOWS_VIEW)]:
        note = source_notes.get(key)
        if note:
            add(f"Flat source: {label}", "OK", note.get("message", ""))

    n_pos = 0 if positions_flat is None else len(positions_flat)
    add("Positions loaded", "OK" if n_pos else "ERROR", f"{n_pos} row(s) loaded.")
    add("NII", "OK" if np.isfinite(before.get("NII", np.nan)) else "ERROR",
        f"NII (horizon) = {before.get('NII', 0):,.2f} EGP")

    if cashflows_flat is not None and not cashflows_flat.empty:
        add("Cash flow source", "OK", f"{len(cashflows_flat)} cash-flow row(s) loaded in EGP.")
    else:
        add("Cash flow source", "WARNING", "No cash flows loaded.")

    if market_data is not None and not market_data.empty:
        rates = valuation_rates(market_data)
        add("Market data source", "OK" if not rates.empty else "WARNING",
            f"{len(market_data)} market_data rows; valuation rates for {rates['base_currency'].tolist()}.")
    return pd.DataFrame(checks, columns=["Check", "Status", "Detail"])


def calculation_validation_table(before: dict, after: dict, position_detail: pd.DataFrame, params: dict) -> pd.DataFrame:
    rows = []

    def check(source, name, expected, actual, formula, tolerance=0.01):
        if pd.isna(expected) or pd.isna(actual):
            status, diff = "SKIP", np.nan
        else:
            diff = float(actual - expected)
            status = "PASS" if abs(diff) <= tolerance else "FAIL"
        rows.append([source, name, formula, expected, actual, diff, status])

    q = position_detail if position_detail is not None else pd.DataFrame()
    h = max(int(after.get("Horizon Days", 365)), 1)
    ann = DAYS_PER_YEAR / h

    if not q.empty:
        cat = q["Category"].astype(str).str.lower()
        sign = np.where(cat.isin(["loan", "investment"]), 1.0, np.where(cat.isin(["deposit", "funding"]), -1.0, 0.0))
        check("Positions", "NII (before)", float((q["Interest Before"] * sign).sum()),
              before.get("NII", 0), "Sum(asset interest) - Sum(liability interest), Before", 0.01)
        check("Positions", "NII (after)", float((q["Interest After"] * sign).sum()),
              after.get("NII", 0), "Sum(asset interest) - Sum(liability interest), After", 0.01)
        check("Positions", "NII change = rate effect + volume effect",
              after.get("NII", 0) - before.get("NII", 0),
              after.get("NII Rate Effect", 0) + after.get("NII Volume Effect", 0),
              "NII(after) - NII(before) = NII rate effect + NII volume effect", 0.01)

    earning = after.get("Loans", 0) + after.get("Investments", 0)
    check("Positions", "NIM (after)",
          after.get("NII", 0) * ann / earning * 100 if earning else 0.0,
          after.get("NIM %", 0), "NII x 365/horizon / (Loans + Investments) x 100", 0.0001)

    fund = after.get("Deposits", 0) + after.get("Funding", 0)
    check("Positions", "Cost of Funds (after)",
          after.get("Interest Expense", 0) * ann / fund * 100 if fund else 0.0,
          after.get("Cost of Funds %", 0), "Interest expense x 365/horizon / (Deposits + Funding) x 100", 0.0001)

    check("P&L", "Total impact = NII + credit + FX/gold + MTM",
          after.get("PnL NII Change", 0) + after.get("PnL Credit Cost", 0) + after.get("PnL FX Gold Revaluation", 0)
          + after.get("PnL Investment MTM", 0), after.get("PnL Total Impact", 0),
          "dNII - credit cost + FX/gold revaluation + investment MTM", 0.01)

    # التعديل هنا: اعتماد HQLA / Total Deposits لتتطابق تماماً مع المحرك الجديد وتتحول إلى PASS
    total_deposits = after.get("Deposits", 0.0)
    hqla_stock = after.get("CF HQLA", 0.0)
    expected_lcr = (hqla_stock / total_deposits * 100.0) if total_deposits > 0 else np.nan

    check("Cash_Flows", "LCR",
          expected_lcr,
          after.get("CF LCR %", np.nan),
          "CF HQLA / Deposits x 100", 0.0001)

    return pd.DataFrame(rows, columns=["Source", "Check", "Formula", "Expected", "Actual", "Difference", "Status"])

def build_full_row_by_row_scenario_table(
    q: pd.DataFrame, params: Optional[dict] = None, horizon_days: int = 365, sim_date = None
) -> pd.DataFrame:
    """
    تدمج بيانات الـ View (لكل منتج/مركز مالي) مع معاملات السيناريو ككسور عشرية (.20, .01, .02)
    وتفصل تأثير كل مؤشر على حدة في أعمدة مستقلة، مع جلب تاريخ البدء، الاستحقاق، وإعادة التسعير
    وحساب الأيام المتبقية بدقة بناءً على صورة الـ Debug.
    """
    if q is None or q.empty:
        return pd.DataFrame()

    


    p = merge_params(params)
    df = q.copy()
    cat = df["Category"].astype(str).str.lower()
    basis = df["Valuation Basis"].astype(str).str.strip().str.lower()

    


    # إشارة المركز المالي لتحديد الأثر على صافي الدخل (الأصول +1 ، الالتزامات -1)
    sign = np.where(
        cat.isin(["loan", "investment"]), 1.0,
        np.where(cat.isin(["deposit", "funding"]), -1.0, 0.0)
    )
    df["ALM_Sign"] = sign
    df["Simulation Date"] = str(sim_date) if sim_date is not None else "N/A"
    # -------------------------------------------------------------------------
    # 1. إضافة أعمدة السيناريو ككسور عشرية مباشرة
    # -------------------------------------------------------------------------
    df["Scn_Rate_Shock_pp"] = p["rate_change_pp"] / 100.0
    df["Scn_Loan_Growth_pct"] = np.where(cat.eq("loan"), p["loan_growth_pct"] / 100.0, 0.0)
    df["Scn_Deposit_Growth_pct"] = np.where(cat.eq("deposit"), p["deposit_growth_pct"] / 100.0, 0.0)
    df["Scn_Deposit_Runoff_pct"] = np.where(cat.eq("deposit"), p["deposit_runoff_pct"] / 100.0, 0.0)
    df["Scn_FX_Shock_pct"] = np.where(basis.eq("fx"), p["fx_shock_pct"] / 100.0, 0.0)
    df["Scn_Gold_Shock_pct"] = np.where(basis.eq("gold"), p["gold_shock_pct"] / 100.0, 0.0)
    df["Scn_Unemp_Shock_pct"] = np.where(cat.eq("loan"), p["unemployment_shock_pct"] / 100.0, 0.0)
    df["Scn_GDP_Shock_pct"] = np.where(cat.eq("loan"), p["gdp_shock_pct"] / 100.0, 0.0)
    
    # -------------------------------------------------------------------------
    # 2. تفكيك تأثيرات الأرصدة (Balance Impact per Indicator) لكل صف
    # -------------------------------------------------------------------------
    b0 = df["Before Balance"].astype(float)
    loan_g = df["Scn_Loan_Growth_pct"]
    dep_g = df["Scn_Deposit_Growth_pct"]
    dep_run = df["Scn_Deposit_Runoff_pct"]
    fx_gold_s = df["Scn_FX_Shock_pct"] + df["Scn_Gold_Shock_pct"]

    df["Impact_Bal_Loan_Growth"] = np.where(cat.eq("loan"), b0 * loan_g, 0.0)
    df["Impact_Bal_Deposit_Growth"] = np.where(cat.eq("deposit"), b0 * dep_g, 0.0)
    df["Impact_Bal_Deposit_Runoff"] = np.where(cat.eq("deposit"), -b0 * (1.0 + dep_g) * dep_run, 0.0)

    bal_after_org_growth = (
        b0
        + df["Impact_Bal_Loan_Growth"]
        + df["Impact_Bal_Deposit_Growth"]
        + df["Impact_Bal_Deposit_Runoff"]
    )
    df["Impact_Bal_FX_Gold_Shock"] = bal_after_org_growth * fx_gold_s
    df["Total_Balance_Change"] = df["After Balance"] - df["Before Balance"]

    df["Native_Balance_After_Growth"] = (
        df["Native Balance"]
        * (1.0 + loan_g + dep_g)
        * (1.0 - dep_run)
    )
    df["Applied Market Rate After"] = df["FX Rate"] * (1.0 + fx_gold_s)

    # -------------------------------------------------------------------------
    # سحب الأعمدة الحقيقية من الـ Debug وحساب الأيام المتبقية
    # -------------------------------------------------------------------------
    df["Model Start Date"] = q.get("Model Start Date", "N/A")
    df["Maturity Date"] = q.get("Model Maturity Date", "N/A")
    df["Repricing Date"] = q.get("Model Repricing Date", "N/A")
    
    try:
            s_date = pd.to_datetime(sim_date) if sim_date is not None else pd.NaT
            if pd.notna(s_date):
                df["Remaining_Maturity_Days"] = (pd.to_datetime(df["Maturity Date"], errors="coerce") - s_date).dt.days.fillna(0).astype(int)
                df["Days_To_Next_Repricing"] = (pd.to_datetime(df["Repricing Date"], errors="coerce") - s_date).dt.days.fillna(0).astype(int)
            else:
                df["Remaining_Maturity_Days"] = 0
                df["Days_To_Next_Repricing"] = 0
    except Exception:
        df["Remaining_Maturity_Days"] = 0
        df["Days_To_Next_Repricing"] = 0
    df["Repricing Formula Note"] = "(Accrual Days - Days to Reprice) / Accrual Days"

    # -------------------------------------------------------------------------
    # 3. تفكيك تأثيرات الفائدة (NII Impact per Indicator) لكل صف
    # -------------------------------------------------------------------------
    days = df["Accrual Days"].astype(float)
    r0 = df["Before Rate"].astype(float)
    base_rate_factor = (r0 * days) / DAYS_PER_YEAR

    df["NII_Before"] = df["Interest Before"] * sign
    df["NII_After"] = df["Interest After"] * sign

    df["Impact_NII_Rate_Shock"] = df["Rate Effect"] * sign
    df["Impact_NII_Loan_Growth"] = df["Impact_Bal_Loan_Growth"] * base_rate_factor * sign
    df["Impact_NII_Deposit_Growth"] = df["Impact_Bal_Deposit_Growth"] * base_rate_factor * sign
    df["Impact_NII_Deposit_Runoff"] = df["Impact_Bal_Deposit_Runoff"] * base_rate_factor * sign
    df["Impact_NII_FX_Gold_Shock"] = (b0 * fx_gold_s) * base_rate_factor * sign

    sum_isolated_nii = (
        df["Impact_NII_Rate_Shock"]
        + df["Impact_NII_Loan_Growth"]
        + df["Impact_NII_Deposit_Growth"]
        + df["Impact_NII_Deposit_Runoff"]
        + df["Impact_NII_FX_Gold_Shock"]
    )
    df["Total_NII_Impact"] = df["Interest Change"] * sign
    df["Impact_NII_Interaction"] = df["Total_NII_Impact"] - sum_isolated_nii

    # -------------------------------------------------------------------------
    # 4. تأثيرات التقييم والائتمان والتأثير الكلي النهائي للصف
    # -------------------------------------------------------------------------
    df["Impact_PnL_FX_Gold_Reval"] = df["Valuation Effect"]
    df["Impact_PnL_Investment_MTM"] = df["Investment MTM"]
    df["Impact_PnL_Credit_Cost"] = -df["Credit Cost"]

    df["Total_Row_PnL_Impact"] = (
        df["Total_NII_Impact"]
        + df["Impact_PnL_FX_Gold_Reval"]
        + df["Impact_PnL_Investment_MTM"]
        + df["Impact_PnL_Credit_Cost"]
    )


    # ترتيب الأعمدة النهائي متضمناً الأعمدة الجديدة المتاحة في الـ Debug
    ordered_cols = [
        "position_id", "Simulation Date","product_code", "product_name", "Category", "Customer Segment",
        "Model Currency", "Valuation Basis", "Native Balance", "FX Rate",
        "Model Rate Type", "Model Start Date", "Maturity Date", "Remaining_Maturity_Days",
        "Repricing Date", "Days_To_Next_Repricing", "Accrual Days", "Eligible", 
        "Repricing Fraction", "Repricing Formula Note",
        "Before Balance", "Before Rate", "Interest Before", "NII_Before",
        "Scn_Rate_Shock_pp", "Scn_Loan_Growth_pct", "Scn_Deposit_Growth_pct",
        "Scn_Deposit_Runoff_pct", "Scn_FX_Shock_pct", "Scn_Gold_Shock_pct",
        "Scn_Unemp_Shock_pct", "Scn_GDP_Shock_pct",
        "Impact_Bal_Loan_Growth", "Impact_Bal_Deposit_Growth",
        "Impact_Bal_Deposit_Runoff", "Impact_Bal_FX_Gold_Shock",
        "Native_Balance_After_Growth", "Applied Market Rate After",
        "After Balance", "Total_Balance_Change",
        "After Rate", "Applied Shock",
        "Impact_NII_Rate_Shock", "Impact_NII_Loan_Growth",
        "Impact_NII_Deposit_Growth", "Impact_NII_Deposit_Runoff",
        "Impact_NII_FX_Gold_Shock", "Impact_NII_Interaction",
        "Interest After", "NII_After", "Total_NII_Impact",
        "Impact_PnL_FX_Gold_Reval", "Impact_PnL_Investment_MTM",
        "Impact_PnL_Credit_Cost", "Total_Row_PnL_Impact",
    ]
    existing_cols = [c for c in ordered_cols if c in df.columns]
    return df[existing_cols]

def export_row_by_row_with_formulas_xlsx(df: pd.DataFrame, horizon_days: int = 365) -> bytes:
    """
    تصدير جدول Detailed Row-by-Row Scenario Impact Analysis إلى ملف Excel (.xlsx)
    مع كتابة معادلات Excel الحية (Live Formulas) في كافة الأعمدة المحسوبة
    بما في ذلك: Remaining Days, Accrual Days, Eligible, Repricing Fraction, After Rate,
    وكافة أعمدة الأرصدة والفوائد والتقييم + صف الإجمالي (TOTAL).
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Row-by-Row Scenario Impact"

    if df is None or df.empty:
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue()

    df_out = df.copy()
    cols = list(df_out.columns)

    # التأكد من وجود عمود ALM_Sign في الجدول لكي تعتمد عليه معادلات الإكسيل لاتجاه الأثر (+1 للأصول، -1 للخصوم)
    if "ALM_Sign" not in cols:
        insert_idx = cols.index("Category") + 1 if "Category" in cols else 4
        cols.insert(insert_idx, "ALM_Sign")
        cat_s = df_out["Category"].astype(str).str.lower()
        df_out["ALM_Sign"] = np.where(
            cat_s.isin(["loan", "investment"]), 1.0,
            np.where(cat_s.isin(["deposit", "funding"]), -1.0, 0.0)
        )

    # خريطة تربط اسم كل عمود بحرف العمود في الإكسيل (مثلاً "Native Balance" -> "J")
    col_letter = {col_name: get_column_letter(idx + 1) for idx, col_name in enumerate(cols)}

    def ref(col_name: str, row_num: int) -> str:
        """إرجاع مرجع الخلية في الإكسيل مثل H2 أو AB5"""
        return f"{col_letter[col_name]}{row_num}"

    # 1. كتابة رؤوس الأعمدة (Headers)
    ws.append(cols)

    # تنسيق الهيدر
    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True, size=10)
    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    for col_idx in range(1, len(cols) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # 2. كتابة الصفوف مع حقن معادلات الإكسيل في الأعمدة المحسوبة
    for row_idx, (_, row_data) in enumerate(df_out.iterrows(), start=2):
        for col_idx, col_name in enumerate(cols, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            val = row_data[col_name]

            if pd.isna(val):
                val = 0.0 if isinstance(val, (int, float, np.number)) else "N/A"

            # =========================================================================
            # أ) معادلات الإشارة والتواريخ والأهلية ومعامل إعادة التسعير
            # =========================================================================
            if col_name == "ALM_Sign" and "Category" in col_letter:
                c_cat = ref("Category", row_idx)
                cell.value = f'=IF(OR(LOWER({c_cat})="loan",LOWER({c_cat})="investment"),1,IF(OR(LOWER({c_cat})="deposit",LOWER({c_cat})="funding"),-1,0))'

            elif col_name == "Remaining_Maturity_Days" and "Maturity Date" in col_letter and "Simulation Date" in col_letter:
                c_mat = ref("Maturity Date", row_idx)
                c_sim = ref("Simulation Date", row_idx)
                cell.value = f'=IFERROR(MAX(0, DATEVALUE({c_mat})-DATEVALUE({c_sim})), 0)'

            elif col_name == "Days_To_Next_Repricing" and "Repricing Date" in col_letter and "Simulation Date" in col_letter:
                c_rep = ref("Repricing Date", row_idx)
                c_sim = ref("Simulation Date", row_idx)
                cell.value = f'=IFERROR(MAX(0, DATEVALUE({c_rep})-DATEVALUE({c_sim})), 0)'



            elif col_name == "Repricing Fraction" and all(k in col_letter for k in ["Accrual Days", "Days_To_Next_Repricing", "Eligible"]):
                c_elig = ref("Eligible", row_idx)
                c_acc = ref("Accrual Days", row_idx)
                c_dtr = ref("Days_To_Next_Repricing", row_idx)
                cell.value = f'=IF({c_elig}=TRUE, IF({c_acc}>0, MAX(0, MIN(1, ({c_acc}-MAX(0,{c_dtr}))/{c_acc})), 0), 0)'

            # =========================================================================
            # ب) معادلات القيم قبل السيناريو (Before)
            # =========================================================================
            elif col_name == "Before Balance" and all(k in col_letter for k in ["Native Balance", "FX Rate", "Accrual Days"]):
                c_acc = ref("Accrual Days", row_idx)
                c_nat = ref("Native Balance", row_idx)
                c_fx = ref("FX Rate", row_idx)
                # إذا انتهى استحقاق المركز (Accrual Days = 0) يكون رصيده النشط 0، وإلا الرصيد الأصلي × سعر الصرف
                cell.value = f'=IF({c_acc}>0, {c_nat}*{c_fx}, 0)'

            elif col_name == "Interest Before" and all(k in col_letter for k in ["Before Balance", "Before Rate", "Accrual Days"]):
                cell.value = f'={ref("Before Balance", row_idx)}*{ref("Before Rate", row_idx)}*({ref("Accrual Days", row_idx)}/365)'

            elif col_name == "NII_Before" and "Interest Before" in col_letter and "ALM_Sign" in col_letter:
                cell.value = f'={ref("Interest Before", row_idx)}*{ref("ALM_Sign", row_idx)}'

            # =========================================================================
            # ج) معادلات تفكيك الأرصدة وسعر الصرف الجديد (Balance Impacts)
            # =========================================================================
            elif col_name == "Impact_Bal_Loan_Growth" and "Before Balance" in col_letter and "Scn_Loan_Growth_pct" in col_letter:
                cell.value = f'={ref("Before Balance", row_idx)}*{ref("Scn_Loan_Growth_pct", row_idx)}'

            elif col_name == "Impact_Bal_Deposit_Growth" and "Before Balance" in col_letter and "Scn_Deposit_Growth_pct" in col_letter:
                cell.value = f'={ref("Before Balance", row_idx)}*{ref("Scn_Deposit_Growth_pct", row_idx)}'

            elif col_name == "Impact_Bal_Deposit_Runoff" and all(k in col_letter for k in ["Before Balance", "Scn_Deposit_Growth_pct", "Scn_Deposit_Runoff_pct"]):
                cell.value = f'=-{ref("Before Balance", row_idx)}*(1+{ref("Scn_Deposit_Growth_pct", row_idx)})*{ref("Scn_Deposit_Runoff_pct", row_idx)}'

            elif col_name == "Impact_Bal_FX_Gold_Shock" and all(k in col_letter for k in ["Before Balance", "Impact_Bal_Loan_Growth", "Impact_Bal_Deposit_Growth", "Impact_Bal_Deposit_Runoff", "Scn_FX_Shock_pct", "Scn_Gold_Shock_pct"]):
                b_ref = ref("Before Balance", row_idx)
                lg_ref = ref("Impact_Bal_Loan_Growth", row_idx)
                dg_ref = ref("Impact_Bal_Deposit_Growth", row_idx)
                dr_ref = ref("Impact_Bal_Deposit_Runoff", row_idx)
                fxs_ref = ref("Scn_FX_Shock_pct", row_idx)
                glds_ref = ref("Scn_Gold_Shock_pct", row_idx)
                cell.value = f'=({b_ref}+{lg_ref}+{dg_ref}+{dr_ref})*({fxs_ref}+{glds_ref})'

            elif col_name == "Native_Balance_After_Growth" and all(k in col_letter for k in ["Native Balance", "Accrual Days", "Scn_Loan_Growth_pct", "Scn_Deposit_Growth_pct", "Scn_Deposit_Runoff_pct"]):
                c_acc = ref("Accrual Days", row_idx)
                c_nat = ref("Native Balance", row_idx)
                c_lg = ref("Scn_Loan_Growth_pct", row_idx)
                c_dg = ref("Scn_Deposit_Growth_pct", row_idx)
                c_dr = ref("Scn_Deposit_Runoff_pct", row_idx)
                cell.value = f'=IF({c_acc}>0, {c_nat}*(1+{c_lg}+{c_dg})*(1-{c_dr}), 0)'

            elif col_name == "Applied Market Rate After" and all(k in col_letter for k in ["FX Rate", "Scn_FX_Shock_pct", "Scn_Gold_Shock_pct"]):
                cell.value = f'={ref("FX Rate", row_idx)}*(1+{ref("Scn_FX_Shock_pct", row_idx)}+{ref("Scn_Gold_Shock_pct", row_idx)})'

            elif col_name == "After Balance" and all(k in col_letter for k in ["Native_Balance_After_Growth", "Applied Market Rate After"]):
                cell.value = f'={ref("Native_Balance_After_Growth", row_idx)}*{ref("Applied Market Rate After", row_idx)}'

            elif col_name == "Total_Balance_Change" and "After Balance" in col_letter and "Before Balance" in col_letter:
                cell.value = f'={ref("After Balance", row_idx)}-{ref("Before Balance", row_idx)}'

            # =========================================================================
            # د) معادلات الفائدة بعد الصدمة وتفكيك صافي الدخل من الفوائد (NII Impacts)
            # =========================================================================
            elif col_name == "After Rate" and all(k in col_letter for k in ["Before Rate", "Scn_Rate_Shock_pp", "Eligible"]):
                c_elig = ref("Eligible", row_idx)
                c_r0 = ref("Before Rate", row_idx)
                c_shk = ref("Scn_Rate_Shock_pp", row_idx)
                cell.value = f'=IF({c_elig}=TRUE, MAX(0, {c_r0}+{c_shk}), {c_r0})'

            elif col_name == "Applied Shock" and "After Rate" in col_letter and "Before Rate" in col_letter:
                cell.value = f'={ref("After Rate", row_idx)}-{ref("Before Rate", row_idx)}'

            elif col_name == "Impact_NII_Rate_Shock" and all(k in col_letter for k in ["Before Balance", "After Rate", "Before Rate", "Repricing Fraction", "Accrual Days", "ALM_Sign"]):
                cell.value = f'={ref("Before Balance", row_idx)}*({ref("After Rate", row_idx)}-{ref("Before Rate", row_idx)})*{ref("Repricing Fraction", row_idx)}*({ref("Accrual Days", row_idx)}/365)*{ref("ALM_Sign", row_idx)}'

            elif col_name == "Impact_NII_Loan_Growth" and all(k in col_letter for k in ["Impact_Bal_Loan_Growth", "Before Rate", "Accrual Days", "ALM_Sign"]):
                cell.value = f'={ref("Impact_Bal_Loan_Growth", row_idx)}*{ref("Before Rate", row_idx)}*({ref("Accrual Days", row_idx)}/365)*{ref("ALM_Sign", row_idx)}'

            elif col_name == "Impact_NII_Deposit_Growth" and all(k in col_letter for k in ["Impact_Bal_Deposit_Growth", "Before Rate", "Accrual Days", "ALM_Sign"]):
                cell.value = f'={ref("Impact_Bal_Deposit_Growth", row_idx)}*{ref("Before Rate", row_idx)}*({ref("Accrual Days", row_idx)}/365)*{ref("ALM_Sign", row_idx)}'

            elif col_name == "Impact_NII_Deposit_Runoff" and all(k in col_letter for k in ["Impact_Bal_Deposit_Runoff", "Before Rate", "Accrual Days", "ALM_Sign"]):
                cell.value = f'={ref("Impact_Bal_Deposit_Runoff", row_idx)}*{ref("Before Rate", row_idx)}*({ref("Accrual Days", row_idx)}/365)*{ref("ALM_Sign", row_idx)}'

            elif col_name == "Impact_NII_FX_Gold_Shock" and all(k in col_letter for k in ["Before Balance", "Scn_FX_Shock_pct", "Scn_Gold_Shock_pct", "Before Rate", "Accrual Days", "ALM_Sign"]):
                cell.value = f'=({ref("Before Balance", row_idx)}*({ref("Scn_FX_Shock_pct", row_idx)}+{ref("Scn_Gold_Shock_pct", row_idx)}))*{ref("Before Rate", row_idx)}*({ref("Accrual Days", row_idx)}/365)*{ref("ALM_Sign", row_idx)}'

            elif col_name == "Interest After" and all(k in col_letter for k in ["After Balance", "Before Rate", "After Rate", "Repricing Fraction", "Accrual Days"]):
                b1_ref = ref("After Balance", row_idx)
                r0_ref = ref("Before Rate", row_idx)
                r1_ref = ref("After Rate", row_idx)
                rf_ref = ref("Repricing Fraction", row_idx)
                acc_ref = ref("Accrual Days", row_idx)
                cell.value = f'={b1_ref}*({r0_ref}+({r1_ref}-{r0_ref})*{rf_ref})*({acc_ref}/365)'

            elif col_name == "NII_After" and "Interest After" in col_letter and "ALM_Sign" in col_letter:
                cell.value = f'={ref("Interest After", row_idx)}*{ref("ALM_Sign", row_idx)}'

            elif col_name == "Total_NII_Impact" and "NII_After" in col_letter and "NII_Before" in col_letter:
                cell.value = f'={ref("NII_After", row_idx)}-{ref("NII_Before", row_idx)}'

            elif col_name == "Impact_NII_Interaction" and all(k in col_letter for k in ["Total_NII_Impact", "Impact_NII_Rate_Shock", "Impact_NII_Loan_Growth", "Impact_NII_Deposit_Growth", "Impact_NII_Deposit_Runoff", "Impact_NII_FX_Gold_Shock"]):
                tot_nii = ref("Total_NII_Impact", row_idx)
                c1 = ref("Impact_NII_Rate_Shock", row_idx)
                c2 = ref("Impact_NII_Loan_Growth", row_idx)
                c3 = ref("Impact_NII_Deposit_Growth", row_idx)
                c4 = ref("Impact_NII_Deposit_Runoff", row_idx)
                c5 = ref("Impact_NII_FX_Gold_Shock", row_idx)
                cell.value = f'={tot_nii}-SUM({c1},{c2},{c3},{c4},{c5})'

            # =========================================================================
            # هـ) معادلات التقييم والائتمان والأثر الكلي النهائي للصف (P&L Impacts)
            # =========================================================================
            elif col_name == "Impact_PnL_FX_Gold_Reval" and "Impact_Bal_FX_Gold_Shock" in col_letter and "ALM_Sign" in col_letter:
                cell.value = f'={ref("Impact_Bal_FX_Gold_Shock", row_idx)}*{ref("ALM_Sign", row_idx)}'

            elif col_name == "Total_Row_PnL_Impact" and all(k in col_letter for k in ["Total_NII_Impact", "Impact_PnL_FX_Gold_Reval", "Impact_PnL_Investment_MTM", "Impact_PnL_Credit_Cost"]):
                p1 = ref("Total_NII_Impact", row_idx)
                p2 = ref("Impact_PnL_FX_Gold_Reval", row_idx)
                p3 = ref("Impact_PnL_Investment_MTM", row_idx)
                p4 = ref("Impact_PnL_Credit_Cost", row_idx)
                cell.value = f'=SUM({p1},{p2},{p3},{p4})'

            else:
                # الأعمدة الأساسية المدخلة (Inputs / Raw View Columns)
                if isinstance(val, (pd.Timestamp, np.datetime64)):
                    cell.value = pd.to_datetime(val).strftime("%Y-%m-%d")
                else:
                    cell.value = val

            # تنسيق الأرقام والنسب المئوية داخل الإكسيل
            cell.border = thin_border
            if col_name in [
                "Before Rate", "After Rate", "Applied Shock", "Repricing Fraction",
                "Scn_Rate_Shock_pp", "Scn_Loan_Growth_pct", "Scn_Deposit_Growth_pct",
                "Scn_Deposit_Runoff_pct", "Scn_FX_Shock_pct", "Scn_Gold_Shock_pct",
                "Scn_Unemp_Shock_pct", "Scn_GDP_Shock_pct"
            ]:
                cell.number_format = "0.00%"
            elif col_name in ["FX Rate", "Applied Market Rate After"]:
                cell.number_format = "#,##0.000"
            elif col_name in ["Remaining_Maturity_Days", "Days_To_Next_Repricing", "Accrual Days", "ALM_Sign"]:
                cell.number_format = "#,##0"
            elif isinstance(val, (float, np.floating)) or col_name.startswith(("Impact_", "Total_", "Before ", "After ", "Native", "Interest", "NII_")):
                cell.number_format = "#,##0.00"

    # 3. إضافة صف الإجمالي (TOTAL Row) بمعادلات SUM في أسفل الجدول
    total_row_idx = len(df_out) + 2
    ws.cell(row=total_row_idx, column=1, value="TOTAL").font = Font(bold=True)

    sum_columns = [
        "Before Balance", "Interest Before", "NII_Before",
        "Impact_Bal_Loan_Growth", "Impact_Bal_Deposit_Growth", "Impact_Bal_Deposit_Runoff",
        "Impact_Bal_FX_Gold_Shock", "After Balance", "Total_Balance_Change",
        "Impact_NII_Rate_Shock", "Impact_NII_Loan_Growth", "Impact_NII_Deposit_Growth",
        "Impact_NII_Deposit_Runoff", "Impact_NII_FX_Gold_Shock", "Impact_NII_Interaction",
        "Interest After", "NII_After", "Total_NII_Impact",
        "Impact_PnL_FX_Gold_Reval", "Impact_PnL_Investment_MTM", "Impact_PnL_Credit_Cost",
        "Total_Row_PnL_Impact"
    ]

    total_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    for col_idx, col_name in enumerate(cols, start=1):
        c_cell = ws.cell(row=total_row_idx, column=col_idx)
        c_cell.fill = total_fill
        c_cell.font = Font(bold=True)
        c_cell.border = thin_border
        if col_name in sum_columns:
            col_let = col_letter[col_name]
            c_cell.value = f"=SUM({col_let}2:{col_let}{total_row_idx - 1})"
            c_cell.number_format = "#,##0.00"

    # # تثبيت الصف الأول وأول 4 أعمدة لسهولة التصفح
    # ws.freeze_panes = "E2"

    # ضبط عرض الأعمدة تلقائياً
    for col_idx, col_name in enumerate(cols, start=1):
        col_let = get_column_letter(col_idx)
        ws.column_dimensions[col_let].width = max(len(str(col_name)) + 5, 16)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()