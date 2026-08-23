from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import ConfigurationError, Settings
from .analysis import I2_PROMPT_VERSION, I2_RULE_VERSION
from .artifacts import ArtifactStore
from .contracts import (
    CONTRACT_VERSION,
    CreateInvestigationRequest,
    I4I_PROMPT_VERSION,
    I4I_RULE_VERSION,
    I4S_PROMPT_VERSION,
    I4S_RULE_VERSION,
    ModuleRunRequest,
)
from .db import Repository, RepositoryConflictError
from .human_review import (
    DocumentDateConfirmationRequest,
    EvidenceFreezer,
    EvidenceImportRequest,
)
from .report import REPORT_CONTRACT_VERSION, ReportDataV1, build_report_data, public_json
from .source_snapshot import (
    SourceFreezer,
    SourceSnapshotError,
    ensure_patent_snapshot,
    stable_investigation_id,
)
from .worker import DurableWorker, JobContext, JobHandler, PermanentJobError


logger = logging.getLogger(__name__)
PIPELINE_VERSION = "invalidity-mvp-v1.1"
ORCHESTRATOR_MODULE_CODE = "I0_ORCHESTRATE"
_TERMINAL_INVESTIGATION_STATUSES = {
    "completed",
    "partial",
    "failed",
    "cancelled",
    "needs_human_review",
}
_REVIEW_ACTOR_PATTERN = re.compile(r"^(?:user|admin):[A-Za-z0-9._@-]{1,120}$")


class CriticalDateConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["v1"] = CONTRACT_VERSION
    claim_investigation_id: str = Field(min_length=1, max_length=100)
    decision: Literal[
        "confirm_priority", "use_filing_date", "set_manual_date"
    ]
    confirmed_date: date
    target_publication_date: date | None = None
    basis: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2_000)
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=200)


class ContinuationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["v1"] = CONTRACT_VERSION
    claim_investigation_ids: list[str] | None = None
    max_additional_rounds: int = Field(ge=1, le=3)
    reason: str = Field(min_length=1, max_length=2_000)
    expected_state_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=200)


class ModuleRunCancellationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["v1"] = CONTRACT_VERSION
    reason: str = Field(min_length=1, max_length=2_000)


class ModuleRunRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["v1"] = CONTRACT_VERSION
    reason: str = Field(min_length=1, max_length=2_000)
    idempotency_key: str = Field(min_length=8, max_length=200)


@dataclass(slots=True)
class ServiceRuntime:
    settings: Settings
    repository: Any
    worker: DurableWorker
    workers: tuple[DurableWorker, ...] = ()
    worker_stop: threading.Event | None = None
    worker_thread: threading.Thread | None = None
    worker_threads: tuple[threading.Thread, ...] = ()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_payload(request: CreateInvestigationRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    idempotency_key = str(payload.pop("idempotency_key"))
    return {
        "contract_version": CONTRACT_VERSION,
        "request": payload,
        "idempotency_key_sha256": _hash_text(idempotency_key),
        "request_sha256": _hash_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        ),
        "snapshot_state": "source_request_frozen",
    }


def _module_request_payload(request: ModuleRunRequest) -> dict[str, Any]:
    payload = request.model_dump(mode="json")
    idempotency_key = str(payload.pop("idempotency_key"))
    analysis_binding = (
        {
            "prompt_version": I4S_PROMPT_VERSION,
            "rule_version": I4S_RULE_VERSION,
        }
        if request.module_code == "I4_S_SINGLE_REFERENCE"
        else {}
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "request": payload,
        **analysis_binding,
        "idempotency_key_sha256": _hash_text(idempotency_key),
        "request_sha256": _hash_text(
            json.dumps(
                {"request": payload, **analysis_binding},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ),
    }


def _module_lab_max_attempts(request: ModuleRunRequest) -> int:
    if request.input_mode == "fixture":
        return 1
    if (
        request.input_mode != "fixture"
        and request.module_code
        in {
            "I2_INVENTIVE_PROFILE",
            "I2_QUERY_PLAN",
            "I4_S_SINGLE_REFERENCE",
            "I4_O_OBVIOUSNESS_PRECHECK",
            "I4_I_INVENTIVE_STEP",
        }
    ):
        # I2 already performs one bounded semantic regeneration inside the
        # engine when deterministic validation fails.  Replaying a transport
        # timeout at the durable job layer previously turned one bad GLM route
        # into three multi-minute waits.  This applies equally to lawyer-
        # supplied/manual and persisted/live inputs because both call the same
        # GLM transport.  Module five similarly owns bounded batch-level retries.
        return 1
    provider = str(request.input.get("search_provider") or "").strip().lower()
    if (
        request.input_mode == "live"
        and request.module_code == "I3_PATENT_SEARCH"
        and provider == "patsnap"
    ):
        return 1
    return 3


def _command_hashes(request: BaseModel) -> tuple[str, str]:
    payload = request.model_dump(mode="json")
    idempotency_key = str(payload.pop("idempotency_key"))
    request_json = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _hash_text(idempotency_key), _hash_text(request_json)


def _authorized(request: Request, expected_token: str) -> bool:
    authorization = request.headers.get("authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied:
        return False
    return secrets.compare_digest(supplied, expected_token)


def _review_actor(request: Request) -> str:
    """Resolve an authenticated actor from server-trusted request metadata."""

    actor = str(request.headers.get("x-invalidity-actor") or "").strip()
    if not _REVIEW_ACTOR_PATTERN.fullmatch(actor):
        raise HTTPException(
            status_code=400,
            detail="X-Invalidity-Actor 必须是 user:<id> 或 admin:<id>",
        )
    return actor


def _matches_idempotent_request(
    stored: Mapping[str, Any], incoming: Mapping[str, Any]
) -> bool:
    return (
        stored.get("idempotency_key_sha256")
        == incoming.get("idempotency_key_sha256")
        and stored.get("request_sha256") == incoming.get("request_sha256")
    )


def _runtime(request: Request) -> ServiceRuntime:
    runtime = getattr(request.app.state, "invalidity_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="服务尚未完成严格配置初始化")
    return runtime


def _require_investigation(repository: Any, investigation_id: str) -> Mapping[str, Any]:
    try:
        investigation = repository.get_investigation(investigation_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="investigation_id 格式无效") from exc
    if investigation is None:
        raise HTTPException(status_code=404, detail="调查不存在")
    return investigation


def _claim_scope_map(investigation: Mapping[str, Any]) -> dict[str, str]:
    source = investigation.get("source_snapshot")
    patent = source.get("patent_snapshot") if isinstance(source, Mapping) else None
    claims = patent.get("claims") if isinstance(patent, Mapping) else None
    if not isinstance(claims, list):
        return {}
    return {
        str(claim.get("claim_id")): str(claim.get("claim_type") or "UNKNOWN")
        for claim in claims
        if isinstance(claim, Mapping) and claim.get("claim_id") is not None
    }


def _enrich_claim_scope(
    investigation: Mapping[str, Any], claims: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scope = _claim_scope_map(investigation)
    enriched: list[dict[str, Any]] = []
    for raw in claims if isinstance(claims, list) else []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        claim_type = scope.get(str(row.get("claim_id")), "UNKNOWN")
        row["claim_type"] = claim_type
        row["in_scope"] = claim_type == "INDEPENDENT"
        row["scope_reason"] = (
            "当前自动无效检索仅处理独立权利要求"
            if row["in_scope"]
            else "历史从属权利要求记录仅供技术审计，不计入当前结果和人工确认数量"
        )
        enriched.append(row)
    included = [claim_id for claim_id, kind in scope.items() if kind == "INDEPENDENT"]
    excluded = [claim_id for claim_id, kind in scope.items() if kind == "DEPENDENT"]
    return enriched, {
        "mode": "independent_only",
        "description": "当前自动检索、稳定性分析和普通报告仅处理独立权利要求。",
        "included_claim_ids": included,
        "excluded_dependent_claim_ids": excluded,
        "included_count": len(included),
        "excluded_dependent_count": len(excluded),
    }


def _find_module_run(
    repository: Any,
    *,
    investigation_id: Any,
    module_code: str,
    idempotency_key: str,
) -> Mapping[str, Any] | None:
    runs = repository.list_module_runs(
        investigation_id=investigation_id,
        module_code=module_code,
        limit=1_000,
    )
    return next(
        (row for row in runs if row.get("idempotency_key") == idempotency_key),
        None,
    )


def _module_run_request(row: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = row.get("input_snapshot")
    request = snapshot.get("request") if isinstance(snapshot, Mapping) else None
    return request if isinstance(request, Mapping) else {}


def _module_run_output(row: Mapping[str, Any]) -> Mapping[str, Any]:
    snapshot = row.get("output_snapshot")
    if not isinstance(snapshot, Mapping):
        return {}
    output = snapshot.get("output")
    return output if isinstance(output, Mapping) else snapshot


def _module_run_matches_claim(
    row: Mapping[str, Any],
    claim_investigation_id: str,
) -> bool:
    direct = str(row.get("claim_investigation_id") or "").strip()
    if direct:
        return direct == claim_investigation_id
    return (
        str(_module_run_request(row).get("claim_investigation_id") or "").strip()
        == claim_investigation_id
    )


def _recover_module9_cursor(
    repository: Any,
    *,
    investigation_id: str,
    claim_investigation_id: str,
) -> dict[str, Any]:
    """Recover the latest successful legacy I2-G cursor for durable Module 9.

    New Module-9 batches persist their own round state.  This compatibility
    cursor is only used when an investigation already completed a browser-led
    gap round before that batch table existed.
    """

    runs = repository.list_module_runs(
        investigation_id=investigation_id,
        module_code="I2_GAP_QUERY_PLAN",
        limit=1_000,
    )
    eligible = [
        row
        for row in runs
        if _module_run_matches_claim(row, claim_investigation_id)
        and str(row.get("status") or "") in {"succeeded", "completed"}
    ]
    if not eligible:
        return {
            "latest_iteration": 0,
            "next_iteration": 1,
            "active_gap_feature_ids": [],
            "previous_query_expressions": [],
            "previous_iteration_failure_reason": "",
            "source_module_run_id": None,
        }
    latest = max(
        eligible,
        key=lambda row: (
            _module_run_created_at(row)
            or datetime.min.replace(tzinfo=timezone.utc),
            str(row.get("id") or ""),
        ),
    )
    output = _module_run_output(latest)
    decision = output.get("gap_reuse_decision")
    decision = decision if isinstance(decision, Mapping) else {}
    iteration = int(
        decision.get("gap_search_iteration")
        or output.get("gap_search_iteration")
        or 0
    )
    iteration = max(0, min(5, iteration))
    expressions = [
        str(query.get("expression") or "").strip()
        for query in output.get("queries") or []
        if isinstance(query, Mapping) and str(query.get("expression") or "").strip()
    ]
    active_gap_feature_ids = [
        str(item).strip()
        for item in (
            output.get("allowed_gap_feature_ids")
            or output.get("uncovered_difference_feature_ids")
            or []
        )
        if str(item).strip()
    ]
    return {
        "latest_iteration": iteration,
        "next_iteration": min(5, max(1, iteration + 1)),
        "active_gap_feature_ids": list(dict.fromkeys(active_gap_feature_ids)),
        "previous_query_expressions": list(dict.fromkeys(expressions)),
        "previous_iteration_failure_reason": str(
            decision.get("previous_iteration_failure_reason")
            or output.get("previous_iteration_failure_reason")
            or (
                f"已恢复历史第 {iteration} 个 gap 轮；该轮检索及逐篇比对已持久化，"
                "剩余未覆盖区别特征必须改词继续"
                if iteration
                else ""
            )
        ).strip(),
        "source_module_run_id": str(latest.get("id") or "") or None,
    }


def _module_run_created_at(row: Mapping[str, Any]) -> datetime | None:
    value = row.get("created_at")
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _latest_contiguous_module_runs(
    rows: list[Mapping[str, Any]],
    *,
    maximum_gap: timedelta = timedelta(minutes=15),
) -> list[Mapping[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: _module_run_created_at(row) or datetime.min.replace(
            tzinfo=timezone.utc
        ),
        reverse=True,
    )
    if not ordered:
        return []
    cluster = [ordered[0]]
    previous = _module_run_created_at(ordered[0])
    for row in ordered[1:]:
        current = _module_run_created_at(row)
        if previous is None or current is None or previous - current > maximum_gap:
            break
        cluster.append(row)
        previous = current
    return cluster


def _module_run_input(row: Mapping[str, Any]) -> Mapping[str, Any]:
    data = _module_run_request(row).get("input")
    return data if isinstance(data, Mapping) else {}


def _analysis_ready_fetch_output(output: Mapping[str, Any]) -> bool:
    readable_document = output.get("readable_document")
    return (
        str(output.get("stage") or "")
        in {"retrieved_document", "qualified_evidence"}
        and bool(str(output.get("content_sha256") or "").strip())
        and output.get("analysis_ready") is True
        and isinstance(readable_document, Mapping)
        and bool(str(readable_document.get("full_text") or "").strip())
    )


def _latest_fetch_lineage(
    rows: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Return one exact I3 fetch batch when persisted lineage is available."""
    ordered = sorted(
        rows,
        key=lambda row: _module_run_created_at(row) or datetime.min.replace(
            tzinfo=timezone.utc
        ),
        reverse=True,
    )
    if not ordered:
        return []
    candidate_filter_run_id = str(
        _module_run_input(ordered[0]).get("candidate_filter_run_id") or ""
    ).strip()
    if not candidate_filter_run_id:
        return _latest_contiguous_module_runs(ordered)
    return [
        row
        for row in ordered
        if str(
            _module_run_input(row).get("candidate_filter_run_id") or ""
        ).strip()
        == candidate_filter_run_id
    ]


def _recover_module5_inputs(
    repository: Any,
    *,
    investigation_id: Any,
    claim_investigation_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    fetch_rows = repository.list_module_runs(
        investigation_id=investigation_id,
        module_code="I3_FETCH",
        limit=1_000,
    )
    eligible_fetch_rows: list[Mapping[str, Any]] = []
    for row in fetch_rows:
        if (
            not isinstance(row, Mapping)
            or str(row.get("status") or "") != "succeeded"
            or str(row.get("effective_mode") or "live") != "live"
            or not _module_run_matches_claim(row, claim_investigation_id)
        ):
            continue
        output = _module_run_output(row)
        if not _analysis_ready_fetch_output(output):
            continue
        document_id = str(
            output.get("external_id")
            or output.get("publication_number")
            or ""
        ).strip()
        if not document_id:
            continue
        eligible_fetch_rows.append(row)

    latest_fetch_rows = _latest_fetch_lineage(eligible_fetch_rows)
    retrieved_by_id: dict[str, dict[str, Any]] = {}
    recovered_ids: list[str] = []
    for row in reversed(latest_fetch_rows):
        output = _module_run_output(row)
        document_id = str(
            output.get("external_id")
            or output.get("publication_number")
            or ""
        ).strip()
        if not document_id or document_id in retrieved_by_id:
            continue
        retrieved_by_id[document_id] = {
            "document_id": document_id,
            "publication_number": output.get("publication_number"),
            "title": output.get("title"),
            "content_sha256": output.get("content_sha256"),
            "fetch_module_run_id": str(row.get("id") or ""),
        }
        recovered_ids.append(document_id)
        if len(recovered_ids) >= limit:
            break

    comparison_rows = [
        row
        for row in repository.list_module_runs(
            investigation_id=investigation_id,
            module_code="I4_S_SINGLE_REFERENCE",
            limit=1_000,
        )
        if isinstance(row, Mapping)
        and str(row.get("effective_mode") or "live") == "live"
        and _module_run_matches_claim(row, claim_investigation_id)
    ]
    latest_comparison_by_document: dict[str, dict[str, Any]] = {}
    for row in sorted(
        comparison_rows,
        key=lambda item: _module_run_created_at(item)
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    ):
        document_id = str(
            _module_run_input(row).get("document_id") or ""
        ).strip()
        if (
            document_id
            and document_id in retrieved_by_id
            and document_id not in latest_comparison_by_document
        ):
            latest_comparison_by_document[document_id] = {
                "document_id": document_id,
                "module_run_id": str(row.get("id") or ""),
                "status": str(row.get("status") or ""),
                "created_at": row.get("created_at"),
            }

    return {
        "document_ids": recovered_ids,
        "documents": [
            retrieved_by_id[document_id]
            for document_id in recovered_ids
            if document_id in retrieved_by_id
        ],
        "comparison_runs": [
            latest_comparison_by_document[document_id]
            for document_id in recovered_ids
            if document_id in latest_comparison_by_document
        ],
        "source": "latest_retrieved_document_lineage",
    }


def _canonical_json_sha256(value: Mapping[str, Any]) -> str:
    return _hash_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def _ensure_run_and_job(
    repository: Any,
    *,
    investigation: Mapping[str, Any],
    module_code: str,
    input_snapshot: Mapping[str, Any],
    idempotency_key: str,
    job_payload: Mapping[str, Any],
    event_type: str,
    max_attempts: int,
    claim_investigation_id: Any = None,
    iteration_id: Any = None,
    requested_mode: str | None = None,
    effective_mode: str | None = None,
    prompt_version: str | None = None,
    rule_version: str | None = None,
    before_enqueue: Callable[[Mapping[str, Any]], None] | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any], bool]:
    run = _find_module_run(
        repository,
        investigation_id=investigation["id"],
        module_code=module_code,
        idempotency_key=idempotency_key,
    )
    created = False
    if run is not None and _canonical_json_sha256(
        run.get("input_snapshot") or {}
    ) != _canonical_json_sha256(input_snapshot):
        raise RepositoryConflictError(
            "同一 module run 幂等键对应了不同输入快照"
        )
    if run is None:
        try:
            run = repository.create_module_run(
                investigation_id=investigation["id"],
                module_code=module_code,
                contract_version=CONTRACT_VERSION,
                input_snapshot=input_snapshot,
                idempotency_key=idempotency_key,
                claim_investigation_id=claim_investigation_id,
                iteration_id=iteration_id,
                status="created",
                requested_mode=requested_mode,
                effective_mode=effective_mode,
                prompt_version=prompt_version,
                rule_version=rule_version,
            )
            created = True
        except Exception:
            # A concurrent/replayed request may have won the unique key race.
            run = _find_module_run(
                repository,
                investigation_id=investigation["id"],
                module_code=module_code,
                idempotency_key=idempotency_key,
            )
            if run is None:
                raise
            if _canonical_json_sha256(
                run.get("input_snapshot") or {}
            ) != _canonical_json_sha256(input_snapshot):
                raise RepositoryConflictError(
                    "同一 module run 幂等键对应了不同输入快照"
                )

    details = repository.get_module_lab_run(run["id"])
    if details is None:
        raise RuntimeError("刚创建的 module run 无法读取")
    job = details.get("job")
    if job is None:
        if before_enqueue is not None:
            before_enqueue(run)
        try:
            job = repository.record_event_and_enqueue_job(
                investigation_id=investigation["id"],
                module_run_id=run["id"],
                event_type=event_type,
                actor="api",
                event_payload={"module_code": module_code},
                job_payload=job_payload,
                claim_investigation_id=claim_investigation_id,
                iteration_id=iteration_id,
                max_attempts=max_attempts,
            )
        except Exception:
            # Recover a crash/race after enqueue but before the caller received it.
            details = repository.get_module_lab_run(run["id"])
            job = details.get("job") if details is not None else None
            if job is None:
                raise
    return run, job, created


def _workflow_handlers(settings: Settings, repository: Any) -> dict[str, JobHandler]:
    """Load workflow adapters lazily so API/worker tests need no providers."""

    try:
        from .lab import build_module_lab_handlers
        from .workflow import build_workflow  # type: ignore[attr-defined]
    except (ImportError, AttributeError) as exc:
        raise ConfigurationError(
            "I0 workflow handler 不可用，测试服务拒绝以空处理器启动"
        ) from exc

    workflow = build_workflow(settings=settings, repository=repository)

    def execute(context: JobContext) -> Mapping[str, Any]:
        payload = context.job.get("payload")
        investigation_id = str(
            payload.get("investigation_id") if isinstance(payload, Mapping) else ""
        )
        if not investigation_id:
            raise PermanentJobError(
                "WORKFLOW_INPUT_ERROR", "I0 job 缺少 investigation_id"
            )
        investigation = repository.get_investigation(investigation_id)
        source_snapshot = (
            investigation.get("source_snapshot")
            if isinstance(investigation, Mapping)
            else None
        )
        source_request = (
            source_snapshot.get("request")
            if isinstance(source_snapshot, Mapping)
            else None
        )
        provider_mode = (
            str(source_request.get("provider_mode") or "")
            if isinstance(source_request, Mapping)
            else ""
        )
        if provider_mode != "live":
            raise PermanentJobError(
                "PROVIDER_MODE_UNSUPPORTED",
                "完整 I0 调查只允许 live provider 模式",
            )
        context.heartbeat()
        ensure_patent_snapshot(
            settings=settings,
            repository=repository,
            investigation_id=investigation_id,
        )
        context.heartbeat()
        result = workflow.execute_job(
            context.job,
            context.module_run,
            heartbeat=context.heartbeat,
        )
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")
        if not isinstance(result, Mapping):
            raise TypeError("workflow.execute_job 必须返回对象")
        return result

    handlers: dict[str, JobHandler] = {
        ORCHESTRATOR_MODULE_CODE: execute,
        "I0_INVALIDITY_WORKFLOW": execute,
    }

    def execute_human_review(context: JobContext) -> Mapping[str, Any]:
        try:
            result = workflow.execute_human_review_action(
                context.job,
                context.module_run,
                heartbeat=context.heartbeat,
            )
        except ValueError as exc:
            raise PermanentJobError(
                "HUMAN_REVIEW_INPUT_ERROR", str(exc)
            ) from exc
        if not isinstance(result, Mapping):
            raise TypeError("HUMAN_REVIEW_APPLY 必须返回对象")
        return result

    handlers["HUMAN_REVIEW_APPLY"] = execute_human_review
    lab_handlers = build_module_lab_handlers(
        settings=settings, repository=repository
    )
    lab_report_handler = lab_handlers.pop("I5_REPORT")
    handlers.update(lab_handlers)

    def execute_report(context: JobContext) -> Mapping[str, Any]:
        payload = context.job.get("payload")
        if not isinstance(payload, Mapping) or payload.get("kind") != "report_snapshot":
            return lab_report_handler(context)
        investigation_id = str(payload.get("investigation_id") or "")
        report_snapshot_id = str(payload.get("report_snapshot_id") or "")
        try:
            source_state_version = int(payload.get("source_state_version"))
            source_review_revision = int(
                payload.get(
                    "source_review_revision",
                    (repository.get_investigation(investigation_id) or {}).get(
                        "review_revision", 0
                    ),
                )
            )
        except (TypeError, ValueError) as exc:
            raise PermanentJobError(
                "REPORT_INPUT_INVALID", "I5 job 缺少有效 source_state_version"
            ) from exc
        if not investigation_id or not report_snapshot_id:
            raise PermanentJobError(
                "REPORT_INPUT_INVALID", "I5 job 缺少调查或快照 ID"
            )
        investigation = repository.get_investigation(investigation_id)
        if not isinstance(investigation, Mapping):
            raise PermanentJobError("REPORT_NOT_FOUND", "I5 调查不存在")
        if str(investigation.get("status")) not in _TERMINAL_INVESTIGATION_STATUSES:
            raise PermanentJobError(
                "REPORT_NOT_TERMINAL", "调查尚未终态，禁止固化最终报告"
            )
        existing = repository.get_report_snapshot(
            investigation_id, report_snapshot_id
        )
        if isinstance(existing, Mapping):
            if (
                str(existing.get("contract_version")) != REPORT_CONTRACT_VERSION
                or int(existing.get("source_state_version", -1))
                != source_state_version
                or int(
                    existing.get("source_review_revision", source_review_revision)
                )
                != source_review_revision
            ):
                raise PermanentJobError(
                    "REPORT_SNAPSHOT_CONFLICT",
                    "I5 快照 ID 已对应其他契约或状态版本",
                )
            return {
                "status": "completed",
                "module_code": "I5_REPORT",
                "report_snapshot_id": str(existing["id"]),
                "report_sha256": str(existing["report_sha256"]),
                "source_state_version": int(existing["source_state_version"]),
                "source_review_revision": int(
                    existing.get("source_review_revision", source_review_revision)
                ),
            }
        context.heartbeat()
        raw = repository.get_report_data(investigation_id)
        if not isinstance(raw, Mapping):
            raise PermanentJobError("REPORT_NOT_FOUND", "I5 报告源数据不存在")
        report = build_report_data(
            raw,
            preview=False,
            report_snapshot_id=report_snapshot_id,
            snapshot_sha256=None,
            generated_at=(
                context.module_run["created_at"]
                if isinstance(context.module_run.get("created_at"), datetime)
                else datetime.now(timezone.utc)
            ),
        )
        ReportDataV1.model_validate(report)
        stored = repository.create_report_snapshot(
            investigation_id=investigation_id,
            contract_version=REPORT_CONTRACT_VERSION,
            source_state_version=source_state_version,
            source_review_revision=source_review_revision,
            module_run_id=context.module_run["id"],
            report_snapshot_id=report_snapshot_id,
            report_snapshot=report,
        )
        context.heartbeat()
        return {
            "status": "completed",
            "module_code": "I5_REPORT",
            "report_snapshot_id": str(stored["id"]),
            "report_sha256": str(stored["report_sha256"]),
            "source_state_version": int(stored["source_state_version"]),
            "source_review_revision": int(
                stored.get("source_review_revision", source_review_revision)
            ),
        }

    handlers["I5_REPORT"] = execute_report
    return handlers


def create_app(
    *,
    settings: Settings | None = None,
    repository: Any | None = None,
    handler_registry: Mapping[str, JobHandler] | None = None,
    worker: DurableWorker | None = None,
    start_worker: bool = True,
) -> FastAPI:
    supplied_settings = settings
    supplied_repository = repository
    supplied_handlers = handler_registry
    supplied_worker = worker

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # The promoted release injects one complete environment.  The same
        # source starts only when every env/port/schema/artifact mapping agrees.
        actual_settings = supplied_settings or Settings.from_environment(
            load_dotenv_files=False
        )
        actual_repository = supplied_repository or Repository(
            actual_settings.database_url,
            actual_settings.database_schema,
            actual_settings.environment,
        )
        repository_environment = getattr(actual_repository, "environment", None)
        repository_schema = getattr(
            actual_repository, "schema", actual_settings.database_schema
        )
        if (
            repository_environment != actual_settings.environment
            or repository_schema != actual_settings.database_schema
        ):
            raise ConfigurationError(
                "API 环境、端口配置与持久层 environment/schema 不一致"
            )
        actual_repository.init_schema()

        handlers = (
            dict(supplied_handlers)
            if supplied_handlers is not None
            else _workflow_handlers(actual_settings, actual_repository)
        )
        actual_workers = (
            [supplied_worker]
            if supplied_worker is not None
            else [
                DurableWorker(
                    settings=actual_settings,
                    repository=actual_repository,
                    handlers=handlers,
                )
                for _ in range(actual_settings.worker_concurrency)
            ]
        )
        actual_worker = actual_workers[0]
        runtime = ServiceRuntime(
            actual_settings,
            actual_repository,
            actual_worker,
            workers=tuple(actual_workers),
        )
        if start_worker:
            runtime.worker_stop = threading.Event()
            runtime.worker_threads = tuple(
                threading.Thread(
                    target=current_worker.serve,
                    args=(runtime.worker_stop,),
                    name=f"invalidity-durable-worker-{index + 1}",
                    daemon=True,
                )
                for index, current_worker in enumerate(actual_workers)
            )
            runtime.worker_thread = runtime.worker_threads[0]
            for current_thread in runtime.worker_threads:
                current_thread.start()
        application.state.invalidity_runtime = runtime
        try:
            yield
        finally:
            if runtime.worker_stop is not None:
                runtime.worker_stop.set()
            # Give idle/polling workers a brief chance to exit.  Every worker
            # still inside a handler then atomically requeues only its own
            # lease, so a replacement process can resume without late writes.
            for current_thread in runtime.worker_threads:
                current_thread.join(timeout=0.25)
            for current_worker, current_thread in zip(
                runtime.workers,
                runtime.worker_threads,
            ):
                if current_thread.is_alive():
                    current_worker.release_active_lease_for_shutdown()
            for current_thread in runtime.worker_threads:
                if current_thread.is_alive():
                    current_thread.join(timeout=5.0)
            worker_still_running = any(
                current_thread.is_alive()
                for current_thread in runtime.worker_threads
            )
            if worker_still_running:
                logger.warning(
                    "one or more worker handlers are still exiting after "
                    "shutdown lease release; repository remains open until "
                    "process exit"
                )
            if supplied_repository is None and not worker_still_running:
                actual_repository.close()
            application.state.invalidity_runtime = None

    application = FastAPI(
        title="Patent Invalidity Search",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def bearer_authentication(request: Request, call_next: Any):
        if request.url.path == "/health":
            return await call_next(request)
        runtime = getattr(request.app.state, "invalidity_runtime", None)
        expected_token = runtime.settings.api_token if runtime is not None else ""
        if not expected_token or not _authorized(request, expected_token):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "AUTHENTICATION_REQUIRED",
                    "message": "需要有效的 Bearer token",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {
                "location": list(error.get("loc", ())),
                "message": str(error.get("msg", "输入不符合契约")),
                "type": str(error.get("type", "validation_error")),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"error": "请求参数不符合契约", "code": "VALIDATION_ERROR", "details": errors},
        )

    @application.exception_handler(RepositoryConflictError)
    async def repository_conflict_handler(
        _request: Request, exc: RepositoryConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": exc.public_message, "code": exc.code},
        )

    @application.exception_handler(SourceSnapshotError)
    async def source_snapshot_error_handler(
        _request: Request, exc: SourceSnapshotError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.public_message, "code": exc.code},
        )

    @application.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("public API failed (%s)", exc.__class__.__name__)
        return JSONResponse(
            status_code=500,
            content={"error": "服务处理失败；未返回敏感诊断信息", "code": "INTERNAL_ERROR"},
        )

    @application.get("/health")
    def health(request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        return {
            "status": "ok",
            "service": "invalidity-search",
            "environment": runtime.settings.environment,
            "contract_version": CONTRACT_VERSION,
            "worker_concurrency": len(runtime.workers),
            "i2_prompt_version": I2_PROMPT_VERSION,
            "i2_rule_version": I2_RULE_VERSION,
            "i4s_prompt_version": I4S_PROMPT_VERSION,
            "i4s_rule_version": I4S_RULE_VERSION,
            "i4i_prompt_version": I4I_PROMPT_VERSION,
            "i4i_rule_version": I4I_RULE_VERSION,
        }

    @application.post("/v1/investigations")
    def create_investigation(
        body: CreateInvestigationRequest, request: Request
    ) -> JSONResponse:
        runtime = _runtime(request)
        if body.provider_mode != "live":
            raise HTTPException(
                status_code=422,
                detail=(
                    "完整 I0 调查当前只支持 live；fixture/manual 请使用单模块实验室，"
                    "禁止以模拟标签调用真实 provider"
                ),
            )
        snapshot = _request_payload(body)
        existing = runtime.repository.get_investigation_by_session(
            body.analysis_session_id
        )
        if existing is not None:
            if not _matches_idempotent_request(existing["source_snapshot"], snapshot):
                raise HTTPException(
                    status_code=409,
                    detail="analysis_session_id 已由不同输入或幂等键占用",
                )
            return JSONResponse(
                status_code=200,
                content=public_json(
                    {
                        "contract_version": CONTRACT_VERSION,
                        "investigation_id": existing["id"],
                        "status": existing["status"],
                        "environment": runtime.settings.environment,
                        "idempotent_replay": True,
                    }
                ),
            )
        investigation_id = stable_investigation_id(
            environment=runtime.settings.environment,
            analysis_session_id=body.analysis_session_id,
            idempotency_key_sha256=str(snapshot["idempotency_key_sha256"]),
        )
        snapshot = SourceFreezer(runtime.settings).freeze(
            request=body,
            investigation_id=investigation_id,
            source_snapshot=snapshot,
        )
        try:
            created = runtime.repository.create_investigation(
                investigation_id=investigation_id,
                analysis_session_id=body.analysis_session_id,
                patent_record_id=body.patent_record_id,
                source_snapshot=snapshot,
                pipeline_version=PIPELINE_VERSION,
                settings={
                    "max_rounds": body.max_rounds,
                    "max_gap_search_iterations": body.max_rounds,
                    "provider_mode": body.provider_mode,
                    "patent_provider": runtime.settings.patent_provider,
                    "npl_provider": runtime.settings.npl_provider,
                    "llm_model": runtime.settings.llm_model,
                },
                budgets={
                    "max_rounds": body.max_rounds,
                    "max_gap_search_iterations": body.max_rounds,
                    "max_candidates_per_query": runtime.settings.max_candidates_per_query,
                },
                workflow_state={"stage": "created", "provider_mode": body.provider_mode},
                status="created",
            )
        except Exception:
            existing = runtime.repository.get_investigation_by_session(
                body.analysis_session_id
            )
            if existing is None or not _matches_idempotent_request(
                existing["source_snapshot"], snapshot
            ):
                raise
            created = existing
            replay = True
        else:
            replay = False
        return JSONResponse(
            status_code=200 if replay else 201,
            content=public_json(
                {
                    "contract_version": CONTRACT_VERSION,
                    "investigation_id": created["id"],
                    "status": created["status"],
                    "environment": runtime.settings.environment,
                    "idempotent_replay": replay,
                }
            ),
        )

    @application.post("/v1/investigations/{investigation_id}/start")
    def start_investigation(investigation_id: str, request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        investigation = _require_investigation(runtime.repository, investigation_id)
        stored_source = investigation.get("source_snapshot")
        stored_request = (
            stored_source.get("request")
            if isinstance(stored_source, Mapping)
            else None
        )
        stored_provider_mode = (
            str(stored_request.get("provider_mode") or "")
            if isinstance(stored_request, Mapping)
            else ""
        )
        if stored_provider_mode != "live":
            raise HTTPException(
                status_code=422,
                detail="完整 I0 调查只允许 live，拒绝把 fixture/manual 任务路由到 live provider",
            )
        status = str(investigation["status"])
        if status not in {
            "created",
            "draft",
            "queued",
            "running",
            *_TERMINAL_INVESTIGATION_STATUSES,
        }:
            raise HTTPException(status_code=409, detail=f"状态 {status} 不允许启动")

        start_key = f"investigation:{investigation['id']}:start:v1"

        queued_investigation: Mapping[str, Any] = investigation

        def mark_queued(run_to_enqueue: Mapping[str, Any]) -> None:
            nonlocal queued_investigation
            if status not in {"created", "draft"}:
                return
            queued_investigation = runtime.repository.update_investigation(
                investigation["id"],
                expected_state_version=investigation.get("state_version"),
                status="queued",
                actor="api",
                event_type="investigation.start_accepted",
                event_payload={"module_run_id": str(run_to_enqueue["id"])},
            )

        run, job, created = _ensure_run_and_job(
            runtime.repository,
            investigation=investigation,
            module_code=ORCHESTRATOR_MODULE_CODE,
            input_snapshot={
                "investigation_id": str(investigation["id"]),
                "source_snapshot_sha256": investigation["source_snapshot_sha256"],
                "pipeline_version": investigation["pipeline_version"],
            },
            idempotency_key=start_key,
            job_payload={
                "kind": "investigation",
                "investigation_id": str(investigation["id"]),
                # 不得 force_recompute：durable job 在进程重启后重试时必须从
                # 持久化 checkpoint 续跑，而不是丢弃已有进度重新插入权利要求行。
            },
            event_type="investigation.queued",
            max_attempts=3,
            before_enqueue=mark_queued,
        )
        latest_investigation = runtime.repository.get_investigation(investigation["id"])
        investigation = latest_investigation or queued_investigation
        accepted_status = "queued" if created else investigation["status"]
        return public_json(
            {
                "contract_version": CONTRACT_VERSION,
                "investigation_id": investigation["id"],
                "status": accepted_status,
                "module_run_id": run["id"],
                "job_id": job["id"],
                "job_status": job["status"],
                "idempotent_replay": not created,
            }
        )

    @application.post("/v1/investigations/{investigation_id}/cancel")
    def cancel_investigation(
        investigation_id: str,
        body: ModuleRunCancellationRequest,
        request: Request,
    ) -> JSONResponse:
        runtime = _runtime(request)
        investigation = _require_investigation(runtime.repository, investigation_id)
        status = str(investigation["status"])
        if status in _TERMINAL_INVESTIGATION_STATUSES:
            return JSONResponse(
                status_code=200,
                content=public_json(
                    {
                        "contract_version": CONTRACT_VERSION,
                        "investigation_id": investigation["id"],
                        "status": status,
                        "cancel_requested_run_ids": [],
                        "idempotent_replay": True,
                    }
                ),
            )
        reason = body.reason.strip()
        if not reason:
            raise HTTPException(status_code=400, detail="取消原因不能为空")
        active_statuses = {"created", "queued", "running", "cancellation_requested"}
        active_runs = [
            run
            for run in runtime.repository.list_module_runs(
                investigation_id=investigation["id"], limit=200
            )
            if str(run.get("status")) in active_statuses
        ]
        requested: list[str] = []
        for run in active_runs:
            runtime.repository.request_module_run_cancellation(
                run["id"],
                reason=reason,
                actor="api",
            )
            requested.append(str(run["id"]))
        latest = runtime.repository.get_investigation(investigation["id"])
        if not requested and latest is not None and str(
            latest["status"]
        ) not in _TERMINAL_INVESTIGATION_STATUSES:
            # 没有任何在途 module run（例如已创建但尚未入队）时直接收口，
            # 避免调查停在 running 假象。
            latest = runtime.repository.update_investigation(
                investigation["id"],
                status="cancelled",
                error_message=reason,
                error_metadata={"code": "CANCELLED_BY_USER", "without_active_run": True},
                completed_at=datetime.now(timezone.utc),
                actor="api",
                event_type="investigation.cancelled",
                event_payload={"reason": reason},
            )
        return JSONResponse(
            status_code=202,
            content=public_json(
                {
                    "contract_version": CONTRACT_VERSION,
                    "investigation_id": investigation["id"],
                    "status": str((latest or investigation)["status"]),
                    "cancel_requested_run_ids": requested,
                    "idempotent_replay": False,
                }
            ),
        )

    @application.get("/v1/investigations/{investigation_id}")
    def get_investigation(investigation_id: str, request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        return public_json(
            _require_investigation(runtime.repository, investigation_id)
        )

    @application.get("/v1/investigations/{investigation_id}/claims")
    def get_claims(investigation_id: str, request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        investigation = _require_investigation(runtime.repository, investigation_id)
        claims = runtime.repository.list_claim_investigations(investigation["id"])
        claims, claim_scope = _enrich_claim_scope(investigation, claims)
        return public_json(
            {
                "investigation_id": investigation["id"],
                "claim_scope": claim_scope,
                "claims": claims,
            }
        )

    @application.get("/v1/investigations/{investigation_id}/module-runs")
    def list_investigation_module_runs(
        investigation_id: str, request: Request
    ) -> dict[str, Any]:
        # 管理员同任务只读诊断：返回本调查全部模块运行（含输入/输出快照），
        # 供只读视图渲染各模块真实输出；经 public_json 脱敏，不泄露凭据或本地路径。
        runtime = _runtime(request)
        investigation = _require_investigation(runtime.repository, investigation_id)
        runs = runtime.repository.list_module_runs(
            investigation_id=investigation["id"], limit=1000
        )
        return public_json(
            {"investigation_id": investigation["id"], "module_runs": runs}
        )

    @application.get(
        "/v1/investigations/{investigation_id}/target-figures/{figure_index}"
    )
    def get_investigation_target_figure(
        investigation_id: str,
        figure_index: int,
        request: Request,
    ) -> FileResponse:
        # 调查级目标专利附图：供管理员同任务只读诊断展示模块一附图。
        # Agent 自动流程的模块一不产生独立 I1 module run，附图按调查创建时
        # 冻结的 patent_figure_artifacts 索引寻址，沿用 lab 端点的路径包含、
        # SHA-256 与 MIME 校验，不暴露本地路径。
        runtime = _runtime(request)
        if figure_index < 0 or figure_index > 999:
            raise HTTPException(status_code=404, detail="目标专利附图不存在")
        investigation = _require_investigation(runtime.repository, investigation_id)
        source = investigation.get("source_snapshot")
        artifacts = (
            source.get("patent_figure_artifacts")
            if isinstance(source, Mapping)
            else None
        )
        artifact = (
            artifacts[figure_index]
            if isinstance(artifacts, list) and figure_index < len(artifacts)
            else None
        )
        if not isinstance(artifact, Mapping):
            raise HTTPException(status_code=404, detail="目标专利附图不存在")
        artifact_dir = (
            ArtifactStore(runtime.settings.artifact_root, runtime.settings.environment)
            .investigation_dir(str(investigation["id"]))
            / "source"
            / "patent-figures"
        )
        try:
            path = Path(str(artifact.get("uri") or "")).expanduser().resolve()
        except (OSError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=404, detail="目标专利附图不存在") from exc
        figure_root = artifact_dir.resolve()
        if not path.is_file() or figure_root not in path.parents:
            raise HTTPException(status_code=404, detail="目标专利附图不存在")
        content = path.read_bytes()
        expected_sha256 = str(artifact.get("sha256") or "").lower()
        if not expected_sha256 or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise HTTPException(status_code=409, detail="目标专利附图完整性校验失败")
        media_type = str(artifact.get("mime_type") or "image/png")
        if media_type not in {"image/png", "image/jpeg", "image/tiff"}:
            raise HTTPException(status_code=415, detail="目标专利附图格式不支持")
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.get(
        "/v1/lab/investigations/{investigation_id}/module5-inputs"
    )
    def get_module5_inputs(
        investigation_id: str,
        claim_investigation_id: str,
        request: Request,
    ) -> dict[str, Any]:
        runtime = _runtime(request)
        investigation = _require_investigation(
            runtime.repository, investigation_id
        )
        claims = runtime.repository.list_claim_investigations(
            investigation["id"]
        )
        if claim_investigation_id not in {
            str(claim.get("id") or "")
            for claim in claims
            if isinstance(claim, Mapping)
        }:
            raise HTTPException(
                status_code=404,
                detail="指定独立权利要求不属于当前调查",
            )
        recovered = _recover_module5_inputs(
            runtime.repository,
            investigation_id=investigation["id"],
            claim_investigation_id=claim_investigation_id,
            limit=50,
        )
        if not recovered["document_ids"]:
            raise HTTPException(
                status_code=404,
                detail="当前案件没有可恢复的已取得全文文献，请先运行模块4",
            )
        return public_json(
            {
                "investigation_id": investigation["id"],
                "claim_investigation_id": claim_investigation_id,
                **recovered,
            }
        )

    @application.get(
        "/v1/lab/investigations/{investigation_id}/module9-cursor"
    )
    def get_module9_cursor(
        investigation_id: str,
        claim_investigation_id: str,
        request: Request,
    ) -> dict[str, Any]:
        runtime = _runtime(request)
        investigation = _require_investigation(
            runtime.repository, investigation_id
        )
        claims = runtime.repository.list_claim_investigations(
            investigation["id"]
        )
        if claim_investigation_id not in {
            str(claim.get("id") or "")
            for claim in claims
            if isinstance(claim, Mapping)
        }:
            raise HTTPException(
                status_code=404,
                detail="指定独立权利要求不属于当前调查",
            )
        return public_json(
            {
                "investigation_id": investigation["id"],
                "claim_investigation_id": claim_investigation_id,
                **_recover_module9_cursor(
                    runtime.repository,
                    investigation_id=str(investigation["id"]),
                    claim_investigation_id=claim_investigation_id,
                ),
            }
        )

    @application.get("/v1/investigations/{investigation_id}/events")
    def get_events(investigation_id: str, request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        investigation = _require_investigation(runtime.repository, investigation_id)
        events = runtime.repository.list_events(investigation["id"])
        return public_json(
            {"investigation_id": investigation["id"], "events": events}
        )

    @application.get("/v1/investigations/{investigation_id}/review-context")
    def get_review_context(
        investigation_id: str, request: Request
    ) -> dict[str, Any]:
        runtime = _runtime(request)
        investigation = _require_investigation(
            runtime.repository, investigation_id
        )
        context = runtime.repository.get_review_context(investigation["id"])
        if context is None:
            raise HTTPException(status_code=404, detail="调查不存在")
        enriched_context = dict(context)
        enriched_claims, claim_scope = _enrich_claim_scope(
            investigation, context.get("claims")
        )
        enriched_context["claims"] = enriched_claims
        enriched_context["claim_scope"] = claim_scope
        return public_json(enriched_context)

    @application.post(
        "/v1/investigations/{investigation_id}/human-reviews/evidence-imports"
    )
    def import_review_evidence(
        investigation_id: str,
        body: EvidenceImportRequest,
        request: Request,
    ) -> JSONResponse:
        runtime = _runtime(request)
        investigation = _require_investigation(
            runtime.repository, investigation_id
        )
        actor = _review_actor(request)
        frozen = EvidenceFreezer(runtime.settings).freeze(
            investigation_id=str(investigation["id"]),
            source_path=body.source_path,
            source_url=body.source_url,
            expected_source_sha256=body.expected_source_sha256,
            expected_source_byte_size=body.expected_source_byte_size,
            expected_source_mime_type=body.expected_source_mime_type,
        )
        idempotency_key_sha256, request_sha256 = _command_hashes(body)
        result = runtime.repository.create_evidence_import_and_enqueue(
            investigation_id=investigation["id"],
            claim_investigation_ids=body.claim_investigation_ids,
            expected_investigation_state_version=(
                body.expected_investigation_state_version
            ),
            expected_claim_state_versions=body.expected_claim_state_versions,
            expected_review_revision=body.expected_review_revision,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            actor=actor,
            reason=body.reason,
            canonical_key=body.canonical_key,
            document_type=body.document_type,
            title=body.title,
            language=body.language,
            publication_number=body.publication_number,
            authority=body.authority,
            declared_date_facts=(
                body.declared_date_facts.model_dump(mode="json")
                if body.declared_date_facts is not None
                else None
            ),
            date_evidence={
                key: value.model_dump(mode="json")
                for key, value in body.date_evidence.items()
            },
            frozen_evidence=frozen.repository_payload(),
        )
        content = dict(result.get("response") or {})
        content["idempotent_replay"] = bool(result.get("idempotent_replay"))
        return JSONResponse(
            status_code=200 if result.get("idempotent_replay") else 202,
            content=public_json(content),
        )

    @application.post(
        "/v1/investigations/{investigation_id}/human-reviews/"
        "document-date-confirmations"
    )
    def confirm_review_document_date(
        investigation_id: str,
        body: DocumentDateConfirmationRequest,
        request: Request,
    ) -> JSONResponse:
        runtime = _runtime(request)
        investigation = _require_investigation(
            runtime.repository, investigation_id
        )
        actor = _review_actor(request)
        idempotency_key_sha256, request_sha256 = _command_hashes(body)
        result = runtime.repository.create_document_date_confirmation_and_enqueue(
            investigation_id=investigation["id"],
            claim_investigation_ids=body.claim_investigation_ids,
            expected_investigation_state_version=(
                body.expected_investigation_state_version
            ),
            expected_claim_state_versions=body.expected_claim_state_versions,
            expected_review_revision=body.expected_review_revision,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            actor=actor,
            reason=body.reason,
            decision=body.decision,
            document_id=body.document_id,
            document_version_id=body.document_version_id,
            expected_qualifications={
                key: value.model_dump(mode="json")
                for key, value in body.expected_qualifications.items()
            },
            date_facts={
                "public_availability_date": body.public_availability_date,
                "publication_date": body.publication_date,
                "filing_date": body.filing_date,
                "priority_date": body.priority_date,
                "source_type": body.source_type,
                "publication_number": body.publication_number,
                "authority": body.authority,
                "cn_application_scope": body.cn_application_scope,
                "date_channel": body.date_channel,
            },
            date_evidence={
                key: value.model_dump(mode="json")
                for key, value in body.date_evidence.items()
            },
        )
        content = dict(result.get("response") or {})
        content["idempotent_replay"] = bool(result.get("idempotent_replay"))
        return JSONResponse(
            status_code=200 if result.get("idempotent_replay") else 202,
            content=public_json(content),
        )

    @application.post(
        "/v1/investigations/{investigation_id}/critical-date-confirmations"
    )
    def confirm_critical_date(
        investigation_id: str,
        body: CriticalDateConfirmationRequest,
        request: Request,
    ) -> JSONResponse:
        runtime = _runtime(request)
        investigation = _require_investigation(
            runtime.repository, investigation_id
        )
        idempotency_key_sha256, request_sha256 = _command_hashes(body)
        result = runtime.repository.confirm_critical_date(
            investigation_id=investigation["id"],
            claim_investigation_id=body.claim_investigation_id,
            decision=body.decision,
            confirmed_date=body.confirmed_date,
            target_publication_date=body.target_publication_date,
            critical_date_basis=body.basis.strip(),
            reason=body.reason.strip(),
            expected_state_version=body.expected_state_version,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            actor="api",
        )
        confirmation = result["confirmation"]
        claim = result["claim"]
        latest_investigation = result["investigation"]
        continuation_required = str(claim.get("status")) in {
            "needs_human_review",
            "search_budget_exhausted",
            "exhausted",
            "partial",
            "failed",
        }
        content = public_json(
            {
                "contract_version": CONTRACT_VERSION,
                "confirmation_id": confirmation["id"],
                "claim_investigation_id": claim["id"],
                "confirmed_date": confirmation["confirmed_date"],
                "target_publication_date": confirmation.get(
                    "target_publication_date"
                ),
                "critical_date_basis": confirmation["critical_date_basis"],
                "claim_state_version": claim["state_version"],
                "investigation_state_version": latest_investigation[
                    "state_version"
                ],
                "investigation_status": latest_investigation["status"],
                "continuation_required": continuation_required,
                "idempotent_replay": bool(result["idempotent_replay"]),
            }
        )
        return JSONResponse(
            status_code=200 if result["idempotent_replay"] else 201,
            content=content,
        )

    @application.post("/v1/investigations/{investigation_id}/continuations")
    def continue_investigation(
        investigation_id: str,
        body: ContinuationRequest,
        request: Request,
    ) -> JSONResponse:
        runtime = _runtime(request)
        investigation = _require_investigation(
            runtime.repository, investigation_id
        )
        idempotency_key_sha256, request_sha256 = _command_hashes(body)
        result = runtime.repository.create_continuation_batch_and_enqueue(
            investigation_id=investigation["id"],
            claim_investigation_ids=body.claim_investigation_ids,
            max_additional_rounds=body.max_additional_rounds,
            reason=body.reason.strip(),
            expected_state_version=body.expected_state_version,
            idempotency_key_sha256=idempotency_key_sha256,
            request_sha256=request_sha256,
            module_code=ORCHESTRATOR_MODULE_CODE,
            contract_version=CONTRACT_VERSION,
            actor="api",
        )
        batch = result["continuation_batch"]
        latest_investigation = result["investigation"]
        run = result["module_run"]
        job = result["job"]
        content = public_json(
            {
                "contract_version": CONTRACT_VERSION,
                "continuation_batch_id": batch["id"],
                "investigation_id": latest_investigation["id"],
                "investigation_status": latest_investigation["status"],
                "investigation_state_version": latest_investigation[
                    "state_version"
                ],
                "claim_investigation_ids": batch["claim_investigation_ids"],
                "max_additional_rounds": batch["max_additional_rounds"],
                "status": batch["status"],
                "module_run_id": run["id"],
                "job_id": job["id"],
                "job_status": job["status"],
                "idempotent_replay": bool(result["idempotent_replay"]),
            }
        )
        return JSONResponse(
            status_code=200 if result["idempotent_replay"] else 201,
            content=content,
        )

    @application.get("/v1/investigations/{investigation_id}/report-data")
    def report_data(
        investigation_id: str,
        request: Request,
        snapshot_id: str | None = None,
        preview: bool = False,
    ) -> JSONResponse:
        runtime = _runtime(request)
        investigation = _require_investigation(
            runtime.repository, investigation_id
        )
        if snapshot_id is not None:
            try:
                snapshot = runtime.repository.get_report_snapshot(
                    investigation["id"], snapshot_id
                )
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=400, detail="snapshot_id 格式无效"
                ) from exc
            if snapshot is None:
                raise HTTPException(status_code=404, detail="报告快照不存在")
        else:
            snapshot = runtime.repository.get_report_snapshot(investigation["id"])

        if snapshot is not None and not preview:
            payload = dict(snapshot["report_snapshot"])
            payload["report_snapshot_id"] = str(snapshot["id"])
            payload["snapshot_sha256"] = str(snapshot["report_sha256"])
            payload["source_state_version"] = int(
                snapshot["source_state_version"]
            )
            payload["source_review_revision"] = int(
                snapshot.get("source_review_revision") or 0
            )
            validated = ReportDataV1.model_validate(payload).model_dump(mode="json")
            return JSONResponse(status_code=200, content=validated)

        if preview:
            raw = runtime.repository.get_report_data(investigation["id"])
            if raw is None:
                raise HTTPException(status_code=404, detail="报告源数据不存在")
            content = build_report_data(
                raw,
                preview=True,
                generated_at=datetime.now(timezone.utc),
            )
            return JSONResponse(status_code=200, content=content)

        return JSONResponse(
            status_code=202,
            content={
                "contract_version": REPORT_CONTRACT_VERSION,
                "code": "REPORT_PENDING",
                "message": "不可变报告快照尚未生成，可稍后重试",
                "investigation_id": str(investigation["id"]),
                "investigation_status": str(investigation["status"]),
                "preview_url": (
                    f"/v1/investigations/{investigation['id']}/report-data?preview=true"
                ),
            },
        )

    @application.post("/v1/lab/module-runs")
    def create_module_lab_run(body: ModuleRunRequest, request: Request) -> JSONResponse:
        runtime = _runtime(request)
        snapshot = _module_request_payload(body)
        if body.module_code == "I1_TARGET_SNAPSHOT" and body.input_mode == "live":
            if not body.investigation_id:
                raise HTTPException(
                    status_code=422,
                    detail="live I1_TARGET_SNAPSHOT 必须关联既有 investigation",
                )
            if body.input:
                raise HTTPException(
                    status_code=422,
                    detail="live I1_TARGET_SNAPSHOT 的 input 必须为空对象",
                )
        if body.claim_investigation_id and not body.investigation_id:
            raise HTTPException(
                status_code=422,
                detail="设置 claim_investigation_id 时必须提供 investigation_id",
            )
        if body.iteration_number is not None and not body.claim_investigation_id:
            raise HTTPException(
                status_code=422,
                detail="设置 iteration_number 时必须提供 claim_investigation_id",
            )

        claim: Mapping[str, Any] | None = None
        iteration: Mapping[str, Any] | None = None
        if body.investigation_id:
            investigation = _require_investigation(
                runtime.repository, body.investigation_id
            )
            if body.claim_investigation_id:
                try:
                    claim = runtime.repository.get_claim_investigation(
                        body.claim_investigation_id
                    )
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="claim_investigation_id 格式无效",
                    ) from exc
                if (
                    claim is None
                    or str(claim.get("investigation_id"))
                    != str(investigation["id"])
                ):
                    raise RepositoryConflictError(
                        "claim 不属于指定 investigation"
                    )
                if body.iteration_number is not None:
                    iteration = runtime.repository.get_claim_iteration(
                        claim["id"], body.iteration_number
                    )
                    if iteration is None:
                        raise RepositoryConflictError(
                            "iteration 不存在或不属于指定 claim"
                        )
        else:
            lab_session = f"invalidity_lab_{snapshot['idempotency_key_sha256'][:32]}"
            investigation = runtime.repository.get_investigation_by_session(lab_session)
            if investigation is None:
                investigation = runtime.repository.create_investigation(
                    analysis_session_id=lab_session,
                    source_snapshot={
                        **snapshot,
                        "snapshot_state": "module_lab_input_frozen",
                    },
                    pipeline_version=PIPELINE_VERSION,
                    settings={"provider_mode": body.input_mode, "module_lab": True},
                    workflow_state={"stage": "module_lab"},
                    status="created",
                )
            elif not _matches_idempotent_request(
                investigation["source_snapshot"], snapshot
            ):
                raise HTTPException(
                    status_code=409,
                    detail="模块实验幂等键已由不同输入占用",
                )

        # In lawyer lab mode we keep module runs explicitly non-replayable.
        # Every invocation should generate a fresh run record even when input is
        # identical, so append a random suffix to avoid cross-click reuse.
        run_key = f"lab:{snapshot['idempotency_key_sha256']}:{secrets.token_hex(8)}"
        run, job, created = _ensure_run_and_job(
            runtime.repository,
            investigation=investigation,
            module_code=body.module_code,
            input_snapshot=snapshot,
            idempotency_key=run_key,
            job_payload={
                "kind": "module_lab",
                "investigation_id": str(investigation["id"]),
                "module_code": body.module_code,
                "input_mode": body.input_mode,
                "claim_investigation_id": (
                    str(claim["id"]) if claim is not None else None
                ),
                "iteration_id": (
                    str(iteration["id"]) if iteration is not None else None
                ),
            },
            event_type="module_lab.queued",
            max_attempts=_module_lab_max_attempts(body),
            claim_investigation_id=(claim["id"] if claim is not None else None),
            iteration_id=(iteration["id"] if iteration is not None else None),
            requested_mode=body.input_mode,
            effective_mode=body.input_mode,
            prompt_version=(
                I4S_PROMPT_VERSION
                if body.module_code == "I4_S_SINGLE_REFERENCE"
                else I4I_PROMPT_VERSION
                if body.module_code == "I4_I_INVENTIVE_STEP"
                else None
            ),
            rule_version=(
                I4S_RULE_VERSION
                if body.module_code == "I4_S_SINGLE_REFERENCE"
                else I4I_RULE_VERSION
                if body.module_code == "I4_I_INVENTIVE_STEP"
                else None
            ),
        )
        content = public_json(
            {
                "contract_version": CONTRACT_VERSION,
                "module_run_id": run["id"],
                "investigation_id": investigation["id"],
                "module_code": run["module_code"],
                "status": job["status"],
                "job_id": job["id"],
                "idempotent_replay": not created,
            }
        )
        return JSONResponse(status_code=201 if created else 200, content=content)

    @application.get("/v1/lab/module-runs/{module_run_id}")
    def get_module_lab_run(module_run_id: str, request: Request) -> dict[str, Any]:
        runtime = _runtime(request)
        try:
            details = runtime.repository.get_module_lab_run(module_run_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="module_run_id 格式无效") from exc
        if details is None:
            raise HTTPException(status_code=404, detail="模块运行不存在")
        return public_json(details)

    @application.get(
        "/v1/lab/module-runs/{module_run_id}/target-figures/{figure_index}"
    )
    def get_module_lab_target_figure(
        module_run_id: str,
        figure_index: int,
        request: Request,
    ) -> FileResponse:
        runtime = _runtime(request)
        if figure_index < 0 or figure_index > 999:
            raise HTTPException(status_code=404, detail="目标专利附图不存在")
        try:
            details = runtime.repository.get_module_lab_run(module_run_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="module_run_id 格式无效") from exc
        if details is None:
            raise HTTPException(status_code=404, detail="模块运行不存在")
        module_run = details.get("module_run") or {}
        if str(module_run.get("module_code") or "") != "I1_TARGET_SNAPSHOT":
            raise HTTPException(status_code=404, detail="目标专利附图不存在")
        investigation_id = str(module_run.get("investigation_id") or "").strip()
        artifact_dir = (
            ArtifactStore(runtime.settings.artifact_root, runtime.settings.environment)
            .investigation_dir(investigation_id)
            / "lab"
            / "module1"
            / module_run_id
        )
        snapshot_path = artifact_dir / "target_snapshot.json"
        try:
            patent = json.loads(snapshot_path.read_text(encoding="utf-8"))
            figures = patent.get("figures") or []
            figure = next(
                item
                for item in figures
                if isinstance(item, Mapping)
                and int(item.get("lab_asset_index", -1)) == figure_index
            )
            path = Path(str(figure.get("file_path") or "")).expanduser().resolve()
        except (OSError, ValueError, TypeError, StopIteration, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=404, detail="目标专利附图不存在") from exc
        figure_root = (artifact_dir / "figures").resolve()
        if not path.is_file() or figure_root not in path.parents:
            raise HTTPException(status_code=404, detail="目标专利附图不存在")
        content = path.read_bytes()
        expected_sha256 = str(figure.get("file_sha256") or "").lower()
        if not expected_sha256 or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise HTTPException(status_code=409, detail="目标专利附图完整性校验失败")
        media_type = str(figure.get("mime_type") or "image/png")
        if media_type not in {"image/png", "image/jpeg", "image/tiff"}:
            raise HTTPException(status_code=415, detail="目标专利附图格式不支持")
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.post("/v1/lab/module-runs/{module_run_id}/cancel")
    def cancel_module_lab_run(
        module_run_id: str,
        body: ModuleRunCancellationRequest,
        request: Request,
    ) -> JSONResponse:
        runtime = _runtime(request)
        try:
            result = runtime.repository.request_module_run_cancellation(
                module_run_id,
                reason=body.reason.strip(),
                actor="api",
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="module_run_id 或取消原因无效"
            ) from exc
        module_run = result["module_run"]
        job = result.get("job")
        content = public_json(
            {
                "contract_version": CONTRACT_VERSION,
                "module_run_id": module_run["id"],
                "investigation_id": module_run["investigation_id"],
                "status": module_run["status"],
                "job_id": job.get("id") if isinstance(job, Mapping) else None,
                "job_status": (
                    job.get("status") if isinstance(job, Mapping) else None
                ),
                "cancel_requested_at": module_run.get("cancel_requested_at"),
                "cancelled_at": module_run.get("cancelled_at"),
                "idempotent_replay": bool(result.get("idempotent_replay")),
            }
        )
        return JSONResponse(
            status_code=200 if result.get("cancelled") else 202,
            content=content,
        )

    @application.post("/v1/lab/module-runs/{module_run_id}/retries")
    def retry_module_lab_run(
        module_run_id: str,
        body: ModuleRunRetryRequest,
        request: Request,
    ) -> JSONResponse:
        runtime = _runtime(request)
        try:
            result = runtime.repository.retry_module_run(
                module_run_id,
                idempotency_key=f"manual-retry:{_hash_text(body.idempotency_key)}",
                reason=body.reason.strip(),
                actor="api",
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="module_run_id 或人工重试参数无效"
            ) from exc
        run = result["module_run"]
        original = result["original_module_run"]
        job = result["job"]
        content = public_json(
            {
                "contract_version": CONTRACT_VERSION,
                "module_run_id": run["id"],
                "retry_of_module_run_id": original["id"],
                "investigation_id": run["investigation_id"],
                "module_code": run["module_code"],
                "status": run["status"],
                "job_id": job["id"],
                "job_status": job["status"],
                "idempotent_replay": bool(result["idempotent_replay"]),
            }
        )
        return JSONResponse(
            status_code=200 if result["idempotent_replay"] else 201,
            content=content,
        )

    return application


app = create_app()


__all__ = [
    "ORCHESTRATOR_MODULE_CODE",
    "PIPELINE_VERSION",
    "ServiceRuntime",
    "app",
    "create_app",
]
