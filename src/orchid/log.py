import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path.home() / ".orchid" / "logs"


def setup_logging(console: bool = True) -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("orchid")
    logger.setLevel(logging.DEBUG)

    # Файловый хэндлер — всё пишем в файл с ротацией
    file_handler = RotatingFileHandler(
        LOG_DIR / "orchid.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(file_handler)

    # Консольный хэндлер — только для CLI, не для TUI
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(console_handler)

    return logger


def get_job_log_path(job_id: str) -> Path:
    job_log_dir = LOG_DIR / "jobs"
    job_log_dir.mkdir(parents=True, exist_ok=True)
    return job_log_dir / f"{job_id}.log"
