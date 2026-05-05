"""
结构化输出节点
职责：将解析结果整理为标准JSON格式输出
"""
import json
import logging
from typing import List, Dict, Optional
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import (
    StructuredOutputInput,
    StructuredOutputOutput,
    GraphOutput,
    Claim,
    PatentFigure,
    SpecificationSection,
    ParseError
)

logger = logging.getLogger(__name__)


def structured_output_node(
    state: StructuredOutputInput, config: RunnableConfig, runtime: Runtime[Context]
) -> StructuredOutputOutput:
    """
    title: 结构化输出
    desc: 将解析结果整理为标准JSON格式的结构化输出
    """
    ctx = runtime.context
    
    claims_list: List[Claim] = state.claims_list
    specification_sections: List[SpecificationSection] = state.specification_sections
    figures_list: List[PatentFigure] = state.figures_list
    patent_metadata = state.patent_metadata
    task_id: str = state.task_id
    db_record_id: Optional[int] = state.db_record_id
    feishu_app_token: Optional[str] = state.feishu_app_token
    feishu_url: Optional[str] = state.feishu_url
    
    # 收集所有错误
    all_errors: List[ParseError] = []
    if state.read_error:
        all_errors.append(state.read_error)
    all_errors.extend(state.identify_errors)
    all_errors.extend(state.claims_errors)
    all_errors.extend(state.figure_errors)
    
    # 将说明书章节转换为字典格式
    specification_dict: Dict[str, str] = {}
    for section in specification_sections:
        specification_dict[section.section_name] = section.section_text
    
    # 构建最终输出
    final_output = GraphOutput(
        claims=claims_list,
        specification=specification_dict,
        figures=figures_list,
        metadata=patent_metadata,
        errors=all_errors,
        task_id=task_id,
        db_record_id=db_record_id,
        feishu_app_token=feishu_app_token,
        feishu_url=feishu_url
    )
    
    # 转换为JSON字符串
    output_json = json.dumps(
        final_output.model_dump(),
        ensure_ascii=False,
        indent=2
    )
    
    logger.info(
        f"生成结构化输出，权利要求数量: {len(claims_list)}, "
        f"章节数量: {len(specification_dict)}, "
        f"附图数量: {len(figures_list)}, "
        f"飞书多维表格: {feishu_url or '未保存'}"
    )
    
    return StructuredOutputOutput(
        final_output=final_output,
        output_json=output_json
    )
