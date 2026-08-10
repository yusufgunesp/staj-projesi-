"""Markdown dokümanlarını başlıklara göre parçalara ayırır.

Kodun yanında README, mimari notları ve ADR'ler de sorulara cevap verirken
işe yarıyor — özellikle "neden böyle yapılmış" tipi sorularda. Kod tarafından
farklı olarak burada AST yok, o yüzden başlık hiyerarşisi bölme ölçütü.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Chunk

DOC_SUFFIXES = frozenset({".md", ".markdown", ".mdx", ".rst", ".txt"})

#: Bir parçanın hedef üst sınırı (karakter). Embedding modellerinin bağlam
#: sınırından çok daha düşük; amaç isabetli arama, sınırı doldurmak değil.
MAX_CHARS = 1500
OVERLAP_CHARS = 150

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def iter_doc_files(root: Path, excludes: frozenset[str]):
    """`root` altındaki doküman dosyalarını verir."""
    root = Path(root).resolve()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.name in excludes:
                continue
            if entry.is_symlink():
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.suffix.lower() in DOC_SUFFIXES:
                yield entry


def _split_long(text: str, start_line: int) -> list[tuple[str, int]]:
    """Çok uzun bir bölümü, satır sınırlarını bozmadan parçalara böler."""
    lines = text.splitlines()
    parts: list[tuple[str, int]] = []
    buffer: list[str] = []
    buffer_start = start_line
    size = 0
    for offset, line in enumerate(lines):
        if size + len(line) > MAX_CHARS and buffer:
            parts.append(("\n".join(buffer), buffer_start))
            # Bağlam kopmasın diye son birkaç satırı bir sonraki parçaya taşı.
            tail: list[str] = []
            tail_size = 0
            for prev in reversed(buffer):
                if tail_size + len(prev) > OVERLAP_CHARS:
                    break
                tail.insert(0, prev)
                tail_size += len(prev) + 1
            buffer = tail
            buffer_start = start_line + offset - len(tail)
            size = tail_size
        buffer.append(line)
        size += len(line) + 1
    if buffer and "".join(buffer).strip():
        parts.append(("\n".join(buffer), buffer_start))
    return parts


def extract_from_markdown(source: str, rel_path: str) -> list[Chunk]:
    """Markdown metnini başlık bölümlerine ayırır; kod bloklarını bozmaz."""
    lines = source.splitlines()
    sections: list[tuple[list[str], int, list[str]]] = []  # (başlık yolu, satır, gövde)
    heading_stack: list[str] = []
    body: list[str] = []
    section_start = 1
    in_fence = False

    def flush() -> None:
        if body and "".join(body).strip():
            sections.append((list(heading_stack), section_start, list(body)))

    for index, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack = heading_stack[: level - 1]
            while len(heading_stack) < level - 1:
                heading_stack.append("")
            heading_stack.append(title)
            body = [line]
            section_start = index + 1
        else:
            body.append(line)
    flush()

    chunks: list[Chunk] = []
    for path_parts, start_line, section_lines in sections:
        title_path = " > ".join(p for p in path_parts if p) or "(başlıksız)"
        text = "\n".join(section_lines).strip("\n")
        for part_text, part_start in _split_long(text, start_line):
            chunks.append(
                Chunk(
                    source="doc",
                    kind="section",
                    path=rel_path,
                    name=title_path,
                    start_line=part_start,
                    end_line=part_start + len(part_text.splitlines()) - 1,
                    text=part_text,
                    context=f"{rel_path} > {title_path} (doküman)",
                    parent=" > ".join(p for p in path_parts[:-1] if p) or None,
                    language="markdown",
                )
            )
    return chunks


def index_docs(root: Path, excludes: frozenset[str]) -> list[Chunk]:
    """Repodaki tüm doküman dosyalarını indeksler."""
    root = Path(root).resolve()
    chunks: list[Chunk] = []
    for file_path in iter_doc_files(root, excludes):
        rel_path = file_path.relative_to(root).as_posix()
        source = file_path.read_bytes().decode("utf-8", errors="replace")
        chunks.extend(extract_from_markdown(source, rel_path))
    return chunks
