from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz


SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_MAX_PDF_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_PDF_PAGES = 300
DEFAULT_MAX_PDF_PAGE_PIXELS = 40_000_000
DEFAULT_MAX_RENDERED_PIXELS = 16_000_000
DEFAULT_MAX_EXTRACTED_CHARS = 2_000_000
DEFAULT_PDF_OPERATION_SECONDS = 30.0


class ArtifactBudgetError(ValueError):
    """A document would exceed deterministic decode/render resource budgets."""


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    uri: str
    sha256: str
    byte_size: int
    mime_type: str
    artifact_type: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "mime_type": self.mime_type,
            "artifact_type": self.artifact_type,
        }


class ArtifactStore:
    def __init__(self, root: Path, environment: str) -> None:
        self.root = root.expanduser().resolve()
        expected = f"invalidity/{environment}"
        if expected not in self.root.as_posix():
            raise ValueError(f"工件根目录必须包含隔离前缀 {expected}")
        self.root.mkdir(parents=True, exist_ok=True)

    def investigation_dir(self, investigation_id: str) -> Path:
        safe_id = self._safe(investigation_id)
        path = self.root / "investigations" / safe_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_bytes(
        self,
        investigation_id: str,
        relative_path: str,
        content: bytes,
        *,
        artifact_type: str,
        mime_type: str | None = None,
    ) -> StoredArtifact:
        destination = self._destination(investigation_id, relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_bytes(content)
        os.replace(temporary, destination)
        return StoredArtifact(
            uri=str(destination),
            sha256=hashlib.sha256(content).hexdigest(),
            byte_size=len(content),
            mime_type=mime_type
            or mimetypes.guess_type(destination.name)[0]
            or "application/octet-stream",
            artifact_type=artifact_type,
        )

    def write_json(
        self,
        investigation_id: str,
        relative_path: str,
        value: Any,
        *,
        artifact_type: str,
    ) -> StoredArtifact:
        content = json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True, default=str
        ).encode("utf-8")
        return self.write_bytes(
            investigation_id,
            relative_path,
            content,
            artifact_type=artifact_type,
            mime_type="application/json",
        )

    def render_pdf_images(
        self,
        investigation_id: str,
        pdf_path: str | Path,
        relative_dir: str,
        *,
        max_images: int = 2,
        max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
        max_pages: int = DEFAULT_MAX_PDF_PAGES,
        max_page_pixels: int = DEFAULT_MAX_PDF_PAGE_PIXELS,
        max_rendered_pixels: int = DEFAULT_MAX_RENDERED_PIXELS,
        operation_timeout_seconds: float = DEFAULT_PDF_OPERATION_SECONDS,
        render_scale: float = 1.5,
    ) -> list[StoredArtifact]:
        source = Path(pdf_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if max_images < 0 or max_pages < 1 or max_pdf_bytes < 1:
            raise ValueError("PDF 渲染预算参数无效")
        if source.stat().st_size > max_pdf_bytes:
            raise ArtifactBudgetError("PDF 字节数超过渲染上限")
        if render_scale <= 0 or operation_timeout_seconds <= 0:
            raise ValueError("PDF 渲染比例/超时必须大于 0")
        deadline = time.monotonic() + operation_timeout_seconds
        document = fitz.open(source)
        artifacts: list[StoredArtifact] = []
        try:
            if document.page_count < 1:
                raise ArtifactBudgetError("PDF 没有页面")
            if document.page_count > max_pages:
                raise ArtifactBudgetError("PDF 页数超过处理上限")
            ranked_rows: list[tuple[int, int]] = []
            for page_index in range(document.page_count):
                self._check_deadline(deadline)
                page = document.load_page(page_index)
                pixels = self._page_pixels(page, render_scale)
                if pixels > max_page_pixels:
                    raise ArtifactBudgetError("PDF 单页解码像素超过上限")
                ranked_rows.append((len(page.get_images(full=True)), page_index))
            ranked = [
                page_index
                for _image_count, page_index in sorted(
                    ranked_rows,
                    key=lambda item: (-item[0], item[1]),
                )
            ]
            rendered_pixels = 0
            for page_index in ranked[:max_images]:
                self._check_deadline(deadline)
                pixmap = document.load_page(page_index).get_pixmap(
                    matrix=fitz.Matrix(render_scale, render_scale), alpha=False
                )
                rendered_pixels += pixmap.width * pixmap.height
                if rendered_pixels > max_rendered_pixels:
                    raise ArtifactBudgetError("PDF 渲染像素总量超过上限")
                artifacts.append(
                    self.write_bytes(
                        investigation_id,
                        f"{relative_dir}/page-{page_index + 1}.png",
                        pixmap.tobytes("png"),
                        artifact_type="rendered_patent_page",
                        mime_type="image/png",
                    )
                )
        finally:
            document.close()
        return artifacts

    def extract_pdf_text(
        self,
        pdf_content: bytes,
        *,
        max_pdf_bytes: int = DEFAULT_MAX_PDF_BYTES,
        max_pages: int = DEFAULT_MAX_PDF_PAGES,
        max_page_pixels: int = DEFAULT_MAX_PDF_PAGE_PIXELS,
        max_extracted_chars: int = DEFAULT_MAX_EXTRACTED_CHARS,
        operation_timeout_seconds: float = DEFAULT_PDF_OPERATION_SECONDS,
    ) -> str:
        if not pdf_content.lstrip().startswith(b"%PDF"):
            raise ArtifactBudgetError("输入不是 PDF")
        if len(pdf_content) > max_pdf_bytes:
            raise ArtifactBudgetError("PDF 字节数超过解析上限")
        if max_pages < 1 or max_extracted_chars < 1 or operation_timeout_seconds <= 0:
            raise ValueError("PDF 文本提取预算参数无效")
        deadline = time.monotonic() + operation_timeout_seconds
        document = fitz.open(stream=pdf_content, filetype="pdf")
        parts: list[str] = []
        total_chars = 0
        try:
            if document.page_count < 1:
                raise ArtifactBudgetError("PDF 没有页面")
            if document.page_count > max_pages:
                raise ArtifactBudgetError("PDF 页数超过处理上限")
            for page_index in range(document.page_count):
                self._check_deadline(deadline)
                page = document.load_page(page_index)
                if self._page_pixels(page, 1.0) > max_page_pixels:
                    raise ArtifactBudgetError("PDF 单页尺寸超过解析上限")
                text = page.get_text("text")
                total_chars += len(text)
                if total_chars > max_extracted_chars:
                    raise ArtifactBudgetError("PDF 提取文本超过字符上限")
                # PostgreSQL JSONB rejects U+0000 even when JSON serialisation
                # escapes it.  Some publisher PDFs expose embedded NULs through
                # PyMuPDF, so replace them before the text enters any durable
                # module/workflow snapshot.  A space preserves the token
                # boundary instead of accidentally joining two technical words.
                parts.append(text.replace("\x00", " "))
        finally:
            document.close()
        return "\n\n".join(parts).strip()

    @staticmethod
    def _page_pixels(page: fitz.Page, scale: float) -> int:
        width = max(1, math.ceil(float(page.rect.width) * scale))
        height = max(1, math.ceil(float(page.rect.height) * scale))
        return width * height

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if time.monotonic() > deadline:
            raise ArtifactBudgetError("PDF 处理超过时间预算")

    def _destination(self, investigation_id: str, relative_path: str) -> Path:
        base = self.investigation_dir(investigation_id)
        parts = [self._safe(part) for part in Path(relative_path).parts if part not in {"", "."}]
        if not parts or any(part == ".." for part in parts):
            raise ValueError("无效工件相对路径")
        destination = (base.joinpath(*parts)).resolve()
        if base not in destination.parents and destination != base:
            raise ValueError("工件路径越界")
        return destination

    @staticmethod
    def _safe(value: str) -> str:
        cleaned = SAFE_NAME.sub("_", str(value).strip())
        if not cleaned or cleaned in {".", ".."}:
            raise ValueError("工件名称为空或不安全")
        return cleaned[:180]
