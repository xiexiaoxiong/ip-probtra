#!/usr/bin/env python3
"""Recompile frozen blind I2 profiles without another model invocation.

Only the previous oracle-free plan and the matching ``target_document`` PDF are
read.  Source-document manifests, retrieval benchmarks, decisions and prior-art
files are intentionally outside this tool's discovery path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from invalidity.analysis import InvalidityAnalysisEngine
from invalidity.module1 import normalize_claims
from invalidity.parser_service import parse_source


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "tests/fixtures/blind_benchmarks"


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


def _plan_files(root: Path, selected: set[str]) -> list[Path]:
    result = []
    for path in sorted(root.glob("*/claim-*.json")):
        if not selected or path.parent.name in selected:
            result.append(path)
    if not result:
        raise SystemExit("没有发现符合条件的冻结 claim-*.json")
    return result


def _target(fixtures: Path, case_id: str) -> Path:
    targets = sorted((fixtures / case_id / "target_document").glob("*.pdf"))
    if len(targets) != 1:
        raise RuntimeError(f"{case_id}: target_document 必须且只能包含一个PDF")
    return targets[0]


def _patent_context(target: Path) -> dict[str, Any]:
    parsed = parse_source(str(target))
    claims = normalize_claims(parsed.get("claims") or [])
    return {
        "patent_number": (parsed.get("metadata") or {}).get("patent_number"),
        "title": (parsed.get("metadata") or {}).get("title"),
        "abstract": (parsed.get("metadata") or {}).get("abstract"),
        "bibliographic_data": dict(parsed.get("metadata") or {}),
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "claims_section_text": parsed.get("claims_section_text"),
        "specification": parsed.get("specification") or {},
        "figures": parsed.get("figures") or [],
        "page_texts": parsed.get("page_texts") or [],
        "raw_text": parsed.get("raw_text") or "",
        "source_sha256": parsed.get("source_sha256"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()

    selected = {str(item).strip() for item in args.case if str(item).strip()}
    context_cache: dict[str, tuple[Path, str, dict[str, Any]]] = {}
    summaries: list[dict[str, Any]] = []
    for source in _plan_files(args.input_dir.resolve(), selected):
        envelope = json.loads(source.read_text(encoding="utf-8"))
        if envelope.get("oracle_loaded") is not False:
            raise RuntimeError(f"{source}: 不是oracle-free冻结输入")
        case_id = str(envelope.get("case_id") or "").strip()
        claim_id = str(envelope.get("claim_id") or "").strip()
        if not case_id or not claim_id or not isinstance(envelope.get("plan"), dict):
            raise RuntimeError(f"{source}: 冻结计划身份不完整")
        if case_id not in context_cache:
            target = _target(args.fixtures.resolve(), case_id)
            target_sha = _sha256(target)
            context_cache[case_id] = (
                target,
                target_sha,
                _patent_context(target),
            )
        target, target_sha, patent_context = context_cache[case_id]
        if str(envelope.get("target_sha256") or "") != target_sha:
            raise RuntimeError(f"{source}: 目标PDF哈希与冻结画像不一致")
        previous_run_id = str(envelope.get("module_run_id") or "").strip()
        previous_plan = envelope["plan"]
        model = str(previous_plan.get("model") or "glm-4.6v")
        engine = InvalidityAnalysisEngine(SimpleNamespace(model=model))
        recompiled = engine.recompile_initial_query_plan(
            previous_plan=previous_plan,
            source_plan_run_id=previous_run_id,
            source_sha256=target_sha,
            patent_context=patent_context,
            max_queries=3,
        )
        destination = args.output_dir / case_id / source.name
        result = {
            "benchmark_phase": "blind_i2_offline_recompile",
            "oracle_loaded": False,
            "case_id": case_id,
            "claim_id": claim_id,
            "critical_date": envelope.get("critical_date"),
            "target_file": str(target.resolve()),
            "target_sha256": target_sha,
            "source_plan_file": str(source.resolve()),
            "source_module_run_id": previous_run_id,
            "module_run_id": f"offline-recompile:{previous_run_id}",
            "plan": recompiled.model_dump(mode="json"),
        }
        _write_json(destination, result)
        summaries.append(
            {
                "case_id": case_id,
                "claim_id": claim_id,
                "source": str(source),
                "file": str(destination),
            }
        )
    _write_json(
        args.output_dir / "recompile-index.json",
        {
            "benchmark_phase": "blind_i2_offline_recompile",
            "oracle_loaded": False,
            "plans": summaries,
        },
    )
    print(json.dumps({"output_dir": str(args.output_dir), "plans": summaries}, ensure_ascii=False))


if __name__ == "__main__":
    main()
