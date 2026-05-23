# 独立部署说明

这版改造的目标不是重写四段工作流，而是把你现有的 Python 工作流原样本地运行，再让 `IP-protral` 只负责前端和统一编排。

## 架构

`ip-probtra.top` 只暴露 Next.js 应用。

内网本机服务：

- `127.0.0.1:5101` `1-patent analysis`
- `127.0.0.1:5102` `2-keyword`
- `127.0.0.1:5103` `2-keyword-fitness`
- `127.0.0.1:5104` `2-keyword-electra`
- `127.0.0.1:5105` `3-search`
- `127.0.0.1:5106` `4-claim chat`
- `127.0.0.1:5000` `IP-protral`

前端只请求 `5000`，Next.js 再去调用本机六个工作流端口。

## 关键变化

- `IP-protral` 默认不再指向 `coze.site/run`，而是默认指向本机 `127.0.0.1:510x/run`
- 前端上传文件改为写入本地 `.data/uploads`
- 分析会话改为写入本地 `.data/analysis-sessions`
- Python 工作流中的飞书认证增加了环境变量回退：
  - 优先 `FEISHU_TENANT_ACCESS_TOKEN`
  - 其次 `FEISHU_APP_ID` + `FEISHU_APP_SECRET`
  - 最后才尝试旧的 Coze workload identity

## 推荐运维方式

- 只使用根目录的 `ecosystem.config.cjs` 管理全部服务
- 只维护一份环境文件：`IP-protral/.env.local`
- 不要手工分别执行各模块的 `scripts/http_run.sh`
- 不要让本机和服务器使用不同启动命令

原因：

- `ecosystem.config.cjs` 会把 `IP-protral/.env.local` 中的数据库、LLM、搜索、对象存储变量统一注入 6 个工作流和前端服务
- 单独手工启动模块容易出现端口被旧进程占用、环境变量缺失、不同机器命令不一致的问题
- 现在各模块的 `scripts/http_run.sh` 已增加统一环境加载、关键变量校验、手工重复占端口拦截；即便误操作，也会尽早失败而不是运行到中途报错

## 启动步骤

1. 至少补齐这些变量：

```env
PGDATABASE_URL=postgresql://user:password@127.0.0.1:5432/patent
LOCAL_LLM_BASE_URL=
LOCAL_LLM_API_KEY=
COZE_SEARCH_API_URL=https://66vpykvvz2.coze.site/run
COZE_SEARCH_API_TOKEN=
COZE_SEARCH_TIMEOUT=120
COZE_MAX_CONCURRENT=5
COZE_BUCKET_ENDPOINT_URL=
COZE_BUCKET_NAME=
COZE_BUCKET_ACCESS_KEY_ID=
COZE_BUCKET_SECRET_ACCESS_KEY=
COZE_BUCKET_REGION=cn-beijing
```

说明：

- `PGDATABASE_URL` 是四段工作流之间的本地数据库通道
- `LOCAL_LLM_*` 会自动映射到旧的 `LLMClient` 所需环境变量
- 第三阶段（商品检索）由本地 `3-search` 服务调用扣子编程工作流；`COZE_SEARCH_API_TOKEN` 必填
- `COZE_BUCKET_*` 仍然需要，因为工作流内部会把附图和商品图片上传到对象存储
- 现在飞书不再是主链路依赖；只要你不再使用飞书节点输出，`FEISHU_*` 可以不配

2. 安装依赖：

```bash
cd /Users/adamrainbow/server/ip-probtra/IP-protral
pnpm install

cd /Users/adamrainbow/server/ip-probtra/1-patent-analysis && uv sync
cd /Users/adamrainbow/server/ip-probtra/2-keyword && uv sync
cd /Users/adamrainbow/server/ip-probtra/2-keyword-fitness && uv sync
cd /Users/adamrainbow/server/ip-probtra/2-keyword-electra && uv sync
cd /Users/adamrainbow/server/ip-probtra/3-search && uv sync
cd /Users/adamrainbow/server/ip-probtra/4-claim-chat && uv sync
```

3. 构建前端：

```bash
cd /Users/adamrainbow/server/ip-probtra/IP-protral
pnpm build
```

4. 用 PM2 启动整套服务：

```bash
cd /Users/adamrainbow/server/ip-probtra
pm2 start ecosystem.config.cjs
pm2 save
```

5. 后续更新和重启只用下面这组固定命令：

```bash
cd /Users/adamrainbow/server/ip-probtra
git pull
pnpm --dir IP-protral install
pnpm --dir IP-protral build
pm2 reload ecosystem.config.cjs --update-env
```

6. 日常查看状态：

```bash
cd /Users/adamrainbow/server/ip-probtra
pm2 ls
pm2 logs patent-web
pm2 logs patent-4-claim-chat
```

## 禁止事项

- 不要直接运行 `1-patent-analysis/scripts/http_run.sh`
- 不要直接运行 `2-keyword*/scripts/http_run.sh`
- 不要直接运行 `3-search/scripts/http_run.sh`
- 不要直接运行 `4-claim-chat/scripts/http_run.sh`
- 不要手工先起一个模块，再用 PM2 起另一套同端口服务

如果确实需要单独调试某个模块：

```bash
SKIP_ENV_VALIDATION=1 bash scripts/http_run.sh -p 5116
```

调试端口不要复用正式端口 `5101-5106`。

## 域名 `ip-probtra.top`

DNS 只需要把 `ip-probtra.top` 指向你的服务器公网 IP。

Nginx 示例：

```nginx
server {
    listen 80;
    server_name ip-probtra.top;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

配完后执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

如果你要直接上 HTTPS，再给这个站点签发证书即可，例如 `certbot --nginx -d ip-probtra.top`。

## 当前限制

- 第三阶段（商品检索）依赖扣子编程工作流（外部服务），需要保证 `COZE_SEARCH_API_URL` 可访问且 Token 有效
- LLM 调用已支持通过 `LOCAL_LLM_*` 做兼容映射，但前提是你的网关支持 OpenAI 兼容协议
- 图片仍然走对象存储；如果你要连这部分一起本地化，下一步再把 `COZE_BUCKET_*` 替换成本地文件存储
