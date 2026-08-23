/**
 * Portal 无效检索契约、隔离守门与 Excel 快照测试
 *
 * 用法: pnpm exec tsx scripts/test-invalidity-portal-contracts.ts
 */

import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { mkdtemp, mkdir, realpath, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import ExcelJS from 'exceljs';
import {
  assertCriticalDateConfirmationResponse,
  assertHumanReviewCommandResponse,
  assertInvalidityReviewContext,
  assertInvalidityReportDataV1,
  claimDisplayLabel,
  exactLoopbackServiceUrl,
  humanReviewUploadReceiptMatches,
  invalidityEnvironment,
  invalidityUploadFileKey,
  invalidityAutomaticRounds,
  invalidityDateReviewDefaults,
  invalidityPendingDateReviewDocuments,
  invalidityPortalSessionStatus,
  invalidityReportPendingMessage,
  invalidityReviewCurrentDocumentVersionId,
  invalidityReviewQualificationsByClaim,
  isInvalidityClaimContinuable,
  isInvaliditySuccessfulClaimTerminal,
  isInvalidityTerminalStatus,
  latestInvalidityGapsByKey,
  liveLabPayloadViolation,
  mergeCriticalDateConfirmation,
  needsInvalidityCriticalDateConfirmation,
  redactInvalidityPrivatePaths,
  reportSnapshotDate,
} from '@/lib/invalidity-contracts';
import { createStableInvalidityIntent } from '@/lib/invalidity-idempotency';
import {
  atomicSearchTerms,
  buildPatsnapSearchPlan,
  patsnapSearchResult,
  selectCriticalDate,
  targetPatentSummary,
  type JsonObject,
} from '@/lib/invalidity-p002-test-flow';
import { invalidityDateClassification } from '@/lib/invalidity-date-classification';
import {
  configuredInvalidityUploadRoots,
  prepareInvalidityUploadRoot,
  resolveInvalidityUploadPath,
} from '@/lib/invalidity-upload-storage';
import { buildInvalidityWorkbook } from '@/lib/invalidity-report-export';
import {
  invalidityTestProxyOperation,
  scopedInvalidityTestIdentifier,
} from '@/lib/invalidity-test-proxy-policy';
import {
  InvalidityTestResourceNotFoundError,
  InvalidityTestResourceOwnership,
  type InvalidityTestResourceOwner,
  type InvalidityTestResourceOwnershipRepository,
} from '@/lib/invalidity-test-resource-ownership';

assert.equal(exactLoopbackServiceUrl('http://127.0.0.1:5209', 'INVALIDITY_TEST_API_URL', 5209), 'http://127.0.0.1:5209');
assert.throws(() => exactLoopbackServiceUrl('http://localhost:5209', 'INVALIDITY_TEST_API_URL', 5209));
assert.throws(() => exactLoopbackServiceUrl('http://127.0.0.1:5109', 'INVALIDITY_TEST_API_URL', 5209));
assert.throws(() => exactLoopbackServiceUrl('http://127.0.0.1:5209/v1', 'INVALIDITY_TEST_API_URL', 5209));
assert.equal(exactLoopbackServiceUrl('http://127.0.0.1:5109', 'INVALIDITY_PROD_API_URL', 5109), 'http://127.0.0.1:5109');
assert.throws(() => exactLoopbackServiceUrl('http://127.0.0.1:5209', 'INVALIDITY_PROD_API_URL', 5109));
assert.deepEqual(atomicSearchTerms('份和陶瓷颗粒'), ['陶瓷颗粒']);
assert.deepEqual(atomicSearchTerms('陶瓷颗粒 和水'), ['陶瓷颗粒']);
assert.deepEqual(atomicSearchTerms('不饱和化合物'), ['不饱和化合物']);
assert.equal(invalidityEnvironment('test'), 'test');
assert.equal(invalidityEnvironment('prod'), 'prod');
assert.throws(() => invalidityEnvironment(undefined), /显式提供 environment/);
assert.throws(() => invalidityEnvironment('production'), /test 或 prod/);
assert.equal(
  invalidityUploadFileKey('invalidity-test-123-patent.pdf', 'test'),
  'invalidity-test-123-patent.pdf',
);
assert.throws(() => invalidityUploadFileKey('invalidity-prod-123-patent.pdf', 'test'), /不属于 test/);
assert.throws(() => invalidityUploadFileKey('../patent.pdf', 'test'));
assert.throws(() => invalidityUploadFileKey('folder/patent.pdf', 'test'));
assert.throws(() => invalidityUploadFileKey('folder\\patent.pdf', 'test'));
assert.deepEqual(
  redactInvalidityPrivatePaths({ source_path: '/private/data/patent.pdf', title: 'D1', locator: { page: 3 } }),
  { source_path: '[REDACTED SERVER PATH]', title: 'D1', locator: { page: 3 } },
);

const p002Target = targetPatentSummary({
  source_snapshot: {
    target_images: ['target-1.png', 'target-2.png'],
    patent_snapshot: {
      patent_number: 'CN222356540U',
      title: '一种开放式头戴耳机',
      application_date: '2024-04-29',
      priority_date: null,
      publication_date: '2025-01-14',
      claims: [{
        claim_id: '1',
        claim_type: 'INDEPENDENT',
        claim_text: '一种开放式头戴耳机，包括第一组件、第二组件和连接组件。',
      }],
      figures: [{}, {}],
    },
  },
});
assert.equal(p002Target.claimId, '1');
assert.equal(p002Target.figureCount, 2);
assert.deepEqual(selectCriticalDate(p002Target), {
  date: '2024-04-29',
  basis: 'application_date',
  requiresHumanReview: true,
});
const p002Plan = buildPatsnapSearchPlan({
  module_run: {
    output_snapshot: {
      output: {
        technical_subject: '开放式头戴耳机',
        subject_synonyms_zh: ['开放耳机'],
        limitations: [
          { feature_id: 'f1', text: '第一组件', mandatory: true },
          { feature_id: 'f2', text: '第二组件', mandatory: true },
          { feature_id: 'f3', text: '连接组件', mandatory: true },
        ],
        queries: [{
          query_id: 'q1',
          provider_kind: 'patent',
          search_objective: 'full_claim_single_reference',
          date_channel: 'ordinary_prior_art',
          technical_subject: '开放式头戴耳机',
          feature_ids: ['f1', 'f2', 'f3'],
          expression: '开放式头戴耳机 第一组件 第二组件 连接组件',
          language: 'zh',
        }],
        model: 'glm-4.6v',
        used_target_images: 2,
      },
    },
  },
}, '2024-04-29');
assert.equal(p002Plan.model, 'glm-4.6v');
assert.equal(p002Plan.usedTargetImages, 2);
assert.equal(p002Plan.strategy, 'balanced');
assert.deepEqual(p002Plan.featureTerms, ['第一组件', '第二组件']);
assert.deepEqual(p002Plan.input, {
  search_provider: 'patsnap',
  search_modality: 'text',
  query: {
      text: 'TACD:((开放式头戴耳机 OR 开放耳机) AND (第一组件 OR 第一组件结构) AND (第二组件 OR 第二组件结构))',
      subject_terms: ['开放式头戴耳机', '开放耳机'],
      feature_terms: ['第一组件', '第二组件'],
      classification_terms: [],
    },
    subject_terms: ['开放式头戴耳机', '开放耳机'],
    feature_terms: ['第一组件', '第二组件'],
    classification_terms: [],
  language: 'zh',
  server_before: '2024-04-29',
  max_results: 10,
  query_plan_query_id: 'q1',
  query_role: 'claim_context_recall',
  query_variant: 'system_architecture_recall',
  allow_zero_results: false,
  compact_fallback_allowed: true,
});

const fixedFiveLaneRun = {
  module_run: {
    output_snapshot: {
      output: {
        technical_subject: '水枪',
        subject_synonyms_zh: ['玩具水枪'],
        subject_synonyms_en: ['water gun'],
        limitations: [{
          feature_id: 'f-inventive',
          text: '选择性联接',
          synonyms_en: ['selective coupling'],
        }],
        queries: [
          {
            query_id: 'fixed-1',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'inventive_point_precision',
            query_variant: 'applicant_plus_object',
            search_scope: 'full_text',
            expression: '(水枪 OR 玩具水枪 OR water gun)',
            subject_terms: ['水枪'],
            feature_ids: [],
            feature_terms: [],
            applicant_terms: ['示例玩具公司'],
            excluded_publication: 'CN112345678A',
            provider_expression: '(AN:(示例玩具公司)) AND (TTL:(水枪 OR 玩具水枪 OR water gun) OR ABST:(水枪 OR 玩具水枪 OR water gun))',
            language: 'zh',
          },
          {
            query_id: 'fixed-2',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'inventive_point_precision',
            query_variant: 'title_object_plus_desc_inventive',
            search_scope: 'full_text',
            expression: '(水枪 OR water gun) AND (选择性联接 OR selective coupling)',
            subject_terms: ['水枪'],
            feature_ids: ['f-inventive'],
            feature_terms: ['选择性联接'],
            feature_term_groups: [['选择性联接', 'selective coupling']],
            provider_expression: '(TTL:(水枪 OR water gun)) AND (DESC:(选择性联接 OR selective coupling))',
            language: 'zh',
          },
          {
            query_id: 'fixed-3',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'inventive_point_precision',
            query_variant: 'classification_plus_desc_inventive',
            search_scope: 'full_text',
            expression: '(选择性联接 OR selective coupling)',
            subject_terms: [],
            feature_ids: ['f-inventive'],
            feature_terms: ['选择性联接'],
            feature_term_groups: [['选择性联接', 'selective coupling']],
            classification_anchors: ['F41B 9/00'],
            provider_expression: '(IPC:(F41B9/00)) AND (DESC:(选择性联接 OR selective coupling))',
            language: 'zh',
          },
          {
            query_id: 'fixed-4',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'inventive_point_precision',
            query_variant: 'desc_object_plus_desc_inventive_plus_effect',
            search_scope: 'full_text',
            expression: '(水枪 OR water gun) AND (选择性联接 OR selective coupling) AND (动量传递 OR momentum transfer)',
            subject_terms: ['水枪'],
            feature_ids: ['f-inventive'],
            feature_terms: ['选择性联接'],
            feature_term_groups: [['选择性联接', 'selective coupling'], ['动量传递', 'momentum transfer']],
            provider_expression: '(DESC:(水枪 OR water gun)) AND (DESC:(选择性联接 OR selective coupling)) AND (DESC:(动量传递 OR momentum transfer))',
            language: 'zh',
          },
          {
            query_id: 'fixed-5',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'inventive_point_precision',
            query_variant: 'title_keyword_object_plus_desc_function',
            search_scope: 'full_text',
            expression: '(水枪 OR water gun) AND (动量传递 OR momentum transfer)',
            subject_terms: ['水枪'],
            feature_ids: ['f-inventive'],
            feature_terms: ['动量传递'],
            feature_term_groups: [['动量传递', 'momentum transfer']],
            provider_expression: '(TTL:(水枪 OR water gun) OR ABST:(水枪 OR water gun)) AND (DESC:(动量传递 OR momentum transfer))',
            language: 'zh',
          },
        ],
        model: 'glm-4.6v',
        used_target_images: 1,
      },
    },
  },
};
const fixedLaneVariants = [
  'applicant_plus_object',
  'title_object_plus_desc_inventive',
  'classification_plus_desc_inventive',
  'desc_object_plus_desc_inventive_plus_effect',
  'title_keyword_object_plus_desc_function',
] as const;
for (const variant of fixedLaneVariants) {
  const plan = buildPatsnapSearchPlan(
    fixedFiveLaneRun,
    '2020-04-22',
    10,
    'balanced',
    'inventive_point_precision',
    undefined,
    variant,
  );
  assert.equal(plan.queryVariant, variant);
  assert.equal(plan.expression, fixedFiveLaneRun.module_run.output_snapshot.output.queries
    .find((query) => query.query_variant === variant)?.provider_expression);
  assert.equal(plan.compactFallbackAllowed, false);
  assert.equal(plan.input.query_variant, variant);
}
const applicantFixedPlan = buildPatsnapSearchPlan(
  fixedFiveLaneRun,
  '2020-04-22',
  10,
  'balanced',
  'inventive_point_precision',
  undefined,
  'applicant_plus_object',
);
assert.deepEqual(applicantFixedPlan.applicantTerms, ['示例玩具公司']);
assert.equal(applicantFixedPlan.excludedPublication, 'CN112345678A');
assert.throws(
  () => buildPatsnapSearchPlan(
    fixedFiveLaneRun,
    '2020-04-22',
    10,
    'compact-fallback',
    'inventive_point_precision',
    undefined,
    'title_object_plus_desc_inventive',
  ),
  /固定五组首轮检索线不执行自动收敛/,
);
const frozenI2Root = path.resolve(
  '..',
  '.data',
  'invalidity',
  'test',
  'decision-benchmarks',
  '20260808-five-case-i2v3-r5',
  'plans',
);
let frozenFixedLaneCount = 0;
for (const caseId of readdirSync(frozenI2Root)) {
  const caseDirectory = path.join(frozenI2Root, caseId);
  for (const fileName of readdirSync(caseDirectory).filter((name) => /^claim-.+\.json$/.test(name))) {
    const envelope = JSON.parse(
      readFileSync(path.join(caseDirectory, fileName), 'utf8'),
    ) as JsonObject;
    const frozenPlan = envelope.plan as JsonObject;
    for (const query of (frozenPlan.queries as JsonObject[]).filter((item) => (
      item.provider_kind === 'patent' && item.date_channel === 'ordinary_prior_art'
    ))) {
      assert.equal(query.compact_fallback_allowed, false);
      const compiled = buildPatsnapSearchPlan(
        { module_run: { output_snapshot: { output: frozenPlan } } },
        String(envelope.critical_date),
        10,
        'balanced',
        'inventive_point_precision',
        String(query.query_id),
      );
      assert.equal(compiled.expression, query.provider_expression);
      assert.equal(compiled.compactFallbackAllowed, false);
      if (query.query_variant === 'applicant_plus_object') {
        assert.equal(compiled.input.excluded_publication, query.excluded_publication);
        assert.match(compiled.expression, /\bAN:/);
        assert.doesNotMatch(compiled.expression, /\b(?:NOT\s+)?PN:/i);
      }
      frozenFixedLaneCount += 1;
    }
  }
}
assert.ok(frozenFixedLaneCount >= 20, 'five-case I2 v3 freeze must expose executable fixed lanes');

const layeredMaterialPlan = buildPatsnapSearchPlan({
  module_run: {
    output_snapshot: {
      output: {
        technical_subject: '锂离子电池隔膜的高固含量水性陶瓷浆料',
        subject_synonyms_zh: [
          '高固含量水性陶瓷浆料',
          '水性陶瓷浆料',
          '锂离子电池隔膜',
          '电池隔膜',
          '锂电池隔膜',
        ],
        limitations: [{
          feature_id: 'ceramic',
          text: '陶瓷颗粒',
          synonyms_zh: ['三氧化二铝'],
        }],
        queries: [{
          query_id: 'material-title-layer',
          provider_kind: 'patent',
          search_objective: 'full_claim_single_reference',
          date_channel: 'ordinary_prior_art',
          technical_subject: '锂离子电池隔膜的高固含量水性陶瓷浆料',
          query_role: 'title_abstract_concept',
          query_variant: 'title_abstract_concept',
          search_scope: 'title_abstract',
          feature_ids: ['ceramic'],
          feature_terms: ['陶瓷颗粒'],
          feature_term_groups: [['陶瓷颗粒', '三氧化二铝']],
          expression: '锂离子电池隔膜的高固含量水性陶瓷浆料 AND 陶瓷颗粒',
          language: 'zh',
        }],
        model: 'glm-4.6v',
        used_target_images: 1,
      },
    },
  },
}, '2014-11-14');
assert.ok(layeredMaterialPlan.subjectTerms.includes('水性陶瓷浆料'));
assert.ok(layeredMaterialPlan.subjectTerms.includes('锂离子电池隔膜'));
assert.ok(layeredMaterialPlan.subjectTerms.includes('电池隔膜'));
assert.ok(layeredMaterialPlan.subjectTerms.includes('锂电池隔膜'));
assert.match(layeredMaterialPlan.expression, /TTL:/);
assert.match(layeredMaterialPlan.expression, /锂离子电池隔膜/);

const waterGunQueryRun: {
  module_run: {
    output_snapshot: {
      output: {
        [key: string]: unknown;
        queries: Array<Record<string, unknown>>;
      };
    };
  };
} = {
  module_run: {
    output_snapshot: {
      output: {
        technical_subject: '水枪',
        subject_synonyms_zh: ['液体喷射装置'],
        subject_synonyms_en: ['water gun', 'liquid projector'],
        limitations: [
          {
            feature_id: 'f-coupling',
            text: '本体和阀杆经由位置选择性联接装置联接',
            synonyms_zh: ['可选择位置的联接装置'],
            synonyms_en: ['position-selective coupling device'],
          },
          {
            feature_id: 'f-intermediate',
            text: '联接装置在中间位置选择性联接本体和阀杆',
            synonyms_zh: ['中间位置'],
            synonyms_en: ['intermediate position'],
          },
          {
            feature_id: 'f-tank',
            text: '压力罐',
            synonyms_zh: ['加压液体罐'],
            synonyms_en: ['pressure tank'],
          },
          {
            feature_id: 'f-conduit',
            text: '带有阀导管的阀',
            synonyms_zh: ['阀导管'],
            synonyms_en: ['valve conduit'],
          },
          {
            feature_id: 'f-stem',
            text: '可移动的阀杆',
            synonyms_zh: ['阀杆'],
            synonyms_en: ['valve stem'],
          },
        ],
        queries: [
          {
            query_id: 'q-inventive',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'inventive_point_precision',
            query_variant: 'object_plus_two_inventive_points',
            search_scope: 'full_text',
            scope_reason: '具体联接关系更可能在说明书全文展开',
            classification_anchors: ['F41B 9/00'],
            feature_ids: ['f-coupling', 'f-intermediate'],
            feature_terms: ['位置选择性联接装置', '中间位置'],
            expression: '水枪 AND 位置选择性联接装置 AND 中间位置',
            language: 'zh',
          },
          {
            query_id: 'q-context',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'claim_context_recall',
            query_variant: 'system_architecture_recall',
            search_scope: 'claims',
            scope_reason: '类别结构通常写入权利要求',
            feature_ids: ['f-tank', 'f-conduit', 'f-stem'],
            feature_terms: ['压力罐', '阀导管', '阀杆'],
            expression: '水枪 AND 压力罐 AND 阀导管 AND 阀杆',
            language: 'zh',
          },
        ],
        model: 'glm-4.6v',
        used_target_images: 2,
      },
    },
  },
};
const waterGunInventivePlan = buildPatsnapSearchPlan(
  waterGunQueryRun,
  '2010-01-01',
  10,
  'balanced',
  'inventive_point_precision',
);
assert.equal(waterGunInventivePlan.queryRole, 'inventive_point_precision');
assert.equal(waterGunInventivePlan.searchScope, 'full_text');
assert.deepEqual(waterGunInventivePlan.classificationAnchors, ['F41B9/00']);
assert.match(waterGunInventivePlan.expression, /IPC:\(F41B9\/00\)/);
assert.match(waterGunInventivePlan.expression, /TACD:/);
assert.match(waterGunInventivePlan.expression, /位置选择性联接装置/);
assert.match(waterGunInventivePlan.expression, /中间位置/);
assert.deepEqual(
  waterGunInventivePlan.featureTerms,
  ['位置选择性联接装置', '中间位置'],
);

const waterGunContextPlan = buildPatsnapSearchPlan(
  waterGunQueryRun,
  '2010-01-01',
  10,
  'balanced',
  'claim_context_recall',
);
assert.equal(waterGunContextPlan.queryRole, 'claim_context_recall');
assert.equal(waterGunContextPlan.searchScope, 'claims');
assert.match(waterGunContextPlan.expression, /^CLMS:/);
assert.match(waterGunContextPlan.expression, /水枪/);
assert.match(waterGunContextPlan.expression, /压力罐/);
assert.match(waterGunContextPlan.expression, /阀导管/);
assert.doesNotMatch(
  waterGunContextPlan.expression,
  /阀杆/,
  'the compiled query must stay within the three text-group budget',
);
assert.deepEqual(
  waterGunContextPlan.featureTerms,
  ['压力罐', '阀导管'],
);

const componentWordQueryRun = structuredClone(waterGunQueryRun);
componentWordQueryRun.module_run.output_snapshot.output.queries = [
  {
    query_id: 'q-component-words',
    provider_kind: 'patent',
    search_objective: 'full_claim_single_reference',
    date_channel: 'ordinary_prior_art',
    technical_subject: '水枪',
    query_role: 'claim_context_recall',
    query_variant: 'system_architecture_recall',
    search_scope: 'claims',
    scope_reason: '类别构件通常写入权利要求',
    feature_ids: ['f-tank', 'f-conduit'],
    feature_terms: ['罐', '阀'],
    feature_term_groups: [
      ['罐', '压力罐', '储液器', 'tank', 'reservoir'],
      ['阀', 'valve'],
    ],
    expression: '水枪 AND 罐 AND 阀',
    language: 'zh',
  },
];
const componentWordPlan = buildPatsnapSearchPlan(
  componentWordQueryRun,
  '2010-01-01',
  10,
  'balanced',
  'claim_context_recall',
);
assert.deepEqual(componentWordPlan.featureTerms, ['罐', '阀']);
assert.match(componentWordPlan.expression, /\(阀 OR [^)]*valve/);
assert.match(componentWordPlan.expression, /\(罐 OR [^)]*reservoir/);

const titleAbstractComponentActionRun = structuredClone(waterGunQueryRun);
titleAbstractComponentActionRun.module_run.output_snapshot.output.queries = [
  {
    query_id: 'q-title-component-action',
    provider_kind: 'patent',
    search_objective: 'full_claim_single_reference',
    date_channel: 'ordinary_prior_art',
    technical_subject: '水枪',
    query_role: 'title_abstract_concept',
    query_variant: 'title_abstract_concept',
    search_scope: 'title_abstract',
    scope_reason: '客体、代表构件与机构动作可能在标题摘要中共同出现',
    feature_ids: ['f-coupling', 'f-intermediate'],
    feature_terms: ['位置选择性联接装置', '中间位置'],
    feature_term_groups: [
      ['位置选择性联接装置', '选择性联接'],
      ['中间位置'],
    ],
    expression: '水枪 AND 位置选择性联接装置 AND 中间位置',
    language: 'zh',
  },
];
const titleAbstractComponentActionPlan = buildPatsnapSearchPlan(
  titleAbstractComponentActionRun,
  '2010-01-01',
  10,
  'balanced',
  'title_abstract_concept',
  'q-title-component-action',
);
assert.equal(titleAbstractComponentActionPlan.queryVariant, 'title_abstract_concept');
assert.equal(titleAbstractComponentActionPlan.searchScope, 'title_abstract');
assert.deepEqual(
  titleAbstractComponentActionPlan.featureTerms,
  ['位置选择性联接装置', '中间位置'],
);
assert.match(titleAbstractComponentActionPlan.expression, /位置选择性联接装置/);
assert.match(titleAbstractComponentActionPlan.expression, /中间位置/);

const targetCitationQueryRun = structuredClone(waterGunQueryRun);
targetCitationQueryRun.module_run.output_snapshot.output.queries = [
  {
    query_id: 'q-target-citation',
    provider_kind: 'patent',
    purpose: 'citation_followup',
    date_channel: 'ordinary_prior_art',
    technical_subject: '水枪',
    query_role: 'title_abstract_concept',
    query_variant: 'target_citation_lookup',
    search_scope: 'full_text',
    scope_reason: '目标专利说明书明确引用该专利文献',
    feature_ids: [],
    feature_terms: [],
    expression: 'WO2018/215646A1',
    target_citation: 'WO2018/215646A1',
    provider_expression: 'PN:(WO2018215646A1)',
    language: 'zh',
  },
];
const targetCitationPlan = buildPatsnapSearchPlan(
  targetCitationQueryRun,
  '2021-09-24',
  10,
  'balanced',
  'title_abstract_concept',
  'q-target-citation',
);
assert.equal(targetCitationPlan.queryVariant, 'target_citation_lookup');
assert.equal(targetCitationPlan.expression, 'PN:(WO2018215646A1)');
assert.deepEqual(targetCitationPlan.featureTerms, []);

const targetEffectQueryRun = structuredClone(waterGunQueryRun);
targetEffectQueryRun.module_run.output_snapshot.output.queries = [
  {
    query_id: 'q-target-effect',
    provider_kind: 'patent',
    purpose: 'technical_effect',
    date_channel: 'ordinary_prior_art',
    technical_subject: '水枪',
    query_role: 'title_abstract_concept',
    query_variant: 'target_effect_recall',
    search_scope: 'title_abstract',
    scope_reason: '目标专利明确描述的工作效果',
    feature_ids: [],
    feature_terms: [],
    target_effect_terms: ['短促水流迸射', '短促水流', 'short burst', 'burst discharge'],
    expression: '水枪 AND 短促水流迸射',
    language: 'zh',
  },
];
const targetEffectPlan = buildPatsnapSearchPlan(
  targetEffectQueryRun,
  '2021-09-24',
  10,
  'balanced',
  'title_abstract_concept',
  'q-target-effect',
);
assert.equal(targetEffectPlan.queryVariant, 'target_effect_recall');
assert.match(targetEffectPlan.expression, /TTL:/);
assert.match(targetEffectPlan.expression, /short burst/);

const interfaceActionEffectRun = structuredClone(targetEffectQueryRun);
interfaceActionEffectRun.module_run.output_snapshot.output.technical_subject =
  '燃气灶调节装置';
interfaceActionEffectRun.module_run.output_snapshot.output.subject_synonyms_zh =
  ['燃气灶', '燃气灶自动调节装置', '燃气炉', '燃气炉调节装置'];
interfaceActionEffectRun.module_run.output_snapshot.output.queries[0].technical_subject =
  '燃气灶调节装置';
interfaceActionEffectRun.module_run.output_snapshot.output.queries[0].target_context_terms =
  ['燃气灶开关轴', '燃气灶开关', '开关'];
interfaceActionEffectRun.module_run.output_snapshot.output.queries[0].target_effect_terms =
  ['定时', '调节', '控制', '驱动'];
const interfaceActionEffectPlan = buildPatsnapSearchPlan(
  interfaceActionEffectRun,
  '2016-11-18',
  10,
  'balanced',
  'title_abstract_concept',
  'q-target-effect',
);
assert.match(interfaceActionEffectPlan.expression, /燃气灶开关/);
assert.match(interfaceActionEffectPlan.expression, /定时/);
assert.equal(interfaceActionEffectPlan.featureQueryGroups.length, 2);
assert.ok(interfaceActionEffectPlan.subjectTerms.length <= 5);
assert.ok(interfaceActionEffectPlan.subjectTerms.includes('燃气炉'));

const classificationActionRun = structuredClone(interfaceActionEffectRun);
classificationActionRun.module_run.output_snapshot.output.queries[0].query_id =
  'q-classification-action';
classificationActionRun.module_run.output_snapshot.output.queries[0].query_variant =
  'classification_action_recall';
classificationActionRun.module_run.output_snapshot.output.queries[0].classification_anchors =
  ['F24C3/12'];
classificationActionRun.module_run.output_snapshot.output.queries[0].target_context_terms = [];
classificationActionRun.module_run.output_snapshot.output.queries[0].target_effect_terms =
  ['定时', 'timer'];
const classificationActionPlan = buildPatsnapSearchPlan(
  classificationActionRun,
  '2016-11-18',
  10,
  'balanced',
  'title_abstract_concept',
  'q-classification-action',
  'classification_action_recall',
);
assert.equal(classificationActionPlan.queryVariant, 'classification_action_recall');
assert.match(classificationActionPlan.expression, /IPC:\(F24C3\/12\)/);
assert.match(classificationActionPlan.expression, /定时/);
assert.doesNotMatch(classificationActionPlan.expression, /燃气灶调节装置/);
assert.deepEqual(classificationActionPlan.subjectTerms, []);
assert.deepEqual(classificationActionPlan.classificationAnchors, ['F24C3/12']);

const modifiedSubjectEffectRun = structuredClone(targetEffectQueryRun);
modifiedSubjectEffectRun.module_run.output_snapshot.output.technical_subject =
  '适配于不同耳朵的无线耳机';
modifiedSubjectEffectRun.module_run.output_snapshot.output.subject_synonyms_zh = [];
modifiedSubjectEffectRun.module_run.output_snapshot.output.subject_synonyms_en = [];
modifiedSubjectEffectRun.module_run.output_snapshot.output.queries[0].technical_subject =
  '适配于不同耳朵的无线耳机';
modifiedSubjectEffectRun.module_run.output_snapshot.output.queries[0].expression =
  '适配于不同耳朵的无线耳机 AND 通过旋转调整';
modifiedSubjectEffectRun.module_run.output_snapshot.output.queries[0].target_effect_terms =
  ['通过旋转调整'];
const modifiedSubjectEffectPlan = buildPatsnapSearchPlan(
  modifiedSubjectEffectRun,
  '2022-06-17',
  10,
  'balanced',
  'title_abstract_concept',
  'q-target-effect',
);
assert.ok(modifiedSubjectEffectPlan.subjectTerms.includes('无线耳机'));
assert.match(modifiedSubjectEffectPlan.expression, /无线耳机/);

const waterGunOutput = waterGunQueryRun.module_run.output_snapshot.output;
const multiVariantQueryRun = {
  module_run: {
    output_snapshot: {
      output: {
        ...waterGunOutput,
        queries: [
          {
            query_id: 'q-maximal',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'inventive_point_precision',
            query_variant: 'maximal_similarity_precision',
            search_scope: 'full_text',
            feature_ids: [
              'f-coupling',
              'f-intermediate',
              'f-tank',
              'f-conduit',
              'f-stem',
            ],
            feature_terms: [
              '位置选择性联接装置',
              '中间位置',
              '压力罐',
              '阀导管',
              '阀杆',
            ],
            expression: '水枪 AND 位置选择性联接装置 AND 中间位置 AND 压力罐 AND 阀导管 AND 阀杆',
            language: 'zh',
            allow_zero_results: true,
            compact_fallback_allowed: false,
          },
          {
            query_id: 'q-subject-classification',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'inventive_point_precision',
            query_variant: 'subject_classification_plus_inventive_point',
            search_scope: 'full_text',
            classification_anchors: ['F41B 9/00'],
            feature_ids: ['f-coupling'],
            feature_terms: ['位置选择性联接装置'],
            feature_term_groups: [[
              '位置选择性联接装置',
              '选择性联接机构',
              '选择性接合机构',
              'selective coupling mechanism',
              'selective engagement mechanism',
              '锁定机构',
              '解锁机构',
              'locking mechanism',
              'unlocking mechanism',
            ]],
            expression: '水枪 AND 位置选择性联接装置',
            language: 'zh',
          },
          {
            query_id: 'q-atomic-composite',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'inventive_point_precision',
            query_variant: 'object_plus_inventive_point',
            search_scope: 'full_text',
            feature_ids: ['f-intermediate'],
            feature_terms: ['中间位置、选择性联接'],
            feature_term_groups: [['中间位置、选择性联接']],
            expression: '水枪 AND 中间位置、选择性联接',
            language: 'zh',
          },
          {
            query_id: 'q-object-inventive-classification',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '水枪',
            query_role: 'title_abstract_concept',
            query_variant: 'object_plus_inventive_classification',
            search_scope: 'title_abstract',
            classification_anchors: ['F16K 1/00'],
            feature_ids: [],
            feature_terms: [],
            expression: '水枪',
            language: 'zh',
          },
          ...waterGunOutput.queries,
        ],
      },
    },
  },
};
const maximalPlan = buildPatsnapSearchPlan(
  multiVariantQueryRun,
  '2010-01-01',
  10,
  'balanced',
  'inventive_point_precision',
  'q-maximal',
);
assert.equal(maximalPlan.queryVariant, 'maximal_similarity_precision');
assert.equal(maximalPlan.featureTerms.length, 5);
assert.equal(maximalPlan.featureQueryGroups.length, 5);
assert.equal(maximalPlan.allowZeroResults, true);
assert.equal(maximalPlan.compactFallbackAllowed, false);
assert.ok((maximalPlan.expression.match(/ AND /g) || []).length >= 5);
assert.doesNotMatch(maximalPlan.expression, /\bstructure\b/i);
assert.throws(
  () => buildPatsnapSearchPlan(
    multiVariantQueryRun,
    '2010-01-01',
    10,
    'compact-fallback',
    'inventive_point_precision',
    'q-maximal',
  ),
  /允许零结果/,
);
const subjectClassificationPlan = buildPatsnapSearchPlan(
  multiVariantQueryRun,
  '2010-01-01',
  10,
  'balanced',
  'inventive_point_precision',
  'q-subject-classification',
);
assert.equal(
  subjectClassificationPlan.queryVariant,
  'subject_classification_plus_inventive_point',
);
assert.deepEqual(subjectClassificationPlan.subjectTerms, []);
assert.equal(subjectClassificationPlan.featureTerms.length, 1);
assert.deepEqual(
  subjectClassificationPlan.input.classification_terms,
  ['F41B9/00'],
);
assert.match(subjectClassificationPlan.expression, /IPC:\(F41B9\/00\)/);
assert.match(subjectClassificationPlan.expression, /位置选择性联接装置/);
assert.doesNotMatch(subjectClassificationPlan.expression, /水枪/);
assert.match(subjectClassificationPlan.expression, /选择性接合机构/);
assert.match(subjectClassificationPlan.expression, /selective coupling mechanism/);
assert.doesNotMatch(subjectClassificationPlan.expression, /解锁机构|锁定机构/);
const atomicCompositePlan = buildPatsnapSearchPlan(
  multiVariantQueryRun,
  '2010-01-01',
  10,
  'balanced',
  'inventive_point_precision',
  'q-atomic-composite',
);
assert.deepEqual(atomicCompositePlan.featureTerms, ['选择性联接']);
assert.deepEqual(atomicCompositePlan.featureQueryGroups[0], ['选择性联接']);
assert.match(atomicCompositePlan.expression, /水枪/);
assert.match(atomicCompositePlan.expression, /选择性联接/);
assert.doesNotMatch(atomicCompositePlan.expression, /中间位置[、，, ]+选择性联接/);
const objectInventiveClassificationPlan = buildPatsnapSearchPlan(
  multiVariantQueryRun,
  '2010-01-01',
  10,
  'balanced',
  'title_abstract_concept',
  'q-object-inventive-classification',
);
assert.equal(
  objectInventiveClassificationPlan.queryVariant,
  'object_plus_inventive_classification',
);
assert.deepEqual(objectInventiveClassificationPlan.featureTerms, []);
assert.deepEqual(
  objectInventiveClassificationPlan.input.classification_terms,
  ['F16K1/00'],
);
assert.match(objectInventiveClassificationPlan.expression, /IPC:\(F16K1\/00\)/);
assert.match(objectInventiveClassificationPlan.expression, /TTL:/);
assert.match(objectInventiveClassificationPlan.expression, /ABST:/);

const bilingualQueryRun = {
  module_run: {
    output_snapshot: {
      output: {
        technical_subject: '开放式头戴耳机',
        subject_synonyms_zh: ['开放式头戴式耳机', '开放式耳麦'],
        subject_synonyms_en: [
          'open - type over - ear headphones',
          'open - type headband headphones',
        ],
        limitations: [
          {
            feature_id: 'f2',
            text: '包括头戴和两个发音单元',
            mandatory: true,
            synonyms_zh: ['头戴与两个发音单元', '头戴及两个发声单元'],
            synonyms_en: [
              'headband and two sound units',
              'headband with two sound units',
            ],
          },
          {
            feature_id: 'f5',
            text: '发音单元设有贯通耳侧与外侧的中空孔',
            mandatory: true,
            synonyms_zh: [
              '发音单元中空孔',
              '发声单元贯通孔',
              '中空孔贯通耳朵侧与外侧',
            ],
            synonyms_en: [
              'sound unit with through - hole',
              'sound unit having through - hole',
              'through - hole from ear side to opposite side',
            ],
          },
        ],
        queries: [{
          query_id: 'bilingual-regression-q1',
          provider_kind: 'patent',
          search_objective: 'full_claim_single_reference',
          date_channel: 'ordinary_prior_art',
          technical_subject: '开放式头戴耳机',
          feature_ids: ['f2', 'f5'],
          expression: '开放式头戴耳机 发音单元 中空孔',
          language: 'zh',
        }],
        model: 'glm-4.6v',
        used_target_images: 2,
      },
    },
  },
};
const bilingualPrimaryPlan = buildPatsnapSearchPlan(
  bilingualQueryRun,
  '2024-04-29',
  10,
  'balanced',
);
const bilingualFallbackPlan = buildPatsnapSearchPlan(
  bilingualQueryRun,
  '2024-04-29',
  10,
  'compact-fallback',
);
assert.ok(
  bilingualPrimaryPlan.subjectTerms.some((term) => /[A-Za-z]/.test(term)),
  'the primary subject group must retain an English term',
);
assert.equal(bilingualPrimaryPlan.featureQueryGroups.length, 2);
for (const group of bilingualPrimaryPlan.featureQueryGroups) {
  assert.ok(group.some((term) => /[\u3400-\u9fff]/.test(term)));
  assert.ok(group.some((term) => /[A-Za-z]/.test(term)));
}
assert.ok(
  bilingualPrimaryPlan.featureQueryGroups[1].includes('sound unit with through-hole'),
  'English terms must not be dropped after three Chinese synonyms fill the old fixed slice',
);
assert.match(bilingualPrimaryPlan.expression, /sound unit with through-hole/);
assert.match(bilingualPrimaryPlan.expression, /headband with two sound units/);
assert.doesNotMatch(bilingualPrimaryPlan.expression, /"headband and two sound units"/);
assert.equal(bilingualFallbackPlan.strategy, 'compact-fallback');
assert.deepEqual(bilingualFallbackPlan.featureTerms, ['发音单元', '中空孔']);
assert.notEqual(bilingualFallbackPlan.expression, bilingualPrimaryPlan.expression);
for (const group of bilingualFallbackPlan.featureQueryGroups) {
  assert.ok(group.some((term) => /[\u3400-\u9fff]/.test(term)));
  assert.ok(group.some((term) => /[A-Za-z]/.test(term)));
}
const subjectContainedFeatureRun = {
  module_run: {
    output_snapshot: {
      output: {
        technical_subject: '开放式头戴耳机',
        subject_synonyms_zh: ['开放式头戴式耳机'],
        subject_synonyms_en: ['open over-ear headphone'],
        limitations: [
          {
            feature_id: 'headband-feature',
            text: '头戴',
            mandatory: true,
            synonyms_zh: ['头戴结构', '头梁'],
            synonyms_en: ['headband', 'headband structure'],
          },
          {
            feature_id: 'through-hole-feature',
            text: '发音单元上设置有贯通耳侧与外侧的中空孔',
            mandatory: true,
            synonyms_zh: ['发音单元中空孔', '贯通中空孔'],
            synonyms_en: ['through-hole in sound unit'],
          },
        ],
        queries: [{
          query_id: 'subject-contained-feature-q1',
          provider_kind: 'patent',
          search_objective: 'full_claim_single_reference',
          date_channel: 'ordinary_prior_art',
          technical_subject: '开放式头戴耳机',
          feature_ids: ['headband-feature', 'through-hole-feature'],
          expression: '开放式头戴耳机 头戴 中空孔',
          language: 'zh',
        }],
        model: 'glm-4.6v',
        used_target_images: 2,
      },
    },
  },
};
const subjectContainedFeaturePlan = buildPatsnapSearchPlan(
  subjectContainedFeatureRun,
  '2024-04-29',
);
assert.deepEqual(
  subjectContainedFeaturePlan.featureTerms,
  ['头戴', '中空孔'],
  'a feature explicitly bound by feature_id must not be removed merely because its text occurs inside the subject',
);
assert.match(subjectContainedFeaturePlan.expression, /\(头戴 /);
assert.match(subjectContainedFeaturePlan.expression, /\(中空孔 /);
assert.doesNotMatch(subjectContainedFeaturePlan.expression, /headband headband/);
assert.doesNotMatch(subjectContainedFeaturePlan.expression, / OR structure(?: OR|\))/);
assert.ok(
  subjectContainedFeaturePlan.featureQueryGroups[1].every((term) => (
    /[\u3400-\u9fff]/.test(term) || /hole|perforat/i.test(term)
  )),
  'compact English terms must retain the distinguishing through-hole meaning',
);

const laterValidQueryRun = structuredClone(subjectContainedFeatureRun);
const laterValidOutput = (
  laterValidQueryRun.module_run.output_snapshot.output
);
laterValidOutput.queries = [
  {
    query_id: 'unmappable-feature-q0',
    provider_kind: 'patent',
    search_objective: 'full_claim_single_reference',
    date_channel: 'ordinary_prior_art',
    technical_subject: '开放式头戴耳机',
    feature_ids: ['unknown-feature'],
    expression: '开放式头戴耳机 中空孔',
    language: 'zh',
  },
  ...laterValidOutput.queries,
];
const laterValidPlan = buildPatsnapSearchPlan(laterValidQueryRun, '2024-04-29');
assert.equal(
  laterValidPlan.queryId,
  'subject-contained-feature-q1',
  'one unmappable query must not discard a later compliant I2 query',
);
const compactP002Plan = buildPatsnapSearchPlan({
  module_run: {
    output_snapshot: {
      output: {
        technical_subject: '开放式头戴耳机',
        limitations: [
          { feature_id: 'f2', text: '包括头戴和两个发音单元', mandatory: true },
          { feature_id: 'f3', text: '发音单元内安装有发音模组', mandatory: true },
        ],
        queries: [{
          query_id: 'compact-q1',
          provider_kind: 'patent',
          search_objective: 'full_claim_single_reference',
          date_channel: 'ordinary_prior_art',
          technical_subject: '开放式头戴耳机',
          feature_ids: ['f2', 'f3'],
          expression: '开放式头戴耳机 头戴 发音单元 发音模组',
          language: 'zh',
        }],
        model: 'glm-4.6v',
        used_target_images: 2,
      },
    },
  },
}, '2024-04-29', 10, 'compact-fallback');
assert.deepEqual(compactP002Plan.featureTerms, ['发音单元', '发音模组']);
assert.equal(
  (compactP002Plan.input.query as Record<string, unknown>).text,
  'TACD:((开放式头戴耳机) AND (发音单元 OR 发音单元结构) AND (发音模组 OR 发音模组结构))',
);
const singleFeatureTwoOfThreePlan = buildPatsnapSearchPlan({
    module_run: {
      output_snapshot: {
        output: {
          technical_subject: '开放式头戴耳机',
          limitations: [{
            feature_id: 'f5',
            text: '发音单元设有贯通耳侧与外侧的中空孔',
            mandatory: true,
          }],
          queries: [{
            query_id: 'single-feature-q1',
            provider_kind: 'patent',
            search_objective: 'full_claim_single_reference',
            date_channel: 'ordinary_prior_art',
            technical_subject: '开放式头戴耳机',
            feature_ids: ['f5'],
            expression: '开放式头戴耳机 中空孔 外侧',
            language: 'zh',
          }],
          model: 'glm-4.6v',
          used_target_images: 2,
        },
      },
    },
  }, '2024-04-29');
assert.equal(singleFeatureTwoOfThreePlan.queryId, 'single-feature-q1');
assert.match(singleFeatureTwoOfThreePlan.expression, /开放式头戴耳机/);
assert.match(singleFeatureTwoOfThreePlan.expression, /中空孔/);

const suggestedClassificationTwoOfThreePlan = buildPatsnapSearchPlan({
  module_run: {
    output_snapshot: {
      output: {
        technical_subject: '水枪',
        limitations: [{
          feature_id: 'f-coupling',
          text: '本体和阀杆经由位置选择性联接装置联接',
          mandatory: true,
        }],
        queries: [{
          query_id: 'suggested-classification-q1',
          provider_kind: 'patent',
          search_objective: 'full_claim_single_reference',
          date_channel: 'ordinary_prior_art',
          technical_subject: '水枪',
          query_role: 'inventive_point_precision',
          search_scope: 'full_text',
          classification_anchors: ['F16K 1/00'],
          classification_anchor_sources: { 'F16K1/00': 'model_suggested' },
          feature_ids: ['f-coupling'],
          feature_terms: ['位置选择性联接装置'],
          expression: 'F16K 1/00 AND 位置选择性联接装置',
          language: 'zh',
        }],
        model: 'glm-4.6v',
        used_target_images: 2,
      },
    },
  },
}, '2010-01-01', 10, 'balanced', 'inventive_point_precision');
assert.deepEqual(suggestedClassificationTwoOfThreePlan.subjectTerms, []);
assert.deepEqual(suggestedClassificationTwoOfThreePlan.classificationAnchors, ['F16K1/00']);
assert.match(suggestedClassificationTwoOfThreePlan.expression, /IPC:\(F16K1\/00\)/);
assert.match(suggestedClassificationTwoOfThreePlan.expression, /位置选择性联接装置/);
const p002Result = patsnapSearchResult({
  module_run: {
    output_snapshot: {
      output: {
        actual_provider: 'patsnap',
        network_used: true,
        count: 1,
        documents: [{
          external_id: 'patent-id-1',
          publication_number: 'CN1234567A',
          title: '候选专利',
          authority: 'CN',
          publication_date: '2020-01-01',
          filing_date: '2019-01-01',
          stage: 'lead',
          raw_metadata: { current_assignee: '测试申请人' },
          provenance: { total_result_count: 25 },
        }],
        search_artifacts: [{ kind: 'provider_search_response', sha256: 'a'.repeat(64), byte_size: 321 }],
      },
    },
  },
});
assert.equal(p002Result.actualProvider, 'patsnap');
assert.equal(p002Result.networkUsed, true);
assert.equal(p002Result.totalResultCount, 25);
assert.equal(p002Result.candidates[0]?.stage, 'lead');

assert.equal(
  invalidityDateClassification({ category: 'ordinary_prior_art' }).label,
  '现有技术',
);
assert.equal(
  invalidityDateClassification({
    category: 'conflicting_application_candidate',
  }).label,
  '抵触申请',
);
assert.equal(
  invalidityDateClassification({ category: 'post_date_lead' }).label,
  '非现有技术',
);
assert.equal(
  invalidityDateClassification({
    category: 'unknown',
    requires_human_review: true,
  }).label,
  '日期无法确定',
);
assert.equal(
  invalidityDateClassification({
    novelty_eligible: true,
    inventive_step_eligible: false,
  }).label,
  '抵触申请',
);
assert.equal(invalidityDateClassification(undefined).label, '日期无法确定');

assert.equal(isInvalidityTerminalStatus('needs_human_review'), true);
assert.equal(isInvalidityTerminalStatus('search_budget_exhausted'), true);
assert.equal(isInvalidityTerminalStatus('exhausted'), true);
assert.equal(isInvalidityTerminalStatus('running'), false);
assert.equal(invalidityPortalSessionStatus('running'), 'running');
assert.equal(invalidityPortalSessionStatus('search_budget_exhausted'), 'completed');
assert.equal(invalidityPortalSessionStatus('exhausted'), 'completed');
assert.equal(invalidityPortalSessionStatus('failed'), 'error');
assert.equal(isInvalidityClaimContinuable('needs_human_review'), true);
assert.equal(isInvalidityClaimContinuable('exhausted'), true);
assert.equal(isInvalidityClaimContinuable('cancelled'), false);
assert.equal(isInvalidityClaimContinuable('novelty_evidence_complete'), false);
assert.equal(isInvalidityClaimContinuable('inventive_step_evidence_complete'), false);
assert.equal(isInvaliditySuccessfulClaimTerminal('novelty_evidence_complete'), true);
assert.equal(isInvaliditySuccessfulClaimTerminal('inventive_step_evidence_complete'), true);
assert.equal(invalidityReportPendingMessage({ code: 'REPORT_PENDING', message: '尚未生成' }), '尚未生成');
assert.equal(invalidityReportPendingMessage({ code: 'OTHER' }), null);
assert.equal(needsInvalidityCriticalDateConfirmation({ status: 'critical_date_review', critical_date: '2020-01-01', target_publication_date: '2021-01-01' }), true);
assert.equal(needsInvalidityCriticalDateConfirmation({ status: 'running', critical_date: '2020-01-01', target_publication_date: '2021-01-01' }), false);
assert.equal(invalidityAutomaticRounds(undefined), 5);
assert.equal(invalidityAutomaticRounds(1), 1);
assert.equal(invalidityAutomaticRounds(5), 5);
assert.throws(() => invalidityAutomaticRounds(6), /1 到 5/);
assert.throws(() => invalidityAutomaticRounds(2.5));
assert.equal(claimDisplayLabel({ claim_id: '7' }), '权利要求 7');
assert.equal(claimDisplayLabel({ claim_id: '权利要求 8' }), '权利要求 8');

const reviewContext = {
  contract_version: 'v1',
  investigation_id: '11111111-1111-4111-8111-111111111111',
  status: 'needs_human_review',
  state_version: 9,
  review_revision: 3,
  quiescent: true,
  recomputation_required: false,
  claims: [{ id: 'claim-a', claim_id: '1', state_version: 4 }],
  documents: [{
    id: 'doc-a',
    current_document_version_id: 'doc-version-2',
    current_version_no: 2,
    current_content_sha256: 'a'.repeat(64),
    current_mime_type: 'application/pdf',
    canonical_key: 'patsnap:US20080245714A1',
    document_type: 'patent',
    title: 'D1 candidate',
    identifiers: { publication_number: 'US20080245714A1' },
    dates: { publication_date: '2008-10-09', filing_date: '2004-11-25' },
  }, {
    id: 'doc-b',
    current_document_version_id: 'doc-version-b',
    canonical_key: 'patsnap:CN000000001A',
    document_type: 'patent',
    title: 'verified document',
  }],
  document_versions: [
    { id: 'doc-version-1', document_id: 'doc-a', version_no: 1 },
    { id: 'doc-version-2', document_id: 'doc-a', version_no: 2 },
    { id: 'doc-version-b', document_id: 'doc-b', version_no: 1 },
  ],
  latest_qualifications: [{
    id: 'qualification-2',
    document_id: 'doc-a',
    document_version_id: 'doc-version-2',
    claim_investigation_id: 'claim-a',
    assessment_version: 2,
    public_availability_date: '2008-10-09',
    publication_date: '2008-10-09',
    filing_date: '2004-11-25',
    eligibility_type: 'unknown',
    verification_status: 'needs_human_review',
    verification_reason: '公开日的冻结证据链尚未通过格式校验',
  }, {
    id: 'qualification-b',
    document_id: 'doc-b',
    document_version_id: 'doc-version-b',
    claim_investigation_id: 'claim-a',
    assessment_version: 1,
    verification_status: 'verified',
  }],
  date_fact_revisions: [],
  current_gaps: [],
  current_d1: [],
  action_history: [],
};
assertInvalidityReviewContext(reviewContext);
assert.equal(
  invalidityReviewCurrentDocumentVersionId(reviewContext, reviewContext.documents[0]),
  'doc-version-2',
);
assert.equal(
  invalidityReviewQualificationsByClaim(reviewContext, reviewContext.documents[0]).get('claim-a')?.id,
  'qualification-2',
  'flat latest_qualifications must enable the matching claim in the date review UI',
);
assert.deepEqual(
  invalidityPendingDateReviewDocuments(reviewContext).map((document) => document.id),
  ['doc-a'],
  'the Agent date review must hide already verified documents',
);
assert.deepEqual(
  invalidityDateReviewDefaults(reviewContext, reviewContext.documents[0]),
  {
    publicAvailabilityDate: '2008-10-09',
    publicationDate: '2008-10-09',
    filingDate: '2004-11-25',
    priorityDate: '',
    publicationNumber: 'US20080245714A1',
    authority: 'US',
    sourceType: 'patent',
    dateEvidenceSource: '当前冻结专利 PDF（系统已提取著录信息，待律师核对）',
    dateEvidenceLocator: `第 1 页著录项目（Pub. Date / Filing Date / (43) / (45) / (22)）；文献版本 v2，SHA-256 ${'a'.repeat(64)}`,
    reason: '系统已回填检测到的文献身份和日期；本次仅核对这些事实与冻结原件是否一致',
    pendingClaimIds: ['claim-a'],
    verificationReasons: ['公开日的冻结证据链尚未通过格式校验'],
  },
);
assert.throws(
  () => assertInvalidityReviewContext({ ...reviewContext, review_revision: undefined }),
  /review_revision/,
);
const queuedReviewAction = {
  contract_version: 'v1',
  review_action_id: 'review-1',
  action_type: 'evidence_import',
  status: 'queued',
  investigation_state_version: 10,
  claim_state_versions: { 'claim-a': 5 },
  review_revision: 4,
  module_run_id: 'run-1',
  job_id: 'job-1',
  invalidated_derivations: ['report'],
  pending_recomputation: ['I4-S'],
  idempotent_replay: false,
  upload_receipt: {
    sha256: 'b'.repeat(64),
    byte_size: 1024,
    mime_type: 'application/pdf',
  },
};
assertHumanReviewCommandResponse(queuedReviewAction);
assert.equal(humanReviewUploadReceiptMatches(queuedReviewAction, {
  sha256: 'b'.repeat(64),
  byteSize: 1024,
  mimeType: 'application/pdf',
}), true);
assert.equal(humanReviewUploadReceiptMatches(queuedReviewAction, {
  sha256: 'c'.repeat(64),
  byteSize: 1024,
  mimeType: 'application/pdf',
}), false);
assert.throws(
  () => assertHumanReviewCommandResponse({ ...queuedReviewAction, status: 'applied' }),
  /失效范围或幂等标记/,
);
assert.throws(
  () => assertHumanReviewCommandResponse({ ...queuedReviewAction, pending_recomputation: undefined }),
  /失效范围或幂等标记/,
);
assert.deepEqual(
  latestInvalidityGapsByKey([
    { id: 'gap-v1', claim_investigation_id: 'claim-a', gap_key: 'feature:A', version_no: 1, status: 'open' },
    { id: 'gap-v2', claim_investigation_id: 'claim-a', gap_key: 'feature:A', version_no: 2, status: 'closed' },
    { id: 'gap-b', claim_investigation_id: 'claim-b', gap_key: 'date:B', version_no: 1, status: 'open' },
  ]).map((item) => item.id).sort(),
  ['gap-b', 'gap-v2'],
);
const stableIntent = createStableInvalidityIntent('contract-review');
const firstIntentKey = stableIntent.key('{"decision":"confirm"}');
assert.equal(stableIntent.key('{"decision":"confirm"}'), firstIntentKey);
assert.notEqual(stableIntent.key('{"decision":"exclude"}'), firstIntentKey);

const criticalDateConfirmation = {
  contract_version: 'v1' as const,
  confirmation_id: 'confirmation-1',
  claim_investigation_id: 'claim-a',
  confirmed_date: '2019-12-31',
  target_publication_date: '2020-06-30',
  critical_date_basis: '已核验优先权日',
  claim_state_version: 5,
  investigation_state_version: 8,
  investigation_status: 'needs_human_review',
  continuation_required: true,
};
assertCriticalDateConfirmationResponse(criticalDateConfirmation);
assert.throws(
  () => assertCriticalDateConfirmationResponse({
    ...criticalDateConfirmation,
    investigation_state_version: undefined,
  }),
  /investigation_state_version/,
);
const mergedCriticalDate = mergeCriticalDateConfirmation({
  investigation: { id: 'inv-1', status: 'running', state_version: 7 },
  claim_investigations: [{ id: 'claim-a', state_version: 4, status: 'awaiting_critical_date' }],
}, criticalDateConfirmation);
assert.equal((mergedCriticalDate.investigation as Record<string, unknown>).state_version, 8);
assert.deepEqual(mergedCriticalDate.claim_investigations, [{
  id: 'claim-a',
  state_version: 5,
  status: 'awaiting_critical_date',
  critical_date: '2019-12-31',
  target_publication_date: '2020-06-30',
  critical_date_basis: '已核验优先权日',
}]);

assert.match(liveLabPayloadViolation({ fixture_output: { ok: true } }) || '', /fixture_output/);
assert.match(liveLabPayloadViolation({ nested: { simulated: true } }) || '', /simulated/);
assert.match(liveLabPayloadViolation({ fixture_output: null }) || '', /fixture_output/);
assert.match(liveLabPayloadViolation({ provider_mode: 'manual' }) || '', /manual/);
assert.match(liveLabPayloadViolation({ provider: 'manual_import' }) || '', /manual/);
assert.match(liveLabPayloadViolation({ model_version: 'fixture-no-model' }) || '', /fixture/);
assert.equal(liveLabPayloadViolation({ provider_mode: 'live', query: 'real source' }), null);
assert.equal(
  liveLabPayloadViolation({
    search_provider: 'patsnap',
    search_modality: 'image_single',
    image_url: 'https://static-open.zhihuiya.com/sample/common_demo.png',
    patent_type: 'D',
    model: 1,
    max_results: 10,
  }),
  null,
);
assert.equal(
  liveLabPayloadViolation({
    search_provider: 'patsnap',
    search_modality: 'image_multiple',
    image_urls: [
      'https://static-open.zhihuiya.com/sample/common_demo.png',
      'https://static-open.zhihuiya.com/sample/common_demo_2.png',
    ],
    patent_type: 'D',
    model: 1,
    max_results: 10,
  }),
  null,
);

const report = {
  contract_version: 'v1',
  report_kind: 'invalidity_evidence_data',
  generated_at: '2026-07-19T12:34:56.000Z',
  is_preview: false,
  source_state_version: 4,
  source_review_revision: 3,
  snapshot_hash_scope: 'canonical_persisted_report_data_v1_excluding_snapshot_sha256',
  snapshot_sha256: 'a'.repeat(64),
  report_snapshot_id: 'snapshot-1',
  report_sha256: 'd'.repeat(64),
  investigation: {
    id: 'inv-1',
    analysis_session_id: 'invalidity-1',
    environment: 'prod',
    status: 'partial',
    pipeline_version: 'workflow-v1',
    state_version: 4,
    review_revision: 3,
    created_at: '2026-07-19T10:00:00.000Z',
    completed_at: '2026-07-19T12:34:56.000Z',
  },
  claim_investigations: [
    { id: 'claim-a', claim_id: '1', status: 'novelty_evidence_complete', critical_date: '2020-01-01', current_iteration_no: 2, terminal_reason: 'single_reference_full_coverage' },
    { id: 'claim-b', claim_id: '2', status: 'needs_human_review', critical_date: '2021-01-01', current_iteration_no: 1 },
  ],
  claim_limitations: [
    { id: 'lim-a', claim_investigation_id: 'claim-a', feature_key: '1A', limitation_text: '特征 A' },
    { id: 'lim-b', claim_investigation_id: 'claim-a', feature_key: '1B', limitation_text: '特征 B' },
  ],
  documents: [
    { id: 'doc-1', title: '正式最接近现有技术', canonical_key: 'DOC-1', document_type: 'patent', evidence_level: 'qualified_evidence', similarity_score: 0.4 },
    { id: 'doc-2', title: '高相似度候选（非正式 D1）', canonical_key: 'DOC-2', document_type: 'patent', evidence_level: 'qualified_evidence', similarity_score: 0.99 },
  ],
  document_sources: [
    { document_id: 'doc-1', provider: 'google_patents', source_url: 'https://patents.example/doc-1', snapshot_sha256: 'b'.repeat(64) },
    { document_id: 'doc-2', provider: 'google_patents', source_url: 'https://patents.example/doc-2', snapshot_sha256: 'c'.repeat(64) },
  ],
  document_qualifications: [
    { id: 'q-a', document_id: 'doc-1', claim_investigation_id: 'claim-a', assessment_version: 1, critical_date: '2020-01-01', publication_date: '2019-01-01', eligibility_type: 'ordinary_prior_art', novelty_eligible: true, inventive_step_eligible: true, verification_status: 'verified', verification_reason: '公开在先' },
    { id: 'q-b', document_id: 'doc-1', claim_investigation_id: 'claim-b', assessment_version: 2, critical_date: '2021-01-01', publication_date: '2022-01-01', eligibility_type: 'lead_only', novelty_eligible: false, inventive_step_eligible: false, verification_status: 'excluded', verification_reason: '关键日后公开' },
    { id: 'q-c', document_id: 'doc-2', claim_investigation_id: 'claim-a', assessment_version: 1, critical_date: '2020-01-01', publication_date: '2018-01-01', eligibility_type: 'ordinary_prior_art', novelty_eligible: true, inventive_step_eligible: true, verification_status: 'verified', verification_reason: '公开在先' },
  ],
  feature_disclosures: [
    { id: 'disc-1', claim_investigation_id: 'claim-a', limitation_id: 'lim-a', document_id: 'doc-1', disclosure_status: 'explicit', excerpt: '文献一原文', locator: { page: 3 }, rule_version: 'r1' },
    { id: 'disc-2', claim_investigation_id: 'claim-a', limitation_id: 'lim-b', document_id: 'doc-2', disclosure_status: 'necessarily_implicit', excerpt: '文献二原文', locator: { page: 4 }, rule_version: 'r1' },
    { id: 'disc-3', claim_investigation_id: 'claim-a', limitation_id: 'lim-b', document_id: 'doc-1', disclosure_status: 'direct_and_unambiguous', excerpt: '文献一另一段原文', locator: { page: 5 }, rule_version: 'r1' },
  ],
  closest_prior_art_versions: [
    { id: 'd1-v1', claim_investigation_id: 'claim-a', iteration_id: 'it-1', document_id: 'doc-1', version_no: 1, is_current: true, selected_by: 'all_limitations_review', metrics: { limitation_coverage: 0.5 }, rationale: { reason: '完成日期资格和逐限制比对后选择' }, created_at: '2026-07-19T11:00:00Z' },
  ],
  iterations: [
    { id: 'it-1', claim_investigation_id: 'claim-a', iteration_no: 1, purpose: 'initial', status: 'completed', stop_reason: 'gap remains', completed_at: '2026-07-19T11:30:00Z' },
  ],
  module_runs: [
    { id: 'run-1', module_code: 'I3_PATENT_SEARCH', status: 'failed', attempt_no: 2, retryable: true, error_code: 'provider_error', error_message: 'provider failed' },
  ],
  jobs: [
    {
      id: 'job-1', module_run_id: 'run-1', status: 'failed', attempt_count: 2, max_attempts: 2,
      last_error: {
        code: 'timeout', request_sha256: 'request-secret-hash',
        source_path: '/Users/private/report-input.pdf', artifact_uri: 'file:///private/report.bin',
        import_metadata: { backend_path: '/private/import.pdf', idempotency_key_sha256: 'idem-secret-hash' },
      },
    },
  ],
  queries: [],
  events: [{
    id: 'event-1', event_type: 'human_review.queued', occurred_at: '2026-07-19T11:01:00Z',
    payload: {
      request_hash: 'request-event-hash',
      idempotency_key_hash: 'idem-event-hash',
      cache_uri: 'file:///Users/private/cache.json',
      import_metadata: { source_path: '/private/source.pdf' },
      public_note: 'kept',
    },
  }],
  gap_items: [],
  combinations: [
    {
      id: 'combination-1',
      claim_investigation_id: 'claim-a',
      iteration_id: 'it-1',
      closest_prior_art_version_id: 'd1-v1',
      document_ids: ['doc-1', 'doc-2'],
      coverage_complete: true,
      motivation_status: 'supported',
      status: 'qualified',
      analysis: { combination_motivation: { status: 'supported', evidence: '组合启示证据' } },
    },
  ],
  document_versions: [{
    id: 'doc-v1', document_id: 'doc-1', version_no: 1, content_sha256: 'e'.repeat(64),
    mime_type: 'application/pdf', byte_size: 1234, acquisition_kind: 'human_import',
    created_by: 'user:7', created_at: '2026-07-19T10:58:00Z',
  }],
  human_review_actions: [{
    id: 'review-1', action_seq: 1, action_type: 'evidence_import', status: 'completed',
    actor: 'user:7', reason: '补充真实 PDF', base_investigation_state_version: 3,
    resulting_investigation_state_version: 4, base_claim_state_versions: { 'claim-a': 2 },
    resulting_claim_state_versions: { 'claim-a': 3 }, base_review_revision: 2,
    resulting_review_revision: 3, invalidated_derivations: ['report'],
    pending_recomputation: [], result_summary: { document_version_id: 'doc-v1' },
    error_summary: null, created_at: '2026-07-19T10:57:00Z', completed_at: '2026-07-19T10:59:00Z',
    idempotency_key_sha256: 'must-not-export', request_sha256: 'must-not-export',
  }],
  document_date_fact_revisions: [{
    id: 'date-rev-1', review_action_id: 'review-1', document_id: 'doc-1', document_version_id: 'doc-v1',
    revision_no: 1, supersedes_id: null, decision: 'confirm_facts', claim_investigation_ids: ['claim-a'],
    publication_date: '2019-01-01', date_channel: 'ordinary_prior_art', source_type: 'official_pdf',
    actor: 'user:7', reason: '核验扫描件', created_at: '2026-07-19T10:59:00Z',
  }],
  evidence_imports: [{
    id: 'import-1', review_action_id: 'review-1', document_id: 'doc-1', document_version_id: 'doc-v1',
    document_source_id: 'source-1', content_artifact_id: 'artifact-1', claim_investigation_ids: ['claim-a'],
    declared_date_facts: { publication_date: '2019-01-01' }, actor: 'user:7', reason: '补充真实 PDF',
    created_at: '2026-07-19T10:58:00Z', import_metadata: { source_path: '/private/import.pdf' },
  }],
};

assertInvalidityReportDataV1(report);
assert.equal(reportSnapshotDate(report).toISOString(), report.generated_at);
assert.throws(() => assertInvalidityReportDataV1({ ...report, contract_version: 'v2' }), /不支持/);
assert.throws(() => assertInvalidityReportDataV1({ ...report, report_kind: 'other' }), /类型不匹配/);
assert.throws(
  () => assertInvalidityReportDataV1({ ...report, snapshot_hash_scope: 'preview_not_hashed' }),
  /哈希范围/,
);
assert.throws(
  () => assertInvalidityReportDataV1({ ...report, snapshot_sha256: null }),
  /SHA-256/,
);
assert.throws(
  () => assertInvalidityReportDataV1({ ...report, source_state_version: undefined }),
  /source_state_version/,
);
assert.throws(
  () => assertInvalidityReportDataV1({ ...report, source_review_revision: 2 }),
  /source_review_revision.*不一致/,
);
assert.throws(
  () => assertInvalidityReportDataV1({
    ...report,
    investigation: { ...report.investigation, review_revision: undefined },
  }),
  /investigation\.review_revision/,
);

const resultsPageSource = readFileSync('src/app/invalidity/results/page.tsx', 'utf8');
assert.doesNotMatch(resultsPageSource, /setInterval\s*\(/, 'invalidity polling must not overlap through setInterval');
assert.match(
  resultsPageSource,
  /if \(shouldContinue\) timer = window\.setTimeout\(\(\) => void poll\(\), 4000\)/,
  'the next poll must be scheduled only after the current request has settled',
);
assert.match(
  resultsPageSource,
  /claims\.filter\(\(claim\) => isInvalidityClaimContinuable\(claim\.status\)\)/,
  'continuation controls must share the contract-level continuable-status gate',
);
const continuationRouteSource = readFileSync(
  'src/app/api/invalidity/session/[id]/continuations/route.ts',
  'utf8',
);
assert.match(
  continuationRouteSource,
  /isInvaliditySuccessfulClaimTerminal/,
  'the Portal continuation API must reject successful claim terminals',
);

async function main() {
  const investigationId = '11111111-1111-4111-8111-111111111111';
  const moduleRunId = '22222222-2222-4222-8222-222222222222';
  assert.deepEqual(invalidityTestProxyOperation('GET', ['health']), { kind: 'health' });
  assert.deepEqual(
    invalidityTestProxyOperation('POST', ['v1', 'investigations']),
    { kind: 'create_investigation' },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('POST', ['v1', 'investigations', investigationId, 'start']),
    { kind: 'start_investigation', investigationId },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('GET', ['v1', 'investigations', investigationId]),
    { kind: 'read_investigation', investigationId },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('GET', ['v1', 'investigations', investigationId, 'report-data']),
    { kind: 'read_report_data', investigationId },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('GET', ['v1', 'investigations', investigationId, 'review-context']),
    { kind: 'read_review_context', investigationId },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('POST', ['v1', 'investigations', investigationId, 'human-reviews', 'evidence-imports']),
    { kind: 'import_evidence', investigationId },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('POST', ['v1', 'investigations', investigationId, 'human-reviews', 'document-date-confirmations']),
    { kind: 'confirm_document_date', investigationId },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('POST', ['v1', 'lab', 'module-runs']),
    { kind: 'create_module_run' },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('GET', ['v1', 'lab', 'module-runs', moduleRunId]),
    { kind: 'read_module_run', moduleRunId },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('POST', ['v1', 'lab', 'module-runs', moduleRunId, 'cancel']),
    { kind: 'cancel_module_run', moduleRunId },
  );
  assert.deepEqual(
    invalidityTestProxyOperation('POST', ['v1', 'lab', 'module-runs', moduleRunId, 'retries']),
    { kind: 'retry_module_run', moduleRunId },
  );
  assert.equal(
    invalidityTestProxyOperation('GET', ['v1', 'investigations', investigationId, 'claims']),
    null,
    'an owned resource must not turn the proxy into an arbitrary /v1 tunnel',
  );
  assert.equal(invalidityTestProxyOperation('GET', ['v1', 'users']), null);
  assert.equal(
    invalidityTestProxyOperation('POST', ['v1', 'lab', 'module-runs', moduleRunId, 'delete']),
    null,
  );
  assert.equal(invalidityTestProxyOperation('PATCH', ['v1', 'investigations', investigationId]), null);
  assert.equal(invalidityTestProxyOperation('GET', ['v1', 'investigations', 'not-a-uuid']), null);

  const userOneScope = scopedInvalidityTestIdentifier('module-key', 101, 'same-client-key');
  assert.equal(userOneScope, scopedInvalidityTestIdentifier('module-key', 101, 'same-client-key'));
  assert.notEqual(userOneScope, scopedInvalidityTestIdentifier('module-key', 202, 'same-client-key'));
  assert.doesNotMatch(userOneScope, /same-client-key/);

  const ownershipRows = new Map<string, InvalidityTestResourceOwner>();
  const ownershipRepository: InvalidityTestResourceOwnershipRepository = {
    async insert(owner) {
      const key = `${owner.resourceKind}:${owner.resourceId}`;
      if (!ownershipRows.has(key)) ownershipRows.set(key, { ...owner });
    },
    async find(resourceKind, resourceId) {
      return ownershipRows.get(`${resourceKind}:${resourceId}`) || null;
    },
  };
  const ownership = new InvalidityTestResourceOwnership(ownershipRepository);
  await ownership.bind({
    resourceKind: 'investigation',
    resourceId: investigationId,
    userId: 101,
    investigationId: null,
  });
  assert.equal((await ownership.require('investigation', investigationId, 101)).userId, 101);
  const indistinguishableNotFound = (error: unknown) => (
    error instanceof InvalidityTestResourceNotFoundError
    && error.status === 404
    && error.message === '测试资源不存在'
  );
  await assert.rejects(
    () => ownership.require('investigation', investigationId, 202),
    indistinguishableNotFound,
    'a foreign resource must look identical to an unmapped resource',
  );
  await assert.rejects(
    () => ownership.require('investigation', '33333333-3333-4333-8333-333333333333', 202),
    indistinguishableNotFound,
  );
  await assert.rejects(
    () => ownership.bind({
      resourceKind: 'investigation',
      resourceId: investigationId,
      userId: 202,
      investigationId: null,
    }),
    indistinguishableNotFound,
    'idempotent backend replay must not transfer ownership',
  );
  await ownership.bind({
    resourceKind: 'module_run',
    resourceId: moduleRunId,
    userId: 101,
    investigationId,
  });
  assert.equal(
    (await ownership.require('module_run', moduleRunId, 101, investigationId)).investigationId,
    investigationId,
  );
  assert.equal(
    (await ownership.requireModuleRunWithInvestigation(moduleRunId, 101)).investigationId,
    investigationId,
  );
  await assert.rejects(
    () => ownership.require(
      'module_run',
      moduleRunId,
      101,
      '44444444-4444-4444-8444-444444444444',
    ),
    indistinguishableNotFound,
    'module-run association is immutable and must be checked',
  );

  const temporaryProjectRoot = await realpath(
    await mkdtemp(path.join(tmpdir(), 'invalidity-portal-isolation-')),
  );
  const testRoot = path.join(temporaryProjectRoot, '5-invalidity-search-test', '.data', 'uploads', 'test');
  const prodRoot = path.join(temporaryProjectRoot, '5-invalidity-search-prod', '.data', 'uploads', 'prod');
  const uploadEnvironment = {
    INVALIDITY_TEST_UPLOAD_ROOT: testRoot,
    INVALIDITY_PROD_UPLOAD_ROOT: prodRoot,
  };
  try {
    assert.throws(
      () => configuredInvalidityUploadRoots({
        environment: { INVALIDITY_TEST_UPLOAD_ROOT: testRoot },
        projectRoot: temporaryProjectRoot,
      }),
      /INVALIDITY_PROD_UPLOAD_ROOT/,
    );
    assert.throws(
      () => configuredInvalidityUploadRoots({
        environment: {
          INVALIDITY_TEST_UPLOAD_ROOT: testRoot,
          INVALIDITY_PROD_UPLOAD_ROOT: testRoot,
        },
        projectRoot: temporaryProjectRoot,
      }),
      /不能相同或互相嵌套/,
    );
    assert.throws(
      () => configuredInvalidityUploadRoots({
        environment: {
          INVALIDITY_TEST_UPLOAD_ROOT: testRoot,
          INVALIDITY_PROD_UPLOAD_ROOT: path.join(testRoot, 'prod'),
        },
        projectRoot: temporaryProjectRoot,
      }),
      /不能相同或互相嵌套/,
    );
    assert.deepEqual(
      configuredInvalidityUploadRoots({
        environment: uploadEnvironment,
        projectRoot: temporaryProjectRoot,
      }),
      { test: testRoot, prod: prodRoot },
    );

    await prepareInvalidityUploadRoot('test', {
      environment: { INVALIDITY_TEST_UPLOAD_ROOT: testRoot },
      projectRoot: temporaryProjectRoot,
      create: true,
    });
    await assert.rejects(
      () => prepareInvalidityUploadRoot('prod', {
        environment: { INVALIDITY_TEST_UPLOAD_ROOT: testRoot },
        projectRoot: temporaryProjectRoot,
        create: true,
      }),
      /INVALIDITY_PROD_UPLOAD_ROOT/,
      'a test-only Portal configuration must work while the prod route remains fail-closed',
    );

    await prepareInvalidityUploadRoot('test', {
      environment: uploadEnvironment,
      projectRoot: temporaryProjectRoot,
      create: true,
    });
    await prepareInvalidityUploadRoot('prod', {
      environment: uploadEnvironment,
      projectRoot: temporaryProjectRoot,
      create: true,
    });
    const testKey = 'invalidity-test-contract-patent.txt';
    const testFile = path.join(testRoot, testKey);
    await writeFile(testFile, 'test patent', 'utf8');
    assert.equal(
      await resolveInvalidityUploadPath('test', testKey, {
        environment: uploadEnvironment,
        projectRoot: temporaryProjectRoot,
      }),
      testFile,
    );
    await assert.rejects(
      () => resolveInvalidityUploadPath('prod', testKey, {
        environment: uploadEnvironment,
        projectRoot: temporaryProjectRoot,
      }),
      /不属于 prod/,
    );

    const prodTarget = path.join(prodRoot, 'invalidity-prod-private.txt');
    await writeFile(prodTarget, 'prod patent', 'utf8');
    const escapedTestKey = 'invalidity-test-symbolic-link.txt';
    await symlink(prodTarget, path.join(testRoot, escapedTestKey));
    await assert.rejects(
      () => resolveInvalidityUploadPath('test', escapedTestKey, {
        environment: uploadEnvironment,
        projectRoot: temporaryProjectRoot,
      }),
      /符号链接/,
    );

    const linkedRootProject = path.join(temporaryProjectRoot, 'linked-root-case');
    const linkedTarget = path.join(temporaryProjectRoot, 'linked-target');
    await mkdir(path.join(linkedRootProject, '5-invalidity-search-test', '.data', 'uploads'), { recursive: true });
    await mkdir(path.join(linkedRootProject, '5-invalidity-search-prod', '.data', 'uploads', 'prod'), { recursive: true });
    await mkdir(linkedTarget, { recursive: true });
    const linkedTestRoot = path.join(linkedRootProject, '5-invalidity-search-test', '.data', 'uploads', 'test');
    await symlink(linkedTarget, linkedTestRoot);
    await assert.rejects(
      () => prepareInvalidityUploadRoot('test', {
        environment: {
          INVALIDITY_TEST_UPLOAD_ROOT: linkedTestRoot,
          INVALIDITY_PROD_UPLOAD_ROOT: path.join(
            linkedRootProject,
            '5-invalidity-search-prod',
            '.data',
            'uploads',
            'prod',
          ),
        },
        projectRoot: linkedRootProject,
      }),
      /符号链接/,
    );
  } finally {
    await rm(temporaryProjectRoot, { recursive: true, force: true });
  }

  const uploadRouteSource = readFileSync('src/app/api/invalidity/uploads/route.ts', 'utf8');
  const invalidityServiceSource = readFileSync('src/lib/invalidity-service.ts', 'utf8');
  const formalPageSource = readFileSync('src/app/invalidity/page.tsx', 'utf8');
  const formalInvestigationSource = readFileSync('src/app/api/invalidity/investigations/route.ts', 'utf8');
  const testPageSource = readFileSync('src/app/test/invalidity-pipeline/page.tsx', 'utf8');
  const p002TestPageSource = readFileSync('src/app/test/invalidity/page.tsx', 'utf8');
  const p002TestFlowSource = readFileSync('src/lib/invalidity-p002-test-flow.ts', 'utf8');
  const portalHomeSource = readFileSync('src/app/page.tsx', 'utf8');
  const moduleLabAliasSource = readFileSync('src/app/test/module-lab/page.tsx', 'utf8');
  const benchmarkCasesRouteSource = readFileSync(
    'src/app/api/test/invalidity/benchmark-cases/route.ts',
    'utf8',
  );
  const earphoneObviousnessFixture = readFileSync(
    '../5-invalidity-search-test/tests/fixtures/obviousness_precheck/'
      + 'wireless-earphone-decision-2022215166391/claim-1.json',
    'utf8',
  );
  const module5BatchRouteSource = readFileSync(
    'src/app/api/test/invalidity/module5-batches/route.ts',
    'utf8',
  );
  const module9BatchRouteSource = readFileSync(
    'src/app/api/test/invalidity/module9-batches/route.ts',
    'utf8',
  );
  const formalResultsSource = readFileSync('src/app/invalidity/results/page.tsx', 'utf8');
  const testProxySource = readFileSync('src/app/api/test/invalidity/[...path]/route.ts', 'utf8');
  const moduleFigureProxySource = readFileSync(
    'src/app/api/test/invalidity/module-figure/[moduleRunId]/[figureIndex]/route.ts',
    'utf8',
  );
  const databaseInitSource = readFileSync('src/lib/db-init.ts', 'utf8');
  const reviewPanelSource = readFileSync('src/components/invalidity-review-panel.tsx', 'utf8');
  const agentReviewSource = readFileSync('src/components/agent-invalidity-review.tsx', 'utf8');
  const reviewContextRouteSource = readFileSync(
    'src/app/api/invalidity/session/[id]/review-context/route.ts',
    'utf8',
  );
  const evidenceImportRouteSource = readFileSync(
    'src/app/api/invalidity/session/[id]/evidence-imports/route.ts',
    'utf8',
  );
  const documentDateRouteSource = readFileSync(
    'src/app/api/invalidity/session/[id]/document-date-confirmations/route.ts',
    'utf8',
  );
  const evidenceUploadRouteSource = readFileSync(
    'src/app/api/invalidity/session/[id]/evidence-uploads/route.ts',
    'utf8',
  );
  const testEvidenceUploadRouteSource = readFileSync(
    'src/app/api/test/invalidity/evidence-uploads/route.ts',
    'utf8',
  );
  const uploadReceiptSource = readFileSync('src/lib/invalidity-upload-receipts.ts', 'utf8');
  const invalidityExportRouteSource = readFileSync(
    'src/app/api/invalidity/session/[id]/export/route.ts',
    'utf8',
  );
  for (const routePath of [
    'src/app/api/analysis/[id]/route.ts',
    'src/app/api/analysis/[id]/export/route.ts',
    'src/app/api/analysis/[id]/keywords/route.ts',
  ]) {
    assert.match(
      readFileSync(routePath, 'utf8'),
      /session\.analysisKind !== 'infringement'/,
      `${routePath} must reject invalidity sessions instead of interpreting them as infringement results`,
    );
  }
  assert.match(uploadRouteSource, /const environment = CANONICAL_INVALIDITY_ENVIRONMENT/);
  assert.match(invalidityServiceSource, /const tokenVariable = environmentVariable\(environment, 'API_TOKEN'\)/);
  assert.match(invalidityServiceSource, /if \(!token\) \{/);
  assert.match(invalidityServiceSource, /process\.env\.INVALIDITY_API_TOKEN \|\| process\.env\[tokenVariable\]/);
  assert.match(formalPageSource, /redirect\('\/'\)/);
  assert.doesNotMatch(formalPageSource, /UploadForm|\/api\/invalidity\/investigations|\/api\/invalidity\/uploads/);
  assert.match(formalInvestigationSource, /resolveInvalidityUploadPath\(CANONICAL_INVALIDITY_ENVIRONMENT, fileKey\)/);
  assert.match(formalInvestigationSource, /requestInvalidityService<Record<string, unknown>>\(\s*CANONICAL_INVALIDITY_ENVIRONMENT/);
  assert.match(
    formalInvestigationSource,
    /try \{\s*await prepareInvalidityUploadRoot\(CANONICAL_INVALIDITY_ENVIRONMENT, \{ create: true \}\);\s*const body =/,
    'every investigation type must fail closed before input dispatch when the canonical upload root is unavailable',
  );
  assert.match(testPageSource, /uploadEndpoint="\/api\/invalidity\/uploads\?environment=test"/);
  assert.match(testPageSource, /fetch\('\/api\/invalidity\/uploads\?environment=test'/);
  assert.doesNotMatch(testPageSource, /environment=prod/);
  assert.match(testPageSource, /fetch\(`\/api\/test\/invalidity\//);
  assert.match(portalHomeSource, /href="\/test\/module-lab"/);
  assert.match(portalHomeSource, />无效检测测试版</);
  assert.match(portalHomeSource, /href="\/test\/product-pipeline"/);
  assert.match(portalHomeSource, />专利分析实验室</);
  assert.equal(
    (portalHomeSource.match(/href="\/test\//g) || []).length,
    2,
    'Agent sidebar must expose exactly the two consolidated test labs',
  );
  assert.doesNotMatch(portalHomeSource, /href="\/test\/invalidity"/);
  assert.match(formalResultsSource, /<Link href="\/">返回 Agent<\/Link>/);
  assert.doesNotMatch(formalResultsSource, /href="\/invalidity"/);
  assert.match(p002TestPageSource, /\/api\/invalidity\/uploads\?environment=test/);
  assert.match(p002TestPageSource, /fetch\(`\/api\/test\/invalidity\//);
  assert.match(p002TestPageSource, /'I1_TARGET_SNAPSHOT'/);
  assert.match(p002TestPageSource, /'I2_QUERY_PLAN'/);
  assert.match(p002TestPageSource, /'I3_PATENT_SEARCH'/);
  assert.match(p002TestPageSource, /actualProvider !== 'patsnap'/);
  assert.match(p002TestPageSource, /max_queries: 5/);
  assert.match(p002TestPageSource, /plannedQueries\.length > 5/);
  assert.match(p002TestPageSource, /固定首轮检索线均为零命中/);
  assert.doesNotMatch(p002TestPageSource, /p002Fallback|fallbackOfRunId/);
  assert.match(p002TestPageSource, /lead 候选线索/);
  assert.doesNotMatch(p002TestPageSource, /\/start(?:'|"|`)/);
  assert.doesNotMatch(p002TestPageSource, /127\.0\.0\.1:5109|INVALIDITY_PROD_/);
  assert.match(p002TestFlowSource, /search_provider: 'patsnap'/);
  assert.match(p002TestFlowSource, /search_modality: 'text'/);
  assert.match(p002TestFlowSource, /server_before: date/);
  assert.match(p002TestFlowSource, /anchoredCategoryCount < 2/);
  assert.doesNotMatch(p002TestFlowSource, /minimumFeatureGroups/);
  assert.match(p002TestFlowSource, /inventive_point_precision/);
  assert.match(p002TestFlowSource, /CLMS:/);
  assert.match(p002TestFlowSource, /TTL:/);
  assert.match(p002TestFlowSource, /ABST:/);
  assert.match(p002TestFlowSource, /balancedProviderTerms/);
  assert.match(p002TestFlowSource, /featureQueryGroups/);
  assert.match(p002TestFlowSource, /compactEnglishVariants/);
  assert.match(p002TestFlowSource, /applicant_plus_object/);
  assert.match(p002TestFlowSource, /title_object_plus_desc_inventive/);
  assert.match(p002TestFlowSource, /classification_plus_desc_inventive/);
  assert.match(p002TestFlowSource, /desc_object_plus_desc_inventive_plus_effect/);
  assert.match(p002TestFlowSource, /title_keyword_object_plus_desc_function/);
  assert.doesNotMatch(p002TestFlowSource, /server_after|filing_before|country:/);
  assert.doesNotMatch(testPageSource, /\/api\/invalidity\/session\//);
  // module-lab 面向律师：十一个业务阶段，每个阶段仅保留真实案件/内置案例两个入口。
  assert.match(moduleLabAliasSource, /当前真实案件/);
  assert.match(moduleLabAliasSource, /十一个律师工作阶段/);
  assert.equal(
    moduleLabAliasSource.match(/technicalCodes: \[/g)?.length,
    11,
    'lawyer module lab must expose exactly eleven business stages',
  );
  for (const name of [
    '读取目标专利',
    '确定检索截止日',
    '总结核心发明点',
    '生成检索关键词',
    '首轮检索与证据核验',
    '单篇新颖性比对',
    '选择 D1 与冻结区别特征',
    '显而易见性预分析',
    '进一步检索与补证循环',
    '创造性组合分析',
    '三步法文字分析、Top 10 总表与全部比对',
  ]) {
    assert.ok(moduleLabAliasSource.includes(name), `module-lab must expose ${name}`);
  }
  assert.match(
    moduleLabAliasSource,
    /BUSINESS_MODULES\.map\(\(module\) =>[\s\S]*?loadBenchmark\(module\.id\)/,
    'the five real benchmark examples must expose the same eleven-stage navigation as the module lab',
  );
  assert.match(
    moduleLabAliasSource,
    /<BenchmarkStageDetails[\s\S]*?moduleId=\{benchmarkView\}/,
    'the selected benchmark case must render the selected one of all eleven stages',
  );
  assert.doesNotMatch(
    moduleLabAliasSource,
    /看模块3发明点总结|看模块4检索关键词|看模块6逐篇比对/,
    'the benchmark examples must not retain the former three-button architecture',
  );
  assert.match(
    moduleLabAliasSource,
    /technicalCodes: \['I2_INVENTIVE_PROFILE'\]/,
    'business module 3 must call the inventive profile module code',
  );
  assert.match(
    moduleLabAliasSource,
    /technicalCodes: \['I2_QUERY_PLAN'\]/,
    'business module 4 must call the query plan module code',
  );
  for (const [stage, code] of [
    ['business stage 6', 'I4_S_SINGLE_REFERENCE'],
    ['business stage 7', 'I4_C_CLOSEST_PRIOR_ART'],
    ['business stage 8', 'I4_O_OBVIOUSNESS_PRECHECK'],
    ['business stage 9', 'I2_GAP_QUERY_PLAN'],
    ['business stage 10', 'I4_I_INVENTIVE_STEP'],
    ['business stage 11', 'I5_REPORT'],
  ]) {
    assert.match(
      moduleLabAliasSource,
      new RegExp(`technicalCodes: \\['${code}'\\]`),
      `${stage} must expose ${code} as its independently testable backend module`,
    );
  }
  assert.match(
    moduleLabAliasSource,
    /attachedRunBelongsToBusinessModule[^]*iteration > 1[^]*businessModule\.id === 'gapSearch'[^]*businessModule\.id === 'evidence' \|\| businessModule\.id === 'singleReference'/,
    'same-session diagnostics must assign reused I3/I4-S runs by iteration so gap partials do not pollute module 5/6',
  );
  for (const module10Output of [
    '第一步：确定最接近的现有技术 D1',
    '第二步：确定区别特征及其实际技术问题',
    '第三步：逐区别特征核验公开、技术启示与组合路径',
    '现有证据已形成缺乏创造性的完整证据链（供律师复核）',
    '现有证据尚不足以证明不具备创造性',
    '证据不足不等于目标专利已经被证明具备创造性或当然有效',
  ]) {
    assert.ok(
      moduleLabAliasSource.includes(module10Output),
      `business module 10 must render ${module10Output}`,
    );
  }
  assert.match(
    moduleLabAliasSource,
    /distinguishing_feature_analysis/,
    'business module 10 must render one three-step evidence row per D1 distinction',
  );
  assert.match(
    moduleLabAliasSource,
    /considered_document_ids/,
    'business module 10 must distinguish all reviewed module-9 material from the selected combination',
  );
  for (const module11Section of [
    '第一部分：模块10结论的律师可读文字版',
    '第二部分：相似度最高的前10篇对比文件横向表',
    '第三部分：全部对比文件与独立权利要求逐篇比对',
  ]) {
    assert.ok(
      moduleLabAliasSource.includes(module11Section),
      `business module 11 must render ${module11Section}`,
    );
  }
  assert.match(
    moduleLabAliasSource,
    /data-module11-three-sections/,
    'module 11 must keep its three report sections inside a default-collapsed accordion',
  );
  assert.match(
    moduleLabAliasSource,
    /data-module11-top10-matrix/,
    'module 11 must expose the horizontal top-10 feature matrix',
  );
  assert.match(
    moduleLabAliasSource,
    /data-module11-all-document-comparisons/,
    'module 11 must retain every per-document comparison in its third section',
  );
  assert.match(
    moduleLabAliasSource,
    /human_review_feature_ids/,
    'business module 9 must keep lawyer-review features visible as unresolved facts',
  );
  assert.match(
    moduleLabAliasSource,
    /不视为已有证据覆盖/,
    'business module 9 must not label human-review routing as full evidence coverage',
  );
  assert.match(
    moduleLabAliasSource,
    /precheck\.closest_document_id[\s\S]*?firstDifference\.d1_document_id[\s\S]*?closestRun\?\.output\.document_id/,
    'business module 9 must recover the persisted D1 from module 8, differences, or module 7',
  );
  assert.match(
    moduleLabAliasSource,
    /固定五类，实际生成.*\/5 条/,
    'business module 4 must show lawyers the fixed-five count explicitly',
  );
  assert.match(
    moduleLabAliasSource,
    /已拒绝回退旧方案/,
    'business module 5 must fail closed instead of searching an old fallback query',
  );
  assert.doesNotMatch(
    moduleLabAliasSource,
    /const executableQueries = plannedQueries\.length/,
    'module-lab must not restore the historical fallback query when I2 returns no fixed lanes',
  );
  assert.match(
    moduleLabAliasSource,
    /input: '输入：模块3总结的核心发明点与技术特征'/,
    'business module 4 input must be the module 3 inventive profile output',
  );
  assert.match(
    moduleLabAliasSource,
    /source_plan_run_id/,
    'module 4 input view must resolve the persisted source inventive profile run',
  );
  assert.match(
    moduleLabAliasSource,
    /allBusinessStates\['profile'\]/,
    'module 4 input view must read module 3 runs from the shared business states',
  );
  assert.match(
    moduleLabAliasSource,
    /模块4只读取模块3总结的核心发明点与技术特征/,
    'module 4 must tell lawyers it no longer re-reads the full patent',
  );
  assert.match(
    moduleLabAliasSource,
    /未找到模块3的输出，请先运行模块3/,
    'module 4 input view must explain when no module 3 output exists',
  );
  assert.match(
    moduleLabAliasSource,
    /请先运行模块3总结核心发明点，模块4才能生成检索关键词/,
    'module 4 must be gated on a successful module 3 run per claim',
  );
  assert.match(
    moduleLabAliasSource,
    /LAB_I2_PROFILE_REQUIRED/,
    'the backend profile-required error must map to a lawyer-readable message',
  );
  assert.match(
    moduleLabAliasSource,
    /本次由大模型基于模块3总结的核心发明点与技术特征分析生成检索关键词/,
    'module 4 must describe the live-model keyword generation from the module 3 profile as the normal path',
  );
  assert.match(
    moduleLabAliasSource,
    /module3_profile_live_model/,
    'module 4 must handle the module3_profile_live_model generation source',
  );
  assert.match(moduleLabAliasSource, /用当前案件测试/);
  assert.match(moduleLabAliasSource, /用内置示例测试/);
  assert.doesNotMatch(moduleLabAliasSource, /Textarea|用我填的输入|input_mode: 'manual'/);
  assert.match(moduleLabAliasSource, /仅检索和分析独立权利要求/);
  assert.match(moduleLabAliasSource, /旧版“需要人工确认”已找到/);
  assert.match(moduleLabAliasSource, /本次实际输入/);
  assert.match(moduleLabAliasSource, /本次输出结果/);
  assert.match(
    moduleLabAliasSource,
    /input_snapshot', 'request', 'input'/,
    'lawyer module inputs must come from each persisted run input snapshot',
  );
  assert.match(
    moduleLabAliasSource,
    /没有实验室 run 的 request\.input 包装层/,
    'read-only view must fall back to the raw I0 input_snapshot when the lab request.input wrapper is absent',
  );
  assert.match(
    moduleLabAliasSource,
    /function documentJoinKeys/,
    'read-only evidence view must join I0 search hits with fetch/qualify records via tolerant identity keys',
  );
  assert.match(
    moduleLabAliasSource,
    /I0 自动流程的检索 run 输出命中列表字段是 records/,
    'read-only evidence view must read I0 search hits from output.records when output.documents is absent',
  );
  for (const lawyerVisibleInput of [
    '送入的专利',
    '申请日',
    '最早优先权日',
    '独立权利要求的技术特征',
    '实际检索输入',
    '逐篇取文输入',
    '逐篇送入的对比文件',
    '候选单篇比对',
    '冻结区别特征',
    '待预分析区别特征',
    '当前 D1',
    '可用单篇矩阵',
    '前十个阶段已经持久化的结果',
  ]) {
    assert.ok(
      moduleLabAliasSource.includes(lawyerVisibleInput),
      `module-lab must render the business input field ${lawyerVisibleInput}`,
    );
  }
  assert.match(moduleLabAliasSource, /技术运行记录（供 Codex 排错）/);
  assert.match(moduleLabAliasSource, /searchParams\.set\('investigation', id\)/);
  assert.match(moduleLabAliasSource, /report-data\?preview=true/);
  assert.match(moduleLabAliasSource, /buildPatsnapSearchPlan/);
  assert.match(moduleLabAliasSource, /query_plan_run_id: queryPlan\.moduleRunId/);
  assert.match(moduleLabAliasSource, /'compact-fallback'/);
  assert.doesNotMatch(moduleLabAliasSource, /findStoredQuery/);
  assert.match(
    moduleLabAliasSource,
    /code: 'I3_CANDIDATE_FILTER'/,
    'lawyer evidence module must run the persisted pre-fetch candidate filter',
  );
  assert.match(
    moduleLabAliasSource,
    /search_run_ids: completedSearches\.map/,
    'candidate filter input must be persisted search run ids, not client-forged documents',
  );
  assert.match(
    moduleLabAliasSource,
    /rows\(candidateFilter\.output\.fetch_candidates\)/,
    'only retained unique representatives may enter the fetch queue',
  );
  assert.match(
    moduleLabAliasSource,
    /candidate_filter_run_id: candidateFilter\.moduleRunId/,
    'live fetch must reference the persisted candidate-filter run',
  );
  assert.match(
    moduleLabAliasSource,
    /candidate_id: lead\.candidateId/,
    'live fetch must reference a retained server-side candidate id',
  );
  assert.match(
    moduleLabAliasSource,
    /runModule9Batch\(/,
    'lawyer module 9 must use the durable multi-round controller instead of stopping at I2-G',
  );
  assert.match(
    moduleLabAliasSource,
    /module9_batch/,
    'lawyer module 9 must persist its batch id for refresh recovery',
  );
  assert.match(
    module9BatchRouteSource,
    /primary\.length !== unresolved\.length[\s\S]*每个主检索组必须且只能绑定一个本轮未覆盖区别特征/,
    'module 9 controller must require exactly one primary query per unresolved feature',
  );
  assert.match(
    module9BatchRouteSource,
    /groups\.length !== 2[\s\S]*groups\.every\(bilingual\)[\s\S]*目标专利完全相同的产品类别/,
    'module 9 controller must enforce two bilingual OR groups and exclude the exact target category',
  );
  assert.match(
    module9BatchRouteSource,
    /advancePlanMatrix[\s\S]*gap_query_matrix_rounds = Array\.from[\s\S]*matrixPreviousExpressions[\s\S]*assertMatrixRoundChanged/,
    'module 9 must generate, persist, and cross-round validate the five-round matrix before execution',
  );
  assert.match(
    module9BatchRouteSource,
    /saveBatch\(row, state, 'awaiting_next_round'[^]*async function queueNextRound/,
    'module 9 must persist each closed round before automatically queueing the next frozen round',
  );
  assert.match(
    module9BatchRouteSource,
    /failedComparisons[^]*finishRound\([^]*failureReason[^]*failure_ledger/,
    'module 9 must preserve failed I4-S work in the error ledger and continue later frozen rounds',
  );
  assert.match(
    module9BatchRouteSource,
    /can_continue:[^]*can_continue_with_failures:[^]*can_retry_failed:/,
    'module 9 batch response must expose explicit one-round continuation controls',
  );
  assert.match(
    module9BatchRouteSource,
    /rounds: roundSummaries[^]*current_round: roundSummaries\.at\(-1\)/,
    'module 9 must return per-round statistics separately from cumulative totals',
  );
  assert.match(
    module9BatchRouteSource,
    /requestedStartMode[^]*startMode === 'fresh'[^]*latest_iteration: 0[^]*next_iteration: 1/,
    'an explicit fresh module 9 batch must start at gap round 1 without inheriting a legacy cursor',
  );
  assert.match(
    moduleLabAliasSource,
    /start_mode: 'fresh'/,
    'the lawyer module 9 first-round action must explicitly request a fresh batch',
  );
  assert.match(
    moduleLabAliasSource,
    /module6_batch_id: module6BatchId,[^]*closest_prior_art_run_id: closestPriorArtRunId,[^]*obviousness_precheck_run_id: obviousnessPrecheckRunId/,
    'a fresh module 9 batch must bind this Module-6 batch and the exact Module-7/8 runs',
  );
  assert.match(
    moduleLabAliasSource,
    /模块8不会读取历史D1[^]*source_closest_prior_art_run_id[^]*模块9不会读取历史运行/,
    'Modules 8 and 9 must reject stale upstream business runs',
  );
  assert.match(
    moduleLabAliasSource,
    /模块10不会混入其他批次文献[^]*module9_batch_id: module9BatchId[^]*obviousness_precheck_run_id: obviousnessPrecheckRunId/,
    'Module 10 must consume only the exact completed Module-9 batch and its upstream lineage',
  );
  assert.match(
    module9BatchRouteSource,
    /validateModule9SourceLineage[^]*invalidity_test_module5_batches[^]*I4_C_CLOSEST_PRIOR_ART[^]*I4_O_OBVIOUSNESS_PRECHECK/,
    'the durable controller must independently verify the exact Module-6/7/8 lineage',
  );
  assert.match(
    module9BatchRouteSource,
    /validateZeroQueryCoverage[^]*source_module6_batch_id[^]*evidence_quote[^]*date_qualification_revision[^]*禁止零检索收口/,
    'zero-query completion must require citable evidence from the exact current Module-6 batch',
  );
  assert.match(
    moduleLabAliasSource,
    /url\.searchParams\.delete\('module6_batch'\)[^]*url\.searchParams\.delete\('module9_batch'\)/,
    'switching target patents must clear stale module 6 and module 9 batch URLs',
  );
  assert.match(
    moduleLabAliasSource,
    /currentBatch\.investigation_id[^]*currentBatch\.claim_investigation_id[^]*已拒绝复用旧案件进度/,
    'module 9 recovery must reject a batch from another investigation or claim',
  );
  assert.match(
    module9BatchRouteSource,
    /claim_investigation_id = \$2 and user_id = \$3[^]*模块九批次不属于当前案件或独立权利要求/,
    'latest module 9 recovery must be scoped by both investigation and claim',
  );
  assert.match(
    moduleLabAliasSource,
    /hasModule9Batch[^]*重新生成五轮并执行第 1 轮[^]*生成五轮并执行第 1 轮[^]*执行第 \{Math\.min\(5, module9Iteration \+ 1\)\} 轮/,
    'module 9 must offer a fresh first round alongside the explicit next-round action',
  );
  assert.match(
    module9BatchRouteSource,
    /inherited_latest_iteration:[^]*started_from_history:[^]*continuation_reason:/,
    'module 9 response must explain inherited round history and why another round is unavailable',
  );
  assert.match(
    module9BatchRouteSource,
    /previous_query_expressions:[\s\S]*previous_iteration_failure_reason:/,
    'later gap rounds must receive the previous expressions and failure reason',
  );
  assert.match(
    module9BatchRouteSource,
    /'I4_S_SINGLE_REFERENCE'/,
    'module 9 durable controller must compare every analysis-ready new document',
  );
  assert.match(
    module9BatchRouteSource,
    /MAX_PLAN_ATTEMPTS[\s\S]*plan_attempt_run_ids[\s\S]*矩阵第 \$\{matrixRound\.iteration\} 轮计划未通过守门/,
    'module 9 must persist and retry a guarded gap-plan failure instead of stopping the batch immediately',
  );
  assert.match(
    module9BatchRouteSource,
    /FIRST_DETERMINISTIC_PLAN_ATTEMPT = 4[\s\S]*MAX_PLAN_ATTEMPTS = 5[\s\S]*force_deterministic_gap_plan:[\s\S]*attemptNumber >= FIRST_DETERMINISTIC_PLAN_ATTEMPT/,
    'module 9 must switch to frozen-fact deterministic plans after three failed model plans',
  );
  assert.match(
    module9BatchRouteSource,
    /MAX_CANDIDATE_FILTER_ATTEMPTS[\s\S]*candidate_filter_attempt_run_ids[\s\S]*candidate-filter:attempt:/,
    'module 9 must recover a candidate-filter run that failed under an older plan contract',
  );
  assert.match(
    module9BatchRouteSource,
    /resolved_gap_feature_ids[\s\S]*executablePlanQueries[\s\S]*skipped_feature_resolved/,
    'module 9 must carry a monotonic remaining-feature cursor and reject reopened features',
  );
  assert.match(
    module9BatchRouteSource,
    /reusableBindingForDocument[\s\S]*document_version_id[\s\S]*content_sha256[\s\S]*date_qualification_revision[\s\S]*i4s_prompt_version[\s\S]*i4s_rule_version/,
    'module 9 may reuse an earlier I4-S result only under the complete frozen binding',
  );
  assert.match(
    moduleLabAliasSource,
    /历史比对版本不完整，暂不计入已覆盖证据；相关特征已保留为未覆盖并继续补检。/,
    'reuse-key audit failures must be summarized without exposing raw version-key diagnostics',
  );
  assert.match(
    moduleLabAliasSource,
    /已复用覆盖文献与引文[^]*对比文件原文[^]*本次模块6批次[^]*I4-S run/,
    'module 9 must show the actual reused document, quote, batch, and I4-S run',
  );
  assert.match(
    moduleLabAliasSource,
    /本轮区别特征检索组（\{primaryQueries\.length\} 组）[\s\S]*每项未解决区别特征对应一组/,
    'module 9 lawyer UI must lead with the per-feature query groups',
  );
  assert.match(
    moduleLabAliasSource,
    /仅重试本轮失败文献[\s\S]*执行第 \{Math\.min\(5, module9Iteration \+ 1\)\} 轮[\s\S]*保留失败并执行第 \{Math\.min\(5, module9Iteration \+ 1\)\} 轮/,
    'module 9 lawyer UI must expose retry and explicit next-round actions',
  );
  assert.match(
    moduleLabAliasSource,
    /roundIteration[\s\S]*GapRoundResultDetails/,
    'module 9 lawyer UI must group output by persisted gap round',
  );
  assert.match(
    moduleLabAliasSource,
    /查看本轮逐篇比对及逐特征判断[\s\S]*执行第 \{Math\.min\(5, module9Iteration \+ 1\)\} 轮/,
    'module 9 lawyer UI must make the current-round comparison and next-round action visible',
  );
  assert.match(
    moduleLabAliasSource,
    /五轮检索词矩阵[\s\S]*五轮检索词在开始检索前一次生成并冻结[\s\S]*skipped_feature_resolved/,
    'module 9 lawyer UI must expose the frozen five-round matrix and resolved-feature skips',
  );
  for (const strategy of [
    '相邻产品类别 + 直接结构特征',
    '优先上位、必要时选择合适下位产品类别 + 结构族同义词',
    '同功能产品类别 + 动作/功能/技术角色',
    '子系统/部件类别 + 构件关系/介质路径',
    '类比领域客体 + 工作原理/技术效果',
  ]) {
    assert.ok(
      moduleLabAliasSource.includes(strategy),
      `module 9 must explain its fixed five-round strategy: ${strategy}`,
    );
  }
  assert.match(
    moduleLabAliasSource,
    /gap_search_strategy_label[\s\S]*gap_search_strategy_description/,
    'module 9 must render the persisted strategy selected by the backend',
  );
  assert.match(
    moduleLabAliasSource,
    /本轮新文献逐篇比对（\{comparisons\.length\} 份）[\s\S]*对比文件原文[\s\S]*分析理由/,
    'module 9 must show readable per-document feature comparisons outside technical logs',
  );
  assert.match(
    moduleLabAliasSource,
    /已到第 5 轮，无下一轮[\s\S]*下一轮状态：/,
    'module 9 must explain a disabled next-round action instead of hiding it',
  );
  assert.doesNotMatch(
    moduleLabAliasSource,
    /document: fetchableLead/,
    'the browser must not resubmit candidate metadata to live fetch',
  );
  assert.match(
    moduleLabAliasSource,
    /errors\.every\(\(item\) => item\.includes\('QueryValidationError'\)\)/,
    'historical shared NPL validation failures must be rendered once, not as four provider outages',
  );
  assert.match(
    moduleLabAliasSource,
    /并非四个论文来源同时故障/,
    'historical NPL validation failures must explain the actual shared-input cause to lawyers',
  );
  assert.doesNotMatch(
    moduleLabAliasSource,
    /fetch_candidates\)\.slice\(/,
    'the retained fetch queue must not have a second hidden truncation',
  );
  assert.doesNotMatch(moduleLabAliasSource, /kind === 'patent' \? 2 : 3/);
  assert.doesNotMatch(moduleLabAliasSource, /documentIds\.slice\(0,\s*5\)/);
  assert.match(
    module5BatchRouteSource,
    /comparisonSuccesses === comparisonDetails\.length/,
    'module 5 follow-up analysis must wait until every persisted single-reference comparison succeeds',
  );
  assert.match(
    module5BatchRouteSource,
    /structural_review_status\) === 'model_error'/,
    'an incomplete whole-structure review must not be presented as a successful comparison',
  );
  assert.match(
    module5BatchRouteSource,
    /structuralReviewIncomplete\(detail\)/,
    'an incomplete whole-structure review must enter the bounded module 5 retry path',
  );
  assert.match(
    module5BatchRouteSource,
    /comparisonHasUsableRows/,
    'partial per-feature output must remain visible without becoming aggregation-ready',
  );
  assert.match(
    module5BatchRouteSource,
    /incompleteFeatureCount/,
    'analysis_failed features must be counted explicitly instead of hiding the whole document',
  );
  assert.match(
    module5BatchRouteSource,
    /整体结构复核未完成/,
    'lawyers must see a concise structural-review failure instead of a false completed result',
  );
  assert.match(
    module5BatchRouteSource,
    /structural_review_error_code/,
    'a provider 1210 captured by the bounded structural review must remain eligible for the existing 1210 retry policy',
  );
  assert.match(moduleLabAliasSource, /text\(item\.feature_text\)/);
  for (const structuralDisclosureField of [
    'mapping_basis',
    'structural_evidence',
    'integrated_structure_mapping',
    'structural_search_summary',
    'necessity_chain',
    'reasonable_alternatives_excluded',
    'alternative_path_analysis',
  ]) {
    assert.ok(
      moduleLabAliasSource.includes(structuralDisclosureField),
      `module-lab must retain and render ${structuralDisclosureField}`,
    );
  }
  for (const lawyerVisibleStructuralOutput of [
    '结构等同',
    '结构角色及关系对应',
    '由运行机理必然隐含',
    '目标结构角色',
    '对比文件对应结构',
    '集成结构对应',
    '同一集成结构承接多个目标特征',
    '全文结构核查概况',
    '结构证据',
    '必然性推导链',
    '已排除合理替代路径',
    '替代路径分析',
  ]) {
    assert.ok(
      moduleLabAliasSource.includes(lawyerVisibleStructuralOutput),
      `module-lab must explain ${lawyerVisibleStructuralOutput} in lawyer-facing language`,
    );
  }
  assert.match(
    moduleLabAliasSource,
    /integrated_structure_mapping === true/,
    'integrated_structure_mapping is a boolean and must only render when true',
  );
  assert.match(
    moduleLabAliasSource,
    /role_and_relation: '结构角色及关系对应'/,
    'module-lab must translate the role_and_relation mapping basis',
  );
  assert.match(
    moduleLabAliasSource,
    /necessarily_implicit_from_operation: '由运行机理必然隐含'/,
    'module-lab must translate the operation-based necessary implication mapping basis',
  );
  assert.match(
    moduleLabAliasSource,
    /function persistedDisclosureView/,
    'persisted report disclosures must unwrap their nested analysis and locator evidence',
  );
  assert.match(
    moduleLabAliasSource,
    /const implicitMapping =/,
    'alternative-path badges must be limited to necessarily implicit mappings',
  );
  assert.match(
    moduleLabAliasSource,
    /已完成第二遍整体结构复核/,
    'successful comparisons must expose whether a bounded second structural pass ran',
  );
  assert.equal(
    (moduleLabAliasSource.match(/<StructureMappingDetails/g) || []).length,
    2,
    'module 6 and module 11 full-document tables must render the same structure mapping details',
  );
  assert.match(moduleLabAliasSource, /一份成功不会代替或掩盖其他文献/);
  assert.doesNotMatch(moduleLabAliasSource, /日期资格：/);
  assert.match(
    moduleLabAliasSource,
    /function ProfileResultDetails/,
    'business module 3 must render the inventive profile view',
  );
  assert.match(
    moduleLabAliasSource,
    /child\.ok && child\.code === 'I2_INVENTIVE_PROFILE'/,
    'the inventive profile view must only read I2_INVENTIVE_PROFILE runs',
  );
  assert.match(
    moduleLabAliasSource,
    /code: 'I2_INVENTIVE_PROFILE'/,
    'business module 3 must post I2_INVENTIVE_PROFILE runs per claim',
  );
  assert.match(moduleLabAliasSource, /invalidityDateClassification\(eligibility\)/);
  for (const lawyerVisibleOutput of [
    '本轮实际处理的独立权利要求',
    '全部权利要求',
    '读取范围与检索范围不同',
    '著录项目与日期',
    '说明书结构',
    '摘要附图与说明书附图',
    '核心发明点总结',
    '检索级核心发明点（最多 3 项）',
    '原始技术特征仍完整列在对应发明点下',
    '首轮分线检索方案',
    '系统理解的发明机构',
    '最能说明发明机构的锚点',
    '类别共有的技术语境',
    '实际提交的检索式',
    '仅检索线索',
    '未取得可核验原文',
    '权利要求特征',
    '精确原文',
    '原文位置',
    '结构角色与映射',
    '律师工作报告 · 当前自动口径',
  ]) {
    assert.ok(
      moduleLabAliasSource.includes(lawyerVisibleOutput),
      `module-lab must render ${lawyerVisibleOutput}`,
    );
  }
  assert.match(moduleLabAliasSource, /uploadEndpoint="\/api\/invalidity\/uploads\?environment=test"/);
  assert.doesNotMatch(moduleLabAliasSource, /redirect\(/);
  assert.doesNotMatch(moduleLabAliasSource, /127\.0\.0\.1:5109|INVALIDITY_PROD_/);
  assert.doesNotMatch(moduleLabAliasSource, /\/api\/invalidity\/session\//);
  assert.match(moduleLabAliasSource, /\/api\/test\/invalidity\/module5-batches/);
  assert.doesNotMatch(moduleLabAliasSource, /ANALYSIS_PIPELINE_PASSES/);
  assert.match(databaseInitSource, /create table if not exists invalidity_test_module5_batches/);
  assert.match(databaseInitSource, /create table if not exists invalidity_test_module9_batches/);
  assert.match(databaseInitSource, /unique \(user_id, idempotency_key_sha256\)/);
  assert.match(databaseInitSource, /inventive_retry_count integer not null default 0/);
  assert.match(module5BatchRouteSource, /MAX_BATCH_DOCUMENTS = 50/);
  assert.match(module5BatchRouteSource, /invalidityTestResourceOwnership\(\)\.require\(/);
  assert.match(module5BatchRouteSource, /invalidityTestResourceOwnership\(\)\.bind\(/);
  assert.match(module5BatchRouteSource, /'I4_S_SINGLE_REFERENCE'/);
  assert.doesNotMatch(module5BatchRouteSource, /'I4_C_CLOSEST_PRIOR_ART'/);
  assert.doesNotMatch(module5BatchRouteSource, /'I4_I_INVENTIVE_STEP'/);
  assert.match(module5BatchRouteSource, /queued_document_count/);
  assert.match(module5BatchRouteSource, /running_document_count/);
  assert.match(module5BatchRouteSource, /entry\.retry_count = retryNumber/);
  assert.match(module5BatchRouteSource, /entry\.retry_count >= 2/);
  assert.match(module5BatchRouteSource, /message\.includes\('error_code=1210'\)/);
  assert.match(moduleLabAliasSource, /module6_batch/);
  assert.match(moduleLabAliasSource, /排队.*执行.*已收口/);
  assert.match(
    moduleLabAliasSource,
    /data-module6-document-disclosure/,
    'module 6 must let lawyers expand or collapse every comparison document',
  );
  assert.match(
    moduleLabAliasSource,
    /data-module6-feature-disclosure/,
    'module 6 must let lawyers expand or collapse every feature assessment inside a document',
  );
  assert.match(
    moduleLabAliasSource,
    /披露 \{counts\.disclosed\}[\s\S]*未披露 \{counts\.notDisclosed\}[\s\S]*待确认 \{counts\.uncertain\}/,
    'collapsed module 6 document rows must retain a useful disclosure summary',
  );
  assert.match(
    moduleLabAliasSource,
    /每项默认收起；点击技术特征可查看原文、位置、结构映射和分析理由/,
    'module 6 must explain its nested disclosure interaction',
  );
  assert.match(
    moduleLabAliasSource,
    /data-module-lab-module-disclosure=\{module\.id\}/,
    'all eleven lawyer modules must use the shared default-collapsed module shell',
  );
  assert.match(
    moduleLabAliasSource,
    /data-module-lab-result-disclosures=\{module\.id\}/,
    'every completed module must keep its actual input and full output collapsed by default',
  );
  assert.match(
    moduleLabAliasSource,
    /data-module-lab-benchmark-disclosures="true"/,
    'benchmark inputs and outputs must also be collapsed by default',
  );
  assert.match(
    moduleLabAliasSource,
    /data-module-lab-report-summary-disclosure="true"/,
    'the standalone report claim list must be collapsed by default',
  );
  assert.match(
    moduleLabAliasSource,
    /输入和输出默认收起，按需展开查看/,
    'the benchmark view must explain its compact disclosure behavior',
  );
  assert.match(moduleLabAliasSource, /function inventiveGapLabel/);
  assert.match(moduleLabAliasSource, /text\(gap\.rationale\)/);
  assert.match(module5BatchRouteSource, /recover_persisted_documents/);
  assert.match(module5BatchRouteSource, /\/module5-inputs/);
  assert.match(moduleLabAliasSource, /recover_persisted_documents:/);
  assert.doesNotMatch(moduleLabAliasSource, /recover_persisted_comparisons/);
  assert.match(module5BatchRouteSource, /body\.recover_persisted_comparisons === true/);
  assert.match(module5BatchRouteSource, /ids = documentIds\(recovered\.document_ids\)/);
  assert.match(moduleLabAliasSource, /input: \{ module6_batch_id: module6BatchId \}/);
  assert.match(
    moduleLabAliasSource,
    /模块7不会读取历史工作流或其他模块6批次/,
  );
  assert.match(module5BatchRouteSource, /claim_investigation_id = \$2/);
  assert.doesNotMatch(module5BatchRouteSource, /127\.0\.0\.1:5109|INVALIDITY_PROD_/);
  assert.match(moduleLabAliasSource, /module-figure/);
  assert.match(moduleFigureProxySource, /requireModuleRunWithInvestigation/);
  assert.match(moduleFigureProxySource, /requestInvalidityServiceBinary/);
  assert.doesNotMatch(moduleFigureProxySource, /127\.0\.0\.1:5109|INVALIDITY_PROD_/);
  assert.doesNotMatch(formalPageSource, /\/api\/test\/invalidity/);
  assert.doesNotMatch(formalResultsSource, /\/api\/test\/invalidity/);
  for (const field of ['server_before', 'server_after', 'filing_before', "country: 'CN'"]) {
    assert.ok(
      testPageSource.includes(field),
      `I3_PATENT_SEARCH live example must expose ${field}`,
    );
  }
  for (const field of [
    "search_provider: 'patsnap'",
    "search_modality: 'text'",
    "search_modality: 'image_single'",
    "search_modality: 'image_multiple'",
    'image_url:',
    'image_urls:',
    "patent_type: 'D'",
    'model: 1',
    'max_results: 10',
  ]) {
    assert.ok(
      testPageSource.includes(field),
      `Patsnap I3 live templates must expose ${field}`,
    );
  }
  for (const template of ['p002-text', 'p060-beta-single', 'p061-multiple']) {
    assert.ok(
      testPageSource.includes(`data-patsnap-template="${template}"`),
      `Patsnap I3 live page must expose the ${template} shortcut`,
    );
  }
  assert.match(
    testPageSource,
    /https:\/\/static-open\.zhihuiya\.com\/sample\/common_demo(?:_2)?\.png/,
  );
  assert.match(testPageSource, /input:\s*parsed/);
  assert.match(
    testProxySource,
    /body\s*=\s*\{\s*\.\.\.body,\s*idempotency_key:/,
    'test proxy must preserve the complete module payload while only scoping its idempotency key',
  );
  assert.doesNotMatch(
    testProxySource,
    /delete\s+(?:body|safeBody)\.(?:search_provider|search_modality|image_url|image_urls|patent_type|model)/,
  );
  assert.doesNotMatch(testPageSource, /127\.0\.0\.1:5109|INVALIDITY_PROD_/);
  assert.match(
    benchmarkCasesRouteSource,
    /wireless-earphone-decision-2022215166391'[\s\S]*?planVersion: '20260808-five-case-i2v3-r5'[\s\S]*?comparisonVersion: '20260806-five-case-v50'/,
    'wireless-earphone lawyer example must use the current I2 v3 and I4-S v50 source-only freezes',
  );
  assert.match(
    benchmarkCasesRouteSource,
    /battery-slurry-decision-2014106703299'[\s\S]*?planVersion: '20260808-five-case-i2v3-r5'[\s\S]*?comparisonVersion: '20260806-five-case-v50'/,
    'battery lawyer example must expose both independent claims from the current freezes',
  );
  assert.match(
    benchmarkCasesRouteSource,
    /gas-stove-decision-2016110150593'[\s\S]*?planVersion: '20260808-five-case-i2v3-r5'[\s\S]*?comparisonVersion: '20260806-five-case-v50'/,
    'gas-stove lawyer example must expose independent claims 1 and 10 from the current freezes',
  );
  assert.doesNotMatch(
    benchmarkCasesRouteSource,
    /evaluation_oracles|evaluate_decision_i4s|decision_source|legal_reasoning/,
    'lawyer built-in runtime must never load post-freeze decision annotations',
  );
  assert.match(earphoneObviousnessFixture, /CN216649931U/);
  assert.match(earphoneObviousnessFixture, /\[0021\]/);
  assert.match(earphoneObviousnessFixture, /\[0031\]/);
  assert.match(earphoneObviousnessFixture, /多个卡槽71和用于卡入卡槽71中的弹性卡接部72/);
  assert.equal(
    (earphoneObviousnessFixture.match(/"search_route": "search_common_knowledge_evidence"/g) || []).length,
    2,
    'earphone output-hole and locating-slot/protrusion groups must only request common-knowledge evidence',
  );
  assert.doesNotMatch(
    earphoneObviousnessFixture,
    /"ordinary_structural_search_required": true/,
    'earphone source-only precheck must not reopen ordinary structural search',
  );
  for (const benchmarkStage of [
    'target',
    'date',
    'profile',
    'query',
    'evidence',
    'singleReference',
    'closestPriorArt',
    'obviousnessPrecheck',
    'gapSearch',
    'inventiveStep',
    'report',
  ]) {
    assert.match(
      benchmarkCasesRouteSource,
      new RegExp(`${benchmarkStage}: \\{[\\s\\S]*?mode:`),
      `the five-case API must describe the evidence mode for benchmark stage ${benchmarkStage}`,
    );
  }
  assert.match(
    benchmarkCasesRouteSource,
    /sourceDocuments:\s*rows\(manifest\.documents\)/,
    'the five-case API must expose frozen source-document facts for stage 5',
  );
  assert.doesNotMatch(
    testPageSource,
    /INVALIDITY_(?:TEST|PROD)_PATSNAP_API_KEY|Authorization\s*:|Bearer\s+|sk-[A-Za-z0-9]/,
  );
  assert.match(testProxySource, /resolveInvalidityUploadPath\('test', sourceFileKey\)/);
  assert.match(
    testProxySource,
    /requestInvalidityService<Record<string, unknown>>\(\s*'test'/,
  );
  assert.match(
    testProxySource,
    /operation\.kind === 'create_investigation'[\s\S]*?await prepareInvalidityUploadRoot\('test', \{ create: true \}\)/,
    'every test investigation type must fail closed when the test upload root is unavailable',
  );
  assert.match(testProxySource, /invalidityTestProxyOperation\(method, path\)/);
  assert.match(testProxySource, /await ownership\.require\('investigation'/);
  assert.match(testProxySource, /await ownership\.requireModuleRunWithInvestigation\(/);
  assert.match(testProxySource, /await ownership\.bind\(\{/);
  assert.match(testProxySource, /operation\.kind === 'cancel_module_run'/);
  assert.match(testProxySource, /operation\.kind === 'retry_module_run'/);
  assert.match(testProxySource, /'module-retry-key'/);
  assert.match(testProxySource, /operation\.kind === 'read_review_context'/);
  assert.match(testProxySource, /operation\.kind === 'import_evidence'/);
  assert.match(testProxySource, /operation\.kind === 'confirm_document_date'/);
  assert.match(testProxySource, /'X-Invalidity-Actor': invalidityPortalActor\(user\)/);
  assert.match(testProxySource, /claimInvalidityUploadReceipt\(/);
  assert.doesNotMatch(testProxySource, /INVALIDITY_PROD_API_URL/);
  assert.match(testPageSource, /\/cancel`/);
  assert.match(testPageSource, /\/retries`/);
  assert.match(testPageSource, /新建重试 run/);
  assert.doesNotMatch(testProxySource, /export function PATCH/);
  assert.match(databaseInitSource, /create table if not exists invalidity_test_resource_owners/);
  assert.match(databaseInitSource, /primary key \(resource_kind, resource_id\)/);
  assert.match(databaseInitSource, /create table if not exists invalidity_upload_receipts/);
  assert.match(databaseInitSource, /reserved_idempotency_key_sha256/);
  assert.match(reviewContextRouteSource, /requireInvaliditySession\(request, id\)/);
  assert.match(reviewContextRouteSource, /getInvalidityReviewContext\(CANONICAL_INVALIDITY_ENVIRONMENT/);
  assert.match(evidenceImportRouteSource, /claimInvalidityUploadReceipt\(/);
  assert.match(evidenceImportRouteSource, /environment: CANONICAL_INVALIDITY_ENVIRONMENT/);
  assert.match(evidenceImportRouteSource, /source_path: receipt\.filePath/);
  assert.match(evidenceImportRouteSource, /expected_source_sha256: receipt\.sha256/);
  assert.match(evidenceImportRouteSource, /expected_source_byte_size: receipt\.byteSize/);
  assert.match(evidenceImportRouteSource, /expected_source_mime_type: receipt\.mimeType/);
  assert.match(evidenceImportRouteSource, /access\.actor/);
  assert.match(evidenceImportRouteSource, /input\.source_url !== undefined/);
  assert.doesNotMatch(reviewPanelSource, /source_path\s*:/);
  assert.doesNotMatch(reviewPanelSource, /actor\s*:/);
  assert.doesNotMatch(reviewPanelSource, /idempotency_key[^\n]*Date\.now/);
  assert.doesNotMatch(reviewPanelSource, /\['public_availability_date', 'review'\]/);
  assert.doesNotMatch(reviewPanelSource, /待 P1 哈希\/CAS 动作契约启用/);
  assert.doesNotMatch(reviewPanelSource, /证据解决（待 P1）/);
  assert.match(reviewPanelSource, /accept="\.pdf,application\/pdf"/);
  assert.match(reviewPanelSource, /code \? `\$\{code\}：\$\{message\}` : message/);
  assert.doesNotMatch(reviewPanelSource, /\.docx|\.doc,|\.txt/);
  assert.match(reviewPanelSource, /latestInvalidityGapsByKey/);
  assert.match(reviewPanelSource, /context\.current_gaps/);
  assert.match(reviewPanelSource, /invalidityReviewQualificationsByClaim/);
  assert.match(reviewPanelSource, /invalidityPendingDateReviewDocuments/);
  assert.match(reviewPanelSource, /后台已检测的事实/);
  assert.match(agentReviewSource, /focus="pending_date_review"/);
  assert.doesNotMatch(agentReviewSource, /下方继续显示证据导入/);
  assert.match(documentDateRouteSource, /浏览器不得提交 \$\{forbidden\}/);
  assert.match(documentDateRouteSource, /confirmInvalidityDocumentDate\(\s*CANONICAL_INVALIDITY_ENVIRONMENT/);
  assert.match(documentDateRouteSource, /确认日期事实时至少填写一个真实日期/);
  assert.match(evidenceUploadRouteSource, /requireInvaliditySession\(request, id\)/);
  assert.doesNotMatch(evidenceUploadRouteSource, /invalidityEnvironment\(/);
  assert.match(testEvidenceUploadRouteSource, /invalidityTestResourceOwnership\(\)\.require/);
  assert.match(testEvidenceUploadRouteSource, /environment: 'test'/);
  assert.match(uploadReceiptSource, /mode: 0o600/);
  assert.match(uploadReceiptSource, /Buffer\.from\('%PDF-'\)/);
  assert.match(uploadReceiptSource, /const EVIDENCE_EXTENSIONS = new Set\(\['\.pdf'\]\)/);
  assert.match(uploadReceiptSource, /const stream = createReadStream\(filePath\)/);
  assert.match(uploadReceiptSource, /byteSize > MAX_EVIDENCE_FILE_SIZE/);
  assert.match(uploadReceiptSource, /digest\.digest\('hex'\) !== row\.sha256/);
  assert.match(uploadReceiptSource, /reserved_idempotency_key_sha256 is null/);
  assert.match(uploadReceiptSource, /consumed_at = coalesce\(consumed_at, now\(\)\)/);
  assert.match(
    evidenceImportRouteSource,
    /humanReviewUploadReceiptMatches\(response, receipt\)[\s\S]*?consumeInvalidityUploadReceipt\(/,
  );
  assert.match(
    testProxySource,
    /humanReviewUploadReceiptMatches\(data, claimedEvidenceReceipt\)[\s\S]*?consumeInvalidityUploadReceipt\(/,
  );
  assert.match(testProxySource, /status: queuedReview \? 202 : 200/);
  assert.match(
    invalidityExportRouteSource,
    /code: 'REPORT_PENDING' \}, \{ status: 202 \}/,
  );
  assert.doesNotMatch(invalidityServiceSource, /closest-prior-art-selections|gap-decisions/);

  const buffer = await buildInvalidityWorkbook(report);
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(Uint8Array.from(buffer).buffer);
  assert.equal(workbook.created.toISOString(), report.generated_at);
  for (const name of [
    '排除与限制记录', 'D1版本历史', '轮次与停止原因', '任务失败与重试',
    '人工复核谱系', '文献版本谱系', '日期事实修订', '证据导入谱系',
  ]) {
    assert.ok(workbook.getWorksheet(name), `missing worksheet ${name}`);
  }
  const qualificationSheet = workbook.getWorksheet('文献与日期资格');
  assert.equal(qualificationSheet?.rowCount, 4, 'same document must retain one row per claim/assessment');
  assert.equal(qualificationSheet?.getRow(2).getCell(1).value, '权利要求 1');
  assert.equal(qualificationSheet?.getRow(3).getCell(1).value, '权利要求 2');
  assert.equal(qualificationSheet?.getRow(2).height, undefined, 'data rows must not use a fixed 42-point height');
  assert.equal(
    workbook.getWorksheet('调查概要')?.getRow(10).getCell(2).value,
    'a'.repeat(64),
    'the exported report digest must use the immutable snapshot_sha256 contract field',
  );
  const noveltySheet = workbook.getWorksheet('新颖性CC');
  assert.equal(noveltySheet?.rowCount, 3);
  assert.equal(noveltySheet?.getRow(2).getCell(4).value, '正式最接近现有技术');
  assert.equal(noveltySheet?.getRow(3).getCell(4).value, '正式最接近现有技术');
  assert.ok(
    !noveltySheet?.getRows(2, 2)?.some((row) => row.getCell(4).value === '高相似度候选（非正式 D1）'),
    'CC must never combine partial disclosures from different documents into a novelty result',
  );
  const inventiveSheet = workbook.getWorksheet('创造性组合');
  assert.equal(inventiveSheet?.rowCount, 2);
  assert.equal(inventiveSheet?.getRow(2).getCell(4).value, '["doc-1","doc-2"]');
  assert.equal(workbook.getWorksheet('排除与限制记录')?.rowCount, 2);
  assert.equal(workbook.getWorksheet('D1版本历史')?.rowCount, 2);
  assert.equal(
    workbook.getWorksheet('D1版本历史')?.getRow(2).getCell(4).value,
    '正式最接近现有技术',
    'a higher raw similarity score must not be promoted to formal D1',
  );
  assert.equal(workbook.getWorksheet('任务失败与重试')?.rowCount, 2);
  assert.equal(workbook.getWorksheet('人工复核谱系')?.rowCount, 2);
  assert.equal(workbook.getWorksheet('文献版本谱系')?.rowCount, 2);
  assert.equal(workbook.getWorksheet('日期事实修订')?.rowCount, 2);
  assert.equal(workbook.getWorksheet('证据导入谱系')?.rowCount, 2);
  const perDocumentIndex = workbook.getWorksheet('逐篇CC索引');
  assert.equal(perDocumentIndex?.rowCount, 3, 'every analysed comparison document must appear in the per-document CC index');
  const firstDocumentSheetName = String(perDocumentIndex?.getRow(2).getCell(6).text || '');
  const secondDocumentSheetName = String(perDocumentIndex?.getRow(3).getCell(6).text || '');
  const firstDocumentSheet = workbook.getWorksheet(firstDocumentSheetName);
  const secondDocumentSheet = workbook.getWorksheet(secondDocumentSheetName);
  assert.equal(firstDocumentSheet?.rowCount, 3, 'doc-1 must have one header and two feature rows in its own worksheet');
  assert.equal(secondDocumentSheet?.rowCount, 2, 'doc-2 must have one header and one feature row in its own worksheet');
  assert.equal(firstDocumentSheet?.getRow(1).getCell(3).value, '目标技术特征');
  assert.equal(firstDocumentSheet?.getRow(2).getCell(4).value, '有（明确）');
  assert.equal(secondDocumentSheet?.getRow(2).getCell(4).value, '有（必然隐含）');
  assert.equal(workbook.getWorksheet('调查概要')?.getRow(11).getCell(2).value, 4);
  assert.equal(workbook.getWorksheet('调查概要')?.getRow(12).getCell(2).value, 3);
  const exportedText: string[] = [];
  workbook.eachSheet((sheet) => sheet.eachRow((row) => row.eachCell((cell) => {
    exportedText.push(typeof cell.value === 'string' ? cell.value : JSON.stringify(cell.value));
  })));
  const exported = exportedText.join('\n');
  for (const forbidden of [
    '/Users/private', '/private/import.pdf', '/private/source.pdf', 'file:///private',
    'request-secret-hash', 'request-event-hash', 'idem-secret-hash', 'idem-event-hash',
    'must-not-export', 'import_metadata',
  ]) {
    assert.doesNotMatch(exported, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }
  console.log('invalidity portal contract tests passed');
}

void main();
