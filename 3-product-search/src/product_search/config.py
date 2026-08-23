from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def bootstrap_local_env() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        load_dotenv = None

    if not load_dotenv:
        return

    env_candidates = [
        Path(__file__).resolve().parents[3] / "IP-protral" / ".env.local",
        Path(__file__).resolve().parents[2] / ".env.local",
        Path.cwd() / ".env.local",
    ]
    for env_path in env_candidates:
        if env_path.exists():
            load_dotenv(env_path, override=False)

    if not os.getenv("PGDATABASE_URL") and os.getenv("DATABASE_URL"):
        os.environ["PGDATABASE_URL"] = os.getenv("DATABASE_URL", "")


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str
    brightdata_api_key: str
    brightdata_serp_zone: str
    brightdata_unlocker_zone: str
    brightdata_endpoint: str
    request_timeout_seconds: int
    max_concurrency: int
    serp_url_limit: int
    allow_direct_fetch_fallback: bool
    render_fallback_enabled: bool
    render_retry_attempts: int
    user_agent: str
    historical_refresh_limit: int = 8
    historical_refresh_concurrency: int = 4
    browser_fallback_enabled: bool = True
    browser_timeout_seconds: int = 60
    browser_stable_rounds: int = 4
    browser_max_scroll_rounds: int = 80
    incomplete_image_threshold: int = 6


def get_settings() -> Settings:
    bootstrap_local_env()
    api_key = os.getenv("BRIGHTDATA_API_KEY", "").strip()
    api_key_file = os.getenv("BRIGHTDATA_API_KEY_FILE", "").strip()
    if not api_key and api_key_file:
        try:
            api_key = Path(api_key_file).expanduser().read_text(encoding="utf-8").strip()
        except Exception:
            api_key = ""
    return Settings(
        database_url=os.getenv("PGDATABASE_URL") or os.getenv("DATABASE_URL") or "",
        brightdata_api_key=api_key,
        brightdata_serp_zone=os.getenv("BRIGHTDATA_SERP_ZONE", "").strip(),
        brightdata_unlocker_zone=os.getenv("BRIGHTDATA_UNLOCKER_ZONE", "").strip(),
        brightdata_endpoint=os.getenv("BRIGHTDATA_REQUEST_ENDPOINT", "https://api.brightdata.com/request").strip(),
        request_timeout_seconds=_int_env("PRODUCT_SEARCH_TIMEOUT_SECONDS", 45),
        max_concurrency=max(1, _int_env("PRODUCT_SEARCH_MAX_CONCURRENCY", 2)),
        serp_url_limit=max(1, _int_env("PRODUCT_SEARCH_SERP_URL_LIMIT", 4)),
        allow_direct_fetch_fallback=_truthy(os.getenv("PRODUCT_SEARCH_ALLOW_DIRECT_FETCH_FALLBACK"), True),
        render_fallback_enabled=_truthy(os.getenv("PRODUCT_SEARCH_BRIGHTDATA_RENDER_FALLBACK"), True),
        render_retry_attempts=max(0, _int_env("PRODUCT_SEARCH_RENDER_RETRY_ATTEMPTS", 4)),
        user_agent=os.getenv(
            "PRODUCT_SEARCH_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        ),
        historical_refresh_limit=min(32, max(1, _int_env("PRODUCT_SEARCH_HISTORICAL_REFRESH_LIMIT", 8))),
        historical_refresh_concurrency=min(8, max(1, _int_env("PRODUCT_SEARCH_HISTORICAL_REFRESH_CONCURRENCY", 4))),
        browser_fallback_enabled=_truthy(os.getenv("PRODUCT_SEARCH_BROWSER_FALLBACK"), True),
        browser_timeout_seconds=min(180, max(15, _int_env("PRODUCT_SEARCH_BROWSER_TIMEOUT_SECONDS", 60))),
        browser_stable_rounds=min(10, max(2, _int_env("PRODUCT_SEARCH_BROWSER_STABLE_ROUNDS", 4))),
        browser_max_scroll_rounds=min(200, max(10, _int_env("PRODUCT_SEARCH_BROWSER_MAX_SCROLL_ROUNDS", 80))),
        incomplete_image_threshold=min(30, max(2, _int_env("PRODUCT_SEARCH_INCOMPLETE_IMAGE_THRESHOLD", 6))),
    )
