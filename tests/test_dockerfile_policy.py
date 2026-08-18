from __future__ import annotations

from tests.conftest import REPO_ROOT


def _compose() -> str:
    return (REPO_ROOT / "compose.yaml").read_text()


def _compose_ports_block() -> str:
    """Return only the lines under the `ports:` key (actual mappings, not comments)."""
    lines = _compose().splitlines()
    out: list[str] = []
    in_ports = False
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("ports:"):
            in_ports = True
            continue
        if in_ports:
            if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
                break
            if stripped.startswith("-"):
                out.append(stripped)
    return "\n".join(out)


def _compose_env_block() -> str:
    """Return only the lines under the `environment:` key (actual vars, not comments)."""
    lines = _compose().splitlines()
    out: list[str] = []
    in_env = False
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("environment:"):
            in_env = True
            continue
        if in_env:
            if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
                break
            if stripped.startswith("-"):
                out.append(stripped)
    return "\n".join(out)


def _dockerfile() -> str:
    return (REPO_ROOT / "Dockerfile").read_text()


def _bootstrap() -> str:
    return (REPO_ROOT / "rootfs/etc/cont-init.d/01-bootstrap.sh").read_text()


def _postgres_init() -> str:
    return (REPO_ROOT / "rootfs/etc/cont-init.d/02-init-postgres.sh").read_text()


def _caddy_tls() -> str:
    return (REPO_ROOT / "rootfs/etc/cont-init.d/03-init-caddy-tls.sh").read_text()


def _worker() -> str:
    return (REPO_ROOT / "rootfs/etc/services.d/gbrain-worker/run").read_text()


# --- Security invariants (static, no Docker needed) ---------------------------

def test_postgres_5432_is_never_published() -> None:
    ports = _compose_ports_block()
    # No host port mapping for 5432 anywhere in the ports block.
    assert "5432" not in ports  # nosec B101


def test_no_agent_database_url_in_compose() -> None:
    # Agents/Hermes must never receive DATABASE_URL. It is only written to
    # /var/lib/gbrain/runtime.env inside the container by the bootstrap script.
    env = _compose_env_block()
    assert "DATABASE_URL" not in env  # nosec B101


def test_only_3132_is_published() -> None:
    ports = _compose_ports_block()
    # The only published port is 3132 (HTTPS via Caddy).
    assert "3132" in ports  # nosec B101
    # No other host port mappings.
    for port in ("8080", "3000", "5432", "3131"):
        assert f":{port}" not in ports  # nosec B101


def test_postgres_listens_on_loopback_only() -> None:
    init = _postgres_init()
    assert "listen_addresses = '127.0.0.1'" in init  # nosec B101


def test_postgres_password_is_required_and_alphanumeric() -> None:
    bootstrap = _bootstrap()
    assert "POSTGRES_PASSWORD is required" in bootstrap  # nosec B101
    assert "must be alphanumeric" in bootstrap  # nosec B101


def test_cert_is_reused_when_present() -> None:
    tls = _caddy_tls()
    assert "reusing existing Caddy TLS cert" in tls  # nosec B101
    assert "cert.pem" in tls and "key.pem" in tls  # nosec B101


def test_worker_is_supervised_and_waits_for_postgres() -> None:
    worker = _worker()
    assert "gbrain jobs supervisor" in worker  # nosec B101
    assert "pg_isready" in worker  # nosec B101


def test_dockerfile_pins_upstream_sha() -> None:
    dockerfile = _dockerfile()
    assert "GBRAIN_GIT_SHA" in dockerfile  # nosec B101
    assert "checkout --detach" in dockerfile  # nosec B101
    assert "rev-parse HEAD" in dockerfile  # nosec B101


def test_dockerfile_uses_s6_overlay() -> None:
    dockerfile = _dockerfile()
    assert "s6-overlay" in dockerfile  # nosec B101
    assert 'ENTRYPOINT ["/init"]' in dockerfile  # nosec B101


def test_dockerfile_runs_gbrain_as_unraid_99_100() -> None:
    """gbrain must run as Unraid's nobody:users (99:100), not 999:999.

    The brain bind mount is owned by 99:100 on Unraid. If gbrain runs as a
    different uid/gid it cannot read 640-permission files in the brain,
    causing EACCES during sync. This guards against regressing to 999.
    """
    dockerfile = _dockerfile()
    assert "useradd --system --uid 99 --gid users" in dockerfile  # nosec B101
    assert "999" not in dockerfile  # nosec B101
    # chown targets must use the users group, not a gbrain group.
    assert "gbrain:users" in dockerfile  # nosec B101
    assert "gbrain:gbrain" not in dockerfile  # nosec B101
