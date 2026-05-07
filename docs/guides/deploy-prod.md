# Production Deployment Runbook

This document describes how to deploy **codex-slack** to production using the pre-built artifact.

## Prerequisites

- [ ] Build completed successfully (CI green on the release tag).
- [ ] Staging UAT signed off (see PR or release notes).
- [ ] Backup of current production data verified.
- [ ] Change window confirmed with stakeholders.
- [ ] On-call responder knows about the deployment.
- [ ] Rollback runbook reviewed.

## Getting the Artifact

The artifact is published as a GitHub Release after CI passes on a release tag.

```bash
# Download the artifact (e.g., for v1.2.3)
gh release download v1.2.3 --dir ~/deployments/codex-slack-v1.2.3

# Verify the contents
ls -la ~/deployments/codex-slack-v1.2.3/
# Expected: deploy.sh, rollback.sh, verify.sh, MANIFEST
```

## Reviewing the Manifest

Before deploying, read the manifest to understand what's being deployed:

```bash
cat ~/deployments/codex-slack-v1.2.3/MANIFEST
```

**Expected contents:**

- Version (semver).
- Image digests for master and other services.
- Migration IDs (if applicable).
- Build commit and timestamp.
- Who triggered the build (CI user).
- Links to CI run and staging UAT.

## Deploying

**On the production host:**

```bash
cd ~/deployments/codex-slack-v1.2.3
./deploy.sh
```

**What it does:**

1. Pulls images by digest (no tag resolution).
2. Runs migrations in order (expand/contract if schema changes).
3. Stops old services and starts new ones.
4. Waits for health checks to pass.
5. Logs the deployment (timestamp, operator, digest, exit code).

**Expected output:**

```
Deploying codex-slack v1.2.3 (digest: sha256:abc1234...)
Pulling images...
Running migrations...
Starting services...
Waiting for health checks...
Deployment complete. Verify with: ./verify.sh
```

## Post-Deploy Verification

Immediately after deployment:

```bash
./verify.sh
```

**Expected output:**

```
Master API: ✅ responding
Frontend: ✅ serving
MQTT: ✅ connected
All checks passed.
```

If `verify.sh` fails, **immediately trigger rollback** (see below).

## Manual Verification Checklist

Beyond automated checks, verify:

- [ ] Web UI loads at https://codex.example.com
- [ ] API docs are at https://codex.example.com/docs
- [ ] Recent agent replies are visible in the UI
- [ ] No error logs in the master container
- [ ] Disk usage is as expected (no runaway growth)

**To check logs:**

```bash
docker compose -p codex-slack logs master | tail -50
```

## Monitoring Post-Deploy

After deployment, watch these metrics for the next hour:

- **Error rate** — should be < 0.1% (normal baseline).
- **Response time** — should be < 500ms (p95).
- **CPU/memory** — should be stable within expected ranges.

If any metric spikes, investigate logs and trigger rollback if needed.

## Rollback

If deployment fails or causes issues, immediately run:

```bash
cd ~/deployments/codex-slack-v1.2.3
./rollback.sh
```

**What it does:**

1. Stops current services.
2. Reverts to the previous known-good digest.
3. Reverses migrations (where safe).
4. Starts previous version.
5. Waits for health checks.

**Expected output:**

```
Rolling back to v1.2.2 (digest: sha256:def5678...)
Stopping current services...
Reversing migrations...
Starting previous version...
Rollback complete. Verify with: ./verify.sh
```

**After rollback:**

1. Run `./verify.sh` from the previous version directory.
2. Notify the team.
3. Investigate what went wrong.
4. Fix and redeploy.

## Troubleshooting

### "Deployment timed out waiting for health checks"

The master service failed to start. Check logs:

```bash
docker compose -p codex-slack logs master | tail -100
```

Common causes:

- **Missing API keys** — if the app validates credentials at startup, set them in the environment.
- **Port conflict** — 8080 is already in use.
- **Network issue** — MQTT or database unreachable.

**To proceed without rolling back:**

1. Fix the underlying issue (set env vars, kill conflicting process, etc.).
2. Manually start the service: `docker compose -p codex-slack up -d`
3. Wait for health checks: `docker compose -p codex-slack ps`
4. Verify: `./verify.sh`

If manual recovery fails, **roll back immediately** and investigate.

### "Migration failed"

Migrations are designed to be safe, but if one fails:

1. **Don't skip it.** Rollback and investigate.
2. Check the migration file for issues (syntax, logic).
3. Verify the database is in a consistent state (see `rollback.sh` for reversal).
4. Fix the migration and redeploy.

### "Rollback failed"

This is rare but critical. Rollback scripts are tested before release, but if it fails:

1. Check disk space (migrations write a lot of temp data).
2. Check database connectivity.
3. Try manual rollback: `docker compose -p codex-slack down && docker pull <PREVIOUS_DIGEST> && docker compose up -d`
4. **Escalate to the SRE team immediately.**

## Post-Deployment (Hours Later)

After 1-2 hours of stability, send a deployment summary to stakeholders:

- **What was deployed** — version, feature highlights.
- **Status** — healthy, no errors, performance stable.
- **Metrics** — error rate, response time, resource usage.
- **Next steps** — monitor over the next 24 hours.

## Rollback Retention

Previous version artifacts are retained for 7 days. After that, only the current deployment can be rolled back automatically. For older versions, use the GitHub Release archive.

---

## Quick Reference

| Task | Command |
|---|---|
| Deploy | `./deploy.sh` |
| Verify | `./verify.sh` |
| Rollback | `./rollback.sh` |
| View logs | `docker compose -p codex-slack logs master` |
| View manifest | `cat MANIFEST` |
| Check service status | `docker compose -p codex-slack ps` |

## Related Documentation

- **SRE workflow:** `docs/guides/sre.md` — container operations, dev env.
- **CI/CD:** `.github/workflows/` — what triggers builds and publishes artifacts.
- **Architecture:** `docs/decisions/` — design decisions and constraints.
