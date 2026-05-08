'use client';

// ============================================================
// 页面3：商品权利要求-特征比对详情
// 展示单个商品的 Claim Chart 级别比对表
// ============================================================

import { Suspense, useEffect, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import type { AnalysisSession } from '@/lib/types';
import { VERDICT_CONFIG } from '@/lib/types';
import { ClaimChartTable } from '@/components/claim-chart-table';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  ArrowLeft,
  ExternalLink,
  Loader2,
  AlertCircle,
  ShoppingCart,
  FileText,
} from 'lucide-react';
import Link from 'next/link';

export default function ProductDetailPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-background">
          <div className="text-center space-y-3">
            <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto" />
            <p className="text-sm text-muted-foreground">加载中...</p>
          </div>
        </div>
      }
    >
      <ProductDetailContent />
    </Suspense>
  );
}

function ProductDetailContent() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();

  const sessionId = searchParams.get('session') || '';
  const productId = params.productId as string;

  const [session, setSession] = useState<AnalysisSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setError('缺少会话 ID');
      setLoading(false);
      return;
    }

    const fetchResults = async () => {
      try {
        const response = await fetch(`/api/analysis/${sessionId}`);
        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
          throw new Error('服务端返回非 JSON 响应，请检查服务是否正常');
        }
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error((data as Record<string, string>).error || '获取分析结果失败');
        }
        const data = await response.json();
        setSession(data.session);
      } catch (err) {
        setError(err instanceof Error ? err.message : '获取结果时出错');
      } finally {
        setLoading(false);
      }
    };

    fetchResults();
  }, [sessionId]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center space-y-3">
          <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto" />
          <p className="text-sm text-muted-foreground">加载比对详情...</p>
        </div>
      </div>
    );
  }

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

  const products = session.results?.products || [];
  const comparisons = session.results?.comparisons || [];
  const product = products.find((p) => p.id === productId);
  const comparison = comparisons.find((c) => c.productId === productId);
  const feishuUrl = session.results?.feishuUrl;
  const patentTitle = session.results?.patent?.title || '专利文档';

  if (!product) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="max-w-md text-center space-y-4">
          <AlertCircle className="h-10 w-10 text-amber-500 mx-auto" />
          <p className="text-sm text-muted-foreground">未找到该商品信息</p>
          <Link href={`/results?session=${sessionId}`}>
            <Button variant="outline">返回结果列表</Button>
          </Link>
        </div>
      </div>
    );
  }

  function getProductVerdict() {
    if (!comparison || comparison.claimElements.length === 0) return 'uncertain';
    
    const claimGroups = new Map<string, typeof comparison.claimElements>();
    for (const el of comparison.claimElements) {
      const claimId = el.patentReference || 'unknown';
      if (!claimGroups.has(claimId)) claimGroups.set(claimId, []);
      claimGroups.get(claimId)!.push(el);
    }
    
    let hasAnyClaimAllMatching = false;
    let allClaimsHaveNotMatching = true;
    
    for (const [, claimElements] of claimGroups) {
      const hasNotMatching = claimElements.some(e => e.status === 'not_matching');
      const allMatching = claimElements.every(e => e.status === 'matching');
      if (allMatching) hasAnyClaimAllMatching = true;
      if (!hasNotMatching) allClaimsHaveNotMatching = false;
    }
    
    if (hasAnyClaimAllMatching) return 'infringement_likely';
    if (allClaimsHaveNotMatching) return 'no_infringement';
    return 'uncertain';
  }

  const productVerdict = getProductVerdict();
  const productVerdictConfig = VERDICT_CONFIG[productVerdict];

  const independentClaims = session.results?.patent?.independentClaims || [];

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-background/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => router.push(`/results?session=${sessionId}`)}
              className="gap-1.5"
            >
              <ArrowLeft className="h-4 w-4" />
              返回列表
            </Button>
            <Separator orientation="vertical" className="h-5" />
            <span className="text-sm font-medium truncate max-w-[300px]">{product.name}</span>
          </div>
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
      </header>

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-6">
        {/* 商品导航 */}
        <div className="rounded-lg border bg-background p-3">
          <div className="text-xs text-muted-foreground mb-2">切换商品:</div>
          <div className="flex gap-1.5 flex-wrap">
            {products.map((p) => (
              <Link
                key={p.id}
                href={`/results/${p.id}?session=${sessionId}`}
                className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${
                  p.id === productId
                    ? 'bg-primary text-primary-foreground border-primary'
                    : 'bg-background hover:bg-muted border-border'
                }`}
              >
                {p.name.slice(0, 15)}{p.name.length > 15 ? '...' : ''}
              </Link>
            ))}
          </div>
        </div>

        {/* 商品信息 + 侵权判定 */}
        <Card>
          <CardContent className="p-6">
            <div className="flex gap-5">
              <div className="h-28 w-28 shrink-0 rounded-lg bg-muted flex items-center justify-center overflow-hidden">
                {product.imageUrl ? (
                  <img src={product.imageUrl} alt={product.name} className="h-full w-full object-cover" />
                ) : (
                  <ShoppingCart className="h-10 w-10 text-muted-foreground" />
                )}
              </div>
              <div className="flex-1 min-w-0 space-y-3">
                <div>
                  <h2 className="text-lg font-bold">{product.name}</h2>
                  {comparison && (
                    <Badge
                      variant="outline"
                      className={`${productVerdictConfig.bgColor} ${productVerdictConfig.color} border text-xs mt-1`}
                    >
                      {productVerdictConfig.label}
                    </Badge>
                  )}
                </div>
                {product.description && (
                  <p className="text-sm text-muted-foreground line-clamp-3">{product.description}</p>
                )}
                <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
                  {product.source && <span>来源: {product.source}</span>}
                  {product.price && <span>价格: {product.price}</span>}
                  {product.url && (
                    <a href={product.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-primary hover:underline">
                      <ExternalLink className="h-3 w-3" />
                      原始链接
                    </a>
                  )}
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* 独立权利要求 */}
        {independentClaims.length > 0 && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileText className="h-4 w-4" />
                独立权利要求
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {independentClaims.map((claim, index) => (
                  <div key={index} className="rounded-lg border bg-muted/30 p-4">
                    <div className="text-xs font-semibold text-muted-foreground mb-1">权利要求 {index + 1}</div>
                    <p className="text-sm leading-relaxed whitespace-pre-wrap">{claim}</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* 比对规则说明 */}
        {comparison?.ruleApplied && (
          <Alert>
            <AlertDescription className="text-xs">
              <strong>适用规则：</strong>{comparison.ruleApplied}
            </AlertDescription>
          </Alert>
        )}

        {/* Claim Chart 比对表 */}
        {comparison && comparison.claimElements.length > 0 ? (
          <ClaimChartTable
            claimElements={comparison.claimElements}
            patentTitle={patentTitle}
            productName={product.name}
          />
        ) : (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              暂无比对数据。该商品可能未完成特征比对分析。
            </AlertDescription>
          </Alert>
        )}
      </main>
    </div>
  );
}
