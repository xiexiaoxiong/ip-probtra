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
from utils.local_llm import invoke_local_llm

logger = logging.getLogger(__name__)


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
    features: List[Dict[str, str]] = state.features
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

    # 构造特征列表文本（包含 claim_id）
    features_text: str = ""
    for feat in features:
        fid: str = str(feat.get("feature_id", ""))
        ftext: str = str(feat.get("feature_text", ""))
        cid: str = str(feat.get("claim_id", ""))
        features_text += f"- [{cid}] {fid}: {ftext}\n"

    # 从 state 获取说明书文本
    specification_text: str = state.specification_text

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
                "claim_id": str(feat.get("claim_id", ""))
            })
        return AnalyzeFeaturesOutput(raw_analysis=raw_analysis, product_name=product_name)

    # 解析响应
    parsed = _extract_json_from_response(response.content)
    if isinstance(parsed, dict) and "analysis" in parsed:
        parsed = parsed.get("analysis", [])
    if not isinstance(parsed, list):
        logger.error(f"大模型返回格式不正确: {type(parsed)}")
        raw_analysis = []
        for feat in features:
            raw_analysis.append({
                "feature_id": str(feat.get("feature_id", "")),
                "evidence": "",
                "reason": "大模型返回格式异常，无法解析",
                "reasoning_type": "相关信息缺失",
                "claim_id": str(feat.get("claim_id", ""))
            })
        return AnalyzeFeaturesOutput(raw_analysis=raw_analysis, product_name=product_name)

    # 验证并补充缺失的特征
    parsed_ids: set = set()
    valid_analysis: List[Dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            fid = str(item.get("feature_id", ""))
            if fid:
                parsed_ids.add(fid)
                # 提取 evidence_images，确保是字符串数组
                raw_images = item.get("evidence_images", [])
                evidence_images = [str(url) for url in raw_images if isinstance(url, str) and url.strip()] if isinstance(raw_images, list) else []
                
                valid_analysis.append({
                    "feature_id": fid,
                    "evidence": str(item.get("evidence", "")),
                    "reason": str(item.get("reason", "")),
                    "reasoning_type": str(item.get("reasoning_type", "相关信息缺失")),
                    "claim_id": str(item.get("claim_id", "")),
                    "evidence_images": evidence_images
                })

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
                "evidence_images": []
            })

    return AnalyzeFeaturesOutput(raw_analysis=valid_analysis, product_name=product_name)
