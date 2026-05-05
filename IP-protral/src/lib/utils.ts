import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
import type { AnalysisSession } from './types';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

function firstNonEmpty(...values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }

  return null;
}

function extractPatentNumber(text: string): string | null {
  const normalized = text.trim();
  if (!normalized) {
    return null;
  }

  const publicationMatch = normalized.match(/\bCN\s*\d{6,12}(?:\.\d)?[A-Z]\b/i);
  if (publicationMatch) {
    return publicationMatch[0].replace(/\s+/g, '').toUpperCase();
  }

  const applicationMatch = normalized.match(/\b\d{8,12}(?:\.\d)\b/);
  if (applicationMatch) {
    return applicationMatch[0];
  }

  return null;
}

function cleanPatentSubject(source: string): string {
  return source
    .replace(/\.[a-z0-9]+$/i, '')
    .replace(/\bCN\s*\d{6,12}(?:\.\d)?[A-Z]\b/gi, ' ')
    .replace(/\b\d{8,12}(?:\.\d)\b/g, ' ')
    .replace(/[【】\[\]（）()]/g, ' ')
    .replace(/[_-]+/g, ' ')
    .replace(/专利分析|专利|分析|示例|样例|demo|test|测试|上传/gi, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function extractPatentSubject(source: string): string | null {
  const cleaned = cleanPatentSubject(source);
  if (!cleaned) {
    return null;
  }

  const explicitObjectPatterns = [
    /([\u4e00-\u9fa5A-Za-z0-9]{2,20}?(?:扫地机器人|平衡车|扫地机|洗地机|吸尘器|无人机|摄像头|门锁|充电桩|投影仪|打印机|净化器|咖啡机|路由器|显示器|耳机|机器人))/g,
    /(?:一种|用于|涉及|关于)?([\u4e00-\u9fa5]{2,20}?)(?:控制方法|控制系统|方法|系统|装置|设备|结构|组件|模组|机构)/g,
  ];

  for (const pattern of explicitObjectPatterns) {
    const matches = Array.from(cleaned.matchAll(pattern))
      .map((match) => match[1]?.trim())
      .filter((value): value is string => Boolean(value && value.length >= 2));

    if (matches.length > 0) {
      return matches.sort((a, b) => b.length - a.length)[0];
    }
  }

  const genericMatch = cleaned.match(/([\u4e00-\u9fa5]{2,20}(?:机|器|车|锁|柜|箱|仪|刷|灯|镜|表|杆|架|轮))/);
  if (genericMatch?.[1]) {
    return genericMatch[1].trim();
  }

  const plainChineseMatch = cleaned.match(/[\u4e00-\u9fa5]{2,12}/);
  if (plainChineseMatch?.[0]) {
    return plainChineseMatch[0].trim();
  }

  return null;
}

export function getSessionDisplayInfo(session: AnalysisSession): {
  title: string;
  patentNumber: string | null;
} {
  const patentNumber = firstNonEmpty(
    session.patentNumber,
    session.results?.patent?.patentNumber,
    extractPatentNumber(session.input.fileName || ''),
    extractPatentNumber(session.input.value || ''),
    extractPatentNumber(session.input.fileUrl || ''),
  );

  const patentSubject = firstNonEmpty(
    extractPatentSubject(session.patentTitle || ''),
    extractPatentSubject(session.results?.patent?.title || ''),
    extractPatentSubject(session.input.fileName || ''),
    extractPatentSubject(session.input.value || ''),
    extractPatentSubject(session.input.fileUrl || ''),
    ...(session.results?.keywords || []).map((keyword) => extractPatentSubject(keyword)),
    ...((session.results?.products || []).slice(0, 5).map((product) => extractPatentSubject(product.name))),
  );

  const title = patentSubject
    ? `${patentSubject}专利分析`
    : firstNonEmpty(
      session.patentTitle,
      session.results?.patent?.title,
    ) || '专利分析';

  return {
    title,
    patentNumber,
  };
}
