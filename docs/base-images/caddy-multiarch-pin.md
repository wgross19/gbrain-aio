# Caddy base pin: single-arch child -> verified multi-arch index

Date: 2026-11-22 · Scope: `gbrain-aio` Dockerfile base stage (`caddy`), read-only
registry verification. No image build, no push, no merge.

## Change

```diff
 ARG CADDY_VERSION=2.10.2
-ARG CADDY_DIGEST=sha256:d8c17a862962def15cde69863a3a463f25a2664942eafd7bdbf050e9c3116b83
+ARG CADDY_DIGEST=sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d
 ARG CADDY_IMAGE=caddy:${CADDY_VERSION}-alpine
```

One ARG line (plus comment). `FROM ${CADDY_IMAGE}@${CADDY_DIGEST}` is unchanged,
so the fleet's digest-pin policy and checkov skips still apply.

## Registry evidence (`base-image-pins.json`)

Captured with anonymous Docker Hub `/v2/` manifest GETs; every child manifest
body was re-hashed and compared to its digest (`digest_matches_body: true` for
all entries, including attestations).

| Ref | Value |
| --- | --- |
| Tag | `caddy:2.10.2-alpine` |
| Index digest (`Docker-Content-Digest`) | `sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d` |
| Index media type | `application/vnd.oci.image.index.v1+json` |
| linux/amd64 child | `sha256:d8c17a862962def15cde69863a3a463f25a2664942eafd7bdbf050e9c3116b83` (== the previous pin) |
| linux/arm64/v8 child | `sha256:8dbca3525dd8dce671e495665a0eb297a83ec87ee30f373947d0ea93115949db` |
| Other linux children | arm/v6 `9ecaeae5…`, arm/v7 `333ca049…`, ppc64le `a46a0437…`, riscv64 `5f7460b2…`, s390x `19c92446…` |
| Attestations | 7 `attestation-manifest` entries, platform `unknown/unknown` — not selectable as a base |

Corroborating tags (same publish, so the index is not an orphan):

- `caddy:2.10-alpine` -> the same index digest `4c6e91c6…`.
- `caddy:2.10.2` -> index `c3d7ee5d…` whose **linux children are identical**
  (including amd64 `d8c17a86…`); it differs only by two windows/amd64 entries.

Because the previously pinned digest is exactly the `linux/amd64` member of the
index, switching the pin cannot change amd64 bytes: same manifest digest, same
5 layers, same config `sha256:aac61abc…`. On arm64 the index now selects a
native child instead of reusing amd64.

## Other pinned stages (already native-multiarch)

| Stage | Pin (in Dockerfile) | Kind | amd64 child | arm64 child |
| --- | --- | --- | --- | --- |
| `bun` | `oven/bun:1.4.4@sha256:1d22548f…` | OCI index | `3f23864d…` | `f2b798e7…` (arm64) |
| debian base | `debian:bookworm-slim@sha256:88200866…` | OCI index (pinned index predates the moved tag, which is now `734a72b3…`) | `5ae3c39e…` | `6bd27d44…` (arm64/v8) |
| s6-overlay | tarball `3.2.1.0`, arch from `dpkg --print-architecture` | `amd64 -> x86_64`, `arm64 -> aarch64` | n/a | n/a |

No stage remains amd64-only, so the Dockerfile is consistent for a native arm64
build after this change.

## Local verification (no Docker)

```console
$ python -m pytest -q tests/test_base_image_pins.py
7 passed in 0.03s

$ python -m pytest -q tests --ignore=tests/integration --ignore=tests/release -m "not integration"
50 passed in 0.19s        # 43 pre-existing + 7 new, 0 skipped

# guard has teeth: reverting only CADDY_DIGEST to the single-arch child
1 failed, 6 passed        # test_caddy_is_pinned_to_the_verified_multiarch_index
```

## Limits / not proven here

- No `docker build`/`docker run` was executed, so this does not prove the arm64
  image builds or boots; it proves digest/pin correctness at the manifest layer.
- Digest resolution is asserted from the captured receipt, not re-queried by the
  test (deliberate: the unit suite must stay offline). Re-capture when changing a
  pin.
- `aio-fleet/app/gbrain.dockerfile` is a stale scaffold copy (Bun 1.4.2, older
  `GBRAIN_GIT_SHA`) and still shows the old Caddy pin; it is not the build input
  and was left untouched.

## Re-capture procedure

```sh
TOKEN=$(curl -fsS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/caddy:pull" | jq -r .token)
curl -fsS -H "Authorization: Bearer $TOKEN" \
  -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json' \
  "https://registry-1.docker.io/v2/library/caddy/manifests/2.10.2-alpine" | jq .
# then hash each child body: sha256sum of the raw manifest bytes must equal its digest
```
