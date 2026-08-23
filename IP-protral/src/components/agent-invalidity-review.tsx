'use client';

import { useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { InvalidityReviewPanel } from '@/components/invalidity-review-panel';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  claimDisplayLabel,
  invalidityText,
  isJsonObject,
  mergeCriticalDateConfirmation,
  needsInvalidityCriticalDateConfirmation,
  reportRows,
  type CriticalDateConfirmationResponse,
  type CriticalDateDecision,
  type JsonObject,
} from '@/lib/invalidity-contracts';
import { createStableInvalidityIntent } from '@/lib/invalidity-idempotency';

function text(value: unknown): string {
  return invalidityText(value).trim();
}

function dateCandidate(claim: JsonObject, key: string): string {
  const source = isJsonObject(claim.source_snapshot) ? claim.source_snapshot : {};
  const result = isJsonObject(claim.result_summary) ? claim.result_summary : {};
  return text(claim[key] || source[key] || result[key]);
}

function CriticalDateForm({
  sessionId,
  claim,
  onSaved,
}: {
  sessionId: string;
  claim: JsonObject;
  onSaved: (confirmation: CriticalDateConfirmationResponse) => void;
}) {
  const [decision, setDecision] = useState<CriticalDateDecision>('confirm_priority');
  const [confirmedDate, setConfirmedDate] = useState(() => dateCandidate(claim, 'critical_date'));
  const [targetPublicationDate, setTargetPublicationDate] = useState(() => dateCandidate(claim, 'target_publication_date'));
  const [basis, setBasis] = useState(() => dateCandidate(claim, 'critical_date_basis'));
  const [reason, setReason] = useState('已核对目标专利著录项目与权利要求主题对应关系');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const intent = useRef(createStableInvalidityIntent('agent-critical-date'));

  const submit = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const formIntent = {
        claim_investigation_id: text(claim.id),
        decision,
        confirmed_date: confirmedDate,
        target_publication_date: targetPublicationDate,
        basis,
        reason,
        expected_state_version: Number(claim.state_version || 0),
      };
      const response = await fetch(
        `/api/invalidity/session/${encodeURIComponent(sessionId)}/critical-date-confirmations`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            ...formIntent,
            idempotency_key: intent.current.key(JSON.stringify(formIntent)),
          }),
        },
      );
      const payload = await response.json().catch(() => ({})) as JsonObject;
      if (!response.ok) throw new Error(text(payload.error || payload.detail) || '保存关键日确认失败');
      setMessage('确认已保存，后台会从当前 checkpoint 恢复。');
      onSaved(payload as CriticalDateConfirmationResponse);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '保存关键日确认失败');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded-lg border bg-background p-4">
      <div>
        <p className="font-medium">{claimDisplayLabel(claim)}</p>
        <p className="text-xs text-muted-foreground">请确认关键日和目标专利公开日；系统据此分别判断普通现有技术与抵触申请。</p>
      </div>
      <div className="grid gap-3 md:grid-cols-3">
        <label className="space-y-1 text-sm">
          <span>确认方式</span>
          <select
            className="h-9 w-full rounded-md border bg-background px-3"
            value={decision}
            onChange={(event) => setDecision(event.target.value as CriticalDateDecision)}
          >
            <option value="confirm_priority">确认优先权日</option>
            <option value="use_filing_date">改用申请日</option>
            <option value="set_manual_date">人工指定关键日</option>
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span>本权利要求关键日</span>
          <Input type="date" value={confirmedDate} onChange={(event) => setConfirmedDate(event.target.value)} />
        </label>
        <label className="space-y-1 text-sm">
          <span>目标专利公开日</span>
          <Input type="date" value={targetPublicationDate} onChange={(event) => setTargetPublicationDate(event.target.value)} />
        </label>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span>日期依据</span>
          <Input value={basis} onChange={(event) => setBasis(event.target.value)} placeholder="优先权文件、申请日著录项目或主题对应关系" />
        </label>
        <label className="space-y-1 text-sm">
          <span>确认理由</span>
          <Input value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          size="sm"
          onClick={() => void submit()}
          disabled={busy || !confirmedDate || !targetPublicationDate || !basis.trim() || !reason.trim()}
        >
          {busy ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
          确认并恢复任务
        </Button>
        {message ? <span className="text-sm text-muted-foreground">{message}</span> : null}
      </div>
    </div>
  );
}

export function AgentInvalidityReview({ sessionId }: { sessionId: string }) {
  const [payload, setPayload] = useState<JsonObject | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const response = await fetch(`/api/invalidity/session/${encodeURIComponent(sessionId)}`, { cache: 'no-store' });
        const value = await response.json().catch(() => ({})) as JsonObject;
        if (!response.ok) throw new Error(text(value.error) || '读取人工确认事项失败');
        if (!cancelled) {
          setPayload(value);
          setError(null);
        }
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : '读取人工确认事项失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [refreshNonce, sessionId]);

  const claims = reportRows(payload?.claim_investigations);
  const dateClaims = claims.filter(needsInvalidityCriticalDateConfirmation);
  const refresh = () => setRefreshNonce((value) => value + 1);
  const saveCriticalDate = (confirmation: CriticalDateConfirmationResponse) => {
    setPayload((current) => current ? mergeCriticalDateConfirmation(current, confirmation) : current);
    refresh();
  };

  if (loading) return <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在读取需要确认的事项…</p>;
  if (error) return <Alert variant="destructive"><AlertTitle>人工确认事项读取失败</AlertTitle><AlertDescription>{error}</AlertDescription></Alert>;

  return (
    <div className="space-y-4">
      {dateClaims.length ? (
        <div className="space-y-3">
          {dateClaims.map((claim) => (
            <CriticalDateForm
              key={`${text(claim.id)}-${text(claim.state_version)}`}
              sessionId={sessionId}
              claim={claim}
              onSaved={saveCriticalDate}
            />
          ))}
        </div>
      ) : null}
      <InvalidityReviewPanel
        environment="prod"
        sessionId={sessionId}
        onChanged={refresh}
        focus="pending_date_review"
      />
    </div>
  );
}
