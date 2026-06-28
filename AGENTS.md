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
  -> 模块4读取专利、商品，拆独立权利要求，逐商品比对，写 claim_compare_runs / claim_compare_results
  -> Portal 从模块4响应或 Postgres 恢复结果，展示列表、详情和报告
```

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
  -> save_results
  -> exit
```

输入输出：

- `GraphInput(patent_record_id, analysis_session_id, input_keywords?)`
- `GraphOutput(product_dataset_id, search_run_id, total_products_count, is_complete, error_message)`

关键环境：

- `COZE_SEARCH_API_URL`
- `COZE_SEARCH_API_TOKEN`
- `COZE_SEARCH_TIMEOUT`
- `COZE_MAX_CONCURRENT`

重要现状：

- `coze_search_node` 支持批量请求，批量无结果时退回逐关键词并发。
- `3-search/README.md` 新增了独立商品页抓取原型 `src/tools/product_page_capture.py`，该原型不接入模块3主流程。
- 抓取原型使用 Playwright，可人工登录/复用浏览器 profile，输出截图、可见文字、OCR、多模态筛选后的商品详情图。
- 抓取原型测试：`uv run pytest tests/test_product_page_capture.py`。

## 模块4：权利要求-商品比对

路径：`4-claim-chat/`

职责：

- 读取专利独立权利要求、说明书、附图和模块3商品。
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
- 当前目标口径：全部技术特征共同分配 100 分；待确认按有效字符/英文词比例得分；所有特征得分相加得到商品总分；任一特征明确不相同则整个商品总分直接归零。

数据库迁移：

- `4-claim-chat/src/main.py` 有 `ensure_claim_compare_async_columns()`，启动时自动补齐模块4异步和评分字段。
- `claim_compare_runs` 包含 `run_id`、`status`、`error_message`、`started_at`、`finished_at`。
- `claim_compare_results` 包含评分和 token 级字段。

注意：

- 文件名 `write_feishu_results_node.py` 已不完全准确，当前还承担写 Postgres 和结果摘要职责。
- 飞书子表格写入相关逻辑仍存在，但主线应优先看 Postgres 和 Portal 结果映射。

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
- 模块4评分化和 token 高亮链路已补脚本验证：token mismatch 会传导到特征、claim、商品归零；无 mismatch 时商品分按特征/claim 分相加。
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
