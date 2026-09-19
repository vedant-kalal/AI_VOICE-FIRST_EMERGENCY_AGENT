from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import relationship

from app.models.base import BaseUUIDAuditModel, JSONType, in_check

CALL_STATUSES = ("in_progress", "completed", "transferred_to_human", "caller_disconnect")


class Call(BaseUUIDAuditModel):
    """One inbound emergency call (PDF §11 `calls` table)."""

    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint(in_check("status", CALL_STATUSES), name="ck_calls_status"),
        Index("ix_calls_live_started", "is_live", "started_at"),
    )

    call_sid = Column(String(255), nullable=False, index=True)
    from_number = Column(String(32))
    status = Column(String(50), default="in_progress")  # in_progress | completed | transferred_to_human | caller_disconnect
    is_live = Column(Boolean, default=True, nullable=False)  # dashboard "caller still on the line" indicator
    started_at = Column(DateTime(timezone=True))
    ended_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Integer)
    caller_language = Column(String(50))
    summary = Column(Text)
    transferred_to_human = Column(Boolean, default=False)
    user_spoken = Column(Boolean)
    incident_id = Column(Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL"), nullable=True, index=True)

    transcripts = relationship("CallTranscript", back_populates="call", order_by="CallTranscript.seq")
    tool_calls = relationship("AgentToolCall", back_populates="call", order_by="AgentToolCall.created_at")


class CallTranscript(BaseUUIDAuditModel):
    """One utterance (PDF §11 `call_transcripts`). Stored in order; role is caller|agent."""

    __tablename__ = "call_transcripts"
    __table_args__ = (
        CheckConstraint(in_check("role", ("caller", "agent")), name="ck_call_transcripts_role"),
        Index("uq_call_transcripts_call_seq", "call_id", "seq", unique=True),
    )

    call_id = Column(Uuid(as_uuid=True), ForeignKey("calls.id"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    spoken_at = Column(DateTime(timezone=True))

    call = relationship("Call", back_populates="transcripts")


class AgentToolCall(BaseUUIDAuditModel):
    """Audit trail of every tool call the agent made, with timing (PDF §11 `agent_tool_calls`)."""

    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        CheckConstraint(in_check("status", ("success", "error", "rejected", "not_found", "none_available")),
                        name="ck_agent_tool_calls_status"),
    )

    call_id = Column(Uuid(as_uuid=True), ForeignKey("calls.id"), nullable=True, index=True)
    incident_id = Column(Uuid(as_uuid=True), ForeignKey("incidents.id"), nullable=True, index=True)
    tool_name = Column(String(100), nullable=False)
    arguments = Column(JSONType)
    result = Column(JSONType)
    status = Column(String(20))  # success | error | rejected
    duration_ms = Column(Integer)

    call = relationship("Call", back_populates="tool_calls")
