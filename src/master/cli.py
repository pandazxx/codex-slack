from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import uuid

from .registry import AgentRegistry
from .runtime_adapter import PodmanRuntimeAdapter
from .service import MasterService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Master orchestrator CLI")
    parser.add_argument("--registry", default="data/master/agents.json", help="Path to registry json")
    parser.add_argument("--dry-run", action="store_true", help="Do not execute podman commands")

    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List agents")
    list_parser.set_defaults(action="list")

    load_parser = sub.add_parser("load", help="Load agent")
    load_parser.add_argument("name")
    load_parser.add_argument("repo_path")
    load_parser.add_argument("channel_id")
    load_parser.set_defaults(action="load")

    start_parser = sub.add_parser("start", help="Start agent")
    start_parser.add_argument("name")
    start_parser.set_defaults(action="start")

    stop_parser = sub.add_parser("stop", help="Stop agent")
    stop_parser.add_argument("name")
    stop_parser.set_defaults(action="stop")

    status_parser = sub.add_parser("status", help="Status agent")
    status_parser.add_argument("name")
    status_parser.set_defaults(action="status")

    remove_parser = sub.add_parser("remove", help="Remove agent")
    remove_parser.add_argument("name")
    remove_parser.set_defaults(action="remove")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    registry = AgentRegistry(args.registry)
    runtime = PodmanRuntimeAdapter(dry_run=args.dry_run)
    service = MasterService(registry=registry, runtime=runtime)

    try:
        if args.action == "list":
            result = service.list_agents()
        elif args.action == "load":
            result = service.load_agent(name=args.name, repo_path=args.repo_path, channel_id=args.channel_id)
        elif args.action == "start":
            result = service.start_agent(name=args.name)
        elif args.action == "stop":
            result = service.stop_agent(name=args.name)
        elif args.action == "status":
            result = service.status(name=args.name)
        elif args.action == "remove":
            result = service.remove_agent(name=args.name)
        else:
            parser.error(f"unknown action: {args.action}")
            return 2
    except Exception as exc:  # noqa: BLE001
        payload = {
            "ok": False,
            "command": args.action,
            "code": "ERR_INTERNAL",
            "message": str(exc),
            "data": {},
            "request_id": f"req_{uuid.uuid4().hex[:12]}",
            "at": datetime.now(timezone.utc).isoformat(),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1

    payload = {
        "ok": result.ok,
        "command": args.action,
        "code": result.code,
        "message": result.message,
        "data": result.data,
        "request_id": f"req_{uuid.uuid4().hex[:12]}",
        "at": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
