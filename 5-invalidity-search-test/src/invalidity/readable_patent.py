from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from datetime import date
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

import fitz
from lxml import etree

from .artifacts import ArtifactStore, StoredArtifact


NORMALIZATION_VERSION = "readable-patent-v1"
_MIN_ANALYSIS_CHARS = 200
_MAX_PAGES = 300
_MAX_PDF_BYTES = 25 * 1024 * 1024


def _clean_text(value: Any) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", str(value or "").replace("\x00", " ")).strip()


def _compact_length(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def _local_name(element: etree._Element) -> str:
    return etree.QName(element).localname.lower()


def _safe_xml(content: bytes) -> etree._Element:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        recover=False,
        huge_tree=False,
    )
    return etree.fromstring(content, parser=parser)


def _bundle_responses(bundle_content: bytes) -> dict[str, bytes]:
    try:
        payload = json.loads(bundle_content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if payload.get("format") != "epo_ops_response_bundle_v1":
        return {}
    responses = payload.get("responses")
    if not isinstance(responses, Mapping):
        return {}
    result: dict[str, bytes] = {}
    for name, row in responses.items():
        if not isinstance(row, Mapping):
            continue
        encoded = row.get("content_base64")
        if not isinstance(encoded, str):
            continue
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError:
            continue
        expected = str(row.get("sha256") or "")
        if expected and hashlib.sha256(content).hexdigest() != expected:
            continue
        result[str(name)] = content
    return result


def _xml_section_rows(content: bytes, section_name: str) -> list[dict[str, Any]]:
    root = _safe_xml(content)
    section_nodes = root.xpath(f"//*[local-name()='{section_name}']")
    rows: list[dict[str, Any]] = []
    sequence = 0
    for section in section_nodes:
        candidates = section.xpath(
            ".//*[local-name()='heading' or local-name()='p' "
            "or local-name()='claim' or local-name()='claim-text']"
        )
        for element in candidates:
            # Claim-text is often nested in claim.  Keep the most specific text
            # exactly once instead of duplicating the whole claim subtree.
            name = _local_name(element)
            if name == "claim" and element.xpath(".//*[local-name()='claim-text']"):
                continue
            text = _clean_text(" ".join(str(item) for item in element.itertext()))
            if not text:
                continue
            if rows and rows[-1]["text"] == text:
                continue
            sequence += 1
            anchor = (
                str(element.get("id") or element.get("num") or "").strip()
                or f"{section_name}-{sequence}"
            )
            rows.append(
                {
                    "section": section_name,
                    "anchor": anchor,
                    "kind": name,
                    "text": text,
                }
            )
    if rows:
        return rows
    for section in section_nodes:
        text = _clean_text(" ".join(str(item) for item in section.itertext()))
        if text:
            rows.append(
                {
                    "section": section_name,
                    "anchor": f"{section_name}-1",
                    "kind": section_name,
                    "text": text,
                }
            )
    return rows


def normalize_epo_bundle(
    bundle_content: bytes | None,
    *,
    source_sha256: str,
    title: str | None,
    abstract: str | None,
) -> dict[str, Any] | None:
    """Convert frozen OPS XML into readable, anchored JSON.

    OPS XML is preferred over PDF text because it preserves description and
    claim structure and avoids OCR noise.  Bibliographic-only bundles are not
    analysis-ready.
    """

    if not bundle_content:
        return None
    responses = _bundle_responses(bundle_content)
    rows: list[dict[str, Any]] = []
    for section in ("description", "claims"):
        content = responses.get(section)
        if content:
            rows.extend(_xml_section_rows(content, section))
    body_text = "\n\n".join(row["text"] for row in rows)
    if _compact_length(body_text) < _MIN_ANALYSIS_CHARS:
        return None
    leading = [
        value
        for value in (
            _clean_text(title),
            f"Abstract\n{_clean_text(abstract)}" if _clean_text(abstract) else "",
        )
        if value
    ]
    full_text = "\n\n".join([*leading, body_text])
    return {
        "schema_version": NORMALIZATION_VERSION,
        "source_sha256": source_sha256,
        "source_kind": "epo_ops_structured_xml",
        "normalization_status": "completed",
        "analysis_ready": True,
        "analysis_readiness_reason": "EPO OPS description/claims structured XML normalized",
        "page_count": None,
        "processed_page_count": None,
        "text_page_count": None,
        "ocr_page_count": 0,
        "failed_pages": [],
        "sections": rows,
        "full_text": full_text,
    }


def _ocr_page(page: fitz.Page, *, timeout_seconds: float) -> str:
    image = page.get_pixmap(
        matrix=fitz.Matrix(2.0, 2.0),
        alpha=False,
    ).tobytes("png")
    return _ocr_image(image, timeout_seconds=timeout_seconds)


def _ocr_image(
    image: bytes,
    *,
    timeout_seconds: float,
    page_segmentation_mode: int = 6,
) -> str:
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
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise ValueError("tesseract returned a non-zero exit status")
    return result.stdout.decode("utf-8", errors="replace").replace("\x00", " ").strip()


def normalize_pdf(
    pdf_content: bytes,
    *,
    source_sha256: str,
    title: str | None,
    abstract: str | None,
    total_timeout_seconds: float = 180.0,
    per_page_ocr_timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    if not pdf_content.lstrip().startswith(b"%PDF"):
        raise ValueError("source is not a PDF")
    if len(pdf_content) > _MAX_PDF_BYTES:
        raise ValueError("PDF exceeds normalization byte budget")
    deadline = time.monotonic() + total_timeout_seconds
    document = fitz.open(stream=pdf_content, filetype="pdf")
    try:
        if document.page_count < 1 or document.page_count > _MAX_PAGES:
            raise ValueError("PDF page count is outside normalization budget")
        extracted = [
            document.load_page(index).get_text("text").replace("\x00", " ").strip()
            for index in range(document.page_count)
        ]
        document_is_scanned = sum(_compact_length(item) for item in extracted) < max(
            _MIN_ANALYSIS_CHARS,
            document.page_count * 20,
        )
        rows: list[dict[str, Any]] = []
        failed_pages: list[int] = []
        ocr_indexes = [
            index
            for index, value in enumerate(extracted)
            if document_is_scanned or _compact_length(value) < 12
        ]
        ocr_images: dict[int, bytes] = {}
        for index in ocr_indexes:
            if time.monotonic() >= deadline:
                failed_pages.append(index + 1)
                continue
            try:
                ocr_images[index] = document.load_page(index).get_pixmap(
                    matrix=fitz.Matrix(2.0, 2.0),
                    alpha=False,
                ).tobytes("png")
            except (RuntimeError, ValueError):
                failed_pages.append(index + 1)
        ocr_results: dict[int, str] = {}
        if ocr_images:
            executor = ThreadPoolExecutor(max_workers=min(2, len(ocr_images)))
            futures = {
                executor.submit(
                    _ocr_image,
                    image,
                    timeout_seconds=min(
                        per_page_ocr_timeout_seconds,
                        max(1.0, deadline - time.monotonic()),
                    ),
                ): index
                for index, image in ocr_images.items()
            }
            try:
                for future in as_completed(
                    futures,
                    timeout=max(1.0, deadline - time.monotonic()),
                ):
                    index = futures[future]
                    try:
                        ocr_results[index] = future.result()
                    except (OSError, subprocess.TimeoutExpired, ValueError):
                        failed_pages.append(index + 1)
            except TimeoutError:
                for future, index in futures.items():
                    if not future.done():
                        future.cancel()
                        failed_pages.append(index + 1)
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
        ocr_page_count = len(ocr_results)
        text_page_count = 0
        for index, value in enumerate(extracted):
            source = "pdf_text"
            text = value
            if index in ocr_indexes:
                if index in ocr_results:
                    text = ocr_results[index]
                    source = "ocr"
                else:
                    source = "failed"
                    text = ""
            if _compact_length(text):
                text_page_count += 1
            rows.append(
                {
                    "section": "pdf_page",
                    "anchor": f"page-{index + 1}",
                    "kind": source,
                    "page_start": index + 1,
                    "page_end": index + 1,
                    "text": text,
                }
            )
        body_text = "\n\n".join(
            f"[Page {row['page_start']}]\n{row['text']}"
            for row in rows
            if row["text"]
        )
        leading = [
            value
            for value in (
                _clean_text(title),
                f"Abstract\n{_clean_text(abstract)}" if _clean_text(abstract) else "",
            )
            if value
        ]
        full_text = "\n\n".join([*leading, body_text])
        failed_pages = sorted(set(failed_pages))
        processed_page_count = len(rows)
        analysis_ready = (
            not failed_pages
            and processed_page_count == document.page_count
            and _compact_length(body_text) >= _MIN_ANALYSIS_CHARS
        )
        if analysis_ready:
            reason = (
                "all PDF pages processed with OCR/text extraction"
                if ocr_page_count
                else "all PDF pages processed with embedded text extraction"
            )
            status = "completed"
        elif failed_pages:
            reason = f"page processing failed or timed out: {failed_pages}"
            status = "failed"
        else:
            reason = "all pages processed but substantive full text was not recovered"
            status = "incomplete"
        return {
            "schema_version": NORMALIZATION_VERSION,
            "source_sha256": source_sha256,
            "source_kind": "pdf_ocr" if ocr_page_count else "pdf_text",
            "normalization_status": status,
            "analysis_ready": analysis_ready,
            "analysis_readiness_reason": reason,
            "page_count": document.page_count,
            "processed_page_count": processed_page_count,
            "text_page_count": text_page_count,
            "ocr_page_count": ocr_page_count,
            "failed_pages": failed_pages,
            "sections": rows,
            "full_text": full_text,
        }
    finally:
        document.close()


def _publication_number_token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _front_page_dates(value: str) -> set[str]:
    result: set[str] = set()

    def add_date(year: int, month: int, day: int) -> None:
        try:
            result.add(date(year, month, day).isoformat())
        except ValueError:
            return

    for match in re.finditer(
        r"(?<!\d)(\d{4})\s*(?:年|[./-])\s*(\d{1,2})\s*"
        r"(?:月|[./-])\s*(\d{1,2})\s*(?:日)?(?!\d)",
        value,
    ):
        year, month, day = (int(item) for item in match.groups())
        add_date(year, month, day)

    month_name = (
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
        r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
        r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    )
    month_numbers = {
        "jan": 1,
        "feb": 2,
        "mar": 3,
        "apr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "aug": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dec": 12,
    }
    # US patent front pages commonly print dates as ``Oct. 9, 2008``.
    # OCR may remove any of the spaces or the period, so accept those layout
    # variations while still requiring a real calendar date.
    for match in re.finditer(
        rf"(?<![A-Za-z])(?P<month>{month_name})\.?\s*"
        rf"(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*"
        rf"(?P<year>\d{{4}})(?!\d)",
        value,
        flags=re.IGNORECASE,
    ):
        month = month_numbers[match.group("month")[:3].lower()]
        add_date(int(match.group("year")), month, int(match.group("day")))

    # Some WIPO/EPO pages and OCR engines emit the day before the month.
    for match in re.finditer(
        rf"(?<!\d)(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s*"
        rf"(?P<month>{month_name})\.?\s*,?\s*"
        rf"(?P<year>\d{{4}})(?!\d)",
        value,
        flags=re.IGNORECASE,
    ):
        month = month_numbers[match.group("month")[:3].lower()]
        add_date(int(match.group("year")), month, int(match.group("day")))
    return result


def verify_frozen_patent_pdf_front_page_date(
    pdf_content: bytes,
    *,
    source_sha256: str,
    publication_number: str | None,
    publication_date: str | None,
    ocr_timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    """Verify provider metadata against the frozen patent PDF front page.

    The function never invents a date.  It only upgrades an already supplied
    publication date when page 1 of the exact frozen PDF contains both the
    exact publication number and the same date under a publication/announcement
    marker.  Image-only fronts receive one bounded sparse OCR pass; the OCR
    excerpt and configuration remain in the returned audit payload.
    """

    expected_number = _publication_number_token(publication_number)
    expected_date = str(publication_date or "").strip()
    actual_sha256 = hashlib.sha256(pdf_content).hexdigest()
    audit: dict[str, Any] = {
        "schema_version": "front-page-date-evidence-v1",
        "verified": False,
        "source_sha256": source_sha256,
        "page": 1,
        "publication_number": publication_number,
        "publication_date": publication_date,
        "attempts": [],
    }
    if actual_sha256 != source_sha256:
        audit["reason"] = "source_sha256_mismatch"
        return audit
    if not expected_number or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expected_date):
        audit["reason"] = "expected_identity_or_date_missing"
        return audit
    if not pdf_content.lstrip().startswith(b"%PDF"):
        audit["reason"] = "source_is_not_pdf"
        return audit

    document = fitz.open(stream=pdf_content, filetype="pdf")
    try:
        if document.page_count < 1:
            audit["reason"] = "pdf_has_no_pages"
            return audit
        page = document.load_page(0)
        candidates: list[tuple[str, str]] = [
            ("pdf_text", page.get_text("text").replace("\x00", " ").strip())
        ]
        # Native text is preferred.  Sparse OCR is only needed when that layer
        # cannot independently verify the same frozen metadata.
        native = candidates[0][1]
        native_matches = (
            expected_number in _publication_number_token(native)
            and expected_date in _front_page_dates(native)
        )
        if not native_matches:
            try:
                image = page.get_pixmap(
                    matrix=fitz.Matrix(2.0, 2.0),
                    alpha=False,
                ).tobytes("png")
                candidates.append(
                    (
                        "tesseract_chi_sim_eng_psm11",
                        _ocr_image(
                            image,
                            timeout_seconds=ocr_timeout_seconds,
                            page_segmentation_mode=11,
                        ),
                    )
                )
            except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as exc:
                audit["ocr_error"] = type(exc).__name__
    finally:
        document.close()

    for method, text in candidates:
        observed_dates = sorted(_front_page_dates(text))
        number_matched = expected_number in _publication_number_token(text)
        marker_matched = bool(
            re.search(
                r"(?:[\(\[]\s*(?:43|45)\s*[\)\]]|公开日|公告日)",
                text,
            )
        )
        attempt = {
            "method": method,
            "number_matched": number_matched,
            "marker_matched": marker_matched,
            "observed_dates": observed_dates,
            "excerpt": text[:2000],
        }
        audit["attempts"].append(attempt)
        if number_matched and marker_matched and expected_date in observed_dates:
            audit.update(
                {
                    "verified": True,
                    "reason": "front_page_identity_and_date_exact_match",
                    "extraction_method": method,
                    "evidence_excerpt": text[:2000],
                }
            )
            return audit
    audit["reason"] = "front_page_identity_or_date_not_exactly_matched"
    return audit


def readable_markdown(document: Mapping[str, Any]) -> str:
    lines = [
        "# Readable patent document",
        "",
        f"- Schema: {document.get('schema_version')}",
        f"- Source: {document.get('source_kind')}",
        f"- Analysis ready: {str(bool(document.get('analysis_ready'))).lower()}",
        "",
    ]
    for row in document.get("sections") or []:
        if not isinstance(row, Mapping):
            continue
        anchor = _clean_text(row.get("anchor"))
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        lines.extend([f"## {anchor}", "", text, ""])
    return "\n".join(lines).strip() + "\n"


def cache_paths(
    store: ArtifactStore,
    investigation_id: str,
    source_sha256: str,
) -> tuple[Path, Path]:
    base = (
        store.investigation_dir(investigation_id)
        / "readable-cache"
        / source_sha256
    )
    return base / "document.json", base / "document.md"


def load_cached_readable_document(
    store: ArtifactStore,
    investigation_id: str,
    source_sha256: str,
) -> dict[str, Any] | None:
    json_path, _ = cache_paths(store, investigation_id, source_sha256)
    if not json_path.is_file():
        return None
    try:
        value = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if (
        isinstance(value, dict)
        and value.get("schema_version") == NORMALIZATION_VERSION
        and value.get("source_sha256") == source_sha256
    ):
        return value
    return None


def persist_readable_document(
    store: ArtifactStore,
    investigation_id: str,
    source_sha256: str,
    document: Mapping[str, Any],
) -> tuple[StoredArtifact, StoredArtifact]:
    relative = f"readable-cache/{source_sha256}"
    json_artifact = store.write_json(
        investigation_id,
        f"{relative}/document.json",
        dict(document),
        artifact_type="readable_patent_json",
    )
    markdown_artifact = store.write_bytes(
        investigation_id,
        f"{relative}/document.md",
        readable_markdown(document).encode("utf-8"),
        artifact_type="readable_patent_markdown",
        mime_type="text/markdown",
    )
    return json_artifact, markdown_artifact
