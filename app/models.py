import enum
import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, Numeric, String, Uuid
from sqlalchemy.sql import func

from app.db import Base


class ContractStatus(enum.StrEnum):
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class CancelRequestStatus(enum.StrEnum):
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (Index("ix_contracts_status", "status"),)

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    amount = Column(Numeric, nullable=False)
    refundable_amount = Column(Numeric, nullable=False)
    status = Column(
        Enum(ContractStatus, native_enum=False),
        default=ContractStatus.CREATED,
        nullable=False,
    )

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class CancelRequest(Base):
    __tablename__ = "cancel_requests"
    __table_args__ = (Index("ix_cancel_requests_contract_id", "contract_id"),)

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contract_id = Column(Uuid(as_uuid=True), ForeignKey("contracts.id"), nullable=False)
    idempotency_key = Column(String, unique=True, nullable=False)

    status = Column(
        Enum(CancelRequestStatus, native_enum=False),
        default=CancelRequestStatus.PROCESSING,
        nullable=False,
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    result = Column(String, nullable=True)
