"""
输入验证节点
职责：验证输入是否足以进行关键词提取，并在缺少权利要求时启用降级路径
"""
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import InputValidationInput, InputValidationOutput


def input_validation_node(
    state: InputValidationInput, 
    config: RunnableConfig, 
    runtime: Runtime[Context]
) -> InputValidationOutput:
    """
    title: 输入验证
    desc: 验证输入数据有效性；当缺少权利要求时回退到说明书上下文
    """
    has_claim_text = bool(state.claim_text and state.claim_text.strip())
    has_context = any(
        value and str(value).strip()
        for value in [state.technical_field, state.invention_content, state.background_tech]
    )

    if has_claim_text:
        return InputValidationOutput(
            is_valid=True,
            use_fallback_context=False,
            exception_type="",
            exception_message=""
        )

    if has_context:
        return InputValidationOutput(
            is_valid=True,
            use_fallback_context=True,
            exception_type="",
            exception_message="权利要求缺失，已切换到说明书降级路径"
        )

    return InputValidationOutput(
        is_valid=False,
        use_fallback_context=False,
        exception_type="EMPTY_INPUT_CONTEXT",
        exception_message="权利要求文本和说明书上下文均为空，无法进行关键词提取"
    )
