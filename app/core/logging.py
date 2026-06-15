import sys
from pathlib import Path

from loguru import logger


def setup_logging(log_level: str = "DEBUG") -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )
    Path("logs").mkdir(exist_ok=True)
    logger.add(
        "logs/ntexecg.log",
        level=log_level,
        rotation="10 MB",
        retention="30 days",
        compression="gz",
        encoding="utf-8",
    )
