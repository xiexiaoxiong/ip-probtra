'use client';

import { Bot } from 'lucide-react';
import { AgentInvalidityReport } from '@/components/agent-invalidity-report';
import type { AgentInvalidityModule11Report } from '@/lib/agent-invalidity-report';
import type { AgentInvaliditySummary } from '@/lib/agent-invalidity-summary';

export function AgentInvalidityResultMessage({
  sessionId,
  summary,
  report,
}: {
  sessionId: string;
  summary: AgentInvaliditySummary | null | undefined;
  report: AgentInvalidityModule11Report;
}) {
  return (
    <div
      className="flex justify-start gap-3"
      data-agent-final-message="invalidity"
      data-agent-message-role="assistant"
    >
      <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-900 text-white">
        <Bot className="h-4 w-4" />
      </div>
      <article className="min-w-0 max-w-[calc(100%-2.75rem)] overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-700 shadow-sm">
        <header className="border-b border-slate-100 px-4 py-4" data-agent-invalidity-result="true">
          <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400">分析结论</div>
          {summary ? (
            <>
              <p className="mt-1 text-base font-semibold text-slate-900">{summary.headline}</p>
              <p className="mt-1 text-sm leading-6 text-slate-600">{summary.detail}</p>
              <p className="mt-3 text-sm font-medium text-slate-800">{summary.patentLabel}</p>
              {summary.claims.length ? (
                <div className="mt-2 space-y-2">
                  {summary.claims.map((claim) => (
                    <div key={claim.id} className={`rounded-lg border px-3 py-2.5 ${claim.needsAttention ? 'border-amber-200 bg-amber-50/70' : 'border-slate-200 bg-slate-50/70'}`}>
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-xs font-medium text-slate-700">{claim.claimLabel}</span>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] ${claim.needsAttention ? 'bg-amber-100 text-amber-700' : 'bg-emerald-100 text-emerald-700'}`}>{claim.statusLabel}</span>
                      </div>
                      <p className="mt-1 text-xs leading-5 text-slate-600">{claim.explanation}</p>
                    </div>
                  ))}
                </div>
              ) : null}
              {summary.hasReport ? (
                <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                  <span>资料 {summary.documentCount} 份</span>
                  <span>逐项披露 {summary.disclosureCount} 条</span>
                  <span>未解决缺口 {summary.openGapCount} 项</span>
                </div>
              ) : null}
              <p className="mt-3 text-xs leading-5 text-slate-500">{summary.evidenceBoundary}</p>
            </>
          ) : (
            <p className="mt-1 text-sm leading-6 text-slate-600">模块 11 已生成完整结果，以下内容来自本次调查的不可变报告快照。</p>
          )}
        </header>
        <AgentInvalidityReport sessionId={sessionId} report={report} />
      </article>
    </div>
  );
}
