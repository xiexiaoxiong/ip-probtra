"""
Coze工作流搜索节点
职责：调用Coze工作流API，传入关键词，获取商品搜索结果
"""
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import CozeSearchInput, CozeSearchOutput

logger = logging.getLogger(__name__)

COZE_API_URL = os.getenv("COZE_SEARCH_API_URL", "https://66vpykvvz2.coze.site/run")
COZE_API_TOKEN = os.getenv("COZE_SEARCH_API_TOKEN", "")
COZE_REQUEST_TIMEOUT = int(os.getenv("COZE_SEARCH_TIMEOUT", "300"))
COZE_MAX_CONCURRENT = int(os.getenv("COZE_MAX_CONCURRENT", "3"))
COZE_COLD_START_RETRIES = int(os.getenv("COZE_COLD_START_RETRIES", "2"))
COZE_COLD_START_EXTRA_TIMEOUT = int(os.getenv("COZE_COLD_START_EXTRA_TIMEOUT", "600"))
COZE_BATCH_SIZE = int(os.getenv("COZE_BATCH_SIZE", "3"))


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


def _deduplicate_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按URL去重"""
    seen_urls = set()
    deduped = []
    for p in products:
        url = p.get("product_url", "")
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        deduped.append(p)
    return deduped


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
            total_products_count=0,
            successful_keywords_count=0,
            failed_keywords_count=0,
            is_complete=True,
            error_message="未提供搜索关键词",
        )

    if not COZE_API_TOKEN:
        return CozeSearchOutput(
            products=[],
            total_products_count=0,
            successful_keywords_count=0,
            failed_keywords_count=len(keywords),
            is_complete=False,
            error_message="未配置COZE_SEARCH_API_TOKEN环境变量",
        )

    try:
        # 只取第3个关键词传给Coze API（单关键词调用）
        keyword_index = min(2, len(keywords) - 1)
        selected_keyword = keywords[keyword_index]
        logger.info("共%d个关键词，只取第%d个关键词调用Coze: '%s'", len(keywords), keyword_index + 1, selected_keyword)

        all_products = _call_coze_api([selected_keyword])

        if not all_products:
            all_products = []

        all_products = _deduplicate_products(all_products)
        successful = 1 if all_products else 0
        failed = 0 if all_products else 1

        return CozeSearchOutput(
            products=all_products,
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
            total_products_count=0,
            successful_keywords_count=0,
            failed_keywords_count=len(keywords),
            is_complete=False,
            error_message=f"Coze搜索失败: {e}",
        )