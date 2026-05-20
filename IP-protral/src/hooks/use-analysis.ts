'use client';

// ============================================================
// 分析流 Hook（轮询模式）
// POST /api/analyze 触发后台执行 → 轮询 /api/analysis/[id] 获取进度
// ============================================================

import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  AnalysisSession,
  AnalysisStep,
  AnalysisResults,
  KeywordConfirmationState,
  StepStatus,
} from '@/lib/types';
import { WORKFLOW_MODULES } from '@/lib/types';

interface UseAnalysisStreamReturn {
  sessionId: string | null;
  steps: AnalysisStep[];
  results: AnalysisResults | null;
  keywordConfirmation: KeywordConfirmationState | null;
  isWaitingForKeywordInput: boolean;
  isAnalyzing: boolean;
  error: string | null;
  startAnalysis: (body: { type: 'url' | 'file' | 'text'; url?: string; fileKey?: string; fileName?: string; fileUrl?: string; text?: string }) => void;
  startKeywordEditing: (sessionId: string) => Promise<void>;
  confirmKeywords: (sessionId: string, keywords: string[]) => Promise<void>;
  reset: () => void;
}

/** 轮询间隔（毫秒） */
const POLL_INTERVAL = 3000;

export function useAnalysisStream(): UseAnalysisStreamReturn {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [steps, setSteps] = useState<AnalysisStep[]>(
    WORKFLOW_MODULES.map((m) => ({ id: m.id, name: m.name, description: m.description, status: 'pending' as StepStatus })),
  );
  const [results, setResults] = useState<AnalysisResults | null>(null);
  const [keywordConfirmation, setKeywordConfirmation] = useState<KeywordConfirmationState | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const syncKeywordConfirmation = useCallback((nextState: KeywordConfirmationState | null) => {
    setKeywordConfirmation(nextState);
  }, []);

  // 停止轮询
  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
  }, []);

  // 轮询单次获取
  const pollOnce = useCallback(async (sid: string) => {
    try {
      const response = await fetch(`/api/analysis/${sid}?t=${Date.now()}`, {
        cache: 'no-store',
      });
      if (response.status === 401) {
        stopPolling();
        setIsAnalyzing(false);
        window.location.href = '/login';
        return;
      }
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('application/json')) return;

      const data = await response.json();
      const session: AnalysisSession | undefined = data.session;
      if (!session) return;

      // 同步步骤状态
      setSteps(session.steps);
      // 同步结果
      if (session.results) {
        setResults(session.results);
      }
      syncKeywordConfirmation(session.results?.keywordConfirmation ?? null);

      // 检查是否完成
      if (session.status === 'completed' || session.status === 'error') {
        setIsAnalyzing(false);
        stopPolling();

        if (session.status === 'error') {
          // 尝试从模块异常中提取错误信息
          const modErrors = session.steps
            .filter(s => s.status === 'error' && s.error)
            .map(s => `${s.name}: ${s.error}`);
          if (modErrors.length > 0) {
            setError(modErrors.join('\n'));
          }
        }
      }
    } catch (err) {
      console.warn('[useAnalysis] 轮询失败:', err);
    }
  }, [stopPolling, syncKeywordConfirmation]);

  // 启动轮询
  const startPolling = useCallback((sid: string) => {
    stopPolling();
    // 立即拉一次
    pollOnce(sid);
    // 定时轮询
    pollingRef.current = setInterval(() => pollOnce(sid), POLL_INTERVAL);
  }, [pollOnce, stopPolling]);

  // 组件卸载时停止轮询
  useEffect(() => {
    return () => stopPolling();
  }, [stopPolling]);

  const startAnalysis = useCallback(
    async (body: { type: 'url' | 'file' | 'text'; url?: string; fileKey?: string; fileName?: string; fileUrl?: string; text?: string }) => {
      // 重置状态
      setSteps(WORKFLOW_MODULES.map((m) => ({ id: m.id, name: m.name, description: m.description, status: 'pending' as StepStatus })));
      setResults(null);
      syncKeywordConfirmation(null);
      setError(null);
      setIsAnalyzing(true);
      stopPolling();

      try {
        const response = await fetch('/api/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });

        if (response.status === 401) {
          window.location.href = '/login';
          return;
        }

        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
          throw new Error('服务端返回非 JSON 响应，请检查服务是否正常');
        }

        const data = await response.json();

        if (!response.ok || !data.success) {
          throw new Error(data.error || `请求失败 (${response.status})`);
        }

        // 拿到 sessionId，开始轮询
        const sid: string = data.sessionId;
        setSessionId(sid);
        startPolling(sid);
      } catch (err) {
        setError(err instanceof Error ? err.message : '分析过程发生未知错误');
        setIsAnalyzing(false);
      }
    },
    [startPolling, stopPolling, syncKeywordConfirmation],
  );

  const startKeywordEditing = useCallback(async (sid: string) => {
    setError(null);

    try {
      const response = await fetch(`/api/analysis/${sid}/keywords`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'start_editing' }),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) {
        throw new Error(data.error || `请求失败 (${response.status})`);
      }

      const nextState = (data.keywordConfirmation ?? null) as KeywordConfirmationState | null;
      syncKeywordConfirmation(nextState);
      if (nextState) {
        setResults((prev) => ({
          ...(prev || {}),
          keywords: nextState.finalKeywords,
          keywordConfirmation: nextState,
        }));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '进入关键词补充状态失败');
      throw err;
    }
  }, [syncKeywordConfirmation]);

  const confirmKeywords = useCallback(async (sid: string, keywords: string[]) => {
    setError(null);

    try {
      const response = await fetch(`/api/analysis/${sid}/keywords`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'confirm', keywords }),
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) {
        throw new Error(data.error || `请求失败 (${response.status})`);
      }

      const nextState = (data.keywordConfirmation ?? null) as KeywordConfirmationState | null;
      syncKeywordConfirmation(nextState);
      if (nextState) {
        setResults((prev) => ({
          ...(prev || {}),
          keywords: nextState.finalKeywords,
          keywordConfirmation: nextState,
        }));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '确认关键词失败');
      throw err;
    }
  }, [syncKeywordConfirmation]);

  const reset = useCallback(() => {
    stopPolling();
    setSessionId(null);
    setSteps(WORKFLOW_MODULES.map((m) => ({ id: m.id, name: m.name, description: m.description, status: 'pending' as StepStatus })));
    setResults(null);
    syncKeywordConfirmation(null);
    setError(null);
    setIsAnalyzing(false);
  }, [stopPolling, syncKeywordConfirmation]);

  const isWaitingForKeywordInput = steps.some((step) => step.id === 3 && step.status === 'waiting_input');

  return {
    sessionId,
    steps,
    results,
    keywordConfirmation,
    isWaitingForKeywordInput,
    isAnalyzing,
    error,
    startAnalysis,
    startKeywordEditing,
    confirmKeywords,
    reset,
  };
}
