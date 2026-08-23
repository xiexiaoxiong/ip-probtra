# 专利无效检索系统（正式快照）

此目录只保存从 `5-invalidity-search-test` 显式 promotion 后形成的正式版本。正式服务运行在 `5109`，使用 `invalidity_prod` schema 与 `invalidity/prod` 工件前缀。

## 不覆盖的 release 模型

- `releases/<release-id>/`：promotion 产生的不可变代码快照；已有目录拒绝覆盖。
- `CURRENT_RELEASE`：当前激活 release 的普通文本 ID；不使用符号链接。
- `RELEASE_MANIFEST.sha256`：进入快照的每个源文件哈希。
- `RULES_MANIFEST.sha256`：该 release 绑定的根宪章和无效工作流规范哈希。
- `RELEASE_METADATA`：源摘要、promotion 时间和上一 release。

默认运行 promotion 只显示变更计划：

```bash
5-invalidity-search-test/scripts/promote_to_prod.sh
```

只有再次传入脚本输出的确认串才会创建并激活 release。promotion 不安装依赖、不启动或重启正式服务。测试版通过契约、隔离、运行恢复和全量样例回归并经人工复核后，才可执行确认。

## 正式运行

复制并填写正式专用配置：

```bash
cp 5-invalidity-search-prod/.env.example 5-invalidity-search-prod/.env.prod.local
ops/pm2/prod/start.sh
ops/pm2/prod/status.sh
```

正式 PM2_HOME 是 `ops/pm2/.state/prod`，与测试的 `ops/pm2/.state/test` 完全不同。正式 PM2 只管理 5109；模块一严格调用既有正式 `http://127.0.0.1:5101/run`，避免重复启动 5101。

正式配置必须提供独立的 `INVALIDITY_PROD_API_TOKEN` 和 `INVALIDITY_ALLOWED_SOURCE_ROOTS`。若 5101 支持 Bearer，使用独立的 `INVALIDITY_PROD_MODULE1_API_TOKEN` 并设置 `INVALIDITY_PROD_MODULE1_AUTH_MODE=bearer`；当前仍调用无鉴权旧 5101 时，必须显式设置 `legacy_unauthenticated`，禁止由缺失配置自动猜测。

若用户明确授权正式环境使用已配置的测试 provider 账号，可执行
`node ops/pm2/bootstrap-local-isolated-env.mjs prod --reuse-test-provider-credentials`。脚本只把智慧芽/EPO
凭据值写入正式前缀，不输出密钥，也不复用 API token、schema、上传目录、工件目录或
PM2_HOME。正式进程始终只读 `.env.prod.local` 中的 `INVALIDITY_PROD_*`，禁止运行时回落测试变量。

在 macOS 受控工作区中，promotion 会对 release 目录同时设置只读 mode 和禁止新增/删除的 ACL。
启动守门检查当前运行用户的实际写权限，并重算全部源文件、宪章和工作流 manifest；不因工作区恢复目录 mode 就放弃不可变性。

禁止直接从测试目录 import 源码、建立符号链接、在 release 内开发，或让测试 PM2 启停正式服务。
