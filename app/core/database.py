from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

_url = settings.database_url
_is_sqlite = _url.startswith("sqlite")

if _is_sqlite:
    # Tools run in worker threads (asyncio.to_thread) so the audio loop never blocks;
    # SQLite must allow cross-thread use. Fine for a local demo — use Postgres for real load.
    engine = create_engine(
        _url,
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )
else:
    engine = create_engine(
        _url,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={"connect_timeout": 60},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

DIALECT = engine.dialect.name  # "postgresql" | "sqlite"
IS_POSTGRES = DIALECT == "postgresql"


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope():
    """Commit-or-rollback session for service code / tool executor (ai-callcenter's
    `context_manager(SessionLocal())` pattern)."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
