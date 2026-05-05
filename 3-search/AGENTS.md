## 项目概述
- **名称**: 商品检索模块
- **功能**: 从 Postgres 数据库读取关键词，调用 Coze 工作流 API 搜索商品信息，将结果保存到 Postgres 数据库

### 环境变量
| 变量名 | 说明 | 默认值 |
|-------|------|--------|
| `COZE_SEARCH_API_URL` | Coze 工作流 API 地址 | `https://66vpykvvz2.coze.site/run` |
| `COZE_SEARCH_API_TOKEN` | Coze 工作流认证 Token | (必填) |
| `COZE_SEARCH_TIMEOUT` | 单次 API 调用超时（秒） | `120` |
| `COZE_MAX_CONCURRENT` | 并发调用最大数 | `5` |

### 节点清单
| 节点名 | 文件位置 | 类型 | 功能描述 | 分支逻辑 | 配置文件 |
|-------|---------|------|---------|---------|---------|
| entry | `graphs/graph.py` | task | 初始化全局状态，生成数据集ID | - | - |
| get_keywords | `graphs/graph.py` (wrapper) + `graphs/nodes/get_keywords_node.py` | task | 从 Postgres 的 keyword_records 表读取关键词 | - | - |
| coze_search | `graphs/graph.py` (wrapper) + `graphs/nodes/coze_search_node.py` | task | 调用 Coze 工作流 API，传入关键词搜索商品 | - | - |
| save_results | `graphs/graph.py` (wrapper) + `graphs/nodes/save_results_node.py` | task | 将商品数据写入 Postgres 数据库 | - | - |
| exit | `graphs/graph.py` | task | 输出最终结果 | - | - |

**类型说明**: task(task节点) / agent(大模型) / condition(条件分支) / looparray(列表循环) / loopcond(条件循环)

**Coze搜索节点说明**:
- 调用 Coze 工作流 API，传入关键词列表，获取商品搜索结果
- 请求格式：`{"keywords": ["关键词1", "关键词2"]}`
- 认证方式：Bearer Token（通过 `COZE_SEARCH_API_TOKEN` 环境变量配置）
- 优先批量调用（所有关键词合为一次请求），若批量无结果则退回逐关键词并发调用
- 并发调用受 `COZE_MAX_CONCURRENT` 信号量限制（默认5个）
- 自动解析 Coze 响应中的商品列表，兼容多种响应格式（JSON数组/嵌套对象/文本中提取）
- 返回结果自动按 URL 去重
- 字段映射：Coze 的 `title/summary/url/source/keyword` 等字段归一化为 `product_name/description/product_url/product_source/matched_keywords` 等

**保存结果节点说明**:
- 将商品数据写入 Postgres 的 search_runs 和 search_products 表
- search_runs 表记录检索运行的元信息（数据集ID、关键词统计等）
- search_products 表记录每个商品的详细信息（名称、URL、价格、品牌、制造商、图片等）
- platforms_queried 固定为 `["Coze工作流"]`

## 子图清单
| 子图名 | 文件位置 | 功能描述 | 被调用节点 |
|-------|---------|------|---------|
| - | - | 无子图 | - |

## 技能使用
- 节点`get_keywords`使用 Postgres 数据库技能
- 节点`coze_search`使用 Coze 工作流 API（HTTP 调用）
- 节点`save_results`使用 Postgres 数据库技能

## 工作流数据流
```
GraphInput (patent_record_id, analysis_session_id, input_keywords?)
    ↓
entry (初始化状态，生成数据集ID)
    ↓
get_keywords (从Postgres的keyword_records表读取关键词)
    ↓
coze_search (调用Coze工作流API搜索商品，批量或并发调用)
    ↓
save_results (写入Postgres数据库：search_runs + search_products表)
    ↓
exit (输出结果)
    ↓
GraphOutput (product_dataset_id, search_run_id, total_products_count, is_complete, ...)
```

## 数据库表结构
保存到 Postgres 的商品数据包含以下表：

### search_runs 表（检索运行记录）
| 字段名 | 类型 | 说明 |
|-------|------|------|
| id | int | 自增主键 |
| patent_record_id | int | 专利解析主记录ID |
| analysis_session_id | text | 分析会话ID |
| product_dataset_id | text | 数据集唯一标识 |
| retrieval_start_time | text | 检索开始时间 |
| successful_keywords_count | int | 成功检索的关键词数 |
| failed_keywords_count | int | 失败检索的关键词数 |
| total_products_count | int | 商品总数 |
| platforms_queried | json | 查询的平台列表 |
| is_complete | bool | 检索是否完整 |
| error_message | text | 错误信息 |

### search_products 表（商品记录）
| 字段名 | 类型 | 说明 |
|-------|------|------|
| id | int | 自增主键 |
| search_run_id | int | 关联search_runs.id |
| patent_record_id | int | 专利解析主记录ID |
| analysis_session_id | text | 分析会话ID |
| product_id | text | 商品唯一标识 |
| product_name | text | 商品标题 |
| product_url | text | 商品URL |
| product_source | text | 商品来源平台 |
| price | text | 商品价格 |
| brand | text | 品牌名称 |
| manufacturer | text | 制造商/工厂名称 |
| matched_keywords | text | 匹配的搜索关键词 |
| description | text | 商品描述文本 |
| picture | json | 图片URL列表 |
| raw_payload | json | 原始数据 |

## 输入输出定义

### 工作流输入
- `patent_record_id`: 专利解析主记录ID（必填，从模块2传入）
- `analysis_session_id`: 分析会话ID（可选，用于限定范围）
- `input_keywords`: 搜索关键词列表（可选，如不提供则从数据库读取）

### 工作流输出
- `product_dataset_id`: 本次检索数据集唯一标识
- `search_run_id`: 商品检索运行记录ID
- `total_products_count`: 检索到的商品总数
- `is_complete`: 检索是否完整
- `error_message`: 错误信息

## 使用示例

```json
{
  "patent_record_id": 1,
  "analysis_session_id": "sess_abc123"
}
```

或者手动指定关键词：
```json
{
  "patent_record_id": 1,
  "analysis_session_id": "",
  "input_keywords": ["计时器", "厨房用品", "定时器"]
}
```

## 上下游衔接

### 上游（模块2 - 关键词生成）
- 输入：`patent_record_id`，从 `keyword_records` 表读取关键词
- 依赖字段：`keyword_text`（搜索关键词文本，非空）

### 下游（模块4 - 权利要求比对）
- 输出：写入 `search_products` 表，模块4通过 `patent_record_id` 读取
- 模块4期望的字段：`product_id`、`product_name`、`description`、`picture`、`raw_payload` 等