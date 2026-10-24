<!--
  MARKETING / PORTFOLIO COVER
  Artifact placeholders are marked with 🖼️ [PLACEHOLDER: ...].
  Replace them with real screenshots, GIFs, and diagrams before publishing.
  Recommended asset home: docs/marketing/assets/
-->

<div align="center">

🖼️ [PLACEHOLDER: hero banner — 1280×640 logo + product shot on a dark gradient]

# Codex‑Slack

### Your own private control room for AI coding agents.

**Self‑hosted orchestration for Claude Code & Codex — run fleets of LLM engineers against your own Git repos, in containers you control.**

🖼️ [PLACEHOLDER: badge row — build passing · coverage · license · release vX.Y · docker pulls]

[Live Demo](#) · [Documentation](../README.md) · [Architecture](#-architecture) · [Screenshots](#-screenshots)

</div>

---

## 🚀 The 15‑second pitch

> Give an LLM coding agent a repository, a chat thread, and its own git worktree —
> then watch it design, implement, test, review, and open a PR while you sip coffee.

Codex‑Slack is a **self‑hosted web platform that turns Claude Code and Codex into a managed, containerized engineering team.** It brings **topic‑level isolation**, **multiple agent roles**, **event‑based cross‑agent orchestration**, and **live visualization of the agent's thinking process** — all behind one easy‑to‑use web UI. Each repository becomes a *workspace* served by its own agent container; each conversation becomes a *topic* with an isolated git worktree and its own LLM session. No Slack, no SaaS, no data leaving your infrastructure.

🖼️ [PLACEHOLDER: 20‑second product GIF — create workspace → open topic → @claude fix bug → streaming reply → PR link]

---

## ✨ Why it turns heads

| | |
|---|---|
| 🧩 **Multi‑agent, multi‑repo** | One dedicated agent container per workspace. Talk to `@claude`, `@codex`, or custom named agents in the same thread. |
| 🌱 **Isolated worktrees per topic** | Every chat thread gets its own git worktree and LLM session — parallel work streams that never step on each other. |
| ⚡ **Real‑time streaming** | Token‑by‑token agent output over WebSocket + MQTT. Watch the agent think, not just the final answer. |
| 🕸️ **Interactive topic graph** | Visualize a topic's transcript as a navigable graph (Vue Flow + Mermaid). |
| 🖼️ **Paste‑to‑attach** | Ctrl/Cmd+V an image straight into the chat — screenshots become context instantly. |
| 🔔 **Event‑driven staff actions** | Schedulers, message hooks, and vetoable archive hooks let agents react to events, not just messages. |
| 🐳 **Bring‑your‑own runtime** | Docker or rootless Podman, local socket or remote host over SSH. No vendor lock‑in. |
| 🔒 **Yours, end to end** | Master + agents run entirely on infrastructure you control. Credentials never leave your box. |

---

## 📸 Screenshots

> Replace each block with a real capture. Suggested set below.

| Workspace list | Topic chat with streaming reply |
|---|---|
| 🖼️ [PLACEHOLDER: workspace list view] | 🖼️ [PLACEHOLDER: topic chat, streaming agent output + thinking spinner] |

| Interactive topic graph | Agent configuration panel |
|---|---|
| 🖼️ [PLACEHOLDER: topic graph visualization] | 🖼️ [PLACEHOLDER: per‑workspace agent + env config] |

---

## 🏗️ Architecture

🖼️ [PLACEHOLDER: architecture diagram — Browser ↔ Master (FastAPI/WS) ↔ MQTT broker ↔ Agent containers ↔ Git repos]

A clean separation between a **control plane** (the master) and a **fleet of stateless‑ish workers** (agent containers), wired together by a lightweight message bus.

| Component | Role | Stack |
|---|---|---|
| **`master`** | REST API + Vue 3 SPA + WebSocket hub + MQTT client + container orchestration | FastAPI · Uvicorn · SQLite |
| **`mosquitto`** | Internal message bus between master and agents | Eclipse Mosquitto 2 (MQTT) |
| **agent containers** | One per workspace; runs the MQTT loop and invokes the `claude` / `codex` CLI in an isolated worktree | Python worker · Claude Code / Codex CLI |

**Design principles on display:**
- **Isolation by construction** — per‑workspace containers and per‑topic git worktrees mean blast radius is always bounded.
- **Event‑driven, decoupled** — master and agents communicate over MQTT topics, not tight RPC coupling.
- **Runtime‑agnostic** — the container layer abstracts Docker vs. Podman, local vs. remote.
- **Documented decisions** — 20+ ADRs capture *why*, not just *what*.

---

## 🛠️ Tech stack

**Backend** · Python · FastAPI · Uvicorn · MQTT (paho‑mqtt) · SQLite · MCP · Docker SDK
**Frontend** · Vue 3 · Vue Router · Vite · Vue Flow · Mermaid · highlight.js · marked
**Infra / DevEx** · Docker & Podman · multi‑stage Dockerfiles · Traefik · `just` task runner · GitHub Actions
**Quality** · pytest · Vitest · ruff · three‑environment CI/CD (test bed → staging → prod) with RC‑based promotion

---

## 📊 By the numbers

> Auto‑fill / verify these before publishing.

| Metric | Value |
|---|---|
| Backend Python (src + tests) | ~22k LOC |
| Vue components | 33 |
| Test files | 42 |
| Architecture Decision Records | 23 |
| Design documents | 15 |
| Container runtimes supported | Docker · Podman (local & remote) |

🖼️ [PLACEHOLDER: optional — CI status / coverage graph]

---

## 🎯 What this project demonstrates

*Written for reviewers and hiring managers — the engineering story behind the product.*

- **Distributed systems design** — a control‑plane/worker architecture coordinated through a message broker, with real‑time streaming over WebSocket + MQTT.
- **Container orchestration from scratch** — the master programmatically spawns, configures, and tears down agent containers across Docker and Podman, local and remote.
- **Full‑stack ownership** — Python API + reactive Vue 3 SPA + build pipeline that bundles the frontend into the backend image.
- **Production‑grade delivery** — multi‑stage Docker builds, a three‑environment promotion pipeline, RC tagging, and operator runbooks.
- **Engineering discipline** — decisions recorded as ADRs, designs written before code, mirrored test structure, and an append‑only lessons‑learned log.
- **Applied AI tooling** — deep, practical integration with agentic coding CLIs (Claude Code, Codex) and MCP.

---

## ⚡ Try it in two minutes

```bash
git clone https://github.com/<org>/codex-slack.git
cd codex-slack

# Minimal .env — see README for the full list
cat > .env <<EOF
ANTHROPIC_API_KEY=sk-ant-...
GH_TOKEN=ghp_...
MASTER_GIT_USER_NAME=Your Name
MASTER_GIT_USER_EMAIL=you@example.com
CONTAINER_RUNTIME=docker
CONTAINER_SOCKET_PATH=/var/run/docker.sock
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
EOF

docker compose up --build -d
open http://localhost:8080
```

Full setup, configuration, and troubleshooting: **[README](../README.md)** · **[Ops Manual](../manuals/ops-manual.md)** · **[User Manual](../manuals/user-manual.md)**

---

## 🗺️ Roadmap

> Trim to what's true — recruiters love a clear "what's next."

- [ ] 🖼️ [PLACEHOLDER: near‑term item]
- [ ] 🖼️ [PLACEHOLDER: mid‑term item]
- [ ] 🖼️ [PLACEHOLDER: stretch item]

---

<div align="center">

**Built by 🖼️ [PLACEHOLDER: your name]** · [Portfolio](#) · [LinkedIn](#) · [GitHub](#)

<sub>Codex‑Slack is self‑hosted and runs entirely on infrastructure you control.
The name reflects its v2 origin as a chat bridge; v3 is a standalone web platform (see ADR‑0006).</sub>

</div>
