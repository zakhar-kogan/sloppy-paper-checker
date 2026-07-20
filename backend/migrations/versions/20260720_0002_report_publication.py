"""Add report expiry and opt-in publication fields."""

from datetime import timedelta

from alembic import op
import sqlalchemy as sa

revision = "20260720_0002"
down_revision = "20260716_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("analyses", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("analyses", sa.Column("public_slug", sa.String(32), nullable=True))

    analyses = sa.table(
        "analyses",
        sa.column("id", sa.String(36)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("expires_at", sa.DateTime(timezone=True)),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(analyses.c.id, analyses.c.created_at)).mappings()
    for row in rows:
        bind.execute(
            analyses.update()
            .where(analyses.c.id == row["id"])
            .values(expires_at=row["created_at"] + timedelta(hours=24))
        )

    with op.batch_alter_table("analyses") as batch:
        batch.alter_column("expires_at", nullable=False)
        batch.create_index("ix_analyses_expires_at", ["expires_at"])
        batch.create_index("ix_analyses_published_at", ["published_at"])
        batch.create_index("ix_analyses_public_slug", ["public_slug"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("analyses") as batch:
        batch.drop_index("ix_analyses_public_slug")
        batch.drop_index("ix_analyses_published_at")
        batch.drop_index("ix_analyses_expires_at")
    op.drop_column("analyses", "public_slug")
    op.drop_column("analyses", "published_at")
    op.drop_column("analyses", "expires_at")
