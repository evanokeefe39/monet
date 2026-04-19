"""Capture FastAPI /openapi.json snapshot for Go conformance tests.

Boots DevServer, fetches /openapi.json, writes tests/compat/openapi.json
pretty-printed. Re-run when the server adds/changes routes; diff the file
in PRs to catch unintended surface changes.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

from tests.compat._server import DevServer

SNAPSHOT = Path(__file__).parent / "openapi.json"


def capture(out_path: Path = SNAPSHOT) -> Path:
    with (
        DevServer() as server,
        urllib.request.urlopen(f"{server.url}/openapi.json", timeout=10) as resp,
    ):
        spec = json.load(resp)
    out_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
    return out_path


def main() -> int:
    path = capture()
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
