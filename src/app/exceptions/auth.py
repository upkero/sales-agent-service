from src.app.exceptions.base import BaseAppException


class UnauthorizedError(BaseAppException):
    """The caller did not present a valid API key on a protected endpoint.

    Raised whenever the key is missing or wrong: it is required (SECURITY_API_KEY),
    so there is no mode in which it is skipped. Kept deliberately vague — "not authorised"
    rather than "wrong key" vs "no key" — so it hands an attacker nothing to distinguish the two.

    The code is ops-core-api's `invalid_api_key`, not a synonym of our own: a
    client talking to several services in this portfolio should be able to
    branch on one vocabulary rather than a table of near-identical strings.
    """

    status_code = 401
    error_code = "invalid_api_key"
    default_detail = "A valid API key is required."
