from sqlalchemy.orm import Session

from app.models import CancelRequest, Contract


class ContractRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, contract: Contract):
        self.db.add(contract)
        self.db.commit()
        self.db.refresh(contract)
        return contract


class CancelRequestRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_key(self, key: str):
        return self.db.query(CancelRequest).filter(CancelRequest.idempotency_key == key).first()
