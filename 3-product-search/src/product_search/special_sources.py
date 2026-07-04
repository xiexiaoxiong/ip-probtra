from __future__ import annotations

import html
import re
from typing import Any

import httpx

from product_search.models import FetchResult
from product_search.platforms import normalize_url


XIAOMI_CROWDFUNDING_RE = re.compile(r"https?://m\.mi\.com/crowdfunding/proddetail/(\d+)", re.I)


def match_xiaomi_crowdfunding_project_id(url: str) -> str:
    match = XIAOMI_CROWDFUNDING_RE.search(normalize_url(url))
    return match.group(1) if match else ""


async def fetch_special_product_page(url: str, timeout_seconds: int, user_agent: str) -> FetchResult | None:
    project_id = match_xiaomi_crowdfunding_project_id(url)
    if not project_id:
        return None

    normalized_url = normalize_url(url)
    headers = {
        "Referer": normalized_url,
        "User-Agent": user_agent,
        "X-User-Agent": "channel/mishop platform/mishop.m",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = {
        "project_id": project_id,
        "client_id": "180100031051",
        "channel_id": "",
        "webp": "1",
    }
    try:
        timeout = httpx.Timeout(timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post("https://m.mi.com/v1/crowd/crowd_detail", headers=headers, data=payload)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return FetchResult(
            ok=False,
            url=normalized_url,
            final_url=normalized_url,
            provider="xiaomi_crowdfunding_api",
            error_message=f"小米众筹接口抓取失败: {str(exc)[:500]}",
        )

    if not isinstance(data, dict) or data.get("code") != 0 or not isinstance(data.get("data"), dict):
        return FetchResult(
            ok=False,
            url=normalized_url,
            final_url=normalized_url,
            status_code=response.status_code,
            provider="xiaomi_crowdfunding_api",
            error_message=f"小米众筹接口返回异常: {data.get('description') if isinstance(data, dict) else 'invalid payload'}",
        )

    html_text = render_xiaomi_crowdfunding_html(data["data"], normalized_url)
    return FetchResult(
        ok=bool(html_text.strip()),
        url=normalized_url,
        final_url=normalized_url,
        status_code=response.status_code,
        html=html_text,
        provider="xiaomi_crowdfunding_api",
    )


def render_xiaomi_crowdfunding_html(data: dict[str, Any], source_url: str) -> str:
    info = data.get("crowd_funding_info") if isinstance(data.get("crowd_funding_info"), dict) else {}
    name = str(info.get("project_name") or _first_text(data, "project_name", "goods_name", "title") or "").strip()
    description = _first_text(info, "project_desc", "support_desc", "desc")
    price = str(info.get("price") or "").strip()
    manufacturer = _nested_value(info, ("company_info", "company_name")) or _first_text(info, "company_name")
    support_num = _nested_value(info, ("support_num", "v")) or ""
    image_urls = _collect_image_urls(data)
    text_parts = _collect_text_parts(data)

    if name and name not in text_parts:
        text_parts.insert(0, name)
    if description and description not in text_parts:
        text_parts.insert(1, description)
    if price:
        text_parts.append(f"售价：{price}")
    if support_num:
        text_parts.append(f"支持人数：{support_num}")
    if manufacturer:
        text_parts.append(f"制造商：{manufacturer}")

    body_text = " ".join(part for part in text_parts if part)
    escaped_name = html.escape(name or "小米众筹商品")
    escaped_description = html.escape(description or body_text[:300])
    escaped_body = html.escape(body_text)
    image_tags = "\n".join(f'<img src="{html.escape(url)}" />' for url in image_urls)
    meta_image = f'<meta property="og:image" content="{html.escape(image_urls[0])}" />' if image_urls else ""

    return f"""
    <html>
      <head>
        <title>{escaped_name}</title>
        <meta name="description" content="{escaped_description}" />
        {meta_image}
      </head>
      <body>
        <h1>{escaped_name}</h1>
        <div class="product-detail">
          {escaped_body}
        </div>
        {image_tags}
      </body>
    </html>
    """


def _collect_text_parts(value: Any) -> list[str]:
    parts: list[str] = []
    seen: set[str] = set()
    text_keys = {
        "project_name",
        "project_desc",
        "support_desc",
        "goods_name",
        "name",
        "desc",
        "company_name",
        "send_info",
        "buy_notice",
        "title",
        "v",
    }

    def add(raw: str) -> None:
        cleaned = re.sub(r"\s+", " ", raw or "").strip()
        if not cleaned or cleaned in seen:
            return
        if cleaned.startswith(("http://", "https://", "/pages/")):
            return
        if len(cleaned) > 600:
            cleaned = cleaned[:600]
        if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", cleaned):
            return
        seen.add(cleaned)
        parts.append(cleaned)

    def walk(item: Any, key: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child_value in item.items():
                walk(child_value, str(child_key))
        elif isinstance(item, list):
            for child in item:
                walk(child, key)
        elif isinstance(item, str) and key in text_keys:
            add(item)
        elif isinstance(item, (int, float)) and key in {"price", "support_num", "supply_num", "goods_num"}:
            add(str(item))

    walk(value)
    return parts[:80]


def _collect_image_urls(value: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        normalized = normalize_url(raw)
        if not normalized or normalized in seen:
            return
        if not re.search(r"(?:mi-img\.com|mifile\.cn|\.jpg|\.jpeg|\.png|\.webp)", normalized, re.I):
            return
        seen.add(normalized)
        urls.append(normalized)

    def walk(item: Any, key: str = "") -> None:
        if isinstance(item, dict):
            for child_key, child_value in item.items():
                walk(child_value, str(child_key))
        elif isinstance(item, list):
            for child in item:
                walk(child, key)
        elif isinstance(item, str) and ("img" in key or "image" in key or "gallery" in key):
            add(item)

    walk(value)
    return urls[:30]


def _first_text(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        text = value.get(key)
        if isinstance(text, str) and text.strip():
            return re.sub(r"\s+", " ", text).strip()
    return ""


def _nested_value(value: dict[str, Any], path: tuple[str, ...]) -> str:
    current: Any = value
    for key in path:
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
    return str(current).strip() if current is not None else ""
