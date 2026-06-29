#!/usr/bin/env python3
"""Verify module1 extracts actual patent figure clips from example PDFs."""

from __future__ import annotations

import sys
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import fitz  # noqa: E402
from PIL import Image  # noqa: E402

from graphs.nodes.file_read_node import (  # noqa: E402
    _extract_pdf_text_with_pymupdf,
    _extract_pdf_text_with_tesseract,
)
from graphs.nodes.figure_extract_node import (  # noqa: E402
    _extract_figure_clips_from_pdf,
    _extract_figure_descriptions,
    _extract_figure_labels,
    _find_candidate_figure_pages,
)
from graphs.nodes.structure_identify_node import _fallback_structure_identify  # noqa: E402


DEFAULT_EXAMPLES_DIR = Path("/Users/xiexiaoxiong/Downloads/IP-probtra/实例专利")
DEFAULT_OUTPUT_DIR = Path("/tmp/patent-module1-figure-test")


def _read_pdf_text(path: Path) -> tuple[str, str]:
    text = ""
    try:
        text = _extract_pdf_text_with_pymupdf(str(path))
    except Exception:
        text = ""
    if text and len(text.strip()) >= 50:
        return text, "pymupdf"
    return _extract_pdf_text_with_tesseract(str(path), max_pages=20), "ocr"


def _image_size_from_bytes(data: bytes) -> tuple[int, int]:
    from io import BytesIO

    image = Image.open(BytesIO(data))
    return image.size


def _expected_label_ids(doc: fitz.Document, candidate_pages: list[int]) -> set[str]:
    labels: set[str] = set()
    for page_num in candidate_pages:
        text = doc[page_num].get_text("text") or ""
        normalized = text.replace("说明书附图", "")
        for figure_id in _extract_figure_labels(normalized):
            labels.add(figure_id)
    return labels


def _remove_parent_labels_covered_by_subfigures(labels: set[str], output_ids: set[str]) -> set[str]:
    result = set(labels)
    for label in labels:
        match = re.fullmatch(r"图(\d+)", label)
        if not match:
            continue
        prefix = f"图{match.group(1)}"
        if any(re.fullmatch(rf"{re.escape(prefix)}[A-Z]", item) for item in labels | output_ids):
            result.discard(label)
    return result


def main() -> int:
    examples_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLES_DIR
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(examples_dir.glob("*.pdf")) + sorted(examples_dir.glob("*.PDF"))
    if not pdfs:
        print(f"No PDF files found in {examples_dir}")
        return 1

    failed: list[str] = []
    for path in pdfs:
        errors = []
        raw_text, text_mode = _read_pdf_text(path)
        sections, _, _ = _fallback_structure_identify(raw_text, errors)
        descriptions = _extract_figure_descriptions(sections)

        doc = fitz.open(str(path))
        candidate_pages = _find_candidate_figure_pages(doc, descriptions)
        clips = _extract_figure_clips_from_pdf(doc, descriptions, candidate_pages, errors)
        expected_labels = _expected_label_ids(doc, candidate_pages)

        out_ids = {clip.figure_id for clip in clips}
        comparable_expected_labels = _remove_parent_labels_covered_by_subfigures(expected_labels, out_ids)
        missing_labels = sorted(comparable_expected_labels - out_ids)
        full_page_like: list[str] = []
        pdf_out = output_dir / path.stem
        pdf_out.mkdir(parents=True, exist_ok=True)

        for clip in clips:
            image_size = _image_size_from_bytes(clip.image_bytes)
            page = doc[clip.page_num]
            page_render_width = int(page.rect.width * 2)
            page_render_height = int(page.rect.height * 2)
            if image_size[0] > page_render_width * 0.86 and image_size[1] > page_render_height * 0.86:
                full_page_like.append(clip.figure_id)

            safe_name = clip.file_name.replace("/", "_").replace("\\", "_")
            (pdf_out / safe_name).write_bytes(clip.image_bytes)

        doc.close()

        ok = (
            len(clips) >= 2
            and not missing_labels
            and not full_page_like
            and not errors
        )
        status = "OK" if ok else "FAIL"
        print(
            f"{status} {path.name} text={text_mode} candidates={[p + 1 for p in candidate_pages]} "
            f"descriptions={len(descriptions)} figures={len(clips)} ids={sorted(out_ids)} "
            f"missing_labels={missing_labels} full_page_like={full_page_like} errors={[e.error_type for e in errors]}"
        )
        if not ok:
            failed.append(path.name)

    if failed:
        print(f"FAILED: {failed}")
        print(f"Extracted images are in {output_dir}")
        return 1

    print(f"All {len(pdfs)} example patent PDFs extracted non-page figure clips.")
    print(f"Extracted images are in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
