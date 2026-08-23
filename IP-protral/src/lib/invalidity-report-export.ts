import ExcelJS from 'exceljs';
import {
  assertInvalidityReportDataV1,
  claimDisplayLabel,
  invalidityNoveltyCcDisclosures,
  reportRows,
  reportSnapshotDate,
  type JsonObject,
} from '@/lib/invalidity-contracts';

const EXCEL_CELL_TEXT_LIMIT = 30_000;
const LOCAL_LOCATION = /(?:file:\/\/|\/Users\/|\/private\/|\/var\/folders\/|[a-zA-Z]:[\\/])[^\s,;\]\}\)]*/g;

function omitExportKey(key: string): boolean {
  const normalized = key.toLowerCase().replace(/[^a-z0-9]/g, '');
  if (normalized === 'importmetadata') return true;
  if (normalized.endsWith('uri')) return true;
  if (
    normalized.startsWith('request')
    && /(?:hash|sha256|digest|fingerprint)$/.test(normalized)
  ) return true;
  return normalized.startsWith('idempotency')
    && /(?:key|hash|sha256|digest|fingerprint)/.test(normalized);
}

function localLocationKey(key: string): boolean {
  return /(?:^|_)(?:path|uri)$/i.test(key);
}

/**
 * Report data is already allowlisted by the backend. Keep a second boundary at
 * the workbook writer because audit/event payloads are intentionally nested and
 * can otherwise reintroduce server-only implementation details.
 */
export function sanitizeInvalidityExportValue(input: unknown, key = ''): unknown {
  if (omitExportKey(key)) return undefined;
  if (Array.isArray(input)) {
    return input
      .map((item) => sanitizeInvalidityExportValue(item, key))
      .filter((item) => item !== undefined);
  }
  if (input && typeof input === 'object') {
    return Object.fromEntries(
      Object.entries(input as Record<string, unknown>)
        .filter(([childKey]) => !omitExportKey(childKey))
        .map(([childKey, childValue]) => [
          childKey,
          sanitizeInvalidityExportValue(childValue, childKey),
        ])
        .filter((entry) => entry[1] !== undefined),
    );
  }
  if (typeof input !== 'string') return input;
  if (localLocationKey(key) && (
    input.startsWith('/')
    || /^[a-zA-Z]:[\\/]/.test(input)
    || /^file:\/\//i.test(input)
  )) return undefined;
  return input.replace(LOCAL_LOCATION, '[REDACTED LOCAL LOCATION]');
}

function rows(value: unknown): JsonObject[] {
  return reportRows(value);
}

function value(input: unknown): string | number | boolean {
  const sanitized = sanitizeInvalidityExportValue(input);
  if (sanitized == null) return '';
  if (typeof sanitized === 'number' || typeof sanitized === 'boolean') return sanitized;
  const output = typeof sanitized === 'string' ? sanitized : JSON.stringify(sanitized);
  return output.length <= EXCEL_CELL_TEXT_LIMIT
    ? output
    : `${output.slice(0, EXCEL_CELL_TEXT_LIMIT)}\n[单元格内容已截断；完整数据保留在服务审计记录中]`;
}

function style(sheet: ExcelJS.Worksheet, widths: number[]) {
  sheet.views = [{ state: 'frozen', ySplit: 1 }];
  widths.forEach((width, index) => { sheet.getColumn(index + 1).width = width; });
  sheet.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' } };
  sheet.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1F4E78' } };
  sheet.eachRow((row) => {
    row.alignment = { vertical: 'top', wrapText: true };
    row.eachCell((cell) => { cell.border = { bottom: { style: 'thin', color: { argb: 'FFD9E2F3' } } }; });
  });
  sheet.autoFilter = { from: { row: 1, column: 1 }, to: { row: 1, column: widths.length } };
}

function worksheetNamePart(input: unknown): string {
  return String(input || '')
    .replace(/[\\/*?:[\]]/g, '-')
    .replace(/[\u0000-\u001f]/g, '')
    .replace(/^'+|'+$/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function uniqueWorksheetName(workbook: ExcelJS.Workbook, preferred: string): string {
  const base = worksheetNamePart(preferred).slice(0, 31) || '逐篇CC';
  if (!workbook.getWorksheet(base)) return base;
  for (let suffix = 2; suffix < 10_000; suffix += 1) {
    const marker = `-${suffix}`;
    const candidate = `${base.slice(0, 31 - marker.length)}${marker}`;
    if (!workbook.getWorksheet(candidate)) return candidate;
  }
  throw new Error('无法为逐篇 Claim Chart 生成唯一工作表名称');
}

function disclosureStatusLabel(input: unknown): string {
  const status = String(input || '');
  if (['disclosed', 'explicit', 'direct_and_unambiguous', 'structural_equivalent'].includes(status)) return '有（明确）';
  if (status === 'necessarily_implicit') return '有（必然隐含）';
  if (status === 'not_disclosed') return '未披露';
  if (status === 'analysis_failed') return '分析失败';
  if (['uncertain', 'insufficient_evidence', 'material_incomplete'].includes(status)) return '待确认';
  return '未完成分析';
}

function documentIdentifier(document: JsonObject): string {
  const identifiers = document.identifiers && typeof document.identifiers === 'object'
    ? document.identifiers as JsonObject
    : {};
  return String(
    document.publication_number
    || identifiers.publication_number
    || document.canonical_key
    || document.id
    || '',
  );
}

export async function buildInvalidityWorkbook(report: unknown): Promise<Buffer> {
  assertInvalidityReportDataV1(report);
  const workbook = new ExcelJS.Workbook();
  workbook.creator = 'IP-Probtra Invalidity Search';
  const generatedAt = reportSnapshotDate(report);
  workbook.created = generatedAt;
  workbook.modified = generatedAt;

  const investigation = report.investigation;
  const claims = rows(report.claim_investigations || report.claims);
  const limitations = rows(report.claim_limitations || report.limitations);
  const documents = rows(report.documents);
  const disclosures = rows(report.feature_disclosures || report.disclosures);
  const qualifications = rows(report.document_qualifications || report.qualifications);
  const combinations = rows(report.combinations);
  const inventiveNarratives = rows(report.inventive_step_narratives);
  const similarityClaimCharts = rows(report.similarity_claim_charts);
  const queries = rows(report.queries);
  const events = rows(report.events);
  const gaps = rows(report.gap_items || report.gaps);
  const sources = rows(report.document_sources);
  const closestVersions = rows(report.closest_prior_art_versions);
  const iterations = rows(report.iterations);
  const moduleRuns = rows(report.module_runs);
  const jobs = rows(report.jobs);
  const documentVersions = rows(report.document_versions);
  const humanReviewActions = rows(report.human_review_actions);
  const dateFactRevisions = rows(report.document_date_fact_revisions);
  const evidenceImports = rows(report.evidence_imports);

  const byId = (items: JsonObject[]) => new Map(items.map((item) => [String(item.id || ''), item]));
  const claimById = byId(claims);
  const limitationById = byId(limitations);
  const documentById = byId(documents);
  const iterationById = byId(iterations);
  const documentVersionById = byId(documentVersions);
  const reviewActionById = byId(humanReviewActions);
  const noveltyDisclosures = invalidityNoveltyCcDisclosures({
    claims,
    limitations,
    disclosures,
    qualifications,
  });

  const overview = workbook.addWorksheet('调查概要');
  overview.addRow(['字段', '内容']);
  [
    ['调查 ID', investigation.id],
    ['Portal 会话', investigation.analysis_session_id],
    ['环境', investigation.environment],
    ['状态', investigation.status],
    ['管线版本', investigation.pipeline_version],
    ['报告契约', report.contract_version],
    ['报告类型', report.report_kind],
    ['报告快照 ID', report.report_snapshot_id || report.snapshot_id],
    ['报告内容哈希', report.snapshot_sha256],
    ['报告源调查状态版本', report.source_state_version],
    ['报告源人工复核版本', report.source_review_revision],
    ['当前调查状态版本', investigation.state_version],
    ['当前人工复核版本', investigation.review_revision],
    ['后端快照生成时间', generatedAt.toISOString()],
    ['创建时间', investigation.created_at],
    ['完成时间', investigation.completed_at],
    ['分析边界', '本报告是检索与分析辅助材料，不是无效决定或正式法律意见。'],
    ['证据规则', '搜索命中不等于证据；仅真实内容、日期资格和来源链核验通过的材料可进入 CC 表。'],
  ].forEach((item) => overview.addRow(item.map(value)));
  style(overview, [24, 110]);

  const claimSheet = workbook.addWorksheet('逐权利要求');
  claimSheet.addRow(['权利要求', '状态', '关键日', '目标公开日', '当前轮次', '停止原因', '结果摘要']);
  claims.forEach((claim) => claimSheet.addRow([
    value(claimDisplayLabel(claim)), value(claim.status), value(claim.critical_date), value(claim.target_publication_date),
    value(claim.current_iteration_no), value(claim.terminal_reason), value(claim.result_summary),
  ]));
  style(claimSheet, [14, 24, 16, 16, 12, 45, 70]);

  const narrativeSheet = workbook.addWorksheet('创造性文字分析');
  narrativeSheet.addRow(['独立权利要求', 'D1', '证据状态', '结论', '人类可读分析']);
  inventiveNarratives.forEach((item) => narrativeSheet.addRow([
    value(item.claim_id || item.claim_investigation_id),
    value(item.d1_document_id),
    value(item.evidence_complete === true ? '证据链闭合' : '证据仍有缺口'),
    value(item.conclusion_text),
    value(Array.isArray(item.paragraphs) ? item.paragraphs.join('\n\n') : item.paragraphs),
  ]));
  style(narrativeSheet, [18, 38, 18, 55, 120]);

  similarityClaimCharts.forEach((chart, chartIndex) => {
    const rankedDocuments = rows(chart.ranked_documents);
    const claimLabel = String(chart.claim_id || chartIndex + 1).replace(/[\\/*?:[\]]/g, '-');
    const sheet = workbook.addWorksheet(`Top10-权利要求${claimLabel}`.slice(0, 31));
    sheet.addRow([
      '独立权利要求技术特征',
      ...rankedDocuments.map((ranked, index) => {
        const document = (ranked.document && typeof ranked.document === 'object')
          ? ranked.document as JsonObject
          : {};
        const identifiers = (document.identifiers && typeof document.identifiers === 'object')
          ? document.identifiers as JsonObject
          : {};
        const label = document.title
          || identifiers.publication_number
          || document.canonical_key
          || ranked.document_id;
        return `第${ranked.rank || index + 1}名 · ${String(label || '')}\n已披露${ranked.confirmed_disclosed_feature_count || 0}/${ranked.total_feature_count || 0}`;
      }),
    ]);
    rows(chart.feature_rows).forEach((feature) => sheet.addRow([
      value(`${feature.feature_key || ''} · ${feature.limitation_text || ''}`),
      ...rows(feature.cells).map((cell) => value(cell.disclosure_status)),
    ]));
    sheet.addRow(['排序规则', value(chart.ranking_basis)]);
    style(sheet, [60, ...rankedDocuments.map(() => 28)]);
  });

  const latestDisclosureByDocumentFeature = new Map<string, JsonObject>();
  disclosures.forEach((disclosure) => {
    const documentId = String(disclosure.document_id || '');
    const claimInvestigationId = String(disclosure.claim_investigation_id || '');
    const limitationId = String(disclosure.limitation_id || disclosure.feature_id || '');
    if (!documentId || !claimInvestigationId || !limitationId) return;
    const document = documentById.get(documentId) || {};
    const currentVersionId = String(document.current_document_version_id || '');
    const disclosureVersionId = String(disclosure.document_version_id || '');
    if (currentVersionId && disclosureVersionId && currentVersionId !== disclosureVersionId) return;
    const key = `${documentId}\u0000${claimInvestigationId}\u0000${limitationId}`;
    const existing = latestDisclosureByDocumentFeature.get(key);
    const timestamp = Date.parse(String(disclosure.updated_at || disclosure.created_at || '')) || 0;
    const existingTimestamp = existing
      ? Date.parse(String(existing.updated_at || existing.created_at || '')) || 0
      : -1;
    if (!existing || timestamp >= existingTimestamp) latestDisclosureByDocumentFeature.set(key, disclosure);
  });

  const analyzedDocumentIds = [...new Set(
    [...latestDisclosureByDocumentFeature.values()]
      .map((item) => String(item.document_id || ''))
      .filter(Boolean),
  )].sort((left, right) => {
    const leftDocument = documentById.get(left) || {};
    const rightDocument = documentById.get(right) || {};
    return documentIdentifier(leftDocument).localeCompare(documentIdentifier(rightDocument), 'zh-CN');
  });
  const perDocumentIndex = workbook.addWorksheet('逐篇CC索引');
  perDocumentIndex.addRow(['序号', '公开号 / 文献标识', '标题', '文献类型', '证据层级', '逐篇 Claim Chart Sheet']);
  analyzedDocumentIds.forEach((documentId, documentIndex) => {
    const document = documentById.get(documentId) || { id: documentId };
    const identifier = documentIdentifier(document);
    const sheetName = uniqueWorksheetName(
      workbook,
      `CC-${String(documentIndex + 1).padStart(2, '0')}-${identifier || documentIndex + 1}`,
    );
    const sheet = workbook.addWorksheet(sheetName);
    sheet.addRow([
      '独立权利要求', '特征编号', '目标技术特征', '披露判断', '对比文件原文', '证据位置',
      '目标结构角色', '对比文件结构对应', '分析理由', 'I4-S run', '文献版本',
    ]);
    [...latestDisclosureByDocumentFeature.values()]
      .filter((item) => String(item.document_id || '') === documentId)
      .sort((left, right) => {
        const leftClaim = claimById.get(String(left.claim_investigation_id || ''));
        const rightClaim = claimById.get(String(right.claim_investigation_id || ''));
        const leftLimitation = limitationById.get(String(left.limitation_id || left.feature_id || '')) || {};
        const rightLimitation = limitationById.get(String(right.limitation_id || right.feature_id || '')) || {};
        return claimDisplayLabel(leftClaim, left.claim_investigation_id).localeCompare(
          claimDisplayLabel(rightClaim, right.claim_investigation_id),
          'zh-CN',
        ) || String(leftLimitation.feature_key || '').localeCompare(String(rightLimitation.feature_key || ''), 'zh-CN');
      })
      .forEach((disclosure) => {
        const claim = claimById.get(String(disclosure.claim_investigation_id || ''));
        const limitation = limitationById.get(String(disclosure.limitation_id || disclosure.feature_id || '')) || {};
        const analysis = disclosure.analysis && typeof disclosure.analysis === 'object'
          ? disclosure.analysis as JsonObject
          : {};
        sheet.addRow([
          value(claimDisplayLabel(claim, disclosure.claim_investigation_id)),
          value(limitation.feature_key || disclosure.feature_id || disclosure.limitation_id),
          value(limitation.limitation_text || limitation.text),
          value(disclosureStatusLabel(disclosure.disclosure_status)),
          value(disclosure.excerpt || disclosure.evidence_quote),
          value(disclosure.locator || disclosure.evidence_location),
          value(analysis.target_structural_role),
          value({
            reference_structure_mapping: analysis.reference_structure_mapping,
            structural_evidence: analysis.structural_evidence,
            mapping_basis: analysis.mapping_basis,
          }),
          value(analysis.reasoning || disclosure.reasoning),
          value(disclosure.module_run_id),
          value(disclosure.document_version_id || document.current_document_version_id),
        ]);
      });
    style(sheet, [20, 18, 60, 18, 75, 38, 55, 75, 90, 38, 38]);
    perDocumentIndex.addRow([
      documentIndex + 1,
      value(identifier),
      value(document.title),
      value(document.document_type),
      value(document.evidence_level),
      { text: sheetName, hyperlink: `#'${sheetName.replace(/'/g, "''")}'!A1` },
    ]);
  });
  style(perDocumentIndex, [10, 32, 60, 18, 24, 38]);

  const cc = workbook.addWorksheet('新颖性CC');
  cc.addRow(['权利要求调查', '特征编号', '目标技术特征', '单一对比文件', '披露状态', '证据原文', '证据位置', '模型/规则']);
  noveltyDisclosures.forEach((item) => {
    const limitation = limitationById.get(String(item.limitation_id || '')) || {};
    const document = documentById.get(String(item.document_id || '')) || {};
    const claim = claimById.get(String(item.claim_investigation_id || ''));
    cc.addRow([
      value(claimDisplayLabel(claim, item.claim_investigation_id)), value(limitation.feature_key), value(limitation.limitation_text),
      value(document.title || document.canonical_key), value(item.disclosure_status), value(item.excerpt),
      value(item.locator), value({ model: item.model_version, prompt: item.prompt_version, rule: item.rule_version }),
    ]);
  });
  style(cc, [22, 14, 60, 38, 22, 70, 35, 35]);

  const inventive = workbook.addWorksheet('创造性组合');
  inventive.addRow(['权利要求调查', '轮次', 'D1 版本', '组合文献', '特征覆盖完整', '组合动机状态', '状态', '分析与证据']);
  combinations.forEach((item) => inventive.addRow([
    value(claimDisplayLabel(claimById.get(String(item.claim_investigation_id || '')), item.claim_investigation_id)),
    value(iterationById.get(String(item.iteration_id || ''))?.iteration_no || item.iteration_id), value(item.closest_prior_art_version_id),
    value(item.document_ids), value(item.coverage_complete), value(item.motivation_status), value(item.status), value(item.analysis),
  ]));
  style(inventive, [22, 22, 22, 38, 18, 22, 20, 90]);

  const evidence = workbook.addWorksheet('文献与日期资格');
  evidence.addRow(['权利要求', '文献 ID', '标题', '类型', '证据层级', '评估版本', '关键日/公开日', '资格类型', '新颖性可用', '创造性可用', '核验状态与理由', '来源']);
  qualifications.forEach((qualification) => {
    const document = documentById.get(String(qualification.document_id || '')) || {};
    const claim = claimById.get(String(qualification.claim_investigation_id || ''));
    const documentSources = sources.filter((item) => String(item.document_id) === String(qualification.document_id));
    evidence.addRow([
      value(claimDisplayLabel(claim, qualification.claim_investigation_id)), value(document.id), value(document.title),
      value(document.document_type), value(document.evidence_level), value(qualification.assessment_version),
      value({ critical_date: qualification.critical_date, publication_date: qualification.publication_date }),
      value(qualification.eligibility_type), value(qualification.novelty_eligible), value(qualification.inventive_step_eligible),
      value({ status: qualification.verification_status, reason: qualification.verification_reason }),
      value(documentSources.map((item) => ({ provider: item.provider, url: item.source_url, sha256: item.snapshot_sha256 }))),
    ]);
  });
  style(evidence, [20, 34, 42, 16, 18, 12, 35, 24, 14, 14, 55, 65]);

  const exclusionSheet = workbook.addWorksheet('排除与限制记录');
  exclusionSheet.addRow(['权利要求', '文献', '评估版本', '资格类型', '新颖性可用', '创造性可用', '核验状态', '排除/限制理由', '日期事实']);
  qualifications
    .filter((item) => item.novelty_eligible !== true || item.inventive_step_eligible !== true || ['excluded', 'lead_only', 'needs_human_review'].includes(String(item.eligibility_type || '')))
    .forEach((item) => {
      const claim = claimById.get(String(item.claim_investigation_id || ''));
      const document = documentById.get(String(item.document_id || '')) || {};
      exclusionSheet.addRow([
        value(claimDisplayLabel(claim, item.claim_investigation_id)), value(document.title || document.canonical_key),
        value(item.assessment_version), value(item.eligibility_type), value(item.novelty_eligible),
        value(item.inventive_step_eligible), value(item.verification_status), value(item.verification_reason),
        value({ critical_date: item.critical_date, priority: item.earliest_priority_date, filing: item.filing_date, publication: item.publication_date, public_availability: item.public_availability_date }),
      ]);
    });
  style(exclusionSheet, [20, 42, 12, 22, 14, 14, 20, 70, 60]);

  const d1History = workbook.addWorksheet('D1版本历史');
  d1History.addRow(['权利要求', '版本', '轮次', 'D1 文献', '当前版本', '替代版本', '选择方式', '指标', '选择/替换理由', '创建时间']);
  closestVersions.forEach((item) => {
    const claim = claimById.get(String(item.claim_investigation_id || ''));
    const document = documentById.get(String(item.document_id || '')) || {};
    const iteration = iterationById.get(String(item.iteration_id || '')) || {};
    d1History.addRow([
      value(claimDisplayLabel(claim, item.claim_investigation_id)), value(item.version_no), value(iteration.iteration_no),
      value(document.title || document.canonical_key), value(item.is_current), value(item.supersedes_id),
      value(item.selected_by), value(item.metrics), value(item.rationale), value(item.created_at),
    ]);
  });
  style(d1History, [20, 10, 10, 42, 12, 36, 18, 55, 75, 24]);

  const iterationSheet = workbook.addWorksheet('轮次与停止原因');
  iterationSheet.addRow(['权利要求', '轮次', '父轮次', '目的', '触发原因', '状态', '目标 gap', '指标', '停止原因', '开始', '完成']);
  iterations.forEach((item) => {
    const claim = claimById.get(String(item.claim_investigation_id || ''));
    iterationSheet.addRow([
      value(claimDisplayLabel(claim, item.claim_investigation_id)), value(item.iteration_no), value(item.parent_iteration_id),
      value(item.purpose), value(item.trigger_reason), value(item.status), value(item.gap_feature_ids), value(item.metrics),
      value(item.stop_reason), value(item.started_at), value(item.completed_at),
    ]);
  });
  style(iterationSheet, [20, 10, 36, 26, 55, 18, 45, 55, 65, 24, 24]);

  const taskSheet = workbook.addWorksheet('任务失败与重试');
  taskSheet.addRow(['模块', 'module run', 'job', '运行状态', '作业状态', '尝试/上限', '可重试', '错误代码', '错误/最后错误', '开始', '完成']);
  const jobsByRun = new Map(jobs.map((item) => [String(item.module_run_id || ''), item]));
  moduleRuns.forEach((run) => {
    const job = jobsByRun.get(String(run.id || '')) || {};
    taskSheet.addRow([
      value(run.module_code), value(run.id), value(job.id), value(run.status), value(job.status),
      value(`${job.attempt_count ?? run.attempt_no ?? 0}/${job.max_attempts ?? ''}`), value(run.retryable),
      value(run.error_code), value(run.error_message || job.last_error), value(run.started_at || job.leased_at),
      value(run.completed_at || job.finished_at),
    ]);
  });
  style(taskSheet, [24, 36, 36, 18, 18, 14, 12, 20, 75, 24, 24]);

  const audit = workbook.addWorksheet('检索审计');
  audit.addRow(['类型', '时间/状态', '表达式/事件', '特征/载荷']);
  queries.forEach((item) => audit.addRow(['query', value(item.status), value(item.expression), value({ features: item.feature_ids, date_filter: item.date_filter, provider: item.provider_plan })]));
  events.forEach((item) => audit.addRow(['event', value(item.occurred_at), value(item.event_type), value(item.payload)]));
  style(audit, [16, 24, 70, 100]);

  const gapSheet = workbook.addWorksheet('未解决缺口');
  gapSheet.addRow(['权利要求调查', '轮次', '特征', '缺口类型', '状态', '说明', '检索目标', '解决证据']);
  gaps.forEach((item) => {
    const limitation = limitationById.get(String(item.limitation_id || '')) || {};
    const claim = claimById.get(String(item.claim_investigation_id || ''));
    gapSheet.addRow([
      value(claimDisplayLabel(claim, item.claim_investigation_id)), value(iterationById.get(String(item.iteration_id || ''))?.iteration_no || item.iteration_id), value(limitation.limitation_text), value(item.gap_type),
      value(item.status), value(item.description), value(item.search_objective), value(item.resolution_evidence),
    ]);
  });
  style(gapSheet, [22, 22, 55, 24, 18, 65, 60, 55]);

  const reviewSheet = workbook.addWorksheet('人工复核谱系');
  reviewSheet.addRow([
    '序号', '操作 ID', '类型', '状态', '复核人', '理由', '调查版本',
    '复核版本', '权利要求版本', '失效派生项', '待重算项', '结果 / 错误', '创建 / 完成',
  ]);
  humanReviewActions.forEach((item) => reviewSheet.addRow([
    value(item.action_seq), value(item.id), value(item.action_type), value(item.status),
    value(item.actor), value(item.reason),
    value({ from: item.base_investigation_state_version, to: item.resulting_investigation_state_version }),
    value({ from: item.base_review_revision, to: item.resulting_review_revision }),
    value({ from: item.base_claim_state_versions, to: item.resulting_claim_state_versions }),
    value(item.invalidated_derivations), value(item.pending_recomputation),
    value({ result: item.result_summary, error: item.error_summary }),
    value({ created_at: item.created_at, started_at: item.started_at, completed_at: item.completed_at }),
  ]));
  style(reviewSheet, [10, 38, 24, 18, 22, 55, 28, 28, 55, 48, 48, 75, 45]);

  const versionSheet = workbook.addWorksheet('文献版本谱系');
  versionSheet.addRow([
    '文献', '文献版本 ID', '版本号', '内容 SHA-256', 'MIME', '字节数',
    '取得方式', '创建人', '创建时间',
  ]);
  documentVersions.forEach((item) => {
    const document = documentById.get(String(item.document_id || '')) || {};
    versionSheet.addRow([
      value(document.title || document.canonical_key || item.document_id), value(item.id),
      value(item.version_no), value(item.content_sha256), value(item.mime_type), value(item.byte_size),
      value(item.acquisition_kind), value(item.created_by), value(item.created_at),
    ]);
  });
  style(versionSheet, [42, 38, 10, 68, 24, 14, 22, 22, 25]);

  const dateRevisionSheet = workbook.addWorksheet('日期事实修订');
  dateRevisionSheet.addRow([
    '修订 ID', '人工操作', '文献', '文献版本', '修订版本', '被替代版本',
    '决定', '目标权利要求', '日期通道', '日期事实', '来源类型', '复核人 / 理由', '创建时间',
  ]);
  dateFactRevisions.forEach((item) => {
    const action = reviewActionById.get(String(item.review_action_id || '')) || {};
    const document = documentById.get(String(item.document_id || '')) || {};
    const documentVersion = documentVersionById.get(String(item.document_version_id || '')) || {};
    dateRevisionSheet.addRow([
      value(item.id), value({ action_seq: action.action_seq, action_id: item.review_action_id }),
      value(document.title || document.canonical_key || item.document_id),
      value({ id: item.document_version_id, version_no: documentVersion.version_no }),
      value(item.revision_no), value(item.supersedes_id), value(item.decision),
      value(item.claim_investigation_ids), value(item.date_channel),
      value({
        public_availability_date: item.public_availability_date,
        publication_date: item.publication_date,
        filing_date: item.filing_date,
        priority_date: item.priority_date,
      }),
      value(item.source_type), value({ actor: item.actor, reason: item.reason }), value(item.created_at),
    ]);
  });
  style(dateRevisionSheet, [38, 38, 42, 42, 12, 38, 22, 42, 28, 60, 24, 55, 25]);

  const importSheet = workbook.addWorksheet('证据导入谱系');
  importSheet.addRow([
    '导入 ID', '人工操作', '文献', '文献版本', '来源记录', '内容工件',
    '目标权利要求', '声明日期事实', '导入人 / 理由', '创建时间',
  ]);
  evidenceImports.forEach((item) => {
    const action = reviewActionById.get(String(item.review_action_id || '')) || {};
    const document = documentById.get(String(item.document_id || '')) || {};
    const documentVersion = documentVersionById.get(String(item.document_version_id || '')) || {};
    importSheet.addRow([
      value(item.id), value({ action_seq: action.action_seq, action_id: item.review_action_id }),
      value(document.title || document.canonical_key || item.document_id),
      value({ id: item.document_version_id, version_no: documentVersion.version_no }),
      value(item.document_source_id), value(item.content_artifact_id),
      value(item.claim_investigation_ids), value(item.declared_date_facts),
      value({ actor: item.actor, reason: item.reason }), value(item.created_at),
    ]);
  });
  style(importSheet, [38, 38, 42, 42, 38, 38, 42, 60, 55, 25]);

  const output = await workbook.xlsx.writeBuffer();
  return Buffer.from(output);
}
