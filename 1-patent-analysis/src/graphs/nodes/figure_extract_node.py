"""
附图提取节点
职责：从专利文档中提取附图并保存到本地文件
支持：PyMuPDF提取（PDF）、本地文件落盘、URL转存
"""
import os
import logging
import re
import tempfile
import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Set
from urllib.parse import quote
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import (
    FigureExtractInput,
    FigureExtractOutput,
    PatentFigure,
    SpecificationSection,
    ParseError
)
from utils.file.file import File
from utils.runtime_paths import get_module_public_base_url, get_task_figures_dir

logger = logging.getLogger(__name__)
MIN_EMBEDDED_IMAGE_BYTES = 5000
RENDER_FALLBACK_DPI_SCALE = 2.0
FIGURE_RENDER_SCALE = 2.0


@dataclass
class FigureClip:
    figure_id: str
    description: str
    page_num: int
    image_bytes: bytes
    file_name: str


@dataclass
class FigureLabel:
    figure_id: str
    bbox: Any


def figure_extract_node(
    state: FigureExtractInput, config: RunnableConfig, runtime: Runtime[Context]
) -> FigureExtractOutput:
    """
    title: 专利附图提取
    desc: 从专利文档中提取附图，上传对象存储并返回URL列表
    integrations: 对象存储
    """
    patent_file: File = state.patent_file
    task_id: str = state.task_id
    specification_sections: List[SpecificationSection] = state.specification_sections
    figures_list: List[PatentFigure] = []
    figure_errors: List[ParseError] = []
    
    try:
        # 判断文件格式（移除URL查询参数后再解析扩展名）
        file_ext = ""
        file_url = patent_file.url
        if file_url:
            # 移除 URL 查询参数（如 ?sign=xxx）
            clean_url = file_url.split('?')[0]
            file_ext = os.path.splitext(clean_url)[1].lower()
            logger.info(f"文件格式检测: URL={file_url[:50]}..., file_ext={file_ext}")
        
        # 根据文件格式选择处理方式
        if file_ext == ".pdf":
            figures_list = _extract_figures_from_pdf(
                patent_file, task_id, specification_sections, figure_errors
            )
        elif file_ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp"]:
            figures_list = _save_single_image(
                patent_file, task_id, specification_sections, figure_errors
            )
        elif file_ext in [".html", ".htm"]:
            figures_list = _extract_figures_from_html(
                patent_file, task_id, specification_sections, figure_errors
            )
        elif file_ext == ".txt":
            figures_list = _extract_figures_from_text(
                patent_file, task_id, specification_sections, figure_errors
            )
            if not figures_list:
                logger.info("TXT文件未包含图片URL，跳过附图提取")
        else:
            logger.warning(f"文件格式 {file_ext} 不支持附图提取")
        
        logger.info(f"附图提取完成，共提取 {len(figures_list)} 张图片")
        
    except Exception as e:
        logger.error(f"附图提取失败: {str(e)}", exc_info=True)
        figure_errors.append(ParseError(
            error_type="FIGURE_EXTRACT_ERROR",
            error_message=f"附图提取失败: {str(e)}",
            is_recoverable=True
        ))
    
    return FigureExtractOutput(
        figures_list=figures_list,
        figure_errors=figure_errors
    )


def _extract_figures_from_pdf(
    patent_file: File,
    task_id: str,
    specification_sections: List[SpecificationSection],
    errors: List[ParseError]
) -> List[PatentFigure]:
    """从PDF中提取说明书附图页（使用PyMuPDF整页渲染）"""
    figures: List[PatentFigure] = []
    
    try:
        # 检查是否安装了 PyMuPDF
        try:
            import fitz  # type: ignore  # PyMuPDF
        except ImportError:
            logger.warning("PyMuPDF未安装")
            errors.append(ParseError(
                error_type="MISSING_DEPENDENCY",
                error_message="PyMuPDF未安装，请执行: pip install PyMuPDF",
                is_recoverable=True
            ))
            return figures
        
        # 下载PDF文件
        import requests
        
        pdf_path = ""
        file_url = patent_file.url
        
        if file_url.startswith("http"):
            # 从URL下载
            response = requests.get(file_url, timeout=60)
            response.raise_for_status()
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                tmp_file.write(response.content)
                pdf_path = tmp_file.name
        else:
            # 本地文件
            pdf_path = file_url
        
        # 打开PDF
        doc = fitz.open(pdf_path)
        logger.info(f"成功打开PDF文件，共 {len(doc)} 页")
        
        # 提取附图说明
        figure_descriptions = _extract_figure_descriptions(specification_sections)
        candidate_pages = _find_candidate_figure_pages(doc, figure_descriptions)
        figure_clips = _extract_figure_clips_from_pdf(
            doc=doc,
            figure_descriptions=figure_descriptions,
            candidate_pages=candidate_pages,
            errors=errors,
        )
        for clip in figure_clips:
            figures.append(
                _persist_figure_file(
                    task_id=task_id,
                    file_name=clip.file_name,
                    image_bytes=clip.image_bytes,
                    mime_type="image/png",
                    figure_id=clip.figure_id,
                    figure_description=clip.description,
                )
            )
        
        doc.close()
        
        # 清理临时文件
        if pdf_path.startswith("/tmp"):
            try:
                os.unlink(pdf_path)
            except Exception:
                pass
        
        logger.info(
            f"PDF附图提取完成: 候选附图页 {len(candidate_pages)} 页，成功提取 {len(figures)} 张"
        )
        
    except Exception as e:
        logger.error(f"PDF图片提取失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="PDF_IMAGE_EXTRACT_ERROR",
            error_message=f"从PDF提取图片失败: {str(e)}",
            is_recoverable=True
        ))
    
    return figures


def _persist_figure_file(
    task_id: str,
    file_name: str,
    image_bytes: bytes,
    mime_type: str,
    figure_id: str,
    figure_description: str,
) -> PatentFigure:
    task_dir = get_task_figures_dir(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)

    safe_file_name = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in file_name)
    file_path = task_dir / safe_file_name
    file_path.write_bytes(image_bytes)

    file_sha256 = hashlib.sha256(image_bytes).hexdigest()
    figure_url = f"{get_module_public_base_url()}/figures/{quote(task_id)}/{quote(safe_file_name)}"

    return PatentFigure(
        figure_id=figure_id,
        figure_url=figure_url,
        figure_description=figure_description,
        storage_key=None,
        file_path=str(file_path),
        mime_type=mime_type,
        file_size=len(image_bytes),
        file_sha256=file_sha256,
    )


def _extract_figure_clips_from_pdf(
    doc: Any,
    figure_descriptions: Dict[str, str],
    candidate_pages: List[int],
    errors: List[ParseError],
) -> List[FigureClip]:
    """按实际图块提取摘要附图和说明书附图，避免整页截图。"""
    clips: List[FigureClip] = []
    seen_hashes: Set[str] = set()
    used_ids: Set[str] = set()

    abstract_clip = _extract_abstract_figure_clip(doc)
    if abstract_clip:
        image_hash = hashlib.md5(abstract_clip.image_bytes).hexdigest()
        seen_hashes.add(image_hash)
        clips.append(abstract_clip)
        used_ids.add(abstract_clip.figure_id)

    remaining_ids = [figure_id for figure_id in figure_descriptions.keys() if figure_id not in used_ids]
    next_index = _next_numeric_figure_index(used_ids)

    for page_num in candidate_pages:
        page = doc[page_num]
        if page_num == 0:
            continue

        if _is_scanned_full_page(page):
            page_clips = _extract_scanned_page_figure_clips(
                page=page,
                page_num=page_num,
                figure_descriptions=figure_descriptions,
                remaining_ids=remaining_ids,
                used_ids=used_ids,
                next_index=next_index,
            )
        else:
            page_clips = _extract_native_page_figure_clips(
                page=page,
                page_num=page_num,
                figure_descriptions=figure_descriptions,
                remaining_ids=remaining_ids,
                used_ids=used_ids,
                next_index=next_index,
            )

        for clip in page_clips:
            image_hash = hashlib.md5(clip.image_bytes).hexdigest()
            if image_hash in seen_hashes:
                continue
            seen_hashes.add(image_hash)
            clips.append(clip)
            used_ids.add(clip.figure_id)
            if clip.figure_id in remaining_ids:
                remaining_ids.remove(clip.figure_id)
            next_index = _next_numeric_figure_index(used_ids)

    if not clips:
        errors.append(ParseError(
            error_type="NO_FIGURE_CLIPS_FOUND",
            error_message="未能从PDF中定位到摘要附图或说明书附图图块",
            is_recoverable=True,
        ))

    return clips


def _extract_abstract_figure_clip(doc: Any) -> Optional[FigureClip]:
    if len(doc) == 0:
        return None

    page = doc[0]
    if _is_scanned_full_page(page):
        image_bytes = _crop_scanned_abstract_figure(page)
        if not image_bytes:
            return None
    else:
        image_bytes = _crop_native_abstract_figure(page)
        if not image_bytes:
            return None

    return FigureClip(
        figure_id="摘要附图",
        description="摘要附图",
        page_num=0,
        image_bytes=image_bytes,
        file_name="abstract_figure.png",
    )


def _crop_native_abstract_figure(page: Any) -> Optional[bytes]:
    import fitz  # type: ignore

    page_area = page.rect.width * page.rect.height
    candidates = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 1:
            continue
        bbox = fitz.Rect(block["bbox"])
        area = bbox.width * bbox.height
        image_size = len(block.get("image", b""))
        center_x = (bbox.x0 + bbox.x1) / 2
        center_y = (bbox.y0 + bbox.y1) / 2
        if image_size < MIN_EMBEDDED_IMAGE_BYTES:
            continue
        if area < page_area * 0.015:
            continue
        if center_y < page.rect.height * 0.35:
            continue
        # CN首页摘要附图通常位于摘要栏右侧或摘要下方，排除页眉小图标/二维码。
        if center_x < page.rect.width * 0.40 and center_y < page.rect.height * 0.55:
            continue
        candidates.append((area, bbox))

    if not candidates:
        return None

    _, bbox = max(candidates, key=lambda item: item[0])
    return _render_page_clip(page, bbox + (-8, -8, 8, 8))


def _crop_scanned_abstract_figure(page: Any) -> Optional[bytes]:
    image = _render_page_to_pil(page, scale=FIGURE_RENDER_SCALE)
    width, height = image.size
    # 首页摘要附图在中国专利首页一般位于页面右下半部分。
    region = (
        int(width * 0.42),
        int(height * 0.45),
        int(width * 0.93),
        int(height * 0.86),
    )
    bbox = _black_pixel_bbox(image, region)
    if not bbox:
        return None
    x0, y0, x1, y1 = _pad_bbox(bbox, width, height, int(width * 0.025), int(height * 0.025))
    if (x1 - x0) * (y1 - y0) < width * height * 0.01:
        return None
    return _pil_crop_to_png_bytes(image, (x0, y0, x1, y1))


def _extract_native_page_figure_clips(
    page: Any,
    page_num: int,
    figure_descriptions: Dict[str, str],
    remaining_ids: List[str],
    used_ids: Set[str],
    next_index: int,
) -> List[FigureClip]:
    import fitz  # type: ignore

    page_area = page.rect.width * page.rect.height
    labels = _extract_label_blocks(page)
    image_blocks = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 1:
            continue
        bbox = fitz.Rect(block["bbox"])
        image_size = len(block.get("image", b""))
        area = bbox.width * bbox.height
        if image_size < MIN_EMBEDDED_IMAGE_BYTES:
            continue
        if area < page_area * 0.008:
            continue
        if bbox.y1 < page.rect.height * 0.07 or bbox.y0 > page.rect.height * 0.93:
            continue
        image_blocks.append(bbox)

    image_blocks.sort(key=lambda rect: (rect.y0, rect.x0))
    clips: List[FigureClip] = []
    assigned_labels: Set[int] = set()

    for bbox in image_blocks:
        label_index = _find_nearest_label_below(bbox, labels, assigned_labels)
        label: Optional[FigureLabel] = labels[label_index] if label_index is not None else None
        if label_index is not None:
            assigned_labels.add(label_index)

        if label and label.figure_id not in used_ids:
            figure_id = label.figure_id
        elif remaining_ids:
            figure_id = remaining_ids[0]
        else:
            figure_id = f"图{next_index}"
            next_index += 1

        clip_rect = fitz.Rect(bbox)
        if label:
            clip_rect.include_rect(label.bbox)
        image_bytes = _render_page_clip(page, clip_rect + (-8, -8, 8, 8))
        clips.append(FigureClip(
            figure_id=figure_id,
            description=figure_descriptions.get(figure_id, ""),
            page_num=page_num,
            image_bytes=image_bytes,
            file_name=f"figure_page_{page_num + 1}_{_safe_figure_id(figure_id)}.png",
        ))
        used_ids.add(figure_id)
        if figure_id in remaining_ids:
            remaining_ids.remove(figure_id)

    return clips


def _extract_scanned_page_figure_clips(
    page: Any,
    page_num: int,
    figure_descriptions: Dict[str, str],
    remaining_ids: List[str],
    used_ids: Set[str],
    next_index: int,
) -> List[FigureClip]:
    image = _render_page_to_pil(page, scale=FIGURE_RENDER_SCALE)
    regions = _split_scanned_figure_regions(image)
    clips: List[FigureClip] = []

    for region in regions:
        if remaining_ids:
            figure_id = remaining_ids[0]
        else:
            while f"图{next_index}" in used_ids:
                next_index += 1
            figure_id = f"图{next_index}"
            next_index += 1

        image_bytes = _pil_crop_to_png_bytes(image, region)
        clips.append(FigureClip(
            figure_id=figure_id,
            description=figure_descriptions.get(figure_id, ""),
            page_num=page_num,
            image_bytes=image_bytes,
            file_name=f"figure_page_{page_num + 1}_{_safe_figure_id(figure_id)}.png",
        ))
        used_ids.add(figure_id)
        if figure_id in remaining_ids:
            remaining_ids.remove(figure_id)

    return clips


def _is_scanned_full_page(page: Any) -> bool:
    text = re.sub(r"\s+", "", page.get_text("text") or "")
    if len(text) > 20:
        return False
    image_blocks = [block for block in page.get_text("dict").get("blocks", []) if block.get("type") == 1]
    if len(image_blocks) != 1:
        return False
    bbox = image_blocks[0].get("bbox", [0, 0, 0, 0])
    image_area = max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])
    page_area = page.rect.width * page.rect.height
    return image_area >= page_area * 0.85


def _extract_label_blocks(page: Any) -> List[FigureLabel]:
    import fitz  # type: ignore

    labels: List[FigureLabel] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        text = "".join(
            span.get("text", "")
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ).strip()
        normalized = re.sub(r"\s+", "", text)
        match = re.fullmatch(r"图(\d+[A-Za-z]?)", normalized)
        if not match:
            continue
        labels.append(FigureLabel(figure_id=f"图{match.group(1).upper()}", bbox=fitz.Rect(block["bbox"])))
    labels.sort(key=lambda item: (item.bbox.y0, item.bbox.x0))
    return labels


def _find_nearest_label_below(
    image_bbox: Any,
    labels: List[FigureLabel],
    assigned_labels: Set[int],
) -> Optional[int]:
    best: Optional[tuple[float, int]] = None
    image_center = (image_bbox.x0 + image_bbox.x1) / 2
    for index, label in enumerate(labels):
        if index in assigned_labels:
            continue
        if label.bbox.y0 < image_bbox.y1 - 2:
            continue
        vertical_gap = label.bbox.y0 - image_bbox.y1
        if vertical_gap > 95:
            continue
        label_center = (label.bbox.x0 + label.bbox.x1) / 2
        center_gap = abs(label_center - image_center)
        if center_gap > max(image_bbox.width * 0.75, 120):
            continue
        score = vertical_gap + center_gap * 0.1
        if best is None or score < best[0]:
            best = (score, index)
    return best[1] if best else None


def _render_page_clip(page: Any, clip_rect: Any) -> bytes:
    import fitz  # type: ignore

    safe_rect = fitz.Rect(clip_rect) & page.rect
    pix = page.get_pixmap(
        matrix=fitz.Matrix(FIGURE_RENDER_SCALE, FIGURE_RENDER_SCALE),
        clip=safe_rect,
        alpha=False,
    )
    return pix.tobytes("png")


def _render_page_to_pil(page: Any, scale: float = FIGURE_RENDER_SCALE) -> Any:
    import fitz  # type: ignore
    from PIL import Image

    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


def _pil_crop_to_png_bytes(image: Any, bbox: tuple[int, int, int, int]) -> bytes:
    from io import BytesIO

    output = BytesIO()
    image.crop(bbox).save(output, format="PNG")
    return output.getvalue()


def _split_scanned_figure_regions(image: Any) -> List[tuple[int, int, int, int]]:
    width, height = image.size
    content_left = int(width * 0.05)
    content_right = int(width * 0.95)
    content_top = int(height * 0.095)
    content_bottom = int(height * 0.90)

    gray = image.convert("L")
    pixels = gray.load()
    row_threshold = max(5, int((content_right - content_left) * 0.002))
    active_rows: List[bool] = []
    for y in range(content_top, content_bottom):
        black_count = 0
        for x in range(content_left, content_right):
            if pixels[x, y] < 210:
                black_count += 1
        active_rows.append(black_count > row_threshold)

    active_rows = _dilate_bool_series(active_rows, radius=8)
    intervals = _active_intervals(active_rows, content_top, min_height=22)
    intervals = _merge_scanned_intervals(intervals)

    regions: List[tuple[int, int, int, int]] = []
    for y0, y1 in intervals:
        bbox = _black_pixel_bbox(gray, (content_left, max(content_top, y0 - 10), content_right, min(content_bottom, y1 + 10)))
        if not bbox:
            continue
        x0, by0, x1, by1 = _pad_bbox(bbox, width, height, pad_x=int(width * 0.025), pad_y=int(height * 0.018))
        if (x1 - x0) < width * 0.12 or (by1 - by0) < height * 0.04:
            continue
        if (x1 - x0) * (by1 - by0) < width * height * 0.008:
            continue
        regions.append((x0, by0, x1, by1))

    return regions


def _dilate_bool_series(values: List[bool], radius: int) -> List[bool]:
    result: List[bool] = []
    for index in range(len(values)):
        result.append(any(values[max(0, index - radius):min(len(values), index + radius + 1)]))
    return result


def _active_intervals(values: List[bool], y_offset: int, min_height: int) -> List[tuple[int, int]]:
    intervals: List[tuple[int, int]] = []
    start: Optional[int] = None
    for index, active in enumerate(values):
        y = y_offset + index
        if active and start is None:
            start = y
        elif not active and start is not None:
            if y - start >= min_height:
                intervals.append((start, y))
            start = None
    if start is not None and y_offset + len(values) - start >= min_height:
        intervals.append((start, y_offset + len(values)))
    return intervals


def _merge_scanned_intervals(intervals: List[tuple[int, int]]) -> List[tuple[int, int]]:
    merged: List[list[int]] = []
    for y0, y1 in intervals:
        height = y1 - y0
        if merged:
            gap = y0 - merged[-1][1]
            previous_height = merged[-1][1] - merged[-1][0]
            # 图号标签常形成一个很短的独立区间，应并入其上方图形。
            if gap < 35 or (height < 95 and previous_height > 120 and gap < 115):
                merged[-1][1] = y1
                continue
        if height < 45:
            continue
        merged.append([y0, y1])
    return [(item[0], item[1]) for item in merged if item[1] - item[0] >= 80]


def _black_pixel_bbox(image: Any, region: tuple[int, int, int, int]) -> Optional[tuple[int, int, int, int]]:
    gray = image if image.mode == "L" else image.convert("L")
    pixels = gray.load()
    x0, y0, x1, y1 = region
    min_x, min_y = x1, y1
    max_x, max_y = x0, y0
    found = False
    for y in range(max(0, y0), min(gray.size[1], y1)):
        for x in range(max(0, x0), min(gray.size[0], x1)):
            if pixels[x, y] < 210:
                found = True
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if not found:
        return None
    return min_x, min_y, max_x + 1, max_y + 1


def _pad_bbox(
    bbox: tuple[int, int, int, int],
    width: int,
    height: int,
    pad_x: int,
    pad_y: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    return (
        max(0, x0 - pad_x),
        max(0, y0 - pad_y),
        min(width, x1 + pad_x),
        min(height, y1 + pad_y),
    )


def _safe_figure_id(figure_id: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", figure_id)


def _next_numeric_figure_index(used_ids: Set[str]) -> int:
    nums = []
    for figure_id in used_ids:
        match = re.fullmatch(r"图(\d+)[A-Z]?", figure_id)
        if match:
            nums.append(int(match.group(1)))
    return (max(nums) + 1) if nums else 1


def _extract_rendered_figure_pages_from_pdf(
    doc: Any,
    task_id: str,
    figure_descriptions: Dict[str, str],
    existing_figure_count: int,
    existing_figure_ids: Set[str],
    errors: List[ParseError],
    candidate_pages: Optional[List[int]] = None,
) -> List[PatentFigure]:
    """整页渲染疑似附图页，覆盖矢量图/扫描图场景。"""
    figures: List[PatentFigure] = []
    rendered_hashes: Set[str] = set()
    next_index = existing_figure_count + 1
    import fitz  # type: ignore  # PyMuPDF

    expected_ids = list(figure_descriptions.keys())
    remaining_ids = [figure_id for figure_id in expected_ids if figure_id not in existing_figure_ids]

    try:
        for page_num in (candidate_pages or list(range(len(doc)))):
            page = doc[page_num]
            page_text = page.get_text("text") or ""
            image_count = len(page.get_images(full=True))
            drawing_count = len(page.get_drawings())
            figure_labels = _extract_figure_labels(page_text)

            if not _looks_like_figure_page(
                page_text=page_text,
                figure_labels=figure_labels,
                image_count=image_count,
                drawing_count=drawing_count,
            ):
                continue

            # 用固定缩放重新渲染，避免 `page.get_images()` 无法提取矢量附图。
            pix = page.get_pixmap(
                matrix=fitz.Matrix(RENDER_FALLBACK_DPI_SCALE, RENDER_FALLBACK_DPI_SCALE),
                alpha=False,
            )
            image_bytes = pix.tobytes("png")
            image_hash = hashlib.md5(image_bytes).hexdigest()
            if image_hash in rendered_hashes:
                continue
            rendered_hashes.add(image_hash)

            figure_id, description = _resolve_rendered_figure_identity(
                figure_labels=figure_labels,
                figure_descriptions=figure_descriptions,
                remaining_ids=remaining_ids,
                existing_figure_ids=existing_figure_ids,
                next_index=next_index,
            )
            existing_figure_ids.add(figure_id)
            if figure_id in remaining_ids:
                remaining_ids.remove(figure_id)
            next_index += 1

            figures.append(
                _persist_figure_file(
                    task_id=task_id,
                    file_name=f"patent_figure_page_{page_num + 1}.png",
                    image_bytes=image_bytes,
                    mime_type="image/png",
                    figure_id=figure_id,
                    figure_description=description,
                )
            )
            logger.info(
                f"通过整页渲染提取附图页成功: page={page_num + 1}, "
                f"figure_id={figure_id}, drawings={drawing_count}, labels={figure_labels}"
            )

    except Exception as e:
        logger.warning(f"整页渲染附图回退失败: {str(e)}")
        errors.append(ParseError(
            error_type="PDF_PAGE_RENDER_FALLBACK_ERROR",
            error_message=f"整页渲染附图回退失败: {str(e)}",
            is_recoverable=True,
        ))

    return figures


def _extract_figure_labels(page_text: str) -> List[str]:
    labels = re.findall(r'图\s*(\d+\s*[A-Za-z]?)', page_text or "")
    ordered: List[str] = []
    for label in labels:
        figure_id = f"图{re.sub(r'\s+', '', label).upper()}"
        if figure_id not in ordered:
            ordered.append(figure_id)
    return ordered


def _looks_like_figure_page(
    page_text: str,
    figure_labels: List[str],
    image_count: int,
    drawing_count: int,
    after_figure_anchor: bool = False,
) -> bool:
    normalized_text = re.sub(r"\s+", "", page_text or "")
    text_length = len(normalized_text)

    if "说明书附图" in normalized_text:
        return True
    if figure_labels and text_length <= 120:
        return True
    if after_figure_anchor and text_length <= 120:
        return True
    if after_figure_anchor and drawing_count >= 10 and text_length <= 600:
        return True
    if drawing_count >= 20 and text_length <= 400:
        return True
    if image_count > 0 and drawing_count > 0 and text_length <= 200:
        return True
    return False


def _find_candidate_figure_pages(doc: Any, figure_descriptions: Dict[str, str]) -> List[int]:
    anchor_page = _find_figure_anchor_page(doc)
    explicit_figure_pages: List[int] = []
    pages: List[int] = []
    scanned_textless_pages: List[int] = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_text = page.get_text("text") or ""
        normalized_text = re.sub(r"\s+", "", page_text or "")
        if "说明书附图" in normalized_text:
            explicit_figure_pages.append(page_num)
            continue
        if "附图说明" in normalized_text:
            continue

        after_anchor = anchor_page is not None and page_num > anchor_page
        if anchor_page is not None and not after_anchor:
            continue
        figure_labels = _extract_figure_labels(page_text)
        image_count = len(page.get_images(full=True))
        drawing_count = len(page.get_drawings())

        if _looks_like_figure_page(
            page_text=page_text,
            figure_labels=figure_labels,
            image_count=image_count,
            drawing_count=drawing_count,
            after_figure_anchor=after_anchor,
        ):
            if anchor_page is None or page_num >= anchor_page:
                pages.append(page_num)

        if not normalized_text and _is_scanned_full_page(page):
            scanned_textless_pages.append(page_num)

    if explicit_figure_pages:
        return explicit_figure_pages

    scanned_figure_pages = _find_scanned_figure_pages_by_ocr(doc, scanned_textless_pages)
    if scanned_figure_pages:
        first_figure_page = min(scanned_figure_pages)
        return [
            page_num
            for page_num in range(first_figure_page, len(doc))
            if _is_scanned_full_page(doc[page_num])
        ]
    trailing_image_pages = _find_trailing_image_only_pages(doc)
    if figure_descriptions and len(trailing_image_pages) >= len(figure_descriptions):
        return trailing_image_pages[-len(figure_descriptions):]
    if pages:
        return pages

    if anchor_page is not None:
        return list(range(anchor_page + 1, len(doc)))

    if figure_descriptions:
        return list(range(max(0, len(doc) - len(figure_descriptions)), len(doc)))

    return []


def _find_scanned_figure_pages_by_ocr(doc: Any, scanned_textless_pages: List[int]) -> List[int]:
    figure_pages: List[int] = []
    if not scanned_textless_pages:
        return figure_pages

    scanned_page_set = set(scanned_textless_pages)
    misses_after_hit = 0
    for page_num in reversed(scanned_textless_pages):
        # 扫描版专利附图一般是连续尾页；从尾页向前找，命中后遇到正文页即可停止。
        if page_num < len(doc) * 0.35 and not figure_pages:
            break

        ocr_text = _ocr_page_text_for_figure_detection(doc[page_num])
        normalized_ocr = re.sub(r"\s+", "", ocr_text or "")
        is_figure_page = (
            "说明书附图" in normalized_ocr
            or "说明书附" in normalized_ocr
            or (
                page_num > len(doc) * 0.45
                and "附图" in normalized_ocr
                and re.search(r"\d+/\d+页?", normalized_ocr) is not None
            )
        )
        if is_figure_page:
            figure_pages.append(page_num)
            misses_after_hit = 0
            continue

        if figure_pages:
            misses_after_hit += 1
            if misses_after_hit >= 8:
                break

    if not figure_pages:
        return []

    first_figure_page = min(figure_pages)
    return [page_num for page_num in range(first_figure_page, len(doc)) if page_num in scanned_page_set]


def _ocr_page_text_for_figure_detection(page: Any) -> str:
    tesseract_bin = shutil.which("tesseract")
    if not tesseract_bin:
        return ""

    import fitz  # type: ignore

    img_path = ""
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as img_tmp:
            img_path = img_tmp.name
            pix.save(img_path)
        proc = subprocess.run(
            [tesseract_bin, img_path, "stdout", "-l", "chi_sim+eng"],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        return (proc.stdout or "").strip()
    except Exception as exc:
        logger.debug(f"扫描页附图检测OCR失败: {exc}")
        return ""
    finally:
        if img_path:
            try:
                os.unlink(img_path)
            except OSError:
                pass


def _find_figure_anchor_page(doc: Any) -> Optional[int]:
    for page_num in range(len(doc)):
        page_text = re.sub(r"\s+", "", doc[page_num].get_text("text") or "")
        if "附图说明" in page_text:
            return page_num
    return None


def _find_trailing_image_only_pages(doc: Any) -> List[int]:
    trailing_pages: List[int] = []

    for page_num in range(len(doc) - 1, -1, -1):
        page = doc[page_num]
        page_text = re.sub(r"\s+", "", page.get_text("text") or "")
        image_count = len(page.get_images(full=True))
        drawing_count = len(page.get_drawings())

        # 连续尾页只保留“几乎无文本 + 含图形内容”的页。
        if len(page_text) <= 40 and (image_count > 0 or drawing_count > 0):
            trailing_pages.append(page_num)
            continue

        if trailing_pages:
            break

    return list(reversed(trailing_pages))


def _resolve_rendered_figure_identity(
    figure_labels: List[str],
    figure_descriptions: Dict[str, str],
    remaining_ids: List[str],
    existing_figure_ids: Set[str],
    next_index: int,
) -> tuple[str, str]:
    candidate_ids = [
        figure_id for figure_id in figure_labels
        if figure_id not in existing_figure_ids
    ]
    if candidate_ids:
        figure_id = candidate_ids[0]
        description = figure_descriptions.get(figure_id, "")
        if not description and len(candidate_ids) > 1:
            description = "；".join(
                figure_descriptions.get(candidate_id, "")
                for candidate_id in candidate_ids
                if figure_descriptions.get(candidate_id, "")
            )
        return figure_id, description

    if remaining_ids:
        figure_id = remaining_ids[0]
        return figure_id, figure_descriptions.get(figure_id, "")

    figure_id = f"图{next_index}"
    return figure_id, figure_descriptions.get(figure_id, "")


def _read_binary_from_path_or_url(file_url: str) -> bytes:
    if file_url.startswith("http://") or file_url.startswith("https://"):
        import requests

        response = requests.get(file_url, timeout=60)
        response.raise_for_status()
        return response.content

    with open(file_url, "rb") as file:
        return file.read()


def _save_single_image(
    patent_file: File,
    task_id: str,
    specification_sections: List[SpecificationSection],
    errors: List[ParseError]
) -> List[PatentFigure]:
    """保存单个图片文件"""
    figures: List[PatentFigure] = []
    
    try:
        file_url = patent_file.url
        if not file_url:
            return figures

        file_content = _read_binary_from_path_or_url(file_url)
        file_name = os.path.basename(file_url.split("?")[0]) or "figure.png"
        lower_name = file_name.lower()
        if lower_name.endswith(".jpg") or lower_name.endswith(".jpeg"):
            content_type = "image/jpeg"
        elif lower_name.endswith(".gif"):
            content_type = "image/gif"
        elif lower_name.endswith(".bmp"):
            content_type = "image/bmp"
        else:
            content_type = "image/png"

        figure_descriptions = _extract_figure_descriptions(specification_sections)

        figures.append(
            _persist_figure_file(
                task_id=task_id,
                file_name=file_name,
                image_bytes=file_content,
                mime_type=content_type,
                figure_id="图1",
                figure_description=figure_descriptions.get("图1", ""),
            )
        )

        logger.info("成功保存单个图片文件")
        
    except Exception as e:
        logger.error(f"图片保存失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="IMAGE_UPLOAD_ERROR",
            error_message=f"图片保存失败: {str(e)}",
            is_recoverable=True
        ))
    
    return figures


def _extract_figures_from_html(
    patent_file: File,
    task_id: str,
    specification_sections: List[SpecificationSection],
    errors: List[ParseError]
) -> List[PatentFigure]:
    """从HTML中提取图片URL"""
    figures: List[PatentFigure] = []
    
    try:
        from utils.file.file import FileOps
        content = FileOps.extract_text(patent_file)
        figure_descriptions = _extract_figure_descriptions(specification_sections)
        
        img_patterns = [
            r'<img[^>]+src=["\']([^"\']+)["\']',
            r'https?://[^\s<>"\']+\.(?:png|jpg|jpeg|gif|bmp)',
        ]
        
        figure_count = 0
        for pattern in img_patterns:
            for img_url in re.findall(pattern, content, re.IGNORECASE):
                if not img_url.startswith("http"):
                    continue
                
                figure_count += 1
                figure_id = f"图{figure_count}"
                
                try:
                    image_bytes = _read_binary_from_path_or_url(img_url)
                    file_name = os.path.basename(img_url.split("?")[0]) or f"{figure_id}.png"
                    lower_name = file_name.lower()
                    mime_type = "image/png"
                    if lower_name.endswith(".jpg") or lower_name.endswith(".jpeg"):
                        mime_type = "image/jpeg"
                    elif lower_name.endswith(".gif"):
                        mime_type = "image/gif"
                    elif lower_name.endswith(".bmp"):
                        mime_type = "image/bmp"

                    figures.append(
                        _persist_figure_file(
                            task_id=task_id,
                            file_name=file_name,
                            image_bytes=image_bytes,
                            mime_type=mime_type,
                            figure_id=figure_id,
                            figure_description=figure_descriptions.get(figure_id, ""),
                        )
                    )
                    
                    logger.info(f"成功转存HTML中的图片: {figure_id}")
                    
                except Exception as e:
                    logger.warning(f"转存图片失败 {img_url}: {str(e)}")
        
    except Exception as e:
        logger.error(f"HTML图片提取失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="HTML_IMAGE_EXTRACT_ERROR",
            error_message=f"从HTML提取图片失败: {str(e)}",
            is_recoverable=True
        ))
    
    return figures


def _extract_figures_from_text(
    patent_file: File,
    task_id: str,
    specification_sections: List[SpecificationSection],
    errors: List[ParseError]
) -> List[PatentFigure]:
    """从TXT中查找图片URL"""
    figures: List[PatentFigure] = []
    
    try:
        from utils.file.file import FileOps
        content = FileOps.extract_text(patent_file)
        figure_descriptions = _extract_figure_descriptions(specification_sections)
        
        pattern = r'https?://[^\s<>"\']+\.(?:png|jpg|jpeg|gif|bmp)'
        
        figure_count = 0
        for img_url in re.findall(pattern, content, re.IGNORECASE):
            figure_count += 1
            figure_id = f"图{figure_count}"
            
            try:
                image_bytes = _read_binary_from_path_or_url(img_url)
                file_name = os.path.basename(img_url.split("?")[0]) or f"{figure_id}.png"
                lower_name = file_name.lower()
                mime_type = "image/png"
                if lower_name.endswith(".jpg") or lower_name.endswith(".jpeg"):
                    mime_type = "image/jpeg"
                elif lower_name.endswith(".gif"):
                    mime_type = "image/gif"
                elif lower_name.endswith(".bmp"):
                    mime_type = "image/bmp"

                figures.append(
                    _persist_figure_file(
                        task_id=task_id,
                        file_name=file_name,
                        image_bytes=image_bytes,
                        mime_type=mime_type,
                        figure_id=figure_id,
                        figure_description=figure_descriptions.get(figure_id, ""),
                    )
                )
                
                logger.info(f"成功转存TXT中的图片URL: {figure_id}")
                
            except Exception as e:
                logger.warning(f"转存图片失败 {img_url}: {str(e)}")
        
    except Exception as e:
        logger.error(f"TXT图片提取失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="TEXT_IMAGE_EXTRACT_ERROR",
            error_message=f"从TXT提取图片失败: {str(e)}",
            is_recoverable=True
        ))
    
    return figures


def _extract_figure_descriptions(
    specification_sections: List[SpecificationSection]
) -> Dict[str, str]:
    """从说明书提取附图说明"""
    descriptions: Dict[str, str] = {}
    
    for section in specification_sections:
        if "附图说明" in section.section_name:
            normalized_text = section.section_text
            normalized_text = normalized_text.replace("”", "").replace("\"", "")
            normalized_text = normalized_text.replace("图]", "图1")
            normalized_text = normalized_text.replace("图l", "图1").replace("图I", "图1")

            pattern = r'((?:图\s*\d+\s*(?:和|及|以及|、|,|，)?\s*)+)\s*[是为：:]\s*([^\n]+)'
            for figure_group, description in re.findall(pattern, normalized_text):
                clean_description = description.strip().strip("，,；;。")
                if not clean_description:
                    continue

                figure_ids = [
                    re.sub(r"\s+", "", figure_id)
                    for figure_id in re.findall(r'图\s*\d+', figure_group)
                ]
                for clean_figure_id in figure_ids:
                    if clean_figure_id:
                        descriptions[clean_figure_id] = clean_description
    
    return descriptions
