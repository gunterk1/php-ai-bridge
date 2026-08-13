<?php

declare(strict_types=1);

namespace App\EventSubscriber;

use App\Entity\QueryLog;
use App\Event\QueryAnswered;
use Doctrine\ORM\EntityManagerInterface;
use Psr\Log\LoggerInterface;
use Symfony\Component\EventDispatcher\Attribute\AsEventListener;

/**
 * Writes the audit trail, and never breaks the answer while doing it.
 *
 * The user already has a correct answer by the time this runs. Letting a database
 * hiccup turn that into a 500 would trade a working feature for a bookkeeping
 * failure, so the write is guarded and logged instead. That is a deliberate
 * trade-off, not an oversight: if the audit trail were legally required to be
 * transactional with the answer, this listener would be the wrong design and the
 * write would belong inside the request path.
 */
final class QueryAuditSubscriber
{
    public function __construct(
        private readonly EntityManagerInterface $entityManager,
        private readonly LoggerInterface $logger,
    ) {
    }

    #[AsEventListener(event: QueryAnswered::class)]
    public function onQueryAnswered(QueryAnswered $event): void
    {
        try {
            $this->entityManager->persist(new QueryLog(
                $event->question,
                $event->answer,
                $event->sources,
                $event->provider,
                $event->latencyMs,
            ));
            $this->entityManager->flush();
        } catch (\Throwable $e) {
            $this->logger->error('Audit write failed; the answer was still delivered.', [
                'exception' => $e,
            ]);
        }
    }
}
