"""Storage for approval requests. `locked(id)` gives exclusive access while a decision is recorded, so two approvers
deciding at once cannot overwrite each other."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class Approval:
    id: str
    idempotency_key: str
    connector: str
    tool: str
    arguments: dict[str, Any]
    args_hash: str
    team: str
    use_case: str
    requester: str  # the agent's service-account subject: the caller the connector will see
    on_behalf_of: str | None  # the person who started the run, if any
    summary: str
    required: int
    created_at: float
    expires_at: float
    decisions: list[dict[str, Any]] = field(default_factory=list)  # {approver, decision, comment, at}
    token_mints: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str | dict) -> Approval:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return cls(**data)


class ApprovalStore(Protocol):
    def create(self, approval: Approval) -> bool:
        """Insert. False if (team, use_case, idempotency_key) already exists."""

    def get(self, approval_id: str) -> Approval | None: ...

    def get_by_key(self, team: str, use_case: str, key: str) -> Approval | None: ...

    def list_for_team(self, team: str, limit: int = 100) -> list[Approval]: ...

    def locked(self, approval_id: str): ...


class MemoryApprovalStore:
    def __init__(self) -> None:
        self._items: dict[str, Approval] = {}
        self._lock = threading.RLock()

    def create(self, approval: Approval) -> bool:
        with self._lock:
            if self.get_by_key(approval.team, approval.use_case, approval.idempotency_key):
                return False
            self._items[approval.id] = Approval.from_json(approval.to_json())
            return True

    def get(self, approval_id: str) -> Approval | None:
        with self._lock:
            item = self._items.get(approval_id)
            return Approval.from_json(item.to_json()) if item else None

    def get_by_key(self, team: str, use_case: str, key: str) -> Approval | None:
        with self._lock:
            for item in self._items.values():
                if (item.team, item.use_case, item.idempotency_key) == (team, use_case, key):
                    return Approval.from_json(item.to_json())
        return None

    def list_for_team(self, team: str, limit: int = 100) -> list[Approval]:
        with self._lock:
            items = [a for a in self._items.values() if a.team == team]
            items.sort(key=lambda a: a.created_at, reverse=True)
            return [Approval.from_json(a.to_json()) for a in items[:limit]]

    @contextmanager
    def locked(self, approval_id: str) -> Iterator[Approval | None]:
        with self._lock:
            current = self.get(approval_id)
            yield current
            if current is not None:
                self._items[approval_id] = current


SCHEMA = """
CREATE TABLE IF NOT EXISTS aurai_approvals (
  id text PRIMARY KEY,
  team text NOT NULL,
  use_case text NOT NULL,
  idempotency_key text NOT NULL,
  created_at double precision NOT NULL,
  data jsonb NOT NULL,
  UNIQUE (team, use_case, idempotency_key)
);
CREATE INDEX IF NOT EXISTS aurai_approvals_team_created ON aurai_approvals (team, created_at DESC);
"""


class PostgresApprovalStore:
    """The deployed store. Uses one connection pool; `locked` holds a row lock (SELECT ... FOR UPDATE) for the duration
    of the decision."""

    def __init__(self, dsn: str) -> None:
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(dsn, min_size=1, max_size=5, open=True, kwargs={"autocommit": False})

    def setup(self) -> None:
        with self._pool.connection() as conn:
            conn.execute(SCHEMA)

    def close(self) -> None:
        self._pool.close()

    def create(self, approval: Approval) -> bool:
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO aurai_approvals (id, team, use_case, idempotency_key, created_at, data) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT (team, use_case, idempotency_key) DO NOTHING "
                "RETURNING id",
                (
                    approval.id,
                    approval.team,
                    approval.use_case,
                    approval.idempotency_key,
                    approval.created_at,
                    approval.to_json(),
                ),
            ).fetchone()
            return row is not None

    def get(self, approval_id: str) -> Approval | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT data FROM aurai_approvals WHERE id = %s", (approval_id,)).fetchone()
        return Approval.from_json(row[0]) if row else None

    def get_by_key(self, team: str, use_case: str, key: str) -> Approval | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT data FROM aurai_approvals WHERE team = %s AND use_case = %s AND idempotency_key = %s",
                (team, use_case, key),
            ).fetchone()
        return Approval.from_json(row[0]) if row else None

    def list_for_team(self, team: str, limit: int = 100) -> list[Approval]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT data FROM aurai_approvals WHERE team = %s ORDER BY created_at DESC LIMIT %s", (team, limit)
            ).fetchall()
        return [Approval.from_json(r[0]) for r in rows]

    @contextmanager
    def locked(self, approval_id: str) -> Iterator[Approval | None]:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT data FROM aurai_approvals WHERE id = %s FOR UPDATE", (approval_id,)).fetchone()
            current = Approval.from_json(row[0]) if row else None
            yield current
            if current is not None:
                conn.execute(
                    "UPDATE aurai_approvals SET data = %s::jsonb WHERE id = %s", (current.to_json(), approval_id)
                )
