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
import type { ProductInfo } from '@/lib/types';

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

  // 获取商品对应的比对结果
  const getComparison = (productId: string): ProductComparison | undefined =>
    comparisons.find((c) => c.productId === productId);

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

  // 统计 - 基于独立权利要求级比对结果
  // 1. 疑似侵权：存在至少一个独立权利要求，其所有特征都相同或等同
  // 2. 疑似不侵权：所有独立权利要求都有至少一个特征不相同
  // 3. 需要进一步分析：没有权利要求全相同，且至少有一个权利要求没有不相同特征
  const verdictStats = {
    infringementLikely: 0,
    needsReview: 0,
    noInfringement: 0,
  };

  // 按 claim_id 分组特征
  function groupByClaimId(elements: ProductComparison['claimElements']) {
    const map = new Map<string, ProductComparison['claimElements']>();
    for (const el of elements) {
      const claimId = el.patentReference || 'unknown';
      if (!map.has(claimId)) map.set(claimId, []);
      map.get(claimId)!.push(el);
    }
    return map;
  }

  for (const comp of comparisons) {
    const elements = comp.claimElements || [];
    if (elements.length === 0) continue;
    
    const claimGroups = groupByClaimId(elements);
    
    let hasAnyClaimAllMatching = false;
    let hasAnyClaimNoNotMatching = false;
    let allClaimsHaveNotMatching = true;
    
    for (const [, claimElements] of claimGroups) {
      const hasNotMatching = claimElements.some(e => e.status === 'not_matching');
      const allMatching = claimElements.every(e => e.status === 'matching');
      
      if (allMatching) hasAnyClaimAllMatching = true;
      if (!hasNotMatching) hasAnyClaimNoNotMatching = true;
      if (!hasNotMatching) allClaimsHaveNotMatching = false;
    }
    
    if (hasAnyClaimAllMatching) {
      verdictStats.infringementLikely++;
    } else if (allClaimsHaveNotMatching) {
      verdictStats.noInfringement++;
    } else if (hasAnyClaimNoNotMatching) {
      verdictStats.needsReview++;
    } else {
      verdictStats.needsReview++;
    }
  }

  return (
    <div className="min-h-screen bg-background">
      {/* 顶部导航 */}
      <header className="border-b bg-background/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
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
            <Separator orientation="vertical" className="h-5" />
            <div className="flex items-center gap-2">
              <Shield className="h-4 w-4 text-primary" />
              <span className="text-sm font-medium">侵权分析结果</span>
            </div>
            {sessionId && (
              <Badge variant="outline" className="font-mono text-[11px]">
                {sessionId}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-2">
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

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        {exportError && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{exportError}</AlertDescription>
          </Alert>
        )}
        {/* 分析概要 */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <FileSearch className="h-4 w-4" />
              分析概要
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {session.results?.patent?.title && (
                <div>
                  <span className="text-xs text-muted-foreground">专利标题</span>
                  <p className="text-sm font-medium">{session.results.patent.title}</p>
                </div>
              )}
              {session.results?.patent?.patentNumber && (
                <div>
                  <span className="text-xs text-muted-foreground">专利号</span>
                  <p className="text-sm font-medium">{session.results.patent.patentNumber}</p>
                </div>
              )}

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
                <div className="grid grid-cols-4 gap-3 mt-2">
                  <div className="rounded-lg border bg-muted/30 p-3 text-center">
                    <div className="text-xl font-bold">{products.length}</div>
                    <div className="text-[11px] text-muted-foreground">检索商品总数</div>
                  </div>
                  <div className="rounded-lg border bg-red-50/50 p-3 text-center dark:bg-red-950/20">
                    <div className="text-xl font-bold text-red-700">{verdictStats.infringementLikely}</div>
                    <div className="text-[11px] text-red-600">疑似侵权商品</div>
                  </div>
                  <div className="rounded-lg border bg-amber-50/50 p-3 text-center dark:bg-amber-950/20">
                    <div className="text-xl font-bold text-amber-700">{verdictStats.needsReview}</div>
                    <div className="text-[11px] text-amber-600">需要进一步分析</div>
                  </div>
                  <div className="rounded-lg border bg-green-50/50 p-3 text-center dark:bg-green-950/20">
                    <div className="text-xl font-bold text-green-700">{verdictStats.noInfringement}</div>
                    <div className="text-[11px] text-green-600">疑似不侵权商品</div>
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 商品比对结果列表 */}
        {products.length > 0 ? (
          <div className="space-y-4">
            <h2 className="text-lg font-semibold">商品列表</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {products.map((product) => {
                const comparison = getComparison(product.id);
                
                // 计算该商品的侵权判定结果（基于权利要求级逻辑）
                let productVerdict: 'infringement_likely' | 'needs_review' | 'no_infringement' = 'needs_review';
                if (comparison && comparison.claimElements.length > 0) {
                  const claimGroups = groupByClaimId(comparison.claimElements);
                  let hasAnyClaimAllMatching = false;
                  let allClaimsHaveNotMatching = true;
                  
                  for (const [, claimElements] of claimGroups) {
                    const hasNotMatching = claimElements.some(e => e.status === 'not_matching');
                    const allMatching = claimElements.every(e => e.status === 'matching');
                    if (allMatching) hasAnyClaimAllMatching = true;
                    if (!hasNotMatching) allClaimsHaveNotMatching = false;
                  }
                  
                  if (hasAnyClaimAllMatching) productVerdict = 'infringement_likely';
                  else if (allClaimsHaveNotMatching) productVerdict = 'no_infringement';
                  else productVerdict = 'needs_review';
                }

                const verdictLabels = {
                  infringement_likely: { label: '疑似侵权', color: 'text-red-700', bg: 'bg-red-50 border-red-200' },
                  needs_review: { label: '需进一步分析', color: 'text-amber-700', bg: 'bg-amber-50 border-amber-200' },
                  no_infringement: { label: '疑似不侵权', color: 'text-green-700', bg: 'bg-green-50 border-green-200' },
                };
                const vConfig = verdictLabels[productVerdict];

                return (
                  <Link
                    key={product.id}
                    href={`/results/${product.id}?session=${sessionId}`}
                    className="block rounded-lg border bg-background hover:shadow-md transition-shadow overflow-hidden"
                  >
                    <div className="p-4 flex gap-3">
                      <div className="h-20 w-20 shrink-0 rounded-lg bg-muted flex items-center justify-center overflow-hidden">
                        {product.imageUrl ? (
                          <img src={product.imageUrl} alt={product.name} className="h-full w-full object-cover" />
                        ) : (
                          <span className="text-2xl text-muted-foreground">📦</span>
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <h3 className="text-sm font-semibold line-clamp-2 mb-1.5">{product.name}</h3>
                        <Badge variant="outline" className={`${vConfig.bg} ${vConfig.color} border text-xs`}>
                          {vConfig.label}
                        </Badge>
                      </div>
                    </div>
                  </Link>
                );
              })}
            </div>
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
          /* 没有结构化商品数据，但有飞书表格链接 */
          <Card>
            <CardContent className="py-8 text-center space-y-4">
              <FileSearch className="h-10 w-10 text-primary mx-auto" />
              <div>
                <p className="text-sm font-medium">分析结果已写入飞书多维表格</p>
                <p className="text-xs text-muted-foreground mt-1">
                  工作流将分析数据直接写入飞书表格，请点击下方链接查看完整结果
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
