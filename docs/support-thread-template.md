# Support Thread Template

Use this template when opening a support thread for a gbrain-aio issue.

## Title

`[gbrain-aio] <short description>`

## Body

**App:** gbrain-aio
**Version:** (from `docker inspect` or the image tag)
**Unraid version:** (e.g. 6.12.x)

**Describe the issue:**

**Steps to reproduce:**

**Expected behavior:**

**Actual behavior:**

**Container logs:**

```text
(paste `docker logs gbrain-aio` output)
```

**Relevant config (redact secrets):**

- `GBRAIN_PUBLIC_URL`
- `GBRAIN_LAN_BIND`
- `SOURCE_NAME`
- `BRAIN_PATH`

**Does Postgres publish 5432?** (should be no)

**Does any agent have `DATABASE_URL`?** (should be no)
