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
    print("Restored Streamlit deployment payload.")


if __name__ == "__main__":
    main()
