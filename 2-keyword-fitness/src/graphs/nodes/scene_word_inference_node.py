"""
场景词推断节点
职责：根据产品特征推断消费者搜索时可能使用的场景词、人群词、环境词等
这些词消费者常用来缩小搜索范围，但不会出现在专利文本中
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
from graphs.state import SceneWordInferenceInput, SceneWordInferenceOutput


def scene_word_inference_node(
    state: SceneWordInferenceInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> SceneWordInferenceOutput:
    """
    title: 场景词推断
    desc: 根据产品特征推断消费者搜索场景词（如家用、静音、省空间等），这些词消费者常用但不会出现在专利文本中
    integrations: 大语言模型
    """
    ctx = runtime.context

    # 如果没有特征词和客体，返回空列表
    if (not state.filtered_core_terms or len(state.filtered_core_terms) == 0) and \
       (not state.product_object or len(state.product_object) == 0):
        return SceneWordInferenceOutput(scene_words=[])

    # 读取 LLM 配置
    cfg_file = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), config['metadata']['llm_cfg'])
    with open(cfg_file, 'r', encoding='utf-8') as fd:
        _cfg = json.load(fd)

    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", "")
    up = _cfg.get("up", "")

    # 准备特征词文本
    feature_texts = []
    for term in state.filtered_core_terms:
        if isinstance(term, dict):
            text = term.get("text", "")
            if text:
                feature_texts.append(text)
        elif isinstance(term, str):
            feature_texts.append(term)

    # 使用 Jinja2 渲染提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render({
        "filtered_features": "、".join(feature_texts) if feature_texts else "无",
        "product_object": "、".join(state.product_object) if state.product_object else "未识别",
        "invention_point": state.invention_point or "未提供"
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
        max_completion_tokens=llm_config.get("max_completion_tokens", 1000)
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

    # 解析场景词结果
    scene_words = []
    try:
        # 尝试提取 JSON 数组
        json_match = re.search(r'\[[\s\S]*\]', result_text)
        if json_match:
            parsed_list = json.loads(json_match.group(0))
            if isinstance(parsed_list, list):
                for item in parsed_list:
                    if isinstance(item, dict) and item.get("text"):
                        scene_words.append({
                            "text": item.get("text", ""),
                            "category": item.get("category", ""),
                            "inference_reason": item.get("inference_reason", "")
                        })
                    elif isinstance(item, str) and item.strip():
                        scene_words.append({
                            "text": item.strip(),
                            "category": "",
                            "inference_reason": ""
                        })
    except json.JSONDecodeError:
        # JSON 解析失败，按行处理
        lines = [line.strip() for line in result_text.split('\n') if line.strip()]
        for line in lines:
            # 去除序号和多余标记
            clean_line = re.sub(r'^[\d\-\*•\.、\s]+', '', line)
            clean_line = clean_line.strip()
            if clean_line:
                scene_words.append({
                    "text": clean_line,
                    "category": "",
                    "inference_reason": ""
                })

    return SceneWordInferenceOutput(scene_words=scene_words)
