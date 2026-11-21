"""Docker transport and the old->candidate release choreography.

Compact reuse of the validated aio-fleet upgrade rehearsal mechanics on the
verified DinD daemon: single-root env (AIO_APPDATA=/data/aio), brain volume at
/source/brain, HTTPS health on 127.0.0.1:3132 via Caddy, seed-by-git commit,
previous-container-only first-install grant, `gbrain put` --source app writes,
and Postgres page-registration checks. Everything runs with --network none so
the suite can never reach an external or paid model. The suite never pulls:
both immutable input images must already exist on the DinD daemon.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess  # nosec B404 - Docker CLI test harness; no shell interpolation
import time
import uuid
from pathlib import Path

from tests.release.release_contract import (
    ABSENT_TERM,
    DIND_CERT_PATH,
    DIND_ENDPOINT,
    DIND_HOST,
    HEREDOC_TAG,
    SOCKET_DIND_TLS,
    SOCKET_HOST_DEFAULT,
    build_fixture,
    classify_release,
    digest_relationship,
    generate_runtime_secrets,
    redact,
    ref_digest,
)

HEALTH_URL = "https://127.0.0.1:3132/health"
HTTPS_HEALTH_BUDGET = 240
REGISTRATION_BUDGET = 60
SEARCH_SETTLE_BUDGET = 90

# Paid-provider surfaces the contract keeps empty on every container under test.
PROVIDER_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "VOYAGE_API_KEY",
    "TOGETHER_API_KEY",
)
_PRIVATE_HOST_RE = re.compile(
    r"^(https?://)?(localhost|127\.|0\.0\.0\.0|\[::1\]|::1|192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)"
)


class ReleaseChoreographyError(RuntimeError):
    """The Docker-backed qualification run itself failed (already redacted)."""


class CheckoutRecoveryRequired(ReleaseChoreographyError):
    """The app explicitly rejected this write because checkout ownership was not established."""


def d(
    *args: str, check: bool = True, timeout: int = 120, stdin: str | None = None
) -> str | None:
    """Run one docker CLI command against the DinD daemon (env carries the TLS trio)."""
    try:
        completed = (
            subprocess.run(  # nosec B603 - tests execute trusted local commands only
                [shutil.which("docker") or "/usr/bin/docker", *args],
                input=stdin,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        )
    except FileNotFoundError as exc:  # pragma: no cover - CI runners have docker
        raise ReleaseChoreographyError(
            f"docker CLI not found on the runner: {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        if check:
            raise ReleaseChoreographyError(
                f"docker {args[0]} timed out after {timeout}s"
            ) from exc
        return None
    if check and completed.returncode:
        raise ReleaseChoreographyError(
            f"docker {args[0]} failed (rc={completed.returncode}): {completed.stderr[-800:]} "
            f"{completed.stdout[-200:]}"
        )
    return completed.stdout.strip() if not completed.returncode else None


def container_logs(name: str, tail: int = 200) -> str:
    merged = subprocess.run(  # nosec B603 - tests execute trusted local commands only
        [
            shutil.which("docker") or "/usr/bin/docker",
            "logs",
            "--tail",
            str(tail),
            name,
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=90,
    ).stdout
    return merged


def _log_tail(name: str, secrets_to_redact: tuple[str, ...]) -> str:
    interesting = ("fatal", "refus", "error", "migration", "failed", "permission")
    forbidden = ("key", "password", "token", "postgres://")
    return redact(
        "\n".join(
            line
            for line in container_logs(name).splitlines()
            if any(word in line.lower() for word in interesting)
            and not any(word in line.lower() for word in forbidden)
        ),
        *secrets_to_redact,
    )[-2000:]


def exec_container(
    name: str,
    *argv: str,
    check: bool = True,
    timeout: int = 120,
    stdin: str | None = None,
) -> str | None:
    argv_list = ("exec", "-i", name) if stdin is not None else ("exec", name)
    return d(*argv_list, *argv, check=check, timeout=timeout, stdin=stdin)


def sql(name: str, statement: str) -> str:
    out = exec_container(
        name,
        "gosu",
        "postgres",
        "psql",
        "-XAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-d",
        "gbrain",
        "-c",
        statement,
    )
    if not (out is not None):
        raise AssertionError(f"psql failed on {name}")
    return out


def gbrain_cli(
    name: str,
    *args: str,
    check: bool = True,
    timeout: int = 120,
    stdin: str | None = None,
) -> str | None:
    return exec_container(
        name,
        "gosu",
        "gbrain",
        "gbrain",
        *args,
        check=check,
        timeout=timeout,
        stdin=stdin,
    )


def is_running(name: str) -> bool:
    return d("inspect", "--format", "{{.State.Running}}", name, check=False) == "true"


def assert_absent(kind: str, name: str) -> None:
    """Never touch anything that already exists: refuse instead of replacing.

    kind is "container"/"image" (inspect by name) or "volume" (docker volume
    inspect) - the probe MUST target the real resource name or the collision
    check silently no-ops.
    """
    if kind == "volume":
        probe = d("volume", "inspect", "--format", "{{.Name}}", name, check=False)
    else:
        probe = d("inspect", "--format", "{{.Id}}", name, check=False)
    if probe:
        raise ReleaseChoreographyError(
            f"{kind} {name!r} already exists on the daemon; refusing to reuse or replace it"
        )


def verify_dind_session(release_inputs) -> dict:
    """Daemon identity + architecture gate, parameterized by the resolved socket mode.

    DinD-only checks (TLS client certs, the 'Docker-in-Docker-DinD Alpine'
    daemon identity, TLS server name) apply on the verified DinD wire-up.
    They are intentionally NOT applied in the verified hosted GitHub case on
    the runner's own default socket: that daemon is trusted by construction
    (ephemeral, runner-scoped, no shared state), and identity-pinning it to a
    DinD name would be wrong for hosted CI.
    """
    hosted_default_socket = release_inputs.socket_mode == SOCKET_HOST_DEFAULT
    identity = d("info", "--format", "{{.Name}} {{.OperatingSystem}}")
    if not identity:
        raise ReleaseChoreographyError(
            "docker info failed against the configured daemon; cannot verify the daemon identity"
        )
    receipt = {"daemon": identity, "socket_mode": release_inputs.socket_mode}
    if hosted_default_socket:
        receipt["tls_server_name"] = None
    else:
        if (
            release_inputs.socket_mode != SOCKET_DIND_TLS
        ):  # pragma: no cover - validator keeps this true
            raise ReleaseChoreographyError(
                f"unknown socket mode {release_inputs.socket_mode!r}"
            )
        cert_dir = Path(DIND_CERT_PATH)
        missing = [
            n for n in ("ca.pem", "cert.pem", "key.pem") if not (cert_dir / n).is_file()
        ]
        if missing:
            raise ReleaseChoreographyError(
                f"DinD TLS client certs missing under {DIND_CERT_PATH}: {missing}. Wire the verified "
                f"DinD contract: DOCKER_HOST={DIND_ENDPOINT}, DOCKER_TLS_VERIFY=1, "
                f"DOCKER_CERT_PATH={DIND_CERT_PATH}, TLS server name {DIND_HOST}."
            )
        if not identity.startswith("Docker-in-Docker-DinD Alpine"):
            raise ReleaseChoreographyError(
                f"daemon identity contract violated: {identity!r} does not start with "
                "'Docker-in-Docker-DinD Alpine' (outside the verified hosted case this suite "
                "is DinD-only)"
            )
        receipt["tls_server_name"] = DIND_HOST
    server_arch = d("version", "--format", "{{.Server.Arch}}")
    if server_arch != release_inputs.arch:
        raise ReleaseChoreographyError(
            f"AIO_RELEASE_ARCH={release_inputs.arch} does not match the daemon architecture {server_arch!r}"
        )
    receipt["server_arch"] = server_arch
    return receipt


def resolve_image(ref: str) -> dict:
    image_id = d("image", "inspect", "--format", "{{.Id}}", ref, check=False)
    if not image_id:
        raise ReleaseChoreographyError(
            f"digest-pinned image ref {ref} is not present on the DinD daemon; candidate CI must "
            "load/pull BOTH immutable input images (by their pinned digest refs) before pytest - "
            "the suite itself never pulls and has no network"
        )
    repo_digests = d("image", "inspect", "--format", "{{.RepoDigests}}", ref) or ""
    return {
        "ref": ref,
        "id": image_id,
        "arch": d("image", "inspect", "--format", "{{.Architecture}}", ref),
        "revision": d(
            "image",
            "inspect",
            "--format",
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            ref,
            check=False,
        )
        or "unlabeled",
        "repo_digests": repo_digests,
        "digest_pinned": ref_digest(ref) is not None,
        "digest_relationship": digest_relationship(ref, image_id, repo_digests),
    }


def runtime_env(password: str, bootstrap: str) -> dict[str, str]:
    """Single-root boot env (AIO_APPDATA=/data/aio) exactly as the rehearsal proved it."""
    return {
        "AIO_APPDATA": "/data/aio",
        "GBRAIN_LAN_BIND": "127.0.0.1",
        "BRAIN_UID": "99",
        "BRAIN_GID": "100",
        "POSTGRES_USER": "gbrain",
        "POSTGRES_DB": "gbrain",
        "POSTGRES_PASSWORD": password,
        "GBRAIN_ADMIN_BOOTSTRAP_TOKEN": bootstrap,
        "GBRAIN_PUBLIC_URL": "https://127.0.0.1:3132",
    }


def seed_script(fixture: dict) -> str:
    lines = ["set -e", "cd /source/brain", "mkdir -p notes"]
    for page in fixture["git_pages"]:
        lines += [
            f"cat > {page['path']} <<'{HEREDOC_TAG}'",
            page["text"].rstrip("\n"),
            HEREDOC_TAG,
        ]
    lines += [
        "git init -q -b main",
        "git add notes",
        "git -c user.email=fixture@invalid.local -c user.name=fixture commit -qm fixture",
        "chown -R 99:100 /source/brain",
    ]
    return "\n".join(lines) + "\n"


def boot(
    image_ref: str, name: str, env: dict[str, str], mounts: dict[str, str]
) -> None:
    assert_absent("container", name)
    argv = ["run", "-d", "--network", "none", "--name", name]
    for volume, target in mounts.items():
        argv += ["-v", f"{volume}:{target}"]
    for key, value in env.items():
        argv += ["-e", f"{key}={value}"]
    argv.append(image_ref)
    d(*argv, timeout=90)
    network_mode = d("inspect", "--format", "{{.HostConfig.NetworkMode}}", name)
    if network_mode != "none":
        raise ReleaseChoreographyError(
            f"{name} must run with --network none (no external/paid model reachability); got {network_mode!r}"
        )


def wait_https_health(
    name: str, secrets_to_redact: tuple[str, ...], budget: int = HTTPS_HEALTH_BUDGET
) -> None:
    deadline = time.monotonic() + budget
    last = ""
    while time.monotonic() < deadline:
        if not is_running(name):
            raise ReleaseChoreographyError(
                f"{name} stopped before HTTPS health; log tail:\n{_log_tail(name, secrets_to_redact)}"
            )
        out = exec_container(name, "curl", "-kfsS", HEALTH_URL, check=False)
        if out:
            try:
                if json.loads(out).get("status") == "ok":
                    return
            except ValueError:
                pass
            last = out
        time.sleep(2)
    raise ReleaseChoreographyError(
        f"{name} never reached HTTPS health within {budget}s; last probe: {redact(last, *secrets_to_redact)!r}"
    )


def wait_page_registered(
    name: str, slug: str, budget: int = REGISTRATION_BUDGET
) -> None:
    def registered() -> bool:
        return _page_rows(name, slug) == "1"

    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if registered():
            return
        if not is_running(name):
            break
        time.sleep(2)
    gbrain_cli(name, "sync", "--source", "brain", "--no-pull", check=False, timeout=150)
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if registered():
            return
        if not is_running(name):
            break
        time.sleep(2)
    raise ReleaseChoreographyError(
        f"{slug} never registered in the DB on {name}; final rows="
        f"{_page_rows(name, slug)}"
    )


def _page_rows(name: str, slug: str) -> str:
    rows = exec_container(
        name,
        "gosu",
        "postgres",
        "psql",
        "-XAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-v",
        f"slug={slug}",
        "-d",
        "gbrain",
        stdin="select count(*) from pages where slug=:'slug' and source_id='brain';\n",
    )
    if rows is None:
        raise ReleaseChoreographyError(
            f"page-count query returned no output for {slug}"
        )
    return rows


def search_once(name: str, query: str) -> dict:
    completed = (
        subprocess.run(  # nosec B603 - tests execute trusted local commands only
            [
                shutil.which("docker") or "/usr/bin/docker",
                "exec",
                name,
                "gosu",
                "gbrain",
                "gbrain",
                "search",
                query,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=90,
        )
    )
    return {
        "query": query,
        "exit": completed.returncode,
        "stdout": completed.stdout[:4000],
        "stderr": completed.stderr[-400:],
    }


def search_settled(name: str, query: str, expected_slugs: list[str]) -> dict:
    """Give lexical indexing time to settle, but only for queries that MUST hit."""
    receipt = search_once(name, query)
    deadline = time.monotonic() + (SEARCH_SETTLE_BUDGET if expected_slugs else 0)
    while (
        expected_slugs
        and time.monotonic() < deadline
        and not any(slug in receipt["stdout"] for slug in expected_slugs)
    ):
        time.sleep(3)
        receipt = search_once(name, query)
    return receipt


def put_page(
    name: str,
    slug: str,
    text: str,
    expected_token: str,
    secrets_to_redact: tuple[str, ...],
) -> dict:
    request_id = str(uuid.uuid4())
    put = subprocess.run(  # nosec B603 - tests execute trusted local commands only
        [
            shutil.which("docker") or "/usr/bin/docker",
            "exec",
            "-i",
            name,
            "gosu",
            "gbrain",
            "gbrain",
            "put",
            slug,
            "--source-id",
            "brain",
            "--request-id",
            request_id,
            "--content-origin",
            "tool_output",
        ],
        input=text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=180,
    )
    if put.returncode:
        status = (
            subprocess.run(  # nosec B603 - tests execute trusted local commands only
                [
                    shutil.which("docker") or "/usr/bin/docker",
                    "exec",
                    name,
                    "gosu",
                    "gbrain",
                    "gbrain",
                    "sources",
                    "writer",
                    "status",
                    "--json",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=90,
            )
        )
        error = (
            CheckoutRecoveryRequired
            if re.search(
                r"^Error \[recovery_required\]: The physical checkout identity changed or belongs to another owner\.",
                put.stderr,
                re.MULTILINE,
            )
            else ReleaseChoreographyError
        )
        raise error(
            f"gbrain put {slug} failed on {name} (writer identity contract); request_id={request_id}; "
            f"put exit={put.returncode}; put stderr tail: "
            f"{redact(put.stderr[-1200:], *secrets_to_redact)}; put stdout tail: "
            f"{redact(put.stdout[-600:], *secrets_to_redact)}; writer-status exit="
            f"{status.returncode}; writer-status stdout: "
            f"{redact(status.stdout[-1200:], *secrets_to_redact) or '(empty)'}; writer-status stderr: "
            f"{redact(status.stderr[-400:], *secrets_to_redact)}"
        )
    disk = exec_container(name, "cat", f"/source/brain/{slug}.md", check=False) or ""
    disk_sha_out = (
        exec_container(name, "sha256sum", f"/source/brain/{slug}.md", check=False) or ""
    )
    readback = (
        gbrain_cli(name, "get", slug, "--source", "brain", check=False, timeout=120)
        or ""
    )
    return {
        "request_id": request_id,
        "stdout_tail": put.stdout[-400:],
        "disk_has_token": expected_token in disk,
        "disk_sha256": (
            disk_sha_out.split(" ")[0] if disk_sha_out.split(" ") else None
        ),
        "readback_has_token": expected_token in readback,
    }


def paid_model_surface(name: str) -> dict:
    env_text = exec_container(name, "printenv", check=False) or ""
    present: list[str] = []
    parsed: dict[str, str] = {}
    for line in env_text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key] = value
            if key in PROVIDER_ENV_KEYS and value.strip():
                present.append(key)
    ollama = parsed.get("OLLAMA_BASE_URL", "").strip()
    ollama_state = (
        "absent"
        if not ollama
        else ("private" if _PRIVATE_HOST_RE.match(ollama) else f"public:{ollama}")
    )
    config_raw = (
        exec_container(
            name, "cat", "/data/aio/gbrain-home/.gbrain/config.json", check=False
        )
        or ""
    )
    flagged: list[str] = []
    if config_raw.strip():
        try:

            def walk(node, path: str = "") -> None:
                if isinstance(node, dict):
                    for key, value in node.items():
                        child = f"{path}.{key}" if path else key
                        if (
                            isinstance(value, str)
                            and value.strip()
                            and ("apikey" in key.lower() or "api_key" in key.lower())
                        ):
                            flagged.append(child)
                        walk(value, child)
                elif isinstance(node, list):
                    for index, value in enumerate(node):
                        walk(value, f"{path}[{index}]")

            walk(json.loads(config_raw))
        except ValueError:
            flagged.append("config.json unparseable")
    return {
        "provider_env_keys_present": present,
        "ollama_base_url": ollama_state,
        "config_flagged_keys": flagged,
    }


def snapshot(name: str, fixture: dict, role: str) -> dict:
    version = (
        exec_container(name, "bun", "-p", 'require("/opt/gbrain/package.json").version')
        or ""
    )
    page_sha: dict[str, str] = {}
    page_rows: dict[str, int] = {}
    app_read: dict[str, bool] = {}
    for page in fixture["pages"]:
        slug = page["slug"]
        sha_out = (
            exec_container(
                name, "sha256sum", f"/source/brain/{page['path']}", check=False
            )
            or ""
        )
        page_sha[slug] = sha_out.split(" ")[0] if sha_out.split(" ") else "missing"
        page_rows[slug] = int(_page_rows(name, slug))
        app_out = (
            gbrain_cli(name, "get", slug, "--source", "brain", check=False, timeout=120)
            or ""
        )
        app_read[slug] = fixture["run_token"] in app_out
    return {
        "role": role,
        "version": version,
        "cluster_id": sql(name, "select system_identifier from pg_control_system()"),
        "page_sha256": page_sha,
        "page_rows": page_rows,
        "app_read_has_token": app_read,
        "image_id": d("inspect", "--format", "{{.Image}}", name),
        "https": "ok",
        "network_mode": d("inspect", "--format", "{{.HostConfig.NetworkMode}}", name),
    }


def _comparable(stage: dict) -> tuple:
    return (
        stage["version"],
        stage["cluster_id"],
        stage["page_sha256"],
        stage["page_rows"],
        stage["app_read_has_token"],
        stage["image_id"],
    )


def checkout_identity() -> dict:
    """Distinguish the checkout that RUNS the suite from the releases under test.

    The receipts' image refs/ids/revisions describe the tested releases; the
    working checkout is recorded here for the report (git HEAD when discoverable).
    """
    identity = {
        "note": (
            "images under test are identified by the receipt refs/ids/revisions; "
            "the suite may be run from a checkout that is NOT a tested release"
        ),
        "git_head": None,
    }
    try:
        head = subprocess.run(  # nosec B603 - tests execute trusted local commands only
            [shutil.which("git") or "/usr/bin/git", "rev-parse", "HEAD"],
            text=True,
            timeout=30,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if head.returncode == 0:
            identity["git_head"] = head.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return identity


_PARENT = (
    "/source/.gbrain-owner-" + hashlib.sha256(b"/source/brain").hexdigest() + ".json"
)
_BACKUP = "/data/aio/gbrain-home/.gbrain/persistence/source-parent-reservation.json"


def prepare_reservation(name: str, saved: bool) -> None:
    # Only in the old, writable fixture, never on the replacement. Reject a conflicting backup.
    exec_container(
        name,
        "gosu",
        "gbrain",
        "sh",
        "-ec",
        """
        test -s "$1" && test ! -L "$1" || exit 1
        test ! -L "$2"
        if test -e "$2"; then cmp -s "$1" "$2"; fi
        if test "$3" = saved; then
            if ! test -e "$2"; then install -m 600 "$1" "$2"; fi
            cmp -s "$1" "$2"
        else
            # Explicit legacy fixture, even when a future baseline saves its own copy.
            rm -f -- "$2"
            test ! -e "$2"
        fi
    """,
        "fixture",
        _PARENT,
        _BACKUP,
        "saved" if saved else "missing",
    )


def probe_reservation(name, saved, stage, fixture, secrets):
    slug = "notes/reservation-" + stage
    token = fixture["run_token"]
    text = f"---\ntitle: Reservation {stage}\ntype: note\n---\n\n# Reservation {stage}\n\n{token}\n"
    if saved:
        exec_container(name, "gosu", "gbrain", "cmp", "-s", _PARENT, _BACKUP)
        result = put_page(name, slug, text, token, secrets)
        if not (result["disk_has_token"] and result["readback_has_token"]):
            raise ReleaseChoreographyError(
                f"{stage}: new write missing from disk or app"
            )
        return "write-verified"
    try:
        put_page(name, slug, text, token, secrets)
    except CheckoutRecoveryRequired as exc:
        exec_container(name, "test", "!", "-e", f"/source/brain/{slug}.md")
        if _page_rows(name, slug) != "0":
            raise ReleaseChoreographyError(
                f"{stage}: refused write created a page row"
            ) from exc
        return "recovery_required"
    raise ReleaseChoreographyError(
        f"{stage}: missing-reservation control unexpectedly accepted a write; review ownership"
    )


def run_choreography(
    release_inputs, receipts_dir: Path, *, saved_reservation: bool = True
) -> dict:
    """Seed on the previous image, recreate with the candidate on the same volumes,
    prove the persisted record readback, restart, and prove it again. Creates only
    uniquely named volumes/containers and tears down exactly those resources."""
    password, bootstrap = generate_runtime_secrets()
    run_token = "aio-rel-" + uuid.uuid4().hex[:8]
    prefix = run_token
    old_name, new_name = f"{prefix}-previous", f"{prefix}-candidate"
    data_vol, brain_vol = f"{prefix}-data", f"{prefix}-brain"
    redaction_values = (password, bootstrap)
    owned_containers: list[str] = []
    owned_volumes: list[str] = []

    receipts: dict = {
        "suite": "tests/release (gbrain-aio release qualification)",
        "run_id": run_token,
        "classification": None,
        "platform_arch": release_inputs.arch,
        "resource_prefix": prefix,
        "inputs": {
            "candidate_image": release_inputs.candidate_image,
            "previous_image": release_inputs.previous_image,
            "arch": release_inputs.arch,
            "hosted_github_mode": release_inputs.hosted,
            "socket_mode": release_inputs.socket_mode,
        },
        "checkout": checkout_identity(),
        "dind": None,
        "images": None,
        "volumes": {"data": data_vol, "brain": brain_vol},
        "containers": {"previous": old_name, "candidate": new_name},
        "fixture": None,
        "stages": {},
        "searches": {"before": {}, "after": {}, "restart": {}},
        "contract": {},
        "verdict": None,
    }

    def persist() -> None:
        payload = redact(
            json.dumps(receipts, indent=2, sort_keys=True), *redaction_values
        )
        receipts_dir.mkdir(parents=True, exist_ok=True)
        (receipts_dir / f"{run_token}.json").write_text(payload + "\n")

    try:
        receipts["dind"] = verify_dind_session(release_inputs)
        previous = resolve_image(release_inputs.previous_image)
        candidate = resolve_image(release_inputs.candidate_image)
        receipts["images"] = {"previous": previous, "candidate": candidate}
        if not (previous["arch"] == candidate["arch"] == release_inputs.arch):
            raise ReleaseChoreographyError(
                f"image architecture mismatch: previous={previous['arch']}, candidate={candidate['arch']}, "
                f"AIO_RELEASE_ARCH={release_inputs.arch}"
            )
        classification = classify_release(
            previous["ref"], previous["id"], candidate["ref"], candidate["id"]
        )
        receipts["classification"] = classification

        fixture = build_fixture(run_token)
        receipts["fixture"] = {
            "run_token": run_token,
            "sha_by_slug": fixture["sha_by_slug"],
            "git_sha_by_slug": fixture["git_sha_by_slug"],
            "git_slugs": [p["slug"] for p in fixture["git_pages"]],
            "put_witness_slug": fixture["put_witness"]["slug"],
            "terms": fixture["terms"],
            "expected_hits": fixture["expected_hits"],
            "absent_term": ABSENT_TERM,
        }
        env = runtime_env(password, bootstrap)
        mounts = {data_vol: "/data/aio", brain_vol: "/source/brain"}

        for volume in (data_vol, brain_vol):
            assert_absent("volume", volume)
            if d("volume", "create", volume) != volume:
                raise ReleaseChoreographyError(f"volume create did not echo {volume}")
            owned_volumes.append(volume)

        # Seed the brain volume through the previous image (git fixture).
        d(
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "sh",
            "-v",
            f"{brain_vol}:/source/brain",
            previous["ref"],
            "-ec",
            seed_script(fixture),
            timeout=120,
        )

        boot(previous["ref"], old_name, env, mounts)
        owned_containers.append(old_name)
        wait_https_health(old_name, redaction_values)
        # Known first-install grant, proven by the rehearsal, for the disposable
        # previous container only. The candidate boots through its own /init path.
        d("exec", old_name, "chown", "99:100", "/source")
        d("exec", old_name, "bash", "/usr/local/bin/gbrain-first-boot", timeout=900)
        for page in fixture["git_pages"]:
            wait_page_registered(old_name, page["slug"])
        witness = put_page(
            old_name,
            fixture["put_witness"]["slug"],
            fixture["put_witness"]["text"],
            run_token,
            redaction_values,
        )
        wait_page_registered(old_name, fixture["put_witness"]["slug"])
        for term in fixture["terms"]:
            receipts["searches"]["before"][term] = search_settled(
                old_name, term, fixture["expected_hits"][term]
            )
        receipts["searches"]["before"][ABSENT_TERM] = search_settled(
            old_name, ABSENT_TERM, []
        )
        receipts["stages"]["before"] = snapshot(old_name, fixture, "previous")
        receipts["stages"]["baseline_write"] = witness
        receipts["contract"]["previous"] = paid_model_surface(old_name)

        if not (witness["disk_has_token"] and witness["readback_has_token"]):
            raise AssertionError("baseline is not writable")
        prepare_reservation(old_name, saved_reservation)
        receipts["reservation"] = {
            "baseline": "matching-backup" if saved_reservation else "no-backup"
        }

        d("stop", "-t", "60", old_name, timeout=180)
        d("rm", "-v", old_name)
        owned_containers.remove(old_name)

        boot(candidate["ref"], new_name, env, mounts)
        owned_containers.append(new_name)
        wait_https_health(new_name, redaction_values)
        for page in fixture["pages"]:
            wait_page_registered(new_name, page["slug"])
        for term in fixture["terms"]:
            receipts["searches"]["after"][term] = search_settled(
                new_name, term, fixture["expected_hits"][term]
            )
        receipts["searches"]["after"][ABSENT_TERM] = search_settled(
            new_name, ABSENT_TERM, []
        )
        receipts["stages"]["after"] = snapshot(new_name, fixture, "candidate")
        receipts["contract"]["candidate"] = paid_model_surface(new_name)
        receipts["reservation"]["after"] = probe_reservation(
            new_name, saved_reservation, "after", fixture, redaction_values
        )

        before, after = receipts["stages"]["before"], receipts["stages"]["after"]
        if after["cluster_id"] != before["cluster_id"]:
            raise ReleaseChoreographyError(
                f"cluster identity changed across the upgrade: {before['cluster_id']} != {after['cluster_id']}"
            )
        if (
            after["page_sha256"] != before["page_sha256"]
            or after["page_rows"] != before["page_rows"]
            or after["app_read_has_token"] != before["app_read_has_token"]
        ):
            raise ReleaseChoreographyError(
                f"persisted record readback changed across the upgrade: "
                f"before={before['page_sha256']} after={after['page_sha256']}"
            )

        d("restart", "-t", "60", new_name, timeout=180)
        wait_https_health(new_name, redaction_values)
        receipts["searches"]["restart"] = {
            term: search_settled(new_name, term, fixture["expected_hits"][term])
            for term in fixture["terms"]
        }
        receipts["searches"]["restart"][ABSENT_TERM] = search_settled(
            new_name, ABSENT_TERM, []
        )
        receipts["stages"]["restart"] = snapshot(new_name, fixture, "candidate-restart")
        receipts["reservation"]["restart"] = probe_reservation(
            new_name, saved_reservation, "restart", fixture, redaction_values
        )
        if _comparable(receipts["stages"]["restart"]) != _comparable(after):
            raise ReleaseChoreographyError(
                f"state changed across restart: {after['page_sha256']} != "
                f"{receipts['stages']['restart']['page_sha256']}"
            )

        receipts["verdict"] = "PASS" if saved_reservation else "CONTROL: WRITE BLOCKED"
        receipts["survivor"] = {"container": new_name, "volumes": [data_vol, brain_vol]}
        persist()
        # The post-restart write gate runs after this fixture returns: keep the
        # surviving candidate and its volumes alive; the session fixture in
        # conftest.py tears them down after the last test has read them.
        owned_containers.remove(new_name)
        owned_volumes.clear()
        return receipts
    except Exception as exc:
        receipts["verdict"] = "FAIL"
        receipts["error"] = redact(str(exc), *redaction_values)
        for name in owned_containers:
            receipts.setdefault("logs", {})[name] = _log_tail(name, redaction_values)
        persist()
        raise ReleaseChoreographyError(
            receipts["error"] + f" | receipts: {receipts_dir}"
        ) from exc
    finally:
        persist()
        for name in owned_containers:
            d("rm", "-fv", name, check=False)
        for volume in owned_volumes:
            d("volume", "rm", volume, check=False)


def teardown_survivor(receipts: dict) -> None:
    """Remove the surviving candidate container and its volumes after the suite."""
    survivor = receipts.get("survivor") or {}
    container = survivor.get("container")
    if container:
        d("rm", "-fv", container, check=False)
    for volume in survivor.get("volumes", []):
        d("volume", "rm", volume, check=False)
