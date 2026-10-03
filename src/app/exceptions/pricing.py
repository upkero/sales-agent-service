"""Typed failures from the pricing domain.

ops-core-api answers every failure with a uniform {detail, error_code} envelope,
and the adapter switches on `error_code` rather than the status code so the agent
can react differently to "there is no such service" (ask the prospect to pick one
that exists) than to "the price list is momentarily unreachable" (apologise and
retry). Collapsing them into a status number would produce one useless apology.
"""

from src.app.exceptions.base import BaseAppException


class PricingError(BaseAppException):
    """Base for anything that goes wrong reaching or using the pricing API."""

    error_code = "pricing_error"
    default_detail = "Pricing lookup failed."


class PricingUnavailableError(PricingError):
    """The pricing API could not be reached, or kept failing.

    Not the prospect's fault and nothing they can do, so the agent says the price
    list is momentarily unavailable rather than reciting a status code.
    """

    status_code = 503
    error_code = "pricing_unavailable"
    default_detail = "The pricing service is unavailable."


class PricingRateLimitedError(PricingError):
    """ops-core-api is throttling us, and still was after every retry.

    Deliberately not a 503: "busy, come back in n seconds" and "broken" are
    different answers, and only one of them tells the caller what to do next.
    The upstream's own Retry-After is passed straight through, because it knows
    when it will be free and we are only guessing.
    """

    status_code = 429
    error_code = "rate_limit_exceeded"
    default_detail = "The pricing service is busy. Please retry shortly."


class PricingRejectedError(PricingError):
    """ops-core-api refused the request with a 4xx this service has no mapping for.

    That means the two services disagree (a wrong OPS_CORE_API_KEY, a changed
    contract), so it is a bad gateway, not our caller's fault. The detail is fixed:
    ops-core's own ("Missing or invalid API key.") would read to the caller as a
    complaint about *their* key.
    """

    status_code = 502
    error_code = "pricing_rejected"
    default_detail = "The pricing service rejected the request."


class ServiceNotFoundError(PricingError):
    """The named service is not in the catalogue.

    Reachable in normal operation: a prospect will ask for something by a name
    that is not exactly how it is listed. The agent recovers by offering the
    real catalogue rather than failing the turn.
    """

    status_code = 404
    error_code = "entity_not_found"
    default_detail = "That service is not in the price list."
