"""Risk scoring models for market, stock, theme, position and portfolio."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from config import (
    MARKET_POSITION_BANDS,
    MARKET_RISK_WEIGHTS,
    PARAMS,
    STOCK_RISK_BANDS,
    STOCK_RISK_WEIGHTS,
    THEME_RISK_WEIGHTS,
)
from data_loader import mock_stock_name, normalize_stock_code


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    """Clamp a numeric value to a fixed range."""

    if value is None or math.isnan(float(value)):
        return low
    return max(low, min(high, float(value)))


def moving_average(series: pd.Series, window: int) -> pd.Series:
    """Compute a simple moving average."""

    return pd.to_numeric(series, errors="coerce").rolling(window).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    """Compute true range from OHLC data."""

    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev_close = close.shift(1)
    ranges = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1)
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, window: int | None = None) -> pd.Series:
    """Compute Average True Range without TA-Lib."""

    window = window or PARAMS.atr_window
    return true_range(df).rolling(window).mean()


def annualized_volatility(df: pd.DataFrame, window: int | None = None) -> float:
    """Compute annualized close-to-close volatility in percent."""

    window = window or PARAMS.volatility_window
    returns = pd.to_numeric(df["close"], errors="coerce").pct_change().tail(window)
    if returns.dropna().empty:
        return 0.0
    return float(returns.std(ddof=1) * math.sqrt(252) * 100)


def max_drawdown(close: pd.Series, window: int | None = None) -> float:
    """Compute max drawdown over the latest window as a negative percent."""

    window = window or PARAMS.drawdown_window
    values = pd.to_numeric(close, errors="coerce").tail(window).dropna()
    if values.empty:
        return 0.0
    drawdown = values / values.cummax() - 1
    return float(drawdown.min() * 100)


def drawdown_series(close: pd.Series, window: int | None = None) -> pd.Series:
    """Compute drawdown curve for charting."""

    window = window or PARAMS.drawdown_window
    values = pd.to_numeric(close, errors="coerce").tail(window)
    return (values / values.cummax() - 1) * 100


def map_market_state(score: float) -> tuple[str, str, float]:
    """Map market risk score to state, suggested total position and midpoint."""

    for upper, state, position_text, midpoint in MARKET_POSITION_BANDS:
        if score < upper:
            return state, position_text, midpoint
    return "系统性风险", "0%-10%", 0.05


def map_stock_level(score: float) -> tuple[str, str]:
    """Map stock risk score to a risk level and action guidance."""

    for upper, level, action in STOCK_RISK_BANDS:
        if score < upper:
            return level, action
    return "禁止买入", "禁止买入或必须减仓"


def component_score(value: float, weight: float) -> float:
    """Clamp one 0-100 raw component and scale it by its weight."""

    return clamp(value) * weight / 100


def compute_market_risk(index_df: pd.DataFrame, breadth: dict[str, Any]) -> dict[str, Any]:
    """Build Market Risk Score from trend, breadth, limit-down, amount, volatility and drawdown."""

    df = index_df.copy()
    close = pd.to_numeric(df["close"], errors="coerce")
    amount = pd.to_numeric(df.get("amount", pd.Series(dtype=float)), errors="coerce")
    latest_close = float(close.iloc[-1])
    ma20 = float(moving_average(close, PARAMS.ma_fast).iloc[-1])
    ma60 = float(moving_average(close, PARAMS.ma_slow).iloc[-1])

    trend_raw = 0.0
    if latest_close < ma20:
        trend_raw += 45
    if latest_close < ma60:
        trend_raw += 45
    if ma20 < ma60:
        trend_raw += 10

    up_ratio = float(breadth.get("up_ratio", 0.0))
    breadth_raw = (1 - up_ratio) * 100

    total = max(float(breadth.get("total", 1)), 1.0)
    limit_down_count = float(breadth.get("limit_down_count", 0))
    limit_down_raw = clamp(limit_down_count / max(total * 0.03, 20) * 100)

    latest_amount = float(amount.iloc[-1]) if not amount.dropna().empty else float(breadth.get("total_amount", 0))
    avg_amount20 = float(amount.tail(20).mean()) if amount.tail(20).notna().any() else latest_amount
    amount_raw = clamp((avg_amount20 - latest_amount) / avg_amount20 * 100) if avg_amount20 > 0 else 50

    vol20 = annualized_volatility(df, 20)
    vol_raw = clamp(vol20 / 35 * 100)

    mdd20 = max_drawdown(close, 20)
    drawdown_raw = clamp(abs(mdd20) / 12 * 100)

    raw_components = {
        "指数趋势风险": trend_raw,
        "市场广度风险": breadth_raw,
        "跌停风险": limit_down_raw,
        "成交额风险": amount_raw,
        "波动率风险": vol_raw,
        "最大回撤风险": drawdown_raw,
    }
    weighted_components = {
        "指数趋势风险": component_score(trend_raw, MARKET_RISK_WEIGHTS["trend"]),
        "市场广度风险": component_score(breadth_raw, MARKET_RISK_WEIGHTS["breadth"]),
        "跌停风险": component_score(limit_down_raw, MARKET_RISK_WEIGHTS["limit_down"]),
        "成交额风险": component_score(amount_raw, MARKET_RISK_WEIGHTS["amount"]),
        "波动率风险": component_score(vol_raw, MARKET_RISK_WEIGHTS["volatility"]),
        "最大回撤风险": component_score(drawdown_raw, MARKET_RISK_WEIGHTS["drawdown"]),
    }
    score = clamp(sum(weighted_components.values()))
    state, suggested_position, position_midpoint = map_market_state(score)
    adjustment = compute_index_adjustment(index_df)

    return {
        "score": score,
        "state": state,
        "suggested_position": suggested_position,
        "position_midpoint": position_midpoint,
        "raw_components": raw_components,
        "weighted_components": weighted_components,
        "ma20": ma20,
        "ma60": ma60,
        "latest_close": latest_close,
        "vol20": vol20,
        "mdd20": mdd20,
        "avg_amount20": avg_amount20,
        "latest_amount": latest_amount,
        "index_adjustment": adjustment,
    }


def compute_index_adjustment(index_df: pd.DataFrame) -> dict[str, Any]:
    """Measure how much the selected index has adjusted from recent highs."""

    df = index_df.copy()
    close = pd.to_numeric(df["close"], errors="coerce").dropna()
    if close.empty:
        return {"score": 0, "state": "等待数据", "explanation": "指数数据不足。"}
    latest = float(close.iloc[-1])
    high20 = float(close.tail(20).max())
    high60 = float(close.tail(60).max())
    ma20 = float(moving_average(close, 20).iloc[-1])
    ma60 = float(moving_average(close, 60).iloc[-1])
    drawdown20 = latest / high20 - 1 if high20 else 0
    drawdown60 = latest / high60 - 1 if high60 else 0
    ma20_gap = latest / ma20 - 1 if ma20 else 0
    ma60_gap = latest / ma60 - 1 if ma60 else 0
    raw = clamp(abs(drawdown20) * 180 + abs(drawdown60) * 120 + max(-ma20_gap, 0) * 180 + max(-ma60_gap, 0) * 120)
    if raw >= 75:
        state = "深度调整"
    elif raw >= 55:
        state = "中度调整"
    elif raw >= 35:
        state = "轻度调整"
    else:
        state = "高位或温和整理"
    return {
        "score": raw,
        "state": state,
        "drawdown20": drawdown20 * 100,
        "drawdown60": drawdown60 * 100,
        "ma20_gap": ma20_gap * 100,
        "ma60_gap": ma60_gap * 100,
        "explanation": f"指数距20日高点 {drawdown20 * 100:.1f}%，距60日高点 {drawdown60 * 100:.1f}%，当前为{state}。",
    }


def etf_flow_risk(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert realtime ETF flow proxy rows into a 0-100 risk pressure score."""

    if not rows:
        return {"score": 50.0, "direction": "等待ETF数据", "explanation": "尚未读取到ETF流向数据。"}
    scores = []
    for row in rows:
        change_pct = float(row.get("change_pct") or 0)
        direction = str(row.get("flow_direction") or "中性")
        signal = str(row.get("signal") or "")
        risk = 50.0
        if direction == "流出":
            risk += 18
        elif direction == "流入":
            risk -= 14
        if change_pct < -1:
            risk += min(18, abs(change_pct) * 3)
        elif change_pct > 1:
            risk -= min(12, change_pct * 2)
        if "异常流出" in signal:
            risk += 16
        elif "异常流入" in signal:
            risk -= 12
        elif "放量流出" in signal:
            risk += 8
        elif "放量流入" in signal:
            risk -= 6
        weight = max(float(row.get("amount") or row.get("interval_amount") or 1), 1)
        scores.append((clamp(risk), weight))
    total_weight = sum(w for _, w in scores)
    score = sum(s * w for s, w in scores) / total_weight if total_weight else 50.0
    direction = "ETF资金偏流出" if score >= 58 else "ETF资金偏流入" if score <= 42 else "ETF资金中性"
    explanation = f"{direction}，ETF资金压力分 {score:.1f}。该指标为二级市场成交额增量代理，不等同于申赎净流入。"
    return {"score": clamp(score), "direction": direction, "explanation": explanation}


def enhance_market_with_etf_flow(market: dict[str, Any], broad_etf_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Blend broad-index ETF flow pressure into Market Risk Score."""

    result = dict(market)
    flow = etf_flow_risk(broad_etf_rows)
    adjustment = (flow["score"] - 50) * 0.22
    result["base_score_without_etf"] = market["score"]
    result["score"] = clamp(market["score"] + adjustment)
    result["state"], result["suggested_position"], result["position_midpoint"] = map_market_state(result["score"])
    result["etf_flow"] = flow
    result["raw_components"] = dict(market.get("raw_components", {}), ETF资金风险=flow["score"])
    result["weighted_components"] = dict(market.get("weighted_components", {}), ETF资金调整=adjustment)
    return result


def analyze_price_volume_node(df: pd.DataFrame, flow_row: dict[str, Any] | None = None) -> dict[str, Any]:
    """Diagnose the current market node from ETF/index K-line trend and volume."""

    data = df.copy()
    close = pd.to_numeric(data["close"], errors="coerce")
    amount = pd.to_numeric(data.get("amount", pd.Series(dtype=float)), errors="coerce")
    if close.dropna().empty:
        return {"node": "等待数据", "risk_level": "未知", "guidance": "等待实时行情数据更新。", "score": 50}

    latest = float(close.iloc[-1])
    ma20 = float(moving_average(close, 20).iloc[-1]) if len(close) >= 20 else latest
    ma60 = float(moving_average(close, 60).iloc[-1]) if len(close) >= 60 else latest
    ret5 = float((close.iloc[-1] / close.iloc[-6] - 1) * 100) if len(close) > 6 else 0.0
    ret20 = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) > 21 else 0.0
    latest_amount = float(amount.iloc[-1]) if amount.notna().any() else 0.0
    avg_amount20 = float(amount.tail(20).mean()) if amount.tail(20).notna().any() else latest_amount
    amount_ratio = latest_amount / avg_amount20 if avg_amount20 else 1.0
    mdd20 = max_drawdown(close, 20)
    trend_up = latest > ma20 > ma60
    trend_down = latest < ma20 < ma60
    flow_direction = str((flow_row or {}).get("flow_direction") or "中性")
    flow_signal = str((flow_row or {}).get("signal") or "正常")

    if trend_up and ret20 > 8 and amount_ratio > 1.5:
        node = "趋势加速/拥挤升温"
        risk_level = "中高"
        guidance = "风险提示：不宜追高加仓，已有仓位建议用移动止损管理；新增仓位只适合小额试探并等待回撤确认。"
        score = 68
    elif trend_up and flow_direction == "流入":
        node = "趋势延续/资金确认"
        risk_level = "中"
        guidance = "仓位建议：可维持风险预算内的观察或分批配置，但必须设置回撤止损，避免单次重仓。"
        score = 42
    elif latest < ma20 and amount_ratio > 1.3 and flow_direction == "流出":
        node = "放量破位/退潮"
        risk_level = "高"
        guidance = "风险提示：停止新增仓位，已有仓位优先降风险；等待重新站回20日线并缩量企稳后再复核。"
        score = 78
    elif trend_down:
        node = "下行趋势/防守优先"
        risk_level = "高"
        guidance = "风险提示：以现金和仓位控制为主，不做确定性买入；若参与只考虑极小仓位和明确止损。"
        score = 72
    elif mdd20 < -8 and ret5 > 2 and flow_direction != "流出":
        node = "调整后修复观察"
        risk_level = "中"
        guidance = "仓位建议：可以进入观察清单，等待量价温和修复；不把反弹直接当成趋势反转。"
        score = 48
    else:
        node = "震荡均衡"
        risk_level = "中性"
        guidance = "仓位建议：控制单笔风险，等待方向选择；突破或跌破关键均线后再调整仓位上限。"
        score = 55

    if "异常流出" in flow_signal:
        score = clamp(score + 10)
        risk_level = "高" if score >= 70 else risk_level
    elif "异常流入" in flow_signal:
        score = clamp(score - 8)

    return {
        "node": node,
        "risk_level": risk_level,
        "guidance": guidance,
        "score": clamp(score),
        "ret5": ret5,
        "ret20": ret20,
        "amount_ratio": amount_ratio,
        "mdd20": mdd20,
        "ma20_gap": (latest / ma20 - 1) * 100 if ma20 else 0,
        "ma60_gap": (latest / ma60 - 1) * 100 if ma60 else 0,
    }


def enhance_theme_with_etf(theme: dict[str, Any], etf_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Blend related sector ETF flow pressure into Theme Risk Score."""

    result = dict(theme)
    flow = etf_flow_risk(etf_rows)
    adjustment = (flow["score"] - 50) * 0.18
    result["base_score_without_etf"] = theme.get("score", 0)
    result["score"] = clamp(float(theme.get("score", 0)) + adjustment)
    result["etf_flow"] = flow
    result["etf_rows"] = etf_rows
    extra = " " + flow["explanation"] if etf_rows else " 暂未匹配到对应板块ETF流向。"
    result["explanation"] = str(theme.get("explanation", "")) + extra
    return result


def analyze_stock_risk(symbol: str, hist_df: pd.DataFrame, spot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compute technical metrics and Stock Risk Score for one stock."""

    code = normalize_stock_code(symbol)
    df = hist_df.copy()
    close = pd.to_numeric(df["close"], errors="coerce")
    amount = pd.to_numeric(df.get("amount", pd.Series(dtype=float)), errors="coerce")
    volume = pd.to_numeric(df.get("volume", pd.Series(dtype=float)), errors="coerce")
    latest_close = float(close.iloc[-1])
    name = str((spot or {}).get("name") or mock_stock_name(code))
    current_price = float((spot or {}).get("latest") or latest_close)

    ma20 = float(moving_average(close, 20).iloc[-1])
    ma60 = float(moving_average(close, 60).iloc[-1])
    ret20 = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) > 21 else 0.0
    ret60 = float((close.iloc[-1] / close.iloc[-61] - 1) * 100) if len(close) > 61 else 0.0
    atr_series = atr(df, 14)
    latest_atr = float(atr_series.iloc[-1]) if not atr_series.dropna().empty else 0.0
    vol20 = annualized_volatility(df, 20)
    mdd20 = max_drawdown(close, 20)
    avg_amount20 = float(amount.tail(20).mean()) if amount.tail(20).notna().any() else 0.0
    latest_return = float(close.pct_change().iloc[-1] * 100) if len(close) > 1 else 0.0
    latest_volume = float(volume.iloc[-1]) if volume.notna().any() else 0.0
    avg_volume20 = float(volume.tail(20).mean()) if volume.tail(20).notna().any() else 0.0

    below_ma20 = latest_close < ma20 if not math.isnan(ma20) else False
    below_ma60 = latest_close < ma60 if not math.isnan(ma60) else False
    volume_down = latest_return <= PARAMS.volume_down_return_threshold and latest_volume > avg_volume20 * PARAMS.volume_spike_multiplier
    near_limit_down = latest_return <= PARAMS.near_limit_down_pct
    special_name_risk = "ST" in name.upper() or "退" in name
    consecutive_down = close.tail(3).pct_change().dropna().lt(-0.03).sum() >= 2

    vol_raw = clamp(vol20 / 65 * 100)
    trend_raw = 0.0
    if below_ma20:
        trend_raw += 40
    if below_ma60:
        trend_raw += 45
    if ret20 < -8:
        trend_raw += 15
    liquidity_raw = 100 if avg_amount20 < PARAMS.very_low_liquidity_amount else 65 if avg_amount20 < PARAMS.low_liquidity_amount else 20
    drawdown_raw = clamp(abs(mdd20) / 30 * 100)
    volume_raw = 100 if volume_down else 40 if latest_volume > avg_volume20 * PARAMS.volume_spike_multiplier else 10
    special_raw = 0.0
    if special_name_risk:
        special_raw += 70
    if near_limit_down:
        special_raw += 45
    if consecutive_down:
        special_raw += 30
    special_raw = clamp(special_raw)

    raw_components = {
        "波动率风险": vol_raw,
        "趋势破坏风险": trend_raw,
        "流动性风险": liquidity_raw,
        "最大回撤风险": drawdown_raw,
        "成交量异常风险": volume_raw,
        "特殊风险": special_raw,
    }
    weighted_components = {
        "波动率风险": component_score(vol_raw, STOCK_RISK_WEIGHTS["volatility"]),
        "趋势破坏风险": component_score(trend_raw, STOCK_RISK_WEIGHTS["trend"]),
        "流动性风险": component_score(liquidity_raw, STOCK_RISK_WEIGHTS["liquidity"]),
        "最大回撤风险": component_score(drawdown_raw, STOCK_RISK_WEIGHTS["drawdown"]),
        "成交量异常风险": component_score(volume_raw, STOCK_RISK_WEIGHTS["volume_anomaly"]),
        "特殊风险": component_score(special_raw, STOCK_RISK_WEIGHTS["special"]),
    }
    score = clamp(sum(weighted_components.values()))
    if special_name_risk:
        score = max(score, 85)
    level, action = map_stock_level(score)

    major_risks = [name for name, raw in raw_components.items() if raw >= 60]
    if not major_risks:
        major_risks = ["暂无单项极端风险"]

    return {
        "code": code,
        "name": name,
        "current_price": current_price,
        "ret20": ret20,
        "ret60": ret60,
        "ma20": ma20,
        "ma60": ma60,
        "atr": latest_atr,
        "atr_series": atr_series,
        "vol20": vol20,
        "mdd20": mdd20,
        "avg_amount20": avg_amount20,
        "below_ma20": below_ma20,
        "below_ma60": below_ma60,
        "volume_down": volume_down,
        "near_limit_down": near_limit_down,
        "special_name_risk": special_name_risk,
        "score": score,
        "level": level,
        "action": action,
        "raw_components": raw_components,
        "weighted_components": weighted_components,
        "major_risks": major_risks,
    }


def market_position_factor(score: float) -> float:
    """Return position discount by market score."""

    if score < 20:
        return 1.0
    if score < 40:
        return 0.8
    if score < 60:
        return 0.5
    if score < 80:
        return 0.3
    return 0.1


def stock_position_factor(score: float) -> float:
    """Return position discount by stock score."""

    if score < 20:
        return 1.0
    if score < 40:
        return 0.8
    if score < 60:
        return 0.5
    if score < 80:
        return 0.2
    return 0.0


def calculate_position_size(
    account_value: float,
    risk_per_trade_pct: float,
    buy_price: float,
    stop_price: float,
    market_score: float,
    stock_score: float,
) -> dict[str, Any]:
    """Calculate risk-budgeted position size with market and stock discounts."""

    stop_width = abs(float(buy_price) - float(stop_price)) / float(buy_price) if buy_price else 0.0
    max_loss_amount = float(account_value) * float(risk_per_trade_pct) / 100
    base_position = max_loss_amount / stop_width if stop_width > 0 else 0.0
    market_factor = market_position_factor(market_score)
    stock_factor = stock_position_factor(stock_score)
    ban_reasons = []
    if stock_score >= PARAMS.stock_ban_threshold:
        stock_factor = 0.0
        ban_reasons.append("个股风险分超过80，最终仓位为0")
    if market_score >= PARAMS.market_ban_threshold:
        market_factor = 0.0
        ban_reasons.append("市场风险分超过80，禁止新开仓")
    final_amount = base_position * market_factor * stock_factor
    final_amount = min(final_amount, float(account_value))
    lot_value = float(buy_price) * 100
    shares = int(final_amount // lot_value * 100) if lot_value > 0 else 0
    final_amount_rounded = shares * float(buy_price)
    return {
        "account_value": account_value,
        "buy_price": buy_price,
        "stop_price": stop_price,
        "stop_width": stop_width,
        "max_loss_amount": max_loss_amount,
        "base_position": base_position,
        "market_factor": market_factor,
        "stock_factor": stock_factor,
        "final_amount": final_amount_rounded,
        "final_shares": shares,
        "ban_reasons": ban_reasons,
    }


def compute_theme_risk(theme_name: str, symbols: list[str], histories: dict[str, pd.DataFrame], spots: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compute risk score and ranking for a manually defined theme."""

    spots = spots or {}
    rows = []
    normalized_curves = []
    for code in symbols:
        clean = normalize_stock_code(code)
        df = histories.get(clean)
        if df is None or df.empty:
            continue
        stock = analyze_stock_risk(clean, df, spots.get(clean))
        close = pd.to_numeric(df["close"], errors="coerce")
        ret20 = stock["ret20"]
        latest_ret = float(close.pct_change().iloc[-1] * 100) if len(close) > 1 else 0.0
        amount = pd.to_numeric(df.get("amount", pd.Series(dtype=float)), errors="coerce")
        avg_amount20 = float(amount.tail(20).mean()) if amount.tail(20).notna().any() else 0.0
        amount_ratio = float(amount.iloc[-1] / avg_amount20) if avg_amount20 > 0 else 1.0
        volume_expanded = bool(amount_ratio > 1.3)
        ma20 = float(moving_average(close, 20).iloc[-1]) if len(close) >= 20 else float(close.iloc[-1])
        price_extension = float(close.iloc[-1] / ma20 - 1) * 100 if ma20 else 0.0
        mdd20 = max_drawdown(close, 20)
        rows.append(
            {
                "代码": clean,
                "名称": stock["name"],
                "20日涨跌幅": ret20,
                "今日涨跌幅": latest_ret,
                "成交额放大倍数": amount_ratio,
                "偏离20日线": price_extension,
                "近20日回撤": mdd20,
                "是否放量": volume_expanded,
                "跌破20日线": stock["below_ma20"],
                "个股风险分": stock["score"],
                "风险等级": stock["level"],
            }
        )
        curve = close.tail(60).reset_index(drop=True)
        if len(curve) > 1 and curve.iloc[0] != 0:
            normalized_curves.append(curve / curve.iloc[0] * 100)

    table = pd.DataFrame(rows).sort_values("个股风险分", ascending=False) if rows else pd.DataFrame()
    if table.empty:
        return {"theme_name": theme_name, "score": 0, "table": table, "avg_curve": pd.DataFrame(), "explanation": "没有可用股票数据。"}

    avg_ret20 = float(table["20日涨跌幅"].mean())
    up_ratio = float((table["今日涨跌幅"] > 0).mean())
    volume_ratio = float(table["是否放量"].mean())
    below_ma20_ratio = float(table["跌破20日线"].mean())
    avg_amount_ratio = float(table["成交额放大倍数"].clip(0, 5).mean())
    avg_extension = float(table["偏离20日线"].mean())
    avg_mdd20 = float(table["近20日回撤"].mean())
    avg_risk = float(table["个股风险分"].mean())
    top_ret = float(table["20日涨跌幅"].max())
    tail_ret = float(table["20日涨跌幅"].median())
    divergence_raw = clamp((top_ret - tail_ret) / 25 * 100)
    crowding_raw = clamp(max(avg_ret20, 0) / 25 * 35 + max(avg_extension, 0) / 18 * 25 + min(avg_amount_ratio, 3) / 3 * 25 + volume_ratio * 15)
    adjustment_raw = clamp(abs(min(avg_mdd20, 0)) / 25 * 45 + below_ma20_ratio * 35 + max(-avg_ret20, 0) / 20 * 20)
    retreat_raw = clamp(below_ma20_ratio * 100)

    score = clamp(
        component_score(avg_risk, THEME_RISK_WEIGHTS["average_stock_risk"])
        + component_score(crowding_raw, THEME_RISK_WEIGHTS["crowding"])
        + component_score(adjustment_raw, THEME_RISK_WEIGHTS["adjustment"])
        + component_score(retreat_raw, THEME_RISK_WEIGHTS["retreat"])
        + component_score(divergence_raw, THEME_RISK_WEIGHTS["divergence"])
    )

    if crowding_raw >= PARAMS.hot_theme_crowding_threshold and adjustment_raw < 45:
        explanation = "板块拥挤度较高：近期涨幅、成交额放大或偏离均线较明显，需警惕过多资金涌入后的波动放大。"
    elif adjustment_raw >= PARAMS.theme_adjustment_warning_threshold:
        explanation = "板块调整程度较高：回撤、跌破20日线或退潮比例已抬升。"
    elif score >= 75:
        explanation = "板块风险较高：平均个股风险、退潮比例或内部分歧已经偏高。"
    elif score >= 55:
        explanation = "板块进入观察区：拥挤度或跌破20日线比例需要持续跟踪。"
    else:
        explanation = "板块风险暂未极端，但仍需控制单票和板块集中度。"

    if normalized_curves:
        avg_curve = pd.concat(normalized_curves, axis=1).mean(axis=1).reset_index()
        avg_curve.columns = ["t", "平均走势"]
    else:
        avg_curve = pd.DataFrame()

    return {
        "theme_name": theme_name,
        "score": score,
        "avg_ret20": avg_ret20,
        "up_ratio": up_ratio,
        "volume_ratio": volume_ratio,
        "below_ma20_ratio": below_ma20_ratio,
        "avg_amount_ratio": avg_amount_ratio,
        "avg_extension": avg_extension,
        "avg_mdd20": avg_mdd20,
        "crowding_score": crowding_raw,
        "adjustment_score": adjustment_raw,
        "retreat_score": retreat_raw,
        "divergence_score": divergence_raw,
        "avg_risk": avg_risk,
        "table": table,
        "avg_curve": avg_curve,
        "explanation": explanation,
    }


def compute_portfolio_risk(holdings: pd.DataFrame, stock_risks: dict[str, dict[str, Any]], market_score: float) -> dict[str, Any]:
    """Compute portfolio PnL, concentration and risk contribution."""

    df = holdings.copy()
    if df.empty:
        return {"table": df, "score": 0, "warnings": ["未输入持仓。"], "theme_weights": pd.DataFrame(), "stock_weights": pd.DataFrame()}

    for col in ["持仓股数", "成本价", "当前价"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["股票代码"] = df["股票代码"].astype(str).map(normalize_stock_code)
    df["市值"] = df["持仓股数"] * df["当前价"]
    df["浮盈浮亏"] = (df["当前价"] - df["成本价"]) * df["持仓股数"]
    total_value = float(df["市值"].sum())
    df["仓位占比"] = df["市值"] / total_value if total_value > 0 else 0
    df["个股风险分"] = df["股票代码"].map(lambda code: stock_risks.get(code, {}).get("score", 50))
    df["风险贡献"] = df["仓位占比"] * df["个股风险分"]

    stock_max_weight = float(df["仓位占比"].max()) if not df.empty else 0
    theme_weights = df.groupby("所属板块", dropna=False)["市值"].sum().reset_index()
    theme_weights["仓位占比"] = theme_weights["市值"] / total_value if total_value > 0 else 0
    theme_max_weight = float(theme_weights["仓位占比"].max()) if not theme_weights.empty else 0
    weighted_stock_risk = float(df["风险贡献"].sum())
    concentration_penalty = max(0, stock_max_weight - PARAMS.concentration_stock_warning) * 120
    theme_penalty = max(0, theme_max_weight - PARAMS.concentration_theme_warning) * 100
    score = clamp(weighted_stock_risk + market_score * 0.2 + concentration_penalty + theme_penalty)

    warnings = []
    if stock_max_weight > PARAMS.concentration_stock_warning:
        warnings.append("单一股票仓位超过20%，存在集中度风险。")
    if theme_max_weight > PARAMS.concentration_theme_warning:
        warnings.append("单一板块仓位超过35%，存在板块集中度风险。")
    if score >= 70:
        warnings.append("组合整体风险较高，建议降低总仓位或削减高风险贡献持仓。")
    if not warnings:
        warnings.append("组合集中度和风险贡献暂未触发硬性预警。")

    return {
        "table": df,
        "score": score,
        "total_value": total_value,
        "total_pnl": float(df["浮盈浮亏"].sum()),
        "warnings": warnings,
        "theme_weights": theme_weights,
        "stock_weights": df[["股票代码", "市值", "仓位占比"]],
    }


def generate_risk_report(
    market: dict[str, Any],
    stock: dict[str, Any] | None,
    position: dict[str, Any] | None,
    stop_price: float | None,
) -> str:
    """Generate a natural-language risk report without deterministic buy/sell advice."""

    stock_text = "暂未选择个股。"
    position_text = "暂未计算仓位。"
    warning_text = f"如果市场风险分升至 {PARAMS.market_ban_threshold:.0f} 以上，停止新开仓。"
    if stock:
        stock_text = (
            f"股票 {stock['code']} {stock['name']} 当前风险分为 {stock['score']:.1f}，"
            f"主要风险来自：{' / '.join(stock['major_risks'])}。"
        )
    if position:
        position_text = (
            f"根据账户资金、止损距离和风险折扣，建议最大买入金额为 {position['final_amount']:,.0f} 元，"
            f"约 {position['final_shares']:,} 股。"
        )
    if stop_price:
        warning_text = f"如果股价跌破 {stop_price:.2f}，触发止损复核。{warning_text} 如果账户回撤超过 {PARAMS.account_drawdown_reduce_threshold:.0f}%，建议强制降仓。"

    return (
        f"【市场状态】\n当前市场风险分为 {market['score']:.1f}，属于 {market['state']} 状态，"
        f"建议总仓位不超过 {market['suggested_position']}。\n\n"
        f"【个股风险】\n{stock_text}\n\n"
        f"【仓位建议】\n{position_text}\n\n"
        f"【风险预警】\n{warning_text}"
    )
