export type AgentInvalidityClaimSummary = {
  id: string;
  claimLabel: string;
  status: string;
  statusLabel: string;
  explanation: string;
  needsAttention: boolean;
};

export type AgentInvaliditySummary = {
  patentLabel: string;
  overallStatus: string;
  headline: string;
  detail: string;
  claims: AgentInvalidityClaimSummary[];
  documentCount: number;
  disclosureCount: number;
  openGapCount: number;
  failedRunCount: number;
  hasReport: boolean;
  evidenceBoundary: string;
};

type JsonObject = Record<string, unknown>;

function record(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : {};
}

function rows(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function text(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function claimStatusLabel(status: string): string {
  if (status === 'novelty_evidence_complete') return '单篇新颖性证据已形成';
  if (status === 'inventive_step_evidence_complete') return '创造性组合证据已形成';
  if (['search_budget_exhausted', 'exhausted'].includes(status)) return '五轮检索已收口';
  if (status === 'needs_human_review') return '等待人工确认';
  if (status === 'partial') return '部分完成';
  if (['failed', 'error'].includes(status)) return '运行失败';
  if (status === 'cancelled') return '已取消';
  if (['completed', 'complete', 'succeeded'].includes(status)) return '已完成';
  return '分析中';
}

function claimExplanation(claim: JsonObject, status: string): string {
  const terminalReason = text(claim.terminal_reason);
  const resultSummary = record(claim.result_summary);
  const persistedSummary = text(resultSummary.summary || resultSummary.conclusion_text);
  if (status === 'novelty_evidence_complete') {
    return '已找到一份日期合格文献，单独覆盖该独立权利要求的全部限制；证据链达到自动流程标准，仍需律师复核。';
  }
  if (status === 'inventive_step_evidence_complete') {
    return '没有用多篇文献拼接新颖性；当前多文献组合及组合理由已达到自动流程标准，仍需律师复核。';
  }
  if (['search_budget_exhausted', 'exhausted'].includes(status)) {
    return '五轮补证预算已经执行完，仍有未解决证据缺口；这不表示该权利要求已经被证明稳定或有效。';
  }
  if (status === 'needs_human_review') {
    return terminalReason || persistedSummary || '关键日、日期资格或其他会改变法律事实的事项需要人工确认。';
  }
  if (status === 'partial') {
    return terminalReason || persistedSummary || '系统已保留当前证据，但仍有未完成步骤或未解决缺口。';
  }
  if (['failed', 'error'].includes(status)) {
    return terminalReason || persistedSummary || '本次自动分析没有完整完成；已有证据仍被保留，失败不等于专利稳定。';
  }
  if (status === 'cancelled') return terminalReason || '本次任务已取消，已有运行记录仍保留。';
  if (['completed', 'complete', 'succeeded'].includes(status)) {
    return persistedSummary || terminalReason || '该独立权利要求已经到达当前自动流程终态。';
  }
  const iteration = Number(claim.current_iteration_no || 0);
  return iteration > 0
    ? `正在推进第 ${iteration} 个持久化检索迭代，当前结论会随新证据更新。`
    : '正在读取目标专利、核验日期并检索可用证据。';
}

function isOpenGap(gap: JsonObject): boolean {
  return ![
    'closed',
    'resolved',
    'resolved_with_evidence',
    'duplicate',
  ].includes(text(gap.status).toLowerCase());
}

function isFailedRun(run: JsonObject): boolean {
  return ['failed', 'error'].includes(text(run.status).toLowerCase())
    || Boolean(text(run.error_code || run.error_message));
}

export function buildAgentInvaliditySummary(payload: unknown): AgentInvaliditySummary | null {
  const root = record(payload);
  const investigation = record(root.investigation);
  const report = record(root.report);
  const session = record(root.session);
  const liveClaims = rows(root.claim_investigations);
  const reportClaims = rows(report.claim_investigations || report.claims);
  const claimsSource = liveClaims.length ? liveClaims : reportClaims;
  const claims = claimsSource
    .filter((claim) => claim.in_scope !== false && text(claim.claim_type || 'independent') !== 'dependent')
    .map((claim, index): AgentInvalidityClaimSummary => {
      const status = text(claim.status).toLowerCase() || 'running';
      const claimId = text(claim.claim_id || claim.claim_number) || String(index + 1);
      return {
        id: text(claim.id) || `claim-${claimId}`,
        claimLabel: `独立权利要求 ${claimId}`,
        status,
        statusLabel: claimStatusLabel(status),
        explanation: claimExplanation(claim, status),
        needsAttention: ['partial', 'failed', 'error', 'needs_human_review', 'search_budget_exhausted', 'exhausted'].includes(status),
      };
    });

  const targetPatent = Object.keys(record(report.target_patent)).length
    ? record(report.target_patent)
    : record(record(investigation.source_snapshot).patent_snapshot);
  const patentNumber = text(targetPatent.patent_number || targetPatent.publication_number);
  const patentTitle = text(targetPatent.title);
  const patentLabel = [patentNumber, patentTitle ? `《${patentTitle}》` : ''].filter(Boolean).join('')
    || '当前专利';
  const overallStatus = text(investigation.status || session.status).toLowerCase() || 'running';
  const attentionCount = claims.filter((claim) => claim.needsAttention).length;
  const successCount = claims.filter((claim) => [
    'novelty_evidence_complete',
    'inventive_step_evidence_complete',
    'completed',
    'complete',
    'succeeded',
  ].includes(claim.status)).length;
  const runningCount = claims.filter((claim) => !claim.needsAttention && ![
    'novelty_evidence_complete',
    'inventive_step_evidence_complete',
    'completed',
    'complete',
    'succeeded',
    'cancelled',
  ].includes(claim.status)).length;

  let headline = '无效证据分析仍在进行';
  let detail = claims.length
    ? `${claims.length} 项独立权利要求正在分别推进。`
    : '系统正在建立独立权利要求调查记录。';
  if (attentionCount > 0) {
    const automaticTaskEnded = [
      'partial',
      'failed',
      'cancelled',
      'exhausted',
      'search_budget_exhausted',
    ].includes(overallStatus);
    headline = automaticTaskEnded
      ? `${attentionCount} 项独立权利要求仍有证据缺口；本次自动任务已结束`
      : `${attentionCount} 项独立权利要求需要继续处理`;
    detail = automaticTaskEnded
      ? '已有证据和报告均已保存；如需补强，应由律师决定补证或另行调查。“未找到”或“证据不足”不能反向证明专利稳定。'
      : '请查看下方逐项解释；“未找到”或“证据不足”都不能反向证明专利稳定。';
  } else if (claims.length > 0 && successCount === claims.length) {
    headline = '当前自动证据分析已经收口';
    detail = '下方状态表示证据包达到自动流程标准，不是行政机关或律师的最终法律结论。';
  } else if (runningCount === 0 && claims.length > 0) {
    headline = '当前任务已经停止推进';
    detail = '请查看每项独立权利要求的停止原因和证据边界。';
  }

  return {
    patentLabel,
    overallStatus,
    headline,
    detail,
    claims,
    documentCount: rows(report.documents).length,
    disclosureCount: rows(report.feature_disclosures || report.disclosures).length,
    openGapCount: rows(report.gap_items || report.gaps).filter(isOpenGap).length,
    failedRunCount: rows(report.module_runs).filter(isFailedRun).length,
    hasReport: Object.keys(report).length > 0,
    evidenceBoundary: '本结果仅说明当前检索和证据分析达到的状态；检索未命中、证据不足或多篇累计覆盖都不能反向证明专利稳定或有效。',
  };
}
