'use client';

import { Fragment, FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import {
  AlertCircle,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Circle,
  CircleDashed,
  Clock3,
  FileText,
  History,
  Loader2,
  LogOut,
  Menu,
  Paperclip,
  Plus,
  Send,
  ShieldCheck,
  Square,
  UserRound,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { AgentInvalidityReview } from '@/components/agent-invalidity-review';
import { AgentInvalidityResultMessage } from '@/components/agent-invalidity-result-message';
import { AgentInfringementResultMessage } from '@/components/agent-infringement-result-message';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  deriveAgentInvalidityAction,
  type AgentInvalidityAction,
} from '@/lib/agent-invalidity-action';
import {
  infringementTaskProgress,
  invalidityTaskProgress,
  type AgentTaskProgress,
  type AgentTaskStepState,
} from '@/lib/agent-task-progress';
import {
  buildAgentInvaliditySummary,
  type AgentInvaliditySummary,
} from '@/lib/agent-invalidity-summary';
import {
  buildAgentInvalidityModule11Report,
  type AgentInvalidityModule11Report,
} from '@/lib/agent-invalidity-report';
import { agentToolAnchorMessageId } from '@/lib/agent-conversation-layout';
import {
  buildAgentInfringementResult,
  type AgentInfringementResult,
} from '@/lib/agent-infringement-result';
import type { AuthUser } from '@/lib/types';

type AgentAttachment = {
  fileName: string;
  fileSize: number;
  mimeType: string;
};

type AgentMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  intent: string | null;
  attachment: AgentAttachment | null;
  createdAt: string;
};

type AgentToolRun = {
  id: string;
  userMessageId: string;
  toolKind: 'infringement' | 'invalidity';
  toolVersion: string;
  status: string;
  analysisSessionId: string | null;
  investigationId: string | null;
  errorMessage: string | null;
  updatedAt: string;
  progress?: AgentTaskProgress | null;
  action?: AgentInvalidityAction | null;
  invaliditySummary?: AgentInvaliditySummary | null;
  invalidityModule11Report?: AgentInvalidityModule11Report | null;
  infringementResult?: AgentInfringementResult | null;
};

type AgentConversation = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  messages: AgentMessage[];
  toolRuns: AgentToolRun[];
};

type ConversationSummary = {
  id: string;
  title: string;
  updatedAt: string;
  lastMessage: string | null;
};

type AnalysisKind = 'invalidity' | 'infringement';

const TERMINAL_STATUSES = new Set([
  'completed',
  'complete',
  'succeeded',
  'failed',
  'error',
  'partial',
  'cancelled',
  'needs_human_review',
  'novelty_evidence_complete',
  'inventive_step_evidence_complete',
  'search_budget_exhausted',
  'exhausted',
]);

function toolLabel(kind: AgentToolRun['toolKind']): string {
  return kind === 'infringement' ? '专利侵权分析' : '专利无效与稳定性分析';
}

function statusLabel(status: string): string {
  const normalized = status.toLowerCase();
  if (['completed', 'complete', 'succeeded', 'novelty_evidence_complete', 'inventive_step_evidence_complete'].includes(normalized)) return '已完成';
  if (['failed', 'error'].includes(normalized)) return '失败';
  if (normalized === 'partial') return '部分完成';
  if (normalized === 'cancelled') return '已取消';
  if (normalized === 'needs_human_review') return '等待人工处理';
  if (normalized === 'search_budget_exhausted') return '检索轮次已完成';
  if (['queued', 'created', 'pending', 'draft'].includes(normalized)) return '排队中';
  return '运行中';
}

function progressStateLabel(state: AgentTaskStepState): string {
  if (state === 'completed') return '已完成';
  if (state === 'running') return '进行中';
  if (state === 'waiting') return '等待处理';
  if (state === 'partial') return '部分完成';
  if (state === 'error') return '失败';
  if (state === 'cancelled') return '已取消';
  return '待开始';
}

function progressStateIcon(state: AgentTaskStepState) {
  if (state === 'completed') return <Check className="h-3.5 w-3.5" />;
  if (state === 'running') return <Loader2 className="h-3.5 w-3.5 animate-spin" />;
  if (state === 'waiting') return <Clock3 className="h-3.5 w-3.5" />;
  if (['error', 'partial', 'cancelled'].includes(state)) return <AlertCircle className="h-3.5 w-3.5" />;
  return <Circle className="h-3.5 w-3.5" />;
}

function progressStateClass(state: AgentTaskStepState): string {
  if (state === 'completed') return 'bg-emerald-100 text-emerald-700';
  if (state === 'running') return 'bg-blue-100 text-blue-700';
  if (state === 'waiting' || state === 'partial') return 'bg-amber-100 text-amber-700';
  if (state === 'error' || state === 'cancelled') return 'bg-rose-100 text-rose-700';
  return 'bg-slate-100 text-slate-400';
}

function AgentWorkTrace({ progress, terminal }: {
  progress: AgentTaskProgress;
  terminal: boolean;
}) {
  const observedSteps = progress.steps.filter((step) => step.state !== 'pending');
  const visibleSteps = (observedSteps.length ? observedSteps : progress.steps.slice(0, 1)).slice(-3);
  return (
    <div
      className="mt-3 rounded-lg border border-slate-200 bg-white/80 px-3 py-2.5"
      data-agent-work-trace="persisted-stage-status"
      aria-label="由后台持久化阶段状态生成的 Agent 工作动态"
    >
      <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400">
        {terminal ? '本次工作动态' : 'Agent 工作动态'}
      </div>
      <div className="mt-2 space-y-2 border-l border-slate-200 pl-3">
        {visibleSteps.map((step) => (
          <div key={step.id} className="relative">
            <span className={`absolute -left-[17px] top-1 h-2 w-2 rounded-full ring-2 ring-white ${
              step.state === 'completed'
                ? 'bg-emerald-500'
                : step.state === 'running'
                  ? 'animate-pulse bg-blue-500'
                  : ['waiting', 'partial'].includes(step.state)
                    ? 'bg-amber-500'
                    : 'bg-rose-500'
            }`} />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-medium text-slate-700">{step.label}</span>
              <span className="text-[10px] text-slate-400">{progressStateLabel(step.state)}</span>
            </div>
            {step.state !== 'completed' ? (
              <p className="mt-0.5 text-[11px] leading-4 text-slate-500">{step.description}</p>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function secondsUntil(deadlineAt: string): number {
  return Math.max(0, Math.ceil((Date.parse(deadlineAt) - Date.now()) / 1000));
}

function AgentInvalidityActionPrompt({
  tool,
}: {
  tool: AgentToolRun;
}) {
  const action = tool.action;
  const [open, setOpen] = useState(Boolean(action));
  const [remainingSeconds, setRemainingSeconds] = useState(
    action?.kind === 'gap_continuation' ? secondsUntil(action.deadlineAt) : 0,
  );
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const submittingRef = useRef(false);
  const autoAttemptedRef = useRef(false);
  const actionKey = action?.key || null;
  const gapDeadline = action?.kind === 'gap_continuation' ? action.deadlineAt : null;

  useEffect(() => {
    setOpen(Boolean(actionKey));
    setRemainingSeconds(gapDeadline ? secondsUntil(gapDeadline) : 0);
    setSubmitting(false);
    setSubmitted(false);
    setActionError(null);
    submittingRef.current = false;
    autoAttemptedRef.current = false;
  }, [actionKey, gapDeadline]);

  const startContinuation = useCallback(async (automatic: boolean) => {
    if (!action || action.kind !== 'gap_continuation' || submittingRef.current) return;
    if (automatic && autoAttemptedRef.current) return;
    if (automatic) autoAttemptedRef.current = true;
    submittingRef.current = true;
    setSubmitting(true);
    setActionError(null);
    try {
      const response = await fetch(
        `/api/agent/tool-runs/${encodeURIComponent(tool.id)}/invalidity-continuation`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_state_version: action.investigationStateVersion,
          }),
        },
      );
      const payload = await response.json().catch(() => ({})) as { error?: string };
      if (!response.ok) throw new Error(payload.error || '创建下一轮检索失败');
      setSubmitted(true);
      setOpen(false);
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : '创建下一轮检索失败');
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }, [action, tool.id]);

  useEffect(() => {
    if (!action || action.kind !== 'gap_continuation' || submitted) return;
    const tick = () => {
      const remaining = secondsUntil(action.deadlineAt);
      setRemainingSeconds(remaining);
      if (remaining === 0) void startContinuation(true);
    };
    tick();
    const timer = window.setInterval(tick, 250);
    return () => window.clearInterval(timer);
  }, [action, startContinuation, submitted]);

  if (!action) return null;
  const isGapContinuation = action.kind === 'gap_continuation';
  const title = isGapContinuation ? '是否开始下一轮检索？' : '需要你确认案件事实';
  const description = isGapContinuation
    ? `${action.detail} ${remainingSeconds} 秒内未操作将自动开始。`
    : action.detail;
  const actionBody = (
    <>
      {isGapContinuation ? (
        <Button
          type="button"
          size="sm"
          disabled={submitting || submitted}
          onClick={() => void startContinuation(false)}
        >
          {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
          {submitted ? '下一轮已提交' : '立即开始下一轮'}
        </Button>
      ) : tool.analysisSessionId ? (
        <Button type="button" size="sm" onClick={() => setOpen(true)}>
          在 Agent 中处理人工确认
        </Button>
      ) : null}
    </>
  );

  return (
    <>
      <div className="border-b border-amber-200 bg-amber-50 px-3 py-3" data-agent-confirmation={action.kind}>
        <div className="flex items-start gap-2">
          <Clock3 className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-amber-900">{title}</div>
            <p className="mt-1 text-xs leading-5 text-amber-800">{description}</p>
            {actionError ? <p className="mt-1 text-xs text-rose-600">{actionError}</p> : null}
            <div className="mt-2">{actionBody}</div>
          </div>
        </div>
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className={isGapContinuation ? undefined : 'max-h-[88vh] max-w-5xl overflow-y-auto'}>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {actionError ? <p className="text-sm text-rose-600">{actionError}</p> : null}
          {isGapContinuation ? (
            <DialogFooter>{actionBody}</DialogFooter>
          ) : tool.analysisSessionId ? (
            <AgentInvalidityReview sessionId={tool.analysisSessionId} />
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}

function AgentToolProgressCard({ tool, isAdmin }: {
  tool: AgentToolRun;
  isAdmin: boolean;
}) {
  const terminal = TERMINAL_STATUSES.has(tool.status.toLowerCase());
  const failed = ['failed', 'error'].includes(tool.status.toLowerCase());
  const attention = ['partial', 'needs_human_review'].includes(tool.status.toLowerCase());
  const progress = tool.progress;
  const diagnosticHref = tool.analysisSessionId && isAdmin
    ? tool.toolKind === 'invalidity'
      ? `/test/module-lab?session=${encodeURIComponent(tool.analysisSessionId)}`
      : `/test/product-pipeline?session=${encodeURIComponent(tool.analysisSessionId)}`
    : null;
  const currentState = progress?.steps.find((step) => step.state !== 'completed' && step.state !== 'pending')?.state
    || (terminal ? failed ? 'error' : 'completed' : 'running');
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm" data-agent-task-progress={tool.toolKind}>
      <div className="flex items-center justify-between gap-3 px-3 py-3">
        <div className="flex min-w-0 items-center gap-2">
          {terminal ? <ShieldCheck className={`h-4 w-4 ${failed ? 'text-rose-500' : attention ? 'text-amber-600' : 'text-emerald-600'}`} /> : <CircleDashed className="h-4 w-4 animate-pulse text-blue-600" />}
          <span className="truncate text-sm font-medium">{toolLabel(tool.toolKind)}</span>
        </div>
        <span className={`rounded-full px-2 py-1 text-xs ${failed ? 'bg-rose-50 text-rose-600' : attention ? 'bg-amber-50 text-amber-700' : terminal ? 'bg-emerald-50 text-emerald-700' : 'bg-blue-50 text-blue-700'}`}>
          {statusLabel(tool.status)}
        </span>
      </div>

      {progress ? (
        <>
          <div className="border-y border-slate-100 bg-slate-50/80 px-3 py-3" aria-live="polite">
            <div className="flex items-start gap-2.5">
              <span className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full ${progressStateClass(currentState)}`}>
                {progressStateIcon(currentState)}
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400">
                  {terminal ? '本次状态' : '当前事项'}
                </div>
                <div className="mt-0.5 text-sm font-medium text-slate-800">{progress.currentLabel}</div>
                <p className="mt-1 text-xs leading-5 text-slate-500">{progress.currentDetail}</p>
              </div>
            </div>
            <AgentWorkTrace progress={progress} terminal={terminal} />
            <div className="mt-3 flex gap-1" aria-label={`已完成 ${progress.completedCount} / ${progress.totalCount} 项`}>
              {progress.steps.map((step) => (
                <span
                  key={step.id}
                  title={`${step.label}：${progressStateLabel(step.state)}`}
                  className={`h-1.5 min-w-0 flex-1 rounded-full ${
                    step.state === 'completed'
                      ? 'bg-emerald-500'
                      : step.state === 'running'
                        ? 'animate-pulse bg-blue-500'
                        : step.state === 'waiting' || step.state === 'partial'
                          ? 'bg-amber-400'
                          : step.state === 'error' || step.state === 'cancelled'
                            ? 'bg-rose-400'
                            : 'bg-slate-200'
                  }`}
                />
              ))}
            </div>
            <div className="mt-1.5 text-right text-[11px] text-slate-400">已完成 {progress.completedCount} / {progress.totalCount} 项</div>
          </div>

          <details className="group border-b border-slate-100">
            <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2.5 text-xs font-medium text-slate-500 hover:bg-slate-50">
              查看全部事项
              <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
            </summary>
            <div className="space-y-1 px-3 pb-3">
              {progress.steps.map((step) => (
                <div key={step.id} className={`flex items-start gap-2 rounded-lg px-2 py-2 ${step.state === 'running' ? 'bg-blue-50/80' : ''}`}>
                  <span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full ${progressStateClass(step.state)}`}>
                    {progressStateIcon(step.state)}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-slate-700">{step.label}</span>
                      <span className="shrink-0 text-[10px] text-slate-400">{progressStateLabel(step.state)}</span>
                    </div>
                    {step.state === 'running' || step.state === 'waiting' || step.state === 'partial' || step.state === 'error' ? (
                      <p className="mt-0.5 text-[11px] leading-4 text-slate-500">{step.description}</p>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </details>
        </>
      ) : !terminal ? (
        <div className="border-y border-slate-100 bg-slate-50/80 px-3 py-3 text-xs text-slate-500" aria-live="polite">
          <span className="inline-flex items-center gap-2"><Loader2 className="h-3.5 w-3.5 animate-spin text-blue-600" />正在读取后台的当前事项…</span>
        </div>
      ) : null}

      {tool.analysisSessionId && (
        (tool.toolKind === 'invalidity' && tool.invalidityModule11Report)
        || (tool.toolKind === 'infringement' && tool.infringementResult)
      ) ? (
        <div className="border-b border-slate-100 px-3 py-2.5 text-xs text-slate-500" data-agent-result-ready="true">
          分析已收口，完整结论已在下方独立回复中输出。
        </div>
      ) : null}

      <AgentInvalidityActionPrompt tool={tool} />

      <div className="px-3 py-2.5">
        {tool.errorMessage ? <p className="mb-2 text-xs text-rose-600">{tool.errorMessage}</p> : null}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {diagnosticHref ? (
            <Link href={diagnosticHref} className="inline-flex items-center gap-1 text-xs font-medium text-violet-700 hover:underline">
              {tool.toolKind === 'infringement' ? '查看本次分析各模块' : '打开管理员诊断视图'}<ChevronRight className="h-3.5 w-3.5" />
            </Link>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

export default function HomePage() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversation, setConversation] = useState<AgentConversation | null>(null);
  const [message, setMessage] = useState('');
  const [attachment, setAttachment] = useState<File | null>(null);
  const [selectedAnalysisKinds, setSelectedAnalysisKinds] = useState<AnalysisKind[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const pendingTools = useMemo(
    () => conversation?.toolRuns.filter((tool) => (
      !TERMINAL_STATUSES.has(tool.status.toLowerCase())
      || Boolean(tool.action)
      || (
        tool.toolKind === 'invalidity'
        && !['failed', 'error', 'cancelled'].includes(tool.status.toLowerCase())
        && !tool.invalidityModule11Report
      )
    )) || [],
    [conversation?.toolRuns],
  );

  // 工作中的任务：发送按钮变为停止按钮，回车保持普通换行。
  const busyTools = useMemo(
    () => conversation?.toolRuns.filter((tool) => !TERMINAL_STATUSES.has(tool.status.toLowerCase())) || [],
    [conversation?.toolRuns],
  );
  const busyInvalidityTools = useMemo(
    () => busyTools.filter((tool) => tool.toolKind === 'invalidity' && Boolean(tool.analysisSessionId)),
    [busyTools],
  );
  const agentBusy = busyTools.length > 0;
  const toolRunsByAnchor = useMemo(() => {
    const grouped = new Map<string, AgentToolRun[]>();
    if (!conversation) return grouped;
    for (const tool of conversation.toolRuns) {
      const anchorId = agentToolAnchorMessageId(conversation.messages, tool.userMessageId);
      grouped.set(anchorId, [...(grouped.get(anchorId) || []), tool]);
    }
    return grouped;
  }, [conversation]);

  async function loadConversation(id: string): Promise<void> {
    const response = await fetch(`/api/agent/conversations/${encodeURIComponent(id)}`, { cache: 'no-store' });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({})) as { error?: string };
      throw new Error(payload.error || '读取对话失败');
    }
    const payload = await response.json() as { conversation: AgentConversation };
    const previousProgress = new Map(
      (conversation?.id === id ? conversation.toolRuns : []).map((tool) => [tool.id, tool.progress]),
    );
    const previousAction = new Map(
      (conversation?.id === id ? conversation.toolRuns : []).map((tool) => [tool.id, tool.action]),
    );
    const previousSummary = new Map(
      (conversation?.id === id ? conversation.toolRuns : []).map((tool) => [tool.id, tool.invaliditySummary]),
    );
    const previousModule11Report = new Map(
      (conversation?.id === id ? conversation.toolRuns : []).map((tool) => [tool.id, tool.invalidityModule11Report]),
    );
    const previousInfringementResult = new Map(
      (conversation?.id === id ? conversation.toolRuns : []).map((tool) => [tool.id, tool.infringementResult]),
    );
    const toolRuns = await Promise.all(payload.conversation.toolRuns.map(async (tool) => {
      if (!tool.analysisSessionId) return tool;
      try {
        const progressResponse = await fetch(
          tool.toolKind === 'infringement'
            ? `/api/analysis/${encodeURIComponent(tool.analysisSessionId)}`
            : `/api/invalidity/session/${encodeURIComponent(tool.analysisSessionId)}`,
          { cache: 'no-store' },
        );
        if (!progressResponse.ok) return {
          ...tool,
          progress: previousProgress.get(tool.id) || null,
          action: previousAction.get(tool.id) || null,
          invaliditySummary: previousSummary.get(tool.id) || null,
          invalidityModule11Report: previousModule11Report.get(tool.id) || null,
          infringementResult: previousInfringementResult.get(tool.id) || null,
        };
        const progressPayload = await progressResponse.json() as unknown;
        const progress = tool.toolKind === 'infringement'
          ? infringementTaskProgress(progressPayload)
          : invalidityTaskProgress(progressPayload);
        return {
          ...tool,
          status: progress?.overallStatus || tool.status,
          progress,
          action: tool.toolKind === 'invalidity'
            ? deriveAgentInvalidityAction(progressPayload, tool.toolVersion)
            : null,
          invaliditySummary: tool.toolKind === 'invalidity'
            ? buildAgentInvaliditySummary(progressPayload)
            : null,
          invalidityModule11Report: tool.toolKind === 'invalidity'
            ? buildAgentInvalidityModule11Report(progressPayload)
              || previousModule11Report.get(tool.id)
              || null
            : null,
          infringementResult: tool.toolKind === 'infringement'
            ? buildAgentInfringementResult(progressPayload)
              || previousInfringementResult.get(tool.id)
              || null
            : null,
        };
      } catch {
        return {
          ...tool,
          progress: previousProgress.get(tool.id) || null,
          action: previousAction.get(tool.id) || null,
          invaliditySummary: previousSummary.get(tool.id) || null,
          invalidityModule11Report: previousModule11Report.get(tool.id) || null,
          infringementResult: previousInfringementResult.get(tool.id) || null,
        };
      }
    }));
    setConversation({ ...payload.conversation, toolRuns });
  }

  async function loadConversationList(selectLatest = false): Promise<void> {
    const response = await fetch('/api/agent/conversations', { cache: 'no-store' });
    if (!response.ok) return;
    const payload = await response.json() as { conversations?: ConversationSummary[] };
    const next = payload.conversations || [];
    setConversations(next);
    if (selectLatest && !conversation && next[0]) {
      await loadConversation(next[0].id);
    }
  }

  async function stopActiveInvalidityTasks(): Promise<void> {
    if (stopping || busyInvalidityTools.length === 0) return;
    setStopping(true);
    setError(null);
    try {
      for (const tool of busyInvalidityTools) {
        const response = await fetch(
          `/api/invalidity/session/${encodeURIComponent(tool.analysisSessionId as string)}`,
          { method: 'POST' },
        );
        if (!response.ok) {
          const payload = await response.json().catch(() => ({})) as { error?: string };
          throw new Error(payload.error || '停止任务失败');
        }
      }
      if (conversation) await loadConversation(conversation.id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '停止任务失败');
    } finally {
      setStopping(false);
    }
  }

  useEffect(() => {
    async function bootstrap() {
      const response = await fetch('/api/auth/me', { cache: 'no-store' });
      if (response.status === 401) {
        window.location.href = '/login';
        return;
      }
      if (!response.ok) return;
      const payload = await response.json() as { user?: AuthUser };
      setUser(payload.user || null);
      await loadConversationList(true);
    }
    void bootstrap();
    // The initial history selection intentionally runs once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [conversation?.messages.length, conversation?.toolRuns.length, submitting]);

  useEffect(() => {
    if (!conversation || pendingTools.length === 0) return;
    const id = conversation.id;
    const timer = window.setInterval(() => {
      void loadConversation(id).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
    // Polling must track the current conversation and whether work remains.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversation?.id, pendingTools.length]);

  async function chooseConversation(id: string): Promise<void> {
    setLoadingConversation(true);
    setError(null);
    try {
      await loadConversation(id);
      setSidebarOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '读取对话失败');
    } finally {
      setLoadingConversation(false);
    }
  }

  function newConversation(): void {
    setConversation(null);
    setMessage('');
    setAttachment(null);
    setSelectedAnalysisKinds([]);
    setError(null);
    setSidebarOpen(false);
  }

  async function submitMessage(event?: FormEvent): Promise<void> {
    event?.preventDefault();
    if (submitting || (!message.trim() && !attachment)) return;
    setSubmitting(true);
    setError(null);
    const form = new FormData();
    form.set('message', message.trim());
    if (conversation?.id) form.set('conversationId', conversation.id);
    if (attachment) form.set('file', attachment);
    selectedAnalysisKinds.forEach((kind) => form.append('analysisKind', kind));
    try {
      const response = await fetch('/api/agent/messages', { method: 'POST', body: form });
      const payload = await response.json().catch(() => ({})) as {
        error?: string;
        conversation?: AgentConversation;
      };
      if (!response.ok || !payload.conversation) {
        throw new Error(payload.error || '消息发送失败');
      }
      setConversation(payload.conversation);
      setMessage('');
      setAttachment(null);
      setSelectedAnalysisKinds([]);
      if (fileInputRef.current) fileInputRef.current.value = '';
      await Promise.all([
        loadConversation(payload.conversation.id),
        loadConversationList(false),
      ]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '消息发送失败');
    } finally {
      setSubmitting(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (event.key !== 'Enter' || event.shiftKey) return;
    // Agent 工作中回车只作为文本框普通回车（换行），不发送也不触发停止。
    if (agentBusy) return;
    event.preventDefault();
    void submitMessage();
  }

  function toggleAnalysisKind(kind: AnalysisKind): void {
    setSelectedAnalysisKinds((current) => current.includes(kind)
      ? current.filter((item) => item !== kind)
      : [...current, kind]);
  }

  async function logout(): Promise<void> {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.href = '/login';
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#f7f7f5] text-slate-900">
      <aside className={`${sidebarOpen ? 'translate-x-0' : '-translate-x-full'} fixed inset-y-0 left-0 z-40 flex w-72 flex-col border-r border-slate-200 bg-white transition-transform md:static md:translate-x-0`}>
        <div className="flex h-16 items-center gap-3 border-b border-slate-100 px-4">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-slate-900 text-white">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">IP-Probtra</div>
            <div className="text-xs text-slate-500">专利工作 Agent</div>
          </div>
        </div>

        <div className="p-3">
          <Button onClick={newConversation} variant="outline" className="w-full justify-start rounded-xl">
            <Plus className="mr-2 h-4 w-4" />新对话
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto px-3 pb-3">
          <div className="mb-2 flex items-center gap-2 px-2 text-xs font-medium text-slate-400">
            <History className="h-3.5 w-3.5" />最近对话
          </div>
          <div className="space-y-1">
            {conversations.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => void chooseConversation(item.id)}
                className={`w-full rounded-xl px-3 py-2.5 text-left transition ${conversation?.id === item.id ? 'bg-slate-100' : 'hover:bg-slate-50'}`}
              >
                <div className="truncate text-sm font-medium">{item.title}</div>
                <div className="mt-1 truncate text-xs text-slate-400">{item.lastMessage || '新对话'}</div>
              </button>
            ))}
            {conversations.length === 0 ? (
              <div className="px-3 py-6 text-center text-xs text-slate-400">还没有历史对话</div>
            ) : null}
          </div>
        </div>

        <div className="space-y-1 border-t border-slate-100 p-3 text-sm">
          {user?.role === 'admin' ? (
            <Link href="/test/module-lab" className="flex items-center justify-between rounded-lg px-3 py-2 text-slate-600 hover:bg-slate-50">无效检测测试版<ChevronRight className="h-4 w-4" />
            </Link>
          ) : null}
          <Link href="/test/product-pipeline" className="flex items-center justify-between rounded-lg px-3 py-2 text-slate-600 hover:bg-slate-50">专利分析实验室<ChevronRight className="h-4 w-4" />
          </Link>
          <Link href="/history" className="flex items-center justify-between rounded-lg px-3 py-2 text-slate-600 hover:bg-slate-50">
            全部分析记录<ChevronRight className="h-4 w-4" />
          </Link>
          {user?.role === 'admin' ? (
            <Link href="/admin" className="flex items-center justify-between rounded-lg px-3 py-2 text-slate-600 hover:bg-slate-50">
              管理后台<ChevronRight className="h-4 w-4" />
            </Link>
          ) : null}
          <button type="button" onClick={() => void logout()} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-slate-500 hover:bg-slate-50">
            <LogOut className="h-4 w-4" />退出登录
          </button>
        </div>
      </aside>

      {sidebarOpen ? <button type="button" aria-label="关闭侧栏" className="fixed inset-0 z-30 bg-black/20 md:hidden" onClick={() => setSidebarOpen(false)} /> : null}

      <main className="relative flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white/90 px-4 backdrop-blur md:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Button size="icon" variant="ghost" className="md:hidden" onClick={() => setSidebarOpen(true)}>
              <Menu className="h-5 w-5" />
            </Button>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold md:text-base">{conversation?.title || '新的专利对话'}</h1>
              <p className="hidden text-xs text-slate-500 sm:block">直接提问；只有选中输入框下方的分析按钮才会启动后台工作流</p>
            </div>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="hidden sm:inline">{user?.name}</span>
            <span className="h-2 w-2 rounded-full bg-emerald-500" />
          </div>
        </header>

        <section className="flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 pb-44 pt-8 md:px-6">
            {loadingConversation ? (
              <div className="grid flex-1 place-items-center text-slate-400"><Loader2 className="h-6 w-6 animate-spin" /></div>
            ) : !conversation || conversation.messages.length === 0 ? (
              <div className="grid flex-1 place-items-center py-16">
                <div className="max-w-xl text-center">
                  <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-2xl bg-slate-900 text-white shadow-sm">
                    <Bot className="h-7 w-7" />
                  </div>
                  <h2 className="text-2xl font-semibold tracking-tight">今天要处理哪项专利工作？</h2>
                  <p className="mx-auto mt-3 max-w-lg text-sm leading-6 text-slate-500">
                    先在输入框下方选择需要的分析类型，系统才会启动相应后台流程。两个按钮可以同时选择；都不选时只由大模型直接回答问题。
                  </p>
                  <div className="mt-6 grid gap-2 text-left text-sm text-slate-500 sm:grid-cols-2">
                    <div className="rounded-xl border border-slate-200 bg-white p-3">“什么是专利的公开日？”</div>
                    <div className="rounded-xl border border-slate-200 bg-white p-3">“怎样理解权利要求的保护范围？”</div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="space-y-7">
                {conversation.messages.map((item) => {
                  const tools = toolRunsByAnchor.get(item.id) || [];
                  return (
                    <Fragment key={item.id}>
                      <div className={`flex gap-3 ${item.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                        {item.role === 'assistant' ? (
                          <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-900 text-white"><Bot className="h-4 w-4" /></div>
                        ) : null}
                        <div className={`max-w-[88%] ${item.role === 'user' ? 'order-first' : ''}`}>
                          <div className={`rounded-2xl px-4 py-3 text-sm leading-6 ${item.role === 'user' ? 'bg-slate-900 text-white' : 'border border-slate-200 bg-white text-slate-700 shadow-sm'}`}>
                            {item.attachment ? (
                              <div className={`mb-2 flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs ${item.role === 'user' ? 'bg-white/10 text-slate-100' : 'bg-slate-50 text-slate-500'}`}>
                                <FileText className="h-4 w-4 shrink-0" />
                                <span className="truncate">{item.attachment.fileName}</span>
                                <span className="shrink-0 opacity-70">{formatFileSize(item.attachment.fileSize)}</span>
                              </div>
                            ) : null}
                            <div className="whitespace-pre-wrap break-words">{item.content}</div>
                          </div>
                          {tools.length > 0 ? (
                            <div className="mt-3 space-y-2">
                              {tools.map((tool) => (
                                <AgentToolProgressCard
                                  key={tool.id}
                                  tool={tool}
                                  isAdmin={user?.role === 'admin'}
                                />
                              ))}
                            </div>
                          ) : null}
                        </div>
                        {item.role === 'user' ? (
                          <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-slate-200 bg-white text-slate-500"><UserRound className="h-4 w-4" /></div>
                        ) : null}
                      </div>
                      {tools.map((tool) => {
                        if (tool.toolKind === 'invalidity' && tool.analysisSessionId && tool.invalidityModule11Report) {
                          return (
                            <AgentInvalidityResultMessage
                              key={`${tool.id}-final-result`}
                              sessionId={tool.analysisSessionId}
                              summary={tool.invaliditySummary}
                              report={tool.invalidityModule11Report}
                            />
                          );
                        }
                        if (tool.toolKind === 'infringement' && tool.analysisSessionId && tool.infringementResult) {
                          return (
                            <AgentInfringementResultMessage
                              key={`${tool.id}-final-result`}
                              sessionId={tool.analysisSessionId}
                              result={tool.infringementResult}
                            />
                          );
                        }
                        return null;
                      })}
                    </Fragment>
                  );
                })}
                {submitting ? (
                  <div className="flex items-center gap-3 text-sm text-slate-400">
                    <div className="grid h-8 w-8 place-items-center rounded-lg bg-slate-900 text-white"><Bot className="h-4 w-4" /></div>
                    <Loader2 className="h-4 w-4 animate-spin" />正在理解你的任务…
                  </div>
                ) : null}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </section>

        <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-[#f7f7f5] via-[#f7f7f5] to-transparent px-4 pb-5 pt-12 md:px-6">
          <form onSubmit={(event) => void submitMessage(event)} className="pointer-events-auto mx-auto max-w-3xl">
            {error ? (
              <div className="mb-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>
            ) : null}
            <div className="rounded-2xl border border-slate-300 bg-white p-2 shadow-lg shadow-slate-200/60 focus-within:border-slate-400">
              {attachment ? (
                <div className="mx-1 mb-1 flex max-w-sm items-center gap-2 rounded-lg bg-slate-100 px-3 py-2 text-xs text-slate-600">
                  <FileText className="h-4 w-4 shrink-0" />
                  <span className="truncate">{attachment.name}</span>
                  <span className="shrink-0 text-slate-400">{formatFileSize(attachment.size)}</span>
                  <button type="button" aria-label="移除附件" onClick={() => setAttachment(null)} className="ml-auto rounded p-0.5 hover:bg-slate-200"><X className="h-3.5 w-3.5" /></button>
                </div>
              ) : null}
              <Textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={handleComposerKeyDown}
                placeholder="输入专利问题，或选择下方分析类型后上传材料…"
                className="min-h-14 max-h-40 resize-none border-0 bg-transparent px-3 py-2 shadow-none focus-visible:ring-0"
              />
              <div className="flex items-center justify-between px-1 pb-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf,.doc,.docx,.txt,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
                    className="hidden"
                    onChange={(event) => setAttachment(event.target.files?.[0] || null)}
                  />
                  <Button type="button" size="icon" variant="ghost" className="rounded-xl text-slate-500" onClick={() => fileInputRef.current?.click()}>
                    <Paperclip className="h-5 w-5" />
                    <span className="sr-only">上传附件</span>
                  </Button>
                  <span className="mx-0.5 h-5 w-px bg-slate-200" aria-hidden="true" />
                  <Button
                    type="button"
                    size="sm"
                    variant={selectedAnalysisKinds.includes('invalidity') ? 'default' : 'outline'}
                    aria-pressed={selectedAnalysisKinds.includes('invalidity')}
                    data-analysis-kind="invalidity"
                    className="h-8 rounded-xl px-2.5 text-xs"
                    onClick={() => toggleAnalysisKind('invalidity')}
                  >
                    <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />专利无效
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={selectedAnalysisKinds.includes('infringement') ? 'default' : 'outline'}
                    aria-pressed={selectedAnalysisKinds.includes('infringement')}
                    data-analysis-kind="infringement"
                    className="h-8 rounded-xl px-2.5 text-xs"
                    onClick={() => toggleAnalysisKind('infringement')}
                  >
                    <FileText className="mr-1.5 h-3.5 w-3.5" />专利侵权分析
                  </Button>
                </div>
                {agentBusy ? (
                  <Button
                    type="button"
                    size="icon"
                    disabled={stopping || busyInvalidityTools.length === 0}
                    onClick={() => void stopActiveInvalidityTasks()}
                    title={busyInvalidityTools.length > 0 ? '停止当前分析任务' : '专利侵权分析暂不支持中途停止'}
                    className="rounded-xl bg-rose-600 text-white hover:bg-rose-700"
                  >
                    {stopping ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
                    <span className="sr-only">停止</span>
                  </Button>
                ) : (
                  <Button type="submit" size="icon" disabled={submitting || (!message.trim() && !attachment)} className="rounded-xl bg-slate-900 text-white hover:bg-slate-800">
                    {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                    <span className="sr-only">发送</span>
                  </Button>
                )}
              </div>
            </div>
            <p className="mt-2 text-center text-[11px] text-slate-400">
              {selectedAnalysisKinds.length === 0
                ? '未选择分析类型：本次只由大模型回答，不会启动后台分析。'
                : `本次将启动：${selectedAnalysisKinds.map((kind) => kind === 'invalidity' ? '专利无效' : '专利侵权分析').join('、')}。`}
            </p>
          </form>
        </div>
      </main>
    </div>
  );
}
