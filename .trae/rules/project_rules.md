# 项目工作规则

## 1. 改完代码必须自动重启服务（无需用户手动操作）

用户在每次代码改动后，都期望**由 AI 自动**完成相关模块的重启/重载，而不是让用户去手动跑 pm2 命令。

### 1.1 服务结构（pm2 名称 → 端口 → 路径）

| pm2 名称 | 端口 | 路径 | 类型 | 重启方式 |
|---|---|---|---|---|
| `patent-1-patent-analysis` | 5101 | `1-patent-analysis` | Python HTTP | `pm2 reload <name>` |
| `patent-2-keyword` | 5102 | `2-keyword` | Python HTTP | `pm2 reload <name>` |
| `patent-2-keyword-fitness` | 5103 | `2-keyword-fitness` | Python HTTP | `pm2 reload <name>` |
| `patent-2-keyword-electra` | 5104 | `2-keyword-electra` | Python HTTP | `pm2 reload <name>` |
| `patent-3-search` | 5105 | `3-search` | Python HTTP | `pm2 reload <name>` |
| `patent-4-claim-chat` | 5106 | `4-claim-chat` | Python HTTP | `pm2 reload <name>` |
| `patent-web` | 3001 | `IP-protral` | Next.js (production) | `pm2 reload <name>` |

启动入口：[`ecosystem.config.cjs`](file:///Users/xiexiaoxiong/Documents/patent/ecosystem.config.cjs)

### 1.2 重启判定规则

修改文件后，按以下规则**必须**自动触发 `pm2 reload`，不要询问用户：

| 修改路径 | 必须 reload | 说明 |
|---|---|---|
| `4-claim-chat/src/**/*.py` | `patent-4-claim-chat` | 后端 Python 节点 |
| `4-claim-chat/config/**/*.json` | `patent-4-claim-chat` | 节点配置 / Prompt |
| `IP-protral/src/**/*.{ts,tsx}` | `patent-web` | 前端代码 |
| `IP-protral/src/lib/**` | `patent-web` | 类型/工具/服务层 |
| `IP-protral/src/components/**` | `patent-web` | 组件 |
| `IP-protral/src/app/**` | `patent-web` | 路由/页面/API |
| 其它 1/2/3 模块 Python 文件 | 对应模块 | 同理 |

### 1.3 重启执行流程

```bash
cd /Users/xiexiaoxiong/Documents/patent

# 1) Python 后端模块（按需 reload 修改过的那些）
pm2 reload patent-4-claim-chat

# 2) Next.js 前端（生产模式必须 reload 触发 next build）
pm2 reload patent-web

# 3) 验证
pm2 list | grep -E "patent-4-claim-chat|patent-web"
```

### 1.4 注意事项

- 用 `pm2 reload`（0-downtime），不要用 `pm2 restart`。
- 多个模块都改了就一次性全 reload，不要分多次询问。
- 如果 pm2 进程处于 `errored` / `stopped` 状态，用 `pm2 restart <name>`。
- reload 完成后告诉用户"已重启：xxx, yyy"即可，不要让用户再去执行任何命令。
- 涉及数据库的修复（如 `score_band` 重算）即使重启了 pm2 也仍然**需要用户用同一份数据重跑一次新分析**才能写入新字段——这一点要在回复时提醒。

## 2. 单元级染色与计分的设计原则（重要的领域知识）

特征级 vs 单元级必须严格分离，否则会出现整条特征被错误染色的问题：

1. **染色和计分的唯一来源是 token_units 数组里每个单元的 unit_status**，不是特征级 reasoning_type。
2. **score_band 不能只看 score 数字**：score=0 既可能是"明确不存在"（红），也可能是"信息不足"（黄）。判别条件：
   - `has_mismatch=true` → "明确不相同"（红）
   - `matched_length=0 && total_length>0 && has_mismatch=false` → "待确认"（黄）
3. **LLM 自检约束**（在 `analyze_features_llm_cfg.json` 的 prompt 里）：
   - `reasoning_type` 选了"文字直接公开/从图片中看出/结合文字和图片毫无疑义得出/根据功能推导得出" → 必须至少有一个 `unit_status='match'`
   - `reasoning_type` 选了"可判断不具有" → 必须至少有一个 `unit_status='mismatch'`
   - `reasoning_type` 选了"相关信息缺失" → 全部 unit 都必须是 `uncertain`
4. **代码层兜底**（在 `apply_rules_node.py` 的 `_upgrade_units_from_positive_reasoning`）：
   - 当 LLM 给正推理 + 全部 unit 都是 uncertain 时，从 reason 文本里识别被点名的最小单元并升级为 match
   - 升级前检查上下文 ±20 字符内不能含负面词（不存在/不具有/没有/未提/无法确认 等）
   - 升级会写 warning log 方便回溯

## 3. 其它约定

- 优先用专用工具（Read/Edit/Write/Glob/Grep）而非 `cat`/`grep`/`find`。
- 修改前先 Read，再 Edit。
- 一次任务涉及多个文件改动时，过程中维护 todo（TodoWrite）跟踪进度。
- Python 改完跑 `python3 -c "import ast; ast.parse(open('xxx').read())"` 验证语法。
- TypeScript 改完跑 `npx tsc --noEmit --skipLibCheck`（在 `IP-protral/` 目录下）验证。
- 回复时用与用户最近消息相同的语言（默认中文）。
