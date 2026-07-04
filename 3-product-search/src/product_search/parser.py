from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from product_search.models import ParsedProductPage
from product_search.platforms import detect_platform, extract_product_id, normalize_url


PRICE_RE = re.compile(r"(?:¥|￥|价格|售价|到手价|活动价|批发价)\s*[:：]?\s*([0-9]+(?:\.[0-9]{1,2})?)")
SALES_RE = re.compile(r"(月销|销量|已售|成交|累计售出|付款人数)\s*[:：]?\s*([0-9万+\.]+)")
BRAND_RE = re.compile(r"(?:品牌|Brand)\s*[:：]\s*([^\s｜|,，;；]{2,40})")
MANUFACTURER_RE = re.compile(r"(?:生产厂家|制造商|厂家|厂商|公司名称|供应商|商家)\s*[:：]\s*([^\n\r｜|,，;；]{2,80})")
GENERIC_TITLE_WORDS = {
    "支持",
    "京东",
    "商品详情",
    "商品介绍",
    "登录",
    "注册",
    "购物车",
    "立即购买",
}


def _normalize_text(text: str, limit: int = 12000) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    return cleaned[:limit]


def _meta_content(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        selector = f'meta[property="{name}"], meta[name="{name}"]'
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            return _normalize_text(str(tag.get("content")), 1000)
    return ""


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            text = _normalize_text(tag.get_text(" ", strip=True), 1000)
            if text:
                return text
    return ""


def _extract_title(soup: BeautifulSoup) -> str:
    candidates = [
        _meta_content(soup, "og:title", "twitter:title"),
        _first_text(soup, ["h1", ".title", ".product-title", ".sku-name", "#J_Title", ".tb-main-title"]),
        _normalize_text(soup.title.get_text(" ", strip=True) if soup.title else "", 1000),
    ]
    for candidate in candidates:
        candidate = re.sub(r"[-_]\s*(淘宝|天猫|京东|1688|阿里巴巴).*$", "", candidate).strip()
        if _is_valid_product_title(candidate):
            return candidate[:500]
    return ""


def _is_valid_product_title(value: str) -> bool:
    value = _clean_product_title(value)
    if len(value) < 6:
        return False
    if value in GENERIC_TITLE_WORDS:
        return False
    if any(value.startswith(prefix) for prefix in ("京东首页", "你好，请登录", "全部分类", "我的购物车")):
        return False
    if "京东" in value and "正品低价" in value:
        return False
    return True


def _clean_product_title(value: str) -> str:
    value = _normalize_text(value, 500)
    value = re.sub(r"【?图片\s*价格\s*品牌\s*报价】?", "", value)
    value = re.sub(r"【.*?京东.*?】", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -_|")
    return value


def _infer_title_from_text(text: str) -> str:
    candidates: list[str] = []
    patterns = [
        r"([^\s。！？]{8,140}?)图片、价格、品牌",
        r"登录查看更多图片\s*>\s*([^\s。！？]{8,140}?)(?:\s+单品购买|\s+京\s*东\s*价|\s+图片)",
        r"(?:运动户外|家用电器|电脑办公|医疗器械|玩具乐器|汽车用品)\s*>\s*[^>]{1,30}\s*>\s*[^>]{1,30}\s*>\s*[^>]{1,30}\s*>\s*([^>]{8,140}?)(?:\s+单品购买|\s+京\s*东\s*价)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            value = _clean_product_title(match.group(1))
            value = re.sub(r"^>\s*", "", value).strip()
            if _is_valid_product_title(value):
                candidates.append(value)
    if not candidates:
        return ""
    candidates.sort(key=len, reverse=True)
    return candidates[0][:500]


def _extract_images(soup: BeautifulSoup, final_url: str, limit: int = 18) -> list[str]:
    images: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        normalized = normalize_url(raw, final_url)
        if not normalized or normalized in seen:
            return
        lower = normalized.lower()
        parsed = urlparse(lower)
        image_hosts = (
            "alicdn.com",
            "360buyimg.com",
            "jd.com",
            "yangkeduo.com",
            "suning.cn",
            "91app.com",
            "books.com.tw",
            "momoshop.com.tw",
            "shoplineimg.com",
            "cloudfront.net",
            "ssl-images-amazon.com",
            "media-amazon.com",
            "nosdn.127.net",
            "mi-img.com",
        )
        image_path_tokens = ("/webapi/images", "/images/", "/image/", "/upload/", "/uploads/", "/product/")
        image_like = bool(re.search(r"\.(?:jpg|jpeg|png|webp|gif)(?:$|\?)", parsed.path)) or any(
            host in parsed.netloc for host in image_hosts
        ) or any(token in parsed.path for token in image_path_tokens)
        if (
            lower.startswith("data:")
            or any(token in lower for token in ("sprite", "icon", "logo", "avatar", "jcm.jd.com/pre"))
            or not image_like
        ):
            return
        seen.add(normalized)
        images.append(normalized)

    for value in (_meta_content(soup, "og:image", "twitter:image"),):
        if value:
            add(value)
    for img in soup.select("img"):
        for attr in ("data-original", "_src", "data-src", "data-lazyload", "data-ks-lazyload", "data-img", "ng-src", "src"):
            raw = img.get(attr)
            if raw:
                add(str(raw))
                if images and images[-1] == normalize_url(str(raw), final_url):
                    break
        if len(images) >= limit:
            break
    return images[:limit]


def _extract_detail_text(soup: BeautifulSoup) -> str:
    selectors = [
        "#description",
        "#J_Detail",
        "#J-detail-content",
        ".detail-content",
        ".product-detail",
        ".desc",
        ".description",
        ".offer-detail",
        ".mod-detail",
        ".p-parameter",
        ".parameter",
        ".attributes",
        ".obj-content",
    ]
    blocks: list[str] = []
    for selector in selectors:
        for tag in soup.select(selector):
            text = _normalize_text(tag.get_text(" ", strip=True), 6000)
            if text and text not in blocks:
                blocks.append(text)
    if blocks:
        return _normalize_text(" ".join(blocks), 12000)
    body = soup.body or soup
    return _normalize_text(body.get_text(" ", strip=True), 12000)


def _regex_first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    if len(match.groups()) >= 2:
        return " ".join(group for group in match.groups() if group)
    return match.group(1)


def _extract_price(text: str) -> str:
    exact_patterns = [
        r"(?:京\s*东\s*价|到手价|售价|批发价|活动价)\s*[:：]?\s*(?:¥|￥)?\s*([1-9][0-9]*(?:\.[0-9]{1,2})?)",
        r"(?:¥|￥)\s*([1-9][0-9]*(?:\.[0-9]{1,2})?)",
    ]
    for pattern in exact_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return ""


def parse_product_page(html: str, requested_url: str, final_url: str) -> ParsedProductPage:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    final_url = normalize_url(final_url or requested_url)
    platform = detect_platform(final_url) or detect_platform(requested_url)
    title = _extract_title(soup)
    meta_description = _meta_content(soup, "description", "og:description")
    detail_text = _extract_detail_text(soup)
    combined_text = _normalize_text(" ".join(part for part in (meta_description, detail_text) if part), 15000)
    if not _is_valid_product_title(title):
        title = _infer_title_from_text(combined_text) or title
    title = _clean_product_title(title)
    images = _extract_images(soup, final_url)
    raw_payload: dict[str, Any] = {
        "requested_url": requested_url,
        "final_url": final_url,
        "meta_description": meta_description,
        "text_length": len(combined_text),
        "image_count": len(images),
    }

    return ParsedProductPage(
        product_id=extract_product_id(final_url, platform),
        platform=platform,
        product_name=title,
        product_url=final_url,
        final_url=final_url,
        price=_extract_price(combined_text),
        sales=_regex_first(SALES_RE, combined_text),
        brand=_regex_first(BRAND_RE, combined_text),
        manufacturer=_regex_first(MANUFACTURER_RE, combined_text),
        description=combined_text,
        detail_text=detail_text,
        picture=images,
        raw_payload=raw_payload,
    )
