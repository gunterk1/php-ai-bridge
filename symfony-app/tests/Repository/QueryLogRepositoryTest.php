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
        $this->persist(new QueryLog('why?', 'because [a#0]', ['a#0', 'b#1'], 'local', 120));
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
                'answer',
                ['a#0'],
                'local',
                10,
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
        $this->persist(new QueryLog('a', 'grounded', ['a#0'], 'local', 10));
        $this->persist(new QueryLog('b', 'grounded', ['b#0'], 'local', 10));
        $this->persist(new QueryLog('c', 'I do not know.', [], 'local', 10));
        $this->persist(new QueryLog('d', 'I do not know.', [], 'local', 10));

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
            $this->persist(new QueryLog("old {$i}", 'grounded', ['a#0'], 'local', 10, $base->modify("+{$i} minutes")));
        }
        foreach ([3, 4] as $i) {
            $this->persist(new QueryLog("new {$i}", 'no idea', [], 'local', 10, $base->modify("+{$i} minutes")));
        }

        self::assertSame(1.0, $this->repository->ungroundedShare(2));
        self::assertSame(0.4, $this->repository->ungroundedShare(5));
    }

    public function testIsUngroundedReflectsEmptySources(): void
    {
        self::assertTrue((new QueryLog('q', 'a', [], 'local', 1))->isUngrounded());
        self::assertFalse((new QueryLog('q', 'a', ['x#0'], 'local', 1))->isUngrounded());
    }
}
