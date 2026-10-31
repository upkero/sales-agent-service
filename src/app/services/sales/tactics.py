"""What the UPSELL stage pitches: the next volume tier, if there is one."""

# Mirrors ops-core-api's discount tiers (10% from 6 units, 15% from 20). Kept in step by
# convention, not import, for the same reason the pricing DTOs are duplicated: the two
# services version independently.
_TIERS = (6, 20)


def next_volume_tier(quantity: int) -> int | None:
    """The smallest tier above `quantity`, or None when it is already at the top."""
    return next((tier for tier in _TIERS if quantity < tier), None)
