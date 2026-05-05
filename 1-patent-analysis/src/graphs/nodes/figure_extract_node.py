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
from typing import List, Optional, Dict, Any
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
        
        # 遍历每一页提取图片
        figure_count = 0
        total_images_found = 0
        filtered_images = 0
        
        for page_num in range(len(doc)):
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
                    
                    # 过滤小图片（小于5KB）
                    if len(image_bytes) < 5000:
                        filtered_images += 1
                        logger.debug(f"跳过小图片: {len(image_bytes)} bytes < 5000 bytes")
                        continue
                    
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
            pattern = r'(图\d+)[是为：:]\s*([^\n图]+)'
            for figure_id, description in re.findall(pattern, section.section_text):
                descriptions[figure_id] = description.strip()
    
    return descriptions
