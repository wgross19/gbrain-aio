# Release Checklist

Use this before making the repo public or submitting it to Community Applications.

## Before Public Launch

- [ ] `template-aio.xml` renamed to `gbrain-aio.xml` (done)
- [ ] Starter base image comment gone
- [ ] Upstream GBrain commit pinned explicitly (not a floating tag)
- [ ] Integration tests assert the real readiness signal and health endpoint
- [ ] README has no placeholder language
- [ ] XML points at the correct repo, icon, and support URLs
- [ ] `aio-fleet validate-repo` passes locally, including manifest-driven XML and runtime contract checks
- [ ] `.aio-fleet.yml` matches the central `aio-fleet` manifest and upstream strategy
- [ ] `CHANGELOG.md` and the XML `<Changes>` block describe the same latest release
- [ ] Docker Hub repository is public and pullable after the first successful publish
- [ ] Docker Hub image name matches the CA XML `<Repository>` value

## Before Enabling Publish Automation

- [ ] Registry and GitHub App secrets configured in `aio-fleet`
- [ ] `python -m aio_fleet signing doctor --repo gbrain-aio --format json` passes
- [ ] Generated automation PRs reviewed manually before merge
- [ ] `aio-fleet / required` check appears on a real PR
