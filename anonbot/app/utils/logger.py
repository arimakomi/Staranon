"""تنظیم لاگ چرخشی (RotatingFileHandler) و لاگ استاندارد ژورنال بدون ثبت اطلاعات حساس."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: str = "/var/log/anonbot", log_level: str = "INFO") -> logging.Logger:
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if root_logger.handlers:
        root_logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(level)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    target_dir = Path(log_dir)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        log_file = target_dir / "anonbot.log"
        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except OSError:
        # در صورتی که در محیط تست بدون دسترسی به /var/log اجرا شود، از مسیر محلی استفاده می‌کند
        fallback_dir = Path("./logs")
        try:
            fallback_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                filename=str(fallback_dir / "anonbot.log"),
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except OSError:
            pass

    # کاهش لاگ‌های پرحجم کتابخانه‌های جانبی
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    return logging.getLogger("anonbot")
