# 模块四相似度评分化改造计划

## Summary

- 目标：将模块四从当前的三态结论模型（`MATCH / NO_MATCH / UNCERTAIN`）改为以特征级 `0-100` 相似度分值为核心的定量模型。
- 评分原则：
  - 明确相同特征：`100`
  - 明确不相同特征：`0`
  - 待进一步确认特征：`1-99`
- 已确认产品决策：
  - 评分来源采用“混合方案”：LLM 输出建议分值，后端规则层做校准、截断与兜底。
  - 不再保留旧三态作为核心字段；接口与展示以分值为主。
  - 权利要求级总分：取该独立权利要求下所有特征分值的最小值。
  - 商品级总分：所有特征得分相加；任一特征明确不相同则商品总分为 0。
  - `1-99` 采用固定区间解释，并在前端/报告中展示可读标签。

## Current State Analysis

### 后端工作流现状

- `4-claim-chat/src/graphs/state.py`
  - `FeatureComparisonItem` 仍以 `comparison_result` 表示特征级结论，没有分数字段。
  - 各节点输入输出和 `all_comparison_results` 结构均默认三态模型。
- `4-claim-chat/src/graphs/nodes/analyze_features_node.py`
  - LLM 当前只返回 `evidence / reason / reasoning_type / evidence_images / claim_id`。
  - 尚未要求模型输出相似度建议分或分值依据。
- `4-claim-chat/config/analyze_features_llm_cfg.json`
  - System Prompt 明确禁止输出最终结论，但未定义“建议分值”字段、分值边界或打分口径。
- `4-claim-chat/src/graphs/nodes/apply_rules_node.py`
  - 通过 `reasoning_type + reason` 确定性映射出 `MATCH / NO_MATCH / UNCERTAIN`。
  - 该节点是当前最适合承接“LLM 建议分 + 规则校准”的位置。
- `4-claim-chat/src/graphs/nodes/compare_products_loop_node.py`
  - 异常兜底仍写入 `comparison_result = UNCERTAIN`。
- `4-claim-chat/src/graphs/nodes/write_feishu_results_node.py`
  - 汇总摘要按 `MATCH / NO_MATCH / UNCERTAIN` 计数。
  - 写库时只落 `comparison_result / reasoning_type / reason / evidence / evidence_images`。
- `4-claim-chat/src/storage/database/shared/model.py`
  - `claim_compare_results` 表只有 `comparison_result` 文本字段，没有 `similarity_score`、标签或聚合分数字段。

### 前端与接口现状

- `IP-protral/src/app/api/analyze/route.ts`
  - `mapComparisonsFromApi()` 与 `groupDbRowsByProduct()` 都按 `comparison_result` 解析。
  - `normalizeStatus()` 和 `determineVerdict()` 都是三态逻辑。
- `IP-protral/src/lib/types.ts`
  - `ClaimElementComparison.status`、`ProductComparison.overallVerdict` 都是枚举式分类，不支持数值评分。
  - `MATCH_CONFIG / VERDICT_CONFIG` 也基于三态或三类结论。
- `IP-protral/src/components/claim-chart-table.tsx`
  - 统计卡片和表格主列均展示“相同 / 不相同 / 不确定”。
- `IP-protral/src/components/product-card.tsx`
  - 商品卡片只显示匹配数和推导后的整体结论，没有商品总分。
- `IP-protral/src/app/results/page.tsx`
  - 结果汇总按“疑似侵权 / 需要进一步分析 / 疑似不侵权”计数。
  - 商品级判定依赖“某权利要求是否全匹配 / 是否存在不匹配”。
- `IP-protral/src/app/results/[productId]/page.tsx`
  - 详情页顶部判定和 Claim Chart 说明都基于旧三态。
- `IP-protral/src/lib/analysis-report-export.ts`
  - 报告导出中的状态归一化、商品结论、统计文案均依赖三态。

### 核心问题

- 当前“待确认”覆盖面过大，导致：
  - 特征区分度低，商品之间无法拉开层次；
  - 列表排序和筛选价值弱；
  - 商品级整体结论过于集中在“需进一步分析”；
  - 报告与详情页难以体现“接近程度”。

## Proposed Changes

### 1. 定义新的评分数据模型

#### 文件

- `4-claim-chat/src/graphs/state.py`
- `IP-protral/src/lib/types.ts`

#### 变更内容

- 后端 `FeatureAnalysisItem / FeatureComparisonItem` 新增并标准化以下字段：
  - `similarity_score`: `0-100`
  - `score_band`: 固定分段标签
  - `score_rationale`: 分值说明，可复用 `reason` 或拆分为更明确的字段
- 商品结果结构新增：
  - `claim_scores`: 各独立权利要求总分
  - `product_similarity_score`: 商品总分
- 前端类型同步改造：
  - `ClaimElementComparison` 改为分值驱动结构，至少包含 `similarityScore`、`scoreBand`、`reasoning`、`productFeature`、`evidenceImages`
  - `ProductComparison` 改为以 `productSimilarityScore` 为主，保留基于分数派生的可读标签，不再依赖旧 `overallVerdict`

#### 设计约束

- 分数是唯一主判断字段；标签全部从分数派生，不再把旧三态作为主接口。
- 为兼容过渡，原始数据库 `raw_payload` 可保留旧字段副本，但前台与主逻辑不再消费 `comparison_result`。

### 2. 改造 LLM 输出协议为“建议分 + 证据”

#### 文件

- `4-claim-chat/config/analyze_features_llm_cfg.json`
- `4-claim-chat/src/graphs/nodes/analyze_features_node.py`

#### 变更内容

- Prompt 中新增要求，让 LLM 对每个特征输出：
  - `suggested_score`
  - `score_band_hint` 或等价解释字段
  - `score_reason`（可与现有 `reason` 合并）
- 明确硬边界：
  - 明确具备该特征时必须给 `100`
  - 明确不具备该特征时必须给 `0`
  - 仅在无法完全确认时允许给 `1-99`
- 固定区间解释写入 Prompt，降低漂移：
  - `1-29`: 低相似 / 低可能
  - `30-69`: 中等相似 / 需补证
  - `70-99`: 高相似 / 高可能
- `analyze_features_node.py` 中补充解析与容错：
  - 解析 `suggested_score`
  - 非法值、缺失值时降级为后端默认建议值
  - 继续保留去重、补漏、异常兜底逻辑

#### 设计理由

- 由 LLM 负责表达“相似程度判断”，由规则层保证分值边界和一致性。
- 这种结构比纯规则更有表达力，又比 LLM 直出最终分更可控。

### 3. 将规则层改造成“分数校准器”

#### 文件

- `4-claim-chat/src/graphs/nodes/apply_rules_node.py`
- `4-claim-chat/src/graphs/nodes/compare_products_loop_node.py`

#### 变更内容

- 用新的确定性函数替代 `_determine_comparison_result()`，改为：
  - `_calibrate_similarity_score(reasoning_type, reason, suggested_score, evidence, evidence_images) -> {score, band}`
- 规则策略：
  - `文字直接公开 / 从图片中看出 / 结合文字和图片毫无疑义得出 / 根据功能推导得出` 强制校准为 `100`
  - `可判断不具有` 强制校准为 `0`
  - `相关信息缺失` 进入 `1-99` 区间，并按 LLM 建议分 + 证据充分度校准
- 对 `相关信息缺失` 的建议校准规则：
  - 若完全无证据，仅为弱推测：压到 `1-29`
  - 若有部分文字或图片侧弱证据，但不足以确认：落 `30-69`
  - 若已有较强证据，只差关键细节确认：落 `70-99`
- 兜底规则：
  - LLM 未返回建议分时，按证据强度自动给默认锚点，例如 `20 / 50 / 80`
  - 异常分支不再写 `UNCERTAIN`，改为写一个默认中低分并标注原因，例如 `20`
- 在子图输出中直接产出：
  - `similarity_score`
  - `score_band`
  - 兼容需要的 `reasoning_type / reason / evidence / evidence_images`

#### 说明

- 这里仍然保留 `reasoning_type`，因为它是规则校准的关键依据，也便于报告解释。
- 不再让任意调用方自己解释“不确定”，而是统一由规则层输出可比较的数值。

### 4. 新增权利要求级与商品级聚合分

#### 文件

- `4-claim-chat/src/graphs/nodes/apply_rules_node.py`
- `4-claim-chat/src/graphs/nodes/write_feishu_results_node.py`
- `IP-protral/src/app/api/analyze/route.ts`
- `IP-protral/src/lib/types.ts`

#### 变更内容

- 后端在单商品结果中增加聚合结果：
  - `claim_scores`: `[{ claim_id, score, band }]`
  - `product_similarity_score`
  - `product_score_band`
- 聚合公式按已确认方案落地：
  - 独立权利要求总分 = 该权利要求下全部特征分的最小值
  - 商品总分 = 所有特征得分相加；任一特征明确不相同则归零
- 前端接口层改为优先消费新的分数字段；若缺失，则回退现场计算。

#### 设计理由

- “最小特征分”体现独立权利要求要素完整性的保守逻辑。
- “商品总分为所有特征得分相加，且任一明确不相同即归零”对应完整覆盖才进入高分候选的业务语义。

### 5. 重构数据库与结果摘要

#### 文件

- `4-claim-chat/src/storage/database/shared/model.py`
- `4-claim-chat/src/graphs/nodes/write_feishu_results_node.py`
- `IP-protral/src/lib/db-init.ts`
- `IP-protral/src/app/api/analyze/route.ts`

#### 变更内容

- `claim_compare_results` 新增列：
  - `similarity_score` (`Integer` 或 `Float`，建议 `Integer`)
  - `score_band` (`Text`)
- 如需直接支持商品级汇总查询，可评估在 `claim_compare_runs` 增加：
  - `score_summary` 或摘要 JSON；若当前不做复杂查询，也可先只写 `result_summary`
- 重写 `_build_summary()`：
  - 不再输出 `MATCH / NO_MATCH / UNCERTAIN` 计数
  - 改为输出 `100 分特征数 / 0 分特征数 / 高中低相似区间特征数 / 商品平均分 / 商品最高分`
- 写库逻辑中同步落新字段，并让 `raw_payload` 保留完整评分结果
- Portal 侧数据库初始化脚本和读取 SQL 同步支持新列

#### 迁移策略

- 若项目已有生产数据，需要补一份数据库迁移脚本或初始化兼容逻辑。
- 旧数据查询时若无 `similarity_score`，前端需要安全回退，避免历史记录完全不可读。

### 6. 改造 API 映射与前端类型

#### 文件

- `IP-protral/src/app/api/analyze/route.ts`
- `IP-protral/src/lib/feishu-client.ts`
- `IP-protral/src/lib/types.ts`

#### 变更内容

- `mapComparisonsFromApi()` 改为读取：
  - `similarity_score`
  - `score_band`
  - `claim_scores`
  - `product_similarity_score`
- 删除或降级 `normalizeStatus()`、`determineVerdict()` 的核心职责，改为：
  - `normalizeScoreBand()` 或 `scoreToLabel()`
  - `computeProductScore()` 仅作为回退逻辑
- `feishu-client.ts` 中的数据库/飞书数据映射同步改成评分模式，避免备选数据源仍返回旧三态。
- `types.ts` 中新增统一的分值分段配置，例如：
  - `SCORE_BAND_CONFIG`
  - `PRODUCT_RISK_CONFIG`

### 7. 改造结果页与详情页展示

#### 文件

- `IP-protral/src/components/claim-chart-table.tsx`
- `IP-protral/src/components/product-card.tsx`
- `IP-protral/src/app/results/page.tsx`
- `IP-protral/src/app/results/[productId]/page.tsx`

#### 变更内容

- `claim-chart-table.tsx`
  - 顶部统计从“相同/不相同/不确定数量”改为“100 分 / 高相似 / 中相似 / 低相似 / 0 分”。
  - 主表增加“相似度分数”列，Badge 显示分数和标签。
  - 底部说明改成分数规则说明。
- `product-card.tsx`
  - 主展示改为商品总分与总分标签。
  - 次级信息可显示“特征得分小计”“100 分特征数”等。
- `results/page.tsx`
  - 概览统计改成按商品总分区间分桶，例如高风险/中风险/低风险/明确不相似。
  - 商品卡片排序可按 `product_similarity_score` 降序，提高列表有效性。
- `results/[productId]/page.tsx`
  - 头部从旧 verdict Badge 改为商品总分 Badge。
  - 可增加独立权利要求分组块，显示每个 `claim_id` 的最小特征分。

#### 标签建议

- 特征级：
  - `100`: 明确相同
  - `70-99`: 高相似
  - `30-69`: 中等相似
  - `1-29`: 低相似
  - `0`: 明确不相同
- 商品级：
  - `>= 70`: 高风险候选
  - `30-69`: 中风险候选
  - `1-29`: 低风险候选
  - `0`: 明确低风险

### 8. 改造报告导出逻辑

#### 文件

- `IP-protral/src/lib/analysis-report-export.ts`

#### 变更内容

- 报告中的结论列从旧 verdict 改为：
  - 商品总分
  - 商品风险标签
  - 特征级分值
  - 权利要求级最小分
- 替换现有状态统计函数：
  - `normalizeStatus()` -> `normalizeScore() / normalizeScoreBand()`
  - `computeVerdict()` -> `computeClaimMinScore()` + `computeProductMaxClaimScore()`
- 导出表头和文案改成评分口径，弱化“待进一步分析”的笼统表达。

### 9. 兼容与降级处理

#### 文件

- `IP-protral/src/app/api/analyze/route.ts`
- `IP-protral/src/lib/feishu-client.ts`
- `4-claim-chat/src/graphs/nodes/write_feishu_results_node.py`

#### 变更内容

- 对历史数据兼容：
  - 如果只有 `comparison_result` 没有 `similarity_score`，在 API 层临时映射：
    - `MATCH -> 100`
    - `NO_MATCH -> 0`
    - `UNCERTAIN -> 50`
  - 该兼容只用于读取旧数据，新的模块四输出不再主动产出三态。
- 对新老工作流混跑兼容：
  - 前端展示层优先读 `similarity_score`
  - 缺失时使用兼容映射生成可展示分数

#### 原因

- 用户已选择“仅保留分数”，但现网历史数据仍可能是三态；读取兼容可以避免一次性切换导致旧会话不可用。

## Assumptions & Decisions

### 已确认决策

- 评分模式采用“LLM 建议分 + 规则层校准”的混合方案。
- 核心输出只保留分值，不再以三态作为主判断字段。
- 权利要求总分按最小特征分计算。
- 商品总分按所有特征得分相加计算；任一特征明确不相同则商品总分为 0。
- `1-99` 使用固定区间。
- 前端和报告继续展示可读标签，但标签由分数派生。

### 本计划内采用的实现性假设

- `similarity_score` 使用整数，避免小数位增加理解成本。
- `score_band` 使用固定标签，避免前后端各自硬编码区间判断。
- `reasoning_type` 继续保留，不属于要删除的“旧三态”；它是评分校准和解释的依据。
- 历史数据兼容通过读取侧映射解决，本轮不强制回填旧库全部存量数据。

### 待实现时需严格统一的分段常量

- 特征级固定分段：
  - `0`
  - `1-29`
  - `30-69`
  - `70-99`
  - `100`
- 商品级标签可直接复用上述区间，或单独抽象配置，但必须全项目统一到同一个常量源。

## Verification Steps

### 后端验证

- 检查 `analyze_features` 的 Prompt 和 JSON 解析，确认每个特征都能稳定返回 `suggested_score` 或正确触发兜底。
- 用构造样例验证规则层输出：
  - 明确公开 -> `100`
  - 明确不具有 -> `0`
  - 弱证据不确定 -> `1-29`
  - 中等证据不确定 -> `30-69`
  - 高证据待确认 -> `70-99`
- 验证 `claim_scores` 与 `product_similarity_score` 的聚合公式是否正确。
- 检查数据库写入和读取，确认新列落库成功，旧数据可兼容读取。

### 前端验证

- 结果列表页能按商品总分展示、排序和分桶。
- 商品详情页能显示：
  - 特征分数
  - 分值标签
  - 权利要求级最小分
  - 商品总分
- 历史三态数据会被映射成可展示分值，不出现空白页面。

### 报告验证

- 导出报告中的分值、标签、商品汇总分与页面一致。
- 不再出现以旧三态为核心的统计口径。

### 回归关注点

- 异常兜底路径不能再产出旧 `UNCERTAIN` 主字段。
- `results/page.tsx`、`results/[productId]/page.tsx`、`analysis-report-export.ts` 三处商品级逻辑必须保持一致，避免一处按旧规则、一处按新分数。
- 备选数据源 `feishu-client.ts` 与 API 主路径必须使用同一分数语义，避免不同入口结果不一致。
