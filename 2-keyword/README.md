# 专利关键词生成模块

## 版本信息
- **当前版本**: v1.2.0
- **最后更新**: 2025-01-XX
- **维护者**: Coze Coding Team

## 项目简介
从飞书多维表格读取专利数据（支持三表关联：数据表、权利要求表、附图表），使用大模型提取用于商品检索的核心关键词，并通过智能筛选和组合生成高价值的检索关键词，最后写入新的飞书数据表。

## 主要功能

### 1. 数据读取
- 解析飞书多维表格链接
- 自动识别和加载三个关联表（数据表、权利要求表、附图表）
- 使用大模型智能匹配字段

### 2. 关键词提取流程
- **产品客体提取**：从技术领域识别产品类型
- **发明点提炼**：从权利要求和说明书中提取核心技术特征
- **关键词提取**：从权利要求中提取核心术语
- **同义词生成**：为核心术语生成同义词候选
- **关键词筛选**：排除宽泛、无法体现核心发明点的关键词
- **关键词组合**：生成两种类型的检索关键词：
  - 权利要求人+同款+客体名称
  - 核心发明点+客体名称

### 3. 结果输出
- 自动创建飞书多维表格的子表
- 批量写入关键词结果
- 支持大规模数据处理（动态调整递归限制）

## 快速开始

### 本地运行
```bash
# 运行完整流程
bash scripts/local_run.sh -m flow

# 运行单个节点
bash scripts/local_run.sh -m node -n node_name

# 启动HTTP服务
bash scripts/http_run.sh -m http -p 5000
```

### 输入参数
- **feishu_url**：飞书多维表格链接（必填）
  - 格式：`https://[企业域名].feishu.cn/base/[app_token]`

### 输出结果
- **app_token**：飞书多维表格 App Token
- **keywords_table_id**：关键词结果表 ID
- **keywords_count**：生成的关键词数量
- **exception_type**：异常类型（如果有）
- **exception_message**：异常消息（如果有）

## 架构设计

### 主图流程
```
飞书链接解析 → 飞书数据加载 → 数据整合分析 → 记录循环处理 → 关键词写入
```

### 子图流程（每条记录）
```
记录分发 → 输入验证 → 产品客体提取 → 发明点提炼 → 
关键词提取 → 同义词生成 → 关键词筛选 → 关键词组合 → 
结果组装 → 结果收集 → 循环判断
```

## 节点说明

### 主图节点
| 节点名 | 类型 | 功能描述 | 配置文件 |
|-------|------|---------|---------|
| feishu_url_parser | task | 解析飞书多维表格链接 | - |
| feishu_data_loader | task | 加载所有数据表 | - |
| data_integration | agent | 分析三个表的关系 | `config/data_integration_llm_cfg.json` |
| record_process_loop | looparray | 循环处理每条记录 | - |
| keyword_writer | task | 写入关键词到飞书 | - |

### 子图节点
| 节点名 | 类型 | 功能描述 | 配置文件 |
|-------|------|---------|---------|
| record_dispatch | task | 分发记录 | - |
| input_validation | task | 验证输入 | - |
| product_object_extraction | agent | 提取产品客体 | `config/product_object_extraction_llm_cfg.json` |
| invention_point_extraction | agent | 提炼发明点 | `config/invention_point_extraction_llm_cfg.json` |
| keyword_extraction | agent | 提取关键词 | `config/keyword_extraction_llm_cfg.json` |
| synonym_generation | agent | 生成同义词 | `config/synonym_generation_llm_cfg.json` |
| keyword_filtering | agent | 筛选关键词 | `config/keyword_filtering_llm_cfg.json` |
| keyword_combination | agent | 组合关键词 | `config/keyword_combination_llm_cfg.json` |
| result_assembly | task | 组装结果 | - |
| result_collect | task | 收集结果 | - |

## 版本历史

### v1.2.0 (2025-01-XX)
**新增功能**：
- 关键词筛选节点：排除宽泛、无法体现核心发明点的关键词
- 关键词组合节点：生成两种类型的检索关键词（权利人+同款+客体 / 发明点+客体）

**修复问题**：
- 修复飞书 API 创建数据表请求体格式错误

### v1.1.0 (2025-01-XX)
**架构优化**：
- 将子图中的所有处理环节展开为独立节点
- 新增记录分发和结果收集节点
- 修复 LangGraph 递归限制问题

### v1.0.0 (2025-01-XX)
**初始版本**：
- 基础关键词提取功能
- 飞书多维表格集成
- 三表关联支持

## 配置说明

### 可调整的配置文件
| 配置文件 | 作用 | 调整建议 |
|---------|------|---------|
| `config/data_integration_llm_cfg.json` | 三表关联分析 | 调整表识别规则 |
| `config/product_object_extraction_llm_cfg.json` | 产品客体提取 | 调整产品类别识别规则 |
| `config/invention_point_extraction_llm_cfg.json` | 发明点提炼 | 调整发明点提取规则 |
| `config/keyword_extraction_llm_cfg.json` | 关键词提取 | 调整关键词提取规则（核心） |
| `config/synonym_generation_llm_cfg.json` | 同义词生成 | 调整同义词生成策略 |
| `config/keyword_filtering_llm_cfg.json` | 关键词筛选 | 调整筛选标准（排除宽泛词汇） |
| `config/keyword_combination_llm_cfg.json` | 关键词组合 | 调整组合策略 |

### 调整方法
编辑对应的 JSON 配置文件，修改 System Prompt（sp）和 User Prompt（up）来调整模型行为。

## 异常处理

系统定义了以下异常类型：
- `EMPTY_URL` - 飞书链接为空
- `INVALID_URL_FORMAT` - 无法解析 app_token
- `FEISHU_API_ERROR` - 飞书 API 调用失败
- `NO_TABLES_FOUND` - 没有找到数据表
- `DATA_LOAD_ERROR` - 数据加载错误
- `DATA_INTEGRATION_ERROR` - 数据整合错误
- `EMPTY_CLAIM_TEXT` - 权利要求文本为空
- `NO_KEYWORD_EXTRACTED` - 无法提取关键词
- `TABLE_CREATE_ERROR` - 创建数据表失败
- `KEYWORD_WRITE_ERROR` - 写入关键词失败

## 技术栈
- Python 3.x
- LangGraph 1.0
- Pydantic
- Jinja2
- 飞书多维表格 API
- 大语言模型（豆包 Doubao）

## 许可证
内部使用
