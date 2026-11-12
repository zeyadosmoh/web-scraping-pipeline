"""Settings loaded from environment variables (.env). No secrets have defaults here."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_USER_AGENT = "P6-portfolio-scraper/0.1 (+educational project; practice sites only)"


@dataclass(frozen=True)
class Settings:
    user_agent: str = DEFAULT_USER_AGENT
    delay_seconds: float = 1.0
    timeout_seconds: float = 15.0
    max_retries: int = 3
    backoff_seconds: float = 1.0
    output_dir: Path = Path("outputs")
    report_timezone: str = "Africa/Cairo"
    pg_host: str = "localhost"
    pg_port: int = 5435
    pg_db: str = "scraper"
    pg_user: str = "scraper"
    pg_password: str | None = None


def load_settings(env_file: str | os.PathLike | None = ".env") -> Settings:
    if env_file:
        load_dotenv(env_file, override=False)
    e = os.environ.get
    return Settings(
        user_agent=e("SCRAPER_USER_AGENT", DEFAULT_USER_AGENT),
        delay_seconds=float(e("SCRAPER_DELAY_SECONDS", "1.0")),
        timeout_seconds=float(e("SCRAPER_TIMEOUT_SECONDS", "15")),
        max_retries=int(e("SCRAPER_MAX_RETRIES", "3")),
        backoff_seconds=float(e("SCRAPER_BACKOFF_SECONDS", "1.0")),
        output_dir=Path(e("OUTPUT_DIR", "outputs")),
        report_timezone=e("REPORT_TIMEZONE", "Africa/Cairo"),
        pg_host=e("POSTGRES_HOST", "localhost"),
        pg_port=int(e("POSTGRES_PORT", "5435")),
        pg_db=e("POSTGRES_DB", "scraper"),
        pg_user=e("POSTGRES_USER", "scraper"),
        pg_password=e("POSTGRES_PASSWORD"),
    )
