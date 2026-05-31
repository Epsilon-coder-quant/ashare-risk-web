"""Restore the deployable Streamlit app from payload parts during Render build."""

from __future__ import annotations

import base64
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARTS = ROOT / "payload_parts"
ARCHIVE = ROOT / "payload.tar.gz"
EXPECTED_B64_LENGTH = 63096
KNOWN_REPAIRS = {
    "TSLYZIClatWpLYQOU": "TSLYZIClatWpVLYQOU",
}


FAST_SAMPLE_BLOCK = '''    if FAST_START and (CLOUD_FAST_MODE or runtime_df is None):
        try:
            df = fetch_public_fast_market_snapshot()
            return df, "快速联网模式：使用真实公开行情样本快照；因不是全市场口径，市场广度/涨跌停不参与核心加权。"
        except Exception:
            if CLOUD_FAST_MODE:
                raise

'''


def patch_restored_files() -> None:
    """Keep the cloud build on the same full-market model path as desktop."""

    config_path = ROOT / "config.py"
    data_loader_path = ROOT / "data_loader.py"

    if config_path.exists():
        config = config_path.read_text(encoding="utf-8")
        config = config.replace(
            'CLOUD_FAST_MODE = os.environ.get("ASHARE_RISK_CLOUD_FAST_MODE", os.environ.get("RENDER", "0")).strip() in {"1", "true", "TRUE", "yes", "YES"}',
            'CLOUD_FAST_MODE = os.environ.get("ASHARE_RISK_CLOUD_FAST_MODE", "0").strip() in {"1", "true", "TRUE", "yes", "YES"}',
        )
        config_path.write_text(config, encoding="utf-8")

    if data_loader_path.exists():
        data_loader = data_loader_path.read_text(encoding="utf-8")
        data_loader = data_loader.replace(
            '            if FAST_START:\n                cached.attrs["cache_stale_seconds"] = age\n                return cached',
            '            if FAST_START and not is_a_share_trading_session():\n                cached.attrs["cache_stale_seconds"] = age\n                return cached',
        )
        data_loader = data_loader.replace(FAST_SAMPLE_BLOCK, "")
        data_loader_path.write_text(data_loader, encoding="utf-8")


def main() -> None:
    chunks = []
    for path in sorted(PARTS.glob("part_*.b64")):
        chunks.append(path.read_text(encoding="ascii"))
    if not chunks:
        raise SystemExit("No payload parts found.")

    payload = "".join("".join(chunks).split())
    for broken, fixed in KNOWN_REPAIRS.items():
        payload = payload.replace(broken, fixed)
    if len(payload) != EXPECTED_B64_LENGTH:
        raise SystemExit(f"Unexpected payload length: {len(payload)}")

    ARCHIVE.write_bytes(base64.b64decode(payload))
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        archive.extractall(ROOT)
    ARCHIVE.unlink(missing_ok=True)
    patch_restored_files()
    print("Restored Streamlit deployment payload in full-market realtime mode.")


if __name__ == "__main__":
    main()
