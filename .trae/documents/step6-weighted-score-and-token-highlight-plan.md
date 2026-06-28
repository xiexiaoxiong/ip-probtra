# 步骤六加权计分与模块四最小单元高亮改造计划

## Summary

- 目标：将当前“按整块特征打 0-100 分”的方案，改为“按独立权利要求全文的有效字数/单词数加权累积分数”的方案，并同步改造模块四，使其输出可用于页面逐词高亮的最小单元状态。
- 本轮范围包含两部分：
  - 步骤六：重构商品结果列表、商品详情页、导出报告、接口映射的评分逻辑与展示形态。
  - 模块四：让 AI 不再只对整条 `feature_text` 给结论，而是对每个特征进一步拆解出最小单元，并返回每个单元的 `相同 / 不相同 / 待确认` 状态与证据。
- 已确认的产品决策：
  - 每个独立权利要求各自按 100 分计算，商品总分取各独立权利要求分数中的最高分。
  - 待确认部分按“中文有效字符 / 英文有效单词”比例计分。
  - 若任一特征出现“明确不相同”，该独立权利要求总分直接归零。
  - 商品详情页采用“原文逐词高亮”的主展示形态。
  - 步骤六列表页不仅改分数，还要整体改成更接近竞品截图的表格式结果页，并显示特征小色块概览。

## Current State Analysis

### 1. 步骤六当前仍是“整块特征分”

- `IP-protral/src/app/api/analyze/route.ts`
  - 当前接口层读取模块四的 `similarity_score` 后，直接把它作为特征级得分。
  - 商品分数通过 `computeProductScore()` 计算：
    - 独立权利要求分 = 该 claim 下特征最低分
    - 商品分 = 所有 claim 分中的最高分
  - 这与用户要求的“按有效字数/单词累加”完全不同。
- `IP-protral/src/lib/types.ts`
  - `ClaimElementComparison` 只有 `similarityScore / scoreBand / reasoning` 等整块字段。
  - 当前类型中没有“最小单元列表”“高亮片段”“claim 全文字数权重”等字段。
- `IP-protral/src/lib/analysis-report-export.ts`
  - 报告导出沿用了和页面一致的整块特征分逻辑。
  - 没有 claim 全文基准分、特征权重分、最小单元命中明细。

### 2. 列表页当前是卡片式，不是竞品表格式

- `IP-protral/src/app/results/page.tsx`
  - 当前商品列表为卡片布局，单卡只展示商品图、商品名、商品总分和最高权利要求分。
  - 没有竞品截图里那种表格式列结构，也没有一排按 `feature_id` 展示的小色块概览。
- `IP-protral/src/components/product-card.tsx`
  - 仍服务于卡片式呈现，不适合直接承载竞品式行列表格。

### 3. 详情页当前没有“逐词荧光标注”

- `IP-protral/src/components/claim-chart-table.tsx`
  - 当前只能展示整条 `claimElement` 文本、整条结论 Badge、整条特征分。
  - 并没有 token / segment 级结构，所以无法实现“相同绿、不同红、待确认黄”的底部荧光线。
- `IP-protral/src/app/results/[productId]/page.tsx`
  - 详情页已展示商品总分和 claim 聚合分，但依赖的还是整块特征结果。

### 4. 模块四当前拆解粒度不够

- `4-claim-chat/src/graphs/nodes/decompose_claim_node.py`
  - 当前只把独立权利要求拆解到 `feature_text` 级别。
  - 没有继续拆到页面高亮所需的“最小可判断单元”级别。
- `4-claim-chat/config/decompose_claim_llm_cfg.json`
  - Prompt 的“最小技术特征单元”定义，仍对应现有的 `feature_text` 粒度，而不是用于细粒度高亮的字符/词组单元。
- `4-claim-chat/src/graphs/nodes/analyze_features_node.py`
  - 当前要求 AI 输出 `evidence / reason / reasoning_type / suggested_score`。
  - 仍以整条特征为判断单位，不会返回“该特征内部哪些词相同、哪些词不同、哪些词待确认”。
- `4-claim-chat/src/graphs/nodes/apply_rules_node.py`
  - 当前规则层只处理整条特征的分值校准，不具备“按有效字数比例结算特征得分”的能力。

### 5. 数据库存储结构还不支持细粒度高亮

- `4-claim-chat/src/storage/database/shared/model.py`
  - `claim_compare_results` 当前只有 `feature_text / evidence / similarity_score / score_band / raw_payload`。
  - 没有用于持久化最小单元列表、最小单元状态、有效字数统计、特征权重分、claim 基准分等字段。
- `4-claim-chat/src/graphs/nodes/write_feishu_results_node.py`
  - 当前写库时主要落整块结果，虽然有 `raw_payload`，但现有摘要与写入逻辑没有围绕“最小单元计分”组织。

## Proposed Changes

### 1. 定义新的评分模型：claim 满分 100，特征按有效字数/单词占比赋权

#### 目标口径

- 对每个独立权利要求单独计分，满分固定为 100。
- 先对独立权利要求全文做“有效内容标准化”：
  - 排除标点符号。
  - 排除标记数字，如括号编号、零件序号、阿拉伯数字标记。
  - 排除“所述”。
  - 中文按有效字符计数；英文按有效单词计数。
- 每个特征的理论满分 = `该特征有效内容数 / 该 claim 有效内容总数 * 100`。
- 每个特征的实际得分规则：
  - 整个特征明确相同：拿满该特征理论满分。
  - 整个特征明确不相同：该 claim 总分直接归零。
  - 待确认：仅按该特征内部“已确认相同”的有效内容占比换算实际分。
- claim 总分 = 各特征实际得分累加；若存在任何“明确不相同”，该 claim 总分直接为 0。
- 商品总分 = 所有独立权利要求分数中的最高分。

#### 文件

- `IP-protral/src/lib/types.ts`
- `IP-protral/src/app/api/analyze/route.ts`
- `IP-protral/src/lib/analysis-report-export.ts`
- 新增建议文件：`IP-protral/src/lib/claim-score.ts`

#### 具体调整

- 在 `types.ts` 中新增以下概念：
  - `ClaimTokenStatus`: `match | mismatch | uncertain`
  - `ClaimTokenUnit`: 最小高亮单元，至少包含 `text / normalizedText / status / evidence / start / end / effectiveLength`
  - `ClaimElementScoreDetail`: 特征理论满分、已确认得分、有效长度、命中长度
  - `ClaimScoreSummary`: 除 `claimId / similarityScore` 外，新增 `claimTotalEffectiveLength / claimMatchedEffectiveLength / zeroedByMismatch`
- 把“分段”语义从当前 `0-29/30-69/70-100` 重新改成用户指定颜色口径：
  - `> 70` 绿色
  - `30-70` 黄色
  - `< 30` 红色
  - 建议边界实现为：`score > 70 => green`，`30 <= score && score <= 70 => yellow`，`score < 30 => red`
- 新增统一计分工具文件 `claim-score.ts`，承载：
  - 文本标准化函数
  - 中文有效字符计数与英文有效单词计数
  - 特征权重分计算
  - claim 汇总分计算
  - 列表/详情/导出共享的颜色映射函数

### 2. 把模块四从“特征级判断”改造成“特征内最小单元判断”

#### 文件

- `4-claim-chat/src/graphs/state.py`
- `4-claim-chat/config/decompose_claim_llm_cfg.json`
- `4-claim-chat/config/analyze_features_llm_cfg.json`
- `4-claim-chat/src/graphs/nodes/decompose_claim_node.py`
- `4-claim-chat/src/graphs/nodes/analyze_features_node.py`

#### 具体调整

- `state.py`
  - 为 `FeatureItem` 增加可选字段，用于承载预拆解结果，例如：
    - `feature_segments`
    - `normalized_feature_text`
    - `effective_length`
  - 为 `FeatureAnalysisItem / FeatureComparisonItem` 增加：
    - `token_units` 或 `segments`
    - `feature_full_score`
    - `feature_awarded_score`
    - `matched_effective_length`
    - `claim_total_effective_length`
    - `zeroed_by_mismatch`
- `decompose_claim_llm_cfg.json`
  - 从“最小技术特征单元”升级为两层结构输出：
    - 第一层：现有 `feature_text`
    - 第二层：`feature_text` 内部的最小判断单元列表
  - 要求 AI 在拆解时保留原文顺序，便于前端逐词高亮。
- `decompose_claim_node.py`
  - 继续保留 `feature_text` 级拆解，但在每条 feature 下再附带最小单元列表。
  - 若 LLM 不稳定，则补一个规则降级：
    - 对中文以连续汉字片段为主；
    - 对英文以词组/单词边界为主；
    - 保留原文字符位置信息，便于高亮回填。
- `analyze_features_llm_cfg.json`
  - 从当前“输出整条特征的 reasoning_type + suggested_score”，改为输出每个 `feature` 内部各最小单元的判断结果。
  - 每个单元至少输出：
    - `unit_text`
    - `unit_status`（相同 / 不相同 / 待确认）
    - `evidence`
    - `reason`
    - `evidence_images`
  - 不再让 AI 负责整条特征最终分值；分值全部交给规则层。
- `analyze_features_node.py`
  - 改为解析最小单元数组，并做好 LLM 漏项、非法状态、重复单元的兜底。
  - 继续保存整条特征的证据摘要，但核心输出改为“单元状态列表”。

### 3. 用纯规则实现新的计分引擎，不再依赖 AI 打分

#### 文件

- `4-claim-chat/src/graphs/nodes/apply_rules_node.py`
- 新增建议文件：`4-claim-chat/src/utils/claim_scoring.py`

#### 具体调整

- 把当前 `_calibrate_similarity_score()` 的职责拆成两部分：
  - `模块四事实判断`：AI 只判断最小单元状态。
  - `规则层计分`：纯规则根据有效字数/单词占比计算分数。
- 在 `claim_scoring.py` 中实现：
  - `normalize_claim_text()`
    - 去标点
    - 去零件标号/数字标记
    - 去“所述”
  - `count_effective_units()`
    - 中文：按有效字符数
    - 英文：按有效单词数
  - `compute_feature_full_score()`
  - `compute_feature_awarded_score()`
  - `compute_claim_score()`
  - `compute_product_score()`
- `apply_rules_node.py` 中改为：
  - 对每条 feature 先计算理论满分。
  - 若任一单元为 `mismatch`，则该 feature 标记 `has_mismatch=true`，并触发 claim 归零。
  - 若只有 `match + uncertain`，则按 `match` 单元有效长度占 feature 总有效长度的比例换算得分。
  - 计算并输出：
    - `feature_full_score`
    - `feature_awarded_score`
    - `matched_effective_length`
    - `feature_effective_length`
    - `claim_total_effective_length`
    - `claim_score`
    - `zeroed_by_mismatch`
- 不再保留当前 `suggested_score` 为核心计分来源；该字段在兼容期可留在 `raw_payload`，但不再参与步骤六最终打分。

### 4. 扩展数据库结构，持久化最小单元与加权计分明细

#### 文件

- `4-claim-chat/src/storage/database/shared/model.py`
- `4-claim-chat/src/main.py`
- `4-claim-chat/src/graphs/nodes/write_feishu_results_node.py`
- `IP-protral/src/lib/db-init.ts`

#### 具体调整

- 在 `claim_compare_results` 中新增字段：
  - `feature_full_score`
  - `feature_awarded_score`
  - `feature_effective_length`
  - `matched_effective_length`
  - `claim_total_effective_length`
  - `zeroed_by_mismatch`
  - `token_units`（JSON）
- 继续保留 `raw_payload`，但读取层不能只靠 `raw_payload` 推断，需要有明确列供查询和导出。
- 在 `main.py` 与 `db-init.ts` 增加启动期 schema 兼容逻辑。
- `write_feishu_results_node.py`
  - 写库时同步写入上述字段。
  - 结果摘要 `_build_summary()` 改成围绕新口径统计：
    - 绿色商品数
    - 黄色商品数
    - 红色商品数
    - claim 归零次数
    - 单元级 `match / uncertain / mismatch` 数量

### 5. 改造步骤六接口层，让前端消费“单元状态 + claim 权重分”

#### 文件

- `IP-protral/src/app/api/analyze/route.ts`
- `IP-protral/src/lib/feishu-client.ts`

#### 具体调整

- `route.ts`
  - 不再把模块四输出的 `similarity_score` 当作最终事实。
  - 改为优先读取：
    - `token_units`
    - `feature_full_score`
    - `feature_awarded_score`
    - `claim_total_effective_length`
    - `zeroed_by_mismatch`
  - 如果新字段缺失，再回退到现有整块分逻辑，保证历史数据可读。
- `feishu-client.ts`
  - 与 API 主路径保持同一套映射逻辑，避免飞书直连场景和本地数据库场景结果口径不一致。

### 6. 把步骤六商品列表改成竞品式表格布局

#### 文件

- `IP-protral/src/app/results/page.tsx`
- 新增建议文件：`IP-protral/src/components/results-score-table.tsx`

#### 具体调整

- 将当前卡片网格替换为更接近竞品截图的表格式列表：
  - 商品名
  - 来源/公司（能拿到则展示，拿不到则用现有来源字段或留空）
  - 总分
  - 可选日期列（若当前数据源没有稳定日期则暂不展示）
  - 特征/元素小色块概览
- 列表排序按商品总分降序。
- 列表颜色规则改成用户指定口径：
  - `>70` 绿色
  - `30-70` 黄色
  - `<30` 红色
- 元素概览按 `feature_id` 展示小色块：
  - 绿色：该特征全匹配
  - 红色：该特征触发 mismatch
  - 黄色：该特征有待确认但未触发归零
- 组件拆分建议：
  - `results/page.tsx` 保留数据加载与状态管理
  - 新建 `results-score-table.tsx` 负责表格布局、排序、概览色块

### 7. 改造详情页和 Claim Chart，支持原文逐词荧光高亮

#### 文件

- `IP-protral/src/components/claim-chart-table.tsx`
- `IP-protral/src/app/results/[productId]/page.tsx`
- 新增建议文件：`IP-protral/src/components/claim-token-highlight.tsx`

#### 具体调整

- 新建 `claim-token-highlight.tsx`
  - 输入原始 `feature_text` 和 `token_units`
  - 按原文顺序渲染，并对每个单元加底部荧光样式：
    - `match`：荧光绿
    - `mismatch`：荧光红
    - `uncertain`：荧光黄
- `claim-chart-table.tsx`
  - “特征内容”列改为渲染高亮后的原文，而不是纯文本。
  - “相似度”列显示：
    - 特征理论满分
    - 特征实际得分
  - “比对分析”列可补充：
    - 命中有效长度 / 特征有效长度
    - 是否因 mismatch 使 claim 归零
- `results/[productId]/page.tsx`
  - 详情头部增加：
    - 商品总分
    - 触发最高分的 claim
    - 各独立权利要求分数
  - 若某 claim 因 mismatch 归零，需要显式显示原因标签，避免用户只看到低分不知为何归零。

### 8. 更新导出报告，让结果和页面口径一致

#### 文件

- `IP-protral/src/lib/analysis-report-export.ts`

#### 具体调整

- 汇总页导出字段改为：
  - 商品总分
  - 颜色等级（绿/黄/红）
  - 最高 claim 分
  - 是否存在 mismatch 归零
- 详情页导出字段改为：
  - 特征理论满分
  - 特征实际得分
  - 匹配有效长度 / 特征有效长度
  - 最小单元状态概览
- 若 Excel 中不适合做富文本荧光标注，至少导出：
  - 原文
  - 单元列表
  - 每个单元状态
  - 命中比例

### 9. 兼容与降级策略

#### 文件

- `IP-protral/src/app/api/analyze/route.ts`
- `IP-protral/src/lib/feishu-client.ts`
- `IP-protral/src/lib/analysis-report-export.ts`

#### 具体调整

- 历史数据兼容：
  - 若旧记录没有 `token_units / feature_full_score / feature_awarded_score`，仍可按当前整块 `similarity_score` 展示，但标记为“旧版评分”。
- 新旧模块四混跑兼容：
  - 前端优先读取新字段。
  - 缺失时回退到旧字段，避免历史会话完全不可用。
- 页面文案需要显式说明“旧版结果与新版字数加权规则不同”，避免用户误解。

## Assumptions & Decisions

### 已确认决策

- 每个独立权利要求各自按 100 分计算。
- 商品总分取最高 claim 分。
- 待确认部分按中文有效字符 / 英文有效单词比例计分。
- 只要出现明确不相同，该 claim 直接归零。
- 结果列表页要改成更接近竞品的表格式。
- 商品列表中需要显示每个特征的小色块概览。
- 详情页要做原文逐词荧光高亮。

### 本计划中的实现性假设

- “标记数字”包括括号数字、部件编号、纯数字序号等，首版用正则统一排除。
- 除“所述”外，本轮不额外扩展中文停用词；若后续发现“一个 / 至少一个 / 若干”等也应排除，再单独迭代。
- 英文单词计数先采用规则分词，不引入额外 NLP 库。
- AI 负责“最小单元状态判断”，不负责最终分值计算。
- 若 Excel 难以复刻荧光底线，则以状态列导出为主，不强做富文本样式。

### 需在实现时统一的规则常量

- 有效内容清洗规则：
  - 去标点
  - 去数字标记
  - 去“所述”
- 颜色规则：
  - `score > 70`：绿色
  - `30 <= score && score <= 70`：黄色
  - `score < 30`：红色
- 单元状态：
  - `match`
  - `mismatch`
  - `uncertain`

## Verification Steps

### 1. 规则计分验证

- 以固定样例 claim 验证：
  - claim 总有效长度 = 1000 时，某 feature 有效长度占 10%，则该 feature 理论满分为 10 分。
  - 若该 feature 全部 match，则得 10 分。
  - 若只有 1/10 的有效字符命中，则得 1 分。
  - 若任一 feature 明确 mismatch，则 claim 总分 = 0。
- 针对中文样例验证“所述”“(38)”不计分。
- 针对英文样例验证按有效单词计数而非按字符长度计数。

### 2. 模块四结构验证

- 检查 `decompose_claim` 是否稳定返回 feature 内部最小单元。
- 检查 `analyze_features` 是否为每个最小单元返回状态。
- 检查 `apply_rules` 是否完全不依赖 AI 打分，也能单独完成最终 claim / product 计分。

### 3. 页面验证

- 列表页：
  - 表格式布局正常
  - 商品按总分降序
  - 颜色区间符合 `>70 / 30-70 / <30`
  - 每个商品显示特征小色块概览
- 详情页：
  - 原文逐词荧光高亮正确
  - 特征理论满分与实际得分可见
  - claim 归零时有显式提示

### 4. 导出与兼容验证

- 导出报告中的商品总分、claim 分、feature 分与页面一致。
- 旧版只有 `similarity_score` 的历史记录仍然可查看，不白屏。
- API 主路径与飞书直连路径输出一致。

### 5. 回归关注点

- 当前所有依赖 `similarity_score` 的地方都要排查，避免有的地方走新规则、有的地方仍走旧规则。
- `results/page.tsx`、`results/[productId]/page.tsx`、`analysis-report-export.ts`、`route.ts` 四处必须共享同一套计分函数，不能重复实现。
- 模块四最小单元的原文顺序和字符位置必须稳定，否则前端高亮会错位。
