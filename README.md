# gbrain-aio

One Unraid Compose service. One custom image. s6 runs:

1. PostgreSQL 17 + pgvector on `127.0.0.1` only
2. `gbrain serve --http` on `127.0.0.1:3131`
3. Caddy TLS on published `3132`
4. `gbrain jobs supervisor --nice`
5. `gbrain autopilot --interval 1800 --no-worker`
6. Nightly dream (02:00) and weekly doctor (Monday 06:00)

Pinned to official [garrytan/gbrain](https://github.com/garrytan/gbrain) `v0.46.14.0` (`864dec4f199f420dba1ea6c5bc72e824e09de978`).

This is **not** the patched live GBrain image. Postgres has no host port. Agents never get `DATABASE_URL`.

Self-maintain spec: [docs/self-sufficient-spec.md](docs/self-sufficient-spec.md).

## Requirements

- Docker + Compose
- A LAN IP you control
- Optional: Ollama at `http://<lan>:11434` for embeddings and openai-compat chat

## Quick start (Unraid)

1. Copy `.env.example` to `.env` (mode `0600`). Set alphanumeric `POSTGRES_PASSWORD` and `GBRAIN_ADMIN_BOOTSTRAP_TOKEN`. Set `GBRAIN_LAN_BIND` and `GBRAIN_PUBLIC_URL` to your LAN HTTPS origin.
2. Create an empty brain directory matching `BRAIN_PATH` / `SOURCE_NAME`.
3. Build and start:

```bash
cp .env.example .env
chmod 600 .env
mkdir -p /mnt/user/my-brain
docker compose --env-file .env up -d --build
docker compose --env-file .env ps
curl -fsSk https://127.0.0.1:3132/health
```

`docker compose --env-file .env port gbrain-aio 5432` must fail.

Admin: `https://<lan>:3132/admin/`  
Trust `caddy/certs/ca.pem` once. Paste the bootstrap token. Do not put the token in a URL.

## After first up

The container does first-boot itself when `/var/lib/gbrain/.gbrain/config.json` is missing:

- `gbrain init` with `ollama:embeddinggemma` @ 768d
- `git init` + initial commit if the mounted brain is not a repo
- `gbrain sources add` + `sources federate` for `SOURCE_NAME`
- file-plane `sync.repo_path` and `gbrain schema use gbrain-everything`
- model routing into `config.json` when `OLLAMA_BASE_URL` is set
- a source-scoped `sync` job after `/health` is up

No `docker exec` is required for that path.

Optional inspect:

```bash
docker compose --env-file .env exec -T gbrain-aio \
  gosu gbrain env HOME=/var/lib/gbrain gbrain sources list --json
```

## What this image does not include

- No host Postgres
- No Tailscale
- No live-lab `/repos` code-sync patch
- Official remote MCP will not honor `sources_add` `path` (security). Use host CLI for local paths, or MCP `url` for HTTPS git clones (markdown sync only)

## Configuration & troubleshooting

### Model routing (chat / expansion / embedding)

The image defaults to `ollama:embeddinggemma` @768d for embeddings. Chat and
expansion default to the upstream init models; to route all gbrain functions
through a local Ollama via the "together hijack" (set `TOGETHER_API_KEY=ollama`
and point the together provider at Ollama's OpenAI-compatible endpoint), edit
`/var/lib/gbrain/.gbrain/config.json` inside the container:

```json
{
  "chat_model": "together:deepseek-v4-flash:cloud",
  "expansion_model": "together:deepseek-v4-flash:cloud",
  "provider_base_urls": { "together": "http://<lan>:11434/v1" }
}
```

Then restart the container. The `provider_base_urls.together` entry is what
routes the "together" provider to Ollama — there is no `TOGETHER_BASE_URL`
env var, so this must live in `config.json`.

### `gbrain config set` writes to the DB plane, not the file

`gbrain config set <key> <value>` reports success but writes to the Postgres
DB plane, which is **shadowed at runtime** by the file plane
(`/var/lib/gbrain/.gbrain/config.json`). To change a model or provider setting
durably, edit `config.json` directly (as above) rather than relying on
`config set`. The CLI prints `source: file/env plane ... a DB-plane value also
exists and is shadowed at runtime` when this is happening.

### Embedding dimension mismatch on re-init

If the Postgres DB already has a `vector(1280)` embedding column (e.g. from a
prior init with a different model) and you re-init with a 768d model, gbrain
refuses with a destructive-migration warning. On a fresh test stack, wipe the
Postgres data dir (`/mnt/user/appdata/gbrain-aio/data/postgres`) and restart.
On a populated brain, follow the migration recipe gbrain prints (NULL the
embeddings, `ALTER COLUMN ... TYPE vector(768)`, rebuild the HNSW index).

### First-boot auto-init

On first boot (no `config.json` yet), the container:

1. Runs `gbrain init` with `ollama:embeddinggemma` @ 768d
2. `git init` + initial commit if `/${SOURCE_NAME}` is not a repo
3. Registers and federates `SOURCE_NAME`
4. Writes file-plane `sync.repo_path` and runs `gbrain schema use gbrain-everything`
5. Writes model routing when `OLLAMA_BASE_URL` is set
6. Enqueues a source-scoped sync after serve is up

If `config.json` already exists, init is skipped. A missing source is re-registered.

## Layout

```text
Dockerfile
compose.yaml
.env.example
rootfs/etc/cont-init.d/     bootstrap, postgres, caddy TLS
rootfs/etc/services.d/      postgres, gbrain-http, caddy, gbrain-worker, gbrain-autopilot, gbrain-dream, gbrain-doctor
rootfs/usr/local/bin/       first-boot, enqueue, dream, doctor, git push
rootfs/etc/caddy/Caddyfile
```

## License

MIT. GBrain itself remains under its upstream license.
