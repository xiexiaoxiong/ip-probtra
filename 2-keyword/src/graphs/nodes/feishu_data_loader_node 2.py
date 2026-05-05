"""
飞书数据加载节点
职责：从飞书多维表格中读取所有数据表、字段结构和样本数据
"""
import json
from typing import List, Dict, Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from tools.feishu_bitable import FeishuBitable
from graphs.state import FeishuDataLoaderInput, FeishuDataLoaderOutput, TableInfo


def feishu_data_loader_node(
    state: FeishuDataLoaderInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> FeishuDataLoaderOutput:
    """
    title: 飞书数据加载
    desc: 从飞书多维表格中读取所有数据表（数据表、权利要求表、附图表），获取字段结构和样本数据
    integrations: 飞书多维表格
    """
    ctx = runtime.context
    
    try:
        # 初始化客户端
        client = FeishuBitable()
        
        app_token = state.app_token
        
        # 1. 获取多维表格下的所有数据表
        tables_result = client.list_tables(app_token=app_token)
        
        if tables_result.get("code") != 0:
            return FeishuDataLoaderOutput(
                all_tables_info={},
                tables=[],
                exception_type="FEISHU_API_ERROR",
                exception_message=f"获取数据表列表失败: {tables_result.get('msg', '未知错误')}"
            )
        
        tables = tables_result.get("data", {}).get("items", [])
        
        if not tables or len(tables) == 0:
            return FeishuDataLoaderOutput(
                all_tables_info={},
                tables=[],
                exception_type="NO_TABLES_FOUND",
                exception_message="该多维表格中没有找到任何数据表"
            )
        
        # 2. 对每个表获取字段结构和样本数据
        all_tables_info: Dict[str, Any] = {}
        table_info_list: List[TableInfo] = []
        
        for table in tables:
            table_id = table.get("table_id", "")
            table_name = table.get("name", "")
            
            if not table_id:
                continue
            
            # 2.1 获取该表的字段列表
            fields_result = client.list_fields(app_token=app_token, table_id=table_id)
            
            fields: List[Dict[str, Any]] = []
            if fields_result.get("code") == 0:
                fields = fields_result.get("data", {}).get("items", [])
            
            # 2.2 获取该表的样本数据（前5条记录）
            sample_result = client.search_record(
                app_token=app_token,
                table_id=table_id,
                page_size=5
            )
            
            sample_records: List[Dict[str, Any]] = []
            if sample_result.get("code") == 0:
                items = sample_result.get("data", {}).get("items", [])
                for item in items:
                    sample_records.append({
                        "record_id": item.get("record_id", ""),
                        "fields": item.get("fields", {})
                    })
            
            # 构建表信息
            table_info = TableInfo(
                table_id=table_id,
                table_name=table_name,
                fields=fields,
                sample_records=sample_records
            )
            
            table_info_list.append(table_info)
            all_tables_info[table_id] = {
                "table_id": table_id,
                "table_name": table_name,
                "fields": fields,
                "sample_records": sample_records
            }
        
        return FeishuDataLoaderOutput(
            all_tables_info=all_tables_info,
            tables=table_info_list,
            exception_type="",
            exception_message=""
        )
        
    except Exception as e:
        return FeishuDataLoaderOutput(
            all_tables_info={},
            tables=[],
            exception_type="DATA_LOAD_ERROR",
            exception_message=f"加载飞书数据时发生错误: {str(e)}"
        )
