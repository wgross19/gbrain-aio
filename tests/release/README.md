# Release qualification suite (`tests/release`)

GBrain-only release gate for the `gbrain-aio` image. Candidate CI runs
exactly `python -m pytest -q tests/release` from the repo root on a GH
ephemeral native runner.

## Contract (strict, fail-fast)

- Immutable digest-pinned inputs: `AIO_CANDIDATE_IMAGE`, `AIO_PREVIOUS_IMAGE`
  must be `name@sha256:<64 lowercase hex>` refs; mutable tags (`:latest`,
  `:<version>`) are rejected as non-immutable. The refs must already exist on
  the daemon (the suite never pulls) and each pinned digest must describe the
  inspected image (digest/image relationship), validated in receipts.
- `AIO_RELEASE_ARCH` (`amd64`|`arm64`) must match the daemon and both images.
- Explicit hosted GitHub mode: `GITHUB_ACTIONS=true` AND
  `RUNNER_ENVIRONMENT=github-hosted` identify the verified hosted case. The
  runner's own default Docker socket is acceptable ONLY there. Everywhere
  else host sockets are rejected and the verified DinD TLS wire-up is
  mandatory: `DOCKER_HOST=tcp://Docker-in-Docker-DinD:2376`,
  `DOCKER_TLS_VERIFY=1`, `DOCKER_CERT_PATH=/certs/dind` (TLS server name
  `Docker-in-Docker-DinD`, daemon identity `Docker-in-Docker-DinD Alpine`).
  The DinD-only daemon identity check is intentionally skipped for the
  hosted default-socket mode, whose daemon is trusted by construction.
- Semantic baseline: a distinct upgrade never regresses the version.
  The upstream version string is NOT required to move (a wrapper-only
  release may keep it); distinct image ids + digest relationships carry that
  proof. Receipts distinguish the tested releases (refs/ids/revisions) from
  the checkout that ran the suite (`checkout.git_head`).
- No network: every container runs `--network none`. No external/paid model:
  provider keys must be absent, config must carry no API keys, and a public
  `OLLAMA_BASE_URL` is rejected. The retrieval eval only uses gbrain's
  keyword search path (no LLM expansion).
- Unique volumes/containers per run (`aio-rel-<id>-*`); existing resources
  are never touched, and teardown removes only what the run created.
- Receipts land in `AIO_RELEASE_RECEIPTS_DIR` or the system temp dir
  (`aio-release-receipts/<run_id>.json`), secrets redacted.

## Classes

The fleet already selects `tests/integration -m integration`. Its `test_release_upgrade_qualification` now runs this suite on the same locally built AMD64 image. The test stages the image on the disposable runner's loopback registry only. It requires both ownership cases and rejects skipped release tests. This is an AMD64 validation-build gate, not proof of ARM64 qualification or publication by digest. The pinned public baseline in `fleet_gate.py` is reviewed when the supported upgrade baseline changes.

Every release run includes **two independent existing-install fixtures** in `test_release_reservation.py`. Both first prove a real write on the previous image with synthetic data and the template mounts. No preparation runs on the replacement.

| Starting state                                                    | Required immediately after replacement AND after restart                                   | Verdict meaning                                       |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| Matching parent reservation saved in appdata on the old container | Candidate restores the original marker; new writes succeed through the app and on disk     | Writable upgrade for this starting state only         |
| Legacy install without the saved reservation                      | A new write specifically reports checkout `recovery_required`; no page file or row appears | Safety control, **not** a successful legacy migration |

If the product adds verified recovery for legacy installs, review and update that contract explicitly. Do not replace it with a skip. Blank-folder first installs and live Unraid qualification are separate cases.

- `test_release_smoke.py` - same-image class: boot, HTTPS health, paid-model
  surface, unique-volume hygiene, persistence readback (after recreate + restart).
- `test_release_upgrade.py` - distinct-upgrade class: semantic baseline kept,
  two distinct image ids, digest/image relationship of the pinned refs,
  cluster identity preserved, byte-identical persisted record readback old ->
  candidate (and after restart). Skips with an explicit message when inputs
  resolve to one image.
- `test_release_retrieval.py` - behavioral retrieval eval on a known fixture
  whose query terms live only in page bodies, so hits prove content search,
  not slug lookup; includes before/after upgrade parity.
- `test_release_post_upgrade_write.py` - the post-restart new-write gate
  (writer-identity `recovery_required` surface), isolated on purpose. A RED
  here is a genuine product failure report, never masked.
- `test_release_inputs.py` - pure negative/positive proof of the strict
  input contract (digest pinning, hosted mode, socket policy, semantic
  baseline, digest relationships); runs with no Docker present.

## Grounding

Boot/seed/readback mechanics reuse the validated aio-fleet upgrade rehearsal
(single-root `AIO_APPDATA=/data/aio`, brain at `/source/brain`, HTTPS health
on 3132 via Caddy, git-seeded fixture, previous-container-only
first-install grant, `gbrain put --source-id brain` writes, Postgres
registration checks, cluster identity via `pg_control_system()`).
