import {
  INVALIDITY_REPORT_CONTRACT_VERSION,
  INVALIDITY_REPORT_KIND,
  invalidityText,
  isJsonObject,
  reportRows,
  type JsonObject,
} from '@/lib/invalidity-contracts';

export type AgentInvalidityNarrative = {
  key: string;
  claimLabel: string;
  conclusion: string;
  evidenceComplete: boolean;
  paragraphs: string[];
};

export type AgentInvalidityMatrixDocument = {
  id: string;
  rank: number;
  label: string;
  confirmedDisclosedFeatureCount: number;
  totalFeatureCount: number;
  isCurrentD1: boolean;
};

export type AgentInvalidityMatrixCell = {
  documentId: string;
  status: string;
  label: string;
};

export type AgentInvalidityMatrixFeature = {
  id: string;
  featureKey: string;
  limitationText: string;
  cells: AgentInvalidityMatrixCell[];
};

export type AgentInvalidityMatrix = {
  key: string;
  claimLabel: string;
  rankingBasis: string;
  documents: AgentInvalidityMatrixDocument[];
  features: AgentInvalidityMatrixFeature[];
};

export type AgentInvalidityModule11Report = {
  snapshotSha256: string;
  narratives: AgentInvalidityNarrative[];
  matrices: AgentInvalidityMatrix[];
  analyzedDocumentCount: number;
};

function record(value: unknown): JsonObject {
  return isJsonObject(value) ? value : {};
}

function text(value: unknown): string {
  return invalidityText(value).trim();
}

function number(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function stringValues(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map(text).filter(Boolean)
    : text(value) ? [text(value)] : [];
}

function reportFromPayload(payload: unknown): JsonObject {
  const root = record(payload);
  const nested = record(root.report);
  return Object.keys(nested).length ? nested : root;
}

function documentLabel(document: JsonObject, fallback: string): string {
  const identifiers = record(document.identifiers);
  const publication = text(document.publication_number)
    || text(identifiers.publication_number)
    || text(document.canonical_key)
    || fallback;
  const title = text(document.title);
  return title ? `${publication}《${title}》` : publication;
}

export function agentInvalidityMatrixStatusLabel(value: unknown): string {
  const status = text(value);
  if (['disclosed', 'explicit', 'direct_and_unambiguous', 'structural_equivalent'].includes(status)) return '有（明确）';
  if (status === 'necessarily_implicit') return '有（必然隐含）';
  if (status === 'not_disclosed') return '未披露';
  if (status === 'analysis_failed') return '分析失败';
  if (['uncertain', 'insufficient_evidence', 'material_incomplete'].includes(status)) return '待确认';
  return '未完成分析';
}

export function buildAgentInvalidityModule11Report(
  payload: unknown,
): AgentInvalidityModule11Report | null {
  const report = reportFromPayload(payload);
  if (
    report.contract_version !== INVALIDITY_REPORT_CONTRACT_VERSION
    || report.report_kind !== INVALIDITY_REPORT_KIND
  ) return null;

  const narratives = reportRows(report.inventive_step_narratives).map((item, index) => {
    const claimId = text(item.claim_id) || String(index + 1);
    return {
      key: text(item.combination_id) || text(item.claim_investigation_id) || `narrative-${claimId}`,
      claimLabel: `独立权利要求 ${claimId}`,
      conclusion: text(item.conclusion_text)
        || (item.evidence_complete === true ? '证据链已闭合（供律师复核）' : '证据仍有缺口'),
      evidenceComplete: item.evidence_complete === true,
      paragraphs: stringValues(item.paragraphs),
    } satisfies AgentInvalidityNarrative;
  });

  const matrices = reportRows(report.similarity_claim_charts).map((chart, chartIndex) => {
    const claimId = text(chart.claim_id) || String(chartIndex + 1);
    const documents = reportRows(chart.ranked_documents).map((ranked, documentIndex) => {
      const id = text(ranked.document_id) || `document-${documentIndex + 1}`;
      const document = record(ranked.document);
      return {
        id,
        rank: number(ranked.rank) || documentIndex + 1,
        label: documentLabel(document, id),
        confirmedDisclosedFeatureCount: number(ranked.confirmed_disclosed_feature_count),
        totalFeatureCount: number(ranked.total_feature_count),
        isCurrentD1: ranked.is_current_d1 === true,
      } satisfies AgentInvalidityMatrixDocument;
    });
    const documentIds = documents.map((item) => item.id);
    const features = reportRows(chart.feature_rows).map((feature, featureIndex) => {
      const cellsByDocument = new Map(
        reportRows(feature.cells).map((cell) => [text(cell.document_id), cell]),
      );
      const id = text(feature.limitation_id) || text(feature.feature_key) || `feature-${featureIndex + 1}`;
      return {
        id,
        featureKey: text(feature.feature_key) || `特征 ${featureIndex + 1}`,
        limitationText: text(feature.limitation_text),
        cells: documentIds.map((documentId) => {
          const cell = cellsByDocument.get(documentId) || {};
          const status = text(cell.disclosure_status) || 'not_analysed';
          return {
            documentId,
            status,
            label: agentInvalidityMatrixStatusLabel(status),
          } satisfies AgentInvalidityMatrixCell;
        }),
      } satisfies AgentInvalidityMatrixFeature;
    });
    return {
      key: text(chart.claim_investigation_id) || `matrix-${claimId}`,
      claimLabel: `独立权利要求 ${claimId}`,
      rankingBasis: text(chart.ranking_basis),
      documents,
      features,
    } satisfies AgentInvalidityMatrix;
  });

  const analyzedDocumentIds = new Set(
    reportRows(report.feature_disclosures || report.disclosures)
      .map((item) => text(item.document_id))
      .filter(Boolean),
  );

  if (!narratives.length && !matrices.length && !analyzedDocumentIds.size) return null;
  return {
    snapshotSha256: text(report.snapshot_sha256),
    narratives,
    matrices,
    analyzedDocumentCount: analyzedDocumentIds.size,
  };
}
