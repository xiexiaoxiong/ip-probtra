from __future__ import annotations

import dataclasses
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field


REPORT_CONTRACT_VERSION = "v1"

_SENSITIVE_KEY = re.compile(
    r"(?:^|_)(?:api_?key|access_?key|authorization|cookie|database_?url|"
    r"password|private_?key|secret|session_?cookie|token)(?:$|_)",
    re.IGNORECASE,
)
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?:api[_-]?key|app[_-]?key|access[_-]?key|authorization|credential|"
    r"password|secret|sign(?:ature)?|token)",
    re.IGNORECASE,
)
_URL_KEY = re.compile(r"(?:^|_)(?:url|uri|href|source)(?:$|_)", re.IGNORECASE)
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_LABELLED_SECRET_VALUE = re.compile(
    r"(?i)\b(api[_ -]?key|password|secret|token)\s*[:=]\s*[^\s,;]+"
)
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_DSN_CREDENTIALS = re.compile(
    r"(?P<scheme>postgres(?:ql)?(?:\+[a-z0-9_]+)?://)"
    r"(?P<credentials>[^/@\s]+@)",
    re.IGNORECASE,
)
_LOCAL_PATH_VALUE = re.compile(
    r"(?<![A-Za-z0-9])(?:/Users/|/private/|/var/folders/|[A-Za-z]:\\)[^\s,;\]\}\)]+"
)

_REPORT_ROW_FIELDS: dict[str, tuple[str, ...]] = {
    "claim_investigations": (
        "id", "investigation_id", "claim_id", "claim_variant_id",
        "source_claim_text", "expanded_claim_text", "dependency_path", "status",
        "current_iteration_no", "terminal_reason", "result_summary", "critical_date",
        "critical_date_basis", "target_publication_date", "state_version",
        "review_revision", "created_at",
        "updated_at", "completed_at",
    ),
    "claim_limitations": (
        "id", "claim_investigation_id", "feature_key", "sequence_no",
        "limitation_text", "normalized_text", "origin_claim_id", "source_start",
        "source_end", "is_inherited", "snapshot_version", "created_at",
    ),
    "iterations": (
        "id", "claim_investigation_id", "parent_iteration_id", "iteration_no",
        "purpose", "trigger_reason", "status", "gap_feature_ids",
        "anchor_document_id", "progress_signature", "metrics", "stop_reason",
        "created_at", "started_at", "completed_at",
    ),
    "module_runs": (
        "id", "investigation_id", "claim_investigation_id", "iteration_id",
        "module_code", "contract_version", "status", "input_sha256", "output_sha256",
        "requested_mode", "effective_mode", "actual_provider", "network_used",
        "used_target_images", "used_document_images", "model_version",
        "prompt_version", "rule_version", "error_code", "error_message", "retryable",
        "attempt_no", "cancel_requested_at", "cancelled_at",
        "retry_of_module_run_id", "retry_reason", "created_at", "started_at",
        "completed_at", "updated_at",
    ),
    "jobs": (
        "id", "module_run_id", "status", "priority", "attempt_count", "max_attempts",
        "available_at", "leased_at", "heartbeat_at", "cancel_requested_at",
        "cancel_reason", "cancelled_at", "last_error", "finished_at", "created_at",
        "updated_at",
    ),
    "events": (
        "id", "investigation_id", "claim_investigation_id", "iteration_id",
        "module_run_id", "event_type", "actor", "payload", "occurred_at",
    ),
    "queries": (
        "id", "iteration_id", "parent_query_id", "query_type", "channel", "language",
        "expression", "feature_ids", "classifications", "date_filter", "provider_plan",
        "rationale", "query_sha256", "status", "diagnostic", "created_at", "executed_at",
    ),
    "documents": (
        "id", "investigation_id", "canonical_key", "document_type", "evidence_level",
        "title", "language", "identifiers", "dates", "family_key", "content_sha256",
        "current_document_version_id", "created_at", "updated_at",
    ),
    "document_versions": (
        "id", "document_id", "version_no", "content_sha256", "mime_type",
        "byte_size", "content_artifact_id", "acquisition_kind", "created_by",
        "readable_document_audit", "created_at",
    ),
    "document_sources": (
        "id", "document_id", "document_version_id", "provider", "source_url", "provider_document_id",
        "retrieved_at", "mime_type", "snapshot_sha256", "snapshot_byte_size", "status",
        "error_message", "created_at",
    ),
    "document_qualifications": (
        "id", "document_id", "document_version_id", "claim_investigation_id", "limitation_id", "critical_date",
        "earliest_priority_date", "filing_date", "publication_date",
        "public_availability_date", "eligibility_type", "novelty_eligible",
        "inventive_step_eligible", "verification_status", "verification_reason",
        "date_rule_version", "human_confirmed", "reason_codes", "supersedes_id",
        "review_action_id", "assessment_version", "created_at",
        "updated_at",
    ),
    "feature_disclosures": (
        "id", "module_run_id", "claim_investigation_id", "limitation_id", "document_id",
        "document_version_id",
        "document_source_id", "disclosure_status", "excerpt", "locator", "excerpt_sha256",
        "analysis", "model_version", "prompt_version", "rule_version", "created_at",
        "updated_at",
    ),
    "closest_prior_art_versions": (
        "id", "claim_investigation_id", "iteration_id", "document_id",
        "document_version_id", "version_no", "metrics", "rationale", "selected_by",
        "review_action_id", "supersedes_id", "is_current", "created_at",
    ),
    "gap_items": (
        "id", "iteration_id", "review_action_id", "claim_investigation_id", "limitation_id",
        "source_module_run_id", "gap_type", "gap_key", "gap_fingerprint",
        "version_no", "supersedes_id", "status", "description", "search_objective",
        "resolved_by_document_id", "resolution_evidence", "closed_reason", "created_at",
        "updated_at", "resolved_at",
    ),
    "combinations": (
        "id", "claim_investigation_id", "iteration_id", "module_run_id",
        "closest_prior_art_version_id", "document_ids", "document_version_ids",
        "review_action_id", "status", "coverage_complete",
        "motivation_status", "analysis", "created_at", "updated_at",
    ),
    "artifacts": (
        "id", "investigation_id", "module_run_id", "artifact_type", "sha256", "mime_type",
        "byte_size", "metadata", "created_at",
    ),
    "critical_date_confirmations": (
        "id", "investigation_id", "claim_investigation_id", "decision", "confirmed_date",
        "target_publication_date", "critical_date_basis", "reason", "previous_claim_status",
        "previous_claim_state_version", "resulting_claim_state_version", "actor", "created_at",
    ),
    "continuation_batches": (
        "id", "investigation_id", "claim_investigation_ids", "max_additional_rounds",
        "reason", "round_plan", "status", "module_run_id", "previous_investigation_status",
        "source_state_version", "resulting_state_version", "error_summary", "created_at",
        "started_at", "completed_at",
    ),
    "human_review_actions": (
        "id", "investigation_id", "action_type", "action_seq", "target_snapshot",
        "actor", "reason", "base_investigation_state_version",
        "resulting_investigation_state_version", "base_claim_state_versions",
        "resulting_claim_state_versions", "base_review_revision",
        "resulting_review_revision", "status", "module_run_id", "job_id",
        "invalidated_derivations", "pending_recomputation", "result_summary",
        "error_summary", "created_at", "started_at", "completed_at",
    ),
    "document_date_fact_revisions": (
        "id", "investigation_id", "document_id", "document_version_id",
        "review_action_id", "revision_no", "supersedes_id", "decision",
        "claim_investigation_ids", "public_availability_date", "publication_date",
        "filing_date", "priority_date", "source_type", "publication_number",
        "authority", "cn_application_scope", "date_channel", "actor", "reason",
        "created_at",
    ),
    "evidence_imports": (
        "id", "investigation_id", "review_action_id", "document_id",
        "document_version_id", "document_source_id", "content_artifact_id",
        "claim_investigation_ids", "declared_date_facts", "actor", "reason",
        "created_at",
    ),
}

_PUBLIC_DECLARED_DATE_FIELDS = (
    "public_availability_date",
    "publication_date",
    "filing_date",
    "priority_date",
    "source_type",
    "publication_number",
    "authority",
    "cn_application_scope",
    "date_channel",
)


class ReportDataV1(BaseModel):
    """Stable public report contract; unknown top-level fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["v1"] = REPORT_CONTRACT_VERSION
    report_kind: Literal["invalidity_evidence_data"] = "invalidity_evidence_data"
    report_snapshot_id: str | None = None
    snapshot_sha256: str | None = None
    source_state_version: int = Field(ge=0)
    source_review_revision: int = Field(ge=0)
    snapshot_hash_scope: Literal[
        "canonical_persisted_report_data_v1_excluding_snapshot_sha256",
        "preview_not_hashed",
    ]
    generated_at: datetime
    is_preview: bool
    legal_disclaimer: str
    investigation: dict[str, Any]
    target_patent: dict[str, Any] = Field(default_factory=dict)
    claim_scope: dict[str, Any] = Field(default_factory=dict)
    claim_investigations: list[dict[str, Any]] = Field(default_factory=list)
    claim_limitations: list[dict[str, Any]] = Field(default_factory=list)
    iterations: list[dict[str, Any]] = Field(default_factory=list)
    module_runs: list[dict[str, Any]] = Field(default_factory=list)
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    queries: list[dict[str, Any]] = Field(default_factory=list)
    documents: list[dict[str, Any]] = Field(default_factory=list)
    document_versions: list[dict[str, Any]] = Field(default_factory=list)
    document_sources: list[dict[str, Any]] = Field(default_factory=list)
    document_qualifications: list[dict[str, Any]] = Field(default_factory=list)
    feature_disclosures: list[dict[str, Any]] = Field(default_factory=list)
    closest_prior_art_versions: list[dict[str, Any]] = Field(default_factory=list)
    inventive_step_narratives: list[dict[str, Any]] = Field(default_factory=list)
    similarity_claim_charts: list[dict[str, Any]] = Field(default_factory=list)
    large_claim_charts: list[dict[str, Any]] = Field(default_factory=list)
    gap_items: list[dict[str, Any]] = Field(default_factory=list)
    combinations: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    critical_date_confirmations: list[dict[str, Any]] = Field(default_factory=list)
    continuation_batches: list[dict[str, Any]] = Field(default_factory=list)
    human_review_actions: list[dict[str, Any]] = Field(default_factory=list)
    document_date_fact_revisions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_imports: list[dict[str, Any]] = Field(default_factory=list)


def _redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if not parsed.scheme or not parsed.netloc:
        return value

    hostname = parsed.hostname or ""
    try:
        port_value = parsed.port
    except ValueError:
        return "[REDACTED INVALID URL]"
    port = f":{port_value}" if port_value is not None else ""
    netloc = f"{hostname}{port}"
    if parsed.username is not None or parsed.password is not None:
        netloc = f"[REDACTED]@{netloc}"

    query = [
        (key, "[REDACTED]" if _SENSITIVE_QUERY_KEY.search(key) else item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (parsed.scheme, netloc, parsed.path, urlencode(query, doseq=True), parsed.fragment)
    )


def _redact_string(value: str, *, key: str | None = None) -> str:
    redacted = _BEARER_VALUE.sub("Bearer [REDACTED]", value)
    redacted = _LABELLED_SECRET_VALUE.sub(r"\1=[REDACTED]", redacted)
    redacted = _OPENAI_STYLE_KEY.sub("[REDACTED]", redacted)
    redacted = _DSN_CREDENTIALS.sub(r"\g<scheme>[REDACTED]@", redacted)
    redacted = _LOCAL_PATH_VALUE.sub("[REDACTED LOCAL PATH]", redacted)
    if (key and _URL_KEY.search(key)) or "://" in redacted:
        redacted = _redact_url(redacted)
    return redacted


def _is_sensitive_key(key: str) -> bool:
    if _SENSITIVE_KEY.search(key):
        return True
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(
        marker in normalized
        for marker in (
            "apikey",
            "appkey",
            "appsecret",
            "authorization",
            "databaseurl",
            "password",
            "privatekey",
            "sessioncookie",
            "accesstoken",
            "refreshtoken",
        )
    ) or normalized in {"cookie", "secret", "token"}


def public_json(value: Any, *, _key: str | None = None) -> Any:
    """Convert repository values to JSON-safe data while removing credentials.

    The repository intentionally stores replayable provider snapshots.  This
    function is therefore used at every public API boundary instead of relying
    on a JSON encoder alone: report/API responses must not expose an auth header,
    signed URL secret, database DSN, or model credential even when a provider
    accidentally included one in raw metadata.
    """

    if _key and _is_sensitive_key(_key):
        return "[REDACTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"[BINARY {len(value)} bytes]"
    if isinstance(value, str):
        return _redact_string(value, key=_key)
    if isinstance(value, (uuid.UUID, Path)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return public_json(value.value, _key=_key)
    if isinstance(value, BaseModel):
        return public_json(value.model_dump(mode="python"), _key=_key)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return public_json(dataclasses.asdict(value), _key=_key)
    if isinstance(value, Mapping):
        return {
            str(key): public_json(item, _key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [public_json(item, _key=_key) for item in value]
    return _redact_string(str(value), key=_key)


def json_safe(value: Any) -> Any:
    """Convert domain/repository values without altering protected evidence data."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"[BINARY {len(value)} bytes]"
    if isinstance(value, (uuid.UUID, Path)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, BaseModel):
        return json_safe(value.model_dump(mode="python"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    return str(value)


def _allowlisted_row(value: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {field: value[field] for field in fields if field in value}


def _target_patent(investigation: Mapping[str, Any]) -> dict[str, Any]:
    source = investigation.get("source_snapshot")
    patent = source.get("patent_snapshot") if isinstance(source, Mapping) else None
    if not isinstance(patent, Mapping):
        return {}
    result = _allowlisted_row(
        patent,
        (
            "patent_number", "title", "holder", "abstract", "application_date",
            "priority_date", "publication_date", "parser_task_id", "source_sha256",
            "parser_errors", "snapshot_created_at",
            # 中立著录/结构事实：供管理员同任务只读诊断完整展示模块一输出
            "application_number", "grant_date", "source_format", "page_count",
            "used_ocr", "specification", "bibliographic_data", "claims_section_text",
        ),
    )
    claims = patent.get("claims")
    if isinstance(claims, list):
        public_claims: list[dict[str, Any]] = []
        for claim in claims:
            public_claim = _allowlisted_row(
                claim,
                (
                    "claim_id", "claim_type", "claim_text", "parent_claim_ids",
                    "dependency_uncertain", "sentence_units", "expanded_claim_text",
                ),
            )
            in_scope = public_claim.get("claim_type") == "INDEPENDENT"
            public_claim["in_scope"] = in_scope
            public_claim["scope_reason"] = (
                "当前自动无效检索仅处理独立权利要求"
                if in_scope
                else "从属权利要求仅保留在目标专利事实快照中，不进入当前自动检索"
            )
            public_claims.append(public_claim)
        result["claims"] = public_claims
    figures = patent.get("figures")
    if isinstance(figures, list):
        artifacts = (
            source.get("patent_figure_artifacts")
            if isinstance(source, Mapping)
            else None
        )
        artifact_indexes_by_sha: dict[str, int] = {}
        if isinstance(artifacts, list):
            for index, artifact in enumerate(artifacts):
                if not isinstance(artifact, Mapping):
                    continue
                sha = str(artifact.get("sha256") or "").lower()
                # 同一 SHA 只绑定第一个工件索引，避免重复内容造成身份歧义
                if sha and sha not in artifact_indexes_by_sha:
                    artifact_indexes_by_sha[sha] = index
        public_figures: list[dict[str, Any]] = []
        for figure in figures:
            public_figure = _allowlisted_row(
                figure,
                (
                    "figure_id", "figure_description", "page_number", "selection_role",
                    "selection_score", "selection_reasons", "mime_type", "file_size",
                    "file_sha256",
                ),
            )
            # 按 SHA-256 精确绑定调查级附图工件索引，供只读视图经身份校验后取图；
            # 绑定不上时不输出索引，前端不得猜测。
            artifact_index = artifact_indexes_by_sha.get(
                str(public_figure.get("file_sha256") or "").lower()
            )
            if artifact_index is not None:
                public_figure["artifact_index"] = artifact_index
            public_figures.append(public_figure)
        result["figures"] = public_figures
    return result


def _id_set(rows: Any, field: str) -> set[str]:
    return {
        str(row.get(field))
        for row in rows
        if isinstance(row, Mapping) and row.get(field) is not None
    } if isinstance(rows, list) else set()


def _filter_to_independent_claims(
    payload: dict[str, Any],
    *,
    included_claim_ids: list[str],
) -> None:
    """Remove historical dependent-claim work from the ordinary report."""

    claim_rows = payload.get("claim_investigations")
    if not isinstance(claim_rows, list):
        return
    payload["claim_investigations"] = [
        row
        for row in claim_rows
        if isinstance(row, Mapping)
        and str(row.get("claim_id")) in set(included_claim_ids)
    ]
    claim_investigation_ids = _id_set(
        payload["claim_investigations"], "id"
    )

    def claim_rows_only(section: str) -> None:
        rows = payload.get(section)
        if isinstance(rows, list):
            payload[section] = [
                row
                for row in rows
                if isinstance(row, Mapping)
                and str(row.get("claim_investigation_id"))
                in claim_investigation_ids
            ]


    for section in (
        "claim_limitations",
        "iterations",
        "document_qualifications",
        "feature_disclosures",
        "closest_prior_art_versions",
        "gap_items",
        "combinations",
        "critical_date_confirmations",
    ):
        claim_rows_only(section)

    iteration_ids = _id_set(payload.get("iterations"), "id")
    queries = payload.get("queries")
    if isinstance(queries, list):
        payload["queries"] = [
            row
            for row in queries
            if isinstance(row, Mapping)
            and str(row.get("iteration_id")) in iteration_ids
        ]

    module_runs = payload.get("module_runs")
    if isinstance(module_runs, list):
        payload["module_runs"] = [
            row
            for row in module_runs
            if isinstance(row, Mapping)
            and (
                not row.get("claim_investigation_id")
                or str(row.get("claim_investigation_id"))
                in claim_investigation_ids
            )
        ]
    module_run_ids = _id_set(payload.get("module_runs"), "id")
    jobs = payload.get("jobs")
    if isinstance(jobs, list):
        payload["jobs"] = [
            row
            for row in jobs
            if isinstance(row, Mapping)
            and str(row.get("module_run_id")) in module_run_ids
        ]
    events = payload.get("events")
    if isinstance(events, list):
        payload["events"] = [
            row
            for row in events
            if isinstance(row, Mapping)
            and (
                (
                    row.get("claim_investigation_id")
                    and str(row.get("claim_investigation_id"))
                    in claim_investigation_ids
                )
                or (
                    not row.get("claim_investigation_id")
                    and (
                        not row.get("module_run_id")
                        or str(row.get("module_run_id")) in module_run_ids
                    )
                )
            )
        ]

    document_ids: set[str] = set()
    for section in (
        "document_qualifications",
        "feature_disclosures",
        "closest_prior_art_versions",
    ):
        document_ids.update(_id_set(payload.get(section), "document_id"))
    for row in payload.get("combinations") or []:
        if isinstance(row, Mapping):
            document_ids.update(str(item) for item in row.get("document_ids") or [])
    for row in payload.get("gap_items") or []:
        if isinstance(row, Mapping) and row.get("resolved_by_document_id"):
            document_ids.add(str(row["resolved_by_document_id"]))

    documents = payload.get("documents")
    if isinstance(documents, list):
        payload["documents"] = [
            row
            for row in documents
            if isinstance(row, Mapping) and str(row.get("id")) in document_ids
        ]
    document_versions = payload.get("document_versions")
    if isinstance(document_versions, list):
        payload["document_versions"] = [
            row
            for row in document_versions
            if isinstance(row, Mapping)
            and str(row.get("document_id")) in document_ids
        ]
    version_ids = _id_set(payload.get("document_versions"), "id")
    document_sources = payload.get("document_sources")
    if isinstance(document_sources, list):
        payload["document_sources"] = [
            row
            for row in document_sources
            if isinstance(row, Mapping)
            and str(row.get("document_id")) in document_ids
            and (
                not row.get("document_version_id")
                or str(row.get("document_version_id")) in version_ids
            )
        ]

    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        payload["artifacts"] = [
            row
            for row in artifacts
            if isinstance(row, Mapping)
            and (
                not row.get("module_run_id")
                or str(row.get("module_run_id")) in module_run_ids
            )
        ]

    continuation_batches = payload.get("continuation_batches")
    if isinstance(continuation_batches, list):
        filtered_batches: list[dict[str, Any]] = []
        for row in continuation_batches:
            if not isinstance(row, Mapping):
                continue
            claims = [
                str(item)
                for item in row.get("claim_investigation_ids") or []
                if str(item) in claim_investigation_ids
            ]
            if claims:
                filtered_batches.append({**row, "claim_investigation_ids": claims})
        payload["continuation_batches"] = filtered_batches

    for section in ("document_date_fact_revisions", "evidence_imports"):
        rows = payload.get(section)
        if isinstance(rows, list):
            payload[section] = [
                {
                    **row,
                    "claim_investigation_ids": [
                        str(item)
                        for item in row.get("claim_investigation_ids") or []
                        if str(item) in claim_investigation_ids
                    ],
                }
                for row in rows
                if isinstance(row, Mapping)
                and str(row.get("document_id")) in document_ids
                and any(
                    str(item) in claim_investigation_ids
                    for item in row.get("claim_investigation_ids") or []
                )
            ]


def _build_large_claim_charts(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Materialise the I4-I D1 + Top-5 incremental combination matrix."""

    claim_rows = {
        str(row.get("id")): row
        for row in payload.get("claim_investigations") or []
        if isinstance(row, Mapping) and row.get("id")
    }
    limitations_by_claim: dict[str, list[Mapping[str, Any]]] = {}
    for row in payload.get("claim_limitations") or []:
        if not isinstance(row, Mapping):
            continue
        claim_id = str(row.get("claim_investigation_id") or "")
        limitations_by_claim.setdefault(claim_id, []).append(row)
    disclosures = [
        row
        for row in payload.get("feature_disclosures") or []
        if isinstance(row, Mapping)
    ]
    documents = {
        str(row.get("id")): row
        for row in payload.get("documents") or []
        if isinstance(row, Mapping) and row.get("id")
    }
    latest: dict[str, Mapping[str, Any]] = {}
    for row in payload.get("closest_prior_art_versions") or []:
        if not isinstance(row, Mapping):
            continue
        claim_id = str(row.get("claim_investigation_id") or "")
        previous = latest.get(claim_id)
        row_current = bool(row.get("is_current"))
        previous_current = bool(previous.get("is_current")) if previous else False
        if previous is None or (
            row_current and not previous_current
        ) or (
            row_current == previous_current
            and int(row.get("version_no") or 0)
            >= int(previous.get("version_no") or 0)
        ):
            latest[claim_id] = row

    charts: list[dict[str, Any]] = []
    for claim_investigation_id, closest in latest.items():
        rationale = closest.get("rationale")
        rationale = rationale if isinstance(rationale, Mapping) else {}
        document_ids = [
            str(item)
            for item in rationale.get(
                "large_claim_chart_repository_document_ids", []
            )
            if str(item)
        ]
        d1_document_id = str(closest.get("document_id") or "")
        if d1_document_id and d1_document_id not in document_ids:
            document_ids.insert(0, d1_document_id)
        document_ids = list(dict.fromkeys(document_ids))[:6]
        if not document_ids:
            continue
        feature_rows: list[dict[str, Any]] = []
        for limitation in sorted(
            limitations_by_claim.get(claim_investigation_id, []),
            key=lambda item: int(item.get("sequence_no") or 0),
        ):
            limitation_id = str(limitation.get("id") or "")
            cells: list[dict[str, Any]] = []
            for document_id in document_ids:
                disclosure = next(
                    (
                        row
                        for row in disclosures
                        if str(row.get("claim_investigation_id") or "")
                        == claim_investigation_id
                        and str(row.get("limitation_id") or "") == limitation_id
                        and str(row.get("document_id") or "") == document_id
                    ),
                    None,
                )
                cells.append(
                    {
                        "document_id": document_id,
                        "disclosure_status": (
                            disclosure.get("disclosure_status")
                            if disclosure is not None
                            else "not_analysed"
                        ),
                        "excerpt": disclosure.get("excerpt") if disclosure else "",
                        "locator": disclosure.get("locator") if disclosure else "",
                        "analysis": disclosure.get("analysis") if disclosure else {},
                    }
                )
            feature_rows.append(
                {
                    "limitation_id": limitation_id,
                    "feature_key": limitation.get("feature_key"),
                    "limitation_text": limitation.get("limitation_text"),
                    "cells": cells,
                }
            )
        claim = claim_rows.get(claim_investigation_id, {})
        charts.append(
            {
                "claim_investigation_id": claim_investigation_id,
                "claim_id": claim.get("claim_id"),
                "closest_prior_art_version_id": closest.get("id"),
                "d1_document_id": d1_document_id,
                "document_ids": document_ids,
                "documents": [
                    documents.get(item, {"id": item}) for item in document_ids
                ],
                "distinguishing_features": rationale.get(
                    "distinguishing_features", []
                ),
                "feature_rows": feature_rows,
            }
        )
    return charts


_DISCLOSED_STATUSES = {
    "disclosed",
    "explicit",
    "direct_and_unambiguous",
    "structural_equivalent",
    "necessarily_implicit",
}
_DEFINITIVE_NOT_DISCLOSED_STATUSES = {"not_disclosed"}
_UNCERTAIN_DISCLOSURE_STATUSES = {
    "uncertain",
    "insufficient_evidence",
    "material_incomplete",
}


def _document_display_name(
    document_id: str,
    documents: Mapping[str, Mapping[str, Any]],
) -> str:
    document = documents.get(document_id, {})
    identifiers = document.get("identifiers")
    identifiers = identifiers if isinstance(identifiers, Mapping) else {}
    return str(
        identifiers.get("publication_number")
        or document.get("canonical_key")
        or document.get("title")
        or document_id
    )


def _criterion_narrative(label: str, value: Any) -> str:
    criterion = value if isinstance(value, Mapping) else {}
    status = str(criterion.get("status") or "uncertain")
    status_labels = {
        "supported": "已有支持",
        "same_or_related": "属于相同或相关问题",
        "absent": "未发现反向教导",
        "predictable": "技术效果可以预期",
        "not_supported": "尚无支持",
        "unexpected": "可能存在不可预期效果",
        "present": "存在反向教导",
        "uncertain": "仍待确认",
    }
    sentence = f"{label}：{status_labels.get(status, status)}"
    reasoning = str(criterion.get("reasoning") or "").strip()
    if reasoning:
        sentence += f"，理由为{reasoning}"
    quote = str(criterion.get("evidence_quote") or "").strip()
    location = str(criterion.get("evidence_location") or "").strip()
    if quote:
        sentence += f"；依据记载“{quote}”"
        if location:
            sentence += f"（{location}）"
    return sentence + "。"


def build_inventive_step_narrative(
    assessment: Mapping[str, Any],
    *,
    claim_investigation_id: str,
    claim_id: str | None = None,
    closest_prior_art_version_id: str | None = None,
    d1_document_id: str | None = None,
    documents: Mapping[str, Mapping[str, Any]] | None = None,
    combination_id: str | None = None,
) -> dict[str, Any]:
    """Turn persisted I4-I facts into decision-style prose without new findings."""

    documents = documents or {}
    closest_document_id = str(
        d1_document_id or assessment.get("closest_document_id") or ""
    )
    d1_label = _document_display_name(closest_document_id, documents)
    feature_rows = [
        item
        for item in assessment.get("distinguishing_feature_analysis") or []
        if isinstance(item, Mapping)
    ]
    paragraphs: list[str] = []
    paragraphs.append(
        (
            f"关于独立权利要求{claim_id or claim_investigation_id}，本次分析以"
            f"{d1_label or '尚未确定的文献'}作为最接近的现有技术。"
            "以下判断仅转写模块10已经冻结的三步法事实，不增加新的文献、特征对应或法律事实。"
        )
    )
    if feature_rows:
        feature_names = [
            str(item.get("feature_text") or item.get("feature_id") or "未命名区别特征")
            for item in feature_rows
        ]
        paragraphs.append(
            "与该最接近现有技术相比，本项权利要求的区别技术特征包括："
            + "；".join(feature_names)
            + "。"
        )
        for index, item in enumerate(feature_rows, start=1):
            feature_text = str(
                item.get("feature_text") or item.get("feature_id") or f"区别特征{index}"
            )
            problem = str(item.get("objective_technical_problem") or "").strip()
            supporters = [
                _document_display_name(str(document_id), documents)
                for document_id in item.get("supporting_document_ids") or []
                if str(document_id)
            ]
            paragraph = (
                f"对于区别技术特征“{feature_text}”，D1 的披露判断为"
                f"{str(item.get('d1_disclosure_status') or '未确认')}。"
            )
            paragraph += (
                f"该区别特征实际解决的技术问题为：{problem}。"
                if problem
                else "现有材料尚未明确该区别特征实际解决的技术问题。"
            )
            paragraph += (
                "模块10确认可用于补充评价的文献为"
                + "、".join(supporters)
                + "。"
                if supporters
                else "模块10尚未确认有日期合格且可引用的补充文献公开该特征。"
            )
            paragraph += _criterion_narrative(
                "该补充手段是否承担相同结构角色并产生相同技术作用",
                item.get("same_role_and_effect"),
            )
            paragraph += _criterion_narrative(
                "现有技术是否给出具体技术启示",
                item.get("technical_teaching"),
            )
            paragraph += _criterion_narrative(
                "本领域技术人员是否具有修改动机",
                item.get("modification_motivation"),
            )
            modification_path = str(item.get("modification_path") or "").strip()
            paragraph += (
                f"据此形成的具体修改路径为：{modification_path}。"
                if modification_path
                else "目前尚未形成从 D1 到目标方案的具体修改路径。"
            )
            paragraph += _criterion_narrative(
                "反向教导",
                item.get("teaching_away"),
            )
            paragraph += _criterion_narrative(
                "修改后的技术效果",
                item.get("technical_effect"),
            )
            unresolved = [
                str(reason).strip()
                for reason in item.get("unresolved_reasons") or []
                if str(reason).strip()
            ]
            if item.get("evidence_chain_complete") is True:
                paragraph += "因此，该区别特征的补充公开和组合理由已经形成完整证据链。"
            else:
                paragraph += "因此，该区别特征的证据链尚未闭合。"
                if unresolved:
                    paragraph += "仍待解决：" + "；".join(unresolved) + "。"
            paragraphs.append(paragraph)
    else:
        problem = str(assessment.get("technical_problem") or "").strip()
        motivation = str(assessment.get("motivation") or "").strip()
        if problem:
            paragraphs.append(f"模块10认定本案实际技术问题为：{problem}。")
        if motivation:
            paragraphs.append(f"关于组合启示和修改动机，模块10的分析为：{motivation}。")

    evidence_complete = assessment.get("evidence_complete") is True
    conclusion_text = str(assessment.get("conclusion_text") or "").strip()
    if not conclusion_text:
        conclusion_text = str(assessment.get("conclusion") or "").strip()
    if not conclusion_text:
        conclusion_text = (
            "现有证据已形成缺乏创造性的完整证据链（供律师复核）"
            if evidence_complete
            else "现有证据尚不足以证明不具备创造性"
        )
    if evidence_complete:
        paragraphs.append(
            f"综上，{conclusion_text}。该结论是现有检索证据的工作判断，"
            "仍需律师结合权利要求解释和证据资格复核，不是行政机关的最终决定。"
        )
    else:
        paragraphs.append(
            f"综上，{conclusion_text}。这表示当前材料仍有证据缺口，"
            "不等于已经证明目标权利要求具备创造性或当然有效。"
        )
    return {
        "claim_investigation_id": claim_investigation_id,
        "claim_id": claim_id,
        "combination_id": combination_id,
        "closest_prior_art_version_id": closest_prior_art_version_id,
        "d1_document_id": closest_document_id,
        "evidence_complete": evidence_complete,
        "conclusion_text": conclusion_text,
        "paragraphs": paragraphs,
    }


def _build_inventive_step_narratives(
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    claims = {
        str(row.get("id")): row
        for row in payload.get("claim_investigations") or []
        if isinstance(row, Mapping) and row.get("id")
    }
    documents = {
        str(row.get("id")): row
        for row in payload.get("documents") or []
        if isinstance(row, Mapping) and row.get("id")
    }
    current_closest: dict[str, Mapping[str, Any]] = {}
    for row in payload.get("closest_prior_art_versions") or []:
        if not isinstance(row, Mapping):
            continue
        claim_key = str(row.get("claim_investigation_id") or "")
        previous = current_closest.get(claim_key)
        if previous is None or (
            bool(row.get("is_current")) and not bool(previous.get("is_current"))
        ) or (
            bool(row.get("is_current")) == bool(previous.get("is_current"))
            and int(row.get("version_no") or 0) >= int(previous.get("version_no") or 0)
        ):
            current_closest[claim_key] = row
    narratives: list[dict[str, Any]] = []
    for claim_key, claim in claims.items():
        closest = current_closest.get(claim_key, {})
        closest_id = str(closest.get("id") or "")
        candidates = [
            row
            for row in payload.get("combinations") or []
            if isinstance(row, Mapping)
            and str(row.get("claim_investigation_id") or "") == claim_key
            and isinstance(row.get("analysis"), Mapping)
        ]
        matching = [
            row
            for row in candidates
            if closest_id
            and str(row.get("closest_prior_art_version_id") or "") == closest_id
        ]
        chosen_rows = matching or candidates
        if not chosen_rows:
            continue
        chosen = sorted(
            chosen_rows,
            key=lambda row: (
                str(row.get("updated_at") or row.get("created_at") or ""),
                str(row.get("id") or ""),
            ),
        )[-1]
        narratives.append(
            build_inventive_step_narrative(
                chosen["analysis"],
                claim_investigation_id=claim_key,
                claim_id=str(claim.get("claim_id") or "") or None,
                closest_prior_art_version_id=(closest_id or None),
                d1_document_id=str(closest.get("document_id") or "") or None,
                documents=documents,
                combination_id=str(chosen.get("id") or "") or None,
            )
        )
    return narratives


def _build_similarity_claim_charts(
    payload: Mapping[str, Any],
    *,
    maximum_documents: int = 10,
) -> list[dict[str, Any]]:
    """Rank analysed documents by confirmed limitation disclosure, per claim."""

    claim_rows = {
        str(row.get("id")): row
        for row in payload.get("claim_investigations") or []
        if isinstance(row, Mapping) and row.get("id")
    }
    limitations_by_claim: dict[str, list[Mapping[str, Any]]] = {}
    for row in payload.get("claim_limitations") or []:
        if isinstance(row, Mapping):
            limitations_by_claim.setdefault(
                str(row.get("claim_investigation_id") or ""), []
            ).append(row)
    documents = {
        str(row.get("id")): row
        for row in payload.get("documents") or []
        if isinstance(row, Mapping) and row.get("id")
    }
    current_d1_by_claim = {
        str(row.get("claim_investigation_id") or ""): str(row.get("document_id") or "")
        for row in payload.get("closest_prior_art_versions") or []
        if isinstance(row, Mapping) and row.get("is_current") is True
    }
    latest: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in payload.get("feature_disclosures") or []:
        if not isinstance(row, Mapping):
            continue
        key = (
            str(row.get("claim_investigation_id") or ""),
            str(row.get("document_id") or ""),
            str(row.get("limitation_id") or ""),
        )
        if not all(key):
            continue
        document = documents.get(key[1], {})
        current_version_id = str(document.get("current_document_version_id") or "")
        row_version_id = str(row.get("document_version_id") or "")
        if current_version_id and row_version_id and current_version_id != row_version_id:
            continue
        previous = latest.get(key)
        if previous is None or (
            str(row.get("updated_at") or row.get("created_at") or ""),
            str(row.get("id") or ""),
        ) >= (
            str(previous.get("updated_at") or previous.get("created_at") or ""),
            str(previous.get("id") or ""),
        ):
            latest[key] = row

    charts: list[dict[str, Any]] = []
    for claim_key, claim in claim_rows.items():
        limitations = sorted(
            limitations_by_claim.get(claim_key, []),
            key=lambda row: int(row.get("sequence_no") or 0),
        )
        if not limitations:
            continue
        document_ids = sorted(
            {
                document_id
                for row_claim_id, document_id, _ in latest
                if row_claim_id == claim_key
            }
        )
        ranked: list[dict[str, Any]] = []
        for document_id in document_ids:
            statuses = [
                str(
                    latest.get(
                        (claim_key, document_id, str(limitation.get("id") or "")),
                        {},
                    ).get("disclosure_status")
                    or "not_analysed"
                )
                for limitation in limitations
            ]
            disclosed = sum(status in _DISCLOSED_STATUSES for status in statuses)
            explicit = sum(
                status in {"explicit", "direct_and_unambiguous"}
                for status in statuses
            )
            implicit = sum(status == "necessarily_implicit" for status in statuses)
            not_disclosed = sum(
                status in _DEFINITIVE_NOT_DISCLOSED_STATUSES for status in statuses
            )
            uncertain = sum(
                status in _UNCERTAIN_DISCLOSURE_STATUSES for status in statuses
            )
            definitive = disclosed + not_disclosed
            analysed = sum(status != "not_analysed" for status in statuses)
            ranked.append(
                {
                    "document_id": document_id,
                    "confirmed_disclosed_feature_count": disclosed,
                    "explicit_feature_count": explicit,
                    "necessarily_implicit_feature_count": implicit,
                    "not_disclosed_feature_count": not_disclosed,
                    "uncertain_feature_count": uncertain,
                    "definitive_feature_count": definitive,
                    "analysed_feature_count": analysed,
                    "total_feature_count": len(limitations),
                    "confirmed_coverage_ratio": disclosed / len(limitations),
                }
            )
        ranked.sort(
            key=lambda item: (
                -int(item["confirmed_disclosed_feature_count"]),
                -int(item["definitive_feature_count"]),
                -int(item["analysed_feature_count"]),
                -int(item["explicit_feature_count"]),
                _document_display_name(str(item["document_id"]), documents),
            )
        )
        ranked = ranked[:maximum_documents]
        for index, item in enumerate(ranked, start=1):
            item["rank"] = index
            item["is_current_d1"] = (
                str(item["document_id"]) == current_d1_by_claim.get(claim_key, "")
            )
        selected_ids = [str(item["document_id"]) for item in ranked]
        feature_rows: list[dict[str, Any]] = []
        for limitation in limitations:
            limitation_id = str(limitation.get("id") or "")
            cells = []
            for document_id in selected_ids:
                disclosure = latest.get((claim_key, document_id, limitation_id))
                cells.append(
                    {
                        "document_id": document_id,
                        "disclosure_status": (
                            disclosure.get("disclosure_status")
                            if disclosure is not None
                            else "not_analysed"
                        ),
                        "excerpt": disclosure.get("excerpt") if disclosure else "",
                        "locator": disclosure.get("locator") if disclosure else "",
                        "analysis": disclosure.get("analysis") if disclosure else {},
                        "module_run_id": (
                            disclosure.get("module_run_id") if disclosure else None
                        ),
                    }
                )
            feature_rows.append(
                {
                    "limitation_id": limitation_id,
                    "feature_key": limitation.get("feature_key"),
                    "limitation_text": limitation.get("limitation_text"),
                    "cells": cells,
                }
            )
        if ranked:
            charts.append(
                {
                    "claim_investigation_id": claim_key,
                    "claim_id": claim.get("claim_id"),
                    "maximum_documents": maximum_documents,
                    "ranking_basis": (
                        "按本次 I4-S 已确认披露的权利要求特征数降序；"
                        "待确认不计入已披露，再按确定性评价完整度和稳定文献标识排序。"
                    ),
                    "document_ids": selected_ids,
                    "ranked_documents": [
                        {
                            **item,
                            "document": documents.get(
                                str(item["document_id"]),
                                {"id": item["document_id"]},
                            ),
                        }
                        for item in ranked
                    ],
                    "feature_rows": feature_rows,
                }
            )
    return charts


def build_report_data(
    raw: Mapping[str, Any],
    *,
    preview: bool = False,
    report_snapshot_id: str | uuid.UUID | None = None,
    snapshot_sha256: str | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the stable I5 read contract without making a legal conclusion."""

    investigation = raw.get("investigation")
    if not isinstance(investigation, Mapping):
        raise ValueError("报告数据缺少 investigation")

    public_investigation = _allowlisted_row(
        investigation,
        (
            "id", "analysis_session_id", "patent_record_id", "environment", "jurisdiction",
            "analysis_kind", "status", "pipeline_version", "budget_used", "error_message",
            "error_metadata", "state_version", "review_revision",
            "last_applied_review_action_seq", "review_recomputation_required",
            "created_at", "updated_at", "completed_at",
        ),
    )
    target_patent = _target_patent(investigation)
    target_claims = (
        target_patent.get("claims")
        if isinstance(target_patent.get("claims"), list)
        else []
    )
    claim_scope_by_id = {
        str(claim.get("claim_id")): claim
        for claim in target_claims
        if isinstance(claim, Mapping) and claim.get("claim_id") is not None
    }
    included_claim_ids = [
        claim_id
        for claim_id, claim in claim_scope_by_id.items()
        if bool(claim.get("in_scope"))
    ]
    excluded_claim_ids = [
        claim_id
        for claim_id, claim in claim_scope_by_id.items()
        if not bool(claim.get("in_scope"))
    ]
    payload: dict[str, Any] = {
        "contract_version": REPORT_CONTRACT_VERSION,
        "report_kind": "invalidity_evidence_data",
        "report_snapshot_id": str(report_snapshot_id) if report_snapshot_id else None,
        "snapshot_sha256": snapshot_sha256,
        "source_state_version": int(investigation.get("state_version") or 0),
        "source_review_revision": int(
            investigation.get("review_revision") or 0
        ),
        "snapshot_hash_scope": (
            "preview_not_hashed"
            if preview
            else "canonical_persisted_report_data_v1_excluding_snapshot_sha256"
        ),
        "generated_at": generated_at or datetime.now().astimezone(),
        "is_preview": preview,
        "legal_disclaimer": (
            "本结果是检索与分析辅助材料，不是专利无效决定或正式法律意见。"
        ),
        "investigation": public_investigation,
        "target_patent": target_patent,
        "claim_scope": {
            "mode": "independent_only",
            "description": "当前自动检索、稳定性分析和普通报告仅处理独立权利要求。",
            "included_claim_ids": included_claim_ids,
            "excluded_dependent_claim_ids": excluded_claim_ids,
            "included_count": len(included_claim_ids),
            "excluded_dependent_count": len(excluded_claim_ids),
        },
    }
    for section, fields in _REPORT_ROW_FIELDS.items():
        rows = raw.get(section)
        public_rows = [
            _allowlisted_row(row, fields)
            for row in rows
            if isinstance(row, Mapping)
        ] if isinstance(rows, list) else []
        if section == "evidence_imports":
            for row in public_rows:
                facts = row.get("declared_date_facts")
                row["declared_date_facts"] = (
                    _allowlisted_row(facts, _PUBLIC_DECLARED_DATE_FIELDS)
                    if isinstance(facts, Mapping)
                    else {}
                )
        if section == "claim_investigations":
            for row in public_rows:
                claim = claim_scope_by_id.get(str(row.get("claim_id")))
                claim_type = (
                    str(claim.get("claim_type"))
                    if isinstance(claim, Mapping) and claim.get("claim_type")
                    else "UNKNOWN"
                )
                row["claim_type"] = claim_type
                row["in_scope"] = claim_type == "INDEPENDENT"
                row["scope_reason"] = (
                    "当前自动无效检索仅处理独立权利要求"
                    if row["in_scope"]
                    else "历史从属权利要求记录仅供技术审计，不计入当前结果和人工确认数量"
                )
        payload[section] = public_rows
    if claim_scope_by_id:
        _filter_to_independent_claims(
            payload,
            included_claim_ids=included_claim_ids,
        )
    payload["inventive_step_narratives"] = _build_inventive_step_narratives(payload)
    payload["similarity_claim_charts"] = _build_similarity_claim_charts(payload)
    payload["large_claim_charts"] = _build_large_claim_charts(payload)
    sanitized = public_json(payload)
    assert isinstance(sanitized, dict)
    return ReportDataV1.model_validate(sanitized).model_dump(mode="json")


def public_exception_message(exc: BaseException) -> str:
    """Return a credential-safe message for persisted/API-visible failures."""

    class_name = exc.__class__.__name__
    if class_name in {
        "ConnectError",
        "ConnectTimeout",
        "HTTPStatusError",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "TimeoutException",
    }:
        return "外部服务调用失败；任务未被标记为成功"
    return "任务执行失败；详细诊断已保留在受限服务日志中"


__all__ = [
    "REPORT_CONTRACT_VERSION",
    "ReportDataV1",
    "build_report_data",
    "build_inventive_step_narrative",
    "json_safe",
    "public_exception_message",
    "public_json",
]
