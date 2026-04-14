"""initial contract schema

Revision ID: 0001_initial_contract_schema
Revises:
Create Date: 2026-04-13 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_contract_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


contract_status_enum = sa.Enum(
    "CREATED",
    "PROCESSING",
    "CANCELLED",
    "FAILED",
    name="contractstatus",
    native_enum=False,
)

cancel_request_status_enum = sa.Enum(
    "PROCESSING",
    "SUCCESS",
    "FAILED",
    name="cancelrequeststatus",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("refundable_amount", sa.Numeric(), nullable=False),
        sa.Column("status", contract_status_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_contracts_status", "contracts", ["status"], unique=False)

    op.create_table(
        "cancel_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("status", cancel_request_status_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("result", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_cancel_requests_contract_id", "cancel_requests", ["contract_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_cancel_requests_contract_id", table_name="cancel_requests")
    op.drop_table("cancel_requests")
    op.drop_index("ix_contracts_status", table_name="contracts")
    op.drop_table("contracts")
