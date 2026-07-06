# 项目上下文

### 版本技术栈

- **Framework**: Next.js 16 (App Router)
- **Core**: React 19
- **Language**: TypeScript 5
- **UI 组件**: shadcn/ui (基于 Radix UI)
- **Styling**: Tailwind CSS 4
- **对象存储**: coze-coding-dev-sdk (S3Storage)
- **LLM 引擎**: coze-coding-dev-sdk (LLMClient, doubao-seed-2-0-pro-260215)
- **搜索引擎**: coze-coding-dev-sdk (SearchClient, 网页搜索)
- **URL 抓取**: coze-coding-dev-sdk (FetchClient, 网页内容提取)

## 项目概述

**专利侵权自动识别系统** — 基于专利文本与市场商品信息，进行事实驱动、可回溯、可验证的侵权技术比对，输出 Claim Chart 级别的专业分析结果。

### 核心设计原则

1. **LLM 不直接做法律结论判断** — LLM 仅用于文本读取、结构化、对齐、标注
2. **所有判断结果可回溯** — 每一比对结论可追溯到专利原文、商品原始描述、比对规则
3. **模块单向依赖** — 模块1→2→3→4→结果提取 串行，不允许循环调用或反向依赖
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

### 实验测试链路

- `/test/product-pipeline` 是“关键词 / 新模块三 / 模块四测试”页面，可手动输入关键词，也可先调用模块2生成关键词，再调用平行模块3 `3-product-search` 获取商品详情和图片，最后调用模块4比对。
- `/api/test/product-pipeline` 支持 `action=keywords|productSearch|claimCompare|all`。`productSearch` 默认调用 `PRODUCT_DETAIL_MODULE3_API_URL || PRODUCT_SEARCH_API_URL || http://127.0.0.1:5107/run`，并从 `product_detail_search_products` 回读商品、图片、质量字段。
- 该页面不接入正式 `/api/analyze` 主流程；用途是调试新模块3是否能根据关键词检索到更多商品信息和商品图片，以及验证模块4是否能消费新模块3独立表结果。

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
