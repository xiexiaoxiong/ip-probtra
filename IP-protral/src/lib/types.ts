// ============================================================
// 专利侵权自动识别系统 - 核心类型定义
// ============================================================

/** 分析会话状态 */
export type AnalysisStatus = 'idle' | 'running' | 'completed' | 'error';

/** 单个步骤的状态 */
export type StepStatus = 'pending' | 'running' | 'waiting_input' | 'completed' | 'error';

/** 比对结论状态 */
export type MatchStatus = 'matching' | 'not_matching' | 'uncertain';

/** 整体侵权判定结果 */
export type InfringementVerdict = 'infringement_likely' | 'uncertain' | 'no_infringement';

/** 输入类型 */
export type InputType = 'url' | 'file' | 'text';

export type UserRole = 'admin' | 'user';

export type UserStatus = 'pending' | 'approved' | 'rejected' | 'disabled';

export interface AuthUser {
  id: number;
  name: string;
  email: string;
  role: UserRole;
  status: UserStatus;
  approvedAt?: string | null;
  createdAt?: string | null;
}

/** 分析步骤定义 */
export interface AnalysisStep {
  id: number;
  name: string;
  description: string;
  status: StepStatus;
  startedAt?: number;
  completedAt?: number;
  error?: string;
}

/** 分析会话的输入 */
export interface AnalysisInput {
  type: InputType;
  value: string; // URL、文件存储 key 或文本内容
  fileName?: string;
  fileUrl?: string; // 上传后的可访问 URL
  text?: string; // 直接粘贴的文本内容
}

/** 专利信息（模块1输出） */
export interface PatentInfo {
  title?: string;
  patentNumber?: string;
  independentClaims?: string[];
  dependentClaims?: string[];
  specification?: string;
  drawings?: string[];
}

/** 商品信息（模块3输出） */
export interface ProductInfo {
  id: string;
  name: string;
  url?: string;
  imageUrl?: string;
  description?: string;
  source?: string;
  price?: string;
}

/** 权利要求要素比对（模块4输出 - 单条） */
export interface ClaimElementComparison {
  /** 特征编号，如 1A、1B、8A */
  featureId?: string;
  claimElement: string;
  productFeature: string;
  status: MatchStatus;
  reasoning: string;
  /** 溯源：专利原文引用 */
  patentReference?: string;
  /** 溯源：商品原始描述引用 */
  productReference?: string;
  /** 证据图片：能体现该特征的商品图片URL列表 */
  evidenceImages?: string[];
}

/** 单个商品的比对结果（模块4输出） */
export interface ProductComparison {
  productId: string;
  productName: string;
  overallVerdict: InfringementVerdict;
  claimElements: ClaimElementComparison[];
  /** 比对依据的规则说明 */
  ruleApplied?: string;
}

export type KeywordConfirmationStatus = 'timed_wait' | 'editing' | 'confirmed' | 'auto_confirmed';

export interface KeywordConfirmationState {
  status: KeywordConfirmationStatus;
  autoKeywords: string[];
  userKeywords: string[];
  finalKeywords: string[];
  promptedAt?: number;
  deadlineAt?: number;
  confirmedAt?: number;
}

/** 分析结果汇总 */
export interface AnalysisResults {
  patent?: PatentInfo;
  keywords?: string[];
  products?: ProductInfo[];
  comparisons?: ProductComparison[];
  dbRecordId?: number;
  keywordRunId?: number;
  searchRunId?: number;
  claimCompareRunId?: number;
  // 飞书表格链接（核心输出，4个模块共享数据）
  feishuUrl?: string;
  feishuAppToken?: string;
  // 各模块运行ID（可回溯）
  module1RunId?: string;
  module2RunId?: string;
  module3RunId?: string;
  module4RunId?: string;
  module4TaskStatus?: 'queued' | 'running' | 'completed' | 'error' | 'cancelled' | 'timeout';
  module4TaskStartedAt?: string;
  module4TaskFinishedAt?: string;
  module4TaskError?: string;
  // 模块异常信息
  module2Exception?: string;
  module3Exception?: string;
  module4Exception?: string;
  // 行业识别结果
  detectedIndustry?: IndustryType;
  industryReasoning?: string;
  industryUsed?: IndustryType;
  keywordConfirmation?: KeywordConfirmationState;
}

/** 分析会话 */
export interface AnalysisSession {
  id: string;
  userId?: number;
  userName?: string;
  status: AnalysisStatus;
  input: AnalysisInput;
  steps: AnalysisStep[];
  results?: AnalysisResults | null;
  patentTitle?: string | null;
  patentNumber?: string | null;
  createdAt: number;
  updatedAt: number;
}

// ============================================================
// SSE 事件类型（后端 → 前端通信协议）
// ============================================================

export type SSEEventType = 'step_start' | 'step_progress' | 'step_complete' | 'step_error' | 'analysis_complete' | 'analysis_error';

/** SSE 事件基础结构 */
export interface SSEEvent {
  type: SSEEventType;
  sessionId: string;
  timestamp: number;
}

/** 步骤开始事件 */
export interface StepStartEvent extends SSEEvent {
  type: 'step_start';
  stepId: number;
  stepName: string;
}

/** 步骤进度事件 */
export interface StepProgressEvent extends SSEEvent {
  type: 'step_progress';
  stepId: number;
  message: string;
}

/** 步骤完成事件 */
export interface StepCompleteEvent extends SSEEvent {
  type: 'step_complete';
  stepId: number;
  stepName: string;
  duration?: number;
  data?: Record<string, unknown>;
}

/** 步骤错误事件 */
export interface StepErrorEvent extends SSEEvent {
  type: 'step_error';
  stepId: number;
  stepName: string;
  error?: string;
  data?: Record<string, unknown>;
}

/** 分析完成事件 */
export interface AnalysisCompleteEvent extends SSEEvent {
  type: 'analysis_complete';
  results: AnalysisResults;
}

/** 分析错误事件 */
export interface AnalysisErrorEvent extends SSEEvent {
  type: 'analysis_error';
  error: string;
}

/** 所有 SSE 事件联合类型 */
export type AnalysisSSEEvent =
  | StepStartEvent
  | StepProgressEvent
  | StepCompleteEvent
  | StepErrorEvent
  | AnalysisCompleteEvent
  | AnalysisErrorEvent;

// ============================================================
// API 请求/响应类型
// ============================================================

/** 开始分析请求 */
export interface AnalyzeRequest {
  type: InputType;
  /** URL 输入时的专利网址 */
  url?: string;
  /** 文件上传后的对象存储 key */
  fileKey?: string;
  fileName?: string;
  /** 直接粘贴的专利文本 */
  text?: string;
}

/** 开始分析响应（SSE 流） */
// 响应通过 SSE 事件流返回，参见 AnalysisSSEEvent

/** 获取分析结果响应 */
export interface GetAnalysisResponse {
  session: AnalysisSession;
}

export interface AuthResponse {
  user: AuthUser | null;
}

// ============================================================
// 工作流模块定义
// ============================================================

export const WORKFLOW_MODULES = [
  {
    id: 1,
    name: '专利文本解析',
    description: '解析专利文件，提取权利要求、说明书和附图',
  },
  {
    id: 2,
    name: '行业识别与路由',
    description: '判断专利所属行业，选择对应的关键词生成工作流',
  },
  {
    id: 3,
    name: '技术关键词生成',
    description: '基于专利内容生成检索关键词',
  },
  {
    id: 4,
    name: '商品信息检索',
    description: '使用关键词检索市场商品信息',
  },
  {
    id: 5,
    name: '技术特征比对',
    description: '拆解技术特征，进行权利要求-商品特征比对',
  },
  {
    id: 6,
    name: '结果汇总',
    description: '优先读取工作流响应，并按需回补飞书结果数据',
  },
] as const;

/** 行业类型 */
export type IndustryType = 'fitness_equipment' | 'home_appliances' | 'general';

/** 行业配置 */
export const INDUSTRY_LABELS: Record<IndustryType, string> = {
  fitness_equipment: '健身器材',
  home_appliances: '家用电器',
  general: '通用',
};

/** 侵权判定对应的展示配置 */
export const VERDICT_CONFIG: Record<InfringementVerdict, { label: string; color: string; bgColor: string }> = {
  infringement_likely: { label: '侵权风险高', color: 'text-red-700', bgColor: 'bg-red-50 border-red-200' },
  uncertain: { label: '待进一步分析', color: 'text-amber-700', bgColor: 'bg-amber-50 border-amber-200' },
  no_infringement: { label: '侵权风险低', color: 'text-green-700', bgColor: 'bg-green-50 border-green-200' },
};

/** 比对状态对应的展示配置 */
export const MATCH_CONFIG: Record<MatchStatus, { label: string; color: string; bgColor: string }> = {
  matching: { label: '相同/等同', color: 'text-red-700', bgColor: 'bg-red-50' },
  not_matching: { label: '不相同', color: 'text-green-700', bgColor: 'bg-green-50' },
  uncertain: { label: '不确定', color: 'text-amber-700', bgColor: 'bg-amber-50' },
};
