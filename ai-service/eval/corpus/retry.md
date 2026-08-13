# Retry policy

Transient failures are retried, deterministic ones are not. Network errors and 5xx
responses are retried twice with exponential backoff of 200 milliseconds and then
400 milliseconds.

Any 4xx response is surfaced immediately without a retry. This includes 429 from an
exhausted quota, which looks transient but is not: the same request repeated against
the same exhausted quota fails the same way, so retrying only burns budget and hides
the cause.

The PHP client and the TypeScript client implement identical behaviour, and the
TypeScript side has eleven behavioural tests covering it against a throwaway HTTP
server.
