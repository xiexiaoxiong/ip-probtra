# projects

这是一个基于 [Next.js 16](https://nextjs.org) + [shadcn/ui](https://ui.shadcn.com) 的全栈应用项目，由扣子编程 CLI 创建。

## 正式首页专利 Agent

登录后的 `/` 是单一对话框专利工作台。输入区附件按钮旁提供“专利无效”和“专利侵权分析”两个
可独立切换的按钮；只有选中的类型才会启动相应后台工作流，两个都选时分别启动两条隔离任务，
两个都不选时只进行普通大模型问答。用户可以附上 PDF、DOC、DOCX、TXT，或在消息中提供专利
URL/正文。

- 按钮状态是后台工作流的唯一启动依据；文字意图和附件不能自行追加未选择的分析。
- 两个按钮均未选择时只调用问答模型，不创建侵权或无效任务。
- 专利侵权分析通过 `/api/analyze` 自动调用与专利分析实验室相同的四模块执行器；无效分析通过
  `/api/invalidity/investigations` 调用与十一模块实验室相同的 5209 后端。
- 对话、消息和工具运行状态按登录用户隔离持久化。侵权和无效分析都采用“启动回复 -> 过程卡 ->
  独立 Agent 结果回复”：过程卡只显示可核验工作动态、当前事项、进度和错误；侵权结果回复直接显示
  结论、统计、商品技术特征比对表以及完整结果/XLSX 入口。无效模块11结果回复显示逐独立权利要求
  结论、证据边界、律师可读文字和最多10篇对比文件的横向特征矩阵；第三部分从同一 session 导出
  XLSX，并为每篇已进入逐篇分析的对比文件分别建立一个 Claim Chart Sheet。
- 后台工具运行时，启动回复下方直接显示由持久化阶段状态生成的最近工作动态、当前事项和真实完成数：
  侵权链按四个业务模块、无效链按十一个律师模块展示；详细事项默认收起。状态每 3 秒从对应业务
  session 刷新，不按耗时伪造进度，也不展示或伪造模型内部隐藏推理。
- 无效任务到达可续跑的 gap `partial` 检查点时，工具卡直接弹出 30 秒确认；用户可立即开始下一轮，
  超时后按持久化 checkpoint、状态版本和幂等键自动开始一轮，最多仍为 5 个 gap 轮。关键日、证据
  日期资格、D1 等法律事实确认只显示人工入口，不会被倒计时自动决定。
- `/test/module-lab` 是 approved admin 的开发诊断页，不再对应另一套“测试服务”。管理员可从
  Agent 按 `?session=<analysis_session_id>` 打开同一任务的只读投影，查看同一 investigation 的
  claims、module runs、错误和报告；打开或刷新不会复制案件、恢复批次或启动新运行。
- 侵权 Agent 的 approved admin 过程卡同样提供“查看本次分析各模块”，进入
  `/test/product-pipeline?session=<analysis_session_id>`。该模式通过
  `/api/admin/patent-analysis/session/[id]` 精确恢复本次专利解析、关键词生成、商品检索、权利要求
  与商品比对的输入、运行记录、部分结果和错误；不会新建实验 session、重跑模块或改写旧会话，
  失败的商品检索仍可查看逐候选排除原因。
- Agent 侧边栏只保留两个测试入口：approved admin 可见 `/test/module-lab` 的 11 阶段无效诊断/
  实验室，`/test/product-pipeline` 是唯一的专利分析实验室。页面顶部统一输入一件专利，下面按
  “专利解析、关键词生成、商品检索、权利要求与商品比对”四个业务阶段逐级测试；每个阶段分别展示
  “本次输入、本次输出、结果”，阶段卡与长内容默认收起，不显示“模块1结果”等技术编号。
  旧 `/test` 自动跳转到该实验室；其他细分测试路由只供兼容和内部调试，不再作为普通用户导航入口。
- 旧的独立 `/invalidity` 上传页已经退役，兼容路由会返回 Agent。无效分析统一从 Agent 的
  “专利无效”开关启动；`/invalidity/results` 只保留历史兼容深链和导出能力，不再是新 Agent
  任务的正常结果出口，人工确认也在 Agent 内完成。

## 快速开始

### 启动开发服务器

```bash
pnpm dev
```

启动后，在浏览器中打开 [http://localhost:5000](http://localhost:5000) 查看应用。

开发服务器支持热更新，修改代码后页面会自动刷新。

### 构建生产版本

```bash
pnpm build
```

### 启动生产服务器

```bash
pnpm start
```

## 项目结构

```
src/
├── app/                      # Next.js App Router 目录
│   ├── layout.tsx           # 根布局组件
│   ├── page.tsx             # 首页
│   ├── globals.css          # 全局样式（包含 shadcn 主题变量）
│   └── [route]/             # 其他路由页面
├── components/              # React 组件目录
│   └── ui/                  # shadcn/ui 基础组件（优先使用）
│       ├── button.tsx
│       ├── card.tsx
│       └── ...
├── lib/                     # 工具函数库
│   └── utils.ts            # cn() 等工具函数
└── hooks/                   # 自定义 React Hooks（可选）

server/
├── index.ts                 # 自定义服务器入口
├── tsconfig.json           # Server TypeScript 配置
└── dist/                    # 编译输出目录（自动生成）
```

## 核心开发规范

### 1. 组件开发

**优先使用 shadcn/ui 基础组件**

本项目已预装完整的 shadcn/ui 组件库，位于 `src/components/ui/` 目录。开发时应优先使用这些组件作为基础：

```tsx
// ✅ 推荐：使用 shadcn 基础组件
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Input } from '@/components/ui/input';

export default function MyComponent() {
  return (
    <Card>
      <CardHeader>标题</CardHeader>
      <CardContent>
        <Input placeholder="输入内容" />
        <Button>提交</Button>
      </CardContent>
    </Card>
  );
}
```

**可用的 shadcn 组件清单**

- 表单：`button`, `input`, `textarea`, `select`, `checkbox`, `radio-group`, `switch`, `slider`
- 布局：`card`, `separator`, `tabs`, `accordion`, `collapsible`, `scroll-area`
- 反馈：`alert`, `alert-dialog`, `dialog`, `toast`, `sonner`, `progress`
- 导航：`dropdown-menu`, `menubar`, `navigation-menu`, `context-menu`
- 数据展示：`table`, `avatar`, `badge`, `hover-card`, `tooltip`, `popover`
- 其他：`calendar`, `command`, `carousel`, `resizable`, `sidebar`

详见 `src/components/ui/` 目录下的具体组件实现。

### 2. 路由开发

Next.js 使用文件系统路由，在 `src/app/` 目录下创建文件夹即可添加路由：

```bash
# 创建新路由 /about
src/app/about/page.tsx

# 创建动态路由 /posts/[id]
src/app/posts/[id]/page.tsx

# 创建路由组（不影响 URL）
src/app/(marketing)/about/page.tsx

# 创建 API 路由
src/app/api/users/route.ts
```

**页面组件示例**

```tsx
// src/app/about/page.tsx
import { Button } from '@/components/ui/button';

export const metadata = {
  title: '关于我们',
  description: '关于页面描述',
};

export default function AboutPage() {
  return (
    <div>
      <h1>关于我们</h1>
      <Button>了解更多</Button>
    </div>
  );
}
```

**动态路由示例**

```tsx
// src/app/posts/[id]/page.tsx
export default async function PostPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return <div>文章 ID: {id}</div>;
}
```

**API 路由示例**

```tsx
// src/app/api/users/route.ts
import { NextResponse } from 'next/server';

export async function GET() {
  return NextResponse.json({ users: [] });
}

export async function POST(request: Request) {
  const body = await request.json();
  return NextResponse.json({ success: true });
}
```

### 3. 依赖管理

**必须使用 pnpm 管理依赖**

```bash
# ✅ 安装依赖
pnpm install

# ✅ 添加新依赖
pnpm add package-name

# ✅ 添加开发依赖
pnpm add -D package-name

# ❌ 禁止使用 npm 或 yarn
# npm install  # 错误！
# yarn add     # 错误！
```

项目已配置 `preinstall` 脚本，使用其他包管理器会报错。

### 4. 样式开发

**使用 Tailwind CSS v4**

本项目使用 Tailwind CSS v4 进行样式开发，并已配置 shadcn 主题变量。

```tsx
// 使用 Tailwind 类名
<div className="flex items-center gap-4 p-4 rounded-lg bg-background">
  <Button className="bg-primary text-primary-foreground">
    主要按钮
  </Button>
</div>

// 使用 cn() 工具函数合并类名
import { cn } from '@/lib/utils';

<div className={cn(
  "base-class",
  condition && "conditional-class",
  className
)}>
  内容
</div>
```

**主题变量**

主题变量定义在 `src/app/globals.css` 中，支持亮色/暗色模式：

- `--background`, `--foreground`
- `--primary`, `--primary-foreground`
- `--secondary`, `--secondary-foreground`
- `--muted`, `--muted-foreground`
- `--accent`, `--accent-foreground`
- `--destructive`, `--destructive-foreground`
- `--border`, `--input`, `--ring`

### 5. 表单开发

推荐使用 `react-hook-form` + `zod` 进行表单开发：

```tsx
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import * as z from 'zod';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

const formSchema = z.object({
  username: z.string().min(2, '用户名至少 2 个字符'),
  email: z.string().email('请输入有效的邮箱'),
});

export default function MyForm() {
  const form = useForm({
    resolver: zodResolver(formSchema),
    defaultValues: { username: '', email: '' },
  });

  const onSubmit = (data: z.infer<typeof formSchema>) => {
    console.log(data);
  };

  return (
    <form onSubmit={form.handleSubmit(onSubmit)}>
      <Input {...form.register('username')} />
      <Input {...form.register('email')} />
      <Button type="submit">提交</Button>
    </form>
  );
}
```

### 6. 数据获取

**服务端组件（推荐）**

```tsx
// src/app/posts/page.tsx
async function getPosts() {
  const res = await fetch('https://api.example.com/posts', {
    cache: 'no-store', // 或 'force-cache'
  });
  return res.json();
}

export default async function PostsPage() {
  const posts = await getPosts();

  return (
    <div>
      {posts.map(post => (
        <div key={post.id}>{post.title}</div>
      ))}
    </div>
  );
}
```

**客户端组件**

```tsx
'use client';

import { useEffect, useState } from 'react';

export default function ClientComponent() {
  const [data, setData] = useState(null);

  useEffect(() => {
    fetch('/api/data')
      .then(res => res.json())
      .then(setData);
  }, []);

  return <div>{JSON.stringify(data)}</div>;
}
```

## 常见开发场景

### 添加新页面

1. 在 `src/app/` 下创建文件夹和 `page.tsx`
2. 使用 shadcn 组件构建 UI
3. 根据需要添加 `layout.tsx` 和 `loading.tsx`

### 创建业务组件

1. 在 `src/components/` 下创建组件文件（非 UI 组件）
2. 优先组合使用 `src/components/ui/` 中的基础组件
3. 使用 TypeScript 定义 Props 类型

### 添加全局状态

推荐使用 React Context 或 Zustand：

```tsx
// src/lib/store.ts
import { create } from 'zustand';

interface Store {
  count: number;
  increment: () => void;
}

export const useStore = create<Store>((set) => ({
  count: 0,
  increment: () => set((state) => ({ count: state.count + 1 })),
}));
```

### 集成数据库

推荐使用 Prisma 或 Drizzle ORM，在 `src/lib/db.ts` 中配置。

## 技术栈

- **框架**: Next.js 16.1.1 (App Router)
- **UI 组件**: shadcn/ui (基于 Radix UI)
- **样式**: Tailwind CSS v4
- **表单**: React Hook Form + Zod
- **图标**: Lucide React
- **字体**: Geist Sans & Geist Mono
- **包管理器**: pnpm 9+
- **TypeScript**: 5.x

## 参考文档

- [Next.js 官方文档](https://nextjs.org/docs)
- [shadcn/ui 组件文档](https://ui.shadcn.com)
- [Tailwind CSS 文档](https://tailwindcss.com/docs)
- [React Hook Form](https://react-hook-form.com)

## 重要提示

1. **必须使用 pnpm** 作为包管理器
2. **优先使用 shadcn/ui 组件** 而不是从零开发基础组件
3. **遵循 Next.js App Router 规范**，正确区分服务端/客户端组件
4. **使用 TypeScript** 进行类型安全开发
5. **使用 `@/` 路径别名** 导入模块（已配置）

## 无效实验室模块十

`/test/module-lab` 的模块十按创造性三步法展示当前 D1、区别特征/实际技术问题，以及模块九最多
五轮补证语料对每项区别特征形成的公开、相同作用、技术启示、修改路径、反向教导和效果可预期性。
全部区块和逐区别特征明细默认收起。累计覆盖只是必要条件；页面只有在逐项证据链均闭合时显示
“现有证据已形成缺乏创造性的完整证据链（供律师复核）”，否则显示“现有证据尚不足以证明不具备
创造性”，不得把证据不足解释为专利已被证明具备创造性或有效。

## 无效实验室模块十一

`/test/module-lab` 的模块十一固定分为三个默认收起的部分：第一部分把当前模块十已经冻结的
创造性三步法结果转写为律师可直接阅读的连续分析；第二部分以独立权利要求全部技术特征为行、
相似度最高的最多10篇已完成单篇比对的文献为列，显示每项特征是否明确/必然隐含披露、未披露、
待确认或分析未完成；第三部分保留当前案件全部对比文件的逐篇完整 Claim Chart。

Top 10 按本次 I4-S 已确认披露的特征数优先排序，待确认不计为披露，再按确定性评价完整度排序；
它不是文本/向量相似度，也不替代创造性组合内部的 D1+最多5篇增量文献选择。Excel 导出同步提供
创造性文字分析和每项独立权利要求的横向 Top10 工作表。
