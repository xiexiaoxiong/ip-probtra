#!/usr/bin/env node

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(SCRIPT_DIR, '../..');
const PORTAL_ENV = path.join(PROJECT_ROOT, 'IP-protral', '.env.local');

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function parseEnvironmentFile(filePath) {
  if (!fs.existsSync(filePath)) fail(`缺少环境文件: ${filePath}`);
  const status = fs.lstatSync(filePath);
  if (status.isSymbolicLink() || !status.isFile()) {
    fail(`环境文件必须是普通文件且不能是符号链接: ${filePath}`);
  }
  if ((status.mode & 0o077) !== 0) {
    fail(`环境文件权限必须为 600 或更严格: ${filePath}`);
  }
  const values = {};
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/);
  lines.forEach((line, index) => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return;
    const separator = trimmed.indexOf('=');
    if (separator <= 0) fail(`${filePath}:${index + 1} 不是 KEY=VALUE`);
    const key = trimmed.slice(0, separator).trim();
    let value = trimmed.slice(separator + 1).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      fail(`${filePath}:${index + 1} 变量名不合法`);
    }
    if (Object.hasOwn(values, key)) fail(`${filePath} 重复定义 ${key}`);
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  });
  return { values, lines };
}

function required(values, key) {
  const value = String(values[key] || '').trim();
  if (!value) fail(`共享环境缺少 ${key}`);
  if (/[\r\n\0]/.test(value)) fail(`${key} 包含不安全换行或 NUL`);
  return value;
}

function randomToken(label) {
  return `${label}_${crypto.randomBytes(32).toString('base64url')}`;
}

function assertSafeTarget(filePath) {
  if (!fs.existsSync(filePath)) return;
  const status = fs.lstatSync(filePath);
  if (status.isSymbolicLink() || !status.isFile()) {
    fail(`目标环境文件必须是普通文件且不能是符号链接: ${filePath}`);
  }
}

function atomicPrivateWrite(filePath, content) {
  assertSafeTarget(filePath);
  fs.mkdirSync(path.dirname(filePath), { recursive: true, mode: 0o700 });
  const temporary = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`,
  );
  fs.writeFileSync(temporary, content, { encoding: 'utf8', mode: 0o600, flag: 'wx' });
  fs.renameSync(temporary, filePath);
  fs.chmodSync(filePath, 0o600);
}

function serialize(values) {
  return `${Object.entries(values)
    .map(([key, value]) => {
      const text = String(value);
      if (/[\r\n\0]/.test(text)) fail(`${key} 包含不安全换行或 NUL`);
      return `${key}=${text}`;
    })
    .join('\n')}\n`;
}

function upsertPortalValues(valuesToSet) {
  const parsed = parseEnvironmentFile(PORTAL_ENV);
  const remaining = new Map(Object.entries(valuesToSet));
  const output = parsed.lines.map((line) => {
    const match = line.match(/^\s*([A-Za-z_][A-Za-z0-9_]*)=/);
    if (!match || !remaining.has(match[1])) return line;
    const value = remaining.get(match[1]);
    remaining.delete(match[1]);
    return `${match[1]}=${value}`;
  });
  if (remaining.size > 0) {
    while (output.length > 0 && output.at(-1) === '') output.pop();
    output.push('', '# Isolated invalidity-search routing (generated locally; no fallback).');
    for (const [key, value] of remaining) output.push(`${key}=${value}`);
  }
  atomicPrivateWrite(PORTAL_ENV, `${output.join('\n').replace(/\n*$/, '')}\n`);
}

function ensurePrivateDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const status = fs.lstatSync(directory);
  if (status.isSymbolicLink() || !status.isDirectory()) {
    fail(`隔离目录必须是普通目录且不能是符号链接: ${directory}`);
  }
  fs.chmodSync(directory, 0o700);
}

function existingOrNewToken(existing, key, label, forbidden = new Set()) {
  const candidate = String(existing[key] || '').trim() || randomToken(label);
  if (candidate.length < 16 || forbidden.has(candidate)) {
    return existingOrNewToken({}, key, label, forbidden);
  }
  return candidate;
}

function buildSharedCredentials(source) {
  const databaseUrl = required(source, 'PGDATABASE_URL');
  if (!/^postgres(?:ql)?:\/\//i.test(databaseUrl)) {
    fail('PGDATABASE_URL 必须是 PostgreSQL URL');
  }
  const llmBaseUrl = required(source, 'LOCAL_LLM_BASE_URL');
  if (!/^https?:\/\//i.test(llmBaseUrl)) fail('LOCAL_LLM_BASE_URL 必须是 HTTP(S) URL');
  const llmApiKey = required(source, 'LOCAL_LLM_API_KEY');
  // The invalidity workflow has a user-confirmed multimodal requirement and
  // must not inherit an older text/default model name from the shared Portal.
  const llmModel = 'glm-4.6v';
  return { databaseUrl, llmBaseUrl, llmApiKey, llmModel };
}

function bootstrapTest(shared) {
  const serviceRoot = path.join(PROJECT_ROOT, '5-invalidity-search-test');
  const target = path.join(serviceRoot, '.env.test.local');
  const existing = fs.existsSync(target) ? parseEnvironmentFile(target).values : {};
  const apiToken = existingOrNewToken(existing, 'INVALIDITY_TEST_API_TOKEN', 'invalidity_test_api');
  const parserToken = existingOrNewToken(
    existing,
    'INVALIDITY_TEST_PARSER_TOKEN',
    'invalidity_test_parser',
    new Set([apiToken]),
  );
  const artifactRoot = path.join(serviceRoot, '.data', 'invalidity', 'test');
  const uploadRoot = path.join(serviceRoot, '.data', 'uploads', 'test');
  const patentProvider = 'patsnap';
  const epoOpsKey = String(existing.INVALIDITY_TEST_EPO_OPS_KEY || '').trim();
  const epoOpsSecret = String(existing.INVALIDITY_TEST_EPO_OPS_SECRET || '').trim();
  const patsnapApiKey = String(existing.INVALIDITY_TEST_PATSNAP_API_KEY || '').trim();
  const patsnapBaseUrl = String(
    existing.INVALIDITY_TEST_PATSNAP_BASE_URL || 'https://connect.zhihuiya.com',
  ).trim();
  const patsnapCountPath = String(
    existing.INVALIDITY_TEST_PATSNAP_COUNT_PATH || '/search/patent/query-search-count/v2',
  ).trim();
  const patsnapSearchPath = String(
    existing.INVALIDITY_TEST_PATSNAP_SEARCH_PATH || '/search/patent/query-search-patent/v2',
  ).trim();
  ensurePrivateDirectory(artifactRoot);
  ensurePrivateDirectory(uploadRoot);
  atomicPrivateWrite(
    target,
    serialize({
      INVALIDITY_ENV: 'test',
      INVALIDITY_PORT: '5209',
      INVALIDITY_DATABASE_URL: shared.databaseUrl,
      INVALIDITY_DATABASE_SCHEMA: 'invalidity_test',
      INVALIDITY_ARTIFACT_ROOT: artifactRoot,
      INVALIDITY_ALLOWED_SOURCE_ROOTS: uploadRoot,
      INVALIDITY_PARSER_PORT: '5201',
      INVALIDITY_TEST_API_URL: 'http://127.0.0.1:5209',
      INVALIDITY_TEST_API_TOKEN: apiToken,
      INVALIDITY_TEST_PARSER_TOKEN: parserToken,
      INVALIDITY_TEST_MODULE1_API_URL: 'http://127.0.0.1:5201/run',
      INVALIDITY_TEST_PATENT_PROVIDER: patentProvider,
      INVALIDITY_TEST_PATSNAP_API_KEY: patsnapApiKey,
      INVALIDITY_TEST_PATSNAP_BASE_URL: patsnapBaseUrl,
      INVALIDITY_TEST_PATSNAP_COUNT_PATH: patsnapCountPath,
      INVALIDITY_TEST_PATSNAP_SEARCH_PATH: patsnapSearchPath,
      INVALIDITY_TEST_EPO_OPS_KEY: epoOpsKey,
      INVALIDITY_TEST_EPO_OPS_SECRET: epoOpsSecret,
      INVALIDITY_TEST_NPL_PROVIDER: 'arxiv_openalex_crossref_web',
      INVALIDITY_TEST_LLM_BASE_URL: shared.llmBaseUrl,
      INVALIDITY_TEST_LLM_API_KEY: shared.llmApiKey,
      INVALIDITY_TEST_LLM_MODEL: shared.llmModel,
      INVALIDITY_LLM_TIMEOUT_SECONDS: '360',
      INVALIDITY_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS: '180',
      INVALIDITY_LLM_DIRECT_PROBE_TIMEOUT_SECONDS: '5',
      INVALIDITY_MAX_ROUNDS: '5',
      INVALIDITY_MAX_CANDIDATES_PER_QUERY: '5',
      INVALIDITY_WORKER_CONCURRENCY: '2',
      INVALIDITY_WORKER_POLL_SECONDS: '2',
      INVALIDITY_JOB_LEASE_SECONDS: '900',
      INVALIDITY_SOURCE_FETCH_TIMEOUT_SECONDS: '30',
      INVALIDITY_SOURCE_MAX_BYTES: '52428800',
      INVALIDITY_IMAGE_MAX_BYTES: '15728640',
      INVALIDITY_SOURCE_MAX_REDIRECTS: '5',
    }),
  );
  upsertPortalValues({
    INVALIDITY_TEST_API_URL: 'http://127.0.0.1:5209',
    INVALIDITY_TEST_API_TOKEN: apiToken,
    INVALIDITY_TEST_UPLOAD_ROOT: uploadRoot,
  });
  process.stdout.write(`[test] 已生成隔离配置: ${target}\n`);
  process.stdout.write('[test] 已生成不同的 5209 API token 与 5201 parser token（值未输出）\n');
}

function bootstrapProd(shared, { reuseTestProviderCredentials = false } = {}) {
  const serviceRoot = path.join(PROJECT_ROOT, '5-invalidity-search-prod');
  const target = path.join(serviceRoot, '.env.prod.local');
  const existing = fs.existsSync(target) ? parseEnvironmentFile(target).values : {};
  const testTarget = path.join(PROJECT_ROOT, '5-invalidity-search-test', '.env.test.local');
  const testValues = fs.existsSync(testTarget)
    ? parseEnvironmentFile(testTarget).values
    : {};
  const forbidden = new Set(
    [testValues.INVALIDITY_TEST_API_TOKEN, testValues.INVALIDITY_TEST_PARSER_TOKEN].filter(Boolean),
  );
  const apiToken = existingOrNewToken(
    existing,
    'INVALIDITY_PROD_API_TOKEN',
    'invalidity_prod_api',
    forbidden,
  );
  const artifactRoot = path.join(serviceRoot, '.data', 'invalidity', 'prod');
  const uploadRoot = path.join(serviceRoot, '.data', 'uploads', 'prod');
  const patentProvider = 'patsnap';
  const epoOpsKey = reuseTestProviderCredentials
    ? required(testValues, 'INVALIDITY_TEST_EPO_OPS_KEY')
    : String(existing.INVALIDITY_PROD_EPO_OPS_KEY || '').trim();
  const epoOpsSecret = reuseTestProviderCredentials
    ? required(testValues, 'INVALIDITY_TEST_EPO_OPS_SECRET')
    : String(existing.INVALIDITY_PROD_EPO_OPS_SECRET || '').trim();
  const patsnapApiKey = reuseTestProviderCredentials
    ? required(testValues, 'INVALIDITY_TEST_PATSNAP_API_KEY')
    : String(existing.INVALIDITY_PROD_PATSNAP_API_KEY || '').trim();
  const patsnapBaseUrl = String(
    (reuseTestProviderCredentials && testValues.INVALIDITY_TEST_PATSNAP_BASE_URL) ||
      existing.INVALIDITY_PROD_PATSNAP_BASE_URL ||
      'https://connect.zhihuiya.com',
  ).trim();
  const patsnapCountPath = String(
    (reuseTestProviderCredentials && testValues.INVALIDITY_TEST_PATSNAP_COUNT_PATH) ||
      existing.INVALIDITY_PROD_PATSNAP_COUNT_PATH ||
      '/search/patent/query-search-count/v2',
  ).trim();
  const patsnapSearchPath = String(
    (reuseTestProviderCredentials && testValues.INVALIDITY_TEST_PATSNAP_SEARCH_PATH) ||
      existing.INVALIDITY_PROD_PATSNAP_SEARCH_PATH ||
      '/search/patent/query-search-patent/v2',
  ).trim();
  ensurePrivateDirectory(artifactRoot);
  ensurePrivateDirectory(uploadRoot);
  atomicPrivateWrite(
    target,
    serialize({
      INVALIDITY_ENV: 'prod',
      INVALIDITY_PORT: '5109',
      INVALIDITY_DATABASE_URL: shared.databaseUrl,
      INVALIDITY_DATABASE_SCHEMA: 'invalidity_prod',
      INVALIDITY_ARTIFACT_ROOT: artifactRoot,
      INVALIDITY_ALLOWED_SOURCE_ROOTS: uploadRoot,
      INVALIDITY_PROD_API_URL: 'http://127.0.0.1:5109',
      INVALIDITY_PROD_API_TOKEN: apiToken,
      INVALIDITY_PROD_MODULE1_API_URL: 'http://127.0.0.1:5101/run',
      INVALIDITY_PROD_MODULE1_API_TOKEN: '',
      INVALIDITY_PROD_MODULE1_AUTH_MODE: 'legacy_unauthenticated',
      INVALIDITY_PROD_PATENT_PROVIDER: patentProvider,
      INVALIDITY_PROD_PATSNAP_API_KEY: patsnapApiKey,
      INVALIDITY_PROD_PATSNAP_BASE_URL: patsnapBaseUrl,
      INVALIDITY_PROD_PATSNAP_COUNT_PATH: patsnapCountPath,
      INVALIDITY_PROD_PATSNAP_SEARCH_PATH: patsnapSearchPath,
      INVALIDITY_PROD_EPO_OPS_KEY: epoOpsKey,
      INVALIDITY_PROD_EPO_OPS_SECRET: epoOpsSecret,
      INVALIDITY_PROD_NPL_PROVIDER: 'arxiv_openalex_crossref_web',
      INVALIDITY_PROD_LLM_BASE_URL: shared.llmBaseUrl,
      INVALIDITY_PROD_LLM_API_KEY: shared.llmApiKey,
      INVALIDITY_PROD_LLM_MODEL: shared.llmModel,
      INVALIDITY_LLM_TIMEOUT_SECONDS: '360',
      INVALIDITY_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS: '180',
      INVALIDITY_LLM_DIRECT_PROBE_TIMEOUT_SECONDS: '5',
      INVALIDITY_MAX_ROUNDS: '5',
      INVALIDITY_MAX_CANDIDATES_PER_QUERY: '5',
      INVALIDITY_WORKER_CONCURRENCY: '2',
      INVALIDITY_WORKER_POLL_SECONDS: '2',
      INVALIDITY_JOB_LEASE_SECONDS: '900',
      INVALIDITY_SOURCE_FETCH_TIMEOUT_SECONDS: '30',
      INVALIDITY_SOURCE_MAX_BYTES: '52428800',
      INVALIDITY_IMAGE_MAX_BYTES: '15728640',
      INVALIDITY_SOURCE_MAX_REDIRECTS: '5',
    }),
  );
  upsertPortalValues({
    INVALIDITY_PROD_API_URL: 'http://127.0.0.1:5109',
    INVALIDITY_PROD_API_TOKEN: apiToken,
    INVALIDITY_PROD_UPLOAD_ROOT: uploadRoot,
  });
  process.stdout.write(`[prod] 已生成隔离配置: ${target}\n`);
  process.stdout.write('[prod] API token 与测试 token 不同（值未输出）\n');
  if (reuseTestProviderCredentials) {
    process.stdout.write(
      '[prod] 已按显式授权把测试 provider 凭据值写入正式前缀变量（值未输出；运行时不回落测试变量）\n',
    );
  }
}

const requested = process.argv[2] || '';
const options = new Set(process.argv.slice(3));
const allowedReuseFlag = '--reuse-test-provider-credentials';
if (
  !['test', 'prod'].includes(requested) ||
  options.size !== process.argv.length - 3 ||
  [...options].some((option) => option !== allowedReuseFlag) ||
  (requested !== 'prod' && options.has(allowedReuseFlag))
) {
  fail(
    '用法: node ops/pm2/bootstrap-local-isolated-env.mjs <test|prod> [--reuse-test-provider-credentials（仅 prod，须获显式授权）]',
  );
}
const portal = parseEnvironmentFile(PORTAL_ENV).values;
const shared = buildSharedCredentials(portal);
if (requested === 'test') bootstrapTest(shared);
if (requested === 'prod') {
  bootstrapProd(shared, {
    reuseTestProviderCredentials: options.has(allowedReuseFlag),
  });
}
process.stdout.write(
  `[${requested}] 数据库/GLM 上游凭据来自受保护的 Portal 环境；schema、目录和本地服务 token 已隔离。\n`,
);
