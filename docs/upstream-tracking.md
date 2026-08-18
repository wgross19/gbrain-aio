# Upstream Tracking

Upstream tracking is owned by `aio-fleet`, not by app-local scripts. This repo declares upstream metadata in `.aio-fleet.yml`; the central `aio-fleet/fleet.yml` remains the source for generated manifests and control-plane policy.

## Required Inputs

- Upstream name and source repository: `garrytan/gbrain`.
- Dockerfile ARG that pins the upstream version: `GBRAIN_GIT_SHA`.
- Update strategy: `pr` for safe single-image bumps, `notify` for multi-image stacks that need manual review.

## GBrain-Specific Note

GBrain is tracked by a Git commit SHA (`GBRAIN_GIT_SHA`), not a released Docker image. The Dockerfile checks out the pinned SHA and verifies `rev-parse HEAD` matches before building. When configuring the central upstream monitor, ensure it can express a Git-SHA pin rather than only a release/digest. If the central monitor cannot express this cleanly, that is a small `aio-fleet` extension, not a reason to redesign the container.

## Validation

Run this from `aio-fleet` after changing upstream metadata or Dockerfile pins:

```bash
uv run aio-fleet validate --repo gbrain-aio
uv run aio-fleet release status --repo gbrain-aio
```
