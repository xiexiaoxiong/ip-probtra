# 步骤4异步检索 + 步骤5方案A补跑计划

## Summary

- 目标：把当前“步骤4超时即报错”的串行流程，改成“步骤4后台持续检索；到首个等待上限时，如果已有商品则先进入步骤5做首轮分析；等步骤4最终完成后，再按方案A对全部商品重跑一次步骤5并覆盖结果”。
- 关键产品决策：
  - 步骤4首个等待上限到达时，如果数据库里仍无商品：`继续等待`，不报错。
  - 前端需要`明确展示部分完成/后台补跑中`，而不是仅复用已有 completed 文案。
  - 步骤5采用方案A：步骤4最终完成后，对当前 session 的全部商品再次发起一次完整模块4比对，覆盖首轮结果。
- 第一版不做“只分析新增商品”的增量模块4，也不做跨多次 `claim_compare_run` 的结果合并；通过“首轮部分分析 + 终轮全量覆盖”实现正确性。

## Current State Analysis

### 编排层

- `IP-protral/src/app/api/analyze/route.ts`
  - 步骤4当前通过 `runModule3()` 同步等待模块3 HTTP 返回。
  - 超时/网络中断后，调用 `waitForSearchRunRecovery()` 最多额外轮询数据库 12 分钟；若仍无商品，则把步骤4和整个 session 标成 `error`。
  - 步骤5当前在步骤4结束后启动；没有“步骤4后台继续、步骤5先跑首轮、步骤4完成后二次补跑”的状态机。
  - 步骤6当前默认读取一次模块4响应或某个最新 `claim_compare_run` 的数据库结果，不是多轮比对合并模型。

### 模块3（商品检索）

- `IP-protral/src/lib/workflow-client.ts`
  - 已有 `runModule3()`，使用同步 `fetch` 调模块3 `/run`，超时为 20 分钟。
  - 还没有类似模块4的 `start/get status/cancel` 这一套模块3异步客户端封装。
- `3-search/src/main.py`
  - `/run` 已支持 `async_mode=true`，并可通过 `/result/{run_id}` 轮询任务结果。
  - 现有异步能力只解决“HTTP 不阻塞”；没有专门给编排层的高阶状态接口，但足够支持第一版计划。
- `3-search/src/graphs/nodes/save_results_node.py`
  - 商品和 `search_run` 在整个检索图结束后一次性写库。
  - 这意味着“首个等待上限时基于已有商品继续步骤5”目前通常做不到，因为数据库里往往还没有任何商品。

### 模块4（技术特征比对）

- `IP-protral/src/lib/workflow-client.ts`
  - 已有成熟的模块4异步启动、状态查询、取消接口，能作为模块3异步化的参考实现。
- `4-claim-chat/src/graphs/nodes/parse_and_fetch_node.py`
  - 模块4启动时会一次性读取当前 session 下全部 `search_products`。
  - 不支持“运行中自动感知后续新增商品”。
- `4-claim-chat/src/graphs/nodes/write_feishu_results_node.py`
  - 写库时会先删除当前 `claim_compare_run` 的旧明细，再写入本轮结果。
  - 这天然更适配“全量覆盖重跑”，不适合直接拼接多轮 partial 结果。

### 状态与前端

- `IP-protral/src/lib/types.ts`
  - `StepStatus` 当前只有 `pending | running | waiting_input | completed | error`。
  - 没有“部分完成/后台继续”的表达能力。
- `IP-protral/src/components/analysis-progress.tsx`
  - UI 只按上述五种状态渲染颜色、图标和文案。
- `IP-protral/src/hooks/use-analysis.ts`
  - 轮询在 session 状态为 `completed` 或 `error` 时停止。
  - 只要 session 保持 `running`，就可以承接本计划的多阶段状态推进。
- `IP-protral/src/app/api/analysis/[id]/route.ts`
  - 只是返回 `analysis-store` 中的 session 快照，不限制扩展新状态字段。

### 数据库基础

- `IP-protral/src/lib/db-init.ts`
  - 已有 `search_runs/search_products/claim_compare_runs/claim_compare_results` 基础表。
  - 当前 schema 没有记录“首轮部分分析是否已触发”“模块3异步 run_id”“步骤4首批商品阈值状态”的专用字段，第一版可以优先放在 `analysis_sessions.results` 中，减少迁移。

## Proposed Changes

### 1. 为编排层新增“步骤4异步 + 首轮/终轮”状态机

- 文件：`IP-protral/src/app/api/analyze/route.ts`
- 变更内容：
  - 把步骤4从同步 `runModule3()` 改为：
    - 提交模块3异步任务；
    - 轮询模块3任务状态与数据库 `search_runs/search_products` 快照；
    - 区分“首轮可开跑条件”和“步骤4最终完成条件”。
  - 新增编排常量，统一定义：
    - 步骤4首轮等待上限（沿用当前 20 分钟语义）；
    - 步骤4零商品继续等待的轮询间隔；
    - 步骤4最终完成后的步骤5补跑触发条件。
  - 编排状态设计：
    - `步骤4.running`：后台检索中，还未拿到足够商品启动首轮分析。
    - `步骤4.partial`：已拿到首批商品，首轮分析已启动，后台继续检索。
    - `步骤4.completed`：模块3最终完成，商品已稳定。
    - `步骤4.error`：仅在模块3终态失败且没有可用商品，或后续补偿失败时进入。
  - 步骤5编排设计：
    - 首轮：在步骤4达到“已有商品 > 0”时立即启动模块4。
    - 终轮：步骤4最终完成后，再启动一次模块4全量重跑，覆盖首轮结果。
  - 步骤6只在终轮模块4完成后执行，确保对用户最终结果是完整稳定的。
- 原因：
  - 这是整个需求的主控状态机。
  - 现有流程只支持严格串行，不支持“边检索边分析再补跑”。
- 具体实现决策：
  - 不新开独立调度服务；继续沿用当前 `setImmediate(() => executePipeline(...))` 的后台编排方式。
  - session 总状态在首轮分析完成后仍保持 `running`，直到终轮步骤5和步骤6完成才改为 `completed`。
  - 首轮结果可提前写入 `analysis_sessions.results` 供页面实时展示，但要在字段上标识为“部分结果”。

### 2. 给模块3补齐异步客户端封装

- 文件：`IP-protral/src/lib/workflow-client.ts`
- 变更内容：
  - 新增模块3异步接口：
    - `startModule3Async()`
    - `getModule3RunStatus()`
    - 可选 `cancelModule3Run()`（用于后续治理，第一版可以先保留接口设计但不在流程中强依赖）
  - 返回结构对齐现有模块4风格，至少包含：
    - `runId`
    - `status`：`queued | running | completed | error | cancelled | timeout`
    - `searchRunId?`
    - `totalProductsCount?`
    - `isComplete?`
    - `errorMessage?`
  - 调用方式：
    - 启动：POST `/run?async_mode=true`
    - 状态查询：GET `/result/{run_id}`
- 原因：
  - 编排层需要像操作模块4一样操作模块3，而不是继续用同步阻塞 HTTP。
- 具体实现决策：
  - 模块3 `/result/{run_id}` 返回 `processing/completed/failed/timeout/cancelled`，客户端统一映射到与模块4一致的内部状态枚举，减少编排分支复杂度。

### 3. 把模块3改成增量落库，支持“已有商品先分析”

- 文件：`3-search/src/graphs/nodes/save_results_node.py`
- 关联文件：`3-search/src/graphs/graph.py`、`3-search/src/graphs/state.py`、`3-search/src/graphs/nodes/coze_search_node.py`
- 变更内容：
  - 将当前“一次性创建 `search_run` + 一次性写所有商品”的模式，改为“单个异步任务内可分阶段更新同一个 `search_run`”。
  - 建议改造方向：
    - 在检索开始时先创建 `search_run`，`is_complete=false`。
    - 每次批量/逐关键词拿到新的商品后，去重写入 `search_products`。
    - 同步更新 `search_runs.total_products_count / successful_keywords_count / failed_keywords_count / error_message / updated_at`。
    - 全部检索完成后，将 `is_complete=true`。
  - 需要在状态模型中补充 `search_run_id` 贯穿全图，避免每次保存都新建 run。
- 原因：
  - 只有模块3能在检索过程中提前把首批商品落库，步骤4超时或到等待上限时才有“现有商品”可供步骤5使用。
- 具体实现决策：
  - 去重主键以 `(analysis_session_id, patent_record_id, product_url)` 为优先判定依据；若 URL 为空，则回退到规范化 `product_id + product_name`。
  - 第一版不改数据库唯一索引，先在保存节点做应用层去重和“已存在则跳过/更新”。
  - `search_run` 维持“一次模块3执行对应一条 run”，不在首轮/终轮之间拆多个 `search_run`。

### 4. 为 session 结果增加“部分结果/后台补跑”元数据

- 文件：`IP-protral/src/lib/types.ts`
- 关联文件：`IP-protral/src/lib/analysis-store.ts`
- 变更内容：
  - 扩展 `StepStatus`，新增 `partial`。
  - 扩展 `AnalysisResults`，新增字段：
    - `module3TaskStatus?`
    - `module3TaskStartedAt?`
    - `module3TaskFinishedAt?`
    - `module3TaskError?`
    - `partialProducts?` 或直接复用 `products` 并新增 `resultsCompleteness`
    - `resultsCompleteness?: 'partial' | 'final'`
    - `step5Phase?: 'initial' | 'rerun' | 'completed'`
    - `partialAnalysisAvailable?: boolean`
  - `analysis-store` 不需要表结构迁移，只需允许这些字段透传到 `results jsonb`。
- 原因：
  - 前端和轮询层要能区分“已有首轮部分结果”与“最终稳定结果”。
- 具体实现决策：
  - `StepStatus.partial` 只用于步骤4和步骤5。
  - session 顶层 `AnalysisStatus` 仍保持原四态，不新增 `partial` 顶层状态，避免全站影响过大。

### 5. 前端展示“部分完成/后台补跑中”

- 文件：`IP-protral/src/components/analysis-progress.tsx`
- 关联文件：`IP-protral/src/hooks/use-analysis.ts`
- 变更内容：
  - 为 `StepStatus.partial` 增加专门样式、图标和说明文案。
  - 步骤4文案：
    - `已检索到部分商品，后台继续检索中`
  - 步骤5文案：
    - `已完成首轮分析，等待检索完成后自动全量补跑`
  - 在 `use-analysis.ts` 中保持现有“仅 session completed/error 才停止轮询”的行为，不提前停止。
  - 如果 `results.resultsCompleteness === 'partial'`，允许页面先展示部分商品和部分比对结果，但要附醒目标记“结果仍在补全中”。
- 原因：
  - 用户已经明确要求前端显式表达 partial 状态，而不是仅靠隐式文案。
- 具体实现决策：
  - 第一版不新增复杂进度子面板；优先复用现有步骤卡片与结果页顶部提示即可。

### 6. 模块4按方案A实现“终轮全量覆盖重跑”

- 文件：`IP-protral/src/app/api/analyze/route.ts`
- 关联文件：`IP-protral/src/lib/workflow-client.ts`
- 变更内容：
  - 首轮步骤5：
    - 只要当前 `search_products` 有商品，就启动模块4异步 run；
    - 完成后把结果写入 `results`，标记为 partial。
  - 终轮步骤5：
    - 步骤4最终完成后，重新发起一次模块4异步 run；
    - 使用相同 session、相同 `patent_record_id`，读取此时数据库中的全部商品；
    - 终轮完成后，用终轮 `claim_compare_run_id` 和结果覆盖首轮展示结果。
  - 步骤6始终基于终轮 `claim_compare_run_id` 做最终汇总。
- 原因：
  - 当前模块4天然适合“整轮覆盖写入”，这是方案A风险最低的落地方式。
- 具体实现决策：
  - 首轮和终轮模块4使用不同 `run_id`、不同 `claim_compare_run_id`。
  - 页面和导出默认只认终轮 run；首轮 run 仅作为过程性可见数据。
  - 如果首轮成功、终轮失败：
    - session 维持 `error` 或 `completed with warning` 的产品决策本次未单独讨论；
    - 第一版按保守策略处理为 `error`，但保留首轮结果在 `results` 中，避免用户看不到已有分析。

### 7. 结果恢复逻辑收束到“优先终轮，必要时回退首轮”

- 文件：`IP-protral/src/app/api/analyze/route.ts`
- 变更内容：
  - 步骤6查询时优先使用终轮 `claimCompareRunId`。
  - 仅在终轮不存在、且 session 尚未结束时，才允许展示首轮 partial 结果。
  - 在 session 最终结束时，如果终轮失败且首轮存在，结果页仍展示首轮数据并给出“终轮补跑失败”的错误说明。
- 原因：
  - 避免步骤6在多轮 run 存在时读错 run。
- 具体实现决策：
  - `results` 内显式区分：
    - `initialClaimCompareRunId`
    - `finalClaimCompareRunId`
  - 现有 `claimCompareRunId` 最终指向 `finalClaimCompareRunId`，保证兼容已有消费方。

### 8. 数据库与兼容策略

- 文件：`IP-protral/src/lib/db-init.ts`
- 关联文件：`3-search/src/storage/database/shared/model.py`、`4-claim-chat/src/storage/database/shared/model.py`
- 变更内容：
  - 第一版尽量不改主表结构，把编排中间态放入 `analysis_sessions.results`。
  - 如实现增量落库需要稳定标记，可以考虑只补最小字段：
    - `search_runs.run_id`（可选）
    - 或新增索引辅助按 session + patent_record_id 查询最新 run
  - 只有当模块3内部确实需要稳定追踪单个异步任务时，再做最小迁移。
- 原因：
  - 先压缩 schema 变更范围，减少跨项目联调成本。
- 具体实现决策：
  - 计划实现时先尝试不迁移；
  - 若发现模块3增量落库无法稳定定位当前 run，再追加最小迁移，不扩大到前端无关字段。

## Assumptions & Decisions

- 已确认决策：
  - 步骤4首个等待上限时无商品：继续等待，不报错。
  - 前端明确展示 partial 状态。
  - 步骤5采用方案A：步骤4最终完成后，全量覆盖重跑模块4。
- 设计决策：
  - `StepStatus` 新增 `partial`，不新增顶层 `AnalysisStatus`。
  - session 只有在终轮步骤5和步骤6结束后才进入 `completed`。
  - 首轮步骤5结果允许用户可见，但必须标记为 partial。
  - 终轮结果覆盖首轮结果，不做多轮结果融合。
  - 模块3第一版通过已有 `/run?async_mode=true` + `/result/{run_id}` 接口接入，不额外给模块3新增新的 HTTP 路由。
- 风险与边界：
  - Next.js 进程内 `setImmediate` 背景任务如果被进程重启打断，仍有恢复一致性问题；本次计划不引入外部任务队列。
  - 模块3增量落库是本次实现成败的关键路径；若不落地，首轮分析几乎无法稳定启动。
  - 首轮成功、终轮失败的最终产品策略当前按“session 失败但保留首轮可见结果”执行。

## Verification Steps

### 代码级验证

- 类型检查：
  - `IP-protral`: `pnpm ts-check`
  - `IP-protral`: `pnpm lint`
- Python 侧：
  - `3-search` 启动后验证异步 run + result 轮询
  - `4-claim-chat` 启动后验证首轮/终轮两次模块4 run 均能落库

### 场景验证

- 场景1：步骤4在首个等待上限前已有商品
  - 预期：步骤4转 `partial`，步骤5启动首轮分析，session 保持 `running`
- 场景2：步骤4在首个等待上限时无商品
  - 预期：不报错，继续等待；步骤5不启动；前端显示后台检索中
- 场景3：步骤4后续补齐商品并最终完成
  - 预期：自动触发步骤5终轮全量补跑；步骤6读取终轮结果；session 最终 `completed`
- 场景4：首轮步骤5成功，终轮步骤5失败
  - 预期：session 最终进入 `error`，但页面仍能看到首轮 partial 结果和终轮失败提示
- 场景5：模块3最终失败且始终没有商品
  - 预期：步骤4 `error`，步骤5/6不启动，session `error`

### 数据一致性验证

- 检查 `search_runs`：
  - 同一 session 的 run 在检索过程中 `total_products_count` 递增，完成时 `is_complete=true`
- 检查 `search_products`：
  - 增量写入后无明显重复商品
- 检查 `claim_compare_runs`：
  - 首轮和终轮各生成独立 run
- 检查 `analysis_sessions.results`：
  - partial/final 元数据、模块3任务状态、首轮/终轮 run_id 均能被前端正确读取
