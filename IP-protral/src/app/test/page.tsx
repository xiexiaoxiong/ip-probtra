'use client';

import { Suspense, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ArrowLeft,
  Loader2,
  Play,
  ExternalLink,
  ImageOff,
  Database,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

interface SearchProduct {
  id: number;
  product_id?: string;
  product_name?: string;
  product_url?: string;
  product_source?: string;
  price?: string;
  brand?: string;
  manufacturer?: string;
  matched_keywords?: string;
  description?: string;
  picture?: string[] | null;
  raw_payload?: Record<string, unknown>;
  created_at?: string;
}

type ModuleStatus = 'idle' | 'running' | 'completed' | 'error';

function ProductImageGallery({ urls, maxVisible = 3 }: { urls: string[]; maxVisible?: number }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? urls : urls.slice(0, maxVisible);
  const remaining = urls.length - visible.length;

  if (!urls || urls.length === 0) {
    return (
      <span className="text-xs text-muted-foreground flex items-center gap-1">
        <ImageOff className="h-3 w-3" />
        无图片
      </span>
    );
  }

  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((url, i) => (
        <a key={i} href={url} target="_blank" rel="noopener noreferrer">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={url}
            alt={`图片 ${i + 1}`}
            className="h-12 w-12 rounded border object-cover hover:scale-110 transition-transform"
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = 'none';
            }}
          />
        </a>
      ))}
      {!expanded && remaining > 0 && (
        <button
          onClick={() => setExpanded(true)}
          className="h-12 w-12 rounded border flex items-center justify-center text-xs text-muted-foreground hover:bg-muted"
        >
          +{remaining}
        </button>
      )}
    </div>
  );
}

function TestPageContent() {
  const [keywords, setKeywords] = useState('自动上台阶扫地机器人');
  const [patentRecordId, setPatentRecordId] = useState('');
  const [status, setStatus] = useState<ModuleStatus>('idle');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [products, setProducts] = useState<SearchProduct[]>([]);
  const [elapsedTime, setElapsedTime] = useState<number | null>(null);

  const runSearch = async () => {
    if (!keywords.trim()) {
      setError('请输入关键词');
      return;
    }

    if (!patentRecordId.trim()) {
      setError('请输入 Patent Record ID（可在数据库页面查看）');
      return;
    }

    setStatus('running');
    setError(null);
    setResult(null);
    setProducts([]);
    setElapsedTime(null);

    const keywordList = keywords
      .split(/[,，\n]/)
      .map((k) => k.trim())
      .filter(Boolean);

    const payload: Record<string, unknown> = {
      input_keywords: keywordList,
      patent_record_id: parseInt(patentRecordId.trim(), 10),
    };

    const startTime = Date.now();

    try {
      const response = await fetch('/api/test/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      const endTime = Date.now();
      setElapsedTime(((endTime - startTime) / 1000).toFixed(1) as unknown as number);

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`HTTP ${response.status}: ${text.slice(0, 500)}`);
      }

      const data = (await response.json()) as Record<string, unknown>;
      setResult(data);

      // Query the database for products
      if (data.search_run_id || data.patent_record_id) {
        await queryProducts(data);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setStatus('error');
      return;
    }

    setStatus('completed');
  };

  const queryProducts = async (moduleResult: Record<string, unknown>) => {
    try {
      const searchRunId = moduleResult.search_run_id;
      const patentRecordIdNum = moduleResult.patent_record_id;

      // Connect to PostgreSQL and query products
      const dbUrl = process.env.NEXT_PUBLIC_DATABASE_URL || '';
      if (!dbUrl) {
        // Try to query via the database API using a session
        return;
      }
    } catch {
      // Silently ignore DB query errors
    }
  };

  const fetchProductsFromDB = async (searchRunId: number) => {
    try {
      // Query products directly from PostgreSQL via API
      // This requires the session ID which we construct from our test
      const response = await fetch(`/api/test/products?search_run_id=${searchRunId}`);
      if (response.ok) {
        const data = (await response.json()) as SearchProduct[];
        setProducts(data);
      }
    } catch {
      // Ignore - products might be in the module result
    }
  };

  // When module result has search_run_id, try to fetch products
  useEffect(() => {
    if (result?.search_run_id) {
      void fetchProductsFromDB(result.search_run_id as number);
    }
  }, [result]);

  // Extract products from module result if available
  const moduleProducts: SearchProduct[] = (() => {
    if (!result) return [];
    const rawProducts = result.products || result.all_products || [];
    if (Array.isArray(rawProducts) && rawProducts.length > 0) {
      return rawProducts.map((p: unknown, i: number) => {
        const product = p as Record<string, unknown>;
        const pictures: string[] = (() => {
          const pic = product.picture;
          if (Array.isArray(pic)) return pic.filter((u): u is string => typeof u === 'string');
          return [];
        })();
        return {
          id: (product.product_id as number) || i + 1,
          product_id: String(product.product_id || i + 1),
          product_name: String(product.product_name || ''),
          product_url: String(product.product_url || ''),
          product_source: String(product.product_source || ''),
          price: String(product.price || '0'),
          brand: String(product.brand || ''),
          manufacturer: String(product.Manufacturer || product.manufacturer || ''),
          matched_keywords: String(product.matched_keywords || ''),
          description: String(product.product_raw_text || product.description || ''),
          picture: pictures,
        } as SearchProduct;
      });
    }
    return products;
  })();

  const sourceStats = moduleProducts.reduce<Record<string, number>>((acc, p) => {
    const src = p.product_source || 'unknown';
    acc[src] = (acc[src] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-background/90 backdrop-blur sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/database">
              <Button variant="ghost" size="sm" className="gap-1.5">
                <ArrowLeft className="h-4 w-4" />
                数据库
              </Button>
            </Link>
            <Link href="/">
              <Button variant="ghost" size="sm" className="gap-1.5">
                首页
              </Button>
            </Link>
          </div>
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-primary" />
            <span className="text-sm font-medium">模块3 商品检索测试</span>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-6">
        {/* Input Panel */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">检索参数</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">关键词（逗号或换行分隔）</label>
              <Textarea
                value={keywords}
                onChange={(e) => setKeywords(e.target.value)}
                placeholder="输入关键词，如：自动上台阶扫地机器人"
                rows={3}
                className="font-mono text-sm"
              />
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">
                Patent Record ID（必填，对应数据库中的专利记录 ID）
              </label>
              <Input
                value={patentRecordId}
                onChange={(e) => setPatentRecordId(e.target.value)}
                placeholder="如 15，可在数据库中查看已有记录"
                type="number"
              />
              <p className="text-xs text-muted-foreground">
                模块3需要关联一个专利记录。可在 /database 页面查看已有的 patent_record_id。
              </p>
            </div>

            <div className="flex items-center gap-3">
              <Button
                onClick={() => void runSearch()}
                disabled={status === 'running'}
                className="gap-1.5"
              >
                {status === 'running' ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                {status === 'running' ? '检索中...' : '开始检索'}
              </Button>
              {elapsedTime && <span className="text-sm text-muted-foreground">耗时 {elapsedTime}s</span>}
            </div>
          </CardContent>
        </Card>

        {/* Error Display */}
        {error && (
          <Alert variant="destructive">
            <AlertDescription className="whitespace-pre-wrap break-all text-sm">{error}</AlertDescription>
          </Alert>
        )}

        {/* Results */}
        {status === 'running' && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <span className="ml-3 text-sm text-muted-foreground">正在检索商品，请等待...</span>
          </div>
        )}

        {/* Module Output */}
        {result && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">模块输出</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2 mb-3">
                {result.search_run_id != null && (
                  <Badge variant="secondary">search_run_id: {String(result.search_run_id)}</Badge>
                )}
                {result.total_products_count != null && (
                  <Badge variant="outline">
                    商品数量: {String(result.total_products_count)}
                  </Badge>
                )}
                {result.patent_record_id != null && (
                  <Badge variant="outline">
                    patent_record_id: {String(result.patent_record_id)}
                  </Badge>
                )}
                {result.is_complete !== undefined && (
                  <Badge variant={result.is_complete ? 'default' : 'destructive'}>
                    {result.is_complete ? '完成' : '未完成'}
                  </Badge>
                )}
              </div>
              <details className="group">
                <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground">
                  查看原始 JSON 输出
                </summary>
                <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap break-all">
                  {JSON.stringify(result, null, 2)}
                </pre>
              </details>
            </CardContent>
          </Card>
        )}

        {/* Product Table */}
        {moduleProducts.length > 0 && (
          <Card>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base">
                  商品结果 ({moduleProducts.length})
                </CardTitle>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(sourceStats)
                    .sort((a, b) => b[1] - a[1])
                    .map(([source, count]) => (
                      <Badge
                        key={source}
                        variant={source === '1688' ? 'default' : 'outline'}
                        className="text-xs"
                      >
                        {source}: {count}
                      </Badge>
                    ))}
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <div className="rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[40px] text-center">#</TableHead>
                      <TableHead className="min-w-[200px]">商品名称</TableHead>
                      <TableHead className="w-[80px]">来源</TableHead>
                      <TableHead className="w-[80px] text-right">价格</TableHead>
                      <TableHead className="w-[100px]">品牌</TableHead>
                      <TableHead className="w-[120px]">关键词</TableHead>
                      <TableHead className="w-[120px]">图片</TableHead>
                      <TableHead className="w-[80px] text-center">链接</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {moduleProducts.map((product, index) => {
                      const pictures: string[] = (() => {
                        const raw = product.picture;
                        if (Array.isArray(raw)) return raw.filter((u): u is string => typeof u === 'string');
                        if (typeof raw === 'string') {
                          try {
                            const parsed = JSON.parse(raw);
                            if (Array.isArray(parsed))
                              return parsed.filter((u): u is string => typeof u === 'string');
                          } catch {
                            return [];
                          }
                        }
                        return [];
                      })();
                      const price = product.price;
                      const priceDisplay =
                        price && !isNaN(Number(price)) && Number(price) > 0
                          ? `¥${Number(price).toLocaleString()}`
                          : '-';

                      return (
                        <TableRow key={product.id || index}>
                          <TableCell className="text-center text-xs text-muted-foreground">
                            {index + 1}
                          </TableCell>
                          <TableCell className="min-w-[200px]">
                            <div className="max-w-[280px]">
                              <p
                                className="text-sm font-medium leading-snug line-clamp-2"
                                title={product.product_name || ''}
                              >
                                {product.product_name || '-'}
                              </p>
                              {product.description && (
                                <p
                                  className="text-xs text-muted-foreground mt-0.5 line-clamp-1"
                                  title={product.description}
                                >
                                  {product.description}
                                </p>
                              )}
                            </div>
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant={
                                product.product_source === '1688'
                                  ? 'default'
                                  : product.product_source === '淘宝'
                                    ? 'secondary'
                                    : 'outline'
                              }
                              className="text-xs"
                            >
                              {product.product_source || 'unknown'}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-right text-sm font-medium tabular-nums">
                            {priceDisplay}
                          </TableCell>
                          <TableCell className="text-sm">{product.brand || '-'}</TableCell>
                          <TableCell className="text-sm">
                            {product.matched_keywords || '-'}
                          </TableCell>
                          <TableCell>
                            <ProductImageGallery urls={pictures} />
                          </TableCell>
                          <TableCell className="text-center">
                            {product.product_url ? (
                              <a
                                href={product.product_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-0.5 text-xs text-primary hover:underline"
                              >
                                <ExternalLink className="h-3 w-3" />
                              </a>
                            ) : (
                              <span className="text-xs text-muted-foreground">-</span>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  );
}

export default function TestPage() {
  return (
    <Suspense fallback={null}>
      <TestPageContent />
    </Suspense>
  );
}