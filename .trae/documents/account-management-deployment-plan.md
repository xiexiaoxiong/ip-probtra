# 账户管理与本地持久化上线方案

## Summary

- 目标：为 `IP-protral` 增加一套可上线的账户管理系统，支持“注册 -> 管理员审批 -> 登录 -> 受保护访问 -> 查看自己的分析历史”，同时管理员可查看全站分析记录。
- 已确认的产品决策：
  - 登录方式：邮箱 + 密码
  - 注册审核：管理员人工审批
  - 管理能力：需要基础后台
  - 数据可见范围：普通用户仅可见自己的分析记录，管理员可见全部
  - 注册字段：姓名 + 邮箱 + 密码
  - 初始管理员：通过环境变量预置，首次启动时写入本地数据库
- 技术方向：基于现有 `Next.js 16 + TypeScript + pg + PostgreSQL`，采用服务端 Cookie Session；不引入第三方认证 SaaS；分析历史统一落本地 Postgres，不再依赖 `.data/analysis-sessions/*.json` 作为主存储。

## Current State Analysis

- 当前应用入口为 `src/app/page.tsx`，未接入任何认证、路由保护或用户上下文。
- 当前分析流程由 `src/app/api/analyze/route.ts` 编排，前端通过 `src/hooks/use-analysis.ts` 轮询 `src/app/api/analysis/[id]/route.ts` 获取会话。
- 当前“历史不丢失”主要依赖 `src/lib/analysis-store.ts`：
  - 内存 `Map`
  - `.data/analysis-sessions/*.json` 文件持久化
  - 没有用户归属，也没有列表查询能力
- 当前已有本地 Postgres 访问层：
  - `src/lib/postgres.ts`
  - `src/app/api/database/[id]/route.ts`
- 当前仓库已有一套未接入主流程的 Drizzle 表定义：
  - `src/storage/database/shared/schema.ts`
  - `src/storage/database/shared/relations.ts`
  - 其中已有 `analysis_sessions`、`analysis_steps`，但没有用户、审批、登录会话等表，也没有用户归属字段。
- 当前无 `middleware`、无 `/login`、`/register`、`/admin` 页面、无鉴权 API。
- 当前结果页 `src/app/results/page.tsx` 与详情页 `src/app/results/[productId]/page.tsx` 直接依赖 URL 参数 `session` 拉取数据，任何知道 sessionId 的访问者理论上都可读取。

## Proposed Changes

### 1. 数据模型与数据库初始化

#### 变更文件

- `src/storage/database/shared/schema.ts`
- `src/storage/database/shared/relations.ts`
- `src/lib/postgres.ts`
- `src/lib/analysis-store.ts`
- `src/lib/types.ts`
- 新增 `src/lib/auth.ts`
- 新增 `src/lib/password.ts`
- 新增 `src/lib/db-init.ts`

#### 具体方案

- 在 `schema.ts` 中新增或重构以下表：
  - `users`
    - `id`
    - `name`
    - `email`（唯一）
    - `password_hash`
    - `role`：`admin | user`
    - `status`：`pending | approved | rejected | disabled`
    - `approved_by`
    - `approved_at`
    - `created_at`
    - `updated_at`
  - `auth_sessions`
    - `id`
    - `user_id`
    - `expires_at`
    - `created_at`
    - `last_seen_at`
  - 扩展 `analysis_sessions`
    - 新增 `user_id`
    - 新增 `patent_title`
    - 新增 `patent_number`
    - 保留 `status / input_type / input_value / file_name / file_url / text_content / results / created_at / updated_at`
  - `analysis_steps` 保留，但以数据库为准，不再只存内存/文件
- 在 `relations.ts` 增加：
  - `users -> analysisSessions`
  - `users -> authSessions`
  - `users(审批人) -> users(被审批人)` 可不强加双向复杂关系，只保留 FK 即可
- 在 `db-init.ts` 中实现：
  - `ensureCoreTables()`：服务启动或 API 首次访问时建表
  - `ensureBootstrapAdmin()`：读取环境变量初始化首个管理员
- 环境变量约定：
  - `AUTH_BOOTSTRAP_ADMIN_EMAIL`
  - `AUTH_BOOTSTRAP_ADMIN_PASSWORD`
  - `AUTH_BOOTSTRAP_ADMIN_NAME`
  - `AUTH_SESSION_TTL_DAYS`，默认 7 天
- `password.ts` 负责密码哈希与校验：
  - 使用 Node `crypto.scrypt` 实现带 salt 的哈希
  - 结果格式统一为单字符串，便于本地数据库保存
- `auth.ts` 负责：
  - 生成随机 session token
  - 解析/写入 HttpOnly Cookie
  - 获取当前登录用户
  - 判定是否管理员

#### 设计原因

- 满足“本地数据库保存”与“上线可用”的基本要求。
- 用服务端 Cookie Session 简化前端复杂度，避免把 JWT 暴露给浏览器业务层。
- 将分析会话直接归属到 `user_id`，天然支持“只看自己的历史”和“管理员看全站”。

### 2. 把分析会话主存储从文件切到数据库

#### 变更文件

- `src/lib/analysis-store.ts`
- `src/lib/types.ts`
- `src/app/api/analyze/route.ts`
- `src/app/api/analysis/[id]/route.ts`
- `src/app/api/database/[id]/route.ts`

#### 具体方案

- 重写 `analysis-store.ts` 的读写路径：
  - `createSession(input, user)`：创建数据库中的 `analysis_sessions` 与默认 `analysis_steps`
  - `updateSessionStatus`
  - `updateStepStatus`
  - `updateResults`
  - `getSessionAsync`
  - 新增 `listSessionsForUser`
- 保留内存缓存作为可选加速层，但数据库是唯一真实来源。
- 逐步降级 `.data/analysis-sessions/*.json`：
  - 本次不再写新文件
  - 可保留只读兼容逻辑：若数据库查不到，且 sessionId 对应旧文件存在，则作为迁移兜底读取；但新创建的记录全部写数据库
- `analysis_sessions.results` 继续保存完整结果 JSON，避免首页历史列表必须实时反查多个工作流表。
- 在 `api/analyze/route.ts` 中：
  - 必须先拿到当前登录用户
  - 未登录直接 401
  - 创建 session 时写入 `user_id`
  - 模块 1 完成后同步回填 `patent_title / patent_number / dbRecordId`
- 在 `api/analysis/[id]/route.ts` 中：
  - 普通用户只能读取自己的会话
  - 管理员可读取全部
- 在 `api/database/[id]/route.ts` 中：
  - 加同样的权限控制

#### 设计原因

- “页面刷新不丢失”应变成数据库级事实，而非文件缓存行为。
- 统一主存储后，后续做历史列表、管理员筛查、审计都更简单。

### 3. 认证 API 与会话管理

#### 变更文件

- 新增 `src/app/api/auth/register/route.ts`
- 新增 `src/app/api/auth/login/route.ts`
- 新增 `src/app/api/auth/logout/route.ts`
- 新增 `src/app/api/auth/me/route.ts`
- 新增 `src/app/api/admin/users/route.ts`
- 新增 `src/app/api/admin/users/[id]/approve/route.ts`
- 新增 `src/app/api/admin/users/[id]/reject/route.ts`

#### 具体方案

- `POST /api/auth/register`
  - 输入：`name`, `email`, `password`
  - 校验：
    - 邮箱格式
    - 密码最小长度（建议 8）
    - email 唯一
  - 行为：
    - 创建 `users` 记录，`status = pending`
    - 默认 `role = user`
  - 输出：提示“已提交，等待管理员审批”
- `POST /api/auth/login`
  - 根据邮箱查用户
  - 校验密码哈希
  - 按状态返回：
    - `pending`：提示待审批
    - `rejected`：提示已拒绝
    - `disabled`：提示已禁用
    - `approved`：创建 `auth_sessions`，写入 HttpOnly Cookie
- `POST /api/auth/logout`
  - 删除当前 `auth_sessions`
  - 清除 Cookie
- `GET /api/auth/me`
  - 返回当前登录用户最小信息：`id/name/email/role/status`
- `GET /api/admin/users`
  - 管理员查询用户列表，支持按状态分组即可，首版不加复杂筛选
- `POST /api/admin/users/[id]/approve`
  - 将 `status` 置为 `approved`
  - 写入 `approved_by / approved_at`
- `POST /api/admin/users/[id]/reject`
  - 将 `status` 置为 `rejected`

#### Cookie 策略

- Cookie 名称：`patent_auth_session`
- `httpOnly: true`
- `sameSite: 'lax'`
- `secure: true`（生产环境）
- `path: '/'`

#### 设计原因

- 满足“注册通过后才能登录”的明确业务要求。
- 首版后台只做审批与列表，不引入密码重置、邮箱通知等额外系统。

### 4. 路由保护与访问控制

#### 变更文件

- 新增 `src/middleware.ts`
- `src/app/page.tsx`
- `src/app/results/page.tsx`
- `src/app/results/[productId]/page.tsx`
- `src/app/database/page.tsx`

#### 具体方案

- 用 `middleware.ts` 做基础路由守卫：
  - 未登录访问 `/`、`/results`、`/database`、`/admin` 时跳转 `/login`
  - 已登录访问 `/login`、`/register` 时跳回 `/`
- 在服务器 API 中仍保留真正的权限校验，不能只依赖中间件。
- 页面侧不再默认认为 URL 中有 `session` 就有权访问：
  - 所有读取都依赖后端鉴权后的数据返回

#### 设计原因

- 阻止匿名访问分析系统。
- 避免 sessionId 被猜到/复制后跨用户读数据。

### 5. 登录、注册、历史列表与管理员后台页面

#### 变更文件

- 新增 `src/app/login/page.tsx`
- 新增 `src/app/register/page.tsx`
- 新增 `src/app/history/page.tsx`
- 新增 `src/app/admin/page.tsx`
- 新增 `src/components/auth/login-form.tsx`
- 新增 `src/components/auth/register-form.tsx`
- 新增 `src/components/history/history-list.tsx`
- 新增 `src/components/admin/pending-users-table.tsx`
- 可选新增 `src/components/app-shell.tsx`
- `src/app/page.tsx`
- `src/app/layout.tsx`

#### 具体方案

- `/login`
  - 邮箱 + 密码
  - 登录成功跳首页
- `/register`
  - 姓名 + 邮箱 + 密码
  - 成功后显示“待管理员审批”
- `/history`
  - 普通用户：只看自己的分析记录
  - 管理员：可看全部记录，并标识创建者
  - 列表字段：
    - 专利标题
    - 专利号
    - 状态
    - 创建时间
    - 最近更新时间
    - 操作：查看结果、查看数据库快照
- `/admin`
  - 基础后台即可：
    - 待审批用户列表
    - 已审批用户列表
    - 审批/拒绝按钮
- `src/app/page.tsx`
  - 顶部导航增加当前用户信息、退出登录、历史记录入口
  - 管理员额外显示“管理后台”入口
- `src/app/layout.tsx`
  - 保持根布局简单；如需要全局用户态，可用服务端读取当前用户后下发到导航组件

#### 设计原因

- 用户的“之前分析结果看得到”在产品上应体现为显式的历史列表页，而不是依赖记住 sessionId。

### 6. 前端分析流程改造

#### 变更文件

- `src/hooks/use-analysis.ts`
- `src/components/upload-form.tsx`
- `src/app/page.tsx`

#### 具体方案

- `use-analysis.ts` 保持当前轮询模式，但增加两点：
  - 当 `/api/analyze` 返回 401 时，跳转登录
  - 分析完成后继续保留当前跳转结果页逻辑
- 首页在登录后显示最近若干条分析记录摘要，避免用户刷新后只剩空白首页。
- `upload-form.tsx` 无需改输入模式，只需适配未登录态不再显示在公开页面。

#### 设计原因

- 尽量复用现有分析编排，不改四个工作流模块。
- 控制首版风险，把改造重点放在账户和会话归属。

### 7. 权限边界与安全规则

#### 变更文件

- `src/lib/auth.ts`
- 各 `api/auth/*`
- 各 `api/admin/*`
- `src/app/api/analyze/route.ts`
- `src/app/api/analysis/[id]/route.ts`
- `src/app/api/database/[id]/route.ts`

#### 具体方案

- 普通用户：
  - 不能访问 `/admin`
  - 不能读取他人的 session、数据库快照和结果详情
- 管理员：
  - 可审批用户
  - 可查看全站分析历史与结果
- 密码安全：
  - 仅存哈希
  - 登录失败统一错误文案，避免泄露账户存在性过多细节
- 审计最小化：
  - 审批动作记录审批人和时间

### 8. 与现有多工作流模块的兼容策略

#### 变更文件

- `src/app/api/analyze/route.ts`
- `src/lib/types.ts`
- `src/lib/analysis-store.ts`

#### 具体方案

- 不修改 `1-patent-analysis`、`2-keyword*`、`3-search`、`4-claim-chat` 的业务协议。
- 用户体系仅落在前端编排层 `IP-protral`。
- 通过现有 `dbRecordId / keywordRunId / searchRunId / claimCompareRunId` 继续关联下游数据。
- 新增 `analysis_sessions.user_id` 只负责“谁发起了这次分析”，不侵入工作流内部数据库。

#### 设计原因

- 这是最小风险上线方案。
- 避免改动 4 个 Python 模块和对应部署流程。

## Assumptions & Decisions

- 使用现有本地 Postgres 作为唯一正式存储；不再依赖文件持久化作为主数据源。
- 首版不做：
  - 忘记密码
  - 邮件通知
  - 邮箱验证
  - 邀请码
  - 多租户组织
  - 细粒度 RBAC
- 管理员拒绝后，用户不能登录；是否允许重新申请，本版默认允许再次注册需使用不同邮箱，首版不做“重新提交审批”。
- 管理员后台首版只做基础审批与查看，不做禁用/重置密码操作。
- `analysis_sessions.results` 继续存汇总 JSON，即使底层已有工作流分表，优先保证结果页加载快、实现简单。
- 为兼容已有数据，可在 `analysis-store.ts` 中保留旧 JSON 文件读取兜底，但新数据不再写文件。

## Verification Steps

### 功能验证

1. 未登录访问 `/`、`/results`、`/database`、`/history`、`/admin`：
   - 应跳转 `/login`
2. 新用户注册：
   - 写入 `users`
   - 状态为 `pending`
   - 登录返回“待审批”
3. 初始管理员自动创建：
   - 启动后数据库存在 admin 用户
4. 管理员登录：
   - 可访问 `/admin`
   - 可查看待审批用户
   - 审批用户成功
5. 被审批用户登录：
   - 登录成功
   - 可访问首页并发起分析
6. 发起分析后：
   - `analysis_sessions.user_id` 正确写入
   - `analysis_steps` 正确创建与更新
   - 结果页刷新后仍能从数据库取回
7. 历史列表：
   - 普通用户只能看到自己的记录
   - 管理员可以看到全部记录
8. 权限校验：
   - 普通用户直接请求他人 `/api/analysis/[id]` 返回 403 或 404 风格拒绝
   - 普通用户访问 `/admin` 被拒绝

### 代码与质量验证

1. `pnpm ts-check`
2. `pnpm lint`
3. 对新建 API 路由进行基础手工接口验证：
   - register
   - login
   - logout
   - me
   - admin approve/reject
4. 走一遍完整链路手工验收：
   - 注册
   - 审批
   - 登录
   - 新建分析
   - 查看历史
   - 刷新结果页
   - 管理员查看全站历史

## 实施顺序

1. 扩展数据库 schema，并补齐初始化与管理员引导
2. 实现密码哈希、Cookie Session 与 `auth.ts`
3. 接入注册/登录/登出/当前用户 API
4. 将 `analysis-store.ts` 主存储切换到 Postgres
5. 改造 `api/analyze`、`api/analysis/[id]`、`api/database/[id]` 的鉴权和用户归属
6. 增加 `middleware.ts` 与受保护路由
7. 落地 `/login`、`/register`、`/history`、`/admin` 页面
8. 改造首页导航和登录后体验
9. 运行 lint / ts-check / 手工验收
