# Power User Guide

Advanced configuration for the gbrain-aio container.

## Model Configuration

The container routes embeddings and openai-compat chat through `OLLAMA_BASE_URL`. The default points at a local Ollama.

- Embeddings: `ollama:embeddinggemma:latest` @ 768d.
- Chat, expansion, dream, think: openai-compat recipe URL-overridden to `OLLAMA_BASE_URL` (local Ollama `/v1`).

To change the model, set the model pins inside the container after first boot:

```bash
docker compose --env-file .env exec -T gbrain-aio gosu gbrain env HOME=/var/lib/gbrain gbrain models set ...
```

## OAuth and MCP

The admin dashboard and MCP share the HTTPS origin `https://<lan>:3132`.

- Register a scoped OAuth client for Hermes or another agent.
- The client must be scoped to the mounted brain source (`SOURCE_NAME`).
- Agents connect only through MCP. They never receive `DATABASE_URL`.

## Brain Repo

- `BRAIN_PATH` is the host path to the brain markdown repo. It must be a git repo.
- `SOURCE_NAME` must equal `basename(BRAIN_PATH)`.
- `BRAIN_UID` / `BRAIN_GID` control the bind-mount ownership. Unraid default is `99:100`.

## TLS

- Caddy mints a persistent self-signed cert on first boot.
- The CA is written to `caddy/certs/ca.pem`.
- Trust it once on any phone or WebView that opens `/admin`.
- The cert is reused across restarts; it is not rotated on every start.

## Maintenance

In-container s6, not Hermes cron:

- Autopilot every 30 minutes: `gbrain autopilot --repo /${SOURCE_NAME} --interval 1800 --no-worker`
- Nightly 02:00: `gbrain dream --dir /${SOURCE_NAME}` (waits if a cycle holds the lock)
- Weekly Monday 06:00: `gbrain doctor --json` → `~/.gbrain/last-doctor.json`
- Optional `DOCTOR_REMEDIATE_MAX_USD` runs `gbrain doctor --remediate --max-usd N` for that run only
- Optional `BRAIN_GIT_PUSH_URL` pushes the mounted brain after a successful `autopilot-cycle` (no force-push). Do not point a test copy at a live canonical remote.

`self_upgrade.mode` should stay `notify`, never `auto`.
