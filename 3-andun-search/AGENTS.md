# 第三种测试版模块三：安盾开放平台检索

## 边界

- 本模块仅用于测试安盾开放平台的关键词监测任务，不替代 `3-search/` 或 `3-product-search/`。
- 默认端口 `5108`，PM2 名称 `patent-3-andun-search`。
- 只写独立表：`andun_search_runs`、`andun_search_products`、`andun_api_call_logs`。
- 在商品详情文本和完整图片能力验证前，不得自动接入正式分析链路或模块四。

## 接口流程

```text
关键词
  -> monitor.keywords.task.add
  -> monitor.keywords.task.info 轮询 0/10/20
  -> monitor.keywords.task.goods.list 分页
  -> 独立落库 + API 耗时审计
```

## 凭据

- `ANDUN_APP_KEY`
- `ANDUN_APP_SECRET`
- `ANDUN_API_ENV=qa|production`
- 可用 `ANDUN_API_BASE_URL` 显式覆盖入口。

禁止把 AppKey/AppSecret 写入代码、测试或日志。签名必须对实际发送的原始 JSON body 做 SHA-256，并按文档将公共参数排序后首尾拼接 AppSecret 计算大写 MD5。

`task.info` 和 `goods.list` 是只读接口，瞬时网络错误可按 `ANDUN_REQUEST_RETRY_ATTEMPTS` 自动重试，每次必须重签名。`task.add` 不是幂等接口，响应丢失时不得自动重试，以免远端创建重复任务。已有 `taskId` 的未完成运行在服务重启后应自动续跑，失败运行可调用 `POST /runs/{run_id}/resume` 继续查询同一任务。

## 返回能力边界

安盾商品列表当前只明确提供商品首图、标题、价格、月销量、商品链接和店铺信息，不包含商品详情正文或详情图片集。因此本模块只记录检索结果，后续是否进入模块四必须基于真实测试再设计详情增强阶段。

2026-07-17 QA 实测中，有效凭据可以创建任务并持续查询，但最小任务超过 30 分钟仍停留在 `status=0`，商品列表为 `total=0`。在获得 `status=20` 和非空 records 之前，不得把“接口业务成功”误写为“商品采集成功”，也不得接入模块四。
