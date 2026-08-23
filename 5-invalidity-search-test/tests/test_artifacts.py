from pathlib import Path

import fitz
import pytest

import invalidity.artifacts as artifacts_module
from invalidity.artifacts import ArtifactBudgetError, ArtifactStore


def test_artifact_store_requires_environment_prefix(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalidity/test"):
        ArtifactStore(tmp_path / "wrong", "test")


def test_artifact_write_is_hashed_and_scoped(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "invalidity" / "test", "test")
    artifact = store.write_bytes(
        "investigation-1",
        "raw/document.bin",
        b"evidence",
        artifact_type="source",
    )
    assert Path(artifact.uri).read_bytes() == b"evidence"
    assert len(artifact.sha256) == 64


def _pdf_bytes(*, pages: int, page_width: float = 300, text: str = "evidence") -> bytes:
    document = fitz.open()
    try:
        for _index in range(pages):
            page = document.new_page(width=page_width, height=300)
            page.insert_text((20, 30), text)
        return document.tobytes()
    finally:
        document.close()


def test_pdf_page_and_text_budgets_fail_closed(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "invalidity" / "test", "test")
    content = _pdf_bytes(pages=3, text="robot cleaner lifting actuator")
    pdf = store.write_bytes(
        "investigation-1",
        "raw/document.pdf",
        content,
        artifact_type="prior_art_pdf",
        mime_type="application/pdf",
    )

    with pytest.raises(ArtifactBudgetError, match="页数"):
        store.render_pdf_images(
            "investigation-1",
            pdf.uri,
            "pages",
            max_pages=2,
        )
    with pytest.raises(ArtifactBudgetError, match="页数"):
        store.extract_pdf_text(content, max_pages=2)
    with pytest.raises(ArtifactBudgetError, match="字符"):
        store.extract_pdf_text(content, max_extracted_chars=10)


def test_pdf_page_pixel_budget_prevents_large_render(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "invalidity" / "test", "test")
    content = _pdf_bytes(pages=1, page_width=10_000)
    pdf = store.write_bytes(
        "investigation-1",
        "raw/wide.pdf",
        content,
        artifact_type="prior_art_pdf",
        mime_type="application/pdf",
    )

    with pytest.raises(ArtifactBudgetError, match="像素"):
        store.render_pdf_images(
            "investigation-1",
            pdf.uri,
            "pages",
            max_page_pixels=100_000,
        )


def test_pdf_text_replaces_postgres_jsonb_unsafe_nul(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRect:
        width = 300
        height = 300

    class FakePage:
        rect = FakeRect()

        def get_text(self, _kind: str) -> str:
            return "position-selective\x00coupling"

    class FakeDocument:
        page_count = 1

        def load_page(self, _index: int) -> FakePage:
            return FakePage()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        artifacts_module.fitz,
        "open",
        lambda *_args, **_kwargs: FakeDocument(),
    )
    store = ArtifactStore(tmp_path / "invalidity" / "test", "test")

    extracted = store.extract_pdf_text(b"%PDF-1.7\nfixture")

    assert extracted == "position-selective coupling"
    assert "\x00" not in extracted
