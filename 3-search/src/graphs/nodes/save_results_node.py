"""
保存结果节点
职责：将商品检索结果写入 Postgres
"""
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import SaveResultsInput, SaveResultsOutput

logger = logging.getLogger(__name__)


def _merge_keyword_text(existing: Optional[str], incoming: Optional[str]) -> Optional[str]:
    merged: List[str] = []
    seen = set()
    for value in (existing or "", incoming or ""):
        for keyword in str(value).split(","):
            normalized = keyword.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)
    return ", ".join(merged) or None


def _product_identity_keys(product: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    product_url = str(product.get("product_url") or "").strip()
    if product_url:
        return product_url, None

    product_id = str(product.get("product_id") or "").strip()
    product_name = str(product.get("product_name") or "").strip()
    if product_id or product_name:
        return None, f"{product_id}::{product_name}".strip(":")
    return None, None


def _normalize_picture(product: Dict[str, Any]) -> List[Any]:
    picture = product.get("picture")
    return picture if isinstance(picture, list) else []


def _ensure_tables():
    from storage.database.db import get_engine
    from storage.database.shared.model import Base, SearchProduct, SearchRun

    engine = get_engine()
    Base.metadata.create_all(
        bind=engine,
        tables=[SearchRun.__table__, SearchProduct.__table__],
    )


def _ensure_search_run(
    session,
    *,
    patent_record_id: int,
    analysis_session_id: str,
    search_run_id: int,
    product_dataset_id: str,
    retrieval_start_time: str,
):
    from storage.database.shared.model import SearchRun

    search_run = None
    if search_run_id > 0:
        search_run = session.get(SearchRun, search_run_id)

    if search_run is None and analysis_session_id:
        search_run = (
            session.query(SearchRun)
            .filter(
                SearchRun.patent_record_id == patent_record_id,
                SearchRun.analysis_session_id == analysis_session_id,
                SearchRun.product_dataset_id == product_dataset_id,
            )
            .order_by(SearchRun.id.desc())
            .first()
        )

    if search_run is None:
        search_run = SearchRun(
            patent_record_id=patent_record_id,
            analysis_session_id=analysis_session_id or None,
            product_dataset_id=product_dataset_id,
            retrieval_start_time=retrieval_start_time,
            platforms_queried=["Coze工作流"],
            is_complete=False,
            total_products_count=0,
        )
        session.add(search_run)
        session.flush()

    if product_dataset_id and not search_run.product_dataset_id:
        search_run.product_dataset_id = product_dataset_id
    if retrieval_start_time and not search_run.retrieval_start_time:
        search_run.retrieval_start_time = retrieval_start_time
    if not search_run.platforms_queried:
        search_run.platforms_queried = ["Coze工作流"]
    search_run.updated_at = datetime.datetime.now(datetime.timezone.utc)
    session.flush()
    return search_run


def _upsert_products(
    session,
    *,
    search_run_id: int,
    patent_record_id: int,
    analysis_session_id: str,
    products: List[Dict[str, Any]],
) -> int:
    from storage.database.shared.model import SearchProduct

    existing_rows = (
        session.query(SearchProduct)
        .filter(
            SearchProduct.patent_record_id == patent_record_id,
            SearchProduct.analysis_session_id == (analysis_session_id or None),
        )
        .all()
    )
    by_url: Dict[str, Any] = {}
    by_fallback: Dict[str, Any] = {}
    for row in existing_rows:
        product_url = str(row.product_url or "").strip()
        fallback_key = f"{str(row.product_id or '').strip()}::{str(row.product_name or '').strip()}".strip(":")
        if product_url:
            by_url[product_url] = row
        if fallback_key:
            by_fallback[fallback_key] = row

    for product in products:
        product_url, fallback_key = _product_identity_keys(product)
        existing = None
        if product_url and product_url in by_url:
            existing = by_url[product_url]
        elif fallback_key and fallback_key in by_fallback:
            existing = by_fallback[fallback_key]

        if existing is None:
            existing = SearchProduct(
                search_run_id=search_run_id,
                patent_record_id=patent_record_id,
                analysis_session_id=analysis_session_id or None,
                product_id=str(product.get("product_id", "")) or None,
                product_name=product.get("product_name"),
                product_url=product.get("product_url"),
                product_source=product.get("product_source"),
                price=str(product.get("price", "")) if product.get("price") is not None else None,
                brand=product.get("brand"),
                manufacturer=product.get("manufacturer"),
                matched_keywords=product.get("matched_keywords"),
                description=product.get("description"),
                picture=_normalize_picture(product),
                raw_payload=product.get("raw_payload") if isinstance(product.get("raw_payload"), dict) else product,
            )
            session.add(existing)
            session.flush()
            if product_url:
                by_url[product_url] = existing
            elif fallback_key:
                by_fallback[fallback_key] = existing
            continue

        existing.search_run_id = search_run_id
        existing.product_id = existing.product_id or (str(product.get("product_id", "")) or None)
        existing.product_name = existing.product_name or product.get("product_name")
        existing.product_url = existing.product_url or product.get("product_url")
        existing.product_source = existing.product_source or product.get("product_source")
        existing.price = existing.price or (str(product.get("price", "")) if product.get("price") is not None else None)
        existing.brand = existing.brand or product.get("brand")
        existing.manufacturer = existing.manufacturer or product.get("manufacturer")
        existing.description = existing.description or product.get("description")
        existing.picture = existing.picture or _normalize_picture(product)
        existing.matched_keywords = _merge_keyword_text(existing.matched_keywords, product.get("matched_keywords"))
        if not existing.raw_payload and product.get("raw_payload"):
            existing.raw_payload = product.get("raw_payload")

    return int(
        session.query(SearchProduct)
        .filter(
            SearchProduct.patent_record_id == patent_record_id,
            SearchProduct.analysis_session_id == (analysis_session_id or None),
        )
        .count()
    )


def persist_search_results(
    *,
    patent_record_id: int,
    analysis_session_id: str,
    products: List[Dict[str, Any]],
    product_dataset_id: str,
    retrieval_start_time: str,
    search_run_id: int,
    successful_keywords_count: int,
    failed_keywords_count: int,
    is_complete: bool,
    error_message: str,
) -> SaveResultsOutput:
    from storage.database.db import get_session

    _ensure_tables()
    session = get_session()
    try:
        search_run = _ensure_search_run(
            session,
            patent_record_id=patent_record_id,
            analysis_session_id=analysis_session_id,
            search_run_id=search_run_id,
            product_dataset_id=product_dataset_id,
            retrieval_start_time=retrieval_start_time,
        )
        total_products_count = _upsert_products(
            session,
            search_run_id=int(search_run.id),
            patent_record_id=patent_record_id,
            analysis_session_id=analysis_session_id,
            products=products,
        )
        search_run.successful_keywords_count = successful_keywords_count
        search_run.failed_keywords_count = failed_keywords_count
        search_run.total_products_count = total_products_count
        search_run.is_complete = is_complete
        search_run.error_message = error_message or None
        search_run.updated_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()
        return SaveResultsOutput(
            search_run_id=int(search_run.id),
            product_dataset_id=search_run.product_dataset_id or product_dataset_id,
            total_products_count=total_products_count,
            is_complete=is_complete,
            error_message=error_message or "",
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def save_results_node(
    state: SaveResultsInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> SaveResultsOutput:
    """
    title: 保存结果
    desc: 将商品数据保存到Postgres
    integrations: Postgres数据库
    """
    try:
        result = persist_search_results(
            patent_record_id=state.patent_record_id,
            analysis_session_id=state.analysis_session_id,
            products=state.products,
            product_dataset_id=state.product_dataset_id,
            retrieval_start_time=state.retrieval_start_time,
            search_run_id=state.search_run_id,
            successful_keywords_count=state.successful_keywords_count,
            failed_keywords_count=state.failed_keywords_count,
            is_complete=state.is_complete,
            error_message=state.error_message,
        )
        logger.info(
            "商品检索结果已写入数据库, patent_record_id=%s, search_run_id=%s, products=%s, complete=%s",
            state.patent_record_id,
            result.search_run_id,
            result.total_products_count,
            result.is_complete,
        )
        return result
    except Exception as error:
        logger.error("保存商品结果失败: %s", error, exc_info=True)
        return SaveResultsOutput(
            search_run_id=state.search_run_id,
            product_dataset_id=state.product_dataset_id,
            total_products_count=len(state.products),
            is_complete=False,
            error_message=f"保存结果失败: {error}",
        )
