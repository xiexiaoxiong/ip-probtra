"""
飞书链接解析节点
职责：从飞书多维表格链接中解析出 app_token
"""
import re
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import FeishuUrlParserInput, FeishuUrlParserOutput


def feishu_url_parser_node(
    state: FeishuUrlParserInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> FeishuUrlParserOutput:
    """
    title: 飞书链接解析
    desc: 从飞书多维表格链接中解析出 app_token
    """
    ctx = runtime.context
    
    url = state.feishu_url
    
    if not url or url.strip() == "":
        return FeishuUrlParserOutput(
            app_token="",
            exception_type="EMPTY_URL",
            exception_message="飞书多维表格链接为空"
        )
    
    # 解析飞书多维表格链接
    # 链接格式：https://xxx.feishu.cn/base/xxx
    # 或者：https://xxx.feishu.cn/base/xxx?table=xxx
    
    try:
        # 提取 app_token（base 或 wiki 后面的部分）
        app_token_match = re.search(r'(?:base|wiki)/([a-zA-Z0-9]+)', url)
        if not app_token_match:
            return FeishuUrlParserOutput(
                app_token="",
                exception_type="INVALID_URL_FORMAT",
                exception_message="无法从链接中解析出 app_token，请确认链接格式正确"
            )
        
        app_token = app_token_match.group(1)
        
        return FeishuUrlParserOutput(
            app_token=app_token,
            exception_type="",
            exception_message=""
        )
        
    except Exception as e:
        return FeishuUrlParserOutput(
            app_token="",
            exception_type="URL_PARSE_ERROR",
            exception_message=f"解析飞书链接时发生错误: {str(e)}"
        )
