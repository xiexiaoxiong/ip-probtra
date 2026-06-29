#!/usr/bin/env python3
"""Verify module1 can extract abstracts from the example patent PDFs."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from graphs.nodes.file_read_node import (  # noqa: E402
    _extract_pdf_text_with_pymupdf,
    _extract_pdf_text_with_tesseract,
)
from graphs.nodes.structure_identify_node import _extract_cn_patent_metadata  # noqa: E402


DEFAULT_EXAMPLES_DIR = Path("/Users/xiexiaoxiong/Downloads/IP-probtra/实例专利")


def _read_pdf_text(path: Path) -> tuple[str, str]:
    text = ""
    try:
        text = _extract_pdf_text_with_pymupdf(str(path))
    except Exception:
        text = ""
    if text and len(text.strip()) >= 50:
        return text, "pymupdf"
    return _extract_pdf_text_with_tesseract(str(path), max_pages=15), "ocr"


def main() -> int:
    examples_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLES_DIR
    pdfs = sorted(examples_dir.glob("*.pdf")) + sorted(examples_dir.glob("*.PDF"))
    if not pdfs:
        print(f"No PDF files found in {examples_dir}")
        return 1

    failed: list[str] = []
    for path in pdfs:
        text, mode = _read_pdf_text(path)
        metadata = _extract_cn_patent_metadata(text)
        abstract = (metadata.abstract or "").strip()
        ok = len(abstract) >= 20
        status = "OK" if ok else "FAIL"
        print(
            f"{status} {path.name} mode={mode} "
            f"title={metadata.title or ''} abstract_len={len(abstract)} "
            f"abstract={abstract[:120]!r}"
        )
        if not ok:
            failed.append(path.name)

    if failed:
        print(f"FAILED: {failed}")
        return 1
    print(f"All {len(pdfs)} example patent PDFs have abstracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
