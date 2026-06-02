"""Bootstrap the Render build restore script from chunked source payload."""

from __future__ import annotations

import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHUNK_DIR = ROOT / "full_market_payload_parts"


def _run_patch(name: str) -> None:
    patch_file = ROOT / name
    if patch_file.exists():
        patch_namespace = {"__file__": str(patch_file), "__name__": name.removesuffix(".py")}
        exec(compile(patch_file.read_text(encoding="utf-8"), str(patch_file), "exec"), patch_namespace)
        patch_namespace["patch"](ROOT)


def main() -> None:
    payload = "".join(path.read_text(encoding="ascii").strip() for path in sorted(CHUNK_DIR.glob("part_*.b64")))
    namespace = {"__file__": str(Path(__file__).resolve()), "__name__": "restore_payload_full_market"}
    exec(compile(base64.b64decode(payload).decode("utf-8"), str(Path(__file__).resolve()), "exec"), namespace)
    namespace["main"]()
    for patch_name in ("cloud_runtime_patch.py", "realtime_index_patch.py"):
        _run_patch(patch_name)


if __name__ == "__main__":
    main()
