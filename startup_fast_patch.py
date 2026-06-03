"""Render patch that prevents cold-start market fetches from blocking Streamlit."""

from __future__ import annotations

from pathlib import Path


DATA_LOADER_APPEND = r'''

# Render nonblocking startup override v2.


_startup_original_load_market_snapshot = load_market_snapshot


def load_market_snapshot(use_mock: bool | None = None) -> tuple[pd.DataFrame, str | None]:
    """Return a fast real snapshot on cold starts without blocking Streamlit sessions."""

    if use_mock is None:
        use_mock = USE_MOCK_DATA
    if use_mock:
        return _startup_original_load_market_snapshot(use_mock=use_mock)

    runtime_df, runtime_warning = load_runtime_market_snapshot()
    if runtime_df is not None and (len(runtime_df) >= 100 or not is_a_share_trading_session()):
        return runtime_df, runtime_warning

    cached = _cloud_last_real_snapshot()
    if cached is not None:
        mark_dataframe(cached, str(cached.attrs.get("source") or "真实行情缓存"), "full")
        return cached, None if is_a_share_trading_session() else f"{a_share_session_label()}，保留最近一次真实全市场快照。"

    try:
        fast_df = fetch_public_fast_market_snapshot()
        mark_dataframe(fast_df, "云端真实轻量首屏行情", "sample")
        fast_df.attrs["coverage_target"] = 5000
        return fast_df, "云端稳定启动：首屏先使用真实轻量公开行情样本，避免免费实例阻塞；有完整真实缓存后自动切换完整口径。"
    except Exception as fast_exc:
        if runtime_df is not None:
            return runtime_df, runtime_warning or "完整实时源暂不可用，暂用后台真实快照。"
        if MOCK_ON_FAILURE:
            return _startup_original_load_market_snapshot(use_mock=use_mock)
        raise DataSourceError(f"云端快启动真实行情失败：{concise_error(fast_exc)}") from fast_exc
'''


APP_PATCH = r'''

# Render startup status marker v1.
'''


def patch(root: Path | str) -> None:
    """Patch restored files so Render cold starts do not block WebSocket setup."""

    root = Path(root)
    data_loader_path = root / "data_loader.py"
    if data_loader_path.exists():
        data_loader = data_loader_path.read_text(encoding="utf-8")
        marker = "# Render nonblocking startup override v2."
        if marker not in data_loader:
            data_loader = data_loader.replace("# Render nonblocking startup override v1.", "# Render nonblocking startup override v1 disabled.")
            data_loader += DATA_LOADER_APPEND
            data_loader_path.write_text(data_loader, encoding="utf-8")

    app_path = root / "app.py"
    if app_path.exists():
        app = app_path.read_text(encoding="utf-8")
        for old in (
            "max(1, REALTIME_REFRESH_SECONDS)",
            "max(3, REALTIME_REFRESH_SECONDS)",
            "max(8, REALTIME_REFRESH_SECONDS)",
        ):
            app = app.replace(old, "max(15, REALTIME_REFRESH_SECONDS)")
        app = app.replace(
            'st.sidebar.caption(f"实时图表每 {max(15, REALTIME_REFRESH_SECONDS)} 秒自动重绘；无需手动刷新页面。")',
            'st.sidebar.caption(f"实时图表每 {max(15, REALTIME_REFRESH_SECONDS)} 秒自动重绘；无需手动刷新页面。")\nst.sidebar.caption("Render免费版稳定模式：首屏快速连接；完整全市场缓存可用时自动切换 full 口径。")',
        )
        app_path.write_text(app, encoding="utf-8")
