import asyncio
import logging
import logging.handlers
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import models  # noqa: F401  (register tables; do NOT `import app.models` — it would bind the name `app`)
from app.api.v1.endpoints.dashboard import router as dashboard_router
from app.api.v1.endpoints.dev_demo import router as dev_demo_router
from app.api.v1.endpoints.dashboard import ws_router as dashboard_ws_router
from app.api.v1.endpoints.openai_realtime_emergency import initialize_cached_audio
from app.api.v1.endpoints.openai_realtime_emergency import router as realtime_router
from app.api.v1.endpoints.twilio import router as twilio_router
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.database import IS_POSTGRES
from app.services.background_workers import start_workers
from app.services.dashboard_hub import hub

os.makedirs("log", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.TimedRotatingFileHandler("log/app.log", when="H", interval=1, backupCount=24, utc=False),
    ],
)
logger = logging.getLogger(__name__)


def _bootstrap_local_db() -> None:
    """Zero-setup local runs: on SQLite create the tables and seed the synthetic demo fleet once.
    On Postgres use `python scripts/init_db.py` or `alembic upgrade head` instead."""
    if IS_POSTGRES:
        return
    from app.models.resource import Resource
    from app.services.seed import seed

    from app.services.seed import PLACED_BATON_ROUGE, SPEED, UNIT_DEPARTMENT

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(Resource).count() == 0:
            logger.info("Empty local database — seeding synthetic demo data: %s", seed(db))
            db.commit()
        else:
            # Databases seeded before drill F existed have no crew near Baton Rouge; top them up so the
            # drill can dispatch. Idempotent: only callsigns that are missing are added.
            added = 0
            for callsign, rtype, lat, lng, caps, status, contact in PLACED_BATON_ROUGE:
                if db.query(Resource).filter(Resource.callsign == callsign).first():
                    continue
                db.add(Resource(callsign=callsign, type=rtype, status=status, capabilities=caps, lat=lat,
                                lng=lng, department_key=UNIT_DEPARTMENT[rtype], speed_kmh=SPEED[rtype],
                                queue_load=0, contact_number=contact, is_synthetic=True))
                added += 1
            if added:
                logger.info("Added %d synthetic unit(s) for the out-of-city drill", added)
                db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    hub.bind_loop(asyncio.get_running_loop())
    await asyncio.to_thread(_bootstrap_local_db)
    await initialize_cached_audio()
    tasks = start_workers()
    logger.info("Ready — dashboard at /dashboard/ (dispatch mode: %s)", settings.DISPATCH_MODE)
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title=settings.app_name, version=settings.app_version, debug=settings.debug, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/ping")
async def ping():
    return JSONResponse(content={"status": "ok"})


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard/")


app.include_router(twilio_router)
app.include_router(realtime_router)
app.include_router(dashboard_router)
app.include_router(dashboard_ws_router)
app.include_router(dev_demo_router)  # 404s unless ENABLE_DEV_ENDPOINTS=true
app.mount("/dashboard", StaticFiles(directory="app/static/dashboard", html=True), name="dashboard")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        timeout_keep_alive=5,
        access_log=False,
        log_level="warning",
    )
