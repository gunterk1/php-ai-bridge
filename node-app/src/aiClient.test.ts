/**
 * Behavioural tests for AiClient against a throwaway HTTP server.
 *
 * These exist because the repository's claim is not "there is also a TypeScript
 * folder" but "the same contract and the same failure handling, in a second
 * ecosystem". That claim is only worth something if the retry policy is checked
 * rather than asserted in a comment.
 *
 * No test framework: node:test and node:assert ship with the runtime, and a
 * dependency-free app is easier to trust when you are reading it as a stranger.
 *
 * Run with:  npm test
 */

import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer, type Server, type IncomingMessage, type ServerResponse } from 'node:http';
import type { AddressInfo } from 'node:net';

import { AiClient, AiServiceError } from './aiClient.js';
import { ContractError } from './contracts.js';

/** Per-path behaviour the fake service should exhibit for the next request(s). */
type Handler = (req: IncomingMessage, res: ServerResponse, hit: number) => void;

let server: Server;
let baseUrl = '';
const handlers = new Map<string, Handler>();
const hits = new Map<string, number>();

function respond(res: ServerResponse, status: number, body: unknown): void {
  const payload = typeof body === 'string' ? body : JSON.stringify(body);
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(payload);
}

before(async () => {
  server = createServer((req, res) => {
    const path = req.url ?? '/';
    const hit = (hits.get(path) ?? 0) + 1;
    hits.set(path, hit);
    const handler = handlers.get(path);
    if (!handler) {
      respond(res, 404, { detail: 'no handler registered' });
      return;
    }
    handler(req, res, hit);
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  baseUrl = `http://127.0.0.1:${port}`;
});

after(async () => {
  await new Promise<void>((resolve, reject) =>
    server.close((err) => (err ? reject(err) : resolve())),
  );
});

function stub(path: string, handler: Handler): void {
  handlers.set(path, handler);
  hits.set(path, 0);
}

const hitCount = (path: string): number => hits.get(path) ?? 0;

describe('AiClient — happy path', () => {
  test('health returns the validated payload', async () => {
    stub('/health', (_req, res) => respond(res, 200, { status: 'ok', provider: 'openai' }));
    const result = await new AiClient(baseUrl).health();
    assert.deepEqual(result, { status: 'ok', provider: 'openai' });
  });

  test('ingest sends doc_id and text, and parses the chunk count', async () => {
    let received = '';
    stub('/ingest', (req, res) => {
      const chunks: Buffer[] = [];
      req.on('data', (c: Buffer) => chunks.push(c));
      req.on('end', () => {
        received = Buffer.concat(chunks).toString('utf8');
        respond(res, 200, { doc_id: 'handbook', chunks: 7 });
      });
    });
    const result = await new AiClient(baseUrl).ingest('handbook', 'some text');
    assert.deepEqual(result, { doc_id: 'handbook', chunks: 7 });
    assert.deepEqual(JSON.parse(received), { doc_id: 'handbook', text: 'some text' });
  });

  test('query parses answer and sources', async () => {
    stub('/query', (_req, res) => respond(res, 200, { answer: 'Yes.', sources: ['a', 'b'] }));
    const result = await new AiClient(baseUrl).query('is it?');
    assert.deepEqual(result, { answer: 'Yes.', sources: ['a', 'b'] });
  });
});

describe('AiClient — retry policy, matching AiClient.php', () => {
  test('a 5xx is retried and the eventual success is returned', async () => {
    stub('/health', (_req, res, hit) =>
      hit < 3
        ? respond(res, 503, { detail: 'AI backend unreachable' })
        : respond(res, 200, { status: 'ok', provider: 'ollama' }),
    );
    const result = await new AiClient(baseUrl, { maxRetries: 2 }).health();
    assert.equal(result.provider, 'ollama');
    assert.equal(hitCount('/health'), 3, 'expected two retries after the first attempt');
  });

  test('a 4xx is NOT retried — the same request would fail identically', async () => {
    stub('/query', (_req, res) => respond(res, 429, { detail: 'AI backend quota/rate limit' }));
    await assert.rejects(
      () => new AiClient(baseUrl, { maxRetries: 2 }).query('anything'),
      (error: unknown) => {
        assert.ok(error instanceof AiServiceError);
        assert.equal(error.status, 429);
        assert.match(error.message, /quota\/rate limit/);
        return true;
      },
    );
    assert.equal(hitCount('/query'), 1, 'a client error must not be retried');
  });

  test('retries are exhausted and the last error surfaces', async () => {
    stub('/health', (_req, res) => respond(res, 500, { detail: 'boom' }));
    await assert.rejects(
      () => new AiClient(baseUrl, { maxRetries: 1 }).health(),
      (error: unknown) => error instanceof AiServiceError && error.status === 500,
    );
    assert.equal(hitCount('/health'), 2);
  });
});

describe('AiClient — the REST boundary is validated, not trusted', () => {
  test('a 2xx with a missing field raises ContractError, not a silent undefined', async () => {
    stub('/health', (_req, res) => respond(res, 200, { status: 'ok' })); // provider missing
    await assert.rejects(
      () => new AiClient(baseUrl).health(),
      (error: unknown) => {
        assert.ok(error instanceof ContractError);
        assert.match(error.message, /provider/);
        return true;
      },
    );
  });

  test('a 2xx with the wrong type for sources raises ContractError', async () => {
    stub('/query', (_req, res) => respond(res, 200, { answer: 'x', sources: 'not-an-array' }));
    await assert.rejects(() => new AiClient(baseUrl).query('q'), ContractError);
  });

  test('a 2xx that is not JSON raises ContractError and is not retried', async () => {
    stub('/health', (_req, res) => {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end('<html>a proxy error page</html>');
    });
    await assert.rejects(() => new AiClient(baseUrl).health(), ContractError);
    assert.equal(hitCount('/health'), 1);
  });
});

describe('AiClient — transport', () => {
  test('an unreachable service surfaces as AiServiceError after retries', async () => {
    // Port 1 is reserved and refuses connections on every platform we target.
    const client = new AiClient('http://127.0.0.1:1', { maxRetries: 1, timeoutMs: 1_000 });
    await assert.rejects(
      () => client.health(),
      (error: unknown) => error instanceof AiServiceError && error.status === null,
    );
  });

  test('a trailing slash in the base URL does not produce a double slash', async () => {
    stub('/health', (_req, res) => respond(res, 200, { status: 'ok', provider: 'openai' }));
    const result = await new AiClient(`${baseUrl}///`).health();
    assert.equal(result.status, 'ok');
  });
});
