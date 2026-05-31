"""Bootstrap the Render build restore script from chunked source payload."""

from __future__ import annotations

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHUNK_DIR = ROOT / "full_market_payload_parts"


def main() -> None:
    payload = "".join(path.read_text(encoding="ascii").strip() for path in sorted(CHUNK_DIR.glob("part_*.b64")))
    namespace = {"__file__": str(Path(__file__).resolve()), "__name__": "restore_payload_full_market"}
    exec(compile(base64.b64decode(payload).decode("utf-8"), str(Path(__file__).resolve()), "exec"), namespace)
    namespace["main"]()


if __name__ == "__main__":
    main()
