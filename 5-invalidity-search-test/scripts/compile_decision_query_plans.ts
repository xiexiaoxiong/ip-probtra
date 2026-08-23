#!/usr/bin/env node

/** Compile frozen I2 outputs with the Portal's production P002 compiler. */

import { readFile, readdir, mkdir, writeFile, rename } from 'node:fs/promises';
import { join, relative, dirname } from 'node:path';
import {
  buildPatsnapSearchPlan,
  type JsonObject,
  type PatsnapQueryRole,
} from '../../IP-protral/src/lib/invalidity-p002-test-flow';

function argument(name: string): string {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? String(process.argv[index + 1] || '').trim() : '';
}

function text(value: unknown): string {
  return value == null ? '' : String(value).trim();
}

function role(value: unknown): PatsnapQueryRole {
  const candidate = text(value);
  if (candidate === 'inventive_point_precision') return candidate;
  if (candidate === 'title_abstract_concept') return candidate;
  if (candidate === 'gap_followup') return candidate;
  return 'claim_context_recall';
}

async function jsonFiles(root: string, current = root): Promise<string[]> {
  const result: string[] = [];
  for (const entry of await readdir(current, { withFileTypes: true })) {
    const path = join(current, entry.name);
    if (entry.isDirectory()) result.push(...await jsonFiles(root, path));
    else if (/claim-[^.]+\.json$/u.test(entry.name)) result.push(path);
  }
  return result.sort();
}

async function atomicWrite(path: string, value: unknown): Promise<void> {
  await mkdir(dirname(path), { recursive: true });
  const temporary = `${path}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  await rename(temporary, path);
}

async function main(): Promise<void> {
  const inputDir = argument('input-dir');
  const outputDir = argument('output-dir');
  if (!inputDir || !outputDir) throw new Error('必须提供 --input-dir 和 --output-dir');
  const summaries: JsonObject[] = [];
  for (const source of await jsonFiles(inputDir)) {
    const envelope = JSON.parse(await readFile(source, 'utf8')) as JsonObject;
    if (envelope.oracle_loaded !== false) throw new Error(`${source}: 不是盲测冻结输入`);
    const plan = envelope.plan as JsonObject;
    const queries = Array.isArray(plan.queries)
      ? plan.queries.filter((item): item is JsonObject => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
      : [];
    const ordinary = queries.filter((query) => (
      text(query.provider_kind) === 'patent'
      && text(query.date_channel || 'ordinary_prior_art') === 'ordinary_prior_art'
      && text(query.expression)
    ));
    const moduleRun = { module_run: { output_snapshot: { output: plan } } } as JsonObject;
    const compiled: JsonObject[] = [];
    for (const query of ordinary) {
      try {
        compiled.push(buildPatsnapSearchPlan(
          moduleRun,
          text(envelope.critical_date),
          10,
          'balanced',
          role(query.query_role),
          text(query.query_id),
        ));
      } catch (error) {
        throw new Error(
          `${text(envelope.case_id)} claim ${text(envelope.claim_id)} `
          + `${text(query.query_id)} ${text(query.expression)}: `
          + `${error instanceof Error ? error.message : String(error)}`,
        );
      }
    }
    const destination = join(outputDir, relative(inputDir, source));
    const output = {
      benchmark_phase: 'blind_p002_compile',
      oracle_loaded: false,
      source_plan_file: source,
      case_id: envelope.case_id,
      claim_id: envelope.claim_id,
      critical_date: envelope.critical_date,
      queries: compiled,
    };
    await atomicWrite(destination, output);
    summaries.push({ case_id: envelope.case_id, claim_id: envelope.claim_id, query_count: compiled.length, file: destination });
  }
  await atomicWrite(join(outputDir, 'compile-index.json'), {
    benchmark_phase: 'blind_p002_compile', oracle_loaded: false, plans: summaries,
  });
  process.stdout.write(`${JSON.stringify({ output_dir: outputDir, plans: summaries })}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
