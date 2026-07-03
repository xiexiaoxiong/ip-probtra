import asyncio

from product_search.brightdata import BrightDataClient
from product_search.models import FetchResult
from product_search.discovery import extract_detail_candidates_from_discovery_page
from product_search.parser import parse_product_page
from product_search.platforms import expand_ecommerce_keyword_variants, is_aggregate_url, is_detail_url, normalize_url
from product_search.quality import evaluate_product_detail
from product_search.service import ProductSearchService
from product_search.config import Settings
from product_search.models import SearchQueryPlan


def test_platform_url_filters_reject_listing_pages():
    assert is_detail_url("https://detail.1688.com/offer/123456.html", "1688")
    assert is_detail_url("https://item.jd.com/100012345.html", "jd")
    assert is_aggregate_url("https://search.jd.com/Search?keyword=扫地机器人")
    assert is_aggregate_url("https://www.jd.com/brand/13182a361beee19dc083.html")
    assert is_aggregate_url("https://www.jd.com/chanpin/53783.html")
    assert not is_detail_url("https://search.jd.com/Search?keyword=扫地机器人", "jd")
    assert is_aggregate_url("https://s.taobao.com/search?q=拖地升降扫地机器人")
    assert normalize_url("#") == ""
    assert normalize_url("/") == ""


def test_external_product_detail_url_filters_are_conservative():
    assert is_detail_url("https://www.medicalexpo.com.cn/prod/euronda/product-68436-949858.html")
    assert is_detail_url("https://www.andemed.com/product/detail/286")
    assert is_detail_url("https://www.linhwa.com/products/142.html")
    assert is_detail_url("http://zfcg.szggzy.com:8081/mall/productdetail.html")
    assert is_detail_url("https://www.johnhouse.tw/SalePage/Index/6594769")
    assert not is_detail_url("https://www.taobao.com/chanpin/0d21bd8275d83b3dadc18ea81b799ad19.html")
    assert not is_detail_url("https://ylbzj.yancheng.gov.cn/module/download/downfile.jsp")
    assert not is_detail_url("https://www.baihewuhan.com/list-xiaodumao.html")
    assert normalize_url("https://") == ""


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


def test_quality_rejects_captcha_page():
    html = "<html><title>安全验证</title><body>请完成验证码验证后继续访问</body></html>"
    final_url = "https://detail.1688.com/offer/123456.html"
    parsed = parse_product_page(html, final_url, final_url)
    decision = evaluate_product_detail(FetchResult(ok=True, url=final_url, final_url=final_url, html=html, provider="test"), parsed)
    assert not decision.accepted
    assert decision.flags["blocked"] is True


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
    assert filtered.flags["matched_keyword_bigram_ratio"] < 0.35


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
    assert "TimeoutError" in meta["error"]
