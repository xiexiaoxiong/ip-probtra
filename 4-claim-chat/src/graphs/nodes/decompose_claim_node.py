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
from graphs.state import DecomposeClaimInput, DecomposeClaimOutput
from utils.local_llm import invoke_local_llm

logger = logging.getLogger(__name__)


def _content_to_text(content: Any) -> str:
    text: str = ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text += str(item.get("text", ""))
            elif isinstance(item, str):
                text += item
        return text
    return str(content)


def _extract_json_from_response(content: Any) -> Any:
    """从LLM响应中提取JSON，处理markdown代码块等情况"""
    text: str = _content_to_text(content)

    # 尝试从markdown代码块中提取JSON
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if json_match:
        json_str: str = json_match.group(1).strip()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    # 尝试直接解析整个文本
    text_stripped: str = text.strip()
    try:
        return json.loads(text_stripped)
    except json.JSONDecodeError:
        pass

    # 尝试找到第一个 [ 和最后一个 ] 之间的内容
    start_idx: int = text.find('[')
    end_idx: int = text.rfind(']')
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        try:
            return json.loads(text[start_idx:end_idx + 1])
        except json.JSONDecodeError:
            pass

    logger.error(f"无法从LLM响应中提取JSON: {text[:200]}")
    return []


def _parse_features_from_response(content: Any, claim_id: str) -> List[Dict[str, str]]:
    parsed = _extract_json_from_response(content)

    if isinstance(parsed, dict):
        for key in ["features", "data", "result", "items"]:
            val = parsed.get(key)
            if isinstance(val, list):
                parsed = val
                break

    if not isinstance(parsed, list):
        logger.error(f"权利要求{claim_id}大模型返回格式不正确: {type(parsed)}")
        return []

    features: List[Dict[str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        feature_id: str = str(item.get("feature_id", "")).strip()
        feature_text: str = str(item.get("feature_text", "")).strip()
        item_claim_id: str = str(item.get("claim_id", claim_id)).strip() or claim_id
        if feature_id and feature_text:
            features.append({
                "feature_id": feature_id,
                "feature_text": feature_text,
                "claim_id": item_claim_id
            })
    return features


def _looks_like_missing_context_reply(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    hints = [
        "请提供需要拆解的独立权利要求",
        "请提供具体的专利技术特征列表",
        "请提供权利要求编号",
        "我需要您提供",
        "才能进行技术特征拆解",
        "我将为您进行技术特征拆解",
    ]
    return any(hint in normalized for hint in hints)


def _build_retry_prompt(user_prompt: str) -> str:
    return (
        f"{user_prompt}\n\n"
        "【重要补充】以上消息已经完整提供了权利要求编号、权利要求文本、说明书文本和可用的附图信息。"
        "禁止再向用户索取材料，必须直接完成拆解。"
        "仅输出 JSON 对象，字段必须为 features / feature_id / feature_text / claim_id。"
        "如果说明书不足，也必须基于现有权利要求文本给出可比对的最小技术特征拆解。"
    )


def _fallback_split_claim_text(claim_id: str, claim_text: str) -> List[Dict[str, str]]:
    text = re.sub(r"\s+", " ", claim_text or "").strip()
    if not text:
        return []

    text = text.replace("；", ";").replace("：", ":").replace("，", "，")
    text = text.replace("\r", "\n")
    text = re.sub(r"\n+", "\n", text)

    prefix = ""
    remainder = text
    include_match = re.search(r"^(.*?)(?:[,，]?\s*其特征在于[,，:]?\s*)?(?:包括|包含)[:：]?\s*(.*)$", text)
    if include_match:
        prefix = include_match.group(1).strip(" ，,;；。")
        remainder = include_match.group(2).strip()
    else:
        remainder = re.sub(r"^[,，]?\s*其特征在于[:：]?\s*", "", remainder).strip()

    feature_texts: List[str] = []
    if prefix:
        feature_texts.append(prefix)

    split_parts = [part.strip(" ，,;；。") for part in re.split(r"[;\n]+", remainder) if part.strip(" ，,;；。")]
    if len(split_parts) <= 1:
        split_parts = [
            part.strip(" ，,;；。")
            for part in re.split(r"，(?=(?:所述|第一|第二|第三|第四|主体|平支架|侧支架|电池|联接|防水|驱动机构|集雪组件|抛雪机构|控制机构|I/O端口|颈带线))", remainder)
            if part.strip(" ，,;；。")
        ]
    if not split_parts and remainder:
        split_parts = [remainder.strip(" ，,;；。")]

    for part in split_parts:
        cleaned = re.sub(r"^(和|及|以及|并且|并|其中|进一步包括)[:：,，]?\s*", "", part).strip(" ，,;；。")
        if cleaned:
            feature_texts.append(cleaned)

    deduped: List[str] = []
    seen = set()
    for feature_text in feature_texts:
        normalized = feature_text.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)

    fallback_features: List[Dict[str, str]] = []
    for index, feature_text in enumerate(deduped[:26]):
        fallback_features.append({
            "feature_id": f"{claim_id}{chr(ord('A') + index)}",
            "feature_text": feature_text,
            "claim_id": claim_id,
        })
    return fallback_features


def _decompose_single_claim(
    claim_id: str,
    claim_text: str,
    specification_text: str,
    specification_images: List[str],
    llm_config: dict,
    sp_template: str,
    up_template: str,
    ctx: Any
) -> List[Dict[str, str]]:
    """
    拆解单个独立权利要求。
    结合说明书上下文理解权利要求的含义，拆解为技术特征单元。
    """
    # 渲染用户提示词
    up_tpl: Template = Template(up_template)

    # 说明书摘要（避免过长，截取前6000字符）
    spec_summary: str = ""
    if specification_text:
        spec_summary = specification_text[:6000]
        if len(specification_text) > 6000:
            spec_summary += "\n...(说明书内容过长，已截取前6000字符)"

    user_prompt: str = up_tpl.render({
        "claim_id": claim_id,
        "claim_text": claim_text,
        "specification_text": spec_summary
    })

    # 调用大模型
    model: str = llm_config.get("model", "doubao-seed-2-0-pro-260215")
    temperature: float = float(llm_config.get("temperature", 0.1))
    max_tokens: int = int(llm_config.get("max_completion_tokens", 8192))

    prompt_candidates: List[str] = [
        user_prompt,
        _build_retry_prompt(user_prompt),
    ]

    for attempt_index, prompt_text in enumerate(prompt_candidates, start=1):
        if specification_images:
            content_parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt_text}]
            for img_url in specification_images:
                if isinstance(img_url, str) and img_url.startswith("http"):
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": img_url}
                    })
            messages = [
                SystemMessage(content=sp_template),
                HumanMessage(content=content_parts)
            ]
        else:
            messages = [
                SystemMessage(content=sp_template),
                HumanMessage(content=prompt_text)
            ]

        try:
            response = invoke_local_llm(
                messages=messages,
                model=model,
                temperature=temperature,
                max_completion_tokens=max_tokens
            )
        except Exception as e:
            logger.error(f"调用大模型拆解权利要求{claim_id}失败 (第{attempt_index}次): {e}")
            continue

        response_text = _content_to_text(response.content)
        features = _parse_features_from_response(response.content, claim_id)
        if features:
            return features

        if attempt_index < len(prompt_candidates):
            if _looks_like_missing_context_reply(response_text):
                logger.warning(
                    f"权利要求{claim_id}拆解第{attempt_index}次疑似进入索取材料模式，准备使用强化提示重试"
                )
            else:
                logger.warning(
                    f"权利要求{claim_id}拆解第{attempt_index}次未得到有效JSON，准备重试。响应片段: {response_text[:120]}"
                )

    fallback_features = _fallback_split_claim_text(claim_id, claim_text)
    if fallback_features:
        logger.warning(
            f"权利要求{claim_id}大模型拆解失败，降级为基于权利要求文本的规则拆分，得到{len(fallback_features)}个技术特征"
        )
    return fallback_features


def decompose_claim_node(
    state: DecomposeClaimInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> DecomposeClaimOutput:
    """
    title: 拆解独立权利要求
    desc: 使用大语言模型结合说明书上下文，将每个独立权利要求拆解为可编号、可独立比对的技术特征单元
    integrations: 大语言模型
    """
    ctx = runtime.context
    independent_claims: List[Dict[str, str]] = state.independent_claims
    specification_text: str = state.specification_text
    specification_images: List[str] = state.specification_images

    if not independent_claims:
        logger.warning("独立权利要求列表为空，无法拆解")
        return DecomposeClaimOutput(features=[])

    # 读取配置文件
    cfg_file: str = os.path.join(
        os.getenv("COZE_WORKSPACE_PATH", ""),
        config.get("metadata", {}).get("llm_cfg", "config/decompose_claim_llm_cfg.json")
    )
    try:
        with open(cfg_file, 'r', encoding='utf-8') as fd:
            _cfg: dict = json.load(fd)
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        return DecomposeClaimOutput(features=[])

    llm_config: dict = _cfg.get("config", {})
    sp: str = _cfg.get("sp", "")
    up_template: str = _cfg.get("up", "")

    # 逐个拆解每个独立权利要求
    all_features: List[Dict[str, str]] = []
    for claim in independent_claims:
        claim_id: str = str(claim.get("claim_id", ""))
        claim_text: str = str(claim.get("claim_text", ""))

        if not claim_text:
            continue

        logger.info(f"开始拆解独立权利要求{claim_id}...")

        features: List[Dict[str, str]] = _decompose_single_claim(
            claim_id=claim_id,
            claim_text=claim_text,
            specification_text=specification_text,
            specification_images=specification_images,
            llm_config=llm_config,
            sp_template=sp,
            up_template=up_template,
            ctx=ctx
        )

        if features:
            all_features.extend(features)
            logger.info(f"权利要求{claim_id}拆解完成，得到{len(features)}个技术特征")
        else:
            logger.warning(f"权利要求{claim_id}拆解结果为空")

    if not all_features:
        logger.warning("所有独立权利要求的拆解结果均为空")

    logger.info(f"全部拆解完成，共{len(all_features)}个技术特征，来自{len(independent_claims)}个独立权利要求")
    return DecomposeClaimOutput(features=all_features)
