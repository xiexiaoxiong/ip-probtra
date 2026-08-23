/**
 * 行业路由与关键词确认流程测试
 *
 * 用法: pnpm exec tsx scripts/test-industry-keyword-flow.ts
 */

import assert from 'node:assert/strict';
import {
  buildAutoConfirmedKeywordState,
  buildConfirmedKeywordState,
  detectIndustryFromText,
  module2ConfigKeyForIndustry,
} from '@/lib/industry-keyword-flow';
import type { KeywordConfirmationState } from '@/lib/types';
import { filterExecutableKeywordRecords } from '@/lib/keyword-utils';

const fitness = detectIndustryFromText('一种跑步机阻力训练装置，适用于健身训练和力量训练。');
assert.equal(fitness.industry, 'fitness_equipment');
assert.equal(module2ConfigKeyForIndustry(fitness.industry), 'module2Fitness');

const appliance = detectIndustryFromText('一种厨房清洁用吸尘器，属于家用电器领域，可用于地面清洁。');
assert.equal(appliance.industry, 'home_appliances');
assert.equal(module2ConfigKeyForIndustry(appliance.industry), 'module2HomeAppliances');

const general = detectIndustryFromText('一种文具收纳结构，包括壳体、转轴和锁止件。');
assert.equal(general.industry, 'general');
assert.equal(module2ConfigKeyForIndustry(general.industry), 'module2');

const currentState: KeywordConfirmationState = {
  status: 'editing',
  autoKeywords: ['跑步机', '阻力训练', '跑步机'],
  userKeywords: [],
  finalKeywords: ['跑步机', '阻力训练'],
  promptedAt: 1000,
  deadlineAt: 31_000,
};

const confirmed = buildConfirmedKeywordState(currentState, '跑步机，折叠跑步机\n静音电机', 40_000);
assert.equal(confirmed.status, 'confirmed');
assert.deepEqual(confirmed.autoKeywords, ['跑步机', '阻力训练']);
assert.deepEqual(confirmed.userKeywords, ['跑步机', '折叠跑步机', '静音电机']);
assert.deepEqual(confirmed.finalKeywords, ['跑步机', '阻力训练', '折叠跑步机', '静音电机']);
assert.equal(confirmed.confirmedAt, 40_000);

const autoConfirmed = buildAutoConfirmedKeywordState(['厨房', '吸尘器', '厨房'], { promptedAt: 2000, deadlineAt: 32_000 }, 33_000);
assert.equal(autoConfirmed.status, 'auto_confirmed');
assert.deepEqual(autoConfirmed.userKeywords, []);
assert.deepEqual(autoConfirmed.finalKeywords, ['厨房', '吸尘器']);
assert.equal(autoConfirmed.promptedAt, 2000);
assert.equal(autoConfirmed.deadlineAt, 32_000);
assert.equal(autoConfirmed.confirmedAt, 33_000);

const executableKeywords = filterExecutableKeywordRecords([
  {
    keyword_text: '开放式头戴耳机',
    keyword_type: 'OBJECT_BASE',
    source_location: '主客体基础词',
    raw_payload: {
      query_role: 'executable_search',
      guard_status: 'passed',
      object_terms: ['开放式头戴耳机'],
    },
  },
  {
    keyword_text: '中空孔',
    keyword_type: 'REQUIRED_FEATURE',
    source_location: '必要特征基础词',
  },
  {
    keyword_text: '闭式切换',
    keyword_type: 'REQUIRED_FEATURE',
    source_location: '必要特征基础词',
  },
  {
    keyword_text: '中空孔开放式头戴耳机',
    keyword_type: 'REQUIRED_FEATURE',
    source_location: '必要特征+主客体',
    raw_payload: {
      query_role: 'executable_search',
      guard_status: 'passed',
      object_terms: ['开放式头戴耳机'],
    },
  },
  {
    keyword_text: '中空塑料板',
    keyword_type: 'COMBINED',
    source_location: '模型生成',
    raw_payload: {
      query_role: 'executable_search',
      guard_status: 'passed',
      object_terms: ['开放式头戴耳机'],
    },
  },
]);
assert.deepEqual(executableKeywords, ['开放式头戴耳机', '中空孔开放式头戴耳机']);

console.log('industry keyword flow tests passed');
