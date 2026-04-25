"""Structured JSON logging via structlog. One file, one console renderer.

Goal: every interesting event in the system is one line of structured JSON
in `logs/sentinel.jsonl` plus a colored line on the console. Logs roll daily.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import structlog


def configure(level: str, json_file: Path, console: bool) -> None:
    json_file.parent.mkdir(parents=True, exist_ok=True)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    pre_chain = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    handlers: list[logging.Handler] = []

    file_handler = logging.handlers.TimedRotatingFileHandler(
        json_file, when="midnight", backupCount=14, encoding="utf-8",
    )
    file_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
            foreign_pre_chain=pre_chain,
        )
    )
    handlers.append(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                processor=structlog.dev.ConsoleRenderer(colors=True),
                foreign_pre_chain=pre_chain,
            )
        )
        handlers.append(console_handler)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(level)

    # tame noisy libs — httpx in particular logs full URLs at INFO level,
    # which can leak API keys embedded in query params.
    for noisy in ("urllib3", "yfinance", "peewee", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
