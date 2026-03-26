"""
Настройка логирования для всего приложения.
Использует structlog-style форматирование через стандартный logging.
"""

import logging
import sys
from bot.config import settings


def setup_logging() -> None:
    """Вызвать один раз при старте (в main.py / webhook.py)."""

    fmt = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=fmt,
        datefmt=date_fmt,
        handlers=handlers,
        force=True,
    )

    # Заглушаем шумные библиотеки
    for noisy in ("httpx", "httpcore", "openai._base_client",
                  "aiogram.event", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("bot").setLevel(
        logging.DEBUG if settings.debug else logging.INFO
    )
