#!/usr/bin/env python3
"""Generate blind module-3 profiles and module-4 I2 plans from target PDFs only.

This phase deliberately discovers only ``target_document/*.pdf``.  It never
opens a decision, a source-document manifest, or a retrieval benchmark.  The
result can therefore be frozen before any known prior-art identifier is read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
import httpx

from invalidity.config import Settings
from invalidity.module1 import normalize_claims
from invalidity.parser_service import parse_source


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "tests/fixtures/blind_benchmarks"
_FIXTURE_MARKERS = {"fixture", "fixture_id", "fixture_output", "simulated"}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date_text(value: Any) -> str:
    raw = str(value or "").strip()
    return raw[:10] if len(raw) >= 10 else raw


def _settings(env_file: Path) -> Settings:
    values = {
        str(key): str(value)
        for key, value in dotenv_values(env_file).items()
        if value is not None
    }
    # Allow an operator to override a test value explicitly without importing
    # unrelated process variables into the isolated Settings validator.
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return Settings.from_environment(values, load_dotenv_files=False)


def _request(
    client: httpx.Client,
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = client.request(method, url, json=payload)
    if response.is_error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:1000]
        raise RuntimeError(f"{url}: HTTP {response.status_code}: {detail}")
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"{url}: 响应不是对象")
    return value


def _wait_run(
    client: httpx.Client,
    base_url: str,
    run_id: str,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        value = _request(client, "GET", f"{base_url}/v1/lab/module-runs/{run_id}")
        run = value.get("module_run") if isinstance(value.get("module_run"), dict) else value
        status = str(run.get("status") or "").lower()
        if status in {"succeeded", "partial"}:
            return value
        if status in {"failed", "cancelled"}:
            job = value.get("job") if isinstance(value.get("job"), dict) else {}
            reason = run.get("error_message") or job.get("last_error") or status
            raise RuntimeError(f"I2运行 {run_id} 失败: {reason}")
        time.sleep(0.75)
    try:
        _request(
            client,
            "POST",
            f"{base_url}/v1/lab/module-runs/{run_id}/cancel",
            {
                "reason": (
                    "blind benchmark client timeout after "
                    f"{timeout_seconds} seconds"
                )
            },
        )
    except (RuntimeError, httpx.HTTPError):
        pass
    raise TimeoutError(
        f"I2运行 {run_id} 超过{timeout_seconds}秒，已请求取消"
    )


def _module_output(value: dict[str, Any]) -> dict[str, Any]:
    run = value.get("module_run") if isinstance(value.get("module_run"), dict) else value
    snapshot = run.get("output_snapshot") if isinstance(run.get("output_snapshot"), dict) else {}
    output = snapshot.get("output")
    if not isinstance(output, dict):
        raise RuntimeError("I2成功运行缺少output_snapshot.output")
    return output


def _strip_fixture_markers(value: Any) -> Any:
    """Remove run-envelope fixture markers before a manual downstream run.

    Module outputs deliberately expose ``simulated`` and related audit fields,
    while the manual lab input guard rejects those keys anywhere in caller-
    supplied input.  The profile facts themselves remain unchanged; only the
    four mode-control markers owned by the lab runtime are omitted.
    """

    if isinstance(value, dict):
        return {
            key: _strip_fixture_markers(child)
            for key, child in value.items()
            if str(key).strip().lower() not in _FIXTURE_MARKERS
        }
    if isinstance(value, list):
        return [_strip_fixture_markers(child) for child in value]
    return value


def _cases(fixtures: Path, selected: set[str]) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for directory in sorted(fixtures.iterdir()):
        if not directory.is_dir() or (selected and directory.name not in selected):
            continue
        targets = sorted((directory / "target_document").glob("*.pdf"))
        if len(targets) == 1:
            result.append((directory.name, targets[0]))
    if not result:
        raise SystemExit("没有发现符合条件的 target_document/*.pdf")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.test.local")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--claim",
        action="append",
        default=[],
        help="只生成指定独立权利要求；可重复传入，默认生成全部独立权利要求",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="每项独立权利要求遇到模型/传输失败时创建新运行的最大次数",
    )
    parser.add_argument(
        "--run-tag",
        default="default",
        help="显式开启新一组幂等重试；仅允许字母、数字、点、下划线和连字符",
    )
    parser.add_argument(
        "--run-timeout-seconds",
        type=int,
        default=480,
        help="单个模块运行的客户端等待上限；必须覆盖后端模型总预算",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="仅复用输出目录中目标哈希、claim 和盲测标记均匹配的已完成计划",
    )
    args = parser.parse_args()
    if not 1 <= args.max_attempts <= 10:
        raise SystemExit("--max-attempts 必须在 1..10 范围内")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,48}", args.run_tag):
        raise SystemExit("--run-tag 格式无效")
    if not 180 <= args.run_timeout_seconds <= 900:
        raise SystemExit("--run-timeout-seconds 必须在 180..900 范围内")

    settings = _settings(args.env_file.resolve())
    generated_at = datetime.now(timezone.utc).isoformat()
    summaries: list[dict[str, Any]] = []
    base_url = settings.api_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.api_token}"}
    client = httpx.Client(headers=headers, timeout=120)
    selected_claims = {str(item).strip() for item in args.claim if str(item).strip()}
    for case_id, target_path in _cases(args.fixtures.resolve(), set(args.case)):
        parsed = parse_source(str(target_path))
        metadata = dict(parsed.get("metadata") or {})
        claims = normalize_claims(parsed.get("claims") or [])
        independent = [claim for claim in claims if claim.claim_type == "INDEPENDENT"]
        if selected_claims:
            independent = [
                claim for claim in independent if claim.claim_id in selected_claims
            ]
        if not independent:
            raise RuntimeError(
                f"{case_id}: 没有匹配 --claim 的独立权利要求"
            )
        images = [
            str(item.get("file_path") or "").strip()
            for item in parsed.get("figures") or []
            if isinstance(item, dict) and str(item.get("file_path") or "").strip()
        ][:2]
        if not images:
            raise RuntimeError(f"{case_id}: 目标专利没有解析出可用附图")
        critical_date = _date_text(
            metadata.get("priority_date") or metadata.get("application_date")
        )
        if not critical_date:
            raise RuntimeError(f"{case_id}: 无法确定关键日")
        target_sha256 = _sha256(target_path)
        patent_context = {
            "patent_number": metadata.get("patent_number"),
            "title": metadata.get("title"),
            "abstract": metadata.get("abstract"),
            "bibliographic_data": metadata,
            "claims": [claim.model_dump(mode="json") for claim in claims],
            "claims_section_text": parsed.get("claims_section_text"),
            "specification": parsed.get("specification") or {},
            "figures": parsed.get("figures") or [],
            "page_texts": parsed.get("page_texts") or [],
            "raw_text": parsed.get("raw_text") or "",
            "source_sha256": parsed.get("source_sha256"),
        }
        case_summary = {
            "case_id": case_id,
            "target_file": str(target_path.resolve()),
            "target_sha256": target_sha256,
            "critical_date": critical_date,
            "independent_claim_ids": [claim.claim_id for claim in independent],
            "plans": [],
        }
        for claim in independent:
            output_path = args.output_dir / case_id / f"claim-{claim.claim_id}.json"
            if args.resume and output_path.is_file():
                existing = json.loads(output_path.read_text(encoding="utf-8"))
                if not isinstance(existing, dict) or (
                    existing.get("oracle_loaded") is not False
                    or str(existing.get("case_id") or "") != case_id
                    or str(existing.get("claim_id") or "") != claim.claim_id
                    or str(existing.get("target_sha256") or "") != target_sha256
                    or not isinstance(existing.get("plan"), dict)
                ):
                    raise RuntimeError(
                        f"{output_path}: --resume 发现不匹配或不完整的既有输出"
                    )
                case_summary["plans"].append(str(output_path.resolve()))
                print(
                    f"[{case_id} claim {claim.claim_id}] resume existing plan: "
                    f"{output_path}",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            failures: list[str] = []
            profile_created: dict[str, Any] | None = None
            profile_completed: dict[str, Any] | None = None
            for attempt in range(1, args.max_attempts + 1):
                profile_created = _request(
                    client,
                    "POST",
                    f"{base_url}/v1/lab/module-runs",
                    {
                        "contract_version": "v1",
                        "module_code": "I2_INVENTIVE_PROFILE",
                        "input_mode": "manual",
                        "input": {
                            "claim_id": claim.claim_id,
                            "expanded_claim_text": claim.expanded_claim_text or claim.claim_text,
                            "target_images": images,
                            "patent_context": patent_context,
                        },
                        "idempotency_key": (
                            f"blind-i2-profile-v8-{case_id}-{target_sha256[:16]}-"
                            f"{claim.claim_id}-{args.run_tag}-attempt-{attempt}"
                        ),
                    },
                )
                print(
                    f"[{case_id} claim {claim.claim_id}] module3 profile attempt "
                    f"{attempt}/{args.max_attempts}: {profile_created['module_run_id']}",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    profile_completed = _wait_run(
                        client,
                        base_url,
                        str(profile_created["module_run_id"]),
                        timeout_seconds=args.run_timeout_seconds,
                    )
                    break
                except (RuntimeError, TimeoutError) as exc:
                    failures.append(str(exc))
                    print(
                        f"[{case_id} claim {claim.claim_id}] attempt {attempt} failed: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    if attempt == args.max_attempts:
                        raise RuntimeError(
                            f"{case_id} 权利要求 {claim.claim_id} 的模块3连续"
                            f"{args.max_attempts}次未生成成功: {' | '.join(failures)}"
                        ) from exc
                    time.sleep(min(5, attempt))
            if profile_created is None or profile_completed is None:
                raise RuntimeError(
                    f"{case_id} 权利要求 {claim.claim_id} 未创建模块3运行"
                )
            profile = _strip_fixture_markers(_module_output(profile_completed))

            query_failures: list[str] = []
            query_created: dict[str, Any] | None = None
            query_completed: dict[str, Any] | None = None
            for attempt in range(1, args.max_attempts + 1):
                query_created = _request(
                    client,
                    "POST",
                    f"{base_url}/v1/lab/module-runs",
                    {
                        "contract_version": "v1",
                        "module_code": "I2_QUERY_PLAN",
                        "input_mode": "manual",
                        "input": {
                            "claim_id": claim.claim_id,
                            "inventive_profile_plan": profile,
                            "source_plan_run_id": str(
                                profile_created["module_run_id"]
                            ),
                            "source_sha256": target_sha256,
                            "max_queries": 5,
                        },
                        "idempotency_key": (
                            f"blind-i2-query-v8-{case_id}-{target_sha256[:16]}-"
                            f"{claim.claim_id}-{args.run_tag}-attempt-{attempt}"
                        ),
                    },
                )
                print(
                    f"[{case_id} claim {claim.claim_id}] module4 query attempt "
                    f"{attempt}/{args.max_attempts}: {query_created['module_run_id']}",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    query_completed = _wait_run(
                        client,
                        base_url,
                        str(query_created["module_run_id"]),
                        timeout_seconds=args.run_timeout_seconds,
                    )
                    break
                except (RuntimeError, TimeoutError) as exc:
                    query_failures.append(str(exc))
                    print(
                        f"[{case_id} claim {claim.claim_id}] module4 attempt "
                        f"{attempt} failed: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    if attempt == args.max_attempts:
                        raise RuntimeError(
                            f"{case_id} 权利要求 {claim.claim_id} 的模块4连续"
                            f"{args.max_attempts}次未生成成功: "
                            f"{' | '.join(query_failures)}"
                        ) from exc
                    time.sleep(min(5, attempt))
            if query_created is None or query_completed is None:
                raise RuntimeError(
                    f"{case_id} 权利要求 {claim.claim_id} 未创建模块4运行"
                )
            plan = _module_output(query_completed)
            envelope = {
                "benchmark_phase": "blind_module3_profile_module4_query_generation",
                "oracle_loaded": False,
                "generated_at": generated_at,
                "case_id": case_id,
                "critical_date": critical_date,
                "target_file": str(target_path.resolve()),
                "target_sha256": case_summary["target_sha256"],
                "claim_id": claim.claim_id,
                "source_profile_run_id": str(profile_created["module_run_id"]),
                "module_run_id": str(query_created["module_run_id"]),
                "profile_attempt_count": len(failures) + 1,
                "profile_prior_attempt_errors": failures,
                "attempt_count": len(query_failures) + 1,
                "prior_attempt_errors": query_failures,
                "run_tag": args.run_tag,
                "plan": plan,
            }
            _write_json(output_path, envelope)
            case_summary["plans"].append(str(output_path.resolve()))
        summaries.append(case_summary)
    _write_json(
        args.output_dir / "generation-index.json",
        {
            "benchmark_phase": "blind_module3_profile_module4_query_generation",
            "oracle_loaded": False,
            "generated_at": generated_at,
            "cases": summaries,
        },
    )
    print(json.dumps({"output_dir": str(args.output_dir), "cases": summaries}, ensure_ascii=False))


if __name__ == "__main__":
    main()
