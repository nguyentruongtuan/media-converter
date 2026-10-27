"""Initial schema: videos, transcode jobs, job outputs.

Revision ID: 0001
Revises:
Create Date: 2026-10-27
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(127), nullable=False),
        sa.Column("declared_size_bytes", sa.BigInteger, nullable=False),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("source_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("duration_seconds", sa.Float),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("frame_rate", sa.Float),
        sa.Column("video_codec", sa.String(32)),
        sa.Column("audio_codec", sa.String(32)),
        sa.Column("bitrate", sa.BigInteger),
        sa.Column("has_audio", sa.Boolean, nullable=False),
        sa.Column("rotation", sa.Integer, nullable=False),
        sa.Column("container", sa.String(64)),
        sa.Column("probe", sa.JSON),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_videos_owner", "videos", ["owner"])
    op.create_index("ix_videos_status", "videos", ["status"])

    op.create_table(
        "transcode_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("video_id", sa.String(36), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("presets", sa.JSON, nullable=False),
        sa.Column("formats", sa.JSON, nullable=False),
        sa.Column("crf", sa.Integer, nullable=False),
        sa.Column("x264_preset", sa.String(16), nullable=False),
        sa.Column("progress", sa.Float, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("worker_id", sa.String(255)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner", "idempotency_key", name="uq_jobs_owner_idempotency_key"),
    )
    op.create_index("ix_transcode_jobs_video_id", "transcode_jobs", ["video_id"])
    op.create_index("ix_transcode_jobs_owner", "transcode_jobs", ["owner"])
    op.create_index("ix_transcode_jobs_status", "transcode_jobs", ["status"])
    op.create_index("ix_transcode_jobs_lease_expires_at", "transcode_jobs", ["lease_expires_at"])

    op.create_table(
        "job_outputs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "job_id", sa.String(36), sa.ForeignKey("transcode_jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("rendition", sa.String(16)),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("content_type", sa.String(127), nullable=False),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("bitrate", sa.BigInteger),
        sa.Column("size_bytes", sa.BigInteger),
    )
    op.create_index("ix_job_outputs_job_id", "job_outputs", ["job_id"])


def downgrade() -> None:
    op.drop_table("job_outputs")
    op.drop_table("transcode_jobs")
    op.drop_table("videos")
