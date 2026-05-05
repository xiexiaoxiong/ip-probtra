import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)

BRIGHTDATA_REQUEST_URL = "https://api.brightdata.com/request"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)


def _is_valid_product_image_url_static(url: str) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    if any(x in url_lower for x in ['logo', 'icon', 'avatar', 'sprite', 'placeholder', 'loading', 'empty', 'default']):
        return False
    if re.search(r'tps-\d{1,2}-\d{1,2}', url):
        return False
    if re.search(r'[_/](\d{1,2}x\d{1,2})', url):
        return False
    return True


def _normalize_image_url_static(url: str) -> str:
    if not url:
        return url
    if url.endswith('.jpg_'):
        url = url[:-1]
    if re.search(r'\.jpg_[^/]*$', url):
        url = re.sub(r'\.jpg_.*$', '.jpg', url)
    if '.webp' in url:
        url = url.replace('.webp', '.jpg')
    return url


@dataclass
class SearchImageRef:
    url: str = ""


@dataclass
class SearchImageItem:
    url: str = ""
    image: SearchImageRef = field(default_factory=SearchImageRef)


@dataclass
class SearchWebItem:
    title: str = ""
    url: str = ""
    snippet: str = ""
    content: str = ""


@dataclass
class SearchResponse:
    web_items: List[SearchWebItem] = field(default_factory=list)
    image_items: List[SearchImageItem] = field(default_factory=list)


class SearchProvider:
    """
    China-first search provider.

    Current implementation:
    - Bright Data proxy + Baidu web search for Chinese public-web discovery
    - Bright Data Unlocker API for detail-page fetch (with JS rendering)
    - Baidu SERP markdown parsing for search result extraction
    - Domain routing for 1688 / Pinduoduo / JD / Taobao supplemental search

    Uses Bright Data with data_format=markdown to get JS-rendered Baidu SERP
    results, then parses the markdown to extract titles, URLs, and snippets.
    """

    def __init__(self, ctx: Any = None):
        self.ctx = ctx
        self.provider = os.getenv("SEARCH_PROVIDER", "brightdata_baidu").strip().lower()
        self.country = os.getenv("SEARCH_COUNTRY", "cn").strip().lower() or "cn"
        self.timeout_seconds = float(os.getenv("SEARCH_TIMEOUT_SECONDS", "45"))
        self.brightdata_api_key = os.getenv("BRIGHTDATA_API_KEY", "").strip()
        self.brightdata_serp_zone = os.getenv("BRIGHTDATA_SERP_ZONE", "serp_api1").strip()
        self.brightdata_unlocker_zone = os.getenv("BRIGHTDATA_UNLOCKER_ZONE", "web_unlocker1").strip()
        self.allow_direct_fetch_fallback = (
            os.getenv("SEARCH_ALLOW_DIRECT_FETCH_FALLBACK", "1").strip().lower() != "0"
        )

    def search(
        self,
        query: str,
        search_type: str = "web",
        count: int = 10,
        need_content: bool = False,
        sites: str = "",
    ) -> SearchResponse:
        if search_type != "web":
            raise ValueError(f"Unsupported search_type: {search_type}")

        response = self.web_search(query=query, count=count, sites=sites)
        if need_content:
            hydrated_items: List[SearchWebItem] = []
            for item in response.web_items:
                content = item.content
                if not content and item.url:
                    content = self.fetch_page_content(item.url, query_hint=item.title or query)
                hydrated_items.append(
                    SearchWebItem(
                        title=item.title,
                        url=item.url,
                        snippet=item.snippet,
                        content=content or "",
                    )
                )
            response.web_items = hydrated_items
        return response

    def web_search(self, query: str, count: int = 10, sites: str = "") -> SearchResponse:
        if self.provider not in {"brightdata_baidu", "brightdata"}:
            raise RuntimeError(
                f"Unsupported SEARCH_PROVIDER={self.provider}. "
                "Current implementation supports brightdata_baidu."
            )
        return self._brightdata_baidu_web_search(query=query, count=count, sites=sites)

    def image_search(self, query: str, count: int = 10) -> SearchResponse:
        image_items: List[SearchImageItem] = []
        seen_urls: set[str] = set()

        params = {
            "tn": "baiduimage",
            "word": query,
            "pn": "0",
            "rn": str(max(1, min(count, 60))),
        }
        url = "https://image.baidu.com/search/index?" + urllib.parse.urlencode(params)

        try:
            raw_html = self._brightdata_fetch_raw(zone=self.brightdata_serp_zone, url=url)
        except Exception as exc:
            logger.warning("Baidu image search fetch failed for '%s': %s", query, exc)
            return SearchResponse(image_items=[])

        if not raw_html:
            return SearchResponse(image_items=[])

        img_url_pattern = re.compile(r'"thumbURL"\s*:\s*"((?:https?:)?//[^"]+)"')
        matches = img_url_pattern.findall(raw_html)

        for img_url in matches:
            if img_url.startswith("//"):
                img_url = "https:" + img_url
            if img_url and img_url not in seen_urls and _is_valid_product_image_url_static(img_url):
                seen_urls.add(img_url)
                normalized = _normalize_image_url_static(img_url)
                image_items.append(SearchImageItem(url=normalized, image=SearchImageRef(url=normalized)))
            if len(image_items) >= count:
                break

        logger.info("Baidu image search found %d images for query: %s", len(image_items), query)
        return SearchResponse(image_items=image_items)

    def ecommerce_search(self, platform: str, keyword: str, count: int = 30) -> SearchResponse:
        """Directly search an ecommerce platform.
        
        Uses Bright Data Unlocker for platforms that work (Amazon, Alibaba, eBay).
        For 1688/Taobao, returns empty (use SERP fallback in search_products_node).
        """
        platform = platform.strip().lower()
        
        # 1688 and Taobao are SPA with heavy anti-bot, skip direct search
        if platform in ("1688", "taobao", "tmall", "jd", "pinduoduo"):
            logger.info("Skipping direct search for %s (anti-bot protected, use SERP fallback)", platform)
            return SearchResponse(web_items=[])
            
        parsers = {
            "amazon": self._parse_amazon_search,
            "alibaba": self._parse_alibaba_search,
            "ebay": self._parse_ebay_search,
        }
        parser = parsers.get(platform)
        if not parser:
            raise ValueError(f"Unsupported ecommerce platform: {platform}")

        url = self._build_ecommerce_search_url(platform, keyword)
        if not url:
            return SearchResponse(web_items=[])

        try:
            raw_html = self._brightdata_unlocker_fetch_raw(url)
        except Exception as exc:
            logger.warning("Unlocker fetch failed for %s search '%s': %s", platform, keyword, exc)
            return SearchResponse(web_items=[])

        if not raw_html:
            logger.warning("Unlocker returned empty content for %s search '%s'", platform, keyword)
            return SearchResponse(web_items=[])

        logger.info("Unlocker fetched %d bytes for %s search '%s'", len(raw_html), platform, keyword)

        items = parser(raw_html, keyword)
        logger.info("Direct %s search parsed %d products for query: %s", platform, len(items), keyword)
        return SearchResponse(web_items=items[:count])

    def _build_ecommerce_search_url(self, platform: str, keyword: str) -> str:
        encoded = urllib.parse.quote(keyword, safe="")
        urls = {
            "1688": f"https://s.1688.com/selloffer/offer_search.htm?keywords={encoded}&n=y&netType=1%2C11",
            "jd": f"https://search.jd.com/Search?keyword={encoded}&enc=utf-8",
            "pinduoduo": f"https://mobile.yangkeduo.com/proxy/api/search?keyword={encoded}",
            "taobao": f"https://s.taobao.com/search?q={encoded}",
            "tmall": f"https://list.tmall.com/search_product.htm?q={encoded}",
            "amazon": f"https://www.amazon.com/s?k={encoded}",
            "alibaba": f"https://www.alibaba.com/trade/search?SearchText={encoded}",
            "ebay": f"https://www.ebay.com/sch/i.html?_nkw={encoded}",
        }
        return urls.get(platform, "")

    def _brightdata_unlocker_fetch_raw(self, url: str) -> str:
        payload = self._brightdata_request(zone=self.brightdata_unlocker_zone, url=url, data_format="raw")
        body = payload.get("body", "")
        if isinstance(body, str):
            return body
        return str(body or "")

    @staticmethod
    def _parse_1688_search(html: str, keyword: str) -> List[SearchWebItem]:
        items: List[SearchWebItem] = []
        seen_urls: set[str] = set()
        price_pattern = re.compile(r'[¥￥]?\s*(\d+\.?\d*)')

        # Match product links - flexible pattern
        link_pattern = re.compile(
            r'<a[^>]*href="((?:https?:)?//(?:detail|m)\.1688\.com/offer/\d+[^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        
        # Match images
        img_pattern = re.compile(r'(?:src|data-lazy|data-lazy-src|data-src)="((?:https?:)?//[^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"', re.DOTALL | re.IGNORECASE)

        for match in link_pattern.finditer(html):
            url = match.group(1).strip()
            if not url.startswith("http"):
                url = "https:" + url
            if url in seen_urls:
                continue
            seen_urls.add(url)

            raw_title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            raw_title = re.sub(r'\s+', ' ', raw_title)
            if len(raw_title) < 4:
                continue

            start = max(0, match.start() - 500)
            end = min(len(html), match.end() + 500)
            nearby = html[start:end]

            price = 0.0
            price_match = price_pattern.search(nearby)
            if price_match:
                try:
                    price = float(price_match.group(1))
                except ValueError:
                    pass

            image_url = ""
            for img_match in img_pattern.finditer(nearby):
                candidate = img_match.group(1)
                if "cbu01.alicdn.com" in candidate or "img.alicdn.com" in candidate:
                    image_url = candidate
                    if not image_url.startswith("http"):
                        image_url = "https:" + image_url
                    break

            snippet = f"¥{price} {keyword}".strip() if price > 0 else keyword
            item = SearchWebItem(title=raw_title[:300], url=url, snippet=snippet, content="")
            item._raw = {"image": image_url, "price": price}
            items.append(item)

        return items

    @staticmethod
    def _parse_taobao_search(html: str, keyword: str) -> List[SearchWebItem]:
        items: List[SearchWebItem] = []
        seen_urls: set[str] = set()
        price_pattern = re.compile(r'[¥￥]?\s*(\d+\.?\d*)')

        # Match product links from rendered DOM
        link_pattern = re.compile(
            r'<a[^>]*href="((?:https?:)?//(?:item|detail)\.taobao\.com/item\.htm\?id=\d+[^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        img_pattern = re.compile(r'(?:src|data-lazy|data-src)="((?:https?:)?//[^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"', re.DOTALL | re.IGNORECASE)

        for match in link_pattern.finditer(html):
            url = match.group(1).strip()
            if not url.startswith("http"):
                url = "https:" + url
            if url in seen_urls:
                continue
            seen_urls.add(url)

            raw_title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            raw_title = re.sub(r'\s+', ' ', raw_title)
            if len(raw_title) < 4:
                continue

            start = max(0, match.start() - 500)
            end = min(len(html), match.end() + 500)
            nearby = html[start:end]

            price = 0.0
            price_match = price_pattern.search(nearby)
            if price_match:
                try:
                    price = float(price_match.group(1))
                except ValueError:
                    pass

            image_url = ""
            for img_match in img_pattern.finditer(nearby):
                candidate = img_match.group(1)
                if "img.alicdn.com" in candidate or "gd1.alicdn.com" in candidate or "imgextra" in candidate:
                    image_url = candidate
                    if not image_url.startswith("http"):
                        image_url = "https:" + image_url
                    break

            snippet = f"¥{price} {keyword}".strip() if price > 0 else keyword
            item = SearchWebItem(title=raw_title[:300], url=url, snippet=snippet, content="")
            item._raw = {"image": image_url, "price": price}
            items.append(item)

        return items

    @staticmethod
    def _parse_jd_search(html: str, keyword: str) -> List[SearchWebItem]:
        items: List[SearchWebItem] = []
        seen_urls: set[str] = set()
        price_pattern = re.compile(r'[¥￥]?\s*(\d+\.?\d*)')

        link_pattern = re.compile(
            r'<a[^>]*href="((?:https?:)?//item\.jd\.com/\d+\.html[^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        img_pattern = re.compile(r'(?:src|data-lazy-img|data-lazy|data-src)="((?:https?:)?//[^"]*\.(?:jpg|jpeg|png|webp)[^"]*)"', re.DOTALL | re.IGNORECASE)

        for match in link_pattern.finditer(html):
            url = match.group(1).strip()
            if not url.startswith("http"):
                url = "https:" + url
            if url in seen_urls:
                continue
            seen_urls.add(url)

            raw_title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            raw_title = re.sub(r'\s+', ' ', raw_title)
            if len(raw_title) < 4:
                continue

            start = max(0, match.start() - 500)
            end = min(len(html), match.end() + 500)
            nearby = html[start:end]

            price = 0.0
            price_match = price_pattern.search(nearby)
            if price_match:
                try:
                    price = float(price_match.group(1))
                except ValueError:
                    pass

            image_url = ""
            for img_match in img_pattern.finditer(nearby):
                candidate = img_match.group(1)
                if "img14.360buyimg.com" in candidate or "img10.360buyimg.com" in candidate or "img13.360buyimg.com" in candidate:
                    image_url = candidate
                    if not image_url.startswith("http"):
                        image_url = "https:" + image_url
                    break

            snippet = f"¥{price} {keyword}".strip() if price > 0 else keyword
            item = SearchWebItem(title=raw_title[:300], url=url, snippet=snippet, content="")
            item._raw = {"image": image_url, "price": price}
            items.append(item)

        return items

    @staticmethod
    def _parse_pinduoduo_search(html: str, keyword: str) -> List[SearchWebItem]:
        items: List[SearchWebItem] = []
        seen_urls: set[str] = set()
        price_pattern = re.compile(r'[¥￥]?\s*(\d+\.?\d*)')

        if '"goodsList"' in html or 'goods_list' in html:
            try:
                json_match = re.search(r'"goodsList"\s*:\s*(\[.*?\])\s*[,}\]]', html, re.DOTALL)
                if json_match:
                    goods_list = json.loads(json_match.group(1))
                    for goods in goods_list:
                        if not isinstance(goods, dict):
                            continue
                        goods_id = goods.get("goodsId", goods.get("goods_sn", ""))
                        if not goods_id:
                            continue
                        url = f"https://mobile.yangkeduo.com/goods.html?goods_id={goods_id}"
                        if url in seen_urls:
                            continue
                        seen_urls.add(url)

                        title = goods.get("goodsName", goods.get("goods_name", ""))
                        if not title or len(title) < 4:
                            continue

                        price = 0.0
                        for price_key in ["minGroupPrice", "min_price", "price", "normalPrice"]:
                            if price_key in goods:
                                try:
                                    price = float(goods[price_key]) / 100.0
                                    break
                                except (ValueError, TypeError):
                                    pass

                        image_url = goods.get("thumbUrl", goods.get("imageUrl", goods.get("goodsImageUrl", "")))
                        snippet = f"¥{price} {keyword}".strip() if price > 0 else keyword

                        item = SearchWebItem(title=title[:300], url=url, snippet=snippet, content="")
                        item._raw = {"image": image_url, "price": price}
                        items.append(item)
            except (json.JSONDecodeError, AttributeError):
                pass

        if not items:
            link_pattern = re.compile(r'goods_id=(\d+)', re.IGNORECASE)
            for match in link_pattern.finditer(html):
                goods_id = match.group(1)
                url = f"https://mobile.yangkeduo.com/goods.html?goods_id={goods_id}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                start = max(0, match.start() - 200)
                end = min(len(html), match.end() + 200)
                nearby = html[start:end]
                title_match = re.search(r'"goodsName"\s*:\s*"([^"]+)"', nearby)
                raw_title = title_match.group(1) if title_match else f"商品 {goods_id}"
                if len(raw_title) < 4:
                    continue

                price = 0.0
                price_match = price_pattern.search(nearby)
                if price_match:
                    try:
                        price = float(price_match.group(1))
                    except ValueError:
                        pass

                snippet = f"¥{price} {keyword}".strip() if price > 0 else keyword
                item = SearchWebItem(title=raw_title[:300], url=url, snippet=snippet, content="")
                item._raw = {"image": "", "price": price}
                items.append(item)

        return items

    @staticmethod
    def _parse_amazon_search(html: str, keyword: str) -> List[SearchWebItem]:
        items: List[SearchWebItem] = []
        seen_urls: set[str] = set()
        price_pattern = re.compile(r'[\$€£]?\s*(\d+\.?\d*)')

        product_pattern = re.compile(
            r'<div[^>]*data-asin="([A-Z0-9]{10})"[^>]*class="[^"]*s-result-item[^"]*"(.*?)</div>\s*</div>\s*</div>',
            re.DOTALL | re.IGNORECASE,
        )

        for match in product_pattern.finditer(html):
            asin = match.group(1)
            if not asin or asin == "":
                continue
            block = match.group(2)

            link_match = re.search(r'<a[^>]*href="(/dp/[^"]*|/gp/product/[^"]*)"', block, re.IGNORECASE)
            if not link_match:
                continue

            url = link_match.group(1)
            if not url.startswith("http"):
                url = f"https://www.amazon.com{url}"
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title_match = re.search(r'<[^>]*class="[^"]*a-text[^"]*"[^>]*>(.*?)</span>', block, re.DOTALL)
            if not title_match:
                title_match = re.search(r'<h2[^>]*>(.*?)</h2>', block, re.DOTALL)
            if not title_match:
                continue

            raw_title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            raw_title = re.sub(r'\s+', ' ', raw_title)
            if len(raw_title) < 4:
                continue

            price = 0.0
            price_match = price_pattern.search(block)
            if price_match:
                try:
                    price = float(price_match.group(1))
                except ValueError:
                    pass

            img_match = re.search(r'<img[^>]*src="(https://m\.media-amazon\.com/images/[^"]*)"', block)
            image_url = img_match.group(1) if img_match else ""

            price_str = f"${price}" if price > 0 else ""
            snippet = f"{price_str} {keyword}".strip()

            item = SearchWebItem(title=raw_title[:300], url=url, snippet=snippet, content="")
            item._raw = {"image": image_url, "price": price}
            items.append(item)

        if not items:
            link_pattern = re.compile(
                r'<a[^>]*href="(/dp/[A-Z0-9]{10}[^"]*)"[^>]*>(.*?)</a>',
                re.DOTALL | re.IGNORECASE,
            )
            for match in link_pattern.finditer(html):
                url = match.group(1)
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                if not url.startswith("http"):
                    url = f"https://www.amazon.com{url}"

                raw_title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
                raw_title = re.sub(r'\s+', ' ', raw_title)
                if len(raw_title) < 4:
                    continue

                nearby = html[max(0, match.start() - 200):match.end() + 200]
                price = 0.0
                price_match = price_pattern.search(nearby)
                if price_match:
                    try:
                        price = float(price_match.group(1))
                    except ValueError:
                        pass

                price_str = f"${price}" if price > 0 else ""
                snippet = f"{price_str} {keyword}".strip()

                item = SearchWebItem(title=raw_title[:300], url=url, snippet=snippet, content="")
                item._raw = {"image": "", "price": price}
                items.append(item)

        return items

    @staticmethod
    def _parse_alibaba_search(html: str, keyword: str) -> List[SearchWebItem]:
        items: List[SearchWebItem] = []
        seen_urls: set[str] = set()
        price_pattern = re.compile(r'[\$€£]?\s*(\d+\.?\d*)')

        link_pattern = re.compile(
            r'<a[^>]*href="(/product-detail/[^"]*|/trade/search\?[^"]*productId=\d+[^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        for match in link_pattern.finditer(html):
            url = match.group(1)
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if not url.startswith("http"):
                url = f"https://www.alibaba.com{url}"

            raw_title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            raw_title = re.sub(r'\s+', ' ', raw_title)
            if len(raw_title) < 4:
                continue

            nearby = html[max(0, match.start() - 200):match.end() + 200]
            price = 0.0
            price_match = price_pattern.search(nearby)
            if price_match:
                try:
                    price = float(price_match.group(1))
                except ValueError:
                    pass

            img_match = re.search(r'<img[^>]*src="(https://s\.alibabaimages\.com/[^"]*)"', nearby, re.IGNORECASE)
            image_url = img_match.group(1) if img_match else ""

            price_str = f"${price}" if price > 0 else ""
            snippet = f"{price_str} {keyword}".strip()

            item = SearchWebItem(title=raw_title[:300], url=url, snippet=snippet, content="")
            item._raw = {"image": image_url, "price": price}
            items.append(item)

        return items

    @staticmethod
    def _parse_ebay_search(html: str, keyword: str) -> List[SearchWebItem]:
        items: List[SearchWebItem] = []
        seen_urls: set[str] = set()
        price_pattern = re.compile(r'[\$€£]?\s*(\d+\.?\d*)')

        link_pattern = re.compile(
            r'<a[^>]*href="(https://www\.ebay\.com/itm/[^"]*)"[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        for match in link_pattern.finditer(html):
            url = match.group(1)
            if url in seen_urls:
                continue
            seen_urls.add(url)

            raw_title = re.sub(r'<[^>]+>', '', match.group(2)).strip()
            raw_title = re.sub(r'\s+', ' ', raw_title)
            if len(raw_title) < 4:
                continue

            nearby = html[max(0, match.start() - 200):match.end() + 200]
            price = 0.0
            price_match = price_pattern.search(nearby)
            if price_match:
                try:
                    price = float(price_match.group(1))
                except ValueError:
                    pass

            img_match = re.search(r'<img[^>]*src="(https://i\.ebayimg\.com/[^"]*)"', nearby, re.IGNORECASE)
            image_url = img_match.group(1) if img_match else ""

            price_str = f"${price}" if price > 0 else ""
            snippet = f"{price_str} {keyword}".strip()

            item = SearchWebItem(title=raw_title[:300], url=url, snippet=snippet, content="")
            item._raw = {"image": image_url, "price": price}
            items.append(item)

        return items

    _BAIDU_REDIRECT_PATTERN = re.compile(r'baidu\.com/link\?|baidu\.com/baidu\.php\?')

    def fetch_page_content(self, url: str, query_hint: str = "") -> str:
        if not url:
            return ""

        if self._BAIDU_REDIRECT_PATTERN.search(url):
            resolved = self._resolve_single_baidu_redirect(url)
            if resolved and not self._BAIDU_REDIRECT_PATTERN.search(resolved):
                logger.info("Baidu redirect resolved: %s -> %s", url[:80], resolved[:80])
                url = resolved
            else:
                logger.info("Skipping unresolved Baidu redirect: %s", url[:80])
                return ""

        if self.provider in {"brightdata_baidu", "brightdata"} and self.brightdata_api_key:
            try:
                return self._brightdata_unlocker_fetch(url)
            except Exception as exc:
                logger.warning("Bright Data Unlocker fetch failed for %s: %s", url, exc)

        if self.allow_direct_fetch_fallback:
            try:
                return self._direct_fetch(url)
            except Exception as exc:
                logger.warning("Direct fetch fallback failed for %s: %s", url, exc)

        return ""

    def _brightdata_baidu_web_search(self, query: str, count: int, sites: str) -> SearchResponse:
        if not self.brightdata_api_key:
            raise RuntimeError("BRIGHTDATA_API_KEY is not configured")

        query_parts = [query.strip()]
        if sites:
            query_parts.append(f"site:{sites.strip()}")
        baidu_query = " ".join(part for part in query_parts if part)

        params = {
            "wd": baidu_query,
            "rn": str(max(1, min(count, 50))),
        }
        url = "https://www.baidu.com/s?" + urllib.parse.urlencode(params)

        try:
            markdown = self._brightdata_fetch_markdown(zone=self.brightdata_serp_zone, url=url)
        except Exception as exc:
            logger.error("Bright Data markdown fetch failed for query '%s': %s", baidu_query, exc)
            markdown = ""

        if not markdown:
            logger.warning("Bright Data returned empty content for query: %s", baidu_query)
            return SearchResponse(web_items=[])

        web_items = self._parse_baidu_serp_markdown(markdown)
        logger.info("Baidu SERP parsed %d results for query: %s", len(web_items), baidu_query)

        # Resolve Baidu redirect URLs to real target URLs
        redirect_urls = [item.url for item in web_items if "baidu.com/link" in item.url or "baidu.com/baidu.php" in item.url]
        if redirect_urls:
            resolved_map = self._resolve_baidu_redirects(redirect_urls)
            for item in web_items:
                if item.url in resolved_map:
                    resolved_url = resolved_map[item.url]
                    if resolved_url != item.url and "baidu.com/link" not in resolved_url:
                        item.url = resolved_url

        return SearchResponse(web_items=web_items)

    @staticmethod
    def _parse_baidu_serp_markdown(markdown: str) -> List[SearchWebItem]:
        items: List[SearchWebItem] = []
        seen_urls: set[str] = set()

        start_marker = "百度为您找到以下结果"
        start_idx = markdown.find(start_marker)
        if start_idx >= 0:
            markdown = markdown[start_idx:]

        serp_section = markdown
        end_markers = ["相关搜索", "其他人还在搜"]
        earliest_end = len(serp_section)
        for marker in end_markers:
            idx = serp_section.find(marker)
            if 0 < idx < earliest_end:
                earliest_end = idx
        serp_section = serp_section[:earliest_end]

        md_link_pattern = re.compile(r'\[([^\]]*)\]\(([^)]+)\)', re.DOTALL)

        SERP_URL_PATTERNS = [
            'baidu.com/link?',
            'baidu.com/baidu.php?',
            '1688.com',
            'taobao.com',
            'tmall.com',
            'jd.com',
            'pinduoduo.com',
            'amazon.',
            'ebay.',
            'alibaba.com',
        ]

        serp_links = md_link_pattern.findall(serp_section)

        for i, (link_text, link_url) in enumerate(serp_links):
            clean_text = link_text.strip().strip('_').strip('*').strip()
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            clean_text = re.sub(r'\\_', '_', clean_text)

            is_serp_url = any(pat in link_url for pat in SERP_URL_PATTERNS)

            if not clean_text or not link_url:
                continue

            if link_url.startswith('javascript:'):
                continue

            if '![' in clean_text:
                continue

            if 'image.baidu.com' in link_url and 'baike.baidu.com' not in link_url:
                continue

            nav_keywords = ['登录', '注册', '设置', '更多产品', '百度首页', 'hao123',
                            '抗击肺炎', '想在此推广', '百度APP', '手写', '拼音',
                            '输入法', '百度百科', '百度图片', '百度爱采购']
            if any(kw in clean_text for kw in nav_keywords):
                continue

            if len(clean_text) < 4:
                continue

            actual_url = link_url

            if not actual_url.startswith(('http://', 'https://')):
                continue

            if actual_url in seen_urls:
                continue
            seen_urls.add(actual_url)

            snippet = ""
            if i + 1 < len(serp_links):
                current_match = next(md_link_pattern.finditer(serp_section), None)
                all_matches = list(md_link_pattern.finditer(serp_section))
                if i < len(all_matches):
                    end_pos = all_matches[i].end()
                    next_start = all_matches[i + 1].start() if i + 1 < len(all_matches) else len(serp_section)
                    between = serp_section[end_pos:next_start].strip()
                    between = re.sub(r'\[([^\]]*)\]\([^)]+\)', '', between).strip()
                    between = re.sub(r'#+\s*', '', between).strip()
                    between = re.sub(r'\*+', '', between).strip()
                    between = re.sub(r'\s+', ' ', between).strip()
                    if between and len(between) > 10:
                        snippet = between[:200]

            items.append(SearchWebItem(title=clean_text[:300], url=actual_url, snippet=snippet, content=""))

        return items

    @staticmethod
    def _resolve_single_baidu_redirect(url: str, timeout: int = 8) -> Optional[str]:
        """Resolve a single Baidu redirect URL to its real target URL.
        
        Returns the resolved URL if successful, or None if resolution fails
        (timeout, network error, or still a Baidu redirect after resolution).
        """
        import requests as _requests
        try:
            session = _requests.Session()
            session.headers.update({
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            try:
                resp = session.head(url, allow_redirects=True, timeout=timeout)
                return resp.url
            except Exception:
                try:
                    resp = session.get(url, allow_redirects=True, timeout=timeout)
                    return resp.url
                except Exception:
                    return None
        except Exception:
            return None

    @staticmethod
    def _resolve_baidu_redirects(urls: List[str], max_workers: int = 8, timeout: int = 10) -> Dict[str, str]:
        """Resolve Baidu redirect URLs (baidu.com/link?url=XXX) to their real target URLs.

        Uses concurrent HTTP HEAD/GET requests to follow redirects.
        Returns a mapping from original redirect URL to resolved URL.
        """
        if not urls:
            return {}

        redirect_pattern = re.compile(r'baidu\.com/link\?|baidu\.com/baidu\.php\?')
        redirect_urls = [u for u in urls if redirect_pattern.search(u)]
        if not redirect_urls:
            return {}

        import requests as _requests

        resolved: Dict[str, str] = {}

        def _resolve_one(url: str) -> tuple:
            try:
                session = _requests.Session()
                session.headers.update({
                    "User-Agent": DEFAULT_USER_AGENT,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                })
                try:
                    resp = session.head(url, allow_redirects=True, timeout=timeout)
                    return (url, resp.url)
                except Exception:
                    try:
                        resp = session.get(url, allow_redirects=True, timeout=timeout)
                        return (url, resp.url)
                    except Exception:
                        pass
            except Exception:
                pass
            return (url, url)

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_resolve_one, u): u for u in redirect_urls}
                for future in as_completed(futures, timeout=len(redirect_urls) * timeout / max_workers + 10):
                    try:
                        original, resolved_url = future.result(timeout=timeout)
                        resolved[original] = resolved_url
                    except Exception:
                        pass
        except Exception as exc:
            logger.warning("Baidu redirect resolution error: %s", exc)

        successfully_resolved = sum(1 for k, v in resolved.items() if v != k and "baidu.com/link" not in v)
        logger.info(
            "Resolved %d/%d Baidu redirect URLs (%d successful)",
            len(resolved), len(redirect_urls), successfully_resolved,
        )
        return resolved

    def _brightdata_fetch_markdown(self, zone: str, url: str) -> str:
        payload = {
            "zone": zone,
            "url": url,
            "format": "raw",
            "method": "GET",
            "country": self.country,
            "data_format": "markdown",
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            BRIGHTDATA_REQUEST_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.brightdata_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=int(self.timeout_seconds * 2)) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Bright Data markdown fetch failed with HTTP {exc.code}: {error_text[:500]}"
            ) from exc

    def _brightdata_fetch_raw(self, zone: str, url: str) -> str:
        payload = {
            "zone": zone,
            "url": url,
            "format": "raw",
            "method": "GET",
            "country": self.country,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            BRIGHTDATA_REQUEST_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.brightdata_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=int(self.timeout_seconds * 2)) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Bright Data raw fetch failed with HTTP {exc.code}: {error_text[:500]}"
            ) from exc

    def _brightdata_unlocker_fetch(self, url: str) -> str:
        payload = self._brightdata_request(zone=self.brightdata_unlocker_zone, url=url, data_format="markdown")
        body = payload.get("body", "")
        if isinstance(body, str):
            return body
        if isinstance(body, dict):
            return json.dumps(body, ensure_ascii=False)
        return str(body or "")

    def _brightdata_request(self, zone: str, url: str, data_format: str = "") -> Dict[str, Any]:
        payload = {
            "zone": zone,
            "url": url,
            "format": "json",
            "method": "GET",
            "country": self.country,
        }
        if data_format:
            payload["data_format"] = data_format
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            BRIGHTDATA_REQUEST_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.brightdata_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=int(self.timeout_seconds * 2)) as response:
                response_bytes = response.read()
                response_text = response_bytes.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"Bright Data request failed with HTTP {exc.code}: {error_text[:500]}"
            ) from exc

        try:
            cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", response_text)
            return json.loads(cleaned)
        except json.JSONDecodeError:
            cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", response_text)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                raise RuntimeError(f"Bright Data returned non-JSON response (len={len(response_text)}): {response_text[:500]}")

    def _direct_fetch(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="ignore")
        return self._html_to_text(raw)

    @staticmethod
    def _html_to_text(html: str) -> str:
        html = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
        html = re.sub(r"(?is)<style.*?>.*?</style>", " ", html)
        html = re.sub(r"(?is)<[^>]+>", " ", html)
        html = re.sub(r"&nbsp;", " ", html)
        html = re.sub(r"\s+", " ", html)
        return html.strip()

    @staticmethod
    def _extract_candidate_image_urls(item: Dict[str, Any]) -> List[str]:
        candidates: List[str] = []
        for key in ("image", "image_url", "thumbnail", "thumbnail_url", "icon"):
            value = item.get(key)
            if isinstance(value, str) and value.startswith("http"):
                candidates.append(value)

        images = item.get("images")
        if isinstance(images, list):
            for image in images:
                if isinstance(image, str) and image.startswith("http"):
                    candidates.append(image)
                elif isinstance(image, dict):
                    for sub_key in ("url", "image", "image_url"):
                        sub_value = image.get(sub_key)
                        if isinstance(sub_value, str) and sub_value.startswith("http"):
                            candidates.append(sub_value)

        return candidates