"""
输入验证节点
职责：验证权利要求文本是否为空
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
    desc: 验证权利要求文本是否为空，确保输入数据有效
    """
    ctx = runtime.context
    
    # 检查权利要求文本是否为空
    if not state.claim_text or state.claim_text.strip() == "":
        return InputValidationOutput(
            is_valid=False,
            exception_type="EMPTY_CLAIM_TEXT",
            exception_message="权利要求文本为空，无法进行关键词提取"
        )
    
    # 检查权利要求编号是否为空
    if not state.claim_id or state.claim_id.strip() == "":
        return InputValidationOutput(
            is_valid=False,
            exception_type="EMPTY_CLAIM_ID",
            exception_message="权利要求编号为空"
        )
    
    return InputValidationOutput(
        is_valid=True,
        exception_type="",
        exception_message=""
    )
