'use client';

import type { ClaimTokenStatus, ClaimTokenUnit, ScoreBand } from '@/lib/types';
import { buildFallbackTokenUnits, tokenStatusClass } from '@/lib/claim-score';

interface ClaimTokenHighlightProps {
  text: string;
  tokenUnits?: ClaimTokenUnit[];
  scoreBand?: ScoreBand;
  zeroedByMismatch?: boolean;
}

interface TextSegment {
  text: string;
  status?: ClaimTokenStatus;
  title?: string;
}

export function ClaimTokenHighlight({ text, tokenUnits, scoreBand, zeroedByMismatch }: ClaimTokenHighlightProps) {
  const units = tokenUnits && tokenUnits.length > 0 ? tokenUnits : buildFallbackTokenUnits(text);
  // 只有在「显式 mismatch 触发了整条归零」时，才把整段文字强制染红。
  // 旧逻辑只要 scoreBand === 'exact_mismatch' 就全红，会导致 score=0 的"待确认"特征被一起标红。
  // 这里同时校验 zeroedByMismatch 与 unit 内部状态，避免误染。
  const hasExplicitMismatch = (zeroedByMismatch ?? false) || units.some((unit) => unit.status === 'mismatch');
  const forceFullMismatch = scoreBand === 'exact_mismatch' && hasExplicitMismatch;
  const segments = buildTextSegments(text, units, forceFullMismatch);

  return (
    <div className="leading-7 whitespace-pre-wrap break-words">
      {segments.map((segment, index) => (
        segment.status ? (
          <span
            key={`${segment.text}-${index}`}
            className={`inline-block px-0.5 rounded-sm ${tokenStatusClass(segment.status)}`}
            title={segment.title || undefined}
          >
            {segment.text}
          </span>
        ) : (
          <span key={`${segment.text}-${index}`}>{segment.text}</span>
        )
      ))}
    </div>
  );
}

function buildTextSegments(text: string, units: ClaimTokenUnit[], forceFullMismatch: boolean): TextSegment[] {
  const source = String(text || '');
  if (!source) return [];
  if (forceFullMismatch) {
    return [{ text: source, status: 'mismatch' }];
  }

  const segments: TextSegment[] = [];
  let cursor = 0;

  for (const unit of units) {
    const unitText = String(unit.text || '');
    if (!unitText) continue;
    const index = source.indexOf(unitText, cursor);
    if (index < 0) continue;

    if (index > cursor) {
      segments.push({ text: source.slice(cursor, index) });
    }

    segments.push({
      text: source.slice(index, index + unitText.length),
      status: unit.status,
      title: unit.reason || unit.evidence,
    });
    cursor = index + unitText.length;
  }

  if (cursor < source.length) {
    segments.push({ text: source.slice(cursor) });
  }

  if (segments.length === 0) {
    return source.split('').map((char) => ({ text: char }));
  }

  return segments;
}
