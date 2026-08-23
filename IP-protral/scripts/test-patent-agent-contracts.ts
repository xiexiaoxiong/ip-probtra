import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import {
  extractPatentAgentSource,
  guardPatentAgentIntent,
  parsePatentAgentAnalysisKinds,
  type PatentAgentAnalysisKind,
} from '../src/lib/patent-agent';
import {
  infringementTaskProgress,
  invalidityTaskProgress,
} from '../src/lib/agent-task-progress';
import { deriveAgentInvalidityAction } from '../src/lib/agent-invalidity-action';
import { buildAgentInvaliditySummary } from '../src/lib/agent-invalidity-summary';
import { buildAgentInvalidityModule11Report } from '../src/lib/agent-invalidity-report';
import { agentToolAnchorMessageId } from '../src/lib/agent-conversation-layout';
import { buildAgentInfringementResult } from '../src/lib/agent-infringement-result';

const projectRoot = path.resolve(process.cwd());

function intent(selectedAnalysisKinds: PatentAgentAnalysisKind[], message: string, attachmentName?: string) {
  const source = extractPatentAgentSource(message, attachmentName);
  return guardPatentAgentIntent({ source, selectedAnalysisKinds });
}

assert.equal(intent([], '专利权的保护期限是多久？'), 'question');
assert.equal(intent([], '请同时做侵权分析和无效检索', 'target.pdf'), 'question', '未选按钮时不得被文字或附件触发后台工作流');
assert.equal(intent(['invalidity'], '请帮我做无效分析'), 'needs_clarification');
assert.equal(intent(['invalidity'], '开始处理', 'target.pdf'), 'invalidity');
assert.equal(intent(['infringement'], '开始处理', 'target.pdf'), 'infringement');
assert.equal(intent(['invalidity', 'infringement'], '开始处理', 'target.pdf'), 'both');
assert.deepEqual(parsePatentAgentAnalysisKinds(['infringement', 'invalidity', 'invalidity']), ['invalidity', 'infringement']);
assert.throws(() => parsePatentAgentAnalysisKinds(['invalidity', 'unexpected']), /分析类型无效/);

const longText = `权利要求1：${'一种技术装置，包括结构单元。'.repeat(20)}`;
assert.equal(extractPatentAgentSource(longText)?.type, 'text');
assert.equal(extractPatentAgentSource('CN123456789A'), null);

const layoutMessages = [
  { id: 'user-1', role: 'user' as const },
  { id: 'assistant-start', role: 'assistant' as const },
  { id: 'assistant-followup', role: 'assistant' as const },
  { id: 'user-2', role: 'user' as const },
];
assert.equal(agentToolAnchorMessageId(layoutMessages, 'user-1'), 'assistant-followup', '过程卡必须位于同轮最后一条启动回复之后');
assert.equal(agentToolAnchorMessageId(layoutMessages, 'user-2'), 'user-2', '没有启动回复时过程卡回退到原用户消息');

const infringementProgress = infringementTaskProgress({
  session: {
    status: 'running',
    steps: [
      { id: 1, name: '专利文本解析', status: 'completed' },
      { id: 2, name: '行业识别与路由', status: 'completed' },
      { id: 3, name: '技术关键词生成', description: '正在提炼检索词', status: 'running' },
      { id: 4, name: '商品信息检索', status: 'pending' },
      { id: 5, name: '技术特征比对', status: 'pending' },
      { id: 6, name: '结果汇总', status: 'pending' },
    ],
  },
});
assert.equal(infringementProgress?.totalCount, 4);
assert.equal(infringementProgress?.completedCount, 1);
assert.equal(infringementProgress?.currentLabel, '技术关键词生成');
assert.equal(infringementProgress?.steps[1]?.state, 'running');

const invalidityProgress = invalidityTaskProgress({
  investigation: { status: 'running' },
  claim_investigations: [
    { status: 'gap_search', current_iteration_no: 3, in_scope: true },
  ],
});
assert.equal(invalidityProgress?.totalCount, 11);
assert.equal(invalidityProgress?.completedCount, 8);
assert.equal(invalidityProgress?.currentLabel, '进一步检索与补证');
assert.match(invalidityProgress?.currentDetail || '', /第 3 个持久化检索迭代/);

const partialWithFinalReport = invalidityTaskProgress({
  investigation: { status: 'partial' },
  claim_investigations: [
    { status: 'partial', current_iteration_no: 6, in_scope: true },
  ],
  report: {
    module_runs: [
      { module_code: 'I4_I_INVENTIVE_STEP', status: 'succeeded' },
      { module_code: 'I5_REPORT', status: 'succeeded' },
    ],
  },
});
assert.equal(partialWithFinalReport?.completedCount, 10, '报告已经持久化时不得把模块10、11显示为待开始');
assert.equal(partialWithFinalReport?.steps[8]?.state, 'partial');
assert.equal(partialWithFinalReport?.steps[9]?.state, 'completed');
assert.equal(partialWithFinalReport?.steps[10]?.state, 'completed');
assert.match(partialWithFinalReport?.currentDetail || '', /已收口/);
assert.match(partialWithFinalReport?.currentDetail || '', /报告已按当前证据完成/);

const queuedClaimsProgress = invalidityTaskProgress({
  investigation: { status: 'running' },
  claim_investigations: [
    { status: 'initial_search', current_iteration_no: 1, in_scope: true },
    { status: 'queued', current_iteration_no: 0, in_scope: true },
    { status: 'queued', current_iteration_no: 0, in_scope: true },
  ],
});
assert.equal(
  queuedClaimsProgress?.currentLabel,
  '首轮检索与证据核验',
  '排队中的权利要求不得把当前事项拽回第一模块',
);
assert.equal(queuedClaimsProgress?.completedCount, 4);
assert.equal(queuedClaimsProgress?.steps[0]?.state, 'completed');
assert.equal(queuedClaimsProgress?.steps[4]?.state, 'running');

const allQueuedProgress = invalidityTaskProgress({
  investigation: { status: 'running' },
  claim_investigations: [
    { status: 'queued', current_iteration_no: 0, in_scope: true },
  ],
});
assert.equal(allQueuedProgress?.currentLabel, '总结核心发明点');
assert.equal(allQueuedProgress?.steps[2]?.state, 'running');

const gapActionPayload = {
  investigation: {
    id: 'investigation-1',
    status: 'partial',
    state_version: 7,
    updated_at: '2026-08-16T12:00:00.000Z',
  },
  claim_investigations: [
    {
      id: 'claim-1',
      status: 'partial',
      in_scope: true,
      current_iteration_no: 2,
      updated_at: '2026-08-16T12:00:05.000Z',
    },
  ],
};
const gapAction = deriveAgentInvalidityAction(gapActionPayload, 'patent-agent-tools-v2');
assert.equal(gapAction?.kind, 'gap_continuation');
if (gapAction?.kind === 'gap_continuation') {
  assert.equal(gapAction.nextGapRound, 2);
  assert.equal(gapAction.deadlineAt, '2026-08-16T12:00:35.000Z', '倒计时必须绑定持久化 checkpoint，刷新不得重置');
  assert.deepEqual(gapAction.claimInvestigationIds, ['claim-1']);
}
assert.equal(deriveAgentInvalidityAction(gapActionPayload, 'patent-agent-tools-v1'), null, '旧工具任务不得被新自动续检合同重放');
assert.equal(
  deriveAgentInvalidityAction({
    ...gapActionPayload,
    claim_investigations: [{ ...gapActionPayload.claim_investigations[0], current_iteration_no: 6 }],
  }, 'patent-agent-tools-v2'),
  null,
  '已完成第五个 gap 轮后不得继续倒计时',
);
assert.equal(
  deriveAgentInvalidityAction({
    ...gapActionPayload,
    investigation: { ...gapActionPayload.investigation, status: 'needs_human_review' },
  }, 'patent-agent-tools-v2')?.kind,
  'human_review',
  '法律事实等待必须显示人工确认而非自动续检',
);

const invaliditySummary = buildAgentInvaliditySummary({
  investigation: { status: 'partial' },
  claim_investigations: [
    { id: 'claim-1', claim_id: '1', in_scope: true, status: 'search_budget_exhausted' },
  ],
  report: {
    target_patent: { patent_number: 'CN100000001A', title: '测试装置' },
    documents: [{ id: 'doc-1' }],
    feature_disclosures: [{ id: 'disclosure-1' }],
    gap_items: [{ id: 'gap-1', status: 'open' }],
    module_runs: [{ id: 'run-1', status: 'failed', error_code: 'TEST_FAILURE' }],
  },
});
assert.equal(invaliditySummary?.patentLabel, 'CN100000001A《测试装置》');
assert.equal(invaliditySummary?.claims[0]?.statusLabel, '五轮检索已收口');
assert.equal(invaliditySummary?.openGapCount, 1);
assert.equal(invaliditySummary?.failedRunCount, 1);
assert.match(invaliditySummary?.headline || '', /本次自动任务已结束/);
assert.match(invaliditySummary?.detail || '', /已有证据和报告均已保存/);
assert.match(invaliditySummary?.evidenceBoundary || '', /不能反向证明专利稳定/);

const module11Report = buildAgentInvalidityModule11Report({
  report: {
    contract_version: 'v1',
    report_kind: 'invalidity_evidence_data',
    snapshot_sha256: 'a'.repeat(64),
    inventive_step_narratives: [{
      claim_id: '1',
      claim_investigation_id: 'claim-1',
      evidence_complete: false,
      conclusion_text: '现有证据尚不足以证明不具备创造性',
      paragraphs: ['第一段律师分析。', '第二段证据边界。'],
    }],
    similarity_claim_charts: [{
      claim_id: '1',
      claim_investigation_id: 'claim-1',
      ranking_basis: '按已确认披露特征数排序',
      ranked_documents: [{
        rank: 1,
        document_id: 'doc-1',
        confirmed_disclosed_feature_count: 1,
        total_feature_count: 2,
        document: {
          title: '第一篇对比文件',
          identifiers: { publication_number: 'CN100000001A' },
        },
      }],
      feature_rows: [{
        limitation_id: 'limitation-1',
        feature_key: '1A',
        limitation_text: '第一项技术特征',
        cells: [{ document_id: 'doc-1', disclosure_status: 'explicit' }],
      }],
    }],
    feature_disclosures: [
      { document_id: 'doc-1' },
      { document_id: 'doc-2' },
    ],
  },
});
assert.equal(module11Report?.narratives[0]?.paragraphs.length, 2);
assert.equal(module11Report?.matrices[0]?.documents[0]?.label, 'CN100000001A《第一篇对比文件》');
assert.equal(module11Report?.matrices[0]?.features[0]?.cells[0]?.label, '有（明确）');
assert.equal(module11Report?.analyzedDocumentCount, 2);
assert.equal(buildAgentInvalidityModule11Report({ report: { contract_version: 'v2' } }), null);

const infringementResult = buildAgentInfringementResult({
  session: {
    analysisKind: 'infringement',
    status: 'completed',
    patentNumber: 'CN200000001U',
    results: {
      resultsCompleteness: 'final',
      patent: { title: '测试取水设备' },
      keywords: ['取水设备', '抽水机'],
      products: [
        { id: 'product-1', name: '商品一' },
        { id: 'product-2', name: '商品二' },
      ],
      comparisons: [
        { productId: 'product-1', productName: '商品一', productSimilarityScore: 82, riskLevel: 'high_risk' },
        { productId: 'product-2', productName: '商品二', productSimilarityScore: 15, riskLevel: 'clear_low_risk' },
      ],
    },
  },
});
assert.equal(infringementResult?.patentLabel, 'CN200000001U《测试取水设备》');
assert.equal(infringementResult?.keywordCount, 2);
assert.equal(infringementResult?.productCount, 2);
assert.equal(infringementResult?.analyzedProductCount, 2);
assert.equal(infringementResult?.riskCounts.high_risk, 1);
assert.equal(infringementResult?.riskCounts.clear_low_risk, 1);
assert.equal(infringementResult?.isPartial, false);
assert.equal(buildAgentInfringementResult({ session: { analysisKind: 'infringement', status: 'running' } }), null);
assert.equal(buildAgentInfringementResult({ session: { analysisKind: 'infringement', status: 'error' } }), null);

async function main(): Promise<void> {
  const pageSource = await readFile(path.join(projectRoot, 'src/app/page.tsx'), 'utf8');
  assert.equal((pageSource.match(/<Textarea/g) || []).length, 1, '正式首页必须只有一个主对话输入框');
  assert.ok(!pageSource.includes('UploadForm'), '正式首页不得恢复旧上传模式选择器');
  assert.ok(!pageSource.includes('AnalysisProgress'), '正式首页不得恢复旧的独立流水线面板');
  assert.equal((pageSource.match(/data-analysis-kind=/g) || []).length, 2, 'Agent 输入区必须只有两个显式分析开关');
  assert.ok(pageSource.includes('data-analysis-kind="invalidity"'), '输入区必须提供专利无效开关');
  assert.ok(pageSource.includes('data-analysis-kind="infringement"'), '输入区必须提供专利侵权分析开关');
  assert.ok(pageSource.includes('aria-pressed={selectedAnalysisKinds.includes'), '两个分析按钮必须暴露可访问的选中状态');
  assert.ok(pageSource.includes("form.append('analysisKind', kind)"), '前端必须把每个显式选择独立提交给服务端');
  assert.ok(pageSource.includes('未选择分析类型：本次只由大模型回答，不会启动后台分析。'), '输入区必须解释未选择时的直接问答行为');
  assert.ok(pageSource.includes('data-agent-task-progress='), 'Agent 消息下必须显示业务工具的当前事项进度');
  assert.ok(pageSource.includes('data-agent-work-trace="persisted-stage-status"'), '过程卡必须显示来自持久化阶段状态的 Agent 工作动态');
  assert.ok(pageSource.includes('查看全部事项'), '详细事项列表必须使用默认收起的展开入口');
  assert.ok(pageSource.includes('已完成 {progress.completedCount} / {progress.totalCount} 项'), '进度必须来自真实完成事项计数');
  assert.ok(pageSource.includes('data-agent-confirmation={action.kind}'), 'Agent 工具卡必须承载确认提示');
  assert.ok(pageSource.includes('秒内未操作将自动开始'), 'gap 调度确认必须显示自动开始倒计时');
  assert.ok(pageSource.includes('在 Agent 中处理人工确认'), '法律事实等待必须在 Agent 中显示人工入口');
  assert.ok(pageSource.includes('data-agent-result-ready="true"'), '过程卡必须明确告知最终结论已另起回复');
  assert.ok(pageSource.includes('<AgentInvalidityResultMessage'), '模块11完成后必须另起独立 Agent 结果回复');
  assert.ok(pageSource.includes('<AgentInfringementResultMessage'), '侵权分析完成后必须另起独立 Agent 结果回复');
  assert.ok(pageSource.includes('buildAgentInfringementResult'), '侵权独立结果必须从 owner-scoped session 构建');
  assert.ok(!pageSource.includes('<AgentInvalidityReport'), '过程卡页面不得直接嵌套模块11全文组件');
  assert.ok(!pageSource.includes('/results?session='), '侵权过程卡不得混入结果页链接');
  assert.ok(pageSource.includes('agentToolAnchorMessageId'), '过程卡必须排在同轮启动回复之后');
  assert.ok(!pageSource.includes('/invalidity/results?session='), 'Agent 无效任务不得再跳到历史结果页');
  assert.ok(pageSource.includes('/test/module-lab?session='), '管理员必须能从 Agent 打开同一 session 的诊断视图');
  assert.ok(pageSource.includes('/test/product-pipeline?session='), '管理员必须能从侵权 Agent 打开同一 session 的四阶段诊断视图');
  assert.ok(pageSource.includes('tool.analysisSessionId && isAdmin'), '两类同任务诊断入口必须只对管理员显示');
  assert.ok(pageSource.includes('查看本次分析各模块'), '侵权 Agent 过程卡必须提供清晰的四阶段诊断按钮');
  assert.ok(pageSource.includes('}, 3000);'), '运行中的 Agent 工具必须以足够及时的周期刷新真实状态');
  assert.ok(pageSource.includes('什么是专利的公开日？'), '引导问题必须改为普通专利知识问题');
  assert.ok(!pageSource.includes('对这个附件同时做侵权和无效分析'), '不得继续用同时启动两条工作流作为引导问题');
  assert.ok(pageSource.includes('href="/test/module-lab"'), 'Agent 侧边栏必须直达十一模块无效实验室');
  assert.ok(pageSource.includes('>无效检测测试版<'), 'Agent 侧边栏必须显示无效检测测试版');
  assert.ok(pageSource.includes('href="/test/product-pipeline"'), 'Agent 侧边栏必须直达专利分析实验室');
  assert.ok(pageSource.includes('>专利分析实验室<'), 'Agent 侧边栏必须显示专利分析实验室');
  assert.equal((pageSource.match(/href="\/test\//g) || []).length, 2, 'Agent 侧边栏只能公开两个测试页面入口');
  assert.ok(!pageSource.includes('href="/test/invalidity"'), '旧无效技术测试页不得继续作为 Agent 导航入口');
  assert.ok(!pageSource.includes('强制停止'), 'Agent 工具卡不得保留单独的强制停止按钮');
  assert.ok(pageSource.includes('stopActiveInvalidityTasks'), 'Agent 工作中发送按钮必须切换为停止处理器');
  assert.ok(pageSource.includes('agentBusy'), 'Agent 必须跟踪工作中的工具任务');
  assert.ok(pageSource.includes('if (agentBusy) return;'), 'Agent 工作中回车键必须保持普通换行行为');
  assert.ok(!pageSource.includes('window.confirm('), '停止按钮不得使用浏览器二次确认弹窗');

  const agentMessageRouteSource = await readFile(path.join(projectRoot, 'src/app/api/agent/messages/route.ts'), 'utf8');
  assert.ok(agentMessageRouteSource.includes('完成后将另起一条回复输出结论和表格'), '新任务启动回复必须明确最终结果会另起消息');
  assert.ok(!agentMessageRouteSource.includes('运行状态和结果入口会显示在本条消息下方'), '新任务不得暗示结果继续混在过程卡里');

  const module11ComponentSource = await readFile(
    path.join(projectRoot, 'src/components/agent-invalidity-report.tsx'),
    'utf8',
  );
  assert.ok(module11ComponentSource.includes('data-agent-module11-narrative'), '模块11第一部分必须直接显示在 Agent');
  assert.ok(module11ComponentSource.includes('data-agent-module11-top10-matrix'), '模块11第二部分必须在 Agent 显示横向矩阵');
  assert.match(module11ComponentSource, /<details open[^>]*data-agent-module11-narrative/, '律师文字版必须默认展开');
  assert.match(module11ComponentSource, /<details open[^>]*data-agent-module11-top10/, 'Top10 横向表必须默认展开');
  assert.ok(module11ComponentSource.includes('/api/invalidity/session/${encodeURIComponent(sessionId)}/export'), '第三部分必须在 Agent 提供同 session XLSX 导出');
  assert.ok(module11ComponentSource.includes('每篇对比文件单独建立一个 Sheet'), 'Agent 必须解释逐篇 Sheet 的导出结构');

  const invalidityResultMessageSource = await readFile(
    path.join(projectRoot, 'src/components/agent-invalidity-result-message.tsx'),
    'utf8',
  );
  assert.ok(invalidityResultMessageSource.includes('data-agent-final-message="invalidity"'), '模块11结果必须是独立的 Agent 消息');
  assert.ok(invalidityResultMessageSource.includes('data-agent-message-role="assistant"'), '独立结果必须使用 assistant 消息语义');
  assert.ok(invalidityResultMessageSource.includes('data-agent-invalidity-result="true"'), '证据结论必须放在独立结果消息内');
  assert.ok(invalidityResultMessageSource.includes('<AgentInvalidityReport'), '独立结果消息必须承载模块11三部分结果');

  const infringementResultMessageSource = await readFile(
    path.join(projectRoot, 'src/components/agent-infringement-result-message.tsx'),
    'utf8',
  );
  assert.ok(infringementResultMessageSource.includes('data-agent-final-message="infringement"'), '侵权结论必须是独立的 Agent 消息');
  assert.ok(infringementResultMessageSource.includes('data-agent-message-role="assistant"'), '侵权结论必须使用 assistant 消息语义');
  assert.ok(infringementResultMessageSource.includes('data-agent-infringement-result="true"'), '侵权结论必须位于独立消息内');
  assert.ok(infringementResultMessageSource.includes('<ResultsScoreTable'), '侵权独立结果必须直接显示商品结果表');
  assert.ok(infringementResultMessageSource.includes('/api/analysis/${encodeURIComponent(sessionId)}/export'), '侵权独立结果必须提供 XLSX 导出');

  const invaliditySessionRouteSource = await readFile(
    path.join(projectRoot, 'src/app/api/invalidity/session/[id]/route.ts'),
    'utf8',
  );
  assert.ok(invaliditySessionRouteSource.includes('export async function POST'), '无效 session 接口必须支持 POST 强制停止');
  assert.ok(invaliditySessionRouteSource.includes('/cancel'), '强制停止必须调用唯一后端的调查级取消接口');
  assert.ok(invaliditySessionRouteSource.includes('session.userId !== user.id'), '强制停止必须校验会话归属');
  assert.ok(invaliditySessionRouteSource.includes("session.analysisKind !== 'invalidity'"), '强制停止只接受无效调查会话');

  const legacyInvalidityPageSource = await readFile(
    path.join(projectRoot, 'src/app/invalidity/page.tsx'),
    'utf8',
  );
  assert.ok(legacyInvalidityPageSource.includes("redirect('/')"), '旧 /invalidity 入口必须回到 Agent');
  assert.ok(!legacyInvalidityPageSource.includes('UploadForm'), '旧 /invalidity 不得继续保留重复上传入口');
  assert.ok(!legacyInvalidityPageSource.includes('/api/invalidity/investigations'), '旧入口不得绕过 Agent 单独建案');

  const invalidityResultsSource = await readFile(
    path.join(projectRoot, 'src/app/invalidity/results/page.tsx'),
    'utf8',
  );
  assert.ok(invalidityResultsSource.includes('<Link href="/">返回 Agent</Link>'), '无效结果页必须返回 Agent');
  assert.ok(!invalidityResultsSource.includes('href="/invalidity"'), '结果页不得重新暴露旧无效入口');

  const patentLabSource = await readFile(path.join(projectRoot, 'src/app/test/product-pipeline/page.tsx'), 'utf8');
  assert.ok(patentLabSource.includes('专利分析实验室'), '专利分析统一测试页必须使用清晰的统一名称');
  assert.ok(patentLabSource.includes('data-patent-analysis-input'), '专利分析测试页顶部必须保留统一专利输入区');
  assert.ok(patentLabSource.includes('<UploadForm'), '专利分析测试页必须支持直接输入或上传专利');
  for (const moduleTitle of ['专利解析', '关键词生成', '商品检索', '权利要求与商品比对']) {
    assert.ok(patentLabSource.includes(`title: '${moduleTitle}'`), `专利分析测试页缺少模块：${moduleTitle}`);
  }
  assert.ok(patentLabSource.includes('data-patent-analysis-modules'), '四个分析阶段必须由统一折叠容器承载');
  assert.ok(patentLabSource.includes('data-patent-analysis-module-input'), '每个阶段必须单独展示本次输入');
  assert.ok(patentLabSource.includes('data-patent-analysis-module-output'), '每个阶段必须单独展示本次输出');
  assert.ok(patentLabSource.includes('data-patent-analysis-module-result'), '每个阶段必须单独展示可读结果');
  assert.ok(patentLabSource.includes('本次输入'), '阶段折叠区必须显示本次输入');
  assert.ok(patentLabSource.includes('本次输出'), '阶段折叠区必须显示本次输出');
  assert.ok(patentLabSource.includes('律师或产品人员可直接阅读的阶段结果'), '阶段结果必须和技术输出分开');
  assert.ok(patentLabSource.includes("parameters.get('session')"), '专利分析实验室必须支持按 Agent session 恢复同一任务');
  assert.ok(patentLabSource.includes('/api/admin/patent-analysis/session/'), '同任务侵权诊断必须经管理员专用接口读取');
  assert.ok(patentLabSource.includes('管理员同任务只读诊断'), '侵权诊断必须明确标记管理员只读语义');
  assert.ok(patentLabSource.includes('data-patent-analysis-no-rerun'), '只读诊断必须明确禁用每个阶段的运行和补跑');
  assert.ok(patentLabSource.includes('if (isReadOnlyDiagnostic) return;'), '只读模式的执行处理器必须有不可变护栏');
  assert.ok(patentLabSource.includes('candidateSummary'), '商品检索失败时必须展示候选核验汇总');
  assert.ok(patentLabSource.includes('candidatesPreview'), '商品检索失败时必须保留候选及排除原因');
  assert.ok(!patentLabSource.includes('defaultValue='), '专利分析测试页的阶段和长内容必须默认收起');
  assert.ok(!patentLabSource.includes('模块1结果'), '专利分析实验室不得显示“模块1结果”');
  assert.ok(!patentLabSource.includes('module.number'), '专利分析实验室不得向用户显示技术模块编号');
  assert.ok(patentLabSource.includes('本页不会自动读取其他测试会话的历史结果'), '后续模块必须明确限定当前测试会话');
  for (const legacyTestHref of ['/test/module1', '/test/andun-search', 'href="/test"']) {
    assert.ok(!patentLabSource.includes(legacyTestHref), `专利分析统一测试页不得继续展示零散入口 ${legacyTestHref}`);
  }

  const legacyPatentTestSource = await readFile(path.join(projectRoot, 'src/app/test/page.tsx'), 'utf8');
  assert.ok(legacyPatentTestSource.includes("redirect('/test/product-pipeline')"), '旧测试首页必须收敛到唯一专利分析实验室');

  const infringementResultsSource = await readFile(path.join(projectRoot, 'src/app/results/page.tsx'), 'utf8');
  assert.ok(infringementResultsSource.includes('专利原文'), '侵权结果页必须使用业务名称打开专利原文');
  assert.ok(!infringementResultsSource.includes('模块1结果'), '侵权结果页不得继续显示“模块1结果”');

  const patentLabRouteSource = await readFile(path.join(projectRoot, 'src/app/api/test/product-pipeline/route.ts'), 'utf8');
  assert.ok(patentLabRouteSource.includes("'patentParse'"), '统一测试接口必须支持模块1解析动作');
  assert.ok(patentLabRouteSource.includes('runPatentParseStep'), '模块1必须通过统一测试接口执行并形成独立输出');
  assert.ok(patentLabRouteSource.includes("canonicalServiceUrl('PATENT_ANALYSIS_MODULE1_API_URL'"), '模块1必须使用唯一专利分析后端配置');
  assert.ok(patentLabRouteSource.includes("canonicalServiceUrl('PATENT_ANALYSIS_PRODUCT_SEARCH_API_URL'"), '商品检索必须使用唯一的新模块3后端配置');
  assert.ok(patentLabRouteSource.includes("'http://127.0.0.1:5107/run'"), '唯一商品检索后端必须保持模块实验室已调试的5107链路');
  assert.ok(patentLabRouteSource.includes('patentRecordId: patentRecordId > 0'), '模块1必须返回供本次会话后续模块使用的记录ID');

  const patentAdminDiagnosticRouteSource = await readFile(
    path.join(projectRoot, 'src/app/api/admin/patent-analysis/session/[id]/route.ts'),
    'utf8',
  );
  assert.ok(patentAdminDiagnosticRouteSource.includes("diagnostic_mode: 'same_task_read_only'"), '侵权管理员接口必须标记同任务只读语义');
  assert.ok(patentAdminDiagnosticRouteSource.includes("session.analysisKind !== 'infringement'"), '侵权管理员接口不得读取无效调查 session');
  assert.ok(patentAdminDiagnosticRouteSource.includes('user.status !== \'approved\' || !isAdmin(user)'), '侵权同任务诊断必须执行管理员权限校验');
  assert.ok(patentAdminDiagnosticRouteSource.includes('WHERE analysis_session_id = $1'), '模块运行恢复必须以精确 analysis_session_id 为数据边界');
  assert.ok(!patentAdminDiagnosticRouteSource.includes('updateSessionStatus'), '侵权管理员只读接口不得改写任务状态');
  assert.ok(!patentAdminDiagnosticRouteSource.includes("'/api/test/product-pipeline'"), '侵权管理员只读接口不得调用执行型测试 API');

  const invalidityLabSource = await readFile(path.join(projectRoot, 'src/app/test/module-lab/page.tsx'), 'utf8');
  assert.ok(invalidityLabSource.includes('href="/"'), '无效实验室必须直接返回 Agent');
  assert.ok(!invalidityLabSource.includes('href="/test"'), '无效实验室不得再引导到测试页目录');
  assert.ok(invalidityLabSource.includes("parameters.get('session')"), '无效实验室必须支持按 Agent session 恢复同一任务');
  assert.ok(invalidityLabSource.includes('/api/admin/invalidity/session/'), '同任务诊断必须经管理员专用接口读取');
  assert.ok(invalidityLabSource.includes('同任务只读'), '同任务诊断必须明确为只读模式');
  assert.ok(invalidityLabSource.includes('if (attachedSessionId || !investigationId'), '只读诊断不得触发模块六或模块九恢复执行器');
  assert.ok(invalidityLabSource.includes('attachedDerivedFacts'), '同任务只读视图必须从报告预览回填模块1/2/3的真实冻结事实');
  assert.ok(invalidityLabSource.includes('异常：未识别到申请日'), '模块1缺日期必须如实标注异常而非空白');
  assert.ok(invalidityLabSource.includes('异常：未提取到任何附图'), '模块1缺附图必须如实标注异常而非空白');

  const invalidityLabLayoutSource = await readFile(path.join(projectRoot, 'src/app/test/module-lab/layout.tsx'), 'utf8');
  assert.ok(invalidityLabLayoutSource.includes('!isAdmin(user)'), '无效实验室页面必须有服务端管理员权限门禁');
  const adminDiagnosticRouteSource = await readFile(
    path.join(projectRoot, 'src/app/api/admin/invalidity/session/[id]/route.ts'),
    'utf8',
  );
  assert.ok(adminDiagnosticRouteSource.includes("diagnostic_mode: 'same_task_read_only'"), '管理员接口必须标记同任务只读语义');
  assert.ok(adminDiagnosticRouteSource.includes('invalidityInvestigationId'), '管理员接口必须从 analysis session 解析唯一 investigation');
  assert.ok(!adminDiagnosticRouteSource.includes('updateSessionStatus'), '管理员只读接口不得推进或改写任务');

  const routeSource = agentMessageRouteSource;
  assert.ok(routeSource.includes("from '@/app/api/analyze/route'"));
  assert.ok(routeSource.includes("from '@/app/api/invalidity/investigations/route'"));
  assert.ok(routeSource.includes('prepareInvalidityUploadRoot(CANONICAL_INVALIDITY_ENVIRONMENT'));
  assert.ok(
    routeSource.includes('`invalidity-${CANONICAL_INVALIDITY_ENVIRONMENT}-${Date.now()}-${randomUUID()}-${suffix}`'),
    'Agent 保存的无效附件 fileKey 必须带唯一后端环境前缀',
  );
  assert.ok(
    !routeSource.includes('`invalidity-${Date.now()}-${randomUUID()}-${suffix}`'),
    'Agent 不得继续生成缺少环境身份的旧 fileKey',
  );
  assert.ok(routeSource.includes("form.getAll('analysisKind')"), '服务端必须读取显式分析选择');
  assert.ok(routeSource.includes('parsePatentAgentAnalysisKinds'), '服务端必须严格校验分析类型白名单');
  assert.ok(routeSource.includes('routePatentAgentMessage({ source, selectedAnalysisKinds })'), '服务端路由必须只消费显式选择和可用来源');
  assert.ok(!routeSource.includes("prepareInvalidityUploadRoot('prod'"), 'Agent 不得继续写入历史5109上传根');

  const analyzeRouteSource = await readFile(path.join(projectRoot, 'src/app/api/analyze/route.ts'), 'utf8');
  assert.ok(analyzeRouteSource.includes("POST as runCanonicalPatentAnalysisRoute"), 'Agent专利分析必须直接复用模块实验室执行器');
  assert.ok(analyzeRouteSource.includes('executeCanonicalPatentAnalysis('), 'Agent必须自动编排同一套四模块后端');
  assert.ok(!analyzeRouteSource.includes('executePipeline(sessionId, type'), '新用户请求不得再进入旧专利分析链');

  const invalidityRouteSource = await readFile(path.join(projectRoot, 'src/app/api/invalidity/investigations/route.ts'), 'utf8');
  assert.ok(invalidityRouteSource.includes('CANONICAL_INVALIDITY_ENVIRONMENT'), 'Agent与无效实验室必须解析到同一无效后端');
  assert.ok(
    invalidityRouteSource.includes('`invalidity-${CANONICAL_INVALIDITY_ENVIRONMENT}-${Date.now()}-${randomUUID()}.txt`'),
    'Agent 文本专利落盘键也必须绑定唯一无效后端身份',
  );
  assert.ok(
    !invalidityRouteSource.includes('`invalidity-${Date.now()}-${randomUUID()}.txt`'),
    '文本专利不得继续生成缺少环境身份的旧 fileKey',
  );
  assert.ok(!invalidityRouteSource.includes("requestInvalidityService<Record<string, unknown>>(\n      'prod'"), 'Agent不得再调用历史5109链');

  const agentSource = await readFile(path.join(projectRoot, 'src/lib/patent-agent.ts'), 'utf8');
  assert.ok(!agentSource.includes('你是专利工作台的意图路由器'), '后台工作流不得再由模型意图路由');
  assert.ok(agentSource.includes("if (selected.length === 0) return 'question'"), '无显式选择时必须确定性进入普通问答');
  assert.ok(agentSource.includes("PATENT_AGENT_TOOLSET_VERSION = 'patent-agent-tools-v2'"), '新任务必须绑定30秒确认工具版本');

  const continuationRouteSource = await readFile(
    path.join(projectRoot, 'src/app/api/agent/tool-runs/[id]/invalidity-continuation/route.ts'),
    'utf8',
  );
  assert.ok(continuationRouteSource.includes('getPatentAgentToolRun(id, user)'), '续检接口必须从 owner-scoped tool run 开始校验');
  assert.ok(continuationRouteSource.includes("investigationStatus !== 'partial'"), '自动续检只允许 partial 检查点');
  assert.ok(continuationRouteSource.includes('currentIteration <= AGENT_MAX_GAP_SEARCH_ROUNDS'), '服务端必须守住五轮上限');
  assert.ok(continuationRouteSource.includes('max_additional_rounds: 1'), '一次确认只能创建一个后续轮');
  assert.ok(continuationRouteSource.includes('expected_state_version: expectedStateVersion'), '续检必须使用 CAS 状态版本');
  assert.ok(continuationRouteSource.includes('`agent-gap-${tool.id}-${expectedStateVersion}`'), '续检必须使用稳定幂等键');
  assert.ok(!continuationRouteSource.includes('input?.claim_investigation_ids'), '浏览器不得指定待续检 claim');

  const storeSource = await readFile(path.join(projectRoot, 'src/lib/patent-agent-store.ts'), 'utf8');
  assert.ok(storeSource.includes('where id = $1 and user_id = $2'));
  assert.ok(storeSource.includes('where c.user_id = $1'));

  console.log('Patent agent contracts passed.');
}

void main();
