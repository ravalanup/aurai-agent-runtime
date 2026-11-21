"""aurai-agents serve-runtime | serve-approvals | init-db | check | new | keycloak-plan | keycloak-apply."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import ConfigError
from .settings import Settings


def _serve_runtime(args: argparse.Namespace) -> int:
    import uvicorn

    from . import runtime_api, wiring

    settings = Settings.from_env()
    runtime, close = wiring.build_runtime(settings)
    app = runtime_api.create_app(runtime, wiring.verifier(settings, settings.agents_audience))
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        close()
    return 0


def _serve_approvals(args: argparse.Namespace) -> int:
    import uvicorn

    from . import approval_api, wiring

    settings = Settings.from_env()
    service = wiring.build_approval_service(settings)
    app = approval_api.create_app(service, wiring.verifier(settings, settings.approvals_audience))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def _init_db(_: argparse.Namespace) -> int:
    from .checkpoint import make_checkpointer
    from .wiring import build_store

    settings = Settings.from_env()
    if not settings.database_url:
        print("AURAI_DATABASE_URL is not set", file=sys.stderr)
        return 1
    _, close = make_checkpointer(settings.database_url)  # creates the LangGraph tables
    close()
    store = build_store(settings)  # creates the approvals table
    getattr(store, "close", lambda: None)()
    print("database ready")
    return 0


def _check(_: argparse.Namespace) -> int:
    from .wiring import load_config

    settings = Settings.from_env()
    try:
        agents, approvers = load_config(settings)
    except (ConfigError, OSError) as exc:
        print(f"config problem: {exc}", file=sys.stderr)
        return 1
    if len(settings.approval_signing_key) < 32:
        print("config problem: AURAI_APPROVAL_SIGNING_KEY must be at least 32 characters", file=sys.stderr)
        return 1
    print(f"ok: {len(agents)} agent(s), approver groups for {len(approvers)} team(s)")
    return 0


def _new(args: argparse.Namespace) -> int:
    from .scaffold import ScaffoldError, scaffold

    try:
        created = scaffold(Path(args.directory), args.name, args.team, args.use_case, args.tier)
    except ScaffoldError as exc:
        print(f"cannot scaffold: {exc}", file=sys.stderr)
        return 1
    for path in created:
        print(path)
    return 0


def _keycloak(args: argparse.Namespace) -> int:
    from .keycloak_clients import KeycloakAdmin, apply_plan, desired_clients, make_plan

    settings = Settings.from_env()
    from .wiring import load_config

    agents, _ = load_config(settings)
    realm_url = settings.issuer.rsplit("/realms/", 1)
    admin = KeycloakAdmin(
        realm_url[0],
        realm_url[1],
        os.environ.get("KEYCLOAK_ADMIN_USER", ""),
        os.environ.get("KEYCLOAK_ADMIN_PASSWORD", ""),
    )
    plan = make_plan(desired_clients(agents.values()), admin.list_clients())
    print(json.dumps(plan.describe(), indent=2))
    if args.command == "keycloak-apply":
        apply_plan(admin, plan, os.environ)
        print("applied")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aurai-agents")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("serve-runtime", _serve_runtime), ("serve-approvals", _serve_approvals)):
        p = sub.add_parser(name)
        p.add_argument("--host", default="0.0.0.0")  # noqa: S104 - listens inside the pod
        p.add_argument("--port", type=int, default=8080)
        p.set_defaults(fn=fn)
    sub.add_parser("init-db").set_defaults(fn=_init_db)
    sub.add_parser("check").set_defaults(fn=_check)
    new = sub.add_parser("new", help="scaffold a new agent from the template")
    new.add_argument("name")
    new.add_argument("--team", required=True)
    new.add_argument("--use-case", required=True)
    new.add_argument("--tier", default="T1", choices=("T0", "T1", "T2", "T3"))
    new.add_argument("--directory", default=".")
    new.set_defaults(fn=_new)
    for name in ("keycloak-plan", "keycloak-apply"):
        sub.add_parser(name, help="show or apply the Keycloak clients the agents need").set_defaults(fn=_keycloak)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
