#!/usr/bin/env python3
"""Freeze an already-successful I2 run without reading a retrieval oracle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from invalidity.config import Settings
from invalidity.analysis import InvalidityAnalysisEngine
from invalidity.parser_service import parse_source


ROOT = Path(__file__).resolve().parents[1]


class _NoNetworkClient:
    model = "glm-4.6v-blind-recompile"

    def invoke_json(self, **_kwargs: Any) -> Any:
        raise AssertionError("冻结画像重编译不得调用模型")


def _settings(path: Path) -> Settings:
    values = {str(k): str(v) for k, v in dotenv_values(path).items() if v is not None}
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return Settings.from_environment(values, load_dotenv_files=False)


def _request(client: httpx.Client, url: str) -> dict[str, Any]:
    response = client.get(url)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("API响应不是对象")
    return value


def _publication_key(value: Any) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def main() -> None:
    parser = argparse.ArgumentParser()
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--run-id")
    source_group.add_argument(
        "--input-plan",
        type=Path,
        help="已经冻结且 oracle_loaded=false 的目标专利 I2 envelope",
    )
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--target-pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env.test.local")
    args = parser.parse_args()
    run: dict[str, Any]
    investigation: dict[str, Any] | None = None
    if args.input_plan is not None:
        source_envelope = json.loads(
            args.input_plan.resolve().read_text(encoding="utf-8")
        )
        if source_envelope.get("oracle_loaded") is not False:
            raise RuntimeError("输入计划不是 oracle_loaded=false 的盲测冻结输出")
        output = source_envelope.get("plan")
        if not isinstance(output, dict):
            raise RuntimeError("输入计划缺少 plan")
        run = {
            "id": str(source_envelope.get("module_run_id") or "frozen-plan"),
            "investigation_id": "",
        }
    else:
        settings = _settings(args.env_file.resolve())
        client = httpx.Client(
            headers={"Authorization": f"Bearer {settings.api_token}"}, timeout=30
        )
        base_url = settings.api_base_url.rstrip("/")
        details = _request(client, f"{base_url}/v1/lab/module-runs/{args.run_id}")
        run = details.get("module_run") if isinstance(details.get("module_run"), dict) else details
        if run.get("module_code") != "I2_QUERY_PLAN" or run.get("status") not in {"succeeded", "partial"}:
            raise RuntimeError("指定运行不是成功的I2_QUERY_PLAN")
        snapshot = run.get("output_snapshot") if isinstance(run.get("output_snapshot"), dict) else {}
        output = snapshot.get("output")
        if not isinstance(output, dict):
            raise RuntimeError("I2运行缺少冻结输出")
        investigation_id = str(run.get("investigation_id") or "")
        investigation = _request(
            client, f"{base_url}/v1/investigations/{investigation_id}"
        )
    parsed = parse_source(str(args.target_pdf.resolve()))
    parsed_metadata = dict(parsed.get("metadata") or {})
    patent_context = {
        "patent_number": parsed_metadata.get("patent_number"),
        "title": parsed_metadata.get("title"),
        "abstract": parsed_metadata.get("abstract"),
        "bibliographic_data": parsed_metadata,
        "claims": parsed.get("claims") or [],
        "specification": parsed.get("specification") or {},
        "page_texts": parsed.get("page_texts") or [],
        "raw_text": parsed.get("raw_text") or "",
    }
    output = InvalidityAnalysisEngine(_NoNetworkClient()).recompile_initial_query_plan(
        previous_plan=output,
        source_plan_run_id=str(run.get("id") or args.run_id or "frozen-plan"),
        source_sha256=str(output.get("source_sha256") or "") or None,
        patent_context=patent_context,
        max_queries=14,
    ).model_dump(mode="json")
    metadata = parsed_metadata
    expected_number = _publication_key(metadata.get("patent_number"))
    if investigation is not None:
        source = investigation.get("source_snapshot") if isinstance(investigation.get("source_snapshot"), dict) else {}
        patent = source.get("patent_snapshot") if isinstance(source.get("patent_snapshot"), dict) else {}
        stored_number = _publication_key(patent.get("patent_number"))
        if not expected_number or expected_number != stored_number:
            raise RuntimeError("成功I2运行的目标专利与基准目标PDF不一致")
    critical_date = str(
        metadata.get("priority_date") or metadata.get("application_date") or ""
    )[:10]
    if not critical_date:
        raise RuntimeError("目标专利缺少关键日")
    target_sha = hashlib.sha256(args.target_pdf.read_bytes()).hexdigest()
    if args.input_plan is not None:
        source_sha = str(source_envelope.get("target_sha256") or "").lower()
        if source_sha and source_sha != target_sha:
            raise RuntimeError("冻结I2计划与目标PDF的SHA-256不一致")
    envelope = {
        "benchmark_phase": "blind_i2_generation_reused_frozen_run",
        "oracle_loaded": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_id": args.case_id,
        "critical_date": critical_date,
        "target_file": str(args.target_pdf.resolve()),
        "target_sha256": target_sha,
        "claim_id": str(output.get("claim_id") or ""),
        "module_run_id": str(run.get("id") or args.run_id or "frozen-plan"),
        "plan": output,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"output": str(args.output), "case_id": args.case_id, "claim_id": envelope["claim_id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
