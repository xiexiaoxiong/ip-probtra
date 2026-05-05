'use client';

// ============================================================
// 权利要求-特征比对表组件（Claim Chart）
// 展示专利独立权利要求与商品技术特征的一一比对
// ============================================================

import type { ClaimElementComparison } from '@/lib/types';
import { MATCH_CONFIG } from '@/lib/types';
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
  const stats = {
    matching: claimElements.filter((e) => e.status === 'matching').length,
    not_matching: claimElements.filter((e) => e.status === 'not_matching').length,
    uncertain: claimElements.filter((e) => e.status === 'uncertain').length,
    total: claimElements.length,
  };

  return (
    <div className="space-y-4">
      {/* 统计概要 */}
      <div className="grid grid-cols-3 gap-3">
        <div className="rounded-lg border bg-red-50/50 p-3 text-center dark:bg-red-950/20">
          <div className="text-2xl font-bold text-red-700">{stats.matching}</div>
          <div className="text-xs text-red-600">相同/等同</div>
        </div>
        <div className="rounded-lg border bg-amber-50/50 p-3 text-center dark:bg-amber-950/20">
          <div className="text-2xl font-bold text-amber-700">{stats.uncertain}</div>
          <div className="text-xs text-amber-600">不确定</div>
        </div>
        <div className="rounded-lg border bg-green-50/50 p-3 text-center dark:bg-green-950/20">
          <div className="text-2xl font-bold text-green-700">{stats.not_matching}</div>
          <div className="text-xs text-green-600">不相同</div>
        </div>
      </div>

      {/* 比对表 */}
      <div className="rounded-lg border overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/50">
              <TableHead className="w-[60px] text-center">特征</TableHead>
              <TableHead className="w-[25%]">
                <div className="flex items-center gap-1.5">
                  <FileText className="h-3.5 w-3.5" />
                  特征内容
                </div>
              </TableHead>
              <TableHead className="w-[80px] text-center">比对结论</TableHead>
              <TableHead className="w-[140px] text-center">
                <div className="flex items-center gap-1.5 justify-center">
                  <ImageIcon className="h-3.5 w-3.5" />
                  产品图片
                </div>
              </TableHead>
              <TableHead>比对分析</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {claimElements.map((element, index) => {
              const matchConfig = MATCH_CONFIG[element.status];
              const featureId = element.featureId || `${index + 1}`;
              const evidenceImages = element.evidenceImages || [];

              return (
                <TableRow key={index}>
                  <TableCell className="text-center align-top py-3">
                    <span className="text-sm font-mono font-medium">{featureId}</span>
                  </TableCell>
                  <TableCell className="align-top py-3">
                    <div className="text-sm whitespace-normal break-words">{element.claimElement}</div>
                  </TableCell>
                  <TableCell className="text-center align-top py-3">
                    <Badge
                      variant="outline"
                      className={`${matchConfig.bgColor} ${matchConfig.color} border text-xs whitespace-nowrap`}
                    >
                      {matchConfig.label}
                    </Badge>
                  </TableCell>
                  <TableCell className="align-top py-3">
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
                  <TableCell className="align-top py-3">
                    <div className="text-sm text-muted-foreground whitespace-normal break-words">
                      {element.reasoning || '—'}
                    </div>
                    {element.productFeature && element.productFeature !== '—' && (
                      <div className="mt-2 text-xs text-blue-600 dark:text-blue-400">
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
      <div className="rounded-lg bg-muted/30 p-4 text-xs text-muted-foreground space-y-1">
        <p className="font-semibold">比对说明</p>
        <ul className="list-disc pl-4 space-y-0.5">
          <li><strong>相同/等同</strong>：商品技术特征与专利权利要求要素在字面或等同意义上匹配</li>
          <li><strong>不相同</strong>：商品技术特征与专利权利要求要素存在实质差异</li>
          <li><strong>不确定</strong>：基于现有信息无法做出明确判断，需进一步人工审查</li>
        </ul>
        <p className="mt-2 text-muted-foreground/70">
          注意：本系统仅提供技术特征比对的事实标注，不构成法律结论。所有判断结果均可追溯至专利原文与商品原始描述。
        </p>
      </div>
    </div>
  );
}
