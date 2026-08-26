"""Kod tabanı soru-cevap asistanı — indeksleme katmanı."""

from .answer import Answer, CodebaseAnswerer
from .docs import extract_from_markdown, index_docs
from .embeddings import Embedder, EmbeddingCache, embed_records, get_embedder, tokenize
from .indexer import extract_from_source, index_file, index_repo, iter_source_files
from .models import Chunk, IndexStats
from .search import HybridSearch, SearchHit

__all__ = [
    "Answer",
    "Chunk",
    "CodebaseAnswerer",
    "Embedder",
    "EmbeddingCache",
    "HybridSearch",
    "IndexStats",
    "SearchHit",
    "embed_records",
    "extract_from_markdown",
    "extract_from_source",
    "get_embedder",
    "index_docs",
    "index_file",
    "index_repo",
    "iter_source_files",
    "tokenize",
]
