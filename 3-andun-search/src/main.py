from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException

from andun_search.config import bootstrap_local_env, get_settings
from andun_search.db import ensure_tables, list_resumable_runs
from andun_search.models import AndunSearchInput, AndunSearchOutput, AsyncRunAccepted
from andun_search.service import AndunSearchService, PreparedRun


bootstrap_local_env()
app = FastAPI(title="Patent Andun Search Lab", version="0.1.0")
logger = logging.getLogger("andun_search")
running_tasks: dict[int, asyncio.Task[AndunSearchOutput]] = {}


def _register_task(run_id: int, task: asyncio.Task[AndunSearchOutput]) -> None:
    running_tasks[run_id] = task

    def cleanup(_: asyncio.Task[AndunSearchOutput]) -> None:
        running_tasks.pop(run_id, None)

    task.add_done_callback(cleanup)


def _elapsed_offset_ms(detail: dict[str, Any]) -> int:
    persisted = max(0, int(detail.get("total_elapsed_ms") or 0))
    started_at = detail.get("started_at")
    if not isinstance(started_at, dt.datetime):
        return persisted
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=dt.timezone.utc)
    wall_elapsed = round((dt.datetime.now(dt.timezone.utc) - started_at).total_seconds() * 1000)
    return max(persisted, wall_elapsed)


def _resume_detail(detail: dict[str, Any]) -> AsyncRunAccepted:
    run_id = int(detail["id"])
    if run_id in running_tasks:
        return AsyncRunAccepted(
            andun_search_run_id=run_id,
            product_dataset_id=str(detail.get("product_dataset_id") or ""),
            status=str(detail.get("status") or "collecting"),
        )
    task_id = str(detail.get("andun_task_id") or "").strip()
    if not task_id:
        raise ValueError("该运行没有安盾 taskId，无法安全续跑（创建请求不可盲目重试）")
    raw_payload = detail.get("raw_payload") if isinstance(detail.get("raw_payload"), dict) else {}
    input_payload = raw_payload.get("input") if isinstance(raw_payload.get("input"), dict) else {}
    payload = AndunSearchInput.model_validate(input_payload)
    prepared = PreparedRun(
        run_id=run_id,
        product_dataset_id=str(detail.get("product_dataset_id") or ""),
        keywords=[str(value) for value in (detail.get("keywords") or [])],
        platforms=[str(value) for value in (detail.get("platforms") or [])],
    )
    service = AndunSearchService(get_settings())
    task = asyncio.create_task(
        service.execute(
            payload,
            prepared,
            existing_task_id=task_id,
            elapsed_offset_ms=_elapsed_offset_ms(detail),
            existing_remote_status=detail.get("remote_status"),
        )
    )
    _register_task(run_id, task)
    return AsyncRunAccepted(
        andun_search_run_id=run_id,
        product_dataset_id=prepared.product_dataset_id,
        status="collecting",
    )


@app.on_event("startup")
async def startup() -> None:
    ensure_tables()
    for detail in list_resumable_runs():
        try:
            accepted = _resume_detail(detail)
            logger.info("自动续跑安盾任务 run_id=%s", accepted.andun_search_run_id)
        except Exception:
            logger.exception("自动续跑安盾任务失败 run_id=%s", detail.get("id"))


@app.get("/health")
def health() -> dict[str, Any]:
    settings = get_settings()
    return {
        "status": "ok",
        "module": "3-andun-search",
        "api_env": settings.api_env,
        "api_base_url": settings.api_base_url,
        "credentials_configured": settings.credentials_configured,
        "request_timeout_seconds": settings.request_timeout_seconds,
        "request_retry_attempts": settings.request_retry_attempts,
        "poll_interval_seconds": settings.poll_interval_seconds,
        "max_wait_seconds": settings.max_wait_seconds,
        "active_local_tasks": len(running_tasks),
    }


@app.post("/run", response_model=AndunSearchOutput)
async def run_search(payload: AndunSearchInput) -> AndunSearchOutput:
    service = AndunSearchService(get_settings())
    try:
        prepared = service.prepare(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return await service.execute(payload, prepared)


@app.post("/async_run", response_model=AsyncRunAccepted)
async def async_run_search(payload: AndunSearchInput) -> AsyncRunAccepted:
    service = AndunSearchService(get_settings())
    try:
        prepared = service.prepare(payload)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    task = asyncio.create_task(service.execute(payload, prepared))
    _register_task(prepared.run_id, task)
    return AsyncRunAccepted(
        andun_search_run_id=prepared.run_id,
        product_dataset_id=prepared.product_dataset_id,
    )


@app.get("/runs/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    detail = AndunSearchService(get_settings()).get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    return detail


@app.post("/runs/{run_id}/resume", response_model=AsyncRunAccepted)
async def resume_run(run_id: int) -> AsyncRunAccepted:
    service = AndunSearchService(get_settings())
    detail = service.get_run(run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
    status = str(detail.get("status") or "")
    if status == "completed":
        raise HTTPException(status_code=409, detail="该运行已经完成，无需续跑")
    if status not in {"submitting", "collecting", "organizing", "error", "timeout"}:
        raise HTTPException(status_code=409, detail=f"当前状态不允许续跑: {status}")
    try:
        return _resume_detail(detail)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-m", "--mode", default="http", choices=["http"])
    parser.add_argument("-p", "--port", default=5108, type=int)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
