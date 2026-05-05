import os
import re
import json
import logging
from typing import Dict, Any, List, Tuple
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime
from coze_coding_utils.runtime_ctx.context import Context
from graphs.state import ParseAndFetchInput, ParseAndFetchOutput
from tools.feishu_bitable import FeishuBitable

logger = logging.getLogger(__name__)

# ===== 飞书多维表格字段类型常量 =====
FIELD_TYPE_TEXT = 1
FIELD_TYPE_NUMBER = 2
FIELD_TYPE_SINGLE_SELECT = 3
FIELD_TYPE_MULTI_SELECT = 4
FIELD_TYPE_DATE = 5
FIELD_TYPE_CHECKBOX = 7
FIELD_TYPE_PERSON = 11
FIELD_TYPE_PHONE = 13
FIELD_TYPE_URL = 14
FIELD_TYPE_AUTO_NUMBER = 15
FIELD_TYPE_ATTACHMENT = 17
FIELD_TYPE_LOCATION = 18
FIELD_TYPE_FORMULA = 19
FIELD_TYPE_RICH_TEXT = 1001

# ===== 表格角色识别关键词 =====
CLAIM_TABLE_KEYWORDS: List[str] = [
    "权利要求", "claim", "权利要求书"
]
SPEC_TABLE_KEYWORDS: List[str] = [
    "说明书", "specification", "详细说明", "发明内容"
]
SPEC_DRAWING_TABLE_KEYWORDS: List[str] = [
    "说明书附图", "附图", "drawing", "附图说明"
]
PRODUCT_TABLE_KEYWORDS: List[str] = [
    "产品", "商品", "product", "被诉", "侵权产品", "涉嫌侵权",
    "对比产品", "涉嫌产品", "目标产品", "被控", "涉嫌"
]

# ===== 字段识别关键词 =====
CLAIM_FIELD_KEYWORDS: List[str] = [
    "独立权利要求", "权利要求", "claim",
    "发明内容", "技术方案",
    "专利要求", "独立权利", "独立项"
]
NAME_FIELD_KEYWORDS: List[str] = [
    "product_name", "product name", "商品名", "产品名", "品名",
    "商品名称", "产品名称", "名称", "name", "标题", "商品"
]
DESC_FIELD_KEYWORDS: List[str] = [
    "description", "描述", "说明", "详情", "detail", "介绍"
]
IMAGE_FIELD_KEYWORDS: List[str] = [
    "image", "图片", "照片", "img", "pic", "photo", "主图", "展示图", "效果"
]
SPEC_FIELD_KEYWORDS: List[str] = [
    "说明书", "specification", "发明内容", "具体实施", "实施方式",
    "技术领域", "背景技术", "详细说明"
]
DRAWING_FIELD_KEYWORDS: List[str] = [
    "附图", "drawing", "图示", "图纸", "示意图"
]


# ============================================================
# 工具函数
# ============================================================

def _parse_feishu_url(url: str) -> Dict[str, str]:
    """解析飞书多维表格URL，提取app_token和table_id"""
    result: Dict[str, str] = {"app_token": "", "table_id": ""}

    base_match = re.search(r"/base/([A-Za-z0-9]+)", url)
    if base_match:
        result["app_token"] = base_match.group(1)

    table_path_match = re.search(r"/table/([A-Za-z0-9]+)", url)
    if table_path_match:
        result["table_id"] = table_path_match.group(1)

    if not result["table_id"]:
        table_param_match = re.search(r"[?&]table=([A-Za-z0-9]+)", url)
        if table_param_match:
            result["table_id"] = table_param_match.group(1)

    return result


def _extract_text_value(field_value: Any) -> str:
    """从飞书字段中提取文本值"""
    if field_value is None:
        return ""
    if isinstance(field_value, str):
        return field_value.strip()
    if isinstance(field_value, (int, float)):
        return str(field_value)
    if isinstance(field_value, bool):
        return str(field_value)
    if isinstance(field_value, list):
        parts: List[str] = []
        for item in field_value:
            if isinstance(item, str):
                parts.append(item.strip())
            elif isinstance(item, dict):
                text_val: str = str(item.get("text", ""))
                if not text_val:
                    text_val = str(item.get("content", ""))
                if not text_val:
                    text_val = str(item.get("name", ""))
                if text_val:
                    parts.append(text_val.strip())
        return " ".join(p for p in parts if p)
    if isinstance(field_value, dict):
        text_val = str(field_value.get("text", field_value.get("content", field_value.get("link", ""))))
        return text_val.strip()
    return str(field_value)


def _extract_image_urls(field_value: Any) -> List[str]:
    """从飞书字段中提取图片URL"""
    image_urls: List[str] = []
    if field_value is None:
        return image_urls

    if isinstance(field_value, list):
        for item in field_value:
            if isinstance(item, dict):
                tmp_url: str = str(item.get("tmp_url", ""))
                if tmp_url and tmp_url.startswith("http"):
                    image_urls.append(tmp_url)
                    continue
                url_val: str = str(item.get("url", ""))
                if url_val and url_val.startswith("http"):
                    image_urls.append(url_val)
                    continue
                link_val: str = str(item.get("link", ""))
                if link_val and link_val.startswith("http"):
                    image_urls.append(link_val)
                    continue
                text_val: str = str(item.get("text", ""))
                if text_val and text_val.startswith("http"):
                    image_urls.append(text_val)
                    continue
                file_token: str = str(item.get("file_token", ""))
                file_name: str = str(item.get("name", ""))
                mime_type: str = str(item.get("type", ""))
                if file_token:
                    is_image: bool = (
                        mime_type.startswith("image/") or
                        any(file_name.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"])
                    )
                    if is_image:
                        image_urls.append(f"[飞书图片: token={file_token}, name={file_name}]")
            elif isinstance(item, str) and item.startswith("http"):
                image_urls.append(item)
    elif isinstance(field_value, str):
        if field_value.startswith("http"):
            image_urls.append(field_value)
        elif field_value.startswith("[") or field_value.startswith("{"):
            try:
                parsed_val = json.loads(field_value)
                if isinstance(parsed_val, list):
                    for p_item in parsed_val:
                        if isinstance(p_item, dict):
                            p_url: str = str(p_item.get("tmp_url", p_item.get("url", p_item.get("link", p_item.get("text", "")))))
                            if p_url and p_url.startswith("http"):
                                image_urls.append(p_url)
                elif isinstance(parsed_val, dict):
                    p_url = str(parsed_val.get("tmp_url", parsed_val.get("url", parsed_val.get("link", parsed_val.get("text", "")))))
                    if p_url and p_url.startswith("http"):
                        image_urls.append(p_url)
            except (json.JSONDecodeError, TypeError):
                pass

    return image_urls


def _fetch_all_fields(bitable: FeishuBitable, app_token: str, table_id: str) -> List[Dict[str, Any]]:
    """获取飞书表格的所有字段定义（自动分页）"""
    fields_items: List[Dict[str, Any]] = []
    page_token: str | None = None
    while True:
        try:
            fields_resp: dict = bitable.list_fields(
                app_token=app_token, table_id=table_id,
                page_token=page_token, page_size=100
            )
            items: list = fields_resp.get("data", {}).get("items", [])
            fields_items.extend(items)
            has_more: bool = fields_resp.get("data", {}).get("has_more", False)
            next_pt: str = fields_resp.get("data", {}).get("page_token", "")
            if not has_more or not next_pt:
                break
            page_token = next_pt
        except Exception as e:
            logger.error(f"获取字段定义失败(app_token={app_token}, table_id={table_id}): {e}")
            break
    return fields_items


def _fetch_all_records(bitable: FeishuBitable, app_token: str, table_id: str) -> List[Dict[str, Any]]:
    """获取飞书表格的所有记录（自动分页）"""
    all_records: List[Dict[str, Any]] = []
    page_token: str | None = None
    while True:
        try:
            search_resp: dict = bitable.search_record(
                app_token=app_token, table_id=table_id,
                page_token=page_token, page_size=500
            )
            items: list = search_resp.get("data", {}).get("items", [])
            all_records.extend(items)
            has_more: bool = search_resp.get("data", {}).get("has_more", False)
            next_pt: str = search_resp.get("data", {}).get("page_token", "")
            if not has_more or not next_pt:
                break
            page_token = next_pt
        except Exception as e:
            logger.error(f"获取记录失败(app_token={app_token}, table_id={table_id}): {e}")
            break
    return all_records


# ============================================================
# 表格角色识别
# ============================================================

def _identify_table_role(table_name: str, fields_items: List[Dict[str, Any]]) -> str:
    """
    识别飞书表格的角色，返回以下之一：
    - "claim": 权利要求表
    - "specification": 说明书表
    - "spec_drawing": 说明书附图表
    - "product": 产品表
    - "unknown": 无法识别
    """
    name_lower: str = table_name.lower() if table_name else ""
    field_names_lower: List[str] = [str(f.get("field_name", "")).lower() for f in fields_items]

    # 1. 表名匹配（按优先级：附图 > 说明书 > 权利要求 > 产品）
    for kw in SPEC_DRAWING_TABLE_KEYWORDS:
        if kw in name_lower:
            return "spec_drawing"

    for kw in SPEC_TABLE_KEYWORDS:
        if kw in name_lower:
            return "specification"

    for kw in CLAIM_TABLE_KEYWORDS:
        if kw in name_lower:
            return "claim"

    for kw in PRODUCT_TABLE_KEYWORDS:
        if kw in name_lower:
            return "product"

    # 2. 字段名匹配
    has_spec_fields: bool = any(
        kw in fn for fn in field_names_lower for kw in SPEC_FIELD_KEYWORDS
    )
    has_drawing_fields: bool = any(
        kw in fn for fn in field_names_lower for kw in DRAWING_FIELD_KEYWORDS
    )
    has_claim_fields: bool = any(
        kw in fn for fn in field_names_lower for kw in CLAIM_FIELD_KEYWORDS
    )
    has_product_name: bool = any(
        kw in fn for fn in field_names_lower for kw in NAME_FIELD_KEYWORDS
    )
    has_image: bool = any(
        kw in fn for fn in field_names_lower for kw in IMAGE_FIELD_KEYWORDS
    )

    # 附图：有附图字段且不是产品表特征
    if has_drawing_fields and not has_product_name:
        return "spec_drawing"

    # 说明书：有说明书特征字段
    if has_spec_fields:
        return "specification"

    # 权利要求
    if has_claim_fields:
        return "claim"

    # 产品表
    if has_product_name and (has_image or any(kw in fn for fn in field_names_lower for kw in DESC_FIELD_KEYWORDS)):
        return "product"

    # 多图片字段 → 产品表
    image_field_count: int = sum(1 for fn in field_names_lower if any(kw in fn for kw in IMAGE_FIELD_KEYWORDS))
    if image_field_count >= 2:
        return "product"

    # 专利表特征
    patent_fields: List[str] = ["专利号", "专利权人", "专利标题", "发明内容", "技术领域", "背景技术", "申请日期"]
    patent_match_count: int = sum(1 for pf in patent_fields if any(pf in fn for fn in field_names_lower))
    if patent_match_count >= 3:
        return "claim"

    return "unknown"


# ============================================================
# 数据提取函数
# ============================================================

def _extract_all_claims_text(records: List[Dict[str, Any]], fields_items: List[Dict[str, Any]]) -> str:
    """从权利要求表中提取所有权利要求的原始文本（整段文本）"""
    # 先尝试结构化提取
    structured_text: str = _extract_claims_from_structured(records, fields_items)
    if structured_text:
        return structured_text

    # 1. 尝试按关键词匹配字段
    claim_field_name: str = ""
    for keyword in CLAIM_FIELD_KEYWORDS:
        for field in fields_items:
            fn: str = str(field.get("field_name", ""))
            if keyword in fn.lower():
                claim_field_name = fn
                break
        if claim_field_name:
            break

    # 2. 从匹配字段提取文本
    if claim_field_name:
        all_texts: List[str] = []
        for record in records:
            fields_data: dict = record.get("fields", {})
            if claim_field_name in fields_data:
                text_val: str = _extract_text_value(fields_data[claim_field_name])
                if text_val:
                    all_texts.append(text_val)
        if all_texts:
            return "\n\n".join(all_texts)

    # 3. 兜底：遍历所有字段查找权利要求文本
    for record in records:
        fields_data = record.get("fields", {})
        for field_name, field_value in fields_data.items():
            text_val = _extract_text_value(field_value)
            if "其特征在于" in text_val or "权利要求" in text_val:
                return text_val

    return ""


def _extract_claims_from_structured(records: List[Dict[str, Any]], fields_items: List[Dict[str, Any]]) -> str:
    """
    从结构化的权利要求表中提取权利要求文本。
    结构化表格通常有以下字段：权利要求编号、类型、原文、父权利要求。
    每条记录对应一个权利要求。
    """
    # 构建字段名映射
    field_name_map: Dict[str, str] = {}
    for field in fields_items:
        fn: str = str(field.get("field_name", "")).lower()
        if "权利要求编号" in fn or "编号" in fn or fn == "claim_number" or fn == "claim no":
            field_name_map["claim_number"] = str(field.get("field_name", ""))
        elif fn == "类型" or fn == "type" or "权利要求类型" in fn:
            field_name_map["type"] = str(field.get("field_name", ""))
        elif fn == "原文" or fn == "original_text" or fn == "text" or "权利要求" in fn and "原文" in fn:
            field_name_map["original_text"] = str(field.get("field_name", ""))
        elif "父权利要求" in fn or "parent" in fn or "引用" in fn:
            field_name_map["parent_claim"] = str(field.get("field_name", ""))

    # 如果找到"原文"字段，说明是结构化表格
    if "original_text" not in field_name_map:
        return ""

    claim_number_field: str = field_name_map.get("claim_number", "")
    type_field: str = field_name_map.get("type", "")
    original_text_field: str = field_name_map.get("original_text", "")
    parent_claim_field: str = field_name_map.get("parent_claim", "")

    logger.info(f"检测到结构化权利要求表，字段映射: {field_name_map}")

    # 按编号排序提取
    claims_list: List[Dict[str, str]] = []
    for record in records:
        fields_data: dict = record.get("fields", {})
        claim_number: str = ""
        if claim_number_field and claim_number_field in fields_data:
            claim_number = _extract_text_value(fields_data[claim_number_field])
        claim_text: str = ""
        if original_text_field and original_text_field in fields_data:
            claim_text = _extract_text_value(fields_data[original_text_field])
        if claim_text:
            claims_list.append({
                "claim_number": claim_number,
                "claim_text": claim_text
            })

    if not claims_list:
        return ""

    # 组装为权利要求书格式文本
    parts: List[str] = []
    for item in claims_list:
        num: str = item["claim_number"].strip()
        text: str = item["claim_text"].strip()
        if num:
            parts.append(f"{num}. {text}")
        else:
            parts.append(text)

    return "\n\n".join(parts)


def _parse_independent_claims(claims_text: str) -> List[Dict[str, str]]:
    """
    从权利要求文本中解析出独立权利要求。
    独立权利要求的特征：
    1. 不引用其他权利要求（不含"根据权利要求X"等表述）
    2. 包含完整的技术方案（含"其特征在于"或类似表述）

    返回: [{"claim_id": "1", "claim_text": "..."}, ...]
    """
    if not claims_text:
        return []

    # 策略：按权利要求编号分段，然后过滤出独立权利要求
    # 常见格式：
    #   "1. 一种XXX，其特征在于..."
    #   "2. 根据权利要求1所述的XXX，其特征在于..."
    #   "7. 一种XXX，包括..."

    # 分段正则：匹配 "数字." 或 "数字、" 开头的权利要求
    claim_pattern = re.compile(
        r'(?:^|\n)\s*(\d+)\s*[.、．]\s*',
        re.MULTILINE
    )

    # 找到所有权利要求的起始位置
    matches: list = list(claim_pattern.finditer(claims_text))

    if not matches:
        # 没有按编号分段，尝试把整段当做一个权利要求
        if "其特征在于" in claims_text or "包括" in claims_text:
            return [{"claim_id": "1", "claim_text": claims_text.strip()}]
        return []

    claims: List[Dict[str, str]] = []
    for i, match in enumerate(matches):
        claim_id: str = match.group(1)
        start_pos: int = match.start()
        end_pos: int = matches[i + 1].start() if i + 1 < len(matches) else len(claims_text)
        claim_text: str = claims_text[start_pos:end_pos].strip()

        # 判断是否为独立权利要求：不含"根据权利要求X"的引用
        is_dependent: bool = bool(re.search(
            r'根据权利要求\s*\d+|按照权利要求\s*\d+|如权利要求\s*\d+|引用权利要求\s*\d+',
            claim_text
        ))

        if not is_dependent and claim_text:
            claims.append({"claim_id": claim_id, "claim_text": claim_text})

    return claims


def _extract_specification_text(records: List[Dict[str, Any]], fields_items: List[Dict[str, Any]]) -> str:
    """从说明书表中提取说明书全文"""
    spec_field_name: str = ""
    # 1. 关键词匹配字段名
    for keyword in SPEC_FIELD_KEYWORDS:
        for field in fields_items:
            fn: str = str(field.get("field_name", ""))
            if keyword in fn.lower():
                spec_field_name = fn
                break
        if spec_field_name:
            break

    # 2. 提取所有记录中的文本
    all_texts: List[str] = []
    for record in records:
        fields_data: dict = record.get("fields", {})
        if spec_field_name and spec_field_name in fields_data:
            text_val: str = _extract_text_value(fields_data[spec_field_name])
            if text_val:
                all_texts.append(text_val)
        else:
            # 兜底：提取所有文本字段的值
            for field_name, field_value in fields_data.items():
                text_val = _extract_text_value(field_value)
                if text_val and len(text_val) > 50:  # 过滤短文本
                    all_texts.append(text_val)

    return "\n\n".join(all_texts)


def _extract_spec_images(records: List[Dict[str, Any]], fields_items: List[Dict[str, Any]]) -> List[str]:
    """从说明书附图表中提取附图URL"""
    all_images: List[str] = []

    for record in records:
        fields_data: dict = record.get("fields", {})
        for field in fields_items:
            field_name: str = str(field.get("field_name", ""))
            field_type: int = int(field.get("type", 0))
            fn_lower: str = field_name.lower()

            if field_name not in fields_data:
                continue

            field_value = fields_data[field_name]

            # 图片/附图关键词字段
            is_image_field: bool = any(kw in fn_lower for kw in IMAGE_FIELD_KEYWORDS + DRAWING_FIELD_KEYWORDS)
            if is_image_field:
                img_urls: List[str] = _extract_image_urls(field_value)
                if not img_urls:
                    text_url: str = _extract_text_value(field_value)
                    if text_url and text_url.startswith("http"):
                        img_urls = [text_url]
                all_images.extend(img_urls)
                continue

            # 附件类型字段
            if field_type == FIELD_TYPE_ATTACHMENT:
                img_urls = _extract_image_urls(field_value)
                all_images.extend(img_urls)
                continue

            # URL类型字段
            if field_type == FIELD_TYPE_URL:
                img_urls = _extract_image_urls(field_value)
                all_images.extend(img_urls)

    return all_images


def _build_product_from_record(
    fields_data: dict,
    fields_items: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """从产品表的单条记录中构建商品信息"""
    product_name: str = ""
    product_description: str = ""
    product_images: List[str] = []

    field_type_map: Dict[str, int] = {}
    for field in fields_items:
        fn: str = str(field.get("field_name", ""))
        ft: int = int(field.get("type", 0))
        field_type_map[fn] = ft

    for field in fields_items:
        field_name: str = str(field.get("field_name", ""))
        field_type: int = int(field.get("type", 0))
        fn_lower: str = field_name.lower()

        if field_name not in fields_data:
            continue

        field_value = fields_data[field_name]

        # 1. 产品名称
        is_name: bool = any(kw in fn_lower for kw in NAME_FIELD_KEYWORDS)
        if is_name and not product_name:
            text_val: str = _extract_text_value(field_value)
            if text_val:
                product_name = text_val
            continue

        # 2. 描述
        is_desc: bool = any(kw in fn_lower for kw in DESC_FIELD_KEYWORDS)
        if is_desc:
            text_val = _extract_text_value(field_value)
            if text_val:
                if product_description:
                    product_description += "\n" + text_val
                else:
                    product_description = text_val
            continue

        # 3. 图片字段
        is_image: bool = any(kw in fn_lower for kw in IMAGE_FIELD_KEYWORDS)
        if is_image:
            img_urls: List[str] = _extract_image_urls(field_value)
            if not img_urls:
                text_url: str = _extract_text_value(field_value)
                if text_url and text_url.startswith("http"):
                    img_urls = [text_url]
                elif text_url:
                    for line in text_url.split("\n"):
                        line = line.strip()
                        if line.startswith("http"):
                            img_urls.append(line)
            product_images.extend(img_urls)
            continue

        # 4. 附件类型 → 图片
        if field_type == FIELD_TYPE_ATTACHMENT:
            img_urls = _extract_image_urls(field_value)
            product_images.extend(img_urls)
            continue

        # 5. URL类型
        if field_type == FIELD_TYPE_URL:
            img_urls = _extract_image_urls(field_value)
            product_images.extend(img_urls)
            continue

        # 6. 其他文本字段 → 补充描述
        if field_type in (FIELD_TYPE_TEXT, FIELD_TYPE_RICH_TEXT):
            text_val = _extract_text_value(field_value)
            if text_val and len(text_val.strip()) > 0:
                if not product_name and len(text_val.strip()) <= 200:
                    product_name = text_val.strip()
                else:
                    if product_description:
                        product_description += f"\n{field_name}: {text_val.strip()}"
                    else:
                        product_description = f"{field_name}: {text_val.strip()}"

    return {
        "name": product_name,
        "description": product_description,
        "images": product_images,
        "raw_data": fields_data
    }


# ============================================================
# 主节点函数
# ============================================================

def parse_and_fetch_node(
    state: ParseAndFetchInput,
    config: RunnableConfig,
    runtime: Runtime[Context]
) -> ParseAndFetchOutput:
    """
    title: 解析飞书URL并获取数据
    desc: 解析飞书多维表格URL，自动发现Base下的权利要求表、说明书表、产品表，分别读取独立权利要求、说明书全文+附图、商品信息
    integrations: 飞书多维表格
    """
    ctx = runtime.context
    feishu_url: str = state.feishu_url

    # ===== 1. 解析URL =====
    parsed: Dict[str, str] = _parse_feishu_url(feishu_url)
    app_token: str = parsed.get("app_token", "")
    url_table_id: str = parsed.get("table_id", "")

    if not app_token:
        return ParseAndFetchOutput(
            app_token="", table_id="",
            independent_claims=[], specification_text="", specification_images=[],
            products=[], error_status="无法从URL中解析出app_token"
        )

    logger.info(f"解析飞书URL: app_token={app_token}, url_table_id={url_table_id}")

    # ===== 2. 初始化飞书客户端 =====
    bitable = FeishuBitable()

    # ===== 3. 列出Base下所有表格 =====
    all_tables: List[Dict[str, Any]] = []
    try:
        tables_resp: dict = bitable.list_tables(app_token=app_token)
        tables_items: list = tables_resp.get("data", {}).get("items", [])
        all_tables = tables_items
    except Exception as e:
        logger.error(f"获取飞书表格列表失败: {e}")
        return ParseAndFetchOutput(
            app_token=app_token, table_id="",
            independent_claims=[], specification_text="", specification_images=[],
            products=[], error_status=f"获取飞书表格列表失败: {e}"
        )

    if not all_tables:
        return ParseAndFetchOutput(
            app_token=app_token, table_id="",
            independent_claims=[], specification_text="", specification_images=[],
            products=[], error_status="飞书多维表格中没有找到任何数据表"
        )

    logger.info(f"共找到 {len(all_tables)} 个数据表:")
    for tbl in all_tables:
        logger.info(f"  表格: name='{tbl.get('name')}', table_id='{tbl.get('table_id')}'")

    # ===== 4. 为每个表格获取字段定义并判断角色 =====
    table_roles: Dict[str, str] = {}
    table_fields: Dict[str, List[Dict[str, Any]]] = {}

    for tbl in all_tables:
        tbl_id: str = str(tbl.get("table_id", ""))
        tbl_name: str = str(tbl.get("name", ""))

        if not tbl_id:
            continue

        fields_items: List[Dict[str, Any]] = _fetch_all_fields(bitable, app_token, tbl_id)
        table_fields[tbl_id] = fields_items

        role: str = _identify_table_role(tbl_name, fields_items)
        table_roles[tbl_id] = role

        field_names_str: str = ", ".join(str(f.get("field_name", "")) for f in fields_items)
        logger.info(f"  表格 '{tbl_name}' ({tbl_id}): role={role}, fields=[{field_names_str}]")

    # ===== 5. 确定各角色表格 =====
    claim_table_id: str = ""
    spec_table_id: str = ""
    spec_drawing_table_id: str = ""
    product_table_id: str = ""

    for tbl_id, role in table_roles.items():
        if role == "claim" and not claim_table_id:
            claim_table_id = tbl_id
        elif role == "specification" and not spec_table_id:
            spec_table_id = tbl_id
        elif role == "spec_drawing" and not spec_drawing_table_id:
            spec_drawing_table_id = tbl_id
        elif role == "product" and not product_table_id:
            product_table_id = tbl_id

    # 兜底分配：如果某些角色没找到，从"unknown"中尝试分配
    unknown_tables: List[str] = [tid for tid, role in table_roles.items() if role == "unknown"]
    if not claim_table_id and unknown_tables:
        claim_table_id = unknown_tables.pop(0)
    if not product_table_id and unknown_tables:
        product_table_id = unknown_tables.pop(0)

    # 最终兜底：如果有2个表且没分出角色
    if not claim_table_id and not product_table_id and len(all_tables) >= 2:
        claim_table_id = str(all_tables[0].get("table_id", ""))
        product_table_id = str(all_tables[1].get("table_id", ""))

    logger.info(
        f"表格分配结果: claim={claim_table_id}, specification={spec_table_id}, "
        f"spec_drawing={spec_drawing_table_id}, product={product_table_id}"
    )

    # ===== 6. 从权利要求表提取独立权利要求 =====
    independent_claims: List[Dict[str, str]] = []

    if claim_table_id:
        claim_fields: List[Dict[str, Any]] = table_fields.get(claim_table_id, [])
        claim_records: List[Dict[str, Any]] = _fetch_all_records(bitable, app_token, claim_table_id)
        logger.info(f"权利要求表({claim_table_id}): {len(claim_fields)} 个字段, {len(claim_records)} 条记录")

        # 提取完整权利要求文本
        all_claims_text: str = _extract_all_claims_text(claim_records, claim_fields)
        if all_claims_text:
            # 解析出独立权利要求
            independent_claims = _parse_independent_claims(all_claims_text)
            logger.info(f"解析出 {len(independent_claims)} 个独立权利要求:")
            for ic in independent_claims:
                logger.info(f"  权利要求{ic['claim_id']}: {ic['claim_text'][:80]}...")
        else:
            logger.warning(f"未能从权利要求表({claim_table_id})中提取到权利要求文本")

    # ===== 7. 从说明书表提取说明书文本 =====
    specification_text: str = ""

    if spec_table_id:
        spec_fields: List[Dict[str, Any]] = table_fields.get(spec_table_id, [])
        spec_records: List[Dict[str, Any]] = _fetch_all_records(bitable, app_token, spec_table_id)
        logger.info(f"说明书表({spec_table_id}): {len(spec_fields)} 个字段, {len(spec_records)} 条记录")

        specification_text = _extract_specification_text(spec_records, spec_fields)
        if specification_text:
            logger.info(f"成功提取说明书文本, 长度={len(specification_text)}")
        else:
            logger.warning(f"未能从说明书表({spec_table_id})中提取到说明书文本")
    else:
        logger.info("未找到说明书表，将仅基于权利要求文字进行理解")

    # ===== 8. 从说明书附图表提取附图 =====
    specification_images: List[str] = []

    if spec_drawing_table_id:
        drawing_fields: List[Dict[str, Any]] = table_fields.get(spec_drawing_table_id, [])
        drawing_records: List[Dict[str, Any]] = _fetch_all_records(bitable, app_token, spec_drawing_table_id)
        logger.info(f"说明书附图表({spec_drawing_table_id}): {len(drawing_fields)} 个字段, {len(drawing_records)} 条记录")

        specification_images = _extract_spec_images(drawing_records, drawing_fields)
        if specification_images:
            logger.info(f"成功提取说明书附图 {len(specification_images)} 张")
        else:
            logger.warning(f"未能从说明书附图表({spec_drawing_table_id})中提取到附图")
    else:
        logger.info("未找到说明书附图表")

    # ===== 9. 从产品表提取所有产品 =====
    products: List[Dict[str, Any]] = []

    if product_table_id:
        product_fields: List[Dict[str, Any]] = table_fields.get(product_table_id, [])
        product_records: List[Dict[str, Any]] = _fetch_all_records(bitable, app_token, product_table_id)
        logger.info(f"产品表({product_table_id}): {len(product_fields)} 个字段, {len(product_records)} 条记录")

        for pf in product_fields:
            logger.info(f"  产品字段: name='{pf.get('field_name')}', type={pf.get('type')}")

        for rec in product_records:
            fields_data: dict = rec.get("fields", {})
            product_info: Dict[str, Any] = _build_product_from_record(fields_data, product_fields)

            has_content: bool = bool(
                product_info.get("name") or
                product_info.get("description") or
                product_info.get("images")
            )
            if not has_content:
                continue

            products.append(product_info)

    # ===== 10. 结果日志 =====
    if not independent_claims:
        logger.warning("未能从飞书表格中提取到独立权利要求")
    if not products:
        logger.warning("未能从飞书表格中提取到商品信息")

    logger.info(
        f"提取结果: 独立权利要求={len(independent_claims)}个, "
        f"说明书长度={len(specification_text)}, "
        f"说明书附图={len(specification_images)}张, "
        f"商品数量={len(products)}"
    )
    for ic in independent_claims:
        logger.info(f"  独立权利要求{ic['claim_id']}: {ic['claim_text'][:100]}...")
    for idx, prod in enumerate(products):
        logger.info(
            f"商品{idx + 1}: name='{str(prod.get('name', ''))[:50]}', "
            f"desc_len={len(str(prod.get('description', '')))}, "
            f"images={len(prod.get('images', []))}"
        )

    output_table_id: str = url_table_id or claim_table_id

    return ParseAndFetchOutput(
        app_token=app_token,
        table_id=output_table_id,
        independent_claims=independent_claims,
        specification_text=specification_text,
        specification_images=specification_images,
        products=products
    )
