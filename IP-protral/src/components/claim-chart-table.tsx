'use client';

// ============================================================
// 权利要求-特征比对表组件（Claim Chart）
// 展示专利独立权利要求与商品技术特征的一一比对
// ============================================================

import type { ClaimElementComparison } from '@/lib/types';
import { SCORE_BAND_CONFIG, scoreToBand } from '@/lib/types';
import { ClaimTokenHighlight } from '@/components/claim-token-highlight';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Badge } from '@/components/ui/badge';
import { FileText, ImageIcon } from 'lucide-react';

interface ClaimChartTableProps {
  claimElements: ClaimElementComparison[];
  patentTitle?: string;
  productName?: string;
}

export function ClaimChartTable({ claimElements }: ClaimChartTableProps) {
  const normalizedElements = claimElements.map((element) => {
    const similarityScore = Number.isFinite(element.similarityScore) ? element.similarityScore : 0;
    const scoreBand = SCORE_BAND_CONFIG[element.scoreBand]
      ? element.scoreBand
      : scoreToBand(similarityScore, {
          zeroedByMismatch: element.scoreDetail?.zeroedByMismatch,
          matchedEffectiveLength: element.scoreDetail?.matchedEffectiveLength,
          totalEffectiveLength: element.scoreDetail?.effectiveLength,
        });
    return {
      ...element,
      similarityScore,
      scoreBand,
      evidenceImages: Array.isArray(element.evidenceImages)
        ? element.evidenceImages.filter((img): img is string => typeof img === 'string' && img.trim().length > 0)
        : [],
      tokenUnits: Array.isArray(element.tokenUnits) ? element.tokenUnits : [],
    };
  });

  return (
    <div className="space-y-4">
      {/* 比对表 */}
      <div className="rounded-lg border bg-background shadow-sm overflow-hidden">
        <Table className="min-w-[1180px] table-fixed">
          <colgroup>
            <col className="w-[6%]" />
            <col className="w-[38%]" />
            <col className="w-[9%]" />
            <col className="w-[8%]" />
            <col className="w-[12%]" />
            <col className="w-[27%]" />
          </colgroup>
          <TableHeader>
            <TableRow className="bg-muted/60 hover:bg-muted/60">
              <TableHead className="text-center">特征</TableHead>
              <TableHead>
                <div className="flex items-center gap-1.5">
                  <FileText className="h-3.5 w-3.5" />
                  特征内容
                </div>
              </TableHead>
              <TableHead className="w-[80px] text-center">状态</TableHead>
              <TableHead className="w-[120px] text-center">得分</TableHead>
              <TableHead className="text-center">
                <div className="flex items-center gap-1.5 justify-center">
                  <ImageIcon className="h-3.5 w-3.5" />
                  产品图片
                </div>
              </TableHead>
              <TableHead>比对分析</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {normalizedElements.map((element, index) => {
              const matchConfig = SCORE_BAND_CONFIG[element.scoreBand];
              const featureId = element.featureId || `${index + 1}`;
              const evidenceImages = element.evidenceImages || [];

              return (
                <TableRow key={index} className="hover:bg-muted/30">
                  <TableCell className="text-center align-top whitespace-normal px-3 py-4">
                    <span className="inline-flex h-7 min-w-8 items-center justify-center rounded-md bg-muted px-2 text-xs font-mono font-semibold text-foreground">
                      {featureId}
                    </span>
                  </TableCell>
                  <TableCell className="align-top whitespace-normal px-4 py-4">
                    <div className="text-sm leading-7 text-foreground [overflow-wrap:anywhere]">
                      <ClaimTokenHighlight
                        text={element.claimElement}
                        tokenUnits={element.tokenUnits}
                        scoreBand={element.scoreBand}
                        zeroedByMismatch={element.scoreDetail?.zeroedByMismatch}
                      />
                    </div>
                  </TableCell>
                  <TableCell className="text-center align-top whitespace-normal px-2 py-4">
                    <Badge
                      variant="outline"
                      className={`${matchConfig.bgColor} ${matchConfig.color} border text-xs whitespace-nowrap`}
                    >
                      {matchConfig.label}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-center align-top whitespace-normal px-2 py-4">
                    <div className="text-sm font-semibold">{element.similarityScore.toFixed(2)}</div>
                  </TableCell>
                  <TableCell className="align-top whitespace-normal px-2 py-4">
                    {evidenceImages.length > 0 ? (
                      <div className="flex gap-1 flex-wrap justify-center">
                        {evidenceImages.slice(0, 2).map((img, imgIndex) => (
                          <img
                            key={imgIndex}
                            src={img}
                            alt="证据图片"
                            className="w-14 h-14 object-cover rounded border"
                          />
                        ))}
                        {evidenceImages.length > 2 && (
                          <span className="text-xs text-muted-foreground self-center">+{evidenceImages.length - 2}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">图片中无法体现</span>
                    )}
                  </TableCell>
                  <TableCell className="align-top whitespace-normal px-4 py-4">
                    <div className="text-sm leading-6 text-muted-foreground whitespace-pre-wrap [overflow-wrap:anywhere]">
                      {element.reasoning || '—'}
                    </div>
                    {element.productFeature && element.productFeature !== '—' && (
                      <div className="mt-3 rounded-md bg-blue-50/70 px-3 py-2 text-xs leading-5 text-blue-700 [overflow-wrap:anywhere] dark:bg-blue-950/20 dark:text-blue-300">
                        <span className="font-medium">商品特征：</span>
                        {element.productFeature}
                      </div>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* 底部说明 */}
      <div className="rounded-lg border bg-muted/20 p-4 text-xs text-muted-foreground space-y-1">
        <p className="font-semibold">比对说明</p>
        <ul className="list-disc pl-4 space-y-0.5">
          <li><strong>绿色底线</strong>：该最小单元已被明确确认相同</li>
          <li><strong>黄色底线</strong>：该最小单元仍待确认</li>
          <li><strong>灰色底线</strong>：该最小单元已被明确确认不相同</li>
          <li><strong>得分规则</strong>：特征得分按本特征命中比例计算，商品总分按特征占比加权累计；若存在明确不相同，商品总分直接归零</li>
        </ul>
        <p className="mt-2 text-muted-foreground/70">
          注意：本系统仅提供技术特征比对的事实标注，不构成法律结论。所有判断结果均可追溯至专利原文与商品原始描述。
        </p>
      </div>
    </div>
  );
}
