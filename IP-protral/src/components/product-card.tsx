'use client';

// ============================================================
// 商品卡片组件
// 用于结果汇总页面，展示单个商品的侵权概要
// ============================================================

import type { ProductInfo, ProductComparison, ProductRiskLevel } from '@/lib/types';
import { PRODUCT_RISK_CONFIG } from '@/lib/types';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ExternalLink, ChevronRight } from 'lucide-react';
import Link from 'next/link';

interface ProductCardProps {
  product: ProductInfo;
  comparison?: ProductComparison;
  sessionId: string;
}

export function ProductCard({ product, comparison, sessionId }: ProductCardProps) {
  const verdict: ProductRiskLevel = comparison?.riskLevel || 'low_risk';
  const config = PRODUCT_RISK_CONFIG[verdict];
  const exactMatchCount = comparison?.claimElements.filter((e) => e.similarityScore === 100).length || 0;
  const totalElements = comparison?.claimElements.length || 0;
  const topClaimScore = comparison?.claimScores.reduce((max, item) => Math.max(max, item.similarityScore), 0) || 0;

  return (
    <Link href={`/results/${product.id}?session=${sessionId}`}>
      <Card className="group cursor-pointer transition-all hover:shadow-md hover:border-primary/30">
        <CardContent className="p-5">
          <div className="flex gap-4">
            {/* 商品图片 */}
            <div className="h-20 w-20 shrink-0 rounded-lg bg-muted flex items-center justify-center overflow-hidden">
              {product.imageUrl ? (
                <img
                  src={product.imageUrl}
                  alt={product.name}
                  className="h-full w-full object-cover"
                />
              ) : (
                <span className="text-2xl text-muted-foreground">📦</span>
              )}
            </div>

            {/* 商品信息 */}
            <div className="flex-1 min-w-0">
              <div className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-semibold truncate group-hover:text-primary transition-colors">
                  {product.name}
                </h3>
                <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5 group-hover:text-primary transition-colors" />
              </div>

              {/* 侵权判定标签 */}
              <div className="mt-2">
                <Badge variant="outline" className={`${config.bgColor} ${config.color} border text-xs`}>
                  {config.label}
                </Badge>
              </div>

              {/* 比对概要 */}
              {comparison && totalElements > 0 && (
                <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                  <span>权利要求要素: {totalElements}</span>
                  <span>商品总分: {comparison.productSimilarityScore}</span>
                  <span>最高权利要求分: {topClaimScore}</span>
                  <span>100 分特征: {exactMatchCount}</span>
                </div>
              )}

              {/* 来源和价格 */}
              <div className="mt-2 flex items-center gap-3 text-xs text-muted-foreground">
                {product.source && <span>来源: {product.source}</span>}
                {product.price && <span>价格: {product.price}</span>}
                {product.url && (
                  <a
                    href={product.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="inline-flex items-center gap-0.5 text-primary hover:underline"
                  >
                    <ExternalLink className="h-3 w-3" />
                    原链接
                  </a>
                )}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
