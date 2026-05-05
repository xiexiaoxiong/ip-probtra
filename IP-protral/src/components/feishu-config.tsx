'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import type { ProductInfo, ProductComparison } from '@/lib/types';

interface FeishuConfigProps {
  feishuUrl: string;
  onResultsLoaded: (data: {
    products: ProductInfo[];
    comparisons: ProductComparison[];
  }) => void;
}

export function FeishuConfig({ feishuUrl, onResultsLoaded }: FeishuConfigProps) {
  const [appId, setAppId] = useState('');
  const [appSecret, setAppSecret] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleFetch = async () => {
    if (!appId.trim() || !appSecret.trim()) {
      setError('请填写 App ID 和 App Secret');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/api/feishu-read', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          feishuUrl,
          appId: appId.trim(),
          appSecret: appSecret.trim(),
        }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || '读取飞书数据失败');
      }

      const data = await response.json();
      onResultsLoaded(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : '未知错误');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card className="mt-4 max-w-md mx-auto">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">从飞书读取分析结果</CardTitle>
        <CardDescription className="text-xs">
          输入飞书开放平台凭证，直接从多维表格读取分析数据
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div>
          <label className="text-xs font-medium text-muted-foreground">App ID</label>
          <Input
            value={appId}
            onChange={(e) => setAppId(e.target.value)}
            placeholder="cli_xxxxxxxx"
            className="h-8 text-xs mt-1"
          />
        </div>
        <div>
          <label className="text-xs font-medium text-muted-foreground">App Secret</label>
          <Input
            value={appSecret}
            onChange={(e) => setAppSecret(e.target.value)}
            type="password"
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            className="h-8 text-xs mt-1"
          />
        </div>
        {error && (
          <p className="text-xs text-destructive">{error}</p>
        )}
        <Button
          onClick={handleFetch}
          disabled={loading || !appId.trim() || !appSecret.trim()}
          size="sm"
          className="w-full"
        >
          {loading ? '读取中...' : '读取飞书数据'}
        </Button>
      </CardContent>
    </Card>
  );
}
