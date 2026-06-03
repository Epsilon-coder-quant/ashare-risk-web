"""Render patch for market volume display and safer refresh cadence."""

from __future__ import annotations

from pathlib import Path


DATA_LOADER_APPEND = r'''

# Render market volume summary override v1.
_volume_original_summarize_market_snapshot = summarize_market_snapshot


def summarize_market_snapshot(snapshot: pd.DataFrame) -> dict:
    """Add explicit full-market volume totals while preserving amount/breadth fields."""

    summary = _volume_original_summarize_market_snapshot(snapshot)
    if "volume" in snapshot.columns:
        volume = pd.to_numeric(snapshot["volume"], errors="coerce").fillna(0)
    else:
        volume = pd.Series(dtype=float)
    total_volume = float(volume.sum())
    summary["total_volume"] = total_volume
    summary["total_volume_shares"] = total_volume * 100
    summary["volume_unit"] = "手"
    return summary
'''


def patch(root: Path | str) -> None:
    """Patch restored Render files with explicit volume totals and stable refresh."""

    root = Path(root)
    data_loader_path = root / "data_loader.py"
    if data_loader_path.exists():
        data_loader = data_loader_path.read_text(encoding="utf-8")
        marker = "# Render market volume summary override v1."
        if marker not in data_loader:
            data_loader += DATA_LOADER_APPEND
            data_loader_path.write_text(data_loader, encoding="utf-8")

    app_path = root / "app.py"
    if not app_path.exists():
        return

    app = app_path.read_text(encoding="utf-8")
    app = app.replace("max(1, REALTIME_REFRESH_SECONDS)", "max(3, REALTIME_REFRESH_SECONDS)")
    if "def compact_volume(" not in app:
        app = app.replace(
            "\n\ndef pct(value: float) -> str:\n",
            "\n\ndef compact_volume(value: float) -> str:\n"
            "    \"\"\"Format full-market volume in lots, matching A-share quote units.\"\"\"\n\n"
            "    if pd.isna(value):\n"
            "        return \"-\"\n"
            "    if abs(value) >= 1e8:\n"
            "        return f\"{value / 1e8:.2f} 亿手\"\n"
            "    if abs(value) >= 1e4:\n"
            "        return f\"{value / 1e4:.2f} 万手\"\n"
            "    return f\"{value:,.0f} 手\"\n"
            "\n\n"
            "def pct(value: float) -> str:\n",
        )

    app = app.replace(
        '    col1, col2, col3, col4 = st.columns(4)\n'
        '    metric_with_meta(col1, "Market Risk Score", f"{live_market[\'score\']:.1f}", model_meta)\n'
        '    metric_with_meta(col2, "建议总仓位", live_market["suggested_position"], model_meta)\n'
        '    metric_with_meta(col3, "市场情绪状态", live_market["state"], model_meta)\n'
        '    metric_with_meta(col4, "全市场成交额", compact_money(live_breadth["total_amount"]), snapshot_meta)\n',
        '    col1, col2, col3, col4, col5 = st.columns(5)\n'
        '    metric_with_meta(col1, "Market Risk Score", f"{live_market[\'score\']:.1f}", model_meta)\n'
        '    metric_with_meta(col2, "建议总仓位", live_market["suggested_position"], model_meta)\n'
        '    metric_with_meta(col3, "市场情绪状态", live_market["state"], model_meta)\n'
        '    metric_with_meta(col4, "全市场成交额", compact_money(live_breadth["total_amount"]), snapshot_meta)\n'
        '    metric_with_meta(col5, "全市场成交量(手)", compact_volume(live_breadth.get("total_volume", 0)), snapshot_meta)\n',
    )
    app_path.write_text(app, encoding="utf-8")
