# Production Deployment

Production deployment is fully automated via CI/CD. No human runs `docker compose` against production.

## Deployment Flow

1. Merge to `master` triggers `build-push.yml` workflow.
2. Workflow builds the `prod` stage of `Dockerfile` and pushes to `$REGISTRY` with a version tag and SHA tag.
3. The workflow outputs the image digest.
4. A human or automated release process invokes the `post-merge-cleanup` operation (via `sre` agent), providing the image ref and digest.
5. `sre` runs `.sre/staging-up.sh master <image-ref> <digest>` against `STAGING_DOCKER_HOST`.

## Prod vs Staging

This project's "production" shape is the canonical staging environment tracked by `main`. There is no separate prod host defined yet.

To add a prod host:
1. Set `PROD_DOCKER_HOST` in environment.
2. Bootstrap `sre-host-infra` on the prod host.
3. Ask `senior-sre` to extend the compose and runbook files for the `prod` tier.

## Rollback

To roll back staging to a previous version:
1. Obtain the previous image ref and digest from the CI workflow run history.
2. Ask `sre` to run `staging-up <branch> <prev-image-ref> <prev-digest>`.
3. Compose will re-pull and replace the running containers.

## Image Tagging Convention

| Pattern | Example | Used for |
|---|---|---|
| Branch name | `master` | Canonical staging tracking |
| Short SHA | `abc1234` | Per-commit traceability |
| Semver | `v1.2.3` | Release tags |

All pushes to production use digest pinning (`image:tag@sha256:...`), never floating tags.
