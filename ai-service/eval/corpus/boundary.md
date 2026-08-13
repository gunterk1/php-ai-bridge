# The REST boundary

The PHP application never imports a model SDK. It calls a Python service over HTTP,
and that service owns everything model-related: chunking, embedding, retrieval and
answer generation.

The reason is operational rather than aesthetic. The two sides deploy, scale and fail
independently. A restarting model backend degrades into a retried request instead of
a user-facing error, and the AI service can be replaced wholesale without touching a
line of application code.

The boundary exposes exactly three endpoints: GET /health reports liveness and which
provider is configured, POST /ingest chunks and embeds a document, POST /query
retrieves the top-k chunks and answers from them.
