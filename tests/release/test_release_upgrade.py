"""Distinct-upgrade class: the old -> candidate release gate.

Skips with an explicit classification message on a same-image qualification;
the smoke class already asserted everything that one image can prove.

A distinct upgrade never regresses the semantic version baseline. The upstream
version string is NOT required to move: a legitimate wrapper-only release may
keep the same upstream version while the image content changes - that case is
validated by the two distinct image ids and the digest/image relationship of
the pinned input refs instead.
"""
from __future__ import annotations

import pytest

from tests.release.release_contract import semantic_baseline_ok

pytestmark = pytest.mark.integration

_SAME_IMAGE_SKIP = (
    "same-image-smoke qualification: AIO_PREVIOUS_IMAGE resolves to the candidate image "
    "itself; a distinct previously released image is required to prove an upgrade"
)


def _require_distinct(release_run: dict) -> None:
    if release_run["classification"] != "distinct-upgrade":
        pytest.skip(_SAME_IMAGE_SKIP)


def test_upgrade_does_not_regress_the_semantic_baseline(release_run) -> None:
    _require_distinct(release_run)
    before = release_run["stages"]["before"]["version"]
    after = release_run["stages"]["after"]["version"]
    restart = release_run["stages"]["restart"]["version"]
    assert before and after, f"missing version strings: {before!r} -> {after!r}"
    assert semantic_baseline_ok(before, after), (
        f"semantic version regression: {before} -> {after}; a release may keep the same "
        "version string (wrapper-only release) but must never move backwards")
    assert restart == after


def test_digest_pinned_inputs_describe_the_images_under_test(release_run, release_inputs) -> None:
    _require_distinct(release_run)
    resolved = release_run["images"]
    for role, ref in (("previous", release_inputs.previous_image),
                      ("candidate", release_inputs.candidate_image)):
        info = resolved[role]
        assert info["ref"] == ref, role
        assert info["digest_pinned"] is True, role
        assert info["digest_relationship"] is True, (
            f"pinned ref {ref} does not describe the resolved {role} image: {info}")


def test_images_resolve_to_two_distinct_ids(release_run) -> None:
    _require_distinct(release_run)
    resolved = release_run["images"]
    assert resolved["previous"]["id"] != resolved["candidate"]["id"]
    assert resolved["previous"]["id"].startswith("sha256:")
    assert resolved["candidate"]["id"].startswith("sha256:")
    assert resolved["candidate"]["arch"] == release_run["platform_arch"]


def test_cluster_identity_survives_the_upgrade(release_run) -> None:
    _require_distinct(release_run)
    stages = release_run["stages"]
    assert stages["after"]["cluster_id"] == stages["before"]["cluster_id"]
    assert stages["restart"]["cluster_id"] == stages["before"]["cluster_id"]


def test_persisted_record_readback_old_to_candidate(release_run) -> None:
    """The release-critical readback: the records seeded and written through the
    previous image survive the candidate recreate with byte-identical content,
    still registered under their source, and still readable through the app -
    and again after the restart.

    Git-seeded pages must equal the fixture bytes; the app-written witness must
    equal the disk bytes measured at write time (gbrain put serializes
    frontmatter metadata on write, so its disk form is authoritative).
    """
    _require_distinct(release_run)
    stages = release_run["stages"]
    fixture = release_run["fixture"]
    git_slugs = fixture["git_slugs"]
    witness = fixture["put_witness_slug"]
    written_disk_sha = stages["baseline_write"]["disk_sha256"]
    assert written_disk_sha, "put-witness disk sha was not measured"
    for stage in ("after", "restart"):
        state = stages[stage]
        assert state["page_sha256"] == stages["before"]["page_sha256"], stage
        assert {s: state["page_sha256"][s] for s in git_slugs} == fixture["git_sha_by_slug"], stage
        assert state["page_sha256"][witness] == written_disk_sha, stage
        assert state["page_rows"] == stages["before"]["page_rows"], stage
        assert all(state["app_read_has_token"].values()), f"app read {stage}"
    assert stages["baseline_write"]["readback_has_token"]
    assert stages["baseline_write"]["disk_has_token"]
