"""Strategy: the interchangeable rule for what to upsell.

The UPSELL stage knows *that* it should offer a bigger commitment; it does not
decide *which* one. That decision — here, "move the prospect to the next volume
tier ops-core-api rewards" — is a Strategy chosen once in the container. Swapping
in a different tactic (a premium-service cross-sell, say) is one line there, and
the stage never learns anything changed.
"""

from abc import ABC, abstractmethod


class SalesTactic(ABC):
    @abstractmethod
    def upsell_target(self, current_quantity: int) -> int | None:
        """The quantity to pitch as the upsell, or None if there is nothing to
        gain by buying more."""


class VolumeDiscountTactic(SalesTactic):
    """Nudge the prospect to the next quantity that unlocks a bigger discount.

    The tiers mirror ops-core-api's own discount policy (10% from 6 units, 15%
    from 20). Kept in step by convention, not import, for the same reason the
    pricing DTOs are duplicated: the two services version independently.
    """

    _TIERS: tuple[int, ...] = (6, 20)

    def upsell_target(self, current_quantity: int) -> int | None:
        for tier in self._TIERS:
            if current_quantity < tier:
                return tier
        # Already at or above the top tier — no volume upsell left to make.
        return None
