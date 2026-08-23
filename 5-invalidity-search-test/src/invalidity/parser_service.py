from __future__ import annotations

import hashlib
import json
import re
import secrets
import subprocess
import zipfile
from contextlib import asynccontextmanager
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import fitz
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import ConfigurationError, ParserSettings, Settings
from .source_snapshot import (
    SafeFetcher,
    SourceSnapshotError,
    _SOURCE_MIME_TYPES,
    read_allowed_file,
)


SERVICE_ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = SERVICE_ROOT / ".data" / "invalidity" / "test" / "parser-cache"
FIGURE_ROOT = SERVICE_ROOT / ".data" / "invalidity" / "test" / "parser-figures"
SPACE_RE = re.compile(r"[\s\u3000]+")
CLAIM_START_RE = re.compile(r"(?m)(?:^|\n)\s*(\d{1,3})\s*[.．、,，]\s*(?=\S)")
HEADER_RE = re.compile(
    r"(?im)^\s*(?:CN\s*\d+\s*[A-Z]\s*)?(?:权\s*利\s*要\s*求\s*书)"
    r"(?:\s*\d+\s*/\s*\d+\s*页)?\s*$"
)
FIGURE_LABEL_RE = re.compile(r"图\s*\d{1,3}(?:[A-Za-z])?", re.IGNORECASE)
PARSER_VERSION = "invalidity-isolated-neutral-parser-v2"
SPECIFICATION_HEADINGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("技术领域", ("技术领域",)),
    ("背景技术", ("背景技术",)),
    ("发明内容", ("发明内容", "发明概述", "发明的内容")),
    ("实用新型内容", ("实用新型内容",)),
    ("附图说明", ("附图说明",)),
    ("具体实施方式", ("具体实施方式", "实施方式", "具体实施例", "实施例")),
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


class PatentFileInput(BaseModel):
    url: str = Field(min_length=1)
    file_type: str = "image"


class RunInput(BaseModel):
    patent_file: PatentFileInput
    task_id: str = Field(min_length=1)


class NodeInput(BaseModel):
    patent_file: PatentFileInput | None = None
    raw_text: str | None = None
    claims_section_text: str | None = None


def _compact(value: str) -> str:
    return SPACE_RE.sub("", value or "")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


ParserRuntimeSettings = ParserSettings | Settings


def _load_source(
    uri: str, settings: ParserRuntimeSettings | None = None
) -> tuple[bytes, str]:
    parsed = urlparse(uri)
    if parsed.scheme in {"http", "https"}:
        fetcher = SafeFetcher(
            timeout_seconds=(
                settings.source_fetch_timeout_seconds if settings is not None else 30
            ),
            max_redirects=(settings.source_max_redirects if settings is not None else 5),
        )
        fetched = fetcher.fetch(
            uri,
            max_bytes=(settings.source_max_bytes if settings is not None else 50 * 1024 * 1024),
            allowed_mime_types=_SOURCE_MIME_TYPES,
            code_prefix="SOURCE",
            user_agent="patent-invalidity-parser/1.0 (bounded-source-fetch)",
        )
        suffix = Path(urlparse(fetched.final_url).path).suffix.lower()
        if not suffix and fetched.media_type == "application/pdf":
            suffix = ".pdf"
        return fetched.content, suffix or ".bin"
    if parsed.scheme not in {"", "file"}:
        raise SourceSnapshotError(
            "SOURCE_PATH_NOT_ALLOWED",
            "目标专利来源只能是允许目录中的文件或公网 HTTP(S) URL",
            status_code=422,
        )
    path_value = parsed.path if parsed.scheme == "file" else uri
    if settings is None:
        # Trusted direct library use remains available for the offline
        # regression runner.  Every HTTP route always supplies Settings and is
        # therefore restricted to the explicit upload/artifact roots below.
        path = Path(path_value).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"专利文件不存在: {path}")
        return path.read_bytes(), path.suffix.lower()
    content, _media_type, path = read_allowed_file(
        path_value,
        allowed_roots=(*settings.allowed_source_roots, settings.artifact_root),
        max_bytes=settings.source_max_bytes,
        allowed_mime_types=_SOURCE_MIME_TYPES,
        code_prefix="SOURCE",
    )
    return content, path.suffix.lower()


def _ocr_page(page: fitz.Page, *, page_segmentation_mode: int = 6) -> str:
    image = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False).tobytes("png")
    try:
        result = subprocess.run(
            [
                "tesseract",
                "stdin",
                "stdout",
                "-l",
                "chi_sim+eng",
                "--psm",
                str(page_segmentation_mode),
            ],
            input=image,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"扫描页 OCR 不可用: {type(exc).__name__}") from exc
    if result.returncode != 0:
        raise ValueError("扫描页 OCR 失败")
    return result.stdout.decode("utf-8", errors="replace").strip()


def _extract_pdf(content: bytes) -> tuple[list[str], bool, str | None]:
    document = fitz.open(stream=content, filetype="pdf")
    try:
        text_pages = [page.get_text("text").strip() for page in document]
        needs_ocr = sum(len(_compact(item)) for item in text_pages) < max(300, document.page_count * 20)
        if needs_ocr:
            text_pages = [_ocr_page(document.load_page(index)) for index in range(document.page_count)]
        else:
            for index, value in enumerate(text_pages):
                if len(_compact(value)) < 12:
                    text_pages[index] = _ocr_page(document.load_page(index))
        # Dense OCR (PSM 6) is suitable for the body but can omit the isolated
        # publication block in the upper-right corner of a CN cover page.
        # Sparse OCR (PSM 11) is therefore retained as a separate metadata
        # source rather than replacing the body text or guessing a date.
        metadata_ocr_text = (
            _ocr_page(document.load_page(0), page_segmentation_mode=11)
            if needs_ocr and document.page_count
            else None
        )
        return text_pages, needs_ocr, metadata_ocr_text
    finally:
        document.close()


def _extract_image_text(content: bytes, suffix: str) -> list[str]:
    try:
        result = subprocess.run(
            [
                "tesseract",
                "stdin",
                "stdout",
                "-l",
                "chi_sim+eng",
                "--psm",
                "6",
            ],
            input=content,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            f"{suffix or '图片'}专利 OCR 不可用: {type(exc).__name__}"
        ) from exc
    if result.returncode != 0:
        raise ValueError(f"{suffix or '图片'}专利 OCR 失败")
    return [result.stdout.decode("utf-8", errors="replace").strip()]


def _extract_docx_text(content: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            document_xml = archive.read("word/document.xml").decode(
                "utf-8", errors="replace"
            )
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError("DOCX 文件结构无效") from exc
    soup = BeautifulSoup(document_xml, "xml")
    paragraphs: list[str] = []
    for paragraph in soup.find_all("w:p"):
        value = "".join(node.get_text() for node in paragraph.find_all("w:t"))
        if value.strip():
            paragraphs.append(value.strip())
    return ["\n".join(paragraphs)]


def _extract_pages(content: bytes, suffix: str) -> tuple[list[str], bool, str | None]:
    if suffix == ".pdf" or content.startswith(b"%PDF"):
        return _extract_pdf(content)
    if suffix in IMAGE_SUFFIXES:
        return _extract_image_text(content, suffix), True, None
    if suffix == ".docx" or content.startswith(b"PK\x03\x04"):
        return _extract_docx_text(content), False, None
    decoded = content.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        decoded = BeautifulSoup(decoded, "html.parser").get_text("\n")
    return [decoded], False, None


def _write_cache(target: Path, payload: dict[str, Any]) -> None:
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def _cache_extract(
    content: bytes,
    suffix: str,
    *,
    cache_root: Path = CACHE_ROOT,
) -> dict[str, Any]:
    digest = _sha256(content)
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / f"{digest}.json"
    if target.is_file():
        try:
            cached = json.loads(target.read_text(encoding="utf-8"))
            if cached.get("sha256") == digest and isinstance(cached.get("pages"), list):
                if (
                    cached.get("used_ocr")
                    and not cached.get("metadata_ocr_text")
                    and (suffix == ".pdf" or content.startswith(b"%PDF"))
                ):
                    document = fitz.open(stream=content, filetype="pdf")
                    try:
                        if document.page_count:
                            cached["metadata_ocr_text"] = _ocr_page(
                                document.load_page(0), page_segmentation_mode=11
                            )
                            _write_cache(target, cached)
                    finally:
                        document.close()
                return cached
        except (OSError, ValueError, TypeError):
            pass
    pages, used_ocr, metadata_ocr_text = _extract_pages(content, suffix)
    payload = {
        "sha256": digest,
        "suffix": suffix,
        "used_ocr": used_ocr,
        "pages": pages,
        "metadata_ocr_text": metadata_ocr_text,
    }
    _write_cache(target, payload)
    return payload


def _claims_section(pages: list[str]) -> str:
    start_index: int | None = None
    for index, page in enumerate(pages):
        compact = _compact(page)
        if "说明书" in compact and "权利要求书" not in compact:
            continue
        if re.search(r"(?m)^\s*1\s*[.．、,，]\s*\S", page):
            start_index = index
            break
    selected: list[str] = []
    if start_index is not None:
        for index in range(start_index, len(pages)):
            page = pages[index]
            compact = _compact(page)
            if index > start_index and "说明书" in compact and "权利要求书" not in compact:
                break
            selected.append(page)
    if not selected:
        joined = "\n\f\n".join(pages)
        compact = _compact(joined)
        start = compact.find("权利要求书")
        if start >= 0:
            # Preserve the original text for parsing; locate section with a
            # whitespace-tolerant expression instead of slicing compact text.
            match = re.search(r"权\s*利\s*要\s*求\s*书", joined)
            if match:
                selected = [joined[match.end() :]]
    section = "\n".join(selected)
    specification_start = re.search(
        rf"(?m)^\s*(?:{_heading_pattern('说明书')}|"
        + "|".join(
            _heading_pattern(alias)
            for _name, values in SPECIFICATION_HEADINGS
            for alias in values
        )
        + r")\s*$",
        section,
    )
    if specification_start:
        section = section[: specification_start.start()]
    section = HEADER_RE.sub("", section)
    section = re.sub(
        r"(?im)^\s*CN\s*\d+\s*[A-Z]\s*\d*\s*/\s*\d+\s*页\s*$",
        "",
        section,
    )
    return section.strip()


def _claim_candidates(section: str) -> list[tuple[int, int, int]]:
    candidates = [(int(match.group(1)), match.start(), match.end()) for match in CLAIM_START_RE.finditer(section)]
    if not candidates:
        fallback = re.compile(
            r"(?<!\d)(\d{1,3})\s*[.．、,，]\s*(?=(?:一种|根据|如|按照|用于|所述|该))"
        )
        candidates = [(int(match.group(1)), match.start(), match.end()) for match in fallback.finditer(section)]
    best: list[tuple[int, int, int]] = []
    expected = 1
    for item in candidates:
        number = item[0]
        if number == expected:
            best.append(item)
            expected += 1
        elif number == 1 and len(best) < 2:
            best = [item]
            expected = 2
    return best


def _claim_parent_ids(claim_text: str) -> list[str]:
    compact = _compact(re.sub(r"^\s*\d+\s*[.．、,，]\s*", "", claim_text))
    match = re.match(
        r"(?:根据|如|按照)权利要求"
        r"([0-9一二三四五六七八九十、,，或和至到\\-~～]+?)"
        r"(?:(?:中)?任(?:一|意一)(?:项)?)?(?:所述|记载|的)",
        compact,
    )
    if not match:
        return []
    expression = match.group(1)
    numbers: list[int] = []
    range_match = re.search(r"(\d+)(?:至|到|[-~～])(\d+)", expression)
    if range_match:
        start, end = int(range_match.group(1)), int(range_match.group(2))
        if 0 < start <= end <= 200:
            numbers.extend(range(start, end + 1))
    for value in re.findall(r"\d+", expression):
        number = int(value)
        if number > 0 and number not in numbers:
            numbers.append(number)
    return [str(number) for number in numbers]


def _parse_claims(section: str) -> list[dict[str, Any]]:
    markers = _claim_candidates(section)
    claims: list[dict[str, Any]] = []
    for index, (number, start, _end) in enumerate(markers):
        boundary = markers[index + 1][1] if index + 1 < len(markers) else len(section)
        raw = section[start:boundary].strip()
        raw = re.sub(r"\s+", " ", raw)
        raw = re.sub(rf"^{number}\s*[.．、,，]\s*", f"{number}. ", raw)
        if len(_compact(raw)) < 20:
            continue
        parent_claim_ids = _claim_parent_ids(raw)
        claims.append(
            {
                "claim_id": str(number),
                "claim_type": "DEPENDENT" if parent_claim_ids else "INDEPENDENT",
                "claim_text": raw,
                "parent_claim_id": (
                    ",".join(parent_claim_ids) if parent_claim_ids else None
                ),
                "parent_claim_ids": parent_claim_ids,
                "sentence_units": [
                    item.strip()
                    for item in re.split(r"(?<=[；;。])", raw)
                    if item.strip()
                ],
            }
        )
    if not claims:
        raise ValueError("没有从权利要求书页面解析出连续权利要求")
    ids = [int(item["claim_id"]) for item in claims]
    if ids != list(range(1, len(ids) + 1)):
        raise ValueError(f"权利要求编号不连续: {ids}")
    return claims


def _label_value(text: str, label: str, stop_labels: tuple[str, ...]) -> str | None:
    label_pattern = r"\s*".join(map(re.escape, label))
    stop = "|".join(r"\s*".join(map(re.escape, item)) for item in stop_labels)
    match = re.search(
        rf"{label_pattern}\s*[:：]?\s*(.+?)(?=(?:{stop})|$)",
        text,
        re.DOTALL,
    )
    if not match:
        return None
    return re.sub(r"\s+", " ", match.group(1)).strip(" ：:\n")


def _date_after_label(text: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        pattern = r"\s*".join(map(re.escape, label))
        match = re.search(
            rf"{pattern}.{{0,80}}?(20\d{{2}}|19\d{{2}})\s*[.年/-]\s*(\d{{1,2}})\s*[.月/-]\s*(\d{{1,2}})",
            text,
            re.DOTALL,
        )
        if match:
            return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None


def _clean_field(value: str | None) -> str | None:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" ：:\n")
    return cleaned or None


def _coded_field(
    text: str,
    code: str,
    label: str,
    stop_codes: tuple[str, ...],
) -> str | None:
    stop = "|".join(rf"\({re.escape(item)}\)" for item in stop_codes)
    pattern = r"\s*".join(map(re.escape, label))
    match = re.search(
        rf"\({re.escape(code)}\)\s*{pattern}\s*[:：]?\s*"
        rf"(.+?)(?=\n\s*(?:{stop})|$)",
        text,
        re.DOTALL,
    )
    return _clean_field(match.group(1)) if match else None


def _all_dates(value: str | None) -> list[str]:
    return [
        f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        for year, month, day in re.findall(
            r"(20\d{2}|19\d{2})\s*[.年/-]\s*(\d{1,2})\s*[.月/-]\s*(\d{1,2})",
            value or "",
        )
    ]


def _split_people(value: str | None) -> list[str]:
    if not value:
        return []
    normalized = re.sub(r"\s{2,}|\u3000+", "、", value)
    normalized = re.sub(r"\s+(?=[A-ZＡ-Ｚ]\s*[·•])", "、", normalized)
    if re.fullmatch(
        r"[\u3400-\u9fff]{2,4}(?:\s+[\u3400-\u9fff]{2,4})+",
        normalized,
    ):
        normalized = re.sub(r"\s+", "、", normalized)
    return [
        item.strip()
        for item in re.split(r"[、；;，,\n]+", normalized)
        if item.strip()
    ]


def _bibliographic_data(
    pages: list[str],
    *,
    patent_number: str | None,
    application_date: str | None,
    priority_date: str | None,
    publication_date: str | None,
) -> dict[str, Any]:
    cover = "\n".join(pages[:2])
    application_number = _coded_field(
        cover, "21", "申请号", ("22", "30", "62", "66", "71", "73")
    )
    application_number_match = re.search(
        r"\b(?:PCT/[A-Z]{2}\d{4}/\d+|\d{8,14}(?:\.\d+)?)\b",
        application_number or "",
        re.IGNORECASE,
    )
    if application_number_match:
        application_number = application_number_match.group(0)
    applicant = _coded_field(
        cover, "71", "申请人", ("72", "73", "74", "51", "54")
    )
    holder = _coded_field(
        cover, "73", "专利权人", ("72", "74", "51", "54", "57")
    )
    if holder:
        holder = re.split(
            r"\s+(?:地址|\(\s*\d{2}\s*\))\s*",
            holder,
            maxsplit=1,
        )[0].strip()
    inventor_text = _coded_field(
        cover, "72", "发明人", ("73", "74", "51", "54", "57")
    )
    agency_text = _coded_field(
        cover, "74", "专利代理机构", ("51", "54", "57")
    )
    priority_text = _coded_field(
        cover, "30", "优先权数据", ("62", "66", "71", "72", "73", "74", "51", "54")
    )
    divisional_text = _coded_field(
        cover, "62", "分案原申请数据", ("66", "71", "72", "73", "74", "51", "54")
    )
    international_application = _coded_field(
        cover, "86", "国际申请", ("85", "87", "30", "71", "72", "73", "74", "51", "54")
    )
    international_publication = _coded_field(
        cover, "87", "国际公布", ("30", "71", "72", "73", "74", "51", "54")
    )
    entry_date_text = _coded_field(
        cover, "85", "进入国家阶段日期", ("86", "87", "30", "71", "72", "73", "74")
    )
    classifications = list(
        dict.fromkeys(
            re.findall(
                r"\b[A-HY]\d{2}[A-Z]\s*\d+/\d+(?:\s*\(\d{4}\.\d{2}\))?",
                cover,
                re.IGNORECASE,
            )
        )
    )
    agents: list[str] = []
    agency = agency_text
    if inventor_text and len(_compact(inventor_text)) > 120:
        inventor_text = None
    if agency_text and len(_compact(agency_text)) > 160:
        agency_text = None
        agency = None
    if agency_text:
        agent_match = re.search(r"代理人\s*(.+)$", agency_text)
        if agent_match:
            agents = _split_people(agent_match.group(1))
            agency = _clean_field(agency_text[: agent_match.start()])
    grant_date = (
        publication_date
        if publication_date and re.search(r"\(45\)\s*授\s*权\s*公\s*告\s*日", cover)
        else _date_after_label(cover, ("(45)授权公告日",))
    )
    application_publication_date = (
        publication_date
        if publication_date and re.search(r"\(43\)\s*申\s*请\s*公\s*布\s*日", cover)
        else _date_after_label(cover, ("(43)申请公布日",))
    )
    date_events: list[dict[str, Any]] = []
    date_sources = (
        ("22", "申请日", application_date),
        ("30", "最早优先权日", priority_date),
        ("43", "申请公布日", application_publication_date),
        ("45", "授权公告日", grant_date),
    )
    for code, label, value in date_sources:
        if value:
            date_events.append({"code": code, "label": label, "date": value})
    for code, label, raw_value in (
        ("30", "优先权", priority_text),
        ("62", "分案原申请", divisional_text),
        ("85", "进入国家阶段", entry_date_text),
        ("86", "国际申请", international_application),
        ("87", "国际公布", international_publication),
    ):
        for value in _all_dates(raw_value):
            row = {"code": code, "label": label, "date": value}
            if row not in date_events:
                date_events.append(row)
    priority_claims = [
        {"date": value, "raw_text": priority_text}
        for value in _all_dates(priority_text)
    ]
    publication_date = publication_date or application_publication_date or grant_date
    return {
        "application_number": application_number,
        "publication_number": patent_number,
        "applicants": [applicant] if applicant else [],
        "patent_holders": [holder] if holder else [],
        "inventors": _split_people(inventor_text),
        "patent_agency": agency,
        "patent_agents": agents,
        "classifications": classifications,
        "priority_claims": priority_claims,
        "divisional_parent": divisional_text,
        "international_application": international_application,
        "international_publication": international_publication,
        "national_phase_entry": entry_date_text,
        "application_date": application_date,
        "priority_date": priority_date,
        "publication_date": publication_date,
        "grant_date": grant_date,
        "date_events": date_events,
    }


def _heading_pattern(value: str) -> str:
    return r"\s*".join(map(re.escape, value))


def _structured_specification(pages: list[str]) -> dict[str, str]:
    raw_text = "\n\f\n".join(pages)
    aliases = [
        (canonical, alias)
        for canonical, values in SPECIFICATION_HEADINGS
        for alias in values
    ]
    expression = "|".join(
        f"(?P<h{index}>{_heading_pattern(alias)})"
        for index, (_canonical, alias) in enumerate(aliases)
    )
    matches = list(re.finditer(rf"(?m)^\s*(?:{expression})\s*$", raw_text))
    if not matches:
        return {"全文": raw_text}
    drawing_header = re.search(
        rf"(?m)^\s*{_heading_pattern('说明书附图')}\s*$",
        raw_text[matches[0].start() :],
    )
    specification_end = (
        matches[0].start() + drawing_header.start()
        if drawing_header
        else len(raw_text)
    )
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        if match.start() >= specification_end:
            break
        group_index = next(
            (
                candidate
                for candidate in range(len(aliases))
                if match.group(f"h{candidate}") is not None
            ),
            None,
        )
        if group_index is None:
            continue
        canonical = aliases[group_index][0]
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else specification_end
        )
        end = min(end, specification_end)
        section_text = raw_text[match.end() : end].strip()
        if not section_text:
            continue
        if canonical in sections:
            sections[canonical] = f"{sections[canonical]}\n{section_text}".strip()
        else:
            sections[canonical] = section_text
    full_text = raw_text[matches[0].start() : specification_end].strip()
    return {**sections, "全文": full_text}


def _page_role(page_number: int, text: str) -> str:
    compact = _compact(text)
    if page_number == 1:
        return "cover_and_abstract"
    if "权利要求书" in compact:
        return "claims"
    if "说明书附图" in compact:
        return "specification_drawings"
    if any(_compact(alias) in compact for _name, values in SPECIFICATION_HEADINGS for alias in values):
        return "specification"
    return "document_body"


def _metadata(
    pages: list[str],
    source_uri: str,
    claims: list[dict[str, Any]],
    *,
    used_ocr: bool = False,
    supplemental_metadata_text: str | None = None,
) -> dict[str, Any]:
    first = "\n".join([*pages[:2], supplemental_metadata_text or ""])
    filename = Path(urlparse(source_uri).path).name
    number_match = re.search(r"CN[\s_-]*(\d{7,12})[\s_-]*([A-Z]\d?)", filename, re.IGNORECASE)
    if not number_match:
        number_match = re.search(r"CN[\s_-]*(\d{7,12})[\s_-]*([A-Z]\d?)", first, re.IGNORECASE)
    patent_number = f"CN{number_match.group(1)}{number_match.group(2).upper()}" if number_match else None
    title = None
    for label in ("(54)发明名称", "(54)实用新型名称", "(54)外观设计名称"):
        title = _label_value(first, label, ("(57)摘要", "(21)申请号", "(22)申请日", "(73)专利权人"))
        if title:
            title = re.sub(r"(?:\(57\)|57)\s*摘\s*要.*$", "", title).strip()
            break
    abstract = _label_value(first, "(57)摘要", ("CN", "权利要求书", "说明书"))
    claim_title = ""
    if claims:
        claim = re.sub(r"^1\s*[.．、,，]\s*", "", claims[0]["claim_text"])
        claim_title = re.split(
            r"[，,；;。]|其特征在于|包括",
            claim,
            maxsplit=1,
        )[0].strip()[:120]
    if not title:
        title = claim_title or Path(filename).stem
    elif used_ocr and claim_title:
        compact_title = _compact(title)
        compact_claim_title = _compact(claim_title)
        if (
            abs(len(compact_title) - len(compact_claim_title)) <= 4
            and SequenceMatcher(None, compact_title, compact_claim_title).ratio() >= 0.75
        ):
            title = claim_title
    publication_date = None
    if patent_number:
        spaced_number = r"\s*".join(map(re.escape, patent_number))
        publication_match = re.search(
            rf"{spaced_number}\s*(20\d{{2}}|19\d{{2}})\s*[.年/-]\s*(\d{{1,2}})\s*[.月/-]\s*(\d{{1,2}})",
            first,
            re.IGNORECASE,
        )
        if publication_match:
            publication_date = (
                f"{int(publication_match.group(1)):04d}-"
                f"{int(publication_match.group(2)):02d}-"
                f"{int(publication_match.group(3)):02d}"
            )
    publication_date = publication_date or _date_after_label(
        first, ("(45)授权公告日", "(43)申请公布日", "公开日", "公告日")
    )
    application_date = _date_after_label(first, ("(22)申请日", "申请日"))
    priority_date = _date_after_label(first, ("(30)优先权", "优先权"))
    if not priority_date and used_ocr:
        priority_anchor = re.search(r"优\s*先.{0,500}", first, re.DOTALL)
        if priority_anchor:
            candidates = [
                f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
                for year, month, day in re.findall(
                    r"(20\d{2}|19\d{2})\s*[.年/-]\s*(\d{1,2})\s*[.月/-]\s*(\d{1,2})",
                    priority_anchor.group(0),
                )
            ]
            earlier = [item for item in candidates if not application_date or item < application_date]
            if earlier:
                priority_date = min(earlier)
    bibliographic_data = _bibliographic_data(
        [first],
        patent_number=patent_number,
        application_date=application_date,
        priority_date=priority_date,
        publication_date=publication_date,
    )
    holder = _label_value(
        first,
        "(73)专利权人",
        ("地址", "(72)发明人", "(74)专利代理机构", "(54)"),
    )
    if not holder:
        applicants = bibliographic_data.get("applicants") or []
        holder = str(applicants[0]) if applicants else None
    return {
        "patent_number": patent_number,
        "application_number": bibliographic_data.get("application_number"),
        "title": title or None,
        "patent_holder": holder,
        "abstract": abstract,
        "application_date": application_date,
        "priority_date": priority_date,
        "publication_date": bibliographic_data.get("publication_date"),
        "grant_date": bibliographic_data.get("grant_date"),
        "bibliographic_data": bibliographic_data,
    }


def _target_page_text(
    document: fitz.Document,
    page_index: int,
    page_texts: list[str] | None,
) -> str:
    if page_texts is not None and page_index < len(page_texts):
        return str(page_texts[page_index] or "")
    return document.load_page(page_index).get_text("text")


def _rank_target_figure_pages(
    document: fitz.Document,
    page_texts: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank non-cover pages by deterministic technical-drawing likelihood.

    Embedded-image count alone cannot distinguish a scanned claim page from a
    scanned drawing page because both are one full-page raster image.  The
    parser has already produced OCR text by this point, so page role, text
    density, drawing labels, and neighboring drawing pages can be combined with
    PDF image/vector structure without another model or OCR pass.
    """

    page_count = document.page_count
    texts = [
        _target_page_text(document, index, page_texts)
        for index in range(page_count)
    ]
    compacts = [_compact(text) for text in texts]
    explicit_drawing_pages = {
        index
        for index, compact in enumerate(compacts)
        if "说明书附图" in compact or "摘要附图" in compact
    }
    candidates: list[dict[str, Any]] = []
    for page_index in range(1, page_count):
        page = document.load_page(page_index)
        text = texts[page_index]
        compact = compacts[page_index]
        text_length = len(compact)
        image_count = len(page.get_images(full=True))
        vector_count = len(page.get_drawings())
        figure_label_count = len(FIGURE_LABEL_RE.findall(compact))
        score = 0
        reasons: list[str] = []

        if page_index in explicit_drawing_pages:
            score += 180
            reasons.append("explicit_drawing_header")
        if figure_label_count:
            score += min(figure_label_count, 6) * 12
            reasons.append(f"figure_labels:{figure_label_count}")
        if image_count:
            score += min(image_count, 8) * 7
            reasons.append(f"embedded_images:{image_count}")
        if vector_count > 1:
            score += min(vector_count - 1, 12) * 2
            reasons.append(f"vector_groups:{vector_count}")

        if text_length <= 80:
            score += 45
            reasons.append("very_low_text_density")
        elif text_length <= 260:
            score += 28
            reasons.append("low_text_density")
        elif text_length >= 1_200:
            score -= 55
            reasons.append("dense_body_text")
        elif text_length >= 650:
            score -= 30
            reasons.append("body_text")

        if "权利要求书" in compact:
            score -= 220
            reasons.append("claim_page_penalty")
        if "附图说明" in compact and page_index not in explicit_drawing_pages:
            score -= 90
            reasons.append("drawing_description_penalty")

        # OCR can miss the header on the first scanned drawing page.  A sparse
        # page adjacent to an explicit drawing page is still a strong member of
        # the same drawing run, without relying on a patent-number exception.
        adjacent_to_drawing = any(
            neighbor in explicit_drawing_pages
            for neighbor in (page_index - 1, page_index + 1)
        )
        if adjacent_to_drawing and text_length <= 260:
            score += 70
            reasons.append("adjacent_to_drawing_run")
        if not explicit_drawing_pages and text_length <= 260:
            score += round(30 * page_index / max(1, page_count - 1))
            reasons.append("sparse_late_page_fallback")

        candidates.append(
            {
                "page_index": page_index,
                "page_number": page_index + 1,
                "score": score,
                "likely_drawing": score >= 100,
                "text_length": text_length,
                "image_count": image_count,
                "vector_count": vector_count,
                "figure_label_count": figure_label_count,
                "reasons": reasons,
            }
        )
    return sorted(
        candidates,
        key=lambda item: (
            -int(item["score"]),
            -int(item["figure_label_count"]),
            -int(item["image_count"]),
            int(item["page_index"]),
        ),
    )


def _figure_descriptions(specification: dict[str, str]) -> dict[str, str]:
    section = str(specification.get("附图说明") or "")
    if not section:
        return {}
    matches = list(
        re.finditer(
            r"图\s*(\d{1,3}(?:[A-Za-z])?)\s*[：:、,，]?\s*",
            section,
            re.IGNORECASE,
        )
    )
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        figure_id = f"图{match.group(1).upper()}"
        description = re.sub(r"\s+", " ", section[match.end() : end]).strip(
            " ；;。"
        )
        result[figure_id] = description
    return result


def _image_mime_and_suffix(block: dict[str, Any]) -> tuple[str, str]:
    extension = str(block.get("ext") or "png").lower().lstrip(".")
    if extension in {"jpg", "jpeg"}:
        return "image/jpeg", ".jpg"
    if extension in {"tif", "tiff"}:
        return "image/tiff", ".tiff"
    return "image/png", ".png"


def _write_figure(
    *,
    directory: Path,
    figure_id: str,
    description: str,
    page_number: int | None,
    selection_role: str,
    sequence: int,
    content: bytes,
    mime_type: str,
    suffix: str,
) -> dict[str, Any]:
    safe_id = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", figure_id)
    destination = directory / f"{sequence:03d}-{safe_id}{suffix}"
    destination.write_bytes(content)
    digest = _sha256(content)
    return {
        "figure_id": figure_id,
        "figure_url": str(destination.resolve()),
        "file_path": str(destination.resolve()),
        "figure_description": description,
        "page_number": page_number,
        "selection_role": selection_role,
        "mime_type": mime_type,
        "file_size": len(content),
        "file_sha256": digest,
    }


def _page_figure_labels(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            f"图{match.upper()}"
            for match in re.findall(
                r"图\s*(\d{1,3}(?:[A-Za-z])?)",
                text or "",
                re.IGNORECASE,
            )
        )
    )


def _large_image_blocks(page: fitz.Page) -> list[dict[str, Any]]:
    page_area = max(1.0, float(page.mediabox.width * page.mediabox.height))
    result: list[dict[str, Any]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 1:
            continue
        content = bytes(block.get("image") or b"")
        bbox = fitz.Rect(block.get("bbox") or (0, 0, 0, 0))
        area = max(0.0, float(bbox.width * bbox.height))
        if len(content) < 5_000 or area < page_area * 0.008:
            continue
        result.append({**block, "_content": content, "_bbox": bbox})
    return sorted(
        result,
        key=lambda item: (
            float(item["_bbox"].y0),
            float(item["_bbox"].x0),
        ),
    )


def _render_page_region(page: fitz.Page, region: fitz.Rect | None = None) -> bytes:
    clip = (region & page.rect) if region is not None else page.rect
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(2.0, 2.0),
        clip=clip,
        colorspace=fitz.csRGB,
        alpha=False,
    )
    return pixmap.tobytes("png")


def _split_raster_drawing_page(page: fitz.Page) -> list[bytes]:
    pixmap = page.get_pixmap(
        matrix=fitz.Matrix(2.0, 2.0),
        colorspace=fitz.csRGB,
        alpha=False,
    )
    width, height, channels = pixmap.width, pixmap.height, pixmap.n
    samples = memoryview(pixmap.samples)
    left, right = int(width * 0.05), int(width * 0.95)
    top, bottom = int(height * 0.08), int(height * 0.92)
    step = 2
    active: list[bool] = []
    threshold = max(4, int((right - left) * 0.0025 / step))
    for y in range(top, bottom):
        row = y * pixmap.stride
        black = 0
        for x in range(left, right, step):
            offset = row + x * channels
            if (
                samples[offset] < 210
                and samples[offset + 1] < 210
                and samples[offset + 2] < 210
            ):
                black += 1
        active.append(black > threshold)
    radius = 8
    prefix = [0]
    for value in active:
        prefix.append(prefix[-1] + int(value))
    dilated = [
        prefix[min(len(active), index + radius + 1)]
        - prefix[max(0, index - radius)]
        > 0
        for index in range(len(active))
    ]
    intervals: list[list[int]] = []
    start: int | None = None
    for index, value in enumerate(dilated):
        y = top + index
        if value and start is None:
            start = y
        elif not value and start is not None:
            if y - start >= 45:
                intervals.append([start, y])
            start = None
    if start is not None and bottom - start >= 45:
        intervals.append([start, bottom])
    merged: list[list[int]] = []
    for y0, y1 in intervals:
        if merged and (
            y0 - merged[-1][1] < 35
            or (y1 - y0 < 95 and merged[-1][1] - merged[-1][0] > 120)
        ):
            merged[-1][1] = y1
        elif y1 - y0 >= 80:
            merged.append([y0, y1])
    results: list[bytes] = []
    x_scale = page.rect.width / width
    y_scale = page.rect.height / height
    for y0, y1 in merged:
        if (y1 - y0) * (right - left) < width * height * 0.008:
            continue
        region = fitz.Rect(
            left * x_scale,
            max(0, y0 - 12) * y_scale,
            right * x_scale,
            min(height, y1 + 12) * y_scale,
        )
        results.append(_render_page_region(page, region))
    return results


def _extract_html_or_text_figure_urls(content: bytes, suffix: str) -> list[str]:
    decoded = content.decode("utf-8", errors="replace")
    if suffix in {".html", ".htm"}:
        soup = BeautifulSoup(decoded, "html.parser")
        return list(
            dict.fromkeys(
                str(node.get("src") or "").strip()
                for node in soup.find_all("img")
                if str(node.get("src") or "").strip().startswith(
                    ("http://", "https://", "data:")
                )
            )
        )
    return list(
        dict.fromkeys(
            re.findall(
                r"https?://[^\s\"'<>]+?\.(?:png|jpe?g|tiff?)(?:\?[^\s\"'<>]*)?",
                decoded,
                re.IGNORECASE,
            )
        )
    )


def _render_target_figures(
    content: bytes,
    digest: str,
    page_texts: list[str] | None = None,
    *,
    suffix: str = ".pdf",
    specification: dict[str, str] | None = None,
    figure_root: Path = FIGURE_ROOT,
) -> list[dict[str, Any]]:
    directory = figure_root / digest
    directory.mkdir(parents=True, exist_ok=True)
    if suffix in IMAGE_SUFFIXES:
        mime_type = (
            "image/jpeg"
            if suffix in {".jpg", ".jpeg"}
            else "image/tiff"
            if suffix in {".tif", ".tiff"}
            else "image/png"
        )
        return [
            _write_figure(
                directory=directory,
                figure_id="原始附图",
                description="用户提交的原始专利图片",
                page_number=1,
                selection_role="source_image",
                sequence=1,
                content=content,
                mime_type=mime_type,
                suffix=suffix,
            )
        ]
    if suffix in {".html", ".htm", ".txt"}:
        return [
            {
                "figure_id": f"图{index}",
                "figure_url": url,
                "figure_description": "原文中引用的附图",
                "page_number": None,
                "selection_role": "referenced_image",
            }
            for index, url in enumerate(
                _extract_html_or_text_figure_urls(content, suffix), start=1
            )
        ]
    if suffix == ".docx" or content.startswith(b"PK\x03\x04"):
        figures: list[dict[str, Any]] = []
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                media_names = sorted(
                    name
                    for name in archive.namelist()
                    if name.startswith("word/media/")
                )
                for index, name in enumerate(media_names, start=1):
                    image = archive.read(name)
                    image_suffix = Path(name).suffix.lower() or ".png"
                    mime_type = (
                        "image/jpeg"
                        if image_suffix in {".jpg", ".jpeg"}
                        else "image/tiff"
                        if image_suffix in {".tif", ".tiff"}
                        else "image/png"
                    )
                    figures.append(
                        _write_figure(
                            directory=directory,
                            figure_id=f"图{index}",
                            description="DOCX 原文中的嵌入图片",
                            page_number=None,
                            selection_role="embedded_document_image",
                            sequence=index,
                            content=image,
                            mime_type=mime_type,
                            suffix=image_suffix,
                        )
                    )
        except (OSError, zipfile.BadZipFile):
            return []
        return figures
    if not (suffix == ".pdf" or content.startswith(b"%PDF")):
        return []
    document = fitz.open(stream=content, filetype="pdf")
    try:
        figures: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        used_figure_ids: set[str] = set()
        descriptions = _figure_descriptions(specification or {})
        sequence = 0
        if document.page_count:
            cover = document.load_page(0)
            cover_blocks = [
                item
                for item in _large_image_blocks(cover)
                if float(item["_bbox"].y0) >= float(cover.mediabox.height) * 0.35
            ]
            if cover_blocks:
                block = max(
                    cover_blocks,
                    key=lambda item: float(
                        item["_bbox"].width * item["_bbox"].height
                    ),
                )
                image = bytes(block["_content"])
                mime_type, image_suffix = _image_mime_and_suffix(block)
                sequence += 1
                seen_hashes.add(_sha256(image))
                figures.append(
                    _write_figure(
                        directory=directory,
                        figure_id="摘要附图",
                        description="摘要附图",
                        page_number=1,
                        selection_role="abstract_figure",
                        sequence=sequence,
                        content=image,
                        mime_type=mime_type,
                        suffix=image_suffix,
                    )
                )
                used_figure_ids.add("摘要附图")
            else:
                region = fitz.Rect(
                    cover.rect.width * 0.42,
                    cover.rect.height * 0.45,
                    cover.rect.width * 0.93,
                    cover.rect.height * 0.86,
                )
                image = _render_page_region(cover, region)
                sequence += 1
                seen_hashes.add(_sha256(image))
                figures.append(
                    _write_figure(
                        directory=directory,
                        figure_id="摘要附图",
                        description="摘要附图（扫描首页摘要区）",
                        page_number=1,
                        selection_role="abstract_figure",
                        sequence=sequence,
                        content=image,
                        mime_type="image/png",
                        suffix=".png",
                    )
                )
                used_figure_ids.add("摘要附图")
        ranked = _rank_target_figure_pages(document, page_texts)
        drawing_pages = sorted(
            (
                item
                for item in ranked
                if bool(item.get("likely_drawing"))
                or "explicit_drawing_header" in (item.get("reasons") or [])
            ),
            key=lambda item: int(item["page_index"]),
        )
        expected_ids = list(descriptions)
        generated_index = 1
        for selection in drawing_pages:
            page_index = int(selection["page_index"])
            page = document.load_page(page_index)
            labels = _page_figure_labels(
                str(page_texts[page_index] if page_texts else page.get_text("text"))
            )
            blocks = _large_image_blocks(page)
            page_images: list[tuple[bytes, str, str]] = []
            if blocks:
                for block in blocks:
                    mime_type, image_suffix = _image_mime_and_suffix(block)
                    page_images.append(
                        (bytes(block["_content"]), mime_type, image_suffix)
                    )
            if len(page_images) <= 1 and len(labels) > 1:
                split_images = _split_raster_drawing_page(page)
                if split_images:
                    page_images = [
                        (image, "image/png", ".png") for image in split_images
                    ]
            if not page_images:
                page_images = [(_render_page_region(page), "image/png", ".png")]
            for item_index, (image, mime_type, image_suffix) in enumerate(page_images):
                image_hash = _sha256(image)
                if image_hash in seen_hashes:
                    continue
                seen_hashes.add(image_hash)
                label_candidate = (
                    labels[item_index] if item_index < len(labels) else None
                )
                if (
                    label_candidate
                    and label_candidate not in used_figure_ids
                    and (not expected_ids or label_candidate in expected_ids)
                ):
                    figure_id = label_candidate
                else:
                    figure_id = next(
                        (
                            value
                            for value in expected_ids
                            if value not in used_figure_ids
                        ),
                        "",
                    )
                if not figure_id:
                    while f"图{generated_index}" in used_figure_ids:
                        generated_index += 1
                    figure_id = f"图{generated_index}"
                numeric = re.match(r"图(\d+)", figure_id)
                if numeric:
                    generated_index = max(generated_index, int(numeric.group(1)) + 1)
                else:
                    generated_index += 1
                used_figure_ids.add(figure_id)
                sequence += 1
                figures.append(
                    _write_figure(
                        directory=directory,
                        figure_id=figure_id,
                        description=descriptions.get(figure_id, ""),
                        page_number=page_index + 1,
                        selection_role="specification_drawing",
                        sequence=sequence,
                        content=image,
                        mime_type=mime_type,
                        suffix=image_suffix,
                    )
                )
        return figures
    finally:
        document.close()


def parse_source(
    uri: str,
    *,
    settings: ParserRuntimeSettings | None = None,
    require_claims: bool = True,
) -> dict[str, Any]:
    content, suffix = _load_source(uri, settings)
    cache_root = settings.artifact_root / "parser-cache" if settings else CACHE_ROOT
    figure_root = settings.artifact_root / "parser-figures" if settings else FIGURE_ROOT
    extracted = _cache_extract(content, suffix, cache_root=cache_root)
    pages = [str(item) for item in extracted["pages"]]
    section = _claims_section(pages)
    parse_errors: list[str] = []
    try:
        claims = _parse_claims(section)
    except ValueError as exc:
        if require_claims:
            raise
        claims = []
        parse_errors.append(f"reference_claims_not_parsed: {exc}")
    metadata = _metadata(
        pages,
        uri,
        claims,
        used_ocr=bool(extracted.get("used_ocr")),
        supplemental_metadata_text=str(extracted.get("metadata_ocr_text") or ""),
    )
    digest = str(extracted["sha256"])
    specification = _structured_specification(pages)
    figures = _render_target_figures(
        content,
        digest,
        pages,
        suffix=suffix,
        specification=specification,
        figure_root=figure_root,
    )
    return {
        "raw_text": "\n\f\n".join(pages),
        "page_texts": [
            {
                "page_number": index,
                "page_role": _page_role(index, value),
                "text": value,
            }
            for index, value in enumerate(pages, start=1)
        ],
        "claims_section_text": section,
        "claims": claims,
        "metadata": metadata,
        "specification": specification,
        "figures": figures,
        "errors": parse_errors,
        "source_sha256": digest,
        "source_format": suffix.lstrip(".") or "bin",
        "source_byte_size": len(content),
        "page_count": len(pages),
        "used_ocr": bool(extracted.get("used_ocr")),
        "parser_version": PARSER_VERSION,
    }


def _authorized(request: Request, expected_token: str) -> bool:
    authorization = request.headers.get("authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied:
        return False
    return secrets.compare_digest(supplied, expected_token)


def create_parser_app(settings: ParserRuntimeSettings | None = None) -> FastAPI:
    supplied_settings = settings

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        actual = supplied_settings or ParserSettings.from_environment(
            load_dotenv_files=False
        )
        if (
            actual.environment != "test"
            or actual.parser_port != 5201
            or not actual.parser_api_token
        ):
            raise ConfigurationError(
                "隔离解析服务只能用 test/5201 和独立 INVALIDITY_TEST_PARSER_TOKEN 启动"
            )
        application.state.invalidity_parser_settings = actual
        try:
            yield
        finally:
            application.state.invalidity_parser_settings = None

    application = FastAPI(
        title="Invalidity Neutral Patent Parser",
        version="1.0.0",
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def bearer_authentication(request: Request, call_next: Any):
        if request.url.path == "/health":
            return await call_next(request)
        actual: ParserRuntimeSettings | None = getattr(
            request.app.state, "invalidity_parser_settings", None
        )
        if actual is None or not actual.parser_api_token or not _authorized(
            request, actual.parser_api_token
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "AUTHENTICATION_REQUIRED",
                    "message": "需要有效的 Bearer token",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    @application.get("/health")
    def health(request: Request) -> dict[str, Any]:
        actual: ParserRuntimeSettings = request.app.state.invalidity_parser_settings
        return {
            "status": "ok",
            "service": "invalidity-neutral-parser",
            "environment": actual.environment,
        }

    @application.post("/run")
    def run(payload: RunInput, request: Request) -> dict[str, Any]:
        actual: ParserRuntimeSettings = request.app.state.invalidity_parser_settings
        try:
            result = parse_source(payload.patent_file.url, settings=actual)
        except SourceSnapshotError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"code": exc.code, "message": exc.public_message},
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "task_id": payload.task_id,
            "claims": result["claims"],
            "specification": result["specification"],
            "figures": result["figures"],
            "metadata": result["metadata"],
            "errors": result["errors"],
            "db_record_id": None,
            "parser_version": result["parser_version"],
            "source_sha256": result["source_sha256"],
            "source_format": result["source_format"],
            "source_byte_size": result["source_byte_size"],
            "page_count": result["page_count"],
            "used_ocr": result["used_ocr"],
            "claims_section_text": result["claims_section_text"],
            "page_texts": result["page_texts"],
        }

    @application.post("/node_run/{node_id}")
    def node_run(node_id: str, payload: NodeInput, request: Request) -> dict[str, Any]:
        actual: ParserRuntimeSettings = request.app.state.invalidity_parser_settings
        try:
            if node_id == "file_read_node" and payload.patent_file:
                result = parse_source(payload.patent_file.url, settings=actual)
                return {
                    "raw_text": result["raw_text"],
                    "file_format": result["source_format"],
                    "source_byte_size": result["source_byte_size"],
                    "page_count": result["page_count"],
                    "used_ocr": result["used_ocr"],
                    "read_error": None,
                }
            if node_id == "structure_identify_node" and payload.raw_text:
                pages = payload.raw_text.split("\n\f\n")
                section = _claims_section(pages)
                claims = _parse_claims(section)
                return {
                    "specification_sections": [
                        {"section_name": key, "section_text": value}
                        for key, value in _structured_specification(pages).items()
                    ],
                    "patent_metadata": _metadata(pages, "text-input.txt", claims),
                    "claims_section_text": section,
                    "identify_errors": [],
                }
            if node_id == "claims_parse_node" and payload.claims_section_text:
                return {
                    "claims_list": _parse_claims(payload.claims_section_text),
                    "claims_errors": [],
                }
        except SourceSnapshotError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"code": exc.code, "message": exc.public_message},
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise HTTPException(status_code=404, detail=f"不支持或缺少输入的节点: {node_id}")

    return application


app = create_parser_app()
