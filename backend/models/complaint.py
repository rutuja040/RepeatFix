from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from backend.db.session import Base


class Complaint(Base):
    __tablename__ = "complaints"

    id = Column(Integer, primary_key=True, index=True)
    complaint_id = Column(String, unique=True, index=True, nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    location_label = Column(String, nullable=True)
    administrative_area = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, default="open", nullable=False)
    assigned_department = Column(String, nullable=True)
    source = Column(String, nullable=True)
    synthetic = Column(Boolean, default=False, nullable=False)
    evidence_metadata = Column(Text, nullable=True)
    severity_label = Column(String, nullable=True)
    data_quality_flags = Column(Text, nullable=True)
    priority_score = Column(Float, nullable=True)
    priority_rationale = Column(Text, nullable=True)
    raw_imported_values = Column(Text, nullable=True)

    history = relationship("ComplaintHistory", back_populates="complaint", cascade="all, delete-orphan")


class ComplaintHistory(Base):
    __tablename__ = "complaint_history"

    id = Column(Integer, primary_key=True, index=True)
    complaint_id = Column(Integer, ForeignKey("complaints.id", ondelete="CASCADE"), nullable=False)
    field_name = Column(String, nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())
    changed_by = Column(String, nullable=True)

    complaint = relationship("Complaint", back_populates="history")
