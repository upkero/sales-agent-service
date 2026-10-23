"""Tracing and metrics setup, identical in every service (CANON C19).

Configuration is the standard OTel environment, not a settings class, so this
file can be copied verbatim: OTEL_EXPORTER_OTLP_ENDPOINT turns export on,
OTEL_SERVICE_NAME overrides the name given in code, OTEL_RESOURCE_ATTRIBUTES
carries deployment.environment, OTEL_TRACES_SAMPLER the sampler,
OTEL_METRIC_EXPORT_INTERVAL the metric push interval, and
OTEL_PYTHON_FASTAPI_EXCLUDED_URLS the paths the server spans skip.
"""

import hashlib
import hmac
import os
import re
import unicodedata
from importlib.metadata import PackageNotFoundError, version
from typing import Any

import opentelemetry.instrumentation.fastapi as otel_fastapi
from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

UNMATCHED_ROUTE = "<unmatched>"


def setup_tracing(service_name: str, distribution: str) -> TracerProvider:
    """Install the process-wide TracerProvider, once.

    Idempotent because the global provider can be set only once per process,
    and the app factory runs more than once under tests. With no collector
    configured, spans are still created (logs carry their ids) but go nowhere,
    rather than an exporter retrying localhost:4317 in the background forever.
    """
    # The stable HTTP names (http.server.request.duration, http.route, ...) are what the
    # dashboards are written against; the library's default is the old http.server.duration.
    # Read once, by whichever HTTP instrumentation attaches first, so it is set here, ahead
    # of all of them. An explicit value in the environment wins.
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")
    current = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current
    try:
        service_version = version(distribution)
    except PackageNotFoundError:
        service_version = "unknown"
    resource = Resource.create(
        {
            SERVICE_NAME: os.environ.get("OTEL_SERVICE_NAME") or service_name,
            SERVICE_VERSION: service_version,
        }
    )
    provider = TracerProvider(resource=resource)
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


def setup_metrics(resource: Resource) -> MeterProvider:
    """Install the process-wide MeterProvider, once, before `instrument_app` and any SQL or HTTP instrumentation.

    Those read the global provider when they attach, and the HTTP server metrics
    come from them. Pass `setup_tracing(...).resource` so both signals carry one
    service identity. A separate call, because a process that must not record OTel
    metrics (the voice worker's job processes, which exit per call) simply never
    makes it. Same shape as `setup_tracing`: idempotent, exporting only when a
    collector is configured.
    """
    current = metrics.get_meter_provider()
    if isinstance(current, MeterProvider):
        return current
    exporting = bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))
    readers = [PeriodicExportingMetricReader(OTLPMetricExporter())] if exporting else []
    provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(provider)
    return provider


def instrument_app(app: FastAPI) -> None:
    """A server span and the HTTP server metrics per request, continuing an inbound `traceparent`.

    Call it last in the app factory, so the server span is the outermost layer and also
    wraps the other middleware (a rate limiter's 429 is a span like any other response).
    """
    _use_route_templates()
    FastAPIInstrumentor.instrument_app(app)


def route_template(app: FastAPI, path: str) -> str:
    """The route template a concrete path belongs to (`/bookings/{booking_id}`), or UNMATCHED_ROUTE.

    For anything that needs a bounded name for a request: a span name, a metric label,
    a rate-limit scope. A raw path carries ids, and there is one per record.
    """
    # Built from the OpenAPI document rather than from `app.routes`: the routing objects are
    # private to FastAPI and nested, while the document is the public, flattened list of the
    # same templates. Built on first use, because the routers are included after the app exists.
    matchers = getattr(app.state, "route_matchers", None)
    if matchers is None:
        # Fewest parameters first, so a literal segment (`/pricing/services`) wins over a
        # parameterised sibling.
        templates = sorted(app.openapi()["paths"], key=lambda template: template.count("{"))
        matchers = app.state.route_matchers = [
            (
                re.compile(
                    "".join(
                        "[^/]+" if part.startswith("{") else re.escape(part)
                        for part in re.split(r"(\{[^}]+\})", template)
                    )
                ),
                template,
            )
            for template in templates
        ]
    return next((template for pattern, template in matchers if pattern.fullmatch(path)), UNMATCHED_ROUTE)


def _route_details(scope: dict[str, Any]) -> str | None:
    template = route_template(scope["app"], scope["path"])
    return None if template == UNMATCHED_ROUTE else template


def _use_route_templates() -> None:
    """Name server spans and label request metrics by route template.

    NOTE: opentelemetry-instrumentation-fastapi 0.60b1 finds the route by walking `app.routes`, and
    FastAPI 0.139 nests every included router in an object with no `path`. Two symptoms follow: the
    span name and `http.route` (a metric label) become the raw path, one series per record id, and a
    405 or a CORS preflight raises AttributeError and answers 500. 0.60b1 is the newest release
    livekit-agents' opentelemetry-api pin allows. Drop this once an instrumentation release reads
    `scope["route"]`; each service's HTTP tracing test fails if it is dropped too early.
    """
    # setattr: the function is private to the library, which is the point of this workaround.
    setattr(otel_fastapi, "_get_route_details", _route_details)  # noqa: B010


def guest_name_fp(name: str) -> str:
    """A name's fingerprint: equal for equal names, useless without the key.

    Logs and metrics carry this instead of the name, so "did the guest give the
    same name at booking and at lookup" is answerable without plaintext. Keyed,
    because an unkeyed hash of a first name is reversed with a list of names.
    Computed identically in every service, so fingerprints join across them.
    Empty when OBS_FINGERPRINT_KEY is unset: no key, no fingerprint.
    """
    key = os.environ.get("OBS_FINGERPRINT_KEY", "")
    if not key:
        return ""
    normalized = unicodedata.normalize("NFKC", name.strip().casefold())
    return hmac.new(key.encode(), normalized.encode(), hashlib.sha256).hexdigest()[:12]
