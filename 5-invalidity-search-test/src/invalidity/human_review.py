from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .artifacts import ArtifactStore
from .config import Settings
from .contracts import CONTRACT_VERSION
from .source_snapshot import SafeFetcher, SourceSnapshotError, read_allowed_file


class HumanReviewCommandRequest(BaseModel):
    """Concurrency envelope shared by every human-review command."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["v1"] = CONTRACT_VERSION
    expected_investigation_state_version: int = Field(ge=0)
    expected_claim_state_versions: dict[str, int] = Field(min_length=1, max_length=500)
    expected_review_revision: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=200)
    reason: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def normalize_expected_claims(self) -> "HumanReviewCommandRequest":
        normalized: dict[str, int] = {}
        for raw_id, raw_version in self.expected_claim_state_versions.items():
            claim_id = str(raw_id).strip()
            if not claim_id:
                raise ValueError("expected_claim_state_versions 不能包含空 claim ID")
            version = int(raw_version)
            if version < 0:
                raise ValueError("expected claim state version 不能小于 0")
            normalized[claim_id] = version
        if len(normalized) != len(self.expected_claim_state_versions):
            raise ValueError("expected_claim_state_versions 不能包含重复 claim ID")
        self.expected_claim_state_versions = normalized
        self.reason = self.reason.strip()
        return self


class DateEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=500)
    locator: str | None = Field(default=None, max_length=2_000)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=100)


class DeclaredDateFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_availability_date: date | None = None
    publication_date: date | None = None
    filing_date: date | None = None
    priority_date: date | None = None
    source_type: str | None = Field(default=None, max_length=100)
    publication_number: str | None = Field(default=None, max_length=200)
    authority: str | None = Field(default=None, max_length=100)
    cn_application_scope: bool | None = None
    date_channel: Literal[
        "ordinary_prior_art", "cn_conflicting_application"
    ] = "ordinary_prior_art"


class EvidenceImportRequest(HumanReviewCommandRequest):
    """Import real source bytes without accepting any legal eligibility flags."""

    claim_investigation_ids: list[str] = Field(min_length=1, max_length=500)
    canonical_key: str = Field(min_length=1, max_length=500)
    document_type: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=1_000)
    language: str | None = Field(default=None, max_length=50)
    source_path: str | None = Field(default=None, min_length=1, max_length=4_000)
    source_url: str | None = Field(default=None, min_length=1, max_length=4_000)
    expected_source_sha256: str = Field(
        min_length=64, max_length=64, pattern=r"^[0-9A-Fa-f]{64}$"
    )
    expected_source_byte_size: int = Field(gt=0)
    expected_source_mime_type: Literal["application/pdf"] = "application/pdf"
    publication_number: str | None = Field(default=None, max_length=200)
    authority: str | None = Field(default=None, max_length=100)
    declared_date_facts: DeclaredDateFacts | None = None
    date_evidence: dict[str, DateEvidenceReference] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_one_source(self) -> "EvidenceImportRequest":
        if bool(self.source_path) == bool(self.source_url):
            raise ValueError("source_path 与 source_url 必须且只能提供一个")
        normalized = [str(value).strip() for value in self.claim_investigation_ids]
        if any(not value for value in normalized):
            raise ValueError("claim_investigation_ids 不能包含空值")
        if len(set(normalized)) != len(normalized):
            raise ValueError("claim_investigation_ids 不能重复")
        if set(normalized) != set(self.expected_claim_state_versions):
            raise ValueError(
                "expected_claim_state_versions 必须覆盖且只覆盖目标 claim"
            )
        self.claim_investigation_ids = normalized
        self.expected_source_sha256 = self.expected_source_sha256.lower()
        return self


class ExpectedQualification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qualification_id: str = Field(min_length=1, max_length=100)
    assessment_version: int = Field(ge=1)


class DocumentDateConfirmationRequest(HumanReviewCommandRequest):
    """Human-confirmed facts; deterministic rules alone derive eligibility."""

    decision: Literal["confirm_facts", "exclude", "reopen_review"] = "confirm_facts"
    document_id: str = Field(min_length=1, max_length=100)
    document_version_id: str = Field(min_length=1, max_length=100)
    claim_investigation_ids: list[str] = Field(min_length=1, max_length=500)
    expected_qualifications: dict[str, ExpectedQualification] = Field(
        min_length=1, max_length=500
    )
    public_availability_date: date | None = None
    publication_date: date | None = None
    filing_date: date | None = None
    priority_date: date | None = None
    source_type: str = Field(min_length=1, max_length=100)
    publication_number: str | None = Field(default=None, max_length=200)
    authority: str | None = Field(default=None, max_length=100)
    cn_application_scope: bool | None = None
    date_channel: Literal[
        "ordinary_prior_art", "cn_conflicting_application"
    ] = "ordinary_prior_art"
    date_evidence: dict[str, DateEvidenceReference] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_claims(self) -> "DocumentDateConfirmationRequest":
        normalized = [str(value).strip() for value in self.claim_investigation_ids]
        if any(not value for value in normalized):
            raise ValueError("claim_investigation_ids 不能包含空值")
        if len(set(normalized)) != len(normalized):
            raise ValueError("claim_investigation_ids 不能重复")
        expected_claims = set(self.expected_claim_state_versions)
        if set(normalized) != expected_claims:
            raise ValueError(
                "expected_claim_state_versions 必须覆盖且只覆盖目标 claim"
            )
        if set(normalized) != set(self.expected_qualifications):
            raise ValueError("expected_qualifications 必须覆盖且只覆盖目标 claim")
        if self.decision == "confirm_facts" and self.public_availability_date is None:
            raise ValueError("confirm_facts 必须提供 public_availability_date")
        supplied_facts = {
            key
            for key in (
                "public_availability_date",
                "publication_date",
                "filing_date",
                "priority_date",
            )
            if getattr(self, key) is not None
        }
        missing_evidence = supplied_facts.difference(self.date_evidence)
        if missing_evidence:
            raise ValueError(
                "每个日期事实都必须提供证据引用: " + ", ".join(sorted(missing_evidence))
            )
        self.claim_investigation_ids = normalized
        return self


@dataclass(frozen=True, slots=True)
class FrozenEvidence:
    uri: str
    sha256: str
    byte_size: int
    mime_type: str
    original_uri: str
    source_url: str
    provider: str
    extracted_text: str
    rendered_images: tuple[dict[str, Any], ...]

    def repository_payload(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "mime_type": self.mime_type,
            "original_uri": self.original_uri,
            "source_url": self.source_url,
            "provider": self.provider,
            "extracted_text": self.extracted_text,
            "rendered_images": [dict(item) for item in self.rendered_images],
        }

    def public_receipt(self) -> dict[str, Any]:
        return {
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "mime_type": self.mime_type,
            "source_url": self.source_url,
            "provider": self.provider,
            "rendered_image_count": len(self.rendered_images),
            "text_extracted": bool(self.extracted_text.strip()),
        }


class EvidenceFreezer:
    """Freeze an imported source under the investigation's isolated root."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = ArtifactStore(settings.artifact_root, settings.environment)
        self.fetcher = SafeFetcher(
            timeout_seconds=float(settings.source_fetch_timeout_seconds),
            max_redirects=settings.source_max_redirects,
        )

    def freeze(
        self,
        *,
        investigation_id: str,
        source_path: str | None,
        source_url: str | None,
        expected_source_sha256: str,
        expected_source_byte_size: int,
        expected_source_mime_type: str,
    ) -> FrozenEvidence:
        if source_path:
            content, mime_type, resolved = read_allowed_file(
                source_path,
                allowed_roots=self.settings.allowed_source_roots,
                max_bytes=self.settings.source_max_bytes,
                code_prefix="EVIDENCE_IMPORT",
            )
            original_uri = str(resolved)
            public_source_url = f"manual-upload://{resolved.name}"
            provider = "human_import_local"
        else:
            assert source_url is not None
            fetched = self.fetcher.fetch(
                source_url,
                max_bytes=self.settings.source_max_bytes,
                code_prefix="EVIDENCE_IMPORT",
                user_agent="patent-invalidity/0.1 (human-evidence-import)",
            )
            content = fetched.content
            mime_type = fetched.media_type
            original_uri = source_url
            public_source_url = fetched.final_url
            provider = "human_import_url"

        # I4-S is deliberately multimodal: a candidate must provide both
        # machine-readable text and rendered document pages.  Until a trusted
        # OCR/text-to-page pipeline is part of the import boundary, PDF is the
        # only input format that can satisfy both conditions.  Rejecting the
        # other MIME types here avoids accepting an import that is guaranteed
        # to fail later in a worker.
        if not content.lstrip().startswith(b"%PDF"):
            raise SourceSnapshotError(
                "EVIDENCE_IMPORT_PDF_REQUIRED",
                "人工导入目前只接受可提取文本的 PDF 文件",
                status_code=422,
            )
        mime_type = "application/pdf"
        sha256 = hashlib.sha256(content).hexdigest()
        expected_mime = str(expected_source_mime_type or "").split(";", 1)[0].lower()
        if (
            sha256 != str(expected_source_sha256 or "").strip().lower()
            or len(content) != int(expected_source_byte_size)
            or expected_mime != mime_type
        ):
            raise SourceSnapshotError(
                "EVIDENCE_IMPORT_RECEIPT_MISMATCH",
                "待导入文件与上传回执不一致，请重新上传后再提交",
                status_code=409,
            )
        try:
            extracted_text = self.store.extract_pdf_text(
                content,
                max_pdf_bytes=self.settings.source_max_bytes,
            )
        except Exception as exc:
            raise SourceSnapshotError(
                "EVIDENCE_IMPORT_PDF_INVALID",
                "PDF 无法安全解析，请重新导出后再导入",
                status_code=422,
            ) from exc
        if not extracted_text.strip():
            raise SourceSnapshotError(
                "EVIDENCE_IMPORT_OCR_REQUIRED",
                "PDF 没有可提取文本，请先生成带 OCR 文本层的 PDF",
                status_code=422,
            )

        artifact = self.store.write_bytes(
            investigation_id,
            f"evidence-imports/{sha256}.pdf",
            content,
            artifact_type="human_imported_evidence",
            mime_type=mime_type,
        )
        try:
            rendered_images = tuple(
                item.model_dump()
                for item in self.store.render_pdf_images(
                    investigation_id,
                    artifact.uri,
                    f"evidence-imports/{sha256}/pages",
                    max_images=2,
                    max_pdf_bytes=self.settings.source_max_bytes,
                )
            )
        except Exception as exc:
            raise SourceSnapshotError(
                "EVIDENCE_IMPORT_PDF_RENDER_FAILED",
                "PDF 页面无法安全渲染，请重新导出后再导入",
                status_code=422,
            ) from exc
        if not rendered_images:
            raise SourceSnapshotError(
                "EVIDENCE_IMPORT_PDF_RENDER_FAILED",
                "PDF 没有可用于多模态比对的页面图像",
                status_code=422,
            )
        return FrozenEvidence(
            uri=artifact.uri,
            sha256=artifact.sha256,
            byte_size=artifact.byte_size,
            mime_type=artifact.mime_type,
            original_uri=original_uri,
            source_url=public_source_url,
            provider=provider,
            extracted_text=extracted_text,
            rendered_images=rendered_images,
        )


__all__ = [
    "DateEvidenceReference",
    "DeclaredDateFacts",
    "DocumentDateConfirmationRequest",
    "EvidenceFreezer",
    "EvidenceImportRequest",
    "ExpectedQualification",
    "FrozenEvidence",
    "HumanReviewCommandRequest",
]
