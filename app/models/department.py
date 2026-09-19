"""Departments, their on-call contacts, and the Twilio escalation-transfer records.

Counterpart of ai-callcenter's staff / nurse_handoffs / nurse_handoff_outreach tables (the after-hours
escalation ladder), reshaped for this project:

  departments ──< department_contacts        who to ring, in what order (priority), incl. the director
       └────────< call_handoffs ──< escalation_attempts     one row per real dial attempt (audit log)
"""
from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid,
)
from sqlalchemy.orm import relationship

from app.models.base import BaseUUIDAuditModel, JSONType, in_check

CONTACT_ROLES = ("on_call", "supervisor", "director")
HANDOFF_STATUSES = ("pending", "dialing", "answered", "exhausted", "failed", "simulated", "cancelled")
DIAL_STATUSES = ("pending", "initiated", "ringing", "answered", "completed", "busy", "no-answer", "failed", "canceled")


class Department(BaseUUIDAuditModel):
    __tablename__ = "departments"
    __table_args__ = (
        CheckConstraint("dial_timeout_seconds BETWEEN 5 AND 120", name="ck_departments_dial_timeout"),
        CheckConstraint("repeat_cycles BETWEEN 1 AND 5", name="ck_departments_repeat_cycles"),
    )

    key = Column(String(50), nullable=False, unique=True)  # taxonomy key: fire_dept, police, ems, ...
    name = Column(String(255), nullable=False)
    facility_type = Column(String(30))  # what its "centre" is: fire_station / police_station / hospital ...
    # Escalation policy: ring every on_call contact in priority order, `repeat_cycles` times, then the director.
    dial_timeout_seconds = Column(Integer, nullable=False, default=30)
    repeat_cycles = Column(Integer, nullable=False, default=2)
    alert_webhook_url = Column(Text)  # department's own API/webhook ("department APIs are the integration point")
    timezone = Column(String(50), nullable=False, default="Asia/Kolkata")

    contacts = relationship("DepartmentContact", back_populates="department",
                            order_by="DepartmentContact.priority", cascade="all, delete-orphan")


class DepartmentContact(BaseUUIDAuditModel):
    __tablename__ = "department_contacts"
    __table_args__ = (
        CheckConstraint(in_check("role", CONTACT_ROLES), name="ck_department_contacts_role"),
        UniqueConstraint("department_id", "priority", name="uq_department_contacts_dept_priority"),
        Index("ix_department_contacts_dept_active", "department_id", "is_active"),
    )

    department_id = Column(Uuid(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="on_call")
    phone = Column(String(20), nullable=False)  # E.164, validated before it is ever placed in TwiML
    priority = Column(Integer, nullable=False)  # 1 = first to ring
    zone = Column(String(20))  # optional: north / south / east (zone-based ladders, cf. ai-callcenter Maintenance)
    # Optional weekly on-duty window, e.g. {"days": [0,1,2,3,4], "start": "18:00", "end": "07:00"}; null = 24x7
    duty_window = Column(JSONType)

    department = relationship("Department", back_populates="contacts")


class CallHandoff(BaseUUIDAuditModel):
    """A live call being transferred to a department's escalation ladder (counterpart of nurse_handoffs)."""

    __tablename__ = "call_handoffs"
    __table_args__ = (
        CheckConstraint(in_check("status", HANDOFF_STATUSES), name="ck_call_handoffs_status"),
        Index("ix_call_handoffs_call_sid", "call_sid"),
    )

    call_id = Column(Uuid(as_uuid=True), ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True)
    incident_id = Column(Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True)
    department_id = Column(Uuid(as_uuid=True), ForeignKey("departments.id"), nullable=True)
    call_sid = Column(String(255), nullable=False)
    reason = Column(Text)  # context summary handed to the human (location, what happened, what is dispatched)
    status = Column(String(20), nullable=False, default="pending")
    ladder = Column(JSONType)  # the frozen step list [{step, cycle, contact_id, name, role, phone}, ...]
    total_steps = Column(Integer, nullable=False, default=0)
    current_step = Column(Integer, nullable=False, default=0)
    answered_contact_id = Column(Uuid(as_uuid=True), ForeignKey("department_contacts.id"), nullable=True)
    started_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))

    attempts = relationship("EscalationAttempt", back_populates="handoff", order_by="EscalationAttempt.step_index",
                            cascade="all, delete-orphan")
    department = relationship("Department")


class EscalationAttempt(BaseUUIDAuditModel):
    """One real dial attempt (and the SMS sent just before it). Every attempt is its own row, so a repeat cycle
    to the same person never overwrites the first — the mistake ai-callcenter had to fix in §18.6."""

    __tablename__ = "escalation_attempts"
    __table_args__ = (
        CheckConstraint(in_check("dial_status", DIAL_STATUSES), name="ck_escalation_attempts_dial_status"),
        UniqueConstraint("handoff_id", "step_index", name="uq_escalation_attempts_handoff_step"),
    )

    handoff_id = Column(Uuid(as_uuid=True), ForeignKey("call_handoffs.id", ondelete="CASCADE"), nullable=False,
                        index=True)
    contact_id = Column(Uuid(as_uuid=True), ForeignKey("department_contacts.id"), nullable=True)
    step_index = Column(Integer, nullable=False)
    cycle = Column(Integer, nullable=False, default=1)
    contact_name = Column(String(255))
    role = Column(String(20))
    phone = Column(String(20), nullable=False)
    sms_status = Column(String(20))  # sent | simulated | failed
    sms_sid = Column(String(64))
    dial_status = Column(String(20), nullable=False, default="pending")
    dial_call_sid = Column(String(64))  # the child call leg Twilio created for this <Number>
    answered = Column(Boolean, default=False)
    duration_seconds = Column(Integer)
    started_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))
    raw_status_events = Column(JSONType)  # Twilio statusCallback events for this leg (initiated/ringing/...)

    handoff = relationship("CallHandoff", back_populates="attempts")
