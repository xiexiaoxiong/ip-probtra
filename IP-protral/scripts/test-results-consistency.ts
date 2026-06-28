/**
 * Portal 结果列表/详情/报告导出数据一致性测试
 *
 * 用法: pnpm exec tsx scripts/test-results-consistency.ts
 */

import assert from 'node:assert/strict';
import {
  buildComparisonMap,
  buildProductResultSnapshot,
  findComparison,
  findProduct,
  sortProductsByComparisonScore,
} from '@/lib/results-consistency';
import type { ProductComparison, ProductInfo } from '@/lib/types';

const products: ProductInfo[] = [
  { id: 'p-low', name: '低分商品', source: 'mock' },
  { id: 'p-high', name: '高分商品', source: 'mock' },
  { id: 'p-name-only', name: '名称回退商品', source: 'mock' },
];

const comparisons: ProductComparison[] = [
  {
    productId: 'p-high',
    productName: '高分商品',
    productSimilarityScore: 88,
    productScoreBand: 'high_similarity',
    riskLevel: 'high_risk',
    claimElements: [
      {
        featureId: '1A',
        claimElement: '技术特征A',
        productFeature: '商品公开A',
        similarityScore: 40,
        scoreBand: 'high_similarity',
        reasoning: 'mock',
        patentReference: '1',
      },
      {
        featureId: '1B',
        claimElement: '技术特征B',
        productFeature: '商品公开B',
        similarityScore: 48,
        scoreBand: 'high_similarity',
        reasoning: 'mock',
        patentReference: '1',
      },
    ],
    claimScores: [
      { claimId: '1', similarityScore: 88, scoreBand: 'high_similarity' },
    ],
  },
  {
    productId: 'p-low',
    productName: '低分商品',
    productSimilarityScore: 15,
    productScoreBand: 'low_similarity',
    riskLevel: 'low_risk',
    claimElements: [],
    claimScores: [
      { claimId: '1', similarityScore: 15, scoreBand: 'low_similarity' },
    ],
  },
  {
    productId: 'legacy-id-not-in-products',
    productName: '名称回退商品',
    productSimilarityScore: 66,
    productScoreBand: 'medium_similarity',
    riskLevel: 'medium_risk',
    claimElements: [
      {
        featureId: '2A',
        claimElement: '技术特征C',
        productFeature: '商品公开C',
        similarityScore: 66,
        scoreBand: 'medium_similarity',
        reasoning: 'mock',
        patentReference: '2',
      },
    ],
    claimScores: [
      { claimId: '2', similarityScore: 66, scoreBand: 'medium_similarity' },
    ],
  },
];

const comparisonMap = buildComparisonMap(comparisons);
assert.equal(comparisonMap.get('p-high')?.productSimilarityScore, 88);
assert.equal(comparisonMap.get('名称回退商品')?.productSimilarityScore, 66);

const sorted = sortProductsByComparisonScore(products, comparisons);
assert.deepEqual(sorted.map((item) => item.id), ['p-high', 'p-name-only', 'p-low']);

const detailProduct = findProduct(products, 'p-name-only');
assert.equal(detailProduct?.name, '名称回退商品');
assert.equal(findComparison(comparisons, 'p-name-only', detailProduct)?.productSimilarityScore, 66);

const highSnapshot = buildProductResultSnapshot(products[1], comparisons);
assert.equal(highSnapshot.product.id, 'p-high');
assert.equal(highSnapshot.score, 88);
assert.equal(highSnapshot.featureCount, 2);
assert.equal(highSnapshot.claimSubtotal, 88);

const fallbackSnapshot = buildProductResultSnapshot(products[2], comparisons);
assert.equal(fallbackSnapshot.score, 66);
assert.equal(fallbackSnapshot.featureCount, 1);
assert.equal(fallbackSnapshot.claimSubtotal, 66);

console.log('results consistency tests passed');
