"""
关键词组合节点
职责：将筛选后的关键词与场景词、人群词组合成最终检索关键词
生成五种类型：
1. 品牌同款型：专利权人 + 同款 + 客体
2. 特征组合型：核心发明点特征词 + 客体
3. 场景型：场景词 + 客体
4. 人群型：人群词 + 客体
5. 功能型：功能特点词 + 客体
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
from graphs.state import KeywordCombinationInput, KeywordCombinationOutput


def keyword_combination_node(
    state: KeywordCombinationInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> KeywordCombinationOutput:
    """
    title: 关键词组合
    desc: 将核心术语、场景词、人群词与客体组合成五种类型的电商搜索关键词
    integrations: 大语言模型
    """
    ctx = runtime.context

    # 如果没有关键词数据，返回空列表
    if not state.filtered_core_terms or len(state.filtered_core_terms) == 0:
        return KeywordCombinationOutput(combined_keywords=[])

    # 读取 LLM 配置
    cfg_file = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), config['metadata']['llm_cfg'])
    with open(cfg_file, 'r', encoding='utf-8') as fd:
        _cfg = json.load(fd)

    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", "")
    up = _cfg.get("up", "")

    # 准备关键词数据
    core_term_texts = [term.get("text", "") for term in state.filtered_core_terms if term.get("text")]

    # 使用 Jinja2 渲染提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render({
        "core_terms": "、".join(core_term_texts) if core_term_texts else "无",
        "product_object": "、".join(state.product_object) if state.product_object else "未识别",
        "patent_holder": state.patent_holder or "未知",
        "invention_point": state.invention_point or "未识别",
        "scenario_words": "、".join(state.scenario_words) if state.scenario_words else "无",
        "audience_words": "、".join(state.audience_words) if state.audience_words else "无"
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

    # 解析组合结果
    combined_keywords = []

    try:
        # 提取 JSON 数组
        json_match = re.search(r'\[[\s\S]*\]', result_text)
        if json_match:
            result_list = json.loads(json_match.group(0))

            for item in result_list:
                if isinstance(item, dict) and item.get("keyword_text"):
                    combined_keywords.append({
                        "keyword_text": item.get("keyword_text", ""),
                        "keyword_type": item.get("keyword_type", "unknown"),
                        "combination_pattern": item.get("combination_pattern", ""),
                        "confidence": item.get("confidence", 0.8)
                    })

    except json.JSONDecodeError:
        # 解析失败，尝试简单文本提取
        lines = result_text.split('\n')
        for line in lines:
            line = line.strip()
            if line and len(line) > 3:
                combined_keywords.append({
                    "keyword_text": line,
                    "keyword_type": "unknown",
                    "combination_pattern": "text_extraction",
                    "confidence": 0.6
                })
    except Exception:
        pass

    return KeywordCombinationOutput(combined_keywords=combined_keywords)
