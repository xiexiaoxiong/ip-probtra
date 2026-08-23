#!/usr/bin/env python3
"""Download target and patent prior-art PDFs for decision benchmarks.

The evidence identities are known only to this isolated benchmark utility.
Retrieval is EPO-first.  Google Patents is used only as a same-publication PDF
fallback.  The utility never reads or writes human comparison conclusions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import fitz
from dotenv import dotenv_values


SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SERVICE_ROOT.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from decision_benchmark_cases import CASES  # noqa: E402
from invalidity.config import Settings  # noqa: E402
from invalidity.providers import (  # noqa: E402
    EpoOpsProvider,
    EvidenceRecord,
    EvidenceStage,
    GooglePatentsProvider,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--env-file",
        type=Path,
        default=SERVICE_ROOT / ".env.test.local",
    )
    parser.add_argument("--case-id", action="append", default=[])
    return parser.parse_args()


def _pdf_facts(content: bytes) -> dict[str, Any]:
    if not content.lstrip().startswith(b"%PDF"):
        raise ValueError("响应不是 PDF")
    with fitz.open(stream=content, filetype="pdf") as document:
        if document.page_count < 1:
            raise ValueError("PDF 没有页面")
        return {
            "page_count": document.page_count,
            "text_char_count": sum(len(page.get_text()) for page in document),
        }


def _record(number: str) -> EvidenceRecord:
    return EvidenceRecord(
        provider="decision_benchmark_import",
        source_type="patent",
        external_id=number,
        title=number,
        source_url=f"https://patents.google.com/patent/{number}/en",
        publication_number=number,
        provenance={"identity_origin": "decision_evidence_list"},
    )


def _retrieve_pdf(
    number: str,
    *,
    epo: EpoOpsProvider,
    google: GooglePatentsProvider,
) -> tuple[bytes, str, str, str | None, str | None]:
    errors: list[str] = []
    try:
        result = epo.retrieve(_record(number))
        artifact = result.primary_artifact
        if result.stage is EvidenceStage.RETRIEVED and artifact is not None:
            _pdf_facts(artifact.content)
            return (
                artifact.content,
                "epo_ops",
                artifact.source_url,
                result.title,
                result.publication_date,
            )
        errors.append("EPO 未返回完整 PDF")
    except Exception as exc:  # noqa: BLE001 - fallback is same exact identity
        errors.append(f"EPO {type(exc).__name__}: {str(exc)[:180]}")

    try:
        detail = google.retrieve(_record(number))
        if detail.publication_number != number:
            raise ValueError("Google Patents 返回公开号不一致")
        if not detail.pdf_url:
            raise ValueError("Google Patents 详情页没有 PDF 地址")
        artifact = google.download_pdf(detail)
        _pdf_facts(artifact.content)
        return (
            artifact.content,
            "google_patents_pdf",
            artifact.source_url,
            detail.title,
            detail.publication_date,
        )
    except Exception as exc:  # noqa: BLE001 - report exact unresolved document
        errors.append(f"Google {type(exc).__name__}: {str(exc)[:180]}")
    raise RuntimeError("; ".join(errors))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = _arguments()
    environment = {
        str(key): str(value)
        for key, value in dotenv_values(args.env_file.expanduser().resolve()).items()
        if value is not None
    }
    settings = Settings.from_environment(environment, load_dotenv_files=False)
    if settings.environment != "test":
        raise SystemExit("只允许读取 INVALIDITY_ENV=test 配置")
    selected = set(args.case_id)
    cases = [case for case in CASES if not selected or case["case_id"] in selected]
    if selected - {case["case_id"] for case in cases}:
        raise SystemExit("存在未知 --case-id")

    benchmark_root = SERVICE_ROOT / "tests/fixtures/blind_benchmarks"
    failures: list[dict[str, str]] = []
    with EpoOpsProvider(
        consumer_key=settings.epo_ops_consumer_key,
        consumer_secret=settings.epo_ops_consumer_secret,
    ) as epo, GooglePatentsProvider() as google:
        for case in cases:
            case_root = benchmark_root / str(case["case_id"])
            target_dir = case_root / "target_document"
            sources_dir = case_root / "source_documents"
            target_dir.mkdir(parents=True, exist_ok=True)
            sources_dir.mkdir(parents=True, exist_ok=True)

            manifest: dict[str, Any] = {
                "fixture_version": "decision-benchmark-source-v1",
                "case_id": case["case_id"],
                "runtime_visibility": "source_inputs_only",
                "conclusions_imported": False,
                "comparison_reasoning_imported": False,
                "target": dict(case["target"]),
                "documents": [],
                "non_patent_documents": list(case.get("non_patent_documents") or ()),
            }
            items = [("target", case["target"]["publication_number"], False)]
            items.extend(case["documents"])
            for label, number, decision_date_qualified in items:
                try:
                    content, provider_name, source_url, title, publication_date = (
                        _retrieve_pdf(number, epo=epo, google=google)
                    )
                    destination_dir = target_dir if label == "target" else sources_dir
                    destination = destination_dir / f"{number}.pdf"
                    destination.write_bytes(content)
                    facts = _pdf_facts(content)
                    row = {
                        "evidence_label": label,
                        "publication_number": number,
                        "title": title or number,
                        "publication_date": publication_date,
                        "decision_date_qualified": bool(decision_date_qualified),
                        "file_name": str(destination.relative_to(case_root)),
                        "source_provider": provider_name,
                        "source_url": source_url,
                        "byte_size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        **facts,
                    }
                    if label == "target":
                        manifest["target_document"] = row
                    else:
                        manifest["documents"].append(row)
                except Exception as exc:  # noqa: BLE001 - preserve per-doc failure
                    failures.append(
                        {
                            "case_id": str(case["case_id"]),
                            "evidence_label": label,
                            "publication_number": number,
                            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                        }
                    )
            _write_json(case_root / "manifest.json", manifest)

    print(json.dumps({"failures": failures}, ensure_ascii=False, indent=2))
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
