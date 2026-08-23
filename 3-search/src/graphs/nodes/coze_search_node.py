"""
Coze工作流搜索节点
职责：调用Coze工作流API，传入关键词，获取商品搜索结果
"""
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import CozeSearchInput, CozeSearchOutput
from graphs.nodes.save_results_node import persist_search_results

logger = logging.getLogger(__name__)

COZE_API_URL = os.getenv("COZE_SEARCH_API_URL", "https://66vpykvvz2.coze.site/run")
COZE_API_TOKEN = os.getenv("COZE_SEARCH_API_TOKEN", "")
COZE_REQUEST_TIMEOUT = int(os.getenv("COZE_SEARCH_TIMEOUT", "300"))
COZE_MAX_CONCURRENT = int(os.getenv("COZE_MAX_CONCURRENT", "3"))
COZE_COLD_START_RETRIES = int(os.getenv("COZE_COLD_START_RETRIES", "2"))
COZE_COLD_START_EXTRA_TIMEOUT = int(os.getenv("COZE_COLD_START_EXTRA_TIMEOUT", "600"))
COZE_PER_KEYWORD_PRODUCT_LIMIT = int(os.getenv("COZE_PER_KEYWORD_PRODUCT_LIMIT", "5"))

PRODUCT_CATEGORY_SUFFIXES = (
    "扫地机器人", "机器人", "头戴耳机", "开放式耳机", "耳机", "音响", "扬声器",
    "跑步机", "椭圆机", "划船机", "健身车", "洗衣机", "洗碗机", "吸尘器",
    "净水器", "净化器", "显示器", "摄像头", "计时器", "电饭煲", "冰箱",
    "空调", "风扇", "门锁", "水枪", "泵", "阀", "锅", "杯", "灯具",
)


def _normalize_match_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _expanded_object_terms(object_terms: List[str]) -> List[str]:
    expanded: List[str] = []
    seen: set[str] = set()
    for value in object_terms:
        normalized = _normalize_match_text(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            expanded.append(normalized)
        for suffix in PRODUCT_CATEGORY_SUFFIXES:
            normalized_suffix = _normalize_match_text(suffix)
            if normalized.endswith(normalized_suffix) and normalized_suffix not in seen:
                seen.add(normalized_suffix)
                expanded.append(normalized_suffix)
    return expanded


def _product_matches_object_terms(product: Dict[str, Any], object_terms: List[str]) -> bool:
    anchors = _expanded_object_terms(object_terms)
    if not anchors:
        return True
    product_text = _normalize_match_text(
        " ".join(
            [
                str(product.get("product_name") or ""),
                str(product.get("description") or ""),
            ]
        )
    )
    return any(anchor in product_text for anchor in anchors)


def _extract_products_from_response(response_data: Any) -> List[Dict[str, Any]]:
    """从Coze响应中提取商品列表，兼容多种响应格式"""
    products = []

    if isinstance(response_data, list):
        products = response_data
    elif isinstance(response_data, dict):
        for key in ("products", "data", "results", "items", "output", "result"):
            if key in response_data:
                val = response_data[key]
                if isinstance(val, list):
                    products = val
                    break
        if not products and any(k in response_data for k in ("title", "product_name", "url")):
            products = [response_data]
    elif isinstance(response_data, str):
        try:
            parsed = json.loads(response_data)
            return _extract_products_from_response(parsed)
        except json.JSONDecodeError:
            json_match = re.search(r'\[.*\]', response_data, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                    return _extract_products_from_response(parsed)
                except json.JSONDecodeError:
                    pass

    return products if isinstance(products, list) else []


def _normalize_product(raw: Dict[str, Any], keyword: str) -> Dict[str, Any]:
    """将Coze返回的商品数据归一化为统一的内部格式"""
    return {
        "product_id": raw.get("product_id") or raw.get("id") or "",
        "product_name": raw.get("product_name") or raw.get("title") or raw.get("name") or "",
        "product_url": raw.get("product_url") or raw.get("url") or raw.get("link") or "",
        "product_source": raw.get("product_source") or raw.get("source") or raw.get("platform") or "",
        "price": raw.get("price") or "",
        "brand": raw.get("brand") or "",
        "manufacturer": raw.get("manufacturer") or raw.get("Manufacturer") or "",
        "matched_keywords": raw.get("matched_keywords") or keyword or "",
        "description": raw.get("description") or raw.get("summary") or raw.get("product_raw_text") or "",
        "picture": raw.get("picture") or raw.get("images") or raw.get("image_urls") or [],
        "raw_payload": raw,
    }


def _call_coze_api(
    keywords: List[str],
    retry_on_cold_start: bool = True,
) -> List[Dict[str, Any]]:
    """同步调用Coze工作流API，支持冷启动重试"""
    headers = {
        "Authorization": f"Bearer {COZE_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"keywords": keywords}

    logger.info("调用Coze工作流, keywords=%s", keywords)
    max_attempts = (1 + COZE_COLD_START_RETRIES) if retry_on_cold_start else 1

    for attempt in range(1, max_attempts + 1):
        try:
            timeout = float(COZE_REQUEST_TIMEOUT) if attempt == 1 else float(COZE_COLD_START_EXTRA_TIMEOUT)
            with httpx.Client(timeout=timeout) as client:
                response = client.post(COZE_API_URL, headers=headers, json=payload)
                response.raise_for_status()

                response_text = response.text
                logger.info("Coze响应(前500字符): %s", response_text[:500])

                break

        except httpx.TimeoutException:
            logger.warning("Coze工作流调用超时(attempt=%d/%d), keywords=%s", attempt, max_attempts, keywords)
            if attempt >= max_attempts:
                logger.error("Coze工作流最终超时, keywords=%s", keywords)
                return []
            logger.info("可能是冷启动，延长超时重试...")
            continue
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (502, 503, 504) and attempt < max_attempts:
                logger.warning("Coze冷启动HTTP错误(attempt=%d/%d), status=%s, 重试...", attempt, max_attempts, e.response.status_code)
                continue
            logger.error("Coze工作流HTTP错误, keywords=%s, status=%s, body=%s", keywords, e.response.status_code, e.response.text[:300])
            return []
        except Exception as e:
            logger.error("Coze工作流调用异常, keywords=%s, error=%s", keywords, e, exc_info=True)
            return []

    try:
        response_data = json.loads(response_text)
    except json.JSONDecodeError:
        logger.warning("Coze响应非JSON格式, keywords=%s", keywords)
        return []

    raw_products = _extract_products_from_response(response_data)

    matched_kw = ", ".join(keywords)
    products = [_normalize_product(p, matched_kw) for p in raw_products if isinstance(p, dict)]
    logger.info("搜索到%d个商品, keywords=%s", len(products), keywords)
    return products


def _merge_keyword_text(existing: str, incoming: str) -> str:
    merged: List[str] = []
    seen = set()
    for value in (existing, incoming):
        for keyword in str(value or "").split(","):
            normalized = keyword.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                merged.append(normalized)
    return ", ".join(merged)


def _get_product_dedup_key(product: Dict[str, Any]) -> str:
    product_url = str(product.get("product_url") or "").strip()
    if product_url:
        return f"url::{product_url}"

    product_id = str(product.get("product_id") or "").strip()
    if product_id:
        return f"id::{product_id}"

    product_name = str(product.get("product_name") or "").strip()
    product_source = str(product.get("product_source") or "").strip()
    return f"name::{product_name}::{product_source}"


def _deduplicate_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """统一去重并合并重复商品命中的关键词信息。"""
    deduped_map: Dict[str, Dict[str, Any]] = {}
    ordered_keys: List[str] = []

    for product in products:
        dedup_key = _get_product_dedup_key(product)
        existing = deduped_map.get(dedup_key)
        if existing is None:
            deduped_map[dedup_key] = dict(product)
            ordered_keys.append(dedup_key)
            continue

        existing["matched_keywords"] = _merge_keyword_text(
            str(existing.get("matched_keywords") or ""),
            str(product.get("matched_keywords") or ""),
        )

        for field in ("description", "price", "brand", "manufacturer", "product_source", "product_url", "product_name"):
            if not existing.get(field) and product.get(field):
                existing[field] = product[field]

        if not existing.get("picture") and product.get("picture"):
            existing["picture"] = product["picture"]
        if not existing.get("raw_payload") and product.get("raw_payload"):
            existing["raw_payload"] = product["raw_payload"]

    return [deduped_map[key] for key in ordered_keys]


def _search_products_for_keyword(keyword: str) -> Tuple[str, List[Dict[str, Any]]]:
    products = _call_coze_api([keyword])
    limited_products = products[:COZE_PER_KEYWORD_PRODUCT_LIMIT]
    logger.info(
        "关键词 '%s' 检索到%d个商品，截断后保留%d个",
        keyword,
        len(products),
        len(limited_products),
    )
    return keyword, limited_products


def coze_search_node(
    state: CozeSearchInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> CozeSearchOutput:
    """
    title: Coze工作流搜索商品
    desc: 调用Coze工作流API，传入关键词搜索商品，返回商品列表
    integrations: Coze工作流API
    """
    keywords = state.keywords
    if not keywords:
        return CozeSearchOutput(
            products=[],
            search_run_id=int(state.search_run_id or 0),
            total_products_count=0,
            successful_keywords_count=0,
            failed_keywords_count=0,
            is_complete=True,
            error_message="未提供搜索关键词",
        )

    if not COZE_API_TOKEN:
        return CozeSearchOutput(
            products=[],
            search_run_id=int(state.search_run_id or 0),
            total_products_count=0,
            successful_keywords_count=0,
            failed_keywords_count=len(keywords),
            is_complete=False,
            error_message="未配置COZE_SEARCH_API_TOKEN环境变量",
        )

    try:
        logger.info(
            "共%d个关键词，逐个调用Coze检索；每个关键词最多保留%d个商品",
            len(keywords),
            COZE_PER_KEYWORD_PRODUCT_LIMIT,
        )

        all_products: List[Dict[str, Any]] = []
        max_workers = max(1, min(COZE_MAX_CONCURRENT, len(keywords)))
        successful = 0
        failed = 0
        search_run_id = int(state.search_run_id or 0)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_keyword = {
                executor.submit(_search_products_for_keyword, keyword): keyword
                for keyword in keywords
            }

            for future in as_completed(future_to_keyword):
                keyword = future_to_keyword[future]
                products: List[Dict[str, Any]] = []
                try:
                    _, products = future.result()
                except Exception as error:
                    failed += 1
                    logger.warning("关键词 '%s' 检索异常: %s", keyword, error)
                    continue

                if state.object_terms and products:
                    raw_count = len(products)
                    products = [
                        product
                        for product in products
                        if _product_matches_object_terms(product, state.object_terms)
                    ]
                    rejected_count = raw_count - len(products)
                    if rejected_count:
                        logger.warning(
                            "关键词 '%s' 的结果中剔除%d个跨品类商品, object_terms=%s",
                            keyword,
                            rejected_count,
                            state.object_terms,
                        )

                if products:
                    successful += 1
                    all_products.extend(products)
                else:
                    failed += 1
                    logger.warning("关键词 '%s' 未检索到商品", keyword)

                all_products = _deduplicate_products(all_products)
                persisted = persist_search_results(
                    patent_record_id=state.patent_record_id,
                    analysis_session_id=state.analysis_session_id,
                    products=all_products,
                    product_dataset_id=state.product_dataset_id,
                    retrieval_start_time=state.retrieval_start_time,
                    search_run_id=search_run_id,
                    successful_keywords_count=successful,
                    failed_keywords_count=failed,
                    is_complete=False,
                    error_message="",
                )
                search_run_id = persisted.search_run_id

        return CozeSearchOutput(
            products=all_products,
            search_run_id=search_run_id,
            total_products_count=len(all_products),
            successful_keywords_count=successful,
            failed_keywords_count=failed,
            is_complete=True,
            error_message="",
        )

    except Exception as e:
        logger.error("Coze搜索异常: %s", e, exc_info=True)
        return CozeSearchOutput(
            products=[],
            search_run_id=int(state.search_run_id or 0),
            total_products_count=0,
            successful_keywords_count=0,
            failed_keywords_count=len(keywords),
            is_complete=False,
            error_message=f"Coze搜索失败: {e}",
        )
