"""initial schema: calls, incidents, resources, assignments, audit trail

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-19

Baseline migration. It builds the schema from the current models (`Base.metadata.create_all`) instead of
listing every column by hand — appropriate for a first migration of a new project. Every LATER change should
be a normal, explicit migration (`alembic revision --autogenerate -m "..."`).

On Postgres it also enables PostGIS and adds an expression GiST index so the nearest-resource query's
ST_DWithin can use an index.
"""
from alembic import op

from app import models  # noqa: F401
from app.core.database import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    Base.metadata.create_all(bind=bind, checkfirst=True)
    if is_pg:
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_resources_geog ON resources USING GIST "
            "((ST_SetSRID(ST_MakePoint(lng, lat), 4326)::geography))"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_resources_geog")
    Base.metadata.drop_all(bind=bind)
