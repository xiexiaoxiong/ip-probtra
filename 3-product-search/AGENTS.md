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
- 页面不能是登录/验证码/安全验证等反爬拦截页。
- 至少提取到标题、一定长度的详情文本、图片；价格、销量、厂商为增强字段，不作为唯一通过条件。
- 关键词相关性不能只看单字覆盖；当前还要求中文二字连续片段覆盖度，防止“移动式充电站”误召回“便携式蓝牙音箱”。

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

- `tests/test_quality.py` 覆盖 URL 过滤、验证码拒绝、京东 discovery page 抽取、京东标题/运费 0 清理、电商关键词透明扩展、连续片段相关性过滤；当前 7/7 通过。
- `record=94 / CN103025390B`：通过京东聚合页发现并抓取真实商品详情 `https://item.jd.com/30147313701.html`，accepted，图片与文本同源。
- `record=95 / CN107786922B`：通过“音响/音箱/耳机”透明 query 扩展找到 `https://item.jd.com/100005207111.html`，多次 render 后 accepted。
- `record=96 / 201780007801.2`：此前误召回京东蓝牙音箱；连续片段相关性过滤后已 rejected，仍需继续寻找真实“移动式充电站/高尔夫球充电站”商品。
- 批量测试必须设置预算：建议 `PRODUCT_SEARCH_MAX_CONCURRENCY=1`、`PRODUCT_SEARCH_SERP_URL_LIMIT=2-4`、`--max-detail-candidates 1-4` 逐专利运行；不要一次全量跑 13 个高预算任务。
