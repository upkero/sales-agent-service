from src.app.exceptions.base import BaseAppException


class UnauthorizedError(BaseAppException):
    """The caller did not present a valid API key on a protected endpoint.

    Raised only when inbound auth is switched on (a key is configured). Kept
    deliberately vague — "not authorised" rather than "wrong key" vs "no key" —
    so it hands an attacker nothing to distinguish the two.
    """

    status_code = 401
    error_code = "unauthorized"
    default_detail = "A valid API key is required."
