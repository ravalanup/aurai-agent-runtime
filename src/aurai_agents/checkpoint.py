"""Where graph state lives. A run's state is checkpointed after every step, so a run that is waiting for an approval
survives a restart and can resume on any replica. The deployed store is Postgres; memory is for tests and for a single
local process."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver


def make_checkpointer(database_url: str):
    """Return (checkpointer, close). With no database URL, state is kept in memory and lost on restart."""
    if not database_url:
        return InMemorySaver(), lambda: None
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import dict_row

    # PostgresSaver wants autocommit and dict rows on the connection it is given.
    conn = Connection.connect(database_url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
    saver = PostgresSaver(conn)
    saver.setup()
    return saver, conn.close
