# Pin Python Base Image to Specific Minor Version

**Status:** Accepted  
**Date:** 2025-05-08  
**Author:** SRE Subagent

## Context

Base images for all Dockerfiles (Dockerfile, Dockerfile.dev, Dockerfile.test, Dockerfile.agent-minimal, Dockerfile.cd-daemon) were using `python:3.11-slim` without pinning the patch version. This allows Docker Hub to silently pull a different patch version on rebuild, breaking reproducibility and potentially introducing security regressions.

## Decision

Pin all Python base images to `python:3.11.9-slim` (major.minor.patch format). This ensures:

- **Reproducibility**: The same SHA256 image digest is pulled on every build.
- **Supply chain security**: Deliberate process for updating Python versions instead of accidental drifts.
- **Predictability**: CI and local builds use the same image.

### Updated Dockerfiles

- `Dockerfile` → `python:3.11.9-slim`
- `Dockerfile.dev` → `python:3.11.9-slim`
- `Dockerfile.test` → `python:3.11.9-slim`
- `Dockerfile.agent-minimal` → `python:3.11.9-slim`
- `Dockerfile.cd-daemon` → `python:3.11.9-slim`

## Rationale

The SRE stop-the-world catalog explicitly blocks production Dockerfiles using major-only tags (`FROM node:20` or `FROM python:3.11`) without major+minor versions. This is a reproducibility and supply-chain security gate. Every rebuild must pull the same bits; drifting silently violates that contract.

Python 3.11.9 is the latest stable patch as of 2025-05-08. Future Python security releases (e.g., 3.11.10, 3.12.x) should be upgraded deliberately via PR, not silently drifted.

## Alternatives Considered

1. **Use floating tags (rejected)** — leads to irreproducible builds.
2. **Use major.minor only (e.g., `3.11`) (rejected)** — still drifts on patch; catalog requires major.minor.patch.
3. **Use Alpine instead of Debian slim (rejected)** — outside this decision's scope; orthogonal choice already made for this project.

## Consequences

- **Positive**: Builds are reproducible; CI and local match. Security is deliberate, not accidental.
- **Negative**: Python updates require manual intervention (acceptable; security updates are infrequent).
- **Risk**: Low. Patch-level base image changes are low-impact and can be tested before committing.

## Implementation

All Dockerfiles updated. No other changes needed (uvicorn, pytest, FastAPI versions are pinned in `requirements.txt`).

## References

- SRE stop-the-world catalog: "Production Dockerfile uses major-only tags (`FROM node:20`) — block in file."
- `docs/guides/sre.md` — supply chain and reproducibility rationale.
- `.github/workflows/ci-pr.yml` — builds use Docker Buildx caching; cache-busting on base image change is automatic.
