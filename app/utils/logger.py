"""Configure application logging with console output and RotatingFileHandler."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: str = "/var/log/anonbot", log_level: str = "INFO") -> logging.Logger:
    level = getattr(logging, log_level.upper(), logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    try:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=log_path / "anonbot.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError:
        # Fallback if running locally without write access to /var/log/anonbot
        fallback_path = Path("./logs")
        fallback_path.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            filename=fallback_path / "anonbot.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    return logging.getLogger("anonbot")
