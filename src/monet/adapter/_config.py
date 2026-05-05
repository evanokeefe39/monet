from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import BaseModel, model_validator

from ._env import interpolate_obj


class RequestConfig(BaseModel):
    body: dict[str, Any] = {}
    params: dict[str, str] = {}
    method: str = "POST"


class ResponseConfig(BaseModel):
    output: str = ""
    artifacts: dict[str, str] = {}


class ProcessConfig(BaseModel):
    command: list[str] = []
    workdir: str | None = None
    ready_timeout: int = 120
    env: dict[str, str] = {}


class StdioConfig(BaseModel):
    command: list[str] = []
    plugin: str = ""
    init_rpc: str | None = None


class AdapterConfig(BaseModel):
    name: str
    type: Literal["openai", "http", "stdio", "plugin"]
    url: str = ""
    port: int = 8080
    timeout: int = 300
    health: str | None = None
    model: str | None = None
    auth: str | None = None
    ready_timeout: int = 120
    # Top-level command shorthand — merged into process.command if process.command empty
    command: list[str] = []
    # type=plugin needs top-level plugin path ("mod:fn")
    plugin: str | None = None
    request: RequestConfig = RequestConfig()
    response: ResponseConfig = ResponseConfig()
    process: ProcessConfig = ProcessConfig()
    headers: dict[str, str] = {}
    stdio: StdioConfig = StdioConfig()

    @model_validator(mode="after")
    def _validate_and_merge(self) -> AdapterConfig:
        # Merge top-level command shorthand into process
        if self.command and not self.process.command:
            self.process = ProcessConfig(
                command=self.command,
                workdir=self.process.workdir,
                ready_timeout=self.process.ready_timeout,
                env=self.process.env,
            )

        if self.type in ("openai", "http") and not self.url:
            raise ValueError(f"type={self.type!r} requires url")
        if self.type == "http" and not self.response.output:
            raise ValueError("type='http' requires [response].output")
        if self.type == "stdio":
            if not self.stdio.command:
                raise ValueError("type='stdio' requires [stdio].command")
            if not self.stdio.plugin:
                raise ValueError("type='stdio' requires [stdio].plugin")
        if self.type == "plugin" and not self.plugin:
            raise ValueError("type='plugin' requires plugin = 'mod:fn'")
        return self


def load_config(path: Path) -> AdapterConfig:
    """Read TOML, interpolate env vars, validate, return config."""
    with open(path, "rb") as f:
        raw: dict[str, Any] = tomllib.load(f)
    raw = interpolate_obj(raw)
    return AdapterConfig.model_validate(raw)
