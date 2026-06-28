import type { ProductComparison, ProductInfo } from './types';

export interface ProductResultSnapshot {
  product: ProductInfo;
  comparison?: ProductComparison;
  score: number;
  featureCount: number;
  claimSubtotal: number;
}

export function buildComparisonMap(comparisons: ProductComparison[]): Map<string, ProductComparison> {
  const map = new Map<string, ProductComparison>();
  for (const comparison of comparisons) {
    map.set(String(comparison.productId), comparison);
    if (comparison.productName && !map.has(comparison.productName)) {
      map.set(comparison.productName, comparison);
    }
  }
  return map;
}

export function findProduct(products: ProductInfo[], productId: string): ProductInfo | undefined {
  return products.find((product) => String(product.id) === productId);
}

export function findComparison(
  comparisons: ProductComparison[],
  productId: string,
  product?: ProductInfo,
): ProductComparison | undefined {
  const comparisonMap = buildComparisonMap(comparisons);
  return comparisonMap.get(String(productId))
    || (product ? comparisonMap.get(product.name) : undefined);
}

export function sortProductsByComparisonScore(
  products: ProductInfo[],
  comparisons: ProductComparison[],
): ProductInfo[] {
  const comparisonMap = buildComparisonMap(comparisons);
  return [...products].sort((a, b) => {
    const scoreA = comparisonMap.get(String(a.id))?.productSimilarityScore
      ?? comparisonMap.get(a.name)?.productSimilarityScore
      ?? -1;
    const scoreB = comparisonMap.get(String(b.id))?.productSimilarityScore
      ?? comparisonMap.get(b.name)?.productSimilarityScore
      ?? -1;
    return scoreB - scoreA;
  });
}

export function buildProductResultSnapshot(
  product: ProductInfo,
  comparisons: ProductComparison[],
): ProductResultSnapshot {
  const comparison = findComparison(comparisons, product.id, product);
  return {
    product,
    comparison,
    score: comparison?.productSimilarityScore ?? 0,
    featureCount: comparison?.claimElements.length ?? 0,
    claimSubtotal: comparison?.claimScores.reduce((sum, item) => sum + item.similarityScore, 0) ?? 0,
  };
}
