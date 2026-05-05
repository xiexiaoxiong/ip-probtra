## 项目概述
- **名称**: 关键词生成模块 (Keyword Extraction Module)
- **功能**: 从飞书多维表格读取专利数据（支持三表关联：数据表、权利要求表、附图表），提取用于商品检索的核心关键词，并写入新的飞书数据表

---

## 架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              主图                                        │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  1. 飞书链接解析 ─────────────────────────────────────────────────→      │
│         ↓                                                               │
│  2. 飞书数据加载 ─────────────────────────────────────────────────→      │
│         ↓                                                               │
│  3. 数据整合分析 (LLM) ──────────────────────────────────────────→      │
│         ↓                                                               │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  4. 记录循环处理 (子图，所有节点可独立调试)                          │   │
│  │  ┌────────────────────────────────────────────────────────────┐  │   │
│  │  │  4.1 记录分发 ─────────────────────────────────────────→   │  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.2 输入验证 ─────────────────────────────────────────→   │  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.3 产品客体提取 (LLM) ← 淘宝品类级精确 ────────────────→  │  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.4 发明点提炼 (LLM) ← 可调试 Prompt ─────────────────→   │  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.5 关键词提取 (LLM) ← 可调试 Prompt ──────────────────→  │  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.6 发明点特征词精炼 (LLM) ← 消费者搜索语言 ⭐ ──────────→│  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.7 关键词筛选 (LLM) ← 排除宽泛/专利书面语 ────────────→│  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.8 场景词推断 (LLM) ← 消费者场景/人群/诉求 ⭐ ─────────→│  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.9 关键词组合 (LLM) ← 品牌同款/特征/场景/人群 ────────→│  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.10 结果组装 ─────────────────────────────────────────→   │  │   │
│  │  │        ↓                                                   │  │   │
│  │  │  4.11 结果收集 ────→ 循环判断 ──→ 有更多记录 → 返回 4.1   │  │   │
│  │  └────────────────────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│         ↓                                                               │
│  5. 关键词写入 ─────────────────────────────────────────────────→       │
│         ↓                                                               │
│      END                                                                │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

### 主图节点清单

| 节点名 | 文件位置 | 类型 | 功能描述 | 配置文件 |
|-------|---------|------|---------|---------|
| feishu_url_parser | `nodes/feishu_url_parser_node.py` | task | 解析飞书多维表格链接，提取 app_token | - |
| feishu_data_loader | `nodes/feishu_data_loader_node.py` | task | 从飞书多维表格加载所有数据表 | - |
| data_integration | `nodes/data_integration_node.py` | agent | 使用大模型分析三个表的关系 | `config/data_integration_llm_cfg.json` |
| record_process_loop | `loop_graph.py` | looparray | 循环处理每条专利记录（调用子图） | - |
| keyword_writer | `nodes/keyword_writer_node.py` | task | 将关键词写入飞书多维表格 | - |

### 子图节点清单（可独立调试 Prompt）

| 节点名 | 文件位置 | 类型 | 功能描述 | 配置文件 |
|-------|---------|------|---------|---------|
| record_dispatch | `nodes/record_dispatch_node.py` | task | 从记录列表取出当前记录，提取字段值 | - |
| input_validation | `nodes/input_validation_node.py` | task | 验证权利要求文本和编号是否为空 | - |
| product_object_extraction | `nodes/product_object_extraction_node.py` | agent | 从技术领域提取产品客体（精确到淘宝品类级） | `config/product_object_extraction_llm_cfg.json` |
| invention_point_extraction | `nodes/invention_point_extraction_node.py` | agent | 从说明书提炼发明点 | `config/invention_point_extraction_llm_cfg.json` |
| keyword_extraction | `nodes/keyword_extraction_node.py` | agent | **核心：从权利要求提取关键词** | `config/keyword_extraction_llm_cfg.json` |
| invention_point_refinement | `nodes/invention_point_refinement_node.py` | agent | **精炼特征词→消费者搜索语言** | `config/invention_point_refinement_llm_cfg.json` |
| keyword_filtering | `nodes/keyword_filtering_node.py` | agent | **筛选关键词，排除宽泛/专利书面语** | `config/keyword_filtering_llm_cfg.json` |
| scene_word_inference | `nodes/scene_word_inference_node.py` | agent | **推断消费者场景词（家用/静音/省空间等）** | `config/scene_word_inference_llm_cfg.json` |
| keyword_combination | `nodes/keyword_combination_node.py` | agent | **组合关键词（品牌同款/特征/场景/人群/功能）** | `config/keyword_combination_llm_cfg.json` |
| result_assembly | `nodes/result_assembly_node.py` | task | 组装最终关键词列表 | - |
| result_collect | `nodes/result_collect_node.py` | task | 收集结果，更新索引 | - |

**类型说明**: task(task节点) / agent(大模型) / looparray(列表循环)

---

## Prompt 调试指南

### 如何调整关键词提取效果

修改 `config/keyword_extraction_llm_cfg.json`：

```json
{
    "config": {
        "model": "glm-5-0-260211",
        "temperature": 0.3
    },
    "sp": "【修改 System Prompt，定义角色和规则】",
    "up": "【修改 User Prompt，传入变量 {{claim_text}} 等】"
}
```

### 可调整的配置文件

| 配置文件 | 作用 | 调整建议 |
|---------|------|---------|
| `config/data_integration_llm_cfg.json` | 三表关联分析 | 调整表识别规则 |
| `config/product_object_extraction_llm_cfg.json` | 产品客体提取 | 调整客体精确度标准（淘宝品类级） |
| `config/invention_point_extraction_llm_cfg.json` | 发明点提炼 | 调整发明点提取规则 |
| `config/keyword_extraction_llm_cfg.json` | **关键词提取** | **调整关键词提取规则** |
| `config/invention_point_refinement_llm_cfg.json` | **特征词精炼** | **调整消费者搜索语言转化规则** |
| `config/keyword_filtering_llm_cfg.json` | **关键词筛选** | **调整筛选标准（排除宽泛/专利书面语）** |
| `config/scene_word_inference_llm_cfg.json` | **场景词推断** | **调整场景词推断维度和规则** |
| `config/keyword_combination_llm_cfg.json` | **关键词组合** | **调整组合策略（品牌同款/特征/场景/人群/功能）** |

---

## 子图清单

| 子图名 | 文件位置 | 功能描述 | 被调用节点 |
|-------|---------|------|---------|
| record_process_subgraph | `graphs/loop_graph.py` | 循环处理每条专利记录（展开所有节点） | record_process_loop |

---

## 技能使用

- 飞书多维表格技能：`feishu_data_loader`, `keyword_writer`
- 大语言模型技能：`data_integration`, `product_object_extraction`, `invention_point_extraction`, `keyword_extraction`, `invention_point_refinement`, `keyword_filtering`, `scene_word_inference`, `keyword_combination`

---

## 三表关联说明

飞书多维表格通常包含三个子表：
1. **数据表**：专利基础信息（专利号、申请人、技术领域、背景技术、发明内容等）
2. **权利要求表**：权利要求详情（权利要求编号、类型、原文等）
3. **附图表**：说明书附图信息（附图编号、附图说明等）

三个表通过**关联字段**（通常是专利号）进行关联，工作流会自动识别并整合数据。

---

## 异常处理

- `EMPTY_URL` - 飞书链接为空
- `INVALID_URL_FORMAT` - 无法解析 app_token
- `FEISHU_API_ERROR` - 飞书API调用失败
- `NO_TABLES_FOUND` - 没有找到数据表
- `DATA_LOAD_ERROR` - 数据加载错误
- `DATA_INTEGRATION_ERROR` - 数据整合错误
- `EMPTY_CLAIM_TEXT` - 权利要求文本为空
- `NO_KEYWORD_EXTRACTED` - 无法提取关键词

---

## 最近更新

### 2025-01-XX: 健身器材领域优化
- **产品客体提取**：SP 增加健身器材品类树，引导 LLM 定位到叶子品类（如「跑步机」而非「健身器材」）
- **发明点提炼**：SP 增加健身器材常见创新维度参考（结构创新、阻力/力度、减震/缓冲、多功能、智能/电控、人体工学、空间/收纳、安全防护、静音降噪）
- **精炼节点**：SP 增加健身器材领域转化示例（作为思路参考，不限定映射关系，让 LLM 自行判断转化）
- **关键词提取**：SP 增加健身器材高价值关键词模式和需谨慎提取的零件名称
- **筛选节点**：SP 增加健身器材专属排除词（零件名、泛功能描述、泛品类词、抽象技术描述）
- **新增场景词推断节点**：从产品特征推断消费者搜索场景词（家用/静音/省空间/老人等），这些词消费者常用但不会出现在专利文本中
  - 新增文件：`src/graphs/nodes/scene_word_inference_node.py`、`config/scene_word_inference_llm_cfg.json`
  - 子图流程变更：`关键词筛选` → `场景词推断` → `关键词组合`
- **组合节点**：SP 增加三种新组合模式（场景组合型/人群组合型/功能组合型），接收场景词作为输入
- **状态定义**：GlobalState 新增 `scene_words` 字段，KeywordCombinationInput 新增 `scene_words` 参数

### 2025-01-XX: 移除同义词生成节点 + 客体精确度升级
- **移除同义词生成节点**：
  - 原因：精炼节点已将专利术语翻译为消费者搜索语言，同时引入了同义不同说的表述（如"屏幕可动"/"屏能转"/"屏幕转动"），无需单独生成同义词
  - 删除文件：`src/graphs/nodes/synonym_generation_node.py`、`config/synonym_generation_llm_cfg.json`
  - 简化流程：`发明点特征词精炼` → `关键词筛选`（直连，去掉条件分支）
- **客体精确度升级（淘宝品类级）**：
  - 标准：在淘宝搜索该词时，出现的是同一类产品才算达标
  - ✅ 「跑步机」→ 搜出来都是跑步机
  - ❌ 「健身器材」→ 搜出来有跑步机、哑铃、瑜伽垫…
  - 判断方法：「淘宝搜索验证法」
- **关键词体系全面消费者化**：
  - 筛选节点增加排除标准：纯专利书面语
  - 组合节点改用消费者搜索语言组合
- **问题**：初步提取的核心术语过于泛化（如"视频显示"、"引导影像"），无法体现核心发明点
- **解决方案**：在关键词提取后增加精炼步骤，由大模型二次判断并精炼
- **精炼逻辑**：
  1. 逐一评估初步术语是否真正体现发明点
  2. 对泛化术语：结合发明内容、权利要求、背景技术，总结出更精准的特征词
  3. 对精准术语：保留不变
  4. 示例："视频显示" → "可移动影像显示"，"引导影像" → "多角度影像显示"
- **新增文件**：
  - `src/graphs/nodes/invention_point_refinement_node.py` - 精炼节点
  - `config/invention_point_refinement_llm_cfg.json` - 精炼节点配置
- **修改文件**：
  - `src/graphs/state.py` - 新增 InventionPointRefinementInput/Output
  - `src/graphs/loop_graph.py` - 在 keyword_extraction 和 synonym_generation 之间插入精炼节点

### 2025-01-XX: 优化产品客体提取逻辑 & 统一模型
- **产品客体提取优化**：
  1. 支持多客体：一项专利可能有多个目标对象（如同时涉及跑步机和椭圆机）
  2. 两步判断逻辑：技术领域已精确 → 直接提取；技术领域过于宽泛 → 结合发明内容进一步明确
  3. 客体必须精确到具体产品（跑步机 ✅ vs 健身器材 ❌）
  4. 输出格式从字符串改为 JSON 数组
  5. 节点输入增加 `invention_content` 字段用于回溯
- **统一模型**：所有节点默认模型统一为 `glm-5-0-260211`
- **修改文件**：
  - `config/product_object_extraction_llm_cfg.json` - 重写 SP/UP
  - `src/graphs/nodes/product_object_extraction_node.py` - 适配多客体逻辑
  - `src/graphs/state.py` - `product_object` 改为 `List[str]`
  - `src/graphs/nodes/keyword_combination_node.py` - 适配列表类型
  - 所有节点默认模型统一更新

### 2024-01-XX: 新增关键词筛选和组合节点
- **新增功能**：
  1. **关键词筛选节点** (`keyword_filtering_node`)：排除宽泛、无法体现核心发明点的关键词（如壳体、按键、外壳等）
  2. **关键词组合节点** (`keyword_combination_node`)：生成两种类型的检索关键词：
     - 权利要求人+同款+客体名称
     - 核心发明点+客体名称
- **修改文件**：
  - `src/graphs/state.py` - 新增状态定义
  - `src/graphs/nodes/keyword_filtering_node.py` - 新节点
  - `src/graphs/nodes/keyword_combination_node.py` - 新节点
  - `config/keyword_filtering_llm_cfg.json` - 新配置
  - `config/keyword_combination_llm_cfg.json` - 新配置
  - `src/graphs/loop_graph.py` - 更新工作流编排
  - `src/graphs/nodes/result_assembly_node.py` - 适配新输入

### 2024-01-XX: 修复飞书多维表格创建表错误 (第二次修复)
- **问题**：`[1254001] WrongRequestBody`
- **根本原因**：飞书 API 创建数据表的请求体格式不符合规范
- **错误格式**：
  ```json
  {
    "table_name": "表名",
    "fields": [...]
  }
  ```
- **正确格式**：
  ```json
  {
    "table": {
      "name": "表名",
      "fields": [...]
    }
  }
  ```
- **修改文件**：`src/tools/feishu_bitable.py` - 修复 `create_table` 方法的请求体结构

### 2024-01-XX: 第一次修复尝试
- **问题**：`TABLE_CREATE_ERROR: WrongRequestBody`
- **尝试方案**：将字段名从中文改为英文
- **结果**：问题未解决，发现是请求体结构问题

### 架构重构
- 将子图中的所有处理环节展开为独立节点，方便单独调试 Prompt
- 新增 `record_dispatch_node.py` 和 `result_collect_node.py`
- 修复 LangGraph 递归限制问题（动态计算 recursion_limit）

---

## 输入输出说明

### 输入
- **feishu_url**：飞书多维表格链接（必填）
  - 格式：`https://[企业域名].feishu.cn/base/[app_token]`

### 输出
- **app_token**：飞书多维表格 App Token
- **keywords_table_id**：关键词结果表 ID
- **keywords_count**：生成的关键词数量
- **exception_type**：异常类型（如果有）
- **exception_message**：异常消息（如果有）
