import os
import time
import requests
from functools import wraps
from cozeloop.decorator import observe
_cached_token: str = ""
_token_expires_at: float = 0.0


def get_access_token() -> str:
    """获取飞书多维表格（Bitable）的租户访问令牌。"""
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
    所有方法返回值均为 Feishu OpenAPI 标准响应。
    """

    def __init__(self, base_url: str = "https://open.larkoffice.com/open-apis", timeout: int = 30):
        self.base_url: str = base_url.rstrip("/")
        self.timeout: int = timeout
        self.access_token: str = get_access_token()

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}" if self.access_token else "",
            "Content-Type": "application/json; charset=utf-8",
        }

    @observe
    def _request(self, method: str, path: str, params: dict | None = None, json: dict | None = None) -> dict:
        try:
            url: str = f"{self.base_url}{path}"
            resp: requests.Response = requests.request(
                method, url, headers=self._headers(), params=params, json=json, timeout=self.timeout
            )
            resp_data: dict = resp.json()
        except requests.exceptions.RequestException as e:
            raise Exception(f"FeishuBitable API request error: {e}")
        if resp_data.get("code") != 0:
            raise Exception(f"FeishuBitable API error: {resp_data}")
        return resp_data

    @require_token
    def list_tables(self, app_token: str, page_token: str | None = None, page_size: int | None = None) -> dict:
        """列出 Base 下所有数据表"""
        params: dict = {}
        if page_token is not None:
            params["page_token"] = page_token
        if page_size is not None:
            params["page_size"] = page_size
        return self._request("GET", f"/bitable/v1/apps/{app_token}/tables", params=params)

    @require_token
    def create_table(self, app_token: str, table_name: str, fields: list | None = None, default_view_name: str | None = None) -> dict:
        """创建数据表

        飞书 API 请求体格式: {"table": {"name": "xxx", "fields": [...], "default_view_name": "xxx"}}
        """
        table_body: dict = {"name": table_name}
        if default_view_name is not None:
            table_body["default_view_name"] = default_view_name
        if fields is not None:
            table_body["fields"] = fields
        body: dict = {"table": table_body}
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables", json=body)

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
        """列出数据表字段"""
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
    def add_field(self, app_token: str, table_id: str, field: dict, client_token: str | None = None) -> dict:
        """新增字段"""
        params: dict = {}
        if client_token is not None:
            params["client_token"] = client_token
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", params=params, json=field)

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
        """批量新增记录"""
        params: dict = {}
        if user_id_type is not None:
            params["user_id_type"] = user_id_type
        if client_token is not None:
            params["client_token"] = client_token
        if ignore_consistency_check is not None:
            params["ignore_consistency_check"] = ignore_consistency_check
        body: dict = {"records": records}
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create", params=params, json=body)

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
        """条件查询记录"""
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
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/search", params=params, json=body)

    @require_token
    def list_records(
        self,
        app_token: str,
        table_id: str,
        record_ids: list[str] | str,
        user_id_type: str | None = None,
        with_shared_url: bool | None = None,
        automatic_fields: bool | None = None,
    ) -> dict:
        """批量获取记录"""
        ids: list[str] = record_ids if isinstance(record_ids, list) else [record_ids]
        body: dict = {"record_ids": ids}
        if user_id_type is not None:
            body["user_id_type"] = user_id_type
        if with_shared_url is not None:
            body["with_shared_url"] = with_shared_url
        if automatic_fields is not None:
            body["automatic_fields"] = automatic_fields
        return self._request("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_get", json=body)
