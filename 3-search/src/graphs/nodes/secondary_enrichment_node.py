"""
二次商品信息补全节点。

该节点只补强第一次检索已经确认的商品，不创建新商品。所有外部资料必须先
通过 URL / 商品 ID / 商品名称相似度校验，才会并入 search_products。
"""
import asyncio
import datetime
import hashlib
import logging
import os
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import SecondaryEnrichmentInput, SecondaryEnrichmentOutput

logger = logging.getLogger(__name__)


ENRICHMENT_ENABLED = (os.getenv("SECONDARY_ENRICHMENT_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"})
ENRICHMENT_MAX_PRODUCTS = int(os.getenv("SECONDARY_ENRICHMENT_MAX_PRODUCTS", "10") or "10")
ENRICHMENT_MAX_TEXT_CHARS = int(os.getenv("SECONDARY_ENRICHMENT_MAX_TEXT_CHARS", "12000") or "12000")
ENRICHMENT_TIMEOUT_SECONDS = float(os.getenv("SECONDARY_ENRICHMENT_TIMEOUT_SECONDS", "20") or "20")
ENRICHMENT_PRODUCT_TIMEOUT_SECONDS = float(os.getenv("SECONDARY_ENRICHMENT_PRODUCT_TIMEOUT_SECONDS", "45") or "45")
ENRICHMENT_ENABLE_PLAYWRIGHT = os.getenv("SECONDARY_ENRICHMENT_ENABLE_PLAYWRIGHT", "1").strip().lower() in {"1", "true", "yes", "on"}
ENRICHMENT_ENABLE_IMAGE_OCR = os.getenv("SECONDARY_ENRICHMENT_ENABLE_IMAGE_OCR", "1").strip().lower() in {"1", "true", "yes", "on"}
ENRICHMENT_ENABLE_IMAGE_VISION = os.getenv("SECONDARY_ENRICHMENT_ENABLE_IMAGE_VISION", "1").strip().lower() in {"1", "true", "yes", "on"}
ENRICHMENT_IMAGE_LIMIT = int(os.getenv("SECONDARY_ENRICHMENT_IMAGE_LIMIT", "4") or "4")
ENRICHMENT_ENABLE_COZE_EXACT_SEARCH = os.getenv("SECONDARY_ENRICHMENT_ENABLE_COZE_EXACT_SEARCH", "0").strip().lower() in {"1", "true", "yes", "on"}
ENRICHMENT_COZE_QUERY_LIMIT = int(os.getenv("SECONDARY_ENRICHMENT_COZE_QUERY_LIMIT", "1") or "1")
SECONDARY_SEARCH_API_URL = os.getenv("SECONDARY_SEARCH_API_URL", "").strip()
ENRICHMENT_ENABLE_DIRECT_WEB_SEARCH = os.getenv("SECONDARY_ENRICHMENT_ENABLE_DIRECT_WEB_SEARCH", "1").strip().lower() in {"1", "true", "yes", "on"}
ENRICHMENT_DIRECT_WEB_QUERY_LIMIT = int(os.getenv("SECONDARY_ENRICHMENT_DIRECT_WEB_QUERY_LIMIT", "2") or "2")

SUPPLEMENT_QUERY_SUFFIXES = [
    "拆机",
    "评测",
    "参数",
    "详情",
    "功能",
    "视频",
    "图文详情",
]

VIDEO_HOST_PATTERNS = (
    "bilibili.com",
    "douyin.com",
    "ixigua.com",
    "youtube.com",
    "youku.com",
    "iqiyi.com",
    "v.qq.com",
)

ARTICLE_HINT_PATTERNS = (
    "拆机",
    "评测",
    "测评",
    "体验",
    "图文",
    "文章",
    "教程",
    "知乎",
    "什么值得买",
    "值得买",
    "post",
    "article",
    "review",
    "teardown",
)


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_name(value: Any) -> str:
    text = _normalize_space(value).lower()
    text = re.sub(r"[\s_\-—–·,，.。:：;；/\\|()（）\[\]【】{}<>《》\"'“”‘’]+", "", text)
    text = re.sub(r"(旗舰店|官方|正品|包邮|现货|新款|同款|厂家|批发|热卖|爆款)", "", text)
    return text


def _text_similarity(a: Any, b: Any) -> float:
    left = _normalize_name(a)
    right = _normalize_name(b)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right))
    return SequenceMatcher(None, left, right).ratio()


def _identity_text(*values: Any) -> str:
    return " ".join(_normalize_space(value).lower() for value in values if _normalize_space(value))


def _extract_model_tokens(*values: Any) -> List[str]:
    text = _identity_text(*values)
    tokens: List[str] = []
    for token in re.findall(r"[a-z]{1,8}[-_\s]?\d[a-z0-9_-]{1,16}|\d[a-z]{1,8}\d*[a-z0-9_-]{0,12}", text):
        normalized = re.sub(r"[\s_-]+", "", token.lower())
        if len(normalized) >= 3 and normalized not in tokens:
            tokens.append(normalized)
    return tokens[:8]


def _canonical_url(value: Any) -> str:
    url = _normalize_space(value)
    if not url:
        return ""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().removeprefix("www.")
    path = re.sub(r"/+$", "", parsed.path or "")
    return f"{host}{path}".lower()


def _classify_supplement_evidence(title: Any, url: Any, source: Any = "") -> str:
    text = f"{_normalize_space(title)} {_normalize_space(url)} {_normalize_space(source)}".lower()
    if any(host in text for host in VIDEO_HOST_PATTERNS) or any(word in text for word in ("视频", "video")):
        return "video"
    if any(pattern.lower() in text for pattern in ARTICLE_HINT_PATTERNS):
        return "article"
    if any(host in text for host in ("1688.com", "taobao.com", "tmall.com", "jd.com", "pinduoduo.com")):
        return "product_page"
    return "search_result"


def _same_product(original: Dict[str, Any], candidate: Dict[str, Any]) -> Tuple[bool, str, float]:
    original_url = _canonical_url(original.get("product_url"))
    candidate_url = _canonical_url(candidate.get("product_url") or candidate.get("url") or candidate.get("link"))
    if original_url and candidate_url and original_url == candidate_url:
        return True, "url_exact", 1.0

    original_id = _normalize_space(original.get("product_id"))
    candidate_id = _normalize_space(candidate.get("product_id") or candidate.get("id"))
    if original_id and candidate_id and original_id == candidate_id:
        return True, "product_id_exact", 1.0
    candidate_text = _identity_text(
        candidate.get("product_url"),
        candidate.get("url"),
        candidate.get("link"),
        candidate.get("product_name"),
        candidate.get("title"),
        candidate.get("name"),
        candidate.get("page_title"),
    )
    if original_id and len(original_id) >= 5 and original_id.lower() in candidate_text:
        return True, "product_id_in_candidate", 0.98

    candidate_name = (
        candidate.get("product_name")
        or candidate.get("title")
        or candidate.get("name")
        or candidate.get("page_title")
        or ""
    )
    original_brand = _normalize_name(original.get("brand"))
    candidate_normalized = _normalize_name(candidate_name)
    if original_brand and original_brand in candidate_normalized:
        original_tokens = _extract_model_tokens(original.get("product_name"), original.get("product_id"), original.get("product_url"))
        candidate_tokens = _extract_model_tokens(candidate_name, candidate.get("product_url"), candidate.get("url"), candidate.get("link"))
        if original_tokens and set(original_tokens).intersection(candidate_tokens):
            return True, "brand_model_match", 0.92

    similarity = _text_similarity(original.get("product_name"), candidate_name)
    if similarity >= 0.72:
        return True, "name_similarity", similarity
    return False, "identity_mismatch", similarity


def _collect_text_from_capture(capture: Dict[str, Any]) -> str:
    blocks = ((capture.get("text") or {}).get("blocks") or [])
    texts = []
    for block in blocks:
        if isinstance(block, dict):
            text = _normalize_space(block.get("text"))
            if text:
                texts.append(text)
    return "\n".join(texts)


def _summarize_capture(capture: Dict[str, Any]) -> Dict[str, Any]:
    page = capture.get("page") if isinstance(capture.get("page"), dict) else {}
    text = _collect_text_from_capture(capture)
    detail_images = ((capture.get("screenshots") or {}).get("detail_image_crops") or [])
    return {
        "source_type": "product_page_playwright",
        "url": page.get("url"),
        "title": page.get("title"),
        "text": text[:ENRICHMENT_MAX_TEXT_CHARS],
        "detail_image_crops": detail_images[:6] if isinstance(detail_images, list) else [],
        "manual_login": capture.get("manual_login") if isinstance(capture.get("manual_login"), dict) else {},
    }


def _normalize_picture_urls(product: Dict[str, Any]) -> List[str]:
    raw_pictures = product.get("picture")
    if not isinstance(raw_pictures, list):
        raw_payload = product.get("raw_payload") if isinstance(product.get("raw_payload"), dict) else {}
        raw_pictures = raw_payload.get("picture") if isinstance(raw_payload.get("picture"), list) else []

    urls: List[str] = []
    for item in raw_pictures:
        if isinstance(item, str):
            url = item.strip()
        elif isinstance(item, dict):
            url = str(item.get("url") or item.get("src") or item.get("image_url") or "").strip()
        else:
            url = ""
        if url.startswith("http") and url not in urls:
            urls.append(url)
    return urls[: max(0, ENRICHMENT_IMAGE_LIMIT)]


def _image_vision_supplement(product: Dict[str, Any]) -> Dict[str, Any]:
    image_urls = _normalize_picture_urls(product)
    if not ENRICHMENT_ENABLE_IMAGE_VISION or not image_urls:
        return {
            "source_type": "first_search_image_vision",
            "accepted": False,
            "reason": "disabled_or_no_images",
            "image_urls": image_urls,
            "text": "",
        }

    try:
        from tools.product_page_capture import invoke_local_llm

        product_name = _normalize_space(product.get("product_name"))
        system_prompt = (
            "你是商品资料读取助手。只根据图片中能直接观察到的内容提取商品信息，"
            "不得作专利侵权结论，不得臆测图片外的信息。重点记录：商品类型、可见结构、"
            "功能部件、参数文字、包装/详情图文字、可能影响技术特征判断的事实。"
        )
        user_text = (
            f"商品名称：{product_name}\n"
            "请阅读以下商品图片，输出中文要点。若图片信息不足，请明确写“图片未显示”。"
        )
        content: List[Dict[str, Any]] = [{"type": "text", "text": user_text}]
        for url in image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}})
        response_text = invoke_local_llm(
            messages=[
                SystemMessage(content=system_prompt),
                HumanMessage(content=content),
            ],
            model=os.getenv("LOCAL_LLM_VISION_MODEL", "glm-4.5v"),
            max_completion_tokens=2048,
        )
        text = _normalize_space(response_text)
        if not text:
            return {
                "source_type": "first_search_image_vision",
                "accepted": False,
                "reason": "empty_vision_response",
                "image_urls": image_urls,
                "text": "",
            }
        return {
            "source_type": "first_search_image_vision",
            "accepted": True,
            "reason": "first_search_product_images",
            "image_urls": image_urls,
            "text": text[:ENRICHMENT_MAX_TEXT_CHARS],
        }
    except Exception as error:
        logger.info("商品图片视觉补全失败 product=%s error=%s", product.get("product_name"), error)
        return {
            "source_type": "first_search_image_vision",
            "accepted": False,
            "reason": "vision_failed",
            "image_urls": image_urls,
            "error": str(error)[:500],
            "text": "",
        }


def _image_ocr_supplement(product: Dict[str, Any]) -> Dict[str, Any]:
    image_urls = _normalize_picture_urls(product)
    if not ENRICHMENT_ENABLE_IMAGE_OCR or not image_urls:
        return {
            "source_type": "first_search_image_ocr",
            "accepted": False,
            "reason": "disabled_or_no_images",
            "image_urls": image_urls,
            "text": "",
        }

    try:
        from tools.product_page_capture import run_tesseract_ocr

        output_dir = Path(tempfile.gettempdir()) / "patent-secondary-enrichment" / "ocr"
        output_dir.mkdir(parents=True, exist_ok=True)
        texts: List[str] = []
        errors: List[str] = []
        with httpx.Client(timeout=ENRICHMENT_TIMEOUT_SECONDS, follow_redirects=True) as client:
            for index, url in enumerate(image_urls, start=1):
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    image_path = output_dir / f"{hashlib.sha1(url.encode('utf-8')).hexdigest()[:12]}-{index}.jpg"
                    image_path.write_bytes(response.content)
                    text = run_tesseract_ocr(image_path, os.getenv("SECONDARY_ENRICHMENT_OCR_LANGUAGES", "chi_sim+eng"))
                    text = _normalize_space(text)
                    if text:
                        texts.append(text)
                except Exception as error:
                    errors.append(f"{index}: {str(error)[:160]}")
        joined = "\n".join(dict.fromkeys(texts))[:ENRICHMENT_MAX_TEXT_CHARS]
        return {
            "source_type": "first_search_image_ocr",
            "accepted": bool(joined),
            "reason": "first_search_product_image_ocr" if joined else "no_ocr_text",
            "image_urls": image_urls,
            "text": joined,
            "errors": errors[:6],
        }
    except Exception as error:
        logger.info("商品图片 OCR 补全失败 product=%s error=%s", product.get("product_name"), error)
        return {
            "source_type": "first_search_image_ocr",
            "accepted": False,
            "reason": "ocr_failed",
            "image_urls": image_urls,
            "error": str(error)[:500],
            "text": "",
        }


async def _capture_product_url(product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    product_url = _normalize_space(product.get("product_url"))
    if not product_url or not ENRICHMENT_ENABLE_PLAYWRIGHT:
        return None

    try:
        from tools.product_page_capture import CaptureConfig, capture_product_page

        digest = hashlib.sha1(product_url.encode("utf-8")).hexdigest()[:12]
        output_dir = Path(tempfile.gettempdir()) / "patent-secondary-enrichment" / digest
        config = CaptureConfig(
            url=product_url,
            output_dir=output_dir,
            timeout_ms=int(ENRICHMENT_TIMEOUT_SECONDS * 1000),
            headless=True,
            enable_ocr=False,
            enable_detail_image_llm_filter=False,
            enable_fullpage_region_llm=False,
            max_image_crops=4,
            detail_image_candidate_limit=6,
            detail_image_max_results=3,
        )
        capture = await capture_product_page(config)
        summary = _summarize_capture(capture)
        same, reason, score = _same_product(
            product,
            {
                "product_url": summary.get("url"),
                "page_title": summary.get("title"),
            },
        )
        summary["identity_check"] = {
            "accepted": same,
            "reason": reason,
            "score": score,
        }
        return summary
    except Exception as error:
        logger.info("Playwright 二次抓取失败 product_url=%s error=%s", product_url, error)
        return {
            "source_type": "product_page_playwright",
            "url": product_url,
            "error": str(error)[:500],
            "identity_check": {"accepted": False, "reason": "capture_failed", "score": 0},
        }


async def _fetch_product_url_text(product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    product_url = _normalize_space(product.get("product_url"))
    if not product_url:
        return None

    try:
        async with httpx.AsyncClient(timeout=ENRICHMENT_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(
                product_url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
                    )
                },
            )
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = _normalize_space(soup.title.get_text(" ")) if soup.title else ""
        text = _normalize_space(soup.get_text(" "))
        same, reason, score = _same_product(product, {"product_url": str(response.url), "page_title": title})
        return {
            "source_type": "product_page_http",
            "url": str(response.url),
            "title": title,
            "text": text[:ENRICHMENT_MAX_TEXT_CHARS],
            "identity_check": {"accepted": same, "reason": reason, "score": score},
        }
    except Exception as error:
        logger.info("HTTP 二次抓取失败 product_url=%s error=%s", product_url, error)
        return {
            "source_type": "product_page_http",
            "url": product_url,
            "error": str(error)[:500],
            "identity_check": {"accepted": False, "reason": "fetch_failed", "score": 0},
        }


def _build_secondary_queries(product: Dict[str, Any]) -> List[str]:
    product_name = _normalize_space(product.get("product_name"))
    brand = _normalize_space(product.get("brand"))
    if not product_name:
        return []
    base = f"{brand} {product_name}".strip()
    queries: List[str] = []
    for suffix in SUPPLEMENT_QUERY_SUFFIXES:
        query = f"{base} {suffix}".strip()
        if query not in queries:
            queries.append(query)
    return queries


def _dedupe_result_items(items: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for item in items:
        marker = _normalize_space(item.get("url") or item.get("title") or item.get("description") or item.get("query"))
        if marker and marker in seen:
            continue
        if marker:
            seen.add(marker)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def _coze_exact_supplement(product: Dict[str, Any]) -> Dict[str, Any]:
    if not ENRICHMENT_ENABLE_COZE_EXACT_SEARCH:
        return {"source_type": "coze_exact_supplement", "accepted": [], "rejected": [], "queries": []}
    try:
        from graphs.nodes.coze_search_node import _call_coze_api
    except Exception as error:
        return {"source_type": "coze_exact_supplement", "error": str(error), "accepted": [], "rejected": [], "queries": []}

    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    queries = _build_secondary_queries(product)[: max(0, ENRICHMENT_COZE_QUERY_LIMIT)]
    for query in queries:
        try:
            candidates = _call_coze_api([query], retry_on_cold_start=False)
        except Exception as error:
            rejected.append({"query": query, "reason": f"search_failed: {error}"})
            continue
        if not candidates:
            rejected.append({"query": query, "reason": "no_results_or_timeout"})
            continue
        for candidate in candidates[:5]:
            same, reason, score = _same_product(product, candidate)
            payload = {
                "query": query,
                "reason": reason,
                "score": score,
                "title": candidate.get("product_name") or candidate.get("title") or candidate.get("name"),
                "url": candidate.get("product_url") or candidate.get("url") or candidate.get("link"),
                "description": candidate.get("description") or candidate.get("summary") or candidate.get("product_raw_text"),
            }
            payload["evidence_type"] = _classify_supplement_evidence(payload.get("title"), payload.get("url"), "coze")
            if same:
                accepted.append(payload)
            else:
                rejected.append(payload)
    return {
        "source_type": "coze_exact_supplement",
        "queries": queries,
        "accepted": _dedupe_result_items(accepted, 8),
        "rejected": _dedupe_result_items(rejected, 12),
    }


def _extract_search_items(response_data: Any) -> List[Dict[str, Any]]:
    common_list_keys = ("results", "items", "data", "organic", "videos", "articles")
    if isinstance(response_data, list):
        return [item for item in response_data if isinstance(item, dict)]
    if isinstance(response_data, dict):
        items: List[Dict[str, Any]] = []
        for key in common_list_keys:
            value = response_data.get(key)
            if isinstance(value, list):
                items.extend(item for item in value if isinstance(item, dict))
            elif isinstance(value, dict):
                items.extend(_extract_search_items(value))
        if items:
            seen: set[str] = set()
            unique: List[Dict[str, Any]] = []
            for item in items:
                marker = _normalize_space(item.get("url") or item.get("link") or item.get("title") or item.get("name"))
                if marker and marker in seen:
                    continue
                if marker:
                    seen.add(marker)
                unique.append(item)
            return unique
    return []


async def _generic_search_supplement(product: Dict[str, Any]) -> Dict[str, Any]:
    queries = _build_secondary_queries(product)
    if not SECONDARY_SEARCH_API_URL or not queries:
        return {"source_type": "generic_search_supplement", "queries": queries, "accepted": [], "rejected": [], "enabled": False}

    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=ENRICHMENT_TIMEOUT_SECONDS, follow_redirects=True) as client:
        for query in queries:
            try:
                response = await client.get(SECONDARY_SEARCH_API_URL, params={"q": query})
                response.raise_for_status()
                items = _extract_search_items(response.json())
            except Exception as error:
                rejected.append({"query": query, "reason": f"search_failed: {error}"})
                continue
            if not items:
                rejected.append({"query": query, "reason": "no_results"})
                continue
            for item in items[:6]:
                candidate = {
                    "product_name": item.get("product_name") or item.get("title") or item.get("name"),
                    "product_url": item.get("product_url") or item.get("url") or item.get("link"),
                }
                same, reason, score = _same_product(product, candidate)
                payload = {
                    "query": query,
                    "reason": reason,
                    "score": score,
                    "title": item.get("title") or item.get("product_name") or item.get("name"),
                    "url": item.get("url") or item.get("link") or item.get("product_url"),
                    "description": item.get("description") or item.get("summary") or item.get("snippet") or item.get("content"),
                    "source": item.get("source") or item.get("site") or item.get("platform"),
                }
                payload["evidence_type"] = _classify_supplement_evidence(payload.get("title"), payload.get("url"), payload.get("source"))
                if same:
                    accepted.append(payload)
                else:
                    rejected.append(payload)
    return {
        "source_type": "generic_search_supplement",
        "queries": queries,
        "accepted": _dedupe_result_items(accepted, 8),
        "rejected": _dedupe_result_items(rejected, 12),
        "enabled": True,
    }


def _parse_bing_results(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, Any]] = []
    for node in soup.select("li.b_algo")[:8]:
        link = node.select_one("h2 a")
        if not link:
            continue
        title = _normalize_space(link.get_text(" "))
        url = _normalize_space(link.get("href"))
        snippet_node = node.select_one(".b_caption p") or node.select_one("p")
        snippet = _normalize_space(snippet_node.get_text(" ")) if snippet_node else ""
        if title and url:
            items.append({"title": title, "url": url, "description": snippet, "source": "bing"})
    return items


def _parse_duckduckgo_results(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, Any]] = []
    for node in soup.select(".result")[:8]:
        link = node.select_one(".result__a")
        if not link:
            continue
        title = _normalize_space(link.get_text(" "))
        url = _normalize_space(link.get("href"))
        snippet_node = node.select_one(".result__snippet")
        snippet = _normalize_space(snippet_node.get_text(" ")) if snippet_node else ""
        if title and url:
            items.append({"title": title, "url": url, "description": snippet, "source": "duckduckgo"})
    return items


async def _direct_web_search_supplement(product: Dict[str, Any]) -> Dict[str, Any]:
    queries = _build_secondary_queries(product)[: max(0, ENRICHMENT_DIRECT_WEB_QUERY_LIMIT)]
    if not ENRICHMENT_ENABLE_DIRECT_WEB_SEARCH or not queries:
        return {"source_type": "direct_web_search", "queries": queries, "accepted": [], "rejected": [], "enabled": False}

    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(timeout=ENRICHMENT_TIMEOUT_SECONDS, follow_redirects=True, headers=headers) as client:
        for query in queries:
            items: List[Dict[str, Any]] = []
            try:
                bing = await client.get("https://www.bing.com/search", params={"q": query, "mkt": "zh-CN"})
                if bing.status_code < 400:
                    items.extend(_parse_bing_results(bing.text))
            except Exception as error:
                rejected.append({"query": query, "reason": f"bing_failed: {error}"})
            if not items:
                try:
                    ddg = await client.get("https://duckduckgo.com/html/", params={"q": query})
                    if ddg.status_code < 400:
                        items.extend(_parse_duckduckgo_results(ddg.text))
                except Exception as error:
                    rejected.append({"query": query, "reason": f"duckduckgo_failed: {error}"})
            if not items:
                rejected.append({"query": query, "reason": "no_direct_web_results"})
                continue
            for item in items[:6]:
                candidate = {
                    "product_name": item.get("title"),
                    "product_url": item.get("url"),
                }
                same, reason, score = _same_product(product, candidate)
                payload = {
                    "query": query,
                    "reason": reason,
                    "score": score,
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "description": item.get("description"),
                    "source": item.get("source"),
                }
                payload["evidence_type"] = _classify_supplement_evidence(payload.get("title"), payload.get("url"), payload.get("source"))
                if same:
                    accepted.append(payload)
                else:
                    rejected.append(payload)
    return {
        "source_type": "direct_web_search",
        "queries": queries,
        "accepted": _dedupe_result_items(accepted, 8),
        "rejected": _dedupe_result_items(rejected, 12),
        "enabled": True,
    }


def _extract_accepted_text(enrichment: Dict[str, Any]) -> str:
    pieces: List[str] = []
    for source in enrichment.get("sources", []):
        if not isinstance(source, dict):
            continue
        identity = source.get("identity_check") if isinstance(source.get("identity_check"), dict) else {}
        if source.get("source_type") in {"product_page_playwright", "product_page_http"} and identity.get("accepted"):
            text = _normalize_space(source.get("text"))
            if text:
                pieces.append(f"[{source.get('source_type')}] {text}")
        if source.get("source_type") == "coze_exact_supplement":
            for item in source.get("accepted", []):
                if not isinstance(item, dict):
                    continue
                text = _normalize_space(item.get("description"))
                title = _normalize_space(item.get("title"))
                if text:
                    evidence_type = _normalize_space(item.get("evidence_type")) or "search_result"
                    pieces.append(f"[二次搜索/{evidence_type}:{title}] {text}")
        if source.get("source_type") == "generic_search_supplement":
            for item in source.get("accepted", []):
                if not isinstance(item, dict):
                    continue
                text = _normalize_space(item.get("description"))
                title = _normalize_space(item.get("title"))
                if text:
                    evidence_type = _normalize_space(item.get("evidence_type")) or "search_result"
                    pieces.append(f"[外部搜索/{evidence_type}:{title}] {text}")
        if source.get("source_type") == "direct_web_search":
            for item in source.get("accepted", []):
                if not isinstance(item, dict):
                    continue
                text = _normalize_space(item.get("description"))
                title = _normalize_space(item.get("title"))
                if text:
                    evidence_type = _normalize_space(item.get("evidence_type")) or "search_result"
                    pieces.append(f"[网页搜索/{evidence_type}:{title}] {text}")
        if source.get("source_type") == "first_search_image_vision" and source.get("accepted"):
            text = _normalize_space(source.get("text"))
            if text:
                pieces.append(f"[商品图片视觉读取] {text}")
        if source.get("source_type") == "first_search_image_ocr" and source.get("accepted"):
            text = _normalize_space(source.get("text"))
            if text:
                pieces.append(f"[商品图片OCR] {text}")
    joined = "\n\n".join(dict.fromkeys(pieces))
    return joined[:ENRICHMENT_MAX_TEXT_CHARS]


def _source_has_accepted_text(source: Dict[str, Any]) -> bool:
    if source.get("source_type") in {"product_page_playwright", "product_page_http"}:
        identity = source.get("identity_check") if isinstance(source.get("identity_check"), dict) else {}
        return bool(identity.get("accepted")) and bool(_normalize_space(source.get("text")))
    if source.get("source_type") == "coze_exact_supplement":
        return any(
            isinstance(item, dict) and bool(_normalize_space(item.get("description")))
            for item in source.get("accepted", [])
        )
    if source.get("source_type") == "generic_search_supplement":
        return any(
            isinstance(item, dict) and bool(_normalize_space(item.get("description")))
            for item in source.get("accepted", [])
        )
    if source.get("source_type") == "direct_web_search":
        return any(
            isinstance(item, dict) and bool(_normalize_space(item.get("description")))
            for item in source.get("accepted", [])
        )
    if source.get("source_type") == "first_search_image_vision":
        return bool(source.get("accepted")) and bool(_normalize_space(source.get("text")))
    if source.get("source_type") == "first_search_image_ocr":
        return bool(source.get("accepted")) and bool(_normalize_space(source.get("text")))
    return False


async def _enrich_one(product: Dict[str, Any]) -> Dict[str, Any]:
    sources: List[Dict[str, Any]] = []
    image_ocr = await asyncio.to_thread(_image_ocr_supplement, product)
    sources.append(image_ocr)
    image_vision = await asyncio.to_thread(_image_vision_supplement, product)
    sources.append(image_vision)
    capture = await _capture_product_url(product)
    if capture:
        sources.append(capture)
    http_text = await _fetch_product_url_text(product)
    if http_text:
        sources.append(http_text)
    direct_web = await _direct_web_search_supplement(product)
    sources.append(direct_web)
    generic_search = await _generic_search_supplement(product)
    sources.append(generic_search)
    coze_supplement = await asyncio.to_thread(_coze_exact_supplement, product)
    sources.append(coze_supplement)

    enrichment = {
        "version": 1,
        "enriched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "sources": sources,
    }
    enrichment["supplement_text"] = _extract_accepted_text(enrichment)
    enrichment["accepted_sources_count"] = sum(1 for source in sources if isinstance(source, dict) and _source_has_accepted_text(source))
    enrichment["rejected_sources_count"] = sum(
        1
        for source in sources
        if (
            isinstance(source, dict)
            and (
                (isinstance(source.get("identity_check"), dict) and not source["identity_check"].get("accepted"))
                or (isinstance(source.get("rejected"), list) and len(source.get("rejected", [])) > 0)
                or bool(source.get("error"))
            )
        )
    )
    return enrichment


async def _enrich_many(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    async def guarded(product: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return await asyncio.wait_for(_enrich_one(product), timeout=ENRICHMENT_PRODUCT_TIMEOUT_SECONDS)
        except Exception as error:
            return {
                "version": 1,
                "enriched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "sources": [
                    {
                        "source_type": "secondary_enrichment",
                        "error": f"product_timeout_or_failure: {str(error)[:500]}",
                    }
                ],
                "supplement_text": "",
                "accepted_sources_count": 0,
                "rejected_sources_count": 1,
            }

    return await asyncio.gather(*[guarded(product) for product in products])


def _merge_product_with_enrichment(product: Dict[str, Any], enrichment: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(product)
    raw_payload = product.get("raw_payload") if isinstance(product.get("raw_payload"), dict) else dict(product)
    raw_payload = dict(raw_payload)
    existing_enrichment = raw_payload.get("secondary_enrichment") if isinstance(raw_payload.get("secondary_enrichment"), dict) else {}
    if (
        isinstance(existing_enrichment, dict)
        and int(existing_enrichment.get("accepted_sources_count") or 0) > 0
        and int(enrichment.get("accepted_sources_count") or 0) <= 0
    ):
        enrichment = existing_enrichment

    existing_description = _normalize_space(product.get("description"))
    supplement_text = _normalize_space(enrichment.get("supplement_text"))
    if supplement_text and supplement_text not in existing_description:
        merged["description"] = f"{existing_description}\n\n【二次检索补充资料】\n{supplement_text}".strip()
    raw_payload["secondary_enrichment"] = enrichment
    merged["raw_payload"] = raw_payload
    return merged


def _update_search_products(
    *,
    patent_record_id: int,
    analysis_session_id: str,
    enriched_products: List[Dict[str, Any]],
) -> Tuple[int, int]:
    from storage.database.db import get_session
    from storage.database.shared.model import SearchProduct

    updated = 0
    accepted = 0
    session = get_session()
    try:
        for product in enriched_products:
            product_url = _normalize_space(product.get("product_url"))
            product_id = _normalize_space(product.get("product_id"))
            product_name = _normalize_space(product.get("product_name"))
            query = session.query(SearchProduct).filter(
                SearchProduct.patent_record_id == patent_record_id,
                SearchProduct.analysis_session_id == (analysis_session_id or None),
            )
            row = None
            if product_url:
                row = query.filter(SearchProduct.product_url == product_url).order_by(SearchProduct.id.asc()).first()
            if row is None and product_id:
                row = query.filter(SearchProduct.product_id == product_id).order_by(SearchProduct.id.asc()).first()
            if row is None and product_name:
                row = query.filter(SearchProduct.product_name == product_name).order_by(SearchProduct.id.asc()).first()
            if row is None:
                continue

            row.description = product.get("description") or row.description
            if isinstance(product.get("raw_payload"), dict):
                row.raw_payload = product.get("raw_payload")
            enrichment = (product.get("raw_payload") or {}).get("secondary_enrichment") if isinstance(product.get("raw_payload"), dict) else {}
            if isinstance(enrichment, dict) and int(enrichment.get("accepted_sources_count") or 0) > 0:
                accepted += 1
            updated += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return updated, accepted


def secondary_enrichment_node(
    state: SecondaryEnrichmentInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> SecondaryEnrichmentOutput:
    """
    title: 二次商品信息补全
    desc: 在第一次商品检索后，用商品页抓取和精确二次搜索补充同一商品的信息
    """
    if not ENRICHMENT_ENABLED:
        return SecondaryEnrichmentOutput(
            products=state.products,
            enriched_products_count=0,
            enrichment_error_message="",
        )

    products = list(state.products or [])
    if not products:
        return SecondaryEnrichmentOutput(
            products=[],
            enriched_products_count=0,
            enrichment_error_message="",
        )

    selected = products[: max(0, ENRICHMENT_MAX_PRODUCTS)]
    untouched = products[len(selected):]
    try:
        enriched_selected = asyncio.run(_enrich_many(selected))
        merged_products = [
            _merge_product_with_enrichment(product, enrichment)
            for product, enrichment in zip(selected, enriched_selected)
        ] + untouched
        updated_count, accepted_count = _update_search_products(
            patent_record_id=state.patent_record_id,
            analysis_session_id=state.analysis_session_id,
            enriched_products=merged_products,
        )
        logger.info(
            "二次检索补全完成 patent_record_id=%s session=%s updated=%s accepted=%s",
            state.patent_record_id,
            state.analysis_session_id,
            updated_count,
            accepted_count,
        )
        return SecondaryEnrichmentOutput(
            products=merged_products,
            enriched_products_count=accepted_count,
            enrichment_error_message="",
        )
    except Exception as error:
        logger.error("二次检索补全失败: %s", error, exc_info=True)
        return SecondaryEnrichmentOutput(
            products=products,
            enriched_products_count=0,
            enrichment_error_message=f"二次检索补全失败: {error}",
        )
