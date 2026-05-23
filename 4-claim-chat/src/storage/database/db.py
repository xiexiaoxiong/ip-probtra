import os
import time
import threading
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
import logging
logger = logging.getLogger(__name__)

MAX_RETRY_TIME = 60
_engine_lock = threading.Lock()

try:
    from dotenv import load_dotenv
    env_candidates = [
        Path(__file__).resolve().parents[3] / "IP-protral" / ".env.local",
        Path(__file__).resolve().parents[2] / ".env.local",
        Path.cwd() / ".env.local",
    ]
    for env_path in env_candidates:
        if env_path.exists():
            load_dotenv(env_path, override=False)
except Exception:
    pass

def get_db_url() -> str:
    url = os.getenv("PGDATABASE_URL") or ""
    if url:
        return url
    logger.error("PGDATABASE_URL is not set")
    raise ValueError("PGDATABASE_URL is not set")

_engine = None
_SessionLocal = None

def _create_engine_with_retry():
    url = get_db_url()
    if url is None or url == "":
        logger.error("PGDATABASE_URL is not set")
        raise ValueError("PGDATABASE_URL is not set")
    size = 100
    overflow = 100
    recycle = 1800
    timeout = 30
    engine = create_engine(
        url,
        pool_size=size,
        max_overflow=overflow,
        pool_pre_ping=True,
        pool_recycle=recycle,
        pool_timeout=timeout,
    )
    start_time = time.time()
    last_error = None
    while time.time() - start_time < MAX_RETRY_TIME:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except OperationalError as e:
            last_error = e
            elapsed = time.time() - start_time
            logger.warning(f"Database connection failed, retrying... (elapsed: {elapsed:.1f}s)")
            time.sleep(max(0, min(1, MAX_RETRY_TIME - elapsed)))
    logger.error(f"Database connection failed after {MAX_RETRY_TIME}s: {last_error}")
    raise last_error  # pyright: ignore [reportGeneralTypeIssues]

def get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = _create_engine_with_retry()
        else:
            try:
                with _engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            except Exception:
                logger.warning("Database engine connection check failed, recreating engine...")
                try:
                    _engine.dispose()
                except Exception:
                    pass
                _engine = _create_engine_with_retry()
    return _engine

def get_sessionmaker():
    global _SessionLocal
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal

def get_session():
    return get_sessionmaker()()

__all__ = [
    "get_db_url",
    "get_engine",
    "get_sessionmaker",
    "get_session",
]
