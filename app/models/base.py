import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB

from app.core.database import Base

# JSONB on Postgres, plain JSON elsewhere (SQLite for zero-infra local runs).
JSONType = JSON().with_variant(JSONB(), "postgresql")


def in_check(column: str, values) -> str:
    """SQL text for a `column IN ('a','b',...)` CHECK constraint (values are code constants, never user input)."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _utcnow():
    return datetime.now(timezone.utc)


class BaseUUIDAuditModel(Base):
    """Same shape as ai-callcenter's BaseUUIDAuditModel (uuid pk, is_active, audit timestamps)."""

    __abstract__ = True

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    is_active = Column(Boolean, server_default="1", default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), default=_utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), default=_utcnow, onupdate=_utcnow, nullable=False
    )
