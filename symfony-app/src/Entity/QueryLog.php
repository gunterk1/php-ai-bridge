<?php

declare(strict_types=1);

namespace App\Entity;

use App\Repository\QueryLogRepository;
use Doctrine\DBAL\Types\Types;
use Doctrine\ORM\Mapping as ORM;

/**
 * One answered question, recorded for audit.
 *
 * This is the reason the Symfony surface exists at all rather than being a third
 * copy of the same stateless proxy. php-app and node-app forget every request the
 * moment they return it. In a regulated setting — the health-tech and finance
 * contexts this pattern keeps turning up in — you have to be able to say, months
 * later, which question was asked, which passages the answer was built from, and
 * which model produced it. That is a persistence problem, and persistence is what
 * Doctrine is for.
 *
 * Sources are stored as a JSON column rather than a join table. They are an
 * immutable snapshot of what the retriever returned at that moment, never queried
 * relationally, and never edited. A join table would model a relationship that
 * does not exist.
 */
#[ORM\Entity(repositoryClass: QueryLogRepository::class)]
#[ORM\Table(name: 'query_log')]
#[ORM\Index(name: 'idx_query_log_created_at', columns: ['created_at'])]
class QueryLog
{
    #[ORM\Id]
    #[ORM\GeneratedValue]
    #[ORM\Column]
    private ?int $id = null;

    #[ORM\Column(type: Types::TEXT)]
    private string $question;

    #[ORM\Column(type: Types::TEXT)]
    private string $answer;

    /** @var list<string> Chunks the retriever returned. */
    #[ORM\Column(type: Types::JSON)]
    private array $sources;

    /** @var list<string> Ids the answer actually cited that were also retrieved. */
    #[ORM\Column(name: 'grounded_citations', type: Types::JSON)]
    private array $groundedCitations;

    /** @var list<string> Ids the answer cited that the retriever never returned. */
    #[ORM\Column(name: 'invented_citations', type: Types::JSON)]
    private array $inventedCitations;

    #[ORM\Column(length: 32)]
    private string $provider;

    #[ORM\Column]
    private int $latencyMs;

    #[ORM\Column(name: 'created_at', type: Types::DATETIME_IMMUTABLE)]
    private \DateTimeImmutable $createdAt;

    /**
     * @param list<string> $sources
     * @param list<string> $groundedCitations
     * @param list<string> $inventedCitations
     */
    public function __construct(
        string $question,
        string $answer,
        array $sources,
        string $provider,
        int $latencyMs,
        array $groundedCitations = [],
        array $inventedCitations = [],
        ?\DateTimeImmutable $createdAt = null,
    ) {
        $this->question = $question;
        $this->answer = $answer;
        $this->sources = $sources;
        $this->groundedCitations = $groundedCitations;
        $this->inventedCitations = $inventedCitations;
        $this->provider = $provider;
        $this->latencyMs = $latencyMs;
        $this->createdAt = $createdAt ?? new \DateTimeImmutable();
    }

    public function getId(): ?int
    {
        return $this->id;
    }

    public function getQuestion(): string
    {
        return $this->question;
    }

    public function getAnswer(): string
    {
        return $this->answer;
    }

    /** @return list<string> */
    public function getSources(): array
    {
        return $this->sources;
    }

    public function getProvider(): string
    {
        return $this->provider;
    }

    public function getLatencyMs(): int
    {
        return $this->latencyMs;
    }

    public function getCreatedAt(): \DateTimeImmutable
    {
        return $this->createdAt;
    }

    /** @return list<string> */
    public function getGroundedCitations(): array
    {
        return $this->groundedCitations;
    }

    /** @return list<string> */
    public function getInventedCitations(): array
    {
        return $this->inventedCitations;
    }

    /**
     * True when the answer cited nothing the retriever had actually returned.
     *
     * Note what this deliberately does NOT look at: whether the retriever returned
     * anything. Top-k is unconditional, so `sources` is populated for every query
     * including the ones the corpus cannot answer — a metric built on it reads 0
     * forever and tells you nothing. The first live run of this endpoint scored an
     * abstention ("I do not know. [source#0]") as grounded for exactly that reason.
     * What matters is whether the *answer* stood on retrieved material.
     */
    public function isUngrounded(): bool
    {
        return $this->groundedCitations === [];
    }
}
