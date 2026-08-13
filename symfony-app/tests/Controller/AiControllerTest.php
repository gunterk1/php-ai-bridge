<?php

declare(strict_types=1);

namespace App\Tests\Controller;

use App\Service\AiClient;
use Doctrine\ORM\EntityManagerInterface;
use Doctrine\ORM\Tools\SchemaTool;
use Symfony\Bundle\FrameworkBundle\Test\WebTestCase;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Component\HttpClient\Response\MockResponse;

/**
 * End-to-end through the Symfony surface, with the model replaced.
 *
 * The AI service is swapped for a MockHttpClient so the assertions are about this
 * application: does the firewall let the right requests through, does a successful
 * answer reach the caller, and — the part only this surface has — does the event
 * listener actually write the audit row.
 *
 * Doing it as a test rather than a curl session against a live model is not a
 * compromise. A manual run proves it worked once on one machine; this proves it on
 * every machine, every commit, and it names the behaviour it is protecting.
 */
final class AiControllerTest extends WebTestCase
{
    private function bootWithAiResponse(MockResponse ...$responses): \Symfony\Bundle\FrameworkBundle\KernelBrowser
    {
        $client = static::createClient();
        // Symfony reboots the kernel between requests by default, which would
        // discard both the mocked client and the in-memory database — the second
        // request in a test would talk to a different application than the first.
        $client->disableReboot();
        $container = static::getContainer();

        $mock = new MockHttpClient(array_values($responses), 'http://ai.test');
        $container->set(AiClient::class, new AiClient($mock, 'http://ai.test', maxRetries: 0, backoffBaseMicroseconds: 0));

        $em = $container->get(EntityManagerInterface::class);
        (new SchemaTool($em))->createSchema($em->getMetadataFactory()->getAllMetadata());

        return $client;
    }

    /** @return array<string,mixed> */
    private function json(\Symfony\Bundle\FrameworkBundle\KernelBrowser $client): array
    {
        /** @var array<string,mixed> $decoded */
        $decoded = json_decode((string) $client->getResponse()->getContent(), true);

        return $decoded;
    }

    public function testHealthIsPublic(): void
    {
        $client = $this->bootWithAiResponse(new MockResponse('{"status":"ok","provider":"local"}'));

        $client->request('GET', '/api/health');

        self::assertResponseIsSuccessful();
        self::assertSame('ok', $this->json($client)['status']);
    }

    public function testQueryWithoutApiKeyIsRejectedAsJson(): void
    {
        $client = $this->bootWithAiResponse();

        $client->request('POST', '/api/query', server: ['CONTENT_TYPE' => 'application/json'], content: '{"question":"x"}');

        // An HTML error page here would be a parse error for every JSON client.
        self::assertResponseStatusCodeSame(401);
        self::assertJson((string) $client->getResponse()->getContent());
        self::assertSame('Unauthorized', $this->json($client)['error']);
    }

    public function testQueryWithWrongApiKeyIsRejected(): void
    {
        $client = $this->bootWithAiResponse();

        $client->request('POST', '/api/query', server: [
            'CONTENT_TYPE' => 'application/json',
            'HTTP_X_API_KEY' => 'not-the-key',
        ], content: '{"question":"x"}');

        self::assertResponseStatusCodeSame(401);
    }

    public function testMissingQuestionIsRejectedBeforeCallingTheModel(): void
    {
        // No MockResponse is queued: if the controller called the AI service, the
        // mock would fail. Validation has to happen first.
        $client = $this->bootWithAiResponse();

        $client->request('POST', '/api/query', server: [
            'CONTENT_TYPE' => 'application/json',
            'HTTP_X_API_KEY' => 'test-key',
        ], content: '{"question":"   "}');

        self::assertResponseStatusCodeSame(400);
    }

    public function testAnsweredQueryIsWrittenToTheAuditTrail(): void
    {
        $client = $this->bootWithAiResponse(new MockResponse(
            '{"answer":"Because the boundary is REST [boundary#0].","sources":["boundary#0"],"provider":"local"}'
        ));

        $client->request('POST', '/api/query', server: [
            'CONTENT_TYPE' => 'application/json',
            'HTTP_X_API_KEY' => 'test-key',
        ], content: '{"question":"Why REST?","k":2}');

        self::assertResponseIsSuccessful();
        self::assertSame(['boundary#0'], $this->json($client)['sources']);

        $client->request('GET', '/api/audit', server: ['HTTP_X_API_KEY' => 'test-key']);
        $audit = $this->json($client);

        self::assertCount(1, $audit['entries']);
        self::assertSame('Why REST?', $audit['entries'][0]['question']);
        self::assertSame(['boundary#0'], $audit['entries'][0]['sources']);
        self::assertFalse($audit['entries'][0]['ungrounded']);
        // JSON has no int/float distinction for whole numbers: 0.0 comes back as
        // int 0. Cast rather than loosen the assertion.
        self::assertSame(0.0, (float) $audit['ungrounded_share']);
    }

    public function testAnswerWithoutSourcesIsRecordedAsUngrounded(): void
    {
        $client = $this->bootWithAiResponse(new MockResponse(
            '{"answer":"I do not know.","sources":[],"provider":"local"}'
        ));

        $client->request('POST', '/api/query', server: [
            'CONTENT_TYPE' => 'application/json',
            'HTTP_X_API_KEY' => 'test-key',
        ], content: '{"question":"What does it cost?"}');

        self::assertResponseIsSuccessful();

        $client->request('GET', '/api/audit', server: ['HTTP_X_API_KEY' => 'test-key']);
        $audit = $this->json($client);

        self::assertTrue($audit['entries'][0]['ungrounded']);
        self::assertSame(1.0, (float) $audit['ungrounded_share']);
    }

    public function testAiServiceFailureBecomes502NotAn500(): void
    {
        // 500 says this application broke; 502 says the dependency did. Collapsing
        // the two sends whoever is on call to the wrong logs.
        $client = $this->bootWithAiResponse(new MockResponse('{"detail":"model unavailable"}', ['http_code' => 503]));

        $client->request('POST', '/api/query', server: [
            'CONTENT_TYPE' => 'application/json',
            'HTTP_X_API_KEY' => 'test-key',
        ], content: '{"question":"anything"}');

        self::assertResponseStatusCodeSame(502);
    }
}
