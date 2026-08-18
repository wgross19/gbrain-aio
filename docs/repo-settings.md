# Recommended GitHub Settings

Apply these to this repo before it becomes public.

## General

- Enable Issues and Discussions only if you plan to support them.
- Disable Wikis and Projects unless you actively use them.
- Add a social preview image before the repo goes public.
- Set a clear About description and relevant GitHub topics.
- Confirm the Docker Hub repository is public and pullable after the first successful publish.
- Make sure the repo description clearly reflects the wrapped upstream app.

## Branch Protection

Create a ruleset for `main`:

- require pull request before merge
- require status checks to pass before merge
- require signed commits
- require linear history
- block force pushes
- block branch deletion
- include administrators

Suggested required checks:

- `aio-fleet / required`

## Actions

- Set `Workflow permissions` to `Read repository contents and packages`.
- Enable `Allow GitHub Actions to create and approve pull requests` only if you explicitly want that.
- Prefer `Allow select actions and reusable workflows`.
- Keep default `GITHUB_TOKEN` permissions minimal and only elevate inside jobs that publish.
- Keep manual dispatch enabled so you can re-run validation or a controlled publish.
- Keep the central `aio-fleet` scheduled workflow enabled so upstream monitoring can run automatically.

## Security

- Enable the dependency graph and GitHub vulnerability alerts.
- Enable secret scanning.
- Enable push protection.
- Enable private vulnerability reporting.
- Keep shared dependency and upstream policy in `aio-fleet`.

## Packages

- After the first successful publish, verify the Docker Hub repository is public if the repo is public.
- Verify the Docker Hub image name matches the intended CA XML repository value.

## Secrets and Variables

App repos should not carry repo-local workflow secrets for shared automation. Configure the GitHub App, Docker Hub credentials, and GHCR token in `aio-fleet`; keep app-local secrets only when the runtime itself needs them.

## Maintenance

- Keep shared dependency and upstream policy in `aio-fleet`.
- Let `aio-fleet` own shared workflow, Trunk, and upstream automation.
- Run `python -m aio_fleet signing doctor --repo gbrain-aio --format json` before merging generated fleet work or enabling publish automation.
- Review generated automation PRs manually before merging.
