"""PostgreSQL persistence for the isolated invalidity-search service.

The repository deliberately has no SQLite fallback.  Durable workflow state,
worker leases and evidence snapshots rely on PostgreSQL semantics (JSONB and
``FOR UPDATE SKIP LOCKED`` in particular), so accepting a different database
would give a misleading approximation of the production behaviour.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterator, Mapping, Sequence

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from .contracts import I4S_PROMPT_VERSION, I4S_RULE_VERSION


SCHEMA_BY_ENVIRONMENT = {
    "test": "invalidity_test",
    "prod": "invalidity_prod",
}

TABLE_NAMES = (
    "investigations",
    "claim_investigations",
    "claim_limitations",
    "iterations",
    "module_runs",
    "jobs",
    "events",
    "queries",
    "documents",
    "document_versions",
    "document_sources",
    "document_qualifications",
    "feature_disclosures",
    "closest_prior_art_versions",
    "gap_items",
    "combinations",
    "artifacts",
    "critical_date_confirmations",
    "human_review_actions",
    "document_date_fact_revisions",
    "evidence_imports",
    "continuation_batches",
    "report_snapshots",
)

_UNSET = object()

_EXHAUSTED_LEASE_ERROR_CODE = "LEASE_EXPIRED_ATTEMPTS_EXHAUSTED"
_EXHAUSTED_LEASE_ERROR_MESSAGE = "作业租约已过期且重试次数已用尽"
_CANCELLED_ERROR_CODE = "CANCELLED_BY_USER"
_CANCELLED_ERROR_MESSAGE = "用户已取消模块运行"
_REPORTABLE_INVESTIGATION_STATUSES = frozenset(
    {"completed", "partial", "failed", "cancelled", "needs_human_review"}
)
_CONTINUABLE_CLAIM_STATUSES = frozenset(
    {
        "needs_human_review",
        "search_budget_exhausted",
        "exhausted",
        "partial",
        "failed",
    }
)
_SUCCESSFUL_CLAIM_STATUSES = frozenset(
    {"novelty_evidence_complete", "inventive_step_evidence_complete"}
)
_TERMINAL_CLAIM_STATUSES = frozenset(
    {
        *_SUCCESSFUL_CLAIM_STATUSES,
        "needs_human_review",
        "search_budget_exhausted",
        "exhausted",
        "partial",
        "failed",
        "cancelled",
    }
)
_TERMINAL_ITERATION_STATUSES = frozenset(
    {"succeeded", "partial", "failed", "cancelled"}
)
_TERMINAL_QUERY_STATUSES = frozenset(
    {"completed", "partial", "failed", "skipped"}
)
_TERMINAL_MODULE_RUN_STATUSES = frozenset(
    {"succeeded", "partial", "failed", "cancelled"}
)
_CANONICAL_GAP_TYPES = frozenset(
    {"feature_gap", "evidence_gap", "date_gap", "combination_gap"}
)
_GAP_FRONTIER_EVENT_TYPE = "gap_frontier.reconciled"
_MAX_GAP_KEY_LENGTH = 255


class RepositoryConfigurationError(ValueError):
    """Raised when database isolation is not explicit and safe."""


class RepositoryConflictError(RuntimeError):
    """Raised when an optimistic workflow transition loses a race."""

    def __init__(self, message: str, *, code: str = "STATE_CONFLICT") -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class LeaseLostError(RuntimeError):
    """Raised when a worker no longer owns the job lease it is updating."""


def _validate_configuration(database_url: str, schema: str, environment: str) -> None:
    normalized_environment = str(environment or "").strip().lower()
    if normalized_environment not in SCHEMA_BY_ENVIRONMENT:
        raise RepositoryConfigurationError(
            "environment 必须显式设置为 test 或 prod"
        )

    expected_schema = SCHEMA_BY_ENVIRONMENT[normalized_environment]
    if schema != expected_schema:
        raise RepositoryConfigurationError(
            f"{normalized_environment} 环境只能使用 {expected_schema}，当前为 {schema!r}"
        )

    try:
        backend = make_url(str(database_url or "").strip()).get_backend_name()
    except Exception as exc:  # SQLAlchemy deliberately has several URL exceptions.
        raise RepositoryConfigurationError("INVALIDITY_DATABASE_URL 无效") from exc
    if backend != "postgresql":
        raise RepositoryConfigurationError(
            "无效检索持久层只支持 PostgreSQL；禁止使用 SQLite 或其他数据库模拟"
        )


def _json(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _mode_value(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"fixture", "manual", "live"} else None


def module_run_audit_values(
    input_snapshot: Mapping[str, Any] | None,
    output_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive non-sensitive run audit fields from server-side snapshots.

    ``output_sha256`` is always calculated from the canonical JSON snapshot;
    a similarly named value inside a handler/client payload is deliberately
    ignored.  Historical rows may keep the new output fields NULL because the
    original server-side output bytes cannot be reconstructed safely.
    """

    input_values = input_snapshot if isinstance(input_snapshot, Mapping) else {}
    request_values = input_values.get("request")
    if not isinstance(request_values, Mapping):
        request_values = {}
    requested_mode = next(
        (
            mode
            for mode in (
                _mode_value(input_values.get("requested_mode")),
                _mode_value(input_values.get("input_mode")),
                _mode_value(input_values.get("provider_mode")),
                _mode_value(request_values.get("input_mode")),
                _mode_value(request_values.get("provider_mode")),
            )
            if mode is not None
        ),
        None,
    )

    output_values = output_snapshot if isinstance(output_snapshot, Mapping) else {}
    nested_output = output_values.get("output")
    if not isinstance(nested_output, Mapping):
        nested_output = {}

    def output_value(key: str) -> Any:
        value = output_values.get(key)
        return value if value is not None else nested_output.get(key)

    effective_mode = _mode_value(output_value("effective_mode"))
    if output_snapshot is not None and effective_mode is None:
        effective_mode = requested_mode
    actual_provider_value = output_value("actual_provider")
    actual_provider = str(actual_provider_value or "").strip() or None
    model_value = output_value("model_version") or output_value("model")
    model_version = str(model_value or "").strip() or None

    def non_negative_count(key: str) -> int | None:
        value = output_value(key)
        if value is None:
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return max(0, parsed)

    network_value = output_value("network_used")
    return {
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "actual_provider": actual_provider,
        "model_version": model_version,
        "network_used": (
            bool(network_value) if network_value is not None else None
        ),
        "used_target_images": non_negative_count("used_target_images"),
        "used_document_images": non_negative_count("used_document_images"),
        "output_sha256": (
            _sha256_json(output_snapshot) if output_snapshot is not None else None
        ),
    }


def _uuid(value: uuid.UUID | str | None = None) -> uuid.UUID:
    if value is None:
        return uuid.uuid4()
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _gap_value(
    gap: Mapping[str, Any], objective: Mapping[str, Any], key: str
) -> Any:
    value = gap.get(key)
    return value if value is not None else objective.get(key)


def _normalize_gap_key(
    gap: Mapping[str, Any],
    *,
    gap_type: str,
    objective: Mapping[str, Any],
) -> str:
    explicit = next(
        (
            str(value).strip()
            for value in (
                gap.get("gap_key"),
                gap.get("gap_id"),
                objective.get("gap_key"),
                objective.get("gap_id"),
            )
            if value is not None and str(value).strip()
        ),
        "",
    )
    if explicit:
        if len(explicit) > _MAX_GAP_KEY_LENGTH:
            raise ValueError(
                f"gap_key 最长 {_MAX_GAP_KEY_LENGTH} 个字符，当前为 {len(explicit)}"
            )
        return explicit

    identity = {
        "gap_type": gap_type,
        "feature_id": str(_gap_value(gap, objective, "feature_id") or "").strip(),
        "subtype": str(_gap_value(gap, objective, "subtype") or "").strip(),
        "search_anchor": str(
            _gap_value(gap, objective, "search_anchor") or ""
        ).strip(),
    }
    return f"gap-{_sha256_json(identity)[:24]}"


def _objective_from_gap(gap: Mapping[str, Any]) -> dict[str, Any]:
    explicit = gap.get("search_objective")
    if explicit is not None:
        if not isinstance(explicit, Mapping):
            raise ValueError("gap search_objective 必须是对象")
        return dict(explicit)
    excluded = {
        "id",
        "iteration_id",
        "claim_investigation_id",
        "limitation_id",
        "source_module_run_id",
        "status",
        "description",
        "resolved_by_document_id",
        "resolution_evidence",
        "resolved_at",
        "closed_reason",
        "version_no",
        "supersedes_id",
        "review_action_id",
        "gap_fingerprint",
    }
    return {key: value for key, value in gap.items() if key not in excluded}


def _normalize_open_gap(
    gap: Mapping[str, Any],
    *,
    default_source_module_run_id: uuid.UUID | str | None,
) -> dict[str, Any]:
    if not isinstance(gap, Mapping):
        raise ValueError("open_gaps 的每一项都必须是对象")
    objective = _objective_from_gap(gap)
    gap_type = str(
        gap.get("gap_type") or objective.get("gap_type") or gap.get("kind")
        or objective.get("kind") or ""
    ).strip()
    if gap_type not in _CANONICAL_GAP_TYPES:
        raise ValueError(f"非 canonical gap_type: {gap_type or '<empty>'}")
    requested_status = str(gap.get("status") or "open").strip().lower()
    if requested_status != "open":
        raise ValueError("open_gaps 只能包含 status=open 的当前缺口")
    if gap.get("resolved_by_document_id") or gap.get("resolved_at"):
        raise ValueError("open gap 不能携带解决文献或 resolved_at")

    gap_key = _normalize_gap_key(
        gap,
        gap_type=gap_type,
        objective=objective,
    )
    limitation_id = gap.get("limitation_id")
    source_module_run_id = (
        gap.get("source_module_run_id") or default_source_module_run_id
    )
    description = str(
        gap.get("description")
        or objective.get("rationale")
        or gap.get("rationale")
        or ""
    )
    fingerprint_payload = {
        "gap_key": gap_key,
        "gap_type": gap_type,
        "limitation_id": str(limitation_id) if limitation_id else None,
        "status": "open",
        "description": description,
        "search_objective": objective,
    }
    return {
        "gap_key": gap_key,
        "gap_fingerprint": _sha256_json(fingerprint_payload),
        "limitation_id": limitation_id,
        "source_module_run_id": source_module_run_id,
        "gap_type": gap_type,
        "status": "open",
        "description": description,
        "search_objective": objective,
        "resolved_by_document_id": None,
        "resolution_evidence": None,
        "resolved_at": None,
        "closed_reason": None,
    }


def _normalize_closed_gap_resolutions(
    value: Mapping[str, Any] | Sequence[Mapping[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    entries: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        if value.get("gap_key") or value.get("gap_id"):
            key = str(value.get("gap_key") or value.get("gap_id") or "").strip()
            entries.append((key, value))
        else:
            for raw_key, raw_resolution in value.items():
                if raw_resolution is None:
                    raw_resolution = {}
                if not isinstance(raw_resolution, Mapping):
                    raise ValueError("closed_gap_resolutions 的值必须是对象")
                entries.append((str(raw_key).strip(), raw_resolution))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for raw_resolution in value:
            if not isinstance(raw_resolution, Mapping):
                raise ValueError("closed_gap_resolutions 的每一项都必须是对象")
            key = str(
                raw_resolution.get("gap_key") or raw_resolution.get("gap_id") or ""
            ).strip()
            entries.append((key, raw_resolution))
    else:
        raise ValueError("closed_gap_resolutions 必须是对象或对象数组")

    result: dict[str, dict[str, Any]] = {}
    for key, resolution in entries:
        if not key:
            raise ValueError("closed gap resolution 缺少 gap_key/gap_id")
        if key in result:
            raise ValueError(f"closed gap resolution 重复: {key}")
        result[key] = dict(resolution)
    return result


def _latest_gap_versions(
    previous_gap_items: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for raw in previous_gap_items:
        key = str(raw.get("gap_key") or "").strip()
        if not key:
            raise ValueError("历史 gap 缺少 gap_key")
        candidate = dict(raw)
        version_no = int(candidate.get("version_no") or 0)
        if version_no <= 0:
            raise ValueError(f"历史 gap {key} 缺少有效 version_no")
        current = latest.get(key)
        if current is None or version_no > int(current.get("version_no") or 0):
            latest[key] = candidate
    return latest


def _gap_frontier_row_contract(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "gap_key": str(row.get("gap_key") or ""),
        "gap_fingerprint": str(row.get("gap_fingerprint") or ""),
        "version_no": int(row.get("version_no") or 0),
        "supersedes_id": (
            str(row["supersedes_id"]) if row.get("supersedes_id") else None
        ),
        "limitation_id": (
            str(row["limitation_id"]) if row.get("limitation_id") else None
        ),
        "source_module_run_id": (
            str(row["source_module_run_id"])
            if row.get("source_module_run_id")
            else None
        ),
        "gap_type": str(row.get("gap_type") or ""),
        "status": str(row.get("status") or ""),
        "description": str(row.get("description") or ""),
        "search_objective": row.get("search_objective") or {},
        "resolved_by_document_id": (
            str(row["resolved_by_document_id"])
            if row.get("resolved_by_document_id")
            else None
        ),
        "resolution_evidence": row.get("resolution_evidence"),
        "closed_reason": row.get("closed_reason"),
    }


def plan_gap_frontier(
    *,
    iteration_no: int | None,
    review_revision: int | None = None,
    open_gaps: Sequence[Mapping[str, Any]],
    previous_gap_items: Sequence[Mapping[str, Any]] = (),
    default_source_module_run_id: uuid.UUID | str | None = None,
    closed_gap_resolutions: Mapping[str, Any]
    | Sequence[Mapping[str, Any]]
    | None = None,
) -> dict[str, Any]:
    """Build one immutable per-claim gap frontier without mutating history."""

    if (iteration_no is None) == (review_revision is None):
        raise ValueError("gap frontier 必须且只能绑定 iteration 或 review revision")
    if iteration_no is not None and iteration_no <= 0:
        raise ValueError("iteration_no 必须大于 0")
    if review_revision is not None and review_revision <= 0:
        raise ValueError("review_revision 必须大于 0")
    frontier_origin = (
        {"iteration_no": iteration_no}
        if iteration_no is not None
        else {"review_revision": review_revision}
    )
    normalized_open: dict[str, dict[str, Any]] = {}
    for raw in open_gaps:
        item = _normalize_open_gap(
            raw,
            default_source_module_run_id=default_source_module_run_id,
        )
        key = item["gap_key"]
        if key in normalized_open:
            raise ValueError(f"同一前沿包含重复 gap_key: {key}")
        normalized_open[key] = item

    latest = _latest_gap_versions(previous_gap_items)
    resolutions = _normalize_closed_gap_resolutions(closed_gap_resolutions)
    rows: list[dict[str, Any]] = []
    delta = {
        "opened_gap_keys": [],
        "updated_gap_keys": [],
        "carried_gap_keys": [],
        "reopened_gap_keys": [],
        "closed_gap_keys": [],
    }

    for key in sorted(normalized_open):
        item = dict(normalized_open[key])
        previous = latest.get(key)
        item["version_no"] = int(previous.get("version_no") or 0) + 1 if previous else 1
        item["supersedes_id"] = previous.get("id") if previous else None
        rows.append(item)
        if previous is None:
            delta["opened_gap_keys"].append(key)
        elif str(previous.get("status") or "") != "open":
            delta["reopened_gap_keys"].append(key)
        elif str(previous.get("gap_fingerprint") or "") == item["gap_fingerprint"]:
            delta["carried_gap_keys"].append(key)
        else:
            delta["updated_gap_keys"].append(key)

    for key in sorted(latest):
        previous = latest[key]
        if key in normalized_open or str(previous.get("status") or "") != "open":
            continue
        resolution = resolutions.get(key, {})
        closed_reason = str(
            resolution.get("closed_reason")
            or resolution.get("reason")
            or "absent_from_committed_frontier"
        ).strip()
        evidence: dict[str, Any] = {}
        supplied_evidence = resolution.get("resolution_evidence")
        if supplied_evidence is not None:
            if not isinstance(supplied_evidence, Mapping):
                raise ValueError("resolution_evidence 必须是对象")
            evidence.update(dict(supplied_evidence))
        evidence.update(frontier_origin)
        evidence.update(
            {
                "reason": closed_reason,
                "previous_gap_item_id": str(previous.get("id") or ""),
            }
        )
        if resolution.get("human_decision") is not None:
            evidence["human_decision"] = resolution["human_decision"]
        resolved_by_document_id = resolution.get("resolved_by_document_id")
        closure_fingerprint = _sha256_json(
            {
                "gap_key": key,
                "gap_type": previous.get("gap_type"),
                "limitation_id": (
                    str(previous["limitation_id"])
                    if previous.get("limitation_id")
                    else None
                ),
                "status": "closed",
                "closed_reason": closed_reason,
                "resolution_evidence": evidence,
                "resolved_by_document_id": (
                    str(resolved_by_document_id)
                    if resolved_by_document_id
                    else None
                ),
            }
        )
        rows.append(
            {
                "gap_key": key,
                "gap_fingerprint": closure_fingerprint,
                "version_no": int(previous["version_no"]) + 1,
                "supersedes_id": previous.get("id"),
                "limitation_id": previous.get("limitation_id"),
                "source_module_run_id": (
                    resolution.get("source_module_run_id")
                    or default_source_module_run_id
                    or previous.get("source_module_run_id")
                ),
                "gap_type": str(previous.get("gap_type") or ""),
                "status": "closed",
                "description": str(previous.get("description") or ""),
                "search_objective": previous.get("search_objective") or {},
                "resolved_by_document_id": resolved_by_document_id,
                "resolution_evidence": evidence,
                "resolved_at": resolution.get("resolved_at"),
                "closed_reason": closed_reason,
            }
        )
        delta["closed_gap_keys"].append(key)

    unused_resolutions = sorted(set(resolutions).difference(delta["closed_gap_keys"]))
    if unused_resolutions:
        raise ValueError(
            "closed_gap_resolutions 只能引用本轮消失的 open gap: "
            + ", ".join(unused_resolutions)
        )

    rows.sort(key=lambda item: (str(item["gap_key"]), int(item["version_no"])))
    contract_rows = [_gap_frontier_row_contract(item) for item in rows]
    return {
        "rows": rows,
        "open_gap_keys": sorted(normalized_open),
        "closed_gap_keys": list(delta["closed_gap_keys"]),
        "delta": delta,
        "frontier_fingerprint": _sha256_json(
            {
                **frontier_origin,
                "rows": contract_rows,
                "open_gap_keys": sorted(normalized_open),
                "closed_gap_keys": delta["closed_gap_keys"],
            }
        ),
    }


def build_claim_job_sql(schema: str, environment: str) -> str:
    """Build the atomic lease statement after enforcing schema isolation."""

    _validate_configuration(
        "postgresql://contract-only/invalidity", schema, environment
    )
    return f"""
        WITH candidate AS (
            SELECT id
            FROM {schema}.jobs
            WHERE cancel_requested_at IS NULL
              AND attempt_count < max_attempts
              AND available_at <= now()
              AND (
                    status = 'queued'
                    OR (status = 'leased' AND lease_expires_at <= now())
                  )
            ORDER BY priority DESC, available_at ASC, created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE {schema}.jobs AS job
        SET status = 'leased',
            lease_owner = :worker_id,
            lease_token = :lease_token,
            leased_at = now(),
            lease_expires_at = now() + make_interval(secs => :lease_seconds),
            heartbeat_at = now(),
            attempt_count = job.attempt_count + 1,
            updated_at = now()
        FROM candidate
        WHERE job.id = candidate.id
        RETURNING job.*
    """


def build_recover_expired_jobs_sql(schema: str, environment: str) -> str:
    """Build the atomic terminal recovery for exhausted expired leases.

    A lease consumes an attempt when it is acquired.  Consequently a job whose
    final lease expires must be failed without being leased (and executed) one
    more time.  ``claim_job`` runs this statement before looking for new work.
    """

    _validate_configuration(
        "postgresql://contract-only/invalidity", schema, environment
    )
    return f"""
        WITH exhausted AS (
            SELECT id
            FROM {schema}.jobs
            WHERE status = 'leased'
              AND cancel_requested_at IS NULL
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= now()
              AND attempt_count >= max_attempts
            ORDER BY lease_expires_at ASC, created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT :recovery_limit
        )
        UPDATE {schema}.jobs AS job
        SET status = 'failed',
            lease_owner = NULL,
            lease_token = NULL,
            lease_expires_at = NULL,
            last_error = CAST(:last_error AS JSONB),
            finished_at = now(),
            updated_at = now()
        FROM exhausted
        WHERE job.id = exhausted.id
        RETURNING job.*
    """


def build_ddl_statements(schema: str, environment: str) -> tuple[str, ...]:
    """Return idempotent PostgreSQL DDL for one strictly matched environment."""

    _validate_configuration(
        "postgresql://contract-only/invalidity", schema, environment
    )
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.investigations (
            id UUID PRIMARY KEY,
            analysis_session_id TEXT NOT NULL UNIQUE,
            patent_record_id TEXT NOT NULL,
            environment TEXT NOT NULL CHECK (environment = '{environment}'),
            jurisdiction TEXT NOT NULL DEFAULT 'CN',
            analysis_kind TEXT NOT NULL DEFAULT 'invalidity',
            status TEXT NOT NULL DEFAULT 'created',
            pipeline_version TEXT NOT NULL,
            source_snapshot JSONB NOT NULL,
            source_snapshot_sha256 CHAR(64) NOT NULL,
            settings JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            budgets JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            budget_used JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            workflow_state JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            error_message TEXT,
            error_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
            review_revision INTEGER NOT NULL DEFAULT 0 CHECK (review_revision >= 0),
            last_applied_review_action_seq INTEGER NOT NULL DEFAULT 0
                CHECK (last_applied_review_action_seq >= 0),
            review_recomputation_required BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )
        """,
        f"""
        ALTER TABLE {schema}.investigations
            ADD COLUMN IF NOT EXISTS workflow_state JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            ADD COLUMN IF NOT EXISTS error_message TEXT,
            ADD COLUMN IF NOT EXISTS error_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            ADD COLUMN IF NOT EXISTS review_revision INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS last_applied_review_action_seq INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS review_recomputation_required BOOLEAN NOT NULL DEFAULT false
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.claim_investigations (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            claim_id TEXT NOT NULL,
            claim_variant_id TEXT NOT NULL DEFAULT 'base',
            source_claim_text TEXT NOT NULL,
            expanded_claim_text TEXT NOT NULL,
            dependency_path JSONB NOT NULL DEFAULT '[]'::jsonb,
            source_snapshot JSONB NOT NULL,
            fingerprint CHAR(64) NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            current_iteration_no INTEGER NOT NULL DEFAULT 0
                CHECK (current_iteration_no >= 0),
            terminal_reason TEXT,
            result_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            critical_date DATE,
            critical_date_basis TEXT,
            target_publication_date DATE,
            state_version INTEGER NOT NULL DEFAULT 0 CHECK (state_version >= 0),
            review_revision INTEGER NOT NULL DEFAULT 0 CHECK (review_revision >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            UNIQUE (investigation_id, claim_id, claim_variant_id)
        )
        """,
        f"""
        ALTER TABLE {schema}.claim_investigations
            ADD COLUMN IF NOT EXISTS result_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            ADD COLUMN IF NOT EXISTS critical_date DATE,
            ADD COLUMN IF NOT EXISTS critical_date_basis TEXT,
            ADD COLUMN IF NOT EXISTS target_publication_date DATE,
            ADD COLUMN IF NOT EXISTS review_revision INTEGER NOT NULL DEFAULT 0
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.claim_limitations (
            id UUID PRIMARY KEY,
            claim_investigation_id UUID NOT NULL
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            feature_key TEXT NOT NULL,
            sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
            limitation_text TEXT NOT NULL,
            normalized_text TEXT NOT NULL,
            origin_claim_id TEXT NOT NULL,
            source_start INTEGER,
            source_end INTEGER,
            is_inherited BOOLEAN NOT NULL DEFAULT false,
            limitation_snapshot JSONB NOT NULL,
            snapshot_version TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (claim_investigation_id, feature_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.iterations (
            id UUID PRIMARY KEY,
            claim_investigation_id UUID NOT NULL
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            parent_iteration_id UUID
                REFERENCES {schema}.iterations(id) ON DELETE SET NULL,
            iteration_no INTEGER NOT NULL CHECK (iteration_no > 0),
            purpose TEXT NOT NULL,
            trigger_reason TEXT,
            status TEXT NOT NULL DEFAULT 'planned',
            gap_feature_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            anchor_document_id UUID,
            progress_signature TEXT,
            metrics JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            stop_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            UNIQUE (claim_investigation_id, iteration_no)
        )
        """,
        f"""
        ALTER TABLE {schema}.iterations
            ALTER COLUMN progress_signature TYPE TEXT
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.module_runs (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            claim_investigation_id UUID
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            iteration_id UUID
                REFERENCES {schema}.iterations(id) ON DELETE CASCADE,
            module_code TEXT NOT NULL,
            contract_version TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'created',
            input_snapshot JSONB NOT NULL,
            output_snapshot JSONB,
            input_sha256 CHAR(64) NOT NULL,
            output_sha256 CHAR(64),
            idempotency_key TEXT NOT NULL,
            requested_mode TEXT,
            effective_mode TEXT,
            actual_provider TEXT,
            network_used BOOLEAN,
            used_target_images INTEGER CHECK (
                used_target_images IS NULL OR used_target_images >= 0
            ),
            used_document_images INTEGER CHECK (
                used_document_images IS NULL OR used_document_images >= 0
            ),
            model_version TEXT,
            prompt_version TEXT,
            rule_version TEXT,
            error_code TEXT,
            error_message TEXT,
            retryable BOOLEAN,
            attempt_no INTEGER NOT NULL DEFAULT 0 CHECK (attempt_no >= 0),
            cancel_requested_at TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ,
            retry_of_module_run_id UUID
                REFERENCES {schema}.module_runs(id) ON DELETE SET NULL,
            retry_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (investigation_id, module_code, idempotency_key)
        )
        """,
        f"""
        ALTER TABLE {schema}.module_runs
            ADD COLUMN IF NOT EXISTS output_sha256 CHAR(64),
            ADD COLUMN IF NOT EXISTS requested_mode TEXT,
            ADD COLUMN IF NOT EXISTS effective_mode TEXT,
            ADD COLUMN IF NOT EXISTS actual_provider TEXT,
            ADD COLUMN IF NOT EXISTS network_used BOOLEAN,
            ADD COLUMN IF NOT EXISTS used_target_images INTEGER,
            ADD COLUMN IF NOT EXISTS used_document_images INTEGER,
            ADD COLUMN IF NOT EXISTS cancel_requested_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS retry_of_module_run_id UUID
                REFERENCES {schema}.module_runs(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS retry_reason TEXT
        """,
        f"""
        UPDATE {schema}.module_runs
        SET requested_mode = COALESCE(
            NULLIF(LOWER(BTRIM(input_snapshot ->> 'requested_mode')), ''),
            NULLIF(LOWER(BTRIM(input_snapshot ->> 'input_mode')), ''),
            NULLIF(LOWER(BTRIM(input_snapshot ->> 'provider_mode')), ''),
            NULLIF(LOWER(BTRIM(input_snapshot #>> '{{request,input_mode}}')), ''),
            NULLIF(LOWER(BTRIM(input_snapshot #>> '{{request,provider_mode}}')), '')
        )
        WHERE requested_mode IS NULL
        """,
        f"""
        CREATE INDEX IF NOT EXISTS module_runs_patent_fetch_cache_idx
        ON {schema}.module_runs (
            (
                UPPER(
                    REGEXP_REPLACE(
                        COALESCE(
                            output_snapshot #>> '{{output,publication_number}}',
                            ''
                        ),
                        '[^A-Za-z0-9]', '', 'g'
                    )
                )
            ),
            created_at DESC
        )
        WHERE module_code = 'I3_FETCH'
          AND status = 'succeeded'
          AND COALESCE(
                effective_mode,
                NULLIF(output_snapshot #>> '{{output,effective_mode}}', '')
              ) = 'live'
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.jobs (
            id UUID PRIMARY KEY,
            module_run_id UUID NOT NULL UNIQUE
                REFERENCES {schema}.module_runs(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')),
            priority INTEGER NOT NULL DEFAULT 0,
            payload JSONB NOT NULL,
            available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
            max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
            lease_owner TEXT,
            lease_token UUID,
            leased_at TIMESTAMPTZ,
            lease_expires_at TIMESTAMPTZ,
            heartbeat_at TIMESTAMPTZ,
            cancel_requested_at TIMESTAMPTZ,
            cancel_reason TEXT,
            cancelled_at TIMESTAMPTZ,
            last_error JSONB,
            finished_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        ALTER TABLE {schema}.jobs
            ADD COLUMN IF NOT EXISTS cancel_reason TEXT,
            ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ
        """,
        f"""
        CREATE INDEX IF NOT EXISTS jobs_ready_idx
        ON {schema}.jobs (priority DESC, available_at ASC, created_at ASC)
        WHERE status IN ('queued', 'leased') AND cancel_requested_at IS NULL
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.events (
            id BIGSERIAL PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            claim_investigation_id UUID
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            iteration_id UUID
                REFERENCES {schema}.iterations(id) ON DELETE CASCADE,
            module_run_id UUID
                REFERENCES {schema}.module_runs(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS events_investigation_idx
        ON {schema}.events (investigation_id, id)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.queries (
            id UUID PRIMARY KEY,
            iteration_id UUID NOT NULL
                REFERENCES {schema}.iterations(id) ON DELETE CASCADE,
            parent_query_id UUID
                REFERENCES {schema}.queries(id) ON DELETE SET NULL,
            query_type TEXT NOT NULL,
            channel TEXT NOT NULL,
            language TEXT,
            expression TEXT NOT NULL,
            feature_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            classifications JSONB NOT NULL DEFAULT '[]'::jsonb,
            date_filter JSONB NOT NULL,
            provider_plan JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            rationale TEXT,
            query_sha256 CHAR(64) NOT NULL,
            status TEXT NOT NULL DEFAULT 'planned',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            executed_at TIMESTAMPTZ,
            UNIQUE (iteration_id, query_sha256)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.documents (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            canonical_key TEXT NOT NULL,
            document_type TEXT NOT NULL,
            evidence_level TEXT NOT NULL DEFAULT 'lead',
            title TEXT NOT NULL,
            language TEXT,
            identifiers JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            dates JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            family_key TEXT,
            content_sha256 CHAR(64),
            current_document_version_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (investigation_id, canonical_key)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.artifacts (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            module_run_id UUID
                REFERENCES {schema}.module_runs(id) ON DELETE SET NULL,
            artifact_type TEXT NOT NULL,
            uri TEXT NOT NULL,
            sha256 CHAR(64) NOT NULL,
            mime_type TEXT,
            byte_size BIGINT CHECK (byte_size IS NULL OR byte_size >= 0),
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.document_versions (
            id UUID PRIMARY KEY,
            document_id UUID NOT NULL
                REFERENCES {schema}.documents(id) ON DELETE CASCADE,
            version_no INTEGER NOT NULL CHECK (version_no > 0),
            content_sha256 CHAR(64) NOT NULL,
            content_artifact_id UUID
                REFERENCES {schema}.artifacts(id) ON DELETE RESTRICT,
            mime_type TEXT,
            byte_size BIGINT CHECK (byte_size IS NULL OR byte_size >= 0),
            acquisition_kind TEXT NOT NULL,
            version_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (document_id, content_sha256),
            UNIQUE (document_id, version_no)
        )
        """,
        f"""
        INSERT INTO {schema}.document_versions (
            id, document_id, version_no, content_sha256, acquisition_kind,
            version_metadata, created_by
        )
        SELECT
            md5(document.id::text || ':' || document.content_sha256)::uuid,
            document.id, 1, document.content_sha256, 'legacy_backfill',
            jsonb_build_object('backfilled_from_documents', true),
            'schema_migration'
        FROM {schema}.documents AS document
        WHERE document.content_sha256 IS NOT NULL
        ON CONFLICT (document_id, content_sha256) DO NOTHING
        """,
        f"""
        ALTER TABLE {schema}.documents
            ADD COLUMN IF NOT EXISTS current_document_version_id UUID
        """,
        f"""
        UPDATE {schema}.documents AS document
        SET current_document_version_id = version.id
        FROM {schema}.document_versions AS version
        WHERE version.document_id = document.id
          AND version.content_sha256 = document.content_sha256
          AND document.current_document_version_id IS NULL
        """,
        f"""
        DO $document_version_fk$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.documents'::regclass
                  AND conname = 'documents_current_document_version_id_fkey'
            ) THEN
                ALTER TABLE {schema}.documents
                    ADD CONSTRAINT documents_current_document_version_id_fkey
                    FOREIGN KEY (current_document_version_id)
                    REFERENCES {schema}.document_versions(id) ON DELETE SET NULL;
            END IF;
        END
        $document_version_fk$
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.document_sources (
            id UUID PRIMARY KEY,
            document_id UUID NOT NULL
                REFERENCES {schema}.documents(id) ON DELETE CASCADE,
            document_version_id UUID
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT,
            provider TEXT NOT NULL,
            source_url TEXT NOT NULL,
            provider_document_id TEXT,
            retrieved_at TIMESTAMPTZ NOT NULL,
            mime_type TEXT,
            artifact_id UUID
                REFERENCES {schema}.artifacts(id) ON DELETE SET NULL,
            snapshot_uri TEXT,
            snapshot_sha256 CHAR(64),
            snapshot_byte_size BIGINT
                CHECK (snapshot_byte_size IS NULL OR snapshot_byte_size >= 0),
            raw_response JSONB,
            status TEXT NOT NULL DEFAULT 'retrieved',
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (document_id, provider, source_url, retrieved_at)
        )
        """,
        f"""
        ALTER TABLE {schema}.document_sources
            ADD COLUMN IF NOT EXISTS document_version_id UUID
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT
        """,
        f"""
        UPDATE {schema}.document_sources AS source
        SET document_version_id = document.current_document_version_id
        FROM {schema}.documents AS document
        WHERE document.id = source.document_id
          AND source.document_version_id IS NULL
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.document_qualifications (
            id UUID PRIMARY KEY,
            document_id UUID NOT NULL
                REFERENCES {schema}.documents(id) ON DELETE CASCADE,
            document_version_id UUID
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT,
            claim_investigation_id UUID NOT NULL
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            limitation_id UUID
                REFERENCES {schema}.claim_limitations(id) ON DELETE CASCADE,
            critical_date DATE NOT NULL,
            earliest_priority_date DATE,
            filing_date DATE,
            publication_date DATE,
            public_availability_date DATE,
            eligibility_type TEXT NOT NULL,
            novelty_eligible BOOLEAN NOT NULL,
            inventive_step_eligible BOOLEAN NOT NULL,
            verification_status TEXT NOT NULL,
            verification_reason TEXT NOT NULL,
            date_rule_version TEXT NOT NULL,
            human_confirmed BOOLEAN NOT NULL DEFAULT false,
            reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
            evidence_references JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            supersedes_id UUID
                REFERENCES {schema}.document_qualifications(id) ON DELETE SET NULL,
            review_action_id UUID,
            assessment_version INTEGER NOT NULL DEFAULT 1
                CHECK (assessment_version > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (document_id, claim_investigation_id, limitation_id, assessment_version)
        )
        """,
        f"""
        ALTER TABLE {schema}.document_qualifications
            ADD COLUMN IF NOT EXISTS document_version_id UUID
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT,
            ADD COLUMN IF NOT EXISTS reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS evidence_references JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            ADD COLUMN IF NOT EXISTS supersedes_id UUID
                REFERENCES {schema}.document_qualifications(id) ON DELETE SET NULL,
            ADD COLUMN IF NOT EXISTS review_action_id UUID
        """,
        f"""
        UPDATE {schema}.document_qualifications AS qualification
        SET document_version_id = document.current_document_version_id
        FROM {schema}.documents AS document
        WHERE document.id = qualification.document_id
          AND qualification.document_version_id IS NULL
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.feature_disclosures (
            id UUID PRIMARY KEY,
            module_run_id UUID NOT NULL
                REFERENCES {schema}.module_runs(id) ON DELETE CASCADE,
            claim_investigation_id UUID NOT NULL
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            limitation_id UUID NOT NULL
                REFERENCES {schema}.claim_limitations(id) ON DELETE CASCADE,
            document_id UUID NOT NULL
                REFERENCES {schema}.documents(id) ON DELETE CASCADE,
            document_version_id UUID
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT,
            document_source_id UUID
                REFERENCES {schema}.document_sources(id) ON DELETE SET NULL,
            disclosure_status TEXT NOT NULL,
            excerpt TEXT,
            locator JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            excerpt_sha256 CHAR(64),
            analysis JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            model_version TEXT,
            prompt_version TEXT,
            rule_version TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (module_run_id, limitation_id, document_id)
        )
        """,
        f"""
        ALTER TABLE {schema}.feature_disclosures
            ADD COLUMN IF NOT EXISTS document_version_id UUID
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT
        """,
        f"""
        UPDATE {schema}.feature_disclosures AS disclosure
        SET document_version_id = document.current_document_version_id
        FROM {schema}.documents AS document
        WHERE document.id = disclosure.document_id
          AND disclosure.document_version_id IS NULL
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.closest_prior_art_versions (
            id UUID PRIMARY KEY,
            claim_investigation_id UUID NOT NULL
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            iteration_id UUID
                REFERENCES {schema}.iterations(id) ON DELETE CASCADE,
            document_id UUID NOT NULL
                REFERENCES {schema}.documents(id) ON DELETE CASCADE,
            document_version_id UUID
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT,
            version_no INTEGER NOT NULL CHECK (version_no > 0),
            metrics JSONB NOT NULL,
            rationale JSONB NOT NULL,
            selected_by TEXT NOT NULL,
            review_action_id UUID,
            supersedes_id UUID
                REFERENCES {schema}.closest_prior_art_versions(id) ON DELETE SET NULL,
            is_current BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (claim_investigation_id, version_no)
        )
        """,
        f"""
        ALTER TABLE {schema}.closest_prior_art_versions
            ALTER COLUMN iteration_id DROP NOT NULL,
            ADD COLUMN IF NOT EXISTS document_version_id UUID
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT,
            ADD COLUMN IF NOT EXISTS review_action_id UUID
        """,
        f"""
        UPDATE {schema}.closest_prior_art_versions AS selection
        SET document_version_id = document.current_document_version_id
        FROM {schema}.documents AS document
        WHERE document.id = selection.document_id
          AND selection.document_version_id IS NULL
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS closest_prior_art_one_current_idx
        ON {schema}.closest_prior_art_versions (claim_investigation_id)
        WHERE is_current
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.gap_items (
            id UUID PRIMARY KEY,
            iteration_id UUID
                REFERENCES {schema}.iterations(id) ON DELETE CASCADE,
            claim_investigation_id UUID NOT NULL
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            limitation_id UUID
                REFERENCES {schema}.claim_limitations(id) ON DELETE CASCADE,
            source_module_run_id UUID
                REFERENCES {schema}.module_runs(id) ON DELETE SET NULL,
            gap_type TEXT NOT NULL,
            gap_key TEXT NOT NULL,
            gap_fingerprint CHAR(64) NOT NULL,
            version_no INTEGER NOT NULL CHECK (version_no > 0),
            supersedes_id UUID
                REFERENCES {schema}.gap_items(id) ON DELETE SET NULL,
            review_action_id UUID,
            status TEXT NOT NULL DEFAULT 'open',
            description TEXT NOT NULL,
            search_objective JSONB NOT NULL,
            resolved_by_document_id UUID
                REFERENCES {schema}.documents(id) ON DELETE SET NULL,
            resolution_evidence JSONB,
            closed_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            resolved_at TIMESTAMPTZ
        )
        """,
        f"""
        ALTER TABLE {schema}.gap_items
            ALTER COLUMN limitation_id DROP NOT NULL,
            ALTER COLUMN iteration_id DROP NOT NULL,
            ADD COLUMN IF NOT EXISTS gap_key TEXT,
            ADD COLUMN IF NOT EXISTS gap_fingerprint CHAR(64),
            ADD COLUMN IF NOT EXISTS version_no INTEGER,
            ADD COLUMN IF NOT EXISTS supersedes_id UUID,
            ADD COLUMN IF NOT EXISTS review_action_id UUID,
            ADD COLUMN IF NOT EXISTS closed_reason TEXT
        """,
        f"""
        ALTER TABLE {schema}.gap_items
            DROP CONSTRAINT IF EXISTS gap_items_iteration_id_limitation_id_gap_type_key
        """,
        f"""
        UPDATE {schema}.gap_items
        SET gap_key = COALESCE(
            NULLIF(BTRIM(search_objective ->> 'gap_key'), ''),
            NULLIF(BTRIM(search_objective ->> 'gap_id'), ''),
            'gap-' || md5(concat_ws(
                '|',
                gap_type,
                COALESCE(limitation_id::text, ''),
                COALESCE(search_objective ->> 'feature_id', ''),
                COALESCE(search_objective ->> 'subtype', ''),
                COALESCE(search_objective ->> 'search_anchor', '')
            ))
        )
        WHERE gap_key IS NULL OR BTRIM(gap_key) = ''
        """,
        f"""
        UPDATE {schema}.gap_items
        SET gap_fingerprint = (
            md5(concat_ws(
                '|', gap_key, gap_type, COALESCE(limitation_id::text, ''),
                status, description, search_objective::text
            )) ||
            md5('gap-frontier-v1|' || concat_ws(
                '|', gap_key, gap_type, COALESCE(limitation_id::text, ''),
                status, description, search_objective::text
            ))
        )
        WHERE gap_fingerprint IS NULL
        """,
        f"""
        WITH ranked AS (
            SELECT
                gap.id,
                row_number() OVER (
                    PARTITION BY gap.claim_investigation_id, gap.gap_key
                    ORDER BY iteration.iteration_no, gap.created_at, gap.id
                )::integer AS calculated_version_no,
                lag(gap.id) OVER (
                    PARTITION BY gap.claim_investigation_id, gap.gap_key
                    ORDER BY iteration.iteration_no, gap.created_at, gap.id
                ) AS calculated_supersedes_id
            FROM {schema}.gap_items AS gap
            JOIN {schema}.iterations AS iteration ON iteration.id = gap.iteration_id
        )
        UPDATE {schema}.gap_items AS gap
        SET version_no = ranked.calculated_version_no,
            supersedes_id = COALESCE(gap.supersedes_id, ranked.calculated_supersedes_id)
        FROM ranked
        WHERE gap.id = ranked.id AND gap.version_no IS NULL
        """,
        f"""
        ALTER TABLE {schema}.gap_items
            ALTER COLUMN gap_key SET NOT NULL,
            ALTER COLUMN gap_fingerprint SET NOT NULL,
            ALTER COLUMN version_no SET NOT NULL
        """,
        f"""
        DO $gap_migration$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.gap_items'::regclass
                  AND conname = 'gap_items_version_no_check'
            ) THEN
                ALTER TABLE {schema}.gap_items
                    ADD CONSTRAINT gap_items_version_no_check CHECK (version_no > 0);
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.gap_items'::regclass
                  AND conname = 'gap_items_supersedes_id_fkey'
            ) THEN
                ALTER TABLE {schema}.gap_items
                    ADD CONSTRAINT gap_items_supersedes_id_fkey
                    FOREIGN KEY (supersedes_id)
                    REFERENCES {schema}.gap_items(id) ON DELETE SET NULL;
            END IF;
        END
        $gap_migration$
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS gap_items_iteration_key_uidx
        ON {schema}.gap_items (iteration_id, gap_key)
        WHERE iteration_id IS NOT NULL
        """,
        f"""
        DROP INDEX IF EXISTS {schema}.gap_items_review_action_key_uidx
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS gap_items_review_action_key_uidx
        ON {schema}.gap_items (
            review_action_id, claim_investigation_id, gap_key
        )
        WHERE review_action_id IS NOT NULL
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS gap_items_lineage_version_uidx
        ON {schema}.gap_items (claim_investigation_id, gap_key, version_no)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.combinations (
            id UUID PRIMARY KEY,
            claim_investigation_id UUID NOT NULL
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            iteration_id UUID
                REFERENCES {schema}.iterations(id) ON DELETE CASCADE,
            module_run_id UUID NOT NULL
                REFERENCES {schema}.module_runs(id) ON DELETE CASCADE,
            closest_prior_art_version_id UUID NOT NULL
                REFERENCES {schema}.closest_prior_art_versions(id) ON DELETE CASCADE,
            document_ids JSONB NOT NULL,
            document_version_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL,
            coverage_complete BOOLEAN NOT NULL DEFAULT false,
            motivation_status TEXT NOT NULL,
            analysis JSONB NOT NULL,
            review_action_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (module_run_id, closest_prior_art_version_id)
        )
        """,
        f"""
        ALTER TABLE {schema}.combinations
            ALTER COLUMN iteration_id DROP NOT NULL,
            ADD COLUMN IF NOT EXISTS document_version_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS review_action_id UUID
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.critical_date_confirmations (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            claim_investigation_id UUID NOT NULL
                REFERENCES {schema}.claim_investigations(id) ON DELETE CASCADE,
            decision TEXT NOT NULL CHECK (
                decision IN ('confirm_priority', 'use_filing_date', 'set_manual_date')
            ),
            confirmed_date DATE NOT NULL,
            target_publication_date DATE,
            critical_date_basis TEXT NOT NULL,
            reason TEXT NOT NULL,
            previous_claim_status TEXT NOT NULL,
            previous_claim_state_version INTEGER NOT NULL CHECK (
                previous_claim_state_version >= 0
            ),
            resulting_claim_state_version INTEGER NOT NULL CHECK (
                resulting_claim_state_version > previous_claim_state_version
            ),
            actor TEXT NOT NULL,
            idempotency_key_sha256 CHAR(64) NOT NULL,
            request_sha256 CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (claim_investigation_id, idempotency_key_sha256)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.human_review_actions (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            action_type TEXT NOT NULL CHECK (
                action_type IN (
                    'evidence_import', 'document_date_confirmation',
                    'closest_prior_art_selection', 'gap_decision'
                )
            ),
            action_seq INTEGER NOT NULL CHECK (action_seq > 0),
            target_snapshot JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            request_sha256 CHAR(64) NOT NULL,
            idempotency_key_sha256 CHAR(64) NOT NULL,
            base_investigation_state_version INTEGER NOT NULL CHECK (
                base_investigation_state_version >= 0
            ),
            resulting_investigation_state_version INTEGER NOT NULL CHECK (
                resulting_investigation_state_version > base_investigation_state_version
            ),
            base_claim_state_versions JSONB NOT NULL,
            resulting_claim_state_versions JSONB NOT NULL,
            base_review_revision INTEGER NOT NULL CHECK (base_review_revision >= 0),
            resulting_review_revision INTEGER NOT NULL CHECK (
                resulting_review_revision > base_review_revision
            ),
            status TEXT NOT NULL DEFAULT 'queued' CHECK (
                status IN ('queued', 'running', 'applied', 'failed', 'cancelled')
            ),
            module_run_id UUID UNIQUE
                REFERENCES {schema}.module_runs(id) ON DELETE RESTRICT,
            job_id UUID UNIQUE
                REFERENCES {schema}.jobs(id) ON DELETE RESTRICT,
            invalidated_derivations JSONB NOT NULL DEFAULT '[]'::jsonb,
            pending_recomputation JSONB NOT NULL DEFAULT '[]'::jsonb,
            response_snapshot JSONB,
            result_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            error_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            UNIQUE (investigation_id, action_seq),
            UNIQUE (investigation_id, idempotency_key_sha256)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.document_date_fact_revisions (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            document_id UUID NOT NULL
                REFERENCES {schema}.documents(id) ON DELETE CASCADE,
            document_version_id UUID NOT NULL
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT,
            review_action_id UUID NOT NULL UNIQUE
                REFERENCES {schema}.human_review_actions(id) ON DELETE RESTRICT,
            revision_no INTEGER NOT NULL CHECK (revision_no > 0),
            supersedes_id UUID
                REFERENCES {schema}.document_date_fact_revisions(id) ON DELETE SET NULL,
            decision TEXT NOT NULL CHECK (
                decision IN ('confirm_facts', 'exclude', 'reopen_review')
            ),
            claim_investigation_ids JSONB NOT NULL,
            expected_qualifications JSONB NOT NULL,
            public_availability_date DATE,
            publication_date DATE,
            filing_date DATE,
            priority_date DATE,
            source_type TEXT NOT NULL,
            publication_number TEXT,
            authority TEXT,
            cn_application_scope BOOLEAN,
            date_channel TEXT NOT NULL,
            date_evidence JSONB NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (document_version_id, revision_no)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.evidence_imports (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            review_action_id UUID NOT NULL UNIQUE
                REFERENCES {schema}.human_review_actions(id) ON DELETE RESTRICT,
            document_id UUID NOT NULL
                REFERENCES {schema}.documents(id) ON DELETE RESTRICT,
            document_version_id UUID NOT NULL
                REFERENCES {schema}.document_versions(id) ON DELETE RESTRICT,
            document_source_id UUID NOT NULL
                REFERENCES {schema}.document_sources(id) ON DELETE RESTRICT,
            content_artifact_id UUID NOT NULL
                REFERENCES {schema}.artifacts(id) ON DELETE RESTRICT,
            claim_investigation_ids JSONB NOT NULL,
            declared_date_facts JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            date_evidence JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            import_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        DO $review_action_fks$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.document_qualifications'::regclass
                  AND conname = 'document_qualifications_review_action_id_fkey'
            ) THEN
                ALTER TABLE {schema}.document_qualifications
                    ADD CONSTRAINT document_qualifications_review_action_id_fkey
                    FOREIGN KEY (review_action_id)
                    REFERENCES {schema}.human_review_actions(id) ON DELETE RESTRICT;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.closest_prior_art_versions'::regclass
                  AND conname = 'closest_prior_art_versions_review_action_id_fkey'
            ) THEN
                ALTER TABLE {schema}.closest_prior_art_versions
                    ADD CONSTRAINT closest_prior_art_versions_review_action_id_fkey
                    FOREIGN KEY (review_action_id)
                    REFERENCES {schema}.human_review_actions(id) ON DELETE RESTRICT;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.gap_items'::regclass
                  AND conname = 'gap_items_review_action_id_fkey'
            ) THEN
                ALTER TABLE {schema}.gap_items
                    ADD CONSTRAINT gap_items_review_action_id_fkey
                    FOREIGN KEY (review_action_id)
                    REFERENCES {schema}.human_review_actions(id) ON DELETE RESTRICT;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.combinations'::regclass
                  AND conname = 'combinations_review_action_id_fkey'
            ) THEN
                ALTER TABLE {schema}.combinations
                    ADD CONSTRAINT combinations_review_action_id_fkey
                    FOREIGN KEY (review_action_id)
                    REFERENCES {schema}.human_review_actions(id) ON DELETE RESTRICT;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.closest_prior_art_versions'::regclass
                  AND conname = 'closest_prior_art_origin_check'
            ) THEN
                ALTER TABLE {schema}.closest_prior_art_versions
                    ADD CONSTRAINT closest_prior_art_origin_check
                    CHECK ((iteration_id IS NOT NULL) <> (review_action_id IS NOT NULL))
                    NOT VALID;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.gap_items'::regclass
                  AND conname = 'gap_items_origin_check'
            ) THEN
                ALTER TABLE {schema}.gap_items
                    ADD CONSTRAINT gap_items_origin_check
                    CHECK ((iteration_id IS NOT NULL) <> (review_action_id IS NOT NULL))
                    NOT VALID;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{schema}.combinations'::regclass
                  AND conname = 'combinations_origin_check'
            ) THEN
                ALTER TABLE {schema}.combinations
                    ADD CONSTRAINT combinations_origin_check
                    CHECK ((iteration_id IS NOT NULL) <> (review_action_id IS NOT NULL))
                    NOT VALID;
            END IF;
        END
        $review_action_fks$
        """,
        f"""
        ALTER TABLE {schema}.closest_prior_art_versions
            VALIDATE CONSTRAINT closest_prior_art_origin_check
        """,
        f"""
        ALTER TABLE {schema}.gap_items
            VALIDATE CONSTRAINT gap_items_origin_check
        """,
        f"""
        ALTER TABLE {schema}.combinations
            VALIDATE CONSTRAINT combinations_origin_check
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.continuation_batches (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            claim_investigation_ids JSONB NOT NULL,
            max_additional_rounds INTEGER NOT NULL CHECK (
                max_additional_rounds BETWEEN 1 AND 3
            ),
            reason TEXT NOT NULL,
            round_plan JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued' CHECK (
                status IN ('queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled')
            ),
            module_run_id UUID NOT NULL UNIQUE
                REFERENCES {schema}.module_runs(id) ON DELETE RESTRICT,
            previous_investigation_status TEXT NOT NULL,
            source_state_version INTEGER NOT NULL CHECK (source_state_version >= 0),
            resulting_state_version INTEGER NOT NULL CHECK (
                resulting_state_version > source_state_version
            ),
            idempotency_key_sha256 CHAR(64) NOT NULL,
            request_sha256 CHAR(64) NOT NULL,
            error_summary JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            UNIQUE (investigation_id, idempotency_key_sha256)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.report_snapshots (
            id UUID PRIMARY KEY,
            investigation_id UUID NOT NULL
                REFERENCES {schema}.investigations(id) ON DELETE CASCADE,
            contract_version TEXT NOT NULL,
            source_state_version INTEGER NOT NULL CHECK (source_state_version >= 0),
            source_review_revision INTEGER NOT NULL DEFAULT 0 CHECK (
                source_review_revision >= 0
            ),
            module_run_id UUID
                REFERENCES {schema}.module_runs(id) ON DELETE SET NULL,
            report_snapshot JSONB NOT NULL,
            report_sha256 CHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (investigation_id, contract_version, source_state_version)
        )
        """,
        f"""
        ALTER TABLE {schema}.report_snapshots
            ADD COLUMN IF NOT EXISTS source_review_revision INTEGER NOT NULL DEFAULT 0
        """,
        f"""
        ALTER TABLE {schema}.report_snapshots
            DROP CONSTRAINT IF EXISTS
            report_snapshots_investigation_id_contract_version_source_state_version_key
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS report_snapshots_source_version_uidx
        ON {schema}.report_snapshots (
            investigation_id, contract_version,
            source_state_version, source_review_revision
        )
        """,
    )


class Repository:
    """Synchronous PostgreSQL repository with explicit environment isolation."""

    def __init__(self, database_url: str, schema: str, environment: str) -> None:
        normalized_environment = str(environment or "").strip().lower()
        _validate_configuration(database_url, schema, normalized_environment)
        self.database_url = database_url
        self.schema = schema
        self.environment = normalized_environment
        self._engine: Engine | None = None

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                future=True,
            )
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        """Expose a real PostgreSQL transaction for multi-record workflow writes."""

        with self.engine.begin() as connection:
            yield connection

    def initialize(self) -> None:
        with self.transaction() as connection:
            for statement in build_ddl_statements(self.schema, self.environment):
                connection.execute(text(statement))
            # Older worker versions could mark the Investigation failed in a
            # second transaction while leaving its current round open.  Repair
            # only those already-failed I0 investigations; no history is
            # deleted and the operation is idempotent.
            self._reconcile_terminal_i0_failures(
                connection,
                actor="repository-initialize",
                limit=1_000,
            )

    def init_schema(self) -> None:
        """Compatibility name used by the API/worker bootstrap."""

        self.initialize()

    @staticmethod
    def _row(result: Any) -> dict[str, Any] | None:
        row = result.mappings().first()
        return dict(row) if row is not None else None

    def create_investigation(
        self,
        *,
        analysis_session_id: str,
        patent_record_id: str | int | None = None,
        source_snapshot: Mapping[str, Any],
        pipeline_version: str,
        settings: Mapping[str, Any] | None = None,
        budgets: Mapping[str, Any] | None = None,
        workflow_state: Mapping[str, Any] | None = None,
        investigation_id: uuid.UUID | str | None = None,
        status: str = "created",
    ) -> dict[str, Any]:
        identifier = _uuid(investigation_id)
        source = _json(source_snapshot)
        sql = text(
            f"""
            INSERT INTO {self.schema}.investigations (
                id, analysis_session_id, patent_record_id, environment, status,
                pipeline_version, source_snapshot, source_snapshot_sha256,
                settings, budgets, workflow_state
            ) VALUES (
                :id, :analysis_session_id, :patent_record_id, :environment, :status,
                :pipeline_version, CAST(:source_snapshot AS JSONB), :snapshot_sha256,
                CAST(:settings AS JSONB), CAST(:budgets AS JSONB),
                CAST(:workflow_state AS JSONB)
            )
            RETURNING *
            """
        )
        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    sql,
                    {
                        "id": identifier,
                        "analysis_session_id": analysis_session_id,
                        "patent_record_id": (
                            str(patent_record_id)
                            if patent_record_id is not None
                            else f"source:{analysis_session_id}"
                        ),
                        "environment": self.environment,
                        "status": status,
                        "pipeline_version": pipeline_version,
                        "source_snapshot": source,
                        "snapshot_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                        "settings": _json(settings),
                        "budgets": _json(budgets),
                        "workflow_state": _json(workflow_state),
                    },
                )
            )
        assert row is not None
        return row

    def get_investigation(
        self, investigation_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"SELECT * FROM {self.schema}.investigations WHERE id = :id"
                    ),
                    {"id": _uuid(investigation_id)},
                )
            )

    def get_investigation_by_session(
        self, analysis_session_id: str
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.investigations
                        WHERE analysis_session_id = :analysis_session_id
                        """
                    ),
                    {"analysis_session_id": analysis_session_id},
                )
            )

    def update_investigation(
        self,
        investigation_id: uuid.UUID | str,
        *,
        expected_state_version: int | None = None,
        status: str | None = None,
        source_snapshot: Mapping[str, Any] | None = None,
        workflow_state: Mapping[str, Any] | object = _UNSET,
        completed_at: datetime | None | object = _UNSET,
        error_message: str | None | object = _UNSET,
        error_metadata: Mapping[str, Any] | None | object = _UNSET,
        actor: str = "orchestrator",
        event_type: str = "investigation.updated",
        event_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update allowed workflow fields with optional state-version CAS.

        The method always emits an event in the same transaction.  Passing
        ``None`` for ``error_message`` or ``completed_at`` explicitly clears
        that value; omitted values remain unchanged.
        """

        assignments = [
            "state_version = state_version + 1",
            "updated_at = now()",
        ]
        parameters: dict[str, Any] = {"id": _uuid(investigation_id)}
        changed_fields: list[str] = []
        if status is not None:
            assignments.append("status = :status")
            parameters["status"] = status
            changed_fields.append("status")
        if source_snapshot is not None:
            serialized = _json(source_snapshot)
            assignments.extend(
                (
                    "source_snapshot = CAST(:source_snapshot AS JSONB)",
                    "source_snapshot_sha256 = :source_snapshot_sha256",
                )
            )
            parameters["source_snapshot"] = serialized
            parameters["source_snapshot_sha256"] = hashlib.sha256(
                serialized.encode("utf-8")
            ).hexdigest()
            changed_fields.append("source_snapshot")
        if workflow_state is not _UNSET:
            assignments.append("workflow_state = CAST(:workflow_state AS JSONB)")
            parameters["workflow_state"] = _json(workflow_state)
            changed_fields.append("workflow_state")
        if completed_at is not _UNSET:
            assignments.append("completed_at = :completed_at")
            parameters["completed_at"] = completed_at
            changed_fields.append("completed_at")
        if error_message is not _UNSET:
            assignments.append("error_message = :error_message")
            parameters["error_message"] = error_message
            changed_fields.append("error_message")
        if error_metadata is not _UNSET:
            assignments.append("error_metadata = CAST(:error_metadata AS JSONB)")
            parameters["error_metadata"] = _json(error_metadata)
            changed_fields.append("error_metadata")

        version_predicate = ""
        if expected_state_version is not None:
            version_predicate = "AND state_version = :expected_state_version"
            parameters["expected_state_version"] = expected_state_version

        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.investigations
                        SET {", ".join(assignments)}
                        WHERE id = :id {version_predicate}
                        RETURNING *
                        """
                    ),
                    parameters,
                )
            )
            if row is None:
                raise RepositoryConflictError(
                    "investigation 不存在或 state_version 已变化"
                )
            self._insert_event(
                connection,
                investigation_id=row["id"],
                event_type=event_type,
                actor=actor,
                payload={
                    **dict(event_payload or {}),
                    "changed_fields": changed_fields,
                    "state_version": row["state_version"],
                },
            )
            return row

    def merge_investigation_workflow_state(
        self,
        investigation_id: uuid.UUID | str,
        patch: Mapping[str, Any],
        *,
        expected_state_version: int | None = None,
        actor: str = "orchestrator",
        event_type: str = "investigation.workflow_state_merged",
    ) -> dict[str, Any]:
        """Top-level JSONB merge with optional optimistic concurrency control."""

        parameters: dict[str, Any] = {
            "id": _uuid(investigation_id),
            "patch": _json(patch),
        }
        version_predicate = ""
        if expected_state_version is not None:
            version_predicate = "AND state_version = :expected_state_version"
            parameters["expected_state_version"] = expected_state_version
        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.investigations
                        SET workflow_state = workflow_state || CAST(:patch AS JSONB),
                            state_version = state_version + 1,
                            updated_at = now()
                        WHERE id = :id {version_predicate}
                        RETURNING *
                        """
                    ),
                    parameters,
                )
            )
            if row is None:
                raise RepositoryConflictError(
                    "investigation 不存在或 state_version 已变化"
                )
            self._insert_event(
                connection,
                investigation_id=row["id"],
                event_type=event_type,
                actor=actor,
                payload={
                    "patch_keys": sorted(str(key) for key in patch),
                    "state_version": row["state_version"],
                },
            )
            return row

    def create_claim_investigation(
        self,
        *,
        investigation_id: uuid.UUID | str,
        claim_id: str | int,
        source_claim_text: str,
        expanded_claim_text: str,
        source_snapshot: Mapping[str, Any],
        dependency_path: Sequence[str | int] = (),
        claim_variant_id: str = "base",
        claim_investigation_id: uuid.UUID | str | None = None,
        status: str = "pending",
        critical_date: date | None = None,
        critical_date_basis: str | None = None,
        target_publication_date: date | None = None,
        result_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        identifier = _uuid(claim_investigation_id)
        fingerprint_source = {
            "claim_id": str(claim_id),
            "claim_variant_id": claim_variant_id,
            "expanded_claim_text": expanded_claim_text,
            "dependency_path": list(dependency_path),
        }
        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.claim_investigations (
                            id, investigation_id, claim_id, claim_variant_id,
                            source_claim_text, expanded_claim_text, dependency_path,
                            source_snapshot, fingerprint, status, critical_date,
                            critical_date_basis, target_publication_date,
                            result_summary
                        ) VALUES (
                            :id, :investigation_id, :claim_id, :claim_variant_id,
                            :source_claim_text, :expanded_claim_text,
                            CAST(:dependency_path AS JSONB),
                            CAST(:source_snapshot AS JSONB), :fingerprint, :status,
                            :critical_date, :critical_date_basis,
                            :target_publication_date,
                            CAST(:result_summary AS JSONB)
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": identifier,
                        "investigation_id": _uuid(investigation_id),
                        "claim_id": str(claim_id),
                        "claim_variant_id": claim_variant_id,
                        "source_claim_text": source_claim_text,
                        "expanded_claim_text": expanded_claim_text,
                        "dependency_path": _json(list(dependency_path)),
                        "source_snapshot": _json(source_snapshot),
                        "fingerprint": _sha256_json(fingerprint_source),
                        "status": status,
                        "critical_date": critical_date,
                        "critical_date_basis": critical_date_basis,
                        "target_publication_date": target_publication_date,
                        "result_summary": _json(result_summary),
                    },
                )
            )
        assert row is not None
        return row

    def get_claim_investigation(
        self, claim_investigation_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.claim_investigations
                        WHERE id = :id
                        """
                    ),
                    {"id": _uuid(claim_investigation_id)},
                )
            )

    def list_claim_investigations(
        self, investigation_id: uuid.UUID | str
    ) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.claim_investigations
                    WHERE investigation_id = :investigation_id
                    ORDER BY claim_id ASC, claim_variant_id ASC, created_at ASC
                    """
                ),
                {"investigation_id": _uuid(investigation_id)},
            ).mappings()
            return [dict(row) for row in rows]

    def confirm_critical_date(
        self,
        *,
        investigation_id: uuid.UUID | str,
        claim_investigation_id: uuid.UUID | str,
        decision: str,
        confirmed_date: date,
        target_publication_date: date | None,
        critical_date_basis: str,
        reason: str,
        expected_state_version: int,
        idempotency_key_sha256: str,
        request_sha256: str,
        actor: str = "api",
        confirmation_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any]:
        """Confirm one claim's date without rewriting its workflow outcome."""

        allowed_decisions = {
            "confirm_priority",
            "use_filing_date",
            "set_manual_date",
        }
        if decision not in allowed_decisions:
            raise ValueError(f"不支持的关键日决定: {decision}")
        if expected_state_version < 0:
            raise ValueError("expected_state_version 不能小于 0")
        if not critical_date_basis.strip() or not reason.strip():
            raise ValueError("critical_date_basis 和 reason 不能为空")

        investigation_uuid = _uuid(investigation_id)
        claim_uuid = _uuid(claim_investigation_id)
        with self.transaction() as connection:
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.investigations
                        WHERE id = :investigation_id
                        FOR UPDATE
                        """
                    ),
                    {"investigation_id": investigation_uuid},
                )
            )
            claim = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.claim_investigations
                        WHERE id = :claim_id
                          AND investigation_id = :investigation_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "claim_id": claim_uuid,
                        "investigation_id": investigation_uuid,
                    },
                )
            )
            if investigation is None or claim is None:
                raise RepositoryConflictError(
                    "investigation、claim 不存在或归属不一致"
                )
            existing = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.critical_date_confirmations
                        WHERE claim_investigation_id = :claim_id
                          AND idempotency_key_sha256 = :idempotency_key_sha256
                        """
                    ),
                    {
                        "claim_id": claim_uuid,
                        "idempotency_key_sha256": idempotency_key_sha256,
                    },
                )
            )
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise RepositoryConflictError(
                        "关键日确认幂等键已用于不同请求"
                    )
                return {
                    "confirmation": existing,
                    "claim": claim,
                    "investigation": investigation,
                    "idempotent_replay": True,
                }
            if claim["state_version"] != expected_state_version:
                raise RepositoryConflictError(
                    "claim state_version 已变化，请重新读取后确认"
                )

            previous_status = str(claim["status"])
            previous_version = int(claim["state_version"])
            claim = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.claim_investigations
                        SET critical_date = :confirmed_date,
                            target_publication_date = :target_publication_date,
                            critical_date_basis = :critical_date_basis,
                            result_summary = result_summary || CAST(:summary_patch AS JSONB),
                            state_version = state_version + 1,
                            updated_at = now()
                        WHERE id = :claim_id
                          AND state_version = :expected_state_version
                        RETURNING *
                        """
                    ),
                    {
                        "claim_id": claim_uuid,
                        "confirmed_date": confirmed_date,
                        "target_publication_date": target_publication_date,
                        "critical_date_basis": critical_date_basis,
                        "summary_patch": _json(
                            {
                                "critical_date_confirmation": {
                                    "decision": decision,
                                    "confirmed_date": confirmed_date.isoformat(),
                                    "target_publication_date": (
                                        target_publication_date.isoformat()
                                        if target_publication_date
                                        else None
                                    ),
                                    "critical_date_basis": critical_date_basis,
                                }
                            }
                        ),
                        "expected_state_version": expected_state_version,
                    },
                )
            )
            if claim is None:
                raise RepositoryConflictError("claim state_version 已变化")
            confirmation = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.critical_date_confirmations (
                            id, investigation_id, claim_investigation_id, decision,
                            confirmed_date, target_publication_date,
                            critical_date_basis, reason, previous_claim_status,
                            previous_claim_state_version,
                            resulting_claim_state_version, actor,
                            idempotency_key_sha256, request_sha256
                        ) VALUES (
                            :id, :investigation_id, :claim_id, :decision,
                            :confirmed_date, :target_publication_date,
                            :critical_date_basis, :reason, :previous_claim_status,
                            :previous_claim_state_version,
                            :resulting_claim_state_version, :actor,
                            :idempotency_key_sha256, :request_sha256
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": _uuid(confirmation_id),
                        "investigation_id": investigation_uuid,
                        "claim_id": claim_uuid,
                        "decision": decision,
                        "confirmed_date": confirmed_date,
                        "target_publication_date": target_publication_date,
                        "critical_date_basis": critical_date_basis,
                        "reason": reason,
                        "previous_claim_status": previous_status,
                        "previous_claim_state_version": previous_version,
                        "resulting_claim_state_version": claim["state_version"],
                        "actor": actor,
                        "idempotency_key_sha256": idempotency_key_sha256,
                        "request_sha256": request_sha256,
                    },
                )
            )
            assert confirmation is not None
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.investigations
                        SET state_version = state_version + 1,
                            updated_at = now()
                        WHERE id = :investigation_id
                        RETURNING *
                        """
                    ),
                    {"investigation_id": investigation_uuid},
                )
            )
            assert investigation is not None
            self._insert_event(
                connection,
                investigation_id=investigation_uuid,
                claim_investigation_id=claim_uuid,
                event_type="claim.critical_date_confirmed",
                actor=actor,
                payload={
                    "confirmation_id": str(confirmation["id"]),
                    "decision": decision,
                    "confirmed_date": confirmed_date.isoformat(),
                    "critical_date_basis": critical_date_basis,
                    "previous_claim_status": previous_status,
                    "claim_state_version": claim["state_version"],
                    "investigation_state_version": investigation["state_version"],
                },
            )
            return {
                "confirmation": confirmation,
                "claim": claim,
                "investigation": investigation,
                "idempotent_replay": False,
            }

    def get_latest_critical_date_confirmation(
        self, claim_investigation_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.critical_date_confirmations
                        WHERE claim_investigation_id = :claim_id
                        ORDER BY created_at DESC, id DESC
                        LIMIT 1
                        """
                    ),
                    {"claim_id": _uuid(claim_investigation_id)},
                )
            )

    def create_continuation_batch_and_enqueue(
        self,
        *,
        investigation_id: uuid.UUID | str,
        claim_investigation_ids: Sequence[uuid.UUID | str] | None,
        max_additional_rounds: int,
        reason: str,
        expected_state_version: int,
        idempotency_key_sha256: str,
        request_sha256: str,
        module_code: str,
        contract_version: str,
        actor: str = "api",
        priority: int = 0,
        max_attempts: int = 3,
        continuation_batch_id: uuid.UUID | str | None = None,
        module_run_id: uuid.UUID | str | None = None,
        job_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any]:
        """Atomically preserve old rounds and queue an explicit continuation."""

        if not 1 <= max_additional_rounds <= 3:
            raise ValueError("max_additional_rounds 必须在 1..3 范围内")
        if not reason.strip():
            raise ValueError("reason 不能为空")
        investigation_uuid = _uuid(investigation_id)
        requested_claim_ids = (
            [_uuid(value) for value in claim_investigation_ids]
            if claim_investigation_ids
            else []
        )
        if len(set(requested_claim_ids)) != len(requested_claim_ids):
            raise ValueError("claim_investigation_ids 不能重复")

        with self.transaction() as connection:
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.investigations
                        WHERE id = :investigation_id
                        FOR UPDATE
                        """
                    ),
                    {"investigation_id": investigation_uuid},
                )
            )
            if investigation is None:
                raise RepositoryConflictError("investigation 不存在")
            existing = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.continuation_batches
                        WHERE investigation_id = :investigation_id
                          AND idempotency_key_sha256 = :idempotency_key_sha256
                        """
                    ),
                    {
                        "investigation_id": investigation_uuid,
                        "idempotency_key_sha256": idempotency_key_sha256,
                    },
                )
            )
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise RepositoryConflictError("继续检索幂等键已用于不同请求")
                module_run = self._row(
                    connection.execute(
                        text(
                            f"SELECT * FROM {self.schema}.module_runs WHERE id = :id"
                        ),
                        {"id": existing["module_run_id"]},
                    )
                )
                job = self._row(
                    connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.jobs
                            WHERE module_run_id = :module_run_id
                            """
                        ),
                        {"module_run_id": existing["module_run_id"]},
                    )
                )
                return {
                    "continuation_batch": existing,
                    "investigation": investigation,
                    "module_run": module_run,
                    "job": job,
                    "idempotent_replay": True,
                }
            if investigation["state_version"] != expected_state_version:
                raise RepositoryConflictError(
                    "investigation state_version 已变化，请重新读取后继续"
                )

            all_claims = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.claim_investigations
                        WHERE investigation_id = :investigation_id
                        ORDER BY claim_id, claim_variant_id, created_at
                        FOR UPDATE
                        """
                    ),
                    {"investigation_id": investigation_uuid},
                ).mappings()
            ]
            gap_history = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT gap.claim_investigation_id, gap.gap_key,
                               gap.status, gap.version_no, gap.created_at, gap.id
                        FROM {self.schema}.gap_items AS gap
                        JOIN {self.schema}.claim_investigations AS claim
                          ON claim.id = gap.claim_investigation_id
                        WHERE claim.investigation_id = :investigation_id
                        ORDER BY gap.claim_investigation_id, gap.gap_key,
                                 gap.version_no DESC, gap.created_at DESC,
                                 gap.id DESC
                        FOR UPDATE OF gap
                        """
                    ),
                    {"investigation_id": investigation_uuid},
                ).mappings()
            ]
            latest_gap_status: dict[tuple[uuid.UUID, str], str] = {}
            for gap in gap_history:
                lineage = (
                    gap["claim_investigation_id"],
                    str(gap["gap_key"]),
                )
                latest_gap_status.setdefault(lineage, str(gap["status"]))
            claims_with_open_gaps = {
                claim_id
                for (claim_id, _gap_key), status in latest_gap_status.items()
                if status == "open"
            }
            if requested_claim_ids:
                requested = set(requested_claim_ids)
                selected_claims = [row for row in all_claims if row["id"] in requested]
                if {row["id"] for row in selected_claims} != requested:
                    raise RepositoryConflictError(
                        "继续检索的 claim 不存在或不属于该 investigation"
                    )
            else:
                selected_claims = [
                    row
                    for row in all_claims
                    if str(row["status"]) in _CONTINUABLE_CLAIM_STATUSES
                    and row["id"] in claims_with_open_gaps
                ]
            if not selected_claims:
                raise RepositoryConflictError("没有可继续检索的 claim")
            successful = [
                str(row["id"])
                for row in selected_claims
                if str(row["status"]) in _SUCCESSFUL_CLAIM_STATUSES
            ]
            if successful:
                raise RepositoryConflictError(
                    "以下 claim 已达到成功证据终态，普通 continuation 不得重开；"
                    "如需补强应另建补充调查: "
                    + ", ".join(successful)
                )
            blocked = [
                str(row["id"])
                for row in selected_claims
                if str(row["status"]) not in _CONTINUABLE_CLAIM_STATUSES
            ]
            if blocked:
                raise RepositoryConflictError(
                    "以下 claim 不处于允许继续的非成功终态: "
                    + ", ".join(blocked)
                )
            without_open_gaps = [
                str(row["id"])
                for row in selected_claims
                if row["id"] not in claims_with_open_gaps
            ]
            if without_open_gaps:
                raise RepositoryConflictError(
                    "以下 claim 没有未解决的 open gap，普通 continuation 不得重开: "
                    + ", ".join(without_open_gaps)
                )

            round_plan: list[dict[str, Any]] = []
            for claim in selected_claims:
                iterations = [
                    dict(row)
                    for row in connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.iterations
                            WHERE claim_investigation_id = :claim_id
                            ORDER BY iteration_no DESC
                            FOR UPDATE
                            """
                        ),
                        {"claim_id": claim["id"]},
                    ).mappings()
                ]
                if any(
                    str(item["status"]) in {"planned", "running"}
                    for item in iterations
                ):
                    raise RepositoryConflictError(
                        f"claim {claim['id']} 仍有未收口 iteration，拒绝并发继续"
                    )
                current = max(
                    [int(item["iteration_no"]) for item in iterations]
                    + [int(claim.get("current_iteration_no") or 0)]
                )
                round_plan.append(
                    {
                        "claim_investigation_id": str(claim["id"]),
                        "parent_iteration_id": (
                            str(iterations[0]["id"]) if iterations else None
                        ),
                        "start_iteration_no": current + 1,
                        "end_iteration_no": current + max_additional_rounds,
                    }
                )

            batch_uuid = _uuid(continuation_batch_id)
            run_uuid = _uuid(module_run_id)
            input_snapshot = {
                "kind": "continuation",
                "investigation_id": str(investigation_uuid),
                "continuation_batch_id": str(batch_uuid),
                "round_plan": round_plan,
                "max_additional_rounds": max_additional_rounds,
                "reason": reason,
            }
            serialized_input = _json(input_snapshot)
            module_run = self._insert_module_run(
                connection,
                module_run_id=run_uuid,
                investigation_id=investigation_uuid,
                claim_investigation_id=None,
                iteration_id=None,
                module_code=module_code,
                contract_version=contract_version,
                input_snapshot=serialized_input,
                input_sha256=hashlib.sha256(serialized_input.encode("utf-8")).hexdigest(),
                idempotency_key=f"continuation:{idempotency_key_sha256}",
                status="queued",
                model_version=None,
                prompt_version=None,
                rule_version=None,
            )
            batch = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.continuation_batches (
                            id, investigation_id, claim_investigation_ids,
                            max_additional_rounds, reason, round_plan, status,
                            module_run_id, previous_investigation_status,
                            source_state_version, resulting_state_version,
                            idempotency_key_sha256, request_sha256
                        ) VALUES (
                            :id, :investigation_id,
                            CAST(:claim_investigation_ids AS JSONB),
                            :max_additional_rounds, :reason,
                            CAST(:round_plan AS JSONB), 'queued', :module_run_id,
                            :previous_investigation_status, :source_state_version,
                            :resulting_state_version, :idempotency_key_sha256,
                            :request_sha256
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": batch_uuid,
                        "investigation_id": investigation_uuid,
                        "claim_investigation_ids": _json(
                            [str(row["id"]) for row in selected_claims]
                        ),
                        "max_additional_rounds": max_additional_rounds,
                        "reason": reason,
                        "round_plan": _json(round_plan),
                        "module_run_id": run_uuid,
                        "previous_investigation_status": investigation["status"],
                        "source_state_version": investigation["state_version"],
                        "resulting_state_version": investigation["state_version"] + 1,
                        "idempotency_key_sha256": idempotency_key_sha256,
                        "request_sha256": request_sha256,
                    },
                )
            )
            assert batch is not None
            job = self._insert_job(
                connection,
                job_id=_uuid(job_id),
                module_run_id=run_uuid,
                payload={
                    "kind": "continuation",
                    "investigation_id": str(investigation_uuid),
                    "continuation_batch_id": str(batch_uuid),
                    "round_plan": round_plan,
                },
                priority=priority,
                max_attempts=max_attempts,
                available_at=None,
            )
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.investigations
                        SET status = 'queued', completed_at = NULL,
                            state_version = state_version + 1,
                            workflow_state = workflow_state || CAST(:workflow_patch AS JSONB),
                            updated_at = now()
                        WHERE id = :investigation_id
                          AND state_version = :expected_state_version
                        RETURNING *
                        """
                    ),
                    {
                        "investigation_id": investigation_uuid,
                        "expected_state_version": expected_state_version,
                        "workflow_patch": _json(
                            {
                                "stage": "continuation_queued",
                                "continuation_batch_id": str(batch_uuid),
                            }
                        ),
                    },
                )
            )
            if investigation is None:
                raise RepositoryConflictError("investigation state_version 已变化")
            self._insert_event(
                connection,
                investigation_id=investigation_uuid,
                module_run_id=run_uuid,
                event_type="investigation.continuation_queued",
                actor=actor,
                payload={
                    "continuation_batch_id": str(batch_uuid),
                    "claim_investigation_ids": [
                        str(row["id"]) for row in selected_claims
                    ],
                    "max_additional_rounds": max_additional_rounds,
                    "state_version": investigation["state_version"],
                },
            )
            return {
                "continuation_batch": batch,
                "investigation": investigation,
                "module_run": module_run,
                "job": job,
                "idempotent_replay": False,
            }

    def get_continuation_batch(
        self, continuation_batch_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"SELECT * FROM {self.schema}.continuation_batches WHERE id = :id"
                    ),
                    {"id": _uuid(continuation_batch_id)},
                )
            )

    def update_continuation_batch(
        self,
        continuation_batch_id: uuid.UUID | str,
        *,
        status: str,
        error_summary: Mapping[str, Any] | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        if status not in {
            "queued", "running", "succeeded", "partial", "failed", "cancelled"
        }:
            raise ValueError(f"不支持的 continuation status: {status}")
        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.continuation_batches
                        SET status = :status,
                            error_summary = CAST(:error_summary AS JSONB),
                            started_at = CASE
                                WHEN :status = 'running' THEN COALESCE(started_at, now())
                                ELSE started_at END,
                            completed_at = CASE
                                WHEN :status IN ('succeeded', 'partial', 'failed', 'cancelled')
                                THEN now() ELSE NULL END
                        WHERE id = :id
                        RETURNING *
                        """
                    ),
                    {
                        "id": _uuid(continuation_batch_id),
                        "status": status,
                        "error_summary": _json(error_summary),
                    },
                )
            )
            if row is None:
                raise RepositoryConflictError("continuation batch 不存在")
            self._insert_event(
                connection,
                investigation_id=row["investigation_id"],
                module_run_id=row["module_run_id"],
                event_type="investigation.continuation_status_changed",
                actor=actor,
                payload={"continuation_batch_id": str(row["id"]), "status": status},
            )
            return row

    def update_claim_status(
        self,
        claim_investigation_id: uuid.UUID | str,
        status: str,
        *,
        expected_state_version: int | None = None,
        current_iteration_no: int | None = None,
        terminal_reason: str | None | object = _UNSET,
        result_summary: Mapping[str, Any] | object = _UNSET,
        result_summary_patch: Mapping[str, Any] | None = None,
        critical_date: date | None | object = _UNSET,
        critical_date_basis: str | None | object = _UNSET,
        target_publication_date: date | None | object = _UNSET,
        completed_at: datetime | None | object = _UNSET,
        actor: str = "orchestrator",
        event_type: str = "claim.status_changed",
        event_payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """CAS-aware claim transition with an audit event in the same commit."""

        if result_summary is not _UNSET and result_summary_patch is not None:
            raise ValueError("result_summary 与 result_summary_patch 不能同时设置")
        assignments = [
            "status = :status",
            "state_version = state_version + 1",
            "updated_at = now()",
        ]
        parameters: dict[str, Any] = {
            "id": _uuid(claim_investigation_id),
            "status": status,
        }
        if current_iteration_no is not None:
            assignments.append("current_iteration_no = :current_iteration_no")
            parameters["current_iteration_no"] = current_iteration_no
        if terminal_reason is not _UNSET:
            assignments.append("terminal_reason = :terminal_reason")
            parameters["terminal_reason"] = terminal_reason
        if result_summary is not _UNSET:
            assignments.append("result_summary = CAST(:result_summary AS JSONB)")
            parameters["result_summary"] = _json(result_summary)
        elif result_summary_patch is not None:
            assignments.append(
                "result_summary = result_summary || CAST(:result_summary_patch AS JSONB)"
            )
            parameters["result_summary_patch"] = _json(result_summary_patch)
        if critical_date is not _UNSET:
            assignments.append("critical_date = :critical_date")
            parameters["critical_date"] = critical_date
        if critical_date_basis is not _UNSET:
            assignments.append("critical_date_basis = :critical_date_basis")
            parameters["critical_date_basis"] = critical_date_basis
        if target_publication_date is not _UNSET:
            assignments.append("target_publication_date = :target_publication_date")
            parameters["target_publication_date"] = target_publication_date
        if completed_at is not _UNSET:
            assignments.append("completed_at = :completed_at")
            parameters["completed_at"] = completed_at

        version_predicate = ""
        if expected_state_version is not None:
            version_predicate = "AND state_version = :expected_state_version"
            parameters["expected_state_version"] = expected_state_version
        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.claim_investigations
                        SET {", ".join(assignments)}
                        WHERE id = :id {version_predicate}
                        RETURNING *
                        """
                    ),
                    parameters,
                )
            )
            if row is None:
                raise RepositoryConflictError(
                    "claim 不存在或 state_version 已变化"
                )
            self._insert_event(
                connection,
                investigation_id=row["investigation_id"],
                claim_investigation_id=row["id"],
                event_type=event_type,
                actor=actor,
                payload={
                    **dict(event_payload or {}),
                    "status": status,
                    "state_version": row["state_version"],
                },
            )
            return row

    def mark_claim_terminal(
        self,
        claim_investigation_id: uuid.UUID | str,
        *,
        status: str,
        terminal_reason: str,
        result_summary: Mapping[str, Any],
        expected_state_version: int | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        return self.update_claim_status(
            claim_investigation_id,
            status,
            expected_state_version=expected_state_version,
            terminal_reason=terminal_reason,
            result_summary=result_summary,
            completed_at=datetime.now().astimezone(),
            actor=actor,
            event_type="claim.terminal",
        )

    def insert_claim_limitations(
        self,
        claim_investigation_id: uuid.UUID | str,
        limitations: Sequence[Mapping[str, Any]],
        *,
        snapshot_version: str = "v1",
        actor: str = "orchestrator",
    ) -> list[dict[str, Any]]:
        """Insert one immutable limitation decomposition as an audited batch."""

        claim_uuid = _uuid(claim_investigation_id)
        with self.transaction() as connection:
            claim = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.claim_investigations
                        WHERE id = :id FOR UPDATE
                        """
                    ),
                    {"id": claim_uuid},
                )
            )
            if claim is None:
                raise RepositoryConflictError("claim 不存在")
            inserted: list[dict[str, Any]] = []
            for index, limitation in enumerate(limitations):
                feature_key = str(
                    limitation.get("feature_key")
                    or limitation.get("feature_id")
                    or f"F{index + 1}"
                )
                limitation_text = str(
                    limitation.get("limitation_text")
                    or limitation.get("text")
                    or ""
                ).strip()
                if not limitation_text:
                    raise ValueError(f"limitation {feature_key} 缺少文本")
                sequence_no = int(limitation.get("sequence_no", index))
                snapshot = limitation.get("limitation_snapshot") or dict(limitation)
                row = self._row(
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO {self.schema}.claim_limitations (
                                id, claim_investigation_id, feature_key, sequence_no,
                                limitation_text, normalized_text, origin_claim_id,
                                source_start, source_end, is_inherited,
                                limitation_snapshot, snapshot_version
                            ) VALUES (
                                :id, :claim_investigation_id, :feature_key,
                                :sequence_no, :limitation_text, :normalized_text,
                                :origin_claim_id, :source_start, :source_end,
                                :is_inherited, CAST(:limitation_snapshot AS JSONB),
                                :snapshot_version
                            )
                            RETURNING *
                            """
                        ),
                        {
                            "id": _uuid(limitation.get("id")),
                            "claim_investigation_id": claim_uuid,
                            "feature_key": feature_key,
                            "sequence_no": sequence_no,
                            "limitation_text": limitation_text,
                            "normalized_text": str(
                                limitation.get("normalized_text") or limitation_text
                            ),
                            "origin_claim_id": str(
                                limitation.get("origin_claim_id") or claim["claim_id"]
                            ),
                            "source_start": limitation.get("source_start"),
                            "source_end": limitation.get("source_end"),
                            "is_inherited": bool(limitation.get("is_inherited", False)),
                            "limitation_snapshot": _json(snapshot),
                            "snapshot_version": str(
                                limitation.get("snapshot_version") or snapshot_version
                            ),
                        },
                    )
                )
                assert row is not None
                inserted.append(row)
            self._insert_event(
                connection,
                investigation_id=claim["investigation_id"],
                claim_investigation_id=claim_uuid,
                event_type="claim.limitations_inserted",
                actor=actor,
                payload={
                    "count": len(inserted),
                    "feature_keys": [item["feature_key"] for item in inserted],
                    "snapshot_version": snapshot_version,
                },
            )
            return inserted

    def list_claim_limitations(
        self, claim_investigation_id: uuid.UUID | str
    ) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.claim_limitations
                    WHERE claim_investigation_id = :claim_id
                    ORDER BY sequence_no ASC, feature_key ASC
                    """
                ),
                {"claim_id": _uuid(claim_investigation_id)},
            ).mappings()
            return [dict(row) for row in rows]

    def create_iteration(
        self,
        *,
        claim_investigation_id: uuid.UUID | str,
        iteration_no: int,
        purpose: str,
        trigger_reason: str | None = None,
        gap_feature_ids: Sequence[str] = (),
        parent_iteration_id: uuid.UUID | str | None = None,
        iteration_id: uuid.UUID | str | None = None,
        status: str = "planned",
    ) -> dict[str, Any]:
        claim_uuid = _uuid(claim_investigation_id)
        parent_uuid = _uuid(parent_iteration_id) if parent_iteration_id else None
        with self.transaction() as connection:
            if parent_uuid is not None:
                parent_owned = connection.execute(
                    text(
                        f"""
                        SELECT 1 FROM {self.schema}.iterations
                        WHERE id = :parent_id
                          AND claim_investigation_id = :claim_id
                        FOR UPDATE
                        """
                    ),
                    {"parent_id": parent_uuid, "claim_id": claim_uuid},
                ).first()
                if parent_owned is None:
                    raise RepositoryConflictError(
                        "parent iteration 不属于指定 claim"
                    )
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.iterations (
                            id, claim_investigation_id, parent_iteration_id,
                            iteration_no, purpose, trigger_reason, status,
                            gap_feature_ids, started_at
                        ) VALUES (
                            :id, :claim_investigation_id, :parent_iteration_id,
                            :iteration_no, :purpose, :trigger_reason, :status,
                            CAST(:gap_feature_ids AS JSONB),
                            CASE WHEN :status = 'running' THEN now() ELSE NULL END
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": _uuid(iteration_id),
                        "claim_investigation_id": claim_uuid,
                        "parent_iteration_id": parent_uuid,
                        "iteration_no": iteration_no,
                        "purpose": purpose,
                        "trigger_reason": trigger_reason,
                        "status": status,
                        "gap_feature_ids": _json(list(gap_feature_ids)),
                    },
                )
            )
        assert row is not None
        return row

    def get_claim_iteration(
        self,
        claim_investigation_id: uuid.UUID | str,
        iteration_no: int,
    ) -> dict[str, Any] | None:
        if iteration_no < 1:
            raise ValueError("iteration_no 必须大于 0")
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.iterations
                        WHERE claim_investigation_id = :claim_id
                          AND iteration_no = :iteration_no
                        """
                    ),
                    {
                        "claim_id": _uuid(claim_investigation_id),
                        "iteration_no": iteration_no,
                    },
                )
            )

    def update_iteration(
        self,
        iteration_id: uuid.UUID | str,
        *,
        status: str,
        metrics: Mapping[str, Any] | None = None,
        stop_reason: str | None = None,
        anchor_document_id: uuid.UUID | str | None = None,
        progress_signature: str | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        """Close or advance one round without leaving stale ``running`` rows."""

        if status not in {"running", "succeeded", "partial", "failed", "cancelled"}:
            raise ValueError(f"不支持的 iteration status: {status}")
        metric_payload = dict(metrics or {})
        signature = progress_signature
        if not signature and metric_payload:
            signature = hashlib.sha256(_json(metric_payload).encode("utf-8")).hexdigest()
        iteration_uuid = _uuid(iteration_id)
        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.iterations
                        SET status = :status,
                            metrics = metrics || CAST(:metrics AS JSONB),
                            stop_reason = :stop_reason,
                            anchor_document_id = COALESCE(
                                :anchor_document_id, anchor_document_id
                            ),
                            progress_signature = COALESCE(
                                :progress_signature, progress_signature
                            ),
                            started_at = COALESCE(started_at, now()),
                            completed_at = CASE
                                WHEN :status = 'running' THEN NULL ELSE now()
                            END
                        WHERE id = :id
                        RETURNING *
                        """
                    ),
                    {
                        "id": iteration_uuid,
                        "status": status,
                        "metrics": _json(metric_payload),
                        "stop_reason": stop_reason,
                        "anchor_document_id": (
                            _uuid(anchor_document_id) if anchor_document_id else None
                        ),
                        "progress_signature": signature,
                    },
                )
            )
            if row is None:
                raise RepositoryConflictError("iteration 不存在")
            owner = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT investigation_id
                        FROM {self.schema}.claim_investigations
                        WHERE id = :claim_id
                        """
                    ),
                    {"claim_id": row["claim_investigation_id"]},
                )
            )
            assert owner is not None
            self._insert_event(
                connection,
                investigation_id=owner["investigation_id"],
                claim_investigation_id=row["claim_investigation_id"],
                iteration_id=row["id"],
                event_type="iteration.status_changed",
                actor=actor,
                payload={
                    "status": status,
                    "stop_reason": stop_reason,
                    "progress_signature": signature,
                },
            )
            return row

    def create_module_run(
        self,
        *,
        investigation_id: uuid.UUID | str,
        module_code: str,
        contract_version: str,
        input_snapshot: Mapping[str, Any],
        idempotency_key: str,
        claim_investigation_id: uuid.UUID | str | None = None,
        iteration_id: uuid.UUID | str | None = None,
        module_run_id: uuid.UUID | str | None = None,
        status: str = "created",
        model_version: str | None = None,
        prompt_version: str | None = None,
        rule_version: str | None = None,
        requested_mode: str | None = None,
        effective_mode: str | None = None,
        actual_provider: str | None = None,
        network_used: bool | None = None,
        used_target_images: int | None = None,
        used_document_images: int | None = None,
        retry_of_module_run_id: uuid.UUID | str | None = None,
        retry_reason: str | None = None,
    ) -> dict[str, Any]:
        snapshot = _json(input_snapshot)
        audit = module_run_audit_values(input_snapshot)
        requested_mode = _mode_value(requested_mode) or audit["requested_mode"]
        effective_mode = _mode_value(effective_mode)
        investigation_uuid = _uuid(investigation_id)
        claim_uuid = _uuid(claim_investigation_id) if claim_investigation_id else None
        iteration_uuid = _uuid(iteration_id) if iteration_id else None
        if iteration_uuid is not None and claim_uuid is None:
            raise ValueError("设置 iteration_id 时必须同时设置 claim_investigation_id")
        with self.transaction() as connection:
            predicates = ["investigation.id = :investigation_id"]
            parameters: dict[str, Any] = {
                "investigation_id": investigation_uuid,
                "claim_id": claim_uuid,
                "iteration_id": iteration_uuid,
            }
            if claim_uuid is not None:
                predicates.append(
                    f"""EXISTS (
                        SELECT 1 FROM {self.schema}.claim_investigations AS claim
                        WHERE claim.id = :claim_id
                          AND claim.investigation_id = investigation.id
                    )"""
                )
            if iteration_uuid is not None:
                predicates.append(
                    f"""EXISTS (
                        SELECT 1 FROM {self.schema}.iterations AS iteration
                        WHERE iteration.id = :iteration_id
                          AND iteration.claim_investigation_id = :claim_id
                    )"""
                )
            owner = connection.execute(
                text(
                    f"""
                    SELECT 1 FROM {self.schema}.investigations AS investigation
                    WHERE {' AND '.join(predicates)}
                    FOR UPDATE
                    """
                ),
                parameters,
            ).first()
            if owner is None:
                raise RepositoryConflictError(
                    "investigation、claim 与 iteration 归属不一致"
                )
            row = self._insert_module_run(
                connection,
                module_run_id=_uuid(module_run_id),
                investigation_id=investigation_uuid,
                claim_investigation_id=claim_uuid,
                iteration_id=iteration_uuid,
                module_code=module_code,
                contract_version=contract_version,
                input_snapshot=snapshot,
                input_sha256=hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
                idempotency_key=idempotency_key,
                status=status,
                model_version=model_version,
                prompt_version=prompt_version,
                rule_version=rule_version,
                requested_mode=requested_mode,
                effective_mode=effective_mode,
                actual_provider=(str(actual_provider).strip() or None if actual_provider is not None else None),
                network_used=network_used,
                used_target_images=used_target_images,
                used_document_images=used_document_images,
                retry_of_module_run_id=(
                    _uuid(retry_of_module_run_id)
                    if retry_of_module_run_id is not None
                    else None
                ),
                retry_reason=retry_reason,
            )
        return row

    def finish_module_run(
        self,
        module_run_id: uuid.UUID | str,
        *,
        status: str,
        output_snapshot: Mapping[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool | None = None,
        model_version: str | None = None,
        requested_mode: str | None = None,
        effective_mode: str | None = None,
        actual_provider: str | None = None,
        network_used: bool | None = None,
        used_target_images: int | None = None,
        used_document_images: int | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        """Persist a synchronous module's terminal output and audit event."""

        if status not in {"succeeded", "partial", "failed", "cancelled"}:
            raise ValueError(f"不支持的 module_run status: {status}")
        serialized_output = _json(output_snapshot)
        audit = module_run_audit_values(None, output_snapshot)
        effective_mode = _mode_value(effective_mode) or audit["effective_mode"]
        actual_provider = (
            str(actual_provider).strip() or None
            if actual_provider is not None
            else audit["actual_provider"]
        )
        model_version = (
            str(model_version).strip() or None
            if model_version is not None
            else audit["model_version"]
        )
        network_used = audit["network_used"] if network_used is None else bool(network_used)
        used_target_images = (
            audit["used_target_images"]
            if used_target_images is None
            else max(0, int(used_target_images))
        )
        used_document_images = (
            audit["used_document_images"]
            if used_document_images is None
            else max(0, int(used_document_images))
        )
        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.module_runs
                        SET status = :status,
                            output_snapshot = CAST(:output_snapshot AS JSONB),
                            output_sha256 = :output_sha256,
                            requested_mode = COALESCE(
                                :requested_mode, requested_mode
                            ),
                            effective_mode = COALESCE(
                                :effective_mode, effective_mode
                            ),
                            actual_provider = COALESCE(
                                :actual_provider, actual_provider
                            ),
                            model_version = COALESCE(
                                :model_version, model_version
                            ),
                            network_used = COALESCE(:network_used, network_used),
                            used_target_images = COALESCE(
                                :used_target_images, used_target_images
                            ),
                            used_document_images = COALESCE(
                                :used_document_images, used_document_images
                            ),
                            error_code = :error_code,
                            error_message = :error_message,
                            retryable = :retryable,
                            started_at = COALESCE(started_at, created_at),
                            completed_at = now(),
                            updated_at = now()
                        WHERE id = :id
                          AND status NOT IN ('cancellation_requested', 'cancelled')
                        RETURNING *
                        """
                    ),
                    {
                        "id": _uuid(module_run_id),
                        "status": status,
                        "output_snapshot": serialized_output,
                        "output_sha256": _sha256_json(output_snapshot),
                        "requested_mode": _mode_value(requested_mode),
                        "effective_mode": effective_mode,
                        "actual_provider": actual_provider,
                        "model_version": model_version,
                        "network_used": network_used,
                        "used_target_images": used_target_images,
                        "used_document_images": used_document_images,
                        "error_code": error_code,
                        "error_message": error_message,
                        "retryable": retryable,
                    },
                )
            )
            if row is None:
                raise RepositoryConflictError("module run 不存在")
            self._insert_event(
                connection,
                investigation_id=row["investigation_id"],
                claim_investigation_id=row["claim_investigation_id"],
                iteration_id=row["iteration_id"],
                module_run_id=row["id"],
                event_type=f"module_run.{status}",
                actor=actor,
                payload={"status": status, "error_code": error_code},
            )
            return row

    def get_module_run(self, module_run_id: uuid.UUID | str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"SELECT * FROM {self.schema}.module_runs WHERE id = :id"
                    ),
                    {"id": _uuid(module_run_id)},
                )
            )

    def get_module_run_by_job(self, job_id: uuid.UUID | str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT run.*
                        FROM {self.schema}.module_runs AS run
                        JOIN {self.schema}.jobs AS job
                          ON job.module_run_id = run.id
                        WHERE job.id = :job_id
                        """
                    ),
                    {"job_id": _uuid(job_id)},
                )
            )

    def list_module_runs(
        self,
        *,
        investigation_id: uuid.UUID | str | None = None,
        module_code: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Bounded query used by the authenticated module-lab UI."""

        if not 1 <= limit <= 1_000:
            raise ValueError("limit 必须在 1..1000 范围内")
        predicates: list[str] = []
        parameters: dict[str, Any] = {"limit": limit}
        if investigation_id is not None:
            predicates.append("investigation_id = :investigation_id")
            parameters["investigation_id"] = _uuid(investigation_id)
        if module_code is not None:
            predicates.append("module_code = :module_code")
            parameters["module_code"] = module_code
        if status is not None:
            predicates.append("status = :status")
            parameters["status"] = status
        where = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.module_runs
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                parameters,
            ).mappings()
            return [dict(row) for row in rows]

    def find_reusable_patent_fetches(
        self,
        *,
        publication_number: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Find exact-publication live I3-FETCH outputs across investigations.

        This is an internal cache index for public patent source bytes.  It
        deliberately does not accept title, family or base-number matches, and
        it does not return I4-S/date conclusions for reuse.
        """

        normalized = re.sub(
            r"[^A-Z0-9]", "", str(publication_number or "").upper()
        )
        if not re.fullmatch(r"[A-Z]{2,3}[A-Z0-9]*\d[A-Z]\d?", normalized):
            raise ValueError("publication_number 必须包含完整公开号和文献种类号")
        if not 1 <= int(limit) <= 100:
            raise ValueError("limit 必须在 1..100 范围内")
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT *
                    FROM {self.schema}.module_runs
                    WHERE module_code = 'I3_FETCH'
                      AND status = 'succeeded'
                      AND COALESCE(
                            effective_mode,
                            NULLIF(
                                output_snapshot #>> '{{output,effective_mode}}',
                                ''
                            )
                          ) = 'live'
                      AND UPPER(
                            REGEXP_REPLACE(
                                COALESCE(
                                    output_snapshot #>> '{{output,publication_number}}',
                                    ''
                                ),
                                '[^A-Za-z0-9]', '', 'g'
                            )
                          ) = :publication_number
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                    """
                ),
                {
                    "publication_number": normalized,
                    "limit": int(limit),
                },
            ).mappings()
            return [dict(row) for row in rows]

    def get_module_lab_run(
        self, module_run_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        run = self.get_module_run(module_run_id)
        if run is None:
            return None
        with self.engine.connect() as connection:
            job = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.jobs
                        WHERE module_run_id = :module_run_id
                        """
                    ),
                    {"module_run_id": run["id"]},
                )
            )
            events = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.events
                        WHERE module_run_id = :module_run_id
                        ORDER BY id ASC
                        """
                    ),
                    {"module_run_id": run["id"]},
                ).mappings()
            ]
        return {"module_run": run, "job": job, "events": events}

    def _finalize_i0_terminal_failure(
        self,
        connection: Connection,
        *,
        module_run: Mapping[str, Any],
        error_code: str,
        error_message: str,
        actor: str,
        job_id: Any | None,
    ) -> dict[str, Any] | None:
        """Close every open I0 round row in the same failure transaction.

        This is deliberately safe to call for an Investigation that is already
        ``failed``.  That property lets startup reconciliation repair records
        written by the older two-transaction worker without incrementing the
        Investigation state version again or erasing any completed history.
        """

        if not str(module_run.get("module_code") or "").startswith("I0"):
            return None
        investigation_id = _uuid(module_run["investigation_id"])
        investigation = self._row(
            connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.investigations
                    WHERE id = :investigation_id
                    FOR UPDATE
                    """
                ),
                {"investigation_id": investigation_id},
            )
        )
        if investigation is None:
            raise RepositoryConflictError("I0 failure 对应 investigation 不存在")
        if str(investigation["status"]) in {"completed", "cancelled"}:
            return {
                "investigation": investigation,
                "transitioned": False,
                "descendant_counts": {},
            }

        failure_metadata = {
            "code": error_code,
            "module_run_id": str(module_run["id"]),
            "job_id": str(job_id) if job_id is not None else None,
            "retryable": False,
        }
        transitioned = str(investigation["status"]) != "failed"
        if transitioned:
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.investigations
                        SET status = 'failed',
                            error_message = :error_message,
                            error_metadata = error_metadata
                                || CAST(:error_metadata AS JSONB),
                            completed_at = COALESCE(completed_at, now()),
                            state_version = state_version + 1,
                            updated_at = now()
                        WHERE id = :investigation_id
                        RETURNING *
                        """
                    ),
                    {
                        "investigation_id": investigation_id,
                        "error_message": error_message,
                        "error_metadata": _json(failure_metadata),
                    },
                )
            )
            assert investigation is not None
        else:
            # Preserve the first terminal error but make old rows complete.
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.investigations
                        SET error_message = COALESCE(error_message, :error_message),
                            error_metadata = CASE
                                WHEN error_metadata = '{{}}'::jsonb
                                THEN CAST(:error_metadata AS JSONB)
                                ELSE error_metadata
                            END,
                            completed_at = COALESCE(completed_at, now()),
                            updated_at = now()
                        WHERE id = :investigation_id
                        RETURNING *
                        """
                    ),
                    {
                        "investigation_id": investigation_id,
                        "error_message": error_message,
                        "error_metadata": _json(failure_metadata),
                    },
                )
            )
            assert investigation is not None

        query_rows = list(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.queries AS query
                    SET status = 'failed',
                        executed_at = COALESCE(executed_at, now()),
                        provider_plan = provider_plan || CAST(:diagnostic AS JSONB)
                    FROM {self.schema}.iterations AS iteration,
                         {self.schema}.claim_investigations AS claim
                    WHERE query.iteration_id = iteration.id
                      AND iteration.claim_investigation_id = claim.id
                      AND claim.investigation_id = :investigation_id
                      AND query.status <> ALL(CAST(:terminal_statuses AS TEXT[]))
                    RETURNING query.id
                    """
                ),
                {
                    "investigation_id": investigation_id,
                    "terminal_statuses": sorted(_TERMINAL_QUERY_STATUSES),
                    "diagnostic": _json(
                        {
                            "execution_diagnostic": {
                                "reason": "upstream_i0_terminal_failure",
                                "error_code": error_code,
                            }
                        }
                    ),
                },
            ).mappings()
        )
        job_rows = list(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.jobs AS job
                    SET status = 'failed',
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL,
                        last_error = CAST(:last_error AS JSONB),
                        finished_at = COALESCE(finished_at, now()),
                        updated_at = now()
                    FROM {self.schema}.module_runs AS run
                    WHERE job.module_run_id = run.id
                      AND run.investigation_id = :investigation_id
                      AND run.iteration_id IS NOT NULL
                      AND job.status IN ('queued', 'leased')
                    RETURNING job.id
                    """
                ),
                {
                    "investigation_id": investigation_id,
                    "last_error": _json(
                        {
                            "code": "UPSTREAM_I0_TERMINATED",
                            "message": "I0 已最终失败，当前轮次作业同步收口",
                        }
                    ),
                },
            ).mappings()
        )
        module_rows = list(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.module_runs
                    SET status = 'failed',
                        error_code = COALESCE(
                            error_code, 'UPSTREAM_I0_TERMINATED'
                        ),
                        error_message = COALESCE(
                            error_message, 'I0 已最终失败，当前轮次模块同步收口'
                        ),
                        retryable = false,
                        completed_at = COALESCE(completed_at, now()),
                        updated_at = now()
                    WHERE investigation_id = :investigation_id
                      AND iteration_id IS NOT NULL
                      AND status <> ALL(CAST(:terminal_statuses AS TEXT[]))
                    RETURNING id
                    """
                ),
                {
                    "investigation_id": investigation_id,
                    "terminal_statuses": sorted(_TERMINAL_MODULE_RUN_STATUSES),
                },
            ).mappings()
        )
        iteration_rows = list(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.iterations AS iteration
                    SET status = 'failed', stop_reason = COALESCE(
                            iteration.stop_reason, :error_message
                        ),
                        completed_at = COALESCE(iteration.completed_at, now())
                    FROM {self.schema}.claim_investigations AS claim
                    WHERE iteration.claim_investigation_id = claim.id
                      AND claim.investigation_id = :investigation_id
                      AND iteration.status
                          <> ALL(CAST(:terminal_statuses AS TEXT[]))
                    RETURNING iteration.id
                    """
                ),
                {
                    "investigation_id": investigation_id,
                    "error_message": error_message,
                    "terminal_statuses": sorted(_TERMINAL_ITERATION_STATUSES),
                },
            ).mappings()
        )
        claim_rows = list(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.claim_investigations
                    SET status = 'failed',
                        terminal_reason = COALESCE(
                            terminal_reason, :error_message
                        ),
                        completed_at = COALESCE(completed_at, now()),
                        state_version = state_version + 1,
                        updated_at = now()
                    WHERE investigation_id = :investigation_id
                      AND status <> ALL(CAST(:terminal_statuses AS TEXT[]))
                    RETURNING id
                    """
                ),
                {
                    "investigation_id": investigation_id,
                    "error_message": error_message,
                    "terminal_statuses": sorted(_TERMINAL_CLAIM_STATUSES),
                },
            ).mappings()
        )
        descendant_counts = {
            "queries": len(query_rows),
            "jobs": len(job_rows),
            "module_runs": len(module_rows),
            "iterations": len(iteration_rows),
            "claims": len(claim_rows),
        }
        if transitioned or any(descendant_counts.values()):
            self._insert_event(
                connection,
                investigation_id=investigation_id,
                claim_investigation_id=module_run.get("claim_investigation_id"),
                iteration_id=module_run.get("iteration_id"),
                module_run_id=module_run["id"],
                event_type=(
                    "investigation.failed"
                    if transitioned
                    else "investigation.failure_descendants_finalized"
                ),
                actor=actor,
                payload={
                    "job_id": str(job_id) if job_id is not None else None,
                    "error_code": error_code,
                    "retryable": False,
                    "descendant_counts": descendant_counts,
                },
            )
        return {
            "investigation": investigation,
            "transitioned": transitioned,
            "descendant_counts": descendant_counts,
        }

    def _reconcile_terminal_i0_failures(
        self,
        connection: Connection,
        *,
        actor: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        investigations = [
            dict(row)
            for row in connection.execute(
                text(
                    f"""
                    SELECT investigation.*
                    FROM {self.schema}.investigations AS investigation
                    WHERE investigation.status = 'failed'
                      AND EXISTS (
                          SELECT 1 FROM {self.schema}.module_runs AS run
                          WHERE run.investigation_id = investigation.id
                            AND run.module_code LIKE 'I0%'
                            AND run.status = 'failed'
                      )
                      AND (
                          EXISTS (
                              SELECT 1
                              FROM {self.schema}.claim_investigations AS claim
                              WHERE claim.investigation_id = investigation.id
                                AND claim.status <> ALL(
                                    CAST(:claim_terminal AS TEXT[])
                                )
                          )
                          OR EXISTS (
                              SELECT 1 FROM {self.schema}.iterations AS iteration
                              JOIN {self.schema}.claim_investigations AS claim
                                ON claim.id = iteration.claim_investigation_id
                              WHERE claim.investigation_id = investigation.id
                                AND iteration.status <> ALL(
                                    CAST(:iteration_terminal AS TEXT[])
                                )
                          )
                          OR EXISTS (
                              SELECT 1 FROM {self.schema}.queries AS query
                              JOIN {self.schema}.iterations AS iteration
                                ON iteration.id = query.iteration_id
                              JOIN {self.schema}.claim_investigations AS claim
                                ON claim.id = iteration.claim_investigation_id
                              WHERE claim.investigation_id = investigation.id
                                AND query.status <> ALL(
                                    CAST(:query_terminal AS TEXT[])
                                )
                          )
                      )
                    ORDER BY investigation.updated_at, investigation.id
                    FOR UPDATE OF investigation SKIP LOCKED
                    LIMIT :limit
                    """
                ),
                {
                    "claim_terminal": sorted(_TERMINAL_CLAIM_STATUSES),
                    "iteration_terminal": sorted(_TERMINAL_ITERATION_STATUSES),
                    "query_terminal": sorted(_TERMINAL_QUERY_STATUSES),
                    "limit": limit,
                },
            ).mappings()
        ]
        reconciled: list[dict[str, Any]] = []
        for investigation in investigations:
            module_run = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.module_runs
                        WHERE investigation_id = :investigation_id
                          AND module_code LIKE 'I0%'
                          AND status = 'failed'
                        ORDER BY completed_at DESC NULLS LAST,
                                 created_at DESC, id DESC
                        LIMIT 1
                        """
                    ),
                    {"investigation_id": investigation["id"]},
                )
            )
            if module_run is None:
                continue
            failure = self._finalize_i0_terminal_failure(
                connection,
                module_run=module_run,
                error_code=str(
                    module_run.get("error_code") or "I0_TERMINAL_FAILURE"
                ),
                error_message=str(
                    module_run.get("error_message")
                    or investigation.get("error_message")
                    or "I0 工作流最终失败"
                ),
                actor=actor,
                job_id=None,
            )
            if failure is not None:
                reconciled.append(failure)
        return reconciled

    def reconcile_terminal_i0_failures(
        self,
        *,
        actor: str = "i0-failure-reconciler",
        limit: int = 1_000,
    ) -> list[dict[str, Any]]:
        """Safely close stale descendants of already-failed I0 runs."""

        if not actor.strip():
            raise ValueError("actor 不能为空")
        if not 1 <= limit <= 10_000:
            raise ValueError("limit 必须在 1..10000 范围内")
        with self.transaction() as connection:
            return self._reconcile_terminal_i0_failures(
                connection,
                actor=actor,
                limit=limit,
            )

    def _mark_i0_investigation_cancelled(
        self,
        connection: Connection,
        *,
        module_run: Mapping[str, Any],
        actor: str,
        job_id: Any | None,
    ) -> dict[str, Any] | None:
        if not str(module_run.get("module_code") or "").startswith("I0"):
            return None
        investigation = self._row(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.investigations
                    SET status = 'cancelled',
                        error_message = :message,
                        error_metadata = CAST(:metadata AS JSONB),
                        completed_at = COALESCE(completed_at, now()),
                        state_version = state_version + 1,
                        updated_at = now()
                    WHERE id = :investigation_id
                      AND status NOT IN ('completed', 'failed', 'cancelled')
                    RETURNING *
                    """
                ),
                {
                    "investigation_id": module_run["investigation_id"],
                    "message": _CANCELLED_ERROR_MESSAGE,
                    "metadata": _json(
                        {
                            "code": _CANCELLED_ERROR_CODE,
                            "module_run_id": str(module_run["id"]),
                            "job_id": str(job_id) if job_id is not None else None,
                        }
                    ),
                },
            )
        )
        if investigation is None:
            return None
        connection.execute(
            text(
                f"""
                UPDATE {self.schema}.claim_investigations
                SET status = 'cancelled', terminal_reason = :reason,
                    completed_at = COALESCE(completed_at, now()),
                    state_version = state_version + 1, updated_at = now()
                WHERE investigation_id = :investigation_id
                  AND status NOT IN (
                      'novelty_evidence_complete',
                      'inventive_step_evidence_complete',
                      'failed', 'cancelled'
                  )
                """
            ),
            {
                "investigation_id": module_run["investigation_id"],
                "reason": _CANCELLED_ERROR_MESSAGE,
            },
        )
        connection.execute(
            text(
                f"""
                UPDATE {self.schema}.iterations AS iteration
                SET status = 'cancelled', stop_reason = :reason,
                    completed_at = COALESCE(iteration.completed_at, now())
                FROM {self.schema}.claim_investigations AS claim
                WHERE iteration.claim_investigation_id = claim.id
                  AND claim.investigation_id = :investigation_id
                  AND iteration.status NOT IN ('completed', 'failed', 'cancelled')
                """
            ),
            {
                "investigation_id": module_run["investigation_id"],
                "reason": _CANCELLED_ERROR_MESSAGE,
            },
        )
        self._insert_event(
            connection,
            investigation_id=module_run["investigation_id"],
            claim_investigation_id=module_run.get("claim_investigation_id"),
            iteration_id=module_run.get("iteration_id"),
            module_run_id=module_run["id"],
            event_type="investigation.cancelled",
            actor=actor,
            payload={
                "job_id": str(job_id) if job_id is not None else None,
                "reason_code": _CANCELLED_ERROR_CODE,
            },
        )
        return investigation

    def _cancel_locked_job(
        self,
        connection: Connection,
        *,
        locked_job: Mapping[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        reason = str(locked_job.get("cancel_reason") or _CANCELLED_ERROR_MESSAGE)
        job = self._row(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.jobs
                    SET status = 'cancelled',
                        cancel_requested_at = COALESCE(cancel_requested_at, now()),
                        cancel_reason = COALESCE(cancel_reason, :reason),
                        cancelled_at = COALESCE(cancelled_at, now()),
                        finished_at = COALESCE(finished_at, now()),
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL,
                        last_error = CAST(:last_error AS JSONB),
                        updated_at = now()
                    WHERE id = :job_id
                      AND status IN ('queued', 'leased')
                    RETURNING *
                    """
                ),
                {
                    "job_id": locked_job["id"],
                    "reason": reason,
                    "last_error": _json(
                        {"code": _CANCELLED_ERROR_CODE, "message": reason}
                    ),
                },
            )
        )
        if job is None:
            raise RepositoryConflictError("作业已进入其他终态，无法完成取消")
        module_run = self._row(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.module_runs
                    SET status = 'cancelled',
                        cancel_requested_at = COALESCE(cancel_requested_at, now()),
                        cancelled_at = COALESCE(cancelled_at, now()),
                        error_code = :error_code,
                        error_message = :error_message,
                        retryable = false,
                        completed_at = COALESCE(completed_at, now()),
                        updated_at = now()
                    WHERE id = :module_run_id
                    RETURNING *
                    """
                ),
                {
                    "module_run_id": job["module_run_id"],
                    "error_code": _CANCELLED_ERROR_CODE,
                    "error_message": reason,
                },
            )
        )
        if module_run is None:
            raise RepositoryConflictError("取消作业时找不到对应 module run")
        self._mark_human_review_action_terminal(
            connection,
            module_run=module_run,
            status="cancelled",
            error_code=_CANCELLED_ERROR_CODE,
            error_message=reason,
        )
        self._insert_event(
            connection,
            investigation_id=module_run["investigation_id"],
            claim_investigation_id=module_run["claim_investigation_id"],
            iteration_id=module_run["iteration_id"],
            module_run_id=module_run["id"],
            event_type="job.cancelled",
            actor=actor,
            payload={
                "job_id": str(job["id"]),
                "attempt": job["attempt_count"],
                "reason_code": _CANCELLED_ERROR_CODE,
                "reason": reason,
            },
        )
        investigation = self._mark_i0_investigation_cancelled(
            connection,
            module_run=module_run,
            actor=actor,
            job_id=job["id"],
        )
        return {
            "job": job,
            "module_run": module_run,
            "investigation": investigation,
            "cancelled": True,
            "will_retry": False,
        }

    def _mark_human_review_action_terminal(
        self,
        connection: Connection,
        *,
        module_run: Mapping[str, Any],
        status: str,
        error_code: str,
        error_message: str,
    ) -> None:
        if str(module_run.get("module_code")) != "HUMAN_REVIEW_APPLY":
            return
        action = self._row(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.human_review_actions
                    SET status = :status,
                        error_summary = CAST(:error AS JSONB),
                        completed_at = COALESCE(completed_at, now())
                    WHERE module_run_id = :module_run_id
                      AND status IN ('queued', 'running')
                    RETURNING *
                    """
                ),
                {
                    "module_run_id": module_run["id"],
                    "status": status,
                    "error": _json(
                        {"code": error_code, "message": error_message}
                    ),
                },
            )
        )
        if action is None:
            return
        connection.execute(
            text(
                f"""
                UPDATE {self.schema}.investigations
                SET status = 'needs_human_review',
                    review_recomputation_required = true,
                    completed_at = NULL, updated_at = now()
                WHERE id = :investigation_id
                """
            ),
            {"investigation_id": action["investigation_id"]},
        )
        connection.execute(
            text(
                f"""
                UPDATE {self.schema}.claim_investigations
                SET status = 'needs_human_review', completed_at = NULL,
                    updated_at = now()
                WHERE id = ANY(CAST(:claim_ids AS UUID[]))
                """
            ),
            {
                "claim_ids": list(
                    (action.get("resulting_claim_state_versions") or {}).keys()
                )
            },
        )

    def request_module_run_cancellation(
        self,
        module_run_id: uuid.UUID | str,
        *,
        reason: str,
        actor: str = "api",
    ) -> dict[str, Any]:
        """Persist cancellation; queued work is cancelled in this transaction."""

        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("取消原因不能为空")
        run_uuid = _uuid(module_run_id)
        with self.transaction() as connection:
            module_run = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.module_runs
                        WHERE id = :module_run_id
                        FOR UPDATE
                        """
                    ),
                    {"module_run_id": run_uuid},
                )
            )
            if module_run is None:
                raise RepositoryConflictError("module run 不存在")
            job = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.jobs
                        WHERE module_run_id = :module_run_id
                        FOR UPDATE
                        """
                    ),
                    {"module_run_id": run_uuid},
                )
            )
            if str(module_run["status"]) == "cancelled" or (
                job is not None and str(job["status"]) == "cancelled"
            ):
                return {
                    "module_run": module_run,
                    "job": job,
                    "investigation": None,
                    "cancelled": True,
                    "idempotent_replay": True,
                }
            if str(module_run["status"]) in {
                "succeeded", "partial", "failed"
            }:
                raise RepositoryConflictError(
                    "已进入终态的 module run 不能再请求取消"
                )

            if job is None:
                cancelled_run = self._row(
                    connection.execute(
                        text(
                            f"""
                            UPDATE {self.schema}.module_runs
                            SET status = 'cancelled',
                                cancel_requested_at = COALESCE(
                                    cancel_requested_at, now()
                                ),
                                cancelled_at = COALESCE(cancelled_at, now()),
                                error_code = :error_code,
                                error_message = :reason,
                                retryable = false,
                                completed_at = COALESCE(completed_at, now()),
                                updated_at = now()
                            WHERE id = :module_run_id
                            RETURNING *
                            """
                        ),
                        {
                            "module_run_id": run_uuid,
                            "error_code": _CANCELLED_ERROR_CODE,
                            "reason": normalized_reason,
                        },
                    )
                )
                assert cancelled_run is not None
                self._mark_human_review_action_terminal(
                    connection,
                    module_run=cancelled_run,
                    status="cancelled",
                    error_code=_CANCELLED_ERROR_CODE,
                    error_message=normalized_reason,
                )
                self._insert_event(
                    connection,
                    investigation_id=cancelled_run["investigation_id"],
                    claim_investigation_id=cancelled_run["claim_investigation_id"],
                    iteration_id=cancelled_run["iteration_id"],
                    module_run_id=cancelled_run["id"],
                    event_type="module_run.cancelled",
                    actor=actor,
                    payload={
                        "reason_code": _CANCELLED_ERROR_CODE,
                        "reason": normalized_reason,
                        "without_job": True,
                    },
                )
                investigation = self._mark_i0_investigation_cancelled(
                    connection,
                    module_run=cancelled_run,
                    actor=actor,
                    job_id=None,
                )
                return {
                    "module_run": cancelled_run,
                    "job": None,
                    "investigation": investigation,
                    "cancelled": True,
                    "idempotent_replay": False,
                }

            if str(job["status"]) not in {"queued", "leased"}:
                raise RepositoryConflictError("作业已进入终态，无法请求取消")
            already_requested = job.get("cancel_requested_at") is not None
            job = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.jobs
                        SET cancel_requested_at = COALESCE(
                                cancel_requested_at, now()
                            ),
                            cancel_reason = COALESCE(cancel_reason, :reason),
                            updated_at = now()
                        WHERE id = :job_id
                        RETURNING *
                        """
                    ),
                    {"job_id": job["id"], "reason": normalized_reason},
                )
            )
            assert job is not None
            if str(job["status"]) == "queued":
                result = self._cancel_locked_job(
                    connection,
                    locked_job=job,
                    actor=actor,
                )
                return {**result, "idempotent_replay": already_requested}

            module_run = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.module_runs
                        SET status = 'cancellation_requested',
                            cancel_requested_at = COALESCE(
                                cancel_requested_at, now()
                            ),
                            error_code = :error_code,
                            error_message = :reason,
                            retryable = false,
                            updated_at = now()
                        WHERE id = :module_run_id
                        RETURNING *
                        """
                    ),
                    {
                        "module_run_id": run_uuid,
                        "error_code": _CANCELLED_ERROR_CODE,
                        "reason": normalized_reason,
                    },
                )
            )
            assert module_run is not None
            if not already_requested:
                self._insert_event(
                    connection,
                    investigation_id=module_run["investigation_id"],
                    claim_investigation_id=module_run["claim_investigation_id"],
                    iteration_id=module_run["iteration_id"],
                    module_run_id=module_run["id"],
                    event_type="module_run.cancel_requested",
                    actor=actor,
                    payload={
                        "job_id": str(job["id"]),
                        "reason_code": _CANCELLED_ERROR_CODE,
                        "reason": normalized_reason,
                    },
                )
            return {
                "module_run": module_run,
                "job": job,
                "investigation": None,
                "cancelled": False,
                "idempotent_replay": already_requested,
            }

    def cancel_leased_job(
        self,
        *,
        job_id: uuid.UUID | str,
        worker_id: str,
        lease_token: uuid.UUID | str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            locked = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.jobs
                        WHERE id = :job_id
                          AND status = 'leased'
                          AND lease_owner = :worker_id
                          AND lease_token = :lease_token
                          AND lease_expires_at > now()
                          AND cancel_requested_at IS NOT NULL
                        FOR UPDATE
                        """
                    ),
                    {
                        "job_id": _uuid(job_id),
                        "worker_id": worker_id,
                        "lease_token": _uuid(lease_token),
                    },
                )
            )
            if locked is None:
                raise LeaseLostError(
                    "job 取消请求不存在，或租约已过期、已转移、token 不匹配"
                )
            return self._cancel_locked_job(
                connection,
                locked_job=locked,
                actor=actor or worker_id,
            )

    def retry_module_run(
        self,
        module_run_id: uuid.UUID | str,
        *,
        idempotency_key: str,
        reason: str,
        actor: str = "api",
    ) -> dict[str, Any]:
        """Create a new run/job for a failed or incomplete-review run."""

        normalized_reason = reason.strip()
        if not normalized_reason or not idempotency_key.strip():
            raise ValueError("人工重试必须提供原因和幂等键")
        original_uuid = _uuid(module_run_id)
        with self.transaction() as connection:
            original = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.module_runs
                        WHERE id = :module_run_id
                        FOR UPDATE
                        """
                    ),
                    {"module_run_id": original_uuid},
                )
            )
            if original is None:
                raise RepositoryConflictError("module run 不存在")
            original_job = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.jobs
                        WHERE module_run_id = :module_run_id
                        FOR UPDATE
                        """
                    ),
                    {"module_run_id": original_uuid},
                )
            )
            if original_job is None:
                raise RepositoryConflictError("失败 module run 缺少持久 job")

            existing = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.module_runs
                        WHERE investigation_id = :investigation_id
                          AND module_code = :module_code
                          AND idempotency_key = :idempotency_key
                        FOR UPDATE
                        """
                    ),
                    {
                        "investigation_id": original["investigation_id"],
                        "module_code": original["module_code"],
                        "idempotency_key": idempotency_key,
                    },
                )
            )
            if existing is not None:
                if (
                    existing.get("retry_of_module_run_id") != original_uuid
                    or str(existing.get("retry_reason") or "") != normalized_reason
                ):
                    raise RepositoryConflictError(
                        "人工重试幂等键已由不同请求占用"
                    )
                existing_job = self._row(
                    connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.jobs
                            WHERE module_run_id = :module_run_id
                            """
                        ),
                        {"module_run_id": existing["id"]},
                    )
                )
                if existing_job is None:
                    raise RepositoryConflictError("人工重试 run 缺少持久 job")
                return {
                    "original_module_run": original,
                    "module_run": existing,
                    "job": existing_job,
                    "idempotent_replay": True,
                }

            output_snapshot = original.get("output_snapshot")
            comparison_output = (
                output_snapshot.get("output")
                if isinstance(output_snapshot, Mapping)
                and isinstance(output_snapshot.get("output"), Mapping)
                else output_snapshot
            )
            incomplete_structural_review = bool(
                str(original.get("module_code") or "")
                == "I4_S_SINGLE_REFERENCE"
                and str(original.get("status") or "") == "succeeded"
                and str(original_job.get("status") or "") == "succeeded"
                and isinstance(comparison_output, Mapping)
                and str(
                    comparison_output.get("structural_review_status") or ""
                )
                == "model_error"
            )
            failed_run = bool(
                str(original.get("status") or "") == "failed"
                and str(original_job.get("status") or "") == "failed"
            )
            if not failed_run and not incomplete_structural_review:
                raise RepositoryConflictError(
                    "只有已失败或结构复核未完成的 I4-S run/job 才能人工重试"
                )
            active_retry = connection.execute(
                text(
                    f"""
                    SELECT 1 FROM {self.schema}.module_runs
                    WHERE retry_of_module_run_id = :module_run_id
                      AND status IN (
                          'created', 'queued', 'running',
                          'cancellation_requested'
                      )
                    LIMIT 1
                    """
                ),
                {"module_run_id": original_uuid},
            ).first()
            if active_retry is not None:
                raise RepositoryConflictError("该失败 run 已存在进行中的人工重试")

            serialized_input = _json(original["input_snapshot"])
            retried = self._insert_module_run(
                connection,
                module_run_id=_uuid(),
                investigation_id=original["investigation_id"],
                claim_investigation_id=original["claim_investigation_id"],
                iteration_id=original["iteration_id"],
                module_code=original["module_code"],
                contract_version=original["contract_version"],
                input_snapshot=serialized_input,
                input_sha256=original["input_sha256"],
                idempotency_key=idempotency_key,
                status="queued",
                model_version=original["model_version"],
                prompt_version=original["prompt_version"],
                rule_version=original["rule_version"],
                requested_mode=original.get("requested_mode"),
                retry_of_module_run_id=original_uuid,
                retry_reason=normalized_reason,
            )
            job = self._insert_job(
                connection,
                job_id=_uuid(),
                module_run_id=retried["id"],
                payload=original_job["payload"],
                priority=original_job["priority"],
                max_attempts=original_job["max_attempts"],
                available_at=None,
            )
            self._insert_event(
                connection,
                investigation_id=original["investigation_id"],
                claim_investigation_id=original["claim_investigation_id"],
                iteration_id=original["iteration_id"],
                module_run_id=original["id"],
                event_type="module_run.retry_requested",
                actor=actor,
                payload={
                    "retry_module_run_id": str(retried["id"]),
                    "retry_job_id": str(job["id"]),
                    "reason": normalized_reason,
                },
            )
            self._insert_event(
                connection,
                investigation_id=retried["investigation_id"],
                claim_investigation_id=retried["claim_investigation_id"],
                iteration_id=retried["iteration_id"],
                module_run_id=retried["id"],
                event_type="module_run.retry_queued",
                actor=actor,
                payload={
                    "retry_of_module_run_id": str(original["id"]),
                    "job_id": str(job["id"]),
                    "reason": normalized_reason,
                },
            )
            if str(retried["module_code"]).startswith("I0"):
                investigation = self._row(
                    connection.execute(
                        text(
                            f"""
                            UPDATE {self.schema}.investigations
                            SET status = 'queued', error_message = NULL,
                                error_metadata = '{{}}'::jsonb,
                                completed_at = NULL,
                                state_version = state_version + 1,
                                updated_at = now()
                            WHERE id = :investigation_id
                              AND status = 'failed'
                            RETURNING *
                            """
                        ),
                        {"investigation_id": retried["investigation_id"]},
                    )
                )
                if investigation is not None:
                    self._insert_event(
                        connection,
                        investigation_id=retried["investigation_id"],
                        module_run_id=retried["id"],
                        event_type="investigation.retry_queued",
                        actor=actor,
                        payload={
                            "retry_of_module_run_id": str(original["id"]),
                            "job_id": str(job["id"]),
                        },
                    )
            return {
                "original_module_run": original,
                "module_run": retried,
                "job": job,
                "idempotent_replay": False,
            }

    def _insert_module_run(
        self,
        connection: Connection,
        **values: Any,
    ) -> dict[str, Any]:
        if "requested_mode" not in values:
            raw_input = values.get("input_snapshot")
            try:
                parsed_input = (
                    json.loads(raw_input) if isinstance(raw_input, str) else raw_input
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed_input = None
            values["requested_mode"] = module_run_audit_values(
                parsed_input if isinstance(parsed_input, Mapping) else None
            )["requested_mode"]
        values.setdefault("effective_mode", None)
        values.setdefault("actual_provider", None)
        values.setdefault("network_used", None)
        values.setdefault("used_target_images", None)
        values.setdefault("used_document_images", None)
        values.setdefault("retry_of_module_run_id", None)
        values.setdefault("retry_reason", None)
        row = self._row(
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.schema}.module_runs (
                        id, investigation_id, claim_investigation_id, iteration_id,
                        module_code, contract_version, status, input_snapshot,
                        input_sha256, idempotency_key, model_version,
                        prompt_version, rule_version, requested_mode,
                        effective_mode, actual_provider, network_used,
                        used_target_images, used_document_images,
                        retry_of_module_run_id, retry_reason
                    ) VALUES (
                        :module_run_id, :investigation_id, :claim_investigation_id,
                        :iteration_id, :module_code, :contract_version, :status,
                        CAST(:input_snapshot AS JSONB), :input_sha256,
                        :idempotency_key, :model_version, :prompt_version,
                        :rule_version, :requested_mode,
                        :effective_mode, :actual_provider, :network_used,
                        :used_target_images, :used_document_images,
                        :retry_of_module_run_id, :retry_reason
                    )
                    RETURNING *
                    """
                ),
                values,
            )
        )
        assert row is not None
        return row

    def enqueue_job(
        self,
        *,
        module_run_id: uuid.UUID | str,
        payload: Mapping[str, Any],
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
        job_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            row = self._insert_job(
                connection,
                job_id=_uuid(job_id),
                module_run_id=_uuid(module_run_id),
                payload=payload,
                priority=priority,
                max_attempts=max_attempts,
                available_at=available_at,
            )
        return row

    def _insert_job(
        self,
        connection: Connection,
        *,
        job_id: uuid.UUID,
        module_run_id: uuid.UUID,
        payload: Mapping[str, Any],
        priority: int,
        max_attempts: int,
        available_at: datetime | None,
    ) -> dict[str, Any]:
        row = self._row(
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.schema}.jobs (
                        id, module_run_id, status, priority, payload,
                        available_at, max_attempts
                    ) VALUES (
                        :id, :module_run_id, 'queued', :priority,
                        CAST(:payload AS JSONB),
                        COALESCE(CAST(:available_at AS TIMESTAMPTZ), now()),
                        :max_attempts
                    )
                    RETURNING *
                    """
                ),
                {
                    "id": job_id,
                    "module_run_id": module_run_id,
                    "priority": priority,
                    "payload": _json(payload),
                    "available_at": available_at,
                    "max_attempts": max_attempts,
                },
            )
        )
        assert row is not None
        return row

    def upsert_query(
        self,
        *,
        iteration_id: uuid.UUID | str,
        query_type: str,
        channel: str,
        expression: str,
        date_filter: Mapping[str, Any],
        feature_ids: Sequence[str] = (),
        classifications: Sequence[str] = (),
        provider_plan: Mapping[str, Any] | None = None,
        parent_query_id: uuid.UUID | str | None = None,
        language: str | None = None,
        rationale: str | None = None,
        status: str = "planned",
        executed_at: datetime | None = None,
        query_id: uuid.UUID | str | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        query_fingerprint = {
            "iteration_id": str(iteration_id),
            "query_type": query_type,
            "channel": channel,
            "language": language,
            "expression": expression,
            "feature_ids": list(feature_ids),
            "classifications": list(classifications),
            "date_filter": date_filter,
        }
        iteration_uuid = _uuid(iteration_id)
        with self.transaction() as connection:
            owner = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT claim.investigation_id,
                               iteration.claim_investigation_id
                        FROM {self.schema}.iterations AS iteration
                        JOIN {self.schema}.claim_investigations AS claim
                          ON claim.id = iteration.claim_investigation_id
                        WHERE iteration.id = :iteration_id
                        FOR UPDATE OF iteration
                        """
                    ),
                    {"iteration_id": iteration_uuid},
                )
            )
            if owner is None:
                raise RepositoryConflictError("iteration 不存在")
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.queries (
                            id, iteration_id, parent_query_id, query_type, channel,
                            language, expression, feature_ids, classifications,
                            date_filter, provider_plan, rationale, query_sha256,
                            status, executed_at
                        ) VALUES (
                            :id, :iteration_id, :parent_query_id, :query_type,
                            :channel, :language, :expression,
                            CAST(:feature_ids AS JSONB),
                            CAST(:classifications AS JSONB),
                            CAST(:date_filter AS JSONB),
                            CAST(:provider_plan AS JSONB), :rationale,
                            :query_sha256, :status, :executed_at
                        )
                        ON CONFLICT (iteration_id, query_sha256) DO UPDATE SET
                            parent_query_id = EXCLUDED.parent_query_id,
                            provider_plan = EXCLUDED.provider_plan,
                            rationale = EXCLUDED.rationale,
                            status = EXCLUDED.status,
                            executed_at = COALESCE(
                                EXCLUDED.executed_at, {self.schema}.queries.executed_at
                            )
                        RETURNING *
                        """
                    ),
                    {
                        "id": _uuid(query_id),
                        "iteration_id": iteration_uuid,
                        "parent_query_id": (
                            _uuid(parent_query_id) if parent_query_id else None
                        ),
                        "query_type": query_type,
                        "channel": channel,
                        "language": language,
                        "expression": expression,
                        "feature_ids": _json(list(feature_ids)),
                        "classifications": _json(list(classifications)),
                        "date_filter": _json(date_filter),
                        "provider_plan": _json(provider_plan),
                        "rationale": rationale,
                        "query_sha256": _sha256_json(query_fingerprint),
                        "status": status,
                        "executed_at": executed_at,
                    },
                )
            )
            assert row is not None
            self._insert_event(
                connection,
                investigation_id=owner["investigation_id"],
                claim_investigation_id=owner["claim_investigation_id"],
                iteration_id=iteration_uuid,
                event_type="query.upserted",
                actor=actor,
                payload={"query_id": str(row["id"]), "status": row["status"]},
            )
            return row

    def update_query_execution(
        self,
        query_id: uuid.UUID | str,
        *,
        status: str,
        diagnostic: Mapping[str, Any] | None = None,
        executed_at: datetime | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        """Record the actual provider outcome for one planned query."""

        if status not in {"completed", "partial", "failed", "skipped"}:
            raise ValueError(f"不支持的 query execution status: {status}")
        diagnostic_payload = dict(diagnostic or {})
        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.queries
                        SET status = :status,
                            executed_at = COALESCE(:executed_at, now()),
                            provider_plan = provider_plan
                                || CAST(:diagnostic AS JSONB)
                        WHERE id = :id
                        RETURNING *
                        """
                    ),
                    {
                        "id": _uuid(query_id),
                        "status": status,
                        "executed_at": executed_at,
                        "diagnostic": _json(
                            {"execution_diagnostic": diagnostic_payload}
                        ),
                    },
                )
            )
            if row is None:
                raise RepositoryConflictError("query 不存在")
            owner = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT claim.investigation_id,
                               iteration.claim_investigation_id
                        FROM {self.schema}.iterations AS iteration
                        JOIN {self.schema}.claim_investigations AS claim
                          ON claim.id = iteration.claim_investigation_id
                        WHERE iteration.id = :iteration_id
                        """
                    ),
                    {"iteration_id": row["iteration_id"]},
                )
            )
            assert owner is not None
            self._insert_event(
                connection,
                investigation_id=owner["investigation_id"],
                claim_investigation_id=owner["claim_investigation_id"],
                iteration_id=row["iteration_id"],
                event_type="query.executed",
                actor=actor,
                payload={
                    "query_id": str(row["id"]),
                    "status": status,
                    "diagnostic": diagnostic_payload,
                },
            )
            return row

    def _upsert_document(
        self,
        connection: Connection,
        *,
        investigation_id: uuid.UUID,
        canonical_key: str,
        document_type: str,
        title: str,
        evidence_level: str,
        language: str | None,
        identifiers: Mapping[str, Any] | None,
        dates: Mapping[str, Any] | None,
        metadata: Mapping[str, Any] | None,
        family_key: str | None,
        content_sha256: str | None,
        document_id: uuid.UUID,
    ) -> dict[str, Any]:
        row = self._row(
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.schema}.documents (
                        id, investigation_id, canonical_key, document_type,
                        evidence_level, title, language, identifiers, dates,
                        metadata, family_key, content_sha256
                    ) VALUES (
                        :id, :investigation_id, :canonical_key, :document_type,
                        :evidence_level, :title, :language,
                        CAST(:identifiers AS JSONB), CAST(:dates AS JSONB),
                        CAST(:metadata AS JSONB), :family_key, :content_sha256
                    )
                    ON CONFLICT (investigation_id, canonical_key) DO UPDATE SET
                        document_type = EXCLUDED.document_type,
                        evidence_level = CASE
                            WHEN EXCLUDED.content_sha256 IS NOT NULL
                                 AND {self.schema}.documents.content_sha256 IS DISTINCT FROM
                                     EXCLUDED.content_sha256
                                THEN EXCLUDED.evidence_level
                            WHEN {self.schema}.documents.evidence_level = 'qualified_evidence'
                                THEN {self.schema}.documents.evidence_level
                            WHEN EXCLUDED.evidence_level = 'qualified_evidence'
                                THEN EXCLUDED.evidence_level
                            WHEN {self.schema}.documents.evidence_level = 'retrieved_document'
                                 AND EXCLUDED.evidence_level = 'lead'
                                THEN {self.schema}.documents.evidence_level
                            ELSE EXCLUDED.evidence_level
                        END,
                        title = EXCLUDED.title,
                        language = COALESCE(EXCLUDED.language, {self.schema}.documents.language),
                        identifiers = {self.schema}.documents.identifiers
                            || EXCLUDED.identifiers,
                        dates = {self.schema}.documents.dates || EXCLUDED.dates,
                        metadata = {self.schema}.documents.metadata || EXCLUDED.metadata,
                        family_key = COALESCE(
                            EXCLUDED.family_key, {self.schema}.documents.family_key
                        ),
                        content_sha256 = COALESCE(
                            EXCLUDED.content_sha256,
                            {self.schema}.documents.content_sha256
                        ),
                        updated_at = now()
                    RETURNING *
                    """
                ),
                {
                    "id": document_id,
                    "investigation_id": investigation_id,
                    "canonical_key": canonical_key,
                    "document_type": document_type,
                    "evidence_level": evidence_level,
                    "title": title,
                    "language": language,
                    "identifiers": _json(identifiers),
                    "dates": _json(dates),
                    "metadata": _json(metadata),
                    "family_key": family_key,
                    "content_sha256": content_sha256,
                },
            )
        )
        assert row is not None
        return row

    def _ensure_document_version(
        self,
        connection: Connection,
        *,
        document_id: uuid.UUID,
        content_sha256: str,
        content_artifact_id: uuid.UUID | None = None,
        mime_type: str | None = None,
        byte_size: int | None = None,
        acquisition_kind: str = "automatic_retrieval",
        version_metadata: Mapping[str, Any] | None = None,
        created_by: str = "orchestrator",
    ) -> dict[str, Any]:
        """Return/create the immutable bytes version for one document.

        The document row is only a mutable identity pointer.  A repeated SHA is
        an idempotent replay; a different SHA appends ``version_no + 1``.  No
        existing version row is updated, including when a later caller has a
        richer source record for the same bytes.
        """

        digest = str(content_sha256 or "").strip().lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("content_sha256 必须是 64 位十六进制 SHA-256")
        document = self._row(
            connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.documents
                    WHERE id = :document_id
                    FOR UPDATE
                    """
                ),
                {"document_id": document_id},
            )
        )
        if document is None:
            raise RepositoryConflictError("document 不存在")
        if content_artifact_id is not None:
            owned = connection.execute(
                text(
                    f"""
                    SELECT 1 FROM {self.schema}.artifacts
                    WHERE id = :artifact_id
                      AND investigation_id = :investigation_id
                    FOR UPDATE
                    """
                ),
                {
                    "artifact_id": content_artifact_id,
                    "investigation_id": document["investigation_id"],
                },
            ).first()
            if owned is None:
                raise RepositoryConflictError(
                    "document version artifact 不属于当前 investigation"
                )
        existing = self._row(
            connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.document_versions
                    WHERE document_id = :document_id
                      AND content_sha256 = :content_sha256
                    FOR UPDATE
                    """
                ),
                {"document_id": document_id, "content_sha256": digest},
            )
        )
        if existing is None:
            next_version = int(
                connection.execute(
                    text(
                        f"""
                        SELECT COALESCE(MAX(version_no), 0) + 1
                        FROM {self.schema}.document_versions
                        WHERE document_id = :document_id
                        """
                    ),
                    {"document_id": document_id},
                ).scalar_one()
            )
            existing = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.document_versions (
                            id, document_id, version_no, content_sha256,
                            content_artifact_id, mime_type, byte_size,
                            acquisition_kind, version_metadata, created_by
                        ) VALUES (
                            :id, :document_id, :version_no, :content_sha256,
                            :content_artifact_id, :mime_type, :byte_size,
                            :acquisition_kind, CAST(:version_metadata AS JSONB),
                            :created_by
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "document_id": document_id,
                        "version_no": next_version,
                        "content_sha256": digest,
                        "content_artifact_id": content_artifact_id,
                        "mime_type": mime_type,
                        "byte_size": byte_size,
                        "acquisition_kind": acquisition_kind,
                        "version_metadata": _json(version_metadata),
                        "created_by": created_by,
                    },
                )
            )
            assert existing is not None
        current_version = None
        if document.get("current_document_version_id"):
            current_version = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.document_versions
                        WHERE id = :id
                        """
                    ),
                    {"id": document["current_document_version_id"]},
                )
            )
        promoted = current_version is None or int(existing["version_no"]) >= int(
            current_version["version_no"]
        )
        connection.execute(
            text(
                f"""
                UPDATE {self.schema}.documents
                SET current_document_version_id = :current_document_version_id,
                    content_sha256 = :current_content_sha256,
                    updated_at = now()
                WHERE id = :document_id
                """
            ),
            {
                "document_id": document_id,
                "current_document_version_id": (
                    existing["id"] if promoted else current_version["id"]
                ),
                "current_content_sha256": (
                    digest if promoted else current_version["content_sha256"]
                ),
            },
        )
        return existing

    def upsert_document(
        self,
        *,
        investigation_id: uuid.UUID | str,
        canonical_key: str,
        document_type: str,
        title: str,
        evidence_level: str = "lead",
        language: str | None = None,
        identifiers: Mapping[str, Any] | None = None,
        dates: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        family_key: str | None = None,
        content_sha256: str | None = None,
        content_artifact_id: uuid.UUID | str | None = None,
        content_mime_type: str | None = None,
        content_byte_size: int | None = None,
        acquisition_kind: str = "automatic_retrieval",
        version_metadata: Mapping[str, Any] | None = None,
        document_id: uuid.UUID | str | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        investigation_uuid = _uuid(investigation_id)
        with self.transaction() as connection:
            row = self._upsert_document(
                connection,
                investigation_id=investigation_uuid,
                canonical_key=canonical_key,
                document_type=document_type,
                title=title,
                evidence_level=evidence_level,
                language=language,
                identifiers=identifiers,
                dates=dates,
                metadata=metadata,
                family_key=family_key,
                content_sha256=content_sha256,
                document_id=_uuid(document_id),
            )
            version = None
            if content_sha256:
                version = self._ensure_document_version(
                    connection,
                    document_id=row["id"],
                    content_sha256=content_sha256,
                    content_artifact_id=(
                        _uuid(content_artifact_id) if content_artifact_id else None
                    ),
                    mime_type=content_mime_type,
                    byte_size=content_byte_size,
                    acquisition_kind=acquisition_kind,
                    version_metadata=version_metadata,
                    created_by=actor,
                )
                current_document = self._row(
                    connection.execute(
                        text(
                            f"SELECT * FROM {self.schema}.documents WHERE id = :id"
                        ),
                        {"id": row["id"]},
                    )
                )
                assert current_document is not None
                row = {
                    **current_document,
                    "document_version_id": version["id"],
                    "document_version_no": version["version_no"],
                }
            self._insert_event(
                connection,
                investigation_id=investigation_uuid,
                event_type="document.upserted",
                actor=actor,
                payload={
                    "document_id": str(row["id"]),
                    "canonical_key": canonical_key,
                    "evidence_level": row["evidence_level"],
                    "document_version_id": (
                        str(version["id"]) if version is not None else None
                    ),
                },
            )
            return row

    def get_document_version(
        self,
        document_version_id: uuid.UUID | str,
    ) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT version.*, document.investigation_id
                        FROM {self.schema}.document_versions AS version
                        JOIN {self.schema}.documents AS document
                          ON document.id = version.document_id
                        WHERE version.id = :id
                        """
                    ),
                    {"id": _uuid(document_version_id)},
                )
            )

    def _insert_artifact_row(
        self,
        connection: Connection,
        *,
        investigation_id: uuid.UUID,
        artifact_type: str,
        uri: str,
        sha256: str,
        mime_type: str | None,
        byte_size: int | None,
        metadata: Mapping[str, Any] | None,
        artifact_id: uuid.UUID | None = None,
        module_run_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        row = self._row(
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.schema}.artifacts (
                        id, investigation_id, module_run_id, artifact_type,
                        uri, sha256, mime_type, byte_size, metadata
                    ) VALUES (
                        :id, :investigation_id, :module_run_id, :artifact_type,
                        :uri, :sha256, :mime_type, :byte_size,
                        CAST(:metadata AS JSONB)
                    ) RETURNING *
                    """
                ),
                {
                    "id": artifact_id or uuid.uuid4(),
                    "investigation_id": investigation_id,
                    "module_run_id": module_run_id,
                    "artifact_type": artifact_type,
                    "uri": uri,
                    "sha256": sha256,
                    "mime_type": mime_type,
                    "byte_size": byte_size,
                    "metadata": _json(metadata),
                },
            )
        )
        assert row is not None
        return row

    def _lock_human_review_command(
        self,
        connection: Connection,
        *,
        investigation_id: uuid.UUID,
        claim_investigation_ids: Sequence[uuid.UUID],
        expected_investigation_state_version: int,
        expected_claim_state_versions: Mapping[str, int],
        expected_review_revision: int,
        idempotency_key_sha256: str,
        request_sha256: str,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        dict[str, Any] | None,
    ]:
        investigation = self._row(
            connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.investigations
                    WHERE id = :investigation_id
                    FOR UPDATE
                    """
                ),
                {"investigation_id": investigation_id},
            )
        )
        if investigation is None:
            raise RepositoryConflictError("investigation 不存在")

        # Idempotency lookup intentionally precedes every CAS/quiescence check.
        existing = self._row(
            connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.human_review_actions
                    WHERE investigation_id = :investigation_id
                      AND idempotency_key_sha256 = :idempotency_key_sha256
                    FOR UPDATE
                    """
                ),
                {
                    "investigation_id": investigation_id,
                    "idempotency_key_sha256": idempotency_key_sha256,
                },
            )
        )
        if existing is not None:
            if str(existing["request_sha256"]) != request_sha256:
                raise RepositoryConflictError(
                    "人工复核幂等键已用于不同请求",
                    code="IDEMPOTENCY_KEY_REUSED",
                )
            response = existing.get("response_snapshot")
            return investigation, [], (
                dict(response) if isinstance(response, Mapping) else {}
            )

        normalized_ids = sorted(set(claim_investigation_ids), key=str)
        if len(normalized_ids) != len(claim_investigation_ids):
            raise ValueError("claim_investigation_ids 不能重复")
        claims = [
            dict(row)
            for row in connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.claim_investigations
                    WHERE investigation_id = :investigation_id
                      AND id = ANY(CAST(:claim_ids AS UUID[]))
                    ORDER BY id
                    FOR UPDATE
                    """
                ),
                {
                    "investigation_id": investigation_id,
                    "claim_ids": [str(item) for item in normalized_ids],
                },
            ).mappings()
        ]
        if {row["id"] for row in claims} != set(normalized_ids):
            raise RepositoryConflictError(
                "目标 claim 不存在或不属于 investigation"
            )
        if int(investigation["state_version"]) != int(
            expected_investigation_state_version
        ):
            raise RepositoryConflictError(
                "investigation state_version 已变化，请重新读取复核上下文"
            )
        if int(investigation.get("review_revision") or 0) != int(
            expected_review_revision
        ):
            raise RepositoryConflictError(
                "review_revision 已变化，请重新读取复核上下文"
            )
        expected_versions = {
            str(key): int(value)
            for key, value in expected_claim_state_versions.items()
        }
        if set(expected_versions) != {str(row["id"]) for row in claims}:
            raise RepositoryConflictError(
                "expected_claim_state_versions 未精确覆盖目标 claim"
            )
        stale_claims = [
            str(row["id"])
            for row in claims
            if int(row["state_version"])
            != expected_versions[str(row["id"])]
        ]
        if stale_claims:
            raise RepositoryConflictError(
                "claim state_version 已变化: " + ", ".join(stale_claims)
            )
        successful = [
            str(row["id"])
            for row in claims
            if str(row["status"]) in _SUCCESSFUL_CLAIM_STATUSES
        ]
        if successful:
            raise RepositoryConflictError(
                "成功终态 claim 不接受普通人工复核重开: "
                + ", ".join(successful),
                code="REVIEW_SUCCESS_TERMINAL",
            )

        active = connection.execute(
            text(
                f"""
                SELECT run.id
                FROM {self.schema}.module_runs AS run
                LEFT JOIN {self.schema}.jobs AS job
                  ON job.module_run_id = run.id
                WHERE run.investigation_id = :investigation_id
                  AND run.module_code IN (
                      'I0_ORCHESTRATE', 'I0_INVALIDITY_WORKFLOW',
                      'I5_REPORT', 'HUMAN_REVIEW_APPLY'
                  )
                  AND (
                      run.status IN ('created', 'queued', 'running', 'cancellation_requested')
                      OR job.status IN ('queued', 'leased')
                  )
                FOR UPDATE OF run
                """
            ),
            {"investigation_id": investigation_id},
        ).first()
        if active is not None:
            raise RepositoryConflictError(
                "调查仍有 I0/I5/人工复核作业未收口",
                code="REVIEW_NOT_QUIESCENT",
            )
        nonterminal_iteration = connection.execute(
            text(
                f"""
                SELECT latest.claim_investigation_id, latest.status
                FROM (
                    SELECT DISTINCT ON (iteration.claim_investigation_id)
                           iteration.claim_investigation_id, iteration.status,
                           iteration.iteration_no, iteration.created_at
                    FROM {self.schema}.iterations AS iteration
                    WHERE iteration.claim_investigation_id = ANY(CAST(:claim_ids AS UUID[]))
                    ORDER BY iteration.claim_investigation_id,
                             iteration.iteration_no DESC,
                             iteration.created_at DESC
                ) AS latest
                WHERE latest.status NOT IN ('succeeded', 'partial', 'failed', 'cancelled')
                """
            ),
            {"claim_ids": [str(item) for item in normalized_ids]},
        ).first()
        if nonterminal_iteration is not None:
            raise RepositoryConflictError(
                "目标 claim 最近 iteration 尚未进入终态",
                code="REVIEW_NOT_QUIESCENT",
            )
        return investigation, claims, None

    def _queue_human_review_action(
        self,
        connection: Connection,
        *,
        investigation: Mapping[str, Any],
        claims: Sequence[Mapping[str, Any]],
        action_id: uuid.UUID,
        action_type: str,
        target_snapshot: Mapping[str, Any],
        actor: str,
        reason: str,
        request_sha256: str,
        idempotency_key_sha256: str,
        invalidated_derivations: Sequence[str],
        pending_recomputation: Sequence[str],
        response_extra: Mapping[str, Any],
    ) -> dict[str, Any]:
        action_seq = int(
            connection.execute(
                text(
                    f"""
                    SELECT COALESCE(MAX(action_seq), 0) + 1
                    FROM {self.schema}.human_review_actions
                    WHERE investigation_id = :investigation_id
                    """
                ),
                {"investigation_id": investigation["id"]},
            ).scalar_one()
        )
        base_claim_versions = {
            str(row["id"]): int(row["state_version"]) for row in claims
        }
        resulting_claims: list[dict[str, Any]] = []
        for claim in sorted(claims, key=lambda row: str(row["id"])):
            updated = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.claim_investigations
                        SET status = 'needs_human_review',
                            completed_at = NULL,
                            state_version = state_version + 1,
                            review_revision = review_revision + 1,
                            updated_at = now()
                        WHERE id = :claim_id
                          AND state_version = :expected_state_version
                        RETURNING *
                        """
                    ),
                    {
                        "claim_id": claim["id"],
                        "expected_state_version": claim["state_version"],
                    },
                )
            )
            if updated is None:
                raise RepositoryConflictError("claim state_version 已变化")
            resulting_claims.append(updated)
        resulting_claim_versions = {
            str(row["id"]): int(row["state_version"])
            for row in resulting_claims
        }
        updated_investigation = self._row(
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.investigations
                    SET status = 'queued', completed_at = NULL,
                        state_version = state_version + 1,
                        review_revision = review_revision + 1,
                        review_recomputation_required = true,
                        updated_at = now()
                    WHERE id = :investigation_id
                      AND state_version = :expected_state_version
                      AND review_revision = :expected_review_revision
                    RETURNING *
                    """
                ),
                {
                    "investigation_id": investigation["id"],
                    "expected_state_version": investigation["state_version"],
                    "expected_review_revision": investigation.get("review_revision") or 0,
                },
            )
        )
        if updated_investigation is None:
            raise RepositoryConflictError("investigation state/review revision 已变化")

        module_run_id = uuid.uuid4()
        job_id = uuid.uuid4()
        input_snapshot = {
            "kind": "human_review_apply",
            "investigation_id": str(investigation["id"]),
            "review_action_id": str(action_id),
            "action_type": action_type,
            "action_seq": action_seq,
            "target_snapshot": dict(target_snapshot),
            "resulting_review_revision": int(
                updated_investigation["review_revision"]
            ),
        }
        serialized = _json(input_snapshot)
        module_run = self._insert_module_run(
            connection,
            module_run_id=module_run_id,
            investigation_id=investigation["id"],
            claim_investigation_id=None,
            iteration_id=None,
            module_code="HUMAN_REVIEW_APPLY",
            contract_version="v1",
            input_snapshot=serialized,
            input_sha256=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            idempotency_key=f"human-review:{idempotency_key_sha256}",
            status="queued",
            model_version=None,
            prompt_version=None,
            rule_version="human-review-v1",
        )
        job = self._insert_job(
            connection,
            job_id=job_id,
            module_run_id=module_run_id,
            payload=input_snapshot,
            priority=10,
            max_attempts=3,
            available_at=None,
        )
        response = {
            "contract_version": "v1",
            "review_action_id": str(action_id),
            "action_type": action_type,
            "status": "queued",
            "investigation_state_version": int(
                updated_investigation["state_version"]
            ),
            "claim_state_versions": resulting_claim_versions,
            "review_revision": int(updated_investigation["review_revision"]),
            "module_run_id": str(module_run["id"]),
            "job_id": str(job["id"]),
            "invalidated_derivations": list(invalidated_derivations),
            "pending_recomputation": list(pending_recomputation),
            "idempotent_replay": False,
            **dict(response_extra),
        }
        action = self._row(
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.schema}.human_review_actions (
                        id, investigation_id, action_type, action_seq,
                        target_snapshot, actor, reason, request_sha256,
                        idempotency_key_sha256,
                        base_investigation_state_version,
                        resulting_investigation_state_version,
                        base_claim_state_versions, resulting_claim_state_versions,
                        base_review_revision, resulting_review_revision,
                        status, module_run_id, job_id,
                        invalidated_derivations, pending_recomputation,
                        response_snapshot
                    ) VALUES (
                        :id, :investigation_id, :action_type, :action_seq,
                        CAST(:target_snapshot AS JSONB), :actor, :reason,
                        :request_sha256, :idempotency_key_sha256,
                        :base_investigation_state_version,
                        :resulting_investigation_state_version,
                        CAST(:base_claim_state_versions AS JSONB),
                        CAST(:resulting_claim_state_versions AS JSONB),
                        :base_review_revision, :resulting_review_revision,
                        'queued', :module_run_id, :job_id,
                        CAST(:invalidated_derivations AS JSONB),
                        CAST(:pending_recomputation AS JSONB),
                        CAST(:response_snapshot AS JSONB)
                    ) RETURNING *
                    """
                ),
                {
                    "id": action_id,
                    "investigation_id": investigation["id"],
                    "action_type": action_type,
                    "action_seq": action_seq,
                    "target_snapshot": _json(target_snapshot),
                    "actor": actor,
                    "reason": reason,
                    "request_sha256": request_sha256,
                    "idempotency_key_sha256": idempotency_key_sha256,
                    "base_investigation_state_version": investigation["state_version"],
                    "resulting_investigation_state_version": updated_investigation[
                        "state_version"
                    ],
                    "base_claim_state_versions": _json(base_claim_versions),
                    "resulting_claim_state_versions": _json(
                        resulting_claim_versions
                    ),
                    "base_review_revision": investigation.get("review_revision") or 0,
                    "resulting_review_revision": updated_investigation[
                        "review_revision"
                    ],
                    "module_run_id": module_run["id"],
                    "job_id": job["id"],
                    "invalidated_derivations": _json(
                        list(invalidated_derivations)
                    ),
                    "pending_recomputation": _json(list(pending_recomputation)),
                    "response_snapshot": _json(response),
                },
            )
        )
        assert action is not None
        self._insert_event(
            connection,
            investigation_id=investigation["id"],
            module_run_id=module_run["id"],
            event_type="human_review.queued",
            actor=actor,
            payload={
                "review_action_id": str(action_id),
                "action_type": action_type,
                "action_seq": action_seq,
                "claim_investigation_ids": [
                    str(row["id"]) for row in resulting_claims
                ],
                "review_revision": updated_investigation["review_revision"],
            },
        )
        return {
            "response": response,
            "action": action,
            "investigation": updated_investigation,
            "claims": resulting_claims,
            "module_run": module_run,
            "job": job,
            "idempotent_replay": False,
        }

    def create_evidence_import_and_enqueue(
        self,
        *,
        investigation_id: uuid.UUID | str,
        claim_investigation_ids: Sequence[uuid.UUID | str],
        expected_investigation_state_version: int,
        expected_claim_state_versions: Mapping[str, int],
        expected_review_revision: int,
        idempotency_key_sha256: str,
        request_sha256: str,
        actor: str,
        reason: str,
        canonical_key: str,
        document_type: str,
        title: str,
        language: str | None,
        publication_number: str | None,
        authority: str | None,
        declared_date_facts: Mapping[str, Any] | None,
        date_evidence: Mapping[str, Any] | None,
        frozen_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not actor.strip() or not reason.strip():
            raise ValueError("actor 和 reason 不能为空")
        investigation_uuid = _uuid(investigation_id)
        claim_ids = [_uuid(value) for value in claim_investigation_ids]
        with self.transaction() as connection:
            investigation, claims, replay = self._lock_human_review_command(
                connection,
                investigation_id=investigation_uuid,
                claim_investigation_ids=claim_ids,
                expected_investigation_state_version=expected_investigation_state_version,
                expected_claim_state_versions=expected_claim_state_versions,
                expected_review_revision=expected_review_revision,
                idempotency_key_sha256=idempotency_key_sha256,
                request_sha256=request_sha256,
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return {"response": replay, "idempotent_replay": True}
            artifact = self._insert_artifact_row(
                connection,
                investigation_id=investigation_uuid,
                artifact_type="human_imported_evidence",
                uri=str(frozen_evidence["uri"]),
                sha256=str(frozen_evidence["sha256"]),
                mime_type=str(frozen_evidence.get("mime_type") or "") or None,
                byte_size=(
                    int(frozen_evidence["byte_size"])
                    if frozen_evidence.get("byte_size") is not None
                    else None
                ),
                metadata={"provider": frozen_evidence.get("provider")},
            )
            rendered_images: list[dict[str, Any]] = []
            for image in frozen_evidence.get("rendered_images") or []:
                if not isinstance(image, Mapping):
                    continue
                image_row = self._insert_artifact_row(
                    connection,
                    investigation_id=investigation_uuid,
                    artifact_type=str(
                        image.get("artifact_type") or "rendered_patent_page"
                    ),
                    uri=str(image["uri"]),
                    sha256=str(image["sha256"]),
                    mime_type=str(image.get("mime_type") or "") or None,
                    byte_size=(
                        int(image["byte_size"])
                        if image.get("byte_size") is not None
                        else None
                    ),
                    metadata={"derived_from_artifact_id": str(artifact["id"])},
                )
                rendered_images.append(
                    {
                        "artifact_id": str(image_row["id"]),
                        "uri": image_row["uri"],
                        "sha256": image_row["sha256"],
                        "mime_type": image_row["mime_type"],
                        "byte_size": image_row["byte_size"],
                    }
                )
            facts = dict(declared_date_facts or {})
            document = self._upsert_document(
                connection,
                investigation_id=investigation_uuid,
                canonical_key=canonical_key,
                document_type=document_type,
                title=title,
                evidence_level="retrieved_document",
                language=language,
                identifiers={
                    "publication_number": publication_number,
                    "authority": authority,
                },
                dates={
                    key: value
                    for key, value in facts.items()
                    if key in {
                        "public_availability_date",
                        "publication_date",
                        "filing_date",
                        "priority_date",
                    }
                    and value is not None
                },
                metadata={"provider": frozen_evidence.get("provider")},
                family_key=None,
                content_sha256=str(frozen_evidence["sha256"]),
                document_id=uuid.uuid4(),
            )
            version = self._ensure_document_version(
                connection,
                document_id=document["id"],
                content_sha256=str(frozen_evidence["sha256"]),
                content_artifact_id=artifact["id"],
                mime_type=str(frozen_evidence.get("mime_type") or "") or None,
                byte_size=artifact.get("byte_size"),
                acquisition_kind="human_import",
                version_metadata={
                    "full_text": str(frozen_evidence.get("extracted_text") or ""),
                    "rendered_images": rendered_images,
                    "source_url": frozen_evidence.get("source_url"),
                    "provider": frozen_evidence.get("provider"),
                },
                created_by=actor,
            )
            source = self._upsert_document_source(
                connection,
                document_id=document["id"],
                document_version_id=version["id"],
                provider=str(frozen_evidence.get("provider") or "human_import"),
                source_url=str(
                    frozen_evidence.get("source_url") or "manual-upload://evidence"
                ),
                provider_document_id=publication_number,
                retrieved_at=datetime.now(timezone.utc),
                mime_type=artifact.get("mime_type"),
                artifact_id=artifact["id"],
                snapshot_uri=str(artifact["uri"]),
                snapshot_sha256=str(artifact["sha256"]),
                snapshot_byte_size=artifact.get("byte_size"),
                raw_response={"human_supplied": True},
                status="retrieved",
            )
            action_id = uuid.uuid4()
            import_id = uuid.uuid4()
            queued = self._queue_human_review_action(
                connection,
                investigation=investigation,
                claims=claims,
                action_id=action_id,
                action_type="evidence_import",
                target_snapshot={
                    "claim_investigation_ids": [str(item) for item in claim_ids],
                    "document_id": str(document["id"]),
                    "document_version_id": str(version["id"]),
                    "evidence_import_id": str(import_id),
                },
                actor=actor,
                reason=reason,
                request_sha256=request_sha256,
                idempotency_key_sha256=idempotency_key_sha256,
                invalidated_derivations=("report",),
                pending_recomputation=(
                    "date_qualification",
                    "I4_S_SINGLE_REFERENCE",
                    "closest_prior_art",
                    "gaps",
                    "combination",
                    "report",
                ),
                response_extra={
                    "evidence_import_id": str(import_id),
                    "document_id": str(document["id"]),
                    "document_version_id": str(version["id"]),
                    "document_version_no": int(version["version_no"]),
                    "upload_receipt": {
                        "sha256": str(artifact["sha256"]),
                        "byte_size": artifact.get("byte_size"),
                        "mime_type": artifact.get("mime_type"),
                        "provider": frozen_evidence.get("provider"),
                        "source_url": frozen_evidence.get("source_url"),
                        "rendered_image_count": len(rendered_images),
                        "text_extracted": bool(
                            str(frozen_evidence.get("extracted_text") or "").strip()
                        ),
                    },
                },
            )
            evidence_import = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.evidence_imports (
                            id, investigation_id, review_action_id, document_id,
                            document_version_id, document_source_id,
                            content_artifact_id, claim_investigation_ids,
                            declared_date_facts, date_evidence, import_metadata,
                            actor, reason
                        ) VALUES (
                            :id, :investigation_id, :review_action_id, :document_id,
                            :document_version_id, :document_source_id,
                            :content_artifact_id,
                            CAST(:claim_investigation_ids AS JSONB),
                            CAST(:declared_date_facts AS JSONB),
                            CAST(:date_evidence AS JSONB),
                            CAST(:import_metadata AS JSONB), :actor, :reason
                        ) RETURNING *
                        """
                    ),
                    {
                        "id": import_id,
                        "investigation_id": investigation_uuid,
                        "review_action_id": action_id,
                        "document_id": document["id"],
                        "document_version_id": version["id"],
                        "document_source_id": source["id"],
                        "content_artifact_id": artifact["id"],
                        "claim_investigation_ids": _json(
                            [str(item) for item in claim_ids]
                        ),
                        "declared_date_facts": _json(facts),
                        "date_evidence": _json(date_evidence),
                        "import_metadata": _json(
                            {
                                "human_supplied": True,
                                "rendered_images": rendered_images,
                                "extracted_text_available": bool(
                                    str(
                                        frozen_evidence.get("extracted_text") or ""
                                    ).strip()
                                ),
                            }
                        ),
                        "actor": actor,
                        "reason": reason,
                    },
                )
            )
            assert evidence_import is not None
            queued["evidence_import"] = evidence_import
            return queued

    def create_document_date_confirmation_and_enqueue(
        self,
        *,
        investigation_id: uuid.UUID | str,
        claim_investigation_ids: Sequence[uuid.UUID | str],
        expected_investigation_state_version: int,
        expected_claim_state_versions: Mapping[str, int],
        expected_review_revision: int,
        idempotency_key_sha256: str,
        request_sha256: str,
        actor: str,
        reason: str,
        decision: str,
        document_id: uuid.UUID | str,
        document_version_id: uuid.UUID | str,
        expected_qualifications: Mapping[str, Mapping[str, Any]],
        date_facts: Mapping[str, Any],
        date_evidence: Mapping[str, Any],
    ) -> dict[str, Any]:
        if decision not in {"confirm_facts", "exclude", "reopen_review"}:
            raise ValueError("不支持的日期确认 decision")
        investigation_uuid = _uuid(investigation_id)
        document_uuid = _uuid(document_id)
        version_uuid = _uuid(document_version_id)
        claim_ids = [_uuid(value) for value in claim_investigation_ids]
        with self.transaction() as connection:
            investigation, claims, replay = self._lock_human_review_command(
                connection,
                investigation_id=investigation_uuid,
                claim_investigation_ids=claim_ids,
                expected_investigation_state_version=expected_investigation_state_version,
                expected_claim_state_versions=expected_claim_state_versions,
                expected_review_revision=expected_review_revision,
                idempotency_key_sha256=idempotency_key_sha256,
                request_sha256=request_sha256,
            )
            if replay is not None:
                replay["idempotent_replay"] = True
                return {"response": replay, "idempotent_replay": True}
            version = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT version.*
                        FROM {self.schema}.document_versions AS version
                        JOIN {self.schema}.documents AS document
                          ON document.id = version.document_id
                         AND document.investigation_id = :investigation_id
                        WHERE version.id = :version_id
                          AND version.document_id = :document_id
                        FOR UPDATE OF version
                        """
                    ),
                    {
                        "investigation_id": investigation_uuid,
                        "version_id": version_uuid,
                        "document_id": document_uuid,
                    },
                )
            )
            if version is None:
                raise RepositoryConflictError(
                    "document/document_version 不存在或归属不一致"
                )
            normalized_expected = {
                str(key): dict(value)
                for key, value in expected_qualifications.items()
            }
            if set(normalized_expected) != {str(item) for item in claim_ids}:
                raise RepositoryConflictError(
                    "expected_qualifications 未精确覆盖目标 claim"
                )
            latest_qualifications: dict[str, dict[str, Any]] = {}
            for claim_id in claim_ids:
                qualification = self._row(
                    connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.document_qualifications
                            WHERE document_id = :document_id
                              AND document_version_id = :document_version_id
                              AND claim_investigation_id = :claim_id
                              AND limitation_id IS NULL
                            ORDER BY assessment_version DESC, created_at DESC, id DESC
                            LIMIT 1 FOR UPDATE
                            """
                        ),
                        {
                            "document_id": document_uuid,
                            "document_version_id": version_uuid,
                            "claim_id": claim_id,
                        },
                    )
                )
                expected = normalized_expected[str(claim_id)]
                if (
                    qualification is None
                    or str(qualification["id"])
                    != str(expected.get("qualification_id") or "")
                    or int(qualification["assessment_version"])
                    != int(expected.get("assessment_version") or 0)
                ):
                    raise RepositoryConflictError(
                        f"claim {claim_id} 的 qualification 已变化"
                    )
                latest_qualifications[str(claim_id)] = qualification
            artifact_ids = {
                _uuid(value["artifact_id"])
                for value in date_evidence.values()
                if isinstance(value, Mapping) and value.get("artifact_id")
            }
            if artifact_ids:
                artifact_count = connection.execute(
                    text(
                        f"""
                        SELECT count(*) FROM {self.schema}.artifacts
                        WHERE investigation_id = :investigation_id
                          AND id = ANY(CAST(:artifact_ids AS UUID[]))
                        """
                    ),
                    {
                        "investigation_id": investigation_uuid,
                        "artifact_ids": [str(item) for item in artifact_ids],
                    },
                ).scalar_one()
                if artifact_count != len(artifact_ids):
                    raise RepositoryConflictError(
                        "日期证据 artifact 不属于当前 investigation"
                    )
            previous_revision = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.document_date_fact_revisions
                        WHERE document_version_id = :document_version_id
                        ORDER BY revision_no DESC, created_at DESC, id DESC
                        LIMIT 1 FOR UPDATE
                        """
                    ),
                    {"document_version_id": version_uuid},
                )
            )
            action_id = uuid.uuid4()
            revision_id = uuid.uuid4()
            queued = self._queue_human_review_action(
                connection,
                investigation=investigation,
                claims=claims,
                action_id=action_id,
                action_type="document_date_confirmation",
                target_snapshot={
                    "claim_investigation_ids": [str(item) for item in claim_ids],
                    "document_id": str(document_uuid),
                    "document_version_id": str(version_uuid),
                    "date_fact_revision_id": str(revision_id),
                    "expected_qualifications": normalized_expected,
                    "decision": decision,
                },
                actor=actor,
                reason=reason,
                request_sha256=request_sha256,
                idempotency_key_sha256=idempotency_key_sha256,
                invalidated_derivations=(
                    "novelty_stop",
                    "closest_prior_art",
                    "gaps",
                    "combination",
                    "report",
                ),
                pending_recomputation=(
                    "date_qualification",
                    "I4_S_SINGLE_REFERENCE",
                    "closest_prior_art",
                    "gaps",
                    "combination",
                    "report",
                ),
                response_extra={
                    "document_id": str(document_uuid),
                    "document_version_id": str(version_uuid),
                    "date_fact_revision_id": str(revision_id),
                },
            )
            revision = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.document_date_fact_revisions (
                            id, investigation_id, document_id, document_version_id,
                            review_action_id, revision_no, supersedes_id, decision,
                            claim_investigation_ids, expected_qualifications,
                            public_availability_date, publication_date, filing_date,
                            priority_date, source_type, publication_number, authority,
                            cn_application_scope, date_channel, date_evidence,
                            actor, reason
                        ) VALUES (
                            :id, :investigation_id, :document_id, :document_version_id,
                            :review_action_id, :revision_no, :supersedes_id, :decision,
                            CAST(:claim_investigation_ids AS JSONB),
                            CAST(:expected_qualifications AS JSONB),
                            :public_availability_date, :publication_date, :filing_date,
                            :priority_date, :source_type, :publication_number, :authority,
                            :cn_application_scope, :date_channel,
                            CAST(:date_evidence AS JSONB), :actor, :reason
                        ) RETURNING *
                        """
                    ),
                    {
                        "id": revision_id,
                        "investigation_id": investigation_uuid,
                        "document_id": document_uuid,
                        "document_version_id": version_uuid,
                        "review_action_id": action_id,
                        "revision_no": (
                            int(previous_revision["revision_no"]) + 1
                            if previous_revision
                            else 1
                        ),
                        "supersedes_id": (
                            previous_revision["id"] if previous_revision else None
                        ),
                        "decision": decision,
                        "claim_investigation_ids": _json(
                            [str(item) for item in claim_ids]
                        ),
                        "expected_qualifications": _json(normalized_expected),
                        "public_availability_date": date_facts.get(
                            "public_availability_date"
                        ),
                        "publication_date": date_facts.get("publication_date"),
                        "filing_date": date_facts.get("filing_date"),
                        "priority_date": date_facts.get("priority_date"),
                        "source_type": str(
                            date_facts.get("source_type") or "patent"
                        ),
                        "publication_number": date_facts.get("publication_number"),
                        "authority": date_facts.get("authority"),
                        "cn_application_scope": date_facts.get(
                            "cn_application_scope"
                        ),
                        "date_channel": str(
                            date_facts.get("date_channel") or "ordinary_prior_art"
                        ),
                        "date_evidence": _json(date_evidence),
                        "actor": actor,
                        "reason": reason,
                    },
                )
            )
            assert revision is not None
            queued["date_fact_revision"] = revision
            queued["prior_qualifications"] = latest_qualifications
            return queued

    def _upsert_document_source(
        self,
        connection: Connection,
        *,
        document_id: uuid.UUID,
        document_version_id: uuid.UUID | None,
        provider: str,
        source_url: str,
        retrieved_at: datetime,
        provider_document_id: str | None = None,
        mime_type: str | None = None,
        artifact_id: uuid.UUID | None = None,
        snapshot_uri: str | None = None,
        snapshot_sha256: str | None = None,
        snapshot_byte_size: int | None = None,
        raw_response: Mapping[str, Any] | None = None,
        status: str = "retrieved",
        error_message: str | None = None,
        source_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        row = self._row(
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.schema}.document_sources (
                        id, document_id, document_version_id, provider, source_url,
                        provider_document_id, retrieved_at, mime_type, artifact_id,
                        snapshot_uri, snapshot_sha256, snapshot_byte_size,
                        raw_response, status, error_message
                    ) VALUES (
                        :id, :document_id, :document_version_id, :provider, :source_url,
                        :provider_document_id, :retrieved_at, :mime_type,
                        :artifact_id, :snapshot_uri, :snapshot_sha256,
                        :snapshot_byte_size, CAST(:raw_response AS JSONB),
                        :status, :error_message
                    )
                    ON CONFLICT (document_id, provider, source_url, retrieved_at)
                    DO UPDATE SET
                        document_version_id = COALESCE(
                            {self.schema}.document_sources.document_version_id,
                            EXCLUDED.document_version_id
                        ),
                        provider_document_id = COALESCE(
                            EXCLUDED.provider_document_id,
                            {self.schema}.document_sources.provider_document_id
                        ),
                        mime_type = COALESCE(
                            EXCLUDED.mime_type, {self.schema}.document_sources.mime_type
                        ),
                        artifact_id = COALESCE(
                            EXCLUDED.artifact_id,
                            {self.schema}.document_sources.artifact_id
                        ),
                        snapshot_uri = COALESCE(
                            EXCLUDED.snapshot_uri,
                            {self.schema}.document_sources.snapshot_uri
                        ),
                        snapshot_sha256 = COALESCE(
                            EXCLUDED.snapshot_sha256,
                            {self.schema}.document_sources.snapshot_sha256
                        ),
                        snapshot_byte_size = COALESCE(
                            EXCLUDED.snapshot_byte_size,
                            {self.schema}.document_sources.snapshot_byte_size
                        ),
                        raw_response = COALESCE(
                            EXCLUDED.raw_response,
                            {self.schema}.document_sources.raw_response
                        ),
                        status = EXCLUDED.status,
                        error_message = EXCLUDED.error_message
                    WHERE {self.schema}.document_sources.document_version_id IS NULL
                       OR {self.schema}.document_sources.document_version_id
                          IS NOT DISTINCT FROM EXCLUDED.document_version_id
                    RETURNING *
                    """
                ),
                {
                    "id": source_id or uuid.uuid4(),
                    "document_id": document_id,
                    "document_version_id": document_version_id,
                    "provider": provider,
                    "source_url": source_url,
                    "provider_document_id": provider_document_id,
                    "retrieved_at": retrieved_at,
                    "mime_type": mime_type,
                    "artifact_id": artifact_id,
                    "snapshot_uri": snapshot_uri,
                    "snapshot_sha256": snapshot_sha256,
                    "snapshot_byte_size": snapshot_byte_size,
                    "raw_response": _json(raw_response) if raw_response is not None else None,
                    "status": status,
                    "error_message": error_message,
                },
            )
        )
        assert row is not None
        return row

    def upsert_document_source(
        self,
        *,
        document_id: uuid.UUID | str,
        document_version_id: uuid.UUID | str | None = None,
        provider: str,
        source_url: str,
        retrieved_at: datetime,
        provider_document_id: str | None = None,
        mime_type: str | None = None,
        artifact_id: uuid.UUID | str | None = None,
        snapshot_uri: str | None = None,
        snapshot_sha256: str | None = None,
        snapshot_byte_size: int | None = None,
        raw_response: Mapping[str, Any] | None = None,
        status: str = "retrieved",
        error_message: str | None = None,
        source_id: uuid.UUID | str | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        document_uuid = _uuid(document_id)
        artifact_uuid = _uuid(artifact_id) if artifact_id else None
        with self.transaction() as connection:
            owner = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT investigation_id, current_document_version_id
                        FROM {self.schema}.documents
                        WHERE id = :id FOR UPDATE
                        """
                    ),
                    {"id": document_uuid},
                )
            )
            if owner is None:
                raise RepositoryConflictError("document 不存在")
            version_uuid = (
                _uuid(document_version_id)
                if document_version_id
                else owner.get("current_document_version_id")
            )
            if version_uuid is not None:
                version = self._row(
                    connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.document_versions
                            WHERE id = :version_id AND document_id = :document_id
                            FOR UPDATE
                            """
                        ),
                        {
                            "version_id": version_uuid,
                            "document_id": document_uuid,
                        },
                    )
                )
                if version is None:
                    raise RepositoryConflictError(
                        "document_version 不属于指定 document"
                    )
                if snapshot_sha256 and str(version["content_sha256"]) != str(
                    snapshot_sha256
                ).lower():
                    raise RepositoryConflictError(
                        "source snapshot SHA 与 document_version 不一致"
                    )
            if artifact_uuid is not None:
                artifact_owned = connection.execute(
                    text(
                        f"""
                        SELECT 1 FROM {self.schema}.artifacts
                        WHERE id = :artifact_id
                          AND investigation_id = :investigation_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "artifact_id": artifact_uuid,
                        "investigation_id": owner["investigation_id"],
                    },
                ).first()
                if artifact_owned is None:
                    raise RepositoryConflictError(
                        "source artifact 不属于 document investigation"
                    )
            row = self._upsert_document_source(
                connection,
                document_id=document_uuid,
                document_version_id=version_uuid,
                provider=provider,
                source_url=source_url,
                retrieved_at=retrieved_at,
                provider_document_id=provider_document_id,
                mime_type=mime_type,
                artifact_id=artifact_uuid,
                snapshot_uri=snapshot_uri,
                snapshot_sha256=snapshot_sha256,
                snapshot_byte_size=snapshot_byte_size,
                raw_response=raw_response,
                status=status,
                error_message=error_message,
                source_id=_uuid(source_id),
            )
            if row is None:
                raise RepositoryConflictError(
                    "同一 source identity 已绑定其他 document_version"
                )
            self._insert_event(
                connection,
                investigation_id=owner["investigation_id"],
                event_type="document.source_upserted",
                actor=actor,
                payload={
                    "document_id": str(document_uuid),
                    "source_id": str(row["id"]),
                    "provider": provider,
                    "status": status,
                },
            )
            return row

    def _upsert_document_qualification(
        self,
        connection: Connection,
        *,
        document_id: uuid.UUID,
        document_version_id: uuid.UUID | None,
        claim_investigation_id: uuid.UUID,
        limitation_id: uuid.UUID | None,
        critical_date: date,
        earliest_priority_date: date | None,
        filing_date: date | None,
        publication_date: date | None,
        public_availability_date: date | None,
        eligibility_type: str,
        novelty_eligible: bool,
        inventive_step_eligible: bool,
        verification_status: str,
        verification_reason: str,
        date_rule_version: str,
        human_confirmed: bool,
        assessment_version: int,
        qualification_id: uuid.UUID,
        reason_codes: Sequence[str] = (),
        evidence_references: Mapping[str, Any] | None = None,
        supersedes_id: uuid.UUID | None = None,
        review_action_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        existing = self._row(
            connection.execute(
                text(
                    f"""
                    SELECT id FROM {self.schema}.document_qualifications
                    WHERE document_id = :document_id
                      AND document_version_id IS NOT DISTINCT FROM :document_version_id
                      AND claim_investigation_id = :claim_investigation_id
                      AND limitation_id IS NOT DISTINCT FROM :limitation_id
                      AND assessment_version = :assessment_version
                    FOR UPDATE
                    """
                ),
                {
                    "document_id": document_id,
                    "document_version_id": document_version_id,
                    "claim_investigation_id": claim_investigation_id,
                    "limitation_id": limitation_id,
                    "assessment_version": assessment_version,
                },
            )
        )
        identifier = existing["id"] if existing else qualification_id
        if existing:
            action = "UPDATE"
            prefix = f"UPDATE {self.schema}.document_qualifications SET"
            suffix = "WHERE id = :id RETURNING *"
        else:
            action = "INSERT"
            prefix = f"""
                INSERT INTO {self.schema}.document_qualifications (
                    critical_date, earliest_priority_date, filing_date,
                    publication_date, public_availability_date, eligibility_type,
                    novelty_eligible, inventive_step_eligible,
                    verification_status, verification_reason, date_rule_version,
                    human_confirmed, assessment_version,
                    reason_codes, evidence_references, supersedes_id,
                    review_action_id, id, document_id, document_version_id,
                    claim_investigation_id, limitation_id
                )
            """
            suffix = ""
        values_sql = """
            critical_date = :critical_date,
            earliest_priority_date = :earliest_priority_date,
            filing_date = :filing_date,
            publication_date = :publication_date,
            public_availability_date = :public_availability_date,
            eligibility_type = :eligibility_type,
            novelty_eligible = :novelty_eligible,
            inventive_step_eligible = :inventive_step_eligible,
            verification_status = :verification_status,
            verification_reason = :verification_reason,
            date_rule_version = :date_rule_version,
            human_confirmed = :human_confirmed,
            reason_codes = CAST(:reason_codes AS JSONB),
            evidence_references = CAST(:evidence_references AS JSONB),
            assessment_version = :assessment_version,
            updated_at = now()
        """
        if action == "INSERT":
            statement = f"""
                {prefix} VALUES (
                    :critical_date, :earliest_priority_date, :filing_date,
                    :publication_date, :public_availability_date,
                    :eligibility_type, :novelty_eligible,
                    :inventive_step_eligible, :verification_status,
                    :verification_reason, :date_rule_version, :human_confirmed,
                    :assessment_version, CAST(:reason_codes AS JSONB),
                    CAST(:evidence_references AS JSONB), :supersedes_id,
                    :review_action_id, :id, :document_id, :document_version_id,
                    :claim_investigation_id, :limitation_id
                ) RETURNING *
            """
        else:
            statement = f"{prefix} {values_sql} {suffix}"
        row = self._row(
            connection.execute(
                text(statement),
                {
                    "id": identifier,
                    "document_id": document_id,
                    "document_version_id": document_version_id,
                    "claim_investigation_id": claim_investigation_id,
                    "limitation_id": limitation_id,
                    "critical_date": critical_date,
                    "earliest_priority_date": earliest_priority_date,
                    "filing_date": filing_date,
                    "publication_date": publication_date,
                    "public_availability_date": public_availability_date,
                    "eligibility_type": eligibility_type,
                    "novelty_eligible": novelty_eligible,
                    "inventive_step_eligible": inventive_step_eligible,
                    "verification_status": verification_status,
                    "verification_reason": verification_reason,
                    "date_rule_version": date_rule_version,
                    "human_confirmed": human_confirmed,
                    "assessment_version": assessment_version,
                    "reason_codes": _json(list(reason_codes)),
                    "evidence_references": _json(evidence_references),
                    "supersedes_id": supersedes_id,
                    "review_action_id": review_action_id,
                },
            )
        )
        assert row is not None
        return row

    def upsert_document_qualification(
        self,
        *,
        document_id: uuid.UUID | str,
        document_version_id: uuid.UUID | str | None = None,
        claim_investigation_id: uuid.UUID | str,
        critical_date: date,
        eligibility_type: str,
        novelty_eligible: bool,
        inventive_step_eligible: bool,
        verification_status: str,
        verification_reason: str,
        date_rule_version: str,
        limitation_id: uuid.UUID | str | None = None,
        earliest_priority_date: date | None = None,
        filing_date: date | None = None,
        publication_date: date | None = None,
        public_availability_date: date | None = None,
        human_confirmed: bool = False,
        assessment_version: int = 1,
        qualification_id: uuid.UUID | str | None = None,
        reason_codes: Sequence[str] = (),
        evidence_references: Mapping[str, Any] | None = None,
        supersedes_id: uuid.UUID | str | None = None,
        review_action_id: uuid.UUID | str | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        document_uuid = _uuid(document_id)
        claim_uuid = _uuid(claim_investigation_id)
        limitation_uuid = _uuid(limitation_id) if limitation_id else None
        limitation_predicate = ""
        if limitation_uuid is not None:
            limitation_predicate = f"""
                AND EXISTS (
                    SELECT 1 FROM {self.schema}.claim_limitations AS limitation
                    WHERE limitation.id = :limitation_id
                      AND limitation.claim_investigation_id = claim.id
                )
            """
        with self.transaction() as connection:
            owner = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT document.investigation_id,
                               document.current_document_version_id
                        FROM {self.schema}.documents AS document
                        JOIN {self.schema}.claim_investigations AS claim
                          ON claim.id = :claim_id
                         AND claim.investigation_id = document.investigation_id
                        WHERE document.id = :id
                        {limitation_predicate}
                        FOR UPDATE OF document, claim
                        """
                    ),
                    {
                        "id": document_uuid,
                        "claim_id": claim_uuid,
                        "limitation_id": limitation_uuid,
                    },
                )
            )
            if owner is None:
                raise RepositoryConflictError(
                    "document、claim 与 limitation 不属于同一 investigation"
                )
            version_uuid = (
                _uuid(document_version_id)
                if document_version_id
                else owner.get("current_document_version_id")
            )
            if version_uuid is not None:
                owned_version = connection.execute(
                    text(
                        f"""
                        SELECT 1 FROM {self.schema}.document_versions
                        WHERE id = :version_id AND document_id = :document_id
                        FOR UPDATE
                        """
                    ),
                    {"version_id": version_uuid, "document_id": document_uuid},
                ).first()
                if owned_version is None:
                    raise RepositoryConflictError(
                        "qualification document_version 不属于指定 document"
                    )
            latest = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.document_qualifications
                        WHERE document_id = :document_id
                          AND claim_investigation_id = :claim_id
                          AND limitation_id IS NOT DISTINCT FROM :limitation_id
                        ORDER BY assessment_version DESC, created_at DESC, id DESC
                        LIMIT 1 FOR UPDATE
                        """
                    ),
                    {
                        "document_id": document_uuid,
                        "claim_id": claim_uuid,
                        "limitation_id": limitation_uuid,
                    },
                )
            )
            if (
                latest is not None
                and latest.get("document_version_id") != version_uuid
                and assessment_version <= int(latest["assessment_version"])
            ):
                assessment_version = int(latest["assessment_version"]) + 1
                if supersedes_id is None:
                    supersedes_id = latest["id"]
            row = self._upsert_document_qualification(
                connection,
                document_id=document_uuid,
                document_version_id=version_uuid,
                claim_investigation_id=claim_uuid,
                limitation_id=limitation_uuid,
                critical_date=critical_date,
                earliest_priority_date=earliest_priority_date,
                filing_date=filing_date,
                publication_date=publication_date,
                public_availability_date=public_availability_date,
                eligibility_type=eligibility_type,
                novelty_eligible=novelty_eligible,
                inventive_step_eligible=inventive_step_eligible,
                verification_status=verification_status,
                verification_reason=verification_reason,
                date_rule_version=date_rule_version,
                human_confirmed=human_confirmed,
                assessment_version=assessment_version,
                qualification_id=_uuid(qualification_id),
                reason_codes=reason_codes,
                evidence_references=evidence_references,
                supersedes_id=_uuid(supersedes_id) if supersedes_id else None,
                review_action_id=(
                    _uuid(review_action_id) if review_action_id else None
                ),
            )
            self._insert_event(
                connection,
                investigation_id=owner["investigation_id"],
                claim_investigation_id=claim_uuid,
                event_type="document.qualification_upserted",
                actor=actor,
                payload={
                    "document_id": str(document_uuid),
                    "qualification_id": str(row["id"]),
                    "eligibility_type": eligibility_type,
                    "verification_status": verification_status,
                },
            )
            return row

    def insert_feature_disclosures(
        self,
        *,
        module_run_id: uuid.UUID | str,
        claim_investigation_id: uuid.UUID | str,
        document_id: uuid.UUID | str,
        document_version_id: uuid.UUID | str | None = None,
        disclosures: Sequence[Mapping[str, Any]],
        rule_version: str,
        document_source_id: uuid.UUID | str | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
        actor: str = "orchestrator",
    ) -> list[dict[str, Any]]:
        run_uuid = _uuid(module_run_id)
        claim_uuid = _uuid(claim_investigation_id)
        document_uuid = _uuid(document_id)
        default_source_uuid = (
            _uuid(document_source_id) if document_source_id else None
        )
        limitation_ids = {_uuid(item["limitation_id"]) for item in disclosures}
        source_ids = {
            _uuid(item["document_source_id"])
            for item in disclosures
            if item.get("document_source_id")
        }
        if default_source_uuid is not None:
            source_ids.add(default_source_uuid)
        with self.transaction() as connection:
            owner = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT run.investigation_id,
                               document.current_document_version_id
                        FROM {self.schema}.module_runs AS run
                        JOIN {self.schema}.claim_investigations AS claim
                          ON claim.id = :claim_id
                         AND claim.investigation_id = run.investigation_id
                        JOIN {self.schema}.documents AS document
                          ON document.id = :document_id
                         AND document.investigation_id = run.investigation_id
                        WHERE run.id = :run_id
                        FOR UPDATE OF run
                        """
                    ),
                    {
                        "run_id": run_uuid,
                        "claim_id": claim_uuid,
                        "document_id": document_uuid,
                    },
                )
            )
            if owner is None:
                raise RepositoryConflictError(
                    "module run、claim 与 document 不属于同一 investigation"
                )
            version_uuid = (
                _uuid(document_version_id)
                if document_version_id
                else owner.get("current_document_version_id")
            )
            if version_uuid is not None:
                version_owned = connection.execute(
                    text(
                        f"""
                        SELECT 1 FROM {self.schema}.document_versions
                        WHERE id = :version_id AND document_id = :document_id
                        FOR UPDATE
                        """
                    ),
                    {"version_id": version_uuid, "document_id": document_uuid},
                ).first()
                if version_owned is None:
                    raise RepositoryConflictError(
                        "disclosure document_version 不属于指定 document"
                    )
            limitation_count = connection.execute(
                text(
                    f"""
                    SELECT count(*) FROM {self.schema}.claim_limitations
                    WHERE claim_investigation_id = :claim_id
                      AND id = ANY(CAST(:ids AS UUID[]))
                    """
                ),
                {
                    "claim_id": claim_uuid,
                    "ids": [str(item) for item in limitation_ids],
                },
            ).scalar_one()
            if limitation_count != len(limitation_ids):
                raise RepositoryConflictError(
                    "disclosure 包含其他 claim 的 limitation"
                )
            if source_ids:
                source_count = connection.execute(
                    text(
                        f"""
                        SELECT count(*) FROM {self.schema}.document_sources
                        WHERE document_id = :document_id
                          AND document_version_id IS NOT DISTINCT FROM :document_version_id
                          AND id = ANY(CAST(:ids AS UUID[]))
                        """
                    ),
                    {
                        "document_id": document_uuid,
                        "document_version_id": version_uuid,
                        "ids": [str(item) for item in source_ids],
                    },
                ).scalar_one()
                if source_count != len(source_ids):
                    raise RepositoryConflictError(
                        "disclosure source 不属于指定 document"
                    )
            rows: list[dict[str, Any]] = []
            for disclosure in disclosures:
                excerpt = disclosure.get("excerpt")
                excerpt_sha256 = disclosure.get("excerpt_sha256")
                if excerpt and not excerpt_sha256:
                    excerpt_sha256 = hashlib.sha256(
                        str(excerpt).encode("utf-8")
                    ).hexdigest()
                row = self._row(
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO {self.schema}.feature_disclosures (
                                id, module_run_id, claim_investigation_id,
                                limitation_id, document_id, document_version_id,
                                document_source_id,
                                disclosure_status, excerpt, locator, excerpt_sha256,
                                analysis, model_version, prompt_version, rule_version
                            ) VALUES (
                                :id, :module_run_id, :claim_investigation_id,
                                :limitation_id, :document_id, :document_version_id,
                                :document_source_id,
                                :disclosure_status, :excerpt,
                                CAST(:locator AS JSONB), :excerpt_sha256,
                                CAST(:analysis AS JSONB), :model_version,
                                :prompt_version, :rule_version
                            )
                            ON CONFLICT (module_run_id, limitation_id, document_id)
                            DO UPDATE SET
                                document_version_id = EXCLUDED.document_version_id,
                                document_source_id = EXCLUDED.document_source_id,
                                disclosure_status = EXCLUDED.disclosure_status,
                                excerpt = EXCLUDED.excerpt,
                                locator = EXCLUDED.locator,
                                excerpt_sha256 = EXCLUDED.excerpt_sha256,
                                analysis = EXCLUDED.analysis,
                                model_version = EXCLUDED.model_version,
                                prompt_version = EXCLUDED.prompt_version,
                                rule_version = EXCLUDED.rule_version,
                                updated_at = now()
                            RETURNING *
                            """
                        ),
                        {
                            "id": _uuid(disclosure.get("id")),
                            "module_run_id": run_uuid,
                            "claim_investigation_id": claim_uuid,
                            "limitation_id": _uuid(disclosure["limitation_id"]),
                            "document_id": document_uuid,
                            "document_version_id": version_uuid,
                            "document_source_id": (
                                _uuid(disclosure["document_source_id"])
                                if disclosure.get("document_source_id")
                                else default_source_uuid
                            ),
                            "disclosure_status": str(
                                disclosure.get("disclosure_status")
                                or disclosure.get("status")
                                or "uncertain"
                            ),
                            "excerpt": excerpt,
                            "locator": _json(disclosure.get("locator")),
                            "excerpt_sha256": excerpt_sha256,
                            "analysis": _json(disclosure.get("analysis")),
                            "model_version": (
                                disclosure.get("model_version") or model_version
                            ),
                            "prompt_version": (
                                disclosure.get("prompt_version") or prompt_version
                            ),
                            "rule_version": str(
                                disclosure.get("rule_version") or rule_version
                            ),
                        },
                    )
                )
                assert row is not None
                rows.append(row)
            self._insert_event(
                connection,
                investigation_id=owner["investigation_id"],
                claim_investigation_id=claim_uuid,
                module_run_id=run_uuid,
                event_type="feature_disclosures.inserted",
                actor=actor,
                payload={"document_id": str(document_uuid), "count": len(rows)},
            )
            return rows

    def insert_closest_prior_art_version(
        self,
        *,
        claim_investigation_id: uuid.UUID | str,
        iteration_id: uuid.UUID | str,
        document_id: uuid.UUID | str,
        document_version_id: uuid.UUID | str | None = None,
        metrics: Mapping[str, Any],
        rationale: Mapping[str, Any],
        selected_by: str,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        claim_uuid = _uuid(claim_investigation_id)
        iteration_uuid = _uuid(iteration_id)
        document_uuid = _uuid(document_id)
        with self.transaction() as connection:
            owner = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT claim.investigation_id,
                               document.current_document_version_id
                        FROM {self.schema}.claim_investigations AS claim
                        JOIN {self.schema}.iterations AS iteration
                          ON iteration.id = :iteration_id
                         AND iteration.claim_investigation_id = claim.id
                        JOIN {self.schema}.documents AS document
                          ON document.id = :document_id
                         AND document.investigation_id = claim.investigation_id
                        WHERE claim.id = :claim_id
                        FOR UPDATE OF claim
                        """
                    ),
                    {
                        "claim_id": claim_uuid,
                        "iteration_id": iteration_uuid,
                        "document_id": document_uuid,
                    },
                )
            )
            if owner is None:
                raise RepositoryConflictError(
                    "claim、iteration 与 document 不属于同一 investigation"
                )
            version_uuid = (
                _uuid(document_version_id)
                if document_version_id
                else owner.get("current_document_version_id")
            )
            if version_uuid is None:
                raise RepositoryConflictError("D1 必须绑定 document_version")
            version_owned = connection.execute(
                text(
                    f"""
                    SELECT 1 FROM {self.schema}.document_versions
                    WHERE id = :version_id AND document_id = :document_id
                    FOR UPDATE
                    """
                ),
                {"version_id": version_uuid, "document_id": document_uuid},
            ).first()
            if version_owned is None:
                raise RepositoryConflictError(
                    "D1 document_version 不属于指定 document"
                )
            previous = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.closest_prior_art_versions
                        WHERE claim_investigation_id = :claim_id
                        ORDER BY version_no DESC LIMIT 1
                        FOR UPDATE
                        """
                    ),
                    {"claim_id": claim_uuid},
                )
            )
            if previous is not None:
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.closest_prior_art_versions
                        SET is_current = false
                        WHERE claim_investigation_id = :claim_id AND is_current
                        """
                    ),
                    {"claim_id": claim_uuid},
                )
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.closest_prior_art_versions (
                            id, claim_investigation_id, iteration_id, document_id,
                            document_version_id,
                            version_no, metrics, rationale, selected_by,
                            supersedes_id, is_current
                        ) VALUES (
                            :id, :claim_id, :iteration_id, :document_id,
                            :document_version_id,
                            :version_no, CAST(:metrics AS JSONB),
                            CAST(:rationale AS JSONB), :selected_by,
                            :supersedes_id, true
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "claim_id": claim_uuid,
                        "iteration_id": iteration_uuid,
                        "document_id": document_uuid,
                        "document_version_id": version_uuid,
                        "version_no": (
                            int(previous["version_no"]) + 1 if previous else 1
                        ),
                        "metrics": _json(metrics),
                        "rationale": _json(rationale),
                        "selected_by": selected_by,
                        "supersedes_id": previous["id"] if previous else None,
                    },
                )
            )
            assert row is not None
            self._insert_event(
                connection,
                investigation_id=owner["investigation_id"],
                claim_investigation_id=claim_uuid,
                iteration_id=iteration_uuid,
                event_type="closest_prior_art.selected",
                actor=actor,
                payload={
                    "selection_id": str(row["id"]),
                    "document_id": str(document_uuid),
                    "version_no": row["version_no"],
                },
            )
            return row

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
        """Atomically append one immutable gap frontier and close disappeared gaps.

        A committed iteration is immutable.  An exact replay returns the existing
        rows; a replay with a different open set raises a conflict.  Closing a gap
        appends a new version and never updates the previous open version.
        """

        iteration_uuid = _uuid(iteration_id)
        claim_uuid = _uuid(claim_investigation_id)
        source_run_uuid = (
            _uuid(source_module_run_id) if source_module_run_id else None
        )
        with self.transaction() as connection:
            owner = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT claim.investigation_id, iteration.iteration_no,
                               iteration.created_at AS iteration_created_at
                        FROM {self.schema}.claim_investigations AS claim
                        JOIN {self.schema}.iterations AS iteration
                          ON iteration.id = :iteration_id
                         AND iteration.claim_investigation_id = claim.id
                        WHERE claim.id = :claim_id
                        FOR UPDATE OF claim, iteration
                        """
                    ),
                    {"claim_id": claim_uuid, "iteration_id": iteration_uuid},
                )
            )
            if owner is None:
                raise RepositoryConflictError("iteration 不属于指定 claim")

            current_rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.gap_items
                        WHERE iteration_id = :iteration_id
                          AND claim_investigation_id = :claim_id
                        ORDER BY gap_key, version_no, id
                        FOR UPDATE
                        """
                    ),
                    {"iteration_id": iteration_uuid, "claim_id": claim_uuid},
                ).mappings()
            ]
            previous_rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT gap.*
                        FROM {self.schema}.gap_items AS gap
                        LEFT JOIN {self.schema}.iterations AS iteration
                          ON iteration.id = gap.iteration_id
                        LEFT JOIN {self.schema}.human_review_actions AS review
                          ON review.id = gap.review_action_id
                        WHERE gap.claim_investigation_id = :claim_id
                          AND (
                              iteration.iteration_no < :iteration_no
                              OR (
                                  review.status = 'applied'
                                  AND review.completed_at
                                      <= :iteration_created_at
                              )
                          )
                        ORDER BY gap.gap_key, gap.version_no DESC,
                                 gap.created_at DESC, gap.id DESC
                        FOR UPDATE OF gap
                        """
                    ),
                    {
                        "claim_id": claim_uuid,
                        "iteration_no": int(owner["iteration_no"]),
                        "iteration_created_at": owner["iteration_created_at"],
                    },
                ).mappings()
            ]
            plan = plan_gap_frontier(
                iteration_no=int(owner["iteration_no"]),
                open_gaps=open_gaps,
                previous_gap_items=previous_rows,
                default_source_module_run_id=source_run_uuid,
                closed_gap_resolutions=closed_gap_resolutions,
            )
            planned_rows = list(plan["rows"])

            limitation_ids = {
                _uuid(item["limitation_id"])
                for item in planned_rows
                if item.get("limitation_id")
            }
            resolved_document_ids = {
                _uuid(item["resolved_by_document_id"])
                for item in planned_rows
                if item.get("resolved_by_document_id")
            }
            source_run_ids = {
                _uuid(item["source_module_run_id"])
                for item in planned_rows
                if item.get("source_module_run_id")
            }
            if source_run_uuid is not None:
                source_run_ids.add(source_run_uuid)
            if limitation_ids:
                limitation_count = connection.execute(
                    text(
                        f"""
                        SELECT count(*) FROM {self.schema}.claim_limitations
                        WHERE claim_investigation_id = :claim_id
                          AND id = ANY(CAST(:ids AS UUID[]))
                        """
                    ),
                    {
                        "claim_id": claim_uuid,
                        "ids": [str(item) for item in limitation_ids],
                    },
                ).scalar_one()
                if limitation_count != len(limitation_ids):
                    raise RepositoryConflictError(
                        "gap 包含其他 claim 的 limitation"
                    )
            if resolved_document_ids:
                document_count = connection.execute(
                    text(
                        f"""
                        SELECT count(*) FROM {self.schema}.documents
                        WHERE investigation_id = :investigation_id
                          AND id = ANY(CAST(:ids AS UUID[]))
                        """
                    ),
                    {
                        "investigation_id": owner["investigation_id"],
                        "ids": [str(item) for item in resolved_document_ids],
                    },
                ).scalar_one()
                if document_count != len(resolved_document_ids):
                    raise RepositoryConflictError(
                        "gap resolution 包含其他 investigation 的 document"
                    )
            if source_run_ids:
                run_count = connection.execute(
                    text(
                        f"""
                        SELECT count(*) FROM {self.schema}.module_runs
                        WHERE investigation_id = :investigation_id
                          AND id = ANY(CAST(:ids AS UUID[]))
                        """
                    ),
                    {
                        "investigation_id": owner["investigation_id"],
                        "ids": [str(item) for item in source_run_ids],
                    },
                ).scalar_one()
                if run_count != len(source_run_ids):
                    raise RepositoryConflictError(
                        "gap 包含其他 investigation 的 module run"
                    )

            expected_contract = [
                _gap_frontier_row_contract(item) for item in planned_rows
            ]
            existing_contract = [
                _gap_frontier_row_contract(item) for item in current_rows
            ]
            if current_rows and existing_contract != expected_contract:
                raise RepositoryConflictError(
                    "该 iteration 的 gap frontier 已提交，禁止覆盖历史"
                )

            frontier_event = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.events
                        WHERE claim_investigation_id = :claim_id
                          AND iteration_id = :iteration_id
                          AND event_type = :event_type
                        ORDER BY id DESC
                        LIMIT 1
                        FOR UPDATE
                        """
                    ),
                    {
                        "claim_id": claim_uuid,
                        "iteration_id": iteration_uuid,
                        "event_type": _GAP_FRONTIER_EVENT_TYPE,
                    },
                )
            )
            if frontier_event is not None:
                event_fingerprint = str(
                    (frontier_event.get("payload") or {}).get(
                        "frontier_fingerprint"
                    )
                    or ""
                )
                if (
                    event_fingerprint != plan["frontier_fingerprint"]
                    or existing_contract != expected_contract
                ):
                    raise RepositoryConflictError(
                        "该 iteration 的 gap frontier 已提交，重放内容不一致"
                    )
                return current_rows

            persisted_rows = current_rows
            if not current_rows:
                persisted_rows = []
                for item in planned_rows:
                    row = self._row(
                        connection.execute(
                            text(
                                f"""
                                INSERT INTO {self.schema}.gap_items (
                                    id, iteration_id, claim_investigation_id,
                                    limitation_id, source_module_run_id, gap_type,
                                    gap_key, gap_fingerprint, version_no,
                                    supersedes_id, status, description,
                                    search_objective, resolved_by_document_id,
                                    resolution_evidence, closed_reason, resolved_at
                                ) VALUES (
                                    :id, :iteration_id, :claim_id,
                                    :limitation_id, :source_module_run_id, :gap_type,
                                    :gap_key, :gap_fingerprint, :version_no,
                                    :supersedes_id, :status, :description,
                                    CAST(:search_objective AS JSONB),
                                    :resolved_by_document_id,
                                    CAST(:resolution_evidence AS JSONB),
                                    :closed_reason,
                                    CASE WHEN :status = 'open' THEN NULL
                                         ELSE COALESCE(:resolved_at, now()) END
                                )
                                RETURNING *
                                """
                            ),
                            {
                                "id": uuid.uuid4(),
                                "iteration_id": iteration_uuid,
                                "claim_id": claim_uuid,
                                "limitation_id": (
                                    _uuid(item["limitation_id"])
                                    if item.get("limitation_id")
                                    else None
                                ),
                                "source_module_run_id": (
                                    _uuid(item["source_module_run_id"])
                                    if item.get("source_module_run_id")
                                    else None
                                ),
                                "gap_type": item["gap_type"],
                                "gap_key": item["gap_key"],
                                "gap_fingerprint": item["gap_fingerprint"],
                                "version_no": item["version_no"],
                                "supersedes_id": (
                                    _uuid(item["supersedes_id"])
                                    if item.get("supersedes_id")
                                    else None
                                ),
                                "status": item["status"],
                                "description": item["description"],
                                "search_objective": _json(
                                    item.get("search_objective") or {}
                                ),
                                "resolved_by_document_id": (
                                    _uuid(item["resolved_by_document_id"])
                                    if item.get("resolved_by_document_id")
                                    else None
                                ),
                                "resolution_evidence": (
                                    _json(item["resolution_evidence"])
                                    if item.get("resolution_evidence") is not None
                                    else None
                                ),
                                "closed_reason": item.get("closed_reason"),
                                "resolved_at": item.get("resolved_at"),
                            },
                        )
                    )
                    assert row is not None
                    persisted_rows.append(row)

            self._insert_event(
                connection,
                investigation_id=owner["investigation_id"],
                claim_investigation_id=claim_uuid,
                iteration_id=iteration_uuid,
                module_run_id=source_run_uuid,
                event_type=_GAP_FRONTIER_EVENT_TYPE,
                actor=actor,
                payload={
                    "iteration_no": int(owner["iteration_no"]),
                    "frontier_fingerprint": plan["frontier_fingerprint"],
                    "open_gap_keys": plan["open_gap_keys"],
                    "closed_gap_keys": plan["closed_gap_keys"],
                    "open_count": len(plan["open_gap_keys"]),
                    "closed_count": len(plan["closed_gap_keys"]),
                    "delta": plan["delta"],
                },
            )
            return persisted_rows

    def insert_gap_items(
        self,
        *,
        iteration_id: uuid.UUID | str,
        claim_investigation_id: uuid.UUID | str,
        gaps: Sequence[Mapping[str, Any]],
        source_module_run_id: uuid.UUID | str | None = None,
        actor: str = "orchestrator",
    ) -> list[dict[str, Any]]:
        """Compatibility wrapper for the versioned frontier repository contract."""

        return self.reconcile_gap_frontier(
            iteration_id=iteration_id,
            claim_investigation_id=claim_investigation_id,
            open_gaps=gaps,
            source_module_run_id=source_module_run_id,
            actor=actor,
        )

    def insert_gaps(self, **kwargs: Any) -> list[dict[str, Any]]:
        """Compatibility alias; new callers should use ``reconcile_gap_frontier``."""

        if "open_gaps" in kwargs:
            return self.reconcile_gap_frontier(**kwargs)
        return self.insert_gap_items(**kwargs)

    def insert_combination(
        self,
        *,
        claim_investigation_id: uuid.UUID | str,
        iteration_id: uuid.UUID | str,
        module_run_id: uuid.UUID | str,
        closest_prior_art_version_id: uuid.UUID | str,
        document_ids: Sequence[uuid.UUID | str],
        document_version_ids: Sequence[uuid.UUID | str] | None = None,
        status: str,
        coverage_complete: bool,
        motivation_status: str,
        analysis: Mapping[str, Any],
        combination_id: uuid.UUID | str | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        claim_uuid = _uuid(claim_investigation_id)
        iteration_uuid = _uuid(iteration_id)
        run_uuid = _uuid(module_run_id)
        selection_uuid = _uuid(closest_prior_art_version_id)
        normalized_document_ids = [_uuid(item) for item in document_ids]
        normalized_version_ids = (
            [_uuid(item) for item in document_version_ids]
            if document_version_ids is not None
            else []
        )
        if normalized_version_ids and len(normalized_version_ids) != len(
            normalized_document_ids
        ):
            raise ValueError("document_version_ids 必须与 document_ids 一一对应")
        with self.transaction() as connection:
            owner = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT claim.investigation_id
                        FROM {self.schema}.claim_investigations AS claim
                        JOIN {self.schema}.iterations AS iteration
                          ON iteration.id = :iteration_id
                         AND iteration.claim_investigation_id = claim.id
                        JOIN {self.schema}.module_runs AS run
                          ON run.id = :module_run_id
                         AND run.investigation_id = claim.investigation_id
                         AND run.claim_investigation_id = claim.id
                         AND run.iteration_id = iteration.id
                        JOIN {self.schema}.closest_prior_art_versions AS selection
                          ON selection.id = :selection_id
                         AND selection.claim_investigation_id = claim.id
                        WHERE claim.id = :claim_id
                        FOR UPDATE OF claim, iteration, run, selection
                        """
                    ),
                    {
                        "claim_id": claim_uuid,
                        "iteration_id": iteration_uuid,
                        "module_run_id": run_uuid,
                        "selection_id": selection_uuid,
                    },
                )
            )
            if owner is None:
                raise RepositoryConflictError(
                    "claim、iteration、module run 与 selection 归属不一致"
                )
            document_count = connection.execute(
                text(
                    f"""
                    SELECT count(*) FROM {self.schema}.documents
                    WHERE investigation_id = :investigation_id
                      AND id = ANY(CAST(:document_ids AS UUID[]))
                    """
                ),
                {
                    "investigation_id": owner["investigation_id"],
                    "document_ids": [str(item) for item in normalized_document_ids],
                },
            ).scalar_one()
            if document_count != len(set(normalized_document_ids)):
                raise RepositoryConflictError(
                    "combination 包含其他 investigation 的 document"
                )
            if not normalized_version_ids:
                current_versions = {
                    row["id"]: row["current_document_version_id"]
                    for row in connection.execute(
                        text(
                            f"""
                            SELECT id, current_document_version_id
                            FROM {self.schema}.documents
                            WHERE investigation_id = :investigation_id
                              AND id = ANY(CAST(:document_ids AS UUID[]))
                            """
                        ),
                        {
                            "investigation_id": owner["investigation_id"],
                            "document_ids": [
                                str(item) for item in normalized_document_ids
                            ],
                        },
                    ).mappings()
                }
                normalized_version_ids = [
                    current_versions.get(document_id)
                    for document_id in normalized_document_ids
                ]
            if any(item is None for item in normalized_version_ids):
                raise RepositoryConflictError(
                    "combination 中每份 document 都必须绑定 document_version"
                )
            bound_pairs = connection.execute(
                text(
                    f"""
                    SELECT document_id, id
                    FROM {self.schema}.document_versions
                    WHERE id = ANY(CAST(:version_ids AS UUID[]))
                    FOR UPDATE
                    """
                ),
                {"version_ids": [str(item) for item in normalized_version_ids]},
            ).mappings()
            actual_pairs = {(row["document_id"], row["id"]) for row in bound_pairs}
            expected_pairs = set(zip(normalized_document_ids, normalized_version_ids))
            if actual_pairs != expected_pairs:
                raise RepositoryConflictError(
                    "combination document_version 与 document 绑定不一致"
                )
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.combinations (
                            id, claim_investigation_id, iteration_id,
                            module_run_id, closest_prior_art_version_id,
                            document_ids, document_version_ids, status, coverage_complete,
                            motivation_status, analysis
                        ) VALUES (
                            :id, :claim_id, :iteration_id, :module_run_id,
                            :selection_id, CAST(:document_ids AS JSONB),
                            CAST(:document_version_ids AS JSONB), :status,
                            :coverage_complete, :motivation_status,
                            CAST(:analysis AS JSONB)
                        )
                        ON CONFLICT (module_run_id, closest_prior_art_version_id)
                        DO UPDATE SET
                            document_ids = EXCLUDED.document_ids,
                            document_version_ids = EXCLUDED.document_version_ids,
                            status = EXCLUDED.status,
                            coverage_complete = EXCLUDED.coverage_complete,
                            motivation_status = EXCLUDED.motivation_status,
                            analysis = EXCLUDED.analysis,
                            updated_at = now()
                        RETURNING *
                        """
                    ),
                    {
                        "id": _uuid(combination_id),
                        "claim_id": claim_uuid,
                        "iteration_id": iteration_uuid,
                        "module_run_id": run_uuid,
                        "selection_id": selection_uuid,
                        "document_ids": _json(
                            [str(item) for item in normalized_document_ids]
                        ),
                        "document_version_ids": _json(
                            [str(item) for item in normalized_version_ids]
                        ),
                        "status": status,
                        "coverage_complete": coverage_complete,
                        "motivation_status": motivation_status,
                        "analysis": _json(analysis),
                    },
                )
            )
            assert row is not None
            self._insert_event(
                connection,
                investigation_id=owner["investigation_id"],
                claim_investigation_id=claim_uuid,
                iteration_id=iteration_uuid,
                module_run_id=run_uuid,
                event_type="combination.upserted",
                actor=actor,
                payload={
                    "combination_id": str(row["id"]),
                    "coverage_complete": coverage_complete,
                    "motivation_status": motivation_status,
                },
            )
            return row

    def append_event(
        self,
        *,
        investigation_id: uuid.UUID | str,
        event_type: str,
        actor: str,
        payload: Mapping[str, Any] | None = None,
        claim_investigation_id: uuid.UUID | str | None = None,
        iteration_id: uuid.UUID | str | None = None,
        module_run_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            return self._insert_event(
                connection,
                investigation_id=_uuid(investigation_id),
                event_type=event_type,
                actor=actor,
                payload=payload,
                claim_investigation_id=(
                    _uuid(claim_investigation_id) if claim_investigation_id else None
                ),
                iteration_id=_uuid(iteration_id) if iteration_id else None,
                module_run_id=_uuid(module_run_id) if module_run_id else None,
            )

    def _insert_event(
        self,
        connection: Connection,
        *,
        investigation_id: uuid.UUID,
        event_type: str,
        actor: str,
        payload: Mapping[str, Any] | None,
        claim_investigation_id: uuid.UUID | None = None,
        iteration_id: uuid.UUID | None = None,
        module_run_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        row = self._row(
            connection.execute(
                text(
                    f"""
                    INSERT INTO {self.schema}.events (
                        investigation_id, claim_investigation_id, iteration_id,
                        module_run_id, event_type, actor, payload
                    ) VALUES (
                        :investigation_id, :claim_investigation_id, :iteration_id,
                        :module_run_id, :event_type, :actor,
                        CAST(:payload AS JSONB)
                    )
                    RETURNING *
                    """
                ),
                {
                    "investigation_id": investigation_id,
                    "claim_investigation_id": claim_investigation_id,
                    "iteration_id": iteration_id,
                    "module_run_id": module_run_id,
                    "event_type": event_type,
                    "actor": actor,
                    "payload": _json(payload),
                },
            )
        )
        assert row is not None
        return row

    def record_event_and_enqueue_job(
        self,
        *,
        investigation_id: uuid.UUID | str,
        module_run_id: uuid.UUID | str,
        event_type: str,
        actor: str,
        event_payload: Mapping[str, Any] | None,
        job_payload: Mapping[str, Any],
        claim_investigation_id: uuid.UUID | str | None = None,
        iteration_id: uuid.UUID | str | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
        job_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any]:
        """Atomically append an audit event and make the next job visible."""

        investigation_uuid = _uuid(investigation_id)
        module_run_uuid = _uuid(module_run_id)
        claim_uuid = _uuid(claim_investigation_id) if claim_investigation_id else None
        iteration_uuid = _uuid(iteration_id) if iteration_id else None
        with self.transaction() as connection:
            owned = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.module_runs
                        WHERE id = :module_run_id
                          AND investigation_id = :investigation_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "module_run_id": module_run_uuid,
                        "investigation_id": investigation_uuid,
                    },
                )
            )
            if owned is None:
                raise RepositoryConflictError(
                    "module run 不存在或不属于指定 investigation"
                )
            if str(owned.get("status") or "") in {
                "cancellation_requested",
                "cancelled",
                "succeeded",
                "partial",
                "failed",
            }:
                raise RepositoryConflictError(
                    "module run 已取消或已终态，禁止补建 job"
                )
            if claim_uuid is not None and claim_uuid != owned["claim_investigation_id"]:
                raise RepositoryConflictError("event claim 与 module run 归属不一致")
            if iteration_uuid is not None and iteration_uuid != owned["iteration_id"]:
                raise RepositoryConflictError("event iteration 与 module run 归属不一致")
            claim_uuid = claim_uuid or owned["claim_investigation_id"]
            iteration_uuid = iteration_uuid or owned["iteration_id"]
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.module_runs
                    SET status = 'queued', updated_at = now()
                    WHERE id = :module_run_id
                    """
                ),
                {"module_run_id": module_run_uuid},
            )
            job = self._insert_job(
                connection,
                job_id=_uuid(job_id),
                module_run_id=module_run_uuid,
                payload=job_payload,
                priority=priority,
                max_attempts=max_attempts,
                available_at=available_at,
            )
            self._insert_event(
                connection,
                investigation_id=investigation_uuid,
                claim_investigation_id=claim_uuid,
                iteration_id=iteration_uuid,
                module_run_id=module_run_uuid,
                event_type=event_type,
                actor=actor,
                payload=event_payload,
            )
            return job

    def transition_claim_and_schedule(
        self,
        *,
        claim_investigation_id: uuid.UUID | str,
        expected_state_version: int,
        new_claim_status: str,
        module_code: str,
        contract_version: str,
        input_snapshot: Mapping[str, Any],
        idempotency_key: str,
        event_type: str,
        actor: str,
        event_payload: Mapping[str, Any] | None,
        job_payload: Mapping[str, Any],
        iteration_id: uuid.UUID | str | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        available_at: datetime | None = None,
        module_run_id: uuid.UUID | str | None = None,
        job_id: uuid.UUID | str | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
        rule_version: str | None = None,
    ) -> dict[str, Any]:
        """CAS a claim state and persist its event, run and next job atomically."""

        claim_uuid = _uuid(claim_investigation_id)
        iteration_uuid = _uuid(iteration_id) if iteration_id else None
        snapshot = _json(input_snapshot)
        with self.transaction() as connection:
            claim = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.claim_investigations
                        SET status = :new_status,
                            state_version = state_version + 1,
                            updated_at = now()
                        WHERE id = :claim_id
                          AND state_version = :expected_state_version
                        RETURNING *
                        """
                    ),
                    {
                        "claim_id": claim_uuid,
                        "new_status": new_claim_status,
                        "expected_state_version": expected_state_version,
                    },
                )
            )
            if claim is None:
                raise RepositoryConflictError(
                    "claim workflow state 已变化，拒绝重复调度下一任务"
                )

            if iteration_uuid is not None:
                iteration_owner = connection.execute(
                    text(
                        f"""
                        SELECT 1 FROM {self.schema}.iterations
                        WHERE id = :iteration_id
                          AND claim_investigation_id = :claim_id
                        FOR UPDATE
                        """
                    ),
                    {"iteration_id": iteration_uuid, "claim_id": claim_uuid},
                ).first()
                if iteration_owner is None:
                    raise RepositoryConflictError(
                        "iteration 不属于正在转换的 claim"
                    )

            module_run = self._insert_module_run(
                connection,
                module_run_id=_uuid(module_run_id),
                investigation_id=claim["investigation_id"],
                claim_investigation_id=claim_uuid,
                iteration_id=iteration_uuid,
                module_code=module_code,
                contract_version=contract_version,
                input_snapshot=snapshot,
                input_sha256=hashlib.sha256(snapshot.encode("utf-8")).hexdigest(),
                idempotency_key=idempotency_key,
                status="queued",
                model_version=model_version,
                prompt_version=prompt_version,
                rule_version=rule_version,
            )
            job = self._insert_job(
                connection,
                job_id=_uuid(job_id),
                module_run_id=module_run["id"],
                payload=job_payload,
                priority=priority,
                max_attempts=max_attempts,
                available_at=available_at,
            )
            self._insert_event(
                connection,
                investigation_id=claim["investigation_id"],
                claim_investigation_id=claim_uuid,
                iteration_id=iteration_uuid,
                module_run_id=module_run["id"],
                event_type=event_type,
                actor=actor,
                payload=event_payload,
            )
            return {"claim": claim, "module_run": module_run, "job": job}

    def recover_expired_jobs(
        self,
        *,
        actor: str = "lease-recovery",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fail final-attempt leases that expired before they could complete.

        The recovery is terminal: it does not increment ``attempt_count`` and
        therefore cannot run a job beyond ``max_attempts``.  Job, module-run,
        investigation (for I0), and audit-event updates commit atomically.
        """

        if not actor.strip():
            raise ValueError("actor 不能为空")
        if not 1 <= limit <= 1_000:
            raise ValueError("limit 必须在 1..1000 范围内")
        with self.transaction() as connection:
            return self._recover_exhausted_expired_jobs(
                connection,
                actor=actor,
                limit=limit,
            )

    def _recover_cancel_requested_jobs(
        self,
        connection: Connection,
        *,
        actor: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            text(
                f"""
                SELECT * FROM {self.schema}.jobs
                WHERE cancel_requested_at IS NOT NULL
                  AND (
                      status = 'queued'
                      OR (
                          status = 'leased'
                          AND lease_expires_at IS NOT NULL
                          AND lease_expires_at <= now()
                      )
                  )
                ORDER BY cancel_requested_at ASC, created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings()
        return [
            self._cancel_locked_job(
                connection,
                locked_job=dict(row),
                actor=actor,
            )
            for row in rows
        ]

    def _recover_exhausted_expired_jobs(
        self,
        connection: Connection,
        *,
        actor: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        error_payload = {
            "code": _EXHAUSTED_LEASE_ERROR_CODE,
            "message": _EXHAUSTED_LEASE_ERROR_MESSAGE,
        }
        rows = connection.execute(
            text(build_recover_expired_jobs_sql(self.schema, self.environment)),
            {
                "recovery_limit": limit,
                "last_error": _json(error_payload),
            },
        ).mappings()
        jobs = [dict(row) for row in rows]
        recovered: list[dict[str, Any]] = []
        for job in jobs:
            module_run = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.module_runs
                        SET status = 'failed',
                            output_snapshot = NULL,
                            error_code = :error_code,
                            error_message = :error_message,
                            retryable = false,
                            completed_at = now(),
                            updated_at = now()
                        WHERE id = :module_run_id
                        RETURNING *
                        """
                    ),
                    {
                        "module_run_id": job["module_run_id"],
                        "error_code": _EXHAUSTED_LEASE_ERROR_CODE,
                        "error_message": _EXHAUSTED_LEASE_ERROR_MESSAGE,
                    },
                )
            )
            if module_run is None:  # Protected by the jobs foreign key.
                raise RepositoryConflictError(
                    "租约回收时找不到对应 module run"
                )
            self._mark_human_review_action_terminal(
                connection,
                module_run=module_run,
                status="failed",
                error_code=_EXHAUSTED_LEASE_ERROR_CODE,
                error_message=_EXHAUSTED_LEASE_ERROR_MESSAGE,
            )
            self._insert_event(
                connection,
                investigation_id=module_run["investigation_id"],
                claim_investigation_id=module_run["claim_investigation_id"],
                iteration_id=module_run["iteration_id"],
                module_run_id=module_run["id"],
                event_type="job.failed",
                actor=actor,
                payload={
                    "job_id": str(job["id"]),
                    "attempt": job["attempt_count"],
                    "error_code": _EXHAUSTED_LEASE_ERROR_CODE,
                    "retryable": False,
                    "recovery_reason": "final_lease_expired",
                },
            )

            i0_failure = self._finalize_i0_terminal_failure(
                connection,
                module_run=module_run,
                error_code=_EXHAUSTED_LEASE_ERROR_CODE,
                error_message=_EXHAUSTED_LEASE_ERROR_MESSAGE,
                actor=actor,
                job_id=job["id"],
            )
            investigation = (
                i0_failure.get("investigation") if i0_failure else None
            )
            recovered.append(
                {
                    "job": job,
                    "module_run": module_run,
                    "investigation": investigation,
                    "i0_finalized": i0_failure is not None,
                    "i0_descendant_counts": (
                        i0_failure.get("descendant_counts") if i0_failure else {}
                    ),
                }
            )
        return recovered

    def claim_job(self, *, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        """Recover terminal expired leases, then lease one runnable job."""

        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")
        lease_token = uuid.uuid4()
        with self.transaction() as connection:
            self._recover_cancel_requested_jobs(
                connection,
                actor=worker_id,
                limit=100,
            )
            self._recover_exhausted_expired_jobs(
                connection,
                actor=worker_id,
                limit=100,
            )
            job = self._row(
                connection.execute(
                    text(build_claim_job_sql(self.schema, self.environment)),
                    {
                        "worker_id": worker_id,
                        "lease_token": lease_token,
                        "lease_seconds": lease_seconds,
                    },
                )
            )
            if job is None:
                return None
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.module_runs
                    SET status = 'running',
                        attempt_no = :attempt_no,
                        started_at = COALESCE(started_at, now()),
                        updated_at = now()
                    WHERE id = :module_run_id
                    """
                ),
                {
                    "module_run_id": job["module_run_id"],
                    "attempt_no": job["attempt_count"],
                },
            )
            return job

    def heartbeat_job(
        self,
        *,
        job_id: uuid.UUID | str,
        worker_id: str,
        lease_token: uuid.UUID | str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须大于 0")
        with self.transaction() as connection:
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.jobs
                        SET heartbeat_at = now(),
                            lease_expires_at = now()
                                + make_interval(secs => :lease_seconds),
                            updated_at = now()
                        WHERE id = :job_id
                          AND status = 'leased'
                          AND lease_owner = :worker_id
                          AND lease_token = :lease_token
                          AND lease_expires_at > now()
                        RETURNING *
                        """
                    ),
                    {
                        "job_id": _uuid(job_id),
                        "worker_id": worker_id,
                        "lease_token": _uuid(lease_token),
                        "lease_seconds": lease_seconds,
                    },
                )
            )
            if row is None:
                raise LeaseLostError("job lease 已过期、已转移或 token 不匹配")
            return row

    def release_job_lease_for_shutdown(
        self,
        *,
        job_id: uuid.UUID | str,
        worker_id: str,
        lease_token: uuid.UUID | str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Requeue one owned lease when this process is deliberately stopping.

        The interrupted attempt is not charged against ``max_attempts``.  The
        old worker token is cleared atomically, so any late completion from the
        terminating handler loses its CAS while a replacement worker can claim
        the job immediately.  This preserves at-least-once execution without a
        lease-expiry outage after PM2 restarts.
        """

        with self.transaction() as connection:
            locked = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.jobs
                        WHERE id = :job_id
                          AND status = 'leased'
                          AND lease_owner = :worker_id
                          AND lease_token = :lease_token
                        FOR UPDATE
                        """
                    ),
                    {
                        "job_id": _uuid(job_id),
                        "worker_id": worker_id,
                        "lease_token": _uuid(lease_token),
                    },
                )
            )
            if locked is None:
                return {"released": False, "job": None, "module_run": None}
            if locked.get("cancel_requested_at") is not None:
                result = self._cancel_locked_job(
                    connection,
                    locked_job=locked,
                    actor=actor or worker_id,
                )
                return {**result, "released": False}
            job = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.jobs
                        SET status = 'queued', available_at = now(),
                            attempt_count = GREATEST(attempt_count - 1, 0),
                            lease_owner = NULL, lease_token = NULL,
                            lease_expires_at = NULL, heartbeat_at = NULL,
                            last_error = CAST(:last_error AS JSONB),
                            finished_at = NULL, updated_at = now()
                        WHERE id = :job_id
                        RETURNING *
                        """
                    ),
                    {
                        "job_id": locked["id"],
                        "last_error": _json(
                            {
                                "code": "WORKER_SHUTDOWN_REQUEUED",
                                "message": "worker 正常关闭，租约已立即释放",
                            }
                        ),
                    },
                )
            )
            assert job is not None
            module_run = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.module_runs
                        SET status = 'queued',
                            error_code = 'WORKER_SHUTDOWN_REQUEUED',
                            error_message = 'worker 正常关闭，作业等待恢复',
                            retryable = true, completed_at = NULL,
                            updated_at = now()
                        WHERE id = :module_run_id
                        RETURNING *
                        """
                    ),
                    {"module_run_id": job["module_run_id"]},
                )
            )
            if module_run is None:
                raise RepositoryConflictError("释放租约时找不到对应 module run")
            self._insert_event(
                connection,
                investigation_id=module_run["investigation_id"],
                claim_investigation_id=module_run["claim_investigation_id"],
                iteration_id=module_run["iteration_id"],
                module_run_id=module_run["id"],
                event_type="job.requeued_for_worker_shutdown",
                actor=actor or worker_id,
                payload={
                    "job_id": str(job["id"]),
                    "interrupted_attempt": int(locked["attempt_count"]),
                    "restored_attempt_count": int(job["attempt_count"]),
                },
            )
            return {
                "released": True,
                "job": job,
                "module_run": module_run,
                "cancelled": False,
            }

    def complete_job(
        self,
        *,
        job_id: uuid.UUID | str,
        worker_id: str,
        lease_token: uuid.UUID | str,
        output_snapshot: Mapping[str, Any],
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Commit job success, immutable output snapshot and event together."""

        serialized_output = _json(output_snapshot)
        audit = module_run_audit_values(None, output_snapshot)
        with self.transaction() as connection:
            locked = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.jobs
                        WHERE id = :job_id
                          AND status = 'leased'
                          AND lease_owner = :worker_id
                          AND lease_token = :lease_token
                          AND lease_expires_at > now()
                        FOR UPDATE
                        """
                    ),
                    {
                        "job_id": _uuid(job_id),
                        "worker_id": worker_id,
                        "lease_token": _uuid(lease_token),
                    },
                )
            )
            if locked is None:
                raise LeaseLostError("job lease 已过期、已转移或 token 不匹配")
            if locked.get("cancel_requested_at") is not None:
                result = self._cancel_locked_job(
                    connection,
                    locked_job=locked,
                    actor=actor or worker_id,
                )
                return {
                    **result,
                    "report_run": None,
                    "report_job": None,
                }
            job = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.jobs
                        SET status = 'succeeded', finished_at = now(),
                            lease_owner = NULL, lease_token = NULL,
                            lease_expires_at = NULL, updated_at = now()
                        WHERE id = :job_id
                          AND cancel_requested_at IS NULL
                        RETURNING *
                        """
                    ),
                    {"job_id": locked["id"]},
                )
            )
            if job is None:
                raise RepositoryConflictError(
                    "取消请求已持久化，拒绝把 job 写为 succeeded"
                )
            module_run = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.module_runs
                        SET status = 'succeeded',
                            output_snapshot = CAST(:output_snapshot AS JSONB),
                            output_sha256 = :output_sha256,
                            effective_mode = COALESCE(
                                :effective_mode, effective_mode, requested_mode
                            ),
                            actual_provider = COALESCE(
                                :actual_provider, actual_provider
                            ),
                            network_used = COALESCE(:network_used, network_used),
                            used_target_images = COALESCE(
                                :used_target_images, used_target_images
                            ),
                            used_document_images = COALESCE(
                                :used_document_images, used_document_images
                            ),
                            model_version = COALESCE(
                                :model_version, model_version
                            ),
                            completed_at = now(), updated_at = now(),
                            error_code = NULL, error_message = NULL,
                            retryable = NULL
                        WHERE id = :module_run_id
                        RETURNING *
                        """
                    ),
                    {
                        "module_run_id": job["module_run_id"],
                        "output_snapshot": serialized_output,
                        "output_sha256": audit["output_sha256"],
                        "effective_mode": audit["effective_mode"],
                        "actual_provider": audit["actual_provider"],
                        "network_used": audit["network_used"],
                        "used_target_images": audit["used_target_images"],
                        "used_document_images": audit["used_document_images"],
                        "model_version": audit["model_version"],
                    },
                )
            )
            assert module_run is not None
            self._insert_event(
                connection,
                investigation_id=module_run["investigation_id"],
                claim_investigation_id=module_run["claim_investigation_id"],
                iteration_id=module_run["iteration_id"],
                module_run_id=module_run["id"],
                event_type="job.succeeded",
                actor=actor or worker_id,
                payload={"job_id": str(job["id"]), "attempt": job["attempt_count"]},
            )
            report_run: dict[str, Any] | None = None
            report_job: dict[str, Any] | None = None
            if module_run["module_code"] in {
                "I0_ORCHESTRATE",
                "I0_INVALIDITY_WORKFLOW",
                "HUMAN_REVIEW_APPLY",
            }:
                investigation = self._row(
                    connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.investigations
                            WHERE id = :investigation_id
                            FOR UPDATE
                            """
                        ),
                        {"investigation_id": module_run["investigation_id"]},
                    )
                )
                if (
                    investigation is not None
                    and str(investigation["status"])
                    in _REPORTABLE_INVESTIGATION_STATUSES
                ):
                    source_state_version = int(investigation["state_version"])
                    source_review_revision = int(
                        investigation.get("review_revision") or 0
                    )
                    report_key = (
                        f"report:v1:state:{source_state_version}:"
                        f"review:{source_review_revision}"
                    )
                    report_run = self._row(
                        connection.execute(
                            text(
                                f"""
                                SELECT * FROM {self.schema}.module_runs
                                WHERE investigation_id = :investigation_id
                                  AND module_code = 'I5_REPORT'
                                  AND idempotency_key = :idempotency_key
                                """
                            ),
                            {
                                "investigation_id": investigation["id"],
                                "idempotency_key": report_key,
                            },
                        )
                    )
                    if report_run is None:
                        report_snapshot_id = _uuid()
                        report_input = {
                            "kind": "report_snapshot",
                            "investigation_id": str(investigation["id"]),
                            "source_state_version": source_state_version,
                            "source_review_revision": source_review_revision,
                            "report_snapshot_id": str(report_snapshot_id),
                            "contract_version": "v1",
                        }
                        serialized = _json(report_input)
                        report_run = self._insert_module_run(
                            connection,
                            module_run_id=_uuid(),
                            investigation_id=investigation["id"],
                            claim_investigation_id=None,
                            iteration_id=None,
                            module_code="I5_REPORT",
                            contract_version="v1",
                            input_snapshot=serialized,
                            input_sha256=hashlib.sha256(
                                serialized.encode("utf-8")
                            ).hexdigest(),
                            idempotency_key=report_key,
                            status="queued",
                            model_version=None,
                            prompt_version=None,
                            rule_version="report-data-v1",
                        )
                        report_job = self._insert_job(
                            connection,
                            job_id=_uuid(),
                            module_run_id=report_run["id"],
                            payload=report_input,
                            priority=0,
                            max_attempts=3,
                            available_at=None,
                        )
                        self._insert_event(
                            connection,
                            investigation_id=investigation["id"],
                            module_run_id=report_run["id"],
                            event_type="report.queued",
                            actor=actor or worker_id,
                            payload={
                                "report_snapshot_id": str(report_snapshot_id),
                                "source_state_version": source_state_version,
                                "source_review_revision": source_review_revision,
                                "trigger_module_run_id": str(module_run["id"]),
                            },
                        )
            return {
                "job": job,
                "module_run": module_run,
                "report_run": report_run,
                "report_job": report_job,
            }

    def fail_job(
        self,
        *,
        job_id: uuid.UUID | str,
        worker_id: str,
        lease_token: uuid.UUID | str,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_delay_seconds: int = 0,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Persist failure and either retry or terminally fail in one transaction."""

        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds 不能小于 0")
        with self.transaction() as connection:
            locked = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.jobs
                        WHERE id = :job_id
                          AND status = 'leased'
                          AND lease_owner = :worker_id
                          AND lease_token = :lease_token
                          AND lease_expires_at > now()
                        FOR UPDATE
                        """
                    ),
                    {
                        "job_id": _uuid(job_id),
                        "worker_id": worker_id,
                        "lease_token": _uuid(lease_token),
                    },
                )
            )
            if locked is None:
                raise LeaseLostError("job lease 已过期、已转移或 token 不匹配")
            if locked.get("cancel_requested_at") is not None:
                return self._cancel_locked_job(
                    connection,
                    locked_job=locked,
                    actor=actor or worker_id,
                )

            will_retry = bool(retryable and locked["attempt_count"] < locked["max_attempts"])
            next_status = "queued" if will_retry else "failed"
            job = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.jobs
                        SET status = :status,
                            available_at = CASE WHEN :will_retry
                                THEN now() + make_interval(secs => :retry_delay_seconds)
                                ELSE available_at END,
                            lease_owner = NULL, lease_token = NULL,
                            lease_expires_at = NULL,
                            last_error = CAST(:last_error AS JSONB),
                            finished_at = CASE WHEN :will_retry THEN NULL ELSE now() END,
                            updated_at = now()
                        WHERE id = :job_id
                        RETURNING *
                        """
                    ),
                    {
                        "job_id": locked["id"],
                        "status": next_status,
                        "will_retry": will_retry,
                        "retry_delay_seconds": retry_delay_seconds,
                        "last_error": _json(
                            {"code": error_code, "message": error_message}
                        ),
                    },
                )
            )
            assert job is not None
            module_status = "queued" if will_retry else "failed"
            module_run = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.module_runs
                        SET status = :status, error_code = :error_code,
                            error_message = :error_message, retryable = :retryable,
                            completed_at = CASE WHEN :will_retry THEN NULL ELSE now() END,
                            updated_at = now()
                        WHERE id = :module_run_id
                        RETURNING *
                        """
                    ),
                    {
                        "module_run_id": locked["module_run_id"],
                        "status": module_status,
                        "error_code": error_code,
                        "error_message": error_message,
                        "retryable": retryable,
                        "will_retry": will_retry,
                    },
                )
            )
            assert module_run is not None
            if not will_retry:
                self._mark_human_review_action_terminal(
                    connection,
                    module_run=module_run,
                    status="failed",
                    error_code=error_code,
                    error_message=error_message,
                )
            self._insert_event(
                connection,
                investigation_id=module_run["investigation_id"],
                claim_investigation_id=module_run["claim_investigation_id"],
                iteration_id=module_run["iteration_id"],
                module_run_id=module_run["id"],
                event_type="job.retry_scheduled" if will_retry else "job.failed",
                actor=actor or worker_id,
                payload={
                    "job_id": str(job["id"]),
                    "attempt": job["attempt_count"],
                    "error_code": error_code,
                    "retryable": retryable,
                },
            )
            i0_failure = None
            if not will_retry:
                i0_failure = self._finalize_i0_terminal_failure(
                    connection,
                    module_run=module_run,
                    error_code=error_code,
                    error_message=error_message,
                    actor=actor or worker_id,
                    job_id=job["id"],
                )
            return {
                "job": job,
                "module_run": module_run,
                "will_retry": will_retry,
                "investigation": (
                    i0_failure.get("investigation") if i0_failure else None
                ),
                "i0_finalized": i0_failure is not None,
                "i0_descendant_counts": (
                    i0_failure.get("descendant_counts") if i0_failure else {}
                ),
            }

    def get_job(self, job_id: uuid.UUID | str) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(f"SELECT * FROM {self.schema}.jobs WHERE id = :id"),
                    {"id": _uuid(job_id)},
                )
            )

    def complete_human_review_apply(
        self,
        *,
        review_action_id: uuid.UUID | str,
        expected_review_revision: int,
        checkpoint: Mapping[str, Any],
        qualification_results: Sequence[Mapping[str, Any]],
        comparison_results: Sequence[Mapping[str, Any]],
        closest_prior_art_results: Sequence[Mapping[str, Any]],
        combination_results: Sequence[Mapping[str, Any]],
        gap_frontier_results: Sequence[Mapping[str, Any]],
        claim_outcomes: Mapping[str, Mapping[str, Any]],
        investigation_status: str,
        actor: str = "HUMAN_REVIEW_APPLY",
    ) -> dict[str, Any]:
        """Atomically append derived rows and publish a rebuilt checkpoint."""

        action_uuid = _uuid(review_action_id)
        checkpoint_payload = json.loads(
            json.dumps(checkpoint, ensure_ascii=False, default=str)
        )
        with self.transaction() as connection:
            action = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.human_review_actions
                        WHERE id = :id FOR UPDATE
                        """
                    ),
                    {"id": action_uuid},
                )
            )
            if action is None:
                raise RepositoryConflictError("human review action 不存在")
            if str(action["status"]) == "applied":
                return {
                    "action": action,
                    "investigation": self._row(
                        connection.execute(
                            text(
                                f"SELECT * FROM {self.schema}.investigations WHERE id = :id"
                            ),
                            {"id": action["investigation_id"]},
                        )
                    ),
                    "idempotent_replay": True,
                }
            if str(action["status"]) not in {"queued", "running"}:
                raise RepositoryConflictError(
                    f"human review action 状态 {action['status']} 不允许 apply"
                )
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.investigations
                        WHERE id = :id FOR UPDATE
                        """
                    ),
                    {"id": action["investigation_id"]},
                )
            )
            assert investigation is not None
            if (
                int(investigation.get("review_revision") or 0)
                != expected_review_revision
                or expected_review_revision
                != int(action["resulting_review_revision"])
            ):
                raise RepositoryConflictError(
                    "apply 时 review_revision 已变化，拒绝提交过期重算"
                )
            if int(investigation["state_version"]) != int(
                action["resulting_investigation_state_version"]
            ):
                raise RepositoryConflictError(
                    "apply 时 investigation state_version 已变化"
                )
            if int(investigation.get("last_applied_review_action_seq") or 0) >= int(
                action["action_seq"]
            ):
                raise RepositoryConflictError(
                    "review action sequence 已被更新版本覆盖"
                )
            target = action.get("target_snapshot") or {}
            target_claim_ids = [
                _uuid(value)
                for value in (
                    target.get("claim_investigation_ids")
                    if isinstance(target, Mapping)
                    else []
                )
            ]
            claims = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.claim_investigations
                        WHERE investigation_id = :investigation_id
                          AND id = ANY(CAST(:claim_ids AS UUID[]))
                        ORDER BY id FOR UPDATE
                        """
                    ),
                    {
                        "investigation_id": investigation["id"],
                        "claim_ids": [str(item) for item in target_claim_ids],
                    },
                ).mappings()
            ]
            expected_claim_versions = {
                str(key): int(value)
                for key, value in (
                    action.get("resulting_claim_state_versions") or {}
                ).items()
            }
            if set(expected_claim_versions) != {str(row["id"]) for row in claims}:
                raise RepositoryConflictError("apply claim scope 与 action 不一致")
            if any(
                int(row["state_version"])
                != expected_claim_versions[str(row["id"])]
                for row in claims
            ):
                raise RepositoryConflictError(
                    "apply 时 claim state_version 已变化，拒绝过期 checkpoint"
                )
            target_claim_id_strings = {str(item) for item in target_claim_ids}

            def result_claim_scope(
                values: Sequence[Mapping[str, Any]],
            ) -> set[str]:
                return {
                    str(_uuid(value["claim_investigation_id"]))
                    for value in values
                }

            if result_claim_scope(qualification_results) != target_claim_id_strings:
                raise RepositoryConflictError("qualification result claim scope 不完整")
            if len(qualification_results) != len(target_claim_id_strings):
                raise RepositoryConflictError("qualification result claim 重复")
            if result_claim_scope(comparison_results) != target_claim_id_strings:
                raise RepositoryConflictError("comparison result claim scope 不完整")
            if len(comparison_results) != len(target_claim_id_strings):
                raise RepositoryConflictError("comparison result claim 重复")
            if result_claim_scope(gap_frontier_results) != target_claim_id_strings:
                raise RepositoryConflictError("gap frontier claim scope 不完整")
            if len(gap_frontier_results) != len(target_claim_id_strings):
                raise RepositoryConflictError("gap frontier result claim 重复")
            if set(claim_outcomes) != target_claim_id_strings:
                raise RepositoryConflictError("claim outcome scope 不完整")
            if not result_claim_scope(closest_prior_art_results).issubset(
                target_claim_id_strings
            ):
                raise RepositoryConflictError("D1 result 包含 action 外 claim")
            if len(closest_prior_art_results) != len(
                result_claim_scope(closest_prior_art_results)
            ):
                raise RepositoryConflictError("D1 result claim 重复")
            if not result_claim_scope(combination_results).issubset(
                result_claim_scope(closest_prior_art_results)
            ):
                raise RepositoryConflictError("combination result 缺少同 claim D1")
            if len(combination_results) != len(result_claim_scope(combination_results)):
                raise RepositoryConflictError("combination result claim 重复")
            document_id = _uuid(target.get("document_id"))
            document_version_id = _uuid(target.get("document_version_id"))
            document = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.documents
                        WHERE id = :document_id
                          AND investigation_id = :investigation_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "document_id": document_id,
                        "investigation_id": investigation["id"],
                    },
                )
            )
            version = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.document_versions
                        WHERE id = :version_id AND document_id = :document_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "version_id": document_version_id,
                        "document_id": document_id,
                    },
                )
            )
            if document is None or version is None:
                raise RepositoryConflictError("apply document version 已丢失")

            persisted_qualifications: list[dict[str, Any]] = []
            for result in qualification_results:
                claim_id = _uuid(result["claim_investigation_id"])
                if claim_id not in target_claim_ids:
                    raise RepositoryConflictError(
                        "qualification result 包含 action 外 claim"
                    )
                latest = self._row(
                    connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.document_qualifications
                            WHERE document_id = :document_id
                              AND claim_investigation_id = :claim_id
                              AND limitation_id IS NULL
                            ORDER BY assessment_version DESC,
                                     created_at DESC, id DESC
                            LIMIT 1 FOR UPDATE
                            """
                        ),
                        {"document_id": document_id, "claim_id": claim_id},
                    )
                )
                eligibility = dict(result.get("eligibility") or {})
                critical_date = result.get("critical_date")
                if isinstance(critical_date, str):
                    critical_date = date.fromisoformat(critical_date)
                if not isinstance(critical_date, date):
                    raise RepositoryConflictError(
                        "qualification result 缺少 claim critical_date"
                    )

                def parsed_date(name: str) -> date | None:
                    value = result.get(name)
                    if isinstance(value, datetime):
                        return value.date()
                    if isinstance(value, date):
                        return value
                    if isinstance(value, str) and value:
                        return date.fromisoformat(value[:10])
                    return None

                persisted = self._upsert_document_qualification(
                    connection,
                    document_id=document_id,
                    document_version_id=document_version_id,
                    claim_investigation_id=claim_id,
                    limitation_id=None,
                    critical_date=critical_date,
                    earliest_priority_date=parsed_date("priority_date"),
                    filing_date=parsed_date("filing_date"),
                    publication_date=parsed_date("publication_date"),
                    public_availability_date=parsed_date(
                        "public_availability_date"
                    ),
                    eligibility_type=str(eligibility.get("category") or "unknown"),
                    novelty_eligible=bool(eligibility.get("novelty_eligible")),
                    inventive_step_eligible=bool(
                        eligibility.get(
                            "inventive_step_eligible",
                            eligibility.get("inventive_eligible", False),
                        )
                    ),
                    verification_status=(
                        "needs_human_review"
                        if eligibility.get("requires_human_review")
                        else (
                            "excluded"
                            if str(result.get("decision")) == "exclude"
                            else "verified"
                        )
                    ),
                    verification_reason=str(
                        eligibility.get("explanation")
                        or ";".join(eligibility.get("reason_codes") or [])
                        or result.get("decision")
                        or "human review"
                    ),
                    date_rule_version=str(
                        result.get("date_rule_version") or "date-rules-v1"
                    ),
                    human_confirmed=bool(result.get("human_confirmed")),
                    assessment_version=(
                        int(latest["assessment_version"]) + 1 if latest else 1
                    ),
                    qualification_id=uuid.uuid4(),
                    reason_codes=tuple(eligibility.get("reason_codes") or ()),
                    evidence_references=(
                        result.get("evidence_references")
                        if isinstance(result.get("evidence_references"), Mapping)
                        else {}
                    ),
                    supersedes_id=latest["id"] if latest else None,
                    review_action_id=action_uuid,
                )
                persisted_qualifications.append(persisted)
                checkpoint_claim_key = str(result.get("checkpoint_claim_id") or "")
                document_key = str(result.get("document_key") or document["canonical_key"])
                claim_checkpoint = (
                    checkpoint_payload.get("claims", {}).get(checkpoint_claim_key)
                    if isinstance(checkpoint_payload.get("claims"), Mapping)
                    else None
                )
                if isinstance(claim_checkpoint, dict):
                    qualification_payload = dict(eligibility)
                    qualification_payload.update(
                        {
                            "repository_qualification_id": str(persisted["id"]),
                            "assessment_version": int(
                                persisted["assessment_version"]
                            ),
                            "document_version_id": str(document_version_id),
                        }
                    )
                    claim_checkpoint.setdefault("date_qualifications", {})[
                        document_key
                    ] = qualification_payload

            persisted_i4s_runs: list[dict[str, Any]] = []
            supported_disclosure_by_claim: dict[str, bool] = {}
            for result in comparison_results:
                claim_id = _uuid(result["claim_investigation_id"])
                if claim_id not in target_claim_ids:
                    raise RepositoryConflictError(
                        "comparison result 包含 action 外 claim"
                    )
                comparison = dict(result.get("comparison") or {})
                supported_disclosure_by_claim[str(claim_id)] = (
                    str(
                        comparison.get("structural_review_status")
                        or "not_needed"
                    )
                    != "model_error"
                    and any(
                    str(disclosure.get("status") or "")
                    in {
                        "explicit",
                        "direct_and_unambiguous",
                        "necessarily_implicit",
                    }
                    and float(disclosure.get("confidence") or 0) >= 0.7
                    and bool(str(disclosure.get("evidence_quote") or "").strip())
                    and bool(
                        str(disclosure.get("evidence_location") or "").strip()
                    )
                    for disclosure in comparison.get("disclosures") or []
                    if isinstance(disclosure, Mapping)
                    )
                )
                if bool(result.get("reused")):
                    continue
                run_id = uuid.uuid5(action_uuid, f"i4s:{claim_id}:{document_version_id}")
                input_snapshot = {
                    "kind": "human_review_i4s",
                    "analysis_rule_version": I4S_RULE_VERSION,
                    "prompt_version": I4S_PROMPT_VERSION,
                    "review_action_id": str(action_uuid),
                    "claim_investigation_id": str(claim_id),
                    "document_id": str(document_id),
                    "document_version_id": str(document_version_id),
                    "document_content_sha256": str(version["content_sha256"]),
                    "limitation_set_sha256": result.get("limitation_set_sha256"),
                    "target_source_sha256": result.get("target_source_sha256"),
                }
                serialized_input = _json(input_snapshot)
                module_run = self._insert_module_run(
                    connection,
                    module_run_id=run_id,
                    investigation_id=investigation["id"],
                    claim_investigation_id=claim_id,
                    iteration_id=None,
                    module_code="I4_S_SINGLE_REFERENCE",
                    contract_version="v2",
                    input_snapshot=serialized_input,
                    input_sha256=hashlib.sha256(
                        serialized_input.encode("utf-8")
                    ).hexdigest(),
                    idempotency_key=(
                        f"human-review:{action_uuid}:{claim_id}:{document_version_id}"
                    ),
                    status="running",
                    model_version=comparison.get("model"),
                    prompt_version=I4S_PROMPT_VERSION,
                    rule_version=I4S_RULE_VERSION,
                    requested_mode="live",
                    effective_mode="live",
                    actual_provider="human_import",
                    network_used=False,
                    used_target_images=comparison.get("used_target_images"),
                    used_document_images=comparison.get("used_document_images"),
                )
                limitation_rows = {
                    str(row["feature_key"]): row
                    for row in connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.claim_limitations
                            WHERE claim_investigation_id = :claim_id
                            """
                        ),
                        {"claim_id": claim_id},
                    ).mappings()
                }
                source_id = result.get("document_source_id")
                for disclosure in comparison.get("disclosures") or []:
                    limitation = limitation_rows.get(str(disclosure.get("feature_id")))
                    if limitation is None:
                        raise RepositoryConflictError(
                            "I4-S disclosure 包含未知 limitation"
                        )
                    status = str(disclosure.get("status") or "uncertain")
                    excerpt = str(disclosure.get("evidence_quote") or "") or None
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO {self.schema}.feature_disclosures (
                                id, module_run_id, claim_investigation_id,
                                limitation_id, document_id, document_version_id,
                                document_source_id, disclosure_status, excerpt,
                                locator, excerpt_sha256, analysis, model_version,
                                prompt_version, rule_version
                            ) VALUES (
                                :id, :module_run_id, :claim_id, :limitation_id,
                                :document_id, :document_version_id,
                                :document_source_id, :disclosure_status, :excerpt,
                                CAST(:locator AS JSONB), :excerpt_sha256,
                                CAST(:analysis AS JSONB), :model_version,
                                '{I4S_PROMPT_VERSION}', '{I4S_RULE_VERSION}'
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "module_run_id": run_id,
                            "claim_id": claim_id,
                            "limitation_id": limitation["id"],
                            "document_id": document_id,
                            "document_version_id": document_version_id,
                            "document_source_id": (
                                _uuid(source_id) if source_id else None
                            ),
                            "disclosure_status": status,
                            "excerpt": excerpt,
                            "locator": _json(
                                {"location": disclosure.get("evidence_location")}
                            ),
                            "excerpt_sha256": (
                                hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
                                if excerpt
                                else None
                            ),
                            "analysis": _json(
                                {
                                    "reasoning": disclosure.get("reasoning"),
                                    "confidence": disclosure.get("confidence"),
                                    "review_action_id": str(action_uuid),
                                    "target_structural_role": disclosure.get(
                                        "target_structural_role"
                                    ),
                                    "reference_structure_mapping": disclosure.get(
                                        "reference_structure_mapping"
                                    ),
                                    "mapping_basis": disclosure.get("mapping_basis"),
                                    "structural_evidence": disclosure.get(
                                        "structural_evidence"
                                    )
                                    or [],
                                    "integrated_structure_mapping": bool(
                                        disclosure.get(
                                            "integrated_structure_mapping", False
                                        )
                                    ),
                                    "structural_search_summary": disclosure.get(
                                        "structural_search_summary"
                                    ),
                                    "necessity_chain": disclosure.get(
                                        "necessity_chain"
                                    )
                                    or [],
                                    "reasonable_alternatives_excluded": bool(
                                        disclosure.get(
                                            "reasonable_alternatives_excluded", False
                                        )
                                    ),
                                    "alternative_path_analysis": disclosure.get(
                                        "alternative_path_analysis"
                                    ),
                                    "target_mechanism_summary": comparison.get(
                                        "target_mechanism_summary"
                                    ),
                                    "reference_mechanism_summary": comparison.get(
                                        "reference_mechanism_summary"
                                    ),
                                    "structural_review_attempted": comparison.get(
                                        "structural_review_attempted", False
                                    ),
                                    "structural_review_performed": comparison.get(
                                        "structural_review_performed", False
                                    ),
                                    "structural_review_status": comparison.get(
                                        "structural_review_status", "not_needed"
                                    ),
                                    "structural_review_error_code": comparison.get(
                                        "structural_review_error_code", ""
                                    ),
                                    "structural_review_used_target_images": comparison.get(
                                        "structural_review_used_target_images", 0
                                    ),
                                    "structural_review_used_document_images": comparison.get(
                                        "structural_review_used_document_images", 0
                                    ),
                                    "analysis_pass_count": comparison.get(
                                        "analysis_pass_count", 1
                                    ),
                                    "analysis_rule_version": comparison.get(
                                        "analysis_rule_version",
                                        I4S_RULE_VERSION,
                                    ),
                                }
                            ),
                            "model_version": comparison.get("model"),
                        },
                    )
                serialized_output = _json(comparison)
                derived_run_status = (
                    "partial"
                    if str(
                        comparison.get("structural_review_status")
                        or "not_needed"
                    )
                    == "model_error"
                    else "succeeded"
                )
                module_run = self._row(
                    connection.execute(
                        text(
                            f"""
                            UPDATE {self.schema}.module_runs
                            SET status = :status,
                                output_snapshot = CAST(:output AS JSONB),
                                output_sha256 = :output_sha256,
                                completed_at = now(), updated_at = now()
                            WHERE id = :id RETURNING *
                            """
                        ),
                        {
                            "id": run_id,
                            "status": derived_run_status,
                            "output": serialized_output,
                            "output_sha256": hashlib.sha256(
                                serialized_output.encode("utf-8")
                            ).hexdigest(),
                        },
                    )
                )
                assert module_run is not None
                persisted_i4s_runs.append(module_run)

            qualification_by_claim = {
                str(_uuid(result["claim_investigation_id"])): result
                for result in qualification_results
            }
            if any(
                supported_disclosure_by_claim.get(claim_id, False)
                and bool(
                    (qualification_by_claim[claim_id].get("eligibility") or {}).get(
                        "novelty_eligible"
                    )
                )
                for claim_id in target_claim_id_strings
            ):
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.documents
                        SET evidence_level = 'qualified_evidence', updated_at = now()
                        WHERE id = :id
                        """
                    ),
                    {"id": document_id},
                )

            # Existing D1 rows remain immutable evidence selections but cease to
            # be current until the rebuilt/continued workflow selects a new one.
            connection.execute(
                text(
                    f"""
                    UPDATE {self.schema}.closest_prior_art_versions
                    SET is_current = false
                    WHERE claim_investigation_id = ANY(CAST(:claim_ids AS UUID[]))
                      AND is_current
                    """
                ),
                {"claim_ids": [str(item) for item in target_claim_ids]},
            )

            persisted_d1_by_claim: dict[str, dict[str, Any]] = {}
            for result in closest_prior_art_results:
                claim_id = _uuid(result["claim_investigation_id"])
                claim_key = str(claim_id)
                selection = dict(result.get("selection") or {})
                if not result.get("document_id") or not result.get(
                    "document_version_id"
                ):
                    raise RepositoryConflictError(
                        "human review D1 必须绑定 document/version"
                    )
                selected_document_id = _uuid(result["document_id"])
                selected_version_id = _uuid(result["document_version_id"])
                owned_version = connection.execute(
                    text(
                        f"""
                        SELECT 1
                        FROM {self.schema}.documents AS document
                        JOIN {self.schema}.document_versions AS version
                          ON version.document_id = document.id
                         AND version.id = :version_id
                        WHERE document.id = :document_id
                          AND document.investigation_id = :investigation_id
                        FOR UPDATE OF document, version
                        """
                    ),
                    {
                        "document_id": selected_document_id,
                        "version_id": selected_version_id,
                        "investigation_id": investigation["id"],
                    },
                ).first()
                if owned_version is None:
                    raise RepositoryConflictError(
                        "human review D1 的 document/version 绑定无效"
                    )
                previous_selection = self._row(
                    connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.closest_prior_art_versions
                            WHERE claim_investigation_id = :claim_id
                            ORDER BY version_no DESC, created_at DESC, id DESC
                            LIMIT 1 FOR UPDATE
                            """
                        ),
                        {"claim_id": claim_id},
                    )
                )
                selection_id = uuid.uuid5(action_uuid, f"d1:{claim_id}")
                persisted_selection = self._row(
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO {self.schema}.closest_prior_art_versions (
                                id, claim_investigation_id, iteration_id,
                                document_id, document_version_id, version_no,
                                metrics, rationale, selected_by, review_action_id,
                                supersedes_id, is_current
                            ) VALUES (
                                :id, :claim_id, NULL, :document_id,
                                :document_version_id, :version_no,
                                CAST(:metrics AS JSONB), CAST(:rationale AS JSONB),
                                'HUMAN_REVIEW_APPLY', :review_action_id,
                                :supersedes_id, true
                            )
                            RETURNING *
                            """
                        ),
                        {
                            "id": selection_id,
                            "claim_id": claim_id,
                            "document_id": selected_document_id,
                            "document_version_id": selected_version_id,
                            "version_no": (
                                int(previous_selection["version_no"]) + 1
                                if previous_selection
                                else 1
                            ),
                            "metrics": _json(selection.get("score") or {}),
                            "rationale": _json(
                                {
                                    "selection_reason": selection.get(
                                        "selection_reason"
                                    ),
                                    "ranked_candidates": selection.get(
                                        "ranked_candidates"
                                    )
                                    or [],
                                    "uncovered_feature_ids": selection.get(
                                        "uncovered_feature_ids"
                                    )
                                    or [],
                                    "uncertain_feature_ids": selection.get(
                                        "uncertain_feature_ids"
                                    )
                                    or [],
                                    "review_revision": expected_review_revision,
                                }
                            ),
                            "review_action_id": action_uuid,
                            "supersedes_id": (
                                previous_selection["id"]
                                if previous_selection
                                else None
                            ),
                        },
                    )
                )
                assert persisted_selection is not None
                persisted_d1_by_claim[claim_key] = persisted_selection
                checkpoint_claim_key = str(result.get("checkpoint_claim_id") or "")
                claim_checkpoint = (
                    checkpoint_payload.get("claims", {}).get(checkpoint_claim_key)
                    if isinstance(checkpoint_payload.get("claims"), Mapping)
                    else None
                )
                if isinstance(claim_checkpoint, dict):
                    current = dict(
                        claim_checkpoint.get("current_closest_prior_art") or selection
                    )
                    current["repository_selection_id"] = str(
                        persisted_selection["id"]
                    )
                    current["review_action_id"] = str(action_uuid)
                    claim_checkpoint["current_closest_prior_art"] = current

            persisted_i4i_runs: list[dict[str, Any]] = []
            persisted_combinations: list[dict[str, Any]] = []
            for result in combination_results:
                claim_id = _uuid(result["claim_investigation_id"])
                claim_key = str(claim_id)
                selection_row = persisted_d1_by_claim.get(claim_key)
                if selection_row is None:
                    raise RepositoryConflictError(
                        "human review I4-I 缺少同一重算事务的新 D1"
                    )
                assessment = dict(result.get("assessment") or {})
                document_ids = [_uuid(item) for item in result.get("document_ids") or []]
                version_ids = [
                    _uuid(item) for item in result.get("document_version_ids") or []
                ]
                if (
                    len(document_ids) < 2
                    or len(document_ids) != len(version_ids)
                    or len(set(zip(document_ids, version_ids))) != len(document_ids)
                ):
                    raise RepositoryConflictError(
                        "human review I4-I 必须绑定至少两份一一对应的 document version"
                    )
                actual_pairs = {
                    (row["document_id"], row["id"])
                    for row in connection.execute(
                        text(
                            f"""
                            SELECT version.document_id, version.id
                            FROM {self.schema}.document_versions AS version
                            JOIN {self.schema}.documents AS document
                              ON document.id = version.document_id
                            WHERE document.investigation_id = :investigation_id
                              AND version.id = ANY(CAST(:version_ids AS UUID[]))
                            FOR UPDATE OF version
                            """
                        ),
                        {
                            "investigation_id": investigation["id"],
                            "version_ids": [str(item) for item in version_ids],
                        },
                    ).mappings()
                }
                if actual_pairs != set(zip(document_ids, version_ids)):
                    raise RepositoryConflictError(
                        "human review I4-I document/version 绑定无效"
                    )
                run_id = uuid.uuid5(action_uuid, f"i4i:{claim_id}")
                input_snapshot = {
                    "kind": "human_review_i4i",
                    "review_action_id": str(action_uuid),
                    "review_revision": expected_review_revision,
                    "claim_investigation_id": claim_key,
                    "closest_prior_art_version_id": str(selection_row["id"]),
                    "document_ids": [str(item) for item in document_ids],
                    "document_version_ids": [str(item) for item in version_ids],
                }
                serialized_input = _json(input_snapshot)
                run = self._insert_module_run(
                    connection,
                    module_run_id=run_id,
                    investigation_id=investigation["id"],
                    claim_investigation_id=claim_id,
                    iteration_id=None,
                    module_code="I4_I_INVENTIVE_STEP",
                    contract_version="v1",
                    input_snapshot=serialized_input,
                    input_sha256=hashlib.sha256(
                        serialized_input.encode("utf-8")
                    ).hexdigest(),
                    idempotency_key=f"human-review-i4i:{action_uuid}:{claim_id}",
                    status="running",
                    model_version=assessment.get("model"),
                    prompt_version="i4-i-v1",
                    rule_version="inventive-step-v1",
                    requested_mode="live",
                    effective_mode="live",
                    actual_provider=None,
                    network_used=True,
                    used_target_images=assessment.get("used_target_images"),
                    used_document_images=assessment.get("used_document_images"),
                )
                combination_id = uuid.uuid5(action_uuid, f"combination:{claim_id}")
                persisted_combination = self._row(
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO {self.schema}.combinations (
                                id, claim_investigation_id, iteration_id,
                                module_run_id, closest_prior_art_version_id,
                                document_ids, document_version_ids, status,
                                coverage_complete, motivation_status, analysis,
                                review_action_id
                            ) VALUES (
                                :id, :claim_id, NULL, :module_run_id,
                                :selection_id, CAST(:document_ids AS JSONB),
                                CAST(:document_version_ids AS JSONB), :status,
                                :coverage_complete, :motivation_status,
                                CAST(:analysis AS JSONB), :review_action_id
                            )
                            RETURNING *
                            """
                        ),
                        {
                            "id": combination_id,
                            "claim_id": claim_id,
                            "module_run_id": run_id,
                            "selection_id": selection_row["id"],
                            "document_ids": _json(
                                [str(item) for item in document_ids]
                            ),
                            "document_version_ids": _json(
                                [str(item) for item in version_ids]
                            ),
                            "status": (
                                "evidence_complete"
                                if assessment.get("evidence_complete")
                                else "gaps_open"
                            ),
                            "coverage_complete": bool(
                                assessment.get("combined_feature_coverage_complete")
                            ),
                            "motivation_status": str(
                                (
                                    assessment.get("combination_motivation") or {}
                                ).get("status")
                                or "unknown"
                            ),
                            "analysis": _json(assessment),
                            "review_action_id": action_uuid,
                        },
                    )
                )
                assert persisted_combination is not None
                persisted_combinations.append(persisted_combination)
                serialized_output = _json(assessment)
                run = self._row(
                    connection.execute(
                        text(
                            f"""
                            UPDATE {self.schema}.module_runs
                            SET status = 'succeeded',
                                output_snapshot = CAST(:output AS JSONB),
                                output_sha256 = :output_sha256,
                                completed_at = now(), updated_at = now()
                            WHERE id = :id RETURNING *
                            """
                        ),
                        {
                            "id": run_id,
                            "output": serialized_output,
                            "output_sha256": hashlib.sha256(
                                serialized_output.encode("utf-8")
                            ).hexdigest(),
                        },
                    )
                )
                assert run is not None
                persisted_i4i_runs.append(run)

            persisted_gap_rows: list[dict[str, Any]] = []
            for result in gap_frontier_results:
                claim_id = _uuid(result["claim_investigation_id"])
                limitation_rows = {
                    str(row["feature_key"]): dict(row)
                    for row in connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.claim_limitations
                            WHERE claim_investigation_id = :claim_id
                            FOR UPDATE
                            """
                        ),
                        {"claim_id": claim_id},
                    ).mappings()
                }
                normalized_gaps: list[dict[str, Any]] = []
                for raw in result.get("open_gaps") or []:
                    if not isinstance(raw, Mapping):
                        raise RepositoryConflictError(
                            "human review gap frontier 包含非对象"
                        )
                    gap_type = str(raw.get("gap_type") or raw.get("kind") or "")
                    feature_id = str(raw.get("feature_id") or "")
                    limitation = limitation_rows.get(feature_id)
                    if gap_type in {"feature_gap", "evidence_gap"} and limitation is None:
                        raise RepositoryConflictError(
                            f"human review {gap_type} 缺少 canonical limitation"
                        )
                    normalized_gaps.append(
                        {
                            "gap_key": str(
                                raw.get("gap_key") or raw.get("gap_id") or ""
                            ),
                            "limitation_id": (
                                limitation["id"] if limitation is not None else None
                            ),
                            "source_module_run_id": action.get("module_run_id"),
                            "gap_type": gap_type,
                            "status": "open",
                            "description": str(
                                raw.get("description") or raw.get("rationale") or ""
                            ),
                            "search_objective": dict(raw),
                        }
                    )
                previous_gap_rows = [
                    dict(row)
                    for row in connection.execute(
                        text(
                            f"""
                            SELECT * FROM {self.schema}.gap_items
                            WHERE claim_investigation_id = :claim_id
                            ORDER BY gap_key, version_no, created_at, id
                            FOR UPDATE
                            """
                        ),
                        {"claim_id": claim_id},
                    ).mappings()
                ]
                gap_plan = plan_gap_frontier(
                    iteration_no=None,
                    review_revision=expected_review_revision,
                    open_gaps=normalized_gaps,
                    previous_gap_items=previous_gap_rows,
                    default_source_module_run_id=action.get("module_run_id"),
                )
                for item in gap_plan["rows"]:
                    persisted_gap = self._row(
                        connection.execute(
                            text(
                                f"""
                                INSERT INTO {self.schema}.gap_items (
                                    id, iteration_id, review_action_id,
                                    claim_investigation_id, limitation_id,
                                    source_module_run_id, gap_type, gap_key,
                                    gap_fingerprint, version_no, supersedes_id,
                                    status, description, search_objective,
                                    resolved_by_document_id, resolution_evidence,
                                    closed_reason, resolved_at
                                ) VALUES (
                                    :id, NULL, :review_action_id, :claim_id,
                                    :limitation_id, :source_module_run_id,
                                    :gap_type, :gap_key, :gap_fingerprint,
                                    :version_no, :supersedes_id, :status,
                                    :description, CAST(:search_objective AS JSONB),
                                    :resolved_by_document_id,
                                    CAST(:resolution_evidence AS JSONB),
                                    :closed_reason,
                                    CASE WHEN :status = 'open' THEN NULL
                                         ELSE COALESCE(:resolved_at, now()) END
                                )
                                RETURNING *
                                """
                            ),
                            {
                                "id": uuid.uuid4(),
                                "review_action_id": action_uuid,
                                "claim_id": claim_id,
                                "limitation_id": (
                                    _uuid(item["limitation_id"])
                                    if item.get("limitation_id")
                                    else None
                                ),
                                "source_module_run_id": (
                                    _uuid(item["source_module_run_id"])
                                    if item.get("source_module_run_id")
                                    else None
                                ),
                                "gap_type": item["gap_type"],
                                "gap_key": item["gap_key"],
                                "gap_fingerprint": item["gap_fingerprint"],
                                "version_no": item["version_no"],
                                "supersedes_id": (
                                    _uuid(item["supersedes_id"])
                                    if item.get("supersedes_id")
                                    else None
                                ),
                                "status": item["status"],
                                "description": item["description"],
                                "search_objective": _json(
                                    item.get("search_objective") or {}
                                ),
                                "resolved_by_document_id": (
                                    _uuid(item["resolved_by_document_id"])
                                    if item.get("resolved_by_document_id")
                                    else None
                                ),
                                "resolution_evidence": (
                                    _json(item["resolution_evidence"])
                                    if item.get("resolution_evidence") is not None
                                    else None
                                ),
                                "closed_reason": item.get("closed_reason"),
                                "resolved_at": item.get("resolved_at"),
                            },
                        )
                    )
                    assert persisted_gap is not None
                    persisted_gap_rows.append(persisted_gap)
                self._insert_event(
                    connection,
                    investigation_id=investigation["id"],
                    claim_investigation_id=claim_id,
                    module_run_id=action.get("module_run_id"),
                    event_type="gap_frontier.human_review_committed",
                    actor=actor,
                    payload={
                        "review_action_id": str(action_uuid),
                        "review_revision": expected_review_revision,
                        "frontier_fingerprint": gap_plan["frontier_fingerprint"],
                        "open_gap_keys": gap_plan["open_gap_keys"],
                        "closed_gap_keys": gap_plan["closed_gap_keys"],
                        "delta": gap_plan["delta"],
                    },
                )

            resulting_claim_versions: dict[str, int] = {}
            for claim in claims:
                outcome = dict(claim_outcomes.get(str(claim["id"])) or {})
                status = str(outcome.get("status") or "needs_human_review")
                terminal_reason = str(
                    outcome.get("terminal_reason")
                    or "人工事实已应用；可基于重建 checkpoint 继续"
                )
                result_summary = dict(outcome.get("result_summary") or {})
                updated_claim = self._row(
                    connection.execute(
                        text(
                            f"""
                            UPDATE {self.schema}.claim_investigations
                            SET status = :status,
                                terminal_reason = :terminal_reason,
                                result_summary = result_summary || CAST(:summary AS JSONB),
                                state_version = state_version + 1,
                                completed_at = now(), updated_at = now()
                            WHERE id = :id
                              AND state_version = :expected_state_version
                            RETURNING *
                            """
                        ),
                        {
                            "id": claim["id"],
                            "expected_state_version": claim["state_version"],
                            "status": status,
                            "terminal_reason": terminal_reason,
                            "summary": _json(result_summary),
                        },
                    )
                )
                if updated_claim is None:
                    raise RepositoryConflictError(
                        "apply 提交时 claim state_version 已变化"
                    )
                resulting_claim_versions[str(updated_claim["id"])] = int(
                    updated_claim["state_version"]
                )

            checkpoint_payload["review_revision"] = expected_review_revision
            checkpoint_payload["last_applied_review_action_seq"] = int(
                action["action_seq"]
            )
            checkpoint_payload["status"] = investigation_status
            checkpoint_payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            workflow_state = dict(investigation.get("workflow_state") or {})
            workflow_state["invalidity_workflow"] = checkpoint_payload
            workflow_state["stage"] = "human_review_applied"
            workflow_state["last_review_action_id"] = str(action_uuid)
            updated_investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.investigations
                        SET status = :status,
                            workflow_state = CAST(:workflow_state AS JSONB),
                            review_recomputation_required = false,
                            last_applied_review_action_seq = :action_seq,
                            state_version = state_version + 1,
                            completed_at = now(), updated_at = now()
                        WHERE id = :id
                          AND state_version = :expected_state_version
                          AND review_revision = :expected_review_revision
                        RETURNING *
                        """
                    ),
                    {
                        "id": investigation["id"],
                        "expected_state_version": investigation["state_version"],
                        "expected_review_revision": expected_review_revision,
                        "status": investigation_status,
                        "workflow_state": _json(workflow_state),
                        "action_seq": action["action_seq"],
                    },
                )
            )
            if updated_investigation is None:
                raise RepositoryConflictError(
                    "apply 提交时 investigation state/review revision 已变化"
                )
            result_summary = {
                "review_action_id": str(action_uuid),
                "action_type": action["action_type"],
                "action_seq": int(action["action_seq"]),
                "review_revision": expected_review_revision,
                "qualification_ids": [
                    str(row["id"]) for row in persisted_qualifications
                ],
                "i4s_module_run_ids": [
                    str(row["id"]) for row in persisted_i4s_runs
                ],
                "d1_version_ids": [
                    str(row["id"]) for row in persisted_d1_by_claim.values()
                ],
                "i4i_module_run_ids": [
                    str(row["id"]) for row in persisted_i4i_runs
                ],
                "combination_ids": [
                    str(row["id"]) for row in persisted_combinations
                ],
                "gap_item_ids": [str(row["id"]) for row in persisted_gap_rows],
                "reused_i4s_count": sum(
                    1 for row in comparison_results if bool(row.get("reused"))
                ),
                "checkpoint_committed": True,
                "investigation_state_version": int(
                    updated_investigation["state_version"]
                ),
                "claim_state_versions": resulting_claim_versions,
            }
            action = self._row(
                connection.execute(
                    text(
                        f"""
                        UPDATE {self.schema}.human_review_actions
                        SET status = 'applied',
                            resulting_investigation_state_version
                                = :investigation_state_version,
                            resulting_claim_state_versions
                                = CAST(:claim_state_versions AS JSONB),
                            result_summary = CAST(:result_summary AS JSONB),
                            completed_at = now()
                        WHERE id = :id
                        RETURNING *
                        """
                    ),
                    {
                        "id": action_uuid,
                        "investigation_state_version": updated_investigation[
                            "state_version"
                        ],
                        "claim_state_versions": _json(resulting_claim_versions),
                        "result_summary": _json(result_summary),
                    },
                )
            )
            assert action is not None
            self._insert_event(
                connection,
                investigation_id=updated_investigation["id"],
                module_run_id=action["module_run_id"],
                event_type="human_review.applied",
                actor=actor,
                payload=result_summary,
            )
            return {
                "action": action,
                "investigation": updated_investigation,
                "checkpoint": checkpoint_payload,
                "qualifications": persisted_qualifications,
                "i4s_module_runs": persisted_i4s_runs,
                "d1_versions": list(persisted_d1_by_claim.values()),
                "i4i_module_runs": persisted_i4i_runs,
                "combinations": persisted_combinations,
                "gap_items": persisted_gap_rows,
                "idempotent_replay": False,
            }

    def insert_artifact(
        self,
        *,
        investigation_id: uuid.UUID | str,
        artifact_type: str,
        uri: str,
        sha256: str,
        module_run_id: uuid.UUID | str | None = None,
        mime_type: str | None = None,
        byte_size: int | None = None,
        metadata: Mapping[str, Any] | None = None,
        artifact_id: uuid.UUID | str | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        investigation_uuid = _uuid(investigation_id)
        module_run_uuid = _uuid(module_run_id) if module_run_id else None
        with self.transaction() as connection:
            if module_run_uuid is not None:
                owned = connection.execute(
                    text(
                        f"""
                        SELECT 1 FROM {self.schema}.module_runs
                        WHERE id = :module_run_id
                          AND investigation_id = :investigation_id
                        FOR UPDATE
                        """
                    ),
                    {
                        "module_run_id": module_run_uuid,
                        "investigation_id": investigation_uuid,
                    },
                ).first()
                if owned is None:
                    raise RepositoryConflictError(
                        "artifact module run 不属于指定 investigation"
                    )
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.artifacts (
                            id, investigation_id, module_run_id, artifact_type,
                            uri, sha256, mime_type, byte_size, metadata
                        ) VALUES (
                            :id, :investigation_id, :module_run_id,
                            :artifact_type, :uri, :sha256, :mime_type,
                            :byte_size, CAST(:metadata AS JSONB)
                        ) RETURNING *
                        """
                    ),
                    {
                        "id": _uuid(artifact_id),
                        "investigation_id": investigation_uuid,
                        "module_run_id": module_run_uuid,
                        "artifact_type": artifact_type,
                        "uri": uri,
                        "sha256": sha256,
                        "mime_type": mime_type,
                        "byte_size": byte_size,
                        "metadata": _json(metadata),
                    },
                )
            )
            assert row is not None
            self._insert_event(
                connection,
                investigation_id=investigation_uuid,
                module_run_id=module_run_uuid,
                event_type="artifact.inserted",
                actor=actor,
                payload={"artifact_id": str(row["id"]), "type": artifact_type},
            )
            return row

    def create_report_snapshot(
        self,
        *,
        investigation_id: uuid.UUID | str,
        contract_version: str,
        source_state_version: int,
        source_review_revision: int,
        report_snapshot: Mapping[str, Any],
        module_run_id: uuid.UUID | str | None = None,
        report_snapshot_id: uuid.UUID | str | None = None,
        actor: str = "I5_REPORT",
    ) -> dict[str, Any]:
        """Persist one immutable, version-addressed public report JSON snapshot.

        ``snapshot_sha256`` is an API envelope field and is deliberately
        excluded from the canonical bytes to avoid a self-referential hash.
        ``snapshot_hash_scope`` in ReportDataV1 makes this rule explicit.
        """

        investigation_uuid = _uuid(investigation_id)
        run_uuid = _uuid(module_run_id) if module_run_id else None
        canonical = dict(report_snapshot)
        canonical.pop("snapshot_sha256", None)
        try:
            payload_state_version = int(canonical["source_state_version"])
            payload_review_revision = int(canonical["source_review_revision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RepositoryConflictError(
                "报告快照缺少有效 source state/review version"
            ) from exc
        if (
            payload_state_version != source_state_version
            or payload_review_revision != source_review_revision
        ):
            raise RepositoryConflictError(
                "报告正文与快照键的 source state/review version 不一致"
            )
        report_sha256 = _sha256_json(canonical)
        persisted = dict(report_snapshot)
        persisted["snapshot_sha256"] = None
        with self.transaction() as connection:
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.investigations
                        WHERE id = :investigation_id
                        FOR UPDATE
                        """
                    ),
                    {"investigation_id": investigation_uuid},
                )
            )
            if investigation is None:
                raise RepositoryConflictError("investigation 不存在")
            if int(investigation["state_version"]) != source_state_version:
                raise RepositoryConflictError(
                    "报告源 state_version 已变化，拒绝固化过期快照"
                )
            if (
                int(investigation.get("review_revision") or 0)
                != source_review_revision
                or bool(investigation.get("review_recomputation_required"))
            ):
                raise RepositoryConflictError(
                    "报告源 review_revision 已变化或仍待重算，拒绝固化过期快照"
                )
            if run_uuid is not None:
                owned = connection.execute(
                    text(
                        f"""
                        SELECT 1 FROM {self.schema}.module_runs
                        WHERE id = :module_run_id
                          AND investigation_id = :investigation_id
                        """
                    ),
                    {
                        "module_run_id": run_uuid,
                        "investigation_id": investigation_uuid,
                    },
                ).first()
                if owned is None:
                    raise RepositoryConflictError(
                        "I5 module run 不属于指定 investigation"
                    )
            existing = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.report_snapshots
                        WHERE investigation_id = :investigation_id
                          AND contract_version = :contract_version
                          AND source_state_version = :source_state_version
                          AND source_review_revision = :source_review_revision
                        """
                    ),
                    {
                        "investigation_id": investigation_uuid,
                        "contract_version": contract_version,
                        "source_state_version": source_state_version,
                        "source_review_revision": source_review_revision,
                    },
                )
            )
            if existing is not None:
                if existing["report_sha256"] != report_sha256:
                    raise RepositoryConflictError(
                        "同一源版本已经固化为不同的报告快照"
                    )
                return existing
            row = self._row(
                connection.execute(
                    text(
                        f"""
                        INSERT INTO {self.schema}.report_snapshots (
                            id, investigation_id, contract_version,
                            source_state_version, source_review_revision,
                            module_run_id,
                            report_snapshot, report_sha256
                        ) VALUES (
                            :id, :investigation_id, :contract_version,
                            :source_state_version, :source_review_revision,
                            :module_run_id,
                            CAST(:report_snapshot AS JSONB), :report_sha256
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": _uuid(report_snapshot_id),
                        "investigation_id": investigation_uuid,
                        "contract_version": contract_version,
                        "source_state_version": source_state_version,
                        "source_review_revision": source_review_revision,
                        "module_run_id": run_uuid,
                        "report_snapshot": _json(persisted),
                        "report_sha256": report_sha256,
                    },
                )
            )
            assert row is not None
            self._insert_event(
                connection,
                investigation_id=investigation_uuid,
                module_run_id=run_uuid,
                event_type="report.snapshot_created",
                actor=actor,
                payload={
                    "report_snapshot_id": str(row["id"]),
                    "contract_version": contract_version,
                    "source_state_version": source_state_version,
                    "source_review_revision": source_review_revision,
                    "report_sha256": report_sha256,
                    "hash_scope": (
                        "canonical_persisted_report_data_v1_excluding_snapshot_sha256"
                    ),
                },
            )
            return row

    def get_report_snapshot(
        self,
        investigation_id: uuid.UUID | str,
        report_snapshot_id: uuid.UUID | str | None = None,
    ) -> dict[str, Any] | None:
        parameters: dict[str, Any] = {
            "investigation_id": _uuid(investigation_id),
        }
        predicate = ""
        if report_snapshot_id is not None:
            predicate = "AND snapshot.id = :report_snapshot_id"
            parameters["report_snapshot_id"] = _uuid(report_snapshot_id)
        else:
            predicate = """
                AND snapshot.source_state_version = investigation.state_version
                AND snapshot.source_review_revision = investigation.review_revision
                AND investigation.review_recomputation_required = false
            """
        with self.engine.connect() as connection:
            return self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT snapshot.*
                        FROM {self.schema}.report_snapshots AS snapshot
                        JOIN {self.schema}.investigations AS investigation
                          ON investigation.id = snapshot.investigation_id
                        WHERE snapshot.investigation_id = :investigation_id
                          {predicate}
                        ORDER BY snapshot.created_at DESC, snapshot.id DESC
                        LIMIT 1
                        """
                    ),
                    parameters,
                )
            )

    def get_report_data(
        self, investigation_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        """Read one replayable report snapshot without crossing investigations."""

        investigation_uuid = _uuid(investigation_id)
        with self.transaction() as connection:
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.investigations
                        WHERE id = :investigation_id
                        """
                    ),
                    {"investigation_id": investigation_uuid},
                )
            )
            if investigation is None:
                return None

            def rows(statement: str) -> list[dict[str, Any]]:
                return [
                    dict(row)
                    for row in connection.execute(
                        text(statement),
                        {"investigation_id": investigation_uuid},
                    ).mappings()
                ]

            return {
                "investigation": investigation,
                "claim_investigations": rows(
                    f"""
                    SELECT * FROM {self.schema}.claim_investigations
                    WHERE investigation_id = :investigation_id
                    ORDER BY claim_id, claim_variant_id, created_at
                    """
                ),
                "claim_limitations": rows(
                    f"""
                    SELECT limitation.*
                    FROM {self.schema}.claim_limitations AS limitation
                    JOIN {self.schema}.claim_investigations AS claim
                      ON claim.id = limitation.claim_investigation_id
                    WHERE claim.investigation_id = :investigation_id
                    ORDER BY limitation.claim_investigation_id,
                             limitation.sequence_no
                    """
                ),
                "iterations": rows(
                    f"""
                    SELECT iteration.*
                    FROM {self.schema}.iterations AS iteration
                    JOIN {self.schema}.claim_investigations AS claim
                      ON claim.id = iteration.claim_investigation_id
                    WHERE claim.investigation_id = :investigation_id
                    ORDER BY iteration.claim_investigation_id,
                             iteration.iteration_no
                    """
                ),
                "module_runs": rows(
                    f"""
                    SELECT * FROM {self.schema}.module_runs
                    WHERE investigation_id = :investigation_id
                    ORDER BY created_at, id
                    """
                ),
                "jobs": rows(
                    f"""
                    SELECT job.* FROM {self.schema}.jobs AS job
                    JOIN {self.schema}.module_runs AS run
                      ON run.id = job.module_run_id
                    WHERE run.investigation_id = :investigation_id
                    ORDER BY job.created_at, job.id
                    """
                ),
                "events": rows(
                    f"""
                    SELECT * FROM {self.schema}.events
                    WHERE investigation_id = :investigation_id
                    ORDER BY id
                    """
                ),
                "queries": rows(
                    f"""
                    SELECT query.* FROM {self.schema}.queries AS query
                    JOIN {self.schema}.iterations AS iteration
                      ON iteration.id = query.iteration_id
                    JOIN {self.schema}.claim_investigations AS claim
                      ON claim.id = iteration.claim_investigation_id
                    WHERE claim.investigation_id = :investigation_id
                    ORDER BY query.created_at, query.id
                    """
                ),
                "documents": rows(
                    f"""
                    SELECT * FROM {self.schema}.documents
                    WHERE investigation_id = :investigation_id
                    ORDER BY created_at, id
                    """
                ),
                "document_versions": rows(
                    f"""
                    SELECT version.id, version.document_id, version.version_no,
                           version.content_sha256, version.mime_type,
                           version.byte_size, version.content_artifact_id,
                           version.acquisition_kind, version.created_by,
                           version.version_metadata -> 'readable_document_audit'
                             AS readable_document_audit,
                           version.created_at
                    FROM {self.schema}.document_versions AS version
                    JOIN {self.schema}.documents AS document
                      ON document.id = version.document_id
                    WHERE document.investigation_id = :investigation_id
                    ORDER BY version.document_id, version.version_no
                    """
                ),
                "document_sources": rows(
                    f"""
                    SELECT source.* FROM {self.schema}.document_sources AS source
                    JOIN {self.schema}.documents AS document
                      ON document.id = source.document_id
                    WHERE document.investigation_id = :investigation_id
                    ORDER BY source.created_at, source.id
                    """
                ),
                "document_qualifications": rows(
                    f"""
                    SELECT qualification.*
                    FROM {self.schema}.document_qualifications AS qualification
                    JOIN {self.schema}.documents AS document
                      ON document.id = qualification.document_id
                    WHERE document.investigation_id = :investigation_id
                    ORDER BY qualification.created_at, qualification.id
                    """
                ),
                "feature_disclosures": rows(
                    f"""
                    SELECT disclosure.*
                    FROM {self.schema}.feature_disclosures AS disclosure
                    JOIN {self.schema}.module_runs AS run
                      ON run.id = disclosure.module_run_id
                    WHERE run.investigation_id = :investigation_id
                    ORDER BY disclosure.created_at, disclosure.id
                    """
                ),
                "closest_prior_art_versions": rows(
                    f"""
                    SELECT selection.*
                    FROM {self.schema}.closest_prior_art_versions AS selection
                    JOIN {self.schema}.claim_investigations AS claim
                      ON claim.id = selection.claim_investigation_id
                    WHERE claim.investigation_id = :investigation_id
                    ORDER BY selection.claim_investigation_id,
                             selection.version_no
                    """
                ),
                "gap_items": rows(
                    f"""
                    SELECT gap.* FROM {self.schema}.gap_items AS gap
                    JOIN {self.schema}.claim_investigations AS claim
                      ON claim.id = gap.claim_investigation_id
                    WHERE claim.investigation_id = :investigation_id
                    ORDER BY gap.claim_investigation_id, gap.gap_key,
                             gap.version_no, gap.created_at, gap.id
                    """
                ),
                "combinations": rows(
                    f"""
                    SELECT combination.* FROM {self.schema}.combinations AS combination
                    JOIN {self.schema}.claim_investigations AS claim
                      ON claim.id = combination.claim_investigation_id
                    WHERE claim.investigation_id = :investigation_id
                    ORDER BY combination.created_at, combination.id
                    """
                ),
                "artifacts": rows(
                    f"""
                    SELECT * FROM {self.schema}.artifacts
                    WHERE investigation_id = :investigation_id
                    ORDER BY created_at, id
                    """
                ),
                "critical_date_confirmations": rows(
                    f"""
                    SELECT * FROM {self.schema}.critical_date_confirmations
                    WHERE investigation_id = :investigation_id
                    ORDER BY created_at, id
                    """
                ),
                "human_review_actions": rows(
                    f"""
                    SELECT id, investigation_id, action_type, action_seq,
                           target_snapshot, actor, reason,
                           base_investigation_state_version,
                           resulting_investigation_state_version,
                           base_claim_state_versions,
                           resulting_claim_state_versions,
                           base_review_revision, resulting_review_revision,
                           status, module_run_id, job_id,
                           invalidated_derivations,
                           pending_recomputation, result_summary, error_summary,
                           created_at, started_at, completed_at
                    FROM {self.schema}.human_review_actions
                    WHERE investigation_id = :investigation_id
                    ORDER BY action_seq, id
                    """
                ),
                "document_date_fact_revisions": rows(
                    f"""
                    SELECT id, investigation_id, document_id,
                           document_version_id, review_action_id, revision_no,
                           supersedes_id, decision, claim_investigation_ids,
                           public_availability_date, publication_date,
                           filing_date, priority_date, source_type,
                           publication_number, authority, cn_application_scope,
                           date_channel, actor, reason, created_at
                    FROM {self.schema}.document_date_fact_revisions
                    WHERE investigation_id = :investigation_id
                    ORDER BY document_id, revision_no, id
                    """
                ),
                "evidence_imports": rows(
                    f"""
                    SELECT id, investigation_id, review_action_id, document_id,
                           document_version_id, document_source_id,
                           content_artifact_id, claim_investigation_ids,
                           declared_date_facts, actor, reason, created_at
                    FROM {self.schema}.evidence_imports
                    WHERE investigation_id = :investigation_id
                    ORDER BY created_at, id
                    """
                ),
                "continuation_batches": rows(
                    f"""
                    SELECT * FROM {self.schema}.continuation_batches
                    WHERE investigation_id = :investigation_id
                    ORDER BY created_at, id
                    """
                ),
            }

    def get_review_context(
        self, investigation_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        """Return a server-computed current human-review projection.

        The response deliberately separates current rows from lineage/history so
        Portal never has to guess the active qualification, D1, gap or document
        version from an unordered relation dump.
        """

        investigation_uuid = _uuid(investigation_id)
        with self.transaction() as connection:
            connection.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            )
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT id, analysis_session_id, patent_record_id,
                               environment, jurisdiction, analysis_kind, status,
                               pipeline_version, source_snapshot_sha256,
                               state_version, review_revision,
                               last_applied_review_action_seq,
                               review_recomputation_required,
                               created_at, updated_at, completed_at
                        FROM {self.schema}.investigations
                        WHERE id = :id
                        """
                    ),
                    {"id": investigation_uuid},
                )
            )
            if investigation is None:
                return None

            def rows(sql: str, **parameters: Any) -> list[dict[str, Any]]:
                return [
                    dict(row)
                    for row in connection.execute(
                        text(sql),
                        {"investigation_id": investigation_uuid, **parameters},
                    ).mappings()
                ]

            claims = rows(
                f"""
                SELECT claim.id, claim.investigation_id, claim.claim_id,
                       claim.claim_variant_id, claim.source_claim_text,
                       claim.expanded_claim_text, claim.dependency_path,
                       claim.fingerprint, claim.status,
                       claim.current_iteration_no, claim.terminal_reason,
                       claim.critical_date, claim.critical_date_basis,
                       claim.target_publication_date, claim.state_version,
                       claim.review_revision, claim.created_at,
                       claim.updated_at, claim.completed_at,
                       latest_iteration.id AS latest_iteration_id,
                       latest_iteration.iteration_no AS latest_iteration_no,
                       latest_iteration.status AS latest_iteration_status,
                       latest_iteration.stop_reason AS latest_iteration_stop_reason
                FROM {self.schema}.claim_investigations AS claim
                LEFT JOIN LATERAL (
                    SELECT iteration.*
                    FROM {self.schema}.iterations AS iteration
                    WHERE iteration.claim_investigation_id = claim.id
                    ORDER BY iteration.iteration_no DESC,
                             iteration.created_at DESC, iteration.id DESC
                    LIMIT 1
                ) AS latest_iteration ON true
                WHERE claim.investigation_id = :investigation_id
                ORDER BY claim.claim_id, claim.claim_variant_id, claim.created_at
                """
            )
            documents = rows(
                f"""
                SELECT document.id, document.investigation_id,
                       document.canonical_key, document.document_type,
                       document.evidence_level, document.title,
                       document.language, document.identifiers, document.dates,
                       document.family_key, document.content_sha256,
                       document.current_document_version_id,
                       document.created_at, document.updated_at,
                       version.version_no AS current_version_no,
                       version.content_sha256 AS current_content_sha256,
                       version.mime_type AS current_mime_type,
                       version.byte_size AS current_byte_size
                FROM {self.schema}.documents AS document
                LEFT JOIN {self.schema}.document_versions AS version
                  ON version.id = document.current_document_version_id
                WHERE document.investigation_id = :investigation_id
                ORDER BY document.created_at, document.id
                """
            )
            document_versions = rows(
                f"""
                SELECT version.id, version.document_id, version.version_no,
                       version.content_sha256, version.mime_type,
                       version.byte_size, version.acquisition_kind,
                       version.version_metadata -> 'readable_document_audit'
                         AS readable_document_audit,
                       version.created_by, version.created_at
                FROM {self.schema}.document_versions AS version
                JOIN {self.schema}.documents AS document
                  ON document.id = version.document_id
                WHERE document.investigation_id = :investigation_id
                ORDER BY version.document_id, version.version_no
                """
            )
            qualification_history = rows(
                f"""
                SELECT qualification.id, qualification.document_id,
                       qualification.document_version_id,
                       qualification.claim_investigation_id,
                       qualification.limitation_id,
                       qualification.critical_date,
                       qualification.earliest_priority_date,
                       qualification.filing_date,
                       qualification.publication_date,
                       qualification.public_availability_date,
                       qualification.eligibility_type,
                       qualification.novelty_eligible,
                       qualification.inventive_step_eligible,
                       qualification.verification_status,
                       qualification.verification_reason,
                       qualification.date_rule_version,
                       qualification.human_confirmed,
                       qualification.reason_codes,
                       qualification.supersedes_id,
                       qualification.review_action_id,
                       qualification.assessment_version,
                       qualification.created_at, qualification.updated_at
                FROM {self.schema}.document_qualifications AS qualification
                JOIN {self.schema}.claim_investigations AS claim
                  ON claim.id = qualification.claim_investigation_id
                WHERE claim.investigation_id = :investigation_id
                ORDER BY qualification.document_version_id,
                         qualification.claim_investigation_id,
                         qualification.limitation_id NULLS FIRST,
                         qualification.assessment_version,
                         qualification.created_at, qualification.id
                """
            )
            latest_qualifications: dict[tuple[str, str, str], dict[str, Any]] = {}
            for qualification in qualification_history:
                key = (
                    str(qualification.get("document_version_id") or ""),
                    str(qualification["claim_investigation_id"]),
                    str(qualification.get("limitation_id") or ""),
                )
                latest_qualifications[key] = qualification
            d1_history = rows(
                f"""
                SELECT selection.*
                FROM {self.schema}.closest_prior_art_versions AS selection
                JOIN {self.schema}.claim_investigations AS claim
                  ON claim.id = selection.claim_investigation_id
                WHERE claim.investigation_id = :investigation_id
                ORDER BY selection.claim_investigation_id,
                         selection.version_no, selection.created_at
                """
            )
            current_d1 = [row for row in d1_history if bool(row.get("is_current"))]
            gap_history = rows(
                f"""
                SELECT gap.*
                FROM {self.schema}.gap_items AS gap
                JOIN {self.schema}.claim_investigations AS claim
                  ON claim.id = gap.claim_investigation_id
                WHERE claim.investigation_id = :investigation_id
                ORDER BY gap.claim_investigation_id, gap.gap_key,
                         gap.version_no, gap.created_at, gap.id
                """
            )
            latest_gaps: dict[tuple[str, str], dict[str, Any]] = {}
            for gap in gap_history:
                latest_gaps[(str(gap["claim_investigation_id"]), str(gap["gap_key"]))] = gap
            actions = rows(
                f"""
                SELECT id, investigation_id, action_type, action_seq,
                       target_snapshot, actor, reason,
                       base_investigation_state_version,
                       resulting_investigation_state_version,
                       base_claim_state_versions,
                       resulting_claim_state_versions,
                       base_review_revision, resulting_review_revision,
                       status, module_run_id, job_id,
                       invalidated_derivations, pending_recomputation,
                       result_summary, error_summary,
                       created_at, started_at, completed_at
                FROM {self.schema}.human_review_actions
                WHERE investigation_id = :investigation_id
                ORDER BY action_seq, created_at, id
                """
            )
            date_fact_revisions = rows(
                f"""
                SELECT id, investigation_id, document_id,
                       document_version_id, review_action_id, revision_no,
                       supersedes_id, decision, claim_investigation_ids,
                       expected_qualifications, public_availability_date,
                       publication_date, filing_date, priority_date,
                       source_type, publication_number, authority,
                       cn_application_scope, date_channel, actor, reason,
                       created_at
                FROM {self.schema}.document_date_fact_revisions
                WHERE investigation_id = :investigation_id
                ORDER BY document_id, revision_no, created_at, id
                """
            )
            active_rows = rows(
                f"""
                SELECT run.id, run.module_code, run.status AS module_run_status,
                       job.id AS job_id, job.status AS job_status
                FROM {self.schema}.module_runs AS run
                LEFT JOIN {self.schema}.jobs AS job ON job.module_run_id = run.id
                WHERE run.investigation_id = :investigation_id
                  AND run.module_code IN (
                      'I0_ORCHESTRATE', 'I0_INVALIDITY_WORKFLOW',
                      'I5_REPORT', 'HUMAN_REVIEW_APPLY'
                  )
                  AND (
                      run.status IN ('created', 'queued', 'running', 'cancellation_requested')
                      OR job.status IN ('queued', 'leased')
                  )
                ORDER BY run.created_at, run.id
                """
            )
            latest_iterations_terminal = all(
                not claim.get("latest_iteration_status")
                or str(claim["latest_iteration_status"])
                in {"succeeded", "partial", "failed", "cancelled"}
                for claim in claims
            )
            d1_candidates = rows(
                f"""
                WITH latest_qualification AS (
                    SELECT DISTINCT ON (
                        qualification.document_version_id,
                        qualification.claim_investigation_id
                    ) qualification.*
                    FROM {self.schema}.document_qualifications AS qualification
                    JOIN {self.schema}.claim_investigations AS claim
                      ON claim.id = qualification.claim_investigation_id
                    WHERE claim.investigation_id = :investigation_id
                      AND qualification.limitation_id IS NULL
                    ORDER BY qualification.document_version_id,
                             qualification.claim_investigation_id,
                             qualification.assessment_version DESC,
                             qualification.created_at DESC,
                             qualification.id DESC
                ), disclosure AS (
                    SELECT feature.document_version_id,
                           feature.claim_investigation_id,
                           count(DISTINCT feature.limitation_id) AS disclosure_count
                    FROM {self.schema}.feature_disclosures AS feature
                    JOIN {self.schema}.claim_investigations AS claim
                      ON claim.id = feature.claim_investigation_id
                    WHERE claim.investigation_id = :investigation_id
                    GROUP BY feature.document_version_id,
                             feature.claim_investigation_id
                ), limitation AS (
                    SELECT claim.id AS claim_investigation_id,
                           count(limit_row.id) AS limitation_count
                    FROM {self.schema}.claim_investigations AS claim
                    LEFT JOIN {self.schema}.claim_limitations AS limit_row
                      ON limit_row.claim_investigation_id = claim.id
                    WHERE claim.investigation_id = :investigation_id
                    GROUP BY claim.id
                )
                SELECT qualification.id, qualification.document_id,
                       qualification.document_version_id,
                       qualification.claim_investigation_id,
                       qualification.limitation_id,
                       qualification.critical_date,
                       qualification.public_availability_date,
                       qualification.publication_date,
                       qualification.eligibility_type,
                       qualification.novelty_eligible,
                       qualification.inventive_step_eligible,
                       qualification.verification_status,
                       qualification.verification_reason,
                       qualification.human_confirmed,
                       qualification.assessment_version,
                       qualification.review_action_id,
                       COALESCE(disclosure.disclosure_count, 0) AS disclosure_count,
                       COALESCE(limitation.limitation_count, 0) AS limitation_count,
                       (
                           qualification.inventive_step_eligible
                           AND qualification.verification_status = 'verified'
                           AND COALESCE(disclosure.disclosure_count, 0)
                               = COALESCE(limitation.limitation_count, 0)
                       ) AS selectable
                FROM latest_qualification AS qualification
                LEFT JOIN disclosure
                  ON disclosure.document_version_id = qualification.document_version_id
                 AND disclosure.claim_investigation_id = qualification.claim_investigation_id
                LEFT JOIN limitation
                  ON limitation.claim_investigation_id = qualification.claim_investigation_id
                ORDER BY qualification.claim_investigation_id,
                         qualification.document_version_id
                """
            )
            return {
                "contract_version": "v1",
                "investigation_id": str(investigation["id"]),
                "status": str(investigation["status"]),
                "investigation": investigation,
                "state_version": int(investigation["state_version"]),
                "review_revision": int(investigation.get("review_revision") or 0),
                "quiescent": not active_rows and latest_iterations_terminal,
                "quiescence_blockers": active_rows,
                "pending_recomputation": bool(
                    investigation.get("review_recomputation_required")
                ),
                "recomputation_required": bool(
                    investigation.get("review_recomputation_required")
                ),
                "claims": claims,
                "documents": documents,
                "document_versions": document_versions,
                "latest_qualifications": list(latest_qualifications.values()),
                "qualification_history": qualification_history,
                "d1_candidates": d1_candidates,
                "current_d1": current_d1,
                "d1_history": d1_history,
                "current_gaps": list(latest_gaps.values()),
                "gap_history": gap_history,
                "date_fact_revisions": date_fact_revisions,
                "pending_actions": [
                    row
                    for row in actions
                    if str(row.get("status")) in {"queued", "running"}
                ],
                "action_history": actions,
            }

    def get_human_review_apply_context(
        self, review_action_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        action_uuid = _uuid(review_action_id)
        with self.transaction() as connection:
            action = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.human_review_actions
                        WHERE id = :id FOR UPDATE
                        """
                    ),
                    {"id": action_uuid},
                )
            )
            if action is None:
                return None
            investigation = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.investigations
                        WHERE id = :id FOR UPDATE
                        """
                    ),
                    {"id": action["investigation_id"]},
                )
            )
            assert investigation is not None
            if str(action["status"]) == "queued":
                action = self._row(
                    connection.execute(
                        text(
                            f"""
                            UPDATE {self.schema}.human_review_actions
                            SET status = 'running', started_at = COALESCE(started_at, now())
                            WHERE id = :id AND status = 'queued'
                            RETURNING *
                            """
                        ),
                        {"id": action_uuid},
                    )
                )
                assert action is not None
            target = action.get("target_snapshot") or {}
            claim_ids = [
                _uuid(value)
                for value in (
                    target.get("claim_investigation_ids")
                    if isinstance(target, Mapping)
                    else []
                )
            ]
            claims = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.claim_investigations
                        WHERE investigation_id = :investigation_id
                          AND id = ANY(CAST(:claim_ids AS UUID[]))
                        ORDER BY id FOR UPDATE
                        """
                    ),
                    {
                        "investigation_id": investigation["id"],
                        "claim_ids": [str(item) for item in claim_ids],
                    },
                ).mappings()
            ]
            document_id = _uuid(target.get("document_id"))
            document_version_id = _uuid(target.get("document_version_id"))
            document = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.documents
                        WHERE id = :document_id
                          AND investigation_id = :investigation_id
                        """
                    ),
                    {
                        "document_id": document_id,
                        "investigation_id": investigation["id"],
                    },
                )
            )
            version = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.document_versions
                        WHERE id = :version_id AND document_id = :document_id
                        """
                    ),
                    {"version_id": document_version_id, "document_id": document_id},
                )
            )
            if document is None or version is None:
                raise RepositoryConflictError(
                    "human review action 的 document version 已丢失"
                )
            source = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.document_sources
                        WHERE document_id = :document_id
                          AND document_version_id = :document_version_id
                        ORDER BY retrieved_at DESC, created_at DESC, id DESC
                        LIMIT 1
                        """
                    ),
                    {
                        "document_id": document_id,
                        "document_version_id": document_version_id,
                    },
                )
            )
            limitations = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT limitation.*
                        FROM {self.schema}.claim_limitations AS limitation
                        WHERE limitation.claim_investigation_id
                              = ANY(CAST(:claim_ids AS UUID[]))
                        ORDER BY limitation.claim_investigation_id,
                                 limitation.sequence_no, limitation.id
                        """
                    ),
                    {"claim_ids": [str(item) for item in claim_ids]},
                ).mappings()
            ]
            evidence_import = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.evidence_imports
                        WHERE review_action_id = :action_id
                        """
                    ),
                    {"action_id": action_uuid},
                )
            )
            date_revision = self._row(
                connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.document_date_fact_revisions
                        WHERE review_action_id = :action_id
                        """
                    ),
                    {"action_id": action_uuid},
                )
            )
            qualifications = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.document_qualifications
                        WHERE document_id = :document_id
                          AND document_version_id = :document_version_id
                          AND claim_investigation_id = ANY(CAST(:claim_ids AS UUID[]))
                        ORDER BY claim_investigation_id,
                                 assessment_version, created_at, id
                        """
                    ),
                    {
                        "document_id": document_id,
                        "document_version_id": document_version_id,
                        "claim_ids": [str(item) for item in claim_ids],
                    },
                ).mappings()
            ]
            disclosures = [
                dict(row)
                for row in connection.execute(
                    text(
                        f"""
                        SELECT * FROM {self.schema}.feature_disclosures
                        WHERE document_id = :document_id
                          AND document_version_id = :document_version_id
                          AND claim_investigation_id = ANY(CAST(:claim_ids AS UUID[]))
                        ORDER BY claim_investigation_id, limitation_id,
                                 created_at DESC, id DESC
                        """
                    ),
                    {
                        "document_id": document_id,
                        "document_version_id": document_version_id,
                        "claim_ids": [str(item) for item in claim_ids],
                    },
                ).mappings()
            ]
            return {
                "action": action,
                "investigation": investigation,
                "claims": claims,
                "limitations": limitations,
                "document": document,
                "document_version": version,
                "document_source": source,
                "evidence_import": evidence_import,
                "date_fact_revision": date_revision,
                "qualifications": qualifications,
                "disclosures": disclosures,
            }

    def list_events(
        self, investigation_id: uuid.UUID | str
    ) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT * FROM {self.schema}.events
                    WHERE investigation_id = :investigation_id
                    ORDER BY id ASC
                    """
                ),
                {"investigation_id": _uuid(investigation_id)},
            ).mappings()
            return [dict(row) for row in rows]


__all__ = [
    "LeaseLostError",
    "Repository",
    "RepositoryConfigurationError",
    "RepositoryConflictError",
    "SCHEMA_BY_ENVIRONMENT",
    "TABLE_NAMES",
    "build_claim_job_sql",
    "build_ddl_statements",
    "build_recover_expired_jobs_sql",
    "plan_gap_frontier",
]
