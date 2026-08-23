#!/usr/bin/env python3
"""Run every oracle patent through the bounded invalidity workflow.

Fixture mode uses the real target PDFs and the real isolated parser.  Search
documents and analysis results are deterministic simulations used only to
exercise I0-I5 contracts; they are never represented as real prior art or
legal evidence.  Live mode calls the isolated test API and never falls back to
fixture data when a provider, model, or service fails.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Iterator, Mapping, Sequence
import uuid

import fitz
import httpx

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = SERVICE_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from invalidity.analysis import (
    AnalysisGap,
    CombinationCriterion,
    FeatureCoverage,
    InventiveStepAssessment,
    ObviousnessPrecheckAssessment,
    QueryPlan,
)
from invalidity.candidate_filter import (
    apply_filter_decisions,
    build_raw_candidates,
    group_candidates,
)
from invalidity.contracts import (
    ClaimSnapshot,
    ClaimStatus,
    DisclosureStatus,
    DocumentComparison,
    FeatureDisclosure,
    I4I_PROMPT_VERSION,
    I4I_RULE_VERSION,
    I4S_RULE_VERSION,
    Limitation,
    PatentSnapshot,
    SearchQuery,
    SearchObjective,
    DateChannel,
    GapType,
)
from invalidity.db import plan_gap_frontier
from invalidity.module1 import normalize_claims
from invalidity.parser_service import parse_source
from invalidity.providers import EvidenceRecord, EvidenceStage
from invalidity.report import ReportDataV1, build_report_data
from invalidity.workflow import InvalidityWorkflow, SearchBatch


DEFAULT_SAMPLE_ROOT = Path("/Users/xiexiaoxiong/Documents/Patent项目配套/实例专利")
DEFAULT_ORACLE = SERVICE_ROOT / "fixtures" / "sample-oracle.json"
DEFAULT_OUTPUT_ROOT = (
    SERVICE_ROOT.parent / ".data" / "invalidity" / "test" / "regression" / "full-pipeline"
)
SCHEMA_VERSION = "invalidity-full-regression-v1"
MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS = 5
FIXTURE_ENGINE = "fixture-deterministic-engine-not-legal-evidence"
FIXTURE_PROVIDER = "fixture_simulation"
FIXTURE_DISCLAIMER = (
    "本次 fixture 回归真实解析目标专利，但检索文献、披露矩阵和创造性组合均为"
    "确定性合成数据，只验证 I1-I5 工作流、持久化和停止规则；未执行真实世界检索，"
    "不得作为对比文件、CC 表证据、法律意见或专利有效/无效结论。"
)
LIVE_DISCLAIMER = (
    "live 回归只调用隔离测试服务和已配置真实 provider；结果仍是检索辅助材料，"
    "不是法律意见或专利有效/无效结论。任何 provider/model 失败均原样记录，绝不"
    "回退为 fixture。"
)
PIPELINE_STAGES = (
    "I1",
    "I1.5",
    "I2",
    "I3-P",
    "I3-NPL",
    "I3-F",
    "I3-E",
    "I4-S",
    "I4-C",
    "I4-O",
    "I4-I",
    "I5",
)
TERMINAL_INVESTIGATION_STATUSES = {
    "completed",
    "partial",
    "failed",
    "cancelled",
    "needs_human_review",
}
LIVE_QUERY_TERMINAL_STATUSES = {"completed", "partial", "failed"}
LIVE_NON_REPORTABLE_TERMINAL_STATUSES = {"failed", "cancelled"}
LIVE_SOURCE_STATUSES = {"retrieved", "qualified"}
LIVE_FORBIDDEN_PROVENANCE = re.compile(
    r"(?:^|[-_])(?:fixture|manual|mock|fake|simulated)(?:$|[-_])",
    re.IGNORECASE,
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normal(value: Any) -> str:
    return "".join(str(value or "").split()).lower()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_live_upload_root(explicit: Path | None) -> Path:
    if explicit is not None:
        candidates = [str(explicit)]
    else:
        raw = str(os.environ.get("INVALIDITY_ALLOWED_SOURCE_ROOTS") or "")
        candidates = []
        for item in raw.split(os.pathsep):
            candidates.extend(item.split(","))
    values = [Path(item.strip()).expanduser().resolve() for item in candidates if item.strip()]
    if not values:
        raise ValueError(
            "live 回归必须通过 --upload-root 或 INVALIDITY_ALLOWED_SOURCE_ROOTS "
            "指定测试上传根目录"
        )
    root = values[0]
    if root == Path(root.anchor):
        raise ValueError("live 回归上传根目录不能是文件系统根目录")
    expected_root = (SERVICE_ROOT / ".data" / "uploads" / "test").resolve()
    if root != expected_root:
        raise ValueError(
            "live 回归只能使用隔离测试上传根目录 "
            f"{expected_root}，拒绝其他目录: {root}"
        )
    root.mkdir(parents=True, exist_ok=True)
    return root


def stage_live_source(path: Path, *, upload_root: Path, run_id: str) -> Path:
    source_hash = file_sha256(path)
    staging_dir = (upload_root / "full-regression" / safe_name(run_id)).resolve()
    try:
        staging_dir.relative_to(upload_root.resolve())
    except ValueError as exc:
        raise ValueError("live 回归 staging 目录越出允许上传根目录") from exc
    staging_dir.mkdir(parents=True, exist_ok=True)
    target = staging_dir / f"{source_hash[:16]}-{safe_name(path.name)}"
    temporary = staging_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with path.open("rb") as source, temporary.open("xb") as destination:
            shutil.copyfileobj(source, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    if file_sha256(target) != source_hash:
        raise IOError("live 回归 staging 文件哈希与源文件不一致")
    return target


def validate_live_api_target(base_url: str, token_env: str) -> None:
    if base_url != "http://127.0.0.1:5209":
        raise ValueError(
            "live 回归 API 必须严格等于 http://127.0.0.1:5209，"
            "禁止调用正式或其他端口"
        )
    if token_env != "INVALIDITY_TEST_API_TOKEN":
        raise ValueError(
            "live 回归只允许读取 INVALIDITY_TEST_API_TOKEN，禁止切换正式 token"
        )


def _report_rows(report: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = report.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_sha256(value: Any) -> bool:
    return bool(SHA256_PATTERN.fullmatch(str(value or "").strip()))


def _positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def _forbidden_live_provenance(value: Any) -> bool:
    return bool(LIVE_FORBIDDEN_PROVENANCE.search(str(value or "").strip()))


def _query_diagnostic(query: Mapping[str, Any]) -> Mapping[str, Any]:
    provider_plan = _mapping(query.get("provider_plan"))
    diagnostic = provider_plan.get("execution_diagnostic")
    if isinstance(diagnostic, Mapping):
        return diagnostic
    return _mapping(query.get("diagnostic"))


def _source_category(
    source: Mapping[str, Any], document: Mapping[str, Any]
) -> str:
    provider = str(source.get("provider") or "").lower()
    document_type = str(document.get("document_type") or "").lower()
    if "google_patent" in provider or "patent" in document_type:
        return "patent"
    return "npl"


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _row_content_sha256(row: Mapping[str, Any]) -> str:
    for key in (
        "document_content_sha256",
        "content_sha256",
        "snapshot_sha256",
    ):
        value = str(row.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def _i4s_input_binding(run: Mapping[str, Any]) -> Mapping[str, Any]:
    """Read the non-secret immutable input binding across contract revisions."""

    direct = run.get("input_binding")
    if isinstance(direct, Mapping):
        return direct
    snapshot = _mapping(run.get("input_snapshot"))
    for key in ("input_binding", "document_binding"):
        value = snapshot.get(key)
        if isinstance(value, Mapping):
            return value
    comparison = _mapping(snapshot.get("comparison"))
    nested = comparison.get("_input_binding") or comparison.get("input_binding")
    if isinstance(nested, Mapping):
        return nested
    if any(
        run.get(key)
        for key in (
            "document_id",
            "document_version_id",
            "document_content_sha256",
        )
    ):
        return run
    return {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def audit_live_report_truth(report: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a replayable live evidence chain from structured report rows.

    This deliberately does not trust aggregate booleans, serialized substring
    searches, a terminal status, or the mere presence of an I3/I4 module code.
    """

    errors: list[str] = []
    claims = _report_rows(report, "claim_investigations")
    limitations = _report_rows(report, "claim_limitations")
    iterations = _report_rows(report, "iterations")
    queries = _report_rows(report, "queries")
    module_runs = _report_rows(report, "module_runs")
    documents = _report_rows(report, "documents")
    document_versions = _report_rows(report, "document_versions")
    sources = _report_rows(report, "document_sources")
    qualifications = _report_rows(report, "document_qualifications")
    disclosures = _report_rows(report, "feature_disclosures")
    closest_prior_art = _report_rows(report, "closest_prior_art_versions")
    combinations = _report_rows(report, "combinations")
    artifacts = _report_rows(report, "artifacts")

    immutable_report = (
        report.get("contract_version") == "v1"
        and report.get("report_kind") == "invalidity_evidence_data"
        and report.get("is_preview") is False
        and bool(str(report.get("report_snapshot_id") or "").strip())
        and _is_sha256(report.get("snapshot_sha256"))
        and report.get("snapshot_hash_scope")
        == "canonical_persisted_report_data_v1_excluding_snapshot_sha256"
    )
    if not immutable_report:
        errors.append("报告不是已哈希的不可变 ReportDataV1 快照")

    live_mode_only = True
    for run in module_runs:
        requested_mode = str(run.get("requested_mode") or "").strip().lower()
        effective_mode = str(run.get("effective_mode") or "").strip().lower()
        provider = str(run.get("actual_provider") or "").strip()
        if requested_mode and requested_mode != "live":
            live_mode_only = False
        if effective_mode and effective_mode != "live":
            live_mode_only = False
        if _forbidden_live_provenance(provider):
            live_mode_only = False
    for source in sources:
        if _forbidden_live_provenance(source.get("provider")):
            live_mode_only = False
    if not live_mode_only:
        errors.append("报告含 fixture/manual/mock/simulated 或非 live 执行谱系")

    iterations_by_claim: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for iteration in iterations:
        iterations_by_claim[str(iteration.get("claim_investigation_id") or "")].append(
            iteration
        )
    queries_by_iteration: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for query in queries:
        queries_by_iteration[str(query.get("iteration_id") or "")].append(query)

    missing_query_lanes: list[str] = []
    structured_network_use = True
    for claim in claims:
        claim_id = str(claim.get("id") or "")
        claim_iterations = iterations_by_claim.get(claim_id, [])
        if not claim_iterations:
            missing_query_lanes.append(f"claim={claim_id or '?'}: no iteration")
            structured_network_use = False
            continue
        for iteration in claim_iterations:
            iteration_id = str(iteration.get("id") or "")
            try:
                iteration_no = int(iteration.get("iteration_no") or 0)
            except (TypeError, ValueError):
                iteration_no = 0
            rows = queries_by_iteration.get(iteration_id, [])
            if iteration_no <= 1:
                objective = "full_claim_single_reference"
                expected_lanes = (("patent", "ordinary_prior_art"),)
            else:
                objective = "gap_or_combination"
                expected_lanes = (
                    ("patent", "ordinary_prior_art"),
                    ("patent", "cn_conflicting_application"),
                    ("npl", "ordinary_prior_art"),
                )
            for channel, date_channel in expected_lanes:
                matches = []
                for query in rows:
                    provider_plan = _mapping(query.get("provider_plan"))
                    query_date_filter = _mapping(query.get("date_filter"))
                    query_objective = str(
                        provider_plan.get("search_objective") or ""
                    )
                    query_channel = str(
                        query.get("channel")
                        or provider_plan.get("provider_kind")
                        or ""
                    )
                    query_date_channel = str(
                        query_date_filter.get("date_channel")
                        or provider_plan.get("date_channel")
                        or ""
                    )
                    if (
                        query_objective == objective
                        and query_channel == channel
                        and query_date_channel == date_channel
                    ):
                        matches.append(query)
                lane_ok = any(
                    str(query.get("status") or "")
                    in LIVE_QUERY_TERMINAL_STATUSES
                    and _query_diagnostic(query).get("network_used") is True
                    for query in matches
                )
                if not lane_ok:
                    structured_network_use = False
                    missing_query_lanes.append(
                        f"claim={claim_id or '?'} iteration={iteration_no or '?'} "
                        f"objective={objective} lane={channel}/{date_channel}"
                    )
    query_matrix_complete = bool(claims) and not missing_query_lanes
    if not query_matrix_complete:
        errors.append(
            "逐权利要求/逐轮检索矩阵未完成真实网络执行: "
            + "; ".join(missing_query_lanes[:12])
        )

    candidate_filter_runs = [
        run
        for run in module_runs
        if str(run.get("module_code") or "") == "I3_CANDIDATE_FILTER"
        and str(run.get("status") or "") == "succeeded"
        and _is_sha256(run.get("input_sha256"))
        and _is_sha256(run.get("output_sha256"))
    ]
    fetch_runs = [
        run
        for run in module_runs
        if str(run.get("module_code") or "") == "I3_FETCH"
    ]

    def completed_at(run: Mapping[str, Any]) -> datetime | None:
        raw = str(run.get("completed_at") or "").strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    candidate_filter_errors: list[str] = []
    if not fetch_runs:
        candidate_filter_errors.append("报告没有 I3_FETCH run")
    for fetch_run in fetch_runs:
        iteration_id = str(fetch_run.get("iteration_id") or "")
        fetch_completed_at = completed_at(fetch_run)
        candidates = [
            run
            for run in candidate_filter_runs
            if str(run.get("iteration_id") or "") == iteration_id
            and completed_at(run) is not None
            and fetch_completed_at is not None
            and completed_at(run) <= fetch_completed_at
        ]
        if not candidates:
            candidate_filter_errors.append(
                f"fetch={fetch_run.get('id') or '?'} iteration={iteration_id or '?'}: "
                "缺少同轮、先完成且带输入/输出哈希的 I3_CANDIDATE_FILTER"
            )
    candidate_filter_fetch_ordering = (
        bool(candidate_filter_runs)
        and bool(fetch_runs)
        and not candidate_filter_errors
    )
    if not candidate_filter_fetch_ordering:
        errors.append(
            "I3-F 候选清理没有在同轮 I3_FETCH 前形成可重放终态: "
            + "; ".join(candidate_filter_errors[:12])
        )

    documents_by_id = {
        str(document.get("id") or ""): document
        for document in documents
        if document.get("id")
    }
    artifacts_by_id = {
        str(artifact.get("id") or ""): artifact
        for artifact in artifacts
        if artifact.get("id")
    }
    document_versions_by_id: dict[str, Mapping[str, Any]] = {}
    valid_document_version_ids: set[str] = set()
    version_binding_errors: list[str] = []
    for version in document_versions:
        version_id = str(version.get("id") or "")
        document_id = str(version.get("document_id") or "")
        content_sha256 = str(version.get("content_sha256") or "").strip().lower()
        artifact_id = str(version.get("content_artifact_id") or "")
        artifact = artifacts_by_id.get(artifact_id, {})
        if not artifact:
            artifact = next(
                (
                    item
                    for item in artifacts
                    if str(item.get("sha256") or "").strip().lower()
                    == content_sha256
                    and int(item.get("byte_size") or 0)
                    == int(version.get("byte_size") or 0)
                ),
                {},
            )
        artifact_sha256 = str(artifact.get("sha256") or "").strip().lower()
        valid = (
            bool(version_id and document_id)
            and document_id in documents_by_id
            and _positive_int(version.get("version_no"))
            and _is_sha256(content_sha256)
            and bool(artifact)
            and artifact_sha256 == content_sha256
            and (
                not artifact.get("investigation_id")
                or not documents_by_id[document_id].get("investigation_id")
                or str(artifact.get("investigation_id"))
                == str(documents_by_id[document_id].get("investigation_id"))
            )
            and _positive_int(version.get("byte_size"))
            and _positive_int(artifact.get("byte_size"))
            and int(version.get("byte_size")) == int(artifact.get("byte_size"))
        )
        if version_id:
            if version_id in document_versions_by_id:
                valid = False
                version_binding_errors.append(
                    f"document_version={version_id}: duplicate id"
                )
            document_versions_by_id[version_id] = version
        if valid:
            valid_document_version_ids.add(version_id)
        else:
            version_binding_errors.append(
                f"document_version={version_id or '?'} document={document_id or '?'}: "
                "identity/SHA/artifact/byte_size binding invalid"
            )
    immutable_document_versions = (
        bool(valid_document_version_ids) and not version_binding_errors
    )
    if not immutable_document_versions:
        errors.append("document_version 没有完整绑定不可变原文工件、SHA 和字节数")

    analysis_ready_document_version_ids: set[str] = set()
    readable_document_errors: list[str] = []
    for version_id in valid_document_version_ids:
        version = document_versions_by_id[version_id]
        metadata = _mapping(version.get("version_metadata"))
        readable = _mapping(metadata.get("readable_document"))
        readable_audit = _mapping(version.get("readable_document_audit"))
        content_sha256 = str(version.get("content_sha256") or "").strip().lower()
        audit_source = readable_audit or readable
        full_text = str(readable.get("full_text") or "")
        compact_char_count = (
            audit_source.get("compact_char_count")
            if readable_audit
            else len(re.sub(r"\s+", "", full_text))
        )
        page_count = audit_source.get("page_count")
        processed_page_count = audit_source.get("processed_page_count")
        failed_pages = audit_source.get("failed_pages")
        sections = readable.get("sections")
        base_valid = (
            (
                readable_audit.get("analysis_ready") is True
                if readable_audit
                else metadata.get("analysis_ready") is True
                and metadata.get("readable_document_version")
                == "readable-patent-v1"
                and readable.get("analysis_ready") is True
            )
            and audit_source.get("schema_version") == "readable-patent-v1"
            and str(audit_source.get("source_sha256") or "").strip().lower()
            == content_sha256
            and int(compact_char_count or 0) >= 200
            and isinstance(failed_pages, list)
            and not failed_pages
            and (
                int(readable_audit.get("section_count") or 0) > 0
                if readable_audit
                else isinstance(sections, list) and bool(sections)
            )
        )
        page_valid = True
        if str(version.get("mime_type") or "").lower() == "application/pdf":
            anchors = (
                {int(item) for item in readable_audit.get("section_page_starts") or []}
                if readable_audit
                else {
                    int(row.get("page_start"))
                    for row in sections or []
                    if isinstance(row, Mapping)
                    and _positive_int(row.get("page_start"))
                }
            )
            page_valid = bool(
                _positive_int(page_count)
                and int(processed_page_count or 0) == int(page_count)
                and anchors == set(range(1, int(page_count) + 1))
            )
        if base_valid and page_valid:
            analysis_ready_document_version_ids.add(version_id)
        else:
            readable_document_errors.append(
                f"document_version={version_id}: missing current readable-patent-v1 "
                "full-document/OCR coverage bound to source SHA"
            )
    analysis_ready_document_versions = bool(
        analysis_ready_document_version_ids
    ) and not readable_document_errors
    if not analysis_ready_document_versions:
        errors.append("document_version 未完整绑定全页文本/OCR 可读文档")

    def resolve_version(
        row: Mapping[str, Any], *, label: str
    ) -> tuple[str, str, Mapping[str, Any]] | None:
        document_id = str(row.get("document_id") or "")
        version_id = str(row.get("document_version_id") or "")
        version = document_versions_by_id.get(version_id)
        if (
            not document_id
            or version_id not in valid_document_version_ids
            or version is None
            or str(version.get("document_id") or "") != document_id
        ):
            return None
        explicit_sha256 = str(
            row.get("document_content_sha256")
            or row.get("content_sha256")
            or ""
        ).strip().lower()
        version_sha256 = str(version.get("content_sha256") or "").strip().lower()
        if explicit_sha256 and explicit_sha256 != version_sha256:
            return None
        return document_id, version_id, version

    valid_sources_by_id: dict[str, Mapping[str, Any]] = {}
    valid_sources_by_document: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    valid_sources_by_version: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    source_binding_errors: list[str] = []
    patent_source_snapshot = False
    npl_source_snapshot = False
    for source in sources:
        source_id = str(source.get("id") or "")
        document_id = str(source.get("document_id") or "")
        version_id = str(source.get("document_version_id") or "")
        provider = str(source.get("provider") or "").strip()
        source_url = str(source.get("source_url") or "").strip().lower()
        document = documents_by_id.get(document_id, {})
        version = document_versions_by_id.get(version_id, {})
        content_sha256 = str(version.get("content_sha256") or "").strip().lower()
        artifact_id = str(source.get("artifact_id") or "")
        content_artifact_id = str(version.get("content_artifact_id") or "")
        evidence_bearing = (
            str(source.get("status") or "") in LIVE_SOURCE_STATUSES
            or bool(source.get("snapshot_sha256"))
            or bool(source.get("artifact_id"))
        )
        valid = (
            bool(source_id and document_id and provider)
            and not _forbidden_live_provenance(provider)
            and source_url.startswith(("http://", "https://"))
            and str(source.get("status") or "") in LIVE_SOURCE_STATUSES
            and resolve_version(source, label=f"source={source_id}") is not None
            and _is_sha256(source.get("snapshot_sha256"))
            and str(source.get("snapshot_sha256") or "").strip().lower()
            == content_sha256
            and _positive_int(source.get("snapshot_byte_size"))
            and int(source.get("snapshot_byte_size"))
            == int(version.get("byte_size") or 0)
            and (
                not artifact_id
                or not content_artifact_id
                or artifact_id == content_artifact_id
            )
        )
        if not valid:
            if evidence_bearing:
                source_binding_errors.append(
                    f"source={source_id or '?'} document={document_id or '?'} "
                    f"version={version_id or '?'}: version/SHA/artifact binding invalid"
                )
            continue
        valid_sources_by_id[source_id] = source
        valid_sources_by_document[document_id].append(source)
        valid_sources_by_version[version_id].append(source)
        if _source_category(source, document) == "patent":
            patent_source_snapshot = True
        else:
            npl_source_snapshot = True
    immutable_source_snapshot = bool(valid_sources_by_id)
    if not immutable_source_snapshot:
        errors.append("没有真实 HTTP(S) 来源、本地冻结 SHA 与正字节数完整的原文快照")

    source_version_bindings = not source_binding_errors
    if not source_version_bindings:
        errors.append("document_source 存在跨版本或 SHA/工件不一致绑定")

    latest_qualifications: dict[tuple[str, str], Mapping[str, Any]] = {}
    qualification_binding_errors: list[str] = []
    for qualification in qualifications:
        qualification_id = str(qualification.get("id") or "")
        version_id = str(qualification.get("document_version_id") or "")
        if resolve_version(
            qualification, label=f"qualification={qualification_id}"
        ) is None:
            qualification_binding_errors.append(
                f"qualification={qualification_id or '?'} "
                f"version={version_id or '?'}: document_version binding invalid"
            )
            continue
        if qualification.get("limitation_id"):
            continue
        key = (
            str(qualification.get("claim_investigation_id") or ""),
            version_id,
        )
        try:
            version = int(qualification.get("assessment_version") or 0)
            previous_version = int(
                latest_qualifications.get(key, {}).get("assessment_version") or 0
            )
        except (TypeError, ValueError):
            version = previous_version = 0
        if key[0] and key[1] and (key not in latest_qualifications or version >= previous_version):
            latest_qualifications[key] = qualification

    def qualification_is_verified(qualification: Mapping[str, Any]) -> bool:
        verification_status = str(
            qualification.get("verification_status") or ""
        ).lower()
        eligibility_type = str(qualification.get("eligibility_type") or "").lower()
        return (
            (
                verification_status in {"verified", "human_confirmed"}
                or qualification.get("human_confirmed") is True
            )
            and eligibility_type
            not in {"", "lead_only", "needs_human_review", "excluded", "unknown"}
            and (
                qualification.get("novelty_eligible") is True
                or qualification.get("inventive_step_eligible") is True
            )
        )

    qualification_version_bindings = not qualification_binding_errors
    if not qualification_version_bindings:
        errors.append("document_qualification 存在缺失或错误的 document_version 绑定")

    verified_date_qualifications = {
        key: qualification
        for key, qualification in latest_qualifications.items()
        if qualification_is_verified(qualification)
        and key[1] in valid_sources_by_version
    }
    verified_date_qualification = bool(verified_date_qualifications)
    if not verified_date_qualification:
        errors.append("没有可与真实原文绑定的确定性日期资格记录")

    limitations_by_claim: dict[str, set[str]] = defaultdict(set)
    for limitation in limitations:
        claim_id = str(limitation.get("claim_investigation_id") or "")
        limitation_id = str(limitation.get("id") or "")
        if claim_id and limitation_id:
            limitations_by_claim[claim_id].add(limitation_id)
    disclosures_by_run: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    valid_disclosures_by_run: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    disclosure_binding_errors: list[str] = []
    module_runs_by_id = {
        str(run.get("id") or ""): run for run in module_runs if run.get("id")
    }
    for disclosure in disclosures:
        run_id = str(disclosure.get("module_run_id") or "")
        disclosures_by_run[run_id].append(disclosure)
        disclosure_id = str(disclosure.get("id") or "")
        claim_id = str(disclosure.get("claim_investigation_id") or "")
        limitation_id = str(disclosure.get("limitation_id") or "")
        version_id = str(disclosure.get("document_version_id") or "")
        source_id = str(disclosure.get("document_source_id") or "")
        run = module_runs_by_id.get(run_id, {})
        source = valid_sources_by_id.get(source_id, {})
        resolved = resolve_version(
            disclosure, label=f"disclosure={disclosure_id}"
        )
        valid = (
            resolved is not None
            and bool(run)
            and str(run.get("module_code") or "") == "I4_S_SINGLE_REFERENCE"
            and str(run.get("claim_investigation_id") or "") == claim_id
            and limitation_id in limitations_by_claim.get(claim_id, set())
            and bool(source)
            and str(source.get("document_id") or "")
            == str(disclosure.get("document_id") or "")
            and str(source.get("document_version_id") or "") == version_id
        )
        if valid:
            valid_disclosures_by_run[run_id].append(disclosure)
        else:
            disclosure_binding_errors.append(
                f"disclosure={disclosure_id or '?'} run={run_id or '?'} "
                f"version={version_id or '?'}: claim/source/version binding invalid"
            )

    disclosure_version_bindings = not disclosure_binding_errors
    if not disclosure_version_bindings:
        errors.append("feature_disclosure 存在跨 document_version 的来源或运行绑定")

    valid_i4s_bindings: dict[str, tuple[str, str, str]] = {}
    i4s_binding_errors: list[str] = []
    for run in module_runs:
        if str(run.get("module_code") or "") != "I4_S_SINGLE_REFERENCE":
            continue
        run_id = str(run.get("id") or "")
        run_rows = disclosures_by_run.get(run_id, [])
        if str(run.get("status") or "") != "succeeded" and not run_rows:
            continue
        binding = _i4s_input_binding(run)
        document_id = str(binding.get("document_id") or "")
        version_id = str(binding.get("document_version_id") or "")
        content_sha256 = str(
            binding.get("document_content_sha256")
            or binding.get("content_sha256")
            or ""
        ).strip().lower()
        version = document_versions_by_id.get(version_id, {})
        row_pairs = {
            (
                str(row.get("document_id") or ""),
                str(row.get("document_version_id") or ""),
            )
            for row in run_rows
        }
        if not binding and len(row_pairs) == 1:
            # ReportDataV1 deliberately omits the potentially sensitive raw
            # input_snapshot.  Its hashed disclosure rows are therefore the
            # public exact-version binding for this run.
            document_id, version_id = next(iter(row_pairs))
            version = document_versions_by_id.get(version_id, {})
            content_sha256 = str(version.get("content_sha256") or "").lower()
        valid = (
            bool(run_id and document_id and version_id)
            and version_id in valid_document_version_ids
            and version_id in analysis_ready_document_version_ids
            and str(version.get("document_id") or "") == document_id
            and content_sha256 == str(version.get("content_sha256") or "").lower()
            and _is_sha256(run.get("input_sha256"))
            and _is_sha256(run.get("output_sha256"))
            and row_pairs == {(document_id, version_id)}
            and len(valid_disclosures_by_run.get(run_id, [])) == len(run_rows)
        )
        if valid:
            valid_i4s_bindings[run_id] = (document_id, version_id, content_sha256)
        else:
            i4s_binding_errors.append(
                f"I4-S run={run_id or '?'} document={document_id or '?'} "
                f"version={version_id or '?'}: input content binding does not match disclosures"
            )

    i4s_version_bindings = not i4s_binding_errors
    if not i4s_version_bindings:
        errors.append("I4-S 输入、披露与不可变 document_version/content_sha 不一致")

    complete_evidence_chains: list[dict[str, Any]] = []
    complete_chain_keys: set[tuple[str, str]] = set()
    cross_version_errors: list[str] = []
    for run in module_runs:
        if str(run.get("module_code") or "") != "I4_S_SINGLE_REFERENCE":
            continue
        run_id = str(run.get("id") or "")
        claim_id = str(run.get("claim_investigation_id") or "")
        model = str(run.get("model_version") or "").strip().lower()
        run_rows = valid_disclosures_by_run.get(run_id, [])
        binding = valid_i4s_bindings.get(run_id)
        document_ids = {str(row.get("document_id") or "") for row in run_rows}
        document_version_ids = {
            str(row.get("document_version_id") or "") for row in run_rows
        }
        run_audit_ok = (
            binding is not None
            and str(run.get("status") or "") == "succeeded"
            and str(run.get("requested_mode") or "").lower() == "live"
            and str(run.get("effective_mode") or "").lower() == "live"
            and model.startswith("glm-4.6v")
            and _is_sha256(run.get("input_sha256"))
            and _is_sha256(run.get("output_sha256"))
            and _positive_int(run.get("used_target_images"))
            and _positive_int(run.get("used_document_images"))
        )
        required_limitations = limitations_by_claim.get(claim_id, set())
        compared_limitations = {
            str(row.get("limitation_id") or "") for row in run_rows
        }
        rows_are_multimodal = bool(run_rows) and all(
            str(row.get("model_version") or model).lower().startswith("glm-4.6v")
            and _positive_int(_mapping(row.get("analysis")).get("used_target_images"))
            and _positive_int(_mapping(row.get("analysis")).get("used_document_images"))
            for row in run_rows
        )
        if (
            not run_audit_ok
            or len(document_ids) != 1
            or len(document_version_ids) != 1
            or not required_limitations
            or not required_limitations.issubset(compared_limitations)
            or not rows_are_multimodal
        ):
            continue
        document_id = next(iter(document_ids))
        document_version_id = next(iter(document_version_ids))
        source_ids = {
            str(row.get("document_source_id") or "")
            for row in run_rows
            if row.get("document_source_id")
        }
        if not source_ids or not any(
            source_id in valid_sources_by_id for source_id in source_ids
        ):
            continue
        document = documents_by_id.get(document_id, {})
        if (claim_id, document_version_id) not in verified_date_qualifications:
            alternate_versions = {
                version_id
                for (qualification_claim_id, version_id) in verified_date_qualifications
                if qualification_claim_id == claim_id
                and str(document_versions_by_id.get(version_id, {}).get("document_id") or "")
                == document_id
            }
            if alternate_versions:
                cross_version_errors.append(
                    f"claim={claim_id} document={document_id}: I4-S uses "
                    f"{document_version_id}, qualification uses {sorted(alternate_versions)}"
                )
            continue
        version = document_versions_by_id[document_version_id]
        complete_chain_keys.add((claim_id, document_version_id))
        complete_evidence_chains.append(
            {
                "claim_investigation_id": claim_id,
                "document_id": document_id,
                "document_version_id": document_version_id,
                "content_sha256": version.get("content_sha256"),
                "module_run_id": run_id,
                "model_version": str(run.get("model_version") or ""),
                "source_category": _source_category(
                    valid_sources_by_version[document_version_id][0], document
                ),
            }
        )

    multimodal_i4s = bool(complete_evidence_chains)
    if not multimodal_i4s:
        errors.append(
            "没有绑定真实快照和日期资格、且由 glm-4.6v* 同时使用目标图/文献图完成的 I4-S 全限制链"
        )

    d1_binding_errors: list[str] = []
    d1_by_id: dict[str, Mapping[str, Any]] = {}
    for selection in closest_prior_art:
        selection_id = str(selection.get("id") or "")
        claim_id = str(selection.get("claim_investigation_id") or "")
        version_id = str(selection.get("document_version_id") or "")
        resolved = resolve_version(selection, label=f"D1={selection_id}")
        qualification = verified_date_qualifications.get((claim_id, version_id), {})
        valid = (
            bool(selection_id and claim_id)
            and resolved is not None
            and bool(qualification)
            and qualification.get("inventive_step_eligible") is True
            and str(qualification.get("eligibility_type") or "")
            != "cn_conflicting_application"
            and (claim_id, version_id) in complete_chain_keys
        )
        if valid:
            d1_by_id[selection_id] = selection
        else:
            d1_binding_errors.append(
                f"D1={selection_id or '?'} claim={claim_id or '?'} "
                f"version={version_id or '?'}: exact-version qualification/I4-S missing"
            )
    d1_version_bindings = not d1_binding_errors
    if not d1_version_bindings:
        errors.append("D1 存在跨版本资格/披露拼接或缺少不可变版本绑定")

    combination_binding_errors: list[str] = []
    for combination in combinations:
        combination_id = str(combination.get("id") or "")
        claim_id = str(combination.get("claim_investigation_id") or "")
        iteration_id = str(combination.get("iteration_id") or "")
        run_id = str(combination.get("module_run_id") or "")
        selection_id = str(combination.get("closest_prior_art_version_id") or "")
        document_ids = _string_list(combination.get("document_ids"))
        version_ids = _string_list(combination.get("document_version_ids"))
        content_sha256s = _string_list(
            combination.get("document_content_sha256s")
            or combination.get("content_sha256s")
        )
        selection = d1_by_id.get(selection_id, {})
        run = module_runs_by_id.get(run_id, {})
        pairs_are_valid = bool(document_ids) and len(document_ids) == len(version_ids)
        pairs: list[tuple[str, str]] = []
        if pairs_are_valid:
            for index, (document_id, version_id) in enumerate(
                zip(document_ids, version_ids)
            ):
                version = document_versions_by_id.get(version_id, {})
                version_sha256 = str(version.get("content_sha256") or "").lower()
                if (
                    version_id not in valid_document_version_ids
                    or str(version.get("document_id") or "") != document_id
                    or (content_sha256s and (
                        len(content_sha256s) != len(version_ids)
                        or content_sha256s[index].lower() != version_sha256
                    ))
                    or (claim_id, version_id) not in complete_chain_keys
                    or verified_date_qualifications.get(
                        (claim_id, version_id), {}
                    ).get("inventive_step_eligible")
                    is not True
                ):
                    pairs_are_valid = False
                    break
                pairs.append((document_id, version_id))
        selected_pair = (
            str(selection.get("document_id") or ""),
            str(selection.get("document_version_id") or ""),
        )
        analysis = (
            combination.get("analysis")
            if isinstance(combination.get("analysis"), Mapping)
            else {}
        )
        feature_analysis = (
            analysis.get("distinguishing_feature_analysis")
            if isinstance(analysis, Mapping)
            else []
        )
        if not isinstance(feature_analysis, Sequence) or isinstance(
            feature_analysis, (str, bytes)
        ):
            feature_analysis = []
        complete_combination = str(combination.get("status") or "") == "evidence_complete"
        three_step_valid = (
            not complete_combination
            or (
                bool(feature_analysis)
                and all(
                    isinstance(item, Mapping)
                    and item.get("evidence_chain_complete") is True
                    for item in feature_analysis
                )
                and analysis.get("conclusion")
                == "lack_of_inventive_step_evidence_complete"
                and analysis.get("evidence_complete") is True
            )
        )
        valid = (
            bool(combination_id and claim_id and selection)
            and pairs_are_valid
            and selected_pair in pairs
            and bool(run)
            and str(run.get("module_code") or "") == "I4_I_INVENTIVE_STEP"
            and str(run.get("claim_investigation_id") or "") == claim_id
            and str(run.get("iteration_id") or "") == iteration_id
            and str(run.get("prompt_version") or "") == I4I_PROMPT_VERSION
            and str(run.get("rule_version") or "") == I4I_RULE_VERSION
            and three_step_valid
        )
        if not valid:
            combination_binding_errors.append(
                f"combination={combination_id or '?'} claim={claim_id or '?'}: "
                "D1/document/document_version/content chain invalid"
            )
    combination_version_bindings = not combination_binding_errors
    if not combination_version_bindings:
        errors.append("创造性组合存在跨 document_version 拼接或 D1 版本不一致")

    cross_version_stitching_free = not cross_version_errors
    if not cross_version_stitching_free:
        errors.append("检测到同一 document 不同版本之间拼接日期资格与 I4-S 披露")

    checks = {
        "immutable_report": immutable_report,
        "live_mode_only": live_mode_only,
        "query_matrix_complete": query_matrix_complete,
        "structured_network_use": structured_network_use and query_matrix_complete,
        "candidate_filter_fetch_ordering": candidate_filter_fetch_ordering,
        "immutable_document_versions": immutable_document_versions,
        "analysis_ready_document_versions": analysis_ready_document_versions,
        "source_version_bindings": source_version_bindings,
        "qualification_version_bindings": qualification_version_bindings,
        "disclosure_version_bindings": disclosure_version_bindings,
        "i4s_version_bindings": i4s_version_bindings,
        "d1_version_bindings": d1_version_bindings,
        "combination_version_bindings": combination_version_bindings,
        "cross_version_stitching_free": cross_version_stitching_free,
        "immutable_source_snapshot": immutable_source_snapshot,
        "verified_date_qualification": verified_date_qualification,
        "multimodal_i4s_complete_chain": multimodal_i4s,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "errors": errors,
        "missing_query_lanes": missing_query_lanes,
        "valid_source_snapshot_count": len(valid_sources_by_id),
        "document_version_count": len(document_versions_by_id),
        "valid_document_version_count": len(valid_document_version_ids),
        "analysis_ready_document_version_count": len(
            analysis_ready_document_version_ids
        ),
        "patent_source_snapshot": patent_source_snapshot,
        "npl_source_snapshot": npl_source_snapshot,
        "verified_date_qualification_count": len(verified_date_qualifications),
        "complete_evidence_chain_count": len(complete_evidence_chains),
        "complete_evidence_chains": complete_evidence_chains,
        "binding_errors": [
            *version_binding_errors,
            *readable_document_errors,
            *source_binding_errors,
            *qualification_binding_errors,
            *disclosure_binding_errors,
            *i4s_binding_errors,
            *d1_binding_errors,
            *combination_binding_errors,
            *cross_version_errors,
        ],
    }


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value).strip("._")
    return (cleaned or "patent")[:100]


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def atomic_write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, separators=(",", ":"), default=str)
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


class StageLedger:
    def __init__(self, attempt: int = 1) -> None:
        self.attempt = attempt
        self.rows: dict[str, dict[str, Any]] = {
            stage: {
                "stage": stage,
                "status": "not_run",
                "started_at": None,
                "ended_at": None,
                "duration_seconds": 0.0,
                "attempts": 0,
                "retries": 0,
                "invocations": 0,
                "errors": [],
            }
            for stage in PIPELINE_STAGES
        }

    @contextmanager
    def track(self, stage: str) -> Iterator[None]:
        row = self.rows[stage]
        started = utcnow()
        tick = time.monotonic()
        if row["started_at"] is None:
            row["started_at"] = started.isoformat()
            row["attempts"] = 1
            row["retries"] = max(0, self.attempt - 1)
        row["status"] = "running"
        row["invocations"] += 1
        try:
            yield
        except Exception as exc:
            row["status"] = "failed"
            row["errors"].append(f"{type(exc).__name__}: {str(exc)[:500]}")
            raise
        else:
            row["status"] = "passed"
        finally:
            row["duration_seconds"] = round(
                float(row["duration_seconds"]) + time.monotonic() - tick, 6
            )
            row["ended_at"] = utcnow().isoformat()

    def touch(self, stage: str) -> None:
        with self.track(stage):
            pass

    def fail_unfinished(self, error: str) -> None:
        for row in self.rows.values():
            if row["status"] == "running":
                row["status"] = "failed"
                row["errors"].append(error[:500])
                row["ended_at"] = utcnow().isoformat()

    def dump(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self.rows.items()}


class RegressionRepository:
    """In-memory implementation of WorkflowRepository for contract regression."""

    def __init__(self, source_snapshot: dict[str, Any], ledger: StageLedger) -> None:
        self.ledger = ledger
        self.investigation = {
            "id": str(uuid.uuid4()),
            "status": "queued",
            "source_snapshot": source_snapshot,
            "settings": {
                "max_gap_search_iterations": MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS,
                "provider_mode": "fixture",
            },
            "budgets": {
                "max_gap_search_iterations": MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS
            },
            "workflow_state": {},
            "state_version": 0,
        }
        self.claims: dict[str, dict[str, Any]] = {}
        self.limitations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.module_runs: list[dict[str, Any]] = []
        self.persisted: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def _id() -> str:
        return str(uuid.uuid4())

    def _event(self, event_type: str, **payload: Any) -> None:
        self.events.append(
            {"at": utcnow().isoformat(), "event_type": event_type, "payload": payload}
        )

    def _document(self, document_id: Any) -> dict[str, Any]:
        row = next(
            (
                item
                for item in self.persisted["documents"]
                if str(item.get("id")) == str(document_id)
            ),
            None,
        )
        if row is None:
            raise ValueError(f"document 不存在: {document_id}")
        return row

    def _document_version(self, document_version_id: Any) -> dict[str, Any]:
        row = next(
            (
                item
                for item in self.persisted["document_versions"]
                if str(item.get("id")) == str(document_version_id)
            ),
            None,
        )
        if row is None:
            raise ValueError(f"document_version 不存在: {document_version_id}")
        return row

    def _bound_document_version(
        self, document_id: Any, document_version_id: Any | None
    ) -> dict[str, Any] | None:
        document = self._document(document_id)
        version_id = document_version_id or document.get("current_document_version_id")
        if not version_id:
            return None
        version = self._document_version(version_id)
        if str(version.get("document_id")) != str(document.get("id")):
            raise ValueError("document_version 不属于指定 document")
        return version

    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        return self.investigation if str(investigation_id) == self.investigation["id"] else None

    def update_investigation(self, investigation_id: str, **kwargs: Any) -> dict[str, Any]:
        if str(investigation_id) != self.investigation["id"]:
            raise KeyError(investigation_id)
        for key in (
            "status",
            "workflow_state",
            "error_message",
            "error_metadata",
            "completed_at",
        ):
            if key in kwargs:
                self.investigation[key] = kwargs[key]
        self.investigation["state_version"] += 1
        self._event(str(kwargs.get("event_type") or "investigation.updated"), status=kwargs.get("status"))
        return dict(self.investigation)

    def merge_investigation_workflow_state(
        self, investigation_id: str, patch: Mapping[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        if str(investigation_id) != self.investigation["id"]:
            raise KeyError(investigation_id)
        self.investigation["workflow_state"].update(dict(patch))
        self.investigation["state_version"] += 1
        self._event(str(kwargs.get("event_type") or "workflow.checkpoint_saved"))
        return dict(self.investigation)

    def create_claim_investigation(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": self._id(), "state_version": 0, "result_summary": {}, **kwargs}
        self.claims[row["id"]] = row
        self._event("claim.created", claim_id=row.get("claim_id"), status=row.get("status"))
        return row

    def update_claim_status(self, claim_id: str, status: str, **kwargs: Any) -> dict[str, Any]:
        row = self.claims[str(claim_id)]
        row["status"] = status
        row["state_version"] = int(row.get("state_version", 0)) + 1
        for key in (
            "current_iteration_no",
            "critical_date",
            "critical_date_basis",
            "target_publication_date",
        ):
            if key in kwargs:
                row[key] = kwargs[key]
        if kwargs.get("result_summary_patch"):
            row.setdefault("result_summary", {}).update(dict(kwargs["result_summary_patch"]))
        self._event("claim.status_changed", claim_id=row.get("claim_id"), status=status)
        if status == ClaimStatus.CRITICAL_DATE_REVIEW.value:
            self.ledger.touch("I1.5")
        return row

    def mark_claim_terminal(self, claim_id: str, **kwargs: Any) -> dict[str, Any]:
        row = self.claims[str(claim_id)]
        row.update(kwargs)
        row["status"] = kwargs["status"]
        row["state_version"] = int(row.get("state_version", 0)) + 1
        self._event(
            "claim.terminal",
            claim_id=row.get("claim_id"),
            status=kwargs.get("status"),
            terminal_reason=kwargs.get("terminal_reason"),
        )
        return row

    def list_claim_limitations(self, claim_id: str) -> list[dict[str, Any]]:
        return list(self.limitations[str(claim_id)])

    def insert_claim_limitations(
        self, claim_id: str, limitations: Sequence[Mapping[str, Any]], **_: Any
    ) -> list[dict[str, Any]]:
        rows = [{"id": self._id(), **dict(item)} for item in limitations]
        self.limitations[str(claim_id)] = rows
        self.persisted["limitations"].extend(rows)
        return rows

    def create_iteration(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": self._id(), **kwargs}
        self.persisted["iterations"].append(row)
        return row

    def update_iteration(self, iteration_id: str, **kwargs: Any) -> dict[str, Any]:
        row = next(
            item for item in self.persisted["iterations"] if item["id"] == str(iteration_id)
        )
        row.update(kwargs)
        if kwargs.get("status") in {"succeeded", "partial", "failed", "cancelled"}:
            row["completed_at"] = utcnow().isoformat()
        return row

    def upsert_query(self, **kwargs: Any) -> dict[str, Any]:
        row = {"id": self._id(), **kwargs}
        self.persisted["queries"].append(row)
        return row

    def update_query_execution(self, query_id: str, **kwargs: Any) -> dict[str, Any]:
        row = next(
            item for item in self.persisted["queries"] if item["id"] == str(query_id)
        )
        row.update(kwargs)
        return row

    def upsert_document(self, **kwargs: Any) -> dict[str, Any]:
        existing = next(
            (
                item
                for item in self.persisted["documents"]
                if item.get("canonical_key") == kwargs.get("canonical_key")
            ),
            None,
        )
        content_sha256 = str(kwargs.get("content_sha256") or "").strip().lower()
        if content_sha256 and not _is_sha256(content_sha256):
            raise ValueError("content_sha256 必须是 64 位十六进制 SHA-256")
        if existing is None:
            existing = {"id": str(kwargs.get("document_id") or self._id())}
            self.persisted["documents"].append(existing)
        identity_fields = {
            key: value
            for key, value in kwargs.items()
            if key
            not in {
                "document_id",
                "content_artifact_id",
                "content_mime_type",
                "content_byte_size",
                "acquisition_kind",
                "version_metadata",
                "actor",
            }
        }
        existing.update(identity_fields)
        if not content_sha256:
            return dict(existing)

        artifact_id = kwargs.get("content_artifact_id")
        if artifact_id:
            artifact = next(
                (
                    item
                    for item in self.persisted["artifacts"]
                    if str(item.get("id")) == str(artifact_id)
                ),
                None,
            )
            if artifact is None:
                raise ValueError("document version artifact 不存在")
            if str(artifact.get("investigation_id")) != str(
                existing.get("investigation_id")
            ):
                raise ValueError("document version artifact 不属于当前 investigation")
            if str(artifact.get("sha256") or "").lower() != content_sha256:
                raise ValueError("document version artifact SHA 不一致")
        version = next(
            (
                item
                for item in self.persisted["document_versions"]
                if str(item.get("document_id")) == str(existing["id"])
                and str(item.get("content_sha256") or "").lower()
                == content_sha256
            ),
            None,
        )
        if version is None:
            version = {
                "id": self._id(),
                "document_id": existing["id"],
                "version_no": 1
                + max(
                    (
                        int(item.get("version_no") or 0)
                        for item in self.persisted["document_versions"]
                        if str(item.get("document_id")) == str(existing["id"])
                    ),
                    default=0,
                ),
                "content_sha256": content_sha256,
                "content_artifact_id": artifact_id,
                "mime_type": kwargs.get("content_mime_type"),
                "byte_size": kwargs.get("content_byte_size"),
                "acquisition_kind": str(
                    kwargs.get("acquisition_kind") or "automatic_retrieval"
                ),
                "version_metadata": dict(kwargs.get("version_metadata") or {}),
                "created_by": str(kwargs.get("actor") or "orchestrator"),
                "created_at": utcnow().isoformat(),
            }
            self.persisted["document_versions"].append(version)
        existing["current_document_version_id"] = version["id"]
        existing["content_sha256"] = content_sha256
        self._event(
            "document.version_bound",
            document_id=existing["id"],
            document_version_id=version["id"],
            content_sha256=content_sha256,
        )
        return {
            **existing,
            "document_version_id": version["id"],
            "document_version_no": version["version_no"],
        }

    def get_document_version(self, document_version_id: Any) -> dict[str, Any] | None:
        try:
            return dict(self._document_version(document_version_id))
        except ValueError:
            return None

    def list_document_versions(self, document_id: Any) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self.persisted["document_versions"]
            if str(item.get("document_id")) == str(document_id)
        ]

    def insert_artifact(self, **kwargs: Any) -> dict[str, Any]:
        if str(kwargs.get("investigation_id")) != str(self.investigation["id"]):
            raise ValueError("artifact 不属于当前 regression investigation")
        sha256 = str(kwargs.get("sha256") or "").strip().lower()
        if not _is_sha256(sha256):
            raise ValueError("artifact sha256 必须是 64 位十六进制 SHA-256")
        row = {
            "id": str(kwargs.get("artifact_id") or self._id()),
            **{
                key: value
                for key, value in kwargs.items()
                if key not in {"artifact_id", "actor"}
            },
            "sha256": sha256,
            "created_at": utcnow().isoformat(),
        }
        self.persisted["artifacts"].append(row)
        return row

    def upsert_document_source(self, **kwargs: Any) -> dict[str, Any]:
        document = self._document(kwargs.get("document_id"))
        version = self._bound_document_version(
            document["id"], kwargs.get("document_version_id")
        )
        snapshot_sha256 = str(kwargs.get("snapshot_sha256") or "").strip().lower()
        if version is not None and snapshot_sha256:
            if snapshot_sha256 != str(version.get("content_sha256") or "").lower():
                raise ValueError("source snapshot SHA 与 document_version 不一致")
        artifact_id = kwargs.get("artifact_id")
        if artifact_id:
            artifact = next(
                (
                    item
                    for item in self.persisted["artifacts"]
                    if str(item.get("id")) == str(artifact_id)
                ),
                None,
            )
            if artifact is None or str(artifact.get("investigation_id")) != str(
                document.get("investigation_id")
            ):
                raise ValueError("source artifact 不属于 document investigation")
        identity = (
            str(document["id"]),
            str(kwargs.get("provider") or ""),
            str(kwargs.get("source_url") or ""),
            str(kwargs.get("retrieved_at") or ""),
        )
        existing = next(
            (
                item
                for item in self.persisted["sources"]
                if (
                    str(item.get("document_id")),
                    str(item.get("provider") or ""),
                    str(item.get("source_url") or ""),
                    str(item.get("retrieved_at") or ""),
                )
                == identity
            ),
            None,
        )
        version_id = version.get("id") if version else None
        if existing is not None:
            if existing.get("document_version_id") and str(
                existing.get("document_version_id")
            ) != str(version_id):
                raise ValueError("同一 source identity 已绑定其他 document_version")
            existing.update(kwargs)
            existing["document_version_id"] = version_id
            if snapshot_sha256:
                existing["snapshot_sha256"] = snapshot_sha256
            return existing
        row = {
            "id": str(kwargs.get("source_id") or self._id()),
            **{
                key: value
                for key, value in kwargs.items()
                if key not in {"source_id", "actor"}
            },
            "document_version_id": version_id,
        }
        if snapshot_sha256:
            row["snapshot_sha256"] = snapshot_sha256
        self.persisted["sources"].append(row)
        return row

    def upsert_document_qualification(self, **kwargs: Any) -> dict[str, Any]:
        document = self._document(kwargs.get("document_id"))
        version = self._bound_document_version(
            document["id"], kwargs.get("document_version_id")
        )
        if version is None:
            raise ValueError("qualification 必须绑定 document_version")
        claim_id = str(kwargs.get("claim_investigation_id") or "")
        if claim_id not in self.claims:
            raise ValueError("qualification claim 不存在")
        limitation_id = str(kwargs.get("limitation_id") or "")
        if limitation_id and limitation_id not in {
            str(item.get("id")) for item in self.limitations[claim_id]
        }:
            raise ValueError("qualification limitation 不属于指定 claim")
        assessment_version = int(kwargs.get("assessment_version") or 1)
        previous = [
            item
            for item in self.persisted["qualifications"]
            if str(item.get("document_id")) == str(document["id"])
            and str(item.get("claim_investigation_id")) == claim_id
            and str(item.get("limitation_id") or "") == limitation_id
        ]
        latest = max(
            previous,
            key=lambda item: int(item.get("assessment_version") or 0),
            default=None,
        )
        if (
            latest is not None
            and str(latest.get("document_version_id")) != str(version["id"])
            and assessment_version <= int(latest.get("assessment_version") or 0)
        ):
            assessment_version = int(latest.get("assessment_version") or 0) + 1
        existing = next(
            (
                item
                for item in previous
                if str(item.get("document_version_id")) == str(version["id"])
                and int(item.get("assessment_version") or 0) == assessment_version
            ),
            None,
        )
        values = {
            key: value
            for key, value in kwargs.items()
            if key not in {"qualification_id", "actor"}
        }
        values.update(
            {
                "document_version_id": version["id"],
                "assessment_version": assessment_version,
            }
        )
        if existing is not None:
            existing.update(values)
            return existing
        row = {
            "id": str(kwargs.get("qualification_id") or self._id()),
            **values,
        }
        if latest is not None and str(latest.get("document_version_id")) != str(
            version["id"]
        ):
            row.setdefault("supersedes_id", latest["id"])
        self.persisted["qualifications"].append(row)
        return row

    def list_module_runs(self, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            item
            for item in self.module_runs
            if item.get("investigation_id") == kwargs.get("investigation_id")
            and item.get("module_code") == kwargs.get("module_code")
        ]

    def create_module_run(self, **kwargs: Any) -> dict[str, Any]:
        existing = next(
            (
                item
                for item in self.module_runs
                if item.get("idempotency_key") == kwargs.get("idempotency_key")
                and item.get("module_code") == kwargs.get("module_code")
            ),
            None,
        )
        if existing:
            return existing
        input_snapshot = dict(kwargs.get("input_snapshot") or {})
        row = {
            "id": self._id(),
            **kwargs,
            "input_snapshot": input_snapshot,
            "input_sha256": _canonical_json_sha256(input_snapshot),
            "requested_mode": "fixture",
            "effective_mode": "fixture",
        }
        self.module_runs.append(row)
        return row

    def finish_module_run(self, module_run_id: str, **kwargs: Any) -> dict[str, Any]:
        row = next(item for item in self.module_runs if item["id"] == str(module_run_id))
        row.update(kwargs)
        output = kwargs.get("output_snapshot")
        if isinstance(output, Mapping):
            row["output_sha256"] = hashlib.sha256(
                json.dumps(
                    output,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            for key in ("used_target_images", "used_document_images"):
                if output.get(key) is not None:
                    row[key] = output[key]
        row["completed_at"] = utcnow().isoformat()
        return row

    def insert_feature_disclosures(self, **kwargs: Any) -> list[dict[str, Any]]:
        document = self._document(kwargs.get("document_id"))
        version = self._bound_document_version(
            document["id"], kwargs.get("document_version_id")
        )
        if version is None:
            raise ValueError("disclosure 必须绑定 document_version")
        source_id = kwargs.get("document_source_id")
        if source_id:
            source = next(
                (
                    item
                    for item in self.persisted["sources"]
                    if str(item.get("id")) == str(source_id)
                ),
                None,
            )
            if (
                source is None
                or str(source.get("document_id")) != str(document["id"])
                or str(source.get("document_version_id")) != str(version["id"])
            ):
                raise ValueError("disclosure source 不属于指定 document_version")
        rows = [
            {
                "id": self._id(),
                "module_run_id": kwargs.get("module_run_id"),
                "claim_investigation_id": kwargs.get("claim_investigation_id"),
                "document_id": kwargs.get("document_id"),
                "document_version_id": version["id"],
                "document_source_id": kwargs.get("document_source_id"),
                "model_version": kwargs.get("model_version"),
                "prompt_version": kwargs.get("prompt_version"),
                "rule_version": kwargs.get("rule_version"),
                **dict(item),
            }
            for item in kwargs.get("disclosures") or []
        ]
        self.persisted["disclosures"].extend(rows)
        return rows

    def insert_closest_prior_art_version(self, **kwargs: Any) -> dict[str, Any]:
        with self.ledger.track("I4-C"):
            document = self._document(kwargs.get("document_id"))
            version = self._bound_document_version(
                document["id"], kwargs.get("document_version_id")
            )
            if version is None:
                raise ValueError("D1 必须绑定 document_version")
            previous = [
                item
                for item in self.persisted["closest_prior_art"]
                if str(item.get("claim_investigation_id"))
                == str(kwargs.get("claim_investigation_id"))
            ]
            for item in previous:
                item["is_current"] = False
            latest = max(
                previous,
                key=lambda item: int(item.get("version_no") or 0),
                default=None,
            )
            row = {
                "id": self._id(),
                **kwargs,
                "document_version_id": version["id"],
                "version_no": int(latest.get("version_no") or 0) + 1
                if latest
                else 1,
                "supersedes_id": latest.get("id") if latest else None,
                "is_current": True,
            }
            self.persisted["closest_prior_art"].append(row)
            return row

    def insert_gaps(self, **kwargs: Any) -> list[dict[str, Any]]:
        if "open_gaps" in kwargs:
            return self.reconcile_gap_frontier(**kwargs)
        return self.reconcile_gap_frontier(
            iteration_id=kwargs["iteration_id"],
            claim_investigation_id=kwargs["claim_investigation_id"],
            open_gaps=kwargs.get("gaps") or (),
            source_module_run_id=kwargs.get("source_module_run_id"),
            actor=str(kwargs.get("actor") or "orchestrator"),
        )

    def reconcile_gap_frontier(
        self,
        *,
        iteration_id: uuid.UUID | str,
        claim_investigation_id: uuid.UUID | str,
        open_gaps: Sequence[Mapping[str, Any]],
        source_module_run_id: uuid.UUID | str | None = None,
        closed_gap_resolutions: Mapping[str, Any]
        | Sequence[Mapping[str, Any]]
        | None = None,
        actor: str = "I0",
    ) -> list[dict[str, Any]]:
        iteration_key = str(iteration_id)
        claim_key = str(claim_investigation_id)
        iteration = next(
            item
            for item in self.persisted["iterations"]
            if str(item.get("id")) == iteration_key
            and str(item.get("claim_investigation_id")) == claim_key
        )
        current_rows = [
            item
            for item in self.persisted["gaps"]
            if str(item.get("iteration_id")) == iteration_key
            and str(item.get("claim_investigation_id")) == claim_key
        ]
        if current_rows:
            return [dict(item) for item in current_rows]

        iteration_numbers = {
            str(item.get("id")): int(item.get("iteration_no") or 0)
            for item in self.persisted["iterations"]
        }
        previous_rows = [
            item
            for item in self.persisted["gaps"]
            if str(item.get("claim_investigation_id")) == claim_key
            and iteration_numbers.get(str(item.get("iteration_id")), 0)
            < int(iteration.get("iteration_no") or 0)
        ]
        plan = plan_gap_frontier(
            iteration_no=int(iteration.get("iteration_no") or 0),
            open_gaps=open_gaps,
            previous_gap_items=previous_rows,
            default_source_module_run_id=source_module_run_id,
            closed_gap_resolutions=closed_gap_resolutions,
        )
        rows = [
            {
                "id": self._id(),
                "iteration_id": iteration_key,
                "claim_investigation_id": claim_key,
                **dict(item),
            }
            for item in plan["rows"]
        ]
        self.persisted["gaps"].extend(rows)
        self._event(
            "gap_frontier.committed",
            claim_investigation_id=claim_key,
            iteration_id=iteration_key,
            actor=actor,
            frontier_fingerprint=plan["frontier_fingerprint"],
            delta=plan["delta"],
        )
        return rows

    def insert_combination(self, **kwargs: Any) -> dict[str, Any]:
        document_ids = _string_list(kwargs.get("document_ids"))
        version_ids = _string_list(kwargs.get("document_version_ids"))
        if not version_ids:
            version_ids = [
                str(self._bound_document_version(document_id, None)["id"])
                for document_id in document_ids
            ]
        if len(document_ids) != len(version_ids):
            raise ValueError("document_version_ids 必须与 document_ids 一一对应")
        for document_id, version_id in zip(document_ids, version_ids):
            self._bound_document_version(document_id, version_id)
        selection = next(
            (
                item
                for item in self.persisted["closest_prior_art"]
                if str(item.get("id"))
                == str(kwargs.get("closest_prior_art_version_id"))
            ),
            None,
        )
        if selection is None:
            raise ValueError("combination 缺少 D1 version")
        row = {
            "id": self._id(),
            **kwargs,
            "document_ids": document_ids,
            "document_version_ids": version_ids,
        }
        self.persisted["combinations"].append(row)
        return row


def claim_outcome(claim_id: str) -> str:
    if claim_id == "1":
        return "inventive_round_2"
    if claim_id.isdigit() and int(claim_id) % 5 == 0:
        return "exhausted_round_3"
    return "novelty_round_1"


def split_fixture_limitations(claim_id: str, claim_text: str, subject: str) -> list[Limitation]:
    cleaned = re.sub(r"^\s*\d+\s*[.．、,，]\s*", "", claim_text).strip()
    parts = [
        item.strip()
        for item in re.split(r"[；;。]|(?<=，)", cleaned)
        if len(normal(item)) >= 6
    ]
    if len(parts) < 2:
        midpoint = max(1, len(cleaned) // 2)
        parts = [cleaned[:midpoint].strip(), cleaned[midpoint:].strip()]
    if not parts[1]:
        parts[1] = f"{subject}的第二个确定性回归限制"
    return [
        Limitation(
            feature_id=f"{claim_id}-fixture-f{index}",
            claim_id=claim_id,
            sequence=index,
            text=text[:800],
            technical_subject=subject,
            visual_relevance="fixture pipeline coverage only",
        )
        for index, text in enumerate(parts[:2], start=1)
    ]


class FixtureAnalysisEngine:
    def __init__(
        self,
        *,
        title: str,
        ledger: StageLedger,
        coverage: dict[str, set[str]],
    ) -> None:
        self.title = title or "目标专利技术客体"
        self.ledger = ledger
        self.coverage = coverage

    def plan_queries(self, **kwargs: Any) -> QueryPlan:
        with self.ledger.track("I2"):
            claim_id = str(kwargs["claim_id"])
            iteration = int(kwargs["iteration_number"])
            limitations = list(kwargs.get("existing_limitations") or ())
            if not limitations:
                limitations = split_fixture_limitations(
                    claim_id,
                    str(kwargs["expanded_claim_text"]),
                    self.title,
                )
            feature_ids = [item.feature_id for item in limitations]
            selected_feature_ids = (
                list(kwargs.get("gap_feature_ids") or [])
                if iteration > 1
                else feature_ids
            )
            if not selected_feature_ids:
                selected_feature_ids = feature_ids
            purpose = "initial" if iteration == 1 else "feature_uncovered"
            feature_text = {
                item.feature_id: item.text for item in limitations
            }
            expression = " ".join(
                [
                    self.title,
                    *(
                        feature_text.get(item, item)
                        for item in selected_feature_ids
                    ),
                ]
            )
            full_claim_queries = [
                SearchQuery(
                    query_id=f"{claim_id}-r{iteration}-fixture-patent",
                    provider_kind="patent",
                    purpose=purpose,
                    technical_subject=self.title,
                    feature_ids=selected_feature_ids,
                    expression=expression,
                    language="fixture",
                    rationale="deterministic simulation; not a real-world query",
                    search_objective=SearchObjective.FULL_CLAIM_SINGLE_REFERENCE,
                    date_channel=DateChannel.ORDINARY_PRIOR_ART,
                ),
                SearchQuery(
                    query_id=f"{claim_id}-r{iteration}-fixture-patent-conflicting",
                    provider_kind="patent",
                    purpose=purpose,
                    technical_subject=self.title,
                    feature_ids=selected_feature_ids,
                    expression=expression,
                    language="fixture",
                    rationale="deterministic simulation; conflicting-application lane",
                    search_objective=SearchObjective.FULL_CLAIM_SINGLE_REFERENCE,
                    date_channel=DateChannel.CN_CONFLICTING_APPLICATION,
                ),
                SearchQuery(
                    query_id=f"{claim_id}-r{iteration}-fixture-npl",
                    provider_kind="npl",
                    purpose=purpose,
                    technical_subject=self.title,
                    feature_ids=selected_feature_ids,
                    expression=expression,
                    language="fixture",
                    rationale="deterministic simulation; not a real-world query",
                    search_objective=SearchObjective.FULL_CLAIM_SINGLE_REFERENCE,
                    date_channel=DateChannel.ORDINARY_PRIOR_ART,
                ),
            ]
            queries = list(full_claim_queries) if iteration == 1 else []
            if iteration > 1:
                for provider_kind, date_channel in (
                    ("patent", DateChannel.ORDINARY_PRIOR_ART),
                    ("patent", DateChannel.CN_CONFLICTING_APPLICATION),
                    ("npl", DateChannel.ORDINARY_PRIOR_ART),
                ):
                    queries.append(
                        SearchQuery(
                            query_id=(
                                f"{claim_id}-r{iteration}-fixture-gap-"
                                f"{provider_kind}-{date_channel.value}"
                            ),
                            provider_kind=provider_kind,
                            purpose=purpose,
                            technical_subject=self.title,
                            feature_ids=selected_feature_ids,
                            expression=expression,
                            language="fixture",
                            rationale=(
                                "deterministic simulation; difference-feature/"
                                "combination-evidence lane"
                            ),
                            search_objective=SearchObjective.GAP_OR_COMBINATION,
                            date_channel=date_channel,
                            target_gap_type=GapType.FEATURE,
                            gap_feature_ids=selected_feature_ids,
                        )
                    )
            return QueryPlan(
                claim_id=claim_id,
                iteration_number=iteration,
                round_kind="initial" if iteration == 1 else "gap",
                technical_subject=self.title,
                limitations=limitations,
                queries=queries,
                model=FIXTURE_ENGINE,
                used_target_images=len(list(kwargs.get("target_images") or ())),
            )

    def compare_single_reference(self, **kwargs: Any) -> DocumentComparison:
        with self.ledger.track("I4-S"):
            document_id = str(kwargs["document_id"])
            covered = self.coverage.get(document_id, set())
            disclosures = []
            for limitation in kwargs["limitations"]:
                supported = limitation.feature_id in covered
                disclosures.append(
                    FeatureDisclosure(
                        feature_id=limitation.feature_id,
                        status=(
                            DisclosureStatus.EXPLICIT
                            if supported
                            else DisclosureStatus.NOT_DISCLOSED
                        ),
                        evidence_quote=(
                            f"fixture quote {limitation.feature_id}" if supported else ""
                        ),
                        evidence_location=(
                            "fixture paragraph 1" if supported else "fixture full document"
                        ),
                        reasoning="deterministic synthetic disclosure; not legal evidence",
                        confidence=0.99 if supported else 0.95,
                        target_structural_role=f"fixture role {limitation.feature_id}",
                        reference_structure_mapping=(
                            f"fixture mapped role {limitation.feature_id}"
                            if supported
                            else ""
                        ),
                        mapping_basis="literal" if supported else "none",
                    )
                )
            return DocumentComparison(
                document_id=document_id,
                disclosures=disclosures,
                field_alignment=0.9,
                purpose_alignment=0.9,
                effect_alignment=0.8,
                evidence_completeness=1.0,
                model=FIXTURE_ENGINE,
                used_target_images=len(list(kwargs.get("target_images") or ())),
                used_document_images=len(list(kwargs.get("document_images") or ())),
                analysis_rule_version=I4S_RULE_VERSION,
            )

    def analyze_inventive_step(self, **kwargs: Any) -> InventiveStepAssessment:
        with self.ledger.track("I4-I"):
            limitations = list(kwargs["limitations"])
            comparisons = list(kwargs["comparisons"])
            document_ids = [item.document_id for item in comparisons]
            outcome = claim_outcome(limitations[0].claim_id)
            coverage = [
                FeatureCoverage(
                    feature_id=limitation.feature_id,
                    disclosed_by_document_ids=[
                        item.document_id
                        for item in comparisons
                        if limitation.feature_id in self.coverage.get(item.document_id, set())
                    ],
                    covered=any(
                        limitation.feature_id in self.coverage.get(item.document_id, set())
                        for item in comparisons
                    ),
                )
                for limitation in limitations
            ]
            combined_complete = all(item.covered for item in coverage)
            complete = (
                outcome == "inventive_round_2"
                and combined_complete
                and any("-r2-npl-d2" in item for item in document_ids)
            )
            supported = CombinationCriterion(
                status="supported",
                evidence_quote="fixture combination teaching",
                evidence_location="fixture paragraph 2",
                reasoning="deterministic synthetic criterion; not legal evidence",
                confidence=0.99,
            )
            gaps = []
            if not complete:
                gaps.append(
                    AnalysisGap(
                        gap_id=f"{limitations[0].claim_id}-fixture-motivation-gap",
                        kind="combination_gap",
                        subtype="combination_motivation",
                        source_document_ids=document_ids,
                        search_anchor=f"{self.title} + fixture combination motivation",
                        rationale="fixture deliberately leaves combination motivation unresolved",
                    )
                )
            raw_differences = list(kwargs.get("distinguishing_features") or ())
            feature_analysis = []
            for difference in raw_differences:
                feature_id = str(getattr(difference, "feature_id", ""))
                supporters = [
                    item.document_id
                    for item in comparisons
                    if item.document_id != str(kwargs["closest_document_id"])
                    and feature_id in self.coverage.get(item.document_id, set())
                ]
                row_complete = bool(complete and supporters)
                feature_analysis.append(
                    {
                        "feature_id": feature_id,
                        "feature_text": str(getattr(difference, "feature_text", "")),
                        "d1_disclosure_status": str(
                            getattr(difference, "d1_disclosure_status", "not_disclosed")
                        ),
                        "objective_technical_problem": "fixture technical problem",
                        "supporting_document_ids": supporters,
                        "disclosure_complete": bool(supporters),
                        "same_role_and_effect": supported.model_dump(mode="json"),
                        "technical_teaching": (
                            supported
                            if row_complete
                            else supported.model_copy(update={"status": "uncertain"})
                        ).model_dump(mode="json"),
                        "modification_motivation": (
                            supported
                            if row_complete
                            else supported.model_copy(update={"status": "uncertain"})
                        ).model_dump(mode="json"),
                        "modification_path": (
                            "apply fixture supporting structure to D1"
                            if row_complete
                            else ""
                        ),
                        "teaching_away": supported.model_copy(
                            update={"status": "absent" if row_complete else "uncertain"}
                        ).model_dump(mode="json"),
                        "technical_effect": supported.model_copy(
                            update={"status": "predictable" if row_complete else "uncertain"}
                        ).model_dump(mode="json"),
                        "evidence_chain_complete": row_complete,
                        "unresolved_reasons": (
                            [] if row_complete else ["fixture evidence chain deliberately open"]
                        ),
                    }
                )
            return InventiveStepAssessment(
                closest_document_id=str(kwargs["closest_document_id"]),
                considered_document_ids=document_ids,
                combination_document_ids=document_ids,
                feature_coverage=coverage,
                distinguishing_feature_analysis=feature_analysis,
                combined_feature_coverage_complete=combined_complete,
                all_documents_date_eligible=True,
                related_technical_problem=supported.model_copy(
                    update={"status": "same_or_related"}
                ),
                combination_motivation=(
                    supported
                    if complete
                    else CombinationCriterion(
                        status="uncertain",
                        reasoning="fixture deliberately unresolved",
                        confidence=0.5,
                    )
                ),
                teaching_away=supported.model_copy(update={"status": "absent"}),
                technical_effect=supported.model_copy(update={"status": "predictable"}),
                evidence_complete=complete,
                gaps=gaps,
                conclusion=(
                    "lack_of_inventive_step_evidence_complete"
                    if complete
                    else "insufficient_evidence_to_establish_lack_of_inventive_step"
                ),
                conclusion_text=(
                    "现有证据已形成缺乏创造性的完整证据链（供律师复核）"
                    if complete
                    else "现有证据尚不足以证明不具备创造性"
                ),
                model=FIXTURE_ENGINE,
                used_target_images=len(list(kwargs.get("target_images") or ())),
                used_document_images=max(2, len(document_ids)),
            )

    def analyze_obviousness_precheck(
        self, **kwargs: Any
    ) -> ObviousnessPrecheckAssessment:
        """Keep fixture differences open without fabricating legal evidence."""

        with self.ledger.track("I4-O"):
            differences = list(kwargs.get("differences") or ())
            groups = []
            targets = []
            for index, difference in enumerate(differences, start=1):
                feature_id = str(getattr(difference, "feature_id", ""))
                feature_text = str(getattr(difference, "feature_text", ""))
                group_id = f"fixture-group-{index}"
                uncertain = {
                    "status": "uncertain",
                    "reasoning": "fixture leaves this fact unresolved; not legal evidence",
                    "confidence": 0.5,
                }
                groups.append(
                    {
                        "feature_group_id": group_id,
                        "feature_ids": [feature_id],
                        "feature_texts": [feature_text],
                        "objective_technical_problem": "fixture technical problem",
                        "d1_teaching": uncertain,
                        "routine_means": uncertain,
                        "modification_motivation": uncertain,
                        "teaching_away": uncertain,
                        "technical_effect": uncertain,
                        "search_route": "search_direct_feature_evidence",
                        "ordinary_structural_search_required": True,
                        "reasoning": "fixture requires later-round structural evidence",
                    }
                )
                targets.append(
                    {
                        "feature_group_id": group_id,
                        "feature_ids": [feature_id],
                        "route": "search_direct_feature_evidence",
                        "target_gap_type": "feature_gap",
                        "search_anchor": feature_text or feature_id,
                        "rationale": "fixture difference remains searchable",
                    }
                )
            return ObviousnessPrecheckAssessment.model_validate(
                {
                    "closest_document_id": str(kwargs["closest_document_id"]),
                    "feature_groups": groups,
                    "resolved_feature_ids": [],
                    "unresolved_feature_ids": [
                        str(getattr(item, "feature_id", "")) for item in differences
                    ],
                    "search_targets": targets,
                    "ordinary_structural_search_required": bool(differences),
                    "evidence_complete": not differences,
                    "model": FIXTURE_ENGINE,
                    "used_target_images": max(
                        1, len(list(kwargs.get("target_images") or ()))
                    ),
                    "used_document_images": max(
                        1, len(list(kwargs.get("document_images") or ()))
                    ),
                }
            )


class FixtureEvidenceGateway:
    def __init__(
        self,
        *,
        sample_id: str,
        target_images: Sequence[str],
        ledger: StageLedger,
        coverage: dict[str, set[str]],
        limitations_by_claim: dict[str, list[Limitation]],
    ) -> None:
        self.sample_id = safe_name(sample_id)
        self.target_images = list(target_images)
        self.ledger = ledger
        self.coverage = coverage
        self.limitations_by_claim = limitations_by_claim

    def search(
        self,
        query: SearchQuery,
        *,
        claim_id: str,
        iteration_number: int,
        critical_date: date,
        **_: Any,
    ) -> SearchBatch:
        stage = "I3-P" if query.provider_kind == "patent" else "I3-NPL"
        with self.ledger.track(stage):
            limitations = self.limitations_by_claim.get(claim_id) or []
            feature_ids = [item.feature_id for item in limitations]
            if not feature_ids:
                feature_ids = list(query.feature_ids)
                self.limitations_by_claim[claim_id] = [
                    Limitation(
                        feature_id=item,
                        claim_id=claim_id,
                        sequence=index,
                        text=f"fixture limitation {index}",
                        technical_subject=query.technical_subject,
                    )
                    for index, item in enumerate(feature_ids, start=1)
                ]
            covered: set[str] = set()
            suffix: str | None = None
            outcome = claim_outcome(claim_id)
            if (
                outcome == "novelty_round_1"
                and iteration_number == 1
                and query.provider_kind == "patent"
                and query.date_channel is DateChannel.ORDINARY_PRIOR_ART
            ):
                suffix, covered = "r1-patent-d1", set(feature_ids)
            elif outcome == "inventive_round_2":
                if (
                    iteration_number == 1
                    and query.provider_kind == "patent"
                    and query.date_channel is DateChannel.ORDINARY_PRIOR_ART
                ):
                    suffix, covered = "r1-patent-d1", {feature_ids[0]}
                elif iteration_number == 2 and query.provider_kind == "npl":
                    suffix, covered = "r2-npl-d2", {feature_ids[-1]}
            elif (
                outcome == "exhausted_round_3"
                and query.provider_kind == "patent"
                and query.date_channel is DateChannel.ORDINARY_PRIOR_ART
            ):
                if iteration_number in {1, 2}:
                    suffix, covered = f"r{iteration_number}-patent-d{iteration_number}", {feature_ids[0]}
                elif iteration_number == 3:
                    suffix, covered = "r3-patent-d3", {feature_ids[-1]}
            if suffix is None:
                return SearchBatch(provider=FIXTURE_PROVIDER, complete=True)
            record = self._record(
                claim_id=claim_id,
                suffix=suffix,
                covered=covered,
                critical_date=critical_date,
                source_type=(
                    "fixture_simulation_patent"
                    if query.provider_kind == "patent"
                    else "fixture_simulation_npl"
                ),
            )
            with self.ledger.track("I3-F"):
                raw_candidates = build_raw_candidates(
                    [
                        {
                            "search_run_id": (
                                f"fixture-{claim_id}-{iteration_number}"
                            ),
                            "query_id": query.query_id,
                            "query_text": query.expression,
                            "provider_kind": query.provider_kind,
                            "documents": [record.model_dump()],
                        }
                    ],
                    top_n_per_search=10,
                )
                groups, duplicates = group_candidates(raw_candidates)
                filtered = apply_filter_decisions(
                    groups,
                    target_context={
                        "protected_subject": query.technical_subject,
                        "concepts": [
                            {"text": item}
                            for item in (*query.subject_terms, *query.feature_terms)
                        ],
                        "classification_anchors": list(
                            query.classification_anchors
                        ),
                    },
                    model_failure=(
                        "fixture deterministic filter intentionally has no semantic model"
                    ),
                )
                if duplicates or filtered["excluded_candidates"] or len(
                    filtered["fetch_candidates"]
                ) != 1:
                    raise AssertionError(
                        "fixture I3-F must retain exactly one unique candidate"
                    )
            with self.ledger.track("I3-E"):
                key = f"{record.provider}:{record.external_id}"
                self.coverage[key] = set(covered)
                return SearchBatch(
                    provider=FIXTURE_PROVIDER,
                    records=(record,),
                    complete=True,
                )

    def _record(
        self,
        *,
        claim_id: str,
        suffix: str,
        covered: set[str],
        critical_date: date,
        source_type: str,
    ) -> EvidenceRecord:
        external_id = f"{self.sample_id}-c{claim_id}-{suffix}"
        publication = critical_date - timedelta(days=365)
        filing = critical_date - timedelta(days=730)
        full_text = "\n".join(
            [
                "FIXTURE SYNTHETIC DOCUMENT — NOT REAL PRIOR ART — NOT LEGAL EVIDENCE",
                *(f"fixture quote {item}" for item in sorted(covered)),
                "fixture combination teaching",
            ]
        )
        return EvidenceRecord(
            provider=FIXTURE_PROVIDER,
            source_type=source_type,
            external_id=external_id,
            title=f"[FIXTURE SYNTHETIC — NOT PRIOR ART] {external_id}",
            source_url=f"fixture://invalidity-regression/{external_id}",
            stage=EvidenceStage.QUALIFIED,
            authority="FIXTURE",
            language="fixture",
            publication_date=publication.isoformat(),
            filing_date=filing.isoformat(),
            full_text=full_text,
            image_urls=tuple(self.target_images[:1]),
            content_sha256=hashlib.sha256(full_text.encode("utf-8")).hexdigest(),
            retrieved_at=utcnow().isoformat(),
            provenance={
                "fixture_only": True,
                "synthetic": True,
                "not_real_prior_art": True,
                "legal_evidence_allowed": False,
                "target_image_reused_for_multimodal_contract_only": True,
                "public_date_evidence": "fixture simulation date; not legal evidence",
                "disclaimer": FIXTURE_DISCLAIMER,
            },
            eligibility={
                "category": "fixture_simulation_only",
                "novelty_eligible": True,
                "inventive_step_eligible": True,
                "requires_human_review": False,
                "public_availability_date": publication.isoformat(),
                "explanation": "fixture-only deterministic date; not a legal qualification",
            },
        )


def discover_cases(sample_root: Path, oracle_path: Path) -> list[dict[str, Any]]:
    if not sample_root.is_dir():
        raise FileNotFoundError(sample_root)
    sample_root = sample_root.resolve()
    oracle_payload = json.loads(oracle_path.read_text(encoding="utf-8"))
    oracle = oracle_payload.get("patents") or {}
    pdfs = sorted(
        (
            path.resolve()
            for path in sample_root.rglob("*")
            if path.is_file() and path.suffix.lower() == ".pdf"
        ),
        key=lambda path: str(path.relative_to(sample_root)).lower(),
    )
    if not pdfs:
        raise RuntimeError(f"样例目录没有发现 PDF 专利文件: {sample_root}")
    cases: list[dict[str, Any]] = []
    for path in pdfs:
        relative_path = str(path.relative_to(sample_root))
        expected = dict(oracle.get(relative_path) or oracle.get(path.name) or {})
        cases.append(
            {
                "file_name": path.name,
                "relative_path": relative_path,
                "path": path,
                "expected": expected,
                "oracle_status": "matched" if expected else "not_available",
            }
        )
    return cases


def parser_checks(
    parsed: Mapping[str, Any], claims: Sequence[ClaimSnapshot], expected: Mapping[str, Any]
) -> dict[str, bool]:
    metadata = parsed.get("metadata") or {}
    independent = [item.claim_id for item in claims if item.claim_type == "INDEPENDENT"]
    figures = list(parsed.get("figures") or [])
    figure_paths = [
        Path(str(item.get("file_path") or ""))
        for item in figures
        if item.get("file_path")
    ]
    checks = {
        "claims_present": bool(claims),
        "independent_claim_present": bool(independent),
        "publication_number_present": bool(metadata.get("patent_number")),
        "title_present": bool(metadata.get("title")),
        "abstract_figure_present": any(
            item.get("selection_role") == "abstract_figure" for item in figures
        ),
        "technical_drawing_present": any(
            item.get("selection_role") == "specification_drawing"
            for item in figures
        ),
        "target_images_materialized": bool(figure_paths)
        and all(path.is_file() and path.stat().st_size > 0 for path in figure_paths),
    }
    if expected.get("claim_count") is not None:
        checks["claim_count"] = len(claims) == int(expected["claim_count"])
    if expected.get("independent_claim_ids") is not None:
        checks["independent_claim_ids"] = independent == list(
            expected["independent_claim_ids"]
        )
    if expected.get("publication_number"):
        checks["publication_number"] = (
            metadata.get("patent_number") == expected["publication_number"]
        )
    if expected.get("title"):
        checks["title"] = normal(expected["title"]) in normal(metadata.get("title"))
    for field in ("application_date", "priority_date", "publication_date"):
        if field in expected and expected.get(field) is not None:
            checks[field] = metadata.get(field) == expected[field]
    actual_critical = metadata.get("priority_date") or metadata.get("application_date")
    if expected.get("critical_date") and actual_critical:
        checks["critical_date"] = actual_critical == expected["critical_date"]
    if "requires_ocr" in expected:
        checks["used_ocr"] = bool(parsed.get("used_ocr")) is bool(
            expected["requires_ocr"]
        )
    return checks


def make_patent_snapshot(
    path: Path, parsed: Mapping[str, Any], claims: Sequence[ClaimSnapshot]
) -> PatentSnapshot:
    metadata = parsed.get("metadata") or {}
    return PatentSnapshot(
        source_sha256=str(parsed["source_sha256"]),
        source_uri=str(path.resolve()),
        parser_task_id=f"fixture-regression-{str(parsed['source_sha256'])[:16]}",
        patent_number=metadata.get("patent_number"),
        title=metadata.get("title"),
        holder=metadata.get("patent_holder"),
        abstract=metadata.get("abstract"),
        application_date=metadata.get("application_date"),
        priority_date=metadata.get("priority_date"),
        publication_date=metadata.get("publication_date"),
        specification=dict(parsed.get("specification") or {}),
        claims=list(claims),
        figures=list(parsed.get("figures") or []),
        parser_errors=list(parsed.get("errors") or []),
    )


def fixture_document_audit(checkpoint: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for claim in checkpoint.claims.values():
        for document_id, payload in claim.documents.items():
            if document_id in seen:
                continue
            seen.add(document_id)
            record = payload.get("record") or {}
            provenance = record.get("provenance") or {}
            rows.append(
                {
                    "document_id": document_id,
                    "provider": record.get("provider"),
                    "source_type": record.get("source_type"),
                    "source_url": record.get("source_url"),
                    "evidence_stage_for_contract_testing": record.get("stage"),
                    "fixture_only": provenance.get("fixture_only") is True,
                    "synthetic": provenance.get("synthetic") is True,
                    "not_real_prior_art": provenance.get("not_real_prior_art") is True,
                    "legal_evidence_allowed": False,
                    "content_sha256": record.get("content_sha256"),
                }
            )
    return rows


def build_fixture_report(
    *,
    snapshot: PatentSnapshot,
    result: Any,
    repository: RegressionRepository,
) -> dict[str, Any]:
    """Build the real public I5 DTO while retaining explicit fixture provenance."""

    generated_at = utcnow()
    report_snapshot_id = str(uuid.uuid4())
    raw = {
        "investigation": {
            **repository.investigation,
            "analysis_session_id": f"fixture-{snapshot.source_sha256[:16]}",
            "environment": "test",
            "jurisdiction": "CN",
            "analysis_kind": "invalidity",
            "pipeline_version": "fixture-regression-workflow-v1",
            "completed_at": generated_at,
        },
        "claim_investigations": list(repository.claims.values()),
        "claim_limitations": repository.persisted["limitations"],
        "iterations": repository.persisted["iterations"],
        "module_runs": repository.module_runs,
        "jobs": [],
        "events": repository.events,
        "queries": repository.persisted["queries"],
        "documents": repository.persisted["documents"],
        "document_versions": repository.persisted["document_versions"],
        "document_sources": repository.persisted["sources"],
        "document_qualifications": repository.persisted["qualifications"],
        "feature_disclosures": repository.persisted["disclosures"],
        "closest_prior_art_versions": repository.persisted["closest_prior_art"],
        "gap_items": repository.persisted["gaps"],
        "combinations": repository.persisted["combinations"],
        "artifacts": repository.persisted["artifacts"],
        "critical_date_confirmations": [],
        "continuation_batches": [],
    }
    report = build_report_data(
        raw,
        preview=False,
        report_snapshot_id=report_snapshot_id,
        snapshot_sha256=None,
        generated_at=generated_at,
    )
    canonical = dict(report)
    canonical.pop("snapshot_sha256", None)
    report["snapshot_sha256"] = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    ReportDataV1.model_validate(report)
    return report


def run_fixture_case(
    case: Mapping[str, Any], *, run_id: str, output_dir: Path, attempt: int = 1
) -> dict[str, Any]:
    path = Path(case["path"])
    expected = dict(case["expected"])
    ledger = StageLedger(attempt)
    started = utcnow()
    try:
        with ledger.track("I1"):
            parsed = parse_source(str(path))
            claims = normalize_claims(parsed["claims"])
            checks = parser_checks(parsed, claims, expected)
            if not all(checks.values()):
                raise AssertionError(
                    "I1 oracle mismatch: "
                    + json.dumps(
                        {key: value for key, value in checks.items() if not value},
                        ensure_ascii=False,
                    )
                )
            snapshot = make_patent_snapshot(path, parsed, claims)
            target_images = [
                str(item.get("file_path"))
                for item in parsed.get("figures") or []
                if item.get("file_path")
            ]
            if not target_images:
                raise AssertionError("I1 did not materialize target images")

        source_snapshot = {
            "patent_snapshot": snapshot.model_dump(mode="json"),
            "target_images": target_images,
            "declared_critical_date": (
                expected.get("critical_date")
                or snapshot.priority_date
                or snapshot.application_date
            ),
            "target_publication_date": (
                expected.get("publication_date") or snapshot.publication_date
            ),
            "target_publication_date_verified": bool(
                expected.get("publication_date") or snapshot.publication_date
            ),
            "regression_mode": "fixture",
            "fixture_disclaimer": FIXTURE_DISCLAIMER,
        }
        if not source_snapshot["declared_critical_date"]:
            raise AssertionError("I1.5 cannot derive a critical date for fixture regression")
        repository = RegressionRepository(source_snapshot, ledger)
        coverage: dict[str, set[str]] = {}
        limitations_by_claim: dict[str, list[Limitation]] = {}
        engine = FixtureAnalysisEngine(
            title=snapshot.title or expected.get("title") or "目标专利技术客体",
            ledger=ledger,
            coverage=coverage,
        )
        gateway = FixtureEvidenceGateway(
            sample_id=(
                expected.get("publication_number")
                or snapshot.patent_number
                or snapshot.source_sha256[:16]
            ),
            target_images=target_images,
            ledger=ledger,
            coverage=coverage,
            limitations_by_claim=limitations_by_claim,
        )

        # Capture the same deterministic limitation IDs used by the engine so
        # the gateway can choose per-round coverage before I4-S is called.
        original_plan = engine.plan_queries

        def plan_and_capture(**kwargs: Any) -> QueryPlan:
            plan = original_plan(**kwargs)
            limitations_by_claim[plan.claim_id] = list(plan.limitations)
            return plan

        engine.plan_queries = plan_and_capture  # type: ignore[method-assign]
        result = InvalidityWorkflow(
            repository,
            engine,
            gateway,
            max_rounds=MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS,
            pipeline_version="fixture-regression-workflow-v1",
        ).execute_investigation(repository.investigation["id"])

        with ledger.track("I5"):
            report = build_fixture_report(
                snapshot=snapshot,
                result=result,
                repository=repository,
            )
            evidence = fixture_document_audit(result.checkpoint)
            if not evidence:
                raise AssertionError("fixture workflow persisted no simulated documents")
            if not all(
                item["fixture_only"]
                and item["synthetic"]
                and item["not_real_prior_art"]
                and item["legal_evidence_allowed"] is False
                and str(item["source_url"]).startswith("fixture://")
                for item in evidence
            ):
                raise AssertionError("fixture provenance guard failed")
            if result.status not in {"completed", "needs_human_review"}:
                raise AssertionError(f"unexpected fixture workflow status: {result.status}")
            if not repository.persisted["combinations"]:
                raise AssertionError("I4-I combination path was not exercised")
            if (
                report.get("contract_version") != "v1"
                or report.get("report_kind") != "invalidity_evidence_data"
                or report.get("is_preview") is not False
                or not report.get("report_snapshot_id")
                or not _is_sha256(report.get("snapshot_sha256"))
            ):
                raise AssertionError("fixture I5 未生成真实 ReportDataV1 契约快照")

        stage_rows = ledger.dump()
        missing_stages = [
            stage for stage in PIPELINE_STAGES if stage_rows[stage]["status"] != "passed"
        ]
        if missing_stages:
            raise AssertionError(f"pipeline stages not exercised: {missing_stages}")
        report_path = (
            output_dir
            / "reports"
            / f"{snapshot.source_sha256[:12]}-{safe_name(path.stem)}.fixture-report.json"
        )
        atomic_write_json(report_path, report)
        with fitz.open(path) as pdf:
            page_count = pdf.page_count
        claim_rows = [item.model_dump(mode="json") for item in result.claim_results]
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "mode": "fixture",
            "status": "passed",
            "fixture_simulation": True,
            "real_world_search_performed": False,
            "legal_evidence_allowed": False,
            "evidence_truth": "synthetic_fixture_only_not_prior_art",
            "analysis_engine": FIXTURE_ENGINE,
            "i5_execution_kind": (
                "real_report_data_v1_builder_without_durable_postgresql_job"
            ),
            "disclaimer": FIXTURE_DISCLAIMER,
            "sample": {
                "file_name": path.name,
                "relative_path": str(case.get("relative_path") or path.name),
                "oracle_status": case.get("oracle_status"),
                "publication_number": (
                    expected.get("publication_number") or snapshot.patent_number
                ),
                "source_sha256": parsed["source_sha256"],
                "byte_size": path.stat().st_size,
                "page_count": page_count,
                "claim_count": len(claims),
                "used_ocr": bool(parsed.get("used_ocr")),
            },
            "oracle_checks": checks,
            "pipeline_coverage": stage_rows,
            "workflow": {
                "investigation_id": result.investigation_id,
                "status": result.status,
                "claim_status_counts": dict(
                    (status, sum(item.status == status for item in result.claim_results))
                    for status in sorted({item.status for item in result.claim_results})
                ),
                "claims": claim_rows,
                "max_rounds_observed": max(
                    (item.rounds_completed for item in result.claim_results), default=0
                ),
                "provider_failures": result.provider_failures,
            },
            "records": {
                key: len(value)
                for key, value in report.items()
                if isinstance(value, list)
            },
            "evidence_audit": {
                "fixture_document_count": len(evidence),
                "all_fixture_only": all(
                    item["fixture_only"]
                    and item["synthetic"]
                    and item["not_real_prior_art"]
                    and item["legal_evidence_allowed"] is False
                    for item in evidence
                ),
            },
            "report_path": str(report_path),
            "started_at": started.isoformat(),
            "ended_at": utcnow().isoformat(),
            "elapsed_seconds": round((utcnow() - started).total_seconds(), 3),
            "attempts": attempt,
            "retries": max(0, attempt - 1),
            "errors": [],
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:1000]}"
        ledger.fail_unfinished(error)
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "mode": "fixture",
            "status": "failed",
            "fixture_simulation": True,
            "real_world_search_performed": False,
            "legal_evidence_allowed": False,
            "evidence_truth": "synthetic_fixture_only_not_prior_art",
            "analysis_engine": FIXTURE_ENGINE,
            "disclaimer": FIXTURE_DISCLAIMER,
            "sample": {
                "file_name": path.name,
                "relative_path": str(case.get("relative_path") or path.name),
                "oracle_status": case.get("oracle_status"),
                "publication_number": expected.get("publication_number"),
                "source_sha256": file_sha256(path) if path.is_file() else None,
            },
            "pipeline_coverage": ledger.dump(),
            "started_at": started.isoformat(),
            "ended_at": utcnow().isoformat(),
            "elapsed_seconds": round((utcnow() - started).total_seconds(), 3),
            "attempts": attempt,
            "retries": max(0, attempt - 1),
            "errors": [error],
        }


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    max_attempts: int = 3,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        try:
            response = client.request(method, url, json=dict(payload) if payload else None)
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("API response is not a JSON object")
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "passed",
                    "http_status": response.status_code,
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            )
            return body, attempts
        except Exception as exc:
            last = exc
            attempts.append(
                {
                    "attempt": attempt,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            )
            if attempt < max_attempts:
                time.sleep(min(2 ** (attempt - 1), 5))
    assert last is not None
    raise RuntimeError(
        f"API call failed after {max_attempts} attempts: {type(last).__name__}: {last}"
    )


def _date_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        return None


def live_date_context(path: Path, expected: Mapping[str, Any]) -> dict[str, str | None]:
    parsed = parse_source(str(path))
    metadata = dict(parsed.get("metadata") or {})
    priority_date = _date_text(metadata.get("priority_date"))
    application_date = _date_text(metadata.get("application_date"))
    critical_date = _date_text(
        expected.get("critical_date") or priority_date or application_date
    )
    publication_date = _date_text(
        expected.get("publication_date") or metadata.get("publication_date")
    )
    if critical_date is None:
        raise ValueError("live 回归无法从 oracle 或解析结果确定待确认关键日")
    return {
        "critical_date": critical_date,
        "priority_date": priority_date,
        "application_date": application_date,
        "publication_date": publication_date,
        "patent_number": str(
            expected.get("publication_number") or metadata.get("patent_number") or ""
        ).strip()
        or None,
    }


def poll_investigation(
    client: httpx.Client,
    *,
    base_url: str,
    investigation_id: str,
    deadline: float,
    poll_interval: float,
    api_calls: list[dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    poll_count = 0
    while time.monotonic() < deadline:
        investigation, calls = request_json(
            client,
            "GET",
            f"{base_url.rstrip('/')}/v1/investigations/{investigation_id}",
            max_attempts=3,
        )
        poll_count += 1
        api_calls.append(
            {
                "operation": "poll",
                "phase": phase,
                "poll": poll_count,
                "calls": calls,
            }
        )
        if str(investigation.get("status")) in TERMINAL_INVESTIGATION_STATUSES:
            return investigation
        time.sleep(max(0.2, poll_interval))
    raise TimeoutError(f"live investigation exceeded deadline during {phase}")


def require_live_reportable_status(
    investigation: Mapping[str, Any], *, investigation_id: str
) -> None:
    """Fail immediately when I0 ended in a state that cannot enqueue I5."""

    status = str(investigation.get("status") or "unknown")
    if status in LIVE_NON_REPORTABLE_TERMINAL_STATUSES:
        raise RuntimeError(
            f"live investigation {investigation_id} entered terminal status "
            f"{status}; no I5 report snapshot is expected"
        )


def confirm_pending_critical_dates(
    client: httpx.Client,
    *,
    base_url: str,
    investigation_id: str,
    claims_payload: Mapping[str, Any],
    date_context: Mapping[str, str | None],
    state_version: int,
    source_hash: str,
    api_calls: list[dict[str, Any]],
) -> tuple[list[str], int]:
    confirmed_claim_ids: list[str] = []
    critical_date = str(date_context["critical_date"])
    priority_date = date_context.get("priority_date")
    application_date = date_context.get("application_date")
    if priority_date and critical_date == priority_date:
        decision = "confirm_priority"
    elif application_date and critical_date == application_date:
        decision = "use_filing_date"
    else:
        decision = "set_manual_date"

    for claim in claims_payload.get("claims") or []:
        summary = claim.get("result_summary") or {}
        resolution = summary.get("critical_date_resolution") or {}
        if not (
            str(claim.get("status")) == "needs_human_review"
            and resolution.get("requires_human_review") is True
        ):
            continue
        claim_id = str(claim["id"])
        confirmation, calls = request_json(
            client,
            "POST",
            (
                f"{base_url.rstrip('/')}/v1/investigations/{investigation_id}/"
                "critical-date-confirmations"
            ),
            payload={
                "contract_version": "v1",
                "claim_investigation_id": claim_id,
                "decision": decision,
                "confirmed_date": critical_date,
                "target_publication_date": date_context.get("publication_date"),
                "basis": "full_regression_versioned_oracle_and_parser_metadata",
                "reason": (
                    "全量 live 回归按版本化 oracle 与目标专利解析元数据确认目标关键日/"
                    "公开日；本操作不确认任何候选对比文件的公开日期"
                ),
                "expected_state_version": state_version,
                "idempotency_key": (
                    f"date-{source_hash[:16]}-{claim_id[:16]}-{critical_date}"
                ),
            },
        )
        api_calls.append(
            {"operation": "critical_date_confirmation", "claim_id": claim_id, "calls": calls}
        )
        state_version = int(confirmation["investigation_state_version"])
        confirmed_claim_ids.append(claim_id)
    return confirmed_claim_ids, state_version


def run_live_case(
    case: Mapping[str, Any],
    *,
    run_id: str,
    output_dir: Path,
    base_url: str,
    token: str,
    upload_root: Path,
    poll_interval: float,
    timeout_seconds: float,
    attempt: int = 1,
) -> dict[str, Any]:
    path = Path(case["path"])
    expected = dict(case["expected"])
    started = utcnow()
    api_calls: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {token}"}
    investigation_id: str | None = None
    investigation: dict[str, Any] = {}
    claims: dict[str, Any] = {"claims": []}
    events: dict[str, Any] = {"events": []}
    try:
        with httpx.Client(timeout=120, headers=headers) as client:
            source_hash = file_sha256(path)
            staged_path = stage_live_source(
                path,
                upload_root=upload_root,
                run_id=run_id,
            )
            date_context = live_date_context(path, expected)
            create, calls = request_json(
                client,
                "POST",
                f"{base_url.rstrip('/')}/v1/investigations",
                payload={
                    "contract_version": "v1",
                    "analysis_session_id": (
                        f"full-live-{run_id}-{source_hash[:12]}-attempt-{attempt}"
                    ),
                    "source_path": str(staged_path),
                    "patent_number": date_context.get("patent_number"),
                    "max_rounds": MAX_AUTOMATIC_GAP_SEARCH_ITERATIONS,
                    "provider_mode": "live",
                    "idempotency_key": (
                        f"full-live-{run_id}-{source_hash[:24]}-attempt-{attempt}"
                    ),
                },
            )
            api_calls.append({"operation": "create", "calls": calls})
            investigation_id = str(create["investigation_id"])
            start_body, calls = request_json(
                client,
                "POST",
                f"{base_url.rstrip('/')}/v1/investigations/{investigation_id}/start",
            )
            api_calls.append({"operation": "start", "calls": calls})
            deadline = time.monotonic() + timeout_seconds
            investigation = poll_investigation(
                client,
                base_url=base_url,
                investigation_id=investigation_id,
                deadline=deadline,
                poll_interval=poll_interval,
                api_calls=api_calls,
                phase="initial",
            )
            claims, calls = request_json(
                client,
                "GET",
                f"{base_url.rstrip('/')}/v1/investigations/{investigation_id}/claims",
            )
            api_calls.append({"operation": "claims", "calls": calls})

            if str(investigation.get("status")) == "needs_human_review":
                confirmed_claim_ids, state_version = confirm_pending_critical_dates(
                    client,
                    base_url=base_url,
                    investigation_id=investigation_id,
                    claims_payload=claims,
                    date_context=date_context,
                    state_version=int(investigation.get("state_version") or 0),
                    source_hash=source_hash,
                    api_calls=api_calls,
                )
                if confirmed_claim_ids:
                    _continuation, calls = request_json(
                        client,
                        "POST",
                        (
                            f"{base_url.rstrip('/')}/v1/investigations/"
                            f"{investigation_id}/continuations"
                        ),
                        payload={
                            "contract_version": "v1",
                            "claim_investigation_ids": confirmed_claim_ids,
                            "max_additional_rounds": 3,
                            "reason": "关键日和目标公开日确认后继续全量 live 回归",
                            "expected_state_version": state_version,
                            "idempotency_key": (
                                f"continue-{run_id}-{source_hash[:24]}-"
                                f"attempt-{attempt}-after-date"
                            ),
                        },
                    )
                    api_calls.append({"operation": "continuation", "calls": calls})
                    investigation = poll_investigation(
                        client,
                        base_url=base_url,
                        investigation_id=investigation_id,
                        deadline=deadline,
                        poll_interval=poll_interval,
                        api_calls=api_calls,
                        phase="after_critical_date_confirmation",
                    )
                    claims, calls = request_json(
                        client,
                        "GET",
                        f"{base_url.rstrip('/')}/v1/investigations/{investigation_id}/claims",
                    )
                    api_calls.append({"operation": "claims_after_continuation", "calls": calls})

            events, calls = request_json(
                client,
                "GET",
                f"{base_url.rstrip('/')}/v1/investigations/{investigation_id}/events",
            )
            api_calls.append({"operation": "events", "calls": calls})
            require_live_reportable_status(
                investigation,
                investigation_id=investigation_id,
            )
            report: dict[str, Any] | None = None
            report_poll = 0
            while time.monotonic() < deadline:
                candidate, calls = request_json(
                    client,
                    "GET",
                    f"{base_url.rstrip('/')}/v1/investigations/{investigation_id}/report-data",
                )
                report_poll += 1
                api_calls.append(
                    {"operation": "report", "poll": report_poll, "calls": calls}
                )
                if candidate.get("code") != "REPORT_PENDING":
                    report = candidate
                    break
                time.sleep(max(0.2, poll_interval))
            if report is None:
                raise TimeoutError("不可变 ReportDataV1 快照未在 case deadline 前生成")
            if (
                report.get("contract_version") != "v1"
                or not report.get("report_snapshot_id")
                or not report.get("snapshot_sha256")
                or report.get("is_preview") is not False
            ):
                raise AssertionError("live report 不是已哈希的不可变 ReportDataV1 快照")

        report_path = (
            output_dir
            / "reports"
            / (
                f"{source_hash[:12]}-{safe_name(path.stem)}-"
                f"attempt-{attempt}.live-report.json"
            )
        )
        atomic_write_json(report_path, report)
        live_truth = audit_live_report_truth(report)
        module_codes = {
            str(item.get("module_code")) for item in report.get("module_runs") or []
        }
        queries = list(report.get("queries") or [])
        query_channels = {
            str(item.get("channel") or item.get("query_type") or "").lower()
            for item in queries
        }
        stage_observed = {
            "I1": bool(report.get("target_patent")),
            "I1.5": bool(report.get("critical_date_confirmations"))
            or any(item.get("critical_date") for item in report.get("claim_investigations") or []),
            "I2": bool(queries) or "I2_QUERY_PLAN" in module_codes,
            "I3-P": "I3_PATENT_SEARCH" in module_codes
            or any("patent" in item for item in query_channels),
            "I3-NPL": "I3_NPL_SEARCH" in module_codes
            or any("npl" in item for item in query_channels),
            "I3-F": bool(
                live_truth["checks"]["candidate_filter_fetch_ordering"]
            ),
            "I3-E": bool(report.get("document_sources"))
            or bool(report.get("document_qualifications"))
            or bool({"I3_FETCH", "I3_QUALIFY"}.intersection(module_codes)),
            "I4-S": bool(report.get("feature_disclosures"))
            or "I4_S_SINGLE_REFERENCE" in module_codes,
            "I4-C": bool(report.get("closest_prior_art_versions"))
            or "I4_C_CLOSEST_PRIOR_ART" in module_codes,
            "I4-I": bool(report.get("combinations"))
            or "I4_I_INVENTIVE_STEP" in module_codes,
            "I5": bool(report.get("report_snapshot_id")),
        }
        coverage = {
            stage: {
                "stage": stage,
                "status": "observed" if observed else "not_observed",
                "attempts": 1,
                "retries": 0,
                "errors": [],
            }
            for stage, observed in stage_observed.items()
        }
        real_world_search_attempted = bool(
            live_truth["checks"]["query_matrix_complete"]
        )
        real_world_search_performed = bool(
            live_truth["checks"]["immutable_source_snapshot"]
        )
        network_used = bool(live_truth["checks"]["structured_network_use"])
        terminal = str(investigation.get("status"))
        terminal_acceptable = terminal in {
            "completed",
            "partial",
            "needs_human_review",
        }
        passed = terminal_acceptable and bool(live_truth["passed"])
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "mode": "live",
            "status": "passed" if passed else "failed",
            "fixture_simulation": False,
            "real_world_search_attempted": real_world_search_attempted,
            "real_world_search_performed": real_world_search_performed,
            "network_used": network_used,
            "live_truth_gate_passed": bool(live_truth["passed"]),
            "live_truth_audit": live_truth,
            "patent_source_snapshot": bool(live_truth["patent_source_snapshot"]),
            "npl_source_snapshot": bool(live_truth["npl_source_snapshot"]),
            "multimodal_i4s": bool(
                live_truth["checks"]["multimodal_i4s_complete_chain"]
            ),
            "fixture_fallback_used": False,
            "legal_evidence_allowed": False,
            "evidence_truth": "live_provider_results_require_professional_verification",
            "report_is_legal_opinion": False,
            "disclaimer": LIVE_DISCLAIMER,
            "sample": {
                "file_name": path.name,
                "relative_path": str(case.get("relative_path") or path.name),
                "publication_number": date_context.get("patent_number"),
                "source_sha256": source_hash,
                "date_context": date_context,
            },
            "pipeline_coverage": coverage,
            "workflow": {
                "investigation_id": investigation_id,
                "status": terminal,
                "start_response": start_body,
                "claims": claims,
            },
            "api_calls": api_calls,
            "report_path": str(report_path),
            "started_at": started.isoformat(),
            "ended_at": utcnow().isoformat(),
            "elapsed_seconds": round((utcnow() - started).total_seconds(), 3),
            "attempts": attempt,
            "retries": max(0, attempt - 1),
            "errors": (
                []
                if passed
                else [
                    *([] if terminal_acceptable else [f"terminal investigation status: {terminal}"]),
                    *list(live_truth["errors"]),
                ]
            ),
        }
    except Exception as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "mode": "live",
            "status": "failed",
            "fixture_simulation": False,
            "real_world_search_attempted": False,
            "real_world_search_performed": False,
            "network_used": False,
            "live_truth_gate_passed": False,
            "live_truth_audit": {
                "passed": False,
                "checks": {},
                "errors": [f"{type(exc).__name__}: {str(exc)[:1000]}"],
            },
            "patent_source_snapshot": False,
            "npl_source_snapshot": False,
            "multimodal_i4s": False,
            "fixture_fallback_used": False,
            "legal_evidence_allowed": False,
            "evidence_truth": "live_provider_failure_no_fixture_fallback",
            "report_is_legal_opinion": False,
            "disclaimer": LIVE_DISCLAIMER,
            "sample": {
                "file_name": path.name,
                "relative_path": str(case.get("relative_path") or path.name),
                "publication_number": expected.get("publication_number"),
                "source_sha256": file_sha256(path) if path.is_file() else None,
            },
            "pipeline_coverage": {},
            "workflow": {
                "investigation_id": investigation_id,
                "status": investigation.get("status") or "not_started",
                "claims": claims.get("claims") or [],
                "events": events.get("events") or [],
            },
            "api_calls": api_calls,
            "started_at": started.isoformat(),
            "ended_at": utcnow().isoformat(),
            "elapsed_seconds": round((utcnow() - started).total_seconds(), 3),
            "attempts": attempt,
            "retries": max(0, attempt - 1),
            "errors": [f"{type(exc).__name__}: {str(exc)[:1000]}"],
        }


def build_summary(
    *, run_id: str, mode: str, sample_root: Path, results: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    passed = sum(item.get("status") == "passed" for item in results)
    failed = len(results) - passed
    status_counts: dict[str, int] = defaultdict(int)
    claim_status_counts: dict[str, int] = defaultdict(int)
    for item in results:
        workflow = item.get("workflow") or {}
        status_counts[str(workflow.get("status") or "not_started")] += 1
        for claim in workflow.get("claims") or []:
            if isinstance(claim, Mapping) and claim.get("status"):
                claim_status_counts[str(claim["status"])] += 1
    search_attempted_count = sum(
        bool(item.get("real_world_search_attempted")) for item in results
    )
    search_performed_count = sum(
        bool(item.get("real_world_search_performed")) for item in results
    )
    network_used_count = sum(bool(item.get("network_used")) for item in results)
    live_truth_count = sum(
        item.get("live_truth_gate_passed") is True for item in results
    )
    patent_snapshot_count = sum(
        item.get("patent_source_snapshot") is True for item in results
    )
    npl_snapshot_count = sum(
        item.get("npl_source_snapshot") is True for item in results
    )
    multimodal_i4s_count = sum(item.get("multimodal_i4s") is True for item in results)
    all_samples_completed = failed == 0
    all_cases_attempted_search = bool(results) and search_attempted_count == len(results)
    all_cases_performed_search = bool(results) and search_performed_count == len(results)
    all_cases_recorded_network = bool(results) and network_used_count == len(results)
    all_cases_passed_live_truth = bool(results) and live_truth_count == len(results)
    live_source_categories_covered = (
        patent_snapshot_count > 0 and npl_snapshot_count > 0
    )
    live_completion_gate_passed = mode == "live" and (
        all_samples_completed
        and all_cases_attempted_search
        and all_cases_performed_search
        and all_cases_recorded_network
        and all_cases_passed_live_truth
        and live_source_categories_covered
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": mode,
        "fixture_simulation": mode == "fixture",
        "real_world_search_attempted": any(
            bool(item.get("real_world_search_attempted")) for item in results
        ),
        "real_world_search_attempted_count": search_attempted_count,
        "all_cases_attempted_real_world_search": all_cases_attempted_search,
        "real_world_search_performed": any(
            bool(item.get("real_world_search_performed")) for item in results
        ),
        "real_world_search_performed_count": search_performed_count,
        "all_cases_performed_real_world_search": all_cases_performed_search,
        "network_used": any(bool(item.get("network_used")) for item in results),
        "network_used_count": network_used_count,
        "all_cases_recorded_network_use": all_cases_recorded_network,
        "live_truth_gate_passed_count": live_truth_count,
        "all_cases_passed_live_truth_gate": all_cases_passed_live_truth,
        "patent_source_snapshot_count": patent_snapshot_count,
        "npl_source_snapshot_count": npl_snapshot_count,
        "multimodal_i4s_count": multimodal_i4s_count,
        "live_source_categories_covered": live_source_categories_covered,
        "fixture_fallback_used": False,
        "legal_evidence_allowed": False,
        "report_is_legal_opinion": False,
        "disclaimer": FIXTURE_DISCLAIMER if mode == "fixture" else LIVE_DISCLAIMER,
        "sample_root": str(sample_root.resolve()),
        "sample_count": len(results),
        "passed": passed,
        "failed": failed,
        "all_samples_completed": all_samples_completed,
        "live_completion_gate_passed": live_completion_gate_passed,
        "investigation_status_counts": dict(status_counts),
        "claim_status_counts": dict(claim_status_counts),
        "failed_samples": [
            {
                "file_name": (item.get("sample") or {}).get("file_name"),
                "errors": item.get("errors") or [],
            }
            for item in results
            if item.get("status") != "passed"
        ],
        "generated_at": utcnow().isoformat(),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--sample-root", type=Path, default=DEFAULT_SAMPLE_ROOT)
    parser.add_argument("--oracle", type=Path, default=DEFAULT_ORACLE)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--max-case-attempts", type=int, default=1)
    parser.add_argument("--api-base-url", default="http://127.0.0.1:5209")
    parser.add_argument("--api-token-env", default="INVALIDITY_TEST_API_TOKEN")
    parser.add_argument(
        "--upload-root",
        type=Path,
        help=(
            "live 模式 staging 根目录；缺省取 INVALIDITY_ALLOWED_SOURCE_ROOTS "
            "中的第一个目录"
        ),
    )
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--case-timeout", type=float, default=3600.0)
    parser.add_argument("--fail-on-error", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_case_attempts < 1:
        raise SystemExit("--max-case-attempts must be >= 1")
    run_id = f"{args.mode}-{utcnow().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = discover_cases(args.sample_root, args.oracle)
    selected = set(args.only)
    if selected:
        cases = [
            item
            for item in cases
            if item["file_name"] in selected or item["relative_path"] in selected
        ]
        matched = {
            value
            for item in cases
            for value in (item["file_name"], item["relative_path"])
            if value in selected
        }
        unknown = selected.difference(matched)
        if unknown:
            raise SystemExit(f"unknown --only sample(s): {sorted(unknown)}")
    results: list[dict[str, Any]] = []
    token = os.environ.get(args.api_token_env) if args.api_token_env else None
    upload_root: Path | None = None
    if args.mode == "live":
        try:
            validate_live_api_target(args.api_base_url, args.api_token_env)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if not token or len(token.strip()) < 16:
            raise SystemExit(
                f"live 模式必须从 {args.api_token_env} 读取至少 16 字符的测试 API token"
            )
        upload_root = resolve_live_upload_root(args.upload_root)
    for index, case in enumerate(cases, start=1):
        attempt_history: list[dict[str, Any]] = []
        final: dict[str, Any] | None = None
        for attempt in range(1, args.max_case_attempts + 1):
            if args.mode == "fixture":
                current = run_fixture_case(
                    case,
                    run_id=run_id,
                    output_dir=output_dir,
                    attempt=attempt,
                )
            else:
                assert token is not None and upload_root is not None
                current = run_live_case(
                    case,
                    run_id=run_id,
                    output_dir=output_dir,
                    base_url=args.api_base_url,
                    token=token,
                    upload_root=upload_root,
                    poll_interval=args.poll_interval,
                    timeout_seconds=args.case_timeout,
                    attempt=attempt,
                )
            attempt_history.append(
                {
                    "attempt": attempt,
                    "status": current["status"],
                    "errors": current.get("errors") or [],
                    "elapsed_seconds": current.get("elapsed_seconds"),
                }
            )
            final = current
            if current["status"] == "passed":
                break
        assert final is not None
        final["attempts"] = len(attempt_history)
        final["retries"] = max(0, len(attempt_history) - 1)
        final["retry_history"] = attempt_history
        final["sample_index"] = index
        results.append(final)
        atomic_write_jsonl(output_dir / "results.jsonl", results)
        summary = build_summary(
            run_id=run_id,
            mode=args.mode,
            sample_root=args.sample_root,
            results=results,
        )
        atomic_write_json(output_dir / "summary.json", summary)
        print(
            json.dumps(
                {
                    "sample_index": index,
                    "sample_count": len(cases),
                    "file_name": case["file_name"],
                    "status": final["status"],
                    "attempts": len(attempt_history),
                    "errors": final.get("errors") or [],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    summary = build_summary(
        run_id=run_id,
        mode=args.mode,
        sample_root=args.sample_root,
        results=results,
    )
    summary["results_jsonl"] = str(output_dir / "results.jsonl")
    summary["summary_json"] = str(output_dir / "summary.json")
    atomic_write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if not args.fail_on_error:
        return 0
    if summary["failed"]:
        return 1
    if args.mode == "live" and not summary["live_completion_gate_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
