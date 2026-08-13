# Provider independence

OpenAI and self-hosted servers such as LocalAI, Ollama and vLLM all expose the same
OpenAI-compatible HTTP API. One code path therefore serves both, and choosing where
the model runs becomes a deployment decision instead of a rewrite.

The switch is made entirely through environment variables. AI_PROVIDER selects the
mode, OPENAI_BASE_URL points at the endpoint, CHAT_MODEL and EMBED_MODEL name the
models, and OPENAI_API_KEY is set to not-needed for a local backend.

This matters when documents may not leave the building. A regulated department can
run the identical integration against a model on its own hardware.

One local-backend quirk is handled explicitly: self-hosted servers often lack the
tiktoken vocabulary that OpenAI uses, so client-side token counting is disabled when
AI_PROVIDER is local and the server counts instead.
