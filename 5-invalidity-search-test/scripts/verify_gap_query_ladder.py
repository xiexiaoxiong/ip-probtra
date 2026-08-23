#!/usr/bin/env python3
"""Verify all five I2-G vocabulary rounds against one frozen real patent case."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping

import fitz
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from invalidity.analysis import (
    I2_PROMPT_VERSION,
    I2_RULE_VERSION,
    InvalidityAnalysisEngine,
    QueryPlan,
    _boolean_or_members,
)
from invalidity.config import Settings
from invalidity.llm import VisionLLMClient


EXPECTED_STRATEGIES = [
    "adjacent_object_direct_structure",
    "broader_object_structural_family",
    "same_function_object_action_role",
    "subsystem_component_relation_path",
    "analogous_domain_principle_effect",
]


class _OfflineVocabularyClient:
    model = "fixture-gap-vocabulary-not-live-model"

    def __init__(self, responses: list[Mapping[str, Any]]) -> None:
        self.responses = list(responses)

    def invoke_json(self, **_kwargs: Any) -> Mapping[str, Any]:
        if not self.responses:
            raise RuntimeError("离线词表夹具响应不足五轮")
        return self.responses.pop(0)


def _settings(path: Path) -> Settings:
    values = {
        str(key): str(value)
        for key, value in dotenv_values(path).items()
        if value is not None
    }
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return Settings.from_environment(values, load_dotenv_files=False)


def _module_output(details: Mapping[str, Any]) -> Mapping[str, Any]:
    run = details.get("module_run")
    if not isinstance(run, Mapping):
        run = details
    snapshot = run.get("output_snapshot")
    if not isinstance(snapshot, Mapping):
        raise RuntimeError("指定 module run 缺少 output_snapshot")
    output = snapshot.get("output")
    if not isinstance(output, Mapping):
        raise RuntimeError("指定 module run 缺少冻结 QueryPlan 输出")
    return output


def _render_first_page(pdf_path: Path, output_path: Path) -> None:
    with fitz.open(pdf_path) as document:
        if document.page_count < 1:
            raise RuntimeError("目标 PDF 没有页面")
        document.load_page(0).get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(
            output_path
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module-run-id", required=True)
    parser.add_argument("--target-pdf", type=Path, required=True)
    parser.add_argument("--feature-id", action="append", required=True)
    parser.add_argument("--technical-subject", required=True)
    parser.add_argument(
        "--offline-vocabulary-file",
        type=Path,
        help="仅验证真实冻结案件输入与编译守门；不调用 GLM",
    )
    parser.add_argument(
        "--env-file", type=Path, default=ROOT / ".env.test.local"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    settings = _settings(args.env_file.resolve())
    response = httpx.get(
        f"{settings.api_base_url.rstrip('/')}/v1/lab/module-runs/"
        f"{args.module_run_id}",
        headers={"Authorization": f"Bearer {settings.api_token}"},
        timeout=30,
    )
    response.raise_for_status()
    details = response.json()
    if not isinstance(details, Mapping):
        raise RuntimeError("module run API 没有返回对象")
    frozen = QueryPlan.model_validate(_module_output(details))
    if frozen.invention_search_profile is None:
        raise RuntimeError("冻结 QueryPlan 缺少 invention_search_profile")
    selected = [
        item.model_copy(update={"technical_subject": args.technical_subject})
        for item in frozen.limitations
        if item.feature_id in set(args.feature_id)
    ]
    if [item.feature_id for item in selected] != args.feature_id:
        raise RuntimeError("feature-id 未按冻结顺序完整匹配")
    profile = frozen.invention_search_profile.model_copy(
        update={"protected_subject": args.technical_subject}
    )
    if args.offline_vocabulary_file is not None:
        raw_vocabularies = json.loads(
            args.offline_vocabulary_file.resolve().read_text(encoding="utf-8")
        )
        if not isinstance(raw_vocabularies, list) or not all(
            isinstance(item, Mapping) for item in raw_vocabularies
        ):
            raise RuntimeError("offline vocabulary file 必须是五轮对象数组")
        llm_client: Any = _OfflineVocabularyClient(raw_vocabularies)
    else:
        llm_client = VisionLLMClient(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            direct_attempt_timeout_seconds=(
                settings.llm_direct_attempt_timeout_seconds
            ),
            direct_probe_timeout_seconds=settings.llm_direct_probe_timeout_seconds,
        )
    engine = InvalidityAnalysisEngine(
        llm_client,
        i2_timeout_seconds=settings.llm_i2_timeout_seconds,
        i2_direct_attempt_timeout_seconds=(
            settings.llm_i2_direct_attempt_timeout_seconds
        ),
    )
    artifact: dict[str, Any] = {
        "schema_version": "real-patent-gap-ladder-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_module_run_id": args.module_run_id,
        "target_pdf": str(args.target_pdf.resolve()),
        "technical_subject": args.technical_subject,
        "prompt_version": I2_PROMPT_VERSION,
        "rule_version": I2_RULE_VERSION,
        "network_used": args.offline_vocabulary_file is None,
        "offline_vocabulary_file": (
            str(args.offline_vocabulary_file.resolve())
            if args.offline_vocabulary_file is not None
            else None
        ),
        "features": [item.model_dump(mode="json") for item in selected],
        "rounds": [],
    }
    previous_expressions: list[str] = []
    with tempfile.TemporaryDirectory(prefix="invalidity-gap-ladder-") as temp_dir:
        target_image = Path(temp_dir) / "target-page-1.png"
        _render_first_page(args.target_pdf.resolve(), target_image)
        for gap_iteration in range(1, 6):
            plan = engine.plan_queries(
                claim_id=frozen.claim_id,
                expanded_claim_text="；".join(item.text for item in selected),
                target_images=[target_image],
                iteration_number=gap_iteration + 1,
                round_kind="gap",
                existing_limitations=selected,
                invention_search_profile=profile,
                gap_feature_ids=args.feature_id,
                previous_iteration_failure_reason=(
                    "上一轮未取得足以关闭区别特征的合格对比文件，必须切换语义层级"
                    if gap_iteration > 1
                    else ""
                ),
                previous_query_expressions=previous_expressions,
                max_queries=len(selected),
            )
            if plan.gap_search_strategy != EXPECTED_STRATEGIES[gap_iteration - 1]:
                raise RuntimeError(
                    f"第{gap_iteration}轮策略错误: {plan.gap_search_strategy}"
                )
            if len(plan.queries) != len(selected):
                raise RuntimeError(
                    f"第{gap_iteration}轮不是每个区别特征恰好一组"
                )
            rows = []
            for query in plan.queries:
                groups = _boolean_or_members(query.expression)
                if len(groups) != 2:
                    raise RuntimeError(
                        f"第{gap_iteration}轮 {query.gap_feature_ids} 不是两个 OR 组"
                    )
                rows.append(
                    {
                        "feature_id": query.gap_feature_ids[0],
                        "expression": query.expression,
                        "subject_group": groups[0],
                        "feature_group": groups[1],
                    }
                )
            audit = engine.last_invocation_audit or {}
            artifact["rounds"].append(
                {
                    "gap_search_iteration": gap_iteration,
                    "gap_search_strategy": plan.gap_search_strategy,
                    "gap_search_strategy_label": plan.gap_search_strategy_label,
                    "model": plan.model,
                    "attempt_count": audit.get("attempt_count", 1),
                    "rows": rows,
                }
            )
            previous_expressions = [item.expression for item in plan.queries]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(artifact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
