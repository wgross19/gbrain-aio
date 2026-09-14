# Customization Guide

How to adapt gbrain-aio without touching the image. The Unraid XML is the trigger surface: every runtime feature or variable is reachable by changing a `<Config>` entry.

## The trigger model

```text
XML <Config>  →  container env  →  01-bootstrap.sh  →  runtime.env
                                        │
                                        ├─→ gbrain-merge-file-config → config.json (file plane)
                                        └─→ s6 services (schedules, serve flags)
```

Precedence: explicit XML/env value > existing brain `config.json` > script default. Empty optional field = default behavior.

## Field reference

### Required (no defaults — install fails without them)

| Field                          | Why                                                                 |
| ------------------------------ | ------------------------------------------------------------------- |
| `POSTGRES_PASSWORD`            | Alphanumeric secret                                                 |
| `GBRAIN_LAN_BIND`              | Unraid LAN IP. Feeds the TLS cert SAN and derives the public origin |
| `GBRAIN_ADMIN_BOOTSTRAP_TOKEN` | First `/admin` login, 32+ chars                                     |

### Paths + port (always visible)

| Path          | Default                                                |
| ------------- | ------------------------------------------------------ |
| Postgres Data | `/mnt/user/appdata/gbrain-aio/data/postgres`           |
| GBrain Home   | `/mnt/user/appdata/gbrain-aio/gbrain-home`             |
| Caddy Certs   | `/mnt/user/appdata/gbrain-aio/caddy`                   |
| Brain Path    | `/mnt/user/my-brain` — must equal the share you create |
| Web UI Port   | `3132` (HTTPS via Caddy; the only published port)      |

The brain mount target is fixed at `/source/brain` and the source id is fixed to `brain` — no name coupling to configure. Future sources follow the same rule: mount under `/source/<id>`, source id = basename.

### Models (advanced, empty = proven keyless defaults)

| Field                  | Empty →                                | Set →                                           |
| ---------------------- | -------------------------------------- | ----------------------------------------------- |
| `EMBEDDING_MODEL`      | `ollama:embeddinggemma`                | any `provider:model` supported by `gbrain init` |
| `EMBEDDING_DIMENSIONS` | `768`                                  | must match the model's native width             |
| `SCHEMA_PACK`          | `gbrain-everything`                    | any schema pack id                              |
| `CHAT_PROVIDER`        | `ollama` when Ollama Base URL is set   | `together` = legacy redirect mode               |
| `CHAT_MODEL`           | `deepseek-v4-flash:cloud`              | any model your Ollama serves                    |
| `OLLAMA_BASE_URL`      | local Ollama off (keyword search only) | e.g. `http://<host>:11434/v1`                   |

### Schedules (advanced, empty = baked defaults)

| Field                      | Empty →                                              |
| -------------------------- | ---------------------------------------------------- |
| `AUTOPILOT_INTERVAL`       | 1800 seconds (upstream default is 300; ours is 1800) |
| `DREAM_AT`                 | 02:00 local                                          |
| `DOCTOR_DAY` / `DOCTOR_AT` | monday / 06:00                                       |

### Origin, TLS, Tailscale (advanced)

| Field                               | Empty →                                        |
| ----------------------------------- | ---------------------------------------------- |
| `GBRAIN_PUBLIC_URL`                 | `https://<LAN_BIND>:3132` (derived)            |
| `TS_PUBLIC_URL`                     | `auto` when per-container Tailscale is enabled |
| `CERT_EXTRA_DNS` / `CERT_EXTRA_IPS` | base SAN only (+ auto-derived tailnet entries) |

Single-origin rule: one input (`GBRAIN_LAN_BIND`) drives both the cert identity and the OAuth origin. Override `GBRAIN_PUBLIC_URL` only for a custom port, DNS name, reverse proxy, or Tailscale origin.

**Tailscale:** enable Unraid's per-container Tailscale toggle → authenticate in the container's Tailscale panel → the next boot derives the origin from the MagicDNS name and adds it (plus the 100.x IP) to the cert SAN automatically. Two boots total; no name is ever typed into the template. `TS_PUBLIC_URL=off` keeps the LAN origin.

### Keys (advanced, masked)

Key fields exist **only** for providers whose recipes require keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `VOYAGE_API_KEY`. Empty = provider off.

No key field for Ollama (its recipe requires none) or Together (script-managed placeholder in legacy mode). Hosted provider endpoints are hardcoded upstream in the recipe registry; only local endpoints (`OLLAMA_BASE_URL`) are user-fillable.

### Universal passthrough

`GBRAIN_EXTRA_CONFIG` — comma-separated `key=value` pairs for **any** gbrain config key. Applied after built-in defaults; explicit entries win. `provider_base_urls` is refused (mount safety).

Worked examples:

```text
# Pin the dream drift judge to the local model (removes the Anthropic requirement)
GBRAIN_EXTRA_CONFIG=models.drift=ollama:deepseek-v4-flash:cloud

# Enable image OCR on import
GBRAIN_EXTRA_CONFIG=GBRAIN_EMBEDDING_IMAGE_OCR=true
```

After adding an expansion-capable key, optionally switch search mode:

```text
GBRAIN_EXTRA_CONFIG=search.mode=tokenmax
```

### First-boot behavior: fresh vs preexisting repos

The XML brain path decides everything at first boot:

- **Fresh/empty folder:** the chain git-inits it, registers the source, syncs, and runs the one-time graph backfill (no-op on an empty graph — auto-link populates as pages are written).
- **Preexisting repo (existing share):** same chain, plus the idempotent backfill (`extract links --source db`, `extract timeline --source db`) runs **after the first sync completes**, populating `links` + `timeline_entries` per upstream docs Step 4.5. No manual exec.
- **Existing install (config.json present):** init, markers, and one-shots are skipped entirely; only source re-registration/federation is checked.

Verification receipts land in `/var/lib/gbrain/.gbrain/`: `first-doctor.done`, `graph-backfill.done`, `last-doctor.json` (also written by the weekly doctor).

## Files You Will Almost Always Touch

- `Dockerfile` (upstream pin + Bun/base ARGs)
- `gbrain-aio.xml` (trigger surface)
- `rootfs/etc/cont-init.d/01-bootstrap.sh` (derivation + runtime.env)
- `rootfs/usr/local/bin/gbrain-merge-file-config` (config.json writer)
- `rootfs/etc/services.d/*/run` (s6 services)
- `tests/test_xml_env_contract.py` (parity contract — extend when adding a field)

## Adding a new trigger field (checklist)

1. Read it in `01-bootstrap.sh` with a generic default; add it to the `runtime.env` heredoc.
2. Consume it in the merge engine (config key) or the relevant s6 script (schedule/flag).
3. Add the XML `<Config Type="Variable">` entry (advanced unless first-boot-critical).
4. Add it to compose.yaml's environment map.
5. The contract test fails if any layer is missed — run `pytest tests/test_xml_env_contract.py`.

## CI and Publishing

The central `aio-fleet` control plane publishes from `main` once registry and GitHub App secrets are configured. Before enabling: `validate-repo`, `pytest tests/ -m integration`, all repository secrets, XML/icon/package names confirmed, upstream monitor matches the stable channel, `CHANGELOG.md` and the XML `<Changes>` block agree.

## Security invariants (do not relax in the XML)

- Postgres listens on 127.0.0.1 inside the container. Never publish 5432.
- Agents never receive `DATABASE_URL`; MCP is the only agent path.
- The only published port is 3132 (HTTPS via Caddy).
- `provider_base_urls` never passes through `GBRAIN_EXTRA_CONFIG`.
