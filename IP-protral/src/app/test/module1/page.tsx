'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  ArrowLeft,
  Database,
  ExternalLink,
  FileJson,
  FileText,
  Image as ImageIcon,
  Loader2,
  Play,
} from 'lucide-react';
import { UploadForm } from '@/components/upload-form';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';

type StartPayload = {
  type: 'url' | 'file' | 'text';
  url?: string;
  fileKey?: string;
  fileName?: string;
  fileUrl?: string;
  text?: string;
};

type Module1Claim = {
  claim_id?: string;
  claim_type?: 'INDEPENDENT' | 'DEPENDENT';
  claim_text?: string;
  parent_claim_id?: string | null;
};

type Module1Figure = {
  figure_id?: string;
  figure_url?: string;
  figure_description?: string;
  storage_key?: string | null;
};

type Module1Metadata = {
  title?: string;
  patent_number?: string;
  application_date?: string;
  priority_date?: string;
  patent_holder?: string;
};

type Module1Error = {
  error_type?: string;
  error_message?: string;
  is_recoverable?: boolean;
};

type Module1TestResponse = {
  ok: boolean;
  inputType: 'url' | 'file' | 'text';
  patentFileUrl: string;
  taskId: string;
  runId: string;
  dbRecordId: number | null;
  feishuUrl: string | null;
  feishuAppToken: string | null;
  rawText: string;
  rawTextLength: number;
  fileFormat: string | null;
  fileReadError: Module1Error | null;
  claimsCount: number;
  figuresCount: number;
  finalOutput: {
    claims?: Module1Claim[];
    figures?: Module1Figure[];
    specification?: Record<string, string>;
    metadata?: Module1Metadata;
    errors?: Module1Error[];
  } | null;
};

const STORAGE_KEY = 'module1-test-page-state';

type PersistedState = {
  status: 'idle' | 'running' | 'completed' | 'error';
  result: Module1TestResponse | null;
  error: string | null;
  elapsedMs: number | null;
  savedAt: number;
};

export default function Module1TestPage() {
  const [status, setStatus] = useState<'idle' | 'running' | 'completed' | 'error'>('idle');
  const [result, setResult] = useState<Module1TestResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [restoredAt, setRestoredAt] = useState<number | null>(null);

  useEffect(() => {
    try {
      const raw = window.sessionStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as PersistedState;
      const restoredStatus = parsed.status === 'running' ? 'error' : (parsed.status || 'idle');
      const restoredError = parsed.status === 'running'
        ? '页面在分析过程中被刷新，原请求已中断。请重新点击“开始分析”。'
        : (parsed.error || null);
      setStatus(restoredStatus);
      setResult(parsed.result || null);
      setError(restoredError);
      setElapsedMs(parsed.elapsedMs ?? null);
      setRestoredAt(parsed.savedAt || Date.now());
    } catch {
      window.sessionStorage.removeItem(STORAGE_KEY);
    }
  }, []);

  useEffect(() => {
    if (status === 'running') {
      return;
    }

    if (status === 'idle' && !result && !error && elapsedMs == null) {
      return;
    }

    const payload: PersistedState = {
      status,
      result,
      error,
      elapsedMs,
      savedAt: Date.now(),
    };

    try {
      window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // Ignore storage quota or serialization failures in dev mode.
    }
  }, [status, result, error, elapsedMs]);

  const handleSubmit = async (payload: StartPayload) => {
    setStatus('running');
    setResult(null);
    setError(null);
    setElapsedMs(null);
    setRestoredAt(null);
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch {}

    const start = Date.now();
    try {
      const response = await fetch('/api/test/module1', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = (await response.json()) as Module1TestResponse | { error?: string };
      setElapsedMs(Date.now() - start);

      if (!response.ok) {
        throw new Error(('error' in data && data.error) || `HTTP ${response.status}`);
      }

      setResult(data as Module1TestResponse);
      setStatus('completed');
    } catch (err) {
      setElapsedMs(Date.now() - start);
      setError(err instanceof Error ? err.message : String(err));
      setStatus('error');
    }
  };

  const figures = useMemo(
    () => (Array.isArray(result?.finalOutput?.figures) ? result?.finalOutput?.figures : []),
    [result],
  );
  const claims = useMemo(
    () => (Array.isArray(result?.finalOutput?.claims) ? result?.finalOutput?.claims : []),
    [result],
  );
  const specificationEntries = useMemo(
    () => Object.entries(result?.finalOutput?.specification || {}),
    [result],
  );

  const clearSavedResult = () => {
    setStatus('idle');
    setResult(null);
    setError(null);
    setElapsedMs(null);
    setRestoredAt(null);
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch {}
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-background/90 backdrop-blur sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/">
              <Button variant="ghost" size="sm" className="gap-1.5">
                <ArrowLeft className="h-4 w-4" />
                首页
              </Button>
            </Link>
            <Link href="/test">
              <Button variant="ghost" size="sm">
                模块3测试
              </Button>
            </Link>
          </div>
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-primary" />
            <span className="text-sm font-medium">模块1 单独测试</span>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-6">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">输入专利 PDF / URL / 文本</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              这个页面只测试模块1，重点查看两类输出：`提取文本` 和 `附图列表`。
              推荐直接上传原始专利 PDF，这样最容易验证图片提取效果。
            </p>
            {restoredAt && (
              <div className="flex items-center justify-between rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
                <span>已恢复上一次测试结果，页面刷新后结果不会丢失。</span>
                <Button variant="ghost" size="sm" onClick={clearSavedResult}>
                  清空结果
                </Button>
              </div>
            )}
            <UploadForm onSubmit={(data) => void handleSubmit(data)} isAnalyzing={status === 'running'} />
          </CardContent>
        </Card>

        {status === 'running' && (
          <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            正在调用模块1并读取提取文本...
          </div>
        )}

        {error && (
          <Alert variant="destructive">
            <AlertDescription className="whitespace-pre-wrap break-all">{error}</AlertDescription>
          </Alert>
        )}

        {result && (
          <>
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">执行摘要</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex flex-wrap gap-2">
                  <Badge variant="secondary">input: {result.inputType}</Badge>
                  <Badge variant="outline">file_format: {result.fileFormat || '-'}</Badge>
                  <Badge variant="outline">文本长度: {result.rawTextLength}</Badge>
                  <Badge variant="outline">权利要求: {result.claimsCount}</Badge>
                  <Badge variant="outline">附图: {result.figuresCount}</Badge>
                  {result.dbRecordId != null && <Badge variant="outline">db_record_id: {result.dbRecordId}</Badge>}
                  {elapsedMs != null && <Badge variant="outline">耗时: {(elapsedMs / 1000).toFixed(1)}s</Badge>}
                </div>

                <div className="grid gap-2 text-sm sm:grid-cols-2">
                  <div className="rounded-md border p-3">
                    <p className="text-muted-foreground">task_id</p>
                    <p className="font-mono break-all">{result.taskId}</p>
                  </div>
                  <div className="rounded-md border p-3">
                    <p className="text-muted-foreground">run_id</p>
                    <p className="font-mono break-all">{result.runId || '-'}</p>
                  </div>
                  <div className="rounded-md border p-3 sm:col-span-2">
                    <p className="text-muted-foreground">输入文件路径</p>
                    <p className="font-mono break-all text-xs">{result.patentFileUrl}</p>
                  </div>
                </div>

                {result.finalOutput?.metadata && (
                  <div className="grid gap-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
                    <MetadataItem label="标题" value={result.finalOutput.metadata.title} />
                    <MetadataItem label="专利号" value={result.finalOutput.metadata.patent_number} />
                    <MetadataItem label="申请日" value={result.finalOutput.metadata.application_date} />
                    <MetadataItem label="优先权日" value={result.finalOutput.metadata.priority_date} />
                    <MetadataItem label="专利权人" value={result.finalOutput.metadata.patent_holder} />
                    <MetadataItem label="飞书结果" value={result.feishuUrl || '-'} href={result.feishuUrl || undefined} />
                  </div>
                )}
              </CardContent>
            </Card>

            {result.fileReadError?.error_message && (
              <Alert variant="destructive">
                <AlertDescription>{result.fileReadError.error_message}</AlertDescription>
              </Alert>
            )}

            {Array.isArray(result.finalOutput?.errors) && result.finalOutput.errors.length > 0 && (
              <Card>
                <CardHeader className="pb-3">
                  <CardTitle className="text-base">模块错误信息</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {result.finalOutput.errors.map((item, index) => (
                    <Alert key={`${item.error_type || 'error'}-${index}`} variant="destructive">
                      <AlertDescription>
                        <span className="font-medium">{item.error_type || 'UNKNOWN_ERROR'}：</span>
                        {item.error_message || '-'}
                      </AlertDescription>
                    </Alert>
                  ))}
                </CardContent>
              </Card>
            )}

            <Tabs defaultValue="raw-text" className="space-y-4">
              <TabsList className="grid w-full grid-cols-4">
                <TabsTrigger value="raw-text" className="gap-1.5">
                  <FileText className="h-4 w-4" />
                  提取文本
                </TabsTrigger>
                <TabsTrigger value="figures" className="gap-1.5">
                  <ImageIcon className="h-4 w-4" />
                  附图输出
                </TabsTrigger>
                <TabsTrigger value="claims" className="gap-1.5">
                  <Play className="h-4 w-4" />
                  权利要求
                </TabsTrigger>
                <TabsTrigger value="json" className="gap-1.5">
                  <FileJson className="h-4 w-4" />
                  原始 JSON
                </TabsTrigger>
              </TabsList>

              <TabsContent value="raw-text">
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base">提取文本</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <Textarea
                      value={result.rawText || ''}
                      readOnly
                      className="min-h-[560px] font-mono text-xs leading-5"
                    />
                    {specificationEntries.length > 0 && (
                      <div className="space-y-3">
                        <p className="text-sm font-medium">结构化章节</p>
                        {specificationEntries.map(([sectionName, sectionText]) => (
                          <div key={sectionName} className="rounded-md border p-3">
                            <p className="text-sm font-medium mb-2">{sectionName}</p>
                            <pre className="whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">
                              {sectionText}
                            </pre>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="figures">
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base">附图输出</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {figures.length === 0 ? (
                      <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
                        当前没有提取到附图。
                        如果这是 PDF，优先检查是否上传了原始 PDF 而不是 TXT/纯文本。
                      </div>
                    ) : (
                      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                        {figures.map((figure, index) => (
                          <div key={`${figure.figure_id || 'figure'}-${index}`} className="rounded-lg border p-3 space-y-3">
                            <div className="flex items-center justify-between gap-2">
                              <p className="font-medium">{figure.figure_id || `图${index + 1}`}</p>
                              {figure.figure_url ? (
                                <a
                                  href={figure.figure_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                                >
                                  <ExternalLink className="h-3 w-3" />
                                  打开
                                </a>
                              ) : (
                                <Badge variant="destructive">无 URL</Badge>
                              )}
                            </div>
                            {figure.figure_url ? (
                              // eslint-disable-next-line @next/next/no-img-element
                              <img
                                src={figure.figure_url}
                                alt={figure.figure_id || `附图 ${index + 1}`}
                                className="w-full rounded-md border bg-muted/30 object-contain max-h-72"
                              />
                            ) : (
                              <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
                                该附图记录没有可访问的图片 URL。
                              </div>
                            )}
                            <div className="space-y-2 text-sm">
                              <p className="text-muted-foreground">
                                {figure.figure_description || '暂无附图说明'}
                              </p>
                              {figure.storage_key && (
                                <p className="text-xs font-mono break-all text-muted-foreground">
                                  storage_key: {figure.storage_key}
                                </p>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="claims">
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base">权利要求输出</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {claims.length === 0 ? (
                      <div className="rounded-md border border-dashed p-8 text-sm text-muted-foreground">
                        当前没有提取到权利要求。
                      </div>
                    ) : (
                      <div className="space-y-3">
                        {claims.map((claim, index) => (
                          <div key={`${claim.claim_id || 'claim'}-${index}`} className="rounded-md border p-3 space-y-2">
                            <div className="flex flex-wrap gap-2">
                              <Badge variant="secondary">claim_id: {claim.claim_id || index + 1}</Badge>
                              <Badge variant="outline">{claim.claim_type || 'UNKNOWN'}</Badge>
                              {claim.parent_claim_id && (
                                <Badge variant="outline">parent: {claim.parent_claim_id}</Badge>
                              )}
                            </div>
                            <pre className="whitespace-pre-wrap break-words text-xs leading-5">
                              {claim.claim_text || ''}
                            </pre>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </TabsContent>

              <TabsContent value="json">
                <Card>
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base">原始 JSON 输出</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <pre className="max-h-[720px] overflow-auto rounded-md bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap break-all">
                      {JSON.stringify(result, null, 2)}
                    </pre>
                  </CardContent>
                </Card>
              </TabsContent>
            </Tabs>
          </>
        )}
      </main>
    </div>
  );
}

function MetadataItem({ label, value, href }: { label: string; value?: string | null; href?: string }) {
  return (
    <div className="rounded-md border p-3">
      <p className="text-muted-foreground">{label}</p>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-primary hover:underline break-all"
        >
          <ExternalLink className="h-3 w-3 shrink-0" />
          {value || '-'}
        </a>
      ) : (
        <p className="break-words">{value || '-'}</p>
      )}
    </div>
  );
}
