<?php

declare(strict_types=1);

namespace PhpAiBridge;

/**
 * Thin REST client for the Python AI service.
 *
 * The PHP layer owns the product surface; the AI capability lives behind a REST
 * boundary. This is the same shape as Nextcloud's integration apps: the app
 * talks to an AI backend over HTTP and stays agnostic about whether that backend
 * runs locally or externally.
 *
 * Transient failures (network errors, 5xx) are retried with exponential backoff,
 * so a slow or restarting model backend degrades gracefully instead of surfacing
 * as a hard error. 4xx responses are not retried: they are client errors and
 * would fail again identically.
 */
final class AiClient
{
    private string $baseUrl;

    public function __construct(
        string $baseUrl,
        private int $timeoutSeconds = 30,
        private int $maxRetries = 2
    ) {
        $this->baseUrl = rtrim($baseUrl, '/');
    }

    public function health(): array
    {
        return $this->request('GET', '/health', null);
    }

    public function ingest(string $docId, string $text): array
    {
        return $this->request('POST', '/ingest', ['doc_id' => $docId, 'text' => $text]);
    }

    public function query(string $question, int $k = 4): array
    {
        return $this->request('POST', '/query', ['question' => $question, 'k' => $k]);
    }

    /**
     * @param array<string,mixed>|null $payload
     * @return array<string,mixed>
     */
    private function request(string $method, string $path, ?array $payload): array
    {
        $lastError = '';

        for ($attempt = 0; $attempt <= $this->maxRetries; $attempt++) {
            $ch = curl_init($this->baseUrl . $path);
            curl_setopt_array($ch, [
                CURLOPT_RETURNTRANSFER => true,
                CURLOPT_CUSTOMREQUEST  => $method,
                CURLOPT_TIMEOUT        => $this->timeoutSeconds,
                CURLOPT_HTTPHEADER     => ['Content-Type: application/json', 'Accept: application/json'],
            ]);
            if ($payload !== null) {
                curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload, JSON_THROW_ON_ERROR));
            }

            $body   = curl_exec($ch);
            $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
            $err    = curl_error($ch);
            curl_close($ch);

            if (is_string($body) && $status >= 200 && $status < 300) {
                return json_decode($body, true, 512, JSON_THROW_ON_ERROR);
            }

            // 5xx and network errors are transient and worth retrying. 4xx
            // (including 429 quota errors) will fail again identically, so we
            // surface them immediately instead of hammering the backend.
            $transient = ($body === false) || $status >= 500;

            $detail = '';
            if (is_string($body) && $body !== '') {
                $parsed = json_decode($body, true);
                if (is_array($parsed)) {
                    $detail = (string) ($parsed['detail'] ?? $parsed['error'] ?? '');
                }
            }
            $lastError = $err !== ''
                ? $err
                : ($detail !== '' ? sprintf('HTTP %d: %s', $status, $detail) : sprintf('HTTP %d', $status));

            if (!$transient) {
                break;
            }
            if ($attempt < $this->maxRetries) {
                // 200ms, then 400ms.
                usleep((int) (200_000 * (2 ** $attempt)));
            }
        }

        throw new \RuntimeException('AI service request failed: ' . $lastError);
    }
}
