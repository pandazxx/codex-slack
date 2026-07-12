"""
Automated contract tests for the compose file layering rules.

Covers: SC-01 through SC-17 from docs/test-plans/justfile-dotenv-deploy.md

Rules under test:
  - docker-compose.yml (base): no build:, no ports:, no digest pin
  - docker-compose.dev.yml (dev overlay): build: target dev, traefik labels,
    sre-traefik-public external network, no ports:, memory limits on all services
  - docker-compose.deploy.yml (singleton overlay): image refs both
    MASTER_RUNTIME_IMAGE and IMAGE_DIGEST, publishes port 8080, no traefik
    labels, no build:, memory limits on all services
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

BASE_FILE = REPO_ROOT / "docker-compose.yml"
DEV_FILE = REPO_ROOT / "docker-compose.dev.yml"
DEPLOY_FILE = REPO_ROOT / "docker-compose.deploy.yml"


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _services(doc: dict[str, Any]) -> dict[str, Any]:
    return doc.get("services") or {}


# ---------------------------------------------------------------------------
# docker-compose.yml (base) — SC-01 through SC-04
# ---------------------------------------------------------------------------


def test_base_parses_as_valid_yaml() -> None:
    """SC-01: docker-compose.yml must be parseable YAML."""
    doc = _load(BASE_FILE)
    assert isinstance(doc, dict), "Expected a YAML mapping at top level"


def test_base_has_no_build_key() -> None:
    """SC-02: No service in the base file may have a build: key."""
    doc = _load(BASE_FILE)
    for name, svc in _services(doc).items():
        assert "build" not in (svc or {}), (
            f"Service '{name}' in docker-compose.yml has a 'build:' key — "
            "build belongs only in the dev overlay"
        )


def test_base_has_no_ports_key() -> None:
    """SC-03: No service in the base file may publish ports."""
    doc = _load(BASE_FILE)
    for name, svc in _services(doc).items():
        assert "ports" not in (svc or {}), (
            f"Service '{name}' in docker-compose.yml has a 'ports:' key — "
            "ports belong only in the singleton deploy overlay"
        )


def test_base_master_image_has_no_digest_pin() -> None:
    """SC-04: master image in the base must not contain a @sha256 digest."""
    doc = _load(BASE_FILE)
    image = _services(doc).get("master", {}).get("image", "")
    assert "@sha256" not in image, (
        f"docker-compose.yml master image '{image}' contains a digest pin — "
        "digest pinning belongs only in docker-compose.deploy.yml"
    )


# ---------------------------------------------------------------------------
# docker-compose.dev.yml (dev overlay) — SC-05 through SC-09, SC-16
# ---------------------------------------------------------------------------


def test_dev_parses_as_valid_yaml() -> None:
    """SC-05: docker-compose.dev.yml must be parseable YAML."""
    doc = _load(DEV_FILE)
    assert isinstance(doc, dict), "Expected a YAML mapping at top level"


def test_dev_master_has_build_with_target_dev() -> None:
    """SC-06: master in the dev overlay must have build: with target: dev."""
    doc = _load(DEV_FILE)
    master = _services(doc).get("master", {})
    assert "build" in master, "docker-compose.dev.yml master missing 'build:'"
    assert master["build"].get("target") == "dev", (
        f"docker-compose.dev.yml master build target is "
        f"'{master['build'].get('target')}', expected 'dev'"
    )


def test_dev_master_has_traefik_labels() -> None:
    """SC-07: master in the dev overlay must declare at least one traefik. label."""
    doc = _load(DEV_FILE)
    master = _services(doc).get("master", {})
    labels = master.get("labels", [])
    traefik_labels = [
        lbl for lbl in labels if str(lbl).startswith("traefik.")
    ]
    assert traefik_labels, (
        "docker-compose.dev.yml master has no 'traefik.' labels"
    )


def test_dev_declares_sre_traefik_public_external_network() -> None:
    """SC-08: sre-traefik-public must be declared as an external network."""
    doc = _load(DEV_FILE)
    networks = doc.get("networks") or {}
    assert "sre-traefik-public" in networks, (
        "docker-compose.dev.yml missing 'sre-traefik-public' network declaration"
    )
    network_cfg = networks["sre-traefik-public"] or {}
    assert network_cfg.get("external") is True, (
        "docker-compose.dev.yml 'sre-traefik-public' network is not declared external: true"
    )


def test_dev_has_no_ports_key() -> None:
    """SC-09: No service in the dev overlay may publish host ports (Traefik handles routing)."""
    doc = _load(DEV_FILE)
    for name, svc in _services(doc).items():
        assert "ports" not in (svc or {}), (
            f"Service '{name}' in docker-compose.dev.yml has a 'ports:' key — "
            "the dev shape routes via Traefik, not host ports"
        )


def test_dev_all_services_have_memory_limit() -> None:
    """SC-16: Every service in the dev overlay must declare deploy.resources.limits.memory."""
    doc = _load(DEV_FILE)
    for name, svc in _services(doc).items():
        svc = svc or {}
        memory = (
            svc.get("deploy", {})
            .get("resources", {})
            .get("limits", {})
            .get("memory")
        )
        assert memory is not None, (
            f"Service '{name}' in docker-compose.dev.yml has no "
            "deploy.resources.limits.memory"
        )


# ---------------------------------------------------------------------------
# docker-compose.deploy.yml (singleton overlay) — SC-10 through SC-15, SC-17
# ---------------------------------------------------------------------------


def test_deploy_parses_as_valid_yaml() -> None:
    """SC-10: docker-compose.deploy.yml must be parseable YAML."""
    doc = _load(DEPLOY_FILE)
    assert isinstance(doc, dict), "Expected a YAML mapping at top level"


def test_deploy_master_image_references_master_runtime_image_var() -> None:
    """SC-11: master image string must reference ${MASTER_RUNTIME_IMAGE."""
    doc = _load(DEPLOY_FILE)
    image = _services(doc).get("master", {}).get("image", "")
    assert "${MASTER_RUNTIME_IMAGE" in image, (
        f"docker-compose.deploy.yml master image '{image}' does not reference "
        "'${{MASTER_RUNTIME_IMAGE'"
    )


def test_deploy_master_image_references_image_digest_var() -> None:
    """SC-12: master image string must reference @${IMAGE_DIGEST."""
    doc = _load(DEPLOY_FILE)
    image = _services(doc).get("master", {}).get("image", "")
    assert "@${IMAGE_DIGEST" in image, (
        f"docker-compose.deploy.yml master image '{image}' does not reference "
        "'@${{IMAGE_DIGEST'"
    )


def test_deploy_master_publishes_port_8080() -> None:
    """SC-13: master in the deploy overlay must publish container port 8080 on the host."""
    doc = _load(DEPLOY_FILE)
    ports = _services(doc).get("master", {}).get("ports", [])
    assert ports, "docker-compose.deploy.yml master has no 'ports:' entry"
    # Ports may be strings like "${MASTER_PORT:-8080}:8080" or short int forms.
    port_strings = [str(p) for p in ports]
    has_8080 = any(":8080" in p or p == "8080" for p in port_strings)
    assert has_8080, (
        f"docker-compose.deploy.yml master ports {port_strings} do not include "
        "container port 8080"
    )


def test_deploy_has_no_traefik_labels() -> None:
    """SC-14: No service in the deploy overlay may have traefik. labels."""
    doc = _load(DEPLOY_FILE)
    for name, svc in _services(doc).items():
        labels = (svc or {}).get("labels", [])
        traefik_labels = [lbl for lbl in labels if str(lbl).startswith("traefik.")]
        assert not traefik_labels, (
            f"Service '{name}' in docker-compose.deploy.yml has traefik labels: "
            f"{traefik_labels}"
        )


def test_deploy_has_no_build_key() -> None:
    """SC-15: No service in the deploy overlay may have a build: key."""
    doc = _load(DEPLOY_FILE)
    for name, svc in _services(doc).items():
        assert "build" not in (svc or {}), (
            f"Service '{name}' in docker-compose.deploy.yml has a 'build:' key — "
            "staging/prod never build from source"
        )


def test_deploy_all_services_have_memory_limit() -> None:
    """SC-17: Every service in the deploy overlay must declare deploy.resources.limits.memory."""
    doc = _load(DEPLOY_FILE)
    for name, svc in _services(doc).items():
        svc = svc or {}
        memory = (
            svc.get("deploy", {})
            .get("resources", {})
            .get("limits", {})
            .get("memory")
        )
        assert memory is not None, (
            f"Service '{name}' in docker-compose.deploy.yml has no "
            "deploy.resources.limits.memory"
        )
