from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import fitz
import pytest

from invalidity.patsnap import (
    PATSNAP_BASE_URL,
    PatsnapApiError,
    PatsnapProvider,
)
from invalidity.providers import (
    EvidenceStage,
    ProviderContentError,
    ProviderRequestError,
    SearchQuery,
)


FIXTURES = Path(__file__).parent / "fixtures"
API_KEY = "sk-fixture-patsnap-key-000001"
PATENT_ID = "718ead9c-4f3c-4674-8f5a-24e126827269"
IMAGE_PATENT_ID = "6e433449-143b-41d9-9abf-6c6519b07779"
SAMPLE_IMAGE_URL = "https://static-open.zhihuiya.com/sample/common_demo.png"
SAMPLE_IMAGE_URL_2 = (
    "https://static-open.zhihuiya.com/sample/common_demo_2.png"
)
MATCHED_IMAGE_URL = (
    "https://patsnap-imagefulltext240.cdn.zhihuiya.com/"
    "CN/S/00/30/54/98/77/60/0/003054987_0001.png"
    "?X-Amz-Signature=fixture-secret-signature"
)
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlS8AAAAASUVORK5CYII="
)


def _pdf_fixture(text: str = "Patsnap PDF fixture") -> bytes:
    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), text)
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


class FakeResponse:
    def __init__(
        self,
        *,
        content: bytes | str = b"",
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        url: str = "",
    ) -> None:
        self.content = content.encode("utf-8") if isinstance(content, str) else content
        self.text = self.content.decode("utf-8", errors="replace")
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.url = url

    def json(self) -> Any:
        return json.loads(self.text)


class ScriptedTransport:
    def __init__(
        self,
        routes: Mapping[str, Sequence[FakeResponse | BaseException]],
    ) -> None:
        self.routes = {name: list(values) for name, values in routes.items()}
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def resolve(_hostname: str) -> list[str]:
        return ["93.184.216.34"]

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._request("POST", url, **kwargs)

    def _request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        route = self._route(method, url)
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": dict(kwargs.get("params") or {}),
                "json": dict(kwargs.get("json") or {}),
                "headers": dict(kwargs.get("headers") or {}),
            }
        )
        try:
            response = self.routes[route].pop(0)
        except (KeyError, IndexError) as exc:
            raise AssertionError(f"unexpected/exhausted route {method} {url}") from exc
        if isinstance(response, BaseException):
            raise response
        if not response.url:
            response.url = url
        return response

    @staticmethod
    def _route(method: str, url: str) -> str:
        path = url.split("?", 1)[0]
        if method == "POST" and path.endswith("/query-search-count/v2"):
            return "count"
        if method == "POST" and path.endswith("/query-search-patent/v2"):
            return "search"
        if method == "POST" and path.endswith("/image-single/beta"):
            return "image-single"
        if method == "POST" and path.endswith("/image-multiple"):
            return "image-multiple"
        if method == "GET" and path.endswith("/bibliography"):
            return "bibliography"
        if method == "GET" and path.endswith("/claim-data"):
            return "claims"
        if method == "GET" and path.endswith("/description-data"):
            return "description"
        if method == "GET" and path.endswith("/pdf-data"):
            return "pdf-metadata"
        if method == "POST" and path.endswith("/fulltext-image"):
            return "image-metadata"
        if method == "GET" and path.endswith(".png"):
            return "image"
        if method == "GET" and path.endswith(".pdf"):
            return "pdf"
        raise AssertionError(f"unexpected route {method} {url}")


def _json_response(
    fixture: str | None = None,
    *,
    payload: Mapping[str, Any] | None = None,
    status_code: int = 200,
) -> FakeResponse:
    if fixture:
        content = (FIXTURES / fixture).read_bytes()
    else:
        content = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    return FakeResponse(
        content=content,
        status_code=status_code,
        headers={
            "content-type": "application/json",
            "x-correlation-id": "fixture-correlation-1",
            "x-openapi-amount": "1",
        },
    )


def _fixture_payload(fixture: str) -> dict[str, Any]:
    payload = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _provider(transport: ScriptedTransport, **kwargs: Any) -> PatsnapProvider:
    return PatsnapProvider(
        api_key=API_KEY,
        transport=transport,
        retry_base_seconds=0,
        sleep=lambda _seconds: None,
        **kwargs,
    )


def _query() -> SearchQuery:
    return SearchQuery.from_input(
        {
            "text": "optical sensor sealed chamber switch",
            "subject_terms": ["optical sensor"],
            "feature_terms": ["sealed chamber", "switch"],
        }
    )


def _image_message(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "loc": ["14-03", "14-02"],
        "url": MATCHED_IMAGE_URL,
        "apdt": 20190102,
        "apno": "CN201930000001",
        "pbdt": 20191231,
        "score": 0.9897707,
        "title": "Fixture image-similarity patent",
        "img_id": "HDA0001130916580000011",
        "inventor": "Fixture Inventor",
        "authority": "CN",
        "patent_id": IMAGE_PATENT_ID,
        "patent_pn": "CN305498776S",
        "current_assignee": "Fixture Assignee",
        "original_assignee": "Fixture Original Assignee",
    }
    result.update(overrides)
    return result


def _image_search_payload(
    *,
    messages: Sequence[Any] | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    normalized_messages = list(messages if messages is not None else [_image_message()])
    return {
        "data": {
            "patent_messages": normalized_messages,
            "total_search_result_count": (
                len(normalized_messages) if total is None else total
            ),
        },
        "status": True,
        "error_code": 0,
    }


def _assert_complete_single_attempt_log(
    attempt_log: Sequence[Mapping[str, Any]],
    *,
    outcome: str,
    status_code: int = 200,
    error_codes: Sequence[int | None] = (None, 0),
) -> None:
    assert len(attempt_log) == 1
    entry = attempt_log[0]
    assert set(entry) == {
        "attempt",
        "request_id",
        "status_code",
        "error_code",
        "correlation_id",
        "openapi_amount",
        "outcome",
        "retry_scheduled",
    }
    assert entry["attempt"] == 1
    assert isinstance(entry["request_id"], str) and entry["request_id"]
    assert entry["status_code"] == status_code
    assert entry["error_code"] in error_codes
    assert entry["correlation_id"] == "fixture-correlation-1"
    assert entry["openapi_amount"] == "1"
    assert entry["outcome"] == outcome
    assert entry["retry_scheduled"] is False
    assert API_KEY not in json.dumps(entry, ensure_ascii=False)


def _assert_signed_binary_operation(
    entry: Mapping[str, Any],
    *,
    operation: str,
    outcome: str,
    status_code: int | None,
) -> None:
    assert set(entry) == {
        "attempt",
        "request_id",
        "status_code",
        "error_code",
        "correlation_id",
        "openapi_amount",
        "outcome",
        "retry_scheduled",
        "operation",
    }
    assert entry["attempt"] == 1
    assert isinstance(entry["request_id"], str) and entry["request_id"]
    assert entry["status_code"] == status_code
    assert entry["error_code"] is None
    assert entry["outcome"] == outcome
    assert entry["retry_scheduled"] is False
    assert entry["operation"] == operation
    assert API_KEY not in json.dumps(entry, ensure_ascii=False)


def test_count_posts_bearer_json_without_leaking_key_to_artifact() -> None:
    transport = ScriptedTransport(
        {"count": [_json_response("patsnap_count.json")]}
    )
    result = _provider(transport).count(
        _query(),
        server_before="2020-01-01",
    )

    assert result.total_search_result_count == 42
    assert result.correlation_id == "fixture-correlation-1"
    assert result.openapi_amount == "1"
    assert result.artifact.kind == "provider_count_response"
    assert API_KEY.encode() not in result.artifact.content
    call = transport.calls[0]
    assert call["headers"]["Authorization"] == f"Bearer {API_KEY}"
    assert call["json"]["query_text"].startswith("(TACD:")
    assert "optical AND sensor AND sealed AND chamber AND switch" in call["json"][
        "query_text"
    ]
    assert "PBD:[* TO 20191231]" in call["json"]["query_text"]


def test_search_returns_leads_and_freezes_json_with_strict_date_query() -> None:
    transport = ScriptedTransport(
        {"search": [_json_response("patsnap_search.json")]}
    )
    provider = _provider(transport)
    records = provider.search(
        _query(),
        max_results=5,
        country="US",
        server_before="2020-01-01",
        filing_before="2019-01-01",
    )

    assert len(records) == 1
    record = records[0]
    assert record.stage is EvidenceStage.LEAD
    assert record.external_id == PATENT_ID
    assert record.publication_number == "US20190300001A1"
    assert record.publication_date == "2019-12-31"
    assert record.filing_date == "2018-01-02"
    assert provider.last_search_artifact is not None
    assert provider.last_search_artifact.media_type == "application/json"
    expression = transport.calls[0]["json"]["query_text"]
    assert "AUTHORITY:(US)" in expression
    assert "PBD:[* TO 20191231]" in expression
    assert "APD:[* TO 20181231]" in expression
    assert "PRIORITY_DATE:[* TO 20181231]" in expression
    assert API_KEY not in json.dumps(record.model_dump(), ensure_ascii=False)


@pytest.mark.parametrize(
    ("raw", "media_type", "expected_outcome"),
    (
        (b"<html>provider error page</html>", "text/html", "invalid_mime"),
        (b'{"status": true, broken', "application/json", "invalid_json"),
    ),
    ids=("non-json-mime", "invalid-json"),
)
def test_search_preserves_raw_error_response_and_attempt_audit(
    raw: bytes,
    media_type: str,
    expected_outcome: str,
) -> None:
    response = FakeResponse(
        content=raw,
        status_code=200,
        headers={
            "content-type": media_type,
            "x-correlation-id": "fixture-correlation-1",
            "x-openapi-amount": "1",
        },
    )
    transport = ScriptedTransport({"search": [response]})
    provider = _provider(transport)

    with pytest.raises(PatsnapApiError) as captured:
        provider.search(_query(), max_results=1)

    error = captured.value
    artifact = error.raw_response_artifact
    assert artifact is not None
    assert artifact.content == raw
    assert artifact.media_type == media_type
    assert artifact.kind == "provider_error_response"
    assert provider.last_search_artifact is artifact
    assert artifact.request_attempt_log == error.request_attempt_log
    _assert_complete_single_attempt_log(
        error.request_attempt_log,
        outcome=expected_outcome,
    )
    assert API_KEY.encode() not in artifact.content
    assert API_KEY not in str(error)
    assert API_KEY not in json.dumps(artifact.model_dump(), ensure_ascii=False)


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    (
        (
            {"status": True, "error_code": 0},
            "缺少 data",
        ),
        (
            {
                "data": {"unexpected_field": 0},
                "status": True,
                "error_code": 0,
            },
            "缺少 results",
        ),
        (
            {
                "data": {
                    "results": [],
                    "result_count": 0,
                    "total_search_result_count": 1,
                },
                "status": True,
                "error_code": 0,
            },
            "数量与 results 不一致",
        ),
    ),
    ids=("missing-data", "missing-results", "positive-total-empty-results"),
)
def test_search_keeps_success_response_artifact_when_p002_shape_is_invalid(
    payload: Mapping[str, Any],
    expected_error: str,
) -> None:
    response = _json_response(payload=payload)
    raw = response.content
    transport = ScriptedTransport({"search": [response]})
    provider = _provider(transport)

    with pytest.raises(ProviderContentError, match=expected_error):
        provider.search(_query(), max_results=1)

    artifact = provider.last_search_artifact
    assert artifact is not None
    assert artifact.content == raw
    assert artifact.kind == "provider_search_response"
    assert artifact.media_type == "application/json"
    _assert_complete_single_attempt_log(
        artifact.request_attempt_log,
        outcome="success",
    )
    assert API_KEY.encode() not in artifact.content
    assert API_KEY not in json.dumps(artifact.model_dump(), ensure_ascii=False)


def test_search_treats_exact_empty_success_data_as_zero_hits() -> None:
    response = _json_response(
        payload={"status": True, "error_code": 0, "data": {}}
    )
    raw = response.content
    transport = ScriptedTransport({"search": [response]})
    provider = _provider(transport)

    records = provider.search(_query(), max_results=10)

    assert records == []
    assert len(transport.calls) == 1
    artifact = provider.last_search_artifact
    assert artifact is not None
    assert artifact.content == raw
    assert artifact.kind == "provider_search_response"
    assert artifact.media_type == "application/json"
    _assert_complete_single_attempt_log(
        artifact.request_attempt_log,
        outcome="success",
    )


def test_single_image_search_uses_p060_beta_and_returns_normalized_leads() -> None:
    response = _json_response(payload=_image_search_payload(total=20783))
    transport = ScriptedTransport({"image-single": [response]})
    provider = _provider(transport)

    records = provider.search_single_image(
        SAMPLE_IMAGE_URL,
        patent_type="D",
        model=1,
        max_results=10,
    )

    assert len(records) == 1
    record = records[0]
    assert record.stage is EvidenceStage.LEAD
    assert record.external_id == IMAGE_PATENT_ID
    assert record.publication_number == "CN305498776S"
    assert record.authority == "CN"
    assert record.publication_date == "2019-12-31"
    assert record.filing_date == "2019-01-02"
    assert record.image_urls == ()
    assert record.raw_metadata["image_similarity_score"] == pytest.approx(
        0.9897707
    )
    assert record.raw_metadata["matched_image_id"] == "HDA0001130916580000011"
    assert "X-Amz-Signature" not in record.raw_metadata[
        "matched_image_url_redacted"
    ]
    assert record.provenance["image_search_operation"] == "P060_beta"
    assert record.provenance["total_result_count"] == 20783
    assert record.provenance["input_image_count"] == 1
    assert SAMPLE_IMAGE_URL not in json.dumps(record.model_dump())
    assert MATCHED_IMAGE_URL not in json.dumps(record.model_dump())

    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == (
        f"{PATSNAP_BASE_URL}/search/patent/image-single/beta"
    )
    assert not call["url"].endswith("/image-single")
    assert call["json"] == {
        "url": SAMPLE_IMAGE_URL,
        "model": 1,
        "patent_type": "D",
        "field": "SCORE",
        "offset": 0,
        "limit": 10,
        "is_https": 1,
        "return_img_id": True,
    }
    assert call["headers"]["Authorization"] == f"Bearer {API_KEY}"
    artifact = provider.last_search_artifact
    assert artifact is not None
    assert artifact.kind == "provider_image_search_response"
    assert artifact.content == response.content
    _assert_complete_single_attempt_log(
        artifact.request_attempt_log,
        outcome="success",
    )
    assert API_KEY.encode() not in artifact.content
    assert API_KEY not in json.dumps(record.model_dump(), ensure_ascii=False)


def test_multiple_image_search_uses_p061_urls_and_returns_normalized_leads() -> None:
    response = _json_response(payload=_image_search_payload(total=19615))
    transport = ScriptedTransport({"image-multiple": [response]})
    provider = _provider(transport)

    records = provider.search_multiple_images(
        (SAMPLE_IMAGE_URL, SAMPLE_IMAGE_URL_2),
        patent_type="D",
        model=2,
        max_results=5,
        offset=100,
    )

    assert len(records) == 1
    record = records[0]
    assert record.stage is EvidenceStage.LEAD
    assert record.raw_metadata["loc_classifications"] == ["14-03", "14-02"]
    assert record.provenance["image_search_operation"] == "P061"
    assert record.provenance["image_model"] == 2
    assert record.provenance["input_image_count"] == 2
    assert record.provenance["total_result_count"] == 19615
    call = transport.calls[0]
    assert call["url"] == f"{PATSNAP_BASE_URL}/search/patent/image-multiple"
    assert call["json"]["urls"] == [SAMPLE_IMAGE_URL, SAMPLE_IMAGE_URL_2]
    assert "url" not in call["json"]
    assert call["json"]["offset"] == 100
    assert call["json"]["limit"] == 5


@pytest.mark.parametrize(
    "method_name",
    ("search_single_image", "search_multiple_images"),
)
def test_image_search_permission_failure_is_audited_without_retry(
    method_name: str,
) -> None:
    route = "image-single" if method_name == "search_single_image" else "image-multiple"
    response = _json_response(
        payload={
            "status": False,
            "error_code": 67200004,
            "error_msg": "permission denied fixture",
        }
    )
    transport = ScriptedTransport({route: [response]})
    provider = _provider(transport, retry_attempts=3)

    with pytest.raises(PatsnapApiError) as captured:
        if method_name == "search_single_image":
            provider.search_single_image(
                SAMPLE_IMAGE_URL,
                patent_type="D",
                model=1,
            )
        else:
            provider.search_multiple_images(
                (SAMPLE_IMAGE_URL, SAMPLE_IMAGE_URL_2),
                patent_type="D",
                model=1,
            )

    error = captured.value
    assert error.error_code == 67200004
    assert error.retryable is False
    assert len(transport.calls) == 1
    assert error.raw_response_artifact is provider.last_search_artifact
    assert error.raw_response_artifact is not None
    assert error.raw_response_artifact.content == response.content
    _assert_complete_single_attempt_log(
        error.request_attempt_log,
        outcome="provider_error",
        error_codes=(67200004,),
    )
    assert "permission denied fixture" not in str(error)
    assert API_KEY not in str(error)


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    (
        (
            {"data": [], "status": True, "error_code": 0},
            "缺少 data 对象",
        ),
        (
            {
                "data": {
                    "patent_messages": {},
                    "total_search_result_count": 0,
                },
                "status": True,
                "error_code": 0,
            },
            "缺少 patent_messages 数组",
        ),
        (
            _image_search_payload(messages=["not-an-object"]),
            "含非对象元素",
        ),
        (
            _image_search_payload(
                messages=[_image_message(patent_id="not-a-uuid")]
            ),
            "缺少 patent_id 或 patent_pn",
        ),
        (
            _image_search_payload(
                messages=[_image_message(patent_pn="")]
            ),
            "缺少 patent_id 或 patent_pn",
        ),
        (
            _image_search_payload(
                messages=[_image_message(pbdt=20190231)]
            ),
            "日期字段无效",
        ),
        (
            _image_search_payload(
                messages=[_image_message(score="0.98")]
            ),
            "不是有效相似度",
        ),
        (
            _image_search_payload(
                messages=[_image_message(score=1.01)]
            ),
            "必须在 0..1",
        ),
        (
            _image_search_payload(
                messages=[
                    _image_message(
                        url="http://patsnap-imagefulltext240.cdn.zhihuiya.com/a.png"
                    )
                ]
            ),
            "HTTPS 图片 URL",
        ),
        (
            _image_search_payload(
                messages=[_image_message(authority="US")]
            ),
            "authority 与 patent_pn 冲突",
        ),
        (
            _image_search_payload(total=0),
            "返回数量与 patent_messages 不一致",
        ),
    ),
    ids=(
        "data-not-object",
        "messages-not-list",
        "message-not-object",
        "bad-patent-id",
        "missing-patent-pn",
        "invalid-publication-date",
        "score-string",
        "score-out-of-range",
        "non-https-result-image",
        "authority-conflicts-with-pn",
        "total-smaller-than-messages",
    ),
)
def test_image_search_schema_failures_keep_raw_success_artifact(
    payload: Mapping[str, Any],
    expected_error: str,
) -> None:
    response = _json_response(payload=payload)
    transport = ScriptedTransport({"image-single": [response]})
    provider = _provider(transport)

    with pytest.raises(ProviderContentError, match=expected_error):
        provider.search_single_image(
            SAMPLE_IMAGE_URL,
            patent_type="D",
            model=1,
        )

    artifact = provider.last_search_artifact
    assert artifact is not None
    assert artifact.kind == "provider_image_search_response"
    assert artifact.content == response.content
    _assert_complete_single_attempt_log(
        artifact.request_attempt_log,
        outcome="success",
    )
    assert API_KEY.encode() not in artifact.content


def test_image_search_preserves_missing_optional_dates_as_none_lead() -> None:
    response = _json_response(
        payload=_image_search_payload(
            messages=[_image_message(apdt=None, pbdt=None)]
        )
    )
    transport = ScriptedTransport({"image-single": [response]})

    records = _provider(transport).search_single_image(
        SAMPLE_IMAGE_URL,
        patent_type="D",
        model=1,
    )

    assert len(records) == 1
    assert records[0].stage is EvidenceStage.LEAD
    assert records[0].publication_date is None
    assert records[0].filing_date is None


@pytest.mark.parametrize(
    "failure",
    (
        ProviderRequestError("fixture transport failed"),
        _json_response(
            payload={"status": False, "error_code": 67200002},
            status_code=429,
        ),
        _json_response(
            payload={"status": False, "error_code": 68300005},
            status_code=500,
        ),
    ),
    ids=("transport", "http-429", "http-500"),
)
def test_image_search_post_transport_rate_limit_and_server_error_do_not_retry(
    failure: FakeResponse | BaseException,
) -> None:
    transport = ScriptedTransport({"image-single": [failure]})
    provider = _provider(transport, retry_attempts=3)

    with pytest.raises(PatsnapApiError) as captured:
        provider.search_single_image(
            SAMPLE_IMAGE_URL,
            patent_type="D",
            model=1,
        )

    assert len(transport.calls) == 1
    assert len(captured.value.request_attempt_log) == 1
    assert captured.value.request_attempt_log[0]["retry_scheduled"] is False


def test_image_search_only_retries_documented_provider_busy_code() -> None:
    busy = _json_response(
        payload={"status": False, "error_code": 68300008}
    )
    success = _json_response(payload=_image_search_payload())
    transport = ScriptedTransport({"image-single": [busy, success]})
    provider = _provider(transport, retry_attempts=3)

    records = provider.search_single_image(
        SAMPLE_IMAGE_URL,
        patent_type="D",
        model=1,
    )

    assert len(records) == 1
    assert len(transport.calls) == 2
    artifact = provider.last_search_artifact
    assert artifact is not None
    assert len(artifact.request_attempt_log) == 2
    assert artifact.request_attempt_log[0]["error_code"] == 68300008
    assert artifact.request_attempt_log[0]["retry_scheduled"] is True
    assert artifact.request_attempt_log[1]["outcome"] == "success"


@pytest.mark.parametrize(
    "url",
    (
        "http://example.com/image.png",
        "https://localhost/image.png",
        "https://127.0.0.1/image.png",
        "https://user:password@example.com/image.png",
        "https://example.com/image.png#fragment",
        "https://example.com",
    ),
)
def test_single_image_search_rejects_unsafe_or_non_image_urls(url: str) -> None:
    transport = ScriptedTransport({})
    provider = _provider(transport)

    with pytest.raises(ValueError, match="HTTPS 图片 URL|公开访问|私网"):
        provider.search_single_image(
            url,
            patent_type="D",
            model=1,
        )

    assert transport.calls == []


@pytest.mark.parametrize(
    "resolved_addresses",
    (
        ["10.0.0.1"],
        ["127.0.0.1"],
        ["169.254.1.1"],
        ["93.184.216.34", "192.168.1.8"],
    ),
)
def test_image_search_rejects_hostname_resolving_to_non_public_address(
    resolved_addresses: Sequence[str],
) -> None:
    transport = ScriptedTransport({})
    provider = _provider(
        transport,
        resolver=lambda _hostname: resolved_addresses,
    )

    with pytest.raises(ValueError, match="公网 DNS 校验"):
        provider.search_single_image(
            "https://images.example.com/input.png",
            patent_type="D",
            model=1,
        )

    assert transport.calls == []


def test_image_search_fails_closed_when_dns_resolution_fails() -> None:
    def failing_resolver(_hostname: str) -> Sequence[str]:
        raise OSError("fixture DNS failure")

    transport = ScriptedTransport({})
    provider = _provider(transport, resolver=failing_resolver)

    with pytest.raises(ValueError, match="公网 DNS 校验"):
        provider.search_multiple_images(
            (
                "https://images.example.com/input-1.png",
                "https://images.example.com/input-2.png",
            ),
            patent_type="D",
            model=1,
        )

    assert transport.calls == []


@pytest.mark.parametrize(
    "image_urls",
    (
        (),
        (SAMPLE_IMAGE_URL,),
        (
            SAMPLE_IMAGE_URL,
            SAMPLE_IMAGE_URL_2,
            "https://example.com/three.png",
            "https://example.com/four.png",
            "https://example.com/five.png",
        ),
        (SAMPLE_IMAGE_URL, SAMPLE_IMAGE_URL),
        SAMPLE_IMAGE_URL,
    ),
)
def test_multiple_image_search_requires_two_to_four_distinct_urls(
    image_urls: Any,
) -> None:
    transport = ScriptedTransport({})
    provider = _provider(transport)

    with pytest.raises(ValueError, match="2..4|互不相同"):
        provider.search_multiple_images(
            image_urls,
            patent_type="D",
            model=1,
        )

    assert transport.calls == []


@pytest.mark.parametrize(
    ("patent_type", "model"),
    (
        ("D", 3),
        ("U", 1),
        ("U", 3),
        ("X", 1),
        (1, 1),
        ("D", True),
        ("D", 0),
        ("U", 5),
    ),
)
def test_image_search_rejects_invalid_patent_type_model_combinations(
    patent_type: Any,
    model: Any,
) -> None:
    transport = ScriptedTransport({})
    provider = _provider(transport)

    with pytest.raises(ValueError):
        provider.search_single_image(
            SAMPLE_IMAGE_URL,
            patent_type=patent_type,
            model=model,
        )

    assert transport.calls == []


@pytest.mark.parametrize("model", (3, 4))
def test_multiple_image_search_accepts_utility_models(model: int) -> None:
    response = _json_response(payload=_image_search_payload())
    transport = ScriptedTransport({"image-multiple": [response]})

    records = _provider(transport).search_multiple_images(
        (SAMPLE_IMAGE_URL, SAMPLE_IMAGE_URL_2),
        patent_type="U",
        model=model,
    )

    assert len(records) == 1
    assert transport.calls[0]["json"]["patent_type"] == "U"
    assert transport.calls[0]["json"]["model"] == model


@pytest.mark.parametrize(
    ("max_results", "offset"),
    (
        (0, 0),
        (101, 0),
        (True, 0),
        (10, -1),
        (10, 1001),
        (10, False),
    ),
)
def test_image_search_rejects_invalid_paging_without_network(
    max_results: Any,
    offset: Any,
) -> None:
    transport = ScriptedTransport({})
    provider = _provider(transport)

    with pytest.raises(ValueError):
        provider.search_single_image(
            SAMPLE_IMAGE_URL,
            patent_type="D",
            model=1,
            max_results=max_results,
            offset=offset,
        )

    assert transport.calls == []


def test_retrieve_maps_verified_dates_text_and_downloads_media_without_signed_url_leak() -> None:
    transport = ScriptedTransport(
        {
            "bibliography": [_json_response("patsnap_bibliography.json")],
            "claims": [_json_response("patsnap_claims.json")],
            "description": [_json_response("patsnap_description.json")],
            "image-metadata": [_json_response("patsnap_images.json")],
            "image": [
                FakeResponse(content=PNG_1X1, headers={"content-type": "image/png"})
            ],
            "pdf-metadata": [_json_response("patsnap_pdf.json")],
                "pdf": [
                    FakeResponse(
                        content=_pdf_fixture(),
                        headers={"content-type": "application/pdf"},
                    )
                ],
        }
    )
    provider = _provider(transport)
    lead = provider._coerce_lead(
        {
            "external_id": PATENT_ID,
            "publication_number": "US20190300001A1",
            "title": "Search title",
        }
    )
    retrieved = provider.retrieve(lead)

    assert retrieved.stage is EvidenceStage.RETRIEVED
    assert retrieved.publication_date == "2019-12-31"
    assert retrieved.filing_date == "2018-01-02"
    assert retrieved.priority_date == "2017-12-30"
    assert "sealed chamber" in str(retrieved.claims)
    assert "protects the optical sensor" in str(retrieved.description)
    assert retrieved.primary_artifact is not None
    assert retrieved.provenance["publication_date_verified"] is True
    assert retrieved.pdf_url is None
    assert retrieved.image_urls == ()
    assert "sign=" not in json.dumps(retrieved.model_dump(), ensure_ascii=False)

    image = provider.download_image(retrieved, index=0)
    assert image.media_type == "image/png"
    assert "?" not in image.source_url
    assert image.source_metadata_artifact is not None
    assert image.source_metadata_artifact.kind == "provider_image_metadata_response"
    assert b"fulltext_image" in image.source_metadata_artifact.content
    assert len(image.request_attempt_log) == 2
    _assert_complete_single_attempt_log(
        image.request_attempt_log[:1],
        outcome="success",
    )
    _assert_signed_binary_operation(
        image.request_attempt_log[1],
        operation="P042_signed_image_download",
        outcome="success",
        status_code=200,
    )
    assert image.request_attempt_log[0]["request_id"] != image.request_attempt_log[1][
        "request_id"
    ]
    pdf = provider.download_pdf(retrieved)
    assert pdf.content.startswith(b"%PDF")
    assert "?" not in pdf.source_url
    assert pdf.source_metadata_artifact is not None
    assert pdf.source_metadata_artifact.kind == "provider_pdf_metadata_response"
    assert b'"pdf"' in pdf.source_metadata_artifact.content
    assert len(pdf.request_attempt_log) == 2
    _assert_complete_single_attempt_log(
        pdf.request_attempt_log[:1],
        outcome="success",
    )
    _assert_signed_binary_operation(
        pdf.request_attempt_log[1],
        operation="P020_signed_pdf_download",
        outcome="success",
        status_code=200,
    )
    assert pdf.request_attempt_log[0]["request_id"] != pdf.request_attempt_log[1][
        "request_id"
    ]

    detail_calls = {
        ScriptedTransport._route(call["method"], call["url"]): call
        for call in transport.calls
        if call["method"] == "GET"
        and "connect.zhihuiya.com/basic-patent-data" in call["url"]
    }
    assert detail_calls["claims"]["params"]["replace_by_related"] == 0
    assert detail_calls["description"]["params"]["replace_by_related"] == 0


def test_retrieve_keeps_p012_http_error_response_and_complete_attempt_audit() -> None:
    error_response = _json_response(
        payload={
            "data": None,
            "status": False,
            "error_code": 67200101,
            "error_msg": "fixture endpoint error",
        },
        status_code=404,
    )
    raw = error_response.content
    transport = ScriptedTransport({"bibliography": [error_response]})
    provider = _provider(transport)
    lead = provider._coerce_lead(
        {
            "external_id": PATENT_ID,
            "publication_number": "US20190300001A1",
        }
    )

    with pytest.raises(PatsnapApiError) as captured:
        provider.retrieve(lead)

    error = captured.value
    artifact = error.raw_response_artifact
    assert artifact is not None
    assert artifact.content == raw
    assert artifact.kind == "provider_error_response"
    assert artifact.request_attempt_log == error.request_attempt_log
    _assert_complete_single_attempt_log(
        error.request_attempt_log,
        outcome="http_error",
        status_code=404,
        error_codes=(67200101,),
    )
    assert provider.last_retrieval_artifacts == (artifact,)
    assert API_KEY.encode() not in artifact.content
    assert API_KEY not in str(error)
    assert API_KEY not in json.dumps(artifact.model_dump(), ensure_ascii=False)


@pytest.mark.parametrize(
    "failing_endpoint",
    ("P012", "P018", "P019"),
)
def test_retrieve_keeps_every_received_response_when_detail_validation_fails(
    failing_endpoint: str,
) -> None:
    bibliography = _fixture_payload("patsnap_bibliography.json")
    claims = _fixture_payload("patsnap_claims.json")
    description = _fixture_payload("patsnap_description.json")
    if failing_endpoint == "P012":
        del bibliography["data"][0]["bibliographic_data"]
    elif failing_endpoint == "P018":
        claims["data"][0]["patent_id"] = (
            "ffffffff-ffff-4fff-8fff-ffffffffffff"
        )
    else:
        description["data"] = {"unexpected": "not-an-array"}

    bibliography_response = _json_response(payload=bibliography)
    claims_response = _json_response(payload=claims)
    description_response = _json_response(payload=description)
    transport = ScriptedTransport(
        {
            "bibliography": [bibliography_response],
            "claims": [claims_response],
            "description": [description_response],
        }
    )
    provider = _provider(transport)
    lead = provider._coerce_lead(
        {
            "external_id": PATENT_ID,
            "publication_number": "US20190300001A1",
        }
    )

    with pytest.raises(ProviderContentError, match=failing_endpoint):
        provider.retrieve(lead)

    artifacts = provider.last_retrieval_artifacts
    assert [item.kind for item in artifacts] == [
        "provider_bibliography_response",
        "provider_claims_response",
        "provider_description_response",
    ]
    assert [item.content for item in artifacts] == [
        bibliography_response.content,
        claims_response.content,
        description_response.content,
    ]
    for artifact in artifacts:
        _assert_complete_single_attempt_log(
            artifact.request_attempt_log,
            outcome="success",
        )
        assert API_KEY.encode() not in artifact.content
        assert API_KEY not in json.dumps(artifact.model_dump(), ensure_ascii=False)


@pytest.mark.parametrize(
    ("returned_patent_id", "returned_pn"),
    (
        (PATENT_ID, "US20999999999A1"),
        ("ffffffff-ffff-4fff-8fff-ffffffffffff", "US20190300001A1"),
    ),
    ids=("publication-number-conflict", "patent-id-conflict"),
)
def test_retrieve_rejects_single_detail_row_when_either_identity_field_conflicts(
    returned_patent_id: str,
    returned_pn: str,
) -> None:
    bibliography = _fixture_payload("patsnap_bibliography.json")
    bibliography["data"][0]["patent_id"] = returned_patent_id
    bibliography["data"][0]["pn"] = returned_pn
    transport = ScriptedTransport(
        {
            "bibliography": [_json_response(payload=bibliography)],
            "claims": [_json_response("patsnap_claims.json")],
            "description": [_json_response("patsnap_description.json")],
        }
    )
    provider = _provider(transport)
    lead = provider._coerce_lead(
        {
            "external_id": PATENT_ID,
            "publication_number": "US20190300001A1",
        }
    )

    with pytest.raises(ProviderContentError, match="P012"):
        provider.retrieve(lead)


def test_retrieve_rejects_nonempty_invalid_vendor_id_even_when_pn_matches() -> None:
    bibliography = _fixture_payload("patsnap_bibliography.json")
    bibliography["data"][0]["patent_id"] = "not-a-valid-vendor-id"
    transport = ScriptedTransport(
        {
            "bibliography": [_json_response(payload=bibliography)],
            "claims": [_json_response("patsnap_claims.json")],
            "description": [_json_response("patsnap_description.json")],
        }
    )
    lead = _provider(transport)._coerce_lead(
        {
            "external_id": PATENT_ID,
            "publication_number": "US20190300001A1",
        }
    )

    with pytest.raises(ProviderContentError, match="P012"):
        _provider(transport).retrieve(lead)


def test_publication_number_retrieval_cross_checks_fulltext_vendor_id_from_p012() -> None:
    claims = _fixture_payload("patsnap_claims.json")
    claims["data"][0]["patent_id"] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    transport = ScriptedTransport(
        {
            "bibliography": [_json_response("patsnap_bibliography.json")],
            "claims": [_json_response(payload=claims)],
            "description": [_json_response("patsnap_description.json")],
        }
    )
    provider = _provider(transport)
    lead = provider._coerce_lead("US20190300001A1")

    with pytest.raises(ProviderContentError, match="P018"):
        provider.retrieve(lead)


@pytest.mark.parametrize(
    "endpoint",
    ("P018", "P019"),
)
def test_retrieve_rejects_related_replacement_for_fulltext(
    endpoint: str,
) -> None:
    claims = _fixture_payload("patsnap_claims.json")
    description = _fixture_payload("patsnap_description.json")
    related_payload = claims if endpoint == "P018" else description
    related_payload["data"][0]["pn_related"] = "US20190300002A1"
    transport = ScriptedTransport(
        {
            "bibliography": [_json_response("patsnap_bibliography.json")],
            "claims": [_json_response(payload=claims)],
            "description": [_json_response(payload=description)],
        }
    )
    lead = _provider(transport)._coerce_lead(
        {
            "external_id": PATENT_ID,
            "publication_number": "US20190300001A1",
        }
    )

    with pytest.raises(ProviderContentError, match=endpoint):
        _provider(transport).retrieve(lead)


def test_retrieve_does_not_verify_dates_when_p002_and_p012_disagree() -> None:
    bibliography = _fixture_payload("patsnap_bibliography.json")
    bibliography["data"][0]["bibliographic_data"]["publication_reference"][
        "date"
    ] = 20191230
    bibliography["data"][0]["bibliographic_data"]["application_reference"][
        "date"
    ] = 20180101
    transport = ScriptedTransport(
        {
            "search": [_json_response("patsnap_search.json")],
            "bibliography": [_json_response(payload=bibliography)],
            "claims": [_json_response("patsnap_claims.json")],
            "description": [_json_response("patsnap_description.json")],
        }
    )
    provider = _provider(transport)
    lead = provider.search(_query(), max_results=1)[0]

    retrieved = provider.retrieve(lead)

    assert retrieved.provenance["publication_date_verified"] is False
    assert retrieved.provenance["filing_date_verified"] is False


def test_post_transport_without_response_is_not_retried() -> None:
    transport = ScriptedTransport(
        {
            "count": [
                TimeoutError("fixture response was lost"),
                _json_response("patsnap_count.json"),
            ]
        }
    )

    with pytest.raises(PatsnapApiError) as captured:
        _provider(transport).count(_query())

    assert captured.value.retryable is False
    assert len(transport.calls) == 1


def test_transient_business_error_retries_but_auth_failure_does_not() -> None:
    transient = {"data": None, "status": False, "error_code": 68300008}
    transport = ScriptedTransport(
        {
            "count": [
                _json_response(payload=transient),
                _json_response(payload=transient),
                _json_response("patsnap_count.json"),
            ]
        }
    )
    result = _provider(transport).count(_query())
    assert result.request_attempts == 3
    assert len(result.request_attempt_log) == 3
    assert [item["outcome"] for item in result.request_attempt_log] == [
        "provider_error",
        "provider_error",
        "success",
    ]
    assert len(transport.calls) == 3

    unauthorized = ScriptedTransport(
        {
            "count": [
                _json_response(
                    payload={"status": False, "error_code": 67200004},
                    status_code=403,
                )
            ]
        }
    )
    with pytest.raises(PatsnapApiError) as captured:
        _provider(unauthorized).count(_query())
    assert captured.value.retryable is False
    assert len(captured.value.request_attempt_log) == 1
    assert len(unauthorized.calls) == 1
    assert API_KEY not in str(captured.value)


def test_download_image_rejects_p042_response_for_another_patent() -> None:
    image_metadata = _fixture_payload("patsnap_images.json")
    image_metadata["data"]["patent_id"] = (
        "ffffffff-ffff-4fff-8fff-ffffffffffff"
    )
    transport = ScriptedTransport(
        {
            "image-metadata": [_json_response(payload=image_metadata)],
            "image": [
                FakeResponse(content=PNG_1X1, headers={"content-type": "image/png"})
            ],
        }
    )
    provider = _provider(transport)
    lead = provider._coerce_lead(
        {
            "external_id": PATENT_ID,
            "publication_number": "US20190300001A1",
        }
    )

    with pytest.raises(ProviderContentError, match="P042"):
        provider.download_image(lead)

    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("endpoint", "binary_content", "binary_media_type"),
    (
        ("P020", b"<html>not a PDF</html>", "text/html"),
        ("P020", b"not-a-real-pdf", "application/pdf"),
        ("P020", b"%PDF-1.4\nnot-parseable", "application/pdf"),
        ("P042", b"<html>not an image</html>", "text/html"),
        ("P042", b"not-a-real-png", "image/png"),
    ),
    ids=(
        "pdf-wrong-mime",
        "pdf-bad-content",
        "pdf-unparseable",
        "image-wrong-mime",
        "image-bad-content",
    ),
)
def test_signed_binary_failure_keeps_metadata_and_redacted_error_artifact(
    endpoint: str,
    binary_content: bytes,
    binary_media_type: str,
) -> None:
    metadata_fixture = (
        "patsnap_pdf.json" if endpoint == "P020" else "patsnap_images.json"
    )
    metadata_route = "pdf-metadata" if endpoint == "P020" else "image-metadata"
    binary_route = "pdf" if endpoint == "P020" else "image"
    metadata_kind = (
        "provider_pdf_metadata_response"
        if endpoint == "P020"
        else "provider_image_metadata_response"
    )
    metadata_response = _json_response(metadata_fixture)
    binary_response = FakeResponse(
        content=binary_content,
        headers={"content-type": binary_media_type},
    )
    transport = ScriptedTransport(
        {
            metadata_route: [metadata_response],
            binary_route: [binary_response],
        }
    )
    provider = _provider(transport)
    lead = provider._coerce_lead(
        {
            "external_id": PATENT_ID,
            "publication_number": "US20190300001A1",
        }
    )

    with pytest.raises(ProviderContentError):
        if endpoint == "P020":
            provider.download_pdf(lead)
        else:
            provider.download_image(lead)

    artifacts = provider.last_media_artifacts
    assert [item.kind for item in artifacts] == [
        metadata_kind,
        "provider_error_response",
    ]
    metadata_artifact, error_artifact = artifacts
    assert metadata_artifact.content == metadata_response.content
    assert error_artifact.content == binary_content
    assert error_artifact.media_type == binary_media_type
    _assert_complete_single_attempt_log(
        metadata_artifact.request_attempt_log,
        outcome="success",
    )
    for artifact in artifacts:
        assert "?" not in artifact.source_url
        assert "sign=" not in artifact.source_url
        assert API_KEY not in json.dumps(artifact.model_dump(), ensure_ascii=False)


@pytest.mark.parametrize(
    "endpoint",
    ("P020", "P042"),
)
def test_signed_get_transport_failure_keeps_zero_byte_audited_error_artifact(
    endpoint: str,
) -> None:
    metadata_fixture = (
        "patsnap_pdf.json" if endpoint == "P020" else "patsnap_images.json"
    )
    metadata_route = "pdf-metadata" if endpoint == "P020" else "image-metadata"
    binary_route = "pdf" if endpoint == "P020" else "image"
    metadata_kind = (
        "provider_pdf_metadata_response"
        if endpoint == "P020"
        else "provider_image_metadata_response"
    )
    operation = (
        "P020_signed_pdf_download"
        if endpoint == "P020"
        else "P042_signed_image_download"
    )
    transport = ScriptedTransport(
        {
            metadata_route: [_json_response(metadata_fixture)],
            binary_route: [TimeoutError("fixture signed response was lost")],
        }
    )
    provider = _provider(transport)
    lead = provider._coerce_lead(
        {
            "external_id": PATENT_ID,
            "publication_number": "US20190300001A1",
        }
    )

    with pytest.raises(ProviderRequestError):
        if endpoint == "P020":
            provider.download_pdf(lead)
        else:
            provider.download_image(lead)

    artifacts = provider.last_media_artifacts
    assert [item.kind for item in artifacts] == [
        metadata_kind,
        "provider_error_response",
    ]
    metadata_artifact, error_artifact = artifacts
    assert error_artifact.content == b""
    assert error_artifact.media_type == "application/octet-stream"
    assert len(error_artifact.request_attempt_log) == 1
    _assert_complete_single_attempt_log(
        metadata_artifact.request_attempt_log,
        outcome="success",
    )
    _assert_signed_binary_operation(
        error_artifact.request_attempt_log[0],
        operation=operation,
        outcome="transport_or_bounds_error",
        status_code=None,
    )
    assert metadata_artifact.request_attempt_log[0]["request_id"] != (
        error_artifact.request_attempt_log[0]["request_id"]
    )
    for artifact in artifacts:
        assert "?" not in artifact.source_url
        assert "sign=" not in artifact.source_url
        assert API_KEY not in json.dumps(artifact.model_dump(), ensure_ascii=False)
    assert len(transport.calls) == 2


def test_empty_signed_failures_from_different_endpoints_are_not_deduplicated() -> None:
    pdf_url = "https://open.zhihuiya.com/pdf/example.pdf?sign=pdf-fixture"
    image_url = "https://open.zhihuiya.com/image/example.png?sign=image-fixture"
    transport = ScriptedTransport(
        {
            "pdf": [TimeoutError("fixture PDF response was lost")],
            "image": [TimeoutError("fixture image response was lost")],
        }
    )
    provider = _provider(transport)

    with pytest.raises(ProviderRequestError):
        provider._request_signed_binary(
            pdf_url,
            accept="application/pdf",
            operation="P020_signed_pdf_download",
        )
    with pytest.raises(ProviderRequestError):
        provider._request_signed_binary(
            image_url,
            accept="image/png",
            operation="P042_signed_image_download",
        )

    artifacts = provider.last_media_artifacts
    assert len(artifacts) == 2
    assert all(item.kind == "provider_error_response" for item in artifacts)
    assert all(item.content == b"" for item in artifacts)
    assert [item.source_url for item in artifacts] == [
        "https://open.zhihuiya.com/pdf/example.pdf",
        "https://open.zhihuiya.com/image/example.png",
    ]
    operations = [item.request_attempt_log[0]["operation"] for item in artifacts]
    assert operations == [
        "P020_signed_pdf_download",
        "P042_signed_image_download",
    ]
    request_ids = [item.request_attempt_log[0]["request_id"] for item in artifacts]
    assert len(set(request_ids)) == 2
    assert len(transport.calls) == 2


def test_unknown_success_shape_and_unsafe_endpoints_fail_closed() -> None:
    transport = ScriptedTransport(
        {
            "count": [
                _json_response(
                    payload={"data": {"total": 42}, "status": True, "error_code": 0}
                )
            ]
        }
    )
    with pytest.raises(ProviderContentError, match="total_search_result_count"):
        _provider(transport).count(_query())

    with pytest.raises(ValueError, match="base_url"):
        PatsnapProvider(api_key=API_KEY, base_url="https://example.com")
    with pytest.raises(ValueError, match="路径白名单"):
        PatsnapProvider(api_key=API_KEY, search_path="/search/patent/unknown")


def test_provider_capabilities_remain_unverified_before_live_smoke() -> None:
    assert set(PatsnapProvider.capabilities.values()) == {"documented_unverified"}
    assert PATSNAP_BASE_URL == "https://connect.zhihuiya.com"


def test_publication_number_is_not_misclassified_as_vendor_patent_id() -> None:
    provider = PatsnapProvider(
        api_key=API_KEY,
        transport=ScriptedTransport({}),
    )
    lead = provider._coerce_lead("US20190300001A1")
    assert lead.publication_number == "US20190300001A1"
    assert lead.raw_metadata["patent_id"] is None
