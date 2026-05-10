import { mkdir, writeFile } from 'fs/promises';
import path from 'path';
import { randomUUID } from 'crypto';
import { promisify } from 'util';
import { execFile } from 'child_process';
import { NextRequest, NextResponse } from 'next/server';
import { getUploadsDir } from '@/lib/runtime-paths';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';

const execFileAsync = promisify(execFile);

type Module1TestInput = {
  type: 'url' | 'file' | 'text';
  url?: string;
  fileKey?: string;
  fileName?: string;
  fileUrl?: string;
  text?: string;
};

type FileReadNodeResult = {
  raw_text?: string;
  file_format?: string | null;
  read_error?: {
    error_type?: string;
    error_message?: string;
    is_recoverable?: boolean;
  } | null;
};

type StructureIdentifyNodeResult = {
  specification_sections?: Array<{
    section_name?: string;
    section_text?: string;
    start_position?: number;
    end_position?: number;
  }>;
  patent_metadata?: Record<string, string | null>;
  claims_section_text?: string;
  identify_errors?: Array<Record<string, unknown>>;
};

type ClaimsParseNodeResult = {
  claims_list?: Array<Record<string, unknown>>;
  claims_errors?: Array<Record<string, unknown>>;
};

type FigureExtractResult = {
  figures: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
};

function getModule1Config(): { url: string; token?: string } {
  return {
    url: process.env.MODULE1_API_URL || 'http://127.0.0.1:5101/run',
    token: process.env.MODULE1_API_TOKEN || undefined,
  };
}

function createHeaders(token?: string): HeadersInit {
  return token
    ? {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      }
    : {
        'Content-Type': 'application/json',
      };
}

function getModule1NodeRunUrl(nodeId: string): string {
  const runUrl = getModule1Config().url;
  if (runUrl.endsWith('/run')) {
    return runUrl.replace(/\/run$/, `/node_run/${nodeId}`);
  }
  return `${runUrl.replace(/\/$/, '')}/node_run/${nodeId}`;
}

async function persistTextInput(text: string): Promise<string> {
  const uploadsDir = getUploadsDir();
  await mkdir(uploadsDir, { recursive: true });

  const filePath = path.join(uploadsDir, `module1-test-${Date.now()}-${randomUUID()}.txt`);
  await writeFile(filePath, text, 'utf-8');
  return filePath;
}

async function readModule1RawText(patentFileUrl: string): Promise<FileReadNodeResult> {
  return callModule1Node<FileReadNodeResult>('file_read_node', {
    patent_file: {
      url: patentFileUrl,
      file_type: 'image',
    },
  });
}

async function callModule1Node<T>(nodeId: string, payload: Record<string, unknown>): Promise<T> {
  const config = getModule1Config();
  const response = await fetch(getModule1NodeRunUrl(nodeId), {
    method: 'POST',
    headers: createHeaders(config.token),
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(10 * 60 * 1000),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`${nodeId} 执行失败: HTTP ${response.status} ${text.slice(0, 300)}`);
  }

  return (await response.json()) as T;
}

function extractFigureDescriptions(
  sections: StructureIdentifyNodeResult['specification_sections'],
): Record<string, string> {
  const result: Record<string, string> = {};
  for (const section of sections || []) {
    const sectionName = section.section_name || '';
    let sectionText = section.section_text || '';
    if (!sectionName.includes('附图说明')) continue;
    sectionText = sectionText.replace(/”|"/g, '');
    sectionText = sectionText.replace(/图\]/g, '图1');
    sectionText = sectionText.replace(/图[Il]/g, '图1');
    const matches = sectionText.matchAll(
      /((?:图\s*\d+\s*(?:和|及|以及|、|,|，)?\s*)+)\s*[是为：:]\s*([^\n]+)/g,
    );
    for (const match of matches) {
      const description = match[2]?.trim().replace(/[，,；;。]+$/, '');
      if (!description) continue;
      const figureIds = Array.from(match[1]?.matchAll(/图\s*\d+/g) || []).map((item) =>
        item[0].replace(/\s+/g, ''),
      );
      for (const figureId of figureIds) {
        if (figureId) {
          result[figureId] = description;
        }
      }
    }
  }
  return result;
}

async function extractFiguresLocally(
  patentFileUrl: string,
  descriptions: Record<string, string>,
): Promise<FigureExtractResult> {
  const projectRoot = process.cwd();
  const runId = randomUUID();
  const uploadsDir = getUploadsDir();
  await mkdir(uploadsDir, { recursive: true });
  const outputDir = path.join(uploadsDir, 'module1-test-figures', runId);
  await mkdir(outputDir, { recursive: true });

  const descriptionsFile = path.join(uploadsDir, `module1-figure-descriptions-${randomUUID()}.json`);
  await writeFile(descriptionsFile, JSON.stringify(descriptions, null, 2), 'utf-8');

  const scriptPath = path.join(projectRoot, 'scripts', 'extract-module1-figures.py');
  const module1Cwd = path.join(projectRoot, '..', '1-patent-analysis');
  try {
    const { stdout } = await execFileAsync(
      'uv',
      [
        'run',
        'python',
        scriptPath,
        '--pdf-path',
        patentFileUrl,
        '--output-dir',
        outputDir,
        '--url-prefix',
        `/api/test/module1/figure/${runId}`,
        '--descriptions-file',
        descriptionsFile,
      ],
      {
        cwd: module1Cwd,
        maxBuffer: 10 * 1024 * 1024,
      },
    );

    return JSON.parse(stdout) as FigureExtractResult;
  } catch (error) {
    return {
      figures: [],
      errors: [
        {
          error_type: 'LOCAL_FIGURE_EXTRACT_ERROR',
          error_message: error instanceof Error ? error.message : String(error),
          is_recoverable: true,
        },
      ],
    };
  }
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  try {
    const body = (await request.json()) as Module1TestInput;
    const { type, url, fileUrl, text } = body;

    let patentFileUrl = '';
    if (type === 'url') {
      if (!url?.trim()) {
        return NextResponse.json({ error: '缺少专利文件 URL' }, { status: 400 });
      }
      patentFileUrl = url.trim();
    } else if (type === 'file') {
      if (!fileUrl?.trim()) {
        return NextResponse.json({ error: '缺少上传后的文件路径' }, { status: 400 });
      }
      patentFileUrl = fileUrl.trim();
    } else if (type === 'text') {
      if (!text?.trim()) {
        return NextResponse.json({ error: '缺少专利文本内容' }, { status: 400 });
      }
      patentFileUrl = await persistTextInput(text.trim());
    } else {
      return NextResponse.json({ error: '不支持的输入类型' }, { status: 400 });
    }

    const taskId = `module1_test_${Date.now()}`;
    const fileReadResult = await readModule1RawText(patentFileUrl).catch(
      (error): FileReadNodeResult => ({
        raw_text: '',
        file_format: null,
        read_error: {
          error_type: 'FILE_READ_NODE_ERROR',
          error_message: error instanceof Error ? error.message : String(error),
          is_recoverable: true,
        },
      }),
    );

    const structureResult = fileReadResult.raw_text
      ? await callModule1Node<StructureIdentifyNodeResult>('structure_identify_node', {
          raw_text: fileReadResult.raw_text,
        })
      : {
          specification_sections: [],
          patent_metadata: {},
          claims_section_text: '',
          identify_errors: [],
        };

    const [claimsResult, localFigureResult] = await Promise.all([
      structureResult.claims_section_text
        ? callModule1Node<ClaimsParseNodeResult>('claims_parse_node', {
            claims_section_text: structureResult.claims_section_text,
          }).catch((error): ClaimsParseNodeResult => ({
            claims_list: [],
            claims_errors: [
              {
                error_type: 'CLAIMS_PARSE_NODE_ERROR',
                error_message: error instanceof Error ? error.message : String(error),
                is_recoverable: true,
              },
            ],
          }))
        : Promise.resolve({
            claims_list: [],
            claims_errors: [],
          }),
      fileReadResult.file_format === 'pdf'
        ? extractFiguresLocally(
            patentFileUrl,
            extractFigureDescriptions(structureResult.specification_sections),
          )
        : Promise.resolve({
            figures: [],
            errors: [],
          }),
    ]);

    const specification = Object.fromEntries(
      (structureResult.specification_sections || []).map((section) => [
        section.section_name || '',
        section.section_text || '',
      ]),
    );
    const finalErrors = [
      ...(fileReadResult.read_error ? [fileReadResult.read_error] : []),
      ...((structureResult.identify_errors || []) as Array<Record<string, unknown>>),
      ...((claimsResult.claims_errors || []) as Array<Record<string, unknown>>),
      ...(localFigureResult.errors || []),
    ];

    return NextResponse.json({
      ok: true,
      inputType: type,
      patentFileUrl,
      taskId,
      runId: '',
      dbRecordId: null,
      feishuUrl: null,
      feishuAppToken: null,
      rawText: fileReadResult?.raw_text || '',
      rawTextLength: fileReadResult?.raw_text?.length || 0,
      fileFormat: fileReadResult?.file_format || null,
      fileReadError: fileReadResult?.read_error || null,
      claimsCount: (claimsResult.claims_list || []).length,
      figuresCount: (localFigureResult.figures || []).length,
      finalOutput: {
        claims: claimsResult.claims_list || [],
        figures: localFigureResult.figures || [],
        specification,
        metadata: structureResult.patent_metadata || {},
        errors: finalErrors.length > 0 ? finalErrors : null,
      },
    });
  } catch (error) {
    return NextResponse.json(
      {
        error: error instanceof Error ? error.message : '模块1测试执行失败',
      },
      { status: 500 },
    );
  }
}
