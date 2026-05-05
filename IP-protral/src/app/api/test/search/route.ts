import { NextRequest, NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';
export const maxDuration = 1200; // 20 minutes timeout

const MODULE3_URL = process.env.MODULE3_API_URL || 'http://127.0.0.1:5105/run';
const MODULE3_TOKEN = process.env.MODULE3_API_TOKEN || '';

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (MODULE3_TOKEN) {
      headers['Authorization'] = `Bearer ${MODULE3_TOKEN}`;
    }

    const response = await fetch(MODULE3_URL, {
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