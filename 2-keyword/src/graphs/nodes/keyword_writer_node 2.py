"""
关键词写入节点
职责：将关键词结果写入飞书多维表格的新表
"""
import uuid
from typing import List
from datetime import datetime
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from tools.feishu_bitable import FeishuBitable
from graphs.state import KeywordWriterInput, KeywordWriterOutput


def keyword_writer_node(
    state: KeywordWriterInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> KeywordWriterOutput:
    """
    title: 关键词写入
    desc: 将生成的关键词写入飞书多维表格的新数据表
    integrations: 飞书多维表格
    """
    ctx = runtime.context
    
    # 如果没有关键词，返回空结果
    if not state.all_keywords or len(state.all_keywords) == 0:
        return KeywordWriterOutput(
            keywords_table_id="",
            keywords_count=0,
            exception_type="NO_KEYWORDS_TO_WRITE",
            exception_message="没有关键词需要写入"
        )
    
    try:
        # 初始化客户端
        client = FeishuBitable()
        
        # 创建新的数据表用于存储关键词
        table_name = f"关键词结果_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # 定义字段结构（使用简单的文本和数字类型）
        # 注意：飞书 API 创建表时，字段定义需要符合特定格式
        fields = [
            {"field_name": "keyword_id", "type": 1},      # 文本类型
            {"field_name": "claim_id", "type": 1},        # 文本类型
            {"field_name": "keyword_text", "type": 1},    # 文本类型
            {"field_name": "keyword_type", "type": 1},    # 文本类型
            {"field_name": "source_location", "type": 1}, # 文本类型
            {"field_name": "generation_method", "type": 1}, # 文本类型
            {"field_name": "confidence_score", "type": 2}, # 数字类型
            {"field_name": "created_at", "type": 1}       # 文本类型
        ]
        
        # 创建数据表
        create_result = client.create_table(
            app_token=state.app_token,
            table_name=table_name,
            fields=fields
        )
        
        # 检查创建结果
        if create_result.get("code") != 0:
            error_code = create_result.get("code", "unknown")
            error_msg = create_result.get("msg", "未知错误")
            return KeywordWriterOutput(
                keywords_table_id="",
                keywords_count=0,
                exception_type="TABLE_CREATE_ERROR",
                exception_message=f"创建关键词表失败: [{error_code}] {error_msg}"
            )
        
        # 获取新表的 table_id
        keywords_table_id = create_result.get("data", {}).get("table_id", "")
        
        if not keywords_table_id:
            return KeywordWriterOutput(
                keywords_table_id="",
                keywords_count=0,
                exception_type="TABLE_ID_MISSING",
                exception_message="创建关键词表成功，但未返回 table_id"
            )
        
        # 准备写入记录（使用英文字段名匹配新表字段）
        records: List[dict] = []
        for keyword in state.all_keywords:
            record = {
                "fields": {
                    "keyword_id": keyword.get("keyword_id", ""),
                    "claim_id": keyword.get("claim_id", ""),
                    "keyword_text": keyword.get("keyword_text", ""),
                    "keyword_type": keyword.get("keyword_type", ""),
                    "source_location": keyword.get("source_location", ""),
                    "generation_method": keyword.get("generation_method", ""),
                    "confidence_score": keyword.get("confidence_score") if keyword.get("confidence_score") is not None else 0,
                    "created_at": keyword.get("created_at", "")
                }
            }
            records.append(record)
        
        # 批量写入记录（最多一次1000条）
        batch_size = 1000
        total_written = 0
        
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            
            add_result = client.add_records(
                app_token=state.app_token,
                table_id=keywords_table_id,
                records=batch
            )
            
            if add_result.get("code") == 0:
                total_written += len(batch)
        
        return KeywordWriterOutput(
            keywords_table_id=keywords_table_id,
            keywords_count=total_written,
            exception_type="",
            exception_message=""
        )
        
    except Exception as e:
        return KeywordWriterOutput(
            keywords_table_id="",
            keywords_count=0,
            exception_type="KEYWORD_WRITE_ERROR",
            exception_message=f"写入关键词时发生错误: {str(e)}"
        )
