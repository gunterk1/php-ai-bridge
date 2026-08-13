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

    /** @var list<string> */
    #[ORM\Column(type: Types::JSON)]
    private array $sources;

    #[ORM\Column(length: 32)]
    private string $provider;

    #[ORM\Column]
    private int $latencyMs;

    #[ORM\Column(name: 'created_at', type: Types::DATETIME_IMMUTABLE)]
    private \DateTimeImmutable $createdAt;

    /** @param list<string> $sources */
    public function __construct(
        string $question,
        string $answer,
        array $sources,
        string $provider,
        int $latencyMs,
        ?\DateTimeImmutable $createdAt = null,
    ) {
        $this->question = $question;
        $this->answer = $answer;
        $this->sources = $sources;
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

    /**
     * True when the answer cited no retrieved passage.
     *
     * Cheap grounding signal for the audit view: an answer with no sources either
     * abstained or was produced without support. Both are worth looking at, and
     * the distinction is exactly the one the Python evaluation suite measures.
     */
    public function isUngrounded(): bool
    {
        return $this->sources === [];
    }
}
