"""
记录分发节点
职责：从记录列表中取出当前需要处理的记录，并提取字段值
"""
from typing import List, Dict, Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import RecordDispatchInput, RecordDispatchOutput


def record_dispatch_node(
    state: RecordDispatchInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> RecordDispatchOutput:
    """
    title: 记录分发
    desc: 从整合后的专利记录列表中取出当前需要处理的记录，提取字段值供后续节点使用
    """
    ctx = runtime.context
    
    records = state.integrated_patent_records
    record_index = state.current_record_index
    claim_index = state.current_claim_index
    field_mapping = state.field_mapping
    
    total_records = len(records)
    
    # 检查是否还有记录需要处理
    if record_index >= total_records:
        return RecordDispatchOutput(
            current_record={},
            current_record_index=record_index,
            current_claim_index=0,
            total_records=total_records,
            has_more_records=False,
            exception_type="",
            exception_message=""
        )
    
    # 获取当前记录
    current_record = records[record_index]
    data_record = current_record.get("data_record", {})
    claim_records = current_record.get("claim_records", [])
    figure_records = current_record.get("figure_records", [])
    
    fields = data_record.get("fields", {})
    
    # 使用字段映射提取数据
    def get_field_value(standard_name: str, default: str = "") -> str:
        """根据字段映射获取字段值"""
        feishu_field_name = field_mapping.get(standard_name, "")
        if feishu_field_name and feishu_field_name in fields:
            value = fields.get(feishu_field_name, "")
            if isinstance(value, str):
                return value
            elif isinstance(value, list):
                return ", ".join(str(v) for v in value)
            else:
                return str(value)
        return default
    
    # 获取附图描述
    figure_descriptions: List[str] = []
    for fig_record in figure_records:
        fig_fields = fig_record.get("fields", {})
        fig_desc_field = field_mapping.get("figure_description", "")
        if fig_desc_field and fig_desc_field in fig_fields:
            fig_desc = fig_fields.get(fig_desc_field, "")
            if fig_desc:
                figure_descriptions.append(str(fig_desc))
    description_figures = "\n".join(figure_descriptions)
    
    # 确定当前要处理的权利要求
    claim_id = ""
    claim_type = "INDEPENDENT"
    claim_text = ""
    
    if claim_records and claim_index < len(claim_records):
        # 处理权利要求表中的记录
        claim_record = claim_records[claim_index]
        claim_fields = claim_record.get("fields", {})
        
        claim_id_field = field_mapping.get("claim_id", "")
        if claim_id_field and claim_id_field in claim_fields:
            claim_id = str(claim_fields.get(claim_id_field, ""))
        
        claim_type_field = field_mapping.get("claim_type", "")
        if claim_type_field and claim_type_field in claim_fields:
            claim_type_val = claim_fields.get(claim_type_field, "")
            if "独立" in str(claim_type_val) or "INDEPENDENT" in str(claim_type_val).upper():
                claim_type = "INDEPENDENT"
            else:
                claim_type = "DEPENDENT"
        
        claim_text_field = field_mapping.get("claim_text", "")
        if claim_text_field and claim_text_field in claim_fields:
            claim_text = str(claim_fields.get(claim_text_field, ""))
    else:
        # 如果没有权利要求表或已处理完，使用数据表中的发明内容
        claim_id = get_field_value("patent_number", "")
        claim_type = "INDEPENDENT"
        claim_text = get_field_value("invention_content", "")
    
    return RecordDispatchOutput(
        current_record=current_record,
        current_record_index=record_index,
        current_claim_index=claim_index,
        total_records=total_records,
        claim_id=claim_id,
        claim_type=claim_type,
        claim_text=claim_text,
        background_tech=get_field_value("background_tech", ""),
        technical_field=get_field_value("technical_field", ""),
        invention_content=get_field_value("invention_content", ""),
        description_figures=description_figures,
        patent_holder=get_field_value("patent_holder", ""),
        patent_number=get_field_value("patent_number", ""),
        application_date=get_field_value("application_date", ""),
        has_more_records=True,
        exception_type="",
        exception_message=""
    )
