import type {
  ClaimElementComparison,
  ClaimScoreSummary,
  ClaimTokenStatus,
  ClaimTokenUnit,
  ProductComparison,
  ScoreBand,
} from './types';
import { scoreToBand, scoreToRiskLevel } from './types';

const STOP_WORDS = ['所述'];

export function normalizeClaimText(text: string): string {
  let cleaned = String(text || '');
  cleaned = cleaned.replace(/[\(\[（【]?\d+[\)\]）】]?/g, '');
  for (const word of STOP_WORDS) {
    cleaned = cleaned.replaceAll(word, '');
  }
  cleaned = cleaned.replace(/[^\w\u4e00-\u9fff]+/g, ' ');
  return cleaned.replace(/\s+/g, ' ').trim();
}

export function countEffectiveUnits(text: string): number {
  const normalized = normalizeClaimText(text);
  if (!normalized) return 0;
  const englishWords = normalized.match(/[A-Za-z]+(?:'[A-Za-z]+)?/g) || [];
  const remaining = normalized.replace(/[A-Za-z]+(?:'[A-Za-z]+)?/g, '');
  const chineseChars = remaining.match(/[\u4e00-\u9fff]/g) || [];
  return englishWords.length + chineseChars.length;
}

export function scoreColorClass(score: number, band?: ScoreBand): string {
  if (band === 'uncertain') return 'text-amber-700';
  if (band === 'exact_mismatch') return 'text-slate-500';
  if (score > 70) return 'text-green-700';
  if (score >= 30) return 'text-amber-700';
  return 'text-red-700';
}

export function scoreBgClass(score: number, band?: ScoreBand): string {
  if (band === 'uncertain') return 'bg-amber-50 border-amber-200';
  if (band === 'exact_mismatch') return 'bg-slate-100 border-slate-200';
  if (score > 70) return 'bg-green-50 border-green-200';
  if (score >= 30) return 'bg-amber-50 border-amber-200';
  return 'bg-red-50 border-red-200';
}

export function tokenStatusClass(status: ClaimTokenStatus): string {
  switch (status) {
    case 'match':
      return 'border-b-[3px] border-green-400 bg-green-100/50';
    case 'mismatch':
      return 'border-b-[3px] border-slate-400 bg-slate-100/70';
    default:
      return 'border-b-[3px] border-yellow-400 bg-yellow-100/60';
  }
}

export function buildFallbackTokenUnits(text: string): ClaimTokenUnit[] {
  const tokens = String(text || '').match(/[A-Za-z]+(?:'[A-Za-z]+)?|[\u4e00-\u9fff]+/g) || [];
  return tokens.map((token) => ({
    text: token,
    normalizedText: normalizeClaimText(token),
    status: 'uncertain',
    effectiveLength: countEffectiveUnits(token),
  }));
}

export function computeClaimScores(elements: ClaimElementComparison[]): ClaimScoreSummary[] {
  const claimMap = new Map<string, ClaimElementComparison[]>();
  for (const element of elements) {
    const claimId = element.patentReference || 'unknown';
    if (!claimMap.has(claimId)) claimMap.set(claimId, []);
    claimMap.get(claimId)!.push(element);
  }

  return Array.from(claimMap.entries()).map(([claimId, claimElements]) => {
    const zeroedByMismatch = claimElements.some((item) => item.scoreDetail?.zeroedByMismatch);
    const claimTotalEffectiveLength = claimElements.reduce((sum, item) => sum + (item.scoreDetail?.effectiveLength ?? 0), 0);
    const claimMatchedEffectiveLength = claimElements.reduce((sum, item) => {
      const effectiveLength = item.scoreDetail?.effectiveLength ?? 0;
      const matchedLength = item.scoreDetail?.matchedEffectiveLength ?? 0;
      return sum + Math.min(Math.max(matchedLength, 0), Math.max(effectiveLength, 0));
    }, 0);
    const similarityScore = zeroedByMismatch
      ? 0
      : Number(
          Math.min(
            claimElements.reduce((sum, item) => {
              const effectiveLength = item.scoreDetail?.effectiveLength ?? 0;
              const matchedLength = item.scoreDetail?.matchedEffectiveLength ?? 0;
              if (effectiveLength > 0 && claimTotalEffectiveLength > 0) {
                const cappedMatched = Math.min(Math.max(matchedLength, 0), effectiveLength);
                return sum + (cappedMatched / claimTotalEffectiveLength) * 100;
              }

              const fullScore = item.scoreDetail?.fullScore;
              const featureScore = item.similarityScore ?? 0;
              if (typeof fullScore === 'number' && Number.isFinite(fullScore)) {
                return sum + (featureScore * fullScore) / 100;
              }

              return sum + (item.scoreDetail?.awardedScore ?? featureScore ?? 0);
            }, 0),
            100,
          ).toFixed(2),
        );
    return {
      claimId,
      similarityScore,
      scoreBand: scoreToBand(similarityScore, {
        zeroedByMismatch,
        matchedEffectiveLength: claimMatchedEffectiveLength,
        totalEffectiveLength: claimTotalEffectiveLength,
      }),
      claimTotalEffectiveLength,
      claimMatchedEffectiveLength,
      zeroedByMismatch,
    };
  });
}

export function computeProductScore(claimScores: ClaimScoreSummary[]): Pick<ProductComparison, 'claimScores' | 'productSimilarityScore' | 'productScoreBand' | 'riskLevel' | 'highestScoringClaimId'> {
  const productZeroedByMismatch = claimScores.some((item) => item.zeroedByMismatch);
  const productSimilarityScore = productZeroedByMismatch
    ? 0
    : Number(Math.min(claimScores.reduce((sum, item) => sum + (item.similarityScore || 0), 0), 100).toFixed(2));
  const highestScoringClaimId = claimScores
    .slice()
    .sort((a, b) => b.similarityScore - a.similarityScore)[0]?.claimId;
  const productMatchedLength = claimScores.reduce((sum, item) => sum + (item.claimMatchedEffectiveLength ?? 0), 0);
  const productTotalLength = claimScores.reduce((sum, item) => sum + (item.claimTotalEffectiveLength ?? 0), 0);
  const bandOptions = {
    zeroedByMismatch: productZeroedByMismatch,
    matchedEffectiveLength: productMatchedLength,
    totalEffectiveLength: productTotalLength,
  };
  return {
    claimScores,
    productSimilarityScore,
    productScoreBand: scoreToBand(productSimilarityScore, bandOptions),
    riskLevel: scoreToRiskLevel(productSimilarityScore, bandOptions),
    highestScoringClaimId,
  };
}
