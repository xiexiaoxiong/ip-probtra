import { NextRequest, NextResponse } from 'next/server';
import { postJsonWithTimeout } from '@/lib/long-running-http';
import { requiredTestServiceUrl, requireTestUser } from '@/lib/test-route-guard';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

type JsonObject = Record<string, unknown>;

function baseUrl(): string {
  return requiredTestServiceUrl('TEST_ANDUN_MODULE3_API_URL', [5108]);
}

function asInteger(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((item) => String(item || '').trim()).filter(Boolean))];
}

async function readJsonResponse(response: Response): Promise<JsonObject> {
  const text = await response.text();
  try {
    return JSON.parse(text) as JsonObject;
  } catch {
    return { raw: text };
  }
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const unauthorized = await requireTestUser(request);
  if (unauthorized) return unauthorized;
  const startedAt = Date.now();
  try {
    const runId = Number(request.nextUrl.searchParams.get('runId') || 0);
    const target = runId > 0 ? `${baseUrl()}/runs/${runId}` : `${baseUrl()}/health`;
    const response = await fetch(target, {
      cache: 'no-store',
      signal: AbortSignal.timeout(15_000),
    });
    const data = await readJsonResponse(response);
    return NextResponse.json(
      { ...data, proxyElapsedMs: Date.now() - startedAt },
      { status: response.status },
    );
  } catch (error) {
    return NextResponse.json(
      {
        status: 'error',
        error: error instanceof Error ? error.message : String(error),
        proxyElapsedMs: Date.now() - startedAt,
      },
      { status: 502 },
    );
  }
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const unauthorized = await requireTestUser(request);
  if (unauthorized) return unauthorized;
  const startedAt = Date.now();
  try {
    const body = (await request.json()) as JsonObject;
    const keywords = stringList(body.keywords);
    if (keywords.length === 0) {
      return NextResponse.json({ error: '至少填写一个检索关键词' }, { status: 400 });
    }
    const platforms = stringList(body.platforms).map((item) => item.toUpperCase());
    const payload = {
      patent_record_id: asInteger(body.patentRecordId, 0, 0, Number.MAX_SAFE_INTEGER),
      analysis_session_id: String(body.analysisSessionId || '').trim(),
      input_keywords: keywords,
      task_name: String(body.taskName || '').trim(),
      platforms,
      max_keywords: asInteger(body.maxKeywords, 3, 1, 20),
      page_size: asInteger(body.pageSize, 100, 1, 100),
      max_pages: asInteger(body.maxPages, 10, 1, 100),
      max_products: asInteger(body.maxProducts, 300, 1, 5000),
      poll_interval_seconds: asInteger(body.pollIntervalSeconds, 10, 2, 120),
      max_wait_seconds: asInteger(body.maxWaitSeconds, 1800, 30, 14400),
      persist: true,
    };
    const response = await postJsonWithTimeout(`${baseUrl()}/async_run`, payload, {
      timeoutMs: 30_000,
    });
    let data: JsonObject;
    try {
      data = JSON.parse(response.text) as JsonObject;
    } catch {
      data = { raw: response.text };
    }
    return NextResponse.json(
      { ...data, proxyElapsedMs: Date.now() - startedAt },
      { status: response.status },
    );
  } catch (error) {
    return NextResponse.json(
      {
        status: 'error',
        error: error instanceof Error ? error.message : String(error),
        proxyElapsedMs: Date.now() - startedAt,
      },
      { status: 502 },
    );
  }
}

export async function PATCH(request: NextRequest): Promise<NextResponse> {
  const unauthorized = await requireTestUser(request);
  if (unauthorized) return unauthorized;
  const startedAt = Date.now();
  try {
    const body = (await request.json()) as JsonObject;
    const runId = asInteger(body.runId, 0, 1, Number.MAX_SAFE_INTEGER);
    if (runId <= 0) {
      return NextResponse.json({ error: '需要有效的本地 run ID' }, { status: 400 });
    }
    const response = await postJsonWithTimeout(`${baseUrl()}/runs/${runId}/resume`, {}, {
      timeoutMs: 30_000,
    });
    let data: JsonObject;
    try {
      data = JSON.parse(response.text) as JsonObject;
    } catch {
      data = { raw: response.text };
    }
    return NextResponse.json(
      { ...data, proxyElapsedMs: Date.now() - startedAt },
      { status: response.status },
    );
  } catch (error) {
    return NextResponse.json(
      {
        status: 'error',
        error: error instanceof Error ? error.message : String(error),
        proxyElapsedMs: Date.now() - startedAt,
      },
      { status: 502 },
    );
  }
}
