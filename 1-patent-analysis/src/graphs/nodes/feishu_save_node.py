"""
飞书多维表格保存节点
职责：将专利解析数据保存到飞书多维表格
"""
import json
import logging
import os
import time
from typing import List, Optional, Dict, Any
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from cozeloop.decorator import observe

from graphs.state import (
    FeishuSaveInput,
    FeishuSaveOutput,
    Claim,
    PatentFigure,
    SpecificationSection,
    PatentMetadata,
    ParseError
)

logger = logging.getLogger(__name__)

_cached_token: str = ""
_token_expires_at: float = 0.0


class FeishuBitableClient:
    """飞书多维表格客户端"""
    
    def __init__(self):
        self.access_token = self._get_access_token()
        self.base_url = "https://open.larkoffice.com/open-apis"
        self.timeout = 30

    def _get_access_token(self) -> str:
        global _cached_token, _token_expires_at

        direct_token = os.getenv("FEISHU_TENANT_ACCESS_TOKEN", "").strip()
        if direct_token:
            return direct_token

        now = time.time()
        if _cached_token and now < _token_expires_at:
            return _cached_token

        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        if app_id and app_secret:
            import requests

            resp = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                headers={"Content-Type": "application/json; charset=utf-8"},
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise ValueError(f"Failed to get Feishu tenant token: {data}")
            _cached_token = data.get("tenant_access_token", "")
            expire = int(data.get("expire", 7200))
            _token_expires_at = now + max(expire - 300, 60)
            return _cached_token

        try:
            from coze_workload_identity import Client
            client = Client()
            return client.get_integration_credential("integration-feishu-base")
        except Exception as error:
            raise ValueError(
                "Missing Feishu credentials. Set FEISHU_TENANT_ACCESS_TOKEN or FEISHU_APP_ID/FEISHU_APP_SECRET."
            ) from error
    
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}" if self.access_token else "",
            "Content-Type": "application/json; charset=utf-8",
        }
    
    @observe
    def _request(self, method: str, path: str, params: dict = None, json_data: dict = None) -> dict:
        import requests
        try:
            url = f"{self.base_url}{path}"
            resp = requests.request(
                method, url, 
                headers=self._headers(), 
                params=params, 
                json=json_data, 
                timeout=self.timeout
            )
            resp_data = resp.json()
        except Exception as e:
            raise Exception(f"飞书API请求失败: {e}")
        
        if resp_data.get("code") != 0:
            raise Exception(f"飞书API错误: {resp_data}")
        
        return resp_data
    
    def create_base(self, name: str) -> dict:
        """创建多维表格"""
        return self._request("POST", "/bitable/v1/apps", json_data={"name": name})
    
    def list_fields(self, app_token: str, table_id: str) -> dict:
        """列出数据表所有字段"""
        return self._request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields")

    def delete_field(self, app_token: str, table_id: str, field_id: str) -> dict:
        """删除字段"""
        return self._request("DELETE", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}")

    def create_field(self, app_token: str, table_id: str, field_name: str, field_type: int = 1) -> dict:
        """创建字段
        field_type: 1=文本, 2=数字, 3=单选, 4=多选, 5=日期, 7=复选框, 11=人员, 13=电话, 14=网址, 15=附件, 17=位置
        """
        body = {"field_name": field_name, "type": field_type}
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", json_data=body)
    
    def create_table(self, app_token: str, table_name: str, fields: list = None) -> dict:
        """创建数据表
        
        注意：飞书API要求请求体格式为 {"table": {"name": "表名"}}
        """
        body: Dict[str, Any] = {"table": {"name": table_name}}
        # 注意：飞书API创建表时不支持同时创建字段，字段需要单独创建
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables", json_data=body)
    
    def add_records(self, app_token: str, table_id: str, records: list) -> dict:
        """批量新增记录"""
        return self._request(
            "POST", 
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
            json_data={"records": records}
        )

    def list_records(self, app_token: str, table_id: str, page_size: int = 100) -> dict:
        """列出数据表中的记录"""
        return self._request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            params={"page_size": page_size}
        )

    def delete_records(self, app_token: str, table_id: str, record_ids: list) -> dict:
        """批量删除记录"""
        return self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
            json_data={"records": record_ids}
        )


def _clear_default_fields(client: FeishuBitableClient, app_token: str, table_id: str) -> str:
    """清理默认字段，返回主键字段ID
    
    飞书创建多维表格/数据表时会自动生成默认列（如"文本"、"单选"、"日期"、"附件"等），
    这些列会占据前几列位置，导致自定义数据从第N列才开始显示。
    
    策略：
    1. 主键字段(Primary Field)不可删除，改为重命名复用
    2. 其余默认字段全部删除
    
    Returns:
        主键字段的 field_id，供后续重命名使用
    """
    primary_field_id: str = ""
    try:
        fields_resp = client.list_fields(app_token=app_token, table_id=table_id)
        items = fields_resp.get("data", {}).get("items", [])
        for field in items:
            field_id = field.get("field_id", "")
            field_name = field.get("field_name", "")
            is_primary = field.get("is_primary", False)
            if is_primary:
                # 主键字段不可删除，记录其ID用于后续重命名
                primary_field_id = field_id
                logger.info(f"保留主键字段: {field_name} ({field_id})")
            else:
                # 非主键默认字段，直接删除
                if field_id:
                    try:
                        client.delete_field(app_token=app_token, table_id=table_id, field_id=field_id)
                        logger.info(f"已删除默认字段: {field_name} ({field_id})")
                    except Exception as e:
                        logger.warning(f"删除默认字段失败: {field_name}, 错误: {str(e)}")
    except Exception as e:
        logger.warning(f"获取字段列表失败: {str(e)}")
    
    return primary_field_id


def _rename_field(client: FeishuBitableClient, app_token: str, table_id: str, field_id: str, new_name: str) -> None:
    """重命名字段（用于将主键字段改为自定义名称）"""
    body = {"field_name": new_name, "type": 1}
    client._request("PUT", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}", json_data=body)
    logger.info(f"字段重命名成功: {field_id} -> {new_name}")


def _clear_default_records(client: FeishuBitableClient, app_token: str, table_id: str) -> None:
    """删除数据表中的默认空行
    
    飞书创建多维表格/数据表时会自动生成若干空行，
    导致后续写入的数据从第N行才开始显示。
    需要在写入自定义数据前先清除这些默认空行。
    """
    try:
        records_resp = client.list_records(app_token=app_token, table_id=table_id)
        items = records_resp.get("data", {}).get("items", [])
        if not items:
            logger.info("无默认记录需要删除")
            return
        
        record_ids: List[str] = []
        for item in items:
            rid = item.get("record_id", "")
            if rid:
                record_ids.append(rid)
        
        if record_ids:
            client.delete_records(app_token=app_token, table_id=table_id, record_ids=record_ids)
            logger.info(f"已删除 {len(record_ids)} 条默认记录")
    except Exception as e:
        logger.warning(f"清理默认记录失败: {str(e)}")


def _setup_table_fields(client: FeishuBitableClient, app_token: str, table_id: str, fields: list) -> None:
    """为数据表设置自定义字段（清理默认 + 创建自定义）
    
    Args:
        fields: 列表，每项为 (field_name, field_type) 元组。
                第一个元素将用于重命名主键字段，其余元素通过 create_field 新建。
    """
    if not fields:
        return
    
    # 1. 清理默认字段，获取主键字段ID
    primary_field_id = _clear_default_fields(client, app_token, table_id)
    
    # 2. 将主键字段重命名为第一个自定义字段
    if primary_field_id:
        first_field_name = fields[0][0]
        try:
            _rename_field(client, app_token, table_id, primary_field_id, first_field_name)
        except Exception as e:
            logger.warning(f"重命名主键字段失败: {str(e)}")
    
    # 3. 创建剩余的自定义字段（跳过第一个，因为它已通过重命名实现）
    for field_name, field_type in fields[1:]:
        try:
            client.create_field(
                app_token=app_token,
                table_id=table_id,
                field_name=field_name,
                field_type=field_type
            )
            logger.info(f"字段创建成功: {field_name}")
        except Exception as e:
            logger.warning(f"字段创建失败（可能已存在）: {field_name}, 错误: {str(e)}")
    
    # 4. 删除默认空行，确保数据从第1行开始写入
    _clear_default_records(client, app_token, table_id)


def feishu_save_node(
    state: FeishuSaveInput, config: RunnableConfig, runtime: Runtime[Context]
) -> FeishuSaveOutput:
    """
    title: 飞书多维表格保存
    desc: 创建飞书多维表格并将专利解析数据写入，包括专利主表、权利要求表和附图表
    integrations: 飞书多维表格
    """
    ctx = runtime.context
    
    feishu_app_token: str = ""
    feishu_url: str = ""
    patent_record_id: str = ""
    save_success: bool = False
    save_error: Optional[ParseError] = None
    
    try:
        client = FeishuBitableClient()
        
        # 1. 创建多维表格
        patent_number = state.patent_metadata.patent_number or "未知专利号"
        base_name = f"专利解析_{patent_number}"
        
        logger.info(f"正在创建飞书多维表格: {base_name}")
        base_result = client.create_base(name=base_name)
        logger.info(f"飞书API返回: {json.dumps(base_result, ensure_ascii=False)}")
        
        # 解析返回数据 - 飞书API返回的字段可能是 app 或 app_token
        data = base_result.get("data", {})
        if "app" in data:
            feishu_app_token = data["app"].get("app_token", "")
        else:
            feishu_app_token = data.get("app_token", "")
        
        if not feishu_app_token:
            raise Exception(f"无法获取app_token，API返回: {base_result}")
        
        feishu_url = f"https://feishu.cn/base/{feishu_app_token}"
        logger.info(f"多维表格创建成功: {feishu_url}")
        
        # 2. 获取默认表作为专利主表（飞书创建多维表格时会自动创建一个默认表）
        default_table_id = data.get("app", {}).get("default_table_id", "") if "app" in data else data.get("default_table_id", "")
        if not default_table_id:
            raise Exception(f"无法获取默认表ID")
        
        patent_table_id = default_table_id  # 使用默认表作为专利主表
        logger.info(f"使用默认表作为专利主表: {patent_table_id}")
        
        # 3. 清理默认字段并创建自定义字段
        fields_to_create = [
            ("任务ID", 1),
            ("专利号", 1),
            ("专利标题", 1),
            ("专利权人", 1),
            ("申请日期", 1),
            ("优先权日期", 1),
            ("技术领域", 1),
            ("背景技术", 1),
            ("发明内容", 1),
            ("解析状态", 1),
        ]
        _setup_table_fields(client, feishu_app_token, patent_table_id, fields_to_create)
        
        # 4. 写入专利主表数据
        spec_dict = _convert_sections_to_dict(state.specification_sections)
        
        patent_record = {
            "fields": {
                "任务ID": state.task_id,
                "专利号": state.patent_metadata.patent_number or "",
                "专利标题": state.patent_metadata.title or "",
                "专利权人": state.patent_metadata.patent_holder or "",
                "申请日期": state.patent_metadata.application_date or "",
                "优先权日期": state.patent_metadata.priority_date or "",
                "技术领域": spec_dict.get("技术领域", ""),
                "背景技术": spec_dict.get("背景技术", ""),
                "发明内容": spec_dict.get("发明内容", "") or spec_dict.get("实用新型内容", ""),
                "解析状态": "成功" if not state.read_error else "有错误",
            }
        }
        
        patent_result = client.add_records(
            app_token=feishu_app_token,
            table_id=patent_table_id,
            records=[patent_record]
        )
        logger.info(f"专利主表写入API返回: {json.dumps(patent_result, ensure_ascii=False)[:500]}")
        records_data = patent_result.get("data", {}).get("records", [])
        if records_data:
            patent_record_id = records_data[0].get("record_id", "")
        logger.info(f"专利主表记录写入成功: {patent_record_id}")
        
        # 5. 创建权利要求表
        claims_table_id: str = ""
        if state.claims_list and len(state.claims_list) > 0:
            try:
                logger.info("正在创建权利要求表...")
                claims_table_result = client.create_table(
                    app_token=feishu_app_token,
                    table_name="权利要求表"
                )
                claims_table_id = claims_table_result.get("data", {}).get("table_id", "")
                if not claims_table_id:
                    raise Exception("无法获取权利要求表table_id")
                logger.info(f"权利要求表创建成功: {claims_table_id}")
                
                # 清理默认字段并创建自定义字段
                claims_fields = [
                    ("权利要求编号", 1),
                    ("类型", 1),
                    ("原文", 1),
                    ("父权利要求", 1),
                    ("句子单元", 1),
                ]
                _setup_table_fields(client, feishu_app_token, claims_table_id, claims_fields)
                
                # 写入权利要求数据
                claims_records = []
                for claim in state.claims_list:
                    claim_type_text = "独立权利要求" if claim.claim_type == "INDEPENDENT" else "从属权利要求"
                    claims_records.append({
                        "fields": {
                            "权利要求编号": claim.claim_id,
                            "类型": claim_type_text,
                            "原文": claim.claim_text,
                            "父权利要求": claim.parent_claim_id or "",
                            "句子单元": "\n".join(claim.sentence_units) if claim.sentence_units else "",
                        }
                    })
                
                if claims_records:
                    client.add_records(
                        app_token=feishu_app_token,
                        table_id=claims_table_id,
                        records=claims_records
                    )
                    logger.info(f"权利要求表写入 {len(claims_records)} 条记录")
                    
            except Exception as e:
                logger.error(f"权利要求表写入失败: {str(e)}", exc_info=True)
        else:
            logger.info("无权利要求数据，跳过创建权利要求表")
        
        # 6. 创建附图表
        figures_table_id: str = ""
        if state.figures_list and len(state.figures_list) > 0:
            try:
                logger.info("正在创建附图表...")
                figures_table_result = client.create_table(
                    app_token=feishu_app_token,
                    table_name="附图表"
                )
                figures_table_id = figures_table_result.get("data", {}).get("table_id", "")
                if not figures_table_id:
                    raise Exception("无法获取附图表table_id")
                logger.info(f"附图表创建成功: {figures_table_id}")
                
                # 清理默认字段并创建自定义字段
                figures_fields = [
                    ("附图编号", 1),
                    ("附图URL", 1),
                    ("附图说明", 1),
                ]
                _setup_table_fields(client, feishu_app_token, figures_table_id, figures_fields)
                
                # 写入附图数据
                figures_records = []
                for figure in state.figures_list:
                    figures_records.append({
                        "fields": {
                            "附图编号": figure.figure_id,
                            "附图URL": figure.figure_url,
                            "附图说明": figure.figure_description or "",
                        }
                    })
                
                if figures_records:
                    client.add_records(
                        app_token=feishu_app_token,
                        table_id=figures_table_id,
                        records=figures_records
                    )
                    logger.info(f"附图表写入 {len(figures_records)} 条记录")
                    
            except Exception as e:
                logger.error(f"附图表写入失败: {str(e)}", exc_info=True)
        else:
            logger.info("无附图数据，跳过创建附图表")
        
        save_success = True
        logger.info(f"飞书多维表格保存完成: {feishu_url}")
        
    except Exception as e:
        logger.error(f"飞书多维表格保存失败: {str(e)}", exc_info=True)
        save_error = ParseError(
            error_type="FEISHU_SAVE_ERROR",
            error_message=f"飞书多维表格保存失败: {str(e)}",
            is_recoverable=True
        )
    
    return FeishuSaveOutput(
        feishu_app_token=feishu_app_token,
        feishu_url=feishu_url,
        patent_record_id=patent_record_id,
        save_success=save_success,
        save_error=save_error
    )


def _convert_sections_to_dict(sections: List[SpecificationSection]) -> Dict[str, str]:
    """将说明书章节列表转换为字典"""
    result: Dict[str, str] = {}
    for section in sections:
        result[section.section_name] = section.section_text
    return result
