'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  CheckCircle2,
  Circle,
  FileSearch,
  FlaskConical,
  Loader2,
  Search,
  ShieldCheck,
  Upload,
} from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  buildPatsnapSearchPlan,
  isJsonObject,
  moduleOutput,
  moduleRunId,
  moduleRunStatus,
  patsnapSearchResult,
  selectCriticalDate,
  targetPatentSummary,
  type CriticalDateSelection,
  type JsonObject,
  type PatsnapSearchPlan,
  type PatsnapSearchResult,
  type TargetPatentSummary,
} from '@/lib/invalidity-p002-test-flow';

type FlowPhase =
  | 'idle'
  | 'uploading'
  | 'snapshot'
  | 'query-plan'
  | 'p002-search'
  | 'completed'
  | 'failed';

type RunIds = {
  snapshot?: string;
  queryPlan?: string;
  p002?: string;
  p002Runs?: string[];
};

type PatsnapAttempt = {
  plan: PatsnapSearchPlan;
  result: PatsnapSearchResult;
  runId: string;
};

const PHASES: Array<{ id: FlowPhase; label: string; description: string }> = [
  { id: 'uploading', label: '上传并创建测试调查', description: '仅写入 TEST 上传根' },
  { id: 'snapshot', label: '解析目标专利', description: '冻结权利要求与附图，不启动完整 I0' },
  { id: 'query-plan', label: 'GLM-4.6V 图文规划', description: '生成固定五组首轮检索线' },
  { id: 'p002-search', label: '智慧芽 P002 检索', description: '逐组独立 run；不做自动扩展' },
  { id: 'completed', label: '展示候选线索', description: 'lead，不是 CC 证据或无效结论' },
];

const PHASE_ORDER = new Map(PHASES.map((item, index) => [item.id, index]));
const POLL_INTERVAL_MS = 2_000;
const POLL_TIMEOUT_MS = 12 * 60 * 1_000;

function errorMessage(data: JsonObject, fallback: string): string {
  const detail = data.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (isJsonObject(detail)) {
    const code = String(detail.code || '').trim();
    const message = String(detail.message || detail.error || '').trim();
    if (code || message) return [code, message].filter(Boolean).join('：');
  }
  const error = String(data.error || data.message || '').trim();
  return error || fallback;
}

function rows(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter(isJsonObject) : [];
}

async function jsonRequest(path: string, init?: RequestInit): Promise<JsonObject> {
  const response = await fetch(`/api/test/invalidity/${path.replace(/^\//, '')}`, {
    ...init,
    headers: init?.body
      ? { 'Content-Type': 'application/json', ...(init.headers || {}) }
      : init?.headers,
    cache: 'no-store',
  });
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    throw new Error(`测试服务返回非 JSON 响应（HTTP ${response.status}）`);
  }
  const data = await response.json() as JsonObject;
  if (!response.ok) throw new Error(errorMessage(data, `HTTP ${response.status}`));
  return data;
}

async function waitForModuleRun(
  runId: string,
  label: string,
  onUpdate: (value: JsonObject) => void,
): Promise<JsonObject> {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const details = await jsonRequest(`v1/lab/module-runs/${encodeURIComponent(runId)}`);
    onUpdate(details);
    const state = moduleRunStatus(details);
    if (state.terminal) {
      if (!state.successful) {
        throw new Error(`${label}失败：${state.error || `run 状态 ${state.runStatus}`}`);
      }
      return details;
    }
    await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
  }
  throw new Error(`${label}等待超过 12 分钟；run ${runId} 仍可在高级实验室中继续查询状态`);
}

function phaseState(current: FlowPhase, candidate: FlowPhase): 'pending' | 'running' | 'done' {
  const currentIndex = PHASE_ORDER.get(current) ?? -1;
  const candidateIndex = PHASE_ORDER.get(candidate) ?? -1;
  if (current === 'failed') return 'pending';
  if (candidateIndex < currentIndex || current === 'completed') return 'done';
  if (candidate === current) return 'running';
  return 'pending';
}

function formatSeconds(value: number): string {
  const minutes = Math.floor(value / 60);
  const seconds = value % 60;
  return minutes > 0 ? `${minutes}分${seconds}秒` : `${seconds}秒`;
}

export default function InvalidityP002TestPage() {
  const [health, setHealth] = useState<JsonObject | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState<FlowPhase>('idle');
  const [error, setError] = useState<string | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [investigationId, setInvestigationId] = useState('');
  const [runIds, setRunIds] = useState<RunIds>({});
  const [target, setTarget] = useState<TargetPatentSummary | null>(null);
  const [criticalDate, setCriticalDate] = useState<CriticalDateSelection | null>(null);
  const [searchPlans, setSearchPlans] = useState<PatsnapSearchPlan[]>([]);
  const [searchAttempts, setSearchAttempts] = useState<PatsnapAttempt[]>([]);
  const [result, setResult] = useState<PatsnapSearchResult | null>(null);
  const [latestRun, setLatestRun] = useState<JsonObject | null>(null);

  const healthEnvironment = useMemo(() => {
    const isolation = isJsonObject(health?.isolation) ? health.isolation : {};
    return String(isolation.environment || health?.environment || 'unknown');
  }, [health]);

  useEffect(() => {
    let cancelled = false;
    void jsonRequest('health')
      .then((value) => {
        if (cancelled) return;
        setHealth(value);
        setHealthError(null);
      })
      .catch((caught) => {
        if (cancelled) return;
        setHealth(null);
        setHealthError(caught instanceof Error ? caught.message : '测试服务不可达');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (startedAt === null) return undefined;
    const update = () => setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1_000));
    update();
    const timer = window.setInterval(update, 1_000);
    return () => window.clearInterval(timer);
  }, [startedAt]);

  const createModuleRun = async (
    moduleCode: 'I1_TARGET_SNAPSHOT' | 'I2_QUERY_PLAN' | 'I3_PATENT_SEARCH',
    id: string,
    input: JsonObject,
  ): Promise<string> => {
    const created = await jsonRequest('v1/lab/module-runs', {
      method: 'POST',
      body: JSON.stringify({
        contract_version: 'v1',
        module_code: moduleCode,
        input_mode: 'live',
        investigation_id: id,
        claim_investigation_id: null,
        input,
        idempotency_key: crypto.randomUUID(),
        force_recompute: true,
      }),
    });
    const runId = moduleRunId(created);
    if (!runId) throw new Error(`${moduleCode} 未返回 module_run_id`);
    return runId;
  };

  const start = async () => {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    setPhase('uploading');
    setElapsedSeconds(0);
    const started = Date.now();
    setStartedAt(started);
    setInvestigationId('');
    setRunIds({});
    setTarget(null);
    setCriticalDate(null);
    setSearchPlans([]);
    setSearchAttempts([]);
    setResult(null);
    setLatestRun(null);

    try {
      if (healthEnvironment !== 'test') {
        throw new Error('测试后端未确认 environment=test，已拒绝运行');
      }

      const form = new FormData();
      form.append('file', file);
      const uploadResponse = await fetch('/api/invalidity/uploads?environment=test', {
        method: 'POST',
        body: form,
      });
      const uploaded = await uploadResponse.json() as JsonObject;
      if (!uploadResponse.ok || !uploaded.fileKey) {
        throw new Error(errorMessage(uploaded, '目标专利上传失败'));
      }
      const fileKey = String(uploaded.fileKey);
      const investigationKey = crypto.randomUUID();
      const created = await jsonRequest('v1/investigations', {
        method: 'POST',
        body: JSON.stringify({
          contract_version: 'v1',
          analysis_session_id: `invalidity_p002_${crypto.randomUUID()}`,
          source_file_key: fileKey,
          source_url: null,
          max_rounds: 5,
          provider_mode: 'live',
          idempotency_key: investigationKey,
        }),
      });
      const id = String(created.investigation_id || '');
      if (!id) throw new Error('测试服务未返回 investigation_id');
      setInvestigationId(id);

      setPhase('snapshot');
      const snapshotRunId = await createModuleRun(
        'I1_TARGET_SNAPSHOT',
        id,
        {},
      );
      setRunIds((current) => ({ ...current, snapshot: snapshotRunId }));
      const snapshotRun = await waitForModuleRun(
        snapshotRunId,
        '目标专利解析',
        setLatestRun,
      );
      setLatestRun(snapshotRun);

      const investigation = await jsonRequest(`v1/investigations/${encodeURIComponent(id)}`);
      const parsedTarget = targetPatentSummary(investigation);
      const selectedDate = selectCriticalDate(parsedTarget);
      setTarget(parsedTarget);
      setCriticalDate(selectedDate);

      setPhase('query-plan');
      const queryInput = {
        claim_id: parsedTarget.claimId,
        expanded_claim_text: parsedTarget.claimText,
        iteration_number: 1,
        round_kind: 'initial',
        max_queries: 5,
      };
      const queryRunId = await createModuleRun(
        'I2_QUERY_PLAN',
        id,
        queryInput,
      );
      setRunIds((current) => ({ ...current, queryPlan: queryRunId }));
      const queryRun = await waitForModuleRun(
        queryRunId,
        'GLM-4.6V 图文查询规划',
        setLatestRun,
      );
      const plannedQueries = rows(moduleOutput(queryRun).queries).filter((query) => (
        String(query.provider_kind || '') === 'patent'
        && String(query.date_channel || 'ordinary_prior_art') === 'ordinary_prior_art'
        && String(query.query_id || '')
      ));
      if (plannedQueries.length < 1 || plannedQueries.length > 5) {
        throw new Error(`I2 固定首轮专利检索线数量异常：${plannedQueries.length}`);
      }
      const plans = plannedQueries.map((plannedQuery) => buildPatsnapSearchPlan(
        queryRun,
        selectedDate.date,
        10,
        'balanced',
        'inventive_point_precision',
        String(plannedQuery.query_id),
      ));
      if (!/glm-4\.6v/i.test(plans[0].model)) {
        throw new Error(`I2 实际模型不是 glm-4.6v 系列：${plans[0].model || '未返回模型名'}`);
      }
      if (plans[0].usedTargetImages < 1) {
        throw new Error('I2 未确认使用目标附图，已拒绝继续智慧芽检索');
      }
      setSearchPlans(plans);

      setPhase('p002-search');
      const attempts: PatsnapAttempt[] = [];
      const p002RunIds: string[] = [];
      for (const [index, plan] of plans.entries()) {
        const p002RunId = await createModuleRun(
          'I3_PATENT_SEARCH',
          id,
          plan.input,
        );
        p002RunIds.push(p002RunId);
        setRunIds((current) => ({
          ...current,
          p002: current.p002 || p002RunId,
          p002Runs: [...p002RunIds],
        }));
        const p002Run = await waitForModuleRun(
          p002RunId,
          `智慧芽 P002 检索（${index + 1}/${plans.length}）`,
          setLatestRun,
        );
        const attemptResult = patsnapSearchResult(p002Run);
        if (attemptResult.actualProvider !== 'patsnap' || !attemptResult.networkUsed) {
          throw new Error(`P002 第 ${index + 1} 组结果没有通过真实 provider/network 审计`);
        }
        attempts.push({ plan, result: attemptResult, runId: p002RunId });
        setSearchAttempts([...attempts]);
        setLatestRun(p002Run);
      }
      const candidates = new Map<string, PatsnapSearchResult['candidates'][number]>();
      for (const attempt of attempts) {
        for (const candidate of attempt.result.candidates) {
          const key = candidate.publicationNumber || candidate.externalId;
          if (key && !candidates.has(key)) candidates.set(key, candidate);
        }
      }
      const totals = attempts
        .map((attempt) => attempt.result.totalResultCount)
        .filter((value): value is number => value !== null);
      const artifacts = new Map<string, PatsnapSearchResult['artifacts'][number]>();
      for (const attempt of attempts) {
        for (const artifact of attempt.result.artifacts) {
          if (artifact.sha256 && !artifacts.has(artifact.sha256)) {
            artifacts.set(artifact.sha256, artifact);
          }
        }
      }
      setResult({
        actualProvider: 'patsnap',
        networkUsed: true,
        returnedCount: candidates.size,
        totalResultCount: totals.length > 0 ? Math.max(...totals) : null,
        candidates: [...candidates.values()],
        artifacts: [...artifacts.values()],
      });
      setPhase('completed');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '测试链路运行失败');
      setPhase('failed');
    } finally {
      setElapsedSeconds(Math.floor((Date.now() - started) / 1_000));
      setStartedAt(null);
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-background to-muted/30">
      <header className="border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex min-h-14 max-w-7xl flex-wrap items-center justify-between gap-3 px-6 py-2">
          <div className="flex items-center gap-2 font-semibold">
            <FlaskConical className="h-5 w-5 text-primary" />
            专利无效检索测试版
            <Badge variant="outline">TEST / 5209</Badge>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button asChild size="sm" variant="outline"><Link href="/">返回首页</Link></Button>
            <Button asChild size="sm" variant="outline"><Link href="/test/module-lab">高级模块实验室</Link></Button>
            <Button asChild size="sm" variant="ghost"><Link href="/">返回 Agent</Link></Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
        <Alert variant={healthEnvironment === 'test' ? 'default' : 'destructive'}>
          <ShieldCheck className="h-4 w-4" />
          <AlertTitle>隔离状态：{healthEnvironment}</AlertTitle>
          <AlertDescription>
            本页只使用 TEST 上传根、测试代理和 5209；不会回落到正式版 5109，也不会调用完整
            I0、P012、P018 或 P019。正式版缺少配置不会影响本页。
          </AlertDescription>
        </Alert>

        {healthError ? (
          <Alert variant="destructive">
            <AlertTitle>测试服务不可达</AlertTitle>
            <AlertDescription>{healthError}</AlertDescription>
          </Alert>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileSearch className="h-5 w-5" />
              上传目标专利并执行受约束的真实智慧芽检索
            </CardTitle>
            <CardDescription>
              当前可验证链路：解析目标专利 → GLM-4.6V 同时分析权利要求和附图 → 生成受约束检索式
              → 智慧芽 P002 逐组返回最多 10 条候选专利。首轮只执行固定五组中事实齐备的检索线，
              单组零命中不会自动改写、扩展或重复检索。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 md:grid-cols-[1fr_auto]">
              <Input
                aria-label="目标专利 PDF"
                type="file"
                accept=".pdf,application/pdf"
                disabled={busy}
                onChange={(event) => {
                  setFile(event.target.files?.[0] || null);
                  setError(null);
                }}
              />
              <Button
                onClick={() => void start()}
                disabled={!file || busy || healthEnvironment !== 'test'}
              >
                {busy
                  ? <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  : <Search className="mr-2 h-4 w-4" />}
                {busy ? '正在运行真实检索' : '开始测试 P002'}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              请选择包含权利要求和附图的 PDF。每组 P002 查询使用独立 run 和幂等键且只请求一次；
              浏览器只轮询对应 run，不把零命中伪装成网络重试。
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between gap-3">
              <span>运行进度</span>
              <Badge variant={phase === 'failed' ? 'destructive' : 'secondary'}>
                {phase === 'idle' ? '尚未开始' : phase === 'failed' ? '失败' : formatSeconds(elapsedSeconds)}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 lg:grid-cols-5">
              {PHASES.map((item) => {
                const state = phaseState(phase, item.id);
                return (
                  <div
                    key={item.id}
                    className={`rounded-lg border p-3 ${
                      state === 'running' ? 'border-primary bg-primary/5' : ''
                    }`}
                  >
                    <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                      {state === 'done'
                        ? <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                        : state === 'running'
                          ? <Loader2 className="h-4 w-4 animate-spin text-primary" />
                          : <Circle className="h-4 w-4 text-muted-foreground" />}
                      {item.label}
                    </div>
                    <p className="text-xs leading-5 text-muted-foreground">{item.description}</p>
                  </div>
                );
              })}
            </div>
            {investigationId ? (
              <div className="mt-4 grid gap-2 rounded-md bg-muted/50 p-3 font-mono text-xs md:grid-cols-2">
                <span className="break-all">investigation: {investigationId}</span>
                <span className="break-all">I1 run: {runIds.snapshot || '—'}</span>
                <span className="break-all">I2 run: {runIds.queryPlan || '—'}</span>
                <span className="break-all">P002 首个 run: {runIds.p002 || '—'}</span>
                <span className="break-all">P002 已创建 run: {runIds.p002Runs?.length || 0} 个</span>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {error ? (
          <Alert variant="destructive">
            <AlertTitle>测试未完成</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {target && criticalDate ? (
          <Card>
            <CardHeader>
              <CardTitle>目标专利与检索边界</CardTitle>
              <CardDescription>
                日期仅作为 P002 发现层过滤；当前快照日期仍需人工核验，不能据此直接形成法律结论。
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
              <div><p className="text-xs text-muted-foreground">专利号</p><p className="break-words font-medium">{target.patentNumber || '—'}</p></div>
              <div><p className="text-xs text-muted-foreground">标题</p><p className="break-words font-medium">{target.title || '—'}</p></div>
              <div><p className="text-xs text-muted-foreground">本次权利要求</p><p className="font-medium">{target.claimId}</p></div>
              <div><p className="text-xs text-muted-foreground">冻结附图</p><p className="font-medium">{target.figureCount} 张</p></div>
              <div><p className="text-xs text-muted-foreground">关键日</p><p className="font-medium">{criticalDate.date}</p></div>
              <div className="md:col-span-3">
                <p className="text-xs text-muted-foreground">关键日依据</p>
                <p className="font-medium">
                  {criticalDate.basis === 'priority_date' ? '优先权日' : '申请日'}（待人工核验）
                </p>
              </div>
            </CardContent>
          </Card>
        ) : null}

        {searchPlans.length > 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>GLM-4.6V 输出经智慧芽编译器生成的查询</CardTitle>
              <CardDescription>
                模型：{searchPlans[0].model}；实际使用目标附图 {searchPlans[0].usedTargetImages} 张；
                首轮按固定五组中事实齐备的检索线执行；字段映射、同义词与中英文术语均来自 I2 冻结输出。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <p className="mb-1 text-xs text-muted-foreground">技术主题</p>
                <p className="break-words font-medium">{searchPlans[0].technicalSubject}</p>
              </div>
              {searchPlans.map((plan, planIndex) => (
                <div key={`${plan.strategy}-${plan.expression}`} className="space-y-3 rounded-lg border p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="default">
                      {planIndex + 1}. {plan.queryVariant}
                    </Badge>
                  </div>
                  <div className="whitespace-pre-wrap break-words rounded-md bg-muted p-3 font-mono text-sm">
                    {plan.expression}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {plan.subjectTerms.map((term) => (
                      <Badge key={`${plan.strategy}-subject-${term}`}>主题：{term}</Badge>
                    ))}
                    {plan.featureTerms.map((term) => (
                      <Badge key={`${plan.strategy}-feature-${term}`} variant="secondary">特征：{term}</Badge>
                    ))}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}

        {result ? (
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2">
                智慧芽 P002 候选结果
                <Badge variant="secondary">{result.returnedCount} 条</Badge>
                {result.totalResultCount !== null ? (
                  <Badge variant="outline">命中总量 {result.totalResultCount.toLocaleString('zh-CN')}</Badge>
                ) : null}
              </CardTitle>
              <CardDescription>
                actual_provider={result.actualProvider}，network_used={String(result.networkUsed)}。以下均为
                <strong className="mx-1">lead 候选线索</strong>，尚未取得全文、完成日期资格或特征覆盖比对，
                不是 CC 证据，也不是新颖性/创造性结论。
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {searchAttempts.length > 0 && searchAttempts.every((attempt) => attempt.result.returnedCount === 0) ? (
                <Alert>
                  <Search className="h-4 w-4" />
                  <AlertTitle>固定首轮检索线均为零命中</AlertTitle>
                  <AlertDescription>
                    系统已保留每组独立 run 和零命中事实，不会自动扩大范围或生成未批准的替代检索线。
                  </AlertDescription>
                </Alert>
              ) : null}
              {searchAttempts.length > 0 ? (
                <div className="grid gap-3 md:grid-cols-2">
                  {searchAttempts.map((attempt, index) => (
                    <div key={attempt.runId} className="rounded-md border p-3">
                      <p className="text-sm font-medium">
                        {index + 1}. {attempt.plan.queryVariant}
                      </p>
                      <p className="mt-1 break-all font-mono text-xs text-muted-foreground">run {attempt.runId}</p>
                      <p className="mt-2 text-sm">
                        返回 {attempt.result.returnedCount} 条
                        {attempt.result.totalResultCount !== null
                          ? `，命中总量 ${attempt.result.totalResultCount.toLocaleString('zh-CN')}`
                          : ''}
                      </p>
                    </div>
                  ))}
                </div>
              ) : null}
              <div className="overflow-x-auto rounded-md border">
                <Table className="w-full table-fixed">
                  <colgroup>
                    <col className="w-12" />
                    <col className="w-36" />
                    <col className="w-72" />
                    <col className="w-20" />
                    <col className="w-28" />
                    <col className="w-28" />
                    <col className="w-48" />
                    <col className="w-20" />
                  </colgroup>
                  <TableHeader>
                    <TableRow>
                      <TableHead>#</TableHead>
                      <TableHead>公开号</TableHead>
                      <TableHead>标题</TableHead>
                      <TableHead>局别</TableHead>
                      <TableHead>公开日</TableHead>
                      <TableHead>申请日</TableHead>
                      <TableHead>当前申请人</TableHead>
                      <TableHead>阶段</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {result.candidates.length > 0 ? result.candidates.map((candidate, index) => {
                      const dateViolation = Boolean(
                        criticalDate
                        && candidate.publicationDate
                        && candidate.publicationDate >= criticalDate.date,
                      );
                      return (
                        <TableRow key={`${candidate.externalId}-${index}`}>
                          <TableCell className="align-top">{index + 1}</TableCell>
                          <TableCell className="break-all align-top font-mono text-xs">{candidate.publicationNumber || '—'}</TableCell>
                          <TableCell className="whitespace-normal break-words align-top leading-5">{candidate.title || '—'}</TableCell>
                          <TableCell className="break-words align-top">{candidate.authority || '—'}</TableCell>
                          <TableCell className={`break-words align-top ${dateViolation ? 'font-semibold text-destructive' : ''}`}>
                            {candidate.publicationDate || '待核验'}
                          </TableCell>
                          <TableCell className="break-words align-top">{candidate.filingDate || '待核验'}</TableCell>
                          <TableCell className="whitespace-normal break-words align-top">{candidate.assignee || '—'}</TableCell>
                          <TableCell className="break-words align-top"><Badge variant="outline">{candidate.stage || 'lead'}</Badge></TableCell>
                        </TableRow>
                      );
                    }) : (
                      <TableRow><TableCell colSpan={8} className="py-8 text-center text-muted-foreground">智慧芽本轮已执行的合规查询仍为 0 条候选线索，需要人工调整术语</TableCell></TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
              {result.artifacts.length > 0 ? (
                <div className="space-y-2 rounded-md bg-muted/50 p-3">
                  <p className="text-xs font-medium">冻结的智慧芽响应审计</p>
                  {result.artifacts.map((artifact, index) => (
                    <p key={`${artifact.sha256}-${index}`} className="break-all font-mono text-xs text-muted-foreground">
                      {artifact.kind || 'response'} · sha256 {artifact.sha256 || '—'} · {artifact.byteSize ?? '—'} bytes
                    </p>
                  ))}
                </div>
              ) : null}
            </CardContent>
          </Card>
        ) : null}

        {latestRun ? (
          <details className="rounded-md border bg-background p-4">
            <summary className="cursor-pointer text-sm font-medium">查看最后一个模块的原始诊断</summary>
            <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all rounded bg-slate-950 p-4 text-xs text-slate-100">
              {JSON.stringify(latestRun, null, 2)}
            </pre>
          </details>
        ) : null}

        <Alert>
          <Upload className="h-4 w-4" />
          <AlertTitle>当前测试范围</AlertTitle>
          <AlertDescription>
            这一步验证真实目标解析、GLM-4.6V 图文查询规划和智慧芽 P002 发现能力。由于当前账号没有
            P012/P018/P019 权限，本页不会把摘要线索伪装成全文证据，也不会生成 CC 表。
          </AlertDescription>
        </Alert>
      </main>
    </div>
  );
}
