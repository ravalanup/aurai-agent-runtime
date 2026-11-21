# State and checkpoints

## What is checkpointed

LangGraph saves the graph state after every step. For a run that is waiting for people, that state is the proposals, the approval ids and the results so far. When the run resumes (hours later, on another replica, after a deploy), the graph continues from the saved step.

`interrupt()` re-runs the interrupted node from its top on resume. That is why the approval nodes are written to be safe to repeat: requests carry an idempotency key, and `wait` recomputes statuses after it resumes.

## Why Postgres

The deployed checkpointer is Postgres (`langgraph-checkpoint-postgres`), and the approval requests live in the same database in a separate table. The alternatives were Redis and process memory:

| | Postgres | Redis | Memory |
|---|---|---|---|
| Survives a restart | Yes | Only with persistence configured and monitored | No |
| A run can wait a day for an approval | Yes: it is durable data | Possible, but a cache eviction or failover can lose it | No |
| Audit and recovery | Standard backups, point-in-time recovery, SQL for investigations | Snapshot-based; awkward to query | None |
| Transactions | Yes: the approval decision takes a row lock, so two approvers cannot race | Not for this | n/a |
| Fit for the platform | Already run (RDS in AWS, an in-cluster instance locally) | Run for the connectors' single-use token set, which *is* cache-like data | Tests and one local process |

The rule behind the choice: if losing it means losing a person's decision or an in-flight action, it belongs in the database of record. Redis stays where the data is disposable (the connectors' used-token set, whose entries expire in minutes).

Postgres is also what a company already knows how to back up, secure and audit, so there is nothing new to operate.

## Layout

- A separate database, `aurai_agents`, so the LangGraph tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) never share a schema with another service.
- `aurai_approvals`: one row per request; the record is a JSONB document, with a unique key on (team, use case, idempotency key).
- `aurai-agents init-db` creates all of it. The chart runs it as a pre-install/upgrade Job so replicas do not race to create tables.

## Connection handling

The LangGraph saver uses one autocommit connection with `prepare_threshold=0` (it manages its own transactions). The approval store uses a small pool and takes `SELECT ... FOR UPDATE` while a decision is recorded. These are separate on purpose.

## Retention (not built)

Checkpoints are kept forever in this prototype. A deployment needs a policy: delete the checkpoints of completed runs after a retention period, keep approval records according to the audit requirement.
