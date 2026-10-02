"""Agent runs: a 'running' status, so only one request advances a run at a time.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-02 09:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "'ready', 'waiting_for_human', 'completed', 'failed', 'cancelled'"
_NEW = "'ready', 'running', 'waiting_for_human', 'completed', 'failed', 'cancelled'"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_agent_runs_status_enum"), "agent_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_agent_runs_status_enum"), "agent_runs", f"status IN ({_NEW})"
    )


def downgrade() -> None:
    # A run interrupted mid-stage can be retried, exactly as a failed one.
    op.execute("UPDATE agent_runs SET status = 'failed' WHERE status = 'running'")
    op.drop_constraint(op.f("ck_agent_runs_status_enum"), "agent_runs", type_="check")
    op.create_check_constraint(
        op.f("ck_agent_runs_status_enum"), "agent_runs", f"status IN ({_OLD})"
    )
