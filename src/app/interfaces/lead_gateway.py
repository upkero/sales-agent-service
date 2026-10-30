from abc import ABC, abstractmethod

from src.app.contracts.lead import Lead


class LeadGateway(ABC):
    """The port through which a closed conversation becomes something a person can act on."""

    @abstractmethod
    async def save_lead(self, lead: Lead) -> None:
        """Record the lead for the front desk.

        Raises LeadNotSavedError if it could not be recorded. Nothing else escapes.
        """
