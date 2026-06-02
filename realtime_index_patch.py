"""Render patch for realtime index K-line updates."""

from __future__ import annotations

from pathlib import Path


DATA_LOADER_APPEND = r'''

# Render realtime index quote overlay v1.
_realtime_original_load_index_history = load_index_history


def apply_realtime_index_quote(index_df: pd.DataFrame, option: dict) -> pd.DataFrame:
    """Update the latest index K-line point with realtime quote data when available."""

    df = index_df.copy()
    try:
        quote = fetch_tencent_spot(str(option["code"]))
    except Exception:
        return df

    latest = pd.to_numeric(quote.get("latest"), errors="coerce")
    if pd.isna(latest) or float(latest) <= 0:
        return df

    latest = float(latest)
    current = datetime.now(ZoneInfo("Asia/Shanghai"))
    current_date = pd.Timestamp(current.date())
    if df.empty:
        return df

    last_date = pd.to_datetime(df["date"].iloc[-1], errors="coerce")
    if pd.isna(last_date):
        return df

    if last_date.normalize() < current_date and current.weekday() < 5 and is_a_share_trading_session(current):
        prev_close = pd.to_numeric(quote.get("prev_close"), errors="coerce")
        prev_close = float(prev_close) if pd.notna(prev_close) and float(prev_close) > 0 else float(df["close"].iloc[-1])
        new_row = {
            "date": current_date,
            "open": pd.to_numeric(quote.get("open"), errors="coerce"),
            "high": pd.to_numeric(quote.get("high"), errors="coerce"),
            "low": pd.to_numeric(quote.get("low"), errors="coerce"),
            "close": latest,
            "volume": pd.to_numeric(quote.get("volume"), errors="coerce"),
            "amount": pd.to_numeric(quote.get("amount"), errors="coerce"),
            "pct_change": (latest / prev_close - 1) * 100 if prev_close else np.nan,
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    elif last_date.normalize() == current_date or is_a_share_trading_session(current):
        idx = df.index[-1]
        prev_close = pd.to_numeric(quote.get("prev_close"), errors="coerce")
        if pd.notna(prev_close) and float(prev_close) > 0:
            df.loc[idx, "pct_change"] = (latest / float(prev_close) - 1) * 100
        for col, quote_key in (("open", "open"), ("volume", "volume"), ("amount", "amount")):
            value = pd.to_numeric(quote.get(quote_key), errors="coerce")
            if pd.notna(value) and float(value) > 0:
                df.loc[idx, col] = float(value)
        high = pd.to_numeric(quote.get("high"), errors="coerce")
        low = pd.to_numeric(quote.get("low"), errors="coerce")
        df.loc[idx, "close"] = latest
        df.loc[idx, "high"] = max(float(df.loc[idx, "high"]), latest, float(high) if pd.notna(high) else latest)
        df.loc[idx, "low"] = min(float(df.loc[idx, "low"]), latest, float(low) if pd.notna(low) else latest)

    df.attrs.update(index_df.attrs)
    df.attrs["updated_at"] = quote.get("updated_at") or datetime.now().isoformat(timespec="seconds")
    df.attrs["source"] = f"{df.attrs.get('source', '指数K线')} + 腾讯实时指数报价"
    df.attrs["realtime_price"] = latest
    return df


def load_index_history(index_name: str = "沪深300", use_mock: bool | None = None) -> tuple[pd.DataFrame, str | None]:
    """Load index K-line data and merge realtime index quote into the latest point."""

    df, warning = _realtime_original_load_index_history(index_name, use_mock=use_mock)
    if use_mock:
        return df, warning
    option = INDEX_OPTIONS.get(index_name, INDEX_OPTIONS["沪深300"])
    live_df = apply_realtime_index_quote(df, option)
    if live_df.attrs.get("realtime_price") is not None:
        warning = warning or f"{index_name} K线已叠加腾讯实时指数报价。"
    return live_df, warning
'''


def patch(root: Path | str) -> None:
    """Patch restored Render files so index charts update from realtime quotes."""

    root = Path(root)
    data_loader_path = root / "data_loader.py"
    if data_loader_path.exists():
        data_loader = data_loader_path.read_text(encoding="utf-8")
        marker = "# Render realtime index quote overlay v1."
        if marker not in data_loader:
            data_loader += DATA_LOADER_APPEND
            data_loader_path.write_text(data_loader, encoding="utf-8")

    app_path = root / "app.py"
    if app_path.exists():
        app = app_path.read_text(encoding="utf-8")
        app = app.replace(
            "        live_breadth = summarize_market_snapshot(live_snapshot)\n        live_state = load_runtime_state()\n",
            "        live_breadth = summarize_market_snapshot(live_snapshot)\n        live_index_df, index_warning = load_index_history(index_name, use_mock=use_mock)\n        live_state = load_runtime_state()\n",
        )
        app = app.replace(
            'compute_market_risk(base_index_df, live_breadth, live_state.get("macro") or {})',
            'compute_market_risk(live_index_df, live_breadth, live_state.get("macro") or {})',
        )
        app = app.replace(
            "        show_warning(spot_warning)\n    except Exception as exc:",
            "        show_warning(spot_warning)\n        show_warning(index_warning)\n    except Exception as exc:",
        )
        app = app.replace(
            'index_meta = frame_meta(base_index_df, f"{index_name} K线")',
            'index_meta = frame_meta(live_index_df, f"{index_name} K线")',
        )
        app = app.replace(
            'index_trend_line(base_index_df, f"{index_name} 趋势")',
            'index_trend_line(live_index_df, f"{index_name} 趋势")',
        )
        app_path.write_text(app, encoding="utf-8")
