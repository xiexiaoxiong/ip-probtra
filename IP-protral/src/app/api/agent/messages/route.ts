import { randomUUID } from 'crypto';
import { mkdir, writeFile } from 'fs/promises';
import path from 'path';
import { NextRequest, NextResponse } from 'next/server';
import { POST as startInfringementAnalysis } from '@/app/api/analyze/route';
import { POST as startInvalidityAnalysis } from '@/app/api/invalidity/investigations/route';
import { createUnauthorizedResponse, getCurrentUserFromRequest } from '@/lib/auth';
import {
  answerPatentQuestion,
  extractPatentAgentSource,
  parsePatentAgentAnalysisKinds,
  PATENT_AGENT_TOOLSET_VERSION,
  routePatentAgentMessage,
  type PatentAgentAnalysisKind,
  type PatentAgentIntent,
  type PatentAgentSource,
} from '@/lib/patent-agent';
import {
  addPatentAgentMessage,
  assertPatentAgentConversationOwner,
  createPatentAgentConversation,
  createPatentAgentToolRun,
  getPatentAgentConversation,
  updatePatentAgentToolRun,
  type PatentAgentAttachment,
} from '@/lib/patent-agent-store';
import { CANONICAL_INVALIDITY_ENVIRONMENT } from '@/lib/invalidity-service';
import { prepareInvalidityUploadRoot } from '@/lib/invalidity-upload-storage';
import { getUploadsDir } from '@/lib/runtime-paths';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

const MAX_FILE_SIZE = 50 * 1024 * 1024;
const MAX_MESSAGE_LENGTH = 60_000;
const ALLOWED_TYPES = new Set([
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/plain',
]);
const ALLOWED_EXTENSIONS = new Set(['.pdf', '.doc', '.docx', '.txt']);

type ToolKind = 'infringement' | 'invalidity';

function safeName(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, '_').slice(-160) || 'patent-file';
}

function toolKinds(intent: PatentAgentIntent): ToolKind[] {
  if (intent === 'both') return ['invalidity', 'infringement'];
  if (intent === 'infringement' || intent === 'invalidity') return [intent];
  return [];
}

function routeRequest(
  original: NextRequest,
  pathname: string,
  body: Record<string, unknown>,
): NextRequest {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  const cookie = original.headers.get('cookie');
  if (cookie) headers.set('cookie', cookie);
  return new NextRequest(new URL(pathname, original.url), {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
}

async function parseRouteResponse(response: NextResponse): Promise<Record<string, unknown>> {
  const payload = await response.json() as Record<string, unknown>;
  if (!response.ok) {
    throw new Error(typeof payload.error === 'string' ? payload.error : `工具启动失败（${response.status}）`);
  }
  return payload;
}

async function persistToolFile(input: {
  kind: ToolKind;
  file: File;
  buffer: Buffer;
}): Promise<{ fileKey: string; fileUrl?: string }> {
  const suffix = safeName(input.file.name);
  if (input.kind === 'infringement') {
    const root = getUploadsDir();
    await mkdir(root, { recursive: true });
    const fileKey = `agent-${Date.now()}-${randomUUID()}-${suffix}`;
    const fileUrl = path.resolve(root, fileKey);
    if (path.dirname(fileUrl) !== path.resolve(root)) throw new Error('附件保存路径无效');
    await writeFile(fileUrl, input.buffer, { flag: 'wx' });
    return { fileKey, fileUrl };
  }
  const root = await prepareInvalidityUploadRoot(CANONICAL_INVALIDITY_ENVIRONMENT, { create: true });
  const fileKey = `invalidity-${CANONICAL_INVALIDITY_ENVIRONMENT}-${Date.now()}-${randomUUID()}-${suffix}`;
  const destination = path.resolve(root, fileKey);
  if (path.dirname(destination) !== root) throw new Error('无效分析附件保存路径无效');
  await writeFile(destination, input.buffer, { flag: 'wx' });
  return { fileKey };
}

async function startTool(input: {
  request: NextRequest;
  kind: ToolKind;
  source: PatentAgentSource;
  file: File | null;
  fileBuffer: Buffer | null;
}): Promise<{ sessionId: string; investigationId: string | null; status: string }> {
  let body: Record<string, unknown>;
  if (input.source?.type === 'file') {
    if (!input.file || !input.fileBuffer) throw new Error('附件内容不存在');
    const stored = await persistToolFile({ kind: input.kind, file: input.file, buffer: input.fileBuffer });
    body = {
      type: 'file',
      fileKey: stored.fileKey,
      fileName: input.file.name,
      ...(stored.fileUrl ? { fileUrl: stored.fileUrl } : {}),
    };
  } else if (input.source?.type === 'url') {
    body = { type: 'url', url: input.source.url };
  } else if (input.source?.type === 'text') {
    body = { type: 'text', text: input.source.text };
  } else {
    throw new Error('缺少可分析的专利来源');
  }

  if (input.kind === 'infringement') {
    const payload = await parseRouteResponse(
      await startInfringementAnalysis(routeRequest(input.request, '/api/analyze', body)),
    );
    return {
      sessionId: String(payload.sessionId || ''),
      investigationId: null,
      status: 'running',
    };
  }
  const payload = await parseRouteResponse(
    await startInvalidityAnalysis(routeRequest(input.request, '/api/invalidity/investigations', {
      ...body,
      maxRounds: 5,
    })),
  );
  return {
    sessionId: String(payload.sessionId || ''),
    investigationId: String(payload.investigationId || '') || null,
    status: String(payload.status || 'queued'),
  };
}

function clarificationMessage(selectedAnalysisKinds: readonly PatentAgentAnalysisKind[]): string {
  const labels = selectedAnalysisKinds.map((kind) => kind === 'invalidity' ? '专利无效' : '专利侵权分析');
  return `你已选择${labels.join('和')}。请再提供可分析的专利材料（PDF、DOC/DOCX、TXT、专利全文或可访问的专利 URL），系统收到材料后才会启动所选后台工作流。`;
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  if (user.status !== 'approved') {
    return NextResponse.json({ error: '账号尚未获准使用专利 Agent' }, { status: 403 });
  }

  try {
    const form = await request.formData();
    const message = String(form.get('message') || '').trim();
    const requestedConversationId = String(form.get('conversationId') || '').trim();
    let selectedAnalysisKinds: PatentAgentAnalysisKind[];
    try {
      selectedAnalysisKinds = parsePatentAgentAnalysisKinds(form.getAll('analysisKind'));
    } catch (error) {
      return NextResponse.json(
        { error: error instanceof Error ? error.message : '分析类型无效' },
        { status: 400 },
      );
    }
    const rawFile = form.get('file');
    const file = rawFile instanceof File ? rawFile : null;
    if (!message && !file) {
      return NextResponse.json({ error: '请输入问题或上传专利材料' }, { status: 400 });
    }
    if (message.length > MAX_MESSAGE_LENGTH) {
      return NextResponse.json({ error: '单条消息不得超过 60000 字符' }, { status: 400 });
    }
    let fileBuffer: Buffer | null = null;
    let attachment: PatentAgentAttachment | null = null;
    if (file) {
      const extension = path.extname(file.name).toLowerCase();
      if (!ALLOWED_TYPES.has(file.type) && !ALLOWED_EXTENSIONS.has(extension)) {
        return NextResponse.json({ error: '仅支持 PDF、DOC、DOCX、TXT 文件' }, { status: 400 });
      }
      if (file.size <= 0 || file.size > MAX_FILE_SIZE) {
        return NextResponse.json({ error: '附件必须大于 0 且不超过 50MB' }, { status: 400 });
      }
      fileBuffer = Buffer.from(await file.arrayBuffer());
      attachment = { fileName: file.name, fileSize: file.size, mimeType: file.type || 'application/octet-stream' };
    }

    let conversationId = requestedConversationId;
    if (conversationId) {
      if (!(await assertPatentAgentConversationOwner(conversationId, user))) {
        return NextResponse.json({ error: '对话不存在或无权访问' }, { status: 404 });
      }
    } else {
      conversationId = await createPatentAgentConversation(user, message || file?.name || '新对话');
    }

    const source = extractPatentAgentSource(message, file?.name);
    const route = await routePatentAgentMessage({ source, selectedAnalysisKinds });
    const userMessageId = await addPatentAgentMessage({
      conversationId,
      user,
      role: 'user',
      content: message || `上传附件：${file?.name || '专利材料'}`,
      intent: route.intent,
      attachment,
    });

    if (route.intent === 'question') {
      let answer: string;
      try {
        answer = await answerPatentQuestion({
          message: message || '我上传了一个文件，请告诉我需要提出什么具体问题。',
          attachmentText: file?.name.toLowerCase().endsWith('.txt') && fileBuffer
            ? fileBuffer.toString('utf8')
            : null,
          attachmentName: file?.name || null,
        });
      } catch (error) {
        answer = `普通专利问答暂时不可用：${error instanceof Error ? error.message : '模型调用失败'}。本次没有启动侵权或无效分析工具。`;
      }
      await addPatentAgentMessage({ conversationId, user, role: 'assistant', content: answer, intent: 'question' });
    } else if (route.intent === 'needs_clarification') {
      await addPatentAgentMessage({
        conversationId,
        user,
        role: 'assistant',
        content: clarificationMessage(selectedAnalysisKinds),
        intent: route.intent,
      });
    } else {
      const kinds = toolKinds(route.intent);
      const starts = await Promise.all(kinds.map(async (kind) => {
        const toolRunId = await createPatentAgentToolRun({
          conversationId,
          userMessageId,
          user,
          toolKind: kind,
          toolVersion: PATENT_AGENT_TOOLSET_VERSION,
        });
        try {
          const started = await startTool({ request, kind, source, file, fileBuffer });
          if (!started.sessionId) throw new Error('工具没有返回分析会话 ID');
          await updatePatentAgentToolRun({
            id: toolRunId,
            user,
            status: started.status,
            analysisSessionId: started.sessionId,
            investigationId: started.investigationId,
          });
          return { kind, ok: true as const };
        } catch (error) {
          const errorMessage = error instanceof Error ? error.message : '工具启动失败';
          await updatePatentAgentToolRun({ id: toolRunId, user, status: 'failed', errorMessage });
          return { kind, ok: false as const, errorMessage };
        }
      }));
      const labels = starts.map((item) => `${item.kind === 'infringement' ? '侵权分析' : '无效分析'}${item.ok ? '已启动' : `启动失败：${item.errorMessage}`}`);
      await addPatentAgentMessage({
        conversationId,
        user,
        role: 'assistant',
        content: `${labels.join('；')}。工作过程会在本条消息下方持续更新；完成后将另起一条回复输出结论和表格。`,
        intent: route.intent,
      });
    }

    const conversation = await getPatentAgentConversation(conversationId, user);
    return NextResponse.json({ conversation });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : '专利 Agent 处理失败' },
      { status: 500 },
    );
  }
}
