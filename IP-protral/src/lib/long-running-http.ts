import { request as httpRequest } from 'node:http';
import { request as httpsRequest } from 'node:https';

export interface LongRunningHttpResponse {
  ok: boolean;
  status: number;
  statusText: string;
  text: string;
}

interface PostJsonOptions {
  headers?: Record<string, string>;
  timeoutMs: number;
}

/**
 * POST JSON without Node fetch/Undici's independent 300-second headers timeout.
 * Workflow endpoints can legitimately take much longer before sending headers,
 * so the caller's business timeout must be the only total request deadline.
 */
export function postJsonWithTimeout(
  urlValue: string,
  payload: unknown,
  options: PostJsonOptions,
): Promise<LongRunningHttpResponse> {
  const url = new URL(urlValue);
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`Unsupported workflow URL protocol: ${url.protocol}`);
  }

  const body = JSON.stringify(payload);
  const request = url.protocol === 'https:' ? httpsRequest : httpRequest;

  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      callback();
    };

    const clientRequest = request(
      url,
      {
        method: 'POST',
        headers: {
          ...options.headers,
          'Content-Length': Buffer.byteLength(body).toString(),
        },
      },
      (response) => {
        const chunks: Buffer[] = [];

        response.on('data', (chunk: Buffer | string) => {
          chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
        });
        response.once('error', (error) => finish(() => reject(error)));
        response.once('end', () => {
          finish(() => {
            const status = response.statusCode || 0;
            resolve({
              ok: status >= 200 && status < 300,
              status,
              statusText: response.statusMessage || '',
              text: Buffer.concat(chunks).toString('utf8'),
            });
          });
        });
      },
    );

    const timeout = setTimeout(() => {
      clientRequest.destroy(
        new Error(`Workflow request timed out after ${Math.ceil(options.timeoutMs / 1000)} seconds`),
      );
    }, options.timeoutMs);

    clientRequest.once('error', (error) => finish(() => reject(error)));
    clientRequest.end(body);
  });
}
