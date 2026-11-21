"""Same-image smoke class.

These assertions are image-agnostic: they pass identically on a same-image
release qualification (previous == candidate) and on a distinct release. They
prove boot health, no-network/paid-model surface, unique-volume hygiene, and
persistence readback across a recreate and a restart on the candidate image.
"""
from __future__ import annotations

import pytest

from tests.release.release_contract import classify_release

pytestmark = pytest.mark.integration


def test_daemon_identity_and_classification_are_declared(release_run, release_inputs) -> None:
    resolved = release_run["images"]
    expected = classify_release(release_inputs.previous_image, resolved["previous"]["id"],
                                release_inputs.candidate_image, resolved["candidate"]["id"])
    assert release_run["classification"] == expected
    assert release_run["verdict"] == "PASS"
    assert release_run["dind"]["socket_mode"] == release_inputs.socket_mode
    if not release_inputs.hosted:
        assert release_run["dind"]["daemon"].startswith("Docker-in-Docker-DinD Alpine")
    assert release_run["dind"]["server_arch"] == release_run["platform_arch"]


def test_candidate_boots_to_https_health_with_network_none(release_run) -> None:
    for stage in ("before", "after", "restart"):
        state = release_run["stages"][stage]
        assert state["https"] == "ok", stage
        assert state["network_mode"] == "none", stage
        assert state["cluster_id"], stage


def test_persistence_readback_after_recreate_and_restart(release_run) -> None:
    """The seeded git pages and the CLI-written witness must read back with the
    run token through the app on the previous image, after the recreate on the
    candidate, and again after the restart.

    Byte identity: git-seeded pages stay byte-identical to the fixture. The
    app-written witness stays byte-identical to what the app actually wrote
    (put_page measures the on-disk sha right after the write; gbrain put
    rewrites frontmatter with ingestion metadata, so the in-put text hash is
    not the disk hash) - stable across the upgrade recreate and the restart.
    """
    stages = release_run["stages"]
    fixture = release_run["fixture"]
    git_slugs = fixture["git_slugs"]
    witness = fixture["put_witness_slug"]
    written_disk_sha = stages["baseline_write"]["disk_sha256"]
    assert written_disk_sha, "put-witness disk sha was not measured"
    for stage in ("before", "after", "restart"):
        state = stages[stage]
        assert all(state["app_read_has_token"].values()), f"app read lost note bodies at {stage}"
        assert all(rows == 1 for rows in state["page_rows"].values()), f"page rows at {stage}"
        assert {s: state["page_sha256"][s] for s in git_slugs} == fixture["git_sha_by_slug"], \
            f"git-seeded page bytes changed at {stage}"
        assert state["page_sha256"][witness] == written_disk_sha, \
            f"app-written page bytes are not stable at {stage}"


def test_paid_model_surface_is_disabled(release_run) -> None:
    """No provider credential in the environment, no paid endpoint in config, and
    a private/-absent Ollama base for every container under test."""
    for role in ("previous", "candidate"):
        surface = release_run["contract"][role]
        assert surface["provider_env_keys_present"] == [], role
        assert surface["config_flagged_keys"] == [], role
        ollama = surface["ollama_base_url"]
        assert ollama in ("absent",) or ollama.startswith("private"), role


def test_only_uniquely_named_volumes_were_touched(release_run) -> None:
    prefix = release_run["resource_prefix"]
    volumes = release_run["volumes"]
    assert sorted(volumes.values()) == [f"{prefix}-brain", f"{prefix}-data"]
    assert release_run["containers"]["previous"].startswith(prefix + "-")
    assert release_run["containers"]["candidate"].startswith(prefix + "-")
