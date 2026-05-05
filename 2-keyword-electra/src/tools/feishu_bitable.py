"""
飞书多维表格工具
提供飞书多维表格 API 的封装
"""
import json
import os
import re
import requests
import time
from functools import wraps
from typing import Optional, List, Dict, Any
from cozeloop.decorator import observe

_cached_token: str = ""
_token_expires_at: float = 0.0


def get_access_token() -> str:
    """
    获取飞书多维表格（Bitable）的租户访问令牌。
    优先级：
    1. FEISHU_TENANT_ACCESS_TOKEN
    2. FEISHU_APP_ID + FEISHU_APP_SECRET
    3. Coze workload identity（兼容旧环境）
    """
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
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            headers={"Content-Type": "application/json; charset=utf-8"},
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=30,
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


def require_token(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        self.access_token = get_access_token()
        if not self.access_token:
            raise ValueError("FEISHU_TENANT_ACCESS_TOKEN is not set")
        return func(self, *args, **kwargs)
    return wrapper


class FeishuBitable:
    """
    飞书多维表格（Bitable）HTTP 客户端。
    所有方法返回值均为 Feishu OpenAPI 标准响应：{"code": int, "msg": str, "data": any}
    基础 URL 默认 "https://open.larkoffice.com/open-apis"。
    """
    def __init__(self, base_url: str = "https://open.larkoffice.com/open-apis", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.access_token = get_access_token()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}" if self.access_token else "",
            "Content-Type": "application/json; charset=utf-8",
        }

    @observe
    def _request(self, method: str, path: str, params: dict | None = None, json_data: dict | None = None) -> dict:
        try:
            url = f"{self.base_url}{path}"
            resp = requests.request(method, url, headers=self._headers(), params=params, json=json_data, timeout=self.timeout)
            resp_text = resp.text
            
            # 尝试解析JSON
            try:
                resp_data = resp.json()
            except Exception as json_err:
                # 如果JSON解析失败，尝试清理响应内容
                # 移除可能的BOM和非JSON字符
                cleaned_text = resp_text.strip()
                if cleaned_text.startswith('\ufeff'):
                    cleaned_text = cleaned_text[1:]
                # 尝试找到JSON对象
                json_match = re.search(r'\{.*\}', cleaned_text, re.DOTALL)
                if json_match:
                    resp_data = json.loads(json_match.group(0))
                else:
                    raise Exception(f"Failed to parse response as JSON: {resp_text[:200]}")
            
        except requests.exceptions.RequestException as e:
            raise Exception(f"FeishuBitable API request error: {e}")
        except Exception as e:
            raise Exception(f"FeishuBitable API error: {e}")
        return resp_data

    @require_token
    def list_tables(self, app_token: str, page_token: str | None = None, page_size: int | None = None) -> dict:
        """
        列出 Base 下所有数据表

        接口：GET `/bitable/v1/apps/:app_token/tables`
        入参（路径）：
        - `app_token`
        入参（查询）：
        - `page_token`: 分页标记，可选
        - `page_size`: 分页大小，默认 20，最大 100，可选

        出参（data）：
        - 数据表列表，包含 `table_id`、`revision`（版本号）、`name`（名称）
        - 可能包含分页信息，如 `page_token`、`has_more`
        """
        params: dict = {}
        if page_token is not None:
            params["page_token"] = page_token
        if page_size is not None:
            params["page_size"] = page_size
        return self._request("GET", f"/bitable/v1/apps/{app_token}/tables", params=params)

    @require_token
    def list_fields(
        self,
        app_token: str,
        table_id: str,
        view_id: str | None = None,
        text_field_as_array: bool | None = None,
        page_token: str | None = None,
        page_size: int | None = None,
    ) -> dict:
        """
        列出数据表字段

        接口：GET `/bitable/v1/apps/:app_token/tables/:table_id/fields`
        入参（路径）：
        - `app_token`、`table_id`
        入参（查询）：
        - `view_id`: 视图 ID（当使用 filter/sort 时忽略），可选
        - `text_field_as_array`: `description` 是否以数组返回，默认 false，可选
        - `page_token`: 分页标记，可选
        - `page_size`: 分页大小，默认 20，最大 100，可选

        出参（data）：
        - 字段列表 `items[]`，每项包含：
          - `field_id`、`field_name`、`type`、`property`、`description`、`ui_type`
        """
        params: dict = {}
        if view_id is not None:
            params["view_id"] = view_id
        if text_field_as_array is not None:
            params["text_field_as_array"] = text_field_as_array
        if page_token is not None:
            params["page_token"] = page_token
        if page_size is not None:
            params["page_size"] = page_size
        return self._request("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", params=params)

    @require_token
    def create_table(self, app_token: str, table_name: str, fields: list | None = None) -> dict:
        """
        创建数据表

        接口：POST `/bitable/v1/apps/:app_token/tables`
        入参（路径）：
        - `app_token`
        入参（JSON）：
        - table 对象，包含：
          - `name`: 表名称（必填）
          - `fields[]`: 初始字段定义列表（可选），每项包含：
            - `field_name`（名称）、`type`（类型：1 文本、2 数字、3 单选、4 多选、5 日期 等）
            - 可选 `property`、`description`、`ui_type`

        出参（data）：
        - 新建表对象，包含 `table_id`、`name`、初始字段列表
        """
        # 构建符合飞书 API 规范的请求体
        table_obj: dict = {"name": table_name}
        if fields is not None:
            table_obj["fields"] = fields
        body: dict = {"table": table_obj}
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables", json_data=body)

    @require_token
    def add_records(
        self,
        app_token: str,
        table_id: str,
        records: list,
        user_id_type: str | None = None,
        client_token: str | None = None,
        ignore_consistency_check: bool | None = None,
    ) -> dict:
        """
        批量新增记录

        接口：POST `/bitable/v1/apps/:app_token/tables/:table_id/records/batch_create`
        入参（路径）：
        - `app_token`、`table_id`
        入参（查询）：
        - `user_id_type`: 用户 ID 类型（`open_id`/`union_id`/`user_id`），默认 `open_id`
        - `client_token`: 幂等操作标识（UUIDv4），可选
        - `ignore_consistency_check`: 是否忽略读写一致性检查，默认 false，可选
        入参（JSON）：
        - `records[]`: 记录列表，单次最多 1,000 条；每条形如：
          - `{ "fields": { "字段名": 值, ... } }`

        出参（data）：
        - 创建成功的记录集合，包含 `record_id`、`fields` 等
        限制：来自外部数据源同步的表不支持增删改；同表写接口不支持并发。
        """
        params: dict = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        if client_token is not None:
            params["client_token"] = client_token
        if ignore_consistency_check is not None:
            params["ignore_consistency_check"] = ignore_consistency_check
        body = {"records": records}
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create", params=params, json_data=body)

    @require_token
    def search_record(
        self,
        app_token: str,
        table_id: str,
        view_id: str | None = None,
        field_names: list[str] | None = None,
        sort: list | None = None,
        filter: dict | str | None = None,
        page_token: str | None = None,
        page_size: int | None = None,
        user_id_type: str | None = None,
    ) -> dict:
        """
        条件查询记录

        HTTP 方法：POST
        路径：`/bitable/v1/apps/:app_token/tables/:table_id/records/search`
        频率限制：20 次/秒

        入参（路径参数）：
        - `app_token`: 多维表格 App 的唯一标识
        - `table_id`: 数据表的唯一标识

        入参（查询参数）：
        - `user_id_type`: 用户 ID 类型，默认 `open_id`；取值 `open_id` / `union_id` / `user_id`
        - `page_token`: 分页标记；首次不填表示从头开始遍历
        - `page_size`: 分页大小，默认 20，最大 500

        入参（请求体 JSON）：
        - `view_id`: 视图 ID，可选；当 `filter` 或 `sort` 不为空时忽略
        - `field_names`: `string[]`，指定返回记录中包含的字段，可选
        - `sort`: `sort[]` 排序条件，可选
            - field_name: 字段名称
            - desc: 是否降序排序，默认 false
        - `filter`: 条件筛选, 可选
             - `conditions`: 条件数组（最多 20 条），每项包含：
               - `field_name`: 字段名称或 `field_id`
               - `operator`: 运算符，随字段类型而异
               - `value`: 值，随字段类型取值
             - `conjunction`: 逻辑连接符，`and` 或 `or`
        出参（data）：
        - `items[]`: 记录列表（含 `record_id`、`fields`、`last_modified_time` 等）
        - `page_token`: 下一页标记
        - `has_more`: 是否还有更多记录
        """
        params: dict = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        if page_token is not None:
            params["page_token"] = page_token
        if page_size is not None:
            params["page_size"] = page_size
        body: dict = {}
        if view_id is not None:
            body["view_id"] = view_id
        if field_names is not None:
            body["field_names"] = field_names
        if sort is not None:
            body["sort"] = sort
        if filter is not None:
            body["filter"] = filter
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/search", params=params, json_data=body)
