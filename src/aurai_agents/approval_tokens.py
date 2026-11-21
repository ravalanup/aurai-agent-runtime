"""Mints the signed approval tokens the Aurai connectors verify (see aurai-mcp-connectors docs/approvals.md). The format
must match exactly: HS256 over tool, args_hash (SHA-256 of the canonical JSON of the arguments), team, use_case,
approvers, iat, exp (15 minutes at most) and a single-use jti."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Mapping
from typing import Any

import jwt

MAX_LIFETIME_SECONDS = 900


def args_hash(arguments: Mapping[str, Any]) -> str:
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def mint(
    secret: str,
    *,
    tool: str,
    arguments: Mapping[str, Any],
    team: str,
    use_case: str,
    approvers: list[str],
    lifetime_seconds: int = 600,
    now: float | None = None,
) -> str:
    if len(secret) < 32:
        raise ValueError("the approval signing key must be at least 32 characters")
    issued = int(now if now is not None else time.time())
    return jwt.encode(
        {
            "tool": tool,
            "args_hash": args_hash(arguments),
            "team": team,
            "use_case": use_case,
            "approvers": sorted(set(approvers)),
            "iat": issued,
            "exp": issued + min(lifetime_seconds, MAX_LIFETIME_SECONDS),
            "jti": uuid.uuid4().hex,
        },
        secret,
        algorithm="HS256",
    )
