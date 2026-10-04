FROM ghcr.io/pandazxx/codex-slack-base:latest AS prod

ARG CODEX_NPM_PACKAGE=@openai/codex
ARG APP_VERSION=dev

ENV APP_VERSION=${APP_VERSION}

# CODEX_CLI_CACHE_BUST has no effect on the install itself — it only exists so
# the GHA layer cache can't serve a stale install from a previous build. CI
# passes a value that changes on every run (see build-push.yml); without it,
# these RUN layers cache-hit forever since their inputs never change, and
# re-tagging/rebuilding keeps shipping whatever version was installed the
# first time the layer was built.
ARG CODEX_CLI_CACHE_BUST=0
RUN echo "cache-bust: ${CODEX_CLI_CACHE_BUST}" \
    && npm install -g ${CODEX_NPM_PACKAGE} \
    && npm list -g --depth=0

ARG JUST_VERSION=1.40.0
RUN curl --proto '=https' --tlsv1.2 -fsSL https://just.systems/install.sh \
    | bash -s -- --tag "${JUST_VERSION}" --to /usr/local/bin

USER appuser
WORKDIR /opt/codex-slack

# claude-code is installed via Anthropic's own installer, not npm: it ships a
# checksum-verified native binary directly with no Node/engines dependency,
# sidestepping the npm engines-capping and allowScripts breakage this image
# hit earlier (see docs/knowledge-base/lessons-learned.md). It lands in
# ~/.local/bin, hence the PATH extension below. The `claude --version` call
# makes a broken install fail the build instead of silently shipping.
RUN echo "cache-bust: ${CODEX_CLI_CACHE_BUST}" \
    && curl -fsSL https://claude.ai/install.sh | bash \
    && ~/.local/bin/claude --version
ENV PATH="/home/appuser/.local/bin:${PATH}"

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
