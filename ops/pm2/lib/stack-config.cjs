'use strict';

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const PROJECT_ROOT = path.resolve(__dirname, '../../..');

const STACKS = Object.freeze({
  test: Object.freeze({
    serviceDir: path.join(PROJECT_ROOT, '5-invalidity-search-test'),
    envFileName: '.env.test.local',
    expectedPort: '5209',
    expectedSchema: 'invalidity_test',
    expectedArtifactFragment: '/invalidity/test',
    apiVariable: 'INVALIDITY_TEST_API_URL',
    apiTokenVariable: 'INVALIDITY_TEST_API_TOKEN',
    module1Variable: 'INVALIDITY_TEST_MODULE1_API_URL',
    expectedModule1Port: '5201',
    prefix: 'INVALIDITY_TEST',
  }),
  prod: Object.freeze({
    serviceDir: path.join(PROJECT_ROOT, '5-invalidity-search-prod'),
    envFileName: '.env.prod.local',
    expectedPort: '5109',
    expectedSchema: 'invalidity_prod',
    expectedArtifactFragment: '/invalidity/prod',
    apiVariable: 'INVALIDITY_PROD_API_URL',
    apiTokenVariable: 'INVALIDITY_PROD_API_TOKEN',
    module1Variable: 'INVALIDITY_PROD_MODULE1_API_URL',
    expectedModule1Port: '5101',
    prefix: 'INVALIDITY_PROD',
  }),
});

const OPTIONAL_INVALIDITY_KEYS = Object.freeze([
  'INVALIDITY_MAX_ROUNDS',
  'INVALIDITY_MAX_CANDIDATES_PER_QUERY',
  'INVALIDITY_WORKER_CONCURRENCY',
  'INVALIDITY_WORKER_POLL_SECONDS',
  'INVALIDITY_JOB_LEASE_SECONDS',
  'INVALIDITY_LLM_TIMEOUT_SECONDS',
  'INVALIDITY_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS',
  'INVALIDITY_LLM_DIRECT_PROBE_TIMEOUT_SECONDS',
  'INVALIDITY_I2_LLM_TIMEOUT_SECONDS',
  'INVALIDITY_I2_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS',
  'INVALIDITY_SOURCE_FETCH_TIMEOUT_SECONDS',
  'INVALIDITY_SOURCE_MAX_BYTES',
  'INVALIDITY_IMAGE_MAX_BYTES',
  'INVALIDITY_SOURCE_MAX_REDIRECTS',
]);

const PATSNAP_BASE_URL = 'https://connect.zhihuiya.com';
const PATSNAP_COUNT_PATHS = new Set([
  '/search/patent/query-search-count',
  '/search/patent/query-search-count/v2',
]);
const PATSNAP_SEARCH_PATHS = new Set([
  '/search/patent/query-search-patent',
  '/search/patent/query-search-patent/v2',
]);

function parseEnvFile(filePath) {
  if (!fs.existsSync(filePath)) {
    throw new Error(
      `缺少隔离环境文件 ${filePath}；请从同目录 .env.example 复制并填写，禁止回落其他环境`,
    );
  }

  const values = {};
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/);
  lines.forEach((line, index) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) {
      return;
    }
    const separatorIndex = trimmed.indexOf('=');
    if (separatorIndex <= 0) {
      throw new Error(`${filePath}:${index + 1} 不是 KEY=VALUE 格式`);
    }
    const key = trimmed.slice(0, separatorIndex).trim();
    let value = trimmed.slice(separatorIndex + 1).trim();
    if (!/^[A-Z][A-Z0-9_]*$/.test(key)) {
      throw new Error(`${filePath}:${index + 1} 的变量名不合法: ${key}`);
    }
    if (Object.prototype.hasOwnProperty.call(values, key)) {
      throw new Error(`${filePath}:${index + 1} 重复定义 ${key}`);
    }
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  });
  return values;
}

function required(values, key) {
  const value = String(values[key] || '').trim();
  if (!value) {
    throw new Error(`隔离环境文件缺少 ${key}`);
  }
  if (/REPLACE_|CHANGE_ME|<[^>]+>/i.test(value)) {
    throw new Error(`${key} 仍是占位值，拒绝启动`);
  }
  return value;
}

function requiredToken(values, key) {
  const value = required(values, key);
  if (value.length < 16) {
    throw new Error(`${key} 必须是至少 16 字符的环境专用 token`);
  }
  return value;
}

function assertHttpUrl(value, key) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch (error) {
    throw new Error(`${key} 必须是完整的 http(s) URL`);
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error(`${key} 只能使用 http 或 https`);
  }
  return parsed;
}

function validatePatsnapConfiguration(values, prefix, patentProvider) {
  const keyName = `${prefix}_PATSNAP_API_KEY`;
  const baseName = `${prefix}_PATSNAP_BASE_URL`;
  const countName = `${prefix}_PATSNAP_COUNT_PATH`;
  const searchName = `${prefix}_PATSNAP_SEARCH_PATH`;
  const apiKey = String(values[keyName] || '').trim();
  const baseUrl = String(values[baseName] || PATSNAP_BASE_URL).trim();
  const countPath = String(
    values[countName] || '/search/patent/query-search-count/v2',
  ).trim();
  const searchPath = String(
    values[searchName] || '/search/patent/query-search-patent/v2',
  ).trim();

  if (baseUrl !== PATSNAP_BASE_URL) {
    throw new Error(`${baseName} 必须严格等于 ${PATSNAP_BASE_URL}`);
  }
  const parsedBase = assertHttpUrl(baseUrl, baseName);
  if (
    parsedBase.protocol !== 'https:' ||
    parsedBase.hostname !== 'connect.zhihuiya.com' ||
    parsedBase.port !== '' ||
    !['', '/'].includes(parsedBase.pathname) ||
    parsedBase.username !== '' ||
    parsedBase.password !== '' ||
    parsedBase.search !== '' ||
    parsedBase.hash !== ''
  ) {
    throw new Error(`${baseName} 必须严格等于 ${PATSNAP_BASE_URL}`);
  }
  if (!PATSNAP_COUNT_PATHS.has(countPath)) {
    throw new Error(`${countName} 不在官方候选路径白名单`);
  }
  if (!PATSNAP_SEARCH_PATHS.has(searchPath)) {
    throw new Error(`${searchName} 不在官方候选路径白名单`);
  }
  if (apiKey && (apiKey.length < 16 || !apiKey.startsWith('sk-') || /\s/.test(apiKey))) {
    throw new Error(`${keyName} 必须是当前环境有效的 sk- 前缀智慧芽 API Key`);
  }
  if (patentProvider === 'patsnap' && !apiKey) {
    throw new Error(`${prefix}_PATENT_PROVIDER=patsnap 时必须配置 ${keyName}`);
  }
}

function validateAllowedSourceRoots(rawValue, environment, artifactRoot, serviceDir) {
  const rawRoots = String(rawValue || '')
    .split(path.delimiter)
    .flatMap((item) => item.split(','))
    .map((item) => item.trim())
    .filter(Boolean);
  if (rawRoots.length === 0) {
    throw new Error('INVALIDITY_ALLOWED_SOURCE_ROOTS 必须包含显式上传目录');
  }
  const expectedRoot = path.resolve(serviceDir, '.data', 'uploads', environment);
  const roots = rawRoots.map((item) => {
    if (!path.isAbsolute(item)) {
      throw new Error('INVALIDITY_ALLOWED_SOURCE_ROOTS 只接受绝对路径');
    }
    const resolved = path.resolve(item);
    if (resolved === path.parse(resolved).root) {
      throw new Error('INVALIDITY_ALLOWED_SOURCE_ROOTS 不能包含文件系统根目录');
    }
    if (resolved !== expectedRoot && !resolved.startsWith(`${expectedRoot}${path.sep}`)) {
      throw new Error(
        `INVALIDITY_ALLOWED_SOURCE_ROOTS 必须位于 ${expectedRoot} 隔离根目录`,
      );
    }
    const artifact = path.resolve(artifactRoot);
    if (
      resolved === artifact ||
      resolved.startsWith(`${artifact}${path.sep}`) ||
      artifact.startsWith(`${resolved}${path.sep}`)
    ) {
      throw new Error('上传目录与工件目录必须互不包含');
    }
    return resolved;
  });
  if (new Set(roots).size !== roots.length) {
    throw new Error('INVALIDITY_ALLOWED_SOURCE_ROOTS 不得重复');
  }
  return roots;
}

function validateEnvironment(environment, values) {
  const definition = STACKS[environment];
  if (!definition) {
    throw new Error(`未知 PM2 隔离环境: ${environment}`);
  }

  const forbiddenPrefix = environment === 'test' ? 'INVALIDITY_PROD_' : 'INVALIDITY_TEST_';
  const forbiddenKeys = Object.keys(values).filter((key) => key.startsWith(forbiddenPrefix));
  if (forbiddenKeys.length > 0) {
    throw new Error(
      `${environment} 环境文件包含另一环境变量: ${forbiddenKeys.join(', ')}`,
    );
  }

  if (required(values, 'INVALIDITY_ENV') !== environment) {
    throw new Error(`INVALIDITY_ENV 必须严格等于 ${environment}`);
  }
  if (required(values, 'INVALIDITY_PORT') !== definition.expectedPort) {
    throw new Error(`${environment} 无效检索端口必须是 ${definition.expectedPort}`);
  }
  if (required(values, 'INVALIDITY_DATABASE_SCHEMA') !== definition.expectedSchema) {
    throw new Error(`${environment} schema 必须是 ${definition.expectedSchema}`);
  }

  required(values, 'INVALIDITY_DATABASE_URL');
  const artifactRoot = path.resolve(required(values, 'INVALIDITY_ARTIFACT_ROOT'));
  const artifactPath = artifactRoot.split(path.sep).join('/');
  if (
    artifactPath !== definition.expectedArtifactFragment &&
    !artifactPath.endsWith(definition.expectedArtifactFragment) &&
    !artifactPath.includes(`${definition.expectedArtifactFragment}/`)
  ) {
    throw new Error(
      `${environment} 工件目录必须包含 ${definition.expectedArtifactFragment}`,
    );
  }

  const apiValue = required(values, definition.apiVariable);
  const expectedApiValue = `http://127.0.0.1:${definition.expectedPort}`;
  const apiUrl = assertHttpUrl(apiValue, definition.apiVariable);
  if (
    apiValue !== expectedApiValue ||
    apiUrl.protocol !== 'http:' ||
    apiUrl.hostname !== '127.0.0.1' ||
    apiUrl.port !== definition.expectedPort ||
    !['', '/'].includes(apiUrl.pathname) ||
    apiUrl.username !== '' ||
    apiUrl.password !== '' ||
    apiUrl.search !== '' ||
    apiUrl.hash !== ''
  ) {
    throw new Error(
      `${definition.apiVariable} 必须严格指向 http://127.0.0.1:${definition.expectedPort}`,
    );
  }
  const apiToken = requiredToken(values, definition.apiTokenVariable);

  const module1Value = required(values, definition.module1Variable);
  const expectedModule1Value = `http://127.0.0.1:${definition.expectedModule1Port}/run`;
  const module1Url = assertHttpUrl(module1Value, definition.module1Variable);
  if (
    module1Value !== expectedModule1Value ||
    module1Url.protocol !== 'http:' ||
    module1Url.hostname !== '127.0.0.1' ||
    module1Url.port !== definition.expectedModule1Port ||
    module1Url.pathname !== '/run' ||
    module1Url.username !== '' ||
    module1Url.password !== '' ||
    module1Url.search !== '' ||
    module1Url.hash !== ''
  ) {
    throw new Error(
      `${definition.module1Variable} 必须严格指向 http://127.0.0.1:${definition.expectedModule1Port}/run`,
    );
  }

  if (environment === 'test') {
    if (required(values, 'INVALIDITY_PARSER_PORT') !== '5201') {
      throw new Error('测试解析兼容服务端口必须是 5201');
    }
    const parserToken = requiredToken(values, 'INVALIDITY_TEST_PARSER_TOKEN');
    if (parserToken === apiToken) {
      throw new Error('INVALIDITY_TEST_PARSER_TOKEN 必须与 INVALIDITY_TEST_API_TOKEN 不同');
    }
  } else {
    const module1Token = String(values.INVALIDITY_PROD_MODULE1_API_TOKEN || '').trim();
    const module1AuthMode = String(values.INVALIDITY_PROD_MODULE1_AUTH_MODE || '').trim();
    if (module1Token) {
      const validatedModule1Token = requiredToken(values, 'INVALIDITY_PROD_MODULE1_API_TOKEN');
      if (validatedModule1Token === apiToken) {
        throw new Error('INVALIDITY_PROD_MODULE1_API_TOKEN 必须与 INVALIDITY_PROD_API_TOKEN 不同');
      }
      if (module1AuthMode && module1AuthMode !== 'bearer') {
        throw new Error('配置正式模块一 token 时，INVALIDITY_PROD_MODULE1_AUTH_MODE 只能是 bearer');
      }
    } else if (module1AuthMode !== 'legacy_unauthenticated') {
      throw new Error(
        '正式模块一未配置独立 token 时，必须显式设置 INVALIDITY_PROD_MODULE1_AUTH_MODE=legacy_unauthenticated',
      );
    }
  }

  validateAllowedSourceRoots(
    required(values, 'INVALIDITY_ALLOWED_SOURCE_ROOTS'),
    environment,
    artifactRoot,
    definition.serviceDir,
  );

  required(values, `${definition.prefix}_PATENT_PROVIDER`);
  const patentProvider = required(values, `${definition.prefix}_PATENT_PROVIDER`)
    .toLowerCase()
    .replaceAll('-', '_');
  if (!['google_patents', 'epo_ops', 'patsnap'].includes(patentProvider)) {
    throw new Error(`${definition.prefix}_PATENT_PROVIDER 不是已批准的 live provider`);
  }
  const epoOpsKey = String(values[`${definition.prefix}_EPO_OPS_KEY`] || '').trim();
  const epoOpsSecret = String(values[`${definition.prefix}_EPO_OPS_SECRET`] || '').trim();
  if (Boolean(epoOpsKey) !== Boolean(epoOpsSecret)) {
    throw new Error(
      `${definition.prefix}_EPO_OPS_KEY 与 ${definition.prefix}_EPO_OPS_SECRET 必须同时配置`,
    );
  }
  if (patentProvider === 'epo_ops' && !epoOpsKey) {
    throw new Error(
      `${definition.prefix}_PATENT_PROVIDER=epo_ops 时，专利检索与取回必须配置当前环境的 EPO OPS 凭据`,
    );
  }
  validatePatsnapConfiguration(values, definition.prefix, patentProvider);
  if (patentProvider === 'patsnap' && !epoOpsKey) {
    throw new Error(
      `${definition.prefix}_PATENT_PROVIDER=patsnap 时，候选发现后的官方原文取回必须配置当前环境的 EPO OPS 凭据`,
    );
  }
  const nplProvider = required(values, `${definition.prefix}_NPL_PROVIDER`).toLowerCase();
  if (
    ![
      'arxiv',
      'arxiv_atom',
      'composite_npl',
      'arxiv_openalex_crossref',
      'arxiv_openalex_crossref_web',
    ].includes(nplProvider)
  ) {
    throw new Error(`${definition.prefix}_NPL_PROVIDER 不是已批准的 live provider 组合`);
  }
  assertHttpUrl(
    required(values, `${definition.prefix}_LLM_BASE_URL`),
    `${definition.prefix}_LLM_BASE_URL`,
  );
  required(values, `${definition.prefix}_LLM_API_KEY`);
  const model = required(values, `${definition.prefix}_LLM_MODEL`).toLowerCase();
  if (!model.startsWith('glm-4.6v')) {
    throw new Error(`${environment} 无效检索必须使用 glm-4.6v 系列多模态模型`);
  }

  for (const key of OPTIONAL_INVALIDITY_KEYS) {
    const value = String(values[key] || '').trim();
    if (value && (!/^\d+$/.test(value) || Number(value) <= 0)) {
      throw new Error(`${key} 必须是正整数`);
    }
  }
  const configuredMaxRounds = Number(values.INVALIDITY_MAX_ROUNDS || 5);
  if (configuredMaxRounds > 5) {
    throw new Error('INVALIDITY_MAX_ROUNDS 表示首轮后的 gap 检索次数，只能是 1..5');
  }
  const configuredWorkerConcurrency = Number(values.INVALIDITY_WORKER_CONCURRENCY || 2);
  if (configuredWorkerConcurrency > 4) {
    throw new Error('INVALIDITY_WORKER_CONCURRENCY 只能是 1..4');
  }

  return Object.freeze({ ...values, INVALIDITY_ARTIFACT_ROOT: artifactRoot });
}

function sha256Buffer(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function sha256File(filePath) {
  return sha256Buffer(fs.readFileSync(filePath));
}

function normalizeManifestPath(rawPath, label) {
  const value = String(rawPath || '').replace(/^\.\//, '');
  if (
    !value ||
    value.includes('\\') ||
    path.posix.isAbsolute(value) ||
    path.posix.normalize(value) !== value ||
    value === '..' ||
    value.startsWith('../')
  ) {
    throw new Error(`${label} 包含不安全路径: ${rawPath}`);
  }
  return value;
}

function parseHashManifest(filePath, label) {
  const rows = new Map();
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/).filter(Boolean);
  if (lines.length === 0) {
    throw new Error(`${label} 为空`);
  }
  for (const line of lines) {
    const match = line.match(/^([0-9a-f]{64})  (.+)$/);
    if (!match) {
      throw new Error(`${label} 含非法记录`);
    }
    const relativePath = normalizeManifestPath(match[2], label);
    if (rows.has(relativePath)) {
      throw new Error(`${label} 重复记录 ${relativePath}`);
    }
    rows.set(relativePath, match[1]);
  }
  return rows;
}

function parseReleaseMetadata(filePath) {
  const result = {};
  for (const line of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    if (!line) continue;
    const separator = line.indexOf('=');
    if (separator <= 0) {
      throw new Error('RELEASE_METADATA 含非法记录');
    }
    const key = line.slice(0, separator);
    if (Object.hasOwn(result, key)) {
      throw new Error(`RELEASE_METADATA 重复字段 ${key}`);
    }
    result[key] = line.slice(separator + 1);
  }
  return result;
}

function assertRegularReleaseFile(releasePath, relativePath, expectedHash, label) {
  const absolutePath = path.resolve(releasePath, relativePath);
  const releasePrefix = `${path.resolve(releasePath)}${path.sep}`;
  if (!absolutePath.startsWith(releasePrefix)) {
    throw new Error(`${label} 路径越出 release: ${relativePath}`);
  }
  if (!fs.existsSync(absolutePath)) {
    throw new Error(`${label} 缺少文件: ${relativePath}`);
  }
  const status = fs.lstatSync(absolutePath);
  if (status.isSymbolicLink() || !status.isFile()) {
    throw new Error(`${label} 只能引用普通文件: ${relativePath}`);
  }
  if ((status.mode & 0o222) !== 0) {
    throw new Error(`${label} 引用的正式文件必须只读: ${relativePath}`);
  }
  if (sha256File(absolutePath) !== expectedHash) {
    throw new Error(`${label} 哈希不匹配: ${relativePath}`);
  }
}

function assertReadOnlyReleaseDirectory(directory, label) {
  try {
    fs.accessSync(directory, fs.constants.W_OK);
  } catch {
    return;
  }
  throw new Error(`${label}必须对当前运行用户只读`);
}

function collectReleaseSourceFiles(releasePath) {
  const result = [];
  const ignoredDirectories = new Set([
    '.venv',
    '.data',
    '.logs',
    '.pytest_cache',
    '__pycache__',
    '.release-rules',
  ]);
  const controlFiles = new Set([
    'RELEASE_MANIFEST.sha256',
    'RULES_MANIFEST.sha256',
    'RELEASE_METADATA',
  ]);

  function visit(directory, relativeDirectory = '') {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const relativePath = relativeDirectory
        ? path.posix.join(relativeDirectory, entry.name)
        : entry.name;
      const absolutePath = path.join(directory, entry.name);
      const status = fs.lstatSync(absolutePath);
      if (status.isSymbolicLink()) {
        throw new Error(`正式 release 内不允许符号链接: ${relativePath}`);
      }
      if (status.isDirectory()) {
        assertReadOnlyReleaseDirectory(absolutePath, `正式 release 目录: ${relativePath}`);
        if (!ignoredDirectories.has(entry.name)) {
          visit(absolutePath, relativePath);
        }
        continue;
      }
      if (!status.isFile()) {
        throw new Error(`正式 release 内只允许普通文件/目录: ${relativePath}`);
      }
      if (!controlFiles.has(relativePath)) {
        result.push(relativePath);
      }
    }
  }

  visit(releasePath);
  return result.sort();
}

function verifyProductionRelease(releasePath, releaseId) {
  const releaseStatus = fs.lstatSync(releasePath);
  if (releaseStatus.isSymbolicLink() || !releaseStatus.isDirectory()) {
    throw new Error('正式 release 必须是普通目录');
  }
  assertReadOnlyReleaseDirectory(releasePath, '正式 release 根目录');
  const manifestPath = path.join(releasePath, 'RELEASE_MANIFEST.sha256');
  const rulesManifestPath = path.join(releasePath, 'RULES_MANIFEST.sha256');
  const metadataPath = path.join(releasePath, 'RELEASE_METADATA');
  for (const [controlPath, label] of [
    [manifestPath, 'RELEASE_MANIFEST.sha256'],
    [rulesManifestPath, 'RULES_MANIFEST.sha256'],
    [metadataPath, 'RELEASE_METADATA'],
  ]) {
    const status = fs.lstatSync(controlPath);
    if (status.isSymbolicLink() || !status.isFile()) {
      throw new Error(`${label} 必须是普通文件`);
    }
    if ((status.mode & 0o222) !== 0) {
      throw new Error(`${label} 必须只读`);
    }
  }
  const sourceManifest = parseHashManifest(manifestPath, 'RELEASE_MANIFEST.sha256');
  const actualSourceFiles = collectReleaseSourceFiles(releasePath);
  const expectedSourceFiles = [...sourceManifest.keys()].sort();
  if (JSON.stringify(actualSourceFiles) !== JSON.stringify(expectedSourceFiles)) {
    throw new Error('正式 release 文件集合与 RELEASE_MANIFEST.sha256 不一致');
  }
  for (const [relativePath, expectedHash] of sourceManifest) {
    assertRegularReleaseFile(
      releasePath,
      relativePath,
      expectedHash,
      'RELEASE_MANIFEST.sha256',
    );
  }

  const expectedRulePaths = [
    '.release-rules/PROJECT_CHARTER.md',
    '.release-rules/WORKFLOW_SPEC.md',
  ];
  const rulesManifest = parseHashManifest(rulesManifestPath, 'RULES_MANIFEST.sha256');
  if (
    JSON.stringify([...rulesManifest.keys()].sort()) !==
    JSON.stringify(expectedRulePaths.slice().sort())
  ) {
    throw new Error('RULES_MANIFEST.sha256 必须精确包含两份冻结规则正文');
  }
  for (const [relativePath, expectedHash] of rulesManifest) {
    assertRegularReleaseFile(
      releasePath,
      relativePath,
      expectedHash,
      'RULES_MANIFEST.sha256',
    );
  }

  const metadata = parseReleaseMetadata(metadataPath);
  const requiredMetadata = [
    'release_id',
    'source_digest',
    'charter_sha256',
    'workflow_spec_sha256',
    'promotion_digest',
    'promoted_at_utc',
    'previous_release',
  ];
  for (const key of requiredMetadata) {
    if (!metadata[key]) {
      throw new Error(`RELEASE_METADATA 缺少 ${key}`);
    }
  }
  if (metadata.release_id !== releaseId) {
    throw new Error('RELEASE_METADATA 的 release_id 与 CURRENT_RELEASE 不一致');
  }
  const sourceDigest = sha256File(manifestPath);
  const charterDigest = rulesManifest.get('.release-rules/PROJECT_CHARTER.md');
  const workflowDigest = rulesManifest.get('.release-rules/WORKFLOW_SPEC.md');
  if (
    metadata.source_digest !== sourceDigest ||
    metadata.charter_sha256 !== charterDigest ||
    metadata.workflow_spec_sha256 !== workflowDigest
  ) {
    throw new Error('RELEASE_METADATA 与代码/规则 manifest 摘要不一致');
  }
  const promotionDigest = sha256Buffer(
    `${sourceDigest}\n${charterDigest}\n${workflowDigest}\n`,
  );
  if (metadata.promotion_digest !== promotionDigest) {
    throw new Error('RELEASE_METADATA 的 promotion_digest 不一致');
  }
  return { metadata, sourceManifest, rulesManifest };
}

function resolveProductionRelease() {
  const productDir = STACKS.prod.serviceDir;
  const pointerPath = path.join(productDir, 'CURRENT_RELEASE');
  if (!fs.existsSync(pointerPath)) {
    throw new Error(
      `正式快照尚未激活：缺少 ${pointerPath}；请先执行测试目录的显式 promotion`,
    );
  }
  if (fs.lstatSync(pointerPath).isSymbolicLink()) {
    throw new Error('CURRENT_RELEASE 不允许是符号链接');
  }

  const releaseId = fs.readFileSync(pointerPath, 'utf8').trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(releaseId)) {
    throw new Error(`CURRENT_RELEASE 内容不合法: ${releaseId}`);
  }

  const releasesRoot = path.join(productDir, 'releases');
  const releasePath = path.resolve(releasesRoot, releaseId);
  const expectedPrefix = `${path.resolve(releasesRoot)}${path.sep}`;
  if (!releasePath.startsWith(expectedPrefix)) {
    throw new Error('CURRENT_RELEASE 越出正式 releases 目录');
  }
  if (!fs.existsSync(releasePath) || !fs.statSync(releasePath).isDirectory()) {
    throw new Error(`正式 release 不存在: ${releasePath}`);
  }
  if (fs.lstatSync(releasePath).isSymbolicLink()) {
    throw new Error('正式 release 不允许是符号链接');
  }
  if (fs.existsSync(path.join(releasePath, 'PROMOTION_FAILED'))) {
    throw new Error(`正式 release 带有 PROMOTION_FAILED 标记，拒绝启动: ${releasePath}`);
  }
  for (const relativePath of [
    'pyproject.toml',
    'uv.lock',
    'src/invalidity',
    'scripts/http_run.sh',
    'RELEASE_MANIFEST.sha256',
    'RULES_MANIFEST.sha256',
    'RELEASE_METADATA',
    '.release-rules/PROJECT_CHARTER.md',
    '.release-rules/WORKFLOW_SPEC.md',
  ]) {
    if (!fs.existsSync(path.join(releasePath, relativePath))) {
      throw new Error(`正式 release 缺少 ${relativePath}: ${releasePath}`);
    }
  }
  verifyProductionRelease(releasePath, releaseId);
  return releasePath;
}

function invalidityChildEnvironment(environment, values) {
  const definition = STACKS[environment];
  const requiredKeys = [
    'INVALIDITY_ENV',
    'INVALIDITY_PORT',
    'INVALIDITY_DATABASE_URL',
    'INVALIDITY_DATABASE_SCHEMA',
    'INVALIDITY_ARTIFACT_ROOT',
    'INVALIDITY_ALLOWED_SOURCE_ROOTS',
    definition.apiVariable,
    definition.apiTokenVariable,
    definition.module1Variable,
    `${definition.prefix}_PATENT_PROVIDER`,
    `${definition.prefix}_PATSNAP_API_KEY`,
    `${definition.prefix}_PATSNAP_BASE_URL`,
    `${definition.prefix}_PATSNAP_COUNT_PATH`,
    `${definition.prefix}_PATSNAP_SEARCH_PATH`,
    `${definition.prefix}_EPO_OPS_KEY`,
    `${definition.prefix}_EPO_OPS_SECRET`,
    `${definition.prefix}_NPL_PROVIDER`,
    `${definition.prefix}_LLM_BASE_URL`,
    `${definition.prefix}_LLM_API_KEY`,
    `${definition.prefix}_LLM_MODEL`,
    ...(environment === 'test'
      ? ['INVALIDITY_TEST_PARSER_TOKEN']
      : ['INVALIDITY_PROD_MODULE1_API_TOKEN', 'INVALIDITY_PROD_MODULE1_AUTH_MODE']),
    ...OPTIONAL_INVALIDITY_KEYS,
  ];
  const child = {
    PYTHONUNBUFFERED: '1',
    PYTHONDONTWRITEBYTECODE: '1',
  };
  for (const key of requiredKeys) {
    if (Object.prototype.hasOwnProperty.call(values, key) && values[key] !== '') {
      child[key] = values[key];
    }
  }
  const oppositePrefix = environment === 'test' ? 'INVALIDITY_PROD' : 'INVALIDITY_TEST';
  for (const suffix of [
    'API_URL',
    'API_TOKEN',
    'MODULE1_API_URL',
    'PATENT_PROVIDER',
    'PATSNAP_API_KEY',
    'PATSNAP_BASE_URL',
    'PATSNAP_COUNT_PATH',
    'PATSNAP_SEARCH_PATH',
    'EPO_OPS_KEY',
    'EPO_OPS_SECRET',
    'NPL_PROVIDER',
    'LLM_BASE_URL',
    'LLM_API_KEY',
    'LLM_MODEL',
  ]) {
    // Explicit empty values prevent python-dotenv from importing the other
    // environment out of a shared legacy .env.local file.
    child[`${oppositePrefix}_${suffix}`] = '';
  }
  return child;
}

function parserChildEnvironment(values) {
  const keys = [
    'INVALIDITY_ENV',
    'INVALIDITY_ARTIFACT_ROOT',
    'INVALIDITY_ALLOWED_SOURCE_ROOTS',
    'INVALIDITY_TEST_PARSER_TOKEN',
    ...OPTIONAL_INVALIDITY_KEYS.filter((key) => key.startsWith('INVALIDITY_SOURCE_')),
    'INVALIDITY_IMAGE_MAX_BYTES',
  ];
  const child = {
    PYTHONUNBUFFERED: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    INVALIDITY_PARSER_PORT: '5201',
  };
  for (const key of keys) {
    if (Object.prototype.hasOwnProperty.call(values, key) && values[key] !== '') {
      child[key] = values[key];
    }
  }
  return child;
}

function logPaths(environment) {
  const serviceDir = STACKS[environment].serviceDir;
  return {
    output: path.join(serviceDir, '.logs', 'pm2-output.log'),
    error: path.join(serviceDir, '.logs', 'pm2-error.log'),
  };
}

function buildInvalidityApp(environment, values, cwd) {
  const logs = logPaths(environment);
  const serviceDir = STACKS[environment].serviceDir;
  const virtualEnvironment =
    environment === 'test'
      ? path.join(cwd, '.venv')
      : path.join(serviceDir, '.data', 'venvs', path.basename(cwd));
  return {
    name: `patent-invalidity-${environment}-api`,
    cwd,
    script: './scripts/http_run.sh',
    interpreter: '/bin/bash',
    instances: 1,
    exec_mode: 'fork',
    autorestart: true,
    max_restarts: 8,
    min_uptime: '10s',
    restart_delay: 3000,
    kill_timeout: 15000,
    merge_logs: true,
    out_file: logs.output,
    error_file: logs.error,
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    env: {
      ...invalidityChildEnvironment(environment, values),
      UV_CACHE_DIR: path.join(serviceDir, '.data', 'uv-cache'),
      UV_PROJECT_ENVIRONMENT: virtualEnvironment,
    },
  };
}

function buildTestModule1App(values) {
  const cwd = STACKS.test.serviceDir;
  const entrypoint = path.join(cwd, 'src', 'invalidity', 'parser_service.py');
  if (!fs.existsSync(entrypoint)) {
    throw new Error(`测试解析兼容服务入口不存在: ${entrypoint}`);
  }
  return {
    name: 'patent-invalidity-test-module1',
    cwd,
    script: './scripts/parser_http_run.sh',
    interpreter: '/bin/bash',
    instances: 1,
    exec_mode: 'fork',
    autorestart: true,
    max_restarts: 8,
    min_uptime: '10s',
    restart_delay: 3000,
    kill_timeout: 15000,
    merge_logs: true,
    out_file: path.join(STACKS.test.serviceDir, '.logs', 'module1-output.log'),
    error_file: path.join(STACKS.test.serviceDir, '.logs', 'module1-error.log'),
    log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
    env: {
      ...parserChildEnvironment(values),
      UV_CACHE_DIR: path.join(cwd, '.data', 'uv-cache'),
      UV_PROJECT_ENVIRONMENT: path.join(cwd, '.venv'),
    },
  };
}

function buildStack(environment) {
  const definition = STACKS[environment];
  if (!definition) {
    throw new Error(`未知 PM2 隔离环境: ${environment}`);
  }
  const envFile = path.join(definition.serviceDir, definition.envFileName);
  const values = validateEnvironment(environment, parseEnvFile(envFile));
  const cwd = environment === 'test' ? definition.serviceDir : resolveProductionRelease();
  const apps = [buildInvalidityApp(environment, values, cwd)];
  if (environment === 'test') {
    apps.unshift(buildTestModule1App(values));
  }
  return { apps };
}

module.exports = {
  PROJECT_ROOT,
  STACKS,
  buildStack,
  invalidityChildEnvironment,
  parseEnvFile,
  parserChildEnvironment,
  resolveProductionRelease,
  validateEnvironment,
  verifyProductionRelease,
};
