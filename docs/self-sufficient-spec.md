# Spec — Self-sufficient gbrain-aio

Status: **implemented on main** (2026-09-11, commit `48bebe3`). Supersedes the original PR #2 design. XML-trigger contract enforced by `tests/test_xml_env_contract.py`.

Goal: a new Unraid user fills the template, clicks Apply, and the container starts healthy, indexes the mounted brain (fresh OR preexisting), and keeps itself fresh. No Hermes cron and no `docker exec` required for default maintenance.

## Product rules

1. Generic template only. No homelab IPs, repo names, or operator-specific comments.
2. Empty optional fields mean off; script defaults apply when a field is empty.
3. File-plane `config.json` is canonical. Do not rely on `gbrain config set` for durable defaults (DB plane is shadowed).
4. Chat-gated phases run only when a chat model is configured. Keyless installs stay free.
5. Never force-push git. Never block HTTPS serve on a long first sync.
6. One origin, one input: `GBRAIN_LAN_BIND` is the only required network value; `GBRAIN_PUBLIC_URL` derives from it unless explicitly overridden.
7. Key fields exist only for providers whose recipes require keys. Ollama needs no key; its key value is script-managed.
8. Provider base URLs are hardcoded upstream for hosted providers. Local endpoints (`OLLAMA_BASE_URL`) are the only user-fillable endpoint. `provider_base_urls` is denylisted from `GBRAIN_EXTRA_CONFIG` (mount safety).

## Runtime trigger model (XML → env → runtime)

Unraid XML `<Config Type="Variable">` → container env → `01-bootstrap.sh` (derivation, validation, `runtime.env`) → file-plane `config.json` (via `gbrain-merge-file-config`) + s6 services.

Precedence: explicit env (XML) > existing `config.json` values on existing brains > script defaults. `GBRAIN_EXTRA_CONFIG` entries apply last inside the merge engine and beat the built-in flags for the same key (but known non-extra flags keep their defaults when not passed).

## In-container schedule

| Job             | When                                                      | Command / process                                                                |
| --------------- | --------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Jobs supervisor | always                                                    | existing `gbrain-worker` (`gbrain jobs supervisor --nice`)                       |
| Autopilot       | every `AUTOPILOT_INTERVAL` (default 1800s)                | s6 longrun: `gbrain autopilot --repo /${SOURCE_NAME} --no-worker`                |
| Dream           | nightly at `DREAM_AT` (default 02:00)                     | `gbrain dream --dir /${SOURCE_NAME}` (wait on cycle lock)                        |
| Doctor          | weekly on `DOCTOR_DAY` at `DOCTOR_AT` (default Mon 06:00) | `gbrain doctor --json` → `~/.gbrain/last-doctor.json`; remediate only if cap set |
| Git push        | after a **successful autopilot-cycle**                    | only if `BRAIN_GIT_PUSH_URL` is set                                              |

Autopilot interval defaults to 30 minutes (upstream's default is 5 minutes — a documented deviation, now overridable). Dream and doctor defaults match the official gbrain cron guide. Dream waits if an autopilot-cycle holds the lock.

## 1. First-boot init

Trigger: `/var/lib/gbrain/.gbrain/config.json` is missing.

Do this, then start or continue serve. Do **not** block HTTP on indexing.

1. Wait for Postgres + `vector` extension.
2. `gbrain init --url $DATABASE_URL --embedding-model ${EMBEDDING_MODEL:-ollama:embeddinggemma} --embedding-dimensions ${EMBEDDING_DIMENSIONS:-768} --non-interactive`.
3. If `/${SOURCE_NAME}` exists and is not a git repo: `git init` and an initial commit as uid 99 / gid users.
4. `git config --global --add safe.directory /${SOURCE_NAME}`.
5. `gbrain sources add ${SOURCE_NAME} --path /${SOURCE_NAME} --name ${SOURCE_NAME}` and federate the source.
6. Set file-plane `sync.repo_path` to `/${SOURCE_NAME}`.
7. `gbrain schema use ${SCHEMA_PACK:-gbrain-everything}`.
8. Write model routing (section 4) + `GBRAIN_EXTRA_CONFIG` into `config.json`.
9. Mark `need-first-sync`, `need-first-doctor`, `need-graph-backfill`; enqueue a source-scoped **sync** job for the worker after serve is up; launch `gbrain-first-sync-post` detached.

Idempotent: if `config.json` exists, skip init. Re-register/federate only if the source is missing. Markers are only created on the init path, so existing installs never re-run one-shots.

## 1b. First-boot one-shots (docs Steps 3/4.5/9)

`gbrain-first-sync-post` (detached from `gbrain-http/run`):

1. **First-boot doctor receipt**: one `gbrain doctor --json` → `last-doctor.json` once serve is healthy. Exam only.
2. **Graph backfill**: waits for the recorded first-sync job to finish (or a 120s grace), then runs `gbrain extract links --source db --source-id ${SOURCE_NAME}` and `gbrain extract timeline --source db --source-id ${SOURCE_NAME}`. Both upstream commands are idempotent:
   - **Preexisting repo** (XML brain path mapped to an existing share): populates `links` + `timeline_entries` per upstream Step 4.5 without waiting for pages to change.
   - **Fresh repo**: converges as no-ops; auto-link populates as pages are written.
3. Marker-gated: runs once per brain. Restarts are no-ops. Failed sync → warning logged, markers persist for the next boot.

## 2. Jobs supervisor

Keep the current `gbrain-worker` s6 service. No behavior change.

Autopilot must use `--no-worker` so it does not spawn a second worker.

## 3. Autopilot s6 service

Start with the container. Wait for Postgres and `config.json`. Then:

```text
gbrain autopilot --repo /${SOURCE_NAME} --interval ${AUTOPILOT_INTERVAL:-1800} --no-worker
```

Do **not** use `gbrain autopilot --install` (that writes crontab; this image has no cron daemon).

## 4. Default model wiring

Fields: `CHAT_MODEL` (default `deepseek-v4-flash:cloud`), `CHAT_PROVIDER` (empty/`ollama`/`together`), `EMBEDDING_MODEL` (default `ollama:embeddinggemma`), `EMBEDDING_DIMENSIONS` (default 768), `SCHEMA_PACK` (default `gbrain-everything`).

No prefilled LAN IP. `OLLAMA_BASE_URL` empty = keyword/embed-or-skip chat.

If `OLLAMA_BASE_URL` is set, first-boot writes file-plane:

- `embedding_model` / `embedding_dimensions` from the knobs (defaults preserve the keyless set)
- `chat_model` / `expansion_model`:
  - `CHAT_PROVIDER` empty or `ollama` → `ollama:<CHAT_MODEL>` (raw recipe; `OLLAMA_BASE_URL` is read by the runtime directly)
  - `CHAT_PROVIDER=together` (legacy) → `together:<CHAT_MODEL>` + `provider_base_urls.together = ${OLLAMA_BASE_URL}/v1`; `TOGETHER_API_KEY` placeholder `ollama` is script-managed, not a template field

Vendor key fields exist only for key-required recipes (Anthropic, OpenAI, Gemini, DeepSeek, Groq, OpenRouter, Voyage). Keys pass through container env to the gateway.

`GBRAIN_EXTRA_CONFIG` (comma-separated `key=value`) sets any gbrain config key after defaults. `provider_base_urls` and invalid keys are rejected. Worked example — drift judge on the local model:

```text
GBRAIN_EXTRA_CONFIG=models.drift=ollama:deepseek-v4-flash:cloud
```

Search mode note: keyless installs resolve `search.mode=conservative` (upstream default without an expansion-capable key). To switch after adding a key: `GBRAIN_EXTRA_CONFIG=search.mode=tokenmax`.

## 5. Dream

Nightly at `DREAM_AT` (default 02:00): `gbrain dream --dir /${SOURCE_NAME}`.

Wait on the cycle lock. Do not add a second overlapping cycle.

## 6. Optional git push

`BRAIN_GIT_PUSH_URL` (empty = off), `BRAIN_GIT_PUSH_TOKEN` (masked; never in the remote URL). After each **successful** `autopilot-cycle`: commit if dirty and push. No force-push. Do not push on freshness ticks. Do not point a test copy at a live canonical remote.

## 7. Doctor

Weekly on `DOCTOR_DAY` at `DOCTOR_AT` (default Mon 06:00): `gbrain doctor --json` → `last-doctor.json`. Plus the one-time first-boot receipt (section 1b).

`DOCTOR_REMEDIATE_MAX_USD`: empty = exam only; a number = `--remediate --max-usd N` for that run's estimate.

## 8. Single origin + Tailscale

Required: `GBRAIN_LAN_BIND` (Unraid LAN IP). It feeds the cert SAN and derives the origin.

`GBRAIN_PUBLIC_URL`: empty = `https://<LAN_BIND>:3132`. Override only for custom port, DNS-name access, reverse proxy, or a Tailscale origin.

Per-container Tailscale (Unraid 7 toggle): when a tailscaled socket exists, bootstrap reads `tailscale status` and derives `GBRAIN_PUBLIC_URL` from the MagicDNS name, and the cert script adds the MagicDNS name + 100.x IP to the SAN. Policy via `TS_PUBLIC_URL`: `auto` (default when integration detected) | `off` | explicit URL. Socket not ready at boot → bounded wait, LAN fallback, next boot converges. Manual fallback for exotic names: `CERT_EXTRA_DNS` / `CERT_EXTRA_IPS`. The cert re-mints only when its SAN lacks a required entry (idempotent; `/config/caddy` persists).

## Feature flags

### On if a chat model exists

| Flag                                        | When                    | Why                                 |
| ------------------------------------------- | ----------------------- | ----------------------------------- |
| `dream.drift.enabled`                       | nightly dream           | Stale-take judge                    |
| `cycle.enrich_thin.enabled`                 | nightly dream           | Person/company stubs                |
| `cycle.conversation_facts_backfill.enabled` | nightly dream           | Facts from conversation pages       |
| `conversation_parser.llm_fallback_enabled`  | always when chat exists | Regex first; LLM if parse is weak   |
| `cycle.grade_takes.auto_resolve.enabled`    | nightly grade           | Auto-apply high-confidence verdicts |

### Always on

- `mcp.publish_advisor` — read-only MCP advisor
- `mcp.publish_skills` — skills catalog over MCP

### Template toggle, default off

| Env / field             | Flag                                          |
| ----------------------- | --------------------------------------------- |
| `SKILLOPT_ENABLED`      | `cycle.skillopt.enabled`                      |
| `NIGHTLY_QUALITY_PROBE` | `autopilot.nightly_quality_probe.enabled`     |
| `PARSER_PROBE_ENABLED`  | `autopilot.conversation_parser_probe.enabled` |

### Stay off

- `dream.auto_think` — no default questions
- `search.unified_multimodal` — needs a multimodal reindex project
- `search.mode=tokenmax` / `spend.posture=tokenmax` — not the simple default (see §4 note)
- `search.reranker.enabled` — stays off with conservative mode

## Out of the default container

| Item                         | Why                       | Later hook                                                                                            |
| ---------------------------- | ------------------------- | ----------------------------------------------------------------------------------------------------- |
| Session synthesize corpus    | Needs a `.txt` exporter   | Mount corpus dir; set `dream.synthesize.session_corpus_dir` via Extra Config. Never mount agent JSONL |
| Gmail / X / Readwise runners | Image has the recipe only | External collectors write into the brain                                                              |
| Live Cortex backup           | Not this container's job  | Optional push is the **mounted** brain only                                                           |

## Template fields (trigger surface)

Required (no defaults): `POSTGRES_PASSWORD`, `GBRAIN_LAN_BIND`, `GBRAIN_ADMIN_BOOTSTRAP_TOKEN`. Paths + port always visible.

Advanced / optional (empty = off or script default): `GBRAIN_PUBLIC_URL` (derived), `CHAT_PROVIDER`, `CHAT_MODEL`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `SCHEMA_PACK`, `AUTOPILOT_INTERVAL`, `DREAM_AT`, `DOCTOR_DAY`, `DOCTOR_AT`, `TS_PUBLIC_URL`, `CERT_EXTRA_DNS`, `CERT_EXTRA_IPS`, `GBRAIN_EXTRA_CONFIG`, `OLLAMA_BASE_URL`, `SOURCE_NAME`, `BRAIN_UID/GID`, `BRAIN_GIT_PUSH_URL/TOKEN`, `DOCTOR_REMEDIATE_MAX_USD`, `SKILLOPT_ENABLED`, `NIGHTLY_QUALITY_PROBE`, `PARSER_PROBE_ENABLED`, vendor keys (Anthropic, OpenAI, Gemini, DeepSeek, Groq, OpenRouter, Voyage).

Key contract: no key fields for providers whose recipes need no key (ollama); `TOGETHER_API_KEY` is script-managed.

## Acceptance

A fresh container with only required Unraid fields:

1. `/health` is ok without a manual exec.
2. Source is registered, federated, and a sync job is queued.
3. `last-doctor.json` exists (first-boot receipt) without manual exec.
4. After first sync, the graph backfill has run once (marker consumed) and `query` returns pages from the mounted brain.
5. Dream and doctor have been scheduled inside the container (s6 timers), not Hermes.
6. Keyless install makes no chat calls; `chat_model` rides the ollama recipe.
7. Empty optional URL/budget/toggles change no behavior.
8. `GBRAIN_PUBLIC_URL` parses back to `GBRAIN_LAN_BIND` unless an override is set.

## Non-goals

- Hermes session exporter
- Collector sidecars
- Multimodal reindex
- Editing upstream provider recipes inside the image (they are code, not config)
