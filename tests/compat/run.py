"""Compat harness: drive each scenario through both clients, diff outputs.

Usage:
    python -m tests.compat.run [--binary PATH] [--scenario FILE ...]
    python -m tests.compat.run --only-py  # skip Go side (no binary needed)

Boots one DevServer per invocation (reused across scenarios for speed),
runs each scenario through the Python driver and the Go binary, normalizes
both JSONL streams, and asserts the event-kind sequences match. Exits 1
on any divergence.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import difflib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

from tests.compat._server import DevServer
from tests.compat.normalize import normalize_stream
from tests.compat.py_headless import run_scenario


@dataclass
class ServerHandle:
    url: str
    api_key: str


@contextlib.contextmanager
def _server(url: str | None, api_key: str) -> Iterator[ServerHandle]:
    if url:
        yield ServerHandle(url=url, api_key=api_key)
        return
    with DevServer() as s:
        yield ServerHandle(url=s.url, api_key=s.api_key)


HERE = Path(__file__).parent
SCENARIOS_DIR = HERE / "scenarios"
REPO_ROOT = HERE.parent.parent
DEFAULT_BINARY = (
    REPO_ROOT / "go" / "monet-tui.exe"
    if sys.platform == "win32"
    else REPO_ROOT / "go" / "monet-tui"
)


def _collect_scenarios(paths: list[Path] | None) -> list[Path]:
    if paths:
        return paths
    return sorted(SCENARIOS_DIR.glob("*.json"))


def _ensure_binary(path: Path) -> Path:
    if path.exists():
        return path
    print(f"building {path}...", file=sys.stderr)
    out = subprocess.run(
        ["go", "build", "-o", str(path), "./cmd/monet-tui"],
        cwd=str(REPO_ROOT / "go"),
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"go build failed:\n{out.stderr}")
    return path


async def _run_python(scenario: Path, server: ServerHandle, out: Path) -> None:
    doc = json.loads(scenario.read_text())
    with out.open("w", encoding="utf-8") as f:
        await run_scenario(
            doc,
            server_url=server.url,
            api_key=server.api_key,
            sink=f,
        )


def _run_go(binary: Path, scenario: Path, server: ServerHandle, out: Path) -> None:
    import os

    env = os.environ.copy()
    env["MONET_SERVER_URL"] = server.url
    env["MONET_API_KEY"] = server.api_key
    result = subprocess.run(
        [str(binary), "--headless", "--scenario", str(scenario)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    out.write_text(result.stdout or "", encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(
            f"go binary failed on {scenario.name}:\n{result.stderr}",
        )


def _kind_sequence(records: list[dict[str, object]]) -> list[str]:
    return [str(r.get("kind", "?")) for r in records]


def _diff_scenario(name: str, py_raw: str, go_raw: str) -> list[str]:
    py_norm = normalize_stream(py_raw)
    go_norm = normalize_stream(go_raw)
    py_kinds = _kind_sequence(py_norm)
    go_kinds = _kind_sequence(go_norm)
    if py_kinds == go_kinds:
        return []
    diff = list(
        difflib.unified_diff(
            py_kinds,
            go_kinds,
            fromfile=f"{name}.py",
            tofile=f"{name}.go",
            lineterm="",
        )
    )
    return diff


async def _main_async(args: argparse.Namespace) -> int:
    scenarios = _collect_scenarios(args.scenario)
    if not scenarios:
        print("no scenarios found", file=sys.stderr)
        return 1

    binary: Path | None = None
    if not args.only_py:
        binary = _ensure_binary(Path(args.binary) if args.binary else DEFAULT_BINARY)

    out_dir = HERE / "_out"
    out_dir.mkdir(exist_ok=True)
    failures: list[str] = []

    with _server(args.server_url, args.api_key) as server:
        for sc in scenarios:
            name = sc.stem
            py_out = out_dir / f"{name}.py.jsonl"
            go_out = out_dir / f"{name}.go.jsonl"
            try:
                await _run_python(sc, server, py_out)
            except Exception as e:
                failures.append(f"{name}: python driver raised: {e}")
                continue
            if binary is not None:
                try:
                    _run_go(binary, sc, server, go_out)
                except Exception as e:
                    failures.append(f"{name}: go binary raised: {e}")
                    continue
                diff = _diff_scenario(
                    name,
                    py_out.read_text(encoding="utf-8", errors="replace"),
                    go_out.read_text(encoding="utf-8", errors="replace"),
                )
                if diff:
                    failures.append(
                        f"{name}: event-kind divergence:\n" + "\n".join(diff),
                    )
                else:
                    print(f"ok  {name}")
            else:
                print(f"ok  {name} (py only)")

    if failures:
        print("\nFAILURES:\n" + "\n\n".join(failures), file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="tests.compat.run")
    ap.add_argument("--binary", default=None, help="path to monet-tui binary")
    ap.add_argument(
        "--scenario",
        type=Path,
        action="append",
        help="scenario file (repeatable)",
    )
    ap.add_argument("--only-py", action="store_true", help="run Python side only")
    ap.add_argument(
        "--server-url",
        default=None,
        help="skip DevServer, use this already-running monet server",
    )
    ap.add_argument("--api-key", default="", help="api key for --server-url")
    args = ap.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
