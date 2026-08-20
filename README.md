# gbrain-aio

Run [GBrain](https://github.com/garrytan/gbrain) as one container — a hosted knowledge base **and MCP server** for AI agents. No Postgres setup, no reverse proxy, no worker to manage. Install the [Unraid template](gbrain-aio.xml), set a few values, and it's ready on first boot.

## What this is for

GBrain is a self-hosted second brain. Point an AI agent harness at it and it becomes your persistent memory and knowledge store. **`gbrain-aio` packages GBrain so you can run it as a hosted GBrain MCP server** for agent frameworks like **Hermes Agent, OpenClaw, Codex**, and any harness that speaks MCP. Agents connect to it over HTTPS and get queryable memory — not a raw database.

## The problem it solves

GBrain will not just run. A real deployment needs five things bolted together:

1. **PostgreSQL + pgvector** — all data + vector embeddings live here
2. **A reverse proxy** — the admin UI and MCP endpoint need HTTPS
3. **A job worker** — background maintenance, nightly "dream," weekly doctor
4. **Embedding + chat models** — Ollama for embeddings, plus a chat model
5. **A build** — GBrain has no official Docker image

Wiring those by hand on Unraid is the whole problem. `gbrain-aio` does it for you.

## What you get in this container

**GBrain, running with its full default setup** — the reason you're here:

- **The GBrain HTTP + MCP server** on `127.0.0.1:3131`, fronted by Caddy TLS on `3132`
- **The job supervisor** — runs background work
- **Autopilot** — self-maintenance every 30 min
- **Nightly dream** (02:00) and **weekly doctor** (Monday 06:00)
- **Schema + sources pre-wired** — `gbrain-everything`, your source federated, sync queued

Plus the supporting infrastructure, all in the same container:

- **PostgreSQL 17 + pgvector** — loopback only, never published
- **Caddy TLS** — the public face
- **Embedding support** — `ollama:embeddinggemma` @ 768d

Pinned to official `garrytan/gbrain` releases. Agents connect only through HTTPS — Postgres is never handed to them.

## Embeddings & models

**A local Ollama is required** for embedding — the container indexes every document into vector space and needs `ollama:embeddinggemma` @ 768d to do it. **Only `embeddinggemma` is supported today.**

- **Ollama (required)** — the container uses `ollama:embeddinggemma` @ 768d. Point `OLLAMA_BASE_URL` at your local Ollama (e.g. `http://<lan>:11434`). A GPU is strongly recommended — large brains index and query much faster with GPU-backed embeddings.
- **Chat / expansion models** — add an Anthropic, OpenAI, Gemini, DeepSeek, Groq, or Voyage API key, or route through the same Ollama instance. Leave chat model fields empty to keep defaults.

## First boot does the setup for you

Start empty, and the container:

1. Runs `gbrain init` with `ollama:embeddinggemma` @ 768d
2. Git-inits the brain if it is not a repo
3. Registers and federates your source
4. Writes sync paths and enables the `gbrain-everything` schema
5. Sets model routing for Ollama
6. Enqueues a sync once `/health` is up

No `docker exec` required.

## Install (Unraid, ~3 min)

1. Install the template
2. Set `POSTGRES_PASSWORD` (alphanumeric) and `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` (32+ chars)
3. Leave default appdata paths, set your LAN HTTPS origin, Apply
4. Wait for init
5. Open `https://<lan>:3132/admin/`, paste the token, trust the generated CA once

## Requirements

- Unraid (with Community Applications) or any Docker host
- A LAN IP you control
- **Ollama** at `http://<lan>:11434` for embeddings (GPU recommended)

## Not included

- No host PostgreSQL — everything runs inside the container

## Persistence

Back up the brain repo, Postgres data, and Caddy certs under `/mnt/user/appdata/gbrain-aio/` if you care about the instance.

## License

MIT. GBrain under its own upstream license.
