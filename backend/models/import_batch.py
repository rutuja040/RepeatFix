from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from backend.db.session import Base


class ImportBatch(Base):
    """One CSV import attempt.

    A batch is created for every real (non-dry-run) import, including failed and
    empty ones, so provenance is never lost. Counts are stored explicitly so a
    batch can be audited without re-reading the source file (which is not kept).
    """

    __tablename__ = "import_batches"

    id = Column(Integer, primary_key=True, index=True)
    import_id = Column(String, unique=True, index=True, nullable=False)
    source_filename = Column(String, nullable=True)
    source_label = Column(String, nullable=True)
    dataset_identity = Column(String, nullable=True, index=True)
    content_sha256 = Column(String, nullable=True)
    status = Column(String, nullable=False)
    dry_run = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    total_rows = Column(Integer, default=0, nullable=False)
    accepted_rows = Column(Integer, default=0, nullable=False)
    rejected_rows = Column(Integer, default=0, nullable=False)
    skipped_rows = Column(Integer, default=0, nullable=False)
    flagged_rows = Column(Integer, default=0, nullable=False)
    exact_duplicate_rows = Column(Integer, default=0, nullable=False)
    potential_duplicate_rows = Column(Integer, default=0, nullable=False)

    mapping_json = Column(Text, nullable=True)
    unmatched_json = Column(Text, nullable=True)
    issues_json = Column(Text, nullable=True)
    issues_truncated = Column(Boolean, default=False, nullable=False)
    message = Column(Text, nullable=True)

    links = relationship(
        "ComplaintImportLink", back_populates="batch", cascade="all, delete-orphan"
    )


class ComplaintImportLink(Base):
    """Traceability link from an imported complaint to its source record.

    ``source_key`` (``dataset_identity::source_record_id``) is the identity used
    for idempotent re-imports. It is nullable because a reliable source identity
    is not always available; SQLite allows multiple NULLs under a unique index.
    """

    __tablename__ = "complaint_import_links"

    id = Column(Integer, primary_key=True, index=True)
    complaint_id = Column(
        Integer,
        ForeignKey("complaints.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    import_batch_id = Column(
        Integer, ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True
    )
    dataset_identity = Column(String, nullable=True)
    source_record_id = Column(String, nullable=True)
    source_row_number = Column(Integer, nullable=True)
    source_key = Column(String, unique=True, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    complaint = relationship("Complaint", back_populates="import_links")
    batch = relationship("ImportBatch", back_populates="links")
