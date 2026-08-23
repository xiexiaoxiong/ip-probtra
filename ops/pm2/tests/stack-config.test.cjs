'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  STACKS,
  invalidityChildEnvironment,
  parserChildEnvironment,
  validateEnvironment,
  verifyProductionRelease,
} = require('../lib/stack-config.cjs');

function sha256(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function createReleaseFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'invalidity-release-'));
  const releaseId = 'release-under-test';
  const release = path.join(root, releaseId);
  fs.mkdirSync(path.join(release, '.release-rules'), { recursive: true });
  const source = Buffer.from('print("immutable")\n');
  fs.writeFileSync(path.join(release, 'app.py'), source);
  const releaseManifest = `${sha256(source)}  ./app.py\n`;
  fs.writeFileSync(path.join(release, 'RELEASE_MANIFEST.sha256'), releaseManifest);
  const charter = Buffer.from('# charter frozen\n');
  const workflow = Buffer.from('# workflow frozen\n');
  fs.writeFileSync(path.join(release, '.release-rules', 'PROJECT_CHARTER.md'), charter);
  fs.writeFileSync(path.join(release, '.release-rules', 'WORKFLOW_SPEC.md'), workflow);
  fs.writeFileSync(
    path.join(release, 'RULES_MANIFEST.sha256'),
    [
      `${sha256(charter)}  .release-rules/PROJECT_CHARTER.md`,
      `${sha256(workflow)}  .release-rules/WORKFLOW_SPEC.md`,
      '',
    ].join('\n'),
  );
  const sourceDigest = sha256(Buffer.from(releaseManifest));
  const promotionDigest = sha256(
    Buffer.from(`${sourceDigest}\n${sha256(charter)}\n${sha256(workflow)}\n`),
  );
  fs.writeFileSync(
    path.join(release, 'RELEASE_METADATA'),
    [
      `release_id=${releaseId}`,
      `source_digest=${sourceDigest}`,
      `charter_sha256=${sha256(charter)}`,
      `workflow_spec_sha256=${sha256(workflow)}`,
      `promotion_digest=${promotionDigest}`,
      'promoted_at_utc=2026-07-19T00:00:00Z',
      'previous_release=NONE',
      '',
    ].join('\n'),
  );
  for (const file of [
    'app.py',
    'RELEASE_MANIFEST.sha256',
    'RULES_MANIFEST.sha256',
    'RELEASE_METADATA',
    path.join('.release-rules', 'PROJECT_CHARTER.md'),
    path.join('.release-rules', 'WORKFLOW_SPEC.md'),
  ]) {
    fs.chmodSync(path.join(release, file), 0o444);
  }
  fs.chmodSync(path.join(release, '.release-rules'), 0o555);
  fs.chmodSync(release, 0o555);
  return { root, release, releaseId };
}

function removeReleaseFixture(fixture) {
  if (fs.existsSync(fixture.release)) {
    fs.chmodSync(fixture.release, 0o755);
    const rulesDirectory = path.join(fixture.release, '.release-rules');
    if (fs.existsSync(rulesDirectory)) {
      fs.chmodSync(rulesDirectory, 0o755);
    }
  }
  fs.rmSync(fixture.root, { recursive: true, force: true });
}

function testValues() {
  return {
    INVALIDITY_ENV: 'test',
    INVALIDITY_PORT: '5209',
    INVALIDITY_DATABASE_URL: 'postgresql://invalidity_test:secret@127.0.0.1/patent',
    INVALIDITY_DATABASE_SCHEMA: 'invalidity_test',
    INVALIDITY_ARTIFACT_ROOT: path.join(STACKS.test.serviceDir, '.data', 'invalidity', 'test'),
    INVALIDITY_ALLOWED_SOURCE_ROOTS: path.join(STACKS.test.serviceDir, '.data', 'uploads', 'test'),
    INVALIDITY_PARSER_PORT: '5201',
    INVALIDITY_TEST_API_URL: 'http://127.0.0.1:5209',
    INVALIDITY_TEST_API_TOKEN: 'test-api-token-000001',
    INVALIDITY_TEST_PARSER_TOKEN: 'test-parser-token-001',
    INVALIDITY_TEST_MODULE1_API_URL: 'http://127.0.0.1:5201/run',
    INVALIDITY_TEST_PATENT_PROVIDER: 'google_patents',
    INVALIDITY_TEST_NPL_PROVIDER: 'arxiv_openalex_crossref',
    INVALIDITY_TEST_LLM_BASE_URL: 'https://open.bigmodel.cn/api/paas/v4',
    INVALIDITY_TEST_LLM_API_KEY: 'test-llm-key-0000001',
    INVALIDITY_TEST_LLM_MODEL: 'glm-4.6v',
    INVALIDITY_MAX_ROUNDS: '5',
    INVALIDITY_SOURCE_MAX_BYTES: '52428800',
  };
}

function prodValues() {
  return {
    INVALIDITY_ENV: 'prod',
    INVALIDITY_PORT: '5109',
    INVALIDITY_DATABASE_URL: 'postgresql://invalidity_prod:secret@127.0.0.1/patent',
    INVALIDITY_DATABASE_SCHEMA: 'invalidity_prod',
    INVALIDITY_ARTIFACT_ROOT: path.join(STACKS.prod.serviceDir, '.data', 'invalidity', 'prod'),
    INVALIDITY_ALLOWED_SOURCE_ROOTS: path.join(STACKS.prod.serviceDir, '.data', 'uploads', 'prod'),
    INVALIDITY_PROD_API_URL: 'http://127.0.0.1:5109',
    INVALIDITY_PROD_API_TOKEN: 'prod-api-token-000001',
    INVALIDITY_PROD_MODULE1_API_URL: 'http://127.0.0.1:5101/run',
    INVALIDITY_PROD_MODULE1_API_TOKEN: '',
    INVALIDITY_PROD_MODULE1_AUTH_MODE: 'legacy_unauthenticated',
    INVALIDITY_PROD_PATENT_PROVIDER: 'google_patents',
    INVALIDITY_PROD_NPL_PROVIDER: 'arxiv_openalex_crossref',
    INVALIDITY_PROD_LLM_BASE_URL: 'https://open.bigmodel.cn/api/paas/v4',
    INVALIDITY_PROD_LLM_API_KEY: 'prod-llm-key-0000001',
    INVALIDITY_PROD_LLM_MODEL: 'glm-4.6v',
  };
}

test('validates strict test and prod stack contracts', () => {
  assert.equal(validateEnvironment('test', testValues()).INVALIDITY_PORT, '5209');
  assert.equal(validateEnvironment('prod', prodValues()).INVALIDITY_PORT, '5109');
});

test('requires distinct environment API and parser tokens', () => {
  const missingApi = testValues();
  missingApi.INVALIDITY_TEST_API_TOKEN = '';
  assert.throws(() => validateEnvironment('test', missingApi), /API_TOKEN/);

  const reused = testValues();
  reused.INVALIDITY_TEST_PARSER_TOKEN = reused.INVALIDITY_TEST_API_TOKEN;
  assert.throws(() => validateEnvironment('test', reused), /必须与.*不同/);
});

test('rejects cross-environment values and non-exact loopback endpoints', () => {
  const crossed = testValues();
  crossed.INVALIDITY_PROD_API_TOKEN = 'prod-secret-must-not-leak';
  assert.throws(() => validateEnvironment('test', crossed), /另一环境变量/);

  const wrongPort = testValues();
  wrongPort.INVALIDITY_TEST_API_URL = 'http://127.0.0.1:5109';
  assert.throws(() => validateEnvironment('test', wrongPort), /必须严格指向/);

  const wrongHost = testValues();
  wrongHost.INVALIDITY_TEST_MODULE1_API_URL = 'http://localhost:5201/run';
  assert.throws(() => validateEnvironment('test', wrongHost), /必须严格指向/);
});

test('rejects unsafe source roots, pure-text models, and invalid limits', () => {
  const rootSource = testValues();
  rootSource.INVALIDITY_ALLOWED_SOURCE_ROOTS = '/';
  assert.throws(() => validateEnvironment('test', rootSource), /根目录/);

  const relativeSource = testValues();
  relativeSource.INVALIDITY_ALLOWED_SOURCE_ROOTS = '.data/uploads/test';
  assert.throws(() => validateEnvironment('test', relativeSource), /绝对路径/);

  const prodSource = testValues();
  prodSource.INVALIDITY_ALLOWED_SOURCE_ROOTS = path.join(
    STACKS.prod.serviceDir,
    '.data',
    'uploads',
    'prod',
  );
  assert.throws(() => validateEnvironment('test', prodSource), /隔离根目录/);

  const textModel = testValues();
  textModel.INVALIDITY_TEST_LLM_MODEL = 'glm-4.6';
  assert.throws(() => validateEnvironment('test', textModel), /多模态模型/);

  const zeroLimit = testValues();
  zeroLimit.INVALIDITY_SOURCE_MAX_BYTES = '0';
  assert.throws(() => validateEnvironment('test', zeroLimit), /正整数/);

  const fiveGapRounds = testValues();
  fiveGapRounds.INVALIDITY_MAX_ROUNDS = '5';
  assert.doesNotThrow(() => validateEnvironment('test', fiveGapRounds));

  const tooManyRounds = testValues();
  tooManyRounds.INVALIDITY_MAX_ROUNDS = '6';
  assert.throws(() => validateEnvironment('test', tooManyRounds), /1\.\.5/);

  const tooManyWorkers = testValues();
  tooManyWorkers.INVALIDITY_WORKER_CONCURRENCY = '5';
  assert.throws(() => validateEnvironment('test', tooManyWorkers), /1\.\.4/);

  const unknownProvider = testValues();
  unknownProvider.INVALIDITY_TEST_PATENT_PROVIDER = 'anything';
  assert.throws(() => validateEnvironment('test', unknownProvider), /已批准/);
});

test('requires paired environment-local EPO OPS credentials', () => {
  const missing = testValues();
  missing.INVALIDITY_TEST_PATENT_PROVIDER = 'epo_ops';
  assert.throws(() => validateEnvironment('test', missing), /EPO OPS 凭据/);

  const unpaired = testValues();
  unpaired.INVALIDITY_TEST_PATENT_PROVIDER = 'epo_ops';
  unpaired.INVALIDITY_TEST_EPO_OPS_KEY = 'test-epo-key';
  assert.throws(() => validateEnvironment('test', unpaired), /必须同时配置/);

  const configured = testValues();
  configured.INVALIDITY_TEST_PATENT_PROVIDER = 'epo_ops';
  configured.INVALIDITY_TEST_EPO_OPS_KEY = 'test-epo-key';
  configured.INVALIDITY_TEST_EPO_OPS_SECRET = 'test-epo-secret';
  assert.doesNotThrow(() => validateEnvironment('test', configured));

  const apiEnv = invalidityChildEnvironment(
    'test',
    validateEnvironment('test', configured),
  );
  assert.equal(apiEnv.INVALIDITY_TEST_EPO_OPS_KEY, 'test-epo-key');
  assert.equal(apiEnv.INVALIDITY_TEST_EPO_OPS_SECRET, 'test-epo-secret');
  assert.equal(apiEnv.INVALIDITY_PROD_EPO_OPS_KEY, '');
  assert.equal(apiEnv.INVALIDITY_PROD_EPO_OPS_SECRET, '');
});

test('validates and isolates environment-local Patsnap REST credentials', () => {
  const missing = testValues();
  missing.INVALIDITY_TEST_PATENT_PROVIDER = 'patsnap';
  assert.throws(() => validateEnvironment('test', missing), /PATSNAP_API_KEY/);

  const configured = testValues();
  configured.INVALIDITY_TEST_PATENT_PROVIDER = 'patsnap';
  configured.INVALIDITY_TEST_PATSNAP_API_KEY = 'sk-test-patsnap-key-000001';
  configured.INVALIDITY_TEST_PATSNAP_BASE_URL = 'https://connect.zhihuiya.com';
  configured.INVALIDITY_TEST_PATSNAP_COUNT_PATH = '/search/patent/query-search-count/v2';
  configured.INVALIDITY_TEST_PATSNAP_SEARCH_PATH = '/search/patent/query-search-patent/v2';
  assert.throws(() => validateEnvironment('test', configured), /EPO OPS 凭据/);
  configured.INVALIDITY_TEST_EPO_OPS_KEY = 'test-epo-key';
  configured.INVALIDITY_TEST_EPO_OPS_SECRET = 'test-epo-secret';
  const values = validateEnvironment('test', configured);
  const apiEnv = invalidityChildEnvironment('test', values);
  assert.equal(apiEnv.INVALIDITY_TEST_PATSNAP_API_KEY, configured.INVALIDITY_TEST_PATSNAP_API_KEY);
  assert.equal(apiEnv.INVALIDITY_TEST_PATSNAP_BASE_URL, 'https://connect.zhihuiya.com');
  assert.equal(apiEnv.INVALIDITY_PROD_PATSNAP_API_KEY, '');
  assert.equal(apiEnv.INVALIDITY_PROD_PATSNAP_BASE_URL, '');

  const wrongBase = { ...configured };
  wrongBase.INVALIDITY_TEST_PATSNAP_BASE_URL = 'https://example.com';
  assert.throws(() => validateEnvironment('test', wrongBase), /必须严格等于/);

  const unknownPath = { ...configured };
  unknownPath.INVALIDITY_TEST_PATSNAP_SEARCH_PATH = '/search/patent/unknown';
  assert.throws(() => validateEnvironment('test', unknownPath), /路径白名单/);
});

test('requires explicit prod module1 authentication mode', () => {
  const missingMode = prodValues();
  missingMode.INVALIDITY_PROD_MODULE1_AUTH_MODE = '';
  assert.throws(() => validateEnvironment('prod', missingMode), /必须显式设置/);

  const bearer = prodValues();
  bearer.INVALIDITY_PROD_MODULE1_API_TOKEN = 'prod-module1-token-01';
  bearer.INVALIDITY_PROD_MODULE1_AUTH_MODE = 'bearer';
  assert.doesNotThrow(() => validateEnvironment('prod', bearer));

  const reused = prodValues();
  reused.INVALIDITY_PROD_MODULE1_API_TOKEN = reused.INVALIDITY_PROD_API_TOKEN;
  reused.INVALIDITY_PROD_MODULE1_AUTH_MODE = 'bearer';
  assert.throws(() => validateEnvironment('prod', reused), /必须与.*不同/);
});

test('parser child receives only its minimal trust-boundary variables', () => {
  const values = validateEnvironment('test', testValues());
  const parserEnv = parserChildEnvironment(values);
  assert.equal(parserEnv.INVALIDITY_ENV, 'test');
  assert.equal(parserEnv.INVALIDITY_PARSER_PORT, '5201');
  assert.equal(parserEnv.INVALIDITY_TEST_PARSER_TOKEN, values.INVALIDITY_TEST_PARSER_TOKEN);
  assert.equal(parserEnv.INVALIDITY_ALLOWED_SOURCE_ROOTS, values.INVALIDITY_ALLOWED_SOURCE_ROOTS);
  assert.equal(parserEnv.INVALIDITY_SOURCE_MAX_BYTES, '52428800');
  for (const forbidden of [
    'INVALIDITY_DATABASE_URL',
    'INVALIDITY_TEST_API_TOKEN',
    'INVALIDITY_TEST_PATENT_PROVIDER',
    'INVALIDITY_TEST_EPO_OPS_KEY',
    'INVALIDITY_TEST_EPO_OPS_SECRET',
    'INVALIDITY_TEST_PATSNAP_API_KEY',
    'INVALIDITY_TEST_PATSNAP_BASE_URL',
    'INVALIDITY_TEST_LLM_API_KEY',
  ]) {
    assert.equal(Object.hasOwn(parserEnv, forbidden), false, forbidden);
  }
});

test('5209 child keeps required runtime credentials without prod leakage', () => {
  const values = validateEnvironment('test', testValues());
  const apiEnv = invalidityChildEnvironment('test', values);
  assert.equal(apiEnv.INVALIDITY_TEST_API_TOKEN, values.INVALIDITY_TEST_API_TOKEN);
  assert.equal(apiEnv.INVALIDITY_TEST_PARSER_TOKEN, values.INVALIDITY_TEST_PARSER_TOKEN);
  assert.equal(apiEnv.INVALIDITY_ALLOWED_SOURCE_ROOTS, values.INVALIDITY_ALLOWED_SOURCE_ROOTS);
  assert.equal(apiEnv.INVALIDITY_PROD_API_TOKEN, '');
  assert.equal(Object.hasOwn(apiEnv, 'INVALIDITY_PROD_LLM_API_KEY'), true);
  assert.equal(apiEnv.INVALIDITY_PROD_LLM_API_KEY, '');
});

test('verifies immutable production code, frozen rules, and metadata digests', (t) => {
  const fixture = createReleaseFixture();
  t.after(() => removeReleaseFixture(fixture));
  assert.doesNotThrow(() => verifyProductionRelease(fixture.release, fixture.releaseId));
});

test('rejects modified, extra, symlinked, or rule-drifted release content', (t) => {
  const modified = createReleaseFixture();
  const extra = createReleaseFixture();
  const linked = createReleaseFixture();
  const rules = createReleaseFixture();
  t.after(() => {
    for (const fixture of [modified, extra, linked, rules]) {
      removeReleaseFixture(fixture);
    }
  });

  fs.chmodSync(path.join(modified.release, 'app.py'), 0o644);
  fs.writeFileSync(path.join(modified.release, 'app.py'), 'print("changed")\n');
  fs.chmodSync(path.join(modified.release, 'app.py'), 0o444);
  assert.throws(
    () => verifyProductionRelease(modified.release, modified.releaseId),
    /哈希不匹配/,
  );

  fs.chmodSync(extra.release, 0o755);
  fs.writeFileSync(path.join(extra.release, 'unlisted.py'), 'unexpected\n');
  fs.chmodSync(extra.release, 0o555);
  assert.throws(
    () => verifyProductionRelease(extra.release, extra.releaseId),
    /文件集合/,
  );

  fs.chmodSync(linked.release, 0o755);
  fs.symlinkSync('app.py', path.join(linked.release, 'linked.py'));
  fs.chmodSync(linked.release, 0o555);
  assert.throws(
    () => verifyProductionRelease(linked.release, linked.releaseId),
    /符号链接/,
  );

  fs.chmodSync(path.join(rules.release, '.release-rules', 'WORKFLOW_SPEC.md'), 0o644);
  fs.writeFileSync(
    path.join(rules.release, '.release-rules', 'WORKFLOW_SPEC.md'),
    '# drifted workflow\n',
  );
  fs.chmodSync(path.join(rules.release, '.release-rules', 'WORKFLOW_SPEC.md'), 0o444);
  assert.throws(
    () => verifyProductionRelease(rules.release, rules.releaseId),
    /哈希不匹配/,
  );

  const writable = createReleaseFixture();
  t.after(() => removeReleaseFixture(writable));
  fs.chmodSync(path.join(writable.release, 'app.py'), 0o644);
  assert.throws(
    () => verifyProductionRelease(writable.release, writable.releaseId),
    /必须只读/,
  );
});
