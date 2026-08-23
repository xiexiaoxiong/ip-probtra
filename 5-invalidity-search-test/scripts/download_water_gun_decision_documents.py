#!/usr/bin/env python3
"""Download source-only prior-art PDFs for the isolated water-gun fixture.

This utility is intentionally outside ``src/invalidity``.  It may use the
known document identities from the decision benchmark, but it never reads or
stores the decision's comparison reasoning or conclusions.
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
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from invalidity.config import Settings  # noqa: E402
from invalidity.providers import EpoOpsProvider, EvidenceRecord, EvidenceStage  # noqa: E402


DOCUMENTS = (
    ("evidence-01", "US20200080816A1"),
    ("evidence-02", "US5799827A"),
    ("evidence-03", "WO1995000221A1"),
    ("evidence-04", "US20030151880A1"),
    ("evidence-07", "TW262532B"),
    ("evidence-08", "DE841868C"),
    ("evidence-09", "US5975358A"),
    ("evidence-10", "CN101970127A"),
    ("evidence-11", "US3796376A"),
    ("evidence-12", "US20080245815A1"),
    ("evidence-13", "CN107917642A"),
    ("evidence-14", "CN203518819U"),
    ("evidence-15", "CN207950396U"),
    ("evidence-16", "US6138871A"),
    ("evidence-17", "US20120097704A1"),
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 EPO OPS 下载决定书所列现有技术原文，仅输出文献事实"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=SERVICE_ROOT / ".env.test.local",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            SERVICE_ROOT
            / "tests/fixtures/blind_benchmarks"
            / "water_gun_decision_2021227533927"
            / "source_documents"
        ),
    )
    return parser.parse_args()


def _pdf_facts(content: bytes) -> dict[str, Any]:
    if not content.lstrip().startswith(b"%PDF"):
        raise ValueError("下载内容不是 PDF")
    with fitz.open(stream=content, filetype="pdf") as document:
        if document.page_count < 1:
            raise ValueError("PDF 没有页面")
        text_chars = sum(len(page.get_text()) for page in document)
        return {
            "page_count": document.page_count,
            "text_char_count": text_chars,
        }


def main() -> int:
    args = _arguments()
    env_path = args.env_file.expanduser().resolve()
    environment = {
        str(key): str(value)
        for key, value in dotenv_values(env_path).items()
        if value is not None
    }
    settings = Settings.from_environment(environment, load_dotenv_files=False)
    if settings.environment != "test":
        raise SystemExit("下载脚本只允许读取 INVALIDITY_ENV=test 配置")
    if not settings.epo_ops_consumer_key or not settings.epo_ops_consumer_secret:
        raise SystemExit("缺少测试环境 EPO OPS 凭据")

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with EpoOpsProvider(
        consumer_key=settings.epo_ops_consumer_key,
        consumer_secret=settings.epo_ops_consumer_secret,
    ) as provider:
        for evidence_label, publication_number in DOCUMENTS:
            try:
                record = provider.retrieve(
                    EvidenceRecord(
                        provider="decision_fixture_import",
                        source_type="patent",
                        external_id=publication_number,
                        title=publication_number,
                        source_url="https://ops.epo.org",
                        publication_number=publication_number,
                        provenance={"discovery_provider": "human_identified_decision"},
                    )
                )
                artifact = record.primary_artifact
                if record.stage is not EvidenceStage.RETRIEVED or artifact is None:
                    raise ValueError("EPO 未返回可下载的完整文献 PDF")
                if artifact.media_type != "application/pdf":
                    raise ValueError("EPO 主工件不是 PDF")
                content = artifact.content
                facts = _pdf_facts(content)
                destination = output_dir / f"{publication_number}.pdf"
                destination.write_bytes(content)
                rows.append(
                    {
                        "evidence_label": evidence_label,
                        "requested_publication_number": publication_number,
                        "publication_number": record.publication_number,
                        "title": record.title,
                        "publication_date": record.publication_date,
                        "source_provider": "epo_ops",
                        "source_url": artifact.source_url,
                        "file_name": destination.name,
                        "byte_size": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                        **facts,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - utility reports each document
                failures.append(
                    {
                        "evidence_label": evidence_label,
                        "publication_number": publication_number,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    }
                )

    print(
        json.dumps(
            {
                "documents": rows,
                "failures": failures,
                "conclusions_imported": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
