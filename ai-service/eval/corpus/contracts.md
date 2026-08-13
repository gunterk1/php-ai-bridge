# Validating the boundary

A boundary is exactly where the compiler's guarantees stop. TypeScript knows nothing
about what FastAPI actually put on the wire, so casting a parsed response to an
interface keeps the feeling of type safety while discarding the substance.

Every response therefore passes through hand-written type guards. A violation raises
a ContractError naming both the endpoint and the offending field, which turns a
schema change on the Python side into an immediate, located error instead of an
undefined surfacing somewhere far away.

The tsconfig enables strict together with noUncheckedIndexedAccess. That combination
is what makes skipping a guard impossible rather than merely discouraged.
