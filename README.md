# gbrain-aio

One Unraid Compose service. One custom image. s6 runs:

1. PostgreSQL 17 + pgvector on `127.0.0.1` only
2. `gbrain serve --http` on `127.0.0.1:3131`
3. Caddy TLS on published `3132`
4. `gbrain jobs supervisor --nice`

Pinned to official [garrytan/gbrain](https://github.com/garrytan/gbrain) `v0.46.14.0` (`864dec4f199f420dba1ea6c5bc72e824e09de978`).

This is **not** the patched live GBrain image. Postgres has no host port. Agents never get `DATABASE_URL`.

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

## After first up (inside the container)

```bash
docker compose --env-file .env exec -T gbrain-aio \
  gosu gbrain env HOME=/var/lib/gbrain gbrain init --help
```

Typical first-brain steps:

- `gbrain init` with `--schema-pack gbrain-everything` (or `gbrain-base-v2`)
- `gbrain sources add` for the mounted brain path
- `gbrain schema use gbrain-everything` if you want all three lenses
- `gbrain auth register-client` for OAuth MCP

`gbrain config set schema_pack` is not a valid key on 0.46.14. Use `gbrain schema use`.

## What this image does not include

- No host Postgres
- No Tailscale
- No live-lab `/repos` code-sync patch
- Official remote MCP will not honor `sources_add` `path` (security). Use host CLI for local paths, or MCP `url` for HTTPS git clones (markdown sync only)

## Layout

```
Dockerfile
compose.yaml
.env.example
rootfs/etc/cont-init.d/     bootstrap, postgres, caddy TLS
rootfs/etc/services.d/      postgres, gbrain-http, caddy, gbrain-worker
rootfs/etc/caddy/Caddyfile
```

## License

MIT. GBrain itself remains under its upstream license.
