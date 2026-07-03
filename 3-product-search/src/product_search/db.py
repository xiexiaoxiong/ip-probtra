from __future__ import annotations

import datetime as dt
import json
import threading
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from product_search.config import get_settings
from product_search.models import CandidateDiagnostic, ProductResult


_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            settings = get_settings()
            if not settings.database_url:
                raise ValueError("PGDATABASE_URL is not set")
            _engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800)
        return _engine


def ensure_tables() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS product_detail_search_runs (
          id SERIAL PRIMARY KEY,
          patent_record_id INTEGER NOT NULL,
          analysis_session_id TEXT,
          product_dataset_id TEXT,
          status TEXT NOT NULL DEFAULT 'running',
          source_keyword_count INTEGER NOT NULL DEFAULT 0,
          candidate_link_count INTEGER NOT NULL DEFAULT 0,
          accepted_products_count INTEGER NOT NULL DEFAULT 0,
          rejected_candidates_count INTEGER NOT NULL DEFAULT 0,
          platforms_queried JSONB,
          provider JSONB,
          error_message TEXT,
          started_at TIMESTAMPTZ,
          finished_at TIMESTAMPTZ,
          raw_payload JSONB,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS product_detail_search_candidates (
          id SERIAL PRIMARY KEY,
          run_id INTEGER NOT NULL REFERENCES product_detail_search_runs(id) ON DELETE CASCADE,
          patent_record_id INTEGER NOT NULL,
          analysis_session_id TEXT,
          keyword_text TEXT,
          platform TEXT,
          candidate_url TEXT,
          final_url TEXT,
          title TEXT,
          status TEXT NOT NULL,
          rejection_reason TEXT,
          quality_score INTEGER NOT NULL DEFAULT 0,
          raw_payload JSONB,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS product_detail_search_products (
          id SERIAL PRIMARY KEY,
          run_id INTEGER NOT NULL REFERENCES product_detail_search_runs(id) ON DELETE CASCADE,
          patent_record_id INTEGER NOT NULL,
          analysis_session_id TEXT,
          product_id TEXT,
          platform TEXT,
          product_name TEXT,
          product_url TEXT,
          final_url TEXT,
          price TEXT,
          sales TEXT,
          brand TEXT,
          manufacturer TEXT,
          matched_keywords TEXT,
          description TEXT,
          detail_text TEXT,
          picture JSONB,
          source_text_url TEXT,
          source_image_url TEXT,
          quality_score INTEGER NOT NULL DEFAULT 0,
          quality_flags JSONB,
          raw_payload JSONB,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_product_detail_search_runs_patent ON product_detail_search_runs(patent_record_id, analysis_session_id)",
        "CREATE INDEX IF NOT EXISTS idx_product_detail_search_products_run ON product_detail_search_products(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_product_detail_search_candidates_run ON product_detail_search_candidates(run_id)",
    ]
    engine = get_engine()
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def fetch_keywords(
    patent_record_id: int,
    analysis_session_id: str,
    input_keywords: list[str] | None,
    max_keywords: int,
) -> list[str]:
    if input_keywords is not None:
        return _dedupe_keywords(input_keywords, max_keywords)

    sql = """
      SELECT keyword_text, keyword_type, confidence_score, raw_payload
      FROM keyword_records
      WHERE patent_record_id = :patent_record_id
        AND (:analysis_session_id = '' OR analysis_session_id = :analysis_session_id)
      ORDER BY id ASC
    """
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(sql),
            {"patent_record_id": patent_record_id, "analysis_session_id": analysis_session_id or ""},
        ).mappings().all()

    weighted: list[tuple[int, float, int, int, str]] = []
    for index, row in enumerate(rows):
        keyword = str(row.get("keyword_text") or "").strip()
        if not keyword:
            continue
        keyword_type = str(row.get("keyword_type") or "").upper()
        confidence = float(row.get("confidence_score") or 0.0)
        effective_len = len(keyword.replace(" ", ""))
        priority = 60
        if "OBJECT_BASE" in keyword_type:
            priority = 4
        elif "REQUIRED" in keyword_type and effective_len >= 5:
            priority = 5
        elif "COMBINED" in keyword_type:
            priority = 15
        elif "INVENTION" in keyword_type:
            priority = 20
        elif "REQUIRED" in keyword_type:
            priority = 70
        elif "HOLDER" in keyword_type:
            priority = 75
        elif "SCENARIO" in keyword_type or "AUDIENCE" in keyword_type:
            priority = 80
        weighted.append((priority, -confidence, -effective_len, index, keyword))

    weighted.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return _dedupe_keywords([item[4] for item in weighted], max_keywords)


def _dedupe_keywords(keywords: list[str], max_keywords: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        cleaned = " ".join(str(keyword or "").split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= max_keywords:
            break
    return result


def create_run(
    *,
    patent_record_id: int,
    analysis_session_id: str,
    product_dataset_id: str,
    keywords: list[str],
    platforms: list[str],
    provider: dict[str, Any],
) -> int:
    ensure_tables()
    sql = """
      INSERT INTO product_detail_search_runs (
        patent_record_id, analysis_session_id, product_dataset_id, status,
        source_keyword_count, platforms_queried, provider, started_at, raw_payload
      )
      VALUES (
        :patent_record_id, :analysis_session_id, :product_dataset_id, 'running',
        :source_keyword_count, CAST(:platforms_queried AS JSONB), CAST(:provider AS JSONB), :started_at, CAST(:raw_payload AS JSONB)
      )
      RETURNING id
    """
    params = {
        "patent_record_id": patent_record_id,
        "analysis_session_id": analysis_session_id or None,
        "product_dataset_id": product_dataset_id,
        "source_keyword_count": len(keywords),
        "platforms_queried": json.dumps(platforms, ensure_ascii=False),
        "provider": json.dumps(provider, ensure_ascii=False),
        "started_at": dt.datetime.now(dt.timezone.utc),
        "raw_payload": json.dumps({"keywords": keywords}, ensure_ascii=False),
    }
    with get_engine().begin() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def insert_candidate(
    *,
    run_id: int,
    patent_record_id: int,
    analysis_session_id: str,
    diagnostic: CandidateDiagnostic,
) -> None:
    sql = """
      INSERT INTO product_detail_search_candidates (
        run_id, patent_record_id, analysis_session_id, keyword_text, platform,
        candidate_url, final_url, title, status, rejection_reason, quality_score, raw_payload
      )
      VALUES (
        :run_id, :patent_record_id, :analysis_session_id, :keyword_text, :platform,
        :candidate_url, :final_url, :title, :status, :rejection_reason, :quality_score, CAST(:raw_payload AS JSONB)
      )
    """
    params = {
        "run_id": run_id,
        "patent_record_id": patent_record_id,
        "analysis_session_id": analysis_session_id or None,
        "keyword_text": diagnostic.keyword,
        "platform": diagnostic.platform,
        "candidate_url": diagnostic.candidate_url,
        "final_url": diagnostic.final_url or None,
        "title": diagnostic.title or None,
        "status": diagnostic.status,
        "rejection_reason": diagnostic.rejection_reason or None,
        "quality_score": diagnostic.quality_score,
        "raw_payload": json.dumps(diagnostic.raw_payload, ensure_ascii=False),
    }
    with get_engine().begin() as conn:
        conn.execute(text(sql), params)


def insert_product(
    *,
    run_id: int,
    patent_record_id: int,
    analysis_session_id: str,
    product: ProductResult,
) -> None:
    sql = """
      INSERT INTO product_detail_search_products (
        run_id, patent_record_id, analysis_session_id, product_id, platform, product_name,
        product_url, final_url, price, sales, brand, manufacturer, matched_keywords,
        description, detail_text, picture, source_text_url, source_image_url,
        quality_score, quality_flags, raw_payload
      )
      VALUES (
        :run_id, :patent_record_id, :analysis_session_id, :product_id, :platform, :product_name,
        :product_url, :final_url, :price, :sales, :brand, :manufacturer, :matched_keywords,
        :description, :detail_text, CAST(:picture AS JSONB), :source_text_url, :source_image_url,
        :quality_score, CAST(:quality_flags AS JSONB), CAST(:raw_payload AS JSONB)
      )
    """
    flags = dict(product.quality_flags)
    source_text_url = str(flags.get("source_text_url") or product.final_url)
    source_image_url = str(flags.get("source_image_url") or product.final_url)
    params = {
        "run_id": run_id,
        "patent_record_id": patent_record_id,
        "analysis_session_id": analysis_session_id or None,
        "product_id": product.product_id or None,
        "platform": product.platform or None,
        "product_name": product.product_name or None,
        "product_url": product.product_url or None,
        "final_url": product.final_url or None,
        "price": product.price or None,
        "sales": product.sales or None,
        "brand": product.brand or None,
        "manufacturer": product.manufacturer or None,
        "matched_keywords": ", ".join(product.matched_keywords) or None,
        "description": product.description or None,
        "detail_text": product.detail_text or None,
        "picture": json.dumps(product.picture, ensure_ascii=False),
        "source_text_url": source_text_url or None,
        "source_image_url": source_image_url or None,
        "quality_score": product.quality_score,
        "quality_flags": json.dumps(product.quality_flags, ensure_ascii=False),
        "raw_payload": json.dumps(product.raw_payload, ensure_ascii=False),
    }
    with get_engine().begin() as conn:
        conn.execute(text(sql), params)


def finish_run(
    *,
    run_id: int,
    status: str,
    candidate_link_count: int,
    accepted_products_count: int,
    rejected_candidates_count: int,
    error_message: str,
) -> None:
    sql = """
      UPDATE product_detail_search_runs
      SET status = :status,
          candidate_link_count = :candidate_link_count,
          accepted_products_count = :accepted_products_count,
          rejected_candidates_count = :rejected_candidates_count,
          error_message = :error_message,
          finished_at = :finished_at,
          updated_at = :finished_at
      WHERE id = :run_id
    """
    with get_engine().begin() as conn:
        conn.execute(
            text(sql),
            {
                "run_id": run_id,
                "status": status,
                "candidate_link_count": candidate_link_count,
                "accepted_products_count": accepted_products_count,
                "rejected_candidates_count": rejected_candidates_count,
                "error_message": error_message or None,
                "finished_at": dt.datetime.now(dt.timezone.utc),
            },
        )


def get_run_detail(run_id: int) -> dict[str, Any] | None:
    run_sql = "SELECT * FROM product_detail_search_runs WHERE id = :run_id"
    products_sql = "SELECT * FROM product_detail_search_products WHERE run_id = :run_id ORDER BY id ASC"
    candidates_sql = "SELECT * FROM product_detail_search_candidates WHERE run_id = :run_id ORDER BY id ASC LIMIT 200"
    with get_engine().connect() as conn:
        run = conn.execute(text(run_sql), {"run_id": run_id}).mappings().first()
        if not run:
            return None
        products = [dict(row) for row in conn.execute(text(products_sql), {"run_id": run_id}).mappings().all()]
        candidates = [dict(row) for row in conn.execute(text(candidates_sql), {"run_id": run_id}).mappings().all()]
    return {"run": dict(run), "products": products, "candidates": candidates}
