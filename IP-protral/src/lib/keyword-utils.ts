export function normalizeKeywordList(input: string[] | string): string[] {
  const segments = Array.isArray(input) ? input : [input];
  const seen = new Set<string>();
  const normalized: string[] = [];

  for (const segment of segments) {
    if (typeof segment !== 'string') {
      continue;
    }

    for (const raw of segment.split(/[\n,，]+/)) {
      const keyword = raw.trim();
      if (!keyword || seen.has(keyword)) {
        continue;
      }

      seen.add(keyword);
      normalized.push(keyword);
    }
  }

  return normalized;
}

export interface ExecutableKeywordRecord {
  keyword_text: string | null;
  keyword_type?: string | null;
  source_location?: string | null;
  raw_payload?: Record<string, unknown> | null;
}

function normalizeForContainment(value: unknown): string {
  return String(value ?? '').replace(/\s+/g, '').toLowerCase();
}

function rawObjectTerms(record: ExecutableKeywordRecord): string[] {
  const value = record.raw_payload?.object_terms;
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

export function extractKeywordObjectTerms(records: ExecutableKeywordRecord[]): string[] {
  return normalizeKeywordList([
    ...records.flatMap(rawObjectTerms),
    ...records
      .filter((record) =>
        String(record.keyword_type ?? '').toUpperCase() === 'OBJECT_BASE'
        || record.source_location === '主客体基础词')
      .map((record) => String(record.keyword_text ?? '')),
  ]);
}

/**
 * Portal 出口兜底：只把含商品客体的关键词交给模块3。
 * 新版模块2会在 raw_payload.object_terms 写入客体；旧数据没有元数据时，
 * 至少剔除历史上明确标记为“必要特征基础词”的裸特征。
 */
export function filterExecutableKeywordRecords(records: ExecutableKeywordRecord[]): string[] {
  const objectTerms = extractKeywordObjectTerms(records);
  const normalizedObjectTerms = objectTerms.map(normalizeForContainment).filter(Boolean);

  const executable = records.filter((record) => {
    const keyword = String(record.keyword_text ?? '').trim();
    if (!keyword || record.source_location === '必要特征基础词') return false;

    const queryRole = String(record.raw_payload?.query_role ?? 'executable_search');
    const guardStatus = String(record.raw_payload?.guard_status ?? 'passed');
    if (queryRole !== 'executable_search' || guardStatus !== 'passed') return false;

    if (normalizedObjectTerms.length === 0) return true;
    const normalizedKeyword = normalizeForContainment(keyword);
    return normalizedObjectTerms.some((term) => normalizedKeyword.includes(term));
  });

  return normalizeKeywordList(executable.map((record) => String(record.keyword_text ?? '')));
}
