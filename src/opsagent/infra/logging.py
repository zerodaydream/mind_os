"""Structured JSON logging with correlation IDs and secret redaction (ARCHITECTURE section 15)."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from opsagent.domain import current, redact

_LOG_FILE = "opsagent.log"


def _merge_correlation(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    for key, value in current().items():
        event_dict.setdefault(key, value)
    return event_dict


def _redact(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    redacted: EventDict = redact(event_dict)
    return redacted


def configure_logging(log_dir: Path, level: str = "INFO", *, to_stderr: bool = False) -> Path:
    """Send JSON logs to a rotating file under `log_dir`. Returns the log file path.

    The terminal UI owns stdout and stderr, so stderr output is opt-in (`ops doctor`, tests).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / _LOG_FILE

    shared: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _merge_correlation,
        structlog.processors.format_exc_info,
        _redact,
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared,
    )
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    ]
    if to_stderr:
        handlers.append(logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(formatter)

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
        old.close()
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
    return log_file


def get_logger(name: str) -> Any:
    return structlog.stdlib.get_logger(name)
