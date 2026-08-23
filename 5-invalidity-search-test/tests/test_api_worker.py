from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

import invalidity.main as main_module
from invalidity.config import ConfigurationError, Settings
from invalidity.main import ORCHESTRATOR_MODULE_CODE, create_app
from invalidity.report import build_report_data
from invalidity.worker import (
    DurableWorker,
    JobContext,
    PermanentJobError,
    RetryableJobError,
    WorkerOutcome,
)


API_TOKEN = "test-api-token-with-enough-entropy"
AUTH_HEADERS = {"Authorization": f"Bearer {API_TOKEN}"}
PARSER_TOKEN = "test-parser-token-with-enough-entropy"


def settings(tmp_path: Path, *, environment: str = "test") -> Settings:
    return Settings(
        environment=environment,
        service_port=5209 if environment == "test" else 5109,
        api_base_url=(
            "http://127.0.0.1:5209"
            if environment == "test"
            else "http://127.0.0.1:5109"
        ),
        api_token=(
            API_TOKEN
            if environment == "test"
            else "prod-api-token-with-enough-entropy"
        ),
        database_url="postgresql://user:database-secret@localhost/invalidity",
        database_schema=(
            "invalidity_test" if environment == "test" else "invalidity_prod"
        ),
        artifact_root=tmp_path / "invalidity" / environment,
        allowed_source_roots=(
            tmp_path.resolve(),
            (Path(__file__).resolve().parent / "fixtures").resolve(),
        ),
        module1_api_url=(
            "http://127.0.0.1:5201/run"
            if environment == "test"
            else "http://127.0.0.1:5101/run"
        ),
        patent_provider="fixture_patent",
        npl_provider="fixture_npl",
        llm_base_url="https://example.invalid/v4?token=secret-query",
        llm_api_key="glm-secret-value",
        llm_model="glm-4.6v",
        module1_api_token=PARSER_TOKEN if environment == "test" else None,
        module1_auth_mode=(
            "bearer" if environment == "test" else "legacy_unauthenticated"
        ),
        parser_api_token=PARSER_TOKEN if environment == "test" else None,
        worker_poll_seconds=1,
        job_lease_seconds=1,
        parser_port=5201 if environment == "test" else None,
    )


class MemoryRepository:
    def __init__(self, *, environment: str = "test") -> None:
        self.environment = environment
        self.schema = "invalidity_test" if environment == "test" else "invalidity_prod"
        self.initialized = 0
        self.closed = 0
        self.investigations: dict[uuid.UUID, dict[str, Any]] = {}
        self.claim_investigations: dict[uuid.UUID, dict[str, Any]] = {}
        self.iterations: dict[uuid.UUID, dict[str, Any]] = {}
        self.module_runs: dict[uuid.UUID, dict[str, Any]] = {}
        self.jobs: dict[uuid.UUID, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.critical_date_confirmations: dict[uuid.UUID, dict[str, Any]] = {}
        self.continuation_batches: dict[uuid.UUID, dict[str, Any]] = {}
        self.report_snapshots: dict[uuid.UUID, dict[str, Any]] = {}
        self.gap_items: list[dict[str, Any]] = []

    def init_schema(self) -> None:
        self.initialized += 1

    def close(self) -> None:
        self.closed += 1

    def create_investigation(
        self,
        *,
        analysis_session_id: str,
        source_snapshot: Mapping[str, Any],
        pipeline_version: str,
        investigation_id: Any = None,
        patent_record_id: Any = None,
        settings: Mapping[str, Any] | None = None,
        budgets: Mapping[str, Any] | None = None,
        workflow_state: Mapping[str, Any] | None = None,
        status: str = "created",
        **_: Any,
    ) -> dict[str, Any]:
        if self.get_investigation_by_session(analysis_session_id):
            raise RuntimeError("duplicate analysis_session_id")
        identifier = (
            uuid.UUID(str(investigation_id))
            if investigation_id is not None
            else uuid.uuid4()
        )
        serialized = json.dumps(source_snapshot, sort_keys=True, default=str)
        row = {
            "id": identifier,
            "analysis_session_id": analysis_session_id,
            "patent_record_id": str(patent_record_id or f"source:{analysis_session_id}"),
            "environment": "test",
            "status": status,
            "pipeline_version": pipeline_version,
            "source_snapshot": dict(source_snapshot),
            "source_snapshot_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            "settings": dict(settings or {}),
            "budgets": dict(budgets or {}),
            "workflow_state": dict(workflow_state or {}),
            "state_version": 0,
            "review_revision": 0,
            "review_recomputation_required": False,
            "created_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
            "updated_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
        }
        self.investigations[identifier] = row
        return row

    def get_investigation_by_session(self, session_id: str) -> dict[str, Any] | None:
        return next(
            (
                row
                for row in self.investigations.values()
                if row["analysis_session_id"] == session_id
            ),
            None,
        )

    def get_investigation(self, investigation_id: Any) -> dict[str, Any] | None:
        return self.investigations.get(uuid.UUID(str(investigation_id)))

    def update_investigation(
        self,
        investigation_id: Any,
        *,
        expected_state_version: int | None = None,
        status: str | None = None,
        source_snapshot: Mapping[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        row = self.investigations[uuid.UUID(str(investigation_id))]
        if expected_state_version is not None and row["state_version"] != expected_state_version:
            raise RuntimeError("state conflict")
        if status is not None:
            row["status"] = status
        if source_snapshot is not None:
            serialized = json.dumps(source_snapshot, sort_keys=True, default=str)
            row["source_snapshot"] = dict(source_snapshot)
            row["source_snapshot_sha256"] = hashlib.sha256(
                serialized.encode()
            ).hexdigest()
        row["state_version"] += 1
        return row

    def list_module_runs(
        self,
        *,
        investigation_id: Any = None,
        module_code: str | None = None,
        limit: int = 100,
        **_: Any,
    ) -> list[dict[str, Any]]:
        rows = list(self.module_runs.values())
        if investigation_id is not None:
            target = uuid.UUID(str(investigation_id))
            rows = [row for row in rows if row["investigation_id"] == target]
        if module_code is not None:
            rows = [row for row in rows if row["module_code"] == module_code]
        return rows[:limit]

    def create_module_run(
        self,
        *,
        investigation_id: Any,
        module_code: str,
        contract_version: str,
        input_snapshot: Mapping[str, Any],
        idempotency_key: str,
        status: str,
        claim_investigation_id: Any = None,
        iteration_id: Any = None,
        requested_mode: str | None = None,
        effective_mode: str | None = None,
        actual_provider: str | None = None,
        retry_of_module_run_id: Any = None,
        retry_reason: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        identifier = uuid.uuid4()
        row = {
            "id": identifier,
            "investigation_id": uuid.UUID(str(investigation_id)),
            "claim_investigation_id": (
                uuid.UUID(str(claim_investigation_id))
                if claim_investigation_id is not None
                else None
            ),
            "iteration_id": (
                uuid.UUID(str(iteration_id)) if iteration_id is not None else None
            ),
            "module_code": module_code,
            "contract_version": contract_version,
            "input_snapshot": dict(input_snapshot),
            "idempotency_key": idempotency_key,
            "status": status,
            "requested_mode": requested_mode,
            "effective_mode": effective_mode,
            "actual_provider": actual_provider,
            "retry_of_module_run_id": (
                uuid.UUID(str(retry_of_module_run_id))
                if retry_of_module_run_id is not None
                else None
            ),
            "retry_reason": retry_reason,
            "cancel_requested_at": None,
            "cancelled_at": None,
            "created_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
        }
        self.module_runs[identifier] = row
        return row

    def get_module_lab_run(self, module_run_id: Any) -> dict[str, Any] | None:
        identifier = uuid.UUID(str(module_run_id))
        run = self.module_runs.get(identifier)
        if run is None:
            return None
        job = next(
            (item for item in self.jobs.values() if item["module_run_id"] == identifier),
            None,
        )
        return {
            "module_run": run,
            "job": job,
            "events": [
                item for item in self.events if item.get("module_run_id") == identifier
            ],
        }

    def request_module_run_cancellation(
        self,
        module_run_id: Any,
        *,
        reason: str,
        **_: Any,
    ) -> dict[str, Any]:
        identifier = uuid.UUID(str(module_run_id))
        run = self.module_runs[identifier]
        job = next(
            (item for item in self.jobs.values() if item["module_run_id"] == identifier),
            None,
        )
        if run["status"] == "cancelled":
            return {
                "module_run": run,
                "job": job,
                "cancelled": True,
                "idempotent_replay": True,
            }
        if run["status"] in {"succeeded", "partial", "failed"}:
            raise main_module.RepositoryConflictError("terminal run")
        now = datetime(2026, 7, 19, 1, tzinfo=timezone.utc)
        run["cancel_requested_at"] = now
        if job is None or job["status"] == "queued":
            run["status"] = "cancelled"
            run["cancelled_at"] = now
            if job is not None:
                job["status"] = "cancelled"
                job["cancel_requested_at"] = now
                job["cancel_reason"] = reason
                job["cancelled_at"] = now
            event_type = "job.cancelled" if job is not None else "module_run.cancelled"
            cancelled = True
        else:
            run["status"] = "cancellation_requested"
            job["cancel_requested_at"] = now
            job["cancel_reason"] = reason
            event_type = "module_run.cancel_requested"
            cancelled = False
        self.events.append(
            {
                "id": len(self.events) + 1,
                "investigation_id": run["investigation_id"],
                "module_run_id": identifier,
                "event_type": event_type,
                "actor": "api",
                "payload": {"reason": reason},
                "occurred_at": now,
            }
        )
        return {
            "module_run": run,
            "job": job,
            "cancelled": cancelled,
            "idempotent_replay": False,
        }

    def retry_module_run(
        self,
        module_run_id: Any,
        *,
        idempotency_key: str,
        reason: str,
        **_: Any,
    ) -> dict[str, Any]:
        original_id = uuid.UUID(str(module_run_id))
        original = self.module_runs[original_id]
        original_job = next(
            (item for item in self.jobs.values() if item["module_run_id"] == original_id),
            None,
        )
        existing = next(
            (
                item for item in self.module_runs.values()
                if item["investigation_id"] == original["investigation_id"]
                and item["module_code"] == original["module_code"]
                and item["idempotency_key"] == idempotency_key
            ),
            None,
        )
        if existing is not None:
            existing_job = next(
                item for item in self.jobs.values()
                if item["module_run_id"] == existing["id"]
            )
            return {
                "original_module_run": original,
                "module_run": existing,
                "job": existing_job,
                "idempotent_replay": True,
            }
        if original["status"] != "failed" or not original_job or original_job["status"] != "failed":
            raise main_module.RepositoryConflictError("not failed")
        retried = self.create_module_run(
            investigation_id=original["investigation_id"],
            module_code=original["module_code"],
            contract_version=original["contract_version"],
            input_snapshot=original["input_snapshot"],
            idempotency_key=idempotency_key,
            status="queued",
            claim_investigation_id=original["claim_investigation_id"],
            iteration_id=original["iteration_id"],
            requested_mode=original.get("requested_mode"),
            retry_of_module_run_id=original_id,
            retry_reason=reason,
        )
        retried_job = {
            "id": uuid.uuid4(),
            "module_run_id": retried["id"],
            "status": "queued",
            "payload": dict(original_job["payload"]),
            "attempt_count": 0,
            "max_attempts": original_job["max_attempts"],
            "created_at": datetime(2026, 7, 19, 2, tzinfo=timezone.utc),
        }
        self.jobs[retried_job["id"]] = retried_job
        self.events.extend(
            [
                {
                    "id": len(self.events) + 1,
                    "investigation_id": original["investigation_id"],
                    "module_run_id": original_id,
                    "event_type": "module_run.retry_requested",
                    "actor": "api",
                    "payload": {"retry_module_run_id": str(retried["id"])},
                    "occurred_at": datetime(2026, 7, 19, 2, tzinfo=timezone.utc),
                },
                {
                    "id": len(self.events) + 2,
                    "investigation_id": original["investigation_id"],
                    "module_run_id": retried["id"],
                    "event_type": "module_run.retry_queued",
                    "actor": "api",
                    "payload": {"retry_of_module_run_id": str(original_id)},
                    "occurred_at": datetime(2026, 7, 19, 2, tzinfo=timezone.utc),
                },
            ]
        )
        return {
            "original_module_run": original,
            "module_run": retried,
            "job": retried_job,
            "idempotent_replay": False,
        }

    def record_event_and_enqueue_job(
        self,
        *,
        investigation_id: Any,
        module_run_id: Any,
        event_type: str,
        actor: str,
        event_payload: Mapping[str, Any],
        job_payload: Mapping[str, Any],
        max_attempts: int,
        **_: Any,
    ) -> dict[str, Any]:
        run_id = uuid.UUID(str(module_run_id))
        if any(job["module_run_id"] == run_id for job in self.jobs.values()):
            raise RuntimeError("duplicate job")
        identifier = uuid.uuid4()
        job = {
            "id": identifier,
            "module_run_id": run_id,
            "status": "queued",
            "payload": dict(job_payload),
            "attempt_count": 0,
            "max_attempts": max_attempts,
            "cancel_requested_at": None,
            "cancel_reason": None,
            "cancelled_at": None,
            "created_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
        }
        self.jobs[identifier] = job
        self.module_runs[run_id]["status"] = "queued"
        self.events.append(
            {
                "id": len(self.events) + 1,
                "investigation_id": uuid.UUID(str(investigation_id)),
                "module_run_id": run_id,
                "event_type": event_type,
                "actor": actor,
                "payload": dict(event_payload),
                "occurred_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
            }
        )
        return job

    def seed_claim(
        self,
        investigation_id: Any,
        *,
        status: str = "initial_search",
        critical_date: date | None = date(2020, 1, 2),
        target_publication_date: date | None = date(2021, 1, 2),
        state_version: int = 0,
    ) -> dict[str, Any]:
        identifier = uuid.uuid4()
        row = {
            "id": identifier,
            "investigation_id": uuid.UUID(str(investigation_id)),
            "claim_id": "1",
            "critical_date": critical_date,
            "critical_date_basis": None,
            "target_publication_date": target_publication_date,
            "status": status,
            "state_version": state_version,
            "terminal_reason": "关键日待人工确认" if status == "needs_human_review" else None,
            "completed_at": (
                datetime(2026, 7, 19, tzinfo=timezone.utc)
                if status == "needs_human_review"
                else None
            ),
            "result_summary": {},
        }
        self.claim_investigations[identifier] = row
        return row

    def get_claim_investigation(self, claim_investigation_id: Any) -> dict[str, Any] | None:
        return self.claim_investigations.get(uuid.UUID(str(claim_investigation_id)))

    def list_claim_investigations(self, investigation_id: Any) -> list[dict[str, Any]]:
        target = uuid.UUID(str(investigation_id))
        rows = [
            row for row in self.claim_investigations.values()
            if row["investigation_id"] == target
        ]
        if not rows:
            rows = [self.seed_claim(target)]
        return rows

    def seed_iteration(
        self, claim_investigation_id: Any, *, iteration_no: int = 1
    ) -> dict[str, Any]:
        identifier = uuid.uuid4()
        row = {
            "id": identifier,
            "claim_investigation_id": uuid.UUID(str(claim_investigation_id)),
            "iteration_no": iteration_no,
            "status": "succeeded",
        }
        self.iterations[identifier] = row
        return row

    def get_claim_iteration(
        self, claim_investigation_id: Any, iteration_no: int
    ) -> dict[str, Any] | None:
        claim_id = uuid.UUID(str(claim_investigation_id))
        return next(
            (
                row for row in self.iterations.values()
                if row["claim_investigation_id"] == claim_id
                and row["iteration_no"] == iteration_no
            ),
            None,
        )

    def confirm_critical_date(self, **kwargs: Any) -> dict[str, Any]:
        claim = self.get_claim_investigation(kwargs["claim_investigation_id"])
        investigation = self.get_investigation(kwargs["investigation_id"])
        if claim is None or investigation is None or claim["investigation_id"] != investigation["id"]:
            raise main_module.RepositoryConflictError("claim ownership mismatch")
        existing = next(
            (
                item for item in self.critical_date_confirmations.values()
                if item["claim_investigation_id"] == claim["id"]
                and item["idempotency_key_sha256"] == kwargs["idempotency_key_sha256"]
            ),
            None,
        )
        if existing is not None:
            if existing["request_sha256"] != kwargs["request_sha256"]:
                raise main_module.RepositoryConflictError("idempotency mismatch")
            return {
                "confirmation": existing,
                "claim": claim,
                "investigation": investigation,
                "idempotent_replay": True,
            }
        if claim["state_version"] != kwargs["expected_state_version"]:
            raise main_module.RepositoryConflictError("state conflict")
        identifier = uuid.uuid4()
        claim["critical_date"] = kwargs["confirmed_date"]
        claim["target_publication_date"] = kwargs.get("target_publication_date")
        claim["critical_date_basis"] = kwargs["critical_date_basis"]
        claim["state_version"] += 1
        investigation["state_version"] += 1
        confirmation = {
            "id": identifier,
            "investigation_id": investigation["id"],
            "claim_investigation_id": claim["id"],
            "decision": kwargs["decision"],
            "confirmed_date": kwargs["confirmed_date"],
            "target_publication_date": kwargs.get("target_publication_date"),
            "critical_date_basis": kwargs["critical_date_basis"],
            "reason": kwargs["reason"],
            "idempotency_key_sha256": kwargs["idempotency_key_sha256"],
            "request_sha256": kwargs["request_sha256"],
            "resulting_claim_state_version": claim["state_version"],
            "created_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
        }
        self.critical_date_confirmations[identifier] = confirmation
        return {
            "confirmation": confirmation,
            "claim": claim,
            "investigation": investigation,
            "idempotent_replay": False,
        }

    def get_latest_critical_date_confirmation(
        self, claim_investigation_id: Any
    ) -> dict[str, Any] | None:
        claim_id = uuid.UUID(str(claim_investigation_id))
        rows = [
            item for item in self.critical_date_confirmations.values()
            if item["claim_investigation_id"] == claim_id
        ]
        return rows[-1] if rows else None

    def create_continuation_batch_and_enqueue(self, **kwargs: Any) -> dict[str, Any]:
        investigation = self.get_investigation(kwargs["investigation_id"])
        if investigation is None:
            raise main_module.RepositoryConflictError("missing investigation")
        existing = next(
            (
                item for item in self.continuation_batches.values()
                if item["investigation_id"] == investigation["id"]
                and item["idempotency_key_sha256"] == kwargs["idempotency_key_sha256"]
            ),
            None,
        )
        if existing is not None:
            if existing["request_sha256"] != kwargs["request_sha256"]:
                raise main_module.RepositoryConflictError("idempotency mismatch")
            run = self.module_runs[existing["module_run_id"]]
            job = next(item for item in self.jobs.values() if item["module_run_id"] == run["id"])
            return {
                "continuation_batch": existing,
                "investigation": investigation,
                "module_run": run,
                "job": job,
                "idempotent_replay": True,
            }
        if investigation["state_version"] != kwargs["expected_state_version"]:
            raise main_module.RepositoryConflictError("state conflict")
        selected = [
            uuid.UUID(str(item))
            for item in (kwargs.get("claim_investigation_ids") or [])
        ]
        claims = self.list_claim_investigations(investigation["id"])
        latest_gap_status: dict[tuple[uuid.UUID, str], tuple[int, str]] = {}
        for gap in self.gap_items:
            lineage = (
                uuid.UUID(str(gap["claim_investigation_id"])),
                str(gap["gap_key"]),
            )
            versioned_status = (
                int(gap.get("version_no") or 0),
                str(gap.get("status") or ""),
            )
            if versioned_status[0] > latest_gap_status.get(lineage, (-1, ""))[0]:
                latest_gap_status[lineage] = versioned_status
        claims_with_open_gaps = {
            claim_id
            for (claim_id, _gap_key), (_version, status) in latest_gap_status.items()
            if status == "open"
        }
        if selected:
            claims = [item for item in claims if item["id"] in selected]
        else:
            claims = [
                item
                for item in claims
                if item["status"]
                in {
                    "needs_human_review",
                    "search_budget_exhausted",
                    "exhausted",
                    "partial",
                    "failed",
                }
                and item["id"] in claims_with_open_gaps
            ]
        if not claims or any(item["investigation_id"] != investigation["id"] for item in claims):
            raise main_module.RepositoryConflictError("claim ownership mismatch")
        if any(
            item["status"]
            in {"novelty_evidence_complete", "inventive_step_evidence_complete"}
            for item in claims
        ):
            raise main_module.RepositoryConflictError(
                "successful claim requires supplemental investigation"
            )
        if any(
            item["status"]
            not in {
                "needs_human_review",
                "search_budget_exhausted",
                "exhausted",
                "partial",
                "failed",
            }
            for item in claims
        ):
            raise main_module.RepositoryConflictError("claim status is not continuable")
        if any(item["id"] not in claims_with_open_gaps for item in claims):
            raise main_module.RepositoryConflictError("claim has no unresolved open gap")
        batch_id = uuid.uuid4()
        run = self.create_module_run(
            investigation_id=investigation["id"],
            module_code=kwargs["module_code"],
            contract_version=kwargs["contract_version"],
            input_snapshot={"continuation_batch_id": str(batch_id)},
            idempotency_key=f"continuation:{kwargs['idempotency_key_sha256']}",
            status="created",
        )
        batch = {
            "id": batch_id,
            "investigation_id": investigation["id"],
            "claim_investigation_ids": [item["id"] for item in claims],
            "max_additional_rounds": kwargs["max_additional_rounds"],
            "reason": kwargs["reason"],
            "status": "queued",
            "module_run_id": run["id"],
            "idempotency_key_sha256": kwargs["idempotency_key_sha256"],
            "request_sha256": kwargs["request_sha256"],
            "created_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
        }
        self.continuation_batches[batch_id] = batch
        job = self.record_event_and_enqueue_job(
            investigation_id=investigation["id"],
            module_run_id=run["id"],
            event_type="investigation.continuation_queued",
            actor="api",
            event_payload={"continuation_batch_id": str(batch_id)},
            job_payload={"continuation_batch_id": str(batch_id)},
            max_attempts=3,
        )
        investigation["status"] = "queued"
        investigation["state_version"] += 1
        return {
            "continuation_batch": batch,
            "investigation": investigation,
            "module_run": run,
            "job": job,
            "idempotent_replay": False,
        }

    def get_continuation_batch(self, continuation_batch_id: Any) -> dict[str, Any] | None:
        return self.continuation_batches.get(uuid.UUID(str(continuation_batch_id)))

    def update_continuation_batch(self, continuation_batch_id: Any, **kwargs: Any) -> dict[str, Any]:
        row = self.continuation_batches[uuid.UUID(str(continuation_batch_id))]
        row.update(kwargs)
        return row

    def create_report_snapshot(self, **kwargs: Any) -> dict[str, Any]:
        existing = next(
            (
                item for item in self.report_snapshots.values()
                if item["investigation_id"] == uuid.UUID(str(kwargs["investigation_id"]))
                and item["contract_version"] == kwargs["contract_version"]
                and item["source_state_version"] == kwargs["source_state_version"]
                and item.get("source_review_revision", 0)
                == kwargs.get("source_review_revision", 0)
            ),
            None,
        )
        if existing is not None:
            return existing
        identifier = uuid.UUID(str(kwargs.get("report_snapshot_id") or uuid.uuid4()))
        payload = dict(kwargs["report_snapshot"])
        payload["snapshot_sha256"] = None
        canonical = dict(payload)
        canonical.pop("snapshot_sha256", None)
        serialized = json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":"))
        row = {
            "id": identifier,
            "investigation_id": uuid.UUID(str(kwargs["investigation_id"])),
            "contract_version": kwargs["contract_version"],
            "source_state_version": kwargs["source_state_version"],
            "source_review_revision": kwargs.get(
                "source_review_revision", 0
            ),
            "report_snapshot": payload,
            "report_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            "created_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
        }
        self.report_snapshots[identifier] = row
        return row

    def get_report_snapshot(
        self, investigation_id: Any, report_snapshot_id: Any = None
    ) -> dict[str, Any] | None:
        target = uuid.UUID(str(investigation_id))
        if report_snapshot_id is not None:
            row = self.report_snapshots.get(uuid.UUID(str(report_snapshot_id)))
            return row if row and row["investigation_id"] == target else None
        investigation = self.investigations[target]
        rows = [
            item
            for item in self.report_snapshots.values()
            if item["investigation_id"] == target
            and item["source_state_version"] == investigation["state_version"]
            and item.get("source_review_revision", 0)
            == investigation.get("review_revision", 0)
            and not investigation.get("review_recomputation_required", False)
        ]
        return rows[-1] if rows else None

    def list_events(self, investigation_id: Any) -> list[dict[str, Any]]:
        target = uuid.UUID(str(investigation_id))
        return [row for row in self.events if row["investigation_id"] == target]

    def get_report_data(self, investigation_id: Any) -> dict[str, Any] | None:
        investigation = self.get_investigation(investigation_id)
        if investigation is None:
            return None
        return {
            "investigation": investigation,
            "claim_investigations": self.list_claim_investigations(investigation_id),
            "module_runs": list(self.module_runs.values()),
            "jobs": list(self.jobs.values()),
            "events": self.list_events(investigation_id),
            "gap_items": list(self.gap_items),
            "critical_date_confirmations": list(self.critical_date_confirmations.values()),
            "continuation_batches": list(self.continuation_batches.values()),
            "provider_debug": {
                "appSecret": "must-not-leak",
                "source_url": "https://example.invalid/doc?token=must-not-leak&x=1",
            },
        }


def create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "analysis_session_id": "invalidity_api_test_session",
        "source_path": str(
            Path(__file__).resolve().parent / "fixtures" / "target-source.txt"
        ),
        "max_rounds": 3,
        "provider_mode": "live",
        "idempotency_key": "create-key-0001",
    }
    body.update(overrides)
    return body


def test_api_is_fail_closed_to_test_environment(tmp_path: Path) -> None:
    repository = MemoryRepository(environment="prod")
    application = create_app(
        settings=settings(tmp_path, environment="prod"),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["environment"] == "prod"
    assert repository.initialized == 1


def test_default_bootstrap_never_loads_shared_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = MemoryRepository()
    called: list[bool] = []

    def fake_from_environment(
        cls: type[Settings],
        source: Mapping[str, str] | None = None,
        *,
        load_dotenv_files: bool = True,
    ) -> Settings:
        assert source is None
        called.append(load_dotenv_files)
        return settings(tmp_path)

    monkeypatch.setattr(
        main_module.Settings,
        "from_environment",
        classmethod(fake_from_environment),
    )
    application = create_app(
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        assert client.get("/health").status_code == 200
    assert called == [False]


def test_service_starts_configured_number_of_independent_workers(
    tmp_path: Path,
) -> None:
    class IdleRepository(MemoryRepository):
        def claim_job(
            self,
            *,
            worker_id: str,
            lease_seconds: int,
        ) -> Mapping[str, Any] | None:
            assert worker_id
            assert lease_seconds > 0
            return None

    repository = IdleRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=True,
    )
    threads: tuple[threading.Thread, ...] = ()
    with TestClient(application) as client:
        runtime = application.state.invalidity_runtime
        threads = runtime.worker_threads
        assert len(runtime.workers) == 2
        assert len(threads) == 2
        assert len({worker.worker_id for worker in runtime.workers}) == 2
        assert all(thread.is_alive() for thread in threads)
        assert client.get("/health").json()["worker_concurrency"] == 2
    assert threads and not any(thread.is_alive() for thread in threads)


def test_create_start_read_and_report_are_idempotent_and_json_safe(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        health = client.get("/health")
        assert health.status_code == 200
        health_text = health.text
        assert "database-secret" not in health_text
        assert "glm-secret-value" not in health_text
        assert health.json()["environment"] == "test"
        assert "isolation" not in health.json()
        assert health.json()["i2_prompt_version"] == main_module.I2_PROMPT_VERSION
        assert health.json()["i2_rule_version"] == main_module.I2_RULE_VERSION
        assert health.json()["i4s_prompt_version"] == main_module.I4S_PROMPT_VERSION
        assert health.json()["i4s_rule_version"] == main_module.I4S_RULE_VERSION

        created = client.post("/v1/investigations", json=create_body())
        assert created.status_code == 201
        assert created.json()["status"] == "created"
        investigation_id = created.json()["investigation_id"]

        replay = client.post("/v1/investigations", json=create_body())
        assert replay.status_code == 200
        assert replay.json()["idempotent_replay"] is True
        conflict = client.post(
            "/v1/investigations",
            json=create_body(idempotency_key="different-create-key"),
        )
        assert conflict.status_code == 409

        started = client.post(f"/v1/investigations/{investigation_id}/start")
        assert started.status_code == 200
        assert started.json()["status"] == "queued"
        assert started.json()["job_status"] == "queued"
        assert len(repository.jobs) == 1
        restarted = client.post(f"/v1/investigations/{investigation_id}/start")
        assert restarted.status_code == 200
        assert len(repository.jobs) == 1

        repository.investigations[uuid.UUID(investigation_id)]["status"] = "completed"
        terminal_replay = client.post(
            f"/v1/investigations/{investigation_id}/start"
        )
        assert terminal_replay.status_code == 200
        assert terminal_replay.json()["status"] == "completed"
        assert terminal_replay.json()["idempotent_replay"] is True
        assert len(repository.jobs) == 1

        investigation = client.get(f"/v1/investigations/{investigation_id}")
        assert investigation.status_code == 200
        assert investigation.json()["id"] == investigation_id
        assert "create-key-0001" not in investigation.text

        claims = client.get(f"/v1/investigations/{investigation_id}/claims")
        assert claims.status_code == 200
        assert claims.json()["claims"][0]["critical_date"] == "2020-01-02"
        events = client.get(f"/v1/investigations/{investigation_id}/events")
        assert events.status_code == 200
        assert events.json()["events"][0]["event_type"] == "investigation.queued"

        pending = client.get(f"/v1/investigations/{investigation_id}/report-data")
        assert pending.status_code == 202
        assert pending.json()["code"] == "REPORT_PENDING"
        claim_id = repository.list_claim_investigations(investigation_id)[0]["id"]
        repository.gap_items.append(
            {
                "id": uuid.uuid4(),
                "iteration_id": uuid.uuid4(),
                "claim_investigation_id": claim_id,
                "limitation_id": None,
                "gap_type": "combination_gap",
                "gap_key": "combination-motivation",
                "gap_fingerprint": "c" * 64,
                "version_no": 2,
                "supersedes_id": uuid.uuid4(),
                "status": "closed",
                "description": "组合启示证据已补齐",
                "search_objective": {"subtype": "combination_motivation"},
                "resolution_evidence": {"reason": "qualified_document_found"},
                "closed_reason": "qualified_document_found",
                "created_at": datetime(2026, 7, 19, tzinfo=timezone.utc),
            }
        )
        snapshot_id = uuid.uuid4()
        raw_report = repository.get_report_data(investigation_id)
        assert raw_report is not None
        snapshot_state_version = repository.investigations[
            uuid.UUID(investigation_id)
        ]["state_version"]
        repository.create_report_snapshot(
            investigation_id=investigation_id,
            contract_version="v1",
            source_state_version=snapshot_state_version,
            report_snapshot_id=snapshot_id,
            report_snapshot=build_report_data(
                raw_report,
                preview=False,
                report_snapshot_id=snapshot_id,
                generated_at=datetime(2026, 7, 19, tzinfo=timezone.utc),
            ),
        )
        report = client.get(f"/v1/investigations/{investigation_id}/report-data")
        assert report.status_code == 200
        assert report.json()["report_kind"] == "invalidity_evidence_data"
        assert report.json()["is_preview"] is False
        assert report.json()["report_snapshot_id"]
        assert len(report.json()["snapshot_sha256"]) == 64
        assert report.json()["snapshot_hash_scope"] == (
            "canonical_persisted_report_data_v1_excluding_snapshot_sha256"
        )
        assert report.json()["source_state_version"] == snapshot_state_version
        assert report.json()["source_review_revision"] == 0
        canonical_report = dict(report.json())
        canonical_report.pop("snapshot_sha256")
        expected_sha256 = hashlib.sha256(
            json.dumps(
                canonical_report,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        assert report.json()["snapshot_sha256"] == expected_sha256
        assert "provider_debug" not in report.json()
        assert "must-not-leak" not in report.text
        assert report.json()["gap_items"][0]["gap_key"] == "combination-motivation"
        assert report.json()["gap_items"][0]["version_no"] == 2
        assert report.json()["gap_items"][0]["status"] == "closed"
        repeated_report = client.get(
            f"/v1/investigations/{investigation_id}/report-data"
        )
        assert repeated_report.json()["report_snapshot_id"] == report.json()[
            "report_snapshot_id"
        ]
        repository.investigations[uuid.UUID(investigation_id)]["state_version"] += 1
        repository.investigations[uuid.UUID(investigation_id)]["review_revision"] = 1
        repository.investigations[uuid.UUID(investigation_id)][
            "review_recomputation_required"
        ] = True
        stale_default = client.get(
            f"/v1/investigations/{investigation_id}/report-data"
        )
        assert stale_default.status_code == 202
        assert stale_default.json()["code"] == "REPORT_PENDING"
        historical = client.get(
            f"/v1/investigations/{investigation_id}/report-data"
            f"?snapshot_id={snapshot_id}"
        )
        assert historical.status_code == 200
        assert historical.json()["report_snapshot_id"] == str(snapshot_id)
        assert historical.json()["source_state_version"] == snapshot_state_version
        assert historical.json()["source_review_revision"] == 0

    assert repository.initialized == 1
    # Injected repositories are owned by the caller and are not closed by the app.
    assert repository.closed == 0


def test_i5_handler_persists_terminal_i0_snapshot_and_replays_by_snapshot_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="report-handler-session",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
        status="completed",
    )
    investigation["state_version"] = 4
    i0_run = repository.create_module_run(
        investigation_id=investigation["id"],
        module_code=ORCHESTRATOR_MODULE_CODE,
        contract_version="v1",
        input_snapshot={"kind": "start"},
        idempotency_key="i0-final",
        status="created",
    )
    i0_job = repository.record_event_and_enqueue_job(
        investigation_id=investigation["id"],
        module_run_id=i0_run["id"],
        event_type="investigation.queued",
        actor="test",
        event_payload={},
        job_payload={"kind": "start"},
        max_attempts=3,
    )
    i0_run["status"] = "succeeded"
    i0_run["completed_at"] = datetime(2026, 7, 19, tzinfo=timezone.utc)
    i0_job["status"] = "succeeded"
    i0_job["finished_at"] = datetime(2026, 7, 19, tzinfo=timezone.utc)

    snapshot_id = uuid.uuid4()
    i5_run = repository.create_module_run(
        investigation_id=investigation["id"],
        module_code="I5_REPORT",
        contract_version="v1",
        input_snapshot={"kind": "report_snapshot"},
        idempotency_key="report:v1:state:4",
        status="running",
    )
    i5_job = {
        "id": uuid.uuid4(),
        "module_run_id": i5_run["id"],
        "status": "leased",
        "payload": {
            "kind": "report_snapshot",
            "investigation_id": str(investigation["id"]),
            "source_state_version": 4,
            "report_snapshot_id": str(snapshot_id),
            "contract_version": "v1",
        },
    }
    repository.jobs[i5_job["id"]] = i5_job
    heartbeat_count = 0

    def heartbeat() -> dict[str, bool]:
        nonlocal heartbeat_count
        heartbeat_count += 1
        return {"ok": True}

    monkeypatch.setattr(
        "invalidity.workflow.build_workflow", lambda **_kwargs: object()
    )
    configured = settings(tmp_path)
    context = JobContext(
        job=i5_job,
        module_run=i5_run,
        repository=repository,
        settings=configured,
        worker_id="report-worker",
        _heartbeat=heartbeat,
    )
    report_handler = main_module._workflow_handlers(
        configured, repository
    )["I5_REPORT"]
    first = report_handler(context)
    stored = repository.get_report_snapshot(investigation["id"], snapshot_id)
    assert stored is not None
    report_data = stored["report_snapshot"]
    assert any(
        row["id"] == str(i0_run["id"]) and row["status"] == "succeeded"
        for row in report_data["module_runs"]
    )
    assert any(
        row["id"] == str(i0_job["id"]) and row["status"] == "succeeded"
        for row in report_data["jobs"]
    )
    assert report_data["snapshot_hash_scope"] == (
        "canonical_persisted_report_data_v1_excluding_snapshot_sha256"
    )

    repository.events.append(
        {
            "id": 99,
            "investigation_id": investigation["id"],
            "event_type": "report.snapshot_created",
            "actor": "test",
            "payload": {},
            "occurred_at": datetime.now(timezone.utc),
        }
    )
    replay = report_handler(context)
    assert replay == first
    assert len(repository.report_snapshots) == 1
    assert heartbeat_count == 2


def test_api_requires_exact_bearer_token_but_health_is_public(tmp_path: Path) -> None:
    application = create_app(
        settings=settings(tmp_path),
        repository=MemoryRepository(),
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
        missing = client.get("/v1/investigations/not-a-uuid")
        wrong = client.get(
            "/v1/investigations/not-a-uuid",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert missing.status_code == 401
        assert wrong.status_code == 401
        assert missing.json()["code"] == "AUTHENTICATION_REQUIRED"
        assert "WWW-Authenticate" in missing.headers


def test_start_recovers_run_created_before_job_enqueue(tmp_path: Path) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        created = client.post(
            "/v1/investigations",
            json=create_body(analysis_session_id="recover_interrupted_start"),
        ).json()
        investigation_id = created["investigation_id"]
        investigation = repository.get_investigation(investigation_id)
        assert investigation is not None
        repository.update_investigation(investigation_id, status="queued")
        repository.create_module_run(
            investigation_id=investigation_id,
            module_code=ORCHESTRATOR_MODULE_CODE,
            contract_version="v1",
            input_snapshot={
                "investigation_id": investigation_id,
                "source_snapshot_sha256": investigation["source_snapshot_sha256"],
                "pipeline_version": investigation["pipeline_version"],
            },
            idempotency_key=f"investigation:{investigation_id}:start:v1",
            status="created",
        )
        assert repository.jobs == {}

        recovered = client.post(f"/v1/investigations/{investigation_id}/start")
        assert recovered.status_code == 200
        assert recovered.json()["idempotent_replay"] is True
        assert recovered.json()["job_status"] == "queued"
        assert len(repository.jobs) == 1


def test_critical_date_confirmation_and_continuation_are_idempotent(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        created = client.post(
            "/v1/investigations",
            json=create_body(analysis_session_id="critical-date-review-session"),
        ).json()
        investigation_id = created["investigation_id"]
        investigation = repository.get_investigation(investigation_id)
        assert investigation is not None
        investigation["status"] = "needs_human_review"
        investigation["state_version"] = 2
        claim = repository.seed_claim(
            investigation_id,
            status="needs_human_review",
            critical_date=None,
            target_publication_date=None,
        )
        confirmation_body = {
            "claim_investigation_id": str(claim["id"]),
            "decision": "confirm_priority",
            "confirmed_date": "2020-01-02",
            "target_publication_date": "2021-03-04",
            "basis": "priority_document_verified",
            "reason": "已核对优先权文件与该项权利要求主题",
            "expected_state_version": 0,
            "idempotency_key": "critical-confirmation-0001",
        }
        confirmed = client.post(
            f"/v1/investigations/{investigation_id}/critical-date-confirmations",
            json=confirmation_body,
        )
        assert confirmed.status_code == 201
        confirmation_id = confirmed.json()["confirmation_id"]
        assert confirmed.json() == {
            **confirmed.json(),
            "confirmation_id": confirmation_id,
            "claim_investigation_id": str(claim["id"]),
            "confirmed_date": "2020-01-02",
            "target_publication_date": "2021-03-04",
            "critical_date_basis": "priority_document_verified",
            "claim_state_version": 1,
            "investigation_status": "needs_human_review",
            "continuation_required": True,
        }
        replay = client.post(
            f"/v1/investigations/{investigation_id}/critical-date-confirmations",
            json=confirmation_body,
        )
        assert replay.status_code == 200
        assert replay.json()["confirmation_id"] == confirmation_id
        assert len(repository.critical_date_confirmations) == 1

        changed = dict(confirmation_body)
        changed["confirmed_date"] = "2020-01-03"
        conflict = client.post(
            f"/v1/investigations/{investigation_id}/critical-date-confirmations",
            json=changed,
        )
        assert conflict.status_code == 409

        historical_gap = {
            "id": uuid.uuid4(),
            "iteration_id": uuid.uuid4(),
            "claim_investigation_id": claim["id"],
            "gap_type": "feature_gap",
            "gap_key": "feature-gap-F1",
            "gap_fingerprint": "d" * 64,
            "version_no": 1,
            "status": "open",
        }
        repository.gap_items.append(historical_gap.copy())
        no_open_gap_claim = repository.seed_claim(
            investigation_id,
            status="needs_human_review",
        )
        continuation_body = {
            "claim_investigation_ids": [],
            "max_additional_rounds": 2,
            "reason": "关键日已确认，针对未解决 gap 继续检索",
            "expected_state_version": confirmed.json()[
                "investigation_state_version"
            ],
            "idempotency_key": "continuation-batch-0001",
        }
        continued = client.post(
            f"/v1/investigations/{investigation_id}/continuations",
            json=continuation_body,
        )
        assert continued.status_code == 201
        assert continued.json()["status"] == "queued"
        assert continued.json()["max_additional_rounds"] == 2
        assert continued.json()["claim_investigation_ids"] == [str(claim["id"])]
        assert len(repository.continuation_batches) == 1
        job_count = len(repository.jobs)
        continuation_replay = client.post(
            f"/v1/investigations/{investigation_id}/continuations",
            json=continuation_body,
        )
        assert continuation_replay.status_code == 200
        assert continuation_replay.json()["continuation_batch_id"] == continued.json()[
            "continuation_batch_id"
        ]
        assert len(repository.jobs) == job_count
        assert repository.gap_items == [historical_gap]

        no_gap_continuation = client.post(
            f"/v1/investigations/{investigation_id}/continuations",
            json={
                **continuation_body,
                "claim_investigation_ids": [str(no_open_gap_claim["id"])],
                "expected_state_version": continued.json()[
                    "investigation_state_version"
                ],
                "idempotency_key": "continuation-no-open-gap",
            },
        )
        assert no_gap_continuation.status_code == 409
        assert len(repository.continuation_batches) == 1
        assert len(repository.jobs) == job_count


@pytest.mark.parametrize(
    "successful_status",
    ("novelty_evidence_complete", "inventive_step_evidence_complete"),
)
def test_continuation_rejects_successful_claim_terminal_states(
    tmp_path: Path,
    successful_status: str,
) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    investigation = repository.create_investigation(
        analysis_session_id=f"continuation-success-{successful_status}",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
        status="completed",
    )
    claim = repository.seed_claim(
        investigation["id"], status=successful_status
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.post(
            f"/v1/investigations/{investigation['id']}/continuations",
            json={
                "claim_investigation_ids": [str(claim["id"])],
                "max_additional_rounds": 1,
                "reason": "普通继续不应重开成功证据终态",
                "expected_state_version": 0,
                "idempotency_key": f"reject-{successful_status}",
            },
        )
    assert response.status_code == 409
    assert repository.continuation_batches == {}
    assert repository.jobs == {}


def test_module_lab_creates_fresh_run_for_every_click(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    body = {
        "module_code": "I2_QUERY_PLAN",
        "input_mode": "fixture",
        "input": {"claim": "一种测试装置", "apiKey": "must-not-leak"},
        "idempotency_key": "module-lab-key-1",
    }
    with TestClient(application, headers=AUTH_HEADERS) as client:
        created = client.post("/v1/lab/module-runs", json=body)
        assert created.status_code == 201
        module_run_id = created.json()["module_run_id"]
        assert created.json()["status"] == "queued"
        assert created.json()["idempotent_replay"] is False
        assert len(repository.jobs) == 1

        # 2026-08-02 用户决定：律师模块实验室每次点击都必须创建全新的
        # 运行和作业，即使输入与上一次完全一致也不允许幂等复用旧运行。
        replay = client.post("/v1/lab/module-runs", json=body)
        assert replay.status_code == 201
        assert replay.json()["idempotent_replay"] is False
        assert replay.json()["module_run_id"] != module_run_id
        assert len(repository.jobs) == 2

        different = dict(body)
        different["input"] = {"claim": "不同输入"}
        conflict = client.post("/v1/lab/module-runs", json=different)
        assert conflict.status_code == 409

        details = client.get(f"/v1/lab/module-runs/{module_run_id}")
        assert details.status_code == 200
        assert details.json()["job"]["status"] == "queued"
        assert "must-not-leak" not in details.text
        assert details.json()["module_run"]["input_snapshot"]["request"]["input"][
            "apiKey"
        ] == "[REDACTED]"


def test_module5_inputs_recovers_latest_fetch_lineage_and_existing_comparisons(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="module5-recovery",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
    )
    claim = repository.seed_claim(investigation["id"])
    base_time = datetime(2026, 7, 28, 1, tzinfo=timezone.utc)

    def add_run(
        *,
        module_code: str,
        created_at: datetime,
        document_id: str,
        output: Mapping[str, Any] | None = None,
        status: str = "succeeded",
        candidate_filter_run_id: str | None = None,
    ) -> None:
        run_id = uuid.uuid4()
        repository.module_runs[run_id] = {
            "id": run_id,
            "investigation_id": investigation["id"],
            "claim_investigation_id": claim["id"],
            "module_code": module_code,
            "status": status,
            "effective_mode": "live",
            "created_at": created_at,
            "input_snapshot": {
                "request": {
                    "claim_investigation_id": str(claim["id"]),
                    "input": {
                        "document_id": document_id,
                        **(
                            {"candidate_filter_run_id": candidate_filter_run_id}
                            if candidate_filter_run_id
                            else {}
                        ),
                    },
                }
            },
            "output_snapshot": {"output": dict(output or {})},
        }

    add_run(
        module_code="I3_FETCH",
        created_at=base_time - timedelta(hours=2),
        document_id="OLD-DOCUMENT",
        candidate_filter_run_id="old-filter-run",
        output={
            "stage": "retrieved_document",
            "external_id": "OLD-DOCUMENT",
            "publication_number": "OLD-DOCUMENT",
            "title": "Old document",
            "content_sha256": "hash-old",
            "analysis_ready": True,
            "readable_document": {"full_text": "old full text"},
        },
    )
    for index, document_id in enumerate(("US-A", "AU-B", "CN-C")):
        add_run(
            module_code="I3_FETCH",
            created_at=base_time + timedelta(minutes=index),
            document_id=document_id,
            candidate_filter_run_id="current-filter-run",
            output={
                "stage": "retrieved_document",
                "external_id": document_id,
                "publication_number": document_id,
                "title": f"Document {document_id}",
                "content_sha256": f"hash-{document_id}",
                "analysis_ready": True,
                "readable_document": {"full_text": f"full text {document_id}"},
            },
        )
    add_run(
        module_code="I4_S_SINGLE_REFERENCE",
        created_at=base_time - timedelta(hours=1),
        document_id="US-A",
        status="failed",
    )
    add_run(
        module_code="I4_S_SINGLE_REFERENCE",
        created_at=base_time + timedelta(minutes=10),
        document_id="US-A",
        status="failed",
    )
    add_run(
        module_code="I4_S_SINGLE_REFERENCE",
        created_at=base_time + timedelta(minutes=12),
        document_id="AU-B",
        status="failed",
    )

    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.get(
            f"/v1/lab/investigations/{investigation['id']}/module5-inputs",
            params={"claim_investigation_id": str(claim["id"])},
        )
    assert response.status_code == 200
    assert response.json()["source"] == "latest_retrieved_document_lineage"
    assert response.json()["document_ids"] == ["US-A", "AU-B", "CN-C"]
    assert [item["content_sha256"] for item in response.json()["documents"]] == [
        "hash-US-A",
        "hash-AU-B",
        "hash-CN-C",
    ]
    assert [
        item["document_id"] for item in response.json()["comparison_runs"]
    ] == ["US-A", "AU-B"]
    assert all(
        item["status"] == "failed"
        and item["module_run_id"]
        for item in response.json()["comparison_runs"]
    )


def test_module9_cursor_recovers_latest_successful_gap_round(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="module9-cursor-recovery",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
    )
    claim = repository.seed_claim(investigation["id"])
    base_time = datetime(2026, 8, 9, 1, tzinfo=timezone.utc)
    for iteration in (1, 2):
        run_id = uuid.uuid4()
        repository.module_runs[run_id] = {
            "id": run_id,
            "investigation_id": investigation["id"],
            "claim_investigation_id": claim["id"],
            "module_code": "I2_GAP_QUERY_PLAN",
            "status": "succeeded",
            "effective_mode": "live",
            "created_at": base_time + timedelta(minutes=iteration),
            "input_snapshot": {
                "request": {"claim_investigation_id": str(claim["id"])}
            },
            "output_snapshot": {
                "output": {
                    "gap_search_iteration": iteration,
                    "allowed_gap_feature_ids": ["F-slot", "F-projection"],
                    "queries": [
                        {
                            "query_id": f"gap-{iteration}",
                            "expression": f"耳机 AND 定位卡槽{iteration}",
                        }
                    ],
                    "gap_reuse_decision": {
                        "gap_search_iteration": iteration,
                        "previous_iteration_failure_reason": (
                            f"第 {iteration} 轮仍未覆盖"
                        ),
                    },
                }
            },
        }

    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.get(
            f"/v1/lab/investigations/{investigation['id']}/module9-cursor",
            params={"claim_investigation_id": str(claim["id"])},
        )

    assert response.status_code == 200
    assert response.json()["latest_iteration"] == 2
    assert response.json()["next_iteration"] == 3
    assert response.json()["previous_query_expressions"] == [
        "耳机 AND 定位卡槽2"
    ]
    assert response.json()["active_gap_feature_ids"] == [
        "F-slot",
        "F-projection",
    ]
    assert response.json()["previous_iteration_failure_reason"] == (
        "第 2 轮仍未覆盖"
    )


def test_live_patsnap_patent_lab_job_has_one_durable_attempt(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="patsnap-one-attempt-lab",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
    )
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    base = {
        "module_code": "I3_PATENT_SEARCH",
        "input_mode": "live",
        "investigation_id": str(investigation["id"]),
        "input": {
            "search_provider": "patsnap",
            "query": "TACD:(optical sensor AND sealed chamber)",
        },
    }
    with TestClient(application, headers=AUTH_HEADERS) as client:
        patsnap = client.post(
            "/v1/lab/module-runs",
            json={**base, "idempotency_key": "patsnap-single-attempt"},
        )
        assert patsnap.status_code == 201
        google = client.post(
            "/v1/lab/module-runs",
            json={
                **base,
                "input": {
                    "search_provider": "google_patents",
                    "query": "optical sensor sealed chamber",
                },
                "idempotency_key": "google-normal-attempts",
            },
        )
        assert google.status_code == 201

    jobs_by_run = {
        str(job["module_run_id"]): job for job in repository.jobs.values()
    }
    assert jobs_by_run[patsnap.json()["module_run_id"]]["max_attempts"] == 1
    assert jobs_by_run[google.json()["module_run_id"]]["max_attempts"] == 3


@pytest.mark.parametrize(
    "module_code,input_data",
    [
        (
            "I2_QUERY_PLAN",
            {
                "claim_id": "1",
                "expanded_claim_text": "一种测试装置，包括测试构件。",
            },
        ),
        ("I4_S_SINGLE_REFERENCE", {"document_id": "US-test"}),
        ("I4_I_INVENTIVE_STEP", {}),
    ],
)
def test_live_model_job_uses_one_durable_transport_attempt(
    tmp_path: Path,
    module_code: str,
    input_data: dict[str, Any],
) -> None:
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="i4s-batch-owned-retry",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
    )
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        created = client.post(
            "/v1/lab/module-runs",
            json={
                "module_code": module_code,
                "input_mode": "live",
                "investigation_id": str(investigation["id"]),
                "input": input_data,
                "idempotency_key": f"{module_code.lower()}-single-attempt",
            },
        )
    assert created.status_code == 201
    job = next(iter(repository.jobs.values()))
    assert job["max_attempts"] == 1


def test_manual_i2_job_does_not_repeat_a_transport_timeout(tmp_path: Path) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        created = client.post(
            "/v1/lab/module-runs",
            json={
                "module_code": "I2_QUERY_PLAN",
                "input_mode": "manual",
                "input": {
                    "claim_id": "1",
                    "expanded_claim_text": "一种测试装置，包括测试构件。",
                    "target_images": ["/tmp/target.png"],
                },
                "idempotency_key": "manual-i2-single-transport-attempt",
            },
        )
    assert created.status_code == 201
    job = next(iter(repository.jobs.values()))
    assert job["max_attempts"] == 1


def test_live_target_snapshot_api_requires_investigation_and_empty_input(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="target-snapshot-lab",
        source_snapshot={
            "request": {"provider_mode": "live"},
            "snapshot_state": "source_bytes_frozen",
        },
        pipeline_version="test",
    )
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    base = {
        "module_code": "I1_TARGET_SNAPSHOT",
        "input_mode": "live",
        "input": {},
    }
    with TestClient(application, headers=AUTH_HEADERS) as client:
        missing_investigation = client.post(
            "/v1/lab/module-runs",
            json={
                **base,
                "idempotency_key": "target-snapshot-no-investigation",
            },
        )
        assert missing_investigation.status_code == 422

        forged_input = client.post(
            "/v1/lab/module-runs",
            json={
                **base,
                "investigation_id": str(investigation["id"]),
                "input": {"claim_id": "client-must-not-select"},
                "idempotency_key": "target-snapshot-forged-input",
            },
        )
        assert forged_input.status_code == 422

        accepted = client.post(
            "/v1/lab/module-runs",
            json={
                **base,
                "investigation_id": str(investigation["id"]),
                "idempotency_key": "target-snapshot-accepted",
            },
        )
        assert accepted.status_code == 201

    assert len(repository.jobs) == 1
    queued_job = next(iter(repository.jobs.values()))
    assert queued_job["module_run_id"] == uuid.UUID(accepted.json()["module_run_id"])
    assert queued_job["max_attempts"] == 3


def test_module_lab_target_figure_is_hash_checked_and_run_scoped(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="target-figure-lab",
        source_snapshot={
            "request": {"provider_mode": "live"},
            "snapshot_state": "source_bytes_frozen",
        },
        pipeline_version="test",
    )
    service_settings = settings(tmp_path)
    application = create_app(
        settings=service_settings,
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        accepted = client.post(
            "/v1/lab/module-runs",
            json={
                "module_code": "I1_TARGET_SNAPSHOT",
                "input_mode": "live",
                "investigation_id": str(investigation["id"]),
                "input": {},
                "idempotency_key": "target-figure-read",
            },
        )
        assert accepted.status_code == 201
        module_run_id = accepted.json()["module_run_id"]
        artifact_dir = (
            service_settings.artifact_root
            / "investigations"
            / str(investigation["id"])
            / "lab"
            / "module1"
            / module_run_id
        )
        figure_dir = artifact_dir / "figures"
        figure_dir.mkdir(parents=True)
        image = b"\x89PNG\r\n\x1a\nhash-checked-target-figure"
        image_path = figure_dir / "figure-000.png"
        image_path.write_bytes(image)
        (artifact_dir / "target_snapshot.json").write_text(
            json.dumps(
                {
                    "figures": [
                        {
                            "lab_asset_index": 0,
                            "file_path": str(image_path),
                            "mime_type": "image/png",
                            "file_sha256": hashlib.sha256(image).hexdigest(),
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        response = client.get(
            f"/v1/lab/module-runs/{module_run_id}/target-figures/0"
        )
        assert response.status_code == 200
        assert response.content == image
        image_path.write_bytes(b"tampered")
        tampered = client.get(
            f"/v1/lab/module-runs/{module_run_id}/target-figures/0"
        )
        assert tampered.status_code == 409


def test_investigation_module_runs_endpoint_returns_snapshots_redacted(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    investigation = repository.create_investigation(
        analysis_session_id="module-runs-list",
        source_snapshot={"snapshot_state": "source_bytes_frozen"},
        pipeline_version="test",
    )
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        accepted = client.post(
            "/v1/lab/module-runs",
            json={
                "module_code": "I2_QUERY_PLAN",
                "input_mode": "live",
                "investigation_id": str(investigation["id"]),
                "input": {"note": "示例", "api_key": "must-not-leak"},
                "idempotency_key": "module-runs-list-1",
            },
        )
        assert accepted.status_code == 201
        run_id = accepted.json()["module_run_id"]
        repository.module_runs[__import__("uuid").UUID(run_id)]["output_snapshot"] = {
            "queries": ["水枪 AND 阀"],
            "authorization": "Bearer must-not-leak",
        }
        response = client.get(f"/v1/investigations/{investigation['id']}/module-runs")
        assert response.status_code == 200
        payload = response.json()
        assert payload["investigation_id"] == str(investigation["id"])
        runs = payload["module_runs"]
        assert len(runs) == 1
        assert runs[0]["module_code"] == "I2_QUERY_PLAN"
        assert runs[0]["input_snapshot"]["request"]["input"]["note"] == "示例"
        assert runs[0]["input_snapshot"]["request"]["input"]["api_key"] == "[REDACTED]"
        assert runs[0]["output_snapshot"]["queries"] == ["水枪 AND 阀"]
        assert runs[0]["output_snapshot"]["authorization"] == "[REDACTED]"
        unknown = client.get(
            "/v1/investigations/00000000-0000-0000-0000-000000000000/module-runs"
        )
        assert unknown.status_code == 404


def test_investigation_target_figure_is_hash_checked_and_investigation_scoped(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    service_settings = settings(tmp_path)
    image = b"\x89PNG\r\n\x1a\ninvestigation-scoped-target-figure"
    investigation = repository.create_investigation(
        analysis_session_id="target-figure-investigation",
        source_snapshot={
            "request": {"provider_mode": "live"},
            "snapshot_state": "source_bytes_frozen",
            "patent_figure_artifacts": [
                {
                    "uri": str(
                        service_settings.artifact_root
                        / "investigations"
                        / "placeholder"
                        / "source"
                        / "patent-figures"
                        / "figure-000.png"
                    ),
                    "sha256": hashlib.sha256(image).hexdigest(),
                    "byte_size": len(image),
                    "mime_type": "image/png",
                    "artifact_type": "target_patent_figure_snapshot",
                }
            ],
        },
        pipeline_version="test",
    )
    # 建库后补齐真实工件路径（调查 ID 建库前未知）
    figure_dir = (
        service_settings.artifact_root
        / "investigations"
        / str(investigation["id"])
        / "source"
        / "patent-figures"
    )
    figure_dir.mkdir(parents=True)
    image_path = figure_dir / "figure-000.png"
    image_path.write_bytes(image)
    investigation["source_snapshot"]["patent_figure_artifacts"][0]["uri"] = str(
        image_path
    )
    application = create_app(
        settings=service_settings,
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.get(
            f"/v1/investigations/{investigation['id']}/target-figures/0"
        )
        assert response.status_code == 200
        assert response.content == image
        # 越界索引与路径逃逸均不得泄露文件
        assert (
            client.get(
                f"/v1/investigations/{investigation['id']}/target-figures/5"
            ).status_code
            == 404
        )
        investigation["source_snapshot"]["patent_figure_artifacts"][0]["uri"] = str(
            tmp_path / "outside.png"
        )
        assert (
            client.get(
                f"/v1/investigations/{investigation['id']}/target-figures/0"
            ).status_code
            == 404
        )
        investigation["source_snapshot"]["patent_figure_artifacts"][0]["uri"] = str(
            image_path
        )
        image_path.write_bytes(b"tampered")
        assert (
            client.get(
                f"/v1/investigations/{investigation['id']}/target-figures/0"
            ).status_code
            == 409
        )


def test_module_lab_validates_and_persists_claim_iteration_ownership(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    first = repository.create_investigation(
        analysis_session_id="lab-owner-first",
        source_snapshot={"fixture": True},
        pipeline_version="test",
    )
    second = repository.create_investigation(
        analysis_session_id="lab-owner-second",
        source_snapshot={"fixture": True},
        pipeline_version="test",
    )
    foreign_claim = repository.seed_claim(second["id"])
    own_claim = repository.seed_claim(first["id"])
    own_iteration = repository.seed_iteration(own_claim["id"], iteration_no=2)
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    base = {
        "module_code": "I2_QUERY_PLAN",
        "input_mode": "fixture",
        "investigation_id": str(first["id"]),
        "iteration_number": 2,
        "input": {"claim": "一种测试装置"},
    }
    with TestClient(application, headers=AUTH_HEADERS) as client:
        rejected = client.post(
            "/v1/lab/module-runs",
            json={
                **base,
                "claim_investigation_id": str(foreign_claim["id"]),
                "idempotency_key": "lab-foreign-owner-key",
            },
        )
        assert rejected.status_code == 409

        accepted = client.post(
            "/v1/lab/module-runs",
            json={
                **base,
                "claim_investigation_id": str(own_claim["id"]),
                "idempotency_key": "lab-owned-iteration-key",
            },
        )
        assert accepted.status_code == 201
        run = repository.module_runs[uuid.UUID(accepted.json()["module_run_id"])]
        assert run["claim_investigation_id"] == own_claim["id"]
        assert run["iteration_id"] == own_iteration["id"]


def test_module_lab_cancel_and_manual_retry_preserve_run_history(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    base = {
        "module_code": "I2_QUERY_PLAN",
        "input_mode": "fixture",
        "input": {"fixture": "default"},
    }
    with TestClient(application, headers=AUTH_HEADERS) as client:
        cancellable = client.post(
            "/v1/lab/module-runs",
            json={**base, "idempotency_key": "cancel-module-run-key"},
        ).json()
        cancelled = client.post(
            f"/v1/lab/module-runs/{cancellable['module_run_id']}/cancel",
            json={"reason": "用户停止本次夹具运行"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["job_status"] == "cancelled"
        assert cancelled.json()["idempotent_replay"] is False
        replayed_cancel = client.post(
            f"/v1/lab/module-runs/{cancellable['module_run_id']}/cancel",
            json={"reason": "重复点击不应新增事件"},
        )
        assert replayed_cancel.status_code == 200
        assert replayed_cancel.json()["idempotent_replay"] is True
        cancelled_events = [
            event for event in repository.events
            if str(event.get("module_run_id")) == cancellable["module_run_id"]
            and event.get("event_type") == "job.cancelled"
        ]
        assert len(cancelled_events) == 1

        failed_created = client.post(
            "/v1/lab/module-runs",
            json={**base, "idempotency_key": "failed-module-run-key"},
        ).json()
        failed_id = uuid.UUID(failed_created["module_run_id"])
        original_run = repository.module_runs[failed_id]
        original_run["status"] = "failed"
        original_run["output_snapshot"] = {"diagnostic": "old run remains immutable"}
        original_job = next(
            job for job in repository.jobs.values()
            if job["module_run_id"] == failed_id
        )
        original_job["status"] = "failed"

        retry_body = {
            "reason": "修复输入后由用户明确重试",
            "idempotency_key": "manual-retry-action-key",
        }
        retried = client.post(
            f"/v1/lab/module-runs/{failed_id}/retries",
            json=retry_body,
        )
        assert retried.status_code == 201
        retried_id = uuid.UUID(retried.json()["module_run_id"])
        assert retried_id != failed_id
        assert retried.json()["retry_of_module_run_id"] == str(failed_id)
        assert retried.json()["job_status"] == "queued"
        assert repository.module_runs[failed_id]["status"] == "failed"
        assert repository.module_runs[failed_id]["output_snapshot"] == {
            "diagnostic": "old run remains immutable"
        }
        assert repository.module_runs[retried_id]["retry_of_module_run_id"] == failed_id
        retried_job = next(
            job for job in repository.jobs.values()
            if job["module_run_id"] == retried_id
        )
        assert retried_job["attempt_count"] == 0

        replayed_retry = client.post(
            f"/v1/lab/module-runs/{failed_id}/retries",
            json=retry_body,
        )
        assert replayed_retry.status_code == 200
        assert replayed_retry.json()["module_run_id"] == str(retried_id)
        assert replayed_retry.json()["idempotent_replay"] is True
        assert len(repository.module_runs) == 3


class WorkerRepository:
    def __init__(self, *, module_code: str = ORCHESTRATOR_MODULE_CODE, max_attempts: int = 1) -> None:
        self.lock = threading.Lock()
        self.job_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.investigation_id = uuid.uuid4()
        self.job = {
            "id": self.job_id,
            "module_run_id": self.run_id,
            "status": "leased",
            "lease_token": uuid.uuid4(),
            "attempt_count": 0,
            "max_attempts": max_attempts,
        }
        self.module_run = {
            "id": self.run_id,
            "investigation_id": self.investigation_id,
            "module_code": module_code,
            "input_snapshot": {},
        }
        self.claimed = False
        self.recovered_expired_lease = False
        self.heartbeat_calls = 0
        self.complete_calls: list[Mapping[str, Any]] = []
        self.fail_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[dict[str, Any]] = []
        self.shutdown_release_calls: list[dict[str, Any]] = []
        self.cancel_requested = False
        self.investigation_updates: list[dict[str, Any]] = []

    def claim_job(self, *, worker_id: str, lease_seconds: int) -> Mapping[str, Any] | None:
        with self.lock:
            if self.claimed:
                return None
            self.claimed = True
            self.recovered_expired_lease = self.job["status"] == "leased"
            self.job["attempt_count"] += 1
            self.job["lease_owner"] = worker_id
            return dict(self.job)

    def get_module_run_by_job(self, _job_id: Any) -> Mapping[str, Any] | None:
        return dict(self.module_run)

    def heartbeat_job(self, **_: Any) -> Mapping[str, Any]:
        with self.lock:
            self.heartbeat_calls += 1
            row = dict(self.job)
            if self.cancel_requested:
                row["cancel_requested_at"] = datetime(
                    2026, 7, 19, 3, tzinfo=timezone.utc
                )
        return row

    def complete_job(self, *, output_snapshot: Mapping[str, Any], **_: Any) -> Mapping[str, Any]:
        self.complete_calls.append(dict(output_snapshot))
        return {"job": {"status": "succeeded"}, "module_run": {"status": "succeeded"}}

    def fail_job(self, **kwargs: Any) -> Mapping[str, Any]:
        self.fail_calls.append(dict(kwargs))
        if self.cancel_requested:
            return {"cancelled": True, "will_retry": False}
        will_retry = bool(
            kwargs["retryable"]
            and self.job["attempt_count"] < self.job["max_attempts"]
        )
        return {"will_retry": will_retry}

    def cancel_leased_job(self, **kwargs: Any) -> Mapping[str, Any]:
        self.cancel_calls.append(dict(kwargs))
        self.job["status"] = "cancelled"
        self.module_run["status"] = "cancelled"
        return {
            "cancelled": True,
            "job": dict(self.job),
            "module_run": dict(self.module_run),
        }

    def release_job_lease_for_shutdown(self, **kwargs: Any) -> Mapping[str, Any]:
        with self.lock:
            self.shutdown_release_calls.append(dict(kwargs))
            if (
                self.job.get("status") != "leased"
                or self.job.get("lease_owner") != kwargs["worker_id"]
                or self.job.get("lease_token") != kwargs["lease_token"]
            ):
                return {"released": False}
            self.job["status"] = "queued"
            self.job["attempt_count"] = max(
                0, int(self.job["attempt_count"]) - 1
            )
            self.job["lease_owner"] = None
            self.job["lease_token"] = None
            self.module_run["status"] = "queued"
            return {
                "released": True,
                "job": dict(self.job),
                "module_run": dict(self.module_run),
            }

    def update_investigation(self, _investigation_id: Any, **kwargs: Any) -> None:
        self.investigation_updates.append(dict(kwargs))


def test_worker_recovers_expired_lease_heartbeats_and_completes_once(
    tmp_path: Path,
) -> None:
    repository = WorkerRepository()

    def handler(_context: Any) -> Mapping[str, Any]:
        time.sleep(0.06)
        return {
            "status": "completed",
            "document_id": uuid.UUID("00000000-0000-0000-0000-000000000001"),
            "completed_at": date(2026, 7, 19),
            "Authorization": "Bearer must-not-leak",
        }

    worker = DurableWorker(
        settings=settings(tmp_path),
        repository=repository,
        handlers={ORCHESTRATOR_MODULE_CODE: handler},
        worker_id="fixture-worker",
        heartbeat_interval_seconds=0.01,
    )
    result = worker.run_once()
    assert result.outcome == WorkerOutcome.SUCCEEDED
    assert repository.recovered_expired_lease is True
    assert repository.heartbeat_calls >= 1
    assert len(repository.complete_calls) == 1
    # Protected replay snapshots keep source data; public API/report boundaries redact it.
    assert repository.complete_calls[0]["Authorization"] == "Bearer must-not-leak"
    assert repository.complete_calls[0]["completed_at"] == "2026-07-19"
    assert repository.fail_calls == []


def test_worker_cooperatively_cancels_and_never_writes_success_after_request(
    tmp_path: Path,
) -> None:
    repository = WorkerRepository(module_code="I2_QUERY_PLAN")

    def handler(context: JobContext) -> Mapping[str, Any]:
        repository.cancel_requested = True
        assert context.stop_requested() is True
        return {"status": "cancelled", "partial_output": "discarded"}

    worker = DurableWorker(
        settings=settings(tmp_path),
        repository=repository,
        handlers={"I2_QUERY_PLAN": handler},
        worker_id="cancel-aware-worker",
        heartbeat_interval_seconds=0,
    )
    result = worker.run_once()
    assert result.outcome == WorkerOutcome.CANCELLED
    assert repository.job["status"] == "cancelled"
    assert repository.module_run["status"] == "cancelled"
    assert len(repository.cancel_calls) == 1
    assert repository.complete_calls == []
    assert repository.fail_calls == []


def test_worker_shutdown_requeues_active_lease_and_rejects_late_success(
    tmp_path: Path,
) -> None:
    repository = WorkerRepository(max_attempts=1)
    entered = threading.Event()
    allow_return = threading.Event()

    def handler(_context: JobContext) -> Mapping[str, Any]:
        entered.set()
        assert allow_return.wait(timeout=5)
        return {"status": "completed", "late": True}

    worker = DurableWorker(
        settings=settings(tmp_path),
        repository=repository,
        handlers={ORCHESTRATOR_MODULE_CODE: handler},
        worker_id="shutdown-worker",
        heartbeat_interval_seconds=0,
    )
    result_holder: list[Any] = []
    thread = threading.Thread(
        target=lambda: result_holder.append(worker.run_once()),
        daemon=True,
    )
    thread.start()
    assert entered.wait(timeout=5)

    released = worker.release_active_lease_for_shutdown()
    assert released["released"] is True
    assert repository.job["status"] == "queued"
    assert repository.job["attempt_count"] == 0
    assert repository.module_run["status"] == "queued"
    allow_return.set()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert result_holder[0].outcome == WorkerOutcome.LEASE_LOST
    assert repository.complete_calls == []
    assert repository.fail_calls == []
    assert len(repository.shutdown_release_calls) == 1


@pytest.mark.parametrize(
    "raised",
    [
        RuntimeError("provider failed Authorization: Bearer must-not-leak"),
        PermanentJobError("MODEL_FAILED", "模型失败 token=must-not-leak"),
    ],
)
def test_worker_never_completes_exceptions_or_leaks_their_secrets(
    tmp_path: Path, raised: BaseException
) -> None:
    repository = WorkerRepository(max_attempts=1)

    def handler(_context: Any) -> Mapping[str, Any]:
        raise raised

    worker = DurableWorker(
        settings=settings(tmp_path),
        repository=repository,
        handlers={ORCHESTRATOR_MODULE_CODE: handler},
        worker_id="fixture-worker",
        heartbeat_interval_seconds=0,
    )
    result = worker.run_once()
    assert result.outcome == WorkerOutcome.FAILED
    assert repository.complete_calls == []
    assert len(repository.fail_calls) == 1
    persisted = json.dumps(repository.fail_calls, ensure_ascii=False, default=str)
    assert "must-not-leak" not in persisted
    assert repository.investigation_updates[0]["status"] == "failed"


def test_worker_retries_only_when_failure_policy_and_attempt_budget_allow_it(
    tmp_path: Path,
) -> None:
    repository = WorkerRepository(max_attempts=2)

    def handler(_context: Any) -> Mapping[str, Any]:
        raise RetryableJobError("PROVIDER_TIMEOUT", "provider 暂时超时")

    worker = DurableWorker(
        settings=settings(tmp_path),
        repository=repository,
        handlers={ORCHESTRATOR_MODULE_CODE: handler},
        worker_id="fixture-worker",
        heartbeat_interval_seconds=0,
    )
    result = worker.run_once()
    assert result.outcome == WorkerOutcome.RETRY_SCHEDULED
    assert repository.complete_calls == []
    assert repository.fail_calls[0]["retryable"] is True
    assert repository.investigation_updates == []


def test_worker_treats_reported_failed_output_as_failure(tmp_path: Path) -> None:
    repository = WorkerRepository(max_attempts=1)
    worker = DurableWorker(
        settings=settings(tmp_path),
        repository=repository,
        handlers={ORCHESTRATOR_MODULE_CODE: lambda _context: {"status": "failed"}},
        worker_id="fixture-worker",
        heartbeat_interval_seconds=0,
    )
    result = worker.run_once()
    assert result.outcome == WorkerOutcome.FAILED
    assert repository.complete_calls == []
    assert repository.fail_calls[0]["error_code"] == "HANDLER_REPORTED_FAILURE"


def test_worker_writes_restricted_diagnostic_for_unexpected_exceptions(
    tmp_path: Path,
) -> None:
    repository = WorkerRepository(max_attempts=1)

    def handler(_context: Any) -> Mapping[str, Any]:
        raise RuntimeError("provider failed Authorization: Bearer must-not-leak")

    worker = DurableWorker(
        settings=settings(tmp_path),
        repository=repository,
        handlers={ORCHESTRATOR_MODULE_CODE: handler},
        worker_id="fixture-worker",
        heartbeat_interval_seconds=0,
    )
    result = worker.run_once()
    assert result.outcome == WorkerOutcome.FAILED

    diagnostic_path = (
        tmp_path
        / "invalidity"
        / "test"
        / "diagnostics"
        / "worker-errors-test.jsonl"
    )
    assert diagnostic_path.exists()
    assert (diagnostic_path.stat().st_mode & 0o777) == 0o600
    records = [
        json.loads(line)
        for line in diagnostic_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1
    record = records[0]
    assert record["exception_type"] == "RuntimeError"
    assert record["module_code"] == ORCHESTRATOR_MODULE_CODE
    assert record["worker_id"] == "fixture-worker"
    assert "Traceback" in record["traceback"]
    # 受限诊断保留真实原因，但不得原文保留凭据。
    assert "provider failed" in record["exception_message"]
    assert "must-not-leak" not in record["exception_message"]
    assert "must-not-leak" not in record["traceback"]
    # 对外持久化的失败信息仍然是脱敏版本。
    persisted = json.dumps(repository.fail_calls, ensure_ascii=False, default=str)
    assert "must-not-leak" not in persisted


def test_investigation_cancel_stops_running_investigation(tmp_path: Path) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    investigation = repository.create_investigation(
        analysis_session_id="cancel-running-session",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
        status="running",
    )
    i0_run = repository.create_module_run(
        investigation_id=investigation["id"],
        module_code=ORCHESTRATOR_MODULE_CODE,
        contract_version="v1",
        input_snapshot={"kind": "start"},
        idempotency_key="cancel-running-i0",
        status="running",
    )
    i0_job = repository.record_event_and_enqueue_job(
        investigation_id=investigation["id"],
        module_run_id=i0_run["id"],
        event_type="investigation.queued",
        actor="test",
        event_payload={},
        job_payload={"kind": "start"},
        max_attempts=3,
    )
    i0_job["status"] = "leased"
    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.post(
            f"/v1/investigations/{investigation['id']}/cancel",
            json={"reason": "用户要求强制停止"},
        )
    assert response.status_code == 202
    body = response.json()
    # 调查状态由 worker 心跳收口后标记，接口返回时仍是 running。
    assert body["status"] == "running"
    assert body["cancel_requested_run_ids"] == [str(i0_run["id"])]
    assert repository.module_runs[i0_run["id"]]["status"] == "cancellation_requested"
    assert i0_job["cancel_requested_at"] is not None
    # 租约中的 job 由 worker 心跳处协作式收口，调查状态由 worker 标记。
    assert repository.investigations[investigation["id"]]["status"] == "running"


def test_investigation_cancel_marks_idle_investigation_cancelled(tmp_path: Path) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    investigation = repository.create_investigation(
        analysis_session_id="cancel-idle-session",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
        status="created",
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.post(
            f"/v1/investigations/{investigation['id']}/cancel",
            json={"reason": "启动前用户取消"},
        )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["cancel_requested_run_ids"] == []
    assert repository.investigations[investigation["id"]]["status"] == "cancelled"


def test_investigation_cancel_is_idempotent_for_terminal_investigation(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    investigation = repository.create_investigation(
        analysis_session_id="cancel-terminal-session",
        source_snapshot={"request": {"provider_mode": "live"}},
        pipeline_version="test",
        status="completed",
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        response = client.post(
            f"/v1/investigations/{investigation['id']}/cancel",
            json={"reason": "重复点击"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["idempotent_replay"] is True
    assert response.json()["cancel_requested_run_ids"] == []
    assert not any(
        event.get("event_type") == "investigation.cancelled"
        for event in repository.events
    )


def test_investigation_cancel_rejects_empty_reason_and_unknown_id(
    tmp_path: Path,
) -> None:
    repository = MemoryRepository()
    application = create_app(
        settings=settings(tmp_path),
        repository=repository,
        handler_registry={},
        start_worker=False,
    )
    with TestClient(application, headers=AUTH_HEADERS) as client:
        empty_reason = client.post(
            f"/v1/investigations/{uuid.uuid4()}/cancel",
            json={"reason": ""},
        )
        assert empty_reason.status_code == 422
        unknown = client.post(
            f"/v1/investigations/{uuid.uuid4()}/cancel",
            json={"reason": "不存在的调查"},
        )
        assert unknown.status_code == 404
