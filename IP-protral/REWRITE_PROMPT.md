# 专利侵权自动识别系统 — 本地化改造 Prompt

> **使用方式**：将此文件 + 项目完整源码 + 4个工作流导出JSON 一并提供给 AI 编程助手（Cursor/Claude Code/Windsurf），指示："请按照此文档将远程工作流调用改造为本地LLM调用"。

---

## 一、改造目标

将当前依赖扣子平台远程工作流的架构，改造为**全部本地执行**的架构：

```
改造前（5个系统，4次远程调用）：
Next.js 应用 → coze.site/run(模块1) → coze.site/run(模块2) → coze.site/run(模块3) → coze.site/run(模块4)
                  ↓ 飞书表格            ↓ 飞书表格            ↓ 飞书表格            ↓ 飞书表格

改造后（1个系统，0次远程调用）：
Next.js 应用
├─ 前端页面（不动）
├─ module1() → LLM API (本地)
├─ module2() → LLM API (本地)
├─ module3() → 搜索API + LLM API (本地)
├─ module4() → LLM API (本地)
└─ PostgreSQL 数据库（模块间直接传参，不走飞书）
```

### 改造范围

| 文件 | 动作 | 说明 |
|------|------|------|
| `src/lib/workflow-client.ts` | **删除** | 远程调用层，全部替换 |
| `src/lib/feishu-client.ts` | **删除** | 飞书数据通道，不再需要 |
| `src/app/api/feishu-read/route.ts` | **删除** | 飞书读取接口 |
| `src/components/feishu-config.tsx` | **删除** | 飞书凭证输入组件 |
| `src/lib/modules/llm-client.ts` | **新建** | LLM API 调用封装 |
| `src/lib/modules/module1-patent-parse.ts` | **新建** | 专利文本解析（替代远程模块1） |
| `src/lib/modules/module2-industry.ts` | **新建** | 行业识别（替代远程模块2） |
| `src/lib/modules/module3-keywords.ts` | **新建** | 关键词生成（替代远程模块2的行业路由部分） |
| `src/lib/modules/module4-product-search.ts` | **新建** | 商品检索（替代远程模块3） |
| `src/lib/modules/module5-comparison.ts` | **新建** | 特征比对（替代远程模块4） |
| `src/app/api/analyze/route.ts` | **改造** | 流水线从远程调用改为本地函数调用 |
| `src/lib/analysis-store.ts` | **简化** | 去掉飞书相关逻辑 |
| `src/lib/types.ts` | **微调** | 去掉飞书相关类型，调整模块定义 |
| `src/app/page.tsx` | **微调** | 去掉预热步骤，调整步骤显示 |
| `src/components/analysis-progress.tsx` | **微调** | 同上 |
| `src/app/results/page.tsx` | **微调** | 去掉飞书导入功能 |
| `src/hooks/use-analysis.ts` | **不动** | 轮询逻辑通用 |
| `src/app/api/upload/route.ts` | **改造** | 从S3改为本地文件存储 |
| `src/app/results/[productId]/page.tsx` | **不动** | 比对详情页通用 |
| `src/components/upload-form.tsx` | **不动** | 上传表单通用 |
| `src/components/product-card.tsx` | **不动** | 商品卡片通用 |
| `src/components/claim-chart-table.tsx` | **不动** | 比对表格通用 |

---

## 二、环境变量变更

```env
# ===== 新增 =====
# LLM API（豆包/字节火山引擎）
DOUBAO_API_KEY=your_doubao_api_key
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3

# 搜索 API（商品检索用，二选一）
BING_API_KEY=your_bing_api_key
BING_BASE_URL=https://api.bing.microsoft.com

# ===== 删除 =====
# 以下全部不再需要：
# MODULE1_API_URL / MODULE1_API_TOKEN
# MODULE2_API_URL / MODULE2_API_TOKEN
# MODULE2_FITNESS_API_URL / MODULE2_FITNESS_API_TOKEN
# MODULE2_HOME_APPLIANCES_API_URL / MODULE2_HOME_APPLIANCES_API_TOKEN
# MODULE3_API_URL / MODULE3_API_TOKEN
# MODULE4_API_URL / MODULE4_API_TOKEN
# FEISHU_APP_ID / FEISHU_APP_SECRET
# COZE_BUCKET_ENDPOINT_URL / COZE_BUCKET_NAME
```

### 豆包 API Key 申请方式

1. 访问 [火山引擎控制台](https://console.volcengine.com/ark)
2. 开通「方舟」(ARK) 服务
3. 创建推理接入点，选择模型 `doubao-seed-2-0-pro-260215`
4. 获取 API Key

---

## 三、LLM 客户端封装 (`src/lib/modules/llm-client.ts`)

```typescript
/**
 * LLM 调用客户端 — 封装豆包 API（OpenAI 兼容接口）
 * 
 * 模型选择策略：
 * - 专利解析/关键词生成/特征比对：doubao-seed-2-0-pro（高精度）
 * - 行业识别：doubao-seed-2-0-mini（追求速度）
 * - 商品信息提取：doubao-seed-2-0-lite（批量处理，性价比）
 */
```

核心函数：
- `callLLM(prompt, options)` — 通用 LLM 调用
- `callLLMWithRetry(prompt, options, maxRetries)` — 带重试的调用（指数退避）
- `parseJSONResponse(rawText)` — 从 LLM 响应中提取 JSON

API 调用方式（OpenAI 兼容）：
```
POST https://ark.cn-beijing.volces.com/api/v3/chat/completions
Authorization: Bearer <DOUBAO_API_KEY>
Content-Type: application/json

{
  "model": "doubao-seed-2-0-pro-260215",
  "messages": [{"role": "user", "content": "..."}],
  "response_format": {"type": "json_object"},
  "temperature": 0.1
}
```

---

## 四、5 个本地模块详细规范

### 模块1: 专利文本解析 (`module1-patent-parse.ts`)

**替代**：原远程调用 `https://zzctsm7xqm.coze.site/run`

**输入**：
```typescript
interface Module1Input {
  fileUrl?: string;      // 专利文件 URL
  fileType?: 'pdf' | 'image' | 'text';
  textContent?: string;  // 文本模式直接传入
}
```

**输出**：
```typescript
interface PatentParseResult {
  patentTitle: string;
  patentNumber: string;
  abstract: string;
  technicalField: string;
  classificationCode: string;
  claims: Claim[];
  description: string;
}

interface Claim {
  claimNumber: number;
  claimText: string;
  claimType: 'independent' | 'dependent';
  dependentOn?: number;
  elements: string[];    // 技术特征拆解
}
```

**实现逻辑**：
1. **文本模式**：直接将文本传给 LLM 解析
2. **PDF 模式**：先用 `pdf-parse` 提取文本，再传给 LLM
3. **图片模式**：用 LLM 视觉理解能力解析（发送图片 URL）
4. LLM Prompt 核心要求（从工作流JSON中提取的原始提示词，此处需替换为实际工作流的提示词）：

```
你是一位专利分析专家。请分析以下专利文本，提取结构化信息：

1. 专利标题
2. 专利号
3. 摘要
4. 技术领域
5. 分类号（IPC分类）
6. 说明书主要内容
7. 逐条提取权利要求：
   - 权利要求编号
   - 权利要求原文
   - 类型（独立权利要求/从属权利要求）
   - 如果是从属权利要求，从属于第几条
   - 拆解该权利要求包含的技术特征（elements）

请以 JSON 格式输出。
```

**注意**：以上是通用提示词。实际实现时，请参考从扣子平台导出的模块1工作流JSON中的原始提示词，那个更精确。

---

### 模块2: 行业识别 (`module2-industry.ts`)

**替代**：原 `detectIndustry()` 函数中用 FetchClient + LLM 的逻辑

**输入**：
```typescript
interface Module2Input {
  claims: Claim[];
  technicalField: string;
  classificationCode: string;
  abstract: string;
}
```

**输出**：
```typescript
interface IndustryDetectionResult {
  industry: 'fitness_equipment' | 'home_appliances' | 'general';
  confidence: number;      // 0-1
  reasoning: string;
  industryPrompt: string;  // 行业特定提示词
}
```

**行业提示词库**（从工作流JSON中提取）：

```typescript
const INDUSTRY_PROMPTS: Record<IndustryType, string> = {
  fitness_equipment: `请针对健身器材行业生成关键词。重点关注：运动类型(有氧/力量/拉伸)、目标肌群、使用场景(家用/商用)、产品形态(跑步机/椭圆机/划船机等)。搜索平台优先选择：京东、天猫、亚马逊、迪卡侬。`,
  
  home_appliances: `请针对家用电器行业生成关键词。重点关注：产品功能(清洁/烹饪/制冷/洗涤)、使用场景(厨房/客厅/卧室)、智能化程度、能效等级。搜索平台优先选择：京东、天猫、苏宁、国美。`,
  
  general: `请生成通用的技术关键词。重点关注：核心技术特征、功能描述、应用场景。搜索平台优先选择：京东、天猫、1688、亚马逊。`,
};
```

**实现逻辑**：
1. 用轻量模型 `doubao-seed-2-0-mini` 做分类
2. Prompt 包含权利要求摘要、技术领域、分类号
3. 置信度 < 0.6 回退到 `general`
4. 返回行业 + 对应的行业提示词

---

### 模块3: 关键词生成 (`module3-keywords.ts`)

**替代**：原远程调用模块2（3个行业变体的 coze.site/run）

**输入**：
```typescript
interface Module3Input {
  claims: Claim[];
  industry: IndustryType;
  industryPrompt: string;
  abstract: string;
}
```

**输出**：
```typescript
interface KeywordGenerationResult {
  keywords: SearchKeyword[];
}

interface SearchKeyword {
  keyword: string;        // 主关键词
  modifiers: string[];    // 修饰词
  platform: string;       // 推荐搜索平台
  priority: number;       // 优先级 1-5
}
```

**实现逻辑**：
1. 用 `doubao-seed-2-0-pro` 生成关键词
2. Prompt 包含：全部权利要求技术特征 + 行业提示词
3. 要求生成 5-10 个关键词
4. 去重（相似度 > 0.8 的合并）

---

### 模块4: 商品信息检索 (`module4-product-search.ts`)

**替代**：原远程调用 `https://sk2jw6vshq.coze.site/run`

**输入**：
```typescript
interface Module4Input {
  keywords: SearchKeyword[];
}
```

**输出**：
```typescript
interface ProductSearchResult {
  products: ProductInfo[];
}

interface ProductInfo {
  id: string;
  name: string;
  platform: string;
  price: string;
  description: string;
  imageUrl: string;
  productUrl: string;
  matchedKeywords: string[];
}
```

**实现逻辑**：
1. 对每个关键词调用搜索 API（Bing Web Search）
2. 搜索结果用 LLM 提取结构化商品信息
3. 去重（商品名相似度 > 0.8 的合并）
4. 每个关键词 Top 5，最多 50 个商品

**搜索 API 调用**：
```typescript
// Bing Web Search API
const searchUrl = `https://api.bing.microsoft.com/v7.0/search?q=${encodeURIComponent(query)}&mkt=zh-CN&count=10`;
const response = await fetch(searchUrl, {
  headers: { 'Ocp-Apim-Subscription-Key': process.env.BING_API_KEY }
});
```

**备选方案**：如果不想用 Bing API，可以直接用 LLM 的联网搜索能力（豆包模型自带联网功能），在 prompt 中要求 LLM 搜索商品信息。

---

### 模块5: 技术特征比对 (`module5-comparison.ts`)

**替代**：原远程调用 `https://36yvrn7jt4.coze.site/run`

**输入**：
```typescript
interface Module5Input {
  claims: Claim[];
  products: ProductInfo[];
}
```

**输出**：
```typescript
interface ComparisonResult {
  comparisons: ProductComparison[];
}

interface ProductComparison {
  productId: string;
  productName: string;
  overallConclusion: 'infringing' | 'suspected' | 'non_infringing';
  overallConfidence: number;
  claimComparisons: ClaimComparison[];
}

interface ClaimComparison {
  claimNumber: number;
  claimText: string;
  elements: ElementComparison[];
  claimConclusion: 'identical' | 'equivalent' | 'different' | 'missing';
}

interface ElementComparison {
  element: string;
  patentDescription: string;
  productImplementation: string;
  conclusion: 'identical' | 'equivalent' | 'different' | 'missing';
  confidence: number;
  reasoning: string;
}
```

**实现逻辑**：
1. **并行比对**：每个商品独立比对，用 `Promise.allSettled` 并行
2. 每个商品的比对调用一次 LLM，Prompt 包含：
   - 专利的全部权利要求和技术特征
   - 该商品的描述信息
3. LLM Prompt 核心要求（参考工作流JSON中的原始提示词）：

```
你是一位专利分析专家，请对以下商品进行技术特征比对分析。

【专利权利要求】
{claims}

【商品信息】
名称：{productName}
描述：{productDescription}

请逐条权利要求、逐个技术特征进行比对：
- identical(相同)：商品实现与专利特征完全一致
- equivalent(等同)：商品实现与专利特征实质相同，仅形式不同
- different(不同)：商品实现与专利特征存在本质区别
- missing(缺失)：商品缺少该技术特征

注意：只做事实比对，不做法律结论判断。每个特征必须给出判断理由。

请以 JSON 格式输出。
```

4. **综合判定规则**（非 LLM 判断，代码逻辑）：
   - 所有独立权利要求的特征都 identical/equivalent → `infringing`
   - 存在 equivalent 但无 missing/different → `suspected`
   - 存在 missing 或 different → `non_infringing`

---

## 五、流水线改造 (`src/app/api/analyze/route.ts`)

### 改造前

```typescript
// 远程调用，5-6分钟超时，504容错，飞书轮询...
const result1 = await callModuleApi(module1Config, payload1);
const industry = await detectIndustry(feishuUrl, textContent);
const result2 = await runModule2(feishuUrl, industry);
const result3 = await callModuleApi(module3Config, payload3);
const result4 = await runModule4(feishuUrl);
```

### 改造后

```typescript
import { parsePatent } from '@/lib/modules/module1-patent-parse';
import { detectIndustry } from '@/lib/modules/module2-industry';
import { generateKeywords } from '@/lib/modules/module3-keywords';
import { searchProducts } from '@/lib/modules/module4-product-search';
import { compareFeatures } from '@/lib/modules/module5-comparison';

async function executePipeline(sessionId: string, input: AnalysisInput) {
  // 步骤1：专利文本解析
  updateStep(sessionId, 1, 'running');
  const patentResult = await parsePatent(input);
  updateStep(sessionId, 1, 'completed');
  
  // 步骤2：行业识别
  updateStep(sessionId, 2, 'running');
  const industryResult = await detectIndustry(patentResult);
  updateStep(sessionId, 2, 'completed');
  
  // 步骤3：关键词生成
  updateStep(sessionId, 3, 'running');
  const keywordResult = await generateKeywords(patentResult, industryResult);
  updateStep(sessionId, 3, 'completed');
  
  // 步骤4：商品检索
  updateStep(sessionId, 4, 'running');
  const productResult = await searchProducts(keywordResult.keywords);
  updateStep(sessionId, 4, 'completed');
  
  // 步骤5：特征比对
  updateStep(sessionId, 5, 'running');
  const comparisonResult = await compareFeatures(patentResult.claims, productResult.products);
  updateStep(sessionId, 5, 'completed');
  
  // 步骤6：结果汇总（纯数据转换）
  updateStep(sessionId, 6, 'running');
  const results = compileResults(patentResult, industryResult, keywordResult, productResult, comparisonResult);
  updateStep(sessionId, 6, 'completed');
  
  // 保存结果
  await updateResults(sessionId, results);
}
```

### 改造后的好处

- **无远程调用**：不存在 504 超时问题
- **无预热**：本地函数调用，毫秒级
- **无飞书**：模块间直接传参，不走表格
- **进度实时**：每个模块完成即更新数据库
- **超时自控**：每个模块自己设超时，不再受代理层限制

---

## 六、文件上传改造 (`src/app/api/upload/route.ts`)

### 改造前

上传到 S3 (coze-coding-dev-sdk S3Storage)

### 改造后

```typescript
import { writeFile, mkdir } from 'fs/promises';
import path from 'path';

export async function POST(request: Request) {
  const formData = await request.formData();
  const file = formData.get('file') as File;
  
  const bytes = await file.arrayBuffer();
  const buffer = Buffer.from(bytes);
  
  // 保存到本地
  const uploadDir = path.join(process.cwd(), 'public', 'uploads');
  await mkdir(uploadDir, { recursive: true });
  
  const fileName = `${Date.now()}-${file.name}`;
  const filePath = path.join(uploadDir, fileName);
  await writeFile(filePath, buffer);
  
  const fileUrl = `/uploads/${fileName}`;
  
  return Response.json({ success: true, fileId: fileName, fileUrl });
}
```

---

## 七、步骤数调整

### 改造前（6步，含预热）

0. 预热工作流服务 ← 删除
1. 专利文本解析
2. 行业识别与路由
3. 技术关键词生成
4. 商品信息检索
5. 技术特征比对
6. 读取分析结果 ← 从"飞书读取"改为"结果汇总"

### 改造后（6步，无需预热）

1. 专利文本解析
2. 行业识别与路由
3. 技术关键词生成
4. 商品信息检索
5. 技术特征比对
6. 结果汇总

步骤数量不变，但删除了预热步骤，步骤6从"飞书读取"改为"纯数据转换"。

---

## 八、前端微调

### `src/app/page.tsx`

- 删除预热相关文案
- 步骤6名称从"读取分析结果"改为"结果汇总"
- isCompleted 检查步骤 1-5

### `src/components/analysis-progress.tsx`

- 无需改动（步骤从 WORKFLOW_MODULES 动态读取）

### `src/app/results/page.tsx`

- 删除 FeishuConfig 组件和飞书导入逻辑
- 删除 importedProducts / importedComparisons 状态
- 数据全部从 session.results 读取

### `src/hooks/use-analysis.ts`

- 无需改动（通用轮询逻辑）

---

## 九、4 个工作流 JSON 的使用方式

从扣子平台导出的工作流 JSON 包含：

1. **提示词（Prompt）**：每个 LLM 节点的 system prompt 和 user prompt — 这是最有价值的部分，直接复制到本地模块中使用
2. **节点连线**：数据流向 — 帮助理解模块间数据传递
3. **条件分支**：行业路由逻辑 — 转为 if/switch 代码
4. **循环节点**：批量处理 — 转为 for 循环或 Promise.all

**操作步骤**：
1. 在扣子平台上打开每个工作流
2. 右上角菜单 → 导出/下载 JSON
3. 保存为 `workflows/module1.json`, `module2.json`, `module3.json`, `module4.json`
4. 重写时，优先从 JSON 中提取原始提示词，替换本文档中的通用提示词

---

## 十、部署到本地服务器

### 1. 安装依赖

```bash
# Node.js 20+
# PostgreSQL 15+
pnpm install
```

### 2. 初始化数据库

```bash
# 创建数据库
createdb patent_analysis

# 运行迁移（如果用 Drizzle）
pnpm drizzle-kit push
```

### 3. 配置环境变量

```bash
cp .env.example .env.local
# 编辑 .env.local 填入 DOUBAO_API_KEY, BING_API_KEY, DATABASE_URL
```

### 4. 启动

```bash
# 开发
pnpm dev

# 生产
pnpm build
pnpm start
```

### 5. PM2 守护

```bash
pm2 start pnpm --name "patent-analysis" -- start
pm2 save
pm2 startup
```

### 6. Nginx 反向代理（绑定域名）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    client_max_body_size 50M;    # 支持大文件上传

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 900s;  # 15分钟，适配长时间分析
    }
}
```

### 7. HTTPS（Let's Encrypt）

```bash
sudo certbot --nginx -d your-domain.com
```

---

## 十一、改造后的对比

| 对比项 | 改造前（扣子平台） | 改造后（本地化） |
|--------|------------------|----------------|
| 远程调用 | 4次 coze.site/run | 0次 |
| 超时问题 | 5分钟504 | 自己控制，无限制 |
| 冷启动 | 20-30秒需预热 | 无 |
| 数据通道 | 飞书多维表格 | 直接传参（内存） |
| LLM | 扣子平台内置 | 豆包 API 直调（同一个模型） |
| 搜索 | 扣子平台内置 | Bing API / LLM 联网 |
| 文件存储 | S3 | 本地文件系统 |
| 数据库 | Supabase (云) | 本地 PostgreSQL |
| 所需 API Key | 6个模块Token + 飞书 | 1个LLM Key + 1个搜索Key |
| 部署依赖 | 扣子平台 + Supabase + 飞书 | 只有你自己的服务器 |
| 代码改动量 | — | ~5个新文件 + 3个改造文件 + 删4个文件 |

---

## 十二、开发顺序

1. **新建 `llm-client.ts`** — LLM 调用封装（最基础）
2. **新建 `module1-patent-parse.ts`** — 专利解析
3. **改造 `analyze/route.ts`** — 流水线从远程改为本地
4. **新建 `module2-industry.ts`** — 行业识别
5. **新建 `module3-keywords.ts`** — 关键词生成
6. **新建 `module4-product-search.ts`** — 商品检索
7. **新建 `module5-comparison.ts`** — 特征比对
8. **改造 `upload/route.ts`** — S3 改本地文件
9. **微调前端** — 删飞书组件，改步骤名称
10. **删除旧文件** — workflow-client.ts, feishu-client.ts 等
11. **测试 + 部署**
