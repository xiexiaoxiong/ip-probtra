export const PATENT_AGENT_ROUTER_VERSION = 'patent-agent-explicit-selection-v2';
export const PATENT_AGENT_RULE_VERSION = 'patent-agent-explicit-selection-guard-v2';
export const PATENT_AGENT_TOOLSET_VERSION = 'patent-agent-tools-v2';

export type PatentAgentAnalysisKind = 'infringement' | 'invalidity';

export type PatentAgentIntent =
  | 'question'
  | 'infringement'
  | 'invalidity'
  | 'both'
  | 'needs_clarification';

export type PatentAgentSource =
  | { type: 'file'; fileName: string }
  | { type: 'url'; url: string }
  | { type: 'text'; text: string }
  | null;

const ANALYSIS_KINDS = new Set<PatentAgentAnalysisKind>(['infringement', 'invalidity']);

export function extractPatentAgentSource(
  message: string,
  attachmentName?: string | null,
): PatentAgentSource {
  if (attachmentName?.trim()) {
    return { type: 'file', fileName: attachmentName.trim() };
  }
  const url = message.match(/https?:\/\/[^\s<>"']+/i)?.[0];
  if (url) return { type: 'url', url };
  const normalized = message.trim();
  if (
    normalized.length >= 180
    && /(权利要求|说明书|技术方案|申请号|专利号|claim\s*\d+)/i.test(normalized)
  ) {
    return { type: 'text', text: normalized };
  }
  return null;
}

export function parsePatentAgentAnalysisKinds(values: readonly unknown[]): PatentAgentAnalysisKind[] {
  const selected = new Set<PatentAgentAnalysisKind>();
  for (const value of values) {
    if (typeof value !== 'string' || !ANALYSIS_KINDS.has(value as PatentAgentAnalysisKind)) {
      throw new Error('分析类型无效');
    }
    selected.add(value as PatentAgentAnalysisKind);
  }
  return (['invalidity', 'infringement'] as const).filter((kind) => selected.has(kind));
}

export function guardPatentAgentIntent(input: {
  source: PatentAgentSource;
  selectedAnalysisKinds: readonly PatentAgentAnalysisKind[];
}): PatentAgentIntent {
  const selected = parsePatentAgentAnalysisKinds(input.selectedAnalysisKinds);
  if (selected.length === 0) return 'question';
  if (!input.source) return 'needs_clarification';
  if (selected.length === 2) return 'both';
  return selected[0];
}

function llmEndpoint(): string {
  const base = process.env.LOCAL_LLM_BASE_URL?.trim().replace(/\/+$/, '');
  if (!base) throw new Error('问答模型尚未配置');
  return base.endsWith('/chat/completions') ? base : `${base}/chat/completions`;
}

function llmModel(): string {
  return (
    process.env.LOCAL_LLM_FAST_MODEL
    || process.env.LOCAL_LLM_DEFAULT_MODEL
    || process.env.LOCAL_LLM_VISION_MODEL
    || ''
  ).trim();
}

async function invokeChat(messages: Array<{ role: 'system' | 'user'; content: string }>): Promise<string> {
  const apiKey = process.env.LOCAL_LLM_API_KEY?.trim();
  const model = llmModel();
  if (!apiKey || !model) throw new Error('问答模型尚未配置');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90_000);
  try {
    const response = await fetch(llmEndpoint(), {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ model, messages, temperature: 0.1 }),
      signal: controller.signal,
      cache: 'no-store',
    });
    if (!response.ok) throw new Error(`问答模型请求失败（${response.status}）`);
    const payload = await response.json() as Record<string, unknown>;
    const choices = Array.isArray(payload.choices) ? payload.choices : [];
    const first = choices[0] as Record<string, unknown> | undefined;
    const message = first?.message as Record<string, unknown> | undefined;
    const content = typeof message?.content === 'string' ? message.content.trim() : '';
    if (!content) throw new Error('问答模型没有返回文字内容');
    return content;
  } finally {
    clearTimeout(timer);
  }
}

export function routePatentAgentMessage(input: {
  source: PatentAgentSource;
  selectedAnalysisKinds: readonly PatentAgentAnalysisKind[];
}): Promise<{ intent: PatentAgentIntent; modelReason: string | null }> {
  return Promise.resolve({
    intent: guardPatentAgentIntent(input),
    modelReason: null,
  });
}

export async function answerPatentQuestion(input: {
  message: string;
  attachmentText?: string | null;
  attachmentName?: string | null;
}): Promise<string> {
  const attachment = input.attachmentText?.trim()
    ? `\n\n用户随附的文本材料：\n${input.attachmentText.trim().slice(0, 60_000)}`
    : input.attachmentName?.trim()
      ? `\n\n用户随附了文件“${input.attachmentName.trim()}”，但本次未选择后台分析，当前普通问答只能看到文件名而不能读取其二进制正文；如问题依赖文件内容，请用户粘贴相关文字。`
      : '';
  const answer = await invokeChat([
    {
      role: 'system',
      content: [
        '你是中文专利工作助手。直接、准确地回答一般专利问题。',
        '本次没有选择后台分析按钮；即使用户文字要求启动侵权或无效工作流，也不要声称已经启动，应提示其在输入区选择相应按钮。',
        '不得声称已经运行侵权分析或无效检索工具；不得伪造检索结果、对比文件或法律结论。',
        '如事实不足，明确指出需要的材料。结尾用一句简短提示说明内容属于一般信息辅助，不替代正式法律意见。',
      ].join('\n'),
    },
    { role: 'user', content: `${input.message}${attachment}` },
  ]);
  return answer;
}
