export type InvalidityDateClassificationLabel =
  | '现有技术'
  | '抵触申请'
  | '非现有技术'
  | '日期无法确定';

export interface InvalidityDateClassification {
  label: InvalidityDateClassificationLabel;
  reason: string;
}

type EligibilityRecord = Record<string, unknown> | null | undefined;

const UNKNOWN_MARKERS = [
  'unknown',
  'needs_human_review',
  'pending',
  'unverified',
  'not_verified',
  'missing',
  'invalid',
];

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function reasonText(eligibility: EligibilityRecord, fallback: string): string {
  if (!eligibility) return fallback;
  return (
    stringValue(eligibility.explanation)
    || stringValue(eligibility.verification_reason)
    || stringValue(eligibility.reason)
    || fallback
  );
}

function hasUnknownMarker(eligibility: Record<string, unknown>): boolean {
  if (
    eligibility.requires_human_review === true
    || eligibility.needs_human_review === true
  ) {
    return true;
  }
  const values = [
    eligibility.category,
    eligibility.eligibility_type,
    eligibility.verification_status,
    ...(Array.isArray(eligibility.reason_codes)
      ? eligibility.reason_codes
      : []),
  ]
    .map((value) => stringValue(value).toLowerCase())
    .filter(Boolean);
  return values.some((value) =>
    UNKNOWN_MARKERS.some((marker) => value.includes(marker)),
  );
}

export function invalidityDateClassification(
  eligibility: EligibilityRecord,
): InvalidityDateClassification {
  if (!eligibility || Object.keys(eligibility).length === 0) {
    return {
      label: '日期无法确定',
      reason: '尚未完成日期核验。',
    };
  }

  const category = (
    stringValue(eligibility.category)
    || stringValue(eligibility.eligibility_type)
  ).toLowerCase();

  if (hasUnknownMarker(eligibility)) {
    return {
      label: '日期无法确定',
      reason: reasonText(eligibility, '关键日期事实缺失或尚未核验。'),
    };
  }

  if (category === 'ordinary_prior_art') {
    return {
      label: '现有技术',
      reason: reasonText(eligibility, '材料已在本案关键日前公开。'),
    };
  }

  if (
    category === 'conflicting_application_candidate'
    || category === 'conflicting_application'
  ) {
    return {
      label: '抵触申请',
      reason: reasonText(
        eligibility,
        '材料符合抵触申请的日期窗口，仅用于新颖性分析。',
      ),
    };
  }

  if (
    category === 'post_date_lead'
    || category === 'lead_only'
    || category === 'excluded'
    || category === 'non_prior_art'
  ) {
    return {
      label: '非现有技术',
      reason: reasonText(eligibility, '材料不符合本案现有技术的日期条件。'),
    };
  }

  if (
    eligibility.inventive_step_eligible === true
    || eligibility.inventive_eligible === true
  ) {
    return {
      label: '现有技术',
      reason: reasonText(eligibility, '材料可进入新颖性和创造性分析。'),
    };
  }

  if (
    eligibility.novelty_eligible === true
    && eligibility.inventive_step_eligible === false
  ) {
    return {
      label: '抵触申请',
      reason: reasonText(
        eligibility,
        '材料仅符合新颖性分析的日期条件。',
      ),
    };
  }

  if (
    eligibility.novelty_eligible === false
    && (
      eligibility.inventive_step_eligible === false
      || eligibility.inventive_eligible === false
    )
  ) {
    return {
      label: '非现有技术',
      reason: reasonText(eligibility, '材料不符合本案现有技术的日期条件。'),
    };
  }

  return {
    label: '日期无法确定',
    reason: reasonText(eligibility, '现有日期信息不足以完成分类。'),
  };
}
