'use client';

import { useEffect, useMemo, useState } from 'react';
import { FileCheck2, Loader2, RefreshCw, Upload } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import {
  assertHumanReviewCommandResponse,
  assertInvalidityReviewContext,
  claimDisplayLabel,
  invalidityDateReviewDefaults,
  invalidityPendingDateReviewDocuments,
  invalidityReviewCurrentDocumentVersionId,
  invalidityReviewQualificationsByClaim,
  invalidityText,
  latestInvalidityGapsByKey,
  reportRows,
  type InvalidityEvidenceUploadReceipt,
  type InvalidityReviewContext,
  type JsonObject,
} from '@/lib/invalidity-contracts';
import {
  createStableInvalidityIntent,
  type StableInvalidityIntent,
} from '@/lib/invalidity-idempotency';

type ReviewPanelProps = {
  environment: 'prod' | 'test';
  sessionId?: string;
  investigationId?: string;
  onChanged?: () => void;
  focus?: 'all' | 'pending_date_review';
};

type DateDecision = 'confirm_facts' | 'exclude' | 'reopen_review';
type DateChannel = 'ordinary_prior_art' | 'cn_conflicting_application';

function useIntent(prefix: string): StableInvalidityIntent {
  const [intent] = useState(() => createStableInvalidityIntent(prefix));
  return intent;
}

function text(value: unknown): string {
  return invalidityText(value).trim();
}

async function jsonResponse(response: Response): Promise<JsonObject> {
  const data = await response.json().catch(() => ({})) as JsonObject;
  if (!response.ok) {
    const message = text(data.error || data.detail) || `HTTP ${response.status}`;
    const code = text(data.code);
    throw new Error(code ? `${code}：${message}` : message);
  }
  return data;
}

function reviewUrls(props: ReviewPanelProps) {
  if (props.environment === 'prod') {
    const sessionId = text(props.sessionId);
    return {
      context: `/api/invalidity/session/${encodeURIComponent(sessionId)}/review-context`,
      upload: `/api/invalidity/session/${encodeURIComponent(sessionId)}/evidence-uploads`,
      evidenceImport: `/api/invalidity/session/${encodeURIComponent(sessionId)}/evidence-imports`,
      dateConfirmation: `/api/invalidity/session/${encodeURIComponent(sessionId)}/document-date-confirmations`,
    };
  }
  const investigationId = text(props.investigationId);
  const base = `/api/test/invalidity/v1/investigations/${encodeURIComponent(investigationId)}`;
  return {
    context: `${base}/review-context`,
    upload: '/api/test/invalidity/evidence-uploads',
    evidenceImport: `${base}/human-reviews/evidence-imports`,
    dateConfirmation: `${base}/human-reviews/document-date-confirmations`,
  };
}

function expectedClaimStateVersions(
  claims: JsonObject[],
  selectedIds: string[],
): Record<string, number> {
  const byId = new Map(claims.map((claim) => [text(claim.id), claim]));
  return Object.fromEntries(selectedIds.map((id) => [id, Number(byId.get(id)?.state_version || 0)]));
}

function ClaimChecks({
  claims,
  selected,
  setSelected,
  allowed,
}: {
  claims: JsonObject[];
  selected: string[];
  setSelected: (ids: string[]) => void;
  allowed?: Set<string>;
}) {
  return (
    <div className="grid gap-2 md:grid-cols-2">
      {claims.map((claim) => {
        const id = text(claim.id);
        const enabled = !allowed || allowed.has(id);
        return (
          <label key={id} className="flex items-start gap-2 rounded border p-2 text-sm">
            <input
              className="mt-1"
              type="checkbox"
              disabled={!enabled}
              checked={selected.includes(id)}
              onChange={(event) => setSelected(
                event.target.checked
                  ? [...new Set([...selected, id])]
                  : selected.filter((item) => item !== id),
              )}
            />
            <span>
              {claimDisplayLabel(claim)}
              <span className="ml-2 text-xs text-muted-foreground">
                {text(claim.status)} · state v{Number(claim.state_version || 0)}
              </span>
              {!enabled ? <span className="block text-xs text-amber-700">当前文献没有该 claim 的可确认资格版本</span> : null}
            </span>
          </label>
        );
      })}
    </div>
  );
}

export function InvalidityReviewPanel(props: ReviewPanelProps) {
  const urls = reviewUrls(props);
  const enabled = props.environment === 'prod' ? Boolean(text(props.sessionId)) : Boolean(text(props.investigationId));
  const [context, setContext] = useState<InvalidityReviewContext | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshNonce, setRefreshNonce] = useState(0);

  const [dateDocumentId, setDateDocumentId] = useState('');
  const [dateClaims, setDateClaims] = useState<string[]>([]);
  const [dateDecision, setDateDecision] = useState<DateDecision>('confirm_facts');
  const [dateChannel, setDateChannel] = useState<DateChannel>('ordinary_prior_art');
  const [publicAvailabilityDate, setPublicAvailabilityDate] = useState('');
  const [publicationDate, setPublicationDate] = useState('');
  const [filingDate, setFilingDate] = useState('');
  const [priorityDate, setPriorityDate] = useState('');
  const [dateSourceType, setDateSourceType] = useState('official_bibliographic_record');
  const [publicationNumber, setPublicationNumber] = useState('');
  const [authority, setAuthority] = useState('');
  const [dateEvidenceSource, setDateEvidenceSource] = useState('');
  const [dateEvidenceLocator, setDateEvidenceLocator] = useState('');
  const [dateReason, setDateReason] = useState('');
  const [dateBusy, setDateBusy] = useState(false);
  const [dateMessage, setDateMessage] = useState<string | null>(null);
  const dateIntent = useIntent(`portal-${props.environment}-document-date`);

  const [importClaims, setImportClaims] = useState<string[]>([]);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [receipt, setReceipt] = useState<InvalidityEvidenceUploadReceipt | null>(null);
  const [canonicalKey, setCanonicalKey] = useState('');
  const [documentType, setDocumentType] = useState('patent');
  const [title, setTitle] = useState('');
  const [language, setLanguage] = useState('zh');
  const [importPublicationNumber, setImportPublicationNumber] = useState('');
  const [importAuthority, setImportAuthority] = useState('');
  const [importPublicDate, setImportPublicDate] = useState('');
  const [importPublicationDate, setImportPublicationDate] = useState('');
  const [importFilingDate, setImportFilingDate] = useState('');
  const [importPriorityDate, setImportPriorityDate] = useState('');
  const [importChannel, setImportChannel] = useState<DateChannel>('ordinary_prior_art');
  const [importDateEvidenceSource, setImportDateEvidenceSource] = useState('');
  const [importDateEvidenceLocator, setImportDateEvidenceLocator] = useState('');
  const [importReason, setImportReason] = useState('人工补充真实现有技术材料，交由系统重新核验日期并执行 I4-S');
  const [importBusy, setImportBusy] = useState(false);
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const importIntent = useIntent(`portal-${props.environment}-evidence-import`);

  useEffect(() => {
    if (!enabled) {
      setContext(null);
      setContextError(null);
      return;
    }
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const response = await fetch(urls.context, { cache: 'no-store' });
        const data = await jsonResponse(response);
        assertInvalidityReviewContext(data);
        if (!cancelled) {
          setContext(data);
          setContextError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setContext(null);
          setContextError(error instanceof Error ? error.message : '读取人工复核上下文失败');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [enabled, refreshNonce, urls.context]);

  const claims = context?.claims || [];
  const documents = context?.documents || [];
  const pendingDateDocuments = useMemo(
    () => context ? invalidityPendingDateReviewDocuments(context) : [],
    [context],
  );
  const dateDocuments = props.focus === 'pending_date_review'
    ? pendingDateDocuments
    : documents;
  const selectedDateDocument = dateDocuments.find((item) => text(item.id) === dateDocumentId);
  useEffect(() => {
    if (!context) return;
    setDateDocumentId((current) => (
      dateDocuments.some((document) => text(document.id) === current)
        ? current
        : text(dateDocuments[0]?.id)
    ));
  }, [context, dateDocuments]);
  const qualificationByClaim = useMemo(
    () => selectedDateDocument && context
      ? invalidityReviewQualificationsByClaim(context, selectedDateDocument)
      : new Map<string, JsonObject>(),
    [context, selectedDateDocument],
  );
  const dateDefaults = useMemo(
    () => selectedDateDocument && context
      ? invalidityDateReviewDefaults(context, selectedDateDocument)
      : null,
    [context, selectedDateDocument],
  );
  useEffect(() => {
    if (!dateDefaults) return;
    setDateClaims(dateDefaults.pendingClaimIds);
    setDateDecision('confirm_facts');
    setDateChannel('ordinary_prior_art');
    setPublicAvailabilityDate(dateDefaults.publicAvailabilityDate);
    setPublicationDate(dateDefaults.publicationDate);
    setFilingDate(dateDefaults.filingDate);
    setPriorityDate(dateDefaults.priorityDate);
    setDateSourceType(dateDefaults.sourceType);
    setPublicationNumber(dateDefaults.publicationNumber);
    setAuthority(dateDefaults.authority);
    setDateEvidenceSource(dateDefaults.dateEvidenceSource);
    setDateEvidenceLocator(dateDefaults.dateEvidenceLocator);
    setDateReason(dateDefaults.reason);
    setDateMessage(null);
  }, [dateDefaults]);
  const currentGaps = useMemo(
    () => latestInvalidityGapsByKey(
      context?.current_gaps?.length
        ? context.current_gaps
        : reportRows(context?.open_gaps),
    ),
    [context],
  );
  const currentD1 = context?.current_d1?.length
    ? context.current_d1
    : reportRows(context?.closest_prior_art_history).filter((item) => item.is_current === true);
  const explicitD1Candidates = documents.filter((item) => (
    item.selectable_as_d1 === true
    || (Array.isArray(item.d1_candidate_for_claims) && item.d1_candidate_for_claims.length > 0)
  ));
  const hasDateFacts = Boolean(
    publicAvailabilityDate || publicationDate || filingDate || priorityDate,
  );

  const refresh = () => {
    setRefreshNonce((value) => value + 1);
    props.onChanged?.();
  };

  const uploadEvidence = async () => {
    if (!importFile) return;
    setImportBusy(true);
    setImportMessage(null);
    try {
      const form = new FormData();
      form.append('file', importFile);
      if (props.environment === 'test') form.append('investigationId', text(props.investigationId));
      const uploaded = await jsonResponse(await fetch(urls.upload, { method: 'POST', body: form }));
      const nextReceipt = uploaded as unknown as InvalidityEvidenceUploadReceipt;
      if (
        nextReceipt.contract_version !== 'v1'
        || !text(nextReceipt.upload_receipt_id)
        || !/^[a-f0-9]{64}$/i.test(text(nextReceipt.sha256))
      ) {
        throw new Error('上传服务未返回有效的 owner-bound receipt');
      }
      setReceipt(nextReceipt);
      setImportMessage('文件已冻结并绑定当前用户、会话和环境；现在可以提交导入。');
    } catch (error) {
      setReceipt(null);
      setImportMessage(error instanceof Error ? error.message : '上传对比材料失败');
    } finally {
      setImportBusy(false);
    }
  };

  const submitEvidenceImport = async () => {
    if (!context || !receipt || !importClaims.length) return;
    const declaredDateFacts = {
      ...(importPublicDate ? { public_availability_date: importPublicDate } : {}),
      ...(importPublicationDate ? { publication_date: importPublicationDate } : {}),
      ...(importFilingDate ? { filing_date: importFilingDate } : {}),
      ...(importPriorityDate ? { priority_date: importPriorityDate } : {}),
      date_channel: importChannel,
    };
    const dateEvidence = importDateEvidenceSource
      ? Object.fromEntries(
        [
          ['public_availability_date', importPublicDate],
          ['publication_date', importPublicationDate],
          ['filing_date', importFilingDate],
          ['priority_date', importPriorityDate],
        ]
          .filter(([, value]) => Boolean(value))
          .map(([field]) => [field, {
            source: importDateEvidenceSource,
            ...(importDateEvidenceLocator ? { locator: importDateEvidenceLocator } : {}),
          }]),
      )
      : {};
    const intent = {
      upload_receipt_id: receipt.upload_receipt_id,
      claim_investigation_ids: importClaims,
      canonical_key: canonicalKey,
      document_type: documentType,
      title,
      language,
      publication_number: importPublicationNumber,
      authority: importAuthority,
      declared_date_facts: declaredDateFacts,
      date_evidence: dateEvidence,
      reason: importReason,
      expected_investigation_state_version: context.state_version,
      expected_claim_state_versions: expectedClaimStateVersions(claims, importClaims),
      expected_review_revision: context.review_revision,
    };
    const body = {
      ...intent,
      idempotency_key: importIntent.key(JSON.stringify(intent)),
    };
    setImportBusy(true);
    setImportMessage(null);
    try {
      const response = await jsonResponse(await fetch(urls.evidenceImport, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }));
      assertHumanReviewCommandResponse(response);
      setImportMessage(`材料导入复核动作已排队（${text(response.review_action_id)}）；旧报告已失效，等待重算。`);
      setReceipt(null);
      importIntent.reset();
      refresh();
    } catch (error) {
      setImportMessage(error instanceof Error ? error.message : '提交材料导入失败');
    } finally {
      setImportBusy(false);
    }
  };

  const submitDateConfirmation = async () => {
    if (!context || !selectedDateDocument || !dateClaims.length) return;
    const expectedQualifications = Object.fromEntries(dateClaims.map((claimId) => {
      const qualification = qualificationByClaim.get(claimId) || {};
      return [claimId, {
        qualification_id: text(qualification.id),
        assessment_version: Number(qualification.assessment_version || 0),
      }];
    }));
    const evidenceFields = [
      ['public_availability_date', publicAvailabilityDate],
      ['publication_date', publicationDate],
      ['filing_date', filingDate],
      ['priority_date', priorityDate],
    ].filter(([, value]) => Boolean(value));
    const dateEvidence = Object.fromEntries(
      evidenceFields.map(([field]) => [field, {
        source: dateEvidenceSource,
        ...(dateEvidenceLocator ? { locator: dateEvidenceLocator } : {}),
      }]),
    );
    const intent = {
      decision: dateDecision,
      document_id: text(selectedDateDocument.id),
      document_version_id: invalidityReviewCurrentDocumentVersionId(context, selectedDateDocument),
      claim_investigation_ids: dateClaims,
      expected_qualifications: expectedQualifications,
      public_availability_date: publicAvailabilityDate || undefined,
      publication_date: publicationDate || undefined,
      filing_date: filingDate || undefined,
      priority_date: priorityDate || undefined,
      source_type: dateSourceType,
      publication_number: publicationNumber || undefined,
      authority: authority || undefined,
      date_channel: dateChannel,
      date_evidence: dateEvidence,
      reason: dateReason,
      expected_investigation_state_version: context.state_version,
      expected_claim_state_versions: expectedClaimStateVersions(claims, dateClaims),
      expected_review_revision: context.review_revision,
    };
    const body = {
      ...intent,
      idempotency_key: dateIntent.key(JSON.stringify(intent)),
    };
    setDateBusy(true);
    setDateMessage(null);
    try {
      const response = await jsonResponse(await fetch(urls.dateConfirmation, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }));
      assertHumanReviewCommandResponse(response);
      setDateMessage(`日期复核动作已排队（${text(response.review_action_id)}）；资格将由确定性规则重新计算。`);
      refresh();
    } catch (error) {
      setDateMessage(error instanceof Error ? error.message : '提交日期确认失败');
    } finally {
      setDateBusy(false);
    }
  };

  if (!enabled) return null;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle>{props.focus === 'pending_date_review' ? '候选文献日期待复核' : '人工复核待办'}</CardTitle>
            <CardDescription>
              {props.focus === 'pending_date_review'
                ? '只显示后端明确标记为待复核的文献，并回填已检测事实；确认会追加新版本，不改写旧审计。'
                : '所有决定追加版本并触发受影响范围重算；不会直接改写旧 checkpoint 或旧报告。'}
            </CardDescription>
          </div>
          <Button size="sm" variant="outline" onClick={refresh} disabled={loading}>
            {loading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1 h-4 w-4" />}
            刷新复核上下文
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {contextError ? <Alert variant="destructive"><AlertTitle>人工复核当前只读</AlertTitle><AlertDescription>{contextError}</AlertDescription></Alert> : null}
        {context ? (
          <Alert variant={context.quiescent ? 'default' : 'destructive'}>
            <AlertTitle>{context.quiescent ? '可以提交人工复核' : '当前不能提交人工复核'}</AlertTitle>
            <AlertDescription>
              调查 state v{context.state_version}，review v{context.review_revision}。
              {context.quiescent ? '当前没有冲突的活动作业。' : '存在 queued/leased/running 作业或未收口轮次；后端会返回 REVIEW_NOT_QUIESCENT。'}
              {context.recomputation_required ? ' 当前存在待重算结果，旧报告不可导出。' : ''}
            </AlertDescription>
          </Alert>
        ) : null}

        {context && props.focus === 'pending_date_review' && !dateDocuments.length ? (
          <Alert>
            <AlertTitle>当前没有待确认的候选文献日期</AlertTitle>
            <AlertDescription>后端未返回 verification_status=needs_human_review 的文献资格版本，无需逐项重复确认已核验事实。</AlertDescription>
          </Alert>
        ) : null}

        {context && dateDocuments.length ? <section className="space-y-4 rounded-lg border p-4">
          <div><h3 className="font-semibold">候选文献日期确认</h3><p className="text-sm text-muted-foreground">只确认日期事实和证据；新颖性/创造性资格由后端日期规则产生。</p></div>
          {props.focus === 'pending_date_review' ? <Alert>
            <AlertTitle>系统只保留了 {dateDocuments.length} 份真正待复核文献</AlertTitle>
            <AlertDescription>日期、公开号、国家/机构和冻结证据定位已根据当前文献版本回填；请只核对是否与原件一致。</AlertDescription>
          </Alert> : null}
          <Select value={dateDocumentId} onValueChange={setDateDocumentId}>
            <SelectTrigger><SelectValue placeholder="选择待复核文献" /></SelectTrigger>
            <SelectContent>{dateDocuments.map((document) => {
              const defaults = invalidityDateReviewDefaults(context, document);
              return <SelectItem key={text(document.id)} value={text(document.id)}>{text(document.title || document.canonical_key)}{defaults.publicationNumber ? ` · ${defaults.publicationNumber}` : ''}{defaults.publicationDate ? ` · ${defaults.publicationDate}` : ''}</SelectItem>;
            })}</SelectContent>
          </Select>
          {dateDefaults ? <div className="rounded-md bg-muted/50 p-3 text-sm">
            <p className="font-medium">后台已检测的事实</p>
            <p className="mt-1 text-muted-foreground">
              {dateDefaults.publicationNumber || '无公开号'} · {dateDefaults.authority || '无机构'} · 公众可得日 {dateDefaults.publicAvailabilityDate || '未检测'} · 公开日 {dateDefaults.publicationDate || '未检测'}
            </p>
            {dateDefaults.verificationReasons.length ? <p className="mt-1 text-amber-700">仅待复核：{dateDefaults.verificationReasons.join('；')}</p> : null}
          </div> : null}
          {selectedDateDocument ? <ClaimChecks claims={claims} selected={dateClaims} setSelected={setDateClaims} allowed={new Set(qualificationByClaim.keys())} /> : null}
          <div className="grid gap-3 md:grid-cols-3">
            <label className="space-y-1 text-sm"><span>决定</span><Select value={dateDecision} onValueChange={(value) => setDateDecision(value as DateDecision)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="confirm_facts">确认事实并重算</SelectItem><SelectItem value="exclude">排除该日期主张</SelectItem><SelectItem value="reopen_review">重新打开复核</SelectItem></SelectContent></Select></label>
            <label className="space-y-1 text-sm"><span>日期通道</span><Select value={dateChannel} onValueChange={(value) => setDateChannel(value as DateChannel)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ordinary_prior_art">普通现有技术</SelectItem><SelectItem value="cn_conflicting_application">中国抵触申请候选</SelectItem></SelectContent></Select></label>
            <label className="space-y-1 text-sm"><span>来源类型</span><Input value={dateSourceType} onChange={(event) => setDateSourceType(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>公众可得日</span><Input type="date" value={publicAvailabilityDate} onChange={(event) => setPublicAvailabilityDate(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>公开日</span><Input type="date" value={publicationDate} onChange={(event) => setPublicationDate(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>申请日</span><Input type="date" value={filingDate} onChange={(event) => setFilingDate(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>优先权日</span><Input type="date" value={priorityDate} onChange={(event) => setPriorityDate(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>公开编号</span><Input value={publicationNumber} onChange={(event) => setPublicationNumber(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>国家/机构</span><Input value={authority} onChange={(event) => setAuthority(event.target.value)} /></label>
          </div>
          <div className="grid gap-3 md:grid-cols-2"><label className="space-y-1 text-sm"><span>日期证据来源</span><Input value={dateEvidenceSource} onChange={(event) => setDateEvidenceSource(event.target.value)} /></label><label className="space-y-1 text-sm"><span>证据定位</span><Input value={dateEvidenceLocator} onChange={(event) => setDateEvidenceLocator(event.target.value)} placeholder="例如：扉页著录项目、页 1" /></label></div>
          <label className="space-y-1 text-sm"><span>复核理由</span><Textarea value={dateReason} onChange={(event) => setDateReason(event.target.value)} /></label>
          {dateDecision === 'confirm_facts' && !hasDateFacts ? <p className="text-sm text-amber-700">确认日期事实前，至少填写公众可得日、公开日、申请日或优先权日之一。</p> : null}
          <div className="flex flex-wrap items-center gap-3"><Button onClick={() => void submitDateConfirmation()} disabled={dateBusy || !context.quiescent || !dateClaims.length || !dateDocumentId || (dateDecision === 'confirm_facts' && !hasDateFacts) || (hasDateFacts && !dateEvidenceSource.trim()) || !dateReason.trim()}>{dateBusy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <FileCheck2 className="mr-1 h-4 w-4" />}提交日期复核</Button>{dateMessage ? <span className="text-sm text-muted-foreground">{dateMessage}</span> : null}</div>
        </section> : null}

        {context && props.focus !== 'pending_date_review' ? <section className="space-y-4 rounded-lg border p-4">
          <div><h3 className="font-semibold">导入真实现有技术材料</h3><p className="text-sm text-muted-foreground">当前仅接受可可靠生成文本和页图的 PDF。先生成当前用户/会话/环境专属上传收据；浏览器不会取得或提交服务器路径。导入只到 retrieved_document，日期仍需核验。</p></div>
          <ClaimChecks claims={claims} selected={importClaims} setSelected={setImportClaims} />
          <div className="grid gap-3 md:grid-cols-[1fr_auto]"><Input type="file" accept=".pdf,application/pdf" onChange={(event) => { setImportFile(event.target.files?.[0] || null); setReceipt(null); }} /><Button variant="outline" onClick={() => void uploadEvidence()} disabled={importBusy || !importFile}><Upload className="mr-1 h-4 w-4" />生成绑定收据</Button></div>
          {receipt ? <Alert><AlertTitle>上传收据已就绪</AlertTitle><AlertDescription>{receipt.file_name} · {receipt.byte_size} bytes · SHA-256 {receipt.sha256}</AlertDescription></Alert> : null}
          <div className="grid gap-3 md:grid-cols-3">
            <label className="space-y-1 text-sm"><span>规范文献标识</span><Input value={canonicalKey} onChange={(event) => setCanonicalKey(event.target.value)} placeholder="例如公开编号、DOI 或稳定来源键" /></label>
            <label className="space-y-1 text-sm"><span>文献题名</span><Input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>文献类型</span><Select value={documentType} onValueChange={setDocumentType}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="patent">专利</SelectItem><SelectItem value="paper">论文</SelectItem><SelectItem value="standard">标准</SelectItem><SelectItem value="manual">产品/用户手册</SelectItem><SelectItem value="datasheet">datasheet</SelectItem><SelectItem value="web">网页/论坛</SelectItem><SelectItem value="other">其他</SelectItem></SelectContent></Select></label>
            <label className="space-y-1 text-sm"><span>语言</span><Input value={language} onChange={(event) => setLanguage(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>公开编号</span><Input value={importPublicationNumber} onChange={(event) => setImportPublicationNumber(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>国家/机构</span><Input value={importAuthority} onChange={(event) => setImportAuthority(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>公众可得日（声明）</span><Input type="date" value={importPublicDate} onChange={(event) => setImportPublicDate(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>公开日（声明）</span><Input type="date" value={importPublicationDate} onChange={(event) => setImportPublicationDate(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>申请日（声明）</span><Input type="date" value={importFilingDate} onChange={(event) => setImportFilingDate(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>优先权日（声明）</span><Input type="date" value={importPriorityDate} onChange={(event) => setImportPriorityDate(event.target.value)} /></label>
            <label className="space-y-1 text-sm"><span>日期通道</span><Select value={importChannel} onValueChange={(value) => setImportChannel(value as DateChannel)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="ordinary_prior_art">普通现有技术</SelectItem><SelectItem value="cn_conflicting_application">中国抵触申请候选</SelectItem></SelectContent></Select></label>
          </div>
          <div className="grid gap-3 md:grid-cols-2"><label className="space-y-1 text-sm"><span>日期证据来源（可稍后确认）</span><Input value={importDateEvidenceSource} onChange={(event) => setImportDateEvidenceSource(event.target.value)} /></label><label className="space-y-1 text-sm"><span>证据定位</span><Input value={importDateEvidenceLocator} onChange={(event) => setImportDateEvidenceLocator(event.target.value)} /></label></div>
          <label className="space-y-1 text-sm"><span>导入理由</span><Textarea value={importReason} onChange={(event) => setImportReason(event.target.value)} /></label>
          <div className="flex flex-wrap items-center gap-3"><Button onClick={() => void submitEvidenceImport()} disabled={importBusy || !context.quiescent || !receipt || !importClaims.length || !canonicalKey.trim() || !title.trim() || !importReason.trim()}>{importBusy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Upload className="mr-1 h-4 w-4" />}提交材料导入</Button>{importMessage ? <span className="text-sm text-muted-foreground">{importMessage}</span> : null}</div>
        </section> : null}

        {context && props.focus !== 'pending_date_review' ? <section className="space-y-3 rounded-lg border p-4">
          <div><h3 className="font-semibold">D1 候选与当前选择</h3><p className="text-sm text-muted-foreground">只展示后端明确确认可选的候选；不会按相似度自行晋升 D1。</p></div>
          {currentD1.length ? currentD1.map((item) => <div key={text(item.id)} className="rounded border p-2 text-sm">{claimDisplayLabel(claims.find((claim) => text(claim.id) === text(item.claim_investigation_id)), item.claim_investigation_id)}：document version {text(item.document_version_id)}，D1 v{text(item.version_no)}</div>) : <p className="text-sm text-muted-foreground">尚无当前 D1。</p>}
          {explicitD1Candidates.length ? explicitD1Candidates.map((item) => <div key={text(item.id)} className="rounded border p-2 text-sm"><span>{text(item.title || item.canonical_key)}</span><Badge className="ml-2" variant="outline">候选，只读</Badge></div>) : <p className="text-sm text-muted-foreground">review-context 尚未返回明确可选 D1 候选。</p>}
          <p className="text-xs text-muted-foreground">P0 不提供 D1 改选动作；需等后端冻结 qualification revision、I4-S 哈希和 limitation-set hash 契约后再启用。</p>
        </section> : null}

        {context && props.focus !== 'pending_date_review' ? <section className="space-y-3 rounded-lg border p-4">
          <div><h3 className="font-semibold">当前 gap frontier</h3><p className="text-sm text-muted-foreground">按 claim + gap_key 只显示最新版本；历史 open/closed 行不会混入当前待办。</p></div>
          {currentGaps.length ? currentGaps.map((gap) => <div key={`${text(gap.claim_investigation_id)}-${text(gap.gap_key)}`} className="rounded border p-3 text-sm"><div className="flex flex-wrap gap-2"><Badge variant="outline">{claimDisplayLabel(claims.find((claim) => text(claim.id) === text(gap.claim_investigation_id)), gap.claim_investigation_id)}</Badge><Badge variant="secondary">{text(gap.gap_type)}</Badge><Badge variant="outline">v{text(gap.version_no)} · {text(gap.status)}</Badge></div><p className="mt-2 whitespace-pre-wrap">{text(gap.description)}</p></div>) : <p className="text-sm text-muted-foreground">当前没有服务端返回的 open gap。</p>}
          <p className="text-xs text-muted-foreground">P0 只读展示 gap；不会发送 close/reopen/stop_search，也不会把停止追索制造成成功结论。</p>
        </section> : null}

        {context && props.focus !== 'pending_date_review' && (reportRows(context.pending_actions).length || context.action_history.length || reportRows(context.recent_actions).length) ? <details><summary className="cursor-pointer text-sm font-medium">查看人工 action 日志</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-3 text-xs text-slate-100">{JSON.stringify(reportRows(context.pending_actions).length ? reportRows(context.pending_actions) : (context.action_history.length ? context.action_history : reportRows(context.recent_actions)), null, 2)}</pre></details> : null}
      </CardContent>
    </Card>
  );
}
