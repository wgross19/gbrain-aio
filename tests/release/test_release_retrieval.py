"""Behavioral retrieval eval: real lexical search by page content.

`gbrain search` is gbrain's cheap keyword path (no LLM expansion), so with
paid models disabled and --network none the eval is deterministic. The known
fixture guarantees every query term lives only in page bodies - slugs and
titles share no vocabulary with the queries - so a hit proves content
retrieval, not slug lookup. Searches run on the previous image, on the
candidate after the upgrade, and after the restart; the receipts carry them.
"""

from __future__ import annotations

import pytest

from tests.release.release_contract import build_fixture

pytestmark = pytest.mark.integration


def _phase_searches(release_run: dict, phase: str) -> dict:
    searches = release_run["searches"][phase]
    assert searches, f"no search receipts recorded for {phase}"  # nosec B101
    return searches


def _assert_hit(searches: dict, term: str, slug: str) -> None:
    receipt = searches[term]
    assert receipt["exit"] == 0, f"search failed for {term!r}: {receipt}"  # nosec B101
    assert (
        slug in receipt["stdout"]
    ), f"lexical search for body term {term!r} missed {slug}: {receipt}"  # nosec B101


def _assert_miss(searches: dict, term: str, slug: str) -> None:
    receipt = searches[term]
    assert (
        slug not in receipt["stdout"]
    ), f"{term!r} wrongly surfaced {slug}: {receipt['stdout'][:600]}"  # nosec B101


def test_content_terms_find_their_pages_on_the_candidate(release_run) -> None:
    fixture = build_fixture(release_run["fixture"]["run_token"])
    for phase in ("after", "restart"):
        searches = _phase_searches(release_run, phase)
        for page in fixture["pages"]:
            _assert_hit(searches, page["term"], page["slug"])


def test_retrieval_is_body_content_not_slug_lookup(release_run) -> None:
    """The discriminating half: query terms share no vocabulary with slugs or
    titles, so a hit can only come from body-content indexing - and a term
    must not surface the OTHER pages."""
    receipt_fixture = release_run["fixture"]
    fixture = build_fixture(receipt_fixture["run_token"])
    searches = _phase_searches(release_run, "after")
    for page in fixture["pages"]:
        assert (
            page["term"] not in page["slug"] and page["term"] not in page["title"]
        )  # nosec B101
        for other in fixture["pages"]:
            if other is not page:
                _assert_miss(searches, page["term"], other["slug"])


def test_unknown_term_returns_no_fixture_pages(release_run) -> None:
    absent = release_run["fixture"]["absent_term"]
    for phase in ("before", "after", "restart"):
        searches = _phase_searches(release_run, phase)
        receipt = searches[absent]
        assert (
            receipt["exit"] == 0
        ), f"{absent} search failed at {phase}: {receipt}"  # nosec B101
        for slug in release_run["fixture"]["sha_by_slug"]:
            assert (
                slug not in receipt["stdout"]
            ), f"{absent} surfaced {slug} at {phase}"  # nosec B101


def test_retrieval_parity_before_vs_after_upgrade(release_run) -> None:
    """Every recorded search phase must agree on which fixture pages exist:
    what the previous image retrieved must still be retrievable on the
    candidate and after the restart."""
    fixture = build_fixture(release_run["fixture"]["run_token"])
    before = _phase_searches(release_run, "before")
    for phase in ("after", "restart"):
        searches = _phase_searches(release_run, phase)
        for term, expected_slug in (
            (entry[0], entry[1][0]) for entry in fixture["expected_hits"].items()
        ):
            _assert_hit(before, term, expected_slug)
            _assert_hit(searches, term, expected_slug)
