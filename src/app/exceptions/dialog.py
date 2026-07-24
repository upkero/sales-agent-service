"""Typed failures from the dialogue state machine."""

from src.app.exceptions.base import BaseAppException


class InvalidStageTransitionError(BaseAppException):
    """A stage tried to move to a target outside its declared successors.

    This is a code bug, not a user error: the legal transitions are declared once
    in contracts/sales.py and every stage's route() is expected to return a member
    of its own row. Raised (not asserted) so it survives `python -O`, and typed so
    the API's exception handler renders the uniform JSON envelope with a structured
    log rather than leaking a stacktrace to the caller.
    """

    status_code = 500
    error_code = "invalid_stage_transition"
    default_detail = "The conversation reached an inconsistent state."
