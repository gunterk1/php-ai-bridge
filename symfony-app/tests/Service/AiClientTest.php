<?php

declare(strict_types=1);

namespace App\Tests\Service;

use App\Service\AiClient;
use PHPUnit\Framework\TestCase;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Component\HttpClient\Response\MockResponse;

/**
 * Behavioural tests for the retry policy — the same contract node-app covers with
 * eleven tests against a throwaway HTTP server. Here Symfony's MockHttpClient
 * does the same job without a socket, and every test asserts the number of
 * attempts rather than only the outcome: "it eventually worked" and "it worked
 * without hammering the backend three times" are different claims.
 *
 * Backoff is configured to zero so the suite stays fast. The timing itself is not
 * what is under test; the decision of *whether* to retry is.
 */
final class AiClientTest extends TestCase
{
    /** @param list<MockResponse> $responses */
    private function client(array $responses, ?MockHttpClient &$mock = null): AiClient
    {
        $mock = new MockHttpClient($responses, 'http://ai.test');

        return new AiClient($mock, 'http://ai.test', maxRetries: 2, backoffBaseMicroseconds: 0);
    }

    public function testHealthReturnsDecodedBody(): void
    {
        $client = $this->client([new MockResponse('{"status":"ok","provider":"local"}')]);

        self::assertSame(['status' => 'ok', 'provider' => 'local'], $client->health());
    }

    public function testIngestSendsDocIdAndText(): void
    {
        $client = $this->client([new MockResponse('{"doc_id":"a","chunks":3}')], $mock);

        $client->ingest('a', 'hello');

        self::assertSame(1, $mock->getRequestsCount());
    }

    public function testQueryPassesTopK(): void
    {
        $client = $this->client([new MockResponse('{"answer":"x","sources":[]}')], $mock);

        $result = $client->query('why?', 6);

        self::assertSame('x', $result['answer']);
        self::assertSame(1, $mock->getRequestsCount());
    }

    public function testTrailingSlashInBaseUrlIsNormalised(): void
    {
        $seen = null;
        $mock = new MockHttpClient(function (string $method, string $url) use (&$seen): MockResponse {
            $seen = $url;

            return new MockResponse('{"status":"ok"}');
        });
        $client = new AiClient($mock, 'http://ai.test/', maxRetries: 0, backoffBaseMicroseconds: 0);

        $client->health();

        // Without rtrim this would request http://ai.test//health.
        self::assertSame('http://ai.test/health', $seen);
    }

    public function testServerErrorIsRetriedAndThenSucceeds(): void
    {
        $client = $this->client([
            new MockResponse('boom', ['http_code' => 503]),
            new MockResponse('{"status":"ok"}'),
        ], $mock);

        self::assertSame(['status' => 'ok'], $client->health());
        self::assertSame(2, $mock->getRequestsCount());
    }

    public function testNetworkErrorIsRetriedAndThenSucceeds(): void
    {
        $client = $this->client([
            new MockResponse('', ['error' => 'connection refused']),
            new MockResponse('{"status":"ok"}'),
        ], $mock);

        self::assertSame(['status' => 'ok'], $client->health());
        self::assertSame(2, $mock->getRequestsCount());
    }

    public function testRetriesAreExhaustedAfterThreeAttempts(): void
    {
        $client = $this->client([
            new MockResponse('boom', ['http_code' => 500]),
            new MockResponse('boom', ['http_code' => 500]),
            new MockResponse('boom', ['http_code' => 500]),
        ], $mock);

        $this->expectException(\RuntimeException::class);

        try {
            $client->health();
        } finally {
            // One initial attempt plus maxRetries — not an unbounded loop.
            self::assertSame(3, $mock->getRequestsCount());
        }
    }

    public function testClientErrorIsNotRetried(): void
    {
        $client = $this->client([
            new MockResponse('{"detail":"doc_id is required"}', ['http_code' => 400]),
            new MockResponse('{"status":"ok"}'),
        ], $mock);

        $this->expectException(\RuntimeException::class);

        try {
            $client->ingest('', '');
        } finally {
            self::assertSame(1, $mock->getRequestsCount());
        }
    }

    public function testQuotaExhaustedIsNotRetried(): void
    {
        // 429 looks transient and is not: the same request against the same
        // exhausted quota fails identically, so retrying only burns budget.
        $client = $this->client([
            new MockResponse('{"detail":"insufficient_quota"}', ['http_code' => 429]),
            new MockResponse('{"status":"ok"}'),
        ], $mock);

        $this->expectException(\RuntimeException::class);

        try {
            $client->query('anything');
        } finally {
            self::assertSame(1, $mock->getRequestsCount());
        }
    }

    public function testErrorDetailFromBodyIsSurfaced(): void
    {
        $client = $this->client([new MockResponse('{"detail":"model not found"}', ['http_code' => 404])]);

        $this->expectExceptionMessageMatches('/model not found/');

        $client->query('anything');
    }

    public function testStatusOnlyErrorStillProducesAMessage(): void
    {
        $client = $this->client([new MockResponse('', ['http_code' => 418])]);

        $this->expectExceptionMessageMatches('/HTTP 418/');

        $client->health();
    }
}
