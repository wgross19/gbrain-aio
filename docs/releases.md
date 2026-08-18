# Releases

This repo uses normal semver releases such as `v0.1.0`, not upstream-aligned GBrain versions.

## Release Model

- App repos publish from `main` through the central `aio-fleet` control plane after required validation passes.
- Formal changelog entries and GitHub Releases are release-driven, not automatic for every merge.
- The XML `<Changes>` block is generated from `CHANGELOG.md` during release preparation. Do not edit it manually.

## Tag Scheme

Every normal `main` publish emits Docker Hub and GHCR tags for:

- `latest`
- the upstream version, such as `0.46.14.0`
- `sha-<commit>`

Formal release publishes add the exact changelog release tag, such as `0.46.14.0-aio.1`.

## Release Commands

Run these from the `aio-fleet` checkout:

```bash
uv run aio-fleet release status --repo gbrain-aio
uv run aio-fleet release prepare --repo gbrain-aio --dry-run
uv run aio-fleet release publish --repo gbrain-aio --dry-run
uv run aio-fleet registry verify --repo gbrain-aio --sha <commit-sha> --dry-run --verbose
```

## Upstream Tracking

The Dockerfile pins the official `garrytan/gbrain` commit via `GBRAIN_GIT_SHA`. Upstream bumps are initiated centrally with `aio-fleet upstream monitor`, which opens a PR for human review. Never auto-merge an upstream update without reviewing database, authentication, storage, and security effects.
