#!/usr/bin/env python3
"""Run I4-S on source PDFs after I2 has been frozen, without any oracle input."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from invalidity.analysis import _parse_limitations
from invalidity.config import Settings
from invalidity.contracts import I4S_PROMPT_VERSION, I4S_RULE_VERSION
from invalidity.module1 import normalize_claims
from invalidity.parser_service import parse_source


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/blind_benchmarks"


class RuleVersionMismatchError(RuntimeError):
    """Stop a blind run when client and service rules are not identical."""


def _settings(path: Path) -> Settings:
    values = {str(k): str(v) for k, v in dotenv_values(path).items() if v is not None}
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return Settings.from_environment(values, load_dotenv_files=False)


def _request(client: httpx.Client, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
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


def _wait(
    client: httpx.Client,
    base_url: str,
    run_id: str,
    *,
    timeout_seconds: float = 420,
    queue_timeout_seconds: float = 900,
    poll_interval: float = 0.75,
    transient_error_grace_seconds: float = 45,
) -> dict[str, Any]:
    queue_deadline = time.monotonic() + queue_timeout_seconds
    execution_deadline: float | None = None
    first_transient_error_at: float | None = None
    last_transient_error: str | None = None
    timeout_kind = "排队"
    timeout_budget = queue_timeout_seconds
    while True:
        now = time.monotonic()
        active_deadline = execution_deadline or queue_deadline
        if now >= active_deadline:
            break
        try:
            value = _request(client, "GET", f"{base_url}/v1/lab/module-runs/{run_id}")
        except httpx.HTTPError as exc:
            now = time.monotonic()
            if first_transient_error_at is None:
                first_transient_error_at = now
                print(
                    f"I4-S {run_id} 状态轮询暂时断开，继续在原截止时间内恢复",
                    file=sys.stderr,
                    flush=True,
                )
            last_transient_error = f"{type(exc).__name__}: {exc}"
            if now - first_transient_error_at >= transient_error_grace_seconds:
                raise RuntimeError(
                    f"I4-S {run_id} 状态轮询连续断开超过"
                    f"{transient_error_grace_seconds:g}秒: {last_transient_error}"
                ) from exc
            time.sleep(poll_interval)
            continue
        if first_transient_error_at is not None:
            print(
                f"I4-S {run_id} 状态轮询已恢复",
                file=sys.stderr,
                flush=True,
            )
            first_transient_error_at = None
            last_transient_error = None
        run = value.get("module_run") if isinstance(value.get("module_run"), dict) else value
        job = value.get("job") if isinstance(value.get("job"), dict) else {}
        status = str(run.get("status") or "").lower()
        if status in {"succeeded", "partial"}:
            snapshot = run.get("output_snapshot") if isinstance(run.get("output_snapshot"), dict) else {}
            output = snapshot.get("output")
            if not isinstance(output, dict):
                raise RuntimeError(f"I4-S {run_id} 成功但缺少输出")
            return output
        if status in {"failed", "cancelled"}:
            raise RuntimeError(
                f"I4-S {run_id} 失败: {run.get('error_message') or job.get('last_error') or status}"
            )
        job_status = str(job.get("status") or "").lower()
        if execution_deadline is None and job_status in {"leased", "running"}:
            execution_deadline = time.monotonic() + timeout_seconds
            timeout_kind = "执行"
            timeout_budget = timeout_seconds
        time.sleep(poll_interval)
    try:
        _request(
            client,
            "POST",
            f"{base_url}/v1/lab/module-runs/{run_id}/cancel",
            {
                "reason": (
                    f"blind I4-S client {timeout_kind} timeout after "
                    f"{timeout_budget:g} seconds"
                )
            },
        )
    except (RuntimeError, httpx.HTTPError):
        pass
    raise TimeoutError(
        f"I4-S {run_id} {timeout_kind}超过{timeout_budget:g}秒，已请求取消"
    )


def _comparison_unusable_reason(value: Any) -> str | None:
    """Explain why a comparison cannot enter the frozen blind benchmark."""
    if not isinstance(value, dict):
        return "comparison_not_object"
    actual_version = str(value.get("analysis_rule_version") or "")
    if actual_version != I4S_RULE_VERSION:
        return (
            "rule_version_mismatch "
            f"expected={I4S_RULE_VERSION} actual={actual_version or '<missing>'}"
        )
    if str(value.get("structural_review_status") or "") == "model_error":
        return "structural_review_model_error"
    disclosures = value.get("disclosures")
    if not isinstance(disclosures, list) or not disclosures:
        return "missing_disclosures"
    failed_features = [
        str(item.get("feature_id") or "<missing>")
        for item in disclosures
        if not isinstance(item, dict)
        or str(item.get("status") or "") == "analysis_failed"
    ]
    if failed_features:
        return "analysis_failed features=" + ",".join(failed_features)
    return None


def _usable_comparison(value: Any) -> bool:
    """Accept only a complete comparison that may enter blind evaluation."""

    return _comparison_unusable_reason(value) is None


def _raise_if_unusable(value: Any, run_id: str) -> None:
    reason = _comparison_unusable_reason(value)
    if reason is None:
        return
    message = f"I4-S {run_id} 输出不可冻结: {reason}"
    if reason.startswith("rule_version_mismatch"):
        raise RuleVersionMismatchError(message)
    raise RuntimeError(message)


def _images(parsed: dict[str, Any], label: str) -> list[str]:
    result = [
        str(item.get("file_path") or "").strip()
        for item in parsed.get("figures") or []
        if isinstance(item, dict) and str(item.get("file_path") or "").strip()
    ]
    if not result:
        raise RuntimeError(f"{label}: 没有可用于图文比对的页面/附图")
    return result


def _atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _normalise_document_selector(value: Any) -> str:
    return "".join(
        character
        for character in str(value or "").upper()
        if character.isalnum()
    )


def _pending_run_ids(
    comparisons: list[dict[str, Any]],
    analysis_input_sha256: str,
) -> list[str]:
    """Return newest interrupted runs that may have completed server-side.

    A local API restart can interrupt polling while the durable job is requeued
    and later succeeds.  Re-running the blind harness must recover that exact
    run before creating another model call; the existing frozen error remains
    in the audit trail.
    """

    result: list[str] = []
    for item in reversed(comparisons):
        if not isinstance(item, dict):
            continue
        # A document hash alone is not an analysis identity.  The same PDF
        # must be re-analysed when the target claim, limitation decomposition,
        # target patent, prompt, or deterministic rule version changes.
        if str(item.get("analysis_input_sha256") or "") != analysis_input_sha256:
            continue
        if _usable_comparison(item.get("comparison")):
            continue
        run_id = str(item.get("module_run_id") or "").strip()
        if run_id and run_id not in result:
            result.append(run_id)
    return result


def _analysis_input_sha256(
    *,
    claim_id: str,
    expanded_claim_text: str,
    limitations: list[dict[str, Any]],
    target_document_sha256: str,
    document_sha256: str,
) -> str:
    """Bind resumable I4-S work to every semantic analysis input."""

    payload = {
        "claim_id": claim_id,
        "expanded_claim_text": expanded_claim_text,
        "limitations": limitations,
        "target_document_sha256": target_document_sha256,
        "document_sha256": document_sha256,
        "analysis_rule_version": I4S_RULE_VERSION,
        "prompt_version": I4S_PROMPT_VERSION,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plans-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.test.local")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument(
        "--claim",
        action="append",
        default=[],
        help="只运行指定独立权利要求；可重复传入，默认运行计划目录中的全部权利要求",
    )
    parser.add_argument(
        "--document",
        action="append",
        default=[],
        help="只运行指定公开号/PDF文件名；可重复传入，默认运行全部源文献",
    )
    parser.add_argument("--max-attempts", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.max_attempts <= 10:
        raise SystemExit("--max-attempts 必须在 1..10 范围内")
    invocation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    settings = _settings(args.env_file.resolve())
    client = httpx.Client(
        headers={"Authorization": f"Bearer {settings.api_token}"}, timeout=120
    )
    base_url = settings.api_base_url.rstrip("/")
    selected = set(args.case)
    selected_claims = {str(item).strip() for item in args.claim if str(item).strip()}
    selected_documents = {
        _normalise_document_selector(item).removesuffix("PDF")
        for item in args.document
        if _normalise_document_selector(item)
    }
    index: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for plan_file in sorted(args.plans_dir.glob("*/claim-*.json")):
        envelope = json.loads(plan_file.read_text(encoding="utf-8"))
        case_id = str(envelope.get("case_id") or "")
        if selected and case_id not in selected:
            continue
        if selected_claims and str(envelope.get("claim_id") or "") not in selected_claims:
            continue
        if envelope.get("oracle_loaded") is not False:
            raise RuntimeError(f"{plan_file}: I2计划不是盲测冻结输出")
        case_dir = FIXTURES / case_id
        targets = sorted((case_dir / "target_document").glob("*.pdf"))
        if len(targets) != 1:
            raise RuntimeError(f"{case_id}: 目标PDF数量不是1")
        target_parsed = parse_source(str(targets[0]))
        target_document_sha = hashlib.sha256(targets[0].read_bytes()).hexdigest()
        claims = normalize_claims(target_parsed.get("claims") or [])
        claim_id = str(envelope.get("claim_id") or "")
        claim = next(item for item in claims if item.claim_id == claim_id)
        target_context = {
            "patent_number": (target_parsed.get("metadata") or {}).get("patent_number"),
            "title": (target_parsed.get("metadata") or {}).get("title"),
            "abstract": (target_parsed.get("metadata") or {}).get("abstract"),
            "claims": [item.model_dump(mode="json") for item in claims],
            "claims_section_text": target_parsed.get("claims_section_text"),
            "specification": target_parsed.get("specification") or {},
            "page_texts": target_parsed.get("page_texts") or [],
        }
        # Match the live I4-S visual budget.  Full target/reference text remains
        # in the request; images are a bounded mechanism cross-check, not a
        # substitute for the readable source.
        target_images = _images(target_parsed, f"{case_id}目标专利")[:1]
        raw_limitations = list((envelope.get("plan") or {}).get("limitations") or [])
        if not raw_limitations:
            raise RuntimeError(f"{plan_file}: I2计划没有技术特征")
        normalised_limitations, _ = _parse_limitations(
            claim_id=claim_id,
            technical_subject=str(
                (envelope.get("plan") or {}).get("technical_subject") or ""
            ),
            raw_features=raw_limitations,
        )
        limitations = [item.model_dump(mode="json") for item in normalised_limitations]
        destination = args.output_dir / case_id / plan_file.name
        frozen = (
            json.loads(destination.read_text(encoding="utf-8"))
            if destination.is_file()
            else {}
        )
        if frozen and frozen.get("oracle_loaded") is not False:
            raise RuntimeError(f"{destination}: 既有文件不是盲测冻结输出")
        comparisons: list[dict[str, Any]] = list(frozen.get("comparisons") or [])
        completed_input_hashes = {
            str(item.get("analysis_input_sha256") or "")
            for item in comparisons
            if isinstance(item, dict)
            and str(item.get("analysis_input_sha256") or "")
            and _usable_comparison(item.get("comparison"))
        }
        failed_documents: list[str] = []

        def checkpoint() -> None:
            _atomic(
                destination,
                {
                    "benchmark_phase": "blind_i4s_frozen",
                    "oracle_loaded": False,
                    "frozen_at": datetime.now(timezone.utc).isoformat(),
                    "case_id": case_id,
                    "claim_id": claim_id,
                    "target_file": str(targets[0].resolve()),
                    "query_plan_sha256": hashlib.sha256(plan_file.read_bytes()).hexdigest(),
                    "comparison_count": len(
                        [
                            item
                            for item in comparisons
                            if _usable_comparison(item.get("comparison"))
                        ]
                    ),
                    "comparisons": comparisons,
                },
            )

        for document_path in sorted((case_dir / "source_documents").glob("*.pdf")):
            if (
                selected_documents
                and _normalise_document_selector(document_path.stem)
                not in selected_documents
            ):
                continue
            document_sha = hashlib.sha256(document_path.read_bytes()).hexdigest()
            expanded_claim_text = claim.expanded_claim_text or claim.claim_text
            analysis_input_sha = _analysis_input_sha256(
                claim_id=claim_id,
                expanded_claim_text=expanded_claim_text,
                limitations=limitations,
                target_document_sha256=target_document_sha,
                document_sha256=document_sha,
            )
            if analysis_input_sha in completed_input_hashes:
                continue
            parsed = parse_source(str(document_path), require_claims=False)
            document_images = _images(parsed, document_path.name)[:2]
            document_text = str(parsed.get("raw_text") or "").strip()
            if not document_text:
                raise RuntimeError(f"{document_path.name}: 未取得可读全文")
            output: dict[str, Any] | None = None
            attempt_errors: list[str] = []
            run_id = ""
            for pending_run_id in _pending_run_ids(comparisons, analysis_input_sha):
                try:
                    candidate = _wait(client, base_url, pending_run_id)
                    _raise_if_unusable(candidate, pending_run_id)
                    output = candidate
                    run_id = pending_run_id
                    comparisons.append(
                        {
                            "module_run_id": pending_run_id,
                            "document_file": str(document_path.resolve()),
                            "document_sha256": document_sha,
                            "analysis_input_sha256": analysis_input_sha,
                            "comparison": output,
                            "attempt_count": 0,
                            "prior_attempt_errors": [],
                            "recovered_from_interrupted_poll": True,
                        }
                    )
                    completed_input_hashes.add(analysis_input_sha)
                    checkpoint()
                    break
                except RuleVersionMismatchError as exc:
                    # A pending run may legitimately belong to an older rule
                    # version.  Preserve the audit reason and create a fresh
                    # run; only a newly created run with version drift is fatal.
                    attempt_errors.append(
                        f"跳过旧版既有 I4-S {pending_run_id}: {exc}"
                    )
                    continue
                except (RuntimeError, TimeoutError) as exc:
                    attempt_errors.append(
                        f"恢复既有 I4-S {pending_run_id} 失败: {exc}"
                    )
            if output is not None:
                continue
            for attempt in range(1, args.max_attempts + 1):
                created = _request(
                    client,
                    "POST",
                    f"{base_url}/v1/lab/module-runs",
                    {
                        "contract_version": "v1",
                        "module_code": "I4_S_SINGLE_REFERENCE",
                        "input_mode": "manual",
                        "input": {
                            "claim_id": claim_id,
                            "expanded_claim_text": expanded_claim_text,
                            "patent_context": target_context,
                            "limitations": limitations,
                            "document_id": document_path.stem,
                            "publication_number": document_path.stem,
                            "document_title": (parsed.get("metadata") or {}).get("title") or document_path.stem,
                            "document_text": document_text,
                            "target_images": target_images,
                            "document_images": document_images,
                        },
                        "idempotency_key": (
                            f"blind-i4s-v2-{case_id}-{claim_id}-{document_sha[:20]}-"
                            f"{invocation_id}-attempt-{attempt}"
                        ),
                    },
                )
                run_id = str(created["module_run_id"])
                print(
                    f"[{case_id} {document_path.stem}] I4-S attempt "
                    f"{attempt}/{args.max_attempts}: {run_id}",
                    file=sys.stderr,
                    flush=True,
                )
                candidate: dict[str, Any] | None = None
                try:
                    candidate = _wait(client, base_url, run_id)
                    _raise_if_unusable(candidate, run_id)
                    output = candidate
                    break
                except RuleVersionMismatchError:
                    comparisons.append(
                        {
                            "module_run_id": run_id,
                            "document_file": str(document_path.resolve()),
                            "document_sha256": document_sha,
                            "analysis_input_sha256": analysis_input_sha,
                            "comparison": candidate,
                            "attempt_error": _comparison_unusable_reason(candidate),
                        }
                    )
                    checkpoint()
                    raise
                except (RuntimeError, TimeoutError) as exc:
                    attempt_errors.append(str(exc))
                    comparisons.append(
                        {
                            "module_run_id": run_id,
                            "document_file": str(document_path.resolve()),
                            "document_sha256": document_sha,
                            "analysis_input_sha256": analysis_input_sha,
                            "comparison": candidate,
                            "attempt_error": str(exc),
                        }
                    )
                    checkpoint()
                    print(str(exc), file=sys.stderr, flush=True)
                    if attempt == args.max_attempts:
                        failed_documents.append(document_path.name)
                        unresolved.append(
                            {
                                "case_id": case_id,
                                "claim_id": claim_id,
                                "document": document_path.name,
                                "attempt_errors": attempt_errors,
                            }
                        )
                    time.sleep(min(5, attempt))
            if output is None:
                checkpoint()
                continue
            comparisons.append(
                {
                    "module_run_id": run_id,
                    "document_file": str(document_path.resolve()),
                    "document_sha256": document_sha,
                    "analysis_input_sha256": analysis_input_sha,
                    "comparison": output,
                    "attempt_count": len(attempt_errors) + 1,
                    "prior_attempt_errors": attempt_errors,
                }
            )
            completed_input_hashes.add(analysis_input_sha)
            checkpoint()
        checkpoint()
        index.append(
            {
                "case_id": case_id,
                "claim_id": claim_id,
                "comparison_count": sum(
                    _usable_comparison(item.get("comparison"))
                    for item in comparisons
                    if isinstance(item, dict)
                ),
                "failed_document_count": len(failed_documents),
                "failed_documents": failed_documents,
                "file": str(destination),
            }
        )
    summary = {
        "benchmark_phase": "blind_i4s_frozen",
        "oracle_loaded": False,
        "runs": index,
        "unresolved": unresolved,
    }
    _atomic(args.output_dir / "i4s-index.json", summary)
    print(
        json.dumps(
            {"output_dir": str(args.output_dir), **summary},
            ensure_ascii=False,
        )
    )
    if unresolved:
        raise RuntimeError(
            f"{len(unresolved)} 个比较单元达到重试上限；其余文献已继续处理并冻结"
        )


if __name__ == "__main__":
    main()
