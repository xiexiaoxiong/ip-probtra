export type StableInvalidityIntent = {
  key(fingerprint: string): string;
  reset(): void;
};

function newIntentKey(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.();
  if (!random) throw new Error('当前运行环境无法生成安全的幂等标识');
  return `${prefix}-${random}`;
}

/**
 * Keep one key for one exact form intent. Network retries reuse it; changing
 * any material field creates a new key on the next submit. No render-time
 * clock/random value is used, so server/client hydration remains stable.
 */
export function createStableInvalidityIntent(prefix: string): StableInvalidityIntent {
  let currentFingerprint = '';
  let currentKey = '';
  return {
    key(fingerprint: string) {
      if (!currentKey || currentFingerprint !== fingerprint) {
        currentFingerprint = fingerprint;
        currentKey = newIntentKey(prefix);
      }
      return currentKey;
    },
    reset() {
      currentFingerprint = '';
      currentKey = '';
    },
  };
}
