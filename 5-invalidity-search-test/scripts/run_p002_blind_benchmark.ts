#!/usr/bin/env node

import { createHash } from 'node:crypto';
import {
  buildPatsnapSearchPlan,
  moduleOutput,
  moduleRunStatus,
  patsnapSearchResult,
  type JsonObject,
  type PatsnapQueryRole,
} from '../../IP-protral/src/lib/invalidity-p002-test-flow';

type Arguments = {
  queryPlanRunId: string;
  investigationId: string;
  claimInvestigationId: string;
  criticalDate: string;
  expectedPublication: string;
  benchmarkId: string;
};

function argument(name: string, fallback = ''): string {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? String(process.argv[index + 1] || '').trim() : fallback;
}

function required(name: string): string {
  const value = argument(name);
  if (!value) throw new Error(`缺少 --${name}`);
  return value;
}

function parseArguments(): Arguments {
  return {
    queryPlanRunId: required('query-plan-run-id'),
    investigationId: required('investigation-id'),
    claimInvestigationId: required('claim-investigation-id'),
    criticalDate: required('critical-date'),
    expectedPublication: required('expected-publication'),
    benchmarkId: argument('benchmark-id', 'blind-d1-v1'),
  };
}

function asObject(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : {};
}

function rows(value: unknown): JsonObject[] {
  return Array.isArray(value)
    ? value.filter(
      (item): item is JsonObject => Boolean(item) && typeof item === 'object' && !Array.isArray(item),
    )
    : [];
}

function text(value: unknown): string {
  return value == null ? '' : String(value).trim();
}

function publicationKey(value: unknown): string {
  return text(value).toUpperCase().replace(/[^A-Z0-9]+/g, '');
}

function queryRole(value: unknown): PatsnapQueryRole {
  const role = text(value);
  if (role === 'inventive_point_precision') return role;
  if (role === 'title_abstract_concept') return role;
  if (role === 'gap_followup') return role;
  return 'claim_context_recall';
}

function idempotencyKey(benchmarkId: string, queryId: string): string {
  return `p002-blind-${createHash('sha256')
    .update(`${benchmarkId}\0${queryId}`)
    .digest('hex')
    .slice(0, 32)}`;
}

async function request(
  token: string,
  pathname: string,
  init?: RequestInit,
): Promise<JsonObject> {
  const response = await fetch(`http://127.0.0.1:5209${pathname}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      `测试 API ${response.status}: ${text(asObject(data).detail || asObject(data).error)}`,
    );
  }
  return asObject(data);
}

async function waitForRun(token: string, runId: string): Promise<JsonObject> {
  const deadline = Date.now() + 180_000;
  while (Date.now() < deadline) {
    const run = await request(
      token,
      `/v1/lab/module-runs/${encodeURIComponent(runId)}`,
    );
    const status = moduleRunStatus(run);
    if (status.terminal) {
      if (!status.successful) {
        throw new Error(`P002 运行 ${runId} 失败: ${status.error || status.runStatus}`);
      }
      return run;
    }
    await new Promise((resolve) => setTimeout(resolve, 750));
  }
  throw new Error(`P002 运行 ${runId} 超过 180 秒`);
}

async function main(): Promise<void> {
  const args = parseArguments();
  const token = text(process.env.INVALIDITY_TEST_API_TOKEN);
  if (!token) throw new Error('缺少 INVALIDITY_TEST_API_TOKEN');

  const queryPlanRun = await request(
    token,
    `/v1/lab/module-runs/${encodeURIComponent(args.queryPlanRunId)}`,
  );
  const output = moduleOutput(queryPlanRun);
  const ordinaryPatentQueries = rows(output.queries).filter((query) => (
    text(query.provider_kind) === 'patent'
    && text(query.date_channel || 'ordinary_prior_art') === 'ordinary_prior_art'
    && text(query.expression)
  ));
  if (ordinaryPatentQueries.length < 1) {
    throw new Error('冻结的 I2 结果没有普通现有技术专利检索式');
  }

  // Freeze/compile every query before the expected D1 identifier is used for
  // evaluation.  This prevents a benchmark answer from affecting generation.
  const compiled = ordinaryPatentQueries.map((query) => {
    const queryId = text(query.query_id);
    if (!queryId) throw new Error('I2 查询缺少 query_id');
    return buildPatsnapSearchPlan(
      queryPlanRun,
      args.criticalDate,
      10,
      'balanced',
      queryRole(query.query_role),
      queryId,
    );
  });
  const expectedKey = publicationKey(args.expectedPublication);
  if (
    compiled.some((plan) => publicationKey(plan.expression).includes(expectedKey))
  ) {
    throw new Error('盲测目标公开号出现在冻结检索式中，拒绝执行受污染基准');
  }

  const summaries: JsonObject[] = [];
  for (const plan of compiled) {
    const created = await request(token, '/v1/lab/module-runs', {
      method: 'POST',
      body: JSON.stringify({
        contract_version: 'v1',
        module_code: 'I3_PATENT_SEARCH',
        input_mode: 'live',
        investigation_id: args.investigationId,
        claim_investigation_id: args.claimInvestigationId,
        iteration_number: 1,
        input: {
          ...plan.input,
          query_strategy: plan.strategy,
        },
        idempotency_key: idempotencyKey(args.benchmarkId, plan.queryId),
      }),
    });
    const runId = text(created.module_run_id);
    const completed = await waitForRun(token, runId);
    const result = patsnapSearchResult(completed);
    summaries.push({
      query_id: plan.queryId,
      query_variant: plan.queryVariant,
      expression: plan.expression,
      module_run_id: runId,
      returned_count: result.returnedCount,
      total_result_count: result.totalResultCount,
      publications: result.candidates.slice(0, 10).map((candidate) => (
        candidate.publicationNumber || candidate.externalId
      )),
    });
  }

  const matched = summaries.flatMap((summary) => (
    Array.isArray(summary.publications) ? summary.publications : []
  )).some((publication) => publicationKey(publication) === expectedKey);
  process.stdout.write(`${JSON.stringify({
    benchmark_id: args.benchmarkId,
    query_plan_run_id: args.queryPlanRunId,
    query_generation_source: output.generation_source,
    frozen_query_count: compiled.length,
    expected_publication: args.expectedPublication,
    passed: matched,
    searches: summaries,
  }, null, 2)}\n`);
  if (!matched) process.exitCode = 2;
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
