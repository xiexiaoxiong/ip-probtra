"""Auditable prior-art provider adapters and evidence-stage primitives.

The adapters intentionally stop short of deciding technical disclosure.  A
search result is a lead, a successfully fetched document is retrieved, and an
explicit local date/content/source review is required for qualified evidence.
Provider-side date filters (including Google Patents ``before``) are discovery
hints only and are never accepted as the final eligibility decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import StrEnum
from functools import lru_cache
import base64
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import time
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
import fitz
import httpx
from lxml import etree

from .date_rules import (
    DateCategory,
    DateEligibilityResult,
    classify_date_eligibility,
    parse_date,
)


DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RESPONSE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_IMAGE_RESPONSE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS = 40_000_000
DEFAULT_MAX_REDIRECTS = 4
GOOGLE_PATENTS_BASE_URL = "https://patents.google.com"
EPO_OPS_BASE_URL = "https://ops.epo.org/3.2"
EPO_OPS_TOKEN_URL = f"{EPO_OPS_BASE_URL}/auth/accesstoken"
ARXIV_EXPORT_BASE_URL = "https://export.arxiv.org"
OPENALEX_API_URL = "https://api.openalex.org/works"
CROSSREF_API_URL = "https://api.crossref.org/works"
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
YAHOO_SEARCH_URL = "https://search.yahoo.com/search"
TRUSTED_DOH_URL = "https://cloudflare-dns.com/dns-query"
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_TUN_FAKE_IP_TRUSTED_HOSTS = frozenset(
    {
        "patents.google.com",
        "arxiv.org",
        "export.arxiv.org",
        "connect.zhihuiya.com",
        "open.zhihuiya.com",
        "data-fulltext-image.zhihuiya.com",
        "ops.epo.org",
    }
)
_GOOGLE_PATENTS_LANGUAGE_MAP = {
    "en": "ENGLISH",
    "en-us": "ENGLISH",
    "zh": "CHINESE",
    "zh-cn": "CHINESE",
    "zh-tw": "CHINESE",
}


class EvidenceStage(StrEnum):
    """Canonical evidence stages from the project charter."""

    LEAD = "lead"
    RETRIEVED_DOCUMENT = "retrieved_document"
    QUALIFIED_EVIDENCE = "qualified_evidence"

    # Short aliases keep provider call sites readable while remaining compatible
    # with the names used by contracts.EvidenceStage.
    RETRIEVED = RETRIEVED_DOCUMENT
    QUALIFIED = QUALIFIED_EVIDENCE


class ProviderError(RuntimeError):
    """Base class for adapter failures that are safe to surface in run logs."""


class ProviderRequestError(ProviderError):
    """A network/HTTP failure with no response body or credential leakage."""


class ProviderContentError(ProviderError):
    """The response was not the requested real document."""


class QueryValidationError(ValueError):
    """A query lacks the technical subject/feature anchoring required by I2."""


class ResponseLike(Protocol):
    status_code: int
    content: bytes
    text: str
    headers: Mapping[str, str]
    url: Any

    def json(self) -> Any: ...


class Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> ResponseLike: ...

    def post(
        self,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | httpx.Timeout | None = None,
    ) -> ResponseLike: ...


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A query plus the anchors that prevent isolated broad-word searches."""

    text: str
    subject_terms: tuple[str, ...] = ()
    feature_terms: tuple[str, ...] = ()
    classification_terms: tuple[str, ...] = ()
    citation_terms: tuple[str, ...] = ()
    # Internal-only capability. Mapping input deliberately cannot set this
    # flag: it is constructed only after the lab/workflow has matched an exact
    # fixed-lane expression back to a successful persisted I2 plan.
    trusted_fixed_plan: bool = False

    @classmethod
    def from_input(
        cls,
        value: str | Mapping[str, Any] | "SearchQuery",
        *,
        subject_terms: Sequence[str] | None = None,
        feature_terms: Sequence[str] | None = None,
        classification_terms: Sequence[str] | None = None,
        citation_terms: Sequence[str] | None = None,
    ) -> "SearchQuery":
        if isinstance(value, SearchQuery):
            query = value
        elif isinstance(value, Mapping):
            query = cls(
                text=str(value.get("text") or value.get("query") or ""),
                subject_terms=_terms(value.get("subject_terms")),
                feature_terms=_terms(value.get("feature_terms") or value.get("gap_terms")),
                classification_terms=_terms(
                    value.get("classification_terms") or value.get("classifications")
                ),
                citation_terms=_terms(value.get("citation_terms") or value.get("citations")),
            )
        else:
            query = cls(text=str(value or ""))

        if any(
            item is not None
            for item in (subject_terms, feature_terms, classification_terms, citation_terms)
        ):
            query = replace(
                query,
                subject_terms=_terms(subject_terms) or query.subject_terms,
                feature_terms=_terms(feature_terms) or query.feature_terms,
                classification_terms=_terms(classification_terms) or query.classification_terms,
                citation_terms=_terms(citation_terms) or query.citation_terms,
            )
        return query.validated()

    def validated(self) -> "SearchQuery":
        subjects = _dedupe_terms(self.subject_terms)
        features = _dedupe_terms(self.feature_terms)
        classifications = _dedupe_terms(self.classification_terms)
        citations = _dedupe_terms(self.citation_terms)
        text = _normalize_space(self.text)

        if not text:
            text = " ".join((*subjects, *features, *classifications, *citations))
        if not text:
            raise QueryValidationError("检索式为空")

        if self.trusted_fixed_plan:
            return replace(
                self,
                text=text,
                subject_terms=subjects,
                feature_terms=features,
                classification_terms=classifications,
                citation_terms=citations,
            )

        # Exact publication/citation lookup is not a broad-word search.
        if _looks_like_publication_identifier(text):
            return replace(
                self,
                text=text,
                subject_terms=subjects,
                feature_terms=features,
                classification_terms=classifications,
                citation_terms=citations,
            )

        normalized_text = _search_normalize(text)
        if subjects:
            if not any(_search_normalize(term) in normalized_text for term in subjects):
                raise QueryValidationError("检索式缺少技术主题锚点")
            if not (features or classifications or citations):
                raise QueryValidationError("检索式只有技术主题，缺少特征、分类或引证锚点")
        else:
            atoms = _query_atoms(text)
            if len(atoms) < 2 or sum(len(atom) for atom in atoms) < 6:
                raise QueryValidationError(
                    "禁止执行孤立宽词；请使用技术主题与特征组合，或提供分类/引证锚点"
                )

        if features and not any(
            _search_normalize(term) in normalized_text for term in features
        ):
            raise QueryValidationError("检索式未包含声明的区别特征锚点")

        return replace(
            self,
            text=text,
            subject_terms=subjects,
            feature_terms=features,
            classification_terms=classifications,
            citation_terms=citations,
        )

    def model_dump(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "subject_terms": list(self.subject_terms),
            "feature_terms": list(self.feature_terms),
            "classification_terms": list(self.classification_terms),
            "citation_terms": list(self.citation_terms),
        }


@dataclass(frozen=True, slots=True)
class RetrievedArtifact:
    provider: str
    kind: str
    source_url: str
    media_type: str
    content: bytes = field(repr=False)
    content_sha256: str = ""
    retrieved_at: str = ""
    # Optional provider JSON/XML response that authorized or located this
    # binary (for example Patsnap P020/P042).  Raw metadata bytes remain
    # transient until the gateway copies them into its isolated ArtifactStore.
    source_metadata_artifact: RetrievedArtifact | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    request_attempt_log: tuple[Mapping[str, Any], ...] = field(
        default=(),
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", _sha256(self.content))
        if not self.retrieved_at:
            object.__setattr__(self, "retrieved_at", _now_iso())

    def model_dump(self, *, include_content: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "provider": self.provider,
            "kind": self.kind,
            "source_url": self.source_url,
            "media_type": self.media_type,
            "content_sha256": self.content_sha256,
            "content_length": len(self.content),
            "retrieved_at": self.retrieved_at,
        }
        if include_content:
            result["content"] = self.content
        if self.source_metadata_artifact is not None:
            result["source_metadata_artifact"] = (
                self.source_metadata_artifact.model_dump(include_content=False)
            )
        if self.request_attempt_log:
            result["request_attempt_log"] = [
                _json_safe(dict(item)) for item in self.request_attempt_log
            ]
        return result


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """Provider-neutral evidence DTO; no provider payload leaks into contracts."""

    provider: str
    source_type: str
    external_id: str
    title: str
    source_url: str
    stage: EvidenceStage = EvidenceStage.LEAD
    publication_number: str | None = None
    authority: str | None = None
    language: str | None = None
    publication_date: str | None = None
    filing_date: str | None = None
    priority_date: str | None = None
    abstract: str | None = None
    snippet: str | None = None
    description: str | None = None
    claims: str | None = None
    full_text: str | None = None
    pdf_url: str | None = None
    image_urls: tuple[str, ...] = ()
    content_sha256: str | None = None
    retrieved_at: str | None = None
    artifacts: tuple[Mapping[str, Any], ...] = ()
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    eligibility: Mapping[str, Any] | None = None
    qualification_issues: tuple[str, ...] = ()
    readable_document: Mapping[str, Any] | None = None
    analysis_ready: bool = False
    analysis_readiness_reason: str | None = None
    # Raw bytes are deliberately transient: the gateway must immediately copy
    # them into the environment-scoped immutable ArtifactStore.  ``model_dump``
    # never serialises them into checkpoints, logs, or API responses.
    primary_artifact: RetrievedArtifact | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def model_dump(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source_type": self.source_type,
            "external_id": self.external_id,
            "title": self.title,
            "source_url": self.source_url,
            "stage": self.stage.value,
            "publication_number": self.publication_number,
            "authority": self.authority,
            "language": self.language,
            "publication_date": self.publication_date,
            "filing_date": self.filing_date,
            "priority_date": self.priority_date,
            "abstract": self.abstract,
            "snippet": self.snippet,
            "description": self.description,
            "claims": self.claims,
            "full_text": self.full_text,
            "pdf_url": self.pdf_url,
            "image_urls": list(self.image_urls),
            "content_sha256": self.content_sha256,
            "retrieved_at": self.retrieved_at,
            "artifacts": [_json_safe(dict(item)) for item in self.artifacts],
            "raw_metadata": _json_safe(dict(self.raw_metadata)),
            "provenance": _json_safe(dict(self.provenance)),
            "eligibility": _json_safe(dict(self.eligibility)) if self.eligibility else None,
            "qualification_issues": list(self.qualification_issues),
            "readable_document": (
                _json_safe(dict(self.readable_document))
                if self.readable_document
                else None
            ),
            "analysis_ready": self.analysis_ready,
            "analysis_readiness_reason": self.analysis_readiness_reason,
        }

    def asdict(self) -> dict[str, Any]:
        return self.model_dump()

    def with_artifact(self, artifact: RetrievedArtifact) -> "EvidenceRecord":
        artifact_summary = artifact.model_dump(include_content=False)
        return replace(
            self,
            stage=EvidenceStage.RETRIEVED,
            content_sha256=artifact.content_sha256,
            retrieved_at=artifact.retrieved_at,
            artifacts=(*self.artifacts, artifact_summary),
            qualification_issues=(),
            primary_artifact=artifact,
        )


def qualify_evidence(
    record: EvidenceRecord,
    *,
    date_eligibility: DateEligibilityResult | Mapping[str, Any],
    content_relevance_verified: bool,
    source_chain_verified: bool,
    public_date_evidence: str | None = None,
    strict: bool = False,
) -> EvidenceRecord:
    """Promote retrieved content only after explicit local evidence checks.

    Failed checks leave the record at ``retrieved_document`` and attach machine
    readable issues.  ``strict=True`` is available to module-lab callers that
    prefer an exception.  No provider-side search filter participates here.
    """

    if isinstance(date_eligibility, DateEligibilityResult):
        eligibility = date_eligibility.model_dump()
    else:
        eligibility = dict(date_eligibility)

    category = str(eligibility.get("category") or "")
    novelty_eligible = bool(eligibility.get("novelty_eligible"))
    date_evidence = public_date_evidence or str(
        record.provenance.get("public_date_evidence") or ""
    ).strip()

    issues: list[str] = []
    if record.stage not in {EvidenceStage.RETRIEVED, EvidenceStage.QUALIFIED}:
        issues.append("document_not_retrieved")
    if not record.content_sha256 and not record.artifacts:
        issues.append("content_hash_missing")
    if not source_chain_verified:
        issues.append("source_chain_not_verified")
    if not content_relevance_verified:
        issues.append("technical_disclosure_not_verified")
    if not date_evidence:
        issues.append("public_date_evidence_missing")
    if category == DateCategory.UNKNOWN.value:
        issues.append("date_eligibility_unknown")
    elif category == DateCategory.POST_DATE_LEAD.value or not novelty_eligible:
        issues.append("candidate_not_date_eligible")
    elif category not in {
        DateCategory.ORDINARY_PRIOR_ART.value,
        DateCategory.CONFLICTING_APPLICATION_CANDIDATE.value,
    }:
        issues.append("unrecognized_date_category")

    if issues:
        if strict:
            raise ProviderContentError("证据资格校验失败: " + ", ".join(issues))
        return replace(
            record,
            stage=(
                EvidenceStage.RETRIEVED
                if record.stage is not EvidenceStage.LEAD
                else EvidenceStage.LEAD
            ),
            eligibility=eligibility,
            qualification_issues=tuple(issues),
        )

    return replace(
        record,
        stage=EvidenceStage.QUALIFIED,
        eligibility=eligibility,
        qualification_issues=(),
        provenance={**record.provenance, "public_date_evidence": date_evidence},
    )


class _BaseHttpProvider:
    provider_name = "base"

    def __init__(
        self,
        *,
        transport: Transport | Callable[..., ResponseLike] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_image_response_bytes: int = DEFAULT_MAX_IMAGE_RESPONSE_BYTES,
        max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        resolver: Callable[[str], Iterable[str]] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes 必须大于 0")
        if max_image_response_bytes < 1:
            raise ValueError("max_image_response_bytes 必须大于 0")
        if max_image_pixels < 1:
            raise ValueError("max_image_pixels 必须大于 0")
        if max_redirects < 0 or max_redirects > 10:
            raise ValueError("max_redirects 必须在 0..10 之间")
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)
        self.max_image_response_bytes = min(
            int(max_image_response_bytes),
            int(max_response_bytes),
        )
        self.max_image_pixels = int(max_image_pixels)
        self.max_redirects = int(max_redirects)
        self._owns_transport = transport is None
        if isinstance(transport, httpx.Client):
            if transport.follow_redirects:
                raise ValueError(
                    "注入的 httpx.Client 必须设置 follow_redirects=False，"
                    "以便逐跳执行 SSRF 校验"
                )
            # httpx does not expose a stable public API proving that an
            # externally-created Client has trust_env/proxies disabled.  A
            # client whose proxy resolves the original Host could bypass the
            # DNS address checked below, so reject it rather than guess.
            raise ValueError(
                "禁止注入 httpx.Client：无法验证 trust_env/代理隔离；"
                "测试请注入 fixture transport"
            )
        self.transport: Any = transport or httpx.Client(
            # Redirects are followed by _get so every hop is checked before a
            # connection is made.  Enabling httpx redirect following here
            # would make a public URL -> private URL SSRF bypass possible.
            follow_redirects=False,
            trust_env=False,
            timeout=self.timeout_seconds,
            headers={
                "User-Agent": "patent-invalidity-test/0.1 (+document-retrieval)",
                # Some public APIs advertise encodings that the local httpx
                # build cannot decode.  Identity keeps the bounded streaming
                # and content-size checks deterministic.
                "Accept-Encoding": "identity",
            },
        )
        if resolver is not None:
            self._resolver: Callable[[str], Iterable[str]] | None = resolver
        elif hasattr(self.transport, "resolve"):
            # Test/in-process transports can expose the DNS decision that they
            # use.  This keeps SSRF tests deterministic and models production.
            self._resolver = self.transport.resolve
        else:
            # Dependency injection must not disable DNS validation.  Custom
            # transports may expose ``resolve`` (recommended for deterministic
            # tests/proxies); otherwise the system resolver remains mandatory.
            self._resolver = _resolve_public_host

    def close(self) -> None:
        if self._owns_transport and hasattr(self.transport, "close"):
            self.transport.close()

    def __enter__(self) -> "_BaseHttpProvider":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        allowed_media_types: Sequence[str] | None = None,
        max_response_bytes: int | None = None,
        allow_error_status: bool = False,
    ) -> ResponseLike:
        byte_limit = self.max_response_bytes if max_response_bytes is None else int(
            max_response_bytes
        )
        if byte_limit < 1:
            raise ValueError("max_response_bytes 必须大于 0")

        current_url = str(url)
        current_params = params
        current_headers = dict(headers or {})
        for redirect_count in range(self.max_redirects + 1):
            resolved_addresses = self._validate_public_url(current_url)
            request_url = current_url
            request_headers = dict(current_headers)
            request_extensions: dict[str, Any] | None = None
            if (
                self._owns_transport
                and isinstance(self.transport, httpx.Client)
                and not all(_is_tun_fake_ip(item) for item in resolved_addresses)
            ):
                # Pin the TCP connection to the exact address that passed the
                # SSRF check.  Host and TLS SNI remain the original hostname,
                # preventing a second DNS lookup (DNS rebinding / TOCTOU).
                request_url, host_header, sni_hostname = _pinned_request_target(
                    current_url,
                    resolved_addresses[0],
                )
                request_headers["Host"] = host_header
                if sni_hostname:
                    request_extensions = {"sni_hostname": sni_hostname}
            response = self._request_once(
                request_url,
                logical_url=current_url,
                params=current_params,
                headers=request_headers,
                extensions=request_extensions,
                max_response_bytes=byte_limit,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            if status in {301, 302, 303, 307, 308}:
                if redirect_count >= self.max_redirects:
                    raise ProviderRequestError(f"{self.provider_name} 重定向次数超限")
                location = _response_header(response, "location")
                if not location:
                    raise ProviderRequestError(
                        f"{self.provider_name} 重定向响应缺少 Location"
                    )
                redirect_base = _response_url(response) or current_url
                next_url = urljoin(redirect_base, location)
                if _url_origin(next_url) != _url_origin(redirect_base):
                    current_headers = {
                        key: value
                        for key, value in current_headers.items()
                        if key.lower()
                        not in {"authorization", "cookie", "proxy-authorization"}
                    }
                current_url = next_url
                current_params = None
                continue

            if not allow_error_status and (status < 200 or status >= 300):
                raise ProviderRequestError(f"{self.provider_name} HTTP {status}")

            final_url = _response_url(response) or current_url
            self._validate_public_url(final_url)
            self._validate_response_size(response, byte_limit)
            if allowed_media_types:
                self._validate_media_type(response, allowed_media_types)
            return response

        raise ProviderRequestError(f"{self.provider_name} 重定向次数超限")

    def _request_once(
        self,
        url: str,
        *,
        logical_url: str,
        params: Mapping[str, Any] | None,
        headers: Mapping[str, str] | None,
        extensions: Mapping[str, Any] | None,
        max_response_bytes: int,
    ) -> ResponseLike:
        try:
            if self._owns_transport and isinstance(self.transport, httpx.Client):
                # Stream production responses so a lying/missing
                # Content-Length cannot force an unbounded allocation.
                with self.transport.stream(
                    "GET",
                    url,
                    params=params,
                    headers=headers,
                    extensions=extensions,
                    timeout=self.timeout_seconds,
                ) as streamed:
                    declared = _content_length(streamed)
                    if declared is not None and declared > max_response_bytes:
                        raise ProviderContentError(
                            f"{self.provider_name} 响应超过大小上限"
                        )
                    status = int(streamed.status_code)
                    chunks: list[bytes] = []
                    total = 0
                    if status not in {301, 302, 303, 307, 308}:
                        for chunk in streamed.iter_bytes():
                            total += len(chunk)
                            if total > max_response_bytes:
                                raise ProviderContentError(
                                    f"{self.provider_name} 响应超过大小上限"
                                )
                            chunks.append(chunk)
                    logical_request = httpx.Request(
                        "GET",
                        logical_url,
                        params=params,
                        headers=_without_credentials(headers),
                    )
                    return httpx.Response(
                        status,
                        headers=streamed.headers,
                        content=b"".join(chunks),
                        request=logical_request,
                    )

            if hasattr(self.transport, "get"):
                response = self.transport.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.transport(
                    "GET",
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
            self._validate_response_size(response, max_response_bytes)
            return response
        except ProviderError:
            raise
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise ProviderRequestError(
                f"{self.provider_name} 请求失败: {type(exc).__name__}"
            ) from exc
        except Exception as exc:
            # Fixture transports can raise their own timeout/error types.  Do
            # not include arguments because they may contain headers or tokens.
            raise ProviderRequestError(
                f"{self.provider_name} transport 失败: {type(exc).__name__}"
            ) from exc

    def _post_form(
        self,
        url: str,
        *,
        data: Mapping[str, str],
        headers: Mapping[str, str] | None = None,
        allowed_media_types: Sequence[str] | None = None,
        max_response_bytes: int | None = None,
    ) -> ResponseLike:
        """POST a fixed form endpoint without following redirects.

        OAuth credentials are deliberately omitted from the synthetic response
        request object and every transport exception is reduced to its type.
        """

        byte_limit = self.max_response_bytes if max_response_bytes is None else int(
            max_response_bytes
        )
        if byte_limit < 1:
            raise ValueError("max_response_bytes 必须大于 0")

        resolved_addresses = self._validate_public_url(url)
        request_url = str(url)
        request_headers = dict(headers or {})
        request_extensions: dict[str, Any] | None = None
        if (
            self._owns_transport
            and isinstance(self.transport, httpx.Client)
            and not all(_is_tun_fake_ip(item) for item in resolved_addresses)
        ):
            request_url, host_header, sni_hostname = _pinned_request_target(
                url,
                resolved_addresses[0],
            )
            request_headers["Host"] = host_header
            if sni_hostname:
                request_extensions = {"sni_hostname": sni_hostname}

        try:
            if self._owns_transport and isinstance(self.transport, httpx.Client):
                with self.transport.stream(
                    "POST",
                    request_url,
                    data=dict(data),
                    headers=request_headers,
                    extensions=request_extensions,
                    timeout=self.timeout_seconds,
                ) as streamed:
                    declared = _content_length(streamed)
                    if declared is not None and declared > byte_limit:
                        raise ProviderContentError(
                            f"{self.provider_name} 响应超过大小上限"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in streamed.iter_bytes():
                        total += len(chunk)
                        if total > byte_limit:
                            raise ProviderContentError(
                                f"{self.provider_name} 响应超过大小上限"
                            )
                        chunks.append(chunk)
                    logical_request = httpx.Request(
                        "POST",
                        url,
                        headers=_without_credentials(request_headers),
                    )
                    response: ResponseLike = httpx.Response(
                        int(streamed.status_code),
                        headers=streamed.headers,
                        content=b"".join(chunks),
                        request=logical_request,
                    )
            elif hasattr(self.transport, "post"):
                response = self.transport.post(
                    request_url,
                    data=dict(data),
                    headers=request_headers,
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.transport(
                    "POST",
                    request_url,
                    data=dict(data),
                    headers=request_headers,
                    timeout=self.timeout_seconds,
                )
        except ProviderError:
            raise
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise ProviderRequestError(
                f"{self.provider_name} 请求失败: {type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise ProviderRequestError(
                f"{self.provider_name} transport 失败: {type(exc).__name__}"
            ) from exc

        status = int(getattr(response, "status_code", 0) or 0)
        if status < 200 or status >= 300:
            raise ProviderRequestError(f"{self.provider_name} HTTP {status}")
        final_url = _response_url(response) or url
        if _url_origin(final_url) != _url_origin(url):
            raise ProviderRequestError(f"{self.provider_name} POST 响应来源不一致")
        self._validate_public_url(final_url)
        self._validate_response_size(response, byte_limit)
        if allowed_media_types:
            self._validate_media_type(response, allowed_media_types)
        return response

    def _post_json(
        self,
        url: str,
        *,
        payload: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
        allowed_media_types: Sequence[str] | None = None,
        max_response_bytes: int | None = None,
        allow_error_status: bool = False,
    ) -> ResponseLike:
        """POST bounded JSON to one fixed public endpoint without redirects.

        The logical request retained by httpx intentionally excludes both the
        Bearer credential and request body.  ``allow_error_status`` is only for
        adapters that must classify a bounded provider error response before
        deciding whether an idempotent request may be retried.
        """

        byte_limit = self.max_response_bytes if max_response_bytes is None else int(
            max_response_bytes
        )
        if byte_limit < 1:
            raise ValueError("max_response_bytes 必须大于 0")

        resolved_addresses = self._validate_public_url(url)
        request_url = str(url)
        request_headers = dict(headers or {})
        request_extensions: dict[str, Any] | None = None
        if (
            self._owns_transport
            and isinstance(self.transport, httpx.Client)
            and not all(_is_tun_fake_ip(item) for item in resolved_addresses)
        ):
            request_url, host_header, sni_hostname = _pinned_request_target(
                url,
                resolved_addresses[0],
            )
            request_headers["Host"] = host_header
            if sni_hostname:
                request_extensions = {"sni_hostname": sni_hostname}

        try:
            if self._owns_transport and isinstance(self.transport, httpx.Client):
                with self.transport.stream(
                    "POST",
                    request_url,
                    json=dict(payload),
                    headers=request_headers,
                    extensions=request_extensions,
                    timeout=self.timeout_seconds,
                ) as streamed:
                    declared = _content_length(streamed)
                    if declared is not None and declared > byte_limit:
                        raise ProviderContentError(
                            f"{self.provider_name} 响应超过大小上限"
                        )
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in streamed.iter_bytes():
                        total += len(chunk)
                        if total > byte_limit:
                            raise ProviderContentError(
                                f"{self.provider_name} 响应超过大小上限"
                            )
                        chunks.append(chunk)
                    logical_request = httpx.Request(
                        "POST",
                        url,
                        headers=_without_credentials(request_headers),
                    )
                    response: ResponseLike = httpx.Response(
                        int(streamed.status_code),
                        headers=streamed.headers,
                        content=b"".join(chunks),
                        request=logical_request,
                    )
            elif hasattr(self.transport, "post"):
                response = self.transport.post(
                    request_url,
                    json=dict(payload),
                    headers=request_headers,
                    timeout=self.timeout_seconds,
                )
            else:
                response = self.transport(
                    "POST",
                    request_url,
                    json=dict(payload),
                    headers=request_headers,
                    timeout=self.timeout_seconds,
                )
        except ProviderError:
            raise
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise ProviderRequestError(
                f"{self.provider_name} 请求失败: {type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise ProviderRequestError(
                f"{self.provider_name} transport 失败: {type(exc).__name__}"
            ) from exc

        status = int(getattr(response, "status_code", 0) or 0)
        if status in {301, 302, 303, 307, 308}:
            raise ProviderRequestError(f"{self.provider_name} POST 禁止重定向")
        if not allow_error_status and (status < 200 or status >= 300):
            raise ProviderRequestError(f"{self.provider_name} HTTP {status}")
        final_url = _response_url(response) or url
        if _url_origin(final_url) != _url_origin(url):
            raise ProviderRequestError(f"{self.provider_name} POST 响应来源不一致")
        self._validate_public_url(final_url)
        self._validate_response_size(response, byte_limit)
        if allowed_media_types:
            self._validate_media_type(response, allowed_media_types)
        return response

    def _validate_public_url(self, url: str) -> tuple[str, ...]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ProviderRequestError(f"{self.provider_name} 只允许 HTTP(S) 公网 URL")
        if parsed.username or parsed.password:
            raise ProviderRequestError(f"{self.provider_name} URL 不得包含用户凭据")
        hostname = (parsed.hostname or "").rstrip(".").lower()
        if not hostname:
            raise ProviderRequestError(f"{self.provider_name} URL 缺少主机名")
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise ProviderRequestError(f"{self.provider_name} URL 必须指向公网地址")

        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            if not literal.is_global:
                raise ProviderRequestError(
                    f"{self.provider_name} URL 必须指向公网地址"
                )
            return (str(literal),)

        if self._resolver is None:
            raise ProviderRequestError(f"{self.provider_name} 缺少安全 DNS 解析器")
        try:
            addresses = tuple(self._resolver(hostname))
        except Exception as exc:
            raise ProviderRequestError(
                f"{self.provider_name} 公网 DNS 解析失败: {type(exc).__name__}"
            ) from exc
        if not addresses:
            raise ProviderRequestError(f"{self.provider_name} 公网 DNS 未返回地址")
        trusted_tun_route = hostname in _TUN_FAKE_IP_TRUSTED_HOSTS and all(
            _is_tun_fake_ip(value) for value in addresses
        )
        for value in addresses:
            try:
                address = ipaddress.ip_address(str(value))
            except ValueError as exc:
                raise ProviderRequestError(
                    f"{self.provider_name} DNS 返回无效地址"
                ) from exc
            if not address.is_global and not trusted_tun_route:
                raise ProviderRequestError(
                    f"{self.provider_name} URL 必须指向公网地址"
                )
        return tuple(sorted({str(ipaddress.ip_address(str(item))) for item in addresses}))

    def _validate_response_size(self, response: ResponseLike, limit: int) -> None:
        declared = _content_length(response)
        if declared is not None and declared > limit:
            raise ProviderContentError(f"{self.provider_name} 响应超过大小上限")
        if len(_response_content(response)) > limit:
            raise ProviderContentError(f"{self.provider_name} 响应超过大小上限")

    def _validate_media_type(
        self,
        response: ResponseLike,
        allowed_media_types: Sequence[str],
    ) -> None:
        actual = _response_header(response, "content-type").split(";", 1)[0].strip().lower()
        allowed = tuple(item.strip().lower() for item in allowed_media_types if item.strip())
        if not actual or not any(_media_type_matches(actual, item) for item in allowed):
            raise ProviderContentError(
                f"{self.provider_name} 响应 MIME 类型不符合预期"
            )


class EpoOpsProvider(_BaseHttpProvider):
    """EPO Open Patent Services adapter with in-memory OAuth credentials.

    OPS discovery is global bibliographic search.  Full text is retrieved only
    for authorities where OPS reports it as available; unsupported authorities
    remain leads for an official/manual document retrieval path.
    """

    provider_name = "epo_ops"
    capabilities: Mapping[str, Any] = {
        "discovery": True,
        "bibliographic_verification": True,
        "full_text": "authority_dependent",
        "pdf": "page_by_page",
        "images": "page_by_page",
        "family": True,
        "publication_date": True,
        "filing_date": True,
        "priority_date": True,
    }

    def __init__(
        self,
        *,
        consumer_key: str,
        consumer_secret: str,
        transport: Transport | Callable[..., ResponseLike] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_image_response_bytes: int = DEFAULT_MAX_IMAGE_RESPONSE_BYTES,
        max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        resolver: Callable[[str], Iterable[str]] | None = None,
        base_url: str = EPO_OPS_BASE_URL,
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
        self.consumer_key = str(consumer_key or "").strip()
        self.consumer_secret = str(consumer_secret or "").strip()
        if not self.consumer_key or not self.consumer_secret:
            raise ValueError("EPO OPS consumer key/secret 必须同时配置")
        self.base_url = base_url.rstrip("/")
        parsed_base = urlparse(self.base_url)
        if parsed_base.scheme != "https" or not parsed_base.hostname:
            raise ValueError("EPO OPS base_url 必须是 HTTPS URL")
        self.token_url = f"{self.base_url}/auth/accesstoken"
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._last_search_artifact: RetrievedArtifact | None = None
        self._last_retrieval_artifacts: tuple[RetrievedArtifact, ...] = ()
        self._last_full_document_manifest: Mapping[str, Any] = {}
        self._last_application_resolution: tuple[str, bytes] | None = None

    @property
    def last_search_artifact(self) -> RetrievedArtifact | None:
        """Return transient raw XML for immediate environment-scoped freezing."""

        return self._last_search_artifact

    @property
    def last_retrieval_artifacts(self) -> tuple[RetrievedArtifact, ...]:
        """Return successful raw OPS responses for failure-path audit freezing."""

        return self._last_retrieval_artifacts

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
        del language
        safe_query = SearchQuery.from_input(
            query,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        if max_results < 1 or max_results > 100:
            raise ValueError("max_results 必须在 1..100 之间")
        normalized_before = _date_string(server_before)
        normalized_after = _date_string(server_after)
        normalized_filing_before = _date_string(filing_before)
        country_code = str(country or "").strip().upper() or None
        if country_code and not re.fullmatch(r"[A-Z]{2}", country_code):
            raise ValueError("country 必须是两位国家/地区代码")
        self._last_search_artifact = None
        cql = _epo_cql_query(
            safe_query,
            country=country_code,
            server_before=normalized_before,
            server_after=normalized_after,
        )
        response = self._ops_get(
            f"{self.base_url}/rest-services/published-data/search/biblio",
            params={"q": cql},
            accept="application/exchange+xml",
            extra_headers={"X-OPS-Range": f"1-{max_results}"},
            allowed_media_types=(
                "application/exchange+xml",
                "application/ops+xml",
                "application/xml",
                "text/xml",
            ),
        )
        raw = _response_content(response)
        root = _safe_xml_root(raw, provider="EPO OPS search")
        response_hash = _sha256(raw)
        retrieved_at = _now_iso()
        self._last_search_artifact = RetrievedArtifact(
            provider=self.provider_name,
            kind="provider_search_response",
            source_url=(
                f"{self.base_url}/rest-services/published-data/search/biblio"
            ),
            media_type=(
                _response_header(response, "content-type").split(";", 1)[0].strip()
                or "application/exchange+xml"
            ),
            content=raw,
            content_sha256=response_hash,
            retrieved_at=retrieved_at,
        )
        quota = _ops_quota_headers(response)
        total_result_count = str(
            root.xpath(
                "string(//*[local-name()='biblio-search']/@total-result-count)"
            )
            or ""
        ).strip()
        records: list[EvidenceRecord] = []
        seen: set[str] = set()
        filing_cutoff = parse_date(normalized_filing_before)
        for values in _epo_exchange_documents(root):
            publication_number = str(values.get("publication_number") or "")
            if not publication_number or publication_number in seen:
                continue
            filing_dates = tuple(
                item
                for item in (
                    parse_date(values.get("priority_date")),
                    parse_date(values.get("filing_date")),
                )
                if item is not None
            )
            local_filing_filter_result: str | None = None
            if filing_cutoff is not None:
                if filing_dates and min(filing_dates) >= filing_cutoff:
                    # OPS has no filing/priority-date CQL index.  The CN
                    # conflicting-application channel searches an explicit
                    # publication-date superset, then removes only rows whose
                    # frozen dates prove they were filed too late.
                    continue
                local_filing_filter_result = (
                    "included_before_cutoff"
                    if filing_dates
                    else "retained_missing_dates"
                )
            seen.add(publication_number)
            epodoc_reference = _epo_epodoc_reference(publication_number)
            source_url = (
                f"{self.base_url}/rest-services/published-data/publication/"
                f"epodoc/{epodoc_reference}/biblio"
            )
            records.append(
                EvidenceRecord(
                    provider=self.provider_name,
                    source_type="patent",
                    external_id=publication_number,
                    title=str(values.get("title") or ""),
                    source_url=source_url,
                    stage=EvidenceStage.LEAD,
                    publication_number=publication_number,
                    authority=str(values.get("authority") or "") or None,
                    language=str(values.get("language") or "") or None,
                    publication_date=_date_string(values.get("publication_date")),
                    filing_date=_date_string(values.get("filing_date")),
                    priority_date=_date_string(values.get("priority_date")),
                    abstract=str(values.get("abstract") or "") or None,
                    snippet=str(values.get("abstract") or "") or None,
                    raw_metadata={
                        "family_id": values.get("family_id"),
                        "application_number": values.get("application_number"),
                    },
                    provenance={
                        "search_query": safe_query.model_dump(),
                        "search_endpoint": (
                            f"{self.base_url}/rest-services/published-data/"
                            "search/biblio"
                        ),
                        "search_cql": cql,
                        "search_response_sha256": response_hash,
                        "search_response_bytes": len(raw),
                        "search_retrieved_at": retrieved_at,
                        "ops_version": "3.2",
                        "requested_range": f"1-{max_results}",
                        "total_result_count": total_result_count or None,
                        "provider_server_before": normalized_before,
                        "provider_server_after": normalized_after,
                        "provider_filing_before": normalized_filing_before,
                        "provider_country": country_code,
                        "filing_filter_execution": (
                            "local_superset_filter"
                            if normalized_filing_before
                            else None
                        ),
                        "local_filing_filter_result": local_filing_filter_result,
                        "provider_filters_are_discovery_only": True,
                        "capabilities": dict(self.capabilities),
                        "quota": quota,
                    },
                )
            )
            if len(records) >= max_results:
                break
        return records

    def retrieve(
        self,
        lead: EvidenceRecord | Mapping[str, Any] | str,
    ) -> EvidenceRecord:
        self._last_retrieval_artifacts = ()
        self._last_full_document_manifest = {}
        self._last_application_resolution = None
        record = self._coerce_lead(lead)
        publication_number = _normalize_publication_number(
            record.publication_number
        )
        if not publication_number:
            publication_number = self._resolve_publication_number(record)
        epodoc_reference = _epo_epodoc_reference(publication_number)
        base_reference = _epo_base_reference(publication_number)
        publication_url = (
            f"{self.base_url}/rest-services/published-data/publication/epodoc/"
        )
        biblio_url = f"{publication_url}{epodoc_reference}/biblio"

        responses: dict[str, bytes] = {}
        if self._last_application_resolution is not None:
            _application_url, application_raw = self._last_application_resolution
            responses["application_biblio"] = application_raw
        biblio_response = self._ops_get(
            biblio_url,
            accept="application/exchange+xml",
            allowed_media_types=(
                "application/exchange+xml",
                "application/ops+xml",
                "application/xml",
                "text/xml",
            ),
        )
        responses["biblio"] = _response_content(biblio_response)
        self._track_retrieval_response(
            name="biblio",
            url=biblio_url,
            response=biblio_response,
        )
        biblio_root = _safe_xml_root(responses["biblio"], provider="EPO OPS biblio")
        biblio_values = next(iter(_epo_exchange_documents(biblio_root)), {})
        returned_publication_number = _normalize_publication_number(
            biblio_values.get("publication_number")
        )
        if not returned_publication_number:
            raise ProviderContentError("EPO OPS biblio 缺少可核验公开编号")
        if returned_publication_number != publication_number:
            raise ProviderContentError(
                "EPO OPS biblio 返回的公开编号与智慧芽候选不一致"
            )

        available_sections: set[str] = set()
        try:
            inquiry_url = f"{publication_url}{base_reference}/fulltext"
            inquiry = self._ops_get(
                inquiry_url,
                accept="application/fulltext+xml",
                allowed_media_types=(
                    "application/fulltext+xml",
                    "application/ops+xml",
                    "application/xml",
                    "text/xml",
                ),
            )
            responses["fulltext_inquiry"] = _response_content(inquiry)
            self._track_retrieval_response(
                name="fulltext_inquiry",
                url=inquiry_url,
                response=inquiry,
            )
            inquiry_root = _safe_xml_root(
                responses["fulltext_inquiry"],
                provider="EPO OPS fulltext inquiry",
            )
            available_sections = {
                str(item).strip().lower()
                for item in inquiry_root.xpath(
                    "//*[local-name()='fulltext-instance']/@desc"
                )
                if str(item).strip()
            }
        except ProviderRequestError as exc:
            if not _provider_http_status(exc, {404}):
                raise

        section_text: dict[str, str] = {}
        for section in ("description", "claims"):
            if section not in available_sections:
                continue
            section_url = f"{publication_url}{base_reference}/{section}"
            response = self._ops_get(
                section_url,
                accept="application/fulltext+xml",
                allowed_media_types=(
                    "application/fulltext+xml",
                    "application/xml",
                    "text/xml",
                ),
            )
            raw = _response_content(response)
            responses[section] = raw
            self._track_retrieval_response(
                name=section,
                url=section_url,
                response=response,
            )
            section_text[section] = _epo_fulltext_text(
                _safe_xml_root(raw, provider=f"EPO OPS {section}"),
                section,
            )

        image_link: str | None = None
        image_pages = 0
        try:
            images_url = f"{publication_url}{epodoc_reference}/images"
            image_response = self._ops_get(
                images_url,
                accept="application/ops+xml",
                allowed_media_types=(
                    "application/ops+xml",
                    "application/xml",
                    "text/xml",
                ),
            )
            responses["images_inquiry"] = _response_content(image_response)
            self._track_retrieval_response(
                name="images_inquiry",
                url=images_url,
                response=image_response,
            )
            image_root = _safe_xml_root(
                responses["images_inquiry"],
                provider="EPO OPS images inquiry",
            )
            image_link, image_pages = _epo_fullimage_info(image_root)
        except ProviderRequestError as exc:
            if not _provider_http_status(exc, {404}):
                raise

        title = str(biblio_values.get("title") or record.title or "")
        abstract = str(biblio_values.get("abstract") or record.abstract or "") or None
        description = section_text.get("description") or None
        claims = section_text.get("claims") or None
        full_text = "\n\n".join(
            part
            for part in (
                title,
                f"Abstract\n{abstract}" if abstract else None,
                f"Description\n{description}" if description else None,
                f"Claims\n{claims}" if claims else None,
            )
            if part
        )
        has_retrieved_text = bool(description or claims)
        response_bundle = RetrievedArtifact(
            provider=self.provider_name,
            kind="provider_retrieval_bundle",
            source_url=biblio_url,
            media_type="application/vnd.epo.ops.bundle+json",
            content=_epo_response_bundle(responses),
        )
        primary_artifact: RetrievedArtifact | None = None
        if image_link and image_pages > 0:
            primary_artifact = self.download_pdf(
                replace(
                    record,
                    publication_number=returned_publication_number,
                    raw_metadata={
                        **dict(record.raw_metadata),
                        "ops_fullimage_link": image_link,
                        "ops_fullimage_pages": image_pages,
                    },
                )
            )
            primary_artifact = replace(
                primary_artifact,
                source_metadata_artifact=response_bundle,
            )
        elif has_retrieved_text:
            primary_artifact = replace(
                response_bundle,
                kind="source_bundle",
            )
        has_retrieved_content = primary_artifact is not None
        image_urls: tuple[str, ...] = ()
        if image_link and image_pages > 0:
            image_base = (
                f"{self.base_url}/rest-services/published-data/images/{image_link}"
            )
            image_urls = tuple(
                f"{image_base}?Range={page}"
                for page in range(1, min(image_pages, 2) + 1)
            )
        biblio_hash = _sha256(responses["biblio"])
        frozen_publication_date = _date_string(
            biblio_values.get("publication_date")
        )
        frozen_filing_date = _date_string(biblio_values.get("filing_date"))
        frozen_priority_date = _date_string(biblio_values.get("priority_date"))
        publication_date = frozen_publication_date or _date_string(
            record.publication_date
        )
        filing_date = frozen_filing_date or _date_string(record.filing_date)
        priority_date = frozen_priority_date or _date_string(record.priority_date)
        discovery_provider = (
            str(record.provenance.get("discovery_provider") or "").strip()
            or str(record.provider or "").strip()
            or None
        )
        date_evidence = {
            "publication_date": {
                "value": frozen_publication_date,
                "source": (
                    "EPO OPS biblio publication-reference/"
                    "document-id[@document-id-type='docdb']/date"
                ),
                "response_name": "biblio",
                "response_sha256": biblio_hash,
            },
            "filing_date": {
                "value": frozen_filing_date,
                "source": (
                    "EPO OPS biblio application-reference/"
                    "document-id[@document-id-type='docdb']/date"
                ),
                "response_name": "biblio",
                "response_sha256": biblio_hash,
            },
            "priority_date": {
                "value": frozen_priority_date,
                "source": "EPO OPS biblio priority-claim/document-id/date",
                "response_name": "biblio",
                "response_sha256": biblio_hash,
            },
        }
        provenance = {
            **dict(record.provenance),
            "discovery_provider": discovery_provider,
            "retrieval_provider": self.provider_name,
            "retrieval_identity": {
                "requested_publication_number": publication_number,
                "returned_publication_number": returned_publication_number,
                "requested_application_number": (
                    _epo_application_number_from_record(record)
                ),
                "match": "exact",
            },
            "detail_endpoint": biblio_url,
            "detail_response_sha256": biblio_hash,
            "detail_response_hashes": {
                name: _sha256(content) for name, content in responses.items()
            },
            "detail_response_bytes": {
                name: len(content) for name, content in responses.items()
            },
            "fulltext_sections": sorted(section_text),
            "fullimage_pages": image_pages,
            "full_document": dict(self._last_full_document_manifest),
            "capabilities": dict(self.capabilities),
            "date_evidence": date_evidence,
            "public_date_evidence": (
                "EPO OPS DOCDB publication-reference/date; "
                f"biblio_sha256={biblio_hash}"
                if frozen_publication_date
                else None
            ),
            "publication_date_verified": bool(frozen_publication_date),
            "filing_date_verified": bool(frozen_filing_date),
            "priority_date_verified": bool(frozen_priority_date),
        }
        if not has_retrieved_content:
            return replace(
                record,
                provider=self.provider_name,
                external_id=record.external_id or returned_publication_number,
                source_url=biblio_url,
                title=title,
                publication_number=returned_publication_number,
                authority=str(biblio_values.get("authority") or record.authority or "")
                or None,
                language=str(biblio_values.get("language") or record.language or "")
                or None,
                publication_date=publication_date,
                filing_date=filing_date,
                priority_date=priority_date,
                abstract=abstract,
                snippet=abstract,
                image_urls=image_urls,
                raw_metadata={
                    **dict(record.raw_metadata),
                    "ops_fullimage_link": image_link,
                    "ops_fullimage_pages": image_pages,
                },
                provenance=provenance,
                qualification_issues=("ops_fulltext_not_available",),
            )

        assert primary_artifact is not None
        return replace(
            record,
            provider=self.provider_name,
            external_id=record.external_id or returned_publication_number,
            source_url=biblio_url,
            stage=EvidenceStage.RETRIEVED,
            title=title,
            publication_number=returned_publication_number,
            authority=str(biblio_values.get("authority") or record.authority or "")
            or None,
            language=str(biblio_values.get("language") or record.language or "") or None,
            publication_date=publication_date,
            filing_date=filing_date,
            priority_date=priority_date,
            abstract=abstract,
            snippet=abstract,
            description=description,
            claims=claims,
            full_text=full_text,
            image_urls=image_urls,
            content_sha256=primary_artifact.content_sha256,
            retrieved_at=primary_artifact.retrieved_at,
            artifacts=(*record.artifacts, primary_artifact.model_dump(include_content=False)),
            raw_metadata={
                **dict(record.raw_metadata),
                "ops_fullimage_link": image_link,
                "ops_fullimage_pages": image_pages,
            },
            provenance=provenance,
            qualification_issues=(),
            primary_artifact=primary_artifact,
        )

    def download_image(
        self,
        record_or_url: EvidenceRecord | str,
        *,
        index: int = 0,
    ) -> RetrievedArtifact:
        if index < 0:
            raise ProviderContentError("附图序号不能为负数")
        if isinstance(record_or_url, EvidenceRecord):
            link = str(record_or_url.raw_metadata.get("ops_fullimage_link") or "")
            page_count = int(record_or_url.raw_metadata.get("ops_fullimage_pages") or 0)
            page = index + 1
            if not link or page < 1 or page > page_count:
                raise ProviderContentError("记录没有指定序号的 OPS 文献页")
            url = f"{self.base_url}/rest-services/published-data/images/{link}"
        else:
            parsed = urlparse(str(record_or_url))
            ranges = parse_qs(parsed.query).get("Range") or []
            try:
                page = int(ranges[0])
            except (IndexError, TypeError, ValueError) as exc:
                raise ProviderContentError("OPS 文献页 URL 缺少 Range") from exc
            url = parsed._replace(query="", fragment="").geturl()
        response = self._ops_get(
            url,
            params={"Range": page},
            accept="application/pdf",
            allowed_media_types=("application/pdf",),
            max_response_bytes=self.max_image_response_bytes,
        )
        pdf = _response_content(response)
        if not pdf.lstrip().startswith(b"%PDF"):
            raise ProviderContentError("OPS 文献页未返回真实 PDF")
        png = _render_first_pdf_page(pdf)
        _validate_raster_image(
            png,
            "image/png",
            max_pixels=self.max_image_pixels,
        )
        return RetrievedArtifact(
            provider=self.provider_name,
            kind="image",
            source_url=f"{url}?Range={page}",
            media_type="image/png",
            content=png,
        )

    def download_pdf(self, record_or_url: EvidenceRecord | str) -> RetrievedArtifact:
        if isinstance(record_or_url, EvidenceRecord):
            link = str(record_or_url.raw_metadata.get("ops_fullimage_link") or "")
            page_count = int(record_or_url.raw_metadata.get("ops_fullimage_pages") or 0)
            if not link:
                raise ProviderContentError("记录没有 OPS 全文页面")
            if page_count < 1 or page_count > 5000:
                raise ProviderContentError("记录没有有效的 OPS 全文页数")
            url = f"{self.base_url}/rest-services/published-data/images/{link}"
        else:
            parsed = urlparse(str(record_or_url))
            ranges = parse_qs(parsed.query).get("Range") or []
            try:
                page_count = int(ranges[0])
            except (IndexError, TypeError, ValueError) as exc:
                raise ProviderContentError("OPS 全文 URL 缺少总页数") from exc
            url = parsed._replace(query="", fragment="").geturl()

        merged = fitz.open()
        page_hashes: list[str] = []
        page_bytes: list[int] = []
        try:
            for page in range(1, page_count + 1):
                response = self._ops_get(
                    url,
                    params={"Range": page},
                    accept="application/pdf",
                    allowed_media_types=("application/pdf",),
                )
                content = _response_content(response)
                self._track_retrieval_response(
                    name=f"document_page_{page}",
                    url=f"{url}?Range={page}",
                    response=response,
                    kind="provider_document_page_response",
                )
                if not content.lstrip().startswith(b"%PDF"):
                    raise ProviderContentError(
                        f"OPS 文献第 {page} 页未返回真实 PDF"
                    )
                try:
                    page_document = fitz.open(stream=content, filetype="pdf")
                except Exception as exc:
                    raise ProviderContentError(
                        f"OPS 文献第 {page} 页 PDF 无法解析"
                    ) from exc
                try:
                    if page_document.page_count != 1:
                        raise ProviderContentError(
                            f"OPS 文献第 {page} 页响应不是单页 PDF"
                        )
                    merged.insert_pdf(page_document)
                finally:
                    page_document.close()
                page_hashes.append(_sha256(content))
                page_bytes.append(len(content))
            if merged.page_count != page_count:
                raise ProviderContentError("OPS 全文合并后的页数与官方页数不一致")
            content = merged.tobytes(garbage=4, deflate=True)
        finally:
            merged.close()
        if not content.lstrip().startswith(b"%PDF"):
            raise ProviderContentError("OPS 全文合并未形成真实 PDF")
        self._last_full_document_manifest = {
            "source": "EPO OPS FullDocument page responses",
            "source_url": url,
            "page_count": page_count,
            "page_sha256": page_hashes,
            "page_bytes": page_bytes,
            "merged_pdf_sha256": _sha256(content),
            "merged_pdf_bytes": len(content),
        }
        self._last_retrieval_artifacts = tuple(
            item
            for item in self._last_retrieval_artifacts
            if not item.kind.startswith("provider_document_page_response_")
        )
        return RetrievedArtifact(
            provider=self.provider_name,
            kind="pdf",
            source_url=f"{url}?Range=1-{page_count}",
            media_type="application/pdf",
            content=content,
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
        return qualify_evidence(
            record,
            date_eligibility=eligibility,
            content_relevance_verified=content_relevance_verified,
            source_chain_verified=source_chain_verified,
            public_date_evidence=public_date_evidence,
            strict=strict,
        )

    def _coerce_lead(
        self,
        value: EvidenceRecord | Mapping[str, Any] | str,
    ) -> EvidenceRecord:
        if isinstance(value, EvidenceRecord):
            publication_number = _normalize_publication_number(
                value.publication_number
            )
            application_number = _epo_application_number_from_record(value)
            if not publication_number and not application_number:
                raise ValueError("EPO OPS lead 必须包含公开编号或申请编号")
            return value
        if isinstance(value, Mapping):
            publication_number = _normalize_publication_number(
                value.get("publication_number") or value.get("pn")
            )
            application_number = _normalize_application_number(
                value.get("application_number") or value.get("application_no")
            )
            title = str(value.get("title") or "")
        else:
            publication_number = _normalize_publication_number(value)
            application_number = None
            title = ""
        if not publication_number and not application_number:
            raise ValueError("EPO OPS lead 必须包含公开编号或申请编号")
        if publication_number:
            reference = _epo_epodoc_reference(publication_number)
            source_url = (
                f"{self.base_url}/rest-services/published-data/publication/"
                f"epodoc/{reference}/biblio"
            )
        else:
            reference = _epo_application_reference(str(application_number))
            source_url = (
                f"{self.base_url}/rest-services/published-data/application/"
                f"epodoc/{reference}/biblio"
            )
        return EvidenceRecord(
            provider=self.provider_name,
            source_type="patent",
            external_id=publication_number or str(application_number),
            title=title or publication_number or str(application_number),
            source_url=source_url,
            publication_number=publication_number,
            authority=_authority_from_number(publication_number or application_number),
            raw_metadata={"application_number": application_number},
        )

    def _resolve_publication_number(self, record: EvidenceRecord) -> str:
        application_number = _epo_application_number_from_record(record)
        if not application_number:
            raise ProviderContentError("EPO OPS lead 缺少公开编号和可解析申请编号")
        reference = _epo_application_reference(
            application_number,
            authority=record.authority,
        )
        url = (
            f"{self.base_url}/rest-services/published-data/application/"
            f"epodoc/{reference}/biblio"
        )
        response = self._ops_get(
            url,
            accept="application/exchange+xml",
            allowed_media_types=(
                "application/exchange+xml",
                "application/ops+xml",
                "application/xml",
                "text/xml",
            ),
        )
        self._track_retrieval_response(
            name="application_biblio",
            url=url,
            response=response,
        )
        raw = _response_content(response)
        self._last_application_resolution = (url, raw)
        candidates = [
            item
            for item in _epo_exchange_documents(
                _safe_xml_root(raw, provider="EPO OPS application biblio")
            )
            if _same_application_identity(
                application_number,
                item.get("application_number"),
                authority=record.authority,
            )
        ]
        publications = sorted(
            {
                normalized
                for item in candidates
                if (
                    normalized := _normalize_publication_number(
                        item.get("publication_number")
                    )
                )
            }
        )
        if len(publications) != 1:
            raise ProviderContentError(
                "EPO OPS 申请编号不能唯一解析为一个公开编号"
            )
        return publications[0]

    def _track_retrieval_response(
        self,
        *,
        name: str,
        url: str,
        response: ResponseLike,
        kind: str = "provider_retrieval_response",
    ) -> None:
        artifact = RetrievedArtifact(
            provider=self.provider_name,
            kind=kind,
            source_url=url,
            media_type=(
                _response_header(response, "content-type").split(";", 1)[0].strip()
                or "application/octet-stream"
            ),
            content=_response_content(response),
        )
        self._last_retrieval_artifacts = (
            *self._last_retrieval_artifacts,
            replace(
                artifact,
                kind=f"{kind}_{_safe_provider_artifact_name(name)}",
            ),
        )

    def _access_token_value(self, *, force_refresh: bool = False) -> str:
        now = time.monotonic()
        if (
            not force_refresh
            and self._access_token
            and now < self._access_token_expires_at
        ):
            return self._access_token
        credentials = base64.b64encode(
            f"{self.consumer_key}:{self.consumer_secret}".encode("utf-8")
        ).decode("ascii")
        response = self._post_form(
            self.token_url,
            data={"grant_type": "client_credentials"},
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            allowed_media_types=("application/json", "text/json"),
            max_response_bytes=64 * 1024,
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderContentError("EPO OPS OAuth 未返回有效 JSON") from exc
        token = str(payload.get("access_token") or "").strip()
        if not token or len(token) > 4096:
            raise ProviderContentError("EPO OPS OAuth 响应缺少有效 access_token")
        try:
            expires_in = max(60, min(int(payload.get("expires_in") or 1199), 86_400))
        except (TypeError, ValueError):
            expires_in = 1199
        self._access_token = token
        self._access_token_expires_at = now + max(1, expires_in - 30)
        return token

    def _ops_get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        accept: str,
        extra_headers: Mapping[str, str] | None = None,
        allowed_media_types: Sequence[str],
        max_response_bytes: int | None = None,
    ) -> ResponseLike:
        for attempt in range(2):
            token = self._access_token_value(force_refresh=attempt > 0)
            try:
                return self._get(
                    url,
                    params=params,
                    headers={
                        "Accept": accept,
                        "Authorization": f"Bearer {token}",
                        **dict(extra_headers or {}),
                    },
                    allowed_media_types=allowed_media_types,
                    max_response_bytes=max_response_bytes,
                )
            except ProviderRequestError as exc:
                if attempt == 0 and _provider_http_status(exc, {401}):
                    self._access_token = None
                    self._access_token_expires_at = 0.0
                    continue
                raise
        raise ProviderRequestError("EPO OPS 请求认证失败")


class GooglePatentsProvider(_BaseHttpProvider):
    """Google Patents XHR discovery plus auditable detail/artifact retrieval."""

    provider_name = "google_patents"

    def __init__(
        self,
        *,
        transport: Transport | Callable[..., ResponseLike] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_image_response_bytes: int = DEFAULT_MAX_IMAGE_RESPONSE_BYTES,
        max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        resolver: Callable[[str], Iterable[str]] | None = None,
        base_url: str = GOOGLE_PATENTS_BASE_URL,
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
        self.base_url = base_url.rstrip("/")

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
        if max_results < 1 or max_results > 100:
            raise ValueError("max_results 必须在 1..100 之间")

        inner_params: list[tuple[str, str]] = [("q", safe_query.text)]
        google_language = _GOOGLE_PATENTS_LANGUAGE_MAP.get(
            str(language or "").strip().lower()
        )
        if google_language:
            inner_params.append(("language", google_language))
        if country:
            inner_params.append(("country", country))
        normalized_before = _date_string(server_before)
        normalized_after = _date_string(server_after)
        normalized_filing_before = _date_string(filing_before)
        if normalized_after:
            inner_params.append(
                ("after", f"publication:{normalized_after.replace('-', '')}")
            )
        if normalized_before:
            # Discovery optimization only.  It is retained in provenance and
            # deliberately not used to discard or qualify any returned row.
            inner_params.append(
                ("before", f"publication:{normalized_before.replace('-', '')}")
            )
        if normalized_filing_before:
            inner_params.append(
                ("before", f"filing:{normalized_filing_before.replace('-', '')}")
            )

        try:
            response = self._get(
                f"{self.base_url}/xhr/query",
                # The current public frontend always sends ``exp``.  Omitting
                # it makes an otherwise valid XHR request return HTTP 500.
                params={"url": urlencode(inner_params), "exp": ""},
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                allowed_media_types=("application/json", "text/json"),
            )
            try:
                payload = response.json()
            except Exception:
                try:
                    payload = json.loads(_response_text(response))
                except (TypeError, ValueError) as exc:
                    raise ProviderContentError(
                        "Google Patents XHR 未返回有效 JSON"
                    ) from exc
        except (ProviderRequestError, ProviderContentError) as exc:
            return self._search_yahoo_fallback(
                query=safe_query,
                max_results=max_results,
                server_before=normalized_before,
                server_after=normalized_after,
                filing_before=normalized_filing_before,
                country=country,
                xhr_error=exc,
            )

        records: list[EvidenceRecord] = []
        seen: set[str] = set()
        for hit in _extract_google_hits(payload):
            record = self._lead_from_hit(
                hit,
                query=safe_query,
                server_before=normalized_before,
                server_after=normalized_after,
                filing_before=normalized_filing_before,
            )
            key = record.publication_number or record.external_id or record.source_url
            if not key or key in seen:
                continue
            seen.add(key)
            records.append(record)
            if len(records) >= max_results:
                break
        return records

    def _search_yahoo_fallback(
        self,
        *,
        query: SearchQuery,
        max_results: int,
        server_before: str | None,
        server_after: str | None,
        filing_before: str | None,
        country: str | None,
        xhr_error: Exception,
    ) -> list[EvidenceRecord]:
        country_code = str(country or "").strip().upper()
        site_scope = (
            f"site:patents.google.com/patent/{country_code}"
            if country_code
            else "site:patents.google.com/patent/"
        )
        search_terms = [site_scope, query.text]
        if server_after:
            search_terms.append(f"after:{server_after}")
        if server_before:
            search_terms.append(f"before:{server_before}")
        search_text = " ".join(search_terms)
        # The Google XHR endpoint is intermittently rate-limited on this
        # deployment's public egress.  Yahoo's public result page can discover
        # canonical Google Patents links without an API credential.  The
        # subprocess target is a fixed host and the query is supplied through a
        # subprocess stdin, so neither an attacker-controlled URL nor claim
        # text appears in the process list or on disk.  This is not a CAPTCHA bypass: any
        # challenge response is rejected below.
        html = _curl_get_search_html(
            provider_slug="yahoo_web_fallback",
            endpoint=YAHOO_SEARCH_URL,
            query_parameter="p",
            query=search_text,
            timeout_seconds=self.timeout_seconds,
            max_response_bytes=self.max_response_bytes,
        )
        _reject_search_access_challenge(html, provider="Yahoo")
        html_bytes = html.encode("utf-8")
        fallback_scope_degraded_reasons = (
            ["filing_date_filter_not_supported"] if filing_before else []
        )
        soup = BeautifulSoup(html, "lxml")
        records: list[EvidenceRecord] = []
        seen: set[str] = set()
        for container in soup.select("div.algo"):
            anchor = container.select_one(".compTitle > a[href]")
            if anchor is None:
                continue
            source_url = _yahoo_result_target(str(anchor.get("href") or ""))
            parsed = urlparse(source_url)
            if (
                parsed.scheme != "https"
                or (parsed.hostname or "").rstrip(".").lower()
                != "patents.google.com"
            ):
                continue
            path_parts = [unquote(item) for item in parsed.path.split("/") if item]
            if len(path_parts) < 2 or path_parts[0] != "patent":
                continue
            publication_number = _normalize_publication_number(path_parts[1])
            if not publication_number or publication_number in seen:
                continue
            seen.add(publication_number)
            canonical_url = f"{self.base_url}/patent/{publication_number}/en"
            title_node = anchor.select_one("h3")
            snippet_node = container.select_one(".compText")
            hit = {
                "publication_number": publication_number,
                "url": canonical_url,
                "title": (
                    title_node.get_text(" ", strip=True)
                    if title_node is not None
                    else anchor.get_text(" ", strip=True)
                ),
                "snippet": (
                    snippet_node.get_text(" ", strip=True)
                    if snippet_node is not None
                    else ""
                ),
            }
            record = self._lead_from_hit(
                hit,
                query=query,
                server_before=server_before,
                server_after=server_after,
                filing_before=filing_before,
            )
            records.append(
                replace(
                    record,
                    provenance={
                        **record.provenance,
                        "search_endpoint": YAHOO_SEARCH_URL,
                        "discovery_provider": "yahoo_web_fallback",
                        "fallback_reason": type(xhr_error).__name__,
                        "fallback_trigger": str(xhr_error)[:200],
                        "fallback_transport": "curl_trusted_host",
                        "fallback_query": search_text,
                        "fallback_country": country_code or None,
                        "fallback_response_sha256": hashlib.sha256(
                            html_bytes
                        ).hexdigest(),
                        "fallback_response_bytes": len(html_bytes),
                        "fallback_retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "fallback_scope_degraded": bool(
                            fallback_scope_degraded_reasons
                        ),
                        "fallback_scope_degraded_reasons": (
                            fallback_scope_degraded_reasons
                        ),
                        "search_snippet_is_lead_only": True,
                    },
                )
            )
            if len(records) >= max_results:
                break
        if not records and not _yahoo_no_results(soup):
            raise ProviderContentError(
                "Yahoo 专利链接检索响应结构不可识别，不能静默当作零命中"
            )
        return records

    def retrieve(
        self,
        lead: EvidenceRecord | Mapping[str, Any] | str,
    ) -> EvidenceRecord:
        record = self._coerce_lead(lead)
        response = self._get(
            record.source_url,
            headers={"Accept": "text/html,application/xhtml+xml"},
            allowed_media_types=("text/html", "application/xhtml+xml"),
        )
        raw = _response_content(response)
        html = _response_text(response)
        if not html.strip():
            raise ProviderContentError("Google Patents 详情页为空")

        soup = BeautifulSoup(html, "lxml")
        visible = _normalize_space(soup.get_text(" ", strip=True)).lower()
        if (
            any(marker in visible for marker in ("unusual traffic", "captcha", "验证码"))
            and not soup.select_one("section[itemprop='claims'], section[itemprop='description']")
        ):
            raise ProviderContentError("Google Patents 返回了验证页而非专利文献")

        final_url = _response_url(response) or record.source_url
        title = _first_nonempty(
            _meta_content(soup, "DC.title"),
            _selector_text(soup, "span[itemprop='title']"),
            _selector_text(soup, "h1"),
            record.title,
        )
        publication_number = _first_nonempty(
            _selector_text(soup, "dd[itemprop='publicationNumber']"),
            _meta_content(soup, "DC.identifier"),
            record.publication_number,
        )
        publication_number = _normalize_publication_number(publication_number)
        abstract = _first_nonempty(
            _selector_text(soup, "section[itemprop='abstract']"),
            _selector_text(soup, "div.abstract"),
            _meta_content(soup, "DC.description"),
            record.abstract,
        )
        description = _first_nonempty(
            _selector_text(soup, "section[itemprop='description']"),
            _selector_text(soup, "div.description"),
        )
        claims = _first_nonempty(
            _selector_text(soup, "section[itemprop='claims']"),
            _selector_text(soup, "div.claims"),
        )
        detail_publication_date = _first_nonempty(
            _selector_date(soup, "time[itemprop='publicationDate']"),
            _meta_content(soup, "DC.date"),
        )
        publication_date = detail_publication_date or record.publication_date
        filing_date = _first_nonempty(
            _selector_date(soup, "time[itemprop='filingDate']"),
            record.filing_date,
        )
        priority_date = _first_nonempty(
            _selector_date(soup, "time[itemprop='priorityDate']"),
            record.priority_date,
        )
        pdf_url = _first_nonempty(
            _meta_content(soup, "citation_pdf_url"),
            _selector_href(soup, "a[itemprop='pdfLink']"),
            _selector_href(soup, "a[href$='.pdf']"),
            record.pdf_url,
        )
        if pdf_url:
            pdf_url = urljoin(final_url, pdf_url)

        image_urls = _google_image_urls(soup, final_url)
        full_text = "\n\n".join(
            part
            for part in (
                title,
                f"Abstract\n{abstract}" if abstract else None,
                f"Description\n{description}" if description else None,
                f"Claims\n{claims}" if claims else None,
            )
            if part
        )
        substantive_text = " ".join(part or "" for part in (abstract, description, claims))
        retrieved = len(_normalize_space(substantive_text)) >= 80
        primary_artifact = (
            RetrievedArtifact(
                provider=self.provider_name,
                kind="html",
                source_url=final_url,
                media_type=(
                    _response_header(response, "content-type").split(";", 1)[0]
                    or "text/html"
                ),
                content=raw,
            )
            if retrieved
            else None
        )

        detail_metadata = {
            "http_status": int(getattr(response, "status_code", 200)),
            "final_url": final_url,
            "content_type": _response_header(response, "content-type"),
            "detail_content_length": len(raw),
        }
        provenance = {
            **dict(record.provenance),
            "detail_url": final_url,
            "retrieved_via": "google_patents_detail_html",
        }
        if _date_string(detail_publication_date):
            provenance["public_date_evidence"] = (
                "Google Patents detail metadata/time[itemprop=publicationDate]"
            )

        return replace(
            record,
            title=title or record.title,
            source_url=final_url,
            external_id=publication_number or record.external_id,
            publication_number=publication_number or record.publication_number,
            authority=_authority_from_number(publication_number) or record.authority,
            publication_date=_date_string(publication_date),
            filing_date=_date_string(filing_date),
            priority_date=_date_string(priority_date),
            abstract=abstract,
            description=description,
            claims=claims,
            full_text=full_text or None,
            pdf_url=pdf_url,
            image_urls=image_urls or record.image_urls,
            stage=EvidenceStage.RETRIEVED if retrieved else EvidenceStage.LEAD,
            content_sha256=(
                primary_artifact.content_sha256
                if primary_artifact
                else record.content_sha256
            ),
            retrieved_at=(primary_artifact.retrieved_at if primary_artifact else None),
            artifacts=(
                (*record.artifacts, primary_artifact.model_dump(include_content=False))
                if primary_artifact
                else record.artifacts
            ),
            primary_artifact=primary_artifact,
            raw_metadata={**dict(record.raw_metadata), "detail": detail_metadata},
            provenance=provenance,
            qualification_issues=() if retrieved else ("detail_full_text_not_retrieved",),
        )

    def download_pdf(self, record_or_url: EvidenceRecord | str) -> RetrievedArtifact:
        url = (
            record_or_url.pdf_url
            if isinstance(record_or_url, EvidenceRecord)
            else str(record_or_url)
        )
        if not url:
            raise ProviderContentError("记录没有 PDF URL")
        response = self._get(
            url,
            headers={"Accept": "application/pdf"},
            allowed_media_types=("application/pdf",),
        )
        content = _response_content(response)
        if not content.lstrip().startswith(b"%PDF"):
            raise ProviderContentError("PDF URL 未返回真实 PDF 文件")
        return RetrievedArtifact(
            provider=self.provider_name,
            kind="pdf",
            source_url=_response_url(response) or url,
            media_type=_response_header(response, "content-type") or "application/pdf",
            content=content,
        )

    def download_image(
        self,
        record_or_url: EvidenceRecord | str,
        *,
        index: int = 0,
    ) -> RetrievedArtifact:
        if isinstance(record_or_url, EvidenceRecord):
            try:
                url = record_or_url.image_urls[index]
            except IndexError as exc:
                raise ProviderContentError("记录没有指定序号的附图") from exc
        else:
            url = str(record_or_url)
        response = self._get(
            url,
            headers={"Accept": "image/*"},
            allowed_media_types=("image/*",),
            max_response_bytes=self.max_image_response_bytes,
        )
        content = _response_content(response)
        media_type = _response_header(response, "content-type")
        _validate_raster_image(
            content,
            media_type,
            max_pixels=self.max_image_pixels,
        )
        return RetrievedArtifact(
            provider=self.provider_name,
            kind="image",
            source_url=_response_url(response) or url,
            media_type=media_type or "application/octet-stream",
            content=content,
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
        """Run the local date engine; never trust XHR ``before`` as eligibility."""

        eligibility = classify_date_eligibility(
            critical_date=critical_date,
            publication_date=record.publication_date,
            candidate_filing_date=record.filing_date,
            candidate_priority_date=record.priority_date,
            target_publication_date=target_publication_date,
            source_type=record.source_type,
            publication_number=record.publication_number,
            authority=record.authority,
            cn_application_scope=cn_application_scope,
            public_date_verified=public_date_verified,
            candidate_filing_date_verified=candidate_filing_date_verified,
            candidate_priority_date_verified=candidate_priority_date_verified,
            target_publication_date_verified=target_publication_date_verified,
            date_channel=date_channel,
        )
        return qualify_evidence(
            record,
            date_eligibility=eligibility,
            content_relevance_verified=content_relevance_verified,
            source_chain_verified=source_chain_verified,
            public_date_evidence=public_date_evidence,
            strict=strict,
        )

    def _lead_from_hit(
        self,
        hit: Mapping[str, Any],
        *,
        query: SearchQuery,
        server_before: str | None,
        server_after: str | None,
        filing_before: str | None,
    ) -> EvidenceRecord:
        publication_number = _normalize_publication_number(
            _pick(hit, "publication_number", "publicationNumber", "grant_number")
        )
        identifier = str(_pick(hit, "id", "result_id", "identifier") or "").strip()
        if not publication_number:
            publication_number = _publication_from_identifier(identifier)
        source_url = str(_pick(hit, "url", "result_url", "link") or "").strip()
        if not source_url and identifier:
            source_url = identifier
        if source_url:
            source_url = urljoin(self.base_url + "/", source_url.lstrip("/"))
        elif publication_number:
            source_url = f"{self.base_url}/patent/{publication_number}/en"

        title = _clean_html(str(_pick(hit, "title", "name") or ""))
        snippet = _clean_html(str(_pick(hit, "snippet", "abstract") or ""))
        external_id = publication_number or identifier or source_url
        return EvidenceRecord(
            provider=self.provider_name,
            source_type="patent",
            external_id=external_id,
            title=title,
            source_url=source_url,
            stage=EvidenceStage.LEAD,
            publication_number=publication_number,
            authority=_authority_from_number(publication_number),
            language=str(_pick(hit, "language") or "") or None,
            publication_date=_date_string(
                _pick(hit, "publication_date", "publicationDate", "grant_date")
            ),
            filing_date=_date_string(_pick(hit, "filing_date", "filingDate")),
            priority_date=_date_string(_pick(hit, "priority_date", "priorityDate")),
            snippet=snippet or None,
            pdf_url=_absolute_optional(
                self.base_url,
                _pick(hit, "pdf", "pdf_url", "pdfUrl"),
            ),
            image_urls=tuple(
                url
                for url in (
                    _absolute_optional(
                        self.base_url,
                        _pick(hit, "thumbnail", "thumbnail_url", "image"),
                    ),
                )
                if url
            ),
            raw_metadata={"search_hit": _json_safe(dict(hit))},
            provenance={
                "search_query": query.model_dump(),
                "search_endpoint": f"{self.base_url}/xhr/query",
                "provider_server_before": server_before,
                "provider_server_after": server_after,
                "provider_filing_before": filing_before,
                "provider_server_before_is_discovery_only": True,
                "provider_server_after_is_discovery_only": True,
                "provider_filing_before_is_discovery_only": True,
            },
        )

    def _coerce_lead(
        self,
        value: EvidenceRecord | Mapping[str, Any] | str,
    ) -> EvidenceRecord:
        if isinstance(value, EvidenceRecord):
            return value
        if isinstance(value, Mapping):
            source_url = str(value.get("source_url") or value.get("url") or "")
            publication_number = _normalize_publication_number(
                value.get("publication_number") or value.get("publicationNumber")
            )
            if not source_url and publication_number:
                source_url = f"{self.base_url}/patent/{publication_number}/en"
            return EvidenceRecord(
                provider=self.provider_name,
                source_type="patent",
                external_id=str(
                    value.get("external_id") or publication_number or source_url
                ),
                title=str(value.get("title") or ""),
                source_url=source_url,
                publication_number=publication_number,
                authority=_authority_from_number(publication_number),
                publication_date=_date_string(value.get("publication_date")),
                filing_date=_date_string(value.get("filing_date")),
                priority_date=_date_string(value.get("priority_date")),
            )

        raw = str(value).strip()
        if not raw:
            raise ValueError("专利标识或 URL 不能为空")
        if raw.startswith(("http://", "https://")):
            source_url = raw
            publication_number = _publication_from_identifier(raw)
        else:
            publication_number = _normalize_publication_number(raw)
            source_url = f"{self.base_url}/patent/{publication_number}/en"
        return EvidenceRecord(
            provider=self.provider_name,
            source_type="patent",
            external_id=publication_number or source_url,
            title="",
            source_url=source_url,
            publication_number=publication_number,
            authority=_authority_from_number(publication_number),
        )


class ArxivProvider(_BaseHttpProvider):
    """arXiv Atom discovery with a separate PDF retrieval operation."""

    provider_name = "arxiv"

    def __init__(
        self,
        *,
        transport: Transport | Callable[..., ResponseLike] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        resolver: Callable[[str], Iterable[str]] | None = None,
        base_url: str = ARXIV_EXPORT_BASE_URL,
    ) -> None:
        super().__init__(
            transport=transport,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            max_redirects=max_redirects,
            resolver=resolver,
        )
        self.base_url = base_url.rstrip("/")

    def search(
        self,
        query: str | Mapping[str, Any] | SearchQuery,
        *,
        max_results: int = 20,
        start: int = 0,
        subject_terms: Sequence[str] | None = None,
        feature_terms: Sequence[str] | None = None,
    ) -> list[EvidenceRecord]:
        safe_query = SearchQuery.from_input(
            query,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        if max_results < 1 or max_results > 100:
            raise ValueError("max_results 必须在 1..100 之间")
        if start < 0:
            raise ValueError("start 不能为负数")

        response = self._get(
            f"{self.base_url}/api/query",
            params={
                "search_query": f"all:{safe_query.text}",
                "start": start,
                "max_results": max_results,
                "sortBy": "relevance",
            },
            headers={"Accept": "application/atom+xml"},
            allowed_media_types=(
                "application/atom+xml",
                "application/xml",
                "text/xml",
            ),
        )
        raw = _response_content(response)
        try:
            root = etree.fromstring(
                raw,
                parser=etree.XMLParser(resolve_entities=False, no_network=True, recover=False),
            )
        except etree.XMLSyntaxError as exc:
            raise ProviderContentError("arXiv 未返回有效 Atom XML") from exc

        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        records: list[EvidenceRecord] = []
        for entry in root.xpath("//atom:entry", namespaces=namespace):
            identifier = _xpath_text(entry, "./atom:id", namespace)
            title = _normalize_space(_xpath_text(entry, "./atom:title", namespace))
            summary = _normalize_space(_xpath_text(entry, "./atom:summary", namespace))
            published = _date_string(_xpath_text(entry, "./atom:published", namespace))
            updated = _date_string(_xpath_text(entry, "./atom:updated", namespace))
            authors = [
                _normalize_space(str(item))
                for item in entry.xpath("./atom:author/atom:name/text()", namespaces=namespace)
                if _normalize_space(str(item))
            ]
            links: list[dict[str, str]] = []
            pdf_url: str | None = None
            for link in entry.xpath("./atom:link", namespaces=namespace):
                data = {str(key): str(value) for key, value in link.attrib.items()}
                links.append(data)
                if data.get("title") == "pdf" or data.get("type") == "application/pdf":
                    pdf_url = data.get("href")

            external_id = _arxiv_id(identifier)
            source_url = identifier or f"https://arxiv.org/abs/{external_id}"
            records.append(
                EvidenceRecord(
                    provider=self.provider_name,
                    source_type="preprint",
                    external_id=external_id,
                    title=title,
                    source_url=source_url,
                    stage=EvidenceStage.LEAD,
                    publication_date=published,
                    abstract=summary,
                    snippet=summary,
                    pdf_url=pdf_url,
                    raw_metadata={
                        "authors": authors,
                        "updated": updated,
                        "links": links,
                        "categories": [
                            str(item)
                            for item in entry.xpath("./atom:category/@term", namespaces=namespace)
                        ],
                    },
                    provenance={
                        "search_query": safe_query.model_dump(),
                        "search_endpoint": f"{self.base_url}/api/query",
                        "public_date_evidence": "arXiv Atom entry/published",
                    },
                )
            )
        return records

    def download_pdf(self, record_or_url: EvidenceRecord | str) -> RetrievedArtifact:
        url = (
            record_or_url.pdf_url
            if isinstance(record_or_url, EvidenceRecord)
            else str(record_or_url)
        )
        if not url:
            raise ProviderContentError("arXiv 记录没有 PDF URL")
        response = self._get(
            url,
            headers={"Accept": "application/pdf"},
            allowed_media_types=("application/pdf",),
        )
        content = _response_content(response)
        if not content.lstrip().startswith(b"%PDF"):
            raise ProviderContentError("arXiv PDF URL 未返回真实 PDF 文件")
        return RetrievedArtifact(
            provider=self.provider_name,
            kind="pdf",
            source_url=_response_url(response) or url,
            media_type=_response_header(response, "content-type") or "application/pdf",
            content=content,
        )

    def retrieve(self, record: EvidenceRecord) -> EvidenceRecord:
        return record.with_artifact(self.download_pdf(record))

    def qualify(
        self,
        record: EvidenceRecord,
        *,
        critical_date: date | str,
        content_relevance_verified: bool,
        source_chain_verified: bool = True,
        public_date_verified: bool = True,
        public_date_evidence: str | None = None,
        strict: bool = False,
    ) -> EvidenceRecord:
        eligibility = classify_date_eligibility(
            critical_date=critical_date,
            publication_date=record.publication_date,
            source_type=record.source_type,
            public_date_verified=public_date_verified,
        )
        return qualify_evidence(
            record,
            date_eligibility=eligibility,
            content_relevance_verified=content_relevance_verified,
            source_chain_verified=source_chain_verified,
            public_date_evidence=public_date_evidence,
            strict=strict,
        )


class OpenAlexProvider(_BaseHttpProvider):
    """OpenAlex discovery adapter.

    OpenAlex metadata and OA URLs are useful discovery inputs, but neither a
    search hit nor a declared PDF URL proves that this system fetched the real
    document.  Consequently this adapter deliberately returns ``lead`` rows.
    """

    provider_name = "openalex"

    def search(
        self,
        query: str | Mapping[str, Any] | SearchQuery,
        *,
        max_results: int = 20,
        subject_terms: Sequence[str] | None = None,
        feature_terms: Sequence[str] | None = None,
    ) -> list[EvidenceRecord]:
        safe_query = SearchQuery.from_input(
            query,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        if max_results < 1 or max_results > 100:
            raise ValueError("max_results 必须在 1..100 之间")
        response = self._get(
            OPENALEX_API_URL,
            params={"search": safe_query.text, "per-page": max_results},
            headers={"Accept": "application/json"},
            allowed_media_types=("application/json", "text/json"),
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderContentError("OpenAlex 未返回有效 JSON") from exc
        rows = payload.get("results") if isinstance(payload, Mapping) else None
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            raise ProviderContentError("OpenAlex JSON 缺少 results")

        records: list[EvidenceRecord] = []
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            identifier = str(item.get("id") or "").strip()
            doi = _normalize_doi(item.get("doi"))
            primary_location = item.get("primary_location")
            primary_location = (
                primary_location if isinstance(primary_location, Mapping) else {}
            )
            best_oa_location = item.get("best_oa_location")
            best_oa_location = (
                best_oa_location if isinstance(best_oa_location, Mapping) else {}
            )
            locations_value = item.get("locations")
            locations = (
                [
                    location
                    for location in locations_value
                    if isinstance(location, Mapping)
                ]
                if isinstance(locations_value, Sequence)
                and not isinstance(locations_value, (str, bytes, bytearray))
                else []
            )
            selected_location: Mapping[str, Any] = {}
            selected_location_kind: str | None = None
            for kind, location in (
                ("best_oa_location", best_oa_location),
                ("primary_location", primary_location),
                *((f"locations[{index}]", location) for index, location in enumerate(locations)),
            ):
                candidate_pdf = _first_nonempty(location.get("pdf_url"))
                if candidate_pdf and urlparse(candidate_pdf).scheme in {"http", "https"}:
                    selected_location = location
                    selected_location_kind = kind
                    break
            if not selected_location:
                if best_oa_location:
                    selected_location = best_oa_location
                    selected_location_kind = "best_oa_location"
                elif primary_location:
                    selected_location = primary_location
                    selected_location_kind = "primary_location"
                elif locations:
                    selected_location = locations[0]
                    selected_location_kind = "locations[0]"
            open_access = item.get("open_access")
            open_access = open_access if isinstance(open_access, Mapping) else {}
            source_url = _first_nonempty(
                selected_location.get("landing_page_url"),
                primary_location.get("landing_page_url"),
                f"https://doi.org/{doi}" if doi else None,
                identifier,
            )
            if not source_url:
                continue
            pdf_url = _first_nonempty(selected_location.get("pdf_url"))
            title = _normalize_space(str(item.get("title") or ""))
            external_id = identifier.rsplit("/", 1)[-1] if identifier else (doi or source_url)
            abstract = _openalex_abstract(item.get("abstract_inverted_index"))
            records.append(
                EvidenceRecord(
                    provider=self.provider_name,
                    source_type="paper",
                    external_id=external_id,
                    title=title,
                    source_url=source_url,
                    stage=EvidenceStage.LEAD,
                    publication_date=_date_string(item.get("publication_date")),
                    abstract=abstract,
                    snippet=abstract,
                    pdf_url=pdf_url,
                    raw_metadata={
                        "openalex_id": identifier or None,
                        "doi": doi,
                        "type": item.get("type"),
                        "is_retracted": item.get("is_retracted"),
                        "open_access": _json_safe(open_access),
                        "primary_location": _json_safe(primary_location),
                        "best_oa_location": _json_safe(best_oa_location),
                        "selected_fulltext_location": _json_safe(selected_location),
                        "selected_fulltext_location_kind": selected_location_kind,
                    },
                    provenance={
                        "search_query": safe_query.model_dump(),
                        "search_endpoint": OPENALEX_API_URL,
                        "metadata_only": True,
                        "declared_pdf_url_is_lead_only": bool(pdf_url),
                        "open_access_declared": bool(
                            open_access.get("is_oa") or best_oa_location
                        ),
                        "fulltext_route": (
                            "openalex_declared_pdf" if pdf_url else "metadata_only"
                        ),
                    },
                )
            )
            if len(records) >= max_results:
                break
        return records


class CrossrefProvider(_BaseHttpProvider):
    """Crossref bibliographic discovery adapter; results remain leads."""

    provider_name = "crossref"

    def search(
        self,
        query: str | Mapping[str, Any] | SearchQuery,
        *,
        max_results: int = 20,
        subject_terms: Sequence[str] | None = None,
        feature_terms: Sequence[str] | None = None,
    ) -> list[EvidenceRecord]:
        safe_query = SearchQuery.from_input(
            query,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        if max_results < 1 or max_results > 100:
            raise ValueError("max_results 必须在 1..100 之间")
        response = self._get(
            CROSSREF_API_URL,
            params={
                "query.bibliographic": safe_query.text,
                "rows": max_results,
                "select": "DOI,title,URL,type,abstract,published-print,published-online,issued,link",
            },
            headers={"Accept": "application/json"},
            allowed_media_types=("application/json", "text/json"),
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderContentError("Crossref 未返回有效 JSON") from exc
        message = payload.get("message") if isinstance(payload, Mapping) else None
        rows = message.get("items") if isinstance(message, Mapping) else None
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            raise ProviderContentError("Crossref JSON 缺少 message.items")

        records: list[EvidenceRecord] = []
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            doi = _normalize_doi(item.get("DOI"))
            source_url = _first_nonempty(
                item.get("URL"),
                f"https://doi.org/{doi}" if doi else None,
            )
            if not source_url:
                continue
            title_value = item.get("title")
            if isinstance(title_value, Sequence) and not isinstance(
                title_value, (str, bytes, bytearray)
            ):
                title = _normalize_space(str(title_value[0] if title_value else ""))
            else:
                title = _normalize_space(str(title_value or ""))
            links = item.get("link")
            links = (
                list(links)
                if isinstance(links, Sequence) and not isinstance(links, (str, bytes, bytearray))
                else []
            )
            pdf_url: str | None = None
            for link in links:
                if not isinstance(link, Mapping):
                    continue
                media_type = str(link.get("content-type") or "").lower()
                candidate = str(link.get("URL") or "").strip()
                if candidate and (media_type == "application/pdf" or candidate.lower().endswith(".pdf")):
                    pdf_url = candidate
                    break
            abstract = _clean_html(str(item.get("abstract") or "")) or None
            records.append(
                EvidenceRecord(
                    provider=self.provider_name,
                    source_type="paper",
                    external_id=doi or source_url,
                    title=title,
                    source_url=source_url,
                    stage=EvidenceStage.LEAD,
                    publication_date=_crossref_date(item),
                    abstract=abstract,
                    snippet=abstract,
                    pdf_url=pdf_url,
                    raw_metadata={
                        "doi": doi,
                        "type": item.get("type"),
                        "links": _json_safe(links),
                    },
                    provenance={
                        "search_query": safe_query.model_dump(),
                        "search_endpoint": CROSSREF_API_URL,
                        "metadata_only": True,
                        "declared_pdf_url_is_lead_only": bool(pdf_url),
                    },
                )
            )
            if len(records) >= max_results:
                break
        return records


class WebEvidenceProvider(_BaseHttpProvider):
    """Public-web discovery and conservative real-page retrieval.

    Search snippets are never technical evidence.  Retrieval validates every
    URL hop and content boundary, then stores the fetched bytes' hash.  Embedded
    dates are retained as review evidence, not automatically treated as legal
    proof of public availability.
    """

    provider_name = "web_search"

    def __init__(
        self,
        *,
        transport: Transport | Callable[..., ResponseLike] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        resolver: Callable[[str], Iterable[str]] | None = None,
        search_url: str = DUCKDUCKGO_HTML_URL,
    ) -> None:
        super().__init__(
            transport=transport,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            max_redirects=max_redirects,
            resolver=resolver,
        )
        self.search_url = search_url

    def search(
        self,
        query: str | Mapping[str, Any] | SearchQuery,
        *,
        max_results: int = 20,
        subject_terms: Sequence[str] | None = None,
        feature_terms: Sequence[str] | None = None,
    ) -> list[EvidenceRecord]:
        safe_query = SearchQuery.from_input(
            query,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        if max_results < 1 or max_results > 100:
            raise ValueError("max_results 必须在 1..100 之间")
        response = self._get(
            self.search_url,
            params={"q": safe_query.text},
            headers={"Accept": "text/html,application/xhtml+xml"},
            allowed_media_types=("text/html", "application/xhtml+xml"),
        )
        soup = BeautifulSoup(_response_text(response), "lxml")
        if _looks_like_access_shell(soup):
            raise ProviderContentError("网页搜索返回登录、验证码或访问受限页面")
        records: list[EvidenceRecord] = []
        seen: set[str] = set()
        for result in soup.select(".result"):
            anchor = result.select_one("a.result__a")
            if not anchor:
                continue
            source_url = _unwrap_search_result_url(str(anchor.get("href") or ""))
            if not source_url or urlparse(source_url).scheme not in {"http", "https"}:
                continue
            if source_url in seen:
                continue
            seen.add(source_url)
            title = _normalize_space(anchor.get_text(" ", strip=True))
            snippet_node = result.select_one(".result__snippet")
            snippet = (
                _normalize_space(snippet_node.get_text(" ", strip=True))
                if snippet_node
                else None
            )
            records.append(
                EvidenceRecord(
                    provider=self.provider_name,
                    source_type=_infer_web_source_type(source_url, title),
                    external_id=f"web-{_sha256(source_url.encode())[:20]}",
                    title=title,
                    source_url=source_url,
                    stage=EvidenceStage.LEAD,
                    snippet=snippet,
                    pdf_url=source_url if _url_looks_like_pdf(source_url) else None,
                    provenance={
                        "search_query": safe_query.model_dump(),
                        "search_endpoint": self.search_url,
                        "search_snippet_is_lead_only": True,
                    },
                )
            )
            if len(records) >= max_results:
                break
        if not records and not _looks_like_explicit_no_results(soup):
            raise ProviderContentError(
                "网页搜索响应结构不可识别，不能把 DOM 漂移静默当作零命中"
            )
        return records

    def retrieve(
        self,
        lead: EvidenceRecord | Mapping[str, Any] | str,
    ) -> EvidenceRecord:
        record = self._coerce_lead(lead)
        response = self._get(
            record.source_url,
            headers={
                "Accept": "application/pdf,text/html,application/xhtml+xml,text/plain"
            },
            allowed_media_types=(
                "application/pdf",
                "text/html",
                "application/xhtml+xml",
                "text/plain",
            ),
        )
        content = _response_content(response)
        final_url = _response_url(response) or record.source_url
        media_type = _response_header(response, "content-type").split(";", 1)[0].lower()
        provenance = {
            **dict(record.provenance),
            "retrieved_via": "public_web_http",
            "retrieved_url": final_url,
        }
        response_metadata = {
            "http_status": int(getattr(response, "status_code", 200)),
            "final_url": final_url,
            "content_type": _response_header(response, "content-type"),
            "content_length": len(content),
        }

        if media_type == "application/pdf":
            if not content.lstrip().startswith(b"%PDF"):
                raise ProviderContentError("网页来源未返回真实 PDF 文件")
            artifact = RetrievedArtifact(
                provider=self.provider_name,
                kind="pdf",
                source_url=final_url,
                media_type=media_type,
                content=content,
            )
            retrieved = record.with_artifact(artifact)
            return replace(
                retrieved,
                source_url=final_url,
                pdf_url=final_url,
                raw_metadata={
                    **dict(record.raw_metadata),
                    "retrieval": response_metadata,
                },
                provenance=provenance,
            )

        text = _response_text(response)
        if media_type == "text/plain":
            if _looks_like_access_text(text):
                raise ProviderContentError("文本来源返回登录、验证码或访问受限内容")
            title = record.title
            full_text = _normalize_space(text)
            publication_date = None
            date_evidence = None
        else:
            soup = BeautifulSoup(text, "lxml")
            if _looks_like_access_shell(soup):
                raise ProviderContentError("网页返回登录、验证码或访问受限页面")
            for node in soup.select("script,style,noscript,template,svg"):
                node.decompose()
            title = _first_nonempty(
                _selector_text(soup, "title"),
                _selector_text(soup, "h1"),
                record.title,
            ) or record.title
            body = soup.select_one("main,article") or soup.body or soup
            full_text = _normalize_space(body.get_text(" ", strip=True))
            publication_date, date_evidence = _embedded_web_publication_date(soup)
        if len(full_text) < 60:
            raise ProviderContentError("网页正文不足，未取得可核验的真实内容")
        if date_evidence:
            provenance["public_date_evidence"] = date_evidence
        artifact = RetrievedArtifact(
            provider=self.provider_name,
            kind="text" if media_type == "text/plain" else "html",
            source_url=final_url,
            media_type=media_type,
            content=content,
        )
        retrieved = record.with_artifact(artifact)
        return replace(
            retrieved,
            title=title,
            source_url=final_url,
            source_type=_infer_web_source_type(final_url, title),
            stage=EvidenceStage.RETRIEVED,
            publication_date=publication_date,
            full_text=full_text,
            raw_metadata={
                **dict(record.raw_metadata),
                "retrieval": response_metadata,
            },
            provenance=provenance,
            qualification_issues=(),
        )

    def qualify(
        self,
        record: EvidenceRecord,
        *,
        critical_date: date | str,
        content_relevance_verified: bool,
        source_chain_verified: bool,
        public_date_verified: bool,
        public_date_evidence: str | None = None,
        strict: bool = False,
    ) -> EvidenceRecord:
        eligibility = classify_date_eligibility(
            critical_date=critical_date,
            publication_date=record.publication_date,
            source_type=record.source_type,
            public_date_verified=public_date_verified,
        )
        return qualify_evidence(
            record,
            date_eligibility=eligibility,
            content_relevance_verified=content_relevance_verified,
            source_chain_verified=source_chain_verified,
            public_date_evidence=public_date_evidence,
            strict=strict,
        )

    def _coerce_lead(
        self,
        value: EvidenceRecord | Mapping[str, Any] | str,
    ) -> EvidenceRecord:
        if isinstance(value, EvidenceRecord):
            return value
        if isinstance(value, Mapping):
            source_url = str(value.get("source_url") or value.get("url") or "").strip()
            if not source_url:
                raise ValueError("网页来源 URL 不能为空")
            title = str(value.get("title") or source_url)
            return EvidenceRecord(
                provider=self.provider_name,
                source_type=str(
                    value.get("source_type") or _infer_web_source_type(source_url, title)
                ),
                external_id=str(
                    value.get("external_id")
                    or f"web-{_sha256(source_url.encode())[:20]}"
                ),
                title=title,
                source_url=source_url,
            )
        source_url = str(value or "").strip()
        if not source_url:
            raise ValueError("网页来源 URL 不能为空")
        return EvidenceRecord(
            provider=self.provider_name,
            source_type=_infer_web_source_type(source_url, source_url),
            external_id=f"web-{_sha256(source_url.encode())[:20]}",
            title=source_url,
            source_url=source_url,
        )


@dataclass(frozen=True, slots=True)
class ProviderSearchBatch:
    records: tuple[EvidenceRecord, ...]
    complete: bool
    providers_attempted: tuple[str, ...]
    providers_succeeded: tuple[str, ...]
    errors: tuple[str, ...] = ()


class CompositeNplProvider:
    """Fan out NPL discovery while preserving partial-source failures."""

    provider_name = "composite_npl"

    def __init__(self, providers: Sequence[Any]) -> None:
        if not providers:
            raise ValueError("CompositeNplProvider 至少需要一个来源")
        self.providers = tuple(providers)
        self._providers_by_name = {
            str(getattr(provider, "provider_name", type(provider).__name__)): provider
            for provider in providers
        }

    def close(self) -> None:
        for provider in self.providers:
            try:
                provider.close()
            except Exception:
                # Closing one adapter must not hide another adapter's results.
                continue

    def search(
        self,
        query: str | Mapping[str, Any] | SearchQuery,
        *,
        max_results: int = 20,
        subject_terms: Sequence[str] | None = None,
        feature_terms: Sequence[str] | None = None,
    ) -> ProviderSearchBatch:
        if max_results < 1 or max_results > 100:
            raise ValueError("max_results 必须在 1..100 之间")
        # Query validity is shared by every NPL source. Validate once before
        # fan-out so one malformed query is not reported as four independent
        # provider failures.
        safe_query = SearchQuery.from_input(
            query,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        attempted: list[str] = []
        succeeded: list[str] = []
        errors: list[str] = []
        source_batches: list[list[EvidenceRecord]] = []
        per_source_limit = max(
            1,
            (max_results + len(self.providers) - 1) // len(self.providers),
        )
        for provider in self.providers:
            name = str(getattr(provider, "provider_name", type(provider).__name__))
            attempted.append(name)
            try:
                found = provider.search(
                    safe_query,
                    max_results=per_source_limit,
                )
            except ProviderError as exc:
                errors.append(f"{name}: {exc}")
                continue
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}")
                continue
            succeeded.append(name)
            bounded: list[EvidenceRecord] = []
            for record in found:
                bounded.append(record)
                if len(bounded) >= per_source_limit:
                    break
            source_batches.append(bounded)

        # Interleave successful sources so the global cap cannot be consumed
        # entirely by whichever adapter happens to be configured first.
        records: list[EvidenceRecord] = []
        seen: set[tuple[str, str]] = set()
        max_batch_size = max((len(batch) for batch in source_batches), default=0)
        for result_index in range(max_batch_size):
            for batch in source_batches:
                if result_index >= len(batch):
                    continue
                record = batch[result_index]
                doi = _normalize_doi(record.raw_metadata.get("doi"))
                key = (
                    ("doi", doi.lower())
                    if doi
                    else ("source", record.source_url or record.external_id)
                )
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
                if len(records) >= max_results:
                    break
            if len(records) >= max_results:
                break
        return ProviderSearchBatch(
            records=tuple(records),
            complete=not errors,
            providers_attempted=tuple(attempted),
            providers_succeeded=tuple(succeeded),
            errors=tuple(errors),
        )

    def provider_for(self, record: EvidenceRecord) -> Any | None:
        return self._providers_by_name.get(record.provider)

    def retrieval_plan_for(
        self,
        record: EvidenceRecord,
    ) -> tuple[Any | None, EvidenceRecord]:
        """Choose a real-document adapter without promoting metadata to evidence.

        OpenAlex and Crossref are discovery-only providers.  When either one
        declares an HTTP(S) PDF URL and the explicitly configured composite
        also contains the hardened public-web adapter, that adapter may fetch
        the declared file.  The bibliographic URL and discovery provenance are
        retained, and the result still requires independent public-date and
        technical-disclosure review before it can become qualified evidence.
        """

        adapter = self.provider_for(record)
        if record.provider not in {"openalex", "crossref"} or not record.pdf_url:
            return adapter, record
        parsed = urlparse(record.pdf_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return adapter, record
        web_adapter = self._providers_by_name.get("web_search")
        if not isinstance(web_adapter, WebEvidenceProvider):
            return adapter, record
        retrieval_record = replace(
            record,
            source_url=record.pdf_url,
            provenance={
                **dict(record.provenance),
                "bibliographic_source_url": record.source_url,
                "declared_pdf_url_used_for_retrieval": True,
                "declared_pdf_url_remains_lead_until_fetched": True,
            },
        )
        return web_adapter, retrieval_record


class ManualProvider:
    """First-class manual import provider using the same evidence gates."""

    provider_name = "manual"

    def __init__(self, documents: Iterable[EvidenceRecord | Mapping[str, Any]] | None = None):
        self._records: list[EvidenceRecord] = []
        for item in documents or ():
            self._records.append(self.ingest(item))

    def ingest(
        self,
        document: EvidenceRecord | Mapping[str, Any] | None = None,
        /,
        **kwargs: Any,
    ) -> EvidenceRecord:
        if isinstance(document, EvidenceRecord):
            return document
        values = dict(document or {})
        values.update(kwargs)

        content = values.pop("content", None)
        full_text = values.get("full_text")
        if content is None and full_text is not None:
            content = str(full_text).encode("utf-8")
        elif isinstance(content, str):
            if full_text is None:
                full_text = content
            content = content.encode("utf-8")
        elif content is not None and not isinstance(content, bytes):
            raise TypeError("manual content 必须是 str 或 bytes")

        source_url = str(values.get("source_url") or values.get("url") or "manual://import")
        external_id = str(values.get("external_id") or values.get("id") or "")
        if not external_id:
            seed = content or json.dumps(_json_safe(values), sort_keys=True).encode("utf-8")
            external_id = f"manual-{_sha256(seed)[:16]}"
        publication_date = _date_string(
            values.get("publication_date") or values.get("public_availability_date")
        )
        provenance = dict(values.get("provenance") or {})
        if values.get("public_date_evidence"):
            provenance["public_date_evidence"] = str(values["public_date_evidence"])
        provenance.setdefault("imported_via", "manual_provider")

        retrieved = bool(content)
        record = EvidenceRecord(
            provider=self.provider_name,
            source_type=str(values.get("source_type") or "manual_document"),
            external_id=external_id,
            title=str(values.get("title") or external_id),
            source_url=source_url,
            stage=EvidenceStage.RETRIEVED if retrieved else EvidenceStage.LEAD,
            publication_number=values.get("publication_number"),
            authority=values.get("authority"),
            language=values.get("language"),
            publication_date=publication_date,
            filing_date=_date_string(values.get("filing_date")),
            priority_date=_date_string(values.get("priority_date")),
            abstract=values.get("abstract"),
            snippet=values.get("snippet"),
            description=values.get("description"),
            claims=values.get("claims"),
            full_text=full_text,
            pdf_url=values.get("pdf_url"),
            image_urls=_terms(values.get("image_urls")),
            content_sha256=_sha256(content) if retrieved else None,
            retrieved_at=_now_iso() if retrieved else None,
            raw_metadata=_json_safe(dict(values.get("raw_metadata") or {})),
            provenance=provenance,
            qualification_issues=() if retrieved else ("document_content_not_imported",),
        )
        return record

    def ingest_file(
        self,
        path: str | Path,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvidenceRecord:
        source = Path(path).expanduser().resolve()
        content = source.read_bytes()
        values = dict(metadata or {})
        values.setdefault("title", source.name)
        values.setdefault("source_url", source.as_uri())
        values.setdefault("external_id", f"manual-{_sha256(content)[:16]}")
        values["content"] = content
        return self.ingest(values)

    def register(
        self,
        document: EvidenceRecord | Mapping[str, Any],
    ) -> EvidenceRecord:
        record = document if isinstance(document, EvidenceRecord) else self.ingest(document)
        self._records.append(record)
        return record

    def search(
        self,
        query: str | Mapping[str, Any] | SearchQuery,
        *,
        max_results: int = 20,
        subject_terms: Sequence[str] | None = None,
        feature_terms: Sequence[str] | None = None,
    ) -> list[EvidenceRecord]:
        safe_query = SearchQuery.from_input(
            query,
            subject_terms=subject_terms,
            feature_terms=feature_terms,
        )
        atoms = _query_atoms(safe_query.text)
        matched: list[EvidenceRecord] = []
        for record in self._records:
            haystack = _search_normalize(
                " ".join(
                    item or ""
                    for item in (
                        record.title,
                        record.abstract,
                        record.snippet,
                        record.full_text,
                    )
                )
            )
            if all(_search_normalize(atom) in haystack for atom in atoms):
                matched.append(record)
            if len(matched) >= max_results:
                break
        return matched

    def retrieve(
        self,
        document: EvidenceRecord | Mapping[str, Any],
    ) -> EvidenceRecord:
        return document if isinstance(document, EvidenceRecord) else self.ingest(document)

    def qualify(
        self,
        record: EvidenceRecord,
        *,
        critical_date: date | str,
        content_relevance_verified: bool,
        source_chain_verified: bool,
        public_date_verified: bool,
        public_date_evidence: str | None = None,
        cn_application_scope: bool | None = None,
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
            publication_number=record.publication_number,
            authority=record.authority,
            cn_application_scope=cn_application_scope,
            public_date_verified=public_date_verified,
            candidate_filing_date_verified=candidate_filing_date_verified,
            candidate_priority_date_verified=candidate_priority_date_verified,
            target_publication_date_verified=target_publication_date_verified,
            date_channel=date_channel,
        )
        return qualify_evidence(
            record,
            date_eligibility=eligibility,
            content_relevance_verified=content_relevance_verified,
            source_chain_verified=source_chain_verified,
            public_date_evidence=public_date_evidence,
            strict=strict,
        )


def _extract_google_hits(payload: Any) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            patent = node.get("patent")
            if isinstance(patent, Mapping):
                combined = {
                    key: value
                    for key, value in node.items()
                    if key not in {"patent", "result", "cluster"}
                }
                combined.update(patent)
                hits.append(combined)
                for key, value in node.items():
                    if key != "patent":
                        walk(value)
                return
            if _looks_like_google_hit(node):
                hits.append(dict(node))
            for value in node.values():
                walk(value)
        elif isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            for item in node:
                walk(item)

    walk(payload)
    return hits


def _looks_like_google_hit(node: Mapping[str, Any]) -> bool:
    keys = set(node)
    return bool(
        keys.intersection({"publication_number", "publicationNumber"})
        and keys.intersection({"title", "snippet", "id", "url"})
    )


def _google_image_urls(soup: BeautifulSoup, base_url: str) -> tuple[str, ...]:
    selectors = (
        "section[itemprop='abstract'] img",
        "section[itemprop='description'] img",
        "section[itemprop='claims'] img",
        "img.patent-full-image",
        "img[id^='img']",
        "img[itemprop='image']",
    )
    urls: list[str] = []
    for selector in selectors:
        for image in soup.select(selector):
            source = image.get("src") or image.get("data-src") or image.get("file")
            if not source or str(source).startswith("data:"):
                continue
            absolute = urljoin(base_url, str(source))
            lowered = absolute.lower()
            if any(marker in lowered for marker in ("logo", "favicon", "spinner", "avatar")):
                continue
            urls.append(absolute)
    return tuple(dict.fromkeys(urls))


def _selector_text(soup: BeautifulSoup, selector: str) -> str | None:
    node = soup.select_one(selector)
    return _normalize_space(node.get_text(" ", strip=True)) if node else None


def _selector_date(soup: BeautifulSoup, selector: str) -> str | None:
    node = soup.select_one(selector)
    if not node:
        return None
    return _date_string(node.get("datetime") or node.get("content") or node.get_text(strip=True))


def _selector_href(soup: BeautifulSoup, selector: str) -> str | None:
    node = soup.select_one(selector)
    return str(node.get("href") or "").strip() if node else None


def _meta_content(soup: BeautifulSoup, name: str) -> str | None:
    escaped = name.replace("'", "\\'")
    node = soup.select_one(f"meta[name='{escaped}']")
    return str(node.get("content") or "").strip() if node else None


def _resolve_public_host(hostname: str) -> tuple[str, ...]:
    addresses = {
        str(sockaddr[0]).split("%", 1)[0]
        for _family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(
            hostname,
            None,
            type=socket.SOCK_STREAM,
        )
        if sockaddr
    }
    # Clash and similar TUN clients can intentionally return RFC 2544
    # benchmark addresses for every public hostname.  Those addresses are
    # routing handles rather than the origin IP, so they cannot be accepted by
    # the SSRF gate or pinned as if they were public.  Resolve the same hostname
    # through a fixed HTTPS DNS service, then keep the existing per-hop global
    # address validation and TCP pinning.  Any genuine private/mixed DNS answer
    # still fails closed and never activates this fallback.
    parsed_addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for value in addresses:
        try:
            parsed_addresses.append(ipaddress.ip_address(value))
        except ValueError:
            return tuple(sorted(addresses))
    if parsed_addresses and all(
        isinstance(item, ipaddress.IPv4Address) and item in _FAKE_IP_NETWORK
        for item in parsed_addresses
    ):
        if hostname.rstrip(".").lower() in _TUN_FAKE_IP_TRUSTED_HOSTS:
            return tuple(sorted(addresses))
        return _resolve_via_trusted_doh(hostname)
    return tuple(sorted(addresses))


_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)
_CURL_BINARY = "/usr/bin/curl"
_CURL_STATUS_MARKER = b"\n__INVALIDITY_CURL_STATUS__="


def _reject_search_access_challenge(html: str, *, provider: str) -> None:
    lowered = html.lower()
    if any(
        marker in lowered
        for marker in (
            "captcha",
            "challenge-form",
            "verify you are human",
            "cf-turnstile",
        )
    ):
        raise ProviderContentError(f"{provider} 专利链接检索返回了访问验证页")


def _curl_get_search_html(
    *,
    provider_slug: str,
    endpoint: str,
    query_parameter: str,
    query: str,
    timeout_seconds: float,
    max_response_bytes: int,
) -> str:
    if (provider_slug, endpoint, query_parameter) != (
        "yahoo_web_fallback",
        YAHOO_SEARCH_URL,
        "p",
    ):
        raise ProviderRequestError("公开检索 curl 目标不在固定允许列表")
    try:
        timeout = max(1, int(timeout_seconds))
        result = subprocess.run(
            [
                _CURL_BINARY,
                # This must be curl's first option.  It prevents ~/.curlrc
                # from injecting redirects, proxies, credentials or -k.
                "--disable",
                "--proxy",
                "",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--max-redirs",
                "0",
                "-sS",
                "--connect-timeout",
                str(min(timeout, 20)),
                "--max-time",
                str(timeout),
                "--max-filesize",
                str(max_response_bytes),
                "-G",
                endpoint,
                "--data-urlencode",
                f"{query_parameter}@-",
                "-H",
                "Accept: text/html,application/xhtml+xml",
                "-H",
                f"User-Agent: {_BROWSER_USER_AGENT}",
                "--write-out",
                "\n__INVALIDITY_CURL_STATUS__=%{http_code}|%{content_type}",
            ],
            input=query.encode("utf-8"),
            capture_output=True,
            timeout=float(timeout) + 5.0,
            check=False,
            env={
                "HOME": "/nonexistent",
                "LC_ALL": "C",
                "NO_PROXY": "*",
                "PATH": "/usr/bin:/bin",
            },
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProviderRequestError(
            f"{provider_slug} 请求失败: {type(exc).__name__}"
        ) from exc

    marker_at = result.stdout.rfind(_CURL_STATUS_MARKER)
    if marker_at < 0:
        raise ProviderRequestError(
            f"{provider_slug} curl 失败: exit={result.returncode}"
        )
    body = result.stdout[:marker_at]
    metadata = result.stdout[marker_at + len(_CURL_STATUS_MARKER) :].decode(
        "ascii", errors="replace"
    )
    status_text, _, content_type = metadata.partition("|")
    try:
        status = int(status_text)
    except ValueError as exc:
        raise ProviderRequestError(f"{provider_slug} curl 状态无效") from exc
    if result.returncode != 0:
        if result.returncode == 63 or len(body) > max_response_bytes:
            raise ProviderContentError(f"{provider_slug} 响应超过大小上限")
        raise ProviderRequestError(
            f"{provider_slug} curl 失败: exit={result.returncode}"
        )
    if status < 200 or status >= 300:
        raise ProviderRequestError(f"{provider_slug} HTTP {status}")
    if not content_type.lower().startswith(("text/html", "application/xhtml+xml")):
        raise ProviderContentError(f"{provider_slug} 响应 MIME 类型不符合预期")
    if len(body) > max_response_bytes:
        raise ProviderContentError(f"{provider_slug} 响应超过大小上限")
    return body.decode("utf-8", errors="replace")


def _yahoo_result_target(href: str) -> str:
    parsed = urlparse(href.strip())
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme == "https" and hostname == "patents.google.com":
        return href.strip()
    if parsed.scheme != "https" or hostname != "r.search.yahoo.com":
        return ""
    match = re.search(r"(?:^|/)RU=([^/]+)(?:/|$)", parsed.path)
    return unquote(match.group(1)) if match else ""


def _yahoo_no_results(soup: BeautifulSoup) -> bool:
    text = _normalize_space(soup.get_text(" ", strip=True)).lower()
    return any(
        marker in text
        for marker in (
            "we did not find results for",
            "no results were found",
            "没有找到结果",
        )
    )


def _without_credentials(headers: Mapping[str, str] | None) -> dict[str, str]:
    blocked = {"authorization", "cookie", "proxy-authorization"}
    return {
        str(key): str(value)
        for key, value in dict(headers or {}).items()
        if str(key).lower() not in blocked
    }


def _provider_http_status(error: Exception, allowed: set[int]) -> bool:
    match = re.search(r"\bHTTP\s+(\d{3})\b", str(error))
    return bool(match and int(match.group(1)) in allowed)


def _safe_xml_root(content: bytes, *, provider: str) -> etree._Element:
    try:
        return etree.fromstring(
            content,
            parser=etree.XMLParser(
                resolve_entities=False,
                no_network=True,
                recover=False,
                huge_tree=False,
            ),
        )
    except etree.XMLSyntaxError as exc:
        raise ProviderContentError(f"{provider} 未返回有效 XML") from exc


def _epo_cql_query(
    query: SearchQuery,
    *,
    country: str | None,
    server_before: str | None,
    server_after: str | None,
) -> str:
    if _looks_like_publication_identifier(query.text):
        publication = _normalize_publication_number(query.text)
        if not publication:
            raise QueryValidationError("EPO OPS 公开编号无效")
        clauses = [f"pn={publication}"]
    else:
        atoms = tuple(dict.fromkeys(_query_atoms(query.text)))[:12]
        if not atoms:
            raise QueryValidationError("EPO OPS 检索式没有可安全编码的术语")
        phrase = " ".join(item[:64] for item in atoms)
        clauses = [f'ta all "{phrase}"']

        classifications = [
            cleaned
            for value in query.classification_terms
            if (
                cleaned := re.sub(
                    r"[^A-Za-z0-9/]",
                    "",
                    str(value).upper(),
                )[:32]
            )
        ][:4]
        if classifications:
            clauses.append(
                "(" + " or ".join(f"cl={item}" for item in classifications) + ")"
            )
        citations = [
            normalized
            for value in query.citation_terms
            if (normalized := _normalize_publication_number(value))
        ][:4]
        if citations:
            clauses.append(
                "(" + " or ".join(f"ct={item}" for item in citations) + ")"
            )

    if country:
        clauses.append(f"pn={country}")
    if server_after:
        clauses.append(f"pd > {server_after.replace('-', '')}")
    if server_before:
        clauses.append(f"pd < {server_before.replace('-', '')}")
    cql = " and ".join(clauses)
    if len(cql) > 2048:
        raise QueryValidationError("EPO OPS CQL 超过长度上限")
    return cql


def _epo_exchange_documents(root: etree._Element) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for document in root.xpath("//*[local-name()='exchange-document']"):
        country = str(document.get("country") or "").strip().upper()
        doc_number = str(document.get("doc-number") or "").strip().upper()
        kind = str(document.get("kind") or "").strip().upper()
        publication_ids = document.xpath(
            ".//*[local-name()='publication-reference']/"
            "*[local-name()='document-id' and @document-id-type='docdb']"
        )
        publication_id = publication_ids[0] if publication_ids else None
        if publication_id is not None:
            country = _first_xpath_text(
                publication_id,
                "./*[local-name()='country']/text()",
            ).upper() or country
            doc_number = _first_xpath_text(
                publication_id,
                "./*[local-name()='doc-number']/text()",
            ).upper() or doc_number
            kind = _first_xpath_text(
                publication_id,
                "./*[local-name()='kind']/text()",
            ).upper() or kind
            publication_date = _date_string(
                _first_xpath_text(
                    publication_id,
                    "./*[local-name()='date']/text()",
                )
            )
        else:
            publication_date = None
        publication_number = _normalize_publication_number(
            f"{country}{doc_number}{kind}"
        )
        if not publication_number:
            continue

        titles = document.xpath(".//*[local-name()='invention-title']")
        selected_title = next(
            (
                item
                for item in titles
                if str(item.get("lang") or "").lower() == "en"
            ),
            titles[0] if titles else None,
        )
        title = _element_text(selected_title)
        language = (
            str(selected_title.get("lang") or "").strip().lower()
            if selected_title is not None
            else None
        )
        abstracts = document.xpath(".//*[local-name()='abstract']")
        selected_abstract = next(
            (
                item
                for item in abstracts
                if str(item.get("lang") or "").lower() == "en"
            ),
            abstracts[0] if abstracts else None,
        )
        abstract = _element_text(selected_abstract)

        application_ids = document.xpath(
            ".//*[local-name()='application-reference']/"
            "*[local-name()='document-id' and @document-id-type='docdb']"
        )
        if not application_ids:
            application_ids = document.xpath(
                ".//*[local-name()='application-reference']/"
                "*[local-name()='document-id']"
            )
        application_id = application_ids[0] if application_ids else None
        filing_date = (
            _date_string(
                _first_xpath_text(
                    application_id,
                    "./*[local-name()='date']/text()",
                )
            )
            if application_id is not None
            else None
        )
        application_number = (
            _normalize_application_number(
                "".join(
                    (
                        _first_xpath_text(
                            application_id,
                            "./*[local-name()='country']/text()",
                        )
                        or country,
                        _first_xpath_text(
                            application_id,
                            "./*[local-name()='doc-number']/text()",
                        ),
                        _first_xpath_text(
                            application_id,
                            "./*[local-name()='kind']/text()",
                        ),
                    )
                )
            )
            if application_id is not None
            else None
        )
        priority_dates = sorted(
            {
                parsed
                for raw in document.xpath(
                    ".//*[local-name()='priority-claim']/*[local-name()='date']/text()"
                    " | .//*[local-name()='priority-claim']/*[local-name()='document-id']"
                    "/*[local-name()='date']/text()"
                )
                if (parsed := _date_string(str(raw)))
            }
        )
        results.append(
            {
                "publication_number": publication_number,
                "authority": country or _authority_from_number(publication_number),
                "publication_date": publication_date,
                "filing_date": filing_date,
                "priority_date": priority_dates[0] if priority_dates else None,
                "application_number": application_number,
                "title": title,
                "abstract": abstract,
                "language": language,
                "family_id": str(document.get("family-id") or "").strip() or None,
            }
        )
    return results


def _first_xpath_text(element: etree._Element, expression: str) -> str:
    values = element.xpath(expression)
    return _normalize_space(str(values[0])) if values else ""


def _element_text(element: etree._Element | None) -> str:
    if element is None:
        return ""
    return _normalize_space(" ".join(str(item) for item in element.itertext()))


def _epo_fulltext_text(root: etree._Element, section: str) -> str:
    nodes = root.xpath(f"//*[local-name()='{section}']")
    return "\n".join(
        value
        for node in nodes
        if (value := _element_text(node))
    )


def _epo_fullimage_info(root: etree._Element) -> tuple[str | None, int]:
    nodes = root.xpath(
        "//*[local-name()='document-instance' and @desc='FullDocument']"
    )
    if not nodes:
        return None, 0
    link = str(nodes[0].get("link") or "").strip().strip("/")
    # OPS live responses may return either the path relative to the images
    # collection (``EP/.../fullimage``) or the collection-prefixed path
    # (``published-data/images/EP/.../fullimage``).  Store one canonical
    # relative form so the request builder never duplicates the collection.
    for prefix in (
        "rest-services/published-data/images/",
        "published-data/images/",
    ):
        if link.startswith(prefix):
            link = link[len(prefix) :]
            break
    if not link or ".." in link or not re.fullmatch(r"[A-Za-z0-9._/-]+", link):
        raise ProviderContentError("EPO OPS images inquiry 返回了不安全的文献路径")
    try:
        page_count = int(nodes[0].get("number-of-pages") or 0)
    except (TypeError, ValueError):
        page_count = 0
    if page_count < 1 or page_count > 5000:
        raise ProviderContentError("EPO OPS images inquiry 页数无效")
    return link, page_count


def _epo_response_bundle(responses: Mapping[str, bytes]) -> bytes:
    payload = {
        "format": "epo_ops_response_bundle_v1",
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


def _epo_epodoc_reference(publication_number: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", str(publication_number).upper())
    match = re.fullmatch(r"([A-Z]{2})(\d+)([A-Z]\d?)?", normalized)
    if not match:
        raise ProviderContentError("EPO OPS 公开编号格式无效")
    country, number, kind = match.groups()
    return f"{country}{number}.{kind}" if kind else f"{country}{number}"


def _epo_base_reference(publication_number: str) -> str:
    reference = _epo_epodoc_reference(publication_number)
    return reference.split(".", 1)[0]


def _normalize_application_number(
    value: Any,
    *,
    authority: str | None = None,
) -> str | None:
    raw = re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())
    if not raw:
        return None
    if not re.match(r"^[A-Z]{2}", raw):
        normalized_authority = str(authority or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", normalized_authority):
            return None
        raw = f"{normalized_authority}{raw}"
    if not re.fullmatch(r"[A-Z]{2}\d{4,}[A-Z]\d?|[A-Z]{2}\d{4,}", raw):
        return None
    return raw


def _epo_application_reference(
    application_number: str,
    *,
    authority: str | None = None,
) -> str:
    normalized = _normalize_application_number(
        application_number,
        authority=authority,
    )
    if not normalized:
        raise ProviderContentError("EPO OPS 申请编号格式无效")
    match = re.fullmatch(r"([A-Z]{2})(\d+)([A-Z]\d?)?", normalized)
    if not match:
        raise ProviderContentError("EPO OPS 申请编号格式无效")
    country, number, kind = match.groups()
    return f"{country}{number}.{kind}" if kind else f"{country}{number}"


def _epo_application_number_from_record(record: EvidenceRecord) -> str | None:
    return _normalize_application_number(
        record.raw_metadata.get("application_number")
        or record.raw_metadata.get("application_no"),
        authority=record.authority,
    )


def _same_application_identity(
    expected: Any,
    actual: Any,
    *,
    authority: str | None = None,
) -> bool:
    normalized_expected = _normalize_application_number(
        expected,
        authority=authority,
    )
    normalized_actual = _normalize_application_number(
        actual,
        authority=authority,
    )
    if not normalized_expected or not normalized_actual:
        return False
    expected_match = re.fullmatch(r"([A-Z]{2}\d+)(?:[A-Z]\d?)?", normalized_expected)
    actual_match = re.fullmatch(r"([A-Z]{2}\d+)(?:[A-Z]\d?)?", normalized_actual)
    return bool(
        expected_match
        and actual_match
        and expected_match.group(1) == actual_match.group(1)
    )


def _safe_provider_artifact_name(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "_", str(value or "").strip().lower())
    return normalized.strip("_") or "response"


def _ops_quota_headers(response: ResponseLike) -> dict[str, str]:
    names = (
        "x-individualquotaperhour-used",
        "x-registeredquotaperweek-used",
        "x-throttling-control",
    )
    return {
        name: value
        for name in names
        if (value := _response_header(response, name).strip())
    }


def _render_first_pdf_page(content: bytes) -> bytes:
    try:
        document = fitz.open(stream=content, filetype="pdf")
        try:
            if document.page_count < 1:
                raise ProviderContentError("OPS PDF 页面为空")
            # Keep the encoded pixel dimensions aligned with the decoder used
            # by _validate_raster_image.  Rendering at 2x embeds a higher DPI
            # that PyMuPDF normalizes on decode and caused a false mismatch.
            pixmap = document.load_page(0).get_pixmap(
                matrix=fitz.Matrix(1, 1),
                alpha=False,
            )
            return pixmap.tobytes("png")
        finally:
            document.close()
    except ProviderContentError:
        raise
    except Exception as exc:
        raise ProviderContentError("OPS PDF 页面损坏或无法渲染") from exc


def _is_tun_fake_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(str(value))
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and address in _FAKE_IP_NETWORK


@lru_cache(maxsize=256)
def _resolve_via_trusted_doh(hostname: str) -> tuple[str, ...]:
    answers: set[str] = set()
    try:
        with httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=10.0,
            headers={
                "Accept": "application/dns-json",
                "User-Agent": "patent-invalidity-test/0.1 (+secure-dns)",
            },
        ) as client:
            for record_type in ("A", "AAAA"):
                response = client.get(
                    TRUSTED_DOH_URL,
                    params={"name": hostname, "type": record_type},
                )
                response.raise_for_status()
                if len(response.content) > 64 * 1024:
                    raise OSError("trusted DNS response exceeded size limit")
                payload = response.json()
                if not isinstance(payload, Mapping) or int(payload.get("Status", -1)) != 0:
                    continue
                for item in payload.get("Answer") or []:
                    if not isinstance(item, Mapping) or item.get("type") not in {1, 28}:
                        continue
                    value = str(item.get("data") or "").strip()
                    try:
                        answers.add(str(ipaddress.ip_address(value)))
                    except ValueError:
                        continue
    except (httpx.HTTPError, ValueError, TypeError, OSError) as exc:
        raise OSError("trusted public DNS lookup failed") from exc
    if not answers:
        raise socket.gaierror(f"trusted public DNS returned no address for {hostname}")
    return tuple(sorted(answers))


def _content_length(response: ResponseLike) -> int | None:
    raw = _response_header(response, "content-length").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _media_type_matches(actual: str, expected: str) -> bool:
    if expected.endswith("/*"):
        return actual.startswith(expected[:-1])
    return actual == expected


def _normalize_doi(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", raw, flags=re.I)
    return raw.strip() or None


def _openalex_abstract(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    indexed: list[tuple[int, str]] = []
    for word, positions in value.items():
        if not isinstance(positions, Sequence) or isinstance(
            positions, (str, bytes, bytearray)
        ):
            continue
        for position in positions:
            try:
                number = int(position)
            except (TypeError, ValueError):
                continue
            if 0 <= number <= 100_000:
                indexed.append((number, str(word)))
    if not indexed:
        return None
    indexed.sort(key=lambda item: item[0])
    return _normalize_space(" ".join(word for _position, word in indexed)) or None


def _crossref_date(item: Mapping[str, Any]) -> str | None:
    for key in ("published-online", "published-print", "published", "issued"):
        value = item.get(key)
        if not isinstance(value, Mapping):
            continue
        parts = value.get("date-parts")
        if not isinstance(parts, Sequence) or not parts:
            continue
        first = parts[0]
        if not isinstance(first, Sequence) or isinstance(first, (str, bytes, bytearray)):
            continue
        try:
            year = int(first[0])
            month = int(first[1]) if len(first) > 1 else 1
            day = int(first[2]) if len(first) > 2 else 1
            return date(year, month, day).isoformat()
        except (IndexError, TypeError, ValueError):
            continue
    return None


def _unwrap_search_result_url(value: str) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    absolute = urljoin("https://duckduckgo.com/", raw)
    parsed = urlparse(absolute)
    if parsed.hostname and parsed.hostname.lower().endswith("duckduckgo.com"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(str(target[0])).strip() or None
    return absolute


def _url_looks_like_pdf(value: str) -> bool:
    return urlparse(value).path.lower().endswith(".pdf")


def _infer_web_source_type(source_url: str, title: str) -> str:
    haystack = f"{urlparse(source_url).path} {title}".lower()
    categories = (
        ("datasheet", ("datasheet", "data-sheet", "spec sheet")),
        ("manual", ("manual", "handbook", "service-guide", "user-guide")),
        ("standard", ("standard", "specification", "rfc", "iso-", "iec-")),
        ("forum", ("forum", "thread", "discussion", "discourse", "stackoverflow")),
        ("software_documentation", ("docs", "documentation", "readme", "changelog")),
    )
    for source_type, markers in categories:
        if any(marker in haystack for marker in markers):
            return source_type
    return "web_page"


def _looks_like_access_shell(soup: BeautifulSoup) -> bool:
    visible = _normalize_space(soup.get_text(" ", strip=True)).lower()
    title = _normalize_space(soup.title.get_text(" ", strip=True) if soup.title else "").lower()
    has_password = bool(soup.select_one("input[type='password']"))
    has_bot_challenge = bool(
        soup.select_one(
            "#challenge-form, #img-form, .anomaly-modal__modal, "
            "[data-testid='anomaly-modal']"
        )
    ) or any(
        "anomaly.js" in str(form.get("action") or "").lower()
        for form in soup.find_all("form")
    )
    markers = (
        "captcha",
        "unusual traffic",
        "access denied",
        "verify you are human",
        "unfortunately, bots use",
        "complete the following challenge",
        "select all squares containing",
        "sign in to continue",
        "log in to continue",
        "enable javascript and cookies",
        "付费阅读",
        "访问被拒绝",
        "验证码",
    )
    return (
        has_password
        or has_bot_challenge
        or any(marker in f"{title} {visible[:2000]}" for marker in markers)
    )


def _looks_like_access_text(value: str) -> bool:
    visible = _normalize_space(str(value or "")).lower()[:4000]
    markers = (
        "captcha",
        "unusual traffic",
        "access denied",
        "verify you are human",
        "unfortunately, bots use",
        "complete the following challenge",
        "select all squares containing",
        "sign in to continue",
        "log in to continue",
        "enable javascript and cookies",
        "付费阅读",
        "访问被拒绝",
        "验证码",
    )
    return any(marker in visible for marker in markers)


def _looks_like_explicit_no_results(soup: BeautifulSoup) -> bool:
    if soup.select_one(".no-results, #no-results, .results--no-results"):
        return True
    visible = _normalize_space(soup.get_text(" ", strip=True)).lower()
    markers = (
        "no results.",
        "no results found",
        "did not match any results",
        "未找到结果",
        "没有找到相关结果",
    )
    return any(marker in visible for marker in markers)


def _embedded_web_publication_date(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    accepted_keys = {
        "article:published_time",
        "datepublished",
        "date",
        "dc.date",
        "dc.date.issued",
        "citation_publication_date",
        "citation_date",
        "pubdate",
    }
    for node in soup.find_all("meta"):
        key = _normalize_space(
            str(node.get("property") or node.get("name") or node.get("itemprop") or "")
        ).lower()
        if key not in accepted_keys:
            continue
        raw = str(node.get("content") or "").strip()
        parsed = _date_string(raw)
        if parsed:
            return parsed, f"embedded HTML metadata: {key}"
    for node in soup.select("time[datetime], time[itemprop='datePublished']"):
        raw = str(node.get("datetime") or node.get("content") or node.get_text(strip=True))
        parsed = _date_string(raw)
        if parsed:
            return parsed, "embedded HTML time element"
    return None, None


def _xpath_text(node: etree._Element, expression: str, namespaces: Mapping[str, str]) -> str:
    values = node.xpath(f"{expression}/text()", namespaces=namespaces)
    return str(values[0]).strip() if values else ""


def _response_content(response: ResponseLike) -> bytes:
    content = getattr(response, "content", b"")
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    return bytes(content or b"")


def _response_text(response: ResponseLike) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    content = _response_content(response)
    return content.decode("utf-8", errors="replace")


def _response_header(response: ResponseLike, name: str) -> str:
    headers = getattr(response, "headers", {}) or {}
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value)
    return ""


def _response_url(response: ResponseLike) -> str:
    value = getattr(response, "url", "")
    return str(value) if value else ""


def _url_origin(value: str) -> tuple[str, str, int]:
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").rstrip(".").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProviderRequestError("URL 端口无效") from exc
    effective_port = port or (443 if scheme == "https" else 80)
    return scheme, hostname, effective_port


def _pinned_request_target(
    value: str,
    address: str,
) -> tuple[str, str, str | None]:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname:
        raise ProviderRequestError("URL 缺少主机名")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProviderRequestError("URL 端口无效") from exc
    parsed_address = ipaddress.ip_address(address)
    pinned_host = f"[{parsed_address}]" if parsed_address.version == 6 else str(parsed_address)
    pinned_netloc = f"{pinned_host}:{port}" if port is not None else pinned_host
    host_header_name = f"[{hostname}]" if ":" in hostname else hostname
    host_header = f"{host_header_name}:{port}" if port is not None else host_header_name
    pinned_url = parsed._replace(netloc=pinned_netloc).geturl()
    sni_hostname = hostname if parsed.scheme.lower() == "https" else None
    return pinned_url, host_header, sni_hostname


def _terms(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values: Iterable[Any] = re.split(r"[,;，；\n]+", value)
    elif isinstance(value, Iterable):
        values = value
    else:
        values = (value,)
    return tuple(
        normalized
        for item in values
        if (normalized := _normalize_space(str(item)))
    )


def _dedupe_terms(values: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = _normalize_space(value)
        key = _search_normalize(normalized)
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return tuple(result)


def _query_atoms(text: str) -> tuple[str, ...]:
    cleaned = re.sub(r"\b(?:AND|OR|NOT)\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"[()\[\]{}:+\-*/=,;，；|]+", " ", cleaned)
    raw_atoms = re.findall(r"[\u3400-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9._-]+", cleaned)
    stop = {"and", "or", "not", "the", "of", "for", "with", "all"}
    return tuple(atom for atom in raw_atoms if atom.lower() not in stop)


def _looks_like_publication_identifier(text: str) -> bool:
    compact = re.sub(r"[\s\-_/]", "", text).upper()
    return bool(re.fullmatch(r"(?:CN|WO|US|EP|JP|KR)[A-Z]?\d{4,}[A-Z]\d?", compact))


def _search_normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]", "", value.lower())


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _clean_html(value: str) -> str:
    if not value:
        return ""
    return _normalize_space(BeautifulSoup(value, "lxml").get_text(" ", strip=True))


def _normalize_publication_number(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw:
        return None
    # DC.identifier can include prefixes such as "patent:CN...".
    match = re.search(r"(?:CN|WO|US|EP|JP|KR)[\s\-_/]*[A-Z0-9.\-/]+", raw)
    if match:
        raw = match.group(0)
    normalized = re.sub(r"[^A-Z0-9]", "", raw)
    return normalized or None


def _publication_from_identifier(value: str) -> str | None:
    match = re.search(
        r"(?:patent/)?((?:CN|WO|US|EP|JP|KR)[A-Z0-9.\-_/]+?)(?:/|$)",
        value.upper(),
    )
    return _normalize_publication_number(match.group(1)) if match else None


def _authority_from_number(value: str | None) -> str | None:
    match = re.match(r"([A-Z]{2})", str(value or "").upper())
    return match.group(1) if match else None


def _absolute_optional(base_url: str, value: Any) -> str | None:
    raw = str(value or "").strip()
    return urljoin(base_url + "/", raw) if raw else None


def _pick(values: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        if value not in (None, "", []):
            return value
    return None


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        normalized = _normalize_space(str(value or ""))
        if normalized:
            return normalized
    return None


def _date_string(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed is not None else None


def _arxiv_id(value: str) -> str:
    parsed = urlparse(value)
    raw = parsed.path.rsplit("/", 1)[-1] if parsed.path else value
    return re.sub(r"v\d+$", "", raw)


def _looks_like_image(content: bytes, media_type: str) -> bool:
    try:
        actual_type, _width, _height = _raster_image_metadata(content)
    except ProviderContentError:
        return False
    declared = (media_type or "").split(";", 1)[0].strip().lower()
    aliases = {"image/jpg": "image/jpeg"}
    return not declared or aliases.get(declared, declared) == actual_type


def _validate_raster_image(
    content: bytes,
    media_type: str,
    *,
    max_pixels: int,
) -> None:
    actual_type, width, height = _raster_image_metadata(content)
    declared = (media_type or "").split(";", 1)[0].strip().lower()
    aliases = {"image/jpg": "image/jpeg"}
    if declared and aliases.get(declared, declared) != actual_type:
        raise ProviderContentError("附图 MIME 与实际格式不一致")
    if width < 1 or height < 1 or width > 32_768 or height > 32_768:
        raise ProviderContentError("附图尺寸无效或超过上限")
    if width * height > max_pixels:
        raise ProviderContentError("附图解码像素超过上限")
    try:
        try:
            # Direct image decoding preserves the file's intrinsic pixels.
            # Opening a PNG as a one-page document applies its physical DPI
            # and can scale a valid image before this comparison.
            pixmap = fitz.Pixmap(content)
            decoded_width, decoded_height = pixmap.width, pixmap.height
        except Exception:
            filetype = actual_type.split("/", 1)[1]
            document = fitz.open(stream=content, filetype=filetype)
            try:
                if document.page_count < 1:
                    raise ProviderContentError("附图没有可解码页面")
                pixmap = document.load_page(0).get_pixmap(alpha=False)
                decoded_width, decoded_height = pixmap.width, pixmap.height
            finally:
                document.close()
        if decoded_width != width or decoded_height != height:
            raise ProviderContentError("附图头部尺寸与解码尺寸不一致")
    except ProviderContentError:
        raise
    except Exception as exc:
        raise ProviderContentError("附图文件损坏或无法安全解码") from exc


def _raster_image_metadata(content: bytes) -> tuple[str, int, int]:
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        return (
            "image/png",
            int.from_bytes(content[16:20], "big"),
            int.from_bytes(content[20:24], "big"),
        )
    if content.startswith((b"GIF87a", b"GIF89a")) and len(content) >= 10:
        return (
            "image/gif",
            int.from_bytes(content[6:8], "little"),
            int.from_bytes(content[8:10], "little"),
        )
    if content.startswith(b"\xff\xd8\xff"):
        width, height = _jpeg_dimensions(content)
        return "image/jpeg", width, height
    if (
        len(content) >= 30
        and content.startswith(b"RIFF")
        and content[8:12] == b"WEBP"
    ):
        width, height = _webp_dimensions(content)
        return "image/webp", width, height
    raise ProviderContentError("附图 URL 未返回支持的 PNG/JPEG/GIF/WebP 图片")


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    index = 2
    while index + 4 <= len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        while index < len(content) and content[index] == 0xFF:
            index += 1
        if index >= len(content):
            break
        marker = content[index]
        index += 1
        if marker in {0x01, *range(0xD0, 0xDA)}:
            continue
        if index + 2 > len(content):
            break
        segment_length = int.from_bytes(content[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(content):
            break
        if marker in sof_markers and segment_length >= 7:
            height = int.from_bytes(content[index + 3:index + 5], "big")
            width = int.from_bytes(content[index + 5:index + 7], "big")
            return width, height
        index += segment_length
    raise ProviderContentError("JPEG 缺少有效尺寸信息")


def _webp_dimensions(content: bytes) -> tuple[int, int]:
    chunk = content[12:16]
    if chunk == b"VP8X" and len(content) >= 30:
        width = 1 + int.from_bytes(content[24:27], "little")
        height = 1 + int.from_bytes(content[27:30], "little")
        return width, height
    if chunk == b"VP8 " and len(content) >= 30 and content[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(content[26:28], "little") & 0x3FFF
        height = int.from_bytes(content[28:30], "little") & 0x3FFF
        return width, height
    if chunk == b"VP8L" and len(content) >= 25 and content[20] == 0x2F:
        bits = int.from_bytes(content[21:25], "little")
        width = 1 + (bits & 0x3FFF)
        height = 1 + ((bits >> 14) & 0x3FFF)
        return width, height
    raise ProviderContentError("WebP 缺少有效尺寸信息")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"sha256": _sha256(value), "content_length": len(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


__all__ = [
    "ArxivProvider",
    "CompositeNplProvider",
    "CrossrefProvider",
    "EpoOpsProvider",
    "EvidenceRecord",
    "EvidenceStage",
    "GooglePatentsProvider",
    "ManualProvider",
    "OpenAlexProvider",
    "ProviderContentError",
    "ProviderError",
    "ProviderRequestError",
    "ProviderSearchBatch",
    "QueryValidationError",
    "RetrievedArtifact",
    "SearchQuery",
    "WebEvidenceProvider",
    "qualify_evidence",
]
