import copy
import json
import logging
import os
import re
from typing import Any, Dict, List, Tuple

from jinja2 import Template
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import ReviewAnalysisInput, ReviewAnalysisOutput
from utils.local_llm import invoke_local_llm

logger = logging.getLogger(__name__)

_POSITIVE_REASONING_TYPES = {
    "文字直接公开",
    "从图片中看出",
    "结合文字和图片毫无疑义得出",
    "根据功能推导得出",
}

_NEGATIVE_CONTEXT_TOKENS = (
    "不存在", "不具有", "没有", "未提", "未提及", "无法确认", "看不出",
    "未出现", "不是", "非", "未公开", "不能确认", "无", "缺失", "不等于",
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
_STRUCTURE_MARKER_TOKENS = (
    "结构", "模块", "组件", "部件", "机构", "装置", "接口", "安装位",
    "吸尘口", "刷", "拖布", "滚刷", "刷头", "水箱", "卡扣", "底座", "通道",
)
_CAPABILITY_MARKER_TOKENS = (
    "功能", "能力", "模式", "扫拖吸", "三合一", "一体", "清洗", "拖地",
    "扫地", "吸尘", "自动", "智能", "支持", "可实现", "可进行",
)
_INFERENCE_MARKER_TOKENS = (
    "推导", "暗示", "表明支持", "意味着", "可推知", "可视为", "可认为", "合理推导",
)
_TACTILE_MARKER_TOKENS = ("盲文", "文点", "点字", "触点", "凸点", "点显")
_VISUAL_MARKER_TOKENS = ("可视", "可视化", "显示", "数字", "刻度", "表盘", "钟面", "时间管理")
_TIME_MODE_TOKENS = ("时间", "计时", "定时", "倒计时", "分钟", "小时", "秒")
_FACE_MODE_TOKENS = ("可立面", "翻转", "多边形", "六边形", "立面")


def _extract_json_from_response(content: Any) -> Any:
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

    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if json_match:
        try:
            return json.loads(json_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            return json.loads(text[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            pass
    return []


def _load_review_llm_cfg() -> Dict[str, Any]:
    cfg_file = os.path.join(
        os.getenv("COZE_WORKSPACE_PATH", ""),
        "config/review_analysis_llm_cfg.json",
    )
    try:
        with open(cfg_file, "r", encoding="utf-8") as fd:
            return json.load(fd)
    except Exception as exc:
        logger.warning("读取规则审查 LLM 配置失败: %s", exc)
        return {}


def _merge_review_item(base_item: Dict[str, Any], reviewed_item: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base_item)
    if not isinstance(reviewed_item, dict):
        return merged

    for key in ("feature_id", "claim_id", "feature_text", "evidence", "reason", "reasoning_type"):
        if str(reviewed_item.get(key, "")).strip():
            merged[key] = reviewed_item.get(key)

    raw_images = reviewed_item.get("evidence_images", [])
    if isinstance(raw_images, list):
        merged["evidence_images"] = [str(url) for url in raw_images if isinstance(url, str) and url.strip()]

    raw_units = reviewed_item.get("token_units", [])
    if not isinstance(raw_units, list):
        return merged

    base_units = merged.get("token_units", [])
    base_unit_map = {
        str(unit.get("text", "")).strip(): copy.deepcopy(unit)
        for unit in base_units
        if isinstance(unit, dict) and str(unit.get("text", "")).strip()
    }
    merged_units: List[Dict[str, Any]] = []
    for unit in raw_units:
        if not isinstance(unit, dict):
            continue
        text = str(unit.get("text", "")).strip()
        if not text:
            continue
        merged_unit = base_unit_map.get(text, {"text": text})
        if str(unit.get("unit_status", "")).strip():
            _set_unit_status(merged_unit, str(unit.get("unit_status", "")).strip())
        for key in ("evidence", "reason"):
            if str(unit.get(key, "")).strip():
                merged_unit[key] = str(unit.get(key, ""))
        merged_units.append(merged_unit)

    if merged_units:
        merged["token_units"] = merged_units
    return merged


def _review_with_llm(
    raw_analysis: List[Dict[str, Any]],
    product_name: str,
    product_description: str,
    product_images: List[str],
) -> List[Dict[str, Any]]:
    if not raw_analysis:
        return []
    use_llm_review = (os.getenv("MODULE4_REVIEW_USE_LLM") or "0").strip().lower()
    if use_llm_review not in {"1", "true", "yes", "on"}:
        return []
    if any(isinstance(item, dict) and item.get("analysis_source") == "rule_fallback" for item in raw_analysis):
        return []

    cfg = _load_review_llm_cfg()
    if not cfg:
        return []

    llm_config = cfg.get("config", {})
    system_prompt = str(cfg.get("sp", ""))
    user_prompt_template = str(cfg.get("up", ""))
    if not system_prompt or not user_prompt_template:
        return []

    up_tpl = Template(user_prompt_template)
    user_prompt = up_tpl.render({
        "product_name": product_name,
        "product_description": product_description,
        "product_images": [img for img in product_images if isinstance(img, str) and img.startswith("http")],
        "raw_analysis_json": json.dumps(raw_analysis, ensure_ascii=False, indent=2),
    })

    model = llm_config.get("model", "glm-4.6v")
    temperature = float(llm_config.get("temperature", 0.0))
    max_tokens = int(llm_config.get("max_completion_tokens", 12000))

    if product_images:
        content_parts: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for img_url in product_images:
            if isinstance(img_url, str) and img_url.startswith("http"):
                content_parts.append({"type": "image_url", "image_url": {"url": img_url}})
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=content_parts)]
    else:
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        response = invoke_local_llm(
            messages=messages,
            model=model,
            temperature=temperature,
            max_completion_tokens=max_tokens,
        )
    except Exception as exc:
        logger.warning("规则审查 LLM 调用失败，回退原始分析: %s", exc)
        return []

    parsed = _extract_json_from_response(response.content)
    if isinstance(parsed, dict) and isinstance(parsed.get("features"), list):
        parsed = parsed.get("features", [])
    if not isinstance(parsed, list):
        logger.warning("规则审查 LLM 返回格式异常: %s", type(parsed).__name__)
        return []

    raw_item_map = {
        str(item.get("feature_id", "")).strip(): item
        for item in raw_analysis
        if isinstance(item, dict) and str(item.get("feature_id", "")).strip()
    }
    merged_items: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        feature_id = str(item.get("feature_id", "")).strip()
        if not feature_id or feature_id not in raw_item_map:
            continue
        merged_items.append(_merge_review_item(raw_item_map[feature_id], item))

    if len(merged_items) != len(raw_analysis):
        return []
    return merged_items


def _normalize_compare_text(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "")))


def _get_unit_status(unit: Dict[str, Any]) -> str:
    return str(unit.get("unit_status") or unit.get("status") or "uncertain").strip() or "uncertain"


def _set_unit_status(unit: Dict[str, Any], status: str) -> None:
    unit["unit_status"] = status
    unit["status"] = status


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


def _expand_entity_aliases(entity: str) -> List[str]:
    normalized = _normalize_compare_text(entity)
    aliases = {normalized}
    if normalized.endswith("机器人") and len(normalized) > len("机器人"):
        aliases.add(normalized[:-3] + "机")
    return sorted((alias for alias in aliases if len(alias) >= 2), key=len, reverse=True)


def _expand_capability_aliases(capability: str) -> List[str]:
    normalized = _normalize_compare_text(capability)
    aliases = {normalized}
    for key, group in _CAPABILITY_ALIAS_GROUPS.items():
        if normalized == key or normalized in group:
            aliases.update(group)
    return sorted((alias for alias in aliases if len(alias) >= 2), key=len, reverse=True)


def _unit_has_explicit_negative_judgement(unit: Dict[str, Any]) -> bool:
    text = _normalize_compare_text(
        " ".join(
            str(unit.get(key, ""))
            for key in ("text", "normalized_text", "evidence", "reason")
            if str(unit.get(key, "")).strip()
        )
    )
    if not text:
        return False
    target = _normalize_compare_text(str(unit.get("normalized_text", "") or unit.get("text", "")))
    if not target:
        return False
    return _has_negative_context(text, target, window_size=12)


def _upgrade_units_from_positive_reasoning(
    token_units: List[Dict[str, Any]],
    reasoning_type: str,
    reason: str,
) -> Tuple[List[Dict[str, Any]], int]:
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
        if _unit_has_explicit_negative_judgement(unit):
            continue
        probe = str(unit.get("normalized_text", "") or unit.get("text", "")).strip()
        probe_clean = re.sub(r"[\(\（].*?[\)\）]", "", probe).strip()
        if not probe_clean or len(probe_clean) < 2:
            continue
        idx = reason.find(probe_clean)
        if idx < 0:
            idx = reason.replace(" ", "").find(probe_clean.replace(" ", ""))
            if idx < 0:
                continue
        window_start = max(0, idx - 20)
        window_end = min(len(reason), idx + len(probe_clean) + 20)
        window = reason[window_start:window_end]
        if any(neg in window for neg in _NEGATIVE_CONTEXT_TOKENS):
            continue
        _set_unit_status(unit, "match")
        unit["reason"] = (str(unit.get("reason", "")).strip() + " | [规则审查] 特征级正推理与单元结论对齐").strip(" |")
        upgraded_count += 1

    return token_units, upgraded_count


def _infer_necessary_component_match(unit_text: str, source_text: str) -> Tuple[str, str] | None:
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
        if len(entity) < 2 or any(token in entity for token in _RELATION_TOKENS):
            continue
        hit = _find_alias_hit(_expand_entity_aliases(entity), source_normalized)
        if not hit:
            continue
        return (
            f"商品名称/描述已明确表明其属于“{hit}”类产品",
            f"[规则审查] 必要构成推理：既然商品已明确是“{hit}”类产品，则“{unit_text}”作为主体可判定存在",
        )
    return None


def _infer_direct_category_or_capability_match(unit_text: str, source_text: str) -> Tuple[str, str] | None:
    probe = _normalize_compare_text(unit_text)
    if not probe or any(token in probe for token in _RELATION_TOKENS):
        return None
    source_normalized = _normalize_compare_text(source_text)
    if not source_normalized:
        return None

    category_hit = _find_alias_hit(_expand_entity_aliases(probe), source_normalized)
    if category_hit:
        return (
            f"商品名称/描述中出现“{category_hit}”",
            f"[规则审查] 类别直接公开：单元“{unit_text}”可由“{category_hit}”直接支持",
        )

    capability_match = re.fullmatch(r"(?:具备|具有|带有|支持)?(.+?)(?:功能|能力)", probe)
    if capability_match:
        capability_core = capability_match.group(1)
        capability_hit = _find_alias_hit(_expand_capability_aliases(capability_core), source_normalized)
        if capability_hit:
            return (
                f"商品名称/描述中出现“{capability_hit}”",
                f"[规则审查] 能力直接公开：单元“{unit_text}”可由“{capability_hit}”稳定支持",
            )
    return None


def _is_necessary_body_candidate(unit_text: str) -> bool:
    probe = _normalize_compare_text(unit_text)
    for suffix in _ENTITY_BODY_SUFFIXES:
        if not probe.endswith(suffix):
            continue
        entity = probe[:-len(suffix)]
        return len(entity) >= 2 and not any(token in entity for token in _RELATION_TOKENS)
    return False


def _is_capability_like_unit(unit_text: str) -> bool:
    probe = _normalize_compare_text(unit_text)
    if re.fullmatch(r"(?:具备|具有|带有|支持)?(.+?)(?:功能|能力)", probe):
        return True
    return any(marker in probe for marker in ("功能", "能力", "模式"))


def _is_concrete_structure_or_relation_unit(unit_text: str) -> bool:
    probe = _normalize_compare_text(unit_text)
    if not probe:
        return False
    if _is_capability_like_unit(probe) or _is_necessary_body_candidate(probe):
        return False
    return any(token in probe for token in _STRUCTURE_MARKER_TOKENS + _RELATION_TOKENS)


def _source_has_direct_support(unit_text: str, source_text: str) -> bool:
    probe = _normalize_compare_text(unit_text)
    source_normalized = _normalize_compare_text(source_text)
    if not probe or not source_normalized:
        return False
    if _find_alias_hit([probe], source_normalized):
        return True
    if _is_necessary_body_candidate(unit_text):
        entity = probe
        for suffix in _ENTITY_BODY_SUFFIXES:
            if entity.endswith(suffix):
                entity = entity[:-len(suffix)]
                break
        if _find_alias_hit(_expand_entity_aliases(entity), source_normalized):
            return True
    if _is_capability_like_unit(unit_text):
        capability_match = re.fullmatch(r"(?:具备|具有|带有|支持)?(.+?)(?:功能|能力)", probe)
        if capability_match:
            capability_core = capability_match.group(1)
            if _find_alias_hit(_expand_capability_aliases(capability_core), source_normalized):
                return True
    return False


def _is_tactile_marker_unit(unit_text: str) -> bool:
    probe = _normalize_compare_text(unit_text)
    return bool(probe) and any(token in probe for token in ("盲人文点", "盲文", "文点", "点字", "触点", "凸点", "字符号"))


def _is_visual_time_marker_unit(unit_text: str) -> bool:
    probe = _normalize_compare_text(unit_text)
    if not probe:
        return False
    has_visual_marker = any(token in probe for token in ("可视", "符号", "字符", "标识", "刻度"))
    has_time_semantic = any(token in probe for token in ("时间", "计时", "分钟", "小时", "秒"))
    return has_visual_marker and has_time_semantic


def _infer_user_perceivable_marker_match(
    reviewed: Dict[str, Any],
    unit: Dict[str, Any],
    source_text: str,
) -> Tuple[str, str] | None:
    unit_text = str(unit.get("normalized_text", "") or unit.get("text", "")).strip()
    if not unit_text:
        return None

    source_normalized = _normalize_compare_text(source_text)
    feature_text = _normalize_compare_text(str(reviewed.get("feature_text", "")))
    evidence_text = _normalize_compare_text(
        " ".join(
            str(reviewed.get(key, ""))
            for key in ("evidence", "reason")
            if str(reviewed.get(key, "")).strip()
        )
    )
    token_units = reviewed.get("token_units", [])
    has_face_support = any(
        isinstance(other, dict)
        and _get_unit_status(other) == "match"
        and "立面" in _normalize_compare_text(str(other.get("normalized_text", "") or other.get("text", "")))
        for other in token_units
    )

    if _is_tactile_marker_unit(unit_text):
        if any(token in source_normalized for token in _TACTILE_MARKER_TOKENS):
            return (
                "商品标题/描述已明确公开盲文或触觉点位类属性",
                "[规则审查] 用户可触知标识推理：标题已明确给出盲文/触点类属性，可稳定支持该符号单元",
            )
        return None

    if not _is_visual_time_marker_unit(unit_text):
        return None

    has_visual_mode = any(token in source_normalized for token in _VISUAL_MARKER_TOKENS)
    has_time_mode = any(token in source_normalized for token in _TIME_MODE_TOKENS)
    has_face_mode = has_face_support or any(token in source_normalized for token in _FACE_MODE_TOKENS)
    image_or_joint_support = any(
        token in evidence_text
        for token in ("从图片中看出", "结合文字和图片毫无疑义得出", "图片直接", "图示", "显示界面")
    )

    if has_time_mode and ((has_visual_mode and has_face_support) or (has_face_mode and image_or_joint_support)):
        return (
            "商品标题/图片已表明其通过用户可见的时间提示界面完成计时交互",
            "[规则审查] 用户可见标识推理：当商品标题已明确时间管理/计时语义，且图片或多面交互模式已支持用户可见界面时，可稳定认定存在可视时间符号",
        )
    return None


def _maybe_downgrade_overbroad_matches(reviewed: Dict[str, Any], source_text: str) -> Tuple[Dict[str, Any], int]:
    token_units = reviewed.get("token_units", [])
    if not isinstance(token_units, list):
        return reviewed, 0

    reasoning_type = str(reviewed.get("reasoning_type", ""))
    item_context = _normalize_compare_text(
        " ".join(
            str(reviewed.get(key, ""))
            for key in ("evidence", "reason")
            if str(reviewed.get(key, "")).strip()
        )
    )
    downgraded = 0
    for unit in token_units:
        if not isinstance(unit, dict) or _get_unit_status(unit) != "match":
            continue
        unit_text = str(unit.get("normalized_text", "") or unit.get("text", "")).strip()
        if not _is_concrete_structure_or_relation_unit(unit_text):
            continue
        if _source_has_direct_support(unit_text, source_text):
            continue

        unit_context = _normalize_compare_text(
            " ".join(
                str(unit.get(key, ""))
                for key in ("evidence", "reason")
                if str(unit.get(key, "")).strip()
            )
        )
        context_text = item_context + unit_context
        if reasoning_type == "根据功能推导得出" or any(token in context_text for token in _CAPABILITY_MARKER_TOKENS + _INFERENCE_MARKER_TOKENS):
            _set_unit_status(unit, "uncertain")
            unit["reason"] = (
                str(unit.get("reason", "")).strip()
                + " | [规则审查] 该单元属于具体结构/关系，但原始商品文本仅给出功能或营销表述，不能据此推出具体实现结构"
            ).strip(" |")
            downgraded += 1

    if downgraded > 0 and not any(_get_unit_status(unit) == "match" for unit in token_units):
        reviewed["reasoning_type"] = "相关信息缺失"
        reviewed["reason"] = (
            str(reviewed.get("reason", "")).rstrip("。. ")
            + "（规则审查后移除了仅由功能词/营销词外推出的具体结构命中）"
        ).strip()
    return reviewed, downgraded


def _review_single_item(item: Dict[str, Any], source_text: str) -> Dict[str, Any]:
    reviewed = copy.deepcopy(item)
    token_units = reviewed.get("token_units", [])
    if not isinstance(token_units, list):
        token_units = []
        reviewed["token_units"] = token_units

    for unit in token_units:
        if isinstance(unit, dict):
            _set_unit_status(unit, _get_unit_status(unit))

    reasoning_type = str(reviewed.get("reasoning_type", "相关信息缺失"))
    reason = str(reviewed.get("reason", ""))
    token_units, upgraded_from_reason = _upgrade_units_from_positive_reasoning(token_units, reasoning_type, reason)

    inferred_count = 0
    if not any(_get_unit_status(unit) == "match" for unit in token_units):
        for unit in token_units:
            if _get_unit_status(unit) != "uncertain":
                continue
            if _unit_has_explicit_negative_judgement(unit):
                continue
            inferred = _infer_necessary_component_match(
                unit_text=str(unit.get("normalized_text", "") or unit.get("text", "")),
                source_text=source_text,
            )
            if not inferred:
                inferred = _infer_direct_category_or_capability_match(
                    unit_text=str(unit.get("normalized_text", "") or unit.get("text", "")),
                    source_text=source_text,
                )
            if not inferred:
                continue
            inferred_evidence, inferred_reason = inferred
            _set_unit_status(unit, "match")
            if not str(unit.get("evidence", "")).strip():
                unit["evidence"] = inferred_evidence
            unit["reason"] = (str(unit.get("reason", "")).strip() + " | " + inferred_reason).strip(" |")
            inferred_count += 1

    marker_inferred_count = 0
    for unit in token_units:
        if _get_unit_status(unit) != "uncertain":
            continue
        if _unit_has_explicit_negative_judgement(unit):
            continue
        inferred = _infer_user_perceivable_marker_match(reviewed, unit, source_text)
        if not inferred:
            continue
        inferred_evidence, inferred_reason = inferred
        _set_unit_status(unit, "match")
        if not str(unit.get("evidence", "")).strip():
            unit["evidence"] = inferred_evidence
        unit["reason"] = (str(unit.get("reason", "")).strip() + " | " + inferred_reason).strip(" |")
        marker_inferred_count += 1

    if any(_get_unit_status(unit) == "match" for unit in token_units) and str(reviewed.get("reasoning_type", "")) == "相关信息缺失":
        reviewed["reasoning_type"] = "根据功能推导得出" if inferred_count > 0 else "文字直接公开"
        reviewed["reason"] = (reason.rstrip("。. ") + "（规则审查后发现存在可确认公开的单元）").strip()

    reviewed, downgraded_count = _maybe_downgrade_overbroad_matches(reviewed, source_text)

    if upgraded_from_reason > 0 or inferred_count > 0 or marker_inferred_count > 0:
        reviewed["review_summary"] = {
            "upgraded_from_reason": upgraded_from_reason,
            "upgraded_from_rules": inferred_count,
        }
    if marker_inferred_count > 0:
        reviewed.setdefault("review_summary", {})
        reviewed["review_summary"]["upgraded_user_perceivable_markers"] = marker_inferred_count
    if downgraded_count > 0:
        reviewed.setdefault("review_summary", {})
        reviewed["review_summary"]["downgraded_overbroad_matches"] = downgraded_count
    return reviewed


def review_analysis_node(
    state: ReviewAnalysisInput,
    config: RunnableConfig,
    runtime: Runtime[Context],
) -> ReviewAnalysisOutput:
    """
    title: 规则审查分析结果
    desc: 基于模型层结构化分析、原始商品数据和审查规则，对单元判断做有限纠偏，产出 reviewed_analysis
    """
    del config, runtime
    raw_analysis = state.raw_analysis if isinstance(state.raw_analysis, list) else []
    product_data = state.product_data if isinstance(state.product_data, dict) else {}
    product_name = str(state.product_name or product_data.get("name", ""))
    product_description = str(product_data.get("description", ""))
    product_images = product_data.get("images", []) if isinstance(product_data.get("images"), list) else []
    source_text = " ".join(part for part in (product_name, product_description) if str(part).strip())

    llm_reviewed_analysis = _review_with_llm(
        raw_analysis=[item for item in raw_analysis if isinstance(item, dict)],
        product_name=product_name,
        product_description=product_description,
        product_images=product_images,
    )
    review_input = llm_reviewed_analysis or raw_analysis

    reviewed_analysis = [_review_single_item(item, source_text) for item in review_input if isinstance(item, dict)]
    logger.info("规则审查完成：商品 '%s' 共审查 %d 条特征", product_name, len(reviewed_analysis))
    return ReviewAnalysisOutput(reviewed_analysis=reviewed_analysis)
