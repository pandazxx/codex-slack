# Production Deployment

Production deployment is fully automated via CI/CD. No human runs `docker compose` against production.

## Deployment Flow

1. Merge to `master` triggers `build-push.yml` workflow.
2. Workflow builds the `prod` stage of `Dockerfile` and pushes to `$REGISTRY` with a version tag and SHA tag.
3. A human or the `sre` agent invokes the `post-merge-cleanup` operation, which runs `just deploy staging master` — the recipe resolves the tag to a digest and refreshes the staging singleton on `STAGING_DOCKER_HOST` (see `.sre/operations/deploy.md`).

## Prod vs Staging

This project's "production" shape is the canonical staging environment tracked by `main`. There is no separate prod host defined yet.

To add a prod host:
1. Set `PROD_DOCKER_HOST` in `.env`.
2. Bootstrap the host (Docker, SSH access) — ask `senior-sre` for first-time provisioning.
3. Run `just deploy prod <tag>`. Prod uses the same singleton shape and overlay as staging (`docker-compose.deploy.yml`) — no new compose or runbook files are needed (ADR-0016).

## Rollback

To roll back staging to a previous version:
1. Obtain the previous tag from the CI workflow run history or the registry.
2. Ask `sre` to run `just deploy staging <previous-tag>`.
3. The recipe resolves the tag to its digest, re-pulls, and replaces the running singleton in place.

## Image Tagging Convention

| Pattern | Example | Used for |
|---|---|---|
| Branch name | `master` | Canonical staging tracking |
| Short SHA | `abc1234` | Per-commit traceability |
| Semver | `v1.2.3` | Release tags |

All pushes to production use digest pinning (`image:tag@sha256:...`), never floating tags.
