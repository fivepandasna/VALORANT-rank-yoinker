"""One rotating log file. Never log tokens, passwords or full headers."""
import logging
import os
from logging.handlers import RotatingFileHandler

from src.constants import LOG_DIR


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("vry")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(LOG_DIR, "vry.log"),
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%Y.%m.%d-%H.%M.%S")
    )
    logger.addHandler(handler)
    return logger
