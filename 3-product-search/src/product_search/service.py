from __future__ import annotations

import asyncio
import datetime as dt
import re
import uuid
from dataclasses import replace

from product_search.brightdata import BrightDataClient
from product_search.config import Settings
from product_search.db import create_run, fetch_keywords, finish_run, insert_candidate, insert_product
from product_search.discovery import extract_detail_candidates_from_discovery_page
from product_search.models import (
    CandidateDiagnostic,
    CandidateLink,
    ProductResult,
    ProductSearchInput,
    ProductSearchOutput,
)
from product_search.parser import parse_product_page
from product_search.platforms import (
    build_search_query_plans,
    dedupe_candidate_links,
    detect_platform,
    is_aggregate_url,
    is_detail_url,
)
from product_search.quality import evaluate_product_detail


def _error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message[:1000]
    return f"{type(exc).__name__}: {exc!r}"[:1000]


TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "體": "体",
        "計": "计",
        "時": "时",
        "轉": "转",
        "學": "学",
        "習": "习",
        "電": "电",
        "價": "价",
        "醫": "医",
        "藥": "药",
        "療": "疗",
        "機": "机",
        "馬": "马",
        "達": "达",
        "聲": "声",
        "顯": "显",
        "掃": "扫",
        "拖": "拖",
        "昇": "升",
        "級": "级",
        "線": "线",
        "護": "护",
        "測": "测",
        "應": "应",
        "專": "专",
        "賣": "卖",
        "購": "购",
    }
)

CORE_PRODUCT_TERMS = (
    "扫地机器人",
    "健身车",
    "动感单车",
    "脚踏车",
    "跑步机",
    "计时器",
    "定时器",
    "音箱",
    "音响",
    "耳机",
    "灯具",
    "水枪",
    "充电站",
    "高尔夫球",
    "马达驱动芯片",
    "驱动芯片",
)

ACCESSORY_TERMS = (
    "配件",
    "耗材",
    "爬坡垫",
    "三角垫",
    "垫板",
    "坡道",
    "支架",
    "保护套",
    "收纳袋",
    "滤芯",
    "滤网",
    "边刷",
    "主刷",
    "拖布",
    "抹布",
    "尘袋",
    "水箱",
    "电池",
    "充电器",
    "遥控器",
    "柜",
    "柜子",
    "收纳柜",
    "阳台柜",
    "洗衣机柜",
)

CORE_COMPONENT_MISMATCH_TERMS = {
    "扫地机器人": (
        "洗地机",
        "洗拖吸一体机",
        "蒸汽拖把",
        "电动拖把",
        "拖把",
    ),
    "灯具": (
        "调光器",
        "控制器",
        "驱动器",
        "电源",
        "插座",
        "连接器",
        "支架",
        "灯座",
        "灯脚",
        "按钮",
        "开关",
    ),
    "充电站": (
        "充电器",
        "充电桩",
        "电动汽车",
        "汽车",
        "车辆",
        "基础设施",
        "连接器",
        "线缆",
        "电源模块",
        "电池",
    ),
    "高尔夫球": (
        "高尔夫球车",
        "球车",
        "球包车",
        "手推车",
    ),
    "马达驱动芯片": (
        "算法",
        "校准算法",
        "方案",
        "控制方法",
        "开发板",
        "评估板",
    ),
    "驱动芯片": (
        "算法",
        "校准算法",
        "方案",
        "控制方法",
        "开发板",
        "评估板",
    ),
}

CORE_OBJECT_ALIAS_TERMS = {
    "灯具": (
        "灯具",
        "射灯",
        "筒灯",
        "吸顶灯",
        "吊灯",
        "台灯",
        "壁灯",
        "线灯",
        "灯带",
        "灯条",
        "灯泡",
        "照明",
    ),
    "充电站": (
        "充电站",
        "移动电站",
        "便携式电站",
        "户外电源",
        "chargingstation",
        "chargerstation",
        "evchargingstation",
        "evcharger",
        "portableevcharger",
        "chargercarstation",
        "carstation",
    ),
    "高尔夫球": (
        "高尔夫球",
        "智能高尔夫球",
    ),
    "马达驱动芯片": (
        "马达驱动芯片",
        "电机驱动芯片",
        "驱动芯片",
        "马达驱动ic",
        "驱动ic",
        "触觉驱动器",
        "hapticdriver",
        "hapticsdriver",
        "motorcontroller",
    ),
    "驱动芯片": (
        "驱动芯片",
        "电机驱动芯片",
        "马达驱动芯片",
        "驱动ic",
        "hapticdriver",
        "hapticsdriver",
    ),
}


def _normalize_relevance_text(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").translate(TRADITIONAL_TO_SIMPLIFIED)).lower()


class ProductSearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = BrightDataClient(settings)

    async def run(self, payload: ProductSearchInput) -> ProductSearchOutput:
        self._apply_payload_overrides(payload)
        product_dataset_id = f"product_detail_{int(dt.datetime.now().timestamp() * 1000)}_{uuid.uuid4().hex[:6]}"
        keywords = fetch_keywords(
            payload.patent_record_id,
            payload.analysis_session_id,
            payload.input_keywords,
            payload.max_keywords,
        )
        provider = {
            "serp": "brightdata_serp_or_auto_zone" if self.settings.brightdata_api_key else "direct_bing",
            "detail": "brightdata_unlocker"
            if self.settings.brightdata_api_key
            else "direct_fetch",
        }
        run_id = 0
        if payload.persist:
            run_id = create_run(
                patent_record_id=payload.patent_record_id,
                analysis_session_id=payload.analysis_session_id,
                product_dataset_id=product_dataset_id,
                keywords=keywords,
                platforms=payload.platforms,
                provider=provider,
            )

        if not keywords:
            if run_id:
                finish_run(
                    run_id=run_id,
                    status="failed",
                    candidate_link_count=0,
                    accepted_products_count=0,
                    rejected_candidates_count=0,
                    error_message="未找到模块2关键词",
                )
            return ProductSearchOutput(
                product_dataset_id=product_dataset_id,
                product_detail_search_run_id=run_id,
                patent_record_id=payload.patent_record_id,
                analysis_session_id=payload.analysis_session_id,
                keywords=[],
                is_complete=False,
                error_message="未找到模块2关键词",
            )

        try:
            candidates, search_diagnostics = await self._collect_candidate_links(payload, keywords)
            detail_candidates = self._select_detail_candidates(candidates, payload.max_detail_candidates)
            candidate_filter_diagnostics = self._diagnose_filtered_candidates(candidates, detail_candidates)
            products, detail_diagnostics = await self._fetch_and_filter_candidates(payload, detail_candidates)
            diagnostics = search_diagnostics + candidate_filter_diagnostics + detail_diagnostics
            accepted = products[: payload.max_products]
            rejected_count = sum(1 for item in diagnostics if item.status != "accepted")

            if payload.persist and run_id:
                for diagnostic in diagnostics:
                    insert_candidate(
                        run_id=run_id,
                        patent_record_id=payload.patent_record_id,
                        analysis_session_id=payload.analysis_session_id,
                        diagnostic=diagnostic,
                    )
                for product in accepted:
                    insert_product(
                        run_id=run_id,
                        patent_record_id=payload.patent_record_id,
                        analysis_session_id=payload.analysis_session_id,
                        product=product,
                    )
                finish_run(
                    run_id=run_id,
                    status="complete",
                    candidate_link_count=len(detail_candidates),
                    accepted_products_count=len(accepted),
                    rejected_candidates_count=rejected_count,
                    error_message="",
                )

            return ProductSearchOutput(
                product_dataset_id=product_dataset_id,
                product_detail_search_run_id=run_id,
                patent_record_id=payload.patent_record_id,
                analysis_session_id=payload.analysis_session_id,
                keywords=keywords,
                total_candidate_links_count=len(detail_candidates),
                accepted_products_count=len(accepted),
                rejected_candidates_count=rejected_count,
                products=accepted,
                candidates_preview=diagnostics[:20],
            )
        except Exception as exc:
            if payload.persist and run_id:
                finish_run(
                    run_id=run_id,
                    status="failed",
                    candidate_link_count=0,
                    accepted_products_count=0,
                    rejected_candidates_count=0,
                    error_message=_error_message(exc),
                )
            raise

    async def _collect_candidate_links(
        self,
        payload: ProductSearchInput,
        keywords: list[str],
    ) -> tuple[list[CandidateLink], list[CandidateDiagnostic]]:
        plans = build_search_query_plans(keywords, payload.platforms, payload.max_keywords)
        semaphore = asyncio.Semaphore(self.settings.max_concurrency)

        async def search_one(plan) -> tuple[list[CandidateLink], CandidateDiagnostic | None]:
            async with semaphore:
                try:
                    links, meta = await self.client.search(plan, payload.max_candidates_per_keyword)
                except Exception as exc:
                    return [], CandidateDiagnostic(
                        keyword=plan.keyword,
                        platform=plan.platform,
                        candidate_url=plan.serp_url,
                        title=plan.query,
                        status="error",
                        rejection_reason=f"搜索计划执行异常: {_error_message(exc)}",
                        raw_payload={"plan": plan.model_dump()},
                    )
                provider = str(meta.get("provider") or "")
                if not links:
                    reason = "搜索未返回商品候选"
                    if meta.get("error"):
                        reason = f"{reason}: {meta.get('error')}"
                    return [], CandidateDiagnostic(
                        keyword=plan.keyword,
                        platform=plan.platform,
                        candidate_url=plan.serp_url,
                        title=plan.query,
                        status="search_empty",
                        rejection_reason=reason,
                        raw_payload={"plan": plan.model_dump(), "search_meta": meta},
                    )
                return [link.model_copy(update={"source": provider or link.source}) for link in links], None

        results = await asyncio.gather(*(search_one(plan) for plan in plans))
        diagnostics = [diagnostic for _links, diagnostic in results if diagnostic is not None]
        candidates = dedupe_candidate_links([candidate for links, _diagnostic in results for candidate in links])
        expanded = await self._expand_discovery_candidates(candidates, payload.max_candidates_per_keyword)
        return dedupe_candidate_links(candidates + expanded), diagnostics

    async def _expand_discovery_candidates(self, candidates: list[CandidateLink], per_page_limit: int) -> list[CandidateLink]:
        discovery_pages: list[CandidateLink] = []
        seen_pages: set[str] = set()
        for candidate in candidates:
            if candidate.candidate_url in seen_pages:
                continue
            if is_detail_url(candidate.candidate_url, candidate.platform):
                continue
            if not is_aggregate_url(candidate.candidate_url):
                continue
            seen_pages.add(candidate.candidate_url)
            discovery_pages.append(candidate)
            if len(discovery_pages) >= 6:
                break

        if not discovery_pages:
            return []

        semaphore = asyncio.Semaphore(max(1, min(2, self.settings.max_concurrency)))

        async def expand_one(candidate: CandidateLink) -> list[CandidateLink]:
            async with semaphore:
                fetch = await self.client.fetch_detail_page(candidate.candidate_url, force_render=True)
            if not fetch.ok or not fetch.html:
                return []
            return extract_detail_candidates_from_discovery_page(
                html=fetch.html,
                page_url=fetch.final_url or candidate.candidate_url,
                keyword=candidate.keyword,
                platform=candidate.platform,
                limit=max(1, per_page_limit),
            )

        groups = await asyncio.gather(*(expand_one(candidate) for candidate in discovery_pages))
        return [candidate for group in groups for candidate in group]

    def _select_detail_candidates(self, candidates: list[CandidateLink], limit: int) -> list[CandidateLink]:
        detail_candidates = [candidate for candidate in candidates if is_detail_url(candidate.candidate_url, candidate.platform)]
        source_priority = {
            "jd_discovery_page": 0,
            "discovery_page": 1,
            "brightdata_serp": 2,
            "brightdata_serp_html": 3,
            "direct_bing": 4,
        }
        detail_candidates.sort(
            key=lambda item: (
                self._candidate_platform_priority(item),
                -self._candidate_relevance_score(item),
                source_priority.get(item.source, 9),
                -len(_normalize_relevance_text(item.keyword)),
                item.rank,
            )
        )
        limit = max(1, limit)
        marketplace = [candidate for candidate in detail_candidates if self._candidate_platform_priority(candidate) == 0]
        external = [candidate for candidate in detail_candidates if self._candidate_platform_priority(candidate) > 0]
        if not marketplace or not external or limit == 1:
            return detail_candidates[:limit]

        external_limit = min(len(external), max(1, limit // 2))
        marketplace_limit = max(1, limit - external_limit)
        selected = marketplace[:marketplace_limit] + external[:external_limit]
        selected_urls = {candidate.candidate_url for candidate in selected}
        for candidate in detail_candidates:
            if len(selected) >= limit:
                break
            if candidate.candidate_url not in selected_urls:
                selected.append(candidate)
                selected_urls.add(candidate.candidate_url)
        return selected[:limit]

    def _candidate_platform_priority(self, candidate: CandidateLink) -> int:
        platform = detect_platform(candidate.candidate_url) or candidate.platform
        if platform == "external_product":
            return 1
        return 0

    def _candidate_relevance_score(self, candidate: CandidateLink) -> float:
        keyword = _normalize_relevance_text(candidate.keyword)
        haystack = _normalize_relevance_text(f"{candidate.title} {candidate.snippet}")
        if not keyword or not haystack:
            return 0.0
        chars = [char for char in dict.fromkeys(keyword) if "\u4e00" <= char <= "\u9fff"]
        char_ratio = sum(1 for char in chars if char in haystack) / max(1, len(chars))
        bigrams = self._keyword_bigrams(keyword)
        bigram_ratio = sum(1 for token in bigrams if token in haystack) / max(1, len(bigrams))
        return round(char_ratio + bigram_ratio * 1.5, 4)

    def _diagnose_filtered_candidates(
        self,
        candidates: list[CandidateLink],
        detail_candidates: list[CandidateLink],
    ) -> list[CandidateDiagnostic]:
        selected_urls = {candidate.candidate_url for candidate in detail_candidates}
        diagnostics: list[CandidateDiagnostic] = []
        for candidate in candidates:
            if candidate.candidate_url in selected_urls:
                continue
            if is_detail_url(candidate.candidate_url, candidate.platform):
                reason = "商品详情候选超过 max_detail_candidates 限制，未抓取详情"
            elif is_aggregate_url(candidate.candidate_url):
                reason = "候选链接是综合/搜索/列表页，不作为商品详情页保留"
            else:
                reason = "候选链接不是当前支持的平台商品详情页"
            diagnostics.append(
                CandidateDiagnostic(
                    keyword=candidate.keyword,
                    platform=candidate.platform,
                    candidate_url=candidate.candidate_url,
                    title=candidate.title,
                    status="rejected",
                    rejection_reason=reason,
                    raw_payload={"candidate": candidate.model_dump()},
                )
            )
            if len(diagnostics) >= 50:
                break
        return diagnostics

    async def _fetch_and_filter_candidates(
        self,
        payload: ProductSearchInput,
        candidates: list[CandidateLink],
    ) -> tuple[list[ProductResult], list[CandidateDiagnostic]]:
        semaphore = asyncio.Semaphore(self.settings.max_concurrency)
        accepted_by_url: dict[str, ProductResult] = {}
        diagnostics: list[CandidateDiagnostic] = []

        async def process(candidate: CandidateLink) -> None:
            if len(accepted_by_url) >= payload.max_products:
                diagnostics.append(
                    CandidateDiagnostic(
                        keyword=candidate.keyword,
                        platform=candidate.platform,
                        candidate_url=candidate.candidate_url,
                        title=candidate.title,
                        status="skipped",
                        rejection_reason="已达到 max_products，未继续抓取该详情候选",
                        raw_payload={"candidate": candidate.model_dump()},
                    )
                )
                return
            if not is_detail_url(candidate.candidate_url, candidate.platform):
                diagnostics.append(
                    CandidateDiagnostic(
                        keyword=candidate.keyword,
                        platform=candidate.platform,
                        candidate_url=candidate.candidate_url,
                        title=candidate.title,
                        status="rejected",
                        rejection_reason="候选链接不是平台商品详情页",
                        raw_payload={"candidate": candidate.model_dump()},
                    )
                )
                return

            async with semaphore:
                fetch = await self.client.fetch_detail_page(candidate.candidate_url)
            if not fetch.ok and self._should_retry_fetch_error(fetch):
                for _attempt in range(self.settings.render_retry_attempts):
                    render_fetch = await self.client.fetch_detail_page(candidate.candidate_url, force_render=True)
                    if render_fetch.ok:
                        fetch = render_fetch
                        break
                    fetch = render_fetch
                    await asyncio.sleep(0.8)
            if not fetch.ok:
                diagnostics.append(
                    CandidateDiagnostic(
                        keyword=candidate.keyword,
                        platform=candidate.platform,
                        candidate_url=candidate.candidate_url,
                        final_url=fetch.final_url,
                        title=candidate.title,
                        status="error",
                        rejection_reason=fetch.error_message or "详情页抓取失败",
                        raw_payload={"candidate": candidate.model_dump(), "fetch": fetch.model_dump()},
                    )
                )
                return

            parsed = parse_product_page(fetch.html, candidate.candidate_url, fetch.final_url)
            decision = evaluate_product_detail(fetch, parsed)
            best_fetch = fetch
            best_parsed = parsed
            best_decision = decision
            for _attempt in range(self.settings.render_retry_attempts + 1):
                if not self._should_retry_render(best_fetch, best_decision):
                    break
                render_fetch = await self.client.fetch_detail_page(candidate.candidate_url, force_render=True)
                render_parsed = parse_product_page(render_fetch.html, candidate.candidate_url, render_fetch.final_url)
                render_decision = evaluate_product_detail(render_fetch, render_parsed)
                if render_decision.score >= best_decision.score:
                    best_fetch = render_fetch
                    best_parsed = render_parsed
                    best_decision = render_decision
                if render_decision.accepted:
                    break
                await asyncio.sleep(0.8)
            fetch = best_fetch
            parsed = best_parsed
            decision = self._apply_keyword_relevance(candidate.keyword, parsed, best_decision)
            final_url = parsed.final_url or fetch.final_url
            diagnostics.append(
                CandidateDiagnostic(
                    keyword=candidate.keyword,
                    platform=candidate.platform,
                    candidate_url=candidate.candidate_url,
                    final_url=final_url,
                    title=parsed.product_name or candidate.title,
                    status=decision.status,
                    rejection_reason="；".join(decision.reasons),
                    quality_score=decision.score,
                    raw_payload={
                        "candidate": candidate.model_dump(),
                        "fetch": fetch.model_dump(exclude={"html"}),
                        "quality": decision.model_dump(),
                    },
                )
            )
            if not decision.accepted or final_url in accepted_by_url:
                return

            accepted_by_url[final_url] = ProductResult(
                product_id=parsed.product_id,
                platform=parsed.platform or candidate.platform,
                product_name=parsed.product_name,
                product_url=final_url,
                final_url=final_url,
                price=parsed.price,
                sales=parsed.sales,
                brand=parsed.brand,
                manufacturer=parsed.manufacturer,
                description=parsed.description,
                detail_text=parsed.detail_text,
                picture=parsed.picture,
                matched_keywords=[candidate.keyword],
                quality_score=decision.score,
                quality_flags=decision.flags,
                raw_payload={
                    "candidate": candidate.model_dump(),
                    "fetch_provider": fetch.provider,
                    "page": parsed.raw_payload,
                    "quality": decision.model_dump(),
                },
            )

        for group in self._candidate_fetch_groups(candidates):
            if len(accepted_by_url) >= payload.max_products:
                for candidate in group:
                    diagnostics.append(
                        CandidateDiagnostic(
                            keyword=candidate.keyword,
                            platform=candidate.platform,
                            candidate_url=candidate.candidate_url,
                            title=candidate.title,
                            status="skipped",
                            rejection_reason="已达到 max_products，未继续抓取该详情候选",
                            raw_payload={"candidate": candidate.model_dump()},
                        )
                    )
                continue

            tasks = {asyncio.create_task(process(candidate)): candidate for candidate in group}
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception as exc:
                        candidate = tasks[task]
                        diagnostics.append(
                            CandidateDiagnostic(
                                keyword=candidate.keyword,
                                platform=candidate.platform,
                                candidate_url=candidate.candidate_url,
                                title=candidate.title,
                                status="error",
                                rejection_reason=f"详情候选处理异常: {_error_message(exc)}",
                                raw_payload={"candidate": candidate.model_dump()},
                            )
                        )
                if len(accepted_by_url) >= payload.max_products:
                    break

            if len(accepted_by_url) >= payload.max_products and pending:
                for task in pending:
                    candidate = tasks[task]
                    task.cancel()
                    diagnostics.append(
                        CandidateDiagnostic(
                            keyword=candidate.keyword,
                            platform=candidate.platform,
                            candidate_url=candidate.candidate_url,
                            title=candidate.title,
                            status="skipped",
                            rejection_reason="已达到 max_products，未继续抓取该详情候选",
                            raw_payload={"candidate": candidate.model_dump()},
                        )
                    )
                await asyncio.gather(*pending, return_exceptions=True)

        return list(accepted_by_url.values()), diagnostics

    def _candidate_fetch_groups(self, candidates: list[CandidateLink]) -> list[list[CandidateLink]]:
        marketplace: list[CandidateLink] = []
        external: list[CandidateLink] = []
        for candidate in candidates:
            if self._candidate_platform_priority(candidate) == 0:
                marketplace.append(candidate)
            else:
                external.append(candidate)
        return [group for group in (marketplace, external) if group]

    def _apply_payload_overrides(self, payload: ProductSearchInput) -> None:
        overrides = {}
        if payload.request_timeout_seconds is not None:
            overrides["request_timeout_seconds"] = payload.request_timeout_seconds
        if payload.serp_url_limit is not None:
            overrides["serp_url_limit"] = payload.serp_url_limit
        if not overrides:
            return
        self.settings = replace(self.settings, **overrides)
        self.client = BrightDataClient(self.settings)

    def _should_retry_render(self, fetch, decision) -> bool:
        if not self.settings.brightdata_api_key or not self.settings.render_fallback_enabled:
            return False
        if fetch.provider == "brightdata_unlocker_render":
            return False
        if not fetch.provider.startswith("brightdata_unlocker"):
            return False
        reasons = " ".join(decision.reasons)
        return any(token in reasons for token in ("未提取到商品标题", "商品详情文本过短", "未提取到商品图片"))

    def _should_retry_fetch_error(self, fetch) -> bool:
        if not self.settings.brightdata_api_key or not self.settings.render_fallback_enabled:
            return False
        provider = str(getattr(fetch, "provider", "") or "")
        if provider == "brightdata_unlocker_render":
            return True
        return provider.startswith("brightdata_unlocker")

    def _apply_keyword_relevance(self, keyword: str, parsed, decision):
        cleaned_keyword = _normalize_relevance_text(keyword)
        if not decision.accepted:
            return decision
        haystack = _normalize_relevance_text(f"{parsed.product_name} {parsed.description}")
        if not haystack:
            return decision
        if self._is_accessory_mismatch(cleaned_keyword, parsed.product_name):
            decision.accepted = False
            decision.status = "rejected"
            decision.reasons.append("商品标题显示为核心商品的配件/耗材，不是商品本体")
            return decision
        if self._is_component_mismatch(cleaned_keyword, parsed.product_name):
            decision.accepted = False
            decision.status = "rejected"
            decision.reasons.append("商品标题显示为核心商品的控制/连接/安装部件，不是商品本体")
            return decision
        if self._short_core_keyword_missing_title_signal(cleaned_keyword, parsed.product_name):
            decision.accepted = False
            decision.status = "rejected"
            decision.reasons.append("商品标题未显示短核心关键词对应的商品本体")
            return decision
        if len(cleaned_keyword) < 5:
            return decision
        chars = [char for char in dict.fromkeys(cleaned_keyword) if "\u4e00" <= char <= "\u9fff"]
        if len(chars) < 4:
            return decision
        ratio = sum(1 for char in chars if char in haystack) / len(chars)
        bigrams = self._keyword_bigrams(cleaned_keyword)
        bigram_ratio = sum(1 for token in bigrams if token in haystack) / max(1, len(bigrams))
        has_core_object_signal = self._has_core_object_signal(cleaned_keyword, haystack)
        decision.flags["matched_keyword_ratio"] = round(ratio, 3)
        decision.flags["matched_keyword_bigram_ratio"] = round(bigram_ratio, 3)
        decision.flags["matched_keyword"] = keyword
        allow_sparse_bigram = has_core_object_signal and (
            ratio >= 0.85
            or ("充电站" in cleaned_keyword and ratio >= 0.58)
            or (("马达驱动芯片" in cleaned_keyword or "驱动芯片" in cleaned_keyword) and ratio >= 0.58)
        )
        if ratio < 0.58 or (
            len(cleaned_keyword) >= 5
            and bigrams
            and bigram_ratio < 0.35
            and not allow_sparse_bigram
        ):
            decision.accepted = False
            decision.status = "rejected"
            decision.reasons.append(f"商品详情与检索关键词连续片段覆盖度不足: 字符={ratio:.2f}, 片段={bigram_ratio:.2f}")
        return decision

    def _keyword_bigrams(self, keyword: str) -> list[str]:
        chars = [char for char in keyword if "\u4e00" <= char <= "\u9fff"]
        tokens: list[str] = []
        seen: set[str] = set()
        for index in range(len(chars) - 1):
            token = "".join(chars[index : index + 2])
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
        return tokens

    def _is_accessory_mismatch(self, cleaned_keyword: str, product_name: str) -> bool:
        title = _normalize_relevance_text(product_name)
        if not title:
            return False
        core_terms = [term for term in CORE_PRODUCT_TERMS if term in cleaned_keyword]
        if not core_terms:
            return False
        if any(term in cleaned_keyword for term in ACCESSORY_TERMS):
            return False
        return any(core in title for core in core_terms) and any(term in title for term in ACCESSORY_TERMS)

    def _has_core_object_signal(self, cleaned_keyword: str, haystack: str) -> bool:
        for core_term in CORE_PRODUCT_TERMS:
            if core_term not in cleaned_keyword:
                continue
            aliases = CORE_OBJECT_ALIAS_TERMS.get(core_term, (core_term,))
            if any(alias in haystack for alias in aliases):
                return True
        return False

    def _is_component_mismatch(self, cleaned_keyword: str, product_name: str) -> bool:
        title = _normalize_relevance_text(product_name)
        if not title:
            return False
        for core_term, component_terms in CORE_COMPONENT_MISMATCH_TERMS.items():
            if core_term not in cleaned_keyword:
                continue
            if core_term == "扫地机器人" and any(term in title for term in component_terms):
                return True
            if core_term == "高尔夫球" and "高尔夫球车" in title and "高尔夫球车" not in cleaned_keyword:
                return True
            if core_term in title:
                continue
            if any(term in title for term in component_terms):
                return True
        return False

    def _short_core_keyword_missing_title_signal(self, cleaned_keyword: str, product_name: str) -> bool:
        if len(cleaned_keyword) >= 5:
            return False
        title = _normalize_relevance_text(product_name)
        if not title:
            return False
        core_terms = [term for term in CORE_PRODUCT_TERMS if cleaned_keyword == term]
        if not core_terms:
            return False
        for core_term in core_terms:
            aliases = CORE_OBJECT_ALIAS_TERMS.get(core_term, (core_term,))
            if any(alias in title for alias in aliases):
                return False
        return True
