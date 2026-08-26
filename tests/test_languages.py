"""Çok dilli sembol çıkarmanın testleri.

Her dil için küçük ama gerçekçi bir örnek: sınıf, metot, dosya seviyesi
fonksiyon ve import. Amaç gramerin doğru düğümlerini seçtiğimizi doğrulamak.
"""

from __future__ import annotations

import pytest

from codeqa.indexer import SOURCE_EXTENSIONS, index_repo
from codeqa.languages import LANGUAGES, extract_from_source, spec_for

SAMPLES = {
    "ornek.c": (
        "#include <stdio.h>\n"
        "#define MAX_RETRIES 3\n"
        "typedef struct { int limit; } OrderService;\n"
        "static int should_retry(int status) { return status >= 500; }\n"
    ),
    "ornek.cpp": (
        "#include <string>\n"
        "class HttpClient {\n"
        "public:\n"
        "  void set_timeout(int s) { timeout_ = s; }\n"
        "private:\n"
        "  int timeout_ = 30;\n"
        "};\n"
        "static bool should_retry(int status) { return status >= 500; }\n"
    ),
    "Ornek.java": (
        "package com.acme;\n"
        "import java.util.List;\n"
        "public class OrderService {\n"
        "  public int create(Order o) { return 1; }\n"
        "}\n"
    ),
    "ornek.cs": (
        "using System;\n"
        "public class OrderService {\n"
        "  public int Create(Order o) { return 1; }\n"
        "}\n"
    ),
    "ornek.go": (
        "package orders\n"
        'import "fmt"\n'
        "type OrderService struct { limit int }\n"
        "func (s *OrderService) Create(o Order) int { return 1 }\n"
        "func NewOrderService(limit int) *OrderService { return nil }\n"
    ),
    "ornek.ts": (
        'import { Order } from "./types";\n'
        "export class OrderService {\n"
        "  create(order: Order): number { return 1; }\n"
        "}\n"
        "export function shouldRetry(status: number): boolean { return status >= 500; }\n"
    ),
    "ornek.js": (
        'import { Order } from "./types";\n'
        "export class OrderService {\n"
        "  create(order) { return 1; }\n"
        "}\n"
        "export function shouldRetry(status) { return status >= 500; }\n"
    ),
}


def chunks_for(filename: str):
    return extract_from_source(SAMPLES[filename], filename, spec_for(filename))


@pytest.mark.parametrize("filename", sorted(SAMPLES))
def test_every_language_produces_chunks(filename):
    chunks = chunks_for(filename)
    assert chunks, f"{filename} için parça çıkmadı"
    assert all(c.language == spec_for(filename).name for c in chunks)


@pytest.mark.parametrize("filename", sorted(SAMPLES))
def test_line_ranges_are_within_the_file(filename):
    """Satır aralığı bozuksa `dosya:satır` referansı yanlış yere işaret eder."""
    total = len(SAMPLES[filename].splitlines())
    for chunk in chunks_for(filename):
        assert 1 <= chunk.start_line <= chunk.end_line <= total, chunk.name


@pytest.mark.parametrize("filename", sorted(SAMPLES))
def test_no_unnamed_chunks(filename):
    assert all(c.name != "<isimsiz>" for c in chunks_for(filename))


def test_cpp_class_and_method():
    names = {c.name: c for c in chunks_for("ornek.cpp")}
    assert names["HttpClient"].kind == "class"
    assert names["HttpClient.set_timeout"].kind == "method"
    assert names["should_retry"].kind == "function"


def test_cpp_class_summary_omits_member_bodies():
    cls = next(c for c in chunks_for("ornek.cpp") if c.name == "HttpClient")
    assert "set_timeout" in cls.text
    assert "timeout_ = s" not in cls.text


def test_go_method_is_bound_to_receiver_type():
    """`func (s *OrderService) Create` → OrderService.Create olmalı.

    Alıcı okunmazsa metot dosya seviyesi fonksiyon gibi indeksleniyor.
    """
    names = {c.name for c in chunks_for("ornek.go")}
    assert "OrderService.Create" in names
    assert "NewOrderService" in names  # alıcısı yok, fonksiyon olarak kalmalı


def test_c_anonymous_struct_takes_typedef_name():
    """`typedef struct { ... } OrderService;` → struct isimsiz, ad typedef'te."""
    names = {c.name for c in chunks_for("ornek.c")}
    assert "OrderService" in names


def test_java_method_is_qualified_with_class():
    names = {c.name for c in chunks_for("Ornek.java")}
    assert "OrderService.create" in names


def test_module_chunk_collects_imports():
    module = next(c for c in chunks_for("ornek.ts") if c.kind == "module")
    assert "import" in module.text


def test_c_module_chunk_collects_macros():
    module = next(c for c in chunks_for("ornek.c") if c.kind == "module")
    assert "MAX_RETRIES" in module.text


def test_spec_for_unknown_extension():
    assert spec_for("resim.png") is None


def test_all_declared_extensions_are_indexable():
    for spec in LANGUAGES.values():
        for extension in spec.extensions:
            assert extension in SOURCE_EXTENSIONS


def test_index_repo_handles_mixed_languages(tmp_path):
    """Karışık dilli bir repo tek indekste toplanmalı."""
    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.go").write_text(SAMPLES["ornek.go"], encoding="utf-8")
    (tmp_path / "c.java").write_text(SAMPLES["Ornek.java"], encoding="utf-8")

    chunks, stats = index_repo(tmp_path)

    assert stats.files_scanned == 3
    assert stats.files_failed == 0
    assert {c.language for c in chunks} == {"python", "go", "java"}


def test_broken_source_does_not_crash(tmp_path):
    """Tree-sitter hatalı kodda da ağaç üretiyor; koşu durmamalı."""
    (tmp_path / "bozuk.go").write_text("func (((", encoding="utf-8")
    chunks, stats = index_repo(tmp_path)
    assert stats.files_failed == 0
    assert isinstance(chunks, list)
