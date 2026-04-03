# Project-Specific Agent Image Guide

This guide explains how a project repository can provide its own agent image while preserving the standard master-agent runtime contract.

## Purpose

Use a project-specific agent image when the default base image is not enough for the repo's tooling needs.

Typical reasons:
- extra OS packages
- language runtimes or SDKs
- project CLIs
- helper tools used by build, test, or deploy workflows

## Contract

The supported customization path is:

- `.prj_assistant/image/Dockerfile`

That file is the project-specific agent image manifest. When master loads a repo and sees this file, it records a dockerfile image plan and builds the repo-local image on `/master-agent-start`.

## Base Image

Use the published minimal agent base image:

- `ghcr.io/pandazxx/codex-slack-agent-minimal:<tag>`

Recommended tag choices:
- `latest` for default-branch testing
- `vX.Y-rcN` for release-candidate validation
- `sha-<commit>` for immutable pinning

## Required Layout

In the project repository:

```text
<repo-root>/
  .prj_assistant/
    image/
      Dockerfile
```

## Minimal Example

Important:
- the published base image runs as `appuser`
- package-manager commands such as `apt-get` will fail unless you switch to `USER root`
- after installing packages, switch back to `USER appuser` before the Dockerfile ends

```dockerfile
FROM ghcr.io/pandazxx/codex-slack-agent-minimal:latest

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    jq \
    ripgrep \
    && rm -rf /var/lib/apt/lists/*
USER appuser
```

Use this pattern whenever your project image needs OS packages:

```dockerfile
FROM ghcr.io/pandazxx/codex-slack-agent-minimal:latest

USER root
# install packages here
RUN apt-get update && apt-get install -y --no-install-recommends \
    <packages> \
    && rm -rf /var/lib/apt/lists/*
USER appuser
```

## Allowed Customization

Safe additions:
- install OS packages
- install project CLIs
- install language runtimes and support libraries

Use caution with:
- changing `USER`
- changing `WORKDIR`
- adding startup-time side effects

For `USER` specifically:
- temporarily switching to `USER root` for package install is expected
- leaving the final image on `USER root` is not recommended
- always switch back to `USER appuser` unless you are intentionally taking ownership of a non-standard runtime contract

## Runtime Invariants

Project images should preserve these assumptions:

- `/workspace` is the shared workspace root
- `/workspace/repo` is the checked-out project repo
- `/workspace/home` is the writable home area
- `CODEX_CONTAINER_MODE=agent-worker` is still used
- the default entrypoint behavior remains intact

Do not replace the standard runtime contract unless you are intentionally leaving the supported path.

In practice, avoid:
- replacing `ENTRYPOINT`
- changing the worker startup command
- assuming a different repo mount path
- assuming a different home path

## Local Build Example

From the project repository:

```bash
podman build -t local-project-agent -f .prj_assistant/image/Dockerfile .prj_assistant/image
```

Or with Docker:

```bash
docker build -t local-project-agent -f .prj_assistant/image/Dockerfile .prj_assistant/image
```

## Local Smoke Checks

Before asking master to use the image, verify:

```bash
podman run --rm local-project-agent codex --version
podman run --rm local-project-agent claude --version
podman run --rm local-project-agent python -m src.agent.main --help
```

If your Dockerfile installs extra tools, check those too:

```bash
podman run --rm local-project-agent jq --version
podman run --rm local-project-agent rg --version
```

## Master Behavior

When master sees `.prj_assistant/image/Dockerfile` in the repo:

1. `/master-agent-load` records an image plan of type `dockerfile`
2. `/master-agent-start` builds the repo-local image
3. the built image is used for that agent instead of `MASTER_AGENT_BASE_IMAGE`

So:
- `MASTER_AGENT_BASE_IMAGE` applies to default-image agents
- `.prj_assistant/image/Dockerfile` overrides it for that specific repo

## Verification In Master

After loading and starting the agent:

```text
/master-agent-load <name> <repo_path> <channel_id> [branch] [--adapter ...]
/master-agent-start <name>
/master-agent-status <name>
```

Expected result:
- status shows a dockerfile-based image plan
- the built image is used for that agent

## Troubleshooting

If build fails:
- inspect master logs around `/master-agent-start`
- verify the Dockerfile path is exactly `.prj_assistant/image/Dockerfile`
- verify the base image tag exists in GHCR

If the image builds but the agent fails at runtime:
- confirm you did not break `/workspace` path assumptions
- confirm you did not replace the standard entrypoint behavior
- confirm required CLIs are present in the final image

If you used `USER root` during package install:
- switch back to the expected runtime user before the image ends

## Related Docs

- `docs/guides/container-runtime.md`
- `docs/guides/tutorials.md`
- `docs/decisions/0002-separate-base-agent-image.md`
- `docs/design/separate-base-agent-image-detailed-design.md`
