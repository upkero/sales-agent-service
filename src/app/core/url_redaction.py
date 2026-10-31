import httpx
from opentelemetry.trace import Span

# The two names the instrumentation uses for the request URL, by semconv generation. Only
# the stable one is reported here (setup_tracing opts in to `http`); the old one is covered
# so that an explicit OTEL_SEMCONV_STABILITY_OPT_IN cannot bring the query back.
_URL_ATTRIBUTES = ("url.full", "http.url")


async def strip_query_from_span(span: Span, request: object) -> None:
    """Keep the query string out of a client span's URL.

    The instrumentation records the whole URL, and a search puts what the visitor typed in it:
    ``/customers?search=Anna%20Petrova``. Logs and spans are retained for days and read by people
    who are not the visitor, so the span keeps the path only; the route says which call it was.
    An async request hook, because the instrumentation only awaits a coroutine function for an
    ``httpx.AsyncClient``.
    """
    url = getattr(request, "url", None)
    if not isinstance(url, httpx.URL):
        return
    recorded = getattr(span, "attributes", None) or {}
    redacted = str(url.copy_with(query=None, fragment=None))
    for attribute in _URL_ATTRIBUTES:
        if attribute in recorded:
            span.set_attribute(attribute, redacted)
