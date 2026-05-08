# Lessons Learned

Append-only log. Each entry: date, summary, root cause, fix applied, prevention.

<!-- last updated: 2026-05-08 -->

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

## 2026-03-24 — docs/knowledge-base directory initialised

*Summary:* The project `CLAUDE.md` references [`docs/knowledge-base/lessons-learned.md`](lessons-learned.md) and [`docs/knowledge-base/faq.md`](faq.md) as required knowledge-persistence targets, but neither file nor the directory existed.

*Root cause:* The document layout was defined in v3.4 as a target structure; no initialisation step created the required stubs.

*Fix applied:* Created `docs/knowledge-base/`, `docs/guides/runbooks/`, `docs/references/`, and `docs/manuals/` with initial stub files during v3.4 doc-writer pass.

*Prevention:* When a new document layout is defined in CLAUDE.md, include a chore commit that scaffolds the required directories and stubs so agents can immediately write to them without a missing-file error.
