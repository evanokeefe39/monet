from __future__ import annotations

from pathlib import Path  # noqa: TC003 — pydantic needs this at runtime
from typing import Any

from pydantic import BaseModel, ConfigDict

from .._env import (
    HONEYCOMB_API_KEY,
    HONEYCOMB_DATASET,
    LANGFUSE_HOST,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGSMITH_API_KEY,
    LANGSMITH_PROJECT,
    MONET_TRACE_FILE,
    OTEL_EXPORTER_OTLP_ENDPOINT,
    OTEL_EXPORTER_OTLP_HEADERS,
    OTEL_SERVICE_NAME,
    read_path,
    read_str,
)
from ._common import _DEFAULT_LANGFUSE_HOST, _UNSET, _redact


class ObservabilityConfig(BaseModel):
    """Tracing configuration.

    Resolves OTLP endpoint and headers from three vendor shortcuts
    (Langfuse, LangSmith, Honeycomb) without mutating ``os.environ``.
    Use :meth:`otlp_endpoint_and_headers` to get the final values to
    hand to an OTel exporter.
    """

    model_config = ConfigDict(frozen=True)

    service_name: str = "monet"
    trace_file: Path | None = None
    otlp_endpoint: str | None = None
    otlp_headers: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = _DEFAULT_LANGFUSE_HOST
    langsmith_api_key: str | None = None
    langsmith_project: str | None = None
    honeycomb_api_key: str | None = None
    honeycomb_dataset: str | None = None

    @classmethod
    def load(cls) -> ObservabilityConfig:
        return cls(
            service_name=read_str(OTEL_SERVICE_NAME, "monet") or "monet",
            trace_file=read_path(MONET_TRACE_FILE),
            otlp_endpoint=read_str(OTEL_EXPORTER_OTLP_ENDPOINT),
            otlp_headers=read_str(OTEL_EXPORTER_OTLP_HEADERS),
            langfuse_public_key=read_str(LANGFUSE_PUBLIC_KEY),
            langfuse_secret_key=read_str(LANGFUSE_SECRET_KEY),
            langfuse_host=(
                read_str(LANGFUSE_HOST, _DEFAULT_LANGFUSE_HOST)
                or _DEFAULT_LANGFUSE_HOST
            ),
            langsmith_api_key=read_str(LANGSMITH_API_KEY),
            langsmith_project=read_str(LANGSMITH_PROJECT),
            honeycomb_api_key=read_str(HONEYCOMB_API_KEY),
            honeycomb_dataset=read_str(HONEYCOMB_DATASET),
        )

    def otlp_endpoint_and_headers(self) -> tuple[str | None, str | None]:
        """Resolve final OTLP endpoint + headers from vendor shortcuts.

        Precedence: explicit ``OTEL_EXPORTER_OTLP_ENDPOINT`` wins; then
        Langfuse if public+secret keys are present; then Honeycomb if
        its API key is present; then LangSmith. Returns ``(None, None)``
        when no target is configured.
        """
        if self.otlp_endpoint:
            return self.otlp_endpoint, self.otlp_headers

        if self.langfuse_public_key and self.langfuse_secret_key:
            import base64

            host = self.langfuse_host.rstrip("/")
            endpoint = f"{host}/api/public/otel"
            token = base64.b64encode(
                f"{self.langfuse_public_key}:{self.langfuse_secret_key}".encode()
            ).decode()
            return endpoint, f"Authorization=Basic {token}"

        if self.honeycomb_api_key:
            headers = f"x-honeycomb-team={self.honeycomb_api_key}"
            if self.honeycomb_dataset:
                headers += f",x-honeycomb-dataset={self.honeycomb_dataset}"
            return "https://api.honeycomb.io", headers

        if self.langsmith_api_key:
            headers = f"x-api-key={self.langsmith_api_key}"
            if self.langsmith_project:
                headers += f",Langsmith-Project={self.langsmith_project}"
            return "https://api.smith.langchain.com/otel", headers

        return None, None

    def otlp_headers_dict(self) -> dict[str, str] | None:
        """Return OTLP headers as a dict suitable for OTLPSpanExporter.

        Parses the comma-separated ``key=value`` form that OTel uses for
        the ``OTEL_EXPORTER_OTLP_HEADERS`` variable. Returns ``None``
        when no headers are configured so callers can pass the value
        straight through to the exporter constructor.
        """
        _, headers = self.otlp_endpoint_and_headers()
        if not headers:
            return None
        pairs: dict[str, str] = {}
        for part in headers.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                pairs[k.strip()] = v.strip()
        return pairs or None

    def redacted_summary(self) -> dict[str, Any]:
        endpoint, _ = self.otlp_endpoint_and_headers()
        return {
            "service_name": self.service_name,
            "trace_file": str(self.trace_file) if self.trace_file else _UNSET,
            "otlp_endpoint": endpoint or _UNSET,
            "langfuse": _redact(self.langfuse_public_key and self.langfuse_secret_key),
            "langsmith": _redact(self.langsmith_api_key),
            "honeycomb": _redact(self.honeycomb_api_key),
        }
