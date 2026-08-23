import asyncio
from unittest.mock import patch

import httpx
import pytest

from andun_search.client import (
    AndunApiError,
    AndunClient,
    build_canonical_params,
    build_signed_query,
    serialize_body,
    sha256_hex,
    sign_params,
)
from andun_search.config import Settings
from andun_search.service import _clean_platforms, normalize_product


def make_settings() -> Settings:
    return Settings(
        database_url="postgresql://unused",
        api_env="qa",
        api_base_url="http://andun.test/open/api",
        app_key="app",
        app_secret="secret",
        request_timeout_seconds=5,
        request_retry_attempts=4,
        poll_interval_seconds=10,
        max_wait_seconds=1800,
    )


async def no_sleep(_: float) -> None:
    return None


def test_documented_body_sha256_is_based_on_exact_json() -> None:
    body = (
        '{"searchKeywords":["护身符","保佑"],"name":"护身符监测任务",'
        '"platforms":["TB","TM","XY","1688","JD","PDD","XHS","DY"]}'
    )
    assert sha256_hex(body) == "0ed994e1a5c04811ec261f93a4f0c92f3853a7c807c4aa7f6705465abbfb4261"


def test_documented_md5_signature_example() -> None:
    params = {
        "app_key": "testAppKey",
        "body": "2b7770f4d8325a31ca90180f86a2944a1417138064d5d4ff149d5f672918a05e",
        "method": "monitor.keywords.task.goods.list",
        "nonce": "ce747f3daaeb4ef4a5a7791f7981b819",
        "timestamp": "2025-10-23 14:50:50",
    }
    assert build_canonical_params(params).startswith("app_key=testAppKey&body=")
    assert sign_params(params, "testAppSecret") == "2A65C5C9C91163D8341BBC8214D41C4B"


def test_signed_query_contains_body_hash_and_no_secret() -> None:
    body_text = serialize_body({"taskId": "123"})
    query = build_signed_query(
        method="monitor.keywords.task.info",
        body_text=body_text,
        app_key="app",
        app_secret="secret",
        timestamp="2026-07-16 10:00:00",
        nonce="12345678",
    )
    assert query["body"] == sha256_hex(body_text)
    assert query["sign"]
    assert "secret" not in "".join(query.values())


def test_platforms_are_validated_and_deduplicated() -> None:
    assert _clean_platforms(["tb", "JD", "bad", "TB", "1688"]) == ["TB", "JD", "1688"]


def test_product_normalization_preserves_andun_fields() -> None:
    product = normalize_product(
        {
            "taskId": 1851,
            "tradeNo": 999,
            "platform": "TB",
            "platformName": "淘宝",
            "productFrontPage": "https://img.example/a.jpg",
            "title": "开放式头戴耳机",
            "price": 399,
            "monthlySales": 12,
            "productUrl": "https://item.example/999",
            "storeName": "示例店铺",
        }
    )
    assert product.task_id == "1851"
    assert product.trade_no == "999"
    assert product.price == "399"
    assert product.monthly_sales == 12
    assert product.product_front_page.endswith("a.jpg")


def test_task_info_retries_transient_protocol_error_with_new_request() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.RemoteProtocolError("peer disconnected", request=request)
        return httpx.Response(
            200,
            json={"code": 0, "message": "成功", "data": {"taskId": "123", "status": 0}},
        )

    client = AndunClient(make_settings(), transport=httpx.MockTransport(handler))
    with patch("andun_search.client.asyncio.sleep", new=no_sleep):
        result = asyncio.run(client.task_info("123"))
        asyncio.run(client.aclose())

    assert call_count == 2
    assert result.code == 0
    assert result.attempt_count == 2
    assert result.retry_errors == ("RemoteProtocolError",)


def test_create_task_does_not_retry_ambiguous_transport_failure() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.RemoteProtocolError("response lost", request=request)

    client = AndunClient(make_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(AndunApiError, match="已尝试 1 次"):
        asyncio.run(client.create_task(name="test", keywords=["耳机"], platforms=["TB"]))
    asyncio.run(client.aclose())
    assert call_count == 1
