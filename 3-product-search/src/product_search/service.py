from __future__ import annotations

import asyncio
import datetime as dt
import re
import uuid

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
from product_search.platforms import build_search_query_plans, dedupe_candidate_links, is_aggregate_url, is_detail_url
from product_search.quality import evaluate_product_detail


class ProductSearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = BrightDataClient(settings)

    async def run(self, payload: ProductSearchInput) -> ProductSearchOutput:
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
            candidates = await self._collect_candidate_links(payload, keywords)
            detail_candidates = self._select_detail_candidates(candidates, payload.max_detail_candidates)
            products, diagnostics = await self._fetch_and_filter_candidates(payload, detail_candidates)
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
                    error_message=str(exc)[:1000],
                )
            raise

    async def _collect_candidate_links(self, payload: ProductSearchInput, keywords: list[str]) -> list[CandidateLink]:
        plans = build_search_query_plans(keywords, payload.platforms, payload.max_keywords)
        semaphore = asyncio.Semaphore(self.settings.max_concurrency)

        async def search_one(plan):
            async with semaphore:
                links, meta = await self.client.search(plan, payload.max_candidates_per_keyword)
                return [link.model_copy(update={"source": meta.get("provider", link.source)}) for link in links]

        results = await asyncio.gather(*(search_one(plan) for plan in plans))
        candidates = dedupe_candidate_links([candidate for group in results for candidate in group])
        expanded = await self._expand_discovery_candidates(candidates, payload.max_candidates_per_keyword)
        return dedupe_candidate_links(candidates + expanded)

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
        detail_candidates.sort(key=lambda item: (source_priority.get(item.source, 9), item.rank))
        return detail_candidates[: max(1, limit)]

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

        await asyncio.gather(*(process(candidate) for candidate in candidates))
        return list(accepted_by_url.values()), diagnostics

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
        cleaned_keyword = re.sub(r"\s+", "", keyword or "")
        if not decision.accepted or len(cleaned_keyword) < 5:
            return decision
        haystack = re.sub(r"\s+", "", f"{parsed.product_name} {parsed.description}")
        if not haystack:
            return decision
        chars = [char for char in dict.fromkeys(cleaned_keyword) if "\u4e00" <= char <= "\u9fff"]
        if len(chars) < 4:
            return decision
        ratio = sum(1 for char in chars if char in haystack) / len(chars)
        bigrams = self._keyword_bigrams(cleaned_keyword)
        bigram_ratio = sum(1 for token in bigrams if token in haystack) / max(1, len(bigrams))
        decision.flags["matched_keyword_ratio"] = round(ratio, 3)
        decision.flags["matched_keyword_bigram_ratio"] = round(bigram_ratio, 3)
        decision.flags["matched_keyword"] = keyword
        if ratio < 0.58 or (len(cleaned_keyword) >= 5 and bigrams and bigram_ratio < 0.35):
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
