from sqlalchemy import Boolean, CheckConstraint, Column, Float, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import relationship

from app.models.base import BaseUUIDAuditModel, JSONType, in_check

# Lifecycle: open -> dispatched -> resolved/closed; escalated overlays; needs_review = low-confidence
# report parked for a human (prank / unclear); merged = folded into another incident (duplicate).
INCIDENT_STATUSES = ("open", "dispatched", "escalated", "needs_review", "resolved", "closed", "merged")
ACTIVE_STATUSES = ("open", "dispatched", "escalated")


class Incident(BaseUUIDAuditModel):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint(in_check("status", INCIDENT_STATUSES), name="ck_incidents_status"),
        CheckConstraint(in_check("confidence", ("high", "medium", "low")), name="ck_incidents_confidence"),
        CheckConstraint("severity BETWEEN 0 AND 100", name="ck_incidents_severity_range"),
        CheckConstraint("lat IS NULL OR (lat BETWEEN -90 AND 90)", name="ck_incidents_lat_range"),
        CheckConstraint("lng IS NULL OR (lng BETWEEN -180 AND 180)", name="ck_incidents_lng_range"),
        Index("ix_incidents_status_created", "status", "created_at"),
        Index("ix_incidents_category_status", "category", "status"),
    )

    incident_number = Column(Integer, unique=True, nullable=False, index=True)  # human-facing "#1042"
    category = Column(String(50), nullable=False, index=True)
    sub_type = Column(String(100))
    description = Column(Text)
    address_text = Column(Text)
    lat = Column(Float)
    lng = Column(Float)
    location_confidence = Column(Float)  # 0..1 from the geocoder
    location_confirmed = Column(Boolean, default=False, nullable=False)  # caller confirmed the read-back
    people_affected = Column(Integer, default=0)
    caller_phone = Column(String(32))

    severity = Column(Integer, default=0, nullable=False)
    severity_level = Column(String(20), default="unrated")
    severity_explanation = Column(Text)
    severity_signals = Column(JSONType)  # merged structured signals (stated facts only)
    severity_estimated = Column(Boolean, default=False, nullable=False)

    status = Column(String(30), default="open", nullable=False, index=True)
    confidence = Column(String(10), default="high")  # high | medium | low
    confidence_reasons = Column(JSONType)
    escalated = Column(Boolean, default=False, nullable=False)

    report_count = Column(Integer, default=1, nullable=False)
    merged_into_id = Column(Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True)
    # No FK on purpose: calls.incident_id already points here, and a two-way FK cycle
    # breaks create_all on SQLite. This is the call that first reported the incident.
    source_call_id = Column(Uuid(as_uuid=True), nullable=True, index=True)
    embedding = Column(JSONType)  # optional description embedding for duplicate detection
    summary = Column(Text)  # AI-generated incident summary (post-call)

    reports = relationship("IncidentReport", back_populates="incident", order_by="IncidentReport.created_at")
    assignments = relationship("Assignment", back_populates="incident")
    escalations = relationship("Escalation", back_populates="incident")
    events = relationship("IncidentEvent", back_populates="incident", order_by="IncidentEvent.created_at")


class IncidentReport(BaseUUIDAuditModel):
    """Every caller report folded into an incident (the original + each merged duplicate)."""

    __tablename__ = "incident_reports"

    incident_id = Column(Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    call_id = Column(Uuid(as_uuid=True), ForeignKey("calls.id"), nullable=True)
    description = Column(Text)
    people_affected = Column(Integer, default=0)
    match_score = Column(Float)  # duplicate match score (None for the original report)
    note = Column(String(200))

    incident = relationship("Incident", back_populates="reports")


class Escalation(BaseUUIDAuditModel):
    __tablename__ = "escalations"

    incident_id = Column(Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    escalate_to = Column(JSONType, nullable=False)  # list[str] department keys
    source = Column(String(30), default="agent")  # agent | auto_severity | watchdog | dashboard

    incident = relationship("Incident", back_populates="escalations")


class IncidentEvent(BaseUUIDAuditModel):
    """Append-only incident timeline (created, merged, severity changed, assigned, escalated, handoff ...).
    Complements agent_tool_calls: that table is what the AI *did*, this is what *happened* to the incident,
    including actions by humans, the watchdog and the system."""

    __tablename__ = "incident_events"
    __table_args__ = (
        CheckConstraint(in_check("actor", ("agent", "system", "human", "watchdog")), name="ck_incident_events_actor"),
        Index("ix_incident_events_incident_created", "incident_id", "created_at"),
    )

    incident_id = Column(Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(60), nullable=False)
    actor = Column(String(20), nullable=False, default="system")
    payload = Column(JSONType)

    incident = relationship("Incident", back_populates="events")
