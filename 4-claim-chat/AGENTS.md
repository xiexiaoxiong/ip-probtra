## 项目概述
- **名称**: 专利技术特征比对模块 (Patent Feature Comparison Module)
- **功能**: 从飞书多维表格中提取独立权利要求、说明书和商品信息，基于说明书理解拆解权利要求为技术特征单元，逐特征与商品信息进行事实比对，输出结构化比对结果并回写飞书多维表格。

### 节点清单
| 节点名 | 文件位置 | 类型 | 功能描述 | 分支逻辑 | 配置文件 |
|-------|---------|------|---------|---------|---------|
| parse_and_fetch | `nodes/parse_and_fetch_node.py` | task | 解析飞书URL，自动识别子表格角色（权利要求表/说明书表/说明书附图表/产品表），读取说明书+附图+独立权利要求+商品信息 | - | - |
| decompose_claim | `nodes/decompose_claim_node.py` | agent | 基于说明书上下文理解，使用大模型将**每个独立权利要求**拆解为可编号的技术特征单元（feature_id含claim_id前缀如1A/9A） | - | `config/decompose_claim_llm_cfg.json` |
| compare_products_loop | `nodes/compare_products_loop_node.py` | looparray | 使用线程池并行比对所有商品，每个商品独立调用子图完成所有独立权利要求的特征分析+规则判定 | - | - |
| write_feishu_results | `nodes/write_feishu_results_node.py` | task | 为每个商品创建子表格，一个子表格包含**多个独立权利要求**的比对结果，串行写入带指数退避重试 | - | - |
| analyze_features | `nodes/analyze_features_node.py` | agent | 子图节点：使用大模型在商品信息中查找每个技术特征的证据（含说明书上下文辅助理解） | - | `config/analyze_features_llm_cfg.json` |
| apply_rules | `nodes/apply_rules_node.py` | task | 子图节点：基于确定性规则判定MATCH/NO_MATCH/UNCERTAIN | - | - |

**类型说明**: task(task节点) / agent(大模型) / condition(条件分支) / looparray(列表循环) / loopcond(条件循环)

## 子图清单
| 子图名 | 文件位置 | 功能描述 | 被调用节点 |
|-------|---------|------|-----------|
| product_comparison_graph | `graphs/loop_graph.py` | 单个商品的所有独立权利要求特征比对流程：LLM分析 + 规则判定 | compare_products_loop |

## 技能使用
- 节点 `parse_and_fetch` 和 `write_feishu_results` 使用飞书多维表格集成
- 节点 `decompose_claim` 使用大语言模型（doubao-seed-2-0-pro-260215）
- 节点 `analyze_features` 使用大语言模型（doubao-seed-1-8-251228，支持多模态）

## 数据流程
1. **parse_and_fetch**: 飞书URL → 自动识别4类子表格 → 提取说明书文本+附图URL+独立权利要求列表+商品列表
2. **decompose_claim**: 对每个独立权利要求，结合说明书上下文 → 拆解为带claim_id前缀的技术特征（如1A,1B,...,9A,9B,...）
3. **compare_products_loop**: 并行处理每个商品，子图内逐特征比对所有权利要求
4. **write_feishu_results**: 每个商品一个子表格，包含所有权利要求的比对结果（claim_id列区分）

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
