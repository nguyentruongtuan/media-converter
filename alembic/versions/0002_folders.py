"""Folders that group a user's videos.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "folders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner", "name", name="uq_folders_owner_name"),
    )
    op.add_column("videos", sa.Column("folder_id", sa.String(36)))
    op.create_foreign_key(
        "fk_videos_folder_id", "videos", "folders", ["folder_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_videos_folder_id", "videos", ["folder_id"])


def downgrade() -> None:
    op.drop_index("ix_videos_folder_id", table_name="videos")
    op.drop_constraint("fk_videos_folder_id", "videos", type_="foreignkey")
    op.drop_column("videos", "folder_id")
    op.drop_table("folders")
