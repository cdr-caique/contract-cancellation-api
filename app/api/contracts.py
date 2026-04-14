from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import ContractResponse, CreateContractRequest
from app.services.contract_service import ContractService

router = APIRouter(prefix="/contracts", tags=["contracts"])

db_dependency = Depends(get_db)


@router.post("", response_model=ContractResponse)
def create_contract(request: CreateContractRequest, db: Session = db_dependency):
    service = ContractService(db)
    contract = service.create_contract(request)
    return contract


@router.post("/{contract_id}/cancel")
def cancel_contract(
    contract_id: UUID,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = db_dependency,
):
    service = ContractService(db)
    return service.cancel_contract(contract_id, idempotency_key)


@router.post("/{contract_id}/reprocess")
def reprocess_contract(contract_id: UUID, db: Session = db_dependency):
    service = ContractService(db)
    return service.reprocess_contract(contract_id)
