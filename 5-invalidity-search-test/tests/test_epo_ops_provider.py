from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import fitz
import pytest

from invalidity.providers import (
    EpoOpsProvider,
    EvidenceRecord,
    EvidenceStage,
    ProviderContentError,
    ProviderRequestError,
    SearchQuery,
)


FIXTURES = Path(__file__).parent / "fixtures"
TOKEN_URL = "https://ops.epo.org/3.2/auth/accesstoken"
CONSUMER_KEY = "fixture-consumer-key"
CONSUMER_SECRET = "fixture-consumer-secret"
ACCESS_TOKEN_1 = "fixture-access-token-one"
ACCESS_TOKEN_2 = "fixture-access-token-two"


class FakeResponse:
    def __init__(
        self,
        *,
        content: bytes | str = b"",
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        json_data: Any = None,
        url: str = "",
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.content = content.encode("utf-8") if isinstance(content, str) else content
        self.text = self.content.decode("utf-8", errors="replace")
        self.headers = dict(headers or {})
        self._json_data = json_data

    def json(self) -> Any:
        if self._json_data is not None:
            return self._json_data
        return json.loads(self.text)


class ScriptedTransport:
    """Small HTTP double that records secrets only in-memory inside the test."""

    def __init__(self, routes: Mapping[str, Sequence[FakeResponse]]) -> None:
        self.routes = {key: list(responses) for key, responses in routes.items()}
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def resolve(_hostname: str) -> list[str]:
        return ["93.184.216.34"]

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        route = self._route(method.upper(), url)
        self.calls.append(
            {
                "method": method.upper(),
                "url": url,
                "params": dict(kwargs.get("params") or {}),
                "headers": dict(kwargs.get("headers") or {}),
                "data": kwargs.get("data"),
                "content": kwargs.get("content"),
                "timeout": kwargs.get("timeout"),
            }
        )
        try:
            response = self.routes[route].pop(0)
        except (KeyError, IndexError) as exc:
            raise AssertionError(
                f"unexpected or exhausted fixture route: {method} {url} ({route})"
            ) from exc
        if not response.url:
            response.url = url
        return response

    @staticmethod
    def _route(method: str, url: str) -> str:
        if method == "POST" and url == TOKEN_URL:
            return "token"
        if method == "GET" and (
            url.rstrip("/").endswith("/published-data/search")
            or url.rstrip("/").endswith("/published-data/search/biblio")
        ):
            return "search"
        if method == "GET" and url.rstrip("/").endswith("/biblio"):
            return "biblio"
        if method == "GET" and url.rstrip("/").endswith("/description"):
            return "description"
        if method == "GET" and url.rstrip("/").endswith("/claims"):
            return "claims"
        if method == "GET" and url.rstrip("/").endswith("/fulltext"):
            return "fulltext"
        if method == "GET" and "/published-data/images/" in url:
            return "pdf_page"
        if method == "GET" and url.rstrip("/").endswith("/images"):
            return "images"
        raise AssertionError(f"unexpected URL: {method} {url}")


def _xml_response(name: str, *, media_type: str) -> FakeResponse:
    return FakeResponse(
        content=(FIXTURES / name).read_bytes(),
        headers={
            "content-type": media_type,
            "date": "Mon, 20 Jul 2026 12:00:00 GMT",
        },
    )


def _token_response(token: str) -> FakeResponse:
    payload = {
        "issued_at": "1784548800000",
        "expires_in": "1199",
        "token_type": "Bearer",
        "access_token": token,
    }
    return FakeResponse(
        content=json.dumps(payload),
        json_data=payload,
        headers={"content-type": "application/json", "cache-control": "no-store"},
    )


def _search_input() -> SearchQuery:
    return SearchQuery.from_input(
        {
            "text": "robot cleaner lifting pad",
            "subject_terms": ["robot cleaner"],
            "feature_terms": ["lifting pad"],
        }
    )


def _calls(transport: ScriptedTransport, route: str) -> list[dict[str, Any]]:
    return [
        call
        for call in transport.calls
        if ScriptedTransport._route(call["method"], call["url"]) == route
    ]


def _header(call: Mapping[str, Any], name: str) -> str:
    for key, value in dict(call.get("headers") or {}).items():
        if str(key).lower() == name.lower():
            return str(value)
    return ""


def _body_text(call: Mapping[str, Any]) -> str:
    value = call.get("data")
    if value is None:
        value = call.get("content")
    if isinstance(value, Mapping):
        return "&".join(f"{key}={item}" for key, item in value.items())
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _valid_pdf(*, pages: int = 1) -> bytes:
    document = fitz.open()
    try:
        for page_number in range(1, pages + 1):
            page = document.new_page()
            page.insert_text((72, 72), f"Synthetic OPS page {page_number}")
        return document.tobytes(garbage=4, deflate=True)
    finally:
        document.close()


@pytest.mark.parametrize(
    "query",
    [
        _search_input(),
        {
            "text": "robot cleaner lifting pad",
            "subject_terms": ["robot cleaner"],
            "feature_terms": ["lifting pad"],
        },
        "robot cleaner lifting pad",
    ],
    ids=("search-query", "mapping", "string"),
)
def test_epo_oauth_client_credentials_and_namespace_search_stays_lead_only(
    query: SearchQuery | Mapping[str, Any] | str,
) -> None:
    search_bytes = (FIXTURES / "epo_ops_search.xml").read_bytes()
    transport = ScriptedTransport(
        {
            "token": [_token_response(ACCESS_TOKEN_1)],
            "search": [
                _xml_response(
                    "epo_ops_search.xml",
                    media_type="application/exchange+xml",
                )
            ],
        }
    )
    provider = EpoOpsProvider(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        transport=transport,
    )

    records = provider.search(query, max_results=5, server_before="2020-06-15")

    assert provider.provider_name == "epo_ops"
    assert len(records) == 1
    record = records[0]
    assert record.provider == "epo_ops"
    assert record.source_type == "patent"
    assert record.stage is EvidenceStage.LEAD
    assert record.publication_number == "EP3456789A1"
    assert record.authority == "EP"
    assert record.title == "Robot floor cleaner with a lifting cleaning pad"
    assert record.publication_date == "2019-11-20"
    assert record.filing_date == "2018-01-15"
    assert record.priority_date == "2017-01-16"
    assert "surface sensor" in (record.abstract or "")
    assert record.description is None
    assert record.claims is None
    assert record.full_text is None
    assert record.content_sha256 is None
    assert record.artifacts == ()
    assert hashlib.sha256(search_bytes).hexdigest() in json.dumps(record.provenance)
    search_artifact = provider.last_search_artifact
    assert search_artifact is not None
    assert search_artifact.kind == "provider_search_response"
    assert search_artifact.media_type == "application/exchange+xml"
    assert search_artifact.content == search_bytes
    assert search_artifact.content_sha256 == hashlib.sha256(search_bytes).hexdigest()
    assert "content" not in search_artifact.model_dump()

    token_call = _calls(transport, "token")[0]
    expected_basic = base64.b64encode(
        f"{CONSUMER_KEY}:{CONSUMER_SECRET}".encode("utf-8")
    ).decode("ascii")
    assert token_call["method"] == "POST"
    assert _header(token_call, "authorization") == f"Basic {expected_basic}"
    assert _header(token_call, "content-type").startswith(
        "application/x-www-form-urlencoded"
    )
    assert "grant_type=client_credentials" in _body_text(token_call)

    search_call = _calls(transport, "search")[0]
    assert _header(search_call, "authorization") == f"Bearer {ACCESS_TOKEN_1}"
    assert "xml" in _header(search_call, "accept").lower()
    assert _header(search_call, "x-ops-range") == "1-5"

    serialized = json.dumps(record.model_dump(), ensure_ascii=False, sort_keys=True)
    assert CONSUMER_KEY not in serialized
    assert CONSUMER_SECRET not in serialized
    assert ACCESS_TOKEN_1 not in serialized


def test_epo_cql_uses_ordinary_and_cn_conflicting_application_date_channels() -> None:
    transport = ScriptedTransport(
        {
            "token": [_token_response(ACCESS_TOKEN_1)],
            "search": [
                _xml_response(
                    "epo_ops_search.xml",
                    media_type="application/exchange+xml",
                ),
                _xml_response(
                    "epo_ops_search_cn.xml",
                    media_type="application/exchange+xml",
                ),
            ],
        }
    )
    provider = EpoOpsProvider(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        transport=transport,
    )

    ordinary = provider.search(
        _search_input(),
        max_results=4,
        server_before="2020-06-15",
    )
    conflicting = provider.search(
        _search_input(),
        max_results=4,
        country="CN",
        server_after="2020-06-15",
        server_before="2021-06-01",
        filing_before="2020-06-15",
    )

    assert [item.publication_number for item in ordinary] == ["EP3456789A1"]
    # A known late filing is excluded locally.  A row with missing filing and
    # priority dates is retained as a lead so the superset filter cannot lose
    # a potentially relevant conflicting-application candidate.
    assert [item.publication_number for item in conflicting] == [
        "CN111234567A",
        "CN111888888A",
    ]

    search_calls = _calls(transport, "search")
    ordinary_cql = str(search_calls[0]["params"]["q"])
    conflicting_cql = str(search_calls[1]["params"]["q"])
    assert "robot cleaner" in ordinary_cql.lower()
    assert re.search(r"\bpd\s*<\s*[\"']?20200615", ordinary_cql, re.IGNORECASE)
    assert "pn=CN" not in ordinary_cql.replace(" ", "")

    compact_conflicting = conflicting_cql.replace(" ", "")
    assert "pn=CN" in compact_conflicting
    assert re.search(r"\bpd\s*>\s*[\"']?20200615", conflicting_cql, re.IGNORECASE)
    assert re.search(r"\bpd\s*<\s*[\"']?20210601", conflicting_cql, re.IGNORECASE)
    # OPS CQL's ap/pr indexes are document numbers, not application/priority
    # dates.  The provider must not fabricate unsupported date predicates.
    assert not re.search(r"\b(?:ap|pr)\s*[<>]=?", conflicting_cql, re.IGNORECASE)

    for record in conflicting:
        assert record.provenance["filing_filter_execution"] == (
            "local_superset_filter"
        )
        assert record.provenance["provider_filing_before"] == "2020-06-15"
        assert record.provenance.get("provider_scope_degraded") is not True


def test_epo_retrieve_merges_biblio_description_claims_and_raw_hashes() -> None:
    fixture_names = (
        "epo_ops_search.xml",
        "epo_ops_biblio.xml",
        "epo_ops_fulltext.xml",
        "epo_ops_description.xml",
        "epo_ops_claims.xml",
        "epo_ops_images.xml",
    )
    transport = ScriptedTransport(
        {
            "token": [_token_response(ACCESS_TOKEN_1)],
            "search": [
                _xml_response(
                    "epo_ops_search.xml",
                    media_type="application/exchange+xml",
                )
            ],
            "biblio": [
                _xml_response(
                    "epo_ops_biblio.xml",
                    media_type="application/exchange+xml",
                )
            ],
            "fulltext": [
                _xml_response(
                    "epo_ops_fulltext.xml",
                    media_type="application/fulltext+xml",
                )
            ],
            "description": [
                _xml_response(
                    "epo_ops_description.xml",
                    media_type="application/fulltext+xml",
                )
            ],
            "claims": [
                _xml_response(
                    "epo_ops_claims.xml",
                    media_type="application/fulltext+xml",
                )
            ],
            "images": [
                _xml_response(
                    "epo_ops_images.xml",
                    media_type="application/ops+xml",
                )
            ],
            "pdf_page": [
                FakeResponse(
                    content=_valid_pdf(),
                    headers={"content-type": "application/pdf"},
                )
                for _ in range(3)
            ],
        }
    )
    provider = EpoOpsProvider(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        transport=transport,
    )
    lead = provider.search(_search_input(), max_results=1)[0]

    retrieved = provider.retrieve(lead)

    assert retrieved.stage is EvidenceStage.RETRIEVED
    assert retrieved.publication_number == "EP3456789A1"
    assert retrieved.publication_date == "2019-11-20"
    assert retrieved.filing_date == "2018-01-15"
    assert retrieved.priority_date == "2017-01-16"
    assert "electric lifting actuator" in (retrieved.description or "")
    assert "selectively raise the cleaning pad" in (retrieved.claims or "")
    assert "Robot floor cleaner" in (retrieved.full_text or "")
    assert "electric lifting actuator" in (retrieved.full_text or "")
    assert "selectively raise the cleaning pad" in (retrieved.full_text or "")
    assert retrieved.content_sha256 and len(retrieved.content_sha256) == 64
    assert "epo" in str(retrieved.provenance["public_date_evidence"]).lower()
    assert retrieved.provider == "epo_ops"
    assert retrieved.primary_artifact is not None
    assert retrieved.primary_artifact.media_type == "application/pdf"
    assert retrieved.primary_artifact.kind == "pdf"
    with fitz.open(
        stream=retrieved.primary_artifact.content,
        filetype="pdf",
    ) as merged:
        assert merged.page_count == 3
    assert len(_calls(transport, "pdf_page")) == 3
    assert retrieved.provenance["retrieval_identity"]["match"] == "exact"
    assert retrieved.provenance["full_document"]["page_count"] == 3

    provenance = json.dumps(retrieved.provenance, ensure_ascii=False, sort_keys=True)
    for fixture_name in fixture_names:
        expected_hash = hashlib.sha256((FIXTURES / fixture_name).read_bytes()).hexdigest()
        assert expected_hash in provenance
    for frozen_date in ("2019-11-20", "2018-01-15", "2017-01-16"):
        assert frozen_date in provenance
    for sensitive in (CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN_1):
        assert sensitive not in provenance

    assert len(_calls(transport, "biblio")) == 1
    assert len(_calls(transport, "description")) == 1
    assert len(_calls(transport, "claims")) == 1


def test_epo_retrieve_rejects_a_different_publication_returned_by_biblio() -> None:
    mismatched = (FIXTURES / "epo_ops_biblio.xml").read_text(encoding="utf-8")
    mismatched = mismatched.replace("3456789", "9999999")
    transport = ScriptedTransport(
        {
            "token": [_token_response(ACCESS_TOKEN_1)],
            "biblio": [
                FakeResponse(
                    content=mismatched,
                    headers={"content-type": "application/exchange+xml"},
                )
            ],
        }
    )
    provider = EpoOpsProvider(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        transport=transport,
    )
    patsnap_lead = EvidenceRecord(
        provider="patsnap",
        source_type="patent",
        external_id="718ead9c-4f3c-4674-8f5a-24e126827269",
        title="Patsnap candidate",
        source_url="https://connect.zhihuiya.com/search/patent/query-search-patent/v2",
        publication_number="EP3456789A1",
        provenance={"discovery_provider": "patsnap"},
    )

    with pytest.raises(ProviderContentError, match="公开编号.*不一致"):
        provider.retrieve(patsnap_lead)

    tracked = provider.last_retrieval_artifacts
    assert len(tracked) == 1
    assert tracked[0].kind.endswith("_biblio")


def test_epo_resolves_application_number_only_when_publication_number_is_absent() -> None:
    transport = ScriptedTransport(
        {
            "token": [_token_response(ACCESS_TOKEN_1)],
            "biblio": [
                _xml_response(
                    "epo_ops_biblio.xml",
                    media_type="application/exchange+xml",
                ),
                _xml_response(
                    "epo_ops_biblio.xml",
                    media_type="application/exchange+xml",
                ),
            ],
            "fulltext": [
                _xml_response(
                    "epo_ops_fulltext.xml",
                    media_type="application/fulltext+xml",
                )
            ],
            "description": [
                _xml_response(
                    "epo_ops_description.xml",
                    media_type="application/fulltext+xml",
                )
            ],
            "claims": [
                _xml_response(
                    "epo_ops_claims.xml",
                    media_type="application/fulltext+xml",
                )
            ],
            "images": [
                _xml_response(
                    "epo_ops_images.xml",
                    media_type="application/ops+xml",
                )
            ],
            "pdf_page": [
                FakeResponse(
                    content=_valid_pdf(),
                    headers={"content-type": "application/pdf"},
                )
                for _ in range(3)
            ],
        }
    )
    provider = EpoOpsProvider(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        transport=transport,
    )
    lead = EvidenceRecord(
        provider="patsnap",
        source_type="patent",
        external_id="718ead9c-4f3c-4674-8f5a-24e126827269",
        title="Application-only lead",
        source_url="https://connect.zhihuiya.com/search/patent/query-search-patent/v2",
        authority="EP",
        raw_metadata={"application_number": "EP18700001A"},
        provenance={"discovery_provider": "patsnap"},
    )

    retrieved = provider.retrieve(lead)

    assert retrieved.publication_number == "EP3456789A1"
    assert retrieved.external_id == lead.external_id
    assert retrieved.provenance["retrieval_identity"] == {
        "requested_publication_number": "EP3456789A1",
        "returned_publication_number": "EP3456789A1",
        "requested_application_number": "EP18700001A",
        "match": "exact",
    }
    biblio_calls = _calls(transport, "biblio")
    assert "/published-data/application/epodoc/EP18700001.A/biblio" in (
        biblio_calls[0]["url"]
    )
    assert "/published-data/publication/epodoc/EP3456789.A1/biblio" in (
        biblio_calls[1]["url"]
    )
    assert "application_biblio" in retrieved.provenance["detail_response_hashes"]


def test_epo_401_refreshes_oauth_token_exactly_once() -> None:
    transport = ScriptedTransport(
        {
            "token": [
                _token_response(ACCESS_TOKEN_1),
                _token_response(ACCESS_TOKEN_2),
            ],
            "search": [
                FakeResponse(
                    status_code=401,
                    content="expired token",
                    headers={"content-type": "application/xml"},
                ),
                _xml_response(
                    "epo_ops_search.xml",
                    media_type="application/exchange+xml",
                ),
            ],
        }
    )
    provider = EpoOpsProvider(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        transport=transport,
    )

    records = provider.search(_search_input(), max_results=1)

    assert len(records) == 1
    assert len(_calls(transport, "token")) == 2
    resource_calls = _calls(transport, "search")
    assert len(resource_calls) == 2
    assert _header(resource_calls[0], "authorization") == f"Bearer {ACCESS_TOKEN_1}"
    assert _header(resource_calls[1], "authorization") == f"Bearer {ACCESS_TOKEN_2}"


def test_epo_second_401_and_429_fail_closed_without_secret_leakage() -> None:
    repeated_401 = ScriptedTransport(
        {
            "token": [
                _token_response(ACCESS_TOKEN_1),
                _token_response(ACCESS_TOKEN_2),
            ],
            "search": [
                FakeResponse(status_code=401, content=f"expired {CONSUMER_SECRET}"),
                FakeResponse(status_code=401, content=f"rejected {ACCESS_TOKEN_2}"),
            ],
        }
    )
    with pytest.raises(ProviderRequestError) as second_401:
        EpoOpsProvider(
            consumer_key=CONSUMER_KEY,
            consumer_secret=CONSUMER_SECRET,
            transport=repeated_401,
        ).search(_search_input(), max_results=1)
    assert len(_calls(repeated_401, "token")) == 2
    assert len(_calls(repeated_401, "search")) == 2

    rate_limited = ScriptedTransport(
        {
            "token": [_token_response(ACCESS_TOKEN_1)],
            "search": [
                FakeResponse(
                    status_code=429,
                    content=(
                        f"upstream body must stay private {CONSUMER_KEY} "
                        f"{CONSUMER_SECRET} {ACCESS_TOKEN_1}"
                    ),
                    headers={"content-type": "application/xml", "retry-after": "60"},
                )
            ],
        }
    )
    with pytest.raises(ProviderRequestError, match="429") as limited:
        EpoOpsProvider(
            consumer_key=CONSUMER_KEY,
            consumer_secret=CONSUMER_SECRET,
            transport=rate_limited,
        ).search(_search_input(), max_results=1)
    assert len(_calls(rate_limited, "token")) == 1
    assert len(_calls(rate_limited, "search")) == 1

    all_errors = f"{second_401.value!s}\n{second_401.value!r}\n{limited.value!s}\n{limited.value!r}"
    for sensitive in (CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN_1, ACCESS_TOKEN_2):
        assert sensitive not in all_errors


def test_epo_download_image_requests_and_validates_one_real_ops_pdf_page() -> None:
    page_bytes = _valid_pdf(pages=1)
    transport = ScriptedTransport(
        {
            "token": [_token_response(ACCESS_TOKEN_1)],
            "images": [
                _xml_response(
                    "epo_ops_images.xml",
                    media_type="application/ops+xml",
                )
            ],
            "pdf_page": [
                FakeResponse(
                    content=page_bytes,
                    headers={"content-type": "application/pdf"},
                )
            ],
        }
    )
    provider = EpoOpsProvider(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        transport=transport,
    )
    lead = EvidenceRecord(
        provider="epo_ops",
        source_type="patent",
        external_id="EP3456789A1",
        title="Robot floor cleaner with a lifting cleaning pad",
        source_url=(
            "https://ops.epo.org/3.2/rest-services/published-data/"
            "publication/epodoc/EP3456789.A1/biblio"
        ),
        publication_number="EP3456789A1",
        authority="EP",
        raw_metadata={
            "ops_fullimage_link": "EP/3456789/A1/fullimage",
            "ops_fullimage_pages": 3,
        },
    )

    artifact = provider.download_image(lead, index=1)

    assert artifact.provider == "epo_ops"
    assert artifact.kind == "image"
    assert artifact.media_type == "image/png"
    assert artifact.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(artifact.content_sha256) == 64

    page_call = _calls(transport, "pdf_page")[0]
    requested_page = _header(page_call, "x-ops-range") or str(
        page_call["params"].get("Range") or ""
    )
    assert requested_page == "2"
    assert "/published-data/images/EP/3456789/A1/fullimage" in page_call["url"]


def test_epo_download_image_rejects_a_pdf_marker_without_a_parseable_page() -> None:
    transport = ScriptedTransport(
        {
            "token": [_token_response(ACCESS_TOKEN_1)],
            "images": [
                _xml_response(
                    "epo_ops_images.xml",
                    media_type="application/ops+xml",
                )
            ],
            "pdf_page": [
                FakeResponse(
                    content=b"%PDF-1.7\nnot a complete PDF page",
                    headers={"content-type": "application/pdf"},
                )
            ],
        }
    )

    with pytest.raises(ProviderContentError, match="PDF|页"):
        EpoOpsProvider(
            consumer_key=CONSUMER_KEY,
            consumer_secret=CONSUMER_SECRET,
            transport=transport,
        ).download_image(
            EvidenceRecord(
                provider="epo_ops",
                source_type="patent",
                external_id="EP3456789A1",
                title="Synthetic OPS lead",
                source_url="https://ops.epo.org/fixture/EP3456789A1",
                publication_number="EP3456789A1",
                authority="EP",
                raw_metadata={
                    "ops_fullimage_link": "EP/3456789/A1/fullimage",
                    "ops_fullimage_pages": 3,
                },
            ),
            index=0,
        )
