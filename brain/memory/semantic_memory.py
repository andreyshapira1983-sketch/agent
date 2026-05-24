"""
brain/memory/semantic_memory.py — Semantic Memory (Long-Term) + RAG

Stores facts, knowledge, and learned information.
Uses vector similarity search — finds relevant facts by meaning, not exact match.

S3.3 RAG additions:
  - ingest_document(path) — load file, chunk, store in ChromaDB
  - ingest_text(text, source) — chunk raw text and store
  - list_sources() — list all ingested documents with chunk counts
  - forget_source(source) — delete all chunks from a document
  - forget_namespace(namespace) — delete entire namespace

S1.3 additions:
  - auto-timestamp (ts) on every stored fact — enables recency reranking

Supported file types: .txt .md .py .js .ts .json .csv .html .pdf (needs pypdf)

Backend: ChromaDB (local, no server needed).
Falls back to simple keyword search if ChromaDB is not installed.
"""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PERSIST_DIR = "data/semantic"

# Default chunking parameters
_DEFAULT_CHUNK_WORDS = 150   # ~600 chars, fits comfortably in context
_DEFAULT_CHUNK_OVERLAP = 20  # words overlap between consecutive chunks


class SemanticMemory:
    """
    Long-term fact + document store with vector similarity search.
    Brain stores facts here and retrieves them by semantic relevance.

    S3.3: RAG pipeline — ingest_document() chunks files and stores them
    so the Brain can retrieve relevant passages for any query.

    Uses ChromaDB locally — no external service needed.
    If chromadb is not installed — falls back to keyword search.
    """

    def __init__(self, persist_dir: str = DEFAULT_PERSIST_DIR) -> None:
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._use_chroma: bool = False
        self._fallback_store: list[dict] = []
        self._collection = self._init_backend()

    # ------------------------------------------------------------------
    # Core fact API (unchanged from S3.2)
    # ------------------------------------------------------------------

    def store_fact(self, text: str, metadata: dict | None = None) -> str:
        """
        Store a single fact in long-term memory.
        Automatically stamps ts (Unix seconds) if not provided.
        Returns the ID of the stored fact.
        """
        fact_id = str(uuid.uuid4())
        # Build metadata — always include ts for recency reranking (S1.3)
        meta: dict = dict(metadata) if metadata else {}
        if "ts" not in meta:
            meta["ts"] = int(time.time())

        if self._use_chroma:
            self._collection.add(
                documents=[text],
                metadatas=[meta],
                ids=[fact_id],
            )
        else:
            self._fallback_store.append({"id": fact_id, "text": text, "meta": meta})

        logger.debug("[SemanticMemory] Stored fact id=%s", fact_id)
        return fact_id

    def recall(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Find facts/chunks semantically similar to the query.
        Returns list of {text, score, metadata}.
        """
        if not query.strip():
            return []

        if self._use_chroma:
            return self._chroma_search(query, top_k)
        else:
            return self._keyword_search(query, top_k)

    def forget_fact(self, fact_id: str) -> None:
        if self._use_chroma:
            self._collection.delete(ids=[fact_id])
        else:
            self._fallback_store[:] = [
                f for f in self._fallback_store if f["id"] != fact_id
            ]
        logger.info("[SemanticMemory] Deleted fact id=%s", fact_id)

    def count(self) -> int:
        if self._use_chroma:
            return self._collection.count()
        return len(self._fallback_store)

    # ------------------------------------------------------------------
    # S3.3 RAG API — document ingestion
    # ------------------------------------------------------------------

    def ingest_document(
        self,
        path: str | Path,
        *,
        namespace: str | None = None,
        chunk_size: int = _DEFAULT_CHUNK_WORDS,
        chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
        force: bool = False,
    ) -> int:
        """
        Load a file, split into overlapping chunks, store in ChromaDB.

        Args:
            path: path to the file (.txt .md .py .json .csv .html .pdf)
            namespace: optional tag to group documents (e.g. "project-docs")
            chunk_size: words per chunk
            chunk_overlap: words overlap between consecutive chunks
            force: re-ingest even if source already exists

        Returns:
            Number of chunks stored (0 if skipped).
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"[SemanticMemory] File not found: {p}")

        source = str(p.resolve())

        if not force and self._source_exists(source):
            logger.info("[SemanticMemory] Already ingested, skipping: %s", source)
            return 0

        text = self._read_file(p)
        if not text.strip():
            logger.warning("[SemanticMemory] Empty file, nothing to ingest: %s", p)
            return 0

        return self.ingest_text(
            text,
            source=source,
            namespace=namespace,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            force=force,
        )

    def ingest_text(
        self,
        text: str,
        *,
        source: str,
        namespace: str | None = None,
        chunk_size: int = _DEFAULT_CHUNK_WORDS,
        chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP,
        force: bool = False,
    ) -> int:
        """
        Chunk raw text and store all chunks in semantic memory.

        Args:
            text: raw text content
            source: identifier for the source (filename, URL, etc.)
            namespace: optional grouping tag
            chunk_size: words per chunk
            chunk_overlap: words overlap
            force: re-ingest even if source already exists

        Returns:
            Number of chunks stored.
        """
        if not force and self._source_exists(source):
            logger.info("[SemanticMemory] Already ingested, skipping: %s", source)
            return 0

        # If re-ingesting, clean up old chunks first
        if force:
            self.forget_source(source)

        chunks = self._chunk_text(text, chunk_size, chunk_overlap)
        if not chunks:
            return 0

        for i, chunk in enumerate(chunks):
            meta: dict = {"source": source, "chunk_index": i, "total_chunks": len(chunks)}
            if namespace:
                meta["namespace"] = namespace
            self.store_fact(chunk, metadata=meta)

        logger.info(
            "[SemanticMemory] Ingested %d chunks from source=%s namespace=%s",
            len(chunks), source, namespace,
        )
        return len(chunks)

    def list_sources(self) -> list[dict]:
        """
        List all ingested document sources.
        Returns list of {source, namespace, chunks}.
        """
        if self._use_chroma:
            try:
                results = self._collection.get(include=["metadatas"])
                metas = results.get("metadatas") or []
                sources: dict[str, dict] = {}
                for m in metas:
                    if not m or "source" not in m:
                        continue
                    src = m["source"]
                    if src not in sources:
                        sources[src] = {
                            "source": src,
                            "namespace": m.get("namespace"),
                            "chunks": 0,
                        }
                    sources[src]["chunks"] += 1
                return list(sources.values())
            except Exception as exc:
                logger.warning("[SemanticMemory] list_sources error: %s", exc)
                return []
        else:
            sources_fb: dict[str, dict] = {}
            for item in self._fallback_store:
                m = item.get("meta") or {}
                src = m.get("source")
                if not src:
                    continue
                if src not in sources_fb:
                    sources_fb[src] = {"source": src, "namespace": m.get("namespace"), "chunks": 0}
                sources_fb[src]["chunks"] += 1
            return list(sources_fb.values())

    def forget_source(self, source: str) -> int:
        """
        Delete all chunks belonging to a source.
        Returns number of deleted chunks.
        """
        if self._use_chroma:
            try:
                existing = self._collection.get(where={"source": source}, include=["metadatas"])
                ids = existing.get("ids") or []
                if ids:
                    self._collection.delete(ids=ids)
                logger.info("[SemanticMemory] Deleted %d chunks for source=%s", len(ids), source)
                return len(ids)
            except Exception as exc:
                logger.warning("[SemanticMemory] forget_source error: %s", exc)
                return 0
        else:
            before = len(self._fallback_store)
            self._fallback_store[:] = [
                f for f in self._fallback_store
                if (f.get("meta") or {}).get("source") != source
            ]
            removed = before - len(self._fallback_store)
            return removed

    def forget_namespace(self, namespace: str) -> int:
        """
        Delete all facts/chunks in a namespace.
        Returns number of deleted items.
        """
        if self._use_chroma:
            try:
                existing = self._collection.get(where={"namespace": namespace}, include=["metadatas"])
                ids = existing.get("ids") or []
                if ids:
                    self._collection.delete(ids=ids)
                logger.info("[SemanticMemory] Deleted %d items for namespace=%s", len(ids), namespace)
                return len(ids)
            except Exception as exc:
                logger.warning("[SemanticMemory] forget_namespace error: %s", exc)
                return 0
        else:
            before = len(self._fallback_store)
            self._fallback_store[:] = [
                f for f in self._fallback_store
                if (f.get("meta") or {}).get("namespace") != namespace
            ]
            return before - len(self._fallback_store)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _source_exists(self, source: str) -> bool:
        """Return True if any chunk with this source already exists."""
        if self._use_chroma:
            try:
                r = self._collection.get(where={"source": source}, limit=1, include=[])
                return bool(r.get("ids"))
            except Exception:
                return False
        return any((f.get("meta") or {}).get("source") == source for f in self._fallback_store)

    def _chunk_text(self, text: str, chunk_size: int, overlap: int) -> list[str]:
        """
        Split text into overlapping word-based chunks.
        Each chunk is ~chunk_size words; consecutive chunks share `overlap` words.
        """
        words = text.split()
        if not words:
            return []
        step = max(1, chunk_size - overlap)
        chunks = []
        for start in range(0, len(words), step):
            chunk_words = words[start: start + chunk_size]
            chunk = " ".join(chunk_words).strip()
            if chunk:
                chunks.append(chunk)
            if start + chunk_size >= len(words):
                break
        return chunks

    def _read_file(self, path: Path) -> str:
        """
        Read text content from supported file types.
        Supported: .txt .md .py .js .ts .json .csv .html .pdf
        """
        suffix = path.suffix.lower()

        if suffix == ".pdf":
            return self._read_pdf(path)

        # All other text-based formats
        encodings = ["utf-8", "utf-8-sig", "cp1251", "latin-1"]
        for enc in encodings:
            try:
                return path.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        raise ValueError(f"[SemanticMemory] Cannot decode file: {path}")

    def _read_pdf(self, path: Path) -> str:
        """Extract text from PDF (requires pypdf)."""
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ImportError(
                "[SemanticMemory] PDF support requires pypdf. "
                "Install with: pip install pypdf"
            )
        reader = PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)

    def _init_backend(self) -> Any:
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(self._persist_dir))
            collection = client.get_or_create_collection(
                name="semantic_memory",
                metadata={"hnsw:space": "cosine"},
            )
            self._use_chroma = True
            logger.info("[SemanticMemory] ChromaDB backend ready | dir=%s", self._persist_dir)
            return collection
        except ImportError:
            logger.warning(
                "[SemanticMemory] chromadb not installed — using keyword fallback. "
                "Install with: pip install chromadb"
            )
            self._use_chroma = False
            return None

    def _chroma_search(self, query: str, top_k: int) -> list[dict]:
        results = self._collection.query(
            query_texts=[query],
            n_results=min(top_k, max(self._collection.count(), 1)),
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0] or [{}] * len(docs)
        distances = results.get("distances", [[]])[0]

        output = []
        for doc, meta, dist in zip(docs, metas, distances):
            output.append({
                "text": doc,
                "score": round(1.0 - dist, 4),  # cosine: closer = higher score
                "metadata": meta,
            })
        return output

    def _keyword_search(self, query: str, top_k: int) -> list[dict]:
        """Simple keyword overlap fallback when ChromaDB unavailable."""
        query_words = set(query.lower().split())
        scored = []
        for item in self._fallback_store:
            words = set(item["text"].lower().split())
            score = len(query_words & words) / max(len(query_words), 1)
            if score > 0:
                scored.append({"text": item["text"], "score": score, "metadata": item["meta"]})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]
