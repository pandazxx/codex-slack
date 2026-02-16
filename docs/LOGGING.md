# Logging Configuration

This bot uses Python standard logging (`logging.basicConfig`) and writes logs to process output streams.

## Logging Destination
Default destination:
- `INFO` and `WARNING` logs -> standard output
- `ERROR` and exception tracebacks -> standard error

No file handler is configured in code by default.

To persist logs, redirect output when starting the bot:
```bash
python -m src.bot.main --session-id <SESSION_ID> > bot.log 2>&1
```

To both view and save logs:
```bash
python -m src.bot.main --session-id <SESSION_ID> 2>&1 | tee bot.log
```

## Logging Level
Set log verbosity with CLI option `--log-level`.

Examples:
```bash
python -m src.bot.main --session-id <SESSION_ID> --log-level INFO
python -m src.bot.main --session-id <SESSION_ID> --log-level DEBUG
python -m src.bot.main --session-id <SESSION_ID> --log-level ERROR
```

Supported values include common Python levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

## What Gets Logged
Current logging includes:
- Slack command activity (`/codex-status`, `/codex-attach`, `/codex-detach`, `/codex-help`)
- Conversation lifecycle events (received/completed/failed)
- Conversation content logs (prompt and response text)
- Error stack traces on failures

Important:
- Conversation content logging includes full prompt/response text.
- Treat log files as sensitive data and avoid committing them.
