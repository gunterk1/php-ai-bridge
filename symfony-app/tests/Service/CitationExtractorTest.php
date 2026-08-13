<?php

declare(strict_types=1);

namespace App\Tests\Service;

use App\Service\CitationExtractor;
use PHPUnit\Framework\TestCase;

/**
 * Mirrors the assertions in ai-service/eval/test_metrics.py for check_citations.
 * Same definition of "grounded" on both sides of the boundary, held in place by
 * tests on both sides.
 */
final class CitationExtractorTest extends TestCase
{
    private CitationExtractor $extractor;

    protected function setUp(): void
    {
        $this->extractor = new CitationExtractor();
    }

    public function testGroundedCitation(): void
    {
        $r = $this->extractor->analyse('As stated in [a#0], the boundary is REST.', ['a#0', 'b#0']);

        self::assertSame(['a#0'], $r['cited']);
        self::assertSame(['a#0'], $r['grounded']);
        self::assertSame([], $r['invented']);
    }

    public function testInventedCitationIsCaught(): void
    {
        // Indistinguishable to a reader; obvious to a set check.
        $r = $this->extractor->analyse('According to [ghost#7] the answer is 42.', ['a#0']);

        self::assertSame(['ghost#7'], $r['invented']);
        self::assertSame([], $r['grounded']);
    }

    public function testMixedGroundedAndInvented(): void
    {
        $r = $this->extractor->analyse('See [a#0] and [ghost#7].', ['a#0']);

        self::assertSame(['a#0', 'ghost#7'], $r['cited']);
        self::assertSame(['a#0'], $r['grounded']);
        self::assertSame(['ghost#7'], $r['invented']);
    }

    public function testPlaceholderCitationOnAnAbstentionCountsAsInvented(): void
    {
        // Verbatim from the first live run: the model abstained correctly and then
        // cited something that does not exist. Harmless in substance, still a
        // broken contract — and the case that exposed the original metric.
        $r = $this->extractor->analyse('I do not know. [source#0]', ['boundary#0', 'retry#0']);

        self::assertSame(['source#0'], $r['invented']);
        self::assertSame([], $r['grounded']);
    }

    public function testRepeatedCitationCountsOnce(): void
    {
        $r = $this->extractor->analyse('[a#0] says so, and [a#0] again.', ['a#0']);

        self::assertSame(['a#0'], $r['cited']);
    }

    public function testNoCitationsAtAll(): void
    {
        $r = $this->extractor->analyse('I do not know.', ['a#0']);

        self::assertSame([], $r['cited']);
        self::assertSame([], $r['grounded']);
        self::assertSame([], $r['invented']);
    }

    public function testEmptyAnswer(): void
    {
        $r = $this->extractor->analyse('', ['a#0']);

        self::assertSame([], $r['cited']);
    }
}
