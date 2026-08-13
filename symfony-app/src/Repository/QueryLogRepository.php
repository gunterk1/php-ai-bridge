<?php

declare(strict_types=1);

namespace App\Repository;

use App\Entity\QueryLog;
use Doctrine\Bundle\DoctrineBundle\Repository\ServiceEntityRepository;
use Doctrine\Persistence\ManagerRegistry;

/**
 * @extends ServiceEntityRepository<QueryLog>
 */
final class QueryLogRepository extends ServiceEntityRepository
{
    public function __construct(ManagerRegistry $registry)
    {
        parent::__construct($registry, QueryLog::class);
    }

    /** @return list<QueryLog> */
    public function findRecent(int $limit = 20): array
    {
        /** @var list<QueryLog> $result */
        $result = $this->createQueryBuilder('q')
            ->orderBy('q.createdAt', 'DESC')
            ->setMaxResults($limit)
            ->getQuery()
            ->getResult();

        return $result;
    }

    /**
     * Share of answers that cited no source, over the most recent entries.
     *
     * The single number worth watching in production. A rising ungrounded share
     * means the retriever stopped finding material, the corpus drifted away from
     * what users ask, or the model started answering from its own weights — three
     * different problems that all look fine from the outside until someone checks.
     */
    public function ungroundedShare(int $window = 100): float
    {
        $rows = $this->createQueryBuilder('q')
            ->select('q.sources')
            ->orderBy('q.createdAt', 'DESC')
            ->setMaxResults($window)
            ->getQuery()
            ->getArrayResult();

        if ($rows === []) {
            return 0.0;
        }

        $ungrounded = 0;
        foreach ($rows as $row) {
            if (($row['sources'] ?? []) === []) {
                $ungrounded++;
            }
        }

        return $ungrounded / count($rows);
    }
}
