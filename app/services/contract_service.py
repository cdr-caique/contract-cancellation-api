from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CancelRequest, CancelRequestStatus, Contract, ContractStatus
from app.repositories.contract_repository import (
    CancelRequestRepository,
    ContractRepository,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ContractService:
    def __init__(self, db: Session):
        self.db = db
        self.contract_repo = ContractRepository(db)
        self.cancel_repo = CancelRequestRepository(db)

    def create_contract(self, request):
        contract = Contract(
            amount=request.amount,
            refundable_amount=request.refundable_amount,
        )
        return self.contract_repo.create(contract)

    def _to_cancel_response(self, cancel_request: CancelRequest):
        return {
            "status": cancel_request.status.value.lower(),
            "result": cancel_request.result,
            "contract_id": cancel_request.contract_id,
        }

    def _mark_request_as_failed(self, contract_id: UUID, idempotency_key: str, result: str):
        existing = self.cancel_repo.get_by_key(idempotency_key)
        if existing:
            if existing.contract_id != contract_id:
                return
            existing.status = CancelRequestStatus.FAILED
            existing.result = result
            self.db.commit()
            return

        failed_request = CancelRequest(
            contract_id=contract_id,
            idempotency_key=idempotency_key,
            status=CancelRequestStatus.FAILED,
            result=result,
        )
        self.db.add(failed_request)
        self.db.commit()

    def cancel_contract(self, contract_id: UUID, idempotency_key: str):
        try:
            # verifica idempotência antes de qualquer operação
            existing = self.cancel_repo.get_by_key(idempotency_key)
            if existing:
                if existing.contract_id != contract_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency key already used for another contract",
                    )
                return self._to_cancel_response(existing)

            # cria o registro de idempotência antes de bloquear o contrato
            cancel_request = CancelRequest(
                contract_id=contract_id,
                idempotency_key=idempotency_key,
                status=CancelRequestStatus.PROCESSING,
            )

            self.db.add(cancel_request)
            self.db.flush()  # reserva a chave sem commit ainda

            # bloqueia o contrato para evitar condição de corrida
            contract = (
                self.db.query(Contract).filter(Contract.id == contract_id).with_for_update().first()
            )

            if not contract:
                raise HTTPException(status_code=404, detail="Contract not found")

            # contrato já cancelado anteriormente com outra chave
            if contract.status == ContractStatus.CANCELLED:
                cancel_request.status = CancelRequestStatus.SUCCESS
                cancel_request.result = "already_cancelled"
                self.db.commit()
                return self._to_cancel_response(cancel_request)

            if contract.created_at < _now() - timedelta(days=7):
                raise HTTPException(status_code=422, detail="Cancellation window expired")

            if contract.refundable_amount <= 0:
                raise HTTPException(status_code=422, detail="No refundable amount")

            # marca como PROCESSING antes de efetivar, para detectar falhas parciais
            contract.status = ContractStatus.PROCESSING
            contract.updated_at = _now()
            self.db.flush()

            contract.status = ContractStatus.CANCELLED
            contract.refundable_amount = 0
            contract.updated_at = _now()

            cancel_request.status = CancelRequestStatus.SUCCESS
            cancel_request.result = "cancelled"

            self.db.commit()
            return self._to_cancel_response(cancel_request)

        except IntegrityError:
            # outro request ganhou a corrida na chave de idempotência
            self.db.rollback()

            existing = self.cancel_repo.get_by_key(idempotency_key)
            if existing:
                if existing.contract_id != contract_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency key already used for another contract",
                    ) from None
                return self._to_cancel_response(existing)
            raise HTTPException(
                status_code=409, detail="Could not resolve idempotent request"
            ) from None

        except HTTPException as exc:
            self.db.rollback()
            try:
                self._mark_request_as_failed(contract_id, idempotency_key, str(exc.detail))
            except Exception:
                pass
            raise exc from None

        except Exception as exc:
            self.db.rollback()
            try:
                self._mark_request_as_failed(contract_id, idempotency_key, "internal_error")
            except Exception:
                pass
            raise exc from None

    def reprocess_contract(self, contract_id: UUID):
        try:
            contract = (
                self.db.query(Contract).filter(Contract.id == contract_id).with_for_update().first()
            )

            if not contract:
                raise HTTPException(status_code=404, detail="Contract not found")

            if contract.status != ContractStatus.PROCESSING:
                raise HTTPException(
                    status_code=422, detail="Only PROCESSING contracts can be reprocessed"
                )

            # só reprocessa se estiver travado por mais de 5 minutos
            if contract.updated_at > _now() - timedelta(minutes=5):
                raise HTTPException(
                    status_code=422, detail="Contract is not stuck long enough to reprocess"
                )

            contract.status = ContractStatus.CREATED
            contract.updated_at = _now()

            self.db.commit()
            self.db.refresh(contract)

            return {"status": "reprocessed", "contract_id": contract.id}

        except Exception as e:
            self.db.rollback()
            raise e from None
