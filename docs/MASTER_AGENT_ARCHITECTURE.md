# Master-Agent Architecture (Draft)

This is a living design document for extending this project from a single Slack-connected agent into a master -> agent orchestration model.

## Goal
- Keep the current container as the **agent runtime**.
- Introduce a **master container** that accepts control commands from Slack and manages agent containers for different repos/channels.
- For v1, the master manages containers via host Podman (socket passthrough), not a nested container daemon.

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
- Runs Codex prompts for that project only.
- In v1 single-bot mode, does not connect to Slack directly (master forwards work).

## Core Principle
- Separate orchestration from execution.
- Master should not execute user coding prompts.
- Agents should not manage other containers.

## Proposed Slack UX (Master)
- `/master-agent-list`
- `/master-agent-load <name> <repo> <channel_id>`
- `/master-agent-start <name>`
- `/master-agent-stop <name>`
- `/master-agent-rm <name>`
- `/master-agent-status <name>`
- `/master-agent-logs <name>`
- `/master-agent-bind <name> <channel_id>` (optional, if binding is not part of `load`)
- `/master-agent-unbind <channel_id>` (optional)

## V1 Master Command Contract (Draft)
This section defines the initial command surface to support the agreed v1 start flow.

### Principles
- Keep commands explicit and small.
- Separate "register/load" from "start runtime" for clearer error handling.
- Support idempotent retries from Slack.
- Do not accept raw secrets in command arguments.

### Commands (v1)
#### `/master-agent-load <name> <repo_path> <channel_id>`
Purpose:
- Register or update an agent definition.
- Validate repo path and channel mapping.
- Load project `main` branch and evaluate `.prj_assistant/image/Dockerfile`.
- Resolve build-vs-default image plan (optionally build in v1 if we choose eager build mode).

Effects:
- Mutates registry.
- May create/update rendered config.
- Does not need to start container (recommended default).
- Binds `channel_id` to `name` if binding is available and not already owned by another agent.

Input rules:
- `name`: `^[a-z0-9][a-z0-9-]{1,30}$`
- `repo_path`: Git URL or approved repo identifier (resolved by master policy)
- `channel_id`: Slack channel ID (`C...`)

#### `/master-agent-start <name>`
Purpose:
- Start agent container from resolved/default image and rendered config.

Effects:
- Mutates runtime state.
- Updates observed status in registry.

#### `/master-agent-stop <name>`
Purpose:
- Stop a running agent container.

Effects:
- Mutates runtime state.
- Updates observed status in registry.

#### `/master-agent-status <name>`
Purpose:
- Show registry data + observed runtime/container status.

Effects:
- Read-only.

#### `/master-agent-list`
Purpose:
- List registered agents and summarized states.

Effects:
- Read-only.

#### `/master-agent-remove <name>`
Purpose:
- Remove agent registration and optionally remove container (stopped first if running).

Effects:
- Mutates registry and runtime state.

### Command Response Contract (v1)
All command handlers should return a shared envelope:

```json
{
  "ok": true,
  "command": "load",
  "agent": "payments-api",
  "code": "OK",
  "message": "Agent loaded",
  "data": {},
  "request_id": "req_01HT...",
  "at": "2026-02-27T00:00:00Z"
}
```

Fields:
- `ok`: success/failure boolean.
- `command`: normalized command (`list|load|start|stop|status|remove`).
- `agent`: target agent when applicable.
- `code`: stable machine code.
- `message`: concise user-facing summary for Slack.
- `data`: command-specific payload.
- `request_id`: correlation ID for logs/debug.
- `at`: RFC3339 timestamp.

`data` payload by command:
- `load`: `state`, `resolved_image`, `build_source`, `channel_id`.
- `start`: `state`, `container_name`, `container_id`, `started_at`.
- `stop`: `state`, `container_name`, `stopped_at`.
- `status`: registry + observed runtime snapshot.
- `list`: list of agents with summary status.
- `remove`: `removed=true`, optional runtime cleanup notes.

### Error Code Contract (v1)
Use stable error codes in Slack and logs:
- `ERR_INVALID_ARGS`: command argument validation failed.
- `ERR_AGENT_NOT_FOUND`: target agent missing from registry.
- `ERR_CHANNEL_CONFLICT`: channel already bound to another agent.
- `ERR_REPO_NOT_ALLOWED`: repo outside policy or unresolvable.
- `ERR_LOAD_FAILED`: repo load or manifest parse failed.
- `ERR_BUILD_FAILED`: image build failed.
- `ERR_RUNTIME_FAILED`: Podman start/stop/inspect failed.
- `ERR_AGENT_NOT_RUNNING`: stop/status path expected running container but none found.
- `ERR_INTERNAL`: unexpected master failure.

### Optional Convenience Command (v1.1)
#### `/master-agent-up <name> <repo_path> <channel_id>`
Wrapper for:
1. `load`
2. `start`

This is optional sugar. Internally it should call the same service methods as `load` + `start`.

## V1 Workflow / State Machine (Draft)
Master manages agent lifecycle state independently from the container engine.

### Logical States
- `registered`: repo/channel recorded, not yet resolved
- `loaded`: repo checked, start plan resolved (default image or build plan)
- `built`: custom image built successfully (only for Dockerfile path case)
- `running`: container running
- `stopped`: container exists but not running (or intentionally stopped)
- `error`: last operation failed (registry retains error details)

### Common Transitions
- `load`:
  - `registered|stopped|error -> loaded`
  - `loaded -> loaded` (idempotent refresh)
  - `running -> loaded` only if load is allowed to refresh config without restart (mark drift)
- `start`:
  - `loaded|built|stopped|error -> running` (if prerequisites met)
  - `running -> running` (idempotent no-op)
- `stop`:
  - `running -> stopped`
  - `stopped -> stopped` (idempotent no-op)
- `remove`:
  - any non-running state -> removed (registry deletion)
  - `running` requires stop first or `--force` policy (not in v1 Slack command)

### Idempotency Rules (Important)
- Repeating `load` should refresh and re-evaluate `.prj_assistant/image/Dockerfile` presence.
- Repeating `start` on a running agent should return success with "already running".
- Repeating `stop` on a stopped/missing runtime should return success with "already stopped".
- `remove` on missing agent should return a not-found error (not success), to catch mistakes.

### Error Reporting (Slack)
Responses should include:
- failed stage (`validate_repo`, `load_main_branch`, `build_image`, `start_container`, ...)
- short error summary
- suggested next action when obvious (`run load again`, `fix Dockerfile`, `check repo path`)

### Slack Output Examples (v1)
`/master-agent-load payments-api github.com/acme/payments C12345`
- success: `OK load payments-api | state=loaded | image=ghcr.io/acme/codex-slack-bot:latest | channel=C12345`
- conflict: `ERR_CHANNEL_CONFLICT load payments-api | channel C12345 is already bound to agent billing-api`

`/master-agent-start payments-api`
- success: `OK start payments-api | state=running | container=agent-payments-api`
- idempotent: `OK start payments-api | already running`

`/master-agent-stop payments-api`
- success: `OK stop payments-api | state=stopped`
- idempotent: `OK stop payments-api | already stopped`

## Slack Topology and Routing (V1, Selected)
### Topology
- One Slack app / bot token for the whole system.
- Master is the only Slack client (single Socket Mode connection).
- Agent containers are worker runtimes and do not connect to Slack directly.

### Channel Roles
- **Admin channel(s)**: accepted for master orchestration commands only.
- **Agent channels**: user prompts routed to exactly one mapped agent.

### Channel Ownership Rule
- One channel maps to one agent.
- One agent may serve one or more channels (defer for now; default to one-to-one).

V1 default:
- one channel <-> one agent (strict)

### Channel Conflict Handling (V1)
When `/master-agent-load ... <channel_id>` is called:
- If channel is unbound: bind it to the agent.
- If channel is already bound to the same agent: idempotent success.
- If channel is bound to a different agent: reject with conflict error.

Optional later:
- explicit `/master-agent-rebind <channel_id> <agent>` or force flag

### Routing Rules (Agent Communication)
For messages in non-admin channels:
1. Master receives Slack event.
2. Master resolves `channel_id -> agent`.
3. If no mapping exists: ignore or reply with setup hint (policy configurable).
4. If mapping exists: forward prompt to that agent worker.
5. Agent returns response.
6. Master posts response to Slack (same thread when applicable).

### Thread Behavior (Recommended)
- Reuse current thread semantics already implemented in this project:
  - initial mention starts a tracked thread
  - follow-up thread replies continue without repeated mention
- Master owns thread tracking and routing in the single-bot model.

## Runtime Interface (Master -> Container Engine)
Use Podman in v1 via a thin runtime adapter:
- `create_or_update_agent(config)`
- `start_agent(name)`
- `stop_agent(name)`
- `remove_agent(name)`
- `inspect_agent(name)`
- `tail_logs(name, lines)`

Start with CLI invocation (`podman`), not daemon APIs.

## Master-In-Container Runtime Model (V1, Selected)
Master runs as a container and controls host Podman through a mounted Podman socket.

Execution model:
1. Host exposes Podman service socket.
2. Master container mounts that socket read/write.
3. Master invokes `podman` client/remote against the mounted socket.
4. Podman service on host creates/stops agent containers.

Socket path options:
- Rootful host Podman: `/run/podman/podman.sock`
- Rootless host Podman: `/run/user/<uid>/podman/podman.sock`

Required master container runtime wiring (v1):
- Mount Podman socket into master container.
- Provide Podman client binary in master image.
- Set connection target (`CONTAINER_HOST=unix:///.../podman.sock`) or equivalent CLI flag.

Guardrails:
- Treat socket access as privileged control-plane capability.
- Restrict which images/containers master may create (name prefix + project policy).
- Keep master deployment limited to trusted infra/operators.

## Registry (Initial)
Store in repo-local data files:
- `data/master/agents.json` (source of truth)
- `build/agents/<name>.compose.yml` (rendered runtime config)

Per-agent fields (v1):
- `name`, `repo_path`, `channel_id`, `container_name`
- `image`, `runtime` (`podman`)
- `codex_session_id` (optional)
- `git_user_name`, `git_user_email` (optional)
- `gh_token_ref` (optional; do not store raw secret in registry)
- `status` (observed), `created_at`, `updated_at`

## Security Boundaries
- One shared Slack app/token set for the system in v1; master is the only Slack client.
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
This is the revised v1 start flow (managed clone workspace):

1. Master is instructed to load a project repo (repo URL / repo identifier).
2. Master resolves the project source and checks project `main` branch contents for `.prj_assistant/image/Dockerfile`.
3. If found:
   - build an agent image from `.prj_assistant/image/`
   - record built image reference in agent registry
4. If not found:
   - use the default agent image
5. Master starts agent container with:
   - a named volume for workspace storage
   - shared SSH agent and/or `GH_TOKEN` references for Git operations
6. Agent container initialization clones/fetches the repo into its internal workspace volume.
7. Agent completes initialization stages and then runs the worker process for prompt execution.

Notes:
- This flow intentionally avoids mutating a host repo working tree.
- "Dirty repo" host working tree concerns are removed from the default path.
- Branch strategy and sync policy are intentionally deferred to project-level decisions (out of current scope).

#### Agent Initialization Stages (Selected v1)
In v1, agent container startup is a staged entrypoint flow with no always-on control service.

Stages:
1. `preflight`: verify required env and credentials (`SSH_AUTH_SOCK` and/or `GH_TOKEN` ref), verify writable workspace volume.
2. `repo_sync`: clone repo if missing; otherwise fetch/reset according to project policy.
3. `workspace_prepare`: apply repo-local setup needed for agent runtime (for example `.codex` bootstrap if configured).
4. `ready`: launch the agent worker process (no Slack client in agent).

Status feedback to master (without in-agent service):
- Container lifecycle state via Podman inspect (`created`, `running`, `exited`).
- Structured stage logs emitted to stdout/stderr (master tails and parses markers).
- Optional init status file in shared control path (for example `/run/master-agent/status.json`) written during stages.

Failure behavior:
- Any failed stage exits container with non-zero code.
- Master records failed stage + exit code + last log lines into registry and reports to Slack.

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
workspace_mode = "named_volume"
codex_home_mode = "project"
```

Rules:
- Manifest is advisory, not authoritative.
- Master validates values against policy (allowed image prefixes, allowed Dockerfile paths/contexts).
- Secrets/tokens are not allowed in the project manifest.
- Project-specific image build assets live under `.prj_assistant/image/`.
- In v1, presence of `.prj_assistant/image/Dockerfile` is the primary image-build trigger.
- Branch strategy / sync behavior are intentionally not defined by this manifest in v1.

#### Managing Images in v1 (Team-Controlled)
Given a private, team-controlled environment:
- Master may accept project manifest image overrides and Dockerfile-based builds.
- Dockerfile path and build context must be constrained to repo-local paths.
- Master should record the resolved image/build source for auditability.

Safety guardrails still recommended (even in private use):
- Restrict Dockerfile/context to under repo root after `realpath` resolution.
- Optional allowlist for image prefixes (e.g. `ghcr.io/myorg/`).
- Build/run with explicit resource limits where possible.

### 3. Workspace Storage Model (Host Mount vs Container Internal)
- **Selected for revised v1:** named volume per agent (container-managed workspace storage).
- Agent clones/fetches the repo during initialization instead of using a host bind mount.
- Host bind mount mode can remain as an optional fallback/debug mode later.

Why this was selected:
- Removes host dirty-working-tree risk.
- Makes agent behavior more reproducible.
- Aligns with frequent commit/push workflow where GitHub is the primary outcome store.

### 4. AI Login and Session Management
- Auth/session forwarding remains supported (read-only mount + local writable `CODEX_HOME` copy).
- Master and agent use the same SSH agent mechanism and/or `GH_TOKEN` references.
- Branch strategy and repo sync policy are deferred to project-level decisions (not in current scope).

#### Future Hardening Path (v2+)
Move to a managed image catalog when team scale/reliability needs increase:
- static catalog file
- profile-based image selection
- CI-built/published images only

### 2. Slack Integration Ownership (User vs Master)
- Single Slack bot for both master and agent communications (selected for v1 to reduce setup overhead).
- Channel-based forwarding determines which agent receives a user message.
- Limitation: one channel maps to one agent at a time.
- Master-managed Slack app creation/install remains out of scope.

Recommended direction (v1):
- Use one Slack app/bot token for the whole system.
- Reserve one admin channel (or command scope) for master control commands.
- Maintain a channel -> agent mapping registry.
- Master routes non-admin channel messages to the mapped agent container.
- Revisit multi-bot isolation later if security/scale requires it.

#### Channel-Based Forwarding Model (Selected v1)
Core rule:
- One Slack channel can be attached to exactly one agent.

Implications:
- Lower Slack app setup overhead (single app, single install, single token set).
- Simpler operator onboarding.
- Stronger need for routing correctness in master.
- Agents no longer need direct Slack connectivity in v1 if master proxies messages (optional design choice to finalize).

Two implementation variants:
1. **Master-only Slack integration (recommended for this model)**
   - Master receives all Slack events.
   - Master forwards prompts/results to agent containers over an internal control interface.
   - Agents do not need Slack tokens.
2. **Shared Slack bot token across master + agents (possible but less clean)**
   - Multiple containers connect to Slack with same app credentials.
   - Routing ambiguity and duplicate event handling become harder.

Recommended v1:
- Master-only Slack integration with channel->agent routing.
- Treat agent containers as worker runtimes, not Slack clients.

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
