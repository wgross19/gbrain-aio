"""Session wiring for the release qualification suite.

The suite runs ONCE per pytest session: strict inputs first (no Docker call
may happen before they validate), then the old->candidate choreography on the
verified DinD daemon, whose receipts every test in this directory asserts on.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.release.release_contract import ReleaseContractError, validate_release_inputs
from tests.release.release_ops import run_choreography, teardown_survivor


@pytest.fixture(scope="session")
def release_receipts_dir() -> Path:
    """Receipts land outside the repo: AIO_RELEASE_RECEIPTS_DIR or the system temp dir."""
    base = os.environ.get("AIO_RELEASE_RECEIPTS_DIR")
    path = Path(base) if base else Path(tempfile.gettempdir()) / "aio-release-receipts"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def release_inputs():
    try:
        return validate_release_inputs(os.environ)
    except ReleaseContractError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture(scope="session")
def release_run(release_inputs, release_receipts_dir) -> Iterator[dict]:
    """Run the full old->candidate choreography once and hand out the receipts.

    Teardown (inside run_choreography) removes only the containers and volumes
    the run created. Teardown happens after ALL tests in this directory read
    the receipts, so `test_release_post_upgrade_write.py` can still exercise
    the candidate container that survived the restart.
    """
    receipts = run_choreography(release_inputs, release_receipts_dir)
    yield receipts
    teardown_survivor(receipts)
