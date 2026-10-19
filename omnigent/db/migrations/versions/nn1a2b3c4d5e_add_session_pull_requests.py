"""Persist session PR associations independently of execution hosts.

Revision ID: nn1a2b3c4d5e
Revises: mm1a2b3c4d5e

Deploy this nullable column before enabling pull_request_management. Older
servers ignore it; disabling the flag preserves the stored associations.
"""

import sqlalchemy as sa
from alembic import op

revision = "nn1a2b3c4d5e"
down_revision = "mm1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "omnigent_conversation_metadata",
        sa.Column("pull_requests", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("omnigent_conversation_metadata", "pull_requests")
