from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from andun_search.config import Settings


METHOD_TASK_ADD = "monitor.keywords.task.add"
METHOD_TASK_INFO = "monitor.keywords.task.info"
METHOD_GOODS_LIST = "monitor.keywords.task.goods.list"


class AndunApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class AndunApiResult:
    method: str
    code: int
    message: str
    data: Any
    elapsed_ms: int
    http_status: int
    raw_response: dict[str, Any]
    attempt_count: int = 1
    retry_errors: tuple[str, ...] = ()


def serialize_body(body: dict[str, Any]) -> str:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_canonical_params(params: dict[str, str]) -> str:
    return "&".join(f"{key}={params[key]}" for key in sorted(params))


def sign_params(params: dict[str, str], app_secret: str) -> str:
    canonical = build_canonical_params(params)
    signed_text = f"{app_secret}{canonical}{app_secret}"
    return hashlib.md5(signed_text.encode("utf-8")).hexdigest().upper()


def build_signed_query(
    *,
    method: str,
    body_text: str,
    app_key: str,
    app_secret: str,
    timestamp: str | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    unsigned = {
        "app_key": app_key,
        "body": sha256_hex(body_text),
        "method": method,
        "nonce": nonce or uuid.uuid4().hex,
        "timestamp": timestamp or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
    }
    return {**unsigned, "sign": sign_params(unsigned, app_secret)}


class AndunClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self._transport = transport
        self._http_client: httpx.AsyncClient | None = None

    async def aclose(self) -> None:
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds,
                transport=self._transport,
            )
        return self._http_client

    async def call(
        self,
        method: str,
        body: dict[str, Any],
        *,
        retryable: bool = False,
    ) -> AndunApiResult:
        if not self.settings.credentials_configured:
            raise AndunApiError("缺少 ANDUN_APP_KEY 或 ANDUN_APP_SECRET")

        body_text = serialize_body(body)
        started = time.perf_counter()
        max_attempts = self.settings.request_retry_attempts if retryable else 1
        retry_errors: list[str] = []
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            # 每次重试都必须生成新的 nonce/timestamp/sign，避免重放校验失败。
            query = build_signed_query(
                method=method,
                body_text=body_text,
                app_key=self.settings.app_key,
                app_secret=self.settings.app_secret,
            )
            try:
                client = await self._client()
                response = await client.post(
                    self.settings.api_base_url,
                    params=query,
                    content=body_text.encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                )
                transient_http = response.status_code in {408, 425, 429} or response.status_code >= 500
                if transient_http:
                    retry_errors.append(f"HTTP {response.status_code}")
                    if attempt >= max_attempts:
                        elapsed_ms = round((time.perf_counter() - started) * 1000)
                        raise AndunApiError(
                            f"安盾接口 HTTP 暂时错误（{elapsed_ms} ms，"
                            f"已尝试 {attempt} 次）: HTTP {response.status_code}"
                        )
                    await self.aclose()
                    await asyncio.sleep(min(2 ** (attempt - 1), 8))
                    continue
                try:
                    payload = response.json()
                except ValueError as error:
                    last_error = error
                    if attempt < max_attempts:
                        retry_errors.append(f"HTTP {response.status_code} non-JSON")
                        await self.aclose()
                        await asyncio.sleep(min(2 ** (attempt - 1), 8))
                        continue
                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                    preview = response.text[:300].replace("\n", " ")
                    raise AndunApiError(
                        f"安盾接口返回非 JSON（尝试 {attempt} 次）: "
                        f"HTTP {response.status_code}, {elapsed_ms} ms, {preview}"
                    ) from error
                if not isinstance(payload, dict):
                    raise AndunApiError(f"安盾接口返回结构错误: {type(payload).__name__}")
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                return AndunApiResult(
                    method=method,
                    code=int(payload.get("code") or 0),
                    message=str(payload.get("message") or ""),
                    data=payload.get("data"),
                    elapsed_ms=elapsed_ms,
                    http_status=response.status_code,
                    raw_response=payload,
                    attempt_count=attempt,
                    retry_errors=tuple(retry_errors),
                )
            except httpx.HTTPError as error:
                last_error = error
                # httpx 异常可能包含带 app_key/sign 的完整 URL，不写入日志或数据库。
                retry_errors.append(type(error).__name__)
                await self.aclose()
                if attempt < max_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 8))
                    continue

        elapsed_ms = round((time.perf_counter() - started) * 1000)
        error_name = type(last_error).__name__ if last_error is not None else "UnknownError"
        raise AndunApiError(
            f"安盾接口网络错误（{elapsed_ms} ms，已尝试 {max_attempts} 次）: {error_name}"
        ) from last_error

    async def create_task(self, *, name: str, keywords: list[str], platforms: list[str]) -> AndunApiResult:
        return await self.call(
            METHOD_TASK_ADD,
            {"name": name, "platforms": platforms, "searchKeywords": keywords},
        )

    async def task_info(self, task_id: str) -> AndunApiResult:
        return await self.call(METHOD_TASK_INFO, {"taskId": task_id}, retryable=True)

    async def goods_list(self, task_id: str, current: int, size: int) -> AndunApiResult:
        return await self.call(
            METHOD_GOODS_LIST,
            {"current": current, "size": size, "taskId": task_id},
            retryable=True,
        )
