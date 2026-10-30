"""A lead that did not reach ops-core-api.

Never the customer's problem: the conversation closes either way, and the stage that
saves the lead catches this, logs it and moves on.
"""

from src.app.exceptions.base import BaseAppException


class LeadNotSavedError(BaseAppException):
    """ops-core-api could not be reached, kept failing, or refused the customer record."""

    status_code = 503
    error_code = "lead_not_saved"
    default_detail = "The lead could not be saved."
