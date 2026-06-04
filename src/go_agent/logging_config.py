"""Logging setup for a single agent run."""

from __future__ import annotations

import logging
import sys
from go_agent.run_context import RunContext

_LOG_FORMAT = "%(asctime)s %(levelname)s [run_id=%(run_id)s] %(name)s: %(message)s"
_LOGGER_NAME = "go_agent"


class _RunIdAdapter(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("run_id", self.extra["run_id"])
        return msg, kwargs


def configure_run_logging(context: RunContext) -> _RunIdAdapter:
    """Configure console and file logging for this run; return run-scoped logger."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(context.settings.logging_level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.setLevel(context.settings.logging_level)
    logger.addHandler(console)

    context.log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(context.log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(context.settings.logging_level)
    logger.addHandler(file_handler)

    return _RunIdAdapter(logger, {"run_id": context.run_id})
