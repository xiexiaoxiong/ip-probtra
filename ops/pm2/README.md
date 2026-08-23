# 无效检索 PM2 隔离运维

本目录只管理无效检索新增服务，不读取或修改根目录 `ecosystem.config.cjs`，也不使用默认 `~/.pm2`。

本机首次配置可运行 `node ops/pm2/bootstrap-local-isolated-env.mjs test`；它只读取权限为 600 的 `IP-protral/.env.local`，复用用户已允许共享的 PostgreSQL 与 GLM-4.6V 上游凭据，生成不同的测试 API/parser token、测试 schema/目录，并把测试 Portal 路由原子写回同一环境文件，过程不输出密钥值。正式环境单独运行同脚本的 `prod` 参数；两次生成的本地服务 token 不复用。只有在用户明确授权时，才可用 `prod --reuse-test-provider-credentials` 把已配置的智慧芽/EPO 值分别写入正式前缀；这是一次性配置复制，运行时仍禁止正式进程读取或回落测试变量。

| 环境 | PM2_HOME | 无效服务 | 模块一 |
| --- | --- | --- | --- |
| 测试 | `ops/pm2/.state/test` | `127.0.0.1:5209` | 本栈独立托管 `127.0.0.1:5201` |
| 正式 | `ops/pm2/.state/prod` | `127.0.0.1:5109` | 严格调用既有正式 `127.0.0.1:5101`，本栈不重复启动 |

测试 PM2 从 `5-invalidity-search-test/.env.test.local` 读取测试专用配置；正式 PM2 只从 `5-invalidity-search-prod/.env.prod.local` 读取正式配置。Portal/API 地址分别固定为 `INVALIDITY_TEST_API_URL=http://127.0.0.1:5209` 和 `INVALIDITY_PROD_API_URL=http://127.0.0.1:5109`，两边的 `*_API_TOKEN` 都是必填且不得复用。测试解析服务另用必填的 `INVALIDITY_TEST_PARSER_TOKEN`，该 token 必须不同于 5209 token；其 PM2 子进程只接收解析端口、工件/上传目录、抓取上限和解析 token，不接收数据库、主 API、provider、Patsnap、EPO 或 LLM 凭据。配置解析会拒绝另一环境前缀、跨环境 API/模块一端口、错误 schema、错误工件目录、纯文本模型以及占位凭据。智慧芽 REST 使用当前环境自己的 `*_PATSNAP_API_KEY` 负责 P002 候选发现，并在 EPO 明确无覆盖时负责 P020 同号 PDF 回退；EPO OPS 使用同环境成对的 `*_EPO_OPS_KEY` / `*_EPO_OPS_SECRET` 按公开号优先取回官方文献。所有 provider 凭据仅传给 5209/5109 API 进程，绝不传给 5201 parser。测试/正式凭据分别保存在各自 600 权限环境文件中，不跨环境传递。控制脚本以清空后的进程环境启动独立 PM2 daemon，避免父 shell中遗留的正式/测试变量被继承；测试目录与每个正式 release 也分别使用自己的 `.venv` 和 UV cache。

专利候选发现统一使用 `patsnap`。初始化脚本会把测试和正式环境的
`*_PATENT_PROVIDER` 固定写为 `patsnap`；缺少对应环境智慧芽 Key 或 EPO OPS 成对凭据时服务
拒绝启动。P002 只负责 lead 发现，EPO OPS 只按 P002 公开号取回同一文献，不接收 I2 查询，
因此不是检索回退。只有 EPO 明确返回同号文献无覆盖/无完整 PDF 时，5209/5109 才按同一
P002 `patent_id + pn` 调用 P020；鉴权、网络、超时、429、5xx、解析或身份错误不得回退。
EPO 与 P020 均无同号 PDF 时保留 evidence gap，不改用同族或其他候选。只有 bootstrap
改动了 Portal 路由或 token 时才需要重启 Portal。

## 测试版

```bash
cp 5-invalidity-search-test/.env.example 5-invalidity-search-test/.env.test.local
# 填写测试专用数据库、GLM-4.6V、智慧芽测试 Key 和 EPO OPS 测试凭据；不得填写正式凭据
ops/pm2/test/start.sh
ops/pm2/test/status.sh
ops/pm2/test/stop.sh
```

测试配置同时启动 `patent-invalidity-test-module1`（5201）和 `patent-invalidity-test-api`（5209）。5201 从 `5-invalidity-search-test` 内运行小型兼容服务 `invalidity.parser_service:app`，不执行根 `1-patent-analysis` 的源码、虚拟环境或旧启动脚本；因此测试解析服务的代码更新不会改变正式 5101。正式 5101 仍由既有正式栈独立管理。

## 正式版

正式服务必须先由 `5-invalidity-search-test/scripts/promote_to_prod.sh` 生成不可变 release 并写入 `CURRENT_RELEASE`。正式配置不启动 5101，以避免和根正式 ecosystem 争抢端口。

macOS 受控工作区会在命令结束后恢复目录 mode；promotion 因此还会对每个 release 目录设置
禁止新增/删除的 ACL。启动守门检查当前运行用户的实际可写性，并继续验证每个只读文件、源文件
manifest、宪章 manifest 和工作流 manifest，不允许仅靠目录模式恢复绕过不可变守门。

```bash
cp 5-invalidity-search-prod/.env.example 5-invalidity-search-prod/.env.prod.local
# 先按 promotion 脚本输出完成显式确认
ops/pm2/prod/start.sh
ops/pm2/prod/status.sh
ops/pm2/prod/stop.sh
```

这些脚本不会安装系统级 PM2 startup 项，也不会自动重启另一环境。电脑重启后，应分别运行所需环境的 `start.sh`。
