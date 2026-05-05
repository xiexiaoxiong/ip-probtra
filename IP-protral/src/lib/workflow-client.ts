import type { IndustryType } from './types';

interface ModuleConfig {
  url: string;
  token?: string;
}

interface ModuleResponse {
  task_id?: string;
  db_record_id?: number;
  patent_record_id?: number;
  keyword_run_id?: number;
  search_run_id?: number;
  claim_compare_run_id?: number;
  feishu_app_token?: string;
  feishu_url?: string;
  run_id?: string;
  app_token?: string;
  keywords_table_id?: string;
  keywords_count?: number;
  exception_type?: string;
  exception_message?: string;
  all_comparison_results?: unknown[];
  result_summary?: string;
  table_urls?: Array<{ table_name?: string; table_url?: string }>;
  [key: string]: unknown;
}

export interface Module1Claim {
  claim_id?: string;
  claim_type?: 'INDEPENDENT' | 'DEPENDENT';
  claim_text?: string;
}

export interface Module1Figure {
  figure_id?: string;
  figure_url?: string;
}

export interface Module1ParseError {
  error_type?: string;
  error_message?: string;
  affected_section?: string;
  is_recoverable?: boolean;
}

export interface Module1FinalOutput {
  claims?: Module1Claim[];
  specification?: Record<string, string>;
  figures?: Module1Figure[];
  metadata?: {
    title?: string;
    patent_number?: string;
    application_date?: string;
    priority_date?: string;
    patent_holder?: string;
  };
  errors?: Module1ParseError[];
  task_id?: string;
  db_record_id?: number | null;
  feishu_app_token?: string | null;
  feishu_url?: string | null;
}

function getWorkflowBaseUrl(port: number): string {
  return `http://127.0.0.1:${port}/run`;
}

function getModuleConfigs(): {
  module1: ModuleConfig;
  module2: ModuleConfig;
  module2Fitness: ModuleConfig;
  module2HomeAppliances: ModuleConfig;
  module3: ModuleConfig;
  module4: ModuleConfig;
} {
  return {
    module1: {
      url: process.env.MODULE1_API_URL || getWorkflowBaseUrl(5101),
      token: process.env.MODULE1_API_TOKEN || undefined,
    },
    module2: {
      url: process.env.MODULE2_API_URL || getWorkflowBaseUrl(5102),
      token: process.env.MODULE2_API_TOKEN || undefined,
    },
    module2Fitness: {
      url: process.env.MODULE2_FITNESS_API_URL || getWorkflowBaseUrl(5103),
      token: process.env.MODULE2_FITNESS_API_TOKEN || undefined,
    },
    module2HomeAppliances: {
      url: process.env.MODULE2_HOME_APPLIANCES_API_URL || getWorkflowBaseUrl(5104),
      token: process.env.MODULE2_HOME_APPLIANCES_API_TOKEN || undefined,
    },
    module3: {
      url: process.env.MODULE3_API_URL || getWorkflowBaseUrl(5105),
      token: process.env.MODULE3_API_TOKEN || undefined,
    },
    module4: {
      url: process.env.MODULE4_API_URL || getWorkflowBaseUrl(5106),
      token: process.env.MODULE4_API_TOKEN || undefined,
    },
  };
}

function createHeaders(token?: string): HeadersInit {
  return token
    ? {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      }
    : {
        'Content-Type': 'application/json',
      };
}

function normalizeFileType(inputType: string): 'image' | 'video' {
  return inputType.toLowerCase() === 'video' ? 'video' : 'image';
}

async function callModuleApi(
  module: ModuleConfig,
  payload: Record<string, unknown>,
  onProgress?: (message: string) => void,
  timeoutMs: number = 15 * 60 * 1000,
  maxRetries: number = 1,
): Promise<ModuleResponse> {
  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    if (onProgress) {
      onProgress(attempt === 0 ? '正在调用本地工作流...' : `正在重试 (${attempt}/${maxRetries})...`);
    }

    try {
      const response = await fetch(module.url, {
        method: 'POST',
        headers: createHeaders(module.token),
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(timeoutMs),
      });

      if (!response.ok) {
        const errorText = await response.text().catch(() => '');
        throw new Error(`HTTP ${response.status}: ${errorText.slice(0, 300)}`);
      }

      const data = (await response.json()) as ModuleResponse;
      if (onProgress) {
        onProgress('工作流执行完成');
      }
      return data;
    } catch (error) {
      lastError = error instanceof Error ? error : new Error(String(error));
      if (attempt < maxRetries) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        continue;
      }
    }
  }

  throw lastError || new Error('工作流调用失败');
}

function scoreIndustryKeywords(content: string, keywords: string[]): number {
  return keywords.reduce((score, keyword) => {
    const pattern = new RegExp(keyword, 'ig');
    const matches = content.match(pattern);
    return score + (matches?.length || 0);
  }, 0);
}

export interface IndustryDetectionResult {
  industry: IndustryType;
  confidence: number;
  reasoning: string;
}

export async function warmupAllModules(): Promise<Array<{ module: string; ok: boolean; ms: number; error?: string }>> {
  const configs = getModuleConfigs();
  const modules = [
    ['模块1-专利解析', configs.module1.url],
    ['模块2-关键词生成(通用)', configs.module2.url],
    ['模块2-关键词生成(健身器材)', configs.module2Fitness.url],
    ['模块2-关键词生成(家用电器)', configs.module2HomeAppliances.url],
    ['模块3-商品检索', configs.module3.url],
    ['模块4-特征比对', configs.module4.url],
  ] as const;

  const results = await Promise.all(
    modules.map(async ([name, runUrl]) => {
      const start = Date.now();
      const healthUrl = runUrl.replace(/\/run$/, '/health');

      try {
        const response = await fetch(healthUrl, { signal: AbortSignal.timeout(10_000) });
        return {
          module: name,
          ok: response.ok,
          ms: Date.now() - start,
          error: response.ok ? undefined : `HTTP ${response.status}`,
        };
      } catch (error) {
        return {
          module: name,
          ok: false,
          ms: Date.now() - start,
          error: error instanceof Error ? error.message : String(error),
        };
      }
    }),
  );

  return results;
}

export async function warmupCozeSearch(): Promise<{ ok: boolean; ms: number; error?: string }> {
  const module3Url = getModuleConfigs().module3.url;
  const warmupUrl = module3Url.replace(/\/run$/, '/api/warmup_coze');
  const start = Date.now();

  try {
    const response = await fetch(warmupUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.timeout(30_000),
    });
    return {
      ok: response.ok,
      ms: Date.now() - start,
      error: response.ok ? undefined : `HTTP ${response.status}`,
    };
  } catch (error) {
    return {
      ok: false,
      ms: Date.now() - start,
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

export async function detectIndustry(
  patentContent: string,
  onProgress?: (message: string) => void,
): Promise<IndustryDetectionResult> {
  if (onProgress) {
    onProgress('正在根据专利文本判断行业...');
  }

  const normalized = patentContent.toLowerCase();
  const fitnessScore = scoreIndustryKeywords(normalized, [
    '健身',
    '训练',
    '跑步机',
    '划船机',
    '椭圆机',
    '单车',
    '哑铃',
    '杠铃',
    '有氧',
    '力量',
    '运动器械',
    'treadmill',
    'rowing',
    'fitness',
  ]);
  const applianceScore = scoreIndustryKeywords(normalized, [
    '家电',
    '家用电器',
    '洗衣机',
    '冰箱',
    '吸尘器',
    '空调',
    '空气净化',
    '电饭煲',
    '烹饪',
    '清洁',
    '厨房',
    'appliance',
    'vacuum',
    'refrigerator',
  ]);

  if (fitnessScore === 0 && applianceScore === 0) {
    return {
      industry: 'general',
      confidence: 0.4,
      reasoning: '未命中健身器材或家用电器的高置信度关键词，回退到通用工作流。',
    };
  }

  if (fitnessScore >= applianceScore) {
    return {
      industry: 'fitness_equipment',
      confidence: Number((fitnessScore / Math.max(fitnessScore + applianceScore, 1)).toFixed(2)),
      reasoning: `命中健身器材相关关键词 ${fitnessScore} 次，家电关键词 ${applianceScore} 次。`,
    };
  }

  return {
    industry: 'home_appliances',
    confidence: Number((applianceScore / Math.max(fitnessScore + applianceScore, 1)).toFixed(2)),
    reasoning: `命中家用电器相关关键词 ${applianceScore} 次，健身器材关键词 ${fitnessScore} 次。`,
  };
}

export interface Module1Result {
  taskId: string;
  feishuUrl: string;
  feishuAppToken: string;
  runId: string;
  dbRecordId?: number;
  finalOutput?: Module1FinalOutput;
}

export async function runModule1(
  patentFileUrl: string,
  patentFileType: string,
  onProgress?: (message: string) => void,
): Promise<Module1Result> {
  const configs = getModuleConfigs();
  const taskId = `analysis_${Date.now()}`;

  const data = await callModuleApi(
    configs.module1,
    {
      patent_file: {
        url: patentFileUrl,
        file_type: normalizeFileType(patentFileType),
      },
      task_id: taskId,
    },
    onProgress,
  );

  return {
    taskId: (data.task_id as string) || taskId,
    feishuUrl: String(data.feishu_url || ''),
    feishuAppToken: String(data.feishu_app_token || ''),
    runId: String(data.run_id || ''),
    dbRecordId: typeof data.db_record_id === 'number' ? data.db_record_id : undefined,
    finalOutput: (data.final_output && typeof data.final_output === 'object')
      ? (data.final_output as Module1FinalOutput)
      : undefined,
  };
}

export interface Module2Result {
  patentRecordId: number;
  keywordRunId?: number;
  keywordsCount: number;
  exceptionType?: string;
  exceptionMessage?: string;
  runId: string;
  industryUsed: IndustryType;
}

export async function runModule2(
  patentRecordId: number,
  analysisSessionId: string,
  industry: IndustryType = 'general',
  onProgress?: (message: string) => void,
): Promise<Module2Result> {
  const configs = getModuleConfigs();

  const moduleConfig =
    industry === 'fitness_equipment'
      ? configs.module2Fitness
      : industry === 'home_appliances'
        ? configs.module2HomeAppliances
        : configs.module2;

  const data = await callModuleApi(
    moduleConfig,
    {
      patent_record_id: patentRecordId,
      analysis_session_id: analysisSessionId,
    },
    onProgress,
  );

  return {
    patentRecordId: Number(data.patent_record_id || patentRecordId),
    keywordRunId: typeof data.keyword_run_id === 'number' ? data.keyword_run_id : undefined,
    keywordsCount: Number(data.keywords_count || 0),
    exceptionType: typeof data.exception_type === 'string' ? data.exception_type : undefined,
    exceptionMessage: typeof data.exception_message === 'string' ? data.exception_message : undefined,
    runId: String(data.run_id || ''),
    industryUsed: industry,
  };
}

export interface Module3Result {
  searchRunId?: number;
  exceptionType?: string;
  exceptionMessage?: string;
  runId: string;
}

export async function runModule3(
  patentRecordId: number,
  analysisSessionId: string,
  inputKeywords?: string[],
  onProgress?: (message: string) => void,
): Promise<Module3Result> {
  const configs = getModuleConfigs();
  const data = await callModuleApi(
    configs.module3,
    {
      patent_record_id: patentRecordId,
      analysis_session_id: analysisSessionId,
      input_keywords: inputKeywords || [],
    },
    onProgress,
    20 * 60 * 1000,
    0,
  );

  return {
    searchRunId: typeof data.search_run_id === 'number' ? data.search_run_id : undefined,
    exceptionType: typeof data.exception_type === 'string' ? data.exception_type : undefined,
    exceptionMessage:
      typeof data.exception_message === 'string'
        ? data.exception_message
        : typeof data.error_message === 'string'
          ? data.error_message
          : undefined,
    runId: String(data.run_id || ''),
  };
}

export interface Module4Result {
  claimCompareRunId?: number;
  exceptionType?: string;
  exceptionMessage?: string;
  runId: string;
  allComparisonResults: unknown[];
  resultSummary: string;
  tableUrls: Array<{ tableName?: string; tableUrl?: string }>;
}

export async function runModule4(
  patentRecordId: number,
  analysisSessionId: string,
  onProgress?: (message: string) => void,
): Promise<Module4Result> {
  const configs = getModuleConfigs();
  const data = await callModuleApi(
    configs.module4,
    {
      patent_record_id: patentRecordId,
      analysis_session_id: analysisSessionId,
    },
    onProgress,
    20 * 60 * 1000,
    0,
  );

  return {
    claimCompareRunId: typeof data.claim_compare_run_id === 'number' ? data.claim_compare_run_id : undefined,
    exceptionType: typeof data.exception_type === 'string' ? data.exception_type : undefined,
    exceptionMessage:
      typeof data.exception_message === 'string'
        ? data.exception_message
        : typeof data.result_summary === 'string' &&
            (
              data.result_summary.includes('失败') ||
              data.result_summary.includes('无比对结果') ||
              data.result_summary.includes('无法进行比对')
            )
          ? data.result_summary
          : typeof data.claim_compare_run_id === 'number' && data.claim_compare_run_id <= 0 && typeof data.result_summary === 'string' && data.result_summary.trim()
            ? data.result_summary
            : undefined,
    runId: String(data.run_id || ''),
    allComparisonResults: Array.isArray(data.all_comparison_results) ? data.all_comparison_results : [],
    resultSummary: String(data.result_summary || ''),
    tableUrls: Array.isArray(data.table_urls)
      ? (data.table_urls as Array<{ table_name?: string; table_url?: string }>).map((item) => ({
          tableName: item.table_name,
          tableUrl: item.table_url,
        }))
      : [],
  };
}

export async function fetchPatentContent(url: string): Promise<string> {
  const response = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  if (!response.ok) {
    throw new Error(`获取专利内容失败: HTTP ${response.status}`);
  }

  const text = await response.text();
  if (!text.trim()) {
    throw new Error('获取到的专利内容为空');
  }

  return text;
}
