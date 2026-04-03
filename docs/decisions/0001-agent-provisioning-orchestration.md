# 0001 Agent Provisioning Orchestration

- Status: proposed
- Date: 2026-04-03
- Issue: [#37](https://github.com/pandazxx/codex-slack/issues/37)

## Context

The current master-agent workflow assumes provisioning dependencies already exist:

- `/master-agent-load` requires an existing `channel_id`
- repository input must already resolve to a local checkout, explicit Git URL, or GitHub shorthand

Issue `#37` expands provisioning so an operator can optionally:

- create the destination channel automatically
- create the backing GitHub repository automatically

The current implementation boundaries matter:

- `MasterService.load_agent()` in `src/master/service.py` is a bind-and-checkout flow
- Slack and Discord API clients live in the frontend layers, not in `MasterService`
- repository checkout and registry persistence live in master core

If channel creation, repository creation, and load/bind behavior are all pushed into `load_agent()`, the existing low-level operation becomes a cross-system orchestration step with frontend SDK coupling and more complex failure handling.

## Decision

Introduce a new high-level provisioning workflow and keep `/master-agent-load` unchanged.

### Command model

Add a new command surface:

- `/master-agent-provision`

Keep `/master-agent-load` as the low-level primitive for binding an already-existing repository and channel.

### Architecture boundary

Add a provisioning orchestration layer that composes three responsibilities:

1. repository creation
2. channel creation
3. existing `load_agent()` binding

Recommended module split:

- `ProvisioningCoordinator`
- `RepoProvisioner` protocol
- `ChannelProvisioner` protocol

### Ownership

Repository creation belongs in master backend orchestration.

- GitHub repository creation is not frontend-specific
- it should use `GH_TOKEN` / `GITHUB_TOKEN`
- it should return clone URL plus creation metadata

Channel creation belongs in platform-specific provisioners.

- Slack creation should live near Slack frontend integration
- Discord creation should live near Discord frontend integration
- `MasterService` should not depend directly on Slack or Discord SDK clients

### Resource creation model

For v1:

- create channels, not threads
- default generated channel name: `agent-<name>`
- default generated repository name: `<name>`
- default repo visibility: `private`
- allow channel creation and repo creation to be independently optional

Provisioning flow:

1. validate request
2. create repo if requested
3. create channel if requested
4. call `load_agent()` with resolved `repo_path` / `repo_source` and `channel_id`
5. return combined provisioning result

### Persistence and results

At minimum, the provisioning result should include:

- created repo URL
- repo owner
- repo name
- repo visibility
- created channel id
- created channel name
- platform

If operator workflows need this metadata later, extend the registry schema to persist:

- `channel_name`
- `provisioned_channel`
- `repo_owner`
- `repo_name`
- `provisioned_repo`

## Alternatives Considered

### 1. Extend `/master-agent-load`

Rejected.

This would overload a deterministic bind command with external side effects and make validation, UX, and rollback behavior harder to reason about.

### 2. Put channel creation inside `MasterService`

Rejected.

`MasterService` does not currently own Slack or Discord API client context. Moving frontend resource creation into the service layer would couple core orchestration to platform SDK details.

### 3. Implement only auto channel creation

Rejected.

Issue `#37` now also includes optional GitHub repository auto-creation. The architecture should solve both under one provisioning workflow instead of creating a second orchestration path later.

## Consequences

Positive:

- preserves the current `load_agent()` contract
- keeps frontend-specific channel APIs out of `MasterService`
- makes Slack and Discord differences explicit
- supports staged rollout and easier testing through provider mocks

Tradeoffs:

- adds a new command instead of extending the existing one
- requires provider abstractions and additional tests
- creates partial-failure scenarios across GitHub and frontend channel APIs

## Operational Notes

Recommended initial constraints:

- Discord channel creation should target a configured guild/category, not an inferred thread destination
- Slack public/private behavior should be explicit or policy-driven, not implicit
- GitHub repo creation should define owner, visibility, and auth requirements up front
- v1 should prefer explicit failure reporting over best-effort rollback automation

## Implementation Guidance

Engineer and tester should treat this ADR as defining the v1 implementation boundary:

- add `/master-agent-provision`
- keep `/master-agent-load` behavior unchanged
- add provisioner protocols and coordinator
- add GitHub repo provisioner
- add Slack channel provisioner
- add Discord channel provisioner
- add tests for success, idempotent reuse, conflict, and partial-failure paths
