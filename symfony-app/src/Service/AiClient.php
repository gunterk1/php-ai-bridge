<?php

declare(strict_types=1);

namespace App\Service;

use Symfony\Component\HttpClient\Exception\TransportException;
use Symfony\Contracts\HttpClient\Exception\ExceptionInterface;
use Symfony\Contracts\HttpClient\HttpClientInterface;

/**
 * REST client for the Python AI service — the third implementation of the same
 * contract in this repository.
 *
 * php-app writes it with curl, node-app with fetch, and this one with Symfony's
 * HttpClient. The behaviour is identical on purpose: network errors and 5xx are
 * retried twice with 200 ms then 400 ms of backoff, and any 4xx is surfaced
 * immediately because the same request would fail the same way — including a 429
 * from an exhausted quota, where retrying only burns budget and hides the cause.
 *
 * Symfony ships RetryableHttpClient, which would express the policy declaratively
 * in configuration. It is deliberately not used here. Three surfaces exist to make
 * one claim checkable — that the integration pattern, not the app framework, is
 * what carries the AI capability — and hiding the policy inside framework config
 * would remove the very thing being compared.
 */
final class AiClient
{
    private string $baseUrl;

    public function __construct(
        private readonly HttpClientInterface $http,
        string $baseUrl,
        private readonly int $maxRetries = 2,
        private readonly int $backoffBaseMicroseconds = 200_000,
    ) {
        $this->baseUrl = rtrim($baseUrl, '/');
    }

    /** @return array<string,mixed> */
    public function health(): array
    {
        return $this->request('GET', '/health', null);
    }

    /** @return array<string,mixed> */
    public function ingest(string $docId, string $text): array
    {
        return $this->request('POST', '/ingest', ['doc_id' => $docId, 'text' => $text]);
    }

    /** @return array<string,mixed> */
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
        $lastError = 'no attempt was made';

        for ($attempt = 0; $attempt <= $this->maxRetries; $attempt++) {
            $options = [];
            if ($payload !== null) {
                $options['json'] = $payload;
            }

            try {
                $response = $this->http->request($method, $this->baseUrl . $path, $options);
                $status = $response->getStatusCode();

                if ($status >= 200 && $status < 300) {
                    /** @var array<string,mixed> $decoded */
                    $decoded = $response->toArray(false);

                    return $decoded;
                }

                $lastError = $this->describe($status, $response->getContent(false));

                // 4xx is deterministic: the same request fails the same way.
                if ($status < 500) {
                    break;
                }
            } catch (TransportException $e) {
                // Network-level failure — no response at all. Worth another try.
                $lastError = $e->getMessage();
            } catch (ExceptionInterface $e) {
                $lastError = $e->getMessage();
                break;
            }

            if ($attempt < $this->maxRetries) {
                usleep($this->backoffBaseMicroseconds * (2 ** $attempt));
            }
        }

        throw new \RuntimeException('AI service request failed: ' . $lastError);
    }

    private function describe(int $status, string $body): string
    {
        $detail = '';
        if ($body !== '') {
            $parsed = json_decode($body, true);
            if (is_array($parsed)) {
                $detail = (string) ($parsed['detail'] ?? $parsed['error'] ?? '');
            }
        }

        return $detail !== ''
            ? sprintf('HTTP %d: %s', $status, $detail)
            : sprintf('HTTP %d', $status);
    }
}
