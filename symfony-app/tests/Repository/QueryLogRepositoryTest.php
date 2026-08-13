<?php

declare(strict_types=1);

namespace App\Tests\Repository;

use App\Entity\QueryLog;
use App\Repository\QueryLogRepository;
use Doctrine\ORM\EntityManagerInterface;
use Doctrine\ORM\Tools\SchemaTool;
use Symfony\Bundle\FrameworkBundle\Test\KernelTestCase;

/**
 * Doctrine tests against a real schema in an in-memory SQLite database.
 *
 * Mocking the EntityManager here would prove nothing: the interesting part is
 * whether the mapping, the DQL and the JSON column round-trip correctly, and a
 * mock answers whatever you told it to. Building the actual schema costs
 * milliseconds and tests the thing that breaks.
 */
final class QueryLogRepositoryTest extends KernelTestCase
{
    private EntityManagerInterface $em;
    private QueryLogRepository $repository;

    protected function setUp(): void
    {
        self::bootKernel();
        $container = static::getContainer();

        $this->em = $container->get(EntityManagerInterface::class);
        $this->repository = $container->get(QueryLogRepository::class);

        $schemaTool = new SchemaTool($this->em);
        $schemaTool->createSchema($this->em->getMetadataFactory()->getAllMetadata());
    }

    protected function tearDown(): void
    {
        $this->em->close();
        parent::tearDown();
    }

    private function persist(QueryLog $log): void
    {
        $this->em->persist($log);
        $this->em->flush();
    }

    public function testJsonSourcesRoundTrip(): void
    {
        // The column that would silently misbehave if the mapping were wrong.
        $this->persist(new QueryLog('why?', 'because [a#0]', ['a#0', 'b#1'], 'local', 120, ['a#0'], []));
        $this->em->clear();

        $found = $this->repository->findRecent(1)[0];

        self::assertSame(['a#0', 'b#1'], $found->getSources());
        self::assertSame('local', $found->getProvider());
        self::assertSame(120, $found->getLatencyMs());
    }

    public function testFindRecentOrdersNewestFirstAndRespectsLimit(): void
    {
        $base = new \DateTimeImmutable('2026-08-13 10:00:00');
        foreach ([0, 1, 2, 3] as $i) {
            $this->persist(new QueryLog(
                "question {$i}",
                'answer [a#0]',
                ['a#0'],
                'local',
                10,
                ['a#0'],
                [],
                $base->modify("+{$i} minutes"),
            ));
        }

        $recent = $this->repository->findRecent(2);

        self::assertCount(2, $recent);
        self::assertSame('question 3', $recent[0]->getQuestion());
        self::assertSame('question 2', $recent[1]->getQuestion());
    }

    public function testUngroundedShareCountsAnswersWithoutSources(): void
    {
        // Note the third and fourth: the retriever DID return chunks, the answer
        // just did not stand on them. That is the case the first implementation
        // scored as grounded, and the reason the metric now looks at citations.
        $this->persist(new QueryLog('a', 'grounded [a#0]', ['a#0'], 'local', 10, ['a#0'], []));
        $this->persist(new QueryLog('b', 'grounded [b#0]', ['b#0'], 'local', 10, ['b#0'], []));
        $this->persist(new QueryLog('c', 'I do not know.', ['a#0'], 'local', 10, [], []));
        $this->persist(new QueryLog('d', 'I do not know. [ghost#9]', ['a#0'], 'local', 10, [], ['ghost#9']));

        self::assertSame(0.5, $this->repository->ungroundedShare(100));
    }

    public function testUngroundedShareIsZeroOnAnEmptyTable(): void
    {
        // Division by zero is the obvious failure here, and returning 0.0 is the
        // honest answer: nothing was answered, so nothing was ungrounded.
        self::assertSame(0.0, $this->repository->ungroundedShare());
    }

    public function testUngroundedShareOnlyLooksAtTheWindow(): void
    {
        $base = new \DateTimeImmutable('2026-08-13 10:00:00');
        // Older entries are grounded, the two newest are not. A window of 2 must
        // report 1.0 — otherwise the metric would dilute a fresh problem in old
        // history, which is precisely what makes it useless in production.
        foreach ([0, 1, 2] as $i) {
            $this->persist(new QueryLog("old {$i}", 'grounded [a#0]', ['a#0'], 'local', 10, ['a#0'], [], $base->modify("+{$i} minutes")));
        }
        foreach ([3, 4] as $i) {
            $this->persist(new QueryLog("new {$i}", 'no idea', ['a#0'], 'local', 10, [], [], $base->modify("+{$i} minutes")));
        }

        self::assertSame(1.0, $this->repository->ungroundedShare(2));
        self::assertSame(0.4, $this->repository->ungroundedShare(5));
    }

    public function testIsUngroundedLooksAtCitationsNotRetrieval(): void
    {
        // The retriever returned something in both cases — top-k is unconditional,
        // so it almost always does. Only the second answer stood on it.
        self::assertTrue((new QueryLog('q', 'I do not know.', ['x#0'], 'local', 1, [], []))->isUngrounded());
        self::assertFalse((new QueryLog('q', 'because [x#0]', ['x#0'], 'local', 1, ['x#0'], []))->isUngrounded());
    }

    public function testCountWithInventedCitations(): void
    {
        $this->persist(new QueryLog('a', 'fine [a#0]', ['a#0'], 'local', 10, ['a#0'], []));
        $this->persist(new QueryLog('b', 'per [ghost#7]', ['a#0'], 'local', 10, [], ['ghost#7']));
        $this->persist(new QueryLog('c', 'mixed [a#0] and [ghost#8]', ['a#0'], 'local', 10, ['a#0'], ['ghost#8']));

        self::assertSame(2, $this->repository->countWithInventedCitations(100));
    }
}
