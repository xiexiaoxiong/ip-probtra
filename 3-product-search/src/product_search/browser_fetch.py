from __future__ import annotations

import asyncio
from typing import Any

from product_search.config import Settings
from product_search.models import FetchResult
from product_search.platforms import normalize_url


_CHROME_EXECUTABLE = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _error_message(exc: Exception) -> str:
    return (str(exc).strip() or f"{type(exc).__name__}: {exc!r}")[:500]


async def _image_snapshot(page) -> dict[str, Any]:
    return await page.evaluate(
        """
        () => {
          const urls = new Set();
          const add = (raw) => {
            if (!raw || typeof raw !== "string") return;
            const value = raw.trim();
            if (!value || value.startsWith("data:") || value.includes("{{")) return;
            try { urls.add(new URL(value, document.baseURI).href); } catch (_) {}
          };
          const addSrcset = (raw) => {
            String(raw || "").split(",").forEach(part => add(part.trim().split(/\\s+/)[0]));
          };
          document.querySelectorAll("img").forEach(img => {
            ["currentSrc", "src"].forEach(key => add(img[key]));
            ["data-original", "data-src", "data-lazy-src", "data-lazy-img",
             "data-lazyload", "data-ks-lazyload", "data-origin", "data-url",
             "data-thumb", "data-img", "data-imgurl", "_src", "ng-src"]
              .forEach(key => add(img.getAttribute(key)));
            addSrcset(img.getAttribute("srcset"));
            addSrcset(img.getAttribute("data-srcset"));
          });
          document.querySelectorAll("source").forEach(source => {
            add(source.src);
            addSrcset(source.srcset || source.getAttribute("data-srcset"));
          });
          document.querySelectorAll("[style]").forEach(element => {
            const value = getComputedStyle(element).backgroundImage || element.style.backgroundImage || "";
            for (const match of value.matchAll(/url\\(["']?(.*?)["']?\\)/g)) add(match[1]);
          });
          const images = Array.from(document.images || []);
          return {
            urls: Array.from(urls),
            domImageCount: images.length,
            loadedImageCount: images.filter(img => img.complete && img.naturalWidth > 0).length,
            height: Math.max(document.body?.scrollHeight || 0, document.documentElement?.scrollHeight || 0),
            readyState: document.readyState,
          };
        }
        """
    )


async def _activate_product_galleries(page) -> int:
    """Hover/click thumbnail candidates so galleries that swap the hero image expose every URL."""
    selectors = [
        "#spec-list img", ".lh li img", ".preview-list img", ".sku-image img",
        ".tb-thumb img", "#J_UlThumb img", ".tm-clear li img", ".gallery img",
        "[class*='thumbnail'] img", "[class*='thumb-list'] img",
    ]
    activated = 0
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(await locator.count(), 80)
            for index in range(count):
                item = locator.nth(index)
                try:
                    await item.scroll_into_view_if_needed(timeout=800)
                    await item.hover(timeout=800)
                    activated += 1
                    await page.wait_for_timeout(120)
                except Exception:
                    continue
        except Exception:
            continue
    return activated


async def _wait_until_images_stable(page, settings: Settings) -> dict[str, Any]:
    all_urls: set[str] = set()
    stable_rounds = 0
    previous_signature: tuple[int, int] | None = None
    rounds = 0
    started = asyncio.get_running_loop().time()
    timeout = settings.browser_timeout_seconds

    while rounds < settings.browser_max_scroll_rounds:
        rounds += 1
        snapshot = await _image_snapshot(page)
        all_urls.update(snapshot.get("urls") or [])
        signature = (int(snapshot.get("height") or 0), len(all_urls))
        if signature == previous_signature:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_signature = signature
        if stable_rounds >= settings.browser_stable_rounds:
            break
        if asyncio.get_running_loop().time() - started >= timeout:
            break

        viewport_height = await page.evaluate("() => window.innerHeight || 900")
        current_y = await page.evaluate("() => window.scrollY || 0")
        height = max(int(snapshot.get("height") or 0), int(viewport_height))
        next_y = min(height, int(current_y) + max(500, int(viewport_height * 0.75)))
        if next_y >= height - 5:
            await page.evaluate("() => window.scrollTo(0, 0)")
        else:
            await page.evaluate("y => window.scrollTo(0, y)", next_y)
        await page.wait_for_timeout(650)

    await page.evaluate("() => window.scrollTo(0, 0)")
    await page.wait_for_timeout(300)
    final_snapshot = await _image_snapshot(page)
    all_urls.update(final_snapshot.get("urls") or [])
    return {
        "image_urls": sorted(all_urls),
        "image_url_count": len(all_urls),
        "dom_image_count": final_snapshot.get("domImageCount", 0),
        "loaded_image_count": final_snapshot.get("loadedImageCount", 0),
        "scroll_height": final_snapshot.get("height", 0),
        "stable_rounds": stable_rounds,
        "scroll_rounds": rounds,
        "stopped_because_stable": stable_rounds >= settings.browser_stable_rounds,
    }


def _append_captured_images(html: str, image_urls: list[str]) -> str:
    if not image_urls:
        return html
    # The final DOM normally already contains these URLs. This block preserves URLs from
    # gallery swaps/lazy nodes that disappeared before page.content() was taken.
    escaped = "".join(
        f'<img data-browser-captured="1" src="{url.replace("&", "&amp;").replace(chr(34), "&quot;")}">'
        for url in image_urls
    )
    marker = f'<div id="product-search-browser-captured-images" style="display:none">{escaped}</div>'
    return html.replace("</body>", marker + "</body>") if "</body>" in html else html + marker


async def fetch_with_browser(url: str, settings: Settings) -> FetchResult:
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return FetchResult(
            ok=False, url=url, final_url=url, provider="local_browser",
            error_message=f"Playwright unavailable: {_error_message(exc)}",
        )

    playwright = None
    browser = None
    context = None
    page = None
    try:
        playwright = await async_playwright().start()
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-first-run",
            ],
        }
        try:
            browser = await playwright.chromium.launch(channel="chrome", **launch_kwargs)
        except Exception:
            browser = await playwright.chromium.launch(executable_path=_CHROME_EXECUTABLE, **launch_kwargs)
        context = await browser.new_context(
            user_agent=settings.user_agent,
            locale="zh-CN",
            viewport={"width": 1440, "height": 1000},
            extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"},
        )
        page = await context.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        response = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=settings.browser_timeout_seconds * 1000,
        )
        try:
            await page.wait_for_load_state("networkidle", timeout=min(12_000, settings.browser_timeout_seconds * 1000))
        except Exception:
            pass
        await page.wait_for_timeout(800)
        gallery_items_activated = await _activate_product_galleries(page)
        stability = await _wait_until_images_stable(page, settings)
        html = await page.content()
        html = _append_captured_images(html, stability.pop("image_urls", []))
        final_url = normalize_url(page.url or url)
        return FetchResult(
            ok=bool(html.strip()),
            url=url,
            final_url=final_url,
            status_code=response.status if response else 0,
            html=html,
            provider="local_browser_stable",
            capture_meta={**stability, "gallery_items_activated": gallery_items_activated},
        )
    except Exception as exc:
        return FetchResult(
            ok=False, url=url, final_url=url, provider="local_browser",
            error_message=_error_message(exc),
        )
    finally:
        if page is not None:
            try:
                await asyncio.shield(page.close())
            except Exception:
                pass
        if context is not None:
            try:
                await asyncio.shield(context.close())
            except Exception:
                pass
        if browser is not None:
            try:
                await asyncio.shield(browser.close())
            except Exception:
                pass
        if playwright is not None:
            try:
                await asyncio.shield(playwright.stop())
            except Exception:
                pass
