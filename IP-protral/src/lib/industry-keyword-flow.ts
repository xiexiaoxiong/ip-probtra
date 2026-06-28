import { normalizeKeywordList } from './keyword-utils';
import type { IndustryType, KeywordConfirmationState } from './types';

export type Module2ConfigKey = 'module2' | 'module2Fitness' | 'module2HomeAppliances';

function scoreIndustryKeywords(content: string, keywords: string[]): number {
  return keywords.reduce((score, keyword) => {
    const pattern = new RegExp(keyword, 'ig');
    const matches = content.match(pattern);
    return score + (matches?.length || 0);
  }, 0);
}

export function detectIndustryFromText(patentContent: string): {
  industry: IndustryType;
  confidence: number;
  reasoning: string;
} {
  const normalized = patentContent.toLowerCase();
  const fitnessScore = scoreIndustryKeywords(normalized, [
    '健身',
    '训练',
    '跑步机',
    '划船机',
    '椭圆机',
    '单车',
    '哑铃',
    '杠铃',
    '有氧',
    '力量',
    '运动器械',
    'treadmill',
    'rowing',
    'fitness',
  ]);
  const applianceScore = scoreIndustryKeywords(normalized, [
    '家电',
    '家用电器',
    '洗衣机',
    '冰箱',
    '吸尘器',
    '空调',
    '空气净化',
    '电饭煲',
    '烹饪',
    '清洁',
    '厨房',
    'appliance',
    'vacuum',
    'refrigerator',
  ]);

  if (fitnessScore === 0 && applianceScore === 0) {
    return {
      industry: 'general',
      confidence: 0.4,
      reasoning: '未命中健身器材或家用电器的高置信度关键词，回退到通用工作流。',
    };
  }

  if (fitnessScore >= applianceScore) {
    return {
      industry: 'fitness_equipment',
      confidence: Number((fitnessScore / Math.max(fitnessScore + applianceScore, 1)).toFixed(2)),
      reasoning: `命中健身器材相关关键词 ${fitnessScore} 次，家电关键词 ${applianceScore} 次。`,
    };
  }

  return {
    industry: 'home_appliances',
    confidence: Number((applianceScore / Math.max(fitnessScore + applianceScore, 1)).toFixed(2)),
    reasoning: `命中家用电器相关关键词 ${applianceScore} 次，健身器材关键词 ${fitnessScore} 次。`,
  };
}

export function module2ConfigKeyForIndustry(industry: IndustryType): Module2ConfigKey {
  if (industry === 'fitness_equipment') return 'module2Fitness';
  if (industry === 'home_appliances') return 'module2HomeAppliances';
  return 'module2';
}

export function buildConfirmedKeywordState(
  currentState: KeywordConfirmationState,
  userKeywordInput: string[] | string,
  now: number = Date.now(),
): KeywordConfirmationState {
  const autoKeywords = normalizeKeywordList(currentState.autoKeywords);
  const userKeywords = normalizeKeywordList(userKeywordInput);
  return {
    ...currentState,
    status: 'confirmed',
    autoKeywords,
    userKeywords,
    finalKeywords: normalizeKeywordList([...autoKeywords, ...userKeywords]),
    confirmedAt: now,
  };
}

export function buildAutoConfirmedKeywordState(
  autoKeywordsInput: string[] | string,
  baseState?: Pick<KeywordConfirmationState, 'promptedAt' | 'deadlineAt'>,
  now: number = Date.now(),
): KeywordConfirmationState {
  const autoKeywords = normalizeKeywordList(autoKeywordsInput);
  return {
    status: 'auto_confirmed',
    autoKeywords,
    userKeywords: [],
    finalKeywords: autoKeywords,
    promptedAt: baseState?.promptedAt,
    deadlineAt: baseState?.deadlineAt,
    confirmedAt: now,
  };
}
