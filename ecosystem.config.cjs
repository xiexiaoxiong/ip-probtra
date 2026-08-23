const fs = require('fs');
const path = require('path');
const root = __dirname;

function loadEnvFile(filePath) {
  const env = {};

  if (!fs.existsSync(filePath)) {
    return env;
  }

  const content = fs.readFileSync(filePath, 'utf8');
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) {
      continue;
    }
    const separatorIndex = trimmed.indexOf('=');
    if (separatorIndex <= 0) {
      continue;
    }
    const key = trimmed.slice(0, separatorIndex).trim();
    const value = trimmed.slice(separatorIndex + 1).trim();
    env[key] = value;
  }

  return env;
}

function appPath(...segments) {
  return path.join(root, ...segments);
}

const fileEnv = loadEnvFile(appPath('IP-protral', '.env.local'));

function envValue(name) {
  return process.env[name] ?? fileEnv[name];
}

function workflowApp(folderName, port) {
  const cwd = appPath(folderName);
  // 本项目需要统一分析文本和图片。不要让 .env.local 中遗留的纯文本/旧视觉
  // 模型覆盖运行配置；如需升级模型，应在这里统一评审后修改。
  const multimodalModel = 'glm-4.6v';

  return {
    name: `patent-${folderName.replace(/\s+/g, '-').toLowerCase()}`,
    cwd,
    script: './scripts/http_run.sh',
    interpreter: '/bin/bash',
    args: `-p ${port}`,
    env: {
      COZE_WORKSPACE_PATH: cwd,
      COZELOOP_DISABLED: '1',
      PGDATABASE_URL: envValue('PGDATABASE_URL') || envValue('DATABASE_URL'),
      DATABASE_URL: envValue('DATABASE_URL') || envValue('PGDATABASE_URL'),
      COZE_WORKLOAD_IDENTITY_API_KEY:
        envValue('COZE_WORKLOAD_IDENTITY_API_KEY') ||
        envValue('LOCAL_LLM_API_KEY'),
      COZE_INTEGRATION_BASE_URL:
        envValue('COZE_INTEGRATION_BASE_URL') ||
        envValue('LOCAL_LLM_BASE_URL'),
      COZE_INTEGRATION_MODEL_BASE_URL:
        envValue('COZE_INTEGRATION_MODEL_BASE_URL') ||
        envValue('LOCAL_LLM_BASE_URL'),
      LOCAL_LLM_BASE_URL: envValue('LOCAL_LLM_BASE_URL'),
      LOCAL_LLM_API_KEY: envValue('LOCAL_LLM_API_KEY'),
      LOCAL_LLM_DEFAULT_MODEL: multimodalModel,
      LOCAL_LLM_FAST_MODEL: multimodalModel,
      LOCAL_LLM_VISION_MODEL: multimodalModel,
      LOCAL_LLM_BIGMODEL_IMAGE_MODE: envValue('LOCAL_LLM_BIGMODEL_IMAGE_MODE') || 'multimodal',
      LOCAL_LLM_ALLOW_FALLBACK: '0',
      LOCAL_LLM_TEXT_PROVIDER: envValue('LOCAL_LLM_TEXT_PROVIDER'),
      LOCAL_LLM_VISION_PROVIDER: envValue('LOCAL_LLM_VISION_PROVIDER'),
      // 显式空值阻止 Python dotenv 重新加载纯文本 fallback 配置。
      LOCAL_LLM_FALLBACK_BASE_URL: '',
      LOCAL_LLM_FALLBACK_API_KEY: '',
      LOCAL_LLM_FALLBACK_DEFAULT_MODEL: '',
      LOCAL_LLM_FALLBACK_FAST_MODEL: '',
      LOCAL_LLM_FALLBACK_VISION_MODEL: '',
      LOCAL_LLM_DIRECT_INTERFACE: envValue('LOCAL_LLM_DIRECT_INTERFACE'),
      LOCAL_LLM_BIGMODEL_DIRECT_IPS: envValue('LOCAL_LLM_BIGMODEL_DIRECT_IPS'),
      LOCAL_LLM_FORCE_DIRECT_ROUTE: envValue('LOCAL_LLM_FORCE_DIRECT_ROUTE'),
      LOCAL_LLM_DISABLE_DIRECT_ROUTE: envValue('LOCAL_LLM_DISABLE_DIRECT_ROUTE'),
      LOCAL_SEARCH_BASE_URL: envValue('LOCAL_SEARCH_BASE_URL'),
      SEARCH_PROVIDER: envValue('SEARCH_PROVIDER'),
      SEARCH_COUNTRY: envValue('SEARCH_COUNTRY'),
      SEARCH_TIMEOUT_SECONDS: envValue('SEARCH_TIMEOUT_SECONDS'),
      SEARCH_ALLOW_DIRECT_FETCH_FALLBACK: envValue('SEARCH_ALLOW_DIRECT_FETCH_FALLBACK'),
      BRIGHTDATA_API_KEY: envValue('BRIGHTDATA_API_KEY'),
      BRIGHTDATA_SERP_ZONE: envValue('BRIGHTDATA_SERP_ZONE'),
      BRIGHTDATA_UNLOCKER_ZONE: envValue('BRIGHTDATA_UNLOCKER_ZONE'),
      FEISHU_APP_ID: envValue('FEISHU_APP_ID'),
      FEISHU_APP_SECRET: envValue('FEISHU_APP_SECRET'),
      FEISHU_TENANT_ACCESS_TOKEN: envValue('FEISHU_TENANT_ACCESS_TOKEN'),
      COZE_BUCKET_ENDPOINT_URL: envValue('COZE_BUCKET_ENDPOINT_URL'),
      COZE_BUCKET_NAME: envValue('COZE_BUCKET_NAME'),
      COZE_BUCKET_ACCESS_KEY_ID: envValue('COZE_BUCKET_ACCESS_KEY_ID'),
      COZE_BUCKET_SECRET_ACCESS_KEY: envValue('COZE_BUCKET_SECRET_ACCESS_KEY'),
      COZE_BUCKET_REGION: envValue('COZE_BUCKET_REGION'),
      COZE_SEARCH_API_URL: envValue('COZE_SEARCH_API_URL'),
      COZE_SEARCH_API_TOKEN: envValue('COZE_SEARCH_API_TOKEN'),
      COZE_SEARCH_TIMEOUT: envValue('COZE_SEARCH_TIMEOUT'),
      COZE_MAX_CONCURRENT: envValue('COZE_MAX_CONCURRENT'),
      ...(folderName === '3-andun-search'
        ? {
            ANDUN_API_ENV: envValue('ANDUN_API_ENV') || 'qa',
            ANDUN_API_BASE_URL: envValue('ANDUN_API_BASE_URL'),
            ANDUN_APP_KEY: envValue('ANDUN_APP_KEY'),
            ANDUN_APP_SECRET: envValue('ANDUN_APP_SECRET'),
            ANDUN_REQUEST_TIMEOUT_SECONDS: envValue('ANDUN_REQUEST_TIMEOUT_SECONDS') || '30',
            ANDUN_REQUEST_RETRY_ATTEMPTS: envValue('ANDUN_REQUEST_RETRY_ATTEMPTS') || '4',
            ANDUN_POLL_INTERVAL_SECONDS: envValue('ANDUN_POLL_INTERVAL_SECONDS') || '10',
            ANDUN_MAX_WAIT_SECONDS: envValue('ANDUN_MAX_WAIT_SECONDS') || '1800',
          }
        : {}),
    },
  };
}

const webCwd = appPath('IP-protral');

module.exports = {
  apps: [
    // 注意：这里的名字必须和你文件夹的名字一模一样！
    workflowApp('1-patent-analysis', 5101), 
    workflowApp('2-keyword', 5102),
    workflowApp('2-keyword-fitness', 5103),
    workflowApp('2-keyword-electra', 5104),
    workflowApp('3-search', 5105),
    workflowApp('3-product-search', 5107),
    workflowApp('3-andun-search', 5108),
    workflowApp('4-claim-chat', 5106),
    {
      name: 'patent-web',
      cwd: webCwd,
      script: './scripts/start.sh',
      interpreter: '/bin/bash',
      env: {
        COZE_WORKSPACE_PATH: webCwd,
        NODE_ENV: envValue('NODE_ENV') || 'production',
        COZE_PROJECT_ENV: envValue('COZE_PROJECT_ENV') || 'PROD',
        PORT: envValue('PORT') || 3001,
        DEPLOY_RUN_PORT: envValue('DEPLOY_RUN_PORT') || 3001,
        LOCAL_DATA_DIR: envValue('LOCAL_DATA_DIR') || path.join(webCwd, '.data'),
        PGDATABASE_URL: envValue('PGDATABASE_URL') || envValue('DATABASE_URL'),
        DATABASE_URL: envValue('DATABASE_URL') || envValue('PGDATABASE_URL'),
        MODULE1_API_URL: envValue('MODULE1_API_URL') || 'http://127.0.0.1:5101/run',
        MODULE2_API_URL: envValue('MODULE2_API_URL') || 'http://127.0.0.1:5102/run',
        MODULE2_FITNESS_API_URL: envValue('MODULE2_FITNESS_API_URL') || 'http://127.0.0.1:5103/run',
        MODULE2_HOME_APPLIANCES_API_URL: envValue('MODULE2_HOME_APPLIANCES_API_URL') || 'http://127.0.0.1:5104/run',
        MODULE3_API_URL: envValue('MODULE3_API_URL') || 'http://127.0.0.1:5105/run',
        ANDUN_MODULE3_API_URL: envValue('ANDUN_MODULE3_API_URL') || 'http://127.0.0.1:5108',
        MODULE4_API_URL: envValue('MODULE4_API_URL') || 'http://127.0.0.1:5106/run',
        PATENT_ANALYSIS_MODULE1_API_URL:
          envValue('PATENT_ANALYSIS_MODULE1_API_URL') || envValue('TEST_MODULE1_API_URL') || envValue('MODULE1_API_URL') || 'http://127.0.0.1:5101/run',
        PATENT_ANALYSIS_MODULE1_API_TOKEN:
          envValue('PATENT_ANALYSIS_MODULE1_API_TOKEN') || envValue('TEST_MODULE1_API_TOKEN') || envValue('MODULE1_API_TOKEN'),
        PATENT_ANALYSIS_MODULE2_API_URL:
          envValue('PATENT_ANALYSIS_MODULE2_API_URL') || envValue('TEST_MODULE2_API_URL') || envValue('MODULE2_API_URL') || 'http://127.0.0.1:5102/run',
        PATENT_ANALYSIS_MODULE2_API_TOKEN:
          envValue('PATENT_ANALYSIS_MODULE2_API_TOKEN') || envValue('TEST_MODULE2_API_TOKEN') || envValue('MODULE2_API_TOKEN'),
        PATENT_ANALYSIS_MODULE2_FITNESS_API_URL:
          envValue('PATENT_ANALYSIS_MODULE2_FITNESS_API_URL') || envValue('TEST_MODULE2_FITNESS_API_URL') || envValue('MODULE2_FITNESS_API_URL') || 'http://127.0.0.1:5103/run',
        PATENT_ANALYSIS_MODULE2_FITNESS_API_TOKEN:
          envValue('PATENT_ANALYSIS_MODULE2_FITNESS_API_TOKEN') || envValue('TEST_MODULE2_FITNESS_API_TOKEN') || envValue('MODULE2_FITNESS_API_TOKEN'),
        PATENT_ANALYSIS_MODULE2_HOME_APPLIANCES_API_URL:
          envValue('PATENT_ANALYSIS_MODULE2_HOME_APPLIANCES_API_URL') || envValue('TEST_MODULE2_HOME_APPLIANCES_API_URL') || envValue('MODULE2_HOME_APPLIANCES_API_URL') || 'http://127.0.0.1:5104/run',
        PATENT_ANALYSIS_MODULE2_HOME_APPLIANCES_API_TOKEN:
          envValue('PATENT_ANALYSIS_MODULE2_HOME_APPLIANCES_API_TOKEN') || envValue('TEST_MODULE2_HOME_APPLIANCES_API_TOKEN') || envValue('MODULE2_HOME_APPLIANCES_API_TOKEN'),
        PATENT_ANALYSIS_PRODUCT_SEARCH_API_URL:
          envValue('PATENT_ANALYSIS_PRODUCT_SEARCH_API_URL') || envValue('TEST_PRODUCT_DETAIL_MODULE3_API_URL') || envValue('PRODUCT_DETAIL_MODULE3_API_URL') || envValue('PRODUCT_SEARCH_API_URL') || 'http://127.0.0.1:5107/run',
        PATENT_ANALYSIS_PRODUCT_SEARCH_API_TOKEN:
          envValue('PATENT_ANALYSIS_PRODUCT_SEARCH_API_TOKEN') || envValue('TEST_PRODUCT_DETAIL_MODULE3_API_TOKEN') || envValue('PRODUCT_DETAIL_MODULE3_API_TOKEN') || envValue('PRODUCT_SEARCH_API_TOKEN'),
        PATENT_ANALYSIS_MODULE4_API_URL:
          envValue('PATENT_ANALYSIS_MODULE4_API_URL') || envValue('TEST_MODULE4_API_URL') || envValue('MODULE4_API_URL') || 'http://127.0.0.1:5106/run',
        PATENT_ANALYSIS_MODULE4_API_TOKEN:
          envValue('PATENT_ANALYSIS_MODULE4_API_TOKEN') || envValue('TEST_MODULE4_API_TOKEN') || envValue('MODULE4_API_TOKEN'),
        INVALIDITY_API_URL:
          envValue('INVALIDITY_API_URL') || envValue('INVALIDITY_TEST_API_URL') || 'http://127.0.0.1:5209',
        INVALIDITY_API_TOKEN:
          envValue('INVALIDITY_API_TOKEN') || envValue('INVALIDITY_TEST_API_TOKEN'),
        INVALIDITY_UPLOAD_ROOT:
          envValue('INVALIDITY_UPLOAD_ROOT') || envValue('INVALIDITY_TEST_UPLOAD_ROOT'),
        INVALIDITY_TEST_API_URL:
          envValue('INVALIDITY_TEST_API_URL') || 'http://127.0.0.1:5209',
        INVALIDITY_TEST_API_TOKEN: envValue('INVALIDITY_TEST_API_TOKEN'),
        INVALIDITY_TEST_UPLOAD_ROOT: envValue('INVALIDITY_TEST_UPLOAD_ROOT'),
        INVALIDITY_PROD_API_URL:
          envValue('INVALIDITY_PROD_API_URL') || 'http://127.0.0.1:5109',
        INVALIDITY_PROD_API_TOKEN: envValue('INVALIDITY_PROD_API_TOKEN'),
        INVALIDITY_PROD_UPLOAD_ROOT: envValue('INVALIDITY_PROD_UPLOAD_ROOT'),
        FEISHU_APP_ID: envValue('FEISHU_APP_ID'),
        FEISHU_APP_SECRET: envValue('FEISHU_APP_SECRET'),
      },
    },
  ],
};
