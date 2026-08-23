import { promises as fs } from 'node:fs';
import path from 'node:path';
import { NextRequest, NextResponse } from 'next/server';
import {
  createUnauthorizedResponse,
  getCurrentUserFromRequest,
} from '@/lib/auth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

type JsonObject = Record<string, unknown>;

const CASE_LABELS: Record<string, string> = {
  'water_gun_decision_2021227533927': '水枪专利',
  'wireless-earphone-decision-2022215166391': '可适配不同耳型的无线耳机',
  'photoinitiator-decision-2017111751351': '光引发剂及光固化组合物',
  'battery-slurry-decision-2014106703299': '锂电池隔膜水性陶瓷浆料',
  'gas-stove-decision-2016110150593': '燃气灶调节装置',
};

// Strict source-only benchmark whitelist.  Each case points to an immutable
// target-only I2 plan and an oracle-free I4-S freeze.  Do not replace this with
// directory auto-discovery: a later evaluator directory may contain human
// decision annotations that must never become runtime input.
const CASE_FREEZES: Record<string, { planVersion: string; comparisonVersion: string; precheckVersion: string }> = {
  'water_gun_decision_2021227533927': {
    planVersion: '20260808-five-case-i2v3-r5',
    comparisonVersion: '20260806-five-case-v50',
    precheckVersion: '20260808-five-case-i4o-r1',
  },
  'wireless-earphone-decision-2022215166391': {
    planVersion: '20260808-five-case-i2v3-r5',
    comparisonVersion: '20260806-five-case-v50',
    precheckVersion: '20260808-five-case-i4o-r1',
  },
  'photoinitiator-decision-2017111751351': {
    planVersion: '20260808-five-case-i2v3-r5',
    comparisonVersion: '20260806-five-case-v50',
    precheckVersion: '20260808-five-case-i4o-r1',
  },
  'battery-slurry-decision-2014106703299': {
    planVersion: '20260808-five-case-i2v3-r5',
    comparisonVersion: '20260806-five-case-v50',
    precheckVersion: '20260808-five-case-i4o-r1',
  },
  'gas-stove-decision-2016110150593': {
    planVersion: '20260808-five-case-i2v3-r5',
    comparisonVersion: '20260806-five-case-v50',
    precheckVersion: '20260808-five-case-i4o-r1',
  },
};

function projectRoot(): string {
  return path.resolve(process.cwd(), '..');
}

function fixtureRoot(): string {
  return path.join(
    projectRoot(),
    '5-invalidity-search-test',
    'tests',
    'fixtures',
    'blind_benchmarks',
  );
}

function frozenRoot(version: string): string {
  return path.join(
    projectRoot(),
    '.data',
    'invalidity',
    'test',
    'decision-benchmarks',
    version,
  );
}

async function firstReadableDirectory(candidates: string[]): Promise<string> {
  for (const candidate of candidates) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {
      // Fall through to the last complete five-case baseline.
    }
  }
  throw new Error('No frozen benchmark directory is readable');
}

async function readJson(filePath: string): Promise<JsonObject> {
  return JSON.parse(await fs.readFile(filePath, 'utf8')) as JsonObject;
}

function rows(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.filter(
        (item): item is JsonObject => Boolean(item) && typeof item === 'object' && !Array.isArray(item),
      )
    : [];
}

function fileStem(fileName: string): string {
  return fileName.replace(/\.json$/i, '');
}

const PRECHECK_DISCLOSED_STATUSES = new Set([
  'explicit',
  'direct_and_unambiguous',
  'necessarily_implicit',
]);

function comparisonDisclosures(item: JsonObject): JsonObject[] {
  return rows((item.comparison as JsonObject | undefined)?.disclosures);
}

function comparisonDocumentId(item: JsonObject): string {
  const comparison = (item.comparison || {}) as JsonObject;
  return String(
    comparison.publication_number
    || comparison.document_id
    || item.documentFile
    || '',
  );
}

function conservativePrecheck(comparisons: JsonObject[]): JsonObject {
  const closest = [...comparisons].sort((left, right) => (
    comparisonDisclosures(right).filter((row) =>
      PRECHECK_DISCLOSED_STATUSES.has(String(row.status || '')),
    ).length
    - comparisonDisclosures(left).filter((row) =>
      PRECHECK_DISCLOSED_STATUSES.has(String(row.status || '')),
    ).length
  ))[0];
  const documentId = closest ? comparisonDocumentId(closest) : '';
  const differences = closest
    ? comparisonDisclosures(closest).filter(
        (row) => !PRECHECK_DISCLOSED_STATUSES.has(String(row.status || '')),
      )
    : [];
  const featureGroups = differences.map((difference, index) => {
    const featureId = String(difference.feature_id || `difference-${index + 1}`);
    const featureText = String(difference.feature_text || featureId);
    return {
      feature_group_id: `G${String(index + 1).padStart(2, '0')}`,
      feature_ids: [featureId],
      feature_texts: [featureText],
      objective_technical_problem: '冻结材料尚不足以可靠概括该区别特征解决的客观技术问题',
      d1_teaching: {
        status: 'uncertain',
        evidence_quote: String(difference.evidence_quote || ''),
        evidence_location: String(difference.evidence_location || ''),
        reasoning: '当前示例没有冻结独立 I4-O 运行，不从单篇披露状态推定 D1 已给出组合启示。',
        confidence: 0,
      },
      routine_means: {
        status: 'uncertain',
        evidence_quote: '',
        evidence_location: '',
        reasoning: '尚未取得可引用的惯用手段或公知常识依据。',
        confidence: 0,
      },
      modification_motivation: {
        status: 'uncertain',
        evidence_quote: '',
        evidence_location: '',
        reasoning: '尚未完成修改动机和具体修改路径分析。',
        confidence: 0,
      },
      teaching_away: {
        status: 'uncertain',
        evidence_quote: '',
        evidence_location: '',
        reasoning: '尚未对 D1 全文完成反向教导分析。',
        confidence: 0,
      },
      technical_effect: {
        status: 'uncertain',
        evidence_quote: '',
        evidence_location: '',
        reasoning: '尚未分析所得技术效果是否可预期。',
        confidence: 0,
      },
      modification_path: '',
      search_route: 'search_direct_feature_evidence',
      ordinary_structural_search_required: true,
      common_knowledge_confirmation_required: false,
      evidence_complete: false,
      lawyer_confirmation_required: true,
      reasoning: 'fail-safe：五项预分析均未冻结，保留直接特征检索。',
    };
  });
  return {
    closest_document_id: documentId,
    feature_groups: featureGroups,
    resolved_feature_ids: [],
    unresolved_feature_ids: featureGroups.flatMap((group) => group.feature_ids),
    search_targets: featureGroups.map((group) => ({
      feature_group_id: group.feature_group_id,
      feature_ids: group.feature_ids,
      route: group.search_route,
      target_gap_type: 'feature_gap',
      search_anchor: group.feature_texts[0],
      rationale: group.reasoning,
    })),
    ordinary_structural_search_required: featureGroups.length > 0,
    evidence_complete: false,
    model: 'derived-source-only-fail-safe',
    used_target_images: 1,
    used_document_images: 1,
  };
}

async function loadPrecheck(
  caseId: string,
  claimId: string,
  comparisons: JsonObject[],
): Promise<JsonObject> {
  const fixture = path.join(
    projectRoot(),
    '5-invalidity-search-test',
    'tests',
    'fixtures',
    'obviousness_precheck',
    caseId,
    `claim-${claimId}.json`,
  );
  try {
    return await readJson(fixture);
  } catch {
    return conservativePrecheck(comparisons);
  }
}

async function caseSummary(caseId: string): Promise<JsonObject> {
  const manifest = await readJson(path.join(fixtureRoot(), caseId, 'manifest.json'));
  const target = (manifest.target || {}) as JsonObject;
  return {
    caseId,
    label: CASE_LABELS[caseId],
    target: {
      applicationNumber: target.application_number,
      publicationNumber: target.publication_number,
      title: target.title,
      fileName: target.file_name,
    },
    sourceDocumentCount: rows(manifest.documents).length,
  };
}

async function selectedCase(caseId: string): Promise<JsonObject> {
  const summary = await caseSummary(caseId);
  const manifest = await readJson(path.join(fixtureRoot(), caseId, 'manifest.json'));
  const freeze = CASE_FREEZES[caseId];
  if (!freeze) throw new Error('Benchmark case is not whitelisted');
  const planDir = await firstReadableDirectory([
    path.join(frozenRoot(freeze.planVersion), 'plans', caseId),
  ]);
  const comparisonDir = path.join(
    frozenRoot(freeze.comparisonVersion),
    'i4s',
    caseId,
  );
  const planFiles = (await fs.readdir(planDir))
    .filter((name) => /^claim-.+\.json$/i.test(name))
    .sort((left, right) => left.localeCompare(right, undefined, { numeric: true }));
  const claims = await Promise.all(planFiles.map(async (fileName) => {
    const planEnvelope = await readJson(path.join(planDir, fileName));
    const claimId = String(planEnvelope.claim_id || fileStem(fileName).replace(/^claim-/, ''));
    const comparisonEnvelope = await readJson(
      path.join(comparisonDir, `claim-${claimId}.json`),
    );
    const latestByDocument = new Map<string, JsonObject>();
    for (const row of rows(comparisonEnvelope.comparisons)) {
      const documentFile = path.basename(String(row.document_file || ''));
      if (!documentFile) continue;
      const comparison = row.comparison as JsonObject | undefined;
      if (!comparison || typeof comparison !== 'object') continue;
      latestByDocument.set(documentFile, {
        documentFile,
        comparison,
      });
    }
    const comparisons = [...latestByDocument.values()];
    return {
      claimId,
      criticalDate: planEnvelope.critical_date,
      queryPlan: planEnvelope.plan,
      comparisons,
      obviousnessPrecheck: await loadPrecheck(caseId, claimId, comparisons),
    };
  }));
  return {
    ...summary,
    frozenAt: '2026-08-08',
    blindProtocol: {
      targetOnlyQueryGeneration: true,
      sourceOnlySingleReferenceAnalysis: true,
      decisionConclusionsImported: false,
    },
    freezeVersions: freeze,
    stageCoverage: {
      target: {
        mode: 'frozen_result',
        note: '读取冻结目标专利清单中的著录事实。',
      },
      date: {
        mode: 'frozen_result',
        note: '读取当前 r5 目标专利画像实际采用的独立权利要求关键日。',
      },
      profile: {
        mode: 'frozen_result',
        note: '读取当前 r5 的目标专利发明点画像。',
      },
      query: {
        mode: 'frozen_result',
        note: '读取当前 r5 的目标专利检索式。',
      },
      evidence: {
        mode: 'source_fixture',
        note: '展示盲测实际使用的源文献及日期；这不是一次实时 provider 检索日志。',
      },
      singleReference: {
        mode: 'frozen_result',
        note: '读取当前 v50 的逐份全文单篇比对结果。',
      },
      closestPriorArt: {
        mode: 'derived_preview',
        note: '依据冻结单篇比对覆盖数生成 D1 复核候选；未冒充独立 I4-C 冻结结论。',
      },
      obviousnessPrecheck: {
        mode: 'derived_preview',
        note: '逐组展示 D1 技术启示、惯用手段、修改动机、反向教导和效果预期；无线耳机案使用已冻结的来源证据复核，其余案例缺少独立 I4-O 时按 fail-safe 明示待分析。',
      },
      gapSearch: {
        mode: 'derived_preview',
        note: '严格读取 I4-O 路由，仅展示应补公知常识、组合证据或直接特征的目标；不伪造尚未运行的检索式。',
      },
      inventiveStep: {
        mode: 'derived_preview',
        note: '只展示冻结证据可支持的组合候选和缺口；不导入无效决定结论。',
      },
      report: {
        mode: 'derived_preview',
        note: '把冻结单篇证据汇总为大 Claim Chart 预览；不冒充正式律师结论。',
      },
    },
    sourceDocuments: rows(manifest.documents).map((document) => ({
      documentFile: path.basename(String(document.file_name || '')),
      publicationNumber: document.publication_number,
      title: document.title,
      publicationDate: document.publication_date,
      sourceProvider: document.source_provider,
      sourceUrl: document.source_url,
      pageCount: document.page_count,
      sha256: document.sha256,
    })),
    claims,
  };
}

export async function GET(request: NextRequest) {
  const user = await getCurrentUserFromRequest(request);
  if (!user) return createUnauthorizedResponse(request);
  if (user.status !== 'approved') {
    return NextResponse.json({ error: '账号尚未获准使用测试功能' }, { status: 403 });
  }
  const caseId = request.nextUrl.searchParams.get('caseId') || '';
  try {
    if (caseId) {
      if (!Object.hasOwn(CASE_LABELS, caseId)) {
        return NextResponse.json({ error: '内置盲测案例不存在' }, { status: 404 });
      }
      return NextResponse.json(await selectedCase(caseId));
    }
    return NextResponse.json({
      cases: await Promise.all(Object.keys(CASE_LABELS).map(caseSummary)),
    });
  } catch (error) {
    console.error('Failed to read invalidity benchmark case', error);
    return NextResponse.json(
      { error: '内置盲测案例尚未完成冻结或文件不可读' },
      { status: 503 },
    );
  }
}
