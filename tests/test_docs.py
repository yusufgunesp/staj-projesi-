"""Markdown parçalayıcının testleri."""

from __future__ import annotations

import textwrap

from codeqa.docs import MAX_CHARS, extract_from_markdown

SAMPLE = textwrap.dedent(
    """
    # Proje

    Giriş yazısı.

    ## Kurulum

    Adımlar burada.

    ```python
    # Bu bir başlık değil, kod bloğu içinde
    print("merhaba")
    ```

    ### Bağımlılıklar

    Liste.
    """
).strip()


def test_sections_split_by_heading():
    chunks = extract_from_markdown(SAMPLE, "README.md")
    names = [c.name for c in chunks]
    assert names == ["Proje", "Proje > Kurulum", "Proje > Kurulum > Bağımlılıklar"]


def test_heading_inside_code_fence_is_not_a_split():
    chunks = extract_from_markdown(SAMPLE, "README.md")
    kurulum = next(c for c in chunks if c.name == "Proje > Kurulum")
    assert "Bu bir başlık değil" in kurulum.text


def test_chunk_metadata():
    chunks = extract_from_markdown(SAMPLE, "docs/README.md")
    chunk = chunks[0]
    assert chunk.source == "doc"
    assert chunk.kind == "section"
    assert chunk.source == "doc"
    assert chunk.location.startswith("docs/README.md:")


def test_line_numbers_match_source():
    lines = SAMPLE.splitlines()
    for chunk in extract_from_markdown(SAMPLE, "README.md"):
        assert lines[chunk.start_line - 1] == chunk.text.splitlines()[0]


def test_long_section_is_split():
    body = "\n".join(f"satir {i} " + "x" * 60 for i in range(80))
    source = f"# Uzun\n\n{body}\n"

    chunks = extract_from_markdown(source, "big.md")

    assert len(chunks) > 1
    assert all(len(c.text) <= MAX_CHARS + 200 for c in chunks)


def test_text_before_first_heading_is_kept():
    chunks = extract_from_markdown("Başlıksız giriş metni.\n\n# Sonra\n\nDevam.", "a.md")
    assert chunks[0].name == "(başlıksız)"
    assert "Başlıksız giriş" in chunks[0].text
