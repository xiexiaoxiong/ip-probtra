"""
发明点特征词精炼节点
职责：将专利术语转化为消费者搜索语言（大白话），确保特征词是普通消费者在电商平台实际会搜索的词
"""
import os
import json
import re
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import LLMClient
from langchain_core.messages import SystemMessage, HumanMessage
from graphs.state import InventionPointRefinementInput, InventionPointRefinementOutput


def invention_point_refinement_node(
    state: InventionPointRefinementInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> InventionPointRefinementOutput:
    """
    title: 发明点特征词精炼
    desc: 将专利术语转化为消费者搜索语言，确保特征词是普通消费者在电商平台上实际会搜索的大白话
    integrations: 大语言模型
    """
    ctx = runtime.context

    # 空值防护：如果没有核心术语且没有发明点，返回空结果
    has_core_terms = bool(state.core_terms and len(state.core_terms) > 0)
    has_invention_point = bool(state.invention_point and state.invention_point.strip())
    
    if not has_core_terms and not has_invention_point:
        return InventionPointRefinementOutput(
            core_terms=[],
            refinement_log="无核心术语和发明点，跳过精炼"
        )

    # 读取 LLM 配置
    cfg_file = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), config['metadata']['llm_cfg'])
    with open(cfg_file, 'r', encoding='utf-8') as fd:
        _cfg = json.load(fd)

    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", "")
    up = _cfg.get("up", "")

    # 将 core_terms 格式化为可读文本
    terms_text = ""
    for i, term in enumerate(state.core_terms, 1):
        if isinstance(term, dict):
            text = term.get("text", "")
            source = term.get("source", "")
            terms_text += f"{i}. {text}（来源：{source}）\n"
        elif isinstance(term, str):
            terms_text += f"{i}. {term}\n"

    # 使用 Jinja2 渲染提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render({
        "core_terms_text": terms_text if terms_text else "（无初步提取的术语）",
        "invention_point": state.invention_point if state.invention_point else "（未识别）",
        "invention_content": state.invention_content if state.invention_content else "（未提供）",
        "claim_text": state.claim_text if state.claim_text else "（未提供）",
        "background_tech": state.background_tech if state.background_tech else "（未提供）"
    })

    # 初始化 LLM 客户端
    client = LLMClient(ctx=ctx)

    # 调用 LLM
    messages = [
        SystemMessage(content=sp),
        HumanMessage(content=user_prompt)
    ]

    response = client.invoke(
        messages=messages,
        model=llm_config.get("model", "glm-5-0-260211"),
        temperature=llm_config.get("temperature", 0.3),
        max_completion_tokens=llm_config.get("max_completion_tokens", 3000)
    )

    # 提取文本内容
    content = response.content
    if isinstance(content, str):
        result_text = content.strip()
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
            elif isinstance(item, str):
                text_parts.append(item)
        result_text = " ".join(text_parts).strip()
    else:
        result_text = str(content).strip()

    # 解析 JSON 结果
    refined_terms = []
    refinement_log = ""

    try:
        # 尝试提取 JSON 部分
        json_match = re.search(r'\[.*\]', result_text, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            parsed_list = json.loads(json_str)
            if isinstance(parsed_list, list):
                for item in parsed_list:
                    if isinstance(item, dict):
                        refined_terms.append({
                            "text": item.get("text", ""),
                            "source": item.get("source", "精炼生成"),
                            "type": item.get("type", "REFINED_FEATURE")
                        })

        # 提取精炼日志（JSON 之后的文本）
        log_match = re.search(r'\](.*)', result_text, re.DOTALL)
        if log_match:
            log_text = log_match.group(1).strip()
            # 去掉可能的 markdown 标记
            log_text = re.sub(r'^[\s\-*]+', '', log_text).strip()
            if log_text:
                refinement_log = log_text

    except json.JSONDecodeError:
        # JSON 解析失败，按行处理
        lines = [line.strip() for line in result_text.split('\n') if line.strip()]
        for line in lines:
            term = re.sub(r'^\d+[\.\、\s]+', '', line)
            if term and not term.startswith("精炼") and not term.startswith("说明"):
                refined_terms.append({
                    "text": term,
                    "source": "精炼生成",
                    "type": "REFINED_FEATURE"
                })

    # 如果精炼结果为空，保留原始术语
    if not refined_terms and state.core_terms:
        refined_terms = state.core_terms
        refinement_log = "精炼结果为空，保留原始术语"

    return InventionPointRefinementOutput(
        core_terms=refined_terms,
        refinement_log=refinement_log
    )
