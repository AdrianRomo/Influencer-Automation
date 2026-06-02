"""Add credit_transactions ledger.

Workspaces already carry credits_balance (migration 014). This adds the
append-only ledger of charges / grants / refunds, with a unique idempotency key
so retries don't double-charge or double-refund.

Revision ID: 016
Revises: 015
"""
from alembic import op
import sqlalchemy as sa


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if "credit_transactions" in set(sa.inspect(conn).get_table_names()):
        return
    op.create_table(
        "credit_transactions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column("ref_type", sa.String(length=50), nullable=True),
        sa.Column("ref_id", sa.String(), nullable=True),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_credit_txn_idem"),
    )
    op.create_index("ix_credit_transactions_workspace_id", "credit_transactions", ["workspace_id"])
    op.create_index("ix_credit_txn_workspace_created", "credit_transactions", ["workspace_id", "created_at"])


def downgrade() -> None:
    conn = op.get_bind()
    if "credit_transactions" not in set(sa.inspect(conn).get_table_names()):
        return
    op.drop_table("credit_transactions")
