"""Exact-document patent retrieval chain.

Patsnap P002 remains the discovery source.  EPO OPS is tried first for the
same publication, and Patsnap P020 is used only when OPS explicitly has no
retrievable document for that exact lead.
"""

from __future__ import annotations

from dataclasses import replace
import re
from typing import Any, Mapping

from .patsnap import PatsnapApiError, PatsnapProvider
from .providers import (
    EpoOpsProvider,
    EvidenceRecord,
    EvidenceStage,
    ProviderError,
    ProviderRequestError,
    RetrievedArtifact,
)


_EPO_NO_DOCUMENT_ISSUES = frozenset({"ops_fulltext_not_available"})
_HTTP_STATUS = re.compile(r"\bHTTP\s+(\d{3})\b")


class EpoFirstPatsnapPdfRetrievalProvider:
    """Retrieve one P002 lead from OPS, then P020 only on explicit absence."""

    provider_name = "epo_ops_then_patsnap_p020"

    def __init__(
        self,
        *,
        epo_provider: EpoOpsProvider,
        patsnap_provider: PatsnapProvider,
        close_patsnap: bool,
    ) -> None:
        self.epo_provider = epo_provider
        self.patsnap_provider = patsnap_provider
        self.close_patsnap = bool(close_patsnap)
        self._last_retrieval_artifacts: tuple[RetrievedArtifact, ...] = ()

    @property
    def last_retrieval_artifacts(self) -> tuple[RetrievedArtifact, ...]:
        return self._last_retrieval_artifacts

    def __enter__(self) -> "EpoFirstPatsnapPdfRetrievalProvider":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def close(self) -> None:
        epo_close = getattr(self.epo_provider, "close", None)
        if callable(epo_close):
            epo_close()
        if self.close_patsnap:
            patsnap_close = getattr(self.patsnap_provider, "close", None)
            if callable(patsnap_close):
                patsnap_close()

    def qualify(self, record: EvidenceRecord, **kwargs: Any) -> EvidenceRecord:
        # Qualification is deterministic and provider-neutral.  Delegating to
        # the OPS adapter preserves the existing workflow call surface.
        return self.epo_provider.qualify(record, **kwargs)

    def retrieve(
        self,
        lead: EvidenceRecord | Mapping[str, Any] | str,
    ) -> EvidenceRecord:
        self._last_retrieval_artifacts = ()
        original = self.patsnap_provider._coerce_lead(lead)
        attempts: list[dict[str, Any]] = []
        try:
            epo_result = self.epo_provider.retrieve(original)
        except ProviderRequestError as exc:
            self._capture_epo_artifacts()
            if _http_status(exc) != 404:
                raise
            attempts.append(
                {
                    "provider": "epo_ops",
                    "status": "not_found",
                    "reason_code": "epo_publication_not_found",
                }
            )
            fallback_base = original
        else:
            self._capture_epo_artifacts()
            if epo_result.stage is not EvidenceStage.LEAD:
                return _with_attempts(
                    epo_result,
                    attempts=(
                        {
                            "provider": "epo_ops",
                            "status": "retrieved",
                            "reason_code": "epo_document_retrieved",
                        },
                    ),
                    retrieval_provider="epo_ops",
                )
            issues = set(epo_result.qualification_issues)
            if not issues.intersection(_EPO_NO_DOCUMENT_ISSUES):
                return _with_attempts(
                    epo_result,
                    attempts=(
                        {
                            "provider": "epo_ops",
                            "status": "unavailable",
                            "reason_code": "epo_non_fallback_state",
                        },
                    ),
                    retrieval_provider=None,
                )
            attempts.append(
                {
                    "provider": "epo_ops",
                    "status": "no_coverage",
                    "reason_code": "ops_fulltext_not_available",
                }
            )
            fallback_base = epo_result

        try:
            pdf = self.patsnap_provider.download_pdf(fallback_base)
        except ProviderError as exc:
            self._capture_patsnap_artifacts()
            attempts.append(
                {
                    "provider": "patsnap_p020",
                    "status": "unavailable",
                    "reason_code": "patsnap_p020_unavailable",
                    "error_type": type(exc).__name__,
                    **_patsnap_error_codes(exc),
                }
            )
            return replace(
                _with_attempts(
                    fallback_base,
                    attempts=tuple(attempts),
                    retrieval_provider=None,
                ),
                qualification_issues=tuple(
                    dict.fromkeys(
                        (
                            *fallback_base.qualification_issues,
                            "patsnap_p020_unavailable",
                        )
                    )
                ),
            )
        self._capture_patsnap_artifacts()
        attempts.append(
            {
                "provider": "patsnap_p020",
                "status": "retrieved",
                "reason_code": "patsnap_p020_pdf_retrieved",
            }
        )
        provenance = {
            **dict(fallback_base.provenance),
            "discovery_provider": (
                fallback_base.provenance.get("discovery_provider") or "patsnap"
            ),
            "retrieval_provider": "patsnap_p020",
            "retrieval_attempts": attempts,
            "p020_identity": {
                "patent_id": str(
                    fallback_base.raw_metadata.get("patent_id")
                    or fallback_base.external_id
                ),
                "publication_number": fallback_base.publication_number,
                "match": "exact",
                "replace_by_related": False,
            },
        }
        return replace(
            fallback_base,
            provider="patsnap",
            source_url=pdf.source_url,
            stage=EvidenceStage.RETRIEVED,
            content_sha256=pdf.content_sha256,
            retrieved_at=pdf.retrieved_at,
            primary_artifact=pdf,
            provenance=provenance,
            qualification_issues=tuple(
                issue
                for issue in fallback_base.qualification_issues
                if issue not in _EPO_NO_DOCUMENT_ISSUES
            ),
        )

    def _capture_epo_artifacts(self) -> None:
        self._last_retrieval_artifacts = _merge_artifacts(
            self._last_retrieval_artifacts,
            tuple(getattr(self.epo_provider, "last_retrieval_artifacts", ()) or ()),
        )

    def _capture_patsnap_artifacts(self) -> None:
        self._last_retrieval_artifacts = _merge_artifacts(
            self._last_retrieval_artifacts,
            tuple(getattr(self.patsnap_provider, "last_media_artifacts", ()) or ()),
        )


def _with_attempts(
    record: EvidenceRecord,
    *,
    attempts: tuple[Mapping[str, Any], ...],
    retrieval_provider: str | None,
) -> EvidenceRecord:
    provenance = {
        **dict(record.provenance),
        "retrieval_attempts": [dict(item) for item in attempts],
    }
    if retrieval_provider:
        provenance["retrieval_provider"] = retrieval_provider
    else:
        provenance.pop("retrieval_provider", None)
    return replace(record, provenance=provenance)


def _http_status(exc: Exception) -> int | None:
    match = _HTTP_STATUS.search(str(exc))
    return int(match.group(1)) if match else None


def _patsnap_error_codes(exc: ProviderError) -> dict[str, int]:
    if not isinstance(exc, PatsnapApiError):
        return {}
    result: dict[str, int] = {}
    if exc.status_code is not None:
        result["status_code"] = int(exc.status_code)
    if exc.error_code is not None:
        result["error_code"] = int(exc.error_code)
    return result


def _merge_artifacts(
    existing: tuple[RetrievedArtifact, ...],
    additions: tuple[RetrievedArtifact, ...],
) -> tuple[RetrievedArtifact, ...]:
    merged = list(existing)
    keys = {
        (item.kind, item.content_sha256, item.source_url)
        for item in existing
    }
    for item in additions:
        key = (item.kind, item.content_sha256, item.source_url)
        if key in keys:
            continue
        merged.append(item)
        keys.add(key)
    return tuple(merged)


__all__ = ["EpoFirstPatsnapPdfRetrievalProvider"]
