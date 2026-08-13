"""RAG core: provider-agnostic document embedding + retrieval-augmented answering.

Both OpenAI and a self-hosted, OpenAI-compatible backend (LocalAI, Ollama, vLLM)
expose the same HTTP API, so a single code path serves both. You switch between
them with environment variables, never with code changes.

This mirrors Nextcloud's Ethical AI approach: the integration is identical whether
the model runs on your own server (no data leaves the building) or on an external
service. The choice is a deployment decision, not an application rewrite.
"""

from __future__ import annotations

import os

from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

_SYSTEM = (
    "You answer strictly from the provided context. "
    "If the answer is not in the context, say you do not know. "
    "Reference the source ids (shown in square brackets) that you used."
)


class RagEngine:
    """Embeds ingested text and answers questions grounded in the retrieved chunks.

    The vector store is kept in memory to keep the demo dependency-light. Swapping
    it for a persistent store (pgvector, Qdrant, Chroma) is a one-line change
    because everything goes through the LangChain VectorStore interface.
    """

    def __init__(self) -> None:
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        api_key = os.getenv("OPENAI_API_KEY", "not-needed-for-local")
        chat_model = os.getenv("CHAT_MODEL", "gpt-4o-mini")
        embed_model = os.getenv("EMBED_MODEL", "text-embedding-3-small")

        self.provider = os.getenv("AI_PROVIDER", "openai")
        self.embeddings = OpenAIEmbeddings(
            model=embed_model,
            base_url=base_url,
            api_key=api_key,
            # Local backends often do not ship the tiktoken vocab OpenAI uses;
            # letting the server count tokens keeps the local path working.
            check_embedding_ctx_length=self.provider != "local",
        )
        self.llm = ChatOpenAI(
            model=chat_model, base_url=base_url, api_key=api_key, temperature=0
        )
        self.store = InMemoryVectorStore(self.embeddings)
        # Chunk ids per document, so re-ingesting a doc_id can replace instead of
        # duplicate. Found by the evaluation suite: ingesting the same document twice
        # left both copies in the store, and a single query then returned the same
        # chunk in two of its top-k slots — half the context window spent on a
        # duplicate. See eval/README.md.
        self._chunk_ids: dict[str, list[str]] = {}
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=800, chunk_overlap=120
        )
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", _SYSTEM),
                ("human", "Context:\n{context}\n\nQuestion: {question}"),
            ]
        )

    def ingest(self, doc_id: str, text: str) -> int:
        """Chunk, embed and store a document. Returns the number of chunks stored.

        Idempotent per doc_id: ingesting the same document again replaces its chunks
        rather than adding a second copy. Without this, a re-ingest silently degrades
        every later query — duplicates crowd out genuinely different chunks in the
        top-k, and the answer is built from less material than it appears to be.
        """
        previous = self._chunk_ids.get(doc_id)
        if previous:
            self.store.delete(ids=previous)

        chunks = self.splitter.split_text(text)
        ids = [f"{doc_id}#{i}" for i in range(len(chunks))]
        docs = [
            Document(page_content=chunk, metadata={"source": doc_id, "chunk": i})
            for i, chunk in enumerate(chunks)
        ]
        self.store.add_documents(docs, ids=ids)
        self._chunk_ids[doc_id] = ids
        return len(docs)

    def query(self, question: str, k: int = 4) -> dict:
        """Retrieve the top-k chunks and answer the question grounded in them."""
        hits = self.store.similarity_search(question, k=k)
        if not hits:
            return {"answer": "No documents have been ingested yet.", "sources": []}

        context = "\n\n".join(
            f"[{d.metadata['source']}#{d.metadata['chunk']}] {d.page_content}"
            for d in hits
        )
        messages = self.prompt.format_messages(context=context, question=question)
        response = self.llm.invoke(messages)
        sources = [f"{d.metadata['source']}#{d.metadata['chunk']}" for d in hits]
        return {"answer": response.content, "sources": sources}
