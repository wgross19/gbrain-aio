# Security Policy

## Reporting a Vulnerability

This project wraps the official [garrytan/gbrain](https://github.com/garrytan/gbrain) application. Security issues in GBrain itself should be reported to the upstream project.

For issues specific to this Unraid AIO packaging, open a private vulnerability report on GitHub:

https://github.com/wgross19/gbrain-vanilla-aio/security/advisories/new

## Security Invariants

These are enforced by the static policy tests in `tests/test_dockerfile_policy.py` and must never be relaxed:

- **Postgres is never published.** It listens on `127.0.0.1:5432` inside the container only. There is no host port `5432`.
- **Agents never receive `DATABASE_URL`.** The only agent path is the MCP endpoint. `DATABASE_URL` is written only to `/var/lib/gbrain/runtime.env` inside the container by the bootstrap script.
- **Only port `3132` is published** (HTTPS via Caddy). No other host port mapping is allowed.
- **The container is not privileged.** `no-new-privileges:true`. No host Docker socket.
- **Secrets are alphanumeric only.** `POSTGRES_PASSWORD` and `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` must be `A-Za-z0-9`. The bootstrap script rejects anything else.
- **The upstream GBrain commit is pinned.** The Dockerfile checks out a specific SHA and verifies `rev-parse HEAD` matches before building.

## Supported Versions

| Version | Supported |
|---|---|
| `v0.46.14.0` (pinned) | Yes |

## Deployment Notes

- Never publish `5432`.
- Never give an agent `DATABASE_URL`.
- Keep `.env` mode `0600` and never commit it.
- Trust the generated CA once on any phone or WebView that opens `/admin`.
