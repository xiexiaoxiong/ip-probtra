from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


QA_BASE_URL = "http://qa-open.andunip.cn/open/api"
PRODUCTION_BASE_URL = "https://openapi.andunip.com/open/api"


def bootstrap_local_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        return

    candidates = [
        Path(__file__).resolve().parents[3] / "IP-protral" / ".env.local",
        Path(__file__).resolve().parents[2] / ".env.local",
        Path.cwd() / ".env.local",
    ]
    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)

    if not os.getenv("PGDATABASE_URL") and os.getenv("DATABASE_URL"):
        os.environ["PGDATABASE_URL"] = os.getenv("DATABASE_URL", "")


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except ValueError:
        value = default
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_env: str
    api_base_url: str
    app_key: str
    app_secret: str
    request_timeout_seconds: int
    request_retry_attempts: int
    poll_interval_seconds: int
    max_wait_seconds: int

    @property
    def credentials_configured(self) -> bool:
        return bool(self.app_key and self.app_secret)


def get_settings() -> Settings:
    bootstrap_local_env()
    api_env = (os.getenv("ANDUN_API_ENV") or "qa").strip().lower()
    default_url = PRODUCTION_BASE_URL if api_env == "production" else QA_BASE_URL
    return Settings(
        database_url=os.getenv("PGDATABASE_URL") or os.getenv("DATABASE_URL") or "",
        api_env=api_env,
        api_base_url=(os.getenv("ANDUN_API_BASE_URL") or default_url).strip(),
        app_key=(os.getenv("ANDUN_APP_KEY") or "").strip(),
        app_secret=(os.getenv("ANDUN_APP_SECRET") or "").strip(),
        request_timeout_seconds=_int_env("ANDUN_REQUEST_TIMEOUT_SECONDS", 30, 5, 120),
        request_retry_attempts=_int_env("ANDUN_REQUEST_RETRY_ATTEMPTS", 4, 1, 6),
        poll_interval_seconds=_int_env("ANDUN_POLL_INTERVAL_SECONDS", 10, 2, 120),
        max_wait_seconds=_int_env("ANDUN_MAX_WAIT_SECONDS", 1800, 30, 14400),
    )
