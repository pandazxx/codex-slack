FROM python:3.11-slim AS prod

ARG CODEX_NPM_PACKAGE=@openai/codex
ARG CLAUDE_NPM_PACKAGE=@anthropic-ai/claude-code
ARG APP_VERSION=dev

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION}

# Add Docker's official apt repo so we get docker-ce-cli + compose plugin.
# The podman package on Debian installs a podman-docker shim that shadows
# /usr/bin/docker; real Docker CLI must come first to avoid Podman being
# invoked when DOCKER_HOST points to a socket (CD daemon, master runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
       https://download.docker.com/linux/debian \
       $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
       > /etc/apt/sources.list.d/docker.list \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    docker-ce-cli \
    docker-compose-plugin \
    git \
    gh \
    jq \
    less \
    make \
    openssh-client \
    poppler-utils \
    tini \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g ${CODEX_NPM_PACKAGE} ${CLAUDE_NPM_PACKAGE}

RUN useradd -m -u 1000 -s /bin/bash appuser \
    && mkdir -p /workspace/home /home/appuser/.claude /opt/codex-slack/data/master \
    && chown -R appuser:appuser /workspace /home/appuser/.claude /opt/codex-slack
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
CMD ["python", "-m", "pytest"]
