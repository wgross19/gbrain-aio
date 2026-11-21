"""Positive and negative proof of the strict release-input contract.

Pure logic only: these tests never touch Docker, so they run on any host and
document exactly why candidate CI fails fast when the immutable digest-pinned
inputs, the hosted-mode marker, the socket policy, or the verified DinD
endpoint contract are absent/wrong.
"""

from __future__ import annotations

import hashlib

import pytest

from tests.release.release_contract import (
    ABSENT_TERM,
    ARCH_VAR,
    CANDIDATE_VAR,
    DEFAULT_HOST_SOCKET,
    DIND_ENDPOINT,
    GITHUB_ACTIONS_VAR,
    PREVIOUS_VAR,
    RUNNER_ENVIRONMENT_VAR,
    ReleaseContractError,
    build_fixture,
    classify_release,
    digest_relationship,
    ref_digest,
    semantic_baseline_ok,
    validate_release_inputs,
)

CANDIDATE_DIGEST = "sha256:" + "1e" * 32
PREVIOUS_DIGEST = "sha256:" + "2f" * 32

VALID = {
    CANDIDATE_VAR: "dub19/gbrain-aio@" + CANDIDATE_DIGEST,
    PREVIOUS_VAR: "dub19/gbrain-aio@" + PREVIOUS_DIGEST,
    ARCH_VAR: "amd64",
    "DOCKER_HOST": DIND_ENDPOINT,
    "DOCKER_TLS_VERIFY": "1",
    "DOCKER_CERT_PATH": "/certs/dind",
}

# The explicitly verified hosted GitHub case (no DinD wire-up -> default socket).
VALID_HOSTED = {
    CANDIDATE_VAR: VALID[CANDIDATE_VAR],
    PREVIOUS_VAR: VALID[PREVIOUS_VAR],
    ARCH_VAR: "amd64",
    GITHUB_ACTIONS_VAR: "true",
    RUNNER_ENVIRONMENT_VAR: "github-hosted",
}


def _without(key: str) -> dict:
    env = dict(VALID)
    env.pop(key)
    return env


def _with(key: str, value: str) -> dict:
    env = dict(VALID)
    env[key] = value
    return env


@pytest.mark.parametrize("key", [CANDIDATE_VAR, PREVIOUS_VAR, ARCH_VAR])
def test_missing_input_names_the_variable_and_the_wiring(key: str) -> None:
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(_without(key))
    message = str(raised.value)
    assert key in message  # nosec B101
    assert "python -m pytest -q tests/release" in message  # nosec B101
    assert DIND_ENDPOINT in message  # nosec B101


@pytest.mark.parametrize("bad", ["a b", "x\ny"])
def test_unusable_candidate_refs_are_rejected(bad: str) -> None:
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(_with(CANDIDATE_VAR, bad))
    assert "whitespace-free" in str(raised.value)  # nosec B101


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_refs_count_as_missing(blank: str) -> None:
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(_with(CANDIDATE_VAR, blank))
    assert "missing required input env vars" in str(raised.value)  # nosec B101


@pytest.mark.parametrize("arch", ["arm", "AMD64", "x86_64"])
def test_unknown_architectures_are_rejected(arch: str) -> None:
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(_with(ARCH_VAR, arch))
    assert ARCH_VAR in str(raised.value) and "'amd64'" in str(
        raised.value
    )  # nosec B101


def test_valid_inputs_round_trip() -> None:
    inputs = validate_release_inputs(VALID)
    assert inputs.candidate_image == VALID[CANDIDATE_VAR]  # nosec B101
    assert inputs.previous_image == VALID[PREVIOUS_VAR]  # nosec B101
    assert inputs.arch == "amd64"  # nosec B101
    assert inputs.hosted is False  # nosec B101
    assert inputs.socket_mode == "dind-tls"  # nosec B101


# --- Digest pinning (mutable tags are not immutable release references) ------


@pytest.mark.parametrize(
    "bad",
    [
        "repo/img:latest",
        "repo/img:v1.2.3",
        "dub19/gbrain-aio:rc-45b451fd",
        "repo/img",
        "repo/img@sha256:abc",
        "repo/img@" + "sha256:" + "1e" * 31 + "f",  # 63 hex chars
        "repo/img@md5:" + "1e" * 32,
        "repo/img@sha256:" + "1E" * 32,  # uppercase hex is not a valid docker digest
    ],
)
def test_mutable_or_malformed_refs_are_rejected(bad: str) -> None:
    assert ref_digest(bad) is None  # nosec B101
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(_with(CANDIDATE_VAR, bad))
    message = str(raised.value)
    assert "digest-pinned" in message or "name@sha256" in message  # nosec B101


@pytest.mark.parametrize(
    "ref,digest",
    [
        ("dub19/gbrain-aio@sha256:" + "1e" * 32, "sha256:" + "1e" * 32),
        ("local/gbrain-aio@sha256:" + "97" * 32, "sha256:" + "97" * 32),
    ],
)
def test_digest_pinned_refs_parse(ref: str, digest: str) -> None:
    assert ref_digest(ref) == digest  # nosec B101
    inputs = validate_release_inputs(_with(CANDIDATE_VAR, ref))
    assert inputs.candidate_image == ref  # nosec B101


def test_digest_pinning_error_names_the_offending_vars() -> None:
    env = dict(VALID_HOSTED)
    env[PREVIOUS_VAR] = "repo/img:2.0"
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(env)
    assert PREVIOUS_VAR in str(raised.value) and "mutable" in str(
        raised.value
    )  # nosec B101


# --- Explicit hosted GitHub mode + socket policy ------------------------------


def test_verified_hosted_case_allows_the_default_socket() -> None:
    inputs = validate_release_inputs(VALID_HOSTED)
    assert inputs.hosted is True  # nosec B101
    assert inputs.socket_mode == "host-default"  # nosec B101
    assert inputs.candidate_image == VALID_HOSTED[CANDIDATE_VAR]  # nosec B101


def test_hosted_case_takes_no_tls_wiring_on_the_host_socket() -> None:
    env = dict(VALID_HOSTED)
    env["DOCKER_HOST"] = DEFAULT_HOST_SOCKET
    env["DOCKER_TLS_VERIFY"] = "1"
    env["DOCKER_CERT_PATH"] = "/certs/dind"
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(env)
    assert "TLS wiring" in str(raised.value)  # nosec B101


def test_hosted_case_with_the_dind_trio_is_valid() -> None:
    env = dict(VALID_HOSTED)
    env["DOCKER_HOST"] = VALID["DOCKER_HOST"]
    env["DOCKER_TLS_VERIFY"] = "1"
    env["DOCKER_CERT_PATH"] = "/certs/dind"
    inputs = validate_release_inputs(env)
    assert inputs.hosted is True and inputs.socket_mode == "dind-tls"  # nosec B101


@pytest.mark.parametrize(
    "mutations",
    [
        {},  # no DOCKER_HOST, no hosted markers -> host socket outside hosted case
        {GITHUB_ACTIONS_VAR: "false"},
        {GITHUB_ACTIONS_VAR: "1"},
        {GITHUB_ACTIONS_VAR: "true"},  # RUNNER_ENVIRONMENT missing
        {GITHUB_ACTIONS_VAR: "true", RUNNER_ENVIRONMENT_VAR: "self-hosted"},
        {RUNNER_ENVIRONMENT_VAR: "github-hosted"},  # GITHUB_ACTIONS missing
        {
            GITHUB_ACTIONS_VAR: "true",
            RUNNER_ENVIRONMENT_VAR: "github-hosted",
            "DOCKER_HOST": "unix:///tmp/other.sock",
        },
        {
            GITHUB_ACTIONS_VAR: "true",
            RUNNER_ENVIRONMENT_VAR: "github-hosted",
            "DOCKER_HOST": "tcp://localhost:2375",
        },
    ],
)
def test_default_host_socket_requires_the_verified_hosted_case(mutations: dict) -> None:
    env = {
        k: v
        for k, v in VALID.items()
        if k not in ("DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
    }
    env.update(mutations)
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(env)
    message = str(raised.value)
    assert "host" in message  # nosec B101
    assert (
        GITHUB_ACTIONS_VAR in message and RUNNER_ENVIRONMENT_VAR in message
    )  # nosec B101


def test_non_dind_endpoints_are_rejected_even_when_hosted() -> None:
    env = dict(VALID_HOSTED)
    env["DOCKER_HOST"] = "tcp://docker.example.internal:2376"
    env["DOCKER_TLS_VERIFY"] = "1"
    env["DOCKER_CERT_PATH"] = "/certs/dind"
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(env)
    assert "tcp://docker.example.internal:2376" in str(
        raised.value
    ) and DIND_ENDPOINT in str(
        raised.value
    )  # nosec B101


@pytest.mark.parametrize(
    "key,value",
    [
        ("DOCKER_HOST", "tcp://localhost:2376"),
        ("DOCKER_TLS_VERIFY", "0"),
        ("DOCKER_CERT_PATH", "/certs/other"),
    ],
)
def test_the_dind_endpoint_trio_is_strict(key: str, value: str) -> None:
    with pytest.raises(ReleaseContractError) as raised:
        validate_release_inputs(_with(key, value))
    assert key in str(raised.value) and DIND_ENDPOINT in str(raised.value)  # nosec B101


# --- Classification -----------------------------------------------------------


@pytest.mark.parametrize(
    "previous,candidate,expected",
    [
        (
            "repo/prev@sha256:" + "a1" * 32,
            "repo/cand@sha256:" + "b2" * 32,
            "distinct-upgrade",
        ),
        (
            "dub19/gbrain-aio@sha256:" + "1e" * 32,
            "dub19/gbrain-aio@sha256:" + "1e" * 32,
            "same-image-smoke",
        ),
    ],
)
def test_classification_by_refs(previous: str, candidate: str, expected: str) -> None:
    assert (
        classify_release(
            previous, "sha256:a" + "0" * 62, candidate, "sha256:b" + "0" * 62
        )
        == expected
    )  # nosec B101


def test_classification_by_resolved_image_id() -> None:
    same_id = "sha256:" + "9f" * 32
    assert (
        classify_release(
            "repo/prev@sha256:" + "a" * 63 + "0",
            same_id,
            "repo/cand@sha256:" + "0" * 63 + "b",
            same_id,
        )
        == "same-image-smoke"
    )  # nosec B101
    assert (
        classify_release(
            "repo/prev@sha256:" + "a" * 63 + "0",
            same_id,
            "repo/cand@sha256:" + "0" * 63 + "b",
            "sha256:" + "7c" * 32,
        )
        == "distinct-upgrade"
    )  # nosec B101


def test_unresolved_ids_never_count_as_same_image() -> None:
    assert (
        classify_release(
            "repo/prev@sha256:" + "a" * 63 + "0",
            "",
            "repo/cand@sha256:" + "0" * 63 + "b",
            "",
        )
        == "distinct-upgrade"
    )  # nosec B101


# --- Semantic baseline (wrapper-only releases keep the version string) --------


@pytest.mark.parametrize(
    "previous,candidate,ok",
    [
        ("0.60.607.0", "0.60.747.0", True),  # upstream moved
        (
            "0.60.747.0",
            "0.60.747.0",
            True,
        ),  # wrapper-only release: same upstream string is legitimate
        ("0.60.7", "0.60.7.0.1", True),  # zero-padded comparison
        ("0.61.1.0", "0.60.9.9", False),  # regression
        ("1.0", "0.99.9", False),
        ("", "0.1", False),  # missing/unparseable versions fail the gate
        ("latest", "1.0", False),
        ("1.0", "", False),
    ],
)
def test_semantic_baseline_never_regresses(
    previous: str, candidate: str, ok: bool
) -> None:
    assert semantic_baseline_ok(previous, candidate) is ok  # nosec B101


# --- Digest / image relationship ------------------------------------------------


def test_digest_relationship_via_image_id_and_repo_digests() -> None:
    digest = "sha256:" + "97" * 32
    other_ref_digest = "sha256:" + "aa" * 32
    # digest not in the repo digests and not equal to the image id -> False
    assert (
        digest_relationship(
            "local/gbrain-aio@" + digest,
            "sha256:" + "cc" * 32,
            "[local/other@" + other_ref_digest + "]",
        )
        is False
    )  # nosec B101
    assert (
        digest_relationship("local/gbrain-aio@" + digest, other_ref_digest, "") is False
    )  # nosec B101
    # digest equals the resolved image id
    assert (
        digest_relationship("local/gbrain-aio@" + digest, digest, "") is True
    )  # nosec B101
    # digest appears among the image's RepoDigests (docker --format string form)
    assert (
        digest_relationship(
            "local/gbrain-aio@" + digest,
            "sha256:" + "cc" * 32,
            "[local/gbrain-aio@" + digest + "]",
        )
        is True
    )  # nosec B101
    # ... and as a plain list
    assert (
        digest_relationship(
            "local/gbrain-aio@" + digest,
            "sha256:" + "cc" * 32,
            ["other/name@" + digest],
        )
        is True
    )  # nosec B101
    assert (
        digest_relationship(
            "local/gbrain-aio@" + digest,
            "sha256:" + "cc" * 32,
            "[other/name@sha256:" + "aa" * 32 + "]",
        )
        is False
    )  # nosec B101
    # unresolved/empty image ids never validate
    assert (
        digest_relationship(
            "local/gbrain-aio@" + digest, "", ["local/gbrain-aio@" + digest]
        )
        is False
    )  # nosec B101
    assert digest_relationship("local/gbrain-aio:rc", digest, "") is False  # nosec B101


# --- Retrieval fixture self-proofs ----------------------------------------------


def test_fixture_terms_live_only_in_bodies() -> None:
    """The retrieval fixture self-proves its searches are content, not slug, matches."""
    fixture = build_fixture("runtoken123")
    slugs = {page["slug"] for page in fixture["pages"]}
    assert len(slugs) == len(fixture["pages"]) == 3  # nosec B101
    for page in fixture["pages"]:
        assert page["term"] not in page["slug"]  # nosec B101
        assert page["term"] not in page["title"]  # nosec B101
        assert fixture["expected_hits"][page["term"]] == [page["slug"]]  # nosec B101
        assert (
            hashlib.sha256(page["text"].encode()).hexdigest() == page["sha256"]
        )  # nosec B101
    assert ABSENT_TERM not in fixture["terms"]  # nosec B101
    assert all(
        ABSENT_TERM not in page["text"] for page in fixture["pages"]
    )  # nosec B101
    assert fixture["absent_term"] == ABSENT_TERM  # nosec B101
