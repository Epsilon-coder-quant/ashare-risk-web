"""Plotly visualization helpers for the risk control system."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots

from risk_model import atr, drawdown_series, moving_average


RED = "#d92d20"
GREEN = "#039855"
BLUE = "#2563eb"
AMBER = "#f79009"
GRAY = "#667085"
CYAN = "#2dd4bf"
PANEL = "#101827"
GRID = "rgba(148, 163, 184, 0.14)"

pio.templates["ashare_dark"] = go.layout.Template(
    layout=dict(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#dbeafe", family="Inter, PingFang SC, Arial"),
        colorway=["#4f7cff", "#2dd4bf", "#f59e0b", "#ef4444", "#8b5cf6", "#38bdf8"],
        xaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor="rgba(148,163,184,.25)"),
        yaxis=dict(gridcolor=GRID, zerolinecolor=GRID, linecolor="rgba(148,163,184,.25)"),
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor="rgba(148,163,184,.18)"),
    )
)
pio.templates.default = "ashare_dark"


def score_color(score: float) -> str:
    """Return a color for a 0-100 risk score."""

    if score >= 80:
        return RED
    if score >= 60:
        return AMBER
    if score >= 40:
        return "#fdb022"
    return GREEN


def gauge_chart(score: float, title: str) -> go.Figure:
    """Create a gauge chart for risk score."""

    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=score,
            number={"suffix": " / 100"},
            title={"text": title},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": score_color(score)},
                "steps": [
                    {"range": [0, 20], "color": "#dcfce7"},
                    {"range": [20, 40], "color": "#ecfdf3"},
                    {"range": [40, 60], "color": "#fef3c7"},
                    {"range": [60, 80], "color": "#ffedd5"},
                    {"range": [80, 100], "color": "#fee2e2"},
                ],
            },
        )
    )
    fig.update_layout(height=280, margin=dict(l=20, r=20, t=50, b=10))
    return fig


def market_breadth_bar(summary: dict) -> go.Figure:
    """Create market breadth bar chart."""

    labels = ["上涨", "下跌", "涨停", "跌停"]
    values = [summary.get("up_count", 0), summary.get("down_count", 0), summary.get("limit_up_count", 0), summary.get("limit_down_count", 0)]
    colors = [RED, GREEN, RED, GREEN]
    fig = go.Figure(go.Bar(x=labels, y=values, marker_color=colors, text=values, textposition="outside"))
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=30, b=20), yaxis_title="家数", bargap=0.42)
    return fig


def index_trend_line(index_df: pd.DataFrame, title: str = "指数趋势") -> go.Figure:
    """Create index close line with 20/60 day moving averages."""

    df = index_df.copy()
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["date"], y=df["close"], name="收盘价", line=dict(color=BLUE, width=2.4)))
    fig.add_trace(go.Scatter(x=df["date"], y=moving_average(df["close"], 20), name="MA20", line=dict(color=AMBER, width=1.7)))
    fig.add_trace(go.Scatter(x=df["date"], y=moving_average(df["close"], 60), name="MA60", line=dict(color=CYAN, width=1.7)))
    fig.update_layout(title=title, height=360, margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified")
    return fig


def component_bar(components: dict[str, float], title: str = "风险分拆解") -> go.Figure:
    """Create horizontal bar chart for risk components."""

    items = sorted(components.items(), key=lambda x: x[1], reverse=True)
    names = [x[0] for x in items]
    values = [x[1] for x in items]
    fig = go.Figure(go.Bar(x=values, y=names, orientation="h", marker_color=[score_color(v) for v in values], text=[f"{v:.1f}" for v in values]))
    fig.update_layout(title=title, height=max(320, 48 * len(items)), margin=dict(l=20, r=20, t=50, b=20), xaxis_range=[0, 100], bargap=0.35)
    return fig


def etf_flow_timeline(history_df: pd.DataFrame, labels: dict[str, str] | None = None, title: str = "ETF实时流入/流出趋势") -> go.Figure:
    """Create a per-second ETF flow timeline from background updater history."""

    if history_df.empty:
        fig = go.Figure()
        fig.update_layout(title=title, height=320, margin=dict(l=20, r=20, t=50, b=20))
        return fig

    labels = labels or {}
    data = history_df.copy().sort_values("ts")
    data["flow_proxy"] = pd.to_numeric(data["flow_proxy"], errors="coerce").fillna(0)
    data["cum_flow"] = data.groupby("code")["flow_proxy"].cumsum()
    latest_abs = data.groupby("code")["cum_flow"].last().abs().sort_values(ascending=False)
    selected_codes = list(latest_abs.head(8).index)

    fig = go.Figure()
    for code in selected_codes:
        part = data[data["code"] == code]
        name = labels.get(code, code)
        color = RED if part["cum_flow"].iloc[-1] >= 0 else GREEN
        fig.add_trace(
            go.Scatter(
                x=part["ts"],
                y=part["cum_flow"],
                name=name,
                mode="lines",
                line=dict(width=2, color=color),
            )
        )
    fig.add_hline(y=0, line_dash="dot", line_color="rgba(148,163,184,.45)")
    fig.update_layout(
        title=title,
        height=340,
        margin=dict(l=20, r=20, t=50, b=20),
        yaxis_title="累计流向代理",
        hovermode="x unified",
    )
    return fig


def kline_risk_chart(df: pd.DataFrame, buy_price: float | None = None, stop_price: float | None = None) -> go.Figure:
    """Create K-line chart with volume, MA20/MA60, buy/stop lines and risk zone."""

    data = df.copy().tail(120)
    ma20 = moving_average(data["close"], 20)
    ma60 = moving_average(data["close"], 60)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.04, row_heights=[0.72, 0.28])
    fig.add_trace(
        go.Candlestick(x=data["date"], open=data["open"], high=data["high"], low=data["low"], close=data["close"], name="K线"),
        row=1,
        col=1,
    )
    fig.add_trace(go.Scatter(x=data["date"], y=ma20, name="MA20", line=dict(color=AMBER)), row=1, col=1)
    fig.add_trace(go.Scatter(x=data["date"], y=ma60, name="MA60", line=dict(color=CYAN)), row=1, col=1)
    vol_colors = [RED if c >= o else GREEN for c, o in zip(data["close"], data["open"])]
    fig.add_trace(go.Bar(x=data["date"], y=data["volume"], name="成交量", marker_color=vol_colors), row=2, col=1)

    x0, x1 = data["date"].iloc[0], data["date"].iloc[-1]
    if buy_price:
        fig.add_hline(y=buy_price, line_dash="dash", line_color=BLUE, annotation_text="买入价", row=1, col=1)
    if stop_price:
        fig.add_hline(y=stop_price, line_dash="dash", line_color=RED, annotation_text="止损价", row=1, col=1)
    if buy_price and stop_price:
        y0, y1 = sorted([buy_price, stop_price])
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor="rgba(217,45,32,0.10)", line_width=0, row=1, col=1)

    fig.update_layout(height=640, margin=dict(l=20, r=20, t=40, b=20), xaxis_rangeslider_visible=False, hovermode="x unified")
    return fig


def atr_chart(df: pd.DataFrame) -> go.Figure:
    """Create ATR chart."""

    data = df.copy().tail(120)
    fig = go.Figure(go.Scatter(x=data["date"], y=atr(data), name="ATR", line=dict(color=BLUE)))
    fig.update_layout(title="ATR变化", height=280, margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified")
    return fig


def drawdown_chart(df: pd.DataFrame) -> go.Figure:
    """Create latest 20-day drawdown curve."""

    data = df.copy()
    dd = drawdown_series(data["close"], 20)
    fig = go.Figure(go.Scatter(x=data["date"].tail(len(dd)), y=dd, name="近20日回撤", fill="tozeroy", line=dict(color=GREEN)))
    fig.update_layout(title="近20日回撤曲线", height=280, margin=dict(l=20, r=20, t=50, b=20), yaxis_ticksuffix="%")
    return fig


def theme_heatmap(table: pd.DataFrame) -> go.Figure:
    """Create theme risk heatmap from ranking table."""

    if table.empty:
        return go.Figure()
    cols = ["20日涨跌幅", "今日涨跌幅", "个股风险分"]
    z = table[cols].to_numpy().T
    fig = go.Figure(go.Heatmap(z=z, x=table["名称"], y=cols, colorscale="RdYlGn_r", colorbar_title="数值"))
    fig.update_layout(title="板块热力图", height=320, margin=dict(l=20, r=20, t=50, b=20))
    return fig


def theme_average_trend(avg_curve: pd.DataFrame) -> go.Figure:
    """Create normalized average theme trend line."""

    fig = go.Figure()
    if not avg_curve.empty:
        fig.add_trace(go.Scatter(x=avg_curve["t"], y=avg_curve["平均走势"], name="板块平均走势", line=dict(color=BLUE)))
    fig.update_layout(title="板块平均走势（近60日归一化）", height=320, margin=dict(l=20, r=20, t=50, b=20))
    return fig


def pie_chart(labels, values, title: str) -> go.Figure:
    """Create a donut pie chart."""

    fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.45))
    fig.update_layout(title=title, height=340, margin=dict(l=20, r=20, t=50, b=20))
    return fig


def risk_contribution_bar(table: pd.DataFrame) -> go.Figure:
    """Create portfolio risk contribution bar chart."""

    fig = go.Figure()
    if not table.empty:
        fig.add_trace(go.Bar(x=table["股票代码"], y=table["风险贡献"], marker_color=AMBER, text=table["风险贡献"].round(1)))
    fig.update_layout(title="风险贡献", height=320, margin=dict(l=20, r=20, t=50, b=20), yaxis_title="贡献分")
    return fig


def etf_flow_bar(table: pd.DataFrame, title: str = "ETF实时流入/流出") -> go.Figure:
    """Create a dynamic bar chart for ETF inflow/outflow proxy."""

    fig = go.Figure()
    if not table.empty and "增量流向代理" in table.columns:
        names = table["ETF名称"].fillna(table["ETF代码"]).astype(str)
        values = pd.to_numeric(table["增量流向代理"], errors="coerce").fillna(0)
        colors = [RED if value > 0 else GREEN if value < 0 else GRAY for value in values]
        fig.add_trace(go.Bar(x=names, y=values, marker_color=colors, text=[f"{v / 1e4:+.0f}万" for v in values]))
    fig.update_layout(title=title, height=320, margin=dict(l=20, r=20, t=50, b=80), yaxis_title="流向代理金额")
    return fig
