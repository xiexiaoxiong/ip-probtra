export type JsonObject = Record<string, unknown>;

export type TargetPatentSummary = {
  patentNumber: string;
  title: string;
  applicationDate: string;
  priorityDate: string;
  publicationDate: string;
  claimId: string;
  claimText: string;
  figureCount: number;
};

export type CriticalDateSelection = {
  date: string;
  basis: 'priority_date' | 'application_date';
  requiresHumanReview: boolean;
};

export type PatsnapQueryStrategy = 'balanced' | 'compact-fallback';
export type PatsnapQueryRole =
  | 'inventive_point_precision'
  | 'claim_context_recall'
  | 'title_abstract_concept'
  | 'gap_followup';
export type PatsnapQueryVariant =
  | 'maximal_similarity_precision'
  | 'object_plus_inventive_point'
  | 'subject_classification_plus_inventive_point'
  | 'object_plus_inventive_classification'
  | 'object_plus_two_inventive_points'
  | 'object_plus_component_plus_effect'
  | 'system_architecture_recall'
  | 'target_citation_lookup'
  | 'target_effect_recall'
  | 'classification_action_recall'
  | 'title_abstract_concept'
  | 'gap_followup'
  | 'applicant_plus_object'
  | 'title_object_plus_desc_inventive'
  | 'classification_plus_desc_inventive'
  | 'desc_object_plus_desc_inventive_plus_effect'
  | 'title_keyword_object_plus_desc_function';
export type PatsnapSearchScope =
  | 'title_abstract'
  | 'claims'
  | 'full_text'
  | 'classification';

export type PatsnapSearchPlan = {
  queryId: string;
  strategy: PatsnapQueryStrategy;
  expression: string;
  language: string;
  technicalSubject: string;
  subjectTerms: string[];
  featureTerms: string[];
  featureQueryGroups: string[][];
  queryRole: PatsnapQueryRole;
  queryVariant: PatsnapQueryVariant;
  searchScope: PatsnapSearchScope;
  scopeReason: string;
  classificationAnchors: string[];
  applicantTerms: string[];
  excludedPublication: string;
  allowZeroResults: boolean;
  compactFallbackAllowed: boolean;
  model: string;
  usedTargetImages: number;
  input: JsonObject;
};

export type PatsnapCandidate = {
  externalId: string;
  publicationNumber: string;
  title: string;
  authority: string;
  publicationDate: string;
  filingDate: string;
  assignee: string;
  stage: string;
  totalResultCount: number | null;
};

export type PatsnapSearchResult = {
  actualProvider: string;
  networkUsed: boolean;
  returnedCount: number;
  totalResultCount: number | null;
  candidates: PatsnapCandidate[];
  artifacts: Array<{
    kind: string;
    sha256: string;
    byteSize: number | null;
  }>;
};

const TERMINAL_RUN_STATUSES = new Set([
  'succeeded',
  'partial',
  'failed',
  'cancelled',
]);

const SUCCESS_RUN_STATUSES = new Set(['succeeded', 'partial']);

export function isJsonObject(value: unknown): value is JsonObject {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function asObject(value: unknown): JsonObject {
  return isJsonObject(value) ? value : {};
}

function asObjects(value: unknown): JsonObject[] {
  return Array.isArray(value) ? value.filter(isJsonObject) : [];
}

function text(value: unknown): string {
  return value == null ? '' : String(value).trim();
}

function integer(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

function isoDate(value: unknown): string {
  const candidate = text(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(candidate)) return '';
  const parsed = new Date(`${candidate}T00:00:00Z`);
  return Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== candidate
    ? ''
    : candidate;
}

function normalized(value: string): string {
  return value
    .normalize('NFKC')
    .toLocaleLowerCase()
    .replace(/[\s"'“”‘’()[\]{}（）【】,，.。:：;；/\\|+\-_]+/g, '');
}

function unique(values: string[]): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const value of values) {
    const item = value.trim();
    const key = normalized(item);
    if (!item || !key || seen.has(key)) continue;
    seen.add(key);
    result.push(item);
  }
  return result;
}

function subjectHeadTerms(value: string): string[] {
  const raw = value.normalize('NFKC').trim();
  const index = raw.lastIndexOf('的');
  if (index < 0) return [];
  const suffix = raw.slice(index + 1).replace(/^(?:一种|一个|该|所述)/u, '').trim();
  const generic = new Set(['装置', '设备', '系统', '方法', '结构', '机构', '组件', '部件']);
  return /^[\u3400-\u9fff]{2,12}$/u.test(suffix) && !generic.has(suffix)
    ? [suffix]
    : [];
}

export function atomicSearchTerms(value: string): string[] {
  const pieces = value
    .normalize('NFKC')
    .split(/[、，,；;]+/)
    .map((item) => item.trim().replace(/^["'“”‘’()[\]{}（）【】]+|["'“”‘’()[\]{}（）【】]+$/g, ''))
    .filter(Boolean);
  const result: string[] = [];
  for (const piece of pieces) {
    if (!piece.includes('不饱和')) {
      const conjunctionParts = piece
        .split(/\s*(?:以及|和|与|及)\s*/u)
        .map((item) => item.trim())
        .filter(Boolean);
      if (conjunctionParts.length > 1) {
        const atoms = conjunctionParts.flatMap((item) => atomicSearchTerms(item));
        // A preceding quantity/unit fragment is intentionally discarded here.
        // If no split side is searchable (for example the lexical word
        // ``中和剂``), keep evaluating the intact phrase below.
        if (atoms.length > 0) {
          result.push(...atoms);
          continue;
        }
      }
    }
    if (
      /(?:装置|机构|组件|模块|结构|部件|构件)$/u.test(piece)
      || /\b(?:device|mechanism|assembly|module|structure|component)$/i.test(piece)
    ) {
      result.push(piece);
      continue;
    }
    const chinesePositions = piece.match(
      /(?:起始|初始|中间|终止|最终|第一|第二|预定|设定|指定|特定|工作|关闭|打开|开启|收缩|伸展)?位置/g,
    ) || [];
    const chineseActions = (
      piece.match(/(?:选择性地?|可选择(?:性)?地?)(?:联接|连接|接合|耦合)/g) || []
    ).map((item) => item.replace(/地/g, ''));
    const englishPositions = piece.match(
      /\b(?:initial|starting|intermediate|terminal|final|first|second|predetermined|selected|specified)\s+position\b/gi,
    ) || [];
    const englishActions = piece.match(
      /\b(?:selective(?:ly)?\s+)(?:coupl(?:e|ing)|engag(?:e|ing)|connect(?:ion|ing)?)\b/gi,
    ) || [];
    const positions = unique([...chinesePositions, ...englishPositions]);
    const actions = unique([...chineseActions, ...englishActions]);
    const atomicKeys = new Set([...positions, ...actions].map(normalized));
    if (positions.length > 0 && actions.length > 0 && !atomicKeys.has(normalized(piece))) {
      result.push(...positions, ...actions);
    } else {
      result.push(piece);
    }
  }
  return unique(result.filter((item) => {
    const key = normalized(item);
    // In a claim-context combination, one-character Chinese component nouns
    // are legitimate technical terms (e.g. 阀/泵/罐).  They are allowed only
    // when I2 has already bound them to a concrete limitation; isolated broad
    // searches remain blocked by the two-of-three and subject guards below.
    return key.length >= 2 || /^[阀泵罐管杆座孔槽轴轮]$/u.test(key);
  }));
}

function positionOnlyTerm(value: string): boolean {
  const matches = value.match(
    /(?:起始|初始|中间|终止|最终|第一|第二|预定|设定|指定|特定|工作|关闭|打开|开启|收缩|伸展)?位置|\b(?:initial|starting|intermediate|terminal|final|first|second|predetermined|selected|specified)\s+position\b/gi,
  ) || [];
  return matches.some((item) => normalized(item) === normalized(value));
}

function containsTerm(expression: string, term: string): boolean {
  const expressionKey = normalized(expression);
  const termKey = normalized(term);
  return Boolean(termKey) && expressionKey.includes(termKey);
}

function mechanismActionFamilies(value: string): Set<string> {
  const key = normalized(value);
  const families = new Set<string>();
  if (!key) return families;
  const hasUnlock = ['解锁', 'unlock', 'unlatch'].some((item) => key.includes(item));
  const hasDisengage = [
    '脱开', '断开', '分离', 'disengag', 'disconnect', 'decoupl',
  ].some((item) => key.includes(item));
  if (
    ['联接', '连接', '接合', '耦合', 'coupl', 'connect'].some(
      (item) => key.includes(item),
    )
    || (key.includes('engag') && !hasDisengage)
  ) families.add('coupling');
  if (hasDisengage) families.add('uncoupling');
  if (hasUnlock) families.add('unlocking');
  if (
    !hasUnlock
    && ['锁定', '锁止', '锁紧', 'locking', 'latch'].some(
      (item) => key.includes(item),
    )
  ) families.add('locking');
  if (
    ['选择性', '可选择', '位置选择', 'selective'].some(
      (item) => key.includes(item),
    )
  ) families.add('selective');
  if (
    ['旋转', '转动', '回转', 'rotate', 'rotat', 'pivot'].some(
      (item) => key.includes(item),
    )
  ) families.add('rotation');
  if (
    ['控制', '调控', 'control', 'regulat'].some((item) => key.includes(item))
  ) families.add('control');
  if (
    ['驱动', '致动', 'drive', 'driving', 'actuat'].some((item) => key.includes(item))
  ) families.add('drive');
  if (
    ['调节', '调整', 'adjust', 'tuning'].some((item) => key.includes(item))
  ) families.add('adjustment');
  return families;
}

function traceableMechanismTerm(candidate: string, sourceTerms: string[]): boolean {
  const candidateKey = normalized(candidate);
  if (!candidateKey) return false;
  const sourceKeys = sourceTerms.map(normalized).filter(Boolean);
  if (sourceKeys.some((sourceKey) => (
    candidateKey.includes(sourceKey) || sourceKey.includes(candidateKey)
  ))) return true;
  const sourceFamilies = new Set(
    sourceTerms.flatMap((item) => [...mechanismActionFamilies(item)]),
  );
  const candidateFamilies = [...mechanismActionFamilies(candidate)];
  return candidateFamilies.length > 0
    && candidateFamilies.every((item) => sourceFamilies.has(item));
}

function sameAtomicSearchConcept(candidate: string, anchor: string): boolean {
  const candidateKey = normalized(candidate);
  const anchorKey = normalized(anchor);
  if (!candidateKey || !anchorKey) return false;
  if (candidateKey.includes(anchorKey) || anchorKey.includes(candidateKey)) return true;
  const componentFamilies: Record<string, string[]> = {
    罐: ['罐', '储液罐', '储液器', '储液容器', 'tank', 'reservoir'],
    阀: ['阀', 'valve'],
    泵: ['泵', 'pump'],
    管: ['管', '导管', '管路', 'tube', 'pipe', 'conduit'],
    杆: ['杆', 'stem', 'rod'],
    座: ['座', 'seat'],
    喷嘴: ['喷嘴', 'nozzle'],
    驱动器: ['驱动器', '执行器', 'drive', 'actuator'],
    触发器: ['触发器', '扳机', 'trigger'],
    连接器: ['连接器', '接头', 'connector'],
    联接器: ['联接器', '接头', 'coupler', 'coupling'],
    活塞: ['活塞', 'piston'],
    弹簧: ['弹簧', 'spring'],
  };
  const componentFamily = (key: string): string => Object.entries(componentFamilies)
    .find(([, values]) => values.some((item) => normalized(item) === key))?.[0] || '';
  const candidateComponent = componentFamily(candidateKey);
  const anchorComponent = componentFamily(anchorKey);
  if (candidateComponent || anchorComponent) {
    return Boolean(candidateComponent) && candidateComponent === anchorComponent;
  }
  const candidateFamilies = [...mechanismActionFamilies(candidate)];
  const anchorFamilies = [...mechanismActionFamilies(anchor)];
  if (anchorFamilies.length > 0) {
    return candidateFamilies.length > 0
      && candidateFamilies.every((item) => anchorFamilies.includes(item));
  }
  if (candidateFamilies.length > 0) return false;
  return positionOnlyTerm(candidate) === positionOnlyTerm(anchor);
}

function expressionAtoms(expression: string): string[] {
  return unique(
    (expression.match(/[\u3400-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9._-]+/g) || [])
      .flatMap(atomicSearchTerms),
  ).filter((item) => {
    const key = normalized(item);
    return key.length >= 2 && !new Set([
      'and', 'or', 'not', 'the', 'of', 'for', 'with',
      '用于', '包括', '具有', '两个', '一侧', '外侧',
    ]).has(key);
  });
}

type FeatureAnchor = {
  limitation: JsonObject;
  term: string;
};

function inferDistinctFeatureAnchors(
  expression: string,
  limitations: JsonObject[],
  subjectCandidates: string[],
  suppliedFeatureTerms: string[] = [],
): FeatureAnchor[] {
  const atoms = expressionAtoms(expression);
  const subjectKeys = subjectCandidates.map(normalized).filter(Boolean);
  const used = new Set<string>();
  const result: FeatureAnchor[] = [];

  for (const [index, limitation] of limitations.entries()) {
    const suppliedTermKey = normalized(suppliedFeatureTerms[index] || '');
    const aliases = unique([
      ...atomicSearchTerms(suppliedFeatureTerms[index] || ''),
      ...limitationTerms(limitation),
    ].filter(Boolean));
    const aliasKeys = aliases.map(normalized).filter(Boolean);
    const candidates = unique([
      ...aliases.filter((item) => containsTerm(expression, item)),
      ...atoms.filter((atom) => {
        const atomKey = normalized(atom);
        return aliasKeys.some((aliasKey) => aliasKey.includes(atomKey));
      }),
    ]).filter((item) => {
      const key = normalized(item);
      if (!key || used.has(key)) return false;
      // feature_ids already bind this candidate to a concrete limitation.
      // A legitimate component such as “头戴” may also occur inside the
      // product subject “开放式头戴耳机”; only the complete subject itself
      // is not an independent feature anchor.
      return !subjectKeys.some((subjectKey) => key === subjectKey);
    });
    const selected = candidates.sort((left, right) => {
      const leftIsSupplied = Boolean(suppliedTermKey)
        && normalized(left) === suppliedTermKey;
      const rightIsSupplied = Boolean(suppliedTermKey)
        && normalized(right) === suppliedTermKey;
      if (leftIsSupplied !== rightIsSupplied) return leftIsSupplied ? -1 : 1;
      const leftIsAtom = atoms.some((atom) => normalized(atom) === normalized(left));
      const rightIsAtom = atoms.some((atom) => normalized(atom) === normalized(right));
      if (leftIsAtom !== rightIsAtom) return leftIsAtom ? -1 : 1;
      const positionDifference = Number(positionOnlyTerm(left)) - Number(positionOnlyTerm(right));
      if (positionDifference !== 0) return positionDifference;
      return normalized(right).length - normalized(left).length;
    })[0];
    if (!selected) continue;
    used.add(normalized(selected));
    result.push({ limitation, term: selected });
  }
  return result;
}

type ProviderTermLanguage = 'zh' | 'en' | 'mixed' | 'unknown';

const COMPACT_ENGLISH_STOP_WORDS = new Set([
  'a',
  'an',
  'and',
  'are',
  'as',
  'at',
  'be',
  'being',
  'by',
  'comprise',
  'comprises',
  'comprising',
  'configured',
  'for',
  'from',
  'has',
  'have',
  'having',
  'include',
  'includes',
  'including',
  'in',
  'is',
  'of',
  'on',
  'or',
  'the',
  'to',
  'wherein',
  'with',
]);

const GENERIC_ENGLISH_TECHNICAL_TERMS = new Set([
  'apparatus',
  'assemblies',
  'assembly',
  'component',
  'components',
  'device',
  'devices',
  'mechanism',
  'mechanisms',
  'module',
  'modules',
  'part',
  'parts',
  'structure',
  'structures',
  'system',
  'systems',
  'unit',
  'units',
]);

const CHINESE_TECHNICAL_ENDINGS = [
  '控制器',
  '处理器',
  '传感器',
  '连接器',
  '单元',
  '组件',
  '模块',
  '机构',
  '装置',
  '结构',
  '部件',
  '构件',
  '头戴',
  '通道',
  '接口',
  '腔体',
  '孔',
  '腔',
  '槽',
  '口',
  '层',
  '板',
  '杆',
  '轴',
  '轮',
];

function providerTerm(value: string): string {
  const normalizedHyphens = value
    .normalize('NFKC')
    .replace(/([A-Za-z0-9])\s*[-‐‑‒–—]\s*(?=[A-Za-z0-9])/g, '$1-');
  const atoms = normalizedHyphens.match(
    /[\u3400-\u9fff]+|[A-Za-z0-9][A-Za-z0-9._-]*/g,
  ) || [];
  const cleaned = atoms.filter((item) => !new Set([
    'or', 'not', 'tacd', 'tac', 'ttl', 'abst', 'clms', 'desc',
  ]).has(item.toLocaleLowerCase()));
  const candidate = cleaned.slice(0, 10).join(' ').trim();
  return normalized(candidate).length <= 80 ? candidate : '';
}

function providerTermLanguage(value: string): ProviderTermLanguage {
  const hasChinese = /[\u3400-\u9fff]/.test(value);
  const hasEnglish = /[A-Za-z]/.test(value);
  if (hasChinese && hasEnglish) return 'mixed';
  if (hasChinese) return 'zh';
  if (hasEnglish) return 'en';
  return 'unknown';
}

function compactEnglishVariants(value: string): string[] {
  const tokens = value
    .toLocaleLowerCase()
    .match(/[a-z0-9]+(?:-[a-z0-9]+)*/g) || [];
  const meaningful = tokens.filter((token) => !COMPACT_ENGLISH_STOP_WORDS.has(token));
  if (meaningful.length < 1) return [];

  const candidates: string[] = [];
  const distinctivePool = meaningful.filter(
    (token) => !GENERIC_ENGLISH_TECHNICAL_TERMS.has(token),
  );
  const distinctive = [...(distinctivePool.length > 0 ? distinctivePool : meaningful)].sort(
    (left, right) => right.length - left.length,
  )[0];
  if (meaningful.length === 1) {
    candidates.push(meaningful[0]);
  } else if (meaningful.length <= 4) {
    candidates.push(meaningful.join(' '));
    candidates.push(meaningful.slice(-2).join(' '));
    candidates.push(`${meaningful[0]} ${meaningful[meaningful.length - 1]}`);
    candidates.push(meaningful.slice(0, 2).join(' '));
  } else {
    candidates.push(unique([
      ...meaningful.slice(0, 2),
      distinctive,
    ]).join(' '));
    candidates.push(`${meaningful[0]} ${distinctive}`);
  }
  if (meaningful.length >= 3) {
    candidates.push(meaningful.slice(0, 3).join(' '));
    candidates.push(meaningful.slice(-3).join(' '));
  }
  candidates.push(distinctive);
  return unique(candidates.map(providerTerm).filter(Boolean)).filter((candidate) => {
    const candidateTokens: string[] = candidate.toLocaleLowerCase().match(
      /[a-z0-9]+(?:-[a-z0-9]+)*/g,
    ) ?? [];
    if (!candidateTokens.includes(distinctive)) return false;
    return !(
      candidateTokens.length === 1
      && GENERIC_ENGLISH_TECHNICAL_TERMS.has(candidateTokens[0])
    );
  });
}

function shortestTerms(values: string[]): string[] {
  return [...values].sort(
    (left, right) => normalized(left).length - normalized(right).length,
  );
}

function termBigrams(value: string): Set<string> {
  const key = normalized(value);
  const result = new Set<string>();
  for (let index = 0; index < key.length - 1; index += 1) {
    result.add(key.slice(index, index + 2));
  }
  return result;
}

function rankTermsByAnchor(values: string[], anchor: string): string[] {
  const anchorBigrams = termBigrams(anchor);
  return [...values].sort((left, right) => {
    const score = (value: string) => {
      const terms = termBigrams(value);
      return [...anchorBigrams].filter((item) => terms.has(item)).length;
    };
    const overlapDifference = score(right) - score(left);
    if (overlapDifference !== 0) return overlapDifference;
    return Math.abs(normalized(left).length - normalized(anchor).length)
      - Math.abs(normalized(right).length - normalized(anchor).length);
  });
}

function chineseStructuralVariants(values: string[], anchor: string): string[] {
  const candidates: string[] = [];
  for (const value of values) {
    if (providerTermLanguage(value) !== 'zh') continue;
    const cleaned = value
      .replace(/^(?:所述|包括|包含|具有|设有|设置有|安装有|其中)+/g, '')
      .replace(/(?:[一二三四五六七八九十两]+|\d+)个/g, '');
    const segments = cleaned
      .split(/[、，,；;和及与并或]/)
      .map((item) => item.trim())
      .filter((item) => item.length >= 2);
    for (const segment of segments) {
      const fragments: string[] = [];
      const isAnchor = normalized(segment) === normalized(anchor);
      let hasTechnicalEnding = false;
      for (const ending of CHINESE_TECHNICAL_ENDINGS) {
        if (!segment.endsWith(ending)) continue;
        hasTechnicalEnding = true;
        const prefixLength = ending.length <= 2 ? 2 : 1;
        fragments.push(segment.slice(-Math.min(segment.length, ending.length + prefixLength)));
      }
      if (isAnchor || hasTechnicalEnding) {
        if (segment.length <= 8) fragments.push(segment);
      }
      for (const fragment of unique(fragments)) {
        if (
          mechanismActionFamilies(fragment).size > 0
          && !/(?:装置|机构|结构|组件|部件|构件)$/.test(fragment)
        ) continue;
        if (/结构$/.test(fragment)) {
          candidates.push(fragment);
        } else if (!/(?:装置|机构)$/.test(fragment)) {
          candidates.push(`${fragment}结构`);
        }
      }
    }
  }
  return shortestTerms(unique(candidates.map(providerTerm).filter(Boolean)));
}

function englishStructuralVariants(values: string[]): string[] {
  const candidates: string[] = [];
  for (const value of values) {
    if (providerTermLanguage(value) !== 'en') continue;
    if (mechanismActionFamilies(value).size > 0) continue;
    const tokens = value
      .toLocaleLowerCase()
      .match(/[a-z0-9]+(?:-[a-z0-9]+)*/g)
      ?.filter((token) => (
        !COMPACT_ENGLISH_STOP_WORDS.has(token)
        && !/^\d+$/.test(token)
      )) || [];
    if (tokens.length < 1) continue;
    const longest = [...tokens].sort((left, right) => right.length - left.length)[0];
    if (longest && longest !== 'structure') candidates.push(`${longest} structure`);
    if (tokens.length >= 2) {
      const tail = tokens.slice(-2).join(' ');
      if (!tail.endsWith(' structure')) candidates.push(`${tail} structure`);
    }
  }
  return unique(candidates.map(providerTerm).filter(Boolean));
}

function balancedProviderTerms(
  anchor: string,
  aliases: string[],
  strategy: PatsnapQueryStrategy,
  role: 'subject' | 'feature',
): string[] {
  const safeAnchor = providerTerm(anchor);
  const safeTerms = unique(
    [safeAnchor, ...aliases.map(providerTerm)].filter(Boolean),
  );
  const chinese = safeTerms.filter((item) => providerTermLanguage(item) === 'zh');
  const english = safeTerms.filter((item) => providerTermLanguage(item) === 'en');
  const executableEnglish = english.filter(
    (item) => !/\b(?:and|or|not)\b/i.test(item),
  );
  const compactEnglish = unique(english.flatMap(compactEnglishVariants));
  const chineseStructures = role === 'feature'
    ? chineseStructuralVariants(chinese, safeAnchor)
    : [];
  const englishStructures = role === 'feature'
    ? englishStructuralVariants(english)
    : [];
  const otherChinese = rankTermsByAnchor(
    chinese.filter((item) => normalized(item) !== normalized(safeAnchor)),
    safeAnchor,
  );

  if (strategy === 'compact-fallback') {
    return unique([
      safeAnchor,
      chineseStructures.find(
        (item) => normalized(item) !== normalized(safeAnchor),
      ) || otherChinese[0] || '',
      englishStructures[0] || compactEnglish[0] || executableEnglish[0] || '',
    ].filter(Boolean));
  }

  if (role === 'subject') {
    const compactChineseSubject = [...otherChinese]
      .sort((left, right) => normalized(left).length - normalized(right).length)[0] || '';
    const compactKey = normalized(compactChineseSubject);
    const complementaryChineseSubject = compactKey
      ? [...otherChinese]
        .filter((item) => {
          const key = normalized(item);
          return key
            && !key.includes(compactKey)
            && !compactKey.includes(key);
        })
        .sort((left, right) => normalized(left).length - normalized(right).length)[0] || ''
      : '';
    const nestedChineseSubject = compactKey
      ? [...otherChinese]
        .filter((item) => {
          const key = normalized(item);
          return key
            && key !== compactKey
            && key !== normalized(safeAnchor)
            && (key.includes(compactKey) || compactKey.includes(key));
        })
        .sort((left, right) => normalized(left).length - normalized(right).length)[0] || ''
      : '';
    const selectedSubjectTerms = unique([
      safeAnchor,
      compactChineseSubject,
      nestedChineseSubject,
      complementaryChineseSubject,
      ...otherChinese.slice(0, 2),
      ...executableEnglish.slice(0, 2),
    ].filter(Boolean));
    // Mixed-language OR groups consume more of P002's expression budget.  Keep
    // five in that case; an all-Chinese layered material subject may retain six
    // short layers (object, application and material class).
    const mixedLanguage = selectedSubjectTerms.some((item) => /[A-Za-z]/u.test(item));
    return selectedSubjectTerms.slice(0, mixedLanguage ? 5 : 6);
  }

  return unique([
    safeAnchor,
    ...otherChinese.slice(0, 3),
    executableEnglish[0] || '',
    executableEnglish[1] || '',
    compactEnglish.find(
      (item) => !english.some((full) => normalized(full) === normalized(item)),
    ) || '',
    chineseStructures[0] || '',
    englishStructures[0] || '',
  ].filter(Boolean));
}

function strictProviderTerms(anchor: string, aliases: string[]): string[] {
  const safeAnchor = providerTerm(anchor);
  if (!safeAnchor) return [];
  const faithful = unique([safeAnchor, ...aliases.map(providerTerm)].filter(Boolean));
  return unique([
    safeAnchor,
    faithful.find((item) => (
      providerTermLanguage(item) !== providerTermLanguage(safeAnchor)
    )) || '',
  ].filter(Boolean));
}

function patsnapTerm(value: string): string {
  if (
    providerTermLanguage(value) === 'en'
    && /\s/.test(value)
    && /\b(?:and|or|not)\b/i.test(value)
  ) {
    return `"${value.replace(/["\\]/g, ' ').replace(/\s+/g, ' ').trim()}"`;
  }
  return value;
}

function patsnapGroup(values: string[]): string {
  const terms = unique(values.map(providerTerm).filter(Boolean));
  if (terms.length < 1) throw new Error('P002 检索组缺少可安全传递的术语');
  return `(${terms.map(patsnapTerm).join(' OR ')})`;
}

function limitationSynonyms(limitation: JsonObject): string[] {
  return unique([
    ...((Array.isArray(limitation.synonyms_zh) ? limitation.synonyms_zh : []).map(text)),
    ...((Array.isArray(limitation.synonyms_en) ? limitation.synonyms_en : []).map(text)),
  ].flatMap(atomicSearchTerms));
}

function sourceSnapshot(investigation: JsonObject): JsonObject {
  const direct = asObject(investigation.source_snapshot);
  if (Object.keys(direct).length > 0) return direct;
  return asObject(asObject(investigation.investigation).source_snapshot);
}

export function targetPatentSummary(investigation: JsonObject): TargetPatentSummary {
  const source = sourceSnapshot(investigation);
  const patent = asObject(source.patent_snapshot);
  if (Object.keys(patent).length === 0) {
    throw new Error('目标专利尚未完成测试环境解析');
  }

  const claims = asObjects(patent.claims);
  const claim = claims.find((item) => text(item.claim_type).toUpperCase() === 'INDEPENDENT')
    || claims[0];
  if (!claim) throw new Error('目标专利解析结果没有权利要求');

  const claimId = text(claim.claim_id);
  const claimText = text(claim.expanded_claim_text) || text(claim.claim_text);
  if (!claimId || !claimText) throw new Error('目标专利缺少可检索的独立权利要求文本');

  const frozenImages = Array.isArray(source.target_images)
    ? source.target_images.filter((item) => text(item)).length
    : 0;
  const figures = Array.isArray(patent.figures) ? patent.figures.length : 0;
  const figureCount = Math.max(frozenImages, figures);
  if (figureCount < 1) {
    throw new Error('目标专利没有冻结附图，不能调用 GLM-4.6V 多模态查询规划');
  }

  return {
    patentNumber: text(patent.patent_number),
    title: text(patent.title),
    applicationDate: isoDate(patent.application_date),
    priorityDate: isoDate(patent.priority_date),
    publicationDate: isoDate(patent.publication_date),
    claimId,
    claimText,
    figureCount,
  };
}

export function selectCriticalDate(target: TargetPatentSummary): CriticalDateSelection {
  if (target.priorityDate) {
    return {
      date: target.priorityDate,
      basis: 'priority_date',
      requiresHumanReview: true,
    };
  }
  if (target.applicationDate) {
    return {
      date: target.applicationDate,
      basis: 'application_date',
      requiresHumanReview: true,
    };
  }
  throw new Error('目标专利没有可用的优先权日或申请日，禁止执行日期范围不明确的 P002 检索');
}

export function moduleRunRecord(value: JsonObject): JsonObject {
  return isJsonObject(value.module_run) ? value.module_run : value;
}

export function moduleRunId(value: JsonObject): string {
  const record = moduleRunRecord(value);
  return text(record.id) || text(value.module_run_id);
}

export function moduleRunStatus(value: JsonObject): {
  runStatus: string;
  jobStatus: string;
  terminal: boolean;
  successful: boolean;
  error: string;
} {
  const record = moduleRunRecord(value);
  const job = asObject(value.job);
  const runStatus = text(record.status || value.status).toLowerCase();
  const jobStatus = text(job.status || value.job_status).toLowerCase();
  const error = text(
    record.error_message
    || record.error
    || job.last_error
    || job.error_message
    || value.detail
    || value.error,
  );
  return {
    runStatus,
    jobStatus,
    terminal: TERMINAL_RUN_STATUSES.has(runStatus),
    successful: SUCCESS_RUN_STATUSES.has(runStatus),
    error,
  };
}

export function moduleOutput(value: JsonObject): JsonObject {
  const snapshot = asObject(moduleRunRecord(value).output_snapshot);
  return asObject(snapshot.output);
}

function limitationTerms(limitation: JsonObject): string[] {
  return unique([
    text(limitation.text),
    ...((Array.isArray(limitation.synonyms_zh) ? limitation.synonyms_zh : []).map(text)),
    ...((Array.isArray(limitation.synonyms_en) ? limitation.synonyms_en : []).map(text)),
  ].flatMap(atomicSearchTerms));
}

function queryRole(value: unknown): PatsnapQueryRole {
  const candidate = text(value);
  if (candidate === 'inventive_point_precision') return candidate;
  if (candidate === 'title_abstract_concept') return candidate;
  if (candidate === 'gap_followup') return candidate;
  return 'claim_context_recall';
}

function queryVariant(value: unknown, role: PatsnapQueryRole): PatsnapQueryVariant {
  const candidate = text(value);
  if (candidate === 'maximal_similarity_precision') return candidate;
  if (candidate === 'object_plus_inventive_point') return candidate;
  if (candidate === 'subject_classification_plus_inventive_point') return candidate;
  if (candidate === 'object_plus_inventive_classification') return candidate;
  if (candidate === 'object_plus_two_inventive_points') return candidate;
  if (candidate === 'object_plus_component_plus_effect') return candidate;
  if (candidate === 'system_architecture_recall') return candidate;
  if (candidate === 'target_citation_lookup') return candidate;
  if (candidate === 'target_effect_recall') return candidate;
  if (candidate === 'classification_action_recall') return candidate;
  if (candidate === 'title_abstract_concept') return candidate;
  if (candidate === 'gap_followup') return candidate;
  if (candidate === 'applicant_plus_object') return candidate;
  if (candidate === 'title_object_plus_desc_inventive') return candidate;
  if (candidate === 'classification_plus_desc_inventive') return candidate;
  if (candidate === 'desc_object_plus_desc_inventive_plus_effect') return candidate;
  if (candidate === 'title_keyword_object_plus_desc_function') return candidate;
  if (role === 'inventive_point_precision') return 'object_plus_inventive_point';
  if (role === 'title_abstract_concept') return 'title_abstract_concept';
  if (role === 'gap_followup') return 'gap_followup';
  return 'system_architecture_recall';
}

const FIXED_FIRST_ROUND_VARIANTS = new Set<PatsnapQueryVariant>([
  'applicant_plus_object',
  'title_object_plus_desc_inventive',
  'classification_plus_desc_inventive',
  'desc_object_plus_desc_inventive_plus_effect',
  'title_keyword_object_plus_desc_function',
]);

function expressionGroups(value: string): string[][] {
  return value
    .split(/\s+AND\s+/i)
    .map((rawGroup) => rawGroup.trim().replace(/^\(([\s\S]*)\)$/, '$1').trim())
    .filter(Boolean)
    .map((rawGroup) => unique(
      rawGroup
        .split(/\s+OR\s+/i)
        .map((item) => providerTerm(item))
        .filter(Boolean),
    ).slice(0, 5))
    .filter((group) => group.length > 0);
}

function publicationNumber(value: unknown): string {
  const candidate = text(value).toUpperCase().replace(/[^A-Z0-9]/g, '');
  return /^(?:WO|US|EP|CN|JP|KR|DE|GB|TW|AU|CA)\d{5,}[A-Z]\d?$/u.test(candidate)
    ? candidate
    : '';
}

function fixedFirstRoundExpression(
  variant: PatsnapQueryVariant,
  groups: string[][],
  classifications: string[],
  applicants: string[],
): string {
  const compiledGroups = groups.map(patsnapGroup);
  const ttlAbstract = (group: string) => `(TTL:${group} OR ABST:${group})`;
  const desc = (group: string) => `(DESC:${group})`;

  if (variant === 'applicant_plus_object') {
    if (applicants.length !== 1 || compiledGroups.length < 1) {
      throw new Error('申请人+客体检索线必须有唯一申请人和客体词组');
    }
    return [
      `(AN:${patsnapGroup(applicants)})`,
      ...compiledGroups.map(ttlAbstract),
    ].join(' AND ');
  }
  if (variant === 'title_object_plus_desc_inventive') {
    if (compiledGroups.length < 2) {
      throw new Error('标题客体+说明书核心发明点检索线缺少固定词组');
    }
    return [`(TTL:${compiledGroups[0]})`, ...compiledGroups.slice(1).map(desc)].join(' AND ');
  }
  if (variant === 'classification_plus_desc_inventive') {
    if (classifications.length < 1 || compiledGroups.length < 1) {
      throw new Error('分类号+说明书核心发明点检索线缺少固定锚点');
    }
    return [
      `(IPC:(${classifications.slice(0, 2).join(' OR ')}))`,
      ...compiledGroups.map(desc),
    ].join(' AND ');
  }
  if (variant === 'desc_object_plus_desc_inventive_plus_effect') {
    if (compiledGroups.length < 3) {
      throw new Error('说明书客体+核心发明点+效果检索线缺少固定词组');
    }
    return compiledGroups.map(desc).join(' AND ');
  }
  if (variant === 'title_keyword_object_plus_desc_function') {
    if (compiledGroups.length < 2) {
      throw new Error('标题关键词客体+说明书功能效果检索线缺少固定词组');
    }
    return [ttlAbstract(compiledGroups[0]), ...compiledGroups.slice(1).map(desc)].join(' AND ');
  }
  throw new Error(`不支持的固定首轮检索变体：${variant}`);
}

function validatedFixedProviderExpression(
  variant: PatsnapQueryVariant,
  providerExpression: string,
  fallbackExpression: string,
  applicantTerms: string[],
): string {
  const candidate = providerExpression.trim() || fallbackExpression;
  if (!candidate || candidate.length > 12_000 || /[;\r\n]/.test(candidate)) {
    throw new Error(`固定首轮检索线缺少安全的字段化表达式：${variant}`);
  }
  const fields = [...candidate.matchAll(/(?:^|[\s(])(?:NOT\s+)?([A-Z]{2,5}):/g)]
    .map((match) => match[1]);
  const allowedByVariant: Record<string, Set<string>> = {
    applicant_plus_object: new Set(['AN', 'TTL', 'ABST']),
    title_object_plus_desc_inventive: new Set(['TTL', 'DESC']),
    classification_plus_desc_inventive: new Set(['IPC', 'DESC']),
    desc_object_plus_desc_inventive_plus_effect: new Set(['DESC']),
    title_keyword_object_plus_desc_function: new Set(['TTL', 'ABST', 'DESC']),
  };
  const allowed = allowedByVariant[variant];
  if (!allowed || fields.length < 2 || fields.some((field) => !allowed.has(field))) {
    throw new Error(`固定首轮检索线含有不允许的字段：${variant}`);
  }
  const requiredByVariant: Record<string, string[]> = {
    applicant_plus_object: ['AN', 'TTL', 'ABST'],
    title_object_plus_desc_inventive: ['TTL', 'DESC'],
    classification_plus_desc_inventive: ['IPC', 'DESC'],
    desc_object_plus_desc_inventive_plus_effect: ['DESC'],
    title_keyword_object_plus_desc_function: ['TTL', 'ABST', 'DESC'],
  };
  if (requiredByVariant[variant].some((field) => !fields.includes(field))) {
    throw new Error(`固定首轮检索线字段不完整：${variant}`);
  }
  if (variant === 'desc_object_plus_desc_inventive_plus_effect'
      && fields.filter((field) => field === 'DESC').length < 3) {
    throw new Error('说明书客体+核心发明点+效果检索线必须至少包含三个 DESC 词组');
  }
  if (variant === 'applicant_plus_object') {
    if (applicantTerms.length !== 1 || !containsTerm(candidate, applicantTerms[0])) {
      throw new Error('申请人+客体检索线没有绑定唯一申请人');
    }
    if (/\bNOT\s+PN:/i.test(candidate) || /\bPN:/i.test(candidate)) {
      throw new Error('P002 不接受申请人线的供应商侧 PN 排除；目标公开号必须由本地结果层排除');
    }
  }
  return candidate;
}

function searchScope(value: unknown): PatsnapSearchScope {
  const candidate = text(value);
  if (candidate === 'title_abstract') return candidate;
  if (candidate === 'claims') return candidate;
  if (candidate === 'classification') return candidate;
  return 'full_text';
}

function classificationCode(value: unknown): string {
  const match = text(value).match(/\b([A-HY]\d{2}[A-Z])\s*(\d+\/\d+)\b/i);
  return match ? `${match[1].toUpperCase()}${match[2]}` : '';
}

function scopedPatsnapExpression(
  groups: string[],
  scope: PatsnapSearchScope,
  classifications: string[],
): string {
  const textExpression = groups.join(' AND ');
  let fieldExpression = '';
  if (scope === 'title_abstract') {
    fieldExpression = `(TTL:(${textExpression}) OR ABST:(${textExpression}))`;
  } else if (scope === 'claims') {
    fieldExpression = `CLMS:(${textExpression})`;
  } else {
    fieldExpression = `TACD:(${textExpression})`;
  }
  const classValues = unique(classifications.map(classificationCode).filter(Boolean));
  if (!classValues.length) return fieldExpression;
  return `(IPC:(${classValues.join(' OR ')})) AND (${fieldExpression})`;
}

export function buildPatsnapSearchPlan(
  queryPlanModuleRun: JsonObject,
  criticalDate: string,
  maxResults = 10,
  strategy: PatsnapQueryStrategy = 'balanced',
  preferredQueryRole?: PatsnapQueryRole,
  preferredQueryId?: string,
  preferredQueryVariant?: PatsnapQueryVariant,
): PatsnapSearchPlan {
  const output = moduleOutput(queryPlanModuleRun);
  const queries = asObjects(output.queries);
  const rolePriority: Record<PatsnapQueryRole, number> = {
    inventive_point_precision: 0,
    claim_context_recall: 1,
    title_abstract_concept: 2,
    gap_followup: 3,
  };
  const variantPriority: Record<PatsnapQueryVariant, number> = {
    applicant_plus_object: 0,
    title_object_plus_desc_inventive: 1,
    classification_plus_desc_inventive: 2,
    desc_object_plus_desc_inventive_plus_effect: 3,
    title_keyword_object_plus_desc_function: 4,
    maximal_similarity_precision: 10,
    object_plus_inventive_point: 11,
    subject_classification_plus_inventive_point: 12,
    object_plus_inventive_classification: 13,
    object_plus_two_inventive_points: 14,
    object_plus_component_plus_effect: 15,
    system_architecture_recall: 16,
    target_citation_lookup: 17,
    target_effect_recall: 18,
    classification_action_recall: 19,
    title_abstract_concept: 20,
    gap_followup: 21,
  };
  const candidateQueries = queries.filter((item) => (
    text(item.provider_kind) === 'patent'
    && text(item.search_objective || 'full_claim_single_reference') === 'full_claim_single_reference'
    && text(item.date_channel || 'ordinary_prior_art') === 'ordinary_prior_art'
    && (!preferredQueryRole || queryRole(item.query_role) === preferredQueryRole)
    && (!preferredQueryId || text(item.query_id) === preferredQueryId)
    && (
      !preferredQueryVariant
      || queryVariant(item.query_variant, queryRole(item.query_role))
        === preferredQueryVariant
    )
  )).sort(
    (left, right) => {
      const leftRole = queryRole(left.query_role);
      const rightRole = queryRole(right.query_role);
      const variantDifference = (
        variantPriority[queryVariant(left.query_variant, leftRole)]
        - variantPriority[queryVariant(right.query_variant, rightRole)]
      );
      if (variantDifference !== 0) return variantDifference;
      return rolePriority[leftRole] - rolePriority[rightRole];
    },
  );
  if (candidateQueries.length < 1) {
    throw new Error('GLM-4.6V 没有生成普通现有技术通道的完整权利要求专利检索式');
  }

  const limitations = asObjects(output.limitations);
  const limitationsById = new Map(
    limitations.map((item) => [text(item.feature_id), item] as const),
  );
  let selected: {
    query: JsonObject;
    technicalSubject: string;
    expression: string;
    subjectCandidates: string[];
    anchoredSubjectTerms: string[];
    featureAnchors: FeatureAnchor[];
    featureTermGroups: Map<string, string[]>;
    queryRole: PatsnapQueryRole;
    queryVariant: PatsnapQueryVariant;
    searchScope: PatsnapSearchScope;
    classificationAnchors: string[];
  } | null = null;
  let completeQueryCount = 0;
  let twoOfThreeAnchoredQueryCount = 0;

  for (const query of candidateQueries) {
    const technicalSubject = text(query.technical_subject) || text(output.technical_subject);
    const expression = text(query.expression);
    if (!technicalSubject || !expression) continue;
    completeQueryCount += 1;
    const hasExplicitRole = Boolean(text(query.query_role));
    const selectedRole = queryRole(query.query_role);
    const selectedVariant = queryVariant(query.query_variant, selectedRole);
    const selectedScope = searchScope(query.search_scope);
    if (FIXED_FIRST_ROUND_VARIANTS.has(selectedVariant)) {
      const date = isoDate(criticalDate);
      if (!date) throw new Error('P002 检索缺少有效关键日');
      if (strategy === 'compact-fallback') {
        throw new Error('固定五组首轮检索线不执行自动收敛或重复回退查询');
      }
      const groups = expressionGroups(expression);
      const classificationAnchors = unique(
        (Array.isArray(query.classification_anchors)
          ? query.classification_anchors
          : []
        ).map(classificationCode).filter(Boolean),
      ).slice(0, 2);
      const applicantTerms = unique(
        (Array.isArray(query.applicant_terms) ? query.applicant_terms : [])
          .map(text)
          .map(providerTerm)
          .filter(Boolean),
      );
      const excludedPublication = publicationNumber(query.excluded_publication);
      const compiledFallbackExpression = fixedFirstRoundExpression(
        selectedVariant,
        groups,
        classificationAnchors,
        applicantTerms,
      );
      const frozenProviderExpression = text(query.provider_expression);
      const providerExpression = validatedFixedProviderExpression(
        selectedVariant,
        frozenProviderExpression,
        compiledFallbackExpression,
        applicantTerms,
      );
      const subjectGroupCount = selectedVariant === 'classification_plus_desc_inventive'
        ? 0
        : 1;
      const subjectTerms = groups.slice(0, subjectGroupCount).flat();
      const featureQueryGroups = groups.slice(subjectGroupCount);
      const featureTerms = unique(
        (Array.isArray(query.feature_terms) ? query.feature_terms : [])
          .map(text)
          .filter(Boolean),
      );
      const maximum = Math.min(10, Math.max(1, Math.trunc(maxResults)));
      return {
        queryId: text(query.query_id),
        strategy,
        expression: providerExpression,
        language: text(query.language) || 'zh',
        technicalSubject,
        subjectTerms,
        featureTerms,
        featureQueryGroups,
        queryRole: selectedRole,
        queryVariant: selectedVariant,
        searchScope: selectedScope,
        scopeReason: text(query.scope_reason),
        classificationAnchors,
        applicantTerms,
        excludedPublication,
        allowZeroResults: query.allow_zero_results === true,
        compactFallbackAllowed: false,
        model: text(output.model),
        usedTargetImages: integer(output.used_target_images) || 0,
        input: {
          search_provider: 'patsnap',
          search_modality: 'text',
          query: {
            text: providerExpression,
            subject_terms: subjectTerms,
            feature_terms: featureTerms,
            classification_terms: classificationAnchors,
          },
          subject_terms: subjectTerms,
          feature_terms: featureTerms,
          classification_terms: classificationAnchors,
          excluded_publication: excludedPublication || undefined,
          language: text(query.language) || 'zh',
          server_before: date,
          max_results: maximum,
          query_plan_query_id: text(query.query_id),
          query_role: selectedRole,
          query_variant: selectedVariant,
          allow_zero_results: query.allow_zero_results === true,
          compact_fallback_allowed: false,
        },
      };
    }
    if (selectedVariant === 'target_citation_lookup') {
      const citation = text(query.target_citation || expression)
        .toUpperCase()
        .replace(/[^A-Z0-9]/g, '');
      if (!/^(?:WO|US|EP|CN|JP|KR|DE|GB|TW|AU|CA)\d{5,}[A-Z]\d?$/u.test(citation)) {
        continue;
      }
      const date = isoDate(criticalDate);
      if (!date) throw new Error('P002 检索缺少有效关键日');
      const maximum = Math.min(10, Math.max(1, Math.trunc(maxResults)));
      const providerExpression = `PN:(${citation})`;
      return {
        queryId: text(query.query_id),
        strategy,
        expression: providerExpression,
        language: text(query.language) || 'zh',
        technicalSubject,
        subjectTerms: [],
        featureTerms: [],
        featureQueryGroups: [],
        queryRole: selectedRole,
        queryVariant: selectedVariant,
        searchScope: selectedScope,
        scopeReason: text(query.scope_reason),
        classificationAnchors: [],
        applicantTerms: [],
        excludedPublication: '',
        allowZeroResults: true,
        compactFallbackAllowed: false,
        model: text(output.model),
        usedTargetImages: integer(output.used_target_images) || 0,
        input: {
          search_provider: 'patsnap',
          search_modality: 'text',
          query: {
            text: providerExpression,
            subject_terms: [],
            feature_terms: [],
            classification_terms: [],
          },
          subject_terms: [],
          feature_terms: [],
          classification_terms: [],
          language: text(query.language) || 'zh',
          server_before: date,
          max_results: maximum,
          query_plan_query_id: text(query.query_id),
          query_role: selectedRole,
          query_variant: selectedVariant,
          allow_zero_results: true,
          compact_fallback_allowed: false,
        },
      };
    }
    if (
      selectedVariant === 'target_effect_recall'
      || selectedVariant === 'classification_action_recall'
    ) {
      const effectTerms = unique(
        (Array.isArray(query.target_effect_terms) ? query.target_effect_terms : [])
          .map(text)
          .filter(Boolean),
      );
      const contextTerms = unique(
        (Array.isArray(query.target_context_terms) ? query.target_context_terms : [])
          .map(text)
          .filter(Boolean),
      );
      if (effectTerms.length < 1) continue;
      const classificationAnchors = unique(
        (Array.isArray(query.classification_anchors)
          ? query.classification_anchors
          : []
        ).map(classificationCode).filter(Boolean),
      );
      const classificationOnly = selectedVariant === 'classification_action_recall';
      const subjectTerms = classificationOnly
        ? []
        : balancedProviderTerms(
          technicalSubject,
          unique([
            technicalSubject,
            ...subjectHeadTerms(technicalSubject),
            ...((Array.isArray(output.subject_synonyms_zh) ? output.subject_synonyms_zh : []).map(text)),
            ...((Array.isArray(output.subject_synonyms_en) ? output.subject_synonyms_en : []).map(text)),
          ]),
          strategy,
          'subject',
        );
      const effectQueryTerms = unique(effectTerms.map(providerTerm).filter(Boolean)).slice(0, 8);
      const contextQueryTerms = contextTerms.length > 0
        ? balancedProviderTerms(contextTerms[0], contextTerms, strategy, 'feature').slice(0, 8)
        : [];
      if (
        effectQueryTerms.length < 1
        || (!classificationOnly && subjectTerms.length < 1)
        || (classificationOnly && classificationAnchors.length < 1)
      ) continue;
      const date = isoDate(criticalDate);
      if (!date) throw new Error('P002 检索缺少有效关键日');
      const maximum = Math.min(10, Math.max(1, Math.trunc(maxResults)));
      const providerExpression = scopedPatsnapExpression(
        [
          ...(!classificationOnly ? [patsnapGroup(subjectTerms)] : []),
          ...(!classificationOnly && contextQueryTerms.length > 0
            ? [patsnapGroup(contextQueryTerms)]
            : []),
          patsnapGroup(effectQueryTerms),
        ],
        selectedScope,
        classificationAnchors,
      );
      return {
        queryId: text(query.query_id),
        strategy,
        expression: providerExpression,
        language: text(query.language) || 'zh',
        technicalSubject,
        subjectTerms,
        featureTerms: [...(!classificationOnly ? contextTerms : []), ...effectTerms],
        featureQueryGroups: [
          ...(!classificationOnly && contextQueryTerms.length > 0
            ? [contextQueryTerms]
            : []),
          effectQueryTerms,
        ],
        queryRole: selectedRole,
        queryVariant: selectedVariant,
        searchScope: selectedScope,
        scopeReason: text(query.scope_reason),
        classificationAnchors,
        applicantTerms: [],
        excludedPublication: '',
        allowZeroResults: true,
        compactFallbackAllowed: false,
        model: text(output.model),
        usedTargetImages: integer(output.used_target_images) || 0,
        input: {
          search_provider: 'patsnap',
          search_modality: 'text',
          query: {
            text: providerExpression,
            subject_terms: subjectTerms,
            feature_terms: [...(!classificationOnly ? contextTerms : []), ...effectTerms],
            classification_terms: classificationAnchors,
          },
          subject_terms: subjectTerms,
          feature_terms: [...(!classificationOnly ? contextTerms : []), ...effectTerms],
          classification_terms: classificationAnchors,
          language: text(query.language) || 'zh',
          server_before: date,
          max_results: maximum,
          query_plan_query_id: text(query.query_id),
          query_role: selectedRole,
          query_variant: selectedVariant,
          allow_zero_results: true,
          compact_fallback_allowed: false,
        },
      };
    }
    if (
      hasExplicitRole
      && selectedRole === 'claim_context_recall'
      && selectedScope !== 'claims'
    ) continue;
    if (
      hasExplicitRole
      &&
      selectedRole === 'title_abstract_concept'
      && selectedScope !== 'title_abstract'
    ) continue;

    const subjectCandidates = unique([
      technicalSubject,
      ...subjectHeadTerms(technicalSubject),
      ...((Array.isArray(output.subject_synonyms_zh) ? output.subject_synonyms_zh : []).map(text)),
      ...((Array.isArray(output.subject_synonyms_en) ? output.subject_synonyms_en : []).map(text)),
    ]);
    const anchoredSubjectTerms = subjectCandidates.filter(
      (item) => containsTerm(expression, item),
    );
    const classificationAnchors = unique(
      (Array.isArray(query.classification_anchors)
        ? query.classification_anchors
        : []
      ).map(classificationCode).filter(Boolean),
    );

    const featureIds = Array.isArray(query.feature_ids)
      ? query.feature_ids.map(text).filter(Boolean)
      : [];
    const selectedLimitations = featureIds
      .map((featureId) => limitationsById.get(featureId))
      .filter(isJsonObject);
    const suppliedFeatureTerms = Array.isArray(query.feature_terms)
      ? query.feature_terms.map(text)
      : [];
    const suppliedFeatureTermGroups = Array.isArray(query.feature_term_groups)
      ? query.feature_term_groups
      : [];
    const featureTermGroups = new Map<string, string[]>();
    featureIds.forEach((featureId, index) => {
      const rawGroup = suppliedFeatureTermGroups[index];
      featureTermGroups.set(
        featureId,
        unique(
          (Array.isArray(rawGroup) ? rawGroup : [])
            .map(text)
            .flatMap(atomicSearchTerms)
            .filter(Boolean),
        ),
      );
    });
    const featureAnchors = inferDistinctFeatureAnchors(
      expression,
      selectedLimitations,
      subjectCandidates,
      suppliedFeatureTerms,
    );
    const anchoredCategoryCount = [
      featureAnchors.length > 0,
      anchoredSubjectTerms.length > 0,
      classificationAnchors.length > 0,
    ].filter(Boolean).length;
    if (anchoredCategoryCount < 2) continue;
    twoOfThreeAnchoredQueryCount += 1;

    selected = {
      query,
      technicalSubject,
      expression,
      subjectCandidates,
      anchoredSubjectTerms,
      featureAnchors: featureAnchors.slice(0, (
        selectedVariant === 'maximal_similarity_precision'
          ? 6
          : selectedVariant === 'object_plus_two_inventive_points'
            || selectedVariant === 'system_architecture_recall'
            || selectedVariant === 'title_abstract_concept'
            ? 2
            : selectedVariant === 'object_plus_inventive_classification'
              ? 0
              : 1
      )),
      featureTermGroups,
      queryRole: selectedRole,
      queryVariant: selectedVariant,
      searchScope: selectedScope,
      classificationAnchors,
    };
    break;
  }

  if (!selected) {
    if (completeQueryCount < 1) {
      throw new Error('GLM-4.6V 返回的检索式缺少技术主题或表达式');
    }
    if (twoOfThreeAnchoredQueryCount < 1) {
      throw new Error(
        'I2 检索式未满足“三类任二”：发明点/技术特征关键词、保护客体/类别、分类号中至少实际包含两类',
      );
    }
    throw new Error('I2 检索式没有可编译的有效关键词，已拒绝执行 P002 检索');
  }

  const {
    query,
    technicalSubject,
    subjectCandidates,
    anchoredSubjectTerms,
    featureAnchors,
    featureTermGroups,
    queryRole: selectedQueryRole,
    queryVariant: selectedQueryVariant,
    searchScope: selectedSearchScope,
    classificationAnchors,
  } = selected;
  const allowZeroResults = query.allow_zero_results === true
    || selectedQueryVariant === 'maximal_similarity_precision';
  const compactFallbackAllowed = query.compact_fallback_allowed !== false
    && selectedQueryVariant !== 'maximal_similarity_precision';
  if (strategy === 'compact-fallback' && !compactFallbackAllowed) {
    throw new Error('最大相似长线允许零结果，不执行自动收敛查询');
  }
  // In the subject-classification lane the IPC/CPC anchor already supplies the
  // technical category.  Requiring the target's exact subject wording as a
  // third AND group defeats the purpose of this recall lane when older art uses
  // a different category name.  The feature + classification pair still
  // satisfies the same two-of-three execution contract.
  const subjectTerms = selectedQueryVariant === 'subject_classification_plus_inventive_point'
    ? []
    : anchoredSubjectTerms.length
    ? (
      selectedQueryVariant === 'maximal_similarity_precision'
        ? strictProviderTerms(anchoredSubjectTerms[0], subjectCandidates)
        : balancedProviderTerms(
        anchoredSubjectTerms[0],
        subjectCandidates,
        strategy,
        'subject',
        )
    )
    : [];

  const featureTerms = featureAnchors.map((item) => item.term);

  const date = isoDate(criticalDate);
  if (!date) throw new Error('P002 检索缺少有效关键日');
  const maximum = Math.min(10, Math.max(1, Math.trunc(maxResults)));
  const featureQueryGroups = featureAnchors.map(({ limitation, term }) => {
    const featureId = text(limitation.feature_id);
    const limitationAliases = limitationSynonyms(limitation);
    const atomicLimitationAliases = limitationAliases.filter(
      (item) => sameAtomicSearchConcept(item, term),
    );
    const groupAliases = (featureTermGroups.get(featureId) || []).filter(
      (item) => (
        sameAtomicSearchConcept(item, term)
        && (
          traceableMechanismTerm(item, [term, ...atomicLimitationAliases])
          // Narrow component families such as 罐/tank/reservoir are ordinary
          // search-language equivalents.  They remain query aliases only;
          // Module 5 must still prove structure and role from the document.
          || sameAtomicSearchConcept(item, term)
        )
      ),
    );
    const limitationKeys = atomicLimitationAliases.map(normalized).filter(Boolean);
    const semanticAliases = unique(
      groupAliases.filter((item) => {
        const key = normalized(item);
        return key && !limitationKeys.some(
          (limitationKey) => (
            key.includes(limitationKey) || limitationKey.includes(key)
          ),
        );
      }).map(providerTerm).filter(Boolean),
    );
    const semanticChinese = semanticAliases.filter(
      (item) => providerTermLanguage(item) === 'zh',
    );
    const semanticEnglish = semanticAliases.filter(
      (item) => providerTermLanguage(item) === 'en',
    );
    const compiledCandidates = (
      selectedQueryVariant === 'maximal_similarity_precision'
        ? strictProviderTerms(term, groupAliases)
        : balancedProviderTerms(
          term,
          unique([...groupAliases, ...atomicLimitationAliases]),
          strategy,
          'feature',
        )
    );
    const declaredAliasKeys = new Set(
      [term, ...groupAliases, ...atomicLimitationAliases]
        .map(providerTerm)
        .map(normalized)
        .filter(Boolean),
    );
    const candidates = unique([
      ...compiledCandidates.filter((item) => (
        groupAliases.length < 1 || declaredAliasKeys.has(normalized(item))
      )),
      ...(
        selectedQueryVariant === 'maximal_similarity_precision'
          ? []
          : [...semanticChinese.slice(0, 2), ...semanticEnglish.slice(0, 4)]
      ),
    ]).filter((item) => (
      !subjectTerms.some((subject) => normalized(subject) === normalized(item))
      || normalized(item) === normalized(term)
    ));
    if (!candidates.some((item) => normalized(item) === normalized(providerTerm(term)))) {
      throw new Error('P002 特征组在编译时丢失必要技术特征锚点');
    }
    return candidates;
  });
  const providerExpression = scopedPatsnapExpression(
    [
      ...(subjectTerms.length ? [patsnapGroup(subjectTerms)] : []),
      ...featureQueryGroups.map(patsnapGroup),
    ],
    selectedSearchScope,
    classificationAnchors,
  );

  return {
    queryId: text(query.query_id),
    strategy,
    expression: providerExpression,
    language: text(query.language) || 'zh',
    technicalSubject,
    subjectTerms,
    featureTerms,
    featureQueryGroups,
    queryRole: selectedQueryRole,
    queryVariant: selectedQueryVariant,
    searchScope: selectedSearchScope,
    scopeReason: text(query.scope_reason),
    classificationAnchors,
    applicantTerms: [],
    excludedPublication: '',
    allowZeroResults,
    compactFallbackAllowed,
    model: text(output.model),
    usedTargetImages: integer(output.used_target_images) || 0,
    input: {
      search_provider: 'patsnap',
      search_modality: 'text',
      query: {
        text: providerExpression,
        subject_terms: subjectTerms,
        feature_terms: featureTerms,
        classification_terms: classificationAnchors,
      },
      subject_terms: subjectTerms,
      feature_terms: featureTerms,
      classification_terms: classificationAnchors,
      language: text(query.language) || 'zh',
      server_before: date,
      max_results: maximum,
      query_plan_query_id: text(query.query_id),
      query_role: selectedQueryRole,
      query_variant: selectedQueryVariant,
      allow_zero_results: allowZeroResults,
      compact_fallback_allowed: compactFallbackAllowed,
    },
  };
}

export function patsnapSearchResult(moduleRun: JsonObject): PatsnapSearchResult {
  const output = moduleOutput(moduleRun);
  const documents = asObjects(output.documents);
  const candidates = documents.map((document): PatsnapCandidate => {
    const rawMetadata = asObject(document.raw_metadata);
    const provenance = asObject(document.provenance);
    return {
      externalId: text(document.external_id),
      publicationNumber: text(document.publication_number),
      title: text(document.title),
      authority: text(document.authority),
      publicationDate: isoDate(document.publication_date),
      filingDate: isoDate(document.filing_date),
      assignee: text(rawMetadata.current_assignee) || text(rawMetadata.original_assignee),
      stage: text(document.stage),
      totalResultCount: integer(provenance.total_result_count),
    };
  });
  const artifacts = asObjects(output.search_artifacts).map((artifact) => ({
    kind: text(artifact.kind || artifact.artifact_type),
    sha256: text(artifact.sha256),
    byteSize: integer(artifact.byte_size),
  }));
  const totals = candidates
    .map((candidate) => candidate.totalResultCount)
    .filter((value): value is number => value !== null);

  return {
    actualProvider: text(output.actual_provider),
    networkUsed: output.network_used === true,
    returnedCount: integer(output.count) ?? candidates.length,
    totalResultCount: totals.length > 0 ? Math.max(...totals) : null,
    candidates,
    artifacts,
  };
}
