FROM python:3.11-slim

ARG CODEX_NPM_PACKAGE=@openai/codex
ARG CLAUDE_NPM_PACKAGE=@anthropic-ai/claude-code

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    gh \
    jq \
    less \
    make \
    nodejs \
    npm \
    openssh-client \
    podman \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Install podman-compose via pip — the apt package (1.3.0) has a broken
# entry_point that crashes on load. pip ships a working recent version.
RUN pip install --no-cache-dir podman-compose

RUN npm install -g ${CODEX_NPM_PACKAGE} ${CLAUDE_NPM_PACKAGE}

RUN useradd -m -u 1000 -s /bin/bash appuser
USER appuser
WORKDIR /opt/codex-slack

COPY --chown=appuser:appuser requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser src ./src
COPY --chown=appuser:appuser config ./config
COPY --chown=appuser:appuser docs ./docs
COPY --chown=appuser:appuser README.md BUILD.md USAGE.md ./
COPY --chown=appuser:appuser docker/entrypoint.sh /usr/local/bin/bot-entrypoint
RUN chmod +x /usr/local/bin/bot-entrypoint

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/bot-entrypoint"]
CMD ["python", "-m", "src.bot.main"]
