from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import socket
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable

from .config import Settings
from .db import LeaseLostError
from .report import json_safe, public_exception_message, public_json


logger = logging.getLogger(__name__)

_DIAGNOSTIC_MESSAGE_LIMIT = 4000
_DIAGNOSTIC_TRACEBACK_LIMIT = 12000
_DIAGNOSTIC_FILE_MAX_BYTES = 50 * 1024 * 1024
_CREDENTIAL_PATTERNS = (
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"Basic\s+\S+", re.IGNORECASE),
    re.compile(
        r"(api[_-]?key|token|secret|password)=([^\s&;]+)", re.IGNORECASE
    ),
)


def _redact_credentials(text: str) -> str:
    redacted = _CREDENTIAL_PATTERNS[0].sub("Bearer [redacted]", text)
    redacted = _CREDENTIAL_PATTERNS[1].sub("Basic [redacted]", redacted)
    redacted = _CREDENTIAL_PATTERNS[2].sub(
        lambda match: f"{match.group(1)}=[redacted]", redacted
    )
    return redacted


def write_restricted_diagnostic(
    settings: Settings,
    *,
    job: Mapping[str, Any] | None,
    module_run: Mapping[str, Any] | None,
    exc: BaseException,
    worker_id: str,
) -> str | None:
    """Persist full failure diagnostics to the restricted service log.

    Public API surfaces, normal logs and reports only carry the sanitized
    message from ``public_exception_message``.  The real exception type,
    message and traceback must still be recoverable for audit and debugging,
    so they are appended to an environment-isolated JSONL file under the
    artifact root with owner-only permissions.  The file never leaves the
    service host and is never referenced from frontend/report payloads.
    """

    try:
        diagnostics_dir = settings.artifact_root / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(diagnostics_dir, 0o700)
        path = diagnostics_dir / f"worker-errors-{settings.environment}.jsonl"
        try:
            if path.exists() and path.stat().st_size > _DIAGNOSTIC_FILE_MAX_BYTES:
                rotated = path.with_suffix(".jsonl.1")
                if rotated.exists():
                    rotated.unlink()
                path.rename(rotated)
        except OSError:
            pass
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "environment": settings.environment,
            "worker_id": worker_id,
            "job_id": str(job.get("id")) if isinstance(job, Mapping) and job.get("id") else None,
            "module_run_id": (
                str(module_run.get("id"))
                if isinstance(module_run, Mapping) and module_run.get("id")
                else None
            ),
            "investigation_id": (
                str(module_run.get("investigation_id"))
                if isinstance(module_run, Mapping) and module_run.get("investigation_id")
                else None
            ),
            "module_code": (
                str(module_run.get("module_code"))
                if isinstance(module_run, Mapping)
                else None
            ),
            "exception_type": exc.__class__.__name__,
            "exception_message": _redact_credentials(str(exc))[
                :_DIAGNOSTIC_MESSAGE_LIMIT
            ],
            "traceback": _redact_credentials(
                "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
            )[:_DIAGNOSTIC_TRACEBACK_LIMIT],
        }
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.chmod(path, 0o600)
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return str(path)
    except Exception as log_exc:  # diagnostics must never break job handling
        logger.warning(
            "could not write restricted diagnostic (%s)",
            log_exc.__class__.__name__,
        )
        return None


class WorkerOutcome(StrEnum):
    IDLE = "idle"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class WorkerResult:
    outcome: WorkerOutcome
    job_id: str | None = None
    module_run_id: str | None = None
    error_code: str | None = None


class JobExecutionError(RuntimeError):
    """A workflow failure with an explicitly classified retry policy."""

    def __init__(
        self,
        error_code: str,
        public_message: str,
        *,
        retryable: bool,
        retry_delay_seconds: int = 0,
    ) -> None:
        super().__init__(public_message)
        self.error_code = error_code
        self.public_message = public_message
        self.retryable = retryable
        self.retry_delay_seconds = retry_delay_seconds


class RetryableJobError(JobExecutionError):
    def __init__(
        self,
        error_code: str,
        public_message: str,
        *,
        retry_delay_seconds: int = 5,
    ) -> None:
        super().__init__(
            error_code,
            public_message,
            retryable=True,
            retry_delay_seconds=retry_delay_seconds,
        )


class PermanentJobError(JobExecutionError):
    def __init__(self, error_code: str, public_message: str) -> None:
        super().__init__(error_code, public_message, retryable=False)


class JobCancellationRequested(RuntimeError):
    """Raised only after the persistent job row records cancellation."""


@dataclass(slots=True)
class JobContext:
    job: Mapping[str, Any]
    module_run: Mapping[str, Any]
    repository: Any
    settings: Settings
    worker_id: str
    _heartbeat: Callable[[], Mapping[str, Any]] = field(repr=False)
    stop_requested: Callable[[], bool] = field(default=lambda: False, repr=False)

    def heartbeat(self) -> Mapping[str, Any]:
        """Extend the current lease from an explicit workflow checkpoint."""

        return self._heartbeat()


JobOutput = Mapping[str, Any]


@runtime_checkable
class JobHandler(Protocol):
    def __call__(
        self, context: JobContext
    ) -> JobOutput | Awaitable[JobOutput]: ...


class RepositoryForWorker(Protocol):
    def claim_job(self, *, worker_id: str, lease_seconds: int) -> Mapping[str, Any] | None: ...

    def get_module_run_by_job(self, job_id: Any) -> Mapping[str, Any] | None: ...

    def heartbeat_job(
        self,
        *,
        job_id: Any,
        worker_id: str,
        lease_token: Any,
        lease_seconds: int,
    ) -> Mapping[str, Any]: ...

    def complete_job(
        self,
        *,
        job_id: Any,
        worker_id: str,
        lease_token: Any,
        output_snapshot: Mapping[str, Any],
        actor: str | None = None,
    ) -> Mapping[str, Any]: ...

    def fail_job(
        self,
        *,
        job_id: Any,
        worker_id: str,
        lease_token: Any,
        error_code: str,
        error_message: str,
        retryable: bool,
        retry_delay_seconds: int = 0,
        actor: str | None = None,
    ) -> Mapping[str, Any]: ...

    def cancel_leased_job(
        self,
        *,
        job_id: Any,
        worker_id: str,
        lease_token: Any,
        actor: str | None = None,
    ) -> Mapping[str, Any]: ...

    def release_job_lease_for_shutdown(
        self,
        *,
        job_id: Any,
        worker_id: str,
        lease_token: Any,
        actor: str | None = None,
    ) -> Mapping[str, Any]: ...


class _LeaseHeartbeat:
    def __init__(
        self,
        *,
        repository: RepositoryForWorker,
        job: Mapping[str, Any],
        worker_id: str,
        lease_seconds: int,
        interval_seconds: float,
    ) -> None:
        self.repository = repository
        self.job = job
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._cancel_requested = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def heartbeat(self) -> Mapping[str, Any]:
        if self._lost.is_set():
            raise LeaseLostError("job lease 已由关闭流程释放")
        try:
            row = self.repository.heartbeat_job(
                job_id=self.job["id"],
                worker_id=self.worker_id,
                lease_token=self.job["lease_token"],
                lease_seconds=self.lease_seconds,
            )
            if row.get("cancel_requested_at") is not None:
                self._cancel_requested.set()
                raise JobCancellationRequested("job cancellation requested")
            return row
        except LeaseLostError:
            self._lost.set()
            raise

    def start(self) -> None:
        if self.interval_seconds <= 0:
            return
        self._thread = threading.Thread(
            target=self._run,
            name=f"invalidity-heartbeat-{str(self.job['id'])[:8]}",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.heartbeat()
            except JobCancellationRequested:
                return
            except LeaseLostError:
                return
            except Exception as exc:  # completion still verifies ownership atomically.
                logger.warning(
                    "job heartbeat failed (%s); lease ownership will be rechecked",
                    exc.__class__.__name__,
                )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(max(self.interval_seconds, 0.1), 2.0))

    def abandon(self) -> None:
        """Stop heartbeats and make every later checkpoint lose its CAS."""

        self._lost.set()
        self.stop()


class DurableWorker:
    """Consume PostgreSQL jobs with leases; never infer success from HTTP return.

    Expired leases are recovered by ``Repository.claim_job``.  This class owns
    only execution and lease semantics.  Workflow state, document qualification,
    and claim transitions remain the responsibility of the registered handler.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        repository: RepositoryForWorker,
        handlers: Mapping[str, JobHandler],
        worker_id: str | None = None,
        heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.handlers = dict(handlers)
        self.worker_id = worker_id or (
            f"{settings.environment}-{socket.gethostname()}-{uuid.uuid4().hex[:10]}"
        )
        default_interval = min(float(settings.job_lease_seconds) / 3.0, 30.0)
        self.heartbeat_interval_seconds = (
            max(0.1, default_interval)
            if heartbeat_interval_seconds is None
            else max(0.0, float(heartbeat_interval_seconds))
        )
        self._service_stop: threading.Event | None = None
        self._active_lock = threading.Lock()
        self._active_job: dict[str, Any] | None = None
        self._active_heartbeat: _LeaseHeartbeat | None = None
        self._active_released = False

    @staticmethod
    def _resolve_output(result: JobOutput | Awaitable[JobOutput]) -> JobOutput:
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        if not isinstance(result, Mapping):
            raise PermanentJobError(
                "INVALID_HANDLER_OUTPUT",
                "模块输出不是对象，未标记为成功",
            )
        serialized = json_safe(dict(result))
        if not isinstance(serialized, Mapping):  # defensive; json_safe preserves dict.
            raise PermanentJobError(
                "INVALID_HANDLER_OUTPUT",
                "模块输出无法序列化，未标记为成功",
            )
        output_status = str(serialized.get("status", "")).strip().lower()
        if output_status in {"error", "failed", "failure"} or serialized.get(
            "ok"
        ) is False:
            raise PermanentJobError(
                "HANDLER_REPORTED_FAILURE",
                "模块报告执行失败，未标记为成功",
            )
        return dict(serialized)

    @staticmethod
    def _safe_job_error(exc: BaseException) -> JobExecutionError:
        if isinstance(exc, JobExecutionError):
            safe = public_json({"message": exc.public_message})["message"]
            return JobExecutionError(
                exc.error_code,
                str(safe)[:500],
                retryable=exc.retryable,
                retry_delay_seconds=exc.retry_delay_seconds,
            )
        if exc.__class__.__name__ in {
            "ConfigurationError",
            "RepositoryConfigurationError",
            "SourceSnapshotError",
            "WorkflowInputError",
        }:
            return JobExecutionError(
                "WORKFLOW_INPUT_ERROR",
                "调查输入快照不符合工作流契约",
                retryable=False,
            )
        return JobExecutionError(
            "UNEXPECTED_HANDLER_ERROR",
            public_exception_message(exc),
            retryable=True,
            retry_delay_seconds=5,
        )

    def _mark_i0_terminal_failure(
        self,
        module_run: Mapping[str, Any],
        *,
        error: JobExecutionError,
        fail_result: Mapping[str, Any],
    ) -> None:
        if fail_result.get("will_retry") or fail_result.get("i0_finalized") or not str(
            module_run.get("module_code", "")
        ).startswith("I0"):
            return
        update = getattr(self.repository, "update_investigation", None)
        if not callable(update):
            return
        try:
            update(
                module_run["investigation_id"],
                status="failed",
                error_message=error.public_message,
                error_metadata={"code": error.error_code, "retryable": False},
                actor=self.worker_id,
                event_type="investigation.failed",
            )
        except Exception as exc:
            logger.warning(
                "could not persist investigation failure (%s)",
                exc.__class__.__name__,
            )

    def _fail(
        self,
        *,
        job: Mapping[str, Any],
        module_run: Mapping[str, Any],
        error: JobExecutionError,
        original_error: BaseException | None = None,
    ) -> WorkerResult:
        if original_error is not None:
            write_restricted_diagnostic(
                self.settings,
                job=job,
                module_run=module_run,
                exc=original_error,
                worker_id=self.worker_id,
            )
        try:
            fail_result = self.repository.fail_job(
                job_id=job["id"],
                worker_id=self.worker_id,
                lease_token=job["lease_token"],
                error_code=error.error_code,
                error_message=error.public_message,
                retryable=error.retryable,
                retry_delay_seconds=error.retry_delay_seconds,
                actor=self.worker_id,
            )
        except LeaseLostError:
            return WorkerResult(
                WorkerOutcome.LEASE_LOST,
                str(job["id"]),
                str(job["module_run_id"]),
                error.error_code,
            )
        if fail_result.get("cancelled"):
            return WorkerResult(
                WorkerOutcome.CANCELLED,
                str(job["id"]),
                str(job["module_run_id"]),
                "CANCELLED_BY_USER",
            )
        self._mark_i0_terminal_failure(
            module_run,
            error=error,
            fail_result=fail_result,
        )
        return WorkerResult(
            WorkerOutcome.RETRY_SCHEDULED
            if fail_result.get("will_retry")
            else WorkerOutcome.FAILED,
            str(job["id"]),
            str(job["module_run_id"]),
            error.error_code,
        )

    def _cancel(
        self,
        *,
        job: Mapping[str, Any],
    ) -> WorkerResult:
        try:
            self.repository.cancel_leased_job(
                job_id=job["id"],
                worker_id=self.worker_id,
                lease_token=job["lease_token"],
                actor=self.worker_id,
            )
        except LeaseLostError:
            return WorkerResult(
                WorkerOutcome.LEASE_LOST,
                str(job["id"]),
                str(job["module_run_id"]),
                "LEASE_LOST",
            )
        return WorkerResult(
            WorkerOutcome.CANCELLED,
            str(job["id"]),
            str(job["module_run_id"]),
            "CANCELLED_BY_USER",
        )

    def _activate_job(self, job: Mapping[str, Any]) -> None:
        with self._active_lock:
            self._active_job = dict(job)
            self._active_heartbeat = None
            self._active_released = False

    def _attach_active_heartbeat(
        self,
        job: Mapping[str, Any],
        heartbeat: _LeaseHeartbeat,
    ) -> bool:
        with self._active_lock:
            if (
                self._active_job is None
                or self._active_job.get("id") != job.get("id")
                or self._active_released
            ):
                return False
            self._active_heartbeat = heartbeat
            return True

    def _clear_active_job(self, job: Mapping[str, Any]) -> None:
        with self._active_lock:
            if (
                self._active_job is not None
                and self._active_job.get("id") == job.get("id")
            ):
                self._active_job = None
                self._active_heartbeat = None
                self._active_released = False

    def release_active_lease_for_shutdown(self) -> Mapping[str, Any]:
        """Immediately make the current durable job reclaimable on restart."""

        releaser = getattr(
            self.repository, "release_job_lease_for_shutdown", None
        )
        if not callable(releaser):
            return {"released": False, "reason": "repository_not_supported"}
        with self._active_lock:
            if self._active_job is None:
                return {"released": False, "reason": "no_active_job"}
            job = dict(self._active_job)
            heartbeat = self._active_heartbeat
            try:
                result = releaser(
                    job_id=job["id"],
                    worker_id=self.worker_id,
                    lease_token=job["lease_token"],
                    actor=self.worker_id,
                )
            except Exception as exc:
                logger.warning(
                    "could not release active job lease during shutdown (%s)",
                    exc.__class__.__name__,
                )
                return {"released": False, "reason": "release_failed"}
            released = bool(
                result.get("released") or result.get("cancelled")
            )
            self._active_released = released
        if released and heartbeat is not None:
            heartbeat.abandon()
        return result

    def run_once(self) -> WorkerResult:
        job = self.repository.claim_job(
            worker_id=self.worker_id,
            lease_seconds=self.settings.job_lease_seconds,
        )
        if job is None:
            return WorkerResult(WorkerOutcome.IDLE)

        self._activate_job(job)
        try:
            return self._run_claimed_job(job)
        finally:
            self._clear_active_job(job)

    def _run_claimed_job(self, job: Mapping[str, Any]) -> WorkerResult:

        module_run = self.repository.get_module_run_by_job(job["id"])
        if module_run is None:
            return self._fail(
                job=job,
                module_run={
                    "id": job["module_run_id"],
                    "module_code": "repository",
                },
                error=PermanentJobError(
                    "MODULE_RUN_NOT_FOUND",
                    "持久任务缺少对应模块运行记录",
                ),
            )

        module_code = str(module_run.get("module_code", ""))
        handler = self.handlers.get(module_code)
        if handler is None:
            return self._fail(
                job=job,
                module_run=module_run,
                error=PermanentJobError(
                    "HANDLER_NOT_REGISTERED",
                    f"模块 {module_code or '[unknown]'} 没有已注册处理器",
                ),
            )

        heartbeat = _LeaseHeartbeat(
            repository=self.repository,
            job=job,
            worker_id=self.worker_id,
            lease_seconds=self.settings.job_lease_seconds,
            interval_seconds=self.heartbeat_interval_seconds,
        )
        if not self._attach_active_heartbeat(job, heartbeat):
            heartbeat.abandon()
            return WorkerResult(
                WorkerOutcome.LEASE_LOST,
                str(job["id"]),
                str(job["module_run_id"]),
                "WORKER_SHUTDOWN_REQUEUED",
            )
        def stop_requested() -> bool:
            if self._service_stop is not None and self._service_stop.is_set():
                return True
            if heartbeat.cancel_requested:
                return True
            try:
                heartbeat.heartbeat()
            except JobCancellationRequested:
                return True
            except LeaseLostError:
                return True
            return heartbeat.cancel_requested

        context = JobContext(
            job=job,
            module_run=module_run,
            repository=self.repository,
            settings=self.settings,
            worker_id=self.worker_id,
            _heartbeat=heartbeat.heartbeat,
            stop_requested=stop_requested,
        )
        heartbeat.start()
        try:
            output = self._resolve_output(handler(context))
        except Exception as exc:
            heartbeat.stop()
            if heartbeat.cancel_requested or isinstance(
                exc, JobCancellationRequested
            ):
                return self._cancel(job=job)
            if heartbeat.lease_lost or isinstance(exc, LeaseLostError):
                return WorkerResult(
                    WorkerOutcome.LEASE_LOST,
                    str(job["id"]),
                    str(job["module_run_id"]),
                    "LEASE_LOST",
                )
            return self._fail(
                job=job,
                module_run=module_run,
                error=self._safe_job_error(exc),
                original_error=exc,
            )
        heartbeat.stop()
        if heartbeat.cancel_requested:
            return self._cancel(job=job)
        if heartbeat.lease_lost:
            return WorkerResult(
                WorkerOutcome.LEASE_LOST,
                str(job["id"]),
                str(job["module_run_id"]),
                "LEASE_LOST",
            )
        try:
            completion = self.repository.complete_job(
                job_id=job["id"],
                worker_id=self.worker_id,
                lease_token=job["lease_token"],
                output_snapshot=output,
                actor=self.worker_id,
            )
        except LeaseLostError:
            return WorkerResult(
                WorkerOutcome.LEASE_LOST,
                str(job["id"]),
                str(job["module_run_id"]),
                "LEASE_LOST",
            )
        if completion.get("cancelled"):
            return WorkerResult(
                WorkerOutcome.CANCELLED,
                str(job["id"]),
                str(job["module_run_id"]),
                "CANCELLED_BY_USER",
            )
        return WorkerResult(
            WorkerOutcome.SUCCEEDED,
            str(job["id"]),
            str(job["module_run_id"]),
        )

    def serve(self, stop_event: threading.Event) -> None:
        self._service_stop = stop_event
        while not stop_event.is_set():
            try:
                result = self.run_once()
            except Exception as exc:
                logger.warning(
                    "worker poll failed (%s); retrying",
                    exc.__class__.__name__,
                )
                result = WorkerResult(WorkerOutcome.IDLE)
            if result.outcome == WorkerOutcome.IDLE:
                stop_event.wait(self.settings.worker_poll_seconds)


__all__ = [
    "DurableWorker",
    "JobContext",
    "JobExecutionError",
    "JobCancellationRequested",
    "JobHandler",
    "PermanentJobError",
    "RetryableJobError",
    "WorkerOutcome",
    "WorkerResult",
]
