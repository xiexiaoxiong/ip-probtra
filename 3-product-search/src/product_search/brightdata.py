from __future__ import annotations

import json
import re
import asyncio
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from product_search.config import Settings
from product_search.models import CandidateLink, FetchResult, SearchQueryPlan
from product_search.platforms import normalize_url


def _json_or_text(response: httpx.Response) -> Any:
    content_type = response.headers.get("content-type", "")
    text = response.text
    if "json" in content_type:
        return response.json()
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return text
    return text


def _extract_serp_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("organic", "results", "items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    nested: list[dict[str, Any]] = []
    for value in payload.values():
        if isinstance(value, dict):
            nested.extend(_extract_serp_items(value))
        elif isinstance(value, list):
            nested.extend(item for item in value if isinstance(item, dict))
    return nested


def parse_serp_payload(payload: Any, plan: SearchQueryPlan, limit: int) -> list[CandidateLink]:
    items = _extract_serp_items(payload)
    candidates: list[CandidateLink] = []
    for index, item in enumerate(items, start=1):
        url = item.get("link") or item.get("url") or item.get("href") or item.get("target_url") or ""
        normalized = normalize_url(str(url))
        if not normalized:
            continue
        candidates.append(
            CandidateLink(
                keyword=plan.keyword,
                platform=plan.platform,
                candidate_url=normalized,
                title=str(item.get("title") or item.get("name") or "")[:500],
                snippet=str(item.get("description") or item.get("snippet") or item.get("text") or "")[:1000],
                source="brightdata_serp",
                rank=int(item.get("position") or item.get("rank") or index),
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def parse_html_search_results(html: str, plan: SearchQueryPlan, limit: int, source: str) -> list[CandidateLink]:
    soup = BeautifulSoup(html or "", "lxml")
    candidates: list[CandidateLink] = []
    for anchor in soup.select("a[href]"):
        href = normalize_url(anchor.get("href") or "")
        if not href or "google." in href or "bing.com" in href:
            continue
        text = " ".join(anchor.get_text(" ", strip=True).split())
        if len(text) < 2:
            continue
        candidates.append(
            CandidateLink(
                keyword=plan.keyword,
                platform=plan.platform,
                candidate_url=href,
                title=text[:500],
                snippet="",
                source=source,
                rank=len(candidates) + 1,
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


class BrightDataClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._zone_lock = asyncio.Lock()
        self._discovered_zones: dict[str, str] | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.brightdata_api_key}",
            "Content-Type": "application/json",
        }

    async def _request_brightdata(self, payload: dict[str, Any]) -> Any:
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                self.settings.brightdata_endpoint,
                headers=self._headers(),
                json=payload,
            )
            response.raise_for_status()
            return _json_or_text(response)

    async def _discover_zones(self) -> dict[str, str]:
        if not self.settings.brightdata_api_key:
            return {}
        if self._discovered_zones is not None:
            return self._discovered_zones
        async with self._zone_lock:
            if self._discovered_zones is not None:
                return self._discovered_zones
            timeout = httpx.Timeout(self.settings.request_timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                response = await client.get(
                    "https://api.brightdata.com/zone/get_active_zones",
                    headers={"Authorization": f"Bearer {self.settings.brightdata_api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
            zones: dict[str, str] = {}
            if isinstance(payload, list):
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    zone_type = str(item.get("type") or "").strip().lower()
                    if name and zone_type and zone_type not in zones:
                        zones[zone_type] = name
            self._discovered_zones = zones
            return zones

    async def _resolve_serp_zone(self) -> str:
        if self.settings.brightdata_serp_zone:
            return self.settings.brightdata_serp_zone
        zones = await self._discover_zones()
        return zones.get("serp", "")

    async def _resolve_unlocker_zone(self) -> str:
        if self.settings.brightdata_unlocker_zone:
            return self.settings.brightdata_unlocker_zone
        zones = await self._discover_zones()
        for zone_type in ("unblocker", "web_unlocker", "unlocker"):
            if zones.get(zone_type):
                return zones[zone_type]
        return ""

    async def search(self, plan: SearchQueryPlan, limit: int) -> tuple[list[CandidateLink], dict[str, Any]]:
        serp_zone = await self._resolve_serp_zone()
        brightdata_error = ""
        if self.settings.brightdata_api_key and serp_zone:
            brightdata_candidates: list[CandidateLink] = []
            serp_urls = _build_serp_urls(plan)[: self.settings.serp_url_limit]
            for serp_url in serp_urls:
                payload = {
                    "zone": serp_zone,
                    "url": serp_url,
                    "format": "raw",
                    "data_format": "parsed_light",
                }
                try:
                    raw = await self._request_brightdata(payload)
                    parsed_candidates = parse_serp_payload(raw, plan, limit)
                    if not parsed_candidates and isinstance(raw, str):
                        parsed_candidates = parse_html_search_results(raw, plan, limit, "brightdata_serp_html")
                    brightdata_candidates.extend(parsed_candidates)
                    if len(brightdata_candidates) >= limit:
                        break
                except Exception as exc:
                    brightdata_error = str(exc)[:500]
                    continue
            if brightdata_candidates:
                return brightdata_candidates[:limit], {
                    "provider": "brightdata_serp",
                    "ok": True,
                    "payload_type": "mixed",
                }
            if not self.settings.allow_direct_fetch_fallback:
                return [], {"provider": "brightdata_serp", "ok": False, "error": brightdata_error or "no serp candidates"}

        if not self.settings.allow_direct_fetch_fallback:
            return [], {"provider": "none", "ok": False, "error": "Bright Data SERP 未配置且禁用了直连兜底"}

        query = quote_plus(plan.query)
        url = f"https://www.bing.com/search?q={query}&setlang=zh-CN&cc=cn"
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        headers = {"User-Agent": self.settings.user_agent}
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                response = await client.get(url)
            response.raise_for_status()
            return parse_html_search_results(response.text, plan, limit, "direct_bing"), {
                "provider": "direct_bing",
                "ok": True,
                "status_code": response.status_code,
                "fallback_after_brightdata_error": brightdata_error,
            }
        except Exception as exc:
            return [], {"provider": "direct_bing", "ok": False, "error": str(exc)[:500], "fallback_after_brightdata_error": brightdata_error}

    async def fetch_detail_page(self, url: str, force_render: bool = False) -> FetchResult:
        unlocker_zone = await self._resolve_unlocker_zone()
        brightdata_error = ""
        if self.settings.brightdata_api_key and unlocker_zone:
            payload = {
                "zone": unlocker_zone,
                "url": url,
                "format": "raw",
            }
            if force_render:
                payload["render"] = "true"
            try:
                raw = await self._request_brightdata(payload)
                html = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                if not html.strip():
                    raise RuntimeError("Bright Data returned empty response")
                final_url = _extract_final_url_from_html(html) or url
                provider = "brightdata_unlocker_render" if force_render else "brightdata_unlocker"
                return FetchResult(ok=True, url=url, final_url=normalize_url(final_url), html=html, provider=provider)
            except Exception as exc:
                brightdata_error = str(exc)[:500]
                if self.settings.render_fallback_enabled and not force_render:
                    try:
                        return await self.fetch_detail_page(url, force_render=True)
                    except Exception:
                        pass
                if self.settings.brightdata_api_key and unlocker_zone:
                    provider = "brightdata_unlocker_render" if force_render else "brightdata_unlocker"
                    return FetchResult(
                        ok=False,
                        url=url,
                        final_url=url,
                        provider=provider,
                        error_message=brightdata_error or "Bright Data detail fetch failed",
                    )
                if not self.settings.allow_direct_fetch_fallback:
                    return FetchResult(ok=False, url=url, final_url=url, provider="brightdata_unlocker", error_message=str(exc)[:500])

        if not self.settings.allow_direct_fetch_fallback:
            return FetchResult(ok=False, url=url, final_url=url, provider="none", error_message="Bright Data Unlocker 未配置且禁用了直连兜底")

        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        headers = {"User-Agent": self.settings.user_agent}
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                response = await client.get(url)
            result = FetchResult(
                ok=response.status_code < 400,
                url=url,
                final_url=normalize_url(str(response.url)),
                status_code=response.status_code,
                html=response.text,
                provider="direct_fetch",
                error_message="" if response.status_code < 400 else f"HTTP {response.status_code}",
            )
            if brightdata_error:
                result.provider = "direct_fetch_after_brightdata_error"
                result.error_message = result.error_message or f"Bright Data fallback: {brightdata_error}"
            return result
        except Exception as exc:
            error = str(exc)[:500]
            if brightdata_error:
                error = f"Bright Data fallback: {brightdata_error}; direct fetch: {error}"
            return FetchResult(ok=False, url=url, final_url=url, provider="direct_fetch", error_message=error)


def _extract_final_url_from_html(html: str) -> str:
    match = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', html or "", re.I)
    return match.group(1) if match else ""


def _build_serp_urls(plan: SearchQueryPlan) -> list[str]:
    variants = [plan.query]
    keyword = plan.keyword.strip()
    if plan.platform == "jd":
        variants.extend(
            [
                f"{keyword} 京东 商品",
                f"{keyword} 京东 item.jd.com",
                f"site:www.jd.com/chanpin {keyword}",
            ]
        )
    elif plan.platform == "1688":
        variants.extend(
            [
                f"{keyword} 1688 商品",
                f"{keyword} 阿里巴巴 detail.1688.com",
                f"site:www.1688.com {keyword}",
            ]
        )
    elif plan.platform == "suning":
        variants.append(f"{keyword} 苏宁 商品")

    google_urls: list[str] = []
    bing_urls: list[str] = []
    seen: set[str] = set()
    for query in variants:
        cleaned = " ".join(query.split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        encoded = quote_plus(cleaned)
        google_urls.append(f"https://www.google.com/search?q={encoded}&hl=zh-CN&gl=cn&num=10&brd_json=1")
        bing_urls.append(f"https://www.bing.com/search?q={encoded}&setlang=zh-CN&cc=cn")
    return google_urls + bing_urls
