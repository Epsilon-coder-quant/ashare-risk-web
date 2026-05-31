"""Render-only runtime patch for faster full-market realtime snapshots."""

from __future__ import annotations

from pathlib import Path


FAST_CLOUD_RUNTIME_OVERRIDE = r'''

# Render fast full-market realtime override v2.
import concurrent.futures as _cloud_futures
import os as _cloud_os
import re as _cloud_re
import subprocess as _cloud_subprocess


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
    if not data_loader_path.exists():
        return
    data_loader = data_loader_path.read_text(encoding="utf-8")
    marker = "# Render fast full-market realtime override v2."
    if marker not in data_loader:
        data_loader += FAST_CLOUD_RUNTIME_OVERRIDE
        data_loader_path.write_text(data_loader, encoding="utf-8")
