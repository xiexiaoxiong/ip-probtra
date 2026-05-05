"""
数据整合节点
职责：使用大模型理解三个表的关系，并整合数据
"""
import os
import json
import re
import traceback
from typing import List, Dict, Any
from jinja2 import Template
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import LLMClient
from graphs.state import DataIntegrationInput, DataIntegrationOutput


def data_integration_node(
    state: DataIntegrationInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> DataIntegrationOutput:
    """
    title: 数据整合分析
    desc: 使用大模型分析三个表（数据表、权利要求表、附图表）的关系，识别字段含义并整合数据
    integrations: 大语言模型
    """
    ctx = runtime.context
    
    try:
        # 1. 读取大模型配置
        cfg_file = os.path.join(os.getenv("COZE_WORKSPACE_PATH", ""), config.get("metadata", {}).get("llm_cfg", "config/data_integration_llm_cfg.json"))
        with open(cfg_file, 'r', encoding='utf-8') as fd:
            _cfg = json.load(fd)
        
        llm_config = _cfg.get("config", {})
        sp_template = _cfg.get("sp", "")
        up_template = _cfg.get("up", "")
        
        # 2. 准备表信息用于大模型分析
        tables_info_for_analysis: List[Dict[str, Any]] = []
        for table_id, table_info in state.all_tables_info.items():
            table_name = table_info.get("table_name", "")
            fields = table_info.get("fields", [])
            sample_records = table_info.get("sample_records", [])
            
            # 构建字段描述
            fields_desc = []
            for field in fields:
                field_name = field.get("field_name", "")
                field_type = field.get("type", 0)
                field_desc = field.get("description", "")
                fields_desc.append(f"- {field_name} (类型: {field_type}, 描述: {field_desc})")
            
            # 构建样本数据描述（仅显示字段名和值的类型）
            samples_desc = []
            for i, record in enumerate(sample_records[:3]):  # 只取前3条样本
                record_fields = record.get("fields", {})
                sample_items = []
                for field_name, field_value in record_fields.items():
                    value_type = type(field_value).__name__
                    # 截取值的前50个字符作为示例
                    value_preview = str(field_value)[:50] if field_value else "空"
                    sample_items.append(f"  {field_name}: {value_preview} ({value_type})")
                samples_desc.append(f"样本{i+1}:\n" + "\n".join(sample_items))
            
            tables_info_for_analysis.append({
                "table_id": table_id,
                "table_name": table_name,
                "fields": "\n".join(fields_desc),
                "samples": "\n\n".join(samples_desc)
            })
        
        # 3. 使用 Jinja2 渲染提示词
        sp_tpl = Template(sp_template)
        system_prompt = sp_tpl.render({})
        
        up_tpl = Template(up_template)
        user_prompt = up_tpl.render({
            "tables_info": json.dumps(tables_info_for_analysis, ensure_ascii=False, indent=2)
        })
        
        # 4. 调用大模型
        client = LLMClient(ctx=ctx)
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ]
        
        response = client.invoke(
            messages=messages,
            model=llm_config.get("model", "glm-5-0-260211"),
            temperature=llm_config.get("temperature", 0.3)
        )
        
        # 5. 解析大模型返回结果
        response_content = response.content
        if isinstance(response_content, list):
            # 如果是列表，提取文本部分
            text_parts = []
            for item in response_content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif isinstance(item, str):
                    text_parts.append(item)
            response_content = "\n".join(text_parts)
        
        # 尝试解析 JSON
        analysis_result: Dict[str, Any] = {}
        try:
            # 尝试直接解析
            analysis_result = json.loads(response_content)
        except json.JSONDecodeError:
            # 尝试提取 JSON 块
            json_match = re.search(r'\{[\s\S]*\}', response_content)
            if json_match:
                analysis_result = json.loads(json_match.group(0))
            else:
                return DataIntegrationOutput(
                    integrated_patent_records=[],
                    field_mapping={},
                    table_identification={},
                    exception_type="LLM_RESPONSE_PARSE_ERROR",
                    exception_message=f"无法解析大模型返回的JSON: {response_content[:200]}"
                )
        
        # 6. 提取分析结果
        table_identification = analysis_result.get("table_identification", {})
        field_mapping = analysis_result.get("field_mapping", {})
        link_field = analysis_result.get("link_field", "")
        
        # 7. 根据分析结果查询并整合数据
        from tools.feishu_bitable import FeishuBitable
        client_bitable = FeishuBitable()
        
        # 获取每个表的完整数据
        all_table_data: Dict[str, List[Dict]] = {}
        
        data_table_id = table_identification.get("data_table", "")
        claim_table_id = table_identification.get("claim_table", "")
        figure_table_id = table_identification.get("figure_table", "")
        
        # 查询数据表
        if data_table_id:
            data_result = client_bitable.search_record(
                app_token=state.app_token,
                table_id=data_table_id,
                page_size=500
            )
            if data_result.get("code") == 0:
                all_table_data["data_table"] = data_result.get("data", {}).get("items", [])
        
        # 查询权利要求表
        if claim_table_id:
            claim_result = client_bitable.search_record(
                app_token=state.app_token,
                table_id=claim_table_id,
                page_size=500
            )
            if claim_result.get("code") == 0:
                all_table_data["claim_table"] = claim_result.get("data", {}).get("items", [])
        
        # 查询附图表
        if figure_table_id:
            figure_result = client_bitable.search_record(
                app_token=state.app_token,
                table_id=figure_table_id,
                page_size=500
            )
            if figure_result.get("code") == 0:
                all_table_data["figure_table"] = figure_result.get("data", {}).get("items", [])
        
        # 8. 整合数据：按关联字段关联三个表
        integrated_records: List[Dict[str, Any]] = []
        
        if link_field and all_table_data.get("data_table"):
            # 构建权利要求表和附图表的索引
            claim_index: Dict[str, List[Dict]] = {}
            figure_index: Dict[str, List[Dict]] = {}
            
            # 获取权利要求表中的关联字段名
            claim_link_field = field_mapping.get("link_field_claim", link_field)
            # 获取附图表中的关联字段名
            figure_link_field = field_mapping.get("link_field_figure", link_field)
            
            # 构建权利要求索引
            for record in all_table_data.get("claim_table", []):
                fields = record.get("fields", {})
                link_value = str(fields.get(claim_link_field, fields.get(link_field, "")))
                if link_value:
                    if link_value not in claim_index:
                        claim_index[link_value] = []
                    claim_index[link_value].append(record)
            
            # 构建附图索引
            for record in all_table_data.get("figure_table", []):
                fields = record.get("fields", {})
                link_value = str(fields.get(figure_link_field, fields.get(link_field, "")))
                if link_value:
                    if link_value not in figure_index:
                        figure_index[link_value] = []
                    figure_index[link_value].append(record)
            
            # 整合数据
            for data_record in all_table_data.get("data_table", []):
                fields = data_record.get("fields", {})
                link_value = str(fields.get(link_field, ""))
                
                integrated_record: Dict[str, Any] = {
                    "data_record": {
                        "record_id": data_record.get("record_id", ""),
                        "fields": fields
                    },
                    "claim_records": [],
                    "figure_records": []
                }
                
                # 关联权利要求
                if link_value in claim_index:
                    integrated_record["claim_records"] = [
                        {"record_id": r.get("record_id", ""), "fields": r.get("fields", {})}
                        for r in claim_index[link_value]
                    ]
                
                # 关联附图
                if link_value in figure_index:
                    integrated_record["figure_records"] = [
                        {"record_id": r.get("record_id", ""), "fields": r.get("fields", {})}
                        for r in figure_index[link_value]
                    ]
                
                integrated_records.append(integrated_record)
        else:
            # 如果没有关联字段，直接返回数据表记录
            for data_record in all_table_data.get("data_table", []):
                integrated_records.append({
                    "data_record": {
                        "record_id": data_record.get("record_id", ""),
                        "fields": data_record.get("fields", {})
                    },
                    "claim_records": [],
                    "figure_records": []
                })
        
        return DataIntegrationOutput(
            integrated_patent_records=integrated_records,
            field_mapping=field_mapping,
            table_identification=table_identification,
            exception_type="",
            exception_message=""
        )
        
    except Exception as e:
        return DataIntegrationOutput(
            integrated_patent_records=[],
            field_mapping={},
            table_identification={},
            exception_type="DATA_INTEGRATION_ERROR",
            exception_message=f"数据整合时发生错误: {str(e)}\n{traceback.format_exc()}"
        )
