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
│       ├── workflow-client.ts           # 扣子编程项目 /run 端点调用 + 行业路由
│       ├── feishu-client.ts             # 飞书多维表格 API 客户端 (备选数据源)
│       ├── analysis-store.ts            # 分析会话存储 (内存+Supabase双写)
│       └── utils.ts                     # 通用工具函数
├── .env.local                           # 环境变量 (模块 tokens + 飞书凭证)
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
步骤1: 专利文本解析 → https://zzctsm7xqm.coze.site/run 提取权利要求/说明书/附图
       ↓ (feishu_url - 飞书多维表格)
步骤2: 行业识别与路由 → LLM判断专利所属行业(fitness_equipment/home_appliances/general)
       ↓
步骤3: 技术关键词生成 → 根据行业路由:
       通用: https://h8qmyd62sq.coze.site/run
       健身器材: https://5rwr6pmzk3.coze.site/run
       家用电器: https://9bq6x5jqkb.coze.site/run
       ↓ (写入同一飞书表格)
步骤4: 商品信息检索 → https://sk2jw6vshq.coze.site/run 搜索并结构化商品信息
       ↓ (写入同一飞书表格)
步骤5: 技术特征比对 → https://36yvrn7jt4.coze.site/run 逐商品比对，返回 all_comparison_results
       ↓
步骤6: 结果提取 → 优先从模块4响应提取; 备选从飞书表格读取(需飞书API凭证)
       ↓
[结果展示: 商品列表 + Claim Chart 比对表]
```

### 数据提取策略（步骤6）

1. **方案1（优先）**: 从模块4 API 响应的 `all_comparison_results` 字段直接提取比对数据
2. **方案2（备选）**: 如果方案1无数据，通过飞书开放平台 API 读取多维表格（需配置 FEISHU_APP_ID + FEISHU_APP_SECRET）

### 行业路由机制

- **行业类型**: `fitness_equipment`(健身器材) | `home_appliances`(家用电器) | `general`(通用)
- **识别方式**: LLM 分析专利文本/权利要求，输出行业+置信度+理由
- **路由逻辑**: 根据行业选择对应的关键词生成工作流；识别失败自动回退通用
- **扩展**: 新增行业只需在 types.ts 添加类型 + workflow-client.ts 添加模块配置和路由分支

### 技术实现

- **调用方式**: 通过扣子编程项目部署后的自定义域名 `/run` 端点，传入 JSON 参数
- **数据传递**: 4个模块共享同一个飞书多维表格，通过 `feishu_url` 参数传递
- **模块1**: 接收 `patent_file.url` + `patent_file.file_type` + `task_id`，file_type 只接受 `image` 或 `video`
- **模块2(通用)**: 接收 `feishu_url`，从飞书表格读取模块1的输出
- **模块2(健身器材)**: 接收 `feishu_url`，健身器材专用关键词生成
- **模块2(家用电器)**: 接收 `feishu_url`，家用电器专用关键词生成
- **模块3**: 接收 `feishu_url` + `input_keywords`(数组)，从飞书表格读取关键词并搜索商品
- **模块4**: 接收 `feishu_url`，返回 `all_comparison_results` + `result_summary` + `table_urls`

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

> 前提条件：所有扣子编程项目必须已部署并发布 API。模块 URL/Token 已硬编码为 fallback 默认值，部署时无需配置环境变量。步骤6优先从模块4响应提取数据，飞书 API 为备选方案。

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
