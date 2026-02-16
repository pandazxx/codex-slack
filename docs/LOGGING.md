# Logging Configuration

This bot uses Python standard logging (`logging.basicConfig`) and can write logs to both console and file.

## Logging Destination
Default destination:
- Console (`stderr`) via `StreamHandler`
- Optional file via `FileHandler` when `BOT_LOG_FILE` is set

Enable file logging with environment variable:
```dotenv
BOT_LOG_FILE=./logs/bot.log
```

Then run the bot normally:
```bash
python -m src.bot.main --session-id <SESSION_ID>
```

The bot writes to both console and file when `BOT_LOG_FILE` is configured.

You can still use shell redirection if preferred:
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
