# 项目上下文

### 版本技术栈

- **Framework**: Next.js 16 (App Router)
- **Core**: React 19
- **Language**: TypeScript 5
- **UI 组件**: shadcn/ui (基于 Radix UI)
- **Styling**: Tailwind CSS 4
- **对象存储**: coze-coding-dev-sdk (S3Storage)
- **LLM 引擎**: 本地工作流服务统一使用 `glm-4.6v`；Portal 负责编排与透传
- **搜索引擎**: coze-coding-dev-sdk (SearchClient, 网页搜索)
- **URL 抓取**: coze-coding-dev-sdk (FetchClient, 网页内容提取)

## 项目概述

**专利侵权自动识别系统** — 基于专利文本与市场商品信息，进行事实驱动、可回溯、可验证的侵权技术比对，输出 Claim Chart 级别的专业分析结果。

### 核心设计原则

1. **LLM 不直接做法律结论判断** — LLM 仅用于文本读取、结构化、对齐、标注
2. **所有判断结果可回溯** — 每一比对结论可追溯到专利原文、商品原始描述、比对规则
3. **原侵权模块单向依赖** — 模块1→2→3→4→结果提取 串行，不允许循环调用或反向依赖；无效检索使用独立 I0 持久化轮次，遵守根宪章和 `docs/invalidity/WORKFLOW_SPEC.md`
4. **稳定性优先** — 优先降低 bug 和理解成本

## 目录结构

```
├── public/                     # 静态资源
├── scripts/                    # 构建与启动脚本
├── src/
│   ├── app/
│   │   ├── api/
│   │   │   ├── analyze/route.ts         # 分析编排 API (SSE 流式, 5步骤)
│   │   │   ├── upload/route.ts          # 文件上传 API (S3 存储)
│   │   │   └── analysis/[id]/route.ts   # 查询分析结果 API
│   │   ├── results/
│   │   │   ├── page.tsx                 # 页面2: 商品侵权汇总
│   │   │   └── [productId]/page.tsx     # 页面3: 权利要求-特征比对详情
│   │   ├── layout.tsx                   # 根布局
│   │   ├── page.tsx                     # 页面1: 专利上传与进度追踪
│   │   └── globals.css
│   ├── components/
│   │   ├── upload-form.tsx              # 上传表单 (URL/文件/文本)
│   │   ├── analysis-progress.tsx        # 6步骤进度展示
│   │   ├── product-card.tsx             # 商品卡片 (含侵权概要)
│   │   ├── claim-chart-table.tsx        # Claim Chart 比对表
│   │   └── ui/                          # Shadcn UI 组件库
│   ├── hooks/
│   │   └── use-analysis.ts              # 轮询分析流 Hook
│   └── lib/
│       ├── types.ts                     # 核心类型定义 + 行业路由协议 (6步骤)
│       ├── workflow-client.ts           # 本地 510x /run 端点调用 + 行业路由
│       ├── feishu-client.ts             # 飞书多维表格 API 客户端 (备选数据源)
│       ├── analysis-store.ts            # 分析会话存储 (Postgres 主链路)
│       └── utils.ts                     # 通用工具函数
├── .env.local                           # 环境变量 (Postgres + LLM + 搜索 + 可选飞书凭证)
├── next.config.ts
├── package.json
└── tsconfig.json
```

## 业务模块与数据流

6 个步骤串行执行，步骤1调用模块1，步骤2做行业识别，步骤3根据行业路由到对应关键词生成模块，步骤4-5调用模块3-4，步骤6提取结果：

```
[用户输入: URL/PDF/文本]
       ↓
步骤0: 预热所有工作流（并发唤醒6个模块，防止冷启动超时）
       ↓
步骤1: 专利文本解析 → 本地模块1 5101/run，写入 Postgres
       ↓ (patent_record_id + analysis_session_id)
步骤2: 行业识别与路由 → LLM判断专利所属行业(fitness_equipment/home_appliances/general)
       ↓
步骤3: 技术关键词生成 → 根据行业路由:
       通用: 本地模块2 5102/run
       健身器材: 本地模块2-fitness 5103/run
       家用电器: 本地模块2-electra 5104/run
       ↓ (keyword_runs / keyword_records)
步骤4: 商品信息检索 → 本地模块3 5105/run，写入 search_runs / search_products
       ↓ (search_run_id + search_products)
步骤5: 技术特征比对 → 本地模块4 5106/run，写入 claim_compare_runs / claim_compare_results
       ↓
步骤6: 结果提取 → 优先从 Postgres 与模块4响应提取; 飞书读取仅为历史兼容备选
       ↓
[结果展示: 商品列表 + Claim Chart 比对表]
```

### 模块实验室与唯一业务后端

- `/test/product-pipeline` 是专利分析唯一后端的逐模块视图；Agent 的 `/api/analyze` 通过同一个
  route executor 自动运行专利解析、模块2、`3-product-search` 和模块4，不再运行旧 5105 链。
- `/api/test/product-pipeline` 支持 `action=patentParse|keywords|productSearch|claimCompare|all`。
  `productSearch` 固定使用 `PATENT_ANALYSIS_PRODUCT_SEARCH_API_URL`（兼容旧变量，默认
  `http://127.0.0.1:5107/run`），并从 `product_detail_search_products` 回读商品、图片、质量字段。
- approved admin 可从侵权 Agent 过程卡进入 `/test/product-pipeline?session=<analysis_session_id>`。
  该模式只调用 `/api/admin/patent-analysis/session/[id]`，按同一 session 保存的 record/run ID 和精确
  `analysis_session_id` 恢复四阶段输入、输出、部分结果及错误；隐藏所有运行控件，查看或轮询不得新建
  实验 session、重跑模块、改写旧会话或读取其他 session 的最新 run。
- Agent 与实验室只在自动完整编排和逐模块调试方式上不同；`analysis_session_id`、数据库表和
  下游结果合同完全一致。
- `/test/invalidity-pipeline` 保留为技术复核页；`/test/module-lab` 是面向律师的十一阶段调试页：目标专利、关键日、核心发明点、检索关键词、首轮证据、单篇比对、D1、显而易见性预分析、进一步检索与补证、创造性和报告。它与 Agent 无效工具共同调用唯一 `127.0.0.1:5209` 后端；5109 退出用户请求链。律师页每阶段只允许“当前真实案件”和“内置案例”两种入口；fixture 必须明确标识，不能冒充真实检索。
- `/test/invalidity` 是智慧芽 P002 技术测试入口，`/test/module-lab` 是律师测试入口；两者的检索链路均为测试上传根 → `I1_TARGET_SNAPSHOT` → GLM-4.6V `I2_QUERY_PLAN` → Patsnap `I3_PATENT_SEARCH` → lead。不得调用 `/start`、5109 或 P012/P018/P019；刷新只轮询既有 run，不创建 transport retry。
- 自 I2 v3 起，Portal 必须按 `query_id` 原样执行本轮实际生成的全部固定检索线：申请人+客体、标题客体+说明书核心发明点、分类号+说明书核心发明点、说明书客体+核心发明点+效果、标题关键词客体+说明书功能效果；事实不足的线由后端跳过并留痕，Portal 不补造。
- 固定五线的 `provider_expression` 是后端冻结的确定性产物。Portal 只校验对应字段、申请人唯一性、排除目标公开号和 OR 组上限后原样提交，并必须同时提交生成该表达式的 `query_plan_run_id` 与 `query_plan_query_id`，供 5209 按同案同独权成功计划精确回读；不得按旧 `query_role` 降级重编译，旧变体仅作历史快照兼容。
- 固定五线不创建 `compact-fallback`；零命中是有效结果，页面必须继续执行其余检索线并如实汇总。每个 P002 run 的实际 attempt 固定为 1。
- `/invalidity`、Agent 无效工具和模块实验室统一调用 `CANONICAL_INVALIDITY_ENVIRONMENT` 对应的 5209；页面之间只按用户/session/资源归属隔离，不再按正式/测试后端分流。

### 数据提取策略（步骤6）

1. **方案1（优先）**: 从模块4 API 响应的 `all_comparison_results` 字段直接提取比对数据
2. **方案2（备选）**: 如果本地结构化数据缺失且存在历史 `feishuUrl`，通过飞书开放平台 API 读取多维表格（需配置 FEISHU_APP_ID + FEISHU_APP_SECRET）

### 行业路由机制

- **行业类型**: `fitness_equipment`(健身器材) | `home_appliances`(家用电器) | `general`(通用)
- **识别方式**: LLM 分析专利文本/权利要求，输出行业+置信度+理由
- **路由逻辑**: 根据行业选择对应的关键词生成工作流；识别失败自动回退通用
- **扩展**: 新增行业只需在 types.ts 添加类型 + workflow-client.ts 添加模块配置和路由分支

### 技术实现

- **调用方式**: 默认通过 PM2 启动的本地 `127.0.0.1:510x/run` 端点，传入 JSON 参数
- **数据传递**: 当前主链路使用 Postgres + `patent_record_id` + `analysis_session_id`，`feishu_url` 仅保留历史兼容
- **模块1**: 接收 `patent_file.url` + `patent_file.file_type` + `task_id`，file_type 只接受 `image` 或 `video`
- **模块2(通用)**: 接收 `patent_record_id` + `analysis_session_id`，从 Postgres 读取模块1输出
- **模块2(健身器材)**: 接收同样主键，健身器材专用关键词生成
- **模块2(家用电器)**: 接收同样主键，家用电器专用关键词生成
- **模块3**: 接收 `patent_record_id` + `analysis_session_id` + `input_keywords`，写入商品检索表
- **模块4**: 接收 `patent_record_id` + `analysis_session_id`，返回并落库比对结果

### 环境变量

| 变量名 | 说明 |
|--------|------|
| `MODULE1_API_URL` | 模块1 API 端点 (https://zzctsm7xqm.coze.site/run) |
| `MODULE1_API_TOKEN` | 模块1 JWT Token |
| `MODULE2_API_URL` | 模块2(通用) API 端点 (https://h8qmyd62sq.coze.site/run) |
| `MODULE2_API_TOKEN` | 模块2(通用) JWT Token |
| `MODULE2_FITNESS_API_URL` | 模块2(健身器材) API 端点 (https://5rwr6pmzk3.coze.site/run) |
| `MODULE2_FITNESS_API_TOKEN` | 模块2(健身器材) JWT Token |
| `MODULE2_HOME_APPLIANCES_API_URL` | 模块2(家用电器) API 端点 (https://9bq6x5jqkb.coze.site/run) |
| `MODULE2_HOME_APPLIANCES_API_TOKEN` | 模块2(家用电器) JWT Token |
| `MODULE3_API_URL` | 模块3 API 端点 (https://sk2jw6vshq.coze.site/run) |
| `MODULE3_API_TOKEN` | 模块3 JWT Token (已更新) |
| `MODULE4_API_URL` | 模块4 API 端点 (https://36yvrn7jt4.coze.site/run) |
| `MODULE4_API_TOKEN` | 模块4 JWT Token |
| `COZE_BUCKET_ENDPOINT_URL` | S3 存储端点 |
| `COZE_BUCKET_NAME` | S3 存储桶名 |
| `FEISHU_APP_ID` | (可选) 飞书开放平台 App ID，用于读取多维表格 |
| `FEISHU_APP_SECRET` | (可选) 飞书开放平台 App Secret，用于读取多维表格 |

> 前提条件：推荐从仓库根目录用 `pm2 start ecosystem.config.cjs` 启动 6 个本地模块和 Portal。步骤6优先从本地结构化数据提取，飞书 API 仅为历史兼容备选。

## 构建与开发命令

- **开发**: `pnpm dev` (端口 5000, HMR)
- **构建**: `pnpm build`
- **类型检查**: `pnpm ts-check`
- **Lint**: `pnpm lint`
- **生产启动**: `pnpm start`

## 关键代码定位

| 功能 | 文件 | 说明 |
|------|------|------|
| 分析编排 | `src/app/api/analyze/route.ts` | 异步轮询，6步骤串行调用+计时 |
| 关键词/新模块三/模块四测试 API | `src/app/api/test/product-pipeline/route.ts` | 测试模块2、新模块3 5107、模块4 5106 的实验链路 |
| 关键词/新模块三/模块四测试页 | `src/app/test/product-pipeline/page.tsx` | 手动关键词或模块2关键词驱动商品详情/图片检索，并预览模块4结果 |
| 侵权同 session 管理员诊断 API | `src/app/api/admin/patent-analysis/session/[id]/route.ts` | 只读恢复 Agent 本次四阶段 run、部分输出、候选排除原因和错误 |
| 工作流调用 | `src/lib/workflow-client.ts` | 扣子编程项目 /run 端点调用 (含行业路由+预热+重试) |
| 飞书数据读取 | `src/lib/feishu-client.ts` | 飞书多维表格 API (备选数据源) |
| 类型协议 | `src/lib/types.ts` | SSE 事件协议、业务类型 (5步骤) |
| 状态存储 | `src/lib/analysis-store.ts` | 内存存储 (globalThis 防HMR丢失) |
| 文件上传 | `src/app/api/upload/route.ts` | S3 上传 + 签名 URL 生成 |
| 前端分析流 | `src/hooks/use-analysis.ts` | 轮询消费 + 状态管理 |
| 比对表格 | `src/components/claim-chart-table.tsx` | Claim Chart 可展开行 |
| 上传表单 | `src/components/upload-form.tsx` | URL/文件/粘贴文本三模式 |

## 开发规范

### Hydration 问题防范

1. 严禁在 JSX 渲染逻辑中直接使用 typeof window、Date.now()、Math.random() 等动态数据。**必须使用 'use client' 并配合 useEffect + useState**
2. **禁止使用 head 标签**，优先使用 metadata

### UI 组件规范

- 项目预装 shadcn/ui，位于 `src/components/ui/`
- 必须默认采用 shadcn/ui 组件和风格

### 文件上传规范

- 使用 `coze-coding-dev-sdk` 的 `S3Storage` 进行对象存储
- 必须使用 `generatePresignedUrl` 生成访问 URL，禁止自行拼接
- 上传后必须使用返回的 key，而非 fileName

### 轮询协议规范

后端 `/api/analyze` POST 立即返回 `{sessionId}`，前端每3秒轮询 `/api/analysis/[id]`。
会话状态包含6个步骤，每个步骤状态：
- `pending` — 未开始
- `running` — 执行中
- `completed` — 完成
- `error` — 出错（不阻塞后续步骤）

前端通过 `useAnalysisStream` Hook 管理轮询和状态同步。

## 包管理规范

**仅允许使用 pnpm** 作为包管理器，**严禁使用 npm 或 yarn**。

## 2026-07-06 更新

- 新增 `/test/product-pipeline` 页面，并在 `/test`、`/test/module1` 顶部增加入口。
- 新增 `/api/test/product-pipeline`，支持单独跑关键词生成、新模块3商品详情检索、模块4比对或全链路。
- 真实验证：`patentRecordId=101`、`analysisSessionId=module_test_codex_product_1782690000`、关键词“扫地机器人/拖地/升降”调用新模块3成功返回 1 个商品、18 张图片、详情描述；同一 session 调用模块4成功返回 1 个商品 8 条特征比对。
- 验证状态：`pnpm ts-check`、新增文件单独 eslint、`pnpm build` 均通过；已重启 `patent-web`。全量 `pnpm lint` 仍因既有 `src/app/module1/page.tsx` 条件 Hook 错误失败。
- `module_test_1783352605979` 的问题不是前端没触发请求，而是该 session 没有关键词来源；新模块3内部返回 failed“未找到模块2关键词”，旧测试页却按 HTTP 200 显示完成，模块4随后在 0 商品下空跑。
- `/api/test/product-pipeline` 已新增 `GET` 示例专利列表，读取 `module1_abstract_examples_1782749163_%` 测试记录和已有关键词/新模块3/模块4运行摘要；前端测试页可选择示例专利并自动填入 `patentRecordId`、示例关键词和新的 `analysisSessionId`。
- 测试 API 步骤现在返回 `completed/failed/skipped`，并在关键词缺失、模块3 failed、accepted 为 0、商品为空、模块4无结果时返回 `ok:false` 和可读诊断；`action=all` 会在上游失败时跳过下游，避免误导用户。
- 测试页新增示例专利状态表、步骤诊断和新模块3候选诊断；failed/skipped 不再显示为绿色完成。重放 `module_test_1783352605979` 无关键词请求时，页面/API 会明确提示“没有可用于新模块三的关键词”。
- 2026-07-06 已重新构建并重启 `patent-web`。入口验证：`/test/product-pipeline` 返回 200，`/api/test/product-pipeline` 返回 13 个示例专利，缺关键词重放返回 `ok:false`。

## 2026-07-14 更新

- `module_test_1783863097738` 的模块2实际成功生成结果：2026-07-14 的 `keyword_run_id=139` 有 9 条关键词。页面约 301 秒显示 `fetch failed` 是 Node fetch/Undici 默认 300 秒响应头超时，而模块2本轮约 308 秒才完成。
- 长耗时工作流 POST 不再使用内置 fetch；`src/lib/long-running-http.ts` 以 Node HTTP/HTTPS 客户端执行请求，让测试链路和正式编排链路设置的业务超时真正生效。
- 重启电脑后已通过根 `ecosystem.config.cjs` 恢复全部 PM2 服务，3001、5101-5107 健康检查通过。
- 修复既有生产 Babel 配置：开发 Inspector 插件和 React `development` JSX runtime 现在只在 development 启用，避免 Webpack 生产预渲染 `_not-found` 时出现 `jsxDEV is not a function`。当前环境的 Turbopack compile 会卡住，`scripts/build.sh` 已固定使用 `next build --webpack`。

## 2026-07-15 关键词确认边界

- 正式分析读取模块2结果时必须同时限定 `patent_record_id`、`analysis_session_id` 和本次 `keyword_run_id`，禁止跨 session 或跨 run 复用旧关键词。
- `filterExecutableKeywordRecords` 是模块3前的最终确认门：拒绝旧的 `必要特征基础词`、未通过模块2 guard 的记录，以及不含产品客体的关键词。
- Portal 将确认后的 `objectTerms` 作为 `input_object_terms` 传给正式模块3，供商品跨品类过滤；为空时保持兼容，但不得重新放行有明确失败元数据的关键词。

## 2026-07-16 安盾模块三测试入口

- `/test/andun-search` 与 `/api/test/andun-search` 只调用本机 `ANDUN_MODULE3_API_URL || http://127.0.0.1:5108`，不修改正式 `MODULE3_API_URL`。
- 页面通过 `/async_run` 启动任务并每 3 秒读取 `/runs/{id}`，展示 taskId、0/10/20 阶段、提交/总耗时、每次 API 调用耗时和安盾商品字段。
- 未配置 `ANDUN_APP_KEY` / `ANDUN_APP_SECRET` 时必须显示明确警告和 error run，不得显示 completed。

## 2026-07-23 智慧芽 P002 查询生成

- 修复 P002 查询按数组 `.slice(0, 4)` 截断导致中文同义词占满名额、英文全部丢失的问题；主题组和每个特征组现在分别保证语言平衡。
- I2 提示词要求纯中文/纯英文、忠实字面名词短语、简洁构件/结构名词和规范英文连字符；Portal 编译器再确定性过滤中英混杂项、孤立方向词和含 `and/or/not` 的供应商语法风险短语。
- 主查询零命中时只允许一个独立 `compact-fallback` run，每个 P002 run 的实际 attempt 仍为 1；两条都为零时页面明确要求人工调整，不伪造候选。
- 真实验证：I2 run `f22143a7-9a94-41f2-8c75-4257ddd4aa12` 使用 `glm-4.6v` 和 2 张附图；P002 run `f40569d7-ee2d-4456-a047-e6f85165f344` 首条返回 10 条 lead、总命中 94，响应 SHA-256 为 `79aed1fd79765df8d3cf8c60afe0bb910434956ae6d5e98be2229ed72b5d16f0`，没有触发回退。
- 调查 `386c60ef-4ea8-58c3-969d-c7f514a6ade5` 的 I2 run `157b3b0b-d791-47e8-b6c0-fd217256d67f` 实际已绑定 `头戴`、`中空孔` 两个不同必要特征；旧编译器把主题“开放式头戴耳机”中出现的“头戴”误判为主题重复，因而在 P002 前失败。现改为只排除与完整主题等同的词项，并在首条查询不合规时继续选择后续合规查询；对应回归还约束英文孔结构不得缩成孤立通用构件词。
- 修复后复用该 I2 快照的真实 P002 run `2d8cdfdc-e97f-4ff1-8fac-d580cd212f6c` 单次 attempt 成功返回 10 条 lead，`actual_provider=patsnap`、`network_used=true`，供应商响应工件 SHA-256 为 `ae4bd3d0c973613e5031708894ce0f6031727c053804b190f39a12051f1f2175`。

## 2026-07-25 无效检索律师版模块实验室

- `/test/module-lab` 重构为六个律师业务模块：目标专利、关键日、检索方案、现有技术、有效性分析、报告。底层 11 个技术模块继续通过 `/v1/lab/module-runs` 运行，但只在折叠的“技术运行记录（供 Codex 排错）”中展示。
- 每张模块卡恰好两个按钮：“用当前案件测试”和“用内置示例测试”。页面不再包含模块级 manual JSON、provider、UUID 输入框或中间确认表单；真实案件由上方 `UploadForm` 创建一次，页面用 `?investigation=<id>` 恢复并复用。
- 当前案件的普通状态、进度、人工确认数量和报告摘要只统计 `in_scope=true` 的独立权利要求。历史从属行仍可从后端审计数据读取，但页面单独解释其被排除的原因。
- 历史调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 的页面验收结果：专利 CN216205649U《水枪》，只展示独立权利要求 1 为 failed 且无独立权利要求待人工确认；旧版人工确认来源明确显示为从属权利要求 17，旧 I2 错误翻译为格式兼容问题。
- Portal 契约测试新增六模块、双入口、无模块 JSON 编辑器、URL 恢复、独立权利要求文案和折叠审计区断言。`pnpm ts-check`、定向 ESLint、契约测试与 production build 均通过，`patent-web` 已重启。

## 2026-07-26 无效模块一律师视图

- `/test/module-lab` 的模块一完整展示著录日期、摘要、全部权利要求及依赖关系、结构化说明书和全部附图；后续模块仍只处理独立权利要求。界面必须明确区分“模块一读取范围”和“后续检索范围”。
- 附图只通过 `/api/test/invalidity/module-figure/[moduleRunId]/[figureIndex]` 读取。Portal 先校验登录用户对 module run 的归属，再代理到 5209 的哈希校验二进制接口；禁止把 bearer token 或后端本地路径交给浏览器。
- 历史水枪案件实际验收：页面显示 19 页、17 项权利要求、5 个说明书正文部分和 10 幅附图，所有图片成功加载且无横向溢出。生产构建使用 8GB Node 堆完成，专用 `.pm2-patent-prod` 中只重启 `patent-web`；正式无效服务 5109 未触碰。

## 2026-07-26 发明点与检索字段律师视图

- `/test/module-lab` 的模块三不再以“必要技术特征”作为主展示，而是直接展示保护客体、发明点摘要、最能说明发明点的特征、类别通常具备的语境特征，以及每条检索式的角色和检索范围。
- P002 编译器读取 `query_role` 与 `search_scope`：全文为 `TACD`、权利要求为 `CLMS`、题名摘要为 `TTL OR ABST`，分类锚点为 `IPC`；旧 run 没有这些字段时仅为可审计兼容保留原 TACD 行为。
- 模块四必须执行 I2 第一轮计划中的全部专利检索角色，不能再只选一条 ordinary query。零命中回退保持原角色与字段范围，不把回退悄悄扩成全文宽搜。
- 水枪回归覆盖“水枪分类/客体 + 位置选择性联接装置 → 全文”和“水枪/液体喷射 + 阀 + 罐 → 权利要求”；实现不维护水枪专用词表。
- Portal `pnpm build`、`pnpm ts-check`、定向 ESLint 和无效契约测试通过；全量 ESLint 仍被本轮范围外 `src/app/module1/page.tsx` 的既有条件 Hook 调用错误阻塞。浏览器实际运行模块三内置案例，普通视图仅展示保护客体、发明点、类别语境、角色和检索范围，不出现 JSON/provider/确认表单。正确的 `/Users/xiexiaoxiong/.pm2-patent-prod` 中仅重启 `patent-web`，正式无效 5109 未触碰。

## 2026-07-26 复合概念部分词编译

- P002 对带明确 `query_role` 的新 I2 查询保留最多三个已核验 feature keyword group，不再一律裁到角色最低数量；因此复合发明点中的“位置选择性联接装置 + 中间位置”和语境线中的“压力罐 + 阀导管 + 阀杆”都能原样进入相应字段检索。
- 旧快照没有 `query_role` 时继续按历史最小组数编译，避免改变历史重放结果。编译器仍只接受能映射回具体 limitation 的实际表达式词，不信任孤立 ID 声明。
- 模块三失败时不再误报“识别 0 个发明点、形成 0 条检索式”；普通视图改为说明“没有形成可执行检索式”，并保留下方具体守门原因。
- 验证：`pnpm ts-check`、定向 ESLint、无效 Portal 契约测试、8GB Node 堆 Webpack production build 和 tsup 均通过；本地 `patent-web` 已重启，匿名访问 `/test/module-lab` 正确 307 到登录页。

## 2026-07-26 检索式三类任二

- P002 编译器与后端 I2 使用同一个可执行规则：真实发明点/技术特征关键词、保护客体/类别、分类号三类中任意两类实际存在即可，三类同时存在也通过；`query_role` 只决定全文、权利要求或题名摘要范围，不再附加固定特征组数门槛。
- 模型建议分类号可参与 `IPC` 编译，但必须由 I2 标记为 `model_suggested`；Portal 不把它显示或回写为目标专利著录事实。
- 回归新增“客体 + 一个真实特征”和“模型建议分类号 + 一个真实特征、无客体”两种合法查询。水枪真实 I2 run `c8dfe3b0-b88f-48dc-b7a3-c93236e5cac1` 的发明点全文线和权利要求语境线均由 Portal 编译器成功编译。
- `node --import tsx scripts/test-invalidity-portal-contracts.ts`、`pnpm ts-check`、定向 ESLint 和 8GB Webpack production build 通过；只重启专用 PM2_HOME 中的 `patent-web`，3001 登录页正常，模块实验室匿名访问按预期 307 跳转登录。

## 2026-07-26 模块四论文全文展示

- `/test/module-lab` 的模块四在真实 NPL 候选中优先尝试带 HTTP(S) `pdf_url` 的文献，再按原顺序补足取文名额，避免前三条元数据线索挤掉可直接取得的论文全文。
- 普通律师视图区分“已发现 PDF 全文地址”“已取得全文”“仅检索线索”。发现 URL 不等于取得证据；只有后端实际下载、校验、冻结并计算哈希后才显示“已取得全文”。
- 模块四摘要显示本轮有多少论文候选具备可取 PDF 路由，并继续展示日期资格结果。即使全文已取得，公开日或技术披露未核验时也不得显示为合格对比文件。
- 真实取文验证使用 module run `fd73a074-dbea-4a28-9b0f-c84246dda6de`；论文被成功冻结为 `retrieved_document`，但后续日期资格保持 unknown，界面语义与证据守门一致。
- 验证：`pnpm ts-check`、模块实验室定向 ESLint、无效 Portal 契约测试、8GB Webpack production build 和 tsup 均通过；专用 `/Users/xiexiaoxiong/.pm2-patent-prod` 中只重启 `patent-web`，匿名访问 `/test/module-lab` 正确 307 到登录页。

## 2026-07-28 模块五持久化批次

> 2026-08-09 后本节只作为历史实现记录：十一阶段架构下该历史表/API 被模块六复用为纯
> I4-S 持久化批次，不再自动创建 I4-C/I4-I；最新地址参数为 `module6_batch`，仍兼容读取旧
> `module5_batch`。

- 新增 `invalidity_test_module5_batches` 和
  `/api/test/invalidity/module5-batches`。POST 在创建子任务前保存整批 1—50 篇文献清单；
  GET 恢复/推进每篇 I4-S、I4-C 与 I4-I。表和每个 module run 均绑定 Portal 用户，批次
  ID 写入 `?module5_batch=`，页面或 `patent-web` 重启后继续原批次。
- 页面内存中没有模块四结果时，POST 可用 `recover_persisted_documents=true` 请求 5209
  从同一 investigation/claim 的持久化成功谱系恢复全文文献；不能回退到题录、摘要或其他
  用户案件。
- 每篇 I4-S 普通瞬时失败最多批次补跑 1 次；只有 provider `1210` 可在后端页面降级后再
  多 1 次。I4-I 的 `1210/1261` 载荷拒绝最多持久化补跑 1 次。批次必须等当前所有文献
  成功才运行 I4-C/I4-I，历史失败 run 只供审计。
- 模块五普通视图显示已提交/收口数量、独立权利要求特征、全部文献清单、逐篇 Claim Chart、
  D1 与组合结果。组合 gap 优先显示 `feature_text + rationale`，通用 gap 显示中文原因，
  不再以“未说明的证据缺口”代替后端已有事实。
- 真实批次 `4dcc04fb-795b-4b6f-8061-45ad4108f3df` 已在页面恢复并完成：19/19 篇逐篇
  成功，D1 与 I4-I 均成功。Portal 类型检查、定向 ESLint、无效契约和 production build
  通过；只重启 `patent-web`，正式 5109 未触碰。

## 2026-08-09 模块六持久化恢复与逐篇进度

- 模块六当前案件不再用浏览器 `Promise.all` 直接等待全部 I4-S；统一调用历史兼容路径
  `/api/test/invalidity/module5-batches` 创建持久化批次。API/表名暂不迁移，业务语义已收敛为
  只运行 `I4_S_SINGLE_REFERENCE`，不得越级创建模块七 I4-C 或模块十 I4-I。
- 批次响应逐篇返回 queued/running/terminal、可用 disclosure、排队秒数和实际执行秒数；页面
  每轮轮询立即保留当前 children。排队/运行项不再按失败渲染，普通模块等待上限从 10 分钟扩为
  2 小时，模块六批次轮询上限为 4 小时；即使当前页面停止轮询，后台 run 和已完成结果仍保留。
- URL 新写 `module6_batch` 并兼容旧 `module5_batch`。如果历史直接 I4-S 已完成但没有 batch，
  页面会通过 5209 返回的最新 I3 candidate-filter 血缘重建完整 analysis-ready 文献清单：已有
  I4-S 收编复用，清单内缺失 I4-S 的文献自动补建；不再只恢复已经创建过比对的文献，也不按
  固定时间窗猜批次。所有收编 run 仍绑定当前 Portal 用户和 investigation。
- `pnpm ts-check` 和 Portal 无效合同通过。全仓 `pnpm lint` 仍被既有
  `src/app/module1/page.tsx:121` 条件 Hook 错误阻断；本次改动文件没有新增 lint 错误。
- 真实案件恢复接口验证为 31 篇可分析全文、28 篇已有成功比对、3 篇待补建；补建后 31/31 均有
  成功 I4-S。`bash ./scripts/build.sh`、类型检查、无效合同和改动文件定向 ESLint 均通过，Portal
  已重启；未登录 `/test/module-lab` 正确 307 至登录页。

## 2026-07-30 模块实验室论文检索输入

- `/test/module-lab` 运行 I3-NPL 时不再只提交检索式字符串；现在同时提交
  `text`、`subject_terms` 和 `feature_terms`，使后端能够核验实际论文查询包含保护客体与
  技术/机构特征。
- 专利 IPC/CPC 分类字段不传给论文来源，也不能替代论文关键词。共同输入无效由后端在四个
  provider fan-out 前统一拒绝，页面不再显示四条相同 `QueryValidationError`。
- 历史快照中的四条相同 `QueryValidationError` 也在普通律师视图合并成一条中文解释：
  共同输入缺少“保护客体 + 技术/机构特征”，请求实际未发往四个来源；真实 provider
  故障仍逐源展示。
- `pnpm ts-check`、模块实验室定向 ESLint、无效 Portal 契约测试和 8GB production build
  通过；`patent-web` 已重启，正式无效 5109 未触碰。

## 2026-07-30 模块四取文前候选清理

- `/test/module-lab` 的真实模块四在所有 I3-P/I3-NPL 检索完成后，先用这些持久化
  `moduleRunId` 创建 `I3_CANDIDATE_FILTER`；前端不提交候选文献内容，也不自行判断重复或
  相关性。只有后端返回的 `fetch_candidates` 进入 I3-FETCH，且不再二次 `.slice()`。
- 律师普通视图保留每条查询的原始前 10 条，逐篇显示“明显无关，未取文”“同申请/同版本，
  已合并”或实际取文状态；顶部显示原始、重复、排除、取文和节省请求数。模型失败时明确
  说明按高召回原则全部保留，不能把失败伪装成已排除。
- `runLabChild` 现在保存真实 `moduleRunId`，供下游模块只引用持久化运行；模块四业务完成
  条件按清理后的唯一代表计算，不再要求已明确排除/合并的原始记录也各自取文。高级技术页
  的模块代码列表同步增加 `I3_CANDIDATE_FILTER`。
- live `I3_FETCH` 的前端输入进一步收紧为
  `candidate_filter_run_id + candidate_id`；Portal 不再把 `document`、provider、stage 或
  hash 回传给取文模块。后端只从同案、同 claim 的成功候选清理 run 回读保留代表，前端
  篡改题录会在 provider 调用前被拒绝。
- 验证通过：`pnpm ts-check`、模块实验室/高级页/契约脚本定向 ESLint、无效 Portal 契约
  测试和 8GB production build；后端全量为 `465 passed, 7 skipped`。`patent-web` 已在
  专用 PM2_HOME 重启，3001 登录页为 200、模块实验室匿名访问正确 307 到登录页；正式
  5109 未触碰。

## 2026-07-30 模块三机构等价词二次守门

- 后端 I2 的可执行主锚点改为目标权利要求原词/可回溯发明点短语，作用等价词只作为同一
  feature 的 OR 扩展。Portal P002 编译器不能无条件信任历史 `feature_term_groups`。
- Portal 在编译每个 feature 组时按主锚点和 limitation 原词核验动作机理；“选择性联接”
  可以保留“选择性接合”一类同动作表达，但不能放行“锁定机构/解锁机构”。旧 run 即使
  已持久化污染组，也不得把这些词发给智慧芽。
- 契约测试显式向历史 feature group 注入锁定/解锁词，并断言最终 provider expression
  保留“位置选择性联接装置/选择性接合机构”而剔除锁定/解锁。

## 2026-07-30 模块三原子关键词二次守门

- P002 编译前会把旧快照中的顿号、逗号、分号并列词拆成原子技术概念，并识别可复用的
  “位置 + 选择性联接”说明文本。完整单一技术名词如`位置选择性联接装置`保持整体。
- 当一个历史 feature 同时带`中间位置`与`选择性联接`时，短线锚点优先选择后者；位置词
  不会作为同义词进入同一个 OR 组。若后端明确输出两组，Portal 才按两个 AND 组编译。
- 动作型短词不再自动生成`选择性联接结构`一类结构碎片。契约测试显式注入
  `中间位置、选择性联接`并断言最终锚点为`选择性联接`、检索组不含拼接短语或位置同义项。
- `pnpm ts-check`、定向 ESLint、无效契约和 production build 通过；`patent-web` 已重启，
  3001 模块实验室鉴权跳转正常。全量 lint 仍被范围外`src/app/module1/page.tsx`既有
  条件 Hook 错误阻塞；正式 5109 未触碰。

## 2026-07-31 模块五整体结构证据展示

- `/test/module-lab` 的模块五逐篇 Claim Chart 与模块六/报告附录使用同一组结构证据字段：
  目标结构角色、对比文件对应结构、映射依据、结构证据、集成构件标记、全文候选结构排查、
  必要性链和替代路径分析。`mapping_basis` 统一显示为逐字对应、角色和关系对应、结构等同、
  工作过程必然隐含、仅功能相似、未找到结构对应或待核对。
- 律师普通视图仍先显示简短判断、理由和主引文；上述详细依据折叠展示，避免把技术审计字段
  全部平铺。`integrated_structure_mapping` 只有后端真实返回布尔值 `true` 时才显示。
- Portal 只负责展示后端 I4-S 已守门事实，不在浏览器内把 `reservoir`、阀芯、阀体流道等
  自动改判为披露，也不根据功能相似自行升级状态。
- `structural_review_status=model_error` 在批次接口中不算成功：普通传输错误自动补跑一次，
  provider `1210` 仍按原有有界策略处理；最终失败时阻止 I4-C/I4-I，不能用首轮残缺结果
  冒充整批完成。页面显示复核是否完成、分析遍数、规则版本和安全错误码。
- 页面恢复历史/持久化报告时，同时兼容顶层 disclosure 与 `disclosure.analysis` 嵌套结构；
  “尚未排除合理替代路径”只在 `necessarily_implicit_from_operation` 映射下显示，普通结构等同
  不再被错误标成隐含披露论证。

## 2026-08-04 模块实验室七业务模块（原模块三拆分）

- 按用户决定：`/test/module-lab` 原业务模块三「制定检索方案」拆分为两个相互独立的业务模块，
  总数由六个变为七个——新模块三「总结核心发明点」（technicalCodes `I2_INVENTIVE_PROFILE`，
  视图展示 `invention_summary` 核心发明点总结一段话 + `inventive_point_features` 及其按
  feature_id 绑定 `limitations` 的技术特征原文，不展示检索式）；新模块四「生成检索关键词」
  （沿用 `I2_QUERY_PLAN`，保留「首轮分线检索方案」检索式视图）。两个模块互不依赖，模块四
  不读取模块三运行结果；原模块四至六（现有技术/分析/报告）顺延为五至七。
- 宪章 2.7、WORKFLOW_SPEC 3.12 已同步。`scripts/test-invalidity-portal-contracts.ts` 断言
  同步更新为七模块结构。
- 验证：`pnpm ts-check` 通过、定向 ESLint 干净；契约测试除 1757 行一处既有失败
  （`fallbackOfRunId` 断言针对未改动的 `/test/invalidity` 页面，非本次引入，待跟进）外全部
  通过；隔离测试栈 5209 重启后 fixture 模式真实跑通 `I2_INVENTIVE_PROFILE`（run=succeeded）。
  详见根 AGENTS.md 2026-08-04 会话日志。


## 2026-08-04 模块四输入改为模块三输出

- 按用户最新决定（取代同日「两个模块相互独立」的表述）：`/test/module-lab` 模块四
  「生成检索关键词」的输入固定为模块三「总结核心发明点」的输出，不再展示专利摘要、说明书
  或权利要求原文。`BusinessInputDetails` 的 'query' 与 'profile' 分支拆开：模块四输入区
  按 `output.source_plan_run_id` 在 `allBusinessStates['profile'].children` 中精确匹配
  模块三 run（找不到时回退同 `claim_id` 最新 ok 的 `I2_INVENTIVE_PROFILE` run），展示
  `invention_summary` 核心发明点总结 + `inventive_point_features` 按 feature_id 绑定
  `limitations` 的技术特征原文；找不到任何模块三输出时显示「未找到模块3的输出，请先运行
  模块3」。BUSINESS_MODULES 模块四 input 文案改为「输入：模块3总结的核心发明点与技术特征」。
- dispatch 门控（仅真实案件 `runCurrentBusiness` 路径）：逐独立权利要求检查同 claim 是否
  存在成功 `I2_INVENTIVE_PROFILE` run，没有则不发 POST，push 本地 `status:'failed'`
  ChildRun（error「请先运行模块3总结核心发明点，模块4才能生成检索关键词」），复用
  `summaryForBusiness` 的 failed 错误展示；fixture 路径不变。`friendlyError` 新增
  `LAB_I2_PROFILE_REQUIRED` → 「需要先在模块3总结核心发明点，模块4才能生成检索关键词。」，
  覆盖模块5 `ensureFirstQueryPlan` 等未门控路径的后端错误透出。
- 契约测试 `scripts/test-invalidity-portal-contracts.ts` 新增 7 条断言（新 input 文案、
  `source_plan_run_id` 匹配、模块三输出展示、门控提示、错误映射）。
- 验证：`pnpm ts-check`、定向 ESLint 干净；契约测试（项目内临时副本注释既有
  `fallbackOfRunId` 失败断言后）`invalidity portal contract tests passed`。宪章 2.8 /
  WORKFLOW_SPEC 3.13 已同步；后端配套见 `5-invalidity-search-test/AGENTS.md` 同日期条目。


### 2026-08-04 跟进（模块四「复用画像」提示改为中性说明）

- 用户反馈模块四仍显示「本次复用了同案发明画像：GLM 未重复调用；本次仅使用……既有成功
  发明画像，按当前规则重新编译检索组合」，与「每次分析都重新来、不要复用之前的结果」的
  表述冲突。经核查：这是 `QueryResultDetails` 对 `generation_source=same_source_cached_profile`
  的旧告警 Alert 直接展示后端 `generation_warning` 原文；而模块四 live 按新设计固定走
  「模块三输出 → 免模型确定性重编译」，该来源标记是必然且正常的，旧告警文案属于历史
  缓存复用语境的残留。模块三每次点击仍重新调用 GLM 全新生成画像，行为本身不变。
- 修改：`QueryResultDetails` 中该 Alert 改为中性蓝色说明「本次使用模块3总结的核心发明点
  与技术特征编译检索关键词，未重复调用 GLM；如需更新发明点分析，请重新运行模块3。」，
  律师视图不再展示后端 `generation_warning` 原文（该字段保留用于高级/审计入口，后端
  `analysis.py` 重编译契约未改）。模块三 `ProfileResultDetails` 的防御性告警保留（模块三
  live 对缓存画像 fail closed，正常不会触发）。契约测试新增新文案断言。
- 验证：`pnpm ts-check`、定向 ESLint 干净、契约测试（临时副本注释既有 `fallbackOfRunId`
  失败断言后）通过；Portal 重建并重启 patent-web。后端无改动，5209 未重启。


## 2026-08-05 模块四检索词改由大模型生成（文案同步）

- 按用户决定：模块四仍以模块三输出为输入，但生成检索词重新调用 GLM（后端
  `generation_source="module3_profile_live_model"`，`network_used=true`）。
- `QueryResultDetails` 说明框条件扩展为 `['module3_profile_live_model',
  'same_source_cached_profile']` 两个来源值：新值显示「本次由大模型基于模块3总结的核心
  发明点与技术特征分析生成检索关键词；如需更新发明点分析，请重新运行模块3。」；旧值
  （2026-08-04 白天的确定性重编译 run）保留原文案。模块四输入区顶部说明补半句
  「……检索关键词由大模型基于模块3的总结分析生成。」
- 契约测试断言同步更新为新文案并新增 `module3_profile_live_model` 来源值断言。
- 验证：`pnpm ts-check`、定向 ESLint 干净；契约测试（临时副本注释既有 `fallbackOfRunId`
  失败断言后）`invalidity portal contract tests passed`。宪章 2.9 / WORKFLOW_SPEC 3.14
  已同步；后端配套见 `5-invalidity-search-test/AGENTS.md` 同日期条目。

## 2026-08-07 I2 v3 固定五线 Portal 收口

- `invalidity-p002-test-flow.ts` 已识别并校验 I2 v3 五个固定 `query_variant`，保留后端冻结的
  `provider_expression`，并对允许/必需字段、申请人唯一性、目标公开号本地排除元数据和固定线禁用
  `compact-fallback` 做确定性守门；旧变体继续只用于历史快照兼容。
- 真实 P002 已验证 `AN` 有效但供应商侧 `NOT PN` 返回 `68300004`；Portal 因此只发送
  `AN + TTL/ABST`，把 `excluded_publication` 作为顶层 I3 输入交给后端，在原始响应冻结后
  本地排除目标专利及其 A/B/U/S 文献种类变体并保留排除记录。
- `/test/invalidity` 不再只执行首条检索式，也不再在零命中后自动裁短；页面按 `query_id`
  顺序为本轮全部普通专利线各创建一个 I3 run，逐条校验真实 Patsnap 网络执行后汇总候选、
  工件和命中数。`/test/module-lab` 同步展示五类律师可读标签。
- 五案白名单的 I2 计划统一晋级到不可变目录 `20260807-five-case-i2v3-r4`，I4-S 继续使用
  `20260806-five-case-v50`。Portal 契约测试会读取这 9 个独权计划并逐条断言编译结果与冻结
  表达式完全一致，并断言固定线冻结契约与执行器均禁用 compact fallback，避免新变体被旧
  角色编译器静默降级。
- 本轮验证：`pnpm ts-check`、`scripts/test-invalidity-portal-contracts.ts`、定向 ESLint 均
  通过，`NODE_OPTIONS=--max-old-space-size=12288 pnpm build` 成功；后端全量为
  `667 passed, 7 skipped`。隔离测试后端 5201/5209 已重启加载且健康检查正常；Portal 和正式
  5109 未重启、未触碰，I4-S `20260806-five-case-v50` 冻结保持不变。

## 2026-08-07 固定五线页面加载修复

- 用户看到旧示例的直接原因是 `patent-web` 启动时间早于 8 月 7 日 `.next`/`dist` 构建时间；
  源码和 `CASE_FREEZES` 已是 r4，但运行进程未加载。本轮完成重建并只重启专用 PM2_HOME 的
  `patent-web`，3001 已加载新 bundle。
- `QueryResultDetails` 新增固定五线状态说明：合规结果显示“固定五类，实际生成 N/5 条”，并
  解释必要事实缺失时可以少于 5 条；旧 variant 结果标为历史方案并要求重新运行模块四。
- `runEvidenceChain` 删除 I2 无查询时构造旧 `claim_context_recall` 的 fallback；仅接受 1—5 条
  普通专利线且 variant 必须属于固定五类，否则明确报错并停止。契约测试新增显示和禁回退断言。
- 验证：`pnpm ts-check`、定向 ESLint、无效检索契约测试通过；12GB 有界堆 production build
  成功；`/login` 200、`/test/module-lab` 匿名 307、测试代理匿名 401 均符合预期。正式 5109
  与隔离测试 5201/5209 未触碰。

## 2026-08-08 五轮差异检索口径与大 Claim Chart

- Portal 的自动预算口径改为“初始首轮之外最多 5 个 gap 检索轮”，`invalidityAutomaticRounds`
  默认 5、只接受 1—5；创建调查和测试入口均发送 5，页面文案不再称自动三轮。
- `/test/module-lab` 模块六在 I4-C 成功后展示 D1 加最多 5 篇高增量日期合格文献的大 Claim
  Chart；律师报告读取 ReportDataV1 的 `large_claim_charts` 输出同一横向表。逐篇 CC 仍保留，
  大表不在浏览器内重新作披露判断。
- 五案示例白名单本轮没有切换：r4 的检索式数量符合“固定五类、事实齐备者每类至多一条、
  总数不超过五条”，但画像来自旧冻结数据，不是当前模块三/四两次 GLM 的真实重跑。r5 生成
  需要向当前外部 GLM 发送测试专利正文/附图，尚待用户明确授权；页面继续如实显示 r4。
- 验证：`pnpm ts-check`、`node --import tsx scripts/test-invalidity-portal-contracts.ts`、定向
  ESLint 和 12GB 有界堆 production build 均通过；`patent-web` 未重启，待 r5 冻结完成并切换
  白名单后再部署，避免把仍指向 r4 的构建误称为示例更新。

## 2026-08-08 I2-G 后端收口后的 Portal 状态

- 后端后续轮现由单一 `I2_GAP_QUERY_PLAN` 同时输出复用决定与实际查询；既有语料覆盖全部差异
  时会返回 `queries=[]` 并继续组合评价/大 Claim Chart。Portal 本轮无需更改显示或执行代码，
  现有通用 QueryPlan 与 `large_claim_charts` 读取路径兼容该输出。
- 五案 `CASE_FREEZES` 仍明确指向 `20260807-five-case-i2v3-r4`。r4 数量满足固定五类最多五条，
  但画像来自旧冻结结果；未获得向外部 GLM 发送五案正文/附图的明确授权，未生成、未切换 r5，
  也未重建或重启 `patent-web`。本轮 Portal `pnpm ts-check` 与无效检索契约脚本通过；后端全量
  回归 `676 passed, 7 skipped`，只重载 5201/5209 隔离测试栈，正式 5109 未触碰。

## 2026-08-08 五案白名单切换真实 r5

- 用户授权后，后端已用当前模块三画像和模块四生成链真实重跑五案，冻结版本为
  `20260808-five-case-i2v3-r5`。五案白名单、冻结日期及 Portal 契约均由 r4 切至 r5；I4-S
  comparison 仍保持 `20260806-five-case-v50`。
- r5 的 9 个独权实际为 3—5 条，而不是强制五条；页面现加载的示例均只含固定五个 variant，
  禁用 compact fallback，OR 组上限为 5。事实不足时少于 5 条的说明继续保留。
- `pnpm ts-check` 与 `scripts/test-invalidity-portal-contracts.ts` 通过；依赖恢复后 12GB 有界堆
  production build 成功。仅重启 `patent-web`，3001 `/login`=200、模块实验室匿名访问=307、
  benchmark API 匿名访问=401；正式 5109 未触碰。

## 2026-08-08 模块实验室十阶段前端

- `/test/module-lab` 从七个折叠业务模块改为十个可分别测试的律师工作阶段：目标专利、关键日、
  核心发明点、首轮检索词、首轮检索与证据核验、单篇新颖性比对、选择 D1 与冻结区别特征、
  区别特征检索循环、创造性组合分析、大 Claim Chart 与律师报告。
- 十个阶段均保留“当前真实案件/内置示例”入口和自己的运行状态。当前案件阶段六直接消费阶段五
  已持久化文献逐篇启动 I4-S；阶段七、八、九分别单独启动 I4-C、I2-G、I4-I；阶段八重复运行
  时按上一成功 gap 轮递增且封顶五轮；fixture 仍走各技术代码冻结输出，不伪造 live 事实。
- `scripts/test-invalidity-portal-contracts.ts` 已改为精确校验十阶段、二十个测试入口和阶段六至十
  的底层代码映射。`pnpm ts-check`、定向 ESLint、Portal 契约和 12GB production build 通过；
  仅重启专用 PM2_HOME 的 `patent-web`，无后端改动。

## 2026-08-08 五案内置盲测同步十阶段

- `/test/module-lab` 的五个真实无效案件不再保留旧的“模块3/4/6”三个按钮，现与主实验室共用
  十阶段导航。模块1—4、6读取目标清单、r5 画像/检索式和 v50 单篇比对冻结结果；模块5展示
  盲测实际源文献、公开日、文件摘要和页数等来源事实。
- 五案没有独立的 I4-C/I2-G/I4-I/I5 冻结输出，因此模块7—10明确标为“冻结证据复核预览”：
  按单篇确认披露数给出 D1 复核候选，检查其他冻结文献对区别特征的覆盖线索，列出组合候选，
  并汇总大 Claim Chart。不得把这些预览冒充正式模块冻结结果、实时检索日志或律师结论。
- benchmark API 新增每项独权关键日、冻结源文献和十阶段 `stageCoverage` 证据模式；继续使用
  source-only 白名单，未读取 oracle 或决定书比对结论。类型检查、定向 ESLint、Portal 契约和
  12GB production build 全部通过；仅重启专用 PM2_HOME 的 `patent-web`，登录页 200，匿名
  模块实验室 307 到登录页，正式 5109 与隔离后端均未触碰。

## 2026-08-08 五案十阶段示例部署复核

- 五个 benchmark 案例均已共用模块实验室的十阶段导航；模块1—6读取当前冻结目标、r5、源文献
  和 v50 事实，模块7—10继续明确显示为“冻结证据复核预览”，不伪装成缺失的 I4-C/I2-G/
  I4-I/I5 live run 或法律结论。
- 当前源码已进入最新 production build，专用 PM2_HOME 下 `patent-web` 在线；`/login` 返回
  200，匿名 `/test/module-lab` 按认证设计返回 307。`pnpm ts-check`、定向 ESLint 和 Portal
  契约均通过。浏览器实例没有 Portal 登录态，因此未代用户输入凭据，未完成登录后人工点击；
  十阶段按钮数量、案例覆盖和证据模式已由源码、构建产物和契约测试验证。

## 2026-08-08 五案示例再核验与后端可读审计兼容

- 再次核对五案冻结目录：水枪/耳机各 1 项独权，电池浆料/燃气灶各 2 项，光引发剂 3 项，
  合计 9 份 r5 plan 且逐项存在 v50 comparison；五个案例继续提供模块1—10全部入口。
- benchmark 的模块1—6仍读取冻结事实，模块7—10仍明确标为 derived preview；没有新增或伪造
  I4-C/I2-G/I4-I/I5 冻结 run。Portal 契约测试、`pnpm ts-check` 和模块实验室/benchmark API
  定向 ESLint 通过。
- 后端 ReportDataV1 新增安全 `readable_document_audit` 字段，只含全文处理审计摘要，不含全文、
  本地路径或 provider 密钥；现有十阶段页面无需读取该字段即可继续工作。全仓 ESLint 仍有
  `src/app/module1/page.tsx` 的既有条件 Hook 错误，该文件不属于本次无效十阶段示例修改，未扩大
  范围处理。

## 2026-08-09 十一阶段模块实验室与示例更新

- `/test/module-lab` 新增第八阶段“显而易见性预分析”，后续顺延为第九阶段进一步检索与补证、
  第十阶段创造性组合、第十一阶段报告。每阶段继续保留当前案件/内置示例两个入口；第八阶段
  展示每个特征组的客观问题、D1 启示及是否形成完整独立路径、惯用手段、修改动机、反向教导、
  技术效果和后续检索路由。
- 五案 benchmark API 和页面均增加 `obviousnessPrecheck`。CN217770321U 内置示例明确展示出音孔
  只补惯用手段证据，以及 CN216649931U [0021]/[0031] 对定位卡槽/卡凸组的转动定位启示；删除
  旧的“现有冻结文献未确认覆盖”和“五项仍需独立证据”占位话术。未冻结 I4-O 的其他案件
  只显示保守 source-only 预分析，不冒充 live。
- `pnpm ts-check`、十一阶段 Portal 契约、定向 ESLint 和 production build 通过；仅重启专用
  PM2_HOME 的 `patent-web`，`/login`=200、匿名实验室=307。正式 5109 未触碰。

## 2026-08-09 固定五组真实检索 lineage

- 模块五逐条调用 `I3_PATENT_SEARCH` 时，除冻结 `query_plan_query_id/provider_expression` 外，
  现同步提交生成它的 `query_plan_run_id`。测试后端据此按同案同独权成功计划精确回读，避免
  已通过 I2 固定五组守门的表达式再次被旧通用规则误拒；Portal 不在浏览器内自行声明可信。
- `pnpm ts-check`、Portal 无效合同、定向 ESLint 与 production build 通过；`patent-web` 已重启。
  指定水枪案五条真实 Patsnap 检索均成功执行并返回合法零命中；正式 5109 未触碰。

## 2026-08-09 模块九 D1 与人工复核显示修复

- 模块九输入卡不再因 I2-G 顶层旧输出缺少 `document_id` 而遮住模块七结果；新输出优先读
  `closest_document_id`，旧运行按模块八 `closest_document_id`、区别特征 `d1_document_id`、
  模块七 `document_id` 顺序恢复当前 D1。
- 结果区把 `human_review_feature_ids` 合并进“仍待解决”数量，并单列“律师人工复核”。零查询
  且存在人工复核时明确显示“未解决、转律师复核、不发起 provider、但不视为已有证据覆盖”，
  不再显示“既有合格语料已经覆盖全部区别特征”。
- Portal 契约、`pnpm ts-check`、定向 ESLint 和 `scripts/build.sh` production build 通过；专用
  PM2_HOME 的 `patent-web` 已重启，`/login`=200、匿名模块实验室=307。正式 5109 未触碰。

## 2026-08-09 模块九完整补检与逐篇比对

- 模块九“用当前案件测试”不再把成功的 `I2_GAP_QUERY_PLAN` 当作整个业务阶段完成。存在查询时
  必须继续执行专利/NPL 真实检索、候选清理、并行取文、日期核验，并把所有新取得的
  `analysis_ready` 文献交给可恢复 I4-S 批次；零命中可以有审计地完成，但有全文未比对时模块九
  不能显示成功。
- gap 专利查询按冻结 `provider_expression` 原样绑定计划 run/query id，不复用首轮五线编译器；
  NPL 继续携带客体/特征词。模块九结果新增真实执行进度：已执行查询、原始命中、全文可比对、
  日期核验和逐篇比对，并在同一模块内列出新文献 I4-S 状态。复用审计最多普通展示 10 条，明确
  “缺少可复用版本键”是触发补检而非停止条件。
- 完成状态同时校验计划、全部搜索、I3-F、全部候选取文、已取得文献日期核验及全部可分析全文
  I4-S，不再因只有 plan 成功而误报。`pnpm ts-check`、定向 ESLint、Portal 契约和两次 production
  build 通过；专用 PM2_HOME 只重启 `patent-web`，正式 5109 未触碰。
- 登录态 Chrome 实际刷新 production 页面确认新模块九问题与输出文案已加载；live 同案后端链路
  4 条真实查询、36 条原始命中、15 份全文和 15 份 I4-S 全部收口成功。页面今后由同一模块卡片
  展示这些计数，技术运行记录继续保留每个 run/document 的完整明细。

## 2026-08-09 模块九可恢复五轮控制器

- 新表 `invalidity_test_module9_batches` 持久化用户、案件、独权、幂等键、当前轮、状态和 JSON
  游标；新接口 `POST/GET /api/test/invalidity/module9-batches` 每次轮询只推进一个可恢复阶段，
  依次保存 I2-G、全部专利/NPL 搜索、I3-F、I3-FETCH、I3-QUALIFY 和 analysis-ready 文献 I4-S。
- 模块实验室用 `module9_batch` URL 参数恢复批次；当前案件按钮不再调用浏览器内单轮 evidence
  chain。旧案件首次创建批次时读取 5209 `module9-cursor`，可从已完成历史 gap 轮的下一轮继续。
- 每轮验证每个 active 区别特征均有独立 ordinary-patent 主查询；完成全部 I4-S 后自动创建下一
  轮，最多第 5 轮。批次持久化上一轮表达式与失败原因，I2-G 计划守门失败最多自动尝试 3 次，
  每次 run 全部保留在技术审计。
- `active_gap_feature_ids` 是单调收缩游标：从最早成功计划恢复，逐轮只与本轮 allowed 集合取
  交集。后续计划若重新打开已关闭特征，会清空该错误计划产生的业务引用、保留原 run 审计并
  创建受限重试，不让旧 D1 的新分析覆盖冻结范围。
- 登录态 live 批次 `43c36e90-7906-4765-b351-04760a48940d` 已验证历史第 1 轮迁移、第 2/3 轮
  自动检索与 18/18、29/29 I4-S 收口、自动进入第 4 轮、计划失败原地重试以及 3 项 active gap
  范围恢复；纠正计划为 `afd1ebb3-3584-4fa9-b5e0-77b82b84d2f0`。`pnpm ts-check`、Portal 契约、
  定向 ESLint 与 production build 通过，`patent-web` 已重启；正式 5109 未触碰。
- 控制器现把前三次模型计划与后续确定性计划区分：第 4 次起设置
  `force_deterministic_gap_plan`，最多保留一次确定性部署恢复机会；I3-F 失败也持久化 attempt 并
  自动补跑一次。这样旧批次可在模型 400、候选筛选契约升级或编译规则修复后原地继续，不重做已
  成功 provider/I4-S 工作。
- 同一 live 批次最终第 2—5 轮全部收口，状态 `completed`；页面显示“全部区别特征已取得可引用
  覆盖证据”，累计 27 条检索线、43 份可分析全文。Portal `pnpm ts-check`、契约、定向 ESLint、
  production build 均通过并已重启 `patent-web`；正式 5109 未触碰。

## 2026-08-10 模块九区别特征检索卡与控制器守门

- `/test/module-lab` 的模块九主视图改为“区别特征检索组”：每个普通专利主查询一张卡，
  直接显示序号、特征文本和表达式。普通视图删除原始 feature UUID、逐条复用失败原因和重复技术
  文案；“缺少可复用版本键”统一说明为旧证据不计入覆盖、特征继续补检；附加证据通道收入
  折叠区。
- `/api/test/invalidity/module9-batches` 在接受 I2-G 结果时强制校验：普通专利主组数等于 active
  feature 数、每项 feature 恰好一次、每式只有两个双语 OR 组，且客体组不包含目标完全相同类别。
  持久化的 `previous_query_expressions` 只收录主查询，避免附助通道干扰下一轮换词。
- Portal `pnpm ts-check`、无效 Portal 契约测试和 `bash ./scripts/build.sh` 全部通过；只重启了
  专用 PM2_HOME 的 `patent-web`，`/login` 返回 200。正式 5109 未触碰。

## 2026-08-11 模块九逐轮操作与分轮展示

- `/api/test/invalidity/module9-batches` 新增 `awaiting_next_round`、`round_partial` 两个可恢复暂停
  状态，以及 `continue_next_round`、`retry_failed` 操作。正常一轮不再自动 push 下一轮；失败
  重试按 document 维护 I4-S attempt 列表，仅当前尝试参与业务统计。
- 批次响应新增 `rounds/current_round`、逐轮计划/检索/命中/全文/资格/I4-S 统计和继续/重试能力
  标志。模块实验室按 `roundIteration` 分组展示，累计统计独立显示，并提供“仅重试本轮失败文献”、
  “继续下一轮”和显式“保留失败并继续下一轮”按钮。
- `pnpm ts-check`、模块九/Portal 契约和本次改动文件定向 ESLint 已通过。全仓 lint 仍被既有
  `src/app/module1/page.tsx` 条件 Hook 错误阻断；该文件不在本次范围内。`scripts/build.sh`
  production build 通过并只重启 `patent-web`，`/login`=200、匿名模块实验室=307。正式 5109
  未触碰。

## 2026-08-12 模块九查看本轮与开启下一轮

- 批次响应新增 `inherited_latest_iteration/started_from_history/continuation_reason`，普通页面
  明确展示本批次实际第几轮、是否从历史游标续跑、为什么有或没有下一轮。
- 模块九输出新增“查看本轮逐篇比对及逐特征判断”锚点；每篇 I4-S 使用可展开表格直接展示
  技术特征、判断、对比文件原文、位置和理由，不再要求律师到技术运行记录中寻找。
- 操作区可继续时显示“开启第 N 轮”；第 5 轮、全部覆盖或异常终态显示禁用按钮和原因。
  已有 durable 批次的同案重新测试从第 1 轮开始，不再误继承前一 durable 批次的第 5 轮游标。
  `pnpm ts-check`、Portal 契约、定向 ESLint 和 production build 通过；仅重启 `patent-web`，
  `/login`=200、匿名模块实验室=307。

## 2026-08-15 模块九五轮检索视角展示

- 模块九在当前轮输入/结果中显示后端持久化的 `gap_search_strategy_label` 和 description，并在操作
  区固定展示五轮递进：相邻产品+直接结构、上位设备+结构族、同功能产品+动作/角色、子系统部件
  +关系/路径、类比领域+原理/效果。页面明确说明这不是同一批同义词换序。
- 前端不依据轮次自行推导当前策略；真实结果缺少持久化标签时只展示五轮规则说明，不伪装成已由
  后端执行。`pnpm ts-check` 与 `scripts/test-invalidity-portal-contracts.ts` 通过。本次未 build、
  未重启 Portal、未执行 live 案件、未触碰正式 5109。

## 2026-08-15 模块六逐文献与逐特征折叠展示

- `AnalysisResultDetails` 的 I4-S 普通律师视图改为两级 Accordion：第一层按对比文件展开/收起，
  第二层按该文献中的每项权利要求技术特征展开/收起；两层默认均收起，避免数十篇文献和大量
  逐项证据同时占满页面。
- 文献收起摘要保留文献号/标题、评价总数及披露/未披露/待确认/分析失败计数；特征收起摘要保留
  特征原文和判断标签。展开单项后显示原文摘录、原文位置、分析理由和完整结构映射；目标/对比
  文件整体机构摘要另设独立折叠区。后端输出、批次恢复和 I4-S 证据合同未改变。
- `scripts/test-invalidity-portal-contracts.ts` 新增文献级、特征级折叠标识及收起摘要断言。验证为
  `pnpm ts-check`、Portal 无效契约脚本和两份改动文件定向 ESLint 全部通过；未 build、未重启。

## 2026-08-15 模块实验室全阶段紧凑折叠

- `/test/module-lab` 的十一张业务模块卡统一为默认收起；卡片标题行始终显示模块名称、状态、业务
  问题和已完成运行的一行结论。展开模块后，实际输入与完整业务输出仍是两个互不影响、默认收起的
  二级 Accordion；技术运行记录继续默认收起。
- 五案内置盲测的输入与输出同步改为默认收起；当前案件报告摘要保留三项总数，逐项权利要求状态
  默认收起。模块六原有逐文献/逐特征两级折叠保持不变，只有在继续展开业务输出后才按需展示。
- 新增 `data-module-lab-module-disclosure`、`data-module-lab-result-disclosures`、benchmark/report
  折叠合同断言，防止后续改版重新默认铺开长内容。`pnpm ts-check`、Portal 无效契约脚本和定向
  ESLint 全部通过；未执行 production build 或服务重启。

## 2026-08-15 模块九五轮矩阵与精确复用展示

- `/api/test/invalidity/module9-batches` 的 JSON state 新增五轮矩阵 ID/版本/绑定摘要、每轮计划和
  resolved feature 游标。新批次先生成五轮，后续操作只执行冻结轮；已解决 feature 的未来查询
  明示 `skipped_feature_resolved`。
- 同一文献跨轮命中时，只有不可变内容、日期资格、矩阵绑定和 I4-S prompt/rule 全部一致才复用
  旧 FETCH/QUALIFY/I4-S，并在子结果显示 `reusedFromI4sRunId`；版本不齐不复用。
- 模块九普通视图先显示默认折叠的五轮矩阵，再显示默认折叠的已执行轮次；全部十一模块的外层
  默认折叠保持不变。`pnpm ts-check`、Portal 契约、定向 ESLint 和 production build 通过；只重启
  `patent-web`，`/login`=200、匿名模块实验室=307。

## 2026-08-15 模块十三步法折叠展示

- 模块十不再只显示“组合覆盖/日期/缺口”四个总数。普通律师视图按创造性三步法分为四个默认
  收起区块：第一步 D1 与全部已审阅/实际组合文献，第二步逐项区别特征和实际技术问题，第三步
  逐项补充公开、相同作用、技术启示、修改动机与路径、反向教导和效果，第四块列未解决 gap。
- 每项区别特征在第三步中继续使用默认收起 Accordion；收起状态保留“证据链闭合/仍有缺口”，
  展开后显示原文、位置、理由和未闭合原因。页面明确区分“形成缺乏创造性证据链（供律师复核）”
  与“尚不足以证明不具备创造性”，后者不得显示成专利已被证明具备创造性或有效。
- `pnpm ts-check`、模块实验室/契约脚本定向 ESLint、Portal 无效契约测试和 production build
  通过；最终构建已只重启 `patent-web`，登录页 200、匿名模块实验室按设计 307 跳登录。

## 2026-08-16 模块八至十当前批次精确绑定

- `/test/module-lab` 运行模块八时只接受当前完成的模块六批次及该批次的模块七 run；创建模块九
  fresh 批次时同时冻结 `source_module6_batch_id`、`source_closest_prior_art_run_id`、
  `source_obviousness_precheck_run_id`。批次 API 会独立回查三者的用户、案件、独权、状态及输出
  绑定，旧批次缺少任一来源键时要求重新生成。
- 模块九 `queries=[]` 不再仅凭后端 `covered_feature_ids` 显示完成。除模块八已经形成完整证据链
  的特征外，每个被关闭的区别特征必须在当前模块六批次中存在可引用引文和完整版本化 I4-S
  复用键；字符串 `None/null/undefined` 不算版本。普通视图新增默认收起的“已复用覆盖文献与
  引文”，展示公开号/标题、特征、引文、位置、理由、模块六批次和 I4-S run。
- 模块十仅在当前模块九批次已完成/耗尽且其三项来源与页面当前模块六/七/八完全一致时运行，并
  把这些精确 ID 传给后端；历史工作流或其他批次不能继续沿用。
- 验证：`pnpm ts-check`、无效 Portal 契约、三份改动文件定向 ESLint、production build 全部
  通过；3001 `patent-web` 已单独重载并返回 `/login` 200、匿名实验室 307。3002 测试副本、
  正式 5109 均未触碰。

## 2026-08-16 模块11三部分折叠报告

- `/test/module-lab` 第11阶段改名为“三步法文字分析、Top 10 总表与全部比对”，业务结果固定
  分成三个默认收起的 Accordion：模块10结论的人类可读文字版；以独立权利要求特征为纵轴、
  相似度最高最多10篇文献为横轴的紧凑矩阵；全部对比文件的逐篇完整 Claim Chart。
- Top 10 单元格只显示“有（明确）/有（必然隐含）/未披露/待确认/分析失败或未完成”，便于快速
  横向查看；展开第三部分后继续显示原文、位置、结构对应和分析理由。排序优先使用后端
  `similarity_claim_charts`，旧持久快照或模块实验附录缺少新字段时按同一确定性规则现场只读
  构造，不调用模型、不改写后端事实。
- Excel 导出同步增加“创造性文字分析”工作表，并为每项独立权利要求生成一张横向 Top10 表；
  原有新颖性 CC、创造性组合和全部审计工作表继续保留。
- Portal 契约新增三部分标题、默认折叠容器、Top10矩阵和全部文献折叠表断言；后续 production
  build、重启和页面健康验收结果记入本节续项。

### 2026-08-16 模块11发布验证

- `pnpm ts-check`、无效 Portal 契约脚本、模块实验室/导出文件定向 ESLint 和 production build
  全部通过；只重启 3001 的 `patent-web`，登录页返回 200，匿名模块实验室按设计 307 跳登录。
- 浏览器技能在本轮最终视觉验收时没有可连接的 Chrome/内置浏览器实例，因此未完成登录态点击
  截图；页面合同已经断言三部分默认收起、Top10 横向矩阵及全部文献折叠表，未把不可执行的视觉
  验收写成通过。正式 5109 和 3002 测试副本均未触碰。

## 2026-08-16 正式首页专利 Agent

- `src/app/page.tsx` 现为单一会话流、单一 `Textarea` 和附件入口，并在附件旁提供“专利无效”与
  “专利侵权分析”两个可独立选择的显式开关；左侧仅保留新对话、owner-scoped 历史对话及既有
  测试/历史/管理入口。`/test/**` 页面没有改动。
- `src/lib/patent-agent.ts` 只按两个显式开关确定后台路由：未选为普通问答，单选启动对应流程，
  双选分别启动两条流程；缺专利来源时不建案并要求补充。文字、附件和模型不得追加未选择的工具。
- `src/app/api/agent/messages` 接收消息和可选 PDF/DOC/DOCX/TXT。附件仅在确定工具后写入对应正式
  根：侵权使用 Portal uploads，无效只用 prod invalidity upload root；随后直接复用既有正式 Route
  Handler，不做 localhost 自请求，也不访问 `/api/test/**`。同一消息的双工具并行启动并独立收口。
- `patent-agent-store.ts` 与 `db-init.ts` 新增 conversation/message/tool-run 持久化。所有查询按
  `user_id` 过滤；对话 API 不返回工具私有路径或模型凭据。前端对 pending 工具轮询既有 session API，
  并在对话内显示状态、失败原因及对应结果页入口。
- 新增 `scripts/test-patent-agent-contracts.ts`，覆盖普通问答、缺材料、单工具、双工具、单输入框、
  prod-only 无效链路和 owner 查询守门。它与既有无效 Portal 合同、类型检查、定向 ESLint 和完整
  build 均通过；专用 PM2_HOME 只重启 `patent-web`，运行时登录/鉴权/测试页跳转正常。

## 2026-08-16 Agent 侧边栏测试入口收敛

- 普通用户可见的测试产品固定为两个入口，均位于 Agent 侧边栏原“无效检索测试版”区域：
  `/test/module-lab` 显示“无效检测测试版”，直接进入现有 11 模块律师实验室；
  `/test/product-pipeline` 显示“专利分析模块测试版”，复用现有模块 2、3、4 分步和全链测试。
- 专利分析统一测试页移除了“模块1测试、旧模块3测试、安盾模块3测试”等顶部零散导航；无效
  实验室改为直接返回 Agent。旧 `/test/**` 路由继续保留供兼容和内部排错，不再作为用户侧第三个
  测试产品入口；正式无效页中的测试按钮也统一直达 11 模块实验室。
- `scripts/test-patent-agent-contracts.ts` 和无效 Portal 合同现在共同约束 Agent 首页恰好只有两个
  `/test/` 链接，并禁止恢复旧 `/test/invalidity` 导航。`pnpm ts-check`、两套合同、定向 ESLint
  和 `bash ./scripts/build.sh` 均通过；专用 PM2_HOME 只重启 `patent-web`，`/login` 返回 200，
  匿名 Agent 和两个测试页均按设计 307 跳登录，5109/5209 未触碰。

## 2026-08-16 专利分析四模块实验室

- `/test/product-pipeline` 已从旧的模块2—4参数面板改为与无效实验室一致的模块化测试页：顶部只有
  一个专利输入区，下面固定为“专利解析、关键词生成、商品检索、权利要求与商品比对”四张模块卡。
- 四张模块卡及其“本模块输入/本模块输出”、逐商品和逐特征长内容均默认收起；展开后显示当前
  实际参数、运行状态、失败原因、可读摘要和完整原始数据，商品检索高级参数另行折叠。
- 统一测试 API 新增 `patentParse` 动作，经隔离测试模块1执行完整解析并取得本地
  `patent_record_id`。后续模块只使用这次页面生成的 `analysis_session_id` 和当前上游结果；重新
  输入专利或重跑上游会清空全部下游状态，不自动读取其他测试会话的关键词、商品或比对结果。
- 已解析示例仍可从顶部折叠区显式选择；这是可见的人工输入方式，不作为静默历史回退。Agent
  侧边栏仍只有无效检测和专利分析两个大型测试入口，没有新增第三个测试页面。
- 验证：`pnpm ts-check`、Agent/无效两套 Portal 契约、三份改动文件定向 ESLint、`git diff
  --check` 和 production build 全部通过；只重启专用 PM2_HOME 中的 `patent-web`。运行时
  `/login`=200，匿名 `/test/product-pipeline` 与 `/test/module-lab` 均按设计 307 跳登录；5109、
  5209 未触碰。

## 2026-08-16 Agent 显式分析开关

- 正式首页输入框下方、上传附件按钮旁新增“专利无效”和“专利侵权分析”两个独立 toggle，均以
  `aria-pressed` 暴露状态。每条消息发送后清空选择，防止下一条普通问题误用上次分析模式。
- 消息接口只接受重复 `analysisKind` 字段中的 `invalidity` / `infringement` 白名单值；服务端以该
  列表为唯一工作流路由依据。两项都不选时即使文字要求分析或带有附件也只调用问答模型；选择分析
  但缺少可用专利来源时只询问补充材料，不创建空任务。
- 首页引导已改为解释“只有选中才启动后台分析”，示例问题改为“什么是专利的公开日？”等普通
  专利问题，不再使用“对附件同时做侵权和无效分析”作为引导。
- 验证：Agent 契约、无效 Portal 契约、`pnpm ts-check`、四份改动代码定向 ESLint 和 production
  build 全部通过；仅重启专用 PM2_HOME 中的 `patent-web`。运行时 `/login`=200、匿名 `/`=307、
  未登录 Agent API=401；5109/5209 未重启。

## 2026-08-16 Agent 正式无效配置缺失诊断

- `prepareInvalidityUploadRoot('prod')` 的 fail-closed 报错准确：运行中的 Portal 仅加载了测试上传根，
  本机此前没有正式 `.env.prod.local`、正式上传目录、正式 release 或 5109 服务，不是 Agent 错把
  测试目录判成正式目录。
- 已用 `ops/pm2/bootstrap-local-isolated-env.mjs prod` 生成独立正式 API token、Portal 正式 URL/token/
  upload root 和权限受限的正式上传目录；没有复制或回落测试 token/provider 凭据。
- 由于正式智慧芽/EPO OPS 凭据和 `CURRENT_RELEASE` 仍缺失，5109 按隔离合同继续 fail closed，Portal
  暂不重启以免把当前明确的配置错误替换成“正式服务不可达”。正式凭据和 release 就绪后再启动
  5109，并以 `patent-web --update-env` 重载 Portal。

## 2026-08-16 Agent 正式无效链路已就绪

- 用户授权将现有智慧芽/EPO provider 值显式写入正式配置，但 Portal 和 5109 仍只读
  `INVALIDITY_PROD_*`；正式 API token、上传根、schema、工件目录和 PM2 状态均与测试隔离。
- 正式 release `20260816T153757Z-3fbc5ba58c5d` 已在 5109 启动并通过 `environment=prod`
  健康检查；Portal production build 成功并只重载 `patent-web`。
- Agent 保持“显式按钮是唯一工作流开关”：后续用户给出文件/需求且选中“专利无效”时，
  启动完整 I1—I5/最多 5 个 gap 轮；未选中时只调用普通问答模型。部署本身未新建案件。
- 验收：Agent 和无效 Portal 契约、`pnpm ts-check`、production build 通过；运行态
  `/login=200`、匿名 `/=307`、未登录 Agent API `=401`。

## 2026-08-16 Agent 与模块实验室统一业务后端

- 无效 Agent、`/invalidity` 和 `/test/module-lab` 统一调用 5209；Portal 使用
  `CANONICAL_INVALIDITY_ENVIRONMENT` 和中性 `INVALIDITY_API_*` 配置，兼容旧 test 变量，
  禁止新请求进入 5109。
- 专利分析 Agent 的 `/api/analyze` 改为自动调用 `/api/test/product-pipeline` 相同执行器：模块1、
  行业对应模块2、5107 商品详情检索、模块4和结果汇总。模块实验室仍可逐步运行同一执行器。
- 专利分析配置使用 `PATENT_ANALYSIS_*` 中性变量并兼容当前模块变量；商品结果同时从
  `product_detail_search_products` 回填，Agent 不再运行旧 5105 商品检索链。
- Portal production build、TypeScript、两套业务契约和定向 ESLint 均通过；只重载
  `patent-web`。运行时 `/login`=200，匿名 Agent 与两个实验室入口按设计 307 跳登录；唯一专利分析
  链 5101/5102/5107/5106 和唯一无效链 5209 的健康检查均返回 200。

## 2026-08-16 Agent 无效附件键修复

- `src/app/api/agent/messages/route.ts` 的无效附件和
  `src/app/api/invalidity/investigations/route.ts` 的纯文本落盘键改为
  `invalidity-${CANONICAL_INVALIDITY_ENVIRONMENT}-...`，与 `/api/invalidity/uploads` 及
  `invalidityUploadFileKey` 的归属合同一致。
- 保留环境前缀严格校验，旧无前缀键不会被兼容放行；合同测试新增两个生产端的正确/错误模板断言。
  TypeScript、两套业务契约、定向 ESLint 和 production build 通过；仅重载 `patent-web`，5209
  健康检查为 200。

## 2026-08-16 Agent 当前事项进度卡

- `src/app/page.tsx` 会在每个后台工具卡中持续显示当前事项、真实完成数和分段进度。侵权分析按
  四个业务模块展示；无效分析按十一模块展示。两条任务并行时各自轮询和渲染，不共享进度状态。
- 进度来自 `/api/analysis/[id]` 的 `analysis_steps` 以及 `/api/invalidity/session/[id]` 返回的调查/
  独立权利要求状态，不使用计时器伪造百分比。轮询周期为 3 秒；终态自动停止。
- 全部事项列表放在默认关闭的“查看全部事项”折叠区；等待人工、部分完成、失败和取消使用不同
  状态。`agent-task-progress.ts` 负责纯确定性投影，Agent 合同覆盖四项/十一项及 gap 轮次显示。
- `pnpm ts-check`、Agent/无效 Portal 合同、定向 ESLint 和 production build 通过，只重载
  `patent-web`；登录页为 200。因本机浏览器会话不可连接，未完成登录态视觉截图验收。

## 2026-08-16 退役独立 `/invalidity` 入口

- `/invalidity` 原是 Agent 上线前的独立无效调查上传页，现与 Agent 的“专利无效”工具重复；该路由
  改为服务端返回 `/`，不再保留第三套用户输入页面。历史书签不会落入 404。
- `/invalidity/results`、导出、人工复核和 `/api/invalidity/**` 后端能力继续保留；Agent 工具卡和历史
  记录仍可进入具体结果。结果页“新建调查”改为“返回 Agent”。
- 内部 P002/高级技术页中原“正式版”链接同步改为“返回 Agent”。普通用户产品表面仍只有 Agent、
  十一模块无效检测测试版和四模块专利分析测试版。

## 2026-08-16 Agent 30 秒 gap 续跑确认

- `patent-agent-tools-v2` 在 owner-scoped 无效 session 返回 `partial` 且独立权利要求尚未完成第 5 个
  gap 轮时，消息工具卡显示确认区并自动打开 Dialog；用户可立即开始下一轮，30 秒倒计时从调查/
  claim 持久化 `updated_at` 计算，刷新不会重新获得 30 秒。
- 新接口 `POST /api/agent/tool-runs/[id]/invalidity-continuation` 不接受浏览器指定 claim 或轮数；服务端
  按 tool owner 重新读取 session/investigation/claims，校验 `state_version`、`partial`、in-scope 和
  五轮上限，每次仅建一轮，以 tool run + state version 幂等。状态已前进时安全返回而不重复建轮。
- `needs_human_review` 在 Agent 内弹出人工确认提示和表单入口，但关键日、证据资格、D1 等事实动作
  不执行倒计时自动确认。Agent/无效合同、`pnpm ts-check`、改动文件 ESLint、纯函数状态投影断言和
  production build 通过；只重载实际 `/Users/xiexiaoxiong/.pm2-patent-prod` 中的 `patent-web`。运行时
  `/login`=200、匿名 `/`=307、新续检接口未登录=401。
- 浏览器实测登录页标题、邮箱/密码输入和禁用登录按钮均正常；当前浏览器没有登录会话，本轮未传输
  登录凭据，因此登录态确认 Dialog 的真实截图仍待有现成登录会话时补验。

## 2026-08-16 Agent 内无效结果与管理员同 session 诊断

- `src/app/page.tsx` 不再为无效工具生成 `/invalidity/results?session=...` 链接；工具卡直接渲染
  `agent-invalidity-summary.ts` 的逐独立权利要求状态、通俗原因、资料/披露/gap 统计和证据边界。
  `needs_human_review` 由 `agent-invalidity-review.tsx` 在 Agent Dialog 内调用 session-scoped 关键日及
  证据复核接口；侵权任务的原结果页链接保持不变。
- approved admin 的工具卡新增 `/test/module-lab?session=<analysis_session_id>` 入口；普通用户不显示
  无效测试导航或诊断入口。`src/app/test/module-lab/layout.tsx` 在服务端执行 admin 守门，新的
  `/api/admin/invalidity/session/[id]` 只读接口由 session 解析唯一 investigation，读取 5209 的
  investigation/claims/report preview，不调用 Portal 状态更新器。
- 模块实验室的同 session 模式显示 Agent 同一任务的 claim 进度、十一阶段持久化 module run/error
  映射和报告摘要；上传、当前案件运行、fixture 运行、模块六/九批次恢复和模块九继续按钮均禁用。
  管理员须退出只读模式后才能显式创建实验运行，避免“查看”本身复制案件或改变进度。
- 历史 `/invalidity/results` 保留兼容，但 session-scoped `InvalidityReviewPanel` 改走 Portal 正式路由。
  验证：`pnpm ts-check`、Agent/无效 Portal 合同、定向 ESLint、`pnpm next build --webpack` 和 tsup
  全部通过；只重启专用 PM2_HOME 的 `patent-web`。管理员浏览器实测指定 session 的 Agent 卡与
  module-lab 同时显示 CN222140203U、36 份资料、216 条披露、58 个缺口；模块1—3从 Agent 同一进度
  投影为已完成，模块6/9显示真实 partial/error，诊断运行按钮禁用且控制台无错误。匿名诊断 API=401、
  模块实验室=307，5209 健康=200且未重启。

## 2026-08-17 Agent 正式任务续跑与管理员同任务终态核验

- 清理仅取消历史实验室批次的陈旧可继续标记，未删除任何审计，也未改写当前 Agent session 或创建
  第二份调查。当前 session `analysis_1786899314010_6vx5sj` 始终绑定唯一 investigation
  `be38f278-634f-5bfa-9c99-c267b65d95bd`。
- continuation 启动后，Portal session 临时同步为 `running/queued`；后端最终 job、I4-I 和 I5 均
  succeeded 后同步为 `completed/partial`，对应历史 v1 Agent tool run 也由遗留 `queued` 同步为
  `completed`。`partial` 保留外部详情页未取得真实全文和未覆盖区别特征的证据边界，不显示成检索
  穷尽或专利稳定。
- 使用既有管理员登录态实测
  `/test/module-lab?session=analysis_1786899314010_6vx5sj`：页面显示“正在查看 Agent 中的同一个任务”，
  CN222140203U、43 份资料、258 条逐项披露、88 个缺口；模块10、11已完成，模块5/6/9按历史和本轮
  partial/error 映射为需处理。刷新和读取均未创建新调查或 module run；带 session 的页面已保留给用户。
- `invalidityTaskProgress` 不再仅用 claim 的 `partial/current_iteration_no` 把所有后续阶段强制标为 pending；
  当同一报告已持久化 succeeded `I4_I_INVENTIVE_STEP` / `I5_REPORT` 时，模块10/11独立显示 completed，
  gap 模块仍保留 partial。终态文案不再误写“正在推进”，而是说明检索轮已收口、下游已按当前证据完成。
- 新增 partial + final report 合同断言；Agent 合同、`pnpm ts-check`、定向 ESLint、Webpack build 与 tsup
  通过。只重启专用 PM2_HOME 的 `patent-web`；`/login` 与 5209 `/health` 均 200。登录态浏览器复核
  Agent 显示 10/11、模块9部分完成、模块10/11已完成，管理员诊断页仍显示同一任务且未产生新运行。
- `buildAgentInvaliditySummary` 对 investigation 已进入 `partial/failed/cancelled/exhausted` 等自动终态且
  claim 仍需关注的情形，改为“仍有证据缺口；本次自动任务已结束”，不再用“需要继续处理”暗示 worker
  仍在运行；同时明确已有证据/报告已保存，补强须由律师决定。对应合同已增加 headline/detail 断言，
  第二次构建与仅重启 `patent-web` 后，浏览器实测该案新文案和同 session 管理员页均正常。

## 2026-08-17 Agent 首轮失败与错误进度投影诊断

- session `analysis_1786955838458_jq2529` 实际在唯一 5209 investigation
  `36467f7a-0be0-528a-9ea6-1f6bfb0c03f9` 中运行；快照/关键日/I2 已完成，I2 真实调用模型生成 5 条
  查询，但 5 次 Patsnap 首轮请求均返回余额不足 `67200005`，因此没有任何文献或 I4 分析输入。
- `deriveAgentInvalidityAction` 当前仅按 investigation/claim 为 `partial` 和轮次 1—5 推导 gap 续跑，
  没有核对 D1、区别特征或 open gap。本案 `open_gap=0`，倒计时仍触发三次 continuation POST，三次
  均被 5209 正确以 409 拒绝且未建批次；这就是界面后续“系统错误”，不是 PM2 或 worker 故障。
- `invalidityTaskProgress` 对 `partial/current_iteration_no=1` 推导 active stage 6，再将所有更早阶段
  直接标 completed；同时空证据 I5 快照的 succeeded run 会把阶段11标 completed。实际报告文献、披露、
  D1、组合、gap 和 Claim Chart 均为 0，I4 run 数也是 0，因此该进度不能作为模块真实执行证据。
- Agent tool row 物理状态仍为 `queued`，读取时通过关联 `analysis_sessions.status=completed` 投影终态；
  本轮只做只读诊断，未修改 Portal、数据库、服务或任务。

## 2026-08-17 旧工具状态收口与新对话交接

- 按用户“更新 Agent 状态并重新开始新任务”的明确要求，将旧 session
  `analysis_1786955838458_jq2529` 对应的唯一 tool run 从遗留 `queued` 更新为 `partial`；session
  继续保持 `completed/partial`，旧 investigation 和全部审计记录不变。更新后确认 5209
  `queued/leased/running` 作业数为 0。
- tool run 的说明明确记录“旧任务因智慧芽余额不足未完成，现已结束；新 Key 已验证，请创建新任务
  重新分析”，防止物理行继续冒充在途。没有预建 conversation、analysis session 或 investigation，
  因为新的分析类型与专利材料仍须由用户在 Agent 显式提交。
- 登录态浏览器关闭旧任务续检 Dialog 并点击“新对话”，最终可见“新的专利对话”、空输入框、未选择
  的“专利无效/专利侵权分析”按钮和 disabled 发送按钮；页面已保留给用户直接开始下一项任务。

## 2026-08-19 模块九自动容错与同 session 诊断归属

- `analysis_1787119382809_qgwj93` 的模块五告警是归属错误：Agent gap 轮复用了
  `I3_PATENT_SEARCH/I3_CANDIDATE_FILTER/I3_FETCH/I3_QUALIFY`，旧只读页只按 module code 匹配，
  因而把 iteration 2 的两个 partial 行归到模块五。`attachedRunBelongsToBusinessModule` 现在按
  `iteration_no` 分流：首轮/旧无迭代 I3 留在模块五/六，iteration > 1 的复用 I3/I4-S 归模块九。
- 模块九 durable controller 的五轮矩阵允许单行在有界尝试后冻结 failed；执行轮的 provider、候选
  清理、取文、日期或 I4-S 失败进入 `failure_reasons/failure_ledger`，成功子结果保持，批次自动排入
  下一冻结轮直至第 5 轮或全部特征关闭。旧 `round_partial/retry_failed/continue_next_round` 仅保留
  历史批次兼容。
- 模块十允许消费同一谱系下已收口的 completed/exhausted/partial/failed 模块九批次，并把
  `module9_failure_ledger` 传给 5209；无新增文献时仍基于模块六既有 D1/I4-S 输出证据不足。
- 第 2 轮页面说明改为“优先上位、必要时选择合适下位产品类别 + 结构族同义词”。Portal
  `pnpm ts-check`、`node --import tsx scripts/test-invalidity-portal-contracts.ts` 和
  `bash scripts/build.sh` production build 已通过；专用 PM2_HOME 中只重启 `patent-web`，`/login`
  返回 200。5101—5108 保持 online 且健康检查均为 200。

## 2026-08-20 Agent 候选日期待办聚焦与事实回填

- Agent 的 `needs_human_review` Dialog 不再直接展示完整通用
  `InvalidityReviewPanel`；现以 `focus="pending_date_review"` 只保留候选日期确认区，隐藏与当前
  停点无关的证据导入、D1、gap 和 action 日志。无目标关键日待办时不再显示“下方继续显示
  其他入口”的误导文案。
- 文献列表只消费当前 document version 下 document-level
  `verification_status=needs_human_review` 的 qualification，并用当前 `date_gap` 的
  canonical key 作防御性回退；已 verified 文献不再让用户重复确认。
- 选中文献后从 `review-context` 自动回填公众可得日、公开日、申请日、优先权日、公开号、
  国家/机构、source type、当前 PDF 版本和 SHA-256 定位，并自动勾选真正待复核的独立
  权利要求。回填值仍须由用户显式点击提交；页面打开/选择不改数据。
- 实际登录态验收 session `analysis_1787217204568_yvb78w`：16 份文献中弹框只显示 2 份
  US 待复核文献，下拉项为 `US20080245714A1 / 2008-10-09` 和
  `US20090145830A1 / 2009-06-11`；首页已回填字段和冻结定位，未点击提交，旧审计不变。
  `pnpm ts-check`、定向 ESLint、无效/Agent 两套契约和 production build 通过；已重启
  `patent-web`，`/login` 与 5101—5108 健康检查均为 200。

## 2026-08-20 Agent 模块11结果直显与逐篇工作簿

- `src/lib/agent-invalidity-report.ts` 从 session API 已返回的 v1 不可变报告构建 Agent 专用视图；
  `src/components/agent-invalidity-report.tsx` 默认展开模块11第一部分律师文字和第二部分 Top 10 横向
  矩阵，并在第三部分提供同 session 导出入口。该正文最初嵌在终态工具卡内；同日用户追加的消息
  层级要求已由下方“独立结果回复”决策取代。未知合同版本和空报告不得拼装假结果。
- 终态 tool run 在报告快照尚未可读时继续轮询，短暂读取错误保留已取得的报告视图，避免用户刷新后
  丢失模块11正文；报告在过程卡还是独立消息中展示不改变该恢复合同。
- `src/lib/invalidity-report-export.ts` 保留原有工作簿，并新增 `逐篇CC索引` 以及每篇已进入逐篇分析
  文献一个独立 Claim Chart Sheet。Sheet 名经过 Excel 长度/非法字符/重名守门，披露行按当前文献
  版本和最新逐项记录去重。
- 指定 session `analysis_1787242957124_padlos` 的真实快照验证为 1 份 5 段律师文字、5×10 横向表、
  21 篇逐篇文献/105 条披露；工作簿生成 21 个逐篇 Sheet。`pnpm ts-check`、Agent 与无效报告契约、
  定向 ESLint、production build 通过；重启 `patent-web` 后登录态页面确认两部分默认展开、导出下载
  成功触发，Portal 3001 和 5209 健康检查均为 200。

## 2026-08-20 Agent 工作动态与模块11独立结果回复

- 用户明确否定把模块11正文放进过程卡。首页消息层级固定为“用户请求 -> Agent 启动回复 -> 过程卡
  -> 独立 Agent 结论回复”；`src/lib/agent-conversation-layout.ts` 把工具运行锚定到同轮最后一条
  assistant 启动回复，旧 session 无须改写消息或审计也能按新层级稳定投影。
- 过程卡新增 `data-agent-work-trace="persisted-stage-status"` 工作动态，只展示最近三项真实持久化阶段
  状态及原有十一模块进度、错误和人工动作；不得把隐藏思维链当作输出或编造后台未持久化的推理。
  报告到达后过程卡仅提示完整结论已在下方另行输出。
- `src/components/agent-invalidity-result-message.tsx` 以新的 assistant 头像和独立气泡承载收口摘要、
  证据边界及 `AgentInvalidityReport`。律师文字、Top 10 表和 XLSX 导出均不再是
  `data-agent-task-progress` 的后代节点。
- `WORKFLOW_SPEC.md` 升至 3.65；宪章仍为 2.40。`pnpm ts-check`、Agent/无效两组合同、改动文件
  定向 ESLint 和 production build 通过；全仓 lint 仍仅被既有 `src/app/module1/page.tsx` 条件调用
  Hook 错误拦截。只重启 `patent-web` 后 Portal 3001 正常响应。登录态实测 session
  `analysis_1787242957124_padlos`：DOM 顺序为 process -> final，过程卡内报告 0、独立结果消息内报告
  1；工作动态可见，律师文字/Top 10 默认展开，矩阵仍为 10 文献列 × 5 特征行，XLSX 链接仍绑定
  同一 session。

## 2026-08-23 侵权 Agent 独立结果消息与唯一专利分析实验室

- 侵权 Agent 复用无效 Agent 的消息层级：启动回复后显示只含真实持久化事项、进度、错误和工作动态
  的过程卡；session 收口后另起 assistant 消息显示结论、风险统计和商品技术特征比对表，并提供完整
  结果页与同 session XLSX 导出。过程卡不再混入结果页入口，新任务启动文案也明确结果将另起回复；
  旧 conversation 消息和审计记录不改写。
- 新增 `agent-infringement-result.ts` 作为 owner-scoped `/api/analysis/{id}` 的只读投影，只消费已持久化
  商品、比对和分数，不重算法律结论；失败且无任何可用结果时不拼装空结论，有部分真实结果时允许以
  partial 边界收口。真实完成 session `analysis_1784098219047_zm2wi5` 已核对为 15 件商品、15 份比对、
  `resultsCompleteness=final`，符合新 DTO。
- `/test/product-pipeline` 收敛为唯一“专利分析实验室”，公开展示“专利解析、关键词生成、商品检索、
  权利要求与商品比对”四个业务阶段，不显示技术模块编号。每个阶段分别提供默认收起的“本次输入、
  本次输出、结果”；输出保留运行标识/原始返回供排错，结果只显示可读业务数据。旧 `/test` 兼容入口
  自动跳转到该页，侧边栏只显示这一个专利分析测试入口；侵权结果页“模块1结果”改名为“专利原文”。
- 宪章升至 2.41。`pnpm ts-check`、Agent/无效 Portal 两组合同、改动文件定向 ESLint 和 8GB 堆
  production build 通过；只重启 `patent-web`，其余后端未重启。登录态浏览器确认实验室仅有四个
  阶段，展开后输入/输出/结果三块均默认收起且无“模块1结果”；既有失败侵权任务只显示过程卡，
  已完成侵权结果页显示“专利原文”。

## 2026-08-23 侵权 Agent 管理员同任务四阶段诊断

- `AgentToolProgressCard` 对 approved admin 的侵权任务新增“查看本次分析各模块”，链接只携带本次
  `analysis_session_id` 到 `/test/product-pipeline?session=...`；无效任务仍进入十一阶段诊断，普通
  用户仍看不到两类管理员入口。
- 新增 `/api/admin/patent-analysis/session/[id]` GET-only 路由：只接受 approved admin 和
  infringement session；模块1按 session 保存的 `dbRecordId`，模块2—4按保存的 run ID 优先并以
  `analysis_session_id + patent_record_id` 双重限制，未保存 run ID 时也只允许在同一 session 内取
  最近 run。接口不调用 `/api/test/product-pipeline` POST，不更新 session/step/audit。
- `product-pipeline` 的 session 模式显示只读说明和 session 总错误，隐藏 UploadForm、示例选择、
  行业/检索参数和运行按钮；四个阶段继续使用原输入/输出/结果折叠结构。失败阶段保留已持久化部分
  结果，商品检索额外显示 candidate summary 和最多 200 条逐候选排除详情；运行中的任务只 GET 轮询。
- 指定失败 session `analysis_1787483255507_irdtd9` 实测恢复 record 113、keyword run 144、product
  run 353：前两阶段完成，商品检索 24 候选全 rejected / 0 accepted，权利要求比对未执行；读取前后
  session `status/results/updated_at` 一致。管理员浏览器实测 Agent 按钮、跳转 URL、四阶段状态、候选
  原因和 0 个运行按钮。
- 宪章升至 2.42。`pnpm ts-check`、Agent 合同、改动文件 ESLint、production build 均通过；全仓
  lint 仍因既有 `src/app/module1/page.tsx` 条件 Hook 错误失败。只重启 `patent-web`，其余八个后端
  进程未重启。
