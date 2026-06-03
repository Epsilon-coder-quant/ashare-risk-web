"""Render patch that prevents cold-start market fetches from blocking Streamlit."""

from __future__ import annotations

from pathlib import Path


DATA_LOADER_APPEND = r'''

# Render nonblocking startup override v1.
import threading as _startup_threading

_STARTUP_PREFETCH_LOCK = _startup_threading.Lock()
_STARTUP_PREFETCH_STARTED = False


def _startup_write_cached_snapshot(key_name: str, df: pd.DataFrame, source: str) -> None:
    """Persist a real full-market snapshot for later Streamlit reruns."""

    mark_dataframe(df, source, "full")
    key = cache_key("spot", key_name)
    key.parent.mkdir(parents=True, exist_ok=True)
    with key.open("wb") as f:
        pickle.dump(df, f)


def _startup_prefetch_full_market_once() -> None:
    """Warm the full-market cache in a daemon thread after first paint."""

    global _STARTUP_PREFETCH_STARTED
    if _STARTUP_PREFETCH_STARTED or _cloud_last_real_snapshot() is not None:
        return
    with _STARTUP_PREFETCH_LOCK:
        if _STARTUP_PREFETCH_STARTED or _cloud_last_real_snapshot() is not None:
            return
        _STARTUP_PREFETCH_STARTED = True

    def worker() -> None:
        fetchers = (
            ("tencent_full_market", fetch_tencent_full_market_snapshot, "腾讯全市场实时行情"),
            ("eastmoney", fetch_eastmoney_market_snapshot, "Eastmoney push2"),
        )
        for key_name, fetcher, source in fetchers:
            try:
                df = fetcher()
                if len(df) >= 2500:
                    _startup_write_cached_snapshot(key_name, df, source)
                    return
            except Exception:
                continue

    _startup_threading.Thread(target=worker, name="ashare-full-market-prefetch", daemon=True).start()


_startup_original_load_market_snapshot = load_market_snapshot


def load_market_snapshot(use_mock: bool | None = None) -> tuple[pd.DataFrame, str | None]:
    """Return a fast real snapshot on cold starts and warm full-market cache in background."""

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

    _startup_prefetch_full_market_once()

    try:
        fast_df = fetch_public_fast_market_snapshot()
        mark_dataframe(fast_df, "云端真实轻量首屏行情", "sample")
        fast_df.attrs["coverage_target"] = 5000
        return fast_df, "云端快启动：首屏先使用真实轻量公开行情样本，后台正在预热完整全市场快照；完成后自动切换完整口径。"
    except Exception as fast_exc:
        if runtime_df is not None:
            return runtime_df, runtime_warning or "完整实时源预热中，暂用后台真实快照。"
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
        marker = "# Render nonblocking startup override v1."
        if marker not in data_loader:
            data_loader += DATA_LOADER_APPEND
            data_loader_path.write_text(data_loader, encoding="utf-8")

    app_path = root / "app.py"
    if app_path.exists():
        app = app_path.read_text(encoding="utf-8")
        app = app.replace("max(3, REALTIME_REFRESH_SECONDS)", "max(8, REALTIME_REFRESH_SECONDS)")
        app = app.replace(
            'st.sidebar.caption(f"实时图表每 {max(8, REALTIME_REFRESH_SECONDS)} 秒自动重绘；无需手动刷新页面。")',
            'st.sidebar.caption(f"实时图表每 {max(8, REALTIME_REFRESH_SECONDS)} 秒自动重绘；无需手动刷新页面。")\nst.sidebar.caption("Render免费版稳定模式：首屏快速连接，完整全市场数据会在后台预热后自动接管。")',
        )
        app_path.write_text(app, encoding="utf-8")
