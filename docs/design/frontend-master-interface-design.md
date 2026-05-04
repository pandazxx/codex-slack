# Frontend-Master Interface Design

**Status:** canonical design  
**Scope:** interface contract between Slack/Discord frontends and the master runtime

## Goal

Define the authoritative contract between frontend adapters and master so that:

- Slack and Discord differences are explicit
- normalized internal models are stable
- command parity and routing behavior are well-defined
- attachment handling differences do not leak into agent-facing contracts

This document focuses on frontend-to-master behavior. It does not define the
master-to-agent runtime contract.

## Roles

### Frontend Adapters

Frontend adapters are responsible for:

- platform-specific event intake
- slash-command or application-command handling
- attachment extraction
- normalization into a shared master-facing shape
- platform-specific response emission

### Master

Master is responsible for:

- admin command dispatch
- mapped-agent routing
- thread continuity tracking
- rate limiting and policy checks
- returning normalized results to the frontend adapter

## Normalized Models

### Inbound Prompt

Each frontend normalizes user prompts into a shared logical model with:

- `platform`
- `channel_id`
- `thread_id`
- `message_id` or event timestamp equivalent
- `user_id`
- `text`
- attachment metadata

Attachment classes:

- image
- document
- text attachment

### Inbound Command

Each frontend normalizes admin commands into:

- `platform`
- `command_name`
- `args_text`
- `channel_id`
- `user_id`

## Shared Master Behaviors

Across both frontends, master enforces:

- admin channels are reserved for `/master-agent-*` commands
- mapped non-admin channels route to exactly one agent
- one channel maps to one agent
- follow-up replies route only within tracked conversation context
- request attachments are staged through the request-manifest workflow rather
  than appended as free-form prompt prose

## Slack vs Discord Differences

### 1. Command Surface

Slack:

- commands arrive as Slack slash commands
- admin commands are routed from the Slack command payload

Discord:

- commands arrive as Discord application commands
- admin commands are registered explicitly on the Discord command tree

Invariant:

- the logical `/master-agent-*` command set must remain equivalent across both
  frontends

### 2. Conversation Trigger

Slack:

- mention-driven start via app mention
- follow-up routing in Slack threads

Discord:

- mention-driven start via message mention
- follow-up routing in Discord threads or reply context

Invariant:

- both frontends must normalize conversation continuation into the same master
  routing concept

### 3. Thread Identity

Slack:

- uses `thread_ts`
- message identity is timestamp-based

Discord:

- uses message/thread IDs (snowflakes)
- threads and replies have different native primitives from Slack

Invariant:

- master tracks thread continuity using a normalized per-platform thread key

### 4. Attachment Extraction

Slack:

- private files may require authenticated download
- file objects expose Slack-specific metadata and private URLs

Discord:

- attachments expose public CDN URLs and Discord-specific attachment objects

Invariant:

- both frontends must normalize supported attachments before routing
- supported images/documents should enter the same request-manifest flow
- platform-specific download details must not leak into the master-agent prompt
  contract

### 5. Response Formatting

Slack:

- Slack markdown constraints
- channel/thread reply primitives

Discord:

- Discord markdown constraints
- interaction responses and message replies
- routed prompt acknowledgements may include platform-native controls such as
  the `Detail` button that shows the recorded agent command

Invariant:

- master returns logical result text
- frontend adapter is responsible for final platform-safe emission
- debug controls must read normalized master/router metadata and avoid changing
  prompt routing semantics

## Command Interface Contract

The frontend-to-master command contract includes:

- `/master-agent-list`
- `/master-agent-load`
- `/master-agent-start`
- `/master-agent-stop`
- `/master-agent-status`
- `/master-agent-usage`
- `/master-agent-remove`
- `/master-agent-refresh-auth`
- `/master-agent-refresh-config`
- `/master-agent-set-model`
- `/master-agent-set-subagent`

Frontend adapters must:

- preserve command intent and arguments
- enforce platform admin-channel policy before execution
- render success/error responses in platform-appropriate formatting

## Prompt Routing Contract

For mapped non-admin channels:

1. frontend receives mention or follow-up event
2. frontend extracts normalized text and attachments
3. master validates channel mapping and thread continuity
4. master stages attachments into request storage when needed
5. master dispatches the prompt to the selected agent
6. frontend emits the returned response in the originating platform

## Platform Difference Table

| Topic | Slack | Discord | Master-facing invariant |
| --- | --- | --- | --- |
| Command transport | Slash commands | Application commands | Same logical `/master-agent-*` commands |
| Conversation start | App mention | Bot mention | Same routed-prompt behavior |
| Follow-up continuity | `thread_ts` | thread/reply IDs | Same tracked-thread semantics |
| Attachment source | Slack file objects and private URLs | Discord attachment objects and CDN URLs | Same normalized attachment classes |
| Reply transport | thread reply / command response | interaction response / reply | Same logical response payload |
| Dispatch detail control | Not currently exposed | `Detail` button on routed-message acknowledgement | Reads the recorded command for the routed prompt |
| Admin allowlist | `MASTER_ADMIN_CHANNELS` | `DISCORD_ADMIN_CHANNELS` | Same policy boundary |

## Implemented Difference Boundaries

The frontend adapters own:

- raw event parsing
- platform-specific command registration
- attachment download metadata extraction
- final reply formatting and emission

Master owns:

- normalized routing policy
- thread tracking
- request staging
- agent selection
- admin command execution

## Invariants

The frontend-master interface must preserve these invariants:

- one normalized internal command model
- one normalized internal prompt model
- platform-specific quirks handled before reaching master core
- admin command parity across Slack and Discord
- attachment normalization before master-agent dispatch

## Out of Scope

This document does not define:

- the internal agent runtime contract
- Slack or Discord app installation steps
- release or deployment workflow
