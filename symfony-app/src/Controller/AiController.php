<?php

declare(strict_types=1);

namespace App\Controller;

use App\Event\QueryAnswered;
use App\Repository\QueryLogRepository;
use App\Service\AiClient;
use App\Service\CitationExtractor;
use Psr\EventDispatcher\EventDispatcherInterface;
use Symfony\Bundle\FrameworkBundle\Controller\AbstractController;
use Symfony\Component\HttpFoundation\JsonResponse;
use Symfony\Component\HttpFoundation\Request;
use Symfony\Component\HttpFoundation\Response;
use Symfony\Component\Routing\Attribute\Route;

/**
 * The same three endpoints as php-app and node-app, plus one this surface earns
 * by having a database: /api/audit.
 */
final class AiController extends AbstractController
{
    public function __construct(
        private readonly AiClient $ai,
        private readonly EventDispatcherInterface $events,
        private readonly CitationExtractor $citations,
    ) {
    }

    #[Route('/api/health', methods: ['GET'])]
    public function health(): JsonResponse
    {
        return $this->guard(fn () => $this->ai->health());
    }

    #[Route('/api/ingest', methods: ['POST'])]
    public function ingest(Request $request): JsonResponse
    {
        $payload = $this->decode($request);
        $docId = trim((string) ($payload['doc_id'] ?? ''));
        $text = (string) ($payload['text'] ?? '');

        if ($docId === '' || $text === '') {
            return new JsonResponse(['error' => 'doc_id and text are required'], Response::HTTP_BAD_REQUEST);
        }

        return $this->guard(fn () => $this->ai->ingest($docId, $text));
    }

    #[Route('/api/query', methods: ['POST'])]
    public function query(Request $request): JsonResponse
    {
        $payload = $this->decode($request);
        $question = trim((string) ($payload['question'] ?? ''));
        $k = (int) ($payload['k'] ?? 4);

        if ($question === '') {
            return new JsonResponse(['error' => 'question is required'], Response::HTTP_BAD_REQUEST);
        }

        $startedAt = microtime(true);

        return $this->guard(function () use ($question, $k, $startedAt) {
            $result = $this->ai->query($question, $k);
            $latencyMs = (int) round((microtime(true) - $startedAt) * 1000);

            /** @var list<string> $sources */
            $sources = array_values(array_map('strval', $result['sources'] ?? []));

            $answer = (string) ($result['answer'] ?? '');
            $citations = $this->citations->analyse($answer, $sources);

            // Answer first, bookkeeping second — the listener persists this and is
            // written so that a failure there cannot take the answer down.
            $this->events->dispatch(new QueryAnswered(
                question: $question,
                answer: $answer,
                sources: $sources,
                provider: (string) ($result['provider'] ?? 'unknown'),
                latencyMs: $latencyMs,
                groundedCitations: $citations['grounded'],
                inventedCitations: $citations['invented'],
            ));

            return $result;
        });
    }

    /**
     * The audit trail, and the one number worth watching: how often the system
     * answered without citing anything.
     */
    #[Route('/api/audit', methods: ['GET'])]
    public function audit(QueryLogRepository $repository): JsonResponse
    {
        $recent = $repository->findRecent(20);

        return new JsonResponse([
            'ungrounded_share' => round($repository->ungroundedShare(100), 3),
            'invented_citation_count' => $repository->countWithInventedCitations(100),
            'entries' => array_map(static fn ($log) => [
                'id' => $log->getId(),
                'question' => $log->getQuestion(),
                'sources' => $log->getSources(),
                'cited_grounded' => $log->getGroundedCitations(),
                'cited_invented' => $log->getInventedCitations(),
                'provider' => $log->getProvider(),
                'latency_ms' => $log->getLatencyMs(),
                'ungrounded' => $log->isUngrounded(),
                'created_at' => $log->getCreatedAt()->format(\DATE_ATOM),
            ], $recent),
        ]);
    }

    /** @return array<string,mixed> */
    private function decode(Request $request): array
    {
        $decoded = json_decode($request->getContent(), true);

        return is_array($decoded) ? $decoded : [];
    }

    /**
     * Turns an AiClient failure into 502 rather than 500.
     *
     * The distinction matters operationally: 500 says this application broke, 502
     * says the dependency behind it did. Collapsing the two sends whoever is on
     * call to the wrong logs.
     *
     * @param callable():array<string,mixed> $call
     */
    private function guard(callable $call): JsonResponse
    {
        try {
            return new JsonResponse($call());
        } catch (\RuntimeException $e) {
            return new JsonResponse(['error' => $e->getMessage()], Response::HTTP_BAD_GATEWAY);
        }
    }
}
