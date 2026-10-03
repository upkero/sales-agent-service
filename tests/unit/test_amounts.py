from decimal import Decimal

from src.app.services.dialog.amounts import unquoted_amounts
from tests.fakes import compute_quote

_EIGHT = compute_quote("Deep Tissue Massage", Decimal("120.00"), 8)  # 960.00 - 96.00 = 864.00


def test_every_number_of_the_quote_is_allowed() -> None:
    reply = "8 sessions at 120.00 each: 960.00, less 10% (96.00), so 864.00 in total."
    assert unquoted_amounts(reply, [_EIGHT]) == []


def test_an_amount_outside_the_quote_is_caught_in_any_spelling() -> None:
    for spelling in ("1040.00", "1,040.00", "1 040,00", "1040", "999,5"):
        assert unquoted_amounts(f"That is {spelling} in total.", [_EIGHT]), spelling


def test_a_discounted_subtotal_without_its_total_is_caught() -> None:
    # The live failure: the pre-discount figure stated as the price.
    assert unquoted_amounts("Eight sessions come to 960.00.", [_EIGHT]) == [Decimal("960.00")]


def test_small_plain_numbers_are_not_amounts() -> None:
    # Session counts, durations and percentages. The limit of this rule is noted in the module.
    assert unquoted_amounts("Book 6 sessions of 60 minutes and save 10%.", [_EIGHT]) == []


def test_with_no_quote_any_amount_is_unquoted() -> None:
    assert unquoted_amounts("Massages start at 120.00.", []) == [Decimal("120.00")]
