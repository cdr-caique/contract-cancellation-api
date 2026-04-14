import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
import pytz
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import app
from app.models import Base, CancelRequest, Contract, ContractStatus
from app.services.contract_service import ContractService

tz_sp = pytz.timezone("America/Sao_Paulo")


@pytest.fixture()
def db_session_factory(tmp_path):
    db_file = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    yield TestingSessionLocal

    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def client(db_session_factory):
    def override_get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def create_contract(client: TestClient, amount=1000, refundable_amount=1000):
    response = client.post(
        "/contracts",
        json={"amount": amount, "refundable_amount": refundable_amount},
    )
    assert response.status_code == 200
    return response.json()


def test_create_contract(client: TestClient):
    contract = create_contract(client)

    assert contract["status"] == "CREATED"
    assert str(contract["amount"]) == "1000"
    assert str(contract["refundable_amount"]) == "1000"


def test_cancel_contract_is_idempotent_same_key(client: TestClient):
    contract = create_contract(client)
    headers = {"Idempotency-Key": "abc-123"}

    first = client.post(f"/contracts/{contract['id']}/cancel", headers=headers)
    second = client.post(f"/contracts/{contract['id']}/cancel", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["result"] == "cancelled"
    assert second.json()["result"] == "cancelled"


def test_cancel_already_cancelled_with_different_key_returns_success(client: TestClient):
    contract = create_contract(client)

    first = client.post(
        f"/contracts/{contract['id']}/cancel",
        headers={"Idempotency-Key": "first-key"},
    )
    second = client.post(
        f"/contracts/{contract['id']}/cancel",
        headers={"Idempotency-Key": "second-key"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["result"] == "already_cancelled"


def test_cancel_after_7_days_returns_422(client: TestClient, db_session_factory):
    contract = create_contract(client)

    db = db_session_factory()
    db_contract = db.query(Contract).filter(Contract.id == UUID(contract["id"])).first()
    db_contract.created_at = datetime.now(tz_sp) - timedelta(days=8)
    db.commit()
    db.close()

    response = client.post(
        f"/contracts/{contract['id']}/cancel",
        headers={"Idempotency-Key": "expired-key"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Cancellation window expired"


def test_cancel_without_refundable_amount_returns_422(client: TestClient):
    contract = create_contract(client, refundable_amount=0)

    response = client.post(
        f"/contracts/{contract['id']}/cancel",
        headers={"Idempotency-Key": "no-refund-key"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "No refundable amount"


def test_reprocess_only_processing_older_than_5_minutes(client: TestClient, db_session_factory):
    contract = create_contract(client)

    db = db_session_factory()
    db_contract = db.query(Contract).filter(Contract.id == UUID(contract["id"])).first()
    db_contract.status = ContractStatus.PROCESSING
    db_contract.updated_at = datetime.now(tz_sp) - timedelta(minutes=6)
    db.commit()
    db.close()

    response = client.post(f"/contracts/{contract['id']}/reprocess")

    assert response.status_code == 200
    assert response.json()["status"] == "reprocessed"


def test_reprocess_invalid_when_not_processing(client: TestClient):
    contract = create_contract(client)

    response = client.post(f"/contracts/{contract['id']}/reprocess")

    assert response.status_code == 422
    assert response.json()["detail"] == "Only PROCESSING contracts can be reprocessed"


def test_same_idempotency_key_on_different_contracts_returns_409(client: TestClient):
    first = create_contract(client)
    second = create_contract(client)

    r1 = client.post(
        f"/contracts/{first['id']}/cancel",
        headers={"Idempotency-Key": "reused-key"},
    )
    r2 = client.post(
        f"/contracts/{second['id']}/cancel",
        headers={"Idempotency-Key": "reused-key"},
    )

    assert r1.status_code == 200
    assert r2.status_code == 409


def test_concurrent_cancel_race_same_key(db_session_factory):
    seed_db = db_session_factory()
    contract = Contract(amount=1000, refundable_amount=1000)
    seed_db.add(contract)
    seed_db.commit()
    seed_db.refresh(contract)
    contract_id = contract.id
    seed_db.close()

    barrier = Barrier(2)
    results = []
    errors = []

    def worker():
        db = db_session_factory()
        service = ContractService(db)
        try:
            barrier.wait()
            result = service.cancel_contract(contract_id, "race-key")
            results.append(result)
        except Exception as exc:
            errors.append(exc)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(worker) for _ in range(2)]
        for future in futures:
            future.result()

    assert not errors
    assert len(results) == 2

    verify_db = db_session_factory()
    updated_contract = verify_db.query(Contract).filter(Contract.id == contract_id).first()
    cancel_rows = (
        verify_db.query(CancelRequest).filter(CancelRequest.idempotency_key == "race-key").all()
    )
    verify_db.close()

    assert updated_contract.status == ContractStatus.CANCELLED
    assert float(updated_contract.refundable_amount) == 0.0
    assert len(cancel_rows) == 1


def test_concurrent_cancel_race_same_key_postgres_real_locking():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run the PostgreSQL real-locking concurrency test")

    engine = create_engine(database_url)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    test_idempotency_key = f"race-key-pg-{uuid4()}"
    contract_id = None

    try:
        seed_db = SessionLocal()
        contract = Contract(amount=1000, refundable_amount=1000)
        seed_db.add(contract)
        seed_db.commit()
        seed_db.refresh(contract)
        contract_id = contract.id
        seed_db.close()

        barrier = Barrier(2)
        results = []
        errors = []

        def worker():
            db = SessionLocal()
            service = ContractService(db)
            try:
                barrier.wait()
                result = service.cancel_contract(contract_id, test_idempotency_key)
                results.append(result)
            except Exception as exc:
                errors.append(exc)
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker) for _ in range(2)]
            for future in futures:
                future.result()

        assert not errors
        assert len(results) == 2

        verify_db = SessionLocal()
        updated_contract = verify_db.query(Contract).filter(Contract.id == contract_id).first()
        cancel_rows = (
            verify_db.query(CancelRequest)
            .filter(CancelRequest.idempotency_key == test_idempotency_key)
            .all()
        )
        verify_db.close()

        assert updated_contract.status == ContractStatus.CANCELLED
        assert float(updated_contract.refundable_amount) == 0.0
        assert len(cancel_rows) == 1
    finally:
        cleanup_db = SessionLocal()
        try:
            # Cleanup seletivo: remove apenas dados criados por este teste.
            cleanup_db.query(CancelRequest).filter(
                CancelRequest.idempotency_key == test_idempotency_key
            ).delete()
            if contract_id is not None:
                cleanup_db.query(Contract).filter(Contract.id == contract_id).delete()
            cleanup_db.commit()
        finally:
            cleanup_db.close()
        engine.dispose()


def test_root_endpoint(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Contract API"
    assert "health" in data
    assert "docs" in data


def test_correlation_id_forwarded_in_response(client: TestClient):
    cid = "my-trace-abc-123"
    response = client.get("/health", headers={"X-Correlation-ID": cid})
    assert response.status_code == 200
    assert response.headers["x-correlation-id"] == cid


def test_correlation_id_generated_when_missing(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert "x-correlation-id" in response.headers


def test_create_contract_with_fractional_amount(client: TestClient):
    response = client.post(
        "/contracts",
        json={"amount": "1200.50", "refundable_amount": "35.25"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "." in data["refundable_amount"]


def test_invalid_request_body_returns_422(client: TestClient):
    response = client.post("/contracts", json={})
    assert response.status_code == 422
    assert "detail" in response.json()


def test_unhandled_exception_returns_500(db_session_factory):
    # o TestClient precisa de raise_server_exceptions=False para receber o JSON 500
    # em vez de propagar a excessão para o runner de testes
    def override_get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            with patch("app.services.contract_service.ContractService.create_contract") as mock:
                mock.side_effect = RuntimeError("unexpected boom")
                response = c.post(
                    "/contracts",
                    json={"amount": "100", "refundable_amount": "50"},
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


def test_cancel_contract_not_found(client: TestClient):
    response = client.post(
        "/contracts/00000000-0000-0000-0000-000000000000/cancel",
        headers={"Idempotency-Key": "key-not-found"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Contract not found"


def test_reprocess_contract_not_found(client: TestClient):
    response = client.post("/contracts/00000000-0000-0000-0000-000000000000/reprocess")
    assert response.status_code == 404
    assert response.json()["detail"] == "Contract not found"


def test_reprocess_contract_not_stuck_long_enough(client: TestClient, db_session_factory):
    contract = create_contract(client)

    db = db_session_factory()
    db_contract = db.query(Contract).filter(Contract.id == UUID(contract["id"])).first()
    db_contract.status = ContractStatus.PROCESSING
    # _now() retorna UTC sem timezone; mantemos a mesma convenção para a comparação ser correta
    db_contract.updated_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=2)
    db.commit()
    db.close()

    response = client.post(f"/contracts/{contract['id']}/reprocess")
    assert response.status_code == 422
    assert response.json()["detail"] == "Contract is not stuck long enough to reprocess"


def test_cancel_generic_exception_returns_500(db_session_factory):
    def override_get_db():
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/contracts", json={"amount": "100", "refundable_amount": "50"})
            assert resp.status_code == 200
            contract_id = resp.json()["id"]

            with patch(
                "app.services.contract_service.ContractService.cancel_contract",
                side_effect=RuntimeError("db exploded"),
            ):
                response = c.post(
                    f"/contracts/{contract_id}/cancel",
                    headers={"Idempotency-Key": "generic-exc-key"},
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"


def test_mark_request_as_failed_updates_existing_record(db_session_factory):
    from app.models import CancelRequestStatus
    from app.services.contract_service import ContractService

    db = db_session_factory()
    contract = Contract(amount=500, refundable_amount=100)
    db.add(contract)
    db.commit()
    db.refresh(contract)

    # Pre-create a PROCESSING cancel_request already committed
    cr = CancelRequest(
        contract_id=contract.id,
        idempotency_key="mark-fail-key",
        status=CancelRequestStatus.PROCESSING,
    )
    db.add(cr)
    db.commit()

    service = ContractService(db)
    service._mark_request_as_failed(contract.id, "mark-fail-key", "some error detail")

    db.refresh(cr)
    assert cr.status == CancelRequestStatus.FAILED
    assert cr.result == "some error detail"
    db.close()


def test_configure_logging_adds_handler_idempotent():
    from app.core.logging import configure_logging

    root = logging.getLogger()
    original_handlers = root.handlers[:]
    try:
        root.handlers.clear()
        configure_logging()
        count_after_first = len(root.handlers)
        configure_logging()  # second call should be no-op
        count_after_second = len(root.handlers)
        assert count_after_first == 1
        assert count_after_second == 1
    finally:
        root.handlers = original_handlers


def test_correlation_id_filter_injects_correlation_id_into_log_record():
    from app.core.logging import CorrelationIdFilter, reset_correlation_id, set_correlation_id

    token = set_correlation_id("cid-test-123")
    try:
        record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "msg", (), None)
        correlation_filter = CorrelationIdFilter()
        assert correlation_filter.filter(record) is True
        assert record.correlation_id == "cid-test-123"
    finally:
        reset_correlation_id(token)
