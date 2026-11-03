"""
Contract tests for `just migrate <env> <tag> [confirm=yes]` and the schema
mismatch hint in `just deploy`.

Rules under test:
  - migrate takes `env tag confirm`, uses the same compose project and file set
    as deploy, runs from the target tag's image, never starts master, and never
    calls `db.sh verify-schema` (pandazxx/codex-slack#283).
  - prod without `confirm=yes` refuses before touching anything.
  - deploy prints a migrate hint on "schema is behind" and a downgrade note on
    "schema is ahead".
  - Behaviour is exercised against a stub `docker` on PATH: no real host, no
    network. The stub records every invocation.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
JUSTFILE = REPO_ROOT / "justfile"
OPS = REPO_ROOT / ".sre" / "operations"

HEAD = "000000000002"
OLD = "000000000001"

needs_just = pytest.mark.skipif(shutil.which("just") is None, reason="just binary not installed")


def _recipe_body(name: str) -> str:
    """Text of a recipe from its header line up to the next top-level line."""
    lines = JUSTFILE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if re.match(rf"^{re.escape(name)}(\s|:)", ln))
    body = [lines[start]]
    for ln in lines[start + 1 :]:
        if ln.strip() and not ln.startswith((" ", "\t")):
            break
        body.append(ln)
    return "\n".join(body)


def _code_lines(body: str) -> list[str]:
    return [ln for ln in body.splitlines() if not ln.strip().startswith("#")]


def _compose_files(body: str) -> set[str]:
    return set(re.findall(r'-f "\$\{PROJECT_ROOT\}/(docker-compose[\w.]*\.yml)"', body))


# ── static contracts ─────────────────────────────────────────────────────────


@needs_just
def test_migrate_recipe_parameters() -> None:
    dump = subprocess.run(
        ["just", "--justfile", str(JUSTFILE), "--dump", "--dump-format", "json"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    )
    params = [p["name"] for p in json.loads(dump.stdout)["recipes"]["migrate"]["parameters"]]
    assert params == ["env", "tag", "confirm"]


def test_migrate_uses_same_project_and_files_as_deploy() -> None:
    mig, dep = _recipe_body("migrate"), _recipe_body("deploy")
    assert "-p codex-slack" in mig and "-p codex-slack" in dep
    assert _compose_files(mig) == _compose_files(dep) == {
        "docker-compose.yml",
        "docker-compose.deploy.yml",
        "docker-compose.postgres.yml",
    }


def test_migrate_runs_from_target_image_digest() -> None:
    mig = _recipe_body("migrate")
    assert 'MASTER_RUNTIME_IMAGE="${REGISTRY}/codex-slack-master:${TAG}"' in mig
    assert "export MASTER_RUNTIME_IMAGE" in mig and "export IMAGE_DIGEST" in mig
    assert "imagetools inspect" in mig
    assert "pull master" in mig


def test_migrate_never_calls_verify_schema() -> None:
    code = "\n".join(_code_lines(_recipe_body("migrate")))
    assert "verify-schema" not in code
    assert "get_current_revision" in code  # reads alembic_version directly instead


def test_migrate_never_starts_master() -> None:
    code = "\n".join(_code_lines(_recipe_body("migrate")))
    ups = re.findall(r"\bdc up\b[^\n]*", code)
    assert ups, "expected the bundled-postgres 'up' step"
    for line in ups:
        assert line.strip().endswith("postgres") and "--remove-orphans" not in line, line
    assert "dc stop master" in code
    assert "snapshot-and-migrate --source-url" in code
    assert "run --rm --no-deps -T master" in code


def test_migrate_sources_same_db_guards_as_deploy() -> None:
    mig = _recipe_body("migrate")
    for needle in ("sre_db_env_singleton", "sre_db_require_config",
                   "sre_db_legacy_sqlite_guard", 'export DOCKER_HOST="$TARGET_HOST"',
                   "STAGING_DOCKER_HOST", "PROD_DOCKER_HOST", "REGISTRY must be set"):
        assert needle in mig, needle


def test_migrate_prod_requires_confirm_before_any_docker_call() -> None:
    mig = _recipe_body("migrate")
    assert "confirm=yes" in mig
    assert mig.index("confirm=yes") < mig.index("imagetools inspect")
    assert mig.index("confirm=yes") < mig.index("docker run")


def test_deploy_hints_migrate_on_schema_behind_and_downgrade_on_ahead() -> None:
    dep = _recipe_body("deploy")
    assert "schema is behind" in dep and "just migrate ${ENV} ${TAG}" in dep
    assert "schema is ahead" in dep and "downgrade" in dep
    assert "logs --tail=50 master" in dep  # existing 50-line tail kept


def test_runbooks_and_release_notes_point_at_migrate() -> None:
    assert (OPS / "migrate.md").is_file()
    assert len((OPS / "migrate.md").read_text().splitlines()) <= 40
    for rel in (".sre/operations/deploy.md", ".sre/operations/db-snapshot-migrate.md",
                "docs/releases/v5.1.md", "docs/sre.md", "docs/manuals/ops-manual.md"):
        assert "just migrate" in (REPO_ROOT / rel).read_text(encoding="utf-8"), rel


# ── behaviour against a stub docker ──────────────────────────────────────────

STUB = r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_LOG"
args="$*"
case "$1" in
  buildx) echo '"sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"'; exit 0 ;;
  volume) exit 1 ;;
  compose)
    if [[ "$args" == *" python -c "* ]]; then
      echo "some alembic noise"
      echo "SRE_DB_STATE $(cat "$STUB_STATE")"
      exit 0
    fi
    if [[ "$args" == *snapshot-and-migrate* ]]; then
      echo "Snapshotting postgresql+psycopg://master:secretpw@postgres:5432/master → /opt/codex-slack/data/master/snapshots/T1"
      if [ "${STUB_FAIL:-}" = 1 ]; then echo "boom" >&2; exit 1; fi
      echo "Target is same as source — running alembic upgrade head"
      echo "Migration complete."
      echo "state=current current=000000000002 head=000000000002" > "$STUB_STATE"
    fi
    exit 0 ;;
esac
exit 0
"""


def _run_migrate(tmp_path: Path, args: list[str], state: str, fail: bool = False):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "docker").write_text(STUB)
    (bindir / "docker").chmod(0o755)
    log = tmp_path / "docker.log"
    log.write_text("")
    state_file = tmp_path / "state"
    state_file.write_text(state + "\n")
    dotenv = tmp_path / "empty.env"
    dotenv.write_text("")
    env = {
        "PATH": f"{bindir}:{Path(shutil.which('just')).parent}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
        "STUB_STATE": str(state_file),
        "STAGING_DOCKER_HOST": "ssh://stub@stub.invalid",
        "PROD_DOCKER_HOST": "ssh://stub@stub-prod.invalid",
        "REGISTRY": "registry.invalid/test",
        "DOCKER_GID": "1",
        "COMPOSE_PROFILES": "db-bundled",
        "POSTGRES_PASSWORD": "secretpw",
    }
    if fail:
        env["STUB_FAIL"] = "1"
    proc = subprocess.run(
        ["just", "--justfile", str(JUSTFILE), "--dotenv-path", str(dotenv), "migrate", *args],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env,
    )
    return proc, log.read_text().splitlines()


BEHIND = f"state=behind current={OLD} head={HEAD}"


@needs_just
def test_prod_without_confirm_refuses_and_touches_nothing(tmp_path: Path) -> None:
    proc, calls = _run_migrate(tmp_path, ["prod", "v5.1"], BEHIND)
    assert proc.returncode == 1
    assert "refusing to migrate prod without confirmation" in proc.stderr
    assert "just migrate prod v5.1 confirm=yes" in proc.stderr
    assert calls == []


@needs_just
def test_prod_with_confirm_proceeds(tmp_path: Path) -> None:
    proc, calls = _run_migrate(tmp_path, ["prod", "v5.1", "confirm=yes"], BEHIND)
    assert proc.returncode == 0, proc.stderr
    assert any("snapshot-and-migrate" in c for c in calls)


@needs_just
def test_bad_env_refused(tmp_path: Path) -> None:
    proc, calls = _run_migrate(tmp_path, ["dev", "v5.1"], BEHIND)
    assert proc.returncode == 1 and calls == []


@needs_just
def test_staging_behind_migrates_in_order_and_never_starts_master(tmp_path: Path) -> None:
    proc, calls = _run_migrate(tmp_path, ["staging", "v5.1-rc1"], BEHIND)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    joined = "\n".join(calls)
    order = [joined.index(m) for m in (" pull master", "up -d --wait postgres", " python -c",
                                        " stop master", "snapshot-and-migrate")]
    assert order == sorted(order)
    assert "-p codex-slack" in joined
    assert not re.search(r"\bup\b(?!.*postgres$)", joined, re.M)
    assert "verify-schema" not in joined
    out = proc.stdout
    assert f"revision before: {OLD}" in out and f"revision after:  {HEAD}" in out
    assert "snapshots/T1" in out
    assert "secretpw" not in out and "secretpw" not in proc.stderr
    assert out.rstrip().endswith("Next: just deploy staging v5.1-rc1")


@needs_just
def test_already_at_head_is_idempotent(tmp_path: Path) -> None:
    proc, calls = _run_migrate(tmp_path, ["staging", "v5.1"], f"state=current current={HEAD} head={HEAD}")
    assert proc.returncode == 0
    assert "already at the image head" in proc.stdout
    assert not any(" stop " in c or "snapshot-and-migrate" in c for c in calls)


@pytest.mark.parametrize("state", ["ahead", "unknown"])
@needs_just
def test_ahead_or_unknown_refuses_and_changes_nothing(tmp_path: Path, state: str) -> None:
    proc, calls = _run_migrate(tmp_path, ["staging", "v5.0"], f"state={state} current=000000000009 head={OLD}")
    assert proc.returncode == 1
    assert "Refusing" in proc.stderr
    assert not any(" stop " in c or "snapshot-and-migrate" in c for c in calls)


@needs_just
def test_failure_after_stop_prints_recovery(tmp_path: Path) -> None:
    proc, calls = _run_migrate(tmp_path, ["staging", "v5.1-rc1"], BEHIND, fail=True)
    assert proc.returncode == 1
    assert "master is STOPPED" in proc.stderr
    assert "just migrate staging v5.1-rc1" in proc.stderr
    assert "just deploy staging <previous-tag>" in proc.stderr
    assert "secretpw" not in proc.stdout + proc.stderr
    assert "boom" in proc.stdout  # full output kept
