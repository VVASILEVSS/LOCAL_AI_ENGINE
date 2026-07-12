"""
LOCAL_AI_ENGINE — centralized logging configuration.

Features:
- Rotating file handler (10 MB × 5 files) → logs/bot.log
- Console handler with colored level
- Structured format: timestamp | level | module:func:line | message
- Third-party noise suppression (httpx, matplotlib, apscheduler)
- Environment override via LOG_LEVEL (default: INFO)
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Constants ──────────────────────────────────────────────
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "bot.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5             # keep 5 rotated files

LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
)
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Levels for noisy third-party libs
NOISY_LIBS = {
    "httpx": logging.WARNING,
    "matplotlib": logging.WARNING,
    "apscheduler": logging.WARNING,
    "PIL": logging.WARNING,
    "asyncio": logging.WARNING,
    "aiogram.event": logging.WARNING,
}

_initialized = False


def setup_logging(level: str | int | None = None) -> logging.Logger:
    """
    Configure root logger with file + console handlers.

    Args:
        level: LOG_LEVEL env var or explicit override.
               Accepts 'DEBUG'/'INFO'/'WARNING'/'ERROR' or int.

    Returns:
        Root logger instance (configured).
    """
    global _initialized
    if _initialized:
        return logging.getLogger()

    # Resolve level
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO").upper()
    if isinstance(level, str):
        level = getattr(logging, level, logging.INFO)

    # Create logs/ dir
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # capture everything; handlers filter
    # Clear any pre-existing handlers (e.g. basicConfig from main.py)
    root.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # ── File handler (rotating) ──
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # file gets everything
    file_handler.setFormatter(formatter)

    # ── Console handler ──
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Suppress noisy libs
    for lib_name, lib_level in NOISY_LIBS.items():
        logging.getLogger(lib_name).setLevel(lib_level)

    # Prevent basicConfig from overriding later
    logging.getLogger().handlers = root.handlers

    _initialized = True
    logger = logging.getLogger("core.logging_setup")
    logger.info(
        "Logging initialized: file=%s (10MB×5), console level=%s",
        LOG_FILE,
        logging.getLevelName(level),
    )
    return root


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a module. Auto-initializes if not yet configured.

    Usage:
        from core.logging_setup import get_logger
        logger = get_logger(__name__)
        logger.info("message")
    """
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)
