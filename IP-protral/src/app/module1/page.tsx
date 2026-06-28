'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import {
  AlertCircle,
  ArrowLeft,
  Database,
  FileText,
  ImageIcon,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';

interface DatabaseSnapshotResponse {
  session: {
    id: string;
    status: string;
    patentTitle?: string | null;
    patentNumber?: string | null;
    results?: Record<string, unknown> | null;
  };
  snapshot: {
    ids: Record<string, number | null>;
    counts: Record<string, number>;
    tables: Record<string, unknown>;
  };
  error?: string;
}

type JsonRecord = Record<string, unknown>;

export default function Module1Page() {
  return (
    <Suspense fallback={null}>
      <Module1Content />
    </Suspense>
  );
}

function Module1Content() {
  const searchParams = useSearchParams();
  const sessionId = searchParams.get('session') || '';
  const [data, setData] = useState<DatabaseSnapshotResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (!sessionId) {
      setError('缺少 session 参数');
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const response = await fetch(`/api/database/${encodeURIComponent(sessionId)}`, { cache: 'no-store' });
      const payload = (await response.json()) as DatabaseSnapshotResponse;
      if (!response.ok) {
        throw new Error(payload.error || '读取模块1解析结果失败');
      }
      setData(payload);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '读取模块1解析结果失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [sessionId]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-muted/20">
        <div className="text-center space-y-3">
          <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto" />
          <p className="text-sm text-muted-foreground">读取模块1解析结果...</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-muted/20 px-6">
        <div className="max-w-xl w-full space-y-4">
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error || '未读取到模块1解析结果'}</AlertDescription>
          </Alert>
          <div className="flex gap-3">
            <Link href={sessionId ? `/results?session=${encodeURIComponent(sessionId)}` : '/'}>
              <Button variant="outline">返回结果页</Button>
            </Link>
            <Button onClick={() => void load()}>重试</Button>
          </div>
        </div>
      </div>
    );
  }

  const tables = data.snapshot.tables || {};
  const patentRecord = (tables.patentParseRecord || null) as JsonRecord | null;
  const claims = Array.isArray(tables.patentClaims) ? (tables.patentClaims as JsonRecord[]) : [];
  const figures = Array.isArray(tables.patentFigures) ? (tables.patentFigures as JsonRecord[]) : [];
  const patentSnapshot = ((data.session.results || {})['patent'] || {}) as JsonRecord;
  const specification = patentRecord?.specification && typeof patentRecord.specification === 'object'
    ? patentRecord.specification as Record<string, unknown>
    : {};
  const abstractText = getPatentAbstract(patentSnapshot, specification);

  const summary = useMemo(() => {
    const independentCount = claims.filter((claim) => claim.claim_type === 'INDEPENDENT').length;
    const dependentCount = claims.filter((claim) => claim.claim_type === 'DEPENDENT').length;
    return { independentCount, dependentCount };
  }, [claims]);

  return (
    <div className="min-h-screen bg-muted/20">
      <header className="border-b bg-background/90 backdrop-blur sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <Link href={sessionId ? `/results?session=${encodeURIComponent(sessionId)}` : '/'}>
              <Button variant="ghost" size="sm" className="gap-1.5">
                <ArrowLeft className="h-4 w-4" />
                返回结果
              </Button>
            </Link>
            <Separator orientation="vertical" className="h-5" />
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-primary" />
              <span className="text-sm font-medium">模块1解析结果</span>
            </div>
            <Badge variant="outline" className="hidden font-mono text-[11px] sm:inline-flex">
              {sessionId}
            </Badge>
          </div>
          <div className="flex shrink-0 gap-2">
            <Link href={sessionId ? `/database?session=${encodeURIComponent(sessionId)}` : '/database'}>
              <Button variant="outline" size="sm" className="gap-1.5">
                <Database className="h-4 w-4" />
                数据库详情
              </Button>
            </Link>
            <Button variant="outline" size="sm" onClick={() => void load()} className="gap-1.5">
              <RefreshCw className="h-4 w-4" />
              刷新
            </Button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        <Card className="shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">解析概要</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary">patent_record_id: {data.snapshot.ids.patentRecordId ?? 'null'}</Badge>
              <Badge variant="outline">权利要求: {claims.length}</Badge>
              <Badge variant="outline">独立: {summary.independentCount}</Badge>
              <Badge variant="outline">从属: {summary.dependentCount}</Badge>
              <Badge variant="outline">附图: {figures.length}</Badge>
            </div>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="专利号" value={toText(patentSnapshot.patentNumber) || toText(patentRecord?.patent_number)} />
                <Field label="专利名称" value={toText(patentSnapshot.title) || toText(patentRecord?.title)} />
                <Field label="专利权人" value={toText(patentRecord?.patent_holder)} />
                <Field label="申请日" value={toText(patentRecord?.application_date)} />
              </div>

              <div className="rounded-lg border bg-background p-3">
                <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
                  <ImageIcon className="h-3.5 w-3.5" />
                  摘要附图 / 第一附图
                </div>
                {toText(figures[0]?.figure_url) ? (
                  <a href={toText(figures[0]?.figure_url)} target="_blank" rel="noopener noreferrer" className="block overflow-hidden rounded-md border bg-muted">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={toText(figures[0]?.figure_url)} alt="模块1第一附图" className="h-44 w-full object-contain" />
                  </a>
                ) : (
                  <div className="flex h-44 items-center justify-center rounded-md border bg-muted/30 text-xs text-muted-foreground">
                    未提取到附图
                  </div>
                )}
                {toText(figures[0]?.figure_description) && (
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">{toText(figures[0]?.figure_description)}</p>
                )}
              </div>
            </div>

            <div>
              <div className="mb-1 text-xs text-muted-foreground">专利摘要</div>
              <div className="rounded-lg border bg-background p-4 text-sm leading-6 whitespace-pre-wrap">
                {abstractText || '未提取到摘要。通常原因是输入文本/解析后的说明书章节中没有“摘要”章节，或首页(57)摘要未被文本提取出来。'}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">说明书章节</CardTitle>
          </CardHeader>
          <CardContent>
            {Object.keys(specification).length > 0 ? (
              <div className="grid gap-3 md:grid-cols-2">
                {Object.entries(specification).map(([section, content]) => (
                  <div key={section} className="rounded-lg border bg-background p-4">
                    <div className="mb-2 text-sm font-semibold">{section}</div>
                    <p className="max-h-40 overflow-auto text-xs leading-5 text-muted-foreground whitespace-pre-wrap">
                      {String(content || '')}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">未提取到说明书章节。</p>
            )}
          </CardContent>
        </Card>

        <Card className="shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">权利要求</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {claims.length > 0 ? claims.map((claim) => (
              <div key={String(claim.id || claim.claim_id)} className="rounded-lg border bg-background p-4">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <Badge variant={claim.claim_type === 'INDEPENDENT' ? 'default' : 'outline'}>
                    权利要求 {toText(claim.claim_id)}
                  </Badge>
                  <span className="text-xs text-muted-foreground">{toText(claim.claim_type)}</span>
                </div>
                <p className="text-sm leading-6 whitespace-pre-wrap">{toText(claim.claim_text)}</p>
              </div>
            )) : (
              <p className="text-sm text-muted-foreground">未提取到权利要求。</p>
            )}
          </CardContent>
        </Card>

        <Card className="shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">附图列表</CardTitle>
          </CardHeader>
          <CardContent>
            {figures.length > 0 ? (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {figures.map((figure, index) => (
                  <div key={`${toText(figure.figure_id) || 'figure'}-${index}`} className="rounded-lg border bg-background p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{toText(figure.figure_id) || `图${index + 1}`}</span>
                      <Badge variant="outline" className="text-[11px]">#{index + 1}</Badge>
                    </div>
                    {toText(figure.figure_url) && (
                      <a href={toText(figure.figure_url)} target="_blank" rel="noopener noreferrer" className="block overflow-hidden rounded-md border bg-muted">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={toText(figure.figure_url)} alt={toText(figure.figure_id) || `附图 ${index + 1}`} className="h-44 w-full object-contain" />
                      </a>
                    )}
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">
                      {toText(figure.figure_description) || '暂无附图说明'}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">未提取到附图。</p>
            )}
          </CardContent>
        </Card>

        {Array.isArray(patentRecord?.parse_errors) && patentRecord.parse_errors.length > 0 && (
          <Card className="shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="text-base">解析错误</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="max-h-64 overflow-auto rounded-md bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap break-all">
                {JSON.stringify(patentRecord.parse_errors, null, 2)}
              </pre>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  );
}

function Field({ label, value }: { label: string; value?: string }) {
  return (
    <div className="rounded-lg border bg-background p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-sm font-medium leading-6">{value || '未提取'}</div>
    </div>
  );
}

function toText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function getPatentAbstract(patentSnapshot: JsonRecord, specification: Record<string, unknown>): string {
  const explicit = toText(patentSnapshot.abstract);
  if (explicit) return explicit;
  for (const [section, content] of Object.entries(specification)) {
    if (!section.includes('摘要')) continue;
    const text = toText(content);
    if (text) return text;
  }
  return '';
}
