from __future__ import annotations

import re

from product_search.models import FetchResult, ParsedProductPage, QualityDecision
from product_search.platforms import detect_platform, is_aggregate_url, is_detail_url


BLOCKED_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        "验证码",
        "安全验证",
        "滑块",
        "请完成验证",
        "访问受限",
        "登录后查看",
        "risk_handler",
        "privatedomain",
        "returnurl=",
        "cfe.m.jd.com",
        "sign in",
        "captcha",
        "security check",
        "verify you are human",
        "robot check",
    )
]

LIST_PAGE_HINTS = [
    re.compile(pattern, re.I)
    for pattern in (
        "搜索结果",
        "为您找到",
        "相关商品",
        "综合排序",
        "筛选",
        "店铺首页",
        "全部商品",
        "商品分类",
    )
]


def is_blocked_page(fetch: FetchResult, parsed: ParsedProductPage | None = None) -> bool:
    haystack = " ".join(
        part
        for part in (
            fetch.final_url,
            fetch.error_message,
            fetch.html[:3000],
            parsed.product_name if parsed else "",
            parsed.description[:3000] if parsed else "",
        )
        if part
    )
    return any(pattern.search(haystack) for pattern in BLOCKED_PATTERNS)


def evaluate_product_detail(fetch: FetchResult, parsed: ParsedProductPage) -> QualityDecision:
    reasons: list[str] = []
    flags: dict[str, object] = {
        "provider": fetch.provider,
        "platform": parsed.platform or detect_platform(fetch.final_url),
        "same_url_assets": parsed.final_url == fetch.final_url or bool(parsed.final_url and fetch.final_url),
        "source_text_url": fetch.final_url,
        "source_image_url": fetch.final_url,
        "is_detail_url": is_detail_url(fetch.final_url, parsed.platform),
        "is_aggregate_url": is_aggregate_url(fetch.final_url),
        "blocked": False,
    }

    if not fetch.ok:
        return QualityDecision(
            accepted=False,
            score=0,
            status="error",
            reasons=[fetch.error_message or "详情页抓取失败"],
            flags={**flags, "blocked": is_blocked_page(fetch, parsed)},
        )

    if is_aggregate_url(fetch.final_url):
        reasons.append("候选 URL 是搜索页/列表页/店铺页，不是真实商品详情页")

    if not is_detail_url(fetch.final_url, parsed.platform):
        reasons.append("URL 未命中平台商品详情页模式")

    if is_blocked_page(fetch, parsed):
        reasons.append("页面疑似登录、验证码或安全验证拦截")
        flags["blocked"] = True

    if not parsed.product_name:
        reasons.append("未提取到商品标题")
    if len(parsed.description) < 120:
        reasons.append("商品详情文本过短")
    if not parsed.picture:
        reasons.append("未提取到商品图片")

    list_hint_count = sum(1 for pattern in LIST_PAGE_HINTS if pattern.search(parsed.description[:5000]))
    flags["list_hint_count"] = list_hint_count
    if list_hint_count >= 3 and not is_detail_url(fetch.final_url, parsed.platform):
        reasons.append("正文更像商品列表或搜索结果聚合页")

    score = 0
    if is_detail_url(fetch.final_url, parsed.platform):
        score += 35
    if parsed.product_name:
        score += 15
    if len(parsed.description) >= 120:
        score += 15
    if len(parsed.description) >= 500:
        score += 10
    if parsed.picture:
        score += 10
    if parsed.price:
        score += 5
    if parsed.sales:
        score += 5
    if parsed.manufacturer or parsed.brand:
        score += 5

    accepted = score >= 65 and not reasons
    if not accepted and not reasons:
        reasons.append("详情页有效信息不足")

    return QualityDecision(
        accepted=accepted,
        score=score,
        status="accepted" if accepted else "rejected",
        reasons=reasons,
        flags=flags,
    )
