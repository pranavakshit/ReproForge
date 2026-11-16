"""Structured logging configuration for ReproForge."""

import logging
import sys
from rich.logging import RichHandler


def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance with rich formatting."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RichHandler(
            rich_tracebacks=True,
            markup=True,
            show_path=False,
            show_time=True,
        )
        formatter = logging.Formatter("%(message)s", datefmt="[%X]")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
