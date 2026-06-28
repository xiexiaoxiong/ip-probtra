'use client';

import Link from 'next/link';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { scoreBgClass, scoreColorClass } from '@/lib/claim-score';
import type { ProductComparison, ProductInfo } from '@/lib/types';

interface ResultsScoreTableProps {
  products: ProductInfo[];
  comparisons: ProductComparison[];
  sessionId: string;
}

export function ResultsScoreTable({ products, comparisons, sessionId }: ResultsScoreTableProps) {
  const comparisonMap = new Map(comparisons.map((item) => [item.productId, item]));
  const sortedProducts = [...products].sort((a, b) => {
    const scoreA = comparisonMap.get(a.id)?.productSimilarityScore ?? -1;
    const scoreB = comparisonMap.get(b.id)?.productSimilarityScore ?? -1;
    return scoreB - scoreA;
  });

  return (
    <div className="rounded-lg border overflow-hidden bg-background">
      <Table>
        <TableHeader>
          <TableRow className="bg-muted/40">
            <TableHead>Product</TableHead>
            <TableHead>品牌</TableHead>
            <TableHead className="w-[110px]">Score</TableHead>
            <TableHead className="w-[120px]">商品图片</TableHead>
            <TableHead>Feature</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortedProducts.map((product, index) => {
            const comparison = comparisonMap.get(product.id);
            const score = comparison?.productSimilarityScore ?? 0;
            return (
              <TableRow key={product.id}>
                <TableCell>
                  <div className="space-y-1">
                    <div className="text-[11px] text-muted-foreground">#{index + 1}</div>
                    <Link href={`/results/${encodeURIComponent(product.id)}?session=${encodeURIComponent(sessionId)}`} className="font-medium hover:underline">
                      {product.name}
                    </Link>
                  </div>
                  {product.source && (
                    <div className="text-xs text-muted-foreground mt-1">{product.source}</div>
                  )}
                </TableCell>
                <TableCell>{product.brand || '—'}</TableCell>
                <TableCell>
                  <span className={`font-semibold ${scoreColorClass(comparison?.productSimilarityScore ?? 0, comparison?.productScoreBand)}`}>
                    {score.toFixed(2)}
                  </span>
                </TableCell>
                <TableCell>
                  <div className="h-14 w-14 overflow-hidden rounded-md border bg-muted flex items-center justify-center">
                    {product.imageUrl ? (
                      <img src={product.imageUrl} alt={product.name} className="h-full w-full object-cover" />
                    ) : (
                      <span className="text-[11px] text-muted-foreground">暂无图片</span>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap gap-1">
                    {(comparison?.claimElements || []).map((element) => (
                      <span
                        key={`${product.id}-${element.featureId}-${element.patentReference}`}
                        className={`inline-flex items-center justify-center min-w-6 h-6 px-1 rounded text-[11px] border ${scoreBgClass(element.similarityScore, element.scoreBand)} ${scoreColorClass(element.similarityScore, element.scoreBand)}`}
                        title={`${element.featureId || '特征'}: ${element.similarityScore.toFixed(2)} 分 / ${element.scoreBand}`}
                      >
                        {element.featureId || '?'}
                      </span>
                    ))}
                    {(!comparison || comparison.claimElements.length === 0) && (
                      <span className="text-xs text-muted-foreground">无元素数据</span>
                    )}
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
