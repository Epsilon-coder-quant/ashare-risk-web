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
_CLOUD_PUBLIC_MACRO_CACHE = {"ts": 0.0, "payload": {}}


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
        ["curl", "-sL", "--connect-timeout", "3", "--max-time", "5", url],
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


def _cloud_flow_risk_score(rows: list[dict]) -> float:
    """Score ETF flow proxy: higher means more outflow/liquidity pressure."""

    gross = sum(abs(float(row.get("flow_proxy") or 0)) for row in rows)
    if gross <= 0:
        return 50.0
    net = sum(float(row.get("flow_proxy") or 0) for row in rows)
    turnover = sum(float(row.get("amount") or 0) for row in rows)
    direction_risk = 50 - net / gross * 42
    activity_boost = min(turnover / 8e11, 1.0) * 8
    return float(np.clip(direction_risk + (activity_boost if net < 0 else -activity_boost * 0.35), 0, 100))


def fetch_public_etf_flow_snapshot(ttl_seconds: int = 1) -> dict:
    """Fetch realtime ETF quotes directly and convert them into flow-pressure inputs."""

    now_ts = datetime.now().timestamp()
    if now_ts - float(_PUBLIC_ETF_CACHE.get("ts") or 0) < ttl_seconds:
        return dict(_PUBLIC_ETF_CACHE.get("payload") or {})

    broad_codes = sorted({normalize_stock_code(code) for codes in BROAD_INDEX_ETFS.values() for code in codes})
    sector_codes = sorted({normalize_stock_code(code) for codes in THEME_ETF_MAP.values() for code in codes})
    updated_at = datetime.now().isoformat(timespec="seconds")

    try:
        quotes = _cloud_fetch_tencent_spots_fast(broad_codes + sector_codes)
    except Exception:
        try:
            quotes = fetch_tencent_spots(broad_codes + sector_codes)
        except Exception:
            quotes = {}

    def safe_number(value: object, default: float = 0.0) -> float:
        number = pd.to_numeric(value, errors="coerce")
        return float(number) if pd.notna(number) else default

    def build_row(code: str, group: str) -> dict | None:
        quote = quotes.get(code)
        if not quote:
            return None
        amount = safe_number(quote.get("amount"))
        change_pct = safe_number(quote.get("change_pct"))
        direction = 1 if change_pct > 0 else -1 if change_pct < 0 else 0
        flow_proxy = amount * direction * min(abs(change_pct) / 100, 0.05)
        return {
            "code": code,
            "name": quote.get("name") or code,
            "label": quote.get("name") or code,
            "group": group,
            "latest": quote.get("latest"),
            "change_pct": change_pct,
            "amount": amount,
            "interval_amount": 0.0,
            "flow_proxy": flow_proxy,
            "flow_direction": "流入" if flow_proxy > 0 else "流出" if flow_proxy < 0 else "中性",
            "signal": "公开行情实时代理",
        }

    broad = [row for row in (build_row(code, "宽基") for code in broad_codes) if row]
    sector = [row for row in (build_row(code, "窄基") for code in sector_codes) if row]
    payload = {
        "updated_at": updated_at,
        "method": "云端公开行情实时代理：使用ETF实时报价、涨跌幅与成交额估算流入/流出方向。",
        "broad": broad,
        "sector": sector,
        "broad_market_flow_score": _cloud_flow_risk_score(broad),
        "sector_flow_score": _cloud_flow_risk_score(sector),
    }
    _PUBLIC_ETF_CACHE["ts"] = now_ts
    _PUBLIC_ETF_CACHE["payload"] = payload
    return payload


def _cloud_cached_index_frame_for_macro(index_name: str, option: dict) -> pd.DataFrame | None:
    """Return a cached index frame, fetching only a lightweight Tencent fallback when needed."""

    for key in (
        cache_key("index_tx", option["ak_symbol"]),
        cache_key("index", index_name),
        cache_key("index_em", option["ak_symbol"]),
    ):
        cached = read_cached_dataframe(key)
        if cached is not None and len(cached) >= 30:
            return cached
    try:
        df = cached_dataframe(
            cache_key("index_tx", option["ak_symbol"]),
            min(int(CACHE_TTL_SECONDS.get("index", 1800)), 300),
            lambda: fetch_tencent_kline(option["code"], 120),
        )
        mark_dataframe(df, "腾讯公开行情")
        return df
    except Exception:
        return None


def _cloud_latest_cached_market_breadth() -> dict:
    """Read latest full-market breadth from real cache without extra network calls."""

    candidates = []
    for key_name in ("tencent_full_market", "eastmoney", "akshare", "akshare_legacy"):
        cached = read_cached_dataframe(cache_key("spot", key_name))
        if cached is not None and len(cached) >= 3000:
            candidates.append(cached)
    if not candidates:
        return {}
    candidates.sort(key=lambda frame: float(frame.attrs.get("cache_age_seconds") or 10**12))
    return summarize_market_snapshot(candidates[0])


def fetch_public_macro_option_state(ttl_seconds: int = 10, etf_flow: dict | None = None) -> dict:
    """Build macro/option preference proxies from public live market data on Render."""

    now_ts = datetime.now().timestamp()
    if now_ts - float(_CLOUD_PUBLIC_MACRO_CACHE.get("ts") or 0) < ttl_seconds:
        return dict(_CLOUD_PUBLIC_MACRO_CACHE.get("payload") or {})

    weights = {
        "沪深300": 0.30,
        "上证指数": 0.25,
        "创业板指": 0.18,
        "科创板指数": 0.17,
        "中证500": 0.10,
    }
    index_rows = []
    for name, option in INDEX_OPTIONS.items():
        df = _cloud_cached_index_frame_for_macro(name, option)
        if df is None or df.empty:
            continue
        close = pd.to_numeric(df["close"], errors="coerce").dropna()
        if len(close) < 30:
            continue
        latest = float(close.iloc[-1])
        ma20 = float(close.tail(20).mean())
        ma60 = float(close.tail(60).mean()) if len(close) >= 60 else ma20
        ret5 = float((close.iloc[-1] / close.iloc[-6] - 1) * 100) if len(close) > 6 else 0.0
        ret20 = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) > 21 else 0.0
        vol20 = float(close.pct_change().tail(20).std() * np.sqrt(252) * 100)
        tail20 = close.tail(20)
        drawdown20 = float((tail20 / tail20.cummax() - 1).min() * 100)
        ma20_gap = latest / ma20 - 1 if ma20 else 0.0
        ma60_gap = latest / ma60 - 1 if ma60 else 0.0
        pressure = float(np.clip(
            32
            + max(-ma20_gap, 0) * 250
            + max(-ma60_gap, 0) * 190
            + max(-ret5, 0) * 3.0
            + max(-ret20, 0) * 1.35
            + min(abs(min(drawdown20, 0)) * 2.3, 25)
            + min(max(vol20 - 18, 0) * 1.25, 22)
            - max(ret5, 0) * 1.2,
            0,
            100,
        ))
        index_rows.append({
            "name": name,
            "weight": weights.get(name, 0.1),
            "ret5": ret5,
            "ret20": ret20,
            "vol20": vol20,
            "drawdown20": drawdown20,
            "below_ma20": latest < ma20,
            "below_ma60": latest < ma60,
            "pressure": pressure,
            "updated_at": df.attrs.get("updated_at"),
            "source": df.attrs.get("source") or "指数K线缓存",
        })

    if index_rows:
        total_weight = sum(row["weight"] for row in index_rows)
        china_pressure = sum(row["pressure"] * row["weight"] for row in index_rows) / total_weight
        avg_ret5 = sum(row["ret5"] * row["weight"] for row in index_rows) / total_weight
        avg_ret20 = sum(row["ret20"] * row["weight"] for row in index_rows) / total_weight
        avg_vol20 = sum(row["vol20"] * row["weight"] for row in index_rows) / total_weight
        down_sync = sum(row["weight"] for row in index_rows if row["ret5"] < 0 or row["below_ma20"]) / total_weight
        sync_score = float(np.clip(30 + down_sync * 52 + max(-avg_ret5, 0) * 2.3 + max(-avg_ret20, 0) * 0.9, 0, 100))
    else:
        china_pressure = 50.0
        avg_ret5 = 0.0
        avg_ret20 = 0.0
        avg_vol20 = 20.0
        sync_score = 50.0

    breadth = _cloud_latest_cached_market_breadth()
    up_ratio = float(breadth.get("up_ratio", 0.5) or 0.5)
    limit_down_count = float(breadth.get("limit_down_count", 0) or 0)
    breadth_pressure = float(np.clip((1 - up_ratio) * 100 + min(limit_down_count / 25, 1) * 15, 0, 100))
    china_pressure = float(np.clip(china_pressure * 0.72 + breadth_pressure * 0.28, 0, 100))

    global_pressure = 50.0
    try:
        global_quotes = _cloud_fetch_tencent_spots_fast(["513500", "513100", "159941", "162411"])
    except Exception:
        global_quotes = {}
    if global_quotes:
        changes = pd.to_numeric([row.get("change_pct") for row in global_quotes.values()], errors="coerce")
        changes = [float(item) for item in changes if pd.notna(item)]
        if changes:
            avg_global_change = sum(changes) / len(changes)
            global_pressure = float(np.clip(50 - avg_global_change * 4.5 + max(avg_vol20 - 25, 0) * 0.6, 0, 100))

    etf_flow = etf_flow or fetch_public_etf_flow_snapshot()
    broad_flow_score = float(etf_flow.get("broad_market_flow_score", 50) or 50)
    sector_flow_score = float(etf_flow.get("sector_flow_score", 50) or 50)
    flow_pressure = float(np.clip(broad_flow_score * 0.65 + sector_flow_score * 0.35, 0, 100))

    option_defensive_pressure = float(np.clip(
        china_pressure * 0.44
        + sync_score * 0.18
        + flow_pressure * 0.23
        + max(avg_vol20 - 20, 0) * 0.9
        + max(0.5 - up_ratio, 0) * 70,
        0,
        100,
    ))
    option_upside_demand = float(np.clip(
        (100 - china_pressure) * 0.34
        + max(50 - flow_pressure, 0) * 0.75
        + max(avg_ret5, 0) * 3.0
        + max(up_ratio - 0.5, 0) * 72
        + 18,
        0,
        100,
    ))
    option_preference_score = float(np.clip(50 + (option_upside_demand - option_defensive_pressure) * 0.55, 0, 100))
    if option_preference_score >= 62:
        option_preference = "看涨修复偏强"
    elif option_preference_score <= 38:
        option_preference = "防守保护偏强"
    elif abs(option_upside_demand - option_defensive_pressure) >= 18:
        option_preference = "多空分歧加大"
    else:
        option_preference = "中性震荡"

    macro_score = float(np.clip(china_pressure * 0.48 + global_pressure * 0.16 + sync_score * 0.20 + flow_pressure * 0.16, 0, 100))
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "云端公开行情宏观/期权代理",
        "scores": {
            "macro_score": macro_score,
            "china_market_pressure": china_pressure,
            "global_market_pressure": global_pressure,
            "market_sync_score": sync_score,
            "option_defensive_pressure": option_defensive_pressure,
            "option_upside_demand": option_upside_demand,
            "option_preference_score": option_preference_score,
            "option_preference": option_preference,
            "regime": "压力升温" if macro_score >= 65 else "风险中性" if macro_score >= 40 else "风险偏好修复",
            "sync_regime": "指数同步转弱" if sync_score >= 65 else "指数分化" if sync_score >= 45 else "指数同步修复",
        },
        "inputs": {
            "index_proxy": index_rows,
            "breadth": breadth,
            "etf_flow": {
                "broad_market_flow_score": broad_flow_score,
                "sector_flow_score": sector_flow_score,
            },
            "global_proxy_quotes": global_quotes,
            "volatility": {
                "avg_index_ret5": avg_ret5,
                "avg_index_ret20": avg_ret20,
                "avg_index_vol20": avg_vol20,
                "option_preference": option_preference,
                "option_preference_score": option_preference_score,
                "option_defensive_pressure": option_defensive_pressure,
                "option_upside_demand": option_upside_demand,
            },
        },
    }
    _CLOUD_PUBLIC_MACRO_CACHE["ts"] = now_ts
    _CLOUD_PUBLIC_MACRO_CACHE["payload"] = payload
    return payload


def fetch_tencent_full_market_snapshot(batch_size: int = 260) -> pd.DataFrame:
    """Fetch full-market realtime quotes concurrently on Render."""

    codes = _cloud_market_codes() if "_cloud_market_codes" in globals() else [
        str(item["code"]) for item in load_market_universe()
    ]
    batch_size = int(_cloud_os.environ.get("ASHARE_TENCENT_BATCH_SIZE", str(batch_size)))
    workers = int(_cloud_os.environ.get("ASHARE_TENCENT_WORKERS", "8"))
    batches = [codes[start : start + batch_size] for start in range(0, len(codes), batch_size)]
    rows: list[dict] = []
    failed_batches = 0
    failed_codes: list[str] = []

    with _cloud_futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(_cloud_fetch_tencent_spots_fast, batch): batch for batch in batches}
        for future in _cloud_futures.as_completed(futures):
            batch = futures[future]
            try:
                quotes = future.result()
            except Exception:
                failed_batches += 1
                failed_codes.extend(batch)
                continue
            for code in batch:
                quote = quotes.get(code)
                if quote:
                    rows.append(quote)

    seen_codes = {str(row.get("code")) for row in rows}
    missing_codes = [code for code in codes if code not in seen_codes]
    retry_codes = sorted(set(failed_codes + missing_codes))
    if retry_codes and len(rows) < 3300:
        retry_batch_size = int(_cloud_os.environ.get("ASHARE_TENCENT_RETRY_BATCH_SIZE", "120"))
        retry_batches = [retry_codes[start : start + retry_batch_size] for start in range(0, len(retry_codes), retry_batch_size)]
        with _cloud_futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as executor:
            futures = {executor.submit(_cloud_fetch_tencent_spots_fast, batch): batch for batch in retry_batches}
            for future in _cloud_futures.as_completed(futures):
                try:
                    quotes = future.result()
                except Exception:
                    continue
                for code, quote in quotes.items():
                    if code not in seen_codes:
                        rows.append(quote)
                        seen_codes.add(code)

    min_rows = int(_cloud_os.environ.get("ASHARE_TENCENT_MIN_REAL_ROWS", "2500"))
    if len(rows) < min_rows:
        raise DataSourceError(f"腾讯全市场实时行情覆盖不足：{len(rows)}/{len(codes)}")

    df = normalize_spot_df(pd.DataFrame(rows))
    df.attrs["source"] = "腾讯全市场实时行情"
    df.attrs["updated_at"] = datetime.now().isoformat(timespec="seconds")
    df.attrs["data_scope"] = "full"
    effective_target = int(_cloud_os.environ.get("ASHARE_EFFECTIVE_COVERAGE_TARGET", "4200"))
    df.attrs["coverage_target"] = max(min(len(codes), effective_target), len(df))
    df.attrs["failed_batches"] = failed_batches
    df.attrs["available_universe"] = len(codes)
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
        if len(df) < 3000:
            return df, "云端真实行情模式：腾讯覆盖偏低但已达到可用真实样本，市场广度/涨跌停/成交额继续纳入模型并降低置信度。"
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


def load_runtime_state() -> dict:
    """Load runtime JSON and fill missing cloud macro/ETF states from public live data."""

    state = {
        "live_quotes": read_runtime_json("live_quotes.json"),
        "market_snapshot": read_runtime_json("market_snapshot_latest.json"),
        "macro": read_runtime_json("macro_regime_latest.json"),
        "etf_flow": read_runtime_json("etf_flow_latest.json"),
    }
    etf_flow = state.get("etf_flow") or {}
    if not (etf_flow.get("broad") or etf_flow.get("sector")):
        state["etf_flow"] = fetch_public_etf_flow_snapshot()
    macro = state.get("macro") or {}
    if not (isinstance(macro.get("scores"), dict) and macro.get("scores")):
        state["macro"] = fetch_public_macro_option_state(etf_flow=state.get("etf_flow") or {})
    return state
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
        app = app.replace(
            'st.sidebar.caption(f"实时图表每 {max(1, REALTIME_REFRESH_SECONDS)} 秒自动重绘；无需手动刷新页面。")\n',
            'st.sidebar.caption(f"实时图表每 {max(1, REALTIME_REFRESH_SECONDS)} 秒自动重绘；无需手动刷新页面。")\nst.sidebar.caption("内存优化已开启：实时更新后会自动清理过期缓存，并限制会话内ETF轨迹长度。")\n',
        )
        app = app.replace(
            'macro_meta = f"来源：宏观/期权实时参数 ｜ 更新时间：{human_time(macro_overlay.get(\'updated_at\'))}"',
            'macro_meta = f"来源：{macro_overlay.get(\'source\', \'宏观/期权实时参数\')} ｜ 更新时间：{human_time(macro_overlay.get(\'updated_at\'))}"',
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

    risk_model_path = root / "risk_model.py"
    if risk_model_path.exists():
        risk_model = risk_model_path.read_text(encoding="utf-8")
        risk_model = risk_model.replace(
            '"updated_at": macro_state.get("updated_at"),\n        "regime":',
            '"updated_at": macro_state.get("updated_at"),\n        "source": macro_state.get("source") or "宏观/期权实时参数",\n        "regime":',
        )
        risk_model = risk_model.replace(
            '        "宏观/期权实时参数",\n        macro_updated,',
            '        macro_option.get("source", "宏观/期权实时参数"),\n        macro_updated,',
        )
        risk_model_path.write_text(risk_model, encoding="utf-8")
