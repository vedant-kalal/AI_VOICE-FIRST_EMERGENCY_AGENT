from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import relationship

from app.models.base import BaseUUIDAuditModel, JSONType, in_check

ASSIGNMENT_STATUSES = ("pending_approval", "dispatched", "en_route", "arrived", "rejected", "cancelled", "completed")


class Assignment(BaseUUIDAuditModel):
    """A resource assigned to an incident."""

    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint(in_check("status", ASSIGNMENT_STATUSES), name="ck_assignments_status"),
        Index("ix_assignments_status_eta", "status", "eta_at"),
    )

    incident_id = Column(Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    resource_id = Column(Uuid(as_uuid=True), ForeignKey("resources.id"), nullable=False, index=True)
    status = Column(String(30), default="dispatched", nullable=False, index=True)
    # pending_approval | dispatched | en_route | arrived | rejected | cancelled | completed
    eta_minutes = Column(Float)
    eta_at = Column(DateTime(timezone=True))
    distance_km = Column(Float)
    score_breakdown = Column(JSONType)  # why this unit won (PDF §5.3 ranking transparency)
    reason = Column(Text)
    approved_by = Column(String(100))
    delay_escalated_at = Column(DateTime(timezone=True))  # set once the watchdog escalated a late unit

    incident = relationship("Incident", back_populates="assignments")
    resource = relationship("Resource")


class DispatchNotification(BaseUUIDAuditModel):
    """Every alert pushed to a crew or department (SMS / webhook / dashboard)."""

    __tablename__ = "dispatch_notifications"
    __table_args__ = (
        CheckConstraint(in_check("channel", ("dashboard", "sms", "webhook")), name="ck_dispatch_notifications_channel"),
        CheckConstraint(in_check("status", ("sent", "held_for_approval", "failed", "simulated")),
                        name="ck_dispatch_notifications_status"),
    )

    incident_id = Column(Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True)
    assignment_id = Column(Uuid(as_uuid=True), ForeignKey("assignments.id"), nullable=True)
    resource_id = Column(Uuid(as_uuid=True), ForeignKey("resources.id"), nullable=True)
    channel = Column(String(30), nullable=False)  # dashboard | sms | webhook
    recipient = Column(String(255))  # callsign / phone / department key
    payload = Column(JSONType)
    status = Column(String(30), default="sent")  # sent | held_for_approval | failed | simulated
    error = Column(Text)
