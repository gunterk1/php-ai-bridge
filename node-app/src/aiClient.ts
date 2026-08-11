/**
 * Thin REST client for the Python AI service — the TypeScript counterpart to
 * php-app/src/AiClient.php.
 *
 * Both clients are deliberately behaviour-identical, because that is the claim
 * this repository makes: the AI capability lives behind a REST boundary, and the
 * product surface can be written in whatever the team already runs. Swapping PHP
 * for Node changes the language, not the contract, not the failure handling, and
 * not where the model runs.
 *
 * Retry policy, matching AiClient.php line for line:
 *   • network errors and 5xx  → transient, retried with 200ms then 400ms backoff
 *   • 4xx (including 429)     → the same request would fail identically, so it is
 *                               surfaced immediately rather than hammering a
 *                               backend that is out of quota
 *
 * The one deliberate difference is at the boundary: responses are validated
 * against the contract instead of cast (see contracts.ts).
 */

import {
  ContractError,
  extractErrorDetail,
  parseHealth,
  parseIngest,
  parseQuery,
  type HealthResponse,
  type IngestResponse,
  type QueryResponse,
} from './contracts.js';

export interface AiClientOptions {
  /** Per-attempt timeout. The PHP client uses curl's 30s default. */
  timeoutMs?: number;
  /** Retries *after* the first attempt, so 2 means up to three requests. */
  maxRetries?: number;
}

/** Raised for transport and HTTP failures, after retries are exhausted. */
export class AiServiceError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
  ) {
    super(`AI service request failed: ${message}`);
    this.name = 'AiServiceError';
  }
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

export class AiClient {
  private readonly baseUrl: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;

  constructor(baseUrl: string, options: AiClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, '');
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.maxRetries = options.maxRetries ?? 2;
  }

  async health(): Promise<HealthResponse> {
    return parseHealth(await this.request('GET', '/health'));
  }

  async ingest(docId: string, text: string): Promise<IngestResponse> {
    return parseIngest(await this.request('POST', '/ingest', { doc_id: docId, text }));
  }

  async query(question: string, k = 4): Promise<QueryResponse> {
    return parseQuery(await this.request('POST', '/query', { question, k }));
  }

  private async request(
    method: 'GET' | 'POST',
    path: string,
    payload?: Record<string, unknown>,
  ): Promise<unknown> {
    let lastError = '';
    let lastStatus: number | null = null;

    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      // A fresh signal per attempt: an aborted controller stays aborted, so
      // reusing one would make every retry fail instantly.
      const abort = AbortSignal.timeout(this.timeoutMs);

      let response: Response;
      try {
        response = await fetch(this.baseUrl + path, {
          method,
          signal: abort,
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          ...(payload !== undefined ? { body: JSON.stringify(payload) } : {}),
        });
      } catch (cause) {
        // Network-level failure, including the timeout above: transient.
        lastError = cause instanceof Error ? cause.message : String(cause);
        lastStatus = null;
        if (attempt < this.maxRetries) {
          await sleep(200 * 2 ** attempt);
          continue;
        }
        break;
      }

      const raw = await response.text();

      if (response.ok) {
        try {
          return JSON.parse(raw) as unknown;
        } catch {
          // A 2xx that is not JSON is a contract violation, not a transport
          // problem — retrying would return the same broken body.
          throw new ContractError(path, 'body was not valid JSON');
        }
      }

      lastStatus = response.status;
      let detail: string | null = null;
      if (raw !== '') {
        try {
          detail = extractErrorDetail(JSON.parse(raw) as unknown);
        } catch {
          detail = null;
        }
      }
      lastError = detail !== null ? `HTTP ${response.status}: ${detail}` : `HTTP ${response.status}`;

      if (response.status < 500) break; // client error — identical on retry
      if (attempt < this.maxRetries) await sleep(200 * 2 ** attempt);
    }

    throw new AiServiceError(lastError, lastStatus);
  }
}
