'use client';

import { Download, FileSpreadsheet } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import type { AgentInvalidityModule11Report } from '@/lib/agent-invalidity-report';

function statusClass(status: string): string {
  if (['disclosed', 'explicit', 'direct_and_unambiguous', 'structural_equivalent', 'necessarily_implicit'].includes(status)) {
    return 'border-emerald-200 bg-emerald-50 text-emerald-700';
  }
  if (status === 'not_disclosed') return 'border-slate-200 bg-slate-100 text-slate-600';
  if (status === 'analysis_failed') return 'border-rose-200 bg-rose-50 text-rose-700';
  return 'border-amber-200 bg-amber-50 text-amber-700';
}

export function AgentInvalidityReport({
  sessionId,
  report,
}: {
  sessionId: string;
  report: AgentInvalidityModule11Report;
}) {
  return (
    <div className="space-y-3 bg-slate-50/50 px-4 py-4" data-agent-module11-report="true">
      <div>
        <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400">模块 11 · 最终结果</div>
        <p className="mt-1 text-sm font-semibold text-slate-800">律师文字分析、Top 10 横向表与完整逐篇导出</p>
      </div>

      <details open className="group rounded-lg border border-slate-200 bg-white" data-agent-module11-narrative>
        <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-3 text-sm font-medium text-slate-800">
          第一部分：给律师可读的文字分析
          <span className="text-xs font-normal text-slate-400">{report.narratives.length} 项独立权利要求</span>
        </summary>
        <div className="space-y-4 border-t border-slate-100 px-3 py-4">
          {report.narratives.length ? report.narratives.map((narrative) => (
            <article key={narrative.key} className="rounded-lg border border-slate-200 bg-slate-50/60 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-sm font-semibold text-slate-800">{narrative.claimLabel}</h4>
                <Badge variant="outline" className={narrative.evidenceComplete ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-amber-200 bg-amber-50 text-amber-700'}>
                  {narrative.conclusion}
                </Badge>
              </div>
              <div className="mt-3 space-y-3 text-sm leading-7 text-slate-700">
                {narrative.paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
              </div>
            </article>
          )) : <p className="text-sm text-slate-500">本次报告尚未形成模块10的律师可读转写。</p>}
        </div>
      </details>

      <details open className="group rounded-lg border border-slate-200 bg-white" data-agent-module11-top10>
        <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-3 text-sm font-medium text-slate-800">
          第二部分：相似度最高的 10 篇对比文件横向表
          <span className="text-xs font-normal text-slate-400">按已确认披露特征排序</span>
        </summary>
        <div className="space-y-4 border-t border-slate-100 px-3 py-4">
          {report.matrices.length ? report.matrices.map((matrix) => (
            <section key={matrix.key} className="space-y-2">
              <div>
                <h4 className="text-sm font-semibold text-slate-800">{matrix.claimLabel}</h4>
                <p className="mt-1 text-xs leading-5 text-slate-500">{matrix.rankingBasis}</p>
              </div>
              <div className="max-w-full overflow-x-auto rounded-lg border border-slate-200">
                <table className="min-w-[1800px] border-collapse text-center text-xs" data-agent-module11-top10-matrix>
                  <thead>
                    <tr className="bg-slate-100 text-slate-700">
                      <th className="sticky left-0 z-20 min-w-80 border-b border-r border-slate-200 bg-slate-100 p-2 text-left">独立权利要求技术特征</th>
                      {matrix.documents.map((document) => (
                        <th key={document.id} className="min-w-40 border-b border-r border-slate-200 p-2 align-top">
                          <p>第 {document.rank} 名{document.isCurrentD1 ? ' · 当前 D1' : ''}</p>
                          <p className="mt-1 font-normal leading-5">{document.label}</p>
                          <p className="mt-1 font-normal text-slate-500">已披露 {document.confirmedDisclosedFeatureCount}/{document.totalFeatureCount || matrix.features.length}</p>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {matrix.features.map((feature) => (
                      <tr key={feature.id}>
                        <td className="sticky left-0 z-10 border-b border-r border-slate-200 bg-white p-2 text-left">
                          <p className="font-medium text-slate-700">{feature.featureKey}</p>
                          <p className="mt-1 leading-5 text-slate-500">{feature.limitationText}</p>
                        </td>
                        {feature.cells.map((cell) => (
                          <td key={`${feature.id}-${cell.documentId}`} className="border-b border-r border-slate-200 p-2">
                            <Badge variant="outline" className={statusClass(cell.status)}>{cell.label}</Badge>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )) : <p className="text-sm text-slate-500">本次尚无完成逐特征分析的 Top 10 矩阵。</p>}
        </div>
      </details>

      <div className="rounded-lg border border-blue-200 bg-blue-50/70 p-3" data-agent-module11-export>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-start gap-2">
            <FileSpreadsheet className="mt-0.5 h-4 w-4 shrink-0 text-blue-700" />
            <div>
              <p className="text-sm font-medium text-blue-950">第三部分：全部对比文件逐篇 Claim Chart</p>
              <p className="mt-1 text-xs leading-5 text-blue-800">
                共 {report.analyzedDocumentCount} 篇已分析对比文件；导出的 XLSX 为每篇对比文件单独建立一个 Sheet。
              </p>
            </div>
          </div>
          <a
            href={`/api/invalidity/session/${encodeURIComponent(sessionId)}/export`}
            download
            className="inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md bg-blue-700 px-3 text-xs font-medium text-white hover:bg-blue-800"
          >
            <Download className="h-3.5 w-3.5" />
            导出 XLSX
          </a>
        </div>
      </div>
    </div>
  );
}
