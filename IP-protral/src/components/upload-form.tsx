'use client';

// ============================================================
// 上传表单组件
// 支持 URL 输入、文件上传、直接粘贴文本三种模式
// ============================================================

import { useState, useCallback, useRef } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent } from '@/components/ui/card';
import { Upload, Link, FileText, X, Loader2, ClipboardPaste } from 'lucide-react';

interface UploadFormProps {
  onSubmit: (data: { type: 'url' | 'file' | 'text'; url?: string; fileKey?: string; fileName?: string; fileUrl?: string; text?: string }) => void;
  isAnalyzing: boolean;
}

export function UploadForm({ onSubmit, isAnalyzing }: UploadFormProps) {
  const [activeTab, setActiveTab] = useState<'url' | 'file' | 'text'>('text');
  const [url, setUrl] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [patentText, setPatentText] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) {
      setFile(selected);
      setUploadError(null);
    }
  }, []);

  const removeFile = useCallback(() => {
    setFile(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, []);

  const handleSubmit = useCallback(async () => {
    if (activeTab === 'url') {
      if (!url.trim()) return;
      onSubmit({ type: 'url', url: url.trim() });
    } else if (activeTab === 'text') {
      if (!patentText.trim()) return;
      onSubmit({ type: 'text', text: patentText.trim() });
    } else {
      if (!file) return;

      setUploading(true);
      setUploadError(null);

      try {
        const formData = new FormData();
        formData.append('file', file);

        const response = await fetch('/api/upload', {
          method: 'POST',
          body: formData,
        });

        const contentType = response.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
          throw new Error('服务端返回非 JSON 响应，请检查服务是否正常启动');
        }

        const result = await response.json();

        if (!response.ok || !result.success) {
          throw new Error(result.error || '文件上传失败');
        }

        onSubmit({
          type: 'file',
          fileKey: result.fileKey,
          fileName: result.fileName,
          fileUrl: result.fileUrl,
        });
      } catch (err) {
        setUploadError(err instanceof Error ? err.message : '文件上传失败');
      } finally {
        setUploading(false);
      }
    }
  }, [activeTab, url, patentText, file, onSubmit]);

  const canSubmit =
    !isAnalyzing &&
    !uploading &&
    (activeTab === 'url' ? url.trim().length > 0 : activeTab === 'text' ? patentText.trim().length > 0 : file !== null);

  return (
    <div className="space-y-6">
      <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'url' | 'file' | 'text')}>
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="text" className="gap-2">
            <ClipboardPaste className="h-4 w-4" />
            粘贴文本
          </TabsTrigger>
          <TabsTrigger value="url" className="gap-2">
            <Link className="h-4 w-4" />
            输入网址
          </TabsTrigger>
          <TabsTrigger value="file" className="gap-2">
            <Upload className="h-4 w-4" />
            上传文件
          </TabsTrigger>
        </TabsList>

        <TabsContent value="text" className="mt-4">
          <Card>
            <CardContent className="pt-6">
              <div className="space-y-3">
                <label className="text-sm font-medium text-foreground">
                  专利文本内容
                </label>
                <textarea
                  placeholder="请粘贴专利文本内容，包括权利要求书、说明书等"
                  value={patentText}
                  onChange={(e) => setPatentText(e.target.value)}
                  disabled={isAnalyzing}
                  className="flex min-h-[240px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 resize-y"
                />
                <p className="text-xs text-muted-foreground">
                  直接粘贴专利的权利要求书和说明书文本，分析效果最佳
                </p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="url" className="mt-4">
          <Card>
            <CardContent className="pt-6">
              <div className="space-y-3">
                <label className="text-sm font-medium text-foreground">
                  专利文件网址
                </label>
                <Input
                  type="url"
                  placeholder="请输入专利文件或专利详情页的网址"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  disabled={isAnalyzing}
                  className="h-11"
                />
                <p className="text-xs text-muted-foreground">
                  支持专利公开网站、专利文档下载链接等（部分网站可能无法抓取）
                </p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="file" className="mt-4">
          <Card>
            <CardContent className="pt-6">
              <div className="space-y-3">
                <label className="text-sm font-medium text-foreground">
                  上传专利文件
                </label>

                {!file ? (
                  <div
                    onClick={() => fileInputRef.current?.click()}
                    className="flex flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/25 bg-muted/30 px-6 py-10 cursor-pointer transition-colors hover:border-primary/50 hover:bg-muted/50"
                  >
                    <Upload className="h-8 w-8 text-muted-foreground mb-3" />
                    <p className="text-sm font-medium text-foreground">
                      点击选择文件或拖拽到此处
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      支持 PDF、DOCX、TXT 格式，最大 50MB
                    </p>
                  </div>
                ) : (
                  <div className="flex items-center gap-3 rounded-lg border bg-muted/30 p-4">
                    <FileText className="h-8 w-8 text-primary shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{file.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {(file.size / 1024 / 1024).toFixed(2)} MB
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={removeFile}
                      disabled={isAnalyzing || uploading}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>
                )}

                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf,.doc,.docx,.txt"
                  onChange={handleFileSelect}
                  className="hidden"
                  disabled={isAnalyzing}
                />

                {uploadError && (
                  <p className="text-sm text-destructive">{uploadError}</p>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Button
        size="lg"
        className="w-full h-12 text-base"
        disabled={!canSubmit}
        onClick={handleSubmit}
      >
        {uploading ? (
          <>
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            上传文件中...
          </>
        ) : isAnalyzing ? (
          <>
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            分析进行中...
          </>
        ) : (
          '开始分析'
        )}
      </Button>
    </div>
  );
}
