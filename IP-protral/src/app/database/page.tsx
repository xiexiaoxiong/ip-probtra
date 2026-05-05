'use client';

import { Suspense, useEffect, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import {
  ArrowLeft,
  Database,
  Loader2,
  RefreshCw,
  ExternalLink,
  ChevronDown,
  ChevronRight,
  ImageOff,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
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

interface DatabaseSnapshotResponse {
  session: {
    id: string;
    status: string;
    results?: Record<string, unknown> | null;
  };
  snapshot: {
    sessionId: string;
    ids: Record<string, number | null>;
    counts: Record<string, number>;
    tables: Record<string, unknown>;
  };
  error?: string;
}

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

function ProductTable({ products }: { products: SearchProduct[] }) {
  if (!products || products.length === 0) {
    return (
      <div className="text-sm text-muted-foreground py-8 text-center">
        暂无商品数据
      </div>
    );
  }

  const sourceOrder = ['1688', '淘宝', '天猫', '京东', '拼多多', '亚马逊', 'Alibaba', 'eBay'];
  const sortedProducts = [...products].sort((a, b) => {
    const aIdx = sourceOrder.indexOf(a.product_source || '') ?? 99;
    const bIdx = sourceOrder.indexOf(b.product_source || '') ?? 99;
    if (aIdx !== bIdx) return aIdx - bIdx;
    return parseFloat(a.price || '0') - parseFloat(b.price || '0');
  });

  return (
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
          {sortedProducts.map((product, index) => {
            const pictures: string[] = (() => {
              const raw = product.picture;
              if (Array.isArray(raw)) return raw.filter((u): u is string => typeof u === 'string');
              if (typeof raw === 'string') {
                try {
                  const parsed = JSON.parse(raw);
                  if (Array.isArray(parsed)) return parsed.filter((u): u is string => typeof u === 'string');
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
              <TableRow key={product.id}>
                <TableCell className="text-center text-xs text-muted-foreground">
                  {index + 1}
                </TableCell>
                <TableCell className="min-w-[200px]">
                  <div className="max-w-[280px]">
                    <p className="text-sm font-medium leading-snug line-clamp-2" title={product.product_name || ''}>
                      {product.product_name || '-'}
                    </p>
                    {product.description && (
                      <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1" title={product.description}>
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
                <TableCell className="text-sm">
                  {product.brand || '-'}
                </TableCell>
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
  );
}

function KeywordTable({ keywords }: { keywords: unknown[] }) {
  if (!keywords || keywords.length === 0) {
    return (
      <div className="text-sm text-muted-foreground py-8 text-center">
        暂无关键词数据
      </div>
    );
  }

  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[40px] text-center">#</TableHead>
            <TableHead>关键词</TableHead>
            <TableHead className="w-[100px]">行业</TableHead>
            <TableHead className="w-[120px]">状态</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {keywords.map((kw, index) => {
            const record = kw as Record<string, unknown>;
            return (
              <TableRow key={index}>
                <TableCell className="text-center text-xs text-muted-foreground">
                  {index + 1}
                </TableCell>
                <TableCell className="text-sm">
                  {(record.keyword_text as string) || (record.keyword as string) || '-'}
                </TableCell>
                <TableCell className="text-sm">
                  {(record.industry as string) || '-'}
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className="text-xs">
                    {(record.status as string) || '-'}
                  </Badge>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function CollapsibleSection({
  title,
  count,
  defaultOpen = false,
  children,
}: {
  title: string;
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <Card>
      <CardHeader
        className="py-3 cursor-pointer select-none"
        onClick={() => setOpen(!open)}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {open ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )}
            <CardTitle className="text-sm">{title}</CardTitle>
            <Badge variant="secondary" className="text-xs">
              {count}
            </Badge>
          </div>
        </div>
      </CardHeader>
      {open && <CardContent className="pt-0">{children}</CardContent>}
    </Card>
  );
}

function DatabaseViewerContent() {
  const searchParams = useSearchParams();
  const sessionId = searchParams.get('session');
  const [data, setData] = useState<DatabaseSnapshotResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (!sessionId) {
      setError('缺少 session 参数');
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(`/api/database/${sessionId}`);
      const payload = (await response.json()) as DatabaseSnapshotResponse;
      if (!response.ok) {
        throw new Error(payload.error || '读取数据库快照失败');
      }
      setData(payload);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '读取数据库快照失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [sessionId]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center space-y-3">
          <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto" />
          <p className="text-sm text-muted-foreground">读取数据库快照...</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background px-6">
        <div className="max-w-xl w-full space-y-4">
          <Alert>
            <AlertDescription>{error || '未读取到数据库内容'}</AlertDescription>
          </Alert>
          <div className="flex gap-3">
            <Link href={sessionId ? `/results?session=${sessionId}` : '/'}>
              <Button variant="outline">返回结果页</Button>
            </Link>
            <Button onClick={() => void load()}>重试</Button>
          </div>
        </div>
      </div>
    );
  }

  const { session, snapshot } = data;
  const tables = snapshot.tables as Record<string, unknown>;

  const products = (tables.searchProducts as SearchProduct[]) || [];
  const keywords = (tables.keywordRecords as unknown[]) || [];
  const searchRuns = (tables.searchRuns as unknown[]) || [];
  const patentClaims = (tables.patentClaims as unknown[]) || [];
  const patentFigures = (tables.patentFigures as unknown[]) || [];
  const claimResults = (tables.claimCompareResults as unknown[]) || [];

  const sourceStats = products.reduce<Record<string, number>>((acc, p) => {
    const src = p.product_source || 'unknown';
    acc[src] = (acc[src] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b bg-background/90 backdrop-blur sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href={sessionId ? `/results?session=${sessionId}` : '/'}>
              <Button variant="ghost" size="sm" className="gap-1.5">
                <ArrowLeft className="h-4 w-4" />
                返回
              </Button>
            </Link>
            <Separator orientation="vertical" className="h-5" />
            <div className="flex items-center gap-2">
              <Database className="h-4 w-4 text-primary" />
              <span className="text-sm font-medium">数据库详情</span>
            </div>
          </div>
          <Button variant="outline" size="sm" onClick={() => void load()} className="gap-1.5">
            <RefreshCw className="h-4 w-4" />
            刷新
          </Button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-6">
        {/* Session Summary */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">会话概要</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Badge variant="secondary">session: {session.id}</Badge>
              <Badge variant="outline">状态: {session.status}</Badge>
              {Object.entries(snapshot.ids).map(([key, value]) => (
                <Badge key={key} variant="outline">
                  {key}: {value ?? 'null'}
                </Badge>
              ))}
            </div>
            <div className="grid gap-3 md:grid-cols-5">
              {Object.entries(snapshot.counts).map(([key, value]) => (
                <div key={key} className="rounded-lg border bg-muted/20 p-3">
                  <div className="text-xl font-semibold">{value}</div>
                  <div className="text-xs text-muted-foreground">{key}</div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Product Table - Primary view */}
        <CollapsibleSection
          title={`商品结果 (search_products)`}
          count={products.length}
          defaultOpen={true}
        >
          {products.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-4">
              {Object.entries(sourceStats)
                .sort((a, b) => b[1] - a[1])
                .map(([source, count]) => (
                  <Badge key={source} variant={source === '1688' ? 'default' : 'outline'} className="text-xs">
                    {source}: {count}
                  </Badge>
                ))}
            </div>
          )}
          <ProductTable products={products} />
        </CollapsibleSection>

        {/* Keywords */}
        <CollapsibleSection
          title={`关键词 (keyword_records)`}
          count={keywords.length}
          defaultOpen={keywords.length > 0 && products.length === 0}
        >
          <KeywordTable keywords={keywords} />
        </CollapsibleSection>

        {/* Search Runs */}
        <CollapsibleSection
          title={`检索批次 (search_runs)`}
          count={searchRuns.length}
          defaultOpen={false}
        >
          <pre className="max-h-64 overflow-auto rounded-md bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap break-all">
            {JSON.stringify(searchRuns, null, 2)}
          </pre>
        </CollapsibleSection>

        {/* Claims */}
        <CollapsibleSection
          title={`权利要求 (patent_claims)`}
          count={patentClaims.length}
          defaultOpen={false}
        >
          <pre className="max-h-64 overflow-auto rounded-md bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap break-all">
            {JSON.stringify(patentClaims, null, 2)}
          </pre>
        </CollapsibleSection>

        {/* Figures */}
        <CollapsibleSection
          title={`附图 (patent_figures)`}
          count={patentFigures.length}
          defaultOpen={false}
        >
          <pre className="max-h-64 overflow-auto rounded-md bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap break-all">
            {JSON.stringify(patentFigures, null, 2)}
          </pre>
        </CollapsibleSection>

        {/* Claim Comparisons */}
        <CollapsibleSection
          title={`比对结果 (claim_compare_results)`}
          count={claimResults.length}
          defaultOpen={false}
        >
          <pre className="max-h-64 overflow-auto rounded-md bg-muted/40 p-3 text-xs leading-5 whitespace-pre-wrap break-all">
            {JSON.stringify(claimResults, null, 2)}
          </pre>
        </CollapsibleSection>
      </main>
    </div>
  );
}

export default function DatabaseViewerPage() {
  return (
    <Suspense fallback={null}>
      <DatabaseViewerContent />
    </Suspense>
  );
}