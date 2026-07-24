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


class ServiceNotFoundError(PricingError):
    """The named service is not in the catalogue.

    Reachable in normal operation: a prospect will ask for something by a name
    that is not exactly how it is listed. The agent recovers by offering the
    real catalogue rather than failing the turn.
    """

    status_code = 404
    error_code = "entity_not_found"
    default_detail = "That service is not in the price list."
