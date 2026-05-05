# Test Plan: Version Number Display

- **Feature design:** [docs/decisions/0011-version-display.md](../decisions/0011-version-display.md)
- **Date:** 2026-05-05
- **Branch:** `topic/version-display-5f4afa2`
- **Components under test:** `src/version.py`, `src/master/main.py` (`GET /health`), `src/master/main.py` (startup log), `src/agent/main.py` (startup log), `src/cd/daemon.py` (startup log), all three Dockerfiles, relevant CI workflow files

---

## 1. Scope

### In scope

- `get_app_version()` helper in `src/version.py` — all env-var input variations.
- `GET /health` response body — presence and correctness of the `version` field.
- Master startup log — presence of `version=<ver>` in the `master.startup` log line.
- Agent startup log — presence of the new `agent.startup version=<ver>` banner.
- CD daemon startup log — presence of `version=<ver>` in the `cd.daemon_start` log line.
- Dockerfile `ARG APP_VERSION=dev` / `ENV APP_VERSION` injection for all three Dockerfiles.
- CI workflow `--build-arg APP_VERSION=...` for `build-rc.yml`, `build-on-demand.yml`, `publish-cd-daemon.yml`, `publish-master.yml`, `publish-agent-minimal.yml`.
- Operational awareness: production images display the RC string of the promoted build, not the release string.

### Out of scope

- A separate `/version` endpoint (deferred per ADR 0011).
- Version field on agent or CD daemon HTTP endpoints (neither exposes HTTP).
- Frontend SPA version badge (deferred per ADR 0011).
- Prometheus / metrics label for version (deferred per ADR 0011).
- Rebuild-on-promote to show `v<major>.<minor>` instead of `v<major>.<minor>-rc<N>` (rejected per ADR 0011).

---

## 2. Test Environment Prerequisites

- Python 3.11+ with `pytest`, `fastapi`, `httpx` installed (see `requirements.txt`).
- For UAT cases involving Docker builds: Docker CLI ≥ 24 available on the host.
- For live startup log UAT cases: a running testbed deployment reachable over `DOCKER_HOST`.
- For CI workflow cases: GitHub Actions access or the ability to inspect workflow YAML diffs.

---

## 3. Test Cases

### TC-01: `get_app_version()` — APP_VERSION set to valid string (automated)

**Type:** unit
**File:** `tests/test_version.py::TestGetAppVersion::test_env_var_set_returns_value`

**Precondition:** `APP_VERSION=v4.0-rc1` in the process environment.

**Expected result:** `get_app_version()` returns `"v4.0-rc1"`.

**Pass criteria:** Return value equals the env-var string exactly. No transformation or stripping that alters a valid non-whitespace value.

---

### TC-02: `get_app_version()` — APP_VERSION not set (automated)

**Type:** unit
**File:** `tests/test_version.py::TestGetAppVersion::test_env_var_unset_returns_dev`

**Precondition:** `APP_VERSION` is absent from the process environment.

**Expected result:** `get_app_version()` returns `"dev"`.

**Pass criteria:** Return value is the string `"dev"`.

---

### TC-03: `get_app_version()` — APP_VERSION set to empty string (automated)

**Type:** unit
**File:** `tests/test_version.py::TestGetAppVersion::test_env_var_empty_string_returns_dev`

**Precondition:** `APP_VERSION=""` in the process environment.

**Expected result:** `get_app_version()` returns `"dev"`.

**Pass criteria:** The empty string is treated as absent; fallback `"dev"` is returned.

---

### TC-04: `get_app_version()` — APP_VERSION set to whitespace only (automated)

**Type:** unit
**File:** `tests/test_version.py::TestGetAppVersion::test_env_var_whitespace_returns_dev`

**Precondition:** `APP_VERSION="  "` (spaces only) in the process environment.

**Expected result:** `get_app_version()` returns `"dev"`.

**Pass criteria:** Whitespace-only values are stripped to empty, triggering the `"dev"` fallback.

---

### TC-05: `GET /health` — version field present when APP_VERSION set (automated)

**Type:** integration (FastAPI TestClient)
**File:** `tests/test_health.py::TestHealthVersion::test_health_returns_version_when_app_version_set`

**Precondition:** `APP_VERSION=test-1.2.3` set in the test environment; master application started via `TestClient`.

**Expected result:** `GET /health` returns HTTP 200 with body `{"status": "ok", "version": "test-1.2.3"}`.

**Pass criteria:** Status code 200; JSON body contains both fields with correct values.

---

### TC-06: `GET /health` — version field falls back to "dev" when APP_VERSION unset (automated)

**Type:** integration (FastAPI TestClient)
**File:** `tests/test_health.py::TestHealthVersion::test_health_returns_dev_when_app_version_unset`

**Precondition:** `APP_VERSION` is absent from the test environment; master application started via `TestClient`.

**Expected result:** `GET /health` returns HTTP 200 with body `{"status": "ok", "version": "dev"}`.

**Pass criteria:** Status code 200; `version` field is `"dev"`.

---

### TC-07: `GET /health` — response contains exactly "status" and "version" keys (automated)

**Type:** integration (FastAPI TestClient)
**File:** `tests/test_health.py::TestHealthVersion::test_health_response_contains_exactly_status_and_version`

**Precondition:** `APP_VERSION=v4.0-rc1` set; master application started via `TestClient`.

**Expected result:** The JSON response has exactly two keys: `"status"` and `"version"`. No extra fields.

**Pass criteria:** `set(body.keys()) == {"status", "version"}`.

---

### TC-08: `GET /health` — RC string is surfaced verbatim (automated)

**Type:** integration (FastAPI TestClient)
**File:** `tests/test_health.py::TestHealthVersion::test_health_rc_string_passed_through_unchanged`

**Precondition:** `APP_VERSION=v4.0-rc3` set; master application started via `TestClient`.

**Expected result:** `GET /health` returns `{"status": "ok", "version": "v4.0-rc3"}`. The RC string is not translated to a release string.

**Pass criteria:** `version` field equals `"v4.0-rc3"` exactly.

---

### TC-09: Master startup log contains version field (needs-human)

**Type:** UAT — log inspection
**Verification method:** Inspect container logs after deploy.

**Precondition:** Master container deployed from an image built with `APP_VERSION=<tag>`.

**Steps:**
1. On the testbed host, run: `docker logs <master-container-name> 2>&1 | head -30`
2. Locate the `master.startup` log line.

**Expected result:** The `master.startup` line includes a `version=<tag>` field as the first interpolated value.

**Example:** `master.startup version=v4.0-rc1 data_dir=... container_runtime=...`

**Pass criteria:** The `version=` field is present and matches the image tag used at build time. Fail if the field is absent or shows `dev` for a tagged build.

---

### TC-10: Agent startup log contains version banner (needs-human)

**Type:** UAT — log inspection
**Verification method:** Inspect agent container logs after startup.

**Precondition:** Agent container deployed from an image built with `APP_VERSION=<tag>`.

**Steps:**
1. On the testbed host, run: `docker logs <agent-container-name> 2>&1 | head -20`
2. Locate the `agent.startup` log line.

**Expected result:** A line of the form `agent.startup version=<tag>` appears in the startup log, emitted after `configure_logging()` and before `load_worker_settings()`.

**Pass criteria:** The `agent.startup` banner is present with the correct `version=` value. Fail if the line is absent.

---

### TC-11: CD daemon startup log contains version field (needs-human)

**Type:** UAT — log inspection
**Verification method:** Inspect CD daemon container logs.

**Precondition:** CD daemon container deployed from an image built with `APP_VERSION=sha-<hash>` (or similar).

**Steps:**
1. On the testbed host, run: `docker logs <cd-daemon-container-name> 2>&1 | head -20`
2. Locate the `cd.daemon_start` log line.

**Expected result:** The `cd.daemon_start` line includes `version=sha-<hash>` as the first interpolated value.

**Pass criteria:** The `version=` field is present and non-empty. Fail if absent or shows `dev` for a pushed build.

---

### TC-12: Docker build-arg injection — image built locally with explicit APP_VERSION (needs-human)

**Type:** UAT — Docker build smoke test
**Verification method:** CLI commands on the testbed host or any machine with Docker.

**Steps:**
1. From the repository root, build the master image:
   ```
   docker build --build-arg APP_VERSION=test-1.2.3 -t codex-slack-test:local .
   ```
2. Inspect the env var in the built image:
   ```
   docker run --rm codex-slack-test:local sh -c 'echo $APP_VERSION'
   ```
3. Optionally, run the health check by starting the container and hitting `/health`.

**Expected result:**
- Step 2: output is `test-1.2.3`.
- Step 3 (if run): `{"status": "ok", "version": "test-1.2.3"}`.

**Pass criteria:** `APP_VERSION` is baked into the image and readable at runtime. Fail if output is `dev` or empty.

---

### TC-13: Docker build-arg default — local build without APP_VERSION shows "dev" (needs-human)

**Type:** UAT — Docker build smoke test
**Verification method:** CLI commands.

**Steps:**
1. From the repository root, build the master image without specifying `APP_VERSION`:
   ```
   docker build -t codex-slack-test:noarg .
   ```
2. Inspect the env var:
   ```
   docker run --rm codex-slack-test:noarg sh -c 'echo $APP_VERSION'
   ```

**Expected result:** Output is `dev` (the Dockerfile `ARG` default).

**Pass criteria:** Untagged local builds report `dev`, not an empty string or error.

---

### TC-14: Staging health endpoint reflects RC tag after deploy (needs-human)

**Type:** UAT — live endpoint verification
**Verification method:** `curl` against the staging environment.

**Precondition:** Staging is running an image built from a tagged RC (e.g. `v4.0-rc1`).

**Steps:**
1. Run: `curl -s https://staging/health | python3 -m json.tool`

**Expected result:** Response body is `{"status": "ok", "version": "v4.0-rc1"}` (or whichever RC tag was most recently deployed to staging).

**Pass criteria:** `version` field is non-empty, not `"dev"`, and matches the last RC tag used for the staging build. Fail if `version` is `"dev"` or absent.

---

### TC-15: Production health endpoint shows RC string, not release string (needs-human / ops awareness)

**Type:** UAT — ops awareness check
**Verification method:** `curl` against the production environment after a release promotion.

**Precondition:** A release has been promoted via `promote-release.yml` (e.g. `v4.0-rc3` promoted to `v4.0`).

**Steps:**
1. Run: `curl -s https://production/health | python3 -m json.tool`

**Expected result:** Response body shows `{"status": "ok", "version": "v4.0-rc3"}` — the RC string of the promoted build, not `v4.0`.

**Pass criteria:** This is the *intentional* behaviour described in ADR 0011 (promote-release retags without rebuilding; the bit-identical invariant is preserved). The version field will always show the last RC string, never the release string. Operators must not treat this as a deployment bug. The ops manual must document this behaviour.

**Note:** If `version` shows `v4.0`, it means the image was rebuilt during promotion — which breaks the bit-identical invariant and must be flagged as a misconfiguration.

---

## 4. Non-Functional Test Cases

### NF-01: `get_app_version()` has no side effects (automated, implicit)

`get_app_version()` must not emit logs, start threads, open files, or cache state beyond what `os.environ.get` provides. This is verified implicitly by the unit tests: if any side effect occurred, the isolated `monkeypatch` environment would expose it.

### NF-02: `get_app_version()` is importable without triggering application startup (automated, implicit)

`from src.version import get_app_version` must not import `src.master`, `src.agent`, or `src.cd`. This is guaranteed by the module placement rule in ADR 0011 and verified implicitly when `tests/test_version.py` imports `src.version` in isolation.

### NF-03: `/health` endpoint remains available during version field addition (automated)

`GET /health` must continue to return HTTP 200. The integration tests in `tests/test_health.py` assert this. The existing `test_health` in `tests/master/test_main.py` must also be updated by the engineer to expect the `version` field so it remains green.

---

## 5. Regression Notes

The existing test `tests/master/test_main.py::test_health` asserts `r.json() == {"status": "ok"}`. After the engineer adds the `version` field to the `/health` response, this assertion will fail because the response body will include an additional key. The engineer must update that assertion to account for the new field. This is an expected, intentional regression and not a sign of a bug.

---

## 6. Sign-Off Template

| Field | Value |
|---|---|
| Date | |
| Build / commit under test | |
| APP_VERSION used for testbed build | |
| Test executor | |
| TC-01 result (automated) | Pass / Fail |
| TC-02 result (automated) | Pass / Fail |
| TC-03 result (automated) | Pass / Fail |
| TC-04 result (automated) | Pass / Fail |
| TC-05 result (automated) | Pass / Fail |
| TC-06 result (automated) | Pass / Fail |
| TC-07 result (automated) | Pass / Fail |
| TC-08 result (automated) | Pass / Fail |
| TC-09 result (needs-human) | Pass / Fail |
| TC-10 result (needs-human) | Pass / Fail |
| TC-11 result (needs-human) | Pass / Fail |
| TC-12 result (needs-human) | Pass / Fail |
| TC-13 result (needs-human) | Pass / Fail |
| TC-14 result (needs-human) | Pass / Fail |
| TC-15 result (needs-human / ops awareness) | Pass / Fail / N/A |
| Blocking issues | |
| Sign-off owner | |
