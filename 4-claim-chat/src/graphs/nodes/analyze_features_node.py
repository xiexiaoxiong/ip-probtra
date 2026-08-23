import os
import re
import json
import logging
import time
from typing import List, Dict, Any
from urllib.parse import urlparse
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import AnalyzeFeaturesInput, AnalyzeFeaturesOutput
from utils.claim_scoring import build_feature_segments, count_effective_units
from utils.local_llm import invoke_local_llm

logger = logging.getLogger(__name__)


_NON_PRODUCT_IMAGE_URL_TOKENS = (
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

_NON_PRODUCT_IMAGE_FILE_PREFIXES = (
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

_NON_PRODUCT_IMAGE_FILE_EXACT = (
    "er.png",
    "er.jpg",
    "er.jpeg",
    "er.webp",
    "ewm.png",
    "ewm.jpg",
    "ewm.jpeg",
    "ewm.webp",
)

_NON_PRODUCT_IMAGE_FILE_SUBSTRINGS = (
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
    "点赞",
)


def _is_non_product_image_url(value: Any) -> bool:
    lower = str(value or "").strip().lower()
    if not lower or lower.startswith("data:"):
        return True
    if "{" in lower or "}" in lower:
        return True
    parsed = urlparse(lower)
    lower_file = parsed.path.rsplit("/", 1)[-1]
    if any(token in lower for token in _NON_PRODUCT_IMAGE_URL_TOKENS):
        return True
    if lower_file in _NON_PRODUCT_IMAGE_FILE_EXACT:
        return True
    if lower_file.startswith(_NON_PRODUCT_IMAGE_FILE_PREFIXES):
        return True
    if any(token in lower_file for token in _NON_PRODUCT_IMAGE_FILE_SUBSTRINGS):
        return True
    return False


def _filter_non_product_image_urls(images: Any) -> List[str]:
    if not isinstance(images, list):
        return []
    filtered: List[str] = []
    for image in images:
        if not isinstance(image, str):
            continue
        cleaned = image.strip()
        if not cleaned or _is_non_product_image_url(cleaned):
            continue
        if cleaned not in filtered:
            filtered.append(cleaned)
    return filtered


def _coerce_score(value: Any, default: int = 50) -> int:
    try:
        score = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(0, min(100, score))


def _feature_key(feature: Dict[str, Any]) -> tuple[str, str, str]:
    feature_id = str(feature.get("feature_id", "")).strip()
    claim_id = str(feature.get("claim_id", "")).strip()
    feature_text = str(feature.get("feature_text", "")).strip()
    return (claim_id, feature_id, feature_text)


def _normalize_token_units(item: Dict[str, Any], feature_segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    raw_units = item.get("token_units", [])
    if not isinstance(raw_units, list):
        raw_units = []

    segment_map = {
        str(segment.get("text", "")).strip(): segment
        for segment in feature_segments
        if isinstance(segment, dict) and str(segment.get("text", "")).strip()
    }

    normalized_units: List[Dict[str, Any]] = []
    for unit in raw_units:
        if not isinstance(unit, dict):
            continue
        text = str(unit.get("text", "")).strip()
        if not text:
            continue
        segment = segment_map.get(text, {})
        normalized_units.append({
            "text": text,
            "unit_status": _normalize_unit_status(unit.get("unit_status", "uncertain")),
            "evidence": str(unit.get("evidence", "")),
            "reason": str(unit.get("reason", "")),
            "start": int(segment.get("start", 0) or 0),
            "end": int(segment.get("end", 0) or 0),
            "effective_length": int(segment.get("effective_length", count_effective_units(text)) or 0),
        })

    if normalized_units:
        return normalized_units

    fallback_units: List[Dict[str, Any]] = []
    for segment in feature_segments or build_feature_segments(str(item.get("feature_text", ""))):
        fallback_units.append({
            "text": str(segment.get("text", "")),
            "unit_status": "uncertain",
            "evidence": "",
            "reason": "模型未返回该单元判断结果",
            "start": int(segment.get("start", 0) or 0),
            "end": int(segment.get("end", 0) or 0),
            "effective_length": int(segment.get("effective_length", 0) or 0),
        })
    return fallback_units


_UNIT_STATUS_MAPPING: Dict[str, str] = {
    "match": "match",
    "matching": "match",
    "相同": "match",
    "相同特征": "match",
    "明确相同": "match",
    "mismatch": "mismatch",
    "not_match": "mismatch",
    "不相同": "mismatch",
    "不同": "mismatch",
    "明确不相同": "mismatch",
    "uncertain": "uncertain",
    "待确认": "uncertain",
    "不确定": "uncertain",
}


def _normalize_unit_status(value: Any) -> str:
    """
    把 LLM 给出的 unit_status 字符串归一为 match / mismatch / uncertain。
    任何缺失/为空/未识别的值一律映射为 uncertain，禁止从特征级 reasoning_type
    推断单元级状态。
    """
    if value is None:
        return "uncertain"
    raw = str(value).strip().lower()
    if not raw:
        return "uncertain"
    if raw in _UNIT_STATUS_MAPPING:
        return _UNIT_STATUS_MAPPING[raw]
    # 中文未小写时按原文再查一次
    raw_orig = str(value).strip()
    if raw_orig in _UNIT_STATUS_MAPPING:
        return _UNIT_STATUS_MAPPING[raw_orig]
    return "uncertain"


_RULE_FALLBACK_STOPWORDS = {
    "一种", "包括", "设置", "连接", "安装", "固定", "用于", "通过", "以及", "所述",
    "具有", "形成", "进行", "实现", "可以", "能够", "产品", "装置", "机构", "组件",
}


def _limit_prompt_text(value: Any, env_key: str, default_limit: int) -> str:
    text = str(value or "")
    try:
        limit = int(os.getenv(env_key, str(default_limit)) or default_limit)
    except ValueError:
        limit = default_limit
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n[已截断：原文共 {len(text)} 字符，仅保留前 {limit} 字符用于本次模型分析]"


def _analyze_batch_retry_sleep_seconds(attempt: int) -> float:
    try:
        base = float(os.getenv("MODULE4_ANALYZE_BATCH_RETRY_SLEEP_SECONDS", "6") or "6")
    except ValueError:
        base = 6.0
    try:
        cap = float(os.getenv("MODULE4_ANALYZE_BATCH_RETRY_SLEEP_CAP_SECONDS", "30") or "30")
    except ValueError:
        cap = 30.0
    return max(0.0, min(base * max(1, attempt), cap))


def _should_abort_llm_batch_retries(error: Exception | None) -> bool:
    detail = str(error or "")
    lower = detail.lower()
    return (
        "http 429" in lower
        or "rate limit" in lower
        or "rate_limit" in lower
        or "速率限制" in detail
        or "限流" in detail
        or "cooling down" in lower
    )


def _normalize_rule_text(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(text or "")).lower()


def _extract_rule_terms(text: str) -> List[str]:
    terms: List[str] = []
    for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9]{3,}", str(text or "")):
        token = token.strip()
        if not token or token in _RULE_FALLBACK_STOPWORDS:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", token):
            terms.append(token)
            for size in (6, 4, 3, 2):
                if len(token) < size:
                    continue
                for idx in range(0, len(token) - size + 1):
                    part = token[idx:idx + size]
                    if part not in _RULE_FALLBACK_STOPWORDS:
                        terms.append(part)
        elif len(token) > 18:
            for size in (6, 4, 3, 2):
                for idx in range(0, max(0, len(token) - size + 1)):
                    part = token[idx:idx + size]
                    if part not in _RULE_FALLBACK_STOPWORDS:
                        terms.append(part)
        else:
            terms.append(token)
    seen: set[str] = set()
    deduped: List[str] = []
    for term in sorted(terms, key=len, reverse=True):
        normalized = _normalize_rule_text(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(term)
    return deduped[:12]


def _build_rule_fallback_analysis(
    features: List[Dict[str, Any]],
    feature_segments_map: Dict[str, List[Dict[str, Any]]],
    product_name: str,
    product_description: str,
) -> List[Dict[str, Any]]:
    _ = (product_name, product_description)
    raw_analysis: List[Dict[str, Any]] = []

    for feat in features:
        fid = str(feat.get("feature_id", ""))
        ftext = str(feat.get("feature_text", ""))
        segments = feature_segments_map.get(fid, []) or build_feature_segments(ftext)
        token_units: List[Dict[str, Any]] = []
        for segment in segments:
            segment_text = str(segment.get("text", "")).strip()
            token_units.append({
                "text": segment_text,
                "unit_status": "uncertain",
                "evidence": "",
                "reason": "大模型调用失败；规则兜底不得仅凭局部词重合确认完整技术特征",
                "start": int(segment.get("start", 0) or 0),
                "end": int(segment.get("end", 0) or 0),
                "effective_length": int(segment.get("effective_length", count_effective_units(segment_text)) or 0),
            })

        raw_analysis.append({
            "feature_id": fid,
            "evidence": "",
            "reason": "大模型调用失败；规则兜底未作技术特征命中判断，需人工复核。",
            "reasoning_type": "相关信息缺失",
            "claim_id": str(feat.get("claim_id", "")),
            "evidence_images": [],
            "suggested_score": 0,
            "score_band_hint": "低相似",
            "score_rationale": "模型分析失败，规则兜底保持全部判断单元为不确定",
            "token_units": _normalize_token_units({"feature_text": ftext, "token_units": token_units}, segments),
            "analysis_source": "rule_fallback",
            "analysis_failed": True,
        })

    return raw_analysis


def _downgrade_feature_mismatch_inconsistency(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    防御性兜底：若 LLM 在特征级给出 ``可判断不具有``，但其下没有任何
    token_unit 的 unit_status 为 mismatch，说明 LLM 给出了相互矛盾的结论。
    此时把特征级 reasoning_type 降级为 ``相关信息缺失``，并把所有 unit 的
    unit_status 强制为 uncertain，避免后续规则层把所有 segment 标红。
    """
    reasoning_type = str(candidate.get("reasoning_type", ""))
    if reasoning_type != "可判断不具有":
        return candidate

    token_units = candidate.get("token_units", [])
    if not isinstance(token_units, list) or not token_units:
        # 没有 token_units 时本来就全是 uncertain，无需处理
        return candidate

    has_any_mismatch = any(
        isinstance(unit, dict) and str(unit.get("unit_status", "")).strip() == "mismatch"
        for unit in token_units
    )
    if has_any_mismatch:
        return candidate

    logger.warning(
        "特征 %s 特征级 reasoning_type='可判断不具有' 但无任何 unit_status='mismatch' 的单元，"
        "自动降级为'相关信息缺失'并把所有单元置为 uncertain",
        candidate.get("feature_id", ""),
    )
    candidate["reasoning_type"] = "相关信息缺失"
    candidate["reason"] = (
        str(candidate.get("reason", "")).rstrip("。. ") +
        "（特征级结论与单元级判断不一致，已自动降级为待确认）"
    )
    for unit in token_units:
        if isinstance(unit, dict):
            unit["unit_status"] = "uncertain"
            if not str(unit.get("reason", "")).strip():
                unit["reason"] = "未给出明确证据，按待确认处理"
    return candidate


_WEAK_ENRICHMENT_MARKERS = (
    "低置信同品线索",
    "/weak",
    "identity_strength=weak",
    "identity_strength: weak",
    "weak；",
    "weak;",
)

_STRONG_EVIDENCE_MARKERS = (
    "[商品图片OCR]",
    "商品图片OCR",
    "图片OCR",
    "OCR",
    "商品名称",
    "商品图片",
    "图片显示",
    "图片中",
    "从图片",
    "/strong",
    "identity_strength=strong",
    "identity_strength: strong",
    "strong；",
    "strong;",
)


def _has_weak_enrichment_marker(text: str) -> bool:
    return any(marker in text for marker in _WEAK_ENRICHMENT_MARKERS)


def _has_stronger_source_marker(text: str) -> bool:
    return any(marker in text for marker in _STRONG_EVIDENCE_MARKERS)


def _downgrade_weak_enrichment_only_units(candidate: Dict[str, Any]) -> Dict[str, Any]:
    """
    二次检索中仅靠名称相似等弱同品校验进入的网页/文章/视频资料，不能单独支撑
    match。若某个 token_unit 的正向依据只指向 weak enrichment，且没有同时引用
    商品自身名称、图片/OCR或 strong 同品来源，则降级为 uncertain。
    """
    token_units = candidate.get("token_units", [])
    if not isinstance(token_units, list) or not token_units:
        return candidate

    changed = False
    for unit in token_units:
        if not isinstance(unit, dict):
            continue
        if str(unit.get("unit_status", "")).strip() != "match":
            continue

        evidence_text = " ".join([
            str(unit.get("evidence", "")),
            str(unit.get("reason", "")),
            str(candidate.get("evidence", "")),
            str(candidate.get("reason", "")),
        ])
        if not _has_weak_enrichment_marker(evidence_text):
            continue
        if _has_stronger_source_marker(evidence_text):
            continue

        changed = True
        unit["unit_status"] = "uncertain"
        unit["reason"] = (
            str(unit.get("reason", "")).rstrip("。. ")
            + "（该正向判断仅由低置信同品线索支撑，不能单独作为结构确认依据，已降级为待确认）"
        )

    if not changed:
        return candidate

    token_units_after = [
        unit for unit in token_units
        if isinstance(unit, dict)
    ]
    has_match = any(str(unit.get("unit_status", "")).strip() == "match" for unit in token_units_after)
    has_mismatch = any(str(unit.get("unit_status", "")).strip() == "mismatch" for unit in token_units_after)
    if not has_match and not has_mismatch:
        candidate["reasoning_type"] = "相关信息缺失"
        candidate["reason"] = (
            str(candidate.get("reason", "")).rstrip("。. ")
            + "（原正向依据仅来自低置信同品线索，不能单独确认商品结构，已整体降级为待确认）"
        )
    elif not has_match and str(candidate.get("reasoning_type", "")) in (
        "文字直接公开",
        "从图片中看出",
        "结合文字和图片毫无疑义得出",
        "根据功能推导得出",
    ):
        candidate["reasoning_type"] = "相关信息缺失"

    return candidate


def _dedupe_features(features: List[Dict[str, str]]) -> List[Dict[str, str]]:
    deduped: List[Dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for feature in features:
        key = _feature_key(feature)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(feature)
    return deduped


def _build_features_text(
    features: List[Dict[str, Any]],
    feature_segments_map: Dict[str, List[Dict[str, Any]]],
) -> str:
    features_text = ""
    for feat in features:
        fid = str(feat.get("feature_id", ""))
        ftext = str(feat.get("feature_text", ""))
        cid = str(feat.get("claim_id", ""))
        features_text += f"- [{cid}] {fid}: {ftext}\n"
        segments = feature_segments_map.get(fid, [])
        if segments:
            features_text += "  feature_segments（最小判断单元，token_units 必须 1:1 输出）：\n"
            for idx, seg in enumerate(segments, 1):
                seg_text = str(seg.get("text", "")).strip()
                if seg_text:
                    features_text += f"    {idx}. {seg_text}\n"
    return features_text


def _coerce_parsed_feature_list(parsed: Any) -> Any:
    if isinstance(parsed, dict):
        for key in ("analysis", "features", "data", "items", "result"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
    return parsed


def _build_missing_feature_analysis(
    feature: Dict[str, Any],
    feature_segments_map: Dict[str, List[Dict[str, Any]]],
    reason: str = "LLM未返回该特征的分析结果",
) -> Dict[str, Any]:
    fid = str(feature.get("feature_id", ""))
    return {
        "feature_id": fid,
        "evidence": "",
        "reason": reason,
        "reasoning_type": "相关信息缺失",
        "claim_id": str(feature.get("claim_id", "")),
        "evidence_images": [],
        "suggested_score": 20,
        "score_band_hint": "低相似",
        "score_rationale": "模型遗漏该特征，按低相似兜底",
        "token_units": _normalize_token_units(
            {"feature_text": feature.get("feature_text", "")},
            feature_segments_map.get(fid, []),
        ),
    }


def _normalize_parsed_analysis(
    parsed: List[Any],
    features: List[Dict[str, Any]],
    feature_segments_map: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    parsed_ids: set = set()
    valid_analysis: List[Dict[str, Any]] = []
    deduped_analysis_by_feature_id: Dict[str, Dict[str, Any]] = {}
    expected_feature_by_id = {
        str(feature.get("feature_id", "")).strip(): feature
        for feature in features
        if str(feature.get("feature_id", "")).strip()
    }
    expected_id_by_text = {
        str(feature.get("feature_text", "")).strip(): str(feature.get("feature_id", "")).strip()
        for feature in features
        if str(feature.get("feature_text", "")).strip()
        and str(feature.get("feature_id", "")).strip()
    }

    for item in parsed:
        if not isinstance(item, dict):
            continue
        raw_fid = str(item.get("feature_id", "")).strip()
        raw_claim_id = str(item.get("claim_id", "")).strip()
        raw_feature_text = str(item.get("feature_text", "")).strip()
        fid = ""
        if raw_fid in expected_feature_by_id:
            fid = raw_fid
        elif raw_claim_id in expected_feature_by_id:
            fid = raw_claim_id
            logger.warning(
                "LLM返回疑似错位 feature_id/claim_id，已按当前批次修正: raw_feature_id=%s raw_claim_id=%s corrected_feature_id=%s",
                raw_fid,
                raw_claim_id,
                fid,
            )
        elif raw_feature_text in expected_id_by_text:
            fid = expected_id_by_text[raw_feature_text]
            logger.warning(
                "LLM返回未知 feature_id，已按 feature_text 匹配当前批次修正: raw_feature_id=%s corrected_feature_id=%s",
                raw_fid,
                fid,
            )
        else:
            logger.warning(
                "LLM返回了不属于当前批次的 feature_id，已跳过: raw_feature_id=%s raw_claim_id=%s batch_feature_ids=%s",
                raw_fid,
                raw_claim_id,
                sorted(expected_feature_by_id),
            )
            continue

        parsed_ids.add(fid)
        expected_feature = expected_feature_by_id.get(fid, {})
        raw_images = item.get("evidence_images", [])
        evidence_images = _filter_non_product_image_urls(raw_images)
        suggested_score = _coerce_score(item.get("suggested_score"), default=50)

        candidate = {
            "feature_id": fid,
            "evidence": str(item.get("evidence", "")),
            "reason": str(item.get("reason", "")),
            "reasoning_type": str(item.get("reasoning_type", "相关信息缺失")),
            "claim_id": str(expected_feature.get("claim_id", "")) or raw_claim_id,
            "feature_text": str(expected_feature.get("feature_text", "")) or raw_feature_text,
            "evidence_images": evidence_images,
            "suggested_score": suggested_score,
            "score_band_hint": str(item.get("score_band_hint", "")),
            "score_rationale": str(item.get("score_rationale", "")) or str(item.get("reason", "")),
            "token_units": _normalize_token_units(item, feature_segments_map.get(fid, [])),
        }

        existing = deduped_analysis_by_feature_id.get(fid)
        if existing is None:
            deduped_analysis_by_feature_id[fid] = candidate
            continue

        existing_score = (
            1 if existing.get("evidence") else 0,
            1 if existing.get("evidence_images") else 0,
            0 if existing.get("reasoning_type") == "相关信息缺失" else 1,
            len(str(existing.get("reason", ""))),
            int(existing.get("suggested_score", 0)),
            len(existing.get("token_units", [])),
        )
        candidate_score = (
            1 if candidate.get("evidence") else 0,
            1 if candidate.get("evidence_images") else 0,
            0 if candidate.get("reasoning_type") == "相关信息缺失" else 1,
            len(str(candidate.get("reason", ""))),
            int(candidate.get("suggested_score", 0)),
            len(candidate.get("token_units", [])),
        )
        if candidate_score > existing_score:
            deduped_analysis_by_feature_id[fid] = candidate

    for fid, candidate in deduped_analysis_by_feature_id.items():
        candidate = _downgrade_weak_enrichment_only_units(candidate)
        deduped_analysis_by_feature_id[fid] = _downgrade_feature_mismatch_inconsistency(candidate)

    valid_analysis.extend(deduped_analysis_by_feature_id.values())

    for feat in features:
        fid = str(feat.get("feature_id", ""))
        if fid not in parsed_ids:
            valid_analysis.append(_build_missing_feature_analysis(feat, feature_segments_map))

    return valid_analysis


def _build_analysis_messages(
    sp: str,
    user_prompt: str,
    product_images: List[str],
) -> List[Any]:
    if product_images:
        content_parts: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for img_url in product_images:
            if isinstance(img_url, str) and img_url.startswith("http"):
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": img_url}
                })
        return [
            SystemMessage(content=sp),
            HumanMessage(content=content_parts)
        ]

    return [
        SystemMessage(content=sp),
        HumanMessage(content=user_prompt)
    ]


def _extract_json_from_response(content: Any) -> Any:
    """从LLM响应中提取JSON"""
    text: str = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text += str(item.get("text", ""))
            elif isinstance(item, str):
                text += item
    else:
        text = str(content)

    # 尝试从markdown代码块中提取
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if json_match:
        json_str: str = json_match.group(1).strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # 直接解析
    text_stripped: str = text.strip()
    try:
        return json.loads(text_stripped)
    except json.JSONDecodeError:
        pass

    # 找 { } 之间的内容
    start_idx: int = text.find('{')
    end_idx: int = text.rfind('}')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            return json.loads(text[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            pass

    # 部分模型会返回“说明文字 + JSON数组 + 说明文字”。旧逻辑只截取对象，
    # 会把这种有效响应误判为格式错误，最终触发规则兜底。
    array_start_idx: int = text.find('[')
    array_end_idx: int = text.rfind(']')
    if array_start_idx != -1 and array_end_idx != -1 and array_end_idx > array_start_idx:
        try:
            return json.loads(text[array_start_idx:array_end_idx + 1])
        except json.JSONDecodeError:
            pass

    logger.error(f"无法从LLM响应中提取JSON: {text[:200]}")
    return []


def analyze_features_node(
    state: AnalyzeFeaturesInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> AnalyzeFeaturesOutput:
    """
    title: 分析技术特征比对证据
    desc: 使用大语言模型在商品信息中逐一查找每个技术特征的证据（支持多独立权利要求），基于说明书理解特征含义，输出evidence、reason、reasoning_type和claim_id
    integrations: 大语言模型
    """
    ctx = runtime.context
    raw_features: List[Dict[str, str]] = state.features
    features: List[Dict[str, str]] = _dedupe_features(raw_features)
    product_data: Dict[str, Any] = state.product_data

    if not features:
        logger.warning("技术特征列表为空，无法分析")
        return AnalyzeFeaturesOutput(raw_analysis=[], product_name="")

    product_name: str = str(product_data.get("name", ""))
    product_description: str = _limit_prompt_text(
        product_data.get("description", ""),
        "MODULE4_ANALYZE_PRODUCT_DESCRIPTION_LIMIT",
        1200,
    )
    product_images: List[str] = _filter_non_product_image_urls(product_data.get("images", []))
    try:
        image_limit = int(os.getenv("MODULE4_ANALYZE_IMAGE_LIMIT", "4") or "4")
    except ValueError:
        image_limit = 4
    if image_limit >= 0:
        product_images = product_images[:image_limit]

    # 读取配置文件
    cfg_file: str = os.path.join(
        os.getenv("COZE_WORKSPACE_PATH", ""),
        config.get("metadata", {}).get("llm_cfg", "config/analyze_features_llm_cfg.json")
    )
    try:
        with open(cfg_file, 'r', encoding='utf-8') as fd:
            _cfg: dict = json.load(fd)
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        return AnalyzeFeaturesOutput(raw_analysis=[], product_name=product_name)

    llm_config: dict = _cfg.get("config", {})
    sp: str = _cfg.get("sp", "")
    up_template: str = _cfg.get("up", "")

    # 构造特征分段映射，后续按小批量渲染 features_text，避免单次 JSON 过长被截断。
    feature_segments_map: Dict[str, List[Dict[str, Any]]] = {}
    for feat in features:
        fid: str = str(feat.get("feature_id", ""))
        ftext: str = str(feat.get("feature_text", ""))
        feature_segments = feat.get("feature_segments", [])
        feature_segments_map[fid] = feature_segments if isinstance(feature_segments, list) else build_feature_segments(ftext)

    # 从 state 获取说明书文本
    specification_text: str = _limit_prompt_text(
        state.specification_text,
        "MODULE4_ANALYZE_SPECIFICATION_LIMIT",
        400,
    )

    duplicate_count = max(0, len(raw_features) - len(features))
    if duplicate_count:
        logger.warning(
            "商品 '%s' 的输入特征存在重复，已在模型调用前去重: raw=%s, deduped=%s",
            product_name,
            len(raw_features),
            len(features),
        )

    # 构造消息（支持多模态）
    model: str = llm_config.get("model", "glm-4.6v")
    temperature: float = float(llm_config.get("temperature", 0.1))
    configured_max_tokens = int(llm_config.get("max_completion_tokens", 12000))
    max_tokens_cap = int(os.getenv("MODULE4_ANALYZE_MAX_COMPLETION_TOKENS", str(configured_max_tokens)) or configured_max_tokens)
    max_tokens: int = min(configured_max_tokens, max_tokens_cap)

    try:
        batch_size = int(os.getenv("MODULE4_ANALYZE_FEATURE_BATCH_SIZE", "1") or "1")
    except ValueError:
        batch_size = 1
    batch_size = max(1, batch_size)
    try:
        batch_attempts = int(os.getenv("MODULE4_ANALYZE_BATCH_ATTEMPTS", "6") or "6")
    except ValueError:
        batch_attempts = 6
    batch_attempts = max(1, batch_attempts)

    up_tpl: Template = Template(up_template)
    valid_analysis: List[Dict[str, Any]] = []
    for batch_start in range(0, len(features), batch_size):
        batch_features = features[batch_start:batch_start + batch_size]
        batch_ids = [str(feat.get("feature_id", "")) for feat in batch_features]
        features_text = _build_features_text(batch_features, feature_segments_map)
        user_prompt: str = up_tpl.render({
            "features_text": features_text,
            "product_name": product_name,
            "product_description": product_description,
            "product_images": product_images,
            "specification_text": specification_text
        })
        messages = _build_analysis_messages(sp, user_prompt, product_images)

        parsed: Any = None
        last_error: Exception | None = None
        for attempt in range(1, batch_attempts + 1):
            try:
                response = invoke_local_llm(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_completion_tokens=max_tokens
                )
                parsed = _extract_json_from_response(response.content)
                try:
                    rc = response.content
                    preview = rc[:800] if isinstance(rc, str) else str(rc)[:800]
                except Exception:
                    preview = "<unprintable>"
                logger.info(
                    "[DIAG] 商品 '%s' 批次 %s 第%s次 LLM 响应预览: %s | 解析后类型: %s",
                    product_name, batch_ids, attempt, preview, type(parsed).__name__,
                )
                parsed = _coerce_parsed_feature_list(parsed)
                has_feature_items = (
                    isinstance(parsed, list)
                    and any(isinstance(item, dict) and str(item.get("feature_id", "")).strip() for item in parsed)
                )
                if has_feature_items:
                    last_error = None
                    break
                last_error = RuntimeError(f"大模型返回格式不正确或缺少特征项: type={type(parsed)}")
            except Exception as e:
                last_error = e

            if attempt < batch_attempts:
                if _should_abort_llm_batch_retries(last_error):
                    logger.warning(
                        "模型限流或处于冷却期，停止当前特征批次重试: product=%s batch=%s attempt=%s/%s error=%s",
                        product_name, batch_ids, attempt, batch_attempts, last_error,
                    )
                    break
                retry_sleep_seconds = _analyze_batch_retry_sleep_seconds(attempt)
                logger.warning(
                    "分析特征批次失败，准备重试: product=%s batch=%s attempt=%s/%s retry_sleep=%.1fs error=%s",
                    product_name, batch_ids, attempt, batch_attempts, retry_sleep_seconds, last_error,
                )
                if retry_sleep_seconds > 0:
                    time.sleep(retry_sleep_seconds)

        if last_error is not None:
            logger.error(
                "调用大模型分析特征批次失败，使用规则兜底: product=%s batch=%s error=%s",
                product_name, batch_ids, last_error,
            )
            valid_analysis.extend(_build_rule_fallback_analysis(
                features=batch_features,
                feature_segments_map=feature_segments_map,
                product_name=product_name,
                product_description=product_description,
            ))
            continue

        valid_analysis.extend(_normalize_parsed_analysis(parsed, batch_features, feature_segments_map))

    return AnalyzeFeaturesOutput(raw_analysis=valid_analysis, product_name=product_name)
