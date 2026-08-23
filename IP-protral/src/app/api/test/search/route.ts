import { NextRequest, NextResponse } from 'next/server';
import { requiredTestServiceUrl, requireTestUser } from '@/lib/test-route-guard';

export const dynamic = 'force-dynamic';
export const maxDuration = 1200; // 20 minutes timeout

export async function POST(request: NextRequest) {
  const unauthorized = await requireTestUser(request);
  if (unauthorized) return unauthorized;
  try {
    const body = await request.json();
    const module3Url = requiredTestServiceUrl('TEST_MODULE3_API_URL', [5105]);
    const module3Token = process.env.TEST_MODULE3_API_TOKEN || '';

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (module3Token) {
      headers['Authorization'] = `Bearer ${module3Token}`;
    }

    const response = await fetch(module3Url, {
      method: 'POST',
      headers,
      body: JSON.stringify(body),
    });

    const text = await response.text();

    let data: unknown;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }

    if (!response.ok) {
      return NextResponse.json(
        { error: `Module 3 returned HTTP ${response.status}`, detail: data },
        { status: response.status },
      );
    }

    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : String(error),
      },
      { status: 500 },
    );
  }
}
