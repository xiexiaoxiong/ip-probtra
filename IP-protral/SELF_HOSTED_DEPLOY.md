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

## 启动步骤

1. 至少补齐这些变量：

```env
PGDATABASE_URL=postgresql://user:password@127.0.0.1:5432/patent
LOCAL_LLM_BASE_URL=
LOCAL_LLM_API_KEY=
SEARCH_PROVIDER=brightdata_baidu
SEARCH_COUNTRY=cn
SEARCH_TIMEOUT_SECONDS=45
SEARCH_ALLOW_DIRECT_FETCH_FALLBACK=1
BRIGHTDATA_API_KEY=
BRIGHTDATA_SERP_ZONE=serp_api1
BRIGHTDATA_UNLOCKER_ZONE=web_unlocker1
COZE_BUCKET_ENDPOINT_URL=
COZE_BUCKET_NAME=
COZE_BUCKET_ACCESS_KEY_ID=
COZE_BUCKET_SECRET_ACCESS_KEY=
COZE_BUCKET_REGION=cn-beijing
```

说明：

- `PGDATABASE_URL` 是四段工作流之间的本地数据库通道
- `LOCAL_LLM_*` 会自动映射到旧的 `LLMClient` 所需环境变量
- `SEARCH_PROVIDER=brightdata_baidu` 是当前第三阶段默认实现，面向中文互联网和中国电商检索
- `BRIGHTDATA_SERP_ZONE` 用于百度 SERP 检索，`BRIGHTDATA_UNLOCKER_ZONE` 用于打开商品详情页抓正文
- `SEARCH_ALLOW_DIRECT_FETCH_FALLBACK=1` 表示 Bright Data 打不开某些页面时，允许直接 HTTP 抓取做兜底
- `COZE_BUCKET_*` 仍然需要，因为工作流内部会把附图和商品图片上传到对象存储
- 现在飞书不再是主链路依赖；只要你不再使用飞书节点输出，`FEISHU_*` 可以不配

3. 安装依赖：

```bash
cd /Users/xiexiaoxiong/Documents/patent/IP-protral
pnpm install

cd /Users/xiexiaoxiong/Documents/patent/1-patent\ analysis && uv sync
cd /Users/xiexiaoxiong/Documents/patent/2-keyword && uv sync
cd /Users/xiexiaoxiong/Documents/patent/2-keyword-fitness && uv sync
cd /Users/xiexiaoxiong/Documents/patent/2-keyword-electra && uv sync
cd /Users/xiexiaoxiong/Documents/patent/3-search && uv sync
cd /Users/xiexiaoxiong/Documents/patent/4-claim\ chat && uv sync
```

4. 构建前端：

```bash
cd /Users/xiexiaoxiong/Documents/patent/IP-protral
pnpm build
```

5. 用 PM2 启动整套服务：

```bash
cd /Users/xiexiaoxiong/Documents/patent
pm2 start ecosystem.config.cjs
pm2 save
```

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

- 第三阶段已经切到本地 `search_provider`，当前实装的是 `Bright Data + Baidu + 商品详情抓取`
- `1688 / 拼多多 / 京东 / 淘宝` 现在通过中国站点优先级和域名定向检索参与召回；如果你后面拿到这些平台的官方开放平台凭证，可以继续往同一个 provider 里加官方 API，不用再改工作流节点
- LLM 调用已支持通过 `LOCAL_LLM_*` 做兼容映射，但前提是你的网关支持 OpenAI 兼容协议
- 图片仍然走对象存储；如果你要连这部分一起本地化，下一步再把 `COZE_BUCKET_*` 替换成本地文件存储
