"""Structured logging setup for codegraph."""

from __future__ import annotations

import logging
import sys
from typing import Literal

_CONFIGURED = False


def setup_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
    *,
    json_format: bool = False,
) -> logging.Logger:
    """Configure the ``codegraph`` logger hierarchy.

    Parameters
    ----------
    level:
        Root log level for the ``codegraph`` namespace.
    json_format:
        When *True*, emit structured JSON lines instead of human-readable text.

    Returns
    -------
    logging.Logger
        The root ``codegraph`` logger.
    """
    global _CONFIGURED
    root_logger = logging.getLogger("codegraph")

    if _CONFIGURED:
        root_logger.setLevel(getattr(logging, level))
        return root_logger

    root_logger.setLevel(getattr(logging, level))

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.DEBUG)

    if json_format:
        formatter = _JsonFormatter()
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Prevent propagation to the root logger to avoid duplicate output
    root_logger.propagate = False
    _CONFIGURED = True
    return root_logger


class _JsonFormatter(logging.Formatter):
    """Minimal JSON-lines formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        import json
        import time

        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
