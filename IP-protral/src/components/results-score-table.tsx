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
import { buildComparisonMap, sortProductsByComparisonScore } from '@/lib/results-consistency';
import type { ProductComparison, ProductInfo } from '@/lib/types';

interface ResultsScoreTableProps {
  products: ProductInfo[];
  comparisons: ProductComparison[];
  sessionId: string;
}

export function ResultsScoreTable({ products, comparisons, sessionId }: ResultsScoreTableProps) {
  const comparisonMap = buildComparisonMap(comparisons);
  const sortedProducts = sortProductsByComparisonScore(products, comparisons);

  return (
    <div className="w-full max-w-full overflow-hidden rounded-lg border bg-background">
      <Table className="w-full table-fixed">
        <colgroup>
          <col style={{ width: '38%' }} />
          <col style={{ width: '14%' }} />
          <col style={{ width: '10%' }} />
          <col style={{ width: '12%' }} />
          <col style={{ width: '26%' }} />
        </colgroup>
        <TableHeader>
          <TableRow className="bg-muted/40">
            <TableHead
              className="whitespace-normal break-words align-top"
              style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
            >
              Product
            </TableHead>
            <TableHead
              className="whitespace-normal break-words align-top"
              style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
            >
              品牌
            </TableHead>
            <TableHead
              className="whitespace-normal break-words align-top"
              style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
            >
              Score
            </TableHead>
            <TableHead
              className="whitespace-normal break-words align-top"
              style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
            >
              商品图片
            </TableHead>
            <TableHead
              className="whitespace-normal break-words align-top"
              style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
            >
              Feature
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortedProducts.map((product, index) => {
            const comparison = comparisonMap.get(product.id);
            const score = comparison?.productSimilarityScore ?? 0;
            return (
              <TableRow key={product.id}>
                <TableCell
                  className="align-top whitespace-normal break-words min-w-0"
                  style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
                >
                  <div className="space-y-1 min-w-0">
                    <div className="text-[11px] text-muted-foreground">#{index + 1}</div>
                    <Link
                      href={`/results/${encodeURIComponent(product.id)}?session=${encodeURIComponent(sessionId)}`}
                      className="font-medium hover:underline block"
                      style={{ display: 'block', overflowWrap: 'anywhere', wordBreak: 'break-word', whiteSpace: 'normal' }}
                    >
                      {product.name}
                    </Link>
                  </div>
                  {product.source && (
                    <div
                      className="text-xs text-muted-foreground mt-1 break-all"
                      style={{ wordBreak: 'break-all' }}
                    >
                      {product.source}
                    </div>
                  )}
                </TableCell>
                <TableCell
                  className="align-top whitespace-normal break-words"
                  style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
                >
                  {product.brand || '—'}
                </TableCell>
                <TableCell
                  className="align-top whitespace-normal break-words"
                  style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
                >
                  <span className={`font-semibold ${scoreColorClass(comparison?.productSimilarityScore ?? 0, comparison?.productScoreBand)}`}>
                    {score.toFixed(2)}
                  </span>
                </TableCell>
                <TableCell
                  className="align-top whitespace-normal break-words"
                  style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
                >
                  <div className="flex aspect-square w-full max-w-14 items-center justify-center overflow-hidden rounded-md border bg-muted">
                    {product.imageUrl ? (
                      <img src={product.imageUrl} alt={product.name} className="h-full w-full object-cover" />
                    ) : (
                      <span className="text-[11px] text-muted-foreground">暂无图片</span>
                    )}
                  </div>
                </TableCell>
                <TableCell
                  className="align-top whitespace-normal break-words"
                  style={{ whiteSpace: 'normal', overflowWrap: 'anywhere', wordBreak: 'break-word' }}
                >
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
