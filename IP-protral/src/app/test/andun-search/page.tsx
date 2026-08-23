'use client';

/* eslint-disable @next/next/no-img-element */

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, ExternalLink, Loader2, Play, RefreshCw, Search } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';

type HealthState = {
  status?: string;
  module?: string;
  api_env?: string;
  api_base_url?: string;
  credentials_configured?: boolean;
  request_timeout_seconds?: number;
  request_retry_attempts?: number;
  poll_interval_seconds?: number;
  max_wait_seconds?: number;
  proxyElapsedMs?: number;
  error?: string;
};

type ApiCallRow = {
  id?: number;
  method?: string;
  http_status?: number;
  response_code?: number;
  response_message?: string;
  elapsed_ms?: number;
  attempt_count?: number;
  retry_errors?: string[];
  response_summary?: Record<string, unknown>;
};

type ProductRow = {
  id?: number;
  platform?: string;
  platform_name?: string;
  product_front_page?: string;
  title?: string;
  price?: string;
  monthly_sales?: number;
  product_url?: string;
  store_name?: string;
  store_url?: string;
  trade_no?: string;
};

type RunDetail = {
  id?: number;
  andun_search_run_id?: number;
  product_dataset_id?: string;
  andun_task_id?: string;
  status?: string;
  remote_status?: number | null;
  keywords?: string[];
  platforms?: string[];
  product_count?: number;
  submit_elapsed_ms?: number;
  total_elapsed_ms?: number;
  wall_elapsed_ms?: number;
  error_message?: string;
  proxyElapsedMs?: number;
  products?: ProductRow[];
  api_calls?: ApiCallRow[];
  error?: string;
};

const TERMINAL_STATUSES = new Set(['completed', 'error', 'timeout']);

function splitList(value: string): string[] {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function makeSessionId(): string {
  return `andun_test_${Date.now()}`;
}

function formatMs(value?: number): string {
  if (value == null) return '-';
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

function statusText(status?: string, remoteStatus?: number | null): string {
  if (status === 'queued') return '本地排队';
  if (status === 'submitting') return '提交安盾任务';
  if (status === 'collecting' || remoteStatus === 0) return '数据采集中';
  if (status === 'organizing' || remoteStatus === 10) return '数据整理中';
  if (status === 'completed' || remoteStatus === 20) return '采集完成';
  if (status === 'timeout') return '等待超时';
  if (status === 'error') return '运行失败';
  return status || '未运行';
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export default function AndunSearchTestPage() {
  const [health, setHealth] = useState<HealthState | null>(null);
  const [patentRecordId, setPatentRecordId] = useState('0');
  const [analysisSessionId, setAnalysisSessionId] = useState(makeSessionId);
  const [taskName, setTaskName] = useState('安盾商品检索测试');
  const [keywords, setKeywords] = useState('开放式头戴耳机\n中空孔开放式头戴耳机');
  const [platforms, setPlatforms] = useState('TB, TM, XY, 1688, JD, PDD, XHS, DY');
  const [maxKeywords, setMaxKeywords] = useState('3');
  const [pageSize, setPageSize] = useState('100');
  const [maxPages, setMaxPages] = useState('3');
  const [maxProducts, setMaxProducts] = useState('100');
  const [pollIntervalSeconds, setPollIntervalSeconds] = useState('10');
  const [maxWaitSeconds, setMaxWaitSeconds] = useState('1800');
  const [existingRunId, setExistingRunId] = useState('');
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [error, setError] = useState('');

  async function refreshHealth() {
    try {
      const response = await fetch('/api/test/andun-search', { cache: 'no-store' });
      const data = (await response.json()) as HealthState;
      setHealth(data);
    } catch (requestError) {
      setHealth({ status: 'error', error: requestError instanceof Error ? requestError.message : String(requestError) });
    }
  }

  useEffect(() => {
    void refreshHealth();
  }, []);

  async function pollRun(runId: number) {
    while (true) {
      await sleep(3000);
      const response = await fetch(`/api/test/andun-search?runId=${runId}`, { cache: 'no-store' });
      const data = (await response.json()) as RunDetail;
      setRun(data);
      if (!response.ok) {
        throw new Error(data.error || `查询运行状态失败：HTTP ${response.status}`);
      }
      if (TERMINAL_STATUSES.has(String(data.status || ''))) return;
    }
  }

  async function loadExistingRun() {
    const runId = Number(existingRunId || 0);
    if (!runId) {
      setError('请填写有效的本地 run ID');
      return;
    }
    setRunning(true);
    setError('');
    try {
      const response = await fetch(`/api/test/andun-search?runId=${runId}`, { cache: 'no-store' });
      const data = (await response.json()) as RunDetail;
      setRun(data);
      if (!response.ok) throw new Error(data.error || `查询运行状态失败：HTTP ${response.status}`);
      if (!TERMINAL_STATUSES.has(String(data.status || ''))) await pollRun(runId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    } finally {
      setRunning(false);
    }
  }

  async function resumeRun() {
    const runId = Number(run?.id || run?.andun_search_run_id || 0);
    if (!runId) return;
    setRunning(true);
    setError('');
    try {
      const response = await fetch('/api/test/andun-search', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ runId }),
      });
      const data = (await response.json()) as RunDetail;
      if (!response.ok) throw new Error(data.error || `续跑失败：HTTP ${response.status}`);
      setRun(data);
      await pollRun(runId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    } finally {
      setRunning(false);
    }
  }

  async function startRun() {
    setRunning(true);
    setError('');
    setRun(null);
    try {
      const response = await fetch('/api/test/andun-search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          patentRecordId: Number(patentRecordId || 0),
          analysisSessionId,
          taskName,
          keywords: splitList(keywords),
          platforms: splitList(platforms),
          maxKeywords: Number(maxKeywords),
          pageSize: Number(pageSize),
          maxPages: Number(maxPages),
          maxProducts: Number(maxProducts),
          pollIntervalSeconds: Number(pollIntervalSeconds),
          maxWaitSeconds: Number(maxWaitSeconds),
        }),
      });
      const accepted = (await response.json()) as RunDetail;
      if (!response.ok) {
        throw new Error(accepted.error || `启动失败：HTTP ${response.status}`);
      }
      const runId = Number(accepted.andun_search_run_id || accepted.id || 0);
      if (!runId) throw new Error('服务未返回 andun_search_run_id');
      setExistingRunId(String(runId));
      setRun(accepted);
      await pollRun(runId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : String(requestError));
    } finally {
      setRunning(false);
    }
  }

  const products = Array.isArray(run?.products) ? run.products : [];
  const apiCalls = Array.isArray(run?.api_calls) ? run.api_calls : [];

  return (
    <main className="min-h-screen bg-muted/30 p-4 md:p-8">
      <div className="mx-auto max-w-[1500px] space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="mb-2 flex items-center gap-2 text-sm text-muted-foreground">
              <Link href="/test" className="inline-flex items-center gap-1 hover:text-foreground">
                <ArrowLeft className="h-4 w-4" /> 返回测试中心
              </Link>
            </div>
            <h1 className="text-2xl font-semibold">第三种模块三：安盾开放平台</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              独立端口 5108；测试异步任务提交、状态轮询、商品返回字段和调用耗时。
            </p>
          </div>
          <Button variant="outline" onClick={() => void refreshHealth()}>
            <RefreshCw className="mr-2 h-4 w-4" /> 刷新服务状态
          </Button>
        </div>

        <Card>
          <CardHeader><CardTitle className="text-base">服务状态</CardTitle></CardHeader>
          <CardContent className="space-y-4 text-sm">
            <div className="flex flex-wrap gap-2">
              <Badge variant={health?.status === 'ok' ? 'default' : 'destructive'}>{health?.status || '读取中'}</Badge>
              <Badge variant="outline">环境：{health?.api_env || '-'}</Badge>
              <Badge variant="outline">凭据：{health?.credentials_configured ? '已配置' : '未配置'}</Badge>
              <Badge variant="outline">网络重试：{health?.request_retry_attempts ?? '-'} 次</Badge>
              <Badge variant="outline">健康检查：{formatMs(health?.proxyElapsedMs)}</Badge>
              <span className="break-all text-muted-foreground">{health?.api_base_url}</span>
            </div>
            <div className="flex max-w-xl gap-2">
              <Input placeholder="输入已有本地 run ID，可在刷新页面后继续查看" value={existingRunId} onChange={(event) => setExistingRunId(event.target.value)} />
              <Button variant="outline" disabled={running} onClick={() => void loadExistingRun()}>读取运行</Button>
            </div>
          </CardContent>
        </Card>

        {!health?.credentials_configured && (
          <Alert variant="destructive">
            <AlertDescription>
              尚未配置 ANDUN_APP_KEY / ANDUN_APP_SECRET。服务和页面可以测试，但远端任务会明确返回凭据缺失。
            </AlertDescription>
          </Alert>
        )}

        <Card>
          <CardHeader><CardTitle className="text-base">测试参数</CardTitle></CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-2"><Label>专利记录 ID（可为 0）</Label><Input value={patentRecordId} onChange={(event) => setPatentRecordId(event.target.value)} /></div>
            <div className="space-y-2"><Label>测试 Session</Label><Input value={analysisSessionId} onChange={(event) => setAnalysisSessionId(event.target.value)} /></div>
            <div className="space-y-2 md:col-span-2"><Label>安盾任务名称</Label><Input value={taskName} onChange={(event) => setTaskName(event.target.value)} /></div>
            <div className="space-y-2 md:col-span-2"><Label>关键词（换行或逗号分隔）</Label><Textarea rows={5} value={keywords} onChange={(event) => setKeywords(event.target.value)} /></div>
            <div className="space-y-2 md:col-span-2"><Label>平台</Label><Textarea rows={5} value={platforms} onChange={(event) => setPlatforms(event.target.value)} /></div>
            <div className="space-y-2"><Label>最多关键词</Label><Input value={maxKeywords} onChange={(event) => setMaxKeywords(event.target.value)} /></div>
            <div className="space-y-2"><Label>每页数量</Label><Input value={pageSize} onChange={(event) => setPageSize(event.target.value)} /></div>
            <div className="space-y-2"><Label>最多页数</Label><Input value={maxPages} onChange={(event) => setMaxPages(event.target.value)} /></div>
            <div className="space-y-2"><Label>最多商品</Label><Input value={maxProducts} onChange={(event) => setMaxProducts(event.target.value)} /></div>
            <div className="space-y-2"><Label>轮询间隔（秒）</Label><Input value={pollIntervalSeconds} onChange={(event) => setPollIntervalSeconds(event.target.value)} /></div>
            <div className="space-y-2"><Label>最长等待（秒）</Label><Input value={maxWaitSeconds} onChange={(event) => setMaxWaitSeconds(event.target.value)} /></div>
            <div className="flex items-end md:col-span-2">
              <Button className="w-full" disabled={running} onClick={() => void startRun()}>
                {running ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Play className="mr-2 h-4 w-4" />}
                {running ? '任务运行中' : '启动安盾检索测试'}
              </Button>
            </div>
          </CardContent>
        </Card>

        {error && <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert>}

        {run && (
          <Card>
            <CardHeader><CardTitle className="text-base">运行结果</CardTitle></CardHeader>
            <CardContent className="space-y-4">
              <div className="flex flex-wrap gap-2">
                <Badge>{statusText(run.status, run.remote_status)}</Badge>
                <Badge variant="outline">本地 run：{run.id || run.andun_search_run_id || '-'}</Badge>
                <Badge variant="outline">安盾 taskId：{run.andun_task_id || '-'}</Badge>
                <Badge variant="outline">提交耗时：{formatMs(run.submit_elapsed_ms)}</Badge>
                <Badge variant="outline">{TERMINAL_STATUSES.has(String(run.status || '')) ? '总耗时' : '已运行'}：{formatMs(TERMINAL_STATUSES.has(String(run.status || '')) ? run.total_elapsed_ms : run.wall_elapsed_ms)}</Badge>
                <Badge variant="outline">商品：{run.product_count ?? products.length}</Badge>
                {(run.status === 'error' || run.status === 'timeout') && run.andun_task_id && (
                  <Button size="sm" variant="outline" disabled={running} onClick={() => void resumeRun()}>
                    <RefreshCw className="mr-2 h-4 w-4" />按同一 taskId 续跑
                  </Button>
                )}
              </div>
              {!TERMINAL_STATUSES.has(String(run.status || '')) && (
                <Alert><AlertDescription>安盾任务在远端异步采集，可能持续数分钟或更久。可记下本地 run ID，刷新页面后重新读取；前端请求不会一直占用到任务完成。</AlertDescription></Alert>
              )}
              {run.error_message && <Alert variant="destructive"><AlertDescription>{run.error_message}</AlertDescription></Alert>}
            </CardContent>
          </Card>
        )}

        {apiCalls.length > 0 && (
          <Card>
            <CardHeader><CardTitle className="text-base">API 调用时延</CardTitle></CardHeader>
            <CardContent className="overflow-x-auto">
              <Table className="min-w-[1050px] table-fixed">
                <TableHeader><TableRow><TableHead className="w-[330px]">接口</TableHead><TableHead className="w-[90px]">HTTP</TableHead><TableHead className="w-[90px]">Code</TableHead><TableHead className="w-[120px]">尝试</TableHead><TableHead className="w-[140px]">耗时</TableHead><TableHead>信息</TableHead></TableRow></TableHeader>
                <TableBody>{apiCalls.map((call, index) => <TableRow key={call.id || index}><TableCell className="break-all">{call.method}</TableCell><TableCell>{call.http_status}</TableCell><TableCell>{call.response_code}</TableCell><TableCell>{call.attempt_count || 1}</TableCell><TableCell>{formatMs(call.elapsed_ms)}</TableCell><TableCell className="whitespace-normal break-words">{call.response_message}{call.retry_errors?.length ? `；已恢复：${call.retry_errors.join(', ')}` : ''}</TableCell></TableRow>)}</TableBody>
              </Table>
            </CardContent>
          </Card>
        )}

        {products.length > 0 && (
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Search className="h-4 w-4" />商品数据</CardTitle></CardHeader>
            <CardContent className="overflow-x-auto">
              <Table className="min-w-[1300px] table-fixed">
                <TableHeader><TableRow><TableHead className="w-[90px]">平台</TableHead><TableHead className="w-[100px]">首图</TableHead><TableHead className="w-[360px]">标题</TableHead><TableHead className="w-[100px]">价格</TableHead><TableHead className="w-[110px]">月销量</TableHead><TableHead className="w-[220px]">店铺</TableHead><TableHead>链接</TableHead></TableRow></TableHeader>
                <TableBody>{products.map((product, index) => <TableRow key={product.id || product.trade_no || index}><TableCell className="whitespace-normal">{product.platform_name || product.platform}</TableCell><TableCell>{product.product_front_page ? <a href={product.product_front_page} target="_blank" rel="noopener noreferrer"><img src={product.product_front_page} alt="商品首图" className="h-16 w-16 rounded border object-cover" /></a> : '-'}</TableCell><TableCell className="whitespace-normal break-words">{product.title}</TableCell><TableCell>{product.price}</TableCell><TableCell>{product.monthly_sales}</TableCell><TableCell className="whitespace-normal break-words">{product.store_url ? <a className="text-primary hover:underline" href={product.store_url} target="_blank" rel="noopener noreferrer">{product.store_name || '店铺'} <ExternalLink className="inline h-3 w-3" /></a> : product.store_name}</TableCell><TableCell className="whitespace-normal break-all">{product.product_url ? <a className="text-primary hover:underline" href={product.product_url} target="_blank" rel="noopener noreferrer">查看商品 <ExternalLink className="inline h-3 w-3" /></a> : '-'}</TableCell></TableRow>)}</TableBody>
              </Table>
            </CardContent>
          </Card>
        )}
      </div>
    </main>
  );
}
