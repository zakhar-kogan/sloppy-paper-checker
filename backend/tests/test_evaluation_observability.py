from sloppy_checker.core.config import AppSettings
from sloppy_checker.core.observability import get_tracer, safe_attributes


def test_observability_allowlist_cannot_export_sensitive_content():
    attributes = safe_attributes(
        **{
            "analysis.id": "analysis-1",
            "model.id": "worker-model",
            "paper.text": "sensitive paper text",
            "prompt": "sensitive prompt",
            "raw.response": "sensitive response",
            "source.url": "https://publisher.example/paper",
            "credential": "secret",
        }
    )
    assert attributes == {
        "analysis.id": "analysis-1",
        "model.id": "worker-model",
    }


def test_observability_is_noop_by_default_and_requires_endpoint_when_enabled():
    settings = AppSettings(observability_enabled=False)
    assert get_tracer(settings).__class__.__name__ == "NoOpTracer"
    enabled = AppSettings(observability_enabled=True)
    try:
        enabled.validate_adapters()
    except ValueError as exc:
        assert "OTLP_ENDPOINT" in str(exc)
    else:
        raise AssertionError("enabled telemetry without an endpoint must be rejected")
