"""Clear the operational tables and put the fleet back where it started.

For a clean board before a demo: every incident, call, transcript, assignment, escalation and audit row
goes, then the synthetic fleet and facilities are re-seeded at their original positions. Departments and
their on-call contacts are left alone.

    python scripts/reset_demo.py            # ask first
    python scripts/reset_demo.py --yes      # no prompt

Restart the backend afterwards: the dashboard hub keeps the last 200 events in memory and replays them to
any console that connects, so a wipe without a restart leaves ghosts on the wire.
"""
import sys

sys.path.insert(0, ".")

from app.core.database import SessionLocal  # noqa: E402
from app.models.call import AgentToolCall, Call, CallTranscript  # noqa: E402
from app.models.department import CallHandoff, EscalationAttempt  # noqa: E402
from app.models.dispatch import Assignment, DispatchNotification  # noqa: E402
from app.models.incident import Escalation, Incident, IncidentEvent, IncidentReport  # noqa: E402
from app.models.resource import ResourceLocationPing  # noqa: E402
from app.services.seed import seed  # noqa: E402

# Children before parents: SQLite enforces the foreign keys these rows point at.
ORDER = [
    EscalationAttempt, CallHandoff, AgentToolCall, CallTranscript, ResourceLocationPing,
    DispatchNotification, Assignment, Escalation, IncidentEvent, IncidentReport, Call, Incident,
]


def main() -> int:
    if "--yes" not in sys.argv:
        print(__doc__)
        if input("Delete every incident and call in this database? [y/N] ").strip().lower() != "y":
            print("Nothing was deleted.")
            return 1

    db = SessionLocal()
    try:
        counts = {}
        for model in ORDER:
            counts[model.__tablename__] = db.query(model).delete(synchronize_session=False)
        db.flush()
        fleet = seed(db)          # re-places every synthetic unit and facility
        db.commit()
    finally:
        db.close()

    wiped = ", ".join(f"{table} {n}" for table, n in counts.items() if n)
    print("Cleared:", wiped or "nothing (already empty)")
    print("Re-seeded:", fleet)
    print("\nRestart the backend so the live event backlog is dropped too.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
