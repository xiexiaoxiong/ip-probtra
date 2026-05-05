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
from coze_coding_dev_sdk import LLMClient
from graphs.state import DecomposeClaimInput, DecomposeClaimOutput

logger = logging.getLogger(__name__)


def _extract_json_from_response(content: Any) -> Any:
    """从LLM响应中提取JSON，处理markdown代码块等情况"""
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

    client = LLMClient(ctx=ctx)

    # 如果有说明书附图，构造多模态消息
    if specification_images:
        content_parts: List[Dict[str, Any]] = [{"type": "text", "text": user_prompt}]
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
            HumanMessage(content=user_prompt)
        ]

    try:
        response = client.invoke(
            messages=messages,
            model=model,
            temperature=temperature,
            max_completion_tokens=max_tokens
        )
    except Exception as e:
        logger.error(f"调用大模型拆解权利要求{claim_id}失败: {e}")
        return []

    # 解析响应
    parsed = _extract_json_from_response(response.content)

    # 处理 dict 格式（如 {"features": [...]}）
    if isinstance(parsed, dict):
        for key in ["features", "data", "result", "items"]:
            val = parsed.get(key)
            if isinstance(val, list):
                parsed = val
                break

    if not isinstance(parsed, list):
        logger.error(f"权利要求{claim_id}大模型返回格式不正确: {type(parsed)}")
        return []

    # 验证并格式化特征列表，添加claim_id
    features: List[Dict[str, str]] = []
    for item in parsed:
        if isinstance(item, dict):
            feature_id: str = str(item.get("feature_id", ""))
            feature_text: str = str(item.get("feature_text", ""))
            if feature_id and feature_text:
                features.append({
                    "feature_id": feature_id,
                    "feature_text": feature_text,
                    "claim_id": claim_id
                })

    return features


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
