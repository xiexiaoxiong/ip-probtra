'use client';

import Link from 'next/link';
import { Bot, ChevronRight, Download } from 'lucide-react';
import { ResultsScoreTable } from '@/components/results-score-table';
import { sortProductsByComparisonScore } from '@/lib/results-consistency';
import type { AgentInfringementResult } from '@/lib/agent-infringement-result';

export function AgentInfringementResultMessage({
  sessionId,
  result,
}: {
  sessionId: string;
  result: AgentInfringementResult;
}) {
  const visibleProducts = sortProductsByComparisonScore(result.products, result.comparisons).slice(0, 10);

  return (
    <div
      className="flex justify-start gap-3"
      data-agent-final-message="infringement"
      data-agent-message-role="assistant"
    >
      <div className="mt-1 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-900 text-white">
        <Bot className="h-4 w-4" />
      </div>
      <article className="min-w-0 max-w-[calc(100%-2.75rem)] overflow-hidden rounded-2xl border border-slate-200 bg-white text-slate-700 shadow-sm">
        <header className="border-b border-slate-100 px-4 py-4" data-agent-infringement-result="true">
          <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400">分析结论</div>
          <p className="mt-1 text-base font-semibold text-slate-900">{result.headline}</p>
          <p className="mt-1 text-sm leading-6 text-slate-600">{result.detail}</p>
          <p className="mt-3 text-sm font-medium text-slate-800">{result.patentLabel}</p>
          <div className="mt-3 grid grid-cols-2 gap-2 text-center sm:grid-cols-5">
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-2">
              <div className="text-base font-semibold text-slate-800">{result.productCount}</div>
              <div className="text-[10px] text-slate-500">检索商品</div>
            </div>
            <div className="rounded-lg border border-green-200 bg-green-50 px-2 py-2">
              <div className="text-base font-semibold text-green-700">{result.riskCounts.high_risk}</div>
              <div className="text-[10px] text-green-700">高分命中</div>
            </div>
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-2 py-2">
              <div className="text-base font-semibold text-amber-700">{result.riskCounts.medium_risk}</div>
              <div className="text-[10px] text-amber-700">中风险候选</div>
            </div>
            <div className="rounded-lg border border-red-200 bg-red-50 px-2 py-2">
              <div className="text-base font-semibold text-red-700">{result.riskCounts.low_risk}</div>
              <div className="text-[10px] text-red-700">低分命中</div>
            </div>
            <div className="rounded-lg border border-slate-200 bg-slate-100 px-2 py-2">
              <div className="text-base font-semibold text-slate-700">{result.riskCounts.clear_low_risk}</div>
              <div className="text-[10px] text-slate-600">疑似不侵权</div>
            </div>
          </div>
          <p className="mt-3 text-xs leading-5 text-slate-500">{result.evidenceBoundary}</p>
        </header>

        <section className="space-y-3 px-4 py-4" data-agent-infringement-results-table>
          <div className="flex flex-wrap items-end justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-slate-900">商品技术特征比对表</h3>
              <p className="mt-0.5 text-xs text-slate-500">
                {result.productCount > 10 ? 'Agent 中显示总分最高的 10 件商品；' : ''}
                点击商品名称可查看完整 Claim Chart。
              </p>
            </div>
            <span className="text-[11px] text-slate-400">已比对 {result.analyzedProductCount} 件</span>
          </div>
          {visibleProducts.length ? (
            <ResultsScoreTable
              products={visibleProducts}
              comparisons={result.comparisons}
              sessionId={sessionId}
            />
          ) : (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-3 text-xs leading-5 text-amber-800">
              当前 session 没有可展示的逐商品比对表，请结合过程卡中的失败阶段和错误原因处理。
            </div>
          )}
        </section>

        <footer className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-slate-100 bg-slate-50/70 px-4 py-3">
          <Link
            href={`/results?session=${encodeURIComponent(sessionId)}`}
            className="inline-flex items-center gap-1 text-xs font-medium text-blue-700 hover:underline"
          >
            查看完整分析详情<ChevronRight className="h-3.5 w-3.5" />
          </Link>
          <a
            href={`/api/analysis/${encodeURIComponent(sessionId)}/export`}
            className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 hover:underline"
          >
            <Download className="h-3.5 w-3.5" />导出 XLSX 报告
          </a>
        </footer>
      </article>
    </div>
  );
}
