/**
 * The wire contract with the Python AI service, as types plus runtime guards.
 *
 * A REST boundary is exactly the place where TypeScript's guarantees stop: the
 * compiler knows nothing about what FastAPI actually put on the wire. Casting
 * `await res.json() as HealthResponse` would restore the *feeling* of type
 * safety while removing the substance — the first schema change on the Python
 * side would then surface as `undefined is not a function` somewhere far away.
 *
 * So every response is validated once, here, and only then enters typed code.
 * The guards are hand-written rather than pulled from a schema library: the
 * contract is three endpoints wide, and a dependency-free app keeps the
 * Dockerfile and the review surface small.
 */

export interface HealthResponse {
  status: string;
  provider: string;
}

export interface IngestResponse {
  doc_id: string;
  chunks: number;
}

export interface QueryResponse {
  answer: string;
  sources: string[];
}

/** Thrown when the service answers with a shape the contract does not describe. */
export class ContractError extends Error {
  constructor(endpoint: string, detail: string) {
    super(`AI service returned an unexpected shape for ${endpoint}: ${detail}`);
    this.name = 'ContractError';
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(
  source: Record<string, unknown>,
  key: string,
  endpoint: string,
): string {
  const value = source[key];
  if (typeof value !== 'string') {
    throw new ContractError(endpoint, `expected "${key}" to be a string, got ${typeof value}`);
  }
  return value;
}

function requireNumber(
  source: Record<string, unknown>,
  key: string,
  endpoint: string,
): number {
  const value = source[key];
  if (typeof value !== 'number' || Number.isNaN(value)) {
    throw new ContractError(endpoint, `expected "${key}" to be a number, got ${typeof value}`);
  }
  return value;
}

export function parseHealth(payload: unknown): HealthResponse {
  if (!isRecord(payload)) throw new ContractError('/health', 'body is not an object');
  return {
    status: requireString(payload, 'status', '/health'),
    provider: requireString(payload, 'provider', '/health'),
  };
}

export function parseIngest(payload: unknown): IngestResponse {
  if (!isRecord(payload)) throw new ContractError('/ingest', 'body is not an object');
  return {
    doc_id: requireString(payload, 'doc_id', '/ingest'),
    chunks: requireNumber(payload, 'chunks', '/ingest'),
  };
}

export function parseQuery(payload: unknown): QueryResponse {
  if (!isRecord(payload)) throw new ContractError('/query', 'body is not an object');
  const sources = payload['sources'];
  if (!Array.isArray(sources) || sources.some((s) => typeof s !== 'string')) {
    throw new ContractError('/query', 'expected "sources" to be an array of strings');
  }
  return {
    answer: requireString(payload, 'answer', '/query'),
    sources: sources as string[],
  };
}

/**
 * Best-effort extraction of the service's own error text.
 *
 * FastAPI puts it in `detail`; the guard in main.py may also produce `error`.
 * Neither is guaranteed, so this returns null rather than inventing a message.
 */
export function extractErrorDetail(payload: unknown): string | null {
  if (!isRecord(payload)) return null;
  const detail = payload['detail'] ?? payload['error'];
  return typeof detail === 'string' && detail !== '' ? detail : null;
}
