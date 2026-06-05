"""Background full-market updater for Render free web services."""

from __future__ import annotations

import argparse
import json
import os
import pickle
import time
from datetime import datetime

import pandas as pd

import data_loader as dl
from config import DATA_DIR
from data_loader import (
    cache_key,
    cleanup_runtime_memory,
    concise_error,
    fetch_eastmoney_market_snapshot,
    fetch_tencent_full_market_snapshot,
    mark_dataframe,
)


def _cleanup() -> None:
    """Release old cache/session objects without crashing the updater."""

    try:
        cleanup_runtime_memory(force=True)
    except TypeError:
        cleanup_runtime_memory()
    except Exception:
        pass


def _records(df: pd.DataFrame) -> list[dict]:
    """Convert a snapshot DataFrame to compact JSON records."""

    safe = df.copy()
    for col in safe.columns:
        if pd.api.types.is_datetime64_any_dtype(safe[col]):
            safe[col] = safe[col].dt.strftime("%Y-%m-%d %H:%M:%S")
    safe = safe.where(pd.notna(safe), None)
    return json.loads(safe.to_json(orient="records", force_ascii=False))


def _write_status(status: str, **fields: object) -> None:
    """Write updater diagnostics that survive Streamlit reruns."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        **fields,
    }
    tmp = DATA_DIR / "market_snapshot_status.json.tmp"
    final = DATA_DIR / "market_snapshot_status.json"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(final)


def _write_snapshot(
    df: pd.DataFrame,
    source: str,
    data_scope: str = "full",
    coverage_target: int | None = None,
) -> None:
    """Persist a real full-market snapshot for Streamlit sessions."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    mark_dataframe(df, source, data_scope)
    if coverage_target is not None:
        df.attrs["coverage_target"] = int(coverage_target)
    updated_at = str(df.attrs.get("updated_at") or datetime.now().isoformat(timespec="seconds"))

    cache = cache_key("spot", "tencent_full_market" if "腾讯" in source else "eastmoney")
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as f:
        pickle.dump(df, f)

    payload = {
        "updated_at": updated_at,
        "source": source,
        "data_scope": data_scope,
        "scope": data_scope,
        "coverage_target": int(coverage_target or len(df)),
        "record_count": int(len(df)),
        "records": _records(df),
    }
    tmp = DATA_DIR / "market_snapshot_latest.json.tmp"
    final = DATA_DIR / "market_snapshot_latest.json"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(final)
    _write_status(
        "ok",
        source=source,
        data_scope=data_scope,
        record_count=int(len(df)),
        coverage_target=int(coverage_target or len(df)),
    )


def _market_codes() -> list[str]:
    """Return the embedded A-share universe used by the restored Render payload."""

    if hasattr(dl, "_cloud_market_codes"):
        return list(dl._cloud_market_codes())
    if hasattr(dl, "load_market_universe"):
        return [str(item.get("code")) for item in dl.load_market_universe() if item.get("code")]
    return []


def _fetch_tencent_batch(codes: list[str]) -> dict[str, dict]:
    """Fetch one Tencent batch using the fastest available cloud helper."""

    fetcher = getattr(dl, "_cloud_fetch_tencent_spots_fast", None) or dl.fetch_tencent_spots
    return fetcher(codes)


def fetch_tencent_progressive_snapshot() -> pd.DataFrame:
    """Fetch Tencent breadth progressively and persist partial progress."""

    codes = _market_codes()
    if not codes:
        raise RuntimeError("市场代码表为空")

    batch_size = int(os.environ.get("ASHARE_TENCENT_BATCH_SIZE", "220"))
    min_rows = int(os.environ.get("ASHARE_TENCENT_MIN_REAL_ROWS", "1500"))
    coverage_target = int(os.environ.get("ASHARE_EFFECTIVE_COVERAGE_TARGET", "5000"))
    checkpoint_rows = int(os.environ.get("ASHARE_TENCENT_CHECKPOINT_ROWS", "500"))
    max_seconds = int(os.environ.get("ASHARE_TENCENT_PROGRESSIVE_MAX_SECONDS", "85"))

    rows: list[dict] = []
    seen: set[str] = set()
    failed_batches = 0
    last_error = ""
    started = time.monotonic()

    for start in range(0, len(codes), batch_size):
        if time.monotonic() - started > max_seconds and len(rows) >= checkpoint_rows:
            break
        batch = codes[start : start + batch_size]
        try:
            quotes = _fetch_tencent_batch(batch)
        except Exception as exc:
            failed_batches += 1
            last_error = concise_error(exc)
            _write_status(
                "fetching",
                source="腾讯渐进式市场广度",
                record_count=len(rows),
                failed_batches=failed_batches,
                last_error=last_error,
            )
            continue

        for code in batch:
            quote = quotes.get(code)
            if quote and code not in seen:
                rows.append(quote)
                seen.add(code)

        if len(rows) >= checkpoint_rows:
            df = dl.normalize_spot_df(pd.DataFrame(rows))
            scope = "partial" if len(rows) < 3000 else "full"
            _write_snapshot(df, "腾讯渐进式市场广度", scope, coverage_target)

    if len(rows) < checkpoint_rows:
        raise RuntimeError(f"腾讯渐进式市场广度覆盖不足：{len(rows)}/{len(codes)}；{last_error}")

    df = dl.normalize_spot_df(pd.DataFrame(rows))
    scope = "partial" if len(rows) < 3000 else "full"
    _write_snapshot(df, "腾讯渐进式市场广度", scope, coverage_target)
    if len(rows) < min_rows:
        _write_status(
            "partial_low_coverage",
            source="腾讯渐进式市场广度",
            data_scope=scope,
            record_count=len(rows),
            coverage_target=coverage_target,
            failed_batches=failed_batches,
            last_error=last_error,
        )
    return df


def update_once() -> bool:
    """Fetch and store one full-market snapshot."""

    _write_status("starting", source="后台全市场广度更新器")
    try:
        df = fetch_tencent_progressive_snapshot()
        print(f"[market-updater] progressive snapshot ok rows={len(df)}", flush=True)
        return True
    except Exception as tx_exc:
        tx_error = concise_error(tx_exc)
        print(f"[market-updater] progressive tencent failed: {tx_error}", flush=True)

    try:
        df = fetch_tencent_full_market_snapshot()
        _write_snapshot(
            df,
            "腾讯全市场实时行情",
            "full",
            int(os.environ.get("ASHARE_EFFECTIVE_COVERAGE_TARGET", str(len(df)))),
        )
        print(f"[market-updater] full snapshot ok rows={len(df)} source=腾讯", flush=True)
        return True
    except Exception as full_tx_exc:
        full_tx_error = concise_error(full_tx_exc)
        print(f"[market-updater] full tencent failed: {full_tx_error}", flush=True)

    try:
        df = fetch_eastmoney_market_snapshot()
        _write_snapshot(
            df,
            "Eastmoney push2",
            "full",
            int(os.environ.get("ASHARE_EFFECTIVE_COVERAGE_TARGET", str(len(df)))),
        )
        print(f"[market-updater] full snapshot ok rows={len(df)} source=Eastmoney", flush=True)
        return True
    except Exception as em_exc:
        em_error = concise_error(em_exc)
        print(f"[market-updater] eastmoney failed: {em_error}", flush=True)
        _write_status(
            "failed",
            source="后台全市场广度更新器",
            tencent_progressive_error=tx_error,
            tencent_full_error=full_tx_error,
            eastmoney_error=em_error,
        )
        return False
    finally:
        _cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--initial-delay", type=int, default=10)
    args = parser.parse_args()
    if args.initial_delay > 0:
        time.sleep(args.initial_delay)
    while True:
        update_once()
        _cleanup()
        time.sleep(max(30, int(args.interval)))


if __name__ == "__main__":
    main()
