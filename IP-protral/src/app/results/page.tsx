'use client';

// ============================================================
// 页面2：商品侵权汇总页面
// 展示所有检索到的商品及其侵权结论概要
// ============================================================

import { Suspense, useEffect, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import type { AnalysisSession, ProductComparison } from '@/lib/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Separator } from '@/components/ui/separator';
import { Shield, ExternalLink, ArrowLeft, Loader2, AlertCircle, FileSearch, Database, Download } from 'lucide-react';
import Link from 'next/link';
import { FeishuConfig } from '@/components/feishu-config';
import { ResultsScoreTable } from '@/components/results-score-table';
import type { ProductInfo } from '@/lib/types';
import { sortProductsByComparisonScore } from '@/lib/results-consistency';

export default function ResultsPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-background">
          <div className="text-center space-y-3">
            <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto" />
            <p className="text-sm text-muted-foreground">加载分析结果...</p>
          </div>
        </div>
      }
    >
      <ResultsContent />
    </Suspense>
  );
}

function getPatentAbstract(explicitAbstract?: string, specification?: string): string {
  if (explicitAbstract?.trim()) {
    return explicitAbstract.trim();
  }
  const source = specification || '';
  if (!source.trim()) return '';

  const match = source.match(/(?:^|\n)摘要\s*\n([\s\S]*?)(?=\n{2,}[^\n]{1,30}\n|$)/);
  if (match?.[1]?.trim()) {
    return match[1].trim();
  }

  const inlineMatch = source.match(/摘要[:：\s]*([\s\S]{20,600}?)(?=\n{2,}|$)/);
  return inlineMatch?.[1]?.trim() || '';
}

function ResultsContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const sessionId = searchParams.get('session');

  const [session, setSession] = useState<AnalysisSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [importedProducts, setImportedProducts] = useState<ProductInfo[] | null>(null);
  const [importedComparisons, setImportedComparisons] = useState<ProductComparison[] | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setError('缺少会话 ID，请从首页开始分析');
      setLoading(false);
      return;
    }

    let pollingTimer: ReturnType<typeof setInterval> | null = null;
    let isDisposed = false;

    const hasStructuredResults = (nextSession: AnalysisSession | null): boolean => {
      if (!nextSession?.results) return false;
      const products = nextSession.results.products || [];
      const comparisons = nextSession.results.comparisons || [];
      return products.length > 0 || comparisons.length > 0;
    };

    const fetchResults = async (): Promise<AnalysisSession | null> => {
      try {
        const response = await fetch(`/api/analysis/${sessionId}?t=${Date.now()}`, {
          cache: 'no-store',
        });
        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
          throw new Error('服务端返回非 JSON 响应，请检查服务是否正常');
        }
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error((data as Record<string, string>).error || '获取分析结果失败');
        }
        const data = await response.json();
        const nextSession = data.session as AnalysisSession;
        if (!isDisposed) {
          setSession(nextSession);
          setError(null);
        }
        return nextSession;
      } catch (err) {
        if (!isDisposed) {
          setError(err instanceof Error ? err.message : '获取结果时出错');
        }
        return null;
      } finally {
        if (!isDisposed) {
          setLoading(false);
        }
      }
    };

    const syncResults = async () => {
      const nextSession = await fetchResults();
      if (!nextSession || isDisposed) return;

      const isFinished = nextSession.status === 'completed' || nextSession.status === 'error';
      if (isFinished && pollingTimer) {
        clearInterval(pollingTimer);
        pollingTimer = null;
      }

      // 即使用户提前进入结果页，也持续刷新直到后台真正完成，避免页面停留在旧快照。
      if (!isFinished || !hasStructuredResults(nextSession)) {
        if (!pollingTimer) {
          pollingTimer = setInterval(() => {
            void syncResults();
          }, 3000);
        }
      }
    };

    void syncResults();

    return () => {
      isDisposed = true;
      if (pollingTimer) {
        clearInterval(pollingTimer);
      }
    };
  }, [sessionId]);

  // 加载状态
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center space-y-3">
          <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto" />
          <p className="text-sm text-muted-foreground">加载分析结果...</p>
        </div>
      </div>
    );
  }

  // 错误状态
  if (error || !session) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="max-w-md text-center space-y-4">
          <AlertCircle className="h-10 w-10 text-destructive mx-auto" />
          <p className="text-sm text-muted-foreground">{error || '未找到分析会话'}</p>
          <Link href="/">
            <Button variant="outline">返回首页</Button>
          </Link>
        </div>
      </div>
    );
  }

  const products = importedProducts || session.results?.products || [];
  const comparisons = importedComparisons || session.results?.comparisons || [];
  const feishuUrl = session.results?.feishuUrl;
  const isSessionFinished = session.status === 'completed' || session.status === 'error';
  const patent = session.results?.patent;
  const patentAbstract = getPatentAbstract(patent?.abstract, patent?.specification);
  const abstractFigure = Array.isArray(patent?.drawings) ? patent.drawings.find((url) => typeof url === 'string' && url.trim()) : undefined;

  const handleExportReport = async () => {
    if (!sessionId || exporting) {
      return;
    }

    try {
      setExporting(true);
      setExportError(null);

      const response = await fetch(`/api/analysis/${sessionId}/export`, {
        method: 'GET',
      });

      if (!response.ok) {
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
          const data = await response.json().catch(() => ({}));
          throw new Error((data as { error?: string }).error || '导出报告失败');
        }
        throw new Error('导出报告失败，请稍后重试');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const disposition = response.headers.get('content-disposition') || '';
      const fileNameMatch = disposition.match(/filename\*=UTF-8''([^;]+)|filename="([^"]+)"/i);
      const encodedName = fileNameMatch?.[1] || fileNameMatch?.[2];
      const fallbackName = `${session.results?.patent?.title || 'analysis-report'}-${sessionId}.xlsx`;
      const fileName = encodedName ? decodeURIComponent(encodedName) : fallbackName;

      const link = document.createElement('a');
      link.href = url;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err instanceof Error ? err.message : '导出报告失败');
    } finally {
      setExporting(false);
    }
  };

  const verdictStats = {
    highRisk: 0,
    mediumRisk: 0,
    lowRisk: 0,
    clearLowRisk: 0,
  };

  for (const comp of comparisons) {
    switch (comp.riskLevel) {
      case 'high_risk':
        verdictStats.highRisk++;
        break;
      case 'medium_risk':
        verdictStats.mediumRisk++;
        break;
      case 'clear_low_risk':
        verdictStats.clearLowRisk++;
        break;
      default:
        verdictStats.lowRisk++;
        break;
    }
  }

  const sortedProducts = sortProductsByComparisonScore(products, comparisons);

  return (
    <div className="min-h-screen bg-muted/20">
      {/* 顶部导航 */}
      <header className="border-b bg-background/90 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="ghost" size="sm" onClick={() => router.push('/')} className="gap-1.5">
              <ArrowLeft className="h-4 w-4" />
              返回
            </Button>
            {sessionId && (
              <Link href={`/database?session=${sessionId}`}>
                <Button variant="outline" size="sm" className="gap-1.5">
                  <Database className="h-4 w-4" />
                  查看数据库
                </Button>
              </Link>
            )}
            {sessionId && (
              <Link href={`/module1?session=${sessionId}`}>
                <Button variant="outline" size="sm" className="gap-1.5">
                  <FileSearch className="h-4 w-4" />
                  模块1结果
                </Button>
              </Link>
            )}
            <Separator orientation="vertical" className="h-5" />
            <div className="flex items-center gap-2">
              <Shield className="h-4 w-4 text-primary" />
              <span className="text-sm font-medium whitespace-nowrap">侵权分析结果</span>
            </div>
            {sessionId && (
              <Badge variant="outline" className="hidden font-mono text-[11px] sm:inline-flex">
                {sessionId}
              </Badge>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={handleExportReport}
              disabled={exporting}
            >
              {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              导出报告
            </Button>
            {feishuUrl && (
              <a
                href={feishuUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                <ExternalLink className="h-3 w-3" />
                飞书多维表格
              </a>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        {exportError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{exportError}</AlertDescription>
          </Alert>
        )}
        {/* 分析概要 */}
        <Card className="shadow-sm">
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <FileSearch className="h-4 w-4" />
              分析概要
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
                <div className="space-y-3">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div>
                      <span className="text-xs text-muted-foreground">专利号</span>
                      <p className="text-sm font-medium leading-6">{patent?.patentNumber || session.patentNumber || '暂未识别'}</p>
                    </div>
                    <div>
                      <span className="text-xs text-muted-foreground">专利名称</span>
                      <p className="text-sm font-medium leading-6">{patent?.title || session.patentTitle || '暂未识别'}</p>
                    </div>
                  </div>
                  <div>
                    <span className="text-xs text-muted-foreground">专利摘要</span>
                    <p className="mt-1 text-sm leading-6 text-muted-foreground whitespace-pre-wrap">
                      {patentAbstract || '当前结果中暂无摘要内容。'}
                    </p>
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="rounded-lg border bg-background p-3 shadow-sm">
                    <span className="text-xs text-muted-foreground">摘要附图</span>
                    {abstractFigure ? (
                      <a href={abstractFigure} target="_blank" rel="noopener noreferrer" className="mt-2 block overflow-hidden rounded-md border bg-muted">
                        <img src={abstractFigure} alt="摘要附图" className="h-40 w-full object-contain" />
                      </a>
                    ) : (
                      <div className="mt-2 flex h-40 items-center justify-center rounded-md border bg-muted/30 text-xs text-muted-foreground">
                        暂无摘要附图
                      </div>
                    )}
                  </div>
                  <div className="rounded-lg border bg-muted/20 p-3">
                    <span className="text-xs text-muted-foreground">分析进度</span>
                    <p className="mt-1 text-sm font-medium">{isSessionFinished ? '已完成' : '分析中'}</p>
                    <p className="mt-1 text-xs text-muted-foreground">结果页会自动同步后台最新数据。</p>
                  </div>
                </div>
              </div>

              {/* 关键词 */}
              {session.results?.keywords && session.results.keywords.length > 0 && (
                <div>
                  <span className="text-xs text-muted-foreground">检索关键词</span>
                  <div className="flex flex-wrap gap-1.5 mt-1">
                    {session.results.keywords.map((kw, i) => (
                      <Badge key={i} variant="secondary" className="text-xs">{kw}</Badge>
                    ))}
                  </div>
                </div>
              )}

              {/* 统计结果 */}
              <div>
                <span className="text-xs text-muted-foreground">分析结果</span>
                <div className="grid gap-3 mt-2 sm:grid-cols-2 lg:grid-cols-5">
                  <div className="rounded-lg border bg-background p-3 text-center shadow-sm">
                    <div className="text-xl font-bold">{products.length}</div>
                    <div className="text-[11px] text-muted-foreground">检索商品总数</div>
                  </div>
                  <div className="rounded-lg border bg-green-50/70 p-3 text-center shadow-sm dark:bg-green-950/20">
                    <div className="text-xl font-bold text-green-700">{verdictStats.highRisk}</div>
                    <div className="text-[11px] text-green-600">高相似度商品</div>
                  </div>
                  <div className="rounded-lg border bg-amber-50/70 p-3 text-center shadow-sm dark:bg-amber-950/20">
                    <div className="text-xl font-bold text-amber-700">{verdictStats.mediumRisk}</div>
                    <div className="text-[11px] text-amber-600">中等相似度商品</div>
                  </div>
                  <div className="rounded-lg border bg-red-50/70 p-3 text-center shadow-sm dark:bg-red-950/20">
                    <div className="text-xl font-bold text-red-700">{verdictStats.lowRisk}</div>
                    <div className="text-[11px] text-red-600">低相似度商品</div>
                  </div>
                  <div className="rounded-lg border bg-slate-50/80 p-3 text-center shadow-sm dark:bg-slate-950/20">
                    <div className="text-xl font-bold text-slate-700 dark:text-slate-200">{verdictStats.clearLowRisk}</div>
                    <div className="text-[11px] text-slate-600 dark:text-slate-300">疑似不侵权</div>
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 商品比对结果列表 */}
        {products.length > 0 ? (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-semibold">商品列表</h2>
              <span className="text-xs text-muted-foreground">按商品总分排序</span>
            </div>
            <ResultsScoreTable products={sortedProducts} comparisons={comparisons} sessionId={sessionId || ''} />
          </div>
        ) : !isSessionFinished ? (
          <Card>
            <CardContent className="py-8 text-center space-y-4">
              <Loader2 className="h-10 w-10 text-primary mx-auto animate-spin" />
              <div>
                <p className="text-sm font-medium">分析仍在进行中</p>
                <p className="text-xs text-muted-foreground mt-1">
                  结果页会自动刷新，待后台完成后展示最新的商品和比对结论。
                </p>
              </div>
            </CardContent>
          </Card>
        ) : feishuUrl ? (
          /* 没有结构化商品数据，但有历史兼容飞书表格链接 */
          <Card>
            <CardContent className="py-8 text-center space-y-4">
              <FileSearch className="h-10 w-10 text-primary mx-auto" />
              <div>
                <p className="text-sm font-medium">仅找到历史兼容飞书链接</p>
                <p className="text-xs text-muted-foreground mt-1">
                  当前结构化结果暂不可用，可先打开飞书多维表格查看历史链路结果
                </p>
              </div>
              <a
                href={feishuUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                {feishuUrl}
              </a>
              {/* 飞书凭证提示 */}
              <Alert className="text-left mt-4 max-w-md mx-auto">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription className="text-xs">
                  <p className="font-medium mb-1">如何在此页面直接展示分析结果？</p>
                  <p>在 <code className="bg-muted px-1 rounded">.env.local</code> 中配置飞书 API 凭证：</p>
                  <pre className="mt-1 bg-muted p-2 rounded text-xs overflow-x-auto">{`FEISHU_APP_ID=your_app_id\nFEISHU_APP_SECRET=your_app_secret`}</pre>
                  <p className="mt-1">获取方式：登录 <a href="https://open.feishu.cn" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">飞书开放平台</a>，创建企业自建应用，添加&ldquo;多维表格&rdquo;读写权限。</p>
                </AlertDescription>
              </Alert>
              {/* 飞书凭证输入组件 */}
              <FeishuConfig
                feishuUrl={feishuUrl}
                onResultsLoaded={(data) => {
                  setImportedProducts(data.products);
                  setImportedComparisons(data.comparisons);
                }}
              />
              {/* 模块异常信息 */}
              {(session.results?.module2Exception || session.results?.module3Exception || session.results?.module4Exception) && (
                <div className="text-xs text-muted-foreground space-y-1 max-w-md mx-auto text-left mt-4 pt-3 border-t">
                  <p className="font-medium mb-1">模块运行状态：</p>
                  {session.results.module2Exception && session.results.module2Exception !== 'SUCCESS' && (
                    <p>模块2（关键词生成）：{session.results.module2Exception}</p>
                  )}
                  {session.results.module3Exception && session.results.module3Exception !== 'SUCCESS' && (
                    <p>模块3（商品检索）：{session.results.module3Exception}</p>
                  )}
                  {session.results.module4Exception && session.results.module4Exception !== 'SUCCESS' && (
                    <p>模块4（特征比对）：{session.results.module4Exception}</p>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        ) : (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              未检索到相关商品。这可能是因为关键词不够精确，或市场上暂无匹配商品。
            </AlertDescription>
          </Alert>
        )}
      </main>
    </div>
  );
}
