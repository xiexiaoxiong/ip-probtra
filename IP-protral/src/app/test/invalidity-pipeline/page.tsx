'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Ban, FlaskConical, Loader2, Play, RefreshCw, RotateCcw } from 'lucide-react';
import { UploadForm } from '@/components/upload-form';
import { InvalidityReviewPanel } from '@/components/invalidity-review-panel';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { liveLabPayloadViolation } from '@/lib/invalidity-contracts';

type JsonObject = Record<string, unknown>;
type InputMode = 'fixture' | 'manual' | 'live';

const MODULES = [
  'I1_TARGET_SNAPSHOT', 'I1_5_CLAIM_DATES', 'I2_QUERY_PLAN', 'I3_PATENT_SEARCH', 'I3_NPL_SEARCH',
  'I3_CANDIDATE_FILTER', 'I3_FETCH', 'I3_QUALIFY', 'I4_S_SINGLE_REFERENCE', 'I4_C_CLOSEST_PRIOR_ART',
  'I4_I_INVENTIVE_STEP', 'I5_REPORT',
] as const;

const EPO_ORDINARY_PRIOR_ART_EXAMPLE = {
  query: 'optical sensor filter',
  language: 'en',
  server_before: '2020-06-15',
  max_results: 10,
};

const EPO_CN_CONFLICTING_APPLICATION_EXAMPLE = {
  query: '光学传感器 滤光结构',
  language: 'zh',
  country: 'CN',
  server_after: '2020-06-15',
  server_before: '2021-06-01',
  filing_before: '2020-06-15',
  max_results: 10,
};

const PATSNAP_P002_TEXT_EXAMPLE = {
  search_provider: 'patsnap',
  search_modality: 'text',
  query: 'TACD:(optical sensor AND sealed chamber)',
  language: 'en',
  server_before: '2020-06-15',
  max_results: 10,
};

const PATSNAP_P060_BETA_SINGLE_IMAGE_EXAMPLE = {
  search_provider: 'patsnap',
  search_modality: 'image_single',
  image_url: 'https://static-open.zhihuiya.com/sample/common_demo.png',
  patent_type: 'D',
  model: 1,
  max_results: 10,
  offset: 0,
};

const PATSNAP_P061_MULTIPLE_IMAGE_EXAMPLE = {
  search_provider: 'patsnap',
  search_modality: 'image_multiple',
  image_urls: [
    'https://static-open.zhihuiya.com/sample/common_demo.png',
    'https://static-open.zhihuiya.com/sample/common_demo_2.png',
  ],
  patent_type: 'D',
  model: 1,
  max_results: 10,
  offset: 0,
};

function PatsnapI3LiveHelp({ onApply }: { onApply: (input: JsonObject) => void }) {
  return (
    <Alert>
      <AlertTitle>智慧芽 I3-P 快捷模板（仅测试实验室）</AlertTitle>
      <AlertDescription className="space-y-3">
        <p>
          先填写当前账号所属的测试 <code>investigation_id</code>。快捷按钮只填充下方通用
          JSON，不会切换全局 provider，也不会调用正式版。
        </p>
        <p>
          P002、P060 beta 和 P061 当前只返回检索 <code>lead</code>；P012、P018、P019
          当前测试账号均无权限，检索命中不会直接进入 CC 表。
        </p>
        <p>
          图片必须是智慧芽可访问的公网 HTTPS URL；P060 beta 使用一张图片，P061
          必须使用 2–4 张图片。模板使用智慧芽官方公开样例，不包含 API Key。
        </p>
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-patsnap-template="p002-text"
            onClick={() => onApply(PATSNAP_P002_TEXT_EXAMPLE)}
          >
            填入 P002 文本模板
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-patsnap-template="p060-beta-single"
            onClick={() => onApply(PATSNAP_P060_BETA_SINGLE_IMAGE_EXAMPLE)}
          >
            填入 P060 beta 单图模板
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-patsnap-template="p061-multiple"
            onClick={() => onApply(PATSNAP_P061_MULTIPLE_IMAGE_EXAMPLE)}
          >
            填入 P061 多图模板
          </Button>
        </div>
        <div className="space-y-2 border-t pt-3">
          <p>
            文本检索的日期通道仍可使用 <code>server_before</code>、<code>server_after</code>
            和 <code>filing_before</code>；最终资格由本地日期引擎核验。
          </p>
          <div className="grid gap-2 lg:grid-cols-2">
            <pre className="overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">
              {JSON.stringify(EPO_ORDINARY_PRIOR_ART_EXAMPLE, null, 2)}
            </pre>
            <pre className="overflow-auto rounded bg-slate-950 p-3 text-xs text-slate-100">
              {JSON.stringify(EPO_CN_CONFLICTING_APPLICATION_EXAMPLE, null, 2)}
            </pre>
          </div>
        </div>
      </AlertDescription>
    </Alert>
  );
}

async function jsonRequest(path: string, init?: RequestInit): Promise<JsonObject> {
  const response = await fetch(`/api/test/invalidity/${path.replace(/^\//, '')}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...(init.headers || {}) } : init?.headers,
    cache: 'no-store',
  });
  const data = await response.json() as JsonObject;
  if (!response.ok) throw new Error(String(data.error || data.detail || `HTTP ${response.status}`));
  return data;
}

export default function InvalidityPipelineLabPage() {
  const [health, setHealth] = useState<JsonObject | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [investigation, setInvestigation] = useState<JsonObject | null>(null);
  const [moduleCode, setModuleCode] = useState<(typeof MODULES)[number]>('I2_QUERY_PLAN');
  const [moduleInputMode, setModuleInputMode] = useState<InputMode>('fixture');
  const [moduleInput, setModuleInput] = useState('{\n  "fixture": "default"\n}');
  const [moduleRun, setModuleRun] = useState<JsonObject | null>(null);
  const [moduleRunMode, setModuleRunMode] = useState<InputMode | null>(null);
  const [investigationId, setInvestigationId] = useState('');
  const [claimInvestigationId, setClaimInvestigationId] = useState('');
  const [moduleActionReason, setModuleActionReason] = useState('模块实验室人工操作');

  const moduleRunRecord = useMemo(() => {
    const nested = moduleRun?.module_run;
    return nested && typeof nested === 'object' && !Array.isArray(nested)
      ? nested as JsonObject
      : moduleRun;
  }, [moduleRun]);
  const moduleJobRecord = useMemo(() => {
    const nested = moduleRun?.job;
    return nested && typeof nested === 'object' && !Array.isArray(nested)
      ? nested as JsonObject
      : null;
  }, [moduleRun]);
  const currentModuleRunId = String(
    moduleRunRecord?.id || moduleRun?.module_run_id || '',
  );
  const currentRunStatus = String(
    moduleRunRecord?.status || moduleRun?.status || '',
  ).toLowerCase();
  const currentJobStatus = String(
    moduleJobRecord?.status || moduleRun?.job_status || '',
  ).toLowerCase();
  const canCancelModule = Boolean(currentModuleRunId) && (
    ['created', 'queued', 'running'].includes(currentRunStatus)
    || ['queued', 'leased'].includes(currentJobStatus)
  );
  const canRetryModule = Boolean(currentModuleRunId)
    && currentRunStatus === 'failed'
    && (!currentJobStatus || currentJobStatus === 'failed');

  const refreshHealth = async () => {
    try { setHealth(await jsonRequest('health')); setError(null); }
    catch (caught) { setHealth(null); setError(caught instanceof Error ? caught.message : '测试服务不可达'); }
  };

  useEffect(() => { void refreshHealth(); }, []);

  const createInvestigation = async (input: { type: 'url' | 'file' | 'text'; url?: string; fileKey?: string; text?: string }) => {
    setBusy(true); setError(null);
    try {
      let sourceFileKey: string | undefined;
      let sourceUrl: string | undefined;
      if (input.type === 'url') sourceUrl = input.url;
      if (input.type === 'file' && input.fileKey) sourceFileKey = input.fileKey;
    if (input.type === 'text' && input.text) {
        const form = new FormData();
        form.append('file', new File([input.text], `invalidity-test-${crypto.randomUUID()}.txt`, { type: 'text/plain' }));
        const uploadResponse = await fetch('/api/invalidity/uploads?environment=test', { method: 'POST', body: form });
        const uploaded = await uploadResponse.json() as JsonObject;
        if (!uploadResponse.ok || !uploaded.fileKey) throw new Error(String(uploaded.error || '测试文本保存失败'));
        sourceFileKey = String(uploaded.fileKey);
      }
      const intentKey = crypto.randomUUID();
      const created = await jsonRequest('v1/investigations', {
        method: 'POST',
        body: JSON.stringify({
          contract_version: 'v1',
          analysis_session_id: `invalidity_test_${crypto.randomUUID()}`,
          source_file_key: sourceFileKey || null,
          source_url: sourceUrl || null,
          max_rounds: 5,
          provider_mode: 'live',
          idempotency_key: intentKey,
        }),
      });
      const id = String(created.investigation_id || '');
      if (!id) throw new Error('测试服务未返回 investigation_id');
      setInvestigationId(id);
      const started = await jsonRequest(`v1/investigations/${encodeURIComponent(id)}/start`, { method: 'POST', body: '{}' });
      setInvestigation({ ...created, start: started });
    } catch (caught) { setError(caught instanceof Error ? caught.message : '创建失败'); }
    finally { setBusy(false); }
  };

  const refreshInvestigation = async () => {
    if (!investigationId) return;
    setBusy(true);
    try { setInvestigation(await jsonRequest(`v1/investigations/${encodeURIComponent(investigationId)}`)); setError(null); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '读取失败'); }
    finally { setBusy(false); }
  };

  const applyPatsnapTemplate = (input: JsonObject) => {
    setModuleCode('I3_PATENT_SEARCH');
    setModuleInputMode('live');
    setModuleInput(`${JSON.stringify(input, null, 2)}\n`);
    setModuleRun(null);
    setModuleRunMode(null);
    setError(null);
  };

  const runModule = async () => {
    setBusy(true); setError(null);
    try {
      const parsed = JSON.parse(moduleInput) as JsonObject;
      if (moduleInputMode === 'live') {
        const violation = liveLabPayloadViolation(parsed);
        if (violation) throw new Error(`live 输入被拒绝：${violation}`);
      }
      const formIntent = {
        module_code: moduleCode,
        input_mode: moduleInputMode,
        investigation_id: investigationId || null,
        claim_investigation_id: claimInvestigationId || null,
        input: parsed,
      };
      const created = await jsonRequest('v1/lab/module-runs', {
        method: 'POST',
        body: JSON.stringify({
          contract_version: 'v1',
          ...formIntent,
          idempotency_key: crypto.randomUUID(),
          force_recompute: true,
        }),
      });
      setModuleRun(created);
      setModuleRunMode(moduleInputMode);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '模块运行失败'); }
    finally { setBusy(false); }
  };

  const refreshModule = async () => {
    const runId = currentModuleRunId;
    if (!runId) return;
    setBusy(true);
    try {
      const result = await jsonRequest(`v1/lab/module-runs/${encodeURIComponent(runId)}`);
      if (moduleRunMode === 'live') {
        const violation = liveLabPayloadViolation(result, 'response');
        if (violation) throw new Error(`live 运行返回了 fixture/manual 标记，已拒绝展示：${violation}`);
      }
      setModuleRun(result);
      setError(null);
    }
    catch (caught) { setError(caught instanceof Error ? caught.message : '模块状态读取失败'); }
    finally { setBusy(false); }
  };

  const cancelModule = async () => {
    if (!currentModuleRunId || !moduleActionReason.trim()) return;
    setBusy(true); setError(null);
    try {
      const result = await jsonRequest(
        `v1/lab/module-runs/${encodeURIComponent(currentModuleRunId)}/cancel`,
        {
          method: 'POST',
          body: JSON.stringify({
            contract_version: 'v1',
            reason: moduleActionReason.trim(),
          }),
        },
      );
      setModuleRun(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '取消模块运行失败');
    } finally { setBusy(false); }
  };

  const retryModule = async () => {
    if (!currentModuleRunId || !moduleActionReason.trim()) return;
    setBusy(true); setError(null);
    try {
      const result = await jsonRequest(
        `v1/lab/module-runs/${encodeURIComponent(currentModuleRunId)}/retries`,
        {
          method: 'POST',
          body: JSON.stringify({
            contract_version: 'v1',
            reason: moduleActionReason.trim(),
            idempotency_key: crypto.randomUUID(),
          }),
        },
      );
      setModuleRun(result);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '人工重试模块失败');
    } finally { setBusy(false); }
  };

  const healthEnvironment = useMemo(() => String((health?.isolation as JsonObject | undefined)?.environment || health?.environment || 'unknown'), [health]);

  return (
    <div className="min-h-screen bg-muted/20">
      <header className="border-b bg-background"><div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6"><div className="flex items-center gap-2 font-semibold"><FlaskConical className="h-5 w-5" />无效检索模块实验室 <Badge variant="outline">TEST / 5209</Badge></div><div className="flex gap-2"><Button asChild size="sm" variant="outline"><Link href="/test">测试首页</Link></Button><Button asChild size="sm" variant="outline"><Link href="/test/invalidity">P002 引导测试</Link></Button><Button asChild size="sm"><Link href="/">返回 Agent</Link></Button></div></div></header>
      <main className="mx-auto max-w-6xl space-y-6 px-6 py-6">
        <Alert variant={healthEnvironment === 'test' ? 'default' : 'destructive'}><AlertTitle>隔离状态：{healthEnvironment}</AlertTitle><AlertDescription>{health ? `测试服务已响应；数据库、工件和模块一地址以健康摘要为准。` : '未取得测试服务健康状态。缺少 TEST 配置时页面会失败，不会回落到正式端口。'}</AlertDescription></Alert>
        {error ? <Alert variant="destructive"><AlertTitle>错误</AlertTitle><AlertDescription>{error}</AlertDescription></Alert> : null}

        <Card><CardHeader><CardTitle>完整调查测试 <Badge variant="outline">live</Badge></CardTitle><CardDescription>完整 I0 只运行真实 provider，避免把 fixture/manual 标签误当成真实世界检索；每项独立权利要求先运行首轮，再对未覆盖区别特征最多检索五轮。固定夹具和人工输入请在下方单模块实验中使用。</CardDescription></CardHeader><CardContent className="space-y-4"><UploadForm onSubmit={createInvestigation} isAnalyzing={busy} uploadEndpoint="/api/invalidity/uploads?environment=test" />{investigationId ? <div className="flex gap-2"><Input value={investigationId} readOnly /><Button variant="outline" onClick={() => void refreshInvestigation()} disabled={busy}><RefreshCw className="mr-1 h-4 w-4" />刷新</Button></div> : null}{investigation ? <pre className="max-h-80 overflow-auto rounded-md bg-slate-950 p-4 text-xs text-slate-100">{JSON.stringify(investigation, null, 2)}</pre> : null}</CardContent></Card>

        <InvalidityReviewPanel environment="test" investigationId={investigationId} onChanged={() => void refreshInvestigation()} />

        <Card>
          <CardHeader>
            <CardTitle>单模块运行</CardTitle>
            <CardDescription>
              可以选择模块，分别投入 fixture、人工 JSON 或真实输入，观察持久化输出与错误。live
              模式会在浏览器和测试代理两层拒绝 fixture、manual provider 与 simulated
              输出。取消会先持久化请求；人工重试始终创建新的 run，旧 run 保持不变。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              <Select
                value={moduleCode}
                onValueChange={(value) => setModuleCode(value as (typeof MODULES)[number])}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {MODULES.map((code) => <SelectItem key={code} value={code}>{code}</SelectItem>)}
                </SelectContent>
              </Select>
              <Select
                value={moduleInputMode}
                onValueChange={(value) => {
                  const mode = value as InputMode;
                  setModuleInputMode(mode);
                  setModuleInput(mode === 'live' ? '{}\n' : mode === 'fixture' ? '{\n  "fixture": "default"\n}' : '{}\n');
                  setModuleRun(null);
                  setModuleRunMode(null);
                }}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="fixture">fixture</SelectItem>
                  <SelectItem value="manual">manual</SelectItem>
                  <SelectItem value="live">live</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {moduleInputMode === 'live' ? (
              <Alert>
                <AlertTitle>真实运行守门已启用</AlertTitle>
                <AlertDescription>
                  输入与响应中出现 fixture_output、fixture_id、simulated=true、manual/fixture
                  mode 或 fixture provider/model 时会明确失败，原始伪造结果不会展示。
                </AlertDescription>
              </Alert>
            ) : null}
            {moduleInputMode === 'live' && moduleCode === 'I3_PATENT_SEARCH' ? (
              <PatsnapI3LiveHelp onApply={applyPatsnapTemplate} />
            ) : null}
            <div className="grid gap-3 md:grid-cols-2">
              <Input
                placeholder="investigation_id（live 必填）"
                value={investigationId}
                onChange={(event) => setInvestigationId(event.target.value)}
              />
              <Input
                placeholder="claim_investigation_id（按需）"
                value={claimInvestigationId}
                onChange={(event) => setClaimInvestigationId(event.target.value)}
              />
            </div>
            <Textarea
              className="min-h-64 font-mono text-xs"
              value={moduleInput}
              onChange={(event) => setModuleInput(event.target.value)}
            />
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => void runModule()} disabled={busy}>
                {busy
                  ? <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  : <Play className="mr-1 h-4 w-4" />}
                运行模块
              </Button>
              <Button
                variant="outline"
                onClick={() => void refreshModule()}
                disabled={busy || !moduleRun}
              >
                <RefreshCw className="mr-1 h-4 w-4" />刷新运行状态
              </Button>
            </div>
            {moduleRun ? (
              <div className="space-y-3 rounded-md border p-3">
                <div className="grid gap-3 md:grid-cols-[1fr_auto_auto]">
                  <Input
                    aria-label="模块操作原因"
                    value={moduleActionReason}
                    onChange={(event) => setModuleActionReason(event.target.value)}
                    placeholder="填写取消或人工重试原因"
                  />
                  <Button
                    variant="destructive"
                    onClick={() => void cancelModule()}
                    disabled={busy || !canCancelModule || !moduleActionReason.trim()}
                  >
                    <Ban className="mr-1 h-4 w-4" />取消运行
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => void retryModule()}
                    disabled={busy || !canRetryModule || !moduleActionReason.trim()}
                  >
                    <RotateCcw className="mr-1 h-4 w-4" />新建重试 run
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  当前 run：{currentModuleRunId || '—'}；run 状态：{currentRunStatus || '—'}；job
                  状态：{currentJobStatus || '—'}。只有 failed run/job 可人工重试。
                </p>
              </div>
            ) : null}
            {moduleRun ? (
              <pre className="max-h-[500px] overflow-auto rounded-md bg-slate-950 p-4 text-xs text-slate-100">
                {JSON.stringify(moduleRun, null, 2)}
              </pre>
            ) : null}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
