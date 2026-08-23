'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import Image from 'next/image';
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Clock,
  FileSearch,
  FileText,
  FlaskConical,
  Lightbulb,
  Loader2,
  Play,
  RefreshCw,
  Scale,
  Search,
  XCircle,
} from 'lucide-react';
import { UploadForm } from '@/components/upload-form';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion';
import {
  Alert,
  AlertDescription,
  AlertTitle,
} from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { invalidityDateClassification } from '@/lib/invalidity-date-classification';
import {
  buildAgentInvaliditySummary,
  type AgentInvaliditySummary,
} from '@/lib/agent-invalidity-summary';
import { invalidityTaskProgress } from '@/lib/agent-task-progress';
import {
  buildPatsnapSearchPlan,
  type PatsnapQueryRole,
} from '@/lib/invalidity-p002-test-flow';

type JsonObject = Record<string, unknown>;
type BusinessModuleId =
  | 'target'
  | 'date'
  | 'profile'
  | 'query'
  | 'evidence'
  | 'singleReference'
  | 'closestPriorArt'
  | 'obviousnessPrecheck'
  | 'gapSearch'
  | 'inventiveStep'
  | 'report';
type RunMode = 'current' | 'fixture';

interface BusinessModule {
  id: BusinessModuleId;
  step: number;
  name: string;
  question: string;
  input: string;
  output: string;
  technicalCodes: string[];
  icon: typeof FileText;
}

interface ChildRun {
  code: string;
  ok: boolean;
  complete?: boolean;
  status: string;
  output: JsonObject;
  moduleRunId?: string;
  documentId?: string;
  roundIteration?: number;
  matrixPlan?: boolean;
  reusedFromI4sRunId?: string;
  isCurrentAttempt?: boolean;
  attemptNumber?: number;
  pipelineAttempts?: number;
  error?: string;
  attempts?: number;
  queueSeconds?: number;
  executionSeconds?: number;
  raw: JsonObject;
}

interface BusinessState {
  phase: 'idle' | 'running' | 'done';
  mode?: RunMode;
  ok?: boolean;
  headline?: string;
  facts?: string[];
  elapsedSeconds?: number;
  children?: ChildRun[];
  reportSnapshot?: JsonObject | null;
  module6Batch?: JsonObject | null;
  module9Batch?: JsonObject | null;
}

const BUSINESS_MODULES: BusinessModule[] = [
  {
    id: 'target',
    step: 1,
    name: '读取目标专利',
    question: '系统读到的是不是律师手上的那件专利？',
    input: '输入：一件专利',
    output: '输出：著录日期、摘要、全部权利要求、说明书章节和全部附图',
    technicalCodes: ['I1_TARGET_SNAPSHOT'],
    icon: FileText,
  },
  {
    id: 'date',
    step: 2,
    name: '确定检索截止日',
    question: '哪些日期以前公开的资料，才可能成为现有技术？',
    input: '输入：申请日、优先权日',
    output: '输出：本案统一采用的关键日及依据',
    technicalCodes: ['I1_5_CLAIM_DATES'],
    icon: Clock,
  },
  {
    id: 'profile',
    step: 3,
    name: '总结核心发明点',
    question: '这件专利真正区别于同类技术的核心发明点是什么？',
    input: '输入：完整专利和独立权利要求',
    output: '输出：核心发明点总结和每个发明点对应的技术特征',
    technicalCodes: ['I2_INVENTIVE_PROFILE'],
    icon: Lightbulb,
  },
  {
    id: 'query',
    step: 4,
    name: '生成检索关键词',
    question: '围绕核心技术特征应当用哪些关键词和检索式查找现有技术？',
    input: '输入：模块3总结的核心发明点与技术特征',
    output: '输出：分线检索关键词与检索式组合',
    technicalCodes: ['I2_QUERY_PLAN'],
    icon: Search,
  },
  {
    id: 'evidence',
    step: 5,
    name: '首轮检索与证据核验',
    question: '首轮检索找到哪些资料，哪些已经取得全文并通过日期核验？',
    input: '输入：首轮检索词和关键日',
    output: '输出：前 10 条原始命中、合并/排除理由、实际取文情况和日期分类',
    technicalCodes: [
      'I3_PATENT_SEARCH',
      'I3_NPL_SEARCH',
      'I3_CANDIDATE_FILTER',
      'I3_FETCH',
      'I3_QUALIFY',
    ],
    icon: FileSearch,
  },
  {
    id: 'singleReference',
    step: 6,
    name: '单篇新颖性比对',
    question: '每一份对比文件单独看，是否完整公开了独立权利要求？',
    input: '输入：独立权利要求和逐份合格全文',
    output: '输出：每份资料各自的逐特征披露矩阵',
    technicalCodes: ['I4_S_SINGLE_REFERENCE'],
    icon: Scale,
  },
  {
    id: 'closestPriorArt',
    step: 7,
    name: '选择 D1 与冻结区别特征',
    question: '哪一份是最接近现有技术，目标权利要求还剩哪些区别特征？',
    input: '输入：全部单篇比对结果和日期资格',
    output: '输出：D1、选择理由和带结构作用的区别特征集合',
    technicalCodes: ['I4_C_CLOSEST_PRIOR_ART'],
    icon: Scale,
  },
  {
    id: 'obviousnessPrecheck',
    step: 8,
    name: '显而易见性预分析',
    question: '区别特征是否属于惯用手段，D1 是否已经给出改进启示和修改动机？',
    input: '输入：D1 全文、冻结区别特征及其结构作用',
    output: '输出：技术问题、D1 启示、惯用手段、修改动机、反向教导、效果及检索路由',
    technicalCodes: ['I4_O_OBVIOUSNESS_PRECHECK'],
    icon: Lightbulb,
  },
  {
    id: 'gapSearch',
    step: 9,
    name: '进一步检索与补证循环',
    question: '预分析后仍缺少什么证据，下一轮检索和逐篇比对得到了什么？',
    input: '输入：显而易见性预分析路由、既有合格文献和上一轮失败原因',
    output: '输出：区别特征检索式、真实命中、全文与日期核验、逐篇比对结果（最多 5 轮）',
    technicalCodes: ['I2_GAP_QUERY_PLAN'],
    icon: RefreshCw,
  },
  {
    id: 'inventiveStep',
    step: 10,
    name: '创造性组合分析',
    question: 'D1 与其他资料能否组合，组合启示和技术效果证据是否完整？',
    input: '输入：D1、区别特征覆盖文献及其原文证据',
    output: '输出：组合覆盖、组合动机、反向教导、技术效果和证据缺口',
    technicalCodes: ['I4_I_INVENTIVE_STEP'],
    icon: Scale,
  },
  {
    id: 'report',
    step: 11,
    name: '三步法文字分析、Top 10 总表与全部比对',
    question: '如何把创造性结论、最相似文献总览和全部逐篇证据汇总成律师可复核材料？',
    input: '输入：前十个阶段的持久化结果',
    output: '输出：模块10文字分析、相似度前10横向表和全部对比文件逐篇比对',
    technicalCodes: ['I5_REPORT'],
    icon: FileText,
  },
];

const MODULE9_GAP_STRATEGIES = [
  '第1轮：相邻产品类别 + 直接结构特征',
  '第2轮：优先上位、必要时选择合适下位产品类别 + 结构族同义词',
  '第3轮：同功能产品类别 + 动作/功能/技术角色',
  '第4轮：子系统/部件类别 + 构件关系/介质路径',
  '第5轮：类比领域客体 + 工作原理/技术效果',
] as const;

const GAP_REUSED_TECHNICAL_CODES = new Set([
  'I3_PATENT_SEARCH',
  'I3_NPL_SEARCH',
  'I3_CANDIDATE_FILTER',
  'I3_FETCH',
  'I3_QUALIFY',
  'I4_S_SINGLE_REFERENCE',
]);

function attachedRunBelongsToBusinessModule(
  businessModule: BusinessModule,
  run: JsonObject,
): boolean {
  const code = text(run.module_code);
  const isGapReusedCode = GAP_REUSED_TECHNICAL_CODES.has(code);
  if (
    !businessModule.technicalCodes.includes(code)
    && !(businessModule.id === 'gapSearch' && isGapReusedCode)
  ) return false;
  if (!isGapReusedCode) return true;
  const iteration = numberValue(run.iteration_no);
  const isGapRound = iteration !== null && iteration > 1;
  if (businessModule.id === 'gapSearch') return isGapRound;
  if (businessModule.id === 'evidence' || businessModule.id === 'singleReference') {
    // Legacy/manual lab runs may have no iteration.  They remain attached to
    // their original first-search business stage; only persisted I0 gap rounds
    // move to module 9.
    return !isGapRound;
  }
  return true;
}

const TERMINAL_INVESTIGATION = new Set([
  'completed',
  'partial',
  'failed',
  'cancelled',
  'needs_human_review',
]);
const TERMINAL_CLAIM = new Set([
  'novelty_evidence_complete',
  'inventive_step_evidence_complete',
  'search_budget_exhausted',
  'exhausted',
  'partial',
  'failed',
  'cancelled',
  'needs_human_review',
]);
const TERMINAL_RUN = new Set([
  'succeeded',
  'completed',
  'partial',
  'failed',
  'cancelled',
]);
const DOCUMENT_WORK_CONCURRENCY = 2;
const MODULE5_BATCH_TERMINAL = new Set(['completed', 'partial', 'failed']);
const MODULE9_BATCH_POLL_STOP = new Set([
  'awaiting_next_round',
  'round_partial',
  'completed',
  'exhausted',
  'partial',
  'failed',
]);

const STATUS_LABELS: Record<string, string> = {
  created: '准备中',
  queued: '排队中',
  running: '运行中',
  critical_date_review: '核对关键日',
  initial_search: '首轮检索',
  single_reference_review: '单篇对比',
  closest_prior_art_selected: '已选最接近现有技术',
  gap_search: '补充检索',
  combination_review: '组合分析',
  novelty_evidence_complete: '已形成新颖性证据',
  inventive_step_evidence_complete: '已形成创造性证据',
  search_budget_exhausted: '本轮检索已结束',
  exhausted: '本轮检索已结束',
  awaiting_next_round: '本轮完成，等待继续',
  round_partial: '本轮部分完成',
  completed: '完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
  needs_human_review: '需要人工确认',
  succeeded: '完成',
};

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

const FIXED_FIRST_ROUND_QUERY_VARIANTS = new Set([
  'applicant_plus_object',
  'title_object_plus_desc_inventive',
  'classification_plus_desc_inventive',
  'desc_object_plus_desc_inventive_plus_effect',
  'title_keyword_object_plus_desc_function',
]);

function queryVariantLabel(value: unknown, role: unknown): string {
  const labels: Record<string, string> = {
    applicant_plus_object: '申请人 + 标题/摘要客体（排除目标专利）',
    title_object_plus_desc_inventive: '标题客体 + 说明书核心发明点',
    classification_plus_desc_inventive: '客体分类号 + 说明书核心发明点',
    desc_object_plus_desc_inventive_plus_effect: '说明书客体 + 核心发明点 + 技术效果',
    title_keyword_object_plus_desc_function: '标题/摘要客体 + 说明书功能效果',
    maximal_similarity_precision: '最大相似长线（允许零结果）',
    object_plus_inventive_point: '客体 + 发明点',
    subject_classification_plus_inventive_point: '客体分类号 + 发明点',
    object_plus_inventive_classification: '客体 + 发明点分类号',
    object_plus_two_inventive_points: '客体 + 两个发明点',
    object_plus_component_plus_effect: '客体 + 通用构件 + 功能效果',
    system_architecture_recall: '系统结构召回线',
    title_abstract_concept: '名称摘要概念线',
    target_citation_lookup: '目标专利引用文献线',
    target_effect_recall: '技术效果召回线',
    classification_action_recall: '分类号 + 动作效果线',
    gap_followup: '后续缺口线',
  };
  return labels[text(value)]
    || (text(role) === 'title_abstract_concept' ? '名称摘要概念线' : '后续缺口线');
}

function record(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
}

function rows(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? (value.filter(
        (item) => item && typeof item === 'object' && !Array.isArray(item),
      ) as JsonObject[])
    : [];
}

function stringValues(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => text(item)).filter(Boolean)
    : [];
}

function gapQueryFeatureIds(query: JsonObject): string[] {
  const gapIds = stringValues(query.gap_feature_ids);
  return gapIds.length ? gapIds : stringValues(query.feature_ids);
}

function isPrimaryGapQuery(query: JsonObject): boolean {
  return text(query.provider_kind) === 'patent'
    && text(query.date_channel || 'ordinary_prior_art') === 'ordinary_prior_art'
    && text(query.search_objective || 'gap_or_combination') === 'gap_or_combination';
}

function gapBooleanGroups(expression: string): string[][] {
  return expression
    .split(/\s+AND\s+/i)
    .map((group) => group.trim().replace(/^\(|\)$/g, '').trim())
    .filter(Boolean)
    .map((group) => group
      .split(/\s+OR\s+/i)
      .map((item) => item.trim().replace(/[()]/g, ''))
      .filter(Boolean));
}

function isBilingualGroup(values: string[]): boolean {
  return values.some((value) => /[\u3400-\u9fff]/.test(value))
    && values.some((value) => /[A-Za-z]/.test(value));
}

function dig(value: unknown, ...path: string[]): unknown {
  let current = value;
  for (const key of path) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) {
      return undefined;
    }
    current = (current as JsonObject)[key];
  }
  return current;
}

function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function statusLabel(value: unknown): string {
  const status = text(value);
  return STATUS_LABELS[status] || status || '未知';
}

function friendlyError(raw: string): string {
  if (raw.includes('测试资源不存在')) {
    return '这个案件不在当前账号的测试记录中。请从本页重新上传，或使用本账号创建过的链接。';
  }
  if (raw.includes('PATSNAP_PERMISSION_DENIED') || raw.includes('67200004')) {
    return '智慧芽账号当前没有该接口权限或套餐额度已用尽；系统已停止重试，不会继续消耗请求次数。';
  }
  if (raw.includes('queries 不是数组')) {
    return '旧版检索方案返回格式不一致。新版已自动识别常见包装结构，并保留原始响应供审计。';
  }
  if (raw.includes('LAB_I2_PROFILE_REQUIRED')) {
    return '需要先在模块3总结核心发明点，模块4才能生成检索关键词。';
  }
  if (raw.includes('LAB_REPORT_SNAPSHOT_REQUIRED')) {
    return '当前案件还没有固化的最终报告；页面会先展示已持久化的报告预览。';
  }
  if (raw.includes('LAB_PERSISTED_DOCUMENT_REQUIRED')) {
    return '当前案件还没有可供本模块复用的现有技术文献。';
  }
  if (
    raw.includes('LAB_PERSISTED_LIMITATIONS_REQUIRED')
    || raw.includes('持久化 claim checkpoint 缺少技术特征')
  ) {
    return '本次分析没有读到模块2生成的技术特征。新版会自动承接同一独立权利要求最近一次成功的模块2结果。';
  }
  if (raw.includes('LAB_NPL_RETRIEVAL_UNAVAILABLE')) {
    return '该候选目前只有题录线索，没有可核验全文，因此不能进入逐项披露分析。';
  }
  if (raw.includes('LAB_PERSISTED_D1_REQUIRED')) {
    return '当前案件尚未选出最接近现有技术，因此创造性组合分析暂时没有输入。';
  }
  if (raw.includes('LAB_DEPENDENCY_FAILED')) {
    return '外部检索或模型服务暂时不可用。';
  }
  if (raw.includes('账号尚未获准')) {
    return '当前账号还没有测试权限，请联系管理员审批。';
  }
  return raw || '运行失败';
}

function attachedDerivedFacts(
  moduleId: string,
  report: JsonObject | null,
  taskPayload: JsonObject,
): string[] {
  // I0 自动流程中模块1/2/3 不是独立 module run（画像嵌在 I2 检索方案运行中），
  // 这里从报告预览的冻结事实回填律师可见的真实输出，缺失时如实标注异常。
  if (moduleId === 'target') {
    const patent = record(report?.target_patent);
    if (!Object.keys(patent).length) return [];
    const claimsCount = rows(patent.claims).length;
    const figuresCount = rows(patent.figures).length;
    const facts = [
      `专利号：${text(patent.patent_number) || '未识别'}`,
      `名称：${text(patent.title) || '未识别'}`,
      `申请日：${text(patent.application_date) || '未识别'}`,
      `优先权日：${text(patent.priority_date) || '无（本案以申请日为关键日）'}`,
      `公开/公告日：${text(patent.publication_date) || '未识别'}`,
      `已冻结权利要求 ${claimsCount} 项、附图 ${figuresCount} 张`,
    ];
    if (!text(patent.application_date)) facts.push('异常：未识别到申请日，后续日期核验无法自动进行');
    if (!figuresCount) facts.push('异常：未提取到任何附图');
    if (!claimsCount) facts.push('异常：未识别到权利要求');
    return facts;
  }
  if (moduleId === 'date') {
    const fromReport = rows(report?.claim_investigations);
    const claims = (fromReport.length ? fromReport : rows(taskPayload.claim_investigations))
      .filter((claim) => claim.in_scope !== false);
    if (!claims.length) return [];
    return claims.map((claim) => {
      const claimLabel = `权利要求 ${text(claim.claim_id) || '?'}`;
      const criticalDate = text(claim.critical_date);
      if (!criticalDate) {
        return `${claimLabel}：等待开始（进入该权利要求流程时才确定关键日）`;
      }
      const basis = text(claim.critical_date_basis);
      const basisLabel = criticalDateBasisLabel(basis) || '未标注';
      return `${claimLabel}：关键日 ${criticalDate}（依据：${basisLabel}）`;
    });
  }
  if (moduleId === 'profile') {
    const limitations = rows(report?.claim_limitations);
    if (!limitations.length) return [];
    const byClaim = new Map<string, JsonObject[]>();
    for (const limitation of limitations) {
      const key = text(limitation.origin_claim_id) || '?';
      byClaim.set(key, [...(byClaim.get(key) || []), limitation]);
    }
    const facts = Array.from(byClaim.entries()).map(([claimId, items]) => (
      `权利要求 ${claimId}：已拆解 ${items.length} 项技术特征，例如「${text(items[0]?.limitation_text).slice(0, 40)}」`
    ));
    facts.push('说明：自动流程的核心发明点画像在模块4检索方案运行中生成，可展开模块4查看检索式');
    return facts;
  }
  if (moduleId === 'query') {
    const queries = rows(report?.queries);
    if (!queries.length) return [];
    const expressions = queries
      .map((query) => text(query.expression))
      .filter(Boolean);
    const facts = [`已冻结检索式 ${queries.length} 条`];
    for (const expression of expressions.slice(0, 5)) {
      facts.push(expression.length > 90 ? `${expression.slice(0, 90)}…` : expression);
    }
    return facts;
  }
  return [];
}

function attachedBusinessStates(
  report: JsonObject | null,
  taskPayload: JsonObject,
): Record<string, BusinessState> {
  const reportRuns = rows(report?.module_runs);
  const detailRuns = rows(taskPayload.module_run_details);
  const detailsById = new Map(
    detailRuns.map((run) => [text(run.id), run] as const),
  );
  // 只读诊断以调查级 run 快照为准填充输入/输出；报告行只作兜底清单。
  const moduleRuns = detailRuns.length ? detailRuns : reportRuns;
  const jobs = rows(report?.jobs);
  const jobsByRunId = new Map(
    jobs.map((job) => [text(job.module_run_id), job]),
  );
  const states: Record<string, BusinessState> = {};
  for (const businessModule of BUSINESS_MODULES) {
    const matching = moduleRuns
      .filter((run) => attachedRunBelongsToBusinessModule(businessModule, run))
      .sort((left, right) => text(left.created_at).localeCompare(text(right.created_at)));
    if (!matching.length) continue;
    const children: ChildRun[] = matching.map((run) => {
      const detail = record(detailsById.get(text(run.id)));
      const merged = Object.keys(detail).length ? { ...run, ...detail } : run;
      const status = text(merged.status);
      const error = text(merged.error_message) || text(merged.error_code);
      return {
        code: text(merged.module_code),
        ok: ['completed', 'succeeded'].includes(status),
        complete: TERMINAL_RUN.has(status),
        status,
        moduleRunId: text(merged.id) || undefined,
        roundIteration: numberValue(merged.iteration_no) ?? undefined,
        error: error || undefined,
        output: record(merged.output_snapshot),
        raw: {
          module_run: merged,
          job: jobsByRunId.get(text(merged.id)) || {},
        },
      };
    });
    const active = children.some((child) => ['created', 'queued', 'pending', 'running'].includes(child.status));
    const failed = children.filter((child) => ['failed', 'error', 'partial'].includes(child.status) || child.error);
    const latest = children[children.length - 1];
    states[businessModule.id] = {
      phase: active ? 'running' : 'done',
      mode: 'current',
      ok: active ? undefined : failed.length === 0,
      headline: active
        ? `同一任务已有 ${children.length} 条运行记录，最新状态为${statusLabel(latest.status)}`
        : failed.length
          ? `同一任务有 ${failed.length} 条运行记录需要排查`
          : `同一任务已有 ${children.length} 条持久化运行记录`,
      facts: [
        `技术节点：${Array.from(new Set(children.map((child) => child.code))).join('、')}`,
        ...attachedDerivedFacts(businessModule.id, report, taskPayload),
        ...(failed.slice(-3).map((child) => `${child.code}：${friendlyError(child.error || child.status)}`)),
      ],
      children,
      reportSnapshot: report,
    };
  }
  const taskProgress = invalidityTaskProgress(taskPayload);
  BUSINESS_MODULES.forEach((businessModule, index) => {
    if (states[businessModule.id]) return;
    const step = taskProgress?.steps[index];
    if (!step || step.state === 'pending') return;
    const failed = ['error', 'partial', 'cancelled'].includes(step.state);
    const derived = attachedDerivedFacts(businessModule.id, report, taskPayload);
    const hasAnomaly = derived.some((fact) => fact.startsWith('异常'));
    states[businessModule.id] = {
      phase: ['running', 'waiting'].includes(step.state) ? 'running' : 'done',
      mode: 'current',
      ok: failed || hasAnomaly ? false : step.state === 'completed',
      headline: step.state === 'completed'
        ? '同一 Agent 任务已经越过该阶段'
        : step.description,
      facts: [...derived, step.description],
      children: [],
      reportSnapshot: report,
    };
  });
  return states;
}

function providerErrorMessages(child: ChildRun): string[] {
  const errors = stringValues(child.output.provider_errors).length
    ? stringValues(child.output.provider_errors)
    : stringValues(child.output.errors);
  if (
    child.code === 'I3_NPL_SEARCH'
    && errors.length > 0
    && errors.every((item) => item.includes('QueryValidationError'))
  ) {
    return [
      '这是一条旧版论文检索记录：共同检索式没有同时包含保护客体和技术/机构特征，'
      + '所以系统在联网前拒绝了请求，并非四个论文来源同时故障。请用新版检索方案重跑本模块。',
    ];
  }
  return errors.map(friendlyError);
}

async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;
  const runners = Array.from(
    { length: Math.min(Math.max(1, concurrency), items.length) },
    async () => {
      while (cursor < items.length) {
        const index = cursor;
        cursor += 1;
        results[index] = await worker(items[index], index);
      }
    },
  );
  await Promise.all(runners);
  return results;
}

async function jsonRequest(path: string, init?: RequestInit): Promise<JsonObject> {
  const response = await fetch(
    `/api/test/invalidity/${path.replace(/^\//, '')}`,
    {
      ...init,
      headers: init?.body
        ? { 'Content-Type': 'application/json', ...(init.headers || {}) }
        : init?.headers,
      cache: 'no-store',
    },
  );
  const data = (await response.json()) as JsonObject;
  if (!response.ok) {
    throw new Error(
      String(data.error || data.detail || data.message || `HTTP ${response.status}`),
    );
  }
  return data;
}

function module5BatchChildren(value: unknown): ChildRun[] {
  return rows(value).map((item) => ({
    code: text(item.code),
    ok: item.ok === true,
    status: text(item.status),
    output: record(item.output),
    moduleRunId: text(item.moduleRunId) || undefined,
    documentId: text(item.documentId) || undefined,
    roundIteration: numberValue(item.roundIteration) ?? undefined,
    matrixPlan: item.matrixPlan === true,
    reusedFromI4sRunId: text(item.reusedFromI4sRunId) || undefined,
    isCurrentAttempt: item.isCurrentAttempt !== false,
    attemptNumber: numberValue(item.attemptNumber) ?? undefined,
    pipelineAttempts: numberValue(item.pipelineAttempts) ?? undefined,
    error: text(item.error) || undefined,
    attempts: numberValue(item.attempts) ?? undefined,
    queueSeconds: numberValue(item.queueSeconds) ?? undefined,
    executionSeconds: numberValue(item.executionSeconds) ?? undefined,
    raw: record(item.raw),
  }));
}

function isNetworkInterruption(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || '');
  return [
    'failed to fetch',
    'fetch failed',
    'networkerror',
    'load failed',
    'aborted',
    '服务不可达',
    'service unavailable',
  ].some((marker) => message.toLowerCase().includes(marker));
}

async function module5BatchRequest(
  init: RequestInit | undefined,
  query = '',
): Promise<JsonObject> {
  const response = await fetch(
    `/api/test/invalidity/module5-batches${query}`,
    {
      ...init,
      headers: init?.body
        ? { 'Content-Type': 'application/json', ...(init.headers || {}) }
        : init?.headers,
      cache: 'no-store',
    },
  );
  const data = (await response.json()) as JsonObject;
  if (!response.ok) {
    throw new Error(
      String(data.error || data.detail || data.message || `HTTP ${response.status}`),
    );
  }
  return data;
}

async function module9BatchRequest(
  init: RequestInit | undefined,
  query = '',
): Promise<JsonObject> {
  const response = await fetch(
    `/api/test/invalidity/module9-batches${query}`,
    {
      ...init,
      headers: init?.body
        ? { 'Content-Type': 'application/json', ...(init.headers || {}) }
        : init?.headers,
      cache: 'no-store',
    },
  );
  const data = (await response.json()) as JsonObject;
  if (!response.ok) {
    throw new Error(
      String(data.error || data.detail || data.message || `HTTP ${response.status}`),
    );
  }
  return data;
}

function claimSort(left: JsonObject, right: JsonObject): number {
  const a = Number.parseInt(text(left.claim_id), 10);
  const b = Number.parseInt(text(right.claim_id), 10);
  return (Number.isFinite(a) ? a : Number.MAX_SAFE_INTEGER)
    - (Number.isFinite(b) ? b : Number.MAX_SAFE_INTEGER);
}

function patentClaimsFrom(
  investigation: JsonObject | null,
  report: JsonObject | null,
): JsonObject[] {
  const reportClaims = rows(dig(report, 'target_patent', 'claims'));
  if (reportClaims.length) return reportClaims;
  return rows(dig(investigation, 'source_snapshot', 'patent_snapshot', 'claims'));
}

function isIndependentPatentClaim(claim: JsonObject): boolean {
  return claim.in_scope === true || text(claim.claim_type) === 'INDEPENDENT';
}

function claimTypeMap(
  investigation: JsonObject | null,
  report: JsonObject | null,
): Map<string, string> {
  return new Map(
    patentClaimsFrom(investigation, report).map((claim) => [
      text(claim.claim_id),
      text(claim.claim_type),
    ]),
  );
}

function scopedClaimRows(
  claims: JsonObject[],
  investigation: JsonObject | null,
  report: JsonObject | null,
): JsonObject[] {
  const types = claimTypeMap(investigation, report);
  return claims
    .filter((claim) => {
      if (claim.in_scope === true) return true;
      if (claim.in_scope === false) return false;
      return text(claim.claim_type) === 'INDEPENDENT'
        || types.get(text(claim.claim_id)) === 'INDEPENDENT';
    })
    .sort(claimSort);
}

function excludedClaimRows(
  claims: JsonObject[],
  investigation: JsonObject | null,
  report: JsonObject | null,
): JsonObject[] {
  const included = new Set(
    scopedClaimRows(claims, investigation, report).map((claim) => text(claim.id)),
  );
  return claims.filter((claim) => !included.has(text(claim.id)));
}

function outputFromRun(run: JsonObject): JsonObject {
  const snapshot = record(run.output_snapshot);
  return Object.keys(record(snapshot.output)).length
    ? record(snapshot.output)
    : snapshot;
}

function providerFromRun(run: JsonObject): string {
  return text(dig(run, 'output_snapshot', 'actual_provider'))
    || text(run.actual_provider);
}

function reportClaimsInScope(
  report: JsonObject | null,
  investigation: JsonObject | null,
): JsonObject[] {
  const types = claimTypeMap(investigation, report);
  return rows(report?.claim_investigations)
    .filter((claim) => {
      if (claim.in_scope === true) return true;
      if (claim.in_scope === false) return false;
      return text(claim.claim_type) === 'INDEPENDENT'
        || types.get(text(claim.claim_id)) === 'INDEPENDENT';
    })
    .sort(claimSort);
}

function summaryForBusiness(
  moduleId: BusinessModuleId,
  children: ChildRun[],
  report: JsonObject | null,
  mode: RunMode,
): Pick<BusinessState, 'ok' | 'headline' | 'facts'> {
  const successful = children.filter((child) => child.ok);
  const failed = children.filter((child) => !child.ok);
  const facts: string[] = [];
  const output = successful[0]?.output || {};

  if (moduleId === 'target') {
    const independent = numberValue(output.independent_claim_count);
    const dependent = numberValue(output.dependent_claim_count);
    facts.push(
      independent !== null
        ? `识别到 ${independent} 项独立权利要求；当前自动流程只处理这些权利要求。`
        : '已读取目标专利事实快照。',
    );
    if (dependent !== null) {
      facts.push(`另有 ${dependent} 项从属权利要求，仅保留原文，不进入当前自动检索。`);
    }
    const patentNumber = text(output.patent_number);
    const patentTitle = text(output.title);
    if (patentNumber || patentTitle) {
      facts.unshift(
        patentNumber && patentTitle
          ? `${patentNumber}《${patentTitle}》`
          : patentNumber || `《${patentTitle}》`,
      );
    }
  }

  if (moduleId === 'date') {
    const critical = record(output.critical_date);
    const date = text(critical.critical_date);
    facts.push(
      date
        ? `本案关键日：${date}；依据：${text(critical.basis) || '系统日期规则'}。`
        : '没有得到可直接采用的关键日，后续检索不会猜测日期。',
    );
  }

  if (moduleId === 'profile') {
    const inventiveCount = successful.reduce(
      (total, child) =>
        total
        + rows(record(child.output.invention_search_profile).inventive_point_features)
          .length,
      0,
    );
    if (successful.length > 0) {
      facts.push(`总结核心发明点，识别 ${inventiveCount} 个发明点概念及对应技术特征。`);
    } else if (failed.length > 0) {
      facts.push('本次没有形成核心发明点总结；具体原因见下方。');
    }
    const subjects = successful
      .map(
        (child) =>
          text(record(child.output.invention_search_profile).protected_subject)
          || text(child.output.technical_subject),
      )
      .filter(Boolean);
    if (subjects.length) facts.push(`保护客体/类别：${[...new Set(subjects)].join('；')}`);
  }

  if (moduleId === 'query') {
    const queryCount = successful.reduce(
      (total, child) => total + rows(child.output.queries).length,
      0,
    );
    const inventiveCount = successful.reduce(
      (total, child) =>
        total
        + rows(record(child.output.invention_search_profile).inventive_point_features)
          .length,
      0,
    );
    const scopeLabels = new Set(
      successful.flatMap((child) =>
        rows(child.output.queries).map((query) => {
          const scope = text(query.search_scope);
          if (scope === 'claims') return '权利要求';
          if (scope === 'title_abstract') return '标题和摘要';
          return '专利全文';
        }),
      ),
    );
    if (successful.length > 0) {
      facts.push(
        `识别 ${inventiveCount} 个发明点概念，形成 ${queryCount} 条分线检索式。`,
      );
    } else if (failed.length > 0) {
      facts.push('本次没有形成可执行检索式；具体规则校验原因见下方。');
    }
    if (scopeLabels.size) {
      facts.push(`检索范围：${[...scopeLabels].join('、')}。`);
    }
    const subjects = successful
      .map(
        (child) =>
          text(record(child.output.invention_search_profile).protected_subject)
          || text(child.output.technical_subject),
      )
      .filter(Boolean);
    if (subjects.length) facts.push(`检索主题：${[...new Set(subjects)].join('；')}`);
  }

  if (moduleId === 'evidence') {
    for (const child of successful) {
      if (child.code === 'I3_PATENT_SEARCH') {
        facts.push(`专利库返回 ${numberValue(child.output.count) ?? rows(child.output.documents).length} 条候选。`);
      }
      if (child.code === 'I3_NPL_SEARCH') {
        facts.push(`论文和技术资料库返回 ${numberValue(child.output.count) ?? rows(child.output.documents).length} 条候选。`);
      }
      if (child.code === 'I3_CANDIDATE_FILTER') {
        facts.push(
          `取文前共核对 ${numberValue(child.output.raw_candidate_count) ?? 0} 条原始候选：`
          + `同申请/同公开版本合并 ${numberValue(child.output.duplicate_count) ?? 0} 条，`
          + `明显跨领域排除 ${numberValue(child.output.excluded_count) ?? 0} 条，`
          + `实际进入取全文 ${numberValue(child.output.fetch_candidate_count) ?? 0} 篇。`,
        );
        if (child.output.model_failure) {
          facts.push('语义筛选本次不可用，系统已按高召回原则保留未确定候选，没有静默丢弃。');
        }
      }
      if (child.code === 'I3_FETCH') {
        const provenance = record(child.output.provenance);
        const reusedPatentText = provenance.retrieval_cache_hit === true;
        if (isRetrievedEvidence(child.output)) {
          const discoveryProvider = text(provenance.discovery_provider);
          const retrievalProvider = text(provenance.retrieval_provider)
            || text(child.output.provider);
          facts.push(
            isAnalysisReady(child.output)
              ? reusedPatentText
                ? '命中同一完整公开号的历史全文缓存：已校验原文件哈希并复用可读化/OCR结果，本次未重复下载。'
                : discoveryProvider && retrievalProvider
                ? `已取得并读完一份候选全文（发现源 ${discoveryProvider}，取文源 ${retrievalProvider}）。`
                : '已取得并读完一份候选资料的完整内容。'
              : `文件已取得，但尚不能进入实体比对：${text(child.output.analysis_readiness_reason) || '全文结构化/OCR未完成'}。`,
          );
        } else {
          facts.push('已尝试取文，但该候选仍只有检索线索，未取得可核验全文。');
        }
      }
      if (child.code === 'I3_QUALIFY') facts.push('已完成一份全文资料的日期分类。');
    }
    const searchRuns = successful.filter((child) =>
      ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(child.code),
    );
    const totalCandidates = searchRuns.reduce(
      (total, child) => total + rows(child.output.documents).length,
      0,
    );
    const fetchedCount = successful.filter(
      (child) =>
        child.code === 'I3_FETCH'
        && isRetrievedEvidence(child.output)
        && isAnalysisReady(child.output),
    ).length;
    const acquiredCount = successful.filter(
      (child) => child.code === 'I3_FETCH' && isRetrievedEvidence(child.output),
    ).length;
    const nplFullTextRoutes = successful
      .filter((child) => child.code === 'I3_NPL_SEARCH')
      .reduce(
        (total, child) =>
          total
          + rows(child.output.documents).filter(hasDirectFullTextRoute).length,
        0,
      );
    if (nplFullTextRoutes) {
      facts.push(
        `论文候选中 ${nplFullTextRoutes} 条带有可尝试取得的 PDF 全文地址，系统已优先取文。`,
      );
    }
    facts.push(
      totalCandidates
        ? `候选共 ${totalCandidates} 条，其中 ${acquiredCount} 条取得原文件，${fetchedCount} 条完成全文结构化/OCR并可比对。`
        : '本次精确检索没有候选；专利库为 0 时已单独执行一次收敛查询。',
    );
  }

  if (moduleId === 'singleReference') {
    const requestedSingleRuns = children.filter(
      (child) => child.code === 'I4_S_SINGLE_REFERENCE',
    );
    const singleRuns = requestedSingleRuns.filter((child) => child.ok);
    if (requestedSingleRuns.length) {
      facts.push(
        `本轮共有 ${requestedSingleRuns.length} 份全文需要逐篇比对，已完成 ${singleRuns.length} 份`
        + `${singleRuns.length === requestedSingleRuns.length ? '。' : `，另有 ${requestedSingleRuns.length - singleRuns.length} 份失败。`}`,
      );
      facts.push('每份文献的技术特征判断分别列示在下方，不以其中一份代替其他文献。');
    }
    if (!requestedSingleRuns.length) {
      facts.push('没有取得可核验全文，因此没有可运行的逐篇披露分析。');
    } else if (!singleRuns.length) {
      facts.push('本轮逐篇分析均失败，不能进入 D1 选择。');
    }
  }

  if (moduleId === 'closestPriorArt') {
    const closest = successful.find((child) => child.code === 'I4_C_CLOSEST_PRIOR_ART');
    if (closest) {
      const differences = rows(closest.output.distinguishing_features);
      facts.push(`最接近现有技术：${text(closest.output.document_id) || '已完成选择'}。`);
      facts.push(`冻结 ${differences.length} 项区别特征，供后续区别特征检索使用。`);
    } else {
      facts.push('尚未选出可用 D1；请先完成日期合格文献的单篇比对。');
    }
  }

  if (moduleId === 'obviousnessPrecheck') {
    const precheck = successful.find(
      (child) => child.code === 'I4_O_OBVIOUSNESS_PRECHECK',
    );
    if (precheck) {
      const groups = rows(precheck.output.feature_groups);
      const directSearchGroups = groups.filter(
        (group) => text(group.search_route) === 'search_direct_feature_evidence',
      );
      const commonKnowledgeGroups = groups.filter(
        (group) => text(group.search_route) === 'search_common_knowledge_evidence',
      );
      facts.push(`已对 ${groups.length} 组相互配合的区别特征完成显而易见性预分析。`);
      facts.push(
        `普通结构检索 ${directSearchGroups.length} 组；仅需补公知常识证据 ${commonKnowledgeGroups.length} 组；其余按组合补证、无需结构检索或人工复核分流。`,
      );
    } else {
      facts.push('尚未形成 D1 技术启示、惯用手段和修改路径的逐组预分析。');
    }
  }

  if (moduleId === 'gapSearch') {
    const gapPlan = successful.find((child) => child.code === 'I2_GAP_QUERY_PLAN');
    if (gapPlan) {
      const decision = record(gapPlan.output.gap_reuse_decision);
      const iteration = numberValue(decision.gap_search_iteration)
        ?? numberValue(gapPlan.output.gap_search_iteration)
        ?? 1;
      const strategyLabel = text(gapPlan.output.gap_search_strategy_label);
      const uncovered = stringValues(
        decision.uncovered_difference_feature_ids
        || gapPlan.output.uncovered_difference_feature_ids,
      );
      const humanReview = stringValues(gapPlan.output.human_review_feature_ids);
      const unresolved = uniqueText([...uncovered, ...humanReview]);
      const covered = stringValues(
        decision.covered_difference_feature_ids
        || gapPlan.output.covered_difference_feature_ids,
      );
      const queries = rows(gapPlan.output.queries);
      const searches = successful.filter((child) =>
        ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(child.code),
      );
      const hitCount = searches.reduce(
        (total, child) => total + rows(child.output.documents).length,
        0,
      );
      const fetched = successful.filter(
        (child) => child.code === 'I3_FETCH' && isAnalysisReady(child.output),
      );
      const qualified = successful.filter(
        (child) => child.code === 'I3_QUALIFY',
      );
      const comparisons = successful.filter(
        (child) => child.code === 'I4_S_SINGLE_REFERENCE',
      );
      facts.push(`第 ${iteration} 个 gap 轮：既有资料已覆盖 ${covered.length} 项，仍有 ${unresolved.length} 项区别特征待解决。`);
      if (strategyLabel) {
        facts.push(`本轮固定检索策略：${strategyLabel}。`);
      }
      facts.push(
        queries.length
          ? `本轮形成 ${queries.length} 条只针对未覆盖区别特征的检索式。`
          : humanReview.length
            ? `${humanReview.length} 项未解决区别特征已转律师人工复核；本轮不调用检索 provider，但不视为已有证据覆盖。`
            : unresolved.length
              ? `${unresolved.length} 项区别特征仍未解决，但本轮没有形成可执行查询；不能视为已有证据覆盖。`
              : '既有合格资料已覆盖全部区别特征，本轮不调用检索 provider。',
      );
      if (queries.length) {
        facts.push(
          `已执行 ${searches.length}/${queries.length} 条真实检索，取得 ${hitCount} 条原始命中；`
          + `${fetched.length} 份完成全文可读化，${qualified.length} 份完成日期核验，`
          + `${comparisons.length} 份完成 I4-S 逐篇比对。`,
        );
      }
    } else {
      facts.push('没有形成区别特征复用决定或 gap 检索计划。');
    }
  }

  if (moduleId === 'inventiveStep') {
    const inventive = successful.find((child) => child.code === 'I4_I_INVENTIVE_STEP');
    if (inventive) {
      facts.push(
        inventive.output.evidence_complete === true
          ? '创造性组合的证据链完整，可以交由律师复核。'
          : '创造性组合仍有证据缺口，不能据此直接作出创造性结论。',
      );
      facts.push(`当前组合仍记录 ${rows(inventive.output.gaps).length} 项证据缺口。`);
    } else {
      facts.push('没有形成创造性组合分析。');
    }
  }

  if (moduleId === 'report') {
    const reportValue = record(output.report_data);
    const activeReport = Object.keys(reportValue).length ? reportValue : report;
    const scopedClaims = reportClaimsInScope(activeReport, null);
    const scopedIds = new Set(scopedClaims.map((claim) => text(claim.id)));
    const scopedDisclosures = rows(activeReport?.feature_disclosures).filter(
      (item) => scopedIds.has(text(item.claim_investigation_id)),
    );
    const scopedDocumentIds = new Set(
      scopedDisclosures.map((item) => text(item.document_id)).filter(Boolean),
    );
    const appendix = record(output.lab_analysis_appendix);
    facts.push(`报告仅展示 ${scopedClaims.length || (text(appendix.claim_id) ? 1 : 0)} 项独立权利要求。`);
    facts.push(
      rows(appendix.limitations).length
        ? `本次实验附录包含 ${rows(appendix.limitations).length} 个技术特征、${Object.keys(record(appendix.comparisons)).length} 份逐文献对比。`
        : `报告汇总 ${scopedDocumentIds.size} 份与独立权利要求有关的资料、${scopedDisclosures.length} 条逐项披露。`,
    );
  }

  failed.forEach((child) => {
    facts.push(`${child.code}：${friendlyError(child.error || statusLabel(child.status))}`);
  });
  const analysisRuns = children.filter(
    (child) => child.code === 'I4_S_SINGLE_REFERENCE',
  );
  const evidenceSearches = children.filter((child) =>
    ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(child.code),
  );
  const evidenceFilter = children.find(
    (child) => child.code === 'I3_CANDIDATE_FILTER',
  );
  const expectedEvidenceKeys = new Set(
    (mode === 'current' && evidenceFilter?.ok
      ? rows(evidenceFilter.output.fetch_candidates).map(
          (candidate) => record(candidate.document),
        )
      : evidenceSearches
        .filter((child) => child.ok)
        .flatMap((child) => rows(child.output.documents).slice(0, 10)))
      .map(evidenceDocumentKey)
      .filter(Boolean),
  );
  const evidenceFetches = children.filter((child) => child.code === 'I3_FETCH');
  const attemptedEvidenceKeys = new Set(
    evidenceFetches.map(childDocumentKey).filter(Boolean),
  );
  const evidenceAllAttempted = [...expectedEvidenceKeys].every(
    (key) => attemptedEvidenceKeys.has(key),
  );
  const evidenceAllRetrieved = evidenceFetches.every(
    (child) =>
      child.ok
      && isRetrievedEvidence(child.output)
      && isAnalysisReady(child.output),
  );
  const ok =
    moduleId === 'singleReference'
      ? analysisRuns.length > 0
        && analysisRuns.every((child) => child.ok)
      : moduleId === 'closestPriorArt'
        ? successful.some((child) => child.code === 'I4_C_CLOSEST_PRIOR_ART')
      : moduleId === 'obviousnessPrecheck'
        ? successful.some((child) => child.code === 'I4_O_OBVIOUSNESS_PRECHECK')
      : moduleId === 'gapSearch'
        ? (() => {
            const plan = successful.find(
              (child) => child.code === 'I2_GAP_QUERY_PLAN',
            );
            if (!plan) return false;
            const plannedQueries = rows(plan.output.queries);
            if (!plannedQueries.length) return true;
            const searches = children.filter((child) =>
              ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(child.code),
            );
            if (
              searches.length !== plannedQueries.length
              || searches.some((child) => !child.ok)
            ) return false;
            const candidateFilter = children.find(
              (child) => child.code === 'I3_CANDIDATE_FILTER',
            );
            if (!candidateFilter?.ok) return false;
            const fetches = children.filter((child) => child.code === 'I3_FETCH');
            const expectedFetchCount = rows(
              candidateFilter.output.fetch_candidates,
            ).length;
            if (
              fetches.length !== expectedFetchCount
              || fetches.some((child) => !child.ok)
            ) return false;
            const retrievedDocumentIds = new Set(
              fetches
                .filter((child) => isRetrievedEvidence(child.output))
                .map((child) => evidenceDocumentKey(child.output))
                .filter(Boolean),
            );
            const qualifiedDocumentIds = new Set(
              children
                .filter((child) => child.ok && child.code === 'I3_QUALIFY')
                .map((child) => child.documentId || evidenceDocumentKey(child.output))
                .filter(Boolean),
            );
            if ([...retrievedDocumentIds].some(
              (documentId) => !qualifiedDocumentIds.has(documentId),
            )) return false;
            const analysisReadyDocumentIds = new Set(
              fetches
                .filter((child) => child.ok && isAnalysisReady(child.output))
                .map((child) => evidenceDocumentKey(child.output))
                .filter(Boolean),
            );
            if (!analysisReadyDocumentIds.size) return true;
            const comparedDocumentIds = new Set(
              children
                .filter(
                  (child) => child.ok && child.code === 'I4_S_SINGLE_REFERENCE',
                )
                .map((child) => child.documentId || evidenceDocumentKey(child.output))
                .filter(Boolean),
            );
            return [...analysisReadyDocumentIds].every(
              (documentId) => comparedDocumentIds.has(documentId),
            );
          })()
      : moduleId === 'inventiveStep'
        ? successful.some((child) => child.code === 'I4_I_INVENTIVE_STEP')
      : moduleId === 'evidence'
        ? evidenceSearches.length > 0
          && evidenceSearches.every((child) => child.ok)
          && (mode !== 'current' || Boolean(evidenceFilter?.ok))
          && evidenceAllAttempted
          && evidenceAllRetrieved
        : moduleId === 'report'
          ? successful.some((child) => child.code === 'I5_REPORT')
            || (mode === 'current' && Boolean(report))
          : successful.length > 0
            && (failed.length === 0 || moduleId === 'query' || moduleId === 'profile');
  const partial = !ok && successful.length > 0;
  return {
    ok,
    headline: ok
      ? mode === 'fixture'
        ? '内置案例运行完成'
        : '当前案件模块运行完成'
      : partial
        ? '本模块部分完成，仍有文献未处理成功'
        : '本模块没有得到可用输出',
    facts: facts.length ? facts : ['模块已运行，但没有返回可展示的业务字段。'],
  };
}

function disclosureLabel(value: unknown): string {
  const status = text(value);
  if (['disclosed', 'explicit', 'direct_and_unambiguous'].includes(status)) {
    return '明确披露';
  }
  if (status === 'structural_equivalent') return '结构等同披露';
  if (status === 'necessarily_implicit') return '必然隐含披露';
  if (status === 'not_disclosed') return '未披露';
  if (status === 'uncertain') return '暂不能确认';
  if (status === 'analysis_failed') return '分析失败';
  return status || '未评价';
}

function disclosureSummary(disclosures: JsonObject[]) {
  const disclosedStatuses = new Set([
    'disclosed',
    'explicit',
    'direct_and_unambiguous',
    'structural_equivalent',
    'necessarily_implicit',
  ]);
  let disclosed = 0;
  let notDisclosed = 0;
  let uncertain = 0;
  let failed = 0;

  for (const disclosure of disclosures) {
    const status = text(disclosure.status);
    if (disclosedStatuses.has(status)) disclosed += 1;
    else if (status === 'not_disclosed') notDisclosed += 1;
    else if (status === 'analysis_failed') failed += 1;
    else uncertain += 1;
  }

  return { disclosed, notDisclosed, uncertain, failed };
}

function disclosureBadgeClass(value: unknown): string {
  const status = text(value);
  if (['disclosed', 'explicit', 'direct_and_unambiguous', 'structural_equivalent', 'necessarily_implicit'].includes(status)) {
    return 'border-emerald-300 bg-emerald-50 text-emerald-800';
  }
  if (status === 'not_disclosed') {
    return 'border-slate-300 bg-slate-100 text-slate-700';
  }
  if (status === 'analysis_failed') {
    return 'border-red-300 bg-red-50 text-red-800';
  }
  return 'border-amber-300 bg-amber-50 text-amber-800';
}

function inventiveGapLabel(gap: JsonObject): string {
  const feature = text(gap.feature_text);
  const rationale = text(gap.rationale);
  if (feature && rationale) return `${feature}：${rationale}`;
  if (rationale) return rationale;
  if (feature) return `${feature}：尚缺少可核查的组合证据`;
  const subtypeLabels: Record<string, string> = {
    date_qualification: '至少一份组合文献未通过创造性日期资格',
    technical_problem: '缺少相同或相关技术问题的可核查证据',
    combination_motivation: '缺少组合动机或明确技术启示',
    teaching_away_review: '反向教导尚未得到明确评估',
    technical_effect: '缺少组合后技术效果可预期的可核查证据',
  };
  return subtypeLabels[text(gap.subtype)]
    || text(gap.description)
    || text(gap.gap_type)
    || '未说明的证据缺口';
}

function evidenceDocumentKey(value: JsonObject): string {
  return text(value.external_id)
    || text(value.publication_number)
    || text(value.id);
}

function normalizePublicationNumber(value: string): string {
  return value.replace(/[^A-Za-z0-9]/g, '').toUpperCase();
}

// I0 自动流程的检索命中与取文/日期结果不是同一条记录对象（external_id 会重新
// 生成），逐篇关联必须同时尝试 external_id、公开号（含规范化）以及从 EPO
// epodoc 链接解析出的公开号。
function documentJoinKeys(value: JsonObject): string[] {
  const keys = new Set<string>();
  const externalId = text(value.external_id);
  if (externalId) keys.add(externalId);
  const publicationNumber = text(value.publication_number);
  if (publicationNumber) {
    keys.add(publicationNumber);
    keys.add(normalizePublicationNumber(publicationNumber));
  }
  const id = text(value.id);
  if (id) keys.add(id);
  const sourceUrl = text(value.source_url);
  const epodoc = sourceUrl.match(/\/publication\/epodoc\/([A-Za-z]{2}[0-9]+)\.([A-Za-z][0-9]?)/);
  if (epodoc) {
    keys.add(normalizePublicationNumber(`${epodoc[1]}${epodoc[2]}`));
  }
  return [...keys];
}

function lookupDocumentMap<T>(map: Map<string, T>, document: JsonObject): T | undefined {
  for (const key of documentJoinKeys(document)) {
    const found = map.get(key);
    if (found !== undefined) return found;
  }
  return undefined;
}

function registerDocumentMap<T>(map: Map<string, T>, document: JsonObject, value: T): void {
  for (const key of documentJoinKeys(document)) {
    if (!map.has(key)) map.set(key, value);
  }
}

function childDocumentKey(child: ChildRun): string {
  if (child.documentId) return child.documentId;
  const input = runInput(child);
  return text(input.document_id)
    || text(input.query_id)
    || evidenceDocumentKey(record(input.document))
    || evidenceDocumentKey(child.output)
    || evidenceDocumentKey(record(rows(child.output.records)[0]));
}

function isRetrievedEvidence(value: JsonObject): boolean {
  return ['retrieved_document', 'qualified_evidence'].includes(text(value.stage))
    && Boolean(text(value.content_sha256));
}

function isAnalysisReady(value: JsonObject): boolean {
  return isRetrievedEvidence(value)
    && value.analysis_ready === true
    && Boolean(text(record(value.readable_document).full_text));
}

function hasDirectFullTextRoute(value: JsonObject): boolean {
  const pdfUrl = text(value.pdf_url);
  return /^https?:\/\//i.test(pdfUrl);
}

function queryInputText(child: ChildRun): string {
  const input = runInput(child);
  const query = input.query;
  if (typeof query === 'string') return query;
  return text(record(query).text) || text(record(query).expression);
}

function runInput(child: ChildRun): JsonObject {
  const wrapped = record(
    dig(child.raw, 'module_run', 'input_snapshot', 'request', 'input'),
  );
  if (Object.keys(wrapped).length) return wrapped;
  // I0 自动流程的 module run 直接把业务输入放在 input_snapshot 顶层，
  // 没有实验室 run 的 request.input 包装层。
  return record(dig(child.raw, 'module_run', 'input_snapshot'));
}

function uniqueText(values: unknown[]): string[] {
  return [...new Set(values.map((value) => text(value)).filter(Boolean))];
}

function mappingBasisLabel(value: unknown): string {
  const labels: Record<string, string> = {
    literal: '字面直接对应',
    literal_match: '字面直接对应',
    exact_structure: '相同结构',
    same_structure: '相同结构',
    explicit: '原文明确记载',
    direct_and_unambiguous: '直接、无歧义披露',
    role_and_relation: '结构角色及关系对应',
    structural_equivalent: '结构等同',
    integrated_structure: '集成结构对应',
    necessarily_implicit: '必然隐含',
    necessarily_implicit_from_operation: '由运行机理必然隐含',
    function_only: '仅功能相同，尚不足以确认',
    none: '未找到结构对应',
    not_disclosed: '未披露',
    uncertain: '结构对应待确认',
  };
  const basis = text(value);
  return labels[basis] || (basis ? '结构对应依据已记录' : '');
}

function structuredDetailItems(value: unknown): string[] {
  const values = Array.isArray(value) ? value : value === undefined || value === null ? [] : [value];
  return uniqueText(
    values.map((entry) => {
      if (typeof entry === 'string') return entry;
      const item = record(entry);
      const detail = text(item.text)
        || text(item.quote)
        || text(item.evidence_quote)
        || text(item.description)
        || text(item.step)
        || text(item.reasoning);
      const location = text(item.location)
        || text(item.locator)
        || text(item.evidence_location);
      return detail && location ? `${detail}（${location}）` : detail || location;
    }),
  );
}

function StructureMappingDetails({ disclosure }: { disclosure: JsonObject }) {
  const targetRole = text(disclosure.target_structural_role);
  const referenceMapping = text(disclosure.reference_structure_mapping);
  const integratedMapping = disclosure.integrated_structure_mapping === true;
  const searchSummary = text(disclosure.structural_search_summary);
  const structuralEvidence = structuredDetailItems(disclosure.structural_evidence);
  const necessityChain = structuredDetailItems(disclosure.necessity_chain);
  const alternativeAnalysis = text(disclosure.alternative_path_analysis);
  const implicitMapping = [
    'necessarily_implicit',
    'necessarily_implicit_from_operation',
  ].includes(text(disclosure.mapping_basis))
    || text(disclosure.status) === 'necessarily_implicit';
  const alternativesExcluded = implicitMapping
    && typeof disclosure.reasonable_alternatives_excluded === 'boolean'
    ? disclosure.reasonable_alternatives_excluded
    : null;
  const basis = mappingBasisLabel(disclosure.mapping_basis);
  const hasDetail = Boolean(
    searchSummary
    || structuralEvidence.length
    || necessityChain.length
    || alternativeAnalysis,
  );
  const hasMapping = Boolean(
    basis
    || targetRole
    || referenceMapping
    || integratedMapping
    || hasDetail
    || alternativesExcluded !== null,
  );

  if (!hasMapping) {
    return (
      <p className="text-muted-foreground">旧结果未保存结构映射依据</p>
    );
  }

  return (
    <div className="min-w-[250px] space-y-2 break-words">
      {(basis || alternativesExcluded !== null) ? (
        <div className="flex flex-wrap gap-1.5">
          {basis ? <Badge variant="outline">{basis}</Badge> : null}
          {alternativesExcluded !== null ? (
            <Badge variant={alternativesExcluded ? 'secondary' : 'outline'}>
              {alternativesExcluded
                ? '已排除合理替代路径'
                : '尚未排除合理替代路径'}
            </Badge>
          ) : null}
        </div>
      ) : null}
      {targetRole ? (
        <p><span className="font-medium">目标结构角色：</span>{targetRole}</p>
      ) : null}
      {referenceMapping ? (
        <p><span className="font-medium">对比文件对应结构：</span>{referenceMapping}</p>
      ) : null}
      {integratedMapping ? (
        <p><span className="font-medium">集成结构对应：</span>同一集成结构承接多个目标特征</p>
      ) : null}
      {hasDetail ? (
        <details className="rounded border bg-muted/30 px-2 py-1.5">
          <summary className="cursor-pointer font-medium">查看结构判断依据</summary>
          <div className="mt-2 space-y-2 text-xs leading-5">
            {searchSummary ? (
              <div>
                <p className="font-medium">全文结构核查概况</p>
                <p className="mt-0.5 text-muted-foreground">{searchSummary}</p>
              </div>
            ) : null}
            {structuralEvidence.length ? (
              <div>
                <p className="font-medium">结构证据</p>
                <ul className="mt-0.5 list-disc space-y-1 pl-4 text-muted-foreground">
                  {structuralEvidence.map((item, index) => (
                    <li key={`${item}-${index}`}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {necessityChain.length ? (
              <div>
                <p className="font-medium">必然性推导链</p>
                <ol className="mt-0.5 list-decimal space-y-1 pl-4 text-muted-foreground">
                  {necessityChain.map((item, index) => (
                    <li key={`${item}-${index}`}>{item}</li>
                  ))}
                </ol>
              </div>
            ) : null}
            {alternativeAnalysis ? (
              <div>
                <p className="font-medium">替代路径分析</p>
                <p className="mt-0.5 text-muted-foreground">{alternativeAnalysis}</p>
              </div>
            ) : null}
          </div>
        </details>
      ) : null}
    </div>
  );
}

function persistedDisclosureView(value: JsonObject | undefined): JsonObject {
  const disclosure = value || {};
  const analysis = record(disclosure.analysis);
  const locator = record(disclosure.locator);
  return {
    ...disclosure,
    ...analysis,
    status: disclosure.disclosure_status || disclosure.status,
    evidence_quote: disclosure.excerpt || analysis.evidence_quote,
    evidence_location: text(locator.location)
      || text(disclosure.locator)
      || text(analysis.evidence_location),
    reasoning: analysis.reasoning || disclosure.reasoning,
  };
}

function readableDocumentName(value: JsonObject, fallback = '未编号文献'): string {
  const number = text(value.publication_number)
    || text(value.external_id)
    || text(value.document_id)
    || text(value.id);
  const title = text(value.title) || text(value.document_title);
  if (number && title) return `${number}《${title}》`;
  return number || (title ? `《${title}》` : fallback);
}

function BusinessInputDetails({
  moduleId,
  state,
  fallbackReport,
  investigation,
  allBusinessStates,
}: {
  moduleId: BusinessModuleId;
  state: BusinessState;
  fallbackReport: JsonObject | null;
  investigation: JsonObject | null;
  allBusinessStates: Record<string, BusinessState>;
}) {
  const runs = state.children || [];
  const fixture = state.mode === 'fixture';
  const reportPatent = record(dig(fallbackReport, 'target_patent'));
  const investigationPatent = record(
    dig(investigation, 'source_snapshot', 'patent_snapshot'),
  );
  const patent = fixture
    ? {}
    : Object.keys(investigationPatent).length
      ? investigationPatent
      : reportPatent;

  if (moduleId === 'target') {
    const output = runs.find((child) => child.code === 'I1_TARGET_SNAPSHOT')?.output || {};
    const patentName = readableDocumentName({
      publication_number: output.patent_number
        || patent.patent_number
        || (fixture ? 'CN209999999U' : undefined),
      title: output.title
        || patent.title
        || (fixture ? '一种光学检测装置' : undefined),
    }, fixture ? '内置示例专利' : '当前案件专利');
    const byteSize = numberValue(output.source_byte_size);
    return (
      <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            模块读取的是这份原始专利文件，不是律师手工填写的摘要。
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">送入的专利</p>
            <p className="mt-1 font-medium">{patentName}</p>
          </div>
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">送入的文件内容</p>
            <p className="mt-1">
              {text(output.source_format).toUpperCase() || '专利文件'}
              {numberValue(output.page_count) !== null
                ? ` · ${numberValue(output.page_count)} 页`
                : ''}
              {byteSize !== null ? ` · ${(byteSize / 1024).toFixed(1)} KB` : ''}
            </p>
          </div>
        </div>
      </div>
    );
  }

  if (moduleId === 'date') {
    const output = runs.find((child) => child.code === 'I1_5_CLAIM_DATES')?.output || {};
    const critical = record(output.critical_date);
    const applicationDate = fixture
      ? '2020-01-02'
      : text(patent.application_date) || '未识别';
    const priorityDate = fixture
      ? '2019-01-02'
      : text(patent.priority_date) || '无可解析优先权日';
    return (
      <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            日期直接来自目标专利冻结事实；律师本次不需要再次填写。
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">申请日</p>
            <p className="mt-1 font-medium">{applicationDate}</p>
          </div>
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">最早优先权日</p>
            <p className="mt-1 font-medium">{priorityDate}</p>
          </div>
        </div>
        {text(critical.critical_date) ? (
          <p className="text-xs text-muted-foreground">
            系统将以上日期交给确定性日期规则计算，不让模型猜测截止日。
          </p>
        ) : null}
      </div>
    );
  }

  if (moduleId === 'profile') {
    const queryRuns = runs.filter((child) => child.code === 'I2_INVENTIVE_PROFILE');
    const patentName = fixture
      ? 'CN209999999U《一种光学检测装置》（内置示例）'
      : readableDocumentName({
          publication_number: patent.patent_number,
          title: patent.title,
        }, '当前案件冻结的完整专利');
    const specRecord = record(patent.specification);
    const specSections = Object.entries(specRecord)
      .filter(([name, value]) => name !== '全文' && text(value))
      .map(([name, value]) => ({ name, value: text(value) }));
    if (!specSections.length && text(specRecord['全文'])) {
      specSections.push({ name: '说明书全文', value: text(specRecord['全文']) });
    }
    const specCharCount = specSections.reduce((sum, item) => sum + item.value.length, 0);
    const abstractText = text(patent.abstract);
    const totalClaimCount = rows(patent.claims).length;
    const figureCount = rows(patent.figure_overview).length || rows(patent.figures).length;
    return (
      <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            系统实际通读 {patentName} 的标题、摘要、全部权利要求、说明书和附图，再结合说明书单独分析下列独立权利要求。
          </p>
        </div>
        {!fixture && (abstractText || specSections.length) ? (
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">完整专利阅读范围（与权利要求一起送入分析）</p>
            {abstractText ? (
              <p className="mt-2 whitespace-pre-wrap text-sm leading-6">
                <span className="font-medium">摘要：</span>
                {abstractText}
              </p>
            ) : null}
            {specSections.length ? (
              <details className="mt-2">
                <summary className="cursor-pointer text-sm text-muted-foreground">
                  说明书 {specSections.length} 个章节、共约 {specCharCount} 字（点击展开全文）
                </summary>
                <div className="mt-2 space-y-2">
                  {specSections.map((section) => (
                    <div key={section.name} className="rounded border p-2">
                      <p className="text-xs font-medium text-muted-foreground">
                        {section.name}（约 {section.value.length} 字）
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{section.value}</p>
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
            <div className="mt-2 flex flex-wrap gap-2">
              {totalClaimCount ? <Badge variant="outline">全部权利要求 {totalClaimCount} 项</Badge> : null}
              {figureCount ? <Badge variant="outline">附图 {figureCount} 幅</Badge> : null}
            </div>
          </div>
        ) : null}
        <div className="space-y-2">
          {queryRuns.map((child, index) => {
            const input = runInput(child);
            const claimId = text(input.claim_id) || text(child.output.claim_id) || String(index + 1);
            const limitations = rows(child.output.limitations);
            const claimText = text(input.expanded_claim_text)
              || text(
                patentClaimsFrom(investigation, fallbackReport).find(
                  (claim) => text(claim.claim_id) === claimId,
                )?.expanded_claim_text,
              )
              || text(
                patentClaimsFrom(investigation, fallbackReport).find(
                  (claim) => text(claim.claim_id) === claimId,
                )?.claim_text,
              );
            return (
              <div key={`${claimId}-${index}`} className="rounded border bg-background p-3">
                <p className="text-xs text-muted-foreground">独立权利要求 {claimId}</p>
                {claimText ? (
                  <p className="mt-1 whitespace-pre-wrap text-sm leading-6">{claimText}</p>
                ) : limitations.length ? (
                  <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
                    {limitations.map((item, featureIndex) => (
                      <li key={text(item.feature_id) || String(featureIndex)}>
                        {text(item.text)}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-sm text-muted-foreground">
                    本次运行未能展开独立权利要求原文。
                  </p>
                )}
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  if (moduleId === 'query') {
    const queryRuns = runs.filter((child) => child.code === 'I2_QUERY_PLAN');
    const profileSourceRuns = (allBusinessStates['profile']?.children || []).filter(
      (child) => child.ok && child.code === 'I2_INVENTIVE_PROFILE',
    );
    const isAutoFlowQueryRun = (child: (typeof queryRuns)[number]) =>
      !text(child.output.source_plan_run_id)
      && Boolean(record(child.output.invention_search_profile).invention_summary
        || rows(record(child.output.invention_search_profile).inventive_point_features).length
        || text(child.output.generation_source) === 'live_model');
    const hasAutoFlowRuns = queryRuns.some(isAutoFlowQueryRun);
    return (
      <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            实验室逐阶段运行时，模块4只读取模块3总结的核心发明点与技术特征，不再重读专利的摘要、说明书或权利要求原文，检索关键词由大模型基于模块3的总结分析生成。
          </p>
          {hasAutoFlowRuns ? (
            <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
              Agent 自动流程没有独立的模块3运行：模块4在同一运行内先由大模型阅读完整专利（标题、摘要、全部权利要求、说明书、附图）并总结核心发明点，再据此生成检索关键词。
            </p>
          ) : null}
        </div>
        <div className="space-y-2">
          {queryRuns.map((child, index) => {
            const input = runInput(child);
            const runClaimId = text(input.claim_id) || text(child.output.claim_id);
            const sourceRunId = text(child.output.source_plan_run_id);
            const sourceRun = profileSourceRuns.find(
              (item) => Boolean(sourceRunId) && item.moduleRunId === sourceRunId,
            ) || [...profileSourceRuns].reverse().find(
              (item) => !runClaimId || text(item.output.claim_id) === runClaimId,
            );
            const sourceProfile = record(sourceRun?.output.invention_search_profile);
            const limitationByFeatureId = new Map(
              rows(sourceRun?.output.limitations)
                .map((item) => [text(item.feature_id), item] as const)
                .filter(([featureId]) => Boolean(featureId)),
            );
            const embeddedProfile = record(child.output.invention_search_profile);
            const embeddedLimitationByFeatureId = new Map(
              rows(child.output.limitations)
                .map((item) => [text(item.feature_id), item] as const)
                .filter(([featureId]) => Boolean(featureId)),
            );
            const autoFlow = !sourceRun && isAutoFlowQueryRun(child);
            const displayProfile = sourceRun ? sourceProfile : embeddedProfile;
            const displayLimitations = sourceRun ? limitationByFeatureId : embeddedLimitationByFeatureId;
            const displayPoints = rows(displayProfile.inventive_point_features);
            return (
              <div
                key={`${runClaimId || index + 1}-${index}`}
                className="space-y-2 rounded border bg-background p-3"
              >
                <p className="text-xs text-muted-foreground">
                  {sourceRun
                    ? `独立权利要求 ${runClaimId || index + 1} 实际读取的模块3结果`
                    : autoFlow
                      ? `独立权利要求 ${runClaimId || index + 1}（自动流程：大模型在同一运行内先总结发明点再生成检索词）`
                      : `独立权利要求 ${runClaimId || index + 1}`}
                </p>
                {autoFlow && text(input.expanded_claim_text) ? (
                  <div className="rounded border bg-muted/30 p-2">
                    <p className="text-xs font-medium">本次实际输入的独立权利要求原文</p>
                    <p className="mt-1 whitespace-pre-wrap text-sm leading-6">
                      {text(input.expanded_claim_text)}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      另读取完整专利上下文（标题、摘要、全部权利要求、说明书章节）及
                      {` ${Number(input.target_image_count) || 0} `}张附图。
                    </p>
                  </div>
                ) : null}
                {sourceRun || autoFlow ? (
                  <>
                    <div className="rounded border border-primary/30 bg-primary/5 p-2">
                      <p className="text-xs font-medium">
                        {autoFlow ? '本次运行内大模型总结的核心发明点' : '核心发明点总结'}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-sm leading-6">
                        {text(displayProfile.invention_summary)
                          || '本次运行没有返回核心发明点总结。'}
                      </p>
                    </div>
                    <ul className="space-y-2 text-sm">
                      {displayPoints.map((concept, conceptIndex) => {
                        const featureIds = stringValues(concept.feature_ids);
                        const featureTexts = featureIds.map(
                          (featureId) =>
                            text(displayLimitations.get(featureId)?.text)
                            || featureId,
                        );
                        return (
                          <li
                            key={text(concept.concept_id) || String(conceptIndex)}
                            className="rounded border bg-muted/30 p-2"
                          >
                            <p className="font-medium">{text(concept.text)}</p>
                            {featureTexts.length ? (
                              <ul className="mt-1 list-disc space-y-1 pl-5 text-muted-foreground">
                                {featureTexts.map((featureText, featureIndex) => (
                                  <li key={`${featureIds[featureIndex]}-${featureIndex}`}>
                                    {featureText}
                                  </li>
                                ))}
                              </ul>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    未找到模块3的输出，请先运行模块3。
                  </p>
                )}
              </div>
            );
          })}
          {!queryRuns.length ? (
            <p className="text-sm text-muted-foreground">本次没有生成检索关键词的运行记录。</p>
          ) : null}
        </div>
      </div>
    );
  }

  if (moduleId === 'evidence') {
    const searches = runs.filter((child) =>
      ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(child.code),
    );
    const candidateFilter = runs.find(
      (child) => child.code === 'I3_CANDIDATE_FILTER',
    );
    const fetches = runs.filter((child) => child.code === 'I3_FETCH');
    const qualifications = runs.filter((child) => child.code === 'I3_QUALIFY');
    return (
      <div className="space-y-4 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            下面列的是本次真正提交的检索式，以及逐篇送去取全文和核验日期的候选。
          </p>
        </div>
        {candidateFilter ? (
          <div className="rounded border bg-background p-3">
            <p className="text-sm font-medium">取全文前候选清理</p>
            <p className="mt-1 text-sm text-muted-foreground">
              输入为上述 {stringValues(candidateFilter.output.source_search_run_ids).length || searches.length}
              {' '}次检索的持久化前 10 条结果；不要求律师另填文献。
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Badge variant="outline">
                原始 {numberValue(candidateFilter.output.raw_candidate_count) ?? 0} 条
              </Badge>
              <Badge variant="secondary">
                合并重复 {numberValue(candidateFilter.output.duplicate_count) ?? 0} 条
              </Badge>
              <Badge variant="secondary">
                排除明显无关 {numberValue(candidateFilter.output.excluded_count) ?? 0} 条
              </Badge>
              <Badge variant="outline">
                实际取文 {numberValue(candidateFilter.output.fetch_candidate_count) ?? 0} 篇
              </Badge>
            </div>
          </div>
        ) : null}
        <div>
          <p className="text-sm font-medium">实际检索输入</p>
          <div className="mt-2 space-y-2">
            {searches.map((child, index) => {
              const input = runInput(child);
              const cutoff = text(input.server_before) || text(input.critical_date);
              return (
                <div key={`${child.code}-${index}`} className="rounded border bg-background p-3">
                  <div className="flex flex-wrap gap-2">
                    <Badge variant="outline">
                      {child.code === 'I3_PATENT_SEARCH' ? '专利' : '论文/技术资料'}
                    </Badge>
                    {cutoff ? <Badge variant="secondary">公开日早于 {cutoff}</Badge> : null}
                  </div>
                  <p className="mt-2 break-words font-mono text-sm">
                    {queryInputText(child) || text(child.output.query_text) || '没有可执行检索式'}
                  </p>
                </div>
              );
            })}
            {!searches.length ? (
              <p className="text-sm text-muted-foreground">本次没有形成检索输入。</p>
            ) : null}
          </div>
        </div>
        <div>
          <p className="text-sm font-medium">逐篇取文输入（{fetches.length} 篇）</p>
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            {fetches.map((child, index) => {
              const input = runInput(child);
              const document = Object.keys(record(input.document)).length
                ? record(input.document)
                : child.output;
              const batchRecords = rows(child.output.records);
              return (
                <div key={`${childDocumentKey(child)}-${index}`} className="rounded border bg-background p-3 text-sm">
                  {readableDocumentName(document, child.documentId || `候选 ${index + 1}`)}
                  {batchRecords.length ? (
                    <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                      {batchRecords.map((item, recordIndex) => (
                        <li key={`${evidenceDocumentKey(item)}-${recordIndex}`}>
                          {readableDocumentName(item, `记录 ${recordIndex + 1}`)}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              );
            })}
          </div>
          {!fetches.length ? (
            <p className="mt-2 text-sm text-muted-foreground">
              没有候选进入全文获取。
            </p>
          ) : null}
        </div>
        {qualifications.length ? (
          <p className="text-xs text-muted-foreground">
            日期核验输入：{uniqueText(
              qualifications.map((child) => childDocumentKey(child)),
            ).join('、') || `${qualifications.length} 份已取得全文的资料`}。
          </p>
        ) : null}
      </div>
    );
  }

  if (moduleId === 'singleReference') {
    const comparisons = runs.filter(
      (child) => child.code === 'I4_S_SINGLE_REFERENCE',
    );
    const queryRuns = state.mode === 'current'
      ? allBusinessStates.query?.children || []
      : [];
    const featureTexts = uniqueText(
      [
        ...comparisons.flatMap((child) =>
          rows(child.output.disclosures).map((item) => item.feature_text),
        ),
        ...queryRuns.flatMap((child) =>
          rows(child.output.limitations).map((item) => item.text),
        ),
        ...rows(fallbackReport?.claim_limitations).map(
          (item) => item.limitation_text,
        ),
      ],
    );
    const inputDocuments = comparisons.map((child, index) => {
      const input = runInput(child);
      return readableDocumentName({
        document_id: input.document_id || child.documentId,
        publication_number: child.output.publication_number,
        title: child.output.document_title,
      }, `对比文件 ${index + 1}`);
    });
    return (
      <div className="space-y-4 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            每份已取得全文的资料分别与同一项独立权利要求比对，不把多篇文献拼成一篇。
          </p>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded border bg-background p-3">
            <p className="text-sm font-medium">独立权利要求的技术特征</p>
            {featureTexts.length ? (
              <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
                {featureTexts.map((value) => <li key={value}>{value}</li>)}
              </ol>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">
                本次没有取得可用于逐篇比对的技术特征。
              </p>
            )}
          </div>
          <div className="rounded border bg-background p-3">
            <p className="text-sm font-medium">逐篇送入的对比文件（{inputDocuments.length} 篇）</p>
            {inputDocuments.length ? (
              <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm">
                {inputDocuments.map((value, index) => (
                  <li key={`${value}-${index}`}>{value}</li>
                ))}
              </ol>
            ) : (
              <p className="mt-2 text-sm text-muted-foreground">没有可核验全文作为输入。</p>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (moduleId === 'closestPriorArt') {
    const closest = runs.find((child) => child.code === 'I4_C_CLOSEST_PRIOR_ART');
    const comparisonRuns = state.mode === 'current'
      ? allBusinessStates.singleReference?.children || []
      : [];
    const comparableDocuments = comparisonRuns.filter(
      (child) => child.ok && child.code === 'I4_S_SINGLE_REFERENCE',
    );
    return (
      <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            系统只在已完成单篇比对且日期合格的文献中选择 D1，不把检索排序直接当作最接近现有技术。
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">候选单篇比对</p>
            <p className="mt-1 font-medium">
              {comparableDocuments.length || (state.mode === 'fixture' ? 1 : 0)} 份
            </p>
          </div>
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">本次选择范围</p>
            <p className="mt-1 font-medium">确认披露数量、技术领域、作用和证据完整度</p>
          </div>
        </div>
        {closest ? (
          <p className="text-xs text-muted-foreground">
            运行将从持久化 I4-S 矩阵重新计算 D1，并冻结区别特征，不依赖前端传入结论。
          </p>
        ) : null}
      </div>
    );
  }

  if (moduleId === 'obviousnessPrecheck') {
    const closestRun = state.mode === 'current'
      ? (allBusinessStates.closestPriorArt?.children || []).find(
          (child) => child.ok && child.code === 'I4_C_CLOSEST_PRIOR_ART',
        )
      : undefined;
    const differences = rows(closestRun?.output.distinguishing_features);
    return (
      <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            保持这些项目仍为 D1 区别特征；本步骤另行分析 D1 的改进启示、惯用手段、修改动机、反向教导与效果可预期性。
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">当前 D1</p>
            <p className="mt-1 font-medium">{text(closestRun?.output.document_id) || (state.mode === 'fixture' ? 'D1' : '等待模块7选择')}</p>
          </div>
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">待预分析区别特征</p>
            <p className="mt-1 font-medium">{differences.length || (state.mode === 'fixture' ? 1 : 0)} 项</p>
          </div>
        </div>
      </div>
    );
  }

  if (moduleId === 'gapSearch') {
    const gapPlan = runs.find((child) => child.code === 'I2_GAP_QUERY_PLAN');
    const closestRun = state.mode === 'current'
      ? (allBusinessStates.closestPriorArt?.children || []).find(
          (child) => child.ok && child.code === 'I4_C_CLOSEST_PRIOR_ART',
        )
      : undefined;
    const closest = gapPlan?.output || closestRun?.output || {};
    const differences = rows(closest.distinguishing_features);
    const precheck = record(gapPlan?.output.obviousness_precheck);
    const firstDifference = differences[0] || {};
    const currentD1 = text(gapPlan?.output.closest_document_id)
      || text(gapPlan?.output.document_id)
      || text(precheck.closest_document_id)
      || text(firstDifference.d1_document_id)
      || text(closestRun?.output.document_id);
    return (
      <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            每次点击测试一个可审计补证轮。系统先读取模块8路由，只对仍需补证的目标生成对应类型检索式，再执行真实检索、候选清理、全文取得、日期核验和 I4-S 逐篇比对。
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">当前 D1</p>
            <p className="mt-1 font-medium">{currentD1 || '等待模块7选择'}</p>
          </div>
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">冻结区别特征</p>
            <p className="mt-1 font-medium">{differences.length} 项</p>
          </div>
        </div>
        {differences.length ? (
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            {differences.map((item, index) => (
              <li key={text(item.feature_id) || index}>
                {text(item.feature_text) || text(item.target_feature_text) || text(item.feature_id)}
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    );
  }

  if (moduleId === 'inventiveStep') {
    const closestRun = state.mode === 'current'
      ? (allBusinessStates.closestPriorArt?.children || []).find(
          (child) => child.ok && child.code === 'I4_C_CLOSEST_PRIOR_ART',
        )
      : undefined;
    const comparisonCount = state.mode === 'current'
      ? (allBusinessStates.singleReference?.children || []).filter(
          (child) => child.ok && child.code === 'I4_S_SINGLE_REFERENCE',
        ).length
      : 2;
    return (
      <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
        <div>
          <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
          <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
            创造性分析读取已冻结 D1、逐篇披露矩阵和日期资格，单独判断组合启示、反向教导和技术效果。
          </p>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">D1</p>
            <p className="mt-1 font-medium">{text(closestRun?.output.document_id) || (state.mode === 'fixture' ? 'D1' : '读取持久化选择')}</p>
          </div>
          <div className="rounded border bg-background p-3">
            <p className="text-xs text-muted-foreground">可用单篇矩阵</p>
            <p className="mt-1 font-medium">{comparisonCount} 份</p>
          </div>
        </div>
      </div>
    );
  }

  const reportRun = runs.find((child) => child.code === 'I5_REPORT');
  const reportOutput = reportRun?.output || {};
  const reportValue = record(reportOutput.report_data);
  const activeReport = Object.keys(reportValue).length ? reportValue : fallbackReport;
  const appendix = record(reportOutput.lab_analysis_appendix);
  const scopedClaims = reportClaimsInScope(activeReport, investigation);
  const claimCount = scopedClaims.length || (text(appendix.claim_id) ? 1 : 0);
  const disclosureCount = rows(activeReport?.feature_disclosures).length
    || Object.values(record(appendix.comparisons)).reduce(
      (total: number, value: unknown) =>
        total + rows(record(value).disclosures).length,
      0,
    );
  const documentCount = rows(activeReport?.documents).length
    || Object.keys(record(appendix.documents)).length;
  return (
    <div className="space-y-3 rounded-lg border border-blue-200 bg-blue-50/70 p-4 dark:border-blue-900 dark:bg-blue-950/20">
      <div>
        <p className="font-medium text-blue-950 dark:text-blue-100">本次实际输入</p>
        <p className="mt-1 text-xs text-blue-800/80 dark:text-blue-200/80">
          报告模块直接读取前十个阶段已经持久化的结果，不要求律师重新填写。
        </p>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded border bg-background p-3">
          <p className="text-xs text-muted-foreground">独立权利要求</p>
          <p className="mt-1 font-medium">{claimCount} 项</p>
        </div>
        <div className="rounded border bg-background p-3">
          <p className="text-xs text-muted-foreground">对比资料</p>
          <p className="mt-1 font-medium">{documentCount} 份</p>
        </div>
        <div className="rounded border bg-background p-3">
          <p className="text-xs text-muted-foreground">逐项披露记录</p>
          <p className="mt-1 font-medium">{disclosureCount} 条</p>
        </div>
      </div>
    </div>
  );
}

const CRITICAL_DATE_BASIS_LABELS: Record<string, string> = {
  verified_priority_date: '最早优先权日（已核验）',
  patent_level_priority_date: '最早优先权日（专利级自动适用）',
  application_date: '申请日',
  patent_level_application_date: '申请日（专利级自动适用）',
  manual: '人工确认日期',
};

function criticalDateBasisLabel(value: unknown): string {
  const basis = text(value);
  return CRITICAL_DATE_BASIS_LABELS[basis] || basis;
}

function DateResultDetails({
  runs,
  fallbackReport,
}: {
  runs: ChildRun[];
  fallbackReport?: JsonObject | null;
}) {
  const output = runs.find(
    (child) => child.ok && child.code === 'I1_5_CLAIM_DATES',
  )?.output || {};
  const critical = record(output.critical_date);
  // 管理员同任务只读视图：自动流程没有独立 I1_5 module run，
  // 关键日按专利级规则直接写入各独立权利要求，从报告预览回退读取。
  const reportClaims = rows(fallbackReport?.claim_investigations)
    .filter((claim) => claim.in_scope !== false);
  const datedClaims = reportClaims.filter((claim) => text(claim.critical_date));
  const unifiedDate = text(critical.critical_date) || text(datedClaims[0]?.critical_date);
  const unifiedBasis = text(critical.basis) || text(critical.reason)
    || criticalDateBasisLabel(datedClaims[0]?.critical_date_basis);
  const mixedDates = new Set(datedClaims.map((claim) => text(claim.critical_date))).size > 1;
  return (
    <div className="space-y-3">
      <div className="rounded border bg-background p-4">
        <p className="text-xs text-muted-foreground">本案统一检索截止日</p>
        <p className="mt-1 text-lg font-semibold">
          {unifiedDate || '未能确定'}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          依据：{unifiedBasis || '日期规则未返回依据'}
        </p>
      </div>
      {!text(critical.critical_date) && datedClaims.length ? (
        <div className="rounded border bg-background p-4 text-sm">
          <p className="text-xs text-muted-foreground">各独立权利要求关键日（持久化记录）</p>
          <ul className="mt-2 space-y-1">
            {reportClaims.map((claim, index) => (
              <li key={text(claim.claim_id) || index}>
                权利要求 {text(claim.claim_id) || '?'}：
                {text(claim.critical_date)
                  ? `${text(claim.critical_date)}（依据：${criticalDateBasisLabel(claim.critical_date_basis) || '未标注'}）`
                  : '等待开始（进入该权利要求流程时才确定关键日）'}
              </li>
            ))}
          </ul>
          {mixedDates ? (
            <p className="mt-2 text-xs text-amber-700">
              注意：各权利要求关键日不一致，请逐项核对，不要把首个日期当作全案统一截止日。
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function TargetResultDetails({
  runs,
  fallbackReport,
  attachedSessionId,
}: {
  runs: ChildRun[];
  fallbackReport: JsonObject | null;
  attachedSessionId?: string;
}) {
  const successfulRun = runs.find((child) => child.ok);
  const output = successfulRun?.output || {};
  const moduleRunId = text(dig(successfulRun?.raw, 'module_run', 'id'));
  // 管理员同任务只读视图：Agent 自动流程没有独立的 I1 module run，
  // 以下字段统一从报告预览的 target_patent 回退，缺失时如实显示未识别。
  const fallbackPatent = record(dig(fallbackReport, 'target_patent'));
  const allClaims = rows(output.claims).length
    ? rows(output.claims)
    : rows(fallbackPatent.claims).length
      ? rows(fallbackPatent.claims)
      : rows(output.independent_claims);
  const figures = rows(output.figure_overview).length
    ? rows(output.figure_overview)
    : rows(fallbackPatent.figures);
  const specification = Object.keys(record(output.specification)).length
    ? record(output.specification)
    : record(fallbackPatent.specification);
  const sectionEntries = Object.entries(specification)
    .filter(([name, value]) => name !== '全文' && text(value))
    .map(([name, value]) => ({ name, value: text(value) }));
  if (!sectionEntries.length && text(specification['全文'])) {
    sectionEntries.push({ name: '说明书全文', value: text(specification['全文']) });
  }
  const bibliography = Object.keys(record(output.bibliographic_data)).length
    ? record(output.bibliographic_data)
    : record(fallbackPatent.bibliographic_data);
  const inventors = stringValues(bibliography.inventors);
  const classifications = stringValues(bibliography.classifications);
  const dateRows = [
    ['申请日', text(output.application_date) || text(fallbackPatent.application_date)],
    ['最早优先权日', text(output.priority_date) || text(fallbackPatent.priority_date)],
    ['公开/公告日', text(output.publication_date) || text(fallbackPatent.publication_date)],
    ['授权公告日', text(output.grant_date) || text(fallbackPatent.grant_date)],
  ].filter((item) => item[1]);
  const parserErrors = rows(output.parser_errors).length
    ? rows(output.parser_errors)
    : rows(fallbackPatent.parser_errors);
  const pageCount = numberValue(output.page_count) ?? numberValue(fallbackPatent.page_count);
  const sourceFormat = text(output.source_format) || text(fallbackPatent.source_format);
  const usedOcr = output.used_ocr === true || fallbackPatent.used_ocr === true;
  return (
    <div className="space-y-5">
      <div className="rounded-lg border bg-background p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs text-muted-foreground">目标专利</p>
            <p className="mt-1 text-lg font-semibold">
              {text(output.patent_number) || text(dig(fallbackReport, 'target_patent', 'patent_number')) || '专利号未识别'}
              {(text(output.title) || text(dig(fallbackReport, 'target_patent', 'title')))
                ? `《${text(output.title) || text(dig(fallbackReport, 'target_patent', 'title'))}》`
                : ''}
            </p>
            <p className="mt-2 text-sm text-muted-foreground">
              申请号：{text(output.application_number) || text(bibliography.application_number) || text(fallbackPatent.application_number) || '未识别'}
              {' · '}
              权利人/申请人：{text(output.holder) || text(fallbackPatent.holder) || '未识别'}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">
              {sourceFormat.toUpperCase() || '未知格式'}
            </Badge>
            <Badge variant="outline">
              {pageCount ?? '未知'} 页
            </Badge>
            {usedOcr ? <Badge variant="secondary">已使用 OCR</Badge> : null}
          </div>
        </div>
      </div>

      <section>
        <p className="font-medium">著录项目与日期</p>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {dateRows.length ? dateRows.map(([label, value]) => (
            <div key={label} className="rounded border bg-background p-3">
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="mt-1 font-medium">{value}</p>
            </div>
          )) : (
            <p className="text-sm text-muted-foreground">没有识别到可核验日期。</p>
          )}
        </div>
        <div className="mt-2 space-y-1 text-sm text-muted-foreground">
          {inventors.length ? <p>发明人：{inventors.join('、')}</p> : null}
          {text(bibliography.patent_agency)
            ? <p>代理机构：{text(bibliography.patent_agency)}</p>
            : null}
          {classifications.length ? <p>分类号：{classifications.join('；')}</p> : null}
        </div>
      </section>

      <section className="rounded-lg border bg-background p-4">
        <p className="font-medium">摘要</p>
        <p className="mt-2 whitespace-pre-wrap text-sm leading-6">
          {text(output.abstract) || text(dig(fallbackReport, 'target_patent', 'abstract')) || '没有识别到摘要原文。'}
        </p>
      </section>

      <section className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-medium">全部权利要求</p>
          <p className="text-xs text-muted-foreground">
            共 {numberValue(output.claim_count) ?? allClaims.length} 项；
            后续检索仅处理其中 {numberValue(output.independent_claim_count) ?? allClaims.filter(isIndependentPatentClaim).length} 项独立权利要求
          </p>
        </div>
        <Alert>
          <AlertTitle>读取范围与检索范围不同</AlertTitle>
          <AlertDescription>
            模块一完整保留独立和从属权利要求及依赖关系；本轮实际处理的独立权利要求只在后续检索与分析模块中使用。
          </AlertDescription>
        </Alert>
        <Accordion type="multiple" className="rounded-lg border bg-background px-4">
          {allClaims.map((claim, index) => {
            const independent = isIndependentPatentClaim(claim);
            const parents = stringValues(claim.parent_claim_ids);
            return (
              <AccordionItem
                key={text(claim.claim_id) || String(index)}
                value={`claim-${text(claim.claim_id) || index}`}
              >
                <AccordionTrigger className="gap-3 text-left">
                  <span className="flex flex-wrap items-center gap-2">
                    <span>权利要求 {text(claim.claim_id) || index + 1}</span>
                    <Badge variant={independent ? 'default' : 'secondary'}>
                      {independent ? '独立' : '从属'}
                    </Badge>
                    {parents.length ? (
                      <span className="text-xs font-normal text-muted-foreground">
                        引用权利要求 {parents.join('、')}
                      </span>
                    ) : null}
                  </span>
                </AccordionTrigger>
                <AccordionContent>
                  <p className="whitespace-pre-wrap text-sm leading-6">
                    {text(claim.claim_text)}
                  </p>
                </AccordionContent>
              </AccordionItem>
            );
          })}
        </Accordion>
        {!allClaims.length ? (
          <p className="text-muted-foreground">没有识别到可展示的权利要求原文。</p>
        ) : null}
      </section>

      <section>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-medium">说明书结构</p>
          <span className="text-xs text-muted-foreground">
            {numberValue(output.specification_section_count) ?? sectionEntries.length} 个正文部分
          </span>
        </div>
        <Accordion type="multiple" className="mt-2 rounded-lg border bg-background px-4">
          {sectionEntries.map((section, index) => (
            <AccordionItem key={`${section.name}-${index}`} value={`spec-${index}`}>
              <AccordionTrigger>{section.name}</AccordionTrigger>
              <AccordionContent>
                <p className="whitespace-pre-wrap text-sm leading-6">{section.value}</p>
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
        {!sectionEntries.length ? (
          <p className="mt-2 text-sm text-muted-foreground">没有识别到说明书章节。</p>
        ) : null}
      </section>

      <section>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-medium">摘要附图与说明书附图</p>
          <span className="text-xs text-muted-foreground">
            共 {numberValue(output.figure_count) ?? figures.length} 幅
          </span>
        </div>
        {figures.length ? (
          <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {figures.map((figure, index) => {
              const assetIndex = numberValue(figure.lab_asset_index);
              const artifactIndex = numberValue(figure.artifact_index);
              const imageUrl = moduleRunId && assetIndex !== null
                ? `/api/test/invalidity/module-figure/${encodeURIComponent(moduleRunId)}/${assetIndex}`
                : attachedSessionId && artifactIndex !== null
                  ? `/api/admin/invalidity/session/${encodeURIComponent(attachedSessionId)}/target-figures/${artifactIndex}`
                  : '';
              return (
                <div key={`${text(figure.figure_id)}-${index}`} className="overflow-hidden rounded-lg border bg-background">
                  {imageUrl ? (
                    <div className="relative aspect-[4/3] bg-white">
                      <Image
                        src={imageUrl}
                        alt={text(figure.figure_id) || `专利附图 ${index + 1}`}
                        fill
                        sizes="(max-width: 640px) 100vw, 33vw"
                        className="object-contain p-2"
                        unoptimized
                      />
                    </div>
                  ) : null}
                  <div className="p-3">
                    <p className="font-medium">{text(figure.figure_id) || `附图 ${index + 1}`}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {text(figure.figure_description) || '原文附图'}
                      {numberValue(figure.page_number)
                        ? ` · PDF 第 ${numberValue(figure.page_number)} 页`
                        : ''}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">当前文件没有提取出附图。</p>
        )}
      </section>

      <section className="rounded border bg-muted/30 p-3 text-xs text-muted-foreground">
        <p>
          解析版本：{text(output.parser_version) || '历史版本未记录'}
          {' · '}
          源文件 SHA-256：{text(output.source_sha256) || text(fallbackPatent.source_sha256) || '未记录'}
        </p>
        <p className="mt-1">
          解析提示：{parserErrors.length ? `${parserErrors.length} 项，详见技术运行记录` : '无'}
        </p>
      </section>
    </div>
  );
}

function MechanismModelDetails({ mechanism }: { mechanism: JsonObject }) {
  const mechanismElements = rows(mechanism.elements);
  return (
    <div className="rounded border border-blue-200 bg-blue-50/60 p-3">
      <p className="text-sm font-medium">系统理解的发明机构</p>
      <p className="mt-1 text-xs text-muted-foreground">
        {text(mechanism.summary) || '按构件角色、相互关系和工作过程理解。'}
      </p>
      <ul className="mt-2 grid gap-2 text-sm md:grid-cols-2">
        {mechanismElements.map((element, index) => (
          <li
            key={text(element.element_id) || String(index)}
            className="rounded bg-background/80 p-2"
          >
            <p className="font-medium">{text(element.text)}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {text(element.kind) === 'component_role'
                ? '构件角色'
                : text(element.kind) === 'topology_relation'
                  ? '连接/位置关系'
                  : text(element.kind) === 'motion_state_relation'
                    ? '运动/状态关系'
                    : text(element.kind) === 'control_relation'
                      ? '控制关系'
                      : '技术作用'}
              {text(element.rationale) ? ` · ${text(element.rationale)}` : ''}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ProfileResultDetails({
  runs,
  fallbackRuns,
}: {
  runs: ChildRun[];
  fallbackRuns?: ChildRun[];
}) {
  const ownPlans = runs.filter(
    (child) => child.ok && child.code === 'I2_INVENTIVE_PROFILE',
  );
  // 自动流程不单独创建画像 run：画像在同一独立权利要求的 I2_QUERY_PLAN
  // 运行中生成并随输出冻结，只读视图以此回退展示。
  const embeddedPlans = (fallbackRuns || []).filter(
    (child) => child.ok
      && child.code === 'I2_QUERY_PLAN'
      && Object.keys(record(child.output.invention_search_profile)).length > 0,
  );
  const plans = ownPlans.length ? ownPlans : embeddedPlans;
  const embedded = !ownPlans.length && embeddedPlans.length > 0;
  return (
    <div className="space-y-4">
      {embedded ? (
        <p className="text-xs text-muted-foreground">
          自动流程的核心发明点画像在模块4检索方案运行中一并生成，以下为该持久化输出中的画像部分。
        </p>
      ) : null}
      {plans.map((child, planIndex) => {
        const profile = record(child.output.invention_search_profile);
        const inventivePoints = rows(profile.inventive_point_features);
        const commonContext = rows(profile.common_context_features);
        const mechanism = record(profile.mechanism_model);
        const limitations = rows(child.output.limitations);
        const limitationByFeatureId = new Map(
          limitations
            .map((item) => [text(item.feature_id), item] as const)
            .filter(([featureId]) => Boolean(featureId)),
        );
        return (
          <div key={`${text(child.output.claim_id)}-${planIndex}`} className="space-y-3 rounded border bg-background p-3">
            <div>
              <p className="font-medium">
                独立权利要求 {text(child.output.claim_id) || planIndex + 1}
              </p>
              <p className="text-sm text-muted-foreground">
                保护客体/类别：
                {text(profile.protected_subject)
                  || text(child.output.technical_subject)
                  || '未识别'}
              </p>
            </div>
            {text(child.output.generation_source) === 'same_source_cached_profile'
            && text(child.output.generation_warning) ? (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>本次复用了同案发明画像</AlertTitle>
                <AlertDescription>
                  {text(child.output.generation_warning)}
                </AlertDescription>
              </Alert>
            ) : null}
            <div className="rounded border border-primary/30 bg-primary/5 p-3">
              <p className="text-sm font-medium">核心发明点总结</p>
              <p className="mt-1 whitespace-pre-wrap text-sm leading-6">
                {text(profile.invention_summary) || '本次运行没有返回核心发明点总结。'}
              </p>
            </div>
            <div>
              <p className="text-sm font-medium">检索级核心发明点（最多 3 项）</p>
              <p className="mt-1 text-xs text-muted-foreground">
                同一技术目的或机构作用链上的细节会归并展示；原始技术特征仍完整列在对应发明点下。
              </p>
              <ul className="mt-2 space-y-2 text-sm">
                {inventivePoints.map((concept, index) => {
                  const featureIds = stringValues(concept.feature_ids);
                  const featureTexts = featureIds.map(
                    (featureId) =>
                      text(limitationByFeatureId.get(featureId)?.text)
                      || featureId,
                  );
                  return (
                    <li
                      key={text(concept.concept_id) || String(index)}
                      className="rounded border bg-muted/30 p-2"
                    >
                      <p className="font-medium">{text(concept.text)}</p>
                      {text(concept.rationale) ? (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {text(concept.rationale)}
                        </p>
                      ) : null}
                      {featureTexts.length ? (
                        <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
                          {featureTexts.map((featureText, featureIndex) => (
                            <li key={`${featureIds[featureIndex]}-${featureIndex}`}>
                              {featureText}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </div>
            <details className="rounded border bg-muted/30 px-3 py-2">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                类别语境与机构工作模型（辅助信息，折叠展示）
              </summary>
              <div className="mt-3 space-y-3">
                <MechanismModelDetails mechanism={mechanism} />
                <div className="rounded border bg-muted/30 p-3">
                  <p className="text-sm font-medium">类别共有的技术语境</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    这些词用于找到同类方案，不因单独出现就判断破坏新颖性。
                  </p>
                  <ul className="mt-2 space-y-1 text-sm">
                    {commonContext.map((concept, index) => (
                      <li key={text(concept.concept_id) || String(index)}>
                        {text(concept.text)}
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </details>
          </div>
        );
      })}
      {!plans.length ? (
        <p className="text-sm text-muted-foreground">本阶段暂无持久化输出。</p>
      ) : null}
    </div>
  );
}

function QueryResultDetails({ runs }: { runs: ChildRun[] }) {
  const plans = runs.filter(
    (child) => child.ok && child.code === 'I2_QUERY_PLAN',
  );
  return (
    <div className="space-y-4">
      {plans.map((child, planIndex) => {
        const profile = record(child.output.invention_search_profile);
        const inventivePoints = rows(profile.inventive_point_features);
        const commonContext = rows(profile.common_context_features);
        const mechanism = record(profile.mechanism_model);
        const queries = rows(child.output.queries);
        const firstRoundPatentQueries = queries.filter(
          (query) =>
            text(query.provider_kind) === 'patent'
            && text(query.date_channel || 'ordinary_prior_art') === 'ordinary_prior_art',
        );
        const usesFixedFiveLineContract =
          firstRoundPatentQueries.length > 0
          && firstRoundPatentQueries.length <= 5
          && firstRoundPatentQueries.every((query) =>
            FIXED_FIRST_ROUND_QUERY_VARIANTS.has(text(query.query_variant)),
          );
        return (
          <div key={`${text(child.output.claim_id)}-${planIndex}`} className="space-y-3 rounded border bg-background p-3">
            <div>
              <p className="font-medium">
                独立权利要求 {text(child.output.claim_id) || planIndex + 1}
              </p>
              <p className="text-sm text-muted-foreground">
                保护客体/类别：
                {text(profile.protected_subject)
                  || text(child.output.technical_subject)
                  || '未识别'}
              </p>
            </div>
            {['module3_profile_live_model', 'same_source_cached_profile'].includes(
              text(child.output.generation_source),
            ) ? (
              <p className="rounded border border-blue-200 bg-blue-50 p-2 text-xs text-blue-900 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-100">
                {text(child.output.generation_source) === 'module3_profile_live_model'
                  ? '本次由大模型基于模块3总结的核心发明点与技术特征分析生成检索关键词；如需更新发明点分析，请重新运行模块3。'
                  : '本次使用模块3总结的核心发明点与技术特征编译检索关键词，未重复调用 GLM；如需更新发明点分析，请重新运行模块3。'}
              </p>
            ) : null}
            {Array.isArray(child.output.portfolio_warnings)
              && child.output.portfolio_warnings.length ? (
                <p className="rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
                  未形成的组合：
                  {' '}
                  {child.output.portfolio_warnings.map(text).filter(Boolean).join('；')}
                </p>
              ) : null}
            <details className="rounded border bg-muted/30 px-3 py-2">
              <summary className="cursor-pointer text-xs text-muted-foreground">
                发明画像（与模块3相同，此处折叠；检索式见下方）
              </summary>
              <div className="mt-3 space-y-3">
                {text(profile.invention_summary) ? (
                  <p className="text-sm">{text(profile.invention_summary)}</p>
                ) : null}
                <MechanismModelDetails mechanism={mechanism} />
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded border border-primary/30 bg-primary/5 p-3">
                    <p className="text-sm font-medium">最能说明发明机构的锚点</p>
                    <ul className="mt-2 space-y-2 text-sm">
                      {inventivePoints.map((concept, index) => (
                        <li key={text(concept.concept_id) || String(index)}>
                          <p className="font-medium">{text(concept.text)}</p>
                          <p className="text-xs text-muted-foreground">
                            {text(concept.rationale)}
                          </p>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="rounded border bg-muted/30 p-3">
                    <p className="text-sm font-medium">类别共有的技术语境</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      这些词用于找到同类方案，不因单独出现就判断破坏新颖性。
                    </p>
                    <ul className="mt-2 space-y-1 text-sm">
                      {commonContext.map((concept, index) => (
                        <li key={text(concept.concept_id) || String(index)}>
                          {text(concept.text)}
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </div>
            </details>
            <div>
              <p className="text-sm font-medium">首轮分线检索方案</p>
              <p className={`mt-1 text-xs ${usesFixedFiveLineContract ? 'text-muted-foreground' : 'text-amber-700'}`}>
                {usesFixedFiveLineContract
                  ? `当前规则：固定五类，实际生成 ${firstRoundPatentQueries.length}/5 条；缺少申请人、分类号或效果词等必要事实时不猜造，因此可能少于 5 条。`
                  : '这是旧规则生成的历史检索方案；请重新运行模块4，首轮才会按固定五类、最多5条生成。'}
              </p>
              <div className="mt-2 space-y-2">
                {queries.map((query, queryIndex) => (
                  <div key={text(query.query_id) || String(queryIndex)} className="rounded bg-muted/50 p-2">
                    <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                      <Badge variant="outline">
                        {text(query.provider_kind) === 'npl' ? '论文/技术资料' : '专利'}
                      </Badge>
                      <Badge variant="secondary">
                        {queryVariantLabel(query.query_variant, query.query_role)}
                      </Badge>
                      <span>
                        范围：
                        {text(query.search_scope) === 'claims'
                          ? '权利要求'
                          : text(query.search_scope) === 'title_abstract'
                            ? '标题和摘要'
                            : '专利全文'}
                      </span>
                    </div>
                    <p className="mt-1 break-words font-mono text-sm">
                      {text(query.provider_expression) || text(query.expression)}
                    </p>
                    {text(query.scope_reason) ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        为什么选这个范围：{text(query.scope_reason)}
                      </p>
                    ) : null}
                    {text(query.rationale) ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        检索思路：{text(query.rationale)}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function EvidenceResultDetails({ runs }: { runs: ChildRun[] }) {
  const searches = runs.filter((child) =>
    ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(child.code),
  );
  const candidateFilter = runs.find(
    (child) => child.ok && child.code === 'I3_CANDIDATE_FILTER',
  );
  const rawCandidateLocations = new Map(
    rows(candidateFilter?.output.raw_candidates)
      .map((candidate) => [
        `${text(candidate.search_run_id)}:${numberValue(candidate.source_rank) ?? 0}`,
        candidate,
      ] as const),
  );
  const duplicateCandidates = new Map(
    rows(candidateFilter?.output.duplicate_candidates)
      .map((candidate) => [text(candidate.candidate_id), candidate] as const)
      .filter(([candidateId]) => Boolean(candidateId)),
  );
  const excludedCandidates = new Map(
    rows(candidateFilter?.output.excluded_candidates)
      .map((candidate) => [text(candidate.candidate_id), candidate] as const)
      .filter(([candidateId]) => Boolean(candidateId)),
  );
  const retainedCandidateIds = new Set(
    rows(candidateFilter?.output.fetch_candidates)
      .map((candidate) => text(candidate.candidate_id))
      .filter(Boolean),
  );
  const fetchRuns = runs.filter((child) => child.code === 'I3_FETCH');
  // I0 自动流程的 I3_FETCH/I3_QUALIFY 输出是 {records:[...]} 批次形状，
  // 需要展开成逐文献记录；实验室 run 的输出本身就是单篇文献。
  const fetched = runs
    .filter((child) => child.ok && child.code === 'I3_FETCH')
    .flatMap((child) => {
      if (isAnalysisReady(child.output)) return [child.output];
      return rows(child.output.records).filter(isAnalysisReady);
    });
  const qualified = runs
    .filter((child) => child.ok && child.code === 'I3_QUALIFY')
    .flatMap((child) => {
      if (evidenceDocumentKey(child.output)) return [child.output];
      return rows(child.output.records).filter(
        (item) => documentJoinKeys(item).length > 0,
      );
    });
  const fetchedKeys = new Set(fetched.flatMap(documentJoinKeys));
  const fetchedDocuments = new Map<string, JsonObject>();
  fetched.forEach((document) => registerDocumentMap(fetchedDocuments, document, document));
  const fetchAttempts = new Map<string, ChildRun>();
  fetchRuns.forEach((child) => {
    const runRecords = rows(child.output.records);
    if (!runRecords.length) {
      const key = childDocumentKey(child);
      if (key) fetchAttempts.set(key, child);
      return;
    }
    runRecords.forEach((item) => {
      const pseudo: ChildRun = {
        ...child,
        output: item,
        documentId: evidenceDocumentKey(item) || child.documentId,
      };
      registerDocumentMap(fetchAttempts, item, pseudo);
    });
  });
  const qualifications = new Map<string, JsonObject>();
  qualified.forEach((document) =>
    registerDocumentMap(qualifications, document, record(document.eligibility)),
  );
  return (
    <div className="space-y-4">
      {candidateFilter ? (
        <div className="rounded border border-blue-200 bg-blue-50/60 p-3 dark:border-blue-900 dark:bg-blue-950/20">
          <p className="font-medium">取全文前已先清理明显错误候选</p>
          <p className="mt-1 text-sm text-muted-foreground">
            原始 {numberValue(candidateFilter.output.raw_candidate_count) ?? 0} 条；
            合并同申请、同公开版本或可靠同族
            {' '}{numberValue(candidateFilter.output.duplicate_count) ?? 0} 条；
            排除明显跨领域 {numberValue(candidateFilter.output.excluded_count) ?? 0} 条；
            实际取全文 {numberValue(candidateFilter.output.fetch_candidate_count) ?? 0} 篇，
            预计减少 {numberValue(candidateFilter.output.estimated_fetch_requests_saved) ?? 0} 次取文。
          </p>
          {candidateFilter.output.model_failure ? (
            <p className="mt-1 text-xs text-amber-700">
              语义判断本次不可用，系统已自动保留所有未确定候选，没有因模型失败而漏检。
            </p>
          ) : null}
        </div>
      ) : null}
      {searches.map((child, searchIndex) => {
        const outputDocuments = rows(child.output.documents);
        // I0 自动流程的检索 run 输出命中列表字段是 records。
        const documents = outputDocuments.length
          ? outputDocuments
          : rows(child.output.records);
        const query = text(child.output.query_text) || queryInputText(child);
        const providerErrors = providerErrorMessages(child);
        return (
          <div key={`${child.code}-${searchIndex}`} className="space-y-3 rounded border bg-background p-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={child.ok ? 'outline' : 'destructive'}>
                {child.code === 'I3_PATENT_SEARCH' ? '专利库' : '论文/标准/技术资料'}
              </Badge>
              <span className="text-sm">
                {child.ok ? `返回 ${documents.length} 条` : '检索失败'}
              </span>
              {text(child.output.query_strategy) ? (
                <Badge variant="secondary">
                  {text(child.output.query_strategy) === 'compact-fallback'
                    ? '收敛查询'
                    : '首轮查询'}
                </Badge>
              ) : null}
            </div>
            <div>
              <p className="text-xs text-muted-foreground">实际提交的检索式</p>
              <p className="mt-1 break-words rounded bg-muted/50 p-2 font-mono text-sm">
                {query || '没有可执行检索式'}
              </p>
            </div>
            {documents.length === 0 ? (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>本次查询没有候选结果</AlertTitle>
                <AlertDescription>
                  {text(child.output.zero_result_reason)
                    || friendlyError(child.error || '检索源返回 0 条。')}
                  {' '}0 条只说明这条查询未命中，不表示不存在现有技术。
                </AlertDescription>
              </Alert>
            ) : (
              <div className="space-y-2">
                {documents.map((document, index) => {
                  const key = evidenceDocumentKey(document);
                  const rawCandidate = rawCandidateLocations.get(
                    `${child.moduleRunId || ''}:${index + 1}`,
                  );
                  const candidateId = text(rawCandidate?.candidate_id);
                  const duplicate = duplicateCandidates.get(candidateId);
                  const excluded = excludedCandidates.get(candidateId);
                  const retained = retainedCandidateIds.has(candidateId);
                  const eligibility = lookupDocumentMap(qualifications, document);
                  const dateClassification = invalidityDateClassification(eligibility);
                  const fetchAttempt = lookupDocumentMap(fetchAttempts, document);
                  const hasFullText = documentJoinKeys(document).some((item) => fetchedKeys.has(item))
                    || Boolean(
                      fetchAttempt?.ok
                      && isAnalysisReady(fetchAttempt.output),
                    );
                  const hasSourceFile = Boolean(
                    fetchAttempt?.ok
                    && isRetrievedEvidence(fetchAttempt.output),
                  );
                  const fetchedDocument = lookupDocumentMap(fetchedDocuments, document)
                    || (hasSourceFile ? fetchAttempt?.output : undefined);
                  const retrievalProvider = text(
                    record(fetchedDocument?.provenance).retrieval_provider,
                  ) || text(fetchedDocument?.provider);
                  const hasFullTextRoute = hasDirectFullTextRoute(document);
                  const fetchFailed = Boolean(
                    fetchAttempt
                    && (
                      !fetchAttempt.ok
                      || !isRetrievedEvidence(fetchAttempt.output)
                      || !isAnalysisReady(fetchAttempt.output)
                    ),
                  );
                  return (
                    <div key={`${key}-${index}`} className="rounded border p-3">
                      <div className="flex flex-wrap items-center gap-2">
                        <p className="font-medium">
                          {text(document.publication_number) || key || `候选 ${index + 1}`}
                          {text(document.title) ? ` · ${text(document.title)}` : ''}
                        </p>
                        <Badge
                          variant={
                            hasFullText
                              ? 'outline'
                              : fetchFailed
                                ? 'destructive'
                                : excluded || duplicate
                                  ? 'secondary'
                                  : 'secondary'
                          }
                        >
                          {hasFullText
                            ? '全文已读完，可比对'
                            : excluded
                              ? '明显无关，未取文'
                              : duplicate
                                ? '同申请/同版本，已合并'
                            : hasSourceFile
                              ? '原文件已取得，暂不可比对'
                            : fetchFailed
                              ? '取文失败'
                              : fetchAttempt
                                ? '未取得全文'
                                : '仅检索线索（等待取文）'}
                        </Badge>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        发现来源 {text(document.provider) || '未知'}
                        {hasFullText && retrievalProvider
                          ? ` · 取文来源 ${retrievalProvider}`
                          : ''}
                        {text(document.publication_date)
                          ? ` · 公开日 ${text(document.publication_date)}`
                          : ' · 公开日待核验'}
                        {text(document.source_type)
                          ? ` · ${text(document.source_type)}`
                          : ''}
                      </p>
                      {fetchedDocument ? (
                        <p className="mt-1 text-xs text-muted-foreground">
                          可读格式：
                          {text(record(fetchedDocument.readable_document).source_kind)
                            || '尚未生成'}
                          {numberValue(record(fetchedDocument.readable_document).page_count) !== null
                            ? ` · 已处理 ${numberValue(record(fetchedDocument.readable_document).processed_page_count) ?? 0}/${numberValue(record(fetchedDocument.readable_document).page_count)} 页`
                            : ''}
                          {(numberValue(record(fetchedDocument.readable_document).ocr_page_count) || 0) > 0
                            ? ` · OCR ${numberValue(record(fetchedDocument.readable_document).ocr_page_count)} 页`
                            : ''}
                        </p>
                      ) : null}
                      {!hasFullText ? (
                        <p className="mt-2 text-sm">
                          {excluded
                            ? `取文前题录技术语境已足以确认其明显不属于目标技术领域：${text(excluded.filter_reason) || '明显跨领域且没有相近作用机理'}。原始命中仍保留在本页供复核。`
                            : duplicate
                              ? `该记录与保留代表文献属于同一申请、同一公开文本版本或可靠同族，已合并到 ${text(duplicate.merged_into_candidate_id) || '代表文献'}，不重复取全文。`
                            : hasSourceFile
                            ? `原文件已经冻结，但尚未达到全文可读门槛：${text(fetchAttempt?.output.analysis_readiness_reason) || '结构化/OCR未完成'}。模块6不会用标题或摘要代替全文。`
                            : fetchFailed
                            ? `系统已经尝试取得该候选全文但没有成功：${friendlyError(fetchAttempt?.error || '未取得可核验原文')}。该文献不会被伪装成已分析。`
                            : hasFullTextRoute
                              ? '已有全文地址并已进入清理后的取文队列；在取得文件和哈希前仍只是检索线索。'
                              : retained
                                ? '该候选已通过取文前清理并进入取文队列；未取得可核验原文前，不能判断它是否公开了权利要求技术特征。'
                                : '该候选尚未完成取文前清理。'}
                        </p>
                      ) : null}
                      <div className="mt-2 flex flex-wrap items-start gap-2">
                        <Badge
                          variant="outline"
                          className={
                            dateClassification.label === '现有技术'
                              ? 'border-emerald-300 bg-emerald-50 text-emerald-800'
                              : dateClassification.label === '抵触申请'
                                ? 'border-blue-300 bg-blue-50 text-blue-800'
                                : dateClassification.label === '非现有技术'
                                  ? 'border-slate-300 bg-slate-100 text-slate-700'
                                  : 'border-amber-300 bg-amber-50 text-amber-800'
                          }
                        >
                          {dateClassification.label}
                        </Badge>
                        <p className="min-w-0 flex-1 text-sm text-muted-foreground">
                          {dateClassification.reason}
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {providerErrors.length ? (
              <ul className="list-disc pl-5 text-xs text-amber-700">
                {providerErrors.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            ) : null}
          </div>
        );
      })}
      {fetchRuns.some(
        (child) => !child.ok || !isAnalysisReady(child.output),
      ) ? (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>部分候选尚未形成可比对全文</AlertTitle>
          <AlertDescription>
            每次检索命中的前 10 条原始结果均保留；同申请/同版本只取一个代表，
            明显跨领域候选不取文，其余保留候选全部进入取文。失败项逐篇保留原因，
            不会因其他候选成功而被隐藏。
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}

function AnalysisResultDetails({ runs }: { runs: ChildRun[] }) {
  const comparisonRuns = runs.filter(
    (child) => child.code === 'I4_S_SINGLE_REFERENCE',
  );
  const comparisons = comparisonRuns.filter((child) => child.ok);
  const pendingComparisons = comparisonRuns.filter(
    (child) => !child.ok && !TERMINAL_RUN.has(child.status),
  );
  const failedComparisons = comparisonRuns.filter(
    (child) => !child.ok && TERMINAL_RUN.has(child.status),
  );
  const closest = runs.find(
    (child) => child.ok && child.code === 'I4_C_CLOSEST_PRIOR_ART',
  );
  const inventive = runs.find(
    (child) => child.ok && child.code === 'I4_I_INVENTIVE_STEP',
  );
  const normalizedLargeChartIds = Array.isArray(
    closest?.output.large_claim_chart_document_ids,
  )
    ? (closest?.output.large_claim_chart_document_ids as unknown[])
        .map((item) => text(item))
        .filter(Boolean)
    : [];
  const largeChartComparisons = normalizedLargeChartIds
    .map((documentId) => comparisons.find((child) => (
      text(child.output.document_id) === documentId
      || text(child.output.publication_number) === documentId
      || child.documentId === documentId
    )))
    .filter((child): child is ChildRun => Boolean(child));
  const largeChartFeatures = Array.from(
    new Map(
      largeChartComparisons.flatMap((child) =>
        rows(child.output.disclosures).map((item) => [text(item.feature_id), item] as const),
      ),
    ).values(),
  );
  return (
    <div className="space-y-4">
      {comparisonRuns.length ? (
        <Alert variant={failedComparisons.length ? 'destructive' : 'default'}>
          {failedComparisons.length ? (
            <AlertTriangle className="h-4 w-4" />
          ) : (
            <CheckCircle2 className="h-4 w-4" />
          )}
          <AlertTitle>
            逐篇分析进度：{comparisons.length}/{comparisonRuns.length} 份已有可用结果
          </AlertTitle>
          <AlertDescription>
            每份已取得全文的对比文件都单独分析；当前另有 {pendingComparisons.length} 份排队或执行中、
            {failedComparisons.length} 份已终态失败。一份成功不会代替或掩盖其他文献。
          </AlertDescription>
        </Alert>
      ) : null}
      {comparisons.length ? (
        <Accordion
          type="multiple"
          className="space-y-2"
          data-module6-document-disclosures
        >
          {comparisons.map((child, comparisonIndex) => {
            const disclosures = rows(child.output.disclosures);
            const counts = disclosureSummary(disclosures);
            const documentNumber = text(child.output.publication_number)
              || text(child.output.document_id)
              || child.documentId
              || '未编号';
            const documentTitle = text(child.output.document_title);
            const documentKey = child.moduleRunId
              || text(child.output.document_id)
              || child.documentId
              || `comparison-${comparisonIndex}`;
            return (
              <AccordionItem
                key={documentKey}
                value={documentKey}
                className="rounded-lg border bg-background px-3"
                data-module6-document-disclosure
              >
                <AccordionTrigger className="gap-3 py-3 text-left hover:no-underline">
                  <span className="flex min-w-0 flex-1 flex-col gap-2 pr-2">
                    <span className="font-medium">
                      文献 {documentNumber}{documentTitle ? `《${documentTitle}》` : ''}
                    </span>
                    <span className="flex flex-wrap gap-1.5 text-xs font-normal text-muted-foreground">
                      <Badge variant="outline">共 {disclosures.length} 项</Badge>
                      <Badge variant="outline" className="border-emerald-300 bg-emerald-50 text-emerald-800">
                        披露 {counts.disclosed}
                      </Badge>
                      <Badge variant="outline" className="border-slate-300 bg-slate-100 text-slate-700">
                        未披露 {counts.notDisclosed}
                      </Badge>
                      <Badge variant="outline" className="border-amber-300 bg-amber-50 text-amber-800">
                        待确认 {counts.uncertain}
                      </Badge>
                      {counts.failed ? (
                        <Badge variant="outline" className="border-red-300 bg-red-50 text-red-800">
                          分析失败 {counts.failed}
                        </Badge>
                      ) : null}
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent className="space-y-3 pb-4">
                  <p className="text-xs text-muted-foreground">
                    排队 {typeof child.queueSeconds === 'number' ? `${child.queueSeconds.toFixed(1)} 秒` : '未记录'}
                    {' · '}执行 {typeof child.executionSeconds === 'number' ? `${child.executionSeconds.toFixed(1)} 秒` : '未记录'}
                  </p>
                  {child.complete === false ? (
                    <Alert variant="destructive">
                      <AlertTriangle className="h-4 w-4" />
                      <AlertTitle>本篇为部分分析结果</AlertTitle>
                      <AlertDescription>
                        {child.error || '部分技术特征尚未形成可用输出；已完成的逐项结果保留展示，但不会进入整体聚合。'}
                      </AlertDescription>
                    </Alert>
                  ) : null}
                  {child.output.structural_review_attempted === true ? (
                    <p className="text-xs text-muted-foreground">
                      {text(child.output.structural_review_status) === 'completed'
                        ? `已完成第二遍整体结构复核（共 ${Number(child.output.analysis_pass_count) || 2} 遍）`
                        : '整体结构复核状态已记录'}
                      {text(child.output.analysis_rule_version)
                        ? ` · 规则 ${text(child.output.analysis_rule_version)}`
                        : ''}
                    </p>
                  ) : null}
                  {(text(child.output.target_mechanism_summary)
                    || text(child.output.reference_mechanism_summary)) ? (
                    <details
                      className="rounded border bg-muted/20 p-3 text-sm"
                      data-module6-mechanism-disclosure
                    >
                      <summary className="cursor-pointer font-medium">
                        展开整体机构对照
                      </summary>
                      <div className="mt-3 grid gap-2 md:grid-cols-2">
                        <div className="rounded bg-background p-2">
                          <p className="text-xs font-medium text-muted-foreground">目标专利机构</p>
                          <p className="mt-1">{text(child.output.target_mechanism_summary) || '—'}</p>
                        </div>
                        <div className="rounded bg-background p-2">
                          <p className="text-xs font-medium text-muted-foreground">对比文件机构</p>
                          <p className="mt-1">{text(child.output.reference_mechanism_summary) || '—'}</p>
                        </div>
                      </div>
                    </details>
                  ) : null}
                  <div className="space-y-2">
                    <div>
                      <p className="font-medium">逐项技术特征评价（{disclosures.length} 项）</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        每项默认收起；点击技术特征可查看原文、位置、结构映射和分析理由。
                      </p>
                    </div>
                    <Accordion
                      type="multiple"
                      className="rounded border px-3"
                      data-module6-feature-disclosures
                    >
                      {disclosures.map((item, disclosureIndex) => {
                        const featureId = text(item.feature_id) || `feature-${disclosureIndex + 1}`;
                        const featureText = text(item.feature_text)
                          || '旧结果未保存该权利要求特征原文';
                        return (
                          <AccordionItem
                            key={`${documentKey}-${featureId}-${disclosureIndex}`}
                            value={`${documentKey}-${featureId}-${disclosureIndex}`}
                            data-module6-feature-disclosure
                          >
                            <AccordionTrigger className="gap-3 py-3 text-left hover:no-underline">
                              <span className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-2 pr-2">
                                <span className="min-w-0 flex-1">
                                  <span className="block text-xs font-normal text-muted-foreground">
                                    技术特征 {disclosureIndex + 1}
                                  </span>
                                  <span className="mt-0.5 block break-words font-medium">
                                    {featureText}
                                  </span>
                                </span>
                                <Badge
                                  variant="outline"
                                  className={disclosureBadgeClass(item.status)}
                                >
                                  {disclosureLabel(item.status)}
                                </Badge>
                              </span>
                            </AccordionTrigger>
                            <AccordionContent className="pb-4">
                              <div className="grid gap-3 text-sm md:grid-cols-2">
                                <div className="rounded border bg-muted/20 p-3">
                                  <p className="text-xs font-medium text-muted-foreground">对比文件原文</p>
                                  <p className="mt-1 whitespace-pre-wrap break-words">
                                    {text(item.evidence_quote) || '无可核验原文摘录'}
                                  </p>
                                </div>
                                <div className="rounded border bg-muted/20 p-3">
                                  <p className="text-xs font-medium text-muted-foreground">原文位置</p>
                                  <p className="mt-1 whitespace-pre-wrap break-words">
                                    {text(item.evidence_location) || '未记录位置'}
                                  </p>
                                </div>
                                <div className="rounded border bg-muted/20 p-3 md:col-span-2">
                                  <p className="text-xs font-medium text-muted-foreground">分析理由</p>
                                  <p className="mt-1 whitespace-pre-wrap break-words">
                                    {text(item.reasoning) || '未记录分析理由'}
                                  </p>
                                </div>
                                <div className="rounded border bg-muted/20 p-3 md:col-span-2">
                                  <p className="mb-2 text-xs font-medium text-muted-foreground">结构角色与映射</p>
                                  <StructureMappingDetails disclosure={item} />
                                </div>
                              </div>
                            </AccordionContent>
                          </AccordionItem>
                        );
                      })}
                    </Accordion>
                  </div>
                </AccordionContent>
              </AccordionItem>
            );
          })}
        </Accordion>
      ) : null}
      {pendingComparisons.map((child, index) => (
        <Alert key={`${child.documentId || childDocumentKey(child) || 'pending'}-${index}`}>
          <CircleDashed className="h-4 w-4" />
          <AlertTitle>
            文献 {child.documentId || childDocumentKey(child) || '未编号'} {statusLabel(child.status)}
          </AlertTitle>
          <AlertDescription>
            排队 {typeof child.queueSeconds === 'number' ? `${child.queueSeconds.toFixed(1)} 秒` : '计时中'}
            {typeof child.executionSeconds === 'number'
              ? `；实际执行 ${child.executionSeconds.toFixed(1)} 秒。`
              : '；尚未开始实际执行。'}
            已完成的其他文献结果会继续保留。
          </AlertDescription>
        </Alert>
      ))}
      {failedComparisons.map((child, index) => (
        <Alert
          key={`${child.documentId || childDocumentKey(child) || 'failed'}-${index}`}
          variant="destructive"
        >
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>
            文献 {child.documentId || childDocumentKey(child) || '未编号'} 分析失败
          </AlertTitle>
          <AlertDescription>
            {friendlyError(child.error || statusLabel(child.status))}
            {child.pipelineAttempts && child.pipelineAttempts > 1
              ? `；系统已自动发起 ${child.pipelineAttempts} 轮分析。`
              : ''}
          </AlertDescription>
        </Alert>
      ))}
      {closest ? (
        <Alert>
          <CheckCircle2 className="h-4 w-4" />
          <AlertTitle>
            最接近现有技术：{text(closest.output.document_id) || '已选出'}
          </AlertTitle>
          <AlertDescription>
            {text(closest.output.selection_reason) || '按明确披露数量、技术领域和证据完整度选择。'}
          </AlertDescription>
        </Alert>
      ) : null}
      {closest && largeChartComparisons.length ? (
        <div className="rounded border bg-background p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <p className="font-medium">大 Claim Chart</p>
              <p className="mt-1 text-xs text-muted-foreground">
                以全部权利要求特征为行；展示 D1 及对区别特征新增覆盖最高的前 5 篇日期合格文献。
              </p>
            </div>
            <Badge variant="outline">
              D1 + {Math.max(0, largeChartComparisons.length - 1)} 篇
            </Badge>
          </div>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[980px] border-collapse text-left text-sm">
              <thead>
                <tr className="bg-muted/50">
                  <th className="sticky left-0 z-10 min-w-72 border bg-muted/50 p-2">
                    权利要求技术特征
                  </th>
                  {largeChartComparisons.map((child, index) => (
                    <th
                      key={text(child.output.document_id) || index}
                      className="min-w-56 border p-2"
                    >
                      {index === 0 ? 'D1 · ' : ''}
                      {text(child.output.publication_number)
                        || text(child.output.document_id)
                        || child.documentId
                        || `文献 ${index + 1}`}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {largeChartFeatures.map((feature) => (
                  <tr key={text(feature.feature_id)}>
                    <td className="sticky left-0 border bg-background p-2">
                      <p className="font-medium">{text(feature.feature_id)}</p>
                      <p className="mt-1 text-muted-foreground">
                        {text(feature.feature_text) || '—'}
                      </p>
                    </td>
                    {largeChartComparisons.map((child, index) => {
                      const disclosure = rows(child.output.disclosures).find(
                        (item) => text(item.feature_id) === text(feature.feature_id),
                      );
                      return (
                        <td
                          key={`${text(child.output.document_id) || index}-${text(feature.feature_id)}`}
                          className="border p-2 align-top"
                        >
                          <p className="font-medium">
                            {disclosureLabel(disclosure?.status)}
                          </p>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {text(disclosure?.evidence_quote) || '无可核验原文摘录'}
                          </p>
                          {text(disclosure?.evidence_location) ? (
                            <p className="mt-1 text-xs">{text(disclosure?.evidence_location)}</p>
                          ) : null}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
      {inventive ? (
        <div className="rounded border bg-background p-3">
          <p className="font-medium">创造性组合评价</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {inventive.output.evidence_complete === true
              ? '组合证据链完整，可以进入律师复核。'
              : '现有资料组合仍有证据缺口，不能据此直接作出创造性结论。'}
          </p>
          {rows(inventive.output.gaps).length ? (
            <ul className="mt-2 list-disc pl-5 text-sm">
              {rows(inventive.output.gaps).map((gap, index) => (
                <li key={index}>
                  {inventiveGapLabel(gap)}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      {!comparisons.length ? (
        <Alert>
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>没有可作逐项对比的全文</AlertTitle>
          <AlertDescription>
            搜索候选必须先取得可核验原文；只有题录或摘要时，系统不会猜测技术特征是否被披露。
          </AlertDescription>
        </Alert>
      ) : null}
    </div>
  );
}

function ClosestPriorArtResultDetails({ runs }: { runs: ChildRun[] }) {
  const closest = runs.find(
    (child) => child.ok && child.code === 'I4_C_CLOSEST_PRIOR_ART',
  );
  if (!closest) {
    return (
      <Alert>
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>尚未选出 D1</AlertTitle>
        <AlertDescription>请先完成日期合格文献的单篇新颖性比对。</AlertDescription>
      </Alert>
    );
  }
  const differences = rows(closest.output.distinguishing_features);
  const chartDocumentIds = stringValues(closest.output.large_claim_chart_document_ids);
  return (
    <div className="space-y-4">
      <Alert>
        <CheckCircle2 className="h-4 w-4" />
        <AlertTitle>最接近现有技术：{text(closest.output.document_id) || '已选出'}</AlertTitle>
        <AlertDescription>
          {text(closest.output.selection_reason) || '按确认披露数量优先，并结合技术领域、结构作用与证据完整度选择。'}
        </AlertDescription>
      </Alert>
      <div className="rounded border bg-background p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="font-medium">冻结区别特征</p>
          <Badge variant="outline">{differences.length} 项</Badge>
        </div>
        {differences.length ? (
          <div className="mt-3 space-y-2">
            {differences.map((item, index) => (
              <div key={text(item.feature_id) || index} className="rounded border p-3 text-sm">
                <p className="font-medium">
                  {text(item.feature_id) || `区别特征 ${index + 1}`} · {text(item.feature_text) || text(item.target_feature_text) || '未保存特征原文'}
                </p>
                <p className="mt-1 text-muted-foreground">
                  结构角色/关系：{text(item.structural_role) || text(item.structure_role) || text(item.relationship) || '待复核'}
                </p>
                <p className="mt-1 text-muted-foreground">
                  技术作用：{text(item.technical_effect) || text(item.technical_role) || '待复核'}
                </p>
                {text(item.rationale) || text(item.d1_non_disclosure_reason) ? (
                  <p className="mt-1">D1 未披露依据：{text(item.rationale) || text(item.d1_non_disclosure_reason)}</p>
                ) : null}
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">D1 已覆盖全部确认特征，未形成区别特征。</p>
        )}
      </div>
      {chartDocumentIds.length ? (
        <p className="text-sm text-muted-foreground">
          大 Claim Chart 候选列：{chartDocumentIds.join('、')}。
        </p>
      ) : null}
    </div>
  );
}

function precheckStatusLabel(value: unknown): string {
  const labels: Record<string, string> = {
    supported: '支持',
    not_supported: '不支持',
    uncertain: '待确认',
    supported_by_citable_evidence: '已有可引用依据',
    preliminary_candidate: '惯用手段候选，需补证',
    absent: '未见反向教导',
    present: '存在反向教导',
    predictable: '效果可预期',
    unexpected: '存在预料不到效果',
  };
  return labels[text(value)] || text(value) || '待确认';
}

function precheckRouteLabel(value: unknown): string {
  const labels: Record<string, string> = {
    skip_structural_gap_search: '不再检索同一结构',
    search_common_knowledge_evidence: '仅补公知常识证据',
    search_combination_evidence: '补检组合启示证据',
    search_direct_feature_evidence: '继续检索区别特征结构',
    human_review: '转律师人工复核',
  };
  return labels[text(value)] || text(value) || '待分流';
}

function ObviousnessPrecheckResultDetails({ runs }: { runs: ChildRun[] }) {
  const child = runs.find(
    (item) => item.ok && item.code === 'I4_O_OBVIOUSNESS_PRECHECK',
  );
  if (!child) {
    return (
      <Alert>
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>尚未形成显而易见性预分析</AlertTitle>
        <AlertDescription>请先选择 D1 并冻结区别特征。</AlertDescription>
      </Alert>
    );
  }
  const groups = rows(child.output.feature_groups);
  return (
    <div className="space-y-4">
      <Alert>
        <Lightbulb className="h-4 w-4" />
        <AlertTitle>已完成 {groups.length} 组区别特征预分析</AlertTitle>
        <AlertDescription>
          区别特征身份保持不变；这里只决定创造性分析和下一轮补证路线。
        </AlertDescription>
      </Alert>
      {groups.map((group, index) => {
        const d1 = record(group.d1_teaching);
        const routine = record(group.routine_means);
        const motivation = record(group.modification_motivation);
        const away = record(group.teaching_away);
        const effect = record(group.technical_effect);
        return (
          <div key={text(group.feature_group_id) || index} className="rounded border bg-background p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-medium">
                  {text(group.feature_group_id) || `特征组 ${index + 1}`} · {stringValues(group.feature_ids).join('、')}
                </p>
                <p className="mt-1 text-sm">{stringValues(group.feature_texts).join('；') || '未保存特征原文'}</p>
              </div>
              <Badge variant="outline">{precheckRouteLabel(group.search_route)}</Badge>
            </div>
            <p className="mt-3 text-sm"><span className="font-medium">客观技术问题：</span>{text(group.objective_technical_problem) || '待确认'}</p>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {[
                ['D1 技术启示', d1],
                ['本领域惯用手段', routine],
                ['修改动机', motivation],
                ['反向教导', away],
                ['技术效果', effect],
              ].map(([label, criterion]) => {
                const value = criterion as JsonObject;
                const d1PathNote = label === 'D1 技术启示'
                  ? (group.d1_teaching_path_complete === true
                      ? ' · 已足以形成独立改造路径'
                      : ' · 仅表示存在启示，仍需结合具体实现路径判断')
                  : '';
                return (
                  <div key={String(label)} className="rounded bg-muted/40 p-3 text-sm">
                    <p className="font-medium">{String(label)}：{precheckStatusLabel(value.status)}{d1PathNote}</p>
                    <p className="mt-1 text-muted-foreground">{text(value.reasoning) || '未给出理由'}</p>
                    {text(value.evidence_quote) ? <p className="mt-1">引文：{text(value.evidence_quote)}</p> : null}
                    {text(value.evidence_location) ? <p className="mt-1 text-xs">位置：{text(value.evidence_location)}</p> : null}
                  </div>
                );
              })}
            </div>
            {text(group.modification_path) ? (
              <p className="mt-3 text-sm"><span className="font-medium">可执行修改路径：</span>{text(group.modification_path)}</p>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function GapQueryMatrixDetails({ batch }: { batch: JsonObject }) {
  const matrix = record(batch.gap_query_matrix);
  const matrixRounds = rows(matrix.rounds);
  const matrixStatus = text(matrix.status) || 'planning';
  const binding = text(matrix.binding_sha256);
  const statusLabel = (status: string) => ({
    submitted: '已提交检索',
    skipped_feature_resolved: '前轮已解决，自动跳过',
    not_executed: '本轮未执行',
    pending: '等待执行',
  }[status] || status || '等待执行');
  return (
    <div className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium">五轮检索词矩阵</p>
          <p className="mt-1 text-sm text-muted-foreground">
            五轮检索词在开始检索前一次生成并冻结；后续按钮只执行下一轮，不再临时重写检索词。
          </p>
        </div>
        <Badge variant={matrixStatus === 'ready' ? 'outline' : 'secondary'}>
          {matrixStatus === 'ready' ? '五轮已冻结' : '正在生成五轮'}
        </Badge>
      </div>
      {binding ? (
        <p className="text-xs text-muted-foreground">
          矩阵版本 v{numberValue(matrix.version) || 1} · 绑定摘要 {binding.slice(0, 12)}…
        </p>
      ) : null}
      {matrixRounds.length ? (
        <Accordion type="multiple" className="rounded border bg-background px-3">
          {matrixRounds.map((matrixRound, index) => {
            const iteration = numberValue(matrixRound.iteration) || index + 1;
            const queries = rows(matrixRound.queries);
            const skipped = queries.filter(
              (query) => text(query.execution_status) === 'skipped_feature_resolved',
            ).length;
            return (
              <AccordionItem key={iteration} value={`matrix-round-${iteration}`}>
                <AccordionTrigger className="gap-3 text-left">
                  <span className="flex flex-1 flex-wrap items-center justify-between gap-2 pr-2">
                    <span>
                      第 {iteration} 轮：{text(matrixRound.strategy_label)
                        || MODULE9_GAP_STRATEGIES[iteration - 1]?.replace(/^第\d轮：/, '')
                        || '已冻结检索策略'}
                    </span>
                    <span className="flex gap-2">
                      {skipped ? <Badge variant="secondary">跳过 {skipped} 组</Badge> : null}
                      <Badge variant="outline">{queries.length} 组</Badge>
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent className="space-y-3">
                  {text(matrixRound.strategy_description) ? (
                    <p className="text-sm text-muted-foreground">
                      {text(matrixRound.strategy_description)}
                    </p>
                  ) : null}
                  {queries.map((query, queryIndex) => {
                    const executionStatus = text(query.execution_status);
                    return (
                      <div
                        key={text(query.query_id) || queryIndex}
                        className="rounded border bg-muted/20 p-3"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <p className="font-medium">
                            区别特征 {queryIndex + 1}
                            {text(query.feature_text) ? `：${text(query.feature_text)}` : ''}
                          </p>
                          <Badge variant={executionStatus === 'skipped_feature_resolved' ? 'secondary' : 'outline'}>
                            {statusLabel(executionStatus)}
                          </Badge>
                        </div>
                        <p className="mt-2 break-words font-mono text-sm">
                          {text(query.expression) || '尚在生成'}
                        </p>
                      </div>
                    );
                  })}
                  {!queries.length ? (
                    <p className="text-sm text-muted-foreground">本轮检索词尚在生成。</p>
                  ) : null}
                </AccordionContent>
              </AccordionItem>
            );
          })}
        </Accordion>
      ) : (
        <p className="text-sm text-muted-foreground">正在生成并校验五轮检索词矩阵。</p>
      )}
    </div>
  );
}

function GapSearchResultDetails({
  runs,
  batch,
}: {
  runs: ChildRun[];
  batch: JsonObject;
}) {
  const currentAttempts = runs.filter((child) => child.isCurrentAttempt !== false);
  const iterations = rows(batch.rounds)
    .map((item) => numberValue(item.iteration))
    .filter((value): value is number => typeof value === 'number')
    .sort((left, right) => left - right);
  const currentIteration = numberValue(batch.current_iteration)
    ?? iterations.at(-1)
    ?? 1;
  const inheritedIteration = numberValue(batch.inherited_latest_iteration) || 0;
  const currentRound = record(batch.current_round);
  const comparisonCount = numberValue(currentRound.comparison_count) || 0;
  const successfulComparisonCount = numberValue(
    currentRound.successful_comparison_count,
  ) || 0;
  const batchStatus = text(batch.status);
  const canContinue = batch.can_continue === true;
  const continuationReason = text(batch.continuation_reason);
  const guide = (
    <Alert className="border-primary/40 bg-primary/5">
      <FileSearch className="h-4 w-4" />
      <AlertTitle>
        本批次实际执行第 {currentIteration}/5 个 gap 轮
      </AlertTitle>
      <AlertDescription className="space-y-2">
        {inheritedIteration > 0 ? (
          <p>
            创建本批次时，该案件已有第 1—{inheritedIteration} 轮历史记录，因此本次从第{' '}
            {inheritedIteration + 1} 轮继续，并不是新的第 1 轮。
          </p>
        ) : (
          <p>这是本批次从第 1 轮开始的补证检索。</p>
        )}
        <p>
          本轮 I4-S 逐篇比对已完成 {successfulComparisonCount}/{comparisonCount} 份。
          {comparisonCount ? (
            <a
              href={`#module9-round-${currentIteration}-comparisons`}
              className="ml-1 font-medium text-primary underline underline-offset-4"
            >
              查看本轮逐篇比对及逐特征判断
            </a>
          ) : null}
        </p>
        <p>
          {canContinue
            ? `本轮已暂停。点击模块9操作区的“执行第 ${Math.min(5, currentIteration + 1)} 轮”，系统会直接使用已经冻结的下一轮检索词。`
            : continuationReason
              || (batchStatus === 'round_partial'
                ? '本轮仍有失败文献，请先重试失败文献，或明确保留失败后继续。'
                : '当前状态没有可开启的下一轮。')}
        </p>
        <p>
          <a
            href="#module9-actions"
            className="font-medium text-primary underline underline-offset-4"
          >
            返回模块9操作区
          </a>
        </p>
      </AlertDescription>
    </Alert>
  );
  if (!iterations.length) {
    return <div className="space-y-4">{guide}<GapQueryMatrixDetails batch={batch} /></div>;
  }
  return (
    <div className="space-y-5">
      {guide}
      <GapQueryMatrixDetails batch={batch} />
      <Accordion type="multiple" className="rounded-lg border px-4">
        {iterations.map((iteration) => {
          const summary = rows(batch.rounds).find(
            (item) => numberValue(item.iteration) === iteration,
          );
          return (
            <AccordionItem key={iteration} value={`executed-gap-round-${iteration}`}>
              <AccordionTrigger className="gap-3 text-left">
                <span className="flex flex-1 flex-wrap items-center justify-between gap-2 pr-2">
                  <span>第 {iteration} 轮检索与比对结果</span>
                  <Badge variant="outline">
                    I4-S {numberValue(summary?.successful_comparison_count) || 0}/
                    {numberValue(summary?.comparison_count) || 0}
                  </Badge>
                </span>
              </AccordionTrigger>
              <AccordionContent>
                <GapRoundResultDetails
                  runs={currentAttempts.filter(
                    (child) => child.roundIteration === iteration,
                  )}
                />
              </AccordionContent>
            </AccordionItem>
          );
        })}
      </Accordion>
    </div>
  );
}

function GapRoundResultDetails({ runs }: { runs: ChildRun[] }) {
  const gapPlan = runs.find(
    (child) => child.ok && child.code === 'I2_GAP_QUERY_PLAN',
  );
  if (!gapPlan) {
    return (
      <Alert>
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>尚未形成 gap 轮计划</AlertTitle>
        <AlertDescription>需要先选择 D1 并冻结区别特征。</AlertDescription>
      </Alert>
    );
  }
  const decision = record(gapPlan.output.gap_reuse_decision);
  const iteration = numberValue(decision.gap_search_iteration)
    ?? numberValue(gapPlan.output.gap_search_iteration)
    ?? 1;
  const strategyLabel = text(gapPlan.output.gap_search_strategy_label);
  const strategyDescription = text(
    gapPlan.output.gap_search_strategy_description,
  );
  const covered = stringValues(
    decision.covered_difference_feature_ids
    || gapPlan.output.covered_difference_feature_ids,
  );
  const explicitlyUncovered = stringValues(
    decision.uncovered_difference_feature_ids
    || gapPlan.output.uncovered_difference_feature_ids,
  );
  const humanReview = stringValues(gapPlan.output.human_review_feature_ids);
  const uncovered = uniqueText([...explicitlyUncovered, ...humanReview]);
  const allowedGap = stringValues(gapPlan.output.allowed_gap_feature_ids);
  const queries = rows(gapPlan.output.queries);
  const primaryQueries = queries.filter(isPrimaryGapQuery);
  const auxiliaryQueries = queries.filter((query) => !isPrimaryGapQuery(query));
  const limitations = rows(gapPlan.output.limitations);
  const featureTextById = new Map(
    limitations.map((item) => [text(item.feature_id), text(item.text)]),
  );
  const reuseEvidence = rows(
    decision.coverage_evidence || gapPlan.output.existing_corpus_reuse,
  );
  const coveredReuseEvidence = reuseEvidence.filter((item) => item.covered === true);
  const hasIncompleteReuseVersion = reuseEvidence.some((item) => (
    `${text(item.rationale)} ${text(item.reason)}`.includes('缺少可复用版本键')
  ));
  const searches = runs.filter((child) =>
    ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(child.code),
  );
  const hitCount = searches.reduce(
    (total, child) => total + rows(child.output.documents).length,
    0,
  );
  const fetches = runs.filter((child) => child.code === 'I3_FETCH');
  const analysisReadyCount = fetches.filter(
    (child) => child.ok && isAnalysisReady(child.output),
  ).length;
  const qualifications = runs.filter((child) => child.code === 'I3_QUALIFY');
  const comparisons = runs.filter(
    (child) => child.code === 'I4_S_SINGLE_REFERENCE',
  );
  return (
    <div className="space-y-4">
      <Alert>
        {humanReview.length && !queries.length
          ? <AlertTriangle className="h-4 w-4" />
          : uncovered.length
            ? <Search className="h-4 w-4" />
            : <CheckCircle2 className="h-4 w-4" />}
        <AlertTitle>第 {iteration} 个 gap 轮</AlertTitle>
        <AlertDescription className="space-y-1">
          {strategyLabel ? (
            <p>
              本轮固定策略：<strong>{strategyLabel}</strong>
              {strategyDescription ? `。${strategyDescription}` : '。'}
            </p>
          ) : null}
          <p>
            {queries.length
              ? `仍有 ${uncovered.length} 项区别特征未被同角色、同作用覆盖，本轮围绕其中 ${allowedGap.length || uncovered.length} 项生成检索，并继续执行真实检索、取文、日期核验和逐篇比对。`
              : humanReview.length
                ? `仍有 ${uncovered.length} 项区别特征未解决，其中 ${humanReview.length} 项已由模块8转律师人工复核；本轮不发起 provider 检索，也不视为已有证据覆盖。`
                : uncovered.length
                  ? `仍有 ${uncovered.length} 项区别特征未解决，但本轮没有形成可执行 provider 查询；不能视为已有证据覆盖。`
                  : '既有合格语料已经覆盖全部区别特征，本轮不发起新的 provider 检索。'}
          </p>
        </AlertDescription>
      </Alert>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded border bg-background p-3">
          <p className="text-xs text-muted-foreground">已复用覆盖</p>
          <p className="mt-1 font-medium">{covered.length} 项</p>
        </div>
        <div className="rounded border bg-background p-3">
          <p className="text-xs text-muted-foreground">仍待解决</p>
          <p className="mt-1 font-medium">{uncovered.length} 项</p>
        </div>
        <div className="rounded border bg-background p-3">
          <p className="text-xs text-muted-foreground">区别特征检索组</p>
          <p className="mt-1 font-medium">{primaryQueries.length} 组</p>
        </div>
        <div className="rounded border bg-background p-3">
          <p className="text-xs text-muted-foreground">律师人工复核</p>
          <p className="mt-1 font-medium">{humanReview.length} 项</p>
        </div>
      </div>
      {coveredReuseEvidence.length ? (
        <Accordion type="single" collapsible className="rounded border bg-background px-3">
          <AccordionItem value="module9-reused-coverage">
            <AccordionTrigger className="text-left">
              已复用覆盖文献与引文（{coveredReuseEvidence.length} 条）
            </AccordionTrigger>
            <AccordionContent className="space-y-3">
              {coveredReuseEvidence.map((item, index) => {
                const reuseKey = record(item.reuse_key);
                const featureId = text(item.feature_id);
                const documentLabel = text(item.publication_number)
                  || text(item.document_title)
                  || text(item.document_id)
                  || `对比文件 ${index + 1}`;
                return (
                  <div key={`${featureId}-${text(item.document_id)}-${index}`} className="rounded border bg-muted/20 p-3 text-sm">
                    <p className="font-medium">{documentLabel}</p>
                    {text(item.document_title) && text(item.document_title) !== documentLabel ? (
                      <p className="mt-1 text-muted-foreground">{text(item.document_title)}</p>
                    ) : null}
                    <p className="mt-2">
                      <span className="font-medium">覆盖区别特征：</span>
                      {featureTextById.get(featureId) || featureId || '未记录'}
                    </p>
                    <p className="mt-1"><span className="font-medium">对比文件原文：</span>{text(item.evidence_quote) || '—'}</p>
                    <p className="mt-1"><span className="font-medium">位置：</span>{text(item.evidence_location) || '—'}</p>
                    <p className="mt-1 text-muted-foreground">{text(item.rationale) || '—'}</p>
                    <p className="mt-2 break-all text-xs text-muted-foreground">
                      本次模块6批次 {text(item.source_module6_batch_id) || '未绑定'} · I4-S run {text(reuseKey.i4s_run_id) || '未绑定'}
                    </p>
                  </div>
                );
              })}
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      ) : covered.length ? (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>已覆盖结论缺少可展示的本次文献证据</AlertTitle>
          <AlertDescription>旧批次结果不能据此视为完成，请重新生成模块9批次。</AlertDescription>
        </Alert>
      ) : null}
      {queries.length ? (
        <div className="rounded border border-blue-200 bg-blue-50/60 p-3 dark:border-blue-900 dark:bg-blue-950/20">
          <p className="font-medium text-blue-950 dark:text-blue-100">本轮真实执行进度</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
            {[
              ['已执行检索', `${searches.filter((child) => child.ok).length}/${queries.length} 条`],
              ['原始命中', `${hitCount} 条`],
              ['全文可比对', `${analysisReadyCount} 份`],
              ['日期核验', `${qualifications.filter((child) => child.ok).length} 份`],
              ['逐篇比对', `${comparisons.filter((child) => child.ok).length}/${analysisReadyCount} 份`],
            ].map(([label, value]) => (
              <div key={label} className="rounded border bg-background p-2">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="mt-1 font-medium">{value}</p>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {hasIncompleteReuseVersion ? (
        <p className="rounded border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
          历史比对版本不完整，暂不计入已覆盖证据；相关特征已保留为未覆盖并继续补检。
        </p>
      ) : null}
      {primaryQueries.length ? (
        <div className="space-y-2">
          <div>
            <p className="font-medium">本轮区别特征检索组（{primaryQueries.length} 组）</p>
            <p className="mt-1 text-xs text-muted-foreground">
              每项未解决区别特征对应一组；客体组与特征组均采用中英文及上下位/同义表达。
            </p>
          </div>
          {primaryQueries.map((query, index) => {
            const featureId = gapQueryFeatureIds(query)[0];
            const featureText = featureTextById.get(featureId);
            return (
            <div key={text(query.query_id) || index} className="rounded border bg-background p-3">
              <p className="font-medium">区别特征 {index + 1}{featureText ? `：${featureText}` : ''}</p>
              <p className="mt-2 break-words font-mono text-sm">{text(query.expression)}</p>
            </div>
            );
          })}
          {auxiliaryQueries.length ? (
            <details className="rounded border bg-muted/20 p-3 text-sm">
              <summary className="cursor-pointer font-medium">补充证据通道（{auxiliaryQueries.length} 条）</summary>
              <div className="mt-2 space-y-2">
                {auxiliaryQueries.map((query, index) => (
                  <p key={text(query.query_id) || index} className="break-words font-mono text-xs">
                    {text(query.expression)}
                  </p>
                ))}
              </div>
            </details>
          ) : null}
        </div>
      ) : null}
      {comparisons.length ? (
        <div
          id={`module9-round-${iteration}-comparisons`}
          className="scroll-mt-6 space-y-2 rounded border bg-background p-3"
        >
          <div>
            <p className="font-medium">本轮新文献逐篇比对（{comparisons.length} 份）</p>
            <p className="mt-1 text-xs text-muted-foreground">
              点击任一文献即可查看逐项判断、对比文件原文、位置和分析理由，无需进入技术运行记录。
            </p>
          </div>
          <Accordion type="multiple" className="rounded border px-3">
            {comparisons.map((comparison, index) => {
              const disclosures = rows(comparison.output.disclosures);
              const documentLabel = comparison.documentId
                || text(comparison.output.publication_number)
                || `新文献 ${index + 1}`;
              return (
                <AccordionItem
                  key={comparison.moduleRunId || index}
                  value={comparison.moduleRunId || `${iteration}-${index}`}
                >
                  <AccordionTrigger className="gap-3 text-left">
                    <span className="flex flex-1 flex-wrap items-center justify-between gap-2 pr-2">
                      <span>{documentLabel}</span>
                      <Badge variant={comparison.ok ? 'outline' : 'destructive'}>
                        {comparison.ok
                          ? comparison.reusedFromI4sRunId
                            ? `复用前轮结论 · ${disclosures.length} 项判断`
                            : `已完成 ${disclosures.length} 项判断`
                          : friendlyError(comparison.error || comparison.status)}
                      </Badge>
                    </span>
                  </AccordionTrigger>
                  <AccordionContent>
                    {disclosures.length ? (
                      <div className="overflow-x-auto">
                        <table className="w-full min-w-[900px] border-collapse text-left text-sm">
                          <thead>
                            <tr className="bg-muted/50">
                              <th className="border p-2">权利要求技术特征</th>
                              <th className="border p-2">判断</th>
                              <th className="border p-2">对比文件原文</th>
                              <th className="border p-2">位置</th>
                              <th className="border p-2">理由</th>
                            </tr>
                          </thead>
                          <tbody>
                            {disclosures.map((item, disclosureIndex) => (
                              <tr key={text(item.feature_id) || disclosureIndex}>
                                <td className="border p-2">
                                  {text(item.feature_text) || text(item.feature_id) || '未记录特征原文'}
                                </td>
                                <td className="border p-2">{disclosureLabel(item.status)}</td>
                                <td className="border p-2">{text(item.evidence_quote) || '—'}</td>
                                <td className="border p-2">{text(item.evidence_location) || '—'}</td>
                                <td className="border p-2">{text(item.reasoning) || '—'}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">
                        {comparison.ok
                          ? '本次 I4-S 没有返回逐特征明细。'
                          : friendlyError(comparison.error || comparison.status)}
                      </p>
                    )}
                  </AccordionContent>
                </AccordionItem>
              );
            })}
          </Accordion>
        </div>
      ) : null}
    </div>
  );
}

function InventiveStepResultDetails({ runs }: { runs: ChildRun[] }) {
  const inventive = runs.find(
    (child) => child.ok && child.code === 'I4_I_INVENTIVE_STEP',
  );
  if (!inventive) {
    return (
      <Alert>
        <AlertTriangle className="h-4 w-4" />
        <AlertTitle>尚未形成创造性组合分析</AlertTitle>
        <AlertDescription>需要先完成 D1 选择，并具备可组合的日期合格文献。</AlertDescription>
      </Alert>
    );
  }
  const featureAnalysis = rows(inventive.output.distinguishing_feature_analysis);
  const gaps = rows(inventive.output.gaps);
  const consideredDocuments = stringValues(inventive.output.considered_document_ids);
  const combinationDocuments = stringValues(inventive.output.combination_document_ids);
  const evidenceComplete = inventive.output.evidence_complete === true;
  const conclusionText = text(inventive.output.conclusion_text)
    || (evidenceComplete
      ? '现有证据已形成缺乏创造性的完整证据链（供律师复核）'
      : '现有证据尚不足以证明不具备创造性');
  const criterionLine = (label: string, value: unknown) => {
    const criterion = record(value);
    return (
      <div className="rounded border bg-background p-3 text-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="font-medium">{label}</span>
          <Badge variant="outline">{text(criterion.status) || '待确认'}</Badge>
        </div>
        <p className="mt-2">{text(criterion.reasoning) || '尚未形成可核查分析。'}</p>
        {text(criterion.evidence_quote) ? (
          <blockquote className="mt-2 border-l-2 pl-3 text-muted-foreground">
            {text(criterion.evidence_quote)}
            {text(criterion.evidence_location)
              ? `（${text(criterion.evidence_location)}）`
              : ''}
          </blockquote>
        ) : null}
      </div>
    );
  };
  return (
    <div className="space-y-4">
      <Alert>
        {evidenceComplete
          ? <CheckCircle2 className="h-4 w-4" />
          : <AlertTriangle className="h-4 w-4" />}
        <AlertTitle>{conclusionText}</AlertTitle>
        <AlertDescription className="space-y-1">
          <p>
            该提示是按创造性三步法形成的证据状态，供律师复核，不是行政机关的最终法律结论。
          </p>
          {!evidenceComplete ? (
            <p>证据不足不等于目标专利已经被证明具备创造性或当然有效。</p>
          ) : null}
        </AlertDescription>
      </Alert>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded border bg-background p-3 text-sm">累计特征覆盖：{inventive.output.combined_feature_coverage_complete === true ? '完整（仅为必要条件）' : '不完整'}</div>
        <div className="rounded border bg-background p-3 text-sm">组合文献日期：{inventive.output.all_documents_date_eligible === true ? '全部通过' : '存在问题'}</div>
        <div className="rounded border bg-background p-3 text-sm">区别特征三步法：{featureAnalysis.length} 项</div>
        <div className="rounded border bg-background p-3 text-sm">证据缺口：{gaps.length} 项</div>
      </div>
      <Accordion type="multiple" className="rounded-lg border bg-background px-4">
        <AccordionItem value="inventive-step-1">
          <AccordionTrigger className="text-left">
            第一步：确定最接近的现有技术 D1
          </AccordionTrigger>
          <AccordionContent className="space-y-3">
            <p className="text-sm">
              D1：<strong>{text(inventive.output.closest_document_id) || '未标明'}</strong>
            </p>
            <p className="text-sm text-muted-foreground">
              模块9全部轮次已审阅 {consideredDocuments.length || combinationDocuments.length} 份当前语料；
              实际组合使用 {combinationDocuments.length} 份：
              {combinationDocuments.join('、') || '尚未形成'}。
            </p>
            {consideredDocuments.length ? (
              <details className="rounded border bg-muted/20 p-3 text-sm">
                <summary className="cursor-pointer font-medium">查看全部已审阅文献</summary>
                <p className="mt-2 break-words">{consideredDocuments.join('、')}</p>
              </details>
            ) : null}
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="inventive-step-2">
          <AccordionTrigger className="text-left">
            第二步：确定区别特征及其实际技术问题
          </AccordionTrigger>
          <AccordionContent>
            {featureAnalysis.length ? (
              <div className="space-y-2">
                {featureAnalysis.map((item, index) => (
                  <div key={text(item.feature_id) || index} className="rounded border p-3 text-sm">
                    <p className="font-medium">
                      区别特征 {index + 1}：{text(item.feature_text) || text(item.feature_id)}
                    </p>
                    <p className="mt-1 text-muted-foreground">
                      D1 状态：{text(item.d1_disclosure_status) || '未公开'}
                    </p>
                    <p className="mt-2">
                      实际技术问题：{text(item.objective_technical_problem) || '尚未明确'}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                当前输出没有逐项冻结 D1 区别特征，不能据此形成创造性结论。
              </p>
            )}
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="inventive-step-3">
          <AccordionTrigger className="text-left">
            第三步：逐区别特征核验公开、技术启示与组合路径
          </AccordionTrigger>
          <AccordionContent>
            {featureAnalysis.length ? (
              <Accordion type="multiple" className="rounded border px-3">
                {featureAnalysis.map((item, index) => {
                  const supporting = stringValues(item.supporting_document_ids);
                  const unresolved = stringValues(item.unresolved_reasons);
                  return (
                    <AccordionItem
                      key={text(item.feature_id) || index}
                      value={`inventive-feature-${text(item.feature_id) || index}`}
                    >
                      <AccordionTrigger className="gap-3 text-left">
                        <span className="flex flex-1 flex-wrap items-center justify-between gap-2 pr-2">
                          <span>{text(item.feature_text) || `区别特征 ${index + 1}`}</span>
                          <Badge variant={item.evidence_chain_complete === true ? 'outline' : 'destructive'}>
                            {item.evidence_chain_complete === true ? '证据链闭合' : '仍有缺口'}
                          </Badge>
                        </span>
                      </AccordionTrigger>
                      <AccordionContent className="space-y-3">
                        <div className="rounded border bg-muted/20 p-3 text-sm">
                          <p>公开该特征的补充证据：{supporting.join('、') || '尚未确认'}</p>
                          <p className="mt-1">具体修改路径：{text(item.modification_path) || '尚未形成'}</p>
                        </div>
                        <div className="grid gap-3 lg:grid-cols-2">
                          {criterionLine('相同结构角色和技术作用', item.same_role_and_effect)}
                          {criterionLine('具体技术启示', item.technical_teaching)}
                          {criterionLine('修改动机', item.modification_motivation)}
                          {criterionLine('反向教导', item.teaching_away)}
                          {criterionLine('技术效果是否可预期', item.technical_effect)}
                        </div>
                        {unresolved.length ? (
                          <ul className="list-disc space-y-1 pl-5 text-sm text-destructive">
                            {unresolved.map((reason, reasonIndex) => (
                              <li key={reasonIndex}>{reason}</li>
                            ))}
                          </ul>
                        ) : null}
                      </AccordionContent>
                    </AccordionItem>
                  );
                })}
              </Accordion>
            ) : null}
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="inventive-step-gaps">
          <AccordionTrigger className="text-left">
            未解决证据缺口（{gaps.length} 项）
          </AccordionTrigger>
          <AccordionContent>
            {gaps.length ? (
              <ul className="list-disc space-y-1 pl-5 text-sm">
                {gaps.map((gap, index) => <li key={index}>{inventiveGapLabel(gap)}</li>)}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">当前没有未解决证据缺口。</p>
            )}
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
}

function module11DocumentRecord(value: unknown): JsonObject {
  const wrapper = record(value);
  return Object.keys(record(wrapper.record)).length ? record(wrapper.record) : wrapper;
}

function module11DocumentLabel(document: JsonObject, fallback: string): string {
  const identifiers = record(document.identifiers);
  const publication = text(document.publication_number)
    || text(identifiers.publication_number)
    || text(document.canonical_key)
    || fallback;
  const title = text(document.title);
  return title ? `${publication}《${title}》` : publication;
}

function module11DisclosureRank(value: unknown): number {
  const status = text(value);
  if (['disclosed', 'explicit', 'direct_and_unambiguous', 'structural_equivalent', 'necessarily_implicit'].includes(status)) return 3;
  if (status === 'not_disclosed') return 2;
  if (['uncertain', 'insufficient_evidence', 'material_incomplete'].includes(status)) return 1;
  return 0;
}

function module11MatrixLabel(value: unknown): string {
  const status = text(value);
  if (['disclosed', 'explicit', 'direct_and_unambiguous', 'structural_equivalent'].includes(status)) return '有（明确）';
  if (status === 'necessarily_implicit') return '有（必然隐含）';
  if (status === 'not_disclosed') return '未披露';
  if (status === 'analysis_failed') return '分析失败';
  if (['uncertain', 'insufficient_evidence', 'material_incomplete'].includes(status)) return '待确认';
  return '未完成分析';
}

function latestModule11Disclosures(
  disclosures: JsonObject[],
  document: JsonObject,
): JsonObject[] {
  const currentVersionId = text(document.current_document_version_id);
  const latest = new Map<string, JsonObject>();
  [...disclosures]
    .filter((item) => (
      !currentVersionId
      || !text(item.document_version_id)
      || text(item.document_version_id) === currentVersionId
    ))
    .sort((left, right) => (
      `${text(left.updated_at) || text(left.created_at)}|${text(left.id)}`.localeCompare(
        `${text(right.updated_at) || text(right.created_at)}|${text(right.id)}`,
      )
    ))
    .forEach((item) => {
      const limitationId = text(item.limitation_id) || text(item.feature_id);
      if (limitationId) latest.set(limitationId, persistedDisclosureView(item));
    });
  return [...latest.values()];
}

function buildClientSimilarityChart({
  claimId,
  claimInvestigationId,
  limitations,
  comparisons,
}: {
  claimId: string;
  claimInvestigationId: string;
  limitations: JsonObject[];
  comparisons: Array<{ documentId: string; document: JsonObject; disclosures: JsonObject[] }>;
}): JsonObject | null {
  if (!limitations.length || !comparisons.length) return null;
  const ranked = comparisons.map((comparison) => {
    const disclosureByFeature = new Map(
      comparison.disclosures.map((item) => [
        text(item.feature_id) || text(item.limitation_id),
        item,
      ]),
    );
    const statuses = limitations.map((limitation) => {
      const featureId = text(limitation.feature_id) || text(limitation.id);
      const item = disclosureByFeature.get(featureId);
      return text(item?.status || item?.disclosure_status) || 'not_analysed';
    });
    const disclosed = statuses.filter((status) => module11DisclosureRank(status) === 3).length;
    const definitive = statuses.filter((status) => module11DisclosureRank(status) >= 2).length;
    const analysed = statuses.filter((status) => module11DisclosureRank(status) > 0).length;
    const explicit = statuses.filter((status) => ['disclosed', 'explicit', 'direct_and_unambiguous', 'structural_equivalent'].includes(status)).length;
    return { ...comparison, disclosed, definitive, analysed, explicit, disclosureByFeature };
  }).sort((left, right) => (
    right.disclosed - left.disclosed
    || right.definitive - left.definitive
    || right.analysed - left.analysed
    || right.explicit - left.explicit
    || module11DocumentLabel(left.document, left.documentId).localeCompare(
      module11DocumentLabel(right.document, right.documentId),
      'zh-CN',
    )
  )).slice(0, 10);
  return {
    claim_id: claimId,
    claim_investigation_id: claimInvestigationId,
    ranking_basis: '按本次 I4-S 已确认披露的权利要求特征数降序；待确认不计入已披露，再按确定性评价完整度排序。',
    ranked_documents: ranked.map((item, index) => ({
      rank: index + 1,
      document_id: item.documentId,
      document: item.document,
      confirmed_disclosed_feature_count: item.disclosed,
      total_feature_count: limitations.length,
    })),
    feature_rows: limitations.map((limitation) => {
      const featureId = text(limitation.feature_id) || text(limitation.id);
      return {
        limitation_id: featureId,
        feature_key: text(limitation.feature_key) || featureId,
        limitation_text: text(limitation.text) || text(limitation.limitation_text),
        cells: ranked.map((item) => {
          const disclosure = item.disclosureByFeature.get(featureId) || {};
          return {
            document_id: item.documentId,
            disclosure_status: disclosure.status || disclosure.disclosure_status || 'not_analysed',
          };
        }),
      };
    }),
  };
}

function fallbackInventiveNarrative(inventive: JsonObject, claimId: string): JsonObject | null {
  if (!Object.keys(inventive).length) return null;
  const featureAnalysis = rows(inventive.distinguishing_feature_analysis);
  const paragraphs = [
    `关于独立权利要求${claimId || ''}，本次以${text(inventive.closest_document_id) || '尚未确定的文献'}作为最接近的现有技术。以下内容仅转写模块10已经冻结的三步法结果。`,
  ];
  if (featureAnalysis.length) {
    paragraphs.push(`与该最接近现有技术相比，区别技术特征包括：${featureAnalysis.map((item) => text(item.feature_text) || text(item.feature_id)).join('；')}。`);
    featureAnalysis.forEach((item) => {
      const supporters = stringValues(item.supporting_document_ids);
      const unresolved = stringValues(item.unresolved_reasons);
      const criteria = [
        ['相同结构角色和技术作用', item.same_role_and_effect],
        ['具体技术启示', item.technical_teaching],
        ['修改动机', item.modification_motivation],
        ['反向教导', item.teaching_away],
        ['技术效果', item.technical_effect],
      ].map(([label, raw]) => {
        const criterion = record(raw);
        return `${label}为“${text(criterion.status) || '待确认'}”${text(criterion.reasoning) ? `，理由为${text(criterion.reasoning)}` : ''}`;
      });
      paragraphs.push(
        `对于区别技术特征“${text(item.feature_text) || text(item.feature_id)}”，其实际技术问题为：${text(item.objective_technical_problem) || '尚未明确'}。`
        + `${supporters.length ? `补充文献为${supporters.join('、')}。` : '尚未确认可引用的补充文献。'}`
        + `${criteria.join('；')}。`
        + `${text(item.modification_path) ? `具体修改路径为：${text(item.modification_path)}。` : '尚未形成具体修改路径。'}`
        + `${item.evidence_chain_complete === true ? '该项证据链已经闭合。' : `该项证据链尚未闭合${unresolved.length ? `，仍待解决：${unresolved.join('；')}` : ''}。`}`,
      );
    });
  }
  const evidenceComplete = inventive.evidence_complete === true;
  const conclusion = text(inventive.conclusion_text)
    || (evidenceComplete ? '现有证据已形成缺乏创造性的完整证据链（供律师复核）' : '现有证据尚不足以证明不具备创造性');
  paragraphs.push(evidenceComplete
    ? `综上，${conclusion}。这仍是供律师复核的证据判断，不是行政机关的最终决定。`
    : `综上，${conclusion}。证据不足不等于已经证明目标权利要求具备创造性或当然有效。`);
  return { claim_id: claimId, evidence_complete: evidenceComplete, conclusion_text: conclusion, paragraphs };
}

function LawyerReportDetails({
  child,
  fallbackReport,
}: {
  child: ChildRun | undefined;
  fallbackReport: JsonObject | null;
}) {
  const output = child?.output || {};
  const reportValue = record(output.report_data);
  const activeReport = Object.keys(reportValue).length ? reportValue : fallbackReport;
  const appendix = record(output.lab_analysis_appendix);
  const appendixLimitations = rows(appendix.limitations);
  const appendixComparisons = record(appendix.comparisons);
  const appendixDocuments = record(appendix.documents);
  const scopedClaims = reportClaimsInScope(activeReport, null);
  const scopedClaimIds = new Set(scopedClaims.map((claim) => text(claim.id)));
  const reportLimitations = rows(activeReport?.claim_limitations).filter((item) =>
    scopedClaimIds.has(text(item.claim_investigation_id)),
  );
  const reportDisclosures = rows(activeReport?.feature_disclosures).filter((item) =>
    scopedClaimIds.has(text(item.claim_investigation_id)),
  );
  const reportDocuments = rows(activeReport?.documents);
  const reportDocumentById = new Map(reportDocuments.map((item) => [text(item.id), item]));
  const usingAppendix = appendixLimitations.length > 0;
  const claimId = text(appendix.claim_id) || text(scopedClaims[0]?.claim_id);
  const claimInvestigationId = text(appendix.claim_investigation_id) || text(scopedClaims[0]?.id);
  const comparisons: Array<{ documentId: string; document: JsonObject; disclosures: JsonObject[] }> = usingAppendix
    ? Object.entries(appendixComparisons).map(([documentId, comparison]) => ({
        documentId,
        document: module11DocumentRecord(appendixDocuments[documentId]),
        disclosures: rows(record(comparison).disclosures),
      }))
    : [...new Set(reportDisclosures.map((item) => text(item.document_id)).filter(Boolean))].map((documentId) => ({
        documentId,
        document: reportDocumentById.get(documentId) || { id: documentId },
        disclosures: latestModule11Disclosures(
          reportDisclosures.filter((item) => text(item.document_id) === documentId),
          reportDocumentById.get(documentId) || { id: documentId },
        ),
      }));
  const normalizedLimitations = usingAppendix
    ? appendixLimitations
    : reportLimitations;
  const backendNarratives = rows(activeReport?.inventive_step_narratives);
  const appendixNarrative = record(appendix.inventive_step_narrative);
  const fallbackNarrative = fallbackInventiveNarrative(record(appendix.inventive_step), claimId);
  const narratives = Object.keys(appendixNarrative).length
    ? [appendixNarrative]
    : fallbackNarrative
      ? [fallbackNarrative]
      : backendNarratives;
  let similarityCharts = rows(activeReport?.similarity_claim_charts);
  if (usingAppendix || !similarityCharts.length) {
    const fallbackChart = buildClientSimilarityChart({
      claimId,
      claimInvestigationId,
      limitations: normalizedLimitations,
      comparisons,
    });
    similarityCharts = fallbackChart ? [fallbackChart] : [];
  }

  return (
    <div className="space-y-4 rounded border bg-background p-4">
      <div>
        <p className="text-xs text-muted-foreground">律师工作报告 · 当前自动口径</p>
        <h4 className="mt-1 text-lg font-semibold">
          {text(dig(activeReport, 'target_patent', 'patent_number')) || '目标专利'}
          {text(dig(activeReport, 'target_patent', 'title'))
            ? `《${text(dig(activeReport, 'target_patent', 'title'))}》`
            : ''}
        </h4>
        <p className="mt-1 text-sm text-muted-foreground">
          模块11分为三部分，默认全部收起。仅评价独立权利要求；本报告是检索与分析辅助材料，不是专利无效决定。
        </p>
      </div>

      <Accordion type="multiple" className="rounded-lg border px-4" data-module11-three-sections>
        <AccordionItem value="module11-narrative">
          <AccordionTrigger className="text-left">
            第一部分：模块10结论的律师可读文字版（{narratives.length} 项独立权利要求）
          </AccordionTrigger>
          <AccordionContent className="space-y-4">
            {narratives.length ? narratives.map((narrative, narrativeIndex) => (
              <article key={text(narrative.combination_id) || text(narrative.claim_investigation_id) || narrativeIndex} className="rounded border bg-muted/20 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h5 className="font-medium">独立权利要求 {text(narrative.claim_id) || narrativeIndex + 1}</h5>
                  <Badge variant={narrative.evidence_complete === true ? 'outline' : 'destructive'}>
                    {text(narrative.conclusion_text) || (narrative.evidence_complete === true ? '证据链闭合' : '证据仍有缺口')}
                  </Badge>
                </div>
                <div className="mt-3 space-y-3 text-sm leading-7">
                  {stringValues(narrative.paragraphs).map((paragraph, paragraphIndex) => (
                    <p key={paragraphIndex}>{paragraph}</p>
                  ))}
                </div>
              </article>
            )) : (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>尚未形成模块10文字分析</AlertTitle>
                <AlertDescription>必须先完成当前案件、当前独立权利要求的模块10三步法分析；模块11不会根据旧案件或其他批次补写结论。</AlertDescription>
              </Alert>
            )}
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="module11-top10">
          <AccordionTrigger className="text-left">
            第二部分：相似度最高的前10篇对比文件横向表
          </AccordionTrigger>
          <AccordionContent className="space-y-4">
            {similarityCharts.length ? similarityCharts.map((chart, chartIndex) => {
              const rankedDocuments = rows(chart.ranked_documents);
              return (
                <div key={text(chart.claim_investigation_id) || chartIndex} className="rounded border p-3">
                  <p className="font-medium">独立权利要求 {text(chart.claim_id) || chartIndex + 1}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{text(chart.ranking_basis)}</p>
                  <div className="mt-3 overflow-x-auto">
                    <table className="w-full min-w-[1800px] border-collapse text-center text-sm" data-module11-top10-matrix>
                      <thead>
                        <tr className="bg-muted/50">
                          <th className="sticky left-0 z-20 min-w-80 border bg-muted p-2 text-left">独立权利要求技术特征</th>
                          {rankedDocuments.map((ranked, documentIndex) => {
                            const document = record(ranked.document);
                            return (
                              <th key={text(ranked.document_id) || documentIndex} className="min-w-40 border p-2 align-top">
                                <p>第 {Number(ranked.rank || documentIndex + 1)} 名</p>
                                <p className="mt-1 font-normal">{module11DocumentLabel(document, text(ranked.document_id))}</p>
                                <p className="mt-1 text-xs font-normal text-muted-foreground">
                                  已披露 {Number(ranked.confirmed_disclosed_feature_count || 0)}/{Number(ranked.total_feature_count || rows(chart.feature_rows).length)}
                                </p>
                                {ranked.is_current_d1 === true ? <Badge className="mt-1" variant="outline">当前 D1</Badge> : null}
                              </th>
                            );
                          })}
                        </tr>
                      </thead>
                      <tbody>
                        {rows(chart.feature_rows).map((feature, featureIndex) => (
                          <tr key={text(feature.limitation_id) || featureIndex}>
                            <td className="sticky left-0 z-10 border bg-background p-2 text-left">
                              <p className="font-medium">{text(feature.feature_key) || `特征 ${featureIndex + 1}`}</p>
                              <p className="mt-1 text-muted-foreground">{text(feature.limitation_text)}</p>
                            </td>
                            {rows(feature.cells).map((cell, cellIndex) => (
                              <td key={`${text(cell.document_id)}-${cellIndex}`} className="border p-2">
                                <Badge variant="outline" className={disclosureBadgeClass(cell.disclosure_status)}>
                                  {module11MatrixLabel(cell.disclosure_status)}
                                </Badge>
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              );
            }) : (
              <p className="text-sm text-muted-foreground">尚无完成逐特征比对的文献，不能生成前10横向表。</p>
            )}
          </AccordionContent>
        </AccordionItem>

        <AccordionItem value="module11-all-documents">
          <AccordionTrigger className="text-left">
            第三部分：全部对比文件与独立权利要求逐篇比对（{comparisons.length} 份）
          </AccordionTrigger>
          <AccordionContent>
            {comparisons.length ? (
              <Accordion type="multiple" className="rounded border px-3" data-module11-all-document-comparisons>
                {comparisons.map((comparison) => {
                  const summary = disclosureSummary(comparison.disclosures.map((item) => ({
                    ...item,
                    status: item.status || item.disclosure_status,
                  })));
                  return (
                    <AccordionItem key={comparison.documentId} value={`module11-document-${comparison.documentId}`}>
                      <AccordionTrigger className="gap-3 text-left">
                        <span className="flex flex-1 flex-wrap items-center justify-between gap-2 pr-2">
                          <span>{module11DocumentLabel(comparison.document, comparison.documentId)}</span>
                          <span className="text-xs text-muted-foreground">
                            披露 {summary.disclosed} · 未披露 {summary.notDisclosed} · 待确认 {summary.uncertain} · 失败 {summary.failed}
                          </span>
                        </span>
                      </AccordionTrigger>
                      <AccordionContent>
                        <div className="overflow-x-auto">
                          <table className="w-full min-w-[1080px] border-collapse text-left text-sm">
                            <thead>
                              <tr className="bg-muted/50">
                                <th className="border p-2">权利要求技术特征</th>
                                <th className="border p-2">披露判断</th>
                                <th className="border p-2">精确原文</th>
                                <th className="border p-2">位置</th>
                                <th className="border p-2">结构对应</th>
                                <th className="border p-2">分析</th>
                              </tr>
                            </thead>
                            <tbody>
                              {normalizedLimitations.map((limitation, limitationIndex) => {
                                const limitationId = text(limitation.feature_id) || text(limitation.id);
                                const disclosure = comparison.disclosures.find((item) => (
                                  text(item.feature_id) === limitationId || text(item.limitation_id) === limitationId
                                ));
                                const disclosureView = usingAppendix ? (disclosure || {}) : persistedDisclosureView(disclosure);
                                return (
                                  <tr key={limitationId || limitationIndex}>
                                    <td className="border p-2">
                                      {text(limitation.feature_key) || limitationId} · {text(limitation.text) || text(limitation.limitation_text)}
                                    </td>
                                    <td className="border p-2">{disclosureLabel(disclosureView.status || disclosureView.disclosure_status)}</td>
                                    <td className="border p-2">{text(disclosureView.evidence_quote) || text(disclosureView.excerpt) || '—'}</td>
                                    <td className="border p-2">{text(disclosureView.evidence_location) || text(disclosureView.locator) || '—'}</td>
                                    <td className="border p-2"><StructureMappingDetails disclosure={disclosureView} /></td>
                                    <td className="border p-2">{text(disclosureView.reasoning) || text(record(disclosureView.analysis).reasoning) || '—'}</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      </AccordionContent>
                    </AccordionItem>
                  );
                })}
              </Accordion>
            ) : (
              <Alert>
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>本次尚无可复核的逐篇比对</AlertTitle>
                <AlertDescription>候选文献尚未取得可核验全文或未完成 I4-S，因此模块11不会伪造披露判断。</AlertDescription>
              </Alert>
            )}
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  );
}

function BusinessResultDetails({
  moduleId,
  state,
  fallbackReport,
  attachedSessionId,
  profileFallbackRuns,
}: {
  moduleId: BusinessModuleId;
  state: BusinessState;
  fallbackReport: JsonObject | null;
  attachedSessionId?: string;
  profileFallbackRuns?: ChildRun[];
}) {
  const runs = state.children || [];
  if (moduleId === 'target') {
    return (
      <TargetResultDetails
        runs={runs}
        fallbackReport={fallbackReport}
        attachedSessionId={attachedSessionId}
      />
    );
  }
  if (moduleId === 'date') return <DateResultDetails runs={runs} fallbackReport={fallbackReport} />;
  if (moduleId === 'profile') {
    return <ProfileResultDetails runs={runs} fallbackRuns={profileFallbackRuns} />;
  }
  if (moduleId === 'query') return <QueryResultDetails runs={runs} />;
  if (moduleId === 'evidence') return <EvidenceResultDetails runs={runs} />;
  if (moduleId === 'singleReference') return <AnalysisResultDetails runs={runs} />;
  if (moduleId === 'closestPriorArt') return <ClosestPriorArtResultDetails runs={runs} />;
  if (moduleId === 'obviousnessPrecheck') return <ObviousnessPrecheckResultDetails runs={runs} />;
  if (moduleId === 'gapSearch') {
    return (
      <GapSearchResultDetails
        runs={runs}
        batch={record(state.module9Batch)}
      />
    );
  }
  if (moduleId === 'inventiveStep') return <InventiveStepResultDetails runs={runs} />;
  if (moduleId === 'report') {
    return (
      <LawyerReportDetails
        child={runs.find((child) => child.ok && child.code === 'I5_REPORT')}
        fallbackReport={fallbackReport}
      />
    );
  }
  return null;
}

const BENCHMARK_DISCLOSED_STATUSES = new Set([
  'explicit',
  'direct_and_unambiguous',
  'necessarily_implicit',
]);

function benchmarkDocumentId(item: JsonObject): string {
  const output = record(item.comparison);
  return text(output.publication_number)
    || text(output.document_id)
    || text(item.documentFile).replace(/\.pdf$/i, '')
    || '未命名文献';
}

function benchmarkDisclosures(item: JsonObject): JsonObject[] {
  return rows(record(item.comparison).disclosures);
}

function benchmarkClosestComparison(claim: JsonObject): JsonObject | null {
  const comparisons = rows(claim.comparisons);
  if (!comparisons.length) return null;
  return [...comparisons].sort((left, right) => {
    const leftRows = benchmarkDisclosures(left);
    const rightRows = benchmarkDisclosures(right);
    const disclosedDifference = rightRows.filter((row) =>
      BENCHMARK_DISCLOSED_STATUSES.has(text(row.status)),
    ).length - leftRows.filter((row) =>
      BENCHMARK_DISCLOSED_STATUSES.has(text(row.status)),
    ).length;
    if (disclosedDifference) return disclosedDifference;
    const evidencedDifference = rightRows.filter((row) => text(row.evidence_quote)).length
      - leftRows.filter((row) => text(row.evidence_quote)).length;
    if (evidencedDifference) return evidencedDifference;
    return benchmarkDocumentId(left).localeCompare(benchmarkDocumentId(right));
  })[0];
}

function benchmarkGapRows(claim: JsonObject): JsonObject[] {
  const closest = benchmarkClosestComparison(claim);
  if (!closest) return [];
  return benchmarkDisclosures(closest).filter(
    (row) => !BENCHMARK_DISCLOSED_STATUSES.has(text(row.status)),
  );
}

function BenchmarkFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 break-words text-sm font-medium">{value}</p>
    </div>
  );
}

function BenchmarkStageDetails({
  moduleId,
  benchmarkCase,
  profileRuns,
  queryRuns,
  analysisRuns,
}: {
  moduleId: BusinessModuleId;
  benchmarkCase: JsonObject;
  profileRuns: ChildRun[];
  queryRuns: ChildRun[];
  analysisRuns: ChildRun[];
}) {
  const claims = rows(benchmarkCase.claims);
  const sourceDocuments = rows(benchmarkCase.sourceDocuments);
  const coverage = record(dig(benchmarkCase, 'stageCoverage', moduleId));
  const coverageMode = text(coverage.mode);
  const coverageLabel = coverageMode === 'frozen_result'
    ? '真实冻结结果'
    : coverageMode === 'source_fixture'
      ? '冻结来源样本'
      : '冻结证据复核预览';

  const coverageNotice = (
    <Alert>
      <CircleDashed className="h-4 w-4" />
      <AlertTitle>{coverageLabel}</AlertTitle>
      <AlertDescription>{text(coverage.note)}</AlertDescription>
    </Alert>
  );

  if (moduleId === 'profile') {
    return <div className="space-y-3">{coverageNotice}<ProfileResultDetails runs={profileRuns} /></div>;
  }
  if (moduleId === 'query') {
    return <div className="space-y-3">{coverageNotice}<QueryResultDetails runs={queryRuns} /></div>;
  }
  if (moduleId === 'singleReference') {
    return <div className="space-y-3">{coverageNotice}<AnalysisResultDetails runs={analysisRuns} /></div>;
  }
  if (moduleId === 'target') {
    return (
      <div className="space-y-3">
        {coverageNotice}
        <div className="grid gap-3 md:grid-cols-2">
          <BenchmarkFact label="申请号" value={text(dig(benchmarkCase, 'target', 'applicationNumber')) || '未记录'} />
          <BenchmarkFact label="公开号" value={text(dig(benchmarkCase, 'target', 'publicationNumber')) || '未记录'} />
          <BenchmarkFact label="专利名称" value={text(dig(benchmarkCase, 'target', 'title')) || '未记录'} />
          <BenchmarkFact label="冻结目标文件" value={text(dig(benchmarkCase, 'target', 'fileName')) || '未记录'} />
          <BenchmarkFact label="独立权利要求" value={claims.map((claim) => text(claim.claimId)).join('、') || '未记录'} />
          <BenchmarkFact label="冻结源文献" value={`${sourceDocuments.length} 份`} />
        </div>
      </div>
    );
  }
  if (moduleId === 'date') {
    return (
      <div className="space-y-3">
        {coverageNotice}
        {claims.map((claim) => (
          <div key={text(claim.claimId)} className="rounded-md border p-3">
            <p className="font-medium">独立权利要求 {text(claim.claimId)}</p>
            <p className="mt-1 text-sm">关键日：{text(claim.criticalDate) || '冻结记录未提供'}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              后续文献日期判断以该日为界；本示例仅复现冻结 r5 实际使用值。
            </p>
          </div>
        ))}
      </div>
    );
  }
  if (moduleId === 'evidence') {
    return (
      <div className="space-y-3">
        {coverageNotice}
        <div className="overflow-x-auto rounded-md border">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-muted/50 text-xs">
              <tr><th className="px-3 py-2">文献</th><th className="px-3 py-2">公开日</th><th className="px-3 py-2">冻结文件</th><th className="px-3 py-2">核验信息</th></tr>
            </thead>
            <tbody>
              {sourceDocuments.map((document) => (
                <tr key={`${text(document.documentFile)}-${text(document.publicationNumber)}`} className="border-t align-top">
                  <td className="px-3 py-2">
                    <p className="font-medium">{text(document.publicationNumber) || '未记录公开号'}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{text(document.title) || '未记录标题'}</p>
                  </td>
                  <td className="px-3 py-2">{text(document.publicationDate) || '待核验'}</td>
                  <td className="px-3 py-2 break-all">{text(document.documentFile)}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">
                    {document.pageCount ? `${String(document.pageCount)} 页；` : ''}
                    SHA-256 {text(document.sha256).slice(0, 12) || '未记录'}…
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (moduleId === 'closestPriorArt') {
    return (
      <div className="space-y-3">
        {coverageNotice}
        {claims.map((claim) => {
          const closest = benchmarkClosestComparison(claim);
          const disclosures = closest ? benchmarkDisclosures(closest) : [];
          const gaps = benchmarkGapRows(claim);
          return (
            <div key={text(claim.claimId)} className="rounded-md border p-3">
              <p className="font-medium">独立权利要求 {text(claim.claimId)} · D1 复核候选 {closest ? benchmarkDocumentId(closest) : '暂无'}</p>
              <p className="mt-1 text-sm">
                已确认披露 {disclosures.length - gaps.length}/{disclosures.length} 项；仍需冻结和复核 {gaps.length} 项区别特征。
              </p>
              {gaps.length ? (
                <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">
                  {gaps.map((row, index) => <li key={`${text(row.feature_id)}-${index}`}>{text(row.feature_text) || text(row.feature_id)}（{statusLabel(text(row.status))}）</li>)}
                </ul>
              ) : null}
            </div>
          );
        })}
      </div>
    );
  }

  if (moduleId === 'obviousnessPrecheck') {
    return (
      <div className="space-y-3">
        {coverageNotice}
        {claims.map((claim) => {
          const output = record(claim.obviousnessPrecheck);
          return (
            <div key={text(claim.claimId)} className="space-y-2">
              <p className="font-medium">独立权利要求 {text(claim.claimId)}</p>
              {Object.keys(output).length ? (
                <ObviousnessPrecheckResultDetails
                  runs={[{
                    code: 'I4_O_OBVIOUSNESS_PRECHECK',
                    ok: true,
                    status: 'succeeded',
                    output,
                    raw: {},
                  }]}
                />
              ) : (
                <Alert>
                  <AlertTriangle className="h-4 w-4" />
                  <AlertTitle>该冻结示例尚无 I4-O 结果</AlertTitle>
                  <AlertDescription>不以“未确认覆盖”代替惯用手段和 D1 技术启示分析。</AlertDescription>
                </Alert>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  if (moduleId === 'gapSearch') {
    return (
      <div className="space-y-3">
        {coverageNotice}
        {claims.map((claim) => {
          const precheck = record(claim.obviousnessPrecheck);
          const targets = rows(precheck.search_targets);
          const groups = rows(precheck.feature_groups);
          return (
            <div key={text(claim.claimId)} className="rounded-md border p-3">
              <p className="font-medium">独立权利要求 {text(claim.claimId)} · 预分析后的补证路线</p>
              <div className="mt-2 space-y-2">
                {targets.map((target, index) => (
                  <div key={`${text(target.feature_group_id)}-${index}`} className="rounded bg-muted/40 p-2 text-sm">
                    <p>{text(target.feature_group_id)} · {stringValues(target.feature_ids).join('、')}</p>
                    <p className="mt-1 font-medium">{precheckRouteLabel(target.route)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{text(target.rationale) || text(target.search_anchor)}</p>
                  </div>
                ))}
                {!targets.length && groups.length ? <p className="text-sm text-muted-foreground">预分析已关闭普通结构检索；无需重复查找同一结构。</p> : null}
                {!groups.length ? <p className="text-sm text-muted-foreground">该示例尚未冻结 I4-O 预分析，不生成臆测检索路线。</p> : null}
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  if (moduleId === 'inventiveStep') {
    return (
      <div className="space-y-3">
        {coverageNotice}
        {claims.map((claim) => {
          const closest = benchmarkClosestComparison(claim);
          const gaps = benchmarkGapRows(claim);
          const groups = rows(record(claim.obviousnessPrecheck).feature_groups);
          return (
            <div key={text(claim.claimId)} className="rounded-md border p-3">
              <p className="font-medium">独立权利要求 {text(claim.claimId)} · D1 {closest ? benchmarkDocumentId(closest) : '暂无'}</p>
              <p className="mt-1 text-sm">区别特征 {gaps.length} 项；已完成 {groups.length} 个特征组的五因素预分析。</p>
              {groups.length ? (
                <ul className="mt-2 list-disc space-y-2 pl-5 text-sm">
                  {groups.map((group, index) => (
                    <li key={text(group.feature_group_id) || index}>
                      {text(group.feature_group_id)}：D1启示{precheckStatusLabel(record(group.d1_teaching).status)}；
                      {group.d1_teaching_path_complete === true ? 'D1独立改造路径完整；' : 'D1启示需结合实现路径；'}
                      惯用手段{precheckStatusLabel(record(group.routine_means).status)}；
                      修改动机{precheckStatusLabel(record(group.modification_motivation).status)}；
                      反向教导{precheckStatusLabel(record(group.teaching_away).status)}；
                      技术效果{precheckStatusLabel(record(group.technical_effect).status)}。
                    </li>
                  ))}
                </ul>
              ) : <p className="mt-2 text-sm text-muted-foreground">当前冻结材料尚未形成 I4-O 预分析，不能用统一占位语代替创造性判断。</p>}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {coverageNotice}
      {claims.map((claim) => {
        const closest = benchmarkClosestComparison(claim);
        const disclosures = closest ? benchmarkDisclosures(closest) : [];
        return (
          <div key={text(claim.claimId)} className="space-y-2 rounded-md border p-3">
            <p className="font-medium">独立权利要求 {text(claim.claimId)} · 大 Claim Chart 预览</p>
            <p className="text-xs text-muted-foreground">主文献：{closest ? benchmarkDocumentId(closest) : '暂无'}。本表汇总冻结证据，不替代正式 I5 律师报告。</p>
            <div className="overflow-x-auto rounded border">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-muted/50 text-xs"><tr><th className="px-2 py-2">技术特征</th><th className="px-2 py-2">披露状态</th><th className="px-2 py-2">原文与位置</th></tr></thead>
                <tbody>
                  {disclosures.map((row, index) => (
                    <tr key={`${text(row.feature_id)}-${index}`} className="border-t align-top">
                      <td className="px-2 py-2">{text(row.feature_text) || text(row.feature_id)}</td>
                      <td className="px-2 py-2">{statusLabel(text(row.status))}</td>
                      <td className="px-2 py-2"><p>{text(row.evidence_quote) || '未取得可复核引文'}</p><p className="mt-1 text-xs text-muted-foreground">{text(row.evidence_location)}</p></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function FriendlyModuleLabPage() {
  const [health, setHealth] = useState<JsonObject | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [flowBusy, setFlowBusy] = useState(false);
  const [flowError, setFlowError] = useState<string | null>(null);
  const [attachedSessionId, setAttachedSessionId] = useState('');
  const [attachedPayload, setAttachedPayload] = useState<JsonObject | null>(null);
  const [investigationId, setInvestigationId] = useState('');
  const [investigation, setInvestigation] = useState<JsonObject | null>(null);
  const [claims, setClaims] = useState<JsonObject[]>([]);
  const [report, setReport] = useState<JsonObject | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [reportLoading, setReportLoading] = useState(false);
  const [benchmarkCases, setBenchmarkCases] = useState<JsonObject[]>([]);
  const [benchmarkCaseId, setBenchmarkCaseId] = useState('');
  const [benchmarkCase, setBenchmarkCase] = useState<JsonObject | null>(null);
  const [benchmarkView, setBenchmarkView] = useState<BusinessModuleId | null>(null);
  const [benchmarkLoading, setBenchmarkLoading] = useState(false);
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null);
  const [businessStates, setBusinessStates] = useState<
    Record<string, BusinessState>
  >({});
  const pollToken = useRef(0);
  const restoredFromUrl = useRef(false);
  const module5RecoveryKey = useRef('');
  const module9RecoveryKey = useRef('');
  const latestModule9Batch = useRef<JsonObject | null>(null);
  const module9PollToken = useRef(0);
  const investigationScope = useRef('');

  const refreshHealth = useCallback(async () => {
    try {
      setHealth(await jsonRequest('health'));
      setPageError(null);
    } catch (caught) {
      setHealth(null);
      setPageError(
        friendlyError(caught instanceof Error ? caught.message : '测试服务不可达'),
      );
    }
  }, []);

  const loadReport = useCallback(async (id: string): Promise<JsonObject> => {
    setReportLoading(true);
    try {
      const value = await jsonRequest(
        `v1/investigations/${encodeURIComponent(id)}/report-data?preview=true`,
      );
      setReport(value);
      setReportError(null);
      return value;
    } catch (caught) {
      const message = friendlyError(
        caught instanceof Error ? caught.message : '报告读取失败',
      );
      setReportError(message);
      throw new Error(message);
    } finally {
      setReportLoading(false);
    }
  }, []);

  const pollInvestigation = useCallback(
    async (id: string, token: number) => {
      for (;;) {
        if (pollToken.current !== token) return;
        try {
          const current = await jsonRequest(
            `v1/investigations/${encodeURIComponent(id)}`,
          );
          if (pollToken.current !== token) return;
          setInvestigation(current);
          try {
            const context = await jsonRequest(
              `v1/investigations/${encodeURIComponent(id)}/review-context`,
            );
            if (pollToken.current !== token) return;
            setClaims(rows(context.claims));
          } catch {
            // 调查刚创建时复核上下文可能尚未生成，下一轮继续读取。
          }
          const status = text(current.status);
          if (TERMINAL_INVESTIGATION.has(status)) {
            void loadReport(id).catch(() => undefined);
            return;
          }
        } catch (caught) {
          if (pollToken.current !== token) return;
          setFlowError(
            friendlyError(caught instanceof Error ? caught.message : '进度查询失败'),
          );
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 5000));
      }
    },
    [loadReport],
  );

  const pollAttachedSession = useCallback(async (sessionId: string, token: number) => {
    for (;;) {
      if (pollToken.current !== token) return;
      setReportLoading(true);
      try {
        const response = await fetch(
          `/api/admin/invalidity/session/${encodeURIComponent(sessionId)}`,
          { cache: 'no-store' },
        );
        const payload = await response.json().catch(() => ({})) as JsonObject;
        if (!response.ok) {
          throw new Error(text(payload.error) || `HTTP ${response.status}`);
        }
        if (pollToken.current !== token) return;
        const currentInvestigation = record(payload.investigation);
        const payloadSession = record(payload.session);
        const payloadResults = record(payloadSession.results);
        const currentReport = Object.keys(record(payload.report)).length
          ? record(payload.report)
          : null;
        setAttachedPayload(payload);
        setInvestigation(currentInvestigation);
        setInvestigationId(
          text(currentInvestigation.id || currentInvestigation.investigation_id)
          || text(payloadResults.invalidityInvestigationId),
        );
        setClaims(rows(payload.claim_investigations));
        setReport(currentReport);
        setReportError(text(payload.report_error) || null);
        setBusinessStates(attachedBusinessStates(currentReport, payload));
        setFlowError(null);
        setReportLoading(false);
        if (TERMINAL_INVESTIGATION.has(text(currentInvestigation.status))) return;
      } catch (caught) {
        if (pollToken.current !== token) return;
        setReportLoading(false);
        setFlowError(friendlyError(caught instanceof Error ? caught.message : '同任务诊断读取失败'));
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 5000));
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
  }, [refreshHealth]);

  useEffect(() => {
    void jsonRequest('benchmark-cases')
      .then((value) => {
        const available = rows(value.cases);
        setBenchmarkCases(available);
        setBenchmarkCaseId((current) => current || text(available[0]?.caseId));
      })
      .catch((error) => {
        setBenchmarkError(
          friendlyError(error instanceof Error ? error.message : '内置案例读取失败'),
        );
      });
  }, []);

  const loadBenchmark = useCallback(async (view: BusinessModuleId) => {
    if (!benchmarkCaseId) return;
    setBenchmarkLoading(true);
    setBenchmarkError(null);
    setBenchmarkView(view);
    try {
      const value = await jsonRequest(
        `benchmark-cases?caseId=${encodeURIComponent(benchmarkCaseId)}`,
      );
      setBenchmarkCase(value);
      setBenchmarkView(view);
    } catch (error) {
      setBenchmarkError(
        friendlyError(error instanceof Error ? error.message : '内置案例读取失败'),
      );
    } finally {
      setBenchmarkLoading(false);
    }
  }, [benchmarkCaseId]);

  useEffect(() => {
    if (restoredFromUrl.current) return;
    restoredFromUrl.current = true;
    const parameters = new URLSearchParams(window.location.search);
    const attached = parameters.get('session');
    if (attached) {
      setAttachedSessionId(attached);
      const token = ++pollToken.current;
      void pollAttachedSession(attached, token);
      return;
    }
    const restored = parameters.get('investigation');
    if (!restored) return;
    setInvestigationId(restored);
    const token = ++pollToken.current;
    void pollInvestigation(restored, token);
  }, [pollAttachedSession, pollInvestigation]);

  const startFlow = useCallback(
    async (input: {
      type: 'url' | 'file' | 'text';
      url?: string;
      fileKey?: string;
      text?: string;
    }) => {
      setFlowBusy(true);
      setFlowError(null);
      setAttachedSessionId('');
      setAttachedPayload(null);
      setInvestigation(null);
      setClaims([]);
      setReport(null);
      setReportError(null);
      setBusinessStates({});
      module5RecoveryKey.current = '';
      module9RecoveryKey.current = '';
      latestModule9Batch.current = null;
      module9PollToken.current += 1;
      pollToken.current += 1;
      try {
        let sourceFileKey: string | undefined;
        let sourceUrl: string | undefined;
        if (input.type === 'url') sourceUrl = input.url;
        if (input.type === 'file' && input.fileKey) sourceFileKey = input.fileKey;
        if (input.type === 'text' && input.text) {
          const form = new FormData();
          form.append(
            'file',
            new File(
              [input.text],
              `invalidity-lawyer-lab-${crypto.randomUUID()}.txt`,
              { type: 'text/plain' },
            ),
          );
          const uploadResponse = await fetch(
            '/api/invalidity/uploads?environment=test',
            { method: 'POST', body: form },
          );
          const uploaded = (await uploadResponse.json()) as JsonObject;
          if (!uploadResponse.ok || !uploaded.fileKey) {
            throw new Error(String(uploaded.error || '专利文本保存失败'));
          }
          sourceFileKey = String(uploaded.fileKey);
        }
        const created = await jsonRequest('v1/investigations', {
          method: 'POST',
          body: JSON.stringify({
            contract_version: 'v1',
            analysis_session_id: `invalidity_lawyer_lab_${crypto.randomUUID()}`,
            source_file_key: sourceFileKey || null,
            source_url: sourceUrl || null,
            max_rounds: 5,
            provider_mode: 'live',
            idempotency_key: crypto.randomUUID(),
          }),
        });
        const id = text(created.investigation_id);
        if (!id) throw new Error('测试服务未返回案件编号');
        setInvestigationId(id);
        const url = new URL(window.location.href);
        url.searchParams.delete('session');
        url.searchParams.set('investigation', id);
        url.searchParams.delete('module5_batch');
        url.searchParams.delete('module6_batch');
        url.searchParams.delete('module9_batch');
        window.history.replaceState(null, '', url);
        await jsonRequest(`v1/investigations/${encodeURIComponent(id)}/start`, {
          method: 'POST',
          body: '{}',
        });
        const token = ++pollToken.current;
        void pollInvestigation(id, token);
      } catch (caught) {
        setFlowError(
          friendlyError(caught instanceof Error ? caught.message : '创建分析失败'),
        );
      } finally {
        setFlowBusy(false);
      }
    },
    [pollInvestigation],
  );

  const runLabChild = useCallback(
    async ({
      code,
      mode,
      currentInvestigationId,
      claimInvestigationId,
      input,
    }: {
      code: string;
      mode: 'fixture' | 'live';
      currentInvestigationId?: string;
      claimInvestigationId?: string;
      input: JsonObject;
    }): Promise<ChildRun> => {
      const created = await jsonRequest('v1/lab/module-runs', {
        method: 'POST',
        body: JSON.stringify({
          contract_version: 'v1',
          module_code: code,
          input_mode: mode,
          investigation_id: mode === 'live' ? currentInvestigationId : null,
          claim_investigation_id:
            mode === 'live' ? claimInvestigationId || null : null,
          force_recompute: true,
          input,
          idempotency_key: `lawyer-lab-${crypto.randomUUID()}`,
        }),
      });
      const runId = text(created.module_run_id);
      if (!runId) throw new Error('测试服务未返回模块运行编号');
      for (let attempt = 0; attempt < 5760; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1250));
        const detail = await jsonRequest(
          `v1/lab/module-runs/${encodeURIComponent(runId)}`,
        );
        const run = record(detail.module_run);
        const job = record(detail.job);
        const status = text(run.status);
        if (!TERMINAL_RUN.has(status)) continue;
        const ok = status === 'succeeded' || status === 'completed';
        const error = friendlyError(
          text(run.error_message) || text(run.error_code) || statusLabel(status),
        );
        return {
          code,
          ok,
          status,
          output: outputFromRun(run),
          moduleRunId: runId,
          error: ok ? undefined : error,
          attempts: numberValue(job.attempt_count) ?? undefined,
          raw: detail,
        };
      }
      throw new Error('模块运行超过 2 小时仍未结束；后台运行记录仍保留，可重新打开案件继续读取。');
    },
    [],
  );

  const runModule5Batch = useCallback(
    async ({
      currentInvestigationId,
      claimInvestigationId,
      documentIds,
      existingBatchId,
      stateModuleId = 'singleReference',
      progressChildren = [],
    }: {
      currentInvestigationId: string;
      claimInvestigationId: string;
      documentIds?: string[];
      existingBatchId?: string;
      stateModuleId?: 'singleReference' | 'gapSearch';
      progressChildren?: ChildRun[];
    }): Promise<ChildRun[]> => {
      let consecutiveNetworkFailures = 0;
      let batch: JsonObject | null = null;
      const postBody = documentIds !== undefined
        ? JSON.stringify({
            contract_version: 'v1',
            investigation_id: currentInvestigationId,
            claim_investigation_id: claimInvestigationId,
            document_ids: documentIds || [],
            recover_persisted_documents:
              documentIds !== undefined && documentIds.length === 0,
            idempotency_key: `lawyer-module6-${crypto.randomUUID()}`,
          })
        : null;

      for (let poll = 0; poll < 9600; poll += 1) {
        try {
          if (!batch) {
            batch = postBody
              ? await module5BatchRequest({ method: 'POST', body: postBody })
              : await module5BatchRequest(
                  undefined,
                  existingBatchId
                    ? (
                        `?batch_id=${encodeURIComponent(existingBatchId)}`
                        + `&claim_investigation_id=${encodeURIComponent(claimInvestigationId)}`
                      )
                    : (
                        `?investigation_id=${encodeURIComponent(currentInvestigationId)}`
                        + `&claim_investigation_id=${encodeURIComponent(claimInvestigationId)}`
                      ),
                );
          } else {
            const batchId = text(batch.batch_id);
            if (!batchId) throw new Error('模块六单篇比对批次缺少批次编号');
            await new Promise((resolve) => setTimeout(resolve, 1500));
            batch = await module5BatchRequest(
              undefined,
              (
                `?batch_id=${encodeURIComponent(batchId)}`
                + `&claim_investigation_id=${encodeURIComponent(claimInvestigationId)}`
              ),
            );
          }
          consecutiveNetworkFailures = 0;
        } catch (error) {
          if (!isNetworkInterruption(error) || consecutiveNetworkFailures >= 60) {
            throw error;
          }
          consecutiveNetworkFailures += 1;
          await new Promise((resolve) =>
            setTimeout(resolve, Math.min(5000, 500 + consecutiveNetworkFailures * 250)),
          );
          continue;
        }

        const currentBatch = batch;
        if (!currentBatch) continue;
        if (
          text(currentBatch.investigation_id) !== currentInvestigationId
          || text(currentBatch.claim_investigation_id) !== claimInvestigationId
        ) {
          throw new Error('模块六批次不属于当前目标专利或独立权利要求，已拒绝复用。');
        }
        const batchId = text(currentBatch.batch_id);
        if (batchId && stateModuleId === 'singleReference') {
          const url = new URL(window.location.href);
          url.searchParams.set('module6_batch', batchId);
          url.searchParams.delete('module5_batch');
          window.history.replaceState(null, '', url);
        }
        const children = module5BatchChildren(batch.children);
        const completed = numberValue(batch.completed_document_count) || 0;
        const total = numberValue(batch.document_count) || documentIds?.length || 0;
        const queued = numberValue(batch.queued_document_count) || 0;
        const running = numberValue(batch.running_document_count) || 0;
        const usable = numberValue(batch.successful_document_count) || 0;
        const status = text(batch.status);
        const progressFacts = [
          `已提交 ${total} 份全文：排队 ${queued} 份、执行 ${running} 份、已收口 ${completed} 份，其中 ${usable} 份已有可用逐项结果。`,
          status === 'retrying'
            ? '首遍已完成，只补跑失败文献；普通临时失败补跑一次，1210 载荷降级最多再补一轮。'
            : '排队时间与单篇实际执行时间分开记录；每份文献都有独立运行记录，页面断线不会清空已经完成的结果。',
        ];
        if (text(batch.warning)) {
          progressFacts.push(`连接提示：${friendlyError(text(batch.warning))}`);
        }
        setBusinessStates((current) => ({
          ...current,
          [stateModuleId]: {
            phase: MODULE5_BATCH_TERMINAL.has(status) ? 'done' : 'running',
            mode: 'current',
            ok: status === 'completed',
            headline: MODULE5_BATCH_TERMINAL.has(status)
              ? status === 'completed'
                ? '当前案件模块运行完成'
                : '本模块部分完成，仍有文献未处理成功'
              : stateModuleId === 'gapSearch'
                ? `正在对本轮新文献逐篇比对（${completed}/${total}）`
                : `正在逐篇分析（${completed}/${total}）`,
            facts: progressFacts,
            children: [...progressChildren, ...children],
            reportSnapshot: report,
            module6Batch: stateModuleId === 'singleReference'
              ? currentBatch
              : current[stateModuleId]?.module6Batch,
          },
        }));
        if (MODULE5_BATCH_TERMINAL.has(status)) return children;
      }
      throw new Error('模块六单篇比对批次持续超过 4 小时；后台任务与逐篇结果均已保留，可稍后继续读取。');
    },
    [report],
  );

  const runModule9Batch = useCallback(
    async ({
      currentInvestigationId,
      claimInvestigationId,
      module6BatchId,
      closestPriorArtRunId,
      obviousnessPrecheckRunId,
      existingBatchId,
      initialBatch,
    }: {
      currentInvestigationId: string;
      claimInvestigationId: string;
      module6BatchId?: string;
      closestPriorArtRunId?: string;
      obviousnessPrecheckRunId?: string;
      existingBatchId?: string;
      initialBatch?: JsonObject;
    }): Promise<ChildRun[]> => {
      const scopeToken = ++module9PollToken.current;
      let batch: JsonObject | null = initialBatch || null;
      let consecutiveNetworkFailures = 0;
      if (
        !existingBatchId
        && (!module6BatchId || !closestPriorArtRunId || !obviousnessPrecheckRunId)
      ) {
        throw new Error('模块9必须绑定本次模块6批次、模块7结果和模块8结果。');
      }
      const postBody = existingBatchId
        ? null
        : JSON.stringify({
            contract_version: 'v1',
            investigation_id: currentInvestigationId,
            claim_investigation_id: claimInvestigationId,
            module6_batch_id: module6BatchId,
            closest_prior_art_run_id: closestPriorArtRunId,
            obviousness_precheck_run_id: obviousnessPrecheckRunId,
            start_mode: 'fresh',
            idempotency_key: `lawyer-module9-${crypto.randomUUID()}`,
          });

      for (let poll = 0; poll < 19200; poll += 1) {
        if (module9PollToken.current !== scopeToken) return [];
        try {
          if (!batch) {
            batch = postBody
              ? await module9BatchRequest({ method: 'POST', body: postBody })
              : await module9BatchRequest(
                  undefined,
                  `?batch_id=${encodeURIComponent(existingBatchId || '')}`,
                );
          } else {
            const batchId = text(batch.batch_id);
            if (!batchId) throw new Error('模块九补证批次缺少批次编号');
            await new Promise((resolve) => setTimeout(resolve, 1500));
            batch = await module9BatchRequest(
              undefined,
              `?batch_id=${encodeURIComponent(batchId)}`,
            );
          }
          consecutiveNetworkFailures = 0;
        } catch (error) {
          if (module9PollToken.current !== scopeToken) return [];
          if (!isNetworkInterruption(error) || consecutiveNetworkFailures >= 60) {
            throw error;
          }
          consecutiveNetworkFailures += 1;
          await new Promise((resolve) =>
            setTimeout(resolve, Math.min(5000, 500 + consecutiveNetworkFailures * 250)),
          );
          continue;
        }

        const currentBatch = batch;
        if (!currentBatch) continue;
        if (
          text(currentBatch.investigation_id) !== currentInvestigationId
          || text(currentBatch.claim_investigation_id) !== claimInvestigationId
        ) {
          throw new Error('模块九批次不属于当前目标专利或独立权利要求，已拒绝复用旧案件进度。');
        }
        if (module9PollToken.current !== scopeToken) return [];
        const batchId = text(currentBatch.batch_id);
        if (batchId) {
          const url = new URL(window.location.href);
          url.searchParams.set('module9_batch', batchId);
          window.history.replaceState(null, '', url);
        }
        const children = module5BatchChildren(currentBatch.children);
        const status = text(currentBatch.status);
        const currentIteration = numberValue(currentBatch.current_iteration) || 1;
        const completedRounds = numberValue(currentBatch.completed_round_count) || 0;
        const queryCount = numberValue(currentBatch.planned_query_count) || 0;
        const documentCount = numberValue(currentBatch.analysis_ready_document_count) || 0;
        const currentRound = record(currentBatch.current_round);
        const pollStopped = MODULE9_BATCH_POLL_STOP.has(status);
        const ok = !['round_partial', 'partial', 'failed'].includes(status);
        latestModule9Batch.current = currentBatch;
        setBusinessStates((current) => ({
          ...current,
          gapSearch: {
            phase: pollStopped ? 'done' : 'running',
            mode: 'current',
            ok: pollStopped ? ok : undefined,
            headline: pollStopped
              ? status === 'completed'
                ? '全部区别特征已取得可引用覆盖证据'
                : status === 'exhausted'
                  ? '五轮补证检索已完成，仍保留未解决区别特征'
                  : status === 'awaiting_next_round'
                    ? `第 ${currentIteration} 轮已完成，可决定是否继续下一轮`
                    : status === 'round_partial'
                      ? `第 ${currentIteration} 轮部分完成，失败文献可单独重试`
                      : '补证循环部分完成，仍有运行失败'
              : status === 'planning_matrix'
                ? '正在一次生成并校验五轮检索词矩阵'
                : `正在执行第 ${currentIteration}/5 个 gap 轮`,
            facts: [
              `本轮生成 ${numberValue(currentRound.planned_query_count) || 0} 条检索线，实际成功执行 ${numberValue(currentRound.executed_search_count) || 0} 条；原始命中 ${numberValue(currentRound.raw_hit_count) || 0} 条。`,
              `本轮 ${numberValue(currentRound.analysis_ready_document_count) || 0} 份全文可比对，I4-S 成功 ${numberValue(currentRound.successful_comparison_count) || 0} 份、失败 ${numberValue(currentRound.failed_comparison_count) || 0} 份。`,
              `累计已收口 ${completedRounds} 轮、生成 ${queryCount} 条检索线，共有 ${documentCount} 份不同文献进入逐篇比对。`,
              '五轮检索词会在批次开始时一次生成并冻结；每次操作只执行一轮，继续下一轮不会重新生成检索词。',
              '某项区别特征在前轮取得可引用覆盖后，后续轮次对应词组会标记为“已解决，自动跳过”。',
              '后轮命中同一文献时，仅在文献版本、全文哈希、日期核验与 I4-S 规则完全一致时复用既有逐项结论。',
              '批次、每轮计划和所有子运行均已持久化，页面刷新后从同一批次继续。',
              ...(text(currentBatch.warning) ? [`运行提示：${friendlyError(text(currentBatch.warning))}`] : []),
            ],
            children,
            reportSnapshot: report,
            module9Batch: currentBatch,
          },
        }));
        if (pollStopped) return children;
      }
      throw new Error('模块九补证批次持续超过 8 小时；后台任务和轮次状态均已保留，可稍后继续读取。');
    },
    [report],
  );

  const operateModule9Batch = useCallback(
    async (action: 'retry_failed' | 'continue_next_round', allowFailed = false) => {
      const currentBatch = latestModule9Batch.current;
      const batchId = text(currentBatch?.batch_id);
      const currentInvestigationId = text(currentBatch?.investigation_id);
      const claimInvestigationId = text(currentBatch?.claim_investigation_id);
      if (!batchId || !currentInvestigationId || !claimInvestigationId) return;
      setBusinessStates((current) => ({
        ...current,
        gapSearch: {
          ...(current.gapSearch || {}),
          phase: 'running',
          mode: 'current',
          headline: action === 'retry_failed'
            ? '正在仅重试本轮失败文献'
            : '正在执行已冻结的下一轮补证检索',
        },
      }));
      try {
        const nextBatch = await module9BatchRequest({
          method: 'POST',
          body: JSON.stringify({
            contract_version: 'v1',
            batch_id: batchId,
            action,
            allow_failed: allowFailed,
          }),
        });
        await runModule9Batch({
          currentInvestigationId,
          claimInvestigationId,
          existingBatchId: batchId,
          initialBatch: nextBatch,
        });
      } catch (error) {
        setBusinessStates((current) => ({
          ...current,
          gapSearch: {
            ...(current.gapSearch || {}),
            phase: 'done',
            mode: 'current',
            ok: false,
            headline: '模块九批次操作失败',
            facts: [friendlyError(error instanceof Error ? error.message : '批次操作失败')],
          },
        }));
      }
    },
    [runModule9Batch],
  );

  const runFixtureBusiness = useCallback(
    async (module: BusinessModule): Promise<ChildRun[]> => {
      const result: ChildRun[] = [];
      for (const code of module.technicalCodes) {
        result.push(
          await runLabChild({
            code,
            mode: 'fixture',
            input: { fixture: 'default' },
          }),
        );
      }
      return result;
    },
    [runLabChild],
  );

  const runCurrentBusiness = useCallback(
    async (
      module: BusinessModule,
    ): Promise<{ children: ChildRun[]; activeReport: JsonObject | null }> => {
      if (!investigationId) {
        throw new Error('请先在页面上方上传一件真实专利。');
      }
      let activeReport = report;
      if (!activeReport) {
        try {
          activeReport = await loadReport(investigationId);
        } catch {
          activeReport = null;
        }
      }
      const patentClaims = patentClaimsFrom(investigation, activeReport)
        .filter(isIndependentPatentClaim)
        .sort(claimSort);
      const claimRows = scopedClaimRows(claims, investigation, activeReport);
      const claimRowById = new Map(
        claimRows.map((claim) => [text(claim.claim_id), claim]),
      );
      const firstClaimRow = claimRows[0];
      const firstClaimRowId = text(firstClaimRow?.id);
      const children: ChildRun[] = [];

      const runInventiveProfileForClaim = async (
        patentClaim: JsonObject,
      ): Promise<ChildRun> => {
        const claimId = text(patentClaim.claim_id);
        return runLabChild({
          code: 'I2_INVENTIVE_PROFILE',
          mode: 'live',
          currentInvestigationId: investigationId,
          claimInvestigationId: text(claimRowById.get(claimId)?.id),
          input: {
            claim_id: claimId,
            expanded_claim_text:
              text(patentClaim.expanded_claim_text)
              || text(patentClaim.claim_text),
            iteration_number: 1,
            round_kind: 'initial',
            existing_limitations: [],
            gap_feature_ids: [],
          },
        });
      };

      const runQueryPlanForClaim = async (
        patentClaim: JsonObject,
      ): Promise<ChildRun> => {
        const claimId = text(patentClaim.claim_id);
        return runLabChild({
          code: 'I2_QUERY_PLAN',
          mode: 'live',
          currentInvestigationId: investigationId,
          claimInvestigationId: text(claimRowById.get(claimId)?.id),
          input: {
            claim_id: claimId,
            expanded_claim_text:
              text(patentClaim.expanded_claim_text)
              || text(patentClaim.claim_text),
            iteration_number: 1,
            round_kind: 'initial',
            existing_limitations: [],
            gap_feature_ids: [],
            max_queries: 5,
          },
        });
      };

      const ensureFirstQueryPlan = async (): Promise<ChildRun> => {
        if (!patentClaims[0]) {
          throw new Error('当前案件没有识别到独立权利要求，不能生成检索关键词。');
        }
        return runQueryPlanForClaim(patentClaims[0]);
      };

      const runEvidenceChain = async (
        queryPlan: ChildRun,
        roundKind: 'initial' | 'gap' = 'initial',
      ): Promise<ChildRun[]> => {
        if (!firstClaimRowId) {
          throw new Error('当前案件还没有独立权利要求分析记录。');
        }
        const evidenceChildren: ChildRun[] = [];
        evidenceChildren.push(queryPlan);
        const criticalDate =
          text(firstClaimRow?.critical_date)
          || text(dig(investigation, 'source_snapshot', 'patent_snapshot', 'priority_date'))
          || text(dig(investigation, 'source_snapshot', 'patent_snapshot', 'application_date'));
        const allQueries = rows(queryPlan.output.queries).filter(
          (query) => text(query.expression),
        );
        const plannedQueries = allQueries.filter(
          (query) => text(query.provider_kind) === 'patent',
        );
        if (roundKind === 'initial' && (
          plannedQueries.length < 1
          || plannedQueries.length > 5
          || plannedQueries.some((query) =>
            text(query.date_channel || 'ordinary_prior_art') !== 'ordinary_prior_art'
            || !FIXED_FIRST_ROUND_QUERY_VARIANTS.has(text(query.query_variant)),
          )
        )) {
          throw new Error(
            `模块4没有形成合规的固定五类首轮检索词（实际 ${plannedQueries.length} 条），已拒绝回退旧方案。`,
          );
        }
        const unresolvedGapFeatures = roundKind === 'gap'
          ? uniqueText([
              ...stringValues(queryPlan.output.allowed_gap_feature_ids),
              ...stringValues(queryPlan.output.uncovered_difference_feature_ids),
            ])
          : [];
        const primaryGapQueries = plannedQueries.filter(isPrimaryGapQuery);
        const primaryGapFeatures = new Set(
          primaryGapQueries.flatMap(gapQueryFeatureIds),
        );
        const missingPrimaryGapFeatures = unresolvedGapFeatures.filter(
          (featureId) => !primaryGapFeatures.has(featureId),
        );
        if (roundKind === 'gap' && (
          allQueries.length > 100
          || primaryGapQueries.length !== unresolvedGapFeatures.length
          || missingPrimaryGapFeatures.length > 0
          || primaryGapQueries.some((query) => {
            const groups = gapBooleanGroups(text(query.expression));
            return gapQueryFeatureIds(query).length !== 1
              || groups.length !== 2
              || groups.some((group) => !isBilingualGroup(group));
          })
          || plannedQueries.some((query) =>
            text(query.query_role) !== 'gap_followup'
            || text(query.query_variant) !== 'gap_followup'
            || !['ordinary_prior_art', 'cn_conflicting_application'].includes(
              text(query.date_channel || 'ordinary_prior_art'),
            ),
          )
        )) {
          throw new Error(
            missingPrimaryGapFeatures.length
              ? `模块9没有为每个未覆盖区别特征保留独立主检索线：${missingPrimaryGapFeatures.join('、')}`
              : `模块9必须为 ${unresolvedGapFeatures.length} 项区别特征生成恰好 ${unresolvedGapFeatures.length} 组双语主检索式。`,
          );
        }
        for (const plannedQuery of plannedQueries) {
          const roleValue = text(plannedQuery.query_role);
          const role: PatsnapQueryRole = [
              'inventive_point_precision',
              'claim_context_recall',
              'title_abstract_concept',
              'gap_followup',
            ].includes(roleValue)
              ? roleValue as PatsnapQueryRole
              : 'claim_context_recall';
          const plannedQueryId = text(plannedQuery.query_id) || undefined;
          if (!plannedQueryId || !queryPlan.moduleRunId) {
            throw new Error('真实检索缺少持久化检索方案或 query_id，不能安全执行。');
          }
          const balanced = roundKind === 'initial'
            ? buildPatsnapSearchPlan(
                queryPlan.raw,
                criticalDate,
                10,
                'balanced',
                role,
                plannedQueryId,
              )
            : {
                input: {
                  search_provider: 'patsnap',
                  search_modality: 'text',
                  query: {
                    text:
                      text(plannedQuery.provider_expression)
                      || text(plannedQuery.expression),
                    subject_terms: stringValues(plannedQuery.subject_terms),
                    feature_terms: stringValues(plannedQuery.feature_terms),
                  },
                  subject_terms: stringValues(plannedQuery.subject_terms),
                  feature_terms: stringValues(plannedQuery.feature_terms),
                  language: text(plannedQuery.language) || 'zh',
                  max_results: 10,
                  query_plan_query_id: plannedQueryId,
                  query_role: 'gap_followup',
                  query_variant: 'gap_followup',
                  allow_zero_results: plannedQuery.allow_zero_results === true,
                  compact_fallback_allowed: false,
                },
                strategy: 'gap-followup',
                compactFallbackAllowed: false,
              };
          const patentRun = await runLabChild({
            code: 'I3_PATENT_SEARCH',
            mode: 'live',
            currentInvestigationId: investigationId,
            claimInvestigationId: firstClaimRowId,
            input: {
              ...balanced.input,
              query_plan_run_id: queryPlan.moduleRunId,
              query_strategy: balanced.strategy,
            },
          });
          evidenceChildren.push(patentRun);

          if (
            patentRun.ok
            && (numberValue(patentRun.output.count)
              ?? rows(patentRun.output.documents).length) === 0
            && balanced.compactFallbackAllowed
          ) {
            const compact = buildPatsnapSearchPlan(
              queryPlan.raw,
              criticalDate,
              10,
              'compact-fallback',
              role,
              plannedQueryId,
            );
            evidenceChildren.push(
              await runLabChild({
                code: 'I3_PATENT_SEARCH',
                mode: 'live',
                currentInvestigationId: investigationId,
                claimInvestigationId: firstClaimRowId,
                input: {
                  ...compact.input,
                  query_strategy: compact.strategy,
                },
              }),
            );
          }
        }

        const nplQueries = allQueries.filter(
          (query) =>
            text(query.provider_kind) === 'npl'
            && text(query.date_channel || 'ordinary_prior_art') === 'ordinary_prior_art',
        );
        for (const nplQuery of nplQueries) {
          evidenceChildren.push(
            await runLabChild({
              code: 'I3_NPL_SEARCH',
              mode: 'live',
              currentInvestigationId: investigationId,
              claimInvestigationId: firstClaimRowId,
              input: {
                query: {
                  text: text(nplQuery.expression),
                  subject_terms: stringValues(nplQuery.subject_terms),
                  feature_terms: stringValues(nplQuery.feature_terms),
                  query_id: text(nplQuery.query_id),
                },
                max_results: 10,
              },
            }),
          );
        }

        const completedSearches = evidenceChildren.filter(
          (child) =>
            child.ok
            && child.moduleRunId
            && ['I3_PATENT_SEARCH', 'I3_NPL_SEARCH'].includes(child.code),
        );
        if (!completedSearches.length) return evidenceChildren;
        const candidateFilter = await runLabChild({
          code: 'I3_CANDIDATE_FILTER',
          mode: 'live',
          currentInvestigationId: investigationId,
          claimInvestigationId: firstClaimRowId,
          input: {
            search_run_ids: completedSearches.map(
              (child) => child.moduleRunId as string,
            ),
            ...(queryPlan.moduleRunId
              ? { query_plan_run_id: queryPlan.moduleRunId }
              : {}),
          },
        });
        evidenceChildren.push(candidateFilter);
        if (!candidateFilter.ok) return evidenceChildren;

        const leads: Array<{
          candidateId: string;
          document: JsonObject;
        }> = [];
        for (const candidate of rows(candidateFilter.output.fetch_candidates)) {
          const document = record(candidate.document);
          const candidateId = text(candidate.candidate_id);
          if (!evidenceDocumentKey(document) || !candidateId) continue;
          leads.push({ candidateId, document });
        }
        if (!candidateFilter.moduleRunId && leads.length) {
          throw new Error('候选清理运行没有持久化编号，不能安全进入全文取文。');
        }

        const leadRuns = await mapWithConcurrency(
          leads,
          DOCUMENT_WORK_CONCURRENCY,
          async (lead): Promise<ChildRun[]> => {
            const leadDocumentId = evidenceDocumentKey(lead.document);
            const fetchedResult = await runLabChild({
              code: 'I3_FETCH',
              mode: 'live',
              currentInvestigationId: investigationId,
              claimInvestigationId: firstClaimRowId,
              input: {
                candidate_filter_run_id: candidateFilter.moduleRunId,
                candidate_id: lead.candidateId,
              },
            });
            const fetched: ChildRun = {
              ...fetchedResult,
              documentId: leadDocumentId,
            };
            const result = [fetched];
            const documentId = evidenceDocumentKey(fetched.output);
            if (fetched.ok && documentId && isRetrievedEvidence(fetched.output)) {
              const qualification = await runLabChild({
                code: 'I3_QUALIFY',
                mode: 'live',
                currentInvestigationId: investigationId,
                claimInvestigationId: firstClaimRowId,
                input: { document_id: documentId },
              });
              result.push({ ...qualification, documentId });
            }
            return result;
          },
        );
        evidenceChildren.push(...leadRuns.flat());
        return evidenceChildren;
      };

      if (module.id === 'target') {
        children.push(
          await runLabChild({
            code: 'I1_TARGET_SNAPSHOT',
            mode: 'live',
            currentInvestigationId: investigationId,
            input: {},
          }),
        );
      }

      if (module.id === 'date') {
        children.push(
          await runLabChild({
            code: 'I1_5_CLAIM_DATES',
            mode: 'live',
            currentInvestigationId: investigationId,
            input: {},
          }),
        );
      }

      if (module.id === 'profile') {
        if (!patentClaims.length) {
          throw new Error('当前案件没有识别到独立权利要求，不能总结核心发明点。');
        }
        for (const claim of patentClaims) {
          children.push(await runInventiveProfileForClaim(claim));
        }
      }

      if (module.id === 'query') {
        if (!patentClaims.length) {
          throw new Error('当前案件没有识别到独立权利要求，不能生成检索关键词。');
        }
        const profileChildren = (businessStates.profile?.mode === 'current'
          ? businessStates.profile.children || []
          : []
        ).filter((child) => child.ok && child.code === 'I2_INVENTIVE_PROFILE');
        for (const claim of patentClaims) {
          const claimId = text(claim.claim_id);
          const hasProfile = profileChildren.some(
            (child) => text(child.output.claim_id) === claimId,
          );
          if (!hasProfile) {
            children.push({
              code: 'I2_QUERY_PLAN',
              ok: false,
              status: 'failed',
              output: { claim_id: claimId },
              error: `独立权利要求 ${claimId}：请先运行模块3总结核心发明点，模块4才能生成检索关键词。`,
              raw: {},
            });
            continue;
          }
          children.push(await runQueryPlanForClaim(claim));
        }
      }

      if (module.id === 'evidence') {
        children.push(...await runEvidenceChain(await ensureFirstQueryPlan()));
      }

      if (module.id === 'singleReference') {
        if (!firstClaimRowId) {
          throw new Error('当前案件还没有独立权利要求分析记录。');
        }
        const priorEvidence = businessStates.evidence?.mode === 'current'
          ? businessStates.evidence.children || []
          : [];
        const documentIds = [
          ...new Set(
            priorEvidence
              .filter(
                (child) =>
                  child.ok
                  && child.code === 'I3_FETCH'
                  && isAnalysisReady(child.output),
              )
              .map((child) => evidenceDocumentKey(child.output))
              .filter(Boolean),
          ),
        ];
        if (!documentIds.length) {
          throw new Error(
            '模块5尚未形成可比对的全文。请先完成原文件获取及结构化/OCR；系统不会用标题、摘要或题录代替全文。',
          );
        }
        children.push(...await runModule5Batch({
          currentInvestigationId: investigationId,
          claimInvestigationId: firstClaimRowId,
          documentIds,
        }));
      }

      if (module.id === 'closestPriorArt') {
        if (!firstClaimRowId) {
          throw new Error('当前案件还没有独立权利要求分析记录。');
        }
        const module6Batch = businessStates.singleReference?.module6Batch;
        const module6BatchId = text(module6Batch?.batch_id);
        if (
          !module6BatchId
          || text(module6Batch?.status) !== 'completed'
          || businessStates.singleReference?.ok !== true
        ) {
          throw new Error('请先完成当前目标专利的模块6批次；模块7不会读取历史工作流或其他模块6批次。');
        }
        children.push(
          await runLabChild({
            code: 'I4_C_CLOSEST_PRIOR_ART',
            mode: 'live',
            currentInvestigationId: investigationId,
            claimInvestigationId: firstClaimRowId,
            input: { module6_batch_id: module6BatchId },
          }),
        );
      }

      if (module.id === 'obviousnessPrecheck') {
        if (!firstClaimRowId) {
          throw new Error('当前案件还没有独立权利要求分析记录。');
        }
        const module6Batch = businessStates.singleReference?.module6Batch;
        const module6BatchId = text(module6Batch?.batch_id);
        const closestPriorArt = [...(businessStates.closestPriorArt?.children || [])]
          .reverse()
          .find((child) => child.ok && child.code === 'I4_C_CLOSEST_PRIOR_ART');
        const closestPriorArtRunId = text(closestPriorArt?.moduleRunId);
        if (
          !module6BatchId
          || text(module6Batch?.status) !== 'completed'
          || !closestPriorArtRunId
          || text(closestPriorArt?.output.source_module6_batch_id) !== module6BatchId
        ) {
          throw new Error('请先基于本次模块6批次完成模块7；模块8不会读取历史D1。');
        }
        children.push(
          await runLabChild({
            code: 'I4_O_OBVIOUSNESS_PRECHECK',
            mode: 'live',
            currentInvestigationId: investigationId,
            claimInvestigationId: firstClaimRowId,
            input: {
              module6_batch_id: module6BatchId,
              closest_prior_art_run_id: closestPriorArtRunId,
            },
          }),
        );
      }

      if (module.id === 'gapSearch') {
        if (!firstClaimRowId) {
          throw new Error('当前案件还没有独立权利要求分析记录。');
        }
        const module6Batch = businessStates.singleReference?.module6Batch;
        const module6BatchId = text(module6Batch?.batch_id);
        const closestPriorArt = [...(businessStates.closestPriorArt?.children || [])]
          .reverse()
          .find((child) => child.ok && child.code === 'I4_C_CLOSEST_PRIOR_ART');
        const closestPriorArtRunId = text(closestPriorArt?.moduleRunId);
        const precheck = [...(businessStates.obviousnessPrecheck?.children || [])]
          .reverse()
          .find((child) => child.ok && child.code === 'I4_O_OBVIOUSNESS_PRECHECK');
        const obviousnessPrecheckRunId = text(precheck?.moduleRunId);
        if (
          !module6BatchId
          || text(module6Batch?.status) !== 'completed'
          || !closestPriorArtRunId
          || !obviousnessPrecheckRunId
          || text(closestPriorArt?.output.source_module6_batch_id) !== module6BatchId
          || text(precheck?.output.source_module6_batch_id) !== module6BatchId
          || text(precheck?.output.source_closest_prior_art_run_id)
            !== closestPriorArtRunId
        ) {
          throw new Error('请按本次模块6批次依次完成模块7和模块8；模块9不会读取历史运行。');
        }
        children.push(...await runModule9Batch({
          currentInvestigationId: investigationId,
          claimInvestigationId: firstClaimRowId,
          module6BatchId,
          closestPriorArtRunId,
          obviousnessPrecheckRunId,
        }));
      }

      if (module.id === 'inventiveStep') {
        if (!firstClaimRowId) {
          throw new Error('当前案件还没有独立权利要求分析记录。');
        }
        const module6BatchId = text(
          businessStates.singleReference?.module6Batch?.batch_id,
        );
        const module9Batch = record(businessStates.gapSearch?.module9Batch);
        const module9BatchId = text(module9Batch.batch_id);
        const closestPriorArt = [...(businessStates.closestPriorArt?.children || [])]
          .reverse()
          .find((child) => child.ok && child.code === 'I4_C_CLOSEST_PRIOR_ART');
        const precheck = [...(businessStates.obviousnessPrecheck?.children || [])]
          .reverse()
          .find((child) => child.ok && child.code === 'I4_O_OBVIOUSNESS_PRECHECK');
        const closestPriorArtRunId = text(closestPriorArt?.moduleRunId);
        const obviousnessPrecheckRunId = text(precheck?.moduleRunId);
        if (
          !module6BatchId
          || !module9BatchId
          || !['completed', 'exhausted', 'partial', 'failed'].includes(text(module9Batch.status))
          || text(module9Batch.source_module6_batch_id) !== module6BatchId
          || text(module9Batch.source_closest_prior_art_run_id) !== closestPriorArtRunId
          || text(module9Batch.source_obviousness_precheck_run_id)
            !== obviousnessPrecheckRunId
        ) {
          throw new Error('请先让本次模块9批次收口；模块10不会混入其他批次文献。');
        }
        children.push(
          await runLabChild({
            code: 'I4_I_INVENTIVE_STEP',
            mode: 'live',
            currentInvestigationId: investigationId,
            claimInvestigationId: firstClaimRowId,
            input: {
              module6_batch_id: module6BatchId,
              module9_batch_id: module9BatchId,
              closest_prior_art_run_id: closestPriorArtRunId,
              obviousness_precheck_run_id: obviousnessPrecheckRunId,
              module9_failure_ledger: rows(module9Batch.failure_ledger),
            },
          }),
        );
      }

      if (module.id === 'report') {
        children.push(
          await runLabChild({
            code: 'I5_REPORT',
            mode: 'live',
            currentInvestigationId: investigationId,
            claimInvestigationId: firstClaimRowId,
            input: {},
          }),
        );
      }
      return { children, activeReport };
    },
    [
      claims,
      investigation,
      investigationId,
      loadReport,
      report,
      runLabChild,
      runModule5Batch,
      runModule9Batch,
      businessStates,
    ],
  );

  const runBusiness = useCallback(
    async (module: BusinessModule, mode: RunMode) => {
      if (attachedSessionId) return;
      setBusinessStates((current) => ({
        ...current,
        [module.id]: { phase: 'running', mode },
      }));
      const startedAt = Date.now();
      try {
        const result =
          mode === 'fixture'
            ? {
                children: await runFixtureBusiness(module),
                activeReport: report,
              }
            : await runCurrentBusiness(module);
        if (module.id === 'gapSearch' && mode === 'current') {
          setBusinessStates((current) => ({
            ...current,
            gapSearch: {
              ...(current.gapSearch || { phase: 'done' }),
              elapsedSeconds: (Date.now() - startedAt) / 1000,
              children: result.children,
              reportSnapshot: result.activeReport,
              module9Batch: latestModule9Batch.current,
            },
          }));
          return;
        }
        const summary = summaryForBusiness(
          module.id,
          result.children,
          result.activeReport,
          mode,
        );
        setBusinessStates((current) => ({
          ...current,
          [module.id]: {
            phase: 'done',
            mode,
            ...summary,
            elapsedSeconds: (Date.now() - startedAt) / 1000,
            children: result.children,
            reportSnapshot: result.activeReport,
            module6Batch: current[module.id]?.module6Batch,
          },
        }));
      } catch (caught) {
        setBusinessStates((current) => ({
          ...current,
          [module.id]: {
            phase: 'done',
            mode,
            ok: false,
            headline: '本模块没有得到可用输出',
            facts: [
              friendlyError(
                caught instanceof Error ? caught.message : '模块运行失败',
              ),
              ...(
                module.id === 'singleReference'
                && (current[module.id]?.children || []).length
                  ? ['已提交的逐篇任务和已完成结果仍保留；重新打开本案件即可继续读取。']
                  : []
              ),
            ],
            elapsedSeconds: (Date.now() - startedAt) / 1000,
            children: current[module.id]?.children || [],
            reportSnapshot: current[module.id]?.reportSnapshot || report,
            module6Batch: current[module.id]?.module6Batch,
          },
        }));
      }
    },
    [attachedSessionId, report, runCurrentBusiness, runFixtureBusiness],
  );

  useEffect(() => {
    if (attachedSessionId || !investigationId || !claims.length) return;
    const claimInvestigationId = text(
      scopedClaimRows(claims, investigation, report)[0]?.id,
    );
    if (!claimInvestigationId) return;
    const url = new URL(window.location.href);
    const batchId = url.searchParams.get('module6_batch') || '';
    const recoveryKey = batchId
      || `latest:${investigationId}:${claimInvestigationId}`;
    if (module5RecoveryKey.current === recoveryKey) return;
    module5RecoveryKey.current = recoveryKey;
    const recoverBatch = async () => {
      try {
        await runModule5Batch({
          currentInvestigationId: investigationId,
          claimInvestigationId,
          existingBatchId: batchId || undefined,
        });
      } catch (initialError) {
        throw initialError;
      }
    };
    void recoverBatch().catch((error) => {
      const message = error instanceof Error ? error.message : '模块六单篇比对批次恢复失败';
      if (
        !batchId
        && (
          message.includes('模块六单篇比对批次不存在')
          || message.includes('没有可恢复的模块六')
        )
      ) return;
      setBusinessStates((current) => ({
        ...current,
        singleReference: {
          phase: 'done',
          mode: 'current',
          ok: false,
          headline: '单篇比对运行记录暂时无法读取',
          facts: [
            friendlyError(message),
            '已提交的后台任务不会因此删除；服务恢复后打开同一案件可继续。',
          ],
          children: current.singleReference?.children || [],
          reportSnapshot: current.singleReference?.reportSnapshot || report,
        },
      }));
    });
  }, [
    claims,
    attachedSessionId,
    investigation,
    investigationId,
    report,
    runModule5Batch,
  ]);

  useEffect(() => {
    if (!investigationId) return;
    if (!investigationScope.current) {
      investigationScope.current = investigationId;
      return;
    }
    if (investigationScope.current === investigationId) return;
    investigationScope.current = investigationId;
    module5RecoveryKey.current = '';
    module9RecoveryKey.current = '';
    latestModule9Batch.current = null;
    module9PollToken.current += 1;
    setBusinessStates({});
    const url = new URL(window.location.href);
    url.searchParams.delete('module5_batch');
    url.searchParams.delete('module6_batch');
    url.searchParams.delete('module9_batch');
    window.history.replaceState(null, '', url);
  }, [investigationId]);

  useEffect(() => {
    if (attachedSessionId || !investigationId || !claims.length) return;
    const claimInvestigationId = text(
      scopedClaimRows(claims, investigation, report)[0]?.id,
    );
    if (!claimInvestigationId) return;
    const batchId = new URL(window.location.href).searchParams.get('module9_batch') || '';
    const recoveryKey = `${investigationId}:${claimInvestigationId}:${batchId || 'latest'}`;
    if (module9RecoveryKey.current === recoveryKey) return;
    module9RecoveryKey.current = recoveryKey;
    const recoverBatch = async () => {
      let initialBatch: JsonObject | undefined;
      if (!batchId) {
        try {
          initialBatch = await module9BatchRequest(
            undefined,
            (
              `?investigation_id=${encodeURIComponent(investigationId)}`
              + `&claim_investigation_id=${encodeURIComponent(claimInvestigationId)}`
            ),
          );
        } catch (error) {
          const message = error instanceof Error ? error.message : '';
          if (message.includes('模块九补证批次不存在')) return;
          throw error;
        }
      }
      await runModule9Batch({
        currentInvestigationId: investigationId,
        claimInvestigationId,
        existingBatchId: batchId || undefined,
        initialBatch,
      });
    };
    void recoverBatch().catch((error) => {
      setBusinessStates((current) => ({
        ...current,
        gapSearch: {
          phase: 'done',
          mode: 'current',
          ok: false,
          headline: '补证循环运行记录暂时无法读取',
          facts: [
            friendlyError(error instanceof Error ? error.message : '模块九补证批次恢复失败'),
            '已提交的每轮计划、检索、取文和逐篇比对任务不会删除；服务恢复后打开同一链接可继续。',
          ],
          children: current.gapSearch?.children || [],
          reportSnapshot: current.gapSearch?.reportSnapshot || report,
        },
      }));
    });
  }, [
    claims,
    attachedSessionId,
    investigation,
    investigationId,
    report,
    runModule9Batch,
  ]);

  const targetPatent = useMemo(
    () =>
      Object.keys(record(report?.target_patent)).length
        ? record(report?.target_patent)
        : record(dig(investigation, 'source_snapshot', 'patent_snapshot')),
    [investigation, report],
  );
  const attachedSummary = useMemo<AgentInvaliditySummary | null>(
    () => attachedPayload ? buildAgentInvaliditySummary(attachedPayload) : null,
    [attachedPayload],
  );
  const independentClaims = useMemo(
    () => scopedClaimRows(claims, investigation, report),
    [claims, investigation, report],
  );
  const excludedClaims = useMemo(
    () => excludedClaimRows(claims, investigation, report),
    [claims, investigation, report],
  );
  const terminalClaims = independentClaims.filter((claim) =>
    TERMINAL_CLAIM.has(text(claim.status)),
  );
  const manualClaims = independentClaims.filter(
    (claim) => text(claim.status) === 'needs_human_review',
  );
  const legacyManualClaims = excludedClaims.filter(
    (claim) => text(claim.status) === 'needs_human_review',
  );
  const independentFailedClaims = independentClaims.filter(
    (claim) => text(claim.status) === 'failed',
  );
  const reportClaimRows = useMemo(
    () => reportClaimsInScope(report, investigation),
    [investigation, report],
  );
  const reportDisclosureRows = useMemo(() => {
    const scopedIds = new Set(reportClaimRows.map((claim) => text(claim.id)));
    return rows(report?.feature_disclosures).filter((item) =>
      scopedIds.has(text(item.claim_investigation_id)),
    );
  }, [report, reportClaimRows]);
  const reportDocumentRows = useMemo(() => {
    const documentIds = new Set(
      reportDisclosureRows.map((item) => text(item.document_id)).filter(Boolean),
    );
    return rows(report?.documents).filter((item) =>
      documentIds.has(text(item.id)),
    );
  }, [report, reportDisclosureRows]);
  const progress = independentClaims.length
    ? Math.round((terminalClaims.length / independentClaims.length) * 100)
    : 0;
  const rawInvestigationStatus = text(investigation?.status);
  const effectiveStatus =
    rawInvestigationStatus === 'needs_human_review'
    && manualClaims.length === 0
    && legacyManualClaims.length > 0
      ? independentFailedClaims.length
        ? 'failed'
        : 'partial'
      : rawInvestigationStatus;
  const healthEnvironment =
    text(dig(health, 'isolation', 'environment'))
    || text(health?.environment)
    || 'unknown';
  const oldI2Failure = rows(report?.module_runs).find(
    (run) =>
      text(run.module_code) === 'I2_QUERY_PLAN'
      && text(run.status) === 'failed',
  );
  const benchmarkClaims = rows(benchmarkCase?.claims);
  const benchmarkProfileRuns: ChildRun[] = benchmarkClaims.map((claim) => ({
    code: 'I2_INVENTIVE_PROFILE',
    ok: true,
    status: 'completed',
    output: record(claim.queryPlan),
    raw: {
      module_run: {
        input_snapshot: {
          request: { input: { claim_id: text(claim.claimId) } },
        },
      },
    },
  }));
  const benchmarkQueryRuns: ChildRun[] = benchmarkClaims.map((claim) => ({
    code: 'I2_QUERY_PLAN',
    ok: true,
    status: 'completed',
    output: record(claim.queryPlan),
    raw: {
      module_run: {
        input_snapshot: {
          request: { input: { claim_id: text(claim.claimId) } },
        },
      },
    },
  }));
  const benchmarkAnalysisRuns: ChildRun[] = benchmarkClaims.flatMap((claim) =>
    rows(claim.comparisons).map((item) => {
      const output = record(item.comparison);
      const documentFile = text(item.documentFile).replace(/\.pdf$/i, '');
      const documentId = text(output.publication_number)
        || text(output.document_id)
        || documentFile;
      return {
        code: 'I4_S_SINGLE_REFERENCE',
        ok: true,
        complete: true,
        status: 'completed',
        output: {
          ...output,
          document_id: documentId,
          publication_number: text(output.publication_number) || documentId,
        },
        documentId,
        raw: {
          module_run: {
            input_snapshot: {
              request: {
                input: {
                  claim_id: text(claim.claimId),
                  limitations: record(claim.queryPlan).limitations,
                  document_id: documentId,
                },
              },
            },
          },
        },
      };
    }),
  );

  return (
    <div className="min-h-screen bg-muted/20">
      <header className="border-b bg-background">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          <div className="flex items-center gap-2 font-semibold">
            <FlaskConical className="h-5 w-5" />
            专利无效模块实验室
            <Badge variant="outline">
              {attachedSessionId ? '管理员 · 同任务只读诊断' : '律师版 · 测试环境'}
            </Badge>
          </div>
          <Button asChild size="sm" variant="outline">
            <Link href="/">返回 Agent</Link>
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
        <Alert variant={attachedSessionId || healthEnvironment === 'test' ? 'default' : 'destructive'}>
          <AlertTitle>
            {attachedSessionId
              ? '正在查看 Agent 中的同一个任务'
              : healthEnvironment === 'test' ? '测试服务已连接' : '测试服务未连接'}
          </AlertTitle>
          <AlertDescription>
            {attachedSessionId
              ? '本页只读取同一 analysis session 的调查、模块运行、错误和报告快照；打开或刷新不会复制案件，也不会启动新的检索。'
              : health
              ? '这里按律师工作顺序测试十一个业务阶段。每个阶段只有两种输入：复用上方真实案件，或使用系统内置案例。'
              : friendlyError(pageError || '暂时无法连接测试服务。')}
          </AlertDescription>
        </Alert>

        <Card>
          <CardHeader>
            <CardTitle>{attachedSessionId ? 'Agent 同任务诊断' : '当前真实案件'}</CardTitle>
            <CardDescription>
              {attachedSessionId
                ? '以下数据来自 Agent 当前任务的唯一持久化记录，供开发管理员确认进度、失败节点和证据边界。'
                : '上传一次后，十一个阶段都复用这一个案件；页面链接会保留案件，无需复制编号或重复填写技术参数。'}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {attachedSessionId ? (
              <Alert>
                <AlertTitle>只读诊断模式</AlertTitle>
                <AlertDescription className="space-y-2">
                  <p>Agent session：<code className="select-all">{attachedSessionId}</code></p>
                  <p>如需另建实验案件或手动重跑模块，请先退出同任务诊断；所有显式重跑都会形成新的审计记录。</p>
                  <Button asChild size="sm" variant="outline">
                    <Link href="/test/module-lab">退出同任务诊断</Link>
                  </Button>
                </AlertDescription>
              </Alert>
            ) : (
              <UploadForm
                onSubmit={(input) => void startFlow(input)}
                isAnalyzing={flowBusy}
                uploadEndpoint="/api/invalidity/uploads?environment=test"
              />
            )}

            {flowError ? (
              <Alert variant="destructive">
                <XCircle className="h-4 w-4" />
                <AlertTitle>案件创建或读取失败</AlertTitle>
                <AlertDescription>{flowError}</AlertDescription>
              </Alert>
            ) : null}

            {investigationId ? (
              <div className="space-y-4 rounded-lg border p-4">
                <div className="flex flex-wrap items-center gap-3">
                  {['created', 'queued', 'running'].includes(effectiveStatus) ? (
                    <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
                  ) : effectiveStatus === 'failed' ? (
                    <XCircle className="h-5 w-5 text-red-500" />
                  ) : effectiveStatus === 'partial'
                    || effectiveStatus === 'needs_human_review' ? (
                    <AlertTriangle className="h-5 w-5 text-amber-500" />
                  ) : (
                    <CheckCircle2 className="h-5 w-5 text-green-600" />
                  )}
                  <div>
                    <p className="font-medium">
                      {text(targetPatent.patent_number) || '正在读取专利号'}
                      {text(targetPatent.title) ? `《${text(targetPatent.title)}》` : ''}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      当前口径：仅检索和分析独立权利要求
                      {effectiveStatus ? ` · ${statusLabel(effectiveStatus)}` : ''}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto"
                    onClick={() => {
                      const token = ++pollToken.current;
                      if (attachedSessionId) {
                        void pollAttachedSession(attachedSessionId, token);
                      } else {
                        void pollInvestigation(investigationId, token);
                      }
                    }}
                  >
                    <RefreshCw className="mr-1 h-4 w-4" />
                    刷新
                  </Button>
                </div>

                {attachedSummary ? (
                  <div className="space-y-2 rounded-md border border-violet-200 bg-violet-50/60 p-3" data-module-lab-attached-summary="true">
                    <p className="font-medium text-violet-950">{attachedSummary.headline}</p>
                    <p className="text-sm text-violet-900/80">{attachedSummary.detail}</p>
                    <div className="flex flex-wrap gap-3 text-xs text-violet-900/70">
                      <span>证据资料 {attachedSummary.documentCount} 份</span>
                      <span>逐项披露 {attachedSummary.disclosureCount} 条</span>
                      <span>未解决缺口 {attachedSummary.openGapCount} 项</span>
                      <span>失败运行 {attachedSummary.failedRunCount} 条</span>
                    </div>
                  </div>
                ) : null}

                <div className="space-y-2">
                  <div className="flex flex-wrap gap-3 text-sm text-muted-foreground">
                    <span>
                      独立权利要求：{terminalClaims.length} / {independentClaims.length}
                      {' '}项已到当前终态
                    </span>
                    {manualClaims.length ? (
                      <span className="text-amber-700">
                        {manualClaims.length} 项独立权利要求需要人工确认
                      </span>
                    ) : (
                      <span>独立权利要求没有待人工确认事项</span>
                    )}
                  </div>
                  <Progress value={progress} />
                  <ul className="divide-y rounded-md border text-sm">
                    {independentClaims.length ? (
                      independentClaims.map((claim) => (
                        <li
                          key={text(claim.id) || text(claim.claim_id)}
                          className="flex flex-wrap items-start gap-2 px-3 py-2"
                        >
                          <span className="font-medium">
                            独立权利要求 {text(claim.claim_id) || '?'}
                          </span>
                          <Badge
                            variant={
                              text(claim.status) === 'failed'
                                ? 'destructive'
                                : 'outline'
                            }
                          >
                            {statusLabel(claim.status)}
                          </Badge>
                          {text(claim.terminal_reason) ? (
                            <span className="text-muted-foreground">
                              {friendlyError(text(claim.terminal_reason))}
                            </span>
                          ) : null}
                        </li>
                      ))
                    ) : (
                      <li className="px-3 py-2 text-muted-foreground">
                        正在建立独立权利要求记录…
                      </li>
                    )}
                  </ul>
                </div>

                {legacyManualClaims.length ? (
                  <Alert>
                    <AlertTriangle className="h-4 w-4" />
                    <AlertTitle>旧版“需要人工确认”已找到</AlertTitle>
                    <AlertDescription>
                      旧记录中的人工确认来自从属权利要求
                      {' '}
                      {legacyManualClaims
                        .map((claim) => text(claim.claim_id))
                        .filter(Boolean)
                        .join('、')}
                      。当前规则不再分析从属权利要求，因此这些记录已从案件状态、进度和报告中排除。
                      {independentFailedClaims.length
                        ? ` 独立权利要求 ${independentFailedClaims.map((claim) => text(claim.claim_id)).join('、')} 的实际状态是“失败”。`
                        : ''}
                      {oldI2Failure
                        ? ` 旧版失败发生在检索方案环节：${friendlyError(text(oldI2Failure.error_message) || text(oldI2Failure.error_code))}`
                        : ''}
                    </AlertDescription>
                  </Alert>
                ) : null}

                <Accordion type="single" collapsible>
                  <AccordionItem value="case-tech">
                    <AccordionTrigger className="text-xs text-muted-foreground">
                      技术审计信息
                    </AccordionTrigger>
                    <AccordionContent>
                      <p className="text-xs text-muted-foreground">
                        案件编号：<code className="select-all">{investigationId}</code>
                      </p>
                      {attachedSessionId ? (
                        <p className="mt-1 text-xs text-muted-foreground">
                          Agent session：<code className="select-all">{attachedSessionId}</code> · 同任务只读
                        </p>
                      ) : null}
                    </AccordionContent>
                  </AccordionItem>
                </Accordion>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                尚未选择真实案件。你仍可直接运行下方任一模块的内置案例。
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>五个真实无效案件的内置盲测</CardTitle>
            <CardDescription>
              五案现已按十一个律师工作阶段同步展示。模块1—7读取目标专利、r5 检索画像和 v50 单篇比对等冻结事实；模块8展示显而易见性预分析，模块9—11只基于冻结证据形成补证、组合与报告预览，不把决定书结论导入生产分析。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <select
                className="h-9 min-w-[300px] rounded-md border bg-background px-3 text-sm"
                value={benchmarkCaseId}
                onChange={(event) => {
                  setBenchmarkCaseId(event.target.value);
                  setBenchmarkCase(null);
                  setBenchmarkView(null);
                }}
              >
                {benchmarkCases.map((item) => (
                  <option key={text(item.caseId)} value={text(item.caseId)}>
                    {text(item.label)} · {text(dig(item, 'target', 'publicationNumber'))}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {BUSINESS_MODULES.map((module) => {
                const Icon = module.icon;
                return (
                  <Button
                    key={module.id}
                    size="sm"
                    variant={benchmarkView === module.id ? 'default' : 'outline'}
                    className="h-auto min-h-10 justify-start whitespace-normal text-left"
                    disabled={!benchmarkCaseId || benchmarkLoading}
                    onClick={() => void loadBenchmark(module.id)}
                  >
                    {benchmarkLoading && benchmarkView === module.id
                      ? <Loader2 className="mr-1 h-4 w-4 shrink-0 animate-spin" />
                      : <Icon className="mr-1 h-4 w-4 shrink-0" />}
                    模块{module.step} · {module.name}
                  </Button>
                );
              })}
            </div>
            {benchmarkError ? (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertTitle>内置案例暂时不可读</AlertTitle>
                <AlertDescription>{benchmarkError}</AlertDescription>
              </Alert>
            ) : null}
            {benchmarkCase && benchmarkView ? (
              <div className="space-y-4 rounded-lg border p-4">
                <p className="text-sm text-muted-foreground">
                  已载入模块 {BUSINESS_MODULES.find((module) => module.id === benchmarkView)?.step}
                  {' · '}{text(benchmarkCase.label)}。输入和输出默认收起，按需展开查看。
                </p>
                <Accordion
                  type="multiple"
                  className="rounded-lg border bg-background px-4"
                  data-module-lab-benchmark-disclosures="true"
                >
                  <AccordionItem value="benchmark-input">
                    <AccordionTrigger className="text-left hover:no-underline">
                      <span>
                        <span className="font-medium">本次实际输入</span>
                        <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                          {text(dig(benchmarkCase, 'target', 'publicationNumber'))}
                          {' · 独立权利要求 '}{benchmarkClaims.length} 项
                        </span>
                      </span>
                    </AccordionTrigger>
                    <AccordionContent className="space-y-2">
                      <p className="text-sm">
                        {text(benchmarkCase.label)} · {text(dig(benchmarkCase, 'target', 'publicationNumber'))}
                        {text(dig(benchmarkCase, 'target', 'title'))
                          ? `《${text(dig(benchmarkCase, 'target', 'title'))}》`
                          : ''}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        独立权利要求 {benchmarkClaims.map((claim) => text(claim.claimId)).join('、')}；
                        {benchmarkView === 'singleReference'
                          ? `逐份送入 ${benchmarkAnalysisRuns.length} 组“独立权利要求 × 单篇全文”，不拼接文献。`
                          : benchmarkView === 'evidence'
                            ? `展示 ${rows(benchmarkCase.sourceDocuments).length} 份冻结源文献及日期。`
                            : ['closestPriorArt', 'obviousnessPrecheck', 'gapSearch', 'inventiveStep', 'report'].includes(benchmarkView)
                              ? '只复用当前冻结证据形成可审计预览，不伪造尚未冻结的模块结果。'
                              : '输入只含目标专利冻结事实。'}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        冻结版本：模块3/4 {text(dig(benchmarkCase, 'freezeVersions', 'planVersion'))}；
                        模块6及后续证据预览 {text(dig(benchmarkCase, 'freezeVersions', 'comparisonVersion'))}。
                      </p>
                    </AccordionContent>
                  </AccordionItem>
                  <AccordionItem value="benchmark-output">
                    <AccordionTrigger className="text-left hover:no-underline">
                      <span>
                        <span className="font-medium">
                          模块 {BUSINESS_MODULES.find((module) => module.id === benchmarkView)?.step} 输出结果
                        </span>
                        <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                          点击查看冻结案例的完整结果
                        </span>
                      </span>
                    </AccordionTrigger>
                    <AccordionContent>
                      <BenchmarkStageDetails
                        moduleId={benchmarkView}
                        benchmarkCase={benchmarkCase}
                        profileRuns={benchmarkProfileRuns}
                        queryRuns={benchmarkQueryRuns}
                        analysisRuns={benchmarkAnalysisRuns}
                      />
                    </AccordionContent>
                  </AccordionItem>
                </Accordion>
              </div>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>十一个律师工作阶段</CardTitle>
            <CardDescription>
              {attachedSessionId
                ? '这里按十一个阶段映射同一 Agent 任务已经持久化的 module_runs；只显示进度、错误与审计事实，不在诊断视图创建新运行。'
                : '不填写 JSON，不选择 provider，也不确认中间技术状态。点击一次，直接看这个模块吃到的业务输入和给出的业务输出。'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ol className="space-y-4">
              {BUSINESS_MODULES.map((module) => {
                const state = businessStates[module.id] || {
                  phase: 'idle' as const,
                };
                const module9Batch = record(state.module9Batch);
                const module9Iteration = numberValue(module9Batch.current_iteration) || 1;
                const hasModule9Batch = module.id === 'gapSearch'
                  && Boolean(text(module9Batch.batch_id));
                const Icon = module.icon;
                return (
                  <li key={module.id} className="rounded-lg border p-4">
                    <Accordion
                      type="single"
                      collapsible
                      data-module-lab-module-disclosure={module.id}
                    >
                      <AccordionItem value={`module-${module.id}`} className="border-0">
                        <AccordionTrigger className="py-0 text-left hover:no-underline">
                          <span className="flex min-w-0 flex-1 items-start gap-3 pr-2">
                            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10">
                              <Icon className="h-4 w-4" />
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="flex flex-wrap items-center gap-2">
                                <Badge variant="secondary">模块 {module.step}</Badge>
                                <span className="font-semibold">{module.name}</span>
                                <Badge
                                  variant={state.phase === 'done' && state.ok === false
                                    ? 'destructive'
                                    : 'outline'}
                                >
                                  {state.phase === 'idle'
                                    ? attachedSessionId ? '未开始' : '未测试'
                                    : state.phase === 'running'
                                      ? '运行中'
                                      : state.ok
                                        ? '已完成'
                                        : '需处理'}
                                </Badge>
                              </span>
                              <span className="mt-1 block text-sm font-normal">{module.question}</span>
                              {state.phase === 'done' && state.headline ? (
                                <span className="mt-1 line-clamp-2 block text-xs font-normal text-muted-foreground">
                                  {state.headline}
                                </span>
                              ) : null}
                            </span>
                          </span>
                        </AccordionTrigger>
                        <AccordionContent className="pt-4">
                          <div className="grid gap-2 text-xs text-muted-foreground sm:grid-cols-2">
                            <p className="rounded bg-muted/50 px-2 py-1.5">{module.input}</p>
                            <p className="rounded bg-muted/50 px-2 py-1.5">{module.output}</p>
                          </div>

                    <div
                      id={module.id === 'gapSearch' ? 'module9-actions' : undefined}
                      className="mt-3 flex scroll-mt-6 flex-wrap gap-2"
                    >
                      <Button
                        size="sm"
                        disabled={Boolean(attachedSessionId) || state.phase === 'running' || !investigationId}
                        onClick={() => void runBusiness(module, 'current')}
                      >
                        {state.phase === 'running' && state.mode === 'current' ? (
                          <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        ) : (
                          <Play className="mr-1 h-4 w-4" />
                        )}
                        {module.id === 'gapSearch'
                          ? hasModule9Batch
                            ? '重新生成五轮并执行第 1 轮'
                            : '生成五轮并执行第 1 轮'
                          : '用当前案件测试'}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={Boolean(attachedSessionId) || state.phase === 'running'}
                        onClick={() => void runBusiness(module, 'fixture')}
                      >
                        {state.phase === 'running' && state.mode === 'fixture' ? (
                          <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                        ) : (
                          <Play className="mr-1 h-4 w-4" />
                        )}
                        用内置示例测试
                      </Button>
                      {module.id === 'gapSearch'
                      && !attachedSessionId
                      && state.phase === 'done'
                      && module9Batch.can_retry_failed === true ? (
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => void operateModule9Batch('retry_failed')}
                        >
                          <RefreshCw className="mr-1 h-4 w-4" />
                          仅重试本轮失败文献
                        </Button>
                      ) : null}
                      {module.id === 'gapSearch'
                      && !attachedSessionId
                      && state.phase === 'done'
                      && module9Batch.can_continue === true ? (
                        <Button
                          size="sm"
                          onClick={() => void operateModule9Batch('continue_next_round')}
                        >
                          <Play className="mr-1 h-4 w-4" />
                          执行第 {Math.min(5, module9Iteration + 1)} 轮
                        </Button>
                      ) : null}
                      {module.id === 'gapSearch'
                      && !attachedSessionId
                      && state.phase === 'done'
                      && module9Batch.can_continue_with_failures === true ? (
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => void operateModule9Batch('continue_next_round', true)}
                        >
                          <Play className="mr-1 h-4 w-4" />
                          保留失败并执行第 {Math.min(5, module9Iteration + 1)} 轮
                        </Button>
                      ) : null}
                      {module.id === 'gapSearch'
                      && !attachedSessionId
                      && state.phase === 'done'
                      && module9Batch.can_continue !== true
                      && module9Batch.can_continue_with_failures !== true
                      && ['completed', 'exhausted', 'partial', 'failed'].includes(
                        text(module9Batch.status),
                      ) ? (
                        <Button size="sm" variant="outline" disabled>
                          <Play className="mr-1 h-4 w-4" />
                          {module9Iteration >= 5 ? '已到第 5 轮，无下一轮' : '当前不能执行下一轮'}
                        </Button>
                      ) : null}
                    </div>
                    {module.id === 'gapSearch' ? (
                      <div className="mt-2 space-y-2 text-xs text-muted-foreground">
                        <p>
                          “生成五轮并执行第 1 轮”会为当前目标专利创建新的补证批次，不删除历史；系统先一次生成并冻结五轮检索词，再只执行第 1 轮。已有批次收口后，可重新生成新矩阵，或直接执行已冻结的下一轮。第 1 轮仍要求当前案件已经完成模块7和模块8。
                        </p>
                        <p>五轮不是同一词池换序，而是按下列语义视角逐轮扩展：</p>
                        <ol className="list-decimal space-y-0.5 pl-5">
                          {MODULE9_GAP_STRATEGIES.map((item) => (
                            <li key={item}>{item.replace(/^\u7b2c\d轮：/, '')}</li>
                          ))}
                        </ol>
                      </div>
                    ) : null}
                    {module.id === 'gapSearch'
                    && state.phase === 'done'
                    && text(module9Batch.continuation_reason) ? (
                      <p className="mt-2 text-xs text-muted-foreground">
                        下一轮状态：{text(module9Batch.continuation_reason)}
                      </p>
                    ) : null}

                    {state.phase === 'idle' ? (
                      <p className="mt-3 flex items-center gap-1 text-xs text-muted-foreground">
                        <CircleDashed className="h-3 w-3" />
                        {attachedSessionId ? '同一任务尚未进入本阶段' : '尚未测试'}
                      </p>
                    ) : null}
                    {state.phase === 'running' ? (
                      <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        正在运行这个业务模块；需要联网的模块可能要等几分钟。
                      </p>
                    ) : null}
                    {state.phase === 'done' ? (
                      <div
                        className={`mt-3 rounded-md border p-3 text-sm ${
                          state.ok
                            ? 'border-green-200 bg-green-50 dark:border-green-900 dark:bg-green-950/30'
                            : 'border-red-200 bg-red-50 dark:border-red-900 dark:bg-red-950/30'
                        }`}
                      >
                        <div className="flex items-start gap-2">
                          {state.ok ? (
                            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
                          ) : (
                            <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
                          )}
                          <div className="min-w-0 flex-1 space-y-2">
                            <p className="font-medium">{state.headline}</p>
                            <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
                              {(state.facts || []).map((fact, index) => (
                                <li key={`${module.id}-${index}`}>{fact}</li>
                              ))}
                            </ul>
                            <p className="text-xs text-muted-foreground">
                              输入方式：
                              {state.mode === 'current'
                                ? '当前真实案件'
                                : '系统内置案例'}
                              {typeof state.elapsedSeconds === 'number'
                                ? ` · 用时 ${state.elapsedSeconds.toFixed(1)} 秒`
                                : ''}
                            </p>
                            <Accordion
                              type="multiple"
                              className="rounded-lg border bg-background px-3"
                              data-module-lab-result-disclosures={module.id}
                            >
                              <AccordionItem value="business-input">
                                <AccordionTrigger className="py-2 text-left hover:no-underline">
                                  <span>
                                    <span className="font-medium">本次实际输入</span>
                                    <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                                      点击查看本次运行实际读取的案件事实
                                    </span>
                                  </span>
                                </AccordionTrigger>
                                <AccordionContent className="pt-1">
                                  <BusinessInputDetails
                                    moduleId={module.id}
                                    state={state}
                                    fallbackReport={state.reportSnapshot || report}
                                    investigation={investigation}
                                    allBusinessStates={businessStates}
                                  />
                                </AccordionContent>
                              </AccordionItem>
                              <AccordionItem value="business-output">
                                <AccordionTrigger className="py-2 text-left hover:no-underline">
                                  <span>
                                    <span className="font-medium">本次输出结果</span>
                                    <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                                      点击查看完整业务结果和逐条明细
                                    </span>
                                  </span>
                                </AccordionTrigger>
                                <AccordionContent className="pt-1">
                                  <BusinessResultDetails
                                  moduleId={module.id}
                                  state={state}
                                  fallbackReport={state.reportSnapshot || report}
                                  attachedSessionId={attachedSessionId || undefined}
                                  profileFallbackRuns={businessStates.query?.children}
                                  />
                                </AccordionContent>
                              </AccordionItem>
                            </Accordion>
                            {state.children?.length ? (
                              <Accordion type="single" collapsible>
                                <AccordionItem value="technical" className="border-0">
                                  <AccordionTrigger className="py-1 text-xs text-muted-foreground">
                                    技术运行记录（供 Codex 排错）
                                  </AccordionTrigger>
                                  <AccordionContent>
                                    <div className="space-y-2">
                                      {state.children.map((child) => (
                                        <div key={`${child.code}-${text(child.raw.id)}`} className="rounded border p-2">
                                          <div className="flex flex-wrap gap-2 text-xs">
                                            <code>{child.code}</code>
                                            {child.roundIteration ? (
                                              <Badge variant="secondary">第 {child.roundIteration} 轮</Badge>
                                            ) : null}
                                            {child.attemptNumber && child.attemptNumber > 1 ? (
                                              <span className="text-muted-foreground">
                                                轮内尝试 {child.attemptNumber}
                                                {child.isCurrentAttempt === false ? '（历史）' : ''}
                                              </span>
                                            ) : null}
                                            <Badge variant={child.ok ? 'outline' : 'destructive'}>
                                              {statusLabel(child.status)}
                                            </Badge>
                                            {providerFromRun(record(child.raw.module_run)) ? (
                                              <span className="text-muted-foreground">
                                                来源：{providerFromRun(record(child.raw.module_run))}
                                              </span>
                                            ) : null}
                                            {child.attempts ? (
                                              <span className="text-muted-foreground">
                                                尝试 {child.attempts} 次
                                              </span>
                                            ) : null}
                                          </div>
                                          <pre className="mt-2 max-h-64 overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">
                                            {JSON.stringify(child.raw, null, 2)}
                                          </pre>
                                        </div>
                                      ))}
                                    </div>
                                  </AccordionContent>
                                </AccordionItem>
                              </Accordion>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    ) : null}
                        </AccordionContent>
                      </AccordionItem>
                    </Accordion>
                  </li>
                );
              })}
            </ol>
          </CardContent>
        </Card>

        {report || reportLoading || reportError ? (
          <Card>
            <CardHeader>
              <CardTitle>当前案件报告摘要</CardTitle>
              <CardDescription>
                普通视图只统计独立权利要求；历史从属权利要求记录仍留在技术审计数据中。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {reportLoading ? (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在读取报告…
                </p>
              ) : null}
              {reportError ? (
                <Alert>
                  <AlertTriangle className="h-4 w-4" />
                  <AlertTitle>报告暂不可用</AlertTitle>
                  <AlertDescription>{reportError}</AlertDescription>
                </Alert>
              ) : null}
              {report ? (
                <>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div className="rounded border p-3">
                      <p className="text-xs text-muted-foreground">独立权利要求</p>
                      <p className="text-2xl font-semibold">{reportClaimRows.length}</p>
                    </div>
                    <div className="rounded border p-3">
                      <p className="text-xs text-muted-foreground">候选资料</p>
                      <p className="text-2xl font-semibold">{reportDocumentRows.length}</p>
                    </div>
                    <div className="rounded border p-3">
                      <p className="text-xs text-muted-foreground">逐项披露记录</p>
                      <p className="text-2xl font-semibold">
                        {reportDisclosureRows.length}
                      </p>
                    </div>
                  </div>
                  <Accordion
                    type="single"
                    collapsible
                    className="rounded border px-3"
                    data-module-lab-report-summary-disclosure="true"
                  >
                    <AccordionItem value="report-claims" className="border-0">
                      <AccordionTrigger className="py-2 text-left hover:no-underline">
                        <span>
                          <span className="font-medium">逐项权利要求状态</span>
                          <span className="mt-0.5 block text-xs font-normal text-muted-foreground">
                            {reportClaimRows.length} 项，默认收起
                          </span>
                        </span>
                      </AccordionTrigger>
                      <AccordionContent>
                        <ul className="divide-y rounded border text-sm">
                          {reportClaimRows.length ? (
                            reportClaimRows.map((claim) => (
                              <li key={text(claim.id)} className="space-y-1 px-3 py-2">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="font-medium">
                                    独立权利要求 {text(claim.claim_id)}
                                  </span>
                                  <Badge
                                    variant={
                                      text(claim.status) === 'failed'
                                        ? 'destructive'
                                        : 'outline'
                                    }
                                  >
                                    {statusLabel(claim.status)}
                                  </Badge>
                                  {text(claim.critical_date) ? (
                                    <span className="text-xs text-muted-foreground">
                                      关键日 {text(claim.critical_date)}
                                    </span>
                                  ) : null}
                                </div>
                                {text(claim.terminal_reason) ? (
                                  <p className="text-muted-foreground">
                                    {friendlyError(text(claim.terminal_reason))}
                                  </p>
                                ) : null}
                              </li>
                            ))
                          ) : (
                            <li className="px-3 py-2 text-muted-foreground">
                              报告中尚无独立权利要求结果。
                            </li>
                          )}
                        </ul>
                      </AccordionContent>
                    </AccordionItem>
                  </Accordion>
                </>
              ) : null}
            </CardContent>
          </Card>
        ) : null}

        <Alert>
          <AlertTitle>测试页不要求律师确认中间技术状态</AlertTitle>
          <AlertDescription>
            如果结果不符合预期，直接把案件链接和模块名称交给 Codex 修改即可。日期缺失、外部服务无权限或证据不足会作为模块输出说明，不会在这里增加表单让你逐项确认。
          </AlertDescription>
        </Alert>
      </main>
    </div>
  );
}
