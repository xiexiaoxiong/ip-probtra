from __future__ import annotations

import re
from bs4 import BeautifulSoup

from product_search.models import CandidateLink
from product_search.platforms import detect_platform, is_detail_url, normalize_url


def extract_detail_candidates_from_discovery_page(
    *,
    html: str,
    page_url: str,
    keyword: str,
    platform: str,
    limit: int,
) -> list[CandidateLink]:
    if platform == "jd" or detect_platform(page_url) == "jd":
        return _extract_jd_detail_candidates(html=html, page_url=page_url, keyword=keyword, limit=limit)
    return _extract_anchor_detail_candidates(
        html=html,
        page_url=page_url,
        keyword=keyword,
        platform=platform,
        source="discovery_page",
        limit=limit,
    )


def _extract_anchor_detail_candidates(
    *,
    html: str,
    page_url: str,
    keyword: str,
    platform: str,
    source: str,
    limit: int,
) -> list[CandidateLink]:
    soup = BeautifulSoup(html or "", "lxml")
    results: list[CandidateLink] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = normalize_url(anchor.get("href") or "", page_url)
        if not href or href in seen or not is_detail_url(href, platform):
            continue
        seen.add(href)
        text = " ".join(anchor.get_text(" ", strip=True).split())
        results.append(
            CandidateLink(
                keyword=keyword,
                platform=platform,
                candidate_url=href,
                title=text[:500],
                snippet=f"discovered_from={page_url}",
                source=source,
                rank=len(results) + 1,
            )
        )
        if len(results) >= limit:
            break
    return results


def _extract_jd_detail_candidates(
    *,
    html: str,
    page_url: str,
    keyword: str,
    limit: int,
) -> list[CandidateLink]:
    soup = BeautifulSoup(html or "", "lxml")
    results: list[CandidateLink] = []
    seen: set[str] = set()

    def add(url: str, title: str = "") -> None:
        normalized = normalize_url(url, page_url)
        if not normalized or normalized in seen or not is_detail_url(normalized, "jd"):
            return
        seen.add(normalized)
        results.append(
            CandidateLink(
                keyword=keyword,
                platform="jd",
                candidate_url=normalized,
                title=" ".join((title or "").split())[:500],
                snippet=f"discovered_from={page_url}",
                source="jd_discovery_page",
                rank=len(results) + 1,
            )
        )

    for item in soup.select("[data-sku]"):
        sku = str(item.get("data-sku") or "").strip()
        if re.fullmatch(r"\d{6,}", sku):
            title = item.get_text(" ", strip=True)
            add(f"https://item.jd.com/{sku}.html", title)
        if len(results) >= limit:
            return results[:limit]

    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        title = anchor.get_text(" ", strip=True)
        add(href, title)
        if len(results) >= limit:
            return results[:limit]

    for match in re.finditer(r"item\.jd\.com/(\d{6,})\.html", html or "", re.I):
        add(f"https://item.jd.com/{match.group(1)}.html")
        if len(results) >= limit:
            break
    return results[:limit]

