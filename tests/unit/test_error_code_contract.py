"""The pricing gateway's error_code map, checked against ops-core-api's own list.

`tests/fixtures/error-codes.json` is a copy of `docs/error-codes.json` from
ops-core-api, which that service generates from its exception hierarchy. Keeping
the copy fresh is a manual step — but a stale one now fails here, at build time,
rather than in production as a sales agent telling a prospect "something went
wrong" because a code was renamed upstream and this map quietly stopped matching.
"""

import json
from pathlib import Path

from src.app.gateways.core_api_pricing import _ERROR_CODES

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "error-codes.json"


def test_every_mapped_code_still_exists_upstream() -> None:
    published = json.loads(_FIXTURE.read_text(encoding="utf-8"))

    unknown = sorted(_ERROR_CODES.keys() - published.keys())

    assert not unknown, (
        f"{unknown} is not in ops-core-api's error-codes.json. Either the code was "
        f"renamed upstream, or this copy of the fixture is out of date — refresh it "
        f"from ops-core-api/docs/error-codes.json."
    )
