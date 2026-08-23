## 项目概述
- **名称**: 专利技术特征比对模块 (Patent Feature Comparison Module)
- **当前主线功能**: 通过 `patent_record_id` + `analysis_session_id` 从 Postgres 读取专利解析结果和商品检索结果，基于说明书理解拆解独立权利要求技术特征，逐商品输出结构化比对结果并写入 `claim_compare_runs` / `claim_compare_results`。
- **历史兼容功能**: 早期飞书多维表格读取/写入逻辑仍保留在 `parse_and_fetch`、`write_feishu_results` 等节点和文档章节中，但新开发优先以 Postgres 主链路为准。

### 节点清单
| 节点名 | 文件位置 | 类型 | 功能描述 | 分支逻辑 | 配置文件 |
|-------|---------|------|---------|---------|---------|
| parse_and_fetch | `nodes/parse_and_fetch_node.py` | task | 当前优先按 `patent_record_id` + `analysis_session_id` 读取 Postgres；历史兼容模式可解析飞书URL并读取子表格 | - | - |
| decompose_claim | `nodes/decompose_claim_node.py` | agent | 基于说明书上下文理解，使用大模型将**每个独立权利要求**拆解为可编号的技术特征单元（feature_id含claim_id前缀如1A/9A） | - | `config/decompose_claim_llm_cfg.json` |
| compare_products_loop | `nodes/compare_products_loop_node.py` | looparray | 使用线程池并行比对所有商品，每个商品独立调用子图完成所有独立权利要求的特征分析+规则判定 | - | - |
| write_feishu_results | `nodes/write_feishu_results_node.py` | task | 当前优先写入 Postgres claim compare 表；历史兼容逻辑可写飞书子表格 | - | - |
| analyze_features | `nodes/analyze_features_node.py` | agent | 子图节点：使用大模型在商品信息中查找每个技术特征的证据（含说明书上下文辅助理解） | - | `config/analyze_features_llm_cfg.json` |
| apply_rules | `nodes/apply_rules_node.py` | task | 子图节点：基于确定性规则判定MATCH/NO_MATCH/UNCERTAIN | - | - |

**类型说明**: task(task节点) / agent(大模型) / condition(条件分支) / looparray(列表循环) / loopcond(条件循环)

## 子图清单
| 子图名 | 文件位置 | 功能描述 | 被调用节点 |
|-------|---------|------|-----------|
| product_comparison_graph | `graphs/loop_graph.py` | 单个商品的所有独立权利要求特征比对流程：LLM分析 + 规则判定 | compare_products_loop |

## 技能使用
- 当前主线依赖 Postgres 表读取/写入；飞书多维表格集成仅为历史兼容路径
- 节点 `decompose_claim` 使用 `glm-4.6v`
- 节点 `analyze_features` 使用 `glm-4.6v` 多模态模型，同时提交商品文本与图片

## 数据流程
1. **parse_and_fetch**: Postgres `patent_record_id` + `analysis_session_id` → 提取说明书文本+附图URL+独立权利要求列表+商品列表；历史兼容模式可从飞书URL读取。商品读取优先旧表 `search_products`；若当前 session 旧表无商品，则兜底读取新模块3独立表 `product_detail_search_products`，将 `description + detail_text` 合成商品描述、`picture` 作为图片，并在 `raw_data` 标记 `module3_product_detail_fallback=true`。
2. **decompose_claim**: 对每个独立权利要求，结合说明书上下文 → 拆解为带claim_id前缀的技术特征（如1A,1B,...,9A,9B,...）
3. **compare_products_loop**: 并行处理每个商品，子图内逐特征比对所有权利要求
4. **write_feishu_results**: 写入 `claim_compare_runs` / `claim_compare_results`，历史兼容模式可写飞书子表格

## 飞书子表格识别策略
- 自动列出Base下所有表格，按名称关键词+字段特征智能识别：
  - **权利要求表**: 名称含"权利要求"，含权利要求编号+原文字段
  - **说明书表**: 名称含"说明书"（排除"附图"），含段落/原文字段
  - **说明书附图表**: 名称含"说明书附图"或"附图"，含图片字段
  - **产品表**: 名称含"产品"或"商品"，含商品名称/描述/图片字段
- 未识别到说明书/附图时降级运行（仅基于权利要求文字拆解）

## 独立权利要求识别逻辑
- 不引用其他权利要求的为独立权利要求（如权利要求1、权利要求9）
- 识别规则：权利要求文本中不含"根据权利要求X"的引用表述
- 仅对独立权利要求进行技术特征拆解和比对

## 飞书 API 注意事项
- **create_table 请求体格式**：必须使用 `{"table": {"name": "xxx", "fields": [...]}}` 包裹，字段名是 `name` 而非 `table_name`
- **飞书写接口不支持并发**：串行调用，间隔 0.5s，带指数退避重试
- **每个 Base 表格数量上限 100**：含原有表格+新建子表格
- **create_table 的 fields 不支持 description 字段**：只支持 field_name/type/property

## 比对规则说明
- reasoning_type 为"文字直接公开"/"从图片中看出"/"结合文字和图片毫无疑义得出"/"根据功能推导得出" → MATCH
- reasoning_type 为"相关信息缺失"且reason不含明确缺失指示 → UNCERTAIN
- reasoning_type 为"相关信息缺失"且reason含明确缺失/不相同指示 → NO_MATCH
- LLM仅输出evidence/reason/reasoning_type，不输出最终比对结论
- 最终比对结论由 `apply_rules` 节点的确定性规则生成
- 二次检索弱同品线索限制：带“低置信同品线索”或 `/weak` 标记的补充资料不能单独支撑 `token_unit=match`；`analyze_features_node._downgrade_weak_enrichment_only_units` 会把仅由 weak 资料支撑的 match 自动降级为 `uncertain`。只有同时引用商品名称、商品图片/OCR 或 `/strong` 同品来源等更强证据时，match 才能保留。
- 真实验证记录：2026-06-29 用 `analysis_1782651371368_9qeyd5-module4-weak-guard` 完整重跑模块4，生成 `claim_compare_run_id=66`，19 个商品/132 行结果写库完成；含 weak 补充的商品（search_product id 690）没有 weak 引用 token，全 run 数据库核查 `bad_weak_only_match_units=0`。

## 字段分类与图片提取策略（parse_and_fetch节点）
- **分类策略**：关键词优先 + 字段类型推断
  1. 关键词匹配 → 权利要求/商品名称/描述/图片
  2. 字段类型推断 → 附件类型(17)=图片，URL类型(14)=图片链接
  3. 兜底 → 未分类文本字段
- **图片提取策略**：六层兜底
  1. 从已分类的图片字段中提取URL（支持附件格式 tmp_url/url/link、富文本 text 键、纯字符串）
  2. 图片字段 fallback: _extract_image_urls 未提取到时，用 _extract_text_value 获取 URL 文本
  3. 从附件字段中补充提取（检查mime_type）
  4. 从未分类文本字段中提取名称/描述
  5. 遍历所有字段值检查附件结构（file_token/tmp_url）
  6. 终极兜底：对所有列表类型字段值尝试提取图片URL
- **飞书字段类型常量**：URL=14（非13），附件=17
- **富文本URL格式**：飞书多行文本字段（type=1）存储 URL 的格式为 `[{"text": "https://...", "type": "text"}]`

## 飞书多维表格 URL 生成规则
- **禁止使用 API 域名**：`https://open.feishu.cn/base/...` 是 API 端点，用户无法在浏览器中访问
- **正确格式**：从用户输入的原始 URL 中提取租户域名（如 `https://bytedance.feishu.cn`），拼接为 `https://{tenant}.feishu.cn/base/{app_token}?table={table_id}`
- **提取逻辑**：`_extract_tenant_base_url()` 函数，支持 `xxx.feishu.cn` 和 `xxx.larkoffice.com` 两种域名
- **回退策略**：若无法提取租户域名，使用 `https://feishu.cn/base`

## LLM 返回值解析注意
- decompose_claim LLM 可能返回 `{"features": [...]}` dict 格式而非直接返回 list
- 代码已处理 dict → list 自动提取（尝试 features/data/result/items 键）

## 2026-07-06 更新

- `parse_and_fetch_node.py` 新增对平行模块3 `product_detail_search_products` 的 fallback 读取。该逻辑仅在旧 `search_products` 查不到商品时触发，不改变正式旧模块3主流程。
- 真实验证：同 `patent_record_id=101`、`analysis_session_id=module_test_codex_product_1782690000` 下，旧表无商品但新模块3表有 Narwal 商品详情；模块4 `/run` 成功读取该商品并完成 1 个商品、8 个特征比对，`claim_compare_run_id=71`。
- 验证命令：`4-claim-chat/.venv/bin/python -m py_compile 4-claim-chat/src/graphs/nodes/parse_and_fetch_node.py` 通过；PM2 `patent-4-claim-chat` 已重启。

## 2026-07-12 模型故障安全规则

- LLM 批次失败时禁止使用局部词、短子串或营销词生成 `match`。规则兜底必须把全部 `token_units` 保持为 `uncertain`，使用 `reasoning_type=相关信息缺失`，并标注 `analysis_failed=true`、`analysis_source=rule_fallback`。
- HTTP 429、速率限制或进程级 cooling-down 属于当前批次不可重试错误；首次失败后立即退出批次重试，不执行剩余次数和退避休眠。
- Portal 测试 API 把“大模型调用失败”“模型调用失败”和“规则兜底”统一计入 `llmErrorCount`；只要存在失败兜底行，测试步骤不得显示 completed。
- `scripts/test_module4_scoring_pipeline.py` 已覆盖“壳体一端”与无关“一端”的局部词重合不产生 match，以及 429 在 6 次配置下实际只调用模型 1 次。

## 2026-07-15 多模态模型规则

- `analyze_features`、`decompose_claim`、`review_analysis` 配置显式使用 `glm-4.6v`；PM2 的 default/fast/vision 模型也统一为 `glm-4.6v`。
- 只要输入含商品图片，传输层必须保留同一请求中的 `text` 与 `image_url` 内容块。禁止因提供商兼容或请求失败把图片转成文字说明后调用纯文本模型。
- 默认 `LOCAL_LLM_ALLOW_FALLBACK=0`。多模态调用失败时应返回明确失败并使用既有的 uncertain 安全兜底，不得静默丢图后给出确定结论。
- `scripts/test_multimodal_transport.py` 以无网络桩测试断言实际请求模型为 `glm-4.6v`，且负载同时含文本和图片。
