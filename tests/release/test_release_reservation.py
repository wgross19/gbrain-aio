"""A prepared upgrade must remain writable; an unprepared legacy state is a separate control."""
import pytest

from tests.release.release_ops import run_choreography, teardown_survivor

pytestmark = pytest.mark.integration


def test_saved_reservation_upgrade_writes_after_replace_and_restart(release_run):
    evidence = release_run.get('reservation')
    assert evidence, 'the release receipt must identify the tested reservation state'
    assert evidence == {'baseline': 'matching-backup', 'after': 'write-verified', 'restart': 'write-verified'}


def test_missing_reservation_fails_closed_not_a_qualified_upgrade(release_inputs, release_receipts_dir):
    result = run_choreography(release_inputs, release_receipts_dir, saved_reservation=False)
    try:
        assert result['verdict'] == 'CONTROL: WRITE BLOCKED'
        assert result['reservation'] == {'baseline': 'no-backup', 'after': 'recovery_required',
                                         'restart': 'recovery_required'}
    finally:
        teardown_survivor(result)
