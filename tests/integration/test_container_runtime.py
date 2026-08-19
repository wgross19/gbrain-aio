from __future__ import annotations

import time

import pytest

from tests.helpers import (
    IMAGE_TAG,
    INTERNAL_HEALTH,
    PUBLISHED_PORT,
    ContainerHandle,
    DockerRuntime,
    base_env,
    docker_available,
    docker_volume,
)

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def runtime() -> DockerRuntime:
    if not docker_available():
        pytest.skip("Docker is unavailable; integration tests require Docker.")
    runtime = DockerRuntime(IMAGE_TAG)
    runtime.build()
    return runtime


# --- 1. Image starts successfully -------------------------------------------
def test_image_starts_and_reaches_internal_health(runtime: DockerRuntime) -> None:
    with runtime.container() as c:
        c.wait_for_internal_health()
        assert c.is_running()  # nosec B101


# --- 2. PostgreSQL initializes ------------------------------------------------
def test_postgres_initializes(runtime: DockerRuntime) -> None:
    with runtime.container() as c:
        c.wait_for_internal_health()
        # PG_VERSION is written by initdb on first boot.
        assert c.path_exists("/data/postgres/PG_VERSION")  # nosec B101
        # pg_isready against the loopback listener inside the container.
        result = c.exec("pg_isready -h 127.0.0.1 -p 5432")
        assert result.returncode == 0  # nosec B101
        assert "accepting connections" in result.stdout  # nosec B101


# --- 3. GBrain starts --------------------------------------------------------
def test_gbrain_http_serves_health(runtime: DockerRuntime) -> None:
    with runtime.container() as c:
        c.wait_for_internal_health()
        result = c.exec(f"curl -fsS {INTERNAL_HEALTH}")
        assert result.returncode == 0  # nosec B101
        assert '"status":"ok"' in result.stdout  # nosec B101


# --- 4. Caddy starts ---------------------------------------------------------
def test_caddy_serves_https(runtime: DockerRuntime) -> None:
    with runtime.container() as c:
        c.wait_for_https()
        # Caddy is the only process that publishes 3132.
        result = c.exec("curl -kfsS https://127.0.0.1:3132/health")
        assert result.returncode == 0  # nosec B101
        assert '"status":"ok"' in result.stdout  # nosec B101


# --- 5. External health endpoint works ---------------------------------------
def test_external_health_endpoint(runtime: DockerRuntime) -> None:
    with runtime.container() as c:
        c.wait_for_https()
        # The published host port proxies to Caddy -> GBrain health.
        result = c.exec(f"curl -kfsS https://127.0.0.1:{PUBLISHED_PORT}/health")
        assert result.returncode == 0  # nosec B101
        assert '"status":"ok"' in result.stdout  # nosec B101


# --- 6. PostgreSQL is not published to the host -------------------------------
def test_postgres_not_published_to_host(runtime: DockerRuntime) -> None:
    with runtime.container() as c:
        c.wait_for_internal_health()
        # Inspect the published port map from the host. 5432 must be absent;
        # 3132 (HTTPS via Caddy) must be the only published port.
        port_map = runtime.inspect_state(c.name, "NetworkSettings.Ports")
        assert "5432" not in port_map  # nosec B101
        assert "3132" in port_map  # nosec B101


# --- 7. Persistent data survives restart -------------------------------------
def test_persistent_data_survives_restart(runtime: DockerRuntime) -> None:
    with runtime.container() as c:
        c.wait_for_internal_health()
        pg_version_before = c.read_text("/data/postgres/PG_VERSION").strip()
        runtime_env_before = c.read_text("/var/lib/gbrain/runtime.env")
        assert pg_version_before  # nosec B101
        assert "DATABASE_URL=" in runtime_env_before  # nosec B101

        c.restart()
        c.wait_for_internal_health()

        pg_version_after = c.read_text("/data/postgres/PG_VERSION").strip()
        runtime_env_after = c.read_text("/var/lib/gbrain/runtime.env")
        assert pg_version_after == pg_version_before  # nosec B101
        assert runtime_env_after == runtime_env_before  # nosec B101


# --- 8. Required environment variables are enforced --------------------------
def _wait_for_container_exit(c: ContainerHandle, *, timeout: int = 60) -> None:
    """Wait for the container to stop. Bootstrap exits 64 on invalid env."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not c.is_running():
            return
        time.sleep(1)
    raise AssertionError(f"{c.name} did not exit within {timeout}s. Logs:\n{c.logs()}")


def test_missing_postgres_password_fails_boot(runtime: DockerRuntime) -> None:
    env = base_env()
    # Empty string triggers the bootstrap's `-z` check (POSTGRES_PASSWORD is
    # required). A `del` would not propagate because container() starts from a
    # fresh base_env() and merges overrides on top.
    env["POSTGRES_PASSWORD"] = ""
    with runtime.container(env_overrides=env) as c:
        # The container should exit (bootstrap exits 64) rather than run.
        _wait_for_container_exit(c)
        logs = c.logs()
        assert "POSTGRES_PASSWORD is required" in logs  # nosec B101


def test_non_alphanumeric_password_fails_boot(runtime: DockerRuntime) -> None:
    env = base_env()
    env["POSTGRES_PASSWORD"] = "has space!"
    with runtime.container(env_overrides=env) as c:
        _wait_for_container_exit(c)
        logs = c.logs()
        assert "must be alphanumeric" in logs  # nosec B101


# --- 9. Bundled certificate is generated and reused --------------------------
def test_cert_generated_and_reused(runtime: DockerRuntime) -> None:
    with runtime.container() as c:
        c.wait_for_https()
        cert_path = "/config/caddy/certs/cert.pem"
        key_path = "/config/caddy/certs/key.pem"
        ca_path = "/config/caddy/certs/ca.pem"
        assert c.path_exists(cert_path)  # nosec B101
        assert c.path_exists(key_path)  # nosec B101
        assert c.path_exists(ca_path)  # nosec B101
        cert_before = c.read_text(cert_path)

        c.restart()
        c.wait_for_https()

        cert_after = c.read_text(cert_path)
        assert cert_after == cert_before  # nosec B101


# --- 10. Mounted brain path behaves correctly ---------------------------------
def test_mounted_brain_path_is_visible(runtime: DockerRuntime) -> None:
    # Use a Docker named volume for the brain mount rather than a host tempdir.
    # The container chowns the mounted brain to BRAIN_UID:BRAIN_GID (99:100)
    # during first boot. On CI runners (e.g. GitHub Actions) the runner user
    # cannot delete a host bind-mount directory once the container has chowned
    # it to 99:100, so a tempdir leaks and `TemporaryDirectory` cleanup raises
    # PermissionError. A named volume is owned by Docker, chowns fine, and is
    # removed cleanly regardless of the runner's user.
    with docker_volume("gbrain-aio-pytest-brain") as brain_volume:
        with runtime.container(brain_mount=brain_volume) as c:
            c.wait_for_internal_health()
            # The brain mount is exposed at /test-brain (SOURCE_NAME=test-brain).
            assert c.path_exists("/test-brain")  # nosec B101
            # Bootstrap chowns it to BRAIN_UID:BRAIN_GID (99:100).
            result = c.exec("stat -c '%u:%g' /test-brain")
            assert result.stdout.strip() == "99:100"  # nosec B101


# --- 11. Worker startup does not silently fail --------------------------------
def test_worker_process_runs(runtime: DockerRuntime) -> None:
    with runtime.container() as c:
        c.wait_for_internal_health()
        # The gbrain jobs supervisor must be running as a long-lived process.
        # `ps` is not installed in the image, so read /proc cmdlines instead.
        # The supervisor runs as: bun run /opt/gbrain/src/cli.ts jobs supervisor
        result = c.exec(
            "for p in /proc/[0-9]*/cmdline; do tr '\\0' ' ' < \"$p\" 2>/dev/null; echo; done | grep -E 'jobs supervisor'"
        )
        assert result.returncode == 0  # nosec B101
        assert "jobs supervisor" in result.stdout  # nosec B101
