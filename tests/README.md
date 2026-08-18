# Tests

Lifecycle and policy tests for the gbrain-aio image.

## Layout

- `tests/test_dockerfile_policy.py` — static checks (no Docker). Verifies the
  security invariants in `compose.yaml`, `Dockerfile`, and `rootfs/`: Postgres
  is never published, no agent `DATABASE_URL`, only 3132 is published, the
  cert is reused, the worker is supervised, and the upstream SHA is pinned.
- `tests/integration/test_container_runtime.py` — Docker-backed lifecycle
  tests. Covers the 11 required checks: image starts, Postgres initializes,
  GBrain starts, Caddy starts, external health works, Postgres is not
  published, persistent data survives restart, required env is enforced,
  the bundled cert is generated and reused, the mounted brain path behaves,
  and the worker does not silently fail.

## Run

Static policy tests (no Docker):

```bash
python -m pytest tests/test_dockerfile_policy.py
```

Docker-backed integration tests (requires Docker + the image build):

```bash
python -m pytest tests/integration -m integration
```

To run against a prebuilt image instead of building:

```bash
AIO_PYTEST_USE_PREBUILT_IMAGE=true python -m pytest tests/integration -m integration
```

## Notes

- Integration tests build the image as `gbrain-aio:pytest` unless
  `AIO_PYTEST_USE_PREBUILT_IMAGE=true` is set.
- The tests use ephemeral Docker volumes and remove the container + volumes
  after each run. Nothing persists.
- `POSTGRES_PASSWORD` and `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` in the test env are
  throwaway alphanumeric values, never real secrets.
