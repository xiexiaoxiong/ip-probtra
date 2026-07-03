from __future__ import annotations

import argparse
import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from product_search.config import bootstrap_local_env, get_settings
from product_search.db import fetch_keywords, get_engine
from product_search.models import ProductSearchInput
from product_search.service import ProductSearchService


@dataclass(frozen=True)
class ExampleRecord:
    id: int
    task_id: str
    patent_number: str
    title: str


@dataclass(frozen=True)
class KeywordSource:
    patent_record_id: int
    analysis_session_id: str
    keyword_count: int
    reason: str


def normalize_patent_number(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def load_example_records(prefix: str) -> list[ExampleRecord]:
    sql = """
      SELECT id, task_id, patent_number, title
      FROM patent_parse_records
      WHERE task_id LIKE :prefix
      ORDER BY task_id ASC, id ASC
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), {"prefix": f"{prefix}%"}).mappings().all()
    return [
        ExampleRecord(
            id=int(row["id"]),
            task_id=str(row["task_id"] or ""),
            patent_number=str(row["patent_number"] or ""),
            title=str(row["title"] or ""),
        )
        for row in rows
    ]


def find_keyword_source(record: ExampleRecord) -> KeywordSource | None:
    sql = """
      SELECT p.id AS patent_record_id, k.analysis_session_id, count(k.id) AS keyword_count,
             p.patent_number, p.title, max(k.created_at) AS last_kw
      FROM patent_parse_records p
      JOIN keyword_records k ON k.patent_record_id = p.id
      WHERE k.keyword_text IS NOT NULL AND trim(k.keyword_text) <> ''
      GROUP BY p.id, k.analysis_session_id, p.patent_number, p.title
      ORDER BY max(k.created_at) DESC NULLS LAST, p.id DESC
    """
    wanted_number = normalize_patent_number(record.patent_number)
    wanted_title = record.title.strip()
    same_record: KeywordSource | None = None
    same_number: KeywordSource | None = None
    same_title: KeywordSource | None = None
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    for row in rows:
        source = KeywordSource(
            patent_record_id=int(row["patent_record_id"]),
            analysis_session_id=str(row["analysis_session_id"] or ""),
            keyword_count=int(row["keyword_count"] or 0),
            reason="",
        )
        if source.patent_record_id == record.id:
            same_record = KeywordSource(source.patent_record_id, source.analysis_session_id, source.keyword_count, "same_record")
            break
        if wanted_number and normalize_patent_number(str(row["patent_number"] or "")) == wanted_number and same_number is None:
            same_number = KeywordSource(source.patent_record_id, source.analysis_session_id, source.keyword_count, "same_patent_number")
        if wanted_title and str(row["title"] or "").strip() == wanted_title and same_title is None:
            same_title = KeywordSource(source.patent_record_id, source.analysis_session_id, source.keyword_count, "same_title")
    return same_record or same_number or same_title


async def run_one(record: ExampleRecord, source: KeywordSource, args: argparse.Namespace) -> dict[str, Any]:
    if args.single_source_keywords:
        keywords = fetch_keywords(source.patent_record_id, source.analysis_session_id, None, args.max_keywords)
    else:
        keywords = fetch_aggregated_keywords(record, args.max_keywords)
    service = ProductSearchService(get_settings())
    payload = ProductSearchInput(
        patent_record_id=record.id,
        analysis_session_id=f"{record.task_id}_product_detail_test",
        input_keywords=keywords,
        platforms=args.platforms,
        max_keywords=args.max_keywords,
        max_candidates_per_keyword=args.max_candidates_per_keyword,
        max_detail_candidates=args.max_detail_candidates,
        max_products=args.max_products,
        persist=not args.no_persist,
    )
    result = await service.run(payload)
    return {
        "record_id": record.id,
        "patent_number": record.patent_number,
        "title": record.title,
        "keyword_source": source,
        "run_id": result.product_detail_search_run_id,
        "keywords": result.keywords,
        "candidates": result.total_candidate_links_count,
        "accepted": result.accepted_products_count,
        "rejected": result.rejected_candidates_count,
        "products": [product.model_dump() for product in result.products],
    }


def fetch_aggregated_keywords(record: ExampleRecord, max_keywords: int) -> list[str]:
    sql = """
      SELECT k.keyword_text, k.keyword_type, k.confidence_score, k.id
      FROM patent_parse_records p
      JOIN keyword_records k ON k.patent_record_id = p.id
      WHERE k.keyword_text IS NOT NULL AND trim(k.keyword_text) <> ''
        AND (
          regexp_replace(upper(coalesce(p.patent_number,'')), '[^A-Z0-9]', '', 'g') = :patent_number
          OR (:title <> '' AND trim(coalesce(p.title,'')) = :title)
          OR p.id = :record_id
        )
      ORDER BY k.id ASC
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(sql),
            {
                "patent_number": normalize_patent_number(record.patent_number),
                "title": record.title.strip(),
                "record_id": record.id,
            },
        ).mappings().all()

    weighted: list[tuple[int, float, int, int, str]] = []
    for row in rows:
        keyword = str(row["keyword_text"] or "").strip()
        if not keyword:
            continue
        keyword_type = str(row["keyword_type"] or "").upper()
        confidence = float(row["confidence_score"] or 0.0)
        effective_len = len(keyword.replace(" ", ""))
        priority = 60
        if "OBJECT_BASE" in keyword_type:
            priority = 4
        elif "COMBINED" in keyword_type and effective_len >= 5:
            priority = 8
        elif "REQUIRED" in keyword_type and effective_len >= 5:
            priority = 10
        elif "INVENTION" in keyword_type:
            priority = 20
        elif "HOLDER" in keyword_type:
            priority = 75
        weighted.append((priority, -confidence, -effective_len, int(row["id"]), keyword))
    weighted.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    result: list[str] = []
    seen: set[str] = set()
    for *_rest, keyword in weighted:
        if keyword in seen:
            continue
        seen.add(keyword)
        result.append(keyword)
        if len(result) >= max_keywords:
            break
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description="Run product detail search against real example patent records.")
    parser.add_argument("--prefix", default="module1_abstract_examples_1782749163_", help="module1 example task_id prefix")
    parser.add_argument("--limit", type=int, default=0, help="max examples to run; 0 means all")
    parser.add_argument("--record-id", action="append", type=int, default=[], help="only run a specific patent_parse_records.id; can be repeated")
    parser.add_argument("--max-keywords", type=int, default=3)
    parser.add_argument("--max-candidates-per-keyword", type=int, default=4)
    parser.add_argument("--max-detail-candidates", type=int, default=12)
    parser.add_argument("--max-products", type=int, default=5)
    parser.add_argument("--platforms", nargs="+", default=["jd", "1688"])
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--list-only", action="store_true", help="only list keyword sources; do not search products")
    parser.add_argument("--single-source-keywords", action="store_true", help="only use the selected keyword source instead of aggregating same-patent module2 keywords")
    args = parser.parse_args()

    bootstrap_local_env()
    records = load_example_records(args.prefix)
    if args.record_id:
        wanted = set(args.record_id)
        records = [record for record in records if record.id in wanted]
    if args.limit:
        records = records[: args.limit]

    print(f"examples={len(records)}")
    for record in records:
        source = find_keyword_source(record)
        if not source:
            print(f"NO_KEYWORDS | record={record.id} | {record.patent_number} | {record.title}")
            continue
        print(
            f"START | record={record.id} | {record.patent_number} | {record.title} | "
            f"source={source.reason}:{source.patent_record_id}:{source.analysis_session_id}"
        )
        if args.list_only:
            continue
        result = await run_one(record, source, args)
        print(
            f"DONE | record={record.id} | run={result['run_id']} | candidates={result['candidates']} | "
            f"accepted={result['accepted']} | rejected={result['rejected']} | keywords={' / '.join(result['keywords'])}"
        )
        for product in result["products"]:
            print(
                "PRODUCT | "
                f"{product.get('platform')} | {str(product.get('product_name') or '')[:80]} | "
                f"{product.get('final_url')} | images={len(product.get('picture') or [])} | "
                f"score={product.get('quality_score')}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
