# 专利解析模块（Patent Parsing Module）

## 项目概述
- **名称**: 专利解析模块
- **功能**: 将原始专利文本转化为结构化专利数据，供后续模块消费

## 模块定位
### ✅ 允许的职责
1. 接收并存储专利原始文本（事实层）
2. 识别并分离说明书各部分（背景技术、技术领域、发明内容等）
3. 拆分权利要求，标注独立/从属关系
4. **提取并保存专利附图**
5. **持久化解析结果到数据库**
6. 提供稳定、可索引的结构化输出

### ❌ 明确禁止的职责
- 总结专利保护范围
- 提炼"发明点""创新点"
- 判断权利要求的技术效果
- 判断是否为功能性限定
- 合并或简化权利要求语言

## 附图提取说明

### 支持的文档格式

| 格式 | 提取方式 | 说明 |
|-----|---------|------|
| **PDF** | PyMuPDF提取 | ✅ 推荐：直接提取图片文件并上传对象存储 |
| PNG/JPG/GIF | 直接上传 | 单个图片文件直接上传 |
| HTML | 正则提取URL | 从`<img>`标签提取图片URL并转存 |
| TXT | 正则提取URL | 从文本中提取图片URL并转存 |

### ⚠️ 已知问题与修复

**问题1**：URL 包含查询参数（如 `?sign=xxx`）时，文件扩展名解析错误，导致跳过 PDF 分支。

**修复**：在 `figure_extract_node` 中，解析扩展名前先移除 URL 查询参数。

```python
# 修复后的代码
clean_url = file_url.split('?')[0]
file_ext = os.path.splitext(clean_url)[1].lower()
```

**问题2**：飞书多维表格数据从第11行开始显示，前10行为空行。

**原因**：飞书创建多维表格/数据表时会自动生成10行空行，新写入的记录被追加到空行之后。

**修复**：在 `_setup_table_fields` 中新增 `_clear_default_records` 调用，写入数据前先删除所有默认空行。

**问题3**：PDF专利文档元数据（专利号、专利权人、申请日期等）无法提取。

**根因**：
1. `FileOps.extract_text` 对PDF文件返回错误信息字符串而非实际文本
2. URL查询参数中包含文件扩展名，但路径部分无扩展名，导致格式误判

**修复**：
1. `file_read_node`：PDF文件改用PyMuPDF直接提取文本；URL扩展名解析增加从查询参数(`file_path`)提取的逻辑
2. `structure_identify_node`：新增 `_extract_cn_patent_metadata` 函数，基于CN专利括号编号规则((21)申请号、(22)申请日、(30)优先权、(54)发明名称、(73)专利权人)用正则提取元数据，LLM结果为空时自动补充

### 为什么TXT文件返回空列表？

**原因**：TXT是纯文本格式，不包含图片文件或图片URL。

**解决方案**：
1. **推荐**：使用PDF格式的专利文档
2. 或使用包含图片URL的HTML文档
3. 或在TXT中包含可访问的图片URL链接

### 附图提取流程

```
专利文档输入
    ↓
判断文件格式
    ↓
┌───────┬───────┬───────┬───────┐
│ PDF   │ 图片   │ HTML  │ TXT   │
↓       ↓       ↓       ↓
PyMuPDF  直接   提取URL  提取URL
提取图片  上传   并转存   并转存
    ↓       ↓       ↓       ↓
    上传到对象存储 → 返回URL列表
```

## 节点清单

| 节点名 | 文件位置 | 类型 | 功能描述 | 分支逻辑 | 配置文件 |
|-------|---------|------|---------|---------|---------|
| file_read_node | `nodes/file_read_node.py` | task | 读取专利文档（PDF/TXT/HTML），提取原始文本 | - | - |
| file_error_check | `nodes/error_check_node.py` | condition | 检查文件读取错误，判断是否可继续 | "致命错误"→END, "继续解析"→structure_identify_node | - |
| structure_identify_node | `nodes/structure_identify_node.py` | agent | 识别说明书章节结构，提取权利要求书文本 | - | `config/structure_identify_llm_cfg.json` |
| claims_parse_node | `nodes/claims_parse_node.py` | agent | 解析权利要求，标注独立/从属关系，拆分句子单元 | - | `config/claims_parse_llm_cfg.json` |
| figure_extract_node | `nodes/figure_extract_node.py` | task | 从PDF/文档中提取附图，上传到对象存储 | - | - |
| database_save_node | `nodes/database_save_node.py` | task | 将解析结果持久化到Supabase数据库 | - | - |
| feishu_save_node | `nodes/feishu_save_node.py` | task | 将解析结果保存到飞书多维表格 | - | - |
| structured_output_node | `nodes/structured_output_node.py` | task | 生成标准JSON格式的结构化输出 | - | - |

**类型说明**: task(普通任务节点) / agent(大模型节点) / condition(条件分支)

## 工作流说明

### 执行流程
```
输入: patent_file, task_id
  ↓
file_read_node: 读取文档，提取原始文本
  ↓
file_error_check: 检查错误
  ↓ (无致命错误)
structure_identify_node: 识别文档结构（LLM辅助）
  ↓
┌─────────────────┬─────────────────┐
│                 │                 │
│ claims_parse_node  figure_extract_node │
│ (权利要求解析)     (附图提取)         │
└─────────────────┴─────────────────┘
  ↓
database_save_node: 保存到数据库
  ↓
feishu_save_node: 保存到飞书多维表格
  ↓
structured_output_node: 生成JSON输出
  ↓
输出: claims, specification, figures, metadata, errors, db_record_id, feishu_url
```

### 并行处理
- **权利要求解析**和**附图提取**并行执行
- 两个分支完成后汇入数据库保存节点

### 异常处理
- **文件读取失败**: 标记致命错误，终止解析
- **文档结构识别失败**: 使用正则规则降级方案
- **权利要求解析失败**: 使用正则规则降级方案
- **附图提取失败**: 记录错误，继续解析（可恢复）
- **数据库保存失败**: 记录错误，返回解析结果
- **所有错误**: 显式记录在输出结果的errors字段中

## 模块输入输出

### 输入结构
```json
{
  "patent_file": {
    "url": "文件路径或URL",
    "file_type": "document"
  },
  "task_id": "任务ID，用于追踪和重放"
}
```

### 输出结构
```json
{
  "claims": [
    {
      "claim_id": "权利要求编号",
      "claim_type": "INDEPENDENT 或 DEPENDENT",
      "claim_text": "权利要求完整原文",
      "parent_claim_id": "父权利要求编号（仅从属权利要求）",
      "sentence_units": ["句子1", "句子2"]
    }
  ],
  "specification": {
    "技术领域": "章节原文",
    "背景技术": "章节原文",
    "发明内容": "章节原文"
  },
  "figures": [
    {
      "figure_id": "附图编号",
      "figure_url": "对象存储URL",
      "figure_description": "附图说明",
      "storage_key": "存储key"
    }
  ],
  "metadata": {
    "patent_holder": "专利权人",
    "patent_number": "专利号",
    "application_date": "申请日期",
    "priority_date": "优先权日期",
    "title": "专利标题"
  },
  "errors": [
    {
      "error_type": "错误类型",
      "error_message": "错误描述",
      "is_recoverable": false
    }
  ],
  "task_id": "任务ID",
  "db_record_id": 123
}
```

## 数据库表结构

### patent_parse_records (主表)
| 字段 | 类型 | 说明 |
|-----|------|------|
| id | integer | 主键 |
| task_id | text | 任务ID（唯一） |
| patent_number | text | 专利号 |
| patent_holder | text | 专利权人 |
| title | text | 专利标题 |
| application_date | text | 申请日期 |
| priority_date | text | 优先权日期 |
| specification | json | 说明书章节内容 |
| parse_errors | json | 解析错误列表 |
| created_at | datetime | 创建时间 |

### patent_claims (权利要求表)
| 字段 | 类型 | 说明 |
|-----|------|------|
| id | integer | 主键 |
| record_id | integer | 关联主表ID |
| claim_id | text | 权利要求编号 |
| claim_type | text | 权利要求类型 |
| claim_text | text | 权利要求原文 |
| parent_claim_id | text | 父权利要求编号 |
| sentence_units | json | 句子单元列表 |
| created_at | datetime | 创建时间 |

### patent_figures (附图表)
| 字段 | 类型 | 说明 |
|-----|------|------|
| id | integer | 主键 |
| record_id | integer | 关联主表ID |
| figure_id | text | 附图编号 |
| figure_url | text | 附图URL |
| figure_description | text | 附图说明 |
| storage_key | text | 对象存储key |
| created_at | datetime | 创建时间 |

## LLM使用边界

### ✅ 允许的LLM用途
- 辅助识别权利要求边界
- 辅助说明书结构化拆分
- 所有LLM输出必须表现为"原文片段 + 结构标签"

### ❌ 禁止的LLM用途
- 解释权利要求含义
- 重写或总结权利要求
- 推断技术特征或功能

## 技能使用
- `structure_identify_node` 使用大语言模型技能
- `claims_parse_node` 使用大语言模型技能
- `figure_extract_node` 使用对象存储技能
- `database_save_node` 使用Supabase数据库技能
- `feishu_save_node` 使用飞书多维表格技能

## 测试文件
- 测试数据位置: `assets/mock/test_patent.txt`
- 测试命令: 使用 test_run 工具执行

## 设计目标验证
- ✅ 同一份专利文本多次解析输出完全一致（temperature=0.1）
- ✅ 法律人员可对照原文逐句核查解析结果（原文逐字保留）
- ✅ 工程人员可单独测试本模块（独立的输入输出定义）
- ✅ 附图已保存到对象存储，可通过URL访问
- ✅ 解析结果已持久化到数据库，可通过db_record_id查询

## 关键文件
- 状态定义: `src/graphs/state.py`
- 主图编排: `src/graphs/graph.py`
- 节点实现: `src/graphs/nodes/`
- LLM配置: `config/`
- 数据库模型: `src/storage/database/shared/model.py`
- 数据库客户端: `src/storage/database/supabase_client.py`

## 依赖说明
### PDF图片提取（可选）
- 如需从PDF提取图片，请安装PyMuPDF：`pip install PyMuPDF`
- 未安装时，会跳过PDF图片提取，不影响其他功能

## 更新日志
- **v2.0**: 新增附图提取和数据库保存功能
- **v1.0**: 基础专利解析功能
