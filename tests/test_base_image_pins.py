"""Base-image pins must stay digest-pinned, immutable and multi-arch.

The release builds linux/amd64 and linux/arm64 from this one Dockerfile. An
index pin lets each platform pick its native child; a single-arch child pin
silently reuses (or fails on) the wrong architecture. The registry evidence in
``docs/base-images/base-image-pins.json`` is a captured, read-only record of
what each pinned digest resolves to, including the exact child digests.

These checks are offline: they parse the Dockerfile and the captured receipt.
Re-capture the receipt only when a pin is intentionally changed.
"""

from __future__ import annotations

import json
import re

from tests.conftest import REPO_ROOT

RECEIPT = REPO_ROOT / "docs/base-images/base-image-pins.json"

# The amd64 Caddy child that was pinned before the index pin. It must remain the
# linux/amd64 child of the index, i.e. amd64 bytes do not change.
LEGACY_CADDY_AMD64 = (
    "sha256:d8c17a862962def15cde69863a3a463f25a2664942eafd7bdbf050e9c3116b83"
)


def _dockerfile() -> str:
    return (REPO_ROOT / "Dockerfile").read_text()


def _args() -> dict[str, str]:
    args: dict[str, str] = {}
    for line in _dockerfile().splitlines():
        match = re.fullmatch(r"ARG ([A-Z0-9_]+)=(.*)", line.strip())
        if match:
            args[match.group(1)] = match.group(2).strip()
    return args


def _receipt() -> dict:
    return json.loads(RECEIPT.read_text())


def _linux_children(index: dict) -> dict[str, dict]:
    return {
        f"{m['platform']['os']}/{m['platform']['architecture']}"
        + (f"/{m['platform']['variant']}" if m["platform"].get("variant") else ""): m
        for m in index["manifests"]
        if m["platform"].get("os") == "linux"
    }


def test_every_captured_child_manifest_matches_its_digest() -> None:
    """A digest that does not hash its own body is not an immutable pin."""
    indexes = [_receipt()["caddy"]["index"], *_receipt()["other_base_pins"].values()]
    for index in indexes:
        for child in index["manifests"]:
            assert child["manifest_body_sha256"] == child["digest"]  # nosec B101
            assert child["digest_matches_body"] is True  # nosec B101


def test_caddy_is_pinned_to_the_verified_multiarch_index() -> None:
    args = _args()
    caddy = _receipt()["caddy"]
    assert args["CADDY_IMAGE"] == "caddy:${CADDY_VERSION}-alpine"  # nosec B101
    assert args["CADDY_VERSION"] == caddy["version"] == "2.10.2"  # nosec B101
    assert args["CADDY_DIGEST"] == caddy["index"]["body_sha256"]  # nosec B101
    assert caddy["tag"] == "caddy:2.10.2-alpine"  # nosec B101
    assert caddy["index"]["reference"] == "2.10.2-alpine"  # nosec B101
    assert caddy["index"]["is_index"] is True  # nosec B101
    assert (
        caddy["index"]["media_type"] == "application/vnd.oci.image.index.v1+json"
    )  # nosec B101


def test_caddy_amd64_child_is_byte_identical_to_the_previous_pin() -> None:
    caddy = _receipt()["caddy"]
    assert caddy["legacy_single_arch_pin"] == LEGACY_CADDY_AMD64  # nosec B101
    amd64 = _linux_children(caddy["index"])["linux/amd64"]
    assert amd64["digest"] == LEGACY_CADDY_AMD64  # nosec B101
    assert amd64["manifest_body_sha256"] == LEGACY_CADDY_AMD64  # nosec B101
    assert (
        amd64["media_type"] == "application/vnd.oci.image.manifest.v1+json"
    )  # nosec B101


def test_caddy_index_selects_a_native_arm64_child() -> None:
    caddy = _receipt()["caddy"]
    children = _linux_children(caddy["index"])
    assert "linux/arm64/v8" in children  # nosec B101
    arm64 = children["linux/arm64/v8"]
    assert arm64["digest"] != children["linux/amd64"]["digest"]  # nosec B101
    assert arm64["platform"]["architecture"] == "arm64"  # nosec B101
    assert arm64["platform"]["variant"] == "v8"  # nosec B101
    # The receipt records the exact digests each build platform will select.
    assert caddy["selected_platforms"] == {
        "linux/amd64": children["linux/amd64"]["digest"],
        "linux/arm64/v8": arm64["digest"],
    }  # nosec B101


def test_no_attestation_manifest_is_selectable_as_a_platform() -> None:
    """buildx attestations are index entries under os=unknown; never a base image."""
    caddy = _receipt()["caddy"]
    for child in caddy["index"]["manifests"]:
        if child["platform"].get("os") == "unknown":
            assert child.get("reference_type") == "attestation-manifest"  # nosec B101
    assert set(caddy["selected_platforms"]) == {
        "linux/amd64",
        "linux/arm64/v8",
    }  # nosec B101


def test_other_base_stages_are_pinned_to_multiarch_indexes() -> None:
    """Bun and Debian must already resolve native children for arm64 builds."""
    args = _args()
    pins = _receipt()["other_base_pins"]
    assert args["BUN_DIGEST"] == pins["oven/bun"]["reference"]  # nosec B101
    assert (
        args["DEBIAN_IMAGE"].rsplit("@", 1)[1] == pins["library/debian"]["reference"]
    )  # nosec B101
    for repo in ("oven/bun", "library/debian"):
        index = pins[repo]
        assert index["is_index"] is True  # nosec B101
        children = _linux_children(index)
        assert "linux/amd64" in children, repo  # nosec B101
        assert any(k.startswith("linux/arm64") for k in children), repo  # nosec B101
        assert children["linux/amd64"]["digest"] != next(
            c["digest"] for k, c in children.items() if k.startswith("linux/arm64")
        ), repo  # nosec B101


def test_s6_overlay_is_fetched_per_architecture() -> None:
    """s6 is a tarball stage: its arch map must cover native amd64 and arm64."""
    dockerfile = _dockerfile()
    assert 'S6_ARCH="$(dpkg --print-architecture)"' in dockerfile  # nosec B101
    mapping = _receipt()["s6_overlay"]["arch_mapping"]
    for arch, uname in mapping.items():
        assert f"{arch}) S6_UNAME={uname}" in dockerfile  # nosec B101
    assert mapping == {"amd64": "x86_64", "arm64": "aarch64"}  # nosec B101
