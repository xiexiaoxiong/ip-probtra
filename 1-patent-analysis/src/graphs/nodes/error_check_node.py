"""
异常检查节点（条件分支节点）
职责：检查解析过程中的错误，判断是否可以继续
"""
import logging
from typing import List
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import (
    ErrorCheckInput,
    ErrorCheckOutput,
    ParseError
)

logger = logging.getLogger(__name__)


def error_check_node(
    state: ErrorCheckInput, config: RunnableConfig, runtime: Runtime[Context]
) -> ErrorCheckOutput:
    """
    title: 异常检查
    desc: 检查解析过程中的错误，判断是否存在致命错误以及错误影响范围
    """
    ctx = runtime.context
    
    # 收集所有错误
    all_errors: List[ParseError] = []
    
    if state.file_read_error:
        all_errors.append(state.file_read_error)
    
    all_errors.extend(state.identify_errors)
    all_errors.extend(state.claims_errors)
    
    # 分类错误：致命错误 vs 可恢复错误
    critical_errors: List[ParseError] = []
    recoverable_errors: List[ParseError] = []
    
    for error in all_errors:
        if not error.is_recoverable:
            critical_errors.append(error)
        else:
            recoverable_errors.append(error)
    
    # 判断是否存在致命错误
    has_critical_error = len(critical_errors) > 0
    
    if has_critical_error:
        logger.error(f"发现 {len(critical_errors)} 个致命错误，无法继续解析")
        for err in critical_errors:
            logger.error(f"  - {err.error_type}: {err.error_message}")
    
    if recoverable_errors:
        logger.warning(f"发现 {len(recoverable_errors)} 个可恢复错误，将继续解析")
        for err in recoverable_errors:
            logger.warning(f"  - {err.error_type}: {err.error_message}")
    
    return ErrorCheckOutput(
        has_critical_error=has_critical_error,
        critical_errors=critical_errors,
        recoverable_errors=recoverable_errors
    )


def should_continue_on_error(state: ErrorCheckOutput) -> str:
    """
    title: 错误处理分支
    desc: 根据错误严重程度决定是否继续解析
    """
    if state.has_critical_error:
        return "致命错误"
    else:
        return "继续解析"
