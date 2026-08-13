<?php

declare(strict_types=1);

namespace App\Event;

/**
 * Dispatched after the AI service has answered a question.
 *
 * The controller's job is to answer the caller; recording the answer for audit is
 * somebody else's. Wiring that through an event keeps the two apart, so the audit
 * trail can be extended, disabled or duplicated without editing the request path.
 * It also means a failure in the audit path cannot take the answer down with it —
 * see QueryAuditSubscriber.
 */
final class QueryAnswered
{
    /** @param list<string> $sources */
    public function __construct(
        public readonly string $question,
        public readonly string $answer,
        public readonly array $sources,
        public readonly string $provider,
        public readonly int $latencyMs,
    ) {
    }
}
