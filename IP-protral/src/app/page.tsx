'use client';

// ============================================================
// 页面1：专利上传与分析入口
// 用户上传专利文件或输入网址，触发本地工作流流水线分析
// ============================================================

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { UploadForm } from '@/components/upload-form';
import { AnalysisProgress } from '@/components/analysis-progress';
import { HistoryList } from '@/components/history/history-list';
import { useAnalysisStream } from '@/hooks/use-analysis';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import type { AnalysisSession, AuthUser } from '@/lib/types';
import { normalizeKeywordList } from '@/lib/keyword-utils';
import { AlertCircle, RotateCcw, ArrowRight, ExternalLink, Shield, Clock3, Plus } from 'lucide-react';

export default function HomePage() {
  const router = useRouter();
  const {
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
  } = useAnalysisStream();
  const [showProgress, setShowProgress] = useState(false);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [recentSessions, setRecentSessions] = useState<AnalysisSession[]>([]);
  const [keywordDialogOpen, setKeywordDialogOpen] = useState(false);
  const [keywordDraft, setKeywordDraft] = useState('');
  const [keywordDialogError, setKeywordDialogError] = useState<string | null>(null);
  const [countdownNow, setCountdownNow] = useState(() => Date.now());

  // 当步骤1-4完成（步骤5为可选的飞书读取，不影响主流程）
  const isCompleted = steps.filter((s) => s.id <= 5 && s.id >= 1).every((s) => s.status === 'completed' || s.status === 'error');

  useEffect(() => {
    if (isAnalyzing) {
      setShowProgress(true);
    }
  }, [isAnalyzing]);

  useEffect(() => {
    if (keywordConfirmation?.status === 'editing') {
      setKeywordDialogOpen(true);
      return;
    }

    if (!isWaitingForKeywordInput) {
      setKeywordDialogOpen(false);
    }
  }, [isWaitingForKeywordInput, keywordConfirmation?.status]);

  useEffect(() => {
    if (keywordConfirmation?.status !== 'timed_wait') {
      return;
    }

    setCountdownNow(Date.now());
    const timer = setInterval(() => setCountdownNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [keywordConfirmation?.status, keywordConfirmation?.deadlineAt]);

  useEffect(() => {
    async function load() {
      const [meRes, historyRes] = await Promise.all([fetch('/api/auth/me'), fetch('/api/history')]);
      if (meRes.status === 401 || historyRes.status === 401) {
        window.location.href = '/login';
        return;
      }
      if (!meRes.ok || !historyRes.ok) {
        return;
      }
      const meData = await meRes.json();
      const historyData = await historyRes.json();
      setCurrentUser(meData.user || null);
      setRecentSessions((historyData.sessions || []).slice(0, 3));
    }

    void load();
  }, []);

  const handleAnalysisStart = (data: { type: 'url' | 'file' | 'text'; url?: string; fileKey?: string; fileName?: string; fileUrl?: string; text?: string }) => {
    setShowProgress(true);
    startAnalysis(data);
  };

  const handleViewResults = () => {
    if (sessionId) {
      router.push(`/results?session=${sessionId}`);
    }
  };

  const handleReset = () => {
    reset();
    setShowProgress(false);
    setKeywordDialogOpen(false);
    setKeywordDraft('');
    setKeywordDialogError(null);
  };

  const handleLogout = async () => {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.href = '/login';
  };

  const handleStartKeywordEditing = async () => {
    if (!sessionId) {
      return;
    }

    setKeywordDialogError(null);

    try {
      await startKeywordEditing(sessionId);
      setKeywordDialogOpen(true);
    } catch (err) {
      setKeywordDialogError(err instanceof Error ? err.message : '进入关键词补充状态失败');
    }
  };

  const handleConfirmKeywords = async () => {
    if (!sessionId) {
      return;
    }

    const keywordList = normalizeKeywordList(keywordDraft);
    if (keywordList.length === 0) {
      setKeywordDialogError('请输入至少一个关键词');
      return;
    }

    setKeywordDialogError(null);

    try {
      await confirmKeywords(sessionId, keywordList);
      setKeywordDialogOpen(false);
    } catch (err) {
      setKeywordDialogError(err instanceof Error ? err.message : '确认增加关键词失败');
    }
  };

  const autoKeywords = keywordConfirmation?.autoKeywords ?? [];
  const countdownSeconds =
    keywordConfirmation?.status === 'timed_wait'
      ? Math.max(0, Math.ceil(((keywordConfirmation.deadlineAt ?? countdownNow) - countdownNow) / 1000))
      : null;
  const keywordPromptVisible =
    isWaitingForKeywordInput
    && (keywordConfirmation?.status === 'timed_wait' || keywordConfirmation?.status === 'editing');
  const pageTitle = isCompleted
    ? '分析完成'
    : isWaitingForKeywordInput
      ? '等待补充关键词'
      : isAnalyzing
        ? '正在分析中...'
        : '分析中断';
  const pageDescription = isCompleted
    ? '所有模块已完成，请查看分析结果'
    : isWaitingForKeywordInput
      ? keywordConfirmation?.status === 'editing'
        ? '步骤3已暂停，请补充关键词并确认后继续检索'
        : '系统已生成关键词，30 秒内如无操作将自动进入下一步检索'
      : isAnalyzing
        ? '系统正在执行6个分析步骤，请耐心等待'
        : '分析过程中出现异常';

  return (
    <div className="min-h-screen bg-gradient-to-b from-background to-muted/30">
      {/* 顶部导航栏 */}
      <header className="border-b bg-background/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-4xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-primary" />
            <h1 className="text-base font-semibold">IP-Probtra 专利侵权自动识别系统</h1>
          </div>
          <div className="flex items-center gap-2">
            {currentUser ? (
              <>
                <Link href="/history">
                  <Button size="sm" variant="outline">历史记录</Button>
                </Link>
                {currentUser.role === 'admin' ? (
                  <Link href="/admin">
                    <Button size="sm" variant="outline">管理后台</Button>
                  </Link>
                ) : null}
                <Badge variant="secondary" className="text-xs">{currentUser.name}</Badge>
                <Button size="sm" variant="ghost" onClick={handleLogout}>退出</Button>
              </>
            ) : null}
          </div>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-12">
        {!showProgress ? (
          /* ===== 上传区域 ===== */
          <div className="space-y-8">
            {/* 系统介绍 */}
            <div className="text-center space-y-3">
              <h2 className="text-2xl font-bold tracking-tight">
                上传专利文件，自动识别侵权风险
              </h2>
              <p className="text-muted-foreground max-w-xl mx-auto leading-relaxed">
                基于专利文本与市场商品信息，进行事实驱动、可回溯、可验证的侵权技术比对，
                输出 Claim Chart 级别的专业分析结果。
              </p>
            </div>

            {/* 原则标签 */}
            <div className="flex flex-wrap justify-center gap-2">
              {['事实驱动', '可回溯', '可验证', '严格比对'].map((tag) => (
                <Badge key={tag} variant="outline" className="text-xs">{tag}</Badge>
              ))}
            </div>

            {/* 上传表单 */}
            <div className="max-w-lg mx-auto">
              <UploadForm onSubmit={handleAnalysisStart} isAnalyzing={isAnalyzing} />
            </div>

            {recentSessions.length > 0 ? (
              <div className="max-w-4xl mx-auto">
                <HistoryList sessions={recentSessions} showUser={currentUser?.role === 'admin'} />
              </div>
            ) : null}

            {/* 分析流程说明 */}
            <div className="grid grid-cols-2 md:grid-cols-6 gap-3 max-w-4xl mx-auto mt-12">
              {[
                { step: 1, title: '专利文本解析', desc: '提取权利要求与说明书' },
                { step: 2, title: '行业识别', desc: '路由到对应关键词工作流' },
                { step: 3, title: '关键词生成', desc: '基于专利内容生成检索词' },
                { step: 4, title: '商品检索', desc: '检索市场中的相关商品' },
                { step: 5, title: '特征比对', desc: '生成 Claim Chart 比对表' },
                { step: 6, title: '结果汇总', desc: '整理工作流与飞书结果' },
              ].map((item) => (
                <div key={item.step} className="text-center space-y-1.5 p-3 rounded-lg bg-muted/30">
                  <div className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">
                    {item.step}
                  </div>
                  <p className="text-xs font-medium">{item.title}</p>
                  <p className="text-[10px] text-muted-foreground">{item.desc}</p>
                </div>
              ))}
            </div>
          </div>
        ) : (
          /* ===== 进度区域 ===== */
          <div className="space-y-6">
            <div className="text-center space-y-2">
              <h2 className="text-xl font-bold">{pageTitle}</h2>
              <p className="text-sm text-muted-foreground">{pageDescription}</p>
              {sessionId && (
                <div className="flex justify-center">
                  <Badge variant="outline" className="font-mono text-[11px]">
                    Session ID: {sessionId}
                  </Badge>
                </div>
              )}
            </div>

            {/* 进度展示 */}
            <div className="max-w-xl mx-auto">
              <AnalysisProgress steps={steps} />
            </div>

            {keywordPromptVisible && (
              <div className="max-w-xl mx-auto rounded-lg border border-amber-200 bg-amber-50/70 p-4 space-y-4 dark:border-amber-800 dark:bg-amber-950/20">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <Clock3 className="h-4 w-4 text-amber-600" />
                      <span className="text-sm font-semibold text-amber-900 dark:text-amber-200">
                        {keywordConfirmation?.status === 'editing' ? '等待你确认新增关键词' : '是否需要自己增加关键词？'}
                      </span>
                    </div>
                    <p className="text-sm text-amber-900/80 dark:text-amber-200/80">
                      {keywordConfirmation?.status === 'editing'
                        ? '系统已暂停自动继续，请在弹窗中输入要追加的一个或多个关键词，确认后再继续检索。'
                        : '下面是系统自动生成的全部关键词。30 秒内如无任何操作，将自动进入下一步检索。'}
                    </p>
                  </div>
                  {countdownSeconds !== null && (
                    <Badge variant="outline" className="border-amber-300 text-amber-700 dark:border-amber-700 dark:text-amber-300">
                      {countdownSeconds}s
                    </Badge>
                  )}
                </div>

                <div className="flex flex-wrap gap-2">
                  {autoKeywords.map((keyword) => (
                    <Badge key={keyword} variant="secondary" className="bg-background/80 text-foreground">
                      {keyword}
                    </Badge>
                  ))}
                </div>

                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="outline"
                    className="gap-2 border-amber-300 text-amber-800 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-900/30"
                    onClick={handleStartKeywordEditing}
                  >
                    <Plus className="h-4 w-4" />
                    {keywordConfirmation?.status === 'editing' ? '继续填写关键词' : '自己增加关键词'}
                  </Button>
                </div>
              </div>
            )}

            {/* 错误提示 */}
            {error && (
              <Alert variant="destructive" className="max-w-xl mx-auto">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            )}

            {/* 飞书链接（核心输出） */}
            {results?.feishuUrl && isCompleted && (
              <div className="max-w-xl mx-auto rounded-lg border bg-card p-4 space-y-3">
                <div className="flex items-center gap-2">
                  <Shield className="h-4 w-4 text-primary" />
                  <span className="text-sm font-medium">分析结果已写入飞书多维表格</span>
                </div>
                <a
                  href={results.feishuUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  {results.feishuUrl}
                </a>
                {/* 模块异常提示 */}
                {(results.module2Exception || results.module3Exception || results.module4Exception) && (
                  <div className="text-xs text-muted-foreground space-y-1 mt-2 pt-2 border-t">
                    {results.module2Exception && results.module2Exception !== 'SUCCESS' && (
                      <p>模块2（关键词生成）：{results.module2Exception}</p>
                    )}
                    {results.module3Exception && results.module3Exception !== 'SUCCESS' && (
                      <p>模块3（商品检索）：{results.module3Exception}</p>
                    )}
                    {results.module4Exception && results.module4Exception !== 'SUCCESS' && (
                      <p>模块4（特征比对）：{results.module4Exception}</p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* 操作按钮 */}
            <div className="flex justify-center gap-3">
              {isCompleted && sessionId && (
                <Button size="lg" onClick={handleViewResults} className="gap-2">
                  查看分析结果
                  <ArrowRight className="h-4 w-4" />
                </Button>
              )}
              {!isAnalyzing && (
                <Button variant="outline" size="lg" onClick={handleReset} className="gap-2">
                  <RotateCcw className="h-4 w-4" />
                  重新分析
                </Button>
              )}
            </div>

            <Dialog open={keywordDialogOpen} onOpenChange={setKeywordDialogOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>增加检索关键词</DialogTitle>
                  <DialogDescription>
                    输入一个或多个关键词，支持使用逗号、中文逗号或换行分隔。确认后系统会将它们追加到自动生成关键词中继续检索。
                  </DialogDescription>
                </DialogHeader>

                <div className="space-y-3">
                  <Textarea
                    value={keywordDraft}
                    onChange={(event) => {
                      setKeywordDraft(event.target.value);
                      if (keywordDialogError) {
                        setKeywordDialogError(null);
                      }
                    }}
                    placeholder={'例如：\n自动上台阶扫地机器人\n履带式越障清扫装置'}
                    rows={6}
                  />
                  {keywordDialogError && (
                    <p className="text-sm text-destructive">{keywordDialogError}</p>
                  )}
                </div>

                <DialogFooter>
                  <Button type="button" variant="outline" onClick={() => setKeywordDialogOpen(false)}>
                    关闭
                  </Button>
                  <Button type="button" onClick={handleConfirmKeywords}>
                    确认增加关键词
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
        )}
      </main>
    </div>
  );
}
