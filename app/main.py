import uuid

from fastapi import FastAPI, Request

from app.api.contracts import router as contracts_router
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, reset_correlation_id, set_correlation_id

app = FastAPI(title="Contract API")

configure_logging()
register_exception_handlers(app)

app.include_router(contracts_router)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    token = set_correlation_id(correlation_id)
    try:
        response = await call_next(request)
    finally:
        # Evita vazamento do correlation id entre requests concorrentes.
        reset_correlation_id(token)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "service": "Contract API",
        "health": "/health",
        "docs": "/docs",
    }
