import { NextRequest, NextResponse } from 'next/server';
import { createUnauthorizedResponse, getCurrentUserFromRequest, isAdmin } from '@/lib/auth';
import { getSessionAsync, updateResults } from '@/lib/analysis-store';
import { normalizeKeywordList } from '@/lib/keyword-utils';
import type { KeywordConfirmationState } from '@/lib/types';

export const dynamic = 'force-dynamic';

type KeywordAction = 'start_editing' | 'confirm';

interface KeywordActionBody {
  action?: KeywordAction;
  keywords?: string[] | string;
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const currentUser = await getCurrentUserFromRequest(request);
  if (!currentUser) {
    return createUnauthorizedResponse(request);
  }

  const { id } = await params;
  const session = await getSessionAsync(id);

  if (!session) {
    return NextResponse.json({ error: '分析会话不存在' }, { status: 404 });
  }

  if (!isAdmin(currentUser) && session.userId !== currentUser.id) {
    return NextResponse.json({ error: '无权操作该分析会话' }, { status: 403 });
  }

  const body = (await request.json().catch(() => ({}))) as KeywordActionBody;
  const action = body.action;
  const currentState = session.results?.keywordConfirmation;

  if (!currentState) {
    return NextResponse.json({ error: '当前会话未处于关键词确认阶段' }, { status: 409 });
  }

  if (action === 'start_editing') {
    if (currentState.status !== 'timed_wait') {
      return NextResponse.json({ error: '当前会话已不允许进入关键词补充状态' }, { status: 409 });
    }

    const nextState: KeywordConfirmationState = {
      ...currentState,
      status: 'editing',
      autoKeywords: normalizeKeywordList(currentState.autoKeywords),
      userKeywords: normalizeKeywordList(currentState.userKeywords),
      finalKeywords: normalizeKeywordList(currentState.finalKeywords),
    };

    await updateResults(id, {
      keywordConfirmation: nextState,
      keywords: nextState.finalKeywords,
    });

    return NextResponse.json({ success: true, keywordConfirmation: nextState });
  }

  if (action === 'confirm') {
    if (currentState.status !== 'timed_wait' && currentState.status !== 'editing') {
      return NextResponse.json({ error: '当前会话已自动继续检索，无法再补充关键词' }, { status: 409 });
    }

    const userKeywords = normalizeKeywordList(body.keywords ?? []);
    if (userKeywords.length === 0) {
      return NextResponse.json({ error: '请输入至少一个关键词' }, { status: 400 });
    }

    const autoKeywords = normalizeKeywordList(currentState.autoKeywords);
    const finalKeywords = normalizeKeywordList([...autoKeywords, ...userKeywords]);
    const nextState: KeywordConfirmationState = {
      ...currentState,
      status: 'confirmed',
      autoKeywords,
      userKeywords,
      finalKeywords,
      confirmedAt: Date.now(),
    };

    await updateResults(id, {
      keywordConfirmation: nextState,
      keywords: finalKeywords,
    });

    return NextResponse.json({ success: true, keywordConfirmation: nextState });
  }

  return NextResponse.json({ error: '不支持的操作' }, { status: 400 });
}
