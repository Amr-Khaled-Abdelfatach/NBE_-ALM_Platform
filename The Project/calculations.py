"""Deterministic ALM calculation engine (Simplified & Verified)."""
import re
from typing import Dict, Optional

import numpy as np
import pandas as pd

from config import (
    MODEL_AMOUNT_UNIT, DAYS_PER_YEAR, RATE_FLOOR, VALUATION_INSTRUMENTS, MACRO_CODES,
    FUNDING_SEGMENTS, EXCLUDE_MATURED_POSITIONS, ROLLOVER_AT_MATURITY, TENOR_DAY_FACTORS,
    FREQUENCY_WORD_DAYS, LCR_WINDOW_DAYS, LCR_DEPOSIT_STRESS_FLOOR_PCT, SCENARIO_DEFAULTS,
    CREDIT_COST_BPS_PER_UNEMP_PP, CREDIT_COST_BPS_PER_GDP_DROP_PP,
)


def col(df: pd.DataFrame, name: str) -> Optional[str]:
    if df is None or df.empty:
        return None
    lookup = {str(c).lower(): c for c in df.columns}
    return lookup.get(name.lower())


def to_num(series, default=0.0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _series(df: pd.DataFrame, name: str, default=np.nan) -> pd.Series:
    if name in df.columns:
        return df[name]
    return pd.Series(default, index=df.index)


def _str(df: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    return _series(df, name, default).astype("object").where(lambda s: s.notna(), default).astype(str)


def latest_date(df: pd.DataFrame, column: str) -> Optional[pd.Timestamp]:
    if df is None or df.empty or column not in df.columns:
        return None
    values = pd.to_datetime(df[column], errors="coerce").dropna()
    return values.max() if not values.empty else None


def _norm_date(value=None) -> pd.Timestamp:
    ts = pd.Timestamp.today() if value is None else pd.Timestamp(value)
    return ts.normalize()


def tenor_to_days(value, default=None):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return default
    text = str(value).strip().upper()
    if not text:
        return default
    if text in FREQUENCY_WORD_DAYS:
        days = FREQUENCY_WORD_DAYS[text]
        return default if days is None else int(days)
    m = re.search(r"(\d+(?:\.\d+)?)\s*([DWMY])", text)
    if not m:
        return default
    return int(round(float(m.group(1)) * TENOR_DAY_FACTORS[m.group(2)]))


def normalize_market_yield(value):
    try:
        x = float(value)
    except Exception:
        return np.nan
    if not np.isfinite(x):
        return np.nan
    return x / 100.0 if abs(x) > 2.0 else x


def merge_params(params: Optional[dict]) -> Dict[str, float]:
    out = dict(SCENARIO_DEFAULTS)
    if params:
        for k, v in params.items():
            if k in out and v is not None:
                try:
                    out[k] = float(v)
                except (TypeError, ValueError):
                    pass
    return out


# ----------------------------------------------------------------------------
# Market data helpers
# ----------------------------------------------------------------------------
def latest_market_date(market_data: pd.DataFrame) -> Optional[pd.Timestamp]:
    return latest_date(market_data, "as_of_datetime")


def valuation_rates(market_data: pd.DataFrame) -> pd.DataFrame:
    cols = ["base_currency", "market_rate"]
    need = {"instrument_code", "base_currency", "value"}
    if market_data is None or market_data.empty or not need <= set(market_data.columns):
        return pd.DataFrame(columns=cols)
    md = market_data[market_data["instrument_code"].isin(VALUATION_INSTRUMENTS)].copy()
    if md.empty:
        return pd.DataFrame(columns=cols)
    md["_d"] = pd.to_datetime(_series(md, "as_of_datetime"), errors="coerce")
    md["_id"] = pd.to_numeric(_series(md, "market_data_id", 0), errors="coerce").fillna(0)
    md["value"] = pd.to_numeric(md["value"], errors="coerce")
    md = md.dropna(subset=["value"]).sort_values(["_d", "_id"])
    latest = md.groupby("base_currency", as_index=False).tail(1)
    return latest[["base_currency", "value"]].rename(columns={"value": "market_rate"}).reset_index(drop=True)


def macro_snapshot(market_data: pd.DataFrame) -> Dict[str, float]:
    out = {k: np.nan for k in MACRO_CODES}
    out["as_of"] = None
    if market_data is None or market_data.empty or "instrument_code" not in market_data.columns:
        return out
    md = market_data.copy()
    md["_d"] = pd.to_datetime(_series(md, "as_of_datetime"), errors="coerce")
    md["_id"] = pd.to_numeric(_series(md, "market_data_id", 0), errors="coerce").fillna(0)
    for key, code in MACRO_CODES.items():
        sub = md[md["instrument_code"] == code].sort_values(["_d", "_id"])
        if sub.empty:
            continue
        out[key] = normalize_market_yield(sub.iloc[-1]["value"])
        d = sub.iloc[-1]["_d"]
        if pd.notna(d) and (out["as_of"] is None or d > out["as_of"]):
            out["as_of"] = d
    return out


def market_history(market_data: pd.DataFrame, instrument_code: str) -> pd.DataFrame:
    if market_data is None or market_data.empty or "instrument_code" not in market_data.columns:
        return pd.DataFrame(columns=["as_of_datetime", "value"])
    h = market_data[market_data["instrument_code"] == instrument_code].copy()
    h["as_of_datetime"] = pd.to_datetime(h["as_of_datetime"], errors="coerce")
    h["value"] = pd.to_numeric(h["value"], errors="coerce")
    return h.sort_values("as_of_datetime")[["as_of_datetime", "value"]].reset_index(drop=True)


# ----------------------------------------------------------------------------
# Normalization from SQL Views (vw_alm_flat_table & vw_alm_cashflows_flat)
# ----------------------------------------------------------------------------
def position_category(position_type: pd.Series, segment: pd.Series) -> pd.Series:
    ptype = position_type.astype("object").where(position_type.notna(), "").astype(str).str.strip().str.title()
    seg = segment.astype("object").where(segment.notna(), "").astype(str).str.strip().str.lower()
    funding = ptype.eq("Deposit") & seg.isin([s.lower() for s in FUNDING_SEGMENTS])
    cat = ptype.mask(funding, "Funding")
    return cat.mask(cat.eq(""), "Other")


def normalize_positions(flat: pd.DataFrame) -> pd.DataFrame:
    """Convert vw_alm_flat_table into the standardized engine DataFrame."""
    if flat is None or flat.empty:
        return pd.DataFrame()
    x = flat.copy()
    status = _str(x, "Status", "Active").str.strip().str.lower()
    x = x[status.isin(["active", ""])].copy().reset_index(drop=True)
    if x.empty:
        return pd.DataFrame()

    bal_native = to_num(_series(x, "Original_Balance_Or_Grams", 0.0))
    fx_rate = to_num(_series(x, "Applied_Market_Rate", 1.0), 1.0)
    egp = pd.to_numeric(_series(x, "Evaluated_Balance_EGP"), errors="coerce")

    model = pd.DataFrame({
        "position_id": pd.to_numeric(x["Position_ID"], errors="coerce").astype("Int64"),
        "product_id": pd.to_numeric(_series(x, "Product_ID"), errors="coerce").astype("Int64"),
        "product_code": _str(x, "Product_Code"),
        "product_name": _str(x, "Product_Name"),
        "Product Type": _str(x, "Product_Type"),
        "Product Family": _str(x, "Product_Family"),
        "Category": position_category(_series(x, "ALM_Position_Category"), _series(x, "Customer_Segment")),
        "Customer Segment": _str(x, "Customer_Segment"),
        "Model Currency": _str(x, "Currency", "EGP"),
        "Valuation Basis": _str(x, "Valuation_Basis", "EGP"),
        "Native Balance": bal_native,
        "FX Rate": fx_rate,
        "Model Balance": egp.fillna(bal_native * fx_rate),
        "Model Rate": to_num(_series(x, "Contractual_Interest_Rate", 0.0)),
        "Model Rate Type": _str(x, "Rate_Type", "Unknown"),
        "Model Start Date": pd.to_datetime(_series(x, "Start_Date"), errors="coerce"),
        "Model Maturity Date": pd.to_datetime(_series(x, "Maturity_Date"), errors="coerce"),
        "Model Repricing Date": pd.to_datetime(_series(x, "Next_Repricing_Date"), errors="coerce"),
        "Repricing Frequency": _str(x, "Repricing_Frequency"),
        "HQLA Flag": to_num(_series(x, "HQLA_Liquidity_Flag", 0)).astype(bool),
        "Status": _str(x, "Status", "Active"),
    })
    model["Repricing Frequency Days"] = model["Repricing Frequency"].map(tenor_to_days).astype(float)
    return model


def normalize_cashflows(flat: pd.DataFrame) -> pd.DataFrame:
    """Convert vw_alm_cashflows_flat into the standardized engine DataFrame."""
    cols = [
        "cashflow_id", "position_id", "Cashflow Date", "Flow", "Amount EGP",
        "Principal EGP", "Interest EGP", "Valuation Basis", "Currency",
        "HQLA Flag", "Bucket", "Cashflow Type", "Product Code", "Product Family",
    ]
    if flat is None or flat.empty:
        return pd.DataFrame(columns=cols)
    x = flat
    return pd.DataFrame({
        "cashflow_id": _series(x, "Cashflow_ID"),
        "position_id": _series(x, "Position_ID"),
        "Cashflow Date": pd.to_datetime(_series(x, "Cashflow_Date"), errors="coerce"),
        "Flow": _str(x, "Flow_Type").str.strip().str.lower(),
        "Amount EGP": to_num(_series(x, "Total_Amount_EGP", 0.0)),
        "Principal EGP": to_num(_series(x, "Principal_EGP", 0.0)),
        "Interest EGP": to_num(_series(x, "Interest_EGP", 0.0)),
        "Valuation Basis": _str(x, "Valuation_Basis", "EGP"),
        "Currency": _str(x, "Currency", "EGP"),
        "HQLA Flag": to_num(_series(x, "HQLA_Flag", 0)).astype(bool),
        "Bucket": _str(x, "ALM_Maturity_Bucket", "Unbucketed"),
        "Cashflow Type": _str(x, "Cashflow_Type"),
        "Product Code": _str(x, "Product_Code"),
        "Product Family": _str(x, "Product_Family"),
    })[cols]


# ----------------------------------------------------------------------------
# Core Position & Repricing Engine
# ----------------------------------------------------------------------------



def _basis_shock_pct(basis: pd.Series, p: Dict[str, float]) -> np.ndarray:
    """النسبة المئوية الإضافية للصدمة (FX % أو Gold %)."""
    b = basis.astype(str).str.strip().str.lower()
    return np.select([b.eq("fx"), b.eq("gold")], [p.get("fx_shock_pct", 0.0), p.get("gold_shock_pct", 0.0)], default=0.0)


def _credit_cost_rate_decimal(p: Dict[str, float]) -> float:
    """Direct macro credit-cost rate from adverse unemployment (+) and GDP (-) shocks."""
    unemp_up = max(p["unemployment_shock_pct"], 0.0)
    gdp_down = max(-p["gdp_shock_pct"], 0.0)
    return (CREDIT_COST_BPS_PER_UNEMP_PP * unemp_up + CREDIT_COST_BPS_PER_GDP_DROP_PP * gdp_down) / 10000.0




def run_position_engine(q: pd.DataFrame, simulation_date, horizon_days: int, params: Optional[dict] = None) -> pd.DataFrame:
    """Per-position Before / After balances, rates, interest, MTM, revaluation, and credit cost."""
    if q is None or q.empty:
        return pd.DataFrame()
    p = merge_params(params)
    sim = _norm_date(simulation_date)
    horizon = max(int(horizon_days), 0)
    h_end = sim + pd.Timedelta(days=horizon)
    out = q.copy()
    cat = out["Category"].astype(str).str.lower()

    # 1. Accrual window
    mat = out["Model Maturity Date"]
    matured = mat.notna() & (mat <= sim)
    mat_in_window = mat.notna() & (mat > sim) & (mat < h_end)
    accrual_end = pd.Series(h_end, index=out.index) if ROLLOVER_AT_MATURITY else mat.where(mat_in_window, h_end)
    days = (accrual_end - sim).dt.days.clip(lower=0).astype(float)
    excluded = matured if EXCLUDE_MATURED_POSITIONS else pd.Series(False, index=out.index)
    days = days.where(~matured, 0.0)

    # 2. Repricing eligibility & first event
    rt = out["Model Rate Type"].astype(str).str.lower()
    fixed = rt.str.contains("fixed|non-interest|non interest|zero interest", regex=True, na=False)
    floating = rt.str.contains("floating|variable|repric|adjustable", regex=True, na=False)
    eligible_type = ~fixed & (floating | cat.isin(["loan", "deposit", "funding", "investment"]))

    fd = out["Repricing Frequency Days"].astype(float).where(lambda s: s > 0)
    rep = out["Model Repricing Date"]
    need_roll = rep.notna() & (rep < sim)
    gap = (sim - rep).dt.days.astype(float)
    rolled = rep + pd.to_timedelta(np.ceil(gap / fd), unit="D")
    first = rep.where(~need_roll, rolled).where(~(need_roll & fd.isna()), sim).fillna(sim)

    reset_event = eligible_type & (first < accrual_end)
    maturity_event = ROLLOVER_AT_MATURITY & ~eligible_type & mat_in_window
    has_event = (days > 0) & (reset_event | maturity_event)
    first = first.where(eligible_type, mat)

    t1 = np.minimum(((first - sim).dt.days.astype(float)).clip(lower=0), days)
    t1 = pd.Series(np.where(has_event, t1, days), index=out.index)
    after_first = ((accrual_end - first).dt.days.astype(float)).clip(lower=0)
    per_freq = np.where(fd.notna() & eligible_type, np.ceil(after_first / fd.fillna(1.0)), 1.0)
    n_events = pd.Series(np.where(has_event, per_freq, 0.0), index=out.index).astype(int)

    # 3. Direct rate shock (no hidden inflation distortion)
    delta_rate = p["rate_change_pp"] / 100.0
    r0 = out["Model Rate"].astype(float)
    r1 = pd.Series(np.where(has_event, np.maximum(RATE_FLOOR, r0 + delta_rate), r0), index=out.index)
    rate_profile = (r0 * days + (r1 - r0) * (days - t1)) / DAYS_PER_YEAR

    # 4. Balances (Loan growth, Deposit growth & runoff, FX & Gold shocks)
    b0 = out["Model Balance"].astype(float).where(~excluded, 0.0)
    growth = np.select(
        [cat.eq("loan"), cat.eq("deposit")],
        [
            1.0 + p["loan_growth_pct"] / 100.0,
            (1.0 + p["deposit_growth_pct"] / 100.0) * (1.0 - p["deposit_runoff_pct"] / 100.0),
        ],
        default=1.0,
    )
    growth = np.maximum(growth, 0.0)
    shock_factor = 1.0 + _basis_shock_pct(out["Valuation Basis"], p) / 100.0
    out["Applied Market Rate After"] = out["FX Rate"] * shock_factor
    b1 = b0 * growth * shock_factor

    # 5. Interest & Rate/Volume decomposition
    interest_before = b0 * ((r0 * days) / DAYS_PER_YEAR)
    interest_rate_only = b0 * rate_profile
    interest_after = b1 * rate_profile

    # 6. P&L & Value components
    sign = np.where(cat.isin(["loan", "investment"]), 1.0, np.where(cat.isin(["deposit", "funding"]), -1.0, 0.0))
    valuation_effect = sign * b0 * growth * (shock_factor - 1.0)
    inv = cat.eq("investment") & ~out["Valuation Basis"].astype(str).str.lower().eq("gold") & ~excluded
    dur_years = ((first - sim).dt.days.astype(float).clip(lower=0) / DAYS_PER_YEAR / (1.0 + r0)).where(first.notna(), 0.0)
    mtm = pd.Series(np.where(inv, -dur_years * delta_rate * b1, 0.0), index=out.index)

    cc_rate = _credit_cost_rate_decimal(p)
    credit_cost = pd.Series(np.where(cat.eq("loan"), b1 * cc_rate * horizon / DAYS_PER_YEAR, 0.0), index=out.index)

    out["Valuation Effect"] = valuation_effect
    out["Duration (yrs)"] = dur_years.where(inv, 0.0)
    out["Investment MTM"] = mtm
    out["Credit Cost"] = credit_cost
    out["Excluded (matured)"] = excluded & matured
    out["Accrual Days"] = days
    out["Eligible"] = has_event
    out["First Repricing Date"] = first.where(has_event)
    out["Repricing Event Count"] = n_events
    out["Repricing Fraction"] = np.where(has_event & (days > 0), (days - t1) / days.where(days > 0, 1.0), 0.0)
    out["Before Balance"] = b0
    out["After Balance"] = b1
    out["Valuation Shock Factor"] = shock_factor
    out["Before Rate"] = r0
    out["After Rate"] = r1
    out["Applied Shock"] = r1 - r0
    out["Annual Interest Before"] = b0 * r0
    out["Annual Interest After"] = b1 * r1
    out["Interest Before"] = interest_before
    out["Interest After"] = interest_after
    out["Interest Change"] = interest_after - interest_before
    out["Rate Effect"] = interest_rate_only - interest_before
    out["Volume Effect"] = interest_after - interest_rate_only
    out["Annual Interest Change"] = out["Interest Change"] * DAYS_PER_YEAR / max(horizon, 1)
    finite = np.isfinite(out["Interest After"]) & np.isfinite(out["Interest Before"])
    out["Validation Status"] = np.where(finite, "PASS", "FAIL")
    return out


def summarize_positions(q: pd.DataFrame, phase: str, horizon_days: int) -> Dict[str, float]:
    keys = [
        "NII", "Interest Income", "Interest Expense",
        "Loan Interest", "Investment Interest",
        "Deposit Interest", "Funding Interest",
        "Loans", "Deposits", "Funding", "Investments",
        "NIM %", "Cost of Funds %", "Loan Deposit %",
        "Repricing Gap", "HQLA Stock",
    ]
    if q is None or q.empty:
        return {**{k: 0.0 for k in keys}, "Horizon Days": int(horizon_days)}
    bal, intr = f"{phase} Balance", f"{phase if phase in ('Before', 'After') else 'After'}"
    intr_col = f"Interest {phase}"
    cat = q["Category"].astype(str).str.lower()
    isl, isd, isf, isi = cat.eq("loan"), cat.eq("deposit"), cat.eq("funding"), cat.eq("investment")

    li, ii = float(q.loc[isl, intr_col].sum()), float(q.loc[isi, intr_col].sum())
    di, fi = float(q.loc[isd, intr_col].sum()), float(q.loc[isf, intr_col].sum())
    loans, deposits = float(q.loc[isl, bal].sum()), float(q.loc[isd, bal].sum())
    funding, invest = float(q.loc[isf, bal].sum()), float(q.loc[isi, bal].sum())
    income, expense = li + ii, di + fi
    nii = income - expense
    earning, fund_base = loans + invest, deposits + funding
    ann = DAYS_PER_YEAR / max(int(horizon_days), 1)
    elig = q["Eligible"].astype(bool)
    gap = float(q.loc[(isl | isi) & elig, bal].sum()) - float(q.loc[(isd | isf) & elig, bal].sum())
    hqla = float(q.loc[isi & q["HQLA Flag"].astype(bool), bal].sum())
    return {
        "NII": nii, "Interest Income": income, "Interest Expense": expense,
        "Loan Interest": li, "Investment Interest": ii,
        "Deposit Interest": di, "Funding Interest": fi,
        "Loans": loans, "Deposits": deposits, "Funding": funding,
        "Investments": invest,
        "NIM %": nii * ann / earning * 100.0 if earning else 0.0,
        "Cost of Funds %": expense * ann / fund_base * 100.0 if fund_base else 0.0,
        "Loan Deposit %": loans / deposits * 100.0 if deposits else 0.0,
        "Repricing Gap": gap, "HQLA Stock": hqla,
        "Horizon Days": int(horizon_days),
    }


# ----------------------------------------------------------------------------
# Balanced Cash-flow & Liquidity Engine
# ----------------------------------------------------------------------------
def _window_mask(cf: pd.DataFrame, sim: pd.Timestamp, days: int) -> pd.Series:
    d = cf["Cashflow Date"]
    return d.notna() & (d >= sim) & (d <= sim + pd.Timedelta(days=int(days)))


def cashflow_metrics(cf: pd.DataFrame, q: pd.DataFrame, simulation_date, horizon_days: int,
                     params: Optional[dict] = None, stressed: bool = False) -> Dict[str, float]:
    """Balanced liquidity metrics & LCR Proxy.

    To avoid astronomical LCR % values when 30-day scheduled cash outflows are small,
    30-day stressed outflow (`LCR Net Outflow`) =
        30-day Scheduled Gross Outflows
      + Stressed Deposit Run-off Outflow
      + 15% Standard Liquidity Reserve of Total Deposits & Wholesale Funding.
    """
    p = merge_params(params)
    sim = _norm_date(simulation_date)
    use = p if stressed else merge_params(None)

    cat = q["Category"].astype(str).str.lower() if q is not None and not q.empty else pd.Series(dtype=str)
    if q is not None and not q.empty:
        bal_col = "After Balance" if stressed else "Before Balance"
        hqla = float(q.loc[cat.eq("investment") & q["HQLA Flag"].astype(bool), bal_col].sum())
        total_deposits = float(q.loc[cat.eq("deposit"), bal_col].sum())
        total_funding = float(q.loc[cat.isin(["deposit", "funding"]), bal_col].sum())
        deposits_pre_runoff = float((q.loc[cat.eq("deposit"), "Before Balance"] *
                                     q.loc[cat.eq("deposit"), "Valuation Shock Factor"]).sum())
    else:
        hqla = total_deposits = total_funding = deposits_pre_runoff = 0.0

    runoff_extra = (use["deposit_runoff_pct"] / 100.0) * deposits_pre_runoff

    if cf is None or cf.empty:
        in_h = out_h = in_l = out_l = 0.0
        cf_asof = None
    else:
        amt = cf["Amount EGP"] * (1.0 + _basis_shock_pct(cf["Valuation Basis"], use) / 100.0)
        is_in, is_out = cf["Flow"].eq("inflow"), cf["Flow"].eq("outflow")
        wh = _window_mask(cf, sim, horizon_days)
        wl = _window_mask(cf, sim, LCR_WINDOW_DAYS)
        in_h, out_h = float(amt[wh & is_in].sum()), float(amt[wh & is_out].sum())
        in_l, out_l = float(amt[wl & is_in].sum()), float(amt[wl & is_out].sum())
        cf_asof = latest_date(cf, "Cashflow Date")

    out_h += runoff_extra
    standard_deposit_reserve = LCR_DEPOSIT_STRESS_FLOOR_PCT * total_funding
    stressed_30d_outflow = out_l + runoff_extra + standard_deposit_reserve

# حساب نسبة HQLA إلى إجمالي الودائع لتكون هي الـ LCR المعتمد
    hqla_to_deposits = (hqla / total_deposits * 100.0) if total_deposits > 0 else np.nan
    
    # اعتماد HQLA to Deposits ليصبح هو الـ LCR Proxy الرئيسي في النظام
    lcr_proxy = hqla_to_deposits

    return {
        "CF Inflow": in_h,
        "CF Outflow": out_h,
        "CF Net Cashflow": in_h - out_h,
        "CF HQLA": hqla,
        "CF LCR %": lcr_proxy,
        "HQLA to Deposits %": hqla_to_deposits,
        "LCR Inflow": in_l,
        "LCR Outflow": out_l + runoff_extra,
        "LCR Net Outflow": stressed_30d_outflow,
        "Liquidity": in_h - out_h,
        "CF As Of": cf_asof,
    }


# ----------------------------------------------------------------------------
# Scenario execution
# ----------------------------------------------------------------------------
def _macro_block(macro: Dict[str, float], p: Dict[str, float]) -> Dict[str, dict]:
    def pct(v):
        return float(v) * 100.0 if pd.notna(v) else np.nan
    pol, inf = pct(macro["policy_rate"]), pct(macro["inflation"])
    une, gdp = pct(macro["unemployment"]), pct(macro["gdp_growth"])
    pol_s = pol + p["rate_change_pp"] if pd.notna(pol) else np.nan
    inf_s = inf + p["inflation_shock_pct"] if pd.notna(inf) else np.nan
    return {
        "before": {
            "Macro Policy Rate %": pol, "Macro Inflation %": inf,
            "Macro Unemployment %": une, "Macro GDP Growth %": gdp,
            "Macro Real Policy Rate %": (pol - inf) if pd.notna(pol) and pd.notna(inf) else np.nan,
        },
        "after": {
            "Macro Policy Rate %": pol_s, "Macro Inflation %": inf_s,
            "Macro Unemployment %": (une + p["unemployment_shock_pct"]) if pd.notna(une) else np.nan,
            "Macro GDP Growth %": (gdp + p["gdp_shock_pct"]) if pd.notna(gdp) else np.nan,
            "Macro Real Policy Rate %": (pol_s - inf_s) if pd.notna(pol_s) and pd.notna(inf_s) else np.nan,
        },
    }


def prepare_inputs(positions: pd.DataFrame, cashflows: Optional[pd.DataFrame] = None,
                   market_data: Optional[pd.DataFrame] = None) -> dict:
    market_data = market_data if market_data is not None else pd.DataFrame()
    return {
        "q0": normalize_positions(positions if positions is not None else pd.DataFrame()),
        "cf": normalize_cashflows(cashflows if cashflows is not None else pd.DataFrame()),
        "macro": macro_snapshot(market_data),
        "valuation": latest_market_date(market_data),
    }


def apply_scenario(positions: pd.DataFrame, cashflows: Optional[pd.DataFrame] = None,
                   market_data: Optional[pd.DataFrame] = None, simulation_date=None,
                   horizon_days: int = 365, params: Optional[dict] = None):
    return apply_prepared(prepare_inputs(positions, cashflows, market_data), simulation_date, horizon_days, params)


def apply_prepared(prep: dict, simulation_date=None, horizon_days: int = 365, params: Optional[dict] = None):
    p = merge_params(params)
    sim = _norm_date(simulation_date)
    horizon = max(int(horizon_days), 1)
    q0, cf = prep["q0"], prep["cf"]
    q = run_position_engine(q0, sim, horizon, p)

    before = summarize_positions(q, "Before", horizon)
    after = summarize_positions(q, "After", horizon)
    before.update(cashflow_metrics(cf, q, sim, horizon, p, stressed=False))
    after.update(cashflow_metrics(cf, q, sim, horizon, p, stressed=True))
    after["CF LCR Proxy Base %"] = before.get("CF LCR %", np.nan)
    after["Liquidity Base"] = before.get("CF Net Cashflow", 0.0)

    macro = _macro_block(prep["macro"], p)
    before.update(macro["before"])
    after.update(macro["after"])

    valuation = prep["valuation"]
    for d in (before, after):
        d["Position As Of"] = valuation if valuation is not None else sim
        d["Simulation Date"] = sim
        d["Baseline Unit"] = MODEL_AMOUNT_UNIT
        d["Model Amount Unit"] = MODEL_AMOUNT_UNIT

    def delta(cats, col_):
        if q is None or q.empty:
            return 0.0
        return float(q.loc[q["Category"].astype(str).str.lower().isin(cats), col_].sum())

    def nii_effect(col_):
        if q is None or q.empty:
            return 0.0
        c = q["Category"].astype(str).str.lower()
        sign = np.where(c.isin(["loan", "investment"]), 1.0, np.where(c.isin(["deposit", "funding"]), -1.0, 0.0))
        return float((q[col_] * sign).sum())

    credit = float(q["Credit Cost"].sum()) if not q.empty else 0.0
    reval = float(q["Valuation Effect"].sum()) if not q.empty else 0.0
    mtm = float(q["Investment MTM"].sum()) if not q.empty else 0.0
    d_nii = after["NII"] - before["NII"]
    cc_rate_pct = _credit_cost_rate_decimal(p) * 100.0

    before.update({
        "PnL NII Change": 0.0, "PnL Credit Cost": 0.0,
        "PnL FX Gold Revaluation": 0.0, "PnL Investment MTM": 0.0, "PnL Total Impact": 0.0,
        "Credit Cost Rate (%)": 0.0,
    })
    after.update({
        "PnL NII Change": d_nii,
        "PnL Credit Cost": -credit if credit else 0.0,
        "PnL FX Gold Revaluation": reval,
        "PnL Investment MTM": mtm,
        "PnL Total Impact": d_nii - credit + reval + mtm,
        "Credit Cost Rate (%)": cc_rate_pct,
        "Loan Interest Change": delta(["loan"], "Interest Change"),
        "Deposit Interest Change": delta(["deposit"], "Interest Change"),
        "Funding Interest Change": delta(["funding"], "Interest Change"),
        "Investment Interest Change": delta(["investment"], "Interest Change"),
        "NII Rate Effect": nii_effect("Rate Effect"),
        "NII Volume Effect": nii_effect("Volume Effect"),
        "Repricing Gap Base": before["Repricing Gap"],
        "Repricing Gap After": after["Repricing Gap"],
        "Repricing Eligible Positions": float(q["Eligible"].sum()) if not q.empty else 0.0,
        "Repricing Events Total": float(q["Repricing Event Count"].sum()) if not q.empty else 0.0,
        "Excluded Matured Positions": float(q["Excluded (matured)"].sum()) if not q.empty else 0.0,
        "Rate Shock (pp)": p["rate_change_pp"],
        "FX Shock (%)": p["fx_shock_pct"],
        "Gold Shock (%)": p["gold_shock_pct"],
        "Deposit Run-off (%)": p["deposit_runoff_pct"],
        "Loan Growth (%)": p["loan_growth_pct"],
        "Deposit Growth (%)": p["deposit_growth_pct"],
        "Inflation Shock (pp)": p["inflation_shock_pct"],
        "Unemployment Shock (pp)": p["unemployment_shock_pct"],
        "GDP Shock (pp)": p["gdp_shock_pct"],
    })
    return before, after, q


def build_model_baseline(positions: pd.DataFrame, cashflows: pd.DataFrame,
                         market_data: Optional[pd.DataFrame] = None,
                         simulation_date=None, horizon_days: int = 365) -> Dict[str, float]:
    before, _, _ = apply_scenario(positions, cashflows, market_data, simulation_date, horizon_days, None)
    return before