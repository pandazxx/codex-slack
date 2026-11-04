set dotenv-load := true

# Compute a URL-safe slug from a branch name or tag.
# Mirrors the transform in env-up.sh: tr '/_.' '-' | lower
[private]
_slug input:
    @echo "{{ input }}" | tr '/_.' '-' | tr '[:upper:]' '[:lower:]'

# Extract the host IP from a ssh://[user@]host DOCKER_HOST value and
# replace dots with dashes for nip.io hostname routing.
[private]
_host-ip docker_host:
    #!/usr/bin/env bash
    set -euo pipefail
    HOST_ADDR="{{ docker_host }}"
    HOST_ADDR="${HOST_ADDR#ssh://}"
    HOST_ADDR="${HOST_ADDR##*@}"
    HOST_IP="$(getent hosts "$HOST_ADDR" 2>/dev/null | awk '{print $1}' || echo "$HOST_ADDR")"
    echo "${HOST_IP//./-}"

# Probe the GID of the Docker socket on the remote host.
# The probe matches env-up.sh verbatim: alpine stat -c '%g' /sock.
[private]
_docker-gid docker_host:
    #!/usr/bin/env bash
    set -euo pipefail
    DOCKER_HOST="{{ docker_host }}" docker run --rm \
        -v /var/run/docker.sock:/sock alpine stat -c '%g' /sock 2>/dev/null

# Resolve an image:tag reference to a sha256 digest via docker buildx imagetools.
[private]
_resolve-digest image_ref:
    #!/usr/bin/env bash
    set -euo pipefail
    docker buildx imagetools inspect --format '{{"{{"}}json .Manifest.Digest{{"}}"}}' "{{ image_ref }}" | tr -d '"'

# Deploy or refresh the dev environment for a branch on DEV_DOCKER_HOST.
# Builds the dev stage from the current commit; no registry round-trip.
# Defaults to the current git branch when branch is omitted.
dev-up branch="":
    #!/usr/bin/env bash
    set -euo pipefail
    : "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"

    BRANCH="{{ branch }}"
    if [ -z "$BRANCH" ]; then
        BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    fi

    BRANCH_SLUG="$(echo "$BRANCH" | tr '/_.' '-' | tr '[:upper:]' '[:lower:]')"

    HOST_ADDR="${DEV_DOCKER_HOST#ssh://}"
    HOST_IP="$(getent hosts "${HOST_ADDR##*@}" 2>/dev/null | awk '{print $1}' || echo "${HOST_ADDR##*@}")"
    HOST_IP_DASHED="${HOST_IP//./-}"

    if [ -z "${DOCKER_GID:-}" ]; then
        DOCKER_GID="$(DOCKER_HOST="$DEV_DOCKER_HOST" docker run --rm \
            -v /var/run/docker.sock:/sock alpine stat -c '%g' /sock 2>/dev/null)"
        echo "==> DOCKER_GID=${DOCKER_GID} (detected from ${HOST_ADDR})"
    fi

    MASTER_SSH_AUTH_SOCK_PATH="${MASTER_SSH_AUTH_SOCK_PATH:-/run/user/1000/ssh-agent.sock}"

    PROJECT_ROOT="$(git rev-parse --show-toplevel)"

    if git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        GIT_BRANCH="$(git -C "$PROJECT_ROOT" rev-parse --abbrev-ref HEAD)"
        GIT_SHA="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD)"
        GIT_DIRTY=""
        [ -n "$(git -C "$PROJECT_ROOT" status --porcelain)" ] && GIT_DIRTY="(dirty)"
        APP_VERSION="${GIT_BRANCH}:${GIT_SHA}${GIT_DIRTY}"
    else
        APP_VERSION="dev-unknown"
    fi
    echo "==> APP_VERSION=${APP_VERSION}"

    # Bundled Postgres per branch stack (compose project = slug, so the
    # postgres_data volume and internal network are project-scoped).
    # shellcheck source=.sre/db-env.sh
    source "${PROJECT_ROOT}/.sre/db-env.sh"
    sre_db_env_dev

    export DOCKER_HOST="$DEV_DOCKER_HOST"
    export BRANCH_SLUG
    export HOST_IP_DASHED
    export DOCKER_GID
    export MASTER_SSH_AUTH_SOCK_PATH
    export APP_VERSION
    export MASTER_RUNTIME_IMAGE="${BRANCH_SLUG}-master:dev"

    echo "==> dev-up: branch=${BRANCH} slug=${BRANCH_SLUG} host=${DEV_DOCKER_HOST}"
    echo "==> Building image (target: dev)..."
    docker compose \
        -p "${BRANCH_SLUG}" \
        -f "${PROJECT_ROOT}/docker-compose.yml" \
        -f "${PROJECT_ROOT}/docker-compose.dev.yml" \
        -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
        build master

    echo "==> Bringing up services..."
    docker compose \
        -p "${BRANCH_SLUG}" \
        -f "${PROJECT_ROOT}/docker-compose.yml" \
        -f "${PROJECT_ROOT}/docker-compose.dev.yml" \
        -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
        up -d --remove-orphans

    echo "==> Waiting for master healthcheck..."
    RETRIES=18
    until docker compose \
        -p "${BRANCH_SLUG}" \
        -f "${PROJECT_ROOT}/docker-compose.yml" \
        -f "${PROJECT_ROOT}/docker-compose.dev.yml" \
        -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
        exec master curl -sf http://localhost:8080/health > /dev/null 2>&1; do
        RETRIES=$((RETRIES - 1))
        if [ "$RETRIES" -eq 0 ]; then
            echo "ERROR: master failed healthcheck after 90s" >&2
            docker compose -p "${BRANCH_SLUG}" \
                -f "${PROJECT_ROOT}/docker-compose.yml" \
                -f "${PROJECT_ROOT}/docker-compose.dev.yml" \
                -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
                logs --tail=50 master >&2
            exit 1
        fi
        sleep 5
    done

    echo ""
    echo "Dev env ready:"
    echo "  master: http://master.${BRANCH_SLUG}.${HOST_IP_DASHED}.nip.io"
    echo ""
    echo "Exec into services:"
    echo "  DOCKER_HOST=${DEV_DOCKER_HOST} docker compose -p ${BRANCH_SLUG} exec master bash"
    echo "  DOCKER_HOST=${DEV_DOCKER_HOST} docker compose -p ${BRANCH_SLUG} exec mosquitto sh"
    echo "  DOCKER_HOST=${DEV_DOCKER_HOST} docker compose -p ${BRANCH_SLUG} exec postgres psql -U master -d master"

# Tear down the dev environment for a branch on DEV_DOCKER_HOST.
# Deletes the branch stack's volumes (master_data and postgres_data): dev data
# is disposable and never survives teardown.
# Defaults to the current git branch when branch is omitted.
dev-down branch="":
    #!/usr/bin/env bash
    set -euo pipefail
    : "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"

    BRANCH="{{ branch }}"
    if [ -z "$BRANCH" ]; then
        BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    fi

    BRANCH_SLUG="$(echo "$BRANCH" | tr '/_.' '-' | tr '[:upper:]' '[:lower:]')"
    PROJECT_ROOT="$(git rev-parse --show-toplevel)"

    # shellcheck source=.sre/db-env.sh
    source "${PROJECT_ROOT}/.sre/db-env.sh"
    sre_db_env_dev

    export DOCKER_HOST="$DEV_DOCKER_HOST"

    echo "==> dev-down: branch=${BRANCH} slug=${BRANCH_SLUG}"
    docker compose \
        -p "${BRANCH_SLUG}" \
        -f "${PROJECT_ROOT}/docker-compose.yml" \
        -f "${PROJECT_ROOT}/docker-compose.dev.yml" \
        -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
        down --volumes --remove-orphans

    echo "==> Dev env ${BRANCH_SLUG} torn down."

# Resolve tag to digest and deploy (or upgrade in place) the singleton stack
# on <env>_DOCKER_HOST. Fixed project name: codex-slack.
# Database: COMPOSE_PROFILES=db-bundled + POSTGRES_PASSWORD starts the bundled
# Postgres (volume postgres_data); otherwise DATABASE_URL must be set.
# env: staging | prod
deploy env tag:
    #!/usr/bin/env bash
    set -euo pipefail

    ENV="{{ env }}"
    TAG="{{ tag }}"

    case "$ENV" in
        staging)
            : "${STAGING_DOCKER_HOST:?STAGING_DOCKER_HOST must be set for deploy staging}"
            TARGET_HOST="$STAGING_DOCKER_HOST"
            ;;
        prod)
            : "${PROD_DOCKER_HOST:?PROD_DOCKER_HOST must be set for deploy prod}"
            TARGET_HOST="$PROD_DOCKER_HOST"
            ;;
        *)
            echo "ERROR: env must be 'staging' or 'prod', got: ${ENV}" >&2
            exit 1
            ;;
    esac

    : "${REGISTRY:?REGISTRY must be set}"

    IMAGE_REF="${REGISTRY}/codex-slack-master:${TAG}"
    MASTER_RUNTIME_IMAGE="${REGISTRY}/codex-slack-master:${TAG}"

    echo "==> Resolving digest for ${IMAGE_REF}..."
    IMAGE_DIGEST="$(docker buildx imagetools inspect \
        --format '{{"{{"}}json .Manifest.Digest{{"}}"}}' "${IMAGE_REF}" | tr -d '"')"
    echo "==> IMAGE_DIGEST=${IMAGE_DIGEST}"

    MASTER_PORT="${MASTER_PORT:-8080}"

    HOST_ADDR="${TARGET_HOST#ssh://}"
    HOST_IP="${HOST_ADDR##*@}"
    # unix:// targets leave a socket path here; the singleton publishes on
    # the local host in that case, so probe localhost instead.
    case "$HOST_IP" in /*) HOST_IP="localhost" ;; esac

    if [ -z "${DOCKER_GID:-}" ]; then
        DOCKER_GID="$(DOCKER_HOST="$TARGET_HOST" docker run --rm \
            -v /var/run/docker.sock:/sock alpine stat -c '%g' /sock 2>/dev/null)"
        echo "==> DOCKER_GID=${DOCKER_GID} (detected from ${HOST_ADDR})"
    fi

    PROJECT_ROOT="$(git rev-parse --show-toplevel)"

    # Database settings (COMPOSE_PROFILES / DATABASE_URL / POSTGRES_PASSWORD)
    # come from the shell or .env. Fail before touching the host if incomplete
    # or if the upgrade would strand a legacy SQLite database.
    # shellcheck source=.sre/db-env.sh
    source "${PROJECT_ROOT}/.sre/db-env.sh"
    sre_db_env_singleton
    sre_db_require_config
    sre_db_legacy_sqlite_guard "$TARGET_HOST"

    export DOCKER_HOST="$TARGET_HOST"
    export MASTER_RUNTIME_IMAGE
    export IMAGE_DIGEST
    export MASTER_PORT
    export DOCKER_GID

    echo "==> deploy: env=${ENV} tag=${TAG} host=${TARGET_HOST}"
    echo "==> Pulling image by digest..."
    docker compose \
        -p codex-slack \
        -f "${PROJECT_ROOT}/docker-compose.yml" \
        -f "${PROJECT_ROOT}/docker-compose.deploy.yml" \
        -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
        pull

    echo "==> Bringing up singleton stack..."
    docker compose \
        -p codex-slack \
        -f "${PROJECT_ROOT}/docker-compose.yml" \
        -f "${PROJECT_ROOT}/docker-compose.deploy.yml" \
        -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
        up -d --remove-orphans

    echo "==> Waiting for master healthcheck on http://${HOST_IP}:${MASTER_PORT}/health..."
    RETRIES=18
    until curl -sf "http://${HOST_IP}:${MASTER_PORT}/health" > /dev/null 2>&1; do
        RETRIES=$((RETRIES - 1))
        if [ "$RETRIES" -eq 0 ]; then
            echo "ERROR: master failed healthcheck after 90s" >&2
            MASTER_LOGS="$(docker compose -p codex-slack \
                -f "${PROJECT_ROOT}/docker-compose.yml" \
                -f "${PROJECT_ROOT}/docker-compose.deploy.yml" \
                -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
                logs --tail=50 master 2>&1 || true)"
            printf '%s\n' "$MASTER_LOGS" >&2
            # Schema mismatch is a database state problem, not a transient one.
            if printf '%s\n' "$MASTER_LOGS" | grep -q 'schema is behind'; then
                echo "" >&2
                echo "!! master refused to start: the database schema is behind this image:" >&2
                printf '%s\n' "$MASTER_LOGS" | grep 'schema is behind' | sort -u | tail -n 2 >&2
                echo "→ run: just migrate ${ENV} ${TAG}$([ "$ENV" = prod ] && echo ' confirm=yes')" >&2
                echo "  then: just deploy ${ENV} ${TAG}" >&2
            elif printf '%s\n' "$MASTER_LOGS" | grep -q 'schema is ahead'; then
                echo "" >&2
                echo "!! master refused to start: the database schema is AHEAD of this image:" >&2
                printf '%s\n' "$MASTER_LOGS" | grep 'schema is ahead' | sort -u | tail -n 2 >&2
                echo "   This looks like a downgrade. Do not migrate; deploy the newer tag, or restore a snapshot." >&2
            fi
            exit 1
        fi
        sleep 5
    done

    echo ""
    echo "Deployed:"
    echo "  env:    ${ENV}"
    echo "  image:  ${MASTER_RUNTIME_IMAGE}@${IMAGE_DIGEST}"
    echo "  url:    http://${HOST_IP}:${MASTER_PORT}"
    if [ -n "${DATABASE_URL// /}" ]; then
        echo "  db:     DATABASE_URL (explicit)"
    else
        echo "  db:     bundled Postgres (profiles=${COMPOSE_PROFILES})"
    fi

# Bring the singleton stack's database to the schema head of <tag>'s image,
# WITHOUT starting master. Run before `just deploy <env> <tag>` when the
# release adds Alembic revisions (master refuses to start on a DB that is
# behind). Runs the migration from the target tag's image, because only it
# contains the new revisions. Never starts master; stops it if running.
#   just migrate staging v5.1-rc1
#   just migrate prod v5.1 confirm=yes     # prod requires confirm=yes
# Idempotent: already at head -> exit 0, master untouched. DB ahead of the
# image or at an unknown revision -> refuse, nothing changed.
# Same compose project and file set as `deploy` (kept in sync by
# tests/deploy_contract/test_justfile.py). The preamble is duplicated rather
# than shared so `deploy` is not modified beyond the failure hint.
# env: staging | prod
migrate env tag confirm="":
    #!/usr/bin/env bash
    set -euo pipefail

    ENV="{{ env }}"
    TAG="{{ tag }}"
    CONFIRM="{{ confirm }}"

    case "$ENV" in
        staging)
            : "${STAGING_DOCKER_HOST:?STAGING_DOCKER_HOST must be set for migrate staging}"
            TARGET_HOST="$STAGING_DOCKER_HOST"
            ;;
        prod)
            : "${PROD_DOCKER_HOST:?PROD_DOCKER_HOST must be set for migrate prod}"
            TARGET_HOST="$PROD_DOCKER_HOST"
            # Prod needs an explicit flag. Refuse before touching anything.
            if [ "$CONFIRM" != "confirm=yes" ] && [ "$CONFIRM" != "yes" ]; then
                echo "ERROR: refusing to migrate prod without confirmation. Nothing was changed." >&2
                echo "       This stops master on prod and migrates the production database in place." >&2
                echo "       Re-run with: just migrate prod ${TAG} confirm=yes" >&2
                exit 1
            fi
            ;;
        *)
            echo "ERROR: env must be 'staging' or 'prod', got: ${ENV}" >&2
            exit 1
            ;;
    esac

    : "${REGISTRY:?REGISTRY must be set}"

    IMAGE_REF="${REGISTRY}/codex-slack-master:${TAG}"
    MASTER_RUNTIME_IMAGE="${REGISTRY}/codex-slack-master:${TAG}"

    echo "==> Resolving digest for ${IMAGE_REF}..."
    IMAGE_DIGEST="$(docker buildx imagetools inspect \
        --format '{{"{{"}}json .Manifest.Digest{{"}}"}}' "${IMAGE_REF}" | tr -d '"')"
    echo "==> IMAGE_DIGEST=${IMAGE_DIGEST}"

    HOST_ADDR="${TARGET_HOST#ssh://}"

    if [ -z "${DOCKER_GID:-}" ]; then
        DOCKER_GID="$(DOCKER_HOST="$TARGET_HOST" docker run --rm \
            -v /var/run/docker.sock:/sock alpine stat -c '%g' /sock 2>/dev/null)"
        echo "==> DOCKER_GID=${DOCKER_GID} (detected from ${HOST_ADDR})"
    fi

    # justfile_directory() works without a git checkout (e.g. inside the test image).
    PROJECT_ROOT="{{ justfile_directory() }}"

    # shellcheck source=.sre/db-env.sh
    source "${PROJECT_ROOT}/.sre/db-env.sh"
    sre_db_env_singleton
    sre_db_require_config
    sre_db_legacy_sqlite_guard "$TARGET_HOST"

    export DOCKER_HOST="$TARGET_HOST"
    export MASTER_RUNTIME_IMAGE
    export IMAGE_DIGEST
    export DOCKER_GID

    dc() {
        docker compose \
            -p codex-slack \
            -f "${PROJECT_ROOT}/docker-compose.yml" \
            -f "${PROJECT_ROOT}/docker-compose.deploy.yml" \
            -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
            "$@"
    }
    # Hide credentials embedded in database URLs (user:pass@host) in anything we print.
    mask() { sed -E 's#(://[^:/@ ]+):[^ ]*@#\1:***@#g'; }

    # Read alembic_version straight from the database (not `db.sh verify-schema`,
    # which always fails: pandazxx/codex-slack#283). One small Python call in the
    # target image; works for bundled Postgres, external Postgres and SQLite
    # because it resolves the URL exactly as master does. Prints one line:
    #   SRE_DB_STATE state=<empty|prebaseline|current|behind|ahead|unknown> current=<rev|-> head=<rev>
    PYREV='
    import os
    from pathlib import Path
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect
    import src.master.db as d
    url = os.getenv("DATABASE_URL", "").strip() or d.resolve_db_url()
    cfg = Config(str(Path(d.__file__).parents[3] / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    eng = d._make_engine(url)
    tables = set(inspect(eng).get_table_names())
    cur = None
    if "alembic_version" in tables:
        with eng.connect() as conn:
            cur = MigrationContext.configure(conn).get_current_revision()
    eng.dispose()
    known = set(s.revision for s in script.walk_revisions())
    if cur is None:
        state = "prebaseline" if "workspaces" in tables else "empty"
    elif cur == head:
        state = "current"
    elif cur in known:
        state = "behind"
    elif cur > head:
        state = "ahead"
    else:
        state = "unknown"
    print("SRE_DB_STATE state=%s current=%s head=%s" % (state, cur or "-", head))
    '
    db_state() {
        local out
        if ! out="$(dc run --rm --no-deps -T master python -c "$PYREV" 2>&1 </dev/null)"; then
            printf '%s\n' "$out" | mask >&2
            echo "ERROR: could not read the database revision (output above)." >&2
            return 1
        fi
        printf '%s\n' "$out" | grep '^SRE_DB_STATE ' | tail -n 1
    }
    field() { printf '%s\n' "$1" | sed -E "s/.* $2=([^ ]+).*/\1/"; }

    MASTER_STOPPED=0
    LOG=""
    on_exit() {
        local rc=$?
        [ -n "$LOG" ] && rm -f "$LOG"
        [ "$rc" -eq 0 ] && return 0
        if [ "$MASTER_STOPPED" -eq 1 ]; then
            {
                echo ""
                echo "!! migrate FAILED and master is STOPPED on ${ENV} (${TARGET_HOST}); it will not restart by itself."
                echo "   Recover (never delete postgres_data or master_data):"
                echo "   1. Re-run (safe: re-reads the revision, takes a fresh snapshot):"
                echo "        just migrate ${ENV} ${TAG}$([ "$ENV" = prod ] && echo ' confirm=yes')"
                echo "   2. Restore the pre-migration snapshot (in the master_data volume under"
                echo "      /opt/codex-slack/data/master/snapshots/<UTC timestamp>/): human decision, see"
                echo "      .sre/operations/db-snapshot-migrate.md (fallback section)."
                echo "   3. Redeploy the previous tag: just deploy ${ENV} <previous-tag>"
                echo "      Only works if the database is still at its old revision; an older image"
                echo "      refuses a database that is ahead of it."
            } >&2
        else
            echo "!! migrate FAILED before master was stopped; master is untouched on ${ENV}." >&2
        fi
    }
    trap on_exit EXIT

    echo "==> migrate: env=${ENV} tag=${TAG} host=${TARGET_HOST}"
    echo "==> Pulling image by digest..."
    dc pull master

    case ",${COMPOSE_PROFILES// /}," in
        *,db-bundled,*)
            echo "==> Ensuring bundled Postgres is up and healthy..."
            dc up -d --wait postgres
            ;;
    esac

    echo "==> Reading current database revision..."
    STATE_LINE="$(db_state)"
    STATE="$(field "$STATE_LINE" state)"
    CURRENT="$(field "$STATE_LINE" current)"
    HEAD_REV="$(field "$STATE_LINE" head)"
    echo "==> revision before: ${CURRENT}  (image head: ${HEAD_REV}, state: ${STATE})"

    case "$STATE" in
        current)
            echo "==> Database is already at the image head (${HEAD_REV}). Nothing to migrate; master untouched."
            echo "Next: just deploy ${ENV} ${TAG}"
            exit 0
            ;;
        empty|prebaseline)
            echo "==> Database is ${STATE} (no alembic revision): master initializes or upgrades it itself on start."
            echo "    Nothing to migrate here; master untouched."
            echo "Next: just deploy ${ENV} ${TAG}"
            exit 0
            ;;
        ahead|unknown)
            echo "ERROR: database revision ${CURRENT} is ${STATE} relative to ${TAG} (head ${HEAD_REV})." >&2
            echo "       This looks like a downgrade or the wrong image. Refusing; nothing was changed." >&2
            echo "       Deploy the newer tag that contains ${CURRENT}, or restore a snapshot first." >&2
            exit 1
            ;;
        behind) ;;
        *)
            echo "ERROR: unexpected database state '${STATE}'. Nothing was changed." >&2
            exit 1
            ;;
    esac

    echo "==> Stopping master (it may be crash-looping on 'schema is behind')..."
    MASTER_STOPPED=1
    dc stop master

    echo "==> Snapshot and migrate (from ${MASTER_RUNTIME_IMAGE}@${IMAGE_DIGEST})..."
    LOG="$(mktemp)"
    dc run --rm --no-deps -T master bash -c 'bash scripts/db.sh snapshot-and-migrate --source-url "${DATABASE_URL:-postgresql+psycopg://master:${POSTGRES_PASSWORD}@postgres:5432/master}"' \
        </dev/null 2>&1 | mask | tee "$LOG"
    # pipefail makes a non-zero exit from the container fail the recipe here (set -e).
    if ! grep -q '^Migration complete\.' "$LOG"; then
        echo "ERROR: snapshot-and-migrate did not report 'Migration complete.'" >&2
        exit 1
    fi
    SNAPSHOT_DIR="$(sed -n -E 's/^Snapshotting .* → (.*)$/\1/p' "$LOG" | tail -n 1)"

    echo "==> Reading database revision after migration..."
    AFTER_LINE="$(db_state)"
    AFTER="$(field "$AFTER_LINE" current)"
    echo "==> revision after:  ${AFTER}  (image head: ${HEAD_REV})"
    if [ "$(field "$AFTER_LINE" state)" != "current" ]; then
        echo "ERROR: database is not at the image head after migration." >&2
        exit 1
    fi

    MASTER_STOPPED=0
    echo ""
    echo "Migrated (master is stopped, not started):"
    echo "  env:       ${ENV}"
    echo "  image:     ${MASTER_RUNTIME_IMAGE}@${IMAGE_DIGEST}"
    echo "  revision:  ${CURRENT} -> ${AFTER}"
    echo "  snapshot:  ${SNAPSHOT_DIR:-<not reported>} (inside the master_data volume)"
    echo "Next: just deploy ${ENV} ${TAG}"

# Bring down the singleton codex-slack stack on <env>_DOCKER_HOST.
# Preserves named volumes (master_data, postgres_data) — use `just destroy <env>`
# to wipe them.
# env: staging | prod
undeploy env:
    #!/usr/bin/env bash
    set -euo pipefail

    ENV="{{ env }}"

    case "$ENV" in
        staging)
            : "${STAGING_DOCKER_HOST:?STAGING_DOCKER_HOST must be set for undeploy staging}"
            TARGET_HOST="$STAGING_DOCKER_HOST"
            ;;
        prod)
            : "${PROD_DOCKER_HOST:?PROD_DOCKER_HOST must be set for undeploy prod}"
            TARGET_HOST="$PROD_DOCKER_HOST"
            ;;
        *)
            echo "ERROR: env must be 'staging' or 'prod', got: ${ENV}" >&2
            exit 1
            ;;
    esac

    PROJECT_ROOT="$(git rev-parse --show-toplevel)"

    # shellcheck source=.sre/db-env.sh
    source "${PROJECT_ROOT}/.sre/db-env.sh"
    sre_db_env_singleton

    export DOCKER_HOST="$TARGET_HOST"
    # deploy.yml requires these vars but they are not meaningful for down;
    # provide placeholders so compose can parse the file without error.
    export MASTER_RUNTIME_IMAGE="${MASTER_RUNTIME_IMAGE:-placeholder}"
    export IMAGE_DIGEST="${IMAGE_DIGEST:-sha256:0000000000000000000000000000000000000000000000000000000000000000}"

    echo "==> undeploy: env=${ENV} host=${TARGET_HOST}"
    docker compose \
        -p codex-slack \
        -f "${PROJECT_ROOT}/docker-compose.yml" \
        -f "${PROJECT_ROOT}/docker-compose.deploy.yml" \
        -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
        down --remove-orphans

    echo "==> ${ENV} stack torn down (volumes preserved: master_data, postgres_data)."

# DESTRUCTIVE: bring down the singleton stack AND delete its named volumes,
# including master_data (attachments, legacy SQLite) and postgres_data (the
# bundled Postgres database). Requires typing the env name
# to confirm. env: staging | prod
destroy env:
    #!/usr/bin/env bash
    set -euo pipefail

    ENV="{{ env }}"

    case "$ENV" in
        staging)
            : "${STAGING_DOCKER_HOST:?STAGING_DOCKER_HOST must be set for destroy staging}"
            TARGET_HOST="$STAGING_DOCKER_HOST"
            ;;
        prod)
            : "${PROD_DOCKER_HOST:?PROD_DOCKER_HOST must be set for destroy prod}"
            TARGET_HOST="$PROD_DOCKER_HOST"
            ;;
        *)
            echo "ERROR: env must be 'staging' or 'prod', got: ${ENV}" >&2
            exit 1
            ;;
    esac

    echo "WARNING: this deletes ALL named volumes for ${ENV}, including master_data and postgres_data (the database)."
    printf "Type '%s' to confirm: " "$ENV"
    read -r CONFIRM
    if [ "$CONFIRM" != "$ENV" ]; then
        echo "Aborted."
        exit 1
    fi

    PROJECT_ROOT="$(git rev-parse --show-toplevel)"

    export DOCKER_HOST="$TARGET_HOST"
    export MASTER_RUNTIME_IMAGE="${MASTER_RUNTIME_IMAGE:-placeholder}"
    export IMAGE_DIGEST="${IMAGE_DIGEST:-sha256:0000000000000000000000000000000000000000000000000000000000000000}"
    # Activate every profile so postgres_data is removed even if the profile
    # was switched off after the volume was created.
    export COMPOSE_PROFILES="*"

    echo "==> destroy: env=${ENV} host=${TARGET_HOST}"
    docker compose \
        -p codex-slack \
        -f "${PROJECT_ROOT}/docker-compose.yml" \
        -f "${PROJECT_ROOT}/docker-compose.deploy.yml" \
        -f "${PROJECT_ROOT}/docker-compose.postgres.yml" \
        down --volumes --remove-orphans

    echo "==> ${ENV} stack and volumes destroyed."

# List active compose projects on all configured hosts.
status:
    #!/usr/bin/env bash
    set -euo pipefail
    : "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"

    echo "=== DEV (${DEV_DOCKER_HOST}) ==="
    DOCKER_HOST="$DEV_DOCKER_HOST" docker compose ls 2>/dev/null || echo "  (no projects or unreachable)"

    if [ -n "${STAGING_DOCKER_HOST:-}" ]; then
        echo ""
        echo "=== STAGING (${STAGING_DOCKER_HOST}) ==="
        DOCKER_HOST="$STAGING_DOCKER_HOST" docker compose ls 2>/dev/null || echo "  (no projects or unreachable)"
    else
        echo ""
        echo "=== STAGING === (STAGING_DOCKER_HOST not set — skipped)"
    fi

    if [ -n "${PROD_DOCKER_HOST:-}" ]; then
        echo ""
        echo "=== PROD (${PROD_DOCKER_HOST}) ==="
        DOCKER_HOST="$PROD_DOCKER_HOST" docker compose ls 2>/dev/null || echo "  (no projects or unreachable)"
    fi

# Stream logs for a service (master, mosquitto, postgres).
# env: dev | staging | prod
# key: branch slug (dev only); omit for singleton envs
logs env service key="":
    #!/usr/bin/env bash
    set -euo pipefail

    ENV="{{ env }}"
    SERVICE="{{ service }}"
    KEY="{{ key }}"
    PROJECT_ROOT="$(git rev-parse --show-toplevel)"
    # shellcheck source=.sre/db-env.sh
    source "${PROJECT_ROOT}/.sre/db-env.sh"

    case "$ENV" in
        dev)
            : "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"
            TARGET_HOST="$DEV_DOCKER_HOST"
            if [ -z "$KEY" ]; then
                KEY="$(git rev-parse --abbrev-ref HEAD | tr '/_.' '-' | tr '[:upper:]' '[:lower:]')"
            fi
            PROJECT_NAME="$KEY"
            COMPOSE_FILES=(-f "${PROJECT_ROOT}/docker-compose.yml" -f "${PROJECT_ROOT}/docker-compose.dev.yml" -f "${PROJECT_ROOT}/docker-compose.postgres.yml")
            sre_db_env_dev
            ;;
        staging)
            : "${STAGING_DOCKER_HOST:?STAGING_DOCKER_HOST must be set}"
            TARGET_HOST="$STAGING_DOCKER_HOST"
            PROJECT_NAME="codex-slack"
            sre_db_env_singleton
            export MASTER_RUNTIME_IMAGE="${MASTER_RUNTIME_IMAGE:-placeholder}"
            export IMAGE_DIGEST="${IMAGE_DIGEST:-sha256:0000000000000000000000000000000000000000000000000000000000000000}"
            COMPOSE_FILES=(-f "${PROJECT_ROOT}/docker-compose.yml" -f "${PROJECT_ROOT}/docker-compose.deploy.yml" -f "${PROJECT_ROOT}/docker-compose.postgres.yml")
            ;;
        prod)
            : "${PROD_DOCKER_HOST:?PROD_DOCKER_HOST must be set}"
            TARGET_HOST="$PROD_DOCKER_HOST"
            PROJECT_NAME="codex-slack"
            sre_db_env_singleton
            export MASTER_RUNTIME_IMAGE="${MASTER_RUNTIME_IMAGE:-placeholder}"
            export IMAGE_DIGEST="${IMAGE_DIGEST:-sha256:0000000000000000000000000000000000000000000000000000000000000000}"
            COMPOSE_FILES=(-f "${PROJECT_ROOT}/docker-compose.yml" -f "${PROJECT_ROOT}/docker-compose.deploy.yml" -f "${PROJECT_ROOT}/docker-compose.postgres.yml")
            ;;
        *)
            echo "ERROR: env must be 'dev', 'staging', or 'prod', got: ${ENV}" >&2
            exit 1
            ;;
    esac

    export DOCKER_HOST="$TARGET_HOST"
    docker compose -p "${PROJECT_NAME}" "${COMPOSE_FILES[@]}" logs -f "$SERVICE"

# Open an interactive shell in a running service container
# (master, mosquitto, postgres).
# env: dev | staging | prod
# key: branch slug (dev only); omit for singleton envs
shell env service key="":
    #!/usr/bin/env bash
    set -euo pipefail

    ENV="{{ env }}"
    SERVICE="{{ service }}"
    KEY="{{ key }}"
    PROJECT_ROOT="$(git rev-parse --show-toplevel)"
    # shellcheck source=.sre/db-env.sh
    source "${PROJECT_ROOT}/.sre/db-env.sh"

    case "$ENV" in
        dev)
            : "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"
            TARGET_HOST="$DEV_DOCKER_HOST"
            if [ -z "$KEY" ]; then
                KEY="$(git rev-parse --abbrev-ref HEAD | tr '/_.' '-' | tr '[:upper:]' '[:lower:]')"
            fi
            PROJECT_NAME="$KEY"
            COMPOSE_FILES=(-f "${PROJECT_ROOT}/docker-compose.yml" -f "${PROJECT_ROOT}/docker-compose.dev.yml" -f "${PROJECT_ROOT}/docker-compose.postgres.yml")
            sre_db_env_dev
            ;;
        staging)
            : "${STAGING_DOCKER_HOST:?STAGING_DOCKER_HOST must be set}"
            TARGET_HOST="$STAGING_DOCKER_HOST"
            PROJECT_NAME="codex-slack"
            sre_db_env_singleton
            export MASTER_RUNTIME_IMAGE="${MASTER_RUNTIME_IMAGE:-placeholder}"
            export IMAGE_DIGEST="${IMAGE_DIGEST:-sha256:0000000000000000000000000000000000000000000000000000000000000000}"
            COMPOSE_FILES=(-f "${PROJECT_ROOT}/docker-compose.yml" -f "${PROJECT_ROOT}/docker-compose.deploy.yml" -f "${PROJECT_ROOT}/docker-compose.postgres.yml")
            ;;
        prod)
            : "${PROD_DOCKER_HOST:?PROD_DOCKER_HOST must be set}"
            TARGET_HOST="$PROD_DOCKER_HOST"
            PROJECT_NAME="codex-slack"
            sre_db_env_singleton
            export MASTER_RUNTIME_IMAGE="${MASTER_RUNTIME_IMAGE:-placeholder}"
            export IMAGE_DIGEST="${IMAGE_DIGEST:-sha256:0000000000000000000000000000000000000000000000000000000000000000}"
            COMPOSE_FILES=(-f "${PROJECT_ROOT}/docker-compose.yml" -f "${PROJECT_ROOT}/docker-compose.deploy.yml" -f "${PROJECT_ROOT}/docker-compose.postgres.yml")
            ;;
        *)
            echo "ERROR: env must be 'dev', 'staging', or 'prod', got: ${ENV}" >&2
            exit 1
            ;;
    esac

    export DOCKER_HOST="$TARGET_HOST"
    # Not every image ships bash (mosquitto is sh-only) — fall back to sh.
    docker compose -p "${PROJECT_NAME}" "${COMPOSE_FILES[@]}" exec "$SERVICE" \
        sh -c 'command -v bash >/dev/null 2>&1 && exec bash || exec sh'

# Build the test stage and run pytest on DEV_DOCKER_HOST.
# pattern: optional pytest -k filter ("" for the whole suite).
# db: sqlite (default) | postgres | mysql. postgres/mysql start an ephemeral
# database in the same throwaway compose project and run the whole suite
# against it. Examples:
#   just test                      # sqlite, whole suite
#   just test "" postgres          # postgres, whole suite
#   just test test_m1_startup postgres
test pattern="" db="sqlite":
    #!/usr/bin/env bash
    set -euo pipefail
    : "${DEV_DOCKER_HOST:?DEV_DOCKER_HOST must be set}"

    PROJECT_ROOT="$(git rev-parse --show-toplevel)"
    export TEST_PROJECT="ci-tests-$$"
    export DOCKER_HOST="$DEV_DOCKER_HOST"

    "${PROJECT_ROOT}/.sre/test-db.sh" "{{ db }}" "{{ pattern }}"

# Refresh the staging singleton with the latest image for a merged branch,
# then tear down that branch's dev environment.
# tag defaults to "master" (CI publishes codex-slack-master:master on every
# push to master per build-push.yml).
post-merge-cleanup branch tag="master":
    #!/usr/bin/env bash
    set -euo pipefail

    BRANCH="{{ branch }}"
    TAG="{{ tag }}"
    PROJECT_ROOT="$(git rev-parse --show-toplevel)"

    echo "==> post-merge-cleanup: branch=${BRANCH} tag=${TAG}"

    # deploy layers docker-compose.postgres.yml and reads COMPOSE_PROFILES /
    # POSTGRES_PASSWORD / DATABASE_URL from .env, so the refresh keeps the
    # staging database configuration (postgres_data is preserved).
    echo "==> Refreshing staging singleton..."
    just deploy staging "$TAG"

    BRANCH_SLUG="$(echo "$BRANCH" | tr '/_.' '-' | tr '[:upper:]' '[:lower:]')"

    if [ -n "${DEV_DOCKER_HOST:-}" ]; then
        RUNNING="$(DOCKER_HOST="$DEV_DOCKER_HOST" docker compose ls --filter "name=${BRANCH_SLUG}" --quiet 2>/dev/null || true)"
        if [ -n "$RUNNING" ]; then
            echo "==> Tearing down dev env for ${BRANCH}..."
            just dev-down "$BRANCH"
        else
            echo "==> No dev env found for ${BRANCH_SLUG} — skipped."
        fi
    else
        echo "==> DEV_DOCKER_HOST not set — skipping dev env teardown."
    fi

    echo "==> post-merge-cleanup complete."
