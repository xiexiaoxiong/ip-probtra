import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Set, Tuple

import fitz
import requests

MIN_EMBEDDED_IMAGE_BYTES = 5000
RENDER_FALLBACK_DPI_SCALE = 2.0


def extract_figure_labels(page_text: str) -> List[str]:
    import re

    labels = re.findall(r"图\s*(\d+)", page_text or "")
    ordered: List[str] = []
    for label in labels:
        figure_id = f"图{label}"
        if figure_id not in ordered:
            ordered.append(figure_id)
    return ordered


def looks_like_figure_page(page_text: str, figure_labels: List[str], image_count: int, drawing_count: int) -> bool:
    import re

    normalized_text = re.sub(r"\s+", "", page_text or "")
    text_length = len(normalized_text)

    if "说明书附图" in normalized_text:
        return True
    if figure_labels and text_length <= 120:
        return True
    if drawing_count >= 20 and text_length <= 400:
        return True
    if image_count > 0 and drawing_count > 0 and text_length <= 200:
        return True
    return False


def find_figure_anchor_page(doc: fitz.Document) -> int | None:
    for page_num in range(len(doc)):
        page_text = (doc[page_num].get_text("text") or "").replace(" ", "").replace("\n", "")
        if "附图说明" in page_text:
            return page_num
    return None


def find_candidate_figure_pages(doc: fitz.Document, figure_descriptions: Dict[str, str]) -> List[int]:
    import re

    anchor_page = find_figure_anchor_page(doc)
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

        figure_labels = extract_figure_labels(page_text)
        image_count = len(page.get_images(full=True))
        drawing_count = len(page.get_drawings())
        text_length = len(normalized_text)

        if after_anchor and text_length <= 120:
            pages.append(page_num)
            continue
        if after_anchor and drawing_count >= 10 and text_length <= 600:
            pages.append(page_num)
            continue
        if looks_like_figure_page(page_text, figure_labels, image_count, drawing_count):
            if anchor_page is None or page_num >= anchor_page:
                pages.append(page_num)

    if explicit_figure_pages:
        return explicit_figure_pages
    trailing_image_pages = find_trailing_image_only_pages(doc)
    if figure_descriptions and len(trailing_image_pages) >= len(figure_descriptions):
        return trailing_image_pages[-len(figure_descriptions):]
    if pages:
        return pages
    if anchor_page is not None:
        return list(range(anchor_page + 1, len(doc)))
    if figure_descriptions:
        return list(range(max(0, len(doc) - len(figure_descriptions)), len(doc)))
    return []


def find_trailing_image_only_pages(doc: fitz.Document) -> List[int]:
    import re

    trailing_pages: List[int] = []
    for page_num in range(len(doc) - 1, -1, -1):
        page = doc[page_num]
        normalized_text = re.sub(r"\s+", "", page.get_text("text") or "")
        image_count = len(page.get_images(full=True))
        drawing_count = len(page.get_drawings())
        if len(normalized_text) <= 40 and (image_count > 0 or drawing_count > 0):
            trailing_pages.append(page_num)
            continue
        if trailing_pages:
            break
    return list(reversed(trailing_pages))


def resolve_rendered_figure_identity(
    figure_labels: List[str],
    figure_descriptions: Dict[str, str],
    remaining_ids: List[str],
    existing_figure_ids: Set[str],
    next_index: int,
) -> Tuple[str, str]:
    candidate_ids = [figure_id for figure_id in figure_labels if figure_id not in existing_figure_ids]
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


def ensure_pdf_local_path(pdf_path: str) -> Tuple[str, bool]:
    if pdf_path.startswith("http://") or pdf_path.startswith("https://"):
        response = requests.get(pdf_path, timeout=60)
        response.raise_for_status()
        local_path = Path("/tmp") / f"module1-test-{hashlib.md5(pdf_path.encode()).hexdigest()}.pdf"
        local_path.write_bytes(response.content)
        return str(local_path), True
    return pdf_path, False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--url-prefix", required=True)
    parser.add_argument("--descriptions-file", required=True)
    args = parser.parse_args()

    figure_descriptions = json.loads(Path(args.descriptions_file).read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    errors: List[dict] = []
    figures: List[dict] = []

    pdf_local_path, should_cleanup = ensure_pdf_local_path(args.pdf_path)
    doc = fitz.open(pdf_local_path)

    try:
        rendered_hashes: Set[str] = set()
        candidate_pages = find_candidate_figure_pages(doc, figure_descriptions)
        existing_ids: Set[str] = set()
        remaining_ids = list(figure_descriptions.keys())
        next_index = 1

        for page_num in candidate_pages:
            page = doc[page_num]
            page_text = page.get_text("text") or ""
            figure_labels = extract_figure_labels(page_text)
            pix = page.get_pixmap(
                matrix=fitz.Matrix(RENDER_FALLBACK_DPI_SCALE, RENDER_FALLBACK_DPI_SCALE),
                alpha=False,
            )
            image_bytes = pix.tobytes("png")
            image_hash = hashlib.md5(image_bytes).hexdigest()
            if image_hash in rendered_hashes:
                continue
            rendered_hashes.add(image_hash)

            figure_id, description = resolve_rendered_figure_identity(
                figure_labels=figure_labels,
                figure_descriptions=figure_descriptions,
                remaining_ids=remaining_ids,
                existing_figure_ids=existing_ids,
                next_index=next_index,
            )
            existing_ids.add(figure_id)
            if figure_id in remaining_ids:
                remaining_ids.remove(figure_id)
            next_index += 1

            file_name = f"rendered-{page_num + 1}-{figure_id}.png"
            file_path = output_dir / file_name
            file_path.write_bytes(image_bytes)
            figures.append({
                "figure_id": figure_id,
                "figure_url": f"{args.url_prefix}/{file_name}",
                "figure_description": description,
                "storage_key": None,
                "local_path": str(file_path),
            })
    except Exception as exc:
        errors.append({
            "error_type": "LOCAL_FIGURE_EXTRACT_ERROR",
            "error_message": str(exc),
            "is_recoverable": True,
        })
    finally:
        doc.close()
        if should_cleanup:
            try:
                os.remove(pdf_local_path)
            except OSError:
                pass

    print(json.dumps({"figures": figures, "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
