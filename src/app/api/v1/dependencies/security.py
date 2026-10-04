"""Inbound API-key auth for the turn endpoint.

Required, like on every service of the portfolio: the key is SECURITY_API_KEY and
every turn must carry it in the X-API-Key header. An optional key once meant a
renamed or forgotten variable left a paid endpoint silently open; a required one
fails the boot instead.
"""

import secrets
from typing import Annotated

from fastapi import Depends, Security
from fastapi.security import APIKeyHeader

from src.app.core.settings.security import SecuritySettings, get_security_settings
from src.app.exceptions.auth import UnauthorizedError

_API_KEY_HEADER = "X-API-Key"

# auto_error=False: a missing key is reported through this project's error
# envelope, and the scheme still advertises the auth in the OpenAPI docs.
api_key_scheme = APIKeyHeader(name=_API_KEY_HEADER, auto_error=False)


async def require_api_key(
    provided: Annotated[str | None, Security(api_key_scheme)],
    settings: Annotated[SecuritySettings, Depends(get_security_settings)],
) -> None:
    # compare_digest keeps the check constant-time, so a caller cannot learn the
    # key one character at a time by measuring how long the comparison takes.
    # Bytes, because compare_digest rejects non-ASCII strings.
    expected = settings.api_key.get_secret_value()
    if not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
        raise UnauthorizedError()
