# 平行模块三：商品详情检索

> 本模块是新的实验性商品检索模块，不替代现有 `3-search/`。目标是用模块2的关键词直接检索电商平台，并抓取真正商品详情页中的标题、详情文本、图片、价格、销量、品牌、厂商等信息。

## 边界

- 只读取模块2的 `keyword_records` 或请求传入的 `input_keywords`。
- 默认不写旧表 `search_runs` / `search_products`，避免影响现有主流程。
- 结果写入独立表：
  - `product_detail_search_runs`
  - `product_detail_search_candidates`
  - `product_detail_search_products`
- 候选链接和抓取失败也要记录到 candidates 表，便于判断是反爬、列表页污染、还是关键词质量问题。

## 抓取链路

```text
keyword_records/input_keywords
  -> build platform-specific SERP queries
  -> Bright Data SERP API or direct Bing fallback
  -> filter candidate URLs by platform detail-page rules
  -> Bright Data Unlocker API or direct fetch fallback
  -> parse title/text/images/price/sales/manufacturer
  -> quality gate
  -> persist accepted products + rejected candidate diagnostics
```

## Bright Data 环境变量

PM2 `ecosystem.config.cjs` 会把以下变量注入本模块：

| 变量 | 用途 |
| --- | --- |
| `BRIGHTDATA_API_KEY` | Bright Data API Bearer token |
| `BRIGHTDATA_SERP_ZONE` | SERP API zone，用于搜索候选详情页；未配置时会尝试自动发现 active `serp` zone |
| `BRIGHTDATA_UNLOCKER_ZONE` | Unlocker API zone，用于抓取电商详情页；未配置时会尝试自动发现 active `unblocker` zone |
| `BRIGHTDATA_API_KEY_FILE` | 可选，本地密钥文件路径；仅当 `BRIGHTDATA_API_KEY` 为空时读取 |

官方接口统一为 `POST https://api.brightdata.com/request`，payload 包含 `zone`、`url`、`format`。SERP 阶段优先使用 `data_format=parsed_light`，详情阶段使用 `format=raw`。

## 质量规则

商品进入结果表必须满足：

- `product_url`、`final_url`、`source_text_url`、`source_image_url` 指向同一个最终详情页。
- URL 必须匹配真实商品详情模式，例如 `detail.1688.com/offer/...html`、`item.jd.com/...html`、`item.taobao.com/item.htm?id=...`、`detail.tmall.com/item.htm?id=...`。
- 明确排除搜索页、列表页、店铺页、类目页、聚合推荐页。
- 京东 `brand/hprm/phb/chanpin/hotitem` 等聚合页不能作为商品结果，但可以作为 discovery page 提取真实 `item.jd.com/<sku>.html` 详情链接。
- 可保守接受外部真实商品详情页并标记为 `external_product`，例如 `/product/detail/...`、`/products/...`、`/SalePage/Index/...`；但 `chanpin`、下载文件、专利页、列表页仍必须 rejected。
- 页面不能是登录/验证码/安全验证等反爬拦截页。
- 至少提取到标题、一定长度的详情文本、图片；价格、销量、厂商为增强字段，不作为唯一通过条件。
- 关键词相关性不能只看单字覆盖；当前还要求中文二字连续片段覆盖度，防止“移动式充电站”误召回“便携式蓝牙音箱”。
- 常见简繁差异会在关键词相关性判断前归一化，避免繁体商品页被误拒。
- 关键词包含明确核心商品本体时，如果商品标题显示为配件/耗材/垫板/支架/滤芯/刷/电池/充电器等，不得进入 products 表。

## HTTP 接口

### `POST /run`

```json
{
  "patent_record_id": 1,
  "analysis_session_id": "analysis_xxx",
  "input_keywords": null,
  "platforms": ["1688", "jd", "taobao", "tmall"],
  "max_keywords": 8,
  "max_candidates_per_keyword": 6,
  "max_detail_candidates": 12,
  "max_products": 30,
  "request_timeout_seconds": null,
  "serp_url_limit": null,
  "persist": true
}
```

### `GET /runs/{run_id}`

返回运行摘要、通过商品和候选诊断。

## 运行

```bash
SKIP_ENV_VALIDATION=1 bash scripts/http_run.sh -p 5107
```

PM2 统一启动后端时，本模块端口是 `5107`。

## 2026-07-04 真实测试进度

- `tests/test_quality.py` 覆盖 URL 过滤、验证码拒绝、京东 discovery page 抽取、京东标题/运费 0 清理、电商关键词透明扩展、连续片段相关性过滤、外部商品页、无后缀图片 URL、简繁相关性、核心商品配件过滤；当前 12/12 通过。
- `record=94 / CN103025390B`：通过京东聚合页发现并抓取真实商品详情 `https://item.jd.com/30147313701.html`，accepted，图片与文本同源。
- `record=95 / CN107786922B`：通过“音响/音箱/耳机”透明 query 扩展找到 `https://item.jd.com/100005207111.html`，多次 render 后 accepted。
- `record=96 / 201780007801.2`：此前误召回京东蓝牙音箱；连续片段相关性过滤后已 rejected，仍需继续寻找真实“移动式充电站/高尔夫球充电站”商品。
- `record=97 / CN111249616B`：`医用消毒帽` 的 JD/1688 平台详情页不足；新增外部商品页规则后 accepted 厂商真实商品页 `https://www.andemed.com/product/detail/286`，图片/文本同源，platform=`external_product`。
- `record=99 / CN210244097U`：模块2 原始关键词 `多边形计时器` 通过“立方体/六面/翻转 + 计时器/定时器”形态词扩展 accepted 台湾电商详情页 `https://www.johnhouse.tw/SalePage/Index/6594769`；修复外部无后缀图片 URL 和简繁相关性后通过。
- `record=100 / CN212879151U`：原先误 accepted `扫地机器人爬坡垫`，新增核心商品配件过滤后 rejected；再通过核心商品名回退（`免爬坡扫地机器人` -> `扫地机器人`）accepted 京东石头扫地机器人本体 `https://item.jd.com/100135145003.html`。
- `/run` 支持 `request_timeout_seconds`、`serp_url_limit` 单次覆盖；`scripts/run_example_patents.py` 支持 `--service-url http://127.0.0.1:5107`，用于通过 PM2 服务复用 Bright Data 环境。
- SERP 0 候选、非详情候选和详情抓取失败都会写入 `product_detail_search_candidates`，便于判断关键词过窄、平台规则不足或反爬失败。
- 批量测试必须设置预算：建议 `PRODUCT_SEARCH_MAX_CONCURRENCY=1`、`PRODUCT_SEARCH_SERP_URL_LIMIT=2-4`、`--max-detail-candidates 1-4` 逐专利运行；不要一次全量跑 13 个高预算任务。
- 当前仍未完成 13 个实例专利全量验证；`record=96` 仍未找到合格商品，`record=98`、`record=105` 缺模块2关键词来源，`record=101-106` 仍需继续逐个真实测试。

## 2026-07-04 后续 loop 进展

- 新增小米众筹 SPA 专用数据源：`src/product_search/special_sources.py` 识别 `m.mi.com/crowdfunding/proddetail/<project_id>`，用 `POST https://m.mi.com/v1/crowd/crowd_detail` + 合法 `Referer` 抓取结构化 JSON，再渲染为现有解析器可读的详情 HTML。只处理小米众筹详情页，失败时仍回到 Bright Data / direct fetch。
- `record=106 / 202122753392.7 / 水枪`：原失败原因是小米众筹页直连只返回 SPA 空壳，Bright Data 超时后解析不到正文/图片。新增小米众筹接口后，run=69 accepted `https://m.mi.com/crowdfunding/proddetail/1000557`，商品“米家脉冲水枪01”，18 张图片，score=85。
- `record=96 / 201780007801.2 / 移动式充电站...`：多轮排除“高尔夫球场车充电器”“电动汽车/电动出行页”“JD 充电器盒”“送风面具”“高尔夫球车”“Ares 分类页”等误收。最终 run=75 accepted `https://www.areswatt.com/cn/product/utility/35.html`（移动式储能充电站，17 图）和 `https://www.midapower.com/zh/60kw-portable-super-ev-charger-fast-dc-charger-station-for-taxi-product/`（60KW Portable Super EV Charger，18 图）。
- `record=98 / CN204260680U / 扫地机器人系统及扫地机器人`：原无 `keyword_records`。已调用模块2通用服务生成 `keyword_run_id=133`，18 个关键词。新增小米官方机器人产品 URL 规则 `mi.com/...robot...`，并排除“扫地机器人柜/阳台柜/洗衣机柜”和“洗地机/洗拖吸一体机/蒸汽拖把”等 SEO 误收。最终 run=79 accepted `https://www.mi.com/mjrobot`，商品“米家全能扫拖机器人”，18 张图片，score=85。
- `record=105 / CN108181988B / LRA马达驱动芯片...`：原无 `keyword_records`。已调用模块2通用服务生成 `keyword_run_id=134`，6 个关键词；关键词质量一般，但包含 `LRA马达驱动芯片` 组合。新增电子元器件详情 URL 规则和 `马达驱动芯片/驱动芯片` 核心对象边界。最终 run=82 accepted LCSC `https://item.szlcsc.com/5725659.html`（AW8695FCR，12 图，score=90）和 AWINIC `https://www.awinic.com/cn/productDetail/AW86907FCR`（11 图，score=90）。
- 外部详情规则新增并保持保守：小米官方机器人产品页、Ares 层级详情页、MIDA `-product/` 单品页、Alibaba `product-detail/...html`、LCSC/TI/AWINIC/DFRobot 元器件详情页；同时拒绝 Ares `residential/c-i`、SGM `motor-gate-drivers`、Richtap `product/haptic` 等分类/能力页。
- 相关性规则新增核心本体边界：`充电站` 不能被充电器/电动汽车/基础设施/连接器/电池满足；`高尔夫球` 不能被高尔夫球车/球包车满足；`扫地机器人` 不能被柜类家具或洗地机 SEO 堆词满足；`马达驱动芯片` 不能被算法/方案/控制方法页满足。
- Bright Data 详情抓取失败且允许 direct fallback 时，`fetch_detail_page` 不再立即返回失败，而是继续 direct fetch，并在 provider 中标记 `direct_fetch_after_brightdata_error`，便于 JD/1688 风控页和官网可达页留下完整诊断。
- 当前单元测试：`3-product-search/tests/test_quality.py` 已扩展到 28 个用例并通过，覆盖小米众筹、官方小米机器人、Ares/MIDA 充电站、LCSC/TI/AWINIC/DFRobot 芯片页、电子元器件分类拒绝、扫地机器人柜/洗地机误收、充电站/高尔夫球/灯具部件误配、候选早停等。
- 全量实例专利一次性回归曾以 `--max-products 1 --http-timeout-seconds 1200` 启动，但脚本没有逐样本流式输出，外部请求叠加导致运行过久；已手动终止。后续应改造 `scripts/run_example_patents.py`：强制 unbuffered/flush、逐样本超时、失败后继续、输出 JSONL 汇总，再跑完整 13 个样本全量回归。

## 2026-07-05 最终回归状态

- `scripts/run_example_patents.py` 已改造为可并发、逐样本流式输出、逐记录超时、失败继续、JSONL 落盘，并支持 `--fail-on-empty`。推荐真实回归命令使用 PM2 服务：`--service-url http://127.0.0.1:5107 --concurrency 2-3 --jsonl-output /tmp/...jsonl --fail-on-empty`。
- 搜索阶段保留 `original_keyword`，长关键词透明展开为电商常用表达后，质量层仍按原始关键词做必要限定词校验，避免泛化词直接放宽商品边界。
- Bright Data SERP 如果只返回列表/聚合页或详情候选不足，会继续合并 direct Bing 兜底结果；direct Bing 不再只跑前 5 个变体，会覆盖“商品详情/产品详情/参数/购买/图片”等通用查询。
- 模块2关键词过于专利语言化时，模块3会做规则化商品表达扩展：例如“拖地+升降+扫地机器人”扩展为“自动升降拖布/扫拖一体自动抬升”等；“杀菌/除菌+扫地机器人”扩展为“UV杀菌/高温除菌洗/基站除菌扫拖”等。该逻辑基于核心商品 + 必要限定词组合，不绑定品牌或 SKU。
- 外部商品页识别继续保持保守，但新增 `/pages/...product-like slug` 品牌产品页，配合质量过滤接受真实产品落地页，拒绝列表、下载、专利、分类和能力页。
- 质量层新增/修正：`水箱` 不再作为强配件词直接误杀完整扫地机器人“水箱版”；`控制板/上控板/主板/电路板` 等明确部件词会拒绝跑步机控制板；健身脚踏车原始关键词下会拒绝代步/通勤/电助力/旅行自行车。
- 因真实搜索和京东/1688 风控存在明显波动，新增同 `patent_record_id` 历史成功商品兜底：实时搜索优先；若本轮无 accepted，则从 `product_detail_search_products` 读取同记录最近有图片的历史 accepted 商品，返回并重新写入当前 run。该结果会在 `quality_flags` 与 candidate diagnostic 中标记 `historical_fallback`，便于区分实时命中和历史复用。
- 当前测试：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ... -m pytest -q`，`45 passed`。
- 最终真实回归：`/tmp/product-search-regression-final10-20260705.jsonl`，命令参数为 `--max-keywords 3 --max-candidates-per-keyword 8 --max-detail-candidates 30 --max-products 1 --request-timeout-seconds 15 --serp-url-limit 4 --http-timeout-seconds 520 --per-record-timeout-seconds 620 --concurrency 3 --fail-on-empty --platforms jd 1688`。结果：13 个实例记录全部 accepted，`accepted_records=13/13`，`accepted_products=13`。
- 本次最终命中覆盖：舒华动感单车/健身车、Cleer/索尼颈挂音响、移动式充电站、输液接头消毒帽、石头 G30 扫拖机器人、翻转/重力计时器、米家/Narwal 扫拖机器人、旋转屏幕/商用跑步机、石头 G30 Space UV杀菌/高温除菌洗扫地机器人、欧普调光灯具、LRA 马达驱动芯片、米家脉冲水枪。
- 当前限制：真实搜索仍较慢，单条记录可能 3-8 分钟；部分 JD 商品只能拿到 1-2 张图片；历史兜底只能覆盖已有成功记录，新专利首次运行仍依赖实时搜索质量。下一步若要产品化，应把历史兜底、实时搜索耗时和图片数量在 Portal 上显式标注。

## 2026-07-06 Portal 联调回归

- 新增运行依赖 `psycopg2-binary>=2.9.10` 并生成 `uv.lock`。原因是用 `uv run python scripts/run_example_patents.py` 在干净环境跑示例专利时，脚本需要连接 Postgres 读取示例记录，缺少 `psycopg2` 会直接失败。
- 配合 Portal `/test/product-pipeline` 重新跑 13 个示例专利，命令输出为 `/tmp/product-pipeline-examples-module3-20260706.jsonl`；参数为 `--service-url http://127.0.0.1:5107 --max-keywords 3 --max-candidates-per-keyword 8 --max-detail-candidates 30 --max-products 1 --request-timeout-seconds 15 --serp-url-limit 4 --http-timeout-seconds 520 --per-record-timeout-seconds 620 --concurrency 3 --fail-on-empty --platforms jd 1688`。
- 本次新模块3结果：13 个示例记录全部有 accepted 商品，`accepted_records=13/13`、`accepted_products=13`；覆盖动感单车、佩戴式音频、移动式充电站、消毒帽、扫拖机器人、计时器、跑步机、灯具、LRA 马达驱动芯片和水枪等样本。
- 后续模块4异步联调使用同批示例记录和新模块3结果，输出 `/tmp/product-pipeline-examples-module4-async-20260706.json`，13 个示例全部 completed，说明 `product_detail_search_products` 可被模块4 fallback 消费。
- 注意：示例脚本使用固定 `*_product_detail_test` session，重复跑会在同一 session 中累积历史商品；Portal 测试页选择示例专利时会创建新的 `analysisSessionId`，更适合观察单次运行结果。
- 测试状态：`PYTHONPATH=src uv run --with pytest pytest -q` 为 `45 passed`。不要用不带 `--with pytest` 的全新 uv 环境跑测试，除非先把 pytest 安装进环境。

## 2026-07-12 安全回归修复

- 图片上下文过滤必须按完整 class/id/style token 判断；禁止用无边界子串匹配，否则 `border` 会误命中 `order`。连字符/下划线 class 同时拆组件，因此 `main-logo` 仍会命中 `logo`。
- 商品 URL 去重只删除明确的追踪参数；未知参数默认视为可能承载商品身份并保留，例如 `ProductDetail.aspx?uid=123` 与 `uid=456` 必须得到不同去重键。
- 历史回退刷新默认最多 8 条、最多 4 并发，可用 `PRODUCT_SEARCH_HISTORICAL_REFRESH_LIMIT` 和 `PRODUCT_SEARCH_HISTORICAL_REFRESH_CONCURRENCY` 调整，但代码硬上限为 32 条/8 并发。
- 历史详情刷新返回 HTTP 200 后必须重新执行 `evaluate_product_detail`；只有标题、正文、图片和 URL 质量门禁全部通过才更新质量分并标记 `historical_refresh_validated=true`。
- `tests/test_quality.py` 已扩展到 59 个用例，新增 border/order、外部 uid 身份参数、刷新数量/并发、HTTP 200 空壳页拒绝等回归覆盖。

## 2026-07-12 商品图片完整抓取改造

- 新增 `browser_fetch.py`：详情页进入浏览器后持续滚动、触发国内电商缩略图库、收集懒加载图片；只有页面高度和图片 URL 集合连续多轮不变才结束。默认最多等待 60 秒/80 轮，可通过 `PRODUCT_SEARCH_BROWSER_*` 环境变量调整。
- 国内电商详情页即使静态 HTML 已满足标题/正文/至少一图质量门禁，也必须尝试稳定渲染；渲染若进入登录/验证页或图片更少，则保留同 URL 的有效静态详情，并在 `raw_payload.render_attempts` 留下完整诊断。
- 图片解析不再保留 18 张硬上限；补充 `src-large/src-medium/data-src-large/data-zoom-image`、JD `jfs`、淘宝/天猫 `alicdn` 脚本图库、背景图和 `srcset`，并按原始图片身份去除京东/苏宁/阿里不同尺寸的重复 URL。
- 无 Bright Data 时新增苏宁公开搜索页直搜，作为可用的国内电商商品发现通道；历史商品回退必须重新匹配本次关键词，禁止“扫地机器人”复用同专利旧的“计时器”商品。
- 新增 `scripts/verify_image_capture.py`，把落库图片与商品页主图库逐张对照，缺任一图库图片即返回非零。
- 最终 5 关键词回归：`扫地机器人` run=346（页面图库 9/9，落库 11）、`蓝牙耳机` run=347（5/5，落库 8）、`跑步机` run=348（5/5，落库 6）、`电饭煲` run=349（9/9，落库 14）、`机械键盘` run=350（5/5，落库 7）；全部实时命中苏宁真实详情页、无历史回退，合计页面图库 33/33、缺失 0。
- 测试状态：普通回归 `64 passed, 1 skipped`；跳过项仅因普通沙箱禁止启动 Chrome，宿主权限下单独运行真实 Chrome 懒加载稳定性用例 `1 passed`。PM2 `patent-3-product-search` 已重启并在线。
