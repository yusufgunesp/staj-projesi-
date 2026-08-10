"""Kod tabanı soru-cevap asistanı — indeksleme katmanı."""

from .docs import extract_from_markdown, index_docs
from .indexer import extract_from_source, index_file, index_repo, iter_python_files
from .models import Chunk, IndexStats

__all__ = [
    "Chunk",
    "IndexStats",
    "extract_from_markdown",
    "extract_from_source",
    "index_docs",
    "index_file",
    "index_repo",
    "iter_python_files",
]
