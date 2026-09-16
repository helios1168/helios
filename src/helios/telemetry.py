"""OpenTelemetry span export over OTLP/HTTP (SPEC section 21).

This is the only module in helios that imports the OpenTelemetry packages. The
rest of helios builds a small, dependency-free :class:`Span` tree and calls
:func:`export`; nothing else here is visible to callers, so telemetry stays
removable. Export never fails a run: every exception from the exporter is
caught, the whole operation is bounded by ``config.timeout_s``, and the only
externally visible effect of a failed or skipped export is the note string
:func:`export` returns for the caller to record once on the attempt record.

Transport is OTLP over HTTP with protobuf, never gRPC, to the signal-specific
traces endpoint named by ``config.endpoint``. Credentials come from the
environment only (``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY``); helios
builds the ``Authorization: Basic <base64 of "public:secret">`` header from
them and always sends ``x-langfuse-ingestion-version: 4``.
"""

from __future__ import annotations

import base64
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime

from helios.config import TelemetryConfig

AttrValue = str | int | float | bool


@dataclass(frozen=True)
class Span:
    """One span: a name, start/end timestamps (SPEC section 4.3 format), attributes
    and child spans. Attributes carry identifiers, enumerated statuses, timings,
    commit hashes and check names only (SPEC section 21); callers must never put
    prompt text, report text, agent output or file paths here.
    """

    name: str
    start: str
    end: str
    attributes: dict[str, AttrValue] = field(default_factory=dict)
    children: tuple[Span, ...] = ()


def export(config: TelemetryConfig, root: Span) -> str | None:
    """Export one trace rooted at ``root``; never raises (SPEC section 21).

    Returns ``None`` when telemetry is disabled or the export succeeded, else
    a one-line note naming the reason it did not happen, for the caller to
    record once on the attempt record. Bounded by ``config.timeout_s``: the
    export runs in a daemon thread, and this function returns as soon as that
    thread finishes or the timeout elapses, whichever comes first, so a
    hanging connection can never make a run wait longer than the configured
    timeout.
    """
    if not config.enabled:
        return None
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        return (
            "telemetry: LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY is missing; "
            "export skipped"
        )
    outcome: dict[str, BaseException] = {}

    def _run() -> None:
        try:
            _do_export(config, root, public_key, secret_key)
        except Exception as exc:  # export must never escape into the caller
            outcome["error"] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(config.timeout_s)
    if worker.is_alive():
        return f"telemetry: export did not finish within {config.timeout_s}s"
    error = outcome.get("error")
    if error is not None:
        return f"telemetry: export failed: {error}"
    return None


def _do_export(config: TelemetryConfig, root: Span, public_key: str, secret_key: str) -> None:
    """Build the spans, then export them in one OTLP/HTTP call.

    Spans are built with a scratch :class:`TracerProvider` wired to an
    in-memory exporter, so building the trace never touches the network; the
    finished spans are then handed to one real :class:`OTLPSpanExporter` call
    whose :class:`SpanExportResult` this function checks directly. The SDK's
    ``BatchSpanProcessor``/``force_flush`` path is not used here because it
    swallows a failed export's result (it only logs, and always reports
    success back to the caller), which would silently defeat the "a failed
    export is recorded" rule (SPEC section 21).
    """
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import set_span_in_context

    memory_exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": config.service_name}))
    provider.add_span_processor(SimpleSpanProcessor(memory_exporter))
    tracer = provider.get_tracer("helios")
    try:
        _emit(tracer, root, None, set_span_in_context)
    finally:
        provider.shutdown()
    spans = memory_exporter.get_finished_spans()

    credentials = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    headers = {
        "Authorization": f"Basic {credentials}",
        "x-langfuse-ingestion-version": "4",
    }
    exporter = OTLPSpanExporter(endpoint=config.endpoint, headers=headers, timeout=config.timeout_s)
    try:
        result = exporter.export(spans)
    finally:
        exporter.shutdown()
    if result is not SpanExportResult.SUCCESS:
        raise RuntimeError("OTLP export did not succeed")


def _emit(tracer, span: Span, parent_ctx, set_span_in_context) -> None:
    """Start ``span`` under ``parent_ctx``, recurse into its children, then end it,
    so every child ends before its parent (SPEC section 21 span order)."""
    otel_span = tracer.start_span(span.name, context=parent_ctx, start_time=_to_ns(span.start))
    for key, value in span.attributes.items():
        otel_span.set_attribute(key, value)
    child_ctx = set_span_in_context(otel_span, parent_ctx)
    for child in span.children:
        _emit(tracer, child, child_ctx, set_span_in_context)
    otel_span.end(end_time=_to_ns(span.end))


def _to_ns(timestamp: str) -> int:
    """Parse a SPEC section 4.3 UTC ISO 8601 ``Z`` timestamp to epoch nanoseconds."""
    dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1_000_000_000)
