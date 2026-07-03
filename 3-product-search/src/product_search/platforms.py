from __future__ import annotations

import re
import base64
from dataclasses import dataclass
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse, urlunparse

from product_search.models import CandidateLink, SearchQueryPlan


@dataclass(frozen=True)
class PlatformRule:
    key: str
    display_name: str
    domains: tuple[str, ...]
    detail_patterns: tuple[re.Pattern[str], ...]
    aggregate_patterns: tuple[re.Pattern[str], ...]
    query_template: str


def _compile_many(patterns: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(pattern, re.I) for pattern in patterns)


PLATFORM_RULES: dict[str, PlatformRule] = {
    "1688": PlatformRule(
        key="1688",
        display_name="1688",
        domains=("1688.com",),
        detail_patterns=_compile_many([r"detail\.1688\.com/offer/\d+\.html"]),
        aggregate_patterns=_compile_many([r"search\.1688\.com", r"/offer_search", r"/chanpin", r"/market/", r"/shop/"]),
        query_template="site:detail.1688.com/offer {keyword}",
    ),
    "jd": PlatformRule(
        key="jd",
        display_name="京东",
        domains=("jd.com",),
        detail_patterns=_compile_many([r"item\.jd\.com/\d+\.html", r"item\.m\.jd\.com/product/\d+\.html"]),
        aggregate_patterns=_compile_many([
            r"search\.jd\.com",
            r"list\.jd\.com",
            r"mall\.jd\.com",
            r"/view_search",
            r"www\.jd\.com/(?:brand|hprm|phb|chanpin|hotitem)/",
        ]),
        query_template="site:item.jd.com {keyword}",
    ),
    "taobao": PlatformRule(
        key="taobao",
        display_name="淘宝",
        domains=("taobao.com",),
        detail_patterns=_compile_many([r"item\.taobao\.com/item\.htm\?", r"id=\d+"]),
        aggregate_patterns=_compile_many([r"s\.taobao\.com", r"re\.taobao\.com", r"world\.taobao\.com/search", r"/search"]),
        query_template="site:item.taobao.com/item.htm {keyword}",
    ),
    "tmall": PlatformRule(
        key="tmall",
        display_name="天猫",
        domains=("tmall.com",),
        detail_patterns=_compile_many([r"detail\.tmall\.com/item\.htm\?", r"id=\d+"]),
        aggregate_patterns=_compile_many([r"list\.tmall\.com", r"pages\.tmall\.com", r"/search"]),
        query_template="site:detail.tmall.com/item.htm {keyword}",
    ),
    "pdd": PlatformRule(
        key="pdd",
        display_name="拼多多",
        domains=("yangkeduo.com", "pinduoduo.com"),
        detail_patterns=_compile_many([r"mobile\.yangkeduo\.com/goods\.html\?", r"goods_id=\d+"]),
        aggregate_patterns=_compile_many([r"/search", r"/mall_page", r"yangkeduo\.com/catgoods"]),
        query_template="site:mobile.yangkeduo.com/goods.html {keyword}",
    ),
    "suning": PlatformRule(
        key="suning",
        display_name="苏宁",
        domains=("suning.com",),
        detail_patterns=_compile_many([r"product\.suning\.com/.+\.html"]),
        aggregate_patterns=_compile_many([r"search\.suning\.com", r"list\.suning\.com", r"/emall/"]),
        query_template="site:product.suning.com {keyword}",
    ),
}


def unwrap_search_redirect(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        query = parse_qs(parsed.query)
        target = query.get("q") or query.get("url")
        if target:
            return target[0]
    if "duckduckgo" in parsed.netloc and parsed.path.startswith("/l/"):
        query = parse_qs(parsed.query)
        target = query.get("uddg")
        if target:
            return unquote(target[0])
    if "bing.com" in parsed.netloc and parsed.path.startswith("/ck/"):
        query = parse_qs(parsed.query)
        target = (query.get("u") or [""])[0]
        if target.startswith("a1"):
            encoded = target[2:]
            padding = "=" * (-len(encoded) % 4)
            try:
                return base64.urlsafe_b64decode((encoded + padding).encode("ascii")).decode("utf-8")
            except Exception:
                return url
        if target:
            return unquote(target)
    return url


def normalize_url(url: str, base_url: str = "") -> str:
    url = (url or "").strip()
    if not url or url.startswith(("#", "javascript:", "mailto:", "tel:")):
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("/") and not base_url:
        return ""
    if base_url:
        url = urljoin(base_url, url)
    url = unwrap_search_redirect(url)
    parsed = urlparse(url)
    if not parsed.scheme:
        parsed = urlparse("https://" + url.lstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", parsed.query, ""))


def canonicalize_product_url(url: str) -> str:
    normalized = normalize_url(url)
    parsed = urlparse(normalized)
    query = parse_qs(parsed.query)
    keep_params: list[tuple[str, str]] = []
    for key in ("id", "goods_id", "skuId"):
        if key in query and query[key]:
            keep_params.append((key, query[key][0]))
    query_string = "&".join(f"{key}={value}" for key, value in keep_params)
    return urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", query_string, ""))


def detect_platform(url: str) -> str:
    parsed = urlparse(normalize_url(url))
    host = parsed.netloc.lower()
    for key, rule in PLATFORM_RULES.items():
        if any(domain in host for domain in rule.domains):
            return key
    return ""


def is_aggregate_url(url: str) -> bool:
    normalized = normalize_url(url)
    platform = detect_platform(normalized)
    rule = PLATFORM_RULES.get(platform)
    if not rule:
        return True
    return any(pattern.search(normalized) for pattern in rule.aggregate_patterns)


def is_detail_url(url: str, platform: str = "") -> bool:
    normalized = normalize_url(url)
    platform = platform or detect_platform(normalized)
    rule = PLATFORM_RULES.get(platform)
    if not rule or is_aggregate_url(normalized):
        return False
    return any(pattern.search(normalized) for pattern in rule.detail_patterns)


def extract_product_id(url: str, platform: str = "") -> str:
    normalized = normalize_url(url)
    platform = platform or detect_platform(normalized)
    parsed = urlparse(normalized)
    if platform in {"taobao", "tmall"}:
        return (parse_qs(parsed.query).get("id") or [""])[0]
    if platform == "pdd":
        return (parse_qs(parsed.query).get("goods_id") or [""])[0]
    if platform == "jd":
        match = re.search(r"/(\d+)\.html", parsed.path)
        return match.group(1) if match else ""
    if platform == "1688":
        match = re.search(r"/offer/(\d+)\.html", parsed.path)
        return match.group(1) if match else ""
    if platform == "suning":
        match = re.search(r"/(\d+)\.html", parsed.path)
        return match.group(1) if match else ""
    return ""


def build_search_query_plans(keywords: list[str], platforms: list[str], max_keywords: int) -> list[SearchQueryPlan]:
    normalized_keywords: list[str] = []
    seen_keywords: set[str] = set()
    for keyword in keywords:
        cleaned = " ".join(str(keyword or "").split())
        if cleaned and cleaned not in seen_keywords:
            normalized_keywords.append(cleaned)
            seen_keywords.add(cleaned)
        if len(normalized_keywords) >= max_keywords:
            break

    plans: list[SearchQueryPlan] = []
    for original_keyword in normalized_keywords:
        for keyword in expand_ecommerce_keyword_variants(original_keyword):
            if not keyword:
                continue
            for platform in platforms:
                rule = PLATFORM_RULES.get(platform)
                if not rule:
                    continue
                query = rule.query_template.format(keyword=keyword)
                serp_url = "https://www.google.com/search?" f"q={quote_plus(query)}&hl=zh-CN&gl=cn&num=10&brd_json=1"
                plans.append(SearchQueryPlan(keyword=keyword, platform=platform, query=query, serp_url=serp_url))
    return plans


def expand_ecommerce_keyword_variants(keyword: str) -> list[str]:
    keyword = " ".join(str(keyword or "").split())
    variants = [keyword] if keyword else []
    replacements = [
        ("蓝牙音响", "蓝牙音箱"),
        ("音响", "音箱"),
        ("健身脚踏车", "健身车"),
        ("健身脚踏车", "动感单车"),
        ("脚踏车", "健身车"),
        ("多边形计时器", "多边形定时器"),
        ("计时器", "定时器"),
        ("灯具装置", "灯具"),
        ("便携式音响设备", "便携蓝牙音箱"),
    ]
    for source, target in replacements:
        if source in keyword:
            variants.append(keyword.replace(source, target))
    if ("颈挂" in keyword or "颈戴" in keyword) and ("音响" in keyword or "音箱" in keyword):
        variants.append(keyword.replace("音响", "耳机").replace("音箱", "耳机"))
        variants.append(keyword.replace("蓝牙音响", "蓝牙耳机").replace("蓝牙音箱", "蓝牙耳机"))
    result: list[str] = []
    seen: set[str] = set()
    for variant in variants:
        cleaned = " ".join(variant.split())
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result[:3]


def dedupe_candidate_links(candidates: list[CandidateLink]) -> list[CandidateLink]:
    result: list[CandidateLink] = []
    seen: set[str] = set()
    for candidate in candidates:
        canonical = canonicalize_product_url(candidate.candidate_url)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        result.append(candidate.model_copy(update={"candidate_url": canonical}))
    return result
