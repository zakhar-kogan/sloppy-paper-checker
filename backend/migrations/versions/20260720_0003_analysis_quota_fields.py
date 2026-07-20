"""Add indexed analysis quota fields."""

from alembic import op
import sqlalchemy as sa

revision = "20260720_0003"
down_revision = "20260720_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("owner_hash", sa.String(64), nullable=True))
    op.add_column(
        "analyses",
        sa.Column("provider_mode", sa.String(24), nullable=False, server_default="hosted"),
    )

    analyses = sa.table(
        "analyses",
        sa.column("id", sa.String(36)),
        sa.column("request", sa.JSON()),
        sa.column("owner_hash", sa.String(64)),
        sa.column("provider_mode", sa.String(24)),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(analyses.c.id, analyses.c.request)).mappings()
    for row in rows:
        request = row["request"] or {}
        runtime = request.get("provider_runtime") or {}
        bind.execute(
            analyses.update()
            .where(analyses.c.id == row["id"])
            .values(
                owner_hash=request.get("_owner_hash"),
                provider_mode=runtime.get("mode", "hosted"),
            )
        )

    with op.batch_alter_table("analyses") as batch:
        batch.create_index("ix_analyses_owner_hash", ["owner_hash"])
        batch.create_index("ix_analyses_provider_mode", ["provider_mode"])
        batch.create_index("ix_analyses_created_at", ["created_at"])


def downgrade() -> None:
    with op.batch_alter_table("analyses") as batch:
        batch.drop_index("ix_analyses_created_at")
        batch.drop_index("ix_analyses_provider_mode")
        batch.drop_index("ix_analyses_owner_hash")
    op.drop_column("analyses", "provider_mode")
    op.drop_column("analyses", "owner_hash")
