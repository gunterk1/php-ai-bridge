/**
 * Front controller for the Node application — the counterpart to
 * php-app/public/index.php.
 *
 * Same three JSON routes, same proxy-to-the-AI-service shape, same minimal UI.
 * Built on node:http rather than a framework: three routes and one static file
 * do not justify a dependency tree, and the point of the app is the integration
 * boundary, not the router.
 */

import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { AiClient, AiServiceError } from './aiClient.js';
import { ContractError } from './contracts.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const INDEX_HTML = join(HERE, '..', 'public', 'index.html');

const PORT = Number(process.env['PORT'] ?? 8081);
const client = new AiClient(process.env['AI_SERVICE_URL'] ?? 'http://localhost:8000');

/** Cap the request body so a stray large upload cannot exhaust memory. */
const MAX_BODY_BYTES = 1_000_000;

function sendJson(res: ServerResponse, status: number, data: unknown): void {
  const body = JSON.stringify(data);
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    const buf = chunk as Buffer;
    size += buf.length;
    if (size > MAX_BODY_BYTES) throw new Error('request body too large');
    chunks.push(buf);
  }
  if (chunks.length === 0) return {};
  const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : {};
}

const asString = (value: unknown, fallback: string): string =>
  typeof value === 'string' ? value : fallback;

const asPositiveInt = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : fallback;

async function handleApi(
  req: IncomingMessage,
  res: ServerResponse,
  method: string,
  path: string,
): Promise<boolean> {
  if (method === 'GET' && path === '/api/health') {
    sendJson(res, 200, await client.health());
    return true;
  }
  if (method === 'POST' && path === '/api/ingest') {
    const body = await readJsonBody(req);
    sendJson(res, 200, await client.ingest(asString(body['doc_id'], 'doc'), asString(body['text'], '')));
    return true;
  }
  if (method === 'POST' && path === '/api/query') {
    const body = await readJsonBody(req);
    sendJson(res, 200, await client.query(asString(body['question'], ''), asPositiveInt(body['k'], 4)));
    return true;
  }
  return false;
}

const server = createServer((req, res) => {
  void (async () => {
    const method = req.method ?? 'GET';
    const path = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`).pathname;

    try {
      if (await handleApi(req, res, method, path)) return;
    } catch (error) {
      // 502 mirrors index.php: the failure is downstream, not in this app.
      // ContractError is kept distinct in the message so a schema drift on the
      // Python side is recognisable rather than looking like a network blip.
      const message =
        error instanceof AiServiceError || error instanceof ContractError
          ? error.message
          : error instanceof Error
            ? error.message
            : 'unknown error';
      sendJson(res, 502, { error: message });
      return;
    }

    try {
      const html = await readFile(INDEX_HTML);
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(html);
    } catch {
      sendJson(res, 500, { error: 'UI asset missing' });
    }
  })();
});

server.listen(PORT, () => {
  process.stdout.write(`node-app listening on :${PORT} → ${process.env['AI_SERVICE_URL'] ?? 'http://localhost:8000'}\n`);
});
