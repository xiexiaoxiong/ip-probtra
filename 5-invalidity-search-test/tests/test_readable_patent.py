from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import fitz

from invalidity import readable_patent


FIXTURES = Path(__file__).parent / "fixtures"


def _epo_bundle() -> bytes:
    responses = {
        "description": (FIXTURES / "epo_ops_description.xml").read_bytes(),
        "claims": (FIXTURES / "epo_ops_claims.xml").read_bytes(),
    }
    return json.dumps(
        {
            "format": "epo_ops_response_bundle_v1",
            "responses": {
                name: {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content_base64": base64.b64encode(content).decode("ascii"),
                }
                for name, content in responses.items()
            },
        }
    ).encode("utf-8")


def test_epo_structured_xml_is_analysis_ready_and_anchored() -> None:
    document = readable_patent.normalize_epo_bundle(
        _epo_bundle(),
        source_sha256="a" * 64,
        title="Test patent",
        abstract="Test abstract",
    )
    assert document is not None
    assert document["analysis_ready"] is True
    assert document["source_kind"] == "epo_ops_structured_xml"
    assert any(row["section"] == "description" for row in document["sections"])
    assert any(row["section"] == "claims" for row in document["sections"])
    assert len(document["full_text"]) > 200


def test_scanned_pdf_requires_every_page_to_finish_ocr(monkeypatch) -> None:
    pdf = fitz.open()
    pdf.new_page()
    pdf.new_page()
    content = pdf.tobytes()
    pdf.close()
    calls: list[int] = []

    def fake_ocr(
        image: bytes,
        *,
        timeout_seconds: float,
        page_segmentation_mode: int = 6,
    ) -> str:
        calls.append(len(image))
        return (
            "valve conduit coupling structure "
            + "technical disclosure " * 20
        )

    monkeypatch.setattr(readable_patent, "_ocr_image", fake_ocr)
    document = readable_patent.normalize_pdf(
        content,
        source_sha256=hashlib.sha256(content).hexdigest(),
        title="Scanned patent",
        abstract=None,
    )
    assert len(calls) == 2
    assert document["processed_page_count"] == 2
    assert document["ocr_page_count"] == 2
    assert document["analysis_ready"] is True


def _front_page_pdf(text: str) -> bytes:
    document = fitz.open()
    try:
        page = document.new_page()
        page.insert_text((72, 72), text)
        return document.tobytes()
    finally:
        document.close()


def test_front_page_date_verification_accepts_exact_native_identity_and_date() -> None:
    content = _front_page_pdf("CN 109876543 A   (43) 2020.05.06")

    result = readable_patent.verify_frozen_patent_pdf_front_page_date(
        content,
        source_sha256=hashlib.sha256(content).hexdigest(),
        publication_number="CN109876543A",
        publication_date="2020-05-06",
    )

    assert result["verified"] is True
    assert result["extraction_method"] == "pdf_text"


def test_front_page_dates_parse_english_month_names_and_reject_invalid_dates() -> None:
    assert readable_patent._front_page_dates(
        "Pub. Date: Oct. 9, 2008; publication: 11 Jun. 2009; invalid: Feb. 31, 2010"
    ) == {"2008-10-09", "2009-06-11"}


def test_front_page_date_verification_accepts_us_english_ocr_date(monkeypatch) -> None:
    content = _front_page_pdf("")

    def fake_ocr(
        _image: bytes,
        *,
        timeout_seconds: float,
        page_segmentation_mode: int = 6,
    ) -> str:
        assert timeout_seconds > 0
        assert page_segmentation_mode == 11
        return "US 2008/0245714 A1 (43) Pub. Date: Oct. 9, 2008"

    monkeypatch.setattr(readable_patent, "_ocr_image", fake_ocr)
    result = readable_patent.verify_frozen_patent_pdf_front_page_date(
        content,
        source_sha256=hashlib.sha256(content).hexdigest(),
        publication_number="US20080245714A1",
        publication_date="2008-10-09",
    )

    assert result["verified"] is True
    assert result["extraction_method"] == "tesseract_chi_sim_eng_psm11"
    assert result["attempts"][-1]["observed_dates"] == ["2008-10-09"]


def test_front_page_date_verification_uses_sparse_ocr_fallback(monkeypatch) -> None:
    content = _front_page_pdf("")
    calls: list[int] = []

    def fake_ocr(
        _image: bytes,
        *,
        timeout_seconds: float,
        page_segmentation_mode: int = 6,
    ) -> str:
        assert timeout_seconds > 0
        calls.append(page_segmentation_mode)
        return "CN109876543A (45) 2020年05月06日"

    monkeypatch.setattr(readable_patent, "_ocr_image", fake_ocr)
    result = readable_patent.verify_frozen_patent_pdf_front_page_date(
        content,
        source_sha256=hashlib.sha256(content).hexdigest(),
        publication_number="CN109876543A",
        publication_date="2020-05-06",
    )

    assert result["verified"] is True
    assert result["extraction_method"] == "tesseract_chi_sim_eng_psm11"
    assert calls == [11]


def test_front_page_date_verification_rejects_date_mismatch() -> None:
    content = _front_page_pdf("CN109876543A (43) 2020.05.07")

    result = readable_patent.verify_frozen_patent_pdf_front_page_date(
        content,
        source_sha256=hashlib.sha256(content).hexdigest(),
        publication_number="CN109876543A",
        publication_date="2020-05-06",
    )

    assert result["verified"] is False
