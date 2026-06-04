"""Background full-market updater for Render free web services."""

from __future__ import annotations

import argparse
import json
import pickle
import time
from datetime import datetime

import pandas as pd

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


def _write_snapshot(df: pd.DataFrame, source: str) -> None:
    """Persist a real full-market snapshot for Streamlit sessions."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    mark_dataframe(df, source, "full")
    updated_at = str(df.attrs.get("updated_at") or datetime.now().isoformat(timespec="seconds"))

    cache = cache_key("spot", "tencent_full_market" if "腾讯" in source else "eastmoney")
    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("wb") as f:
        pickle.dump(df, f)

    payload = {
        "updated_at": updated_at,
        "source": source,
        "data_scope": "full",
        "scope": "full",
        "record_count": int(len(df)),
        "records": _records(df),
    }
    tmp = DATA_DIR / "market_snapshot_latest.json.tmp"
    final = DATA_DIR / "market_snapshot_latest.json"
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(final)


def update_once() -> bool:
    """Fetch and store one full-market snapshot."""

    try:
        df = fetch_tencent_full_market_snapshot()
        _write_snapshot(df, "腾讯全市场实时行情")
        print(f"[market-updater] full snapshot ok rows={len(df)} source=腾讯", flush=True)
        return True
    except Exception as tx_exc:
        print(f"[market-updater] tencent failed: {concise_error(tx_exc)}", flush=True)

    try:
        df = fetch_eastmoney_market_snapshot()
        _write_snapshot(df, "Eastmoney push2")
        print(f"[market-updater] full snapshot ok rows={len(df)} source=Eastmoney", flush=True)
        return True
    except Exception as em_exc:
        print(f"[market-updater] eastmoney failed: {concise_error(em_exc)}", flush=True)
        return False
    finally:
        _cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--initial-delay", type=int, default=25)
    args = parser.parse_args()
    if args.initial_delay > 0:
        time.sleep(args.initial_delay)
    while True:
        update_once()
        _cleanup()
        time.sleep(max(30, int(args.interval)))


if __name__ == "__main__":
    main()
