# Lessons Learned

Append-only log. Each entry: date, summary, root cause, fix applied, prevention.

<!-- last updated: 2026-05-18 -->

---

## 2026-05-18 — Second SIGKILL of agent container on same workspace; pre-flight signal handler added to distinguish SIGTERM-escalation vs. direct SIGKILL

*Summary:* Prod message `587031aa` in topic `7c4f67e9` (same workspace `e6d8e527` as the 2026-05-17 cbc02192 incident) was cut off mid-response at 00:56:00 UTC. v4.15-rc6 worked as designed: the agent self-labeled the message `interrupt_reason=agent-killed`, master log carried `agent.startup_inherited_active count=2 message_ids=[ad22b3cc-…, 587031aa-…]`, and the dockerd journal confirmed `exitCode=137 manualRestart=false`. All ruleouts from the runbook §2 Steps 1–6 came back clean (no OOM, no master `idle_stop`, no CD deploy, no other container restart in the same minute, no `item.started` without matching `item.completed` in the transcript). The kill happened ~57 s after a healthy `agent.pong … alive=True` at 00:55:03 — no exception, no warning, no shutdown trace.

*Root cause:* Same as 2026-05-17 — unidentified external SIGKILL. Two incidents in 24 h on the same workspace is a pattern, not coincidence. Reviewed `docker-compose.staging.yml` (the file used in prod): master is capped at `memory: 1g cpus: 1.0`; mosquitto at `128m / 0.25`; **agent containers have no resource limits at all** because they're spawned imperatively by `src/master/agent_runner.py:spawn_agent` with no `mem_limit` argument. That rules out cgroup-OOM on the agent (host kernel log also clean).

*Fix applied:*
- **v4.15-rc7** — `src/agent/main.py` now installs a pre-flight diagnostic signal handler for SIGTERM/SIGINT/SIGHUP/SIGQUIT before `run_worker` is reached. Each captured signal logs `agent.signal_received signum=N name=<NAME> phase=pre-mqtt-loop` immediately, then raises `SystemExit(128+signum)`. The MQTT loop's existing `_sigterm_handler` (which also publishes interrupt events) replaces this in-place once it registers. Tests in `tests/agent/test_main.py` verify all four signals are wired, unknown signums don't crash the handler, unsupported signals on the platform are swallowed, and `main()` installs the handler before `run_worker`.
- **Runbook §2 Step 7** updated: now distinguishes "signal was delivered to Python" (`agent.signal_received` present → hunt the SIGTERM source) from "Python never got a chance" (line absent → direct SIGKILL or signal-to-tini-PID-1). Renamed the old "things that bypass" content to §2 Step 8.

*Prevention:* The next incident of this shape will tell us *which* of three branches it's on: (a) Python saw SIGTERM and was escalated by tini, (b) Python saw a non-SIGTERM signal, or (c) Python saw nothing (direct SIGKILL or signal to PID 1). Each branch has a different next-step investigation in the updated runbook. Open questions for follow-up if the pattern continues:
- Should agent containers carry an explicit `mem_limit` so future OOMs are *visible* (`docker inspect OOMKilled=true`) rather than ambiguous? Currently no limit means OOM is host-wide-only and our diagnostic is "kernel journal."
- Should we bump tini's `stop_grace_period` (currently default 10 s) so a legitimate SIGTERM has time for the Python shutdown handler to run to completion before tini escalates?

---

## 2026-05-17 — Agent container SIGKILL'd mid-stream surfaces as `ping-timeout` with no diagnostic

*Summary:* Prod message `cbc02192` in topic `7c4f67e9` was cut off mid-response. Master labeled it `interrupt_reason=ping-timeout`, which read as "agent unresponsive" — misleading. The actual sequence: agent container was SIGKILL'd at 08:48:03 while codex was running `git worktree add origin/pr/219`, Docker's `restart: unless-stopped` policy bounced it ~1s later, master pinged the fresh agent 4s after that, the new agent had no record of the message (in-memory `_active_procs` is empty), so it returned `alive=False`. None of the existing interrupt paths (SIGTERM handler, master `stop_agent`, container-gone detection) fit, because SIGKILL bypasses Python entirely and the container *was* running by the time master checked.

*Root cause:* Two compounding gaps.
1. **Visibility:** `docker inspect` reports the post-restart `ExitCode=0` regardless of what killed the previous run. The ground-truth `exitCode=137` only appears in `journalctl -u docker`. Operators tracking through `docker inspect` will be misled into ruling out a kill.
2. **Recovery:** `_active_procs` was Python-memory-only. SIGKILL → restart-policy bounce loses the entire in-flight set with no signal to master beyond an opaque `alive=False`.

*Fix applied:*
- **v4.15-rc5 (`f36eee3`)** — `LOGGER.exception` in the `except Exception` branches of `_stream_claude_once` / `_stream_codex_once` carrying `pid`, `returncode`, `chunks`, `exc_type`, `exc`, `stderr_tail[-500:]`. Non-zero proc exits now emit `agent.*_subprocess_nonzero_exit` even when partial output was produced. `agent.llm_done`/`agent.codex_done` now include `message_id`/`pid`/`returncode`. `agent.pong … alive=False` now includes `active_procs_size`.
- **v4.15-rc6 (`8b5fa1d`)** — Atomic snapshot of `_active_contexts` written to `/tmp/master-agent/active_procs.json` on every mutation under `_active_procs_lock`. On agent startup (`_on_connect` after subscribe), `_publish_inherited_interrupts` reads the snapshot and emits `(message interrupted)` with new `interrupt_reason=agent-killed` for every leftover entry, then unlinks the file. SIGTERM handler (`_publish_interrupted_all`) also clears the file so a clean stop+restart doesn't double-publish. UI badge added for `agent-killed`.
- **Runbook** — `docs/guides/runbooks/agent-message-cutoff.md` documents the §1–§4 decision tree.

*Prevention:* Two angles.
1. Future incidents of the same shape now self-label as `agent-killed` and leave a marker (`agent.startup_inherited_active`) in the agent log. That's enough to confirm a SIGKILL+restart vs. other interrupt modes without paging the host journal.
2. The kill *source* was never positively identified for the 2026-05-17 case — ruled out kernel OOM, master stop_agent, CD daemon, host-wide event, cron. Most plausible remaining hypothesis is tini's SIGTERM→SIGKILL escalation, but no SIGTERM source was located either. If the same pattern recurs with the new logs in place, the additional context (which message_ids, what codex was doing per the transcript, exact dockerd journal line) should narrow it.

---

## 2026-05-09 — Codex CLI API is not documented; must be inferred from the binary

*Summary:* The `@openai/codex` CLI surface changed between the npm-published JS wrapper and the actual Rust binary it vendors. Documentation online (and model training data) describes the old JS-era flags (`--approval-mode full-auto`, `--output-format stream-json`) that no longer exist in the Rust binary shipped with v0.128.0. Using those flags causes silent failure with no output.

*Root cause:* The npm package (`@openai/codex`) is a thin JS launcher that spawns a vendored platform-specific Rust binary. The Rust binary has its own subcommand structure (`codex exec`) and flag set that is not published in any README or man page. The `--help` output is the only authoritative source.

*Fix applied:* Determined correct flags by running `codex exec --help` inside the container, then confirmed event type names by extracting strings from the Rust binary with `grep -oaE '[a-z]+\.[a-z_]+' <binary>`. Final correct invocation:
```
codex exec --json \
  --dangerously-bypass-approvals-and-sandbox \
  -s danger-full-access \
  --ephemeral \
  -o <tempfile> \
  [-m <model>] \
  <prompt>
```
Output events are JSONL on stdout. The canonical final-output field is `turn.completed.output_text` (with `last_message` as fallback). The `-o <tempfile>` flag writes the final answer to a file, which is more reliable than parsing stdout when the process exits quickly.

*Prevention:* When integrating any CLI tool whose npm package is a wrapper around a native binary, always run `<binary> --help` directly rather than reading the npm README. Check flag existence before writing code — assume nothing is backward-compatible across major rewrites.

---

## 2026-05-08 — SRE workflow fully onboarded and validated

*Summary:* The SRE subagent completed end-to-end onboarding of the containerized dev/test/staging workflow. All infrastructure files are in place, documented, and validated. Remote Docker support verified with DOCKER_HOST and DOCKER_GID environment variable handling. No blocking issues found.

*Root cause:* N/A — this is a successful validation of existing infrastructure set up in prior audit.

*Fix applied:* Enhanced three SRE scripts (env-up.sh, env-down.sh, test.sh) with explicit documentation of required environment variables (DOCKER_HOST, DOCKER_GID). Added defensive warning in test.sh when remote Docker is used without DOCKER_GID.

*Prevention:* The enhanced script documentation ensures operators understand the dependency chain: when DOCKER_HOST is set to an SSH URL, DOCKER_GID must also be set so the compose file can map permissions correctly via the docker group. The warning prevents silent failures and permission errors in container startup.

**Validated components:**
- All .sre/ scripts present and executable (env-up.sh, env-down.sh, test.sh, setup-repo-protection.sh)
- Production Dockerfile hardened (non-root user, HEALTHCHECK, pinned base image)
- Dev/test Dockerfiles configured for live-reload and containerized testing
- Three compose files (base, override, ci) with correct topology and no :latest tags in prod
- Complete documentation: guides, onboarding summary, decision records, lessons-learned
- Remote Docker support with proper group_add and fallback defaults
- Secrets handling via .env.local (per-branch, gitignored)
- Test infrastructure ready (docker-compose.ci.yml + Dockerfile.test)

---

## 2026-05-05 — CD daemon fails with `ModuleNotFoundError: No module named 'src'`

*Summary:* The CD daemon container started with `/usr/local/bin/python: Error while finding module specification for 'src.cd.main' (ModuleNotFoundError: No module named 'src')` immediately on startup.

*Root cause:* `docker-compose.cd-daemon.example.yml` mounted the host project directory directly over `/opt/codex-slack` — the same path where the image bakes in the `src/` package. The volume mount shadowed the entire directory, hiding `src/` from Python's module search path.

*Fix applied:* Changed the volume mount target from `/opt/codex-slack` to `/opt/codex-slack-host`, and updated the defaults for `CD_COMPOSE_FILE`, `CD_ENV_FILE`, and `CD_STATE_FILE` to point to the new mount path. Also updated the image variable from `MASTER_RUNTIME_IMAGE` to `CD_DAEMON_IMAGE` (with fallback) to align with the operator's `.env` convention.

*Prevention:* When a container mounts an external directory over a path that also contains baked-in code (`COPY … ./src`), the code is silently hidden. Always mount external config/data to a path that does not overlap with the image's working directory.

---

## 2026-05-02 — v3 bug triage: three bugs fixed

*Summary:* Bug triage of pre-v3 issues against the v3 codebase revealed three actionable bugs: (A) `Dockerfile.agent-minimal` was missing the `/home/appuser/.claude` pre-creation fix applied to `Dockerfile`, causing agent session volumes to be root-owned and unwritable; (B) `_respawn_agents` in `main.py` was respawning archived workspace containers on master restart; (C) `send_message` in `messages.py` accepted messages to archived workspaces/topics whose containers had already been stopped.

*Root cause:* (A) A targeted fix was applied to one Dockerfile but not the other. (B)(C) Archived-at filtering was added for workspace CRUD but not carried through to `_respawn_agents` or `send_message`.

*Fix applied:* Added `mkdir -p /home/appuser/.claude` to `Dockerfile.agent-minimal`; added `AND archived_at IS NULL` guards to `_respawn_agents` SQL and both workspace/topic lookups in `send_message`.

*Prevention:* When a soft-delete pattern (`archived_at`) is added, audit every query that touches the affected tables to ensure the filter is applied everywhere — not just in the CRUD layer.

---

## 2026-05-05 — Multiple final responses from Claude CLI were silently dropped

*Summary:* When the Claude CLI produces more than one `result` event in a single run (e.g. a main response followed by a background-task completion notification), the frontend only showed the last one. The first response was silently discarded.

*Root cause:* `_run_claude_once` in `src/agent/mqtt_loop.py` assigned `output = event.get("result")` inside the `result`-event loop, overwriting on each iteration. Only the final result event's text survived.

*Fix applied:* Changed to collect all result texts into a list (`outputs`) and join them with `"\n\n---\n\n"` after the loop. Also changed `is_error` to accumulate with `or` so any error in any turn is preserved.

*Prevention:* When iterating over a stream of events and extracting a single value, check whether multiple occurrences of the target event type are possible. If so, accumulate rather than overwrite.

---

## 2026-05-02 — `--verbose` required for stream-json `result` event (v3 slices 3–4)

*Summary:* The claude-code agent returned `(no output)` for all LLM turns, and no `session_id` was ever stored in the `sessions` table. Agent responses appeared empty in the UI.

*Root cause:* `claude --output-format stream-json` without `--verbose` does not emit the `result` event, which is the only event that carries `result`/`last_response` and `session_id`. The required flag combination is `--print --verbose --output-format stream-json`.

*Fix applied:* Updated `_run_claude_once` in `src/agent/mqtt_loop.py` to always include `--print --verbose --output-format stream-json`.

*Prevention:* When using `stream-json` output format with the claude CLI, always include `--verbose`. Document this in the config reference under agent LLM CLI invocations.

---

## 2026-05-02 — Claude session volume ownership on first container start (v3 slice 5)

*Summary:* Agent container failed at startup with permission errors accessing `/home/appuser/.claude` on fresh deployments.

*Root cause:* The named Docker volume `codex-claude-{workspace_id}` is created by the Docker daemon owned by `root`. The agent container runs as `appuser` (UID 1000). The volume mount shadows the image-layer directory, exposing the root-owned volume directory instead of the `appuser`-owned one.

*Fix applied:* Changed the Dockerfile so `chown -R appuser:appuser /home/appuser/.claude` happens as root before the `USER appuser` switch. The volume inherits the correct ownership on first use.

*Prevention:* When mounting a named volume over a directory that the container process must own, ensure `RUN chown` happens as root in the Dockerfile before the `USER` switch.

---

## 2026-05-02 — Expired Claude sessions must auto-retry without `--resume` (v3 slices 6–7)

*Summary:* After a master restart or a long idle period, the agent failed all prompts in a topic with `No conversation found with session ID`.

*Root cause:* Claude Code expires sessions after inactivity. The `sessions` table retained the old `llm_session_id`, and the agent always passed `--resume` if a session ID was present.

*Fix applied:* `_run_claude` in `src/agent/mqtt_loop.py` inspects output for `No conversation found with session ID`. If found and `--resume` was used, it retries without `--resume`. The new session ID is published in the response payload and master updates the `sessions` table.

*Prevention:* Never assume a stored LLM session ID is still valid. Always implement an expired-session retry path for resumable LLM CLIs.

---

## 2026-05-02 — Soft-delete `archived_at IS NULL` filter must be applied consistently (v3 slices 10–12)

*Summary:* Queries returned archived workspaces or topics in active-only contexts (workspace list, topic create workspace check, respawn loop).

*Root cause:* The `archived_at` column was added incrementally and was not applied to every query that should filter on active records.

*Fix applied:* Audited all SQL in `workspaces.py`, `topics.py`, `messages.py`, `agents.py`, and `main.py`. Added `AND archived_at IS NULL` consistently to all active-record lookups. Added `_fetch_workspace_any` (no archived filter) for read paths that must work on both active and archived records.

*Prevention:* When adding a soft-delete column, apply `IS NULL` filters to all affected queries in the same commit. Code-review soft-delete migrations specifically for missed WHERE clauses.

---

## 2026-05-02 — Container vs. volume naming convention (v3 slice 5)

*Summary:* Early docs used `codex-claude-{workspace_id}` for both the container name and the Claude volume. The implementation uses different prefixes.

*Root cause:* Design doc was written before implementation. The naming diverged during coding.

*Fix applied:* Clarified in all docs:
- Container name: `codex-agent-{workspace_id}` (from `agent_runner.container_name()`)
- Claude session volume: `codex-claude-{workspace_id}` → `/home/appuser/.claude`

*Prevention:* Verify naming against `agent_runner.py` before writing operational docs.

---

## 2026-05-02 — Documentation was stale across all v3 slices (v3 slice 12 doc pass)

*Summary:* `docs/references/api.md`, `docs/references/config.md`, `docs/guides/onboarding.md`, `docs/manuals/ops-manual.md`, `docs/manuals/user-manual.md`, `docs/guides/runbooks/master-agent.md`, and the design/ADR docs all described the pre-v3 Slack/Discord bot architecture or early v3 design proposals.

*Root cause:* Documentation was not updated during slice development; the doc-writer pass was deferred to slice 12.

*Fix applied:* Full documentation rewrite in slice 12 based on source code verification.

*Prevention:* Each slice should include a documentation sub-task updating the directly affected reference pages before the PR merges.

---

## 2026-05-05 — Vendor MIME subtype dots produce multi-dot extensions in filename generation

*Summary:* When generating filenames for clipboard-pasted images, vendor MIME types such as `image/vnd.microsoft.icon` contain dots in the subtype. A naive regex that includes `.` in its character class (e.g. `[a-z0-9.+-]+`) extracts the entire subtype including dots, producing malformed extensions like `.vnd.microsoft.icon` instead of falling back to a safe default.

*Root cause:* The `mimeToExt` fallback regex in the paste-image handler originally used `[a-z0-9.+-]+` for the subtype portion, which allowed dot characters through and incorporated them directly into the generated filename extension.

*Fix applied:* Removed `.` from the regex character class, changing it to `[a-z0-9+-]+`. Subtypes containing dots no longer match, causing the function to fall back to `'png'` as the safe default extension.

*Prevention:* When extracting file extensions from MIME type subtypes, restrict the character class to `[a-z0-9+-]` (no dots). Vendor MIME types with dotted subtypes should not produce dotted extensions — always fall back to a sensible default for unrecognised subtypes.

---

## 2026-05-05 — CD daemon fails with `No module named 'src'` because volume mount shadows baked source

*Summary:* `docker compose -f docker-compose.cd-daemon.example.yml up -d` caused the daemon to crash immediately with `ModuleNotFoundError: No module named 'src'`. The image appeared to build correctly.

*Root cause:* `Dockerfile.cd-daemon` copied `src/` into `/opt/codex-slack/src/` and set `WORKDIR /opt/codex-slack`. The compose file mounts `${MASTER_PROJECT_DIR:-.}` over `/opt/codex-slack`. This volume mount shadows everything baked into that path, including the `src/` package, so Python cannot find the module at runtime.

*Fix applied:* Moved the `COPY` targets in `Dockerfile.cd-daemon` to `/app/src/` (outside the volume mount point) and added `ENV PYTHONPATH=/app`. `WORKDIR` remains `/opt/codex-slack` so compose commands still resolve paths relative to the project root.

*Prevention:* Never bake application source into a path that is volume-mounted at runtime. Use a dedicated source directory (`/app`, `/usr/local/lib/<name>`, etc.) that is disjoint from any host-mounted paths, and expose it via `PYTHONPATH` or an install step.

---

## 2026-05-05 — Production /health reports RC tag, not release tag

*Summary:* After a release promotion, production containers report a version string like `v4.0-rc3` in `/health` and startup logs, not `v4.0`. On-call responders initially suspected a bad deploy.

*Root cause:* `promote-release.yml` promotes a build to production by retagging the RC image without rebuilding it. The `APP_VERSION` environment variable is baked at RC build time, so the running container retains the RC string regardless of the Docker image tag it was launched from.

*Fix applied:* This is intentional behaviour, not a bug. Rebuilding on promote would break the "bit-identical to UAT-approved RC" invariant. The ops manual (`docs/manuals/ops-manual.md`, Version display section) now documents this explicitly.

*Prevention:* When triaging an incident, a `version` field ending in `-rc<N>` in production is expected and does not indicate a misdeployment. The RC string identifies which UAT-approved build is running. Check `promote-release.yml` history to confirm which RC was promoted if a cross-reference is needed.

---

## 2026-05-05 — 5-minute master image build: Debian npm bloat + missing GHA cache scopes

*Summary:* The `build-rc.yml` master image build took ~5 minutes per run. The dominant bottleneck was a 62-second `apt-get install nodejs npm` step caused by the Debian `npm` metapackage pulling in ~200 extraneous packages (webpack, eslint, babel, and the full Debian node ecosystem). Secondary issues were absent GHA cache scopes (causing `build-rc.yml` and `ci-pr.yml` to overwrite each other's layer caches) and sequential master + agent-minimal builds in `build-rc.yml` that could run in parallel.

*Root cause:* The Debian `npm` package has deep dependency chains into the Debian-packaged node ecosystem. Installing `nodejs npm` via apt on Debian trixie installs hundreds of unneeded packages. Without explicit `scope=` parameters on `type=gha` cache entries, all workflows share one undifferentiated cache bucket; a push from `build-rc.yml` evicts layers written by `ci-pr.yml` and vice versa.

*Fix applied:* (1) Both `Dockerfile` and `Dockerfile.agent-minimal` now install Node 20 from NodeSource (`curl -fsSL https://deb.nodesource.com/setup_20.x | bash -` followed by `apt-get install nodejs`). The NodeSource `nodejs` package bundles npm — the separate `npm` apt package is removed. (2) All `cache-from` and `cache-to` entries in every docker-related workflow now carry explicit scopes: `scope=master` for the master image and `scope=agent-minimal` for the agent-minimal image. (3) `build-rc.yml` was restructured from one sequential job into three parallel-friendly jobs (`build-master`, `build-cd-daemon`, `build-agent-minimal`, `summary`) so all images build and push simultaneously.

*Prevention:* Never install Node.js via the Debian `nodejs`/`npm` apt packages in a lean Docker image — use NodeSource or a similar vendor-maintained binary distribution. Always add explicit `scope=` to GHA cache entries when more than one workflow writes to the same cache type. When two independent Docker image builds appear in the same workflow job, split them into separate jobs to exploit parallel runners.

---

## 2026-05-06 — Agent containers spawned on wrong network when Docker project name differs from default

*Summary:* Agent containers started by the master were unreachable from the MQTT broker (mosquitto) and from each other. The agents would connect to `localhost:1883` and immediately fail with `Connection refused`.

*Root cause:* `MASTER_AGENT_NETWORK` defaults to `codex-slack_internal` — a network name derived from the Compose project name `codex-slack`. When the project is started with a custom project name (e.g. `docker compose -p myproject up`) or the working directory slug differs, Docker creates a network named `myproject_internal` instead. Agent containers attached to the wrong network are isolated from the broker.

*Fix applied:* Set `MASTER_AGENT_NETWORK` explicitly in `.env` to match the actual network name created by Compose (visible via `docker network ls`). Alternatively, pin the Compose project name with `COMPOSE_PROJECT_NAME=codex-slack` in `.env` so the default network name is always predictable.

*Prevention:* When the default value of `MASTER_AGENT_NETWORK` is derived from a Compose project name, document that the value must match the actual network created by `docker compose`. Always verify with `docker network inspect <network>` that agent containers and the broker share the same network before debugging connectivity further.

---

## 2026-05-06 — Docker socket on macOS is root:root; master container needs group_add or socket override

*Summary:* The master container could not reach the Docker daemon on macOS (`docker.from_env()` raised `PermissionError: [Errno 13] Permission denied: '/var/run/docker.sock'`), preventing agent container spawn and stop.

*Root cause:* On macOS Docker Desktop, `/var/run/docker.sock` inside a container is owned by `root:root` with mode `0660`. The master image runs as `appuser` (UID 1000), which is not in the `root` group (GID 0). The result is a permission error on every Docker API call.

*Fix applied:* Added `group_add: ["0"]` (root GID) to the master service in the Compose file, or alternatively changed the socket bind-mount to the Docker Desktop user socket (`~/.docker/run/docker.sock`) where permissions are more permissive. The `group_add` approach is preferred because it does not depend on the socket path differing between Docker Desktop and Linux Docker.

*Prevention:* When a container needs access to the Docker socket on macOS, always add `group_add: ["0"]` to the Compose service definition, or use a socket proxy (e.g. `tecnativa/docker-socket-proxy`) to restrict permissions. Do not assume the socket is world-writable or that the running user's GID matches the socket GID.

---

## 2026-05-06 — pip deps baked as appuser make pytest unreachable to tini exec

*Summary:* The agent container's `tini` entrypoint could not exec `pytest` during test runs: `exec: "pytest": executable file not found in $PATH`. The package was installed, but only in `/home/appuser/.local/bin/`, which was not on `PATH` for the exec context.

*Root cause:* The Dockerfile ran `pip install --user` as `appuser`, placing pytest and other console-scripts into `/home/appuser/.local/bin/`. `tini` execs the command with the process environment, which at container startup includes `PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`. `/home/appuser/.local/bin` is not in that default PATH, so `tini` cannot find the script.

*Fix applied:* Added `ENV PATH="/home/appuser/.local/bin:${PATH}"` to the Dockerfile after the `USER appuser` line, ensuring the user-local bin directory is on PATH for both interactive shells and tini-exec'd commands.

*Prevention:* When installing Python packages with `pip install --user` as a non-root user in a Dockerfile, always add the user's `.local/bin` to `ENV PATH` explicitly. Alternatively, install into the system site-packages as root (remove `--user`), which avoids the PATH issue entirely at the cost of needing a `USER root` block during install.

---

## 2026-05-08 — SQLite `column = NULL` never matches; must use `IS NULL`

*Summary:* Event actions with `timing=None` (i.e. `topic_archived` and `topic_message_received` rows stored with `timing` as SQL NULL) were silently never dispatched when those events fired. No error was logged; the worker simply found zero matching rows for every event of those types.

*Root cause:* The `_handle_event` worker query used `AND timing=?` with a Python `None` value bound as the parameter. In SQLite, `NULL = NULL` evaluates to NULL (not TRUE), so the predicate `timing = NULL` never matches any row, even when the column value is NULL. The query returned an empty result set for all actions where `timing IS NULL`, regardless of whether the event type was correct.

*Fix applied:* Changed the WHERE clause to `AND (timing IS NULL OR timing=?)`. This correctly matches rows where `timing` is NULL (scheduler, archived, received actions) and rows where `timing` equals the event's timing value (before/after for `topic_message_sent`). The fix is in `src/master/event_dispatcher.py:_handle_event`.

*Prevention:* Never use `= NULL` or `!= NULL` in SQL. SQL NULL comparisons require `IS NULL` or `IS NOT NULL`. When a column is legitimately nullable and you need to match rows where it is null, always write `column IS NULL` or `(column IS NULL OR column = ?)`. Code review for any query that binds a Python `None` as a SQL parameter for a non-primary-key column should check whether the intent is an equality test (wrong for NULL) or an IS NULL test (correct).

---

## 2026-05-08 — Pydantic v2 default `None` collapses "omitted" and "explicit null" in PATCH bodies

*Summary:* The PATCH handler for event actions could not distinguish between a client omitting the `timing` field and a client explicitly sending `"timing": null`. Both appeared as `None` in the model, so a PATCH that only updated `staff_name` would silently reset `timing` to null, invalidating `topic_message_sent` rows that require a non-null `timing`.

*Root cause:* In Pydantic v2, a field declared as `timing: Literal["before","after"] | None = None` initialises to `None` whether the field is absent from the JSON body or present with a null value. The handler was reading `body.timing` directly, making it impossible to distinguish "client did not send this field" from "client explicitly nulled it".

*Fix applied:* Changed the PATCH handler to use `body.model_dump(exclude_unset=True)` to obtain only the fields actually present in the request body. The merged state is then constructed by layering the sent fields over the existing database row values, and the combined state is validated for field-combination consistency before the UPDATE. This ensures that omitted fields are never mutated and explicit nulls are applied only when the field was actually sent.

*Prevention:* In any Pydantic v2 PATCH endpoint where field omission and explicit null carry different semantics, always use `model_dump(exclude_unset=True)` to extract the sent fields. Do not read model attributes directly — they collapse the distinction. This pattern applies broadly to any partial-update endpoint.

---

## 2026-03-24 — docs/knowledge-base directory initialised

*Summary:* The project `CLAUDE.md` references [`docs/knowledge-base/lessons-learned.md`](lessons-learned.md) and [`docs/knowledge-base/faq.md`](faq.md) as required knowledge-persistence targets, but neither file nor the directory existed.

*Root cause:* The document layout was defined in v3.4 as a target structure; no initialisation step created the required stubs.

*Fix applied:* Created `docs/knowledge-base/`, `docs/guides/runbooks/`, `docs/references/`, and `docs/manuals/` with initial stub files during v3.4 doc-writer pass.

*Prevention:* When a new document layout is defined in CLAUDE.md, include a chore commit that scaffolds the required directories and stubs so agents can immediately write to them without a missing-file error.
