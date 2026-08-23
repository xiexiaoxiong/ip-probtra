'use client';

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { AlertCircle, Download, Loader2, Play, RotateCw } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { InvalidityReviewPanel } from '@/components/invalidity-review-panel';
import { createStableInvalidityIntent } from '@/lib/invalidity-idempotency';
import {
  assertCriticalDateConfirmationResponse,
  assertInvalidityReportDataV1,
  claimDisplayLabel,
  invalidityNoveltyCcDisclosures,
  invalidityText as text,
  isInvalidityClaimContinuable,
  isInvalidityTerminalStatus,
  isJsonObject,
  mergeCriticalDateConfirmation,
  needsInvalidityCriticalDateConfirmation,
  reportRows as array,
  type CriticalDateConfirmationResponse,
  type CriticalDateDecision,
  type InvalidityReportDataV1,
  type JsonObject,
} from '@/lib/invalidity-contracts';

function display(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'object') return JSON.stringify(value, null, 2);
  return String(value);
}

async function postAction(path: string, body: JsonObject): Promise<JsonObject> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json() as JsonObject;
  if (!response.ok) throw new Error(text(data.error || data.detail) || `HTTP ${response.status}`);
  return data;
}

function dateCandidate(claim: JsonObject, key: string): string {
  const source = isJsonObject(claim.source_snapshot) ? claim.source_snapshot : {};
  const result = isJsonObject(claim.result_summary) ? claim.result_summary : {};
  return text(claim[key] || source[key] || result[key]);
}

function CriticalDateConfirmation({
  sessionId,
  claim,
  onSaved,
}: {
  sessionId: string;
  claim: JsonObject;
  onSaved: (confirmation: CriticalDateConfirmationResponse) => void;
}) {
  const claimId = text(claim.id);
  const [decision, setDecision] = useState<CriticalDateDecision>('confirm_priority');
  const [confirmedDate, setConfirmedDate] = useState(() => dateCandidate(claim, 'critical_date'));
  const [targetPublicationDate, setTargetPublicationDate] = useState(() => dateCandidate(claim, 'target_publication_date'));
  const [basis, setBasis] = useState(() => dateCandidate(claim, 'critical_date_basis'));
  const [reason, setReason] = useState('已核对目标专利著录项目与权利要求主题对应关系');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const intent = useRef(createStableInvalidityIntent('portal-critical-date'));
  const stateVersion = Number(claim.state_version || 0);
  const source = isJsonObject(claim.source_snapshot) ? claim.source_snapshot : {};

  const submit = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const formIntent = {
        claim_investigation_id: claimId,
        decision,
        confirmed_date: confirmedDate,
        target_publication_date: targetPublicationDate,
        basis,
        reason,
        expected_state_version: stateVersion,
      };
      const confirmation = await postAction(
        `/api/invalidity/session/${encodeURIComponent(sessionId)}/critical-date-confirmations`,
        {
          ...formIntent,
          idempotency_key: intent.current.key(JSON.stringify(formIntent)),
        },
      );
      assertCriticalDateConfirmationResponse(confirmation);
      setMessage('确认已保存；系统将从当前 checkpoint 恢复，不需要重新上传。');
      onSaved(confirmation);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '保存关键日确认失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border bg-background p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="font-medium">{claimDisplayLabel(claim)}</div>
        <div className="flex gap-2"><Badge variant="outline">state v{stateVersion}</Badge><Badge variant="secondary">{text(claim.status)}</Badge></div>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <label className="space-y-1 text-sm"><span>确认方式</span><Select value={decision} onValueChange={(value) => setDecision(value as CriticalDateDecision)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="confirm_priority">确认优先权日</SelectItem><SelectItem value="use_filing_date">改用申请日</SelectItem><SelectItem value="set_manual_date">人工指定关键日</SelectItem></SelectContent></Select></label>
        <label className="space-y-1 text-sm"><span>本权利要求关键日</span><Input type="date" value={confirmedDate} onChange={(event) => setConfirmedDate(event.target.value)} /></label>
        <label className="space-y-1 text-sm"><span>目标专利公开日</span><Input type="date" value={targetPublicationDate} onChange={(event) => setTargetPublicationDate(event.target.value)} /></label>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <label className="space-y-1 text-sm"><span>日期依据</span><Input value={basis} onChange={(event) => setBasis(event.target.value)} placeholder="例如：优先权文件、申请日著录项目、主题对应关系" /></label>
        <label className="space-y-1 text-sm"><span>确认理由</span><Input value={reason} onChange={(event) => setReason(event.target.value)} /></label>
      </div>
      {Object.keys(source).length ? <details className="mt-3 text-xs text-muted-foreground"><summary className="cursor-pointer">查看后端候选日期与来源快照</summary><pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-muted p-2">{display(source)}</pre></details> : null}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Button size="sm" onClick={() => void submit()} disabled={busy || !confirmedDate || !targetPublicationDate || !basis || !reason}>
          {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}确认并恢复
        </Button>
        {message ? <span className="text-sm text-muted-foreground">{message}</span> : null}
      </div>
    </div>
  );
}

function ContinuationControl({
  sessionId,
  investigation,
  claims,
  onStarted,
}: {
  sessionId: string;
  investigation: JsonObject;
  claims: JsonObject[];
  onStarted: () => void;
}) {
  const candidates = claims.filter((claim) => isInvalidityClaimContinuable(claim.status));
  const candidateKey = candidates.map((claim) => text(claim.id)).join('|');
  const [selected, setSelected] = useState<string[]>([]);
  const [rounds, setRounds] = useState<'1' | '2' | '3'>('1');
  const [reason, setReason] = useState('已复核当前 D1、未解决 gap、失败来源和停止原因，申请继续定向检索');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const intent = useRef(createStableInvalidityIntent('portal-continuation'));

  useEffect(() => {
    setSelected(candidateKey ? candidateKey.split('|').filter(Boolean) : []);
  }, [candidateKey]);

  if (!candidates.length) return null;

  const submit = async () => {
    const candidateIds = new Set(candidates.map((claim) => text(claim.id)).filter(Boolean));
    const submissionIds = selected.filter((id) => candidateIds.has(id));
    if (!submissionIds.length || submissionIds.length !== selected.length) {
      setMessage('所选权利要求状态已经变化；成功终态不能普通续检，请刷新后重新选择。');
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      const formIntent = {
        claim_investigation_ids: submissionIds,
        max_additional_rounds: Number(rounds),
        reason,
        expected_state_version: Number(investigation.state_version || 0),
      };
      await postAction(`/api/invalidity/session/${encodeURIComponent(sessionId)}/continuations`, {
        ...formIntent,
        idempotency_key: intent.current.key(JSON.stringify(formIntent)),
      });
      setMessage('新轮次已创建，页面将恢复串行轮询。');
      onStarted();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '继续调查失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader><CardTitle>人工确认后继续检索</CardTitle><CardDescription>固定首轮和最多五个区别特征检索轮与人工继续分开记录。请先查看 D1、未解决 gap、provider 失败和停止原因；继续会创建新轮次，不覆盖历史。</CardDescription></CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-2 md:grid-cols-2">{candidates.map((claim) => {
          const id = text(claim.id);
          const checked = selected.includes(id);
          return <label key={id} className="flex items-start gap-2 rounded border p-3 text-sm"><input className="mt-1" type="checkbox" checked={checked} onChange={(event) => setSelected((current) => event.target.checked ? [...new Set([...current, id])] : current.filter((item) => item !== id))} /><span><strong>{claimDisplayLabel(claim)}</strong><br /><span className="text-muted-foreground">{text(claim.status)}：{text(claim.terminal_reason) || '无停止原因'}</span></span></label>;
        })}</div>
        <div className="grid gap-3 md:grid-cols-[220px_1fr]"><label className="space-y-1 text-sm"><span>追加轮次（本次最多 3）</span><Select value={rounds} onValueChange={(value) => setRounds(value as '1' | '2' | '3')}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1">1 轮</SelectItem><SelectItem value="2">2 轮</SelectItem><SelectItem value="3">3 轮</SelectItem></SelectContent></Select></label><label className="space-y-1 text-sm"><span>继续理由</span><Textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label></div>
        <div className="flex flex-wrap items-center gap-3"><Button onClick={() => void submit()} disabled={busy || !selected.length || !reason.trim()}>{busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Play className="mr-1 h-4 w-4" />}创建继续轮次</Button>{message ? <span className="text-sm text-muted-foreground">{message}</span> : null}</div>
      </CardContent>
    </Card>
  );
}

function InvalidityResultsContent() {
  const params = useSearchParams();
  const sessionId = params.get('session')?.trim() || '';
  const [payload, setPayload] = useState<JsonObject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    if (!sessionId) {
      setError('网址缺少 session 参数，无法确定要读取的无效调查');
      setLoading(false);
      return;
    }
    let cancelled = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;

    const poll = async () => {
      controller = new AbortController();
      let shouldContinue = false;
      let responseReceived = false;
      try {
        const response = await fetch(`/api/invalidity/session/${encodeURIComponent(sessionId)}`, {
          cache: 'no-store',
          signal: controller.signal,
        });
        responseReceived = true;
        const data = await response.json() as JsonObject;
        if (!response.ok) {
          shouldContinue = response.status === 429 || response.status >= 500;
          throw new Error(text(data.error) || '读取调查失败');
        }
        if (cancelled) return;
        setPayload(data);
        setError(null);
        const investigation = isJsonObject(data.investigation) ? data.investigation : {};
        shouldContinue = !isInvalidityTerminalStatus(investigation.status);
      } catch (caught) {
        if (cancelled || (caught instanceof DOMException && caught.name === 'AbortError')) return;
        if (!responseReceived) shouldContinue = true;
        setError(caught instanceof Error ? caught.message : '读取调查失败');
      } finally {
        if (!cancelled) {
          setLoading(false);
          if (shouldContinue) timer = window.setTimeout(() => void poll(), 4000);
        }
      }
    };

    setLoading(true);
    void poll();
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [refreshNonce, sessionId]);

  const rawReport = payload?.report;
  let report: InvalidityReportDataV1 | null = null;
  let contractError: string | null = null;
  if (rawReport != null) {
    try {
      assertInvalidityReportDataV1(rawReport);
      report = rawReport;
    } catch (caught) {
      contractError = caught instanceof Error ? caught.message : '报告契约无效';
    }
  }
  const investigation = isJsonObject(payload?.investigation) ? payload.investigation : {};
  const reportClaims = report ? array(report.claim_investigations || report.claims) : [];
  const liveClaims = array(payload?.claim_investigations);
  const claims = reportClaims.length ? reportClaims : liveClaims;
  const currentClaims = liveClaims.length ? liveClaims : claims;
  const limitations = array(report?.claim_limitations || report?.limitations);
  const documents = array(report?.documents);
  const disclosures = array(report?.feature_disclosures || report?.disclosures);
  const gaps = array(report?.gap_items || report?.gaps);
  const qualifications = array(report?.document_qualifications || report?.qualifications);
  const noveltyDisclosures = useMemo(
    () => invalidityNoveltyCcDisclosures({ claims, limitations, disclosures, qualifications }),
    [claims, limitations, disclosures, qualifications],
  );
  const combinations = array(report?.combinations);
  const closestVersions = array(report?.closest_prior_art_versions);
  const iterations = array(report?.iterations);
  const queries = array(report?.queries);
  const sources = array(report?.document_sources);
  const events = array(report?.events);
  const jobs = array(report?.jobs);
  const moduleRuns = array(report?.module_runs);
  const documentById = useMemo(() => new Map(documents.map((item) => [text(item.id), item])), [documents]);
  const limitationById = useMemo(() => new Map(limitations.map((item) => [text(item.id), item])), [limitations]);
  const claimById = useMemo(
    () => new Map([...claims, ...currentClaims].map((item) => [text(item.id), item])),
    [claims, currentClaims],
  );
  const closestById = useMemo(() => new Map(closestVersions.map((item) => [text(item.id), item])), [closestVersions]);
  const iterationById = useMemo(() => new Map(iterations.map((item) => [text(item.id), item])), [iterations]);
  const jobByRunId = useMemo(() => new Map(jobs.map((item) => [text(item.module_run_id), item])), [jobs]);
  const status = text(investigation.status || (isJsonObject(payload?.session) ? payload.session.status : '') || 'running');
  const terminal = isInvalidityTerminalStatus(status);
  const needsDateConfirmation = currentClaims.filter(needsInvalidityCriticalDateConfirmation);
  const failedTaskCount = moduleRuns.filter((run) => {
    const job = jobByRunId.get(text(run.id));
    return text(run.status) === 'failed' || text(run.error_message) || text(job?.status) === 'failed' || Boolean(job?.last_error);
  }).length;
  const refresh = () => setRefreshNonce((current) => current + 1);
  const saveCriticalDate = (confirmation: CriticalDateConfirmationResponse) => {
    setPayload((current) => current ? mergeCriticalDateConfirmation(current, confirmation) : current);
    refresh();
  };

  return (
    <div className="min-h-screen bg-muted/20">
      <header className="border-b bg-background"><div className="mx-auto flex h-14 max-w-[1500px] items-center justify-between px-6"><div className="flex items-center gap-2 font-semibold">无效检索结果 <Badge variant="outline">{status}</Badge></div><div className="flex gap-2"><Button size="sm" variant="outline" onClick={refresh}><RotateCw className="mr-1 h-4 w-4" />刷新</Button><Button size="sm" variant="outline" disabled={!terminal || !report} asChild={Boolean(terminal && report)}>{terminal && report ? <a href={`/api/invalidity/session/${encodeURIComponent(sessionId)}/export`}><Download className="mr-1 h-4 w-4" />导出 CC 报告</a> : <span><Download className="mr-1 inline h-4 w-4" />导出 CC 报告</span>}</Button><Button asChild size="sm"><Link href="/">返回 Agent</Link></Button></div></div></header>

      <main className="mx-auto max-w-[1500px] space-y-6 px-6 py-6">
        {loading ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取持久化调查状态…</div> : null}
        {error ? <Alert variant="destructive"><AlertCircle className="h-4 w-4" /><AlertTitle>读取失败</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}
        {contractError ? <Alert variant="destructive"><AlertCircle className="h-4 w-4" /><AlertTitle>报告契约不兼容</AlertTitle><AlertDescription>{contractError}。页面拒绝用备用字段拼出空报告。</AlertDescription></Alert> : null}
        {text(payload?.report_error) ? <Alert><AlertCircle className="h-4 w-4" /><AlertTitle>报告快照尚未就绪</AlertTitle><AlertDescription>{text(payload?.report_error)}</AlertDescription></Alert> : null}
        {!terminal && !error ? <Alert><Loader2 className="h-4 w-4 animate-spin" /><AlertTitle>调查仍在运行</AlertTitle><AlertDescription>采用单次完成后再等待 4 秒的串行轮询；进入终态后自动停止。各项权利要求独立推进。</AlertDescription></Alert> : null}
        {['failed', 'cancelled'].includes(status) && !error ? <Alert variant="destructive"><AlertCircle className="h-4 w-4" /><AlertTitle>调查未完整完成</AlertTitle><AlertDescription>{text(investigation.error_message) || '下方展示已持久化证据、失败阶段、作业重试和审计事件；失败或未命中不等于专利稳定。'}</AlertDescription></Alert> : null}
        {['search_budget_exhausted', 'exhausted'].includes(status) && !error ? <Alert><AlertCircle className="h-4 w-4" /><AlertTitle>自动检索预算已用尽</AlertTitle><AlertDescription>这是等待人工决定的停止状态，不是 completed，也不表示专利具备新颖性或创造性。请查看 D1、gap 和失败来源后使用下方“人工继续”。</AlertDescription></Alert> : null}

        <Card><CardHeader><CardTitle>逐权利要求状态</CardTitle></CardHeader><CardContent className="overflow-x-auto"><Table className="table-fixed min-w-[1150px]"><TableHeader><TableRow><TableHead className="w-32">权利要求</TableHead><TableHead className="w-44">状态</TableHead><TableHead className="w-36">关键日</TableHead><TableHead className="w-36">目标公开日</TableHead><TableHead className="w-24">轮次</TableHead><TableHead className="w-[520px]">停止原因 / 当前结论边界</TableHead></TableRow></TableHeader><TableBody>{currentClaims.map((claim) => <TableRow key={text(claim.id)}><TableCell className="align-top whitespace-normal break-words">{claimDisplayLabel(claim)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(claim.status)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(claim.critical_date) || '待复核'}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(claim.target_publication_date) || '待复核'}</TableCell><TableCell className="align-top">{text(claim.current_iteration_no)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(claim.terminal_reason) || text((isJsonObject(claim.result_summary) ? claim.result_summary : {}).summary) || '—'}</TableCell></TableRow>)}</TableBody></Table></CardContent></Card>

        {needsDateConfirmation.length ? <Card><CardHeader><CardTitle>逐权利要求确认关键日与目标公开日</CardTitle><CardDescription>确认按 claim 单独保存并从 checkpoint 恢复。普通现有技术、抵触申请候选和后公开线索将分别按这两个日期判定。</CardDescription></CardHeader><CardContent className="space-y-4">{needsDateConfirmation.map((claim) => <CriticalDateConfirmation key={`${text(claim.id)}-${text(claim.state_version)}`} sessionId={sessionId} claim={claim} onSaved={saveCriticalDate} />)}</CardContent></Card> : null}

        <InvalidityReviewPanel environment="prod" sessionId={sessionId} onChanged={refresh} />

        <ContinuationControl sessionId={sessionId} investigation={investigation} claims={currentClaims} onStarted={refresh} />

        <Card><CardHeader><CardTitle>新颖性 CC 表</CardTitle><CardDescription>这里只显示日期核验合格、且同一份文献独立覆盖一项权利要求全部技术特征的证据；不同文献永不拼接。</CardDescription></CardHeader><CardContent className="overflow-x-auto"><Table className="table-fixed min-w-[1450px]"><TableHeader><TableRow><TableHead className="w-44">权利要求 / 特征</TableHead><TableHead className="w-[360px]">目标技术特征</TableHead><TableHead className="w-48">单一对比文件</TableHead><TableHead className="w-40">披露状态</TableHead><TableHead className="w-[380px]">原文引证</TableHead><TableHead className="w-48">位置</TableHead></TableRow></TableHeader><TableBody>{noveltyDisclosures.map((item) => { const limitation = limitationById.get(text(item.limitation_id)) || {}; const claim = claimById.get(text(item.claim_investigation_id)); const document = documentById.get(text(item.document_id)) || {}; const locator = isJsonObject(item.locator) ? item.locator : {}; return <TableRow key={text(item.id)}><TableCell className="align-top whitespace-normal break-words">{claimDisplayLabel(claim, item.claim_investigation_id)}<br /><span className="text-xs text-muted-foreground">{text(limitation.feature_key)}</span></TableCell><TableCell className="align-top whitespace-normal break-words">{text(limitation.limitation_text)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(document.title || document.canonical_key)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(item.disclosure_status)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(item.excerpt)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(locator.location || locator.page || locator.paragraph)}</TableCell></TableRow>; })}</TableBody></Table>{!noveltyDisclosures.length ? <p className="py-4 text-sm text-muted-foreground">尚无满足“单一日期合格文献完整覆盖”的新颖性 CC；这不表示目标专利具备新颖性。</p> : null}</CardContent></Card>

        <Card><CardHeader><CardTitle>全部逐篇单文献比对</CardTitle><CardDescription>这里保留所有候选文献的逐特征矩阵，供选择 D1、识别 gap 和人工复核；这些行不会自动构成新颖性结论。</CardDescription></CardHeader><CardContent className="overflow-x-auto"><Table className="table-fixed min-w-[1450px]"><TableHeader><TableRow><TableHead className="w-44">权利要求 / 特征</TableHead><TableHead className="w-[360px]">目标技术特征</TableHead><TableHead className="w-48">候选文献</TableHead><TableHead className="w-40">披露状态</TableHead><TableHead className="w-[380px]">原文引证</TableHead><TableHead className="w-48">位置</TableHead></TableRow></TableHeader><TableBody>{disclosures.map((item) => { const limitation = limitationById.get(text(item.limitation_id)) || {}; const claim = claimById.get(text(item.claim_investigation_id)); const document = documentById.get(text(item.document_id)) || {}; const locator = isJsonObject(item.locator) ? item.locator : {}; return <TableRow key={text(item.id)}><TableCell className="align-top whitespace-normal break-words">{claimDisplayLabel(claim, item.claim_investigation_id)}<br /><span className="text-xs text-muted-foreground">{text(limitation.feature_key)}</span></TableCell><TableCell className="align-top whitespace-normal break-words">{text(limitation.limitation_text)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(document.title || document.canonical_key)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(item.disclosure_status)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(item.excerpt)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(locator.location || locator.page || locator.paragraph)}</TableCell></TableRow>; })}</TableBody></Table>{!disclosures.length ? <p className="py-4 text-sm text-muted-foreground">尚无完成的单文献比对记录。</p> : null}</CardContent></Card>

        <Card><CardHeader><CardTitle>最接近现有技术与创造性组合</CardTitle></CardHeader><CardContent className="overflow-x-auto"><Table className="table-fixed min-w-[1450px]"><TableHeader><TableRow><TableHead className="w-32">权利要求</TableHead><TableHead className="w-20">轮次</TableHead><TableHead className="w-56">D1</TableHead><TableHead className="w-64">组合文献</TableHead><TableHead className="w-32">特征覆盖</TableHead><TableHead className="w-40">组合动机</TableHead><TableHead className="w-40">技术问题</TableHead><TableHead className="w-40">反向教导</TableHead><TableHead className="w-40">技术效果</TableHead><TableHead className="w-36">证据状态</TableHead></TableRow></TableHeader><TableBody>{combinations.map((item) => { const closest = closestById.get(text(item.closest_prior_art_version_id)) || {}; const d1 = documentById.get(text(closest.document_id)) || {}; const analysis = isJsonObject(item.analysis) ? item.analysis : {}; const documentIds = Array.isArray(item.document_ids) ? item.document_ids : []; const combinationNames = documentIds.map((id) => { const document = documentById.get(text(id)) || {}; return text(document.title || document.canonical_key || id); }).join('\n'); const criterion = (key: string) => isJsonObject(analysis[key]) ? analysis[key] : {}; const claim = claimById.get(text(item.claim_investigation_id)); const iteration = iterationById.get(text(item.iteration_id)) || {}; return <TableRow key={text(item.id)}><TableCell className="align-top whitespace-normal break-words">{claimDisplayLabel(claim, item.claim_investigation_id)}</TableCell><TableCell className="align-top">{text(iteration.iteration_no)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(d1.title || d1.canonical_key || closest.document_id)}</TableCell><TableCell className="align-top whitespace-pre-wrap break-words">{combinationNames}</TableCell><TableCell className="align-top whitespace-normal break-words">{item.coverage_complete === true ? '完整' : '仍有缺口'}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(criterion('combination_motivation').status)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(criterion('related_technical_problem').status)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(criterion('teaching_away').status)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(criterion('technical_effect').status)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(item.status)}</TableCell></TableRow>; })}</TableBody></Table>{!combinations.length ? <p className="py-4 text-sm text-muted-foreground">尚无可持久化的创造性组合；这不代表专利具备创造性。</p> : null}</CardContent></Card>

        <Card><CardHeader><CardTitle>逐权利要求、逐文献、逐评估版本的日期资格</CardTitle></CardHeader><CardContent className="overflow-x-auto"><Table className="table-fixed min-w-[1550px]"><TableHeader><TableRow><TableHead className="w-36">权利要求</TableHead><TableHead className="w-56">文献</TableHead><TableHead className="w-24">评估版本</TableHead><TableHead className="w-36">关键日 / 公开日</TableHead><TableHead className="w-44">资格类别</TableHead><TableHead className="w-24">新颖性</TableHead><TableHead className="w-24">创造性</TableHead><TableHead className="w-[360px]">核验状态 / 理由</TableHead><TableHead className="w-72">来源</TableHead></TableRow></TableHeader><TableBody>{qualifications.map((qualification, index) => { const document = documentById.get(text(qualification.document_id)) || {}; const claim = claimById.get(text(qualification.claim_investigation_id)); const documentSources = sources.filter((item) => text(item.document_id) === text(qualification.document_id)); return <TableRow key={text(qualification.id) || `${text(qualification.document_id)}-${text(qualification.claim_investigation_id)}-${text(qualification.assessment_version)}-${index}`}><TableCell className="align-top whitespace-normal break-words">{claimDisplayLabel(claim, qualification.claim_investigation_id)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(document.title || document.canonical_key)}</TableCell><TableCell className="align-top">v{text(qualification.assessment_version || 1)}</TableCell><TableCell className="align-top whitespace-pre-wrap break-words">{text(qualification.critical_date)}{text(qualification.publication_date) ? `\n${text(qualification.publication_date)}` : ''}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(qualification.eligibility_type)}</TableCell><TableCell className="align-top">{qualification.novelty_eligible === true ? '可用' : '不可用'}</TableCell><TableCell className="align-top">{qualification.inventive_step_eligible === true ? '可用' : '不可用'}</TableCell><TableCell className="align-top whitespace-pre-wrap break-words">{text(qualification.verification_status)}：{text(qualification.verification_reason)}</TableCell><TableCell className="align-top whitespace-pre-wrap break-all">{documentSources.map((item) => `${text(item.provider)}\n${text(item.source_url)}`).join('\n\n') || '—'}</TableCell></TableRow>; })}</TableBody></Table>{!qualifications.length ? <p className="py-4 text-sm text-muted-foreground">尚无按权利要求保存的日期资格记录。</p> : null}</CardContent></Card>

        <Card><CardHeader><CardTitle>检索轮次与查询谱系</CardTitle></CardHeader><CardContent className="overflow-x-auto"><Table className="table-fixed min-w-[1350px]"><TableHeader><TableRow><TableHead className="w-32">权利要求</TableHead><TableHead className="w-20">轮次</TableHead><TableHead className="w-44">目的 / 通道</TableHead><TableHead className="w-[500px]">检索式</TableHead><TableHead className="w-64">目标特征</TableHead><TableHead className="w-48">日期过滤与状态</TableHead><TableHead className="w-48">停止原因</TableHead></TableRow></TableHeader><TableBody>{queries.map((query) => { const iteration = iterationById.get(text(query.iteration_id)) || {}; const claim = claimById.get(text(iteration.claim_investigation_id)); return <TableRow key={text(query.id)}><TableCell className="align-top whitespace-normal break-words">{claimDisplayLabel(claim, iteration.claim_investigation_id)}</TableCell><TableCell className="align-top">{text(iteration.iteration_no)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(query.query_type)} / {text(query.channel)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(query.expression)}</TableCell><TableCell className="align-top whitespace-pre-wrap break-words">{display(query.feature_ids)}</TableCell><TableCell className="align-top whitespace-pre-wrap break-words">{text(query.status)}\n{display(query.date_filter)}</TableCell><TableCell className="align-top whitespace-normal break-words">{text(iteration.stop_reason)}</TableCell></TableRow>; })}</TableBody></Table></CardContent></Card>

        <Card><CardHeader><CardTitle>未解决区别特征与组合证据缺口</CardTitle></CardHeader><CardContent>{gaps.length ? <div className="space-y-3">{gaps.map((gap) => { const claim = claimById.get(text(gap.claim_investigation_id)); return <div key={text(gap.id)} className="rounded-md border p-3"><div className="flex flex-wrap gap-2"><Badge variant="outline">{claimDisplayLabel(claim, gap.claim_investigation_id)}</Badge><Badge variant="secondary">{text(gap.gap_type)}</Badge><Badge variant="outline">{text(gap.status)}</Badge></div><p className="mt-2 whitespace-pre-wrap break-words text-sm">{text(gap.description)}</p></div>; })}</div> : <p className="text-sm text-muted-foreground">暂无已保存缺口；调查未结束时不代表不存在区别特征。</p>}</CardContent></Card>

        <Card><CardHeader><CardTitle>模块运行、作业重试与审计事件</CardTitle><CardDescription>共 {moduleRuns.length} 个 module run，{jobs.length} 个 job，失败 {failedTaskCount} 个。失败必须可见；query、iteration、module run 和 job 的终态由后端持久化，本页不把失败转换为成功。</CardDescription></CardHeader><CardContent className="space-y-4">{moduleRuns.length ? <div className="overflow-x-auto"><Table className="table-fixed min-w-[1250px]"><TableHeader><TableRow><TableHead className="w-52">模块</TableHead><TableHead className="w-32">运行状态</TableHead><TableHead className="w-32">作业状态</TableHead><TableHead className="w-28">尝试/上限</TableHead><TableHead className="w-28">可重试</TableHead><TableHead className="w-[520px]">错误 / 最后错误</TableHead></TableRow></TableHeader><TableBody>{moduleRuns.map((run) => { const job = jobByRunId.get(text(run.id)) || {}; return <TableRow key={text(run.id)}><TableCell className="align-top whitespace-normal break-words">{text(run.module_code)}<br /><span className="text-xs text-muted-foreground">{text(run.id)}</span></TableCell><TableCell className="align-top">{text(run.status)}</TableCell><TableCell className="align-top">{text(job.status) || '—'}</TableCell><TableCell className="align-top">{text(job.attempt_count ?? run.attempt_no)} / {text(job.max_attempts)}</TableCell><TableCell className="align-top">{run.retryable === true ? '是' : run.retryable === false ? '否' : '—'}</TableCell><TableCell className="align-top whitespace-pre-wrap break-words">{text(run.error_code)} {text(run.error_message) || display(job.last_error)}</TableCell></TableRow>; })}</TableBody></Table></div> : <p className="text-sm text-muted-foreground">当前报告快照没有 module run；若调查已经结束，这属于需要复核的审计缺口。</p>}{events.length ? <details><summary className="cursor-pointer text-sm font-medium">查看 {events.length} 条审计事件</summary><pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-4 text-xs text-slate-100">{display(events)}</pre></details> : <p className="text-sm text-muted-foreground">尚无审计事件。</p>}</CardContent></Card>
      </main>
    </div>
  );
}

export default function InvalidityResultsPage() {
  return <Suspense fallback={<div className="p-8">正在加载…</div>}><InvalidityResultsContent /></Suspense>;
}
