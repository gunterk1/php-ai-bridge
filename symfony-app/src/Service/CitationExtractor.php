<?php

declare(strict_types=1);

namespace App\Service;

/**
 * Pulls the `[id]` citations out of an answer and checks them against what the
 * retriever actually returned.
 *
 * This is the PHP twin of `check_citations` in ai-service/eval/metrics.py. The
 * evaluation suite runs it offline over a labelled dataset; here the same check
 * runs online over live traffic and lands in the audit trail. One definition of
 * "grounded", enforced in two places, is worth more than two definitions that
 * drift apart.
 */
final class CitationExtractor
{
    private const CITATION = '/\[([^\[\]]{1,120})\]/';

    /**
     * @param list<string> $retrieved
     * @return array{cited: list<string>, grounded: list<string>, invented: list<string>}
     */
    public function analyse(string $answer, array $retrieved): array
    {
        preg_match_all(self::CITATION, $answer, $matches);

        /** @var list<string> $cited */
        $cited = array_values(array_unique($matches[1] ?? []));
        $available = array_flip($retrieved);

        $grounded = [];
        $invented = [];
        foreach ($cited as $id) {
            if (isset($available[$id])) {
                $grounded[] = $id;
            } else {
                $invented[] = $id;
            }
        }

        return ['cited' => $cited, 'grounded' => $grounded, 'invented' => $invented];
    }
}
