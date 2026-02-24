# Master-Agent Architecture (Draft)

This is a living design document for extending this project from a single Slack-connected agent into a master -> agent orchestration model.

## Goal
- Keep the current container as the **agent runtime**.
- Introduce a **master container** that accepts control commands from Slack and manages agent containers for different repos/channels.

## Roles
### Master Container (Control Plane)
- Listens in one admin Slack channel.
- Creates/starts/stops/removes agent containers.
- Maintains agent registry/state on disk.
- Applies policy checks (allowed repo paths, channel mapping, naming).
- Surfaces status/logs back to Slack.

### Agent Container (Worker Plane)
- Existing `codex-slack-bot` image.
- One target repo per container.
- One allowlisted Slack channel per container.
- Runs Codex prompts for that project only.

## Core Principle
- Separate orchestration from execution.
- Master should not execute user coding prompts.
- Agents should not manage other containers.

## Proposed Slack UX (Master)
- `/master-agent-list`
- `/master-agent-create <name> <repo_path> <channel_id>`
- `/master-agent-start <name>`
- `/master-agent-stop <name>`
- `/master-agent-rm <name>`
- `/master-agent-status <name>`
- `/master-agent-logs <name>`
- `/master-agent-config <name> key=value`

## Runtime Interface (Master -> Container Engine)
Support both Docker and Podman via a thin adapter:
- `create_or_update_agent(config)`
- `start_agent(name)`
- `stop_agent(name)`
- `remove_agent(name)`
- `inspect_agent(name)`
- `tail_logs(name, lines)`

Start with CLI invocation (`docker` / `podman`), not daemon APIs.

## Registry (Initial)
Store in repo-local data files:
- `data/master/agents.json` (source of truth)
- `build/agents/<name>.compose.yml` (rendered runtime config)

Per-agent fields (v1):
- `name`, `repo_path`, `channel_id`, `container_name`
- `image`, `runtime` (`docker|podman`)
- `codex_session_id` (optional)
- `git_user_name`, `git_user_email` (optional)
- `gh_token_ref` (optional; do not store raw secret in registry)
- `status` (observed), `created_at`, `updated_at`

## Security Boundaries
- One Slack app for master, separate Slack apps for agents (recommended).
- No raw secrets entered in Slack commands.
- Secrets injected from host env/files or secret store references.
- Restrict master-managed repo paths to approved prefixes.
- Restrict generated container names to safe pattern.

## Failure Model
- Master command succeeds but agent fails to start:
  - Registry persists desired state + error message.
- Agent crashes:
  - Master reports status and restart option.
- Slack outage:
  - Master/agents continue local state; recover on reconnect.

## Open Design Questions
1. Should master use slash commands only, or admin-channel mentions too?
2. How should secret references be represented (env key name vs file path)?
3. Do we allow shared Codex auth/session mounts for all agents, or per-agent scoped homes by default?
4. How much of runtime config should be user-editable from Slack?

## Discussion Topics (Current)
### 1. Per-Project Toolchains (C++, Go, etc.)
- Option A: one generic agent image + project-provided image override (selected for v1).
- Option B: language/profile-specific agent images managed by master (future hardening path).
- Option C: devcontainer/Nix per project (powerful, higher complexity).

Recommended direction (v1, private team):
- Default to one base agent image.
- Allow project manifest to specify a custom image or Dockerfile build context under policy.
- Keep decisions simple because both master and project repos are team-controlled.

#### Image Selection (Proposed Resolution Order, Simplified v1)
When creating a new agent, master selects the image/profile using this precedence:
1. Explicit `image_override` provided by admin (highest priority).
2. Project manifest in repo (for example `.prj_assistant/agent.toml`) declaring image override/build config.
3. Default image (`codex-slack-bot:latest` or configured team default).

Master should persist both:
- `resolved_profile` (optional in v1; may always be `default`)
- `resolved_image`

This makes decisions auditable and stable across restarts.

#### Start Agent Flow (v1 Baseline, Agreed)
This is the simplified start flow to implement first:

1. Master is instructed to load a project repo.
2. Master loads the project's main branch (default `main`, configurable later).
3. Master checks whether `.prj_assistant/image/Dockerfile` exists in that repo.
4. If found:
   - build an agent image from that Dockerfile/context
   - start the agent container using the built image
5. If not found:
   - start the agent container using the default agent image
6. Proceed with workspace mounting and agent initialization (details defined separately).

Notes:
- This flow intentionally keeps image selection simple for a private team environment.
- Additional manifest-based controls remain useful, but image build-vs-default decision comes first.
- Branch switching/worktree cleanliness rules still need a separate policy section.

#### Project Requirements Manifest (Proposed v1)
Allow each repo to define non-secret requirements in a project-owned file, e.g. `.prj_assistant/agent.toml`:

```toml
image_override = ""

[image]
# One of:
# - prebuilt image name
# - Dockerfile path under `.prj_assistant/image/`
name = "ghcr.io/myorg/codex-agent-cpp:team-v1"
dockerfile = ".prj_assistant/image/Dockerfile"
context = ".prj_assistant/image"

[runtime]
workspace_mode = "host_bind"
codex_home_mode = "project"
```

Rules:
- Manifest is advisory, not authoritative.
- Master validates values against policy (allowed image prefixes, allowed Dockerfile paths/contexts).
- Secrets/tokens are not allowed in the project manifest.
- Project-specific image build assets live under `.prj_assistant/image/`.
- In v1, presence of `.prj_assistant/image/Dockerfile` is the primary image-build trigger.

#### Managing Images in v1 (Team-Controlled)
Given a private, team-controlled environment:
- Master may accept project manifest image overrides and Dockerfile-based builds.
- Dockerfile path and build context must be constrained to repo-local paths.
- Master should record the resolved image/build source for auditability.

Safety guardrails still recommended (even in private use):
- Restrict Dockerfile/context to under repo root after `realpath` resolution.
- Optional allowlist for image prefixes (e.g. `ghcr.io/myorg/`).
- Build/run with explicit resource limits where possible.

#### Future Hardening Path (v2+)
Move to a managed image catalog when team scale/reliability needs increase:
- static catalog file
- profile-based image selection
- CI-built/published images only

### 2. Slack Integration Ownership (User vs Master)
- User-managed Slack app/channel setup is easiest to ship first.
- Master-managed channel creation is possible through Slack Web API.
- Master-managed Slack app creation/install is possible but significantly more complex and high-risk (OAuth distribution flow, admin consent, token storage).

Recommended direction (v1):
- User creates Slack apps manually (master app + agent apps).
- Master validates provided channel IDs and token presence.
- Revisit automated channel creation later; avoid app creation/install automation initially.

### 3. Workspace Storage Model (Host Mount vs Container Internal)
- Host bind mounts: best for real repo workflows and Git interoperability (recommended default).
- Container internal storage: safer isolation but poor UX for existing repos and external tooling.
- Managed named volumes: useful for ephemeral agents, but harder for user inspection and external IDE use.

Recommended direction (v1):
- Host bind mount required for repo-based agents.
- Add policy restrictions on allowed host repo roots.
- Consider optional "ephemeral clone into managed volume" mode later.

### 4. AI Login and Session Management
- Shared host Codex auth/session mounts are simple but broad.
- Per-agent project-local `CODEX_HOME` is safer and isolates sessions.
- Token-based auth via env is easiest to automate but user may prefer auth cache.

Recommended direction (v1):
- Support auth cache forwarding (read-only host mount + local copy) as default.
- Support per-agent project-local `CODEX_HOME` when repo has `.codex/`.
- Master stores only session IDs / auth references, never raw secrets.
