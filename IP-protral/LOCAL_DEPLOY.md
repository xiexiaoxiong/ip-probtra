# 本地化部署指南

## 架构说明

本地化部署 = **在你自己的服务器上运行 Next.js 应用**，4 个工作流仍在扣子平台上运行。

```
你的服务器                              扣子平台（远程）
┌──────────────────────┐               ┌─────────────────────┐
│  Next.js 应用        │               │  模块1 coze.site/run │
│  ├─ 前端页面         │──HTTP调用────→│  模块2 coze.site/run │
│  ├─ 后端API          │               │  模块3 coze.site/run │
│  └─ 数据库/存储      │               │  模块4 coze.site/run │
└──────────────────────┘               └─────────────────────┘
```

好处：
- 无平台休眠问题（你的服务器你控制）
- 无代理层超时（直接调用 coze.site，超时自己控制）
- 环境变量完全可控
- 数据存储在本地

## 前置要求

- Node.js 18+ (推荐 20+)
- pnpm 8+
- PostgreSQL 14+ (或使用云数据库)
- 可选：S3 兼容对象存储 (MinIO / 阿里云 OSS / AWS S3)

## 步骤1：获取代码

```bash
# 从当前项目复制代码到你的服务器
git clone <你的仓库地址>
cd patent-analysis
```

## 步骤2：安装依赖

```bash
pnpm install
```

## 步骤3：配置环境变量

创建 `.env.local` 文件：

```bash
# ============ 必填：4个工作流模块配置 ============
# 模块1 - 专利文本解析
MODULE1_API_URL=https://zzctsm7xqm.coze.site/run
MODULE1_API_TOKEN=<你的模块1 Token>

# 模块2 - 通用关键词生成
MODULE2_API_URL=https://h8qmyd62sq.coze.site/run
MODULE2_API_TOKEN=<你的模块2通用Token>

# 模块2 - 健身器材关键词生成
MODULE2_FITNESS_API_URL=https://5rwr6pmzk3.coze.site/run
MODULE2_FITNESS_API_TOKEN=<你的模块2健身器材Token>

# 模块2 - 家用电器关键词生成
MODULE2_HOME_APPLIANCES_API_URL=https://9bq6x5jqkb.coze.site/run
MODULE2_HOME_APPLIANCES_API_TOKEN=<你的模块2家用电器Token>

# 模块3 - 商品信息检索
MODULE3_API_URL=https://sk2jw6vshq.coze.site/run
MODULE3_API_TOKEN=<你的模块3 Token>

# 模块4 - 技术特征比对
MODULE4_API_URL=https://36yvrn7jt4.coze.site/run
MODULE4_API_TOKEN=<你的模块4 Token>

# ============ 必填：S3 对象存储 ============
# 用于文件上传（专利PDF/图片等）
# 选项A：使用 MinIO 本地部署
COZE_BUCKET_ENDPOINT_URL=http://localhost:9000
COZE_BUCKET_NAME=patent-analysis
COZE_BUCKET_ACCESS_KEY_ID=minioadmin
COZE_BUCKET_SECRET_ACCESS_KEY=minioadmin
COZE_BUCKET_REGION=us-east-1

# 选项B：使用阿里云 OSS / AWS S3
# COZE_BUCKET_ENDPOINT_URL=https://oss-cn-hangzhou.aliyuncs.com
# COZE_BUCKET_NAME=your-bucket-name
# COZE_BUCKET_ACCESS_KEY_ID=<your-access-key>
# COZE_BUCKET_SECRET_ACCESS_KEY=<your-secret-key>
# COZE_BUCKET_REGION=oss-cn-hangzhou

# ============ 必填：数据库 ============
# Supabase 或自建 PostgreSQL
# 选项A：Supabase 云服务
COZE_SUPABASE_URL=https://xxxx.supabase.co
COZE_SUPABASE_ANON_KEY=<your-anon-key>
COZE_SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>

# 选项B：如使用 Supabase 自托管，URL 改为你的地址

# ============ 可选：飞书API（用于读取多维表格） ============
FEISHU_APP_ID=
FEISHU_APP_SECRET=

# ============ 可选：端口配置 ============
PORT=5000
```

> **注意**：代码中已将所有模块 URL/Token 硬编码为 fallback 默认值，所以 MODULE* 变量不配置也能工作。
> 但 S3 和数据库配置是必须的。

## 步骤4：配置 S3 存储

### 选项A：MinIO 本地部署（推荐）

```bash
# Docker 一键启动
docker run -d \
  --name minio \
  -p 9000:9000 \
  -p 9001:9001 \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"

# 创建 bucket
docker exec minio mc alias set local http://localhost:9000 minioadmin minioadmin
docker exec minio mc mb local/patent-analysis
```

### 选项B：使用现有云存储

直接配置对应的 endpoint 和凭证即可。

## 步骤5：配置数据库

### 选项A：Supabase 云服务（推荐，零运维）

1. 访问 https://supabase.com 注册
2. 创建项目，获取 URL 和 anon key
3. 在 SQL Editor 中执行建表语句（见下方）

### 选项B：自建 PostgreSQL

```bash
# Docker 一键启动
docker run -d \
  --name postgres \
  -p 5432:5432 \
  -e POSTGRES_DB=patent_analysis \
  -e POSTGRES_USER=patent \
  -e POSTGRES_PASSWORD=your_password \
  postgres:16
```

## 步骤6：初始化数据库表

在 PostgreSQL / Supabase SQL Editor 中执行：

```sql
-- 分析会话表
CREATE TABLE IF NOT EXISTS analysis_sessions (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'pending',
  input_type TEXT,
  input_value TEXT,
  file_name TEXT,
  file_url TEXT,
  text_content TEXT,
  results JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 分析步骤表
CREATE TABLE IF NOT EXISTS analysis_steps (
  id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
  session_id TEXT NOT NULL REFERENCES analysis_sessions(id),
  step_id INTEGER NOT NULL,
  step_name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  error TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(session_id, step_id)
);

-- RLS 策略（Supabase 必须）
ALTER TABLE analysis_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE analysis_steps ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow all operations" ON analysis_sessions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all operations" ON analysis_steps FOR ALL USING (true) WITH CHECK (true);

-- 索引
CREATE INDEX IF NOT EXISTS idx_steps_session ON analysis_steps(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON analysis_sessions(status);
```

## 步骤7：构建与启动

```bash
# 构建
pnpm build

# 启动（生产模式）
pnpm start

# 或使用 PM2 守护进程
npm install -g pm2
pm2 start pnpm --name "patent-analysis" -- start
pm2 save
pm2 startup  # 开机自启
```

## 步骤8：验证

```bash
# 检查服务是否启动
curl http://localhost:5000

# 测试文件上传
curl -X POST http://localhost:5000/api/upload \
  -F "file=@test.pdf"

# 测试分析流程
curl -X POST http://localhost:5000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"type":"url","url":"https://example.com/patent.pdf"}'
```

## 本地化部署 vs 扣子平台部署 对比

| 特性 | 扣子平台 | 本地部署 |
|------|---------|---------|
| 服务休眠 | 会休眠，需预热 | 不会休眠 |
| 代理超时 | 5-6分钟504 | 无代理层，自己控制超时 |
| 环境变量 | .env.local 不部署 | 完全可控 |
| 数据持久化 | 依赖Supabase | 本地数据库 |
| 文件存储 | S3云存储 | 自选（MinIO/云存储） |
| 4个工作流 | 远程coze.site | 仍然是远程coze.site |
| HTTPS | 平台提供 | 需自己配置（Nginx+Let's Encrypt） |

## 常见问题

### Q: coze-coding-dev-sdk 在本地能用吗？
A: 可以。SDK 读取环境变量（COZE_BUCKET_*、COZE_SUPABASE_* 等），只要这些环境变量配置正确就行。

### Q: 4个工作流能本地部署吗？
A: 不能。工作流运行在扣子平台上，只能通过 coze.site 域名调用。但你可以把 Next.js 应用部署在离扣子平台近的区域，减少网络延迟。

### Q: 如何配置 HTTPS？
A: 推荐使用 Nginx 反向代理 + Let's Encrypt：

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    # 关键：代理超时设长，工作流可能跑15分钟
    proxy_read_timeout 900s;
    proxy_connect_timeout 60s;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
