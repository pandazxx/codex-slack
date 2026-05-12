FROM ghcr.io/pandazxx/codex-slack-base:latest AS prod

ARG CODEX_NPM_PACKAGE=@openai/codex
ARG CLAUDE_NPM_PACKAGE=@anthropic-ai/claude-code
ARG APP_VERSION=dev

ENV APP_VERSION=${APP_VERSION}

RUN npm install -g ${CODEX_NPM_PACKAGE} ${CLAUDE_NPM_PACKAGE}

USER appuser
WORKDIR /opt/codex-slack

COPY --chown=appuser:appuser requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser frontend/package.json frontend/package-lock.json* ./frontend/
RUN cd frontend && npm ci --prefer-offline 2>/dev/null || npm install

COPY --chown=appuser:appuser src ./src
COPY --chown=appuser:appuser frontend ./frontend
RUN cd frontend && npm run build && rm -rf node_modules

COPY --chown=appuser:appuser config ./config
COPY --chown=appuser:appuser docs ./docs
COPY --chown=appuser:appuser docker-compose.yml ./
COPY --chown=appuser:appuser README.md BUILD.md USAGE.md ./
COPY --chown=appuser:appuser docker/entrypoint.sh /usr/local/bin/bot-entrypoint
RUN chmod +x /usr/local/bin/bot-entrypoint

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=30s \
  CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "uvicorn", "src.master.main:app", "--host", "0.0.0.0", "--port", "8080"]

FROM prod AS dev
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    strace procps vim \
    && rm -rf /var/lib/apt/lists/*
USER appuser
CMD ["python", "-m", "uvicorn", "src.master.main:app", "--host", "0.0.0.0", "--port", "8080", "--reload"]

FROM prod AS test
USER root
RUN pip install --no-cache-dir pytest pytest-cov pytest-asyncio httpx
USER appuser
COPY --chown=appuser:appuser tests ./tests
COPY --chown=appuser:appuser Dockerfile.agent-minimal ./Dockerfile.agent-minimal
COPY --chown=appuser:appuser docker/entrypoint.sh ./docker/entrypoint.sh
CMD ["python", "-m", "pytest"]
