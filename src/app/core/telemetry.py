"""Tracing setup, identical in every service (CANON C19).

Configuration is the standard OTel environment, not a settings class, so this
file can be copied verbatim: OTEL_EXPORTER_OTLP_ENDPOINT turns export on,
OTEL_SERVICE_NAME overrides the name given in code, OTEL_RESOURCE_ATTRIBUTES
carries deployment.environment, OTEL_TRACES_SAMPLER the sampler, and
OTEL_PYTHON_FASTAPI_EXCLUDED_URLS the paths the server spans skip.
"""

import hashlib
import hmac
import os
import unicodedata
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def setup_tracing(service_name: str, distribution: str) -> TracerProvider:
    """Install the process-wide TracerProvider, once.

    Idempotent because the global provider can be set only once per process,
    and the app factory runs more than once under tests. With no collector
    configured, spans are still created (logs carry their ids) but go nowhere,
    rather than an exporter retrying localhost:4317 in the background forever.
    """
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


def instrument_app(app: FastAPI) -> None:
    """A server span per request, continuing an inbound `traceparent`."""
    FastAPIInstrumentor.instrument_app(app)


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
