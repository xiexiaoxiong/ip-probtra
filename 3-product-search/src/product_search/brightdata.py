from __future__ import annotations

import json
import re
import asyncio
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from product_search.config import Settings
from product_search.browser_fetch import fetch_with_browser
from product_search.models import CandidateLink, FetchResult, SearchQueryPlan
from product_search.platforms import is_detail_url, normalize_url
from product_search.special_sources import fetch_special_product_page


def _error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message[:500]
    return f"{type(exc).__name__}: {exc!r}"[:500]


def _looks_blocked_browser_result(result: FetchResult) -> bool:
    haystack = f"{result.final_url} {result.html[:5000]}".lower()
    markers = (
        "risk_handler", "安全验证", "验证码", "请完成验证", "滑块验证",
        "punish", "sec.taobao.com", "login.taobao.com", "passport.jd.com",
        "passport.suning.com", "/login.aspx", "账号登录",
    )
    return any(marker.lower() in haystack for marker in markers)


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
                original_keyword=plan.original_keyword or plan.keyword,
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
                original_keyword=plan.original_keyword or plan.keyword,
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


def parse_suning_search_results(html: str, plan: SearchQueryPlan, limit: int) -> list[CandidateLink]:
    soup = BeautifulSoup(html or "", "lxml")
    candidates: list[CandidateLink] = []
    seen: set[str] = set()
    for anchor in soup.select('a[href*="product.suning.com/"]'):
        href = normalize_url(str(anchor.get("href") or ""), "https://search.suning.com/")
        if not href or href in seen or not is_detail_url(href, "suning"):
            continue
        seen.add(href)
        title = " ".join(str(anchor.get("title") or anchor.get_text(" ", strip=True) or "").split())
        candidates.append(
            CandidateLink(
                keyword=plan.keyword,
                original_keyword=plan.original_keyword or plan.keyword,
                platform="suning",
                candidate_url=href,
                title=title[:500],
                source="direct_suning",
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
        self._last_zone_error = ""

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
            try:
                timeout = httpx.Timeout(self.settings.request_timeout_seconds)
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    response = await client.get(
                        "https://api.brightdata.com/zone/get_active_zones",
                        headers={"Authorization": f"Bearer {self.settings.brightdata_api_key}"},
                    )
                    response.raise_for_status()
                    payload = response.json()
            except Exception as exc:
                self._last_zone_error = _error_message(exc)
                self._discovered_zones = {}
                return {}
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
        try:
            zones = await self._discover_zones()
        except Exception as exc:
            self._last_zone_error = _error_message(exc)
            return ""
        return zones.get("serp", "")

    async def _resolve_unlocker_zone(self) -> str:
        if self.settings.brightdata_unlocker_zone:
            return self.settings.brightdata_unlocker_zone
        try:
            zones = await self._discover_zones()
        except Exception as exc:
            self._last_zone_error = _error_message(exc)
            return ""
        for zone_type in ("unblocker", "web_unlocker", "unlocker"):
            if zones.get(zone_type):
                return zones[zone_type]
        return ""

    async def search(self, plan: SearchQueryPlan, limit: int) -> tuple[list[CandidateLink], dict[str, Any]]:
        serp_zone = await self._resolve_serp_zone()
        brightdata_error = self._last_zone_error
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
                    brightdata_error = _error_message(exc)
                    continue
            if brightdata_candidates:
                if self.settings.allow_direct_fetch_fallback:
                    detail_count = sum(
                        1 for candidate in brightdata_candidates if is_detail_url(candidate.candidate_url, candidate.platform)
                    )
                    if len(brightdata_candidates) < limit or detail_count < min(3, limit):
                        direct_candidates, direct_meta = await self._search_direct_bing(plan, limit, brightdata_error)
                        merged = _merge_candidate_links(brightdata_candidates, direct_candidates)
                        return merged[:limit], {
                            "provider": "brightdata_serp+direct_bing",
                            "ok": bool(merged),
                            "payload_type": "mixed",
                            "brightdata_candidate_count": len(brightdata_candidates),
                            "direct_candidate_count": len(direct_candidates),
                            "direct_meta": direct_meta,
                        }
                return brightdata_candidates[:limit], {
                    "provider": "brightdata_serp",
                    "ok": True,
                    "payload_type": "mixed",
                }
            if not self.settings.allow_direct_fetch_fallback:
                return [], {"provider": "brightdata_serp", "ok": False, "error": brightdata_error or "no serp candidates"}

        if not self.settings.allow_direct_fetch_fallback:
            return [], {
                "provider": "none",
                "ok": False,
                "error": brightdata_error or "Bright Data SERP 未配置且禁用了直连兜底",
            }

        return await self._search_direct_bing(plan, limit, brightdata_error)

    async def _search_direct_bing(
        self,
        plan: SearchQueryPlan,
        limit: int,
        brightdata_error: str = "",
    ) -> tuple[list[CandidateLink], dict[str, Any]]:
        marketplace_candidates: list[CandidateLink] = []
        marketplace_meta: dict[str, Any] = {}
        if plan.platform == "suning":
            marketplace_candidates, marketplace_meta = await self._search_direct_suning(plan, limit)
            if len(marketplace_candidates) >= limit:
                return marketplace_candidates[:limit], marketplace_meta
        urls = [url for url in _build_serp_urls(plan) if "bing.com/search" in url]
        if not urls:
            query = quote_plus(plan.query)
            urls = [f"https://www.bing.com/search?q={query}&setlang=zh-CN&cc=cn"]
        urls = urls[: max(1, max(self.settings.serp_url_limit, 12))]
        timeout = httpx.Timeout(self.settings.request_timeout_seconds)
        headers = {"User-Agent": self.settings.user_agent}
        candidates: list[CandidateLink] = list(marketplace_candidates)
        last_error = ""
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                for url in urls:
                    try:
                        response = await client.get(url)
                        response.raise_for_status()
                        candidates.extend(parse_html_search_results(response.text, plan, limit, "direct_bing"))
                        if len(candidates) >= limit:
                            break
                    except Exception as exc:
                        last_error = _error_message(exc)
                        continue
            merged = _merge_candidate_links(marketplace_candidates, candidates)
            return merged[:limit], {
                "provider": "direct_suning+direct_bing" if marketplace_candidates else "direct_bing",
                "ok": bool(candidates),
                "status_code": 200 if candidates else 0,
                "fallback_after_brightdata_error": brightdata_error,
                "error": "" if candidates else last_error,
                "marketplace_meta": marketplace_meta,
            }
        except Exception as exc:
            return [], {"provider": "direct_bing", "ok": False, "error": _error_message(exc), "fallback_after_brightdata_error": brightdata_error}

    async def _search_direct_suning(
        self,
        plan: SearchQueryPlan,
        limit: int,
    ) -> tuple[list[CandidateLink], dict[str, Any]]:
        search_url = f"https://search.suning.com/{quote_plus(plan.keyword)}/"
        try:
            timeout = httpx.Timeout(self.settings.request_timeout_seconds)
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": self.settings.user_agent},
            ) as client:
                response = await client.get(search_url)
                response.raise_for_status()
            candidates = parse_suning_search_results(response.text, plan, limit)
            return candidates, {
                "provider": "direct_suning",
                "ok": bool(candidates),
                "status_code": response.status_code,
                "candidate_count": len(candidates),
            }
        except Exception as exc:
            return [], {"provider": "direct_suning", "ok": False, "error": _error_message(exc)}

    async def fetch_detail_page(self, url: str, force_render: bool = False) -> FetchResult:
        special_result = await fetch_special_product_page(url, self.settings.request_timeout_seconds, self.settings.user_agent)
        if special_result and special_result.ok:
            return special_result

        unlocker_zone = await self._resolve_unlocker_zone()
        brightdata_error = self._last_zone_error
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
                brightdata_result = FetchResult(
                    ok=True, url=url, final_url=normalize_url(final_url), html=html, provider=provider
                )
                if force_render and self.settings.browser_fallback_enabled:
                    browser_result = await fetch_with_browser(url, self.settings)
                    brightdata_result.capture_meta["local_browser_attempted"] = True
                    if browser_result.error_message:
                        brightdata_result.capture_meta["local_browser_error"] = browser_result.error_message
                    browser_blocked = _looks_blocked_browser_result(browser_result)
                    if browser_result.ok and not browser_blocked:
                        browser_image_signals = len(re.findall(r"(?:<img|image|\.jpg|\.jpeg|\.png|\.webp)", browser_result.html, re.I))
                        brightdata_image_signals = len(re.findall(r"(?:<img|image|\.jpg|\.jpeg|\.png|\.webp)", html, re.I))
                        if browser_image_signals >= brightdata_image_signals:
                            return browser_result
                return brightdata_result
            except Exception as exc:
                brightdata_error = _error_message(exc)
                if self.settings.render_fallback_enabled and not force_render:
                    try:
                        render_result = await self.fetch_detail_page(url, force_render=True)
                        if render_result.ok or not self.settings.allow_direct_fetch_fallback:
                            return render_result
                        brightdata_error = render_result.error_message or brightdata_error
                    except Exception:
                        pass
                if self.settings.brightdata_api_key and unlocker_zone:
                    provider = "brightdata_unlocker_render" if force_render else "brightdata_unlocker"
                    if not self.settings.allow_direct_fetch_fallback:
                        return FetchResult(
                            ok=False,
                            url=url,
                            final_url=url,
                            provider=provider,
                            error_message=brightdata_error or "Bright Data detail fetch failed",
                        )
                if not self.settings.allow_direct_fetch_fallback:
                    return FetchResult(ok=False, url=url, final_url=url, provider="brightdata_unlocker", error_message=_error_message(exc))

        browser_attempt_meta: dict[str, Any] = {}
        if force_render and self.settings.browser_fallback_enabled:
            browser_result = await fetch_with_browser(url, self.settings)
            if browser_result.ok and not _looks_blocked_browser_result(browser_result):
                return browser_result
            browser_attempt_meta = {
                "local_browser_attempted": True,
                "local_browser_rejected": bool(browser_result.ok),
                "local_browser_final_url": browser_result.final_url,
                "local_browser_error": browser_result.error_message,
                "local_browser_capture": browser_result.capture_meta,
            }

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
            elif browser_attempt_meta:
                result.provider = "direct_fetch_after_browser_rejection"
            result.capture_meta = browser_attempt_meta
            return result
        except Exception as exc:
            error = _error_message(exc)
            if brightdata_error:
                error = f"Bright Data fallback: {brightdata_error}; direct fetch: {error}"
            return FetchResult(ok=False, url=url, final_url=url, provider="direct_fetch", error_message=error)


def _extract_final_url_from_html(html: str) -> str:
    match = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', html or "", re.I)
    return match.group(1) if match else ""


def _merge_candidate_links(*groups: list[CandidateLink]) -> list[CandidateLink]:
    merged: list[CandidateLink] = []
    seen: set[str] = set()
    for group in groups:
        for candidate in group:
            key = normalize_url(candidate.candidate_url)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(candidate)
    return merged


def _build_serp_urls(plan: SearchQueryPlan) -> list[str]:
    variants = [plan.query]
    keyword = plan.keyword.strip()
    original_keyword = (plan.original_keyword or "").strip()
    keyword_variants = [keyword]
    if original_keyword and original_keyword != keyword:
        keyword_variants.append(original_keyword)
    for search_keyword in keyword_variants:
        if search_keyword:
            variants.extend(
                [
                    f"{search_keyword} 商品",
                    f"{search_keyword} 价格",
                    f"{search_keyword} 官方 产品",
                    f"{search_keyword} 官网 商品 图片",
                    f"{search_keyword} 商品详情 图片",
                    f"{search_keyword} 产品详情 图片",
                    f"{search_keyword} 参数 图片",
                    f"{search_keyword} 购买",
                ]
            )
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
