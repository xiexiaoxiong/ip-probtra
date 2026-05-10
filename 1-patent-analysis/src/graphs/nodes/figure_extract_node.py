"""
附图提取节点
职责：从专利文档中提取附图并上传到对象存储
支持：PyMuPDF提取（PDF）、多模态模型识别、URL转存
"""
import os
import json
import logging
import re
import tempfile
import hashlib
from typing import List, Optional, Dict, Any, Set
from langchain_core.runnables import RunnableConfig
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from coze_coding_dev_sdk import LLMClient
from coze_coding_dev_sdk.s3 import S3SyncStorage

from graphs.state import (
    FigureExtractInput,
    FigureExtractOutput,
    PatentFigure,
    SpecificationSection,
    ParseError
)
from utils.file.file import File

logger = logging.getLogger(__name__)
MIN_EMBEDDED_IMAGE_BYTES = 5000
RENDER_FALLBACK_DPI_SCALE = 2.0


def figure_extract_node(
    state: FigureExtractInput, config: RunnableConfig, runtime: Runtime[Context]
) -> FigureExtractOutput:
    """
    title: 专利附图提取
    desc: 从专利文档中提取附图，上传对象存储并返回URL列表
    integrations: 对象存储
    """
    ctx = runtime.context
    
    patent_file: File = state.patent_file
    specification_sections: List[SpecificationSection] = state.specification_sections
    figures_list: List[PatentFigure] = []
    figure_errors: List[ParseError] = []
    
    try:
        # 初始化对象存储客户端
        storage = S3SyncStorage(
            endpoint_url=os.getenv("COZE_BUCKET_ENDPOINT_URL"),
            access_key="",
            secret_key="",
            bucket_name=os.getenv("COZE_BUCKET_NAME"),
            region="cn-beijing",
        )
        
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
            # 优先使用 PyMuPDF 提取
            figures_list = _extract_figures_from_pdf(
                patent_file, specification_sections, storage, figure_errors
            )
            # 如果提取失败，尝试多模态模型
            if not figures_list:
                logger.info("PyMuPDF提取失败，尝试使用多模态模型")
                figures_list = _extract_figures_with_vision(
                    patent_file, specification_sections, storage, ctx, figure_errors
                )
        elif file_ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp"]:
            # 单个图片文件，直接上传
            figures_list = _upload_single_image(
                patent_file, specification_sections, storage, figure_errors
            )
        elif file_ext in [".html", ".htm"]:
            # HTML 文件，提取图片 URL
            figures_list = _extract_figures_from_html(
                patent_file, specification_sections, storage, figure_errors
            )
        elif file_ext == ".txt":
            # TXT 文件，检查是否有图片 URL
            figures_list = _extract_figures_from_text(
                patent_file, specification_sections, storage, figure_errors
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
    specification_sections: List[SpecificationSection],
    storage: S3SyncStorage,
    errors: List[ParseError]
) -> List[PatentFigure]:
    """从PDF中提取图片（使用PyMuPDF）"""
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
        seen_hashes: Set[str] = set()
        figure_count = 0
        total_images_found = 0
        filtered_images = 0

        # 优先整页提取说明书附图页，避免把整本 PDF 的内嵌图片都当成附图。
        rendered_figures = _extract_rendered_figure_pages_from_pdf(
            doc=doc,
            storage=storage,
            figure_descriptions=figure_descriptions,
            existing_figure_count=figure_count,
            existing_figure_ids=set(),
            errors=errors,
            candidate_pages=candidate_pages,
        )
        if rendered_figures:
            figures.extend(rendered_figures)
            figure_count = len(figures)
        
        # 若未识别到整页附图，再在候选附图页中尝试提取嵌入图片。
        pages_to_scan = candidate_pages or list(range(len(doc)))
        for page_num in pages_to_scan:
            page = doc[page_num]
            image_list = page.get_images(full=True)
            total_images_found += len(image_list)
            
            if image_list:
                logger.info(f"第 {page_num + 1} 页发现 {len(image_list)} 张图片")
            
            for img_info in image_list:
                try:
                    # 获取图片数据
                    xref = img_info[0]
                    base_image = doc.extract_image(xref)
                    image_bytes = base_image["image"]
                    image_ext = base_image.get("ext", "png")
                    
                    image_hash = hashlib.md5(image_bytes).hexdigest()

                    # 过滤重复图片和明显无效的小图标
                    if image_hash in seen_hashes:
                        logger.debug(f"跳过重复图片: xref={xref}")
                        continue
                    if len(image_bytes) < MIN_EMBEDDED_IMAGE_BYTES:
                        filtered_images += 1
                        logger.debug(
                            f"跳过小图片: {len(image_bytes)} bytes < {MIN_EMBEDDED_IMAGE_BYTES} bytes"
                        )
                        continue
                    seen_hashes.add(image_hash)
                    
                    figure_count += 1
                    figure_id = f"图{figure_count}"
                    
                    # 上传到对象存储
                    file_name = f"patent_figure_{figure_count}.{image_ext}"
                    content_type = f"image/{image_ext}" if image_ext in ["png", "jpg", "jpeg", "gif"] else "image/png"
                    
                    storage_key = storage.upload_file(
                        file_content=image_bytes,
                        file_name=file_name,
                        content_type=content_type
                    )
                    
                    # 生成访问URL
                    figure_url = storage.generate_presigned_url(
                        key=storage_key,
                        expire_time=86400 * 30  # 30天
                    )
                    
                    # 获取附图说明
                    description = figure_descriptions.get(figure_id, "")
                    
                    figures.append(PatentFigure(
                        figure_id=figure_id,
                        figure_url=figure_url,
                        figure_description=description,
                        storage_key=storage_key
                    ))
                    
                    logger.info(f"成功提取并上传附图: {figure_id}, 大小: {len(image_bytes)} bytes")
                    
                except Exception as e:
                    logger.warning(f"提取图片失败: {str(e)}")
                    continue

        should_try_page_render_fallback = (
            not figures
            or (figure_descriptions and len(figures) < len(figure_descriptions))
        )
        if should_try_page_render_fallback and not rendered_figures:
            rendered_figures = _extract_rendered_figure_pages_from_pdf(
                doc=doc,
                storage=storage,
                figure_descriptions=figure_descriptions,
                existing_figure_count=figure_count,
                existing_figure_ids={figure.figure_id for figure in figures},
                errors=errors,
                candidate_pages=candidate_pages,
            )
            if rendered_figures:
                figures.extend(rendered_figures)
        
        doc.close()
        
        # 清理临时文件
        if pdf_path.startswith("/tmp"):
            try:
                os.unlink(pdf_path)
            except Exception:
                pass
        
        logger.info(f"PDF图片提取完成: 共找到 {total_images_found} 张图片，过滤小图片 {filtered_images} 张，成功提取 {len(figures)} 张")
        
    except Exception as e:
        logger.error(f"PDF图片提取失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="PDF_IMAGE_EXTRACT_ERROR",
            error_message=f"从PDF提取图片失败: {str(e)}",
            is_recoverable=True
        ))
    
    return figures


def _extract_rendered_figure_pages_from_pdf(
    doc: Any,
    storage: S3SyncStorage,
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

            storage_key = storage.upload_file(
                file_content=image_bytes,
                file_name=f"patent_figure_page_{page_num + 1}.png",
                content_type="image/png",
            )
            figure_url = storage.generate_presigned_url(
                key=storage_key,
                expire_time=86400 * 30,
            )

            figures.append(PatentFigure(
                figure_id=figure_id,
                figure_url=figure_url,
                figure_description=description,
                storage_key=storage_key,
            ))
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
    labels = re.findall(r'图\s*(\d+)', page_text or "")
    ordered: List[str] = []
    for label in labels:
        figure_id = f"图{label}"
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

    if explicit_figure_pages:
        return explicit_figure_pages
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


def _extract_figures_with_vision(
    patent_file: File,
    specification_sections: List[SpecificationSection],
    storage: S3SyncStorage,
    ctx: Context,
    errors: List[ParseError]
) -> List[PatentFigure]:
    """使用多模态模型识别图片（辅助方法）"""
    figures: List[PatentFigure] = []
    
    try:
        client = LLMClient(ctx=ctx)
        
        file_url = patent_file.url
        if not file_url:
            return figures
        
        # 如果是本地文件，先上传
        if not file_url.startswith("http"):
            storage_key = storage.upload_from_url(file_url, timeout=30)
            file_url = storage.generate_presigned_url(storage_key, expire_time=3600)
        
        system_prompt = """你是一个专利文档分析专家。请识别文档中的所有图片。

输出格式（JSON）：
{
  "figures": [
    {
      "figure_id": "图1",
      "description": "图片内容描述"
    }
  ]
}"""

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=[
                {"type": "text", "text": "请识别文档中的图片"},
                {"type": "file_url", "file_url": {"url": file_url}}
            ])
        ]
        
        response = client.invoke(
            messages=messages,
            model="doubao-seed-1-6-vision-250815",
            temperature=0.1,
            max_completion_tokens=4096
        )
        
        response_text = ""
        if isinstance(response.content, str):
            response_text = response.content
        elif isinstance(response.content, list):
            for item in response.content:
                if isinstance(item, dict) and item.get("type") == "text":
                    response_text += item.get("text", "")
        
        # 解析结果
        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            figures_data = json.loads(json_match.group())
            figure_descriptions = _extract_figure_descriptions(specification_sections)
            
            for idx, fig_data in enumerate(figures_data.get("figures", [])):
                figure_id = fig_data.get("figure_id", f"图{idx + 1}")
                description = fig_data.get("description", "")
                
                if figure_id in figure_descriptions:
                    description = figure_descriptions[figure_id]
                
                figures.append(PatentFigure(
                    figure_id=figure_id,
                    figure_url="",
                    figure_description=description,
                    storage_key=None
                ))
        
    except Exception as e:
        logger.error(f"多模态模型识别失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="VISION_MODEL_ERROR",
            error_message=f"多模态模型识别失败: {str(e)}",
            is_recoverable=True
        ))
    
    return figures


def _upload_single_image(
    patent_file: File,
    specification_sections: List[SpecificationSection],
    storage: S3SyncStorage,
    errors: List[ParseError]
) -> List[PatentFigure]:
    """上传单个图片文件"""
    figures: List[PatentFigure] = []
    
    try:
        file_url = patent_file.url
        if not file_url:
            return figures
        
        if file_url.startswith("http"):
            storage_key = storage.upload_from_url(file_url, timeout=30)
        else:
            with open(file_url, "rb") as f:
                file_content = f.read()
            
            file_name = os.path.basename(file_url)
            storage_key = storage.upload_file(
                file_content=file_content,
                file_name=file_name,
                content_type="image/png"
            )
        
        figure_url = storage.generate_presigned_url(storage_key, expire_time=86400 * 30)
        figure_descriptions = _extract_figure_descriptions(specification_sections)
        
        figures.append(PatentFigure(
            figure_id="图1",
            figure_url=figure_url,
            figure_description=figure_descriptions.get("图1", ""),
            storage_key=storage_key
        ))
        
        logger.info("成功上传单个图片文件")
        
    except Exception as e:
        logger.error(f"图片上传失败: {str(e)}", exc_info=True)
        errors.append(ParseError(
            error_type="IMAGE_UPLOAD_ERROR",
            error_message=f"图片上传失败: {str(e)}",
            is_recoverable=True
        ))
    
    return figures


def _extract_figures_from_html(
    patent_file: File,
    specification_sections: List[SpecificationSection],
    storage: S3SyncStorage,
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
                    storage_key = storage.upload_from_url(url=img_url, timeout=30)
                    figure_url = storage.generate_presigned_url(storage_key, expire_time=86400 * 30)
                    
                    figures.append(PatentFigure(
                        figure_id=figure_id,
                        figure_url=figure_url,
                        figure_description=figure_descriptions.get(figure_id, ""),
                        storage_key=storage_key
                    ))
                    
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
    specification_sections: List[SpecificationSection],
    storage: S3SyncStorage,
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
                storage_key = storage.upload_from_url(url=img_url, timeout=30)
                figure_url = storage.generate_presigned_url(storage_key, expire_time=86400 * 30)
                
                figures.append(PatentFigure(
                    figure_id=figure_id,
                    figure_url=figure_url,
                    figure_description=figure_descriptions.get(figure_id, ""),
                    storage_key=storage_key
                ))
                
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
