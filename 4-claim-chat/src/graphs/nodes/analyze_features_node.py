import os
import re
import json
import logging
from typing import List, Dict, Any
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import AnalyzeFeaturesInput, AnalyzeFeaturesOutput
from utils.claim_scoring import build_feature_segments, count_effective_units
from utils.local_llm import invoke_local_llm

logger = logging.getLogger(__name__)


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
    product_description: str = str(product_data.get("description", ""))
    product_images: List[str] = product_data.get("images", []) if isinstance(product_data.get("images"), list) else []

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

    # 构造特征列表文本（包含 claim_id，并把 feature_segments 显式列出来供 LLM 1:1 比对）
    features_text: str = ""
    feature_segments_map: Dict[str, List[Dict[str, Any]]] = {}
    for feat in features:
        fid: str = str(feat.get("feature_id", ""))
        ftext: str = str(feat.get("feature_text", ""))
        cid: str = str(feat.get("claim_id", ""))
        feature_segments = feat.get("feature_segments", [])
        feature_segments_map[fid] = feature_segments if isinstance(feature_segments, list) else build_feature_segments(ftext)
        features_text += f"- [{cid}] {fid}: {ftext}\n"
        segments = feature_segments_map[fid]
        if segments:
            features_text += "  feature_segments（最小判断单元，token_units 必须 1:1 输出）：\n"
            for idx, seg in enumerate(segments, 1):
                seg_text = str(seg.get("text", "")).strip()
                if seg_text:
                    features_text += f"    {idx}. {seg_text}\n"

    # 从 state 获取说明书文本
    specification_text: str = state.specification_text

    duplicate_count = max(0, len(raw_features) - len(features))
    if duplicate_count:
        logger.warning(
            "商品 '%s' 的输入特征存在重复，已在模型调用前去重: raw=%s, deduped=%s",
            product_name,
            len(raw_features),
            len(features),
        )

    # 渲染用户提示词
    up_tpl: Template = Template(up_template)
    user_prompt: str = up_tpl.render({
        "features_text": features_text,
        "product_name": product_name,
        "product_description": product_description,
        "product_images": product_images,
        "specification_text": specification_text
    })

    # 构造消息（支持多模态）
    model: str = llm_config.get("model", "doubao-seed-1-8-251228")
    temperature: float = float(llm_config.get("temperature", 0.1))
    max_tokens: int = int(llm_config.get("max_completion_tokens", 16384))

    # 如果有图片，构造多模态消息
    if product_images:
        content_parts: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for img_url in product_images:
            if isinstance(img_url, str) and img_url.startswith("http"):
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": img_url}
                })
        messages = [
            SystemMessage(content=sp),
            HumanMessage(content=content_parts)
        ]
    else:
        messages = [
            SystemMessage(content=sp),
            HumanMessage(content=user_prompt)
        ]

    try:
        response = invoke_local_llm(
            messages=messages,
            model=model,
            temperature=temperature,
            max_completion_tokens=max_tokens
        )
    except Exception as e:
        logger.error(f"调用大模型分析特征失败: {e}")
        raw_analysis: List[Dict[str, Any]] = []
        for feat in features:
            raw_analysis.append({
                "feature_id": str(feat.get("feature_id", "")),
                "evidence": "",
                "reason": f"大模型调用失败，无法分析: {e}",
                "reasoning_type": "相关信息缺失",
                "claim_id": str(feat.get("claim_id", "")),
                "suggested_score": 20,
                "score_band_hint": "低相似",
                "score_rationale": "模型调用失败，按低相似兜底",
                "token_units": _normalize_token_units({"feature_text": feat.get("feature_text", "")}, feature_segments_map.get(str(feat.get("feature_id", "")), [])),
            })
        return AnalyzeFeaturesOutput(raw_analysis=raw_analysis, product_name=product_name)

    # 解析响应
    parsed = _extract_json_from_response(response.content)
    # 临时诊断日志：打印 LLM 响应的前 800 字 + 解析后类型
    try:
        rc = response.content
        if isinstance(rc, str):
            preview = rc[:800]
        else:
            preview = str(rc)[:800]
    except Exception:
        preview = "<unprintable>"
    logger.info(
        "[DIAG] 商品 '%s' LLM 响应预览: %s | 解析后类型: %s",
        product_name, preview, type(parsed).__name__,
    )
    # 兼容多种外层 dict 包裹：analysis / features / data / items / result
    if isinstance(parsed, dict):
        for _key in ("analysis", "features", "data", "items", "result"):
            if _key in parsed and isinstance(parsed[_key], list):
                parsed = parsed[_key]
                break
    if not isinstance(parsed, list):
        logger.error(f"大模型返回格式不正确: {type(parsed)}")
        raw_analysis = []
        for feat in features:
            raw_analysis.append({
                "feature_id": str(feat.get("feature_id", "")),
                "evidence": "",
                "reason": "大模型返回格式异常，无法解析",
                "reasoning_type": "相关信息缺失",
                "claim_id": str(feat.get("claim_id", "")),
                "suggested_score": 20,
                "score_band_hint": "低相似",
                "score_rationale": "模型输出不可解析，按低相似兜底",
                "token_units": _normalize_token_units({"feature_text": feat.get("feature_text", "")}, feature_segments_map.get(str(feat.get("feature_id", "")), [])),
            })
        return AnalyzeFeaturesOutput(raw_analysis=raw_analysis, product_name=product_name)

    # 验证并补充缺失的特征
    parsed_ids: set = set()
    valid_analysis: List[Dict[str, Any]] = []
    deduped_analysis_by_feature_id: Dict[str, Dict[str, Any]] = {}
    for item in parsed:
        if isinstance(item, dict):
            fid = str(item.get("feature_id", ""))
            if fid:
                parsed_ids.add(fid)
                # 提取 evidence_images，确保是字符串数组
                raw_images = item.get("evidence_images", [])
                evidence_images = [str(url) for url in raw_images if isinstance(url, str) and url.strip()] if isinstance(raw_images, list) else []
                suggested_score = _coerce_score(item.get("suggested_score"), default=50)

                candidate = {
                    "feature_id": fid,
                    "evidence": str(item.get("evidence", "")),
                    "reason": str(item.get("reason", "")),
                    "reasoning_type": str(item.get("reasoning_type", "相关信息缺失")),
                    "claim_id": str(item.get("claim_id", "")),
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

    # 防御性兜底：特征级 reasoning_type 与单元级 unit_status 出现矛盾时，
    # 强制把整条特征降级为"待确认"，避免后续把所有 segment 标红。
    for fid, candidate in deduped_analysis_by_feature_id.items():
        candidate = _downgrade_weak_enrichment_only_units(candidate)
        deduped_analysis_by_feature_id[fid] = _downgrade_feature_mismatch_inconsistency(candidate)

    valid_analysis.extend(deduped_analysis_by_feature_id.values())

    # 补充LLM遗漏的特征
    for feat in features:
        fid = str(feat.get("feature_id", ""))
        if fid not in parsed_ids:
            valid_analysis.append({
                "feature_id": fid,
                "evidence": "",
                "reason": "LLM未返回该特征的分析结果",
                "reasoning_type": "相关信息缺失",
                "claim_id": str(feat.get("claim_id", "")),
                "evidence_images": [],
                "suggested_score": 20,
                "score_band_hint": "低相似",
                "score_rationale": "模型遗漏该特征，按低相似兜底",
                "token_units": _normalize_token_units({"feature_text": feat.get("feature_text", "")}, feature_segments_map.get(fid, [])),
            })

    return AnalyzeFeaturesOutput(raw_analysis=valid_analysis, product_name=product_name)
