export type JsonObject = Record<string, unknown>;
export type InvalidityEnvironment = 'test' | 'prod';

export const INVALIDITY_REPORT_CONTRACT_VERSION = 'v1' as const;
export const INVALIDITY_REPORT_KIND = 'invalidity_evidence_data' as const;

export const INVALIDITY_TERMINAL_STATUSES = new Set([
  'completed',
  'partial',
  'needs_human_review',
  'failed',
  'cancelled',
  'search_budget_exhausted',
  'exhausted',
]);

export const INVALIDITY_CONTINUABLE_CLAIM_STATUSES = new Set([
  'needs_human_review',
  'search_budget_exhausted',
  'exhausted',
  'partial',
  'failed',
]);

export const INVALIDITY_SUCCESSFUL_CLAIM_TERMINALS = new Set([
  'novelty_evidence_complete',
  'inventive_step_evidence_complete',
]);

export type CriticalDateDecision =
  | 'confirm_priority'
  | 'use_filing_date'
  | 'set_manual_date';

export type CriticalDateConfirmationRequest = {
  contract_version: typeof INVALIDITY_REPORT_CONTRACT_VERSION;
  claim_investigation_id: string;
  decision: CriticalDateDecision;
  confirmed_date: string;
  target_publication_date: string;
  basis: string;
  reason: string;
  expected_state_version: number;
  idempotency_key: string;
};

export type CriticalDateConfirmationResponse = {
  contract_version: typeof INVALIDITY_REPORT_CONTRACT_VERSION;
  confirmation_id: string;
  claim_investigation_id: string;
  confirmed_date: string;
  target_publication_date: string;
  critical_date_basis: string;
  claim_state_version: number;
  investigation_state_version: number;
  investigation_status: string;
  continuation_required: boolean;
};

export type InvestigationContinuationRequest = {
  contract_version: typeof INVALIDITY_REPORT_CONTRACT_VERSION;
  claim_investigation_ids?: string[];
  max_additional_rounds: 1 | 2 | 3;
  reason: string;
  expected_state_version: number;
  idempotency_key: string;
};

export type InvestigationContinuationResponse = {
  contract_version: typeof INVALIDITY_REPORT_CONTRACT_VERSION;
  investigation_id: string;
  investigation_status: string;
  claim_investigation_ids: string[];
  created_iteration_ids?: string[];
  investigation_state_version: number;
};

export type InvalidityReviewContext = JsonObject & {
  contract_version: typeof INVALIDITY_REPORT_CONTRACT_VERSION;
  investigation_id: string;
  status: string;
  state_version: number;
  review_revision: number;
  quiescent: boolean;
  recomputation_required: boolean;
  claims: JsonObject[];
  documents: JsonObject[];
  document_versions: JsonObject[];
  latest_qualifications: JsonObject[];
  date_fact_revisions: JsonObject[];
  current_gaps: JsonObject[];
  current_d1: JsonObject[];
  action_history: JsonObject[];
  pending_actions?: JsonObject[];
  /** Read-only compatibility with review-context drafts preceding v1.3. */
  open_gaps?: JsonObject[];
  closest_prior_art_history?: JsonObject[];
  recent_actions?: JsonObject[];
};

export type HumanReviewCommandBase = {
  contract_version: typeof INVALIDITY_REPORT_CONTRACT_VERSION;
  expected_investigation_state_version: number;
  expected_claim_state_versions: Record<string, number>;
  expected_review_revision: number;
  idempotency_key: string;
  reason: string;
};

export type EvidenceImportRequest = HumanReviewCommandBase & {
  claim_investigation_ids: string[];
  canonical_key: string;
  document_type: string;
  title: string;
  language?: string;
  source_path?: string;
  source_url?: string;
  expected_source_sha256: string;
  expected_source_byte_size: number;
  expected_source_mime_type: string;
  publication_number?: string;
  authority?: string;
  declared_date_facts?: {
    public_availability_date?: string;
    publication_date?: string;
    filing_date?: string;
    priority_date?: string;
    date_channel?: 'ordinary_prior_art' | 'cn_conflicting_application';
  };
  date_evidence?: Partial<Record<
    'public_availability_date' | 'publication_date' | 'filing_date' | 'priority_date',
    { source: string; locator?: string; artifact_id?: string }
  >>;
};

export type DocumentDateConfirmationRequest = HumanReviewCommandBase & {
  decision: 'confirm_facts' | 'exclude' | 'reopen_review';
  document_id: string;
  document_version_id: string;
  claim_investigation_ids: string[];
  expected_qualifications: Record<string, {
    qualification_id: string;
    assessment_version: number;
  }>;
  public_availability_date?: string;
  publication_date?: string;
  filing_date?: string;
  priority_date?: string;
  source_type: string;
  publication_number?: string;
  authority?: string;
  cn_application_scope?: string;
  date_channel: 'ordinary_prior_art' | 'cn_conflicting_application';
  date_evidence: Partial<Record<
    'public_availability_date' | 'publication_date' | 'filing_date' | 'priority_date',
    { source: string; locator?: string; artifact_id?: string }
  >>;
};

export type HumanReviewCommandResponse = JsonObject & {
  contract_version: typeof INVALIDITY_REPORT_CONTRACT_VERSION;
  review_action_id: string;
  action_type: string;
  status: 'queued';
  review_revision: number;
  investigation_state_version: number;
  claim_state_versions: Record<string, number>;
  module_run_id?: string | null;
  job_id?: string | null;
  invalidated_derivations: string[];
  pending_recomputation: string[];
  idempotent_replay: boolean;
  upload_receipt?: {
    sha256: string;
    byte_size: number;
    mime_type: string;
  };
};

export type InvalidityEvidenceUploadReceipt = {
  contract_version: typeof INVALIDITY_REPORT_CONTRACT_VERSION;
  upload_receipt_id: string;
  file_name: string;
  byte_size: number;
  sha256: string;
  mime_type: string;
  expires_at: string;
};

export type InvalidityReportDataV1 = JsonObject & {
  contract_version: typeof INVALIDITY_REPORT_CONTRACT_VERSION;
  report_kind: typeof INVALIDITY_REPORT_KIND;
  generated_at: string;
  is_preview: boolean;
  source_state_version: number;
  source_review_revision: number;
  snapshot_hash_scope:
    | 'preview_not_hashed'
    | 'canonical_persisted_report_data_v1_excluding_snapshot_sha256';
  snapshot_sha256?: string | null;
  investigation: JsonObject;
  inventive_step_narratives?: JsonObject[];
  similarity_claim_charts?: JsonObject[];
};

export function isJsonObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

export function reportRows(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter(isJsonObject) : [];
}

export function invalidityText(value: unknown): string {
  return value == null ? '' : String(value);
}

export function invalidityEnvironment(value: unknown): InvalidityEnvironment {
  const environment = invalidityText(value).trim();
  if (!environment) {
    throw new Error('必须显式提供 environment=test 或 environment=prod');
  }
  if (environment !== 'test' && environment !== 'prod') {
    throw new Error('environment 只能是 test 或 prod');
  }
  return environment;
}

export function isInvalidityTerminalStatus(status: unknown): boolean {
  return INVALIDITY_TERMINAL_STATUSES.has(invalidityText(status));
}

export function invalidityPortalSessionStatus(
  status: unknown,
): 'running' | 'completed' | 'error' {
  const normalized = invalidityText(status);
  if (['failed', 'cancelled'].includes(normalized)) return 'error';
  return isInvalidityTerminalStatus(normalized) ? 'completed' : 'running';
}

export function isInvaliditySuccessfulClaimTerminal(status: unknown): boolean {
  return INVALIDITY_SUCCESSFUL_CLAIM_TERMINALS.has(invalidityText(status));
}

export function isInvalidityClaimContinuable(status: unknown): boolean {
  return INVALIDITY_CONTINUABLE_CLAIM_STATUSES.has(invalidityText(status));
}

export function invalidityReportPendingMessage(value: unknown): string | null {
  if (!isJsonObject(value) || invalidityText(value.code) !== 'REPORT_PENDING') return null;
  return invalidityText(value.message || value.detail).trim()
    || '报告快照尚未建立；以下仅显示已持久化的权利要求状态';
}

export function needsInvalidityCriticalDateConfirmation(claim: JsonObject): boolean {
  const status = invalidityText(claim.status).trim();
  return !invalidityText(claim.critical_date).trim()
    || !invalidityText(claim.target_publication_date).trim()
    || status === 'awaiting_critical_date'
    || status === 'critical_date_review';
}

export function assertCriticalDateConfirmationResponse(
  value: unknown,
): asserts value is CriticalDateConfirmationResponse {
  if (!isJsonObject(value) || value.contract_version !== INVALIDITY_REPORT_CONTRACT_VERSION) {
    throw new Error('关键日确认响应契约无效');
  }
  if (
    !invalidityText(value.confirmation_id).trim()
    || !invalidityText(value.claim_investigation_id).trim()
    || !invalidityText(value.confirmed_date).trim()
    || !invalidityText(value.target_publication_date).trim()
    || !invalidityText(value.critical_date_basis).trim()
    || !invalidityText(value.investigation_status).trim()
  ) {
    throw new Error('关键日确认响应缺少必要字段');
  }
  for (const [name, version] of [
    ['claim_state_version', value.claim_state_version],
    ['investigation_state_version', value.investigation_state_version],
  ] as const) {
    if (!Number.isInteger(version) || Number(version) < 0) {
      throw new Error(`关键日确认响应的 ${name} 无效`);
    }
  }
  if (typeof value.continuation_required !== 'boolean') {
    throw new Error('关键日确认响应缺少 continuation_required');
  }
}

export function assertInvalidityReviewContext(
  value: unknown,
): asserts value is InvalidityReviewContext {
  if (!isJsonObject(value) || value.contract_version !== INVALIDITY_REPORT_CONTRACT_VERSION) {
    throw new Error('人工复核上下文契约无效');
  }
  if (!invalidityText(value.investigation_id).trim() || !invalidityText(value.status).trim()) {
    throw new Error('人工复核上下文缺少调查标识或状态');
  }
  for (const [name, version] of [
    ['state_version', value.state_version],
    ['review_revision', value.review_revision],
  ] as const) {
    if (!Number.isInteger(version) || Number(version) < 0) {
      throw new Error(`人工复核上下文的 ${name} 无效`);
    }
  }
  if (typeof value.quiescent !== 'boolean' || typeof value.recomputation_required !== 'boolean') {
    throw new Error('人工复核上下文缺少静止或重算状态');
  }
  for (const name of [
    'claims',
    'documents',
    'document_versions',
    'latest_qualifications',
    'date_fact_revisions',
    'current_gaps',
    'current_d1',
    'action_history',
  ] as const) {
    if (!Array.isArray(value[name])) throw new Error(`人工复核上下文缺少 ${name}`);
  }
}

export function invalidityReviewDocumentVersions(
  context: InvalidityReviewContext,
  document: JsonObject,
): JsonObject[] {
  const documentId = invalidityText(document.id).trim();
  const flat = context.document_versions.filter(
    (version) => invalidityText(version.document_id).trim() === documentId,
  );
  return flat.length ? flat : reportRows(document.versions || document.document_versions);
}

export function invalidityReviewCurrentDocumentVersionId(
  context: InvalidityReviewContext,
  document: JsonObject,
): string {
  const explicit = invalidityText(document.current_document_version_id).trim();
  if (explicit) return explicit;
  const versions = [...invalidityReviewDocumentVersions(context, document)];
  versions.sort((a, b) => Number(b.version_no || 0) - Number(a.version_no || 0));
  return invalidityText(versions[0]?.id).trim();
}

export function invalidityReviewQualificationsByClaim(
  context: InvalidityReviewContext,
  document: JsonObject,
): Map<string, JsonObject> {
  const documentId = invalidityText(document.id).trim();
  const versionId = invalidityReviewCurrentDocumentVersionId(context, document);
  const nested = reportRows(document.qualifications);
  const candidates = context.latest_qualifications.length
    ? context.latest_qualifications
    : nested;
  const matching = candidates.filter((qualification) => (
    invalidityText(qualification.document_id).trim() === documentId
    && (!versionId || invalidityText(qualification.document_version_id).trim() === versionId)
  ));
  const documentLevel = matching.filter(
    (qualification) => !invalidityText(qualification.limitation_id).trim(),
  );
  const result = new Map<string, JsonObject>();
  (documentLevel.length ? documentLevel : matching).forEach((qualification) => {
    const claimId = invalidityText(qualification.claim_investigation_id).trim();
    if (!claimId) return;
    const existing = result.get(claimId);
    if (
      !existing
      || Number(qualification.assessment_version || 0) >= Number(existing.assessment_version || 0)
    ) result.set(claimId, qualification);
  });
  return result;
}

export function invalidityPendingDateReviewDocuments(
  context: InvalidityReviewContext,
): JsonObject[] {
  const pendingDocumentIds = new Set<string>();
  context.latest_qualifications.forEach((qualification) => {
    if (invalidityText(qualification.limitation_id).trim()) return;
    if (invalidityText(qualification.verification_status).trim() !== 'needs_human_review') return;
    const documentId = invalidityText(qualification.document_id).trim();
    if (documentId) pendingDocumentIds.add(documentId);
  });

  const pendingCanonicalKeys = new Set<string>();
  latestInvalidityGapsByKey(context.current_gaps).forEach((gap) => {
    const kind = invalidityText(gap.gap_type || gap.kind).trim();
    const status = invalidityText(gap.status).trim();
    if (kind !== 'date_gap' || ['closed', 'resolved', 'excluded'].includes(status)) return;
    const documentKeys = Array.isArray(gap.source_document_ids)
      ? gap.source_document_ids
      : [];
    documentKeys.forEach((value) => {
      const key = invalidityText(value).trim();
      if (key) pendingCanonicalKeys.add(key);
    });
  });

  return context.documents.filter((document) => (
    pendingDocumentIds.has(invalidityText(document.id).trim())
    || pendingCanonicalKeys.has(invalidityText(document.canonical_key).trim())
  ));
}

export type InvalidityDateReviewDefaults = {
  publicAvailabilityDate: string;
  publicationDate: string;
  filingDate: string;
  priorityDate: string;
  publicationNumber: string;
  authority: string;
  sourceType: string;
  dateEvidenceSource: string;
  dateEvidenceLocator: string;
  reason: string;
  pendingClaimIds: string[];
  verificationReasons: string[];
};

export function invalidityDateReviewDefaults(
  context: InvalidityReviewContext,
  document: JsonObject,
): InvalidityDateReviewDefaults {
  const dates = isJsonObject(document.dates) ? document.dates : {};
  const identifiers = isJsonObject(document.identifiers) ? document.identifiers : {};
  const qualifications = [...invalidityReviewQualificationsByClaim(context, document).values()];
  const firstText = (...values: unknown[]) => {
    for (const value of values) {
      const normalized = invalidityText(value).trim();
      if (normalized) return normalized;
    }
    return '';
  };
  const firstDate = (...values: unknown[]) => firstText(...values).slice(0, 10);
  let publicationNumber = firstText(
    identifiers.publication_number,
    identifiers.publicationNumber,
    identifiers.pn,
    document.publication_number,
  );
  const canonicalKey = invalidityText(document.canonical_key).trim();
  if (!publicationNumber) {
    const canonicalCandidate = canonicalKey.includes(':')
      ? canonicalKey.slice(canonicalKey.lastIndexOf(':') + 1)
      : canonicalKey;
    if (/^[A-Z]{2}[A-Z0-9./-]+$/i.test(canonicalCandidate)) {
      publicationNumber = canonicalCandidate;
    }
  }
  let authority = firstText(
    identifiers.authority,
    identifiers.country,
    document.authority,
  );
  if (!authority && publicationNumber) {
    authority = publicationNumber.match(/^[A-Za-z]{2}/)?.[0]?.toUpperCase() || '';
  }
  const currentMimeType = invalidityText(document.current_mime_type).trim();
  const isPatentPdf = invalidityText(document.document_type).trim() === 'patent'
    && currentMimeType === 'application/pdf';
  const currentVersion = Number(document.current_version_no || 0);
  const currentSha = invalidityText(document.current_content_sha256 || document.content_sha256).trim();
  const versionLocator = [
    currentVersion > 0 ? `文献版本 v${currentVersion}` : '',
    currentSha ? `SHA-256 ${currentSha}` : '',
  ].filter(Boolean).join('，');
  const pendingQualifications = qualifications.filter(
    (qualification) => invalidityText(qualification.verification_status).trim() === 'needs_human_review',
  );
  const verificationReasons = [...new Set(
    pendingQualifications
      .map((qualification) => invalidityText(qualification.verification_reason).trim())
      .filter(Boolean),
  )];

  return {
    publicAvailabilityDate: firstDate(
      ...qualifications.map((qualification) => qualification.public_availability_date),
      dates.public_availability_date,
      ...qualifications.map((qualification) => qualification.publication_date),
      dates.publication_date,
    ),
    publicationDate: firstDate(
      ...qualifications.map((qualification) => qualification.publication_date),
      dates.publication_date,
    ),
    filingDate: firstDate(
      ...qualifications.map((qualification) => qualification.filing_date),
      dates.filing_date,
      dates.application_date,
    ),
    priorityDate: firstDate(
      ...qualifications.map((qualification) => qualification.earliest_priority_date),
      dates.priority_date,
      dates.earliest_priority_date,
    ),
    publicationNumber,
    authority,
    sourceType: invalidityText(document.document_type).trim() || 'other',
    dateEvidenceSource: isPatentPdf
      ? '当前冻结专利 PDF（系统已提取著录信息，待律师核对）'
      : '当前冻结候选材料（系统已提取日期信息，待律师核对）',
    dateEvidenceLocator: isPatentPdf
      ? ['第 1 页著录项目（Pub. Date / Filing Date / (43) / (45) / (22)）', versionLocator]
        .filter(Boolean).join('；')
      : versionLocator,
    reason: '系统已回填检测到的文献身份和日期；本次仅核对这些事实与冻结原件是否一致',
    pendingClaimIds: (pendingQualifications.length ? pendingQualifications : qualifications)
      .map((qualification) => invalidityText(qualification.claim_investigation_id).trim())
      .filter(Boolean),
    verificationReasons,
  };
}

export function assertHumanReviewCommandResponse(
  value: unknown,
): asserts value is HumanReviewCommandResponse {
  if (!isJsonObject(value) || value.contract_version !== INVALIDITY_REPORT_CONTRACT_VERSION) {
    throw new Error('人工复核响应契约无效');
  }
  for (const [name, version] of [
    ['review_revision', value.review_revision],
    ['investigation_state_version', value.investigation_state_version],
  ] as const) {
    if (!Number.isInteger(version) || Number(version) < 0) {
      throw new Error(`人工复核响应的 ${name} 无效`);
    }
  }
  if (
    !invalidityText(value.review_action_id).trim()
    || !invalidityText(value.action_type).trim()
    || value.status !== 'queued'
    || !isJsonObject(value.claim_state_versions)
    || !Array.isArray(value.invalidated_derivations)
    || !Array.isArray(value.pending_recomputation)
    || typeof value.idempotent_replay !== 'boolean'
  ) {
    throw new Error('人工复核响应缺少 action、版本、失效范围或幂等标记');
  }
}

export function humanReviewUploadReceiptMatches(
  response: unknown,
  expected: { sha256: string; byteSize: number; mimeType: string },
): boolean {
  if (!isJsonObject(response) || !isJsonObject(response.upload_receipt)) return false;
  const receipt = response.upload_receipt;
  return Boolean(
    receipt.sha256 === expected.sha256
    && receipt.byte_size === expected.byteSize
    && receipt.mime_type === expected.mimeType,
  );
}

/**
 * Review context normally contains only the current frontier. Keep this
 * defensive reduction so a future backend response with lineage history
 * cannot make the Portal display an old closed/open version as current.
 */
export function latestInvalidityGapsByKey(gaps: JsonObject[]): JsonObject[] {
  const latest = new Map<string, JsonObject>();
  gaps.forEach((gap) => {
    const claimId = invalidityText(gap.claim_investigation_id).trim();
    const gapKey = invalidityText(gap.gap_key).trim();
    if (!claimId || !gapKey) return;
    const key = `${claimId}\u0000${gapKey}`;
    const existing = latest.get(key);
    const version = Number(gap.version_no || 0);
    const existingVersion = Number(existing?.version_no || 0);
    if (!existing || version >= existingVersion) latest.set(key, gap);
  });
  return [...latest.values()];
}

export function mergeCriticalDateConfirmation(
  payload: JsonObject,
  response: CriticalDateConfirmationResponse,
): JsonObject {
  assertCriticalDateConfirmationResponse(response);
  const investigation = isJsonObject(payload.investigation) ? payload.investigation : {};
  const claims = reportRows(payload.claim_investigations);
  return {
    ...payload,
    investigation: {
      ...investigation,
      status: response.investigation_status,
      state_version: response.investigation_state_version,
    },
    claim_investigations: claims.map((claim) => (
      invalidityText(claim.id) === response.claim_investigation_id
        ? {
            ...claim,
            critical_date: response.confirmed_date,
            target_publication_date: response.target_publication_date,
            critical_date_basis: response.critical_date_basis,
            state_version: response.claim_state_version,
          }
        : claim
    )),
  };
}

export function invalidityAutomaticRounds(value: unknown): 1 | 2 | 3 | 4 | 5 {
  const rounds = value === undefined ? 5 : Number(value);
  if (!Number.isInteger(rounds) || rounds < 1 || rounds > 5) {
    throw new Error('maxRounds 表示首轮后的区别特征检索次数，必须是 1 到 5 的整数');
  }
  return rounds as 1 | 2 | 3 | 4 | 5;
}

export function claimDisplayLabel(claim: JsonObject | undefined, fallback?: unknown): string {
  const raw = invalidityText(claim?.claim_id || fallback).trim();
  if (!raw) return '权利要求（未知）';
  if (/^(?:权利要求|claim)\s*/i.test(raw)) return raw;
  return `权利要求 ${raw}`;
}

const INVALIDITY_POSITIVE_DISCLOSURE_STATUSES = new Set([
  'disclosed',
  'explicit',
  'direct_and_unambiguous',
  'necessarily_implicit',
]);

export function isInvalidityPositiveDisclosureStatus(status: unknown): boolean {
  return INVALIDITY_POSITIVE_DISCLOSURE_STATUSES.has(invalidityText(status).trim());
}

function latestQualificationsByClaimAndDocument(
  qualifications: JsonObject[],
): Map<string, JsonObject> {
  const latest = new Map<string, JsonObject>();
  qualifications.forEach((qualification) => {
    const claimId = invalidityText(qualification.claim_investigation_id);
    const documentId = invalidityText(qualification.document_id);
    if (!claimId || !documentId || qualification.limitation_id) return;
    const key = `${claimId}\u0000${documentId}`;
    const existing = latest.get(key);
    const version = Number(qualification.assessment_version || 0);
    const existingVersion = Number(existing?.assessment_version || 0);
    if (!existing || version >= existingVersion) latest.set(key, qualification);
  });
  return latest;
}

/**
 * Return only evidence rows that can form a novelty CC: one date-qualified
 * document must positively disclose every limitation of a novelty-complete
 * claim.  Rows from different documents are never unioned.
 */
export function invalidityNoveltyCcDisclosures(input: {
  claims: JsonObject[];
  limitations: JsonObject[];
  disclosures: JsonObject[];
  qualifications: JsonObject[];
}): JsonObject[] {
  const noveltyClaims = new Set(
    input.claims
      .filter((claim) => invalidityText(claim.status) === 'novelty_evidence_complete')
      .map((claim) => invalidityText(claim.id))
      .filter(Boolean),
  );
  const requiredLimitations = new Map<string, Set<string>>();
  input.limitations.forEach((limitation) => {
    const claimId = invalidityText(limitation.claim_investigation_id);
    const limitationId = invalidityText(limitation.id);
    if (!noveltyClaims.has(claimId) || !limitationId) return;
    const ids = requiredLimitations.get(claimId) || new Set<string>();
    ids.add(limitationId);
    requiredLimitations.set(claimId, ids);
  });

  const latestDisclosure = new Map<string, JsonObject>();
  input.disclosures.forEach((disclosure) => {
    const claimId = invalidityText(disclosure.claim_investigation_id);
    const limitationId = invalidityText(disclosure.limitation_id);
    const documentId = invalidityText(disclosure.document_id);
    if (!noveltyClaims.has(claimId) || !limitationId || !documentId) return;
    const key = `${claimId}\u0000${documentId}\u0000${limitationId}`;
    const existing = latestDisclosure.get(key);
    const timestamp = Date.parse(invalidityText(disclosure.updated_at || disclosure.created_at)) || 0;
    const existingTimestamp = existing
      ? Date.parse(invalidityText(existing.updated_at || existing.created_at)) || 0
      : -1;
    if (!existing || timestamp >= existingTimestamp) latestDisclosure.set(key, disclosure);
  });

  const latestQualifications = latestQualificationsByClaimAndDocument(input.qualifications);
  const disclosedLimitations = new Map<string, Set<string>>();
  latestDisclosure.forEach((disclosure) => {
    if (!isInvalidityPositiveDisclosureStatus(disclosure.disclosure_status)) return;
    const claimId = invalidityText(disclosure.claim_investigation_id);
    const documentId = invalidityText(disclosure.document_id);
    const limitationId = invalidityText(disclosure.limitation_id);
    const key = `${claimId}\u0000${documentId}`;
    const ids = disclosedLimitations.get(key) || new Set<string>();
    ids.add(limitationId);
    disclosedLimitations.set(key, ids);
  });

  const qualifyingClaimDocuments = new Set<string>();
  disclosedLimitations.forEach((covered, key) => {
    const [claimId] = key.split('\u0000');
    const required = requiredLimitations.get(claimId);
    const qualification = latestQualifications.get(key);
    if (
      required?.size
      && [...required].every((limitationId) => covered.has(limitationId))
      && qualification?.novelty_eligible === true
      && invalidityText(qualification.verification_status) === 'verified'
    ) {
      qualifyingClaimDocuments.add(key);
    }
  });

  return [...latestDisclosure.values()].filter((disclosure) => {
    if (!isInvalidityPositiveDisclosureStatus(disclosure.disclosure_status)) return false;
    const key = `${invalidityText(disclosure.claim_investigation_id)}\u0000${invalidityText(disclosure.document_id)}`;
    return qualifyingClaimDocuments.has(key);
  });
}

export function assertInvalidityReportDataV1(report: unknown): asserts report is InvalidityReportDataV1 {
  if (!isJsonObject(report)) {
    throw new Error('无效检索报告不是有效 JSON 对象');
  }
  if (report.contract_version !== INVALIDITY_REPORT_CONTRACT_VERSION) {
    throw new Error(`不支持的无效检索报告契约版本：${invalidityText(report.contract_version) || '缺失'}`);
  }
  if (report.report_kind !== INVALIDITY_REPORT_KIND) {
    throw new Error(`无效检索报告类型不匹配：${invalidityText(report.report_kind) || '缺失'}`);
  }
  if (!isJsonObject(report.investigation)) {
    throw new Error('无效检索报告缺少 investigation 快照');
  }
  for (const [name, version] of [
    ['source_state_version', report.source_state_version],
    ['source_review_revision', report.source_review_revision],
  ] as const) {
    if (!Number.isInteger(version) || Number(version) < 0) {
      throw new Error(`无效检索报告的 ${name} 无效`);
    }
  }
  for (const [sourceName, sourceVersion, currentName, currentVersion] of [
    [
      'source_state_version',
      report.source_state_version,
      'investigation.state_version',
      report.investigation.state_version,
    ],
    [
      'source_review_revision',
      report.source_review_revision,
      'investigation.review_revision',
      report.investigation.review_revision,
    ],
  ] as const) {
    if (!Number.isInteger(currentVersion) || Number(currentVersion) < 0) {
      throw new Error(`无效检索报告的 ${currentName} 无效`);
    }
    if (Number(sourceVersion) !== Number(currentVersion)) {
      throw new Error(`无效检索报告的 ${sourceName} 与 ${currentName} 不一致`);
    }
  }
  const expectedHashScope = report.is_preview
    ? 'preview_not_hashed'
    : 'canonical_persisted_report_data_v1_excluding_snapshot_sha256';
  if (report.snapshot_hash_scope !== expectedHashScope) {
    throw new Error('无效检索报告的快照哈希范围与 preview 状态不一致');
  }
  if (
    !report.is_preview
    && !/^[a-f0-9]{64}$/i.test(invalidityText(report.snapshot_sha256))
  ) {
    throw new Error('终态无效检索报告缺少有效的 SHA-256 快照摘要');
  }
}

export function reportSnapshotDate(report: InvalidityReportDataV1): Date {
  const investigation = report.investigation;
  const snapshot = isJsonObject(report.report_snapshot) ? report.report_snapshot : {};
  const candidates = [
    report.generated_at,
    snapshot.generated_at,
    snapshot.created_at,
    investigation.completed_at,
    investigation.updated_at,
    investigation.created_at,
  ];
  for (const candidate of candidates) {
    const text = invalidityText(candidate).trim();
    if (!text) continue;
    const parsed = new Date(text);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  throw new Error('无效检索报告缺少可核验的后端快照生成时间');
}

export function exactLoopbackServiceUrl(rawValue: unknown, variableName: string, expectedPort: number): string {
  const raw = invalidityText(rawValue).trim();
  if (!raw) throw new Error(`缺少 ${variableName}，禁止跨环境回落`);
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(`${variableName} 不是有效 URL`);
  }
  if (
    parsed.protocol !== 'http:'
    || parsed.hostname !== '127.0.0.1'
    || Number(parsed.port) !== expectedPort
    || !['', '/'].includes(parsed.pathname)
    || parsed.username !== ''
    || parsed.password !== ''
    || parsed.search !== ''
    || parsed.hash !== ''
  ) {
    throw new Error(`${variableName} 必须严格指向 http://127.0.0.1:${expectedPort}`);
  }
  return `http://127.0.0.1:${expectedPort}`;
}

export function invalidityUploadFileKey(
  value: unknown,
  environment: InvalidityEnvironment,
): string {
  const key = invalidityText(value).trim();
  if (!key || key === '.' || key === '..' || key.includes('/') || key.includes('\\') || key.includes('\0')) {
    throw new Error('上传 fileKey 无效');
  }
  if (!key.startsWith(`invalidity-${environment}-`)) {
    throw new Error(`上传 fileKey 不属于 ${environment} 环境`);
  }
  return key;
}

export function redactInvalidityPrivatePaths(input: unknown, key = ''): unknown {
  if (Array.isArray(input)) return input.map((item) => redactInvalidityPrivatePaths(item, key));
  if (isJsonObject(input)) {
    return Object.fromEntries(
      Object.entries(input).map(([childKey, value]) => [childKey, redactInvalidityPrivatePaths(value, childKey)]),
    );
  }
  if (typeof input !== 'string') return input;
  const pathLikeKey = /(?:^|_)(?:path|uri)$/i.test(key);
  const absolutePath = input.startsWith('/') || /^[a-zA-Z]:[\\/]/.test(input) || /^file:\/\//i.test(input);
  return pathLikeKey && absolutePath ? '[REDACTED SERVER PATH]' : input;
}

const FIXTURE_KEYS = new Set(['fixture', 'fixture_id', 'fixture_output']);
const MODE_KEYS = new Set(['input_mode', 'requested_mode', 'effective_mode', 'provider_mode']);

export function liveLabPayloadViolation(input: unknown, path = 'input'): string | null {
  if (Array.isArray(input)) {
    for (let index = 0; index < input.length; index += 1) {
      const violation = liveLabPayloadViolation(input[index], `${path}[${index}]`);
      if (violation) return violation;
    }
    return null;
  }
  if (!isJsonObject(input)) return null;
  for (const [key, value] of Object.entries(input)) {
    const nextPath = `${path}.${key}`;
    if (FIXTURE_KEYS.has(key)) {
      return `${nextPath} 是 fixture 标记`;
    }
    if (key === 'simulated' && (value === true || invalidityText(value).toLowerCase() === 'true')) return `${nextPath}=true`;
    if (MODE_KEYS.has(key) && ['fixture', 'manual'].includes(invalidityText(value).toLowerCase())) {
      return `${nextPath}=${invalidityText(value)}`;
    }
    if (
      ['provider', 'model', 'model_version'].includes(key)
      && /(?:^|[-_])(?:fixture|manual)(?:$|[-_])/i.test(invalidityText(value))
    ) {
      return `${nextPath}=${invalidityText(value)}`;
    }
    const nested = liveLabPayloadViolation(value, nextPath);
    if (nested) return nested;
  }
  return null;
}
