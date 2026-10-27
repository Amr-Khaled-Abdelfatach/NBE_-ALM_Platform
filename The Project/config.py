"""Global configuration and model parameters for NBE ALM (Simplified & Verified)."""
import os

APP_NAME = "NBE ALM Simulation Center"
APP_VERSION = "2.1.0"
CALCULATION_ENGINE_VERSION = 2

# ----------------------------------------------------------------------------
# Model conventions
# ----------------------------------------------------------------------------
MODEL_AMOUNT_UNIT = "EGP"
RATE_STORAGE_CONVENTION = "decimal_fraction"   # 0.1225 == 12.25 %
DAYS_PER_YEAR = 365.0
DEFAULT_HORIZON_DAYS = 365
DEFAULT_CURRENCY = "EGP"
DATABASE_NAME = "nbe_alm_simulation"
RATE_FLOOR = 0.0

# ----------------------------------------------------------------------------
# Database connection
# ----------------------------------------------------------------------------
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", DATABASE_NAME)
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_DRIVER = os.getenv("DB_DRIVER", "mysql+pymysql")
DB_POOL_PRE_PING = True
DB_POOL_RECYCLE = 1800
DB_READ_LIMIT = 100000

REQUIRED_TABLES = [
    "products", "positions", "cash_flows", "market_data",
    "scenarios", "simulation_runs", "simulation_results",
]
FLAT_POSITIONS_VIEW = "vw_alm_flat_table"
FLAT_CASHFLOWS_VIEW = "vw_alm_cashflows_flat"
OPTIONAL_VIEWS = [FLAT_POSITIONS_VIEW, FLAT_CASHFLOWS_VIEW]

# ----------------------------------------------------------------------------
# Market data / valuation conventions
# ----------------------------------------------------------------------------
VALUATION_INSTRUMENTS = ["FX_USD_EGP", "FX_EUR_EGP", "FX_GBP_EGP", "FX_SAR_EGP", "GOLD_24K_EGP"]
MACRO_CODES = {
    "policy_rate": "CBE_POLICY_RATE",
    "inflation": "MACRO_INFLATION",
    "unemployment": "MACRO_UNEMPLOYMENT",
    "gdp_growth": "MACRO_GDP_GROWTH",
}
FUNDING_SEGMENTS = ["Treasury"]
EXCLUDE_MATURED_POSITIONS = True
ROLLOVER_AT_MATURITY = True

# Fixed, transparent credit-cost sensitivity constants (bps of loans per 1 pp adverse macro shock)
CREDIT_COST_BPS_PER_UNEMP_PP = 25.0
CREDIT_COST_BPS_PER_GDP_DROP_PP = 20.0

# ----------------------------------------------------------------------------
# Repricing / frequency parsing
# ----------------------------------------------------------------------------
TENOR_DAY_FACTORS = {"D": 1.0, "W": 7.0, "M": 30.4375, "Y": 365.0}
FREQUENCY_WORD_DAYS = {
    "DAILY": 1, "DAILY SPOT": 1, "WEEKLY": 7, "MONTHLY": 30, "QUARTERLY": 91,
    "SEMI-ANNUAL": 182, "SEMI ANNUAL": 182, "SEMIANNUAL": 182,
    "ANNUAL": 365, "ANNUALLY": 365, "YEARLY": 365,
    "AT MATURITY": None, "SPOT": None, "OPEN-ENDED": None, "NONE": None,
}

# ----------------------------------------------------------------------------
# Balanced Liquidity & LCR Proxy conventions
# ----------------------------------------------------------------------------
LCR_WINDOW_DAYS = 30
LCR_DEPOSIT_STRESS_FLOOR_PCT = 0.15
MATURITY_BUCKET_ORDER = ["0-7 Days", "8-30 Days", "31-90 Days", "91-180 Days", "181-365 Days", "Over 1 Year"]

# ----------------------------------------------------------------------------
# Scenario parameters (1-to-1 match with the 9 columns in `scenarios` table)
# ----------------------------------------------------------------------------
PARAM_SPECS = {
    # --- Market Shocks ---
    "rate_change_pp":          ("Policy / market rate shock", "pp", -1000.0, 1000.0, 0.0, 0.25, "Market"),
    "fx_shock_pct":            ("FX shock (USD/EGP & FCY)", "%", -500.0, 500.0, 0.0, 1.0, "Market"),
    "gold_shock_pct":          ("Gold price shock", "%", -500.0, 500.0, 0.0, 1.0, "Market"),
    # --- Balance Sheet & Liquidity ---
    "deposit_runoff_pct":      ("Deposit run-off (outflow)", "%", 0.0, 100.0, 0.0, 1.0, "Balance sheet & liquidity"),
    "loan_growth_pct":         ("Loan growth", "%", -100.0, 100.0, 0.0, 1.0, "Balance sheet & liquidity"),
    "deposit_growth_pct":      ("Deposit growth", "%", 0.0, 100.0, 0.0, 1.0, "Balance sheet & liquidity"),
    # --- Macroeconomic Shocks ---
    "inflation_shock_pct":     ("Inflation shock", "pp", -200.0, 200.0, 0.0, 0.5, "Macro"),
    "unemployment_shock_pct":  ("Unemployment shock", "pp", -100.0, 100.0, 0.0, 0.5, "Macro"),
    "gdp_shock_pct":           ("GDP growth shock", "pp", -200.0, 200.0, 0.0, 0.5, "Macro"),




}

SCENARIO_DEFAULTS = {k: v[4] for k, v in PARAM_SPECS.items()}
SCENARIO_LIMITS = {k: (v[2], v[3]) for k, v in PARAM_SPECS.items()}
PARAMETER_DEFAULTS = SCENARIO_DEFAULTS

INDIVIDUAL_DRIVERS = {
    "Policy Rate Shock": "rate_change_pp",
    "FX Shock (USD/EGP)": "fx_shock_pct",
    "Gold Price Shock": "gold_shock_pct",
    "Deposit Run-off": "deposit_runoff_pct",
    "Loan Growth": "loan_growth_pct",
    "Deposit Growth": "deposit_growth_pct",
    "Inflation Shock": "inflation_shock_pct",
    "Unemployment Shock": "unemployment_shock_pct",
    "GDP Growth Shock": "gdp_shock_pct",
}

SCENARIO_TEMPLATES = {
    "Base Case": {
        "scenario_type": "BASE",
        "description": "Baseline scenario with no stress assumptions.",
        "visible": [],
        "values": {},
    },
    "Combined Stress Scenario": {
        "scenario_type": "COMBINED_STRESS",
        "description": "Combined multi-factor stress testing across all database scenario variables.",
        "visible": list(PARAM_SPECS.keys()),
        "values": {k: 0.0 for k in PARAM_SPECS},  # <-- جعل القيم الافتراضية كلها تبدأ من الصفر
    },
    "Individual Parameter Scenario": {
        "scenario_type": "INDIVIDUAL_PARAMETER",
        "description": "Choose ONE primary scenario driver and set its value while keeping all other assumptions neutral.",
        "visible": ["individual_parameter", "individual_value"],
        "values": {},
        "individual_parameter": "Policy Rate Shock",
        "individual_value": 0.0,
    },
    "Free Type Scenario": {
        "scenario_type": "FREE_TYPE",
        "description": "Describe the scenario in natural language.",
        "visible": ["text_prompt"],
        "values": {},
    },

}
PREDEFINED_TEMPLATE_NAMES = list(SCENARIO_TEMPLATES.keys())

# استبدل PAGES و PAGE_ICONS في config.py بالتالي:
PAGES = {
    "Dashboard": "dashboard",
    "Scenarios": "scenarios",
    "Documentation": "documentation",
    "Data & Settings": "system",
}

PAGE_ICONS = {
    "Dashboard": "",
    "Scenarios": "",
    "Documentation": "",
    "Data & Settings": "    ",
}


HORIZON_PRESETS = {
    "30 days": 30,
    "90 days (quarter)": 90,
    "180 days": 180,
    "365 days (1 year - standard)": 365,
    "730 days (2 years)": 730,
}

# Overall-risk rating thresholds
RISK_NII_HIGH_DROP_PCT = -10.0
RISK_NII_MEDIUM_DROP_PCT = -3.0
RISK_COF_MEDIUM_RISE_BPS = 50.0
RISK_LCR_MEDIUM_RELATIVE_DROP_PCT = -25.0

# Local AI / explanation-only configuration
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() == "true"
AI_EXPLANATION_ONLY = True
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "phi3")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "800"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.0"))

# Validation thresholds
CALCULATION_TOLERANCE = float(os.getenv("CALCULATION_TOLERANCE", "1e-6"))
RATE_WARNING_THRESHOLD = float(os.getenv("RATE_WARNING_THRESHOLD", "2.0"))
LCR_WARNING_THRESHOLD = float(os.getenv("LCR_WARNING_THRESHOLD", "100.0"))
MARKET_DATA_STALE_DAYS = int(os.getenv("MARKET_DATA_STALE_DAYS", "7"))

# Streamlit / UI
PAGE_TITLE = APP_NAME
PAGE_ICON = "🏦"
PAGE_LAYOUT = "wide"
INITIAL_SIDEBAR_STATE = "expanded"
CHART_HEIGHT = 400
CHART_MARGIN = dict(l=20, r=20, t=50, b=40)
CHART_FONT = "Arial"
UI_BORDER_RADIUS = 14
UI_BUTTON_HEIGHT = 42
DEFAULT_TABLE_HEIGHT = 420
DATA_CACHE_TTL_SECONDS = 120

# NBE-inspired visual palette
NBE_GREEN = "#007A3D"
NBE_DARK_GREEN = "#005B2D"
NBE_ORANGE = "#F7941D"
NBE_LIGHT_GREEN = "#EAF5EF"
NBE_PALE_ORANGE = "#FFF3E0"
NBE_TEXT = "#173B2A"
NBE_MUTED = "#66786D"
NBE_PALETTE = [NBE_GREEN, NBE_ORANGE, NBE_DARK_GREEN, "#2E8B57", "#F6A64B", "#73B88D"]

# ----------------------------------------------------------------------------
# Documentation & External Links
# ----------------------------------------------------------------------------
DOCS_ENABLED = True
DOCS_BASE_URL = "https://amr-khaled-abdelfatach.github.io/NBE_-ALM_Platform/"

PAGE_DOCS = {
    "Dashboard": "",
    "Scenarios": "scenarios/",
    "Documentation": "",
    "Data & Settings": "database/",
}

DOCS_LINKS = {
    "Market-rate assumption": "scenarios/#market-rate-assumption",
    "Scenario assumptions": "scenarios/#scenario-assumptions",
    "Live calculation preview": "scenarios/#live-calculation-preview",
    "ALM Analytics": "analytics/",
    "Calculation Audit": "calculation-engine/",
}