from src.app.exceptions.base import BaseAppException


class UnauthorizedError(BaseAppException):
    """The caller did not present a valid API key on a protected endpoint.

    Raised only when inbound auth is switched on (a key is configured). Kept
    deliberately vague — "not authorised" rather than "wrong key" vs "no key" —
    so it hands an attacker nothing to distinguish the two.

    The code is ops-core-api's `invalid_api_key`, not a synonym of our own: a
    client talking to several services in this portfolio should be able to
    branch on one vocabulary rather than a table of near-identical strings.
    """

    status_code = 401
    error_code = "invalid_api_key"
    default_detail = "A valid API key is required."
