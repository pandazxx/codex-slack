# Traefik-based multi-env routing for dev

**Status:** Accepted
**Date:** 2026-05-07
**Decided by:** SRE subagent (senior escalation, opus override)
**Supersedes:** Per-branch published-port scheme in `.sre/env-up.sh` (commit `00d7a94`).

## Context

`docs/sre-decisions/2025-05-06-containerized-dev-workflow.md` (Accepted) committed to "parallel feature-branch envs on the same Docker host". The SRE subagent definition (`.claude/agents/sre.md`) further commits to **Traefik-based multi-env routing by default, label-based, with hostnames derived from labels**.

The as-shipped implementation (commits `36dee82`, `00d7a94`, `230dc88`, `06d22f6`) instead allocates a deterministic per-branch port from a hash of the project name (e.g. `8676` for `pandazxx-feat-steaming-response`) and instructs the user to reach the env via SSH local-port-forward:

```
ssh -L 8676:localhost:8676 ubuntu@docker-testbed.local
```

This is functional — the user has a UAT'd PR running on it right now — but it diverges from the confirmed design. The user escalated for a senior-SRE review and asked for the design to be brought back into compliance.

## Constraints

1. **Single user, on macOS, reaching a remote testbed (`docker-testbed.local`, IP `10.10.10.238`) over the LAN.** No public DNS.
2. **mDNS resolves `docker-testbed.local` already**, but mDNS does not support wildcards. `*.docker-testbed.local` does not resolve.
3. **Ports 80 and 443 are free** on the testbed.
4. **One existing dev env is running** (`pandazxx-feat-steaming-response`) backing PR #146 currently in UAT. Disruption must be minimised.
5. **Future migration to public wildcard DNS should be a one-line change**, not a re-architecture.
6. **One service is HTTP** (master, port 8080). **One service is TCP** (mosquitto MQTT, port 1883) and is currently only accessed via `docker compose exec` — no external clients.

## Decision

### 1. Traefik runs as a separate one-time ingress stack on the testbed

A new top-level compose file, `docker-compose.traefik.yml`, defines the Traefik service. It is brought up once per host via `.sre/traefik-up.sh` and stays up across branch lifecycles. It is **not** part of any branch's compose project — branch envs and Traefik are orthogonal.

Traefik discovers branch services via the Docker provider on a shared external network, `sre_ingress`. Each branch env joins this network in addition to its private `internal` network.

Putting Traefik in the base `docker-compose.yml` was rejected: every branch project would launch its own Traefik, and they would all fight for ports 80/443.

### 2. Hostname scheme: `<service>.<branch>.dev.docker-testbed.local`

Examples:
- `master.feat-billing.dev.docker-testbed.local` → branch's master (HTTP).
- `master.feat-steaming-response.dev.docker-testbed.local` → currently-running branch's master.

The **hostname structure mirrors what a future public-wildcard-DNS deployment would use** (`*.dev.<public-domain>`), so migration is a suffix swap and a DNS change, not a redesign.

### 3. Resolution: per-branch `/etc/hosts` line on the laptop

`env-up.sh` adds **one line** to `/etc/hosts` of the form:

```
10.10.10.238  master.<branch>.dev.docker-testbed.local
```

`env-down.sh` removes it.

Rationale:
- mDNS does not do wildcards (rejected `*.dev.docker-testbed.local` via mDNS).
- Running a per-host dnsmasq for one developer is heavy.
- `/etc/hosts` doesn't support wildcards either, but we don't need them — we add exactly one entry per running branch and remove it on teardown.
- Migration to public wildcard DNS is literally "delete the `/etc/hosts` step from `env-up.sh` and tell users to point a wildcard CNAME at the testbed/edge".

The script gates the `/etc/hosts` write behind `sudo` (uses `sudo -n` then falls back to printing the line for the user to add manually if non-interactive sudo isn't available). It will not silently fail to add the entry — if it can't write, it prints a clear message and continues so the env still comes up.

### 4. TLS: plain HTTP for now

mkcert / self-signed adds a laptop-trust-store step that's not worth it for one user. Traefik's static config has a TLS resolver placeholder commented out; flipping to TLS later is a 5-line change. Rejected: cluttering the dev path with cert handling before there's a second user.

### 5. Mosquitto stays internal-only

Mosquitto is currently accessed only via `docker compose exec`. Routing TCP through Traefik for plain MQTT requires SNI, which requires TLS, which we're explicitly deferring (decision 4). Adding mosquitto to the ingress is documented as a follow-up, not done now.

The existing access patterns (`docker compose -p ... exec mosquitto mosquitto_sub ...`) continue to work unchanged.

### 6. Per-branch port allocation kept as a fallback, not the default

`env-up.sh` chooses ingress mode based on whether the shared Traefik stack is reachable on the target Docker host. If Traefik is up: no published port on master, Traefik-routed only. If Traefik is not up (or `DEV_DOCKER_HOST` is unset and the user is on local Docker without Traefik): falls back to the existing per-branch hashed port and prints the SSH-tunnel instructions.

This lets the existing `pandazxx-feat-steaming-response` env keep working during the migration and gives users an escape hatch when Traefik is broken.

### 7. Service exposure (per `.claude/agents/sre.md` section 2 step 8)

Lower envs exist to be poked at:
- **Master HTTP**: Traefik-routed at `master.<branch>.dev.docker-testbed.local`. Plus `/docs` (FastAPI Swagger) and `/health`.
- **Master shell**: `docker compose -p ... exec -it master bash`.
- **Master logs**: `docker compose -p ... logs -f master`.
- **Mosquitto**: `docker compose -p ... exec mosquitto mosquitto_sub -h localhost -t '#' -v`.
- **Traefik dashboard**: exposed at `traefik.dev.docker-testbed.local` (no auth in dev — single-user local network, threat model is laptop-on-LAN). `env-up.sh` adds the `/etc/hosts` entry for it on first invocation.

## Alternatives rejected

| Alternative | Why rejected |
|---|---|
| `*.localhost` hostnames | macOS resolves to 127.0.0.1 (the laptop), not the testbed. Would still need an SSH tunnel — defeats the goal. |
| Wildcard public DNS now | User has no public domain configured. Listed as the migration path, not the current implementation. |
| `*.docker-testbed.local` via mDNS wildcard | Avahi/Bonjour mDNS does not support wildcards by default. Per-host advertisement of every branch is operationally ugly. |
| dnsmasq on the testbed + macOS resolver pointing `.test` TLD at it | Requires laptop-side `/etc/resolver/<tld>` config. High-friction for one user; bigger redesign than this is solving for. |
| Caddy instead of Traefik | The subagent definition explicitly says Traefik. Both are fine; Traefik is the committed choice. No reason to override on aesthetic grounds. |
| Put Traefik in base `docker-compose.yml` | Every branch project would launch its own Traefik; they'd all collide on 80/443. Topology must be one-Traefik-per-host. |
| Embed Traefik in each branch project but with port-binding only on the first project | Race-condition-prone, fragile, requires knowing "which env is first". One ingress stack is simpler. |

## Consequences

**Positive**
- Matches the committed design in `.claude/agents/sre.md`.
- Removes per-branch port allocation (the user's actual gripe).
- Hostname scheme is identical to what public-DNS deployment will use — migration is trivial.
- Service exposure is name-based, scales to many parallel branches.
- Existing `pandazxx-feat-steaming-response` env keeps working through the migration via fallback path.

**Negative**
- One-time setup step on each Docker host: `.sre/traefik-up.sh`. Documented in `docs/guides/sre.md`.
- Per-branch `/etc/hosts` mutation requires `sudo` on the laptop. Mitigated by graceful fallback when sudo isn't available.
- `docker-testbed.local` is mDNS — if the testbed's IP changes, every `/etc/hosts` line goes stale. Acceptable: one user, one testbed; if the IP changes, `env-down.sh && env-up.sh` re-derives it.

**Risk: TLS deferred**
- Plain HTTP across the LAN is fine in the current threat model (single user, home/office LAN). Onboarding-loud item: revisit before adding a second developer or moving to a hostile network.

## Migration of existing env

The currently-running `pandazxx-feat-steaming-response` env (PR #146 UAT) will continue to work via the fallback per-branch port scheme until the next `env-up.sh` invocation. After Traefik is brought up on the testbed, re-running `env-up.sh feat-steaming-response` will:

1. Detect Traefik is reachable.
2. Add labels and join the env to `sre_ingress`.
3. Stop binding port 8676 on the host.
4. Add the `/etc/hosts` line on the laptop.
5. Print the new URL.

Existing in-flight UAT continues uninterrupted because compose's container-recreate respects health checks.

## References

- `.claude/agents/sre.md` (lines 184, 206, 208) — the committed design.
- `docs/sre-decisions/2025-05-06-containerized-dev-workflow.md` — accepted parent decision.
- Implementation: `docker-compose.traefik.yml`, `docker-compose.override.yml`, `docker-compose.remote.yml`, `.sre/env-up.sh`, `.sre/env-down.sh`, `.sre/traefik-up.sh`, `.sre/traefik-down.sh`.
- User-facing: `docs/guides/sre.md`.
