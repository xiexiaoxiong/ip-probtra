from __future__ import annotations

import datetime as dt
import json
import threading
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from andun_search.config import get_settings
from andun_search.models import AndunProduct, ApiCallMetric


_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            database_url = get_settings().database_url
            if not database_url:
                raise ValueError("PGDATABASE_URL is not set")
            _engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=1800)
        return _engine


def ensure_tables() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS andun_search_runs (
          id SERIAL PRIMARY KEY,
          patent_record_id INTEGER NOT NULL DEFAULT 0,
          analysis_session_id TEXT,
          product_dataset_id TEXT NOT NULL,
          andun_task_id TEXT,
          status TEXT NOT NULL DEFAULT 'queued',
          remote_status INTEGER,
          keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
          platforms JSONB NOT NULL DEFAULT '[]'::jsonb,
          product_count INTEGER NOT NULL DEFAULT 0,
          submit_elapsed_ms INTEGER NOT NULL DEFAULT 0,
          total_elapsed_ms INTEGER NOT NULL DEFAULT 0,
          error_message TEXT,
          raw_payload JSONB,
          started_at TIMESTAMPTZ,
          finished_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS andun_search_products (
          id SERIAL PRIMARY KEY,
          run_id INTEGER NOT NULL REFERENCES andun_search_runs(id) ON DELETE CASCADE,
          patent_record_id INTEGER NOT NULL DEFAULT 0,
          analysis_session_id TEXT,
          andun_task_id TEXT,
          trade_no TEXT,
          platform TEXT,
          platform_name TEXT,
          product_front_page TEXT,
          title TEXT,
          price TEXT,
          monthly_sales BIGINT NOT NULL DEFAULT 0,
          product_url TEXT,
          store_name TEXT,
          store_url TEXT,
          shopkeeper_id TEXT,
          source_create_time TEXT,
          raw_payload JSONB,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS andun_api_call_logs (
          id SERIAL PRIMARY KEY,
          run_id INTEGER NOT NULL REFERENCES andun_search_runs(id) ON DELETE CASCADE,
          method TEXT NOT NULL,
          http_status INTEGER NOT NULL DEFAULT 0,
          response_code INTEGER NOT NULL DEFAULT 0,
          response_message TEXT,
          elapsed_ms INTEGER NOT NULL DEFAULT 0,
          attempt_count INTEGER NOT NULL DEFAULT 1,
          retry_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
          request_body JSONB,
          response_summary JSONB,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_andun_runs_session ON andun_search_runs(patent_record_id, analysis_session_id)",
        "CREATE INDEX IF NOT EXISTS idx_andun_products_run ON andun_search_products(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_andun_calls_run ON andun_api_call_logs(run_id)",
        "ALTER TABLE andun_api_call_logs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE andun_api_call_logs ADD COLUMN IF NOT EXISTS retry_errors JSONB NOT NULL DEFAULT '[]'::jsonb",
    ]
    with get_engine().begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _dedupe_keywords(values: list[str], limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = " ".join(str(value or "").split())
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        result.append(keyword)
        if len(result) >= limit:
            break
    return result


def fetch_keywords(
    patent_record_id: int,
    analysis_session_id: str,
    input_keywords: list[str] | None,
    max_keywords: int,
) -> list[str]:
    if input_keywords is not None:
        return _dedupe_keywords(input_keywords, max_keywords)
    if patent_record_id <= 0:
        return []

    sql = """
      SELECT keyword_text, source_location, raw_payload
      FROM keyword_records
      WHERE patent_record_id = :patent_record_id
        AND (:analysis_session_id = '' OR analysis_session_id = :analysis_session_id)
        AND keyword_run_id = (
          SELECT MAX(keyword_run_id)
          FROM keyword_records
          WHERE patent_record_id = :patent_record_id
            AND (:analysis_session_id = '' OR analysis_session_id = :analysis_session_id)
        )
      ORDER BY id ASC
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(sql),
            {
                "patent_record_id": patent_record_id,
                "analysis_session_id": analysis_session_id or "",
            },
        ).mappings().all()

    object_terms: list[str] = []
    for row in rows:
        raw_payload = row.get("raw_payload") if isinstance(row.get("raw_payload"), dict) else {}
        terms = raw_payload.get("object_terms", [])
        if isinstance(terms, list):
            object_terms.extend(str(term).replace(" ", "") for term in terms if str(term).strip())

    selected: list[str] = []
    for row in rows:
        keyword = str(row.get("keyword_text") or "").strip()
        if not keyword or str(row.get("source_location") or "") == "必要特征基础词":
            continue
        raw_payload = row.get("raw_payload") if isinstance(row.get("raw_payload"), dict) else {}
        if str(raw_payload.get("query_role", "executable_search")) != "executable_search":
            continue
        if str(raw_payload.get("guard_status", "passed")) != "passed":
            continue
        normalized = keyword.replace(" ", "")
        if object_terms and not any(term in normalized for term in object_terms):
            continue
        selected.append(keyword)
    return _dedupe_keywords(selected, max_keywords)


def create_run(
    *,
    patent_record_id: int,
    analysis_session_id: str,
    product_dataset_id: str,
    keywords: list[str],
    platforms: list[str],
    raw_payload: dict[str, Any],
) -> int:
    ensure_tables()
    sql = """
      INSERT INTO andun_search_runs (
        patent_record_id, analysis_session_id, product_dataset_id, status,
        keywords, platforms, raw_payload, started_at
      ) VALUES (
        :patent_record_id, :analysis_session_id, :product_dataset_id, 'queued',
        CAST(:keywords AS JSONB), CAST(:platforms AS JSONB), CAST(:raw_payload AS JSONB), :started_at
      ) RETURNING id
    """
    with get_engine().begin() as conn:
        return int(
            conn.execute(
                text(sql),
                {
                    "patent_record_id": patent_record_id,
                    "analysis_session_id": analysis_session_id or None,
                    "product_dataset_id": product_dataset_id,
                    "keywords": json.dumps(keywords, ensure_ascii=False),
                    "platforms": json.dumps(platforms, ensure_ascii=False),
                    "raw_payload": json.dumps(raw_payload, ensure_ascii=False),
                    "started_at": dt.datetime.now(dt.timezone.utc),
                },
            ).scalar_one()
        )


def update_run(run_id: int, **values: Any) -> None:
    allowed = {
        "andun_task_id",
        "status",
        "remote_status",
        "product_count",
        "submit_elapsed_ms",
        "total_elapsed_ms",
        "error_message",
        "raw_payload",
        "finished_at",
    }
    assignments: list[str] = []
    params: dict[str, Any] = {"run_id": run_id}
    for key, value in values.items():
        if key not in allowed:
            continue
        if key == "raw_payload":
            assignments.append(f"{key} = CAST(:{key} AS JSONB)")
            params[key] = json.dumps(value or {}, ensure_ascii=False)
        else:
            assignments.append(f"{key} = :{key}")
            params[key] = value
    if not assignments:
        return
    assignments.append("updated_at = now()")
    with get_engine().begin() as conn:
        conn.execute(
            text(f"UPDATE andun_search_runs SET {', '.join(assignments)} WHERE id = :run_id"),
            params,
        )


def insert_api_call(
    *,
    run_id: int,
    metric: ApiCallMetric,
    request_body: dict[str, Any],
    response_summary: dict[str, Any],
) -> None:
    sql = """
      INSERT INTO andun_api_call_logs (
        run_id, method, http_status, response_code, response_message,
        elapsed_ms, attempt_count, retry_errors, request_body, response_summary
      ) VALUES (
        :run_id, :method, :http_status, :response_code, :response_message,
        :elapsed_ms, :attempt_count, CAST(:retry_errors AS JSONB),
        CAST(:request_body AS JSONB), CAST(:response_summary AS JSONB)
      )
    """
    with get_engine().begin() as conn:
        conn.execute(
            text(sql),
            {
                "run_id": run_id,
                "method": metric.method,
                "http_status": metric.http_status,
                "response_code": metric.code,
                "response_message": metric.message or None,
                "elapsed_ms": metric.elapsed_ms,
                "attempt_count": metric.attempt_count,
                "retry_errors": json.dumps(metric.retry_errors, ensure_ascii=False),
                "request_body": json.dumps(request_body, ensure_ascii=False),
                "response_summary": json.dumps(response_summary, ensure_ascii=False),
            },
        )


def replace_products(
    *,
    run_id: int,
    patent_record_id: int,
    analysis_session_id: str,
    products: list[AndunProduct],
) -> None:
    insert_sql = text(
        """
        INSERT INTO andun_search_products (
          run_id, patent_record_id, analysis_session_id, andun_task_id, trade_no,
          platform, platform_name, product_front_page, title, price, monthly_sales,
          product_url, store_name, store_url, shopkeeper_id, source_create_time, raw_payload
        ) VALUES (
          :run_id, :patent_record_id, :analysis_session_id, :andun_task_id, :trade_no,
          :platform, :platform_name, :product_front_page, :title, :price, :monthly_sales,
          :product_url, :store_name, :store_url, :shopkeeper_id, :source_create_time,
          CAST(:raw_payload AS JSONB)
        )
        """
    )
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM andun_search_products WHERE run_id = :run_id"), {"run_id": run_id})
        for product in products:
            conn.execute(
                insert_sql,
                {
                    "run_id": run_id,
                    "patent_record_id": patent_record_id,
                    "analysis_session_id": analysis_session_id or None,
                    "andun_task_id": product.task_id or None,
                    "trade_no": product.trade_no or None,
                    "platform": product.platform or None,
                    "platform_name": product.platform_name or None,
                    "product_front_page": product.product_front_page or None,
                    "title": product.title or None,
                    "price": product.price or None,
                    "monthly_sales": product.monthly_sales,
                    "product_url": product.product_url or None,
                    "store_name": product.store_name or None,
                    "store_url": product.store_url or None,
                    "shopkeeper_id": product.shopkeeper_id or None,
                    "source_create_time": product.create_time or None,
                    "raw_payload": json.dumps(product.raw_payload, ensure_ascii=False),
                },
            )


def get_run_detail(run_id: int) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        run = conn.execute(
            text("SELECT * FROM andun_search_runs WHERE id = :run_id"),
            {"run_id": run_id},
        ).mappings().one_or_none()
        if run is None:
            return None
        products = conn.execute(
            text("SELECT * FROM andun_search_products WHERE run_id = :run_id ORDER BY id ASC"),
            {"run_id": run_id},
        ).mappings().all()
        calls = conn.execute(
            text("SELECT * FROM andun_api_call_logs WHERE run_id = :run_id ORDER BY id ASC"),
            {"run_id": run_id},
        ).mappings().all()
    result = dict(run)
    started_at = result.get("started_at")
    if isinstance(started_at, dt.datetime):
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=dt.timezone.utc)
        result["wall_elapsed_ms"] = max(
            0,
            round((dt.datetime.now(dt.timezone.utc) - started_at).total_seconds() * 1000),
        )
    else:
        result["wall_elapsed_ms"] = int(result.get("total_elapsed_ms") or 0)
    result["products"] = [dict(row) for row in products]
    result["api_calls"] = [dict(row) for row in calls]
    return result


def list_resumable_runs() -> list[dict[str, Any]]:
    """返回服务重启时仍有远端 taskId 的未终态任务。"""
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT *
                FROM andun_search_runs
                WHERE status IN ('submitting', 'collecting', 'organizing')
                  AND andun_task_id IS NOT NULL
                  AND andun_task_id <> ''
                ORDER BY id ASC
                """
            )
        ).mappings().all()
    return [dict(row) for row in rows]
