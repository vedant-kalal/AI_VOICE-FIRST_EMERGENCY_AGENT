"""Create the schema and (optionally) seed synthetic demo data.

    python scripts/init_db.py            # create tables + seed
    python scripts/init_db.py --no-seed  # tables only
    python scripts/init_db.py --reset    # drop everything first (DESTRUCTIVE — dev DB only)

On Postgres this also enables PostGIS (used by resource_service's ST_DWithin path). With the default
SQLite URL no extension is needed. Alembic (alembic/versions/0001_initial_schema.py) builds the same schema.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401  (register tables)
from app.core.config import settings  # noqa: E402
from app.core.database import IS_POSTGRES, Base, SessionLocal, engine  # noqa: E402
from app.services.seed import seed  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--no-seed", action="store_true")
    p.add_argument("--reset", action="store_true", help="drop all tables first (destructive)")
    args = p.parse_args()

    print(f"Database: {engine.url.render_as_string(hide_password=True)}")
    if IS_POSTGRES:
        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    if args.reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    print("Schema ready.")
    if not args.no_seed:
        db = SessionLocal()
        try:
            counts = seed(db, reset=True)
            db.commit()
            print(f"Seeded synthetic demo data: {counts}  (dispatch mode: {settings.DISPATCH_MODE})")
        finally:
            db.close()


if __name__ == "__main__":
    main()
