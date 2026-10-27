"""Scenario definitions, parameter parsing, templates, and explanation-only AI (Simplified)."""
import json
import re
from typing import Dict, Optional, Tuple

import pandas as pd

from config import (
    SCENARIO_DEFAULTS, SCENARIO_TEMPLATES, PARAM_SPECS, INDIVIDUAL_DRIVERS,
    OLLAMA_TIMEOUT, OLLAMA_TEMPERATURE,
)


def scenario_summary(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    fields = [
        "scenario_id", "scenario_code", "scenario_name", "scenario_type",
        "base_as_of_datetime", "simulation_date", "horizon_days",
        "policy_rate_shock_bps", "fx_shock_pct", "gold_shock_pct",
        "deposit_runoff_pct", "loan_growth_pct", "deposit_growth_pct",
        "inflation_shock_pct", "unemployment_shock_pct", "gdp_shock_pct", "status",
    ]
    keep = [c for c in fields if c in df.columns]
    return df[keep].drop_duplicates().reset_index(drop=True)


def scenario_params_from_row(row) -> Dict[str, float]:
    """Read the 9 scenario parameters directly from a `scenarios` table row."""
    if row is None:
        return dict(SCENARIO_DEFAULTS)

    def f(name, default=0.0):
        try:
            v = row.get(name, default)
            return float(v) if v is not None and not pd.isna(v) else float(default)
        except Exception:
            return float(default)

    p = dict(SCENARIO_DEFAULTS)
    p.update({
        "rate_change_pp": f("policy_rate_shock_bps") / 100.0,
        "fx_shock_pct": f("fx_shock_pct"),
        "gold_shock_pct": f("gold_shock_pct"),
        "deposit_runoff_pct": f("deposit_runoff_pct"),
        "loan_growth_pct": f("loan_growth_pct"),
        "deposit_growth_pct": f("deposit_growth_pct"),
        "inflation_shock_pct": f("inflation_shock_pct"),
        "unemployment_shock_pct": f("unemployment_shock_pct"),
        "gdp_shock_pct": f("gdp_shock_pct"),
    })
    return p


def scenario_row_from_params(params: Dict[str, float]) -> Dict[str, float]:
    """Convert engine parameters directly into the 9 numeric columns of `scenarios`."""
    p = build_scenario_parameters(params)
    return {
        "policy_rate_shock_bps": p["rate_change_pp"] * 100.0,
        "fx_shock_pct": p["fx_shock_pct"],
        "gold_shock_pct": p["gold_shock_pct"],
        "deposit_runoff_pct": p["deposit_runoff_pct"],
        "loan_growth_pct": p["loan_growth_pct"],
        "deposit_growth_pct": p["deposit_growth_pct"],
        "inflation_shock_pct": p["inflation_shock_pct"],
        "unemployment_shock_pct": p["unemployment_shock_pct"],
        "gdp_shock_pct": p["gdp_shock_pct"],
    }


def build_scenario_parameters(values: Optional[dict] = None) -> Dict[str, float]:
    params = dict(SCENARIO_DEFAULTS)
    if values:
        for k, v in values.items():
            if k in params and v is not None:
                try:
                    params[k] = float(v)
                except (TypeError, ValueError):
                    pass
    return params


# ----------------------------------------------------------------------------
# Templates
# ----------------------------------------------------------------------------
def template_values(template_name: str) -> dict:
    template = SCENARIO_TEMPLATES[template_name]
    values = dict(SCENARIO_DEFAULTS)
    values.update({k: float(v) for k, v in template.get("values", {}).items()})
    if template_name == "Individual Parameter Scenario":
        values["individual_parameter"] = template.get("individual_parameter", "Policy Rate Shock")
        values["individual_value"] = float(template.get("individual_value", 2.0))
    return values


_TEMPLATE_BY_NAME = {name.upper(): name for name in SCENARIO_TEMPLATES}


def resolve_saved_template(scenario_type, saved_values=None) -> str:
    t = str(scenario_type).upper()
    if t in _TEMPLATE_BY_NAME:
        return _TEMPLATE_BY_NAME[t]
    if t == "BASE":
        return "Base Case"
    if t in {"COMBINED_STRESS", "CUSTOM"}:
        return "Combined Stress Scenario"
    if t == "FREE_TYPE":
        return "Free Type Scenario"
    if "INDIVIDUAL" in t or "SHOCK" in t:
        return "Individual Parameter Scenario"
    return "Combined Stress Scenario"


def individual_driver_to_params(driver_label: str, value: float) -> Dict[str, float]:
    params = dict(SCENARIO_DEFAULTS)
    key = INDIVIDUAL_DRIVERS.get(driver_label)
    if key:
        params[key] = float(value)
    return params


def params_to_individual_driver(params: Dict[str, float]) -> Tuple[str, float]:
    for label, key in INDIVIDUAL_DRIVERS.items():
        if abs(float(params.get(key, SCENARIO_DEFAULTS[key])) - SCENARIO_DEFAULTS[key]) > 1e-12:
            return label, float(params[key])
    return "Policy Rate Shock", 0.0


def build_saved_template_values(template_name: str, params: Dict[str, float]) -> dict:
    values = template_values(template_name)
    if template_name == "Individual Parameter Scenario":
        label, value = params_to_individual_driver(params)
        values["individual_parameter"], values["individual_value"] = label, value
        return values
    if template_name == "Base Case":
        return values
    allowed = set(SCENARIO_TEMPLATES[template_name].get("visible", []))
    for key in PARAM_SPECS:
        if key in allowed and key in params:
            values[key] = float(params[key])
    return values


# ----------------------------------------------------------------------------
# Local AI (Phi-3 explanation + Free Type parsing)
# ----------------------------------------------------------------------------
def ollama_status(host, model):
    try:
        import requests
        r = requests.get(host.rstrip("/") + "/api/tags", timeout=3)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
        installed = any(model.lower() in n.lower() or n.lower().startswith(model.lower() + ":") for n in names)
        return True, installed, names
    except Exception as exc:
        return False, False, str(exc)


def ask_ollama(host, model, payload):
    try:
        import requests
        running, installed, info = ollama_status(host, model)
        if not running:
            return f"Phi-3 unavailable: Ollama is not running at {host}. Start Ollama and try again."
        if not installed:
            return f"Phi-3 unavailable: model '{model}' is not installed. Available: {', '.join(info) if isinstance(info, list) else info}"
        prompt = (
            "You are a Senior Asset and Liability Management (ALM) Analyst at the National Bank of Egypt (NBE). "
            "Your task is to provide an executive-level analysis of the supplied deterministic ALM simulation results. "
            "STRICT CONSTRAINTS:\n"
            "- Do NOT change, invent, or calculate new numerical values. Python is the sole source of numbers.\n"
            "- Base your entire analysis strictly on the provided JSON payload.\n\n"
            "Please structure your response into the following clear sections:\n"
            "1. Executive Summary: A concise overview of the scenario's overall effect.\n"
            "2. Key Financial Drivers: Analyze the main drivers impacting NII, NIM, Cost of Funds, and P&L.\n"
            "3. Risk & Liquidity Impact: Discuss changes in LCR and overall risk posture.\n"
            "4. Strategic ALCO Recommendations: Provide actionable, professional recommendations for the ALCO committee based strictly on these results.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )
        response = requests.post(
            host.rstrip("/") + "/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "options": {"temperature": OLLAMA_TEMPERATURE}},
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status()
        return response.json().get("response", "No response.")
    except Exception as exc:
        return f"Phi-3 request failed: {exc}"


def clean_numeric_value(val):
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace(",", "").lower()
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s)
    if not m:
        return None
    f = float(m.group(0))
    if "bps" in s:
        return f / 100.0
    return f


_NUM = r"([+-]?\d+(?:\.\d+)?)\s*(%|pp|bps|bp|نقطة أساس|نقطة)?"
_DECREASE_WORDS = ["decrease", "reduction", "reduce", "drop", "fall", "decline", "cut", "lower", "انخفاض", "تراجع", "خفض", "سحب"]
_REGEX_PATTERNS = [
    ("deposit_runoff_pct", r"(?:run[- ]?off|outflows?|withdraw\w*|سحوبات|سحب)\D{0,25}?" + _NUM),
    ("fx_shock_pct", r"(?:\bfx\b|dollar|usd|currency|exchange rate|الدولار|العملة)\D{0,25}?" + _NUM),
    ("gold_shock_pct", r"(?:gold|ذهب|الذهب)\D{0,25}?" + _NUM),
    ("inflation_shock_pct", r"(?:inflation|تضخم|التضخم)\D{0,25}?" + _NUM),
    ("unemployment_shock_pct", r"(?:unemployment|بطالة|البطالة)\D{0,25}?" + _NUM),
    ("gdp_shock_pct", r"(?:\bgdp\b|economic growth|نمو)\D{0,25}?" + _NUM),
    ("deposit_growth_pct", r"(?:deposit\w*|ودائع|الودائع)\D{0,25}?" + _NUM),
    ("loan_growth_pct", r"(?:loan\w*|قروض|القروض)\D{0,25}?" + _NUM),
    ("rate_change_pp", r"(?:rate|rates|interest|policy|فائدة|فائده)\D{0,25}?" + _NUM),
]


def extract_parameters_regex(text_val: str) -> Dict[str, float]:
    res: Dict[str, float] = {}
    work = str(text_val or "").lower()
    for key, pat in _REGEX_PATTERNS:
        m = re.search(pat, work, re.I)
        if not m:
            continue
        v = float(m.group(1))
        unit = (m.group(2) or "").lower()
        if unit in {"bps", "bp"} or "نقطة" in unit:
            v /= 100.0
        clause = re.split(r"[,;.]| and | while | but | و", work[:m.start()])[-1] + work[m.start():m.end()]
        if key == "deposit_runoff_pct":
            v = abs(v)
        elif v > 0 and any(w in clause for w in _DECREASE_WORDS):
            v = -v
        res[key] = v
        work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]
    return res


def parse_free_type_scenario_with_ollama(host, model, scenario_text):
    text_value = str(scenario_text or "").strip()
    if not text_value:
        raise ValueError("Scenario text is empty.")
    allowed = list(PARAM_SPECS.keys())
    result = {k: None for k in allowed}
    parser_source = "regex_fallback"
    try:
        import requests
        running, installed, _ = ollama_status(host, model)
        if running and installed:
            parser_source = "phi3"
            units = ", ".join(f"{k} ({PARAM_SPECS[k][1]})" for k in allowed)
            prompt = (
                "Extract explicit ALM scenario assumptions from the text. Return ONLY JSON with numeric values. "
                "Keys and units: " + units + ". A rate shock of +300 bps or +3% means rate_change_pp = 3.\nScenario: " + text_value
            )
            r = requests.post(
                host.rstrip("/") + "/api/generate",
                json={"model": model, "prompt": prompt, "stream": False, "format": "json", "options": {"temperature": 0}},
                timeout=OLLAMA_TIMEOUT,
            )
            r.raise_for_status()
            parsed = json.loads(r.json().get("response", "").strip())
            for k in allowed:
                v = clean_numeric_value(parsed.get(k)) if isinstance(parsed, dict) else None
                if v is not None:
                    result[k] = v
    except Exception:
        parser_source = "regex_fallback"
    for k, v in extract_parameters_regex(text_value).items():
        if k in result and result.get(k) is None:
            result[k] = v
    return {"scenario_name": "Free Type Scenario", "parameters": result, "parser_source": parser_source}