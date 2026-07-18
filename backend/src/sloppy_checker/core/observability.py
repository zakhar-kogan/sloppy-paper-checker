from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from .config import AppSettings

SAFE_ATTRIBUTE_KEYS = {
    "analysis.id",
    "analysis.state",
    "analysis.profile",
    "analysis.content_level",
    "analysis.source_format",
    "analysis.methodology_version",
    "analysis.methodology_hash",
    "analysis.reviewer_repaired",
    "analysis.assessed_items",
    "analysis.expected_items",
    "analysis.grounding_rate",
    "analysis.coverage",
    "model.role",
    "model.id",
    "model.attempt",
    "model.outcome",
    "model.input_tokens",
    "model.output_tokens",
    "model.total_tokens",
    "module.key",
    "module.state",
    "module.evidence_count",
    "provider.profile",
    "error.type",
}


def safe_attributes(**values: object) -> dict[str, str | int | float | bool]:
    attributes: dict[str, str | int | float | bool] = {}
    for key, value in values.items():
        if key not in SAFE_ATTRIBUTE_KEYS or value is None:
            continue
        if isinstance(value, (bool, int, float)):
            attributes[key] = value
        else:
            attributes[key] = str(value)[:160]
    return attributes


def _headers(value: str | None) -> dict[str, str] | None:
    if not value:
        return None
    result: dict[str, str] = {}
    for item in value.split(","):
        key, separator, header_value = item.partition("=")
        if separator and key.strip() and header_value.strip():
            result[key.strip()] = header_value.strip()
    return result or None


@lru_cache(maxsize=8)
def _configured_tracer(endpoint: str, headers: str, service_name: str):
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name[:120]})
    )
    exporter = OTLPSpanExporter(endpoint=endpoint, headers=_headers(headers))
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider.get_tracer("sloppy_checker.analysis")


def get_tracer(settings: AppSettings):
    if not settings.observability_enabled or not settings.otel_exporter_otlp_endpoint:
        return trace.NoOpTracerProvider().get_tracer("sloppy_checker.analysis")
    return _configured_tracer(
        settings.otel_exporter_otlp_endpoint,
        settings.otel_exporter_otlp_headers or "",
        settings.otel_service_name,
    )


def _response_usage(response: object) -> dict[str, int]:
    metrics = getattr(response, "metrics", None)
    if metrics is None:
        return {}
    return {
        key: value
        for key in ("input_tokens", "output_tokens", "total_tokens")
        if isinstance((value := getattr(metrics, key, None)), int)
    }


@dataclass
class AnalysisTelemetry:
    tracer: Any
    span: Any
    context: Any
    finished: bool = False

    @classmethod
    def start(cls, settings: AppSettings, analysis_id: str) -> AnalysisTelemetry:
        tracer = get_tracer(settings)
        span = tracer.start_span(
            "spc.analysis.execute",
            attributes=safe_attributes(**{"analysis.id": analysis_id}),
        )
        return cls(tracer=tracer, span=span, context=trace.set_span_in_context(span))

    def annotate(self, **attributes: object) -> None:
        self.span.set_attributes(safe_attributes(**attributes))

    def event(self, name: str, **attributes: object) -> None:
        self.span.add_event(name, attributes=safe_attributes(**attributes))

    async def model_request(
        self,
        awaitable: Awaitable[Any],
        *,
        role: str,
        model_id: str,
        attempt: str,
        module: str | None = None,
    ) -> Any:
        span = self.tracer.start_span(
            "spc.model.request",
            context=self.context,
            attributes=safe_attributes(
                **{
                    "model.role": role,
                    "model.id": model_id,
                    "model.attempt": attempt,
                    "module.key": module,
                }
            ),
        )
        try:
            response = await awaitable
        except Exception as exc:
            span.set_attributes(
                safe_attributes(
                    **{"model.outcome": "error", "error.type": type(exc).__name__}
                )
            )
            raise
        else:
            usage = _response_usage(response)
            span.set_attributes(
                safe_attributes(
                    **{
                        "model.outcome": "completed",
                        "model.input_tokens": usage.get("input_tokens"),
                        "model.output_tokens": usage.get("output_tokens"),
                        "model.total_tokens": usage.get("total_tokens"),
                    }
                )
            )
            return response
        finally:
            span.end()

    def finish(self, state: str, error: Exception | None = None) -> None:
        if self.finished:
            return
        self.finished = True
        values: dict[str, object] = {"analysis.state": state}
        if error is not None:
            values["error.type"] = type(error).__name__
        self.annotate(**values)
        self.span.end()
