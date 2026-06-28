/**
 * 模块3异步检索 + 模块4首轮/终轮补跑状态测试
 *
 * 用法: pnpm exec tsx scripts/test-async-module3-module4.ts
 */

import assert from 'node:assert/strict';
import {
  buildFinalModule4Results,
  buildInitialModule4PartialResults,
  isModule3Terminal,
  shouldTriggerInitialModule4,
} from '@/lib/async-analysis-state';
import type { ProductComparison, ProductInfo } from '@/lib/types';

function sampleProducts(): ProductInfo[] {
  return [
    { id: 'p1', name: '部分检索商品' },
    { id: 'p2', name: '终轮新增商品' },
  ];
}

function sampleComparisons(productId: string, score: number): ProductComparison[] {
  return [
    {
      productId,
      productName: productId === 'p1' ? '部分检索商品' : '终轮新增商品',
      productSimilarityScore: score,
      productScoreBand: score >= 80 ? 'high_similarity' : 'medium_similarity',
      riskLevel: score >= 80 ? 'high_risk' : 'medium_risk',
      claimElements: [],
      claimScores: [
        {
          claimId: 'claim-1',
          similarityScore: score,
          scoreBand: score >= 80 ? 'high_similarity' : 'medium_similarity',
        },
      ],
    },
  ];
}

function testModule3TriggerDecision(): void {
  assert.equal(
    shouldTriggerInitialModule4({
      initialStep5Triggered: false,
      module3ProductsCount: 1,
      module3IsComplete: false,
    }),
    true,
    '有部分商品且模块3未完成时应触发首轮模块4',
  );
  assert.equal(
    shouldTriggerInitialModule4({
      initialStep5Triggered: true,
      module3ProductsCount: 2,
      module3IsComplete: false,
    }),
    false,
    '首轮模块4已触发后不应重复触发',
  );
  assert.equal(
    shouldTriggerInitialModule4({
      initialStep5Triggered: false,
      module3ProductsCount: 0,
      module3IsComplete: false,
    }),
    false,
    '没有商品时不应触发首轮模块4',
  );
  assert.equal(
    shouldTriggerInitialModule4({
      initialStep5Triggered: false,
      module3ProductsCount: 2,
      module3IsComplete: true,
    }),
    false,
    '模块3已完成时不走 partial 首轮分支',
  );

  for (const status of ['completed', 'error', 'cancelled', 'timeout']) {
    assert.equal(isModule3Terminal(status), true, `${status} 应是模块3终态`);
  }
  for (const status of ['queued', 'running', undefined]) {
    assert.equal(isModule3Terminal(status), false, `${String(status)} 不应是模块3终态`);
  }
}

function testPartialAndFinalResultPatches(): void {
  const [partialProduct, finalProduct] = sampleProducts();
  const partialComparisons = sampleComparisons(partialProduct.id, 70);
  const finalComparisons = [
    ...partialComparisons,
    ...sampleComparisons(finalProduct.id, 85),
  ];

  const partialPatch = buildInitialModule4PartialResults({
    products: [partialProduct],
    comparisons: partialComparisons,
    claimCompareRunId: 101,
    module4RunId: 'session-module4-initial',
    module4TaskStatus: 'completed',
    module4TaskStartedAt: '2026-06-28T03:00:00.000Z',
    module4TaskFinishedAt: '2026-06-28T03:01:00.000Z',
  });

  assert.equal(partialPatch.claimCompareRunId, 101);
  assert.equal(partialPatch.initialClaimCompareRunId, 101);
  assert.equal(partialPatch.finalClaimCompareRunId, undefined);
  assert.equal(partialPatch.resultsCompleteness, 'partial');
  assert.equal(partialPatch.step5Phase, 'initial');
  assert.equal(partialPatch.partialAnalysisAvailable, true);
  assert.equal(partialPatch.products?.length, 1);
  assert.equal(partialPatch.comparisons?.length, 1);

  const finalPatch = buildFinalModule4Results({
    products: [partialProduct, finalProduct],
    comparisons: finalComparisons,
    initialClaimCompareRunId: partialPatch.initialClaimCompareRunId,
    finalClaimCompareRunId: 202,
    module4RunId: 'session-module4-final',
    module4TaskStatus: 'completed',
    module4TaskStartedAt: '2026-06-28T03:02:00.000Z',
    module4TaskFinishedAt: '2026-06-28T03:05:00.000Z',
  });

  assert.equal(finalPatch.claimCompareRunId, 202);
  assert.equal(finalPatch.initialClaimCompareRunId, 101);
  assert.equal(finalPatch.finalClaimCompareRunId, 202);
  assert.equal(finalPatch.resultsCompleteness, 'final');
  assert.equal(finalPatch.step5Phase, 'completed');
  assert.equal(finalPatch.partialAnalysisAvailable, true);
  assert.equal(finalPatch.products?.length, 2);
  assert.equal(finalPatch.comparisons?.length, 2);
}

testModule3TriggerDecision();
testPartialAndFinalResultPatches();

console.log('async module3/module4 state tests passed');
