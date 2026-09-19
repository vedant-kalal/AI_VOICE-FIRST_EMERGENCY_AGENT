"""Offline test setup: temp SQLite DB, offline geocoder, no OpenAI / Twilio / network.

Env is set BEFORE any `app.*` import so settings/engine pick it up.
"""
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="emergency-tests-")
os.environ["IS_LOCAL"] = "true"
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TMP, 'test.db')}"
os.environ["GEOCODER_PROVIDER"] = "gazetteer"
os.environ["FACILITY_PROVIDER"] = "seed"      # no external place API in tests
os.environ["ROUTING_PROVIDER"] = "haversine"  # no external routing API in tests
os.environ["ESCALATION_DEMO_NUMBER"] = ""
os.environ["USE_EMBEDDINGS"] = "false"
os.environ["SMS_ENABLED"] = "false"
os.environ["DEPARTMENT_WEBHOOK_URL"] = ""
os.environ["DISPATCH_MODE"] = "autonomous"
os.environ["OPENAI_API_KEY"] = ""

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)  # main.py mounts app/static/dashboard and writes log/ relative to the project root

import pytest  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.services.seed import seed  # noqa: E402
from app.services.tool_executor import ToolContext, execute_tool  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    seed(db, reset=True)
    db.commit()
    db.close()
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def new_call():
    """Factory: a fresh caller's ToolContext + a `run(tool, **args)` helper."""
    counter = {"n": 0}

    def _make(phone="+919000000001"):
        counter["n"] += 1
        sid = f"TEST-CALL-{counter['n']}"
        # Same as the live handler: a Call row exists before any tool runs, so audit rows and the
        # call->incident link (incl. re-pointing on duplicate merge) are exercised for real.
        from app.core.database import session_scope
        from app.models.call import Call
        with session_scope() as s:
            row = Call(call_sid=sid, from_number=phone, status="in_progress", is_live=True)
            s.add(row)
            s.flush()
            call_id = row.id
        ctx = ToolContext(call_id=call_id, call_sid=sid, caller_phone=phone)

        def run(tool, **args):
            return execute_tool(tool, args, ctx)

        return ctx, run

    return _make
