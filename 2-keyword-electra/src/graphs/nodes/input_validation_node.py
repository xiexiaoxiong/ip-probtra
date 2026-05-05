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

    # 核心判断：只要有权利要求文本或发明内容，就能继续生成关键词
    # 权利要求文本为空但发明内容不为空时，仍可通过发明内容提取关键信息
    has_claim_text = bool(state.claim_text and state.claim_text.strip())
    # 从 InputValidationInput 只能拿到 claim_id 和 claim_text，
    # 但如果 claim_text 为空，说明这条记录确实没有可处理的内容
    if not has_claim_text:
        return InputValidationOutput(
            is_valid=False,
            exception_type="EMPTY_CLAIM_TEXT",
            exception_message="权利要求文本为空，无法进行关键词提取"
        )

    # 专利权人为空、权利要求编号为空等非核心字段缺失，不阻断流程
    # 缺少权利要求编号时自动生成一个默认值
    return InputValidationOutput(
        is_valid=True,
        exception_type="",
        exception_message=""
    )
