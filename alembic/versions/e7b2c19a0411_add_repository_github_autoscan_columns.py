"""Add repository GitHub autoscan metadata columns.

Revision ID: e7b2c19a0411
Revises: da5121aa74fd
Create Date: 2026-11-25 12:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e7b2c19a0411"
down_revision: Union[str, Sequence[str], None] = "da5121aa74fd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add additive GitHub upstream & scan metadata columns to repositories table."""
    op.add_column("repositories", sa.Column("upstream_github_url", sa.String(length=512), nullable=True))
    op.add_column("repositories", sa.Column("github_slug", sa.String(length=128), nullable=True))
    op.add_column(
        "repositories",
        sa.Column("scan_metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("repositories", sa.Column("last_scanned_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Remove additive GitHub autoscan columns from repositories table."""
    op.drop_column("repositories", "last_scanned_at")
    op.drop_column("repositories", "scan_metadata_json")
    op.drop_column("repositories", "github_slug")
    op.drop_column("repositories", "upstream_github_url")
