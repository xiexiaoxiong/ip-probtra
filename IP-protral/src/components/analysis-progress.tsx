'use client';

// ============================================================
// 分析进度组件
// 展示6个步骤的执行状态：pending / running / completed / error
// ============================================================

import type { AnalysisStep } from '@/lib/types';
import { CheckCircle2, Loader2, Circle, AlertCircle, Clock3 } from 'lucide-react';

interface AnalysisProgressProps {
  steps: AnalysisStep[];
}

export function AnalysisProgress({ steps }: AnalysisProgressProps) {
  const completedCount = steps.filter((s) => s.status === 'completed' || s.status === 'error').length;
  const errorCount = steps.filter((s) => s.status === 'error').length;
  const progress = (completedCount / steps.length) * 100;

  return (
    <div className="space-y-6">
      {/* 总进度条 */}
      <div className="space-y-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">分析进度</span>
          <span className="font-medium">{completedCount}/{steps.length} 步骤完成 {errorCount > 0 && `(${errorCount} 异常)`}</span>
        </div>
        <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all duration-700 ease-out"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      {/* 步骤列表 */}
      <div className="space-y-3">
        {steps.map((step, index) => (
          <div
            key={step.id}
            className={`flex items-start gap-4 rounded-lg border p-4 transition-all ${
              step.status === 'running'
                ? 'border-primary/30 bg-primary/5'
                : step.status === 'waiting_input'
                  ? 'border-amber-200 bg-amber-50/60 dark:border-amber-800 dark:bg-amber-950/20'
                : step.status === 'completed'
                  ? 'border-green-200 bg-green-50/50 dark:border-green-800 dark:bg-green-950/20'
                  : step.status === 'error'
                    ? 'border-red-200 bg-red-50/50 dark:border-red-800 dark:bg-red-950/20'
                    : 'border-muted bg-muted/20'
            }`}
          >
            {/* 状态图标 */}
            <div className="mt-0.5 shrink-0">
              {step.status === 'pending' && (
                <Circle className="h-5 w-5 text-muted-foreground" />
              )}
              {step.status === 'running' && (
                <Loader2 className="h-5 w-5 text-primary animate-spin" />
              )}
              {step.status === 'waiting_input' && (
                <Clock3 className="h-5 w-5 text-amber-600" />
              )}
              {step.status === 'completed' && (
                <CheckCircle2 className="h-5 w-5 text-green-600" />
              )}
              {step.status === 'error' && (
                <AlertCircle className="h-5 w-5 text-destructive" />
              )}
            </div>

            {/* 步骤内容 */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground">
                  步骤 {index + 1}
                </span>
                <h4 className={`text-sm font-semibold ${
                  step.status === 'running' ? 'text-primary' :
                  step.status === 'waiting_input' ? 'text-amber-700 dark:text-amber-400' :
                  step.status === 'completed' ? 'text-green-700 dark:text-green-400' :
                  step.status === 'error' ? 'text-destructive' :
                  'text-foreground'
                }`}>
                  {step.name}
                </h4>
              </div>
              <p className="text-xs text-muted-foreground mt-0.5">
                {step.description}
              </p>
              {step.error && (
                <p className="text-xs text-destructive mt-1">{step.error}</p>
              )}
              {step.status === 'running' && (
                <p className="text-xs text-primary mt-1">正在处理中...</p>
              )}
              {step.status === 'waiting_input' && (
                <p className="text-xs text-amber-700 mt-1 dark:text-amber-400">等待用户补充关键词...</p>
              )}
            </div>

            {/* 耗时 */}
            {step.status === 'completed' && step.startedAt && step.completedAt && (
              <span className="text-xs text-muted-foreground shrink-0">
                {((step.completedAt - step.startedAt) / 1000).toFixed(1)}s
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
