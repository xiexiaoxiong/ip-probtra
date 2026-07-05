from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from product_search.config import bootstrap_local_env, get_settings
from product_search.db import fetch_keywords, get_engine
from product_search.models import ProductSearchInput
from product_search.platforms import OBJECT_BASE_TERMS, derive_title_product_keywords
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


def print_line(message: str) -> None:
    print(message, flush=True)


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
    payload = ProductSearchInput(
        patent_record_id=record.id,
        analysis_session_id=f"{record.task_id}_product_detail_test",
        input_keywords=keywords,
        platforms=args.platforms,
        max_keywords=args.max_keywords,
        max_candidates_per_keyword=args.max_candidates_per_keyword,
        max_detail_candidates=args.max_detail_candidates,
        max_products=args.max_products,
        request_timeout_seconds=args.request_timeout_seconds,
        serp_url_limit=args.serp_url_limit,
        persist=not args.no_persist,
    )
    if args.service_url:
        async with httpx.AsyncClient(timeout=httpx.Timeout(args.http_timeout_seconds)) as client:
            response = await client.post(f"{args.service_url.rstrip('/')}/run", json=payload.model_dump())
            response.raise_for_status()
            data = response.json()
        products = data.get("products") or []
        return {
            "record_id": record.id,
            "patent_number": record.patent_number,
            "title": record.title,
            "keyword_source": source,
            "run_id": int(data.get("product_detail_search_run_id") or 0),
            "keywords": list(data.get("keywords") or []),
            "candidates": int(data.get("total_candidate_links_count") or 0),
            "accepted": int(data.get("accepted_products_count") or 0),
            "rejected": int(data.get("rejected_candidates_count") or 0),
            "products": products,
        }

    service = ProductSearchService(get_settings())
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


def keyword_source_to_dict(source: KeywordSource | None) -> dict[str, Any] | None:
    if source is None:
        return None
    return asdict(source)


def summarize_product(product: dict[str, Any]) -> dict[str, Any]:
    pictures = product.get("picture") or []
    if not isinstance(pictures, list):
        pictures = []
    return {
        "platform": product.get("platform"),
        "product_name": product.get("product_name"),
        "final_url": product.get("final_url"),
        "source_text_url": product.get("source_text_url"),
        "source_image_url": product.get("source_image_url"),
        "picture_count": len(pictures),
        "pictures_sample": pictures[:3],
        "quality_score": product.get("quality_score"),
        "price": product.get("price"),
        "sales": product.get("sales"),
        "brand": product.get("brand"),
        "manufacturer": product.get("manufacturer"),
    }


def build_outcome(
    record: ExampleRecord,
    source: KeywordSource | None,
    status: str,
    *,
    ok: bool,
    elapsed_seconds: float = 0.0,
    error: str | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    outcome: dict[str, Any] = {
        "ok": ok,
        "status": status,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "record": asdict(record),
        "keyword_source": keyword_source_to_dict(source),
    }
    if error:
        outcome["error"] = error
    if result:
        products = [summarize_product(product) for product in result.get("products", [])]
        outcome.update(
            {
                "run_id": int(result.get("run_id") or 0),
                "keywords": list(result.get("keywords") or []),
                "candidates": int(result.get("candidates") or 0),
                "accepted": int(result.get("accepted") or 0),
                "rejected": int(result.get("rejected") or 0),
                "products": products,
            }
        )
    return outcome


async def append_jsonl(path: Path | None, lock: asyncio.Lock, outcome: dict[str, Any]) -> None:
    if path is None:
        return
    line = json.dumps(outcome, ensure_ascii=False, sort_keys=True)
    async with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


async def run_record(
    record: ExampleRecord,
    source: KeywordSource,
    args: argparse.Namespace,
    jsonl_path: Path | None,
    jsonl_lock: asyncio.Lock,
) -> dict[str, Any]:
    print_line(
        f"START | record={record.id} | {record.patent_number} | {record.title} | "
        f"source={source.reason}:{source.patent_record_id}:{source.analysis_session_id}"
    )
    started = time.monotonic()
    if args.list_only:
        outcome = build_outcome(record, source, "listed", ok=True)
        await append_jsonl(jsonl_path, jsonl_lock, outcome)
        return outcome

    try:
        run_coro = run_one(record, source, args)
        if args.per_record_timeout_seconds:
            result = await asyncio.wait_for(run_coro, timeout=args.per_record_timeout_seconds)
        else:
            result = await run_coro
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - started
        outcome = build_outcome(
            record,
            source,
            "timeout",
            ok=False,
            elapsed_seconds=elapsed,
            error=f"per-record timeout after {args.per_record_timeout_seconds}s",
        )
        print_line(
            f"TIMEOUT | record={record.id} | elapsed={outcome['elapsed_seconds']}s | "
            f"timeout={args.per_record_timeout_seconds}s"
        )
        await append_jsonl(jsonl_path, jsonl_lock, outcome)
        return outcome
    except Exception as exc:
        elapsed = time.monotonic() - started
        outcome = build_outcome(
            record,
            source,
            "error",
            ok=False,
            elapsed_seconds=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )
        print_line(f"ERROR | record={record.id} | elapsed={outcome['elapsed_seconds']}s | {outcome['error']}")
        await append_jsonl(jsonl_path, jsonl_lock, outcome)
        return outcome

    elapsed = time.monotonic() - started
    accepted = int(result.get("accepted") or 0)
    status = "ok" if accepted > 0 else "empty"
    outcome = build_outcome(
        record,
        source,
        status,
        ok=(accepted > 0 or not args.fail_on_empty),
        elapsed_seconds=elapsed,
        result=result,
    )
    print_line(
        f"DONE | record={record.id} | run={result['run_id']} | candidates={result['candidates']} | "
        f"accepted={result['accepted']} | rejected={result['rejected']} | "
        f"elapsed={outcome['elapsed_seconds']}s | keywords={' / '.join(result['keywords'])}"
    )
    for product in result["products"]:
        print_line(
            "PRODUCT | "
            f"{product.get('platform')} | {str(product.get('product_name') or '')[:80]} | "
            f"{product.get('final_url')} | images={len(product.get('picture') or [])} | "
            f"score={product.get('quality_score')}"
        )
    await append_jsonl(jsonl_path, jsonl_lock, outcome)
    return outcome


async def run_records(
    records: list[tuple[ExampleRecord, KeywordSource]],
    args: argparse.Namespace,
    jsonl_path: Path | None,
    jsonl_lock: asyncio.Lock,
) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def guarded(record: ExampleRecord, source: KeywordSource) -> dict[str, Any]:
        async with semaphore:
            return await run_record(record, source, args, jsonl_path, jsonl_lock)

    tasks = [asyncio.create_task(guarded(record, source)) for record, source in records]
    outcomes: list[dict[str, Any]] = []
    for task in asyncio.as_completed(tasks):
        outcomes.append(await task)
    return outcomes


def print_summary(outcomes: list[dict[str, Any]]) -> None:
    status_counts: dict[str, int] = {}
    for outcome in outcomes:
        status = str(outcome.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    accepted_records = sum(1 for outcome in outcomes if int(outcome.get("accepted") or 0) > 0)
    accepted_products = sum(int(outcome.get("accepted") or 0) for outcome in outcomes)
    parts = [
        f"records={len(outcomes)}",
        f"accepted_records={accepted_records}",
        f"accepted_products={accepted_products}",
    ]
    parts.extend(f"{status}={count}" for status, count in sorted(status_counts.items()))
    print_line("SUMMARY | " + " | ".join(parts))


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
        has_core_object = any(term in keyword for term in OBJECT_BASE_TERMS)
        priority = 60
        if "REQUIRED" in keyword_type and effective_len >= 5 and has_core_object:
            priority = 5
        elif "INVENTION" in keyword_type and has_core_object:
            priority = 8
        elif "COMBINED" in keyword_type and effective_len >= 5 and has_core_object:
            priority = 10
        elif "OBJECT_BASE" in keyword_type:
            priority = 30
        elif "REQUIRED" in keyword_type:
            priority = 70
        elif "HOLDER" in keyword_type:
            priority = 75
        weighted.append((priority, -confidence, -effective_len, int(row["id"]), keyword))
    weighted.sort(key=lambda item: (item[0], item[1], item[2], item[3]))

    fallback_keywords = derive_title_product_keywords(record.title, limit=1)
    result: list[str] = []
    seen: set[str] = set()
    feature_slots = max(0, max_keywords - len(fallback_keywords))
    for *_rest, keyword in weighted:
        if len(result) >= feature_slots:
            break
        if keyword in seen:
            continue
        seen.add(keyword)
        result.append(keyword)
    for keyword in fallback_keywords:
        if keyword in seen:
            continue
        seen.add(keyword)
        result.append(keyword)
        if len(result) >= max_keywords:
            break
    for *_rest, keyword in weighted:
        if len(result) >= max_keywords:
            break
        if keyword in seen:
            continue
        seen.add(keyword)
        result.append(keyword)
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
    parser.add_argument("--request-timeout-seconds", type=int, default=None)
    parser.add_argument("--serp-url-limit", type=int, default=None)
    parser.add_argument("--platforms", nargs="+", default=["jd", "1688"])
    parser.add_argument("--service-url", default="", help="call a running 3-product-search HTTP service instead of direct in-process service")
    parser.add_argument("--http-timeout-seconds", type=int, default=240)
    parser.add_argument("--per-record-timeout-seconds", type=int, default=0, help="wall-clock timeout per patent record; 0 disables")
    parser.add_argument("--concurrency", type=int, default=1, help="max patent records to run in parallel")
    parser.add_argument("--jsonl-output", default="", help="write one JSON object per record as soon as it finishes")
    parser.add_argument("--fail-on-empty", action="store_true", help="return non-zero if any searched record has no accepted products or no keywords")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--list-only", action="store_true", help="only list keyword sources; do not search products")
    parser.add_argument("--single-source-keywords", action="store_true", help="only use the selected keyword source instead of aggregating same-patent module2 keywords")
    args = parser.parse_args()
    args.concurrency = max(1, args.concurrency)

    bootstrap_local_env()
    jsonl_path = Path(args.jsonl_output).expanduser() if args.jsonl_output else None
    if jsonl_path:
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path.write_text("", encoding="utf-8")
        print_line(f"JSONL | path={jsonl_path}")

    records = load_example_records(args.prefix)
    if args.record_id:
        wanted = set(args.record_id)
        records = [record for record in records if record.id in wanted]
    if args.limit:
        records = records[: args.limit]

    print_line(f"examples={len(records)}")
    jsonl_lock = asyncio.Lock()
    runnable: list[tuple[ExampleRecord, KeywordSource]] = []
    outcomes: list[dict[str, Any]] = []
    for record in records:
        source = find_keyword_source(record)
        if not source:
            print_line(f"NO_KEYWORDS | record={record.id} | {record.patent_number} | {record.title}")
            outcome = build_outcome(
                record,
                None,
                "no_keywords",
                ok=not args.fail_on_empty,
                error="no matching keyword_records source",
            )
            await append_jsonl(jsonl_path, jsonl_lock, outcome)
            outcomes.append(outcome)
            continue
        runnable.append((record, source))

    outcomes.extend(await run_records(runnable, args, jsonl_path, jsonl_lock))
    print_summary(outcomes)
    has_runtime_failure = any(outcome.get("status") in {"error", "timeout"} for outcome in outcomes)
    has_empty_failure = args.fail_on_empty and any(outcome.get("status") in {"empty", "no_keywords"} for outcome in outcomes)
    return 1 if has_runtime_failure or has_empty_failure else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
