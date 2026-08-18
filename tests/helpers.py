from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess  # nosec B404 - test helpers shell out only to local tooling
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tests.conftest import REPO_ROOT

# The image publishes HTTPS on 3132 (Caddy). Internal GBrain HTTP is 3131.
# Postgres listens on 127.0.0.1:5432 inside the container and is never published.
IMAGE_TAG = "gbrain-aio:pytest"
INTERNAL_HEALTH = "http://127.0.0.1:3131/health"
PUBLISHED_PORT = 3132


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 - tests execute trusted local commands only
        command,
        cwd=cwd or REPO_ROOT,
        env=env,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = run_command(["docker", "info"], check=False)
    return result.returncode == 0


def docker_image_exists(image_tag: str) -> bool:
    result = run_command(["docker", "image", "inspect", image_tag], check=False)
    return result.returncode == 0


def ensure_pytest_image(image_tag: str) -> None:
    if os.environ.get("AIO_PYTEST_USE_PREBUILT_IMAGE") == "true":
        if not docker_image_exists(image_tag):
            raise AssertionError(
                f"Expected prebuilt pytest image {image_tag} to be loaded before the test run."
            )
        return
    run_command(["docker", "build", "--platform", "linux/amd64", "-t", image_tag, "."])


def reserve_host_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return sock.getsockname()[1]


def create_docker_volume(prefix: str) -> str:
    volume_name = f"{prefix}-{uuid.uuid4().hex[:10]}"
    run_command(["docker", "volume", "create", volume_name])
    return volume_name


def remove_docker_volume(volume_name: str) -> None:
    run_command(["docker", "volume", "rm", "-f", volume_name], check=False)


@contextmanager
def docker_volume(prefix: str) -> Iterator[str]:
    volume_name = create_docker_volume(prefix)
    try:
        yield volume_name
    finally:
        remove_docker_volume(volume_name)


def docker_exec(
    container_name: str, command: str, *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return run_command(
        ["docker", "exec", container_name, "sh", "-lc", command], check=check
    )


def container_path_exists(container_name: str, path: str) -> bool:
    return (
        docker_exec(
            container_name, f"test -e {shlex.quote(path)}", check=False
        ).returncode
        == 0
    )


def read_container_file(container_name: str, path: str) -> str:
    return docker_exec(container_name, f"cat {shlex.quote(path)}").stdout


def container_file_size(container_name: str, path: str) -> int:
    return int(
        docker_exec(container_name, f"wc -c < {shlex.quote(path)}").stdout.strip()
    )


def base_env() -> dict[str, str]:
    """Required env for a healthy first boot. Alphanumeric secrets only."""
    return {
        "POSTGRES_USER": "gbrain",
        "POSTGRES_PASSWORD": "testpass123",
        "POSTGRES_DB": "gbrain",
        "GBRAIN_ADMIN_BOOTSTRAP_TOKEN": "testbootstrap123",
        "GBRAIN_LAN_BIND": "127.0.0.1",
        "GBRAIN_PUBLIC_URL": "https://127.0.0.1:3132",
        "SOURCE_NAME": "test-brain",
        "BRAIN_UID": "999",
        "BRAIN_GID": "100",
    }


class DockerRuntime:
    def __init__(self, image_tag: str) -> None:
        self.image_tag = image_tag

    def build(self) -> None:
        ensure_pytest_image(self.image_tag)

    def inspect_state(self, name: str, field: str) -> str:
        result = run_command(
            ["docker", "inspect", "-f", f"{{{{.{field}}}}}", name],
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def logs(self, name: str) -> str:
        result = run_command(["docker", "logs", name], check=False)
        return result.stdout + result.stderr

    def remove(self, name: str) -> None:
        run_command(["docker", "rm", "-f", name], check=False)

    @contextmanager
    def container(
        self,
        *,
        env_overrides: dict[str, str] | None = None,
        brain_mount: str | None = None,
    ) -> Iterator["ContainerHandle"]:
        suffix = uuid.uuid4().hex[:10]
        name = f"gbrain-aio-pytest-{suffix}"
        http_port = reserve_host_port()
        data_volume = create_docker_volume(f"{name}-data")
        home_volume = create_docker_volume(f"{name}-home")
        caddy_volume = create_docker_volume(f"{name}-caddy")
        try:
            command = [
                "docker",
                "run",
                "-d",
                "--platform",
                "linux/amd64",
                "--name",
                name,
                "-p",
                f"{http_port}:{PUBLISHED_PORT}",
                "-v",
                f"{data_volume}:/data/postgres",
                "-v",
                f"{home_volume}:/var/lib/gbrain",
                "-v",
                f"{caddy_volume}:/config/caddy",
            ]
            if brain_mount:
                command.extend(["-v", f"{brain_mount}:/test-brain"])
            env = dict(base_env())
            if env_overrides:
                env.update(env_overrides)
            for key, value in env.items():
                command.extend(["-e", f"{key}={value}"])
            command.append(self.image_tag)
            run_command(command)
            handle = ContainerHandle(
                runtime=self,
                name=name,
                http_port=http_port,
                data_volume=data_volume,
                home_volume=home_volume,
                caddy_volume=caddy_volume,
            )
            try:
                yield handle
            finally:
                self.remove(name)
        finally:
            remove_docker_volume(data_volume)
            remove_docker_volume(home_volume)
            remove_docker_volume(caddy_volume)


class ContainerHandle:
    def __init__(
        self,
        *,
        runtime: DockerRuntime,
        name: str,
        http_port: int,
        data_volume: str,
        home_volume: str,
        caddy_volume: str,
    ) -> None:
        self.runtime = runtime
        self.name = name
        self.http_port = http_port
        self.data_volume = data_volume
        self.home_volume = home_volume
        self.caddy_volume = caddy_volume

    def logs(self) -> str:
        return self.runtime.logs(self.name)

    def exec(
        self, command: str, *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return docker_exec(self.name, command, check=check)

    def restart(self) -> None:
        run_command(["docker", "restart", self.name])

    def is_running(self) -> bool:
        return self.runtime.inspect_state(self.name, "State.Status") == "running"

    def path_exists(self, path: str) -> bool:
        return container_path_exists(self.name, path)

    def read_text(self, path: str) -> str:
        return read_container_file(self.name, path)

    def file_size(self, path: str) -> int:
        return container_file_size(self.name, path)

    def wait_for_internal_health(self, *, timeout: int = 240) -> None:
        """Wait for the internal GBrain HTTP health endpoint (127.0.0.1:3131)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_running():
                raise AssertionError(
                    f"{self.name} stopped before internal health. Logs:\n{self.logs()}"
                )
            result = run_command(["curl", "-fsS", INTERNAL_HEALTH], check=False)
            if result.returncode == 0:
                return
            time.sleep(2)
        raise AssertionError(
            f"{self.name} did not reach internal health. Logs:\n{self.logs()}"
        )

    def wait_for_https(self, *, timeout: int = 240) -> None:
        """Wait for the published HTTPS endpoint (Caddy on 3132)."""
        deadline = time.time() + timeout
        url = f"https://127.0.0.1:{self.http_port}/health"
        while time.time() < deadline:
            if not self.is_running():
                raise AssertionError(
                    f"{self.name} stopped before HTTPS healthy. Logs:\n{self.logs()}"
                )
            result = run_command(
                ["curl", "-kfsS", url], check=False
            )
            if result.returncode == 0:
                return
            time.sleep(2)
        raise AssertionError(
            f"{self.name} did not become HTTPS healthy. Logs:\n{self.logs()}"
        )
