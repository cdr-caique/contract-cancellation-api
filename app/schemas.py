from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class CreateContractRequest(BaseModel):
    amount: Decimal
    refundable_amount: Decimal


class ContractResponse(BaseModel):
    id: UUID
    amount: Decimal
    refundable_amount: Decimal
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("amount", "refundable_amount", mode="before")
    @classmethod
    def normalize_decimal(cls, v) -> Decimal:
        d = Decimal(str(v))
        normalized = d.normalize()
        sign, digits, exponent = normalized.as_tuple()
        if exponent >= 0:
            return Decimal(int(normalized))
        return normalized
