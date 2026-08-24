"""Sembol çıkarıcının testleri."""

from __future__ import annotations

import textwrap

import pytest

from codeqa.indexer import extract_from_source, index_repo, iter_python_files

SAMPLE = textwrap.dedent(
    '''
    """Sipariş servisi."""

    import json
    from typing import Any


    def normalize(value: str) -> str:
        """Boşlukları temizler."""
        return value.strip()


    class OrderService:
        """Sipariş akışını yönetir."""

        TIMEOUT = 30

        @property
        def name(self) -> str:
            return "orders"

        async def create(self, payload: dict[str, Any], *, dry_run: bool = False) -> int:
            def inner() -> None:
                pass

            inner()
            return 1
    '''
).strip()


@pytest.fixture
def chunks():
    return extract_from_source(SAMPLE, "api/orders.py")


def by_name(chunks, name):
    return next(c for c in chunks if c.name == name)


def test_module_chunk_has_docstring_and_imports(chunks):
    module = by_name(chunks, "api.orders")
    assert module.kind == "module"
    assert "Sipariş servisi." in module.text
    assert "import json" in module.text
    assert "from typing import Any" in module.text


def test_top_level_function(chunks):
    func = by_name(chunks, "normalize")
    assert func.kind == "function"
    assert func.signature == "def normalize(value: str) -> str:"
    assert func.docstring == "Boşlukları temizler."
    assert func.parent is None
    assert "return value.strip()" in func.text


def test_class_chunk_summarizes_methods(chunks):
    cls = by_name(chunks, "OrderService")
    assert cls.kind == "class"
    assert cls.signature == "class OrderService:"
    assert "def name(self) -> str:" in cls.text
    assert "async def create" in cls.text
    # Sınıf parçası metot gövdelerini tekrar etmemeli.
    assert "return 1" not in cls.text


def test_methods_are_separate_chunks(chunks):
    method = by_name(chunks, "OrderService.create")
    assert method.kind == "method"
    assert method.parent == "OrderService"
    assert method.signature == (
        "async def create(self, payload: dict[str, Any], *, dry_run: bool=False) -> int:"
    )
    assert "return 1" in method.text


def test_decorator_included_in_span(chunks):
    prop = by_name(chunks, "OrderService.name")
    assert prop.text.lstrip().startswith("@property")


def test_nested_function_is_not_a_chunk(chunks):
    assert not any(c.name.endswith("inner") for c in chunks)


def test_line_numbers_point_at_real_source(chunks):
    """Fonksiyon/metot parçaları kaynağın birebir kopyası — satırlar tutmalı.

    Modül ve sınıf parçalarının metni özet olarak üretiliyor, o yüzden birebir
    eşleşme beklenmiyor; onlarda sadece aralığın geçerli olması yeterli.
    """
    lines = SAMPLE.splitlines()
    for chunk in chunks:
        assert 1 <= chunk.start_line <= len(lines), chunk.name
        assert chunk.start_line <= chunk.end_line <= len(lines), chunk.name
        if chunk.kind in {"function", "method"}:
            assert lines[chunk.start_line - 1] == chunk.text.splitlines()[0], chunk.name


def test_module_chunk_starts_at_first_import_when_no_docstring():
    source = "\n\nimport os\nimport sys\n\n\ndef f():\n    pass\n"
    module = next(c for c in extract_from_source(source, "m.py") if c.kind == "module")
    assert module.start_line == 3
    assert module.end_line == 4


def test_location_format(chunks):
    func = by_name(chunks, "normalize")
    assert func.location == f"api/orders.py:{func.start_line}"


def test_embed_text_includes_context(chunks):
    func = by_name(chunks, "normalize")
    assert func.context in func.embed_text
    assert func.text in func.embed_text


def test_content_hash_covers_context_not_just_text():
    """Gövde aynı, bağlam farklı → hash farklı olmalı.

    Aksi hâlde iki dosyadaki birebir aynı fonksiyon tek vektörü paylaşır ve
    bir dosya taşındığında önbellekten eski bağlamla üretilmiş vektör döner.
    """
    body = "def normalize(value):\n    return value.strip()\n"
    first = extract_from_source(body, "api/orders.py")[0]
    second = extract_from_source(body, "web/orders.py")[0]

    assert first.text == second.text
    assert first.content_hash != second.content_hash


def test_content_hash_is_stable_for_identical_chunks():
    body = "def normalize(value):\n    return value.strip()\n"
    first = extract_from_source(body, "api/orders.py")[0]
    second = extract_from_source(body, "api/orders.py")[0]
    assert first.content_hash == second.content_hash


def test_ids_are_unique(chunks):
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids))


def test_empty_file_yields_no_chunks():
    assert extract_from_source("", "empty.py") == []


def test_syntax_error_is_reported_not_raised(tmp_path):
    (tmp_path / "good.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")

    chunks, stats = index_repo(tmp_path)

    assert stats.files_scanned == 2
    assert stats.files_failed == 1
    assert any("broken.py" in error for error in stats.errors)
    assert [c.name for c in chunks] == ["ok"]


def test_excluded_directories_are_skipped(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def a():\n    pass\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "dep.py").write_text("def b():\n    pass\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.py").write_text("def c():\n    pass\n", encoding="utf-8")

    found = {p.name for p in iter_python_files(tmp_path)}
    assert found == {"app.py"}


def test_relative_paths_are_repo_relative(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")

    chunks, _ = index_repo(tmp_path)

    assert all(c.path == "pkg/mod.py" for c in chunks)
