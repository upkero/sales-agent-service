"""Slot coercion: the last place an implausible number can be stopped cheaply.

Everything here runs before a request leaves the process, which is the point —
a quantity the pricing service would reject costs a round trip and comes back as
an unmapped 4xx, so it is worth catching at the boundary instead.
"""

import pytest

from src.app.services.dialog.extraction import canonical_service, coerce_quantity, mentions_quantity


@pytest.mark.parametrize("value", [1, 3, 1000, "7", " 12 "])
def test_a_plausible_quantity_is_accepted(value: object) -> None:
    assert coerce_quantity(value) == int(str(value).strip())


@pytest.mark.parametrize("value", [0, -1, 1001, 5000, "5000", True, False, None, "many", 2.5])
def test_an_implausible_quantity_reads_as_an_unfilled_slot(value: object) -> None:
    # None, not an exception: the stage then simply asks again, exactly as it does
    # for any other answer it could not read.
    assert coerce_quantity(value) is None


def test_a_loose_name_snaps_onto_the_catalogue() -> None:
    assert canonical_service(("Deep Tissue Massage",), "deep tissue massage") == "Deep Tissue Massage"


def test_an_unknown_name_is_left_alone_for_present_to_recover() -> None:
    assert canonical_service(("Deep Tissue Massage",), "hot stone") == "hot stone"


def test_a_quantity_counts_only_when_the_customer_named_it() -> None:
    assert mentions_quantity("Neither, make it 10.", 10)
    assert mentions_quantity("actually make it eight", 8)
    assert mentions_quantity("Давайте восемь", 8)
    # As seen live: "No thanks" reported as quantity 1, the order re-priced to one session.
    assert not mentions_quantity("No thanks.", 1)
    assert not mentions_quantity("make it 100", 10)
