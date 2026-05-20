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
