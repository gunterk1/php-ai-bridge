<?php

declare(strict_types=1);

require __DIR__ . '/../src/AiClient.php';

use PhpAiBridge\AiClient;

$client = new AiClient(getenv('AI_SERVICE_URL') ?: 'http://localhost:8000');

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$path   = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';

/**
 * @param array<string,mixed> $data
 */
function json_response(array $data, int $code = 200): never
{
    http_response_code($code);
    header('Content-Type: application/json');
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}

/**
 * @return array<string,mixed>
 */
function read_json_body(): array
{
    $raw = file_get_contents('php://input');
    if ($raw === '' || $raw === false) {
        return [];
    }
    $decoded = json_decode($raw, true);
    return is_array($decoded) ? $decoded : [];
}

// --- JSON API: the PHP app proxies to the AI service over REST ---
try {
    if ($method === 'GET' && $path === '/api/health') {
        json_response($client->health());
    }
    if ($method === 'POST' && $path === '/api/ingest') {
        $body = read_json_body();
        json_response($client->ingest(
            (string) ($body['doc_id'] ?? 'doc'),
            (string) ($body['text'] ?? '')
        ));
    }
    if ($method === 'POST' && $path === '/api/query') {
        $body = read_json_body();
        json_response($client->query(
            (string) ($body['question'] ?? ''),
            (int) ($body['k'] ?? 4)
        ));
    }
} catch (\Throwable $e) {
    json_response(['error' => $e->getMessage()], 502);
}

// --- Default: minimal UI ---
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>php-ai-bridge — semantic search over your documents</title>
<style>
  :root { --ink: #1a1a2e; --accent: hsl(187,74%,32%); --muted: #667; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         max-width: 760px; margin: 40px auto; padding: 0 20px; color: var(--ink); }
  h1 { font-size: 22px; margin-bottom: 4px; }
  p.sub { color: var(--muted); margin-top: 0; }
  textarea, input { width: 100%; padding: 10px; border: 1px solid #ccd; border-radius: 6px;
                    font: inherit; margin-bottom: 10px; }
  textarea { min-height: 140px; resize: vertical; }
  button { background: var(--accent); color: #fff; border: 0; border-radius: 6px;
           padding: 10px 16px; font: inherit; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  .row { display: flex; gap: 10px; align-items: flex-start; }
  .row input { flex: 1; }
  .card { border: 1px solid #e2e2ea; border-radius: 8px; padding: 16px; margin-top: 18px; }
  .answer { white-space: pre-wrap; line-height: 1.55; }
  .sources { color: var(--muted); font-size: 13px; margin-top: 10px; }
  .status { font-size: 13px; color: var(--muted); }
  code { background: #f4f4f8; padding: 1px 5px; border-radius: 4px; }
</style>
</head>
<body>
  <h1>php-ai-bridge</h1>
  <p class="sub">A PHP app that integrates a Python AI service over REST. Privacy-first: the same code runs against a local model or an external one.</p>
  <p class="status" id="status">checking AI service…</p>

  <h3>1. Ingest a document</h3>
  <input id="docId" placeholder="document id (e.g. handbook)" value="handbook">
  <textarea id="docText" placeholder="Paste any text here…"></textarea>
  <button id="ingestBtn">Ingest</button>

  <h3>2. Ask a question</h3>
  <div class="row">
    <input id="question" placeholder="What does the document say about…?">
    <button id="askBtn">Ask</button>
  </div>

  <div class="card" id="result" style="display:none;">
    <div class="answer" id="answer"></div>
    <div class="sources" id="sources"></div>
  </div>

<script>
const $ = (id) => document.getElementById(id);

async function api(path, body) {
  const opts = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
                    : { method: 'GET' };
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
  return data;
}

api('/api/health')
  .then(d => $('status').textContent = `AI service: ${d.status} · provider: ${d.provider}`)
  .catch(e => $('status').textContent = 'AI service unreachable: ' + e.message);

$('ingestBtn').onclick = async () => {
  const btn = $('ingestBtn'); btn.disabled = true;
  try {
    const d = await api('/api/ingest', { doc_id: $('docId').value || 'doc', text: $('docText').value });
    $('status').textContent = `Ingested "${d.doc_id}" (${d.chunks} chunks).`;
  } catch (e) { $('status').textContent = 'Ingest failed: ' + e.message; }
  btn.disabled = false;
};

$('askBtn').onclick = async () => {
  const btn = $('askBtn'); btn.disabled = true;
  try {
    const d = await api('/api/query', { question: $('question').value });
    $('result').style.display = 'block';
    $('answer').textContent = d.answer;
    $('sources').textContent = d.sources.length ? 'Sources: ' + d.sources.join(', ') : '';
  } catch (e) { $('result').style.display = 'block'; $('answer').textContent = 'Query failed: ' + e.message; $('sources').textContent = ''; }
  btn.disabled = false;
};
</script>
</body>
</html>
