"""
人群场景词推断节点
职责：从产品特征推断消费者搜索时使用的场景词和人群词
专利文本不会写"本发明适合小户型使用"，但消费者搜索时确实会用"家用""静音""老人"等词
本节点通过分析产品特征，推断出消费者可能使用的场景词和人群词
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
from graphs.state import ScenarioAudienceInferenceInput, ScenarioAudienceInferenceOutput


def scenario_audience_inference_node(
    state: ScenarioAudienceInferenceInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ScenarioAudienceInferenceOutput:
    """
    title: 人群场景词推断
    desc: 从产品特征推断消费者搜索人群词和场景词（如家用、静音、老人、康复等）
    integrations: 大语言模型
    """
    ctx = runtime.context

    # 如果没有发明点信息，返回空
    if not state.invention_point and not state.claim_text:
        return ScenarioAudienceInferenceOutput(scenario_words=[], audience_words=[])

    # 读取 LLM 配置
    cfg_file = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), config['metadata']['llm_cfg'])
    with open(cfg_file, 'r', encoding='utf-8') as fd:
        _cfg = json.load(fd)

    llm_config = _cfg.get("config", {})
    sp = _cfg.get("sp", "")
    up = _cfg.get("up", "")

    # 准备核心术语文本
    core_term_texts = [term.get("text", "") for term in state.filtered_core_terms if term.get("text")]

    # 使用 Jinja2 渲染提示词
    up_tpl = Template(up)
    user_prompt = up_tpl.render({
        "invention_point": state.invention_point or "未识别",
        "invention_content": state.invention_content or "无",
        "claim_text": state.claim_text or "无",
        "primary_product_object": state.primary_product_object or "未识别",
        "search_product_objects": "、".join(state.search_product_objects) if state.search_product_objects else "无",
        "product_object": "、".join(state.product_object) if state.product_object else "未识别",
        "core_terms": "、".join(core_term_texts) if core_term_texts else "无"
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
        temperature=llm_config.get("temperature", 0.5),
        max_completion_tokens=llm_config.get("max_completion_tokens", 1500)
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

    # 解析结果
    scenario_words: list = []
    audience_words: list = []

    try:
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            result_dict = json.loads(json_match.group(0))
            raw_scenario = result_dict.get("scenario_words", [])
            raw_audience = result_dict.get("audience_words", [])

            if isinstance(raw_scenario, list):
                scenario_words = [str(w).strip() for w in raw_scenario if str(w).strip()]
            if isinstance(raw_audience, list):
                audience_words = [str(w).strip() for w in raw_audience if str(w).strip()]

    except json.JSONDecodeError:
        # 解析失败，尝试简单文本提取
        lines = result_text.split('\n')
        current_section = ""
        for line in lines:
            line = line.strip()
            if "场景" in line:
                current_section = "scenario"
            elif "人群" in line:
                current_section = "audience"
            elif line and len(line) > 1 and current_section:
                word = line.lstrip("-•·0123456789.、 ").strip()
                if word:
                    if current_section == "scenario":
                        scenario_words.append(word)
                    elif current_section == "audience":
                        audience_words.append(word)
    except Exception:
        pass

    return ScenarioAudienceInferenceOutput(
        scenario_words=scenario_words,
        audience_words=audience_words
    )
