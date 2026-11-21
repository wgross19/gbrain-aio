"""Pure contract logic for the gbrain-aio release suite.

Nothing in this module touches Docker or the network: the strict input
validators, the release classification, the semantic-baseline comparison, the
digest/image relationship check, and the known retrieval fixture are pure
functions so the negative input tests run on any host (including CI before any
daemon exists).

Released under the repo's MIT license; part of ``tests/release``.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# --- The only Docker daemon identity this suite trusts by default -----------
# Verified DinD: TLS on tcp://Docker-in-Docker-DinD:2376, client certs under
# /certs/dind, TLS server name "Docker-in-Docker-DinD" (Alpine containerized).
DIND_HOST = "Docker-in-Docker-DinD"
DIND_ENDPOINT = f"tcp://{DIND_HOST}:2376"
DIND_CERT_PATH = "/certs/dind"
REQUIRED_TLS_ENV = {
    "DOCKER_HOST": DIND_ENDPOINT,
    "DOCKER_TLS_VERIFY": "1",
    "DOCKER_CERT_PATH": DIND_CERT_PATH,
}

# --- Explicit hosted GitHub mode --------------------------------------------
# The suite is a candidate-CI gate: on the GH ephemeral native runner
# GITHUB_ACTIONS=true and RUNNER_ENVIRONMENT=github-hosted identify the
# *verified hosted case*. Only that case may use the runner's own default
# Docker socket; everywhere else host sockets are rejected and the verified
# DinD TLS wire-up above is mandatory.
GITHUB_ACTIONS_VAR = "GITHUB_ACTIONS"
RUNNER_ENVIRONMENT_VAR = "RUNNER_ENVIRONMENT"
HOSTED_ACTIONS_VALUE = "true"
HOSTED_RUNNER_VALUE = "github-hosted"
DEFAULT_HOST_SOCKET = "unix:///var/run/docker.sock"

# Resolved socket modes (recorded for receipts, used by release_ops).
SOCKET_DIND_TLS = "dind-tls"
SOCKET_HOST_DEFAULT = "host-default"

CANDIDATE_VAR = "AIO_CANDIDATE_IMAGE"
PREVIOUS_VAR = "AIO_PREVIOUS_IMAGE"
ARCH_VAR = "AIO_RELEASE_ARCH"
ARCHES = ("amd64", "arm64")

CONTRACT_TEXT = (
    "Candidate CI (GH ephemeral native runner) must run: python -m pytest -q tests/release "
    f"with three immutable, DIGEST-PINNED inputs ({CANDIDATE_VAR}=candidate image ref, "
    f"{PREVIOUS_VAR}=previously released image ref, both name@sha256:<64 lowercase hex> - "
    f"mutable tags (:latest, :version) are rejected; {ARCH_VAR}=amd64|arm64) and either the "
    f"verified DinD TLS daemon wire-up (DOCKER_HOST={DIND_ENDPOINT}, DOCKER_TLS_VERIFY=1, "
    f"DOCKER_CERT_PATH={DIND_CERT_PATH}, TLS server name {DIND_HOST}) or - only in the "
    "explicitly verified hosted GitHub case (GITHUB_ACTIONS=true AND "
    "RUNNER_ENVIRONMENT=github-hosted) - the runner's own default Docker socket. Host sockets "
    "are rejected everywhere else. The suite never pulls, never reaches an external/paid "
    "model, and only touches the uniquely named volumes and containers it created itself."
)


class ReleaseContractError(ValueError):
    """A strict input/endpoint contract violation — raised before any Docker call."""


@dataclass(frozen=True)
class ReleaseInputs:
    candidate_image: str
    previous_image: str
    arch: str
    hosted: bool = False
    socket_mode: str = SOCKET_DIND_TLS


# --- Digest pinning ----------------------------------------------------------
def ref_digest(ref: str) -> str | None:
    """The ``sha256:<64 lowercase hex>`` digest of a pinned ref, else None.

    Mutable tags (``repo/img:latest``), bare names, uppercase hashes, and
    non-sha256 algorithms all return None - a None here means the ref is NOT
    an accepted immutable release reference.
    """
    ref = (ref or "").strip()
    name, sep, digest = ref.partition("@")
    if not sep or not name or "@" in digest:
        return None
    algo, _, hexpart = digest.partition(":")
    if algo != "sha256" or not re.fullmatch(r"[0-9a-f]{64}", hexpart):
        return None
    return digest


def digest_relationship(ref: str, image_id: str, repo_digests: str | Iterable[str] | None) -> bool:
    """True when the pinned digest ref describes the inspected image record.

    The digest either equals the resolved image id or appears among the
    image's RepoDigests (``repo@sha256:<hex>`` entries; docker also accepts
    the bracketed ``[...]`` --format string). This validates the
    digest/image relationship required of every immutable input.
    """
    digest = ref_digest(ref)
    if digest is None or not (image_id or "").strip():
        return False
    if (image_id or "").strip() == digest:
        return True
    if isinstance(repo_digests, str):
        inner = repo_digests.strip()
        if inner.startswith("[") and inner.endswith("]"):
            inner = inner[1:-1]
        entries = [entry.strip() for entry in inner.split() if entry.strip()]
    else:
        entries = [str(entry).strip() for entry in (repo_digests or []) if str(entry).strip()]
    return any(entry.rpartition("@")[2] == digest for entry in entries)


def validate_release_inputs(env: Mapping[str, str]) -> ReleaseInputs:
    """Strictly validate the immutable release inputs, digest pinning, the
    hosted-mode marker, and the Docker socket policy.

    Raises :class:`ReleaseContractError` with the full wiring contract on any
    violation. Never calls Docker.
    """
    missing = [v for v in (CANDIDATE_VAR, PREVIOUS_VAR, ARCH_VAR) if not (env.get(v) or "").strip()]
    if missing:
        raise ReleaseContractError(
            f"missing required input env vars {sorted(missing)}; {CONTRACT_TEXT}"
        )
    candidate, previous, arch = (
        (env[CANDIDATE_VAR].strip(), env[PREVIOUS_VAR].strip(), env[ARCH_VAR].strip())
    )
    bad_refs = [v for v in (candidate, previous) if re.search(r"\s", v)]
    if bad_refs:
        raise ReleaseContractError(
            f"image refs must be whitespace-free (docker argv safety, got {bad_refs!r}); {CONTRACT_TEXT}"
        )
    unpinned = [var for var, ref in ((CANDIDATE_VAR, candidate), (PREVIOUS_VAR, previous))
                if ref_digest(ref) is None]
    if unpinned:
        raise ReleaseContractError(
            f"{unpinned} must be digest-pinned as name@sha256:<64 lowercase hex>; mutable "
            f"tags (:latest, :<version>) are not immutable release references; {CONTRACT_TEXT}"
        )
    if arch not in ARCHES:
        raise ReleaseContractError(f"{ARCH_VAR}={arch!r} is not in {list(ARCHES)}; {CONTRACT_TEXT}")

    # Explicit hosted GitHub mode: the verified hosted case needs BOTH markers
    # with their exact values. Anything else is a non-hosted (or invalid)
    # environment and falls under the stricter socket policy below.
    gh_actions = (env.get(GITHUB_ACTIONS_VAR) or "").strip()
    runner_env = (env.get(RUNNER_ENVIRONMENT_VAR) or "").strip()
    hosted = gh_actions == HOSTED_ACTIONS_VALUE and runner_env == HOSTED_RUNNER_VALUE

    docker_host = (env.get("DOCKER_HOST") or "").strip()
    tls_verify = (env.get("DOCKER_TLS_VERIFY") or "").strip()
    cert_path = (env.get("DOCKER_CERT_PATH") or "").strip()

    if not docker_host or docker_host == DEFAULT_HOST_SOCKET:
        # The runner's own (default) socket: acceptable ONLY in the verified
        # hosted case, rejected everywhere else.
        if not hosted:
            detail = ""
            if gh_actions or runner_env:
                detail = (f" (got {GITHUB_ACTIONS_VAR}={gh_actions!r}, "
                          f"{RUNNER_ENVIRONMENT_VAR}={runner_env!r}; verified hosted requires "
                          f"exactly {HOSTED_ACTIONS_VALUE!r}/{HOSTED_RUNNER_VALUE!r})")
            raise ReleaseContractError(
                "the host/default docker socket is only permitted in the verified hosted "
                f"GitHub case ({GITHUB_ACTIONS_VAR}={HOSTED_ACTIONS_VALUE} AND "
                f"{RUNNER_ENVIRONMENT_VAR}={HOSTED_RUNNER_VALUE}){detail}; elsewhere the "
                f"verified DinD TLS wire-up is required ({REQUIRED_TLS_ENV}); {CONTRACT_TEXT}"
            )
        if tls_verify or cert_path:
            raise ReleaseContractError(
                f"a host/default socket ({docker_host or DEFAULT_HOST_SOCKET}) takes no "
                f"TLS wiring (DOCKER_TLS_VERIFY={tls_verify!r}, DOCKER_CERT_PATH={cert_path!r}); "
                f"use it plainly in the hosted case or wire the DinD trio; {CONTRACT_TEXT}"
            )
        return ReleaseInputs(candidate_image=candidate, previous_image=previous, arch=arch,
                             hosted=hosted, socket_mode=SOCKET_HOST_DEFAULT)
    if docker_host != DIND_ENDPOINT:
        raise ReleaseContractError(
            f"DOCKER_HOST={docker_host!r} is neither the verified DinD endpoint "
            f"({DIND_ENDPOINT}) nor the hosted-case default socket; host sockets are rejected "
            f"outside the verified hosted case ({GITHUB_ACTIONS_VAR}={HOSTED_ACTIONS_VALUE} AND "
            f"{RUNNER_ENVIRONMENT_VAR}={HOSTED_RUNNER_VALUE}); {CONTRACT_TEXT}"
        )
    violated = {
        var: (env.get(var, "").strip(), expected)
        for var, expected in REQUIRED_TLS_ENV.items()
        if env.get(var, "").strip() != expected
    }
    if violated:
        raise ReleaseContractError(
            f"DinD endpoint contract violated (got {violated}); required: {REQUIRED_TLS_ENV}; {CONTRACT_TEXT}"
        )
    return ReleaseInputs(candidate_image=candidate, previous_image=previous, arch=arch,
                         hosted=hosted, socket_mode=SOCKET_DIND_TLS)


def classify_release(previous_ref: str, previous_id: str, candidate_ref: str, candidate_id: str) -> str:
    """Classify the qualification run.

    Returns ``"same-image-smoke"`` when the two immutable inputs resolve to the
    same image (same ref or same resolved image id), ``"distinct-upgrade"``
    otherwise. Unresolved ids (empty strings) never count as equal.
    """
    if previous_ref == candidate_ref:
        return "same-image-smoke"
    if previous_id and candidate_id and previous_id == candidate_id:
        return "same-image-smoke"
    return "distinct-upgrade"


# --- Semantic baseline --------------------------------------------------------
def parse_semantic_version(version: str) -> tuple[int, ...] | None:
    """Numeric dotted version as an int tuple, or None when unparseable/empty."""
    version = (version or "").strip()
    if not version:
        return None
    parts = version.split(".")
    if any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def semantic_baseline_ok(previous_version: str, candidate_version: str) -> bool:
    """Upgrade-direction gate: the candidate must not regress the version.

    Equality is legitimate: a wrapper-only release may carry the same upstream
    version string with different image content - that case is validated
    separately via the distinct image ids and the digest/image relationship.
    A candidate that reports a version OLDER than the previous release is a
    regression and fails here.
    """
    prev = parse_semantic_version(previous_version)
    cand = parse_semantic_version(candidate_version)
    if prev is None or cand is None:
        return False
    width = max(len(prev), len(cand))
    return cand + (0,) * (width - len(cand)) >= prev + (0,) * (width - len(prev))


def generate_runtime_secrets() -> tuple[str, str]:
    """(alphanumeric POSTGRES_PASSWORD, GBRAIN_ADMIN_BOOTSTRAP_TOKEN >= 32 chars)."""
    return secrets.token_hex(20), secrets.token_hex(30)


def redact(text: str, *values: str) -> str:
    out = text or ""
    for value in values:
        if value:
            out = out.replace(value, "[redacted]")
    return out


# --- Known retrieval fixture -------------------------------------------------
# Terms live ONLY in page bodies: slugs and titles are deliberately unrelated,
# so a lexical hit for a term can only come from content, never slug lookup.
HEREDOC_TAG = "AIO_REL_EOF"
GIT_PAGES = (
    {"slug": "notes/persistence-matrix", "path": "notes/persistence-matrix.md",
     "title": "Persistence matrix", "term": "glowworm"},
    {"slug": "notes/replay-ledger", "path": "notes/replay-ledger.md",
     "title": "Replay ledger", "term": "kelpstone"},
)
PUT_WITNESS_SPEC = {"slug": "notes/put-witness", "path": "notes/put-witness.md",
                    "title": "Put witness", "term": "quahog"}
ABSENT_TERM = "umbrafax"
AFTER_UPGRADE_SLUG = "notes/after-upgrade"


def page_text(term: str, title: str, token: str) -> str:
    body = (
        f"The {term} sentence proves that words living only in page bodies stay retrievable "
        f"by lexical search, not only by their slug. Release run token: {token}."
    )
    return f"---\ntitle: {title}\ntype: note\n---\n\n# {title}\n\n{body}\n"


def after_upgrade_page(token: str) -> str:
    body = (
        "Candidate wrote this page after the restart; the CLI write path must still reach disk "
        f"and survive an app read. Release run token: {token}."
    )
    return f"---\ntitle: After upgrade\ntype: note\n---\n\n# After upgrade\n\n{body}\n"


def build_fixture(token: str) -> dict:
    """Build the known fixture and hard-fail if its self-checks are violated.

    Self-checks: every query term is absent from its own slug and title and
    from every other page's slug/title/body-term, terms are pairwise distinct,
    the absent-probe term appears nowhere, and the heredoc tag never appears in
    page text.
    """
    specs = [dict(spec, seeded_by="git") for spec in GIT_PAGES]
    specs.append(dict(PUT_WITNESS_SPEC, seeded_by="app_put"))
    pages = []
    for spec in specs:
        text = page_text(spec["term"], spec["title"], token)
        assert HEREDOC_TAG not in text
        pages.append({**spec, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()})
    terms = [p["term"] for p in pages]
    assert len(set(terms)) == len(terms), f"query terms must be pairwise distinct: {terms}"
    for page in pages:
        assert page["term"] not in page["slug"] and page["term"] not in page["title"], page
        for other in pages:
            if other is not page:
                assert page["term"] not in other["slug"] and page["term"] not in other["title"], page
    assert ABSENT_TERM not in terms and ABSENT_TERM not in token
    assert token and token.strip() == token and not re.search(r"\s", token)
    for page in pages:
        assert ABSENT_TERM not in page["slug"] + page["title"] + page["text"]
    expected_hits = {p["term"]: [p["slug"]] for p in pages}
    git_pages = [p for p in pages if p["seeded_by"] == "git"]
    return {
        "run_token": token,
        "pages": pages,
        "git_pages": git_pages,
        "put_witness": pages[-1],
        "sha_by_slug": {p["slug"]: p["sha256"] for p in pages},
        # Bytes of the GIT-SEEDED pages only (no app serialization): gbrain put
        # rewrites frontmatter when persisting an app write (adds source_kind/
        # ingested_via/ingested_at/trust_tier and normalizes type/title), so the
        # in-put text hash is NOT the on-disk hash for the app-written witness.
        # That page is anchor-checked via put_page's measured disk sha instead.
        "git_sha_by_slug": {p["slug"]: p["sha256"] for p in git_pages},
        "terms": terms,
        "expected_hits": expected_hits,
        "absent_term": ABSENT_TERM,
    }
