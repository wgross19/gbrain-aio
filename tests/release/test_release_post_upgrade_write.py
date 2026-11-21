"""The post-restart write gate, run last, on the surviving candidate container.

The rehearsal proved persistence readback through the restart but hit
`recovery_required` (writer identity) when writing a NEW page after the
restart. This test performs exactly that write so a candidate that cannot take
a fresh app write after restart fails here, isolated from the upgrade
readback, with the writer-status evidence in the message.
"""

from __future__ import annotations

import pytest

from tests.release.release_contract import AFTER_UPGRADE_SLUG, after_upgrade_page
from tests.release.release_ops import put_page, wait_page_registered

pytestmark = pytest.mark.integration


def test_candidate_takes_a_new_write_after_restart(release_run) -> None:
    candidate = release_run["containers"]["candidate"]
    restart = release_run["stages"]["restart"]
    assert (
        restart["https"] == "ok" and restart["version"]
    ), "candidate unhealthy at hand-off"  # nosec B101
    receipt = put_page(
        candidate,
        AFTER_UPGRADE_SLUG,
        after_upgrade_page(release_run["fixture"]["run_token"]),
        release_run["fixture"]["run_token"],
        (),
    )
    wait_page_registered(candidate, AFTER_UPGRADE_SLUG)
    assert receipt[
        "disk_has_token"
    ], f"write reached neither disk nor app read: {receipt}"  # nosec B101
    assert receipt[
        "readback_has_token"
    ], f"app read lost the new write: {receipt}"  # nosec B101
