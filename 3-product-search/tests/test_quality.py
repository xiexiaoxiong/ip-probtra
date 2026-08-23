import asyncio

import pytest

from product_search.brightdata import BrightDataClient, parse_suning_search_results
from product_search.browser_fetch import fetch_with_browser
from product_search.models import CandidateLink, FetchResult, ProductResult, ProductSearchInput
from product_search.discovery import extract_detail_candidates_from_discovery_page
from product_search.parser import parse_product_page
from product_search.platforms import build_search_query_plans, canonicalize_product_url, derive_title_product_keywords, expand_ecommerce_keyword_variants, is_aggregate_url, is_detail_url, normalize_url
from product_search.quality import evaluate_product_detail
from product_search.service import ProductSearchService
from product_search.config import Settings
from product_search.models import SearchQueryPlan
from product_search.special_sources import render_xiaomi_crowdfunding_html


def _make_service() -> ProductSearchService:
    return ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )


def test_platform_url_filters_reject_listing_pages():
    assert is_detail_url("https://detail.1688.com/offer/123456.html", "1688")
    assert is_detail_url("https://item.jd.com/100012345.html", "jd")
    assert is_aggregate_url("https://search.jd.com/Search?keyword=扫地机器人")
    assert is_aggregate_url("https://www.jd.com/brand/13182a361beee19dc083.html")
    assert is_aggregate_url("https://www.jd.com/chanpin/53783.html")
    assert not is_detail_url("https://search.jd.com/Search?keyword=扫地机器人", "jd")
    assert is_aggregate_url("https://s.taobao.com/search?q=拖地升降扫地机器人")
    assert is_aggregate_url("https://www.taobao.com/list/product/%E5%8F%AF%E8%B0%83%E8%A7%92%E5%B0%84%E7%81%AF.htm")
    assert normalize_url("#") == ""
    assert normalize_url("/") == ""


def test_external_product_detail_url_filters_are_conservative():
    assert is_detail_url("https://www.medicalexpo.com.cn/prod/euronda/product-68436-949858.html")
    assert is_detail_url("https://www.andemed.com/product/detail/286")
    assert is_detail_url("https://www.cleeraudio.cn/ProductDetails/18")
    assert is_detail_url("https://www.postmall.com.tw/ProductDetail.aspx?uid=123")
    assert is_detail_url("https://consumer.huawei.com/cn/headphones/freelace-pro-2/")
    assert is_detail_url("https://shopee.tw/demo-product-i.288644823.26979442031")
    assert is_detail_url("https://www.linhwa.com/products/142.html")
    assert is_detail_url("http://zfcg.szggzy.com:8081/mall/productdetail.html")
    assert is_detail_url("https://www.johnhouse.tw/SalePage/Index/6594769")
    assert is_detail_url("https://you.163.com/item/detail?id=1690003")
    assert is_detail_url("https://www.waclighting.com.cn/product-detail/45")
    assert is_detail_url("https://www.amazon.com/JARLSTAR-Ceiling-Spotlight-Selectable-Adjustable/dp/B0B2DSP2MY")
    assert is_detail_url("https://m.mi.com/crowdfunding/proddetail/1000557")
    assert is_detail_url("https://www.mi.com/mjrobot")
    assert is_detail_url("https://www.mi.com/roomrobot")
    assert is_detail_url("https://www.midapower.com/zh/60kw-portable-super-ev-charger-fast-dc-charger-station-for-taxi-product/")
    assert is_detail_url("https://th.dreametech.com/zh/pages/the-dreame-d20-ultra-a-premium-robot-vacuum-mop-1")
    assert is_detail_url("https://chinese.alibaba.com/product-detail/Intelligent-Follow-Golf-Carts-Remote-APP-1600531372236.html")
    assert is_detail_url("https://www.areswatt.com/cn/product/utility/35.html")
    assert is_detail_url("https://item.szlcsc.com/5787307.html")
    assert is_detail_url("https://www.ti.com.cn/product/cn/DRV2605/part-details/DRV2605YZFT")
    assert is_detail_url("https://www.awinic.com/cn/productDetail/AW86907FCR")
    assert is_detail_url("https://www.dfrobot.com.cn/goods-4226.html")
    assert not is_detail_url("https://www.murata.com.cn/zh-cn/products/sensor")
    assert not is_detail_url("https://www.areswatt.com/cn/product/residential/")
    assert not is_detail_url("https://www.areswatt.com/cn/product/c-i/")
    assert not is_detail_url("https://www.richtap-haptics.com/product/haptic")
    assert not is_detail_url("https://www.sg-micro.com/cn/products/motor-gate-drivers")
    assert not is_detail_url("https://www.taobao.com/chanpin/0d21bd8275d83b3dadc18ea81b799ad19.html")
    assert not is_detail_url("https://ylbzj.yancheng.gov.cn/module/download/downfile.jsp")
    assert not is_detail_url("https://www.baihewuhan.com/list-xiaodumao.html")
    assert normalize_url("https://") == ""


def test_canonical_product_url_preserves_unknown_identity_params():
    first = canonicalize_product_url(
        "https://www.postmall.com.tw/ProductDetail.aspx?uid=123&utm_source=test"
    )
    second = canonicalize_product_url(
        "https://www.postmall.com.tw/ProductDetail.aspx?uid=456&utm_source=test"
    )

    assert first.endswith("?uid=123")
    assert second.endswith("?uid=456")
    assert first != second

    service = _make_service()
    first_product = ProductResult(
        platform="external_product",
        product_name="商品一",
        product_url="https://www.postmall.com.tw/ProductDetail.aspx?uid=123",
        final_url="https://www.postmall.com.tw/ProductDetail.aspx?uid=123",
    )
    second_product = ProductResult(
        platform="external_product",
        product_name="商品二",
        product_url="https://www.postmall.com.tw/ProductDetail.aspx?uid=456",
        final_url="https://www.postmall.com.tw/ProductDetail.aspx?uid=456",
    )
    assert service._product_dedupe_key(first_product) != service._product_dedupe_key(second_product)


def test_xiaomi_crowdfunding_payload_renders_real_product_page():
    payload = {
        "crowd_funding_info": {
            "project_id": 1000557,
            "project_name": "米家脉冲水枪01",
            "project_desc": "炫酷光效射击联动，多模式击发随机应变，自动吸水。",
            "price": "649",
            "support_num": {"k": "支持人数", "v": "8715人"},
            "company_info": {"company_name": "小米通讯技术有限公司"},
            "support_list": [
                {
                    "support_desc": "炫酷光效射击联动，多模式击发随机应变。",
                    "goods_list": [
                        {
                            "goods_name": "米家脉冲水枪01 白色",
                            "goods_image": "https://cdn.cnbj1.fds.api.mi-img.com/nr-pub/demo.png",
                        }
                    ],
                }
            ],
        },
        "viewContent": [
            {
                "gallery_view": [
                    "https://cdn.cnbj1.fds.api.mi-img.com/mi-mall/gallery.jpg",
                ]
            },
            {
                "desc_tabs_view": [
                    {
                        "name": "商品详情",
                        "tab_content": [
                            {"plain_view": {"img": "https://cdn.cnbj1.fds.api.mi-img.com/mi-mall/detail.jpg?w=1080"}}
                        ],
                    }
                ]
            },
        ],
    }
    url = "https://m.mi.com/crowdfunding/proddetail/1000557"
    html = render_xiaomi_crowdfunding_html(payload, url)
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="xiaomi_crowdfunding_api"), parsed)

    assert decision.accepted
    assert parsed.product_id == "1000557"
    assert parsed.product_name == "米家脉冲水枪01"
    assert parsed.price == "649"
    assert parsed.manufacturer == "小米通讯技术有限公司"
    assert parsed.picture[0].endswith("/demo.png")
    assert "自动吸水" in parsed.description


def test_quality_accepts_same_detail_page_with_text_and_images():
    html = """
    <html><head>
      <title>升降拖地扫地机器人 - 1688</title>
      <meta property="og:image" content="https://cbu01.alicdn.com/img/detail.jpg" />
      <meta name="description" content="这是一款具备拖地、扫地、拖布升降功能的扫地机器人。" />
    </head>
    <body>
      <h1>智能升降拖地扫地机器人</h1>
      <div class="offer-detail">
        品牌：测试品牌 厂家：测试电器有限公司 价格：1299 月销：300
        这款智能升降拖地扫地机器人支持扫地、拖地、自动抬升拖布、越障和自动回充。
        详情介绍包含水箱结构、升降机构、拖布组件、控制方式、传感器和使用场景。
      </div>
      <img src="//cbu01.alicdn.com/img/detail2.jpg" />
    </body></html>
    """
    final_url = "https://detail.1688.com/offer/123456.html"
    parsed = parse_product_page(html, final_url, final_url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=final_url, final_url=final_url, html=html, provider="test"), parsed)
    assert decision.accepted
    assert decision.flags["same_url_assets"] is True
    assert parsed.product_name == "智能升降拖地扫地机器人"
    assert parsed.picture


def test_parser_accepts_external_product_image_urls_without_file_extension():
    html = """
    <html><head>
      <title>立方翻转计时器</title>
      <meta property="og:image" content="//img.91app.com/webapi/imagesV3/Original/SalePage/6594769/0/639064217944970000?v=1" />
      <meta name="description" content="立方翻转计时器，重力感应，倒数计时器，桌面时间管理工具。" />
    </head><body>
      <h1>立方翻转计时器</h1>
      <div class="product-detail">商品特色包括重力感应、LED屏幕、倒数计时、音量调节，适合学习办公和厨房烘焙使用。</div>
    </body></html>
    """
    parsed = parse_product_page(html, "https://www.johnhouse.tw/SalePage/Index/6594769", "https://www.johnhouse.tw/SalePage/Index/6594769")
    assert parsed.picture == ["https://img.91app.com/webapi/imagesV3/Original/SalePage/6594769/0/639064217944970000?v=1"]


def test_parser_prefers_lazy_product_images_over_placeholders():
    html = """
    <html><head><title>R1智能折叠跑步机</title></head><body>
      <h1>R1智能折叠跑步机</h1>
      <img src="//yanxuan.nosdn.127.net/placeholder.gif"
           data-original="https://yanxuan-item.nosdn.127.net/98e71265bddcbf67def3034e270d8685.jpg?type=webp&imageView&quality=90" />
    </body></html>
    """
    parsed = parse_product_page(html, "https://you.163.com/item/detail?id=1690003", "https://you.163.com/item/detail?id=1690003")
    assert parsed.picture[0].startswith("https://yanxuan-item.nosdn.127.net/98e71265")


def test_parser_extracts_jd_lazy_srcset_and_background_images():
    html = """
    <html><head><title>穿戴音响蓝牙音箱</title></head><body>
      <h1>穿戴音响蓝牙音箱</h1>
      <div class="sku-image">
        <img data-lazy-img="//img10.360buyimg.com/n1/s450x450_jfs/t1/product-main.jpg"
             src="//misc.360buyimg.com/lib/img/e/blank.gif" />
      </div>
      <picture>
        <source srcset="//img11.360buyimg.com/n0/jfs/t1/product-detail.webp 1x, //img12.360buyimg.com/n0/jfs/t1/product-detail-2.webp 2x" />
      </picture>
      <div class="detail-content" style="background-image:url('//img13.360buyimg.com/n0/jfs/t1/product-scene.jpg')">
        商品详情展示佩戴、蓝牙连接、扬声器和户外使用场景。
      </div>
    </body></html>
    """
    url = "https://item.jd.com/100005207111.html"
    parsed = parse_product_page(html, url, url)

    assert "https://img10.360buyimg.com/n1/s450x450_jfs/t1/product-main.jpg" in parsed.picture
    assert "https://img11.360buyimg.com/n0/jfs/t1/product-detail.webp" in parsed.picture
    assert "https://img12.360buyimg.com/n0/jfs/t1/product-detail-2.webp" in parsed.picture
    assert "https://img13.360buyimg.com/n0/jfs/t1/product-scene.jpg" in parsed.picture


def test_parser_does_not_truncate_product_gallery_at_eighteen_images():
    images = "".join(
        f'<img class="product-image" src="https://cdn.example.com/product/gallery-{index}.jpg" />'
        for index in range(1, 33)
    )
    html = f"""
    <html><head><title>完整图库测试商品</title></head><body>
      <main class="product-detail"><h1>完整图库测试商品</h1>{images}</main>
    </body></html>
    """
    url = "https://example.com/products/full-gallery"
    parsed = parse_product_page(html, url, url)

    assert len(parsed.picture) == 32
    assert parsed.picture[-1].endswith("gallery-32.jpg")


def test_parser_extracts_taobao_script_gallery_urls():
    html = """
    <html><head><title>淘宝完整图库测试商品</title></head><body>
      <main class="product-detail"><h1>淘宝完整图库测试商品</h1>
        商品详情包含尺寸、颜色、材质、包装、使用说明、售后服务和完整的商品展示信息。
      </main>
      <script>
        window.__ITEM_DATA__ = {"images":[
          "//gw.alicdn.com/imgextra/i1/123/O1CN-main.jpg",
          "//gw.alicdn.com/imgextra/i2/123/O1CN-side.jpg_640x640q90",
          "//img.alicdn.com/imgextra/i3/123/O1CN-detail"
        ]};
      </script>
    </body></html>
    """
    url = "https://item.taobao.com/item.htm?id=123456789"
    parsed = parse_product_page(html, url, url)

    assert "https://gw.alicdn.com/imgextra/i1/123/O1CN-main.jpg" in parsed.picture
    assert "https://gw.alicdn.com/imgextra/i2/123/O1CN-side.jpg" in parsed.picture
    assert "https://img.alicdn.com/imgextra/i3/123/O1CN-detail" in parsed.picture


def test_incomplete_accepted_page_still_requires_render_until_browser_stable():
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=True,
            render_fallback_enabled=True,
            render_retry_attempts=1,
            user_agent="test",
            browser_fallback_enabled=True,
            incomplete_image_threshold=6,
        )
    )
    url = "https://item.jd.com/100005207111.html"
    html = """
    <html><head><title>测试商品完整名称</title></head><body>
      <main class="product-detail">
        商品详情包含参数、材质、尺寸、功能、使用说明、包装、配送和售后服务。
        产品支持多种使用场景，页面同时介绍安装步骤、操作方式、注意事项、保养方法、
        规格型号、颜色选择、包装清单、质量保障、退换货规则以及生产厂商信息。
        本页展示的是一个可直接购买的完整商品，不是搜索列表、配件页面或宣传首页。
      </main>
      <img src="https://img10.360buyimg.com/n1/jfs/only-one.jpg" />
    </body></html>
    """
    fetch = FetchResult(ok=True, url=url, final_url=url, html=html, provider="direct_fetch")
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(fetch, parsed)

    assert decision.accepted
    assert decision.flags["image_count"] == 1
    assert service._should_retry_render(fetch, decision)

    stable_fetch = fetch.model_copy(
        update={
            "provider": "local_browser_stable",
            "capture_meta": {"stopped_because_stable": True},
        }
    )
    stable_decision = evaluate_product_detail(stable_fetch, parsed)
    assert not service._should_retry_render(stable_fetch, stable_decision)


def test_browser_fetch_scrolls_until_lazy_images_stabilize():
    from urllib.parse import quote

    page_html = """
    <html><head><title>Lazy gallery product</title></head><body style='height:3200px'>
      <h1>Lazy gallery product</h1>
      <div id='gallery'><img src='https://images.invalid/product/lazy-1.jpg'></div>
      <script>
        let added = false;
        window.addEventListener('scroll', () => {
          if (!added && window.scrollY > 500) {
            added = true;
            document.querySelector('#gallery').insertAdjacentHTML(
              'beforeend', "<img src='https://images.invalid/product/lazy-2.jpg'><img src='https://images.invalid/product/lazy-3.jpg'>"
            );
          }
        });
      </script>
    </body></html>
    """
    settings = Settings(
        database_url="",
        brightdata_api_key="",
        brightdata_serp_zone="",
        brightdata_unlocker_zone="",
        brightdata_endpoint="",
        request_timeout_seconds=5,
        max_concurrency=1,
        serp_url_limit=1,
        allow_direct_fetch_fallback=True,
        render_fallback_enabled=True,
        render_retry_attempts=1,
        user_agent="Mozilla/5.0 Chrome/126",
        browser_fallback_enabled=True,
        browser_timeout_seconds=15,
        browser_stable_rounds=2,
        browser_max_scroll_rounds=20,
    )
    result = asyncio.run(fetch_with_browser(f"data:text/html;charset=utf-8,{quote(page_html)}", settings))

    if not result.ok and "browser has been closed" in result.error_message.lower():
        pytest.skip("当前执行沙箱禁止启动本机 Chrome")
    assert result.ok, result.error_message
    assert result.provider == "local_browser_stable"
    assert result.capture_meta["stopped_because_stable"] is True
    assert result.capture_meta["image_url_count"] >= 3
    assert "lazy-3.jpg" in result.html


def test_parser_filters_logo_and_navigation_images_by_context():
    html = """
    <html><head>
      <title>HALO 颈挂音响蓝牙音响</title>
      <meta property="og:image" content="https://www.cleeraudio.cn/static/brand-logo.png" />
    </head><body>
      <header class="site-header">
        <img class="main-logo" src="https://cdn.cleeraudio.cn/assets/cleer-mark.png" />
      </header>
      <main class="product-detail">
        <h1>HALO 颈挂音响蓝牙音响</h1>
        <img class="product-hero" src="https://cdn.cleeraudio.cn/products/halo-neck-speaker.jpg" />
        <img alt="商品佩戴场景" src="https://cdn.cleeraudio.cn/products/halo-wearing-scene.jpg" />
      </main>
    </body></html>
    """
    url = "https://www.cleeraudio.cn/ProductDetails/18"
    parsed = parse_product_page(html, url, url)

    assert "https://cdn.cleeraudio.cn/products/halo-neck-speaker.jpg" in parsed.picture
    assert "https://cdn.cleeraudio.cn/products/halo-wearing-scene.jpg" in parsed.picture
    assert all("logo" not in image.lower() for image in parsed.picture)
    assert all("cleer-mark" not in image.lower() for image in parsed.picture)


def test_parser_extracts_product_images_from_nuxt_scripts_without_ui_assets():
    html = """
    <html><head>
      <title>HALO 颈挂音响蓝牙音响</title>
    </head><body>
      <main class="product-detail">
        <h1>HALO 颈挂音响蓝牙音响</h1>
        <img src="https://www.cleeraudio.cn/_nuxt/img/search.054b125.png" />
        <img src="https://www.cleeraudio.cn/_nuxt/img/cart.32c59b3.png" />
        <img src="https://www.cleeraudio.cn/_nuxt/img/qrcode.9a95fa5.png" />
      </main>
      <script>
        window.__NUXT__ = {
          gallery: [
            "/_nuxt/img/halo-neck-speaker-main.webp",
            "_nuxt/img/halo-neck-speaker-side.jpg",
            "https://www.cleeraudio.cn/_nuxt/img/brand-logo.png"
          ]
        }
      </script>
    </body></html>
    """
    url = "https://www.cleeraudio.cn/ProductDetails/18"
    parsed = parse_product_page(html, url, url)

    assert "https://www.cleeraudio.cn/_nuxt/img/halo-neck-speaker-main.webp" in parsed.picture
    assert "https://www.cleeraudio.cn/_nuxt/img/halo-neck-speaker-side.jpg" in parsed.picture
    assert all("search" not in image.lower() for image in parsed.picture)
    assert all("cart" not in image.lower() for image in parsed.picture)
    assert all("qrcode" not in image.lower() for image in parsed.picture)
    assert all("logo" not in image.lower() for image in parsed.picture)


def test_parser_filters_suning_layout_assets():
    html = """
    <html><head><title>联想无线蓝牙耳机颈挂脖运动耳机</title></head><body>
      <main class="product-detail">
        <h1>联想无线蓝牙耳机颈挂脖运动耳机</h1>
        <img src="https://imgservice.suning.cn/uimg1/b2c/image/6JZmXiIrL3viHnoLyfI0iQ.jpg_800w_800h_4e" />
        <img src="https://res.suning.cn/project/cmsWeb/suning/public/base/public/v3/images/snms.png?v=2021012601" />
        <img src="https://res.suning.cn/project/pdsWeb/csspc2021/images/new_people.png" />
        <img src="https://product.suning.com/pds-web/project/pds/csspc2021/images/gend-finish.gif" />
        <img src="https://res.suning.cn/project/pdsWeb/csspc2017/images/TMreturn-process.jpg?v=2026051922" />
        <img src="https://product.suning.com/images/blank_pic_60.png" />
        <img src="https://product.suning.com/uimg/b2c/newcatentries/{{sku.vendorId}}-{{sku.sugGoodsCode}}_1_100x100.jpg" />
        <img src="https://res.suning.cn/project/pdsWeb//images/trend.png" />
      </main>
    </body></html>
    """
    url = "https://product.suning.com/0070893940/11538453672.html"
    parsed = parse_product_page(html, url, url)

    assert parsed.picture == [
        "https://imgservice.suning.cn/uimg1/b2c/image/6JZmXiIrL3viHnoLyfI0iQ.jpg_800w_800h_4e"
    ]


def test_direct_suning_search_extracts_unique_detail_links():
    html = """
    <html><body>
      <a href="//product.suning.com/0000000000/12450943438.html" title="科沃斯扫地机器人"></a>
      <a href="//product.suning.com/0000000000/12450943438.html">重复链接</a>
      <a href="//product.suning.com/0070893940/11538453672.html">联想蓝牙耳机</a>
      <a href="https://search.suning.com/test/">列表页</a>
    </body></html>
    """
    plan = SearchQueryPlan(
        keyword="扫地机器人",
        original_keyword="扫地机器人",
        platform="suning",
        query="site:product.suning.com 扫地机器人",
        serp_url="",
    )

    candidates = parse_suning_search_results(html, plan, 10)

    assert [item.candidate_url for item in candidates] == [
        "https://product.suning.com/0000000000/12450943438.html",
        "https://product.suning.com/0070893940/11538453672.html",
    ]


def test_parser_filters_footer_qr_images_by_url_path():
    html = """
    <html><head><title>AW86927FCR 有刷直流电机驱动芯片</title></head><body>
      <main class="product-detail">
        <h1>AW86927FCR 有刷直流电机驱动芯片</h1>
        <img src="https://static.szlcsc.com/upload/public/product/source/20240612/AW86927FCR.jpg" />
      </main>
      <footer>
        <img src="https://static.szlcsc.com/ecp/assets/newWeb/footer/gh.png" />
      </footer>
    </body></html>
    """
    url = "https://item.szlcsc.com/5787307.html"
    parsed = parse_product_page(html, url, url)

    assert parsed.picture == ["https://static.szlcsc.com/upload/public/product/source/20240612/AW86927FCR.jpg"]


def test_parser_filters_theme_qr_assets():
    html = """
    <html><head><title>输液接头消毒帽</title></head><body>
      <main class="product-detail">
        <h1>输液接头消毒帽</h1>
        <img src="https://www.andemed.com/upload/storage/35df6aef5c93a4f06cd006b56acc6580.jpg" />
        <img src="https://www.andemed.com/themes/default/assets/img/d12.jpg" />
        <img src="https://www.andemed.com/themes/default/assets/img/er.png" />
      </main>
    </body></html>
    """
    url = "https://www.andemed.com/product/disinfecting-cap"
    parsed = parse_product_page(html, url, url)

    assert parsed.picture == [
        "https://www.andemed.com/upload/storage/35df6aef5c93a4f06cd006b56acc6580.jpg"
    ]


def test_parser_does_not_treat_border_class_or_style_as_order_context():
    html = """
    <html><head><title>边框展示的商品图片</title></head><body>
      <main class="product-detail border-card">
        <h1>边框展示的商品图片</h1>
        <img class="product-image border" style="border: 1px solid #ddd"
             src="https://cdn.example.com/products/main.jpg" />
      </main>
    </body></html>
    """
    url = "https://example.com/products/123"
    parsed = parse_product_page(html, url, url)

    assert parsed.picture == ["https://cdn.example.com/products/main.jpg"]


def test_historical_fallback_rejects_obvious_placeholder_titles():
    service = _make_service()

    assert service._is_obvious_invalid_historical_product(
        ProductResult(
            platform="external_product",
            product_name="产品找不到？ 直接发需求试试！",
            product_url="https://www.iotku.com/Product/862349076807548928.html",
            final_url="https://www.iotku.com/Product/862349076807548928.html",
        )
    )
    assert service._is_obvious_invalid_historical_product(
        ProductResult(
            platform="external_product",
            product_name="Amazon.com",
            product_url="https://www.amazon.com/example/dp/B000000000",
            final_url="https://www.amazon.com/example/dp/B000000000",
        )
    )


def test_quality_rejects_captcha_page():
    html = "<html><title>安全验证</title><body>请完成验证码验证后继续访问</body></html>"
    final_url = "https://detail.1688.com/offer/123456.html"
    parsed = parse_product_page(html, final_url, final_url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=final_url, final_url=final_url, html=html, provider="test"), parsed)
    assert not decision.accepted
    assert decision.flags["blocked"] is True


def test_quality_ignores_login_words_on_rich_external_product_page():
    html = """
    <html><head><title>我，米家扫地机器人</title>
      <meta property="og:image" content="https://i01.appmifile.com/webfile/globalimg/roomrobot.jpg" />
    </head><body>
      <h1>我，米家扫地机器人</h1>
      <div class="detail-content">
        sign in 登录入口位于页面导航栏，但本页面主体是完整商品介绍。
        米家扫地机器人提供路径规划、自动回充、智能清扫、APP远程控制、定时任务、边缘清扫和大吸力吸尘。
        商品详情展示主机、充电座、传感器、尘盒、滤网、电池、滚刷、边刷和移动端控制能力。
        页面包含产品图片、技术参数、包装清单、售后服务、适用房型、清洁策略和日常维护说明。
        该产品是完整扫地机器人本体，不是列表页、搜索页、登录页、验证码页或安全验证页面。
        更多详情包含导航算法、清扫覆盖率、越障能力、续航时间、噪音水平、耗材更换和固件升级说明。
        产品图文说明还介绍了底部结构、传感器布局、充电回座方式、尘盒容量、滤网拆洗和家庭使用场景。
      </div>
      <img src="//i01.appmifile.com/webfile/globalimg/roomrobot2.jpg" />
    </body></html>
    """
    url = "https://www.mi.com/roomrobot"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)

    assert decision.accepted
    assert decision.flags["blocked_ignored_for_rich_external_product"] is True


def test_quality_rejects_not_found_demand_page_title():
    html = """
    <html><head><title>产品找不到？ 直接发需求试试！</title>
      <meta property="og:image" content="https://example.com/request.jpg" />
    </head><body>
      <h1>产品找不到？ 直接发需求试试！</h1>
      <div class="detail-content">
        页面提供需求提交、供应商匹配、采购咨询、发布需求、留下联系方式和等待报价服务。
        文本里虽然可能包含消毒帽、RFID、医疗器械、产品图片、商品参数等关键词，但它不是一个具体商品详情页。
        该页面主要用于撮合供需、提交采购信息、等待客服回访和展示平台服务流程。
      </div>
      <img src="https://example.com/request2.jpg" />
    </body></html>
    """
    url = "https://www.iotku.com/Product/862349076807548928.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)

    assert not decision.accepted
    assert "找不到" in " ".join(decision.reasons)


def test_jd_discovery_page_extracts_detail_links_without_accepting_aggregate_page():
    html = """
    <div id="J_goodsList">
      <ul>
        <li data-sku="10144993891768" class="gl-item">
          <div class="p-name"><a href="//item.jd.com/10144993891768.html">动感单车家用健身车</a></div>
        </li>
        <li data-sku="10087537810000" class="gl-item">
          <a href="//item.jd.com/10087537810000.html">室内脚踏车健身单车</a>
        </li>
      </ul>
    </div>
    """
    candidates = extract_detail_candidates_from_discovery_page(
        html=html,
        page_url="https://www.jd.com/chanpin/53783.html",
        keyword="健身脚踏车",
        platform="jd",
        limit=5,
    )
    assert [candidate.candidate_url for candidate in candidates] == [
        "https://item.jd.com/10144993891768.html",
        "https://item.jd.com/10087537810000.html",
    ]
    assert all(candidate.source == "jd_discovery_page" for candidate in candidates)


def test_parser_recovers_jd_title_and_ignores_shipping_zero_price():
    html = """
    <html><head><title>支持</title></head>
    <body>
      <h1>支持</h1>
      <div>
        超士老人家用功能健身车上下肢脚踏车恢复训练器手脚锻炼机运动器材
        4功能健身车(3年只换不修)图片、价格、品牌样样齐全！
        登录查看更多图片 > 超士老人家用功能健身车上下肢脚踏车恢复训练器手脚锻炼机运动器...
        京 东 价 ￥ 限时特惠 运费 ￥0 好评度 乔山动感单车
      </div>
      <img src="//img11.360buyimg.com/n1/s720x720_jfs/t1/demo.jpg" />
    </body></html>
    """
    parsed = parse_product_page(html, "https://item.jd.com/30147313701.html", "https://item.jd.com/30147313701.html")
    assert parsed.product_name.startswith("超士老人家用功能健身车")
    assert "图片" not in parsed.product_name
    assert parsed.price == ""


def test_parser_rejects_jd_generic_site_title():
    html = """
    <html><head><title>京东(JD.COM)-正品低价、品质保障、配送及时、轻松购物！</title></head>
    <body>
      <div class="detail-content">充电站 充电站 充电站 商品介绍 商品参数 配送服务 售后保障</div>
      <img src="//img11.360buyimg.com/n1/demo.jpg" />
    </body></html>
    """
    parsed = parse_product_page(html, "https://item.jd.com/10104647328355.html", "https://item.jd.com/10104647328355.html")
    assert parsed.product_name == ""


def test_ecommerce_keyword_variants_are_transparent_query_expansion():
    assert "颈挂式蓝牙音箱" in expand_ecommerce_keyword_variants("颈挂式蓝牙音响")
    assert "颈挂式蓝牙耳机" in expand_ecommerce_keyword_variants("颈挂式蓝牙音响")
    variants = expand_ecommerce_keyword_variants("健身脚踏车")
    assert "健身车" in variants
    assert "动感单车" in variants
    timer_variants = expand_ecommerce_keyword_variants("多边形计时器")
    assert "立方体计时器" in timer_variants
    assert "六面计时器" in timer_variants
    assert "翻转计时器" in timer_variants
    assert "扫地机器人" in expand_ecommerce_keyword_variants("免爬坡扫地机器人")
    assert "跑步机" in expand_ecommerce_keyword_variants("省空间跑步机")
    lighting_variants = expand_ecommerce_keyword_variants("免拆调角灯具")
    assert "可调角射灯" in lighting_variants
    assert "可调角筒灯" in lighting_variants
    robot_lift_variants = expand_ecommerce_keyword_variants("拖地升降拖地模式收纳扫地机器人")
    assert "拖布自动抬升扫地机器人" in robot_lift_variants
    assert "扫拖一体自动抬升扫地机器人" in robot_lift_variants
    robot_uv_variants = expand_ecommerce_keyword_variants("基站UV杀菌扫地机器人")
    assert "UV杀菌扫地机器人" in robot_uv_variants
    assert "除菌洗拖布扫地机器人" in robot_uv_variants
    assert "高温除菌洗扫地机器人" in robot_uv_variants


def test_title_product_keywords_keep_product_body_fallback():
    assert derive_title_product_keywords("移动式充电站以及用于确定球类运动器材位置的系统") == ["移动式充电站"]
    assert derive_title_product_keywords("扫地机器人系统及扫地机器人") == ["扫地机器人"]
    assert derive_title_product_keywords("便携式音响设备") == []


def test_search_query_plans_preserve_original_keyword_for_fallback_terms():
    plans = build_search_query_plans(["颈挂式蓝牙音响"], ["jd"], 1)
    fallback = next(plan for plan in plans if plan.keyword == "音响")

    assert fallback.original_keyword == "颈挂式蓝牙音响"


def test_detail_candidate_selection_prefers_required_qualifier_matches():
    service = _make_service()
    candidates = [
        CandidateLink(
            keyword="计时器",
            original_keyword="多边形计时器",
            platform="jd",
            candidate_url="https://item.jd.com/100000000001.html",
            title="厨房电子计时器",
            source="brightdata_serp",
            rank=1,
        ),
        CandidateLink(
            keyword="翻转计时器",
            original_keyword="多边形计时器",
            platform="jd",
            candidate_url="https://item.jd.com/100000000002.html",
            title="六面翻转计时器 重力感应定时器",
            source="brightdata_serp",
            rank=5,
        ),
    ]

    selected = service._select_detail_candidates(candidates, limit=1)

    assert [candidate.candidate_url for candidate in selected] == ["https://item.jd.com/100000000002.html"]


def test_detail_candidate_selection_prefers_marketplace_products_over_external_pages():
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    candidates = [
        CandidateLink(
            keyword="免工具调光灯具",
            platform="jd",
            candidate_url="https://enttec.com.cn/products/led-dimmers/",
            title="LED 调光器",
            source="brightdata_serp",
            rank=1,
        ),
        CandidateLink(
            keyword="可调角筒灯",
            platform="jd",
            candidate_url="https://item.jd.com/72058331086.html",
            title="可调角筒灯",
            source="brightdata_serp",
            rank=6,
        ),
    ]

    selected = service._select_detail_candidates(candidates, limit=1)

    assert [candidate.candidate_url for candidate in selected] == ["https://item.jd.com/72058331086.html"]


def test_detail_candidate_selection_reserves_external_fallback_slot():
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    candidates = [
        CandidateLink(keyword="灯具", platform="jd", candidate_url=f"https://item.jd.com/10000000000{index}.html", title=f"灯具{index}", source="brightdata_serp", rank=index)
        for index in range(1, 5)
    ]
    candidates.append(
        CandidateLink(
            keyword="免拆调角灯具",
            platform="jd",
            candidate_url="https://www.signliteled.com/zh/products/flexible-led-strip/",
            title="SMD LED 灯带",
            snippet="discovered_from=https://www.signliteled.com/zh/special-beam-angle-led-strips-light/",
            source="jd_discovery_page",
            rank=1,
        )
    )
    candidates.append(
        CandidateLink(
            keyword="免拆调角灯具",
            platform="jd",
            candidate_url="https://www.waclighting.com.cn/product-detail/45",
            title="ULTRA 下照调角一体方形无边",
            source="brightdata_serp",
            rank=2,
        )
    )

    selected = service._select_detail_candidates(candidates, limit=3)
    selected_urls = [candidate.candidate_url for candidate in selected]

    assert len(selected) == 3
    assert "https://www.waclighting.com.cn/product-detail/45" in selected_urls
    assert "https://www.signliteled.com/zh/products/flexible-led-strip/" not in selected_urls
    assert selected[0].candidate_url.startswith("https://item.jd.com/")


def test_keyword_relevance_rejects_sparse_character_overlap():
    html = """
    <html><head><title>便携式蓝牙音箱</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/demo.jpg" />
    </head><body>
      <h1>便携式蓝牙音箱</h1>
      <div class="detail-content">
        便携式手提蓝牙音箱，小型家用k歌录音，内置电池，支持充电器供电。
        商品详情介绍包含无线蓝牙连接、户外播放、门店促销播报、录音播放、灯光效果和长续航电池。
        适合家庭娱乐、广场舞教学、摆摊叫卖和超市播报使用，箱体轻便，支持多种音频输入，操作简单。
      </div>
      <img src="//img11.360buyimg.com/n1/demo2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10196801444765.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("移动式充电站", parsed, decision)
    assert not filtered.accepted
    assert "核心商品本体" in " ".join(filtered.reasons)


def test_keyword_relevance_uses_original_keyword_for_wearable_audio_qualifier():
    html = """
    <html><head><title>小米小爱音箱 Play 增强版</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/audio.jpg" />
    </head><body>
      <h1>小米小爱音箱 Play 增强版</h1>
      <div class="detail-content">
        这是一款桌面智能音箱，支持语音助手、红外遥控、蓝牙播放、家庭控制和闹钟提醒。
        商品适合卧室、客厅和办公桌使用，提供稳定音质、远场拾音、儿童模式和多平台音乐服务。
        页面包含商品参数、包装清单、售后服务、连接方式、扬声器单元和电源适配器说明。
        详情还包括颜色版本、连接步骤、适配设备、保修政策和常见问题，便于用户购买前确认。
      </div>
      <img src="//img11.360buyimg.com/n1/audio2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/100012854455.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("音响", parsed, decision, "颈挂式蓝牙音响")

    assert not filtered.accepted
    assert "佩戴形态" in filtered.flags["missing_required_qualifiers"]


def test_keyword_relevance_rejects_plain_timer_when_original_keyword_requires_shape():
    html = """
    <html><head><title>厨房电子计时器</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/timer.jpg" />
    </head><body>
      <h1>厨房电子计时器</h1>
      <div class="detail-content">
        这是一款普通厨房倒计时提醒器，支持烘焙、学习、自习、运动训练和会议提醒。
        商品提供大按键、磁吸背贴、蜂鸣提醒、分钟秒钟设置、便携挂孔和简单清零功能。
        页面展示颜色、包装清单、售后服务、使用方法和电池安装说明，适合日常计时场景。
        详情还包括声音大小、摆放方式、屏幕显示、按键说明和适用人群，便于用户快速选择。
      </div>
      <img src="//img11.360buyimg.com/n1/timer2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10161470152223.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("计时器", parsed, decision, "多边形计时器")

    assert not filtered.accepted
    assert "多面/翻转形态" in filtered.flags["missing_required_qualifiers"]


def test_keyword_relevance_rejects_plain_timer_when_original_keyword_requires_braille():
    html = """
    <html><head><title>智能数字计时器 光电门气垫导轨物理教仪</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/lab-timer.jpg" />
    </head><body>
      <h1>智能数字计时器 光电门气垫导轨物理教仪</h1>
      <div class="detail-content">
        这是一款物理实验教学用计时器，配套光电门、气垫导轨、数据线和实验支架。
        商品用于速度测量、加速度实验、碰撞实验和课堂演示，强调数字显示、稳定计时和实验精度。
        页面包含实验参数、接口说明、包装清单、供电方式、售后服务和学校采购说明，适合教学设备场景。
      </div>
      <img src="//img11.360buyimg.com/n1/lab-timer2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10226969588272.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("计时器", parsed, decision, "盲文计时器")

    assert not filtered.accepted
    assert "盲文/触觉辅助" in filtered.flags["missing_required_qualifiers"]


def test_keyword_relevance_accepts_english_gravity_cube_timer_with_sensor_qualifier():
    html = """
    <html><head><title>Gravity Cube Timer - Kitchen Countdown Timer</title>
      <meta property="og:image" content="https://m.media-amazon.com/images/I/cube-timer.jpg" />
    </head><body>
      <h1>Gravity Cube Timer - Kitchen Countdown Timer</h1>
      <div class="detail-content">
        This cube timer uses a gravity sensor and flip-to-start operation for preset countdowns.
        It is designed for kitchen cooking, classroom study, productivity, exercise and tabletop time management.
        The product page includes timer modes, alarm volume, vibration reminder, LED display, battery charging,
        package contents, product photos, usage instructions, warranty details and multiple preset minutes.
      </div>
      <img src="https://m.media-amazon.com/images/I/cube-timer-2.jpg" />
    </body></html>
    """
    url = "https://www.amazon.com/example/dp/B0DJT81BB6"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("角度感应立方体计时器", parsed, decision, "角度感应立方体计时器")

    assert filtered.accepted
    assert filtered.flags["matched_keyword_ratio"] == 0


def test_keyword_relevance_rejects_industrial_water_gun_when_original_keyword_requires_toy():
    html = """
    <html><head><title>数控机床冲洗枪全金属高压水枪</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/water.jpg" />
    </head><body>
      <h1>数控机床冲洗枪全金属高压水枪</h1>
      <div class="detail-content">
        这是一款工业清洗用高压水枪，适合数控机床、雕刻机、水中心和车间冲洗作业。
        商品采用金属枪体、快拧接口、耐压软管、喷嘴组件和防滑把手，强调耐用、强力和连续冲洗。
        页面包含规格、接口尺寸、安装方式、压力范围、包装清单和售后服务说明。
        详情还包括工厂设备清洁、维护保养、接头兼容和安全操作提示，面向工业使用场景。
      </div>
      <img src="//img11.360buyimg.com/n1/water2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10215177053446.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("高射程水枪", parsed, decision, "高射程玩具水枪")

    assert not filtered.accepted
    assert "儿童/玩具用途" in filtered.flags["missing_required_qualifiers"]


def test_keyword_relevance_rejects_commuter_bike_when_keyword_requires_fitness():
    html = """
    <html><head><title>锂电动助力自行车长途旅行单脚踏车代步车</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/bike.jpg" />
    </head><body>
      <h1>锂电动助力自行车长途旅行单脚踏车代步车</h1>
      <div class="detail-content">
        这是一款户外通勤代步用电动自行车，支持长途旅行、锂电助力、城市骑行、折叠收纳和日常出行。
        商品提供电池容量、续航里程、刹车系统、轮胎规格、车架材质、照明系统和骑行安全说明。
        页面包含包装清单、售后服务、上牌提示、充电方式和道路骑行场景，面向交通代步用途。
      </div>
      <img src="//img11.360buyimg.com/n1/bike2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10176841888739.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("健身脚踏车", parsed, decision, "健身脚踏车")

    assert not filtered.accepted
    assert "健身/训练用途" in filtered.flags["missing_required_qualifiers"]


def test_keyword_relevance_rejects_commuter_bike_when_generic_pedal_variant_keeps_fitness_context():
    html = """
    <html><head><title>JAZZDA捷时达锂电动助力自行车长途旅行瓶单脚踏车代步</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/bike.jpg" />
    </head><body>
      <h1>JAZZDA捷时达锂电动助力自行车长途旅行瓶单脚踏车代步</h1>
      <div class="detail-content">
        这是一款户外运动骑行和通勤代步用电动自行车，支持长途旅行、锂电助力、城市骑行和日常出行。
        商品提供电池容量、续航里程、刹车系统、轮胎规格、车架材质、照明系统和骑行安全说明。
        页面包含包装清单、售后服务、上牌提示、充电方式、道路骑行场景和户外运动用品说明。
        虽然标题包含脚踏车并提到运动，但销售对象是交通代步自行车，不是室内健身车或动感单车。
      </div>
      <img src="//img11.360buyimg.com/n1/bike2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10176841888739.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("脚踏车", parsed, decision, "脚踏驱动摇动健身脚踏车")

    assert not filtered.accepted
    assert "代步自行车" in " ".join(filtered.reasons)


def test_keyword_relevance_rejects_plain_robot_when_original_keyword_requires_uv_sterilizing():
    html = """
    <html><head><title>石头扫地机器人扫拖一体 G30</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/robot.jpg" />
    </head><body>
      <h1>石头扫地机器人扫拖一体 G30</h1>
      <div class="detail-content">
        这是一款家用扫地机器人，支持扫地、拖地、自动上下水、路径规划和智能避障。
        商品提供大吸力、自动回充、多地图管理、语音控制、地毯识别和APP远程控制等清洁功能。
        页面包含商品参数、配件清单、清洁模式、续航时间、适用面积、售后服务和安装说明。
        详情还展示主机、基站、滚刷、边刷、拖布、水箱、电源线、联网方式、地图管理和日常维护流程。
      </div>
      <img src="//img11.360buyimg.com/n1/robot2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/100135145003.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("扫地机器人", parsed, decision, "基站UV杀菌扫地机器人")

    assert not filtered.accepted
    assert "杀菌/消毒" in filtered.flags["missing_required_qualifiers"]


def test_keyword_relevance_allows_robot_water_tank_version_when_required_uv_sterilizing_is_present():
    html = """
    <html><head><title>石头（roborock）扫地机器人80℃高温除菌洗UV杀菌拖地机 G30 Space水箱版</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/robot-g30.jpg" />
    </head><body>
      <h1>石头（roborock）扫地机器人80℃高温除菌洗UV杀菌拖地机折叠仿生机械臂智能收纳清洁机三维感知底盘升降0缠扫地机 G30 Space水箱版</h1>
      <div class="detail-content">
        这是一款完整的扫地机器人主机和基站套装，支持扫地、拖地、自动上下水、路径规划、智能避障和自动回充。
        商品详情明确包含80℃高温除菌洗、UV杀菌、基站清洁、拖布清洗、污水回收、尘盒管理和拖地模式。
        页面展示主机、基站、水箱、滚刷、边刷、拖布、电源线、清洁液、安装说明、售后服务和包装清单。
        该版本名称包含水箱版，但销售对象仍是扫地机器人本体，不是单独水箱、滤芯、拖布、边刷或爬坡垫配件。
      </div>
      <img src="//img11.360buyimg.com/n1/robot-g30-2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10223894052220.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("基站UV杀菌扫地机器人", parsed, decision, "基站UV杀菌扫地机器人")

    assert filtered.accepted


def test_keyword_relevance_rejects_treadmill_control_board_component():
    html = """
    <html><head><title>亿健（YIJIAN）跑步机T900 精灵ELF E3 JD618显示屏控制板液晶显示器上控板</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/treadmill-board.jpg" />
    </head><body>
      <h1>亿健（YIJIAN）跑步机T900 精灵ELF E3 JD618显示屏控制板液晶显示器上控板</h1>
      <div class="detail-content">
        该商品为跑步机维修部件和显示屏控制板，适用于特定型号跑步机的上控板、液晶屏、电路板和控制面板更换。
        页面说明安装方式、接口排线、维修注意事项、适配型号、售后政策和包装内容，仅包含控制板组件。
        商品不包含完整跑步机主体、跑带、立柱、电机、扶手、底座、运动器材整机或完整训练设备。
        详情页展示零件外观、主板接口、屏幕控制模块、配件清单和维修场景。
      </div>
      <img src="//img11.360buyimg.com/n1/treadmill-board-2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10193706012064.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = _make_service()._apply_keyword_relevance("可位移屏幕跑步机", parsed, decision, "可位移屏幕跑步机")

    assert not filtered.accepted
    assert "配件" in " ".join(filtered.reasons)


def test_keyword_relevance_normalizes_common_traditional_chinese():
    html = """
    <html><head><title>立方翻轉計時器</title>
      <meta property="og:image" content="https://img.91app.com/webapi/imagesV3/Original/SalePage/6594769/0/639064217944970000?v=1" />
    </head><body>
      <h1>立方翻轉計時器</h1>
      <div class="product-detail">
        重力感應翻轉計時器，倒數計時器，LED電子計時器，適合學習、辦公、烘焙和時間管理。
        商品特色包括音量調節、小巧便攜、續航力佳和清脆響鈴。
        這款立方翻轉計時器具有多段預設時間，翻到指定面即可開始倒數，桌面擺放穩定。
        外殼小巧，螢幕清楚，操作簡單，適合學生自習、工作番茄鐘、廚房料理和運動休息。
        產品頁提供商品特色、商品編號、價格、付款方式、配送方式和售後服務資訊。
      </div>
    </body></html>
    """
    url = "https://www.johnhouse.tw/SalePage/Index/6594769"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("立方体计时器", parsed, decision)
    assert filtered.accepted
    assert filtered.flags["matched_keyword_bigram_ratio"] >= 0.35


def test_keyword_relevance_rejects_core_product_accessories():
    html = """
    <html><head><title>室内扫地机器人爬坡垫上坡道三角垫塑料小台阶垫板</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/demo.jpg" />
    </head><body>
      <h1>室内扫地机器人爬坡垫上坡道三角垫塑料小台阶垫板家用</h1>
      <div class="detail-content">
        这是一款适用于扫地机器人的爬坡垫、三角垫和门槛坡道配件，用于帮助设备越过门槛。
        商品详情包含垫板材质、防滑纹理、不同高度规格、使用场景、安装方式和售后说明。
        页面销售的是配件耗材，不是扫地机器人本体。
        该配件可放置在厨房、卫生间、阳台和客厅门槛位置，帮助已有扫地机器人通过小台阶。
        详情页介绍了灰色、白色、不同高度和长度规格，可裁剪使用，适合多种地面材质。
        包装内仅包含坡道垫板，不包含主机、充电座、拖地模块、导航系统和清扫系统。
      </div>
      <img src="//img11.360buyimg.com/n1/demo2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10108481976279.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("免爬坡扫地机器人", parsed, decision)
    assert not filtered.accepted
    assert "配件" in " ".join(filtered.reasons)


def test_keyword_relevance_rejects_core_product_furniture_accessories():
    html = """
    <html><head><title>切角阳台柜带搓衣板石英石洗衣机柜扫地机器人柜洗衣池</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/cabinet.jpg" />
    </head><body>
      <h1>切角阳台柜带搓衣板石英石洗衣机柜扫地机器人柜洗衣池</h1>
      <div class="detail-content">
        这是一款阳台洗衣机柜和扫地机器人收纳柜，用于放置洗衣机、搓衣板和清洁设备。
        商品详情包含柜体尺寸、石英石台面、洗衣池、水龙头、安装方式、颜色和售后服务。
        页面销售的是柜子家具，不是扫地机器人本体，也不包含清扫主机、传感器或移动控制系统。
        详情还介绍了定制尺寸、板材、防水工艺、台盆结构、下水方式、配送安装和包装清单。
      </div>
      <img src="//img11.360buyimg.com/n1/cabinet2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/34677624309.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("扫地机器人", parsed, decision)
    assert not filtered.accepted
    assert "配件" in " ".join(filtered.reasons)


def test_keyword_relevance_rejects_floor_washer_keyword_stuffing_as_robot():
    html = """
    <html><head><title>添可极客蒸汽2.0 家用洗地机洗拖吸一体 扫地机器人 洗地拖</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/floor-washer.jpg" />
    </head><body>
      <h1>添可极客蒸汽2.0 家用洗地机洗拖吸一体 扫地机器人 洗地拖</h1>
      <div class="detail-content">
        这是一款手持家用洗地机，支持高温蒸汽、吸拖洗一体、自动清洗滚刷和地面除菌。
        商品详情包含水箱、滚刷、吸力、电池、清洁液、拖地模式、包装清单和售后服务。
        页面销售的是洗地机，不是自主导航的扫地机器人本体，也不包含机器人移动底盘或路径规划系统。
        详情还介绍了蒸汽源、清洁头、续航、噪音、适用地面、操作方式和用户评价。
      </div>
      <img src="//img11.360buyimg.com/n1/floor-washer2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/100254751065.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("扫地机器人", parsed, decision)
    assert not filtered.accepted
    assert "部件" in " ".join(filtered.reasons)


def test_keyword_relevance_rejects_lighting_control_component_as_fixture():
    html = """
    <html><head><title>LED 调光器</title>
      <meta property="og:image" content="https://example.com/dimmer.jpg" />
    </head><body>
      <h1>LED 调光器</h1>
      <div class="detail-content">
        LED调光器用于控制免工具调光灯具亮度，提供恒压调光器和恒流调光器，适配灯带和大功率灯具。
        产品可实现照明系统调光控制、稳定供电、保护LED免受电压或电流波动影响。
        适用于建筑照明、重点照明、舞台照明和专业设计场景，支持多种控制方式。
        该页面介绍调光控制部件，不是射灯、筒灯、吸顶灯、吊灯、台灯或完整灯具本体。
      </div>
      <img src="//example.com/dimmer2.jpg" />
    </body></html>
    """
    url = "https://enttec.com.cn/products/led-dimmers/"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("免工具调光灯具", parsed, decision)
    assert not filtered.accepted
    assert "控制" in " ".join(filtered.reasons)


def test_keyword_relevance_rejects_charger_when_keyword_is_charging_station():
    html = """
    <html><head><title>Golf Cart charger/高尔夫球场车充电器</title>
      <meta property="og:image" content="https://example.com/charger.jpg" />
    </head><body>
      <h1>Golf Cart charger/高尔夫球场车充电器</h1>
      <div class="detail-content">
        这是一款用于高尔夫球场车辆的车载充电器，适配多种电池和车辆接口。
        页面介绍充电器参数、输出电压、电池连接方式、安装方式和售后服务。
        商品本体是充电器部件，不是移动式充电站，也不是带定位系统的球类运动器材充电站。
        详情包含外壳材质、风冷结构、防护等级、线缆规格、适配车型和包装清单。
        该产品用于给高尔夫球车电池补电，不包含可移动站体、定位通信系统或高尔夫球收纳结构。
      </div>
      <img src="//example.com/charger2.jpg" />
    </body></html>
    """
    url = "https://www.casil-jeckson.com/product/227.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("高尔夫球移动充电站", parsed, decision)
    assert not filtered.accepted
    assert any(token in " ".join(filtered.reasons) for token in ("配件", "部件"))

    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    filtered = service._apply_keyword_relevance("充电站", parsed, decision)
    assert not filtered.accepted
    assert any(token in " ".join(filtered.reasons) for token in ("配件", "部件"))


def test_keyword_relevance_rejects_short_core_without_title_signal():
    html = """
    <html><head><title>后背式进气电动口罩防尘防烟焊工面罩过滤带管后置电焊工送风面具</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/mask.jpg" />
    </head><body>
      <h1>后背式进气电动口罩防尘防烟焊工面罩过滤带管后置电焊工送风面具</h1>
      <div class="detail-content">
        商品详情介绍过滤系统、送风管、电池、面罩、防护等级、焊工使用场景和售后服务。
        页面有商品图片、参数、包装清单、配送服务和品牌信息，但商品不是充电站本体。
        该防护面具适合焊接、打磨、粉尘环境和喷涂作业，包含头罩、过滤盒、管路和风机。
        详情还包含佩戴方式、续航时间、过滤效率、尺寸规格、配件清单和保修说明。
      </div>
      <img src="//img11.360buyimg.com/n1/mask2.jpg" />
    </body></html>
    """
    url = "https://item.jd.com/10121288851079.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("充电站", parsed, decision)
    assert not filtered.accepted
    assert "商品本体" in " ".join(filtered.reasons)


def test_keyword_relevance_rejects_golf_cart_when_keyword_is_golf_ball():
    html = """
    <html><head><title>智能跟随高尔夫球车远程应用控制电动高尔夫球车</title>
      <meta property="og:image" content="https://sc04.alicdn.com/kf/golf-cart.jpg" />
    </head><body>
      <h1>智能跟随高尔夫球车远程应用控制电动高尔夫球车</h1>
      <div class="detail-content">
        这是一款电动高尔夫球车，支持远程控制、自动跟随、慢动作回放和球包承载。
        商品详情包含球车电池、电机、车架、轮胎、遥控器、折叠结构、颜色和包装信息。
        页面销售的是高尔夫球车，不是智能高尔夫球，也不是球内定位装置或移动式充电站。
        详情还展示了车辆尺寸、载重、续航、坡度能力、充电方式、应用控制和售后服务。
      </div>
      <img src="//sc04.alicdn.com/kf/golf-cart-2.jpg" />
    </body></html>
    """
    url = "https://chinese.alibaba.com/product-detail/Intelligent-Follow-Golf-Carts-Remote-APP-1600531372236.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("高尔夫球场用智能高尔夫球", parsed, decision)
    assert not filtered.accepted
    assert "部件" in " ".join(filtered.reasons)


def test_keyword_relevance_accepts_english_charging_station_product_signal():
    html = """
    <html><head><title>China 60KW Portable Super EV Charger Electric Charger Car station</title>
      <meta property="og:image" content="https://www.midapower.com/product.jpg" />
    </head><body>
      <h1>China 60KW Portable Super EV Charger Electric Charger Car station</h1>
      <div class="detail-content">
        Portable EV charger station for taxi fleet and emergency charging service.
        Product details include cabinet, cable, power module, display, cooling system, charging protocol and warranty.
        中文资料零散标注：移、动、式、充、电、站，适合临时站点、出租车运营、道路救援和车队服务。
        The page introduces one charger station product with rated power, connector type, input voltage, output current,
        enclosure level, installation method, manufacturer information, package list, delivery service and product images.
        It is sold as a complete station product, not only a cable, adapter, battery or accessory.
      </div>
      <img src="//www.midapower.com/product2.jpg" />
    </body></html>
    """
    url = "https://www.midapower.com/zh/60kw-portable-super-ev-charger-fast-dc-charger-station-for-taxi-product/"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("移动式充电站", parsed, decision)
    assert filtered.accepted
    assert filtered.flags["matched_keyword_bigram_ratio"] < 0.35


def test_keyword_relevance_accepts_motor_driver_chip_product_signal():
    html = """
    <html><head><title>AW86927FCR AWINIC LRA 触觉驱动器 马达驱动IC</title>
      <meta property="og:image" content="https://item.szlcsc.com/chip.jpg" />
    </head><body>
      <h1>AW86927FCR AWINIC LRA 触觉驱动器 马达驱动IC</h1>
      <div class="detail-content">
        电子元器件商品详情，型号 AW86927FCR，品牌 AWINIC，适用于 LRA/ERM 触觉反馈马达。
        页面提供封装、库存、价格、数据手册、PCB引脚图、焊盘图、参数、包装和采购信息。
        中文资料零散标注：硬、件、触、发、管、脚、马、达、驱、动、芯、片，用于跨语言检索。
        The product is a haptic driver IC with waveform playback, trigger pin support, motor control and protection.
        It is sold as a semiconductor chip product, not an algorithm page, application note or software solution.
      </div>
      <img src="//item.szlcsc.com/chip2.jpg" />
    </body></html>
    """
    url = "https://item.szlcsc.com/5787307.html"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("硬件触发管脚LRA马达驱动芯片", parsed, decision)
    assert filtered.accepted
    assert filtered.flags["matched_keyword_bigram_ratio"] < 0.35


def test_keyword_relevance_rejects_haptic_algorithm_when_keyword_is_driver_chip():
    html = """
    <html><head><title>自适应 F0 校准算法</title>
      <meta property="og:image" content="https://www.richtap-haptics.com/haptic.jpg" />
    </head><body>
      <h1>自适应 F0 校准算法</h1>
      <div class="detail-content">
        页面介绍触觉反馈算法、F0校准、波形播放规则、软件方案、SDK能力和手机触感调校服务。
        内容提到 LRA 马达驱动芯片、硬件触发管脚、预设播放规则和触觉反馈体验。
        但该页面销售或展示的是算法方案，不是独立的马达驱动芯片商品，也没有芯片型号和采购信息。
        详情包含方案架构、客户案例、软件能力、算法优势、开发流程、技术白皮书和服务支持。
      </div>
      <img src="//www.richtap-haptics.com/haptic2.jpg" />
    </body></html>
    """
    url = "https://www.richtap-haptics.com/product/detail/123"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("预设播放规则LRA马达驱动芯片", parsed, decision)
    assert not filtered.accepted
    assert "部件" in " ".join(filtered.reasons)


def test_keyword_relevance_accepts_core_lighting_product_with_non_patent_phrase():
    html = """
    <html><head><title>LeoFresnel by Astera</title>
      <meta property="og:image" content="https://example.com/leofresnel.jpg" />
    </head><body>
      <h1>LeoFresnel by Astera</h1>
      <div class="detail-content">
        这是一款专业影视照明灯具，灯体支持无需拆卸即可调节光束角，覆盖15度至60度。
        产品用于演播室、舞台和建筑照明，提供稳定输出、调光控制、色温控制和附件安装。
        页面展示单一灯具产品的技术参数、安装方式、配光曲线、产品图片和应用场景。
        该灯具具备高亮度输出、均匀光斑、静音散热、标准支架安装和多种控制协议，适合长期商业使用。
        商品详情包含包装清单、配件选项、供电方式、控制接口、安装说明、售后服务以及制造商信息。
        用户可以根据空间需要调整照射角度和亮度，免工具完成常规角度设置和维护。
      </div>
      <img src="//example.com/leofresnel2.jpg" />
    </body></html>
    """
    url = "https://astera-led.com/zh/products/leofresnel/"
    parsed = parse_product_page(html, url, url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=url, final_url=url, html=html, provider="test"), parsed)
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    filtered = service._apply_keyword_relevance("免拆调角灯具", parsed, decision)
    assert filtered.accepted
    assert filtered.flags["matched_keyword_ratio"] >= 0.85
    assert filtered.flags["matched_keyword_bigram_ratio"] < 0.35


def test_fetch_candidates_stops_after_max_products():
    good_url = "https://item.jd.com/100000000001.html"
    slow_url = "https://item.jd.com/100000000002.html"
    html = """
    <html><head><title>智能扫地机器人扫拖一体</title>
      <meta property="og:image" content="https://img11.360buyimg.com/n1/demo.jpg" />
    </head><body>
      <h1>智能扫地机器人扫拖一体</h1>
      <div class="detail-content">
        这是一款扫地机器人本体，支持扫地、拖地、自动回充、路径规划和越障。
        商品详情介绍包含主机、充电座、拖地模块、传感器、导航系统、清扫系统和售后服务。
        页面销售的是完整扫地机器人，不是滤芯、拖布、爬坡垫、刷子或其他配件耗材。
        产品具有大吸力、智能避障、自动集尘、APP控制、定时清扫、地毯识别和多地图管理功能。
        包装包括扫地机器人主机、基站、电源线、说明书和原厂附件，适合家庭地面清洁使用。
        详情页展示了商品参数、适用面积、续航时间、水箱容量、清扫模式、拖地方式和售后保障。
      </div>
      <img src="//img11.360buyimg.com/n1/demo2.jpg" />
    </body></html>
    """

    class FakeClient:
        async def fetch_detail_page(self, url, force_render=False):
            if url == slow_url:
                await asyncio.sleep(5)
            return FetchResult(ok=True, url=url, final_url=url, html=html, provider="test")

    async def run_case():
        service = ProductSearchService(
            Settings(
                database_url="postgresql://unused",
                brightdata_api_key="",
                brightdata_serp_zone="",
                brightdata_unlocker_zone="",
                brightdata_endpoint="https://api.brightdata.com/request",
                request_timeout_seconds=1,
                max_concurrency=2,
                serp_url_limit=1,
                allow_direct_fetch_fallback=False,
                render_fallback_enabled=False,
                render_retry_attempts=0,
                user_agent="test",
            )
        )
        service.client = FakeClient()
        payload = ProductSearchInput(
            patent_record_id=1,
            input_keywords=["扫地机器人"],
            max_products=1,
            persist=False,
        )
        candidates = [
            CandidateLink(keyword="扫地机器人", platform="jd", candidate_url=good_url, title="智能扫地机器人", source="test"),
            CandidateLink(keyword="扫地机器人", platform="jd", candidate_url=slow_url, title="慢详情页", source="test"),
        ]
        return await service._fetch_and_filter_candidates(payload, candidates)

    products, diagnostics = asyncio.run(asyncio.wait_for(run_case(), timeout=1))
    assert len(products) == 1
    assert any(diagnostic.status == "skipped" for diagnostic in diagnostics)


def test_brightdata_zone_failure_does_not_escape(monkeypatch):
    async def fail_discovery(_self):
        raise TimeoutError()

    monkeypatch.setattr(BrightDataClient, "_discover_zones", fail_discovery)
    client = BrightDataClient(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="configured",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    plan = SearchQueryPlan(keyword="医用消毒帽", platform="jd", query="医用消毒帽 site:jd.com", serp_url="")
    candidates, meta = asyncio.run(client.search(plan, 1))
    assert candidates == []
    assert meta["ok"] is False


def test_brightdata_search_merges_direct_fallback_when_serp_has_only_listing_pages(monkeypatch):
    async def fake_request(_self, _payload):
        return {
            "organic": [
                {
                    "link": "https://www.jd.com/brand/1367822b088da51a04b47.html",
                    "title": "扫地机器人行业品牌及商品 - 京东",
                }
            ]
        }

    async def fake_direct(self, plan, limit, brightdata_error=""):
        return (
            [
                CandidateLink(
                    keyword=plan.keyword,
                    original_keyword=plan.original_keyword,
                    platform=plan.platform,
                    candidate_url="https://item.jd.com/100135145003.html",
                    title="石头扫地机器人扫拖一体 G30",
                    source="direct_bing",
                )
            ],
            {"provider": "direct_bing", "ok": True},
        )

    monkeypatch.setattr(BrightDataClient, "_request_brightdata", fake_request)
    monkeypatch.setattr(BrightDataClient, "_search_direct_bing", fake_direct)
    client = BrightDataClient(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="configured",
            brightdata_serp_zone="serp",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=True,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    plan = SearchQueryPlan(
        keyword="扫地机器人",
        original_keyword="拖地升降扫地机器人",
        platform="jd",
        query="site:item.jd.com 扫地机器人",
        serp_url="",
    )

    candidates, meta = asyncio.run(client.search(plan, 3))

    assert meta["provider"] == "brightdata_serp+direct_bing"
    assert any(candidate.candidate_url == "https://item.jd.com/100135145003.html" for candidate in candidates)


def test_service_uses_historical_products_when_live_search_has_no_accepted_results(monkeypatch):
    fallback = ProductResult(
        platform="jd",
        product_name="石头扫地机器人扫拖一体 G30",
        product_url="https://item.jd.com/100135145003.html",
        final_url="https://item.jd.com/100135145003.html",
        picture=["https://img11.360buyimg.com/n1/robot.jpg"],
        matched_keywords=["扫地机器人"],
        quality_score=75,
        quality_flags={"historical_fallback": True},
    )

    class FakeClient:
        async def search(self, _plan, _limit):
            return [], {"provider": "direct_bing", "ok": False}

    monkeypatch.setattr("product_search.service.fetch_recent_products_for_record", lambda _record_id, _limit: [fallback])
    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    service.client = FakeClient()
    payload = ProductSearchInput(
        patent_record_id=98,
        input_keywords=["扫地机器人"],
        platforms=["jd"],
        max_products=1,
        persist=False,
    )

    output = asyncio.run(service.run(payload))

    assert output.accepted_products_count == 1
    assert output.products[0].product_name == "石头扫地机器人扫拖一体 G30"
    assert output.candidates_preview[-1].status == "accepted"
    assert output.candidates_preview[-1].raw_payload["historical_fallback"] is True


def test_historical_fallback_does_not_reuse_product_for_unrelated_keyword(monkeypatch):
    fallback = ProductResult(
        platform="jd",
        product_name="儿童自律时间管理翻转电子计时器",
        product_url="https://item.jd.com/100080783793.html",
        final_url="https://item.jd.com/100080783793.html",
        description="电子倒计时器，支持翻转计时、时间提醒、学习管理和静音模式。" * 8,
        picture=["https://img11.360buyimg.com/n1/timer.jpg"],
        matched_keywords=["多边形计时器"],
        quality_score=85,
        quality_flags={"historical_fallback": True},
    )

    class FakeClient:
        async def search(self, _plan, _limit):
            return [], {"provider": "direct_bing", "ok": False}

    monkeypatch.setattr("product_search.service.fetch_recent_products_for_record", lambda _record_id, _limit: [fallback])
    service = _make_service()
    service.client = FakeClient()
    payload = ProductSearchInput(
        patent_record_id=99,
        input_keywords=["扫地机器人"],
        platforms=["jd"],
        max_products=1,
        persist=False,
    )

    output = asyncio.run(service.run(payload))

    assert output.accepted_products_count == 0
    assert output.products == []


def test_refresh_historical_products_skips_stale_jd_one_image_after_risk_redirect():
    fallback = ProductResult(
        platform="jd",
        product_name="奥粘 颈挂蓝牙音箱",
        product_url="https://item.jd.com/10166086752695.html",
        final_url="https://item.jd.com/10166086752695.html",
        picture=["https://img10.360buyimg.com/n1/s720x720_jfs/t1/main.jpg"],
        matched_keywords=["颈挂蓝牙音箱"],
        quality_score=85,
        quality_flags={"historical_fallback": True},
    )

    class FakeClient:
        async def fetch_detail_page(self, _url):
            return FetchResult(
                ok=True,
                url="https://item.jd.com/10166086752695.html",
                final_url="https://cfe.m.jd.com/privatedomain/risk_handler/03101900/?returnurl=https%3A%2F%2Fitem.jd.com%2F10166086752695.html",
                html="<html><title>京东验证</title></html>",
                provider="direct_fetch",
            )

    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    service.client = FakeClient()

    refreshed = asyncio.run(service._refresh_historical_products([fallback]))

    assert refreshed == []


def test_refresh_historical_products_skips_aggregate_external_history():
    fallback = ProductResult(
        platform="external_product",
        product_name="工商业-深圳龙电埃瑞斯新能源有限公司",
        product_url="https://www.areswatt.com/cn/product/c-i/",
        final_url="https://www.areswatt.com/cn/product/c-i/",
        picture=["https://www.areswatt.com/cn/category.jpg"],
        matched_keywords=["移动式充电站"],
        quality_score=85,
        quality_flags={"historical_fallback": True},
    )

    class FakeClient:
        async def fetch_detail_page(self, _url):
            raise AssertionError("aggregate historical URLs should be skipped before refresh")

    service = ProductSearchService(
        Settings(
            database_url="postgresql://unused",
            brightdata_api_key="",
            brightdata_serp_zone="",
            brightdata_unlocker_zone="",
            brightdata_endpoint="https://api.brightdata.com/request",
            request_timeout_seconds=1,
            max_concurrency=1,
            serp_url_limit=1,
            allow_direct_fetch_fallback=False,
            render_fallback_enabled=False,
            render_retry_attempts=0,
            user_agent="test",
        )
    )
    service.client = FakeClient()

    refreshed = asyncio.run(service._refresh_historical_products([fallback]))

    assert refreshed == []


def test_refresh_historical_products_rejects_http_200_empty_shell():
    fallback = ProductResult(
        platform="external_product",
        product_name="历史商品名称",
        product_url="https://example.com/products/123",
        final_url="https://example.com/products/123",
        description="历史商品详情" * 30,
        picture=["https://example.com/products/old.jpg"],
        quality_score=90,
    )

    class FakeClient:
        async def fetch_detail_page(self, url):
            return FetchResult(
                ok=True,
                url=url,
                final_url=url,
                html="<html><head><title>页面加载中</title></head><body></body></html>",
                provider="direct_fetch",
            )

    service = _make_service()
    service.client = FakeClient()

    refreshed = asyncio.run(service._refresh_historical_products([fallback]))

    assert refreshed == []


def test_historical_refresh_is_limited_and_bounded_concurrent():
    active = 0
    peak_active = 0
    calls = 0

    products = [
        ProductResult(
            platform="external_product",
            product_name=f"历史商品 {index}",
            product_url=f"https://example.com/products/{index}",
            final_url=f"https://example.com/products/{index}",
            description="历史商品详情" * 30,
            picture=[f"https://example.com/products/{index}.jpg"],
            quality_score=80,
        )
        for index in range(20)
    ]

    class FakeClient:
        async def fetch_detail_page(self, _url):
            nonlocal active, peak_active, calls
            calls += 1
            active += 1
            peak_active = max(peak_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            raise RuntimeError("temporary fetch failure")

    service = _make_service()
    service.client = FakeClient()

    refreshed = asyncio.run(service._refresh_historical_products(products, max_products=20))

    assert calls == service.settings.historical_refresh_limit == 8
    assert 1 < peak_active <= service.settings.historical_refresh_concurrency
    assert len(refreshed) == 8


def test_historical_fallback_prefers_older_rich_product_over_recent_stale_jd(monkeypatch):
    stale_products = [
        ProductResult(
            platform="jd",
            product_name=f"失效京东计时器 {index}",
            product_url=f"https://item.jd.com/10000000000{index}.html",
            final_url=f"https://item.jd.com/10000000000{index}.html",
            picture=[f"https://img10.360buyimg.com/n1/stale-{index}.jpg"],
            matched_keywords=["立方计时器"],
            quality_score=85,
            quality_flags={"historical_product_row_id": 100 + index},
        )
        for index in range(3)
    ]
    rich_product = ProductResult(
        platform="external_product",
        product_name="Kitchen Cube Timer",
        product_url="https://www.amazon.com/LZTGFT-Kitchen-15-20-30-60-Management-Exercise/dp/B09BMQFZS5",
        final_url="https://www.amazon.com/LZTGFT-Kitchen-15-20-30-60-Management-Exercise/dp/B09BMQFZS5",
        picture=[f"https://m.media-amazon.com/images/I/timer-{index}.jpg" for index in range(14)],
        matched_keywords=["立方计时器"],
        description="Kitchen cube timer with gravity sensor.",
        quality_score=90,
        quality_flags={"historical_product_row_id": 10},
    )

    class FakeClient:
        async def search(self, _plan, _limit):
            return [], {"provider": "direct_bing", "ok": False}

        async def fetch_detail_page(self, url):
            if "item.jd.com" in url:
                return FetchResult(
                    ok=True,
                    url=url,
                    final_url="https://cfe.m.jd.com/privatedomain/risk_handler/03101900/",
                    html="<html><title>京东验证</title></html>",
                    provider="direct_fetch",
                )
            return FetchResult(
                ok=True,
                url=url,
                final_url=url,
                html="""
                <html><head>
                  <title>Kitchen Cube Timer</title>
                  <meta property="og:image" content="https://m.media-amazon.com/images/I/timer-main.jpg" />
                  <meta name="description" content="Kitchen cube timer with gravity sensor and flip countdown modes." />
                </head><body>
                  <h1>Kitchen Cube Timer</h1>
                  <div id="feature-bullets">15-20-30-60 minute cube timer for kitchen, classroom and exercise.</div>
                  <img src="https://m.media-amazon.com/images/I/timer-detail.jpg" />
                </body></html>
                """,
                provider="direct_fetch",
            )

    monkeypatch.setattr(
        "product_search.service.fetch_recent_products_for_record",
        lambda _record_id, _limit: stale_products + [rich_product],
    )
    service = _make_service()
    service.client = FakeClient()
    payload = ProductSearchInput(
        patent_record_id=99,
        input_keywords=["立方计时器"],
        platforms=["jd"],
        max_products=1,
        persist=False,
    )

    output = asyncio.run(service.run(payload))

    assert output.accepted_products_count == 1
    assert output.products[0].product_name == "Kitchen Cube Timer"
    assert output.products[0].final_url == rich_product.final_url
