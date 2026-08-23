export type AgentTaskStepState =
  | 'pending'
  | 'running'
  | 'completed'
  | 'waiting'
  | 'partial'
  | 'error'
  | 'cancelled';

export type AgentTaskProgressStep = {
  id: string;
  label: string;
  description: string;
  state: AgentTaskStepState;
};

export type AgentTaskProgress = {
  overallStatus: string;
  currentLabel: string;
  currentDetail: string;
  completedCount: number;
  totalCount: number;
  steps: AgentTaskProgressStep[];
};

type JsonObject = Record<string, unknown>;

const INFRINGEMENT_GROUPS = [
  {
    id: 'patent-read',
    label: '读取并解析专利',
    description: '提取权利要求、说明书、摘要和附图',
    sourceStepIds: [1],
  },
  {
    id: 'keyword-plan',
    label: '理解发明点并生成检索词',
    description: '识别行业、提炼必要特征并生成商品检索词',
    sourceStepIds: [2, 3],
  },
  {
    id: 'product-search',
    label: '检索相关商品',
    description: '按本次关键词检索并补全商品信息',
    sourceStepIds: [4],
  },
  {
    id: 'claim-compare',
    label: '逐项比对并整理结果',
    description: '比对独立权利要求特征并汇总分析结果',
    sourceStepIds: [5, 6],
  },
] as const;

const INVALIDITY_STEPS = [
  ['target-patent', '读取目标专利', '解析并冻结目标专利原文、权利要求和附图'],
  ['critical-date', '确定关键日', '核验申请日、优先权日和专利级关键日'],
  ['inventive-profile', '总结核心发明点', '提炼独立权利要求的核心发明构思'],
  ['initial-query-plan', '生成首轮检索词', '形成并冻结首轮五组检索方案'],
  ['initial-search', '首轮检索与证据核验', '检索候选、取得全文并核验日期资格'],
  ['single-reference', '单篇新颖性比对', '逐篇对比全部权利要求特征'],
  ['closest-prior-art', '选择 D1 与区别特征', '选择最接近现有技术并冻结区别特征'],
  ['obviousness-precheck', '显而易见性预分析', '分析惯用手段、技术启示、修改路径和技术效果'],
  ['gap-search', '进一步检索与补证', '按区别特征推进最多五轮检索和逐篇比对'],
  ['inventive-step', '创造性组合分析', '按三步法核验组合覆盖与技术启示'],
  ['lawyer-report', '形成律师报告', '整理文字分析、前十矩阵和全部对比表'],
] as const;

function objectValue(value: unknown): JsonObject | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

function stringValue(value: unknown): string {
  return value == null ? '' : String(value).trim();
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function sourceStepState(value: unknown): AgentTaskStepState {
  switch (stringValue(value).toLowerCase()) {
    case 'completed':
    case 'complete':
    case 'succeeded':
      return 'completed';
    case 'running':
      return 'running';
    case 'waiting_input':
      return 'waiting';
    case 'partial':
      return 'partial';
    case 'error':
    case 'failed':
      return 'error';
    case 'cancelled':
      return 'cancelled';
    default:
      return 'pending';
  }
}

function groupedState(states: AgentTaskStepState[]): AgentTaskStepState {
  if (states.includes('running')) return 'running';
  if (states.includes('waiting')) return 'waiting';
  if (states.includes('error')) return 'error';
  if (states.includes('cancelled')) return 'cancelled';
  if (states.includes('partial')) return 'partial';
  if (states.length > 0 && states.every((state) => state === 'completed')) return 'completed';
  if (states.includes('completed')) return 'running';
  return 'pending';
}

function currentStep(steps: AgentTaskProgressStep[]): AgentTaskProgressStep | undefined {
  return steps.find((step) => ['running', 'waiting', 'error', 'partial', 'cancelled'].includes(step.state))
    || steps.find((step) => step.state === 'pending')
    || steps.at(-1);
}

export function infringementTaskProgress(value: unknown): AgentTaskProgress | null {
  const payload = objectValue(value);
  const session = objectValue(payload?.session);
  if (!session) return null;
  const rawSteps = Array.isArray(session.steps)
    ? session.steps.map(objectValue).filter((step): step is JsonObject => Boolean(step))
    : [];
  const byId = new Map(rawSteps.map((step) => [numberValue(step.id), step]));
  const steps: AgentTaskProgressStep[] = INFRINGEMENT_GROUPS.map((group) => {
    const sourceSteps = group.sourceStepIds.map((id) => byId.get(id)).filter(Boolean) as JsonObject[];
    return {
      id: group.id,
      label: group.label,
      description: group.description,
      state: groupedState(sourceSteps.map((step) => sourceStepState(step.status))),
    };
  });
  const overallStatus = stringValue(session.status) || 'running';
  const activeSource = rawSteps.find((step) => sourceStepState(step.status) === 'running')
    || rawSteps.find((step) => sourceStepState(step.status) === 'waiting');
  const active = currentStep(steps);
  const completedCount = steps.filter((step) => step.state === 'completed').length;
  const completed = overallStatus === 'completed' && completedCount === steps.length;
  return {
    overallStatus,
    currentLabel: completed
      ? '专利侵权分析已经完成'
      : stringValue(activeSource?.name) || active?.label || '正在准备分析',
    currentDetail: completed
      ? '四个业务模块均已收口，可以查看分析结果。'
      : stringValue(activeSource?.description) || active?.description || '等待后台返回下一项真实进度。',
    completedCount,
    totalCount: steps.length,
    steps,
  };
}

function invalidityStage(status: string, iterationNo: number): number {
  switch (status) {
    case 'awaiting_critical_date':
    case 'critical_date_review':
      return 2;
    case 'initial_search':
      return 5;
    case 'single_reference_review':
      return 6;
    case 'closest_prior_art_selected':
      return 7;
    case 'obviousness_precheck':
      return 8;
    case 'gap_search':
      return 9;
    case 'combination_review':
      return 10;
    case 'novelty_evidence_complete':
    case 'inventive_step_evidence_complete':
    case 'search_budget_exhausted':
    case 'exhausted':
      return 11;
    case 'needs_human_review':
      return iterationNo > 0 ? 9 : 2;
    case 'partial':
    case 'failed':
    case 'cancelled':
      return iterationNo > 1 ? 9 : iterationNo === 1 ? 6 : 1;
    default:
      return 1;
  }
}

function invalidityActiveState(status: string): AgentTaskStepState {
  if (status === 'needs_human_review') return 'waiting';
  if (status === 'partial') return 'partial';
  if (status === 'failed') return 'error';
  if (status === 'cancelled') return 'cancelled';
  return 'running';
}

function successfulInvalidityModuleCodes(value: unknown): Set<string> {
  const payload = objectValue(value);
  const report = objectValue(payload?.report);
  const moduleRuns = Array.isArray(report?.module_runs)
    ? report.module_runs.map(objectValue).filter((run): run is JsonObject => Boolean(run))
    : [];
  return new Set(
    moduleRuns
      .filter((run) => ['completed', 'succeeded'].includes(stringValue(run.status).toLowerCase()))
      .map((run) => stringValue(run.module_code).toUpperCase())
      .filter(Boolean),
  );
}

export function invalidityTaskProgress(value: unknown): AgentTaskProgress | null {
  const payload = objectValue(value);
  const investigation = objectValue(payload?.investigation);
  if (!investigation) return null;
  const overallStatus = stringValue(investigation.status) || 'running';
  const claims = Array.isArray(payload?.claim_investigations)
    ? payload.claim_investigations
      .map(objectValue)
      .filter((claim): claim is JsonObject => claim !== null && claim.in_scope !== false)
    : [];
  const successfulTerminals = new Set([
    'novelty_evidence_complete',
    'inventive_step_evidence_complete',
    'search_budget_exhausted',
    'exhausted',
  ]);
  const claimStages = claims.map((claim) => ({
    status: stringValue(claim.status),
    iterationNo: numberValue(claim.current_iteration_no),
    stage: invalidityStage(stringValue(claim.status), numberValue(claim.current_iteration_no)),
  }));
  const allClaimsComplete = claimStages.length > 0
    && claimStages.every((claim) => successfulTerminals.has(claim.status));
  const fullyCompleted = ['completed', 'search_budget_exhausted', 'exhausted'].includes(overallStatus)
    && allClaimsComplete;
  const notStarted = new Set(['created', 'queued', 'pending']);
  const started = claimStages.filter((claim) => !notStarted.has(claim.status));
  const unresolvedStarted = started.filter((claim) => !successfulTerminals.has(claim.status));
  // 排队中的权利要求尚未开始首轮，不得参与“当前事项”定位，否则会把已推进
  // 到后续阶段的任务错误显示回第一模块；全部排队时才显示首轮前的起始阶段。
  const activeStage = fullyCompleted
    ? 11
    : Math.max(1, Math.min(11, unresolvedStarted.length > 0
      ? Math.min(...unresolvedStarted.map((claim) => claim.stage))
      : started.length > 0
        ? Math.min(...started.map((claim) => claim.stage))
        : claimStages.length > 0 ? 3 : 1));
  const activeState = invalidityActiveState(overallStatus === 'completed' && !fullyCompleted ? 'partial' : overallStatus);
  const successfulModuleCodes = successfulInvalidityModuleCodes(value);
  const steps: AgentTaskProgressStep[] = INVALIDITY_STEPS.map(([id, label, description], index) => {
    const stage = index + 1;
    const persistedDownstreamCompletion = (
      stage === 10 && successfulModuleCodes.has('I4_I_INVENTIVE_STEP')
    ) || (
      stage === 11 && successfulModuleCodes.has('I5_REPORT')
    );
    return {
      id,
      label,
      description,
      state: fullyCompleted || stage < activeStage || persistedDownstreamCompletion
        ? 'completed'
        : stage === activeStage
          ? activeState
          : 'pending',
    };
  });
  const active = steps[activeStage - 1];
  const completedCount = steps.filter((step) => step.state === 'completed').length;
  const iterationNo = Math.max(0, ...claimStages.map((claim) => claim.iterationNo));
  const terminalClaims = claimStages.filter((claim) => successfulTerminals.has(claim.status)).length;
  let currentDetail = active.description;
  if (activeStage === 9 && iterationNo > 0) {
    const iterationPrefix = ['created', 'queued', 'running'].includes(overallStatus)
      ? `正在推进第 ${iterationNo} 个持久化检索迭代`
      : `第 ${iterationNo} 个持久化检索迭代已收口，但仍有证据或外部服务缺口`;
    const downstreamSuffix = successfulModuleCodes.has('I5_REPORT')
      ? '；组合分析和报告已按当前证据完成。'
      : '；只统计本次调查的文献和比对。';
    currentDetail = `${iterationPrefix}${downstreamSuffix}`;
  } else if (claimStages.length > 1) {
    currentDetail = `${terminalClaims}/${claimStages.length} 项独立权利要求已经到达工作流终态。`;
  }
  if (fullyCompleted) {
    currentDetail = overallStatus === 'search_budget_exhausted' || overallStatus === 'exhausted'
      ? '五轮自动检索已收口，报告已按当前证据形成并等待专业复核。'
      : '本次调查的全部独立权利要求和报告均已收口。';
  }
  return {
    overallStatus,
    currentLabel: fullyCompleted ? '专利无效分析已经完成' : active.label,
    currentDetail,
    completedCount,
    totalCount: steps.length,
    steps,
  };
}
