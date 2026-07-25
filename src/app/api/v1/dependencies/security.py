"""Inbound API-key auth for the turn endpoint.

Off by default (no key configured) so `docker compose up` and the curl examples
work with no ceremony. Switch it on by setting INBOUND_API_KEY, and every turn
must then carry a matching X-API-Key header. This is the same shape ops-core-api
uses on its own endpoints.
"""

import secrets
from typing import Annotated

from fastapi import Security
from fastapi.security import APIKeyHeader

from src.app.core.settings.app import get_app_settings
from src.app.exceptions.auth import UnauthorizedError

_API_KEY_HEADER = "X-API-Key"

# auto_error=False: we decide what a missing key means (open vs 401), and the
# scheme still advertises the auth in the OpenAPI docs.
api_key_scheme = APIKeyHeader(name=_API_KEY_HEADER, auto_error=False)


async def require_api_key(provided: Annotated[str | None, Security(api_key_scheme)]) -> None:
    expected = get_app_settings().inbound_api_key
    if expected is None:
        return  # auth disabled: the endpoint is open (local/demo mode)

    # compare_digest keeps the check constant-time, so a caller cannot learn the
    # key one character at a time by measuring how long the comparison takes.
    if not provided or not secrets.compare_digest(provided, expected.get_secret_value()):
        raise UnauthorizedError()
