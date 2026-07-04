import asyncio

from product_search.brightdata import BrightDataClient
from product_search.models import CandidateLink, FetchResult, ProductSearchInput
from product_search.discovery import extract_detail_candidates_from_discovery_page
from product_search.parser import parse_product_page
from product_search.platforms import expand_ecommerce_keyword_variants, is_aggregate_url, is_detail_url, normalize_url
from product_search.quality import evaluate_product_detail
from product_search.service import ProductSearchService
from product_search.config import Settings
from product_search.models import SearchQueryPlan
from product_search.special_sources import render_xiaomi_crowdfunding_html


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
    assert is_detail_url("https://chinese.alibaba.com/product-detail/Intelligent-Follow-Golf-Carts-Remote-APP-1600531372236.html")
    assert is_detail_url("https://www.areswatt.com/cn/product/utility/35.html")
    assert is_detail_url("https://item.szlcsc.com/5787307.html")
    assert is_detail_url("https://www.ti.com.cn/product/cn/DRV2605/part-details/DRV2605YZFT")
    assert is_detail_url("https://www.awinic.com/cn/productDetail/AW86907FCR")
    assert is_detail_url("https://www.dfrobot.com.cn/goods-4226.html")
    assert not is_detail_url("https://www.areswatt.com/cn/product/residential/")
    assert not is_detail_url("https://www.areswatt.com/cn/product/c-i/")
    assert not is_detail_url("https://www.richtap-haptics.com/product/haptic")
    assert not is_detail_url("https://www.sg-micro.com/cn/products/motor-gate-drivers")
    assert not is_detail_url("https://www.taobao.com/chanpin/0d21bd8275d83b3dadc18ea81b799ad19.html")
    assert not is_detail_url("https://ylbzj.yancheng.gov.cn/module/download/downfile.jsp")
    assert not is_detail_url("https://www.baihewuhan.com/list-xiaodumao.html")
    assert normalize_url("https://") == ""


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
    assert "短核心关键词" in " ".join(filtered.reasons)


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
    assert "TimeoutError" in meta["error"]
