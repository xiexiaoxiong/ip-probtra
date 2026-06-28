## 项目概述
- **名称**: 商品检索模块
- **功能**: 从 Postgres 数据库读取关键词，调用 Coze 工作流 API 搜索商品信息，将结果保存到 Postgres 数据库

### 环境变量
| 变量名 | 说明 | 默认值 |
|-------|------|--------|
| `COZE_SEARCH_API_URL` | Coze 工作流 API 地址 | `https://66vpykvvz2.coze.site/run` |
| `COZE_SEARCH_API_TOKEN` | Coze 工作流认证 Token | (必填) |
| `COZE_SEARCH_TIMEOUT` | 单次 API 调用超时（秒） | `120` |
| `COZE_MAX_CONCURRENT` | 并发调用最大数 | `5` |
| `SECONDARY_ENRICHMENT_ENABLED` | 是否启用二次检索补全 | `true` |
| `SECONDARY_ENRICHMENT_MAX_PRODUCTS` | 单次二次检索最多补全商品数 | `10` |
| `SECONDARY_ENRICHMENT_PRODUCT_TIMEOUT_SECONDS` | 单商品二次检索预算 | `45` |
| `SECONDARY_ENRICHMENT_ENABLE_IMAGE_OCR` | 是否对第一次检索图片做 OCR | `true` |
| `SECONDARY_ENRICHMENT_ENABLE_IMAGE_VISION` | 是否用本地多模态模型读取第一次检索图片 | `true` |
| `SECONDARY_SEARCH_API_URL` | 外部搜索/拆机文章/视频搜索接口 | 空 |
| `SECONDARY_ENRICHMENT_ENABLE_DIRECT_WEB_SEARCH` | 是否启用 Bing/DuckDuckGo HTML 兜底搜索 | `true` |
| `SECONDARY_ENRICHMENT_ENABLE_COZE_EXACT_SEARCH` | 是否启用 Coze 精确二次补充 | `false` |

### 节点清单
| 节点名 | 文件位置 | 类型 | 功能描述 | 分支逻辑 | 配置文件 |
|-------|---------|------|---------|---------|---------|
| entry | `graphs/graph.py` | task | 初始化全局状态，生成数据集ID | - | - |
| get_keywords | `graphs/graph.py` (wrapper) + `graphs/nodes/get_keywords_node.py` | task | 从 Postgres 的 keyword_records 表读取关键词 | - | - |
| coze_search | `graphs/graph.py` (wrapper) + `graphs/nodes/coze_search_node.py` | task | 调用 Coze 工作流 API，传入关键词搜索商品 | - | - |
| secondary_enrichment | `graphs/graph.py` (wrapper) + `graphs/nodes/secondary_enrichment_node.py` | task | 对第一次检索到的同一商品进行二次资料补全，不新增商品 | - | - |
| save_results | `graphs/graph.py` (wrapper) + `graphs/nodes/save_results_node.py` | task | 将商品数据写入 Postgres 数据库 | - | - |
| exit | `graphs/graph.py` | task | 输出最终结果 | - | - |

**类型说明**: task(task节点) / agent(大模型) / condition(条件分支) / looparray(列表循环) / loopcond(条件循环)

**Coze搜索节点说明**:
- 调用 Coze 工作流 API，传入关键词列表，获取商品搜索结果
- 请求格式：`{"keywords": ["关键词1", "关键词2"]}`
- 认证方式：Bearer Token（通过 `COZE_SEARCH_API_TOKEN` 环境变量配置）
- 优先批量调用（所有关键词合为一次请求），若批量无结果则退回逐关键词并发调用
- 并发调用受 `COZE_MAX_CONCURRENT` 信号量限制（默认5个）
- 自动解析 Coze 响应中的商品列表，兼容多种响应格式（JSON数组/嵌套对象/文本中提取）
- 返回结果自动按 URL 去重
- 字段映射：Coze 的 `title/summary/url/source/keyword` 等字段归一化为 `product_name/description/product_url/product_source/matched_keywords` 等

**保存结果节点说明**:
- 将商品数据写入 Postgres 的 search_runs 和 search_products 表
- search_runs 表记录检索运行的元信息（数据集ID、关键词统计等）
- search_products 表记录每个商品的详细信息（名称、URL、价格、品牌、制造商、图片等）
- platforms_queried 固定为 `["Coze工作流"]`

**二次检索补全节点说明**:
- 节点位于 `coze_search` 之后、`save_results` 之前；`coze_search` 已经会增量写入部分商品，因此 Portal 的首轮 partial 模块4仍可先跑，终轮模块4读取补全后的商品。
- 默认只更新第一次检索已经存在的 `search_products`，不新增商品。
- 可接受的同一商品依据：URL 精确一致、商品 ID 精确一致或商品名称相似度达到阈值。
- 同一商品校验也支持更适合拆机文章/视频的强标识：搜索结果标题或链接包含原商品 ID，或同时命中原商品品牌和型号标识时，可接受；同品牌不同型号必须拒绝。
- accepted 来源的文本会合并到 `description`，并完整记录在 `raw_payload.secondary_enrichment`；rejected 来源只留诊断，不进入模块4比对文本。
- 默认启用第一次检索图片 OCR、第一次检索图片视觉读取、商品 URL Playwright 抓取和 HTTP 抓取；`SECONDARY_SEARCH_API_URL` 可接入搜索引擎/拆机文章/视频搜索；`SECONDARY_ENRICHMENT_ENABLE_COZE_EXACT_SEARCH=1` 可开启 Coze 精确二次搜索，但默认关闭以避免主流程耗时失控。
- 图片视觉读取需要本地多模态模型环境变量；若未配置，会自动失败隔离。图片 OCR 不依赖 LLM，使用本机 `tesseract`。
- 图片视觉读取若只返回“图片未显示/无法识别/信息不足”等无信息文本，必须 rejected，不得进入模块4描述。
- `direct_web_search` 是无 API 兜底，默认少量查询 Bing/DuckDuckGo 并解析搜索结果；所有结果仍需同一商品校验。
- accepted 搜索结果会标注 `evidence_type`：`video`、`article`、`product_page`、`search_result`。
- 关键开关：`SECONDARY_ENRICHMENT_ENABLED`、`SECONDARY_ENRICHMENT_MAX_PRODUCTS`、`SECONDARY_ENRICHMENT_TIMEOUT_SECONDS`、`SECONDARY_ENRICHMENT_PRODUCT_TIMEOUT_SECONDS`、`SECONDARY_ENRICHMENT_ENABLE_PLAYWRIGHT`、`SECONDARY_ENRICHMENT_ENABLE_IMAGE_OCR`、`SECONDARY_ENRICHMENT_ENABLE_IMAGE_VISION`、`SECONDARY_ENRICHMENT_IMAGE_LIMIT`、`SECONDARY_SEARCH_API_URL`、`SECONDARY_ENRICHMENT_ENABLE_DIRECT_WEB_SEARCH`、`SECONDARY_ENRICHMENT_DIRECT_WEB_QUERY_LIMIT`、`SECONDARY_ENRICHMENT_ENABLE_COZE_EXACT_SEARCH`、`SECONDARY_ENRICHMENT_COZE_QUERY_LIMIT`。

**独立补全接口**:

- `POST /api/enrich_search_run`
- 请求体：`{"search_run_id": 78, "max_products": 1}`，或 `{"patent_record_id": 91, "analysis_session_id": "...", "max_products": 1}`。
- 用途：对已有第一次检索结果单独重跑二次补全，便于迭代 Playwright、搜索引擎、拆机文章/视频通道，不必重新搜索商品。
- 约束：传入 `search_run_id` 时，加载和回写均限定该 run，避免同一 session 多次检索时写错历史商品。
- 响应：`total_products_count`、`updated_products_count`、`enriched_products_count`、前 20 个商品的 `accepted_sources_count/rejected_sources_count/supplement_text` 摘要。

## 子图清单
| 子图名 | 文件位置 | 功能描述 | 被调用节点 |
|-------|---------|------|---------|
| - | - | 无子图 | - |

## 技能使用
- 节点`get_keywords`使用 Postgres 数据库技能
- 节点`coze_search`使用 Coze 工作流 API（HTTP 调用）
- 节点`save_results`使用 Postgres 数据库技能

## 商品页抓取原型状态

- `src/tools/product_page_capture.py` 是独立实验工具，不接入当前模块3主流程。
- 决策：暂不默认纳入 `coze_search -> save_results` 链路。原因是该工具依赖 Playwright Chromium、页面登录态/人工验证、可选 OCR 和多模态模型，真实电商页面反爬和登录状态不可控；默认启用会降低模块3检索稳定性。
- 允许用途：对具体商品 URL 做人工或离线增强取证，输出截图、可见文字和详情图候选，供后续人工核查或单独实验。
- 若未来进入主流程，必须先满足：环境变量显式开关、单商品失败不阻断搜索、抓取超时/并发上限、输出字段与 `search_products.raw_payload` 的兼容映射、以及覆盖真实失败页面的回归测试。

## 工作流数据流
```
GraphInput (patent_record_id, analysis_session_id, input_keywords?)
    ↓
entry (初始化状态，生成数据集ID)
    ↓
get_keywords (从Postgres的keyword_records表读取关键词)
    ↓
coze_search (调用Coze工作流API搜索商品，批量或并发调用)
    ↓
secondary_enrichment (对已检索商品做二次资料补全并写回同一 search_products 行)
    ↓
save_results (写入Postgres数据库：search_runs + search_products表)
    ↓
exit (输出结果)
    ↓
GraphOutput (product_dataset_id, search_run_id, total_products_count, is_complete, error_message, enriched_products_count, enrichment_error_message)
```

## 数据库表结构
保存到 Postgres 的商品数据包含以下表：

### search_runs 表（检索运行记录）
| 字段名 | 类型 | 说明 |
|-------|------|------|
| id | int | 自增主键 |
| patent_record_id | int | 专利解析主记录ID |
| analysis_session_id | text | 分析会话ID |
| product_dataset_id | text | 数据集唯一标识 |
| retrieval_start_time | text | 检索开始时间 |
| successful_keywords_count | int | 成功检索的关键词数 |
| failed_keywords_count | int | 失败检索的关键词数 |
| total_products_count | int | 商品总数 |
| platforms_queried | json | 查询的平台列表 |
| is_complete | bool | 检索是否完整 |
| error_message | text | 错误信息 |

### search_products 表（商品记录）
| 字段名 | 类型 | 说明 |
|-------|------|------|
| id | int | 自增主键 |
| search_run_id | int | 关联search_runs.id |
| patent_record_id | int | 专利解析主记录ID |
| analysis_session_id | text | 分析会话ID |
| product_id | text | 商品唯一标识 |
| product_name | text | 商品标题 |
| product_url | text | 商品URL |
| product_source | text | 商品来源平台 |
| price | text | 商品价格 |
| brand | text | 品牌名称 |
| manufacturer | text | 制造商/工厂名称 |
| matched_keywords | text | 匹配的搜索关键词 |
| description | text | 商品描述文本 |
| picture | json | 图片URL列表 |
| raw_payload | json | 原始数据 |

`raw_payload.secondary_enrichment` 结构：

| 字段名 | 说明 |
|-------|------|
| version | 二次检索结构版本 |
| enriched_at | 补全时间 |
| sources | 来源列表，包含 source_type、identity_check、accepted/rejected/error 等 |
| supplement_text | 已通过同一商品校验且可用于模块4比对的补充文本 |
| accepted_sources_count | 有效补充来源数量 |
| rejected_sources_count | 被拒绝或失败来源数量 |

## 输入输出定义

### 工作流输入
- `patent_record_id`: 专利解析主记录ID（必填，从模块2传入）
- `analysis_session_id`: 分析会话ID（可选，用于限定范围）
- `input_keywords`: 搜索关键词列表（可选；`null`/不提供表示从数据库读取，显式空数组 `[]` 表示不搜索且不得回退数据库）

### 工作流输出
- `product_dataset_id`: 本次检索数据集唯一标识
- `search_run_id`: 商品检索运行记录ID
- `total_products_count`: 检索到的商品总数
- `is_complete`: 检索是否完整
- `error_message`: 错误信息
- `enriched_products_count`: 二次检索成功补全的商品数量
- `enrichment_error_message`: 二次检索错误信息

## 使用示例

```json
{
  "patent_record_id": 1,
  "analysis_session_id": "sess_abc123"
}
```

或者手动指定关键词：
```json
{
  "patent_record_id": 1,
  "analysis_session_id": "",
  "input_keywords": ["计时器", "厨房用品", "定时器"]
}
```

## 上下游衔接

### 上游（模块2 - 关键词生成）
- 输入：`patent_record_id`，从 `keyword_records` 表读取关键词
- 依赖字段：`keyword_text`（搜索关键词文本，非空）

### 下游（模块4 - 权利要求比对）
- 输出：写入 `search_products` 表，模块4通过 `patent_record_id` 读取
- 模块4期望的字段：`product_id`、`product_name`、`description`、`picture`、`raw_payload` 等
- 模块4会把 `raw_payload.secondary_enrichment.supplement_text` 拼入商品描述后再用现有规则比对。

## 2026-06-29 二次检索验证记录

- 新增 `tests/test_secondary_enrichment.py`，覆盖同一商品 URL 接受、无关商品拒绝、补充资料合并、通用搜索 accepted/rejected 文本过滤。
- `tests/test_product_page_capture.py` 与新增测试在宿主权限下 11/11 通过。
- 对 `analysis_1782651371368_9qeyd5` 前 2 个 1688 商品做真实小样本：能写回同一商品行并记录来源；但 Playwright 进入 1688 验证码拦截，HTTP 正文为空，未获得有效补充文本。
- Coze 精确二次搜索单查询在 25 秒内超时，因此默认关闭；如需启用，必须配合更严格的查询上限和超时。
- 本地临时搜索 API 模拟验证通过：同一商品拆机文章被 accepted，无关商品 rejected，生成可供模块4使用的 `supplement_text`。
- 新增图片 OCR 通道后，`analysis_1782651371368_9qeyd5` 第一条真实商品在不访问 1688 正文的情况下获得新增 OCR 文本并写回同一商品；模块4重新比对 run_id `analysis_1782651371368_9qeyd5-module4-secondary-enrichment-test` 已完成，`claim_compare_run_id=62`，数据库结果显示第一条商品证据引用“OCR图片文字‘三合一’”。
- 新增 `direct_web_search` 无 API 搜索兜底，默认尝试 Bing/DuckDuckGo 的少量查询并解析搜索结果；当前真实关键词未返回可接受结果。合并策略要求无 accepted 来源的新一轮二次检索不得覆盖已有成功补充。
- Portal 已透传模块3二次检索统计：`enriched_products_count`、`enrichment_error_message` 会进入 `analysis_sessions.results` 的 `module3EnrichedProductsCount`、`module3EnrichmentError`。
- 入口语义修正：`input_keywords=[]` 必须直接返回空关键词，不得从数据库读取旧关键词。新增测试覆盖空数组不读库和显式关键词清理去重；`tests/test_secondary_enrichment.py` 13/13 通过。PM2 重启后 API 验证 `POST /run` with `input_keywords: []` 返回 `total_products_count=0`、`error_message="未提供搜索关键词"`。
- 搜索结果增强：外部搜索响应会递归合并 `organic/videos/articles` 等混合列表；同一 URL/标题的 accepted/rejected 结果会去重；新增商品 ID 命中、品牌+型号命中的同一商品判定。新增测试证明同型号拆机视频 accepted、不同型号评测 rejected；`tests/test_secondary_enrichment.py` 18/18 通过。2026-06-29 已重启 `patent-3-search`，5105 `/health` 正常。
- 新增已有 run 独立补全接口 `POST /api/enrich_search_run`。真实验证 `search_run_id=78,max_products=1` 成功回写同一商品，返回 `updated_products_count=1`、`enriched_products_count=1`、accepted 来源为 OCR + 网页搜索补充；无信息视觉输出“图片未显示”已被 rejected。模块4重新比对 run `d54aa5cf-18f5-4f1b-809f-22880de3da6f` / `claim_compare_run_id=63` 完成 19 个商品，目标商品证据引用 OCR 和网页补充资料。`tests/test_secondary_enrichment.py` 20/20 通过。
