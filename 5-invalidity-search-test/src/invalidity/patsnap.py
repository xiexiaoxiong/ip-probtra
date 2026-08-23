"""Fail-closed Patsnap (智慧芽) REST adapter.

The adapter deliberately keeps the vendor DTO at this boundary.  Search rows
are leads, claims/description responses are retrieved documents, and legal
date/content qualification remains local to the invalidity workflow.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import date, timedelta
import ipaddress
import json
import math
import re
import time
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse
import uuid

from bs4 import BeautifulSoup
import fitz

from .date_rules import classify_date_eligibility, parse_date
from .providers import (
    DEFAULT_MAX_IMAGE_PIXELS,
    DEFAULT_MAX_IMAGE_RESPONSE_BYTES,
    DEFAULT_MAX_REDIRECTS,
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    EvidenceRecord,
    EvidenceStage,
    ProviderContentError,
    ProviderRequestError,
    ResponseLike,
    RetrievedArtifact,
    SearchQuery,
    Transport,
    _BaseHttpProvider,
    _normalize_publication_number,
    _response_content,
    _response_header,
    _sha256,
    _validate_raster_image,
    qualify_evidence,
)


PATSNAP_BASE_URL = "https://connect.zhihuiya.com"
PATSNAP_COUNT_PATH = "/search/patent/query-search-count/v2"
PATSNAP_SEARCH_PATH = "/search/patent/query-search-patent/v2"
PATSNAP_SINGLE_IMAGE_SEARCH_PATH = "/search/patent/image-single/beta"
PATSNAP_MULTIPLE_IMAGE_SEARCH_PATH = "/search/patent/image-multiple"
PATSNAP_BIBLIOGRAPHY_PATH = "/basic-patent-data/bibliography"
PATSNAP_CLAIMS_PATH = "/basic-patent-data/claim-data"
PATSNAP_DESCRIPTION_PATH = "/basic-patent-data/description-data"
PATSNAP_PDF_PATH = "/basic-patent-data/pdf-data"
PATSNAP_FULLTEXT_IMAGE_PATH = "/basic-patent-data/fulltext-image"

ALLOWED_COUNT_PATHS = frozenset(
    {
        "/search/patent/query-search-count",
        "/search/patent/query-search-count/v2",
    }
)
ALLOWED_SEARCH_PATHS = frozenset(
    {
        "/search/patent/query-search-patent",
        "/search/patent/query-search-patent/v2",
    }
)
# The public error guide explicitly marks only this provider-busy code as a
# retry candidate.  In particular, do not turn path/parameter/account errors
# into repeated metered POST requests.
_TRANSIENT_BUSINESS_CODES = frozenset({68300008})
_FIELD_EXPRESSION = re.compile(
    r"(?:^|[\s(])(?:TACD|TAC|TTL|ABST|CLMS|DESC|PN|APNO|IPC|CPC|"
    r"AUTHORITY|PBD|APD|PRIORITY_DATE)\s*:",
    re.IGNORECASE,
)
_PATENT_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class PatsnapApiError(ProviderRequestError):
    """A safe, classified Patsnap API failure with no response body leakage."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        correlation_id: str | None = None,
        retryable: bool = False,
        request_attempt_log: Sequence[Mapping[str, Any]] = (),
        raw_response_artifact: RetrievedArtifact | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.correlation_id = correlation_id
        self.retryable = retryable
        self.request_attempt_log = tuple(dict(item) for item in request_attempt_log)
        self.raw_response_artifact = raw_response_artifact

    def __str__(self) -> str:
        message = super().__str__()
        if not self.request_attempt_log:
            return message
        correlations = [
            str(item.get("correlation_id"))
            for item in self.request_attempt_log
            if item.get("correlation_id")
        ]
        amounts = [
            str(item.get("openapi_amount"))
            for item in self.request_attempt_log
            if item.get("openapi_amount")
        ]
        audit = f"attempts={len(self.request_attempt_log)}"
        if correlations:
            audit += f", correlation_ids={','.join(correlations)}"
        if amounts:
            audit += f", openapi_amounts={','.join(amounts)}"
        return f"{message}; {audit}"


@dataclass(frozen=True, slots=True)
class PatsnapCountResult:
    total_search_result_count: int
    query_text: str
    endpoint: str
    request_attempts: int
    correlation_id: str | None
    openapi_amount: str | None
    request_attempt_log: tuple[Mapping[str, Any], ...]
    artifact: RetrievedArtifact

    def model_dump(self) -> dict[str, Any]:
        return {
            "total_search_result_count": self.total_search_result_count,
            "query_text": self.query_text,
            "endpoint": self.endpoint,
            "request_attempts": self.request_attempts,
            "correlation_id": self.correlation_id,
            "openapi_amount": self.openapi_amount,
            "request_attempt_log": [dict(item) for item in self.request_attempt_log],
            "artifact": self.artifact.model_dump(include_content=False),
        }


class PatsnapProvider(_BaseHttpProvider):
    """P001/P002/P012/P018/P019/P020/P042/P060-beta/P061 adapter."""

    provider_name = "patsnap"
    # These are documented capabilities, not a claim that the user's product
    # subscription has passed a live permission/response/retention smoke test.
    capabilities: Mapping[str, Any] = {
        "discovery": "documented_unverified",
        "bibliographic_verification": "documented_unverified",
        "full_text": "documented_unverified",
        "pdf": "documented_unverified",
        "images": "documented_unverified",
        "family": "documented_unverified",
        "publication_date": "documented_unverified",
        "filing_date": "documented_unverified",
        "priority_date": "documented_unverified",
    }

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = PATSNAP_BASE_URL,
        count_path: str = PATSNAP_COUNT_PATH,
        search_path: str = PATSNAP_SEARCH_PATH,
        transport: Transport | Callable[..., ResponseLike] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_image_response_bytes: int = DEFAULT_MAX_IMAGE_RESPONSE_BYTES,
        max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        resolver: Callable[[str], Iterable[str]] | None = None,
        retry_attempts: int = 3,
        retry_base_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(
            transport=transport,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            max_image_response_bytes=max_image_response_bytes,
            max_image_pixels=max_image_pixels,
            max_redirects=max_redirects,
            resolver=resolver,
        )
        self.api_key = _validated_api_key(api_key)
        self.base_url = _validated_base_url(base_url)
        self.count_path = _validated_endpoint_path(
            count_path,
            allowed=ALLOWED_COUNT_PATHS,
            name="Patsnap count_path",
        )
        self.search_path = _validated_endpoint_path(
            search_path,
            allowed=ALLOWED_SEARCH_PATHS,
            name="Patsnap search_path",
        )
        if retry_attempts < 1 or retry_attempts > 5:
            raise ValueError("retry_attempts 必须在 1..5 之间")
        if retry_base_seconds < 0 or retry_base_seconds > 5:
            raise ValueError("retry_base_seconds 必须在 0..5 之间")
        self.retry_attempts = int(retry_attempts)
        self.retry_base_seconds = float(retry_base_seconds)
        self._sleep = sleep
        self._last_search_artifact: RetrievedArtifact | None = None
        self._last_count_artifact: RetrievedArtifact | None = None
        self._last_retrieval_artifacts: tuple[RetrievedArtifact, ...] = ()
        self._last_media_artifacts: tuple[RetrievedArtifact, ...] = ()
        self._image_path_cache: dict[str, tuple[str, ...]] = {}
        self._image_metadata_cache: dict[str, RetrievedArtifact] = {}

    @property
    def last_search_artifact(self) -> RetrievedArtifact | None:
        return self._last_search_artifact

    @property
    def last_count_artifact(self) -> RetrievedArtifact | None:
        return self._last_count_artifact

    @property
    def last_retrieval_artifacts(self) -> tuple[RetrievedArtifact, ...]:
        return self._last_retrieval_artifacts

    @property
    def last_media_artifacts(self) -> tuple[RetrievedArtifact, ...]:
        return self._last_media_artifacts

    def count(
        self,
        query: str | Mapping[str, Any] | SearchQuery,
        *,
        country: str | None = None,
        server_before: date | str | None = None,
        server_after: date | str | None = None,
        filing_before: date | str | None = None,
        subject_terms: Sequence[str] | None = None,
        feature_terms: Sequence[str] | None = None,
    ) -> PatsnapCountResult:
        safe_query = SearchQuery.from_input(
            query,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        query_text, _filters = _patsnap_query_text(
            safe_query,
            country=country,
            server_before=server_before,
            server_after=server_after,
            filing_before=filing_before,
            max_length=3000,
        )
        response_payload = {
            "query_text": query_text,
            "collapse_type": "ALL",
            "collapse_by": "PBD",
            "collapse_order": "OLDEST",
            "collapse_order_authority": ["CN", "US", "EP", "JP", "KR"],
            "stemming": 0,
        }
        endpoint = self._endpoint(self.count_path)
        self._last_count_artifact = None
        try:
            payload, raw, audit = self._request_json(
                "POST",
                self.count_path,
                payload=response_payload,
            )
        except PatsnapApiError as exc:
            if exc.raw_response_artifact is not None:
                self._last_count_artifact = exc.raw_response_artifact
            raise
        artifact = RetrievedArtifact(
            provider=self.provider_name,
            kind="provider_count_response",
            source_url=endpoint,
            media_type="application/json",
            content=raw,
            request_attempt_log=tuple(audit.get("request_attempt_log") or ()),
        )
        # Retain freeze-capable bytes even when HTTP succeeds but the vendor
        # business payload is malformed.
        self._last_count_artifact = artifact
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ProviderContentError("智慧芽 P001 响应缺少 data 对象")
        raw_count = data.get("total_search_result_count")
        if raw_count is None:
            # Kept only for the alternate shape documented by the official
            # first-request guide.  No other aliases are accepted.
            raw_count = data.get("count")
        total = _nonnegative_int(raw_count, name="P001 total_search_result_count")
        return PatsnapCountResult(
            total_search_result_count=total,
            query_text=query_text,
            endpoint=endpoint,
            request_attempts=int(audit["request_attempts"]),
            correlation_id=audit.get("correlation_id"),
            openapi_amount=audit.get("openapi_amount"),
            request_attempt_log=tuple(audit.get("request_attempt_log") or ()),
            artifact=artifact,
        )

    def search(
        self,
        query: str | Mapping[str, Any] | SearchQuery,
        *,
        max_results: int = 20,
        language: str = "en",
        country: str | None = None,
        server_before: date | str | None = None,
        server_after: date | str | None = None,
        filing_before: date | str | None = None,
        subject_terms: Sequence[str] | None = None,
        feature_terms: Sequence[str] | None = None,
    ) -> list[EvidenceRecord]:
        safe_query = SearchQuery.from_input(
            query,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        if max_results < 1 or max_results > 1000:
            raise ValueError("max_results 必须在 1..1000 之间")
        query_text, filters = _patsnap_query_text(
            safe_query,
            country=country,
            server_before=server_before,
            server_after=server_after,
            filing_before=filing_before,
            max_length=12000,
        )
        request_payload = {
            "query_text": query_text,
            "collapse_type": "ALL",
            "collapse_by": "PBD",
            "collapse_order": "OLDEST",
            "collapse_order_authority": ["CN", "US", "EP", "JP", "KR"],
            "sort": [{"field": "SCORE", "order": "DESC"}],
            "offset": 0,
            "limit": max_results,
            "stemming": 0,
        }
        self._last_search_artifact = None
        endpoint = self._endpoint(self.search_path)
        try:
            payload, raw, audit = self._request_json(
                "POST",
                self.search_path,
                payload=request_payload,
            )
        except PatsnapApiError as exc:
            if exc.raw_response_artifact is not None:
                self._last_search_artifact = exc.raw_response_artifact
            raise
        artifact = RetrievedArtifact(
            provider=self.provider_name,
            kind="provider_search_response",
            source_url=endpoint,
            media_type="application/json",
            content=raw,
            request_attempt_log=tuple(audit.get("request_attempt_log") or ()),
        )
        self._last_search_artifact = artifact
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ProviderContentError("智慧芽 P002 响应缺少 data 对象")
        if data == {}:
            # Real P002 zero-hit responses use an exact empty data object after
            # the already-validated status=true/error_code=0 envelope.
            results: list[Any] = []
            total = 0
            result_count = 0
        else:
            results = data.get("results")
            if not isinstance(results, list):
                raise ProviderContentError("智慧芽 P002 响应缺少 results 数组")
            total = _nonnegative_int(
                data.get("total_search_result_count"),
                name="P002 total_search_result_count",
            )
            result_count = _nonnegative_int(
                data.get("result_count", len(results)),
                name="P002 result_count",
            )
        if result_count != len(results) or (not results and total > 0):
            raise ProviderContentError("智慧芽 P002 返回数量与 results 不一致")

        requested_language = str(language or "").strip().lower() or None
        records: list[EvidenceRecord] = []
        seen: set[str] = set()
        for item in results:
            if not isinstance(item, Mapping):
                raise ProviderContentError("智慧芽 P002 results 含非对象元素")
            patent_id = _validated_patent_id(item.get("patent_id"))
            publication_number = _normalize_publication_number(item.get("pn"))
            if not patent_id or not publication_number:
                raise ProviderContentError("智慧芽 P002 文献缺少 patent_id 或 pn")
            if patent_id in seen:
                continue
            publication_date = _patsnap_date(item.get("pbdt"))
            filing_date = _patsnap_date(item.get("apdt"))
            authority = str(item.get("authority") or "").strip().upper() or None
            _assert_search_row_matches_filters(
                authority=authority,
                publication_date=publication_date,
                filters=filters,
            )
            seen.add(patent_id)
            records.append(
                EvidenceRecord(
                    provider=self.provider_name,
                    source_type="patent",
                    external_id=patent_id,
                    title=str(item.get("title") or "").strip(),
                    source_url=self._endpoint(PATSNAP_BIBLIOGRAPHY_PATH),
                    stage=EvidenceStage.LEAD,
                    publication_number=publication_number,
                    authority=authority,
                    language=requested_language,
                    publication_date=publication_date,
                    filing_date=filing_date,
                    raw_metadata={
                        "patent_id": patent_id,
                        "application_number": str(item.get("apno") or "").strip()
                        or None,
                        "inventor": str(item.get("inventor") or "").strip() or None,
                        "current_assignee": str(
                            item.get("current_assignee") or ""
                        ).strip()
                        or None,
                        "original_assignee": str(
                            item.get("original_assignee") or ""
                        ).strip()
                        or None,
                    },
                    provenance={
                        "discovery_provider": self.provider_name,
                        "search_query": safe_query.model_dump(),
                        "patsnap_query_text": query_text,
                        "search_endpoint": endpoint,
                        "search_response_sha256": artifact.content_sha256,
                        "search_response_bytes": len(raw),
                        "search_retrieved_at": artifact.retrieved_at,
                        "requested_offset": 0,
                        "requested_limit": max_results,
                        "result_count": result_count,
                        "total_result_count": total,
                        "request_attempts": audit["request_attempts"],
                        "correlation_id": audit.get("correlation_id"),
                        "openapi_amount": audit.get("openapi_amount"),
                        "request_attempt_log": list(
                            audit.get("request_attempt_log") or ()
                        ),
                        "provider_server_before": filters.get("server_before"),
                        "provider_server_after": filters.get("server_after"),
                        "provider_filing_before": filters.get("filing_before"),
                        "provider_country": filters.get("country"),
                        "provider_filters_are_discovery_only": True,
                        "capabilities": dict(self.capabilities),
                    },
                )
            )
        return records

    def search_single_image(
        self,
        image_url: str,
        *,
        patent_type: str,
        model: int,
        max_results: int = 20,
        offset: int = 0,
    ) -> list[EvidenceRecord]:
        """Return P060-beta image-similarity hits as provider-neutral leads."""

        return self._search_images(
            (_validated_https_image_url(image_url, name="image_url"),),
            path=PATSNAP_SINGLE_IMAGE_SEARCH_PATH,
            operation="P060_beta",
            patent_type=patent_type,
            model=model,
            max_results=max_results,
            offset=offset,
        )

    def search_multiple_images(
        self,
        image_urls: Sequence[str],
        *,
        patent_type: str,
        model: int,
        max_results: int = 20,
        offset: int = 0,
    ) -> list[EvidenceRecord]:
        """Return P061 multi-image similarity hits as provider-neutral leads."""

        if isinstance(image_urls, (str, bytes)) or not isinstance(
            image_urls, Sequence
        ):
            raise ValueError("P061 image_urls 必须是 2..4 个 HTTPS URL 的序列")
        if len(image_urls) < 2 or len(image_urls) > 4:
            raise ValueError("P061 image_urls 数量必须在 2..4 之间")
        validated_urls = tuple(
            _validated_https_image_url(item, name=f"image_urls[{index}]")
            for index, item in enumerate(image_urls)
        )
        if len(set(validated_urls)) != len(validated_urls):
            raise ValueError("P061 image_urls 必须是互不相同的图片 URL")
        return self._search_images(
            validated_urls,
            path=PATSNAP_MULTIPLE_IMAGE_SEARCH_PATH,
            operation="P061",
            patent_type=patent_type,
            model=model,
            max_results=max_results,
            offset=offset,
        )

    def _search_images(
        self,
        image_urls: tuple[str, ...],
        *,
        path: str,
        operation: str,
        patent_type: str,
        model: int,
        max_results: int,
        offset: int,
    ) -> list[EvidenceRecord]:
        normalized_patent_type, normalized_model = _image_model_and_patent_type(
            patent_type=patent_type,
            model=model,
            operation=operation,
        )
        normalized_limit = _bounded_int(
            max_results,
            name="max_results",
            minimum=1,
            maximum=100,
        )
        normalized_offset = _bounded_int(
            offset,
            name="offset",
            minimum=0,
            maximum=1000,
        )
        for image_url in image_urls:
            try:
                self._validate_public_url(image_url)
            except ProviderRequestError as exc:
                raise ValueError(
                    "智慧芽图像检索 URL 必须通过公网 DNS 校验"
                ) from exc
        if path == PATSNAP_SINGLE_IMAGE_SEARCH_PATH and len(image_urls) == 1:
            image_field: dict[str, Any] = {"url": image_urls[0]}
        elif path == PATSNAP_MULTIPLE_IMAGE_SEARCH_PATH and 2 <= len(image_urls) <= 4:
            image_field = {"urls": list(image_urls)}
        else:
            raise ValueError("智慧芽图像检索 endpoint 与图片数量不匹配")

        request_payload = {
            **image_field,
            "model": normalized_model,
            "patent_type": normalized_patent_type,
            "field": "SCORE",
            "offset": normalized_offset,
            "limit": normalized_limit,
            "is_https": 1,
            "return_img_id": True,
        }
        endpoint = self._endpoint(path)
        self._last_search_artifact = None
        try:
            payload, raw, audit = self._request_json(
                "POST",
                path,
                payload=request_payload,
            )
        except PatsnapApiError as exc:
            if exc.raw_response_artifact is not None:
                self._last_search_artifact = exc.raw_response_artifact
            raise

        artifact = RetrievedArtifact(
            provider=self.provider_name,
            kind="provider_image_search_response",
            source_url=endpoint,
            media_type="application/json",
            content=raw,
            request_attempt_log=tuple(audit.get("request_attempt_log") or ()),
        )
        # Keep the bounded raw response available to the isolated artifact
        # gateway even when subsequent endpoint-specific validation fails.
        self._last_search_artifact = artifact
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise ProviderContentError(
                f"智慧芽 {operation} 响应缺少 data 对象"
            )
        messages = data.get("patent_messages")
        if not isinstance(messages, list):
            raise ProviderContentError(
                f"智慧芽 {operation} 响应缺少 patent_messages 数组"
            )
        total = _nonnegative_int(
            data.get("total_search_result_count"),
            name=f"{operation} total_search_result_count",
        )
        if len(messages) > normalized_limit or total < len(messages):
            raise ProviderContentError(
                f"智慧芽 {operation} 返回数量与 patent_messages 不一致"
            )

        input_url_hashes = tuple(
            _sha256(item.encode("utf-8")) for item in image_urls
        )
        records: list[EvidenceRecord] = []
        seen: dict[str, str] = {}
        for item in messages:
            if not isinstance(item, Mapping):
                raise ProviderContentError(
                    f"智慧芽 {operation} patent_messages 含非对象元素"
                )
            patent_id = _validated_patent_id(item.get("patent_id"))
            publication_number = _normalize_publication_number(
                item.get("patent_pn")
            )
            if not patent_id or not publication_number:
                raise ProviderContentError(
                    f"智慧芽 {operation} 文献缺少 patent_id 或 patent_pn"
                )
            previous_pn = seen.get(patent_id)
            if previous_pn is not None:
                if previous_pn != publication_number:
                    raise ProviderContentError(
                        f"智慧芽 {operation} 同一 patent_id 返回冲突 patent_pn"
                    )
                continue
            title = _required_text(
                item.get("title"),
                name=f"{operation} title",
                maximum=2000,
            )
            matched_image_id = _required_text(
                item.get("img_id"),
                name=f"{operation} img_id",
                maximum=512,
            )
            try:
                matched_image_url = _validated_https_image_url(
                    item.get("url"),
                    name=f"{operation} result url",
                )
            except ValueError as exc:
                raise ProviderContentError(str(exc)) from exc
            publication_date = _patsnap_date(item.get("pbdt"))
            filing_date = _patsnap_date(item.get("apdt"))
            score = _image_similarity_score(
                item.get("score"),
                name=f"{operation} score",
            )
            authority = _image_result_authority(
                item.get("authority"),
                publication_number=publication_number,
                operation=operation,
            )
            application_number = _optional_text(
                item.get("apno"),
                name=f"{operation} apno",
                maximum=512,
            )
            inventor = _optional_text(
                item.get("inventor"),
                name=f"{operation} inventor",
                maximum=4000,
            )
            current_assignee = _optional_text(
                item.get("current_assignee"),
                name=f"{operation} current_assignee",
                maximum=4000,
            )
            original_assignee = _optional_text(
                item.get("original_assignee"),
                name=f"{operation} original_assignee",
                maximum=4000,
            )
            loc_classifications = _loc_classifications(
                item.get("loc"),
                operation=operation,
            )

            seen[patent_id] = publication_number
            records.append(
                EvidenceRecord(
                    provider=self.provider_name,
                    source_type="patent",
                    external_id=patent_id,
                    title=title,
                    source_url=endpoint,
                    stage=EvidenceStage.LEAD,
                    publication_number=publication_number,
                    authority=authority,
                    publication_date=publication_date,
                    filing_date=filing_date,
                    raw_metadata={
                        "patent_id": patent_id,
                        "application_number": application_number,
                        "inventor": inventor,
                        "current_assignee": current_assignee,
                        "original_assignee": original_assignee,
                        "matched_image_id": matched_image_id,
                        "matched_image_url_sha256": _sha256(
                            matched_image_url.encode("utf-8")
                        ),
                        "matched_image_url_redacted": _redacted_download_url(
                            matched_image_url
                        ),
                        "image_similarity_score": score,
                        "loc_classifications": list(loc_classifications),
                    },
                    provenance={
                        "discovery_provider": self.provider_name,
                        "search_modality": "image_similarity",
                        "image_search_operation": operation,
                        "image_search_endpoint": endpoint,
                        "image_search_response_sha256": artifact.content_sha256,
                        "image_search_response_bytes": len(raw),
                        "image_search_retrieved_at": artifact.retrieved_at,
                        "input_image_count": len(image_urls),
                        "input_image_url_sha256": list(input_url_hashes),
                        "patent_type": normalized_patent_type,
                        "image_model": normalized_model,
                        "requested_offset": normalized_offset,
                        "requested_limit": normalized_limit,
                        "total_result_count": total,
                        "request_attempts": audit["request_attempts"],
                        "correlation_id": audit.get("correlation_id"),
                        "openapi_amount": audit.get("openapi_amount"),
                        "request_attempt_log": list(
                            audit.get("request_attempt_log") or ()
                        ),
                        "temporary_result_image_not_retrieved": True,
                        "provider_filters_are_discovery_only": True,
                        "capabilities": dict(self.capabilities),
                    },
                )
            )
        return records

    def retrieve(
        self,
        lead: EvidenceRecord | Mapping[str, Any] | str,
    ) -> EvidenceRecord:
        self._last_retrieval_artifacts = ()
        record = self._coerce_lead(lead)
        reference_key, reference_value, patent_id = _detail_reference(record)
        params: dict[str, Any] = {reference_key: reference_value}
        biblio_payload, biblio_raw, biblio_audit = self._tracked_request_json(
            "GET",
            PATSNAP_BIBLIOGRAPHY_PATH,
            kind="provider_bibliography_response",
            tracking_attribute="_last_retrieval_artifacts",
            params=params,
        )
        claims_payload, claims_raw, claims_audit = self._tracked_request_json(
            "GET",
            PATSNAP_CLAIMS_PATH,
            kind="provider_claims_response",
            tracking_attribute="_last_retrieval_artifacts",
            params={**params, "replace_by_related": 0},
        )
        description_payload, description_raw, description_audit = (
            self._tracked_request_json(
                "GET",
                PATSNAP_DESCRIPTION_PATH,
                kind="provider_description_response",
                tracking_attribute="_last_retrieval_artifacts",
                params={**params, "replace_by_related": 0},
            )
        )

        biblio_item = _matching_data_item(
            biblio_payload,
            patent_id=patent_id,
            publication_number=record.publication_number,
            endpoint="P012",
        )
        claims_item = _matching_data_item(
            claims_payload,
            patent_id=patent_id,
            publication_number=record.publication_number,
            endpoint="P018",
            allow_empty=True,
        )
        description_item = _matching_data_item(
            description_payload,
            patent_id=patent_id,
            publication_number=record.publication_number,
            endpoint="P019",
            allow_empty=True,
        )
        _reject_related_replacement(biblio_item, endpoint="P012")
        _reject_related_replacement(claims_item, endpoint="P018")
        _reject_related_replacement(description_item, endpoint="P019")
        biblio = biblio_item.get("bibliographic_data")
        if not isinstance(biblio, Mapping):
            raise ProviderContentError("智慧芽 P012 缺少 bibliographic_data")

        preferred = record.language or (
            "cn" if str(record.authority or "").upper() == "CN" else "en"
        )
        title, title_language = _select_language_text(
            biblio.get("invention_title"),
            preferred=preferred,
            text_key="text",
        )
        abstract, abstract_language = _select_language_text(
            biblio.get("abstracts"),
            preferred=preferred,
            text_key="text",
        )
        claims_html, claims_language = _select_language_text(
            claims_item.get("claims"),
            preferred=preferred,
            text_key="claim_text",
        )
        description_html, description_language = _select_language_text(
            description_item.get("description"),
            preferred=preferred,
            text_key="text",
        )
        claims = _html_text(claims_html)
        description = _html_text(description_html)
        abstract = _html_text(abstract)

        publication_reference = biblio.get("publication_reference")
        if not isinstance(publication_reference, Mapping):
            publication_reference = {}
        application_reference = biblio.get("application_reference")
        if not isinstance(application_reference, Mapping):
            application_reference = {}
        priority_claims = biblio.get("priority_claims")
        if priority_claims is None:
            priority_claims = []
        if not isinstance(priority_claims, list):
            raise ProviderContentError("智慧芽 P012 priority_claims 不是数组")
        priority_dates = sorted(
            value
            for item in priority_claims
            if isinstance(item, Mapping)
            if (value := _patsnap_date(item.get("date"))) is not None
        )
        frozen_publication_date = _patsnap_date(publication_reference.get("date"))
        frozen_filing_date = _patsnap_date(application_reference.get("date"))
        frozen_priority_date = priority_dates[0] if priority_dates else None
        date_conflicts = _date_conflicts(
            record,
            publication_date=frozen_publication_date,
            filing_date=frozen_filing_date,
            priority_date=frozen_priority_date,
        )
        publication_number = _normalize_publication_number(
            biblio_item.get("pn") or record.publication_number
        )
        if not publication_number:
            raise ProviderContentError("智慧芽 P012 缺少公开编号")
        frozen_patent_id = _validated_patent_id(
            biblio_item.get("patent_id") or patent_id
        )
        if not frozen_patent_id:
            raise ProviderContentError("智慧芽 P012 缺少有效 patent_id")
        for endpoint, item in (
            ("P018", claims_item),
            ("P019", description_item),
        ):
            if item:
                _assert_mapping_identity(
                    item,
                    patent_id=frozen_patent_id,
                    publication_number=publication_number,
                    endpoint=endpoint,
                )
        authority = str(publication_reference.get("country") or "").strip().upper()
        if not authority:
            authority = str(record.authority or publication_number[:2]).upper()

        responses = {
            "bibliography": biblio_raw,
            "claims": claims_raw,
            "description": description_raw,
        }
        response_hashes = {name: _sha256(value) for name, value in responses.items()}
        has_retrieved_content = bool(claims or description)
        primary_artifact = (
            RetrievedArtifact(
                provider=self.provider_name,
                kind="source_bundle",
                source_url=self._endpoint(PATSNAP_BIBLIOGRAPHY_PATH),
                media_type="application/vnd.patsnap.bundle+json",
                content=_response_bundle(responses),
            )
            if has_retrieved_content
            else None
        )
        language = next(
            (
                str(item).lower()
                for item in (
                    claims_language,
                    description_language,
                    title_language,
                    abstract_language,
                )
                if item
            ),
            record.language,
        )
        full_text = "\n\n".join(
            part
            for part in (
                title or record.title,
                f"Abstract\n{abstract}" if abstract else None,
                f"Description\n{description}" if description else None,
                f"Claims\n{claims}" if claims else None,
            )
            if part
        )
        public_date_evidence = (
            "Patsnap P012 bibliographic_data.publication_reference.date; "
            f"response_sha256={response_hashes['bibliography']}"
            if frozen_publication_date and "publication_date" not in date_conflicts
            else None
        )
        provenance = {
            **dict(record.provenance),
            "detail_endpoints": {
                "bibliography": self._endpoint(PATSNAP_BIBLIOGRAPHY_PATH),
                "claims": self._endpoint(PATSNAP_CLAIMS_PATH),
                "description": self._endpoint(PATSNAP_DESCRIPTION_PATH),
            },
            "detail_response_hashes": response_hashes,
            "detail_response_bytes": {
                name: len(value) for name, value in responses.items()
            },
            "detail_request_attempts": {
                "bibliography": biblio_audit["request_attempts"],
                "claims": claims_audit["request_attempts"],
                "description": description_audit["request_attempts"],
            },
            "detail_request_attempt_log": {
                "bibliography": list(
                    biblio_audit.get("request_attempt_log") or ()
                ),
                "claims": list(claims_audit.get("request_attempt_log") or ()),
                "description": list(
                    description_audit.get("request_attempt_log") or ()
                ),
            },
            "date_evidence": {
                "publication_date": {
                    "value": frozen_publication_date,
                    "source": "P012 bibliographic_data.publication_reference.date",
                    "response_sha256": response_hashes["bibliography"],
                },
                "filing_date": {
                    "value": frozen_filing_date,
                    "source": "P012 bibliographic_data.application_reference.date",
                    "response_sha256": response_hashes["bibliography"],
                },
                "priority_date": {
                    "value": frozen_priority_date,
                    "source": "P012 bibliographic_data.priority_claims[].date (earliest)",
                    "response_sha256": response_hashes["bibliography"],
                },
            },
            "public_date_evidence": public_date_evidence,
            "publication_date_verified": bool(frozen_publication_date)
            and "publication_date" not in date_conflicts,
            "filing_date_verified": bool(frozen_filing_date)
            and "filing_date" not in date_conflicts,
            "priority_date_verified": bool(frozen_priority_date)
            and "priority_date" not in date_conflicts,
            "date_conflicts": date_conflicts,
            "replace_by_related": 0,
            "related_publication_reported": {
                "claims": str(claims_item.get("pn_related") or "") or None,
                "description": str(description_item.get("pn_related") or "") or None,
            },
            "capabilities": dict(self.capabilities),
        }
        common = {
            "title": title or record.title,
            "publication_number": publication_number,
            "authority": authority,
            "language": language,
            "publication_date": frozen_publication_date or record.publication_date,
            "filing_date": frozen_filing_date or record.filing_date,
            "priority_date": frozen_priority_date or record.priority_date,
            "abstract": abstract or record.abstract,
            "snippet": abstract or record.snippet,
            "raw_metadata": {
                **dict(record.raw_metadata),
                "patent_id": frozen_patent_id,
                "application_number": str(
                    application_reference.get("doc_number") or ""
                ).strip()
                or record.raw_metadata.get("application_number"),
            },
            "provenance": provenance,
        }
        if not has_retrieved_content:
            return replace(
                record,
                **common,
                pdf_url=None,
                image_urls=(),
                qualification_issues=(
                    "patsnap_fulltext_not_available",
                    *(
                        f"patsnap_{field}_conflict"
                        for field in sorted(date_conflicts)
                    ),
                ),
            )

        assert primary_artifact is not None
        return replace(
            record,
            **common,
            stage=EvidenceStage.RETRIEVED,
            description=description or None,
            claims=claims or None,
            full_text=full_text,
            # P020/P042 are separate, permissioned calls.  A retrieved text
            # bundle must not pretend those media routes have been verified.
            pdf_url=None,
            image_urls=(),
            content_sha256=primary_artifact.content_sha256,
            retrieved_at=primary_artifact.retrieved_at,
            artifacts=(*record.artifacts, primary_artifact.model_dump()),
            qualification_issues=tuple(
                f"patsnap_{field}_conflict" for field in sorted(date_conflicts)
            ),
            primary_artifact=primary_artifact,
        )

    def download_image(
        self,
        record_or_url: EvidenceRecord | str,
        *,
        index: int = 0,
    ) -> RetrievedArtifact:
        self._last_media_artifacts = ()
        if index < 0 or index > 99:
            raise ProviderContentError("智慧芽附图序号必须在 0..99 之间")
        patent_id = _media_patent_id(record_or_url)
        paths = self._image_path_cache.get(patent_id)
        if paths is None:
            payload, raw_metadata, metadata_audit = self._tracked_request_json(
                "POST",
                PATSNAP_FULLTEXT_IMAGE_PATH,
                kind="provider_image_metadata_response",
                tracking_attribute="_last_media_artifacts",
                payload={"patent_id": patent_id, "offset": 0, "limit": 100},
            )
            data = payload.get("data")
            if not isinstance(data, Mapping):
                raise ProviderContentError("智慧芽 P042 响应缺少 data 对象")
            _assert_mapping_identity(
                data,
                patent_id=patent_id,
                publication_number=(
                    record_or_url.publication_number
                    if isinstance(record_or_url, EvidenceRecord)
                    else None
                ),
                endpoint="P042",
            )
            _reject_related_replacement(data, endpoint="P042")
            metadata_artifact = RetrievedArtifact(
                provider=self.provider_name,
                kind="provider_image_metadata_response",
                source_url=self._endpoint(PATSNAP_FULLTEXT_IMAGE_PATH),
                media_type="application/json",
                content=raw_metadata,
                request_attempt_log=tuple(
                    metadata_audit.get("request_attempt_log") or ()
                ),
            )
            images = data.get("fulltext_image")
            if not isinstance(images, list):
                raise ProviderContentError("智慧芽 P042 响应缺少 fulltext_image 数组")
            collected: list[str] = []
            for item in images:
                if not isinstance(item, Mapping):
                    raise ProviderContentError("智慧芽 P042 附图项不是对象")
                path = _validated_signed_download_url(item.get("path"))
                if path:
                    collected.append(path)
            paths = tuple(dict.fromkeys(collected))
            self._image_path_cache[patent_id] = paths
            self._image_metadata_cache[patent_id] = metadata_artifact
        elif patent_id in self._image_metadata_cache:
            self._last_media_artifacts = (self._image_metadata_cache[patent_id],)
        if index >= len(paths):
            raise ProviderContentError("智慧芽文献没有指定序号的全文附图")
        signed_url = paths[index]
        content, media_type, status_code, binary_audit = self._request_signed_binary(
            signed_url,
            accept="image/png,image/jpeg,image/webp",
            operation="P042_signed_image_download",
            max_response_bytes=self.max_image_response_bytes,
        )
        try:
            if status_code < 200 or status_code >= 300:
                binary_audit = {**binary_audit, "outcome": "http_error"}
                raise ProviderContentError(f"智慧芽 P042 图片下载 HTTP {status_code}")
            if media_type.lower() not in {"image/png", "image/jpeg", "image/webp"}:
                binary_audit = {**binary_audit, "outcome": "invalid_mime"}
                raise ProviderContentError("智慧芽 P042 图片下载 MIME 无效")
            _validate_raster_image(
                content,
                media_type,
                max_pixels=self.max_image_pixels,
            )
        except ProviderContentError:
            if binary_audit.get("outcome") == "response_received":
                binary_audit = {**binary_audit, "outcome": "invalid_content"}
            _append_tracked_artifact(
                self,
                "_last_media_artifacts",
                _binary_error_artifact(
                    source_url=signed_url,
                    content=content,
                    media_type=media_type,
                    request_attempt_log=(binary_audit,),
                ),
            )
            raise
        binary_audit = {**binary_audit, "outcome": "success"}
        metadata_attempt_log = tuple(
            (
                self._image_metadata_cache[patent_id].request_attempt_log
                if patent_id in self._image_metadata_cache
                else ()
            )
        )
        return RetrievedArtifact(
            provider=self.provider_name,
            kind="image",
            source_url=_redacted_download_url(signed_url),
            media_type=media_type,
            content=content,
            source_metadata_artifact=self._image_metadata_cache.get(patent_id),
            request_attempt_log=(*metadata_attempt_log, binary_audit),
        )

    def download_pdf(self, record_or_url: EvidenceRecord | str) -> RetrievedArtifact:
        self._last_media_artifacts = ()
        patent_id = _media_patent_id(record_or_url)
        payload, raw_metadata, metadata_audit = self._tracked_request_json(
            "GET",
            PATSNAP_PDF_PATH,
            kind="provider_pdf_metadata_response",
            tracking_attribute="_last_media_artifacts",
            params={"patent_id": patent_id, "replace_by_related": 0},
        )
        item = _matching_data_item(
            payload,
            patent_id=patent_id,
            publication_number=(
                record_or_url.publication_number
                if isinstance(record_or_url, EvidenceRecord)
                else None
            ),
            endpoint="P020",
        )
        if str(item.get("pn_related") or "").strip():
            raise ProviderContentError("智慧芽 P020 返回了未授权的同族替代 PDF")
        pdf = item.get("pdf")
        if not isinstance(pdf, Mapping):
            raise ProviderContentError("智慧芽 P020 响应缺少 pdf 对象")
        signed_url = _validated_signed_download_url(pdf.get("path"))
        if not signed_url:
            raise ProviderContentError("智慧芽 P020 响应缺少 PDF 路径")
        content, media_type, status_code, binary_audit = self._request_signed_binary(
            signed_url,
            accept="application/pdf",
            operation="P020_signed_pdf_download",
        )
        try:
            if status_code < 200 or status_code >= 300:
                binary_audit = {**binary_audit, "outcome": "http_error"}
                raise ProviderContentError(f"智慧芽 P020 PDF 下载 HTTP {status_code}")
            if media_type.lower() != "application/pdf":
                binary_audit = {**binary_audit, "outcome": "invalid_mime"}
                raise ProviderContentError("智慧芽 P020 PDF 下载 MIME 无效")
            if not content.lstrip().startswith(b"%PDF"):
                binary_audit = {**binary_audit, "outcome": "invalid_content"}
                raise ProviderContentError("智慧芽 P020 下载内容不是真实 PDF")
            try:
                document = fitz.open(stream=content, filetype="pdf")
            except Exception as exc:
                binary_audit = {**binary_audit, "outcome": "invalid_content"}
                raise ProviderContentError("智慧芽 P020 PDF 无法解析") from exc
            try:
                if document.page_count < 1:
                    binary_audit = {**binary_audit, "outcome": "invalid_content"}
                    raise ProviderContentError("智慧芽 P020 PDF 没有可读取页面")
            finally:
                document.close()
        except ProviderContentError:
            _append_tracked_artifact(
                self,
                "_last_media_artifacts",
                _binary_error_artifact(
                    source_url=signed_url,
                    content=content,
                    media_type=media_type,
                    request_attempt_log=(binary_audit,),
                ),
            )
            raise
        binary_audit = {**binary_audit, "outcome": "success"}
        metadata_artifact = RetrievedArtifact(
            provider=self.provider_name,
            kind="provider_pdf_metadata_response",
            source_url=self._endpoint(PATSNAP_PDF_PATH),
            media_type="application/json",
            content=raw_metadata,
            request_attempt_log=tuple(metadata_audit.get("request_attempt_log") or ()),
        )
        return RetrievedArtifact(
            provider=self.provider_name,
            kind="pdf",
            source_url=_redacted_download_url(signed_url),
            media_type="application/pdf",
            content=content,
            source_metadata_artifact=metadata_artifact,
            request_attempt_log=(
                *metadata_artifact.request_attempt_log,
                binary_audit,
            ),
        )

    def qualify(
        self,
        record: EvidenceRecord,
        *,
        critical_date: date | str,
        content_relevance_verified: bool,
        source_chain_verified: bool = True,
        public_date_evidence: str | None = None,
        cn_application_scope: bool | None = None,
        public_date_verified: bool = True,
        candidate_filing_date_verified: bool = True,
        candidate_priority_date_verified: bool = True,
        target_publication_date: date | str | None = None,
        target_publication_date_verified: bool = False,
        date_channel: str = "ordinary_prior_art",
        strict: bool = False,
    ) -> EvidenceRecord:
        eligibility = classify_date_eligibility(
            critical_date=critical_date,
            publication_date=record.publication_date,
            candidate_filing_date=record.filing_date,
            candidate_priority_date=record.priority_date,
            target_publication_date=target_publication_date,
            source_type=record.source_type,
            authority=record.authority,
            cn_application_scope=cn_application_scope,
            public_date_verified=public_date_verified,
            candidate_filing_date_verified=candidate_filing_date_verified,
            candidate_priority_date_verified=candidate_priority_date_verified,
            target_publication_date_verified=target_publication_date_verified,
            date_channel=date_channel,
        )
        qualified = qualify_evidence(
            record,
            date_eligibility=eligibility,
            content_relevance_verified=content_relevance_verified,
            source_chain_verified=source_chain_verified,
            public_date_evidence=public_date_evidence,
            strict=strict,
        )
        persistent_issues = tuple(
            issue
            for issue in record.qualification_issues
            if issue.startswith("patsnap_")
        )
        if not persistent_issues:
            return qualified
        if strict:
            raise ProviderContentError(
                "智慧芽证据仍有未解决缺口: " + ", ".join(persistent_issues)
            )
        return replace(
            qualified,
            stage=(
                EvidenceStage.RETRIEVED
                if record.stage is not EvidenceStage.LEAD
                else EvidenceStage.LEAD
            ),
            qualification_issues=tuple(
                dict.fromkeys((*persistent_issues, *qualified.qualification_issues))
            ),
        )

    def _coerce_lead(
        self,
        value: EvidenceRecord | Mapping[str, Any] | str,
    ) -> EvidenceRecord:
        if isinstance(value, EvidenceRecord):
            return value
        if isinstance(value, Mapping):
            external_id = str(
                value.get("external_id") or value.get("patent_id") or ""
            ).strip()
            publication_number = _normalize_publication_number(
                value.get("publication_number") or value.get("pn")
            )
            title = str(value.get("title") or "").strip()
        else:
            external_id = str(value or "").strip()
            publication_number = _normalize_publication_number(value)
            title = ""
        if not external_id and not publication_number:
            raise ValueError("智慧芽 lead 必须包含 patent_id 或公开编号")
        return EvidenceRecord(
            provider=self.provider_name,
            source_type="patent",
            external_id=external_id or str(publication_number),
            title=title or external_id or str(publication_number),
            source_url=self._endpoint(PATSNAP_BIBLIOGRAPHY_PATH),
            publication_number=publication_number,
            authority=(publication_number[:2] if publication_number else None),
            raw_metadata={
                "patent_id": external_id if _valid_patent_id(external_id) else None
            },
        )

    def _endpoint(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _tracked_request_json(
        self,
        method: str,
        path: str,
        *,
        kind: str,
        tracking_attribute: str,
        payload: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[Mapping[str, Any], bytes, Mapping[str, Any]]:
        """Track every bounded API response before endpoint schema validation."""

        try:
            response, raw, audit = self._request_json(
                method,
                path,
                payload=payload,
                params=params,
            )
        except PatsnapApiError as exc:
            if exc.raw_response_artifact is not None:
                _append_tracked_artifact(
                    self,
                    tracking_attribute,
                    exc.raw_response_artifact,
                )
            raise
        artifact = RetrievedArtifact(
            provider=self.provider_name,
            kind=kind,
            source_url=self._endpoint(path),
            media_type="application/json",
            content=raw,
            request_attempt_log=tuple(audit.get("request_attempt_log") or ()),
        )
        _append_tracked_artifact(self, tracking_attribute, artifact)
        return response, raw, audit

    def _request_signed_binary(
        self,
        signed_url: str,
        *,
        accept: str,
        operation: str,
        max_response_bytes: int | None = None,
    ) -> tuple[bytes, str, int, Mapping[str, Any]]:
        """Fetch one signed media URL with a separate, non-retried audit row."""

        request_id = uuid.uuid4().hex
        try:
            response = self._get(
                signed_url,
                headers={"Accept": accept},
                max_response_bytes=max_response_bytes,
                allow_error_status=True,
            )
        except (ProviderRequestError, ProviderContentError) as exc:
            audit = {
                **_attempt_audit(
                    attempt=1,
                    request_id=request_id,
                    status_code=_status_from_error(exc),
                    error_code=None,
                    correlation_id=None,
                    openapi_amount=None,
                    outcome="transport_or_bounds_error",
                    retry_scheduled=False,
                ),
                "operation": operation,
            }
            _append_tracked_artifact(
                self,
                "_last_media_artifacts",
                _binary_error_artifact(
                    source_url=signed_url,
                    content=b"",
                    media_type="application/octet-stream",
                    request_attempt_log=(audit,),
                ),
            )
            raise
        content = _response_content(response)
        media_type = _response_header(response, "content-type").split(";", 1)[0]
        status_code = int(getattr(response, "status_code", 0) or 0)
        audit = {
            **_attempt_audit(
                attempt=1,
                request_id=request_id,
                status_code=status_code,
                error_code=None,
                correlation_id=_safe_header_value(
                    _response_header(response, "x-correlation-id")
                ),
                openapi_amount=_safe_amount(
                    _response_header(response, "x-openapi-amount")
                ),
                outcome="response_received",
                retry_scheduled=False,
            ),
            "operation": operation,
        }
        return content, media_type, status_code, audit

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> tuple[Mapping[str, Any], bytes, Mapping[str, Any]]:
        endpoint = self._endpoint(path)
        base_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if method == "POST":
            base_headers["Content-Type"] = "application/json"

        last_error: PatsnapApiError | None = None
        attempt_log: list[dict[str, Any]] = []
        for attempt in range(1, self.retry_attempts + 1):
            request_id = uuid.uuid4().hex
            headers = {**base_headers, "X-Request-ID": request_id}
            try:
                if method == "POST":
                    response = self._post_json(
                        endpoint,
                        payload=dict(payload or {}),
                        headers=headers,
                        allow_error_status=True,
                    )
                elif method == "GET":
                    response = self._get(
                        endpoint,
                        params=params,
                        headers=headers,
                        # The adapter must inspect and retain bounded provider
                        # error/non-JSON bodies before classifying the failure.
                        allow_error_status=True,
                    )
                else:
                    raise ValueError("智慧芽 adapter 只支持 GET/POST")
            except ProviderRequestError as exc:
                status = _status_from_error(exc)
                # A POST may already have reached the metered provider when the
                # response is lost.  Without an idempotency contract, never
                # repeat it merely because transport/HTTP failed.
                retryable = method == "GET" and (
                    status is None or status == 429 or status >= 500
                )
                attempt_log.append(
                    {
                        "attempt": attempt,
                        "request_id": request_id,
                        "status_code": status,
                        "error_code": None,
                        "correlation_id": None,
                        "openapi_amount": None,
                        "outcome": "transport_error",
                        "retry_scheduled": bool(
                            retryable and attempt < self.retry_attempts
                        ),
                    }
                )
                safe = PatsnapApiError(
                    _safe_api_error_message(status_code=status),
                    status_code=status,
                    retryable=retryable,
                    request_attempt_log=attempt_log,
                    raw_response_artifact=_raw_response_artifact(
                        endpoint=endpoint,
                        raw=b"",
                        media_type="application/octet-stream",
                        attempt_log=attempt_log,
                    ),
                )
                if retryable and attempt < self.retry_attempts:
                    self._sleep(_retry_delay(self.retry_base_seconds, attempt))
                    last_error = safe
                    continue
                raise safe from exc

            raw = _response_content(response)
            status_code = int(getattr(response, "status_code", 0) or 0)
            media_type = _response_header(response, "content-type").split(";", 1)[0]
            correlation_id = _safe_header_value(
                _response_header(response, "x-correlation-id")
            )
            openapi_amount = _safe_amount(
                _response_header(response, "x-openapi-amount")
            )
            decoded: Mapping[str, Any] | None = None
            try:
                candidate = json.loads(raw.decode("utf-8"))
                if isinstance(candidate, Mapping):
                    decoded = candidate
            except (UnicodeDecodeError, json.JSONDecodeError):
                decoded = None

            if status_code < 200 or status_code >= 300:
                error_code = _optional_error_code(decoded)
                retryable = status_code not in {401, 403} and (
                    (method == "GET" and (status_code == 429 or status_code >= 500))
                    or error_code in _TRANSIENT_BUSINESS_CODES
                )
                attempt_log.append(
                    _attempt_audit(
                        attempt=attempt,
                        request_id=request_id,
                        status_code=status_code,
                        error_code=error_code,
                        correlation_id=correlation_id,
                        openapi_amount=openapi_amount,
                        outcome="http_error",
                        retry_scheduled=retryable and attempt < self.retry_attempts,
                    )
                )
                safe = PatsnapApiError(
                    _safe_api_error_message(
                        status_code=status_code,
                        error_code=error_code,
                        correlation_id=correlation_id,
                    ),
                    status_code=status_code,
                    error_code=error_code,
                    correlation_id=correlation_id,
                    retryable=retryable,
                    request_attempt_log=attempt_log,
                    raw_response_artifact=_raw_response_artifact(
                        endpoint=endpoint,
                        raw=raw,
                        media_type=media_type,
                        attempt_log=attempt_log,
                    ),
                )
                if retryable and attempt < self.retry_attempts:
                    self._sleep(_retry_delay(self.retry_base_seconds, attempt))
                    last_error = safe
                    continue
                raise safe

            if media_type.lower() not in {"application/json", "text/json"}:
                attempt_log.append(
                    _attempt_audit(
                        attempt=attempt,
                        request_id=request_id,
                        status_code=status_code,
                        error_code=None,
                        correlation_id=correlation_id,
                        openapi_amount=openapi_amount,
                        outcome="invalid_mime",
                        retry_scheduled=False,
                    )
                )
                raise PatsnapApiError(
                    "智慧芽 API 成功响应 MIME 不是 JSON",
                    status_code=status_code,
                    correlation_id=correlation_id,
                    retryable=False,
                    request_attempt_log=attempt_log,
                    raw_response_artifact=_raw_response_artifact(
                        endpoint=endpoint,
                        raw=raw,
                        media_type=media_type,
                        attempt_log=attempt_log,
                    ),
                )
            if decoded is None:
                attempt_log.append(
                    _attempt_audit(
                        attempt=attempt,
                        request_id=request_id,
                        status_code=status_code,
                        error_code=None,
                        correlation_id=correlation_id,
                        openapi_amount=openapi_amount,
                        outcome="invalid_json",
                        retry_scheduled=False,
                    )
                )
                raise PatsnapApiError(
                    "智慧芽 API 未返回有效 JSON 对象",
                    status_code=status_code,
                    correlation_id=correlation_id,
                    retryable=False,
                    request_attempt_log=attempt_log,
                    raw_response_artifact=_raw_response_artifact(
                        endpoint=endpoint,
                        raw=raw,
                        media_type=media_type,
                        attempt_log=attempt_log,
                    ),
                )
            api_status = decoded.get("status")
            error_code = _optional_error_code(decoded)
            if api_status is not True or error_code != 0:
                retryable = error_code in _TRANSIENT_BUSINESS_CODES
                attempt_log.append(
                    _attempt_audit(
                        attempt=attempt,
                        request_id=request_id,
                        status_code=status_code,
                        error_code=error_code,
                        correlation_id=correlation_id,
                        openapi_amount=openapi_amount,
                        outcome="provider_error",
                        retry_scheduled=retryable and attempt < self.retry_attempts,
                    )
                )
                safe = PatsnapApiError(
                    _safe_api_error_message(
                        status_code=status_code,
                        error_code=error_code,
                        correlation_id=correlation_id,
                    ),
                    status_code=status_code,
                    error_code=error_code,
                    correlation_id=correlation_id,
                    retryable=retryable,
                    request_attempt_log=attempt_log,
                    raw_response_artifact=_raw_response_artifact(
                        endpoint=endpoint,
                        raw=raw,
                        media_type=media_type,
                        attempt_log=attempt_log,
                    ),
                )
                if retryable and attempt < self.retry_attempts:
                    self._sleep(_retry_delay(self.retry_base_seconds, attempt))
                    last_error = safe
                    continue
                raise safe
            attempt_log.append(
                _attempt_audit(
                    attempt=attempt,
                    request_id=request_id,
                    status_code=status_code,
                    error_code=0,
                    correlation_id=correlation_id,
                    openapi_amount=openapi_amount,
                    outcome="success",
                    retry_scheduled=False,
                )
            )
            return decoded, raw, {
                "request_attempts": attempt,
                "correlation_id": correlation_id,
                "openapi_amount": openapi_amount,
                "request_attempt_log": tuple(attempt_log),
            }

        assert last_error is not None
        raise last_error


def _validated_api_key(value: Any) -> str:
    key = str(value or "").strip()
    if (
        len(key) < 16
        or len(key) > 4096
        or not key.startswith("sk-")
        or any(character.isspace() for character in key)
        or any(marker in key.upper() for marker in ("REPLACE_", "CHANGE_ME"))
    ):
        raise ValueError("智慧芽 API Key 必须是当前环境有效的 sk- 前缀密钥")
    return key


def _validated_base_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if (
        raw != PATSNAP_BASE_URL
        or parsed.scheme != "https"
        or parsed.hostname != "connect.zhihuiya.com"
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"智慧芽 base_url 必须严格等于 {PATSNAP_BASE_URL}")
    return raw


def _validated_endpoint_path(
    value: Any,
    *,
    allowed: frozenset[str],
    name: str,
) -> str:
    raw = str(value or "").strip()
    if raw not in allowed:
        raise ValueError(f"{name} 不在官方候选路径白名单")
    return raw


def _patsnap_query_text(
    query: SearchQuery,
    *,
    country: str | None,
    server_before: date | str | None,
    server_after: date | str | None,
    filing_before: date | str | None,
    max_length: int,
) -> tuple[str, Mapping[str, str | None]]:
    text = " ".join(str(query.text or "").split())
    if not text:
        raise ProviderContentError("智慧芽检索式为空")
    if _FIELD_EXPRESSION.search(text):
        base = text
    elif re.fullmatch(r"[A-Za-z]{2}[A-Za-z0-9./-]{4,}", text):
        publication_number = _normalize_publication_number(text)
        base = f"PN:{publication_number}" if publication_number else f"TACD: ({text})"
    else:
        base = f"TACD: ({_explicit_natural_language_terms(text)})"

    before = _required_date_or_none(server_before, name="server_before")
    after = _required_date_or_none(server_after, name="server_after")
    filing = _required_date_or_none(filing_before, name="filing_before")
    country_code = str(country or "").strip().upper() or None
    if country_code and not re.fullmatch(r"[A-Z]{2}", country_code):
        raise ValueError("country 必须是两位国家/地区代码")

    clauses = [f"({base})"]
    if country_code:
        clauses.append(f"AUTHORITY:({country_code})")
    if before or after:
        lower = (after + timedelta(days=1)).strftime("%Y%m%d") if after else "*"
        upper = (before - timedelta(days=1)).strftime("%Y%m%d") if before else "*"
        if lower != "*" and upper != "*" and lower > upper:
            raise ValueError("server_after 与 server_before 之间没有可检索日期")
        clauses.append(f"PBD:[{lower} TO {upper}]")
    if filing:
        upper = (filing - timedelta(days=1)).strftime("%Y%m%d")
        clauses.append(
            f"(APD:[* TO {upper}] OR PRIORITY_DATE:[* TO {upper}])"
        )
    expression = " AND ".join(clauses)
    if len(expression) > max_length:
        raise ProviderContentError(f"智慧芽检索式超过 {max_length} 字符")
    return expression, {
        "country": country_code,
        "server_before": before.isoformat() if before else None,
        "server_after": after.isoformat() if after else None,
        "filing_before": filing.isoformat() if filing else None,
    }


def _required_date_or_none(value: date | str | None, *, name: str) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError(f"{name} 不是有效日期")
    return parsed


def _explicit_natural_language_terms(value: str) -> str:
    """Turn an unfielded planner expression into an explicit AND query.

    Patsnap accepts raw spaces, but the account's Analytics default Boolean
    setting is not part of the REST contract.  Explicit AND prevents a single
    broad feature from silently becoming an OR-style global search.
    """

    if re.search(r"\b(?:AND|OR|NOT)\b|\$(?:W|PRE|WS|SEN|PARA|FREQ)\d*", value, re.I):
        return value
    atoms = re.findall(r"[\u3400-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9._-]+", value)
    deduplicated: list[str] = []
    seen: set[str] = set()
    for atom in atoms:
        normalized = atom.casefold()
        if normalized in {"and", "or", "not", "the", "of", "for", "with"}:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        deduplicated.append(atom)
    if len(deduplicated) < 2:
        raise ProviderContentError("智慧芽自然语言检索式缺少主题+特征组合")
    return " AND ".join(deduplicated[:24])


def _assert_search_row_matches_filters(
    *,
    authority: str | None,
    publication_date: str | None,
    filters: Mapping[str, str | None],
) -> None:
    expected_country = filters.get("country")
    if expected_country and authority and authority != expected_country:
        raise ProviderContentError("智慧芽 P002 返回了国家过滤范围外的文献")
    parsed_publication = parse_date(publication_date)
    before = parse_date(filters.get("server_before"))
    after = parse_date(filters.get("server_after"))
    if parsed_publication and before and parsed_publication >= before:
        raise ProviderContentError("智慧芽 P002 返回了截止日之后的文献")
    if parsed_publication and after and parsed_publication <= after:
        raise ProviderContentError("智慧芽 P002 返回了起始日之前的文献")


def _patsnap_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    raw = re.sub(r"[^0-9]", "", str(value))
    if len(raw) != 8:
        raise ProviderContentError("智慧芽日期字段不是 8 位日期")
    try:
        parsed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError as exc:
        raise ProviderContentError("智慧芽日期字段无效") from exc
    return parsed.isoformat()


def _bounded_int(
    value: Any,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} 必须在 {minimum}..{maximum} 之间")
    return value


def _image_model_and_patent_type(
    *,
    patent_type: Any,
    model: Any,
    operation: str,
) -> tuple[str, int]:
    if not isinstance(patent_type, str):
        raise ValueError("patent_type 必须是 D 或 U")
    normalized_type = patent_type.strip().upper()
    if normalized_type not in {"D", "U"}:
        raise ValueError("patent_type 必须是 D 或 U")
    normalized_model = _bounded_int(
        model,
        name="model",
        minimum=1,
        maximum=4,
    )
    allowed_models = (
        {"D": {1, 2}}
        if operation == "P060_beta"
        else {"D": {1, 2}, "U": {3, 4}}
    )
    if normalized_type not in allowed_models:
        raise ValueError(f"{operation} 只支持 patent_type=D")
    if normalized_model not in allowed_models[normalized_type]:
        raise ValueError(
            f"patent_type={normalized_type} 与 model={normalized_model} 不兼容"
        )
    return normalized_type, normalized_model


def _validated_https_image_url(value: Any, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} 必须是 HTTPS 图片 URL")
    raw = value.strip()
    if (
        not raw
        or len(raw) > 4096
        or any(character.isspace() for character in raw)
    ):
        raise ValueError(f"{name} 必须是 HTTPS 图片 URL")
    parsed = urlparse(raw)
    hostname = str(parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or not parsed.path
        or parsed.fragment
        or hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
        or hostname.endswith(".internal")
    ):
        raise ValueError(f"{name} 必须是可公开访问的 HTTPS 图片 URL")
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not literal_ip.is_global:
        raise ValueError(f"{name} 不得指向本机、私网或保留地址")
    return raw


def _required_text(value: Any, *, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ProviderContentError(f"智慧芽 {name} 不是字符串")
    raw = value.strip()
    if not raw or len(raw) > maximum or "\x00" in raw:
        raise ProviderContentError(f"智慧芽 {name} 无效")
    return raw


def _optional_text(value: Any, *, name: str, maximum: int) -> str | None:
    if value is None or value == "":
        return None
    return _required_text(value, name=name, maximum=maximum)


def _image_similarity_score(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderContentError(f"智慧芽 {name} 不是有效相似度")
    score = float(value)
    if not math.isfinite(score) or score < 0 or score > 1:
        raise ProviderContentError(f"智慧芽 {name} 必须在 0..1 之间")
    return score


def _image_result_authority(
    value: Any,
    *,
    publication_number: str,
    operation: str,
) -> str | None:
    pn_match = re.match(r"^([A-Z]{2})", publication_number)
    pn_authority = pn_match.group(1) if pn_match else None
    if value is None or value == "":
        return pn_authority
    if not isinstance(value, str):
        raise ProviderContentError(f"智慧芽 {operation} authority 不是字符串")
    authority = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", authority):
        raise ProviderContentError(f"智慧芽 {operation} authority 无效")
    if pn_authority and authority != pn_authority:
        raise ProviderContentError(
            f"智慧芽 {operation} authority 与 patent_pn 冲突"
        )
    return authority


def _loc_classifications(value: Any, *, operation: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ProviderContentError(f"智慧芽 {operation} loc 不是数组")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ProviderContentError(
                f"智慧芽 {operation} loc 含非字符串元素"
            )
        normalized = item.strip().upper()
        if (
            not normalized
            or len(normalized) > 32
            or not re.fullmatch(r"[A-Z0-9.-]+", normalized)
        ):
            raise ProviderContentError(f"智慧芽 {operation} loc 分类号无效")
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _nonnegative_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ProviderContentError(f"智慧芽 {name} 不是非负整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProviderContentError(f"智慧芽 {name} 不是非负整数") from exc
    if parsed < 0:
        raise ProviderContentError(f"智慧芽 {name} 不是非负整数")
    return parsed


def _valid_patent_id(value: Any) -> bool:
    return bool(_PATENT_ID.fullmatch(str(value or "").strip()))


def _validated_patent_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return raw if _valid_patent_id(raw) else None


def _detail_reference(record: EvidenceRecord) -> tuple[str, str, str | None]:
    patent_id = _validated_patent_id(
        record.raw_metadata.get("patent_id") or record.external_id
    )
    if patent_id:
        return "patent_id", patent_id, patent_id
    publication_number = _normalize_publication_number(
        record.publication_number or record.external_id
    )
    if publication_number:
        return "patent_number", publication_number, None
    raise ProviderContentError("智慧芽 lead 缺少有效 patent_id 或公开编号")


def _matching_data_item(
    payload: Mapping[str, Any],
    *,
    patent_id: str | None,
    publication_number: str | None,
    endpoint: str,
    allow_empty: bool = False,
) -> Mapping[str, Any]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ProviderContentError(f"智慧芽 {endpoint} data 不是数组")
    if not data and allow_empty:
        return {}
    if not data:
        raise ProviderContentError(f"智慧芽 {endpoint} 未返回文献")
    wanted_pn = _normalize_publication_number(publication_number)
    for item in data:
        if not isinstance(item, Mapping):
            raise ProviderContentError(f"智慧芽 {endpoint} data 含非对象元素")
        if _mapping_identity_matches(
            item,
            patent_id=patent_id,
            publication_number=wanted_pn,
        ):
            return item
    raise ProviderContentError(f"智慧芽 {endpoint} 没有匹配请求文献的响应")


def _assert_mapping_identity(
    item: Mapping[str, Any],
    *,
    patent_id: str | None,
    publication_number: str | None,
    endpoint: str,
) -> None:
    if _mapping_identity_matches(
        item,
        patent_id=patent_id,
        publication_number=publication_number,
    ):
        return
    raise ProviderContentError(f"智慧芽 {endpoint} 没有匹配请求文献的响应")


def _mapping_identity_matches(
    item: Mapping[str, Any],
    *,
    patent_id: str | None,
    publication_number: str | None,
) -> bool:
    raw_item_id = str(item.get("patent_id") or "").strip()
    item_id = _validated_patent_id(raw_item_id)
    if raw_item_id and not item_id:
        return False
    raw_item_pn = str(item.get("pn") or "").strip()
    item_pn = _normalize_publication_number(raw_item_pn)
    if raw_item_pn and not item_pn:
        return False
    wanted_pn = _normalize_publication_number(publication_number)
    matched = False
    if patent_id and item_id:
        if item_id != patent_id:
            return False
        matched = True
    if wanted_pn and item_pn:
        if item_pn != wanted_pn:
            return False
        matched = True
    return matched


def _reject_related_replacement(item: Mapping[str, Any], *, endpoint: str) -> None:
    if str(item.get("pn_related") or "").strip():
        raise ProviderContentError(f"智慧芽 {endpoint} 返回了未授权的同族替代文献")


def _date_conflicts(
    record: EvidenceRecord,
    *,
    publication_date: str | None,
    filing_date: str | None,
    priority_date: str | None,
) -> dict[str, dict[str, str]]:
    conflicts: dict[str, dict[str, str]] = {}
    for field, lead_value, detail_value in (
        ("publication_date", record.publication_date, publication_date),
        ("filing_date", record.filing_date, filing_date),
        ("priority_date", record.priority_date, priority_date),
    ):
        lead_date = parse_date(lead_value)
        detail_date = parse_date(detail_value)
        if lead_date and detail_date and lead_date != detail_date:
            conflicts[field] = {
                "lead_value": lead_date.isoformat(),
                "detail_value": detail_date.isoformat(),
            }
    return conflicts


def _attempt_audit(
    *,
    attempt: int,
    request_id: str,
    status_code: int | None,
    error_code: int | None,
    correlation_id: str | None,
    openapi_amount: str | None,
    outcome: str,
    retry_scheduled: bool,
) -> dict[str, Any]:
    return {
        "attempt": attempt,
        "request_id": request_id,
        "status_code": status_code,
        "error_code": error_code,
        "correlation_id": correlation_id,
        "openapi_amount": openapi_amount,
        "outcome": outcome,
        "retry_scheduled": retry_scheduled,
    }


def _raw_response_artifact(
    *,
    endpoint: str,
    raw: bytes,
    media_type: str,
    attempt_log: Sequence[Mapping[str, Any]],
) -> RetrievedArtifact:
    """Expose the final failed response only to the restricted artifact path."""

    return RetrievedArtifact(
        provider="patsnap",
        kind="provider_error_response",
        source_url=endpoint,
        media_type=media_type or "application/octet-stream",
        content=raw,
        request_attempt_log=tuple(dict(item) for item in attempt_log),
    )


def _binary_error_artifact(
    *,
    source_url: str,
    content: bytes,
    media_type: str,
    request_attempt_log: Sequence[Mapping[str, Any]] = (),
) -> RetrievedArtifact:
    return RetrievedArtifact(
        provider="patsnap",
        kind="provider_error_response",
        source_url=_redacted_download_url(source_url),
        media_type=media_type or "application/octet-stream",
        content=content,
        request_attempt_log=tuple(dict(item) for item in request_attempt_log),
    )


def _append_tracked_artifact(
    provider: PatsnapProvider,
    attribute: str,
    artifact: RetrievedArtifact,
) -> None:
    current = tuple(getattr(provider, attribute, ()) or ())
    if any(
        item.content_sha256 == artifact.content_sha256
        and item.kind == artifact.kind
        and item.source_url == artifact.source_url
        and item.request_attempt_log == artifact.request_attempt_log
        for item in current
    ):
        return
    setattr(provider, attribute, (*current, artifact))


def _select_language_text(
    values: Any,
    *,
    preferred: str | None,
    text_key: str,
) -> tuple[str | None, str | None]:
    if values is None:
        return None, None
    if not isinstance(values, list):
        raise ProviderContentError("智慧芽多语言文本字段不是数组")
    candidates = [item for item in values if isinstance(item, Mapping)]
    if len(candidates) != len(values):
        raise ProviderContentError("智慧芽多语言文本数组含非对象元素")
    wanted = str(preferred or "").strip().lower()
    aliases = {
        "zh": {"zh", "cn", "zh-cn"},
        "cn": {"zh", "cn", "zh-cn"},
        "en": {"en", "eng"},
        "jp": {"jp", "ja"},
        "ja": {"jp", "ja"},
    }
    preferred_aliases = aliases.get(wanted, {wanted} if wanted else set())
    selected = next(
        (
            item
            for item in candidates
            if str(item.get("lang") or "").strip().lower() in preferred_aliases
            and str(item.get(text_key) or "").strip()
        ),
        None,
    )
    if selected is None:
        selected = next(
            (item for item in candidates if str(item.get(text_key) or "").strip()),
            None,
        )
    if selected is None:
        return None, None
    return (
        str(selected.get(text_key) or "").strip() or None,
        str(selected.get("lang") or "").strip().lower() or None,
    )


def _html_text(value: str | None) -> str | None:
    if not value:
        return None
    soup = BeautifulSoup(value, "lxml")
    lines = [" ".join(item.split()) for item in soup.get_text("\n").splitlines()]
    cleaned = "\n".join(item for item in lines if item).strip()
    return cleaned or None


def _response_bundle(responses: Mapping[str, bytes]) -> bytes:
    payload = {
        "format": "patsnap_rest_response_bundle_v1",
        "responses": {
            name: {
                "sha256": _sha256(content),
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
            for name, content in sorted(responses.items())
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _media_patent_id(value: EvidenceRecord | str) -> str:
    if isinstance(value, EvidenceRecord):
        candidate = value.raw_metadata.get("patent_id") or value.external_id
    else:
        parsed = urlparse(str(value or ""))
        candidate = parsed.netloc if parsed.scheme == "patsnap" else str(value or "")
    patent_id = _validated_patent_id(candidate)
    if not patent_id:
        raise ProviderContentError("智慧芽媒体请求缺少有效 patent_id")
    return patent_id


def _validated_signed_download_url(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    hostname = str(parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or not (
            hostname == "zhihuiya.com"
            or hostname.endswith(".zhihuiya.com")
            or hostname == "patsnap.com"
            or hostname.endswith(".patsnap.com")
        )
    ):
        raise ProviderContentError("智慧芽媒体响应包含非许可 HTTPS 下载地址")
    return raw


def _redacted_download_url(value: str) -> str:
    parsed = urlparse(value)
    return parsed._replace(query="", fragment="").geturl()


def _optional_error_code(payload: Mapping[str, Any] | None) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("error_code")
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _status_from_error(error: Exception) -> int | None:
    match = re.search(r"\bHTTP\s+(\d{3})\b", str(error))
    return int(match.group(1)) if match else None


def _retry_delay(base: float, attempt: int) -> float:
    return min(2.0, base * (2 ** max(0, attempt - 1)))


def _safe_header_value(value: Any) -> str | None:
    raw = str(value or "").strip()
    if raw and len(raw) <= 128 and re.fullmatch(r"[A-Za-z0-9._:-]+", raw):
        return raw
    return None


def _safe_amount(value: Any) -> str | None:
    raw = str(value or "").strip()
    if raw and len(raw) <= 32 and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw):
        return raw
    return None


def _safe_api_error_message(
    *,
    status_code: int | None,
    error_code: int | None = None,
    correlation_id: str | None = None,
) -> str:
    parts = ["智慧芽 REST API 调用失败"]
    if status_code is not None:
        parts.append(f"HTTP={status_code}")
    if error_code is not None:
        parts.append(f"error_code={error_code}")
    if correlation_id:
        parts.append(f"correlation_id={correlation_id}")
    return "；".join(parts)
