# Logging Reference

The v3 stack writes structured logs to stderr from each container. There is no built-in file rotation; collect logs through the container runtime (`docker logs`, `journalctl`, log driver, etc.).

## Format

All processes use a shared formatter (`src/logging_utils.py:LocalTimeFormatter`) that emits:

```
YYYY-MM-DD HH:MM:SS +OFFSET LEVEL logger.name: message
```

Timestamps respect the container `TZ` environment variable.

## Levels

| Process | How to set | Default |
|---|---|---|
| **agent** (`python -m src.agent.main`) | `--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}` CLI flag | `INFO` |
| **master** (`python -m src.master.main`) | Hardcoded to `INFO` at `basicConfig` time. Override per-logger via standard Python config if needed. | `INFO` |
| **cd daemon** (`python -m src.cd.main`) | Hardcoded to `INFO`. | `INFO` |

## Conventions

The first line each process writes on startup carries the build version:

- `master.startup version=<version>` — master service
- `agent.startup version=<version>` — agent worker
- `cd.daemon_start version=<version>` — CD daemon

`<version>` comes from the `APP_VERSION` env var baked at image build time (or `dev` for unsigned local builds). See [`docs/manuals/ops-manual.md`](../manuals/ops-manual.md) for how RC vs release version strings flow through promotion.

## Reading logs

```bash
# Master
docker logs -f codex-slack-master

# A specific agent
docker logs -f codex-agent-<workspace_id>

# CD daemon (if deployed)
docker logs -f codex-slack-cd
```

## Sensitive content

Agent logs include the prompt body and the LLM response in full. Treat container logs as sensitive — do not commit them, do not paste them into bug reports without redaction.

## Useful loggers

| Logger | What it covers |
|---|---|
| `src.master.main` | Service startup, db init, MQTT loop start |
| `src.master.mqtt_client` | MQTT publish/subscribe events |
| `src.master.ws_hub` | WebSocket connect/disconnect, broadcast |
| `src.master.workspaces` / `topics` / `messages` | REST request handling |
| `src.master.agent_runner` | Agent container spawn/stop/respawn |
| `src.agent.mqtt_loop` | Agent-side MQTT subscribe/dispatch |
| `src.agent.worker` | Per-prompt CLI invocation, session resume |
| `src.cd.daemon` | Polling loop and digest changes |
| `src.cd.deploy` | Image pull, container recreate, rollback |
