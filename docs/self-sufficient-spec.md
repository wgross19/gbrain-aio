# Spec — Self-sufficient gbrain-aio

Status: **implemented as PR** (2026-08-18). Open review: <https://github.com/wgross19/gbrain-aio/pull/2> (`feat/self-sufficient-maintain`). Not merged. GHCR not published. Live gbrain-aio not restarted.

Goal: a new Unraid user fills the template, clicks Apply, and the container starts healthy, indexes the mounted brain, and keeps itself fresh. No Hermes cron and no `docker exec` required for default maintenance.

## Product rules

1. Generic template only. No homelab IPs, repo names, or operator-specific comments.
2. Empty optional fields mean off.
3. File-plane `config.json` is canonical. Do not rely on `gbrain config set` for durable defaults (DB plane is shadowed).
4. Chat-gated phases run only when a chat model is configured. Keyless installs stay free.
5. Never force-push git. Never block HTTPS serve on a long first sync.

## In-container schedule

| Job             | When                                   | Command / process                                                                |
| --------------- | -------------------------------------- | -------------------------------------------------------------------------------- |
| Jobs supervisor | always                                 | existing `gbrain-worker` (`gbrain jobs supervisor --nice`)                       |
| Autopilot       | every **30 min**                       | s6 longrun: `gbrain autopilot --repo /${SOURCE_NAME} --no-worker`                |
| Dream           | nightly **02:00**                      | `gbrain dream --dir /${SOURCE_NAME}` (wait on cycle lock)                        |
| Doctor          | weekly **Mon 06:00**                   | `gbrain doctor --json` → `~/.gbrain/last-doctor.json`; remediate only if cap set |
| Git push        | after a **successful autopilot-cycle** | only if `BRAIN_GIT_PUSH_URL` is set                                              |

Autopilot interval is 30 minutes (not upstream’s 5-minute default). Dream and doctor times match the official gbrain cron guide. Dream waits if an autopilot-cycle holds the lock.

## 1. First-boot init

Trigger: `/var/lib/gbrain/.gbrain/config.json` is missing.

Do this, then start or continue serve. Do **not** block HTTP on indexing.

1. Wait for Postgres + `vector` extension.
2. `gbrain init --url $DATABASE_URL --embedding-model ollama:embeddinggemma --embedding-dimensions 768 --non-interactive`.
3. If `/${SOURCE_NAME}` exists and is not a git repo: `git init` and an initial commit as uid 99 / gid users.
4. `git config --global --add safe.directory /${SOURCE_NAME}`.
5. `gbrain sources add ${SOURCE_NAME} --path /${SOURCE_NAME} --name ${SOURCE_NAME}` and federate the source.
6. Set file-plane `sync.repo_path` to `/${SOURCE_NAME}`.
7. `gbrain schema use gbrain-everything`.
8. Write model routing (section 4) into `config.json`.
9. Enqueue a source-scoped **sync** job for the worker after serve is up.

Idempotent: if `config.json` exists, skip init. Re-register/federate only if the source is missing.

## 2. Jobs supervisor

Keep the current `gbrain-worker` s6 service. No behavior change.

Autopilot must use `--no-worker` so it does not spawn a second worker.

## 3. Autopilot s6 service

New s6 longrun. Do **not** use `gbrain autopilot --install` (that writes crontab; this image has no cron daemon).

Start with the container. Wait for Postgres and `config.json`. Then:

```text
gbrain autopilot --repo /${SOURCE_NAME} --interval 1800 --no-worker
```

Always run: freshness sync, embed backfill, lint, backlinks, orphans, 60-min full `autopilot-cycle`.

Chat-gated (only if a chat model is configured): `extract_atoms` drain, `propose_takes`.

## 4. Default model wiring

Template field for the chat model name. Default: `deepseek-v4-flash:cloud`.

No prefilled LAN IP. `OLLAMA_BASE_URL` empty = keyword/embed-or-skip chat.

If `OLLAMA_BASE_URL` is set, first-boot writes file-plane:

- `embedding_model`: `ollama:embeddinggemma`
- `embedding_dimensions`: `768`
- `chat_model` / `expansion_model`: `together:<CHAT_MODEL>`
- `provider_base_urls.together`: `${OLLAMA_BASE_URL}/v1` (append `/v1` if missing)
- `TOGETHER_API_KEY`: `ollama` if unset

## 5. Dream

Nightly 02:00: `gbrain dream --dir /${SOURCE_NAME}`.

Wait on the cycle lock. Do not add a second overlapping cycle.

Shared phases (no chat required): lint, backlinks, sync, extract, embed, orphans.

Chat-gated when a chat model exists: synthesize (no-ops without corpus), patterns (needs reflections), extract_atoms, synthesize_concepts, propose_takes, grade_takes, drift, enrich_thin, conversation_facts_backfill, schema-suggest LLM refine, conversation_parser LLM fallback.

Synthesize still no-ops until a later `.txt` corpus hook. That hook is out of this implementation.

## 6. Optional git push

Template (generic names only):

- `BRAIN_GIT_PUSH_URL` — empty = off
- `BRAIN_GIT_PUSH_TOKEN` — masked; never put the token in the remote URL string that `git remote -v` prints

If URL is set: after each **successful** `autopilot-cycle`, commit if dirty (generic message) and `git push` the mounted brain. No force-push. Do not push on the 30-min freshness tick.

Document: do not point a test copy at a live canonical remote.

## 7. Weekly doctor

Monday 06:00.

Always: `gbrain doctor --json` written to `/var/lib/gbrain/.gbrain/last-doctor.json`.

`DOCTOR_REMEDIATE_MAX_USD`:

- empty = exam only
- a number = `gbrain doctor --remediate --max-usd N` for **that run’s estimate**

If estimated plan cost exceeds N, abort and submit nothing. Not a weekly wallet. Official step default cited in upstream is $5.

## Feature flags

### On if a chat model exists

| Flag                                        | When                    | Why                                               |
| ------------------------------------------- | ----------------------- | ------------------------------------------------- |
| `dream.drift.enabled`                       | nightly dream           | Stale-take judge                                  |
| `cycle.enrich_thin.enabled`                 | nightly dream           | Person/company stubs; 3/source; $1/source $5/tick |
| `cycle.conversation_facts_backfill.enabled` | nightly dream           | Facts from conversation pages; no-op if none      |
| `conversation_parser.llm_fallback_enabled`  | always when chat exists | Regex first; LLM if parse is weak                 |
| `cycle.grade_takes.auto_resolve.enabled`    | nightly grade           | Auto-apply verdicts at confidence ≥ 0.95          |
| extract_atoms / propose_takes               | autopilot + dream       | Already gated on chat                             |

### Always on

- `mcp.publish_advisor` — read-only MCP advisor
- `mcp.publish_skills` — already true

### Template toggle, default off

| Env / field             | Flag                                          |
| ----------------------- | --------------------------------------------- |
| `SKILLOPT_ENABLED`      | `cycle.skillopt.enabled`                      |
| `NIGHTLY_QUALITY_PROBE` | `autopilot.nightly_quality_probe.enabled`     |
| `PARSER_PROBE_ENABLED`  | `autopilot.conversation_parser_probe.enabled` |

### Stay off

- `dream.auto_think` — no default questions
- `search.unified_multimodal` — needs a multimodal reindex project
- `search.mode=tokenmax` and `spend.posture=tokenmax` — not the simple default
- `search.reranker.enabled` — stays with conservative mode (off)

## Out of the default container

| Item                         | Why                          | Later hook                                                                            |
| ---------------------------- | ---------------------------- | ------------------------------------------------------------------------------------- |
| Session synthesize corpus    | Needs Hermes `.txt` exporter | Mount corpus dir; set `dream.synthesize.session_corpus_dir`. Never mount Hermes JSONL |
| Gmail / X / Readwise runners | Image has the recipe only    | Hermes collectors write into the brain                                                |
| Live Cortex backup           | Not this container’s job     | Optional push is the **mounted** brain only                                           |

## LLM vs no-LLM (for operators)

No model: lint, backlinks, sync, extract (links/timeline), extract_facts, extract_takes, resolve_symbol_edges, emotional weight, orphans, purge, doctor exam, git push.

Embedding only: embed / stale embed, first sync backfill.

Chat LLM: synthesize, patterns, extract_atoms, synthesize_concepts, propose_takes, grade_takes, drift, enrich_thin, conversation_facts_backfill, skillopt, parser LLM fallback, some remediate jobs, query expansion (tokenmax only — not in this spec).

## New / changed template fields (generic)

Always shown or already present: `SOURCE_NAME`, brain path, `OLLAMA_BASE_URL`, chat model name (default `deepseek-v4-flash:cloud`).

Advanced / optional:

- `BRAIN_GIT_PUSH_URL`
- `BRAIN_GIT_PUSH_TOKEN` (masked)
- `DOCTOR_REMEDIATE_MAX_USD`
- `SKILLOPT_ENABLED`
- `NIGHTLY_QUALITY_PROBE`
- `PARSER_PROBE_ENABLED`

## Acceptance

A fresh container with only required Unraid fields:

1. `/health` is ok without a manual exec.
2. Source is registered, federated, and a sync job is queued.
3. Autopilot process is running (`--repo` + `--no-worker`).
4. After first sync, `query` returns pages from the mounted brain.
5. Dream and doctor have been scheduled inside the container (s6/timer), not Hermes.
6. Keyless install makes no chat calls.
7. Empty optional URL/budget/toggles change no behavior.

## Non-goals for the first implementation PR

- Hermes session exporter
- Collector sidecars
- Multimodal reindex
- Changing the published GHCR image until this PR is merged and publish is approved
