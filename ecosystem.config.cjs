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
      LOCAL_LLM_BASE_URL: envValue('LOCAL_LLM_BASE_URL'),
      LOCAL_LLM_API_KEY: envValue('LOCAL_LLM_API_KEY'),
      LOCAL_LLM_DEFAULT_MODEL: envValue('LOCAL_LLM_DEFAULT_MODEL'),
      LOCAL_LLM_FAST_MODEL: envValue('LOCAL_LLM_FAST_MODEL'),
      LOCAL_LLM_VISION_MODEL: envValue('LOCAL_LLM_VISION_MODEL'),
      LOCAL_LLM_TEXT_PROVIDER: envValue('LOCAL_LLM_TEXT_PROVIDER'),
      LOCAL_LLM_VISION_PROVIDER: envValue('LOCAL_LLM_VISION_PROVIDER'),
      LOCAL_LLM_FALLBACK_BASE_URL: envValue('LOCAL_LLM_FALLBACK_BASE_URL'),
      LOCAL_LLM_FALLBACK_API_KEY: envValue('LOCAL_LLM_FALLBACK_API_KEY'),
      LOCAL_LLM_FALLBACK_DEFAULT_MODEL: envValue('LOCAL_LLM_FALLBACK_DEFAULT_MODEL'),
      LOCAL_LLM_FALLBACK_FAST_MODEL: envValue('LOCAL_LLM_FALLBACK_FAST_MODEL'),
      LOCAL_LLM_FALLBACK_VISION_MODEL: envValue('LOCAL_LLM_FALLBACK_VISION_MODEL'),
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
    workflowApp('4-claim-chat', 5106),
    {
      name: 'patent-web',
      cwd: webCwd,
      script: './scripts/start.sh',
      interpreter: '/bin/bash',
      env: {
        COZE_WORKSPACE_PATH: webCwd,
        PORT: envValue('PORT') || 5000,
        DEPLOY_RUN_PORT: envValue('DEPLOY_RUN_PORT') || 5000,
        LOCAL_DATA_DIR: envValue('LOCAL_DATA_DIR') || path.join(webCwd, '.data'),
        PGDATABASE_URL: envValue('PGDATABASE_URL') || envValue('DATABASE_URL'),
        DATABASE_URL: envValue('DATABASE_URL') || envValue('PGDATABASE_URL'),
        MODULE1_API_URL: envValue('MODULE1_API_URL') || 'http://127.0.0.1:5101/run',
        MODULE2_API_URL: envValue('MODULE2_API_URL') || 'http://127.0.0.1:5102/run',
        MODULE2_FITNESS_API_URL: envValue('MODULE2_FITNESS_API_URL') || 'http://127.0.0.1:5103/run',
        MODULE2_HOME_APPLIANCES_API_URL: envValue('MODULE2_HOME_APPLIANCES_API_URL') || 'http://127.0.0.1:5104/run',
        MODULE3_API_URL: envValue('MODULE3_API_URL') || 'http://127.0.0.1:5105/run',
        MODULE4_API_URL: envValue('MODULE4_API_URL') || 'http://127.0.0.1:5106/run',
        FEISHU_APP_ID: envValue('FEISHU_APP_ID'),
        FEISHU_APP_SECRET: envValue('FEISHU_APP_SECRET'),
      },
    },
  ],
};
