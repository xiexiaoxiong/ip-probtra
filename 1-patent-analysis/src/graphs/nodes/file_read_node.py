"""
文件读取节点
职责：从PDF/TXT/HTML格式的专利文档中提取原始文本
"""
import os
import logging
import tempfile
from typing import Optional
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context

from graphs.state import FileReadInput, FileReadOutput, ParseError
from utils.file.file import File, FileOps

logger = logging.getLogger(__name__)


def _extract_pdf_text_with_pymupdf(file_url: str) -> str:
    """使用PyMuPDF从PDF文件中提取文本
    
    Args:
        file_url: PDF文件的URL或本地路径
        
    Returns:
        提取的文本内容
    """
    import fitz
    import requests
    
    text_parts: list = []
    
    # 判断是URL还是本地路径
    if file_url.startswith("http://") or file_url.startswith("https://"):
        # 下载PDF到临时文件
        resp = requests.get(file_url, timeout=60)
        resp.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path: str = tmp.name
    else:
        tmp_path = file_url
    
    try:
        doc = fitz.open(tmp_path)
        for page in doc:
            page_text = page.get_text()
            if page_text:
                text_parts.append(page_text)
        doc.close()
    finally:
        # 清理临时文件
        if file_url.startswith("http://") or file_url.startswith("https://"):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    
    return "\n".join(text_parts)


def file_read_node(
    state: FileReadInput, config: RunnableConfig, runtime: Runtime[Context]
) -> FileReadOutput:
    """
    title: 专利文档读取
    desc: 从PDF/TXT/HTML格式的专利文档中提取原始文本内容
    """
    ctx = runtime.context
    
    patent_file: File = state.patent_file
    file_format: str = ""
    raw_text: str = ""
    read_error: Optional[ParseError] = None
    
    try:
        # 判断文件格式 - 先移除URL查询参数再判断扩展名
        file_url: str = patent_file.url or ""
        clean_url = file_url.split('?')[0]
        file_ext = os.path.splitext(clean_url)[1].lower()
        
        # 如果URL路径无扩展名，尝试从查询参数中提取（如 file_path=xxx.pdf）
        if not file_ext and '?' in file_url:
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(file_url)
            qs = parse_qs(parsed.query)
            # 检查常见的文件路径参数
            for param_name in ["file_path", "file", "name", "filename"]:
                if param_name in qs:
                    param_val: str = qs[param_name][0] if isinstance(qs[param_name], list) else qs[param_name]
                    file_ext = os.path.splitext(param_val)[1].lower()
                    if file_ext:
                        break
        
        # 如果仍未获取到扩展名，根据file_type推断
        if not file_ext:
            file_type: str = patent_file.file_type or ""
            if file_type == "document":
                file_ext = ".pdf"  # 文档类型默认尝试PDF
            else:
                file_ext = ".txt"  # 默认作为文本处理
        
        logger.info(f"文件格式检测: file_ext={file_ext}, url={file_url[:80]}...")
        
        # 根据格式选择读取方式
        if file_ext == ".pdf":
            file_format = "pdf"
            # PDF文档优先使用PyMuPDF直接提取文本（更可靠）
            try:
                raw_text = _extract_pdf_text_with_pymupdf(file_url)
                logger.info(f"PyMuPDF提取PDF文本成功，长度: {len(raw_text)}")
            except Exception as pymupdf_err:
                logger.warning(f"PyMuPDF提取PDF文本失败: {pymupdf_err}，尝试FileOps降级")
                raw_text = FileOps.extract_text(patent_file)
                if not raw_text or raw_text.startswith("[FileOps Error]"):
                    raise ValueError(f"PDF文本提取失败: {pymupdf_err}")
            
            if not raw_text or len(raw_text.strip()) < 50:
                raise ValueError("PDF文档内容过少或无法解析文本")
                
        elif file_ext in [".txt"]:
            file_format = "txt"
            raw_text = FileOps.extract_text(patent_file)
            if not raw_text or raw_text.startswith("[FileOps Error]"):
                raise ValueError("TXT文件读取失败")
            
        elif file_ext in [".html", ".htm"]:
            file_format = "html"
            raw_text = FileOps.extract_text(patent_file)
            if not raw_text or raw_text.startswith("[FileOps Error]"):
                raise ValueError("HTML文件读取失败")
            
        else:
            # 尝试作为文档读取
            file_format = "unknown"
            raw_text = FileOps.extract_text(patent_file)
            if not raw_text or raw_text.startswith("[FileOps Error]"):
                raise ValueError("文件读取失败")
            
        # 验证文本内容
        if not raw_text or len(raw_text.strip()) < 100:
            read_error = ParseError(
                error_type="EMPTY_OR_CORRUPT_FILE",
                error_message=f"文件内容为空或过短，长度: {len(raw_text.strip())}",
                is_recoverable=False
            )
        else:
            logger.info(f"成功读取专利文档，格式: {file_format}, 文本长度: {len(raw_text)}")
            
    except Exception as e:
        logger.error(f"文件读取失败: {str(e)}", exc_info=True)
        read_error = ParseError(
            error_type="FILE_READ_ERROR",
            error_message=f"无法读取文件: {str(e)}",
            is_recoverable=False
        )
        raw_text = ""
    
    return FileReadOutput(
        raw_text=raw_text,
        file_format=file_format,
        read_error=read_error
    )
