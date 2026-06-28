import logging
import re
from typing import List, Dict, Any, Tuple
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import ApplyRulesInput, ApplyRulesOutput
from utils.claim_scoring import (
    build_claim_totals,
    build_feature_segments,
    cap_matched_effective_length,
    classify_unit_status,
    compute_claim_score,
    compute_feature_awarded_score,
    compute_feature_full_score,
    compute_feature_similarity_score,
    compute_product_score,
    count_effective_units,
    score_band,
)

logger = logging.getLogger(__name__)


# 特征级正推理类型（这些情况下 LLM 应当至少有一个 match unit）
_POSITIVE_REASONING_TYPES = {
    "文字直接公开",
    "从图片中看出",
    "结合文字和图片毫无疑义得出",
    "根据功能推导得出",
}

# 上下文负面词（出现则不升级为 match）
_NEGATIVE_CONTEXT_TOKENS = (
    "不存在", "不具有", "没有", "未提", "未提及", "无法确认", "看不出",
    "未出现", "不是", "非", "未公开", "不能确认",
)

_ENTITY_BODY_SUFFIXES = ("本体", "主体", "机体", "整机")
_RELATION_TOKENS = (
    "安装", "设置", "处于", "控制", "连接", "相接", "位于", "底部",
    "顶部", "内部", "外部", "模式", "伸出", "收纳", "活动", "吸尘口",
)
_CAPABILITY_ALIAS_GROUPS = {
    "拖地": ("拖地", "扫拖", "拖洗", "洗地", "湿拖"),
    "扫地": ("扫地", "清扫"),
    "吸尘": ("吸尘", "扫拖吸", "吸拖"),
}


def _normalize_compare_text(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "")))


def _get_unit_status(unit: Dict[str, Any]) -> str:
    return str(unit.get("status") or unit.get("unit_status") or "").strip()


def _set_unit_status(unit: Dict[str, Any], status: str) -> None:
    unit["status"] = status
    unit["unit_status"] = status


def _expand_entity_aliases(entity: str) -> List[str]:
    normalized = _normalize_compare_text(entity)
    aliases = {normalized}
    if normalized.endswith("机器人") and len(normalized) > len("机器人"):
        aliases.add(normalized[:-3] + "机")
    return sorted((alias for alias in aliases if len(alias) >= 2), key=len, reverse=True)


def _has_negative_context(source_normalized: str, hit: str, window_size: int = 8) -> bool:
    if not source_normalized or not hit:
        return False
    start = 0
    while True:
        idx = source_normalized.find(hit, start)
        if idx < 0:
            return False
        window_start = max(0, idx - window_size)
        window_end = min(len(source_normalized), idx + len(hit) + window_size)
        window = source_normalized[window_start:window_end]
        if any(neg in window for neg in _NEGATIVE_CONTEXT_TOKENS):
            return True
        start = idx + len(hit)


def _find_alias_hit(candidates: List[str], source_normalized: str) -> str:
    for candidate in candidates:
        if not candidate or candidate not in source_normalized:
            continue
        if _has_negative_context(source_normalized, candidate):
            continue
        return candidate
    return ""


def _expand_capability_aliases(capability: str) -> List[str]:
    normalized = _normalize_compare_text(capability)
    aliases = {normalized}
    for key, group in _CAPABILITY_ALIAS_GROUPS.items():
        if normalized == key or normalized in group:
            aliases.update(group)
    return sorted((alias for alias in aliases if len(alias) >= 2), key=len, reverse=True)


def _infer_necessary_component_match(
    unit_text: str,
    source_text: str,
) -> Tuple[str, str] | None:
    """
    高置信推理兜底：
    - 当商品名称/证据已经明确表明其属于某一产品类别时，
      "<产品类别>本体/主体/机体/整机" 可判定为 match。
    - 仅处理纯实体主体单元，不处理带安装/位置/模式/连接关系的复合单元，
      避免把需要额外结构证据的内容误判为公开。
    """
    probe = _normalize_compare_text(unit_text)
    if not probe:
        return None

    source_normalized = _normalize_compare_text(source_text)
    if not source_normalized:
        return None

    for suffix in _ENTITY_BODY_SUFFIXES:
        if not probe.endswith(suffix):
            continue
        entity = probe[:-len(suffix)]
        if len(entity) < 2:
            continue
        if any(token in entity for token in _RELATION_TOKENS):
            continue
        aliases = _expand_entity_aliases(entity)
        hit = _find_alias_hit(aliases, source_normalized)
        if not hit:
            continue
        inferred_evidence = f"商品名称/证据已明确表明其属于“{hit}”类产品"
        inferred_reason = (
            f"[必要构成推理] 既然商品已被明确表述为“{hit}”类产品，"
            f"则“{unit_text}”作为该类产品的必需主体可判定存在"
        )
        return inferred_evidence, inferred_reason
    return None


def _infer_direct_category_or_capability_match(
    unit_text: str,
    source_text: str,
) -> Tuple[str, str] | None:
    probe = _normalize_compare_text(unit_text)
    if not probe or any(token in probe for token in _RELATION_TOKENS):
        return None

    source_normalized = _normalize_compare_text(source_text)
    if not source_normalized:
        return None

    category_hit = _find_alias_hit(_expand_entity_aliases(probe), source_normalized)
    if category_hit:
        return (
            f"商品名称/证据中出现“{category_hit}”",
            f"[类别直接公开] 单元“{unit_text}”可由商品名称/证据中的“{category_hit}”直接对应认定",
        )

    capability_match = re.fullmatch(r"(?:具备|具有|带有|支持)?(.+?)(?:功能|能力)", probe)
    if capability_match:
        capability_core = capability_match.group(1)
        capability_hit = _find_alias_hit(_expand_capability_aliases(capability_core), source_normalized)
        if capability_hit:
            return (
                f"商品名称/证据中出现“{capability_hit}”",
                f"[能力直接公开] 单元“{unit_text}”可由商品名称/证据中的“{capability_hit}”稳定支持",
            )
    return None


def _recompute_feature_result(feature_result: Dict[str, Any]) -> None:
    matched_effective_length = 0
    has_mismatch = False
    for unit in feature_result.get("token_units", []):
        unit_status = _get_unit_status(unit)
        if unit_status == "match":
            matched_effective_length += int(unit.get("effective_length", 0) or 0)
        elif unit_status == "mismatch":
            has_mismatch = True

    feature_result["matched_effective_length"] = matched_effective_length
    feature_result["zeroed_by_mismatch"] = has_mismatch
    capped_matched_effective_length = cap_matched_effective_length(
        feature_result["feature_effective_length"],
        matched_effective_length,
    )
    feature_result["matched_effective_length"] = capped_matched_effective_length
    feature_result["similarity_score"] = compute_feature_similarity_score(
        feature_effective_length=feature_result["feature_effective_length"],
        matched_effective_length=capped_matched_effective_length,
        has_mismatch=has_mismatch,
    )
    feature_result["feature_awarded_score"] = compute_feature_awarded_score(
        feature_full_score=feature_result["feature_full_score"],
        feature_effective_length=feature_result["feature_effective_length"],
        matched_effective_length=capped_matched_effective_length,
        has_mismatch=has_mismatch,
    )


def _upgrade_units_from_positive_reasoning(
    token_units: List[Dict[str, Any]],
    reasoning_type: str,
    reason: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    兜底规则：当 LLM 在特征级给出正推理（文字直接公开等），但 token_units 里
    没有任何一个 unit_status='match' 时，从 LLM 的 reason 文本里识别被点名的
    最小单元，把对应 unit 升级为 match。

    设计要点（避免误判）：
    1. 只在正推理下生效，负推理或"相关信息缺失"不动
    2. 只在当前所有 unit 都是 uncertain 时升级
    3. 只在 reason 文本里能完整找到 unit 文本（normalized_text）才升级
    4. 升级前检查上下文：紧邻 ±20 字符内不能含负面词
    5. 升级会写一条 warning log 方便回溯

    返回值：(升级后的 token_units, 升级成功的数量)
    """
    if not token_units or reasoning_type not in _POSITIVE_REASONING_TYPES:
        return token_units, 0
    if any(_get_unit_status(unit) == "match" for unit in token_units):
        return token_units, 0
    if not reason or not reason.strip():
        return token_units, 0

    upgraded_count = 0
    for unit in token_units:
        if _get_unit_status(unit) != "uncertain":
            continue
        # 优先用 normalized_text（已去除 (数字) 后缀），回落到 text
        probe = str(unit.get("normalized_text", "") or unit.get("text", "")).strip()
        # 二次清理：去掉尾部数字 / 标点
        probe_clean = re.sub(r"[\(\（].*?[\)\）]", "", probe).strip()
        if not probe_clean or len(probe_clean) < 2:
            continue
        idx = reason.find(probe_clean)
        if idx < 0:
            # 退一步：去掉空格匹配
            idx = reason.replace(" ", "").find(probe_clean.replace(" ", ""))
            if idx < 0:
                continue
        # 上下文窗口：前后 20 字符
        window_start = max(0, idx - 20)
        window_end = min(len(reason), idx + len(probe_clean) + 20)
        window = reason[window_start:window_end]
        if any(neg in window for neg in _NEGATIVE_CONTEXT_TOKENS):
            continue
        # 升级
        _set_unit_status(unit, "match")
        existing_reason = str(unit.get("reason", "")).strip()
        unit["reason"] = (existing_reason + " | [特征级正推理兜底] " + str(reason)[:80]).strip(" |")
        upgraded_count += 1

    if upgraded_count > 0:
        logger.warning(
            "特征级正推理兜底：reasoning_type=%s，无任何 unit_status='match'，"
            "已在 reason 文本中识别出 %d 个 unit 并升级为 match",
            reasoning_type, upgraded_count,
        )
    return token_units, upgraded_count


def _upgrade_units_from_necessary_component_inference(
    token_units: List[Dict[str, Any]],
    source_text: str,
    product_name: str,
) -> Tuple[List[Dict[str, Any]], int]:
    if not token_units:
        return token_units, 0

    upgraded_count = 0
    for unit in token_units:
        if _get_unit_status(unit) != "uncertain":
            continue
        inferred = _infer_necessary_component_match(
            unit_text=str(unit.get("normalized_text", "") or unit.get("text", "")).strip(),
            source_text=source_text,
        )
        if not inferred:
            inferred = _infer_direct_category_or_capability_match(
                unit_text=str(unit.get("normalized_text", "") or unit.get("text", "")).strip(),
                source_text=source_text,
            )
        if not inferred:
            continue
        inferred_evidence, inferred_reason = inferred
        _set_unit_status(unit, "match")
        if not str(unit.get("evidence", "")).strip():
            unit["evidence"] = inferred_evidence
        existing_reason = str(unit.get("reason", "")).strip()
        unit["reason"] = (existing_reason + " | " + inferred_reason).strip(" |")
        upgraded_count += 1

    if upgraded_count > 0:
        logger.warning(
            "必要构成推理兜底：商品 '%s' 中有 %d 个单元因产品类别可稳定推出主体存在而升级为 match",
            product_name,
            upgraded_count,
        )
    return token_units, upgraded_count


def _compute_feature_result(
    raw_item: Dict[str, Any],
    feature_meta: Dict[str, Any],
    claim_total_effective_length: int,
) -> Dict[str, Any]:
    feature_text = str(feature_meta.get("feature_text", ""))
    feature_segments = feature_meta.get("feature_segments", [])
    if not isinstance(feature_segments, list) or not feature_segments:
        feature_segments = build_feature_segments(feature_text)

    token_units = raw_item.get("token_units", [])
    if not isinstance(token_units, list):
        token_units = []

    normalized_units: List[Dict[str, Any]] = []
    matched_effective_length = 0
    has_mismatch = False

    token_map = {
        str(unit.get("text", "")).strip(): unit
        for unit in token_units
        if isinstance(unit, dict) and str(unit.get("text", "")).strip()
    }

    for segment in feature_segments:
        segment_text = str(segment.get("text", "")).strip()
        if not segment_text:
            continue
        raw_unit = token_map.get(segment_text, {})
        unit_status = classify_unit_status(raw_unit.get("unit_status", "uncertain"), raw_item.get("reasoning_type", ""))
        effective_length = int(segment.get("effective_length", count_effective_units(segment_text)) or 0)
        if unit_status == "match":
            matched_effective_length += effective_length
        if unit_status == "mismatch":
            has_mismatch = True
        normalized_units.append({
            "text": segment_text,
            "normalized_text": str(segment.get("normalized_text", "")),
            "start": int(segment.get("start", 0) or 0),
            "end": int(segment.get("end", 0) or 0),
            "effective_length": effective_length,
            "status": unit_status,
            "evidence": str(raw_unit.get("evidence", "")),
            "reason": str(raw_unit.get("reason", "")),
        })

    feature_effective_length = int(feature_meta.get("effective_length", 0) or 0)
    if feature_effective_length <= 0:
        feature_effective_length = sum(int(unit.get("effective_length", 0) or 0) for unit in normalized_units)
    feature_full_score = compute_feature_full_score(feature_effective_length, claim_total_effective_length)
    matched_effective_length = cap_matched_effective_length(feature_effective_length, matched_effective_length)
    feature_similarity_score = compute_feature_similarity_score(
        feature_effective_length=feature_effective_length,
        matched_effective_length=matched_effective_length,
        has_mismatch=has_mismatch,
    )
    feature_awarded_score = compute_feature_awarded_score(
        feature_full_score=feature_full_score,
        feature_effective_length=feature_effective_length,
        matched_effective_length=matched_effective_length,
        has_mismatch=has_mismatch,
    )

    return {
        "token_units": normalized_units,
        "feature_effective_length": feature_effective_length,
        "matched_effective_length": matched_effective_length,
        "feature_full_score": feature_full_score,
        "similarity_score": feature_similarity_score,
        "feature_awarded_score": feature_awarded_score,
        "zeroed_by_mismatch": has_mismatch,
    }


def apply_rules_node(
    state: ApplyRulesInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ApplyRulesOutput:
    """
    title: 应用比对规则
    desc: 基于最小判断单元状态，按有效字数/单词占比计算每个特征、每个权利要求和每个商品的最终得分
    """
    reviewed_analysis: List[Dict[str, Any]] = state.reviewed_analysis
    product_name: str = state.product_name
    features: List[Dict[str, str]] = state.features

    # 构建 feature_id -> feature_text / claim_id 的映射
    feature_meta_map: Dict[str, Dict[str, Any]] = {}
    for feat in features:
        fid: str = str(feat.get("feature_id", ""))
        if fid:
            feature_meta_map[fid] = {
                "feature_text": str(feat.get("feature_text", "")),
                "claim_id": str(feat.get("claim_id", "")),
                "feature_segments": feat.get("feature_segments", []),
                "effective_length": int(feat.get("effective_length", 0) or 0),
            }

    claim_total_lengths = build_claim_totals(list(feature_meta_map.values()))
    comparison_features: List[Dict[str, Any]] = []
    for item in reviewed_analysis:
        fid = str(item.get("feature_id", ""))
        feature_meta = feature_meta_map.get(fid, {})
        claim_id: str = str(feature_meta.get("claim_id", "")).strip() or str(item.get("claim_id", "")).strip() or "unknown"
        claim_total_effective_length = int(claim_total_lengths.get(claim_id, 0) or 0)
        feature_result = _compute_feature_result(item, feature_meta, claim_total_effective_length)

        reasoning_type: str = str(item.get("reasoning_type", "相关信息缺失"))
        reason: str = str(item.get("reason", ""))
        evidence: str = str(item.get("evidence", ""))
        evidence_images: List[str] = item.get("evidence_images", []) if isinstance(item.get("evidence_images"), list) else []

        comparison_features.append({
            "feature_id": fid,
            "feature_text": str(feature_meta.get("feature_text", "")),
            "evidence": evidence,
            "similarity_score": feature_result["similarity_score"],
            "score_band": score_band(
                feature_result["similarity_score"],
                has_mismatch=feature_result["zeroed_by_mismatch"],
                matched_length=feature_result["matched_effective_length"],
                total_length=feature_result["feature_effective_length"],
            ),
            "reason": reason,
            "reasoning_type": reasoning_type,
            "claim_id": claim_id,
            "evidence_images": evidence_images,
            "score_rationale": (
                f"特征占比 {feature_result['feature_full_score']}%，"
                f"特征得分 {feature_result['similarity_score']}%，"
                f"加权贡献 {feature_result['feature_awarded_score']}，"
                f"命中有效长度 {feature_result['matched_effective_length']}/{feature_result['feature_effective_length']}"
            ),
            "token_units": feature_result["token_units"],
            "feature_full_score": feature_result["feature_full_score"],
            "feature_awarded_score": feature_result["feature_awarded_score"],
            "feature_effective_length": feature_result["feature_effective_length"],
            "matched_effective_length": feature_result["matched_effective_length"],
            "claim_total_effective_length": claim_total_effective_length,
            "zeroed_by_mismatch": feature_result["zeroed_by_mismatch"],
        })

    claim_groups: Dict[str, List[Dict[str, Any]]] = {}
    for feature in comparison_features:
        claim_groups.setdefault(str(feature.get("claim_id", "")).strip() or "unknown", []).append(feature)

    claim_scores: List[Dict[str, Any]] = []
    for claim_id, claim_features in sorted(claim_groups.items()):
        claim_total_length = int(claim_total_lengths.get(claim_id, 0) or 0)
        claim_score, claim_matched_effective_length, zeroed_by_mismatch = compute_claim_score(
            claim_features,
            claim_total_length,
        )
        for feature in claim_features:
            local_zeroed_by_mismatch = bool(feature.get("zeroed_by_mismatch"))
            feature["zeroed_by_mismatch"] = local_zeroed_by_mismatch
            feature["claim_zeroed_by_mismatch"] = zeroed_by_mismatch
            if zeroed_by_mismatch:
                feature["score_rationale"] = (
                    str(feature.get("score_rationale", "")).rstrip("。")
                    + "；所属权利要求因其他特征存在明确不相同单元而总分归零，"
                    + "当前特征分段仍按本特征自身证据展示"
                ).strip("；")
        claim_scores.append({
            "claim_id": claim_id,
            "similarity_score": claim_score,
            "score_band": score_band(
                claim_score,
                has_mismatch=zeroed_by_mismatch,
                matched_length=claim_matched_effective_length,
                total_length=claim_total_length,
            ),
            "claim_total_effective_length": claim_total_length,
            "claim_matched_effective_length": claim_matched_effective_length,
            "zeroed_by_mismatch": zeroed_by_mismatch,
        })

    product_similarity_score = compute_product_score(claim_scores)

    product_zeroed_by_mismatch = any(bool(c.get("zeroed_by_mismatch")) for c in claim_scores)
    product_matched_length = sum(int(c.get("claim_matched_effective_length", 0) or 0) for c in claim_scores)
    product_total_length = sum(int(c.get("claim_total_effective_length", 0) or 0) for c in claim_scores)

    result: Dict[str, Any] = {
        "product_name": product_name,
        "features": comparison_features,
        "claim_scores": claim_scores,
        "product_similarity_score": product_similarity_score,
        "product_score_band": score_band(
            product_similarity_score,
            has_mismatch=product_zeroed_by_mismatch,
            matched_length=product_matched_length,
            total_length=product_total_length,
        ),
    }

    return ApplyRulesOutput(comparison_result=result)
