import { PATENT_AGENT_TOOLSET_VERSION } from './patent-agent';

type JsonObject = Record<string, unknown>;

export const AGENT_GAP_CONFIRMATION_SECONDS = 30;
export const AGENT_MAX_GAP_SEARCH_ROUNDS = 5;

export type AgentInvalidityAction =
  | {
      kind: 'gap_continuation';
      key: string;
      investigationStateVersion: number;
      claimInvestigationIds: string[];
      nextGapRound: number;
      deadlineAt: string;
      detail: string;
    }
  | {
      kind: 'human_review';
      key: string;
      detail: string;
    };

function objectValue(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

function textValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function integerValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) ? parsed : 0;
}

function timestampValue(value: unknown): number | null {
  const parsed = Date.parse(textValue(value));
  return Number.isFinite(parsed) ? parsed : null;
}

export function deriveAgentInvalidityAction(
  value: unknown,
  toolVersion: string,
): AgentInvalidityAction | null {
  const payload = objectValue(value);
  const investigation = objectValue(payload?.investigation);
  if (!investigation) return null;
  const investigationStatus = textValue(investigation.status).toLowerCase();
  const claims = Array.isArray(payload?.claim_investigations)
    ? payload.claim_investigations
      .map(objectValue)
      .filter((claim): claim is JsonObject => claim !== null && claim.in_scope !== false)
    : [];
  const reviewClaims = claims.filter((claim) => (
    textValue(claim.status).toLowerCase() === 'needs_human_review'
  ));
  if (investigationStatus === 'needs_human_review' || reviewClaims.length > 0) {
    const reason = reviewClaims
      .map((claim) => textValue(claim.terminal_reason))
      .find(Boolean);
    const stateVersion = integerValue(investigation.state_version);
    return {
      kind: 'human_review',
      key: `human-review:${textValue(investigation.id)}:${stateVersion}`,
      detail: reason || '需要你核验关键日、证据资格或其他案件事实后，后台才能继续。',
    };
  }
  if (
    toolVersion !== PATENT_AGENT_TOOLSET_VERSION
    || investigationStatus !== 'partial'
  ) {
    return null;
  }
  const eligibleClaims = claims.filter((claim) => {
    const currentIteration = integerValue(claim.current_iteration_no);
    return textValue(claim.status).toLowerCase() === 'partial'
      && currentIteration >= 1
      && currentIteration <= AGENT_MAX_GAP_SEARCH_ROUNDS;
  });
  if (eligibleClaims.length === 0) return null;
  const stateVersion = integerValue(investigation.state_version);
  const claimIds = eligibleClaims
    .map((claim) => textValue(claim.id))
    .filter(Boolean)
    .sort();
  if (claimIds.length === 0) return null;
  const checkpointTimes = [
    timestampValue(investigation.updated_at),
    ...eligibleClaims.map((claim) => timestampValue(claim.updated_at)),
  ].filter((item): item is number => item !== null);
  if (checkpointTimes.length === 0) return null;
  const checkpointAt = Math.max(...checkpointTimes);
  const deadlineAt = new Date(
    checkpointAt + AGENT_GAP_CONFIRMATION_SECONDS * 1000,
  ).toISOString();
  const nextGapRound = Math.max(
    1,
    Math.min(...eligibleClaims.map((claim) => integerValue(claim.current_iteration_no))),
  );
  return {
    kind: 'gap_continuation',
    key: `gap-continuation:${textValue(investigation.id)}:${stateVersion}:${claimIds.join(',')}`,
    investigationStateVersion: stateVersion,
    claimInvestigationIds: claimIds,
    nextGapRound,
    deadlineAt,
    detail: `下一次将启动第 ${nextGapRound} 个 gap 检索轮；仍受最多 ${AGENT_MAX_GAP_SEARCH_ROUNDS} 轮硬上限约束。`,
  };
}
