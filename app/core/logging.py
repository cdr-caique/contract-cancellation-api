import logging
from contextvars import ContextVar, Token

_correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Injeta o correlation id atual em cada linha de log.
        record.correlation_id = _correlation_id_ctx.get()
        return True


def set_correlation_id(correlation_id: str) -> Token:
    return _correlation_id_ctx.set(correlation_id)


def reset_correlation_id(token: Token) -> None:
    _correlation_id_ctx.reset(token)


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.addFilter(CorrelationIdFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s correlation_id=%(correlation_id)s %(name)s %(message)s")
    )

    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
