'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ArrowLeft,
  CheckCircle2,
  Database,
  ExternalLink,
  ImageOff,
  Loader2,
  Play,
  RefreshCw,
  Search,
} from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
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
import { Textarea } from '@/components/ui/textarea';
import type { IndustryType } from '@/lib/types';

type PipelineAction = 'keywords' | 'productSearch' | 'claimCompare' | 'all';
type RunStatus = 'idle' | 'running' | 'completed' | 'error';

type KeywordRow = {
  id?: number;
  keyword_text?: string;
  keyword_type?: string;
  source_location?: string;
  confidence_score?: number;
};

type ProductRow = {
  id?: number;
  run_id?: number;
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
  quality_flags?: Record<string, unknown>;
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

type StepPayload = Record<string, unknown> & {
  elapsedMs?: number;
  keywordRunId?: number;
  keywords?: KeywordRow[];
  keywordsCount?: number;
  productDetailSearchRunId?: number;
  acceptedProductsCount?: number;
  products?: ProductRow[];
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
  keywordStep?: StepPayload;
  productSearchStep?: StepPayload;
  claimCompareStep?: StepPayload;
  keywords?: KeywordRow[];
};

type PersistedState = {
  patentRecordId: string;
  analysisSessionId: string;
  industry: IndustryType;
  manualKeywords: string;
  platforms: string;
  maxKeywords: string;
  maxCandidatesPerKeyword: string;
  maxDetailCandidates: string;
  maxProducts: string;
  status: RunStatus;
  lastAction: PipelineAction;
  result: PipelineResponse | null;
  error: string | null;
  elapsedMs: number | null;
};

const STORAGE_KEY = 'product-pipeline-test-state';

const ACTION_LABELS: Record<PipelineAction, string> = {
  keywords: '生成关键词',
  productSearch: '测试新模块三',
  claimCompare: '测试模块四',
  all: '全链路运行',
};

function makeDefaultSessionId(): string {
  return `module_test_${Date.now()}`;
}

function splitList(value: string): string[] {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatMs(ms?: number | null): string {
  if (!ms && ms !== 0) return '-';
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value);
}

function imageUrls(product: ProductRow): string[] {
  return Array.isArray(product.picture) ? product.picture.filter((url): url is string => typeof url === 'string') : [];
}

function ProductImages({ product }: { product: ProductRow }) {
  const urls = imageUrls(product);
  if (urls.length === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <ImageOff className="h-3.5 w-3.5" />
        无图片
      </span>
    );
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {urls.slice(0, 4).map((url, index) => (
        <a key={`${url}-${index}`} href={url} target="_blank" rel="noopener noreferrer" className="block">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={`商品图 ${index + 1}`}
            className="h-12 w-12 rounded-md border bg-muted object-cover"
          />
        </a>
      ))}
      {urls.length > 4 && (
        <Badge variant="secondary" className="h-12 rounded-md px-2">
          +{urls.length - 4}
        </Badge>
      )}
    </div>
  );
}

function StepCard({
  title,
  status,
  value,
  elapsedMs,
}: {
  title: string;
  status: RunStatus;
  value: string;
  elapsedMs?: number;
}) {
  const isCompleted = status === 'completed';
  const isRunning = status === 'running';
  return (
    <Card className="shadow-sm">
      <CardContent className="flex items-center justify-between gap-4 p-4">
        <div className="min-w-0">
          <div className="text-xs text-muted-foreground">{title}</div>
          <div className="mt-1 truncate text-sm font-medium">{value}</div>
          {elapsedMs !== undefined && (
            <div className="mt-1 text-xs text-muted-foreground">{formatMs(elapsedMs)}</div>
          )}
        </div>
        {isRunning ? (
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
        ) : isCompleted ? (
          <CheckCircle2 className="h-5 w-5 text-emerald-600" />
        ) : (
          <Search className="h-5 w-5 text-muted-foreground" />
        )}
      </CardContent>
    </Card>
  );
}

export default function ProductPipelineTestPage() {
  const [patentRecordId, setPatentRecordId] = useState('');
  const [analysisSessionId, setAnalysisSessionId] = useState(makeDefaultSessionId);
  const [industry, setIndustry] = useState<IndustryType>('general');
  const [manualKeywords, setManualKeywords] = useState('');
  const [platforms, setPlatforms] = useState('jd,1688');
  const [maxKeywords, setMaxKeywords] = useState('3');
  const [maxCandidatesPerKeyword, setMaxCandidatesPerKeyword] = useState('8');
  const [maxDetailCandidates, setMaxDetailCandidates] = useState('30');
  const [maxProducts, setMaxProducts] = useState('3');
  const [status, setStatus] = useState<RunStatus>('idle');
  const [lastAction, setLastAction] = useState<PipelineAction>('all');
  const [result, setResult] = useState<PipelineResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);

  useEffect(() => {
    try {
      const raw = window.sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw) as Partial<PersistedState>;
      setPatentRecordId(saved.patentRecordId || '');
      setAnalysisSessionId(saved.analysisSessionId || makeDefaultSessionId());
      setIndustry(saved.industry || 'general');
      setManualKeywords(saved.manualKeywords || '');
      setPlatforms(saved.platforms || 'jd,1688');
      setMaxKeywords(saved.maxKeywords || '3');
      setMaxCandidatesPerKeyword(saved.maxCandidatesPerKeyword || '8');
      setMaxDetailCandidates(saved.maxDetailCandidates || '30');
      setMaxProducts(saved.maxProducts || '3');
      setStatus(saved.status === 'running' ? 'error' : (saved.status || 'idle'));
      setLastAction(saved.lastAction || 'all');
      setResult(saved.result || null);
      setError(saved.status === 'running' ? '页面刷新导致上一次请求中断，请重新运行。' : (saved.error || null));
      setElapsedMs(saved.elapsedMs ?? null);
    } catch {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  useEffect(() => {
    if (status === 'running') return;
    const payload: PersistedState = {
      patentRecordId,
      analysisSessionId,
      industry,
      manualKeywords,
      platforms,
      maxKeywords,
      maxCandidatesPerKeyword,
      maxDetailCandidates,
      maxProducts,
      status,
      lastAction,
      result,
      error,
      elapsedMs,
    };
    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {}
  }, [
    patentRecordId,
    analysisSessionId,
    industry,
    manualKeywords,
    platforms,
    maxKeywords,
    maxCandidatesPerKeyword,
    maxDetailCandidates,
    maxProducts,
    status,
    lastAction,
    result,
    error,
    elapsedMs,
  ]);

  const keywords = useMemo(() => {
    const fromStep = result?.keywordStep?.keywords;
    if (Array.isArray(fromStep)) return fromStep;
    if (Array.isArray(result?.keywords)) return result.keywords;
    return [];
  }, [result]);

  const products = useMemo(() => {
    const fromStep = result?.productSearchStep?.products;
    return Array.isArray(fromStep) ? fromStep : [];
  }, [result]);

  const compareRows = useMemo(() => {
    const rows = result?.claimCompareStep?.rows;
    return Array.isArray(rows) ? rows : [];
  }, [result]);

  const runPipeline = async (action: PipelineAction) => {
    const patentId = Number(patentRecordId);
    if (!Number.isInteger(patentId) || patentId <= 0) {
      setError('请输入有效的 patent_record_id');
      setStatus('error');
      return;
    }

    setStatus('running');
    setLastAction(action);
    setError(null);
    setResult(null);
    setElapsedMs(null);
    const startedAt = Date.now();

    try {
      const response = await fetch('/api/test/product-pipeline', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          patentRecordId: patentId,
          analysisSessionId: analysisSessionId.trim() || makeDefaultSessionId(),
          industry,
          manualKeywords: splitList(manualKeywords),
          platforms: splitList(platforms),
          maxKeywords: Number(maxKeywords),
          maxCandidatesPerKeyword: Number(maxCandidatesPerKeyword),
          maxDetailCandidates: Number(maxDetailCandidates),
          maxProducts: Number(maxProducts),
          requestTimeoutSeconds: 15,
          serpUrlLimit: 4,
        }),
      });
      const data = (await response.json()) as PipelineResponse;
      setElapsedMs(Date.now() - startedAt);
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      setResult(data);
      setStatus('completed');
    } catch (runError) {
      setElapsedMs(Date.now() - startedAt);
      setError(runError instanceof Error ? runError.message : String(runError));
      setStatus('error');
    }
  };

  const resetSession = () => {
    setAnalysisSessionId(makeDefaultSessionId());
    setStatus('idle');
    setResult(null);
    setError(null);
    setElapsedMs(null);
  };

  return (
    <div className="min-h-screen bg-muted/20">
      <header className="sticky top-0 z-40 border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <Link href="/">
              <Button variant="ghost" size="sm" className="gap-1.5">
                <ArrowLeft className="h-4 w-4" />
                首页
              </Button>
            </Link>
            <Separator orientation="vertical" className="h-5" />
            <div className="flex min-w-0 items-center gap-2">
              <Database className="h-4 w-4 text-primary" />
              <span className="truncate text-sm font-medium">关键词 / 新模块三 / 模块四测试</span>
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            <Link href="/test/module1">
              <Button variant="outline" size="sm">模块1测试</Button>
            </Link>
            <Link href="/test">
              <Button variant="outline" size="sm">旧模块3测试</Button>
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
        {error && (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        <div className="grid gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
          <Card className="shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-base">测试参数</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="patentRecordId">patent_record_id</Label>
                <Input
                  id="patentRecordId"
                  inputMode="numeric"
                  value={patentRecordId}
                  onChange={(event) => setPatentRecordId(event.target.value)}
                  placeholder="例如 101"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="analysisSessionId">analysis_session_id</Label>
                <div className="flex gap-2">
                  <Input
                    id="analysisSessionId"
                    value={analysisSessionId}
                    onChange={(event) => setAnalysisSessionId(event.target.value)}
                    className="font-mono text-xs"
                  />
                  <Button type="button" variant="outline" size="icon" onClick={resetSession} title="生成新测试 session">
                    <RefreshCw className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <div className="space-y-2">
                <Label>模块2行业</Label>
                <Select value={industry} onValueChange={(value) => setIndustry(value as IndustryType)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="general">通用</SelectItem>
                    <SelectItem value="fitness_equipment">健身器材</SelectItem>
                    <SelectItem value="home_appliances">家用电器</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="manualKeywords">手动关键词，可选</Label>
                <Textarea
                  id="manualKeywords"
                  value={manualKeywords}
                  onChange={(event) => setManualKeywords(event.target.value)}
                  placeholder="留空时，新模块三读取 keyword_records；填写后以这些关键词测试新模块三。"
                  rows={4}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-2">
                  <Label htmlFor="platforms">平台</Label>
                  <Input id="platforms" value={platforms} onChange={(event) => setPlatforms(event.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="maxProducts">商品数</Label>
                  <Input id="maxProducts" value={maxProducts} onChange={(event) => setMaxProducts(event.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="maxKeywords">关键词数</Label>
                  <Input id="maxKeywords" value={maxKeywords} onChange={(event) => setMaxKeywords(event.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="maxCandidates">候选/词</Label>
                  <Input
                    id="maxCandidates"
                    value={maxCandidatesPerKeyword}
                    onChange={(event) => setMaxCandidatesPerKeyword(event.target.value)}
                  />
                </div>
                <div className="col-span-2 space-y-2">
                  <Label htmlFor="maxDetailCandidates">详情候选上限</Label>
                  <Input
                    id="maxDetailCandidates"
                    value={maxDetailCandidates}
                    onChange={(event) => setMaxDetailCandidates(event.target.value)}
                  />
                </div>
              </div>
              <div className="grid gap-2">
                <Button disabled={status === 'running'} onClick={() => void runPipeline('all')} className="gap-1.5">
                  {status === 'running' && lastAction === 'all' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                  全链路运行
                </Button>
                <div className="grid grid-cols-3 gap-2">
                  <Button disabled={status === 'running'} variant="outline" onClick={() => void runPipeline('keywords')}>
                    关键词
                  </Button>
                  <Button disabled={status === 'running'} variant="outline" onClick={() => void runPipeline('productSearch')}>
                    模块三
                  </Button>
                  <Button disabled={status === 'running'} variant="outline" onClick={() => void runPipeline('claimCompare')}>
                    模块四
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-4">
              <StepCard title="当前动作" status={status} value={ACTION_LABELS[lastAction]} elapsedMs={elapsedMs || undefined} />
              <StepCard title="关键词" status={result?.keywordStep ? 'completed' : status === 'running' && lastAction !== 'claimCompare' ? 'running' : 'idle'} value={`${keywords.length} 个`} elapsedMs={result?.keywordStep?.elapsedMs} />
              <StepCard title="新模块三" status={result?.productSearchStep ? 'completed' : status === 'running' && ['productSearch', 'all'].includes(lastAction) ? 'running' : 'idle'} value={`${products.length} 个商品`} elapsedMs={result?.productSearchStep?.elapsedMs} />
              <StepCard title="模块四" status={result?.claimCompareStep ? 'completed' : status === 'running' && ['claimCompare', 'all'].includes(lastAction) ? 'running' : 'idle'} value={`${result?.claimCompareStep?.featureCount || 0} 条结果`} elapsedMs={result?.claimCompareStep?.elapsedMs} />
            </div>

            <Card className="shadow-sm">
              <CardHeader className="pb-3">
                <CardTitle className="text-base">运行标识</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-wrap gap-2 text-xs">
                <Badge variant="secondary">session: {result?.analysisSessionId || analysisSessionId}</Badge>
                {result?.keywordStep?.keywordRunId && <Badge variant="outline">keyword_run_id: {result.keywordStep.keywordRunId}</Badge>}
                {result?.productSearchStep?.productDetailSearchRunId && (
                  <Badge variant="outline">product_detail_search_run_id: {result.productSearchStep.productDetailSearchRunId}</Badge>
                )}
                {result?.claimCompareStep?.claimCompareRunId && (
                  <Badge variant="outline">claim_compare_run_id: {result.claimCompareStep.claimCompareRunId}</Badge>
                )}
              </CardContent>
            </Card>

            {result?.claimCompareStep?.resultSummary && (
              <Card className="shadow-sm">
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">模块四摘要</CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="whitespace-pre-wrap text-sm leading-6">{result.claimCompareStep.resultSummary}</p>
                </CardContent>
              </Card>
            )}
          </div>
        </div>

        <Card className="shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">关键词</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[44px]">#</TableHead>
                    <TableHead>关键词</TableHead>
                    <TableHead className="w-[150px]">类型</TableHead>
                    <TableHead className="w-[110px]">置信度</TableHead>
                    <TableHead>来源</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {keywords.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="h-24 text-center text-sm text-muted-foreground">
                        暂无关键词
                      </TableCell>
                    </TableRow>
                  ) : keywords.map((keyword, index) => (
                    <TableRow key={`${keyword.id || index}-${keyword.keyword_text}`}>
                      <TableCell>{index + 1}</TableCell>
                      <TableCell className="font-medium">{keyword.keyword_text}</TableCell>
                      <TableCell><Badge variant="outline">{keyword.keyword_type || '-'}</Badge></TableCell>
                      <TableCell>{keyword.confidence_score ?? '-'}</TableCell>
                      <TableCell className="max-w-[340px] truncate text-muted-foreground">{keyword.source_location || '-'}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        <Card className="shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">新模块三商品</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[56px]">图</TableHead>
                    <TableHead>商品</TableHead>
                    <TableHead className="w-[110px]">平台</TableHead>
                    <TableHead className="w-[90px]">质量</TableHead>
                    <TableHead className="w-[120px]">图片数</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {products.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="h-24 text-center text-sm text-muted-foreground">
                        暂无商品
                      </TableCell>
                    </TableRow>
                  ) : products.map((product, index) => {
                    const url = product.final_url || product.product_url || '';
                    const historical = product.quality_flags?.historical_fallback === true || product.quality_flags?.historical_fallback === 'true';
                    return (
                      <TableRow key={`${product.id || index}-${url}`}>
                        <TableCell><ProductImages product={product} /></TableCell>
                        <TableCell className="min-w-[360px]">
                          <div className="flex items-start gap-2">
                            <div className="min-w-0">
                              <div className="font-medium leading-5">{product.product_name || '-'}</div>
                              <div className="mt-1 line-clamp-2 text-xs text-muted-foreground">{text(product.description)}</div>
                              {url && (
                                <a href={url} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-primary">
                                  打开商品页
                                  <ExternalLink className="h-3 w-3" />
                                </a>
                              )}
                            </div>
                            {historical && <Badge variant="secondary">历史兜底</Badge>}
                          </div>
                        </TableCell>
                        <TableCell>{product.platform || '-'}</TableCell>
                        <TableCell>{product.quality_score ?? '-'}</TableCell>
                        <TableCell>{imageUrls(product).length}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        <Card className="shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">模块四比对结果预览</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>商品</TableHead>
                    <TableHead className="w-[90px]">权利要求</TableHead>
                    <TableHead className="w-[90px]">特征</TableHead>
                    <TableHead className="w-[110px]">结果</TableHead>
                    <TableHead className="w-[90px]">分数</TableHead>
                    <TableHead>理由/证据</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {compareRows.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="h-24 text-center text-sm text-muted-foreground">
                        暂无比对结果
                      </TableCell>
                    </TableRow>
                  ) : compareRows.slice(0, 80).map((row, index) => (
                    <TableRow key={`${row.product_name}-${row.feature_id}-${index}`}>
                      <TableCell className="max-w-[260px] truncate">{row.product_name || '-'}</TableCell>
                      <TableCell>{row.claim_id || '-'}</TableCell>
                      <TableCell>{row.feature_id || '-'}</TableCell>
                      <TableCell><Badge variant="outline">{row.comparison_result || '-'}</Badge></TableCell>
                      <TableCell>{row.similarity_score ?? row.feature_awarded_score ?? '-'}</TableCell>
                      <TableCell className="max-w-[520px]">
                        <div className="line-clamp-2 text-xs leading-5 text-muted-foreground">
                          {row.reason || row.evidence || '-'}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
