from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


def identity(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if "imgservice.suning.cn" in parsed.netloc and "/uimg1/b2c/image/" in path:
        path = re.sub(r"(\.(?:jpg|jpeg|png|webp))(?:_[^/]*)$", r"\1", path, flags=re.I)
    return f"{parsed.netloc.lower()}{path.lower()}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify that every visible marketplace gallery image was captured.")
    parser.add_argument("results", nargs="+", type=Path)
    args = parser.parse_args()

    reports = []
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Chrome/126"}) as client:
        for result_path in args.results:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            product = payload["products"][0]
            url = product["final_url"]
            response = client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            gallery_urls: list[str] = []
            for image in soup.select(".imgzoom-thumb-main img, #spec-list img, #J_UlThumb img, .tb-thumb img"):
                raw = (
                    image.get("src-large")
                    or image.get("data-src-large")
                    or image.get("data-original")
                    or image.get("data-src")
                    or image.get("src")
                    or ""
                )
                normalized = urljoin(url, str(raw))
                if normalized and identity(normalized) not in {identity(item) for item in gallery_urls}:
                    gallery_urls.append(normalized)
            captured = list(product.get("picture") or [])
            captured_ids = {identity(item) for item in captured}
            missing = [item for item in gallery_urls if identity(item) not in captured_ids]
            reports.append(
                {
                    "keyword": payload.get("keywords", [""])[0],
                    "run_id": payload.get("product_detail_search_run_id"),
                    "product": product.get("product_name"),
                    "url": url,
                    "page_gallery_count": len(gallery_urls),
                    "captured_image_count": len(captured),
                    "captured_gallery_count": len(gallery_urls) - len(missing),
                    "missing_gallery_images": missing,
                    "historical_fallback": bool((product.get("quality_flags") or {}).get("historical_fallback")),
                }
            )
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 1 if any(report["missing_gallery_images"] for report in reports) else 0


if __name__ == "__main__":
    raise SystemExit(main())
