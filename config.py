"""Central configuration for the A-share risk control system.

All model thresholds, weights, cache settings and optional tokens live here so
future tuning does not require editing the product pages or model functions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # dotenv is optional at runtime. Environment variables still work without it.
    pass


BASE_DIR = Path(__file__).resolve().parent
APP_HOME = Path(os.environ.get("ASHARE_RISK_ROOT", BASE_DIR.parents[1]))
APP_DIR = APP_HOME / "app"
DATA_DIR = Path(os.environ.get("ASHARE_RISK_DATA", APP_HOME / "data"))
OUTPUTS = Path(os.environ.get("ASHARE_RISK_OUTPUTS", APP_HOME / "outputs"))
WORKBOOK = Path(os.environ.get("ASHARE_RISK_WORKBOOK", OUTPUTS / "A股风控模型_东方财富自动更新版.xlsx"))
CACHE_DIR = Path(os.environ.get("ASHARE_RISK_CACHE_DIR", BASE_DIR / ".cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

USE_MOCK_DATA = os.environ.get("ASHARE_RISK_USE_MOCK", "0").strip() in {"1", "true", "TRUE", "yes", "YES"}
MOCK_ON_FAILURE = os.environ.get("ASHARE_RISK_MOCK_ON_FAILURE", "0").strip() in {"1", "true", "TRUE", "yes", "YES"}
ALLOW_MOCK_TOGGLE = os.environ.get("ASHARE_RISK_ALLOW_MOCK_TOGGLE", "0").strip() in {"1", "true", "TRUE", "yes", "YES"}
FAST_START = os.environ.get("ASHARE_RISK_FAST_START", "1").strip() not in {"0", "false", "FALSE", "no", "NO"}
CLOUD_FAST_MODE = os.environ.get("ASHARE_RISK_CLOUD_FAST_MODE", os.environ.get("RENDER", "0")).strip() in {"1", "true", "TRUE", "yes", "YES"}
REALTIME_REFRESH_SECONDS = int(os.environ.get("ASHARE_REALTIME_REFRESH_SECONDS", "1"))
AUTO_REFRESH_DEFAULT = os.environ.get("ASHARE_AUTO_REFRESH_DEFAULT", "0").strip() not in {"0", "false", "FALSE", "no", "NO"}
MARKET_SNAPSHOT_MAX_AGE_SECONDS = int(os.environ.get("ASHARE_MARKET_SNAPSHOT_MAX_AGE_SECONDS", "1800"))
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-pro")


DEFAULT_INDEX_NAME = "沪深300"
INDEX_OPTIONS = {
    "沪深300": {"code": "000300", "ak_symbol": "sh000300"},
    "上证指数": {"code": "000001", "ak_symbol": "sh000001"},
    "科创板指数": {"code": "000688", "ak_symbol": "sh000688"},
    "中证500": {"code": "000905", "ak_symbol": "sh000905"},
    "创业板指": {"code": "399006", "ak_symbol": "sz399006"},
}

BROAD_INDEX_ETFS = {
    "沪深300": ["510300", "159919"],
    "上证指数": ["510050", "510180"],
    "科创板指数": ["588000", "588080"],
    "创业板指": ["159915", "159949"],
    "中证500": ["510500"],
}

THEME_ETF_MAP = {
    "PCB板块": ["515260", "159997", "159732"],
    "CPO板块": ["515050", "515880", "159994"],
    "半导体": ["512480", "512760", "159995"],
    "新能源车": ["515030"],
    "银行": ["512800"],
    "光伏": ["515790"],
    "医药": ["512010", "512170"],
    "酒": ["512690"],
    "军工": ["512660"],
    "有色金属": ["512400"],
}


MARKET_POSITION_BANDS = [
    (20, "市场强势", "80%-100%", 0.90),
    (40, "正常偏强", "60%-80%", 0.70),
    (60, "震荡", "30%-60%", 0.45),
    (80, "高风险", "10%-30%", 0.20),
    (101, "系统性风险", "0%-10%", 0.05),
]

STOCK_RISK_BANDS = [
    (20, "低风险", "可正常观察或交易"),
    (40, "中风险", "轻度控制仓位"),
    (60, "较高风险", "只允许半仓以内"),
    (80, "高风险", "只允许小仓位"),
    (101, "禁止买入", "禁止买入或必须减仓"),
]


MARKET_RISK_WEIGHTS = {
    "trend": 25,
    "breadth": 20,
    "limit_down": 15,
    "amount": 15,
    "volatility": 15,
    "drawdown": 10,
}

STOCK_RISK_WEIGHTS = {
    "volatility": 25,
    "trend": 25,
    "liquidity": 15,
    "drawdown": 15,
    "volume_anomaly": 10,
    "special": 10,
}

THEME_RISK_WEIGHTS = {
    "average_stock_risk": 30,
    "crowding": 20,
    "adjustment": 20,
    "retreat": 20,
    "divergence": 10,
}


@dataclass(frozen=True)
class ModelParams:
    """Risk model parameters and default thresholds."""

    ma_fast: int = 20
    ma_slow: int = 60
    atr_window: int = 14
    volatility_window: int = 20
    drawdown_window: int = 20
    amount_window: int = 20
    low_liquidity_amount: float = 50_000_000
    very_low_liquidity_amount: float = 20_000_000
    volume_down_return_threshold: float = -3.0
    volume_spike_multiplier: float = 1.5
    near_limit_down_pct: float = -8.5
    concentration_stock_warning: float = 0.20
    concentration_theme_warning: float = 0.35
    market_ban_threshold: float = 80.0
    stock_ban_threshold: float = 80.0
    account_drawdown_reduce_threshold: float = 10.0
    hot_theme_crowding_threshold: float = 70.0
    theme_adjustment_warning_threshold: float = 60.0
    index_adjustment_warning_threshold: float = 60.0


PARAMS = ModelParams()

CACHE_TTL_SECONDS = {
    "spot": 60,
    "history": 60 * 60 * 4,
    "index": 60 * 30,
    "theme": 60 * 30,
}

DEFAULT_THEMES = {
    "PCB板块": ["002463", "300308", "688183", "603228"],
    "CPO板块": ["300308", "300502", "688205"],
    "半导体": ["512480", "688981", "603986", "688012", "688256"],
    "新能源车": ["515030", "300750", "002594", "601689"],
    "银行": ["512800", "600036", "601398", "601939"],
}

SAMPLE_PORTFOLIO = [
    {"股票代码": "300308", "持仓股数": 1000, "成本价": 60.0, "当前价": 60.0, "所属板块": "CPO"},
    {"股票代码": "002463", "持仓股数": 1500, "成本价": 35.0, "当前价": 35.0, "所属板块": "PCB"},
]
