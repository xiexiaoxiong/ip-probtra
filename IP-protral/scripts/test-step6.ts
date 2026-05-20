/**
 * 步骤 6 结果提取测试脚本
 * 
 * 用法: npx tsx scripts/test-step6.ts
 * 
 * 测试内容:
 * 1. mapComparisonsFromApi - 从模块4 API 响应提取比对数据
 * 2. groupDbRowsByProduct - 将数据库扁平行分组为嵌套格式
 * 3. normalizeStatus - 状态标准化
 * 4. determineVerdict - 侵权判定
 * 5. enrichProductsFromDb - 补充商品详情（需要数据库连接）
 */

import type { ProductComparison, ProductInfo, MatchStatus, InfringementVerdict } from '@/lib/types';

// ============================================================
// 从 route.ts 提取的核心函数
// ============================================================

function getStringField(record: Record<string, unknown>, ...candidates: string[]): string {
  for (const key of candidates) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (Array.isArray(value)) {
      const text = value
        .map((item: unknown) => {
          if (typeof item === 'string') return item;
          if (item && typeof item === 'object' && 'text' in item) return (item as { text: string }).text;
          return '';
        })
        .join('')
        .trim();
      if (text) return text;
    }
  }
  return '';
}

function normalizeStatus(status: string): MatchStatus {
  if (!status) return 'uncertain';
  const lower = status.trim().toLowerCase();
  const compact = lower.replace(/[\s_-]+/g, '');
  if (
    lower.includes('不匹配')
    || lower.includes('不相同')
    || lower.includes('不同')
    || lower.includes('不一致')
    || lower.includes('区别')
    || lower.includes('no_match')
    || lower.includes('no-match')
    || lower.includes('no match')
    || lower.includes('not_match')
    || lower.includes('not-match')
    || lower.includes('not match')
    || compact.includes('nomatch')
    || compact.includes('notmatch')
  ) return 'not_matching';
  if (
    lower.includes('匹配')
    || lower.includes('matching')
    || /\bmatch\b/.test(lower)
    || lower.includes('相同')
    || lower.includes('一致')
    || lower.includes('等同')
  ) return 'matching';
  return 'uncertain';
}

function determineVerdict(elements: ProductComparison['claimElements']): InfringementVerdict {
  if (elements.length === 0) return 'uncertain';
  const matching = elements.filter(e => e.status === 'matching').length;
  const notMatching = elements.filter(e => e.status === 'not_matching').length;
  if (notMatching === 0 && matching === elements.length) return 'infringement_likely';
  if (notMatching > 0 && matching === 0) return 'no_infringement';
  return 'uncertain';
}

function mapComparisonsFromApi(rawResults: unknown[]): ProductComparison[] {
  if (!Array.isArray(rawResults) || rawResults.length === 0) return [];

  const productMap = new Map<string, {
    productId: string;
    productName: string;
    elements: ProductComparison['claimElements'];
  }>();

  for (const item of rawResults) {
    if (!item || typeof item !== 'object') continue;

    const record = item as Record<string, unknown>;

    const productId = getStringField(record, 'product_id', '商品ID', 'productId');
    const productName = getStringField(record, 'product_name', '商品名称', 'productName', '产品名称');

    const features = record['features'];
    if (Array.isArray(features) && features.length > 0) {
      const id = productId || productName || `product_${productMap.size + 1}`;
      if (!productMap.has(id)) {
        productMap.set(id, { productId: id, productName: productName || id, elements: [] });
      }

      for (const feat of features) {
        if (!feat || typeof feat !== 'object') continue;
        const f = feat as Record<string, unknown>;

        const featureText = getStringField(f, 'feature_text', '权利要求特征', '专利技术特征', 'featureElement', 'claimElement', 'claim_element');
        const evidence = getStringField(f, 'evidence', '商品特征', '产品技术特征', 'productFeature', 'product_feature', 'product_feature_evidence');
        const statusRaw = getStringField(f, 'comparison_result', 'status', '匹配状态', '比对结果', 'matchStatus', 'result');
        const reason = getStringField(f, 'reason', '推理过程', '比对分析', '分析', 'reasoning');
        const reasoningType = getStringField(f, 'reasoning_type', '推理类型');

        productMap.get(id)!.elements.push({
          claimElement: featureText || '',
          productFeature: evidence || '',
          status: normalizeStatus(statusRaw),
          reasoning: [reason, reasoningType].filter(Boolean).join(' | ') || '',
          patentReference: getStringField(f, 'claim_id') || undefined,
        });
      }
      continue;
    }

    const claimElement = getStringField(record, 'claim_element', '权利要求特征', '专利技术特征', 'claimElement', 'feature_text');
    const productFeature = getStringField(record, 'product_feature', '商品特征', '产品技术特征', 'productFeature', 'evidence');
    const status = getStringField(record, 'status', '匹配状态', '比对结果', 'matchStatus', 'comparison_result');
    const reasoning = getStringField(record, 'reasoning', '推理过程', '比对分析', '分析', 'reason');

    if (!claimElement && !productFeature) {
      if (productId || productName) {
        const id = productId || productName || `product_${productMap.size + 1}`;
        if (!productMap.has(id)) {
          productMap.set(id, { productId: id, productName: productName || id, elements: [] });
        }
      }
      continue;
    }

    const id = productId || `product_${productMap.size + 1}`;
    if (!productMap.has(id)) {
      productMap.set(id, { productId: id, productName: productName || id, elements: [] });
    }

    productMap.get(id)!.elements.push({
      claimElement: claimElement || '',
      productFeature: productFeature || '',
      status: normalizeStatus(status),
      reasoning: reasoning || '',
    });
  }

  return Array.from(productMap.values()).map(group => ({
    productId: group.productId,
    productName: group.productName,
    overallVerdict: determineVerdict(group.elements),
    claimElements: group.elements,
  }));
}

function groupDbRowsByProduct(rows: unknown[]): unknown[] {
  const productMap = new Map<string, { product_id: string; product_name: string; features: unknown[] }>();

  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const r = row as Record<string, unknown>;

    const rawProductId = r['product_id'];
    const rawProductName = r['product_name'];
    const productId = typeof rawProductId === 'string' && rawProductId.trim() ? rawProductId.trim()
      : typeof rawProductId === 'number' ? String(rawProductId)
      : '';
    const productName = typeof rawProductName === 'string' && rawProductName.trim() ? rawProductName.trim() : '';

    const key = productId || productName || `product_${productMap.size + 1}`;
    if (!productMap.has(key)) {
      productMap.set(key, { product_id: productId || key, product_name: productName || key, features: [] });
    }

    const feature: Record<string, unknown> = {};
    if (r['feature_id'] != null) feature['feature_id'] = r['feature_id'];
    if (r['feature_text'] != null) feature['feature_text'] = r['feature_text'];
    if (r['claim_id'] != null) feature['claim_id'] = r['claim_id'];
    if (r['evidence'] != null) feature['evidence'] = r['evidence'];
    if (r['comparison_result'] != null) feature['comparison_result'] = r['comparison_result'];
    if (r['reason'] != null) feature['reason'] = r['reason'];
    if (r['reasoning_type'] != null) feature['reasoning_type'] = r['reasoning_type'];
    if (r['raw_payload'] != null && typeof r['raw_payload'] === 'object') {
      const payload = r['raw_payload'] as Record<string, unknown>;
      for (const k of ['feature_id', 'feature_text', 'claim_id', 'evidence', 'comparison_result', 'reason', 'reasoning_type']) {
        if (payload[k] != null && !(k in feature)) feature[k] = payload[k];
      }
    }

    productMap.get(key)!.features.push(feature);
  }

  return Array.from(productMap.values());
}

// ============================================================
// 测试数据（基于 session analysis_1777910597406_1lhxrc）
// ============================================================

const TEST_CASES = {
  // 测试用例 1: 嵌套格式（模块4标准输出）
  nestedFormat: [
    {
      product_id: "5",
      product_name: "智能家用扫地机器人吸扫拖一体USB除尘器家居清",
      features: [
        {
          feature_id: "f1",
          feature_text: "一种扫地机器人，包括机身、驱动轮、边刷和吸尘模块",
          claim_id: "claim-1",
          evidence: "该产品为扫地机器人，具有吸尘功能，配备边刷",
          comparison_result: "匹配",
          reason: "商品描述中明确提到吸尘功能和边刷，与专利权利要求1的技术特征一致",
          reasoning_type: "literal_infringement",
        },
        {
          feature_id: "f2",
          feature_text: "所述机身底部设有驱动轮，由电机驱动",
          claim_id: "claim-2",
          evidence: "底部设有驱动轮，由电机驱动前进",
          comparison_result: "匹配",
          reason: "商品图片显示底部有驱动轮结构",
          reasoning_type: "literal_infringement",
        },
      ],
    },
    {
      product_id: "6",
      product_name: "爆款 智能扫地机器人 家用充电四合一 吸尘器扫地礼品 批发印logo",
      features: [
        {
          feature_id: "f1",
          feature_text: "一种扫地机器人，包括机身、驱动轮、边刷和吸尘模块",
          claim_id: "claim-1",
          evidence: "四合一功能包含吸尘和扫地",
          comparison_result: "匹配",
          reason: "四合一功能包含专利所述吸尘模块",
          reasoning_type: "literal_infringement",
        },
        {
          feature_id: "f3",
          feature_text: "所述边刷可拆卸更换",
          claim_id: "claim-3",
          evidence: "未提及边刷是否可拆卸",
          comparison_result: "不匹配",
          reason: "商品描述中未明确边刷可拆卸特性",
          reasoning_type: "missing_element",
        },
      ],
    },
  ],

  // 测试用例 2: 扁平格式（兼容旧版/飞书格式）
  flatFormat: [
    {
      product_id: "9",
      product_name: "智能扫地机器人全自动扫地拖地吸尘三合一体家用清洁吸尘器擦地机",
      claim_element: "一种扫地机器人，包括机身、驱动轮、边刷和吸尘模块",
      product_feature: "三合一体包含扫地、拖地、吸尘功能",
      status: "匹配",
      reasoning: "商品具备专利所述核心功能模块",
    },
    {
      product_id: "9",
      product_name: "智能扫地机器人全自动扫地拖地吸尘三合一体家用清洁吸尘器擦地机",
      claim_element: "所述机身底部设有驱动轮，由电机驱动",
      product_feature: "全自动运行，底部有驱动轮",
      status: "匹配",
      reasoning: "全自动运行暗示有电机驱动系统",
    },
    {
      product_id: "11",
      product_name: "家用多功能扫地机器人 喷雾五合一清洁机充电智能吸尘器 拓客",
      claim_element: "一种扫地机器人，包括机身、驱动轮、边刷和吸尘模块",
      product_feature: "五合一清洁机，包含吸尘功能",
      status: "匹配",
      reasoning: "五合一包含吸尘模块",
    },
    {
      product_id: "11",
      product_name: "家用多功能扫地机器人 喷雾五合一清洁机充电智能吸尘器 拓客",
      claim_element: "所述边刷可拆卸更换",
      product_feature: "未提及边刷结构",
      status: "不匹配",
      reasoning: "商品描述未提及边刷可拆卸特性",
    },
  ],

  // 测试用例 3: 数据库格式（扁平记录，需要分组）
  dbRows: [
    {
      id: 1,
      claim_compare_run_id: 3,
      product_id: "5",
      product_name: "智能家用扫地机器人吸扫拖一体USB除尘器家居清",
      feature_id: "f1",
      feature_text: "一种扫地机器人，包括机身、驱动轮、边刷和吸尘模块",
      claim_id: "claim-1",
      evidence: "该产品为扫地机器人，具有吸尘功能，配备边刷",
      comparison_result: "匹配",
      reason: "商品描述中明确提到吸尘功能和边刷",
      reasoning_type: "literal_infringement",
    },
    {
      id: 2,
      claim_compare_run_id: 3,
      product_id: "5",
      product_name: "智能家用扫地机器人吸扫拖一体USB除尘器家居清",
      feature_id: "f2",
      feature_text: "所述机身底部设有驱动轮，由电机驱动",
      claim_id: "claim-2",
      evidence: "底部设有驱动轮，由电机驱动前进",
      comparison_result: "匹配",
      reason: "商品图片显示底部有驱动轮结构",
      reasoning_type: "literal_infringement",
    },
    {
      id: 3,
      claim_compare_run_id: 3,
      product_id: "6",
      product_name: "爆款 智能扫地机器人 家用充电四合一 吸尘器扫地礼品 批发印logo",
      feature_id: "f1",
      feature_text: "一种扫地机器人，包括机身、驱动轮、边刷和吸尘模块",
      claim_id: "claim-1",
      evidence: "四合一功能包含吸尘和扫地",
      comparison_result: "匹配",
      reason: "四合一功能包含专利所述吸尘模块",
      reasoning_type: "literal_infringement",
    },
    {
      id: 4,
      claim_compare_run_id: 3,
      product_id: "6",
      product_name: "爆款 智能扫地机器人 家用充电四合一 吸尘器扫地礼品 批发印logo",
      feature_id: "f3",
      feature_text: "所述边刷可拆卸更换",
      claim_id: "claim-3",
      evidence: "未提及边刷是否可拆卸",
      comparison_result: "不匹配",
      reason: "商品描述中未明确边刷可拆卸特性",
      reasoning_type: "missing_element",
    },
  ],

  // 测试用例 4: 空数据
  emptyData: [],

  // 测试用例 5: 混合格式（部分嵌套，部分扁平）
  mixedFormat: [
    {
      product_id: "12",
      product_name: "智能扫地机器人家用扫吸拖三合一带地图APP控制自动回充源头工厂",
      features: [
        {
          feature_id: "f1",
          feature_text: "一种扫地机器人，包括机身、驱动轮、边刷和吸尘模块",
          claim_id: "claim-1",
          evidence: "扫吸拖三合一，包含吸尘模块",
          comparison_result: "匹配",
          reason: "三合一功能包含专利核心特征",
          reasoning_type: "literal_infringement",
        },
      ],
    },
    {
      product_id: "15",
      product_name: "包邮扫地机器人吸力强劲充电款二合一家用全自动清洁机扫吸拖吸尘",
      claim_element: "一种扫地机器人，包括机身、驱动轮、边刷和吸尘模块",
      product_feature: "二合一扫吸功能",
      status: "匹配",
      reasoning: "具备吸尘模块",
    },
  ],
};

// ============================================================
// 测试执行
// ============================================================

function printHeader(title: string) {
  console.log('\n' + '='.repeat(60));
  console.log(`  ${title}`);
  console.log('='.repeat(60));
}

function printResult(label: string, data: unknown) {
  console.log(`\n${label}:`);
  console.log(JSON.stringify(data, null, 2));
}

function runTests() {
  let passed = 0;
  let failed = 0;

  // 测试 1: 嵌套格式
  printHeader('测试 1: 嵌套格式（模块4标准输出）');
  try {
    const result = mapComparisonsFromApi(TEST_CASES.nestedFormat);
    printResult('映射结果', result);
    
    console.log('\n验证:');
    console.log(`  - 商品数量: ${result.length} (期望: 2)`);
    console.log(`  - 商品5的特征数: ${result[0]?.claimElements.length} (期望: 2)`);
    console.log(`  - 商品6的特征数: ${result[1]?.claimElements.length} (期望: 2)`);
    console.log(`  - 商品5 verdict: ${result[0]?.overallVerdict} (期望: infringement_likely)`);
    console.log(`  - 商品6 verdict: ${result[1]?.overallVerdict} (期望: uncertain)`);
    
    if (result.length === 2 && result[0]?.claimElements.length === 2) {
      console.log('  ✅ 通过');
      passed++;
    } else {
      console.log('  ❌ 失败');
      failed++;
    }
  } catch (e) {
    console.log('  ❌ 异常:', e);
    failed++;
  }

  // 测试 2: 扁平格式
  printHeader('测试 2: 扁平格式（兼容旧版/飞书格式）');
  try {
    const result = mapComparisonsFromApi(TEST_CASES.flatFormat);
    printResult('映射结果', result);
    
    console.log('\n验证:');
    console.log(`  - 商品数量: ${result.length} (期望: 2)`);
    console.log(`  - 商品9的特征数: ${result[0]?.claimElements.length} (期望: 2)`);
    console.log(`  - 商品11的特征数: ${result[1]?.claimElements.length} (期望: 2)`);
    
    if (result.length === 2 && result[0]?.claimElements.length === 2) {
      console.log('  ✅ 通过');
      passed++;
    } else {
      console.log('  ❌ 失败');
      failed++;
    }
  } catch (e) {
    console.log('  ❌ 异常:', e);
    failed++;
  }

  // 测试 3: 数据库格式（先分组，再映射）
  printHeader('测试 3: 数据库格式（分组 + 映射）');
  try {
    const grouped = groupDbRowsByProduct(TEST_CASES.dbRows);
    printResult('分组结果', grouped);
    
    const result = mapComparisonsFromApi(grouped);
    printResult('映射结果', result);
    
    console.log('\n验证:');
    console.log(`  - 分组后商品数: ${(grouped as unknown[]).length} (期望: 2)`);
    console.log(`  - 映射后商品数: ${result.length} (期望: 2)`);
    console.log(`  - 商品5的特征数: ${result[0]?.claimElements.length} (期望: 2)`);
    console.log(`  - 商品6的特征数: ${result[1]?.claimElements.length} (期望: 2)`);
    
    if (result.length === 2 && result[0]?.claimElements.length === 2) {
      console.log('  ✅ 通过');
      passed++;
    } else {
      console.log('  ❌ 失败');
      failed++;
    }
  } catch (e) {
    console.log('  ❌ 异常:', e);
    failed++;
  }

  // 测试 4: 空数据
  printHeader('测试 4: 空数据');
  try {
    const result = mapComparisonsFromApi(TEST_CASES.emptyData);
    printResult('映射结果', result);
    
    if (result.length === 0) {
      console.log('  ✅ 通过');
      passed++;
    } else {
      console.log('  ❌ 失败');
      failed++;
    }
  } catch (e) {
    console.log('  ❌ 异常:', e);
    failed++;
  }

  // 测试 5: 混合格式
  printHeader('测试 5: 混合格式（嵌套 + 扁平）');
  try {
    const result = mapComparisonsFromApi(TEST_CASES.mixedFormat);
    printResult('映射结果', result);
    
    console.log('\n验证:');
    console.log(`  - 商品数量: ${result.length} (期望: 2)`);
    console.log(`  - 商品12的特征数: ${result[0]?.claimElements.length} (期望: 1)`);
    console.log(`  - 商品15的特征数: ${result[1]?.claimElements.length} (期望: 1)`);
    
    if (result.length === 2) {
      console.log('  ✅ 通过');
      passed++;
    } else {
      console.log('  ❌ 失败');
      failed++;
    }
  } catch (e) {
    console.log('  ❌ 异常:', e);
    failed++;
  }

  // 测试 6: normalizeStatus
  printHeader('测试 6: normalizeStatus 状态标准化');
  const statusTests = [
    { input: '匹配', expected: 'matching' },
    { input: '不匹配', expected: 'not_matching' },
    { input: 'MATCH', expected: 'matching' },
    { input: 'NOT_MATCH', expected: 'not_matching' },
    { input: 'NO_MATCH', expected: 'not_matching' },
    { input: 'no-match', expected: 'not_matching' },
    { input: 'no match', expected: 'not_matching' },
    { input: '相同', expected: 'matching' },
    { input: '不同', expected: 'not_matching' },
    { input: '等同', expected: 'matching' },
    { input: '区别', expected: 'not_matching' },
    { input: '', expected: 'uncertain' },
    { input: 'unknown', expected: 'uncertain' },
  ];
  
  let statusPassed = 0;
  for (const test of statusTests) {
    const result = normalizeStatus(test.input);
    const ok = result === test.expected;
    if (ok) statusPassed++;
    console.log(`  ${ok ? '✅' : '❌'} "${test.input}" -> "${result}" (期望: "${test.expected}")`);
  }
  console.log(`\n  通过: ${statusPassed}/${statusTests.length}`);
  if (statusPassed === statusTests.length) {
    passed++;
  } else {
    failed++;
  }

  // 测试 7: determineVerdict
  printHeader('测试 7: determineVerdict 侵权判定');
  const verdictTests = [
    { 
      name: '全部匹配',
      elements: [
        { claimElement: 'a', productFeature: 'a', status: 'matching' as MatchStatus, reasoning: '' },
        { claimElement: 'b', productFeature: 'b', status: 'matching' as MatchStatus, reasoning: '' },
      ],
      expected: 'infringement_likely',
    },
    { 
      name: '全部不匹配',
      elements: [
        { claimElement: 'a', productFeature: 'x', status: 'not_matching' as MatchStatus, reasoning: '' },
        { claimElement: 'b', productFeature: 'y', status: 'not_matching' as MatchStatus, reasoning: '' },
      ],
      expected: 'no_infringement',
    },
    { 
      name: '混合',
      elements: [
        { claimElement: 'a', productFeature: 'a', status: 'matching' as MatchStatus, reasoning: '' },
        { claimElement: 'b', productFeature: 'x', status: 'not_matching' as MatchStatus, reasoning: '' },
      ],
      expected: 'uncertain',
    },
    { 
      name: '空',
      elements: [],
      expected: 'uncertain',
    },
  ];
  
  let verdictPassed = 0;
  for (const test of verdictTests) {
    const result = determineVerdict(test.elements);
    const ok = result === test.expected;
    if (ok) verdictPassed++;
    console.log(`  ${ok ? '✅' : '❌'} ${test.name}: "${result}" (期望: "${test.expected}")`);
  }
  console.log(`\n  通过: ${verdictPassed}/${verdictTests.length}`);
  if (verdictPassed === verdictTests.length) {
    passed++;
  } else {
    failed++;
  }

  // 汇总
  printHeader('测试汇总');
  console.log(`\n  通过: ${passed}`);
  console.log(`  失败: ${failed}`);
  console.log(`  总计: ${passed + failed}`);
  console.log('\n' + '='.repeat(60));
}

// 运行测试
runTests();
