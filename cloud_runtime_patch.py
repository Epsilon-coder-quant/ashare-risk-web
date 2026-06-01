"""Render-only runtime patch for faster full-market realtime snapshots."""

from __future__ import annotations

from pathlib import Path


FAST_CLOUD_RUNTIME_OVERRIDE = r'''

# Render fast full-market realtime override v2.
import concurrent.futures as _cloud_futures
import os as _cloud_os
import re as _cloud_re
import subprocess as _cloud_subprocess

_CLOUD_MEMORY_LAST_TS = 0.0


def _cloud_cache_retention_seconds(path: Path) -> int:
    """Return cloud cache retention seconds by file family."""

    name = path.name
    max_age = int(_cloud_os.environ.get("ASHARE_CACHE_MAX_AGE_SECONDS", str(60 * 60 * 24)))
    if name.startswith("spot_"):
        return max(int(MARKET_SNAPSHOT_MAX_AGE_SECONDS) * 2, int(CACHE_TTL_SECONDS.get("spot", 60)) * 20)
    if name.startswith(("index_", "index_tx_", "index_em_")):
        return max(int(CACHE_TTL_SECONDS.get("index", 1800)) * 4, 60 * 60 * 12)
    if name.startswith(("history_", "stock_hist_", "stock_hist_tx_", "stock_hist_em_")):
        return max(int(CACHE_TTL_SECONDS.get("history", 60 * 60 * 4)) * 4, max_age)
    return max_age


def _cloud_cleanup_runtime_memory(force: bool = False) -> None:
    """Delete stale cache files and cap cloud cache footprint."""

    global _CLOUD_MEMORY_LAST_TS
    now = datetime.now().timestamp()
    interval = int(_cloud_os.environ.get("ASHARE_MEMORY_CLEANUP_INTERVAL_SECONDS", "120"))
    if not force and now - _CLOUD_MEMORY_LAST_TS < interval:
        return
    _CLOUD_MEMORY_LAST_TS = now

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    files = [path for path in CACHE_DIR.glob("*.pkl") if path.is_file()]
    if not files:
        return

    newest_spot = max((path for path in files if path.name.startswith("spot_")), key=lambda p: p.stat().st_mtime, default=None)
    for path in list(files):
        try:
            age = now - path.stat().st_mtime
            keep_rest_snapshot = newest_spot == path and not is_a_share_trading_session()
            if age > _cloud_cache_retention_seconds(path) and not keep_rest_snapshot:
                path.unlink(missing_ok=True)
        except Exception:
            path.unlink(missing_ok=True)

    max_files = int(_cloud_os.environ.get("ASHARE_CACHE_MAX_FILES", "80"))
    max_bytes = int(_cloud_os.environ.get("ASHARE_CACHE_MAX_BYTES", str(256 * 1024 * 1024)))
    files = [path for path in CACHE_DIR.glob("*.pkl") if path.is_file()]
    if len(files) > max_files:
        protected = {max(files, key=lambda p: p.stat().st_mtime)}
        old_files = sorted((path for path in files if path not in protected), key=lambda p: p.stat().st_mtime)
        for path in old_files[: max(0, len(files) - max_files)]:
            path.unlink(missing_ok=True)

    files = [path for path in CACHE_DIR.glob("*.pkl") if path.is_file()]
    total_bytes = sum(path.stat().st_size for path in files)
    if total_bytes > max_bytes:
        for path in sorted(files, key=lambda p: p.stat().st_mtime):
            try:
                total_bytes -= path.stat().st_size
                path.unlink(missing_ok=True)
            except Exception:
                pass
            if total_bytes <= max_bytes:
                break


cleanup_runtime_memory = _cloud_cleanup_runtime_memory
_cloud_original_cached_dataframe_v2 = cached_dataframe
_cloud_original_full_cached_dataframe_v2 = globals().get("_cloud_cached_dataframe", cached_dataframe)


def cached_dataframe(key: Path, ttl_seconds: int, fetcher: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """Wrap base cache reads with periodic memory cleanup."""

    _cloud_cleanup_runtime_memory()
    df = _cloud_original_cached_dataframe_v2(key, ttl_seconds, fetcher)
    _cloud_cleanup_runtime_memory(force=True)
    return df


def _cloud_cached_dataframe(key: Path, ttl_seconds: int, fetcher: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """Wrap cloud full-market cache reads with periodic memory cleanup."""

    _cloud_cleanup_runtime_memory()
    df = _cloud_original_full_cached_dataframe_v2(key, ttl_seconds, fetcher)
    _cloud_cleanup_runtime_memory(force=True)
    return df


def _cloud_fetch_tencent_spots_fast(symbols: list[str]) -> dict[str, dict]:
    """Fetch Tencent quotes with short cloud timeouts to avoid Streamlit disconnects."""

    codes = [normalize_stock_code(symbol) for symbol in symbols if normalize_stock_code(symbol)]
    if not codes:
        return {}
    market_symbols = [tencent_symbol(code) for code in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(market_symbols)
    raw = _cloud_subprocess.check_output(
        ["curl", "-sL", "--connect-timeout", "2", "--max-time", "3", url],
        stderr=_cloud_subprocess.DEVNULL,
    )
    text = raw.decode("gb18030", errors="replace")
    rows: dict[str, dict] = {}
    for match in _cloud_re.finditer(r'v_[a-z]{2}(\d{6})="([^"]*)"', text):
        code = normalize_stock_code(match.group(1))
        fields = match.group(2).split("~")
        try:
            rows[code] = tencent_quote_from_fields(code, fields)
        except Exception:
            continue
    if not rows:
        raise DataSourceError("腾讯批量实时行情无数据")
    return rows


def fetch_tencent_full_market_snapshot(batch_size: int = 450) -> pd.DataFrame:
    """Fetch full-market realtime quotes concurrently on Render."""

    codes = _cloud_market_codes() if "_cloud_market_codes" in globals() else [
        str(item["code"]) for item in load_market_universe()
    ]
    batch_size = int(_cloud_os.environ.get("ASHARE_TENCENT_BATCH_SIZE", str(batch_size)))
    workers = int(_cloud_os.environ.get("ASHARE_TENCENT_WORKERS", "8"))
    batches = [codes[start : start + batch_size] for start in range(0, len(codes), batch_size)]
    rows: list[dict] = []
    failed_batches = 0

    with _cloud_futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(_cloud_fetch_tencent_spots_fast, batch): batch for batch in batches}
        for future in _cloud_futures.as_completed(futures):
            batch = futures[future]
            try:
                quotes = future.result()
            except Exception:
                failed_batches += 1
                continue
            for code in batch:
                quote = quotes.get(code)
                if quote:
                    rows.append(quote)

    if len(rows) < 3000:
        raise DataSourceError(f"腾讯全市场实时行情覆盖不足：{len(rows)}/{len(codes)}")

    df = normalize_spot_df(pd.DataFrame(rows))
    df.attrs["source"] = "腾讯全市场实时行情"
    df.attrs["updated_at"] = datetime.now().isoformat(timespec="seconds")
    df.attrs["data_scope"] = "full"
    df.attrs["coverage_target"] = len(codes)
    df.attrs["failed_batches"] = failed_batches
    return df


def _cloud_last_real_snapshot() -> pd.DataFrame | None:
    """Return the newest full real cache if live sources are temporarily unavailable."""

    candidates = []
    for key_name in ("tencent_full_market", "eastmoney", "akshare", "akshare_legacy"):
        cached = read_cached_dataframe(cache_key("spot", key_name))
        if cached is not None and len(cached) >= 3000:
            candidates.append(cached)
    if not candidates:
        return None
    candidates.sort(key=lambda frame: float(frame.attrs.get("cache_age_seconds") or 10**12))
    df = candidates[0]
    df.attrs.setdefault("source", "真实行情缓存")
    df.attrs.setdefault("data_scope", "full")
    return df


def load_market_snapshot(use_mock: bool | None = None) -> tuple[pd.DataFrame, str | None]:
    """Load full-market realtime data without blocking the Streamlit websocket."""

    if use_mock is None:
        use_mock = USE_MOCK_DATA
    if use_mock:
        return mark_dataframe(mock_market_snapshot(), "mock", "sample"), "当前为 mock data 模式。"

    runtime_df, runtime_warning = load_runtime_market_snapshot()
    if runtime_df is not None and (not is_a_share_trading_session() or FAST_START):
        return runtime_df, runtime_warning

    refresh_seconds = int(_cloud_os.environ.get("ASHARE_FULL_MARKET_REFRESH_SECONDS", "10"))
    ttl_seconds = max(1, min(int(CACHE_TTL_SECONDS.get("spot", 60)), refresh_seconds))

    try:
        df = _cloud_cached_dataframe(
            cache_key("spot", "tencent_full_market"),
            ttl_seconds,
            fetch_tencent_full_market_snapshot,
        )
        mark_dataframe(df, "腾讯全市场实时行情", "full")
        return df, "云端全市场模式：使用腾讯并发批量实时行情，市场广度/涨跌停/成交额按完整口径纳入模型。"
    except Exception as tx_exc:
        tx_error = tx_exc

    try:
        df = _cloud_cached_dataframe(
            cache_key("spot", "eastmoney"),
            ttl_seconds,
            fetch_eastmoney_market_snapshot,
        )
        mark_dataframe(df, "Eastmoney push2", "full")
        return df, "腾讯全市场接口临时失败，已使用东方财富全市场接口兜底。"
    except Exception as em_exc:
        em_error = em_exc

    if _cloud_os.environ.get("ASHARE_ENABLE_AKSHARE_FULL_SNAPSHOT", "0") in {"1", "true", "TRUE", "yes", "YES"}:
        def fetch_ak() -> pd.DataFrame:
            ak = _akshare()
            return normalize_spot_df(ak.stock_zh_a_spot_em())

        try:
            df = _cloud_cached_dataframe(cache_key("spot", "akshare"), ttl_seconds, fetch_ak)
            mark_dataframe(df, "AKShare Eastmoney", "full")
            return df, "腾讯/东方财富接口临时失败，已使用 AKShare 全市场接口。"
        except Exception:
            pass

    cached = _cloud_last_real_snapshot()
    if cached is not None:
        mark_dataframe(cached, str(cached.attrs.get("source") or "真实行情缓存"), "full")
        return cached, (
            f"{a_share_session_label()}，实时源短暂不可用，保留最近一次真实全市场缓存；"
            f"腾讯错误：{concise_error(tx_error)}；东方财富错误：{concise_error(em_error)}"
        )

    if runtime_df is not None:
        return runtime_df, runtime_warning or "实时源短暂不可用，已使用后台真实快照。"

    if MOCK_ON_FAILURE:
        mock = mock_market_snapshot()
        mark_dataframe(mock, "mock", "sample")
        return mock, f"全市场实时源失败，临时显示 mock：{concise_error(tx_error)}"

    raise DataSourceError(
        f"全市场实时源失败：腾讯={concise_error(tx_error)}；东方财富={concise_error(em_error)}"
    ) from tx_error
'''


def patch(root: Path | str) -> None:
    """Append the cloud override to the restored data loader."""

    root = Path(root)
    data_loader_path = root / "data_loader.py"
    if data_loader_path.exists():
        data_loader = data_loader_path.read_text(encoding="utf-8")
        marker = "# Render fast full-market realtime override v2."
        if marker not in data_loader:
            data_loader += FAST_CLOUD_RUNTIME_OVERRIDE
            data_loader_path.write_text(data_loader, encoding="utf-8")

    app_path = root / "app.py"
    if app_path.exists():
        app = app_path.read_text(encoding="utf-8")
        app = app.replace(
            "from data_loader import (\n    clear_cache,\n",
            "from data_loader import (\n    clear_cache,\n    cleanup_runtime_memory,\n",
        )
        app = app.replace(
            'st.set_page_config(page_title="A股风控助手", page_icon="A", layout="wide")\n',
            'st.set_page_config(page_title="A股风控助手", page_icon="A", layout="wide")\ncleanup_runtime_memory()\n',
        )
        old = "    max_points = max(limit * max(len(rows), 1), limit)\n    if len(points) > max_points:\n"
        new = (
            "    active_codes = {normalize_stock_code(row.get(\"code\")) for row in rows if normalize_stock_code(row.get(\"code\"))}\n"
            "    for code in list(last_amount):\n"
            "        if active_codes and code not in active_codes:\n"
            "            last_amount.pop(code, None)\n\n"
            "    session_limit = int(__import__(\"os\").environ.get(\"ASHARE_SESSION_ETF_HISTORY_LIMIT\", \"2400\"))\n"
            "    max_points = min(max(limit * max(len(rows), 1), limit), max(session_limit, limit))\n"
            "    if len(points) > max_points:\n"
        )
        app = app.replace(old, new)
        app_path.write_text(app, encoding="utf-8")
