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
import type { AnalysisSession, AuthUser } from '@/lib/types';
import { AlertCircle, RotateCcw, ArrowRight, ExternalLink, Shield } from 'lucide-react';

export default function HomePage() {
  const router = useRouter();
  const { sessionId, steps, results, isAnalyzing, error, startAnalysis, reset } = useAnalysisStream();
  const [showProgress, setShowProgress] = useState(false);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [recentSessions, setRecentSessions] = useState<AnalysisSession[]>([]);

  // 当步骤1-4完成（步骤5为可选的飞书读取，不影响主流程）
  const isCompleted = steps.filter((s) => s.id <= 5 && s.id >= 1).every((s) => s.status === 'completed' || s.status === 'error');

  useEffect(() => {
    if (isAnalyzing) {
      setShowProgress(true);
    }
  }, [isAnalyzing]);

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
  };

  const handleLogout = async () => {
    await fetch('/api/auth/logout', { method: 'POST' });
    window.location.href = '/login';
  };

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
              <h2 className="text-xl font-bold">
                {isCompleted ? '分析完成' : isAnalyzing ? '正在分析中...' : '分析中断'}
              </h2>
              <p className="text-sm text-muted-foreground">
                {isCompleted
                  ? '所有模块已完成，请查看分析结果'
                  : isAnalyzing
                    ? '系统正在执行6个分析步骤，请耐心等待'
                    : '分析过程中出现异常'}
              </p>
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
          </div>
        )}
      </main>
    </div>
  );
}
