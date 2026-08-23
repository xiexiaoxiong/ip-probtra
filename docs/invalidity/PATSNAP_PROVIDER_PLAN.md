# 智慧芽（Patsnap）专利 Provider 接入计划

> 状态：智慧芽 REST adapter、隔离配置与离线契约测试已完成；P002、P060 beta、P061 已通过真实响应验收；P020 PDF 权限和真实 PDF 下载已通过 live smoke；专利取文已实现“EPO OPS 精确取文优先、仅在明确无覆盖时按同一 `patent_id + pn` 回退 P020”
>
> 版本：0.8
>
> 日期：2026-07-27
>
> 上位规则：`PROJECT_CHARTER.md` 1.5、`docs/invalidity/WORKFLOW_SPEC.md` 2.4

## 1. 我们要开发什么

在 `5-invalidity-search-test` 的 `I3-P` 中增加一个 `patsnap` provider adapter，把智慧芽 API
转换为项目统一的 `EvidenceRecord`、`SearchBatch`、`RetrievedArtifact` 和 capability
contract。第一阶段只接测试环境，完成：

- 普通现有技术检索日期通道；
- 中国抵触申请候选日期通道（如果智慧芽接口提供所需申请日/优先权日/公开日字段）；
- 候选题录、全文/PDF、图片、专利族和日期字段的能力探测；
- 原始响应/文件快照、哈希、分页、限流和失败审计；
- 与现有 I3-E、I4-S、D1/gap 和 I5 报告契约的衔接。

## 2. 解决的核心问题

当前系统没有智慧芽的已验证接口契约，不能安全假设其认证方式、签名、查询语法、分页模型、
全文下载方式、日期字段含义、专利族结构或商业数据留存权限。接入计划要解决：

1. 将供应商 DTO 隔离在 adapter 内，不让其字段进入业务状态机；
2. 区分智慧芽搜索 lead、已取回文献和可进入 CC 的合格证据；
3. 按普通现有技术/抵触申请两条日期通道保存 provider 过滤和本地资格判断；
4. 在测试/正式环境中隔离凭据、请求、工件和报告；
5. 在 API 不可用、字段缺失、限流或许可不明确时 fail closed 或显式 partial。

## 3. 服务的用户

- 使用中国专利无效/稳定性分析的律师、专利代理师和检索人员；
- 负责维护检索 provider、凭据和供应商合规边界的工程人员；
- 需要在模块实验室中观察查询、题录、全文、日期和失败原因的测试人员。

## 4. MVP 架构原则

- `PatsnapProvider` 只负责供应商通信、响应校验和统一证据对象转换；不得承担日期法律判断、
  新颖性判断、D1 选择或创造性组合。
- 使用现有 HTTP transport、超时、重试、ArtifactStore 和 PostgreSQL 审计能力；不新增消息队列、
  搜索集群、向量数据库或第二套编排框架。
- API 原始响应只能进入受控快照和哈希审计，不进入普通日志、前端、模型提示词或导出报告；
  密钥、鉴权头和签名材料永不落库。
- 测试配置只读取 `INVALIDITY_TEST_*`，正式配置只读取 `INVALIDITY_PROD_*`；缺失凭据、跨环境
  凭据、未批准 provider 或未知 API contract 必须 fail closed。
- provider capability 必须逐项声明：discovery、bibliographic_verification、full_text、
  pdf、images、family、publication_date、filing_date、priority_date；没有实际验证的能力
  不得宣称支持。
- 智慧芽不能因“商业数据库”身份自动成为 `qualified_evidence`；仍需真实内容、公开日期和
  来源链核验。
- 候选发现和正式取文可以使用不同数据源。当前默认固定为智慧芽 P002 发现候选、EPO OPS
  按同一公开号优先取文；只有 EPO 对该公开号明确返回无该文献、无全文或无 PDF 媒体覆盖时，
  才按同一 P002 `patent_id + pn` 回退智慧芽 P020。EPO 不接收 I2 查询，不增加、替换或重排
  智慧芽候选，P020 也不得改取同族或其他文献。
- 图文分析仍必须使用 `glm-4.6v` 系列多模态模型；provider 接入不能增加纯文本降级路径。

## 5. 当下限制与必须避开的依赖方式

- 在收到接口文档前，不猜测 URL、HTTP 方法、字段名、签名算法、分页参数或下载地址。
- 不绕过登录、验证码、访问控制、配额、频率限制或许可限制；不抓取超出用户授权范围的数据。
- 不把智慧芽 SDK 作为业务层依赖；如供应商 SDK 确有必要，必须固定版本、审查许可证并在
  adapter 内封装。优先使用现有 HTTP 客户端和项目 transport。
- 不把供应商原始 JSON 直接拼进 SQL、提示词、命令或报告；所有字段先经过 schema 校验和
  安全白名单转换。
- 不删除或覆盖 EPO/USPTO 的历史 adapter、fixture、报告和 provider 审计数据；当前仅停用其
  live 默认路径。

## 6. 做什么、不做什么与触发标准

### 6.1 本次要做

- 收集并核对智慧芽 API 文档、测试地址、认证资料和许可条款；
- 建立 request/response 脱敏 fixture 和 capability contract；
- 实现查询、分页、重试、限流、原始响应哈希和文献取回 adapter；
- 映射题录、公开日、申请日、优先权日、公开编号、族、全文/PDF/图片和来源标识；
- 为普通现有技术和抵触申请候选分别编写日期通道测试；
- 用一组真实样本完成测试环境 live smoke，再决定是否允许进入实例回归。

### 6.2 本次不做

- 不在没有真实 API 响应前声称智慧芽已经具备全球覆盖或全文/图片能力；
- 不修改 I0 多轮工作流、I4-S 新颖性规则或 I4-I 创造性规则来适配供应商返回格式；
- 不把智慧芽题录、摘要、相似度或搜索排序直接写入 CC 表；
- 不在测试凭据验收前配置正式环境或启动 5109；
- 不自动购买、续费或扩大智慧芽账户权限。

### 6.3 功能修改触发标准

provider 的实现分成“可离线审查的 adapter”与“可进入真实工作流的 live provider”两级。
官方文档和 OpenAPI 足以触发前者，但只有同时满足以下条件才能启用后者：

1. 用户提供接口文档或可核验的 API contract；
2. 明确测试环境 base URL、认证方式和凭据注入方式；
3. 明确搜索、取文、分页、错误码/限流、日期字段、专利族/图片能力及商业留存许可；
4. 至少提供一个可脱敏的真实响应样本，或允许在测试环境取得该样本；
5. 用户确认可以使用这些 API 资料在测试环境进行真实连通测试。

在第 4、5 项完成前，可以实现受 feature flag 保护、默认不启用的配置解析、请求/响应 schema、
fixture 测试和 smoke 工具；不得把未经真实验收的能力标为 `true`，也不得切换当前运行中的
provider。用户提供资料后，先更新本文件的“已确认 API contract”小节并重新阅读，再修改代码。

## 7. 已确认 API contract（2026-07-23）

根据智慧芽开放平台官方 REST API 概览、鉴权、请求格式、响应格式、错误处理、官方 OpenAPI
以及 P001/P002/P012/P018/P019/P020/P042/P060/P061 参考页，当前只冻结以下已经能够交叉
核对的事实：

- REST 基础地址为 `https://connect.zhihuiya.com`，所有请求必须使用 HTTPS；请求和响应使用
  JSON。
- 服务端请求采用 `Authorization: Bearer <API Key>`，POST 请求使用
  `Content-Type: application/json`；允许发送 `Accept: application/json` 和用于审计关联的
  `X-Request-ID`。
- 测试配置使用 `INVALIDITY_TEST_PATSNAP_API_KEY`，正式配置使用
  `INVALIDITY_PROD_PATSNAP_API_KEY`；两者不得同时出现在同一进程，也不得跨环境回退。
- 基础地址分别使用 `INVALIDITY_TEST_PATSNAP_BASE_URL` / `INVALIDITY_PROD_PATSNAP_BASE_URL`，
  默认只允许精确的 `https://connect.zhihuiya.com`。endpoint 使用独立环境变量，以便在官方
  指南和 API 参考页差异实测完成前不把路径写死为唯一真相。
- 已确认的 P001 能力只是“检索式统计专利量”，请求体必填 `query_text`（最长 3000 字符），并支持
  `collapse_type`、`collapse_by`、`collapse_order`、`collapse_order_authority` 和
  `stemming`。它只能用于查询预检、过宽查询拦截和预算估算，不能返回对比文件，也不能
  宣称完成 `discovery`。
- 官方 REST 指南示例路径为 `/search/patent/query-search-count`，P001 API 参考页展示
  `/search/patent/query-search-count/v2`；真实密钥 smoke test 前，两者均只作为候选路径，
  通过 `INVALIDITY_<ENV>_PATSNAP_COUNT_PATH` 显式选择。默认采用接口参考页明确标注的 `/v2`
  路径；无版本路径仅作为遇到官方 `67200101`/HTTP 404 时由人工显式切换的兼容候选，禁止
  adapter 静默双请求。
- 通用响应顶层为 `data`、`status`、`error_code`，错误时还可能包含 `error_msg`。P001 参考页
  将数量描述为 `data.total_search_result_count`，快速入门又描述为 `count`；adapter 只允许
  白名单兼容这些已记录变体，未知结构必须 fail closed，并保存脱敏原始响应工件及哈希。
- P002 专利列表检索采用 POST。REST 参考页展示
  `/search/patent/query-search-patent/v2`，官方 OpenAPI 同时列出无版本路径
  `/search/patent/query-search-patent`；通过
  `INVALIDITY_<ENV>_PATSNAP_SEARCH_PATH` 显式选择。默认采用接口参考页的 `/v2` 路径，无版本
  路径只允许人工显式切换，避免一次检索被自动重复计费。
  请求体必填 `query_text`（最长 12000 字符），支持去重/族折叠、排序、`offset`、`limit` 和
  `stemming`；`limit` 最大 1000，且 `offset + limit` 不得超过 20000。响应的文献数组为
  `data.results`，总量为 `data.total_search_result_count`，单条已记录字段包括 `patent_id`、
  `pn`、`apno`、`apdt`、`pbdt`、`title`、`inventor`、`authority`、申请人字段。列表结果仍只是
  search lead，不能直接进入 CC。
- P060 beta 单图相似检索采用 POST `/search/patent/image-single/beta`；P061 多图相似检索采用
  POST `/search/patent/image-multiple`。请求只接受可由智慧芽服务端访问的 HTTPS 图片 URL，
  同时显式传递 `model` 和 `patent_type`；P061 一次接收 2–4 张图片。单图和多图都是同步响应，
  不依赖 P010；只有本地图片需要先经另一个已授权上传通道转换为供应商可访问 URL 时，才需要
  单独评估 P010，adapter 不得自动代传或自动产生额外计费调用。
- P060 beta/P061 响应数量位于 `data.total_search_result_count`，候选位于
  `data.patent_messages`；已记录字段包括 `patent_id`、`patent_pn`、`title`、`url`、`img_id`、
  `score`、`apno`、`apdt`、`pbdt` 和 `loc`。这些结果只能作为带图像相似度的 search lead，
  不得直接升级为已取回文献或 CC 证据；原始响应、哈希、request id、供应商 correlation id
  和调用审计仍需进入隔离 ArtifactStore。
- 2026-07-23 的测试账号实测确认：P002 `/v2`、P060 beta、P061 均返回业务成功；同日 P001
  `/v2` 与标准 P060 `/search/patent/image-single` 返回权限码 `67200004`。因此当前账户契约
  明确选择 P060 beta，不得在 adapter 内静默回退或探测标准 P060；P001 只能标为无权限的可选
  预检能力，不能阻断已授权的 P002 检索。
- 同日使用 P002 返回的真实 `patent_id` 分别单次调用 P012、P018、P019，三个接口均在 HTTP 200
  内返回业务权限码 `67200004`。因此当前账号只能把 P002/P060 beta/P061 用于候选发现实验，
  不能通过智慧芽自身取得结构化题录、权利要求和说明书。自 2026-07-26 起，P002 的规范化
  公开号先由 EPO OPS 精确取文；自 2026-07-27 起，EPO 对同号文献明确无覆盖时，可按同一
  `patent_id + pn` 调用已开通的 P020 取得原始 PDF。P012/P018/P019 权限失败不得伪装成结构化
  详情已取回，也不再是取得 P020 PDF 的前置条件。
- P012 题录接口为 GET `/basic-patent-data/bibliography`；P018 权利要求为 GET
  `/basic-patent-data/claim-data`；P019 说明书为 GET
  `/basic-patent-data/description-data`；P020 PDF 地址为 GET
  `/basic-patent-data/pdf-data`；P042 全文附图为 POST
  `/basic-patent-data/fulltext-image`。详情接口使用 `patent_id` 或 `patent_number`，adapter 默认
  使用稳定的 `patent_id`；P018/P019 明确发送 `replace_by_related=0`，禁止供应商静默换成族内
  相关文献。P042 `limit` 最大 100。PDF 签名地址文档称有效 10 天，附图签名地址称有效 9 小时，
  因此这些地址只能作为短期取回线索，不能当作永久证据来源。
- P014 专利族、P015 后向引证、P016 智慧芽引证接口已在官方目录中发现，但 MVP adapter 不以
  它们作为首轮连通的前置条件；未完成真实响应映射前相关 capability 保持
  `documented_unverified`。
- 响应头中的 `x-correlation-id` 用于供应商排错，`x-openapi-amount` 表示本次调用量；可以
  写入受控审计元数据，但不得记录 API Key 或完整 Authorization 请求头。
- 官方检索帮助已确认 `PBD`、`APD` 和 `PRIORITY_DATE` 日期字段；provider 过滤只用于减少召回，
  P002 返回日期仍需本地边界检查，P012 与 P002 日期冲突时不得标记 verified，必须进入人工复核。
- 无正式幂等合同前，POST 的未知 transport 失败、HTTP 429 或 5xx 不自动重试，以免响应丢失后
  重复计费；仅对官方明确的业务忙码 `68300008` 作有上限退避。GET 可对 transport/429/5xx
  作有上限退避。每次实际请求都保存独立 request id、状态、供应商 correlation id 和
  `x-openapi-amount`；最终失败的异常也携带脱敏 attempt log。401/403 绝对优先禁止重试，
  凭据/权限/余额/配额、路径和参数错误直接失败。
- P012/P018/P019/P020/P042 的响应必须与请求的 `patent_id`/`pn` 严格一致；任何已返回标识
  冲突、非空非法 vendor ID 或 `pn_related` 族内替代都直接拒绝。以公开号请求时，P012 返回的
  vendor ID 还必须反向约束 P018/P019。仅因 `data` 只有一项不得推定它就是目标文献。
- P020/P042 是独立的权限与计费调用。P012/P018/P019 成功不预置 PDF/附图可用标志；只有显式
  下载并校验 PDF/图片 MIME、真实字节和哈希后，才能声明媒体能力通过。媒体失败保留已冻结的
  文本文献并形成 `evidence_gap/partial`，不得丢弃整篇文献。成功媒体的 P020/P042 metadata
  原始响应、响应哈希、调用审计和二进制哈希必须一并写入隔离 ArtifactStore，二进制工件引用
  对应 metadata 哈希，不能只保留会过期的签名 URL。

官方页面已确认检索、题录、权利要求、说明书、PDF 和附图 endpoint；测试账号已确认 P002、
P020、P060 beta、P061 权限及真实响应，并确认 P012/P018/P019 当前无权限。P020 live smoke
已取得 `application/pdf`、256,789 字节且可解析的真实 PDF，SHA-256 为
`db35a411165ce47fd090d191c6e6e7599425a4500b950dedd3d8e504aedb5c45`。仍未确认的事项包括
P042 权限和真实响应、P002 无版本路径、所有可选/空值结构、账户 QPS/日额度、商业用途及
原始响应/PDF/图片留存许可。capability 必须按接口逐项标注，P020 成功不推定 P042 或
P012/P018/P019 可用。

## 8. 当前实现与启用门（2026-07-27）

- 测试版已实现 P001/P002/P012/P018/P019/P020/P042、P060 beta、P061 REST adapter、Bearer
  注入、响应大小/MIME 校验、严格文献身份绑定、短期签名 URL 后端封装、原始响应哈希和环境
  隔离 ArtifactStore。P060 beta/P061 已通过官方样例图的适配器级真实 smoke。
- 测试账号已真实验证 P002、P060 beta、P061；实测样本分别返回专利列表、单图相似候选和多图
  相似候选，且均取得供应商 correlation id。成功响应未返回 `x-openapi-amount`，不能据此
  推定调用免费或额度未扣减。
- P001 和标准 P060 已真实确认当前账号无权限；smoke 与工作流不得先调用 P001 再决定能否调用
  P002，也不得把标准 P060 作为 beta 的自动回退路径。
- P012/P018/P019 已使用同一真实 P002 lead 分别实测，均返回 `67200004`。它们不再是默认
  取文前置条件：P002 已提供的规范化公开号会先转入 EPO OPS 精确取回；EPO 只有明确返回
  无该文献、无全文或无 PDF 媒体覆盖时，才会按原 P002 `patent_id + pn` 回退 P020。
- P020 已用既有 P002 lead 完成一次独立 live smoke，返回真实 `application/pdf` 文件；
  adapter 还会校验 `%PDF` 文件签名、PyMuPDF 可解析性、页数大于零、字节数和 SHA-256。
  P042 仍为 `documented_unverified`。
- 测试/正式分别使用 `INVALIDITY_TEST_PATSNAP_*` 与 `INVALIDITY_PROD_PATSNAP_*`；测试 Key 只会
  注入 5209 API，不进入 5201 parser、Portal 或正式 release。正式版尚未配置或启动智慧芽。
- 固定脱敏 fixture 与 smoke CLI 已建立；P002 discovery 与 P060 beta/P061 图像 lead 检索可按
  已验收契约用于独立模块实验，P012/P018/P019 在当前测试账号状态为 `permission_denied`。
- 测试模块实验室复用 `I3_PATENT_SEARCH`，支持 `search_modality` 为 `text`、`image_single`
  或 `image_multiple`；显式 `search_provider=patsnap` 只覆盖当前 lab run，不修改全局
  provider，不进入 I0。Portal `/test/module-lab` 已提供 P002、P060 beta、P061 三个只填充
  通用 JSON 的快捷模板，结果仍统一为 lead 并冻结真实 `provider_search_response` 或
  `provider_image_search_response` 工件。
- 真实 module-lab P060 beta 运行 `6fca23a3-5e2d-4b8a-a762-b45f21038e1f` 已成功：返回 3 条
  lead，调用一次，原始响应作为 `provider_image_search_response` 冻结并通过 SHA-256 校验；
  API 输出中的本地 URI 和租约 token 均被脱敏。该结果只验证测试实验路径，不扩大 I0 能力。
- 当前实际 `.env.test.local` 已设置智慧芽测试 Key，发现 provider 固定为 `patsnap`。完整
  I0 还必须具有同环境 EPO OPS 成对凭据；任一缺失即拒绝启动。P012/P018/P019 未开通不改变
  P002 发现能力，也不阻断 P020 PDF 回退。EPO 明确无覆盖且 P020 也无法取得同号 PDF 时，
  才保留 lead/evidence gap。正式环境尚未启动，启动前必须分别配置正式智慧芽与 EPO 凭据
  并重新验收。
- `/v2` 是当前默认搜索路径；只有真实返回 HTTP 404 或 `67200101` 时，才人工改为官方列出的
  无版本路径并单次重试，adapter 不做可能重复计费的静默双请求。

## 9. 仍需确认或开通的事项

智慧芽接口文档、base URL、Bearer 认证和测试 Key 已到位；凭据继续只保存在本机测试配置。
下一阶段还需补齐：

- 持续用不同国家/年代的公开号抽样验证 EPO OPS 与 P020 的覆盖边界；
- 如需使用智慧芽自身详情能力，再为测试账号开通 P012、P018、P019；
- 说明 P010 是否已开通，以便把冻结的本地目标图片转换成短期可检索 URL；
- 明确 P042 是否已开通；P020 已独立验证，不能据此推断 P042；
- 提供账户 QPS、日额度、计费规则和建议轮询/并发限制；
- 确认 API 原始响应、PDF、图片及其哈希能否作为内部审计快照留存，以及允许的保存期限；
- 确认测试账号覆盖的国家/地区、可检索文献范围及任何商用限制。

## 10. 智慧芽 P002 → EPO OPS → P020 取文合同（2026-07-27）

1. 智慧芽 P002 的 `patent_id` 是发现审计标识，`pn` 是跨 provider 精确取文标识；优先使用
   `pn`，只有 `pn` 缺失时才允许用 `apno` 查询 EPO application biblio。
2. EPO OAuth 使用 client credentials。Consumer Key、Consumer Secret 和 access token
   仅存在当前环境配置/进程内存，不得写入请求工件、异常、数据库、Portal 或报告。
3. EPO publication biblio 返回的规范化公开号必须与 P002 `pn` 完全相同。application biblio
   必须先把申请号唯一解析为一个公开号；多结果、无结果或身份冲突均 fail closed。
4. OPS XML description/claims 是可搜索文字来源；`images` inquiry 中 `FullDocument` 的
   `number-of-pages` 是完整文档页数。系统逐页请求真实 PDF，要求每次响应 MIME 为
   `application/pdf` 且恰为一页，再合并为完整 PDF。
5. 证据审计分别保存智慧芽检索响应、EPO 题录/全文/images 响应 bundle、每页哈希与字节数、
   合并 PDF 哈希及 `discovery_provider/retrieval_provider`。只有真实字节、MIME、精确身份和
   哈希都通过，才允许从 lead 升级为 `retrieved_document`。
6. 只有 EPO 对目标公开号明确返回 HTTP 404、无该公开号、无全文或无 `FullDocument` PDF
   媒体时，才允许进入 P020。EPO 鉴权失败、超时、网络错误、429、5xx、解析失败、身份冲突
   或多结果歧义都必须原样失败，不得借 P020 掩盖 EPO 故障。
7. P020 必须使用 P002 原始候选的同一 `patent_id + pn`；不得改取同族、相似标题或另一文献。
   返回 metadata 中任何身份冲突均 fail closed。系统必须立即下载短期签名 URL 指向的 PDF，
   校验 MIME、`%PDF`、可解析页数、字节数和哈希，并冻结 P020 metadata、调用审计和 PDF
   工件，不能只保存会过期的 URL。
8. EPO 明确无覆盖且 P020 也失败时，记录两个 provider 的有序尝试及明确
   `evidence_gap/partial`，保留智慧芽 lead，供律师决定其他官方或人工取文路径。
