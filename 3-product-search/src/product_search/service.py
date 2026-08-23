from __future__ import annotations

import asyncio
import datetime as dt
import re
import uuid
from dataclasses import replace

from product_search.brightdata import BrightDataClient
from product_search.config import Settings
from product_search.db import (
    create_run,
    fetch_keywords,
    fetch_recent_products_for_record,
    finish_run,
    insert_candidate,
    insert_product,
)
from product_search.discovery import extract_detail_candidates_from_discovery_page
from product_search.models import (
    CandidateDiagnostic,
    CandidateLink,
    ParsedProductPage,
    ProductResult,
    ProductSearchInput,
    ProductSearchOutput,
    QualityDecision,
)
from product_search.parser import parse_product_page
from product_search.platforms import (
    build_search_query_plans,
    canonicalize_product_url,
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
    "控制板",
    "上控板",
    "主板",
    "电路板",
    "显示屏控制板",
    "屏幕控制板",
    "屏幕总成",
    "液晶屏",
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
    "扫地机器人": (
        "扫地机器人",
        "扫拖机器人",
        "扫地机",
        "扫拖机",
        "robotvacuum",
        "roboticvacuum",
        "vacuumrobot",
    ),
    "健身车": (
        "健身车",
        "动感单车",
        "脚踏车",
        "健身单车",
        "exercisebike",
        "stationarybike",
        "spinningbike",
        "spinbike",
    ),
    "动感单车": (
        "动感单车",
        "健身车",
        "健身单车",
        "exercisebike",
        "stationarybike",
        "spinningbike",
        "spinbike",
    ),
    "脚踏车": (
        "脚踏车",
        "健身车",
        "动感单车",
        "健身单车",
        "exercisebike",
        "stationarybike",
        "spinningbike",
        "spinbike",
    ),
    "跑步机": (
        "跑步机",
        "走步机",
        "treadmill",
        "inclinetrainer",
        "runner",
    ),
    "计时器": (
        "计时器",
        "定时器",
        "timer",
        "countdowntimer",
        "kitchentimer",
        "cubetimer",
        "pomodorotimer",
    ),
    "定时器": (
        "定时器",
        "计时器",
        "timer",
        "countdowntimer",
        "kitchentimer",
        "cubetimer",
        "pomodorotimer",
    ),
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
    "音箱": (
        "音箱",
        "音响",
        "扬声器",
        "蓝牙音箱",
        "speaker",
        "bluetoothspeaker",
        "neckspeaker",
        "wearablespeaker",
    ),
    "音响": (
        "音响",
        "音箱",
        "扬声器",
        "蓝牙音箱",
        "speaker",
        "bluetoothspeaker",
        "neckspeaker",
        "wearablespeaker",
    ),
    "耳机": (
        "耳机",
        "蓝牙耳机",
        "neckband",
        "headphones",
        "earphones",
        "earbuds",
    ),
    "水枪": (
        "水枪",
        "watergun",
        "waterguns",
        "squirtgun",
        "squirtguns",
        "waterblaster",
    ),
}


REQUIRED_QUALIFIER_GROUPS = (
    (
        "佩戴形态",
        ("颈挂", "颈戴", "挂脖", "挂颈", "穿戴", "可穿戴"),
        ("颈挂", "颈戴", "挂脖", "挂颈", "项圈", "穿戴", "可穿戴", "neckband", "wearable"),
    ),
    (
        "多面/翻转形态",
        ("多边形", "多面体", "立方体", "六面", "六边形", "翻转", "翻面"),
        ("多边形", "多面体", "立方体", "六面", "六边形", "翻转", "翻面", "重力感应", "cube", "cubic", "polygon"),
    ),
    (
        "盲文/触觉辅助",
        ("盲文", "盲人", "视障", "触感", "触摸", "可触摸"),
        ("盲文", "盲人", "视障", "语音", "报时", "凸点", "触感", "触摸", "可触摸", "braille", "tactile"),
    ),
    (
        "儿童/玩具用途",
        ("儿童", "玩具"),
        ("儿童", "玩具", "亲子", "孩子", "小孩", "宝宝", "戏水", "夏季", "toy", "kids", "children"),
    ),
    (
        "健身/训练用途",
        ("健身", "训练", "运动"),
        ("健身", "训练", "运动", "锻炼", "室内", "家用", "exercise", "fitness", "workout", "stationary", "indoor", "gym"),
    ),
    (
        "屏幕/影像显示",
        ("影像", "显示", "视频", "跟练", "屏幕", "显影"),
        ("影像", "显示", "视频", "屏幕", "显示屏", "大屏", "跟练", "课程", "app", "智能屏", "display", "screen", "video"),
    ),
    (
        "可调角",
        ("调角", "可调角"),
        ("调角", "可调角", "角度", "光束角", "可调", "调节", "转向", "旋转", "tilt", "adjustable"),
    ),
    (
        "角度/重力感应",
        ("角度感应", "重力感应"),
        ("角度感应", "重力感应", "重力", "感应", "翻转", "翻面", "gravity", "sensor", "flip"),
    ),
    (
        "免工具/免拆",
        ("免拆", "免工具"),
        ("免拆", "免工具", "无需拆卸", "无需工具", "不用拆", "不用工具", "tool-free", "toolfree", "toolless"),
    ),
    (
        "调光/亮度",
        ("调光", "亮度"),
        ("调光", "亮度", "调亮", "调暗", "dimming", "dimmer", "brightness"),
    ),
    (
        "拖地/扫拖",
        ("拖地", "扫拖", "刷地", "洗拖", "拖布"),
        ("拖地", "扫拖", "拖布", "洗拖", "刷地", "拖擦", "mop", "mopping"),
    ),
    (
        "升降/抬升",
        ("升降", "抬升", "提升"),
        ("升降", "抬升", "提升", "自动抬升", "拖布抬升", "lift", "lifting"),
    ),
    (
        "杀菌/消毒",
        ("杀菌", "消毒", "uv", "紫外"),
        ("杀菌", "消毒", "除菌", "抗菌", "uv", "紫外", "sterili", "disinfect"),
    ),
)


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
            fallback_diagnostics: list[CandidateDiagnostic] = []
            if not products:
                refresh_limit = min(payload.max_products, self.settings.historical_refresh_limit)
                historical_products = fetch_recent_products_for_record(
                    payload.patent_record_id,
                    max(refresh_limit * 3, refresh_limit),
                )
                historical_products = self._filter_historical_products_by_keywords(
                    historical_products, keywords
                )
                historical_products = self._rank_historical_fallback_products(historical_products)
                products = await self._refresh_historical_products(
                    historical_products[:refresh_limit],
                    max_products=refresh_limit,
                )
                products = self._filter_historical_products_by_keywords(products, keywords)
                products = self._rank_historical_fallback_products(products)
                if products:
                    fallback_diagnostics.append(
                        CandidateDiagnostic(
                            keyword=", ".join(keywords[:3]),
                            platform=products[0].platform,
                            candidate_url=products[0].product_url,
                            final_url=products[0].final_url,
                            title=products[0].product_name,
                            status="accepted",
                            rejection_reason="实时搜索未得到 accepted 商品，复用同记录历史成功商品",
                            quality_score=products[0].quality_score,
                            raw_payload={
                                "historical_fallback": True,
                                "product": products[0].model_dump(),
                            },
                        )
                    )
            diagnostics = search_diagnostics + candidate_filter_diagnostics + detail_diagnostics + fallback_diagnostics
            products = self._dedupe_products(products)
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
                original_keyword=candidate.original_keyword or candidate.keyword,
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
        validation_keyword = _normalize_relevance_text(candidate.original_keyword or candidate.keyword)
        haystack = _normalize_relevance_text(f"{candidate.title} {candidate.snippet}")
        if not keyword or not haystack:
            return 0.0
        chars = [char for char in dict.fromkeys(keyword) if "\u4e00" <= char <= "\u9fff"]
        char_ratio = sum(1 for char in chars if char in haystack) / max(1, len(chars))
        bigrams = self._keyword_bigrams(keyword)
        bigram_ratio = sum(1 for token in bigrams if token in haystack) / max(1, len(bigrams))
        qualifier_score = self._qualifier_sort_score(validation_keyword, haystack)
        return round(char_ratio + bigram_ratio * 1.5 + qualifier_score, 4)

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
            render_attempts: list[dict[str, object]] = []
            for _attempt in range(self.settings.render_retry_attempts + 1):
                if not self._should_retry_render(best_fetch, best_decision):
                    break
                render_fetch = await self.client.fetch_detail_page(candidate.candidate_url, force_render=True)
                render_parsed = parse_product_page(render_fetch.html, candidate.candidate_url, render_fetch.final_url)
                render_decision = evaluate_product_detail(render_fetch, render_parsed)
                render_attempts.append(
                    {
                        "provider": render_fetch.provider,
                        "final_url": render_fetch.final_url,
                        "ok": render_fetch.ok,
                        "image_count": len(render_parsed.picture),
                        "quality_score": render_decision.score,
                        "accepted": render_decision.accepted,
                        "capture_meta": render_fetch.capture_meta,
                        "error_message": render_fetch.error_message,
                    }
                )
                render_rank = (
                    1 if render_decision.accepted else 0,
                    len(render_parsed.picture),
                    render_decision.score,
                    len(render_parsed.description),
                )
                best_rank = (
                    1 if best_decision.accepted else 0,
                    len(best_parsed.picture),
                    best_decision.score,
                    len(best_parsed.description),
                )
                if render_rank > best_rank:
                    best_fetch = render_fetch
                    best_parsed = render_parsed
                    best_decision = render_decision
                if render_fetch.provider == "local_browser_stable" or (
                    render_decision.accepted
                    and len(render_parsed.picture) >= self.settings.incomplete_image_threshold
                ):
                    break
                await asyncio.sleep(0.8)
            fetch = best_fetch
            parsed = best_parsed
            decision = self._apply_keyword_relevance(
                candidate.keyword,
                parsed,
                best_decision,
                candidate.original_keyword or candidate.keyword,
            )
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
                        "render_attempts": render_attempts,
                    },
                )
            )
            if not decision.accepted:
                return

            product = ProductResult(
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
                    "render_attempts": render_attempts,
                },
            )
            product_key = self._product_dedupe_key(product) or final_url
            if product_key in accepted_by_url:
                accepted_by_url[product_key] = self._merge_duplicate_products(accepted_by_url[product_key], product)
                return
            accepted_by_url[product_key] = product

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

    def _rank_historical_fallback_products(self, products: list[ProductResult]) -> list[ProductResult]:
        return sorted(products, key=self._historical_fallback_rank_key, reverse=True)

    def _filter_historical_products_by_keywords(
        self,
        products: list[ProductResult],
        keywords: list[str],
    ) -> list[ProductResult]:
        matched_products: list[ProductResult] = []
        for product in products:
            prior_keywords = {
                _normalize_relevance_text(keyword)
                for keyword in product.matched_keywords
                if _normalize_relevance_text(keyword)
            }
            parsed = ParsedProductPage(
                product_id=product.product_id,
                platform=product.platform,
                product_name=product.product_name,
                product_url=product.product_url,
                final_url=product.final_url,
                description=product.description,
                detail_text=product.detail_text,
                picture=product.picture,
            )
            matched_keywords: list[str] = []
            for keyword in keywords:
                if _normalize_relevance_text(keyword) in prior_keywords:
                    matched_keywords.append(keyword)
                    continue
                decision = QualityDecision(
                    accepted=True,
                    score=product.quality_score,
                    status="accepted",
                    flags={},
                )
                decision = self._apply_keyword_relevance(keyword, parsed, decision, keyword)
                if decision.accepted:
                    matched_keywords.append(keyword)
            if not matched_keywords:
                continue
            copied = product.model_copy(deep=True)
            copied.matched_keywords = matched_keywords
            copied.quality_flags = {
                **dict(copied.quality_flags),
                "historical_keyword_revalidated": True,
                "historical_matched_keywords": matched_keywords,
            }
            matched_products.append(copied)
        return matched_products

    def _historical_fallback_rank_key(self, product: ProductResult) -> tuple[int, int, int, int, int, int]:
        pictures = self._filter_obvious_non_product_pictures(product.picture)
        url = product.final_url or product.product_url
        platform = product.platform or detect_platform(url)
        try:
            historical_row_id = int((product.quality_flags or {}).get("historical_product_row_id") or 0)
        except (TypeError, ValueError):
            historical_row_id = 0
        return (
            1 if len(pictures) >= 2 else 0,
            min(len(pictures), 20),
            1 if is_detail_url(url, platform) else 0,
            int(product.quality_score or 0),
            1 if (product.description or product.detail_text) else 0,
            historical_row_id,
        )

    async def _refresh_historical_products(
        self,
        products: list[ProductResult],
        max_products: int | None = None,
    ) -> list[ProductResult]:
        target_count = max(1, max_products or len(products) or 1)
        candidates = products[: min(len(products), self.settings.historical_refresh_limit)]
        semaphore = asyncio.Semaphore(self.settings.historical_refresh_concurrency)

        async def refresh_one(product: ProductResult) -> ProductResult | None:
            async with semaphore:
                return await self._refresh_historical_product(product)

        results = await asyncio.gather(
            *(refresh_one(product) for product in candidates),
            return_exceptions=True,
        )
        refreshed: list[ProductResult] = []
        for result in results:
            if isinstance(result, ProductResult):
                refreshed.append(result)
                if len(refreshed) >= target_count:
                    break
        return refreshed

    async def _refresh_historical_product(self, product: ProductResult) -> ProductResult | None:
        refreshed_product = product.model_copy(deep=True)
        refreshed_product.picture = self._filter_obvious_non_product_pictures(refreshed_product.picture)
        url = refreshed_product.final_url or refreshed_product.product_url
        if not url:
            if self._is_obvious_invalid_historical_product(refreshed_product):
                return None
            return refreshed_product

        raw_payload = dict(refreshed_product.raw_payload)
        if self._is_non_detail_or_aggregate_historical_product(refreshed_product):
            raw_payload["historical_refresh"] = {
                "status": "skipped",
                "reason": "历史商品 URL 不是可接受的商品详情页",
            }
            refreshed_product.raw_payload = raw_payload
            return None
        try:
            fetch = await self.client.fetch_detail_page(url)
        except Exception as exc:
            raw_payload["historical_refresh"] = {
                "status": "error",
                "reason": _error_message(exc),
            }
            refreshed_product.raw_payload = raw_payload
            if self._is_obvious_invalid_historical_product(refreshed_product):
                return None
            if self._is_non_detail_or_aggregate_historical_product(refreshed_product):
                return None
            return refreshed_product

        if not fetch.ok:
            raw_payload["historical_refresh"] = {
                "status": "error",
                "reason": fetch.error_message or "详情页抓取失败",
                "provider": fetch.provider,
            }
            refreshed_product.raw_payload = raw_payload
            if self._is_obvious_invalid_historical_product(refreshed_product):
                return None
            if self._is_non_detail_or_aggregate_historical_product(refreshed_product):
                return None
            return refreshed_product

        parsed = parse_product_page(fetch.html, url, fetch.final_url)
        quality = evaluate_product_detail(fetch, parsed)
        if self._should_retry_render(fetch, quality):
            try:
                render_fetch = await self.client.fetch_detail_page(url, force_render=True)
                render_parsed = parse_product_page(
                    render_fetch.html, url, render_fetch.final_url
                )
                render_quality = evaluate_product_detail(render_fetch, render_parsed)
                current_rank = (
                    1 if quality.accepted else 0,
                    len(parsed.picture),
                    quality.score,
                    len(parsed.description),
                )
                render_rank = (
                    1 if render_quality.accepted else 0,
                    len(render_parsed.picture),
                    render_quality.score,
                    len(render_parsed.description),
                )
                if render_rank > current_rank:
                    fetch, parsed, quality = render_fetch, render_parsed, render_quality
            except Exception as exc:
                raw_payload["historical_render_error"] = _error_message(exc)
        refreshed_final_url = parsed.final_url or fetch.final_url or refreshed_product.final_url
        refreshed_platform = parsed.platform or refreshed_product.platform
        if not is_detail_url(refreshed_final_url, refreshed_platform):
            raw_payload["historical_refresh"] = {
                "status": "skipped",
                "reason": "刷新结果不是商品详情页，保留历史商品 URL 和已净化图片",
                "provider": fetch.provider,
                "final_url": refreshed_final_url,
            }
            refreshed_product.raw_payload = raw_payload
            if self._is_obvious_invalid_historical_product(refreshed_product):
                return None
            if self._is_non_detail_or_aggregate_historical_product(refreshed_product):
                return None
            if self._is_stale_incomplete_jd_historical_product(refreshed_product):
                return None
            return refreshed_product

        refreshed_product.final_url = refreshed_final_url
        refreshed_product.product_url = refreshed_product.product_url or refreshed_product.final_url
        if parsed.product_id:
            refreshed_product.product_id = parsed.product_id
        if parsed.platform:
            refreshed_product.platform = parsed.platform
        if parsed.product_name:
            refreshed_product.product_name = parsed.product_name
        if parsed.price:
            refreshed_product.price = parsed.price
        if parsed.sales:
            refreshed_product.sales = parsed.sales
        if parsed.brand:
            refreshed_product.brand = parsed.brand
        if parsed.manufacturer:
            refreshed_product.manufacturer = parsed.manufacturer
        if parsed.description:
            refreshed_product.description = parsed.description
        if parsed.detail_text:
            refreshed_product.detail_text = parsed.detail_text
        refreshed_product.picture = parsed.picture
        raw_payload["historical_refresh"] = {
            "status": "ok" if quality.accepted else "rejected",
            "provider": fetch.provider,
            "picture_count_before": len(product.picture),
            "picture_count_after": len(refreshed_product.picture),
            "quality": quality.model_dump(),
        }
        refreshed_product.raw_payload = raw_payload
        if not quality.accepted:
            return None
        refreshed_product.quality_score = quality.score
        refreshed_product.quality_flags = {
            **dict(refreshed_product.quality_flags),
            **quality.flags,
            "historical_fallback": True,
            "historical_refresh_validated": True,
        }
        if self._is_obvious_invalid_historical_product(refreshed_product):
            return None
        return refreshed_product

    def _filter_obvious_non_product_pictures(self, pictures: list[str]) -> list[str]:
        filtered: list[str] = []
        blocked_url_tokens = (
            "sprite",
            "icon",
            "logo",
            "avatar",
            "qrcode",
            "qr-code",
            "wechat",
            "weixin",
            "jcm.jd.com/pre",
            "/etc/designs/",
            "/footer/",
            "/themes/default/assets/",
            "/theme/default/assets/",
            "/public/base/public/",
            "/project/cmsweb/suning/public/base/",
            "/project/pdsweb/",
            "/pdsweb/csspc",
            "/pds-web/project/",
            "about-sony-close",
        )
        blocked_file_prefixes = (
            "search.",
            "search-",
            "search_",
            "cart.",
            "cart-",
            "cart_",
            "nav.",
            "nav-",
            "menu.",
            "menu-",
            "user.",
            "user-",
            "order.",
            "order-",
            "coupon.",
            "coupon-",
            "chat.",
            "chat-",
            "chat2.",
            "service.",
            "service-",
            "snms.",
            "snms-",
            "blank.",
            "blank-",
            "blank_pic",
            "loading.",
            "loading-",
            "placeholder.",
            "placeholder-",
            "trend.",
            "trend-",
        )
        blocked_file_exact = (
            "er.png",
            "er.jpg",
            "er.jpeg",
            "er.webp",
            "ewm.png",
            "ewm.jpg",
            "ewm.jpeg",
            "ewm.webp",
        )
        blocked_file_substrings = (
            "shopping-cart",
            "kefu",
            "nationalemblem",
            "erweima",
            "ewm",
            "about-sony-close",
            "new_people",
            "gend-finish",
            "return-process",
            "tmreturn-process",
            "\u70b9\u8d5e",
        )
        for picture in pictures:
            cleaned = str(picture or "").strip()
            if not cleaned:
                continue
            lower = cleaned.lower()
            if "{" in lower or "}" in lower:
                continue
            lower_file = lower.split("?", 1)[0].rsplit("/", 1)[-1]
            if any(token in lower for token in blocked_url_tokens):
                continue
            if lower_file in blocked_file_exact:
                continue
            if lower_file.startswith(blocked_file_prefixes) or any(
                token in lower_file for token in blocked_file_substrings
            ):
                continue
            if cleaned not in filtered:
                filtered.append(cleaned)
        return filtered

    def _is_stale_incomplete_jd_historical_product(self, product: ProductResult) -> bool:
        url = (product.final_url or product.product_url or "").lower()
        platform = (product.platform or detect_platform(url)).lower()
        if platform != "jd" and "item.jd.com/" not in url and "item.m.jd.com/product/" not in url:
            return False
        pictures = self._filter_obvious_non_product_pictures(product.picture)
        if len(pictures) >= 2:
            return False
        return True

    def _is_non_detail_or_aggregate_historical_product(self, product: ProductResult) -> bool:
        url = (product.final_url or product.product_url or "").strip()
        if not url:
            return False
        platform = product.platform or detect_platform(url)
        return is_aggregate_url(url) or not is_detail_url(url, platform)

    def _is_obvious_invalid_historical_product(self, product: ProductResult) -> bool:
        title = (product.product_name or "").strip()
        if not title:
            return False
        raw_title = title.lower().replace(" ", "")
        if raw_title in {"amazon.com", "amazoncom", "amazon"}:
            return True
        normalized_title = _normalize_relevance_text(title).lower()
        if normalized_title in {"amazoncom", "amazon"}:
            return True
        invalid_title_tokens = (
            "产品找不到",
            "商品找不到",
            "直接发需求",
            "提交需求",
            "notfound",
            "pagenotfound",
        )
        return any(token in normalized_title for token in invalid_title_tokens)

    def _dedupe_products(self, products: list[ProductResult]) -> list[ProductResult]:
        merged_by_key: dict[str, ProductResult] = {}
        order: list[str] = []
        for product in products:
            key = self._product_dedupe_key(product)
            if not key:
                key = f"row:{len(order)}"
            if key not in merged_by_key:
                merged_by_key[key] = product
                order.append(key)
                continue
            merged_by_key[key] = self._merge_duplicate_products(merged_by_key[key], product)
        return [merged_by_key[key] for key in order]

    def _product_dedupe_key(self, product: ProductResult) -> str:
        platform = (product.platform or "").strip().lower()
        product_id = (product.product_id or "").strip()
        if platform and product_id:
            return f"id:{platform}:{product_id}"
        for url in (product.final_url, product.product_url):
            canonical_url = canonicalize_product_url(url)
            if canonical_url:
                return f"url:{canonical_url}"
        title = _normalize_relevance_text(product.product_name)
        if title:
            return f"title:{platform}:{title}"
        return ""

    def _merge_duplicate_products(self, existing: ProductResult, incoming: ProductResult) -> ProductResult:
        primary = existing if existing.quality_score >= incoming.quality_score else incoming
        secondary = incoming if primary is existing else existing

        keywords: list[str] = []
        for keyword in [*existing.matched_keywords, *incoming.matched_keywords]:
            cleaned = " ".join(str(keyword or "").split())
            if cleaned and cleaned not in keywords:
                keywords.append(cleaned)

        pictures: list[str] = []
        for picture in [*existing.picture, *incoming.picture]:
            cleaned = str(picture or "").strip()
            if cleaned and cleaned not in pictures:
                pictures.append(cleaned)

        flags = dict(primary.quality_flags)
        deduped_variants = list(flags.get("deduped_variants") or [])
        deduped_variants.append(
            {
                "product_name": secondary.product_name,
                "product_url": secondary.product_url,
                "final_url": secondary.final_url,
                "matched_keywords": secondary.matched_keywords,
                "quality_score": secondary.quality_score,
            }
        )
        flags["deduped_variants"] = deduped_variants[:20]

        raw_payload = dict(primary.raw_payload)
        deduped_sources = list(raw_payload.get("deduped_sources") or [])
        deduped_sources.append(
            {
                "product_name": secondary.product_name,
                "product_url": secondary.product_url,
                "final_url": secondary.final_url,
                "matched_keywords": secondary.matched_keywords,
            }
        )
        raw_payload["deduped_sources"] = deduped_sources[:20]

        return primary.model_copy(
            update={
                "matched_keywords": keywords,
                "picture": pictures or primary.picture,
                "quality_flags": flags,
                "raw_payload": raw_payload,
            }
        )

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
        render_available = (
            self.settings.browser_fallback_enabled
            or (self.settings.brightdata_api_key and self.settings.render_fallback_enabled)
        )
        if not render_available:
            return False
        if fetch.provider == "local_browser_stable":
            return False
        if fetch.provider == "brightdata_unlocker_render" and (fetch.capture_meta or {}).get("local_browser_attempted"):
            return False
        platform = str((getattr(decision, "flags", {}) or {}).get("platform") or "")
        if platform in {"jd", "taobao", "tmall", "1688", "pdd", "suning"}:
            return True
        page_image_count = 0
        try:
            page_image_count = int((getattr(decision, "flags", {}) or {}).get("image_count") or 0)
        except (TypeError, ValueError):
            page_image_count = 0
        reasons = " ".join(decision.reasons)
        incomplete = page_image_count < self.settings.incomplete_image_threshold
        return incomplete or any(
            token in reasons for token in ("未提取到商品标题", "商品详情文本过短", "未提取到商品图片")
        )

    def _should_retry_fetch_error(self, fetch) -> bool:
        render_available = (
            self.settings.browser_fallback_enabled
            or (self.settings.brightdata_api_key and self.settings.render_fallback_enabled)
        )
        if not render_available:
            return False
        provider = str(getattr(fetch, "provider", "") or "")
        if provider == "brightdata_unlocker_render":
            return True
        return provider.startswith("brightdata_unlocker")

    def _apply_keyword_relevance(self, keyword: str, parsed, decision, original_keyword: str = ""):
        cleaned_keyword = _normalize_relevance_text(keyword)
        validation_keyword = _normalize_relevance_text(original_keyword or keyword)
        if not decision.accepted:
            return decision
        haystack = _normalize_relevance_text(f"{parsed.product_name} {parsed.description}")
        if not haystack:
            return decision
        decision.flags["matched_keyword"] = keyword
        decision.flags["validation_keyword"] = original_keyword or keyword
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
        missing_qualifiers = self._missing_required_qualifier_groups(validation_keyword, haystack)
        if missing_qualifiers:
            decision.accepted = False
            decision.status = "rejected"
            decision.flags["missing_required_qualifiers"] = missing_qualifiers
            decision.reasons.append("商品详情缺少原始关键词必要限定词: " + "、".join(missing_qualifiers))
            return decision
        if self._fitness_bike_transport_mismatch(validation_keyword, parsed.product_name, haystack):
            decision.accepted = False
            decision.status = "rejected"
            decision.reasons.append("商品标题/详情显示为出行代步自行车，不是健身脚踏车/健身车本体")
            return decision
        if self._core_product_missing_title_signal(cleaned_keyword, f"{parsed.product_name} {parsed.description[:500]}"):
            decision.accepted = False
            decision.status = "rejected"
            decision.reasons.append("商品页面未显示关键词对应的核心商品本体")
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
        qualifier_score = self._qualifier_sort_score(validation_keyword, haystack)
        has_required_qualifier = self._has_required_qualifier_trigger(validation_keyword)
        decision.flags["matched_keyword_ratio"] = round(ratio, 3)
        decision.flags["matched_keyword_bigram_ratio"] = round(bigram_ratio, 3)
        allow_sparse_bigram = has_core_object_signal and (
            ratio >= 0.85
            or ("充电站" in cleaned_keyword and ratio >= 0.58)
            or (("马达驱动芯片" in cleaned_keyword or "驱动芯片" in cleaned_keyword) and ratio >= 0.58)
            or (has_required_qualifier and qualifier_score > 0)
        )
        if (ratio < 0.58 and not allow_sparse_bigram) or (
            len(cleaned_keyword) >= 5
            and bigrams
            and bigram_ratio < 0.35
            and not allow_sparse_bigram
        ):
            decision.accepted = False
            decision.status = "rejected"
            decision.reasons.append(f"商品详情与检索关键词连续片段覆盖度不足: 字符={ratio:.2f}, 片段={bigram_ratio:.2f}")
        return decision

    def _missing_required_qualifier_groups(self, validation_keyword: str, haystack: str) -> list[str]:
        missing: list[str] = []
        for label, triggers, accepted_terms in REQUIRED_QUALIFIER_GROUPS:
            if not any(trigger in validation_keyword for trigger in triggers):
                continue
            if any(term in haystack for term in accepted_terms):
                continue
            missing.append(label)
        return missing

    def _has_required_qualifier_trigger(self, validation_keyword: str) -> bool:
        return any(
            trigger in validation_keyword
            for _label, triggers, _accepted_terms in REQUIRED_QUALIFIER_GROUPS
            for trigger in triggers
        )

    def _qualifier_sort_score(self, validation_keyword: str, haystack: str) -> float:
        score = 0.0
        for _label, triggers, accepted_terms in REQUIRED_QUALIFIER_GROUPS:
            if not any(trigger in validation_keyword for trigger in triggers):
                continue
            if any(term in haystack for term in accepted_terms):
                score += 1.0
            else:
                score -= 0.75
        return score

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

    def _core_product_missing_title_signal(self, cleaned_keyword: str, product_name: str) -> bool:
        title = _normalize_relevance_text(product_name)
        if not title:
            return False
        for core_term in CORE_PRODUCT_TERMS:
            if core_term not in cleaned_keyword:
                continue
            if core_term == "高尔夫球" and "充电站" in cleaned_keyword:
                continue
            aliases = CORE_OBJECT_ALIAS_TERMS.get(core_term, (core_term,))
            if any(alias in title for alias in aliases):
                continue
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

    def _fitness_bike_transport_mismatch(self, validation_keyword: str, product_name: str, haystack: str) -> bool:
        if not any(term in validation_keyword for term in ("健身", "训练", "锻炼")):
            return False
        if not any(term in validation_keyword for term in ("脚踏车", "健身车", "动感单车", "健身单车")):
            return False
        title = _normalize_relevance_text(product_name)
        if not title:
            return False
        fitness_title_terms = (
            "健身车",
            "动感单车",
            "健身单车",
            "室内单车",
            "室内健身",
            "脚踏健身",
            "exercisebike",
            "stationarybike",
            "spinningbike",
            "spinbike",
        )
        if any(term in title for term in fitness_title_terms):
            return False
        transport_terms = (
            "代步",
            "通勤",
            "长途旅行",
            "旅行",
            "助力自行车",
            "电动助力",
            "锂电",
            "上牌",
            "公路车",
            "山地车",
            "骑行装备",
            "户外骑行",
            "城市骑行",
        )
        return any(term in title or term in haystack for term in transport_terms)

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
