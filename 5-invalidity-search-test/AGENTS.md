# 无效检索唯一后端开发约束（目录名保留历史 test 标识）

本目录是专利无效检索的唯一业务后端。Agent 自动流程和十一模块实验室都调用这里；目录名中的 `test`、5209 和 `invalidity_test` 只为兼容既有数据与脚本保留。执行本目录任何任务前，仍必须先完整读取仓库根目录 `PROJECT_CHARTER.md`、根目录 `AGENTS.md` 和 `../docs/invalidity/WORKFLOW_SPEC.md`，再读取本文件及 README。工作流规范优先决定无效业务状态机和数据契约；本文件只增加本目录的工程、安全和运行约束。

## 业务边界

- 适用中国专利无效分析规则，检索范围覆盖全球专利与可证明公开时间的非专利材料。
- 当前阶段只为独立权利要求创建调查并独立运行；从属权利要求只保留在不可变目标快照，不进入检索、分析、状态汇总或报告。每轮新文献必须先逐篇做新颖性比对；没有单篇完整覆盖时才选择最接近现有技术、提取区别特征并进入下一轮及创造性组合分析。
- 第二/第三轮必须同时保留完整权利要求的单文献新颖性检索和 D1/gap/组合理由检索；D1 可以版本化重选，不能固定为第一轮相似度最高文献。
- 新颖性不得拼接文献；创造性不能仅凭多篇材料合计覆盖特征得出结论。
- 日期资格由确定性日期引擎判定，模型不得决定关键日或文献资格。
- 专利检索必须分开执行关键日前公开的普通现有技术通道和中国抵触申请候选通道；后者只进入新颖性分析。
- 模块一结果在调查创建时复制为不可变快照；不得长期依赖会被模块一重跑删除重建的行 ID。

## 模型与证据

- 图文分析统一使用 `glm-4.6v` 系列多模态模型；禁止静默回退纯文本模型。
- 单文献比对应同时提供目标专利文本/附图与对比文件文本/附图。确实没有图像时必须显式记录，不得伪造多模态成功。
- 搜索命中先是 `lead`；实际取得并哈希后是 `retrieved_document`；日期、内容和来源链通过后才是 `qualified_evidence`。
- 摘要、搜索片段、模型记忆和只有题录的 API 响应不能进入 CC 表。
- 自动化专利候选发现固定使用智慧芽 P002；缺少当前环境智慧芽 Key 时 fail closed，不回落 Google Patents、EPO 或 USPTO 检索。候选取文与发现解耦：P002 返回的规范化公开号先交给当前环境 EPO OPS 做精确题录与完整文献取回；只有 EPO 对该同号文献明确无覆盖或无完整 PDF 时，才按原 P002 `patent_id + pn` 回退智慧芽 P020。EPO 不接收 I2 查询、不扩展候选集，P020 也不得用同族或相似标题替代目标公开号。
- I2 必须先通读标题、摘要、全部权利要求和说明书，把检索概念区分为“保护客体/类别”“最能说明发明点的专有特征”“类别通常具备的语境特征”；不得再把全部权利要求限制等权地称为必要特征并固定机械拼接。
- 第一轮普通现有技术专利检索只执行宪章 2.15 / WORKFLOW_SPEC 3.21 固定的五种事实线：申请人+客体、标题客体+说明书核心发明点、分类号+说明书核心发明点、说明书客体+核心发明点+效果、标题/摘要客体+说明书功能效果；事实不足的组跳过留痕，每组至多一条、总数不超过五条，不再发射旧多分辨率/NPL/引证/效果辅助首轮线。
- P002 编译器按固定线映射 `AN/TTL/ABST/DESC/IPC`；每个概念组内部 OR 同义词、组间 AND，每个最终 OR 组最多五项。申请人线不得发送 P002 不接受的 `NOT PN`：供应商原始前十先冻结，再由本地确定性层按规范化公开号主体排除目标专利及 A/B/U/S 变体并记录排除项。
- 固定五线分别以 `query_id/query_variant` 创建独立 run，每个 run 的实际 attempt 为 1；零命中是成功事实并继续其他线，不创建 compact fallback。两类真实信息守门只在 I2 生成/编译阶段执行一次；I3 必须以同案同独权成功 `I2_QUERY_PLAN` 的 `query_plan_run_id + query_id + provider_expression` 精确回读冻结组合后原样发射，不得再用旧通用单条守门把申请人+客体线误判为“只有技术主题”，也不得因原始长 feature 未逐字出现在原子化表达式中拒绝。临时、跨案或表达式不一致输入继续 fail closed。
- I2 第一次模型输出没有形成完整合规计划时，必须携带拒绝原因自动重新生成；连续不合规时由确定性编译器使用冻结客体、真实 feature 词和分类候选组装缺失角色。每次尝试单独审计，不得只因单次格式或守门失败要求律师处理。
- 英文紧凑变体必须保留区别性技术词，不得把含孔、通道、槽或接口等限定的特征退化为孤立的 `unit`、`component` 或 `structure`。

## 唯一后端与历史标识

- 当前服务仍以 `INVALIDITY_ENV=test`、端口 `5209`、`invalidity_test` schema 和 `invalidity/test`
  工件前缀运行，以避免破坏既有数据与审计；这些值不再代表另一套“测试业务”。
- Agent 与模块实验室必须解析到同一 5209 服务身份、规则版本和数据命名空间，只按用户、session、
  investigation 和资源 owner 隔离。
- Portal 优先使用 `INVALIDITY_API_*` 中性变量，迁移期允许显式读取同值的 `INVALIDITY_TEST_*`；
  不得回落 5109、`invalidity_prod` 或历史 release。
- 历史 `5-invalidity-search-prod` 只读保留，修改或启动它不得影响当前用户请求。

## 工程约束

- I0 使用 PostgreSQL 持久化状态机与带租约作业，不使用仅存在进程内的递归或后台任务表达长期调查。
- iteration、query、module run 和 job 必须真实收口；上一轮全部持久化完成后才能创建下一轮。
- 不引入 Celery、Redis、Kafka、Elasticsearch、向量数据库或第二套大型编排框架。
- 不跨目录 import 原模块内部源码；复用能力必须经 API、数据库适配器或复制后独立测试。
- 所有外部调用有超时、来源标识、错误状态和审计事件；非幂等调用不盲目重试。
- 测试优先覆盖日期规则、合法状态转换、三轮上限、逐独立权利要求停止、从属权利要求范围排除、证据升级、正式/测试隔离和进程恢复。

## 2026-07-25 实施记录

- I0 新调查只遍历独立权利要求；混合目标快照中的从属权利要求不创建 `claim_investigations`，不进入 continuation、人工复核后的案件聚合或执行结果。目标专利没有独立权利要求时以 `independent_claim_missing` fail closed。
- I5 报告新增 `claim_scope`，目标权利要求与历史 claim 行均标注 `claim_type/in_scope/scope_reason`。历史从属行不删除，只作为技术审计数据保留。
- I2 增加确定性 JSON 包装兼容；任何兼容都只改变数组定位，不改变检索式守门。原始模型响应与标准化元数据写入 `i2_model_response` 工件，普通输出只返回工件 ID、哈希和标准化摘要。
- module-lab 对 `PatsnapApiError(error_code=67200004, retryable=False)` 返回永久错误 `PATSNAP_PERMISSION_DENIED`，不再进入 worker 自动重试；其他明确不可重试 provider 请求同理。
- 验证：全量 pytest `398 passed, 7 skipped`。历史调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 的 I2 真实重跑 `e1021e1b-1bdb-40b2-a42e-6d7f372c2fe4` 单次 succeeded，生成 8 条检索式和 13 个特征，并产生数据库审计工件。

## 2026-07-26 模块一完整性

- 5201 中立解析合同必须完整返回源文件信息、分页正文、著录项目与日期、摘要、权利要求书原文、全部权利要求及依赖关系、结构化说明书、摘要附图和全部说明书附图；“仅处理独立权利要求”只约束 I0/I2-I5，不约束 I1 事实读取。
- 新调查冻结全部 `patent_figure_artifacts`。module-lab 的 live I1 只能从调查已冻结源文件生成模块 run 专属 `module_test_snapshot`，不得覆盖调查原快照；供既有下游模型使用的 `target_images` 仍最多选择两张。
- 模块 run 附图读取接口必须同时校验 bearer token、module code、run 归属、工件根路径、MIME 与 SHA-256，不能向 Portal 暴露本地路径。
- 完整性回归要求实例目录所有专利 PDF 均具备摘要、日期、权利要求书、分页文本、结构化/完整说明书、摘要附图、技术附图、唯一 figure id、真实文件和哈希。本轮结果为 15/15；全量 pytest `402 passed, 7 skipped`。

## 2026-07-26 发明点与字段范围检索策略

- I2 新增 `invention_search_profile`，冻结保护客体、发明点摘要、发明点概念、类别共有语境概念及分类锚点；每个概念仍绑定独立权利要求的真实 feature ID，保留后续单文献完整覆盖审查所需的法律可追溯性。
- `SearchQuery` 新增 `query_role`、`search_scope`、`scope_reason`、`concept_ids`、`classification_anchors` 和 `provider_expression`。第一轮不再统一套用“主题 + 两个必要特征 + TACD”，而是至少生成“发明点精确线 + 全文”和“类别语境召回线 + 权利要求”。
- 水枪实例的确定性回归固定验证两条思路：分类号/水枪 + 位置选择性联接装置在全文检索；水枪/液体喷射 + 阀 + 罐在权利要求检索。生产逻辑不写死这些词，只以该实例验证通用角色、概念和字段守门。
- 类别语境概念必须是可独立进入 AND 组的原子特征；模型若把“罐 + 阀 + 管”等多个已标记为 common context 的真实 feature ID 压成一个概念，确定性层按这些既有 feature ID 拆开，不改变其角色，也不新增专利事实。
- 后端全量 pytest 为 `403 passed, 7 skipped`。水枪真实 I2 run `aa75e01c-65ea-46e9-9f87-d6faf64d05b6` 单次 succeeded：发明点精确线为 `TACD:(水枪 AND 位置选择性联接装置 AND 本体与阀杆轴线平行移动)`，类别语境线为 `CLMS:(水枪 AND 压力罐 AND 阀杆 AND 本体)`。旧冻结快照没有可核实分类号，因此依法守门使用保护客体而未猜造 IPC。

## 2026-07-26 复合概念的部分关键词规则

- 发明点或类别语境的概念说明可以是完整复合语句；可执行检索式只需使用其中一至三个有意义的部分技术关键词，不再要求概念整句连续出现。
- 部分词必须实际出现在 `expression` 中，并由确定性层映射回概念绑定的真实独立权利要求 feature ID；仅由模型声明 `concept_id`/`feature_id` 不能放行。
- 发明点精确线优先命中发明点概念，权利要求语境线优先组合多个类别语境概念；这是生成与解释策略，不再是额外硬守门。水枪固定回归接受 `水枪 AND 位置选择性联接装置 AND 中间位置` 与 `水枪 AND 压力罐 AND 阀导管 AND 阀杆`。
- 验证：新增两条水枪精确表达式回归通过；后端全量 `404 passed, 7 skipped`。隔离测试 5201/5209 已通过专用 PM2_HOME 重载，5209 `/health` 返回 test 环境正常；正式 5109 未触碰。

## 2026-07-26 三类任二与自动重生规则

- 首轮每条可执行查询只统一检查三类信息：表达式中实际命中的真实 feature 关键词、保护客体/类别、格式合法的分类号；任意两类存在即通过，三类同时存在也通过，不再按查询角色追加“发明点至少一个/语境至少两个”的硬淘汰。
- 模型提出的 IPC/CPC 可以作为标记为 `model_suggested` 的检索建议参与查询，但不得写回模块一著录事实、日期资格或法律证据。
- 单次模型输出不合规时 I2 自动把拒绝原因反馈给下一次生成；连续不合规时确定性组装缺失角色并重走同一守门，避免律师反复点击和模型无限空转。所有尝试分别保留审计。
- 水枪真实回归 run `c8dfe3b0-b88f-48dc-b7a3-c93236e5cac1` 首次生成即成功，内部模型审计为 `attempt_count=1`、`successful_attempt=1`；输出包含发明点全文线和权利要求语境线，四个模型建议分类号均标记为 `model_suggested`。后端全量回归为 `405 passed, 7 skipped`。

## 2026-07-26 GLM 快速失败与论文全文

- GLM 直连候选在发送任何鉴权或专利载荷前，必须先做最多 5 秒的无鉴权 HEAD 预检。预检失败立即换线；健康线路的单次真实请求最多 180 秒，模型调用总预算 360 秒。三项预算由 `INVALIDITY_LLM_DIRECT_PROBE_TIMEOUT_SECONDS`、`INVALIDITY_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS`、`INVALIDITY_LLM_TIMEOUT_SECONDS` 配置。
- 预检和请求错误只保留 `probe_timeout/probe_exit/request_timeout/request_exit` 等脱敏状态，不能记录 key、prompt、图片或 curl stderr。真实水枪 I2 run `65557897-4bfd-4210-b8a9-28c3e9485c49` 单次 job attempt 在 98.6 秒成功，证明失败直连不再占满旧 600 秒预算。
- OpenAlex 论文 lead 按 `best_oa_location`、`primary_location`、`locations` 顺序选择声明的 PDF；Portal 模块四优先取带 HTTP(S) `pdf_url` 的候选。声明 PDF 只算 lead，必须实际下载、校验 PDF/MIME、冻结原始字节、计算 SHA-256、抽取文本并渲染页面后才是 `retrieved_document`。
- arXiv 官方域名可在本机 TUN fake-IP 环境中使用精确主机白名单；任意其他出版社或仓储域名不得借此绕过公共 DNS 和 SSRF 校验。PDF 提取文本中的 U+0000 持久化前替换为空格，避免 PostgreSQL JSONB 写入失败。
- 真实取文 run `fd73a074-dbea-4a28-9b0f-c84246dda6de` 从 arXiv 取得 67,007 字符、4 张页面图及 SHA-256 `22b822fb3b0022e35a1b5156b47d599f4e5ba6810d92802624a2633c516e0bb2`；其日期资格 run `b4202b08-5ee1-440d-b30c-ee5c2bce80e8` 因缺少确定性公开日证据保持 unknown，证明“取得全文”不会绕过日期和技术披露守门。该论文只验证取文链路，不代表与水枪技术相关。
- 最终全量回归为 `410 passed, 7 skipped`；隔离测试 5209 health 正常，正式 5109 未触碰、未 promotion。

## 2026-07-26 智慧芽发现与 EPO OPS 精确取文

- `ConfiguredEvidenceGateway` 和 module-lab `I3_FETCH` 已把专利发现 provider 与取文 provider 分离：智慧芽 P002 只产生 lead，EPO OPS 只按该 lead 的公开号取回同一文献。输出明确记录 `discovery_provider=patsnap`、`retrieval_provider=epo_ops` 和精确身份映射。
- EPO OAuth 使用 client credentials，仅在内存缓存 access token；Consumer Key、Secret、token 和 Authorization 永不进入源码、数据库、前端、日志、报告或工件。`PATENT_PROVIDER=patsnap` 时缺少当前环境 EPO 成对凭据会拒绝启动。
- EPO biblio 返回的规范化公开号必须与智慧芽 `pn` 完全一致；不一致立即 fail closed。只有没有公开号时才允许用申请号查询官方 application biblio，且必须唯一解析到一个公开号后再继续。
- EPO `FullDocument` 按官方页数逐页下载并逐页校验为单页 PDF，再确定性合并为完整 PDF；记录每页 SHA-256、字节数、总页数和合并 PDF SHA-256。XML 题录、全文能力、description、claims、images inquiry 另存为 provider retrieval bundle。只有真实 PDF/XML 字节、MIME、身份和哈希通过才升级为 `retrieved_document`。
- 启动隔离补充：`scripts/http_run.sh` 的第二层 `env -i` 必须显式透传当前环境 EPO 成对凭据和 GLM probe、单线路、总预算三个变量；PM2 环境存在但 5209 设置缺失时先审计该 allowlist。EPO live `FullDocument` link 可能返回 `published-data/images/...` collection 前缀，取页 URL 构造前必须规范化，禁止产生重复路径。本机 TUN 仅精确兼容 `ops.epo.org`，相似后缀主机仍必须被 SSRF 守门拒绝。
- 水枪调查 `eda8afe2-9b90-5ad0-8965-b0851eebb8ec` 已用真实智慧芽候选 `US4239129A` 完成 EPO live smoke。I3_FETCH run `9fdfd93a-29b3-4f13-82d7-d1db81944bf0` 一次尝试 succeeded，请求/返回公开号均为 `US4239129A`；固化官方 PDF 为 10 页、909,288 字节，SHA-256 `4e895de12ff009120949feb2f296f641b16a15442cc7c91ac1bf8d82495d96f2`，重新读取文件与数据库记录一致，首页人工复核的公开号、标题和水枪结构图一致。
- 最终验证为后端 `416 passed, 7 skipped`、PM2 合同 `11 passed`；5201/5209 在线。Portal 无效合同、TypeScript 和有界 8GB Node 堆的 production build 通过。正式 5109 未触碰、未 promotion。
- Portal 律师模块四只有在 I3_FETCH 输出为 `retrieved_document/qualified_evidence` 且存在内容哈希时才显示“已取得全文”、执行日期核验或交给模块五；成功返回 lead 不再被误算为全文。候选卡分别显示发现来源和取文来源，智慧芽申请号可作为无公开号时的 EPO 兜底输入。Portal `pnpm ts-check`、定向 ESLint、无效契约测试和 production build 均通过。

## 2026-07-27 EPO 优先、智慧芽 P020 同号回退

- 用户确认智慧芽测试账号已开通 P020。专利发现仍只由 P002 完成；同一候选取文先调用 EPO
  OPS，只有 EPO 对精确公开号明确返回 HTTP 404、无文献、无全文或无 `FullDocument` PDF
  媒体时才调用 P020。EPO 鉴权、网络、超时、429、5xx、解析、身份冲突或多结果歧义不得触发
  回退，避免用另一路成功掩盖基础设施或身份错误。
- `EpoFirstPatsnapPdfRetrievalProvider` 已接入自动工作流和 module-lab `I3_FETCH`。P020 必须
  使用 P002 原始候选的同一 `patent_id + pn`，返回身份冲突直接拒绝；成功后立即下载真实 PDF，
  校验 MIME、`%PDF`、PyMuPDF 可解析性、页数、字节数和 SHA-256。EPO/P020 每次尝试、原始
  响应、调用审计、选用 provider 和 PDF 工件均分别冻结，P020 短期签名 URL 不作为唯一证据。
- P020 独立 live smoke 已通过：使用既有 P002 候选 `US4239129A` 的同一 `patent_id + pn`
  一次取得 `application/pdf`、256,789 字节，SHA-256 为
  `db35a411165ce47fd090d191c6e6e7599425a4500b950dedd3d8e504aedb5c45`。该样本本身已有 EPO
  覆盖，因此只验证 P020 真实权限和 PDF 能力；“EPO 明确无覆盖才回退”的分支由确定性集成
  测试验证，不额外消耗供应商调用。
- 后端全量回归为 `423 passed, 7 skipped`；PM2 隔离合同 `11/11 passed`。P020 无需新增凭据，
  继续复用只注入 5209 的测试智慧芽 Key。隔离测试栈已重载，5201/5209 均 online，5209
  `/health` 返回 `status=ok`、`environment=test`。正式 5109 未触碰、未 promotion。

## 2026-07-27 模块四“很多候选未取得全文”只读诊断

- 水枪调查最新律师模块四在两条 P002 检索线各返回 10 篇专利，但 Portal 当前对每条检索线
  固定 `slice(0, 2)`，因此总共只对 4 篇专利创建 I3_FETCH。`US20100051848A1` 与
  `AU2007309115B2` 分别位于第一条检索线第 3、4 位，均已保存准确公开号和智慧芽
  `patent_id`，但本轮没有创建取文 run；最新一轮 P020 实际选用次数为 0。
- `US20100051848A1` 的两条历史取文失败发生在新链路上线前：最早直接调用未开通的智慧芽
  详情链返回 `67200004`，之后 EPO-only 版本收到 EPO HTTP 404 并重试 3 次；当时尚没有
  404 → P020 链。按当前代码，该文献只要真正被调度，EPO 404 会触发同一 `patent_id + pn`
  的 P020。
- 最新实际调度的 4 篇中，`US5265547A`、`US4440193A`、`US6220311B1` 均由 EPO 成功取得；
  `USRE26631E1` 在本地 EPO 公开号解析阶段失败，按当前故障不降级规则未触发 P020。
  Portal 目前把“未调度”“调度后失败”统一显示成未取得全文，这是显示语义缺陷。此轮仅诊断，
  未修改候选预算、调度策略或页面。

## 2026-07-27 前 10 篇取文、逐篇 I4-S 与未披露语义

- 律师模块四的每条检索结果只截取供应商原始排序的前 10 篇；NPL 可以在这 10 篇内部优先
  处理带 PDF 路由的候选。同一独立权利要求下按规范化文献标识去重后，全部进入 I3_FETCH，
  最多 2 路并发。Portal 用每个 fetch run 的实际输入建立候选映射，分别显示取文成功、取文
  失败、未取得全文和等待取文，不再把未调度与失败混成一个状态。
- 成功取得 `retrieved_document/qualified_evidence + content_sha256` 的文献全部进入 I4-S，
  不再截取前 5。模型连接类临时错误在完成首遍后允许一次整批失败项补跑；最终逐篇结果才进入
  普通视图。部分 I4-S 成功不能把模块标成完成，也不能触发 I4-C/I4-I。
- `FeatureDisclosure.feature_text` 必须由 `Limitation.text` 确定性回填，不能要求模型重复生成；
  `DocumentComparison` 增加 `document_title/publication_number` 供律师视图识别文献。
- I4-S 的状态合同固定为：完整全文/附图复核后明确没有对应特征是 `not_disclosed`；材料不完整、
  含义模糊、证据冲突或无法决定是 `uncertain`；解析失败是 `analysis_failed`。模型理由明确
  包含“未披露、没有公开、未记载”等且不含不确定限定时，程序把错误的 `uncertain` 校正为
  `not_disclosed`；理由含“无法确认、全文不完整、证据不足、可能”等时保持 `uncertain`。
- 回归新增水枪“压力罐未披露”及“全文不完整无法确认”两组确定性测试。全量 pytest
  `425 passed, 7 skipped`；Portal 类型、ESLint、契约与 production build 通过。测试栈
  5201/5209 和 Portal 3001 已重启并健康，正式 5109 未操作。

## 2026-07-27 律师页实际输入展示

- `/test/module-lab` 六个业务模块的普通结果区必须先展示“本次实际输入”，再展示“本次输出
  结果”。模块定义中的简短“输入：……”只作未运行时说明，不能代替本次运行内容。
- 律师视图优先读取每个 module run 的不可变
  `input_snapshot.request.input`。live 运行因安全边界使用空 input、由服务端读取冻结案件事实时，
  Portal 应从关联 Investigation 和同次输出还原业务输入，不得把空对象、UUID、provider 或
  manual JSON 当作律师输入。
- 输入内容按六个模块分别显示专利文件、申请/优先权日、完整专利与独立权利要求、实际
  检索式及逐篇取文对象、技术特征及逐篇对比文件、报告所消费的前序持久化结果。技术审计区
  继续保留原始运行记录供 Codex 排错。
- Portal 类型检查、定向 ESLint、无效契约测试和 8GB production build 通过；仅重启
  `patent-web`，没有改动或重启 5201/5209，也未触碰正式 5109。

## 2026-07-28 律师模块四日期四分类

- `/test/module-lab` 的日期普通视图只显示 `现有技术`、`抵触申请`、`非现有技术` 和
  `日期无法确定` 四个律师标签，并在标签下显示一行后端日期规则给出的原因。
- 映射严格使用 `DateEligibilityResult.category`：`ordinary_prior_art`、
  `conflicting_application_candidate`、`post_date_lead`、`unknown` 分别对应上述四类。
  旧数据没有 category 时才按可进入新颖性/创造性的布尔值和人工复核标志兼容；日期缺失或
  状态冲突必须显示为日期无法确定。
- 此改动只简化 Portal 显示，不改变日期资格引擎、抵触申请只能用于新颖性的法律边界、
  I3_QUALIFY 输出或审计数据。

## 2026-07-28 模块五失败只读诊断

- 水枪调查最新一轮模块五实际收到 19 份已取得全文和哈希的文献，并创建 19 个
  `I4_S_SINGLE_REFERENCE`；1 篇成功、18 篇在各自 3 次 worker attempt 后因 GLM 外部依赖
  失败。成功文献为 `US20100051848A1`，返回 8 条特征披露。由于整批未全部成功，系统按合同
  没有继续运行 I4-C/I4-I。
- GLM 受限诊断共记录 54 次失败：49 次主线路 curl `request_exit=22`，4 次所有线路均在
  probe 阶段不可用，1 次主线路 `request_exit=28`；备用线路反复为 `probe_timeout` /
  `probe_exit=7`。现有错误审计没有 HTTP status 和脱敏错误信封，不能把 exit 22 进一步
  确认为限流、请求校验或其他 HTTP 错误。
- Portal 在模块运行期间 08:31:56 被重启，浏览器当前轮询因此抛出 `Failed to fetch`；
  5209 没有重启并继续收口后台任务。页面 catch 分支把已持久化子运行全部丢成空数组，导致
  普通视图误写“没有技术特征、0 篇全文”。后续修复必须支持按业务运行谱系恢复子任务，并
  把网络中断、无输入和逐篇模型失败分别展示。
- 本轮仅诊断，没有重跑、重启或改动运行代码。

## 2026-07-28 模块五 19 篇批次修复

- live 模块五必须由 Portal 持久化整批清单后再排队，允许 1—50 篇；批次保存每篇当前
  I4-S、历史 run、重试次数、I4-C、I4-I、状态与错误。页面断线或 Portal 重启必须按
  `module5_batch` 恢复，不能重跑前序模块或把输入清成 0。
- 后端 `/v1/lab/investigations/{investigation_id}/module5-inputs` 只恢复同一 claim 最近
  连续成功谱系中已取得全文、通过阶段守门且有内容哈希的文献，最多 50 篇。
- `VisionLLMClient` 对 HTTP 错误记录安全的 status/provider code；I4-S/I4-I live job 的
  `max_attempts=1`，重试由 Portal 批次有界管理。普通 I4-S 瞬时故障只补跑一次；
  `1210` 可在本地页面优先、首轮页面集合失败后缩为前 2 页的确定性降级下多一次。
- `_record_images` 优先使用不可变本地页或 data URL，每份最多 4 张；只有没有本地页时才
  使用远程 URL。禁止把需要 EPO OAuth 的保护图片 URL 直接交给 GLM。
- I4-I 必须消费模块九最多五轮的全部当前、日期合格 I4-S 结构化结果并记录已审阅语料；实际
  组合按区别特征增量选择 D1 与最多 5 篇后续文献。每项 D1 区别特征必须分别闭合公开、相同
  作用、技术启示、修改动机/路径、反向教导和效果可预期性，累计覆盖本身不得产生缺乏创造性结论。
  后续文献按新增必需特征覆盖贪心选择，再按 D1 既有评分因素和文献 ID 稳定破平；每篇最多
  2 张页面。返回的 `combination_document_ids` 是实际组合集合，未入选文献仍保留逐篇
  I4-S 结果。组合请求遇 `1210/1261` 只允许批次补跑一次。
- 真实批次 `4dcc04fb-795b-4b6f-8061-45ad4108f3df`：19/19 个当前 I4-S 成功，
  I4-C 成功，I4-I 成功；I4-I 实际为 4 篇、8 张文献页、2 张目标专利图，约 80 秒。
  `evidence_complete=false` 是证据结论而非执行失败。全量回归 `437 passed, 7 skipped`。

## 2026-07-29 通用机构检索、可读全文与结构披露

- I2 不再以“把全部必要特征串入检索式”为目标。运行时通读目标专利后形成通用
  `mechanism_model`：构件角色、拓扑关系、运动/状态关系、控制关系和技术作用均绑定真实
  `feature_id`；发明机构精确线和系统结构/变体线保持短组合，每条文字检索式最多三个概念
  组，执行门槛仍是“技术/机构锚点、保护客体、分类号三类任二”。缺少推荐角色时首轮会重新
  生成，第二轮可仅用同次已确认的目标事实确定性补齐；角色完整性不再盖过已有可执行检索式。
- I2 live 会读取同一调查最新成功、且源文件 SHA 一致的 I1 结果，把分类号和完整说明书章节
  覆盖到旧调查快照后再生成查询；只允许目标专利事实，不允许既知对比文件进入。
- 决定书基准与运行时完全隔离：已知对比文件只保存在
  `tests/fixtures/blind_benchmarks/`，`src/invalidity` 禁止出现这些公开号或导入该目录；
  `scripts/evaluate_blind_retrieval.py` 只在查询和检索结果冻结后评价各分线累计前 10 命中。
- I3-FETCH 的持久化后台作业新增 `readable-patent-v1`：EPO OPS description/claims XML
  优先生成带锚点 JSON/Markdown；没有结构化 XML 时处理 PDF 全部页面，扫描件使用
  `chi_sim+eng` Tesseract 双路并发 OCR，并按源 SHA 缓存。原始文件已取得与
  `analysis_ready` 分开显示。
- I4-S live 只接受 `analysis_ready=true` 且存在可读全文的文献。标题、摘要、题录不能回退
  成实体比对文本。披露判断改为目标结构角色与对比文件构件的连接/位置/运动关系映射：
  术语不同可以等价，只有功能相同不足以确认；工作过程只有在该结构不可避免时才允许
  `necessarily_implicit`。输出新增结构角色、结构映射、映射依据和双方机构摘要。
- 真实历史 P020 扫描件 `US20100051848A1` 验证：旧路径 8 页提取 0 字符；新路径 8/8 页
  OCR 成功，得到约 31,799 字符，双路并发约 21 秒，且可以命中内容哈希缓存。第一次新逻辑
  live I2 验证未进入生成阶段：主线路 `request_timeout`，备用线路
  `probe_timeout/probe_exit=7`；同时发现 worker 把一次传输失败重复三遍，现已把 live
  `I2_QUERY_PLAN` 外层 `max_attempts` 固定为 1，语义校验重生仍只在同一 job 内有界执行。
  后端全量回归 `444 passed, 7 skipped`；Portal `pnpm ts-check`、无效契约测试和 production
  build 通过。仓库全局 `pnpm lint` 仍被既有
  `src/app/module1/page.tsx` 条件调用 Hook 错误阻断，与本轮无效测试页修改无关。

## 2026-07-29 模块三多分辨率组合与冻结前十基准

- I2 ordinary 专利计划保留 6 类组合，并允许同一
  `subject_classification_plus_inventive_point` 对不同目标专利分类号形成多个独立 query。
  分类角色是 `general` 时最多逐项运行 5 个既有分类号，不得只选首项或伪造 subject/
  inventive_point 角色。计划预算为 14；P002 仍对每条只返回前 10。
- `SearchQuery.feature_term_groups` 是 feature ID 对齐的检索同义组。模型生成的
  `role_equivalent_terms`/`operation_terms` 和目标原词共同进入该组；历史画像无机构模型时，
  只允许从目标文本恢复通用结构动作族，并在 `portfolio_warnings` 中说明。该兼容路径不得
  接收候选文献事实。
- P002 对最大相似线使用 strict 编译：完整客体与完整 feature 锚点、组间 AND、允许 0 条、
  禁止 compact fallback。短线保护完整客体短语，已声明作用词优先于自动紧凑词和结构碎片。
- 最终 I2 run `e39c2365-63aa-43bc-8469-91e7f438ff7d` 无 GLM 网络重编译成功；冻结基准
  `20260729-compact-role-anchors-v2` 执行 10 条 ordinary 查询后通过，D1
  `US20200080816A1` 在客体+发明点线第 9、F41B9/00 分类作用线第 7。运行时查询与代码中
  不包含该公开号，答案只在冻结结果完成后由 benchmark 参数核对。
- 隔离测试栈 5201/5209 已重载且 `/health` 为 test/ok；Portal 3001 返回正常鉴权跳转。
  正式 5109 未触碰、未 promotion。
- 最终回归为后端 `447 passed, 7 skipped`；Portal 无效契约、`pnpm ts-check` 和 8GB
  有界 production build 通过。

## 2026-07-30 论文检索式与统一校验

- 水枪调查最新 I3-NPL 的四项 `QueryValidationError` 实际来自同一个输入
  `query="水枪"`：I2 将正文只含客体、分类号存于专利字段的
  `object_plus_inventive_classification` 查询复制为 NPL；四个 adapter 在网络调用前各自
  重复拒绝，因此并非四个论文来源同时宕机。
- I2 确定性矩阵现在只从实际表达式同时包含保护客体与真实技术/机构特征的 sibling 补齐
  NPL，并移除不适用于论文检索的分类字段。I0 传递结构化
  `text/subject_terms/feature_terms`；`CompositeNplProvider` 在 fan-out 前统一校验一次。
- 同案成功画像 `e7bf3eda-8186-4193-8e41-0a4bb172748d` 以新规则只读重编译后，NPL 查询为
  `水枪 AND 解锁机构`，不含分类字段；直接实测 arXiv、OpenAlex、Crossref 均完成请求，
  不再出现 `QueryValidationError`。公共 Web 本次实际返回 DuckDuckGo 图片验证码；现已
  识别为独立的访问受限错误并按 partial 保留，不能伪装成查询错误、DOM 漂移或零命中。
- 完整真实输入 run `387da09e-7b71-407d-b9fe-663fcd219982` 已由单 worker 正常收口为
  `succeeded`，`actual_provider=same_source_cached_profile`、`network_used=false`，
  持久化的 NPL 查询正是 `水枪 AND 解锁机构`，分类锚点为空。
- 重载后的真实 I3-NPL run `d886306c-cb9a-41b6-a1e4-09f232521d56` 单次 attempt
  `succeeded`，返回 6 条 lead；arXiv、OpenAlex、Crossref 成功，公共 Web 明确显示
  “登录、验证码或访问受限页面”，没有任何 `QueryValidationError`。
- Portal 对历史四条相同 `QueryValidationError` 只显示一次共同输入校验说明，明确本次
  请求没有发往四个来源；新运行的真实 provider 故障继续逐源显示。
- 定向回归 `56 passed`，后端全量 `450 passed, 7 skipped`；Portal 类型检查、模块实验室
  ESLint、无效契约测试和 8GB production build 均通过。隔离 5201/5209 与 Portal 3001
  已重载；正式 5109 未触碰、未 promotion。

## 2026-07-30 I3 取文前候选清理

- 新增持久化模块 `I3_CANDIDATE_FILTER`，位于 I3 专利/NPL 检索与 I3-FETCH 之间。live 输入
  只接受同一调查、同一独立权利要求的 `search_run_ids` 和可选 `query_plan_run_id`；候选、
  阶段、哈希和资格均从服务端持久化运行读取，浏览器不能提交或改写。
- 每条实际查询的供应商原始前 10 条全部保留。确定性规则按稳定 provider ID、申请号、
  去除 kind code 后的公开文本主号和可靠 family ID 合并；A2/A3/A4 等版本只选一个可取文
  代表，并保留所有查询、排名和合并理由。
- GLM-4.6V 只对唯一代表做一次批量技术语境分类，不作日期、披露、新颖性、创造性或无效
  结论。强目标客体/发明点/分类命中不得排除；题录不足、模型缺项、超时或失败一律
  `keep_uncertain`。模型只有高置信逐项确认无相关客体、无相关介质/能量、无相近部件作用、
  无相近操作/控制且无可类比机理时，才可 `exclude_obvious_unrelated`。
- 模型调用使用一张冻结目标图作视觉语境核对，I2 的完整发明理解为主输入；单次直连上限
  90 秒、总上限 120 秒，不做无限重试。模型审计保存提示哈希、脱敏错误类型/状态和实际
  decisions；失败后模块仍成功收口并把未确定候选全部交给取文。
- 筛选输出只复制取文身份和必要题录字段，大型供应商 provenance/工件仍以原检索 run 为
  审计真源，避免在 raw/group/fetch 三处重复膨胀。清理后 `fetch_candidates` 全部取文，
  不得再隐藏截断。
- 单元/接口回归新增同申请 A2/A3/A4 合并、跨查询谱系、强锚点不可覆盖、题录不足、
  模型失败、排除检查不完整、目标技术词重叠和可类比机理守门；后端全量为
  `463 passed, 7 skipped`。目标技术词高召回守门不把 `connect/coupling` 等通用连接词
  当作技术相关性；定向测试确认水流相关资料保留，而孤立 coupling 不会保护跨领域光学
  连接器。
- 水枪既有 P002 前 10 首次语义成功 run
  `d80208a4-4895-4188-bc3d-7b12d0eeaab3` 验证了 A2/A3/A4 合并和冰箱、光纤连接器等
  明显跨领域排除。重载后最终 run `97a7302e-e3e0-415a-aabc-9205f90fb59e` 为
  10 条原始、2 条重复、8 个唯一代表；本次 `glm_direct_transport_error` 在约 11 秒内
  fail-open 成功收口并保留全部 8 个代表。模型故障只降低取文节省量，不降低召回率，也
  不触发无限重试。
- 完成安全与重放审计：live `I3_FETCH` 不再接受浏览器回传的候选 `document/provider_kind`
  作为取文真源，只接受同案、同独立权利要求、成功 `I3_CANDIDATE_FILTER` 的
  `candidate_filter_run_id + candidate_id`，后端从持久化 `fetch_candidates` 回读题录。
  清理输出新增合同/身份/语义规则版本、代表选择理由、输入/决定/模型 prompt/总审计哈希；
  family ID 在同语言题名明显冲突时不合并。
- 本轮全量回归更新为 `465 passed, 7 skipped`。律师模块四已经按 I3-F 合同闭环；完整 I0
  的历史 `ConfiguredEvidenceGateway` 仍是单查询内发现后立即取文，尚未全局切换到本模块，
  后续改造不得把这一已知边界误报为完成。隔离 5201/5209 已重载并分别通过 health；
  正式 5109 未触碰。

## 2026-07-30 I2 发明锚点与机构等价词来源守门

- 水枪调查最新 I2 run `d248496a-f954-4d10-af2e-7b6858acfff9` 明确识别了
  “位置选择性联接装置”和“本体在中间位置时选择性联接阀杆”，却生成
  `水枪 AND 解锁机构`。根因不是本次 GLM 新输出，而是旧画像兼容函数把
  “选择性联接”自动扩成锁定/解锁动作，短线又优先选择了更短的扩展词；该错误随后沿
  `same_source_cached_profile` 链连续重编译。
- I2 现把联接/接合、锁定、解锁、脱开/分离作为不同动作族。可执行 `feature_terms`
  只能选独立权利要求原词或可回溯发明点短语；同动作机理的等价词只进入同一 feature 的
  OR 组，不能替代原词单独执行。已有 `mechanism_model` 的历史缓存画像也会先清理未获
  目标原文支持的 role/operation terms。
- Portal P002 编译器增加同一来源守门，即使读取旧持久化 run，也会在发网前剔除由
  “选择性联接”跨推得到的“锁定机构/解锁机构”。测试 fixture 可保留污染词验证兼容，
  但实际 provider expression 不得包含它们。
- 重载后完整真实输入 run `8b424a58-4d88-448d-b476-67a2b501a1e5` 单次成功并复用同源画像；
  实际短线为 `水枪 AND 中间位置、选择性联接`，双锚点线包含
  `位置选择性联接装置`，全部 query/term group/NPL 分线均不再出现“锁定机构/解锁机构”。
  后端全量 `465 passed, 7 skipped`；Portal 类型、契约、定向 ESLint 和 production build
  通过。隔离 5201/5209 与 Portal 3001 已重载，正式 5109 未触碰、未 promotion。

## 2026-07-30 I2 原子关键词守门

- `feature_terms` 和 `feature_term_groups` 中的每个可执行项必须是一个可独立检索的原子
  技术概念。顿号、逗号、分号并列项先拆分；可识别的“位置 + 选择性联接”说明文本也拆为
  不同候选。完整单一技术名词如`位置选择性联接装置`不拆。
- 短线排序优先真实动作/结构词，纯位置词降级；同一 OR 组只允许同一原子概念的原词和
  受控等价词。不同概念若同时使用必须位于不同 AND 组。I2 初始查询会把模型说明文本
  规范成`客体 AND 原子特征...`，同源缓存也执行同样规则。
- 历史画像里的作用词可以作为短线候选，但必须先通过目标原文和动作族守门；字面可追溯的
  `选择性联接`优先于`位置选择性联接装置`用于短召回，长精确线仍可保留完整装置术语。
- 真实 run `8ce8091b-824a-47c2-b600-2495965250e4` 的短线为
  `水枪 AND 选择性联接`，双词线为`水枪 AND 选择性联接 AND 中间位置`，所有
  `feature_term_groups` 均不再保存`中间位置、选择性联接`这一伪短语。
- 后端全量回归 `465 passed, 7 skipped`；只重载隔离测试服务 5201/5209，正式 5109
  未触碰、未 promotion。

## 2026-07-31 I4-S 整体结构等同与条件复核

- I4-S 不再把目标 feature 当成孤立词组逐字核对。每次运行同时读取同一冻结来源 SHA 的
  目标专利独立权利要求、完整说明书/分页正文和附图，以及一份 `analysis_ready=true` 的
  对比文件全文和附图；目标说明书只解释当前独立权利要求，不得把从属权利要求附加限定
  写回 limitation 集合。
- 映射顺序为 `literal -> role_and_relation/structural_equivalent ->
  necessarily_implicit_from_operation`。名称、构件数量、外形和拆分方式不同不自动否定；
  一个集成构件可同时映射多个目标特征。只比较当前特征实际适用的介质/能量路径、连接
  拓扑、运动/状态和控制关系，不把所有维度机械设为必选项。
- `necessarily_implicit` 必须保存至少三步必要性链并明确排除与全文相容的替代路径。
  `not_disclosed` 必须有 `mapping_basis=none`、候选结构排查概况和实质结构差异；仅缺少目标
  术语、独立零件或相同拆分方式的否定结果降为 `uncertain`。
- `structural-disclosure-v7` 对首轮内部矛盾执行一次有界复核：首轮已有至少两项高置信正向
  映射，或不超过五项的短权利要求达到 40%，或理由已承认候选构件的移动、旋转、开闭、
  流动、连接、传力、导电、传热、储液/承压关系却仍按名称否定时，只把开放特征送入第二遍。
  真正的连接拓扑/运动/控制差异不自动触发改判。
- 复核输入只含压缩目标上下文、对比文件证据窗口、最多八项最有结构对应可能的首轮开放项
  和不可改动的正向项，视觉输入最多一张目标图、两张对比文件图；总预算 60 秒、单直连
  40 秒。复核失败保存首轮结果并
  记录 `structural_review_attempted=true`、`structural_review_status=model_error` 和安全错误码；
  该结果不得进入新颖性/D1 聚合，模块五批次只补跑一次，仍失败则 partial 收口。
- 输出和持久化新增目标/对比机构摘要、结构角色与映射、映射依据、多条结构证据、集成构件
  标记、候选结构排查、必要性链、替代路径分析、复核 attempted/performed/status/error、复核
  图像数和 `analysis_pass_count`。prompt/rule 版本进入输入绑定，旧版结果不能静默复用为 v7。
- `structural_evidence` 必须回溯到当前冻结全文；普通文本采用严格逐段校验。仅当 OCR 有局部
  损坏、局部序列仍高度相似且多数构件编号锚点一致时，才允许有界模糊回溯；跨机构文本即使
  复用了相同编号也不得通过。这样既避免 OCR 小错抹掉真实结构证据，也禁止模型补写原文。
- 模块五批次按 `module5_batch_id` 隔离当前 I4-S/I4-C/I4-I 谱系；存在批次 ID 时，不得预载
  同案历史 canonical I4-S 结果。结构复核 `model_error` 的 I4-S 不算成功，不参与新颖性、
  D1 或组合分析；批次允许有界补跑，仍失败必须以 partial/failed 收口。
- `HUMAN_REVIEW_APPLY` 也执行相同 fail-closed 守门：child I4-S 的结构复核为 `model_error`
  时，在写 comparison 或提交 `complete_human_review_apply` 前抛出可重试工作流错误；人工动作
  不标 applied，`review_recomputation_required` 不清除。最终全量回归为
  `491 passed, 7 skipped`，跳过项均依赖真实 PostgreSQL。
- CN203880100U 的 v7 定点复跑 `50d4dfb9-5dd6-4510-8308-248a2f6d3c0b` 首次因 GLM 传输
  超时失败；唯一补跑 `04f667bb-a16c-438e-a6d6-d2609fa7ea2e` 完成首轮但第二遍结构复核仍为
  `model_error/glm_direct_transport_error`。该输出按合同不可进入 D1/实体聚合，不能作为结构
  等同业务验收成功；待 GLM 稳定后应重新验证 CN203880100U 与 US20050173559A1。

## 2026-07-31 水枪决定书模块五原文输入夹具

- 从 `/Users/xiexiaoxiong/Downloads/无效宣告请求审查决定书_2021227533927.pdf` 的证据清单
  只提取文献身份：证据 1–4、7–17 共 15 篇专利属于本次现有技术原文集合；证据 5 是另一份
  无效决定，证据 6 是专利及评价报告，均未纳入。
- 15 份原始 PDF 固定在
  `tests/fixtures/blind_benchmarks/water_gun_decision_2021227533927/source_documents/`。
  EPO OPS 精确取回 5 份；EPO 明确 404 或本次超时的同号文献由公开专利 PDF 镜像补取；
  `TW262532B` 经智慧芽 P002 精确公开号取得同一 `patent_id` 后由 P020 下载。每份均校验
  `%PDF`、可解析页数、字节数、SHA-256，并人工查看首页公开号与题名/附图。
- `manifest.json` 只保存证据编号、公开号、题名、公开日、来源、文件名、大小、页数和哈希，
  强制 `conclusions_imported=false`、`comparison_reasoning_imported=false`，且不提供预期
  disclosure 或法律结论。模块五后续使用时必须重新执行项目自身的全文可读化和 I4-S，不能
  读取决定书第 7 页以后的特征对应或评价结论。
- 输入目录不复制决定书 PDF；已移除分析 oracle 和人工结论评估器。模块五可用输入仅为
  `target_document/CN216205649U.pdf` 与 `source_documents/` 下 15 份原文。
- 已增加隔离回归，验证恰有 15 份 PDF、证据编号范围、文件签名/页数/大小/哈希及 I2 运行时
  不可见性。下载工具位于 `scripts/download_water_gun_decision_documents.py`，只允许测试
  环境 EPO 凭据，且不属于 `src/invalidity` 运行时。最终定向回归 `4 passed`，无效后端
  全量回归 `492 passed, 7 skipped`。

## 2026-08-01 决定书后验评估与 I4-S v17

- 决定书人工映射恢复为测试专用后验评估层，位于 `tests/evaluation_oracles/decision_i4s/`；
  只允许 `scripts/evaluate_decision_i4s.py` 在盲测冻结后读取。运行时代码和 blind runner
  继续只读目标专利与对比文件原文，测试会扫描 `src/invalidity` 防止 oracle 路径泄漏。
- I4-S v17 对成对旋转机构采用确定性跨行一致性：一条可回溯原文已证明 shaft/boss 位于
  ring/sleeve 内并相对转动时，可同步简单构件存在和包围关系；定位、间距、数量、方向、
  角度等附加限定仍逐项独立判断。明确完成全文/附图否定核验时返回 `not_disclosed`。
- 无线耳机最接近现有技术 `CN216649931U` 的 v68 冻结输出在后验层达到 `8/8`，且
  `oracle_loaded=false`。全量回归 `549 passed, 7 skipped`；正式 5109 未触碰。

## 2026-08-01 I4-S v28 证据来源与材料判定

- 所有肯定/否定引文都必须来自当前对比文件。模型复制目标专利文字时清空引文和位置；模型
  仅在真实引文外附加公开号、权利要求或段落括注时，可移除括注，但内部正文仍须独立回溯。
- 材料名称/成员和含量分散在同一文献不同段落时，可由多段证据共同证明；材料身份、化学
  形态或相态的实质差异不因数值范围相交而消失。中文“材料类型和化学身份不同”等并列
  表述属于实质差异，不要求机械出现相邻的“类型不同”。
- blind runner 对规则版本 fail-fast：旧 pending run 版本不同则跳过留痕并新建任务；新建
  任务返回版本不同立即中止。当前全量回归 `565 passed, 7 skipped`，正式 5109 未触碰。

## 2026-08-01 I4-S v44 结构作用等同与分散证据链

- 复合电子结构允许同一来源的分散原文形成连接链：必须同时逐字回溯“电路板—集合电子
  元器件的电连接关系”和集合分别包含目标成员的段落；缺少任一段时维持 `uncertain`。
- 全文和附图明确核查声学开口/通道或定位位置关系后，若候选结构不存在或拓扑不对应，使用
  `not_disclosed`；机械零件只有目标名称缺失时仍不得作确定否定。
- 流体阀只有在同一全文可回溯入口—阀座—出口、移动闭合件、关闭封紧和打开通流四组证据
  后，才按完整机构统一映射阀导管/阀杆/阀塞及开闭状态；选择性联接、轴线和位置限定不回填。
- `CN203880100U` v44 定点 run `8b9f05c5-6443-4fd1-9bc5-b5cbde4d0512` 已验证
  压柱+阀芯、阀座及贯通流路和手动把手/弹簧的集成映射；OCR 空行块以同文献最多 12 个相邻
  块形成证据窗口，三个实际窗口均可回溯，非相邻段落和跨文献内容不得拼接。
- 光引发剂 claim 8 / `CN107272336A` 后验评分 `2/2`，无线耳机 claim 1 /
  `CN216649931U` 后验评分 `8/8`，两次运行均先以 `oracle_loaded=false` 冻结再独立评分。
- 定向联合回归 `223 passed`；Portal TypeScript 与契约脚本通过。测试 5201/5209 已重载，
  正式 5109 未触碰；P002 五案真实前十仍由余额错误 `67200005` 阻塞。

## 2026-08-01 I4-S v45 配方含量计量口径

- 质量份/重量份与重量百分比只在“同一组分 + 整个组合物作为计量对象 + 可回溯区间相交”时
  作为配方含量等价表达；裸数字或采用树脂、溶剂、单体、固体、粘结剂等其他分母时不转换。
- 光引发剂在感光性树脂组合物中的 `1-5质量份` 与其占组合物重量 `0.5-10%` 的交集可确认
  公开；相对于树脂 100 质量份的相同数字保持 `uncertain`。全量回归 `598 passed, 7 skipped`。
- PM2 测试栈重载被本机提权额度限制拦截，不能声称 5201/5209 已加载 v45；正式 5109 未触碰。

## 2026-08-01 模块五材料引文精确来源守门

- 材料、配方和数值限定的 `evidence_quote` 改用去空格/标点后的精确来源匹配，不再复用
  机械长段落允许的有界 OCR 模糊匹配。对比文件中的“水性分散剂 0.1-2%”不能因为数值和
  大部分字形相近，就让模型生成的“水性润湿剂 0.1-2%”进入证据栏。
- 新增“不同材料名 + 相同用量区间”回归，错误引文必须清空并保留来源复核说明。当前工作区
  I4-S 规则已被并发迭代到 `structural-disclosure-v44`；后端全量回归 `593 passed, 7 skipped`。
- 电池浆料 `CN103633269A` 的 v42 source-only 定点实跑确认：水性乳胶为 `explicit`，水性
  润湿剂为 `not_disclosed`，润湿剂 `evidence_quote` 为空且位置为全文复核。随后全量冻结
  期间规则连续升级至 v44，blind runner 按版本守门拒绝混合结果；所以
  `20260801-five-case-v91` 暂不作为律师页稳定内置版本，Portal 仍绑定既有 v34。
- Portal `pnpm ts-check` 与 `node --import tsx scripts/test-invalidity-portal-contracts.ts` 通过；
  隔离测试服务已重载，正式 5109 未修改、未重启、未 promotion。待规则版本稳定后，需按
  同一版本重跑六篇电池浆料文献，再做冻结后评分并更新内置绑定。

## 2026-08-01 五案严格冻结审计与 v44 补跑

- `scripts/evaluate_decision_i4s.py` 现在只接受 `oracle_loaded=false`、决定书涉及文献全部完成、
  且各文献最新可用结果严格同一规则版本的冻结；`--expected-rule-version` 可进一步锁定版本。
  新增混合版本、缺文献和期望版本不一致的拒绝回归，防止旧评估把多个规则版本拼成假满分。
- 当前 v44 有效冻结与后验结果：电池 claim 1 为六篇、`7/7`，claim 4 为六篇完整但决定书无
  可评分概念；燃气灶 claim 1 为三篇、`6/6`，claim 10 为三篇、`1/1`；无线耳机 claim 1
  为六篇、`8/8`；光引发剂 claim 8 为五篇、`2/2`。所有 blind runner 输入仍只含目标专利、
  target-only limitation 计划和 source-only 对比文件，后验 oracle 未进入运行时。
- Portal 内置白名单更新：无线耳机使用计划 v62 / 比对 v93，电池使用计划 v46 / 比对 v91，
  燃气灶使用计划 v46 / 比对 v91；后者必须展示 claim 1 和 claim 10。契约测试固定这些绑定，
  并禁止该 route 出现 `evaluation_oracles`、决定书理由或后验评估器引用。
- 光引发剂 claim 1、9 尚未完成 v44；尝试继续调用 5209 时执行授权额度被系统拒绝，不得以
  间接方式绕过。水枪 claim 1 的决定书没有逐项特征映射，维持不可评分标记。
- P002 当前仍返回 `67200005`。v59 燃气灶历史搜索中 11/12 实际为余额失败却被旧脚本标为空；
  新脚本已经 fail-closed，余额恢复前不得以该历史搜索证明模块三召回成功。
- I2 新增公式型化学权利要求的目标全文结构片段线：仅在目标标题、摘要、权利要求、说明书
  或分页文本的光引发剂/引发剂邻近语境中，以通用形态规则提取最多两个非普通取代基片段，
  用目标标题中的保护类别组成全文线。不得使用`下式`、裸`化合物`或决定书/D1 词汇。
  光引发剂 v95 target-only 重编译现包含`光引发剂 AND N-吗啉基`和另一条目标结构片段线；
  是否真实命中 `CN107272336A` 必须等 P002 恢复后以该检索式自己的前十验证。
- 工作区当前 I4-S 规则为 v45；本轮通过并绑定的是此前在 v44 单一版本下完成的不可变冻结，
  光引发剂 claim 1、9 以及所有 v45 全量重跑仍不得声称完成。
- 最新 I2 代码修改后的测试服务重载被执行授权额度限制拒绝；5209 常驻进程尚未确认加载该
  规则。后续必须在用户明确批准后正常重载，不得使用替代进程或其他端口绕过隔离运维。
- 本轮后端全量回归 `600 passed, 7 skipped`；Portal TypeScript、无效契约脚本及定向 ESLint
  通过。只运行隔离测试 5201/5209，正式 5109 未触碰。

## 2026-08-02 模块实验室禁复用 + 首轮检索词上限 5 条

- 按用户决定，测试阶段禁止 I2 复用同源缓存画像：`invention_search_profile + mechanism_model`
  每次运行必须重新调用 GLM-4.6V 完整生成；测试服务（5209）与律师模块实验室对
  `generation_source=same_source_cached_profile` fail closed（`LAB_I2_CACHED_PROFILE_FORBIDDEN`）。
  同源复用只保留给正式 release 与离线重编译脚本。宪章 2.4 / WORKFLOW_SPEC 3.9 已冻结。
- 按用户决定，首轮可执行检索词总数硬上限为 5 条（`I2_DEFAULT_MAX_QUERIES = 5`，Portal
  module-lab `max_queries: 5`）。`_ensure_query_matrix` 首轮截断改为确定性优先级排名制：
  引证精确回查(cap2) > 说明书明确效果线(名词短语效果,cap2) > 客体分类+发明点(cap2) >
  NPL 完整权利要求线(cap1) > 双发明点(cap1) > 标题摘要(cap1,子序 -特征数+表达式长度) >
  结构召回(cap1,同子序) > 单发明点短精确(cap2) > 最大相似长线(cap1) > 纯动作族辅助效果线 >
  客体+发明点分类 > 分类动作线 > 其他 NPL > 抵触申请复制线 > 其余；gap 轮按
  (objective gap 优先, patent-ord/npl/conf) 每桶 cap1 + 填充。被裁线进入 `trimmed_sink`
  并写 `portfolio_warnings`，与「未形成」warning 区分。NPL sibling 继承专利线变体标签时，
  `is_npl_full_claim_lane` 判定必须先于 portfolio_rank 变体检查。
- 效果线分级：`_TARGET_EFFECT_RE` 已从单案件「水枪|喷枪」正则泛化为贪婪到标点的通用效果
  短语抽取，并在 `_target_patent_effect_terms` 修剪末尾「的+标题客体名词」；目标效果词全部
  属于 `_mechanism_action_families`（控制/定时/联接等纯动作词）时该线降级为 55+ 辅助线，
  名词短语效果线保持 10+ 高优先级。
- 双发明点抑制：`short_anchors < 2` 时不 emit `object_plus_two_inventive_points`，改发
  warning，避免与单发明点线重复占名额。
- 词族 bucket 机制修复（通用、非案件特定）：`add_anchor` 的 bucket_hint 提前到 term 选择前
  从 source_values+limitation.text 取首个非空 bucket，并始终按 (长度,词) 重选词族最短原子词，
  修复「显示屏和前置摄像头」复合词一次抢走两个锚点；`compact_inventive_anchors` 严格词组为
  空的锚点直接跳过（不再回退 short_terms 垃圾词如「定时」）；`_bucket_allows_term` timer 分支
  删除 `\b(计时|定时)\b`；`_query_anchor_term_group` 的 prefer_chinese 过滤豁免
  `_ARCHITECTURE_GENERIC_EQUIVALENTS` 受控英文等价词；`_guard_queries` 新增
  `raw_group_lists_by_feature` 保留同一特征多个不同词族的原始 OR 组边界；守门 initial 轮把
  模型散文本规范化为「subject首 + 锚点词」AND 组合。
- 专利锁定审计结论：全库无按专利号分支的生产代码（`lab.py` 中的 CN/US/WO 号码均为合成测试
  fixture）；所有词族/等价词/效果规则只由目标专利自身标题/摘要/权利要求文本驱动，对任意
  专利通用。宪章 2.5 / WORKFLOW_SPEC 3.10 已记录该边界。
- 验证：后端全量回归 `603 passed, 7 skipped` 连续 3 次通过；调试插桩已清除。测试对齐：
  wrapper/stable_features 上限改 5、gap objective 集合调整、same_source_recompile 显式
  `max_queries=20`（编译器变体覆盖意图）、legacy_profile 显式 `max_queries=20`。

## 2026-08-03 可执行关键词原子粒度（≤6 字）

- 按用户决定（任务 12328641 耳机案件暴露：客体「适配于不同耳朵的无线耳机」过长、结构召回线把
  权利要求整句当检索词）：冻结可执行关键词原子粒度规则，宪章 2.6 / WORKFLOW_SPEC 3.11 第 18 条。
- 实现：`analysis.py` 新增 `_refine_executable_cn_term`、`_refine_executable_subject`、
  `_atomize_executable_expression`、`_atomize_executable_queries`，在两个 `_ensure_query_matrix`
  调用点之后对最终检索组合统一原子化，提炼说明写入 `portfolio_warnings`。
- 规则要点：单个中文关键词以中文字符计不超过 6 字；英文短语与分类号不受影响；无显式 AND/OR
  的 NPL 自然语言整句线保持原样。客体提炼为核心类别名词（剥离「适配于…的」「无线/开放式/头戴」
  等通用修饰），修饰语成为独立 AND 关键词，客体同义词经同一提炼后组成有界 OR 组。
- 提炼管线（确定性、通用、无案件词表）：标点/关系动词（具有|设置有|电性连接|适配于|对应|用于…）
  /并列连词/「相对」拆分 → 去「所述/本体/内」等指代 → 超长时依次按「的」后缀、通用修饰前缀
  （记录为独立关键词）、通用尾缀（装置|机构|组件…）、两字修饰兜底（拒绝「式型性化」开头破词）
  提炼 → 尾部运动动词（升降|旋转|移动…）拆为独立原子词 → 仍超长的片段丢弃并写 warning，不得
  原句放行。
- 验证：耳机整句「所述耳机本体内具有PCBA板，所述PCBA板电性连接有喇叭和电池 AND …出音孔」→
  `(耳机 OR 耳麦) AND 无线 AND PCBA板 AND 喇叭 AND 电池 AND 出音孔`；「清洁件相对底盘升降」→
  清洁件/底盘/升降；水枪等短客体场景不回退。全量回归 `605 passed, 7 skipped` 连续 2 次；
  测试栈 5201/5209 已重载，正式 5109 未触碰。

## 2026-08-04 新增 I2_INVENTIVE_PROFILE（模块三画像-only 拆分）

- 按用户决定：原业务模块三「检索方案」拆分为「总结核心发明点」与「生成检索关键词」两个
  相互独立业务模块。后端新增底层 module code `I2_INVENTIVE_PROFILE`：与 `I2_QUERY_PLAN`
  同一 GLM prompt、同一画像/limitations 解析，但跳过全部查询守门与编译，输出 `queries` 恒为
  空；不走同源缓存画像复用（每次运行重新调用 GLM，天然满足测试环境禁复用规则），保留
  `LAB_I2_CACHED_PROFILE_FORBIDDEN` fail-closed 守门作为防线。`I2_QUERY_PLAN` 行为不变。
- 触点：`contracts.py` module_code Literal、`lab.py` `LAB_MODULE_CODES`（插在 I2 前）与新
  handler、GLM 响应 artifact（stage `I2_INVENTIVE_PROFILE`，路径 `model-responses/i2-profile/`）、
  `main.py` max_attempts=1 白名单、`scripts/lab_unit_check.py` 模块清单、`tests/test_lab_modes.py`
  fixture/manual/live 契约用例。宪章 2.7、WORKFLOW_SPEC 3.12 已同步。
- 验证：`tests/test_lab_modes.py` 88 passed、`tests/test_lab_source.py` 14 passed、全量
  `uv run pytest -q` **611 passed, 7 skipped**（基线 605，+6 全部来自本次新增用例与参数化
  扩展，无既有断言修改）。


## 2026-08-04 模块四输入固定为模块三画像输出（确定性重编译）

- 按用户最新决定（取代同日早些时候「模块三/模块四相互独立」的表述）：律师模块实验室模块四
  「生成检索关键词」的输入就是模块三「总结核心发明点」的输出。`lab.py` `_query_plan_handler`
  live 路径不再调 GLM 重新生成画像：先用 `_successful_live_lab_runs(request, claim=...)`
  找同案同独立权利要求最新成功的 `I2_INVENTIVE_PROFILE` run（claim 取自
  `request.source`/`data` 的 `claim_investigation_id` + input `claim_id`，与
  I3_CANDIDATE_FILTER 等 handler 的实际输入形态一致，不依赖 I0 checkpoint）；找到则
  `engine.recompile_initial_query_plan(previous_plan=<画像 run 输出>, source_plan_run_id,
  source_sha256, patent_context=_persisted_patent_context(request), max_queries)` 免模型
  确定性编译并直接返回（`network_used=False`、`actual_provider="same_source_cached_profile"`、
  `model_response_audit=None`，输出携带 `source_plan_run_id` 溯源）；找不到抛
  `PermanentJobError("LAB_I2_PROFILE_REQUIRED", ...)`，禁止静默回退 GLM。manual/fixture
  路径一行未动。
- `LAB_I2_CACHED_PROFILE_FORBIDDEN` 守门（2026-08-02）未删：因重编译分支提前 return，
  守门天然只作用于 `plan_queries` 分支的引擎内部缓存复用，注释已写明该语义。
  `recompile_initial_query_plan` 无需适配即可接受 queries=[] 的画像 plan（QueryPlan 允许
  空 queries，重编译不读 `plan.queries`）。正式 I0 流水线 I2 不经 lab handler，行为不变。
  宪章 2.8 / WORKFLOW_SPEC 3.13 已冻结。
- 测试：FakeEngine 增加 `recompile_initial_query_plan`；新增
  `test_live_query_plan_recompiles_from_latest_inventive_profile_run`（画像 run 选择正确、
  plan_queries 零调用、守门不触发、network_used=False）、
  `test_live_query_plan_requires_successful_inventive_profile_run`、
  `test_i2_recompile_accepts_profile_only_plan_without_queries`。全量 `uv run pytest -q`
  **614 passed, 7 skipped**（基线 611，+3 全部为新用例，无既有断言修改）。
- 隔离测试栈 5201/5209 已重启加载，`/health` 正常；正式 5109 未触碰、未 promotion。


## 2026-08-05 模块四改为以模块三画像调用 GLM 生成检索词

- 按用户决定（修订 2026-08-04「免模型确定性重编译」）：模块四的输入仍是模块三最新成功
  输出（发明画像 + 技术特征），但每次运行必须以该画像为输入重新调用 GLM 分析生成检索
  关键词/查询组合。
- 实现：`analysis.py` 新增 `generate_queries_from_inventive_profile`（7178）与
  `_generate_queries_from_profile_once`（8010）、`_queries_from_profile_prompt`（10285）：
  prompt 只含画像冻结事实（invention_summary、inventive_point_features 绑定 limitations、
  common_context、mechanism_model、客体同义词、分类号），不读专利全文/附图；有界重试语义
  与 `plan_queries` 对齐（反馈重试一次，第二次仍不合规才用画像事实确定性组装兜底并留痕）。
  模型提案依次过 `_guard_queries`（三类任二）→ `_ensure_query_matrix`（5 条上限优先级
  裁减）→ `_atomize_executable_queries`（≤6 字原子化）。`generation_source` Literal 新增
  `"module3_profile_live_model"`（analysis.py:84）。引证回查/目标效果线依赖 patent_context，
  本路径不生成，缺失经 portfolio_warnings 呈现。
- `llm.py` `invoke_json` 新增显式 `allow_text_only`（默认 False 不变）：模块四该次调用是
  纯文本子任务（`target_images=[]`），模型仍为 GLM-4.6V，不构成静默纯文本降级；其他调用点
  fail-closed 行为不变。
- `lab.py` `_query_plan_handler` live 分支改用新方法；模块三 run 查找与
  `LAB_I2_PROFILE_REQUIRED` 门控不变；返回 `network_used=True`、`actual_provider=None`、
  `model_response_audit` 持久化。`LAB_I2_CACHED_PROFILE_FORBIDDEN` 守门、manual/fixture、
  `recompile_initial_query_plan`（正式 release/离线脚本）均未动。
- 测试：lab 用例替换为 `test_live_query_plan_regenerates_queries_from_latest_inventive_profile_run`
  （断言新方法被调、plan_queries/recompile 零调用、network_used=True、audit 持久化）；
  引擎级新增 4 用例（主流程 prompt 不含专利全文且原子化/上限/audit 生效、反馈重试、连续
  不合规确定性兜底、输入不足零调用失败）。全量 `uv run pytest -q` **618 passed, 7 skipped**
  （基线 614，无既有断言改坏）。
- 宪章 2.9 / WORKFLOW_SPEC 3.14 已冻结。隔离测试栈 5201/5209 已重启加载，`/health` 正常；
  正式 5109 未触碰、未 promotion。


## 2026-08-05 检索词 OR 同义词/中英文并联、NPL 关键词化、重复检索式去重

- 按用户决定（真实运行 `c32ffcef-aac5-4f0e-90af-5245ec12cb1b` 模块四输出暴露四项问题：
  英文关键词孤立无 OR 同义/中文、检索式重复、NPL 线为权利要求整句、客体未原子化）。
- OR 同义词补齐（`_supplement_executable_or_members`，`analysis.py`）：只取画像冻结事实，
  两层池——可信池（synonyms_zh/en、机构等价词）直接放行；原文词 atoms 池需同族过滤，
  无同族命中则单原词放行、不硬造；每词 OR 成员 ≤5。
- NPL 整句豁免废除：本文件 2026-08-03 条目中「NPL 自然语言整句线保持原样」的表述
  **已被本次决定取代**（宪章 0.1 规则 7 要求的过时表述修正以此条目为准）——NPL
  （论文/技术资料）检索式改为关键词 AND/OR 组合，共享 ≤6 字原子化层，适用全部 I2 路径。
- 去重：键 = (provider_kind, 规范化 expression, 分类号, 日期通道, 检索范围)，被去重的
  检索式进 trimmed_sink 并写 portfolio_warnings 留痕。
- `_queries_from_profile_prompt`（analysis.py:10625）同步要求模型输出 OR 并联同义/中英文
  关键词、NPL 用关键词组合而非整句。
- 测试：修正 9 处因新规失效的既有断言；全量 `uv run pytest -q` **622 passed, 7 skipped**
  （基线 618）。隔离测试栈 5201/5209 已重启加载，`/health` 正常；正式 5109 未触碰。
- 宪章 2.10 / WORKFLOW_SPEC 3.15 已冻结。

## 2026-08-06 I4-S 实质相同标准 + 两道确定性守门（v46/v23）

- 宪章 2.11 / WORKFLOW_SPEC 3.16 已冻结：`compare_single_reference` 在结构复核合并、
  确定性 reconcile 之后、输出组装之前执行两道确定性守门，manual/live/fixture 全路径
  自动覆盖；I2/lab/main 契约、数据库 schema 不变。
- Prompt 强化（`_single_reference_prompt` 与 `_structural_reconciliation_prompt` 同步）：
  实质相同 = 结构和位置关系与目标没有不同且功能作用相同；下位概念/具体构件承担相同
  角色（钉子之于固定单元）即视为公开；输出 not_disclosed 前须逐一排查候选构件并在
  `alternative_path_analysis`/`structural_search_summary` 记录每个候选为何不对应。
- 守门 A 否定断言核验（`_run_negation_guard`）：not_disclosed（或理由含
  未提及/未发现/无对应/未公开/未记载 的 uncertain）触发；候选词集按 ①特征核心名词
  ②绑定同义词 ③角色词 ④其他特征映射/结构证据构件 ⑤机构总结构件 有序并集、去重、
  上限 8；候选在全文出现且未在该条目推理/结构证据/排查字段处理的，触发一次有界候选
  复核（≤4 特征、1 次纯文本调用，附 ±200 字命中上下文）；复核后仍遗漏的确定性降级
  uncertain 并写 `guard_warnings`。
- 守门 B 跨特征一致性对账（`_run_consistency_guard`）：父披露子不得「无该结构」否定；
  机构总结/其他特征映射出现的构件不得被判「全文未提及」；矛盾对并查集成簇，≤3 簇各
  一次合并重判，仍矛盾（含超上限簇）的否定侧降级 uncertain。两道守门只许降级、绝不
  由程序把否定改成肯定。
- 契约：`I4S_RULE_VERSION=structural-disclosure-v46`、`I4S_PROMPT_VERSION=i4-s-structural-v23`；
  `DocumentComparison` 新增 `negation_guard_*`、`consistency_guard_*`、`guard_warnings`；
  被降级条目保留 `guard_original_status`/`guard_note`。
- 测试：新增 13 个单元/全路径测试，修正 2 个既有断言（补守门复核响应）；模型调用全部
  mock；全量 `uv run pytest -q` **635 passed, 7 skipped**（基线 622）。

## 2026-08-06 I4-S 守门回归修复（fail-closed 放行 / 文案 / 空证据盲区）

- 问题1：`_run_negation_guard` 与 `_run_consistency_guard` 的守门复核 `invoke_json`
  此前漏传 `allow_text_only=True`，真实客户端 fail-closed 拒纯文本调用导致
  performed=False（FakeVisionClient 不校验该参数，故 mock 测试未暴露）；两处均已
  显式放行并附宪章 2.9/2.11 注释（首轮已完成多模态阅读，守门复核是有界纯文本
  质量检查，不构成 4.7 禁止的静默纯文本降级）。
- 问题2：performed=False 时告警文案不再误写「复核后仍……」；守门 A 分「经候选
  复核后仍未逐一处理」（reviewed_ids 内）与「候选复核未执行/失败，按 fail-safe
  降级」，守门 B 分「合并重判后仍矛盾」与「合并重判未执行/失败，按 fail-safe
  降级」。
- 问题3：空证据否定盲区——中文候选词对英文 document_text 词面零命中时，原守门
  完全跳过。守门 A 新增确定性触发：`not_disclosed 且 evidence_quote 为空` 即纳入
  候选复核（`blind_review_ids`，payload 带 `target_structural_role` 与
  `empty_evidence_negative` 标记）；`_negation_guard_review_prompt` 要求按角色语义
  （非中文词面）在全文搜索。复核后仍空引文**且** `alternative_path_analysis` 也空
  → 降级 uncertain；模型在 apa 记录完整排查过程可维持 not_disclosed；复核未执行 →
  fail-safe 降级。
- 源码 bug 修复：盲区特征（无词面违规）进入复核时 `violations[feature_id]`
  KeyError，改为 `violations.get(feature_id, [])`。
- 测试：新增 6 个回归测试（fail-closed 客户端放行、A/B 告警文案区分、盲区触发
  与 prompt 指令、盲区仍空证据降级、盲区模型改判正向）；修正约 20 处既有测试
  （补守门复核响应，`RejectTextOnlyWithoutAllowanceClient`/`FailGuardReviewClient`
  /`_guard_review_keeps_negative` 辅助）；全量 `uv run pytest -q`
  **641 passed, 7 skipped**（基线 635）。

## 2026-08-06 I4-S 守门修复第二轮（v47/v24）：角色全文重扫 + 守门错误码

- 问题1（守门A只打发确定性候选）：真实 run 9c63b7ee 中「压力罐」复核只逐条驳回
  词面候选，英文全文中真正的角色候选（reservoir/bladder）从未被评估。修复：
  `_negation_guard_review_prompt` 对**所有**被复核特征（不只 empty_evidence_negative）
  要求围绕 target_structural_role 按实质相同标准做角色语义全文重扫（按角色语义而
  非中文词面搜索）；payload 新增有界 `document_evidence_text`（≤18000 字，重扫需要
  可读全文）；响应 schema 新增每特征 `role_candidates_evaluated`（{candidate,
  source: deterministic|full_text_rescan, verdict, reason}），合并时逐条并入
  `alternative_path_analysis` 留痕（`_guard_review_role_evaluations` /
  `_guard_merge_role_evaluations`，保留现有字段兼容）。确定性复检加严：空证据否定
  特征复核后维持否定的前提是至少一条 source=full_text_rescan 记录，否则降级
  uncertain 并告警「未做全文角色重扫」；有词面候选的非盲特征维持「候选全部处理
  即可」规则不变。
- 问题2（耳机案守门B performed=False，run 72820386）：错误被 except 吞掉无法确认
  根因。修复：守门 A/B 复核调用 `request_timeout_seconds` 60→120、
  `direct_attempt_timeout_seconds` 45→90、`max_tokens` 4096→6144（疑似超时/截断
  两个主因一并抬高）；调用失败按 `_safe_review_error_code` 记录
  `negation_guard_error`/`consistency_guard_error`（contracts 新增两字段），
  响应整体无效记 `GUARD_REVIEW_OUTPUT_INVALID`，fail-safe 告警文案附真实错误码。
  schema 过严修复：合并重判/候选复核返回条目缺失时不再整体丢弃，按 feature_id
  部分合并，缺失条目记 `GUARD_REVIEW_OUTPUT_INCOMPLETE` 并按未重判 fail-safe
  降级；守门B 的 `rejudged_negative_ids` 只计入实际合并的否定条目（此前响应部分
  无效时未重判条目被误标「合并重判后仍矛盾」）。
- 契约：`I4S_RULE_VERSION=structural-disclosure-v47`、`I4S_PROMPT_VERSION=
  i4-s-structural-v24`。
- 测试：新增 5 个回归测试（全特征重扫指令与全文入 prompt、缺 full_text_rescan
  降级、A/B 错误码记录、截断响应部分合并）；既有盲区/一致性测试的复核响应补
  `role_candidates_evaluated`；全量 `uv run pytest -q` **646 passed, 7 skipped**
  （基线 641）。

## 2026-08-06 I4-S 守门修复第三轮（v48/v25）：守门B反向对账 + 全否定特征重扫留痕

- 缺陷1（守门B缺反向对账）：v47 run 5990ba53 中「阀杆带有阀塞」等子特征正向
  披露、父特征「可移动阀杆」却判 not_disclosed 的反向矛盾未检出（模式一要求共享
  构件同时出现在正向映射文本中，子特征映射到阀座26/阀杆32 时不含父特征名词）。
  `_consistency_contradictions` 新增矛盾模式三 `reverse_parent_child`：子特征为
  正向披露且 reference_structure_mapping/structural_evidence 非空，父特征
  not_disclosed 且理由属「无该结构/未提及」类，父子方向按文本包含关系判定
  （子特征 ≥2 字核心构件短语是父特征 feature_text 的组成），走既有并查集簇合并
  与合并重判流程。通用实现，无案件特定词。
- 缺陷2（守门A复核可无重扫痕迹蒙混）：「压力罐」复核只有 deterministic 评估
  记录，因 evidence_quote 非空绕过空证据复检。按 SPEC 3.16 §6 加严：确定性复检
  扩到**所有被复核后维持否定的特征**（不再区分空证据/有词面候选、不看
  evidence_quote）——`role_candidates_evaluated` 必须含 ≥1 条
  source=full_text_rescan 记录（含「未发现候选」的显式记录），缺失则降级
  uncertain，告警「候选复核缺少全文角色重扫痕迹」（空证据盲区特征文案保留
  「空证据」前缀）；复核未执行/失败的空证据特征仍走 fail-safe。复核 prompt 同步
  明确：每特征至少一条 full_text_rescan 记录，重扫无候选时须写 candidate=「全文
  角色重扫」、verdict=「未发现候选」、reason=重扫范围与结论，缺失视为复核未完成。
- 契约：`I4S_RULE_VERSION=structural-disclosure-v48`、`I4S_PROMPT_VERSION=
  i4-s-structural-v25`。
- 测试：新增 3 个回归测试（反向矛盾检出+合并重判消解、quote 非空否定缺重扫
  降级、「未发现候选」显式记录通过）；既有 4 处测试修正（补 rescan 记录/更新
  告警断言/版本号）；全量 `uv run pytest -q` **649 passed, 7 skipped**
  （基线 646）。

## 2026-08-06 I4-S 候选构件发现预检（v49/v26，宪章 2.12 / WORKFLOW_SPEC 3.17 §6）

- 逐特征判断之前新增「候选构件发现」预检：`_discover_candidate_components`
  在 `compare_single_reference` 首轮批次调用之前执行一次纯文本模型调用
  （`allow_text_only=True` 附宪章 2.9/2.12 注释，120s/6144 tokens），prompt
  （`_candidate_discovery_prompt`）含对比文件全文证据窗口（复用首轮 ≤36000
  字符窗口）+ 全部目标特征，要求模型先理解整体机构再按角色语义（非中文词面）
  为每个特征列出候选构件/结构区域，引文必须逐字来自原文。
- 确定性回验（`_normalise_candidate_inventory`）：每条 candidate 的 quote 经
  归一化子串匹配真实存在于 document_text，无法回验/缺名称引文的整条丢弃并记入
  `candidate_discovery_dropped`（feature_id+candidate+reason）；集合外 feature_id
  丢弃；整体无效记 `CANDIDATE_DISCOVERY_OUTPUT_INVALID`。
- 清单注入（`_inventory_entries_for_features` 按特征子集裁剪）：①首轮各批次
  `_single_reference_prompt`（只带本批特征）②结构复核 prompt（含补漏重判）
  ③守门A复核 prompt（特征级 `discovered_candidates`）④守门B簇 prompt；
  清单内容保留在 prompt JSON 中（空/None 时 pop 省略清单段）；守门A候选词集
  新增 ③+ 来源——已回验清单候选优先于词面来源④⑤（跨语言场景最可靠候选，
  英文候选名可直接命中英文 document_text 触发候选复核）。
- 失败降级：发现调用失败/响应无效不阻断比对，降级为无清单运行（各 prompt
  省略清单段），记录安全错误码。run 输出新增 `candidate_discovery_attempted/
  performed/error`、`candidate_inventory`（持久化供律师查看）、
  `candidate_discovery_dropped`（均带默认值，向后兼容）。
- 契约：`I4S_RULE_VERSION=structural-disclosure-v49`、`I4S_PROMPT_VERSION=
  i4-s-structural-v26`。
- 测试：新增 4 个（schema/引文回验与伪造丢弃留痕、失败降级无清单运行+错误码、
  四类 prompt 清单注入断言、守门A候选词集含已回验候选）；FakeVisionClient 增加
  `discovery_response` 路由（按 prompt 标记识别发现调用，未配置时模拟传输失败
  且不计入 calls——既有 600+ 测试的响应序列与调用序号零连锁）；全量
  `uv run pytest -q` **653 passed, 7 skipped**（基线 649）。

## 2026-08-06 I4-S 候选构件发现传输重试（v50/v27）

- v49 真实回归：耳机案发现正常（含 1 条伪造引文回验丢弃）；水枪案（全文
  47032 字符、证据窗口 ≤36000、16 特征）发现调用 `MODEL_TRANSPORT_ERROR`
  降级无清单——大 prompt + 6144 token 响应在 120s 内未完成（或网关偶发）。
- 修复（与既有有界重试语义一致）：`_discover_candidate_components` 传输类失败
  自动重试一次（共最多 2 次实际调用），第二次仍失败才记错误码降级；
  `request_timeout_seconds` 120→240、`direct_attempt_timeout_seconds` 90→180，
  max_tokens 6144 不变；schema 无效（`CANDIDATE_DISCOVERY_OUTPUT_INVALID`）
  不重试（非传输问题）。
- 审计新增 `candidate_discovery_attempts`（contracts 带默认值字段）：区分一次
  成功（1）/ 重试后成功（2）/ 两次均失败（2）。
- 契约：`I4S_RULE_VERSION=structural-disclosure-v50`、`I4S_PROMPT_VERSION=
  i4-s-structural-v27`。
- 测试：新增 3 个（首次传输失败+重试成功 attempts=2、两次均失败降级+错误码、
  schema 无效不重试 attempts=1），`FlakyDiscoveryClient` 模拟发现调用前 N 次
  传输失败；全量 `uv run pytest -q` **656 passed, 7 skipped**（基线 653）。

## 2026-08-06 I2 发明点自创术语改写（功能表达线，宪章 2.14 / SPEC 3.19）

- 画像 schema：`SearchConcept` 新增可选 `generic_component`（上位通用构件词）
  与 `function_effect`（功能效果词组），类型为新模型 `ProfileTermGroup`
  （text + synonyms_zh/en + source_reference + rationale，全带默认值）；
  缺失、非对象或缺主词一律容忍为 `None`，旧画像/旧缓存解析不变。
- 新检索线 `QueryVariant.OBJECT_PLUS_COMPONENT_PLUS_EFFECT`
  （object_plus_component_plus_effect）：客体 AND 通用构件 AND 功能效果词组，
  提供者表达式按字段拆分——客体+构件进 `CLMS`、效果词组进 `DESC`
  （`_component_effect_patent_expression`）；多发明点各自分线、最多 2 条，
  每词 OR ≤5 成员、组间 AND、分类号独立字段照旧。
- 首轮混编：portfolio_rank=22（客体分类+发明点 20 之后、NPL 线 25 之前），
  variant_caps=2；`_ensure_query_matrix` 首轮在预算 ≥2 时确定性预留 1 条
  功能表达线（至少 1 条、最多 2 条），自创词术语线不得独占首轮；被裁线进
  trimmed_sink/portfolio_warnings 照旧。
- 守门：改写词经 `_profile_component_effect_sources` 并入
  `_profile_feature_sources`/`_profile_feature_or_groups`，与画像同义词同等
  可信（不受同词族门槛剔除）；功能表达线两 OR 组绑定同一特征时按锚点所属
  raw 组分别过滤，防止构件词与效果词合并互污；候选自带的字段化
  provider_expression 在正文未被改写时原样透传，原子化改写后按同一确定性
  规则重建 CLMS/DESC，不退化单字段。三类任二、≤6 字原子化、5 条上限、
  去重、禁复用守门与 manual/live/fixture 一致性均不变。
- prompt：模块三首轮画像 prompt 要求发明点含自创术语时产出两组改写词
  （可省略）并扩 schema；模块四 prompt 增加第 ⑧ 条功能表达线角色说明，
  改写词组列为合法 OR 来源。
- 版本：`I2_RULE_VERSION=i2-query-plan-v2`、`I2_PROMPT_VERSION=
  i2-inventive-profile-v2`（analysis.py 新增并写入两处 I2 调用审计）；
  I4S 的 v50/v27 不动。
- 测试：新增 5 个（新字段解析+缺省/非法容忍、水枪案表达式与 CLMS/DESC
  形态+≤6 字原子化、首轮混编与紧预算保底、多发明点分线与 OR≤5、版本号
  与审计）；全量 `uv run pytest -q` **661 passed, 7 skipped**（基线 656）。

## 2026-08-06 I2 画像 prompt 修复：common_context 场景客体无绑定（化学案）

- 症状：电池浆料案（2014106703299）权利要求 1 真实 GLM 生成连续失败，错误为
  `common_context_features 第 1 项没有绑定权利要求中的真实特征` /
  `缺少具体技术概念`（run e7429b3e、24658f7a、7539ec8f）。
- 复现与根因（in-process 走 generate_decision_query_plans.py 同路径，真实 GLM）：
  修复前 7 跑 3 成 4 败。失败响应中模型反复把「锂离子电池隔膜」（应用对象）、
  「水性陶瓷浆料」（保护客体本身）列为 common_context_features 且
  `feature_ids=[]`——该案权利要求 1 是组合物权利要求，features 数组里没有
  隔膜/浆料限定可绑定；旧 prompt 把 common_context 定义为「确认技术场景的
  客体或构件」，诱导模型列出场景客体；校验（analysis.py:5481/5492，宪章冻结）
  要求每项绑定真实特征，必然冲突。新功能表达字段也使响应变长，偶发 JSON
  截断/传输超时。
- 修复（只改 prompt 表述，校验未动）：首轮画像 prompt ①明确两类 concept
  每项 text 非空、feature_ids ≥1 且只能引用 features 数组已列 temp_id，
  绑定不了就不返回该项；②保护客体/应用对象/整机类别只属于
  technical_subject/subject_synonyms 层级，不得进任何 concept 数组；
  ③返回前逐项自检、不合规项先删除再返回；④generic_component/
  function_effect 是附加改写字段，保持简短、不得顶替挤占主概念字段。
  第一版带「例如浆料专利的隔膜」反例反而锚定模型，已去除反例。
- 验证：电池案连跑 5/5 通过（修复前 3/7）；成功画像中隔膜进入
  subject_synonyms 层级、common_context 三项均绑定真实特征、发明点正常
  携带 generic_component/function_effect。其余四案（燃气灶、光引发剂、
  水枪、无线耳机）各跑 1 次全部通过。
- 测试：全量 `uv run pytest -q` **661 passed, 7 skipped**（无新增用例，
  纯 prompt 文案改动）。

## 2026-08-06 I2 首轮固定五组检索线（宪章 2.15 / SPEC 3.20）

- 首轮检索组合固定为五组检索线，每组至多 1 条、首轮至多 5 条：
  ① 申请人+客体（AN + TTL∨ABST；目标本身在原始响应冻结后本地排除）；
  ② 标题客体+说明书核心发明点（TTL + DESC）；③ 分类号+说明书核心发明点
  （IPC ≤2 个同组 + DESC）；④ 说明书客体+说明书核心发明点+说明书效果
  （全 DESC）；⑤ 标题关键词客体+说明书功能效果（TTL∨ABST 承载关键词 +
  DESC）。核心发明点一律取画像顺序首个（派生显示/摄像头概念前置），多
  发明点不复制分线；词池 text+画像同义词 ≤5，≤6 字原子化规则不变。
- contracts：QueryVariant 新增五枚举；SearchQuery 新增 `applicant_terms`、
  `excluded_publication`；QueryPlan 新增 `target_applicants`、
  `target_publication_number`（申请人/公开号事实首轮生成时冻结，模块4
  重编译复用）。
- analysis.py：`_fixed_lane_patent_expression` 按组序容错映射生成
  AN/TTL/ABST/DESC/IPC 前缀（≤6 字原子化会把长概念拆成多组，不做精确
  组数硬匹配）；`_deterministic_initial_query_candidates` 首轮只编译五组
  （①申请人去重后恰 1 个才发射，无公开号仍发射并留痕；④效果
  池优先画像 function_effect 组、回退目标专利确定性效果词；⑤必须画像
  function_effect 组）；旧多分辨率首轮编译代码保留但不在首轮 requested
  集合中（死代码，刻意保留）；旧变体的无条件缺失 warning 加 requested
  守卫；首轮组合留痕循环跳过编译器已留痕的变体，不再重复追加占位警告。
- plan_queries / 模块四 / 同案重编译：模型返回的首轮查询一律不采用
  （留"未采用"warning），首轮不再有 NPL 线、不再注入目标引证回查线与
  目标效果召回线（两函数保留供 gap/后续轮次）；模块三首轮 prompt、gap
  prompt、模块四 prompt 同步为"系统确定性编译五组、queries 固定返回
  空数组[]"。
- 字段映射的当日初始假设：AN 申请人字段与供应商侧 `NOT PN` 尚待真实检索验证；该假设已被
  2026-08-07 的真实 P002 结果取代——AN 有效，`NOT PN` 返回 `68300004`，不得继续发送；
  ⑤“关键词”无独立字段，以 TTL+ABST 组合承载。
- 版本：`I2_RULE_VERSION=i2-query-plan-v3`、`I2_PROMPT_VERSION=
  i2-inventive-profile-v3`；I4S 的 v50/v27 不动。
- 测试：改写 20 个（旧首轮线/NPL 首轮/模型查询采用等前提已死的用例，
  部分改名以匹配新意图），新增 4 个（五组全形态精确断言、申请人缺失/
  多申请人跳过留痕、无公开号仍发射留痕、首轮只含五组+四类任二守门）；
  全量 `uv run pytest -q` **665 passed, 7 skipped**（基线 661）。

## 2026-08-07 I2 v3 固定五线收口与五案再冻结

- 修复最终供应商表达式的 OR 成员上限：主题、显式特征、画像词组和原子化后的候选均在
  进入表达式前统一收口到每组最多 5 项；裁剪时保留主锚点并优先保留首个跨语言对应词，
  防止中文候选先占满配额而丢失英文。
- 目标申请人事实新增通用 OCR 空格清理：仅合并中文字符之间的误识别空格，英文机构名称
  的正常空格保持不变。由此固定线①稳定生成唯一 `AN` 申请人；申请人缺失或不唯一时仍按
  宪章跳过并留痕。
- 真实 P002 小样本确认：`AN + TTL/ABST` 成功返回候选，追加 `NOT PN` 或 `NOT (PN:...)`
  均返回 `68300004`。因此供应商只接收正向申请人+客体表达式；`lab.py` 在供应商原始前 10
  与响应工件冻结之后，按规范化公开号主体排除目标专利及 A/B/U/S 变体，并在输出保存
  `excluded_target_documents`，不得把本地排除伪装成供应商过滤。
- 基于 2026-08-06 已冻结、未加载 oracle 的 I2 v3 画像，重新离线编译五案 9 个独立权利
  要求计划到不可变目录 `20260807-five-case-i2v3-r4`。审计结果为全部 OR 组不超过 5，
  申请人线均使用规范化名称并冻结 `excluded_publication` 本地排除元数据；事实不足的固定线
  没有补造。
- 固定五线的冻结契约强制写入 `compact_fallback_allowed=false`，防止执行器虽然禁用回退、
  审计 JSON 却仍遗留旧默认值。Portal 配套编译器按 `query_id` 原样校验并执行这些固定线；
  五案白名单已指向 r4，旧 r3、r2、`20260807-five-case-i2v3` 和
  `20260806-five-case-i2v3` 仅保留为历史审计工件。
- 验证：真实 P002 对 r4 电池浆料案完整申请人线一次调用成功并返回前 10；后端全量
  `667 passed, 7 skipped`，新增用例证明供应商原始响应先冻结、再本地排除同公开号主体的
  A/B/U/S 变体。Portal 类型检查、契约测试、定向 ESLint 和 12GB 堆生产构建均通过；隔离
  测试栈 5201/5209 已重启加载，两个 `/health` 均正常，正式 5109 未触碰。

## 2026-08-07 固定五线 Portal 加载与示例数量说明

- 后端固定五线规则和 r4 冻结数据本轮未改：每项独立权利要求只生成事实齐备的固定类别，
  总数最多 5 条；申请人、分类号或说明书效果等必要事实缺失时对应类别跳过，不得猜造补满。
  五案 r4 的 9 项独立权利要求因此分别有 2—5 条，均只属于固定五个 variant。
- 页面仍显示旧示例的根因是 3001 进程早于 8 月 7 日 Portal 构建。Portal 已重建重启，并删除
  模块五执行链在 I2 空结果时回退旧 `claim_context_recall` 的残余路径；不合规计划现在直接
  fail closed。隔离测试 5201/5209 和正式 5109 本轮未重启、未触碰。

## 2026-08-08 D1 区别特征循环、五轮上限与示例重生成入口

- 新增 `I2_GAP_QUERY_PLAN` 公共/实验室模块。I0 在每个 gap 轮创建 iteration 后先持久化该
  module run；输入冻结 D1 区别特征、上一轮失败原因和严格复用键，输出逐特征覆盖证据、
  `allowed_gap_feature_ids` 与是否继续搜索。缺少文献版本、内容 SHA-256、目标限制版本、日期
  资格 revision、I4-S run 或当前规则版本时一律禁止复用。
- D1 排序改为确认相同必需特征数优先；区别特征保存目标原文、结构角色/拓扑/运动/控制关系、
  技术作用与 D1 未披露依据。gap 编译器只接受未覆盖差异，不再混入完整权利要求或已覆盖特征。
  初始轮不计数，自动预算为最多 5 个 gap 轮；第五轮后保存 `gap_search_exhausted`，继续组合分析
  和 D1 + Top 5 大 Claim Chart。
- I4-C 持久化 `large_claim_chart_document_ids` 及 repository document IDs；ReportDataV1 新增
  `large_claim_charts`，按全部权利要求特征生成横向矩阵。新增/更新分析、工作流、实验室和报告
  契约测试；全量 `675 passed, 7 skipped`。
- 五案 r4 审计确认只重新编译了旧模块三画像。生成脚本已改为每项独权先运行当前
  `I2_INVENTIVE_PROFILE`，再把该 run 输出显式交给当前 `I2_QUERY_PLAN` 且 `max_queries=5`；
  外部 GLM 数据发送尚未获明确授权，因此 r5 未生成，不能把 r4 描述为真正全链路重跑结果。

## 2026-08-08 I2-G 查询生成与全差异复用路径完成

- 修复模块所有权缺口：`workflow._round_plan` 仅首轮创建 `I2_QUERY_PLAN`；第 2—6 轮统一创建
  `I2_GAP_QUERY_PLAN`，同一 run 内保存区别特征、严格版本复用决定、允许 feature IDs、实际
  QueryPlan 和模型响应审计，不再出现“I2-G 只审计、旧 I2 实际生成”的双 run。
- `InvalidityAnalysisEngine.plan_queries` 的 gap 输入新增已覆盖 IDs、既有语料复用证据、上一轮
  失败原因和旧查询表达式；这些字段写入 prompt、QueryPlan 和每条 SearchQuery。gap prompt
  明确禁止 full-claim 并行查询，查询混入已覆盖/非区别特征仍由既有 guard 拒绝；有失败原因时
  全部重复上一轮表达式会触发有界重生并最终 fail closed。
- 全部区别特征已被既有日期合格文献同角色/同作用覆盖时，生成零查询 I2-G 计划并跳过 provider，
  但继续走既有 comparisons、D1 复选、I4-I 和大 Claim Chart，组合证据不完整时再以 exhausted
  收口；不得在 I2-G 完成后提前 return。模块实验室 I2-G 同步输出实际 plan，fixture 保持离线。
- 回归新增“初始+五个 gap 轮没有旧 I2 run”和“既有语料全覆盖、零新增检索仍运行组合评价”
  端到端用例；`tests/test_analysis.py` 另验证失败原因/旧表达式进入 prompt 与元数据持久化。
  全量：`676 passed, 7 skipped`。隔离测试 PM2 已重载，5201/5209 `/health` 均为 test/ok；
  正式 5109 未触碰。

## 2026-08-08 gap 命中同轮停止与全量 fixture 回归修复

- 完成性审计发现：本轮新取得文献覆盖全部 D1 区别特征后，旧 I0 虽已运行 I4-I，却仍会多建
  下一轮零查询 I2-G 才停止。现于每个 gap 轮全部 I4-S 后，按当前 D1、区别特征和严格复用键
  同轮复核；全部覆盖时在本轮完成组合评价和大 Claim Chart 后立即停止。停止原因区分
  `new_gap_evidence_covered_all_differences` 与预检免搜的
  `existing_corpus_covered_all_differences`。
- 新增端到端用例证明第 2 轮新 D2 覆盖全部差异后只有两次 provider 调用、一个 I2-G run、
  不创建第 3 轮；五次均失败的用例进一步断言第 5 个 gap 轮实际运行 I4-I，且 D1+Top5 大表
  已写入 checkpoint。
- 全量回归入口清除旧三轮预算和 legacy I4-S fixture：统一为首轮后最多五个 gap 轮，gap 查询
  只绑定剩余差异，fixture comparison 绑定 `structural-disclosure-v50`。首次重跑暴露并修复旧
  fixture 的 `legacy-unversioned` 被当前输入守门正确拒绝问题；定向一案通过后，目录当前发现
  的 17 个 PDF 全量 fixture 回归 17/17 通过，run
  `fixture-20260808T084455Z-0aca0f1b`。这不是 live 真实检索完成证据。
- 后端全量在工作流修复后为 `677 passed, 7 skipped`；Portal `pnpm ts-check` 与无效契约脚本
  通过。隔离测试 5201/5209 已重载且 health 为 test/ok；正式 5109、Portal 3001 和 r4 白名单
  未触碰。r5 仍待用户明确授权向外部 GLM 发送五案正文/附图。

## 2026-08-08 r5 当前链路生成、live 超时与回归审计修复

- 获得用户明确外发授权后，`generate_decision_query_plans.py` 对五案目标 PDF 逐独权真实运行
  `I2_INVENTIVE_PROFILE -> I2_QUERY_PLAN`。脚本递归剥离 manual 输入禁止的
  `simulated/fixture/fixture_id/fixture_output` 运行标记，不改画像事实；新增默认 480 秒的
  `--run-timeout-seconds` 与严格按 case/claim/目标 SHA/oracle 标记复用的 `--resume`。
- r5 共 9 个独权，查询数依次为：电池浆料 1/4=`4/4`，燃气灶 1/10=`5/5`，光引发剂
  1/8/9=`3/3/5`，水枪 1=`3`，无线耳机 1=`4`。全部 `oracle_loaded=false`、来源为当前
  `module3_profile_live_model`，只含五种 fixed variant，无重复、无 compact fallback，OR 组
  最大 5 项。冻结目录为 `20260808-five-case-i2v3-r5`，raw plan 与 Portal 编译结果分目录保存。
- 完整 live I0 的 I2 曾在恰好 120 秒以 `total_deadline_exceeded` 失败。根因是
  `llm_i2_timeout_seconds=120`/attempt=105 的专用默认覆盖了通用 360/180 预算；现默认统一为
  360/180，并在配置审计和 `http_run.sh` 透传测试中覆盖两项 I2 专用变量。5209 单独重启生效，
  5201 和正式 5109 未动。
- `run_full_invalidity_regression.py` 的 live truth gate 原仍要求首轮同时执行 patent ordinary、
  patent conflicting 和 NPL，违反宪章 2.15 固定五线。现首轮只要求
  `full_claim_single_reference + patent/ordinary_prior_art`；后续 gap 轮只要求
  `gap_or_combination` 的普通专利、抵触申请和 NPL 三通道。新增测试证明首轮无需旧线、gap 缺
  任一通道仍 fail closed。
- live 重试此前复用同一 idempotency key，第二次 `/start` 409 且覆盖第一次报告结果；
  analysis session、investigation idempotency、continuation key 和报告名现全部带 attempt。
- 授权范围内实跑：水枪不可变报告经修正审计 15 项全通过；无线耳机先后 3 次执行均发出 5/5
  网络查询，但因 P012/OPS 波动只能部分取得 P020 PDF，缺确定日期资格或完整原文工件绑定而
  保持 `partial`。对 `CN215734747U`、`CN209748774U` 的独立 EPO 著录/原文探测分别返回
  `ConnectError`、`RemoteProtocolError`，未降低证据门槛。实例目录其余 15 件未获外发授权。
- 测试：生成器递归标记清理新增单测；配置、首轮/gap 审计和 retry 相关修改后全量
  `679 passed, 7 skipped`。

## 2026-08-08 十阶段示例后的 17 件实例清单与回归审计

- 实例目录现有 17 件专利 PDF、2 个辅助 XLSX。`sample-oracle.json` 补入此前漏列的
  `CN216086819U-化妆镜.pdf` 和 `CN217770321U.pdf`；manifest 的 `--check` 现将未登记专利
  PDF 视为失败，测试也要求目录 PDF 与 oracle 精确相等，防止新增实例被静默漏跑。
- 最新 manifest 为 19 个文件、17 件专利，缺失和意外文件均为 0；隔离模块一解析回归
  17/17 通过。全量 fixture run `fixture-20260808T172013Z-2ab095c1` 为 17/17 通过，但仍只
  证明真实目标解析和确定性合成合同，不代表 live 现有技术检索完成。
- 全量回归阶段账本补入 `I3-F`，fixture 会真实执行候选构建、分组、过滤决定和进入抓取队列的
  合同校验；live 报告只有出现持久化 `I3_CANDIDATE_FILTER` run 才记为该阶段已覆盖。新增顺序
  防回退测试，确保 I3-F 位于 I3-NPL 与 I3-E 之间。本修改不等同于把 I3-F 新接入完整 I0；
  I0 未持久化该模块时，live 完整性审计仍应如实失败。
- 本轮后端全量为 `680 passed, 7 skipped`。测试隔离服务 5201/5209 均在线且 health 正常。
  用户此前明确外发授权仅覆盖五案；未获得把其余实例正文、图片和派生检索词发送到测试 GLM、
  Patsnap、EPO 或 NPL provider 的授权，因此没有启动 17 件 live，也没有绕过权限审查。

## 2026-08-08 完整 I0 接入取文前 I3-F

- 修复完整 I0 此前由 `EvidenceGateway.search()` 一次性完成发现、取文和日期资格，导致
  `I3_CANDIDATE_FILTER` 只存在于律师模块实验室而无法真正位于取文前的问题。
  `ConfiguredEvidenceGateway` 现提供可独立调用的 `discover` / `retrieve`：I0 同一轮先完成
  全部查询并冻结供应商原始排序，再统一构建/分组候选、运行一次持久化 I3-F，最后仅对保留的
  唯一代表取文。兼容 `search()` 也组合这两个新阶段，不再走另一套行为。
- I0 与模块实验室共用同一个 GLM 保守语义筛选 prompt/解析器。强正向锚点、元数据不足、目标
  技术词重叠和模型失败继续确定性 fail-open；模型响应另存不可变审计工件。I3-F 输出保存原始
  候选、身份组、重复、排除、取文清单、合同/规则版本和输入/决定/总审计哈希。
- 新增双查询顺序回归：两次 discover 必须都发生在任何 retrieve 前；同申请 A1/A2 合并为
  一个代表，只发生一次非空 retrieve；持久化 I3-F 的 `prefetch_filter_applied=true`。真实
  arXiv adapter 测试也改为走 discover→retrieve，确认来源冻结和 I4-S 升级链未回归。
- live 真值审计不再只检查模块代码存在：每个 I3_FETCH 必须有同 iteration、成功、输入/输出
  SHA-256 完整且先完成的 I3_CANDIDATE_FILTER，否则 I3-F 覆盖和整案 live gate 均失败。
  fixture 汇总的 `live_completion_gate_passed` 现固定为 false，避免离线 17/17 被误读成 live。
- 后端全量 `682 passed, 7 skipped`；17 件最新 fixture run
  `fixture-20260808T174538Z-923fdf55` 为 17/17，所有阶段通过，live gate 如实为 false。
  隔离测试 5201/5209 已重载并返回 `environment=test/status=ok`；正式 5109 未触碰。其余 15 件
  的真实外部 live 仍待用户把既有五案外发授权扩展至全部 17 件。

## 2026-08-08 完整 I0 全页可读文档与 live 报告审计

- `ConfiguredEvidenceGateway` 对 PDF 取文统一调用 `normalize_pdf`，逐页嵌入文本/OCR并持久化
  `readable-patent-v1` JSON/Markdown；EPO bundle 优先结构化 description/claims，其他 provider
  结构化文本只有说明书/权利要求正文达到门槛才可 analysis-ready。`_record_text` 的题名/摘要
  fallback 仅保留 fixture/manual 兼容，真实文献不得再借此进入 I4-S。
- `_record_ready_for_single_reference` 对真实记录要求：本地不可变主原文、readable schema 与原文
  SHA 一致、正文至少 200 个非空白字符；PDF 还要求 `processed_page_count == page_count` 且
  `failed_pages=[]`。不满足时 I0 把“全文/OCR可读覆盖未完成”写入轮次和 claim provider failure。
- P020 冻结 PDF 首页新增原生文本/PSM11 OCR 的精确公开号与公开日核验；日期证据写入 provenance
  后继续由统一日期规则资格化，OCR 猜测或日期不一致不升级。
- `document_versions.version_metadata` 保存完整内部 readable 文档；ReportDataV1 只投影安全的
  `readable_document_audit`，不含全文和路径。live runner 以该摘要验证规则版本、源 SHA、全文
  哈希/字符数、全页覆盖，再把同一 version 绑定到来源、日期资格、I4-S disclosure。
- 修复报告白名单最初丢弃审计摘要后，授权耳机案 run
  `live-20260808T190623Z-016eee88` 首次尝试通过 live truth：5 个不可变 PDF 版本全部
  analysis-ready，页数 44/7/5/8/5、失败页均 0；2 个版本形成完整多模态 I4-S 链。后端全量
  `686 passed, 7 skipped`；测试栈 5201/5209 已重载健康，正式 5109 未触碰。

## 2026-08-08 Portal 十阶段映射（后端接口不变）

- 律师模块实验室把原七张卡片拆为十个可独立测试阶段；测试后端模块代码和持久化接口未改。
  新前端阶段六至十依次调用现有 `I4_S_SINGLE_REFERENCE`、`I4_C_CLOSEST_PRIOR_ART`、
  `I2_GAP_QUERY_PLAN`、`I4_I_INVENTIVE_STEP`、`I5_REPORT`。
- 阶段八作为一个可重复测试的有界 gap 业务阶段展示一至五轮，不把每个 gap 轮扩成新的顶层
  模块；同案前序事实仍由 repository overlay 和后端输入守门读取，客户端不构造替代事实。
- 本轮没有修改或重启 5201/5209，也未触碰正式 5109；Portal 类型、ESLint、契约和 production
  build 已通过。

## 2026-08-08 五案十阶段示例的冻结边界

- Portal 五案盲测区已从旧的模块3/4/6三个视图扩为十阶段。后端现有不可变冻结仍只直接提供
  r5 目标画像/检索式和 v50 source-only 单篇比对；目标清单、关键日和源文献 manifest 作为
  同一盲测事实层展示。
- 因五案尚无独立 I4-C、I2-G、I4-I、I5 冻结 run，Portal 后四阶段只能基于 v50 披露矩阵生成
  明示的复核预览，不得标称 live、不得虚构 gap 检索式、不得导入无效决定结论，也不得作为
  正式律师报告。若今后冻结这些阶段，应新增不可变版本并切换 `stageCoverage`，不能静默用
  派生预览覆盖正式结果。
- 本轮没有修改或重启测试后端 5201/5209，也未触碰正式 5109；只更新 Portal benchmark API、
  页面和契约测试。

## 2026-08-08 全页守门后的 fixture provider 兼容修复

- 全页可读守门上线后，17 件完整 fixture 首次重跑全部在 I3-E 后停止并返回 `partial`；原因是
  `_record_ready_for_single_reference` 只豁免 `fixture/manual`，遗漏回归器使用的明确模拟
  provider `fixture_simulation`。这不是专利解析失败，也不是 17 个案件分别失败。
- 新增 `_NON_LIVE_EVIDENCE_PROVIDERS = {fixture, fixture_simulation, manual}` 并在全文回退、
  本地主快照和 I4-S 输入选择处统一使用；真实 provider 的 `readable-patent-v1`、原文 SHA、
  全页处理和失败页为零要求不变。参数化单测锁定三个非 live provider 的合同兼容。
- 修复后 run `fixture-20260808T193424Z-99648101`：17/17 案、26 项独权全部可解释收口，逐案
  I1/I1.5/I2/I3-P/I3-NPL/I3-F/I3-E/I4-S/I4-C/I4-I/I5 无遗漏；manifest 检查为 17 PDF、
  2 XLSX、missing/unexpected 均 0；后端全量 `689 passed, 7 skipped`。运行无网络且 live gate
  保持 false；其余 15 件 live 仍须获得外发授权。

## 2026-08-08 17 件解析、I5 与实验室合同复验

- `run_parser_regression.py --fail-on-error` 当前再次完成 17/17：专利事实、关键日、分页正文、
  权利要求、说明书和附图物化/哈希检查无失败；输出
  `.data/invalidity/test/regression/parser/parser-20260808-current-r2.json`。
- 最新 fixture run 的 17 个 `report_path` 全部存在且唯一；报告均为正式 `ReportDataV1 v1`
  数据合同（非 preview、报告快照 ID/SHA 完整），合计 26 项独权均处于可解释终态，无 provider
  failure；证据仍明确为 synthetic fixture、无网络、不可用于法律结论。
- 模块实验室 `test_lab_modes.py + test_lab_source.py` 为 109 passed，Portal 十阶段合同、类型检查、
  定向 ESLint 通过；PM2 隔离合同 11/11。宿主只读检查 5201/5209 均为 test/ok，Portal 登录页
  200；未重启任何服务，正式 5109 未访问。其余 15 件 live 仍待外发授权。

## 2026-08-09 I4-O 后端落地

- 新增 `ObviousnessPrecheckAssessment` 及 I4-O 模块处理器，读取当前持久化 D1、区别特征、冻结
  全文和双方图片，逐组分析技术问题、D1 启示、惯用手段、修改动机/路径、反向教导和效果，
  引文须通过冻结全文回验。`d1_teaching_path_complete` 明确区分方向性启示与可独立成立的完整
  D1 改造路径。
- 确定性分流为跳过结构检索、仅补公知常识、补组合证据、直接特征检索或人工复核；I2-G live
  必须消费同案最新成功 I4-O，只编译未解决目标。I4-I 支持 D1+公知常识/惯用替换，但 I0 不会
  在 I4-O 仍要求直接或补证检索时提前运行 I4-I。
- `lab_unit_check.py` 的模块清单已加入 I4-O。分析、实验室与工作流定向回归共 `349 passed`；
  隔离 5201/5209 已重载健康。耳机案 live I4-O run
  `07d88eb8-1b02-41e8-8c6c-5fa14ca58161` 成功并关闭普通结构检索；正式 5109 未触碰。

## 2026-08-09 固定五组 I3 冻结计划校验

- `SearchQuery.trusted_fixed_plan` 只允许后端内部构造，mapping/HTTP 输入不能设置。live 专利
  文本检索携带计划 lineage 时，先从 repository 精确回读同案同独权成功 `I2_QUERY_PLAN`，
  核对 plan run、query id、固定 variant、日期通道、检索目标和冻结 provider expression；
  通过后跳过旧通用语义重解释，但仍执行非空、规范化、身份和计划一致性守门。
- 计划 `3fde0fac-858a-4c16-ae3c-ca08554ad921` 五条真实 Patsnap run 全部成功且真实联网，输出均
  保存 `frozen_plan_query_verified=true`；五式合法零命中。定向测试 `110 passed`；全量唯一
  失败来自实例目录新增三个未登记 PDF，非本修改回归。隔离 5201/5209 已重载，正式 5109 未触碰。

## 2026-08-09 固定五线 v4 核心含义词扩展

- 用户确认固定五条规则不变，但零命中表明每组原词过窄。I2 v4 新增可审计的
  `subject_core_terms_zh/en` 与逐概念/功能词组 `core_meaning_terms_zh/en`；具体词、忠实同义词、
  核心含义或同类合理上位词在原语义组内 OR，并继续受每组最多 5 个成员约束，不新增检索线。
- 兼容旧画像的确定性升级只做通用语言归纳：去除序号、位置/环境修饰和构件载体后缀，从目标词
  自身恢复短核心动作或基础类别，例如“第一供水管”保留“供水管/供水”、“水中吸水结构”保留
  “吸水结构/吸水”；没有案件公开号或对比文件词表。裸“装置/结构/部件/系统”等继续拒绝。
- 修复长具体词处在显式 OR 组时仍被拆成多个强制 AND 的收窄：仅当同组存在该长词的字面核心
  短词时，拆出的原子仍留在同一 OR 组；其他没有核心短词依据的既有长词维持原子 AND 规则。
  `I2_RULE_VERSION=i2-query-plan-v4`、`I2_PROMPT_VERSION=i2-inventive-profile-v4`；分析定向测试
  `199 passed`。最终水陆喷水车 live 计划为 `4d155834-30b3-4286-a529-fa2eacc902b0`，五条均
  成功冻结；真实 I3 run 为 `5073bf70-01e7-40e9-bf19-d97ac4a046a6`（同申请人线 0 条）、
  `e1d04e86-1069-40d6-8656-0806e3ebae8f`、`712e8d80-4da1-4af5-a412-01f49e3735d2`、
  `08a688d1-fa06-4bd8-8299-a75795f1e2e5`、`32ed0c0f-dc56-4d98-8408-3f71d18b993b`（后四线各
  返回前 10 条）；全部 `patsnap/network_used/frozen_plan_query_verified=true`。全量回归
  `701 passed, 7 skipped, 1 failed`，唯一失败仍是实例目录 3 份新增 PDF 未登记 oracle。
  隔离 5201/5209 已重载健康，正式 5109 未触碰。

## 2026-08-09 5209 受控并行与模块六历史运行恢复

- `Settings` 新增 `worker_concurrency`，读取 `INVALIDITY_WORKER_CONCURRENCY`，默认 2、硬上限
  4；PM2 隔离 allowlist、环境 bootstrap 和 `http_run.sh` 已同步。超范围配置 fail closed。
- `create_app` 在未注入测试 worker 时创建对应数量的 `DurableWorker`，共享 stop event，但每个
  worker 有独立 ID、线程、心跳和活动租约；PostgreSQL `FOR UPDATE SKIP LOCKED`/CAS 保证同一
  job 不会被两个 worker 同时领取。关闭时只遍历实际启动的线程，并逐 worker 释放活动租约。
- `/v1/lab/investigations/{id}/module5-inputs` 以同一 claim 最新 I3_FETCH 共享的
  `candidate_filter_run_id` 和严格 analysis-ready 正文作为模块六恢复清单；没有筛选血缘的旧数据
  才退回时间连续性。清单内返回最新 I4-S 的 `document_id/module_run_id/status/created_at` 映射，
  已有 run 由 Portal 收编复用，缺失文献补建，避免按时间误并批次或旧页面提前退出造成漏文献。
- 定向配置/API/worker/数据库回归为 `86 passed, 7 skipped`，并发数量与历史映射专项为
  `3 passed`。正式 5109 未修改。
- 案件 `033f35cc-4229-501e-82fc-1fc0fb53e605` 的最新 I3 血缘验收为 31 篇全文、原有 28 篇
  成功 I4-S、3 篇遗漏。3 篇 live 补建全部 succeeded 且各有 5 项 disclosure；双 worker 先同时
  启动两篇，第一篇结束后第三篇立即接棒，墙钟约 160 秒、串行时长合计约 296 秒。最终恢复接口
  映射 31/31 成功；5209 health=`worker_concurrency:2`，正式 5109 未触碰。

## 2026-08-09 模块九人工复核状态修复

- 运行 `65431bb0-6388-4f25-8db6-79ed61c46aab` 的 D1 实际已由模块七选择并由模块八继承；错误
  来自 I2-G 把 `human_review` 特征从未覆盖集合删除，随后误写停止原因为
  `all_differences_covered`，且顶层没有冻结 `closest_document_id`。
- 新增统一分流函数：`covered_difference_feature_ids` 只保存真正关闭的特征，全部未解决特征
  继续留在 `uncovered_difference_feature_ids`，可自动检索子集与律师复核子集分别写入
  `allowed_gap_feature_ids`、`human_review_feature_ids`。只有后者时零查询且停止原因为
  `human_review_required`；I0 与 module-lab 共用同一语义。
- 工作流规范升至 3.32。定向回归 `33 passed`，后端全量 `704 passed, 7 skipped, 1 failed`；
  唯一失败仍是实例目录新增 3 份 PDF 未登记 oracle。隔离 5201/5209 已重载，正式 5109 未触碰。
- 同案 live 重跑 `de161249-faba-466a-bd39-879c95993cd7` succeeded：D1 为
  `ccb50dd7-4276-49df-bcf5-d92fd736e3e5`，0 项覆盖、3 项未解决且全部转律师复核、0 项允许
  自动检索、0 条查询，`stop_reason=human_review_required`。

## 2026-08-09 模块九 gap 守门恢复与宽检索线

- 失败 run `3777d654-31c4-4ae5-ac29-2c21697d0de6` 的两次 GLM 响应都把
  `I2_GAP_QUERY_PLAN` 写成 `purpose=initial`、`full_claim_single_reference` 的首轮组合；守门
  正确拒绝了 5 条查询，但旧代码在确定性 gap 矩阵执行前即抛错，页面因而显示无输出。
- gap 模型收到一次守门反馈后仍无合格查询时，系统现在丢弃全部不合规提案，仅使用模块三画像、
  I4-O 冻结的 `allowed_gap_feature_ids` 和补证类型确定性恢复；每个允许区别特征分别生成一条短
  检索线，以一个核心含义锚点和同概念 OR 同义/上位词检索，不再把多个复合限定全文拆成十余个
  AND 条件。随后在五条预算内补齐专利普通、NPL 普通和中国抵触申请通道，并保留两次模型审计、
  拒绝理由和恢复警告。权利要求残词 `且`/`部分` 不进入最终 provider 表达式。
- 工作流规范升至 3.33。核心回归 `354 passed`；后端全量 `705 passed, 7 skipped, 1 failed`，唯一
  失败仍是实例目录新增 3 份 PDF 未登记 oracle。隔离 5201/5209 已重载健康，正式 5109 未触碰。
- 最终同案 live run `de5a9f48-dcec-471e-9c1b-7f34a664fd7e` succeeded（首次作业尝试遇到
  `GLM_TEMPORARY_UNAVAILABLE`，durable job 第 2 次自动成功）。D1 仍为
  `ccb50dd7-4276-49df-bcf5-d92fd736e3e5`；0 覆盖、3 未解决、2 允许自动检索、1 律师复核，形成
  4 条 gap 查询。两条普通专利主线分别为“水陆喷水车上位词组 AND 供水管/供水词组”和
  “水陆喷水车上位词组 AND 炮塔壳内/炮塔壳词组”，另补 1 条 NPL 与 1 条抵触申请通道。

## 2026-08-09 模块九 gap 计划真实执行契约

- 用户指出 run `adf2b7a6-8655-4ac0-8ed1-810e83ce01b6` 虽已生成 4 条 gap 查询，但页面只展示
  `existing_corpus_reuse` 的“缺少可复用版本键”，没有继续检索和比对。复核确认该文案只表示
  旧 I4-S 缺少完整缓存身份、不得直接计为覆盖；正确后果是保留未覆盖并继续补检，不是停止。
- logical `I2_GAP_QUERY_PLAN` 仍只负责计划，不进程内调用下一模块；律师业务模块九由 Portal
  顺序编排 `I2-G -> I3 专利/NPL -> I3-F -> I3-E -> I3-QUALIFY -> 新文献 I4-S`。测试后端现
  允许 I3 按同案同独权成功 I2-G 的 `plan run + query_id + provider_expression` 精确回读执行；
  普通现有技术按公开日截止，CN 抵触申请只按申请日截止，避免错误排除关键日后公开文件。
- I3-F 可显式绑定 I2-G 画像，固定首轮 I2 行为保持不变。相关专项回归覆盖普通/抵触申请日期
  过滤、gap 原式校验和 I3-F gap plan lineage；工作流规范升至 3.34。正式 5109 未触碰。
- 同案 live 回归：4/4 条查询全部成功（两条普通专利各 10 条、NPL 6 条、抵触申请 10 条），
  I3-F 从 36 条原始命中保留 15 个唯一候选；15/15 取得 analysis-ready 全文、日期核验完成，
  15/15 新 I4-S 均 succeeded 且各返回 5 项 disclosure，无单篇完整披露。全量后端为
  `708 passed, 7 skipped, 1 failed`，唯一失败仍是实例目录 3 份新增 PDF 未登记 oracle。

## 2026-08-09 I2-G 多轮逐特征查询与单调 gap 游标

- `_ensure_query_matrix` 在 gap 轮先为每个 `required_gap_id` 保留一条 ordinary-patent 主线，查询
  预算不再用首轮五线规则截断第六个以后区别特征；去重键包含 `gap_feature_ids`，相同表达式服务
  不同特征时不会互相吞掉最低覆盖。
- `previous_query_expressions` 现在同时约束模型原始候选与确定性补齐。模型直接复用上一轮普通
  专利主式时先进入裁减审计，再改用新的客体层、特征核心词或字段组合；最终守门逐条拒绝任何
  仍与上一轮相同的逐特征主线，而不再只检查“是否全部重复”。
- `common_context_features` 是辅助召回提示；绑定不存在/过期 feature id 的项被确定性删除，真实
  inventive-point 绑定仍严格 fail closed。`I2_GAP_QUERY_PLAN` 新接受持久化
  `gap_feature_ids`，与重新计算的允许集合取交集，确保多轮 active gap 集合只能收缩不能扩张。
- `/module9-cursor` 返回 `active_gap_feature_ids`，兼容旧单轮页面迁移。新增回归覆盖 6 个区别特征
  仍保留 6 条主线、模型原式跨轮重复替换、辅助画像坏 ID 不阻断、请求子集不得重新打开 F1，及
  cursor 恢复 active 集合。
- 最终全量 `714 passed, 7 skipped, 1 failed`；唯一失败为外部实例目录新增
  `稳定性弱-CN221412222U.pdf`、`稳定性弱-CN222140203U.pdf`、
  `稳定性弱-CN222366795U.pdf` 未登记 `fixtures/sample-oracle.json`，未修改外部样例或 oracle。
  测试 5209 已加载新代码并在 live 回归期间按允许上限运行 4 worker；正式 5109 未触碰。
- I2-G 新增 `deterministic_gap_fallback`：GLM HTTP 400/传输失败时不消耗有效 gap 轮，直接从冻结
  limitations、I4-O 路由、D1、上一轮表达式编译逐特征主线。上一轮每个特征的语义锚点用于下一轮
  改词，防止复合 limitation 退化到附带名词；原子化去重键加入 `gap_feature_ids`，同词服务不同
  区别特征时不得互相删除。I3-F 对此类 I2-G 允许用技术主题+冻结 limitations 构造筛选上下文。
- live 批次最终第 4/5 轮均完成真实检索并进入终轮复核，最终状态 `completed`。全量回归更新为
  `718 passed, 7 skipped, 1 failed`，唯一失败仍是上述 3 份外部 PDF 的 oracle 差异；5209 已加载
  最终代码并保持 4 worker，正式 5109 未触碰。

## 2026-08-10 I2-G v5 逐区别特征双语查询编译

- gap 普通专利主查询不再保留模型原式：模型原输出只用作词汇候选，确定性层按每项未解决
  feature 重编译为一条 `(object OR...) AND (feature OR...)`。主线数必须等于未解决特征数，
  每条只绑定一个 feature，两个 OR 组都必须中英文并存，客体组排除目标完全相同类别。
- 新增通用客体词头及英文上位词恢复、特征词组变体、双语守门和原子语义 key。跨轮按 OR 成员
  集合比较，仅调整顺序会被视为重复；后续轮使用实际不同的双语成员子集/新词组，最多五轮。
- 兼容旧 limitation：`lab.py` 在当前输入没有双语 `invention_search_profile` 时，恢复同案同独权
  最新成功 I2 且 concept feature IDs 与当前 limitations 相交的画像；仍缺词时只发起词汇补全
  LLM 调用。`I2_RULE_VERSION=i2-query-plan-v5`，`I2_PROMPT_VERSION=i2-inventive-profile-v5`。
- 水陆喷水车 live run `1992876e-dfbb-41a6-bbab-827d081285bd` 对 5 项特征产生 5 组合规查询；
  第二轮 run `65333baa-07d6-41b1-a827-d0344e68abfb` 对 2 项特征产生 2 组，两组词集均实质变化。
  定向回归 `9 passed, 200 deselected`，lab gap 回归 `2 passed, 104 deselected`；全量
  `720 passed, 7 skipped, 1 failed`，唯一失败为外部样例新增 3 份 PDF 未登记 oracle。
  5209 已重载并完成 live 验收，正式 5109 未触碰。

## 2026-08-11 Portal 模块九单轮持久化边界

- 模块九批次控制器对每轮继续使用独立的 I2-G、I3、I3-F、I3-E/资格和 I4-S run；后端模块
  契约不递归调用下一轮。Portal 批次完成一轮后写 `awaiting_next_round`，不得自动消费下一轮。
- I4-S 部分失败写 `round_partial`；重试只创建失败 document 的新 I4-S run，既有成功 run 和
  provider 工作保持不变。旧尝试 ID 保存在批次 JSON 状态中供技术审计，当前 run 单独标记。
- 本轮没有改动或重启 5201/5209，也未触碰正式 5109；前端控制器与合同测试承担本次变更验证。

## 2026-08-12 模块九旧游标迁移边界

- 5209 `/module9-cursor` 继续只提供旧浏览器单轮流程向首个 durable 批次的兼容迁移；Portal
  创建同案后续 durable 批次前先检查批次表，已有任何 durable 批次时新批次从第 1 轮开始，
  禁止把上一 durable 批次创建的 I2-G 再解释为旧游标。
- 本轮后端 5209 代码未修改；修复位于 Portal 批次控制器和展示层，正式 5109 未触碰。

## 2026-08-12 模块三 JSON 截断修复

- 消火栓案件模块三连续 run `2f4c1bac-0a1e-4b95-8bbc-dcdd3c89cade`、
  `a5a39b01-3c2e-4f27-b581-6bc667068d0f` 均在约 1.2 万字符处产生未闭合 JSON；根因是
  `I2_INVENTIVE_PROFILE` 与短首轮计划共用 4096 token 输出上限，而不是案件输入或网络故障。
- profile-only 输出预算升至 12288，普通首轮 I2 升至 8192；长度截断/非法 JSON 在 I2 内携带
  压缩及闭合反馈重生一次。LLM 客户端在解析前保留 `finish_reason`、原始内容、响应字符数、模型
  和预算，失败也可由实验室冻结；错误码区分 `GLM_OUTPUT_TRUNCATED`、
  `GLM_OUTPUT_INVALID_JSON` 与真实传输故障。
- 定向回归为 analysis/LLM `233 passed`、module lab `106 passed`。测试栈 5201/5209 已重载；
  同案 live run `6e38f613-16fd-4dcc-a96e-03265e70f5d9` 一次 succeeded，
  `finish_reason=stop`、响应 10886 字符、预算 12288，并冻结 139029-byte 模型审计工件。
  正式 5109 未修改、未重启、未 promotion。

## 2026-08-12 I2 专利撰写外壳清洗

- 宪章升至 2.27、工作流规范升至 3.40；`I2_RULE_VERSION` 与 `I2_PROMPT_VERSION` 均升至 v6。
- 发明画像继续保留标题/权利要求完整原文供审计，但可执行关键词会通用剥离“具有/具备/带有/
  设有/设置有/采用 + 技术含义 + 结构/装置/机构/系统/组件/部件/单元”等撰写外壳。例如
  “具有升降结构”编译为绑定原 feature 的“升降”，不得因为短于 6 字整体放行。
- 客体与特征严格分组：“具有升降结构的室外消火栓”以“室外消火栓”为客体；模型误给的
  “具有升降结构”从 `subject_synonyms` 和固定五线客体 OR 组删除，核心词“升降”只能来自并
  留在相应特征组。实现不含消火栓/升降案件词表，覆盖模型画像解析、固定五线、表达式原子化、
  模块四画像复核和离线重编译。
- 定向回归 `5 passed, 207 deselected`，`tests/test_analysis.py` 全量 `212 passed`；模块自身
  `.venv` 全量为 `724 passed, 7 skipped, 1 failed`，唯一失败仍是实例目录新增三份 PDF 未登记
  oracle，与本次规则无关。测试 5201/5209 已重启在线；当前消火栓案件模块四 live run
  `49c2a9f6-f727-4c41-89cb-83852ba26a6c` 成功，五条 `provider_expression` 均移除“具有升降
  结构”，客体组保留“室外消火栓/消火栓/消防栓/消防设施/fire hydrant”，特征组保留“升降”；
  普通输出与画像 `subject_synonyms_zh` 也已同步移除错误别名。正式 5109 未触碰。

## 2026-08-13 回退到全案例自测 goal 前

- 用户要求撤销 2026-08-12 10:13:12 PDT 创建的“全部实例专利持续自测”goal 之后的改动；
  测试版后端、回归 runner、Portal 模块实验室接入及隔离 PM2 相关代码已恢复到该时刻之前。
- 覆盖前快照保存在根目录
  `.data/rollback-backups/pre-goal-20260812T101312PDT-current-20260813T1920PDT/`；调查数据库和
  `.data` live/fixture 历史工件保留，未作破坏性删除，正式 5109 未触碰。
- 回退全量基线为 `724 passed, 7 skipped, 1 failed`；唯一失败是旧 oracle 不包含后来新增的
  三份实例 PDF，和目标版本当时的已知记录一致。
- 确认测试库 queued/leased job 为 0 后，5201/5209 已用回退代码重启并通过 `/health`；旧版
  `control.sh` 依赖外部 PATH 提供 `uv`，本次只在启动命令中补入 `~/.local/bin`，未改变旧版文件。
  Portal production build 成功并只重启 `patent-web`，模块实验室匿名访问返回预期登录重定向。

## 2026-08-14 模块三检索级核心发明点归并

- `features` 与 `mechanism_model` 继续保存可逐项比对的全部底层结构事实；模块三普通视图中的
  `inventive_point_features` 改为 1—3 项检索级核心发明点，不能再逐条复述构件、导向、限位、
  驱动和连接细节。
- 候选核心发明点只有 1—2 项时保持原数量；不超过 3 项且技术目的/作用链彼此独立时分别保留；
  超过 3 项时按共同技术目的或同一机构作用链语义归并，并以 feature ID 并集保留全部底层绑定。
  超过 3 项的模型输出触发一次带明确原因的重生，代码不得静默截断或自行编造上位概念。
- `I2_RULE_VERSION` 与 `I2_PROMPT_VERSION` 升至 v7。通用回归以“多个支承/导向/限位/驱动细节
  共同形成设备移动能力”为例，生产实现不包含消火栓案件词表。

## 2026-08-14 模块九案件重置与轮次选择

- Portal 模块九新增显式 fresh 第 1 轮入口；即使当前批次停在 `awaiting_next_round` 或
  `round_partial`，律师也可以选择重新从第 1 轮创建新批次，同时继续保留“开启下一轮”和失败
  文献定向重试入口。fresh 批次不覆盖历史，也不继承旧浏览器 module9 cursor。
- 切换目标专利时清除旧 `module6_batch/module9_batch` URL、恢复键、内存批次和轮询 token；恢复
  最新批次按 investigation + claim 精确查询，前后端均拒绝跨案件或跨独权批次。
- 模块九仍以当前案件模块七 I4-C 和模块八 I4-O 为前置事实；重置仅重置补证批次，不允许从原始
  专利直接跳过 D1/区别特征冻结与显而易见性预分析。

## 2026-08-15 I2-G v8 五轮固定语义策略

- `I2_PROMPT_VERSION=i2-inventive-profile-v8`、`I2_RULE_VERSION=i2-query-plan-v8`。五个 gap
  轮分别绑定 `adjacent_object_direct_structure`、`broader_object_structural_family`、
  `same_function_object_action_role`、`subsystem_component_relation_path`、
  `analogous_domain_principle_effect`，不得由模型自行选择轮次策略。
- 画像词族按直接结构、结构族、动作/功能/技术角色、关系/路径、原理/效果分开冻结；轮次编译只
  读取对应词族。第 5 轮类比领域客体必须来自冻结画像或本轮模型建议，缺失时 fail closed，代码
  不维护案件特定类比词表。fallback 同样遵守当前轮词族，不再复用上一轮 expression anchor。
- QueryPlan、SearchQuery、workflow I2-G input/output 和 lab 输出均冻结 strategy code/label；前端
  以持久化字段显示当前轮。分析/workflow/lab 定向 375 项通过；全后端除旧 oracle 未登记新增 3 个
  PDF 的既有单项失败外为 728 passed、7 skipped。本次未运行 provider 或 live 案件。

## 2026-08-15 I2-G v9 当前轮专用词表与逐组换族守门

- `I2_PROMPT_VERSION=i2-inventive-profile-v9`、`I2_RULE_VERSION=i2-query-plan-v9`。gap 轮不再
  重生整套权利要求画像和大查询 schema，而只消费首轮冻结画像，调用紧凑的当前轮词表任务：
  一组双语客体类别词，以及每个未覆盖区别特征恰好一组双语特征词；系统据此确定性编译。
- 当前轮 live 词表会替换而非扩充对应策略词池，防止旧轮词汇混入。跨轮同时比较客体 OR 组与
  对应 feature 的特征 OR 组；整组大半成员仍属同一词族时拒绝，删成员、调顺序、仅改译法或
  替换检索外壳不得算新轮次。模型不可用且冻结画像没有本轮专属客体词时 fail closed，不再用
  旧词池产生看似成功的重复查询。
- 5209 `/health` 暴露当前 I2 prompt/rule 版本，I0 的 I2/I2-G module run 也冻结同一版本；真实
  消火栓三项区别特征的离线五轮回归已加入分析测试。真实 GLM 五轮验证脚本为
  `scripts/verify_gap_query_ladder.py`，必须在用户对具体专利和测试 GLM 作明确外发授权后运行。

## 2026-08-15 模块九五轮计划矩阵

- durable 模块九控制器在 provider 检索前一次生成并冻结第 1—5 轮 I2-G；五轮共享矩阵 ID、版本
  和输入绑定摘要，每轮自身保留模型/确定性重试 run。跨轮客体组及逐 feature 特征组必须实质换族。
- 实际 provider 工作仍一次只执行一轮。前轮已取得可引用披露的 feature 以后不再发查询；重复
  文献只有内容版本、日期资格、矩阵输入和 I4-S 版本完全一致才复用旧 FETCH/QUALIFY/I4-S，
  新轮检索与候选命中血缘继续保留。
- `/health` 增加当前 I4-S prompt/rule，供新矩阵 fail closed 绑定。定向 API/lab 136 passed；
  全后端 729 passed、7 skipped、1 个既有 sample oracle 失败。5201/5209 已在无在途 job 后重载。

## 2026-08-15 I4-I 创造性三步法合同

- `InventiveStepAssessment` 新增 `considered_document_ids`、逐项
  `distinguishing_feature_analysis`、受控 `conclusion/conclusion_text`。每项区别特征分别冻结
  实际技术问题、补充公开文献、相同作用、技术启示、修改动机与路径、反向教导、效果可预期性、
  闭合状态和未解决原因；全局组合条件不能代替逐项闭合。
- I0 向 I4-I 传入当前 D1、D1 区别特征、最新 I4-O 和全部当前日期合格 I4-S 语料；analysis 层
  记录全部已审阅文献后，按有效增量选择 D1+最多5篇实际组合。模块九后轮文献不得在调用前被
  固定 D1+2 篇裁掉；多篇累计覆盖不得写成单篇新颖性公开。
- 新版本为 `I4I_PROMPT_VERSION=i4-i-three-step-v1`、
  `I4I_RULE_VERSION=inventive-step-three-step-v1`，I0 与 module-lab module run、health 和 live
  truth gate 均绑定当前版本。fixture runner 增加不伪造证据的 I4-O，单案 CN217770321U 离线
  I1—I5 通过；fixture 只验证编排/合同，不构成真实证据。

## 2026-08-15 I4-C 仅消费当前模块六批次

- module-lab 的 I4-C live 请求必须显式提供 `module6_batch_id`；缺失时返回
  `LAB_MODULE6_BATCH_REQUIRED`，不得回读当前工作流或历史实验室 I4-S 作为候选。
- 实验室批次字段统一为 `module6_batch_id`。I4-S/I4-C/I4-O/I4-I 的实验室 lineage 在请求绑定
  批次时只接受同批次 run；即使 feature ID 恰好相同，也必须逐项核对当前 limitation 的完整
  `feature_text`，禁止按 sequence 或“特征3/4”迁移披露状态。
- 新建 live I4-S 输出冻结目标 source SHA、claim/claim-investigation、展开权利要求 SHA、
  limitation-set SHA、文献版本/内容 SHA 和 I4-S prompt/rule。旧批次在没有该新 binding 时仍
  必须同时通过同 investigation/claim、精确批次和完整特征实质校验；任何拒绝原因进入
  `rejected_lab_comparisons`。
- 指定案件修复后 I4-C run `f78d5fa5-8d30-4729-abf1-d826794bf4b3` 只消费批次
  `1fccc90f-1319-433d-aff3-e93ec154d413` 的 12 个当前 I4-S；历史 `CN206949116U` 被排除，
  D1 为 `CN205987792U`。隔离 5201/5209 已加载本次代码，正式 5109 未触碰。

## 2026-08-15 模块五历史专利全文复用

- 模块五 `I3_FETCH` 在构造 EPO/P020 provider 前，先用带 kind code 的完整规范公开号
  查找同环境历史 live 成功取文。只有原始工件仍在当前环境工件根目录、
  现场重算的字节数/SHA-256 一致，且可读化 JSON 哈希、标准化版本、全页状态与
  `analysis_ready` 全部合格时才直接命中；A1/B2/U 不混用，不按标题或同族模糊命中。
- 命中当前可读化版本时完全跳过 provider 与 OCR；只有可读化版本过期时，从已验证
  冻结 PDF/EPO bundle 本地重建，仍不重复网络取文。号码、kind code、哈希、大小或工件
  完整性任一不合格则回到原 provider 链，不带病复用。
- 复用只限公开原文和可读化/OCR。本次 query/candidate/I3-F lineage 仍冻结，日期资格仍按
  当前案件重做；输出不携带历史 disclosure。模块六 I4-S 仍绑定当前目标 SHA、当前
  claim/limitation 集合和当前规则重新比对，不按特征序号复用历史结论。
- 新增精确公开号部分索引和 repository 查询合同。缓存定向 4 项、module-lab 111 项、
  数据库合同定向 2 项通过；全后端 736 passed、7 skipped，唯一失败为外部实例目录新增
  3 个 PDF 未登记旧 oracle，与本功能无关。确认测试 schema 无在途 job 后已重载
  隔离 5201/5209，5209 健康且数据库已建立 `module_runs_patent_fetch_cache_idx`；正式
  5109 未触碰。Portal production build 成功并只重启 `patent-web`。

## 2026-08-16 模块九零检索误报“全部区别特征已覆盖”诊断

- run `6410ce62-dc5e-40ec-8c13-2d7829311453` 前序 I4-O 明确把
  `1-f-9483db6cc604`（“蓄电池与水泵相连”）保留为未解决并路由直接特征检索；I2-G 却输出
  `covered=[该特征]`、`queries=[]`、`generation_source=existing_corpus_reuse`，模块九批次
  没有任何搜索、取文或新 I4-S 子运行便收口 completed。
- `_gap_query_plan_handler` 当前用 `_persisted_comparisons(claim)` 读取整个 checkpoint，未绑定
  当前模块六批次 `525a0592-39a2-417e-89e4-7f9be376136c`，从而静默混入历史
  `CN108029521A`、`CN206949116U`、`CN208650150U` 的 I4-S 结果。
- 同一路径用 `str(None)` 构造复用键，再以 `all(values.values())` 判断完整，使当前批次
  `CN111213574A` 缺少文献版本和日期资格 revision 时仍被当成完整键；其引用也不足以直接证明
  蓄电池—水泵连接。Portal 又没有把 covered 复用证据列表渲染出来，故页面只见完成文案而没有
  对比文件。本轮仅只读诊断，未修改代码、数据或服务。

## 2026-08-16 模块八至十单次业务运行谱系隔离

- 用户确认“不要串，一次就是一次，每次单独分析”。律师实验室模块八必须显式绑定当前已完成的
  `module6_batch_id` 与该批次的模块七 I4-C run；模块九再显式绑定同一模块六批次、模块七 run
  和模块八 I4-O run；模块十还必须绑定同一来源链及当前已完成/耗尽的模块九批次。后端不再从
  claim checkpoint 的全部历史 comparisons 中挑选可用项。
- 实验室 I4-S 只在其输出精确携带当前 `module6_batch_id` 或当前 `module9_batch_id` 时进入本次
  分析；I4-C/I4-O/I4-I 分别校验前序 run ID、模块代码、同案同独权和批次。历史工作流、其他
  模块六/九批次即使 feature ID 或显示序号相同，也不得静默混入；下游输出冻结所消费的批次和
  前序 run ID。
- `ExistingCorpusReuseKey` 现在拒绝空值以及字符串形式的 `None/null/undefined/n/a`。模块九零
  查询完成还必须逐区别特征给出当前模块六批次中的可引用引文、位置、同角色/同作用确认以及
  文献版本/内容 SHA、目标限制版本、日期 revision、当前 I4-S run/rule；任何一项缺失都保留为
  未覆盖并继续本轮检索，不能再显示“全部区别特征已覆盖”。
- 覆盖证据新增文献标题、公开号和来源模块六批次；Portal 普通视图以默认收起区展示逐项文献、
  引文、位置、理由和 I4-S run。旧模块九批次没有三项来源谱系时明确要求重新生成，不对旧结果
  做可信升级。
- 回归：`tests/test_analysis.py` 与 `tests/test_lab_modes.py` 合计 332 passed；后端全量仍只有
  已知 sample oracle 未登记目录新增 3 个 PDF 的 1 个失败。Portal `pnpm ts-check`、无效契约、
  定向 ESLint 和 production build 均通过。确认测试 schema 无在途任务后已重载 5201/5209；
  只重启 3001 的 `patent-web`，登录页 200、匿名模块实验室 307；正式 5109 未触碰。

## 2026-08-16 模块11三部分报告合同

- `ReportDataV1` 新增 `inventive_step_narratives` 与 `similarity_claim_charts`。前者只把当前组合行中
  已冻结的 I4-I 三步法字段确定性转写为决定书式人类可读段落，不调用模型，也不新增文献、特征
  对应或法律事实；后者逐独立权利要求按当前 I4-S 已确认披露特征数排序最多10篇文献，待确认不
  计入披露，再按确定性评价完整度和稳定文献标识排序。
- 相似度 Top 10 与 I4-I 内部的 D1 + 最多5篇组合增量表是两个不同用途：Top 10 回答“哪些单篇
  文献在技术特征层面最接近”，内部大表回答“D1 后哪些文献对区别特征提供最高组合增量”。二者
  均保留，但不得混称或相互替代。
- 模块实验室 live I5 的 `lab_analysis_appendix` 同步保存当前模块10的人类可读转写；正式报告
  继续从当前调查持久化组合生成。文献排序和逐篇表只消费同一当前案件/独权的披露记录；文献
  当前版本存在时排除旧版本披露。第三部分继续保留全部已分析文献，不受 Top 10 裁剪。
- 规范已升至 `WORKFLOW_SPEC.md` 3.49、宪章 2.31。定向 `test_report_contract.py` 新增11篇排序、
  待确认不计分、Top10截断及文字论述内容测试；后续发布验证结果记入本节续项。

### 2026-08-16 验证与测试环境发布

- `test_report_contract.py` 定向 5 项通过，报告合同与模块实验室合计 119 项通过；后端全量
  740 passed、7 skipped、1 failed，唯一失败是已知的外部样例目录新增三份 PDF 未登记旧
  sample oracle，不属于模块11。
- 无在途 durable job 后已重载隔离测试 5201/5209；健康检查均正常，正式 5109 未触碰。重载后
  单案 fixture I5 实际输出包含 1 份文字分析、1 张相似度矩阵、2 篇矩阵文献及 4 条全部文献逐
  特征披露记录，`network_used=false`。

## 2026-08-16 正式 provider 授权复制与 promotion 收口

- `bootstrap-local-isolated-env.mjs` 新增仅限 prod 的显式选项
  `--reuse-test-provider-credentials`。它只在用户授权后把测试配置中的智慧芽/EPO 值写入正式前缀，
  不输出密钥，不复用正式 API token、schema、上传/工件目录和 PM2_HOME，也不增加运行时回落。
- 初始轮之后的最大 gap 预算在 bootstrap、环境示例和 PM2 校验中统一为 5；配置守门接受
  1—5，拒绝 6 或更大值。
- 已晋级并激活正式 release `20260816T153757Z-3fbc5ba58c5d`。受控 macOS 工作区会恢复目录
  mode，promotion 现在同时设置禁止新增/删除的 ACL；PM2 守门检查当前用户的实际写权限、只读
  文件及完整 manifest。
- 验证：PM2 合同 11 passed；当前后端全量 740 passed、7 skipped，唯一失败仍是外部实例
  目录新增 3 份 PDF 未登记旧 sample oracle。本轮不跑 20 案，不创建任何 live 调查。

## 2026-08-16 唯一无效后端

- 用户明确 Agent 与十一模块实验室只是两种前端视图。本目录的 5209 模块链成为唯一业务后端；
  Agent 自动完整推进，实验室逐模块运行并展示输入输出。
- 5209、`invalidity_test` 和 `INVALIDITY_TEST_*` 暂作为历史标识兼容；Portal 新入口优先使用
  `INVALIDITY_API_*`。5109/`invalidity_prod` 退出新的用户请求链，不再承担“正式版”。
- 同一后端不表示跨用户复用：调查、上传收据、module run、报告和人工复核仍须按用户、session、
  investigation、版本哈希和资源 owner 严格隔离。
- Portal 已完成 production build 并只重载 `patent-web`；5209 本身未重启，运行健康检查返回 200。
  Agent 与实验室的差异仅为自动推进或逐模块观察，不再通过环境名选择不同服务。

## 2026-08-16 I2-G v10 exact 客体过滤

- 真实 Agent 调查的第 3 个迭代因 `消防设施` 被旧 `_gap_exact_subject_values` 当成“室外消火栓”
  完全相同类别而失败；服务和 worker 正常，终态是 `query_plan_failed`，不是 PM2/环境故障。
- `I2_RULE_VERSION=i2-query-plan-v10`。exact 集只保留目标实际客体及语义等同类别，画像中以设备、
  设施、器材、装置等宽类别结尾的 synonym 不再被吸收。模型客体组混有 exact 时只过滤 exact 并
  记录 `removed_exact_subject_terms`；过滤后仍双语则编译，缺任一语言仍 fail closed。
- 新增消火栓混合词表回归，断言“室外消火栓/outdoor fire hydrant”被移除而“消防设施/fire
  equipment”保留；`tests/test_analysis.py` 全文件 219 passed。确认 schema 无 queued/leased job 后
  只重载 5209；健康返回 `worker_concurrency=2`、`i2_rule_version=i2-query-plan-v10`，5201 继续正常。

## 2026-08-17 同一 Agent 调查的最终 gap 续跑

- 先取消 1 个陈旧模块六批次与 4 个陈旧模块九等待批次；只改变批次可继续标记，不删除调查、模块
  运行、证据或事件。清理前后均无其他 queued/leased/running job，当前 investigation 明确排除在外。
- investigation `be38f278-634f-5bfa-9c99-c267b65d95bd` 的总轮次 5 / 第 4 个 gap 轮因模型连续两次复用
  上一轮客体和特征词族而由 v10 守门正确拒绝；审计工件
  `4ca2d958-e94b-446c-9543-27cc8108ec4a` 保留完整两次响应，不修改守门或硬编码案件词表。
- 用户明确要求继续当前 Agent 正式任务后，新建 continuation
  `2cd05758-862f-4832-9922-e38336be726c`，只授权总轮次 6 / 第 5 个 gap 策略。I2-G 与 3 组搜索成功，
  3 组 I3 fetch/qualify 均按真实全文取得情况标记 partial，7 篇 I4-S 及 I4-C/I4-O/I4-I/I5 均 succeeded。
- durable job `0ef927f7-865b-480e-b245-9e98a72bbf46` 和 I0 module run 最终 succeeded；调查/claim 按
  `partial` 收口，理由为 provider/模型执行不完整，未覆盖区别特征继续保留。全过程 worker 心跳正常，
  无 PM2、5209、Postgres 或环境挂载中断。

## 2026-08-17 Agent 首轮 Patsnap 余额失败诊断

- session `analysis_1786955838458_jq2529` / investigation
  `36467f7a-0be0-528a-9ea6-1f6bfb0c03f9` 的 I0 job 一次 succeeded；事件流完整记录目标快照冻结、
  checkpoint、claim 日期上下文、I2、五条查询、partial 收口和 I5 快照，worker 未中断。
- `I2_QUERY_PLAN` 为真实 `live_model` 运行，形成 5 条固定查询；5 个 `I3_PATENT_SEARCH` 均
  `network_used=true/actual_provider=patsnap`，但全部被 `67200005` 拒绝。该码在本模块合同中是
  `PATSNAP_BALANCE_INSUFFICIENT`，表示余额不足、本次检索未执行完成且不得记为零命中。
- 因候选为 0，本案没有文献、全文版本、日期资格、I4 披露、D1、区别特征、组合或 Claim Chart；
  I4 module run 数为 0。I3-F 的 succeeded 仅是空候选集确定性收口；五组 FETCH/QUALIFY failed
  记录传播相同 search error，不代表真实取得/核验了文献。I5 只生成零证据报告快照。
- 调查为 initial provider failure，`gap_items/open_gap` 都是 0。Portal 倒计时误发的三次 continuation
  均由 repository open-gap 守门返回 409，continuation batch 数仍为 0。本轮只读诊断，未重启服务、
  未改数据或代码，也未重跑该调查。

## 2026-08-17 Patsnap P002 新 Key 恢复验收

- 用户更新 `.env.test.local` 后，权限/格式与完整 test stack 配置守门均通过；检查过程只输出配置状态
  和长度，不输出或记录密钥。重载前 `invalidity_test.jobs` 的在途任务数为 0。
- 使用官方隔离入口 `ops/pm2/test/start.sh` 重载 5201/5209；Patsnap Key 只注入 5209，5201 parser
  不接收 provider 凭据，历史 5109 未触碰。重载后 5209 health 为 test/ok、2 workers，5201 为 test/ok。
- 项目 smoke 以 `--skip-count --search-limit 1` 只执行 1 次 P002，返回 HTTP 200、`error_code=0`、
  `outcome=success` 和 1 条 lead；此前账号余额错误 `67200005` 已消失。本次没有调用 P001、P012、
  P018、P019、P020、P042、P060 或 P061，也没有重跑或改写旧失败调查。

## 2026-08-19 五轮错误隔离与 I4-I 降级输出

- `I2-G` 第 2 轮继续沿用兼容 code `broader_object_structural_family`，语义改为“优先有效上位、
  必要时合适下位产品类别 + 结构族”。模型必须在 `semantic_basis` 说明选择理由，计划 warning
  保存该理由；`I2_RULE_VERSION=i2-query-plan-v11`、`I2_PROMPT_VERSION=i2-inventive-profile-v10`。
- `RoundCheckpoint` 新增 `failure_records`。gap 规划失败时原 I2-G module run 仍为 failed，但 I0
  创建不可冒充成功检索的零查询 error shell，继续用已有语料执行 I4-C/I4-O/I4-I 并进入后续轮；
  provider/I4-S partial 同样不阻断当前已授权的后续轮。第五 gap 轮后按证据状态 partial/exhausted
  收口，完整保留错误与未解决 gap。
- `InventiveStepAssessment` 新增 `upstream_gap_search_errors`。I4-I 允许仅有持久化 D1 时运行，强制
  `combination_basis=unresolved`、`evidence_complete=false`；只使用真实已有文献，不虚构 D2/D3。
  模块实验室 live I4-I 接收 Portal 当前模块九批次的错误台账。
- 新增回归覆盖“取水设备无法有效上位时选择抽水机”和“两轮 I2-G 连续失败仍跑完首轮 + 五个
  gap 轮并进入 I4-I”。全量回归 754 passed、8 skipped；唯一失败是外部实例目录新增 3 份 PDF
  未登记旧 sample oracle。确认无 queued/leased/running job 后用 `ops/pm2/test/start.sh` 重载
  5201/5209；健康为 ok，5209 报告 I2 prompt/rule v10/v11、worker_concurrency=2。

## 2026-08-20 P020 英文首页日期自动核验

- session `analysis_1787217204568_yvb78w` 的 16 份候选中，仅
  `US20080245714A1` 与 `US20090145830A1` 的 document-level qualification 为
  `needs_human_review`。P020 冻结 PDF 首页 OCR 已读出 `Oct. 9, 2008` 和
  `Jun. 11, 2009`，公开号和 (43) 标记也命中；旧解析器只识别中文/数字日期，
  因而把已取得的真实首页证据误判为日期链未核验。
- `_front_page_dates` 现支持英文月份全称/缩写、月-日-年及日-月-年、OCR 空格/句点
  变体，并用真实日历日校验拒绝 `Feb. 31`。不降低原有证据门槛：仍要求冻结字节
  SHA-256、首页公开号、公开标记和期望日期同时精确匹配。
- 新增英文月份解析与 US OCR 全链回归；定向 7 passed。后端全量 756 passed、
  8 skipped、1 failed，唯一失败仍是外部实例目录 3 份新 PDF 未登记旧 sample oracle。
  确认无 queued/leased/running job 后用官方隔离脚本重载 5201/5209；两个健康端点均为 200。
- 旧 session 保留当时的 qualification、checkpoint 和首页核验审计，未做批量回写或自动续跑；
  新规则对后续新取得文献生效。
