# Test Plan: justfile + dotenv deploy

**Design doc:** `docs/design/justfile-dotenv-deploy.md`
**ADR:** `docs/decisions/0016-singleton-justfile-deploys.md`
**Issue:** #245
**Branch:** `feat/justfile-dotenv-deploy`

---

## Scope

This plan covers the contracts introduced by issue #245:

- The compose file layering rules (base + one additive overlay per shape).
- The `.env.example` variable inventory and absence of retired CD-daemon vars.
- The `justfile` recipe surface and dotenv-load setting.
- The retirement of the CD-daemon codebase and the multi-version staging shape.
- The new runbook files (`.sre/operations/deploy.md`, `undeploy.md`).
- Live recipe behaviour (dev-up, deploy, undeploy, rollback, post-merge-cleanup).

Cases that can be verified by reading files on disk are `automated`.
Cases that require a live Docker host, a real registry, or network calls are `needs-human`.

---

## Test Cases

### Static contract checks (automated)

| # | Test case | Module | Automation |
|---|-----------|--------|------------|
| SC-01 | `docker-compose.yml` parses as valid YAML | `test_compose_base.py` | automated |
| SC-02 | No service in `docker-compose.yml` contains a `build:` key | `test_compose_base.py` | automated |
| SC-03 | No service in `docker-compose.yml` contains a `ports:` key | `test_compose_base.py` | automated |
| SC-04 | `master` image in `docker-compose.yml` contains no `@sha256` digest pin | `test_compose_base.py` | automated |
| SC-05 | `docker-compose.dev.yml` parses as valid YAML | `test_compose_dev.py` | automated |
| SC-06 | `master` in `docker-compose.dev.yml` has `build:` with `target: dev` | `test_compose_dev.py` | automated |
| SC-07 | `master` in `docker-compose.dev.yml` has at least one `traefik.` label | `test_compose_dev.py` | automated |
| SC-08 | `sre-traefik-public` is declared as an external network in `docker-compose.dev.yml` | `test_compose_dev.py` | automated |
| SC-09 | No service in `docker-compose.dev.yml` has a `ports:` key (Traefik shape, no host ports) | `test_compose_dev.py` | automated |
| SC-10 | `docker-compose.deploy.yml` parses as valid YAML | `test_compose_deploy.py` | automated |
| SC-11 | `master` image string in `docker-compose.deploy.yml` references `${MASTER_RUNTIME_IMAGE` | `test_compose_deploy.py` | automated |
| SC-12 | `master` image string in `docker-compose.deploy.yml` references `@${IMAGE_DIGEST` | `test_compose_deploy.py` | automated |
| SC-13 | `master` in `docker-compose.deploy.yml` publishes port `8080` on the host | `test_compose_deploy.py` | automated |
| SC-14 | No service in `docker-compose.deploy.yml` has any `traefik.` label | `test_compose_deploy.py` | automated |
| SC-15 | No service in `docker-compose.deploy.yml` has a `build:` key | `test_compose_deploy.py` | automated |
| SC-16 | Every service in `docker-compose.dev.yml` declares `deploy.resources.limits.memory` | `test_compose_dev.py` | automated |
| SC-17 | Every service in `docker-compose.deploy.yml` declares `deploy.resources.limits.memory` | `test_compose_deploy.py` | automated |
| SC-18 | `.env.example` contains an uncommented `DEV_DOCKER_HOST` line | `test_env_example.py` | automated |
| SC-19 | `.env.example` contains an uncommented `STAGING_DOCKER_HOST` line | `test_env_example.py` | automated |
| SC-20 | `.env.example` contains an uncommented `REGISTRY` line | `test_env_example.py` | automated |
| SC-21 | `.env.example` contains no `CD_` variable (commented or not) | `test_env_example.py` | automated |
| SC-22 | `justfile` contains `set dotenv-load := true` | `test_justfile.py` | automated |
| SC-23 | `justfile` contains a `dev-up` recipe definition | `test_justfile.py` | automated |
| SC-24 | `justfile` contains a `dev-down` recipe definition | `test_justfile.py` | automated |
| SC-25 | `justfile` contains a `deploy` recipe definition | `test_justfile.py` | automated |
| SC-26 | `justfile` contains an `undeploy` recipe definition | `test_justfile.py` | automated |
| SC-27 | `justfile` contains a `status` recipe definition | `test_justfile.py` | automated |
| SC-28 | `justfile` contains a `logs` recipe definition | `test_justfile.py` | automated |
| SC-29 | `justfile` contains a `shell` recipe definition | `test_justfile.py` | automated |
| SC-30 | `justfile` contains a `test` recipe definition | `test_justfile.py` | automated |
| SC-31 | `justfile` contains a `post-merge-cleanup` recipe definition | `test_justfile.py` | automated |

### Retirement contract (automated)

| # | Test case | Module | Automation |
|---|-----------|--------|------------|
| RT-01 | `src/cd/` directory does NOT exist | `test_retirement.py` | automated |
| RT-02 | `Dockerfile.cd-daemon` does NOT exist | `test_retirement.py` | automated |
| RT-03 | `scripts/gen-env.sh` does NOT exist | `test_retirement.py` | automated |
| RT-04 | `docker-compose.staging.yml` does NOT exist | `test_retirement.py` | automated |
| RT-05 | `docker-compose.override.yml` does NOT exist (renamed to `docker-compose.dev.yml`) | `test_retirement.py` | automated |
| RT-06 | `.sre/operations/staging-up.md` does NOT exist | `test_retirement.py` | automated |
| RT-07 | `.sre/operations/deploy.md` DOES exist | `test_retirement.py` | automated |
| RT-08 | `.sre/operations/undeploy.md` DOES exist | `test_retirement.py` | automated |
| RT-09 | `.github/workflows/build-push.yml` contains no `build-cd-daemon` job name | `test_retirement.py` | automated |

---

### Happy path — live recipe execution (needs-human)

| # | Test case | Steps | Expected outcome | Automation |
|---|-----------|-------|-----------------|------------|
| HP-01 | `just dev-up` with default branch | Set `DEV_DOCKER_HOST` in `.env`. Run `just dev-up` from the branch root. | Stack comes up on DEV host; master accessible at `http://master.<slug>.<ip-dashed>.nip.io`; health endpoint returns 200. | needs-human |
| HP-02 | `just dev-up <branch>` with explicit branch name | `just dev-up feat/my-feature` | Same as HP-01 but slug is computed from the explicit branch name. | needs-human |
| HP-03 | `just dev-down` tears down branch stack | After HP-01, run `just dev-down`. | `docker compose ls` on `DEV_DOCKER_HOST` shows no project for that slug. | needs-human |
| HP-04 | `just deploy staging <rc-tag>` deploys to staging singleton | Set `STAGING_DOCKER_HOST`, `REGISTRY`, valid RC tag in `.env`. Run `just deploy staging v1.0.0-rc1`. | Digest resolved; image pulled; `http://<staging-host>:8080/health` returns 200; `docker compose ls` on staging host shows `codex-slack`. | needs-human |
| HP-05 | `just deploy staging <tag>` again (same tag) — idempotent | Run `just deploy staging v1.0.0-rc1` a second time immediately after HP-04. | Command succeeds; no containers recreated if digest unchanged; health still 200. | needs-human |
| HP-06 | Rollback via `just deploy staging <previous-tag>` | After deploying a newer tag, run `just deploy staging <previous-tag>`. | Previous digest deployed; health returns 200; `docker compose ps` shows old image SHA. | needs-human |
| HP-07 | `just undeploy staging` tears down singleton | After HP-04, run `just undeploy staging`. | `docker compose ls` on staging host shows no `codex-slack` project. | needs-human |
| HP-08 | `just post-merge-cleanup <branch>` refreshes staging and tears down dev | Ensure HP-01 dev env is up. Run `just post-merge-cleanup feat/my-feature`. | Staging updated to `master` tag; dev env for branch torn down. | needs-human |
| HP-09 | `MASTER_PORT` override applies to deploy | Set `MASTER_PORT=9090` in `.env`. Deploy staging. | `http://<staging-host>:9090/health` returns 200; port 8080 not listening. | needs-human |

---

### Failure modes (live)

| # | Test case | Steps | Expected outcome | Automation |
|---|-----------|-------|-----------------|------------|
| FM-01 | `just deploy staging <tag>` with `STAGING_DOCKER_HOST` unset | Remove `STAGING_DOCKER_HOST` from env. Run `just deploy staging master`. | Recipe exits non-zero with a message containing "STAGING_DOCKER_HOST must be set". No connection to any host attempted. | needs-human |
| FM-02 | `just deploy staging <tag>` with `REGISTRY` unset | `STAGING_DOCKER_HOST` set; `REGISTRY` absent. | Recipe exits non-zero with "REGISTRY must be set". | needs-human |
| FM-03 | `just deploy prod <tag>` with `PROD_DOCKER_HOST` unset | `PROD_DOCKER_HOST` not in env. | Recipe exits non-zero with "PROD_DOCKER_HOST must be set". | needs-human |
| FM-04 | `just deploy unknown-env <tag>` with unknown env value | Run `just deploy badenv v1.0.0`. | Recipe exits non-zero with message indicating `badenv` is not a valid env. | needs-human |
| FM-05 | `just deploy staging <tag>` with unresolvable tag | Tag does not exist in the registry. | `docker buildx imagetools inspect` fails; recipe exits non-zero before touching staging host. | needs-human |
| FM-06 | `just dev-up` with `DEV_DOCKER_HOST` unset | Remove `DEV_DOCKER_HOST`. Run `just dev-up`. | Recipe exits non-zero with "DEV_DOCKER_HOST must be set". | needs-human |
| FM-07 | Health check timeout: master container fails to become healthy | Deploy an image whose process exits immediately. | Recipe polls up to 90 s, then exits non-zero with "failed healthcheck" and dumps container logs. | needs-human |

---

### Edge cases (live)

| # | Test case | Expected outcome | Automation |
|---|-----------|-----------------|------------|
| EC-01 | `just dev-up` with no dev env already running (first deploy) | Stack created fresh; health passes. | needs-human |
| EC-02 | `just dev-up` with an existing dev env for the same branch (re-deploy/upgrade) | Old containers replaced in place; health passes. | needs-human |
| EC-03 | `just post-merge-cleanup` when no dev env exists for the branch | Staging refreshed; teardown step skipped gracefully with "skipped" message. | needs-human |
| EC-04 | `just status` with both `DEV_DOCKER_HOST` and `STAGING_DOCKER_HOST` set | Outputs headings for both DEV and STAGING sections. | needs-human |
| EC-05 | `just status` with only `DEV_DOCKER_HOST` set (`STAGING_DOCKER_HOST` absent) | Outputs DEV section; STAGING section shows "not set — skipped". | needs-human |

---

### Non-functional requirements

| # | Requirement | Automation |
|---|-------------|------------|
| NF-01 | `just` must be available in the agent container image (Dockerfile prod stage installs it) | needs-human (image build) |
| NF-02 | Real shell env vars override `.env` values (dotenv-load precedence) | needs-human |
| NF-03 | Recipes never silently fall back to a local Docker socket; absence of `DEV_DOCKER_HOST` is a hard error | automated (FM-01 through FM-06 pattern; static: justfile body check) |
| NF-04 | Base compose file is a valid neutral intersection — not a deployable artifact on its own | automated (SC-02, SC-03, SC-04) |

---

## Pass/Fail Criteria

- All `automated` cases in `tests/deploy_contract/` must pass with `pytest -x`.
- All `needs-human` live cases must be signed off by the operator on the PR before merge.
- No `CD_` variables may appear in `.env.example`.
- `docker-compose.override.yml` must not exist (replaced by `docker-compose.dev.yml`).
- `.sre/operations/deploy.md` and `undeploy.md` must exist.
- `.sre/operations/staging-up.md` must not exist.
