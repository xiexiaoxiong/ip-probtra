# 无效检索正式快照约束

进入本目录前，必须依次完整读取根目录 `PROJECT_CHARTER.md`、根目录 `AGENTS.md`、`docs/invalidity/WORKFLOW_SPEC.md` 和本文件。本文件只补充正式快照的工程与运行约束，不得改变无效工作流业务语义。

## 冻结与 promotion

- 正式源码只来自 `5-invalidity-search-test/scripts/promote_to_prod.sh` 的显式 promotion，禁止直接在正式 release 中开发或打补丁。
- promotion 默认 dry-run；真正应用必须提供与当次源摘要和当前 release 绑定的确认串。
- 每次 promotion 新建 `releases/<release-id>`，已有 release 永不覆盖；`CURRENT_RELEASE` 是普通文本指针，不得使用符号链接。
- promotion 不自动重启正式服务。测试通过、人工复核 manifest 后，才可由 `ops/pm2/prod/start.sh` 启动或更新正式进程。

## 正式隔离

- 正式无效服务端口固定 `5109`，schema 固定 `invalidity_prod`，工件目录必须包含 `invalidity/prod`。
- 正式模块一 URL 必须严格为 `http://127.0.0.1:5101/run`；正式无效 PM2 不重复托管 5101，避免与现有正式 ecosystem 冲突。
- 正式 PM2_HOME 固定为项目内 `ops/pm2/.state/prod`，禁止使用 `~/.pm2` 或测试 PM2_HOME。
- 正式配置只从 `.env.prod.local` 读取；Portal/API 地址必须是 `INVALIDITY_PROD_API_URL=http://127.0.0.1:5109`，鉴权 token 如启用只能使用同环境 `INVALIDITY_PROD_API_TOKEN`。存在测试前缀、5201/5209、`invalidity_test` 或 `invalidity/test` 时必须 fail closed。
- GLM 配置必须为 `glm-4.6v` 系列多模态模型，不得回落纯文本模型。

运行产物、`.env.prod.local`、release 文件和 `CURRENT_RELEASE` 不提交为测试源码；每个 release 自带 SHA-256 manifest 与 promotion 元数据。

## 2026-08-16 正式 Agent 无效工作流上线

- 用户明确授权当前正式环境使用已配置的测试智慧芽/EPO 账号值。它们已由显式
  `--reuse-test-provider-credentials` 选项写入正式前缀；正式 API token、schema、上传/工件目录、
  5109、PM2_HOME 和 release 仍与测试隔离，运行时无测试变量回落。
- 已激活 release `20260816T153757Z-3fbc5ba58c5d`；5109 以 `environment=prod`、`invalidity_prod`、
  `INVALIDITY_MAX_ROUNDS=5` 和 2 个 worker 启动。本轮只部署服务，没有创建专利调查。
- 正式 Agent 只在用户后续提供专利文件/需求并显式选中“专利无效”时，才调用 I1—I5 及最多
  5 个 gap 轮；未选中时不得创建案件或猜测意图。
- macOS 受控工作区会恢复目录 mode，promotion 因此同时对 release 目录写入禁止新增/删除的 ACL；
  启动守门检查实际可写性、只读文件和完整 manifest，不减少内容完整性验证。
