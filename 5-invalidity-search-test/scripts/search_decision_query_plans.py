#!/usr/bin/env python3
"""Execute frozen compiled queries against P002 and freeze each actual top 10."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from invalidity.config import Settings
from invalidity.patsnap import PatsnapApiError, PatsnapProvider


ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _settings(path: Path) -> Settings:
    values = {str(k): str(v) for k, v in dotenv_values(path).items() if v is not None}
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return Settings.from_environment(values, load_dotenv_files=False)


def _compiled_plan_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _search_key(value: dict[str, Any]) -> tuple[str, str]:
    return str(value.get("query_id") or ""), str(value.get("expression") or "")


def _checkpoint_payload(
    *,
    envelope: dict[str, Any],
    compiled_plan_sha256: str,
    searches: list[dict[str, Any]],
    completed: bool,
    failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "benchmark_phase": (
            "blind_p002_top10_frozen" if completed else "blind_p002_top10_partial"
        ),
        "oracle_loaded": False,
        "frozen_at": datetime.now(timezone.utc).isoformat() if completed else None,
        "checkpointed_at": datetime.now(timezone.utc).isoformat(),
        "case_id": str(envelope.get("case_id") or ""),
        "claim_id": envelope.get("claim_id"),
        "critical_date": envelope.get("critical_date"),
        "compiled_plan_sha256": compiled_plan_sha256,
        "completed_search_count": len(searches),
        "expected_search_count": len(envelope.get("queries") or []),
        "searches": searches,
        "search_runs": [
            {
                "query_id": item["query_id"],
                "query_role": item["query_role"],
                "query_variant": item["query_variant"],
                "documents": item["ranked_results"],
            }
            for item in searches
        ],
    }
    if failure:
        payload["last_failure"] = failure
    return payload


def _load_resumable_searches(
    destination: Path,
    *,
    compiled_plan_sha256: str,
    envelope: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    if not destination.exists():
        return [], False
    previous = json.loads(destination.read_text(encoding="utf-8"))
    if previous.get("compiled_plan_sha256") != compiled_plan_sha256:
        return [], False
    if previous.get("oracle_loaded") is not False:
        raise RuntimeError(f"{destination}: 断点文件不是盲测产物")

    allowed = {
        (
            str(item.get("queryId") or ""),
            str(item.get("expression") or ""),
        )
        for item in envelope.get("queries") or []
    }
    searches = [
        dict(item)
        for item in previous.get("searches") or []
        if isinstance(item, dict) and _search_key(item) in allowed
    ]
    completed = (
        previous.get("benchmark_phase") == "blind_p002_top10_frozen"
        and len(searches) == len(envelope.get("queries") or [])
    )
    return searches, completed


def _load_reusable_searches(
    source: Path,
    *,
    envelope: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reuse only byte-for-byte identical query identities from a frozen run."""

    if not source.exists():
        return []
    previous = json.loads(source.read_text(encoding="utf-8"))
    if (
        previous.get("oracle_loaded") is not False
        or previous.get("benchmark_phase") != "blind_p002_top10_frozen"
        or str(previous.get("case_id") or "") != str(envelope.get("case_id") or "")
        or str(previous.get("claim_id") or "") != str(envelope.get("claim_id") or "")
        or str(previous.get("critical_date") or "") != str(envelope.get("critical_date") or "")
    ):
        return []
    allowed = {
        (
            str(item.get("queryId") or ""),
            str(item.get("expression") or ""),
        )
        for item in envelope.get("queries") or []
    }
    result: list[dict[str, Any]] = []
    for item in previous.get("searches") or []:
        if not isinstance(item, dict) or _search_key(item) not in allowed:
            continue
        reused = dict(item)
        reused["reused_from_frozen_file"] = str(source.resolve())
        result.append(reused)
    return result


def _ordered_searches(
    envelope: dict[str, Any],
    values: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key = {_search_key(item): item for item in values}
    return [
        by_key[key]
        for compiled in envelope.get("queries") or []
        for key in [
            (
                str(compiled.get("queryId") or ""),
                str(compiled.get("expression") or ""),
            )
        ]
        if key in by_key
    ]


def _safe_failure(exc: PatsnapApiError, *, query_id: str, attempt: int) -> dict[str, Any]:
    return {
        "query_id": query_id,
        "attempt": attempt,
        "status_code": exc.status_code,
        "error_code": exc.error_code,
        "correlation_id": exc.correlation_id,
        "retryable": exc.retryable,
        "message": "智慧芽检索请求失败；响应正文未写入冻结文件",
    }


def _write_index(output_dir: Path) -> None:
    runs: list[dict[str, Any]] = []
    for path in sorted(output_dir.glob("*/claim-*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        runs.append(
            {
                "case_id": value.get("case_id"),
                "claim_id": value.get("claim_id"),
                "status": value.get("benchmark_phase"),
                "search_count": len(value.get("searches") or []),
                "expected_search_count": value.get("expected_search_count"),
                "file": str(path),
            }
        )
    _write(
        output_dir / "search-index.json",
        {"benchmark_phase": "blind_p002_top10_index", "oracle_loaded": False, "runs": runs},
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.test.local")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--claim", action="append", default=[])
    parser.add_argument("--query-delay-seconds", type=float, default=1.0)
    parser.add_argument("--rate-limit-delay-seconds", type=float, default=15.0)
    parser.add_argument("--rate-limit-retries", type=int, default=2)
    parser.add_argument(
        "--reuse-dir",
        type=Path,
        help="复用另一轮中 query_id 与 expression 均完全一致的已冻结结果",
    )
    args = parser.parse_args()
    if args.query_delay_seconds < 0 or args.rate_limit_delay_seconds < 0:
        raise SystemExit("请求间隔不能为负数")
    if args.rate_limit_retries < 0 or args.rate_limit_retries > 3:
        raise SystemExit("限流重试次数必须在 0 到 3 之间")
    settings = _settings(args.env_file.resolve())
    if not settings.patsnap_api_key:
        raise SystemExit("测试环境未配置智慧芽 API Key")
    provider = PatsnapProvider(
        api_key=settings.patsnap_api_key,
        base_url=settings.patsnap_base_url,
        count_path=settings.patsnap_count_path,
        search_path=settings.patsnap_search_path,
        timeout_seconds=settings.source_fetch_timeout_seconds,
    )
    selected = set(args.case)
    selected_claims = {str(value) for value in args.claim}
    files = sorted(args.input_dir.glob("*/claim-*.json"))
    summaries: list[dict[str, Any]] = []
    for source in files:
        envelope = json.loads(source.read_text(encoding="utf-8"))
        case_id = str(envelope.get("case_id") or "")
        if selected and case_id not in selected:
            continue
        if selected_claims and str(envelope.get("claim_id") or "") not in selected_claims:
            continue
        if envelope.get("oracle_loaded") is not False:
            raise RuntimeError(f"{source}: 不是盲测冻结输入")
        plan_sha256 = _compiled_plan_sha256(source)
        destination = args.output_dir / case_id / source.name
        searches, already_completed = _load_resumable_searches(
            destination,
            compiled_plan_sha256=plan_sha256,
            envelope=envelope,
        )
        if already_completed:
            summaries.append(
                {
                    "case_id": case_id,
                    "claim_id": envelope.get("claim_id"),
                    "search_count": len(searches),
                    "status": "already_frozen",
                    "file": str(destination),
                }
            )
            continue
        if args.reuse_dir is not None:
            reusable = _load_reusable_searches(
                args.reuse_dir / case_id / source.name,
                envelope=envelope,
            )
            existing_keys = {_search_key(item) for item in searches}
            searches.extend(
                item for item in reusable if _search_key(item) not in existing_keys
            )
            searches = _ordered_searches(envelope, searches)
            _write(
                destination,
                _checkpoint_payload(
                    envelope=envelope,
                    compiled_plan_sha256=plan_sha256,
                    searches=searches,
                    completed=False,
                ),
            )
        completed_keys = {_search_key(item) for item in searches}
        for index, compiled in enumerate(envelope.get("queries") or [], start=1):
            compiled_key = (
                str(compiled.get("queryId") or ""),
                str(compiled.get("expression") or ""),
            )
            if compiled_key in completed_keys:
                continue
            query_input = dict(compiled.get("input") or {})
            query = dict(query_input.get("query") or {})
            records = None
            query_failure: dict[str, Any] | None = None
            for attempt in range(1, args.rate_limit_retries + 2):
                try:
                    records = provider.search(
                        query,
                        max_results=10,
                        language=str(query_input.get("language") or "zh"),
                        server_before=str(
                            query_input.get("server_before") or envelope["critical_date"]
                        ),
                    )
                    break
                except PatsnapApiError as exc:
                    failure = _safe_failure(
                        exc,
                        query_id=str(compiled.get("queryId") or ""),
                        attempt=attempt,
                    )
                    _write(
                        destination,
                        _checkpoint_payload(
                            envelope=envelope,
                            compiled_plan_sha256=plan_sha256,
                            searches=searches,
                            completed=False,
                            failure=failure,
                        ),
                    )
                    _write_index(args.output_dir)
                    is_rate_limited = exc.error_code == 67200002 or exc.status_code == 429
                    if not is_rate_limited or attempt > args.rate_limit_retries:
                        raise
                    time.sleep(args.rate_limit_delay_seconds)
            if records is None:
                raise RuntimeError("智慧芽检索未返回结果，也未抛出可分类错误")
            artifact = provider.last_search_artifact
            raw_name = f"query-{index:02d}-{compiled.get('queryId')}.response.json"
            raw_path = args.output_dir / case_id / raw_name
            if artifact is not None:
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(artifact.content)
            searches.append(
                {
                    "query_id": compiled.get("queryId"),
                    "query_role": compiled.get("queryRole"),
                    "query_variant": compiled.get("queryVariant"),
                    "search_scope": compiled.get("searchScope"),
                    "expression": compiled.get("expression"),
                    "ranked_results": [record.model_dump() for record in records[:10]],
                    "raw_response_file": str(raw_path.resolve()) if artifact else None,
                    "raw_response_sha256": artifact.content_sha256 if artifact else None,
                    "raw_response_bytes": len(artifact.content) if artifact else None,
                    "provider_failure": query_failure,
                }
            )
            searches = _ordered_searches(envelope, searches)
            completed_keys.add(compiled_key)
            _write(
                destination,
                _checkpoint_payload(
                    envelope=envelope,
                    compiled_plan_sha256=plan_sha256,
                    searches=searches,
                    completed=False,
                ),
            )
            _write_index(args.output_dir)
            if args.query_delay_seconds:
                time.sleep(args.query_delay_seconds)
        _write(
            destination,
            _checkpoint_payload(
                envelope=envelope,
                compiled_plan_sha256=plan_sha256,
                searches=searches,
                completed=True,
            ),
        )
        summaries.append(
            {
                "case_id": case_id,
                "claim_id": envelope.get("claim_id"),
                "search_count": len(searches),
                "status": "frozen",
                "file": str(destination),
            }
        )
    _write_index(args.output_dir)
    print(json.dumps({"output_dir": str(args.output_dir), "runs": summaries}, ensure_ascii=False))


if __name__ == "__main__":
    main()
