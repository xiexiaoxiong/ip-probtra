# 专利分析比对软件总索引

> [!IMPORTANT]
> **强制预读：**Codex 或其他编程工具每个新的工作回合或独立任务，在首次读取、搜索、修改项目文件，查看 Git 状态、日志，运行测试或启动服务之前，必须先完整阅读根目录 [`PROJECT_CHARTER.md`](./PROJECT_CHARTER.md)。如果同一回合修改了该文件，修改后必须重新完整阅读，再继续其他项目操作。请求与宪章冲突时，应先说明冲突并取得用户明确决定。
>
> 这是 `../Documents/patent` 项目的根级协作索引。完成上述强制预读后，再读取本文件，并按需读取各模块自己的 `AGENTS.md`、`README.md` 和代码。
>
> 规则：每次会话结束时，必须把本次作出的架构决策、接口决策、限制、进度变化、待办变化更新到本文档的「会话决策日志」和相关章节。

## 项目定位

本项目是一个本地化运行的专利侵权/相似度分析系统。主链路从专利文件或文本开始，解析专利，生成商品检索关键词，检索市场商品，再对独立权利要求和商品信息做技术特征比对，最后在 Next.js Portal 中展示分数、证据、Claim Chart 和报告。

项目已确认并开始实现“专利稳定性与无效检索”产品方向：与现有侵权系统共用前端入口和中立专利解析事实层，适用中国专利无效规则，检索覆盖全球专利及论文、标准、产品手册、datasheet、论坛等可证明公开时间的非专利现有技术。当前已建立隔离测试服务、正式晋级骨架、Portal 接入和回归脚本，仍处于实现、审计和全量实例回归阶段；目标、范围、模块边界、日期规则、依赖限制和完成门槛以根目录 `PROJECT_CHARTER.md` 为准，详细状态机以 `docs/invalidity/WORKFLOW_SPEC.md` 为准。

核心原则：

- LLM 不直接输出法律侵权结论，只做文本读取、结构化、事实对齐、证据标注。
- 业务判断必须可回溯到专利原文、商品描述、商品图片和确定性规则。
- 无效检索当前自动范围只处理独立权利要求；模块一仍冻结全部权利要求原文，但从属权利要求不得创建新调查、进入检索/分析、影响案件状态或出现在普通报告结果中。历史从属调查行仅保留在技术审计数据中。
- 原侵权主链路单向依赖：模块1 -> 模块2 -> 模块3 -> 模块4 -> Portal 结果提取，不允许反向调用。无效检索不复用该循环限制；它由独立 `I0` 根据持久化结果逐权利要求推进轮次，但各逻辑模块仍不得进程内递归调用下一轮。
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
| `3-andun-search/` | 第三种模块3测试版：调用安盾开放平台异步监测任务，记录任务状态、商品列表和每次 API 耗时，独立表落库 |
| `4-claim-chat/` | 模块4，权利要求-商品比对：拆独立权利要求特征，逐商品分析证据，规则计分，写 Postgres |
| `5-invalidity-search-test/` | 无效检索唯一后端（历史目录名）：5209、`invalidity_test` schema、中立解析 5201、Agent 自动流程、模块实验室和实例回归 |
| `5-invalidity-search-prod/` | 历史正式版晋级目录：5109/`invalidity_prod` 仅保留迁移审计，不接受新的 Portal/Agent 请求 |
| `IP-protral/` | Next.js Portal：上传、认证、编排、进度、结果页、详情页、报告导出 |
| `docs/invalidity/WORKFLOW_SPEC.md` | 无效检索详细状态机、双检索线、日期通道、D1/gap、报告和验收规范 |
| `ops/pm2/` | 无效测试/正式独立 PM2_HOME、端口和 release 启停脚本 |
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
- `3-andun-search` -> `127.0.0.1:5108`（安盾开放平台测试模块，不替代 5105/5107）
- `4-claim-chat` -> `127.0.0.1:5106`
- `IP-protral` -> 默认 `3001` 或 `.env.local` 的 `PORT`

推荐只维护一份环境文件：`IP-protral/.env.local`。`ecosystem.config.cjs` 会把数据库、LLM、搜索、对象存储、飞书变量注入所有模块。

无效检索不使用默认 `~/.pm2`。当前唯一业务后端沿用 `ops/pm2/.state/test` 管理 5201/5209；
Agent 与模块实验室都调用该实例。`ops/pm2/.state/prod`/5109 仅为历史迁移审计保留，不得接收
新的用户请求。业务隔离按用户、session、investigation 和资源 owner 实施，而不是按前端页面实施。

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
| `LOCAL_LLM_DEFAULT_MODEL` | 默认模型；当前 PM2 默认固定为 `glm-4.6v` |
| `LOCAL_LLM_FAST_MODEL` | 快速模型；当前 PM2 默认固定为 `glm-4.6v` |
| `LOCAL_LLM_VISION_MODEL` | 多模态模型；当前 PM2 默认固定为 `glm-4.6v` |
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
- 安盾模块3 `3-andun-search` 写入 `andun_search_runs`、`andun_search_products`、`andun_api_call_logs`，只在 `/test/andun-search` 测试，不接正式分析和模块4。
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
- 关键词组合护栏已改为强制生成：主客体基础词、必要特征+主客体、多必要特征+主客体；禁止把必要特征单独作为可执行检索词。只有独立权利要求直接限定的特征可进入 `required_features`，从属权利要求、摘要和说明书效果词只能作为扩展特征。最终关键词必须含已识别的主商品客体，且写入 `query_role`、`guard_status`、`object_terms`、`feature_source_tiers` 供 Portal 和模块3再次校验。

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
- `src/app/test/product-pipeline/page.tsx`：专利分析四阶段实验室；无 `session` 时可逐阶段测试，带 `?session=<analysis_session_id>` 时仅为 approved admin 恢复 Agent 同一侵权任务的只读诊断视图。
- `src/app/api/admin/patent-analysis/session/[id]/route.ts`：侵权 Agent 同任务只读诊断 API；只按当前 session 保存的 record/run ID 与精确 `analysis_session_id` 读取四阶段输入、输出、部分结果和错误，不运行或改写任务。
- `src/app/test/module-lab/page.tsx`：无效检索律师版模块实验室。自 2026-08-09 起固定为十一个可分别测试的律师工作阶段，每阶段只保留“当前真实案件/内置示例”两种输入：①读取目标专利；②确定关键日；③总结核心发明点；④生成首轮检索词；⑤首轮检索与证据核验；⑥单篇新颖性比对（`I4_S_SINGLE_REFERENCE`）；⑦选择 D1 与冻结区别特征（`I4_C_CLOSEST_PRIOR_ART`）；⑧显而易见性预分析（`I4_O_OBVIOUSNESS_PRECHECK`）；⑨进一步检索与补证循环（`I2_GAP_QUERY_PLAN`，可重复测试一至五轮）；⑩创造性组合分析（`I4_I_INVENTIVE_STEP`）；⑪大 Claim Chart 与律师报告（`I5_REPORT`）。阶段六至十一只消费同案已持久化的独立权利要求和前序事实，禁止客户端伪造输入绕过后端守门；技术运行记录继续折叠供 Codex 排错。
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

基于 2026-07-27 代码阅读和本轮验证：

- 账户管理已基本落地：存在 `auth` API、登录/注册/管理员页面、`users`/`auth_sessions` schema、middleware。
- Portal 主存储已明显转向 Postgres：`analysis_sessions` 带 `user_id`，分析历史页存在。
- 模块1已支持 PDF 文本提取、CN 专利元数据正则兜底、附图提取和 Postgres 保存。
- 模块2通用/健身/家电三套代码存在，主输入已是 `patent_record_id` + `analysis_session_id`。
- 模块3主流程可写 Postgres；Playwright 商品页抓取原型保留为独立实验/人工取证工具，不接入默认主流程。
- 模块4评分化和 token 高亮链路已补脚本验证：token mismatch 会传导到特征、claim、商品归零；无 mismatch 时商品分按“特征得分 * 特征占比”加权求和，且重叠 token 命中长度会封顶到特征有效长度。
- 异步检索 + partial + 终轮补跑已有编排字段，并新增脚本测试覆盖首轮 partial 与终轮 final 结果字段。
- Portal 结果列表、详情页和分数表已共用结果一致性 helper，并新增脚本测试覆盖排序、详情查找和导出前数据摘要口径。
- 行业路由与关键词确认流程已抽出纯函数并补脚本测试。
- 无效检索测试版的专利链路当前固定为智慧芽 P002 候选发现、EPO OPS 同号全文优先取回；
  EPO 明确无该文献/全文/PDF 覆盖时，才按同一 `patent_id + pn` 回退已开通并通过 live smoke
  的智慧芽 P020。任何身份冲突、鉴权、网络、限流或服务错误都 fail closed，不触发回退。

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
- 当时决定进入 `../Documents/patent` 后先读根 `AGENTS.md`、再读子模块 `AGENTS.md`；该顺序已于 2026-07-19 被宪章 0.3 更新为：先读 `PROJECT_CHARTER.md`，再读根 `AGENTS.md`，最后按需读取模块级说明。
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

### 2026-07-12

- 修复新模块3图片上下文误过滤：class/id/style 改为完整 token 及连字符组件匹配，`border` 不再因包含 `order` 被误判，`main-logo` 仍能识别为 Logo。
- 商品 URL 规范化改为仅移除已知追踪参数并保留其他查询参数，避免 `ProductDetail.aspx?uid=123` 与 `uid=456` 被合并；Portal 测试诊断采用同一语义。
- 历史商品刷新默认最多 8 条、最多 4 并发，环境上限分别为 32/8；实时返回 HTTP 200 的历史页必须重新通过 `evaluate_product_detail` 才可接受，验证页、空壳页和无图片页不得沿用旧质量分。
- 模块4 LLM 故障兜底不再按局部词命中生成 `match`；所有判断单元保持 `uncertain`，并写入 `analysis_failed=true`、`analysis_source=rule_fallback` 与“大模型调用失败”原因。HTTP 429、速率限制或 cooling-down 会立即停止当前批次重试。
- Portal 商品实验链路将“规则兜底”计入 `llmErrorCount`，存在任一模型失败兜底行时步骤返回 failed，避免误显示模块4完成。
- 安全与仓库清理：删除含凭据的 `.env.local.bak-20260708231055`，删除 `.trae/.DS_Store`、`3-search/tmp-retest-after-restart.py`、`IP-protral/tmp-check-5051.ts`，并忽略环境备份、DS_Store 和 `tmp-*` Python/TypeScript 文件。
- 验证：`3-product-search/tests/test_quality.py` 59/59 通过；模块4评分脚本覆盖局部词误命中及 429 单次短路并通过；Portal `pnpm ts-check`、改单文件 eslint、生产构建通过。已重启 5107、5106、3001，健康检查及 `/test/product-pipeline` 返回 200。
- 新模块3商品图片完整性改造：新增本机 Chrome 稳定渲染抓取，持续滚动并以页面高度+图片 URL 集合连续稳定作为终止条件；移除 18 图上限，增加 JD/淘宝/天猫/苏宁图库属性及脚本解析、尺寸变体去重。渲染进入登录/验证页时不得覆盖有效静态详情。
- 搜索与历史正确性：无 Bright Data 时新增苏宁公开搜索页直搜；历史回退必须重新匹配本次关键词，修复任意输入关键词错误复用同专利旧商品的问题。
- 图片真实验证：`扫地机器人/蓝牙耳机/跑步机/电饭煲/机械键盘` 5 个关键词全部实时命中真实苏宁详情页且非历史回退；最终版本逐页主图库合计 33/33 全部抓取、缺失 0，落库图片共 46 张。验证脚本为 `3-product-search/scripts/verify_image_capture.py`，run id 为 346/347/348/349/350。
- 回归状态：新模块3普通测试 `64 passed, 1 skipped`，宿主权限下真实 Chrome 懒加载稳定性用例单独 `1 passed`；PM2 `patent-3-product-search` 已重启并通过 5107 健康检查。

### 2026-07-14

- `module_test_1783863097738` 的模块2页面请求在约 301 秒显示 `fetch failed`，但模块2日志确认工作流从 08:54:01 运行到 08:59:09 后成功；数据库已写入 `keyword_run_id=139` 和 9 条关键词。直接原因是 Node 内置 fetch/Undici 的独立 300 秒响应头超时先于业务层 20 分钟 `AbortSignal` 触发，并非模块2没有结果。
- Portal 新增 `src/lib/long-running-http.ts`，使用 Node HTTP/HTTPS 客户端执行长耗时 JSON POST，以调用方设置的 15/20/30 分钟业务超时作为总截止时间；测试链路 `callModule` 和正式主链路 `callModuleApi` 均已切换，避免 300 秒边界导致成功结果被前端误报失败。
- 电脑重启后 PM2 进程表为空，已用根 `ecosystem.config.cjs` 重新拉起 8 个服务；Portal 3001 与工作流 5101-5107 健康检查均返回 200。
- 生产构建补充修复：原 `.babelrc` 在所有环境强制 React Babel `development=true` 并加载 Inspector 插件，Webpack 生产预渲染会报 `jsxDEV is not a function`；已改为 `babel.config.js` 按 Babel env 仅在 development 启用 jsxDEV 与 Inspector，production 使用正常 JSX runtime。当前机器的 Turbopack 构建会卡在 compile，`scripts/build.sh` 已固定使用 Next 官方 `--webpack` 构建模式。

### 2026-07-15

- `analysis_1784098219047_zm2wi5` 的误检根因是旧模块2把“中空孔”等单一必要特征直接生成为可执行关键词，并把摘要/说明书中的效果表述“闭式切换”提升为必要特征；Portal 随后无条件确认，模块3实际检索到中空塑料板等跨品类商品。
- 三套模块2统一增加来源分层：只有独立权利要求直接限定的特征保留为必要特征；从属权利要求及摘要/说明书效果词降为扩展特征。可执行关键词必须含主商品客体，裸特征词在模块2组合护栏和 Portal 最终确认处双重拒绝。
- Portal 读取关键词时严格限定 `patent_record_id + analysis_session_id + keyword_run_id`，避免同一专利不同 session/run 的关键词串用；同时把 `object_terms` 传给正式模块3。
- 正式模块3新增商品客体一致性过滤，商品标题/描述不含主商品客体或其类目后缀时在落库前拒绝，避免“中空孔”召回 PP 中空板后继续进入模块4。
- PM2 默认模型统一为 `glm-4.6v`，模块4三个 LLM 配置显式指定 `glm-4.6v`。含图片的请求必须保留文本和 `image_url` 内容块，禁止降级为纯文本；默认关闭其他提供商/纯文本 fallback，模型不可用时明确失败。

### 2026-07-16

- 从 `/Users/xiexiaoxiong/Downloads/安盾开放平台接口文档.md` 确认安盾 API 是异步监测任务：`monitor.keywords.task.add` 创建任务，`monitor.keywords.task.info` 返回 0/10/20 阶段，完成后用 `monitor.keywords.task.goods.list` 分页取商品。
- 新增独立 `3-andun-search/`，PM2 端口 5108。签名严格对实际 JSON body 做 SHA-256，并对排序后的 `app_key/body/method/nonce/timestamp` 首尾拼接 AppSecret 后计算大写 MD5；文档两个签名向量测试通过。
- 新模块独立保存 run、商品和 API 调用耗时，不写 `search_products` 或 `product_detail_search_products`。安盾当前明确返回首图、标题、价格、月销量、商品/店铺链接，不包含详情正文和详情图片集，因此未接模块4。
- 新增 Portal `/test/andun-search` 和 `/api/test/andun-search`，支持关键词、平台、轮询间隔、最大等待、分页上限测试，展示远端 taskId、阶段、提交/总耗时、逐接口耗时和商品字段。
- QA 真实连通测试：文档示例 AppKey 请求 HTTP 200，业务返回 `code=1001 / appKey已过期`；curl 首次约 4.55 秒，新客户端探测约 0.99 秒。说明入口和签名链路可达，但需要有效 `ANDUN_APP_KEY` / `ANDUN_APP_SECRET` 才能验证成功商品数据与完整任务耗时。

### 2026-07-17

- 有效安盾 QA 凭据已通过环境变量加载且未写入代码或日志。最小真实任务 `andun_search_run_id=2` / `taskId=2078034311787646977` 创建成功：`monitor.keywords.task.add` 返回 `code=0`，耗时约 3.97 秒；首次 `task.info` 耗时约 0.50 秒。
- 初版在任务运行约 11 分 04 秒时因单次 `RemoteProtocolError` 将整项运行误判为失败。现已仅对幂等只读接口 `task.info` / `goods.list` 增加最多 4 次瞬时错误重试，每次重建连接并重新生成 nonce/timestamp/sign；非幂等 `task.add` 不自动重试，避免响应丢失时重复创建远端任务。
- 新增 `POST /runs/{run_id}/resume`，可按原 taskId 续跑 error/timeout 任务；服务启动时自动恢复数据库中已有 taskId 的 submitting/collecting/organizing 任务。真实 PM2 重启验证 `active_local_tasks=1`，run 2 与原 taskId 连续，没有重复创建。
- Portal `/test/andun-search` 新增已有 run 读取、同 taskId 续跑、墙钟已运行时间、调用尝试次数和重试原因展示。前端仍只通过 5108 独立服务访问安盾，不调用正式模块3端口。
- 安盾 QA 实测边界：run 2 超过 30 分钟仍为 `status=0`，状态查询持续 `HTTP 200 / code=0`；同一 taskId 的只读商品列表返回 `total=0 / records=[]`。文档示例 taskId 在当前账号下返回 `code=7777 / 该任务不存在`。因此目前没有可验证的真实商品返回，问题位于安盾 QA 任务处理/队列侧，模块仍不得接入正式模块4。
- 验证：`3-andun-search` 7 个 pytest 通过，Python `py_compile` 通过；Portal 单文件 eslint、`pnpm ts-check`、两次生产构建通过；`node --check ecosystem.config.cjs`、`git diff --check` 通过；5108 健康检查、3001 测试页和测试代理均为 200。

### 2026-07-19

- 产品方向确认：新增面向中国专利无效/稳定性评估的模块化系统，与现有侵权系统共用前端上传入口和中立专利解析层，但后续会话、运行、数据表、端口和结果保持隔离。
- 检索范围确认：不仅覆盖全球专利文献，还覆盖论文、标准、产品手册、datasheet、论坛、网页、软件资料等非专利现有技术；无法证明公开时间或内容稳定性的材料只能作为调查线索。
- 法律分析边界确认：新颖性与创造性均纳入 MVP；逐项权利要求判断有效优先权日/申请日，普通现有技术、抵触申请和后公开追溯线索分流，新颖性禁止拼接文献，创造性不得使用关键日后首次公开的普通材料。
- 建立根级 `PROJECT_CHARTER.md` 作为强制产品宪章，记录开发目标、核心问题、目标用户、MVP 架构原则、禁止依赖、做/不做范围、功能修改触发标准和完成判定。
- 协作入口调整：今后每个新工作回合或独立任务，在读取/搜索/修改项目文件、查看 Git/日志、运行测试或启动服务前，必须先完整阅读 `PROJECT_CHARTER.md`；若本回合修改宪章，修改后必须重新阅读。
- 宪章 0.2 修正来源工具边界：不再禁止第三方库、Google Patents、MCP 或 Playwright；允许它们用于检索、取文和证据快照。材料是否能进入 CC 表由真实内容、公开日期、来源链和可追溯性决定，而不是由获取工具是否“官方”决定。
- 开发顺序确认：无效检索业务代码之前仍先做数据源验证，将官方 API、第三方服务、Google Patents、浏览器自动化、非专利来源和人工导入同场比较，输出来源能力矩阵、证据升级规则、固定样本以及 go/no-go 结论；未获得用户下一步确认前不启动该验证或业务开发。
- 宪章 0.3 解决说明文件边界：整个仓库每个新任务都先读 `PROJECT_CHARTER.md`，再读根和目标模块 `AGENTS.md`；但宪章的无效检索业务规则只适用于新系统及为其修改的共享边界，不自动改写与其无关的原侵权系统行为。
- 无效检索工作流修正为逐权利要求的持久化多轮循环：首轮检索后每篇材料先单独做新颖性比对；无单篇完整覆盖时选择/更新最接近现有技术，提取区别特征和组合理由 gap，再运行第二/第三轮定向检索。某项权利要求解决后只停止该项，不能停止其他权利要求。
- 创造性边界：D1+D2/D3 合计覆盖全部技术特征只是组合分析的必要条件；仍须记录组合动机、技术问题、明确启示/公知常识证据、反向教导和技术效果，不能只因特征拼齐即判断缺乏创造性。
- 用户确认按逐权利要求多轮方案开始实现，并继续要求对 `/Users/xiexiaoxiong/Documents/Patent项目配套/实例专利` 全部专利逐件测试、失败修复后重跑，直到全量流程顺畅完成。
- 宪章升级为 0.5：仓库只保留一份根产品宪章；明确原侵权单向链路不限制无效 `I0` 循环，并冻结普通现有技术/抵触申请双日期通道、后续轮次双检索线、D1 版本化、四类 gap、轮次原子收口和人工继续规则。
- 新增 `docs/invalidity/WORKFLOW_SPEC.md` 1.0，作为无效测试版、正式 release 和 Portal 接入的详细状态机、数据持久化、安全、报告与全量回归规范。
- 当前状态修正：无效检索业务代码、数据源实测、Portal 接入和隔离运维骨架已经部分建立；尚未完成法律日期双通道、人工关键日确认、稳定报告快照、安全边界、全量实例完整流程和正式晋级验证，不能标记为 MVP 完成。
- 说明文件冲突处理再次确认：仓库仍只有一个产品主文件 `PROJECT_CHARTER.md`；原有 `AGENTS.md` 是工程索引，不与宪章并列竞争。无效业务状态机由从属的 `docs/invalidity/WORKFLOW_SPEC.md` 统一定义，共享模块改动必须同时满足原侵权不回归和无效隔离要求。
- 无效多轮算法进一步冻结：逐权利要求原子提交 `claim_round_frontier` 语义；每轮所有新材料先逐篇做日期资格和全限制比对，任何单篇完整覆盖即进入新颖性 CC。D1/D2/D3 是完成证据校验后的分析角色而非命中或轮次顺序，多篇累计覆盖只能进入创造性分析。
- 成功终态继续规则收紧：`novelty_evidence_complete` 和 `inventive_step_evidence_complete` 均直接输出当前调查报告，不进入普通 continuation；默认继续集合只能包含仍有未解决 gap 的非成功终态。未来如需补强成功结论，应另建关联的补充调查，不能重开或覆盖原 ClaimInvestigation。
- 无效人工复核 P0 已形成可测试闭环：候选日期确认和正式 PDF 证据导入采用三路 CAS、稳定幂等键、真实 actor、不可变 `document_versions`、SHA/大小/MIME 回执核验和 PDF/OCR/渲染 fail-closed；资格、I4-S、D1、gap、I4-I 与报告均绑定确切文献版本。
- 人工复核不创建伪检索轮次，也不扩大 `max_rounds`；新增回归验证自动第 3 轮后的人工复核仍由 continuation 创建真实第 4 轮。人工 gap 使用独立 review frontier，下一真实系统轮会继承并 supersede，旧 iteration 重放不会读取其后产生的人工事实。
- 报告当前性改为同时绑定 investigation state version 与 review revision；人工复核成功后排队新 I5，默认报告拒绝 stale snapshot，显式 snapshot id 仍可读取历史。worker 最终失败、取消和租约耗尽会同步关闭人工 action，避免永久停在 running。
- Portal 人工复核入口只接受 PDF（50MB 上限），上传回执绑定用户、session、环境和 SHA；浏览器不能提交 actor、环境、调查 ID、预期哈希或后端路径。正式代理固定 5109，测试代理固定 5209，不再相互回退。
- 本轮静态与确定性验证：无效后端 `232 passed, 3 skipped`（3 项为真实 PostgreSQL）；Portal `ts-check`、定向 eslint、无效契约测试和 production build 通过；隔离 PM2 配置 9/9；实例专利 parser 15/15、fixture 工作流 15/15。fixture 只验证合同与停止规则，不是 live 检索或法律证据。
- 当前宿主验收阻塞：受限执行环境禁止访问本机 5432，隔离 PM2 守护进程也不能跨执行会话存活；两次宿主权限申请均因 Codex 当前用量额度被审批系统拒绝。故真实 PostgreSQL、5201/5209 健康检查、GLM-4.6V、真实 provider 和 15 件 live 全量回归尚未完成，不得将上述 fixture 结果标记为 live 或正式晋级依据。

### 2026-07-22

- 用户决定暂时停止从 USPTO/EPO 进行 live 专利检索，改以智慧芽（Patsnap）作为当前全球专利主 provider 候选；EPO/USPTO adapter 保留但不默认启用，不删除既有代码或 fixture。
- 已将该决定写入 `PROJECT_CHARTER.md` 0.8、`docs/invalidity/WORKFLOW_SPEC.md` 1.5、`docs/invalidity/PATSNAP_PROVIDER_PLAN.md`、无效测试版 README 和 PM2 隔离说明。智慧芽 API 的认证、签名、查询/取文路径、字段能力和许可边界必须以用户提供的接口文档为准；文档和测试凭据到位前不得猜测 endpoint 或伪造响应。
- 无效后端完整回归达到 266 项普通测试通过、6 项真实 PostgreSQL 用例在提权环境通过；后续将按智慧芽 contract 增加 provider fixture、测试配置和 live 小样本验收。
- 测试 PM2 `5201/5209` 与 Portal `3001` 已按隔离实例重启并健康；正式无效 `5109` 仍未 promotion/启动，避免在智慧芽真实验收前误把测试代码当正式 release。
- 智慧芽 REST adapter 已按官方 contract 完成测试版离线实现：P001 计数、P002 检索、P012 题录、P018 权利要求、P019 说明书、P020 PDF、P042 附图；Bearer Key 仅由当前环境 5209 API 读取，5201 parser、Portal 和正式 release 均不接收该 Key。
- 独立安全复核后收紧了文献身份和证据边界：P012/P018/P019/P020/P042 必须匹配目标 `patent_id`/`pn`，拒绝 `pn_related` 族内替代；P002/P012 日期冲突转人工复核；P020/P042 未真实下载前不预置媒体可用标志，媒体失败保留已冻结的文本文献并标记 evidence gap。
- 智慧芽重试改为计费安全策略：POST transport/429/5xx 不自动重放，仅官方业务忙码 `68300008` 可有界重试；GET 可对瞬时 transport/429/5xx 有界重试；每次实际调用分别记录 request id、correlation id、状态和 `x-openapi-amount`，不记录 Key/Authorization。
- P020/P042 成功下载时会把供应商 metadata 原始响应、调用审计和响应哈希写入环境隔离 ArtifactStore，并由 PDF/图片二进制工件引用该 metadata 哈希；过期签名 URL 不再是唯一来源链。最终失败的 `PatsnapApiError` 也携带脱敏逐次调用审计，HTTP 401/403 绝不因业务码组合而重试。
- 新增 `scripts/patsnap_smoke.py` 分阶段验收：默认只调 P001，`--search-limit` 调 P002，`--retrieve-first` 调 P012/P018/P019，P020/P042 必须通过显式媒体参数触发并校验 MIME、真实字节和 SHA-256。当前真实测试环境智慧芽 Key 为空、provider 仍为 `google_patents`，因此尚未发出 live 请求或声明任何账户 capability 已验证。
- 智慧芽失败审计已扩展到搜索、详情和媒体全链路：HTTP/MIME/JSON/schema/身份/二进制校验失败会保留可取得的原始响应与完整脱敏 attempt log；无响应的 transport 失败仍保存零字节响应标记和调用审计。详情或媒体失败工件会冻结到环境隔离 ArtifactStore，不再只留下截断错误字符串。另修复 `provider_image_metadata_response` 被误算为真实附图的问题。
- 本轮离线回归达到 Patsnap adapter 32/32、Patsnap+工作流定向 73/73、无效后端全量 306 passed/6 skipped（跳过项为需要本机 PostgreSQL 的环境用例）、PM2 隔离 11/11；测试版 5201/5209 已由独立 PM2_HOME 重启并返回 200，正式 5109 未启动。当前 `.env.test.local` 的智慧芽 Key 仍为空且 provider 保持 `google_patents`，未发出 live 智慧芽请求；最终启用仍需用户只在该文件写入测试 Key，依次完成真实 P001/P002/详情/媒体 smoke 与许可、额度验收。

### 2026-07-23

- 智慧芽测试 Key 已由用户写入隔离的 `.env.test.local`，未进入代码、日志、Portal 或正式配置。真实权限矩阵确认：P002 `/v2`、P060 beta、P061 成功；P001 `/v2`、标准 P060、P012、P018、P019 均返回业务权限码 `67200004`；P020/P042 未擅自调用。
- 测试版 Patsnap adapter 新增 P060 beta 单图与 P061 多图相似检索。P060 beta 只允许 `D + model 1/2`，P061 允许 `D + 1/2` 或 `U + 3/4`；P061 限 2–4 个不同公网 HTTPS URL，并在 POST 前执行 DNS 公网校验。POST transport/429/5xx 不盲目重放，结果只形成 lead，原始响应使用独立 `provider_image_search_response` 工件。
- `scripts/patsnap_smoke.py` 支持 `--skip-count` 独立验证 P002、使用既有 `patent_id` 直接验证/探测详情权限，以及用一张或 2–4 张公开图片分别验证 P060 beta/P061。官方样例图的适配器级 P060/P061 smoke 均成功，输出只含脱敏题录、哈希和调用审计。
- 测试 module-lab 在既有 `I3_PATENT_SEARCH` 中增加 `text`、`image_single`、`image_multiple` 三种 modality。显式 `search_provider=patsnap` 只覆盖当前 lab run，全局测试 provider 继续是 `google_patents`，不修改 I0。图片输入要求严格整数 model/max_results/offset，结果必须保持 lead，文本/图片原始响应按真实 kind 冻结。
- `/test/module-lab` 已增加 P002、P060 beta、P061 三个快捷模板，按钮只填充通用 JSON；页面明确测试 investigation、lead 边界、当前详情权限缺口和公网图片要求，不包含 Key，也不回退正式 5109。真实 P060 module-lab run `6fca23a3-5e2d-4b8a-a762-b45f21038e1f` 已 succeeded，返回 3 条 lead并冻结响应 SHA-256，本地路径和租约 token 在 API 输出中脱敏。
- 验证：无效后端 `372 passed, 6 skipped`，真实 PostgreSQL 合同文件在随机临时 schema 中通过并清理；Portal `pnpm ts-check`、定向 ESLint、无效契约测试和 production build 通过；PM2 隔离测试 11/11。隔离测试 5201/5209 与共享 Portal 3001 已重启并健康，正式无效 5109 仍未启动。
- 当前不切换完整智慧芽 provider：必须先开通 P012/P018/P019，并确认 P010、P020/P042、额度/QPS及原始响应/PDF/图片商业留存许可；否则 P002/P060/P061 只能用于测试实验室中的候选发现，不能进入 D1、I4 或 CC 表。
- `/test/module-lab` 登录后偶发显示 Next 404 的根因不是路由漏构建，而是别名页使用 App Router 页面级 `redirect()`；Next 16 的静态 307 响应体包含 404 外壳，登录跳转与客户端刷新竞态时可能直接显示该外壳。别名页现已直接复用 `/test/invalidity-pipeline` 页面，不再产生二次重定向；匿名访问仍固定跳转登录，带登录态条件的别名和 canonical 页面均返回 200。Portal 类型检查、定向 ESLint、无效契约测试和生产构建通过，`patent-web` 已重启。
- 新增面向用户的 `/test/invalidity` P002 引导页，固定使用测试上传根、测试代理和 5209，依次执行 `I1_TARGET_SNAPSHOT → I2_QUERY_PLAN → I3_PATENT_SEARCH`；首页“无效检索测试版”也指向该页。正式 `/invalidity` 继续只认 prod 配置，不会回落 5209；正式 5109 仍未启动。
- I2 检索守门现在按表达式原子与权利要求限制/同义词做确定性匹配，只保留实际锚定的不同 feature ID；“中空孔”这种单特征查询继续拒绝。某 provider lane 缺失时优先复用另一 provider 已通过守门的 ordinary 紧凑查询，不再默认膨胀成全部限制的超长 `AND`。
- P002 引导页将技术主题同义词组、两个不同必要特征同义词组编译为组内 `OR`、组间 `AND` 的 `TACD` 查询，并在 provider 端追加 `PBD:[* TO 关键日前一日]`。智慧芽成功信封中的精确 `data:{}` 被确认为合法零命中；仅该形状按 0 条处理，其他未知非空结构仍 fail-closed。
- 真实样例 `CN222356540U`：I2 run `c6ff98cd-c460-49b2-aa0e-75fb5af07968` 使用 `glm-4.6v` 和 2 张附图，约 65 秒成功；P002 run `5b0a3941-c8eb-4302-b435-8515335e3d93` 单次约 3 秒成功，返回 10 条 lead、总命中 100，实际 provider=`patsnap`、network_used=true，响应快照 SHA-256=`c827f224e32878331525ba751300b3388f74dec7cdcbe6f04c44cf2ca85e40f5`。
- 最终验证：无效后端 `388 passed, 6 skipped`；Portal `pnpm ts-check`、定向 ESLint、无效契约测试和 Webpack production build/tsup 均通过。测试 5201/5209 和正确的 `.pm2-patent-prod` Portal 3001 已重启在线；误建在默认 `~/.pm2` 的重复 `patent-web` 已删除，避免 3001 端口互相抢占。
- 后续真实零命中复盘确认 Portal P002 编译器存在固定顺序截断缺陷：每组先放中文再 `.slice(0, 4)`，三个中文同义词会把英文全部挤掉；长英文关系句还可能退化成孤立方向词，含 `and` 的短语使用未验证引号语法也存在供应商解释歧义。
- `PROJECT_CHARTER.md` 升级为 0.9、`docs/invalidity/WORKFLOW_SPEC.md` 升级为 1.6：当前及后续自动化专利候选发现默认使用智慧芽；查询必须是技术主题 + 两个不同必要特征，组内 OR/组间 AND，每组平衡中英文；首条零命中时至多创建一条独立 `compact-fallback` 查询，每个 run 的实际 attempt 固定为 1。
- I2 提示词现要求纯中文/纯英文的字面名词短语和构件/结构名词；Portal P002 编译器按词形接近度选择中文，保留安全英文完整短语、紧凑技术词和通用结构抽象，并拒绝中英混杂项、孤立位置词及有布尔语法歧义的英文短语。该规则使用通用构件后缀和词法算法，没有写死本例耳机或中空孔词表。
- 修正后真实 I2 run `f22143a7-9a94-41f2-8c75-4257ddd4aa12` 使用 `glm-4.6v` 和 2 张附图；首条 P002 run `f40569d7-ee2d-4456-a047-e6f85165f344` 返回 10 条 lead、总命中 94，实际 provider=`patsnap`、network_used=true，响应工件 SHA-256=`79aed1fd79765df8d3cf8c60afe0bb910434956ae6d5e98be2229ed72b5d16f0`，因此未触发回退。回归为后端 `388 passed, 6 skipped`，Portal 类型检查、定向 ESLint、无效契约测试和 8GB Node 堆的 Webpack production build/tsup 通过。
- 用户进一步确认后，`docs/invalidity/WORKFLOW_SPEC.md` 升级为 1.7，自动化专利候选发现不再只在测试页按 run 覆盖智慧芽：测试本地配置、测试/正式模板和 PM2 bootstrap 的默认 provider 已统一固定为 `patsnap`，缺少对应环境 Key 时拒绝启动，不回落 Google Patents、EPO 或 USPTO。测试 5201/5209 已重启加载该配置；P012/P018/P019 未开通仍会阻止后续全文证据升级，但不会改变检索 provider。
- 调查 `386c60ef-4ea8-58c3-969d-c7f514a6ade5` 的 I2 run `157b3b0b-d791-47e8-b6c0-fd217256d67f` 已成功输出 5 个必要特征并让首条 ordinary query 绑定 `头戴`、`中空孔` 两个不同 feature ID；失败发生在 Portal 调 P002 前：旧规则因“头戴”是主题“开放式头戴耳机”的子串而误删该真实限制。`WORKFLOW_SPEC.md` 升级为 1.8，编译器现在只排除与完整主题等同的词项，首条不合规时继续选择后续合规查询，并要求英文紧凑化保留区别性技术词。

### 2026-07-24

- 无效检索问题排查与修复轮：完整测试基线先确认为 单元测试 388 passed/6 skipped、parser 全量回归 15/15、fixture 工作流全量回归 15/15、Portal `pnpm ts-check` 与 `scripts/test-invalidity-portal-contracts.ts` 通过；问题集中在 live 链路。
- 修复 1（可观测性）：`worker.py` 对 handler 未处理异常只对外写“任务执行失败；详细诊断已保留在受限服务日志中”，但实际没有任何代码写该受限日志，真实异常完全丢失，违反宪章“错误不得被吞掉”。现新增 `write_restricted_diagnostic`：异常类型、脱敏消息（Bearer/Basic/api_key/token/secret/password 模式打码）和完整堆栈写入 `<artifact_root>/diagnostics/worker-errors-<env>.jsonl`（目录 0700、文件 0600、50MB 轮换），不进入前端、普通日志或报告。新增单元测试 `test_worker_writes_restricted_diagnostic_for_unexpected_exceptions`。
- 修复 2（live 崩溃根因）：借助新诊断日志定位到 live 调查反复死于 `psycopg2.errors.StringDataRightTruncation` —— `iterations.progress_signature` 定义为 `CHAR(64)`（只够放哈希），但 `_round_progress_signature` 实际写入“轮次|文献键列表|gap 列表|计数|complete”的可读签名，live 文献一多即超 64 字符，导致 I0_ORCHESTRATE 每次收口时崩溃、整个调查 failed。已将列放宽为 TEXT 并在 `db.py` 增加幂等迁移 `ALTER COLUMN progress_signature TYPE TEXT`，服务重启自动应用。
- 修复后 live 单件端到端验证（CN222356540U，调查 `93ebcbd2-15fd-538e-9d0c-623c1931dcd9`）：13 项权利要求全部到达真实终态（9 `partial`、4 `needs_human_review`，无 `failed`、无卡死 `running`）；调查终态 `needs_human_review`；9 个 iteration 全部 `partial` 收口，42/16/9 条查询分别 completed/failed/partial 终态，`/v1/investigations/{id}/report-data` 返回 200 及合法 ReportDataV1 快照。
- live 侧确认为预期行为而非缺陷：智慧芽 `error_code=67200004` 是“无权限或套餐额度超限”（测试账号仅 P002/P060 beta/P061 已开通，P012/P018/P019 详情接口未开通），系统按 README 边界记录 provider 失败并把权利要求标为 partial，不静默降级；抵触申请通道因无用户确认的目标公开日按规范 fail-closed 跳过；多重/择一依附权利要求按规范转人工复核。
- 修复后全量重跑：单元测试 389 passed/6 skipped（含新增诊断测试）、fixture 全量回归 15/15、parser 全量回归 15/15、Portal ts-check 与无效契约测试通过。测试 5209 已重启加载修复。
- 仍未完成（不算本轮回退）：智慧芽 P012/P018/P019/P020/P042 权限未开通前，专利侧真实全文/PDF 证据链无法闭合，live truth gate 的多模态 I4-S 完整链只能依赖 NPL 取文；15 件实例专利的 live 全量回归（每件约 40–70 分钟）尚未跑完，MVP 完成判定第 3/15 项仍不成立；正式 5109 仍未 promotion。

### 2026-07-25

- 全文取文接口权限实测（用户要求逐接口确认）：P002 检索正常（lead `CN118474599A`，error_code=0）；`--probe-detail-endpoint` 分别实测 P012/P018/P019 均返回 67200004；首次独立实测 P020（pdf-data）与 P042（fulltext-image）也均返回 67200004，五个接口的错误响应是同一个 111 字节权限信封（SHA-256=`1373786f22793a81…`，`error_msg="No permission or API package quota has exceeded the limit!"`），确认是账号级“未开通或套餐额度超限”，不是路径或代码问题。接口路径常量 `/basic-patent-data/{bibliography,claim-data,description-data,pdf-data,fulltext-image}` 已核对无误。仓库没有智慧芽 MCP 通道，不存在可测的 MCP 取文路径。
- 当前账号实测能力矩阵：P002 文字检索、P060 beta 单图、P061 多图可用；P001 计数、P012/P018/P019 详情、P020 PDF、P042 附图不可用。专利侧证据升级仍被阻塞在 lead 层，需用户向智慧芽确认是“接口未开通”还是“套餐额度超限”。
- 智慧芽 streamable HTTP MCP（用户提供 `connect.zhihuiya.com/f176d7/mcp` 地址）实测：标准 initialize 握手、GET、Bearer 头传递、旧协议版本四种变体均被网关以同一个 67200004 权限信封拦截，未到达 MCP 协议层；说明 MCP 通道与 REST 共用同一套账号套餐权限，不是绕过 REST 权限的独立通路。MCP 地址已只写入 `.env.test.local`（`INVALIDITY_TEST_PATSNAP_MCP_URL`），探针脚本 `scripts/patsnap_mcp_probe.py` 保留用于权限开通后复测。
- 修复后复用该 I2 快照的真实 P002 run `2d8cdfdc-e97f-4ff1-8fac-d580cd212f6c` 以单次 attempt 成功返回 10 条 lead，`actual_provider=patsnap`、`network_used=true`；供应商响应工件 SHA-256=`ae4bd3d0c973613e5031708894ce0f6031727c053804b190f39a12051f1f2175`。后端全量 `388 passed, 6 skipped`，Portal 类型检查、定向 ESLint、无效契约、production build 和 PM2 隔离 11/11 均通过；正确的 `.pm2-patent-prod` Portal 3001 已重启。
- Portal 测试页 `/test/invalidity-pipeline` 报“无效检索测试代理失败”的根因：后端 `GET /v1/investigations/{id}/review-context` 的响应把调查标识和状态放在嵌套 `investigation` 对象里，而 Portal `invalidity-contracts.ts` 的 `assertInvalidityReviewContext` 要求顶层 `investigation_id`/`status`；校验抛出普通 Error 落到代理路由 catch-all，统一变成 500“无效检索测试代理失败”。Portal 契约单测 fixture 自带顶层字段，与真实后端形状脱节，所以此前未暴露。
- 修复（后端兼容增量，不动 Portal 生产构建）：`db.py` `get_review_context` 返回 dict 新增顶层 `investigation_id` 与 `status`（取自嵌套 `investigation` 行）。review-context 顶层契约字段由此固定为：`contract_version`、`investigation_id`、`status`、`state_version`、`review_revision`、`quiescent`、`recomputation_required` 及 `claims/documents/document_versions/latest_qualifications/date_fact_revisions/current_gaps/current_d1/action_history` 等数组；任何一侧改动都必须保持该形状，Portal 契约 fixture 与后端响应必须同步更新。
- 验证：新增集成测试 `test_real_postgresql_review_context_exposes_top_level_identity`（`tests/test_db_contracts.py`，`postgres_repository` 临时 schema，断言顶层标识/状态与全部 Portal 必需字段形状）；带 `INVALIDITY_TEST_DATABASE_URL`（取自 `.env.test.local` 的 `INVALIDITY_DATABASE_URL`）全量 pytest `396 passed`（此前 6 个真实 PostgreSQL 集成测试因该变量未导出而 skip，本次一并跑通）；重启 5209 后对用户调查 `0b61ccc1-…-23b07c99519c` curl review-context 确认顶层字段返回正常。
- module-lab 逐单元验证工具落地：`5-invalidity-search-test/scripts/lab_unit_check.py`，从 `.env.test.local` 读 token/地址，支持 `--mode fixture|manual|live`、`--module` 多选（缺省全部 11 个模块代码）、`--investigation-id`、`--claim-investigation-id`、`--input-json`、`--tag` 幂等标签和 `--timeout` 轮询上限；只打测试 5209，不触正式 5109。
- 逐单元验证结果（对用户调查 `0b61ccc1-…-23b07c99519c`）：fixture 模式 11/11 全部 succeeded（单次 attempt、`actual_provider=fixture`、`network_used=false`）；live I1_TARGET_SNAPSHOT succeeded（`persisted_target_snapshot`，读持久化调查，无网络）；live I3_PATENT_SEARCH succeeded（`patsnap`、`network_used=true`、单次 attempt、count=10，命中 US20020142469A1/US6696296B2 等 10 篇真实专利，供应商响应工件已冻结含 SHA-256）；live I5_REPORT succeeded（`persistent_report_snapshot`）。
- live I3_FETCH 预期失败并确认根因：用 I3 命中的 US6696296B2 lead 取全文，run 终态 failed（3/3 attempts，`LAB_DEPENDENCY_FAILED`，事件链 queued→retry×2→failed 完整）；进程内直接调 `PatsnapProvider.retrieve` 复现底层错误为 `PatsnapApiError error_code=67200004`，即账号级权限墙，与 P012/P018/P019 探测结论一致。
- 已知限制（待办）：67200004 这类账号权限/额度错误当前被 `_guarded` 归为 RetryableJobError，会白跑 3 次 attempt；后续应在 Patsnap provider 或 `_guarded` 把 67200004 识别为永久性错误（PermanentJobError），避免无效重试并直接把权限原因写进 error_message。
- Portal 测试页“错误：测试资源不存在”（404）的触发条件已确认：这是 Portal 测试资源归属校验（`invalidity_test_resource_owners` 表，按 user_id 绑定 investigation/module_run）按设计拒绝，不是系统故障。只有通过 Portal 测试代理创建的资源才会自动绑定归属；直接调 5209 API 创建的调查（如 `93ebcbd2-…` live 回归调查、`lab_unit_check.py` 的 `invalidity_lab_*` 调查）没有归属记录，把这些 id 填进测试页的 investigation_id 输入框必返回 404。页面可用 id 以归属表为准；fixture/manual 模式 lab 运行留空 investigation_id 时会由后端自动建 lab 调查并由代理自动绑定。
- 经用户确认，已把 `93ebcbd2-…`（CN222356540U live 回归调查，needs_human_review，222 个模块运行）及其 I0_ORCHESTRATE（`1caa35a8-…`）、I5_REPORT（`4ee7a451-…`）两条顶层运行补绑到唯一用户（user_id=1）名下；该调查名下其余 220 条逐轮次模块运行未绑定，Portal 页面对其刷新状态仍会 404，需要时再补。
- 用户反馈测试页显示方式过于技术化看不懂，已将 `/test/module-lab` 从 re-export 改为独立的**通俗版测试台**（`IP-protral/src/app/test/module-lab/page.tsx`，约 600 行客户端组件）：三部分结构——①「测试完整流程」用 UploadForm 上传后按 5 秒轮询调查状态和 review-context，用进度条+每权利要求一行通俗状态徽章展示（状态/终态原因全部翻译成通俗中文，如“需要先人工确认这项权利要求的申请日期”）；②「单独测试每个环节」把 11 个模块代码映射为带序号的通俗环节卡片（如“4 搜索相似专利”），每个环节一键“用示例数据测试”（fixture），I1/I3_PATENT_SEARCH/I5_REPORT 三个环节额外提供“用真实数据测试”（live，输入自动构造），结果显示成功/失败横幅+通俗结果说明+耗时/尝试次数+可折叠技术详情；③「人工确认」内嵌原 InvalidityReviewPanel 并加通俗引导。错误信息统一经 `friendlyError` 翻译（404 归属、REVIEW_NOT_QUIESCENT、LAB_DEPENDENCY_FAILED 等）。fixture/live 两种数据方式按宪章 4.8 保留明确标注（内置示例/真实联网）。原技术页保留在 `/test/invalidity-pipeline`，新页 header 提供“高级技术页面”入口。
- 验证：`pnpm ts-check` 通过、定向 ESLint 干净、`bash scripts/build.sh` 生产构建成功（路由含 /test/module-lab）、`.pm2-patent-prod` 的 patent-web 已重启；未登录 curl `/test/module-lab` 正确 307 到 /login（鉴权行为符合宪章 4.8）。
- 通俗版第二轮改版（用户反馈“只看每个流程的输入输出，不管细节”）：①11 个环节卡片每张新增一行灰底“输入：… → 输出：…”说明（如“输入：检索词 → 输出：相似专利清单”）；②移除页面内嵌的 InvalidityReviewPanel 交互面板，第三部分改为纯文字说明卡“为什么系统有时会停下来等人确认”——解释关键日停闸是法律安全设计（AI 先自动核验，核验不了不允许猜日期继续，猜测日期的代价大于停下问人），并明确“测试时不需要操作这部分，看到需要人工确认=按规则正确停闸=测试通过”；③完整流程到达 needs_human_review 时的提示同步改为“流程已按规则正常结束，测试到此算走通”。专业复核操作仍保留在 `/test/invalidity-pipeline` 高级页面，说明卡提供入口。ts-check、ESLint、生产构建、patent-web 重启均通过。
- **关键日规则产品变更（用户 2026-07-25 明确决定，宪章已升至 1.0）**：关键日由“逐权利要求人工核验”改为“专利级单一关键日”——专利有可解析优先权日取最早优先权日，否则取申请日，自动适用于全部权利要求并逐条记录 `critical_date_basis`，不再逐项停车；仅专利级申请日/优先权日均缺失或无法解析时才转人工；多优先权逐主题精细化留待用户明确恢复。用户原话触发：调查 `75d78f0b-…`（CN216205649U 水枪，申请日 2021-04-20、优先权日 2020-04-22）16 项权利要求全部因“优先权未核验”停车，用户指出日期是专利级事实，无需逐项填写。
- 文档同步：`PROJECT_CHARTER.md` §4.4 关键日句改写、§9 新增 2026-07-25 决定、§10 新增 1.0 版本行、头部版本号 0.9→1.0；`docs/invalidity/WORKFLOW_SPEC.md` §4.1 由“逐权利要求关键日”改写为“专利级关键日”（含人工改定专利级日期、checkpoint 继续规则），§12 人工复核操作同步改为“确认或改定专利级优先权和关键日”。
- 代码改动：`date_rules.py` `resolve_critical_date` 语义重写——`priority_verified=None` 不再阻断，有可解析优先权日直接返回（basis=`patent_level_priority_date`）；`priority_verified=False` 仍回落申请日；申请日/优先权日均缺失或无法解析才 `requires_human_review`；`application_date_verified=False` 显式标记仍 fail-closed。`workflow.py` 停车文案改为“专利级关键日无法确定：申请日和优先权日均缺失或无法解析，需人工确认”。Portal `module-lab/page.tsx` `friendlyTerminalReason` 同步翻译新文案。
- 测试更新：`test_date_rules.py` 旧“优先权未核验阻断”测试改为专利级规则三组断言 + 新增缺日期转人工测试；`test_workflow.py` `test_continuation_resumes_confirmed_critical_date_at_round_one` 把停车条件改为专利级日期全缺（申请日/优先权日置 None）；新增 `test_patent_level_priority_date_auto_applies_without_human_stop`（优先权日自动适用、不停车、basis=patent_level_priority_date）与 `test_patent_level_application_date_used_when_no_priority`（无优先权用申请日）。带 `INVALIDITY_TEST_DATABASE_URL` 全量 pytest `399 passed`；测试 5209 与 Portal 3001（生产构建）均已重启加载。
- 注意：旧调查（如 `75d78f0b-…`）已停车的权利要求不会自愈，需在 `/test/module-lab` 重新上传专利跑新流程验证；人工复核改定关键日的 continuation 通道（confirm_priority/use_filing_date/set_manual_date）保持不变。
- **测试页合并为单一入口（用户 2026-07-25 要求“所有测试放同一个页面”）**：`/test/module-lab` 重写为唯一无效检索测试页，两大部分——①「完整流程：上传专利，输出无效分析报告」：上传后除原有进度/权利要求状态外，流程到终态自动拉取 `GET /v1/investigations/{id}/report-data?preview=true`，在页内渲染通俗报告：报告头（专利号/名称/申请日/优先权日/免责声明）+ 逐项权利要求卡片，每卡含**无效说明**（按状态生成通俗结论，明确“未找到≠有效”）、**对比文件**清单（D1/组合文献角色、公开日、provider）、**比对表**（技术特征×对比文件的披露状态矩阵，明确公开/隐含公开/不确定/未公开/分析失败五色）、**比对说明**（D1 选择理由、逐特征 analysis+原文定位、创造性组合 analysis）；②「单独测试每个步骤」：11 张步骤卡每张带可编辑输入框（预填与 lab fixture 同形的 manual 示例 JSON，常量 `MANUAL_EXAMPLES`），三个按钮「运行（用我填的输入）」（input_mode=manual，JSON 解析失败给通俗提示）、「用内置示例跑」（fixture）、「用真实数据跑」（仅 I1/I3_PATENT_SEARCH/I5_REPORT），输出区直接展示格式化 output JSON + 可折叠完整运行记录。第三部分说明卡改写为专利级关键日口径（自动用优先权日/申请日，读不出才停车），专业复核入口降级为文字按钮保留（`/test/invalidity-pipeline` 页面本身保留）。`/test` 首页入口改指 `/test/module-lab`。
- 配套改动：代理白名单 `invalidity-test-proxy-policy.ts` 新增 `read_report_data`（`GET /v1/investigations/{id}/report-data`，query 透传 preview/snapshot_id），route.ts 归属校验组同步加入；契约测试 `test-invalidity-portal-contracts.ts` 新增 read_report_data 断言，并把 module-lab 的过时断言（原要求 re-export alias 且禁止出现 /invalidity 路径）替换为统一页结构断言（含两大部分标题、report-data preview、test 上传端点，禁止 5109/PROD 与正式 session API）。
- 验证：`pnpm ts-check` 通过、定向 ESLint 干净（仅 /test 首页两个改动前已存在的 unused 警告）、契约测试 `invalidity portal contract tests passed`、`pnpm next build --webpack` + tsup 生产构建成功（`scripts/build.sh` 的 next build worker 在本机偶发 SIGABRT，直接跑 `pnpm next build --webpack` 再 `pnpm tsup` 可完成，原因未查明，疑似构建并发与运行中服务争资源）、patent-web 已重启、未登录 curl `/test/module-lab` 正确 307。后端冒烟：带 token 直调 5209 `report-data?preview=true` 对旧调查 `75d78f0b-…` 返回 17 项权利要求与专利元数据正常（该旧调查无关键日/对比文件，属规则变更前数据，需重新上传验证完整报告）。
- **独立权利要求范围落地（取代本日早先“逐全部权利要求”记录）**：`InvalidityWorkflow` 新调查只为 `claim_type=INDEPENDENT` 的权利要求创建 checkpoint 和 `claim_investigations`，continuation、人工复核后的案件汇总也只计算独立权利要求；没有独立权利要求时 fail closed。模块一目标快照仍完整保留从属权利要求。I5 `ReportDataV1` 新增 `claim_scope`，目标/调查行均标注 `claim_type`、`in_scope`、`scope_reason`；旧从属调查行保留审计但不进入 Portal 普通状态、进度和报告摘要。
- **律师版模块实验室（取代本日早先 11 卡片/JSON 输入设计）**：`/test/module-lab` 现固定为六个业务模块——读取目标专利、确定检索截止日、制定检索方案、寻找并核验现有技术、分析新颖性与创造性、形成律师工作报告。每个模块恰好两个业务入口：“用当前案件测试”“用内置示例测试”；不再给律师展示 provider、UUID 输入框、manual JSON 编辑器或中间确认表单。真实案件只上传一次并写入 `?investigation=`，六模块复用同一案件；底层 11 个技术代码仅在折叠审计区展示。
- 历史调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 已核实：旧版“1 项需要人工确认”来自从属权利要求 17；独立权利要求 1 实际在 I2 因 `queries 不是数组` 失败。新页面明确解释并排除权利要求 17，显示独立权利要求 1 为失败、独立范围无待人工确认。
- I2 兼容层现可确定性解包 `queries/search_queries/query_plan/data/result/output/items` 等常见 JSON 包装和 query-id 映射，解包后仍执行原有至少两个必要特征等全部守门，畸形数据继续 fail closed。完整原始模型响应、标准化前后数据、prompt hash、模型和图像计数写入隔离工件，数据库/API 普通输出只暴露工件 ID、SHA-256 和标准化摘要。用上述历史案件重跑 I2 module run `e1021e1b-1bdb-40b2-a42e-6d7f372c2fe4` 一次 succeeded，主题“水枪”、8 条检索式、13 个特征；审计工件 ID `10e83ea3-73b1-4fbd-8e34-517a40897d42`，SHA-256=`d4a6751cdf01dacb114a10a83c2b383c8fc5e70fc1dd1c859ec9f5856cba753b`。
- 智慧芽 `67200004` 现由 module-lab `_guarded` 分类为永久错误 `PATSNAP_PERMISSION_DENIED`，直接说明账号无权限或额度用尽并停止自动重试；其他明确 `retryable=False` 的 provider 请求同样不再包装成三次临时重试。此前“live I3_FETCH 会白跑 3 次”的已知限制至此关闭。
- 本轮验证：无效后端全量 `398 passed, 7 skipped`；Portal `pnpm ts-check`、定向 ESLint、`node --import tsx scripts/test-invalidity-portal-contracts.ts`、两次 production build 均通过。隔离测试栈 5201/5209 与 `patent-web` 已重启；浏览器登录态验收确认历史案件恢复、六模块各 2 个按钮、旧人工确认解释、独立权利要求过滤、真实/fixture 模块一和 fixture I2 输出均正确。正式 5109 未启动、未 promotion。
- **律师版六模块结果展示完成**：模块一普通视图直接展示目标专利号、名称、完整独立权利要求和首页/技术附图概况；模块三展示 I2 本轮形成的必要技术特征、每条精确检索式和策略，不再回读旧报告中的过时 query；模块四展示专利/NPL 实际提交式、每一候选的题录、公开日、provider、全文取得状态、日期资格和 provider 错误；模块五逐文献输出“特征—判断—精确原文—位置—理由”表；模块六输出同口径律师工作报告。未取得可核验全文时必须明确显示无 Claim Chart，不得把题录、摘要或搜索线索伪装成技术披露证据。
- **module-lab 同案实验谱系决策**：`5-invalidity-search-test/src/invalidity/lab.py` 可以在只读基础 checkpoint 上叠加同一调查、同一独立权利要求最新成功的 live lab 输出（I2 limitations、I3_FETCH 文献、I3_QUALIFY 日期资格、I4-S 逐篇对比、I4-C D1），供后一业务模块读取；实验谱系不回写、不覆盖正式调查 checkpoint 或不可变报告快照。I5 live 通过 `lab_analysis_appendix` 汇总这些实验结果，并明确标注为模块实验附录。
- **NPL 实验通道与取文边界**：律师版真实模块四支持与正式测试配置一致的 `arxiv_openalex_crossref_web` 组合检索，逐 provider 保留成功、零命中和错误；文献取回按实际 `source_provider` 路由。智慧芽 P012/P018/P019 等详情接口的 `67200004` 仍是账号权限/套餐限制，永久错误只尝试一次并原样解释；搜索 lead 未取得全文时只保留为线索。
- **历史水枪案件浏览器验收**：调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 只显示独立权利要求 1，旧“1 项需要人工确认”明确归因并排除从属权利要求 17。真实模块四本轮返回 10 件智慧芽专利候选和 2 件 NPL 候选；原 `LAB_LIVE_INPUT_FORGED input.document.stage` 已修复，2 件可读 NPL 进入 I4-S，模块五对 13 个特征逐篇输出且均保持“暂不能确认”，因没有通过创造性日期资格的 D1 而停止 I4-C；模块六成功形成 13 特征×2 文献的实验附录。智慧芽全文权限限制和 Crossref/Web 搜索结构错误均以可解释终态展示，没有隐藏人工确认或 `LAB_PERSISTED_LIMITATIONS_REQUIRED`。
- **本轮全量回归（实例目录实有 17 文件）**：`/Users/xiexiaoxiong/Documents/Patent项目配套/实例专利` 包含 15 份专利 PDF 和 2 份辅助 Excel；manifest 检查通过，真实 parser 回归 15/15（权利要求、独立权利要求、专利号/名称/日期、首页和技术附图均通过），真实目标 PDF + 确定性模拟检索的 I1-I5 契约回归 15/15。模拟数据仅验证持久化、状态机、停止规则和报告契约，不作为真实现有技术或法律证据。
- **实例 live 覆盖边界（不得误报为 15/15 live）**：全量 live runner 实际完成第 1 件 `CN103025390B`，调查 `a3662ce7-82dd-5c61-b152-6ebf80445e44` 终态 `partial`；truth gate 的三个失败项分别是：未获用户确认目标公开日而按宪章跳过抵触申请真实网络通道、没有与真实原文绑定的确定性日期资格、没有绑定目标图/文献图和日期资格的完整 I4-S 链。这些是当前全局法律/证据门槛，不是代码崩溃。runner 中断时第 2 件 `CN107786922B` 已提交，调查 `c6ec85d0-9fab-5e4f-8de6-508236704b03` 随后自然到达 `partial`、无 `error_message`；为避免其余 13 件在同一全局门槛下重复消耗真实 provider 请求，没有继续启动。15/15 仅指真实解析和 fixture 契约回归；live 全量完成门槛仍未成立。
- 最终验证：无效后端 `401 passed, 7 skipped`；Portal `pnpm ts-check`、定向 ESLint、`scripts/test-invalidity-portal-contracts.ts`、8GB Node 堆 Webpack production build 与 tsup 均通过。隔离测试 5201/5209 和 Portal 3001 已重启加载；正式 5109 未触碰、未 promotion。

### 2026-07-26

- **模块一读取范围与后续检索范围正式分离**：无效测试模块一 5201 升级为 `invalidity-isolated-neutral-parser-v2`，中立快照完整保存源文件格式/字节数/页数/OCR 状态、申请号与著录项目、申请日/最早优先权日/公开公告日/授权公告日、摘要、权利要求书原文、全部权利要求及依赖关系、分页正文、结构化说明书、摘要附图和全部说明书附图。模块二至六仍只为独立权利要求建立检索和分析，不得因为下游范围收窄而丢弃模块一事实。
- **模块实验室不可变边界**：真实案件的 I1 测试从原调查已冻结的源文件重新解析，产出 `module_test_snapshot` 和模块 run 专属不可变附图；不覆盖原调查 target snapshot、checkpoint 或报告。新调查创建时会冻结全部专利附图，但现有下游多模态输入仍按既有合同选择最多两张 `target_images`，避免无关扩大模型输入。
- **律师版模块一界面完成**：`/test/module-lab` 的“读取目标专利”直接展示专利/来源摘要、四类日期、权利人/发明人/分类号、摘要、全部权利要求折叠项、说明书章节和附图画廊；页面明确提示“模块一完整读取，后续只检索独立权利要求”。附图通过 Portal 归属校验和后端 run-scope、路径、MIME、SHA-256 校验的二进制接口读取，不暴露本地工件路径。
- **历史人工确认问题闭环**：调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 的旧“1 项需要人工确认”确认来自从属权利要求 17，当前普通状态明确排除；独立权利要求 1 的真实旧状态是 I2 失败。该案件新版模块一实跑得到 19 页、17 项权利要求（1 独立、16 从属）、5 个说明书正文部分、摘要附图及图 1—图 9，共 10 幅，浏览器中全部实际加载。
- **验证与运行状态**：无效后端全量 `402 passed, 7 skipped`；实例专利真实 parser 完整性回归 15/15；Portal `pnpm ts-check`、定向 ESLint、契约测试、8GB Node 堆 Webpack production build 和 tsup 通过。隔离测试 5201/5209 与 Portal 3001 已重启加载；正式 5109 未触碰、未 promotion。
- **无效检索式生成改为“发明点 × 字段范围”**：I2 先通读标题、摘要、全部权利要求、说明书和附图，冻结 `invention_search_profile`，把概念区分为保护客体、专利特有发明点和类别共有语境。第一轮至少同时生成 `inventive_point_precision`（分类号/保护客体 + 发明点，通常全文）和 `claim_context_recall`（保护客体 + 至少两个类别语境概念，权利要求）两条检索线，可选增加题名摘要线；旧“技术主题 + 两个必要特征 + 统一 TACD”规则被取代。
- **接口与确定性守门**：`SearchQuery` 新增 `query_role`、`search_scope`、`scope_reason`、`concept_ids`、`classification_anchors`、`provider_expression`；Portal P002 编译器按 `TACD/CLMS/TTL+ABST/IPC` 保留字段范围，并按角色设置概念数门槛。类别共有特征只用于召回和语境，不得凭其命中直接宣称新颖性被破坏；最终新颖性结论仍要求一篇日期合格文献覆盖独立权利要求全部限制。
- **律师测试页与水枪真实回归完成**：`/test/module-lab` 模块三改为展示保护客体、发明点摘要、发明点概念、类别语境、查询角色和范围；模块四执行全部第一轮专利检索角色。水枪实例的通用回归验证“分类号/水枪 + 位置选择性联接装置 → 全文”和“水枪/液体喷射 + 阀 + 罐 → 权利要求”，代码不写死实例词。真实 I2 run `aa75e01c-65ea-46e9-9f87-d6faf64d05b6` 单次 succeeded，输出 `TACD:(水枪 AND 位置选择性联接装置 AND 本体与阀杆轴线平行移动)` 与 `CLMS:(水枪 AND 压力罐 AND 阀杆 AND 本体)`；旧冻结快照无分类号事实，因此未猜造 IPC。
- **最终验证与运行状态**：后端全量 `403 passed, 7 skipped`；Portal `pnpm ts-check`、无效契约测试、定向 ESLint 和 `pnpm build` 通过。全量 `pnpm lint` 仅被本轮范围外 `src/app/module1/page.tsx` 的既有条件 Hook 调用错误阻塞。浏览器验收确认 3001 新模块三文案、两种输入、内置案例的发明点/类别语境/全文与权利要求分线均正确；只在 `/Users/xiexiaoxiong/.pm2-patent-prod` 重启 `patent-web`，隔离测试 5201/5209 online，正式 5109 未触碰、未 promotion。
- **模块三复合概念改用部分关键词表达（用户 2026-07-26 决定）**：发明点画像仍可用完整复合语句解释专利发明点，但执行检索式只需从中选择一至三个有意义的部分技术关键词。确定性守门要求每个词真实出现在表达式中并映射回概念绑定的独立权利要求 feature ID，不凭模型声明的 ID 放行；发明点线至少一个概念，语境线至少两个不同概念。固定回归接受 `水枪 AND 位置选择性联接装置 AND 中间位置`（全文）与 `水枪 AND 压力罐 AND 阀导管 AND 阀杆`（权利要求）。
- **P002 与律师页面同步**：新 role-aware 查询保留最多三个已核验关键词组，避免把合规检索式静默裁短；旧无 `query_role` 快照保持历史最小组数。模块三失败摘要不再把“无可执行输出”误写成“模型识别 0 个发明点”，而是直接显示守门失败并引导查看具体原因。正式 5109 与 prod release 未触碰。
- **验证与加载状态**：后端全量 `404 passed, 7 skipped`；Portal 无效契约测试、`pnpm ts-check`、定向 ESLint、8GB Webpack production build 和 tsup 通过。隔离测试 5201/5209 与本地 Portal 3001 已重载，5209 health 正常、module-lab 匿名鉴权跳转正常；正式 5109 未触碰、未 promotion。
- **水枪案件 I2 二次实跑失败诊断（尚未改代码）**：最新 module run `ef718b48-c231-4ed7-ad90-394703c6a297` 的模型原始响应实际同时返回了 `inventive_point_precision` 与 `claim_context_recall`。发明点线写成 `F16K 1/00 AND 位置选择性联接装置 AND 中间位置`，但该调查冻结的模块一事实没有任何分类号；模型猜出的 `F16K 1/00` 因而被分类号白名单剔除，表达式自身又没有“水枪”，遂被守门规则以“发明点精确线必须包含保护客体或专利分类号锚点”拒绝。语境线通过后，末端矩阵校验只看到该角色缺失，页面才显示较笼统的“缺少 inventive_point_precision”。同一模式已连续出现两次，说明仅靠 prompt 禁止猜分类号不足；待实现的稳妥修复应在“发明点关键词已真实映射、仅缺合法客体/分类锚点”时确定性丢弃未核实分类号并补入已冻结保护客体，再走原守门，同时把具体淘汰原因透传到律师页面。
- **上述严格守门诊断已由用户新决定取代并完成实现**：首轮查询的唯一通用硬门槛改为“真实发明点/技术特征关键词、保护客体/类别、分类号”三类任二，查询角色仅决定检索策略和字段范围，不再要求发明点整句、发明点线固定概念数或语境线至少两个概念。格式合法但不是模块一事实的分类号可作为 `model_suggested` 检索建议使用，绝不回写成著录事实、日期事实或法律证据。
- **I2 自动重生与兜底**：第一次模型生成未形成包含必需检索角色且满足三类任二的计划时，系统把逐条拒绝原因加入下一次 prompt 自动重新生成；第二次仍缺角色时，确定性编译器仅用冻结保护客体、已映射真实 feature 词和有来源标记的分类候选补齐，再走同一守门。普通格式/组合失败不再要求律师反复点击；输入不足、模型/服务不可用或用户取消仍明确失败。模型尝试分别保存在 I2 审计工件，普通输出新增 `attempt_count`、`successful_attempt` 摘要。
- **水枪真实回归成功**：调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 新 I2 module run `c8dfe3b0-b88f-48dc-b7a3-c93236e5cac1` 首次生成即 succeeded，内部审计 `attempt_count=1`、`successful_attempt=1`、无 rejected query。发明点全文线为 `水枪 AND 位置选择性联接装置 AND 本体 AND 阀驱动器 AND 阀杆`，权利要求语境线为 `水枪 AND 压力罐 AND 阀 AND 管`；四个模型建议分类号均保留 `model_suggested` 来源。两条真实输出均通过 Portal P002 编译器。
- **本轮验证和运行状态**：无效后端全量 `405 passed, 7 skipped`，I2 定向 `20 passed`；Portal 无效契约测试、`pnpm ts-check`、定向 ESLint 和 8GB Webpack production build 通过。隔离测试 5201/5209 已用项目专用 PM2_HOME 重载并健康；专用 Portal PM2_HOME 中只重启 `patent-web`，3001 正常；正式 5109 未触碰、未 promotion。
- **模块三长耗时只读诊断**：水枪案件 I2 run `7e579c00-01f0-42b3-874c-aabf0742f6bf` 于 08:47:02 创建、08:57:40 succeeded，总耗时约 637 秒。首次 job attempt 的 GLM 直连依次出现 curl exit 56、28、7，在 600 秒模型总截止时间内约 571 秒后被归类为可重试外部依赖失败；5 秒退避后的第 2 次 job attempt 约 61 秒成功，内部模型生成 `attempt_count=1`，说明慢因是首次网络传输失败而非检索式内容自动重生。Portal 以 480×1.25 秒轮询并在“超过 10 分钟”终止，最后一次 GET 与后端成功提交同秒竞态，可能把已成功 run 显示为超时；本次未重启服务、未修改运行代码。
- **模块三 GLM 快速失败与分层超时完成**：`VisionLLMClient` 在向每个直连 IP 发送专利正文和图片前，先做不含鉴权、不含业务载荷的 5 秒 HEAD 预检；预检失败的线路立即跳过。通过预检的单次真实请求最多 180 秒，整次模型调用共享 360 秒总预算，均可通过 `INVALIDITY_LLM_*_TIMEOUT_SECONDS` 配置并在隔离配置摘要中审计。API key 继续通过文件描述符传递，prompt/payload 继续走 stdin，不进入命令行或错误日志。真实水枪 I2 run `65557897-4bfd-4210-b8a9-28c3e9485c49` 单次 job attempt 在 98.6 秒 succeeded，生成 4 条检索式；模型审计工件 SHA-256 为 `3245d65d4f882e9ceeaa95edb5c67468b618ab25f1edeb5c5a3839fde71fdb39`，不再复现坏线路占用约 571 秒的问题。
- **模块四论文全文链路完成真实闭环**：NPL 搜索继续并列使用 arXiv、OpenAlex、Crossref 和 Web；OpenAlex 现在按 `best_oa_location → primary_location → locations` 选择可用 PDF，律师页优先尝试带直接 PDF 地址的论文候选。PDF 地址仍只是 lead，只有实际下载、PDF/MIME 校验、字节冻结、SHA-256、全文抽取和页面渲染完成后才升级为 `retrieved_document`；全文取得后仍须分别通过公开日期资格和 I4-S 技术披露核验。arXiv 官方域名在本机 TUN fake-IP 环境下使用精确白名单，任意出版社/仓储域名仍走公共 DNS 与 SSRF 守门。PDF 文本中的 U+0000 在持久化前替换为空格，避免 PostgreSQL JSONB 拒绝合法解析结果。
- **真实全文验证与边界**：OpenAlex 检索 run `fbeb6a63-8018-4e80-8f9d-5ad3d8b1ebd1` 找到 3 个带 PDF 路由的候选；取文 run `fd73a074-dbea-4a28-9b0f-c84246dda6de` 从 `https://arxiv.org/pdf/1412.8416` 在 3.2 秒内取得 67,007 字符全文、4 张页面图及 SHA-256 `22b822fb3b0022e35a1b5156b47d599f4e5ba6810d92802624a2633c516e0bb2`。该论文仅用于验证取文技术链路，并非已确认的水枪现有技术；日期资格 run `b4202b08-5ee1-440d-b30c-ee5c2bce80e8` 因缺少确定性公开日证据保持 `unknown/needs human review`，未被错误升级为可用对比文件。专利全文仍等待智慧芽详情/全文接口和账号权限，接入后复用同一冻结、哈希、日期及技术披露守门。
- **最终回归与加载状态**：无效后端全量 `410 passed, 7 skipped`；Portal `pnpm ts-check`、模块实验室定向 ESLint、无效契约测试、8GB Webpack production build 和 tsup 均通过；隔离 PM2 配置测试 `11 passed`。5209 health 返回 test 环境正常，专用 `/Users/xiexiaoxiong/.pm2-patent-prod` 中只重启 `patent-web`，3001 `/test/module-lab` 正确 307 到登录页。正式 5109 未触碰、未 promotion。
- **专利发现与取文 provider 正式拆分**：产品边界升级为智慧芽 P002 负责候选发现，EPO OPS 只按 P002 的规范化公开号取回同一官方文献。EPO 不接收 I2 查询、不增加候选、不作检索回退；公开号不一致、多义申请号或同族替代均 fail closed。发现源和取文源在文献 provenance、模块 run 和工件中分别记录。
- **EPO OPS 完整文档实现**：现有 OAuth client-credentials adapter 增加精确身份核验、申请号唯一解析兜底、XML 响应审计和 `FullDocument` 全页下载。每个官方页面必须是可解析的单页 PDF，系统按官方页数逐页取得后确定性合并，记录逐页 SHA-256/字节数、总页数和合并 PDF SHA-256；EPO biblio/fulltext/description/claims/images 响应另存 retrieval bundle。只有真实字节、MIME、身份和哈希均通过才升级为 `retrieved_document`。
- **自动工作流与律师模块四同步**：`ConfiguredEvidenceGateway` 和 module-lab `I3_FETCH` 均使用智慧芽 discovery adapter + EPO retrieval adapter；EPO 无覆盖时保留智慧芽 lead/evidence gap，不再因智慧芽 P012/P018/P019 未开通而重复调用失败详情接口。`PATENT_PROVIDER=patsnap` 时测试服务要求同环境智慧芽 Key 与 EPO 成对凭据，凭据仅传 5209 API，不进入 5201、Portal、日志、数据库、报告或工件。
- **启动隔离与 EPO live 兼容修复**：测试启动脚本的第二层 `env -i` 必须显式透传当前环境 EPO 成对凭据和三项 GLM 连接预算；此前 PM2 已持有变量但 5209 子进程仍缺失的根因即为该 allowlist 漏项，现已加入合同测试。EPO live `FullDocument` link 可能以 `published-data/images/...` 开头，URL 组装前必须规范化该 collection 前缀，避免重复路径。本机 TUN 仅以完全相等主机名兼容 `ops.epo.org`，相似后缀域名仍被 SSRF 守门拒绝。
- **真实智慧芽→EPO 全文闭环完成**：水枪调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 的智慧芽 run `6298aa44-4cbd-4282-8ed2-7f6a0b09cadd` 返回候选 `US4239129A`；I3_FETCH run `9fdfd93a-29b3-4f13-82d7-d1db81944bf0` 一次尝试 succeeded，EPO 请求/返回公开号均为 `US4239129A`。固化官方 PDF 共 10 页、909,288 字节，SHA-256 `4e895de12ff009120949feb2f296f641b16a15442cc7c91ac1bf8d82495d96f2`；重新读取文件所得页数、大小和哈希均与审计记录一致，首页人工复核的公开号、标题和水枪结构图也一致。
- **最终验证与加载状态**：无效后端全量 `416 passed, 7 skipped`，PM2 隔离/凭据透传合同 `11 passed`，Portal 无效合同与 `pnpm ts-check` 通过；Portal production build 在 Node 默认约 4GB 堆限制下 OOM，改用有界 `NODE_OPTIONS=--max-old-space-size=8192` 后完整通过。隔离测试 5201/5209 均在线，正式 5109 未触碰、未 promotion。
- **律师页面证据状态修正**：模块四只有 I3_FETCH 真正返回 `retrieved_document/qualified_evidence` 且有内容 SHA-256 才计入“已取得全文”、进入日期资格和模块五逐项比对；EPO 无覆盖但请求正常收口的 lead 不再被误算为全文。候选卡分别显示发现来源与取文来源，并透传智慧芽申请号作为无公开号时的 EPO 兜底。Portal `pnpm ts-check`、定向 ESLint、无效契约测试和 production build 均通过。
- **Portal“测试服务未连接”只读诊断（未改运行代码、未重启）**：隔离测试 5201/5209 均 online 且 `/health` 返回 200；Portal `patent-web` 进程 online，其 `INVALIDITY_TEST_API_URL` 与访问令牌和两份环境文件完全一致，登录态下手工请求 `/api/test/invalidity/health` 也返回 200/test。页面没有发出自动健康检查的直接原因是客户端未完成 hydration：3001 对 HTML 引用的 `webpack-6c15e17fe1e3c70f.js` 和 `app/test/module-lab/page-88b73b0db7917f93.js` 返回 404。`patent-web` 启动于 09:41，而 `.next`/`dist` 在 10:45 被重新构建，运行进程仍持有旧构建路由/清单；因此服务端默认文案被误显示为“未连接”。恢复动作应是构建完成后重启专用 PM2_HOME 中的 `patent-web`，不需重启 5201/5209；正式 5109 未触碰。
- **Portal 测试页面恢复加载**：按用户要求只重启 `/Users/xiexiaoxiong/.pm2-patent-prod` 中的 `patent-web`，新进程 online；没有重启隔离测试 5201/5209，也未触碰正式 5109。重启后 3001 对 Webpack/runtime 与 module-lab 页面 chunk 均返回 200，5201/5209 health 均返回 200；登录态浏览器实际刷新历史水枪案件后显示“测试服务已连接”、`CN216205649U《水枪》`及可用的“用当前案件测试/用内置示例测试”按钮，确认客户端 hydration、测试代理与案件读取均已恢复。

### 2026-07-27

- **专利取文改为 EPO 优先、P020 同号回退（用户决定）**：智慧芽 P002 继续只负责候选发现；
  同一候选先用规范化公开号请求 EPO OPS，只有 EPO 明确返回 HTTP 404、无该文献、无全文或
  无 `FullDocument` PDF 媒体时，才按 P002 原始 `patent_id + pn` 调用智慧芽 P020。EPO
  鉴权、网络、超时、429、5xx、解析、身份冲突或多结果歧义不得触发 P020，P020 也不得换取
  同族、相似标题或其他候选。
- **代码与证据合同完成**：新增 `EpoFirstPatsnapPdfRetrievalProvider`，并接入
  `ConfiguredEvidenceGateway` 与律师 module-lab `I3_FETCH`。EPO/P020 有序尝试、原始响应和
  provider 选择均冻结审计；P020 成功必须校验同一文献身份、MIME、`%PDF`、PyMuPDF 可解析性、
  页数、字节数和 SHA-256，并冻结 metadata、调用审计和 PDF，不能只保留短期签名 URL。
- **P020 真实权限验证**：复用既有 P002 候选 `US4239129A` 的精确 `patent_id + pn`，不调用
  P012/P018/P019，单次 P020 live smoke 取得 `application/pdf`、256,789 字节，SHA-256
  `db35a411165ce47fd090d191c6e6e7599425a4500b950dedd3d8e504aedb5c45`。该公开号本身已有 EPO
  覆盖，因此本次只验证 P020 能力；EPO 明确无覆盖才回退及其他错误不回退由确定性集成测试覆盖。
- **验证和隔离状态**：无效后端全量 `423 passed, 7 skipped`，PM2 隔离合同 `11/11 passed`。
  P020 复用只注入测试 5209 的智慧芽 Key，无新增凭据或 Portal 秘密。隔离测试栈已重载，
  5201/5209 均 online，5209 `/health` 返回 `status=ok`、`environment=test`。正式 5109
  未触碰、未 promotion。
- **模块四全文缺失只读诊断**：最新水枪模块四两条 P002 检索线各返回 10 篇专利，但律师页
  编排当前对每条检索线固定只取前 2 篇，总计只创建 4 个专利 I3_FETCH。
  `US20100051848A1`、`AU2007309115B2` 位于第一条线第 3、4 位，均具有准确公开号和智慧芽
  `patent_id`，本轮却没有进入 EPO/P020 取文；最新 P020 实际调用为 0。页面把“未调度”和
  “取文失败”统一显示成未取得全文。`US20100051848A1` 的历史 EPO 404 发生在 P020 链上线
  前，当前若被调度应进入 404 → P020；本轮按用户要求只分析原因，未改调度或页面。

### 2026-07-27（模块四前 10 篇与模块五逐篇分析）

- 用户确认律师模块四的每条实际检索结果均处理前 10 篇候选；同一独立权利要求下同一规范化
  文献可以跨检索式去重，但不得再使用前 2/前 5 的隐藏抽样。Portal 现对专利和 NPL 每条
  检索均先冻结原始前 10 篇，再以最多 2 路并发逐篇 I3_FETCH；每篇的已取得全文、取文失败、
  未取得全文或尚未调度状态分别展示。
- 模块五不再 `slice(0, 5)`，而是对模块四所有成功取得真实全文和哈希的文献逐篇创建
  I4-S。GLM 连接类瞬时失败允许在全部文献跑完一遍后再补跑一遍；最终仍失败的文献逐篇显示
  错误，任一 I4-S 失败都会使模块保持部分完成，并阻止 I4-C/I4-I 以部分结果冒充整批完成。
- `FeatureDisclosure` 新增 `feature_text`，由程序按 `feature_id` 确定性绑定独立权利要求
  特征原文；`DocumentComparison` 同时返回公开号和文献标题。律师页逐行显示“特征编号 +
  权利要求特征原文”，不再只显示内部 feature id。
- I4-S Prompt 明确区分 `not_disclosed` 与 `uncertain`：完整复核文献后没有找到特征必须
  返回 `not_disclosed`；全文/附图不完整、证据冲突或语义模糊才返回 `uncertain`。确定性
  一致性守门会把“status=uncertain 但理由明确写未披露/没有公开”的结果校正为
  `not_disclosed`，并补记全文复核范围；带“无法确认、全文不完整、证据不足”等不确定语义
  的理由不会被误校正。
- 验证：无效后端全量 `425 passed, 7 skipped`；Portal `pnpm ts-check`、定向 ESLint、
  `node --import tsx scripts/test-invalidity-portal-contracts.ts` 和 production build 全部
  通过。隔离测试 5201/5209 与 Portal 3001 已重启；健康检查均正常，登录态浏览器刷新确认
  “测试服务已连接”及模块四/五新文案已加载。此次未启动新的真实检索或模型分析，未消耗
  智慧芽、EPO 或 GLM 调用；正式 5109 未触碰、未 promotion。

### 2026-07-27（律师页六模块显示本次实际输入）

- 用户要求 `/test/module-lab` 的六个业务模块在现有输出之外直接显示本次输入内容。普通视图
  现固定按“本次实际输入 → 本次输出结果”展示，不再只显示模块定义中的“输入：……”概括。
- 显式请求字段从每个底层运行持久化的
  `module_run.input_snapshot.request.input` 读取；模块一、日期模块、D1/组合和报告等由服务端
  自动承接持久化案件事实的空请求，不显示 `{}`，而是结合同次运行输出及冻结 Investigation
  还原律师可读输入。原始 JSON、provider 和完整运行记录仍只在折叠技术审计区显示。
- 六模块输入口径分别为：原始专利文件和专利身份；申请日与最早优先权日；完整专利阅读范围
  及独立权利要求原文；实际检索式、关键日前截止日、逐篇取文候选与日期核验对象；独立
  权利要求技术特征和逐篇比对文献；前五模块持久化结果中的独立权利要求、对比资料和逐项
  披露数量。模块二另增加独立的截止日输出卡，避免只有摘要没有成对结果。
- Portal `pnpm ts-check`、定向 ESLint、无效契约测试及 8GB 有界 Node 堆 production build
  通过；默认约 4GB 堆的首次构建按既有边界 OOM 后重跑成功。只重启专用
  `/Users/xiexiaoxiong/.pm2-patent-prod` 中的 `patent-web`，3001 鉴权跳转正常，5209 health
  保持 test/ok；正式 5109 未触碰、未 promotion。

### 2026-07-28（律师模块四日期结果简化）

- 用户确认 `/test/module-lab` 模块四不再展示“可进入创造性分析”“尚未满足创造性证据门槛”
  等技术资格文案，普通视图只保留四类日期结果：`现有技术`、`抵触申请`、`非现有技术`、
  `日期无法确定`，标签下保留一行日期判断理由。
- 映射由确定性前端 helper 完成：后端 `ordinary_prior_art` → 现有技术，
  `conflicting_application_candidate` → 抵触申请，`post_date_lead` → 非现有技术，
  `unknown` → 日期无法确定；旧记录按 `novelty_eligible`、`inventive_step_eligible` 和
  人工复核/缺失标志兼容，出现冲突时 fail closed 为日期无法确定。底层日期规则、证据字段和
  技术审计数据均未改变。
- Portal `pnpm ts-check`、定向 ESLint、无效契约测试和 8GB 有界 Node 堆 production build
  通过；只重启专用 `/Users/xiexiaoxiong/.pm2-patent-prod` 中的 `patent-web`。3001
  `/test/module-lab` 鉴权跳转正常，5209 health 保持 test/ok；正式 5109 未触碰、未
  promotion。

### 2026-07-28（律师模块五失败只读诊断）

- 水枪调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 的最新模块五并非没有输入：本轮从
  2026-07-28 08:10:30 至 08:32:50 共创建 19 个 `I4_S_SINGLE_REFERENCE`，对应 19 份已有
  `retrieved_document + content_sha256` 的全文。仅 `US20100051848A1` 一篇成功并返回 8 条
  特征披露，另外 18 篇均在 3 次 worker attempt 后以 `LAB_DEPENDENCY_FAILED` 收口；因此
  按 fail-closed 规则没有继续创建 I4-C/I4-I。
- 受限诊断日志中的 54 次模型失败均发生在 GLM 直连：49 次为主线路
  `request_exit=22`、备用线路分别 `probe_timeout` 和 `probe_exit=7`，4 次为三条线路均未
  通过探测，另 1 次主线路 `request_exit=28`。当前直连层只保存 curl exit code，没有保存
  HTTP status 或脱敏错误信封，故只能确定上游返回 HTTP 错误/连接不可用，不能严谨区分限流、
  请求拒绝或其他 4xx/5xx。
- 页面显示 `Failed to fetch` 的直接原因是 `patent-web` 在 08:31:56 被 SIGINT 重启；该时刻
  与页面显示的 1286.7 秒完全吻合。5209 未重启，最后两个后台任务仍继续到 08:32:50 收口。
  客户端 `runBusiness` 的异常分支把 `children` 直接置为 `[]`，随后律师输入卡错误渲染为
  “0 个技术特征、0 篇全文”。这是前端中断恢复/展示缺陷，不代表模块四没有交付全文。
- 本轮按用户要求仅做只读诊断，没有重跑模型、重启服务或修改运行代码。待实现项是：业务
  模块运行谱系可恢复、单次轮询断线不清空已持久化子任务、页面区分“传输中断”和“无输入”，
  以及 GLM 直连保留脱敏 HTTP 状态/错误码并避免 worker 三次重试与页面整批补跑叠加放大耗时。

### 2026-07-28（模块五 19 篇持久化批次与真实回归）

- Portal 新增 `invalidity_test_module5_batches` 与
  `/api/test/invalidity/module5-batches`。模块五在创建子任务前先持久化 1—50 篇全文清单、
  文献到当前 I4-S run 的映射、重试次数、I4-C/I4-I run 和终态；批次绑定 Portal 用户。
  页面 URL 保存 `module5_batch`，浏览器或 `patent-web` 重启后按批次恢复，不再清空为
  “0 篇全文”，也不重新运行模块三、模块四。
- 5209 新增只读 `/v1/lab/investigations/{id}/module5-inputs`，只从同一独立权利要求最近一组
  成功 I3-FETCH/I4-S 持久化谱系恢复真实全文文献，并要求 `retrieved_document` /
  `qualified_evidence` 与内容哈希；不从题录猜测模块五输入。
- GLM 传输审计现在保存脱敏 HTTP status 和 provider error code。I4-S live worker 自身只尝试
  1 次；全批首遍结束后普通瞬时错误只补跑 1 次，只有实测 `error_code=1210` 才允许在本地
  页面载荷降为 2 页后再多 1 次。EPO 文献优先发送已经冻结的本地页面，不把需要 EPO
  授权的远程图片 URL 交给 GLM；远程 URL 只在没有本地页面时兜底。
- 当时版本的 I4-I 已停止把全部 19 篇全文页面展平为一个超大多模态请求：19 篇仍全部各自完成
  I4-S，组合请求曾按增量裁为 D1+最多3篇、每篇最多2张页面。该“最多3篇”口径已由
  2026-08-15 模块十三步法决定取代；当前是先审阅模块九全部 current I4-S 语料，再选择
  D1+最多5篇实际组合文献。I4-I worker 自身仍只尝试1次，批次对 `1210/1261` 载荷拒绝最多补跑1次。
- 真实水枪批次 `4dcc04fb-795b-4b6f-8061-45ad4108f3df` 已完成：清单 19 篇，当前
  I4-S 19/19 succeeded，I4-C succeeded，裁剪后 I4-I succeeded；组合请求实际使用
  4 篇文献、8 张文献页面和 2 张目标专利图，约 80 秒收口。`evidence_complete=false`
  表示组合证据仍有法律事实缺口，不是模块运行失败。历史失败 run 留在审计表中但不冒充
  当前结果。
- 律师页面显示“已提交 19 份全文、19/19 完成”、19 篇逐项表、D1 和组合评价；组合 gap
  改为显示具体特征与 `rationale`，不再重复“未说明的证据缺口”。验证为无效后端
  `437 passed, 7 skipped`，Portal `pnpm ts-check`、定向 ESLint、无效契约和 production
  build 通过。只使用隔离 5201/5209 与 Portal 3001；正式 5109 未触碰、未 promotion。

### 2026-07-29（通用机构检索、可读全文与结构披露）

- 模块三规则改为从目标专利本身提取通用机构模型，而非针对决定书中的具体文献倒推词：
  通读完整专利后识别保护客体、构件角色、拓扑关系、运动/状态、控制关系和技术作用，所有
  元素绑定独立权利要求真实 `feature_id`。发明机构精确线与系统结构/变体线均使用短组合，
  仍按技术/机构锚点、客体、分类号“三类任二”执行；补齐查询不得机械复制完整权利要求。
- 决定书只作隔离盲测：已知 D1/D2/D3 公开号仅存在测试 fixture，运行时 `src/invalidity`
  不得包含、导入或读取；查询及检索结果冻结后，独立 evaluator 才核对各分线累计前 10。
- 模块四新增按内容 SHA 缓存的 `readable-patent-v1`。优先把 EPO OPS description/claims
  XML 标准化为带锚点 JSON/Markdown；否则处理 PDF 全部页面，扫描件在 I3-FETCH 持久化
  后台作业中双路并发 OCR。取得原文件与 `analysis_ready` 分离，未读全的文献不会进入
  模块五。
- 模块五不再逐词机械比对：先理解目标与对比文件的整体机构，再逐特征核查构件角色及
  连接/位置/运动关系。术语不同但结构关系等价可认定披露；只有功能/效果相同不足以确认；
  操作过程仅在该结构不可避免时可支持必然隐含披露。输出新增双方机构摘要、目标结构角色、
  对比文件结构映射和映射依据。
- 历史智慧芽 P020 扫描件 `US20100051848A1` 现场验证从“8 页提取 0 字符”改为 8/8 页
  OCR、约 31,799 字符、约 21 秒。新逻辑第一次 live I2 验证因 GLM 主线路
  `request_timeout`、备用线路 `probe_timeout/probe_exit=7` 未进入生成；诊断同时发现外层
  worker 将同一传输故障重试三次，现已把 live I2 job 固定为单次持久化 attempt，只有已
  返回但语义不合规的 JSON 才在同一 job 内有界重生。后端全量
  `444 passed, 7 skipped`，Portal `pnpm ts-check`、无效契约测试和 production build
  通过；全局 lint 仍有用户既有 `module1/page.tsx` 条件 Hook 错误，本轮未改动该无关文件。
  正式 `5-invalidity-search-prod`/5109 未改动。

### 2026-07-29（模块三多分辨率组合与 D1 前十回归）

- I2 首轮确定性编译现固定生成最大相似长线、客体+发明点、客体+分类号+发明点、
  客体+发明点分类号、客体+两个发明点和系统结构召回线。`SearchQuery` 新增
  `feature_term_groups`，同一 feature 同时保存目标原词、机构角色等价词和动作词；每个
  查询仍按“技术/机构锚点、保护客体、分类号三类任二”守门。
- 历史同源 I2 画像没有 `mechanism_model` 时，不重新等待 GLM，而是仅从目标权利要求已
  冻结的选择性联接/接合等结构动作恢复可审计动作族，输出兼容警告；不得读取决定书、
  候选公开号或对比文件内容。新模型响应仍由 GLM 负责理解发明、绑定 feature、给出机构
  角色及等价表达，确定性代码只负责通用兼容、组合、守门与审计。
- 分类号角色全部为 `general` 时，不再把第一项假装成客体分类；对目标专利已有分类号最多
  5 条逐项分线。I2 计划预算提高到 14，以同时保留 6 类律师组合、分类分线、NPL 和抵触
  申请通道。
- P002 编译器对最大相似长线只保留完整锚点，不添加 `structure` 或单词碎片；短线优先
  保留已声明作用等价词。保护客体只保留完整中英文短语，不再把 `water gun` 裁成
  `water`。最大相似长线 0 命中按设计正常收口，不触发 compact fallback。
- 水枪最终同源 I2 run `e39c2365-63aa-43bc-8469-91e7f438ff7d` 使用
  `same_source_cached_profile`，`network_used=false`，形成 10 条 ordinary 专利分线；
  最终冻结智慧芽前十基准 `20260729-compact-role-anchors-v2` 通过：
  `US20200080816A1` 在“客体+发明点”第 9，在
  `F41B9/00 + 客体 + 发明作用词`第 7。最大相似长线返回 0 条，符合允许零命中的合同。
- 先前 192.5 秒失败已定位为 GLM 主线路 POST 在旧 180 秒请求上限内停滞，备用线路随后
  probe timeout/exit 7；现有 I2 单调用总预算 120 秒、单直连 105 秒，外层 job
  `max_attempts=1`，且同一 source SHA 的成功画像优先免网络重编译，避免相同传输故障反复
  长等。正式 `5-invalidity-search-prod`/5109 未改动、未 promotion。
- 最终验证：无效后端 `447 passed, 7 skipped`；Portal
  `node --import tsx scripts/test-invalidity-portal-contracts.ts`、`pnpm ts-check` 和
  8GB 有界 production build 全部通过。5201/5209 在线，3001 鉴权跳转正常。

### 2026-07-30（论文检索式守门与重复错误修复）

- 调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 的 I3-NPL 输入快照实际只有
  `query="水枪"`。I2 曾把“客体 + 专利分类号”查询复制为论文查询，但分类号只存在专利
  provider 字段中；四个论文 adapter 随后在发网前重复执行同一宽词校验，因而页面错误地
  看起来像 arXiv、OpenAlex、Crossref 和 Web 同时失败。
- NPL 现在有专门守门：实际文本必须同时包含保护客体/类别和至少一个真实技术、机构或作用
  特征，IPC/CPC 不得替代论文关键词。I2 只复用满足该条件的同案查询并去除分类字段；I0 与
  Portal 保留 `text/subject_terms/feature_terms`；复合 provider 在 fan-out 前统一校验
  一次。
- 同案历史成功 I2 画像按新规则只读重编译后生成 `水枪 AND 解锁机构`；该实际查询直连
  四来源验证时 arXiv、OpenAlex、Crossref 均完成请求且无 `QueryValidationError`。公共
  Web 实际返回 DuckDuckGo 图片验证码，已补充识别为访问受限并保持 partial。检查脚本首条 run
  `0cd0aba0-0542-4767-9b25-23c1406fb711` 因漏传 claim 业务输入被合同立即拒绝；随后复用
  上一成功 run 的完整真实输入重新提交 `387da09e-7b71-407d-b9fe-663fcd219982`；它在既有
  模块五任务之后正常收口为 `succeeded`，持久化的 NPL 查询为 `水枪 AND 解锁机构` 且分类
  锚点为空。期间未取消或插队用户任务。
- 重载后的真实 I3-NPL run `d886306c-cb9a-41b6-a1e4-09f232521d56` 单次成功并返回 6 条
  lead；arXiv、OpenAlex、Crossref 成功，Web 只留下明确的验证码/访问受限错误，四来源均
  不再出现 `QueryValidationError`。
- 律师页面兼容历史运行：如果旧快照仍保存四条相同 `QueryValidationError`，普通视图只
  显示一次“共同检索式缺少客体+技术/机构特征、请求未发往四个来源”的中文解释；不会再
  把共同输入错误显示成四个外部数据库同时宕机。新的真实分源故障仍逐源保留。
- 后端定向测试 `56 passed`、全量 `450 passed, 7 skipped`；Portal `pnpm ts-check`、
  模块实验室定向 ESLint、无效契约测试和 8GB production build 通过。隔离测试栈
  5201/5209 与 Portal 3001 已重载并在线，正式 5109 未触碰、未 promotion。

### 2026-07-30（模块四取文前候选清理）

- 产品合同新增 `I3-F/I3_CANDIDATE_FILTER`：每条实际检索的供应商原始前 10 条仍完整冻结，
  但取全文前先按稳定文献 ID、申请号、公开文本主号/kind code 和可靠同族合并。原始命中、
  查询排名、合并成员、代表文献和排除理由均可审计。
- 唯一代表由 GLM-4.6V 按 I2 已形成的保护客体、发明点、机构模型和技术特征做批量技术语境
  分类；模型不判断日期、技术披露、新颖性、创造性或无效结论。强目标锚点、题录不足、
  模型漏项/超时/失败、存在相关介质/能量、部件作用、操作关系或可类比机理时都必须保留。
  只有五项排除检查完整为 false 且模型高置信时才允许标记“明显无关”。
- `/test/module-lab` 的模块四只把后端 `fetch_candidates` 全部送入 I3-FETCH，不再作隐藏
  前 2/前 5 截断；普通视图同时显示原始数、合并数、排除数、实际取文数和每条候选状态。
  浏览器只提交同案持久化 search/query-plan run ID，不能伪造文献或筛选结论。
- GLM 筛选单次直连上限 90 秒、总上限 120 秒，失败自动高召回收口；输出复制精简题录，
  大型 provider provenance 仍引用原检索 run，避免重复存储。正式
  `5-invalidity-search-prod`/5109 未修改、未 promotion。
- 水枪真实小批先以 run `d80208a4-4895-4188-bc3d-7b12d0eeaab3` 验证
  `WO2019112939A2/A3/A4` 可合为 1 个代表且模型可排除冰箱、光纤连接器等明显无关组。
  后续增加目标技术词高召回守门并把 `connect/coupling` 等通用连接词排除在该守门之外；
  定向测试确认水流相关资料必须保留，而孤立“coupling”不能保护跨领域光学连接器。
- 重载后最终小批 run `97a7302e-e3e0-415a-aabc-9205f90fb59e` 为 10 原始、2 重复、8 个
  唯一代表；本次 GLM 直连传输失败，模块在约 11 秒内按高召回原则保留全部 8 个代表，
  证明失败只减少节省的取文请求，不造成静默漏检或长时间等待。
- 验证为后端 `463 passed, 7 skipped`；Portal `pnpm ts-check`、定向 ESLint、无效合同测试
  和 8GB production build 通过。隔离 5201/5209 已重载，专用 PM2 只重启 `patent-web`，
  3001 鉴权跳转与 5209 test health 正常；正式 5109 未触碰、未 promotion。
- 后续完成候选清理的安全/重放验收：live I3-FETCH 只接受同案、同独立权利要求的
  `candidate_filter_run_id + candidate_id`，后端从成功 I3-F 持久化输出回读候选；浏览器
  回传 `document/provider/stage/hash` 会在 provider 前被拒绝。I3-F 输出新增合同、身份和
  语义规则版本、代表选择理由、输入哈希、决定哈希、模型 prompt 哈希和总审计哈希；
  可靠 family ID 遇同语言题名明显冲突时不合并。
- 本轮验证更新为后端 `465 passed, 7 skipped`，Portal 类型检查、定向 ESLint、无效契约
  测试和 8GB production build通过。当前律师模块四已完成“全部检索后统一 I3-F、再对全部
  保留代表取文”的闭环；完整 I0 的历史 `ConfiguredEvidenceGateway` 仍在单条查询内发现后
  直接取文，尚未全局切换。该边界必须继续显式记录，不能将测试模块验收等同于正式 I0 或
  5109 已完成。隔离 5201/5209 已重载并 health=ok；专用 PM2_HOME 只重启 `patent-web`，
  3001 登录页为 200、模块实验室匿名访问正确 307 到登录页；正式 5109 本轮未修改、
  未 promotion。

### 2026-07-30（模块三发明锚点与机构等价词来源修复）

- 水枪最新 I2 run `d248496a-f954-4d10-af2e-7b6858acfff9` 已正确识别“位置选择性联接装置”
  及“本体在中间位置时选择性联接阀杆”，但历史兼容编译器把“选择性联接/接合”无依据地
  扩展为“锁定机构/解锁机构”，再因短词优先让“解锁机构”取代原始发明锚点；该问题属于
  确定性后处理和历史画像污染，不是本轮 GLM 理解结果。
- I2 主发明锚点现在只能来自目标独立权利要求或同案发明概念中可逐字追溯的原始表述；
  机构角色词和动作等价词只能作为同一动作族内的 OR 扩展，不得替代主锚点。联接/接合、
  锁定、解锁、脱开/分离分别视为不同动作族，除非目标专利原文明确出现，否则不得跨族
  推导。
- 同源缓存即使已有 `mechanism_model` 也会先清洗与原始构件/限定不相容的历史等价词；
  Portal 的 P002 编译器另设一次来源与动作族守门，防止旧持久化结果把“锁定/解锁”继续
  发送给智慧芽。
- 模型仍负责通读专利、识别发明点、构件关系和候选同义表达；确定性代码负责原文溯源、
  同动作族校验、组合、范围选择和合同守门。不得使用针对某一公开号、决定书或目标答案的
  专用关键词表。
- 实际完整输入重跑 `8b424a58-4d88-448d-b476-67a2b501a1e5` 单次成功，
  `actual_provider=same_source_cached_profile`、`network_used=false`。短线已改为
  `水枪 AND 中间位置、选择性联接`，双锚点线为
  `水枪 AND 中间位置、选择性联接 AND 位置选择性联接装置`；全部 query、
  `feature_term_groups` 和 NPL 分线均不再包含“锁定机构/解锁机构”。
- 最终验证：无效后端 `465 passed, 7 skipped`；Portal 类型检查、无效契约、定向 ESLint
  和 8GB production build 全部通过。隔离 5201/5209 已重载并 health=ok，专用
  `/Users/xiexiaoxiong/.pm2-patent-prod` 只重启 `patent-web`，3001 鉴权跳转正常；
  正式 `5-invalidity-search-prod`/5109 未修改、未 promotion。

### 2026-07-30（模块三原子关键词与位置限定拆分）

- 用户进一步指出上一轮的`中间位置、选择性联接`仍不是一个合法检索词。I2 可执行
  feature anchor 现限定为单一、可独立检索的技术概念；说明句或用顿号、逗号、分号并列
  的概念必须先拆分。`位置选择性联接装置`作为单一完整技术术语可以保留，
  `中间位置`与`选择性联接`不得写进同一字符串或同一 OR 组。
- 短线优先从目标原文和已通过来源守门的机构作用词中选择高信息动作/结构词；纯位置词降级。
  如果同一发明关系还需要位置限定，位置词只能作为独立 AND 组加入。历史同源画像重编译
  和新 GLM 输出均经过同一原子拆分、同概念 OR 分组和初始表达式规范化。
- Portal 的 P002 编译器增加第二层原子化：旧快照即使保存
  `中间位置、选择性联接`，也会拆分并优先锚定`选择性联接`；`中间位置`不会作为其同义词
  放入同一 OR 组。动作词不会再自动附加无来源的`结构`碎片。
- 水枪同案最终真实重跑 `8ce8091b-824a-47c2-b600-2495965250e4` 单次成功，
  `actual_provider=same_source_cached_profile`、`network_used=false`。短线实际为
  `水枪 AND 选择性联接`；双关键词线为
  `水枪 AND 选择性联接 AND 中间位置`；最大相似长线仍保留完整术语
  `位置选择性联接装置`。上一节 run 中的`中间位置、选择性联接`结果已被本节规则取代。
- 最终验证：无效后端 `465 passed, 7 skipped`；Portal `pnpm ts-check`、无效契约、
  本次文件定向 ESLint 和 8GB production build 通过。全量 Portal lint 仍被本次范围外
  `src/app/module1/page.tsx` 的既有条件 Hook 错误阻塞。隔离 5201/5209 与 Portal 3001
  已重载且健康；正式 `5-invalidity-search-prod`/5109 未修改、未 promotion。

### 2026-07-31（模块五整体机构结构等同与有界复核）

- I4-S 从孤立词面对照改为整体机构映射：先识别目标特征在完整方案中的结构角色，再比较
  对比文件的介质/能量路径、连接拓扑、运动/状态和控制关系。术语、外形、构件数量或拆分方式
  不同不自动否定，一个集成构件可以映射多个目标特征；映射依据区分逐字、角色关系、结构
  等同、工作过程必然隐含、仅功能相似、未找到对应和待核对。
- 必然隐含披露必须保存至少三步物理必要性链并排除合理替代结构。任意 reservoir 不会仅因
  储液自动成为压力罐，泵活塞也不会仅因移动自动成为阀杆；但储液—加压—排出组合、阀芯/
  压柱集成移动件、阀体内部流路、落座/离座与开闭状态均可在全文证据支持时按整体作用映射。
  `not_disclosed` 必须指向实质连接、流路、运动或控制差异，仅未出现目标词或未采用独立零件
  必须降为待核对。
- `structural-disclosure-v7` 在首轮承认候选结构却仍按名称否定，或已形成足够正向映射而仍有
  开放特征时，条件触发一次有界整体复核；最多复核 8 项开放特征，目标图最多 1 张、文献图
  最多 2 张，总预算 60 秒。第二遍只能补足正向结构映射或降低过度否定，不能从裁剪上下文
  新建明确否定；漏答或传输失败记 `model_error`，不得进入新颖性、D1 或创造性组合。
- 结构证据必须回溯冻结全文。OCR 仅在局部序列高度相似且多数构件编号锚点一致时允许有界
  模糊匹配；跨机构同编号文本仍拒绝。prompt/rule、目标 SHA、limitation SHA、文献版本与
  content SHA 全部进入输入绑定；模块五存在 batch 时严格按 `module5_batch_id` 隔离 I4-S、
  I4-C、I4-I，旧版或其他批次结果不得混入。
- Portal 模块五普通视图增加映射依据、目标角色、对比结构、结构证据、集成映射、复核状态/
  遍数/规则版本；详细审计折叠显示。结构复核 `model_error` 不算成功，批次有界补跑后仍失败
  即 partial/failed，并阻止 I4-C/I4-I。历史报告兼容 disclosure 顶层和 `analysis` 嵌套字段，
  替代路径提示只用于必然隐含披露。
- 人工复核导入路径同样 fail-closed：child I4-S 结构复核失败时，在写 comparison 和
  `complete_human_review_apply` 前抛可重试错误，人工动作不标 applied，重算要求不清除。
  终审无 HIGH/MEDIUM；后端全量 `491 passed, 7 skipped`、compileall、Portal 类型检查、
  定向 ESLint 和无效契约测试通过。
- CN203880100U 最终 v7 实跑首次 run `50d4dfb9-5dd6-4510-8308-248a2f6d3c0b` 因 GLM
  临时网络超时失败；唯一补跑 `04f667bb-a16c-438e-a6d6-d2609fa7ea2e` 完成首轮，但第二遍
  结构复核仍返回 `model_error/glm_direct_transport_error`。系统没有把首轮过严否定冒充
  可用结论，该结果不会进入实体聚合；待 GLM 稳定后仍需定点复跑 CN203880100U 和
  US20050173559A1 才能完成真实语义验收。隔离 5201/5209 已重载并 health=ok，Portal 3001
  模块实验室匿名访问正确 307 到登录页；正式 5109 未修改、未 promotion。

### 2026-07-31（水枪决定书对比文件原文夹具）

- 决定书 `无效宣告请求审查决定书_2021227533927.pdf` 的证据身份经文字抽取和第 5–6 页
  视觉复核确认：纳入证据 1–4、7–17 共 15 篇现有技术专利；证据 5（另一决定）和证据 6
  （专利及评价报告）排除。没有复制决定书后续的技术特征对应、差异评价、组合理由或结论。
- 15 份 PDF 已保存到无效测试目录的隔离 blind benchmark 子目录，并逐份校验文件签名、
  PyMuPDF 可解析页数、字节数和 SHA-256，且人工查看全部首页核对公开号、题名和代表附图。
  取文来源为 EPO OPS 5 份、公开专利 PDF 镜像 9 份、智慧芽 P002 精确身份 + P020 1 份。
- 新增 source-only manifest：明确 `conclusions_imported=false`、不含预期 disclosure/法律
  结论，只允许作为模块五重新运行项目自身全文/OCR/附图读取与 I4-S 的输入。公开号和文件
  不得进入 I2 prompt、同义词、查询生成、候选筛选或生产运行时；回归会扫描
  `src/invalidity` 防止泄漏。
- 按用户进一步限定，输入目录不复制决定书 PDF，也已移除离线分析 oracle/人工结论评估器；
  模块五内置样例只保留目标专利 `CN216205649U`、15 份对比文件和非结论性 manifest。
- 定向回归 `tests/test_blind_retrieval_benchmark.py` 为 `4 passed`，无效后端全量回归为
  `492 passed, 7 skipped`。本轮没有启动模块五实体分析、没有生成或移植任何比对结论，
  也没有修改/重启正式 5109。

### 2026-07-31（决定书盲测检索组合与模块五断点运行）

- 新增五案目标专利/对比文件 source-only 盲测工具链：I2 只读目标专利并先冻结，Portal
  编译器再逐条形成 P002 查询，供应商每条前 10 独立冻结后才允许加载人工检索文献清单；
  I4-S 运行器只读取目标和对比文件 PDF，不读取决定书或结论。
- 水枪同案复用成功 I2 画像做无模型重编译。结构召回改为复合构件部件化并保留窄同义族：
  `压力罐`可形成`罐/储液器/tank/reservoir`，`带有阀导管的阀`可形成`阀/valve`；同时
  生成系统组合、单构件及有界控制链结构分线。分类角色未知时只保留首尾两个代表分类，
  不再用五条相同发明点查询占满预算。规则不含决定书公开号或答案词。
- P002 评估器修正为“任一条实际查询自己的前 10”，不再把相同 query role 的多条结果
  串联成累计排名。冻结 v8 中 `US5799827A` 已在`水枪 + 阀`分线第 4；另外两篇尚未进入
  前 10，不能宣称水枪三篇检索验收已通过。目标说明书明确引用的专利同族应作为后续通用
  引证检索来源实现，而不是继续根据决定书倒推关键词。
- 水枪模块五原文运行器已改为每篇完成立即原子写盘、按 PDF SHA-256 跳过已完成项，可在
  中断后续跑。首次真实盲测已冻结 `CN101970127A` 的 I4-S 输出；下一篇因 GLM 三条直连
  路由 request/probe timeout 失败，未把失败冒充结果。该首篇第二遍结构复核也因相同传输
  错误未完成，不能进入实体聚合。
- 本轮定向验证：后端分析/盲测/模块一/API worker 共 `91 passed`，Portal 无效合同脚本
  通过；5201/5209 已重载且 health=ok，Portal `patent-web` 已重启。正式 5109 未修改、
  未 promotion。

### 2026-07-31（水枪原文夹具终检与盲测号码等同）

- 水枪模块五内置样例终检确认仍仅包含目标专利 `CN216205649U` 和决定书证据 1–4、7–17
  的 15 份原始专利 PDF；证据 5、6 继续排除，manifest 不含特征比对、组合理由或法律结论。
- 冻结后的 P002 v16 已实际返回证据 3 的完整公开号 `WO1995000221A1`。盲测验收清单把
  决定书记载的旧式号码 `WO9500221A1` 与该完整号码列为同一文献的可接受身份；该别名只
  存在于运行后验收数据，不进入 I2/I4-S 运行时代码或 prompt。
- 原文夹具定向测试 `4 passed`：逐份验证 PDF 签名、页数、字节数、SHA-256，并验证
  `conclusions_imported=false`、`expected_disclosures=null` 和运行时代码无答案标识泄漏。

### 2026-08-01（水枪决定书对比文件下载复核）

- 按用户“只下载原始对比文件、不移植比对结论”的限定，再次从决定书证据清单页核对范围：
  模块五内置示例仅纳入证据 1–4、7–17 共 15 篇现有技术专利；证据 5（另一份无效决定）
  和证据 6（专利及评价报告）继续排除。
- 15 份文件已位于
  `5-invalidity-search-test/tests/fixtures/blind_benchmarks/water_gun_decision_2021227533927/source_documents/`；
  本轮重新核验全部为 PDF，文件集合、页数、字节数和 SHA-256 与 source-only manifest 一致。
  `conclusions_imported=false`、`comparison_reasoning_imported=false`、
  `expected_disclosures=null`、`expected_legal_conclusion=null` 的隔离边界保持不变。
- 定向回归 `tests/test_blind_retrieval_benchmark.py` 为 `4 passed`。本轮没有运行模块五比对、
  没有读取或保存决定书后续的技术特征评价和最终结论，也没有修改/重启正式 5109。

### 2026-08-01（五案内置盲测、模块三通用检索与模块五结构等同）

- 五个真实无效案件统一采用盲测隔离：模块三只读取目标专利形成并冻结查询，再调用真实
  P002 并按每条查询冻结前 10；模块五只读取目标专利和已取得的对比文件原文。决定书中的
  比对理由、组合评价和结论不得进入 prompt、运行时代码、检索词或模块五输入。
- 水枪案 15 篇 source-only PDF 继续作为模块五内置文件；五案共 35 篇对比文件、5 篇目标
  专利已形成隔离夹具。冻结基线已经完成 54 个“对比文件 × 独立权利要求”单文献比对单元，
  不把多篇文献拼接后判断，也不把目标专利自身当作对比证据。
- 模块三改为先从完整技术限定提取“客体、具体构件、结构关系、作用”后再原子化关键词；
  `适配/套设/连接/设置`等无客体的弱关系词不得单独成为锚点。`套圈/定位卡槽/圆形凸台/耳挂`
  等仅使用通用结构别名扩检，别名只扩大候选召回，模块五仍须回到全文证明结构。检索结果如实
  冻结，未命中人工文献时保留为未命中，不按决定书答案继续定向调词。
- 模块五规则升级为 `structural-disclosure-v12`：按构件在整体方案中的位置、连接、运动和作用
  判断结构等同，不要求名称或句式相同；材料/化学方案另按成分身份、骨架、含量、工艺条件和
  性能作用分别核验，数值范围重叠不得覆盖材料身份或结构差异。水枪代表复测已能把可平移阀体、
  开闭位置和轴线作用识别为阀杆/阀塞对应结构；无原文支持的压力罐及连接关系仍不得猜测。
- Portal 模块实验室新增“五个真实无效案件的内置盲测”律师视图，可直接选择案件查看模块三
  的目标专利输入/冻结查询输出，或模块五的独立权利要求输入/逐篇全文比对输出；接口不返回
  决定书结论。因浏览器当前停留在登录页，本轮完成类型与静态检查，登录后的最终视觉点击仍待验证。
- 最终定向回归为 `165 passed`，Portal `pnpm ts-check` 和定向 ESLint 通过；测试栈 5201/5209
  已重载。正式 5109 未修改、未启动、未 promotion。

### 2026-08-01（盲测冻结版本绑定与水枪十五篇完整运行）

- 模块三通用检索编译进一步保留同一限定中的不同机构构件，并新增“具体构件 + 关系动作”与
  “客体 + 动作链”的目标原文分线；查询预算优先保留分类、题名/摘要结构线、目标接口动作线
  和最多三条不同目标扩展线。关系动作使用可独立检索的原子词，不能让泛化的“连接/控制”或
  决定书答案取代目标专利中的具体构件。
- 冻结检索结果如实保留：无线耳机案 `CN216649931U` 在题名/摘要分线第 9 位；燃气灶案命中
  `CN106091032A` 第 1 位及第三篇的同族 `US20150122134A1` 第 9 位，旧称“燃气炉”对应的
  `CN2519162Y` 尚未进入前 10；电池案权利要求 1 命中 `CN103633269A` 第 1 位。后续不得为
  补齐未命中文献向运行时注入公开号或决定书用语。GLM 新画像连续三次 180 秒超时均已取消，
  未继续无期限等待，也未把失败输出冻结为可用计划。
- 水枪案模块五 `structural-disclosure-v12` 已按“一项独立权利要求 × 一篇原始全文”完成并
  冻结 15/15 篇；重试历史保留但成功文献数按 PDF 身份去重。该冻结结果只由目标专利及 15 份
  source-only PDF 生成，`oracle_loaded=false`，没有读取决定书的特征对应或结论。
- Portal 真实案件内置视图改为严格白名单绑定每案明确的 I2/I4-S 冻结版本，不使用目录自动
  发现；水枪绑定计划 `20260801-five-case-v46`、比对 `20260801-five-case-v47`，并在律师页面
  显示实际冻结版本。这样即使以后另建含人工评估的目录，也不会被测试运行时误读。
- 后端全量回归更新为 `530 passed, 7 skipped`。本轮仍未修改、重启或 promotion 正式
  `5-invalidity-search-prod`/5109。

### 2026-08-01（决定书后验评估隔离与模块五 v17）

- 五案决定书人工判断新增为严格隔离的后验评估数据，目录为
  `5-invalidity-search-test/tests/evaluation_oracles/decision_i4s/`。运行时
  `src/invalidity`、I2 计划生成、P002 检索和 I4-S 盲测运行器均不得读取该目录；只有
  `scripts/evaluate_decision_i4s.py` 在 `oracle_loaded=false` 的结果冻结后读取并评分。
  决定书未实际评价某项独立权利要求/某篇文献时明确记为不可评分，不从请求人陈述反推答案。
- 模块五升级到 `structural-disclosure-v17`：结构等同按完整拓扑和作用判断；已用可回溯
  原文确认“内侧轴形件位于外侧环形件并形成旋转副”时，确定性同步其轴/凸台、环/套圈和
  外侧包围关系，但不传播定位、间距、数量、方向或角度限定。完整全文/附图核验后明确缺失
  的结构显示 `not_disclosed`，不再写成 `uncertain`。
- 同一文献中分散记载的结构链可以合并：例如一段公开电路板电连接内部电子元器件，其他段
  公开电子元器件包括喇叭和电池，可分别引用后确认完整电连接链；不要求全部内容位于同一句。
  模型返回大型 JSON 偶发漏逗号时，仅进行有界标点修复，不改键、值或技术结论。
- 无线耳机 `CN216649931U` 使用目标专利生成的 v62 限制集合运行 v17 盲测，冻结于
  `20260801-five-case-v68`，人工基准后验评分为 `8/8`；其模块五输入从未包含决定书理由或
  结论。Portal 内置案例白名单已绑定计划 v62 / 比对 v68，不自动发现评估目录。
- 本轮后端全量回归 `549 passed, 7 skipped`，Portal `pnpm ts-check` 与定向 ESLint 通过；
  隔离测试 5201/5209 已重载。正式 `5-invalidity-search-prod`/5109 未修改、未重启、未 promotion。

### 2026-08-01（模块五材料、分散证据与关系链盲测修正）

- 模块五继续遵守决定书后验隔离：本轮真实运行只读取目标专利和 source-only 对比文件 PDF，
  `oracle_loaded=false`；决定书理由和结论不进入 prompt、规则或运行输入。水枪内置示例仍仅含
  已下载的目标专利、15 份证据专利和非结论性 manifest。
- I4-S 规则迭代至 `structural-disclosure-v28`，prompt 为 `i4-s-structural-v19`。材料身份/
  化学形态差异不得被重叠的用量区间覆盖；材料与含量同时属于一个限定时，肯定披露证据必须
  同时覆盖材料身份和对应数值。多个可选化学成员的清单不得误当作数值组成范围。
- 所有状态的 `evidence_quote` 统一执行对比文件来源回溯。模型引用目标专利或生成不可回溯
  文本时清空引文和原位置；完整的未披露复核仍可使用全文复核位置。模型在原文前增加 CN 号、
  段落号和引号包装时，只在去包装后的内部文字可独立回溯源文献时保留内部原文。
- 复合关系证据允许同一文献的多段可回溯原文共同证明。模型实际进行了结构角色映射却误把
  `mapping_basis` 标为 `literal` 时，可校正为 `role_and_relation`，但仍必须通过多构件及
  关系守门；只有上位类别或整体功能的证据不会因此放行。电池供电、电子控制组件和驱动马达
  的控制链按实际供电/连接/驱动关系核验，不要求与目标专利零件名称逐字相同。
- 新增通用实体差异维度：材料类型、转速、套设/嵌套、切面配合等。相同参数范围确无交集、
  完整检查后确认没有套设拓扑或配合切面时可确定为 `not_disclosed`；仍禁止仅凭目标术语
  缺失作否定。
- 电池浆料 claim 1 对 `CN103633269A` 的 v20 盲测运行
  `adf77670-41ea-4526-bd20-b98298447744` 已把关键“碱溶性高分子乳液增稠剂”稳定识别为
  未披露，并移除目标专利污染引文。claim 4 对 `CN103915594A` 的 v21 运行
  `f75ceeef-cdb2-4c8f-b55c-92e8e20f5fa1` 完成；短暂轮询断线在同一持久化 run 内恢复，
  未重复模型调用。
- 燃气灶 claim 1 已从目标专利重新生成 7 项关系型限定计划（run
  `bbc9d7a3-3c7e-46c7-aa5c-c46bc260369a`），不再沿用 12 个孤立名词的旧拆分。对
  `CN2519162Y` 的 v26 盲测 run `a8df762e-5448-44ea-aa3d-d0396144c1d9` 已确认开关轴连接和
  传动链；其暴露的供电控制链、套设和切面差异门槛已在 v27 修正，尚待下一轮真实复跑验证。
- blind I4-S 运行器新增明确的不可冻结原因和版本漂移保护：历史旧版本任务跳过并留痕，
  本轮新任务若服务返回不同 `analysis_rule_version` 立即停止，不再把版本不匹配误报为结构
  复核失败或继续消耗模型重试。材料名称/具体成员与用量分散在同一文献不同权利要求段落时，
  可在原文可回溯且数值范围相交时共同证明；模型在逐字引文后附加的公开号/段落括注会被
  分离，只有去除括注后的正文能够回溯时才保留。
- 电池浆料 claim 1 对 `CN103633269A` 的 v26 冻结验证已把水性分散剂、水性乳胶和陶瓷浆料
  三类公开稳定识别；模型虚构的水性润湿剂引文被来源守门清除，没有误判为公开。材料类型
  与化学身份使用并列句式描述差异时，v28 也视为实质材料差异，待下一轮无版本漂移实跑验证。
- 后端全量回归为 `565 passed, 7 skipped`（另有 37 条第三方/弃用 warning）。测试 5201/5209
  已重载，正式 `5-invalidity-search-prod`/5109 未修改、未重启、未 promotion。模块三真实
  P002 前十盲测仍受供应商余额错误 `67200005` 阻塞，恢复余额前不得声称五案检索验收完成。
- 本轮再次核验水枪模块五 source-only 内置示例：目标专利 `CN216205649U` 与证据 1–4、7–17
  共 15 份对比文件 PDF 均在固定目录，文件签名、页数、大小和 SHA-256 回归 `4 passed`；
  manifest 继续固定 `conclusions_imported=false`、`comparison_reasoning_imported=false`，
  未增加决定书特征对应、区别判断、组合理由或最终结论。

### 2026-08-01（水枪对比文件下载任务交付复核）

- 再次按用户限定收口为“只下载并提供原始对比文件”：证据 1–4、7–17 共 15 份专利 PDF
  已作为水枪专利模块五 source-only 内置输入；证据 5、6 不属于本次现有技术原文集合，未纳入。
- `tests/test_blind_retrieval_benchmark.py` 本轮复跑为 `5 passed`，逐份验证 PDF 文件签名、页数、
  字节数、SHA-256、文件集合及 manifest 的无结论边界。没有从决定书导入特征对应、比对理由、
  文献组合意见或法律结论，本轮也没有运行新的模块五实体比对或触碰正式 5109。

### 2026-08-01（水枪模块五原文输入再次验收）

- 决定书证据清单再次确认：模块五水枪内置案例只使用证据 1–4、7–17 的 15 份现有技术专利
  原文；证据 5（另一份无效决定）与证据 6（相关专利及评价报告）不作为本次对比文件输入。
- 15 份 PDF、目标专利和 source-only manifest 已位于固定 blind benchmark 目录；定向回归
  `tests/test_blind_retrieval_benchmark.py` 为 `6 passed`，验证文件集合、PDF 可解析性、页数、
  字节数、SHA-256 以及 `conclusions_imported=false`、`comparison_reasoning_imported=false`。
- 本次仅验收下载文件和模块五内置输入绑定，没有运行新的实体比对，没有把决定书的技术特征
  对应、区别判断、组合理由或最终结论导入系统，也没有修改或重启正式 5109。

### 2026-08-01（模块五结构作用等同与分散证据链）

- I4-S 当前测试规则为 `structural-disclosure-v44`。比对不要求目标名称与对比文件名称逐字
  相同；必须结合全文和附图判断构件在整体方案中的连接拓扑、运动/状态、介质或能量路径及
  技术作用。只有用途相似而没有对应结构关系时仍不得确认披露。
- 同一文献的分散段落可以共同证明复合关系。例如一段公开电路板与内部电子元器件电连接，
  其他段落分别公开电子元器件包括喇叭和电池时，确定性来源守门会在三段原文均可回溯后形成
  完整连接链；只有“电子元器件”上位词或缺少任一成员/关系时不得放行。
- 完整全文及附图已经核查且明确没有出音孔/开口/声学通道、定位构件或相应位置关系时，应
  输出 `not_disclosed`，不再以 `uncertain` 掩盖已完成的否定核验；仅仅没有出现目标零件名称
  仍不足以证明未披露。
- 流体阀增加完整机构一致性守门：同一来源必须同时证明入口—阀座—出口流路、可移动闭合
  件、封紧阀座的关闭状态及离开阀座后的通流状态，之后才可把阀座/内部流路、阀芯/压柱等
  集成结构映射到阀导管、阀杆、阀塞和开闭位置；轴线、位置选择性联接和中间位置不传播。
- 水枪真实案件 `CN203880100U` 定点 run `8b9f05c5-6443-4fd1-9bc5-b5cbde4d0512`
  已验证上述规则：阀导管/进出口/阀座、供液连接管、压柱+阀芯对应的阀杆/阀塞、开闭位置
  和手动阀驱动机构均为 `direct_and_unambiguous/structural_equivalent`；第一纵向轴线及
  位置选择性联接仍为 `not_disclosed`。OCR 相邻块只在同一原文有界窗口内合并并保持回溯。
- source-only 盲测后验评分：光引发剂 claim 8 对 `CN107272336A` 为 `2/2`；无线耳机
  claim 1 对 `CN216649931U` 为 `8/8`。运行输入均为目标专利与对比文件原文，决定书人工
  映射只在冻结后由独立评估脚本读取。
- 后端定向联合回归 `223 passed`，Portal `pnpm ts-check` 及无效检索契约脚本通过；隔离
  测试服务 5201/5209 已重载，正式 5109 未修改、未重启、未 promotion。五案真实 P002
  前十检索仍受智慧芽余额错误 `67200005` 阻塞，不能宣称模块三真实检索验收完成。

### 2026-08-01（模块五配方质量份与重量占比）

- I4-S 测试规则升级为 `structural-disclosure-v45`。同一配方组分的数值不能脱离计量对象机械
  比较；仅当对比文件明确写明该组分“在/占整个组合物中的用量”为质量份或重量份、目标限定
  是该组分占组合物重量的百分比、两区间相交、原文可回溯且不存在“相对于树脂/溶剂等另一
  基准”时，才按配方含量的等价表达确认交集被公开。
- 新增正反例：`1-5质量份` 的光引发剂在整个感光性树脂组合物中的用量可覆盖目标
  `0.5-10%` 的交集；“相对于树脂100质量份”的用量不得换算成整个组合物重量占比。后端
  全量回归为 `598 passed, 7 skipped`。
- 隔离测试服务 5201/5209 的重载因本机提权额度限制未执行成功，因此运行中服务尚未确认加载
  v45；正式 5109 未修改、未重启、未 promotion。水枪 source-only 夹具的 15 份 PDF 与
  manifest 已再次逐份核对，仍不含决定书比对理由或法律结论。

### 2026-08-01（模块五材料引文来源精确守门）

- 模块五对整体机构、作用和必然隐含关系的判断继续允许名称不同，但证据来源标准不放宽。
  材料、配方和数值限定的引文现要求在当前对比文件中去空格/标点后精确出现，不再使用机械
  长段落的 OCR 模糊匹配，防止把“水性分散剂 0.1-2%”误当成“水性润湿剂 0.1-2%”。
- 新增上述同数值、不同材料名称的污染回归。当前工作区规则已并发升级到
  `structural-disclosure-v44`，后端全量 `593 passed, 7 skipped`；Portal `pnpm ts-check` 和
  无效契约脚本通过。
- `CN103633269A` 的 v42 source-only 定点实跑中，水性乳胶保持 `explicit`，水性润湿剂为
  `not_disclosed`，且错误润湿剂引文已清空。六篇电池浆料文献的同版本全量冻结尚未完成：
  运行期间规则连续变化，blind runner 正确拒绝 v39/v40/v41/v42/v44 混合结果。因此律师页
  没有切换到 v91，仍使用既有稳定冻结；待规则稳定后需按单一版本重跑、冻结后评分再绑定。
- 本轮只重载隔离测试 5201/5209，正式 5109 未修改、未重启、未 promotion。模块三五案 P002
  前十真实回归仍受智慧芽余额错误 `67200005` 阻塞。

### 2026-08-01（五案同版本后验审计与独立权利要求补齐）

- 决定书后验评估器增加严格冻结守门：同一 claim 的决定书文献必须全部有可用 I4-S 输出，
  且每篇最新输出必须属于同一个 `analysis_rule_version`；可用 `--expected-rule-version` 锁定
  当前版本。混合 v41/v42/v44 或缺少任一文献时直接拒绝评分，不能再以局部满分冒充案例通过。
- `structural-disclosure-v44` 的 source-only 盲测已确认：电池浆料 claim 1 六篇完整且后验
  `7/7`，燃气灶 claim 1 三篇 `6/6`、claim 10 三篇 `1/1`，无线耳机 claim 1 六篇
  `8/8`，光引发剂 claim 8 五篇 `2/2`。上述运行均为 `oracle_loaded=false`，决定书只在冻结后
  由独立评估脚本读取。
- 电池浆料 claim 4 已按 v44 对六篇文献逐篇完成，但决定书没有逐项评价其加工步骤，因此
  只证明模块五输入完整，不显示或宣称决定书一致性满分。水枪 claim 1 同样没有决定书逐项
  映射，只能作为检索召回和 source-only 输出案例，不能作为实体判断一致性评分案例。
- 律师内置案例已把无线耳机切换至 v93，把电池浆料和燃气灶切换至 v91；燃气灶计划回到
  含 claim 1、10 的 v46，修复旧 v80 只展示 claim 1 的不完整绑定。光引发剂仍暂用旧稳定
  冻结，claim 1、9 的 v44 真实补跑因本轮本机服务调用授权额度被拒绝而尚未完成。
- 模块三最新真实 P002 复核仍返回 `67200005 Insufficient balance`。历史燃气灶 v59 中 11/12
  条查询曾把该错误误冻结为空结果，现行 runner 已禁止这种行为；该历史结果不得用作真实
  前十召回证据。待余额恢复后必须重新逐案、逐条检索式验证 D1 是否进入各自前十。
- 光引发剂案例暴露出通用 I2 漏洞：公式型权利要求若只写“具有下式的化合物”，不得把
  `下式`、`化合物`或仅`光引发剂化合物`当作唯一发明锚点。当前规则会只从目标专利全文中
  与光引发剂/引发剂语境共同出现的化学名称提取最多两个罕见结构片段，并生成“保护客体 +
  结构片段”的全文线；例如目标自身可形成`光引发剂 AND N-吗啉基`。该提取为形态规则，
  不读取决定书或对比文件，也不维护具体案件词表。v95 target-only 离线重编译已生成该线，
  待 P002 余额恢复后验证是否把人工 D1 带入该检索式自己的前十。
- 当前工作区 I4-S 已另行演进到 `structural-disclosure-v45`；律师页新绑定的 v44 是已完成、
  同版本、不可变的稳定冻结，不等同于宣称尚未补跑的 v45 已通过。
- 最新 I2 化学结构片段改造完成后，隔离 5201/5209 重载因本机执行授权额度耗尽被拒绝；
  因此源码与离线回归已更新，但当前常驻服务未确认加载该次改造。需用户明确批准后再重载，
  不能绕过权限限制或声称网页已经生效。
- 后端全量回归 `600 passed, 7 skipped`，Portal `pnpm ts-check`、无效检索契约脚本及定向
  ESLint 通过。仅重启隔离测试 5201/5209，正式 5109 未修改、未重启、未 promotion。

### 2026-08-02（I2 主锚点原子化与 timer/角度检索回归）

- 本轮继续在 `5-invalidity-search-test` 侧收紧 I2 检索方案编译器：对
  `claim-timer-angle` 场景增加“主锚点不退化到句式碎片”硬约束。`_query_anchor_term_group(strict=True)`
  通过概念分桶+原子守门，优先保留稳定核心 anchor（如 `计时器/秒表/闹钟` 与
  `角度运算单元/角度计/陀螺仪`）并拒绝 `计时器并可立面预设有`、`盲人文点字符号`、`可立面预设有` 等
  草稿片段进入 OR 组。
- 新增回归断言到
  `5-invalidity-search-test/tests/test_analysis.py::test_i2_timer_angle_anchor_generates_concept_or_groups`：
  断言目标查询表达式与 `feature_term_groups` 中不出现上述句式污染片段，保证
  `object_plus_two_inventive_points` 仍稳定保留 timer 与角度特征分离。
- 该修改仅为检索方案可执行性边界收紧，未触达正式/无效正式目录与外部服务；不影响
  现有 Portal 页面流程。

### 2026-08-02（任务 62ac4f20-fb87-4233-8cc5-9aad021988d6：I2 显示屏/摄像头发明点补齐）

- 反思原因为：此前模型发明点未包含“显示屏/摄像头”时，补齐逻辑按“是否存在任意显示/成像特征”
  做布尔判断，命中一个后就跳过另一家族，导致关键家族被遗漏；同时模块锚点约束仍可能偏向
  时间/姿态类文本，错失镜面整机关键特征。
- 已在 `5-invalidity-search-test/src/invalidity/analysis.py` 同步落地：
  - 增加 `_mentions_display_feature`、`_mentions_camera_feature`；`_mentions_display_or_camera` 改为家族级判定。
  - `_parse_search_plan`（即发明画像解析）按 `display`、`camera` 两个家族计算缺口：`limitations`
    中出现该家族而模型发明点未覆盖时，按 `feature_id` 强制补齐对应家族锚点；无模型发明点时保底补齐。
  - `_query_anchor_bucket/_bucket_allows_term` 增加 `display/camera` 概念守门，修复 `display`
    正则漏括号导致的运行风险。
- 同步执行“只保留 3 条检索词”策略：`I2_DEFAULT_MAX_QUERIES=3` 已同步到
  `analysis.py`、`lab.py`、脚本与 module-lab 默认输入。
- 本次改动仅影响 `5-invalidity-search-test` 与页面测试路径，不触碰 `5-invalidity-search-prod` 与正式
  生产编排；待你同意再继续看是否需要把相同策略固化到 prod 版本。

### 2026-08-02（无效模块三：测试禁复用 + 检索词上限 5 条 + 无专利锁定审计）

- 用户两个叠加请求：①测试阶段无效检索模块三（I2）每次点击必须全新重跑，任何情况下不复用
  既往画像；②检索词限制为 AI 认为最有希望的 5 条，并确认模块三没有刻意针对当前测试专利
  锁定发明点。
- 禁复用（已完成并重启验证）：测试 5209 与律师模块实验室对
  `generation_source=same_source_cached_profile` fail closed，每次点击重新调用 GLM-4.6V 完整
  生成检索方案；同源缓存复用仅保留给正式 release 与离线脚本。宪章 2.4、WORKFLOW_SPEC 3.9
  已冻结；start_key 稳定键恢复后 110/110 场景通过。
- 5 条上限：`5-invalidity-search-test/src/invalidity/analysis.py` 中
  `I2_DEFAULT_MAX_QUERIES=5`，`_ensure_query_matrix` 首轮截断改为确定性优先级排名制
  （引证精确回查 > 说明书名词效果线 > 客体分类+发明点 > NPL 完整权利要求线 > 双发明点 >
  标题摘要 > 结构召回 > 单发明点短精确 > 最大相似长线，纯动作族辅助效果线降级，抵触申请
  通道在上限内折叠）；被裁线入 `trimmed_sink`/`portfolio_warnings` 可审计。Portal
  `module-lab` 默认 `max_queries: 5`。详细机制见 `5-invalidity-search-test/AGENTS.md`
  2026-08-02 条目与 WORKFLOW_SPEC 3.10 第 17 条。
- 专利锁定审计结论：生产代码无任何按专利号分支；所有词族/等价词/效果规则由目标专利自身
  文本驱动的通用规则触发，换一篇专利不会触发、不锁定发明点。唯一单案件痕迹（`_TARGET_EFFECT_RE`
  写死「水枪|喷枪」）已泛化为通用效果短语抽取并验证恢复。宪章 2.5 记录该边界。
- 回归：`5-invalidity-search-test` 全量 pytest `603 passed, 7 skipped` 连续 3 次；测试对齐
  包括上限 3→5 断言、gap objective 集合、离线重编译显式 `max_queries=20`。
- 本轮仍需：`bash IP-protral/scripts/build.sh` 重建 Portal 并重启 `patent-web`（前端是生产
  构建，只 restart 不生效），`ops/pm2/test/stop.sh && start.sh` 重载 5201/5209 并验证
  `/health`；正式 5109 与 `5-invalidity-search-prod` 不触碰、不 promotion。

### 2026-08-03（无效模块三：可执行关键词原子粒度 ≤6 字）

- 用户针对任务 12328641 耳机案件指出：①所有检索词都带「适配于不同耳朵的无线耳机」，客体过长
  无法命中，应改为「耳机 or 耳麦 or …」核心类别名词组；②结构召回线把权利要求整句当检索词，
  应提炼为「无线 and 耳机 and PCBA板 and 喇叭 and 电池 and 出音孔」式原子词 AND 组合；并明确
  规则「单个关键词不能超过 6 个字，多了就提炼」，以后一律照此执行。
- 已冻结：宪章 2.6、WORKFLOW_SPEC 3.11 第 18 条；`5-invalidity-search-test/analysis.py` 新增
  `_atomize_executable_queries` 在最终检索组合上统一原子化（客体核心名词化 + 同义 OR 组、整句
  提炼为原子名词、运动/关系短语拆分、无法保守提炼的片段丢弃并写 `portfolio_warnings`）。
- 规则为通用确定性提炼管线，无案件特定词表；英文短语与分类号不受 6 字上限约束，NPL 自然语言
  整句线保持原样。
- 验证：全量回归 `605 passed, 7 skipped` 连续 2 次；测试栈 5201/5209 已重载并 `/health` 正常；
  前端无改动无需重建；正式 5109 与 prod 目录未触碰。

### 2026-08-04（无效模块三拆分为「核心发明点」与「关键词生成」两个独立业务模块）

- 用户决定将律师模块实验室原业务模块三「制定检索方案」拆分为两个相互独立的业务模块：
  新模块三「总结核心发明点」（新底层模块 `I2_INVENTIVE_PROFILE`，输出 AI 核心发明点总结
  一段话 `invention_summary`、发明点区别特征及其对应技术特征，不生成检索式）；新模块四
  「生成检索关键词」（沿用 `I2_QUERY_PLAN` 原行为，内部独立重新生成画像并编译检索关键词/
  查询组合，不读取模块三运行结果）。两个模块互不依赖、可分别单独运行；业务模块总数由六个
  变为七个，原四至六模块顺延为五至七；I0–I5 底层模块边界与正式 I0 流水线不变。
- 已冻结：宪章 2.7、WORKFLOW_SPEC 3.12 与第 11 章七业务模块名单。
- 代码改动：`5-invalidity-search-test` 新增 `I2_INVENTIVE_PROFILE` module code（contracts
  Literal、LAB_MODULE_CODES、lab handler、GLM 响应 artifact、max_attempts=1 白名单），
  analysis 引擎新增 profile-only 生成路径（同一 prompt/GLM 调用/画像与 limitations 解析，
  跳过查询守门编译，queries 恒为空，测试环境每次运行重新调用模型）；`I2_QUERY_PLAN` 行为
  不变。Portal `module-lab/page.tsx` `BUSINESS_MODULES` 变为七项并顺延 step，新增 'profile'
  模块视图（核心发明点总结 + 发明点对应技术特征），'query' 模块保留检索式视图；Portal 契约
  测试断言同步更新。
- 后端验证（2026-08-04 完成）：`tests/test_lab_modes.py` 88 passed、`tests/test_lab_source.py` 14 passed、
  全量 `uv run pytest -q` **611 passed, 7 skipped**（基线 605，+6 为本次新增用例）；引擎层冒烟
  确认 profile-only 路径 `queries=[]`、画像/limitations 完整、审计 stage 正确，画像缺失按
  `plan_queries` 同语义有界重试后明确失败。
- 前端验证：`IP-protral/src/app/test/module-lab/page.tsx` `BUSINESS_MODULES` 变为七项（新增
  step3 'profile'「总结核心发明点」+ step4 'query'「生成检索关键词」，evidence/analysis/report
  顺延 5/6/7），新增 `runInventiveProfileForClaim` 与 `ProfileResultDetails` 视图（突出
  `invention_summary` 与发明点对应技术特征原文），`QueryResultDetails` 保留「首轮分线检索方案」；
  benchmark 拆为「看模块3发明点总结/看模块4检索关键词/看模块6逐篇比对」三视图。`pnpm ts-check`
  通过、定向 ESLint 干净、契约测试除一处既有失败外全部通过。
- 端到端：隔离测试栈 5201/5209 经 `ops/pm2/test` 重启加载新代码，`/health` 正常；
  `scripts/lab_unit_check.py --mode fixture --module I2_INVENTIVE_PROFILE` 真实 POST 5209
  run=succeeded（provider=fixture、attempts=1、limitations=2）。
- **既有问题（非本次改动引入，待跟进）**：契约脚本 `test-invalidity-portal-contracts.ts:1757`
  断言 `src/app/test/invalidity/page.tsx` 含 `fallbackOfRunId`，但该页面只有 `p002Fallback`/
  `fallbackRunId`，全仓 `src/` 无 `fallbackOfRunId`；本任务两个改动文件均不涉及该行与该页面。
  临时注释该一行后整套契约断言（含本次全部七模块新断言）`invalidity portal contract tests passed`。
- 部署：Portal 8GB 堆 `pnpm next build --webpack` + tsup 生产构建成功，`patent-web`
  （`~/.pm2-patent-prod`）已重启，3001 正常、未登录访问 `/test/module-lab` 正确 307 跳登录；
  正式 5109 未触碰、未 promotion。
- 同日跟进（用户反馈「模块3输入不能只看到权利要求1，要结合说明书分析」）：经核查，live 模式
  GLM prompt 本就通过 `_persisted_patent_context` → `_patent_context_for_prompt` 带入完整
  说明书章节、摘要、全部权利要求与附图，行为无缺口；缺口在律师页面「本次实际输入」只展示
  独立权利要求原文。已在 `module-lab/page.tsx` 模块三/四输入区新增「完整专利阅读范围」块：
  摘要全文、说明书各章节（章节名+字数，折叠展开全文）、全部权利要求数与附图数，标题改为
  「再结合说明书单独分析下列独立权利要求」。`pnpm ts-check`、定向 ESLint、契约测试
  （除既有 fallbackOfRunId 失败外）通过；Portal 已重建并重启 patent-web。


### 2026-08-04（模块四输入固定为模块三输出：免模型确定性重编译）

- 用户最新决定（取代同日早些时候「模块三/模块四相互独立」的表述）：律师模块实验室模块四
  「生成检索关键词」的输入就是模块三「总结核心发明点」的输出——只输入核心发明点对应特征
  （发明画像 `invention_search_profile` + limitations），模块四据此生成关键词；不再向律师
  展示或重新读取专利摘要、说明书、独立权利要求原文，模块四也不再重新调用 GLM 生成画像。
- 后端 `5-invalidity-search-test/src/invalidity/lab.py` `_query_plan_handler` live 路径：
  先用 `_successful_live_lab_runs(request, claim=...)` 找同案同独立权利要求最新成功的
  `I2_INVENTIVE_PROFILE` run（claim 取自 `request.source`/`data` 的
  `claim_investigation_id` + input `claim_id`，不依赖 I0 checkpoint）；找到则
  `engine.recompile_initial_query_plan(previous_plan=<画像 run 输出>, source_plan_run_id,
  source_sha256, patent_context=_persisted_patent_context(request), max_queries)` 免模型
  确定性编译并直接返回（`network_used=False`、`actual_provider="same_source_cached_profile"`、
  `model_response_audit=None`）；找不到抛 `PermanentJobError("LAB_I2_PROFILE_REQUIRED")`，
  不静默回退 GLM。manual/fixture 路径不变；正式 I0 流水线 I2 不经 lab handler，行为不变。
- `LAB_I2_CACHED_PROFILE_FORBIDDEN` 守门（2026-08-02）收窄但未删：因重编译分支提前
  return，守门只作用于 `plan_queries` 分支的引擎内部缓存复用，不拦这条律师显式的
  「模块三输出 → 模块四确定性重编译」路径。
- 前端 `IP-protral/src/app/test/module-lab/page.tsx`：模块四输入区改为展示模块三输出——按
  `output.source_plan_run_id` 在 `allBusinessStates['profile'].children` 精确匹配模块三
  run（回退同 claim 最新 ok run），展示 `invention_summary` + `inventive_point_features`
  绑定 limitations 原文；找不到显示「未找到模块3的输出，请先运行模块3」。dispatch 门控
  （真实案件路径）：无对应成功模块三 run 时不发 POST，push 本地 failed ChildRun 给律师可读
  提示；`friendlyError` 新增 `LAB_I2_PROFILE_REQUIRED` 映射；BUSINESS_MODULES 模块四
  input 文案改为「输入：模块3总结的核心发明点与技术特征」；契约断言同步新增 7 条。
- 验证：后端全量 `uv run pytest -q` **614 passed, 7 skipped**（基线 611，+3 新用例：
  `test_live_query_plan_recompiles_from_latest_inventive_profile_run`、
  `test_live_query_plan_requires_successful_inventive_profile_run`、
  `test_i2_recompile_accepts_profile_only_plan_without_queries`，无既有断言修改）；前端
  `pnpm ts-check`、定向 ESLint 干净、契约测试（临时副本注释既有 `fallbackOfRunId` 失败
  断言后）`invalidity portal contract tests passed`。
- 部署：隔离测试栈 5201/5209 经 `ops/pm2/test` 重启，`/health` 均 ok；Portal 8GB 堆
  `pnpm next build --webpack` + tsup 重建，`patent-web`（`~/.pm2-patent-prod`）已重启，
  `/` 与 `/test/module-lab` 均正确 307 跳登录、`/login` 200；正式 5109 未触碰、未
  promotion。
- 文档：宪章 2.8（含同日第二条决定与禁复用守门收窄说明）、WORKFLOW_SPEC 3.13（§11 修订）、
  `5-invalidity-search-test/AGENTS.md`、`IP-protral/AGENTS.md` 已同步。


#### 跟进（模块四「复用画像」提示改为中性说明）

- 用户反馈模块四仍显示「本次复用了同案发明画像 GLM 未重复调用……」告警，与「每次分析都
  重新来」的表述冲突。核查结论：这是 `QueryResultDetails` 对
  `generation_source=same_source_cached_profile` 的旧告警 Alert 直接展示后端
  `generation_warning` 原文；模块四 live 按新设计固定走「模块三输出 → 免模型确定性重
  编译」，该来源标记是必然且正常的，旧告警是历史缓存复用语境的残留。模块三每次点击仍
  重新调用 GLM 全新生成画像，行为不变。
- 修改（仅前端）：`module-lab/page.tsx` `QueryResultDetails` 该 Alert 改为中性蓝色说明
  「本次使用模块3总结的核心发明点与技术特征编译检索关键词，未重复调用 GLM；如需更新
  发明点分析，请重新运行模块3。」；律师视图不再展示 `generation_warning` 原文（字段保留
  给高级/审计入口，后端未改）。模块三 `ProfileResultDetails` 防御性告警保留。契约测试
  新增新文案断言。
- 验证与部署：`pnpm ts-check`、定向 ESLint、契约测试（临时副本注释既有失败断言后）通过；
  Portal 已重建并重启 patent-web，`/test/module-lab` 正确 307 跳登录、`/login` 200；
  后端无改动，5209 未重启。


### 2026-08-05（模块四检索词改由大模型生成：以模块三画像为输入调用 GLM）

- 用户确认模块四输入直接使用模块三输出是对的，但要求生成检索词时必须重新调用大模型分析
  生成。核查确认此前实现为免模型确定性重编译（`recompile_initial_query_plan`），不符合
  要求，已改造。
- 后端 `analysis.py` 新增 `generate_queries_from_inventive_profile` +
  `_generate_queries_from_profile_once` + `_queries_from_profile_prompt`：prompt 只含模块三
  画像冻结事实（invention_summary、inventive_point_features 绑定 limitations、
  common_context、mechanism_model、客体同义词、分类号），不读专利全文/附图；GLM 按多分辨率
  检索线角色提案后，依次过 `_guard_queries`（三类任二）→ `_ensure_query_matrix`（5 条上限
  优先级裁减）→ `_atomize_executable_queries`（≤6 字原子化）；有界重试与 `plan_queries`
  同语义，第二次仍不合规才用画像事实确定性组装兜底并留痕。`generation_source` 新增
  `"module3_profile_live_model"`；`llm.py` `invoke_json` 新增显式 `allow_text_only`
  （默认 False 不变，模块四纯文本子任务唯一使用点，模型仍为 GLM-4.6V，不构成静默纯文本
  降级）。`lab.py` live 分支切换至新方法，`LAB_I2_PROFILE_REQUIRED` 门控、
  `LAB_I2_CACHED_PROFILE_FORBIDDEN` 守门、manual/fixture、`recompile_initial_query_plan`
  （正式 release/离线）均未动；返回 `network_used=True`、`model_response_audit` 持久化。
- 前端 `module-lab/page.tsx`：结果区说明按来源值区分——`module3_profile_live_model` 显示
  「本次由大模型基于模块3总结的核心发明点与技术特征分析生成检索关键词」，旧
  `same_source_cached_profile` run 保留原文案；输入区说明补「检索关键词由大模型基于模块3
  的总结分析生成」；契约断言同步。
- 验证：后端全量 `uv run pytest -q` **618 passed, 7 skipped**（基线 614，lab 用例替换 1 +
  引擎级新增 4，无既有断言改坏）；前端 `pnpm ts-check`、定向 ESLint、契约测试（临时副本
  注释既有 `fallbackOfRunId` 失败断言后）通过。
- 部署：隔离测试栈 5201/5209 重启 `/health` 正常；Portal 8GB 堆重建 + tsup，`patent-web`
  重启后 `/test/module-lab` 正确 307 跳登录、`/login` 200；正式 5109 未触碰、未
  promotion。
- 文档：宪章 2.9（生效日期 2026-08-05）、WORKFLOW_SPEC 3.14（§11 修订）、
  `5-invalidity-search-test/AGENTS.md`、`IP-protral/AGENTS.md` 已同步。


## 2026-08-05 检索词 OR 同义词/中英文并联 + NPL 关键词化 + 去重

- 用户决定（针对真实 run `c32ffcef-aac5-4f0e-90af-5245ec12cb1b` 模块四输出）：
  ①构建检索词时每个单词用 OR 并联同义词及中文，扩大检索范围；②论文/技术资料（NPL）
  检索词必须是词而不是一整句话。排查确认该 run 还存在检索式完全重复、客体未原子化
  （走了 2026-08-03 NPL 整句豁免）问题，一并修复。
- 实现（只改 `5-invalidity-search-test/src/invalidity/analysis.py` + 测试）：新增
  `_supplement_executable_or_members`——OR 同义词只取画像冻结事实，两层池（可信池
  synonyms_zh/en+机构等价词直接放行；原文词 atoms 需同族过滤，无则单原词放行、不硬造，
  每词 ≤5 成员）；废除 NPL 自然语言整句豁免（2026-08-03 日志中该表述被本次决定取代），
  NPL 共享 ≤6 字原子化层、适用全部 I2 路径；检索式去重键 = (provider_kind, 规范化
  expression, 分类号, 日期通道, 检索范围)，被去重者进 trimmed_sink 写 portfolio_warnings；
  `_queries_from_profile_prompt` 同步要求 OR 并联 + NPL 关键词组合。修正 9 处因新规失效
  的既有断言。
- 验证：全量 `uv run pytest -q` **622 passed, 7 skipped**（基线 618）。
- 部署：隔离测试栈 5201/5209 重启 `/health` 正常；前端无改动，Portal 未重建；正式 5109
  未触碰、未 promotion。
- 文档：宪章 2.10（生效日期 2026-08-05）、WORKFLOW_SPEC 3.15（§16 版本表）、
  `5-invalidity-search-test/AGENTS.md` 已同步。


## 2026-08-06 I4-S 实质相同标准与两道确定性守门（v46→v48，四轮）

- 用户决定：①I4-S 实质相同标准最终解释——基于对比文件全文理解整体机构；结构和位置关系
  与目标无不同且功能作用相同即为实质相同；下位概念/具体构件承担相同角色（钉子之于固定
  单元）即视为公开，不要求名称或结构完全相同。②批准新增两道确定性守门：否定断言核验、
  跨特征一致性对账。触发案例：水枪专利×US20200080816A1 v12 run（eb094177）把
  「压力罐」「带有阀导管的阀」误判 not_disclosed，且同 run 父子特征自相矛盾。
- 宪章 2.11 / WORKFLOW_SPEC 3.16（§6 实质相同标准 + 守门条款 + 复核留痕要求）已冻结。
- 实现（`5-invalidity-search-test/src/invalidity/analysis.py` + contracts.py + 测试，
  coder agent-9 四轮）：
  - v46/v23：prompt 加实质相同标准；守门 A `_run_negation_guard`（not_disclosed/否定断言
    触发，候选词五来源并集 ≤8，全文命中且未处理触发一次有界候选复核，复核后仍遗漏降级
    uncertain）；守门 B `_run_consistency_guard`（父披露子不得否定、机构总结构件不得被判
    全文未提及，矛盾簇 ≤3 各一次合并重判，仍矛盾降级）；守门只降级不升级，留
    `guard_original_status`/`guard_note`/`guard_warnings`。635 passed。
  - v46b：修守门复核调用缺 `allow_text_only=True` 被 fail-closed 吞掉（performed=False）
    + 盲区特征 `violations[id]` KeyError；空 evidence_quote 否定强制进入复核。641 passed。
  - v47/v24：角色语义全文重扫指令扩到所有被复核特征 + 复核 prompt 附全文证据窗口
    （18000 字符）+ `role_candidates_evaluated`（deterministic/full_text_rescan）留痕；
    守门 B 失败根因（错误被 except 吞掉；超时 60→120s、max_tokens 4096→6144 防截断；
    部分无效响应按 feature_id 部分合并）；新增 `negation_guard_error`/
    `consistency_guard_error` 错误码。646 passed。
  - v48/v25：守门 B 加反向对账（子特征已就组成结构正向映射，父特征不得反向「无该结构」
    否定，文本包含判父子）；守门 A 重扫留痕门槛扩到全部否定特征（复核维持否定必须有
    ≥1 条 full_text_rescan 记录，「重扫未发现候选」显式记录也算，否则降级）。649 passed。
- 部署：三轮重启隔离测试栈 5201/5209，`/health` 均正常；前端无改动；正式 5109 未触碰。
- 回归（manual、输入与出错 run 完全相同）：
  - 耳机×CN216649931U：5 项披露稳定正确（凸台/套圈结构等价）；出音孔、定位卡凸经守门
    降级 uncertain，定位卡槽 not_disclosed；无矛盾、无守门错误码。
  - 水枪×US20200080816A1：阀导管端口/阀座、阀开闭位置、阀驱动器等判披露正确；v48 中
    「压力罐」等 5 项经守门 A、2 项经守门 B 降级 uncertain 并留痕——错误的确定性否定已
    消除，但模型复核仍未正向映射 reservoir/storage/bladder→压力罐，首轮判断跨 run 波动
    较大（同一特征不同 run 结果翻转），属模型侧残留问题，守门保证其不再以 not_disclosed
    错杀。
- 文档：宪章 2.11、SPEC 3.16、`5-invalidity-search-test/AGENTS.md`（4 条 2026-08-06
  条目）已同步。

## 2026-08-06 I4-S 候选构件发现预检（v49→v50，两轮）

- 用户决定：逐特征判断前先做「候选构件发现」——先列出对比文件中可能承担各角色的构件
  清单（带原文引文+位置），再从功能角色角度比对，解决首轮判断直接下结论导致的角色
  映射遗漏（压力罐、位置选择性联接装置从未判对）。
- 宪章 2.12 / WORKFLOW_SPEC 3.17 已冻结（发现→引文确定性回验→清单注入首轮/复核/守门
  prompt；发现失败降级为无清单运行，不阻塞分析；守门规则不变）。
- 实现（coder agent-9）：
  - v49/v26：`_run_candidate_discovery` 预检步骤；每条候选项引文回验对比文件原文，
    伪造引文丢弃并留痕 `candidate_discovery_dropped`；清单注入首轮比对、结构复核、
    两道守门 prompt；审计字段 `candidate_discovery_attempted/performed/error`。
    653 passed。回归：耳机案发现成功且抓到 1 条伪造引文；水枪案全文 47032 字符
    超时（120s 不够）报 MODEL_TRANSPORT_ERROR 降级无清单运行。
  - v50/v27：发现调用传输失败自动重试一次（最多 2 次），timeout 120→240s、
    direct 90→180s；新增 `candidate_discovery_attempts` 审计字段；schema 无效不重试。
    656 passed（已亲自复验）。
- 部署：重启隔离测试栈 5201/5209，`/health` 均正常；正式 5109 未触碰。
- v50 回归（manual，输入与出错 run eb094177 / 239c87ff 完全相同）：
  - 水枪×US20200080816A1（run 20966f7f）：发现步骤首次调用即成功（attempts=1），
    16 个特征全部有候选清单；9 条伪造引文被回验丢弃。历史性误判全部修正——
    「压力罐」→storage device/pressurized liquid source 判 explicit（此前从未判对）、
    「带有阀导管的阀」判 explicit、「位置选择性联接装置」→locking mechanism 判
    explicit；16 特征 14 项披露、2 项 uncertain（管连接、本体中间位置联接），
    无 not_disclosed 错杀、无守门错误码。
  - 耳机×CN216649931U（run 8fec6d10）：发现成功，2 条伪造引文丢弃；出音孔经守门 A
    从 not_disclosed 降级 uncertain（候选复核留痕）；凸台/套圈仍结构等价判披露；
    无倒退。

## 2026-08-06 内置盲测跟随规则升版：v50 五案重冻结（宪章 2.13 / SPEC 3.18）

- 用户决定：五个真实无效案件的内置盲测此前展示的是 2026-08-01 旧规则冻结结果，不会
  随模块6判断规则升版自动更新；要求每次 I4-S 规则升版后必须同步重跑内置盲测比对。
  该义务已固化到宪章 §4 第 4 条 + 版本表 2.13、WORKFLOW_SPEC 3.18（I2 检索计划冻结
  同理只在检索规则升版时重跑）。
- 执行：用 structural-disclosure-v50 对五案全部 54 个比对单元（水枪 15、光引发剂 15、
  电池浆料 12、无线耳机 6、燃气灶 6）经 `scripts/run_decision_i4s_blind.py` 重新逐篇
  分析，原子冻结到 `.data/.../decision-benchmarks/20260806-five-case-v50/i4s`；电池浆料
  claim-1 两篇中途遇 GLM 直连瞬时故障（probe_exit=7），按输入哈希断点续跑一次补齐，
  最终严格口径 54/54 全部 usable、unresolved=0、全部 v50。水枪×US20200080816A1 盲测
  结果为 12 披露 4 uncertain 0 not_disclosed（压力罐、位置选择性联接装置判披露），
  与实验室回归口径一致、略保守属模型正常波动。
- 前端：`benchmark-cases/route.ts` 的 `CASE_FREEZES` 五案 comparisonVersion 全部指向
  `20260806-five-case-v50`（planVersion 不变），`frozenAt` 改为 2026-08-06；首次构建
  遇 Node 堆 OOM，以 `NODE_OPTIONS=--max-old-space-size=12288` 重建通过；已用专用
  PM2_HOME `~/.pm2-patent-prod` 重启 `patent-web`（3001），`/login` 200、盲测 API
  匿名 401 鉴权正常。正式 5109 与 patent-test-copy（3002）未触碰。

## 2026-08-06 发明点自创术语改写：功能表达线（宪章 2.14 / SPEC 3.19）

- 用户决定：发明点自创词（如「位置选择性联接装置」）单独做 AND 锚点会检不到；目标检索式
  结构为 `CLMS:((水枪 or water gun) and (阀 or valve)) and DESC:(迸射 or 水弹 or Shot or
  water bullet)`——权利要求层客体+上位通用构件、说明书层功能效果词。首轮检索线混编：
  一部分用功能表达，一部分保留原术语表达，自创词术语线不得独占首轮。
- 实现（coder agent-9）：画像 `SearchConcept` 新增 `generic_component`/`function_effect`
  （ProfileTermGroup，缺省兼容旧画像）；新检索线 `object_plus_component_plus_effect`
  （客体 AND 通用构件 CLMS AND 功能效果词 DESC，每词 OR ≤5、≤6 字原子化照旧）；
  首轮预算 ≥2 时确定性预留至少 1 条功能表达线（portfolio_rank 22，cap 2）；修复两 OR 组
  绑定同一 feature_id 时构件词泄漏进效果组的守门互污；新增 I2 版本常量
  `i2-query-plan-v2`/`i2-inventive-profile-v2`；661 passed（+5 测试，已亲自复验）。
- 事故与修复：重跑五案 I2 生成时电池浆料案 3/3 失败（common_context_features 第 1 项
  未绑定真实特征/缺具体概念）——根因是旧 prompt 鼓励列场景客体与冻结校验必然冲突，
  化学类组合物权利要求无对应限定可绑定；agent-9 仅改 prompt 文案（客体/应用对象只进
  technical_subject/subject_synonyms 层级、concept 必须绑定 temp_id、返回前自检），
  校验未动；修复后电池案 5/5、其余四案各 1/1 通过。
- 部署与冻结：测试栈重启加载；五案 9 个独权计划用 v2 规则重新生成
  （`20260806-five-case-i2v2-gen`）并逐案离线重编译到
  `20260806-five-case-i2v2/plans`（含 recompile-index.json）；水枪案首轮首位即功能表达线
  `CLMS:((水枪 OR …) AND (联接机构 OR … OR coupling mechanism OR linkage)) AND
  DESC:(动量传递控制 OR … OR speed control)`，术语线（选择性联接）仍保留在其他线中，
  混编符合要求。I4-S 比对冻结（20260806-five-case-v50）不受影响、继续有效。
- 前端：CASE_FREEZES 五案 planVersion 全部指向 `20260806-five-case-i2v2`；
  重建重启 patent-web。

## 2026-08-07 Kimi 未完成工作审计与 I2 v3 固定五线收口

- 审计确认 Kimi 已完成 I2 v3 固定五种首轮线的大部分后端、Portal 和五案冻结工作，但存在
  三个未收口点：Portal 仍可能只执行首条/使用 compact fallback，五案冻结中的固定线仍残留
  `compact_fallback_allowed=true`，申请人线使用了未经真实 P002 验证的 `NOT PN`。
- 后端和 Portal 已统一为按 `query_id` 执行事实齐备的全部固定线，每线独立 run、实际请求
  一次；固定线一律 `compact_fallback_allowed=false`，零命中继续其他线。最终供应商 OR 组
  最多五项，中文申请人 OCR 字间空格确定性清理，旧首轮变体只保留历史兼容。
- 真实 P002 只读验证确认 `AN + TTL/ABST` 有效，完整 r4 电池浆料申请人线 HTTP 200、一次
  成功并返回前 10；加入 `NOT PN` 或 `NOT (PN:...)` 均返回 `68300004`。因此供应商不再接收
  PN 排除：原始前 10 和响应工件先冻结，本地再按规范化公开号主体排除目标专利及 A/B/U/S
  变体，输出保存 `excluded_target_documents`，不得把本地过滤冒充供应商过滤。
- 基于未加载 oracle 的既有画像离线重编译五案 9 个独立权利要求到不可变目录
  `20260807-five-case-i2v3-r4`。审计：32 条普通专利线，仅五种固定 variant，全部禁用 compact
  fallback，最终 OR 组最大五项；9 条申请人线全部含 AN、不含 PN/NOT PN、均冻结本地排除
  公开号。Portal 五案白名单已切到 r4；I4-S 继续使用 `20260806-five-case-v50`。
- 文档已同步到宪章 2.15、WORKFLOW_SPEC 3.21、测试 README 及模块/Portal 协作索引；根索引
  当前描述也修正为模块四读取模块三最新画像并重新调用 GLM，不再沿用 2026-08-04 被取代的
  “两个模块互不依赖”表述。
- 验证：后端全量 `667 passed, 7 skipped`；Portal `pnpm ts-check`、无效检索契约脚本和定向
  ESLint 通过，`NODE_OPTIONS=--max-old-space-size=12288 pnpm build` 成功（Next.js 页面构建
  与 tsup server bundle 均完成）。隔离测试栈 5201/5209 已重启加载且两个 `/health` 均正常；
  Portal 与正式 5109 未重启、未触碰，未 promotion。

## 2026-08-07 固定五线示例未生效诊断与 Portal 收口

- 用户反馈模块四示例仍像旧版。只读核验确认源码和五案白名单已指向
  `20260807-five-case-i2v3-r4`，但 `patent-web` 进程启动于 2026-08-06，而 `.next`/`dist`
  构建于 2026-08-07；3001 实际仍运行旧 bundle，是页面未显示新示例的直接原因。
- `/test/module-lab` 的模块四结果现明确显示“固定五类，实际生成 N/5 条”；事实不足时不猜造，
  因而是最多 5 条而非强行补满 5 条。历史旧变体直接标为旧规则结果，提示重新运行模块四。
- 模块五执行链删除“模块四无查询时构造旧 `claim_context_recall` 查询”的残余回退；首轮普通
  专利线必须为 1—5 条且全部属于五个固定 variant，否则 fail closed，不再搜索旧方案。
- Portal `pnpm ts-check`、定向 ESLint、无效检索契约测试通过；默认 4GB build OOM 后按既定
  12GB 有界堆重建成功。专用 PM2_HOME 中 `patent-web` 已重启，3001 `/login` 返回 200、
  `/test/module-lab` 匿名访问正确 307 到登录页；5201/5209 与正式 5109 未重启、未触碰。

## 2026-08-08 五案示例真实性审计与 D1 区别特征循环

- 用户指出“最多 5 条”示例仍像旧版。核验确认 r4 虽符合固定五类模板，但实际由 2026-08-06
  的旧模块三画像离线重编译，并未重新执行当前“模块三 GLM 画像 → 模块四 GLM 生成词”链路；
  页面进程不是根因。新增 `generate_decision_query_plans.py` v8 流程和 manual 模块四显式画像输入，
  准备生成 r5；因该动作会把五案目标专利正文/附图发送给外部 GLM，安全审查要求用户明确授权，
  本轮未伪造或切换白名单，Portal 仍指向可审计的 r4。
- 宪章升至 2.16、WORKFLOW_SPEC 升至 3.22：D1 先按确认相同特征数排序；冻结区别特征的内容、
  结构角色/关系和技术作用；新增 `I2_GAP_QUERY_PLAN` 先按文献版本、内容哈希、目标限制版本、
  日期资格和 I4-S 规则/run 检查既有语料复用，仅搜索未覆盖差异。初始轮不计数，之后最多 5 个
  gap 轮；第五轮仍未覆盖时 `gap_search_exhausted` 收口并保留组合分析和大 Claim Chart。
- I0 每个 gap 轮现在持久化独立 I2-G module run、输入版本键、覆盖决定和允许检索的 feature IDs；
  大 Claim Chart 选择 D1 加最多 5 篇日期合格高增量文献，报告契约新增 `large_claim_charts`。
  `/test/module-lab` 模块六及律师报告视图已增加横向大 CC 表。
- 验证：无效测试后端全量 `675 passed, 7 skipped`；Portal `pnpm ts-check`、无效检索契约脚本、
  定向 ESLint 和 12GB 有界堆 production build 通过。隔离测试栈 5201/5209 已重启加载且两个
  `/health` 均为 test/ok；正式 5109 未触碰。r5 live 重生成尚待
  用户明确同意测试专利材料发往当前配置的外部 GLM，完成前不得宣称示例已经更新。

## 2026-08-08 I2-G 所有权与零检索组合路径收口

- 继续审计发现此前 I0 虽创建 `I2_GAP_QUERY_PLAN`，该 run 只保存复用决定和允许 feature ID，
  真正查询仍由另一个旧 `I2_QUERY_PLAN` 生成；现已把后续轮查询生成完全收归 I2-G，首轮之外
  不再创建 I2_QUERY_PLAN。I2-G 输出同时包含区别特征、复用证据、允许 IDs、完整 QueryPlan、
  上一轮失败原因与模型响应审计。
- gap prompt 删除“并行保留 full-claim 检索线”的旧规则，只允许未覆盖 D1 区别特征；上一轮
  失败原因和旧检索表达式进入 prompt，若新计划全部重复旧表达式则有界重生后 fail closed。
  每篇新文献仍由 I4-S 比对完整独权，不影响单篇新颖性停止。
- 既有语料覆盖全部区别特征时，I2-G 生成 `generation_source=existing_corpus_reuse`、
  `queries=[]` 的可审计计划，I0 不调用 provider，但继续运行 I4-I 组合评价和大 Claim Chart
  后才收口；新增端到端用例验证只发生首轮一次检索、两次组合评价，并保留 D1+D2 大表。
- 全量无效测试后端回归为 `676 passed, 7 skipped`。五案 Portal 白名单未切换，仍是只用旧
  模块三画像离线重编译的 r4；真正当前模块三/四重跑 r5 仍需用户明确授权向外部 GLM 发送
  五案正文/附图，未获授权前不得把示例描述为已更新。
- Portal `pnpm ts-check` 与无效检索契约脚本通过；仅重载隔离测试 PM2 的 5201/5209，两个
  `/health` 均返回 `environment=test`/`status=ok`。正式 5109 与 3001 `patent-web` 未触碰。

## 2026-08-08 无效 loop 同轮停止与 17 件 fixture 全量回归

- 完成性审计修复 I0 停止点：gap 轮新取得文献经当前版本 I4-S 后若已同角色/同作用覆盖全部
  D1 区别特征，系统在该轮完成 I4-I 与大 Claim Chart 后立即停止，不再创建下一轮零查询
  I2-G；预检即已覆盖的零查询路径继续保留并使用独立停止原因。
- 工作流端到端测试新增“新 D2 在第 2 轮命中即停”，并加强“初始+五个 gap 全失败”用例，
  断言第 5 个 gap 轮仍完成组合分析以及 D1+Top5 大表。
- `run_full_invalidity_regression.py` 的旧三轮预算改为五个 gap 轮，gap fixture 不再生成完整权利
  要求并行查询，comparison 显式绑定当前 I4-S 规则版本。目录当前发现 17 个 PDF；首次全量
  回归因旧 fixture 为 `legacy-unversioned` 全部 fail closed，修复后最终 run
  `fixture-20260808T084455Z-0aca0f1b` 为 17/17 通过。该结果只证明真实目标解析与合成证据合同，
  全量 live 检索仍未完成。
- 无效后端全量 `677 passed, 7 skipped`，Portal 类型检查与契约脚本通过；隔离测试 5201/5209
  已重载且 health 正常。正式 5109、Portal 3001 与 r4 示例白名单未触碰；r5 仍需外部数据发送
  的明确用户授权。

## 2026-08-08 五案 r5 真重生成与授权范围内 live 回归

- 用户明确授权把五案测试目标专利材料发送到测试 GLM。当前模块三画像再交模块四生成的真实
  r5 已冻结到 `20260808-five-case-i2v3-r5`；未加载 oracle，五案 9 个独权分别生成
  `4/4、5/5、3/3/5、3、4` 条，仅含固定五类且全部禁用 compact fallback，每个 OR 组最多
  5 项。事实不足的组仍跳过，不强行补满五条。
- 生成器删除 manual 下游输入中的 `simulated/fixture*` 运行标记但保留画像事实，客户端等待
  上限改为可配置默认 480 秒，并新增按目标 SHA/claim/oracle 标记校验的显式 `--resume`。
  Portal 五案白名单和契约已切换 r5；`pnpm ts-check`、Portal 契约及 12GB production build
  通过，`patent-web` 已重启加载，正式 5109 未触碰。
- live 首次暴露完整工作流 I2 专用默认预算仍为 120/105 秒，与文档和通用 360/180 秒预算不符；
  默认已统一为 360/180，保留独立环境变量覆盖。修复后水枪案完成 5 条真实检索并生成不可变
  报告；按现行首轮仅普通专利固定五线的审计口径，15 项真实性、版本、日期与多模态链检查
  全部通过。
- 回归审计清除了旧“首轮必须同时有抵触申请/NPL”矩阵：首轮只验普通专利固定线；第 2 轮起
  gap 才验普通专利、抵触申请和 NPL 三通道。live retry 的 investigation/session/idempotency
  key 与报告文件现按 attempt 隔离，避免第二次尝试复用调查并 409 覆盖第一次真实结果。
- 无线耳机案三次真实执行均形成 5/5 网络检索，但最终仍为 `partial`：智慧芽 P012/详情链和
  EPO OPS 波动，P020 PDF 回退取得的原文虽有 SHA，公开日证据未达到确定资格门槛；独立 EPO
  探测分别为 `ConnectError`/`RemoteProtocolError`。未把题录、失败抓取或未核验日期冒充证据。
  本轮只发送了用户明确授权范围内的水枪/无线耳机实例；实例目录其余 15 件尚未获外发授权。
- 后端新增生成器标记清理、首轮/gap 审计矩阵测试，最终全量 `679 passed, 7 skipped`。

## 2026-08-08 律师模块实验室拆为十个可测试阶段

- 用户确认把原七张业务卡片调整为十个按律师工作顺序排列、可分别测试的阶段；前五阶段保持
  目标专利、关键日、发明画像、首轮检索词和首轮证据链，原模块六拆为单篇 I4-S、D1/I4-C、
  gap/I2-G、创造性 I4-I，原报告顺延为第十阶段。
- 每个阶段均保留“当前真实案件”和“内置示例”入口及独立运行状态。当前案件的阶段六按阶段五
  已持久化文献逐篇启动 I4-S；阶段七、八、九分别独立启动 I4-C、I2-G、I4-I；阶段八重复
  点击按同案上一成功 gap 轮递增，最多五轮；阶段十继续输出大 Claim Chart 与律师报告。
- Portal 契约改为精确断言十阶段及 I4-S/I4-C/I2-G/I4-I/I5 映射。`pnpm ts-check`、定向
  ESLint、无效 Portal 契约和 12GB 有界堆 production build 均通过；仅重启 `patent-web`，
  后端 5201/5209 与正式 5109 未修改、未重启。

## 2026-08-08 五案示例同步十阶段架构

- 修复 `/test/module-lab` 五个真实无效案件仍停留在模块3、4、6三个入口的问题；五案现与主
  实验室一致提供模块1—10十个按钮和逐阶段结果。benchmark API 同步返回申请号、每项独权
  关键日、冻结源文献以及十阶段证据模式。
- 五案直接冻结事实覆盖目标清单、r5 发明画像/检索式、源文献清单和 v50 单篇比对。由于没有
  独立 I4-C/I2-G/I4-I/I5 冻结 run，模块7—10明确显示为冻结证据复核预览：D1 仅按确认披露
  数排序供复核，gap 只检查其他冻结文献覆盖线索且不伪造查询，创造性只列组合候选和证据缺口，
  报告只汇总大 Claim Chart；均不得冒充实时运行、法律结论或正式报告。
- 新增契约断言确保五案共用十阶段导航、删除旧三按钮文案、API 为全部十阶段声明证据模式且
  继续禁止 oracle/决定结论进入运行输入。`pnpm ts-check`、定向 ESLint、Portal 契约、12GB
  production build 均通过；仅重启专用 PM2_HOME 的 `patent-web`，`/login`=200、模块实验室
  匿名访问=307，测试后端 5201/5209 和正式 5109 均未修改、未重启。

## 2026-08-08 十阶段示例复核与 17 件实例覆盖收口

- 五个 benchmark 示例已确认全部显示模块1—10；前六阶段使用冻结事实，后四阶段在没有独立
  I4-C/I2-G/I4-I/I5 冻结 run 时只显示明示的证据复核预览。当前 production build 与源码
  一致，`patent-web` 在线，登录页 200、匿名模块实验室按设计 307；Portal 类型、ESLint 和契约
  验证通过。因浏览器无登录态，未代用户输入账户执行登录后点击测试。
- 实例目录实际为 17 件专利 PDF 加 2 个辅助 XLSX。oracle 已补齐 `CN216086819U`、
  `CN217770321U`，manifest 现在拒绝未登记 PDF；解析回归 17/17，最新含 I3-F 候选过滤合同的
  fixture 全流程 run `fixture-20260808T172013Z-2ab095c1` 亦为 17/17。后端全量
  `680 passed, 7 skipped`。
- fixture 回归阶段账本现显式包含 I3-F，并在 live 模式只认持久化
  `I3_CANDIDATE_FILTER` run；这不会掩盖完整 I0 尚未持久化该阶段的架构缺口。实例目录其余
  15 件仍未获外发授权，本轮没有向测试 GLM、Patsnap、EPO 或 NPL provider 发送其内容或查询，
  所以不得把 17/17 fixture 描述为 17 件 live 完成。

## 2026-08-08 完整 I0 的 I3-F 取文前闭环

- 完整 I0 已从“gateway 同时检索并取文”改为同轮全部查询先 discover、统一持久化
  `I3_CANDIDATE_FILTER`、再只对唯一保留代表 retrieve。正式 gateway 拆分 discover/retrieve，
  兼容 search 也组合同一实现；模块实验室与 I0 共用 GLM 保守语义筛选合同和 fail-open 守门。
- 新回归证明两条查询全部 discover 后才 retrieve，同申请 A1/A2 只取一个代表；live 审计要求
  每个 I3_FETCH 前存在同 iteration、成功、输入/输出哈希完整的 I3-F。仅出现模块代码、过滤
  发生在取文后或缺哈希都不得算 live 完成。
- 后端全量为 `682 passed, 7 skipped`。最新 17 件 fixture run
  `fixture-20260808T174538Z-923fdf55` 为 17/17，且 `live_completion_gate_passed=false`；隔离
  5201/5209 已重载并健康，正式 5109 未触碰。实例目录其余 15 件仍未获外发授权，因此全量
  live 完成门槛继续保持未满足。

## 2026-08-08 全页可读/OCR证据闸门与五案十阶段复核

- 修复完整 I0 仍可能用题名、摘要或说明书片段代替全文进入 I4-S 的缺口。真实 provider 文献
  现在必须先冻结原文，再对 PDF 每一页完成嵌入文本提取或 OCR；只有当前
  `readable-patent-v1` 与原文 SHA 精确绑定、处理页数等于总页数、失败页为零且正文达到可分析
  门槛时，才允许单篇比对。缺页/正文不足会写明 provider failure，不再静默使用题录降级。
- P020 PDF 新增首页公开号与 `(43)/(45)/公开日/公告日` 精确核验，原生文本不足时仅对首页用
  sparse OCR 复核；通过后可形成与冻结 PDF SHA 绑定的日期证据。报告不公开全文或本地路径，
  只公开可读审计摘要：规则版本、源 SHA、总页数/处理页数/OCR页数/失败页、正文字符数与哈希。
- live truth gate 新增 `analysis_ready_document_versions`，I4-S、日期资格、来源和原文必须绑定同一
  document_version。授权范围内 `CN217770321U` 重跑为 5/5 文献版本全页完成（44、7、5、8、5
  页，失败页均 0），两条同版本图文 I4-S 完整链通过；调查业务状态仍为 `partial`，不冒充整案
  完成。该单案 summary 的全量 live gate 因未覆盖 NPL 类别仍保持 false。
- 五案十阶段示例再次核验：五案共 9 项独权的 r5 plan 与 v50 comparison 文件齐全，模块1—6
  展示冻结事实，模块7—10继续明示为冻结证据复核预览。Portal 契约、TypeScript、定向 ESLint
  通过；后端全量 `686 passed, 7 skipped`。隔离 5201/5209 已重载健康，正式 5109 未触碰。
- 用户外发授权仍只覆盖五案；实例目录其余 15 件没有发送给 GLM、Patsnap、EPO 或 NPL。
  因此 17 件 live 完成目标仍未满足，不得用 17/17 fixture 或本次耳机单案通过替代。

## 2026-08-08 全页守门后的 17 件 fixture 回归修复

- 当前代码首次重跑 17 件实例时全部以 `partial` 收口。逐案阶段账本显示 I1— I3-E 正常、
  I4-S/I4-C/I4-I 均未运行；根因是全页可读守门只识别历史 provider 名 `fixture`，遗漏完整
  回归实际使用的明确模拟 provider 名 `fixture_simulation`，导致模拟文献被误当作真实外部
  文献要求不可变原文和 `ReadablePatentDocumentV1`。
- 工作流现统一维护非 live provider 集合 `fixture / fixture_simulation / manual`，只对这些
  明确合同模式保留兼容；真实 provider 的全页、SHA、原文快照与 OCR 守门不放宽。新增参数化
  回归覆盖三个 provider 名。
- 修复后全量 fixture run `fixture-20260808T193424Z-99648101` 为 17/17 通过、26 项独立权利
  要求全部到达可解释终态；逐案 I1、I1.5、I2、I3-P、I3-NPL、I3-F、I3-E、I4-S、I4-C、
  I4-I、I5 均通过。manifest 仍为 17 件 PDF + 2 个辅助 XLSX、无缺失或意外专利；后端全量
  `689 passed, 7 skipped`。该运行未使用网络，`live_completion_gate_passed=false`，不能替代
  尚未获授权的其余 15 件 live 回归。

## 2026-08-08 17 件全模块完成性补充审计

- 当前模块一解析器再次对实例目录全部 17 件 PDF 执行 `--fail-on-error` 回归，17/17 通过；
  权利要求/独权、专利号、标题、摘要、申请/公开/关键日、分页正文、说明书、摘要附图、技术
  附图、图片文件与 SHA 等逐项检查全部为真。工件为
  `.data/invalidity/test/regression/parser/parser-20260808-current-r2.json`。
- 对最新 I0 fixture run 的 I5 工件做逐份审计：17 个结果对应 17 个唯一且实际存在的报告文件，
  全部为 `ReportDataV1 contract_version=v1`、非 preview、具有报告快照 ID 和 64 位 SHA-256；
  共覆盖 26 项独权，状态为创造性证据完成 17、新颖性证据完成 8、检索预算如实收口 1，
  无 provider failure。fixture/无网络/不可作法律证据标记保持完整。
- Portal 十阶段合同、`pnpm ts-check`、模块实验室/benchmark 定向 ESLint 通过；模块实验室
  fixture/source 109 项测试通过；PM2 测试/正式隔离合同 11/11 通过。宿主只读健康检查确认
  5201=`test/ok`、5209=`test/ok/v1`、3001 登录页 HTTP 200；本轮没有重启服务或访问正式 5109。
- 完成性边界不变：17 件本地 fixture 已证明目标解析、I0— I5 状态机和十阶段前端合同；实例
  目录其余 15 件尚未获发送到 GLM/Patsnap/EPO/NPL 的授权，不能据此宣称 17 件 live 完成。

## 2026-08-09 I4-O 显而易见性预分析与十一阶段部署

- 在 I4-C 与 I2-G 之间正式加入 `I4_O_OBVIOUSNESS_PRECHECK`，律师实验室由十阶段扩展为十一
  阶段。I4-O 对区别特征/耦合特征组逐项保存客观技术问题、D1 技术启示、惯用手段/公知常识、
  修改动机与路径、反向教导、效果可预期性及确定性检索路由；删除“组合启示、反向教导和技术
  效果仍需独立证据”的统一占位结论。
- 路由守门明确区分两条路径：`d1_teaching_path_complete=true` 的 D1 完整改造路径可独立关闭
  普通结构检索；惯用手段有可引用证据也可独立关闭；惯用手段仅为模型预判时只生成
  `search_common_knowledge_evidence`，不再重复检索完全相同结构。I0 只有在存在至少两篇可组合
  文献，或 I4-O 已以完整证据关闭普通结构缺口时才进入 I4-I，避免预分析刚运行就提前改写 gap。
- CN217770321U 内置示例新增 source-only I4-O 冻结：出音孔作为惯用布置候选仅补公知常识证据；
  定位卡槽/定位卡凸组明确引用 CN216649931U 说明书 [0021]/[0031] 的相对转动、卡紧组件、多个
  卡槽和弹性卡接部启示，并结合惯用卡槽—卡凸定位手段，仅补公知常识证据。其余四案没有独立
  I4-O 冻结时保持保守未解决输出，不伪造 live 结论。
- 验证：后端定向组合回归 `349 passed`；Portal `pnpm ts-check`、十一阶段契约、定向 ESLint、
  `scripts/build.sh` production build 全部通过。仅重载隔离测试 5201/5209 和 Portal
  `patent-web`，健康检查分别为 test/ok、test/ok/v1、登录页 200；正式 5109 未触碰。
- live 冒烟仅使用已获授权的耳机案：I4-C 成功后 I4-O run
  `07d88eb8-1b02-41e8-8c6c-5fa14ca58161` 成功，实际结果的两组差异均路由为“只补公知常识证据”
  且 `ordinary_structural_search_required=false`。该持久化调查当前选取的 D1 不是内置示例指定
  的 CN216649931U，因此该 live run 只证明新路由生效；CN216649931U 的具体 [0021]/[0031]
  逻辑由独立 source-only fixture 与契约测试验证，二者不得混称。

## 2026-08-09 固定五组冻结计划直接执行

- 修复 `I3_PATENT_SEARCH` 在 I2 已按固定五组生成合规表达式后仍重复套用旧通用单条守门的
  问题。I3 现在要求并精确核对同案同独权成功 I2 的 `query_plan_run_id`、`query_id`、固定
  variant 与 `provider_expression`；核对通过后按冻结组合原样调用 Patsnap，不再把申请人+客体
  误判为“只有技术主题”，也不再要求原始长 feature 在已原子化表达式中逐字出现。临时、跨案、
  错 query 或表达式不一致输入仍 fail closed，客户端不能伪造 trusted 标记。
- Portal 已为每条固定线同时发送 plan run 与 query id。目标计划
  `3fde0fac-858a-4c16-ae3c-ca08554ad921` 的五条真实 I3 回归 run 为
  `b11d4774-93a7-439e-8078-1c5823e75c9f`、`43060822-bd77-4810-b9a6-93beacab92ac`、
  `7c200e16-0a19-4f05-bfab-b1c132733880`、`6e68362f-cf62-4d01-98ab-839e3cf0e0ed`、
  `df75134a-2984-407b-b04a-66b98bfb1295`；五条均 `succeeded`、`patsnap`、`network_used=true`、
  `frozen_plan_query_verified=true`，本案均为合法零命中，不再出现 `LAB_QUERY_INVALID`。
- 后端定向回归 `110 passed`；全量 `700 passed, 7 skipped, 1 failed`，唯一失败是实例目录新增
  三个尚未登记 oracle 的 PDF，与本次检索执行修改无关，未擅自登记或外发。Portal 类型检查、
  契约、定向 ESLint 和 production build 通过。仅重载隔离测试 5201/5209 与 `patent-web`，
  正式 5109 未触碰。

## 2026-08-09 I2 v4 固定五线核心含义/合理上位词

- 固定首轮仍只有五类、至多五条；不把宽词拆成额外检索线。每个客体、构件、动作或功能组现
  同时冻结具体词/同义词与 `subject_core_terms_*`、`core_meaning_terms_*`，并在同一个 OR 组
  执行。例：具体喷水车客体可并列喷水车/玩具车，第一供水管并列供水，水中吸水结构并列吸水。
- 核心词只能源于目标专利或目标词的通用语言归纳，禁止已知文献反向补词，禁止以宽词替换具体
  词，禁止裸“装置/结构/部件/系统”。旧画像同案重编译会确定性补齐可恢复的短核心词；每组
  OR 仍不超过 5 个成员并保留跨语言词。
- 原子化器仅在显式 OR 组已有长词的字面核心短词时，把长词拆出的原子留在同组 OR，避免原本
  一个发明概念变成多个必须同时命中的 AND；无该依据的旧查询保持既有收窄规则。规则/prompt
  升为 I2 v4，后端分析定向回归 `199 passed`。水陆喷水车最终 live 计划
  `4d155834-30b3-4286-a529-fa2eacc902b0` 的客体组为“水陆喷水车/玩具车/喷水车/两栖喷水车/
  喷水玩具车”，发明点组为“体底部吸水口/第一供水管/水泵进水口/供水/吸水”；五条真实
  Patsnap run 全部成功并通过冻结计划校验，同申请人线 0 条，其余四线各返回前 10 条。
  全量回归 `701 passed, 7 skipped, 1 failed`，唯一失败为 3 份新增 PDF 未登记 oracle；隔离
  5201/5209 已重载，正式 5109 未触碰。

## 2026-08-09 模块六超时假失败与受控并行修复

- 对调查 `033f35cc-4229-501e-82fc-1fc0fb53e605`、独权调查
  `fa1d118e-1dd6-4977-b2fb-8292592273fb` 的持久化记录复核确认：最新候选筛选血缘取得 31 篇
  可分析全文，旧页面在退出前只创建了 28 篇 I4-S，且这 28 篇最终全部成功。旧 5209 只有一个
  durable worker，第二个前端并发任务曾先排队约 405 秒再执行约 257 秒，创建到完成约 662 秒，
  超过 Portal 固定 600 秒；前端又只在整个 Promise 完成后写 children，因而错误显示“本模块没有
  得到可用输出”。这不是 I4-S 实体分析失败。
- 宪章升至 2.22、工作流规范升至 3.30：模块六必须使用逐篇持久化、可恢复批次，排队和执行
  分开显示，页面超时/断线不得清空已完成结果；模块六只做 I4-S，不自动越级运行 I4-C/I4-I。
- 测试 5209 新增 `INVALIDITY_WORKER_CONCURRENCY`，默认 2、允许 1—4；每个 worker 使用独立
  身份和租约，通过现有 `FOR UPDATE SKIP LOCKED` 并行领取任务，关闭时分别释放活动租约。
  注入单一测试 worker 时不复制。
- Portal 模块六改走兼容保留的 `invalidity_test_module5_batches`/
  `/api/test/invalidity/module5-batches`；URL 新写 `module6_batch`、兼容旧参数。批次每轮立即返回
  queued/running/terminal、排队秒数、执行秒数和已有 disclosure。历史恢复改以最新 I3_FETCH
  共享 `candidate_filter_run_id` 的 analysis-ready 全文为清单，不再用 15 分钟时间窗猜批次；
  清单内已有 I4-S 复用最新 run，缺失项补建。
- 验证截至本日志：后端配置/API/worker/DB 定向 `86 passed, 7 skipped`，并发与恢复专项
  `3 passed`，PM2 隔离合同 11/11、Portal `pnpm ts-check` 和无效合同通过。全仓 ESLint 仍仅被
  既有 `IP-protral/src/app/module1/page.tsx:121` 条件 Hook 错误阻断；正式 5109 未触碰。
- 真实补跑验证：恢复接口准确返回 31 篇全文、28 篇已有成功 I4-S、3 篇遗漏；补建的 3 个 live
  run `22df588c-a0f4-44f0-8b82-be9b3da59497`、`91726ed9-0c51-4548-a2b2-68d3dc841d96`、
  `693f4078-9777-4ddb-8f13-3efdf0eb9255` 均 succeeded 且各返回 5 项 disclosure。前两篇同秒
  启动，第三篇在第一篇结束后约 0.01 秒接棒；三篇墙钟约 160 秒，对比串行执行时长合计约
  296 秒，节省约 46%。最终该血缘 31/31 均已有成功 I4-S。5201/5209 与 Portal 已重载，5209
  health 返回 `worker_concurrency=2`，正式 5109 未触碰。

## 2026-08-09 模块九 D1/未解决状态一致性

- 模块九旧 run `65431bb0-6388-4f25-8db6-79ed61c46aab` 的异常是状态映射和显示错误，不是模块七
  未运行：模块七/八均绑定 D1 `ccb50dd7-4276-49df-bcf5-d92fd736e3e5`。I2-G 曾把转
  `human_review` 的 3 项区别特征从未覆盖集合删除，进而误报全部覆盖。
- 工作流规范 3.32 明确覆盖、未解决、允许自动检索和律师复核四个集合。后端 I0/module-lab
  共用确定性分流；新 I2-G 顶层冻结 D1。Portal 同时兼容旧运行的嵌套 D1 和人工复核字段，零
  查询不再自动等同于全部覆盖。
- live 验收 run `de161249-faba-466a-bd39-879c95993cd7` succeeded：0 覆盖、3 未解决/人工复核、
  0 自动检索、0 查询，停止原因为 `human_review_required`。后端全量 `704 passed, 7 skipped,
  1 failed`，唯一失败为既有 3 份新增 PDF 未登记 oracle；Portal 类型、契约、定向 ESLint 与
  production build 通过。隔离 5201/5209 和 `patent-web` 已重载，正式 5109 未触碰。

## 2026-08-09 模块九 gap 查询确定性恢复

- run `3777d654-31c4-4ae5-ac29-2c21697d0de6` 并非没有区别特征：GLM 连续两次把 gap 轮返回为
  首轮 `initial/full_claim_single_reference` 五线，守门全部拒绝后，旧代码在 gap 矩阵补齐前
  提前抛出 `I2 没有通过守门的检索式`。
- I2-G 现在在一次守门反馈重试仍失败时丢弃模型的不合规首轮提案，并仅从同案冻结画像、I4-O
  允许自动检索区别特征和补证类型确定性恢复。恢复按区别特征分别生成短线，每线为客体上位词组
  AND 一个核心含义词组，同义/合理上位词只在同一 OR 组；禁止把复合限定全文的所有构件多重
  AND。五条预算内确定性补齐 patent ordinary、NPL ordinary、CN conflicting-application，模型
  双响应和所有拒绝原因继续审计。工作流规范升至 3.33。
- 最终 live run `de5a9f48-dcec-471e-9c1b-7f34a664fd7e` succeeded：D1
  `ccb50dd7-4276-49df-bcf5-d92fd736e3e5`，0 覆盖、3 未解决、2 自动检索、1 律师复核、4 条
  合规 gap 查询；核心词组分别落为“供水管/供水/第一供水管/供水管连接”和
  “炮塔壳内/炮塔壳内喷水/炮塔壳”。第一次作业尝试的 GLM 临时无 JSON 由 durable job 第 2 次
  自动恢复。核心测试 `354 passed`，后端全量 `705 passed, 7 skipped, 1 failed`（唯一既知样本
  oracle 差异）；隔离 5201/5209 健康，正式 5109 未触碰，Portal 本轮无代码变更、无需重建。

## 2026-08-09 模块九业务阶段端到端修复

- run `adf2b7a6-8655-4ac0-8ed1-810e83ce01b6` 已成功生成 4 条 gap 查询；页面异常的根因不是
  查询生成失败，而是模块九前端只运行 logical I2-G，未继续 I3/I4-S。“缺少可复用版本键”仅
  表示旧文献结果没有同时绑定文献版本/哈希、目标 limitation 版本、日期资格 revision、I4-S
  run/rule，禁止直接复用为覆盖证据；它必须令特征保持未覆盖并触发补检。
- 工作流规范升至 3.34：律师业务模块九在存在可执行查询时必须完成
  `I2-G -> I3-P/NPL -> I3-F -> I3-E -> I3-QUALIFY -> I4-S`，只有真实零命中/无可取全文可
  审计收口；存在 analysis-ready 新文献但未完成逐篇比对不得显示成功。logical I2-G 本身仍不
  直接调用 provider，模块边界未改变。
- 测试后端支持按 I2-G 冻结原式与 lineage 执行 gap 专利查询，并区分普通公开日截止和 CN 抵触
  申请的申请日截止；Portal 模块九展示查询、命中、全文、日期和逐篇比对五组进度以及新文献
  I4-S。隔离 5201/5209 与 Portal 已重载，正式 5109 未修改、未启动、未 promotion。
- 同案 live 最终结果：4/4 查询成功、36 条原始命中、I3-F 保留 15 个唯一候选、15/15 取得
  analysis-ready 全文并完成日期流程、15/15 I4-S succeeded（每篇 5 项 disclosure，未出现单篇
  完整披露）。后端全量 `708 passed, 7 skipped, 1 failed`，唯一失败为既知样本 oracle 差异；
  Portal 类型、定向 ESLint、契约、production build 和登录态实际页面加载均通过。

## 2026-08-09 模块九逐特征五轮持久化循环

- 宪章升至 2.24、工作流规范升至 3.35：每个 gap 轮必须为每个仍未覆盖区别特征保留至少一条
  普通专利主检索线；辅助 NPL/抵触申请/组合或公知常识线不得占用该最低覆盖。新文献逐篇 I4-S
  后只关闭有可引用披露的特征，其余特征必须改词续轮，最多 5 轮。
- Portal 新增 `invalidity_test_module9_batches` 和 `/api/test/invalidity/module9-batches`，持久化每轮
  I2-G、I3 搜索/筛选/取文/日期资格、I4-S 及失败原因；URL 使用 `module9_batch` 恢复。旧浏览器
  单轮运行由 5209 `module9-cursor` 迁移，不再重复已完成第 1 轮。
- 后续轮查询禁止逐字复用上一轮普通专利主式；即使重复式由模型直接返回，也先剔除再按同上位
  类别、具体构件/产品类别或结构角色/动作/路径/效果词确定性补齐。辅助 common-context 项若绑定
  模型自创特征 ID，只删除该辅助项，不再中止整个 gap 轮；I2-G 守门失败由批次最多持久化尝试
  3 次。
- `active_gap_feature_ids` 成为单调收缩游标：从最早成功 gap 轮恢复，逐轮只做交集，传回后端
  再次守门；同一 D1 的较新 I4-S 更不确定时也不得重新打开已关闭/已处理特征。
- 指定案件 live 批次 `43c36e90-7906-4765-b351-04760a48940d` 从历史第 1 轮续到第 2 轮：5 条查询、
  46 命中、18 份可分析全文、18/18 I4-S 成功；随后自动第 3 轮改词，5 条查询、46 命中、29 份
  可分析全文、29/29 I4-S 成功，并自动进入第 4 轮。回归过程中发现并修复跨轮重复式、辅助画像
  守门后直接 partial、以及 gap 特征集合回扩三处边界；同一批次已恢复为最初 3 个 active gap
  特征并创建纠正计划 `afd1ebb3-3584-4fa9-b5e0-77b82b84d2f0`。
- 最终后端全量 `714 passed, 7 skipped, 1 failed`；唯一失败仍是外部样例目录新增 3 份 PDF 未登记
  oracle。Portal 类型、契约、定向 ESLint 和 production build 通过。隔离 5209 live 回归临时按
  宪章上限启用 4 worker；正式 5109 未修改、未重启。
- 后续 live 将第 4 轮 GLM HTTP 400、候选清理旧画像依赖和第 5 轮原子化跨特征误去重继续修复：
  I2-G 模型传输失败可仅凭冻结事实确定性编译；前三次模型计划后切换确定性计划；I3-F 接受
  I2-G 的技术主题+冻结 limitations；相同文本但绑定不同 gap feature 的主线分别保留。既有批次
  `43c36e90-7906-4765-b351-04760a48940d` 最终 `completed`，从历史第 1 轮续跑第 2—5 轮，页面
  显示“全部区别特征已取得可引用覆盖证据”，累计 27 条检索线、43 份可分析全文。最终全量
  `718 passed, 7 skipped, 1 failed`，唯一失败仍为同一外部样例 oracle 差异；正式 5109 未触碰。

## 2026-08-10 模块九一特征一检索组与界面简化

- 宪章升至 2.25、工作流规范升至 3.37。gap 轮的普通专利主线组数必须严格等于
  本轮未解决区别特征数，每组只绑定一项特征，形式固定为“排除目标完全相同类别的
  邻近/上下位客体中英文 OR 组 AND 特征点中英文/同义/上下位 OR 组”；NPL、抵触申请等只能
  作附加通道，不冒充区别特征检索组。
- I2-G 改为由确定性编译器统一组装主式，模型只供应可追溯候选词。旧案缺少双语 limitation 时，
  优先恢复同案同独权最新成功 I2 画像，仍缺词才做窄化查询语言补全，不要求重跑前七个模块。
  跨轮重复按 OR 成员集合判定，仅改词序不算新查询；未命中时保持同等组数并逐特征实质换词，
  最多五轮。`I2_RULE_VERSION` 与 `I2_PROMPT_VERSION` 均升至 v5。
- Portal 模块九改为一项区别特征一张检索卡，普通视图隐去 UUID、重复复用理由和技术细节；
  “缺少可复用版本键”简化为旧证据不直接计入覆盖、相关特征继续补检的说明；附加证据通道
  收入折叠区。Portal 控制器在持久化前再校验组数、一对一绑定、两个双语 OR 组和目标类别排除。
- 指定水陆喷水车 live 案第一轮 run `1992876e-dfbb-41a6-bbab-827d081285bd` 对 5 项特征生成
  5 组双语查询，客体组使用“车/vehicle/喷水车/喷水玩具/car”而未使用完全相同类别
  “水陆喷水车”。第二轮 run `65333baa-07d6-41b1-a827-d0344e68abfb` 对指定 2 项特征生成
  2 组查询，两组的 OR 成员集合均相对前轮实际变化。
- 后端全量回归 `720 passed, 7 skipped, 1 failed`，唯一失败是外部实例目录新增 3 份 PDF 未登记
  sample oracle；Portal `pnpm ts-check`、无效合同测试和 production build 通过，`patent-web`已重启。
  测试 5201/5209 已加载最新代码，正式 5109 未触碰。

## 2026-08-11 模块九逐轮拆分与失败文献定向重试

- 宪章升至 2.26、工作流规范升至 3.38。模块九仍是单一业务模块，但一次用户操作最多完整推进
  一个 gap 轮；本轮完成后停在 `awaiting_next_round`，只有律师点击“继续下一轮”才新建下一轮。
- 本轮 I4-S 有临时失败时停在 `round_partial`。系统保留成功检索、命中、全文、日期资格及成功
  I4-S，只为失败文献创建新 I4-S 尝试并保留旧 run 审计；律师也可明确保留失败并继续，失败项
  不得计为覆盖证据。
- Portal 改为按轮分组展示计划、真实检索、命中、全文、日期资格和 I4-S 成败，累计数字另列，
  不再把三轮累计 6 次检索除以单轮 2 条计划显示为“6/2”。Portal production build 通过并只
  重启 `patent-web`；`/login`=200、匿名模块实验室=307。正式 5109 未触碰。

## 2026-08-12 模块九轮次引导与逐篇比对可见性

- 最新批次 `87e4a02a-f468-4fe4-aa6a-d22fbe90dfc2` 实际从旧 I2-G 游标继承到第 5 轮，并非
  用户看到的新批次“第 1 轮”；该轮 25 份 I4-S 均已完成，随后按五轮上限终止，所以没有下一轮。
- 新 durable 批次只有在该案从未创建过 durable 模块九批次时才兼容迁移旧浏览器游标；同案
  已有 durable 批次后，点击重新测试必须从第 1 轮开始，避免旧批次自身的 I2-G 被再次继承成
  新批次第 5 轮。
- Portal 模块九新增轮次引导、继承历史说明、逐篇比对锚点和逐特征证据表；可继续时显示明确的
  “开启第 N 轮”，达到上限/已覆盖时显示禁用按钮和原因，不再把按钮静默隐藏。类型、契约、
  定向 ESLint 与 production build 通过；只重启 `patent-web`，`/login`=200、匿名页面=307。

## 2026-08-12 无效模块三模型输出完整性修复

- 当前消火栓案件的两次模块三失败不是偶发网络错误：相同输入均在约 1.2 万字符处返回未闭合
  JSON，原因是 profile-only 画像仍受 4096-token 上限约束。修复后模块三预算为 12288，普通
  首轮 I2 为 8192；JSON 截断/格式错误在模块内只重生一次，错误码与传输故障分离。
- 模型响应现在先保留原始内容、`finish_reason`、字符数、模型和输出预算再解析；失败响应也能
  冻结为审计工件。专项回归共 `339 passed`；测试 5201/5209 已重载健康。
- 同案真实回归 run `6e38f613-16fd-4dcc-a96e-03265e70f5d9` 一次成功，完整响应 10886 字符、
  `finish_reason=stop`，模型审计工件已冻结。正式 5109 未触碰。

## 2026-08-12 无效模块四检索词撰写外壳清洗

- 宪章升至 2.27，工作流规范升至 3.40，I2 规则/prompt 升至 v6。画像层保留专利完整原文，
  可执行检索层通用剥离“具有/具备/带有/设有/设置有/采用 + 技术含义 + 结构/装置/机构/系统/
  组件/部件/单元”等专利撰写外壳；例如“具有升降结构”编译为绑定原 feature 的“升降”。
- 客体与特征不再混组：“具有升降结构的室外消火栓”抽取“室外消火栓”为产品客体，误标成
  客体别名的“具有升降结构”在画像解析与固定五线编译时删除；核心词只进入相应特征组。
- 实现位于测试版 `invalidity/analysis.py`，不含当前案件词表；专项测试覆盖升降、旋转、吸水三种
  撰写外壳和消火栓固定五线，analysis 全量 `212 passed`。模块自身 `.venv` 全量回归为
  `724 passed, 7 skipped, 1 failed`，唯一失败仍是外部实例目录新增三份 PDF 未登记 oracle。
- 隔离测试 5201/5209 已重启在线；同案模块四 live run
  `49c2a9f6-f727-4c41-89cb-83852ba26a6c` 成功。五条真实 `provider_expression` 的客体组固定为
  “室外消火栓/消火栓/消防栓/消防设施/fire hydrant”，特征组保留“升降”，可执行式和普通
  展示画像均不再出现“具有升降结构”。正式 5109 未触碰、未 promotion。

## 2026-08-13 无效检索回退至全案例自测 goal 前

- 按用户明确要求，把无效检索相关代码、Portal 模块实验室接入、隔离 PM2 配置及对应测试/文档
  回退到 2026-08-12 10:13:12 PDT（“使用全部实例专利持续自测”goal 创建时刻）之前；该时刻
  对应用户所称 8 月 11 日版本。回退不删除后来生成的调查数据库记录或 `.data` 历史工件。
- 覆盖前版本已完整保存到
  `.data/rollback-backups/pre-goal-20260812T101312PDT-current-20260813T1920PDT/`，可恢复。
- 回退后无效模块全量回归恢复为当时基线：`724 passed, 7 skipped, 1 failed`；唯一失败仍是实例
  目录后来新增三份 PDF、而旧版 `sample-oracle.json` 未登记，与当时日志一致。正式 5109 未触碰。
- Portal 已按回退源码重新 production build 并只重启 `patent-web`；隔离测试 5201/5209 在确认
  queued/leased job 为 0 后重启，健康检查均为 `ok`。旧版启停脚本依赖调用环境 PATH 中存在
  `uv`，本次以 `/Users/xiexiaoxiong/.local/bin` 显式补入 PATH 启动，未改旧版代码。

## 2026-08-14 无效模块三检索级核心发明点

- 模块三不再把同一发明构思的多个结构细节逐条列成核心发明点。底层 `features` 和
  `mechanism_model` 继续完整保留；律师普通视图只显示 1—3 项检索级上位概括，并在每项下展开
  其绑定的原始技术特征。
- 候选只有 1—2 项时保持；超过 3 项时按共同技术目的或同一机构作用链归并为 1—3 项，归并项
  必须绑定底层 feature ID 并集，禁止截断丢失或代码猜造概括。超过上限的模型输出会携带原因
  有界重生一次。I2 prompt/rule 升为 v7，工作流规范升为 3.41。

## 2026-08-14 无效模块九案件重置与轮次入口

- 模块九页面把“fresh 第 1 轮”和“继续当前批次下一轮”拆成并列操作；等待下一轮时不再禁用
  第 1 轮入口。fresh 操作创建新批次、保留历史，并明确不继承 legacy module9 cursor。
- 新目标专利建立或调查作用域切换时，Portal 清除旧模块六/九 URL 参数、恢复键、内存批次和旧
  轮询；自动恢复按 investigation + 独立权利要求双重筛选。前端收到作用域不一致批次立即拒绝，
  API 同样 fail closed，避免同一用户不同案件之间串轮次。
- 工作流规范升至 3.42。模块九的 fresh 第 1 轮仍要求当前案件已有模块七 D1/区别特征与模块八
  I4-O，不把“重置”解释为跳过前置证据阶段。

## 2026-08-15 模块九五轮固定语义检索阶梯

- 用户确认模块九最多五轮不应在同一词池内机械换序。宪章升至 2.28、工作流规范升至 3.43：
  第 1—5 个 gap 轮依次固定为“相邻产品+直接结构”“上位设备+结构族”“同功能产品+动作/角色”
  “子系统部件+关系/路径”“类比领域+原理/效果”。每轮仍保持一项未解决区别特征恰好一组
  “双语客体 OR 组 AND 双语特征 OR 组”。
- I2 prompt/rule 升至 v8。模型只在当前轮语义视角内补候选词，确定性编译器从对应冻结词族取词；
  本轮词族不足时不得退回上一轮词池换序，必须明确失败或仅补齐本轮词汇。计划和每条查询新增并
  持久化 `gap_search_strategy` 与可读标签，workflow/lab 在保存前再次绑定，防止模型或旧调用方
  漏字段。
- Portal 模块九展示当前轮策略和完整五轮阶梯，明确不是同义词轮换。定向后端 375 项通过；全后端
  728 passed、7 skipped，唯一失败仍为回退基线已记录的 3 个新增样例 PDF 未进入旧 oracle；Portal
  `pnpm ts-check` 与无效检索契约测试通过。本次未启动服务、未执行 live 检索、未触碰正式 5109。

## 2026-08-15 模块六逐文献与逐特征两级折叠

- 律师测试页模块六不再把全部对比文件和全部逐项评价一次性平铺成长表。每份对比文件改为默认
  收起的第一层折叠项；收起状态仍显示文献身份、评价总数以及披露、未披露、待确认、分析失败
  数量，排队/执行时间和部分结果说明在展开后保留。
- 展开文献后，每项权利要求技术特征再作为可独立展开/收起的第二层折叠项；标题直接显示特征
  原文和判断标签，展开后显示对比文件原文、位置、分析理由及结构角色/映射。整体机构对照另设
  折叠区。该变更只调整 Portal 展示，不改变 I4-S 数据、状态、证据或后端运行语义。
- 验证：`pnpm ts-check`、`node --import tsx scripts/test-invalidity-portal-contracts.ts` 及改动文件
  定向 ESLint 全部通过。本次未 build、未重启 Portal、未执行 live 案件、未触碰正式 5109。

## 2026-08-15 律师模块实验室全阶段默认折叠

- 十一个律师工作阶段统一改为默认收起的模块卡。收起状态保留模块编号、名称、运行状态、业务
  问题及运行完成后的关键结论；运行按钮、输入/输出概括和详细结果只在律师主动展开模块后显示。
- 已完成模块的“本次实际输入”和“本次输出结果”再分别作为默认收起的第二层区域；既有技术运行
  记录继续默认收起。五案内置盲测的输入/输出及页面底部报告的逐项权利要求状态也使用同一交互，
  但数量摘要和成功/失败状态始终留在外层，避免折叠掩盖运行结果。
- 本次只调整 `IP-protral` 展示层，不改变任何 I0—I5 输入、证据、状态或运行语义。验证为
  `pnpm ts-check`、`node --import tsx scripts/test-invalidity-portal-contracts.ts` 和两份改动文件
  定向 ESLint 全部通过；未 build、未重启 Portal、未执行 live 案件、未触碰正式 5109。

## 2026-08-15 模块九第二轮近重复检索词只读诊断

- 批次 `e735c5b2-dc95-4faf-91bd-b17294d5f9ff` 是当前案件 fresh 批次，并非前端把两个案件或轮次
  混在一起；第 1、2 轮 I2-G 均因模型错误 `1261` 转入 `deterministic_gap_fallback`。两条已保存
  输出没有 v8 应有的 `gap_search_strategy` 字段，且生成时运行中的 5209 未加载磁盘上的最新
  v8；历史批次输出不会因后端代码更新而自动重写。
- 同时确认当前 v8 源码仍有独立守门缺口：第 1、2 轮客体池共同包含 `heads/core_terms`，跨轮校验
  只比较整条表达式的 OR 成员集合。删除一个词或替换一个英文表达即可通过，尚未逐组证明客体已
  从“相邻产品”切换为“上位设备”、特征已从“直接结构”切换为“结构族”。因此该结果既有旧服务
  未重载因素，也不能仅靠重启解决；后续修复应收紧逐组语义池互斥与跨轮语义守门。本次按用户问题
  只完成只读诊断，未修改生产逻辑、未重启服务、未改数据库、未触碰正式 5109。

## 2026-08-15 模块九 v9 五轮逐组换族与前端折叠发布

- 已确认批次 `e735c5b2-dc95-4faf-91bd-b17294d5f9ff` 的前两轮近重复既包含旧 5209 未加载
  v8，也包含 v8 本身的守门缺口：第二轮继续混入第一轮 `heads/core_terms`，跨轮仅比较完整
  expression 集合，删词或替换译法即可通过。
- I2 prompt/rule 升至 v9。gap 轮在有首轮冻结画像时改走紧凑的“当前轮专用词表”调用，不再
  重生整套画像/查询大 schema；模型只返回一组双语客体类别及每个未覆盖区别特征的一组双语
  本轮词，系统确定性编译。当前策略词会替换而非扩充旧策略池；客体组与对应 feature 特征组
  都必须相对上一轮实质换族，同族删词、换序、只换译法或修改装置/结构外壳一律拒绝。模型
  失败且没有本轮专属冻结客体词时 fail closed，不再输出看似完成的重复轮次。
- 真实冻结 run `631d8c9a-58e2-4ecc-b90e-3e689d3a8d20` 的 CN222140203U 三项区别特征已完成
  纯本地五轮编译验收，工件为
  `.data/invalidity/test/regression/gap-ladder/CN222140203U-i2v9-five-rounds-offline.json`，明确
  标记 `network_used=false`、fixture model，不能冒充真实 GLM。针对该具体专利首页及三项区别
  特征的真实 GLM 外发被运行权限拒绝，需用户对该具体内容和测试 GLM 再作明确确认后运行
  `scripts/verify_gap_query_ladder.py` 的 live 模式。
- 后端 `tests/test_analysis.py` 为 217 passed；workflow/lab/API/runner 定向 211 passed；全后端
  729 passed、7 skipped、1 个既有样例 manifest 失败（实例目录多出三份 PDF，旧 oracle 未登记）。
  5209 已在 queued/leased=0 后重载，`/health` 明确返回 I2 prompt/rule v9；正式 5109 未触碰。
- “所有模块默认折叠”此前确已写入源码，但源文件时间晚于 `dist/.next`，所以用户看到的是旧
  构建。本轮 production build 成功并只重启 `patent-web`；新构建含 11 个顶层模块默认折叠、
  已完成模块输入/输出二级折叠、模块六逐文献/逐特征折叠。`pnpm ts-check` 和 Portal 契约测试
  通过，3001 模块实验室匿名访问按预期跳转登录；浏览器插件当前无可用实例，未伪称完成点击验收。

## 2026-08-15 模块九五轮矩阵一次生成与逐轮执行

- 宪章升至 2.29、工作流规范升至 3.45。fresh 模块九批次先连续生成、逐轮交叉校验并冻结完整
  五轮检索矩阵，全部计划就绪后才执行第 1 轮；律师后续按钮只执行已冻结的下一轮，不再在每轮
  结束后临时生成关键词。
- 每轮仍按未解决区别特征逐项执行；某特征取得带引文和位置的可引用披露后，后续矩阵行保留并
  标为 `skipped_feature_resolved`。后轮重复命中文献仍保留新 query/rank/round 血缘；只有冻结
  文献版本/全文哈希、日期资格、矩阵输入、I4-S prompt/rule 全部一致时，才复用旧取文、资格和
  I4-S 结论，否则重新处理。
- Portal 顶部一次展示五轮矩阵，每轮和每个实际执行结果均默认折叠；按钮已改为“生成五轮并执行
  第 1 轮”与“执行第 N 轮”。生产构建通过并只重启 `patent-web`；无在途测试 job 后重载 5201/
  5209，健康页公开当前 I2 v9 与 I4-S v27/v50。后端全量 729 passed、7 skipped，唯一失败仍是
  已记录的 3 个新增 PDF 未进入旧 oracle；Portal 类型、契约和定向 ESLint 均通过。正式 5109 未触碰。

## 2026-08-15 模块十三步法逐区别特征证据闭环

- 宪章升至 2.30、工作流规范升至 3.46。模块十 `I4-I` 不再只做一组全局“组合动机/反向教导/
  技术效果”模板判断：第一步固定模块七当前 D1，第二步逐项固定 D1 区别特征和实际技术问题，
  第三步对每项区别特征分别输出补充文献/公知常识公开、相同结构角色和作用、具体技术启示、
  修改动机与路径、反向教导、效果可预期性以及引文位置。
- I0 不再在进入 I4-I 前把模块九语料裁成 D1+2 篇。I4-I 接收最多五轮形成的全部当前、日期合格
  I4-S 结果并记录 `considered_document_ids`，再按区别特征有效增量选择 D1+最多5篇实际组合
  文献。累计覆盖全部限制只算必要条件；逐项证据链全部闭合才输出“现有证据已形成缺乏创造性
  的完整证据链（供律师复核）”，否则只输出“现有证据尚不足以证明不具备创造性”，不得反向
  宣告专利具备创造性或有效。I4-I prompt/rule 冻结为 `i4-i-three-step-v1` /
  `inventive-step-three-step-v1`，5209 health 对外可核验。
- Portal 模块十改为默认折叠的四区：D1、区别特征/技术问题、逐区别特征第三步证据、未解决 gap；
  每项区别特征还能继续展开查看引文、启示、动机、修改路径、反向教导和效果。生产构建成功，
  只重启 `patent-web`；测试 5201/5209 在 queued/leased=0 后重载，正式 5109 未触碰。
- 验证：分析/工作流 270 passed，API/runner 52 passed，Portal `ts-check`、定向 ESLint、契约测试
  与 production build 通过；CN217770321U 单案离线 fixture I1— I5（含 I4-O/I4-I）通过并实际
  生成逐区别特征三步法字段。后端全量为 730 passed、7 skipped，仅旧 sample oracle 未登记目录
  新增的三份稳定性弱 PDF 导致 1 个已知 manifest 失败，与本次逻辑无关；fixture 非真实现有技术
  或法律证据。

## 2026-08-15 模块六到模块七批次与特征实质绑定

- 指定案件 `c33a4cfa-6f22-5368-9672-fee789f0ec3a` 的模块七曾选择模块六页面未展示的
  `CN206949116U`。根因不是 D1 排名公式，而是 Portal 创建 I4-C 时没有传模块六批次号，后端
  实验室又错误读取废弃字段 `module5_batch_id`，使当前工作流 I4-S 和其他模块六批次可进入
  候选集合。该次模块七结论作废。
- 模块七 live 现在强制绑定当前目标、当前独立权利要求且已完成的 `module6_batch_id`；后端只
  接受该批次的 I4-S run，缺批次直接 fail closed。模块六最新批次恢复也按 investigation +
  claim 双键查询，自动把历史工作流 I4-S 包装成新模块六批次的路径已移除。
- I4-S 复用不再只比较特征顺序或显示序号。候选必须同时通过当前 investigation/claim、当前
  模块六批次、完整 feature ID 集合和每项 `feature_text` 实质原文校验；新建模块六 I4-S 另冻结
  目标专利 SHA、展开权利要求 SHA、limitation-set SHA、文献版本/全文 SHA 及 prompt/rule。
  不一致文献被排除并在 `rejected_lab_comparisons` 留下原因，不能静默进入 D1 排名。
- 验证：module-lab 107 项通过；后端全量 731 passed、7 skipped，唯一失败仍是外部实例目录
  新增三份 PDF 未登记旧 oracle；Portal `pnpm ts-check`、契约测试及三份改动文件定向 ESLint
  通过。指定 I4-S run `0a9d7661-e1e1-4a38-b424-23b8205f4b10` 已只读核验携带批次
  `1fccc90f-1319-433d-aff3-e93ec154d413`，并逐项保存内容哈希型 feature ID 与完整特征原文。
- 修复后对同一批次创建 I4-C run `f78d5fa5-8d30-4729-abf1-d826794bf4b3`，候选严格为该批次
  12 篇，`CN206949116U` 不再出现；D1 改为 `CN205987792U`，`CN115088594A` 排第二，二者在
  当前确定性字段上均为 4 项确认、2 项待确认、证据完整度 0.6667 的完全同分。测试 5201/5209
  已在无在途 job 时重载，Portal production build 成功并只重启 `patent-web`；正式 5109 未触碰。

## 2026-08-15 模块五全文精确复用

- 用户更正为“模块五取文避免重复”。`I3_FETCH` 现在于 EPO/P020 前按带 kind code
  的完整公开号查找同环境历史成功全文，只有原文字节数/SHA-256、可读化 JSON
  哈希、标准化版本和全页状态全部通过才命中。A1/B2/U 不混用，不按标题、
  同族、主号或特征序号模糊复用；损坏或不匹配时回到正常 provider 链。
- 可读化版本有效时跳过网络取文和 OCR；版本过期时只从已验证的冻结 PDF/EPO bundle
  本地重建。当前 query/candidate/I3-F lineage 和日期资格仍重做，模块六仍按当前目标专利、
  当前独立权利要求和特征实质重新比对，历史 disclosure/I4-S 结论不随全文复用。
- 已增加精确公开号数据库索引、环境路径与工件哈希守门，前端模块五命中时显示
  “已复用、未重复下载”。验证为缓存定向 4 项、module-lab 111 项、DB 合同定向 2 项
  通过；全后端 736 passed、7 skipped，唯一失败为已知外部实例目录新增 3 个 PDF 未登记
  旧 oracle。测试 schema 无在途 job 后已重载隔离 5201/5209，5209 健康且缓存索引
  存在；Portal production build 成功并只重启 `patent-web`，模块实验室路由匿名访问按预期
  返回登录跳转。正式 5109 未触碰。

## 2026-08-16 模块九零检索误报“全部区别特征已覆盖”诊断

- 指定 run `6410ce62-dc5e-40ec-8c13-2d7829311453` 的模块八前序 run
  `9d2c1d63-54db-4dbc-86a8-6fa16d9efd19` 明确保留一个未解决区别特征
  `1-f-9483db6cc604`（“蓄电池与水泵相连”），路线为
  `search_direct_feature_evidence`；但模块九把该特征改为 covered，生成 0 条查询、没有创建
  I3/I4-S 子运行并直接把批次标为 completed。
- 根因一是实验室 I2-G live 路径读取整个 claim checkpoint 的全部 `comparisons`，没有绑定当前
  模块六批次 `525a0592-39a2-417e-89e4-7f9be376136c`。实际闭合证据混入了不属于该批次的历史
  `CN108029521A`、`CN206949116U`、`CN208650150U`；这与“每个业务模块只消费当前前序模块
  结果、其他批次不得静默混入”的既定边界冲突。
- 根因二是复用键构造把缺失值 `None` 转成字符串 `"None"` 后再用 `all(values.values())`
  判断完整，导致当前批次 `CN111213574A` 在缺少 document version/date qualification revision
  的情况下也被误计为可复用覆盖。其引用“所述一体化灌溉机与所述控制装置电连接”本身也没有
  直接证明“蓄电池与水泵相连”。
- Portal 只用 `coverage_evidence` 判断是否显示“缺少复用版本键”提示，没有渲染 covered 文献
  的公开号、引文、位置和 I4-S run；因此页面只显示“全部区别特征已取得可引用覆盖证据”，用户
  看不到系统实际拿了哪些旧文献作闭合。本轮仅做只读诊断，未修改运行数据、代码或重新执行案件。

## 2026-08-16 无效实验室模块八至十单次运行隔离

- 用户最终确认：无效实验室“一次就是一次，每次单独分析”。模块八、九、十现以当前模块六批次
  加精确模块七/八/九 run 组成不可跨越的业务谱系，不再把同一 claim checkpoint 中的历史 I4-S
  当成本次输入；历史工作流、其他模块六/九批次、仅序号相同或 feature ID 巧合相同的结论均不得
  静默混入。
- 指定异常 run `6410ce62-dc5e-40ec-8c13-2d7829311453` 的旧结论不可信：其模块九 0 查询覆盖
  来自跨批次 comparison 和把 `None` 字符串误当版本。新代码要求每个关闭特征来自当前模块六
  批次并具备引文、位置、同角色/同作用及完整文献/限制/日期/I4-S 版本键；缺任何一项都继续检索。
- Portal 新建模块九批次会冻结并回查当前模块六、模块七、模块八三项来源；旧批次缺谱系直接要求
  重建。模块九页面默认收起展示所复用的文献、公开号、引文、位置、理由和 I4-S run；模块十只
  消费与当前三项来源一致且已完成的模块九批次。
- `docs/invalidity/WORKFLOW_SPEC.md` 升至 3.48。后端分析/实验室回归 332 passed；全量仅保留已知
  sample oracle 漏登 3 个外部新增 PDF 的 1 个失败。Portal 类型、契约、定向 ESLint 与 production
  build 通过。测试 5201/5209 和 3001 `patent-web` 已分别安全重载，健康检查正常；正式 5109、
  3002 测试副本均未触碰。

## 2026-08-16 模块11三部分律师报告

- 用户确定模块11固定包含三部分：将模块10当前三步法结构化结论转写为人类可读分析；展示相似度
  最高最多10篇对比文件与独立权利要求全部特征的横向矩阵；保留当前案件全部已分析对比文件的
  逐篇完整比对。三部分在律师页面默认收起。
- 文字版只转写模块10已经冻结的 D1、区别特征、实际技术问题、补充公开、相同作用、技术启示、
  修改动机/路径、反向教导、效果可预期性和证据结论，不调用模型补写。表达结构参考既有无效
  决定书，但决定书答案和其中对比文件不得进入运行时案件。
- Top 10 的“相似度”定义为本次 I4-S 确认披露的权利要求特征数/覆盖率优先；`uncertain` 不计
  披露，再按确定性评价完整度排序。它不同于 I4-I 的 D1+最多5篇组合增量表，后者继续保留为
  内部组合证据选择。第三部分不受 Top10 截断，且不得混入其他案件或历史批次。
- `PROJECT_CHARTER.md` 升至2.31，`WORKFLOW_SPEC.md` 升至3.49；后端 ReportDataV1、模块实验室、
  Excel 导出和契约测试已按该口径改造，最终验证与部署结果在本节后续补记。

### 2026-08-16 发布验证补记

- 后端报告合同及实验室定向回归 119 项通过；后端全量为 740 passed、7 skipped、1 failed，
  唯一失败仍是外部实例目录新增三份 PDF 未登记旧 sample oracle，与模块11改造无关。
- Portal `pnpm ts-check`、无效实验室契约、定向 ESLint 和 production build 全部通过。测试环境
  5201/5209 与 3001 `patent-web` 已安全重载，5201/5209 健康检查正常，登录页返回 200；正式
  5109 未触碰。
- 已在重载后的 5209 运行单案 fixture I5 验证：报告实际生成 1 份创造性文字分析、1 张 Top10
  矩阵（fixture 语料实际含 2 篇文献）及 4 条全部文献逐特征披露记录，未使用真实网络。
- 尝试以浏览器技能做登录态点击验收时，当前会话没有可用 Chrome/内置浏览器实例；因此本轮页面
  验收以通过的生产构建、前端合同和 HTTP 健康检查为准，未冒称完成登录态视觉验收。

## 2026-08-16 正式首页单对话框专利 Agent

- 正式 Portal 首页已从固定侵权上传页改为单一 ChatGPT 式对话框；测试页、模块实验室及其现有
  路由不变。输入区提供“专利无效”和“专利侵权分析”两个独立显式开关；按钮状态直接确定普通
  问答、单项分析或双项分析，服务端不再从文字或模型意图推断是否调用工作流。
- 只有用户选中对应按钮并提供附件、专利 URL 或足够的专利正文时才创建分析任务。侵权分析复用既有
  `/api/analyze` 正式链路；无效分析只复用 `/api/invalidity/investigations` 的 prod/5109 链路并按
  5 轮自动检索启动，绝不回退 5209。明确同时要求两项时分别创建两个任务，单项失败不抹掉另一项。
- 新增 `patent_agent_conversations/messages/tool_runs` 三张 owner-scoped Postgres 表，以及对话列表、
  对话详情和消息提交 API。公开附件记录只保存文件名、大小和 MIME；绝对路径不进入对话响应。
  工具状态通过既有 analysis session 刷新，结果入口继续指向原侵权结果页和正式无效结果页。
- 普通文字问题调用已配置的 GLM 问答模型且不调用两套分析工具；TXT 附件可作为问答文本。普通
  问答若只附 PDF/DOC/DOCX，模型只看到文件名并明确要求粘贴待解读文字；要分析整份材料必须
  选择对应按钮，系统不得伪装已经读取二进制正文。
- 验证：新 Agent 合同、既有无效 Portal 合同、`pnpm ts-check`、新增文件定向 ESLint 和完整
  production build 全部通过；只重启专用 PM2_HOME 中的 `patent-web`。运行时 `/login`=200、匿名
  `/`=307 到登录、Agent API=401、匿名模块实验室=307。5109、5209 和测试页均未重启或修改。
- 同轮再次核验模块11：报告合同与实验室 119 项通过，当前构建仍包含模块10文字版、Top10横向
  特征矩阵和全部文献逐篇比对三部分，三部分默认收起；本次 Agent 改造未改变模块11事实口径。

## 2026-08-16 两个大型测试页面入口

- 用户明确把 Portal 普通测试产品收敛为两个大型页面，不再维护复杂的用户侧测试目录：Agent
  侧边栏在原无效测试入口位置并列显示“无效检测测试版”和“专利分析模块测试版”。前者直达
  `/test/module-lab` 的 11 模块律师实验室，后者直达 `/test/product-pipeline` 的模块 2、3、4
  分步/全链测试。
- 其他 `/test/**` 路由仍可作为内部调试或历史深链保留，但不得出现在 Agent 普通导航中；专利分析
  统一页已移除模块1、旧模块3、安盾模块3等顶部零散入口，无效实验室返回按钮直接回 Agent，正式
  无效页的测试按钮也统一指向 11 模块实验室。
- `PROJECT_CHARTER.md` 升至 2.33。Portal 类型检查、Agent/无效两套合同、定向 ESLint 和 production
  build 全部通过；只重启专用 PM2_HOME 的 `patent-web`。运行时 `/login`=200，匿名 `/`、
  `/test/module-lab`、`/test/product-pipeline` 均 307 到登录页；5109、5209 未重启或修改。

## 2026-08-16 专利分析测试页四模块输入输出

- “专利分析模块测试版”仍使用唯一入口 `/test/product-pipeline`，但页面结构已与无效检测实验室
  对齐：顶部输入一件测试专利，下面依次提供模块1专利解析、模块2关键词生成、模块3商品检索、
  模块4权利要求与商品比对。没有新增测试产品、路由层级或侧边栏入口。
- 每张模块卡、每个模块的实际输入/输出及长列表默认收起；展开后可查看运行状态、失败原因、可读
  结果和完整原始数据。模块2行业和模块3检索预算放在对应模块内部，页面不再把全部参数堆在首屏。
- 统一测试 API 新增模块1完整运行入口，解析成功后把其 `patent_record_id` 与本页新建的
  `analysis_session_id` 交给后续模块。后续模块必须逐级使用当前会话的上游结果；重新输入专利或
  重跑上游会清空下游，禁止静默复用其他会话的关键词、商品和比对记录。
- Portal 类型检查、Agent/无效两套契约、定向 ESLint、差异格式检查和 production build 均通过；
  仅重启 `patent-web`。运行时登录页返回 200，两个大型测试页匿名访问均 307 跳登录；无效测试
  5209 和正式无效 5109 均未重启。

## 2026-08-16 Agent 分析按钮成为唯一工作流开关

- `PROJECT_CHARTER.md` 升至 2.35。Agent 输入区附件按钮旁新增“专利无效”和“专利侵权分析”两个
  独立多选按钮，可以单选、双选或都不选；每次成功发送后重置，避免跨消息误启动。
- `POST /api/agent/messages` 只按经白名单校验的 `analysisKind` 列表启动工具。未选择时不论文字内容
  和附件类型均只进入 GLM 普通问答；选择但没有可分析专利来源时只要求补充材料，不创建后台任务。
- 首页说明与输入区状态提示同步解释该规则，引导问题使用“什么是专利的公开日？”等普通问答，
  不再用“对附件同时做侵权和无效分析”诱导同时启动两条流程。
- 验证：Agent 契约、无效 Portal 契约、`pnpm ts-check`、四份改动代码定向 ESLint 和 production
  build 全部通过；仅重启专用 PM2_HOME 中的 `patent-web`。运行时 `/login`=200、匿名 `/`=307、
  未登录 Agent API=401；5109/5209 未重启。

## 2026-08-16 Agent 正式无效启动配置诊断

- Agent 附件启动时报“缺少 `INVALIDITY_PROD_UPLOAD_ROOT`”不是上传逻辑回退，而是正式无效环境从未
  完成初始化：Portal `.env.local` 只有测试 5209 的 URL/token/upload root；正式
  `5-invalidity-search-prod/.env.prod.local`、`CURRENT_RELEASE`、正式上传目录和 5109 监听均不存在。
- 已运行仓库既有的隔离初始化器 `node ops/pm2/bootstrap-local-isolated-env.mjs prod`：创建权限 700 的
  `5-invalidity-search-prod/.data/uploads/prod`，生成权限 600 的正式环境文件和独立 API token，并把
  `INVALIDITY_PROD_API_URL/API_TOKEN/UPLOAD_ROOT` 原子写回 Portal 环境。测试 token、schema 和目录未复用。
- 正式服务仍不得启动：配置守门确认缺少正式 `INVALIDITY_PROD_PATSNAP_API_KEY` 与成对 EPO OPS 凭据，
  且尚无经 promotion 的 `CURRENT_RELEASE`。不得为了消除前端报错把 5209 或测试 provider 凭据静默回落到
  正式 Agent；应取得正式凭据、完成当前 release 验证/promotion、启动 5109 后，再重启 `patent-web`
  加载已生成的正式 Portal 变量。

## 2026-08-16 Agent 正式专利无效服务上线

- 用户明确将当前部署定义为正式环境，并授权正式配置使用已配置的测试智慧芽/EPO
  provider 值。该复制必须由 `prod --reuse-test-provider-credentials` 显式执行；正式 API token、
  `invalidity_prod`、上传/工件目录、5109、PM2_HOME 和不可变 release 继续隔离，运行时禁止读取
  `INVALIDITY_TEST_*` 或回落 5209。
- 已晋级 release `20260816T153757Z-3fbc5ba58c5d`，并启动正式 5109。健康返回
  `status=ok`、`environment=prod`、`worker_concurrency=2`；配置静态核验为端口 5109、schema
  `invalidity_prod`、工件目录 `invalidity/prod`、最多 5 个 gap 轮，正式凭据齐备且测试凭据未进入子进程。
- 正式 Portal production build 成功，只以专用 PM2_HOME 重载 `patent-web`。运行验收为
  `/login=200`、匿名 `/=307`、未登录 Agent API `=401`；Agent/无效 Portal 契约与 `pnpm ts-check`
  均通过。
- Agent 后续只在用户提供专利文件/需求并显式选中“专利无效”后，启动既有 I1—I5 和最多
  5 个 gap 轮。未选中时只走普通问答；本次配置与发布没有创建或运行具体专利案件。
- 受控 macOS 工作区会恢复 release 目录 mode，promotion 改为同时写入禁止新增/删除的 ACL；
  启动守门按实际可写性、只读文件和全量 manifest 检查，不放宽正式内容完整性。
- 当前后端全量为 740 passed、7 skipped、1 failed；唯一失败是外部实例目录新增 3 份 PDF
  未登记旧 `sample-oracle.json`，与本次正式配置/运行无关。按用户要求未做 20 案 live 回归。

## 2026-08-16 两项业务前端统一到各自唯一后端

- 用户纠正“正式版/测试版”含义：Agent 与模块实验室只是同一业务的自动视图和逐模块调试视图，
  不得各连一套后端。`PROJECT_CHARTER.md` 升至 2.37，`WORKFLOW_SPEC.md` 升至 3.51。
- 无效分析的 Agent、`/invalidity` 和十一模块实验室统一调用现有 5209/`invalidity_test` 模块链；
  Portal 新增中性 `INVALIDITY_API_URL/API_TOKEN/UPLOAD_ROOT` 配置并兼容读取既有
  `INVALIDITY_TEST_*` 同值。所有新用户请求禁止进入 5109/`invalidity_prod`。
- 专利分析的 `/api/analyze` 不再调用旧 5105 商品检索链，而是自动复用
  `/api/test/product-pipeline` 的同一执行器，按专利解析 → 行业选择 → 模块2 →
  `3-product-search`（默认 5107）→ 模块4 → 结果汇总推进；商品详情从
  `product_detail_search_products` 回填到 Agent 结果页。
- 专利分析模块地址统一使用 `PATENT_ANALYSIS_*` 中性变量，迁移期兼容既有 `TEST_*`/`MODULE*`
  变量；实验室与 Agent 的 service resolver、数据库主键和结果合同一致。旧
  `executePipeline` 只保留历史会话迁移审计，不接受新请求。
- 已通过 Portal TypeScript、Agent 契约、无效 Portal 契约、定向 ESLint和 production build；
  只重载 `patent-web`，未重启分析后端。运行核验：`/login`=200，匿名 Agent 首页和两个模块实验室
  均按设计 307 跳登录；5101、5102、5107、5106、5209 的 `/health` 均为 200。

## 2026-08-16 Agent 无效附件 fileKey 身份修复

- Agent 保存无效附件和无效调查保存纯文本专利时，仍生成了不带环境身份的历史格式
  `invalidity-<timestamp>-...`；唯一后端的严格解析器要求 `invalidity-test-...`，因此在任何 provider
  调用前正确拒绝并显示“上传 fileKey 不属于 test 环境”。问题位于 Portal 生产端，不在 5209。
- 两个生产端现统一把 `CANONICAL_INVALIDITY_ENVIRONMENT` 写入 fileKey；解析器继续严格拒绝其他
  环境和无环境键，没有放宽跨环境归属校验。新增 Agent 合同同时拒绝旧生成模板，覆盖文件和纯文本入口。
- Agent/无效 Portal 契约、TypeScript、定向 ESLint 和 production build 均通过；只重载
  `patent-web`。运行时 `/login`=200、匿名首页=307、5209 `/health`=200。失败的旧消息不自动重放，
  用户重新提交附件后使用新键。

## 2026-08-16 Agent 消息内实时事项进度

- Agent 的侵权/无效工具卡不再只显示笼统“运行中”。前端每 3 秒读取对应 owner-scoped session
  API：侵权链把现有六个执行步骤归并为四个用户业务事项；无效链依据当前调查及独立权利要求状态
  映射到十一项律师流程。两项同时运行时分别刷新，互不覆盖。
- 卡片始终突出一个真实“当前事项”、后端返回的说明和“已完成 X / N 项”分段条；不按时间推算
  百分比。全部事项明细使用原生 `details` 默认收起，等待人工、部分完成、失败和取消分别显示，
  结果页入口继续保留。
- 新增纯前端状态投影 `IP-protral/src/lib/agent-task-progress.ts` 及合同样例，覆盖侵权模块内半完成和
  无效第 3 个检索迭代。Agent/无效合同、TypeScript、定向 ESLint 和 production build 均通过；
  只重载 `patent-web`，`/login` 返回 200。浏览器连接本轮不可用，因此未冒称完成登录态截图验收。

## 2026-08-16 独立无效入口退役

- 用户确认不再需要独立 `/invalidity` 页面：正式无效调查只从 Agent 的“专利无效”显式开关启动，
  逐模块观察只使用 `/test/module-lab`。旧路由保留为返回 Agent 的兼容入口，避免历史书签 404。
- `/invalidity/results` 与全部无效 API、报告导出和人工复核能力继续保留，Agent 和历史记录仍可直达
  具体结果；结果页和内部技术页不再提供返回旧上传页的链接。

## 2026-08-16 Agent gap 续跑确认与 I2-G 上位客体修复

- 上次 Agent 调查 `be38f278-634f-5bfa-9c99-c267b65d95bd` 不是 PM2、5209 worker 或环境变量中断；
  第 3 个持久化迭代的 `I2_GAP_QUERY_PLAN` 把“消防设施”等上位类别误判为目标“室外消火栓”的
  完全相同类别，连续两次词表响应未过守门后以 `query_plan_failed` 收口。受限 shell 连接 PM2
  socket 的 `EPERM` 只影响诊断命令，5201/5209/Portal HTTP 与 2 个 durable worker 均在线。
- `I2_RULE_VERSION` 升至 `i2-query-plan-v10`：完全相同类别集合不再吸收以设备/设施/器材/装置等
  上位类结尾的画像 synonym；模型混合返回 exact 和合格上位/邻近客体时，确定性层移除 exact 词、
  写入 invocation audit/portfolio warning，剩余仍双语即继续，过滤后缺失任一语言才 fail closed。
- 新建 Agent 工具使用 `patent-agent-tools-v2`。无效调查为 `partial`、claim 仍在五轮上限内时，消息
  工具卡弹出 30 秒下一轮确认；点击立即启动，超时从后端 checkpoint `updated_at` 计算并自动启动。
  新 owner-scoped POST 接口重新校验 tool/session/investigation/claim、`state_version` 和轮次，每次
  只创建一轮并以 tool run + state version 幂等。`needs_human_review` 只弹人工表单入口，不超时确认。
- 验证：后端 `test_analysis.py` 219 passed；Portal Agent/无效合同、`pnpm ts-check`、定向 ESLint、
  倒计时状态投影断言和 production build 通过。确认测试 schema 无 queued/leased job 后只重载 5209，
  健康返回 2 workers 与 `i2-query-plan-v10`；只重载实际 PM2_HOME 中的 `patent-web`，登录页 200、
  匿名首页 307、新续检接口匿名 401。历史失败任务不自动重放，新 v2 Agent 任务使用新合同。
  浏览器实测可渲染登录页并正确从首页跳转；当前浏览器没有登录会话，本轮未输入账号密码，故不冒称
  已完成登录态倒计时弹窗截图验收。

## 2026-08-16 无效任务统一为 Agent 用户视图与管理员同任务诊断

- 用户确认每次无效分析只有一份任务数据：普通用户在 Agent 消息工具卡中直接查看逐独立权利要求
  状态、通俗解释、证据数量、未解决缺口和“检索未命中不等于专利有效”的边界；Agent 不再把新无效
  任务的“查看结果”导向 `/invalidity/results`，关键日和其他律师确认也在 Agent Dialog 内完成。
- `/test/module-lab` 改为 approved admin 专用。管理员从 Agent 使用
  `?session=<analysis_session_id>` 打开时，新的只读 API 从该 session 解析唯一
  `invalidityInvestigationId`，读取同一 5209 investigation、claims、module runs/jobs、错误和报告预览；
  页面禁止模块六/九恢复器和全部运行按钮，查看、轮询、刷新都不复制调查、不创建 module run、不改写
  session。退出同任务诊断后仍可显式使用原实验室，新运行继续保留审计记录。
- 历史 `/invalidity/results` 仅保留兼容深链/导出；旧页面的 session-scoped 人工复核改为使用正式
  Portal session 路由。`PROJECT_CHARTER.md` 升至 2.39，`WORKFLOW_SPEC.md` 升至 3.53。
- 验证已完成：Portal `pnpm ts-check`、两组合同测试、改动文件定向 ESLint、Webpack production
  build 和 tsup 均通过；只重启专用 PM2_HOME 中的 `patent-web`，5209 未重启且 `/health=200`。
  浏览器用既有管理员登录态实测指定 session：Agent 直接显示该案 36 份资料、216 条披露、58 个
  未解决缺口与证据边界且无旧结果页链接；同 session 模块实验室显示相同专利/claim/report，模块
  1—3 与 Agent 状态同步，模块6 `partial`、模块9 的 I2-G exact 客体错误明确可见，当前案件和 fixture
  运行按钮在只读模式禁用，控制台无错误。匿名模块实验室 307、管理员诊断 API 401。

## 2026-08-17 清理陈旧实验检查点并续跑同一 Agent 正式任务

- 数据审计确认不存在第二个 queued/leased/running 调查作业；管理员诊断页也没有复制调查。按用户
  “清除之前测试任务”的要求，将 1 个陈旧模块六批次和 4 个陈旧模块九等待批次标记为 `cancelled`，
  保留全部不可变 module run、证据和事件审计；当前 Agent investigation
  `be38f278-634f-5bfa-9c99-c267b65d95bd` 未被清理或重建。
- 5201/5209 的隔离 PM2 进程与 durable worker 始终在线；受限 shell 对本机 PM2 socket/localhost 的
  `EPERM` 只影响诊断，提升为本机只读检查后确认并非 PM2、端口、数据库挂载或环境变量故障。
- 第 4 个 gap 策略的两次模型词表都把“子系统/部件类别”重复写成消火栓/消防接口/消防设备，并重复
  上轮活动槽、固定块和滑动安装词族；`i2-query-plan-v10` 正确 fail closed，未为跑通而放松证据规则。
  随后按用户已明确授权续跑总轮次 6，即第 5 个 gap 策略“类比领域 + 工作原理/技术效果”。
- 最后一轮成功完成 I2-G、3 组专利检索、候选过滤、7 篇 I4-S、I4-C、I4-O、I4-I 与 I5 报告；作业
  `0ef927f7-865b-480e-b245-9e98a72bbf46` 为 `succeeded`。因 3 组详情抓取/日期核验均有部分文献未取得
  真实全文，最终调查按证据边界收口为 `partial`，不得写成检索穷尽或专利稳定。
- Portal session `analysis_1786899314010_6vx5sj` 已同步为 `completed/partial`；历史 v1 Agent tool run
  `4ae66e40-f84b-4cf4-9ab0-9b37cf5ea8ea` 也从遗留 `queued` 同步为 `completed`，避免工具卡继续显示
  在途。管理员浏览器实测
  `/test/module-lab?session=analysis_1786899314010_6vx5sj` 只读显示同一 CN222140203U 调查、43 份资料、
  258 条逐项披露、88 个未解决缺口以及十一阶段运行状态；模块10、11已完成，查看/刷新未创建新任务。
- 修复 Agent 终态进度投影：调查可以因 gap/provider 不完整而整体 `partial`，同时 I4-I 与 I5 已按当前
  证据成功完成；前端现在读取报告中 succeeded module runs，将模块10、11显示为已完成，并把说明从
  “正在推进”改为“第6迭代已收口，组合分析和报告已完成”。合同、TypeScript、定向 ESLint、Webpack
  production build 和 tsup 均通过；只重启 `patent-web`，5209 未重启且 3001/5209 均为 200。浏览器
  复核 Agent 为 10/11、模块9 partial、模块10/11 completed，管理员同 session 页面结果一致。
- Agent 结果摘要不再把终态 `partial` 写成“需要继续处理”，现明确显示“仍有证据缺口；本次自动任务
  已结束”，并说明证据/报告已保存、继续补强须由律师决定。该文案同样通过合同、类型、ESLint、生产
  构建与登录态浏览器复核，避免用户把证据不完整误解为后台仍在运行。

## 2026-08-17 Agent session `analysis_1786955838458_jq2529` 只读故障诊断

- 该 session 唯一绑定 investigation `36467f7a-0be0-528a-9ea6-1f6bfb0c03f9`；本轮没有第二份测试
  调查。I0 durable job 和 I5 job 均由 5209 worker 一次领取并 `succeeded`，同时间 `/health` 多次
  返回 200，因此不是 PM2、worker、Postgres、端口或环境挂载中断。
- 目标快照、关键日与 claim 事实已冻结；`I2_QUERY_PLAN` 以 `generation_source=live_model` 实际运行
  约 68 秒并生成 5 条查询。随后 5 个 `I3_PATENT_SEARCH` 均真实调用 Patsnap，但供应商全部返回
  `HTTP 200 / error_code=67200005`，即智慧芽账号余额不足；不得解释为零命中。
- 初始检索没有取得任何候选文献：报告中 document/version/disclosure/D1/combination/gap/Claim Chart
  数量全部为 0，且 I4-S/I4-C/I4-O/I4-I module run 总数为 0。I3-F 只对空候选集确定性收口；后续
  I3-FETCH/I3-QUALIFY failed 行是首轮搜索错误的阶段传播记录，并非取得全文或完成日期核验。
- Agent 前端仅以 `partial + current_iteration_no=1` 推导可续检，未核对 D1/open gap，因而在倒计时后
  三次请求 continuation；后端因该案 `gap_items=0/open_gap=0` 正确返回 409，未创建任何 continuation
  batch。进度投影又用 `stage < activeStage` 批量标 completed，并因空报告的 I5 run 把模块11标完成，
  所以界面比真实 module runs 更乐观。本轮只做诊断，未改代码、数据、服务或重跑任务。

## 2026-08-17 智慧芽 Key 更新与 P002 恢复验收

- 用户在 `5-invalidity-search-test/.env.test.local` 更新智慧芽 Key；未在代码、日志、数据库、Portal
  或本文记录密钥值。环境文件为普通文件、权限 `600`，PM2 完整配置守门通过，Key 非空且符合
  当前 `sk-` 格式合同。
- 重载前只读确认 `invalidity_test.jobs` 中 `queued/leased/running` 合计为 0；随后只用
  `ops/pm2/test/start.sh` 重载隔离测试 5201/5209，未触碰历史 5109。5209 返回
  `status=ok/environment=test/worker_concurrency=2`，5201 parser 同样 `status=ok`。
- 通过项目 `scripts/patsnap_smoke.py --skip-count --search-limit 1` 仅执行一次最小 P002 请求；供应商
  返回 HTTP 200、`error_code=0`、`outcome=success` 并返回 1 条 lead，证明新 Key 的鉴权、P002 权限
  与可用余额均已恢复，先前 `67200005` 不再出现。本次未调用 P001、详情、PDF 或图片接口。
- 失败的旧 session `analysis_1786955838458_jq2529` 未自动重跑或改写；如需重新分析，应由用户从
  Agent 显式创建/启动新的业务运行，避免把旧的零候选失败快照误当成已恢复任务。

## 2026-08-17 旧 Agent 工具收口并切换新任务

- 用户明确要求结束旧 Agent 状态并重新开始新任务。只读核对发现 session
  `analysis_1786955838458_jq2529` 已是 `completed/partial`、5209 在途作业为 0，但对应
  `patent_agent_tool_runs` 行仍错误保留 `queued`，与真实终态不一致。
- 仅把该精确 tool run `fe2bee5d-541e-4ed2-a538-3a4f45a382b3` 收口为 `partial`，并记录旧任务因
  智慧芽余额不足已经结束、应新建任务重新分析；没有删除或改写 investigation、module run、证据、
  报告或消息，也没有创建/启动新业务任务。
- 浏览器使用现有管理员登录态确认旧卡显示“部分完成/本次自动任务已结束”及新 Key 提示；随后关闭
  旧续检弹窗并点击“新对话”。当前保留页面为 `http://localhost:3001/` 的“新的专利对话”，分析类型
  未选择、发送按钮待输入，不存在后台请求或智慧芽调用；用户可直接选择业务类型并提交新材料。

## 2026-08-17 Agent 无效进度“卡在第一个模块”投影修复

- 用户反馈 Agent session `analysis_1786977995613_0egoqj`（investigation
  `d94eb4c7-7229-5aa6-b8d3-25b0199f427a`）“一直没有动静”且卡片停在第一模块。只读诊断确认系统
  正常：5209/5201/3001 健康，I0_ORCHESTRATE durable job 心跳活跃；claim 1 处于 `initial_search`
  首轮，P002 五线全部成功、I3-F 冻结 13 个唯一候选，逐篇 I3_FETCH/I3_QUALIFY/I4_S 推进中（13 篇
  完成 3 篇），claim 6/7 为 `queued`。卡片“无动静”的另一原因是首轮内逐篇步骤不写入 session 状态。
- 根因：`agent-task-progress.ts` 的 `invalidityStage()` 未处理 `queued`，排队权利要求落到
  `default: return 1`，而“当前事项”取全部未完结权利要求的最小阶段，min(5,1,1)=1 把卡片拽回
  “读取目标专利”。
- 修复：activeStage 只由已启动（非 created/queued/pending）的权利要求定位；全部排队时显示起始
  阶段（第 3 步）。纯前端展示修复，不改后端状态机、I0—I5 语义或任何持久化数据。
- 验证：`scripts/test-patent-agent-contracts.ts` 新增排队/全排队两条回归断言并通过、`pnpm
  ts-check`、定向 ESLint 通过；12GB 堆 `bash scripts/build.sh` 生产构建成功；专用 PM2_HOME
  `~/.pm2-patent-prod` 仅重启 `patent-web`，`/login`=200、匿名首页=307、静态 chunk=200、匿名
  Agent API=401。5201/5209 与正式 5109 未重启、未触碰。
- 边界说明：claim 状态在整个首轮（含逐篇取文与 I4-S）期间保持 `initial_search`，Agent 卡片会停在
  第 5 模块直到首轮收口；逐篇实时明细以管理员 `/test/module-lab?session=<id>` 只读视图为准。

## 2026-08-17 无效任务强制停止按钮、只读视图真实输出与 I0 重启幂等修复

- 用户针对 Agent session `analysis_1786977995613_0egoqj`（调查 `d94eb4c7-7229-5aa6-b8d3-25b0199f427a`，
  CN212879151U 扫地机器人）提出三个问题：页面长期“没动静”、模块1/2/3 显示为空却仍继续、要求输出异常
  即停在那里并提供强制停止按钮。只读诊断结论：后端数据完整（快照含申请日 2020-05-06、公开日
  2021-04-06、10 项权利要求、31 张附图；claim 1 关键日 2020-05-06/申请日依据；claim 6/7 的关键日按
  设计在进入该 claim 流程时才解析），显示为空是管理员只读视图 `attachedBusinessStates` 不渲染真实
  输出所致；fail-closed 守门本就存在（缺附图/缺独立权利要求直接 FAILED、关键日不可定转人工、无合规
  检索式 query_plan_failed 收口）。首轮检索词为固定五线：申请人线 AN+TTL、客体 TTL+DESC、
  IPC A47L11/24|A47L11/28+DESC、客体+发明点+效果词、TTL/ABST+DESC 功能表达。
- 新增调查级强制停止：5209 `POST /v1/investigations/{id}/cancel`（reason 必填；终态幂等 200；
  在途 run 逐个 `request_module_run_cancellation`，queued 直接收口、leased 由 worker 心跳协作式停止并
  联动取消调查/权利要求/轮次；无在途 run 的非终态调查直接置 cancelled 并留 `investigation.cancelled`
  事件）。Portal 新增 `POST /api/invalidity/session/{id}` 归属校验代理。
- **停止交互按用户决定改为与其他 Agent 一致**：不单独设“强制停止”按钮；Agent 输入框的发送按钮
  在有任务工作中时变为红色停止按钮，点击即停止当前对话中全部在途无效调查（证据保留、可重跑，
  无浏览器二次确认弹窗），空闲时恢复为发送按钮；工作中键盘回车只作为文本框普通回车（换行），
  不触发发送也不触发停止。侵权链暂无取消后端，仅侵权在跑时停止按钮禁用并提示不支持。
- `/test/module-lab` 只读视图新增 `attachedDerivedFacts`：从报告预览回填模块1（专利号/名称/申请日/
  优先权日/公开日/权利要求数/附图数，缺日期缺附图如实标注异常）、模块2（各独权关键日与依据，未开始的
  显示“等待开始”）、模块3（技术特征拆解数+示例，说明画像在模块4运行中生成）、模块4（冻结检索式）。
- **事故与修复**：为部署取消接口重启 5209 时，在途 I0 durable job 被自动重试，但 start job payload 硬编码
  `force_recompute=True` 且 `_ensure_claim` 只看内存 checkpoint 不查数据库，重试时重复插入
  claim_investigations 触发唯一约束，三次尝试后 I0 job failed、调查被连带标记 failed。修复：
  `_ensure_claim` 现在先按（investigation_id, claim_id, variant=base）收养数据库已有行（连同状态/关键日），
  start job payload 删除 `force_recompute`；新增回归
  `test_i0_start_retry_adopts_existing_claim_rows_instead_of_duplicate_insert`（含 FakeRepository 唯一约束
  场景）。教训：持久化 checkpoint 完备时也不得随意重启在途 I0 的后端；必须先确认 queued/leased 为 0。
- 恢复：以 force_recompute=false 手工入队 resume job（run `31e5734a-1188-4f82-aa6b-d0e68ab572a6`），
  调查恢复 running；claim 1 保持 `novelty_evidence_complete` 未重跑，claim 6 从 checkpoint 续跑
  （复用已冻结 plan/查询/9 篇合格文献），claim 7 排队。续跑后已观察到新的 I3_PATENT_SEARCH/I3-F/
  I3_FETCH/I3-QUALIFY succeeded。
- 验证：后端全量 `746 passed, 7 skipped, 1 failed`（唯一失败为既知的外部实例目录 3 份新增 PDF 未登记
  旧 sample oracle，与本次无关）；Portal `pnpm ts-check`、Agent/无效两套契约、定向 ESLint、12GB 堆
  production build 通过；契约断言覆盖停止按钮、取消代理与只读视图异常标注。部署：无在途 job 时重载
  5201/5209（health 正常，worker_concurrency=2），专用 PM2_HOME 重启 `patent-web`（/login=200、
  匿名首页=307、Agent API 匿名=401）。WORKFLOW_SPEC 升至 3.54。正式 5109 未触碰、未 promotion。

## 2026-08-18 Agent 停止按钮“假停止”双根因修复

- 用户点击 Agent 停止按钮后程序继续运行。只读诊断确认为两个叠加 bug：①`db.py`
  `_mark_i0_investigation_cancelled` 的多表 `UPDATE iterations ... FROM claim_investigations` 中
  `COALESCE(completed_at, now())` 未限定表别名（两表都有该列），每次触发
  `psycopg2.errors.AmbiguousColumn`，导致 worker 每次 poll 的 `_recover_cancel_requested_jobs`
  清扫整体 ProgrammingError 回滚——5209 错误日志中大量 `worker poll failed (ProgrammingError)`，
  leased 取消请求永远落不了终态；②I0 handler 的心跳只在 claim/query 边界执行，单条查询内逐篇
  fetch+I4-S 循环（可达 15 分钟以上）完全不检查取消，且 fetch 外层 `except Exception` 会吞掉
  `JobCancellationRequested`。
- 修复：清扫器 SQL 改为 `COALESCE(iteration.completed_at, now())`；workflow.py 在逐文献循环与
  `retrieve()` 逐候选循环顶部执行显式 `heartbeat()`（新增 `cancel_check` kwarg 透传），外层
  `except JobCancellationRequested: raise` 防吞。新增真实 Postgres 回归
  `test_real_postgresql_cancel_requested_expired_lease_is_swept_atomically`（取消请求的过期租约
  job 被原子清扫为 cancelled、调查/claim/iteration 级联、再 poll 幂等无重复事件）。
- 实测：修复后卡住的 continuation job `2a9140e8-…` 被清扫为 cancelled，调查
  `d94eb4c7-7229-5aa6-b8d3-25b0199f427a`（CN212879151U，Agent session
  `analysis_1786977995613_0egoqj`）进入 cancelled 终态，已完成的 claim 1/6
  （novelty_evidence_complete）保留不动。
- 验证与部署：后端全量 `754 passed, 1 failed`（唯一失败仍为外部实例目录 3 份新增 PDF 未登记旧
  sample oracle，与本次无关）；确认 `invalidity_test.jobs` queued/leased=0 后重载隔离测试
  5201/5209，`/health` 均正常，重启后 pm2-error.log 零新增 `worker poll failed`。前端本次无改动，
  Portal 未重建；正式 5109 未触碰、未 promotion。WORKFLOW_SPEC 升至 3.55。
- 早前同会话已完成：Agent 输入框停止交互改造（工作中发送按钮变停止按钮、无二次确认弹窗、
  回车=换行），Portal 已构建重启生效；I0 重启幂等修复（`_ensure_claim` 收养数据库已有行）。

## 2026-08-18 只读诊断视图模块一输出缺失修复（session analysis_1787060521785_b20ed7）

- 用户反馈 Agent session `analysis_1787060521785_b20ed7`（investigation
  `093abe4c-5c2f-589f-9a80-7563b76d82e6`，CN217443913U《无人值守监控系统》）在模块实验室只读页
  模块1“什么都没有检测出来”。核查确认模块一提取完全成功：快照含申请号 202123053399.4、申请日
  2021-12-07、授权公告日 2022-09-16、3 项权利要求、3 张附图、说明书四章节、完整著录项目。
- 根因是展示链三段缺口：①报告 `target_patent` 投影（report.py `_target_patent`）白名单裁剪掉
  申请号、授权公告日、格式、页数、说明书章节、著录项目；②只读视图 `TargetResultDetails` 的
  回退只取专利号/名称/摘要/权利要求/附图列表，日期区块和权利人只读 module run output，Agent
  自动流程没有独立 I1 run，于是显示“没有识别到可核验日期”；③附图二进制端点只支持 lab
  module-run 作用域（`/v1/lab/module-runs/{id}/target-figures/{i}`），Agent 调查没有 I1 run，
  附图只能显示元数据卡片、无图片。
- 修复：报告投影补齐中立事实字段（application_number/grant_date/source_format/page_count/
  used_ocr/specification/bibliographic_data/claims_section_text），并按 SHA-256 为每幅附图绑定
  `artifact_index`（source_uri/page_texts/file_path 继续不外泄）；5209 新增调查级
  `GET /v1/investigations/{id}/target-figures/{index}`，沿用 lab 端点的索引边界、路径包含、
  SHA-256、MIME 白名单校验；Portal 新增管理员代理
  `/api/admin/invalidity/session/{id}/target-figures/{index}`（仅 approved 管理员、session→
  investigation 归属解析）；前端只读视图模块一完整回退展示日期、申请号、权利人、格式页数、
  说明书章节与附图图片。
- 验证：后端新增调查级附图端点测试（200/越界 404/路径逃逸 404/篡改 409）与报告投影专项测试
  （新字段透出 + 路径不泄露 + artifact_index 绑定）均通过；全量 `756 passed, 1 failed`（唯一失败
  仍为既知的外部实例目录 3 份新增 PDF 未登记旧 sample oracle）；Portal `pnpm ts-check`、无效契约
  测试、定向 ESLint、12GB 堆 production build 通过。无在途 job 时重载隔离 5201/5209（health
  正常），实测该调查报告预览已返回全部新字段且附图端点返回真实 JPEG；专用 PM2_HOME 重启
  `patent-web`，/login=200、模块实验室匿名=307、新代理匿名=401。正式 5109 未触碰。
- 顺带说明：该调查已被用户通过停止按钮主动取消（`cancelled / 用户已取消模块运行`），取消链路
  在 3.55 修复后工作正常。WORKFLOW_SPEC 升至 3.56。

### 2026-08-18 跟进：只读视图模块二关键日回退

- 同一 session 模块2显示“未能确定/日期规则未返回依据”：自动流程没有独立 I1_5 module run，
  关键日按专利级规则直接写入各独立权利要求，`DateResultDetails` 只读 run output 导致空显示。
  已改为从报告预览 `claim_investigations` 回退：统一截止日 + 依据中文映射
  （verified_priority_date/patent_level_priority_date/application_date/manual），并逐权利要求
  列出持久化关键日；各权利要求日期不一致时明确警示。`attachedDerivedFacts` 的依据映射同步统一。
- 该修复与模块1修复一样按数据链路生效：任何已有/新建调查的报告预览都带完整字段，不限于
  本次 session。验证：`pnpm ts-check`、定向 ESLint、无效契约测试通过；12GB 堆 production
  build 通过并重启 `patent-web`；后端无改动未重载。WORKFLOW_SPEC 升至 3.57。

### 2026-08-18 跟进：只读视图模块三至十一全部输出回退

- 同一 session 模块3、4 及后续模块“本次输出结果”为空：Agent 自动流程不产生 lab 式独立
  module run（模块1/2/3 尤其），且管理员诊断路由此前只用报告投影（故意剥离 input/output
  snapshot）填充 children，导致各模块输出区空白或误导性文案。
- 修复：5209 新增调查级 `GET /v1/investigations/{id}/module-runs`（public_json 脱敏，含
  input/output snapshot，limit 1000）；Portal 管理员路由 `route.ts` 返回体新增
  `module_run_details`；只读视图 children 按 run id 合并该快照填充真实输入/输出。模块三
  （核心发明点）在无独立 `I2_INVENTIVE_PROFILE` run 时回退展示自动流程嵌在 `I2_QUERY_PLAN`
  输出中的 `invention_search_profile` 画像并明示来源；模块1—11 各阶段在无任何持久化 run
  时明确显示“本阶段暂无持久化输出”。
- 验证：后端新增 module-runs 端点测试通过，全量 `757 passed, 1 failed`（唯一失败仍为既知
  外部实例目录 3 份新增 PDF 未登记旧 sample oracle）；Portal 定向 ESLint、无效契约测试通过。
  无在途 job 时重载隔离 5201/5209（health 正常），实测该调查 module-runs 端点返回完整 run
  快照。WORKFLOW_SPEC 升至 3.58。
- 边界说明：只读视图 5 秒轮询现会多拉 module-runs 全量快照，大案（200+ run）载荷变大，
  本地管理员诊断可接受，未做分页。

### 2026-08-18 跟进：模块四"未找到模块3的输出"假象修复

- 用户反馈模块4输入显示"未找到模块3的输出，请先运行模块3"，但模块4实际有输出。核查 run
  `16a7d5f0-31b7-439f-9211-ab0f933b2459`（I2_QUERY_PLAN，succeeded）确认：这是 Agent 自动
  流程（I0）的运行，`generation_source=live_model`，无 `source_plan_run_id`——模块4在同一
  运行内自行调用 GLM 生成画像+检索式，根本不消费模块3 run。真正的输入是 input_snapshot 里的
  独立权利要求原文（expanded_claim_text）、target_image_count 以及服务端持久化的完整专利
  上下文；输出（4 条检索式 + 内嵌 invention_search_profile）由 GLM 现场生成。
- 修复（仅前端展示）：模块4输入区识别自动流程 run（无 source_plan_run_id 且输出含内嵌画像
  或 live_model 标记），如实展示真实输入与本次运行内模型总结的核心发明点，并说明自动流程
  没有独立模块3运行；实验室逐阶段链路（消费模块3 run）显示逻辑不变。
- 验证：`pnpm ts-check`、定向 ESLint、无效契约测试通过。WORKFLOW_SPEC 升至 3.59。

## 2026-08-18 自动流程与实验室链路统一为"前一模块输出 → 后一模块输入"

- 用户要求确认 Agent 自动流程是否严格按模块顺序链式执行（和实验室测试版一样），不成立则修改。
  只读调查（explore 报告）结论：大部分阶段已链式且有 fail-closed 守门，但首轮 I2 存在实质
  偏离——自动流程没有 `I2_INVENTIVE_PROFILE` run，`I2_QUERY_PLAN` 在同一 GLM 调用内自行通读
  完整专利生成画像+检索词（`generation_source=live_model`），而实验室是模块3独立画像 run →
  模块4只消费画像输出再调 GLM。
- 修改（`5-invalidity-search-test/src/invalidity/workflow.py` `_round_plan`）：首轮先创建并
  持久化 `I2_INVENTIVE_PROFILE` run（`generate_inventive_profile`，queries 恒空，失败即
  claim FAILED 且不创建检索词 run），随后 `I2_QUERY_PLAN` 经
  `generate_queries_from_inventive_profile` 只消费该画像 run 输出（
  `generation_source=module3_profile_live_model`、`source_plan_run_id` 指向画像 run）。
  `_persist_i2_model_response` 新增 stage 参数区分两段审计。gap 轮、零查询复用、continuation
  路径均不变；首轮画像仍内嵌于 QueryPlan 供 gap 轮复用。
- 不改项及理由：模块1/2 的冻结快照与关键日本就供下游消费（仅缺独立 run，展示已由 3.56/3.57
  修复）；I4-C/I4-I 跨轮累计语料是 gap 补证的设计意图且输入按 SHA 绑定；I3-FETCH 无 DB 级
  复用缓存只是效率差异。
- 测试：`FakeAnalysisEngine` 补 `generate_inventive_profile` /
  `generate_queries_from_inventive_profile`；新增两条回归（首轮画像 run 先于检索词 run 且
  source_plan_run_id 绑定；画像失败时 claim FAILED 且无检索词 run）。test_workflow.py
  56 passed；全量 `751 passed, 1 failed, 8 skipped`（唯一失败仍为既知外部实例目录 3 份新增
  PDF 未登记旧 sample oracle）。
- 部署：确认 queued/leased=0 后重载隔离测试 5201/5209，`/health` 均正常。前端零改动——只读
  视图模块3卡片按 technicalCodes 分组、模块4输入区按 source_plan_run_id 匹配，自动流程产生
  真实画像 run 后显示自动变准；历史 live_model 旧 run 继续走 3.59 回退。正式 5109 未触碰。
- 影响：首轮每个独立权利要求多一次 GLM 调用，耗时增加；模块4 prompt 只含画像冻结事实、不再
  含专利全文/附图，与实验室完全一致。WORKFLOW_SPEC 升至 3.60。

## 2026-08-18 只读视图模块五"没有可执行检索式/0 条"假象修复

- 用户反馈 session `analysis_1787060521785_b20ed7`（investigation
  `093abe4c-5c2f-589f-9a80-7563b76d82e6`）模块5输入显示"没有可执行检索式"、输出显示"检索源
  返回 0 条"。数据库核查真实状态：4 条 I3_PATENT_SEARCH 全部 succeeded 且真实联网（patsnap），
  仅申请人线合法零命中，其余三线均有命中；I3-F 原始 15 条、合并重复 2、排除 5、实际取文 8；
  I3_FETCH 2 篇成功取回（1 篇 EPO ReadTimeout 记入 errors）；随后用户主动停止，I4-S 完成 2 篇。
  显示与实际完全不符。
- 根因：只读渲染器只认实验室 run 快照形状——输入读 `input_snapshot.request.input`，命中列表读
  `output.documents`，取文/日期 run 输出本身即单篇文献；而 I0 自动流程 run 输入直接在
  `input_snapshot` 顶层、命中在 `output.records`、I3_FETCH/I3_QUALIFY 输出为 `{records:[...]}`
  批次形状，且 retrieve 会重建记录对象导致取文记录的 external_id 与检索命中不同。
- 修复（仅前端 `module-lab/page.tsx`）：`runInput`/`queryInputText` 回退读取顶层 input_snapshot；
  命中列表兼容 `output.records`；取文/日期结果展开为逐文献记录，用宽容身份键（external_id、
  规范化公开号、从 EPO epodoc source_url 解析的公开号）做多键关联检索命中与取文/日期结果；
  provider 错误兼容 `errors` 字段；逐篇取文输入列表展示批次内每篇记录。lab run 形状渲染不变。
- 验证：`pnpm ts-check`、定向 ESLint、无效契约测试通过（新增 3 条断言锁定该行为）。
  WORKFLOW_SPEC 升至 3.61。

## 2026-08-19 模块九五轮容错、上下位客体与模块归属修复

- 指定 Agent session `analysis_1787119382809_qgwj93` 绑定 investigation
  `c830392c-eb5f-5e40-b81a-aec4fb3c1dfd`。真实谱系显示：首轮模块五成功；第 1 个 gap 轮的
  `I3_FETCH/I3_QUALIFY` 为 partial 后，`I4_I_INVENTIVE_STEP` 与 `I5_REPORT` 均 succeeded；真正
  停点是下一轮 `I2_GAP_QUERY_PLAN` 因与上一轮同族而 failed。诊断页仅按技术节点名归组，错误把
  gap 轮复用的 I3 partial 显示到模块五。
- gap 第 2 轮改为优先选择仍有技术区分度的上位产品类别；目标客体已经过宽、继续上位只会得到
  泛词或无法形成双语组时，允许选择与区别特征用途/技术角色相符的合适下位产品，并保存上下切换
  理由。I2 规则/提示版本升至 `i2-query-plan-v11` / `i2-inventive-profile-v10`。
- 任一 gap 轮的规划、检索、候选清理、取文、日期或 I4-S 错误按轮冻结并进入错误台账，不再把
  claim 直接置 failed 或阻断后续冻结轮；自动链继续直至五个 gap 轮均执行、按已关闭特征跳过或
  用户取消。I4-I 在模块九无新增甚至全部错误时仍消费现有真实 D1/I4-S 语料；仅 D1 时以
  `combination_basis=unresolved` 输出证据不足并携带逐轮错误，禁止虚构 D2/D3。
- Portal 同 session 诊断按 `iteration_no` 区分首轮与 gap 轮复用的 I3/I4-S：首轮归模块五/六，
  iteration > 1 归模块九，解决模块五误报。律师模块九 durable 批次也按失败轮留痕并自动排入后续
  冻结轮，模块十接受已收口的 error/partial 批次及其 `failure_ledger`。
- 宪章升至 2.40，工作流规范升至 3.62。后端全量回归 754 passed、8 skipped；唯一失败是外部实例
  目录新增 3 份 PDF 未登记旧 sample oracle，与本轮代码无关。Portal `pnpm ts-check`、无效契约和
  `bash scripts/build.sh` production build 通过。确认无在途 invalidity job 后，以官方隔离脚本重载
  5201/5209，并重启 `patent-web`；5201/5209、Portal 及 5101—5108 健康检查均为 200/ok，5209
  返回 I2 v11/v10。

## 2026-08-20 P020 英文日期核验与 Agent 人工待办收敛

- 新 session `analysis_1787217204568_yvb78w` 的人工待办发生在 I3 日期资格核验，而非目标专利
  关键日确认。两份 US P020 冻结首页 OCR 已读出公开号、(43) 和 `Oct. 9, 2008` /
  `Jun. 11, 2009`，但旧确定性解析器只接受中文/数字日期，因此误转人工。
- 后端首页日期解析现支持英文月份全称/缩写、月日年/日月年及 OCR 空格句点变体，并继续
  要求冻结 SHA-256、首页文献身份、公开标记和期望日期全部精确命中。旧 session 审计未回写；
  新规则对后续新取文生效。
- Agent 弹框只展示当前 document-level `needs_human_review` 日期文献，隐藏与停点无关的证据
  导入、D1、gap 与 action 区；选中后回填已检测的日期、公开号、机构、文献版本与 SHA-256。
  指定旧 session 登录态验收确认 16 份文献中只列 2 份 US 待复核文献，未提交任何变更。
- `WORKFLOW_SPEC.md` 升至 3.63；宪章边界未变，仍为 2.40。后端定向 7 passed，全量
  756 passed、8 skipped、1 个已知 sample oracle 外部文件失败；Portal ts-check、定向 ESLint、
  无效/Agent 契约和 production build 通过。确认无在途 job 后已重载 5201/5209 并重启
  `patent-web`；5201/5209、Portal 及 5101—5108 健康检查均为 200。

## 2026-08-20 Agent 模块11直显与逐篇 XLSX

- 指定历史 session `analysis_1787242957124_padlos` 绑定唯一 investigation
  `c2b358f9-ffa8-5b86-b2e4-ee929bebd018`。不可变 `ReportDataV1` 快照包含 1 项独立权利要求、
  21 篇已完成逐篇分析的对比文件、105 条逐特征披露、1 份 5 段律师文字分析及 1 张
  “5 个特征 × 10 篇文献”横向矩阵；本轮只重投影和导出该快照，未改写旧调查、运行或审计记录。
- Agent 使用 session 轮询返回的同一报告 DTO，模块11第一部分律师文字和第二部分 Top 10 横向矩阵
  默认展开显示，不再只显示摘要计数；该正文最初嵌在工具卡内，同日新增的独立结果消息决策已取代
  这一视觉层级。报告快照稍晚于业务终态到达时继续短轮询，防止终态竞态导致页面永久缺少结果。
- 第三部分使用同 session 的 XLSX 导出。工作簿在既有总览、律师分析、Top 10、审计表之外新增
  `逐篇CC索引`，并为全部已进入逐篇分析的对比文件分别建立唯一命名的 Claim Chart Sheet；指定
  session 实测生成 21 个逐篇 Sheet，每个 Sheet 含 5 条技术特征及原文、位置、结构对应和理由。
- `WORKFLOW_SPEC.md` 升至 3.64，宪章边界未变（2.40）。Portal `pnpm ts-check`、Agent/无效报告
  契约、定向 ESLint 和 production build 均通过；已只重启 `patent-web`，Portal 3001 与 5209
  健康检查均为 200。登录态浏览器复核该 session：第一、二部分均直接展开，横向表为 10 文献列、
  5 特征行，第三部分显示 21 篇且 XLSX 下载成功触发。

## 2026-08-20 Agent 过程与结论消息分离

- 用户明确要求 Agent 仿 Codex Work 的消息层级：运行中显示“现在正在做什么”的工作动态并保留
  原十一模块进度；完成后另起一条对话输出结论文字、Top 10 表和 XLSX 导出，完整结果不得继续
  混在过程卡里。工作动态只投影持久化阶段状态，不输出或伪造大模型隐藏思维链。
- Portal 新增 `agentToolAnchorMessageId`，把每个工具运行从原始 user message 锚定到同轮最后一条
  assistant 启动回复；因此旧 session 无须重写消息审计，也按“启动回复 -> 过程卡 -> 独立结论回复”
  渲染。`AgentToolProgressCard` 只保留工作动态、进度、错误和人工动作；新
  `AgentInvalidityResultMessage` 独立承载收口摘要、证据边界和完整 `AgentInvalidityReport`。
- `WORKFLOW_SPEC.md` 升至 3.65，宪章边界保持 2.40。Portal `pnpm ts-check`、Agent/无效合同、改动
  文件定向 ESLint、production build 均通过；全仓 lint 仍被既有 module1 条件 Hook 错误拦截。
  已只重启 `patent-web`，3001 正常响应。登录态复核 `analysis_1787242957124_padlos`：过程卡与结果
  消息为两个独立节点且顺序为 process -> final；工作动态列出模块9/10/11真实完成状态；律师文字和
  Top 10 均默认展开，表为 10 文献列 × 5 特征行，导出链接继续绑定同一 session。历史调查、模块
  运行、报告快照和审计记录均未改写。

## 2026-08-23 侵权 Agent 独立结果消息与专利分析单一实验室

- 侵权 Agent 现在与无效 Agent 使用同一消息层级：“用户请求 -> Agent 启动回复 -> 过程卡 -> 独立
  Agent 结果回复”。过程卡只呈现真实持久化阶段、进度、错误和可审计工作动态，不输出或伪造隐藏
  思维链；完成后独立 assistant 气泡直接显示结论、统计、商品技术特征比对表和完整结果/XLSX 入口。
- 新的侵权结果 DTO 只读投影 owner-scoped analysis session 的已保存商品、比对、分数和完整性状态，
  不重新计算评分或法律结论。真实完成 session `analysis_1784098219047_zm2wi5` 已验证持有 15 件商品、
  15 份比对及 final 完整性；旧 Agent conversation 和 audit 不做回写。
- 专利分析测试产品只公开 `/test/product-pipeline` 一个实验室，旧 `/test` 自动跳转。页面不再显示
  “模块1结果”或阶段编号，改为“专利解析、关键词生成、商品检索、权利要求与商品比对”四个业务
  阶段；每阶段把本次输入、后台输出、可读结果分成三个默认收起区块。细分旧测试页仅保留内部兼容，
  不再进入 Agent 导航；结果页入口改称“专利原文”。
- `PROJECT_CHARTER.md` 升至 2.41。Portal 类型检查、Agent/无效合同、定向 ESLint、production build
  和登录态页面验收通过；只重启 `patent-web`，5101—5108 与唯一无效后端 5201/5209 均未重启。

## 2026-08-23 侵权 Agent 管理员同 session 四阶段诊断

- approved admin 的侵权 Agent 过程卡新增“查看本次分析各模块”，固定进入
  `/test/product-pipeline?session=<analysis_session_id>`；普通用户不显示该入口。无 `session` 的
  `/test/product-pipeline` 仍是原有可运行实验室，两种模式不复制页面或业务后端。
- 新增只读接口 `/api/admin/patent-analysis/session/[id]`。接口先校验 approved admin 与
  `analysisKind=infringement`，再只使用 session 已保存的 `dbRecordId/keywordRunId/searchRunId/
  claimCompareRunId` 以及精确 `analysis_session_id` 读取模块1记录、模块2关键词 run、模块3商品/
  候选 run、模块4比对 run；不得按同专利或全库最新 run 模糊回填，不调用执行型测试 API，也不更新
  session、step 或审计。
- 带 `session` 的实验室显示“管理员同任务只读诊断”，隐藏上传、示例、参数和全部运行按钮；四阶段
  分别保留本次输入、本次输出和可读结果。失败阶段仍展示失败前已持久化的部分输出；模块3特别展示
  候选核验汇总、逐候选检索词/平台/来源和排除原因。运行中只按 3 秒 GET 轮询同一 session。
- 实测失败 session `analysis_1787483255507_irdtd9`：专利解析完成（record 113、3 项权利要求）、
  关键词完成（run 144、4 条）、商品检索失败（run 353、24 条候选全部 rejected、accepted 0）、
  权利要求比对未执行。接口读取前后原 session 的 `status/results/updated_at` 完全一致。
- `PROJECT_CHARTER.md` 升至 2.42。验证：`pnpm ts-check`、Agent 合同、改动文件定向 ESLint、
  `bash scripts/build.sh` production build 通过；全仓 `pnpm lint` 仍仅被既有
  `src/app/module1/page.tsx` 条件 Hook 错误拦截。只重启专用 PM2_HOME 的 `patent-web`，其余八个
  后端进程未动。管理员 Chrome 登录态实测 Agent 按钮跳转、四阶段状态、24 条排除候选及无运行
  按钮均正确。

## 2026-08-23 GitHub 最新源码快照

- 按用户要求，将当前分支的最新源码、产品宪章、工作流规范、测试代码及可重放的固定测试夹具统一
  整理为 Git 提交并发布到现有 GitHub 远端；不拆分或回滚此前 Kimi/Codex 已形成但尚未提交的项目改动。
- 提交范围明确排除本机 PM2 状态目录、顶层 `tmp/` 诊断/渲染临时文件、各模块 `.data`、上传文件、
  `.env.*.local`、日志、虚拟环境、依赖目录和构建产物；根 `.gitignore` 已补充
  `/.pm2-patent-prod/` 与 `/tmp/`，避免后续误提交运行状态或临时案件材料。
- 发布前执行候选文件高置信密钥模式检查和 `git diff --check`；命中项仅为测试专用 fixture Key，
  环境示例中的凭据值均为空或明确占位符，真实本地环境文件继续保持忽略。
