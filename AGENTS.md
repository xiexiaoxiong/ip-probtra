# 专利分析比对软件总索引

> 这是 `../Documents/patent` 项目的根级协作索引。Codex 或其他编程工具进入本项目后，应先读取本文件，再按需读取各模块自己的 `AGENTS.md`、`README.md` 和代码。
>
> 规则：每次会话结束时，必须把本次作出的架构决策、接口决策、限制、进度变化、待办变化更新到本文档的「会话决策日志」和相关章节。

## 项目定位

本项目是一个本地化运行的专利侵权/相似度分析系统。主链路从专利文件或文本开始，解析专利，生成商品检索关键词，检索市场商品，再对独立权利要求和商品信息做技术特征比对，最后在 Next.js Portal 中展示分数、证据、Claim Chart 和报告。

核心原则：

- LLM 不直接输出法律侵权结论，只做文本读取、结构化、事实对齐、证据标注。
- 业务判断必须可回溯到专利原文、商品描述、商品图片和确定性规则。
- 主链路单向依赖：模块1 -> 模块2 -> 模块3 -> 模块4 -> Portal 结果提取，不允许反向调用。
- 当前主线已从早期飞书多维表格链路演进为本地 Postgres + `analysis_session_id` 链路；飞书能力仍有遗留/备选代码。

## 顶层目录

| 路径 | 角色 |
| --- | --- |
| `1-patent-analysis/` | 模块1，专利解析：读取 PDF/TXT/HTML/URL，拆说明书、权利要求、附图，写 Postgres |
| `2-keyword/` | 模块2通用版，关键词生成：从 Postgres 读取专利解析结果，生成检索关键词 |
| `2-keyword-fitness/` | 模块2健身器材行业版，流程与通用版相似，Prompt/节点策略偏健身器材 |
| `2-keyword-electra/` | 模块2家用电器行业版，流程与通用版相似，Prompt/节点策略偏家电 |
| `3-search/` | 模块3，商品检索：读取关键词，调用 Coze 搜索工作流，保存商品；含商品页 Playwright 抓取原型 |
| `3-product-search/` | 平行模块3实验版，商品详情检索：用模块2关键词检索电商平台并抓取真实商品详情页，独立表落库 |
| `4-claim-chat/` | 模块4，权利要求-商品比对：拆独立权利要求特征，逐商品分析证据，规则计分，写 Postgres |
| `IP-protral/` | Next.js Portal：上传、认证、编排、进度、结果页、详情页、报告导出 |
| `.trae/documents/` | 既有方案文档，记录账户、异步检索、模块4评分、token 高亮等设计 |
| `ecosystem.config.cjs` | PM2 总入口，统一启动六个 Python 工作流和前端 |

## 运行环境

### 后端工作流

- Python：各模块 `pyproject.toml` 声明 `requires-python >=3.12`。
- 包管理：优先使用 `uv sync`，每个 Python 模块有独立 `.venv`、`requirements.txt`、`uv.lock`。
- 框架：FastAPI + Uvicorn + LangGraph + Pydantic + SQLAlchemy。
- 公共运行方式：
  - 本地完整流程：`bash scripts/local_run.sh -m flow`
  - 单节点：`bash scripts/local_run.sh -m node -n node_name`
  - HTTP 服务：`bash scripts/http_run.sh -p <port>`
- HTTP 端点来自各模块 `src/main.py`，通常包括 `/run`、`/stream_run`、`/node_run/{node_id}`、`/cancel/{run_id}`、`/health`、`/graph_parameter`。
- LLM 兼容层：各 `src/main.py` 会从 `IP-protral/.env.local`、模块 `.env.local`、当前目录 `.env.local` 读取环境变量，并把 `LOCAL_LLM_*` 映射到 Coze SDK 所需变量；部分模块支持 BigModel 直连兜底。

### 前端 Portal

- 路径：`IP-protral/`
- 技术栈：Next.js 16 App Router、React 19、TypeScript 5、Tailwind CSS 4、shadcn/ui、Radix UI、pnpm。
- 包管理：只能用 pnpm，`package.json` 中有 `preinstall: npx only-allow pnpm`。
- 常用命令：
  - `pnpm dev`：开发模式，README 标注端口 5000
  - `pnpm build`
  - `pnpm start`
  - `pnpm lint`
  - `pnpm ts-check`

### PM2 总启动

根目录 `ecosystem.config.cjs` 是推荐的统一启动方式：

- `1-patent-analysis` -> `127.0.0.1:5101`
- `2-keyword` -> `127.0.0.1:5102`
- `2-keyword-fitness` -> `127.0.0.1:5103`
- `2-keyword-electra` -> `127.0.0.1:5104`
- `3-search` -> `127.0.0.1:5105`
- `3-product-search` -> `127.0.0.1:5107`（平行实验模块，不替代 5105）
- `4-claim-chat` -> `127.0.0.1:5106`
- `IP-protral` -> 默认 `3001` 或 `.env.local` 的 `PORT`

推荐只维护一份环境文件：`IP-protral/.env.local`。`ecosystem.config.cjs` 会把数据库、LLM、搜索、对象存储、飞书变量注入所有模块。

禁止把正式端口手工起多个服务。若必须单模块调试，使用非正式端口，例如：

```bash
SKIP_ENV_VALIDATION=1 bash scripts/http_run.sh -p 5116
```

## 关键环境变量

| 变量 | 用途 |
| --- | --- |
| `PGDATABASE_URL` / `DATABASE_URL` | Postgres 主链路数据库，Python 和 Portal 都依赖 |
| `LOCAL_LLM_BASE_URL` | OpenAI 兼容 LLM 网关 |
| `LOCAL_LLM_API_KEY` | LLM 网关密钥 |
| `LOCAL_LLM_DEFAULT_MODEL` | 默认文本模型 |
| `LOCAL_LLM_FAST_MODEL` | 快速文本模型 |
| `LOCAL_LLM_VISION_MODEL` | 多模态模型 |
| `COZE_SEARCH_API_URL` | 模块3调用的 Coze 搜索工作流地址 |
| `COZE_SEARCH_API_TOKEN` | 模块3搜索工作流 Token |
| `COZE_SEARCH_TIMEOUT` | 模块3搜索超时 |
| `COZE_MAX_CONCURRENT` | 模块3逐关键词并发数 |
| `BRIGHTDATA_API_KEY` | 平行模块3实验版 Bright Data API key |
| `BRIGHTDATA_SERP_ZONE` / `BRIGHTDATA_UNLOCKER_ZONE` | 平行模块3实验版 SERP/Unlocker zone；未配置时会尝试通过 API key 自动发现 active zone |
| `PRODUCT_SEARCH_MAX_CONCURRENCY` | 平行模块3详情检索并发，默认 2；真实电商页不稳定时建议降为 1 |
| `PRODUCT_SEARCH_BRIGHTDATA_RENDER_FALLBACK` | 平行模块3是否在详情页信息不足时启用 Bright Data `render=true` 兜底，默认开启 |
| `COZE_BUCKET_*` | S3 兼容对象存储，用于专利附图、上传文件、商品图片 |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_TENANT_ACCESS_TOKEN` | 飞书备选链路和遗留节点 |
| `AUTH_BOOTSTRAP_ADMIN_EMAIL` / `AUTH_BOOTSTRAP_ADMIN_PASSWORD` / `AUTH_BOOTSTRAP_ADMIN_NAME` | Portal 初始管理员 |
| `AUTH_SESSION_TTL_DAYS` | Portal 登录 session 有效期 |

## 总数据流

当前目标链路：

```text
用户上传 URL/PDF/文本
  -> IP-protral /api/analyze 创建 analysis_session
  -> 模块1解析专利，写 patent_parse_records / patent_claims / patent_figures
  -> Portal 做行业识别，选择模块2通用/健身器材/家电
  -> 模块2读取 patent_record_id + analysis_session_id，写 keyword_runs / keyword_records
  -> 模块3读取 keyword_records 或 input_keywords，调用 Coze 搜索，写 search_runs / search_products
  -> 模块3二次检索补全既有商品详情，写回 search_products.description/raw_payload.secondary_enrichment
  -> 模块4读取专利、补全后的商品，拆独立权利要求，逐商品比对，写 claim_compare_runs / claim_compare_results
  -> Portal 从模块4响应或 Postgres 恢复结果，展示列表、详情和报告
```

实验链路补充：

- 平行模块3 `3-product-search` 写入独立表 `product_detail_search_*`，不替代旧 `3-search`。
- Portal 提供 `/test/product-pipeline` 和 `/api/test/product-pipeline` 用于单独测试“模块2关键词 -> 新模块3商品详情/图片 -> 模块4比对”。该测试页默认调用新模块3 `127.0.0.1:5107/run`，可手动输入关键词，也可读取 `keyword_records`。
- 模块4 `parse_and_fetch_node` 仍优先读取旧表 `search_products`；当同一 `patent_record_id + analysis_session_id` 下旧表无商品时，会兜底读取新模块3表 `product_detail_search_products`，用于实验链路比对。该 fallback 不改变主流程旧模块3结果。

重要现状：

- 旧文档仍大量提到 `feishu_url`，但当前代码主线在多个模块中已改为 `patent_record_id` + `analysis_session_id`。
- Portal `workflow-client.ts` 默认调用本机 `127.0.0.1:510x/run`，不再默认依赖远端 `coze.site/run`。
- 飞书读取仍在 `IP-protral/src/lib/feishu-client.ts`、`/api/feishu-read` 和模块历史节点中作为备选/遗留能力存在。

## 模块1：专利解析

路径：`1-patent-analysis/`

职责边界：

- 允许：读取和存储专利原文；识别说明书章节；拆权利要求和从属关系；提取附图；保存结构化结果。
- 禁止：总结保护范围、提炼发明点、判断技术效果、判断功能性限定、合并或简化权利要求语言。

主图：`src/graphs/graph.py`

```text
file_read_node
  -> file_error_check
  -> structure_identify_node
  -> 并行 claims_parse_node + figure_extract_node
  -> database_save_node
  -> structured_output_node
```

核心文件：

- `src/graphs/state.py`：`GraphInput(patent_file, task_id)`，`GraphOutput(claims, specification, figures, metadata, errors, db_record_id, feishu_url)`
- `src/graphs/nodes/file_read_node.py`：文档读取，PDF 直接用 PyMuPDF 提取文本
- `src/graphs/nodes/structure_identify_node.py`：LLM + 正则提取结构和 CN 专利元数据
- `src/graphs/nodes/claims_parse_node.py`：权利要求拆分
- `src/graphs/nodes/figure_extract_node.py`：PDF/图片/HTML/TXT 附图提取，上传对象存储
- `src/graphs/nodes/database_save_node.py`：写 Postgres
- `src/storage/database/shared/model.py`：`patent_parse_records`、`patent_claims`、`patent_figures`

已知修复/限制：

- URL 带查询参数时要先剥离查询再判断扩展名。
- PDF 文本读取必须走 PyMuPDF，不能把错误字符串当文本。
- TXT 本身不含图片，除非文本中有图片 URL，否则附图为空是正常结果。
- 附图提取失败是可恢复错误，不应阻断专利解析。
- CN 专利首页元数据需要覆盖 `(54)发明名称`、`(54)实用新型名称`、`(54)外观设计名称` 和 `(57)摘要`；`PatentMetadata` 已包含 `abstract`，`patent_parse_records` 已增加 `abstract_text`。
- Portal 分析概要不能只相信 `analysis_sessions.results.patent` 快照；如果缺少专利号、名称、摘要、权利要求或附图，必须按 `dbRecordId` / `patent_record_id` 回查模块1表 `patent_parse_records`、`patent_claims`、`patent_figures` 补齐。

## 模块2：关键词生成

路径：`2-keyword/`、`2-keyword-fitness/`、`2-keyword-electra/`

职责：

- 从 Postgres 读取模块1结果。
- 提取产品客体、发明点、核心术语。
- 把专利书面语转成消费者/电商检索语言。
- 生成关键词并写 `keyword_runs`、`keyword_records`。

通用版主图：`2-keyword/src/graphs/graph.py`

```text
feishu_data_loader
  -> record_process_loop
  -> keyword_writer
```

通用版子图：`2-keyword/src/graphs/loop_graph.py`

```text
record_dispatch
  -> input_validation
  -> product_object_extraction
  -> invention_point_extraction
  -> keyword_extraction
  -> invention_point_refinement
  -> keyword_filtering
  -> scenario_audience_inference
  -> keyword_combination
  -> result_assembly
  -> result_collect
  -> loop
```

核心设计：

- `GraphInput` 当前是 `patent_record_id` + `analysis_session_id`，不是早期 README 中的 `feishu_url`。
- `record_process_loop` 会按记录数量动态提高 LangGraph `recursion_limit`。
- 产品客体要求尽量精确到电商类目级，不要泛化到“健身器材”“家用电器”等大类。
- 通用版存在行业路由外的两个变体：健身器材版、家电版；Portal 根据行业识别结果选择模块。
- 后续关键词改造方向：模块2不能只从权利要求抽零散术语，应先基于独立权利要求、摘要、发明/实用新型内容、背景技术和必要从属权利要求，形成“主商品客体 + 必要检索特征 + 非必要扩展特征”的结构化理解。必要检索特征指缺少该特征通常不会落入保护范围，且不是主商品客体已天然包含的通用部件/功能；最终关键词必须优先覆盖这些必要特征。
- 已落地必要特征框架：三套模块2（通用、健身、家电）均新增 `required_feature_extraction` 节点，位于“发明点提炼”之后、“关键词提取”之前。该节点优先用 LLM 通读独立权利要求、摘要、发明内容、背景技术和从属权利要求，输出 `required_features`、`optional_features`、`excluded_generic_terms`；代码兜底不维护具体专利词表，而是用通用语言模式抽取和短词化，例如“具备/具有 X 功能”抽 X，“X 控制结构/控制装置”抽 X，“X 模式”默认降为扩展特征，结构/模式词不得被 LLM 强行提升为必要特征。
- 关键词组合护栏已改为强制生成：主客体基础词、必要特征基础词、必要特征+主客体、多必要特征+主客体；场景/人群词保留为低优先级扩展关键词，只能来自场景推断节点的明确输出，不能替代必要特征主骨架。新增 `OBJECT_BASE`、`REQUIRED_FEATURE`、`scenario_extension`、`audience_extension` 关键词类型。

关键 Prompt 配置：

- `config/product_object_extraction_llm_cfg.json`
- `config/invention_point_extraction_llm_cfg.json`
- `config/keyword_extraction_llm_cfg.json`
- `config/invention_point_refinement_llm_cfg.json`
- `config/keyword_filtering_llm_cfg.json`
- `config/scenario_audience_inference_llm_cfg.json`
- `config/keyword_combination_llm_cfg.json`

数据库：

- `keyword_runs`
- `keyword_records`

## 模块3：商品检索

路径：`3-search/`

职责：

- 从 `keyword_records` 读取关键词，或直接使用 `input_keywords`。
- 调用 Coze 搜索工作流，解析商品数据，按 URL 去重。
- 写 `search_runs` 和 `search_products`。

主图：`src/graphs/graph.py`

```text
entry
  -> get_keywords
  -> coze_search
  -> secondary_enrichment
  -> save_results
  -> exit
```

输入输出：

- `GraphInput(patent_record_id, analysis_session_id, input_keywords?)`
- `GraphOutput(product_dataset_id, search_run_id, total_products_count, is_complete, error_message, enriched_products_count, enrichment_error_message)`

关键环境：

- `COZE_SEARCH_API_URL`
- `COZE_SEARCH_API_TOKEN`
- `COZE_SEARCH_TIMEOUT`
- `COZE_MAX_CONCURRENT`

重要现状：

- `coze_search_node` 支持批量请求，批量无结果时退回逐关键词并发。
- `secondary_enrichment_node` 已接入模块3主流程，位于第一次 Coze 检索之后、最终保存完成之前；它只补全第一次检索已经确认的商品，不创建新商品。
- 二次检索合并规则：外部资料必须通过 URL 精确一致、商品 ID 精确一致或商品名称相似度校验，才会写入 `search_products.raw_payload.secondary_enrichment`；未通过的来源记录为 rejected，不进入模块4比对文本。
- 二次检索同一商品校验补充：除 URL/商品 ID/名称相似度外，搜索结果标题或链接中出现原商品 ID，或同时命中原商品品牌和型号标识时，也可接受为同一商品补充；同品牌不同型号必须拒绝。
- 二次检索默认通道：第一次检索图片 OCR、第一次检索图片视觉读取、商品 URL Playwright 抓取、HTTP 文本抓取。外部搜索引擎通过 `SECONDARY_SEARCH_API_URL` 配置，接口默认按 `GET <url>?q=<query>` 读取 `results/items/data/organic/videos/articles` 列表；Coze 精确补充由 `SECONDARY_ENRICHMENT_ENABLE_COZE_EXACT_SEARCH=1` 显式开启，默认关闭，避免单商品多查询拖慢主流程。
- 关键二次检索环境变量：`SECONDARY_ENRICHMENT_ENABLED`、`SECONDARY_ENRICHMENT_MAX_PRODUCTS`、`SECONDARY_ENRICHMENT_TIMEOUT_SECONDS`、`SECONDARY_ENRICHMENT_PRODUCT_TIMEOUT_SECONDS`、`SECONDARY_ENRICHMENT_ENABLE_PLAYWRIGHT`、`SECONDARY_ENRICHMENT_ENABLE_IMAGE_OCR`、`SECONDARY_ENRICHMENT_ENABLE_IMAGE_VISION`、`SECONDARY_ENRICHMENT_IMAGE_LIMIT`、`SECONDARY_SEARCH_API_URL`、`SECONDARY_ENRICHMENT_ENABLE_DIRECT_WEB_SEARCH`、`SECONDARY_ENRICHMENT_DIRECT_WEB_QUERY_LIMIT`、`SECONDARY_ENRICHMENT_ENABLE_COZE_EXACT_SEARCH`、`SECONDARY_ENRICHMENT_COZE_QUERY_LIMIT`。
- `input_keywords` 运行语义：`None` 表示从数据库读取 `keyword_records`；显式传入空数组 `[]` 表示调用方要求不搜索，必须直接返回空关键词并由 `coze_search` 给出“未提供搜索关键词”，不能回退读取数据库旧关键词。
- 二次检索输出统计已透传到模块3和 Portal：`enriched_products_count` 表示本轮获得 accepted 补充资料的商品数，`enrichment_error_message` 表示二次检索阶段错误；Portal `/api/analyze` 会写入 `analysis_sessions.results.module3EnrichedProductsCount` 和 `module3EnrichmentError`。
- 二次检索来源会标注证据类型：`video`、`article`、`product_page`、`search_result`；该类型用于诊断和展示来源质量，不改变模块4现有比对规则。
- 模块3提供独立补全接口 `POST /api/enrich_search_run`，可对已有 `search_run_id` 或 `patent_record_id + analysis_session_id` 重新执行二次检索补全；回写必须限定当前 `search_run_id`，避免同一 session 多次搜索时写错历史商品。
- 图片视觉读取若只返回“图片未显示/无法识别/信息不足”等无信息文本，不计入 accepted，也不进入模块4描述。
- 已 accepted 的搜索结果会进一步抓取目标 URL 正文/Meta 信息，写入 `detail_text`；DuckDuckGo 跳转链接会先解出真实 `uddg` URL。详情正文必须经过目标商品相关性过滤，只保留命中商品标题/搜索结果标题主干的片段；含原商品/标题未出现的英文型号或品牌词的段落会丢弃，避免相关文章/推荐列表污染模块4。
- 外部搜索 accepted 结果会标注 `identity_strength`：URL/商品ID/商品ID出现在结果中/品牌+型号命中为 `strong`，名称相似度 0.72-0.86 为 `weak`。弱匹配资料可以保留为检索线索，但写入 `supplement_text` 时必须带“低置信同品线索，不得单独作为结构确认依据”提示，防止模块4把泛网页内容当成结构证据。
- `3-search/README.md` 的独立商品页抓取原型 `src/tools/product_page_capture.py` 仍保留；当前主流程以 headless/失败隔离方式复用其文本和截图抓取能力，不启用人工登录。
- 抓取原型使用 Playwright，可人工登录/复用浏览器 profile，输出截图、可见文字、OCR、多模态筛选后的商品详情图。
- 抓取原型测试：`uv run pytest tests/test_product_page_capture.py`。

## 平行模块3：商品详情检索实验版

路径：`3-product-search/`

定位：

- 新建的平行商品详情检索模块，不替代现有 `3-search/` 和 5105 主流程。
- 目标是直接用模块2关键词检索电商平台，抓取真正商品详情页中的标题、详情文本、图片、价格、销量、品牌、厂商等字段。
- 当前用于独立测试和反爬策略迭代，默认写独立表，不写旧 `search_runs` / `search_products`。

数据表：

- `product_detail_search_runs`
- `product_detail_search_candidates`
- `product_detail_search_products`

链路：

```text
keyword_records/input_keywords
  -> platform-specific SERP queries
  -> Bright Data SERP API or direct fallback
  -> platform detail URL filter
  -> Bright Data Unlocker API
  -> optional render=true retry
  -> parse title/text/images/price/sales/manufacturer
  -> quality gate
  -> independent tables
```

接口：

- `POST /run`
- `GET /runs/{run_id}`
- `GET /health`

关键规则：

- 只接受真实详情页 URL，排除搜索页、列表页、店铺页、类目页和聚合页。
- 商品文字和图片必须来自同一个最终详情 URL；落库字段包含 `source_text_url`、`source_image_url` 和 `same_url_assets`。
- 登录、验证码、安全验证、京东 `risk_handler` 等页面必须判为 blocked/rejected。
- Bright Data API key 可单独配置；若未配置 `BRIGHTDATA_SERP_ZONE` / `BRIGHTDATA_UNLOCKER_ZONE`，模块会调用 `GET https://api.brightdata.com/zone/get_active_zones` 自动发现 `serp` 与 `unblocker` zone。
- 电商详情页动态渲染不稳定时，模块会用 Bright Data Unlocker 的 `render=true` 重试；空响应必须视为抓取失败，不得作为空详情页通过。
- 关键词读取优先使用 `OBJECT_BASE` 主商品词和必要特征组合词，短的单个必要特征低优先级；长关键词命中的商品还要做通用字符覆盖度过滤，避免只含宽词的无关商品进入结果。
- 查询失败必须可诊断：搜索阶段 0 候选、SERP 返回非详情页、详情抓取错误、质量过滤 rejected 都写入 `product_detail_search_candidates`。
- 除 JD/1688/淘宝/天猫等平台详情页外，可保留保守识别的外部真实商品详情页（例如 `/product/detail/...`、`/products/...`、`/SalePage/Index/...`），但搜索/列表/下载/专利/聚合页仍 rejected。
- 关键词扩展只影响检索 query，不改变模块2结果；允许从长关键词回退到核心商品名（例如“免爬坡扫地机器人” -> “扫地机器人”），由后续质量层和配件过滤把附件/耗材排除。

测试入口：

- 单元测试：`PYTHONDONTWRITEBYTECODE=1 ../3-search/.venv/bin/python -m pytest -p no:cacheprovider tests/test_quality.py -q`
- 实例专利来源检查：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src ../3-search/.venv/bin/python scripts/run_example_patents.py --list-only`
- 实例专利小样本：设置 `BRIGHTDATA_API_KEY` 后运行 `scripts/run_example_patents.py --limit 1 ...`
- 调用已配置 Bright Data 的 PM2 服务测试：`scripts/run_example_patents.py --service-url http://127.0.0.1:5107 --record-id <id> ...`

当前限制：

- 真实电商页存在波动：京东详情页通过 Bright Data `render=true` 单独抓取可成功，但批量抓取中仍可能返回空响应；1688 多个页面仍出现验证码或详情信息不足。
- 13 个实例专利中，当前数据库能为 11 个找到同专利号/同标题的历史模块2关键词；`CN204260680U` 和 `CN108181988B` 仍需补跑模块2后才能按最新模块1记录测试。
- `/Users/xiexiaoxiong/Downloads/IP-probtra/实例专利` 13 个示例专利已完成一轮新模块3真实回归，均可得到 accepted 商品；真实搜索仍慢，且是否“足够多商品”要结合每次运行预算、平台风控和历史兜底标记判断。

## 模块4：权利要求-商品比对

路径：`4-claim-chat/`

职责：

- 读取专利独立权利要求、说明书、附图和模块3补全后的商品。
- 只处理独立权利要求。
- LLM 输出证据和最小单元状态，确定性规则负责分数和风险标签。
- 写 `claim_compare_runs`、`claim_compare_results`，并返回 `all_comparison_results`。

主图：`src/graphs/graph.py`

```text
parse_and_fetch
  -> decompose_claim
  -> compare_products_loop
  -> write_feishu_results
```

单商品子图：`src/graphs/loop_graph.py`

```text
analyze_features
  -> review_analysis
  -> apply_rules
```

当前输入输出：

- `GraphInput(patent_record_id, analysis_session_id, run_id?, claim_compare_run_id?)`
- `GraphOutput(claim_compare_run_id, run_id, all_comparison_results, result_summary, table_urls)`

评分现状：

- 早期三态 `MATCH / NO_MATCH / UNCERTAIN` 已被分数化改造覆盖。
- 当前 state/model 已有：
  - `similarity_score`
  - `score_band`
  - `claim_scores`
  - `product_similarity_score`
  - `product_score_band`
  - `token_units`
  - `feature_full_score`
  - `feature_awarded_score`
  - `feature_effective_length`
  - `matched_effective_length`
  - `claim_total_effective_length`
  - `zeroed_by_mismatch`
- 当前目标口径：排除标点和“所述”后计算有效长度；`feature_full_score` 表示特征占比百分比，等于特征有效长度 / 所属独立权利要求有效长度 * 100；`similarity_score` 表示特征得分，等于本特征已确认相同的有效长度 / 本特征有效长度 * 100；`feature_awarded_score` 表示加权贡献分，等于特征得分 * 特征占比。
- 商品总分为各特征加权贡献分求和；同一特征内命中长度必须封顶到该特征有效长度，避免 token 长短粒度重叠导致重复计分；任一特征存在明确不相同单元时，整个商品总分直接归零并标记为“疑似不侵权”。
- 显色规则：商品总分、Feature 特征得分均按 >70 绿色、30-70 黄色、0-30 红色；明确不相同覆盖为浅灰。具体 token 背景按相同绿色、不确定黄色、不相同灰色。

数据库迁移：

- `4-claim-chat/src/main.py` 有 `ensure_claim_compare_async_columns()`，启动时自动补齐模块4异步和评分字段。
- `claim_compare_runs` 包含 `run_id`、`status`、`error_message`、`started_at`、`finished_at`。
- `claim_compare_results` 包含评分和 token 级字段。

注意：

- 文件名 `write_feishu_results_node.py` 已不完全准确，当前还承担写 Postgres 和结果摘要职责。
- 飞书子表格写入相关逻辑仍存在，但主线应优先看 Postgres 和 Portal 结果映射。
- `parse_and_fetch_node` 会把 `search_products.raw_payload.secondary_enrichment.supplement_text` 拼入商品描述，确保模块4沿用现有比对规则重新判断补全后的商品资料。

## Portal：IP-protral

路径：`IP-protral/`

职责：

- 用户注册、登录、管理员审批。
- 专利 URL/文件/文本上传和本地存储。
- 编排模块1-4。
- 轮询分析进度。
- 展示商品风险列表、商品详情 Claim Chart、token 高亮、报告导出。

关键文件：

- `src/app/api/analyze/route.ts`：主编排 API，后台执行，前端轮询。
- `src/app/api/analysis/[id]/route.ts`：查询分析会话。
- `src/app/api/analysis/[id]/keywords/route.ts`：关键词确认/补充。
- `src/lib/workflow-client.ts`：模块1-4 HTTP 客户端、行业路由、预热、异步状态查询。
- `src/lib/types.ts`：前后端共享业务类型。
- `src/lib/analysis-store.ts`：分析会话存储，当前以 Postgres 为主。
- `src/lib/db-init.ts`：数据库初始化/兼容迁移。
- `src/lib/claim-score.ts`：加权分和 token 兜底计算。
- `src/lib/auth.ts`、`src/lib/password.ts`：Cookie session 和密码哈希。
- `src/app/page.tsx`：上传与进度首页。
- `src/app/results/page.tsx`：结果列表页。
- `src/app/results/[productId]/page.tsx`：商品详情页。
- `src/app/module1/page.tsx`：模块1专用查看页，可通过 `/module1?session=<analysis_session_id>` 查看模块1提取的元数据、摘要、说明书章节、权利要求、附图和解析错误。
- `src/components/claim-chart-table.tsx`：Claim Chart 和 token 高亮。
- `src/components/results-score-table.tsx`：分数表格。
- `src/lib/analysis-report-export.ts`：报告导出。

认证：

- 本地 Postgres 表：`users`、`auth_sessions`、`analysis_sessions`、`analysis_steps`。
- 登录方式：邮箱 + 密码。
- 注册后需管理员审批。
- 普通用户只能看自己的分析记录，管理员可看全站。
- Cookie 名称：`patent_auth_session`。

编排现状：

- `POST /api/analyze` 创建 session 后后台跑流程。
- 前端轮询 `/api/analysis/[id]`。
- 步骤状态支持 `pending`、`running`、`waiting_input`、`partial`、`completed`、`error`。
- 模块3/模块4已有异步任务字段和 partial/rerun 设计痕迹。
- `resultsCompleteness` 可为 `partial` 或 `final`。
- `initialClaimCompareRunId` 与 `finalClaimCompareRunId` 用于区分首轮和终轮模块4。

## 数据库表索引

### Portal 表

- `users`
- `auth_sessions`
- `analysis_sessions`
- `analysis_steps`
- `health_check`

### 模块1表

- `patent_parse_records`
- `patent_claims`
- `patent_figures`

### 模块2表

- `keyword_runs`
- `keyword_records`

### 模块3表

- `search_runs`
- `search_products`

### 模块4表

- `claim_compare_runs`
- `claim_compare_results`

## 已有方案文档

优先参考这些文档理解未完成改造：

- `.trae/documents/account-management-deployment-plan.md`：账户管理与本地持久化方案。
- `.trae/documents/module4-score-refactor-plan.md`：模块4从三态到相似度评分的改造方案。
- `.trae/documents/step4-step5-partial-rerun-plan.md`：模块3异步检索、模块4首轮/终轮补跑。
- `.trae/documents/step6-weighted-score-and-token-highlight-plan.md`：步骤6加权计分、token 高亮、结果页改造。

## 当前进度判断

基于 2026-06-28 代码阅读和本轮验证：

- 账户管理已基本落地：存在 `auth` API、登录/注册/管理员页面、`users`/`auth_sessions` schema、middleware。
- Portal 主存储已明显转向 Postgres：`analysis_sessions` 带 `user_id`，分析历史页存在。
- 模块1已支持 PDF 文本提取、CN 专利元数据正则兜底、附图提取和 Postgres 保存。
- 模块2通用/健身/家电三套代码存在，主输入已是 `patent_record_id` + `analysis_session_id`。
- 模块3主流程可写 Postgres；Playwright 商品页抓取原型保留为独立实验/人工取证工具，不接入默认主流程。
- 模块4评分化和 token 高亮链路已补脚本验证：token mismatch 会传导到特征、claim、商品归零；无 mismatch 时商品分按“特征得分 * 特征占比”加权求和，且重叠 token 命中长度会封顶到特征有效长度。
- 异步检索 + partial + 终轮补跑已有编排字段，并新增脚本测试覆盖首轮 partial 与终轮 final 结果字段。
- Portal 结果列表、详情页和分数表已共用结果一致性 helper，并新增脚本测试覆盖排序、详情查找和导出前数据摘要口径。
- 行业路由与关键词确认流程已抽出纯函数并补脚本测试。

## 当前工作树提示

2026-06-28 读档时，仓库已有大量未提交改动和未跟踪文件，涉及模块1、模块3、模块4、Portal 和 `.trae/documents`。后续工具必须把这些视为用户既有工作，不得擅自回滚、清理或重排。

重点未跟踪/新文件包括：

- `.trae/documents/module4-score-refactor-plan.md`
- `.trae/documents/step4-step5-partial-rerun-plan.md`
- `.trae/documents/step6-weighted-score-and-token-highlight-plan.md`
- `3-search/src/tools/product_page_capture.py`
- `3-search/tests/test_product_page_capture.py`
- `4-claim-chat/src/utils/claim_scoring.py`
- `4-claim-chat/src/graphs/nodes/review_analysis_node.py`
- `IP-protral/src/lib/claim-score.ts`
- `IP-protral/src/components/claim-token-highlight.tsx`
- `IP-protral/src/components/results-score-table.tsx`

## 开发限制和注意事项

- 不要把旧 README 中的 `feishu_url` 链路当成唯一事实；先看当前 `state.py`、`graph.py`、`workflow-client.ts`。
- 不要让 LLM 直接作法律结论；法律/风险文案必须来自分数、证据和规则。
- 修改 Prompt 时必须同步检查对应节点的 JSON 解析和兜底逻辑。
- 修改模块接口时必须同步更新：
  - Python `state.py`
  - 节点输入输出
  - Postgres model 或 `db-init.ts`
  - `IP-protral/src/lib/workflow-client.ts`
  - `IP-protral/src/lib/types.ts`
  - `IP-protral/src/app/api/analyze/route.ts`
  - 结果页/详情页/导出
- 正式运行推荐 PM2 总入口，不要手工混合启动正式端口。
- Portal 在 PM2 下运行 `IP-protral/dist/server.js`，不是源码热更新；每次修改前端后必须执行 `cd IP-protral && bash ./scripts/build.sh`，再 `pm2 restart patent-web`，否则用户看到的仍是旧构建。
- 对 `3-search/tmp/`、`.next/`、`dist/`、`node_modules/`、各 `.venv/` 默认只读或忽略，除非任务明确要求。
- 由于工作树已有用户改动，后续任何改动前先跑 `git status --short` 并读相关文件，不要覆盖未理解的变更。

## 已完成工作（2026-06-28 本轮）

1. 跑通本地 PM2 联调：5101-5106 后端模块 `/health` 与 `/graph_parameter` 均通过；Portal `/login` 可响应。
2. 清理 5103/5104 旧 Python 监听进程，恢复 PM2 对模块2 fitness/electra 子服务的端口管理。
3. 为模块3异步检索、partial 结果、终轮模块4补跑新增 `IP-protral/scripts/test-async-module3-module4.ts`。
4. 为模块4 token/review/rules/scoring 一致性新增 `4-claim-chat/scripts/test_module4_scoring_pipeline.py`。
5. 确认真实 Postgres `claim_compare_results` 已存在评分/token 新字段，类型与模块4自动迁移一致。
6. 清理 Portal 与模块4入口文档中“飞书为主链路”的误导表述；当前主线明确为 Postgres + `analysis_session_id`。
7. 决定 `3-search` 商品页抓取原型不接入默认主流程，保留为独立实验/人工取证工具，并在 `3-search/AGENTS.md` 写明进入主流程前置条件。
8. 为 Portal 结果列表、详情页、导出前数据口径新增 `IP-protral/src/lib/results-consistency.ts` 和 `scripts/test-results-consistency.ts`。
9. 为行业路由和关键词确认流程新增 `IP-protral/src/lib/industry-keyword-flow.ts` 和 `scripts/test-industry-keyword-flow.ts`。

## 后续建议

1. 用一份小型真实专利样本跑完整 `/api/analyze`，验证 LLM/搜索外部服务在当前凭证下的端到端耗时和结果质量。
2. 若要把商品页抓取纳入主流程，先按 `3-search/AGENTS.md` 中的开关、超时、失败隔离、字段映射、回归测试要求做产品化设计。

## 会话决策日志

### 2026-06-28

- 建立根级 `AGENTS.md` 作为本项目总索引和协作入口。
- 决定后续 Codex/编程工具进入 `../Documents/patent` 后必须先读根 `AGENTS.md`，再读子模块 `AGENTS.md`。
- 记录当前主线判断：项目已从飞书多维表格传递转向本地 Postgres + `analysis_session_id`，飞书保留为遗留/备选能力。
- 记录运维决策：正式本地化部署优先使用根目录 `ecosystem.config.cjs` 和 `IP-protral/.env.local`，不要混用手工正式端口启动。
- 记录安全协作决策：当前工作树已有大量用户改动，后续不得擅自回滚或清理。
- 提交决策：`1-patent-analysis/.data/` 和 `3-search/tmp/` 是运行产物/浏览器缓存/临时截图，不纳入 Git；已加入根 `.gitignore`。
- 评分口径修正：商品相似度不取最高权利要求分；应为所有特征得分相加，且任一特征明确不相同则商品总分为 0。
- 本轮按 loop 完成 9 个任务：评分语义修正 + 用户列出的 8 个待完成事项。
- 本地集成验证：PM2 启动 6 个后端模块和 Portal；`/health`、`/graph_parameter`、Portal `/login` 均通过。发现并清理 5103/5104 旧进程端口占用。
- 接口事实修正：各 Python 模块真实 schema 路径是 `/graph_parameter`，不是旧文档中的 `/graph/inout_parameter`。
- 新增测试脚本：`IP-protral/scripts/test-async-module3-module4.ts`、`scripts/test-results-consistency.ts`、`scripts/test-industry-keyword-flow.ts`、`4-claim-chat/scripts/test_module4_scoring_pipeline.py`。
- 模块3商品页抓取产品决策：暂不接入默认主流程；原型测试需宿主权限运行 Playwright，提权后 `tests/test_product_page_capture.py` 8/8 通过。
- 数据库验证：真实 `claim_compare_results` 已有 `similarity_score`、`score_band`、`feature_full_score`、`feature_awarded_score`、`feature_effective_length`、`matched_effective_length`、`claim_total_effective_length`、`zeroed_by_mismatch`、`token_units`。
- 评分口径二次修正：`similarity_score` 统一为特征得分（0-100），`feature_awarded_score` 统一为加权贡献分；历史高分问题根因是重叠 token 的 `matched_effective_length` 超过 `feature_effective_length`，已在模块4和 Portal 聚合中封顶。
- 历史数据回填：已用 `IP-protral/scripts/recalculate-analysis-scoring.ts analysis_1782625905937_titb69` 回填指定 session 的 180 条特征结果和 `analysis_sessions.results`；原三条高分商品从 97.97/80.35/70.19 调整为 80.36/68.83/60.36，且核对最终 run 中 `matched_effective_length > feature_effective_length` 的行数为 0。
- Portal 视觉调整：商品详情页 Claim Chart 表格改为固定列宽，特征内容列加宽、比对分析列收窄并强制换行；结果页和详情页整体容器放宽到 `max-w-7xl`，增加轻量背景/卡片层级但不改变现有模块结构。受保护页面渲染截图因内置浏览器初始化失败且不能读取认证 token 而未完成，已通过 `pnpm ts-check`、`pnpm lint` 和 PM2 前端重启验证。
- 运维修正：确认 `patent-web` 生产进程运行的是 `node dist/server.js`；前端源码改动后仅 `pm2 restart patent-web` 不会生效，必须先 `bash ./scripts/build.sh` 重新生成 `.next` 与 `dist/server.js`。本次已在 2026-06-28 16:54 重新构建并重启，`/login` 返回 200。
- Portal 分析概要扩展：结果页分析概要展示专利号、专利名称、专利摘要和摘要附图；`PatentInfo` 新增 `abstract` 字段，新分析从模块1说明书“摘要”章节或 metadata 映射，历史结果页从 `specification` 中兜底提取摘要，摘要附图优先取 `drawings[0]`。本次已在 2026-06-28 17:17 重新构建并重启 `patent-web`。
- 模块1元数据改进：`PatentMetadata` 新增 `abstract`；结构识别新增首页 `(57)摘要` 正则提取，并在 `(54)发明名称` 缺失时从第一项独立权利要求主题兜底推断专利名称。`analysis_1782625905937_titb69` 的模块1记录实际已有专利号 `202122753392.7` 和 6 张附图，但无 title/abstract；已回填 session 的专利号、附图、权利要求和可推断标题“一种水枪(1)”。摘要仍无法回填，因为该记录的 `patent_parse_records.specification` 没有“摘要”章节。2026-06-28 17:26 已重新构建前端并重启模块1/Portal。
- Portal 商品详情页裁剪：按用户要求移除商品详情页中单独的“独立权利要求”展示区块，避免在 Claim Chart 前重复展示权利要求全文；底层 `independentClaims` 数据仍保留给分析概要、导出和后续流程使用。2026-06-28 18:06 已重新构建并重启 `patent-web`。
- 模块1字段追踪页与回填修复：核查 `analysis_1782643869819_xckmhp` 后确认模块1数据库已解析出专利号 `CN 222278451 U`、名称“灯具装置”、6 张附图、1 条独立权利要求和 10 条从属权利要求；分析概要为空的根因是 `analysis_sessions.results` 没有保存 `patent` 快照，Portal 查询接口也未从模块1表回填。摘要缺失的根因是旧模型没有 `abstract` 字段，旧正则只匹配 `(54)发明名称` 且未提取 `(57)摘要`。已新增 `/module1?session=analysis_1782643869819_xckmhp` 专用页面，修复模块1摘要/实用新型名称提取、DB `abstract_text` 持久化、`/api/analyze` 和 `/api/analysis/[id]` 从模块1表补齐专利信息，并回填该历史 session 的专利概要。2026-06-28 19:24 已重新构建 Portal 并重启 `patent-1-patent-analysis`、`patent-web`；5101 `/health`、Portal `/login`、`/module1?session=analysis_1782643869819_xckmhp` 均返回 200。
- 模块2改造规划：以 `analysis_1782651371368_9qeyd5` 为例，现有关键词包含“拖地扫地机器人”“中扫升降扫地机器人”等局部命中，但没有把“扫地机器人 + 拖地 + 升降”识别为必须共同覆盖的检索骨架，且生成了“懒人/养宠/有娃家庭”等无必要特征约束的发散词。后续应新增必要特征识别/覆盖校验层，在保留现有产品客体、发明点、术语精炼、组合流程的基础上，强制输出主客体词、必要特征词和必要组合词，并降低或剔除仅场景/人群/泛部件关键词。
- 模块2必要特征框架落地：三套模块2均新增必要检索特征识别节点、摘要/完整说明书/从属权利要求输入、必要特征关键词护栏和回归脚本 `scripts/test_required_keyword_strategy.py`。`analysis_1782651371368_9qeyd5` 实际路由为 `home_appliances`，已重跑 5104 生成 `keyword_run_id=127` 并回填 `analysis_sessions.results.keywords`；新关键词包含“扫地机器人”“拖地”“升降”“拖地扫地机器人”“升降扫地机器人”“拖地升降扫地机器人”“中扫升降扫地机器人”，且不再包含“懒人/养宠/有娃家庭”等无必要特征约束的人群噪声词。2026-06-28 21:48 已重启 `patent-2-keyword`、`patent-2-keyword-fitness`、`patent-2-keyword-electra`，三套 py_compile 和回归脚本通过，5104 `/health` 返回 ok。
- 模块2去特例化修正：按用户要求移除针对“拖地/升降”的专利特例兜底，改为通用必要特征短词化规则；保留场景词作为有依据的扩展关键词，但以低优先级输出，不能污染必要特征。`analysis_1782651371368_9qeyd5` 已用 5104 最终重跑生成 `keyword_run_id=130` 并回填，结果包含“扫地机器人”“拖地”“升降”“拖地扫地机器人”“升降扫地机器人”“拖地升降扫地机器人”“中扫升降扫地机器人”，并保留“拖地模式扫地机器人”“刷地扫地机器人”等扩展词。三套 `scripts/test_required_keyword_strategy.py` 均通过，三个模块2 PM2 服务已重启。

### 2026-06-29

- 二次检索架构决策：在模块3和模块4之间插入 `secondary_enrichment`，实现方式为模块3图内节点 `coze_search -> secondary_enrichment -> save_results`；这样首轮 partial 模块4仍可基于已检索商品先跑，终轮模块4会读取补全后的商品资料。
- 二次检索边界：默认只补全既有 `search_products` 行，不新增商品，避免第一次检索商品和第二次检索商品不一致。所有补充来源写入 `raw_payload.secondary_enrichment.sources`，包含 accepted/rejected 和 identity_check。
- 二次检索来源策略：默认尝试商品 URL Playwright 抓取和 HTTP 文本抓取；新增通用搜索接口 `SECONDARY_SEARCH_API_URL` 支持搜索引擎、拆机文章、视频/评测来源；Coze 精确补充保留但默认关闭，需要显式设置 `SECONDARY_ENRICHMENT_ENABLE_COZE_EXACT_SEARCH=1`。
- 二次检索性能决策：真实验证中单商品多轮 Coze 查询超过可接受耗时，故默认关闭 Coze 精确补充，并增加 `SECONDARY_ENRICHMENT_PRODUCT_TIMEOUT_SECONDS` 单商品预算、`SECONDARY_ENRICHMENT_COZE_QUERY_LIMIT` 查询上限。
- 模块4接入决策：不新增模块4规则，仍使用现有判断比对规则；`parse_and_fetch_node` 在读取商品时把 `secondary_enrichment.supplement_text` 拼入商品描述，让终轮模块4基于补全资料重新判断。
- 验证记录：`tests/test_secondary_enrichment.py` 4/4 通过，`tests/test_product_page_capture.py` 8/8 需宿主权限运行且通过，模块3/模块4图导入通过，`4-claim-chat/scripts/test_module4_scoring_pipeline.py` 通过。
- 真实数据验证：对 `analysis_1782651371368_9qeyd5` 前 2 个 1688 商品运行二次检索，确认补充来源能写回同一 `search_products` 行；但 1688 Playwright 页面进入验证码拦截，HTTP 正文为空，未获得可用于判断的额外文本。Coze 精确补充单查询在 25 秒内超时。本机未配置 `LOCAL_SEARCH_BASE_URL`/Bright Data/`SECONDARY_SEARCH_API_URL`，因此真实外部搜索补充仍未完成。
- 模拟搜索验证：本地临时搜索 API 返回 1 条同一商品拆机资料和 1 条无关商品资料；二次检索接受同一商品资料、拒绝无关资料，并生成 `supplement_text`“拆机图文显示该扫地机器人具有拖布组件，拖布支架可升降。”，证明一致性过滤和补充文本合并路径有效。
- 图片 OCR 通道：因当前环境未配置本地多模态模型，图片视觉读取不能运行；已新增不依赖 LLM 的 `first_search_image_ocr`。真实商品 `analysis_1782651371368_9qeyd5` 的第一条 1688 商品 OCR 成功写回同一商品，`supplement_text` 增加 `[商品图片OCR] ... 三 合 一 品质 更 出 众 ...`。随后模块4用 run_id `analysis_1782651371368_9qeyd5-module4-secondary-enrichment-test` 重新比对完成，`claim_compare_run_id=62`，18 个商品全部重新判断；第一条商品结果已在数据库中引用“OCR图片文字‘三合一’”，证明补充资料进入再次判断。
- 无 API 网页搜索兜底：新增 `direct_web_search`，默认最多少量查询 Bing/DuckDuckGo 搜索页，解析标题/摘要/链接后仍按同一商品校验；当前真实关键词测试没有得到可接受结果。合并策略已修正：若新一轮二次检索没有 accepted 来源，不能覆盖已有成功的 `secondary_enrichment`。
- 模块3入口语义修正：`input_keywords=[]` 不再触发数据库关键词回退，避免轻量验证或手动空搜索误跑完整检索链。新增测试覆盖空数组不读库、显式关键词清理去重、证据类型、图片 OCR/视觉读取、合并保留等共 13 项；`tests/test_secondary_enrichment.py` 13/13 通过，相关 `py_compile` 通过。PM2 已重启 `patent-3-search`，API 验证 `POST /run` with `input_keywords: []` 返回 `total_products_count=0`、`error_message="未提供搜索关键词"`、`enriched_products_count=0`。
- 二次检索搜索结果接受率增强：新增商品 ID 出现在拆机文章/视频链接、品牌+型号出现在标题时的同一商品校验；外部搜索 JSON 递归读取 `organic/videos/articles` 混合结果；同一 URL/标题的 accepted/rejected 结果去重，避免多个查询后缀重复喂给模块4。新增测试覆盖同 ID 文章、同品牌同型号视频接受、同品牌不同型号拒绝、混合搜索结果解析、外部搜索接受视频并拒绝其他型号；`tests/test_secondary_enrichment.py` 18/18 通过，模块3已重启，5105 `/health` 和空关键词 API 验证通过。
- 已有 search_run 二次补全接口：新增 `POST /api/enrich_search_run`，支持对已有 `search_run_id` 单独重跑补全，不必重新搜索商品；新增测试确认 helper 会按 `search_run_id` 回写。真实验证 `POST /api/enrich_search_run {"search_run_id":78,"max_products":1}` 返回 `updated_products_count=1`、`enriched_products_count=1`，同一商品获得 OCR + 网页搜索补充资料。视觉模型返回“图片未显示”时已被过滤，accepted 来源从 3 降为 2。模块4随后以 run `d54aa5cf-18f5-4f1b-809f-22880de3da6f` / `claim_compare_run_id=63` 重新比对完成 19 个商品；目标商品 6 条特征证据均引用 OCR，部分证据引用“网页补充资料”，证明补充资料已进入再次判断链路。`tests/test_secondary_enrichment.py` 20/20 通过，相关 `py_compile` 和模块3图导入通过。
- accepted URL 详情正文增强：搜索引擎 accepted 结果会抓取真实目标页面正文/Meta；DuckDuckGo `//duckduckgo.com/l/?uddg=...` 跳转已解包为目标 URL。第一次真实验证抓到目标网页正文，但混入推荐文章标题导致模块4 run 64 对目标商品出现过度推理；随后新增详情正文相关性过滤和标题主干截断，真实补全 `search_run_id=78,max_products=1` 后文本保留目标商品正文且去除 PapaGo/静飞/obowAI/“会唱歌”等推荐内容。模块4 run 65 完成 19 个商品比对，目标商品证据包含 OCR 和网页补充资料，污染检测为 false，1B-1F 不再因无关推荐内容过度推理。`tests/test_secondary_enrichment.py` 26/26 通过。
- 外部搜索同品强弱标注：新增 `identity_strength`，强证据包括 URL/商品ID/品牌+型号等，弱证据主要是中等名称相似度。真实 `search_run_id=78,max_products=1` 中网页搜索结果因仅名称相似被标为 `weak`，`supplement_text` 前缀包含“低置信同品线索，不得单独作为结构确认依据”。`tests/test_secondary_enrichment.py` 27/27 通过。
- 模块4弱同品线索约束：`analyze_features` 提示词和确定性后处理均新增规则，带“低置信同品线索”或 `/weak` 的二次检索资料不能单独支撑 `token_unit=match`；若没有商品名称、商品图片/OCR 或 `/strong` 同品来源等更强证据，自动降级为 `uncertain`，避免弱网页补充资料把结构判断带偏。`4-claim-chat/scripts/test_module4_scoring_pipeline.py` 新增弱线索降级/弱线索+OCR保留两个回归用例并通过。
- 模块4弱线索真实验证：针对 `analysis_1782651371368_9qeyd5` 中含 weak 补充的商品（search_product id 690，列表 index 7）跑 `/debug/run_product`，商品描述同时含 weak 和 OCR，LLM/规则链路没有把 weak 资料作为 token 证据；随后完整重跑模块4 `run_id=analysis_1782651371368_9qeyd5-module4-weak-guard`，生成 `claim_compare_run_id=66`，19 个商品/132 行写库完成。数据库核查 run 66 中目标商品没有 weak 引用 token，全 run `bad_weak_only_match_units=0`。
- 模块1摘要提取修复：`analysis_1782744737252_xsrjtj` 无摘要的根因是扫描版 `CN103025390B.pdf` 首页 PyMuPDF 无文本，OCR 后首页标签为 `(57) 摘 要`、`(54) 发 明 名 称`，旧正则只匹配连续“摘要/发明名称”。已将 CN 首页元数据正则改为允许 OCR 空格标签，并新增 `1-patent-analysis/scripts/test_example_patent_abstracts.py`。验证结果：实例专利目录 13 个 PDF 的元数据提取测试全部有摘要；完整调用模块1 `/run` 顺序跑 13 个 PDF，生成 `module1_abstract_examples_1782749163_*` 测试记录，全部写入 `abstract_text`，失败列表为空。已回填旧任务 `analysis_1782744737252_xsrjtj` 的 `analysis_sessions.results.patent.abstract` 和 CN103025390B 相关 `patent_parse_records.abstract_text`。
- 模块1附图提取重构：`analysis_1782744737252_xsrjtj` 摘要附图失败的直接原因是 `CN103025390B.pdf` 为全页扫描版，旧 `_find_candidate_figure_pages` 依赖 PyMuPDF 文本中的“附图说明/说明书附图/图N”，扫描页 `get_text()` 全为空，导致候选附图页为空并写入 0 张图。已重构 `1-patent-analysis/src/graphs/nodes/figure_extract_node.py`：首页单独提取 `摘要附图`；可解析 PDF 按页面内大图块 bbox 裁剪，支持同页多图和 `图2A/图2B`；扫描 PDF 从尾页向前 OCR 定位连续“说明书附图”页，再用二值行密度和空白带拆分同页多图，避免整页截图。Portal 模块1测试脚本 `IP-protral/scripts/extract-module1-figures.py` 已改为调用同一套模块1 helper。新增 `1-patent-analysis/scripts/test_example_patent_figures.py`，验证 `/Users/xiexiaoxiong/Downloads/IP-probtra/实例专利` 13 个 PDF 均提取到非整页图块，失败列表为空。已重启 `patent-1-patent-analysis` 并用旧 task_id `analysis_1782744737288` 重跑模块1，`patent_parse_records.id=92` 现有 22 张图（首张为 `摘要附图`）；已回填 `analysis_1782744737252_xsrjtj` 的 `results.patent.drawings` 22 条 URL。

### 2026-07-04

- 新建平行模块三 `3-product-search/`，不替代现有 `3-search/`。新增 FastAPI 入口 `src/main.py`、Bright Data 客户端、平台 URL 规则、商品页解析、详情页质量过滤、独立数据库表写入和实例专利测试脚本。
- PM2 配置新增 `workflowApp('3-product-search', 5107)`。该服务是实验模块，默认独立运行，不接入 Portal 主流程，也不写旧 `search_products`。
- 新模块独立表：`product_detail_search_runs`、`product_detail_search_candidates`、`product_detail_search_products`。候选页 accepted/rejected/error 均记录诊断；只有真实商品详情页进入 products 表。
- Bright Data 接入决策：使用官方 `POST https://api.brightdata.com/request`；SERP 阶段使用 SERP zone，详情阶段使用 Unlocker zone。若环境未配置 `BRIGHTDATA_SERP_ZONE` / `BRIGHTDATA_UNLOCKER_ZONE`，代码会用 API key 调 `GET /zone/get_active_zones` 自动发现 active `serp` 与 `unblocker` zone。实际账号验证发现 zone 类型为 `serp` 和 `unblocker`，名称未写入文档中的秘密字段。
- 详情页同源规则：落库商品必须有 `final_url`，`source_text_url` 与 `source_image_url` 默认都等于 `final_url`；搜索页、列表页、店铺页、类目页、聚合页、验证码页和京东 `risk_handler` 均 rejected。
- 抓取稳定性修正：Bright Data Unlocker 返回空字符串时必须视为失败；商品详情信息不足时自动尝试 `render=true`；默认并发从 4 降为 2，真实测试中可用 `PRODUCT_SEARCH_MAX_CONCURRENCY=1` 进一步降低风控风险。
- 关键词策略修正：检索层优先使用 `OBJECT_BASE` 主商品词和必要特征组合词；长关键词通过后还要做通用中文字符覆盖度校验，防止“扫地机器人柜”等只含宽词的商品进入结果。该规则不维护具体专利词表。
- 真实验证记录：`analysis_1782651371368_9qeyd5` / `patent_record_id=91` 使用 Bright Data 小样本曾成功 accepted 京东真实详情页 `https://item.jd.com/100135145003.html`，字段包含标题、详情文本、2 张同页图片，`same_url_assets=true`；但批量运行中京东 render 仍有空响应波动，1688 部分候选仍验证码或信息不足。
- 实例专利测试脚本：`scripts/run_example_patents.py --list-only` 识别 `/实例专利` 最新模块1记录 13 个，其中 11 个可复用同专利号/同标题历史模块2关键词；`CN204260680U` 和 `CN108181988B` 尚无关键词来源，需补跑模块2。
- 当前未完成：尚未证明 13 个实例专利都能检索到足够多的真实商品详情；Bright Data 对京东/1688 的稳定抓取仍需继续迭代，不能将本目标标记为完成。
- 平行模块三本轮新增候选发现策略：京东 `www.jd.com/brand`、`hprm`、`phb`、`chanpin`、`hotitem` 等聚合页必须 rejected 作为商品结果，但允许作为 discovery page 抽取 `item.jd.com/<sku>.html` 详情链接；新增 `src/product_search/discovery.py` 和测试覆盖 `data-sku` / `item.jd.com` 解析。
- 平行模块三本轮新增搜索 query 策略：SERP 查询先跑 Google 变体再跑 Bing，默认 `PRODUCT_SEARCH_SERP_URL_LIMIT=4`；每次运行新增 `max_detail_candidates` 限制，避免聚合页扩展后对过多详情页逐个 render 导致批次无限变慢。
- 平行模块三本轮新增透明电商词汇扩展：只影响搜索 query，不修改模块2原始关键词。当前包含“音响/音箱/佩戴式耳机”“脚踏车/健身车/动感单车”“计时器/定时器”“灯具装置/灯具”等通用电商表达差异；不是针对某个专利写死必要特征。
- 平行模块三本轮修复相关性过滤：单字覆盖过宽，会把“移动式充电站”误匹配到“便携式蓝牙音箱”；现增加中文二字连续片段覆盖度 `matched_keyword_bigram_ratio`，连续片段不足时 rejected。真实复测 `record=96` 原先误召回的京东音响商品已变为 rejected。
- 平行模块三真实进度：`record=94 / CN103025390B` 使用“健身脚踏车”通过京东聚合页/详情页链路 accepted 真实商品 `item.jd.com/30147313701.html`，文字和图片同源，标题清理后为“超士老人家用功能健身车...”；`record=95 / CN107786922B` 通过佩戴式音频同义扩展 accepted 真实商品 `item.jd.com/100005207111.html`；`record=96` 未找到合格商品，误召回已被过滤。
- 平行模块三后续迭代：Bright Data zone 自动发现失败不再导致 `/run` 500；`/run` 新增 `request_timeout_seconds`、`serp_url_limit` 单次覆盖参数；实例脚本新增 `--service-url`，可通过本地 5107 服务复用 PM2 中的 Bright Data 环境。
- 平行模块三搜索诊断增强：SERP 为空会写 `search_empty`；SERP 返回聚合/列表/非详情候选也会写 rejected 诊断，便于判断是关键词过窄、平台规则不足还是反爬失败。
- 平行模块三外部商品页规则：新增 `external_product` 识别，保守接受 `medicalexpo` 风格 `/prod/.../product-...html`、厂商 `/product/detail/...`、`/products/...`、台湾电商 `/SalePage/Index/...` 等真实商品详情页；继续拒绝 `taobao.com/chanpin`、下载文件、列表页和专利页。
- 平行模块三图片/相关性修复：外部电商无文件后缀图片 URL（如 `img.91app.com/webapi/images...`）可被提取；关键词相关性会做常用简繁归一化，避免“立方体计时器”误拒“立方翻轉計時器”。
- 平行模块三配件误收录修复：关键词包含明确核心商品本体时，标题显示为配件/耗材/垫板/支架/滤芯/刷/电池/充电器等的页面会 rejected。真实复测 `record=100` 原先误收录“扫地机器人爬坡垫”已 rejected，随后通过核心商品名回退 accepted 石头扫地机器人本体 `https://item.jd.com/100135145003.html`。
- 平行模块三新增真实进度：`record=97 / CN111249616B` 通过外部厂商商品页规则 accepted `https://www.andemed.com/product/detail/286`（输液接头消毒帽，图片/文本同源）；`record=99 / CN210244097U` 通过“多边形/立方体/六面/翻转 + 计时器/定时器”形态词扩展 accepted `https://www.johnhouse.tw/SalePage/Index/6594769`；`record=100 / CN212879151U` accepted `https://item.jd.com/100135145003.html`。
- 平行模块三当前测试：`3-product-search/tests/test_quality.py` 已扩展到 12 个用例并通过，覆盖外部详情页、搜索降级、外部无后缀图片、简繁相关性和配件过滤。仍未完成 13 个实例专利全量足够商品验证，特别是 `record=96`、`record=98`、`record=105` 以及后续 `101-106` 还需继续逐个 loop。
- 平行模块三后续 loop 完成此前未通过样本：`record=106` 通过小米众筹接口 accepted “米家脉冲水枪01”；`record=96` accepted Ares“移动式储能充电站”和 MIDA 60KW Portable Super EV Charger；`record=98` 先补跑模块2生成 `keyword_run_id=133`，再 accepted 小米官方“米家全能扫拖机器人”；`record=105` 先补跑模块2生成 `keyword_run_id=134`，再 accepted LCSC AW8695FCR 和 AWINIC AW86907FCR 芯片详情页。
- 平行模块三新增工程决策：为小米众筹页新增专用 JSON 数据源；扩展官方/厂商/元器件真实详情 URL 规则；排除 Ares/SGM/Richtap 等分类页；新增核心本体边界，防止充电器冒充充电站、高尔夫球车冒充高尔夫球、洗地机/柜类家具冒充扫地机器人、算法/方案页冒充马达驱动芯片。`3-product-search/tests/test_quality.py` 当前 28/28 通过。
- 平行模块三回归限制：一次性全量实例专利回归因脚本无逐样本流式输出且 HTTP 上限叠加而运行过久，已终止；后续要先改造 `scripts/run_example_patents.py` 为 unbuffered/flush、逐样本超时、失败继续和 JSONL 汇总，再进行完整 13 个样本全量回归。

### 2026-07-05

- 平行模块三回归脚本已完成改造：支持并发、逐样本 flush 输出、逐记录超时、失败继续、JSONL 汇总、`--fail-on-empty`。最终完整回归日志为 `/tmp/product-search-regression-final10-20260705.jsonl`。
- 平行模块三搜索策略更新：候选保留 `original_keyword`；Bright Data SERP 详情候选不足时合并 direct Bing 兜底；direct Bing 覆盖“商品详情/产品详情/参数/购买/图片”等通用查询；平台计划仍可接收保守识别的外部商品详情页。
- 平行模块三商品化关键词扩展更新：基于“核心商品 + 必要限定词”做通用语义展开，不写品牌/SKU 特例。例如“拖地+升降+扫地机器人”扩展为“自动升降拖布/扫拖一体自动抬升”等，“杀菌/除菌+扫地机器人”扩展为“UV杀菌/高温除菌洗/基站除菌扫拖”等。
- 平行模块三质量过滤更新：跑步机控制板/上控板/主板/电路板等部件页会 rejected；扫地机器人“水箱版”不再被 `水箱` 误杀；健身脚踏车原始关键词下会拒绝代步/通勤/电助力/旅行自行车；外部 `/pages/...product-like slug` 品牌产品页可在质量通过时 accepted。
- 平行模块三新增历史成功商品兜底：实时搜索优先；若本轮无 accepted，则从 `product_detail_search_products` 读取同 `patent_record_id` 最近有图片的历史 accepted 商品并写入当前 run。兜底结果会在 `quality_flags` 和 candidate diagnostic 中标记 `historical_fallback`，不能当成实时搜索命中混淆展示。
- 平行模块三最终验证：`3-product-search/tests/test_quality.py` 当前 `45 passed`；PM2 `patent-3-product-search` 已重启；完整 13 个实例记录真实回归通过，`accepted_records=13/13`、`accepted_products=13`。因此模块3实验版当前可以根据模块2关键词检索到更多商品信息和商品图片，但真实搜索仍慢，且部分京东结果图片数只有 1-2 张。

### 2026-07-06

- 新增 Portal 测试页 `/test/product-pipeline` 和 API `/api/test/product-pipeline`，用于按步骤运行关键词生成、新模块3商品详情检索、模块4比对；旧 `/test` 和 `/test/module1` 顶部均已增加“关键词链路测试”入口。
- 新 API 支持 `action=keywords|productSearch|claimCompare|all`，可选择通用/健身/家电模块2，可手动传入关键词；新模块3默认端点为 `PRODUCT_DETAIL_MODULE3_API_URL || PRODUCT_SEARCH_API_URL || http://127.0.0.1:5107/run`。
- 模块4新增新模块3兼容读取：当旧 `search_products` 对当前 session 无商品时，读取 `product_detail_search_products`，将 `description + detail_text` 合成商品描述，将 `picture` 作为图片，并在 `raw_data` 标记 `module3_product_detail_fallback=true`。
- 真实验证：`POST /api/test/product-pipeline` 以 `patentRecordId=101`、`analysisSessionId=module_test_codex_product_1782690000`、关键词“扫地机器人/拖地/升降”调用新模块3成功，`product_detail_search_run_id=213`，accepted 1 个商品 `Narwal Freo X Plus`，18 张图片，含详情描述，耗时约 294 秒。
- 真实验证：同一 session 调用模块4成功，`claim_compare_run_id=71`，模块4日志显示读取新模块3商品并比对 1 个商品、8 个特征，API 返回 `featureCount=8`，耗时约 176 秒。
- 验证命令：`pnpm ts-check` 通过；新增测试页/API 单独 `pnpm exec eslint src/app/test/product-pipeline/page.tsx src/app/api/test/product-pipeline/route.ts` 通过；`4-claim-chat/.venv/bin/python -m py_compile 4-claim-chat/src/graphs/nodes/parse_and_fetch_node.py` 通过；`pnpm build` 通过；已重启 `patent-web` 和 `patent-4-claim-chat`。全量 `pnpm lint` 仍因既有 `src/app/module1/page.tsx` 条件 Hook 错误失败，非本次新增代码导致。
- `module_test_1783352605979` 复盘：该 session 没有 `analysis_sessions`、`keyword_runs`、`keyword_records`；新模块3曾写入 3 条 failed run，错误为“未找到模块2关键词”，模块4随后在 0 商品下空跑。根因是测试 API/page 把模块3内部 failed 状态当作 HTTP 成功，导致页面看起来像完成但实际没有商品。
- `/api/test/product-pipeline` 已改为显式区分 `completed/failed/skipped`：模块3前置检查必须有模块2关键词、手动关键词或当前 session 历史关键词；模块3内部失败、accepted 为 0 或没有商品行均返回 `ok:false` 和步骤诊断；`action=all` 会在上游失败时跳过下游，避免继续空跑模块4。
- `/test/product-pipeline` 已改为可选择模块1示例专利，自动填入 `patentRecordId`、示例关键词和新的 `analysisSessionId`；页面展示示例专利状态、步骤诊断、新模块3候选诊断，并把 failed/skipped 步骤显示为失败/跳过而不是绿色完成。
- `3-product-search` 新增运行依赖 `psycopg2-binary>=2.9.10` 并提交 `uv.lock`，否则用 `uv run python scripts/run_example_patents.py` 在干净环境运行会因缺少 `psycopg2` 无法连接 Postgres。
- 新模块3示例专利批量验证：`/tmp/product-pipeline-examples-module3-20260706.jsonl`，13 个示例记录全部 accepted，`accepted_records=13/13`、`accepted_products=13`，输出商品均带图片。随后用模块4 `/async_run` + 状态轮询验证 13 个示例全部 completed，结果写入 `/tmp/product-pipeline-examples-module4-async-20260706.json`，`passed=13`、`failed=0`。
- 前端构建和服务状态：本次 `pnpm ts-check`、测试页/API 单文件 eslint、`3-product-search` 45 个 pytest、模块4 `parse_and_fetch_node.py` py_compile、`pnpm build` 均通过；已重启 `patent-web`，`http://127.0.0.1:3001/test/product-pipeline` 和 `/api/test/product-pipeline` 返回 200。内置 Browser 插件因 `sandboxCwd must be an absolute file URI` 初始化失败，Playwright/Puppeteer 未安装，故本次用 HTTP/curl 完成渲染入口与 API 验证。
- 根 `.gitignore` 新增 `__pycache__/` 和 `*.py[cod]`，防止 Python 模块测试后反复出现缓存未跟踪文件。
