'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import {
  ArrowLeft,
  CheckCircle2,
  CircleDashed,
  Database,
  FileSearch,
  ImageOff,
  Loader2,
  Play,
  RefreshCw,
  Scale,
  Search,
  ShieldCheck,
  Tags,
  XCircle,
} from 'lucide-react';
import { UploadForm } from '@/components/upload-form';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { IndustryType } from '@/lib/types';

type PipelineAction = 'patentParse' | 'keywords' | 'productSearch' | 'claimCompare';
type ModuleKey = 'module1' | 'module2' | 'module3' | 'module4';
type RunStatus = 'idle' | 'running' | 'completed' | 'error';
type JsonObject = Record<string, unknown>;

type StartPayload = {
  type: 'url' | 'file' | 'text';
  url?: string;
  fileKey?: string;
  fileName?: string;
  fileUrl?: string;
  text?: string;
};

type KeywordRow = {
  id?: number;
  keyword_text?: string;
  keyword_type?: string;
  source_location?: string;
  confidence_score?: number;
};

type ProductRow = {
  id?: number;
  platform?: string;
  product_name?: string;
  product_url?: string;
  final_url?: string;
  price?: string;
  brand?: string;
  manufacturer?: string;
  matched_keywords?: string;
  description?: string;
  picture?: string[];
  quality_score?: number;
};

type CompareRow = {
  product_name?: string;
  claim_id?: string;
  feature_id?: string;
  comparison_result?: string;
  similarity_score?: number;
  score_band?: string;
  feature_awarded_score?: number;
  reason?: string;
  evidence?: string;
};

type CandidateRow = {
  id?: number;
  keyword_text?: string;
  platform?: string;
  candidate_url?: string;
  final_url?: string;
  title?: string;
  status?: string;
  rejection_reason?: string;
  quality_score?: number;
};

type CandidateSummaryRow = {
  status?: string;
  rejection_reason?: string;
  count?: number;
};

type StepPayload = JsonObject & {
  status?: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  elapsedMs?: number;
  errorMessage?: string;
  patentRecordId?: number;
  taskId?: string;
  runId?: string;
  inputType?: string;
  inputLabel?: string;
  claimsCount?: number;
  figuresCount?: number;
  metadata?: JsonObject;
  finalOutput?: JsonObject;
  keywordRunId?: number;
  keywords?: KeywordRow[];
  keywordsCount?: number;
  productDetailSearchRunId?: number;
  acceptedProductsCount?: number;
  totalCandidateLinksCount?: number;
  rejectedCandidatesCount?: number;
  products?: ProductRow[];
  candidateSummary?: CandidateSummaryRow[];
  candidatesPreview?: CandidateRow[];
  claimCompareRunId?: number;
  productCount?: number;
  featureCount?: number;
  resultSummary?: string;
  rows?: CompareRow[];
};

type PipelineResponse = {
  ok?: boolean;
  action?: PipelineAction;
  patentRecordId?: number;
  analysisSessionId?: string;
  elapsedMs?: number;
  error?: string;
  patentParseStep?: StepPayload;
  keywordStep?: StepPayload;
  productSearchStep?: StepPayload;
  claimCompareStep?: StepPayload;
};

type ModuleState = {
  status: RunStatus;
  result: StepPayload | null;
  error: string | null;
};

type ExamplePatent = {
  id: number;
  patentNumber?: string;
  title?: string;
};

type DiagnosticResponse = {
  diagnostic_mode?: 'same_task_read_only';
  readOnly?: boolean;
  patentRecordId?: number | null;
  session?: {
    id?: string;
    status?: string;
    pipelineVersion?: string | null;
    patentTitle?: string | null;
    patentNumber?: string | null;
    createdAt?: string;
    updatedAt?: string;
    errors?: string[];
  };
  inputs?: Partial<Record<ModuleKey, JsonObject>>;
  modules?: Partial<Record<ModuleKey, ModuleState>>;
  error?: string;
};

const MODULES: Array<{
  key: ModuleKey;
  action: PipelineAction;
  title: string;
  description: string;
  icon: typeof FileSearch;
}> = [
  { key: 'module1', action: 'patentParse', title: '专利解析', description: '读取专利原文，提取著录信息、权利要求、说明书和附图。', icon: FileSearch },
  { key: 'module2', action: 'keywords', title: '关键词生成', description: '基于当前专利解析事实生成商品检索关键词。', icon: Tags },
  { key: 'module3', action: 'productSearch', title: '商品检索', description: '使用上一阶段关键词检索并读取真实商品详情。', icon: Search },
  { key: 'module4', action: 'claimCompare', title: '权利要求与商品比对', description: '逐项比对当前专利独立权利要求与本次检索商品。', icon: Scale },
];

const EMPTY_MODULES: Record<ModuleKey, ModuleState> = {
  module1: { status: 'idle', result: null, error: null },
  module2: { status: 'idle', result: null, error: null },
  module3: { status: 'idle', result: null, error: null },
  module4: { status: 'idle', result: null, error: null },
};

const ACTION_TO_MODULE: Record<PipelineAction, ModuleKey> = {
  patentParse: 'module1',
  keywords: 'module2',
  productSearch: 'module3',
  claimCompare: 'module4',
};

function makeSessionId(): string {
  return `patent_analysis_lab_${Date.now()}`;
}

function formatMs(ms?: number): string {
  if (typeof ms !== 'number') return '尚未记录';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} 秒`;
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value);
}

function inputSummary(value: unknown): string {
  if (Array.isArray(value)) return value.length ? `${value.length} 项` : '0 项';
  if (value && typeof value === 'object') return `${Object.keys(value as JsonObject).length} 个字段`;
  return stringValue(value) || '未提供';
}

function objectValue(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {};
}

function arrayValue<T>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

function stepForAction(response: PipelineResponse, action: PipelineAction): StepPayload | null {
  if (action === 'patentParse') return response.patentParseStep || null;
  if (action === 'keywords') return response.keywordStep || null;
  if (action === 'productSearch') return response.productSearchStep || null;
  return response.claimCompareStep || null;
}

function statusBadge(status: RunStatus) {
  if (status === 'running') return <Badge variant="secondary"><Loader2 className="mr-1 h-3 w-3 animate-spin" />运行中</Badge>;
  if (status === 'completed') return <Badge className="bg-emerald-600"><CheckCircle2 className="mr-1 h-3 w-3" />已完成</Badge>;
  if (status === 'error') return <Badge variant="destructive"><XCircle className="mr-1 h-3 w-3" />失败</Badge>;
  return <Badge variant="outline"><CircleDashed className="mr-1 h-3 w-3" />未运行</Badge>;
}

function RawJson({ value }: { value: unknown }) {
  return (
    <details className="rounded-md border bg-muted/20 p-3 text-xs" data-patent-analysis-raw-output>
      <summary className="cursor-pointer font-medium">查看完整原始数据</summary>
      <pre className="mt-3 max-h-[520px] overflow-auto whitespace-pre-wrap break-all rounded bg-background p-3">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

function ProductImages({ product }: { product: ProductRow }) {
  const urls = arrayValue<string>(product.picture).filter(Boolean);
  if (!urls.length) {
    return <span className="inline-flex items-center gap-1 text-xs text-muted-foreground"><ImageOff className="h-3.5 w-3.5" />无图片</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {urls.slice(0, 3).map((url, index) => (
        <a key={`${url}-${index}`} href={url} target="_blank" rel="noopener noreferrer">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt={`商品图 ${index + 1}`} className="h-12 w-12 rounded border bg-muted object-cover" />
        </a>
      ))}
    </div>
  );
}

function InputDetails({ value }: { value: JsonObject }) {
  const entries = Object.entries(value).filter(([, item]) => item !== undefined);
  return (
    <div className="space-y-3">
      <div className="grid gap-2 text-sm sm:grid-cols-2">
        {entries.slice(0, 8).map(([key, item]) => (
          <div key={key} className="rounded-md border bg-background px-3 py-2">
            <p className="text-[11px] text-muted-foreground">{key}</p>
            <p className="mt-0.5 line-clamp-3 break-all font-medium">
              {inputSummary(item)}
            </p>
          </div>
        ))}
      </div>
      <RawJson value={value} />
    </div>
  );
}

function StageOutput({ moduleKey, result }: { moduleKey: ModuleKey; result: StepPayload | null }) {
  if (!result) return <p className="text-sm text-muted-foreground">本阶段尚未产生输出。</p>;
  const identifiers = moduleKey === 'module1'
    ? [`patent_record_id ${result.patentRecordId || '-'}`, `run ${result.runId || result.taskId || '-'}`]
    : moduleKey === 'module2'
      ? [`keyword_run_id ${result.keywordRunId || '-'}`, `关键词 ${result.keywordsCount ?? arrayValue(result.keywords).length}`]
      : moduleKey === 'module3'
        ? [`product_search_run_id ${result.productDetailSearchRunId || '-'}`, `有效商品 ${result.acceptedProductsCount ?? arrayValue(result.products).length}`]
        : [`claim_compare_run_id ${result.claimCompareRunId || '-'}`, `比对行 ${result.featureCount ?? arrayValue(result.rows).length}`];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Badge variant={result.status === 'completed' ? 'secondary' : result.status === 'failed' ? 'destructive' : 'outline'}>
          {result.status === 'completed'
            ? '输出已保存'
            : result.status === 'skipped'
              ? '未执行'
              : result.status === 'running'
                ? '正在运行'
                : result.status === 'pending'
                  ? '等待运行'
                  : '输出失败'}
        </Badge>
        {identifiers.map((item) => <Badge key={item} variant="outline">{item}</Badge>)}
        <Badge variant="outline">耗时 {formatMs(result.elapsedMs)}</Badge>
      </div>
      {result.errorMessage ? (
        <Alert variant="destructive"><AlertDescription>{result.errorMessage}</AlertDescription></Alert>
      ) : null}
      <RawJson value={result} />
    </div>
  );
}

function StageResult({ moduleKey, result }: { moduleKey: ModuleKey; result: StepPayload | null }) {
  if (!result) return <p className="text-sm text-muted-foreground">运行完成后在这里显示可读结果。</p>;
  const hasReadableData = moduleKey === 'module1'
    ? Object.keys(objectValue(result.finalOutput)).length > 0 || Object.keys(objectValue(result.metadata)).length > 0
    : moduleKey === 'module2'
      ? arrayValue(result.keywords).length > 0
      : moduleKey === 'module3'
        ? arrayValue(result.products).length > 0 || arrayValue(result.candidatesPreview).length > 0
        : arrayValue(result.rows).length > 0 || Boolean(result.resultSummary);
  if (result.status !== 'completed' && !hasReadableData) {
    return (
      <div className="space-y-2 text-sm text-muted-foreground">
        <p>{result.status === 'skipped' ? '本阶段没有执行。' : result.status === 'pending' ? '本阶段尚未开始。' : '本次没有形成可读结果。'}</p>
        {result.errorMessage ? <p className="whitespace-pre-wrap text-destructive">{result.errorMessage}</p> : null}
        <p>技术状态、失败原因和持久化快照请查看“本次输出”。</p>
      </div>
    );
  }
  const stageNotice = result.status !== 'completed' ? (
    <Alert variant="destructive">
      <AlertDescription>本阶段未成功收口；下方保留的是失败前已经持久化的部分结果。</AlertDescription>
    </Alert>
  ) : null;

  if (moduleKey === 'module1') {
    const finalOutput = objectValue(result.finalOutput);
    const metadata = objectValue(result.metadata || finalOutput.metadata);
    const claims = arrayValue<JsonObject>(finalOutput.claims);
    const figures = arrayValue<JsonObject>(finalOutput.figures);
    return (
      <div className="space-y-4">
        {stageNotice}
        <div className="flex flex-wrap gap-2"><Badge variant="secondary">权利要求 {result.claimsCount ?? claims.length}</Badge><Badge variant="outline">附图 {result.figuresCount ?? figures.length}</Badge></div>
        <div className="grid gap-3 text-sm md:grid-cols-2">
          <div className="rounded border bg-background p-3"><p className="text-xs text-muted-foreground">专利名称</p><p className="mt-1 font-medium">{stringValue(metadata.title) || '未识别'}</p></div>
          <div className="rounded border bg-background p-3"><p className="text-xs text-muted-foreground">专利号</p><p className="mt-1 font-medium">{stringValue(metadata.patent_number) || '未识别'}</p></div>
        </div>
        {claims.length ? (
          <Accordion type="multiple" className="rounded border px-3">
            {claims.map((claim, index) => (
              <AccordionItem key={`${stringValue(claim.claim_id)}-${index}`} value={`claim-${index}`}>
                <AccordionTrigger className="text-left">权利要求 {stringValue(claim.claim_id) || index + 1}</AccordionTrigger>
                <AccordionContent><p className="whitespace-pre-wrap text-sm leading-6">{stringValue(claim.claim_text)}</p></AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        ) : null}
      </div>
    );
  }

  if (moduleKey === 'module2') {
    const keywords = arrayValue<KeywordRow>(result.keywords);
    return (
      <div className="space-y-4">
        {stageNotice}
        <div className="flex flex-wrap gap-2"><Badge variant="secondary">关键词 {keywords.length}</Badge></div>
        <Table>
          <TableHeader><TableRow><TableHead>检索词</TableHead><TableHead>类型</TableHead><TableHead>来源</TableHead></TableRow></TableHeader>
          <TableBody>{keywords.map((row, index) => <TableRow key={row.id || index}><TableCell className="font-medium">{row.keyword_text || '-'}</TableCell><TableCell>{row.keyword_type || '-'}</TableCell><TableCell>{row.source_location || '-'}</TableCell></TableRow>)}</TableBody>
        </Table>
        {!keywords.length ? <p className="text-sm text-muted-foreground">没有生成关键词。</p> : null}
      </div>
    );
  }

  if (moduleKey === 'module3') {
    const products = arrayValue<ProductRow>(result.products);
    const candidates = arrayValue<CandidateRow>(result.candidatesPreview);
    const candidateSummary = arrayValue<CandidateSummaryRow>(result.candidateSummary);
    return (
      <div className="space-y-4">
        {stageNotice}
        <div className="flex flex-wrap gap-2"><Badge variant="secondary">有效商品 {result.acceptedProductsCount ?? products.length}</Badge><Badge variant="outline">候选 {result.totalCandidateLinksCount ?? 0}</Badge><Badge variant="outline">排除 {result.rejectedCandidatesCount ?? 0}</Badge></div>
        <Accordion type="multiple" className="rounded border px-3">
          {products.map((product, index) => (
            <AccordionItem key={product.id || index} value={`product-${product.id || index}`}>
              <AccordionTrigger className="gap-3 text-left"><span className="min-w-0 flex-1"><span className="block font-medium">{product.product_name || `商品 ${index + 1}`}</span><span className="text-xs font-normal text-muted-foreground">{product.platform || '未知平台'} · {product.price || '价格未记录'}</span></span></AccordionTrigger>
              <AccordionContent className="space-y-3">
                <ProductImages product={product} />
                <p className="whitespace-pre-wrap text-sm">{product.description || '没有商品详情文字。'}</p>
                {(product.final_url || product.product_url) ? <a className="text-sm text-primary underline" href={product.final_url || product.product_url} target="_blank" rel="noopener noreferrer">打开商品来源</a> : null}
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
        {!products.length ? <p className="text-sm text-muted-foreground">没有取得有效商品。</p> : null}
        {candidateSummary.length ? (
          <div className="space-y-2">
            <p className="text-sm font-medium">候选核验汇总</p>
            <div className="flex flex-wrap gap-2">
              {candidateSummary.map((row, index) => (
                <Badge key={`${row.status}-${row.rejection_reason}-${index}`} variant="outline">
                  {row.status || '未知状态'} · {row.rejection_reason || '未记录原因'} · {row.count ?? 0} 条
                </Badge>
              ))}
            </div>
          </div>
        ) : null}
        {candidates.length ? (
          <Accordion type="multiple" className="rounded border px-3">
            {candidates.map((candidate, index) => (
              <AccordionItem key={candidate.id || index} value={`candidate-${candidate.id || index}`}>
                <AccordionTrigger className="gap-3 text-left">
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium">{candidate.title || candidate.candidate_url || `候选 ${index + 1}`}</span>
                    <span className="text-xs font-normal text-muted-foreground">
                      {candidate.keyword_text || '未记录检索词'} · {candidate.platform || '未知平台'} · {candidate.status || '未知状态'}
                    </span>
                  </span>
                </AccordionTrigger>
                <AccordionContent className="space-y-2 text-sm">
                  <p><span className="font-medium">核验结果：</span>{candidate.rejection_reason || '未记录排除原因'}</p>
                  {(candidate.final_url || candidate.candidate_url) ? (
                    <a className="break-all text-primary underline" href={candidate.final_url || candidate.candidate_url} target="_blank" rel="noopener noreferrer">打开候选来源</a>
                  ) : null}
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        ) : null}
      </div>
    );
  }

  const rows = arrayValue<CompareRow>(result.rows);
  return (
    <div className="space-y-4">
      {stageNotice}
      <div className="flex flex-wrap gap-2"><Badge variant="secondary">商品 {result.productCount ?? 0}</Badge><Badge variant="outline">比对行 {result.featureCount ?? rows.length}</Badge></div>
      {result.resultSummary ? <div className="rounded border bg-background p-3 text-sm whitespace-pre-wrap">{result.resultSummary}</div> : null}
      <Accordion type="multiple" className="rounded border px-3">
        {rows.map((row, index) => (
          <AccordionItem key={`${row.product_name}-${row.claim_id}-${row.feature_id}-${index}`} value={`compare-${index}`}>
            <AccordionTrigger className="gap-3 text-left"><span className="min-w-0 flex-1"><span className="block font-medium">{row.product_name || '未命名商品'} · 特征 {row.feature_id || index + 1}</span><span className="text-xs font-normal text-muted-foreground">{row.comparison_result || '未给出结论'} · 相似度 {row.similarity_score ?? '-'}%</span></span></AccordionTrigger>
            <AccordionContent className="space-y-2 text-sm"><p><span className="font-medium">理由：</span>{row.reason || '未记录'}</p><p><span className="font-medium">证据：</span>{row.evidence || '未记录'}</p></AccordionContent>
          </AccordionItem>
        ))}
      </Accordion>
      {!rows.length ? <p className="text-sm text-muted-foreground">没有取得逐项比对结果。</p> : null}
    </div>
  );
}

function ProductPipelineTestContent() {
  const parameters = useSearchParams();
  const attachedSessionId = parameters.get('session')?.trim() || '';
  const isReadOnlyDiagnostic = Boolean(attachedSessionId);
  const [analysisSessionId, setAnalysisSessionId] = useState(() => attachedSessionId || makeSessionId());
  const [patentRecordId, setPatentRecordId] = useState<number | null>(null);
  const [patentInput, setPatentInput] = useState<JsonObject | null>(null);
  const [industry, setIndustry] = useState<IndustryType>('general');
  const [platforms, setPlatforms] = useState('jd,1688');
  const [maxKeywords, setMaxKeywords] = useState('3');
  const [maxCandidatesPerKeyword, setMaxCandidatesPerKeyword] = useState('8');
  const [maxDetailCandidates, setMaxDetailCandidates] = useState('30');
  const [maxProducts, setMaxProducts] = useState('3');
  const [modules, setModules] = useState<Record<ModuleKey, ModuleState>>(EMPTY_MODULES);
  const [examples, setExamples] = useState<ExamplePatent[]>([]);
  const [examplesError, setExamplesError] = useState<string | null>(null);
  const [diagnosticInputs, setDiagnosticInputs] = useState<Partial<Record<ModuleKey, JsonObject>>>({});
  const [diagnosticSession, setDiagnosticSession] = useState<NonNullable<DiagnosticResponse['session']> | null>(null);
  const [diagnosticError, setDiagnosticError] = useState<string | null>(null);
  const [diagnosticLoading, setDiagnosticLoading] = useState(isReadOnlyDiagnostic);
  const [diagnosticRefresh, setDiagnosticRefresh] = useState(0);

  useEffect(() => {
    if (isReadOnlyDiagnostic) return undefined;
    let cancelled = false;
    void fetch('/api/test/product-pipeline', { cache: 'no-store' })
      .then(async (response) => {
        const data = await response.json() as { ok?: boolean; examples?: ExamplePatent[]; error?: string };
        if (!response.ok || data.ok === false) throw new Error(data.error || `HTTP ${response.status}`);
        if (!cancelled) setExamples(Array.isArray(data.examples) ? data.examples : []);
      })
      .catch((error) => {
        if (!cancelled) setExamplesError(error instanceof Error ? error.message : String(error));
      });
    return () => { cancelled = true; };
  }, [isReadOnlyDiagnostic]);

  useEffect(() => {
    if (!attachedSessionId) return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const loadDiagnostic = async () => {
      setDiagnosticLoading(true);
      try {
        const response = await fetch(
          `/api/admin/patent-analysis/session/${encodeURIComponent(attachedSessionId)}`,
          { cache: 'no-store' },
        );
        const data = await response.json() as DiagnosticResponse;
        if (!response.ok || data.diagnostic_mode !== 'same_task_read_only' || data.readOnly !== true) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        if (cancelled) return;
        const nextModules = data.modules || {};
        setAnalysisSessionId(data.session?.id || attachedSessionId);
        setPatentRecordId(typeof data.patentRecordId === 'number' ? data.patentRecordId : null);
        setPatentInput(data.inputs?.module1 || null);
        setDiagnosticInputs(data.inputs || {});
        setDiagnosticSession(data.session || null);
        setDiagnosticError(null);
        setModules({
          module1: nextModules.module1 || EMPTY_MODULES.module1,
          module2: nextModules.module2 || EMPTY_MODULES.module2,
          module3: nextModules.module3 || EMPTY_MODULES.module3,
          module4: nextModules.module4 || EMPTY_MODULES.module4,
        });
        const diagnosticIndustry = stringValue(data.inputs?.module2?.industry);
        if (['general', 'fitness_equipment', 'home_appliances'].includes(diagnosticIndustry)) {
          setIndustry(diagnosticIndustry as IndustryType);
        }
        if (data.session?.status === 'running') {
          timer = setTimeout(() => { void loadDiagnostic(); }, 3000);
        }
      } catch (error) {
        if (!cancelled) setDiagnosticError(error instanceof Error ? error.message : String(error));
      } finally {
        if (!cancelled) setDiagnosticLoading(false);
      }
    };

    void loadDiagnostic();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [attachedSessionId, diagnosticRefresh]);

  const currentKeywords = useMemo(
    () => arrayValue<KeywordRow>(modules.module2.result?.keywords).map((row) => row.keyword_text || '').filter(Boolean),
    [modules.module2.result],
  );
  const currentProducts = useMemo(
    () => arrayValue<ProductRow>(modules.module3.result?.products),
    [modules.module3.result],
  );

  const clearFrom = (moduleKey: ModuleKey) => {
    const index = MODULES.findIndex((item) => item.key === moduleKey);
    setModules((current) => {
      const next = { ...current };
      for (const item of MODULES.slice(index)) next[item.key] = { status: 'idle', result: null, error: null };
      return next;
    });
  };

  const runAction = async (
    action: PipelineAction,
    options: { sessionId?: string; patentId?: number | null; patentPayload?: StartPayload } = {},
  ) => {
    if (isReadOnlyDiagnostic) return;
    const moduleKey = ACTION_TO_MODULE[action];
    const sessionId = options.sessionId || analysisSessionId;
    const patentId = options.patentId === undefined ? patentRecordId : options.patentId;
    clearFrom(moduleKey);
    setModules((current) => ({ ...current, [moduleKey]: { status: 'running', result: null, error: null } }));
    try {
      const response = await fetch('/api/test/product-pipeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          patentInput: options.patentPayload,
          patentRecordId: patentId,
          analysisSessionId: sessionId,
          industry,
          platforms: platforms.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean),
          maxKeywords: Number(maxKeywords),
          maxCandidatesPerKeyword: Number(maxCandidatesPerKeyword),
          maxDetailCandidates: Number(maxDetailCandidates),
          maxProducts: Number(maxProducts),
          requestTimeoutSeconds: 15,
          serpUrlLimit: 4,
        }),
      });
      const data = await response.json() as PipelineResponse;
      const step = stepForAction(data, action);
      const stepFailed = !response.ok || data.ok === false || step?.status === 'failed';
      setModules((current) => ({
        ...current,
        [moduleKey]: {
          status: stepFailed ? 'error' : 'completed',
          result: step,
          error: stepFailed ? step?.errorMessage || data.error || `HTTP ${response.status}` : null,
        },
      }));
      if (action === 'patentParse' && !stepFailed) {
        const nextPatentId = Number(step?.patentRecordId || data.patentRecordId || 0);
        setPatentRecordId(nextPatentId > 0 ? nextPatentId : null);
      }
    } catch (error) {
      setModules((current) => ({
        ...current,
        [moduleKey]: { status: 'error', result: null, error: error instanceof Error ? error.message : String(error) },
      }));
    }
  };

  const handlePatentSubmit = (payload: StartPayload) => {
    if (isReadOnlyDiagnostic) return;
    const sessionId = makeSessionId();
    setAnalysisSessionId(sessionId);
    setPatentRecordId(null);
    setPatentInput({
      type: payload.type,
      source: payload.type === 'text' ? '粘贴文本' : payload.type === 'url' ? payload.url : payload.fileName,
      url: payload.url,
      fileName: payload.fileName,
      fileUrl: payload.fileUrl,
      text: payload.type === 'text' ? payload.text : undefined,
      textLength: payload.type === 'text' ? payload.text?.length || 0 : undefined,
    });
    setModules(EMPTY_MODULES);
    void runAction('patentParse', { sessionId, patentId: null, patentPayload: payload });
  };

  const useExample = (value: string) => {
    if (isReadOnlyDiagnostic) return;
    const example = examples.find((item) => String(item.id) === value);
    if (!example) return;
    const sessionId = makeSessionId();
    setAnalysisSessionId(sessionId);
    setPatentRecordId(example.id);
    setPatentInput({ type: 'existing_record', patentRecordId: example.id, patentNumber: example.patentNumber, title: example.title });
    setModules({
      ...EMPTY_MODULES,
      module1: {
        status: 'completed',
        error: null,
        result: {
          status: 'completed',
          patentRecordId: example.id,
          inputType: 'existing_record',
          inputLabel: '选择已解析示例专利',
          metadata: { title: example.title, patent_number: example.patentNumber },
          finalOutput: { metadata: { title: example.title, patent_number: example.patentNumber } },
        },
      },
    });
  };

  const resetAll = () => {
    if (isReadOnlyDiagnostic) return;
    setAnalysisSessionId(makeSessionId());
    setPatentRecordId(null);
    setPatentInput(null);
    setModules(EMPTY_MODULES);
  };

  const moduleInput = (moduleKey: ModuleKey): JsonObject => {
    if (isReadOnlyDiagnostic) {
      return diagnosticInputs[moduleKey] || { analysisSessionId, message: '该阶段未保存输入快照' };
    }
    if (moduleKey === 'module1') return patentInput || { message: '请先在页面顶部输入专利' };
    if (moduleKey === 'module2') return { patentRecordId, analysisSessionId, industry };
    if (moduleKey === 'module3') return {
      patentRecordId,
      analysisSessionId,
      keywordRunId: modules.module2.result?.keywordRunId || null,
      keywords: currentKeywords,
      platforms: platforms.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean),
      maxKeywords: Number(maxKeywords),
      maxCandidatesPerKeyword: Number(maxCandidatesPerKeyword),
      maxDetailCandidates: Number(maxDetailCandidates),
      maxProducts: Number(maxProducts),
    };
    return {
      patentRecordId,
      analysisSessionId,
      productDetailSearchRunId: modules.module3.result?.productDetailSearchRunId || null,
      products: currentProducts.map((product) => ({ id: product.id, name: product.product_name, url: product.final_url || product.product_url })),
    };
  };

  const canRun = (moduleKey: ModuleKey): boolean => {
    if (isReadOnlyDiagnostic) return false;
    if (moduleKey === 'module1') return false;
    if (moduleKey === 'module2') return Boolean(patentRecordId && modules.module1.status === 'completed');
    if (moduleKey === 'module3') return modules.module2.status === 'completed' && currentKeywords.length > 0;
    return modules.module3.status === 'completed' && currentProducts.length > 0;
  };

  return (
    <div className="min-h-screen bg-muted/20">
      <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Link href="/"><Button variant="ghost" size="sm" className="gap-1.5"><ArrowLeft className="h-4 w-4" />Agent</Button></Link>
            <Separator orientation="vertical" className="h-5" />
            <div className="flex min-w-0 items-center gap-2"><Database className="h-4 w-4 text-primary" /><span className="truncate text-sm font-medium">专利分析实验室</span></div>
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">
            {isReadOnlyDiagnostic ? '管理员同任务只读诊断' : '4 个阶段 · 输入、输出、结果分别查看'}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6">
        {isReadOnlyDiagnostic ? (
          <Card className="border-violet-200 shadow-sm" data-patent-analysis-read-only="same-session">
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2 text-base"><ShieldCheck className="h-4 w-4 text-violet-700" />管理员同任务只读诊断</CardTitle>
                  <p className="mt-1 text-sm text-muted-foreground">这里只恢复 Agent 本次分析已经持久化的输入、运行记录、部分输出和错误；刷新页面不会新建测试 session，也不会重跑或改写任何阶段。</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Link href="/test/product-pipeline"><Button variant="outline" size="sm">退出只读诊断</Button></Link>
                  <Button variant="outline" size="sm" disabled={diagnosticLoading} onClick={() => setDiagnosticRefresh((value) => value + 1)}>
                    {diagnosticLoading ? <Loader2 className="mr-1.5 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1.5 h-4 w-4" />}刷新快照
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-wrap items-center gap-2 rounded border bg-violet-50 p-3 text-sm text-violet-950">
                <Badge variant={diagnosticSession?.status === 'error' ? 'destructive' : 'outline'}>{diagnosticSession?.status || (diagnosticLoading ? '读取中' : '未知状态')}</Badge>
                <span className="font-medium">{diagnosticSession?.patentNumber || '专利号未识别'} · {diagnosticSession?.patentTitle || '专利名称未识别'}</span>
                {patentRecordId ? <Badge variant="outline">record {patentRecordId}</Badge> : null}
                <Badge variant="outline" className="font-mono text-[10px]">session {analysisSessionId}</Badge>
              </div>
              {diagnosticSession?.errors?.length ? (
                <Alert variant="destructive">
                  <AlertDescription className="space-y-1 whitespace-pre-wrap">
                    {diagnosticSession.errors.map((error, index) => <p key={`${error}-${index}`}>{error}</p>)}
                  </AlertDescription>
                </Alert>
              ) : null}
              {diagnosticError ? <Alert variant="destructive"><AlertDescription>{diagnosticError}</AlertDescription></Alert> : null}
            </CardContent>
          </Card>
        ) : (
          <Card id="patent-input" className="shadow-sm" data-patent-analysis-input>
            <CardHeader className="pb-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div><CardTitle className="text-base">输入测试专利</CardTitle><p className="mt-1 text-sm text-muted-foreground">上传文件、输入网址或粘贴专利文本。解析成功后，下面四个阶段只使用这一次测试会话的数据。</p></div>
                <Button variant="outline" size="sm" onClick={resetAll}><RefreshCw className="mr-1.5 h-4 w-4" />重置本次测试</Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <UploadForm onSubmit={handlePatentSubmit} isAnalyzing={modules.module1.status === 'running'} />
              <details className="rounded border bg-muted/20 p-3 text-sm">
                <summary className="cursor-pointer font-medium">或者选择一件已解析的示例专利</summary>
                <div className="mt-3 space-y-2">
                  <Select onValueChange={useExample}>
                    <SelectTrigger><SelectValue placeholder="选择示例专利" /></SelectTrigger>
                    <SelectContent>{examples.map((example) => <SelectItem key={example.id} value={String(example.id)}>{example.patentNumber || '无专利号'} · {example.title || '未命名'}</SelectItem>)}</SelectContent>
                  </Select>
                  {examplesError ? <p className="text-xs text-destructive">{examplesError}</p> : null}
                </div>
              </details>
              {patentRecordId ? (
                <div className="flex flex-wrap items-center gap-2 rounded border bg-emerald-50 p-3 text-sm text-emerald-900">
                  <CheckCircle2 className="h-4 w-4" /><span className="font-medium">当前测试专利已就绪</span><Badge variant="outline">record {patentRecordId}</Badge><Badge variant="outline" className="font-mono text-[10px]">session {analysisSessionId}</Badge>
                </div>
              ) : null}
            </CardContent>
          </Card>
        )}

        <section data-patent-analysis-modules>
          <div className="mb-3"><h2 className="text-lg font-semibold">四个分析阶段</h2><p className="text-sm text-muted-foreground">所有阶段默认收起。展开后，可分别查看{isReadOnlyDiagnostic ? '该 session 已保存的' : '本次实际'}输入、后台输出和可读结果。</p></div>
          <Accordion type="multiple" className="space-y-3">
            {MODULES.map((module) => {
              const state = modules[module.key];
              const Icon = module.icon;
              return (
                <AccordionItem key={module.key} value={module.key} className="rounded-lg border bg-background px-4 shadow-sm" data-patent-analysis-module={module.key}>
                  <AccordionTrigger className="gap-3 py-4 text-left hover:no-underline">
                    <span className="flex min-w-0 flex-1 items-center gap-3 pr-2">
                      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"><Icon className="h-4 w-4" /></span>
                      <span className="min-w-0 flex-1"><span className="block font-semibold">{module.title}</span><span className="mt-0.5 block text-xs font-normal text-muted-foreground">{module.description}</span></span>
                      {statusBadge(state.status)}
                    </span>
                  </AccordionTrigger>
                  <AccordionContent className="space-y-4 pb-5">
                    {!isReadOnlyDiagnostic && module.key === 'module2' ? (
                      <div className="max-w-sm space-y-2"><Label>关键词策略类型</Label><Select value={industry} onValueChange={(value) => setIndustry(value as IndustryType)}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="general">通用</SelectItem><SelectItem value="fitness_equipment">健身器材</SelectItem><SelectItem value="home_appliances">家用电器</SelectItem></SelectContent></Select></div>
                    ) : null}
                    {!isReadOnlyDiagnostic && module.key === 'module3' ? (
                      <details className="rounded border bg-muted/20 p-3 text-sm" data-patent-analysis-advanced>
                        <summary className="cursor-pointer font-medium">商品检索参数</summary>
                        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                          <div className="space-y-1"><Label htmlFor="platforms">平台</Label><Input id="platforms" value={platforms} onChange={(event) => setPlatforms(event.target.value)} /></div>
                          <div className="space-y-1"><Label htmlFor="maxKeywords">关键词数</Label><Input id="maxKeywords" value={maxKeywords} onChange={(event) => setMaxKeywords(event.target.value)} /></div>
                          <div className="space-y-1"><Label htmlFor="maxCandidates">候选/词</Label><Input id="maxCandidates" value={maxCandidatesPerKeyword} onChange={(event) => setMaxCandidatesPerKeyword(event.target.value)} /></div>
                          <div className="space-y-1"><Label htmlFor="maxDetails">详情候选</Label><Input id="maxDetails" value={maxDetailCandidates} onChange={(event) => setMaxDetailCandidates(event.target.value)} /></div>
                          <div className="space-y-1"><Label htmlFor="maxProducts">商品数</Label><Input id="maxProducts" value={maxProducts} onChange={(event) => setMaxProducts(event.target.value)} /></div>
                        </div>
                      </details>
                    ) : null}

                    {!isReadOnlyDiagnostic ? (
                      <>
                        {module.key === 'module1' ? (
                          <Button variant="outline" onClick={() => document.getElementById('patent-input')?.scrollIntoView({ behavior: 'smooth' })}>在上方输入并解析专利</Button>
                        ) : (
                          <Button disabled={!canRun(module.key) || state.status === 'running'} onClick={() => void runAction(module.action)}>
                            {state.status === 'running' ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}运行{module.title}
                          </Button>
                        )}
                        {!canRun(module.key) && module.key !== 'module1' ? <p className="text-xs text-muted-foreground">请先完成上一阶段；本页不会自动读取其他测试会话的历史结果。</p> : null}
                      </>
                    ) : (
                      <p className="text-xs text-violet-700" data-patent-analysis-no-rerun>同任务只读模式：本阶段只展示持久化快照，不提供运行或补跑操作。</p>
                    )}
                    {state.error ? <Alert variant="destructive"><AlertDescription className="whitespace-pre-wrap">{state.error}</AlertDescription></Alert> : null}

                    <Accordion type="multiple" className="rounded-lg border px-4" data-patent-analysis-io>
                      <AccordionItem value={`${module.key}-input`} data-patent-analysis-module-input>
                        <AccordionTrigger className="text-left">
                          <span><span className="font-medium">本次输入</span><span className="mt-0.5 block text-xs font-normal text-muted-foreground">查看本阶段实际读取的数据与参数</span></span>
                        </AccordionTrigger>
                        <AccordionContent><InputDetails value={moduleInput(module.key)} /></AccordionContent>
                      </AccordionItem>
                      <AccordionItem value={`${module.key}-output`} data-patent-analysis-module-output>
                        <AccordionTrigger className="text-left">
                          <span><span className="font-medium">本次输出</span><span className="mt-0.5 block text-xs font-normal text-muted-foreground">查看运行标识、计数、错误与完整原始返回</span></span>
                        </AccordionTrigger>
                        <AccordionContent><StageOutput moduleKey={module.key} result={state.result} /></AccordionContent>
                      </AccordionItem>
                      <AccordionItem value={`${module.key}-result`} data-patent-analysis-module-result>
                        <AccordionTrigger className="text-left">
                          <span><span className="font-medium">结果</span><span className="mt-0.5 block text-xs font-normal text-muted-foreground">查看律师或产品人员可直接阅读的阶段结果</span></span>
                        </AccordionTrigger>
                        <AccordionContent><StageResult moduleKey={module.key} result={state.result} /></AccordionContent>
                      </AccordionItem>
                    </Accordion>
                  </AccordionContent>
                </AccordionItem>
              );
            })}
          </Accordion>
        </section>
      </main>
    </div>
  );
}

export default function ProductPipelineTestPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-muted/20 p-6 text-sm text-muted-foreground">正在读取专利分析页面…</div>}>
      <ProductPipelineTestContent />
    </Suspense>
  );
}
